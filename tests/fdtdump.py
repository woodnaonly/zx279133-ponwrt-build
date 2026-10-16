#!/usr/bin/env python3
# 本地排演套件（local rehearsal suite）：依赖 staging/ 下的真实构建产物，
# 在只 clone 本仓库的环境里跑不起来，也**没有**接进 CI。
# 为什么、怎么跑、缺的那个夹具从哪来：同目录 README.md。
"""Minimal Flat Device Tree parser.

Written because dtc is not installed in this WSL distro and apt has been
unreliable. The FDT blob format is fully specified and self-describing
(magic 0xd00dfeed, version, totalsize, off_dt_strings, ...), so a parser can be
validated against known-good dtc output before being trusted.

Two validation gates before we use it on the unknown blob:
  1. structural: magic/version/total size/off_* fields are consistent and the
     strings block is in range;
  2. cross-check: parse ci-build-repo/decompiled/*.dtb and compare the node
     /property skeleton we emit against the dtc-produced .dts text.
"""
import struct
import sys

FDT_MAGIC = 0xD00DFEED
FDT_BEGIN_NODE = 1
FDT_END_NODE = 2
FDT_PROP = 3
FDT_NOP = 4
FDT_END = 9

TYPE_NAMES = {1: "u8", 2: "u16", 3: "u32", 4: "u64", 5: "string-list"}


class Fdt:
    def __init__(self, blob):
        self.b = blob
        (self.magic, self.totalsize, self.off_dt_struct, self.off_dt_strings,
         self.off_mem_rsvmap, self.version, self.last_comp_version,
         self.boot_cpuid_phys, self.size_dt_strings, self.size_dt_struct) = \
            struct.unpack(">10I", blob[:40])
        self._validate()
        self.strings = blob[self.off_dt_strings:
                            self.off_dt_strings + self.size_dt_strings]
        self.reserved = self._rsvmap()

    def _validate(self):
        assert self.magic == FDT_MAGIC, "bad magic 0x%08x" % self.magic
        assert self.version >= 16, "version %d too old" % self.version
        assert self.last_comp_version <= 17
        assert self.totalsize <= len(self.b), "totalsize %d > blob %d" % (
            self.totalsize, len(self.b))
        assert self.off_dt_struct + self.size_dt_struct <= len(self.b)
        assert self.off_dt_strings + self.size_dt_strings <= len(self.b)
        assert self.off_mem_rsvmap < len(self.b)

    def _rsvmap(self):
        out, off = [], self.off_mem_rsvmap
        while True:
            addr, size = struct.unpack(">QQ", self.b[off:off + 16])
            off += 16
            if addr == 0 and size == 0:
                return out
            out.append((addr, size))

    def s(self, off):
        end = self.strings.index(b"\0", off)
        return self.strings[off:end].decode("utf-8", "replace")

    def walk(self):
        """Yield (path, is_node) in document order; properties handled separately."""
        off, path = self.off_dt_struct, []
        depth = 0
        while True:
            (tok,) = struct.unpack(">I", self.b[off:off + 4])
            off += 4
            if tok == FDT_BEGIN_NODE:
                end = self.b.index(b"\0", off)
                name = self.b[off:end].decode("utf-8", "replace")
                off = (end + 4) & ~3
                path.append(name)
                yield ("/".join(path), True)
                depth += 1
            elif tok == FDT_END_NODE:
                path.pop()
                depth -= 1
                yield ("/".join(path), False)
            elif tok == FDT_PROP:
                plen, nameoff = struct.unpack(">II", self.b[off:off + 8])
                off += 8
                yield ("/".join(path), False)
                off = (off + plen + 3) & ~3
            elif tok == FDT_NOP:
                continue
            elif tok == FDT_END:
                return
            else:
                raise ValueError("bad token %d at %d" % (tok, off - 4))

    def props_of_node(self, node_path):
        """Return [(name, raw_bytes)] for one node. Root is spelled "/"."""
        node_path = node_path.rstrip("/")   # root node's own name is ""
        off, path, found = self.off_dt_struct, [], []
        while True:
            (tok,) = struct.unpack(">I", self.b[off:off + 4])
            off += 4
            if tok == FDT_BEGIN_NODE:
                end = self.b.index(b"\0", off)
                path.append(self.b[off:end].decode("utf-8", "replace"))
                off = (end + 4) & ~3
            elif tok == FDT_END_NODE:
                path.pop()
            elif tok == FDT_PROP:
                plen, nameoff = struct.unpack(">II", self.b[off:off + 8])
                off += 8
                raw = self.b[off:off + plen]
                if "/".join(path).rstrip("/") == node_path:
                    found.append((self.s(nameoff), raw))
                off = (off + plen + 3) & ~3
            elif tok == FDT_NOP:
                continue
            elif tok == FDT_END:
                return found
            else:
                raise ValueError("bad token %d" % tok)

    def node_paths(self):
        return [p for p, is_node in self.walk() if is_node]


def show(raw):
    if not raw:
        return '(empty)'
    if raw[-1:] == b"\0" and b"\0" not in raw[:-1]:
        return '"%s"' % raw[:-1].decode("utf-8", "replace")
    if len(raw) % 4 == 0 and len(raw) <= 32:
        vals = struct.unpack(">%dI" % (len(raw) // 4), raw)
        return "<" + " ".join(str(v) for v in vals) + ">"
    return raw[:48].hex() + ("..." if len(raw) > 48 else "")


if __name__ == "__main__":
    path = sys.argv[1]
    f = Fdt(open(path, "rb").read())
    print("file            %s" % path)
    print("version         %d (last_comp %d)" % (f.version, f.last_comp_version))
    print("totalsize       %d  (blob %d)" % (f.totalsize, len(f.b)))
    print("size_dt_struct  %d" % f.size_dt_struct)
    print("size_dt_strings %d" % f.size_dt_strings)
    print("mem reserve     %s" % (f.reserved or "(none)"))
    print("node count      %d" % len(f.node_paths()))
    if len(sys.argv) > 2:
        want = sys.argv[2]
        print("\n--- %s ---" % want)
        for name, raw in f.props_of_node(want):
            print("  %-28s %s" % (name, show(raw)))