#!/usr/bin/env python3
"""Self-contained rehearsal for tools/itbcheck.py - it needs no build artifact.

Every other suite in this directory either reads staging/ or cd's to a hard-coded
path on another machine, which is why 40 of them cannot run here. This one carries
its own fixtures: the FIT, the gzip member and the newc archive are built in memory,
so the parsers are exercised on bytes this file can also prove wrong.

A tool that only ever succeeds is not a tool: each check has its refusing twin.
"""
import hashlib
import os
import re
import struct
import subprocess
import sys
import tempfile
import zlib

HERE = os.path.dirname(os.path.abspath(__file__))
TOOL = os.path.join(HERE, '..', 'tools', 'itbcheck.py')

sys.path.insert(0, os.path.dirname(TOOL))
import itbcheck  # noqa: E402

failures = []
checks = 0


def check(name, ok, detail=''):
    global checks
    checks += 1
    print(('PASS  ' if ok else 'FAIL  ') + name + (('  ' + detail) if detail else ''))
    if not ok:
        failures.append(name)


def pad4(b):
    return b + b'\0' * ((4 - len(b) % 4) % 4)


def build_newc(entries):
    """newc: 110-byte ASCII-hex header, name (with NUL) padded so header+name is
    4-aligned *in absolute terms*, then data padded the same way."""
    out = bytearray()
    for i, (name, mode, data) in enumerate(entries):
        nm = name.encode() + b'\0'
        fields = [0o707071 + 0, mode, 0, 0, 1, 0, len(data), 0, 0, 0, 0, len(nm), 0]
        head = b'070701' + b''.join(b'%08X' % v for v in fields)
        rec = bytearray(head + nm)
        while len(rec) % 4:
            rec += b'\0'
        rec += data
        while len(rec) % 4:
            rec += b'\0'
        out += rec
    tr = bytearray(b'070701' + b''.join(b'%08X' % v for v in
                                        [0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, len(b'TRAILER!!!\0'), 0])
                   + b'TRAILER!!!\0')
    while len(tr) % 4:
        tr += b'\0'
    return bytes(out + tr)


def build_fit(images):
    """A minimal-but-real FDT: { /images/{kernel-1: type, compression, data} }.

    Deliberately built with the property *data* following the (length, name-offset)
    pair and the name living in the strings block, because that is the detail the
    first version of the walker got wrong.
    """
    st = bytearray()
    strings = bytearray(b'\0')

    def sref(name):
        off = len(strings)
        strings.extend(name.encode() + b'\0')
        return off

    def begin(name=b''):
        nonlocal st
        st += struct.pack('>I', 1) + name + b'\0'
        st += b'\0' * ((4 - len(st) % 4) % 4)

    def prop(name, data):
        st[:] = st + struct.pack('>III', 3, len(data), sref(name)) + pad4(data)

    def end():
        st[:] = st + struct.pack('>I', 2)

    begin()
    begin(b'images')
    for n, (comp, blob) in images.items():
        begin(n.encode())
        prop('type', b'kernel\0')
        prop('compression', comp + b'\0')
        prop('data', blob)
        end()
    end()
    end()
    st += struct.pack('>I', 9)
    rsvmap = struct.pack('>QQ', 0, 0)
    off_struct = 0x28
    off_rsvmap = off_struct + len(st)
    off_strings = off_rsvmap + len(rsvmap)
    total = off_strings + len(strings)
    head = struct.pack('>10I', 0xd00dfeed, total, off_struct, off_strings, off_rsvmap,
                       17, 16, 0, len(strings), len(st))
    return head + bytes(st) + rsvmap + bytes(strings)


BRINGUP = b'#!/bin/sh\n# fixed\nsleep 5\n'
DIGEST = hashlib.sha256(BRINGUP).hexdigest()

# --- 1. the parsers, on fixtures this file also knows how to make wrong ------------------
cpio = build_newc([('.', 0o40755, b''), ('usr/libexec/zrec-bringup', 0o100755, BRINGUP),
                  ('bin/sh', 0o120755, b'/usr/bin/busybox\0'),
                  ('etc/hosts', 0o100644, b'127.0.0.1 localhost\n')])
recs = list(itbcheck.newc_records(cpio))
check('newc walk reaches the trailer', recs and recs[-1][0] == 'TRAILER!!!',
      '%d records' % len(recs))
check('newc yields the right bytes for a file', dict((r[0], r[2]) for r in recs)
      ['usr/libexec/zrec-bringup'] == BRINGUP)
# The record class that kills the on-disk unpacker: a symlink whose body keeps its NUL.
sym = dict((r[0], r[2]) for r in recs)['bin/sh']
check('a symlink record with its NUL does not end the walk', sym == b'/usr/bin/busybox\0'
      and recs[-1][0] == 'TRAILER!!!')

# --- 2. the false positives, refused explicitly ------------------------------------------
junk = b'\x00' * 4 + b'070701' + b'not hex at all...........' + os.urandom(4096)
check('070701 inside random data yields no records', list(itbcheck.newc_records(junk)) == [])
check('find_initramfs says no to a kernel with no cpio',
      itbcheck.find_initramfs(os.urandom(20000)) is None)
# The trailer record is 110 header bytes + "TRAILER!!!\0" (11) padded up to 124.
no_trailer = cpio[:-124]
check('a cpio that never reaches its trailer is refused',
      list(itbcheck.newc_records(no_trailer))[-1][0] != 'TRAILER!!!'
      and itbcheck.find_initramfs(no_trailer) is None)

# --- 3. the container: a FIT whose kernel-1 is a gzip member, exactly like a real kit -----
import gzip as _gz  # noqa: E402
itb = build_fit({'kernel-1': (b'gzip', _gz.compress(cpio, 9))})
which, image = itbcheck.kernel_image(itb)
check('kernel_image finds kernel-1 and inflates it', which == '/images/kernel-1:data'
      and image == cpio, which)
found = itbcheck.find_initramfs(image)
check('the initramfs inside the synthetic FIT is located', found is not None
      and len(found[1]) == 5)
try:
    itbcheck.fdt_props(b'HelloHeLLo' * 8)
    check('a non-FDT blob raises instead of wandering', False)
except ValueError as e:
    check('a non-FDT blob raises instead of wandering', 'flattened device tree' in str(e), str(e))

# --- 4. the CLI, which is what a round actually runs --------------------------------------
tmp = tempfile.NamedTemporaryFile(suffix='.itb', delete=False)
tmp.write(itb)
tmp.close()
try:
    env = dict(os.environ, PYTHONUTF8='1')
    r = subprocess.run([sys.executable, TOOL, tmp.name,
                        '--expect', 'usr/libexec/zrec-bringup=' + DIGEST],
                       capture_output=True, env=env)
    out = r.stdout.decode('utf-8', 'replace')
    check('--expect with the right digest exits 0', r.returncode == 0, out.strip()[:120])
    check('--expect prints the mode and size',
          re.search(r'mode=100755\s+%d bytes' % len(BRINGUP), out) is not None,
          out.strip()[:160])
    r = subprocess.run([sys.executable, TOOL, tmp.name,
                        '--expect', 'usr/libexec/zrec-bringup=' + '0' * 64],
                       capture_output=True, env=env)
    check('--expect with a wrong digest exits 1', r.returncode == 1
          and 'FAIL' in r.stdout.decode('utf-8', 'replace'))
    r = subprocess.run([sys.executable, TOOL, tmp.name, 'usr/sbin/zrec'],
                       capture_output=True, env=env)
    check('a member that is not in the kit is reported MISSING, not skipped',
          r.returncode == 1 and 'MISSING usr/sbin/zrec' in r.stdout.decode('utf-8', 'replace'))
finally:
    os.unlink(tmp.name)

print()
if failures:
    print('%d FAILED: %s' % (len(failures), ', '.join(failures)))
    sys.exit(1)
print('all %d itbcheck checks passed' % checks)
