#!/usr/bin/env python3
"""Check that every ZX279133_* macro a driver file uses is reachable for it.

    python3 tools/macrocheck.py <kernel-tree> [driver-dir]

Why this exists
---------------
Run 124 spent 78 minutes of CI time to fail with nine lines of

    zx279133-switch.c:647:29: error: 'ZX279133_SMAC_COUNT' undeclared

The overlay's zx279133-switch.c included only "zx279133-lan.h", while the
SMAC constants had been added to "zx279133.h" - a header that file never
included.  The other five driver files do include "zx279133.h", so they
compiled, and nothing else in the tree noticed.  The macro existed; the file
just could not see it.

A compiler is the only thing that can see that kind of mistake, and a
compiler here means a 70-minute build.  This script does the same job in a
few seconds: it follows the #include graph of every driver source, collects
the ZX279133_* macros those headers define, and reports the ones a source
uses without being able to see them.

Run it against the kernel tree *after* the overlay has been copied into it,
because that is where the two sets have to meet.
"""

import os
import re
import sys

# A macro name: the driver's own prefix, then an upper-case tail.
NAME = re.compile(r'\b(ZX279133_[A-Z0-9_]+)\b')
# "#define NAME" or "#define NAME(...)" at the start of a line.
DEFINE = re.compile(r'^\s*#\s*define\s+(ZX279133_[A-Z0-9_]+)', re.M)
# An #include, quoted or angled.
INCLUDE = re.compile(r'^\s*#\s*include\s+([<"])([^>"]+)[>"]', re.M)

# Where a header can live, relative to the tree root.
HEADER_ROOTS = (
    'drivers/net/ethernet/zte',
    'include',
    'arch/arm64/include',
    'net',
)


def read(path):
    try:
        with open(path, encoding='utf-8', errors='replace') as fh:
            return fh.read()
    except OSError:
        return None


def strip_comments(text):
    text = re.sub(r'/\*.*?\*/', ' ', text, flags=re.S)
    return re.sub(r'//[^\n]*', ' ', text)


class Tree:
    """The tree, with a header index and an #include resolver."""

    def __init__(self, root):
        self.root = root
        self.index = {}
        self.cache = {}
        self.defines_in = {}
        self.driver_dir = os.path.join(root, 'drivers/net/ethernet/zte')
        for d in HEADER_ROOTS:
            base = os.path.join(root, d)
            if not os.path.isdir(base):
                continue
            for dirpath, _dirnames, filenames in os.walk(base):
                for fn in filenames:
                    if not fn.endswith('.h'):
                        continue
                    path = os.path.join(dirpath, fn)
                    self.index.setdefault(fn, []).append(path)
                    here = 'driver' if path.startswith(self.driver_dir) else path
                    for name in DEFINE.findall(self.text(path)):
                        self.defines_in.setdefault(name, here)

    def text(self, path):
        if path not in self.cache:
            t = read(path)
            self.cache[path] = t if t is not None else ''
        return self.cache[path]

    def resolve(self, name, angled, from_dir):
        """The file an #include refers to, or None."""
        cands = []
        if not angled:
            cands.append(os.path.join(from_dir, name))
            cands.append(os.path.join(self.root, 'include', name))
            cands.append(os.path.join(self.root, 'arch/arm64/include', name))
            for p in self.index.get(os.path.basename(name), []):
                cands.append(p)
        else:
            cands.append(os.path.join(self.root, 'include', name))
            cands.append(os.path.join(self.root, 'arch/arm64/include', name))
        for c in cands:
            if os.path.isfile(c):
                return c
        return None

    def reachable_defines(self, path):
        """The ZX279133_* macros this file can see through its includes.

        The walk is iterative on purpose: a kernel driver's include graph is
        deep (linux/netdevice.h alone reaches hundreds of headers), and a
        recursive walk of it runs into Python's recursion limit - which is
        what run 125 did, five minutes into the build, with a traceback
        instead of a verdict.
        """
        names = set()
        seen = set()
        stack = [path]
        while stack:
            cur = stack.pop()
            if cur in seen:
                continue
            seen.add(cur)
            text = self.text(cur)
            names.update(DEFINE.findall(text))
            for _q, name in INCLUDE.findall(text):
                child = self.resolve(name, _q == '<', os.path.dirname(cur))
                if child and child not in seen:
                    stack.append(child)
        return names

    def local_defines(self, path):
        return set(DEFINE.findall(self.text(path)))


def main():
    if len(sys.argv) < 2:
        sys.exit('usage: macrocheck.py <kernel-tree> [driver-dir]')
    root = sys.argv[1]
    drv = sys.argv[2] if len(sys.argv) > 2 else 'drivers/net/ethernet/zte'

    if not os.path.isdir(root):
        sys.exit('no such kernel tree: %s' % root)
    drvdir = os.path.join(root, drv)
    if not os.path.isdir(drvdir):
        sys.exit('no such driver directory: %s' % drvdir)

    tree = Tree(root)
    problems = []
    warnings = []
    nsources = 0
    for fn in sorted(os.listdir(drvdir)):
        if not fn.endswith('.c'):
            continue
        nsources += 1
        path = os.path.join(drvdir, fn)
        text = strip_comments(tree.text(path))
        used = set(NAME.findall(text))
        visible = tree.reachable_defines(path) | tree.local_defines(path)
        for name in sorted(used - visible):
            where = tree.defines_in.get(name)
            if where is None:
                problems.append('%s: %s is used but nothing in the tree defines it'
                                % (fn, name))
            elif where == 'driver':
                problems.append('%s: %s is defined in the driver\'s own headers '
                                'but not in any header this file includes'
                                % (fn, name))
            else:
                # Defined somewhere else in the tree: most likely this file is
                # missing an include, but it could also be that the resolver
                # does not follow that include.  Worth saying out loud.
                warnings.append('%s: %s is used but not reachable from any '
                                'header this file includes (it is defined in '
                                '%s)' % (fn, name, where))

    print('macrocheck: %d sources, %d headers indexed' % (nsources, len(tree.index)))
    for w in warnings:
        print('  warning: ' + w)
    if problems:
        print('macrocheck: %d problem(s):' % len(problems))
        for p in problems:
            print('  ' + p)
        return 1
    print('macrocheck: 0 problems, %d warning(s)' % len(warnings))
    return 0


if __name__ == '__main__':
    sys.exit(main())
