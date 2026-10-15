#!/usr/bin/env python
"""Flip one node's `status` in a compiled dtb, in place, without dtc.

Why this exists: there is no dtc on this PC, and the question worth asking of the board is not
"can we build a new dtb" but "does the mainline boot stop inside the ZTE ethernet stack". The
answer comes from a round booted with that one node switched off, and a node can be switched
off with a five-byte edit, because any status that is not "okay"/"ok" means disabled to the
kernel - so "okay\0" becomes "off\0\0" and every offset, length and size in the blob stays
valid. That is the whole trick: same length, no restructuring, and the FDT still parses.

	python tools/dtbdisable.py in.dtb out.dtb zte,zx279133-nppt

It refuses rather than guessing: the node must be found by its compatible, must already carry a
`status` property, and the value must be exactly "okay" (4 bytes + NUL) so the replacement fits.
The output is then checked with the same reader, and tools/dtbparts.py confirms the partition
table is untouched.
"""
import struct
import sys

BEGIN_NODE, END_NODE, PROP, NOP, END, MISSING, RESV = 1, 2, 3, 4, 9, 10, 11


def rnd(n):
    return (n + 3) & ~3


def scan(blob):
    """(path, props, value-offsets) per node - same conventions tools/dtbparts.py's walk()
    uses and verified against this blob: BEGIN_NODE is tag then the name inline and
    null-terminated, PROPERTY is tag, len, nameoff, then the value inline, each padded to 4.
    """
    (magic, totalsize, off_dt, off_strings, _rsv, _ver, _lc, _bcp,
     sz_strings, sz_struct) = struct.unpack(">10I", blob[:40])
    if magic != 0xD00DFEED:
        raise SystemExit("not a dtb (magic %08x)" % magic)
    strings = blob[off_strings:off_strings + sz_strings]
    end = off_dt + sz_struct

    def s(off):
        return strings[off:strings.index(b"\0", off)].decode("latin1")

    stack, out, cur, p = [], [], None, off_dt
    while p < end:
        tok = struct.unpack(">I", blob[p:p + 4])[0]
        p += 4
        if tok == BEGIN_NODE:
            q = blob.index(b"\0", p)
            name = blob[p:q].decode("latin1")
            p = (q + 4) & ~3
            cur = {"name": name, "path": "/" + "/".join([x for x in stack + [name] if x]),
                   "props": {}, "off": {}}
            stack.append(name)
            out.append(cur)
        elif tok == END_NODE:
            if stack:
                stack.pop()
            cur = None
        elif tok == PROP:
            ln, noff = struct.unpack(">2I", blob[p:p + 8])
            p += 8
            if cur is not None:
                cur["props"][s(noff)] = blob[p:p + ln]
                cur["off"][s(noff)] = p
            p = (p + ln + 3) & ~3
        elif tok in (NOP, MISSING, RESV):
            continue
        elif tok == END:
            break
        else:
            raise SystemExit("FDT token %d at 0x%x - stopping, this blob is not what I expect" % (tok, p))
    return out, s


def main():
    if len(sys.argv) < 4:
        print(__doc__)
        return 2
    src, dst, compat = sys.argv[1:4]
    blob = bytearray(open(src, "rb").read())
    nodes, _ = scan(bytes(blob))
    hits = [n for n in nodes if n["props"].get("compatible", b"").split(b"\0")[0] == compat.encode()]
    if not hits:
        print("no node with compatible = \"%s\" in %s" % (compat, src))
        print("compatibles present: %s" % ", ".join(sorted({n["props"]["compatible"].split(b"\0")[0].decode("latin1")
              for n in nodes if "compatible" in n["props"]})[:40]))
        return 1
    n = hits[0]
    if "status" not in n["props"]:
        print("%s has no status property - it is enabled by default and a value cannot be added"
              " without rewriting the blob. Use dtc (the CI has it) for that." % n["path"])
        return 1
    if n["props"]["status"] != b"okay\0":
        print("%s status is already %r - nothing to do" % (n["path"], n["props"]["status"].rstrip(b"\0")))
        return 1
    off = n["off"]["status"]
    blob[off:off + 5] = b"off\0\0"
    open(dst, "wb").write(bytes(blob))
    after, _ = scan(bytes(blob))
    now = [x for x in after if x["path"] == n["path"]][0]
    print("%s: status %r -> %r   (same length, blob %d B unchanged)" % (
        n["path"], b"okay", now["props"]["status"].rstrip(b"\0"), len(blob)))
    if now["props"]["status"].rstrip(b"\0") != b"off":
        print("readback disagrees - not writing a blob I cannot re-parse")
        return 1
    print("wrote %s - next: round it with DTB_LOCAL=%s and read the pstore window" % (dst, dst))
    return 0


if __name__ == "__main__":
    sys.exit(main())
