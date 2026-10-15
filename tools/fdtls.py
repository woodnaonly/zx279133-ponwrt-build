#!/usr/bin/env python3
"""Walk an FDT blob and print the partition table it actually carries.

No dtc on this PC, and the layout about to be flashed has to be read from the artifact,
not from the DTS that was supposed to produce it: U-Boot hands the new system
`root=/dev/mtdblock5`, so mainline's own table must make `root` both index 5 and the
offset where the jffs2 image will really sit.

  python tools/fdtls.py <dtb> [compatible-substring]
"""
import struct
import sys

F_BEGIN_NODE, F_END_NODE, F_PROP, F_NOP, F_END = 1, 2, 3, 4, 5


def human(n):
    for u, s in ((1 << 20, "M"), (1 << 10, "k")):
        if n and n % u == 0:
            return "%d%s" % (n // u, s)
    return hex(n)


class FDT:
    def __init__(self, blob):
        if blob[:4] != b"\xd0\x0d\xfe\xed":
            sys.exit("not an FDT blob")
        # header order per the spec: magic totalsize off_struct off_rsvmap off_strings
        # version last_comp boot_cpuid size_strings size_struct  (the last two are easy to
        # transpose, and transposing them makes the walk stop after one node)
        (self.totalsize, self.off_struct, self.off_rsvmap, self.off_strings, _version,
         _last, _cpuid, self.size_strings, self.size_struct) = struct.unpack(">9I", blob[4:40])
        self.blob = blob
        self.strs = blob[self.off_strings:self.off_strings + self.size_strings]

    def parse(self):
        """Return a list of (path, {prop: raw bytes}) for every node."""
        b, out, stack, props = self.blob, [], [], {}
        p = self.off_struct
        end = self.off_struct + self.size_struct
        while p < end:
            token, = struct.unpack(">I", b[p:p + 4]); p += 4
            if token == F_NOP:
                continue
            if token == F_END:
                break
            if token == F_BEGIN_NODE:
                e = b.index(b"\0", p)
                name = b[p:e].decode()
                p = (e + 4) & ~3
                path = "/" + "/".join(x for x in stack + [name.split("@")[0]] if x)
                full = "/" + "/".join(x for x in stack + [name] if x)
                node = (full, path, {})
                out.append(node)
                stack.append(name)
                props[id(node)] = node[2]
                continue
            if token == F_END_NODE:
                stack.pop()
                continue
            if token == F_PROP:
                sz, nameoff = struct.unpack(">II", b[p:p + 8]); p += 8
                e = self.strs.index(b"\0", nameoff)
                name = self.strs[nameoff:e].decode()
                data = bytes(b[p:p + sz]); p = (p + sz + 3) & ~3
                out[-1][2][name] = data
                continue
            sys.exit("unknown token %d at %d" % (token, p))
        return out


def cell_vals(props, node_path, n, default):
    v = props.get(n)
    return struct.unpack(">I", v[:4])[0] if v else default


def main():
    fn = sys.argv[1]
    want = sys.argv[2] if len(sys.argv) > 2 else "fixed-partitions"
    f = FDT(open(fn, "rb").read())
    nodes = f.parse()
    hits = [n for n in nodes if want in n[2].get("compatible", b"").decode(errors="replace")]
    print("%s: %d nodes, %d container(s) with compatible ~ %s" %
          (fn, len(nodes), len(hits), want))
    for full, path, props in hits:
        par = [n for n in nodes if full.rsplit("/", 1)[0] == n[1] or n[1] == path.rsplit("/", 1)[0]]
        ac = cell_vals(props, path, "#address-cells", 2)
        sc = cell_vals(props, path, "#size-cells", 2)
        print("  container %s  ac=%d sc=%d" % (full, ac, sc))
        idx = 0
        for n2 in nodes:
            if n2[1].startswith(path + "/") and n2[1].count("/") == path.count("/") + 1:
                reg = n2[2].get("reg", b"")
                vals = []
                off = 0
                for c in [ac, sc]:
                    for _ in range(c):
                        vals.append(int.from_bytes(reg[off:off + 4], "big")); off += 4
                label = n2[2].get("label", b"").split(b"\0")[0].decode(errors="replace")
                name = n2[2].get("name", b"").split(b"\0")[0].decode(errors="replace")
                start, size = vals[-2], vals[-1]
                print("    mtdblock%-2d %-22s off=0x%08x size=%-8s label=%-14s %s" %
                      (idx, n2[0].split("/")[-1], start, human(size), label or name,
                       n2[2].get("compatible", b"").split(b"\0")[0].decode(errors="replace")))
                idx += 1
    root_idx = None
    for full, path, props in hits:
        idx = 0
        for n2 in nodes:
            if n2[1].startswith(path + "/") and n2[1].count("/") == path.count("/") + 1:
                lab = n2[2].get("label", b"").split(b"\0")[0].decode(errors="replace")
                nm = n2[2].get("name", b"").split(b"\0")[0].decode(errors="replace")
                if lab == "root" or nm == "root":
                    root_idx = idx
                idx += 1
    print("  => `root` is mtdblock index %s   (U-Boot passes root=/dev/mtdblock5)" % root_idx)


main()
