#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Negative controls for tools/check-sk-datapath-restore.py.

The gate asks whether the tree has a way back from a datapath that could
not be brought up.  Each control below breaks one of the things it asks
about, and the gate has to turn red *on the check that names that thing*
- not merely red somewhere.

The order matters most of all: a datapath that failed leaves the
hardware unprepared, so a check for the hardware that sits before the
restore refuses the open before any restore could run.  There is a
control for exactly that.

The tree is the one the build will use, so the controls run against it:
a synthetic driver would only prove that the gate reads a synthetic
driver.

    python3 tests/test_sk_datapath_restore.py --tree TREE
"""
import argparse
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GATE = ROOT / "tools/check-sk-datapath-restore.py"

FILES = [
    "drivers/net/ethernet/zte/zx279133-netdev.c",
    "drivers/net/ethernet/zte/zx279133-lan-conduit.c",
    "net/dsa/user.c",
]

# (name, file, old, new, the check that has to go red)
MUTATIONS = [
    ("the conduit's open does not restore",
     "drivers/net/ethernet/zte/zx279133-netdev.c",
     "\tret = zx279133_datapath_restore_failed_locked(eth);\n"
     "\tif (ret)\n\t\tgoto out_unlock;\n\n",
     "",
     "zx279133_lan_netdev_open() restores a failed datapath"),

    ("the restore comes after the hardware check",
     "drivers/net/ethernet/zte/zx279133-netdev.c",
     "\tret = zx279133_datapath_restore_failed_locked(eth);\n"
     "\tif (ret)\n\t\tgoto out_unlock;\n"
     "\n"
     "\tif (!READ_ONCE(eth->hardware_prepared)) {\n"
     "\t\tret = -ENETDOWN;\n"
     "\t\tgoto out_unlock;\n"
     "\t}\n",
     "\tif (!READ_ONCE(eth->hardware_prepared)) {\n"
     "\t\tret = -ENETDOWN;\n"
     "\t\tgoto out_unlock;\n"
     "\t}\n"
     "\n"
     "\tret = zx279133_datapath_restore_failed_locked(eth);\n"
     "\tif (ret)\n\t\tgoto out_unlock;\n",
     "the restore comes before the hardware check"),

    ("a failed restore is swallowed",
     "drivers/net/ethernet/zte/zx279133-netdev.c",
     "\tret = zx279133_datapath_restore_failed_locked(eth);\n"
     "\tif (ret)\n\t\tgoto out_unlock;\n",
     "\tret = zx279133_datapath_restore_failed_locked(eth);\n",
     "a failed restore fails the open"),

    ("the restore runs without the datapath lock",
     "drivers/net/ethernet/zte/zx279133-netdev.c",
     "\tmutex_lock(&eth->datapath_lock);\n"
     "\tif (!(eth->datapath_users & ZX279133_DATAPATH_USER_LAN) ||\n"
     "\t    !eth->lan_datapath_ready) {\n",
     "\tif (!(eth->datapath_users & ZX279133_DATAPATH_USER_LAN) ||\n"
     "\t    !eth->lan_datapath_ready) {\n",
     "zx279133_lan_netdev_open() restores under the datapath lock"),

    ("the restore no longer returns at once when the datapath is healthy",
     "drivers/net/ethernet/zte/zx279133-netdev.c",
     "\tif (!eth->datapath_failed || !eth->datapath_users)\n"
     "\t\treturn 0;\n",
     "",
     "the restore returns at once while the datapath is healthy"),

    ("the service's netdev_open is something else",
     "drivers/net/ethernet/zte/zx279133-netdev.c",
     ".netdev_open = zx279133_lan_netdev_open,",
     ".netdev_open = zx279133_lan_netdev_stop,",
     "the service's netdev_open is zx279133_lan_netdev_open()"),

    ("the conduit does not go through the service",
     "drivers/net/ethernet/zte/zx279133-lan-conduit.c",
     "zx279133_lan_service_netdev_open(priv->service, ndev)",
     "0",
     "the conduit's open goes through the service"),

    ("DSA does not open the conduit",
     "net/dsa/user.c",
     "\terr = dev_open(conduit, NULL);",
     "\terr = 0;",
     "DSA opens the conduit when a user port comes up"),
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
    return work


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tree", type=Path, required=True,
                        help="the prepared kernel tree to check")
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

    with tempfile.TemporaryDirectory(prefix="sk-dp-restore-") as tmp:
        work = Path(tmp)

        # The tree as it is has to pass.
        good = copy_tree(args.tree, work / "good")
        names, rc = failed_checks(good)
        check(rc == 0, "the tree as it is passes the gate",
              ", ".join(sorted(names)[:3]))

        for index, (name, target, old, new, expected) in enumerate(MUTATIONS):
            broken = copy_tree(args.tree, work / ("bad-%02d" % index))
            path = broken / target
            text = path.read_text(encoding="utf-8")
            if text.count(old) != 1:
                check(False, name + " - the text to mutate is not there once "
                                "(%d)" % text.count(old))
                continue
            path.write_bytes(text.replace(old, new, 1).encode("utf-8"))
            names, rc = failed_checks(broken)
            if expected in names:
                check(True, name + " -> red on %r" % expected)
            else:
                check(False, name + " -> the check %r did not go red" % expected,
                      ", ".join(sorted(names)[:3]))

    print("%d checks, %d failed" % (checks, len(failures)))
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
