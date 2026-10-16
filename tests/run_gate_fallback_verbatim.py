#!/usr/bin/env python3
# 本地排演套件（local rehearsal suite）：依赖 staging/ 下的真实构建产物，
# 在只 clone 本仓库的环境里跑不起来，也**没有**接进 CI。
# 为什么、怎么跑、缺的那个夹具从哪来：同目录 README.md。
"""Run the driver gate's fallback-verdict block, extracted verbatim from the workflow.

Why not run the whole step: it needs `dtc`, which is not installed here, and installing
a device-tree compiler is not something to do unasked. The `dtc` call only produces the
DTS text that the step's awk turns into compat.txt; the awk itself is untouched. So this
extracts the block that WAS changed - `has_driver()` through the end of the fallback
`case` - and runs it against the real strings.txt built by the step's own pipeline, plus
a fixture compat list containing the three syscon nodes and two deliberate wrong cases.

If the block is edited later and the extraction stops finding it, that is a FAIL, not a
silent skip: an unchanged block would otherwise pass forever.
"""
import os
import subprocess
import sys
import tempfile

import yaml

WF = '/mnt/c/ugit/SK-D840N4/ci-build-repo/.github/workflows/build-sk-d840n.yml'
IMG = '/mnt/c/ugit/SK-D840N4/staging/ctl/gate-Image'
START = 'has_driver() { LC_ALL=C grep -Fxq -e "$1" "$WORK/strings.txt"; }'
LOOP = 'while IFS=\'|\' read -r compat node fb status; do'
DONE = 'done <"$WORK/compat.txt"'
# The gate only gates if `rc` reaches the exit status. This extractor used to stop at
# DONE, which made every verdict look like exit 0 - so a fatal branch could print
# ::error:: and the test would still call it a pass. The tail is part of the block.
TAIL = 'exit "$rc"'


def extract():
    doc = yaml.safe_load(open(WF, encoding='utf-8'))
    for job in doc.get('jobs', {}).values():
        for step in job.get('steps') or []:
            run = step.get('run') or ''
            if START not in run:
                continue
            if TAIL not in run:
                # Without this the gate prints ::error:: and the step still succeeds, which
                # is the same class of failure as the apostrophe one: looks fatal, is not.
                raise SystemExit('the driver gate never propagates rc - '
                                 'it prints ::error:: and exits 0. Add `exit "$rc"`.')
            i = run.index(START)
            j = run.index(LOOP)
            k = run.index(TAIL) + len(TAIL)
            if not i < j < k:
                raise SystemExit('markers out of order: has_driver=%d loop=%d exit=%d' % (i, j, k))
            return run[i:j], run[j:k]
    raise SystemExit('could not locate the fallback-verdict block - the workflow changed shape')


def main():
    head, body = extract()
    work = tempfile.mkdtemp(prefix='gatefrag.')
    strings = os.path.join(work, 'strings.txt')

    # The step's own pipeline, unchanged.
    raw = os.path.join(work, 'img.raw')
    with open(IMG, 'rb') as f:
        magic = f.read(2)
    if magic == b'\x1f\x8b':
        with open(raw, 'wb') as out:
            subprocess.run(['gzip', '-dc', IMG], stdout=out, check=True)
    else:
        with open(raw, 'wb') as out, open(IMG, 'rb') as f:
            out.write(f.read())
    env = dict(os.environ, LC_ALL='C')
    with open(raw, 'rb') as f:
        p1 = subprocess.Popen(['tr', '-c', '[:print:]', '\n'], stdin=f, stdout=subprocess.PIPE, env=env)
        p2 = subprocess.Popen(['grep', '-aE', '.{3,}'], stdin=p1.stdout, stdout=subprocess.PIPE, env=env)
        p1.stdout.close()
        p3 = subprocess.Popen(['sort', '-u'], stdin=p2.stdout, stdout=open(strings, 'wb'), env=env)
        p2.stdout.close()
        p3.wait()
        p2.wait()
        p1.wait()
    n = sum(1 for _ in open(strings, 'rb'))
    print('strings.txt: %d lines (real, from %s)' % (n, os.path.basename(IMG)))
    if n < 1000:
        raise SystemExit('strings pipeline produced almost nothing - aborting')

    # Two fixtures, because the branch changed from note to fatal on 2026-10-15 and the
    # old single fixture could no longer distinguish "the gate says ok" from "the gate
    # stopped". Clean = the real rows, all of which must pass. Dirty = clean plus one
    # fallback with no driver, which must now be refused.
    real_rows = [
        'zte,zx279133-pon-route|syscon@17000080|syscon|okay\n',
        'zte,zx279133-idm-cci|syscon@10e00078|syscon|okay\n',
        'zte,zx279133-wdt-reset|syscon@10e10208|syscon|okay\n',
        'zte,zx279133-nppt|ethernet@19000000|-|okay\n',          # no fallback: needs a driver
        'zte,zx279133-lan-conduit|lanconduit@2|-|disabled\n',   # disabled: skipped
    ]
    dirty_rows = real_rows + ['zte,zx279133-fake-fb|node@1|nosuchdriver|okay\n']

    compat = os.path.join(work, 'compat.txt')
    script = os.path.join(work, 'frag.sh')
    with open(script, 'w') as f:
        f.write('set -u\nWORK=%s\n' % work)
        f.write(head)
        f.write(body)
    print('--- extracted script (%d bytes) ---' % os.path.getsize(script))
    sys.stdout.write(open(script).read())

    env = dict(os.environ, LC_ALL='C')
    rc = 0

    def run(rows, label):
        with open(compat, 'w') as f:
            f.writelines(rows)
        r = subprocess.run(['sh', script], cwd=work, env=env, capture_output=True, text=True)
        print('--- %s --- block exit %d' % (label, r.returncode))
        for l in r.stdout.splitlines():
            if l.startswith(('ok', 'note', '::error')):
                print('   ', l.strip()[:110])
        return r

    print('=== A. the real rows must all pass, and the gate must exit 0 ===')
    r = run(real_rows, 'A: clean fixture')
    if r.returncode != 0:
        print('    FAIL: the real rows were refused')
        rc = 1
    else:
        print('    ok: exit 0')
    if any(l.startswith('::error') for l in r.stdout.splitlines()):
        print('    FAIL: an ::error on rows that are known good')
        rc = 1
    else:
        print('    ok: no ::error')

    print('\n=== B. a fallback with no driver must now be REFUSED, not noted ===')
    r = run(dirty_rows, 'B: + one driver-less fallback')
    errs = [l for l in r.stdout.splitlines() if l.startswith('::error')]
    if r.returncode != 0 and errs:
        print('    ok: exit %d and it named the node' % r.returncode)
    else:
        print('    FAIL: expected a nonzero exit and an ::error, got exit %d, %d ::error line(s)'
              % (r.returncode, len(errs)))
        rc = 1
    if any('fake-fb' in l for l in errs):
        print('    ok: the message names zte,zx279133-fake-fb')
    else:
        print('    FAIL: no ::error mentions the offending compatible')
        rc = 1
    if any(l.startswith('note zte,zx279133-fake-fb') for l in r.stdout.splitlines()):
        print('    FAIL: it still downgraded that case to a note')
        rc = 1
    else:
        print('    ok: and it is no longer a note')

    # Negative controls for the syscon branch, whose behaviour did NOT change.
    # A verdict that can only ever say "ok" is decoration.
    full = open(strings, encoding='utf-8', errors='replace').read()
    cases = [
        ('C: no "regmap init failed"', full.replace('regmap init failed', 'GONE')),
        ('D: no syscon core at all', full.replace('regmap init failed', 'GONE')
                                       .replace('__regmap_init_mmio_clk', 'GONE')),
    ]
    with open(compat, 'w') as f:
        f.writelines(real_rows)
    for label, content in cases:
        with open(strings, 'w', encoding='utf-8') as f:
            f.write(content)
        out = subprocess.run(['sh', script], cwd=work, env=env,
                             capture_output=True, text=True)
        lines = [l for l in out.stdout.splitlines() if 'syscon core' in l or 'does not look' in l]
        print('--- negative control %s ---' % label)
        for l in lines[:3]:
            print('   ', l.strip())
        if not any(l.startswith('note') for l in lines):
            print('    FAIL: the syscon branch still said ok with the evidence removed')
            rc = 1
        else:
            print('    ok: it said note')
    print('\ngate fragment rc=%d' % rc)
    return rc


if __name__ == '__main__':
    sys.exit(main())
