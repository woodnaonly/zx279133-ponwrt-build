#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Negative controls for tools/check-sk-lan-egress.py.

The gate asks whether the 7.3 tree's LAN egress names the jack the frame
is for.  Each control below breaks one of the rules it asks about, and
the gate has to turn red *on the check that names that rule* - not merely
red somewhere.

The tree is the one the build will use, so the controls run against it: a
synthetic driver would only prove that the gate reads a synthetic driver.

    python3 tests/test_sk_lan_egress.py --tree TREE
"""
import argparse
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GATE = ROOT / "tools/check-sk-lan-egress.py"
ZTE = "drivers/net/ethernet/zte"

FILES = ["zx279133.h", "zx279133-netdev.c", "zx279133-idm.c"]

STRIP_BLOCK = (
    "\tif (ndev != hw_ndev) {\n"
    "\t\tint popped = zx279133_lan_strip_transport_vlan(skb, &lan_out_port);\n"
    "\n"
    "\t\tif (popped < 0)\n"
    "\t\t\tgoto drop;\n"
    "\t}\n")

# (name, file, old, new, the check that has to go red)
MUTATIONS = [
    ("the output port is a constant again",
     "zx279133-netdev.c",
     "\t\ttx_port = lan_out_port ? lan_out_port : ZX279133_LAN_TX_PORT;\n",
     "\t\ttx_port = ZX279133_LAN_TX_PORT;\n",
     "the LAN egress names the chip port the transport VID carries"),
    ("the strip reads the VID with the wrong sign",
     "zx279133-netdev.c",
     "vid - (ZX279133_LAN_TRANSPORT_VID_MIN -",
     "vid - (ZX279133_LAN_TRANSPORT_VID_MIN +",
     "the chip port the VID names is the one the receive path names"),
    ("the tag stays on the frame",
     "zx279133-netdev.c",
     "\terr = __skb_vlan_pop(skb, &tci);\n",
     "\terr = 0;\n",
     "the transport tag comes off the frame before the descriptor is filled"),
    ("the LAN path writes the PON control word again",
     "zx279133.h",
     "#define ZX279133_LAN_TX_WORD4\t\t0x003e0000\n",
     "#define ZX279133_LAN_TX_WORD4\t\t0x08000000\n",
     "the LAN path writes the vendor's fourth metadata word"),
    ("the fallback port is a MAC with no peer",
     "zx279133.h",
     "#define ZX279133_LAN_TX_PORT\t\t2\n",
     "#define ZX279133_LAN_TX_PORT\t\t5\n",
     "a frame with no transport VID goes out of a UNI port"),
    ("the tag is taken off before the ring capacity check",
     "zx279133-netdev.c",
     "u8 lan_out_port)\n{\n",
     "u8 lan_out_port)\n{\n" + STRIP_BLOCK,
     "the tag is only taken off after the ring capacity check"),
]


def failed_checks(tree):
    """The checks the gate reported as failed."""
    result = subprocess.run([sys.executable, str(GATE), "--tree", str(tree)],
                            capture_output=True, text=True)
    names = set()
    for line in (result.stdout + result.stderr).splitlines():
        match = re.match(r"^\s*\[FAIL\]\s+(.+?)(?:\s+-\s+.*)?$", line)
        if match:
            names.add(match.group(1))
    return names, result.returncode


def copy_tree(tree, work):
    """A tree with just the files the gate reads."""
    for name in FILES:
        source = Path(tree) / ZTE / name
        if not source.is_file():
            raise SystemExit("the tree has no %s" % source)
        target = work / ZTE / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
    # The gate refuses a directory that is not a kernel tree, and the
    # controls hand it one with just the files it reads.
    (work / "Makefile").write_text("# the controls only need the files\n",
                                   encoding="utf-8")
    return work


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tree", type=Path, required=True)
    args = parser.parse_args()

    checks = 0
    failures = []

    def check(ok, what, detail=""):
        nonlocal checks
        checks += 1
        if ok:
            print("  ok   %s" % what)
        else:
            print("  FAIL %s%s" % (what, (" - " + detail) if detail else ""))
            failures.append(what)

    with tempfile.TemporaryDirectory(prefix="sk-lan-egress-") as tmp:
        work = Path(tmp)

        # The tree as it is has to pass.
        good = copy_tree(args.tree, work / "good")
        names, rc = failed_checks(good)
        check(rc == 0, "the tree as it is passes the gate",
              ", ".join(sorted(names)[:3]))

        for name, target, old, new, expected in MUTATIONS:
            broken = copy_tree(args.tree,
                               work / ("bad-" + name[:12].replace(" ", "-")))
            path = broken / ZTE / target
            text = path.read_text(encoding="utf-8")
            if old not in text:
                check(False, name + " - the text to mutate is gone")
                continue
            path.write_text(text.replace(old, new, 1), encoding="utf-8")
            names, rc = failed_checks(broken)
            if expected in names:
                check(True, name + " -> red on '%s'" % expected)
            else:
                check(False, name + " -> the check '%s' did not go red" % expected,
                      ", ".join(sorted(names)[:3]))

    print("%d checks, %d failed" % (checks, len(failures)))
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
