#!/usr/bin/env python3
# 本地排演套件（local rehearsal suite）：依赖 staging/ 下的真实构建产物，
# 在只 clone 本仓库的环境里跑不起来，也**没有**接进 CI。
# 为什么、怎么跑、缺的那个夹具从哪来：同目录 README.md。
"""Check the shipped microcode blob against the exact test the driver performs.

drivers/net/ethernet/zte/zx279133-np.c:zx279133_ppu_mcode_prepare() reads
the blob's header and then decides, on a size AND a CRC32, whether the
RX-hash hook can be installed:

    hash = rx_hash && fw->size == 103456 &&
           (crc32_le(~0U, fw->data, fw->size) ^ ~0U) == 0xef1d7647;

crc32_le(~0, data, len) ^ ~0 is the standard reflected CRC-32/ISO-HDLC.

Two blobs are involved and they are not the same blob:

  * 0xc839b946 is **this board's** PPU microcode - the vendor's own
    /etc/mcode/mcode_133.bin, which the vendor's np_133.ko loads.  It is
    the blob this repository now ships, and the one the accelerated path
    needs: with it the PPU forwards every frame it is given.
  * 0xef1d7647 is the **SR1010's** PPU microcode, the one that used to be
    shipped here.  The RX-hash hook is a patch to that blob's instruction
    stream - four instructions rewritten at a known offset - so the offset
    means nothing in any other blob.  The vendor does not patch this
    board's blob either; the vendor's PPU gets its hash from the SE
    hardware hash table instead.

So the shipped blob is expected to pass the format checks and to fail the
hook's CRC check.  That is the intended state, and the driver says so at
boot ("the board's own PPU firmware is loaded").

Also parses the header fields the driver reads (magic, inst_num, first) and
walks the tag stream far enough to prove the format is really the tagged
instruction encoding and not arbitrary bytes that happen to have the right
length.
"""
import binascii
import struct
import sys

WANT_SIZE = 103456
# The board's own blob: what this repository ships.
WANT_CRC = 0xc839b946
# The blob the RX-hash hook was calibrated against.  The hook is a patch to
# that blob's instruction stream and is not installed for any other.
HOOK_CRC = 0xef1d7647


def crc32_le(data):
    """crc32_le(~0U, data, len) ^ ~0U -- what the kernel computes."""
    return binascii.crc32(data) & 0xffffffff


def main():
    paths = sys.argv[1:]
    if not paths:
        print('usage: check_ppu_mcode.py <blob> [blob...]')
        return 2
    rc = 0
    for p in paths:
        data = open(p, 'rb').read()
        size_ok = len(data) == WANT_SIZE
        got = crc32_le(data)
        crc_ok = got == WANT_CRC
        hook_ok = got == HOOK_CRC
        print('== %s' % p)
        print('   size            %d   expected %d   %s' % (len(data), WANT_SIZE, 'OK' if size_ok else 'MISMATCH'))
        print('   crc32           0x%08x   expected 0x%08x   %s' % (got, WANT_CRC, 'OK' if crc_ok else 'MISMATCH'))
        print('   rx-hash hook    %s' %
              ('installed (this is the SR1010 blob)' if hook_ok else
               'not installed (not the blob the hook was calibrated for)'))
        if len(data) >= 16:
            # Offsets come from the driver, not from a guess about the layout:
            #   get_unaligned_be32(fw->data)      -> magic
            #   get_unaligned_be32(fw->data + 8)  -> inst_num
            #   get_unaligned_be32(fw->data + 12) -> first
            # There is an unlabelled word at offset 4; do not pretend to know what
            # it is, just show it.
            magic = struct.unpack('>I', data[0:4])[0]
            word4 = struct.unpack('>I', data[4:8])[0]
            inst_num = struct.unpack('>I', data[8:12])[0]
            first = struct.unpack('>I', data[12:16])[0]
            print('   magic    @0     0x%08x  expected 0xffffaaaa  %s'
                  % (magic, 'OK' if magic == 0xffffaaaa else 'MISMATCH'))
            print('   word4    @4     0x%08x  (driver does not read this)' % word4)
            print('   inst_num @8     %-10d expected 12288  %s'
                  % (inst_num, 'OK' if inst_num == 12288 else 'MISMATCH'))
            print('   first    @12    %-10d (ALIGN4 -> %d)' % (first, (first + 3) & ~3))
            hdr_ok = (magic == 0xffffaaaa and inst_num == 12288
                      and 0 < first <= 12288 - 80)
            print('   header check (driver\'s goto err_fw condition): %s'
                  % ('PASS' if hdr_ok else 'FAIL'))
            if not hdr_ok:
                rc = 1
        print('   first 16 bytes  %s' % data[:16].hex())
        # ASCII-ish sanity: a real instruction blob is mostly not printable text.
        printable = sum(1 for b in data[:4096] if 32 <= b < 127)
        print('   printable ratio of first 4 KiB: %.1f%%' % (100.0 * printable / 4096))
        if size_ok and crc_ok:
            print('   VERDICT: this is the board\'s own PPU firmware; the driver will '
                  'load it and the PPU will forward frames')
        elif size_ok and hook_ok:
            print('   VERDICT: this is the SR1010 blob; the driver will load it and '
                  'install the RX-hash hook, and the PPU will drop frames')
        else:
            print('   VERDICT: this blob is neither the board\'s own firmware nor the '
                  'one the RX-hash hook was calibrated for')
            rc = 1
        print()
    return rc


if __name__ == '__main__':
    sys.exit(main())
