#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Compare the IDM register writes of two generations, statement by statement.

The function-body diff (tools/func-body-diff.py) says "this function has to
be written again", but it does not say why.  For the IDM the reason is nearly
always one register write, so the useful unit of comparison is the
statement: which register gets which value in each generation.

This is how the first slice of the IDM port was measured.  With the
measured-register patch applied to the 7.3 tree, the two generations write
the same value to the same register in every statement of
zx279133_idm_tx_configure except the five buffer-pool ones - and those are
the ones the 7.3 tree already gets right.

    python3 tools/idm-register-diff.py <ours.c> <theirs.c> [function]
"""
import re
import sys

# The value may contain one level of parentheses (a helper call such as
# zx279133_idm_bp_ring_cfg(A, B)), and the argument list of that call
# contains a comma of its own.
WRITE_RE = re.compile(
    r"writel\(\s*(?P<value>(?:[^,()]|\([^()]*\))*?)\s*,\s*"
    r"idm\s*\+\s*(?P<offset>0x[0-9a-fA-F]+)\s*\)",
    re.S)


def function_body(path, name):
    lines = open(path, "r", encoding="utf-8", errors="replace").read().split("\n")
    start = None
    pattern = re.compile(r"^(?:static\s+)?[\w\s\*]*\b" + re.escape(name) + r"\s*\(")
    for index, line in enumerate(lines):
        if line[:1] in (" ", "\t"):
            continue
        if pattern.match(line):
            start = index
            break
    if start is None:
        return None
    depth = 0
    opened = False
    for index in range(start, min(start + 500, len(lines))):
        depth += lines[index].count("{") - lines[index].count("}")
        if "{" in lines[index]:
            opened = True
        if opened and depth == 0:
            return "\n".join(lines[start:index + 1])
    return "\n".join(lines[start:start + 200])


def writes(body):
    out = []
    for match in WRITE_RE.finditer(body):
        value = " ".join(match.group("value").split())
        out.append((match.group("offset").lower(), value))
    return out


def main():
    if len(sys.argv) < 3:
        print(__doc__)
        return 2
    ours_path, theirs_path = sys.argv[1], sys.argv[2]
    name = sys.argv[3] if len(sys.argv) > 3 else "zx279133_idm_tx_configure"

    ours = writes(function_body(ours_path, name) or "")
    theirs = writes(function_body(theirs_path, name) or "")
    print("%s: ours %d writes, theirs %d writes\n" % (name, len(ours), len(theirs)))

    print("%-3s %-10s %-36s %s" % ("", "offset", "ours (6.18 generation)",
                                   "theirs (7.3 generation)"))
    print("-" * 96)
    index = 0
    while index < max(len(ours), len(theirs)):
        left = ours[index] if index < len(ours) else ("-", "-")
        right = theirs[index] if index < len(theirs) else ("-", "-")
        mark = " " if left == right else "*"
        print("%s %-9s %-36s %s" % (mark, left[0], left[1], right[1]))
        index += 1

    differing = sum(1 for index in range(max(len(ours), len(theirs)))
                    if (ours[index] if index < len(ours) else None)
                    != (theirs[index] if index < len(theirs) else None))
    print("\n%d of %d statements differ" % (differing, max(len(ours), len(theirs))))
    return 0


if __name__ == "__main__":
    sys.exit(main())
