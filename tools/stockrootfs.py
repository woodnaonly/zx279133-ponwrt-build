#!/usr/bin/env python3
# Extract the gzip streams hidden inside the stock kernel partitions and identify
# them. mtd3_kernel0 / mtd6_kernel1 hold a boot wrapper + kernel, and then further
# gzip streams at 15-19 MB - which on these ONTs is the rootfs.
#
#	python tools/stockrootfs.py backups/mtd3_kernel0.bin
import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, 'staging', 'stockroot')
os.makedirs(OUT, exist_ok=True)


def streams(data):
    for m in re.finditer(rb'\x1f\x8b\x08', data):
        yield m.start()


def try_gzip(path, off, name):
    """Run gzip -dc on the tail starting at off; report what came out."""
    raw = open(path, 'rb').read()
    chunk = raw[off:]
    dst = os.path.join(OUT, name)
    with open(dst + '.gzpart', 'wb') as fh:
        fh.write(chunk)
    # gzip -dc tolerates trailing garbage after the last member, python's gzip does not.
    r = subprocess.run(['gzip', '-dc', dst + '.gzpart'], stdout=open(dst, 'wb'),
                       stderr=subprocess.PIPE)
    n = os.path.getsize(dst)
    head = open(dst, 'rb').read(8)
    kind = 'unknown'
    if head.startswith(b'070701'):
        kind = 'cpio-newc (initramfs)'
    elif head[:4] == b'hsqs':
        kind = 'squashfs'
    elif head[:2] == b'\x27\x05':
        kind = 'uImage'
    elif head[4:8] == b'\x00\x00\x00\x00' and n > 4096:
        kind = 'raw/other'
    print('%-34s off=%-10d rc=%d out=%-10d %s head=%s' % (
        os.path.basename(path), off, r.returncode, n, kind, head.hex()))
    return dst, kind, n


path = sys.argv[1] if len(sys.argv) > 1 else os.path.join(ROOT, 'backups', 'mtd3_kernel0.bin')
data = open(path, 'rb').read()
for i, off in enumerate(streams(data)):
    dst, kind, n = try_gzip(path, off, '%s.%d.out' % (os.path.basename(path), i))
    if kind.startswith('cpio'):
        names = re.findall(rb'070701[\x00-\xff]{104}([ -~]{2,96}?)\x00', open(dst, 'rb').read())
        print('   cpio members found: %d' % len(names))
