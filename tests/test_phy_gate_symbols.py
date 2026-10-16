#!/usr/bin/env python3
# 本地排演套件（local rehearsal suite）：依赖 staging/ 下的真实构建产物，
# 在只 clone 本仓库的环境里跑不起来，也**没有**接进 CI。
# 为什么、怎么跑、缺的那个夹具从哪来：同目录 README.md。
"""The CI PHY gate demands seven symbols be `=y` in the merged kernel .config and hard-exits
otherwise. Two things can break it, and only one of them is the thing that has already broken
twice this project:

  1. the symbol name does not exist in this tree at all, so no merged config can ever contain
     `CONFIG_<name>=y` -> the gate can never pass and every build dies at that step;
  2. the symbol exists but is `=m` or unset, which depends on the build, not on the name.

(1) is decidable offline, from the kernel tree's Kconfig. (2) is not: there is no merged
.config on this machine, and one only exists after a build. This script decides (1) and
says plainly that it does not decide (2).

THE REASON THIS SCRIPT RESOLVES `source` LINES
----------------------------------------------
A first pass reported REALTEK_PHY "NOT FOUND" after reading only drivers/net/phy/Kconfig.
That was wrong. Realtek PHY drivers live in drivers/net/phy/realtek/, and the parent merely
does `source "drivers/net/phy/realtek/Kconfig"`. Reading one file and reporting "absent" is
the non-recursive-tree mistake, and it has already been made once in this project. So the
resolver follows `source` and only reports absence after the graph is exhausted - and when a
symbol is found, it prints the chain that led to it.
"""
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
DST = os.path.join(HERE, 'kernelsrc')
ENTRY = 'drivers/net/phy/Kconfig'

WANTED = ['REALTEK_PHY', 'LEDS_GPIO', 'GPIO_ZX279133', 'GPIO_SYSFS',
          'PSTORE', 'PSTORE_CONSOLE', 'PSTORE_RAM']

# Dependency symbols that decide whether the seven can actually be set. Not asserted as
# errors - only listed, because whether they end up =y is a property of the build.
DEPS = ['SYSFS', 'EXPERT', 'LEDS_CLASS', 'GPIOLIB', 'OF_GPIO', 'HAS_IOMEM',
        'ARCH_ZX279133', 'ARCH_ZX279051']

BLOCK_START = re.compile(r'^\s*config\s+([A-Z0-9_]+)\s*$')
TYPE = re.compile(r'^\s*(bool|tristate|def_bool|def_tristate|hex|string)\b')
# re.M is REQUIRED. Without it `^` anchors to the start of the whole file, not to a line, and
# `findall` returns an empty list - so `collect` walks nothing and the suite reports every
# symbol as "not a Kconfig symbol". That is the fourth time in this project a checker has
# silently collected less than it meant to and then reported absence; here it would have
# invented a third CI bug out of a missing regex flag.
SOURCE = re.compile(r'^\s*(?:menu)?source\s+"([^"]+)"', re.M)

fail = 0
checks = 0


def ok(msg):
    global checks
    checks += 1
    print('PASS %s' % msg)


def bad(msg):
    global checks, fail
    checks += 1
    fail += 1
    print('FAIL %s' % msg)


def note(msg):
    print('note %s' % msg)


def read(path):
    p = os.path.join(DST, path.replace('/', '_'))
    if not os.path.exists(p):
        return None
    with open(p, encoding='utf-8', errors='replace') as f:
        return f.read()


def collect(path, acc, chain=(), depth=0, unreadable=None):
    """Walk the Kconfig source graph, accumulating {symbol: (path, chain, type, body)}."""
    if depth > 4 or path in chain:
        return
    text = read(path)
    if text is None:
        # Not a silent return. A file that could not be read means the walk covered less
        # than it looks like it covered, and reporting symbols as absent from a partial walk
        # is how you invent a missing symbol that is not missing.
        if unreadable is not None:
            unreadable.append(path)
        return
    lines = text.splitlines()
    for i, ln in enumerate(lines):
        m = BLOCK_START.match(ln)
        if not m:
            continue
        name = m.group(1)
        if name in acc:
            continue
        typ = None
        body = []
        for j in range(i + 1, min(i + 80, len(lines))):
            # A Kconfig symbol block has no explicit closing brace in the common case. Every
            # line belonging to it is indented; the first line that starts in column 0 is the
            # next symbol, an `if`, or `endif`. Stopping only on a lone `}` overran into the
            # NEXT symbol, which produced two confidently wrong notes:
            #   REALTEK_PHY "sits under `if REALTEK_PHY`"  (that `if` opens the next symbol)
            #   PSTORE "depends on PSTORE"                 (that is its own child)
            if lines[j] and not lines[j][0].isspace():
                break
            body.append(lines[j])
            if typ is None:
                t = TYPE.match(lines[j])
                if t:
                    typ = t.group(1)
        acc[name] = (path, list(chain), typ, '\n'.join(body))
    srcs = SOURCE.findall(text)
    if not srcs:
        return
    for src in srcs:
        collect(src, acc, chain + (path,), depth + 1, unreadable)


def main():
    # Three entry points, because the seven symbols do not live under one subtree:
    # LEDS_GPIO is in drivers/leds, GPIO_* in drivers/gpio, PSTORE* in fs/pstore, and only
    # REALTEK_PHY is under drivers/net/phy. Walking from a single entry made the other six
    # look absent - which is the same false-absence this suite is meant to prevent.
    entries = [ENTRY, 'drivers/leds/Kconfig', 'drivers/gpio/Kconfig', 'fs/pstore/Kconfig']
    acc = {}
    unreadable = []
    missing_entry = []
    for e in entries:
        if read(e) is None:
            missing_entry.append(e)
            continue
        collect(e, acc, unreadable=unreadable)

    if missing_entry:
        bad('entry Kconfig not fetched: %s - run fetch_kernel_kconfig.py'
            % ', '.join(missing_entry))
        return 1
    ok('all %d entry Kconfigs are on disk' % len(entries))
    ok('resolved %d symbols across the source graph' % len(acc))

    if unreadable:
        # Not fatal on its own, but it caps how far the word "absent" below can be trusted,
        # so it is stated before any absence is reported.
        note('WALK INCOMPLETE - %d file(s) could not be read, so an absence below may be'
             % len(unreadable))
        note('  an artefact of the walk: %s' % ', '.join(sorted(set(unreadable))))

    # The specific way this suite can go wrong is reporting absence from too little.
    if 'REALTEK_PHY' in acc:
        path, chain, _, _ = acc['REALTEK_PHY']
        ok('REALTEK_PHY found at %s' % path)
        note('  reached through: %s' % ' -> '.join(chain + [path]))
        if path != ENTRY:
            note('  a one-file check of %s would have reported it MISSING - that is the'
                 % ENTRY)
            note('  non-recursive-tree mistake, and it is what this script exists to avoid')
    else:
        bad('REALTEK_PHY not found even after following every source line')

    print('\n=== the seven symbols, after the full source graph ===')
    for w in WANTED:
        if w not in acc:
            bad('%-18s not a Kconfig symbol anywhere in the walked graph' % w)
            continue
        path, chain, typ, body = acc[w]
        can_y = typ in (None, 'bool', 'tristate', 'def_bool', 'def_tristate')
        if not can_y:
            bad('%-18s has type %s - `=y` is not expressible, so the gate can never pass'
                % (w, typ))
        else:
            ok('%-18s %-9s %s -> can be =y' % (w, typ or '(inherits)', path))
        deps = re.findall(r'^\s*depends on\s+(.*)$', body, re.M)
        for d in deps[:3]:
            note('%-18s depends on %s' % (w, d.strip()[:70]))
        gated = re.search(r'^\s*if\s+(.*)$', body, re.M)
        if gated:
            note('%-18s sits under `if %s` - its prompt only exists when that is set'
                 % (w, gated.group(1).strip()))
        # `bool "/sys/class/gpio/..." if EXPERT` puts the gate on the TYPE line, not on its
        # own line, so the search above misses it. This is the one that matters: a symbol
        # whose prompt does not exist cannot be enabled by an ordinary config, so it stays
        # n and the gate's `grep -qx "CONFIG_<name>=y"` can never match.
        inline = re.search(r'^\s*(?:bool|tristate)\b.*?\bif\s+([A-Z0-9_&|\s()!]+)$',
                           body, re.M)
        if inline:
            note('%-18s prompt is gated inline on `if %s` - if that is not set, this '
                 'symbol' % (w, inline.group(1).strip()))
            note('%-18s cannot be set to y and the gate would fail' % w)

    print('\n=== the dependency symbols these need, and whether we know them ===')
    for d in DEPS:
        print('  %-16s %s' % (d, 'present in this tree' if d in acc
                                else 'not covered by this fetch - UNKNOWN, not absent'))

    print('\n=== the one `if EXPERT` gate in the seven, checked against the real configs ===')
    # GPIO_SYSFS's prompt is `if EXPERT`. If CONFIG_EXPERT is not set, the prompt does not
    # exist, the symbol stays n, and the PHY gate's `grep -qx "CONFIG_GPIO_SYSFS=y"` can
    # never match - a hard failure at a step that runs unconditionally.
    for cfg in ['immortalwrt/target/linux/zte/zx279133/config-6.18',
                'immortalwrt/target/linux/generic/config-6.18']:
        full = os.path.join('/mnt/c/ugit/SK-D840N4', cfg)
        if not os.path.exists(full):
            bad('%s is missing - the EXPERT question cannot be answered' % cfg)
            continue
        with open(full, encoding='utf-8', errors='replace') as f:
            body = f.read()
        if re.search(r'^CONFIG_EXPERT=y\s*$', body, re.M):
            ok('%s sets CONFIG_EXPERT=y, so GPIO_SYSFS has a prompt to answer'
               % cfg.replace('immortalwrt/', ''))
        elif re.search(r'^# CONFIG_EXPERT is not set', body, re.M):
            bad('%s explicitly does NOT set CONFIG_EXPERT - GPIO_SYSFS can never be =y'
                % cfg)
        else:
            note('%s says nothing about CONFIG_EXPERT' % cfg)

    print('\n=== what this does NOT decide ===')
    note('Whether these are =y in the MERGED config of a real build. There is no merged')
    note('.config on this machine - it only exists after a build - so the gate itself')
    note('cannot be run offline. This script only proves the names are real and that =y')
    note('is expressible for each; it says nothing about what the build will produce.')

    print('\n=== rc=%d, %d checks ===' % (fail, checks))
    return 1 if fail else 0


if __name__ == '__main__':
    sys.exit(main())
