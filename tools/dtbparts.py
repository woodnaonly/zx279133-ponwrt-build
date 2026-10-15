#!/usr/bin/env python3
"""Print the mtd partition table a .dtb actually contains.

Why this exists as a gate rather than a convenience: /sbin/mtd on the running 4.19 system
can only program a *whole partition by label*, while U-Boot hard-codes root=/dev/mtdblock5.
So a partition table that is wrong by one erase block writes the jffs2 where the new kernel
will not look for it - a panic with no serial to read it. Sizes and offsets therefore have to
be read off the artifact that is about to be flashed, not off the DTS we think we wrote.

The index printed per partition is the one forced by an `aliases { mtdN = <&part> }` entry.
Without such a pin the index is only node order, and node order is off by one whenever
CONFIG_MTD_PARTITIONED_MASTER registers the master as mtd0 - so the board dts pins all six.

Walking tokens is the whole job: BEGIN_NODE(name) / END_NODE / PROP(len,nameoff) / NOP / END,
every block padded up to a 4-byte boundary, strings looked up in the separate string block.

	python tools/dtbparts.py staging/trial/flash-dtb.bin [expect-label=off,size ...]
Exit 0 and "TABLE OK" only when every expectation matches.
"""
import gzip
import struct
import sys

FDT_BEGIN_NODE, FDT_END_NODE, FDT_PROP, FDT_NOP = 1, 2, 3, 4
FDT_END = 9  # not 5 - libfdt reserves 5..8 and ends the struct block with 0x00000009


def walk(dtb):
    (magic, totalsize, off_struct, off_strings, _rsv, _ver, _lc, _bcp,
     sz_strings, sz_struct) = struct.unpack(">10I", dtb[:40])
    assert magic == 0xD00DFEED, "not a dtb (magic %08x)" % magic
    assert off_struct + sz_struct <= len(dtb), "struct block runs past the file"
    strings = dtb[off_strings:off_strings + sz_strings]
    end = off_struct + sz_struct

    def s(off):
        return strings[off:strings.index(b"\0", off)].decode("latin1")

    path, depth, nodes = [], 0, []
    p = off_struct
    while p < end:
        token, = struct.unpack(">I", dtb[p:p + 4])
        p += 4
        if token == FDT_BEGIN_NODE:
            q = dtb.index(b"\0", p)
            name = dtb[p:q].decode("latin1")
            p = (q + 4) & ~3
            segs = [x.split("@")[0] for x in path] + [name.split("@")[0]]
            full = "/" + "/".join(segs)
            # The same path *with* unit addresses, which is what an /aliases entry's value is:
            # dtc encodes a reference as the target node's full name string, not a phandle.
            # The root node's name is empty, so it must not contribute a leading slash.
            real = "/" + "/".join([x for x in path + [name] if x])
            nodes.append({"path": full, "real": real, "name": name, "props": {}})
            path.append(name)
        elif token == FDT_END_NODE:
            path.pop()
        elif token == FDT_PROP:
            ln, nameoff = struct.unpack(">II", dtb[p:p + 8])
            p += 8
            val = dtb[p:p + ln]
            p = (p + ln + 3) & ~3
            nodes[-1]["props"][s(nameoff)] = val
        elif token == FDT_NOP:
            pass
        elif token == FDT_END:
            break
        else:
            raise AssertionError("bad token %d at %d" % (token, p - 4))
    return nodes


def alias_pins(nodes):
    """{index into nodes: /dev/mtdN} from an `aliases` node with `mtdN = <&part>` entries.

    mtdcore.c's add_mtd_device() does `ofidx = of_alias_get_id(np, "mtd")` and, when that is
    >= 0, allocates exactly that index instead of the next free one - so a pinned partition's
    number no longer depends on how many siblings precede it or on whether
    CONFIG_MTD_PARTITIONED_MASTER registers the master as mtd0. ofpart hands each partition
    its own DT node (parts[i].of_node = pp), which is what makes the pin apply to partitions
    rather than only to masters. The reference is matched by the node's full path, because
    that is how dtc writes it: an /aliases value is the target node's name string
    (`/soc/spi@10d0f000/flash@0/partitions/partition@f00000`), not a phandle, and the kernel
    resolves aliases with the same comparison. A first version of this function looked only
    for a `phandle` property and so reported a correctly pinned board file as unpinned;
    phandle matching is kept for blobs built some other way.

    Keyed by position in `nodes`: `path` drops unit addresses, so every `partition@xxx`
    sibling would otherwise look like the same node.
    """
    byph = {}
    for i, n in enumerate(nodes):
        for k in ("phandle", "linux,phandle"):
            v = n["props"].get(k)
            if v and len(v) == 4:
                byph[struct.unpack(">I", v)[0]] = i
    bypath = {}
    for i, n in enumerate(nodes):
        bypath.setdefault(n["real"], i)
    pins = {}
    for a in nodes:
        if a["name"].split("@")[0] != "aliases":
            continue
        for k, v in a["props"].items():
            if not (k.startswith("mtd") and k[3:].isdigit()):
                continue
            if len(v) == 4:
                idx = byph.get(struct.unpack(">I", v)[0])
            else:
                idx = bypath.get(v.split(b"\0")[0].decode("latin1"))
            if idx is not None:
                pins[idx] = int(k[3:])
    return pins


def _reg(val):
    """A (base, size) pair from a reg property, 1+1 or 2+2 cells."""
    c = list(struct.unpack(">%dI" % (len(val) // 4), val))
    if len(c) >= 4:
        return ((c[0] << 32) | c[1], (c[2] << 32) | c[3])
    if len(c) == 2:
        return (c[0], c[1])
    return None


CARVE = []          # [(base, size, no-map, name)] from /reserved-memory
RAM = []            # [(base, size)] from the device_type = "memory" nodes


def reserved_map(nodes, seq):
    """Report the DRAM carveouts and check them against what the installed U-Boot uses.

    `bootcmd` is `mtd read kernel 0x88000000; mtd read dtb 0x88800000; bootm 0x88000000 -
    0x88800000`, so those two addresses are where the images physically land, and ih_load in
    the uImage header is where arm64 bootm puts the running kernel. A range the dtb marks
    `no-map` is one the kernel will not use, and this has already bitten the project once
    (fe466ce: the ramoops address sat inside where the loader had just placed the kernel).
    """
    problems = 0
    for n in nodes:
        if n["name"].split("@")[0] == "memory" and "device_type" in n["props"]:
            r = _reg(n["props"].get("reg", b""))
            if r:
                RAM.append(r)
    rm = [n for n in nodes if n["name"].split("@")[0] == "reserved-memory"]
    if not rm:
        print("no reserved-memory node: the whole of DRAM is usable")
        return 0
    parent = rm[0]["path"] + "/"
    for n in nodes:
        p = n["path"]
        if not p.startswith(parent) or p.count("/") != parent.count("/"):
            continue
        r = _reg(n["props"].get("reg", b""))
        if not r:
            continue
        nomap = "no-map" in n["props"]
        CARVE.append((r[0], r[1], nomap, n["name"]))
        print("  carveout  %-28s 0x%09x..0x%09x (%d MiB) %s" %
              (n["name"], r[0], r[0] + r[1], r[1] // (1 << 20), "no-map" if nomap else "mapped"))
    for b, s in RAM:
        print("  memory    0x%09x..0x%09x (%d MiB usable if nothing is carved out)" %
              (b, b + s, s // (1 << 20)))
    for label, addr, size in (("bootcmd kernel read", 0x88000000, 8 << 20),
                              ("bootcmd dtb read", 0x88800000, 1 << 20)):
        bad_here = [c for c in CARVE if c[2] and not (addr + size <= c[0] or addr >= c[0] + c[1])]
        print("  %-22s 0x%09x + %d B: %s" % (label, addr, size,
              "CLASH with " + ",".join(b[3] for b in bad_here) if bad_here else "clear"))
        problems += len(bad_here)
    return problems


def check_uimage(path):
    """ih_load..ih_load+Image size against the carveouts - the placement bootm honours."""
    u = open(path, "rb").read()
    magic, _h, _t, dsize, load, ep, _d = struct.unpack(">7I", u[:28])
    if magic != 0x27051956:
        print("  uimage    %s is not a legacy uImage (%08x) - not checked" % (path, magic))
        return 0
    body = u[64:64 + dsize] if dsize else u[64:]
    try:
        img = gzip.decompress(body)
        span = struct.unpack("<2Q", img[0x08:0x18])   # text_offset, image_size
        need = span[0] + span[1]
    except Exception:
        need = dsize
    print("  uimage    ih_load=0x%x ih_ep=0x%x, arm64 Image needs %d B there" % (load, ep, need))
    hit = [c for c in CARVE if c[2] and not (load + need <= c[0] or load >= c[0] + c[1])]
    inram = any(load >= b and load + need <= b + s for b, s in RAM) if RAM else None
    print("  kernel placement 0x%09x..0x%09x: %s%s" % (
        load, load + need,
        "CLASH with " + ",".join(h[3] for h in hit) if hit else "clear of the no-map ranges",
        "" if inram is None else ("; inside the memory node" if inram else "; OUTSIDE the memory node")))
    return len(hit) + (0 if inram is not False else 1)


def main():
    dtb = open(sys.argv[1], "rb").read()
    nodes = walk(dtb)
    pins = alias_pins(nodes)
    parts = [i for i, n in enumerate(nodes) if n["name"].startswith("partition@")]
    if not parts:
        print("no partition@ nodes found (checked %d nodes)" % len(nodes))
        return 2
    bad, seq, unpinned = 0, [], 0
    for i, n in enumerate([nodes[j] for j in parts]):
        reg = n["props"].get("reg", b"")
        label = n["props"].get("label", b"?\0").split(b"\0")[0].decode()
        # #address-cells/#size-cells are 2/2 in this tree, so each value is two BE 32-bit
        # cells; the high cell is zero for every offset and size on this 256 MiB part.
        cells = list(struct.unpack(">%dI" % (len(reg) // 4), reg))
        if len(cells) == 4:
            off, size = (cells[0] << 32) | cells[1], (cells[2] << 32) | cells[3]
        elif len(cells) == 2:
            off, size = cells
        else:
            off, size = -1, -1
        idx = pins.get(parts[i], i)
        if parts[i] not in pins:
            unpinned += 1
        seq.append((label, off, size, idx))
        print("%-16s @0x%08x  len=0x%08x (%d MiB)  %s" %
              (label, off, size, size // (1 << 20), n["name"]))
    print("index order: " + " ".join("%d:%s" % (d, l) for l, _, _, d in seq))
    if unpinned:
        print("  %d of %d partitions are NOT pinned by an mtdN alias: for those the index is"
              " node order, which is off by one if CONFIG_MTD_PARTITIONED_MASTER is set"
              % (unpinned, len(parts)))
    else:
        print("  every partition's index is pinned by an alias - node order cannot shift it")
    got = {}
    for l, o, s, _d in seq:
        got.setdefault(l, (o, s))
    # Unconditional, because this is the constraint a reordered node list breaks in silence:
    # the installed U-Boot always boots with root=/dev/mtdblock5.
    roots = [r for r in seq if r[0] == "root"]
    if roots:
        i = roots[0][3]
        print("root is /dev/mtdblock%d - U-Boot's bootargs hard-code 5, so %s" %
              (i, "OK" if i == 5 else "THIS IMAGE PANICS ON THE ROOT MOUNT"))
        if i != 5:
            bad += 1

    bad += reserved_map(nodes, seq)

    for spec in sys.argv[2:]:
        want, val = spec.split("=", 1)
        if want == "uimage":
            # The kernel's own placement comes from the image header, not from the dtb, so it
            # is checked here rather than assumed: arm64 bootm honours ih_load, and a load
            # address that the dtb marks `no-map` is a region the kernel will refuse to use.
            bad += check_uimage(val)
            continue
        off_s, size_s = val.split(",")
        exp = (int(off_s, 0), int(size_s, 0))
        act = got.get(want)
        if act != exp:
            print("  MISMATCH %s: want 0x%x/0x%x, dtb has %s" %
                  (want, exp[0], exp[1], ("0x%x/0x%x" % act) if act else "no such label"))
            bad += 1
    if bad:
        print("TABLE WRONG - do not flash this dtb")
        return 1
    print("TABLE OK" + (" - all %d expectations matched" % (len(sys.argv) - 2) if len(sys.argv) > 2 else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
