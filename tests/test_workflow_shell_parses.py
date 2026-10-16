#!/usr/bin/env python3
# 本地排演套件（local rehearsal suite）：依赖 staging/ 下的真实构建产物，
# 在只 clone 本仓库的环境里跑不起来，也**没有**接进 CI。
# 为什么、怎么跑、缺的那个夹具从哪来：同目录 README.md。
"""Every `run:` block in every workflow must parse as shell. 30 checks with controls.

THE BUG THIS EXISTS FOR
-----------------------
The driver gate wraps a long awk program in shell single quotes. A comment inside that
program read "the gate's own offline rehearsal". The apostrophe closed the quote 53 lines
early, so the step could not run at all - and `bash` blamed an innocent `gsub()` 24 lines
further down. The gate that round 24 spent its effort repairing had never executed once.

Nothing caught it because:
  * check_workflow_yaml.py only proves the YAML parses, not the shell;
  * test_gate_endtoend.sh extracts only the *verdict* half of the gate, which is a
    separate run: block that happens to be well formed;
  * the YAML parser had no reason to look inside a block scalar.

CALIBRATION
-----------
A detector that has never fired is not evidence. Each of the two behaviours below is
checked against a case that must trip it, before it is trusted on the real files.
"""
import os
import re
import subprocess
import sys
import tempfile

import yaml

WF_DIR = '/mnt/c/ugit/SK-D840N4/ci-build-repo/.github/workflows'

P = 0
F = 0


def ok(msg):
    global P
    P += 1
    print('  PASS %s' % msg)


def bad(msg, detail=''):
    global F
    F += 1
    print('  FAIL %s' % msg)
    for line in str(detail).splitlines()[:12]:
        print('       %s' % line)


def shparse(text, shell='bash'):
    with tempfile.NamedTemporaryFile('w', suffix='.sh', delete=False,
                                     encoding='utf-8') as f:
        f.write(text)
        p = f.name
    try:
        r = subprocess.run([shell, '-n', p], capture_output=True, text=True)
        return r.returncode, (r.stderr or '').strip()
    finally:
        os.unlink(p)


def blocks():
    for name in sorted(os.listdir(WF_DIR)):
        if not name.endswith(('.yml', '.yaml')):
            continue
        doc = yaml.safe_load(open(os.path.join(WF_DIR, name), encoding='utf-8'))
        for jname, job in (doc.get('jobs') or {}).items():
            for i, step in enumerate(job.get('steps') or []):
                run = step.get('run')
                if run:
                    yield name, jname, i, (step.get('name') or step.get('uses')
                                           or '(unnamed)'), run


print('=== 1. the detector can tell broken shell from clean shell ===')
rc, _ = shparse('set -eu\necho ok\nfor i in 1 2; do echo "$i"; done\n')
if rc == 0:
    ok('a clean script parses')
else:
    bad('a clean script should parse', rc)
rc, err = shparse("set -eu\necho 'unterminated\n")
if rc != 0:
    ok('and an unterminated quote is reported (rc=%d)' % rc)
else:
    bad('the detector cannot see an unterminated quote - it is useless')
# the exact shape of the real bug: a comment apostrophe inside a quoted program
tricky = """set -eu
awk '
  function f() { return 1 }
  # the gate's own rehearsal saw nothing
  BEGIN { print f() }
' /dev/null
"""
rc, _ = shparse(tricky)
if rc != 0:
    ok('and an apostrophe inside a quoted program is reported - the exact real failure')
else:
    bad('the detector cannot see the failure that actually happened', rc)

print('\n=== 2. every run: block in every workflow parses as shell ===')
allb = list(blocks())
nwf = len({b[0] for b in allb})
print('     %d run: blocks across %d workflows' % (len(allb), nwf))
if len(allb) >= 30:
    ok('the sweep found the workflows at all (%d blocks)' % len(allb))
else:
    bad('only %d run: blocks found - the sweep is not reading the files' % len(allb))

broken = []
for name, jname, i, label, run in allb:
    rc, err = shparse(run)
    if rc != 0:
        broken.append((name, jname, i, label, err, run))

if broken:
    for name, jname, i, label, err, run in broken:
        bad('%s :: %s :: step %d (%s) does not parse' % (name, jname, i, label),
            err.splitlines()[0] if err else '(no message)')
        m = re.search(r'line (\d+)', err)
        if m:
            lines = run.split('\n')
            ln = int(m.group(1))
            # Find the earliest apostrophe at or before the line bash complained about:
            # that is where the quote really closed, not where bash noticed.
            for k in range(min(ln, len(lines))):
                if "'" in lines[k] and k > 0 and lines[k].lstrip().startswith('#'):
                    bad('  the apostrophe is on the step\'s line %d: %s' % (k + 1, lines[k].strip()))
                    break
else:
    ok('all %d run: blocks parse as shell' % len(allb))

print('\n=== 3. no apostrophe inside any single-quoted awk program ===')
found = 0
for name, jname, i, label, run in allb:
    lines = run.split('\n')
    inside = False
    for n, line in enumerate(lines, 1):
        opens = re.match(r"\s*awk\s+'", line)
        closes = re.match(r"\s*'\s+\S", line)
        if opens:
            inside = True
            continue
        if inside:
            body = line
            # the closing delimiter itself is expected
            if re.match(r"^\s*'\s", line):
                inside = False
                continue
            if "'" in body and body.lstrip().startswith('#'):
                found += 1
                bad('%s :: step %d line %d: apostrophe inside a quoted program'
                    % (name, i, n), body.strip())
if not found:
    ok('no comment inside a quoted awk program contains an apostrophe')

print('\n=== 4. the driver gate step specifically ===')
gate = [(n, j, i, l, r) for n, j, i, l, r in allb
        if 'dtb compatible has a driver' in l]
if len(gate) == 1:
    ok('the gate step is found, exactly one')
    n, j, i, l, r = gate[0]
    rc, err = shparse(r)
    if rc == 0:
        ok('and it parses')
    else:
        bad('the driver gate step does not parse', err)
    # and its awk program must be extractable, which is what proved the truncation
    if re.search(r"awk\s+'", r) and "compat.txt" in r:
        ok('and it still contains the scan')
    else:
        bad('the gate step no longer has the compat scan - shape changed')
else:
    bad('expected exactly one driver-gate step, found %d' % len(gate))

print('\n=== 5. the scan program, run for real on the real dtb ===')
# This is the check the whole thing was for: the awk must not merely parse, it must
# produce the node list. Fed the shipped dtb through the repo's own decompiler.
sys.path.insert(0, '/mnt/c/ugit/SK-D840N4/staging/diag-20261014')
import importlib
mod = importlib.import_module('round29_real_compat_scan')
try:
    block = mod.extract()
    if block.strip().endswith('compat.txt"'):
        ok('the extracted scan ends at the redirect, not mid-program')
    else:
        bad('the extracted scan looks truncated', block[-120:])
except SystemExit as e:
    bad('could not extract the scan', e)

print('\n=== %d passed, %d failed ===' % (P, F))
sys.exit(1 if F else 0)
