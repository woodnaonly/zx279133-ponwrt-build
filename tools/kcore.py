#!/usr/bin/env python3
# Read and disassemble live kernel code/data out of /proc/kcore on the target.
#
# Why this exists: the vendor 4.19 image carries code we have no source for - the
# "Monitor" event subsystem is built into vmlinuz, not in a .ko, so no amount of
# module analysis reaches it. /proc/kcore is readable as root and its program headers
# give an exact VA -> file-offset map, so a few dd bytes over ssh beat reconstructing
# and matching a kernel build by hand.
#
# `dis` tracks the ADRP/ADD pairs the compiler emits for data references, so each
# printk format string and function pointer shows up inline in the listing.
#
#	python tools/kcore.py dis  ffffff80085c55c8 0x240   # disassemble
#	python tools/kcore.py ptrs ffffff8008b0fea8 0x40    # decode pointers
#	python tools/kcore.py dump ffffff8008a8cb38 0x60    # hexdump
import os
import re
import struct
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CACHE = os.path.join(ROOT, 'staging')
OW = os.path.join(ROOT, 'tools', 'ow.sh')
KBASE = 0xffffff8008080000      # _text, per the first kcore PT_LOAD
KOFF = 0x8082000                # that segment's p_offset
KSIZE = 0xaa3000

ADR = re.compile(r'^(x\d+), #0x([0-9a-f]+)$')
ADD = re.compile(r'^(x\d+), (x\d+), #0x([0-9a-f]+)$')
MEM = re.compile(r'^(x\d+), \[(x\d+)(?:, #0x([0-9a-f]+))?\]$')
BR = re.compile(r'^#0x([0-9a-f]+)$')


def rpc(cmd):
    # Bytes, not text=True: on Windows Python translates '\n' to '\r\n' when it feeds
    # a subprocess stdin, and a trailing CR silently corrupts the remote command line.
    r = subprocess.run(['sh', OW], input=(cmd + '\n').encode(),
                       capture_output=True)
    out = r.stdout.decode('latin-1')
    if not out.strip() and r.stderr:
        sys.stderr.write(r.stderr.decode('latin-1')[:400] + '\n')
    return out.replace('\r', '')


def read_va(va, n):
    """Bytes at kernel virtual address `va` inside the mapped kernel segment."""
    off = va - KBASE + KOFF
    if va < KBASE or va - KBASE + n > KSIZE:
        sys.exit('%#x is outside the kcore segment this tool maps' % va)
    blk, rem = divmod(off, 4096)
    count = (rem + n + 4095) // 4096
    out = rpc('dd if=/proc/kcore bs=4096 skip=%d count=%d 2>/dev/null | '
              'hexdump -v -e "1/1 \\"%%02x\\""' % (blk, count))
    blob = re.sub(r'[^0-9a-f]', '', out)
    if len(blob) < (rem + n) * 2:
        sys.stderr.write(out[-300:] + '\n')
        sys.exit('short read at %#x: %d hex chars, need %d' % (va, len(blob), (rem + n) * 2))
    return bytes.fromhex(blob)[rem:rem + n]


def syms(prefix):
    """kallsyms rows whose address starts with the hex string `prefix`, cached."""
    path = os.path.join(CACHE, 'kallsyms-%s.txt' % prefix)
    if not os.path.exists(path):
        open(path, 'w').write(rpc('grep -E "^%s[0-9a-f]+ [a-zA-Z] " /proc/kallsyms' % prefix))
    rows = set()
    for line in open(path):
        f = line.split()
        if len(f) >= 3:
            rows.add((int(f[0], 16), ' '.join(f[2:])))
    return sorted(rows)


def name_of(map_, addr):
    near = None
    for a, n in map_:
        if a == addr:
            return n
        if a < addr and (near is None or a > near[0]):
            near = (a, n)
    return '%s+0x%x' % (near[1], addr - near[0]) if near else None


def cstr_at(addr):
    if not (0xffffff8008000000 < addr < 0xffffff8009000000):
        return ''
    try:
        raw = read_va(addr, 80)
    except SystemExit:
        return ''
    e = raw.find(b'\0')
    if not 0 < e:
        return ''
    s = raw[:e]
    return s.decode('latin-1') if all(32 <= b < 127 or b in (9, 10, 13) for b in s) else ''


def dis(va, n):
    from capstone import Cs, CS_ARCH_ARM64, CS_MODE_LITTLE_ENDIAN
    data = read_va(va, n)
    near = sorted(set(syms('%x' % (va >> 20)) + syms('%x' % (va >> 24)) +
                      syms('ffffff80085') + syms('ffffff8008b') + syms('ffffff8008a')))
    md = Cs(CS_ARCH_ARM64, CS_MODE_LITTLE_ENDIAN)
    page = {}
    for i in md.disasm(data, va):
        note = ''
        if i.mnemonic == 'adrp':
            m = ADR.match(i.op_str)
            if m:
                page[m.group(1)] = int(m.group(2), 16)
        elif i.mnemonic in ('add', 'ldr', 'ldur', 'ldrb', 'str', 'strb', 'stur'):
            m = ADD.match(i.op_str)
            if m and m.group(2) in page:
                page[m.group(1)] = page[m.group(2)] + int(m.group(3), 16)
            else:
                m = MEM.match(i.op_str)
                base = page.get(m.group(2)) if m else None
                if m and base is not None:
                    page.pop(m.group(1), None)
                    target = base + int(m.group(3) or '0', 16)
                    note = '   ; [%s] %s' % (i.mnemonic, name_of(near, target) or '%x' % target)
            if i.mnemonic == 'add' and page.get(i.op_str.split(',')[0].strip()):
                t = page[i.op_str.split(',')[0].strip()]
                note = '   ; -> %s' % (name_of(near, t) or '%x' % t)
                s = cstr_at(t)
                if s:
                    note += '  "%s"' % s[:64]
        elif i.mnemonic in ('bl', 'b'):
            m = BR.match(i.op_str)
            if m:
                note = '   ; -> %s' % (name_of(near, int(m.group(1), 16)) or m.group(1))
        print('%-14x %-8s %s%s' % (i.address, i.mnemonic, i.op_str, note))


def main():
    what, va = sys.argv[1], int(sys.argv[2], 16)
    n = int(sys.argv[3], 16) if len(sys.argv) > 3 else 0x40
    if what == 'dis':
        return dis(va, n)
    data = read_va(va, n)
    if what == 'dump':
        for i in range(0, len(data), 16):
            row = data[i:i + 16]
            print('%-14s %-48s %s' % ('%x' % (va + i), row.hex(' '),
                                      ''.join(chr(b) if 32 <= b < 127 else '.' for b in row)))
        return
    if what == 'ptrs':
        wide = sorted(set(syms('%x' % (va >> 24)) + syms('ffffff80085') +
                          syms('ffffff8008a') + syms('ffffff8008b') + syms('ffffff80087')))
        for i in range(0, len(data) - 7, 8):
            v, = struct.unpack_from('<Q', data, i)
            tag = ''
            if 0xffffff8008000000 < v < 0xffffff800a000000:
                tag = name_of(wide, v) or ''
                if not tag:
                    tag = ('"%s"' % cstr_at(v)) if cstr_at(v) else ''
            print('%-18x %#-20x %s' % (va + i, v, tag))
        return
    sys.exit('usage: kcore.py {dis|ptrs|dump} <hex VA> <hex len>')


main()
