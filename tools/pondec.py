#!/usr/bin/env python3
# Decode the two command surfaces of the vendor PON stack out of the .ko files.
#
# Neither ZTE nor Skyworth shipped source or a header for these, and both surfaces
# are data-driven tables the driver builds at init, so the only faithful way to read
# them is to walk the binary the way the driver itself does:
#
#	python tools/pondec.py cli    -> /proc/gpon_sdk/debug commands   (zte_xgpon)
#	python tools/pondec.py ioctl  -> /dev/gpondrv_dev macro ids       (gpondrv_xg)
#
# The cli records are 0x130-byte structs in .data: name / handler / sscanf format /
# argc / usage. The ioctl ids are the w0 immediates fed to fuc_table_node_create()
# by public_interface_init() and private_pdt_init(), which needs a disassembly pass.
import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from ponelf import Ko, sec_off                                    # noqa: E402
from capstone import Cs, CS_ARCH_ARM64, CS_MODE_LITTLE_ENDIAN      # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DIR = os.path.join(ROOT, 'pon', 'vendor-modules')

RECORD = 0x130
COUNT = 0x2c                     # the write handler stops (and prints help) here
NAME, FN, FMT, NARGS, USAGE = 0x00, 0x20, 0x28, 0x68, 0x70


def reloc_map(ko, section):
    return {r['off']: r for r in ko.relocs() if r['rsec'] == section}


def handler_of(r, funcs):
    """Resolve a relocation that names a function into that function's name.

    A .ko encodes the same thing three ways depending on whether the target has a
    symbol: a named symbol, ".text+0x.." for the odd local label, or a bare section
    symbol plus addend for the statics the compiler never named.
    """
    if r is None:
        return '(none)'
    sym = r['sym']
    if sym.startswith('.text+'):
        addr = int(sym[6:], 16)
    elif sym == '<.text>':
        addr = r['add']
    else:
        return sym
    return funcs.get(addr, 'unnamed .text+%x' % addr)


def cstr_at(ko, section, off):
    s = ko.section(section)
    raw = ko.d[s['off'] + off:]
    e = raw.find(b'\0')
    return raw[:e].decode('latin-1') if e >= 0 else ''


def cli():
    ko = Ko(os.path.join(DIR, 'zte_xgpon.ko'))
    rel = reloc_map(ko, '.data')
    dat = ko.section('.data')
    funcs = {f['value']: f['name'] for f in ko.funcs() if f['sec'] == '.text'}
    first = ko.d[dat['off']:dat['off'] + dat['size']].find(b'pstate\0')
    if first < 0:
        sys.exit('could not locate the pstate record')
    print('# /proc/gpon_sdk/debug - the %d-command table read out of zte_xgpon.ko' % COUNT)
    print('# write syntax:  echo "<name> [args]" > /proc/gpon_sdk/debug')
    print('# the handler is called as fn(int, ...) after sscanf(rest, fmt, &a0..a6).')
    print('# The kernel answers in dmesg with "func <name> return success".')
    print('# An unknown token does not error - the write handler dumps this whole')
    print('# table into dmesg instead, so `echo nonsense > /proc/gpon_sdk/debug` is')
    print('# the read-only way to recover the vocabulary from a live unit.')
    print('#')
    print('# p* = print   (read only; output goes to the kernel log)')
    print('# s* = set     (writes real PON configuration)')
    print('# r* = remove, c* = clear   <-- destructive, they delete T-CONT/GEM entries')
    print('#')
    print('# one command is not in this table: `ssn <12 chars>` is matched before the')
    print('# loop and calls test_Configure_Ponmac_Set_Sn(), i.e. programs the SN.')
    print('%-18s %-4s %-8s %-52s %s' % ('command', 'argc', 'fmt', 'handler', 'usage'))
    for i in range(COUNT):
        base = first + i * RECORD
        f = rel.get(base + FN)
        print('%-18s %-4d %-8s %-52s %s' % (
            cstr_at(ko, '.data', base + NAME),
            struct.unpack_from('<I', ko.d, dat['off'] + base + NARGS)[0],
            cstr_at(ko, '.data', base + FMT) or '-',
            handler_of(f, funcs),
            cstr_at(ko, '.data', base + USAGE)))


def ioctl():
    ko = Ko(os.path.join(DIR, 'gpondrv_xg.ko'))
    rel = reloc_map(ko, '.text')
    names = {f['value']: f['name'] for f in ko.funcs() if f['sec'] == '.text'}
    handler_names = set(names.values())
    md = Cs(CS_ARCH_ARM64, CS_MODE_LITTLE_ENDIAN)
    out = []
    for tbl, kind in (('public_interface_init', 'public'), ('private_pdt_init', 'private')):
        y = next(s for s in ko.syms() if s['name'] == tbl and s['type'] == 2)
        code = ko.d[sec_off(ko, y):sec_off(ko, y) + y['size']]
        cid, handler = None, None
        for i in md.disasm(code, y['value']):
            r = rel.get(i.address)
            if i.mnemonic == 'mov' and i.op_str.startswith('w0, #'):
                v = i.op_str.split('#')[1]
                cid = int(v, 16) if v.startswith('0x') else int(v)
            elif i.mnemonic == 'add' and r and (r['sym'] in handler_names or
                                                r['sym'].startswith(('.text+', '<.text>'))):
                handler = handler_of(r, names)
            elif i.mnemonic == 'bl' and r and r['sym'] == 'fuc_table_node_create':
                out.append((kind, cid, handler or '(none)'))
                handler = None
    print('# /dev/gpondrv_dev - the command table read out of gpondrv_xg.ko')
    print('#')
    print('# ABI, from gpondrv_devIoctl(): ioctl(fd, ANY_CMD, &buf) over a 136-byte')
    print('# struct, synchronous request/response:')
    print('#   +0x00 u32  result flag - if the handler leaves it nonzero nothing is')
    print('#              copied back, so a caller must zero it first')
    print('#   +0x04 u32  the macro id below (dmesg: "ioctl_data->m_gponFuncMacro")')
    print('#   +0x08..    payload; the handler is invoked as fn(buf + 8)')
    print('# The ioctl() cmd argument itself is ignored. All 136 bytes are copied')
    print('# back to userspace on success.')
    print('#')
    print('# major 102 ("gpondrv_dev" in /proc/devices); this image creates no node:')
    print('#   mknod /dev/gpondrv_dev c 102 0')
    print('%-8s %-6s %s' % ('table', 'macro', 'handler'))
    for kind, cid, h in sorted(out, key=lambda x: (x[0] != 'public', x[1] or 0)):
        print('%-8s %-6s %s' % (kind, cid, h))


if sys.argv[1:2] == ['cli']:
    cli()
elif sys.argv[1:2] == ['ioctl']:
    ioctl()
else:
    sys.exit('usage: pondec.py {cli|ioctl}')
