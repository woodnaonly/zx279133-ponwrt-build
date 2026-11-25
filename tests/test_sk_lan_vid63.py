#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Negative controls for tools/check-sk-lan-vid63.py.

The gate asks whether the 7.3 tree's transport VID range covers the 2.5G
jack.  Each control below breaks one of the rules it asks about, and the
gate has to turn red *on the check that names that rule* - not merely
red somewhere.

The tree is the one the build will use, so the controls run against it: a
synthetic driver would only prove that the gate reads a synthetic driver.

The first control - the range itself - is the root of the others: with
the range back at 62 the checks that depend on it go red as well, and
that is the point - the range is what the whole LAN path agrees on.

    python3 tests/test_sk_lan_vid63.py --tree TREE
"""
import argparse
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GATE = ROOT / "tools/check-sk-lan-vid63.py"

FILES = [
    "drivers/net/ethernet/zte/zx279133.h",
    "drivers/net/ethernet/zte/zx279133-netdev.c",
    "drivers/net/ethernet/zte/zx279133-idm.c",
    "drivers/net/ethernet/zte/zx279133-np.c",
    "drivers/net/ethernet/zte/zx279133-9132.h",
    "include/net/dsa_zx279133_rtl8372n.h",
]

# (name, file, old, new, the check that has to go red)
MUTATIONS = [
    ("the tree's range stops at 62 again",
     "drivers/net/ethernet/zte/zx279133.h",
     "#define ZX279133_LAN_TRANSPORT_VID_MAX\t63\n",
     "#define ZX279133_LAN_TRANSPORT_VID_MAX\t62\n",
     "the tree's transport VID range covers the 2.5G jack's VID"),
    ("the tagger's range stops at port 7 again",
     "include/net/dsa_zx279133_rtl8372n.h",
     "#define ZX279133_RTL8372N_USER_PORT_MAX\t8\n",
     "#define ZX279133_RTL8372N_USER_PORT_MAX\t7\n",
     "the tagger accepts the port that VID names"),
    ("the egress names the chip port with the wrong sign",
     "drivers/net/ethernet/zte/zx279133-netdev.c",
     "vid - (ZX279133_LAN_TRANSPORT_VID_MIN -",
     "vid - (ZX279133_LAN_TRANSPORT_VID_MIN +",
     "the chip port the VID names is the 2.5G jack's port"),
    ("the receive path stops at the fourth port",
     "drivers/net/ethernet/zte/zx279133-idm.c",
     "\t    port <= ZX279133_9132_OUTERPHY_PPU)\n",
     "\t    port <= ZX279133_9132_UNI_PPU_LAST)\n",
     "the receive path maps the 2.5G jack onto the same VID"),
    ("the fifth entry is gone",
     "drivers/net/ethernet/zte/zx279133-np.c",
     "\t{ 0x1c8c2800, 0x31a37e00, 0x00000ad8, 0 },\t/* VID 63, the 2.5G jack */\n",
     "",
     "every transport VID has an MF extension entry"),
    ("the fifth entry is not the one the 6.18 generation has",
     "drivers/net/ethernet/zte/zx279133-np.c",
     "0x31a37e00",
     "0x31a37e04",
     "the fifth entry is the 2.5G jack's"),
    ("the assertion that ties the table to the range is gone",
     "drivers/net/ethernet/zte/zx279133-np.c",
     "static_assert(ARRAY_SIZE(zx279133_lan_vport_mf) ==\n"
     "\t      ZX279133_LAN_VPORT_LAST - ZX279133_LAN_VPORT_FIRST + 1);\n",
     "",
     "the table cannot fall behind the range"),
    ("the egress compares the VID against a literal",
     "drivers/net/ethernet/zte/zx279133-netdev.c",
     "vid > ZX279133_LAN_TRANSPORT_VID_MAX)",
     "vid > 62)",
     "the LAN egress reads the VID out of the range it writes"),
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
        source = Path(tree) / name
        if not source.is_file():
            raise SystemExit("the tree has no %s" % source)
        target = work / name
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

    with tempfile.TemporaryDirectory(prefix="sk-lan-vid63-") as tmp:
        work = Path(tmp)

        # The tree as it is has to pass.
        good = copy_tree(args.tree, work / "good")
        names, rc = failed_checks(good)
        check(rc == 0, "the tree as it is passes the gate",
              ", ".join(sorted(names)[:3]))

        for name, target, old, new, expected in MUTATIONS:
            broken = copy_tree(args.tree,
                               work / ("bad-" + name[:12].replace(" ", "-")))
            path = broken / target
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
