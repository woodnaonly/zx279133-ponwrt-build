#!/usr/bin/env python3
# 本地排演套件（local rehearsal suite）：依赖 staging/ 下的真实构建产物，
# 在只 clone 本仓库的环境里跑不起来，也**没有**接进 CI。
# 为什么、怎么跑、缺的那个夹具从哪来：同目录 README.md。
"""Fetch the Kconfig files that decide whether the CI PHY gate's symbol names exist at all.

The gate ("Assert the kernel really has the PHY driver") demands seven symbols be `=y` in the
MERGED kernel .config and hard-exits if any is not. Its own comment says a symbol name that
does not exist is dropped in silence - which is exactly why the check is against the merged
.config rather than the edited target config. But that cuts both ways: if one of the seven
names is not a real Kconfig symbol in this tree, the check can never pass and every build
dies at that step, with no artifacts.

There is no local copy of a merged .config to check against, and no kernel tree checked out.
So the question is answered at the source: does each name exist as a Kconfig symbol at all,
and can it be built in (i.e. is it not bool-only-off / tristate-only-as-module)?

Fetched from the same repo and branch the workflow pins, so the answer is about the tree CI
actually builds.
"""
import os
import re
import sys
import urllib.request

REPO = 'cnjn/linux-mainline-zte-zxslc-sr1010'
BR = 'codex/sr1010-mainline'
RAW = 'https://raw.githubusercontent.com/%s/%s/' % (REPO, BR)
DST = '/mnt/c/ugit/SK-D840N4/staging/diag-20261014/kernelsrc'

FILES = [
    'drivers/net/phy/Kconfig',
    'drivers/leds/Kconfig',
    'drivers/gpio/Kconfig',
    'fs/pstore/Kconfig',
    'fs/pstore/Makefile',
]

WANTED = ['REALTEK_PHY', 'LEDS_GPIO', 'GPIO_ZX279133', 'GPIO_SYSFS',
          'PSTORE', 'PSTORE_CONSOLE', 'PSTORE_RAM']


def get(path):
    dest = os.path.join(DST, path.replace('/', '_'))
    last = ''
    for _ in range(4):
        try:
            with urllib.request.urlopen(RAW + path, timeout=30) as r:
                body = r.read()
            with open(dest, 'wb') as f:
                f.write(body)
            return dest, body.decode('utf-8', 'replace')
        except Exception as e:            # noqa: BLE001 - report it, do not hide it
            last = repr(e)
    return None, last


def symbol_blocks(text):
    """name -> (type, first lines of the block). Kconfig entry types are what decide
    whether `=y` is even expressible: `tristate` and `bool` can be y, but a symbol whose
    prompt is unreachable can still be forced; the real question here is only whether the
    name is a symbol at all."""
    out = {}
    lines = text.splitlines()
    for i, ln in enumerate(lines):
        m = re.match(r'^\s*config\s+([A-Z0-9_]+)\s*$', ln)
        if not m:
            continue
        name = m.group(1)
        body = []
        typ = None
        depth = 0
        for j in range(i + 1, min(i + 60, len(lines))):
            body.append(lines[j])
            if typ is None:
                t = re.match(r'^\s*(bool|tristate|hex|string|def_bool|def_tristate)\b', lines[j])
                if t:
                    typ = t.group(1)
            depth += lines[j].count('{') - lines[j].count('}')
            if depth <= 0 and re.match(r'^\s*\}?\s*$', lines[j]) and j > i + 1:
                break
        out[name] = (typ, '\n'.join(body))
    return out


def main():
    print('repo %s@%s\n' % (REPO, BR))
    found = {}
    missing_fetch = []
    for p in FILES:
        dest, body = get(p)
        if dest is None:
            missing_fetch.append((p, body))
            print('FAIL fetch %s: %s' % (p, body))
            continue
        print('ok   %-28s %6d B -> %s' % (p, os.path.getsize(dest), os.path.basename(dest)))
        for name, (typ, blk) in symbol_blocks(body).items():
            if name in WANTED:
                found.setdefault(name, (p, typ, blk))

    print('\n=== the seven symbols the gate demands be =y ===')
    rc = 0
    for w in WANTED:
        if w not in found:
            print('  %-18s NOT FOUND as a Kconfig symbol' % w)
            rc = 1
            continue
        p, typ, blk = found[w]
        # A tristate or bool symbol can be =y. Anything else cannot, and the gate's
        # `grep -qx "CONFIG_$s=y"` would then never match.
        can_y = typ in (None, 'bool', 'tristate', 'def_bool', 'def_tristate')
        print('  %-18s %-9s in %-26s %s' % (
            w, typ or '(inherits)', p, 'can be =y' if can_y else 'CANNOT be =y'))
        if not can_y:
            rc = 1

    print('\n=== dependency lines inside those blocks ===')
    for w in WANTED:
        if w not in found:
            continue
        p, typ, blk = found[w]
        deps = [l.strip() for l in blk.splitlines()
                if 'depends on' in l or 'select' in l or 'default ' in l]
        head = [l.strip() for l in blk.splitlines()[:3]]
        print('  %-18s %s' % (w, ' ; '.join(head)[:110]))
        for d in deps[:4]:
            print('        %s' % d[:110])

    if missing_fetch:
        print('\nFETCH FAILURES: %d file(s) could not be read; conclusions above are only '
              'as complete as the files that arrived.' % len(missing_fetch))
        rc = 1
    return rc


if __name__ == '__main__':
    sys.exit(main())
