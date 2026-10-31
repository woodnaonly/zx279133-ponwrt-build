#!/usr/bin/env python3
"""Catch a static function whose name a header of this overlay has already taken.

Run 293 built the kernel overlay and the compiler stopped on two errors that
have one shape between them:

    kernel-overlays/drivers/net/ethernet/zte/zx279133-pon.c:127
        static int zxpon_set_identity(struct sk_buff *skb, struct genl_info *info)
    kernel-overlays/drivers/net/ethernet/zte/zxpon.h:105
        void zxpon_set_identity(struct zxpon *z, const uint8_t sn[],
                                const uint8_t regid[]);

C keeps one namespace for both, so the compiler reads the two as one function:
the definition is "conflicting types", the call is "too many arguments".  None
of that is a warning to be tidy about - the build produces no image, and it
finds out about forty minutes into a build that costs an hour.

This is not a compiler and does not try to be one.  It knows two shapes:

  * a static function defined in a .c file of the overlay,
  * a function declared (not defined, not static) in a header that the same
    .c file includes with #include "...".

Only the overlay's own headers are read.  A header that comes from the kernel
tree is not in this repository, and a name that the kernel's headers happen to
use is the kernel's business; the tree's headers are checked by the build
itself, and this gate must never refuse a good push.

Usage:  python3 tools/check-c-name-collision.py <repo root>
"""

import os
import re
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

SKIP_DIRS = {".git", "__pycache__", "build_dir", "staging", "dl", "tmp"}
ROOTS = ("kernel-overlays",)

# A #include with quotes: the header that lives next to the file.
QUOTED_INCLUDE = re.compile(r'^\s*#\s*include\s+"([^"]+)"')

# "static <type> <name>(" at the start of a line.  The name is the last
# identifier before the first "(".
STATIC_FN = re.compile(r"^static\s+[^=;]*?([A-Za-z_]\w*)\s*\(")

# A declaration of a function: something, a name, a parameter list, and the
# end of the statement.  It must not contain a brace (that would be a
# definition or a struct) and must not start with a "#" (that would be a
# macro).  The name is the identifier just before the "(".
DECL = re.compile(r"^(?P<decl>[^;{}#]*?)\b(?P<name>[A-Za-z_]\w*)\s*\((?P<args>[^;{}]*)\)\s*$")

# The words that are never a function's name, whatever they sit next to.
KEYWORDS = {
    "if", "for", "while", "switch", "return", "sizeof", "defined", "typedef",
    "struct", "union", "enum", "void", "int", "char", "short", "long", "float",
    "double", "signed", "unsigned", "const", "volatile", "static", "extern",
    "inline", "register", "auto", "case", "default", "do", "else", "goto",
    "break", "continue", "unsigned", "restrict", "_Bool", "bool",
}


def strip_c(src):
    """Blank out comments and string literals, but keep every line where it was.

    A name in a comment is not a declaration, and a name in a string is not a
    declaration either - but both of them have to stay on their own line,
    because the finding is reported with a line number.
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


def overlay_root(repo):
    return os.path.join(repo, ROOTS[0])


def local_headers(c_path, repo):
    """The headers the file includes with quotes, as paths inside the repo.

    A quoted include is looked for next to the file first, then in the
    overlay's include directory.  A header that is not in the repository (the
    tree's own headers) is not returned: this gate reads what it can see.
    """
    with open(c_path, encoding="utf-8", errors="replace") as fh:
        src = fh.read()

    found = []
    for line in src.splitlines():
        m = QUOTED_INCLUDE.match(line)
        if not m:
            continue
        name = m.group(1)
        for cand in (os.path.join(os.path.dirname(c_path), name),
                     os.path.join(overlay_root(repo), name),
                     os.path.join(overlay_root(repo), "include", name)):
            if os.path.isfile(cand):
                found.append(cand)
                break
    return found


def header_functions(path):
    """The names a header declares as functions, with the line they are on.

    A declaration is a statement that ends in ")".  A definition ends in "}",
    a struct or an enum has braces, a macro starts with a "#", and a typedef
    of a function pointer has its name inside the parentheses - none of those
    are a function this gate has to know about.
    """
    with open(path, encoding="utf-8", errors="replace") as fh:
        src = fh.read()

    # Comments and string literals go; preprocessor lines go with them, so a
    # macro cannot be glued onto the declaration that follows it.
    lines = []
    for line in strip_c(src).splitlines():
        if line.lstrip().startswith("#"):
            line = ""
        lines.append(line)

    names = {}
    statement = []
    start = 1
    for i, line in enumerate(lines, 1):
        if not statement:
            start = i
        statement.append(line)
        text = "\n".join(statement)
        if ";" not in text:
            continue
        # Everything up to the first ";" is the statement; the rest of the
        # line belongs to the next one.
        head, _, tail = text.partition(";")
        names.update(declared(head, start))
        statement = [tail] if tail.strip() else []
    return names


def declared(text, start_line):
    """The function names one statement declares."""
    flat = " ".join(text.split())
    if not flat:
        return {}
    m = DECL.match(flat)
    if not m:
        return {}
    decl = m.group("decl")
    name = m.group("name")
    if name in KEYWORDS:
        return {}
    if "static" in decl.split():
        return {}
    if "typedef" in decl.split():
        return {}
    return {name: start_line}


def static_functions(path):
    """The static functions a .c file defines or declares, with their lines."""
    with open(path, encoding="utf-8", errors="replace") as fh:
        lines = strip_c(fh.read()).splitlines()

    found = {}
    for i, line in enumerate(lines, 1):
        m = STATIC_FN.match(line)
        if m:
            found.setdefault(m.group(1), i)
    return found


def check(repo):
    """The problems in the whole overlay: (file, line, name, header)."""
    problems = []
    for c_path in c_files(repo):
        statics = static_functions(c_path)
        if not statics:
            continue
        for h_path in local_headers(c_path, repo):
            for name, line in header_functions(h_path).items():
                if name in statics:
                    problems.append((c_path, statics[name], name, h_path))
    return problems


def main():
    if len(sys.argv) != 2:
        print(__doc__)
        return 2
    repo = os.path.abspath(sys.argv[1])
    bad = 0
    for c_path, line, name, h_path in check(repo):
        print("%s:%d: %s is defined here as a static function, but %s declares a "
              "function of the same name - C has one namespace for both, so the "
              "compiler reads them as one function and stops with 'conflicting "
              "types' and 'too many arguments'"
              % (os.path.relpath(c_path, repo), line, name,
                 os.path.relpath(h_path, repo)))
        bad += 1
    print("name-collision: %d problems" % bad)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
