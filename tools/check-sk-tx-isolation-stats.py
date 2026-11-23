#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Check that the TX fault isolation's counters are published where the board can read them.

The TX fault isolation keeps four counters: how often a completion counter
could not be trusted, how often a recovery was asked for, how often the
shared datapath was restarted, and how often the recovery failed.  Without
a way to read them, a board that quarantines a ring looks exactly like a
board that never had a problem.

The 7.3 tree already has the place to publish them: the driver's ethtool
statistics table, whose names are what "ethtool -S" prints.  That table is
three lists that have to stay in lockstep:

  * the enum that gives each statistic its index,
  * the array that gives each index its name,
  * the assignments that give each index its value.

Nothing in the build notices when they drift apart.  A name added to the
array without the enum growing by one prints every later counter under
the wrong label, and the compiler is happy: an array of the wrong length
is a compile error, but a name in the wrong place is not.  So the check
here is about positions, not about the presence of a string: the two
lists have to agree on where each counter is, not only on which counters
are in them.

It also asks where the values are read.  The counters are incremented
under the TX lock, so they have to be read under it as well; a read
outside the lock is a torn read waiting for the right alignment.

Usage:
  check-sk-tx-isolation-stats.py --tree TREE
"""
import argparse
import hashlib
import os
import re
import sys
from pathlib import Path

ZTE = "drivers/net/ethernet/zte"
NETDEV = "zx279133-netdev.c"
HEADER = "zx279133.h"

ENUM = "enum zx279133_ethtool_stat"
NAMES = "zx279133_gstrings_stats"
FILL = "zx279133_get_ethtool_stats"

# The four counters, in the order they must appear in both lists.
COUNTERS = [
    ("tx_invalid_completions", "ZX279133_STAT_TX_INVALID_COMPLETIONS",
     "tx_invalid_completions"),
    ("tx_recovery_requests", "ZX279133_STAT_TX_RECOVERY_REQUESTS",
     "tx_recovery_requests"),
    ("tx_recovery_restarts", "ZX279133_STAT_TX_RECOVERY_RESTARTS",
     "tx_recovery_restarts"),
    ("tx_recovery_failures", "ZX279133_STAT_TX_RECOVERY_FAILURES",
     "tx_recovery_failures"),
]


class Report:
    def __init__(self):
        self.failures = []
        self.checks = 0
        self.results = []

    def require(self, ok, message, detail=""):
        self.checks += 1
        self.results.append((ok, message, detail))
        print(f"  [{'OK ' if ok else 'FAIL'}] {message}"
              + (f" - {detail}" if detail and not ok else ""))
        if not ok:
            self.failures.append(message)
        return ok


def tree_fingerprint(tree, names):
    """What the files this gate read amount to."""
    out = []
    for name in names:
        path = Path(tree) / ZTE / name
        if not path.is_file():
            out.append((name, "missing", 0, 0))
            continue
        data = path.read_bytes()
        out.append((name, hashlib.sha256(data).hexdigest(),
                    len(data), data.count(b"\n")))
    return out


def write_summary(report, title, files=None):
    """Put the verdict where the run page shows it."""
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if not path:
        return
    lines = [f"### {title}", ""]
    for ok, message, detail in report.results:
        mark = "PASS" if ok else "FAIL"
        lines.append(f"- **{mark}** {message}")
        if detail and not ok:
            lines.append(f"  - {detail}")
    lines.append("")
    lines.append(f"{report.checks} check(s), {len(report.failures)} failed.")
    lines.append("")
    if files:
        lines.append("The files this gate read:")
        lines.append("")
        for name, digest, size, lines_in_file in files:
            lines.append(f"- `{name}`: {digest} ({size} bytes, {lines_in_file} lines)")
        lines.append("")
    Path(path).write_text("\n".join(lines), encoding="utf-8")


def block_after(text, anchor):
    """The body of the brace block that follows an anchor."""
    match = re.search(re.escape(anchor), text)
    if not match:
        return None
    start = text.find("{", match.end())
    if start == -1:
        return None
    depth = 0
    for index in range(start, len(text)):
        if text[index] == "{":
            depth += 1
        elif text[index] == "}":
            depth -= 1
            if depth == 0:
                return text[start + 1:index]
    return None


def function_body(text, name):
    """The body of a function definition, braces balanced."""
    for match in re.finditer(r"\b" + re.escape(name) + r"\s*\([^;]*?\)\s*\{",
                             text, re.S):
        prefix = text[max(0, match.start() - 300):match.start()]
        cut = max(prefix.rfind(";"), prefix.rfind("{"), prefix.rfind("}"))
        head = prefix[cut + 1:][-80:]
        if not re.search(r"(?:static|inline|void|bool|int|unsigned|signed|u\d+|"
                         r"s\d+|char|long|short|size_t|const\s+struct\s+\w+|"
                         r"struct\s+\w+|enum\s+\w+)\s*\*?\s*$", head):
            continue
        start = match.end() - 1
        depth = 0
        for index in range(start, len(text)):
            if text[index] == "{":
                depth += 1
            elif text[index] == "}":
                depth -= 1
                if depth == 0:
                    return text[start + 1:index]
    return None


def enum_entries(body):
    """The enum's entries, in order, without the count sentinel."""
    entries = []
    for line in body.split("\n"):
        line = line.split("/*")[0].split("//")[0].strip().rstrip(",").strip()
        if not line:
            continue
        entries.append(line)
    return entries


def name_entries(body):
    """The names in the table, in order."""
    names = []
    for match in re.finditer(r'"([^"]*)"', body):
        names.append(match.group(1))
    return names


def check(tree, report):
    netdev = Path(tree) / ZTE / NETDEV
    header = Path(tree) / ZTE / HEADER
    if not netdev.is_file():
        report.require(False, f"the tree has {NETDEV}")
        return
    if not header.is_file():
        report.require(False, f"the tree has {HEADER}")
        return
    text = netdev.read_text(encoding="utf-8", errors="replace")
    head = header.read_text(encoding="utf-8", errors="replace")

    enum_body = block_after(text, ENUM)
    names_body = block_after(text, NAMES)
    if not report.require(enum_body is not None,
                           f"the tree has the {ENUM} enum"):
        return
    if not report.require(names_body is not None,
                           f"the tree has the {NAMES} table"):
        return

    entries = enum_entries(enum_body)
    names = name_entries(names_body)
    count = entries[-1] if entries else ""
    indexed = entries[:-1] if count == "ZX279133_STAT_COUNT" else entries
    report.require(count == "ZX279133_STAT_COUNT",
                   "the enum ends with its count sentinel",
                   f"last entry is {count!r}")
    report.require(len(indexed) == len(names),
                   "the enum and the name table have the same length",
                   f"{len(indexed)} enum entries, {len(names)} names")

    wanted_enums = [entry for _, entry, _ in COUNTERS]
    wanted_names = [label for label, _, _ in COUNTERS]
    report.require(
        indexed[-len(wanted_enums):] == wanted_enums,
        "the four counters are the last entries of the enum",
        f"the enum ends with {indexed[-len(wanted_enums):]}")
    report.require(
        names[-len(wanted_names):] == wanted_names,
        "the four counters are the last names of the table",
        f"the table ends with {names[-len(wanted_names):]}")
    report.require(
        [entry.lower().replace("zx279133_stat_", "") for entry in wanted_enums]
        == wanted_names,
        "the gate's own enum entries and names agree with each other")

    # Both lists have to say where each counter is, not only which counters
    # are there.  A name that sits one place too early prints its
    # neighbour's counter under its own label: the array is still the right
    # length, the enum is still the right length, and the compiler has
    # nothing to say about it.
    for label, entry, _ in COUNTERS:
        enum_index = indexed.index(entry) if entry in indexed else -1
        name_index = names.index(label) if label in names else -1
        report.require(
            enum_index != -1 and enum_index == name_index,
            f"{label} sits at the same index in the enum and in the table",
            f"enum index {enum_index}, table index {name_index}")

    body = function_body(text, FILL)
    if not report.require(body is not None,
                           f"the tree has {FILL}()"):
        return

    lock = body.find("spin_lock_bh(&eth->tx_lock)")
    unlock = body.find("spin_unlock_bh(&eth->tx_lock)")
    report.require(lock != -1 and unlock != -1 and lock < unlock,
                   f"{FILL}() takes the TX lock",
                   f"lock at {lock}, unlock at {unlock}")

    for label, entry, field in COUNTERS:
        assignment = re.search(
            r"data\[" + re.escape(entry) + r"\]\s*=\s*[^;]+;", body, re.S)
        report.require(assignment is not None,
                       f"{label} is given a value in {FILL}()")
        if assignment is None:
            continue
        where = assignment.start()
        report.require(
            lock != -1 and unlock != -1 and lock < where < unlock,
            f"{label} is read under the TX lock that protects it",
            f"the assignment is at {where}")
        report.require(
            f"eth->{field}" in assignment.group(0),
            f"{label} is read from the counter it names",
            assignment.group(0).strip()[:80])
        report.require(
            re.search(r"\b" + re.escape(field) + r"\b", head) is not None,
            f"the tree has the {field} counter")
        counted = re.search(re.escape(field) + r"\s*\+\+", text)
        report.require(counted is not None,
                       f"{field} is counted somewhere",
                       "the published counter would always read zero")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tree", type=Path, required=True,
                        help="the prepared kernel tree to check")
    args = parser.parse_args()

    report = Report()
    check(args.tree, report)
    files = tree_fingerprint(args.tree, [NETDEV, HEADER])
    write_summary(report, "The TX isolation counters the board can read",
                  files)
    if report.failures:
        print(f"\nFAILED: {len(report.failures)} of {report.checks} check(s)")
        for item in report.failures:
            print(f"  - {item}")
        return 1
    print(f"\nOK: {report.checks} check(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
