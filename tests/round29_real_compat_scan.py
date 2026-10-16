#!/usr/bin/env python3
# 本地排演套件（local rehearsal suite）：依赖 staging/ 下的真实构建产物，
# 在只 clone 本仓库的环境里跑不起来，也**没有**接进 CI。
# 为什么、怎么跑、缺的那个夹具从哪来：同目录 README.md。
"""Run the driver gate's COMPAT SCAN (the dtc -> awk -> compat.txt half) against the real
compiled SK-D840N dtb, not the 6-row rehearsal fixture.

Why this exists: run_gate_fallback_verbatim.py extracts the *verdict* block and feeds it a
fixture compat list. The scan that builds that list is only exercised in CI, so "how many
real nodes take the non-syscon fallback branch" - the exact input to the question "should
that branch be fatal?" - has never been answered offline against the shipped dtb.

The awk is taken verbatim out of the workflow's own `run:` block by the same YAML walk the
existing extractor uses, so it cannot drift from the gate.

Caveat stated up front: the DTS text is produced by staging/diag-20261014/fdt2dts.py, which
is a STAND-IN for dtc. It was shown to render the same list form dtc emits, but it is not
dtc. That caveat bounds this to node/fallback classification, not to byte fidelity.
"""
import os
import re
import subprocess
import sys
import tempfile

import yaml

WF = '/mnt/c/ugit/SK-D840N4/ci-build-repo/.github/workflows/build-sk-d840n.yml'
DTB = '/mnt/c/ugit/SK-D840N4/staging/ctl/gate-dtb'
FDT2DTS = '/mnt/c/ugit/SK-D840N4/staging/diag-20261014/fdt2dts.py'
IMG = '/mnt/c/ugit/SK-D840N4/staging/ctl/gate-Image'

# The scan step starts at this line of `run:` and ends at the compat.txt redirect.
ANCHOR = 'dtc -I dtb -O dts -o "$WORK/dts" "$DTB"'
END = '| LC_ALL=C sort -u >"$WORK/compat.txt"'


def extract():
    doc = yaml.safe_load(open(WF, encoding='utf-8'))
    for job in doc.get('jobs', {}).values():
        for step in job.get('steps') or []:
            run = step.get('run') or ''
            if ANCHOR not in run or END not in run:
                continue
            i = run.index(ANCHOR) + len(ANCHOR)
            j = run.index(END) + len(END)
            raw = run[i:j]
            # The span between the dtc line and the redirect starts with a comment
            # ("Pipe-separated, never tab: ..."), so slice from the awk line itself
            # rather than asserting the block begins with awk.
            m = re.search(r"^\s*awk\s+'", raw, re.M)
            if not m:
                raise SystemExit('no awk program in the extracted span: %r' % raw[:200])
            return raw[m.start():]
    raise SystemExit('could not locate the compat scan in the workflow - it changed shape')


def main():
    block = extract()
    if 'gsub(/\\\\0/' not in block:
        raise SystemExit('the extracted scan has lost the backslash-zero normalisation')

    work = tempfile.mkdtemp(prefix='realscan.')
    dts = os.path.join(work, 'dts')
    compat = os.path.join(work, 'compat.txt')
    strings = os.path.join(work, 'strings.txt')

    subprocess.run([sys.executable, FDT2DTS, DTB], stdout=open(dts, 'w'), check=True)
    print('decompiled %s -> %d lines' % (os.path.basename(DTB),
                                         sum(1 for _ in open(dts, encoding='utf-8'))))

    # The workflow's own dts has tabs stripped and is indented; awk patterns use
    # [[:space:]] so indentation is irrelevant. Run it as the workflow runs it.
    script = os.path.join(work, 'scan.sh')
    with open(script, 'w') as f:
        f.write('set -u\nWORK=%s\n' % work)
        f.write(block)
        f.write('\n')
    r = subprocess.run(['sh', script], cwd=work, capture_output=True, text=True)
    if r.stderr.strip():
        print('scan stderr: %s' % r.stderr.strip()[:400])
    if not os.path.exists(compat):
        raise SystemExit('the scan produced no compat.txt')

    rows = [l.rstrip('\n') for l in open(compat, encoding='utf-8')]
    rows = [l for l in rows if l.strip()]
    print('\n=== the gate\'s own scan, on the real dtb: %d rows ===' % len(rows))
    for i, row in enumerate(rows, 1):
        print('  %2d  %s' % (i, row))

    # The workflow's own sanity guard: zero rows means the scan is broken, not clean.
    if not rows:
        print('\nFAIL: zero rows - this is the workflow\'s own "::error::broken" condition')
        return 1

    # Real kernel strings, built the way the step builds them.
    raw = os.path.join(work, 'img.raw')
    with open(IMG, 'rb') as f:
        head = f.read(2)
    if head == b'\x1f\x8b':
        subprocess.run('gzip -dc %s > %s' % (IMG, raw), shell=True, check=True)
    else:
        with open(raw, 'wb') as o, open(IMG, 'rb') as i:
            o.write(i.read())
    with open(strings, 'w', encoding='utf-8', errors='replace') as f:
        subprocess.run("tr -c '[:print:]' '\\n' < %s | grep -aE '.{3,}' | LC_ALL=C sort -u"
                       % raw, shell=True, stdout=f)

    tally = {'syscon': [], 'other-fallback': [], 'disabled': [], 'primary-only': []}
    print('\n=== per-node classification ===')
    for row in rows:
        parts = row.split('|')
        if len(parts) != 4:
            print('  MALFORMED ROW: %r' % row)
            continue
        name, node, fb, st = parts
        if st == 'disabled':
            tally['disabled'].append(row)
            verdict = 'skipped (disabled)'
        elif fb not in ('', '-'):
            if fb == 'syscon':
                tally['syscon'].append(row)
                verdict = "ok (syscon fallback)"
            else:
                tally['other-fallback'].append(row)
                verdict = "NOTE (non-syscon fallback, no fatal)"
        else:
            tally['primary-only'].append(row)
            present = subprocess.run(['grep', '-Fxq', '-e', name, strings]).returncode == 0
            verdict = 'ok (driver present)' if present else 'FATAL (no driver)'
        print('  %-26s %-34s %s' % (name, node, verdict))

    print('\n=== tally ===')
    print('  syscon fallback (ok)     : %d' % len(tally['syscon']))
    print('  non-syscon fallback (note): %d' % len(tally['other-fallback']))
    print('  disabled (skipped)        : %d' % len(tally['disabled']))
    print('  primary only              : %d' % len(tally['primary-only']))
    print('  TOTAL                     : %d' % len(rows))
    if tally['other-fallback']:
        print('\n=== the rows that "make it fatal" would newly reject ===')
        for row in tally['other-fallback']:
            print('  %s' % row)
    else:
        print('\n=== no row takes the non-syscon fallback branch at all ===')
        print('  so changing that branch to fatal changes NOTHING for this board today.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
