#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Compare two versions of one driver file function by function.

tools/func-surface-diff.py answers "which function names exist in only one
of the two trees".  That is the first question of the port, but not the
second one: a function can keep its name and still be a different function.
The ZX279133 IDM is the clearest case - 40 of its functions exist in both
generations, and 22 of those are byte-for-byte the same, while 9 of them
share a name and share almost no code.

So this compares the bodies.  For every function name that exists in both
files it reports one of three verdicts:

  IDENTICAL - the bodies are the same once comments and blank lines are
              removed.  Nothing to port.
  similar   - most lines are shared.  A look at the difference is enough.
  REWRITE   - the bodies have little in common.  This is the work.

and it lists the functions that exist on only one side.

  python3 tools/func-body-diff.py <ours.c> <theirs.c> [--threshold 0.6]
"""
import argparse
import os
import re
import sys

# A definition looks like "type name(args)" at column 0.  Declarations end
# with ';' and assignments are skipped.
FUNC_RE = re.compile(
    r"^(?:static\s+|inline\s+|extern\s+)*"
    r"(?:const\s+)?(?:struct\s+\w+\s*\*|void\s*\*|char\s*\*|u8\s*\*|u16\s*\*|u32\s*\*|u64\s*\*|"
    r"int|bool|long|unsigned|signed|size_t|ssize_t|void|u8|u16|u32|u64|s8|s16|s32|s64|"
    r"__be16|__be32|__le16|__le32|sk_buff|netdev|struct \w+|enum \w+)\s*\*?\s*"
    r"(?P<name>[A-Za-z_][A-Za-z0-9_]*)\s*\("
)

# Types the strict pattern above does not know about; a definition that the
# strict pattern misses shows up as "only in ours" next to a function with a
# very similar body, which is worse than a slightly loose match.
LOOSE_RE = re.compile(r"^(?P<name>[A-Za-z_][A-Za-z0-9_]*)\s*\(")


def read_lines(path):
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as handle:
            return handle.read().split("\n")
    except OSError:
        return []


KEYWORDS = {
    "if", "for", "while", "switch", "return", "sizeof", "do", "else", "case",
    "goto", "break", "continue", "defined", "offsetof", "typeof",
    "container_of", "struct", "union", "enum", "static", "const", "inline",
    "extern", "unsigned", "signed", "void", "int", "long", "char", "bool",
    "u8", "u16", "u32", "u64", "s8", "s16", "s32", "s64",
}


def strip_comment(line):
    return line.split("//")[0]


def find_body(lines, start):
    """Return (first brace line, closing brace line) of a function body.

    The braces are counted, so a body that contains braces of its own is
    still bounded correctly - which is what a "next definition starts
    here" heuristic cannot do.
    """
    depth = 0
    opened = None
    in_block_comment = False
    for index in range(start, min(start + 400, len(lines))):
        line = lines[index]
        code = []
        position = 0
        while position < len(line):
            if in_block_comment:
                end = line.find("*/", position)
                if end < 0:
                    position = len(line)
                    break
                position = end + 2
                in_block_comment = False
                continue
            begin = line.find("/*", position)
            if begin >= 0:
                code.append(line[position:begin])
                in_block_comment = True
                position = begin + 2
                continue
            code.append(line[position:])
            break
        text = "".join(code)
        for char in text:
            if char == "{":
                if opened is None:
                    opened = index
                depth += 1
            elif char == "}":
                depth -= 1
                if depth == 0 and opened is not None:
                    return opened, index
    return None, None


def definition_lines(lines):
    """Map function name -> line number, for definitions only."""
    names = {}
    for index, line in enumerate(lines):
        if line[:1] in (" ", "\t", "#"):
            continue
        code = strip_comment(line)
        if "(" not in code:
            continue
        if "=" in code.split("(")[0]:
            continue
        match = re.search(r"([A-Za-z_]\w*)\s*\(", code)
        if not match:
            continue
        name = match.group(1)
        if name in KEYWORDS or name.startswith("__"):
            continue
        opened, closed = find_body(lines, index)
        if opened is None or closed is None:
            continue
        names.setdefault(name, index + 1)
    return names


def bodies(path):
    lines = read_lines(path)
    starts = definition_lines(lines)
    result = {}
    for name, line in starts.items():
        opened, closed = find_body(lines, line - 1)
        if opened is None or closed is None:
            continue
        result[name] = (line, "\n".join(lines[line - 1:closed + 1]))
    return result


def normalize(body):
    """Drop comments and blank lines, so two bodies can be compared."""
    out = []
    for line in body.splitlines():
        line = re.sub(r"/\*.*?\*/", "", line, flags=re.S)
        line = re.sub(r"//.*$", "", line)
        line = line.strip()
        if line:
            out.append(line)
    return out


def compare(ours, theirs, threshold):
    ours_body = bodies(ours)
    theirs_body = bodies(theirs)
    ours_lines = {name: line for name, (line, _) in ours_body.items()}
    theirs_lines = {name: line for name, (line, _) in theirs_body.items()}

    only_ours = sorted(set(ours_lines) - set(theirs_lines))
    only_theirs = sorted(set(theirs_lines) - set(ours_lines))
    common = sorted(set(ours_lines) & set(theirs_lines))

    print("ours  : %s  (%d functions)" % (ours, len(ours_lines)))
    print("theirs: %s  (%d functions)" % (theirs, len(theirs_lines)))
    print("both: %d   only ours: %d   only theirs: %d"
          % (len(common), len(only_ours), len(only_theirs)))

    print("\n=== only in ours: has to be ported as new code (%d) ===" % len(only_ours))
    for name in only_ours:
        print("  %-46s L%-6d %s" % (name, ours_lines[name], ours))

    print("\n=== only in theirs: the new tree already does this differently (%d) ==="
          % len(only_theirs))
    for name in only_theirs:
        print("  %-46s L%-6d %s" % (name, theirs_lines[name], theirs))

    print("\n=== in both: how different is the body? ===")
    print("  %-46s %6s %7s  verdict" % ("function", "ours", "theirs"))
    buckets = {"IDENTICAL": [], "similar": [], "REWRITE": []}
    for name in common:
        a = normalize(ours_body[name][1])
        b = normalize(theirs_body[name][1])
        if a == b:
            verdict, key = "IDENTICAL", "IDENTICAL"
        else:
            shared = len(set(a) & set(b))
            ratio = shared / max(1, max(len(a), len(b)))
            if ratio >= threshold:
                verdict, key = "similar %3.0f%%" % (ratio * 100), "similar"
            else:
                verdict, key = "REWRITE %3.0f%%" % (ratio * 100), "REWRITE"
        buckets[key].append(name)
        print("  %-46s %6d %7d  %s"
              % (name, len(a), len(b), verdict))

    print("\nidentical: %d   similar: %d   rewrite: %d"
          % (len(buckets["IDENTICAL"]), len(buckets["similar"]),
             len(buckets["REWRITE"])))
    print("\n--- nothing to port ---")
    for name in buckets["IDENTICAL"]:
        print("  %s" % name)
    print("\n--- has to be written again ---")
    for name in buckets["REWRITE"]:
        print("  %s" % name)
    return buckets


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("ours")
    parser.add_argument("theirs")
    parser.add_argument("--threshold", type=float, default=0.6,
                        help="share of common lines above which a body counts as similar")
    args = parser.parse_args()

    if not os.path.exists(args.ours) or not os.path.exists(args.theirs):
        print("one of the two files does not exist", file=sys.stderr)
        return 2

    compare(args.ours, args.theirs, args.threshold)
    return 0


if __name__ == "__main__":
    sys.exit(main())
