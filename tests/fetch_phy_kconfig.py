#!/usr/bin/env python3
# 本地排演套件（local rehearsal suite）：依赖 staging/ 下的真实构建产物，
# 在只 clone 本仓库的环境里跑不起来，也**没有**接进 CI。
# 为什么、怎么跑、缺的那个夹具从哪来：同目录 README.md。
"""Follow the `source` lines out of drivers/net/phy/Kconfig before concluding anything.

The first Kconfig fetch reported REALTEK_PHY "NOT FOUND" in drivers/net/phy/Kconfig. That
is exactly the non-recursive-tree mistake this project has already made once: a symbol in
a subdirectory Kconfig that the parent merely `source`s looks absent from the parent.
Realtek PHY drivers were split into drivers/net/phy/realtek/ a few kernels ago.

So: resolve the `source` graph properly, then report.
"""
import os
import re
import sys
import urllib.request

REPO = 'cnjn/linux-mainline-zte-zxslc-sr1010'
BR = 'codex/sr1010-mainline'
RAW = 'https://raw.githubusercontent.com/%s/%s/' % (REPO, BR)
DST = '/mnt/c/ugit/SK-D840N4/staging/diag-20261014/kernelsrc'
WANT = 'REALTEK_PHY'

cache = {}


def fetch(path, depth=0, seen=None):
    """Return (text, chain). Follows `source "..."` one level of nesting deep."""
    if seen is None:
        seen = set()
    if path in seen or depth > 3:
        return None, [path]
    seen.add(path)
    dest = os.path.join(DST, path.replace('/', '_'))
    if os.path.exists(dest):
        body = open(dest, 'rb').read().decode('utf-8', 'replace')
    else:
        try:
            with urllib.request.urlopen(RAW + path, timeout=30) as r:
                body = r.read().decode('utf-8', 'replace')
            with open(dest, 'w', encoding='utf-8') as f:
                f.write(body)
        except Exception as e:                        # noqa: BLE001
            return None, ['%s (%s)' % (path, e)]
    for src in re.findall(r'^\s*(?:menu)?source\s+"([^"]+)"', body, re.M):
        sub, chain = fetch(src, depth + 1, seen)
        if sub and re.search(r'^\s*config\s+%s\s*$' % WANT, sub, re.M):
            return sub, [path] + chain
    if re.search(r'^\s*config\s+%s\s*$' % WANT, body, re.M):
        return body, [path]
    return None, [path]


def show(body, path):
    lines = body.splitlines()
    for i, ln in enumerate(lines):
        if re.match(r'^\s*config\s+%s\s*$' % WANT, ln):
            print('  FOUND `config %s` in %s, line %d' % (WANT, path, i + 1))
            print('\n'.join('    %s' % l for l in lines[i:i + 12]))
            return True
    return False


def main():
    entry = 'drivers/net/phy/Kconfig'
    body = open(os.path.join(DST, 'drivers_net_phy_Kconfig'),
                encoding='utf-8').read()
    srcs = re.findall(r'^\s*(?:menu)?source\s+"([^"]+)"', body, re.M)
    print('drivers/net/phy/Kconfig sources %d file(s):' % len(srcs))
    for s in srcs:
        print('    %s' % s)
    rk = [s for s in srcs if 'realtek' in s]
    print('\nrealtek-related source: %s' % (rk or 'NONE'))

    body2, chain = fetch(entry)
    if body2:
        print('resolved through: %s' % ' -> '.join(chain))
        show(body2, chain[-1])
        print('\nCONCLUSION: REALTEK_PHY EXISTS. The first fetch did not show it because it')
        print('only looked at the parent Kconfig, which merely `source`s the subdirectory.')
        return 0
    print('CONCLUSION: REALTEK_PHY not found even after following source lines.')
    return 1


if __name__ == '__main__':
    sys.exit(main())
