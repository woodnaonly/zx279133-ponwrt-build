#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Negative controls for the PPU microcode gate.

tools/check-sk-mcode.py asks the tree's driver which microcode it will take
and checks the blob that is in the tree against it.  A gate that cannot turn
red proves nothing, so this does:

  * the microcode the 7.3 line carries has to be accepted;
  * a microcode with one byte changed has to be refused, and it has to be
    the crc32 check that turns red - not some other one;
  * a microcode that is not the size the driver asks for has to be refused
    on the size check.

The two blobs that exist with the same name and the same size differ in
bytes, so the first case is the one that would be wrong in a build: a tree
that carries the 6.18 line's copy.

Usage:
  python3 tests/test_sk_mcode.py --tree TREE
"""
import argparse
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CHECK = ROOT / 'tools/check-sk-mcode.py'
BLOB_73 = ROOT / 'overlays-73/target/linux/zte/zx279133/firmware/zte/zx279133/mcode_intel.bin'
BLOB_618 = ROOT / 'overlays/target/linux/zte/zx279133/firmware/zte/zx279133/mcode_intel.bin'


def run_checker(tree, blob):
    """Run the gate against one blob; return (rc, output)."""
    result = subprocess.run(
        [sys.executable, str(CHECK), '--tree', str(tree), '--blob', str(blob)],
        capture_output=True, text=True, encoding='utf-8', errors='replace')
    return result.returncode, result.stdout + result.stderr


def red_checks(output):
    """The names of the checks that turned red."""
    names = []
    for line in output.splitlines():
        if line.startswith('[FAIL]'):
            name = line[len('[FAIL]'):].split(':', 1)[0].strip()
            names.append(name)
    return names


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--tree', type=Path, required=True,
                        help='the prepared kernel tree')
    args = parser.parse_args(argv)

    failures = []

    def check(ok, what, detail=''):
        print('PASS: %s' % what if ok else 'FAIL: %s: %s' % (what, detail))
        if not ok:
            failures.append(what)

    if not BLOB_73.is_file():
        print('FAIL: the 7.3 line carries no microcode: %s' % BLOB_73)
        return 1

    rc, output = run_checker(args.tree, BLOB_73)
    check(rc == 0, 'the microcode the 7.3 line carries is accepted', output)

    with tempfile.TemporaryDirectory(prefix='sk-mcode-') as tmp:
        work = Path(tmp)

        # One byte changed: the size and the header still look right, only
        # the crc32 does not.
        broken = work / 'one-byte-changed.bin'
        data = bytearray(BLOB_73.read_bytes())
        data[len(data) // 2] ^= 0x01
        broken.write_bytes(bytes(data))
        rc, output = run_checker(args.tree, broken)
        check(rc != 0, 'a microcode with one byte changed is refused', output)
        check('the microcode is one the driver accepts' in red_checks(output),
              'it is the crc32 check that turns red',
              'red checks: %s' % (red_checks(output) or 'none'))

        # Truncated: the size is wrong before the crc32 is even looked at.
        short = work / 'too-short.bin'
        short.write_bytes(bytes(data[:1000]))
        rc, output = run_checker(args.tree, short)
        check(rc != 0, 'a microcode of the wrong size is refused', output)
        check(any('bytes' in name for name in red_checks(output)),
              'it is the size check that turns red',
              'red checks: %s' % (red_checks(output) or 'none'))

        # The 6.18 line's copy of the same name: the same size, the same
        # header, and a crc32 this driver does not accept.
        if BLOB_618.is_file():
            rc, output = run_checker(args.tree, BLOB_618)
            check(rc != 0, "the 6.18 line's microcode is refused", output)
            check('the microcode is one the driver accepts' in red_checks(output),
                  "it is the crc32 check that turns red for it",
                  'red checks: %s' % (red_checks(output) or 'none'))

    if failures:
        print('%d control(s) failed' % len(failures))
        return 1
    print('PASS: the microcode gate still notices')
    return 0


if __name__ == '__main__':
    sys.exit(main())
