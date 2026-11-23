#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Negative controls for tools/check-sk-tx-isolation-stats.py.

The gate asks whether the TX fault isolation's four counters are published
where the board can read them.  Publishing them takes three lists that have
to stay in lockstep - the enum, the name table and the assignments - and
none of the three drifting apart is a compile error: an array of the wrong
length is, but a name in the wrong place is not.

Each control below breaks one of those lists, and the gate has to turn red
*on the check that names that list* - not merely red somewhere.  A gate that
turns red for the wrong reason is a gate nobody can act on.

The tree is the one the build will use, so the controls run against it: a
synthetic driver would only prove that the gate reads a synthetic driver.

    python3 tests/test_sk_tx_isolation_stats.py --tree TREE
"""
import argparse
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GATE = ROOT / "tools/check-sk-tx-isolation-stats.py"
ZTE = "drivers/net/ethernet/zte"

FILES = ["zx279133-netdev.c", "zx279133.h"]

FILL = "zx279133_get_ethtool_stats"

# The four counters, in the order the gate expects them.
ASSIGN = (
    "\tdata[ZX279133_STAT_TX_INVALID_COMPLETIONS] =\n"
    "\t\teth->tx_invalid_completions;\n"
    "\tdata[ZX279133_STAT_TX_RECOVERY_REQUESTS] = eth->tx_recovery_requests;\n"
    "\tdata[ZX279133_STAT_TX_RECOVERY_RESTARTS] = eth->tx_recovery_restarts;\n"
    "\tdata[ZX279133_STAT_TX_RECOVERY_FAILURES] = eth->tx_recovery_failures;\n"
)

# (name, file, old, new, the check that has to go red)
MUTATIONS = [
    # The enum and the name table disagree about the order of two counters.
    # Both lists still have the same length and still end with the four
    # counters, so only a check that compares the two positions finds it.
    ("the enum and the table drift apart",
     "zx279133-netdev.c",
     "\tZX279133_STAT_TX_RECOVERY_REQUESTS,\n"
     "\tZX279133_STAT_TX_RECOVERY_RESTARTS,\n",
     "\tZX279133_STAT_TX_RECOVERY_RESTARTS,\n"
     "\tZX279133_STAT_TX_RECOVERY_REQUESTS,\n",
     ("the four counters are the last entries of the enum",
      "tx_recovery_requests sits at the same index in the enum and in "
      "the table")),

    ("the names drift apart from the enum",
     "zx279133-netdev.c",
     '\t"tx_recovery_requests",\n'
     '\t"tx_recovery_restarts",\n',
     '\t"tx_recovery_restarts",\n'
     '\t"tx_recovery_requests",\n',
     ("the four counters are the last names of the table",
      "tx_recovery_requests sits at the same index in the enum and in "
      "the table")),

    # A name in the table without an entry in the enum: the table is one
    # longer, so every counter after it is printed under the wrong label.
    ("the table has a name the enum does not have",
     "zx279133-netdev.c",
     '\t"tx_recovery_failures",\n',
     '\t"tx_recovery_failures",\n'
     '\t"tx_recovery_extra",\n',
     "the enum and the name table have the same length"),

    # The other way round: an entry in the enum without a name.
    ("the enum has an entry the table does not have",
     "zx279133-netdev.c",
     "\tZX279133_STAT_TX_RECOVERY_FAILURES,\n",
     "\tZX279133_STAT_TX_RECOVERY_FAILURES,\n"
     "\tZX279133_STAT_TX_RECOVERY_EXTRA,\n",
     "the enum and the name table have the same length"),

    # The counter is published, but it is read after the lock is dropped:
    # a torn read waiting for the right alignment.
    ("the counters are read after the lock is dropped",
     "zx279133-netdev.c",
     ASSIGN + "\tspin_unlock_bh(&eth->tx_lock);\n",
     "\tspin_unlock_bh(&eth->tx_lock);\n" + ASSIGN,
     "tx_invalid_completions is read under the TX lock that protects it"),

    # The counter is published, but it is the wrong counter's value.
    ("a counter is read from the wrong field",
     "zx279133-netdev.c",
     "\tdata[ZX279133_STAT_TX_RECOVERY_REQUESTS] = eth->tx_recovery_requests;\n",
     "\tdata[ZX279133_STAT_TX_RECOVERY_REQUESTS] = eth->tx_recovery_restarts;\n",
     "tx_recovery_requests is read from the counter it names"),

    # The counter is published, but nothing is counted into it: it would
    # read zero forever.
    ("a counter is never counted",
     "zx279133-netdev.c",
     "\t\t\teth->tx_invalid_completions++;\n",
     "",
     "tx_invalid_completions is counted somewhere"),

    # The eth does not even carry the field.
    ("the eth does not carry the counter",
     "zx279133.h",
     "\tu64 tx_invalid_completions;\n",
     "",
     "the tree has the tx_invalid_completions counter"),

    # The counters are published without the lock that protects them.
    ("the statistics are read without the lock",
     "zx279133-netdev.c",
     "\t\tzx279133_hw_stat(eth, ZX279133_SOPC_REQ_SMAC5);\n"
     "\tspin_lock_bh(&eth->tx_lock);\n",
     "\t\tzx279133_hw_stat(eth, ZX279133_SOPC_REQ_SMAC5);\n",
     f"{FILL}() takes the TX lock"),

    # One of the four is not published at all.
    ("a counter is never given a value",
     "zx279133-netdev.c",
     "\tdata[ZX279133_STAT_TX_RECOVERY_FAILURES] = eth->tx_recovery_failures;\n",
     "",
     "tx_recovery_failures is given a value in "
     f"{FILL}()"),
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

    with tempfile.TemporaryDirectory(prefix="sk-tx-stats-") as tmp:
        work = Path(tmp)

        # The tree as it is has to pass.
        good = copy_tree(args.tree, work / "good")
        names, rc = failed_checks(good)
        check(rc == 0, "the tree as it is passes the gate",
              ", ".join(sorted(names)[:3]))

        for index, (name, target, old, new, expected) in enumerate(MUTATIONS):
            broken = copy_tree(args.tree, work / ("bad-%02d" % index))
            path = broken / ZTE / target
            text = path.read_text(encoding="utf-8")
            if old not in text:
                check(False, name + " - the text to mutate is gone")
                continue
            path.write_bytes(text.replace(old, new, 1).encode("utf-8"))
            names, rc = failed_checks(broken)
            expected = (expected,) if isinstance(expected, str) else expected
            missing = [item for item in expected if item not in names]
            if not missing:
                check(True, name + " -> red on %s" % ", ".join(expected))
            else:
                check(False, name + " -> the check %r did not go red"
                      % missing[0],
                      ", ".join(sorted(names)[:3]))

    print("%d checks, %d failed" % (checks, len(failures)))
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
