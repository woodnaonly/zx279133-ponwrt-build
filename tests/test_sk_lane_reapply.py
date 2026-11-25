#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Negative controls for tools/check-sk-lane-reapply.py.

The gate asks whether the 7.3 tree programs the 2.5G jack's lane again
after the bring-up - the pass whose absence the boot of 2026-11-25
18:12:20 shows, with a dead egress and a PC that never resolved the
board's address.

Each control below takes one of the gate's rules away, and the gate has to
turn red *on the check that names that rule* - not merely red somewhere.
A control that makes the gate red on a different check proves nothing about
the rule it was written for.

The tree is the one the build will use, so the controls run against it: a
synthetic driver would only prove that the gate reads a synthetic driver.

    python3 tests/test_sk_lane_reapply.py --tree TREE
"""
import argparse
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GATE = ROOT / "tools/check-sk-lane-reapply.py"
ZTE = "drivers/net/ethernet/zte"

FILES = ["zx279133-switch.c"]

# (name, file, old, new, the check that has to go red)
MUTATIONS = [
    ("the second pass is left to the class change",
     "zx279133-switch.c",
     "\tif (!priv->lane_reapplies)\n"
     "\t\tzx279133_switch_lane_reapply(priv,\n"
     "\t\t\t\"the bring-up is over\");\n",
     "",
     "the watch programs the lane again after the bring-up"),
    ("the pass only says it happened",
     "zx279133-switch.c",
     "\tzx279133_switch_outerphy_apply_speed(priv, speed);\n",
     "\t/* the lane is not programmed */\n",
     "the re-apply pass programs the lane"),
    ("the pass is not bounded",
     "zx279133-switch.c",
     "\tif (priv->lane_reapplies >= ZX279133_SWITCH_LANE_REAPPLY_MAX)\n"
     "\t\treturn;\n",
     "",
     "the re-apply pass is bounded"),
    ("the pass restarts the far end's auto-negotiation",
     "zx279133-switch.c",
     "\tzx279133_switch_outerphy_apply_speed(priv, speed);\n",
     "\tzx279133_switch_outerphy_serdes_set(priv, true);\n"
     "\tzx279133_switch_outerphy_apply_speed(priv, speed);\n",
     "the re-apply pass does not restart the far end's auto-negotiation"),
    ("the class change stops going through the pass",
     "zx279133-switch.c",
     "\t\t\tzx279133_switch_lane_reapply(priv,\n"
     "\t\t\t\t\"the far end changed its SerDes class\");\n",
     "\t\t\tzx279133_switch_outerphy_apply_speed(priv,\n"
     "\t\t\t\tcls == ZX279133_SWITCH_OUTERPHY_SERDES_MODE_2G5 ?\n"
     "\t\t\t\tSPEED_2500 : SPEED_1000);\n",
     "the far end's class change goes through the same pass"),
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

    with tempfile.TemporaryDirectory(prefix="sk-lane-reapply-") as tmp:
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
