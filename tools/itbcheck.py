#!/usr/bin/env python3
"""Read one file out of the initramfs inside a .itb - read-only, no cpio, no root.

Why this exists: the delivery gates prove what the packer staged, but the only way to
know what a *released kit* carries is to open the image, and on this PC there is no
cpio, no dumpimage and no OpenWrt tree. The image is also the thing a board round is
about to boot, so "the fix is in git" and "the fix is in the bytes the board will run"
are two claims, and until now only the first one was ever checked.

tools/itbrootfs.py - the first version of this idea - cannot unpack this image class:
it writes records to disk and os.symlink()s a symlink record's body, which still
carries its NUL, so the walk dies partway and reports nothing. Nothing here touches
the filesystem, so the records that break the unpacker are read fine.
"""
import argparse
import gzip
import hashlib
import re
import struct
import sys
import zlib

BEGIN_NODE, END_NODE, PROP, NOP, FDT_END = 1, 2, 3, 4, 9
NEWC_MAGIC = b'070701'
# ino mode uid gid nlink mtime datasize devmajor devminor rdevmajor rdevminor namesize check
NEWC_FIELDS = 13


def fdt_props(d):
    """Flatten a whole FDT to {'<path>:<property>': raw bytes}.

    Property data follows the (length, name-offset) pair - the name lives in the
    strings block, not inline. Reading it as if the name were inline was the first
    version's failure mode: it walked into the data and died on a UnicodeDecodeError
    that looks like a broken image rather than a broken parser.
    """
    magic, totalsize, off_struct, off_strings, off_rsvmap = struct.unpack_from('>5I', d, 0)
    if magic != 0xd00dfeed:
        raise ValueError('not a flattened device tree: magic %08x' % magic)
    len_strings, len_struct = struct.unpack_from('>2I', d, 0x20)
    structs = d[off_struct:off_struct + len_struct]
    strings = d[off_strings:off_strings + len_strings]
    out = {}
    path = []
    pos = 0

    def sname(offset):
        return strings[offset:strings.index(b'\0', offset)].decode('utf-8', 'replace')

    while pos + 4 <= len(structs):
        tok = struct.unpack_from('>I', structs, pos)[0]
        pos += 4
        if tok == NOP:
            continue
        if tok == BEGIN_NODE:
            if pos >= len(structs):
                break
            if structs[pos] == 0:
                # The root node's name is the empty string: one NUL byte, then padding.
                # Leaving pos on the NUL reads the padding as a token and the walk dies
                # on "unknown FDT token 0" - which is a parser bug, not a broken image.
                path.append('')
                pos += 1
            else:
                nend = structs.index(b'\0', pos)
                path.append(structs[pos:nend].decode('utf-8', 'replace'))
                pos = nend + 1
            pos += (4 - pos % 4) % 4
        elif tok == END_NODE:
            if path:
                path.pop()
        elif tok == PROP:
            plen, pstr = struct.unpack_from('>2I', structs, pos)
            pos += 8
            data = structs[pos:pos + plen]
            out['%s:%s' % ('/'.join(path) or '/', sname(pstr))] = data
            pos += (plen + 3) & ~3
        elif tok == FDT_END:
            break
        else:
            raise ValueError('unknown FDT token %d at %d' % (tok, pos))
    return out


def kernel_image(d):
    """Return the uncompressed kernel payload of a FIT: the kernel-1 image's data."""
    props = fdt_props(d)
    names = [k for k in props if k.startswith('/images/') and k.endswith(':data')]
    if not names:
        raise ValueError('no /images/*:data property - not a FIT with an inline image')
    kernel = [n for n in names if '/kernel' in n] or names
    blob = props[kernel[0]]
    comp = props.get(kernel[0].rsplit(':', 1)[0] + ':compression', b'gzip\0')
    if comp.split(b'\0')[0] == b'gzip':
        blob = zlib.decompressobj(47).decompress(blob)
    elif blob[:2] == b'\x1f\x8b':
        blob = gzip.decompress(blob)
    return kernel[0], blob


def newc_records(blob, limit=400000):
    """Yield (name, mode, data) for every newc record, stopping at the trailer.

    A header is only trusted if all 13 fields are hex: the same 070701 byte string
    occurs inside compressed firmware, and a record that parses there wanders for
    thousands of "files" before it fails. That is the false positive this filter is
    for, and it is why a caller may ask for a path and get a clean miss instead of a
    plausible-looking one.
    """
    head_re = re.compile(rb'^070701[0-9a-fA-F]{104}$')
    pos = 0
    n = 0
    while n < limit:
        head = blob[pos:pos + 110]
        if not head_re.match(head):
            return
        f = [head[6 + 8 * i:14 + 8 * i] for i in range(NEWC_FIELDS)]
        mode = int(f[1], 16)
        datasize = int(f[6], 16)
        namesize = int(f[11], 16)
        if datasize > 64 * 1024 * 1024 or not 0 < namesize <= 4096:
            return
        nstart = pos + 110
        name = blob[nstart:nstart + namesize].rstrip(b'\0').decode('utf-8', 'replace')
        dstart = (nstart + namesize + 3) & ~3
        if name == 'TRAILER!!!':
            yield name, mode, b''
            return
        yield name, mode, blob[dstart:dstart + datasize]
        pos = (dstart + datasize + 3) & ~3
        n += 1


def find_initramfs(image):
    """Locate the newc archive inside a kernel image.

    OpenWrt can ship the initramfs as a gzip member whose payload is a cpio, or as an
    uncompressed cpio, at an offset nobody records - so every candidate is walked and
    only one that reaches the trailer with a plausible entry count is believed.
    """
    cands = []
    for m in re.finditer(re.escape(b'\x1f\x8b\x08'), image):
        try:
            raw = zlib.decompressobj(47).decompress(image[m.start():])
        except zlib.error:
            continue
        if raw[:6] == NEWC_MAGIC:
            cands.append(('gzip@%x' % m.start(), raw))
    for m in re.finditer(re.escape(NEWC_MAGIC), image):
        cands.append(('raw@%x' % m.start(), image[m.start():]))
    best = None
    for label, blob in cands:
        recs = list(newc_records(blob))
        if not recs or recs[-1][0] != 'TRAILER!!!':
            continue
        if best is None or len(recs) > len(best[1]):
            best = (label, recs)
    return best


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument('itb')
    ap.add_argument('paths', nargs='*', help='member names inside the initramfs, no leading /')
    ap.add_argument('--expect', action='append', default=[], metavar='PATH=SHA256',
                    help='assert a member is byte-exact; repeatable, and a mismatch exits 1')
    a = ap.parse_args()

    d = open(a.itb, 'rb').read()
    which, image = kernel_image(d)
    found = find_initramfs(image)
    if not found:
        print('FAIL no newc payload with a trailer inside %s (%d bytes, kernel %s)' %
              (a.itb, len(image), which))
        return 1
    label, recs = found
    print('%s: %s -> %d records (trailer reached)' % (a.itb, label, len(recs) - 1))
    by_name = {}
    for name, mode, data in recs[:-1]:
        by_name.setdefault(name, (mode, data))

    want = list(a.paths) + [p.split('=')[0] for p in a.expect]
    rc = 0
    for name in want:
        if name not in by_name:
            print('MISSING %s' % name)
            rc = 1
            continue
        mode, data = by_name[name]
        digest = hashlib.sha256(data).hexdigest()
        print('%-44s mode=%05o %9d bytes  sha256=%s' % (name, mode, len(data), digest))
    for spec in a.expect:
        name, _, sha = spec.partition('=')
        if name not in by_name:
            print('FAIL %s is not in the kit at all' % name)
            rc = 1
            continue
        got = hashlib.sha256(by_name[name][1]).hexdigest()
        if got == sha:
            print('PASS %s is byte-exact (%s)' % (name, got))
        else:
            print('FAIL %s: kit has %s, expected %s' % (name, got, sha))
            rc = 1
    if not want:
        zrec = [n for n in by_name if 'zrec' in n]
        print('members mentioning zrec: %d%s' % (len(zrec), (' -> ' + ', '.join(sorted(zrec))) if zrec else ''))
    return rc


if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    sys.exit(main())
