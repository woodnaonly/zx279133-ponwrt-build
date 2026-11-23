#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Negative controls for tools/check-sk-tx-isolation.py.

The gate asks whether the tree quarantines a TX ring whose completion
counter cannot be trusted.  Each control below breaks one of the rules it
asks about, and the gate has to turn red *on the check that names that
rule* - not merely red somewhere.

The tree is the one the build will use, so the controls run against it: a
synthetic driver would only prove that the gate reads a synthetic driver.

    python3 tests/test_sk_tx_isolation.py --tree TREE
"""
import argparse
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GATE = ROOT / "tools/check-sk-tx-isolation.py"
ZTE = "drivers/net/ethernet/zte"

FILES = ["zx279133-idm.c", "zx279133-main.c", "zx279133-netdev.c",
         "zx279133.h"]

# (name, file, old, new, the check that has to go red)
MUTATIONS = [
    ("the fault branch falls through to the clamp again",
     "zx279133-idm.c",
     "\t\tzx279133_tx_recovery_request_locked(eth, queue, true);\n"
     "\t\treturn 0;\n",
     "\t\tcompleted = tx->pending;\n",
     "the fault branch returns instead of falling through"),
    ("the fault branch does not say the completion was invalid",
     "zx279133-idm.c",
     "zx279133_tx_recovery_request_locked(eth, queue, true);",
     "zx279133_tx_recovery_request_locked(eth, queue, false);",
     "the fault branch says the completion was invalid"),
    ("the fault branch releases a buffer the DMA may still own",
     "zx279133-idm.c",
     "\t\tzx279133_tx_recovery_request_locked(eth, queue, true);\n",
     "\t\tzx279133_tx_recovery_request_locked(eth, queue, true);\n"
     "\t\tdma_unmap_single(eth->dev, 0, 0, DMA_TO_DEVICE);\n",
     "the fault branch releases nothing"),
    ("a frame is released before the fault branch is decided",
     "zx279133-idm.c",
     "\teth->tx_reclaim_polls++;\n\tif (completed > tx->pending) {",
     "\teth->tx_reclaim_polls++;\n\ttx->done = done;\n"
     "\tif (completed > tx->pending) {",
     "nothing is released before the fault branch is decided"),
    ("a quarantined ring is reclaimed anyway",
     "zx279133-idm.c",
     "\tif (!READ_ONCE(eth->tx_prepared) ||\n"
     "\t    !READ_ONCE(eth->hardware_prepared) ||\n"
     "\t    test_bit(queue, &eth->tx_fault_queues))\n"
     "\t\treturn 0;\n\n",
     "",
     "a quarantined ring is not reclaimed at all"),
    ("the quarantine leaves the LAN device sending",
     "zx279133-netdev.c",
     "\t\tif (eth->lan_ndev)\n"
     "\t\t\tnetif_tx_stop_all_queues(eth->lan_ndev);\n",
     "",
     "the quarantine stops the LAN device as well"),
    ("the recovery waits for RTNL",
     "zx279133-netdev.c",
     "\tif (!rtnl_trylock()) {",
     "\tif (!rtnl_lock()) {",
     "the recovery does not wait for RTNL"),
    ("the recovery restarts the datapath every time",
     "zx279133-netdev.c",
     "\trestart = !eth->tx_recovery_attempted;",
     "\trestart = true;",
     "the restart is rearmed only by a real completion"),
    ("the recovery is not armed by the quarantine",
     "zx279133-netdev.c",
     "\tmod_delayed_work(system_wq, &eth->tx_recovery_work, 0);",
     "\tmod_delayed_work(system_wq, &eth->tx_reclaim_work, 0);",
     "the quarantine arms the recovery work"),
    ("opening an interface does not restore a failed datapath",
     "zx279133-netdev.c",
     "\t\tret = zx279133_datapath_restore_failed_locked(eth);\n"
     "\t\tif (ret)\n"
     "\t\t\tgoto out_unlock;\n",
     "",
     "zx279133_open restores a failed datapath before using it"),
    ("the eth carries no recovery work",
     "zx279133.h",
     "\tstruct delayed_work tx_recovery_work;\n",
     "",
     "the eth carries the recovery work"),
    ("the invalid completions are not counted",
     "zx279133.h",
     "\tu64 tx_invalid_completions;\n",
     "",
     "the eth counts tx_invalid_completions"),
    ("the probe never arms the recovery",
     "zx279133-main.c",
     "\tzx279133_tx_recovery_init(eth);\n",
     "",
     "the probe arms the recovery work"),
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

    with tempfile.TemporaryDirectory(prefix="sk-tx-iso-") as tmp:
        work = Path(tmp)

        # The tree as it is has to pass.
        good = copy_tree(args.tree, work / "good")
        names, rc = failed_checks(good)
        check(rc == 0, "the tree as it is passes the gate",
              ", ".join(sorted(names)[:3]))

        for name, target, old, new, expected in MUTATIONS:
            broken = copy_tree(args.tree, work / ("bad-" + name[:12].replace(" ", "-")))
            path = broken / ZTE / target
            text = path.read_text(encoding="utf-8")
            if old not in text:
                check(False, name + " - the text to mutate is gone")
                continue
            path.write_text(text.replace(old, new, 1), encoding="utf-8")
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
