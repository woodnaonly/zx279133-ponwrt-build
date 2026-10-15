#!/usr/bin/env python3
"""ponDis.py - disassemble one function of a vendor .ko with reloc annotations.

ponelf.py answers "what is there"; this answers "what does it do".  bl/adrp
targets are resolved through .rela<sec> relocations, which is the only way an
external symbol reference in a kernel module is identifiable.

  python tools/ponDis.py pon/vendor-modules/netdriver.ko pdt_ethdrv_send
  python tools/ponDis.py pon/vendor-modules/zte_xgpon.ko fi_Configure_Ponmac_Send_Omci_Msg
  python tools/ponDis.py scan pon/vendor-modules/*.ko 0x8c8
"""
import sys
sys.path.insert(0, 'tools')
import ponelf
from capstone import Cs, CS_ARCH_ARM64, CS_MODE_ARM


def syms(ko):
    return {s['name']: s for s in ko.syms()}


def reloc_map(ko, secname):
    out = {}
    for r in ko.relocs():
        if r['rsec'] == secname:
            out.setdefault(r['off'], []).append(r)
    return out


def annot(r):
    """ponelf keeps three spellings of a reference: UND symbol names, section-symbol
    data refs, and merged-string content in sname.  Pick the readable one."""
    if r['symsec'] == 'UND':
        return r['sym']
    if r['sym'].startswith('.'):
        return r['sname'] or r['sym']
    return r['sym'] + (('+0x%x' % r['add']) if r['add'] else '')


def show(path, func, n=None):
    ko = ponelf.Ko(path)
    S = syms(ko)
    s = S.get(func)
    if s is None:
        have = [k for k in S if func.lower() in k.lower()]
        print('no such symbol; close matches: %s' % ', '.join(have[:12]))
        return 1
    sec = ko.sh[s['shndx']]['sname']
    off = ponelf.sec_off(ko, s)
    if off is None:
        print('%s is in %s (NOBITS), i.e. it is loaded data, not file content' % (func, sec))
        return 1
    size = s['size'] or (n or 64) * 4
    code = ko.d[off:off + size]
    R = reloc_map(ko, sec)
    base = s['value']
    md = Cs(CS_ARCH_ARM64, CS_MODE_ARM)
    md.detail = True
    print('# %s  %s  value=0x%x size=0x%x  (%d bytes)' % (func, sec, base, s['size'], len(code)))
    for i in md.disasm(code, base):
        rs = R.get(i.address - base)
        ann = '  ;' + ' '.join(annot(r) for r in rs) if rs else ''
        print('%08x  %-8s %-40s%s' % (i.address, i.mnemonic, i.op_str, ann))
    return 0


def scan(paths, offset):
    """Which functions touch struct member `offset`?  A field read by a driver and never
    written by anything in the image is dead code, and that is only visible this way."""
    md = Cs(CS_ARCH_ARM64, CS_MODE_ARM)
    want = '#0x%x]' % offset
    for p in paths:
        ko = ponelf.Ko(p)
        ix = ponelf.funclist(ko)
        n = 0
        for f in ko.funcs():
            if not f['size'] or f['sec'].startswith('.exit'):
                continue
            off = ponelf.sec_off(ko, f)
            if off is None:
                continue
            for i in md.disasm(ko.d[off:off + f['size']], f['value']):
                if i.mnemonic in ('ldr', 'str') and want in i.op_str:
                    print('%-14s %-26s %08x  %-4s %s' % (
                        p.split('/')[-1], ponelf.find_func(ix, f['sec'], i.address),
                        i.address, i.mnemonic, i.op_str))
                    n += 1
        print('# %s: %d accesses to +%#x' % (p.split('/')[-1], n, offset))
    return 0


def main():
    a = sys.argv[1:]
    if a and a[0] == 'scan':
        return scan(a[1:-1], int(a[-1], 0))
    return show(a[0], a[1], int(a[2]) if len(a) > 2 else None)


if __name__ == '__main__':
    sys.exit(main())
