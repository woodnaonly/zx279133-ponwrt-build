#!/usr/bin/env python3
# Find which of our raw flash dumps actually contains a filesystem, and what kind.
# Vendor ONT partitions are a soup of kernel+rootfs+OSGi jars, so this just reports
# magic signatures with offsets - extraction follows only where there is a hit.
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
D = os.path.join(ROOT, 'backups')

MAGICS = {
    'squashfs': re.compile(rb'hsqs'),
    'cramfs': re.compile(rb'\x45\x19\x85\x25'),
    'jffs2-eb': re.compile(rb'\x19\x85\x0c\x20'),          # JFFS2_MAGIC_BITMASK
    'ubi': re.compile(rb'UBI#\x21'),
    'cpio-newc': re.compile(rb'070701'),
    'gzip': re.compile(rb'\x1f\x8b\x08'),
    'xz': re.compile(rb'\xfd7zXZ\x00'),
    'lzma-oplw': re.compile(rb'\x5d\x00\x00'),
    'elf32': re.compile(rb'\x7fELF\x01\x01'),
    'elf64': re.compile(rb'\x7fELF\x02\x01'),
    'java-class': re.compile(rb'\xca\xfe\xba\xbe'),
    'zip': re.compile(rb'PK\x03\x04'),
    'uImage': re.compile(rb'\x27\x05\x05\x30'),
}

for name in sorted(os.listdir(D)):
    p = os.path.join(D, name)
    if not name.endswith('.bin') or not os.path.isfile(p):
        continue
    data = open(p, 'rb').read()
    # skip the all-FF/00 padding quickly
    print('== %s (%d bytes)' % (name, len(data)))
    for k, rx in MAGICS.items():
        hits = [m.start() for m in rx.finditer(data)]
        if not hits:
            continue
        # report count and a few offsets, collapsing dense runs
        sample = hits[:6]
        print('   %-12s count=%-7d first=%s' % (k, len(hits),
                                                ','.join(str(h) for h in sample)))
