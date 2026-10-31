#!/usr/bin/env python3
"""Catch a call to a static function before the compiler has been told about it.

The kernel builds with -Werror=implicit-function-declaration (since 6.5), so a
call to a static function that is defined further down the same file, with no
prototype above the call, is not a warning to be tidy about - it is a build
that never produces an image.  The overlay is compiled on a runner, and a
runner is where that costs an hour.

This is not a compiler and does not try to be one.  It knows three shapes and
nothing else:

  * a static function defined at the start of a line,
  * a static function declared (a prototype) at the start of a line,
  * a call: an identifier followed by "(", not preceded by "." or "->" and not
    preceded by a word character.

Only names that the file itself introduces as "static something (" are looked
at.  The kernel's own functions, macros, function pointers and everything the
tree's headers bring in are left alone, because this has to be a gate that
never refuses a good push.

Usage:  python3 tools/check-c-use-before-decl.py <repo root>
"""

import os
import re
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

SKIP_DIRS = {".git", "__pycache__", "build_dir", "staging", "dl", "tmp"}
ROOTS = ("kernel-overlays",)

# "static <type> <name>(" at the start of a line.  The name is the last
# identifier before the first "(".
STATIC_FN = re.compile(r"^static\s+[^=;]*?([A-Za-z_]\w*)\s*\(")

# A call: an identifier followed by "(", not a member access and not a longer
# identifier.
CALL = re.compile(r"(?<![.\w>])([A-Za-z_]\w*)\s*\(")


def strip_c(src):
    """Blank out comments and string literals, but keep every line where it was.

    A name in a comment is not a call, and a name in a string is not a call
    either - but both of them have to stay on their own line, because the
    finding is reported with a line number.
    """
    out = []
    i, n = 0, len(src)
    while i < n:
        c = src[i]
        if c == "/" and i + 1 < n and src[i + 1] == "/":
            while i < n and src[i] != "\n":
                out.append(" ")
                i += 1
        elif c == "/" and i + 1 < n and src[i + 1] == "*":
            out.append("  ")
            i += 2
            while i + 1 < n and not (src[i] == "*" and src[i + 1] == "/"):
                out.append("\n" if src[i] == "\n" else " ")
                i += 1
            out.append("  ")
            i += 2
        elif c == '"':
            out.append(" ")
            i += 1
            while i < n and src[i] != '"':
                out.append("\n" if src[i] == "\n" else " ")
                i += 2 if src[i] == "\\" else 1
            out.append(" ")
            i += 1
        elif c == "'":
            out.append(" ")
            i += 1
            while i < n and src[i] != "'":
                out.append("\n" if src[i] == "\n" else " ")
                i += 2 if src[i] == "\\" else 1
            out.append(" ")
            i += 1
        else:
            out.append(c)
            i += 1
    return "".join(out)


def c_files(repo):
    for root in ROOTS:
        base = os.path.join(repo, root)
        if not os.path.isdir(base):
            continue
        for dirpath, dirnames, names in os.walk(base):
            dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
            for n in names:
                if n.endswith(".c"):
                    yield os.path.join(dirpath, n)


def check(path):
    """The problems in one file: a list of (line, name, ready_line)."""
    with open(path, encoding="utf-8", errors="replace") as fh:
        lines = strip_c(fh.read()).splitlines()

    # The earliest line at which each static name is known: its definition or
    # its prototype, whichever comes first.
    ready = {}
    decl_lines = set()
    for i, line in enumerate(lines, 1):
        m = STATIC_FN.match(line)
        if not m:
            continue
        name = m.group(1)
        decl_lines.add(i)
        if name not in ready or i < ready[name]:
            ready[name] = i

    problems = []
    for i, line in enumerate(lines, 1):
        if i in decl_lines:
            continue
        for m in CALL.finditer(line):
            name = m.group(1)
            first = ready.get(name)
            if first is None:
                continue
            if i < first:
                problems.append((i, name, first))
    return problems


def main():
    if len(sys.argv) != 2:
        print(__doc__)
        return 2
    repo = os.path.abspath(sys.argv[1])
    bad = 0
    for f in c_files(repo):
        for line, name, first in check(f):
            print("%s:%d: %s is used before it is declared: the compiler is "
                  "only told about it at line %d, and the kernel builds with "
                  "-Werror=implicit-function-declaration, so this build would "
                  "not produce an image"
                  % (os.path.relpath(f, repo), line, name, first))
            bad += 1
    print("use-before-decl: %d problems" % bad)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
