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
    on the size check;
  * the SR1010's microcode has to be refused even though the driver knows
    its crc32 - the check that does not ask the driver has to be the one
    that turns red.

The last one is the point of the file.  The driver takes this board's own
blob and the SR1010's - the second is the blob the RX-hash hook was
calibrated against - so a gate that asks the driver cannot tell them apart.
The two carry the same name and the same size and differ in bytes, and the
SR1010's is the one that leaves the board with no frames at all (docs/75):
the frames stall inside the PPU and no netdev ever counts a byte.

Usage:
  python3 tests/test_sk_mcode.py --tree TREE
"""
import argparse
import importlib.util
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CHECK = ROOT / 'tools/check-sk-mcode.py'
BLOB_73 = ROOT / 'overlays-73/target/linux/zte/zx279133/firmware/zte/zx279133/mcode_intel.bin'
BLOB_618 = ROOT / 'overlays/target/linux/zte/zx279133/firmware/zte/zx279133/mcode_intel.bin'
BLOB_SR1010 = ROOT / 'tests/fixtures/mcode_intel.sr1010.bin'

ACCEPTS = 'the microcode is one the driver accepts'
OWNS = "the microcode is this board's own"


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


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

    gate = load(CHECK, 'sk_mcode_tree_gate')

    if not BLOB_73.is_file():
        print('FAIL: the 7.3 line carries no microcode: %s' % BLOB_73)
        return 1
    if not BLOB_SR1010.is_file():
        print('FAIL: the SR1010 fixture is missing: %s' % BLOB_SR1010)
        return 1

    rc, output = run_checker(args.tree, BLOB_73)
    check(rc == 0, 'the microcode the 7.3 line carries is accepted', output)

    # The two blobs have to be really different, or the control below would
    # be about one file with two names.
    own_crc = gate.crc32_le(BLOB_73.read_bytes())
    other_crc = gate.crc32_le(BLOB_SR1010.read_bytes())
    check(own_crc != other_crc,
          'the SR1010 fixture is a different blob from the one the 7.3 line carries',
          'both have crc32 0x%08x' % own_crc)
    check(other_crc != gate.BOARD_CRC,
          'the SR1010 fixture is not this board\'s own blob',
          'the fixture has crc32 0x%08x, this board\'s is 0x%08x'
          % (other_crc, gate.BOARD_CRC))

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
        check(ACCEPTS in red_checks(output),
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

        # The other board's blob.  The driver knows its crc32 - it is the
        # one the RX-hash hook was calibrated against - so the check that
        # turns red has to be the one that does not ask the driver.
        rc, output = run_checker(args.tree, BLOB_SR1010)
        check(rc != 0, "the SR1010's microcode is refused", output)
        check(ACCEPTS not in red_checks(output),
              'the driver takes that crc32, so the driver check stays green',
              'red checks: %s' % (red_checks(output) or 'none'))
        check(OWNS in red_checks(output),
              "it is this board's own check that turns red",
              'red checks: %s' % (red_checks(output) or 'none'))

        # The 6.18 line's copy is the same file as the 7.3 line's now: both
        # carry this board's own blob.  It has to be accepted.
        if BLOB_618.is_file():
            rc, output = run_checker(args.tree, BLOB_618)
            check(rc == 0,
                  "the 6.18 line's microcode is the same one and is accepted",
                  output)

    if failures:
        print('%d control(s) failed' % len(failures))
        return 1
    print('PASS: the microcode gate still notices')
    return 0


if __name__ == '__main__':
    sys.exit(main())
