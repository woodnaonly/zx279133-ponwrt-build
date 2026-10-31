#!/usr/bin/env python3
"""Catch a name the compiler has not been told about yet, when it is used.

The kernel builds with -Werror=implicit-function-declaration (since 6.5), so a
call to a static function that is defined further down the same file, with no
prototype above the call, is not a warning to be tidy about - it is a build
that never produces an image.  The overlay is compiled on a runner, and a
runner is where that costs an hour.

Run 293 found the same thing about an object rather than a function:
"zxpon_family" was used at line 110 of zx279133-pon.c and declared at line
222, so the compiler stopped with "'zxpon_family' undeclared".  A file-scope
static object is therefore checked the same way a static function is.

This is not a compiler and does not try to be one.  It knows four shapes and
nothing else:

  * a static function defined at the start of a line,
  * a static function declared (a prototype) at the start of a line,
  * a call: an identifier followed by "(", not preceded by "." or "->" and not
    preceded by a word character,
  * a file-scope static object: "static <type> <name>" with an initializer or
    a tentative definition.

Only names that the file itself introduces as "static something (" or as a
"static <type> <name>" are looked at.  The kernel's own functions, macros,
function pointers and everything the tree's headers bring in are left alone,
because this has to be a gate that never refuses a good push.

For an object the question is asked of the FIRST mention of the name in the
file, because that is the mention the compiler complains about: if the first
mention reads as a declaration - "int name", "int *name", a name in an
argument list - the compiler knows the name and says nothing, and a local or
a parameter of the same name is exactly what such a mention is.  A first
mention that reads as a use - "&name", "name.field", "name = ..." - is a name
the compiler has not been told about yet.

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

# A file-scope static object: "static <type> <name>", with an initializer or
# as a tentative definition.  The name is the last identifier before the
# "=", the ";" or the "[", so "static struct genl_family zxpon_family = {"
# gives zxpon_family and not genl_family.  A function is not matched: its
# name is followed by "(" and not by "=", ";" or "[".
STATIC_VAR = re.compile(
    r"^static\s+"
    r"(?:(?:const|volatile|unsigned|signed|struct|union|enum)\s+)*"
    r"(?:[A-Za-z_]\w*\s+)*"
    r"([A-Za-z_]\w*)\s*(?:\[[^\]]*\])?\s*(?:=[^=]|;)")

# An identifier that is not a member of something else and not part of a
# longer name.
IDENT = re.compile(r"(?<![.\w>])([A-Za-z_]\w*)(?!\w)")


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


def static_objects(lines):
    """The file-scope static objects a file declares, and where."""
    found = {}
    for i, line in enumerate(lines, 1):
        m = STATIC_VAR.match(line)
        if not m:
            continue
        name = m.group(1)
        if name not in found or i < found[name]:
            found[name] = i
    return found


def first_mention(lines, name, before):
    """Where the name is first mentioned, as (line, column), or None.

    Only the lines before the declaration are looked at: a mention after it
    is a mention the compiler has been told about.
    """
    for i in range(before - 1):
        for m in IDENT.finditer(lines[i]):
            if m.group(1) == name:
                return (i + 1, m.start(1))
    return None


def reads_as_declaration(line, column):
    """Whether a mention reads as a declaration of something of that name.

    "int name", "int *name", "struct foo name" and a name in an argument list
    all read as declarations - and a local or a parameter of the same name is
    exactly what such a mention is, so the compiler knows the name and says
    nothing about it.  "&name", "name.field" and "name = ..." read as uses.
    """
    before = line[:column].rstrip()
    if not before:
        return False
    last = before[-1]
    return last.isalnum() or last == "_" or last in "*,("


def check_objects(path):
    """The static objects a file uses before it has told the compiler about them."""
    with open(path, encoding="utf-8", errors="replace") as fh:
        lines = strip_c(fh.read()).splitlines()

    problems = []
    for name, declared_at in static_objects(lines).items():
        first = first_mention(lines, name, declared_at)
        if first is None:
            continue
        line, column = first
        if reads_as_declaration(lines[line - 1], column):
            continue
        problems.append((line, name, declared_at))
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
        for line, name, declared_at in check_objects(f):
            print("%s:%d: %s is used before it is declared: the compiler is "
                  "only told about it at line %d, so this build would stop with "
                  "'%s undeclared' and would not produce an image"
                  % (os.path.relpath(f, repo), line, name, declared_at, name))
            bad += 1
    print("use-before-decl: %d problems" % bad)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
