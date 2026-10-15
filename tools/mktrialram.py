#!/usr/bin/env python3
# Patch the trial files from ci-build-repo/trial-files into the initramfs of an
# arm64 Image, and emit it as a standalone cpio.gz that kexec can be given with
# --initrd. No compiling happens here: the kernel and dtb come from CI untouched,
# only the RAM rootfs gains the trial-only files, which lets one physical round
# answer "did mainline boot?" instead of only "did mainline answer ssh?".
#
#	python tools/mktrialram.py [Image] [out]
#
# The Image read is the already-unpacked one; tools/gettrial.sh ships trial-Image.gz,
# so: gunzip -c staging/trial/trial-Image.gz > staging/trial/trial-Image
import os
import re
import sys
import gzip
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
IMG = sys.argv[1] if len(sys.argv) > 1 else os.path.join(ROOT, 'staging/trial/trial-Image')
OUT = sys.argv[2] if len(sys.argv) > 2 else os.path.join(ROOT, 'staging/trial/trial-initramfs.cpio.gz')
FILES = os.path.join(ROOT, 'ci-build-repo/trial-files')
EXEC_DIRS = ('etc/init.d/', 'etc/hotplug.d/', 'etc/rc.d/', 'usr/libexec/',
             'bin/', 'sbin/')


def h(b):
    return int(b.decode('ascii'), 16)


def parse(buf):
    """Return (entries, trailer_range). Each entry is (name, raw bytes)."""
    starts = [m.start() for m in re.finditer(rb'070701', buf)]
    best = None
    for s in starts:
        n, end = try_parse(buf, s)
        if n and (best is None or n > best[0]):
            best = (n, s, end)
    if not best:
        sys.exit('no readable cpio newc archive in %s' % IMG)
    _, start, end = best
    entries = []
    off = start
    while True:
        assert buf[off:off + 6] == b'070701', off
        nsize = h(buf[off + 94:off + 102])
        fsize = h(buf[off + 54:off + 62])
        name = buf[off + 110:off + 110 + nsize].rstrip(b'\0').decode()
        ds = off + ((110 + nsize + 3) & ~3)
        nxt = ds + ((fsize + 3) & ~3)
        raw = buf[off:nxt]
        if name == 'TRAILER!!!':
            return entries, raw, start
        entries.append([name, raw])
        off = nxt


def try_parse(buf, off):
    n = 0
    try:
        while off + 110 <= len(buf):
            if buf[off:off + 6] != b'070701':
                return None, None
            fsize = h(buf[off + 54:off + 62])
            nsize = h(buf[off + 94:off + 102])
            if not 0 < nsize <= 300:
                return None, None
            nm = buf[off + 110:off + 110 + nsize].rstrip(b'\0')
            try:
                nm.decode()
            except UnicodeDecodeError:
                return None, None
            ds = off + ((110 + nsize + 3) & ~3)
            off = ds + ((fsize + 3) & ~3)
            n += 1
            if nm == b'TRAILER!!!':
                return n, ds + ((fsize + 3) & ~3)
    except Exception:
        return None, None
    return None, None


def newc(name, data, mode, now):
    name_b = name.encode() + b'\0'
    hdr = (b'070701' + b'%08x' * 13) % (
        0, mode, 0, 0, 1, now, len(data), 0, 0, 0, 0, len(name_b), 0)
    pad = b'\0' * ((4 - (110 + len(name_b)) % 4) % 4)
    body = data + b'\0' * ((4 - len(data) % 4) % 4)
    return hdr + name_b + pad + body


def walk(base):
    out = {}
    for dirpath, _dirs, names in os.walk(base):
        for fn in names:
            p = os.path.join(dirpath, fn)
            rel = os.path.relpath(p, base).replace('\\', '/')
            # the tree is edited on Windows, and ash/uci do not want CRLF
            with open(p, 'rb') as fh:
                data = fh.read().replace(b'\r\n', b'\n')
            mode = 0o100755 if rel.startswith(EXEC_DIRS) else 0o100644
            out[rel] = (data, mode)
    return out


entries, trailer, start = parse(open(IMG, 'rb').read())
print('cpio at 0x%x, %d members' % (start, len(entries)))

add = walk(FILES)
if not add:
    sys.exit('nothing under %s' % FILES)

now = int(time.time())
have = {e[0] for e in entries}
for i, e in enumerate(entries):
    if e[0] in add:
        data, mode = add[e[0]]
        entries[i] = [e[0], newc(e[0], data, mode, now)]
        print('replaced %s (%d B)' % (e[0], len(data)))
for name, (data, mode) in sorted(add.items()):
    if name not in have:
        entries.append([name, newc(name, data, mode, now)])
        print('added    %s (%d B, %s)' % (name, len(data), oct(mode)))

blob = b''.join(e[1] for e in entries) + trailer
with gzip.open(OUT, 'wb', compresslevel=9) as fh:
    fh.write(blob)
print('wrote %s: %d members, %d B uncompressed, %d B gz' %
      (OUT, len(entries), len(blob), os.path.getsize(OUT)))
