#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Controls for the control: does the flash-permit gate still notice?

tests/test_sk_flash_permit.py asks the real gate whether it accepts the bytes
this build produced.  A gate that passes is only worth something if it can also
fail, and the only way to know that is to take the teeth out and watch it turn
red - on the check that was supposed to notice.

So this breaks tools/gatecheck.sh three ways, in a copy, and each time asks the
new gate again:

  * the version comparison is short-circuited (KV is set to the image's own
    version before the comparison) - the gate then accepts a record about any
    kernel, so the control that says "a record about another kernel is refused"
    has to turn red;
  * root_read is dropped from the fields a gate record must carry - so the
    control that says "a record whose root was not read back is refused" has to
    turn red;
  * the uImage checksum is no longer matched - so the control that says "a
    record about other bytes is refused" has to turn red.

The copy is written to a temporary directory and reached through the SK_GATECHECK
environment variable, so the repository's own gate is never modified.
"""
import argparse
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GATE = ROOT / 'tools/gatecheck.sh'
GATE_UNDER_TEST = ROOT / 'tests/test_sk_flash_permit.py'

# (what the mutation does, the check that has to turn red, the text to replace,
#  the text to replace it with)
MUTATIONS = [
    ('the version comparison is short-circuited',
     "a record about another kernel is refused",
     '\tcase "$KV" in',
     '\tKV="$EXP"\n\tcase "$KV" in'),
    ('root_read is no longer required of a gate record',
     "a record whose root was not read back is refused",
     'for k in root_read=IDENTICAL root_index=5 jffs2_ro=OK; do',
     'for k in root_index=5 jffs2_ro=OK; do'),
    ('the uImage checksum is no longer matched',
     'a record about other bytes is refused',
     'grep -F "uimage_md5=$UM"',
     'grep -F "image_md5="'),
]


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--uimage', type=Path, required=True)
    parser.add_argument('--dtb', type=Path, required=True)
    args = parser.parse_args(argv)

    source = GATE.read_text(encoding='utf-8')
    failures = []
    # The paths are resolved because the control is run with the repository as
    # its working directory, and a relative path would then be looked up in the
    # wrong tree.
    uimage = str(args.uimage.resolve())
    dtb = str(args.dtb.resolve())

    def check(name, condition, detail=''):
        if condition:
            print('PASS: %s' % name)
        else:
            print('FAIL: %s: %s' % (name, detail))
            failures.append(name)

    # The unmutated gate has to let the positive case through, otherwise the
    # controls below would prove nothing.
    result = subprocess.run(
        [sys.executable, str(GATE_UNDER_TEST), '--uimage', uimage,
         '--dtb', dtb],
        cwd=str(ROOT), capture_output=True, text=True)
    check('the unmutated gate passes the control',
          result.returncode == 0, result.stdout + result.stderr)

    with tempfile.TemporaryDirectory(prefix='sk-gate-mut-') as tmp:
        broken = Path(tmp) / 'gatecheck.sh'
        for what, expected, needle, replacement in MUTATIONS:
            if needle not in source:
                check('the mutation for "%s" applies' % what, False,
                      'the gate does not contain %r' % needle)
                continue
            broken.write_text(source.replace(needle, replacement, 1), encoding='utf-8')
            result = subprocess.run(
                [sys.executable, str(GATE_UNDER_TEST), '--uimage', uimage,
                 '--dtb', dtb],
                cwd=str(ROOT), capture_output=True, text=True,
                env=dict(os.environ, SK_GATECHECK=str(broken)))
            output = result.stdout + result.stderr
            red = [l for l in output.splitlines() if l.startswith('FAIL: ')]
            names = []
            for line in red:
                hit = re.match(r'FAIL: (.*?):', line)
                names.append(hit.group(1) if hit else line)
            check('%s - the check turns red' % what,
                  result.returncode != 0, output)
            check('%s - it is the expected check that turned red' % what,
                  expected in names, 'red checks: %s' % (names or 'none'))

    if failures:
        print('%d control(s) failed' % len(failures))
        return 1
    print('PASS: the flash-permit gate still notices')
    return 0


if __name__ == '__main__':
    sys.exit(main())
