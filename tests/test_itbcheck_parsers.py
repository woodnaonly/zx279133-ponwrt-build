#!/usr/bin/env python3
"""Self-contained rehearsal for tools/itbcheck.py - it needs no build artifact.

Every other suite in this directory either reads staging/ or cd's to a hard-coded
path on another machine, which is why 40 of them cannot run here. This one carries
its own fixtures: the FIT, the gzip member and the newc archive are built in memory,
so the parsers are exercised on bytes this file can also prove wrong.

A tool that only ever succeeds is not a tool: each check has its refusing twin.
"""
import hashlib
import importlib
import os
import re
import struct
import subprocess
import sys
import tempfile
import zlib
from pathlib import Path

HERE = os.path.dirname(os.path.abspath(__file__))
TOOL = os.path.join(HERE, '..', 'tools', 'itbcheck.py')

sys.path.insert(0, os.path.dirname(TOOL))
import itbcheck  # noqa: E402

ipsec = importlib.import_module('sk-ipsec-kat-image')

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


def build_fdt(root):
    """Flatten nested node dictionaries with bytes-valued properties.

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

    def walk(name, entries):
        begin(name.encode())
        for key, value in entries.items():
            if not isinstance(value, dict):
                prop(key, value)
        for key, value in entries.items():
            if isinstance(value, dict):
                walk(key, value)
        end()

    walk('', root)
    st += struct.pack('>I', 9)
    rsvmap = struct.pack('>QQ', 0, 0)
    off_struct = 0x28
    off_rsvmap = off_struct + len(st)
    off_strings = off_rsvmap + len(rsvmap)
    total = off_strings + len(strings)
    head = struct.pack('>10I', 0xd00dfeed, total, off_struct, off_strings, off_rsvmap,
                       17, 16, 0, len(strings), len(st))
    return head + bytes(st) + rsvmap + bytes(strings)


def build_fit(images):
    return build_fdt({'images': {n: {'type': b'kernel\0', 'compression': comp + b'\0', 'data': blob}
                                 for n, (comp, blob) in images.items()}})


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


def diagnostic_fit(*, enabled=True, nand=False, shell=True, param=True, phandle=1,
                   load=0x80200000, bad_hash=None, ignored_hash=False, swapped_hashes=False,
                   wiring_mutation=None, entry=None, text_offset=0, image_size=0x1000,
                   magic=b'ARM\x64', provider=False, provider_marker=True):
    def hashed(data):
        crc = {'algo': b'crc32\0', 'value': struct.pack('>I', zlib.crc32(data))}
        sha = {'algo': b'sha1\0', 'value': hashlib.sha1(data).digest()}
        if bad_hash == 'crc32':
            crc['value'] = b'\0' * 4
        if bad_hash == 'sha1':
            sha['value'] = b'\0' * 20
        if ignored_hash:
            sha['ignore'] = b''
        if swapped_hashes:
            return {'data': data, 'hash-2': sha, 'hash-1': crc}
        return {'data': data, 'hash-1': crc, 'hash-2': sha}

    cells = struct.pack('>I', 1)
    engine = {
        'compatible': b'zte,zx279133-ipsec\0',
        'status': b'okay\0' if enabled else b'disabled\0',
        'memory-region': struct.pack('>I', phandle),
        'reg': struct.pack('>8I', 0, 0x12000000, 0, 0x1000000, 0, 0x13000000, 0, 0x1000000),
        'interrupts': b''.join(struct.pack('>3I', 0, spi, 4) for spi in range(174, 185)),
        'clocks': struct.pack('>8I', 2, 28, 2, 29, 2, 30, 2, 31),
        'clock-names': b'wclk\0aclk\0sec_hclk\0sec_wclk\0',
        'resets': struct.pack('>2I', 4, 13),
        'reset-names': b'ipsec\0',
    }
    if wiring_mutation:
        engine.update(wiring_mutation)
    board = build_fdt({
        'compatible': b'skyworth,sk-d840n\0zte,zx279133\0',
        'interrupt-parent': struct.pack('>I', 3),
        'interrupt-controller@500000': {'compatible': b'arm,gic-v3\0',
                                        '#interrupt-cells': struct.pack('>I', 3),
                                        'phandle': struct.pack('>I', 3)},
        'reserved-memory': {'buffer@90000000': {
            'no-map': b'', 'status': b'okay\0', 'phandle': cells,
            'reg': struct.pack('>4I', 0, 0x90000000, 0, 0x200000),
        }},
        'soc': {
            'crypto@12000000': engine,
            'clock-controller@10e10000': {'compatible': b'zte,zx279133-topcrm\0syscon\0',
                                          '#clock-cells': cells, 'phandle': struct.pack('>I', 2)},
            'reset-controller@10e10060': {'compatible': b'zte,zx296718-reset\0', '#reset-cells': cells,
                                          'reg': struct.pack('>4I', 0, 0x10e10060, 0, 4),
                                          'phandle': struct.pack('>I', 4)},
            'spi@10d0f000': {'compatible': b'zte,zx279133-sfc\0',
                             'status': b'okay\0' if nand else b'disabled\0'},
        },
    })
    initramfs = build_newc([('bin/sh', 0o120777, b'busybox\0'),
                           ('bin/busybox', 0o100755, b'executable')] if shell else [])
    image = struct.pack('<IIQQQQQQ4sI', 0, 0, text_offset, image_size, 0, 0, 0, 0, magic, 0)
    cmdline = ipsec.PROVIDER_CMDLINE if provider else ipsec.CMDLINE
    image += (cmdline.encode() + b'\0' if param else b'')
    if provider and provider_marker:
        image += b'IRQ KAT passed; registered ecb-aes-zx279133\0'
    image += b'AES-128 ECB encrypt/decrypt KAT passed\0' + initramfs
    entry = load if entry is None else entry
    return build_fdt({
        'images': {'kernel-1': {'compression': b'gzip\0', **hashed(_gz.compress(image)),
                                'load': struct.pack('>I', load), 'entry': struct.pack('>I', entry)},
                   'fdt-1': hashed(board)},
        'configurations': {'default': b'config-1\0',
                           'config-1': {'kernel': b'kernel-1\0', 'fdt': b'fdt-1\0'}},
    })


blob = diagnostic_fit()
manifest = ipsec.verify_fit(blob)
check('IPsec diagnostic accepts and records the FIT and its four U-Boot hashes',
      manifest['bytes'] == len(blob) and manifest['crc32'] == f'{zlib.crc32(blob):08x}' and
      manifest['sha256'] == hashlib.sha256(blob).hexdigest() and len(manifest['hashes']) == 4)
blob = diagnostic_fit(load=0x80280000, text_offset=0x80000,
                      image_size=ipsec.RAM_LOAD - 0x80280000)
check('IPsec diagnostic accepts an aligned base and an Image ending at the FIT address',
      ipsec.verify_fit(blob)['bytes'] == len(blob))
blob = diagnostic_fit(provider=True)
check('IPsec provider gate accepts an explicitly enabled image',
      ipsec.verify_fit(blob, provider=True)['bytes'] == len(blob))
for blob, provider in ((diagnostic_fit(), True), (blob, False),
                       (diagnostic_fit(provider=True, provider_marker=False), True)):
    try:
        ipsec.verify_fit(blob, provider=provider)
        check('IPsec provider gate rejects a mode or provider marker mismatch', False)
    except ValueError:
        check('IPsec provider gate rejects a mode or provider marker mismatch', True)
for state in ({'enabled': False}, {'nand': True}, {'shell': False}, {'param': False},
              {'phandle': 2}, {'load': ipsec.RAM_LOAD}, {'bad_hash': 'crc32'},
              {'magic': b'BAD!'}, {'image_size': 0}, {'entry': 0x80200004},
              {'load': 0x80280000}, {'load': 0x80000000, 'text_offset': 0x200000},
              {'image_size': ipsec.RAM_LOAD - 0x80200000 + 1},
              {'bad_hash': 'sha1'}, {'ignored_hash': True}, {'swapped_hashes': True},
              {'wiring_mutation': {'reg': b''}}, {'wiring_mutation': {'interrupts': b''}},
              {'wiring_mutation': {'interrupts-extended': b''}},
              {'wiring_mutation': {'interrupt-parent': struct.pack('>I', 2)}},
              {'wiring_mutation': {'clocks': struct.pack('>8I', 2, 28, 2, 29, 2, 30, 1, 31)}},
              {'wiring_mutation': {'clock-names': b'aclk\0wclk\0sec_hclk\0sec_wclk\0'}},
              {'wiring_mutation': {'resets': struct.pack('>2I', 4, 12)}},
              {'wiring_mutation': {'resets': struct.pack('>2I', 2, 13)}},
              {'wiring_mutation': {'reset-names': b'sec\0'}}):
    try:
        ipsec.verify_fit(diagnostic_fit(**state))
        check(f'IPsec diagnostic rejects FIT mutation {state}', False)
    except ValueError:
        check(f'IPsec diagnostic rejects FIT mutation {state}', True)

with tempfile.TemporaryDirectory() as tmp:
    config = Path(tmp) / '.config'
    config.write_text('CONFIG_OTHER=y\nCONFIG_KEXEC_FILE=y\n', encoding='utf-8')
    ipsec.set_config(config)
    ipsec.verify_config(config)
    original = config.read_text(encoding='utf-8')
    ipsec.set_config(config)
    check('IPsec diagnostic config edits are idempotent and scoped',
          original == config.read_text(encoding='utf-8') and 'CONFIG_OTHER=y' in original)
    config.write_text(original + 'CONFIG_KEXEC_FILE=y\n', encoding='utf-8')
    try:
        ipsec.verify_config(config)
        check('IPsec diagnostic rejects kexec in resolved config', False)
    except ValueError:
        check('IPsec diagnostic rejects kexec in resolved config', True)

print()
if failures:
    print('%d FAILED: %s' % (len(failures), ', '.join(failures)))
    sys.exit(1)
print('all %d itbcheck checks passed' % checks)
