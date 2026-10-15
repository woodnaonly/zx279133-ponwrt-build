#!/usr/bin/env python3
# Read a vendor .ko as an ELF object instead of as a bag of strings.
#
# Nobody has source for these 4.19 BSP modules, so the binary IS the
# documentation. A relocatable kernel module keeps every reference as a
# relocation record, which means the call graph and the "which function prints
# which string" map can be rebuilt exactly, without disassembly:
#
#   .symtab  -> function names, sizes, section
#   .rel*    -> (offset inside a section) -> (symbol + addend)
#   both     -> per-function: callees, strings read, data touched
#
#	python tools/ponelf.py pon/vendor-modules/zte_xgpon.ko sections
#	python tools/ponelf.py pon/vendor-modules/zte_xgpon.ko symbols [regex]
#	python tools/ponelf.py pon/vendor-modules/zte_xgpon.ko modinfo
#	python tools/ponelf.py pon/vendor-modules/zte_xgpon.ko imports
#	python tools/ponelf.py pon/vendor-modules/zte_xgpon.ko xref <symbol|regex>
#	python tools/ponelf.py pon/vendor-modules/zte_xgpon.ko strings [regex]
import bisect
import re
import struct
import sys

SHT = {0: 'NULL', 1: 'PROGBITS', 2: 'SYMTAB', 3: 'STRTAB', 4: 'RELA', 6: 'DYNSYM',
       9: 'NOBITS', 0x6ffffffa: 'RELCOUNT', 0x6ffffffb: 'SHFLAGS', 0x70000000: 'ARCH0'}
STT = {0: 'NOTYPE', 1: 'OBJECT', 2: 'FUNC', 3: 'SECTION', 4: 'FILE', 13: 'TLS'}


class Ko:
    def __init__(self, path):
        self.d = open(path, 'rb').read()
        assert self.d[:4] == b'\x7fELF' and self.d[4] == 2, 'need ELF64'
        self.machine, = struct.unpack_from('<H', self.d, 18)
        shoff, = struct.unpack_from('<Q', self.d, 0x28)
        shentsz, self.shnum, self.shstrndx = struct.unpack_from('<HHH', self.d, 0x3a)
        self.sh = []
        for i in range(self.shnum):
            o = shoff + i * shentsz
            (name, typ, flags, addr, off, size, link, info, align, entsz) = \
                struct.unpack_from('<IIQQQQIIQQ', self.d, o)
            self.sh.append(dict(i=i, name=name, typ=typ, flags=flags, addr=addr,
                                off=off, size=size, link=link, info=info, entsz=entsz))
        s = self.sh[self.shstrndx]
        self.shstr = self.d[s['off']:s['off'] + s['size']]
        for x in self.sh:
            x['sname'] = cstr(self.shstr, x['name'])
        self._syms = None

    def section(self, name):
        for x in self.sh:
            if x['sname'] == name:
                return x
        return None

    def bytes_of(self, name):
        s = self.section(name)
        if s is None:
            return b''
        return self.d[s['off']:s['off'] + s['size']]

    def syms(self):
        if self._syms is not None:
            return self._syms
        s = self.section('.symtab')
        strp = self.sh[s['link']]['sname']
        self._strtab = self.bytes_of(strp)
        out = []
        n = s['size'] // (s['entsz'] or 24)
        for i in range(n):
            o = s['off'] + i * (s['entsz'] or 24)
            (name, info, other, shndx, value, size) = \
                struct.unpack_from('<IBBHQQ', self.d, o)
            st_bind, st_type = info >> 4, info & 0xF
            sym = dict(i=i, name=cstr(self._strtab, name), info=info, shndx=shndx,
                       value=value, size=size, bind=st_bind, type=st_type)
            sym['sec'] = self.sh[shndx]['sname'] if 0 < shndx < self.shnum else (
                'ABS' if shndx == 0xFFF1 else 'UND')
            sym['off'] = sec_off(self, sym)
            out.append(sym)
        self._syms = out
        return out

    def funcs(self):
        return sorted((y for y in self.syms() if y['type'] == 2 and y['shndx'] > 0
                       and y['shndx'] < 0xFFF0),
                      key=lambda y: (y['sec'], y['value']))

    def relocs(self):
        """dicts: rsec (section the reloc lives in), off, sym, symsec, add, sname.

        In an unlinked .ko a reference to a string constant is a reloc against the
        *section* symbol (which has no name), so `sym` alone is useless - `symsec`
        plus `add` is what actually says which bytes are being pointed at. sname
        resolves that to the string itself when it is one.
        """
        syms = self.syms()
        out = []
        for s in self.sh:
            if s['typ'] != 4 or not s['sname'].startswith('.rel'):
                continue
            if s['sname'] in ('.rela.dyn', '.rel.dyn'):
                continue
            # ".rela" + target section name verbatim: .rela.text -> .text,
            # .rela__ksymtab -> __ksymtab
            tgt = s['sname'][5:]
            for i in range(s['size'] // 24):
                o = s['off'] + i * 24
                r_off, r_info, r_add = struct.unpack_from('<QQq', self.d, o)
                sym_i = r_info >> 32
                y = syms[sym_i] if sym_i < len(syms) else None
                if y is None:
                    out.append(dict(rsec=tgt, off=r_off, sym='?', symsec='', add=r_add,
                                    sname=''))
                    continue
                name = y['name'] or ('<%s>' % y['sec'])
                text = ''
                if not y['name'] and y['type'] == 3 and r_add >= 0:
                    ts = self.section(y['sec'])
                    if ts is not None and ts['typ'] == 1:
                        raw = self.d[ts['off'] + r_add:ts['off'] + ts['size']]
                        end = raw.find(b'\0')
                        if 0 <= end < 200:
                            cand = raw[:end]
                            if all(32 <= b < 127 or b in (9, 10, 13) for b in cand):
                                text = cand.decode('latin-1')
                                name = y['sec'] + '+%#x' % r_add
                out.append(dict(rsec=tgt, off=r_off, sym=name, symsec=y['sec'],
                                add=r_add, sname=text))
        return out


def cstr(buf, off):
    if off >= len(buf):
        return ''
    e = buf.index(b'\0', off) if b'\0' in buf[off:] else len(buf)
    return buf[off:e].decode('latin-1')


def sec_off(ko, sym):
    """File offset of a symbol, via its section header."""
    if not (0 < sym['shndx'] < ko.shnum):
        return None
    s = ko.sh[sym['shndx']]
    if s['typ'] == 8:  # NOBITS
        return None
    return s['off'] + sym['value']


def funclist(ko):
    """Functions bucketed per section, ascending by offset, for fast lookup."""
    index = {}
    for f in ko.funcs():
        size = f['size'] or 4
        index.setdefault(f['sec'], []).append((f['value'], size, f['name']))
    for k in index:
        index[k].sort()
    return index


def find_func(index, sec, off):
    lst = index.get(sec)
    if not lst:
        return None
    i = bisect.bisect_right(lst, (off, 1 << 60, '')) - 1
    if i < 0:
        return None
    start, size, name = lst[i]
    return name if start <= off < start + size else None


def main():
    path, cmd = sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else 'sections'
    arg = sys.argv[3] if len(sys.argv) > 3 else ''
    ko = Ko(path)
    rx = re.compile(arg) if arg else None

    if cmd == 'sections':
        print('%-30s %-8s %-9s %-8s %s' % ('name', 'type', 'size', 'entsz', 'flags'))
        for s in ko.sh:
            print('%-30s %-8s %#-9x %-8d %#x' % (s['sname'], SHT.get(s['typ'], hex(s['typ'])),
                                                 s['size'], s['entsz'], s['flags']))
        print('\nmachine=%#x (%s)' % (ko.machine, 'AArch64' if ko.machine == 183 else '?'))
    elif cmd == 'symbols':
        for y in ko.syms():
            if rx and not rx.search(y['name']):
                continue
            print('%-6s %-5s %-4s %-14s %-24s %#x' % (y['sec'], STT.get(y['type'], y['type']),
                                                       {0: 'LOCAL', 1: 'GLOBAL', 2: 'WEAK'}.get(y['bind'], ''),
                                                       '', y['name'], y['size']))
    elif cmd == 'modinfo':
        for line in ko.bytes_of('.modinfo').split(b'\0'):
            if line:
                print(line.decode('latin-1'))
    elif cmd == 'imports':
        # Undefined symbols = what this module demands from the rest of the stack.
        ext = sorted({y['name'] for y in ko.syms() if y['sec'] == 'UND' and y['name']})
        index = funclist(ko)
        used = {}
        for r in ko.relocs():
            if r['sym'] not in ext:
                continue
            f = find_func(index, r['rsec'], r['off'])
            if f:
                used.setdefault(r['sym'], set()).add(f)
        for n in ext:
            print('%-50s %s' % (n, ', '.join(sorted(used.get(n, {'(no code ref)'})))[:160]))
    elif cmd == 'ksymtab':
        # struct kernel_symbol { const char *sym; unsigned long crc; } in 4.19: 16
        # bytes per entry. The name pointer is a reloc against the (unnamed) section
        # symbol of __ksymtab_strings, so the string itself is the useful half.
        ents = {}
        for r in ko.relocs():
            if r['rsec'] != '__ksymtab':
                continue
            k = r['off'] // 16
            e = ents.setdefault(k, ['', ''])
            if r['off'] % 16 == 0:
                e[0] = r['sym']
            else:
                e[1] = r['sname']
        for k in sorted(ents):
            print('%-46s <- %s' % (ents[k][1] or '?', ents[k][0]))
    elif cmd == 'funcs':
        for f in ko.funcs():
            if rx and not rx.search(f['name']):
                continue
            print('%-14s %#-8x %-46s %s' % (f['sec'], f['value'], f['name'], f['size']))
    elif cmd == 'xref':
        if not rx:
            sys.exit('xref needs a symbol pattern')
        targets = [f for f in ko.funcs() if rx.search(f['name'])]
        for y in ko.syms():
            if rx.search(y['name'] or '') and y['type'] in (1, 2) and y not in targets:
                targets.append(y)
        for y in sorted(targets, key=lambda s: (s['sec'], s['value'])):
            size = y['size'] or 4
            print('== %s  [%s + %#x, size %#x]' % (y['name'], y['sec'], y['value'], y['size']))
            seen = set()
            for r in sorted((r for r in ko.relocs() if r['rsec'] == y['sec']
                             and y['value'] <= r['off'] < y['value'] + size),
                            key=lambda r: r['off']):
                lbl = r['sym']
                if r['sname']:
                    lbl = '%s  "%s"' % (lbl, r['sname'][:96])
                key = (lbl, r['add'])
                if key in seen:
                    continue
                seen.add(key)
                print('   +%-#-8x %s' % (r['off'], lbl))
    elif cmd == 'strings':
        for s in ko.syms():
            if s['type'] == 1 and (s['sec'].startswith('.rodata') or
                                   s['sec'].startswith('.str1')):
                o = sec_off(ko, s)
                if o is None:
                    continue
                t = ko.d[o:o + s['size']].rstrip(b'\0').decode('latin-1')
                if rx and not rx.search(t):
                    continue
                print('%-16s %-40s %s' % (s['sec'], s['name'], t.replace('\n', '\\n')[:150]))

    elif cmd == 'strpool':
        # Merged string sections (.rodata.str1.1) usually have no symbols at all,
        # so dump the section bytes and split on NUL.
        for s in ko.sh:
            if not (s['sname'].startswith('.rodata') or s['sname'].startswith('.str')):
                continue
            buf = ko.d[s['off']:s['off'] + s['size']]
            for piece in buf.split(b'\0'):
                t = piece.decode('latin-1')
                if len(t) >= 5 and (not rx or rx.search(t)):
                    print('%-20s %s' % (s['sname'], t.replace('\n', '\\n')[:150]))


if __name__ == '__main__':
    main()
