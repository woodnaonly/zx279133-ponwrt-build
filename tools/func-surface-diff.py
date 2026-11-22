#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Compare the function surface of two versions of a driver directory.

The 7.3 migration is not a version bump: the ZX279133 driver was rewritten
between the 6.18 generation and the 7.3 generation, so the question "what do
we have that the new tree does not" has to be answered by name, not by eye.
This prints the functions that exist in only one of the two trees, which is
the work list for the port.

  python3 tools/func-surface-diff.py <ours-dir> <theirs-dir>
"""
import os
import re
import sys

# A definition looks like "type name(args)" at column 0, and ends with '{' or
# is followed by one on the next line.  Declarations end with ';' and are
# skipped; assignments are skipped as well.
FUNC_RE = re.compile(
    r"^(?:static\s+|inline\s+|extern\s+)*"
    r"(?:const\s+)?(?:struct\s+\w+\s*\*|void\s*\*|char\s*\*|u8\s*\*|u16\s*\*|u32\s*\*|u64\s*\*|"
    r"int|bool|long|unsigned|signed|size_t|ssize_t|void|u8|u16|u32|u64|s8|s16|s32|s64|"
    r"__be16|__be32|__le16|__le32|sk_buff|netdev|struct \w+|enum \w+)\s*\*?\s*"
    r"(?P<name>[A-Za-z_][A-Za-z0-9_]*)\s*\("
)


def functions_in(path):
    names = {}
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            lines = fh.read().split("\n")
    except OSError:
        return names
    for number, line in enumerate(lines, start=1):
        if line[:1] in ("#", " ", "\t"):
            continue
        if "(" not in line or not line.rstrip().endswith((")", "{")):
            continue
        head = line.strip()
        if head.endswith(";"):
            continue
        if "=" in head.split("(")[0]:
            continue
        match = FUNC_RE.match(head)
        if match:
            names.setdefault(match.group("name"), []).append(number)
    return names


def collect(directory):
    found = {}
    if not os.path.isdir(directory):
        return found
    for name in sorted(os.listdir(directory)):
        if not name.endswith((".c", ".h")):
            continue
        for symbol, lines in functions_in(os.path.join(directory, name)).items():
            found.setdefault(symbol, []).append(name)
    return found


def main():
    if len(sys.argv) != 3:
        print(__doc__)
        return 2
    ours = collect(sys.argv[1])
    theirs = collect(sys.argv[2])
    only_ours = sorted(set(ours) - set(theirs))
    only_theirs = sorted(set(theirs) - set(ours))
    common = sorted(set(ours) & set(theirs))

    print("ours: %s" % sys.argv[1])
    print("theirs: %s" % sys.argv[2])
    print("functions: ours=%d theirs=%d common=%d only_ours=%d only_theirs=%d"
          % (len(ours), len(theirs), len(common), len(only_ours), len(only_theirs)))
    for title, names, table in (("only in ours", only_ours, ours),
                                ("only in theirs", only_theirs, theirs)):
        print("\n=== %s (%d) ===" % (title, len(names)))
        for symbol in names:
            print("  %-46s %s" % (symbol, ",".join(sorted(set(table[symbol])))))
    return 0


if __name__ == "__main__":
    sys.exit(main())
