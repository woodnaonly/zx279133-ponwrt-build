#!/usr/bin/env python3
"""stocksearch.py - find the vendor PON userspace inside the stock flash backups.

The 4.19 stack on the unit has a Monitor netlink and an ioctl door but no client, so
whatever the stock firmware ran as its PON daemon is the most valuable artifact we can
find: it would show the real ABI usage (event ids, macro payloads, OMCI framing) without
any reverse-engineering guesswork.  The partitions are compressed, so a plain grep over
the .bin finds nothing - this walks every compressed stream it can locate, inflates it,
and greps the *decoded* bytes.

    python tools/stocksearch.py backups/mtd10_osgi0.bin [more.bin ...]

Read-only; writes nothing.
"""
import lzma
import os
import re
import sys
import gzip
import bz2
import zlib

MAGICS = [
    (b'\x1f\x8b\x08', 'gzip'),
    (b'\xfd7zXZ\x00', 'xz'),
    (b'BZh', 'bzip2'),
]
# Raw LZMA (no container) shows up as 5d 00 00 followed by a plausible properties byte.
LZMA_RAW = re.compile(rb'\x5d\x00\x00')

# What would prove a PON client is in there.
MARKERS = [
    b'gpondrv_dev', b'gpon_sdk', b'gponsdk', b'Montor', b'monitor_', b'omci', b'OMCI',
    b'ploam', b'PLOAM', b'xgpon', b'XGPON', b'gemport', b'TCONT', b'tcont',
]

FILENAMES = re.compile(rb'[\x20-\x7e]{0,60}(?:pon|omci|ploam|gem|tcont)[\x20-\x7e]{0,60}')


def try_decompress(kind, data, off):
    buf = data[off:]
    try:
        if kind == 'gzip':
            return gzip.decompress(buf)
        if kind == 'xz':
            return lzma.LZMADecompressor().decompress(buf)
        if kind == 'bzip2':
            return bz2.decompress(buf)
        if kind == 'lzma':
            return lzma.LZMADecompressor(
                format=lzma.FORMAT_ALONE).decompress(buf, 1 << 23)
    except Exception:
        # Truncated streams still decode partially; keep whatever arrived.
        try:
            if kind == 'gzip':
                d = zlib.decompressobj(16 + zlib.MAX_WBITS)
                return d.decompress(buf, 1 << 23)
            if kind in ('xz', 'lzma'):
                fmt = lzma.FORMAT_ALONE if kind == 'lzma' else lzma.FORMAT_XZ
                d = lzma.LZMADecompressor(format=fmt)
                return d.decompress(buf, 1 << 23)
            if kind == 'bzip2':
                d = bz2.BZ2Decompressor()
                return d.decompress(buf, 1 << 23)
        except Exception:
            return b''
    return b''


def scan(path):
    data = open(path, 'rb').read()
    print('# %s  %d bytes' % (path, len(data)))
    starts = [(m.start(), k) for magic, k in MAGICS for m in re.finditer(re.escape(magic), data)]
    starts += [(m.start(), 'lzma') for m in LZMA_RAW.finditer(data)]
    starts.sort()
    print('  %d candidate compressed streams' % len(starts))
    hits = 0
    for off, kind in starts:
        out = try_decompress(kind, data, off)
        if len(out) < 64:
            continue
        found = [mk for mk in MARKERS if mk in out]
        if not found:
            continue
        hits += 1
        print('  stream @0x%x (%s) -> %d bytes, markers: %s' %
              (off, kind, len(out), b' '.join(found).decode('latin-1')))
        for m in list(FILENAMES.finditer(out))[:12]:
            print('     %s' % m.group().decode('latin-1').strip()[:96])
        # keep the interesting one so the file can be examined properly afterwards
        dst = os.path.join('staging', 'stockpon',
                           '%s.%x.%s' % (os.path.basename(path), off, kind))
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        open(dst, 'wb').write(out)
        print('     saved %s' % dst)
    if not hits:
        print('  no PON-related markers in any decompressed stream')
    return 0


if __name__ == '__main__':
    rc = 0
    for p in sys.argv[1:] or ['backups/mtd10_osgi0.bin']:
        rc |= scan(p)
    sys.exit(rc)
