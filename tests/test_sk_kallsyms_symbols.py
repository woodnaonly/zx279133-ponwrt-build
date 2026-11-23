#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Controls for tools/check-sk-kallsyms-symbols.py.

The trial's judge reads the kernel's symbol table to answer "are these four
drivers in this kernel".  A symbol that is not in the table means the
driver is not in the kernel - but only if the symbol would have been in the
table if it were.  A static function the compiler inlines into its one
caller has no symbol at all, and a judge that looks for that one would
report a healthy driver as missing.

So the gate has to notice two different things: a symbol that is not in the
tree at all, and a symbol that is in the tree but could be inlined away.
Each control below breaks exactly one of them, and the gate has to turn red
on exactly that symbol and on no other.

The last control is the one that keeps three lists from drifting apart: the
instrument looks the symbols up in /proc/kallsyms, the judge looks the same
symbols up in what the instrument printed, and this gate says those symbols
exist.  If one of the three moves, the judge stops finding what the
instrument prints - and it would do so silently, on a board, in the middle
of the trial.

    python3 tests/test_sk_kallsyms_symbols.py
"""

import argparse
import importlib.util
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GATE = ROOT / "tools" / "check-sk-kallsyms-symbols.py"
INSTRUMENT = ROOT / "tools" / "trialsurvey.sh"
JUDGE = ROOT / "tools" / "trialsurvey-verdict.py"

# The four symbols, spelled out here rather than taken from the gate: a
# control that reads its own expectation from the thing it checks proves
# nothing.  The drift control at the bottom is what ties this list to the
# gate's.
#
# (the CONFIG symbol, the kernel symbol, the file it is defined in)
SYMBOLS = (
    ("ZTE_ZX279133_ETH", "zx279133_lan_netdev_open", "zx279133-netdev.c"),
    ("ZTE_ZX279133_LAN_CONDUIT", "zx279133_lan_conduit_get_stats64",
     "zx279133-lan-conduit.c"),
    ("ZTE_ZX279133_SWITCH", "zx279133_switch_get_tag_protocol",
     "zx279133-switch.c"),
    ("ZTE_ZX279133_FNOFFLOAD", "zx279133_offload_attach",
     "zx279133-offload.c"),
)

MAKEFILE = """\
obj-$(CONFIG_ZTE_ZX279133_ETH) += npu.o
npu-y := zx279133-main.o zx279133-netdev.o

obj-$(CONFIG_ZTE_ZX279133_LAN_CONDUIT) += zx279133-lan-conduit.o
obj-$(CONFIG_ZTE_ZX279133_SWITCH) += zx279133-switch.o

obj-$(CONFIG_ZTE_ZX279133_FNOFFLOAD) += fnoffload.o
fnoffload-y := zx279133-offload.o
"""


def source_for(config, symbol):
    """A source file that defines the symbol and takes its address."""
    return """\
#include <linux/module.h>

static int
%s(struct net_device *ndev)
{
	return 0;
}

static const struct net_device_ops ops = {
	.ndo_open = %s,
};
""" % (symbol, symbol)


def make_tree(root):
    """Build a synthetic driver tree the gate can read."""
    d = root / "drivers" / "net" / "ethernet" / "zte"
    d.mkdir(parents=True)
    (d / "Makefile").write_text(MAKEFILE, encoding="utf-8", newline="\n")
    for config, symbol, name in SYMBOLS:
        (d / name).write_text(source_for(config, symbol), encoding="utf-8",
                              newline="\n")
    # the ETH object is a composite, so its member's source has to be there
    # under the name the Makefile lists
    (d / "zx279133-main.c").write_text("/* nothing */\n", encoding="utf-8",
                                       newline="\n")
    return root


def run_gate(tree):
    """Run the gate and return (returncode, {symbol: PASS or FAIL})."""
    proc = subprocess.run(
        [sys.executable, str(GATE), "--tree", str(tree)],
        capture_output=True, text=True)
    if proc.returncode not in (0, 1):
        raise SystemExit("the gate crashed (rc=%d):\n%s"
                         % (proc.returncode, proc.stderr))
    out = {}
    for line in proc.stdout.splitlines():
        m = re.match(r"^(PASS|FAIL)\s+(\S+)\s+(\S+)\s", line)
        if m:
            out[m.group(3)] = m.group(1)
    return proc.returncode, out


def load(path, name):
    """Load a tool whose file name is not a module name."""
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args(argv)

    failures = []
    checks = 0
    tmp = Path(tempfile.mkdtemp(prefix="sk-kallsyms-"))

    try:
        # ---- the healthy tree passes ----------------------------------
        tree = make_tree(tmp / "tree")
        rc, out = run_gate(tree)
        checks += 1
        if rc != 0:
            failures.append("the gate did not pass on a healthy tree (rc=%d)"
                            % rc)
        for config, symbol, name in SYMBOLS:
            checks += 1
            if out.get(symbol) != "PASS":
                failures.append("%s is %r on a healthy tree, expected PASS"
                                % (symbol, out.get(symbol)))
        if args.verbose:
            print("healthy: rc=%d %r" % (rc, out))

        # ---- a symbol that is not in the tree at all --------------------
        # The ETH symbol is gone from its source file.  Only that one may
        # turn red: the gate has to say which one, not "something is wrong".
        tree2 = make_tree(tmp / "tree2")
        path = (tree2 / "drivers/net/ethernet/zte/zx279133-netdev.c")
        text = path.read_text(encoding="utf-8")
        path.write_text(text.replace("zx279133_lan_netdev_open", "gone"),
                        encoding="utf-8", newline="\n")
        rc2, out2 = run_gate(tree2)
        checks += 1
        if out2.get("zx279133_lan_netdev_open") != "FAIL":
            failures.append("a symbol that is not in the tree is %r, "
                            "expected FAIL"
                            % out2.get("zx279133_lan_netdev_open"))
        checks += 1
        red = sorted(k for k, v in out2.items() if v == "FAIL")
        if red != ["zx279133_lan_netdev_open"]:
            failures.append("a missing definition turned red on %r, expected "
                            "only zx279133_lan_netdev_open" % red)
        checks += 1
        if rc2 != 1:
            failures.append("the gate exited %d with a missing definition, "
                            "expected 1" % rc2)
        if args.verbose:
            print("missing-definition: rc=%d red=%r" % (rc2, red))

        # ---- a symbol that could be inlined away -----------------------
        # The switch symbol is still defined, but nothing takes its
        # address any more, so the compiler is free to inline it into its
        # one caller and the symbol would not be in the kernel's table at
        # all.  That is the case that would make the judge cry wolf on a
        # healthy board.
        tree3 = make_tree(tmp / "tree3")
        path3 = (tree3 / "drivers/net/ethernet/zte/zx279133-switch.c")
        text3 = path3.read_text(encoding="utf-8")
        path3.write_text(
            text3.replace(".ndo_open = zx279133_switch_get_tag_protocol,",
                          ".ndo_open = NULL,"),
            encoding="utf-8", newline="\n")
        rc3, out3 = run_gate(tree3)
        checks += 1
        if out3.get("zx279133_switch_get_tag_protocol") != "FAIL":
            failures.append("a symbol whose address is not taken is %r, "
                            "expected FAIL"
                            % out3.get("zx279133_switch_get_tag_protocol"))
        checks += 1
        red3 = sorted(k for k, v in out3.items() if v == "FAIL")
        if red3 != ["zx279133_switch_get_tag_protocol"]:
            failures.append("an inlinable symbol turned red on %r, expected "
                            "only zx279133_switch_get_tag_protocol" % red3)
        checks += 1
        if rc3 != 1:
            failures.append("the gate exited %d with an inlinable symbol, "
                            "expected 1" % rc3)
        if args.verbose:
            print("inlinable: rc=%d red=%r" % (rc3, red3))

        # ---- and the three lists have to be the same list --------------
        # The instrument prints what it found in the symbol table, the
        # judge looks for those names in what the instrument printed, and
        # this gate says those names exist in the kernel.  If one of the
        # three moves, the judge stops finding what the instrument prints,
        # and it does so on a board, in the middle of the trial.
        checks += 1
        script = INSTRUMENT.read_text(encoding="utf-8")
        m = re.search(r'^DRIVER_SYMBOLS="([^"]*)"', script, re.M)
        if not m:
            failures.append("the instrument has no DRIVER_SYMBOLS list")
        else:
            instrument = m.group(1).split()
            judge = list(load(JUDGE, "sk_verdict").DRIVER_SYMBOLS)
            gate = [s for _c, s in
                    load(GATE, "sk_kallsyms_gate").CRITERIA]
            expected = [s for _c, s, _f in SYMBOLS]
            for name, got in (("the instrument", instrument),
                              ("the judge", judge),
                              ("the gate", gate)):
                if got != expected:
                    failures.append("%s looks for %r, expected %r"
                                    % (name, got, expected))
        if args.verbose:
            print("drift: instrument=%r judge=%r gate=%r"
                  % (instrument, judge, gate))

    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    if failures:
        for f in failures:
            print("FAIL: %s" % f)
        print("%d of %d controls failed" % (len(failures), checks))
        return 1
    print("kallsyms symbols: %d controls, 0 failed" % checks)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
