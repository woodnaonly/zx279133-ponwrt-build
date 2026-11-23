#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Is the PPU microcode in this tree the one the tree's own driver will take?

The NPU's PPU runs a microcode blob that the driver hands to the hardware.
The 7.3 driver does not take any blob: it compares the blob's crc32 against
one value and, when the blob is a different one, refuses it outright when
vlan offload is on ("release_firmware(); return -ESTALE").  The microcode is
a *built-in* firmware (CONFIG_EXTRA_FIRMWARE), so the blob that reaches the
board is the one embedded in the kernel image - and the rootfs carries none:
the file the driver asks for is not in the jffs2 image at all.

Two blobs exist with the same name and the same size (103,456 B) and
different bytes: the one read out of this board's own flash, and the one the
7.3 driver was written against.  Which one is in the tree is therefore a
question with a wrong answer available, and nothing in the build says so.

This asks the driver instead of the reader: the CRC values the tree's own
driver compares the blob against are read out of the driver source, and the
blob that is actually in the tree is checked against them.  The header the
driver parses (magic, instruction count) is checked the same way.

Usage:
  python3 tools/check-sk-mcode.py --tree TREE [--blob FILE]
"""
import argparse
import re
import sys
import zlib
from pathlib import Path

DRIVER = 'drivers/net/ethernet/zte/zx279133-np.c'
HEADER = 'drivers/net/ethernet/zte/zx279133.h'
BLOB_IN_TREE = 'firmware/zte/zx279133/mcode_intel.bin'
EXPECT_SIZE = 103456
EXPECT_MAGIC = 0xffffaaaa
EXPECT_INST_NUM = 12288


def crc32_le(data):
    """The value the driver compares against.

    The driver computes crc32_le(~0U, fw->data, fw->size) ^ ~0U.  zlib.crc32
    is the reflected CRC-32 with the standard init and the standard final
    xor, so the driver's value is zlib.crc32(data) itself: the init the
    kernel uses is zlib's, and the two final xors cancel.
    """
    return zlib.crc32(data) & 0xffffffff


def demanded_crcs(tree):
    """The CRC values the tree's driver compares the microcode against.

    Two shapes are read: a literal in the driver (the 7.3 generation) and a
    named constant resolved in the header (the 6.18 generation, which knows
    two blobs: this board's own, and the one the RX hash path wants).
    """
    driver = tree / DRIVER
    if not driver.is_file():
        raise RuntimeError('the tree has no %s' % DRIVER)
    text = driver.read_text(encoding='utf-8', errors='replace')

    values = set()
    for match in re.finditer(r'fw_crc\s*[!=]=\s*(0x[0-9a-fA-F]{8})', text):
        values.add(int(match.group(1), 16))

    header = tree / HEADER
    if header.is_file():
        head = header.read_text(encoding='utf-8', errors='replace')
        constants = dict(re.findall(
            r'#define\s+(ZX279133_MCODE_\w*CRC\w*)\s+(0x[0-9a-fA-F]{8})', head))
        for name in re.findall(r'(ZX279133_MCODE_\w*CRC\w*)', text):
            if name in constants:
                values.add(int(constants[name], 16))
    return values


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--tree', type=Path, required=True,
                        help='the prepared kernel tree')
    parser.add_argument('--blob', type=Path, default=None,
                        help='the microcode to check (default: the one in the tree)')
    args = parser.parse_args(argv)

    failures = []

    def check(ok, what, detail=''):
        print('%s %s%s' % ('[OK ]' if ok else '[FAIL]', what,
                           '' if ok else ': ' + detail))
        if not ok:
            failures.append(what)

    try:
        wanted = demanded_crcs(args.tree)
    except RuntimeError as exc:
        print('FAIL: %s' % exc)
        return 1
    if not wanted:
        print('FAIL: the driver says nothing about which microcode it wants')
        return 1
    print('the driver wants: %s' % ', '.join('0x%08x' % v for v in sorted(wanted)))

    blob = args.blob or (args.tree / BLOB_IN_TREE)
    if not blob.is_file():
        print('FAIL: no microcode to check: %s' % blob)
        return 1
    data = blob.read_bytes()
    digest = crc32_le(data)
    print('the microcode: %s (%d B) crc32 0x%08x' % (blob, len(data), digest))

    check(len(data) == EXPECT_SIZE, 'the microcode is %d bytes' % EXPECT_SIZE,
          'it is %d bytes' % len(data))
    check(digest in wanted,
          'the microcode is one the driver accepts',
          'crc32 0x%08x is not in %s'
          % (digest, ', '.join('0x%08x' % v for v in sorted(wanted))))
    if len(data) >= 16:
        magic = int.from_bytes(data[0:4], 'big')
        inst = int.from_bytes(data[8:12], 'big')
        check(magic == EXPECT_MAGIC, 'the microcode starts with the magic',
              '0x%08x' % magic)
        check(inst == EXPECT_INST_NUM, 'the microcode carries %d instructions'
              % EXPECT_INST_NUM, '%d' % inst)

    if failures:
        print('%d check(s) failed' % len(failures))
        return 1
    print('OK: the microcode in the tree is the one this driver will take')
    return 0


if __name__ == '__main__':
    sys.exit(main())
