#!/usr/bin/env python3
"""The checks this project wishes it had before a push.

    python staging/ctl/prepush.py [repo dir]

Four things a compiler would have caught, that a one hour build cycle should
not have to:

  1. a #define line with two #defines on it.  The preprocessor reads the
     second one as part of the first one's replacement list, so every use of
     the first macro expands to a stray '#' and the file stops compiling.
  2. a #define whose replacement list contains a '#', which is the same
     mistake in another shape.
  3. an unbalanced brace, bracket or parenthesis.
  4. a ZX279133_* macro that is used but never defined.

Exit status 0 means clean.
"""
import os
import re
import sys

REPO = sys.argv[1] if len(sys.argv) > 1 else os.getcwd()
ROOTS = ("kernel-overlays", "overlays", "scripts", "templates")
SKIP_DIRS = {".git", "__pycache__", "node_modules"}


def strip_c(src):
    out = []
    i, n = 0, len(src)
    while i < n:
        c = src[i]
        if c == '/' and i + 1 < n and src[i + 1] == '/':
            while i < n and src[i] != '\n':
                i += 1
        elif c == '/' and i + 1 < n and src[i + 1] == '*':
            i += 2
            while i + 1 < n and not (src[i] == '*' and src[i + 1] == '/'):
                i += 1
            i += 2
        elif c == '"':
            i += 1
            while i < n and src[i] != '"':
                i += 2 if src[i] == '\\' else 1
            i += 1
        elif c == "'":
            i += 1
            while i < n and src[i] != "'":
                i += 2 if src[i] == '\\' else 1
            i += 1
        else:
            out.append(c)
            i += 1
    return ''.join(out)


def c_files():
    for root in ROOTS:
        base = os.path.join(REPO, root)
        if not os.path.isdir(base):
            continue
        for dirpath, dirnames, names in os.walk(base):
            dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
            for n in names:
                if n.endswith(('.c', '.h')):
                    yield os.path.join(dirpath, n)


def main():
    bad = 0
    files = list(c_files())
    if not files:
        print("prepush: no C files under %s" % REPO)
        return 1

    defined = set()
    for f in files:
        text = open(f, encoding='utf-8', errors='replace').read()
        for ln, line in enumerate(text.splitlines(), 1):
            s = line.split('/*')[0]
            n_def = s.count('#define')
            if n_def > 1:
                print("%s:%d: %d #defines on one line: %s"
                      % (os.path.relpath(f, REPO), ln, n_def, line.strip()))
                bad += 1
            m = re.match(r'\s*#\s*define\s+([A-Za-z_][A-Za-z0-9_]*)\s*(.*)$', s)
            if m:
                name, rest = m.group(1), m.group(2)
                defined.add(name)
                rest = rest.split('\\')[0]
                if '#' in rest:
                    print("%s:%d: a # inside a #define's replacement list: %s"
                          % (os.path.relpath(f, REPO), ln, line.strip()))
                    bad += 1

        # enum members, so that a name like ZX279133_STAT_TX_PACKETS counts
        # as defined.
        for em in re.finditer(r'\benum\b[^{]*\{(.*?)\}', text, re.S):
            for mem in re.finditer(r'\b([A-Za-z_][A-Za-z0-9_]*)\s*(?:=[^,}]*)?,',
                                   em.group(1)):
                defined.add(mem.group(1))

    for f in files:
        src = strip_c(open(f, encoding='utf-8', errors='replace').read())
        for open_ch, close_ch in (('{', '}'), ('(', ')'), ('[', ']')):
            depth = minimum = 0
            for ch in src:
                if ch == open_ch:
                    depth += 1
                elif ch == close_ch:
                    depth -= 1
                    minimum = min(minimum, depth)
            if depth != 0 or minimum < 0:
                print("%s: %s is unbalanced (balance %+d, min %d)"
                      % (os.path.relpath(f, REPO), open_ch, depth, minimum))
                bad += 1

    used = {}
    for f in files:
        if not f.endswith('.c'):
            continue
        for ln, line in enumerate(open(f, encoding='utf-8', errors='replace'),
                                  1):
            for m in re.finditer(r'\b(ZX279133_[A-Za-z0-9_]*)', line):
                used.setdefault(m.group(1), (f, ln))
    for name, (f, ln) in sorted(used.items()):
        if name not in defined:
            # Some names live in the tree's own headers, which this overlay
            # does not carry (zx279133-lan.h and zx279133-stats.h are the
            # tree's).  Report them, but do not call them problems.
            print("%s:%d: note: %s is not defined in this repo (the tree's "
                  "own header may have it)" % (os.path.relpath(f, REPO), ln,
                                               name))

    print("prepush: %d files, %d problems" % (len(files), bad))
    return 1 if bad else 0


if __name__ == '__main__':
    sys.exit(main())
