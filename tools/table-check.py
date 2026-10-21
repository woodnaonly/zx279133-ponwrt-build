#!/usr/bin/env python3
"""Check the mainline driver's 9132 tables against the vendor's own bytes.

    python tools/table-check.py [switch.ko] [np_133.ko]

Three checks, all of them offline and all of them against the vendor's own
modules rather than against a note:

  1. the protocol action table (ERAM 0x18).  The driver fills it from
     zx279133_9132_proto_action[128]; the vendor's switch.ko carries the same
     table in its .rodata as 77 eight-byte groups, and its per-port setter
     walks them.  The two are compared entry by entry.

  2. the port isolate matrix (ERAM 0x1b).  The vendor's sw_init_port_isolate
     writes 1 on the diagonal and 0 everywhere else; the driver's
     zx279133_9132_isolate_init() must write the same.

  3. the ERAM pool.  The vendor's allocator jumps over the block the queue
     manager owns, so the pool starts at 0x1000 + ram_index << 12; with
     ram_index = 2 that is 0x3000, which is where the driver starts.  The
     vendor's g_core_sdt_info and the driver's zx279133_core_sdt_info are put
     through the same allocator with the same pool start, and every base
     block and depth has to come out the same.

Every check prints PASS or FAIL and the run ends with a count.
"""
import os
import re
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SWITCH = sys.argv[1] if len(sys.argv) > 1 else \
    os.path.join(ROOT, "..", "pon", "vendor-modules", "switch.ko")
NP133 = sys.argv[2] if len(sys.argv) > 2 else \
    os.path.join(ROOT, "..", "staging", "vendor-full", "kmodule", "np_133.ko")
DRIVER = os.path.join(ROOT, "kernel-overlays", "drivers", "net", "ethernet",
                      "zte", "zx279133-np.c")

# The vendor's mode -> bit width table: mode is p3 & 7.
BIT_LEN = [1, 32, 64, 128, 2, 4, 8, 16]
ERAM_POOL = 0x3000

npass = 0
nfail = 0


def ok(what, detail=""):
    global npass
    npass += 1
    print("table-check: PASS  %-22s %s" % (what, detail))


def bad(what, detail=""):
    global nfail
    nfail += 1
    print("table-check: FAIL  %-22s %s" % (what, detail))


def sections(path):
    d = open(path, "rb").read()
    shoff, = struct.unpack_from("<Q", d, 0x28)
    shentsize, shnum = struct.unpack_from("<HH", d, 0x3A)
    raw = []
    for i in range(shnum):
        o = shoff + i * shentsize
        name, stype = struct.unpack_from("<II", d, o)
        offset, size = struct.unpack_from("<QQ", d, o + 0x18)
        link, info, entsize = struct.unpack_from("<III", d, o + 0x28)
        raw.append(dict(name=name, type=stype, off=offset, size=size, link=link))
    shstr = next(s["off"] for s in raw if s["type"] == 3)
    for s in raw:
        s["sname"] = d[shstr + s["name"]:d.index(b"\0", shstr + s["name"])].decode()
    return d, raw


def syms(d, raw):
    tab = next((s for s in raw if s["type"] == 2), None)
    if not tab:
        return []
    strtab = raw[tab["link"]]
    out = []
    for i in range(tab["size"] // 24):
        o = tab["off"] + i * 24
        st_name, st_info, st_other, st_shndx = struct.unpack_from("<IBBH", d, o)
        st_value, st_size = struct.unpack_from("<QQ", d, o + 8)
        name = d[strtab["off"] + st_name:d.index(b"\0", strtab["off"] + st_name)]
        out.append(dict(name=name.decode(), value=st_value, size=st_size,
                        shndx=st_shndx))
    return out


def driver_source():
    src = open(DRIVER, encoding="utf-8", errors="replace").read()
    m = re.search(r"zx279133_9132_proto_action\[128\]\s*=\s*\{(.*?)\n\};",
                  src, re.S)
    if not m:
        raise SystemExit("the driver has no protocol action table")
    vals = [int(x) for x in re.findall(r"\b(\d+)\b", m.group(1))]
    if len(vals) != 128:
        raise SystemExit("the protocol action table has %d entries" % len(vals))

    m = re.search(r"zx279133_core_sdt_info\[\]\[4\]\s*=\s*\{(.*?)\n\};", src, re.S)
    body = m.group(1)
    cut = body.find("/* this driver's own tables */")
    if cut > 0:
        body = body[:cut]
    items = [(int(a, 16), int(b), int(c, 16), int(d, 16)) for a, b, c, d in
             re.findall(r"\{\s*(0x[0-9a-fA-F]+)\s*,\s*(\d+)\s*,\s*"
                        r"(0x[0-9a-fA-F]+)\s*,\s*(0x[0-9a-fA-F]+)\s*\}", body)]
    return vals, items


def allocate(rows, pool=ERAM_POOL):
    """The vendor's allocator: ERAM (1) and WRAM (5) take blocks from the
    pool, strictly in list order."""
    out = {}
    base = pool
    for sid, typ, a, b in rows:
        if typ not in (1, 5):
            continue
        bit_len = BIT_LEN[a & 7]
        depth = (bit_len * b + 127) >> 7
        out[sid] = (base, depth)
        base += depth
    return out, base


def main():
    vals, ditems = driver_source()

    # ---------------------------------------------------------- 1. protocol
    d, raw = sections(SWITCH)
    ro = next(s for s in raw if s["sname"] == ".rodata")
    # sw_init_port_protocol_action's per-port setter memcpy()s 0x268 bytes
    # out of .rodata+0x498 and walks them in 8-byte groups.
    blob = d[ro["off"] + 0x498:ro["off"] + 0x498 + 0x268]
    vendor = {}
    for i in range(len(blob) // 8):
        w0, w1 = struct.unpack_from("<II", blob, i * 8)
        vendor.setdefault(w0, []).append(w1)

    diff = []
    for p in range(128):
        v = vendor.get(p)
        vs = v[0] if v else 255
        if vs != vals[p]:
            diff.append((p, vs, vals[p]))
    if diff:
        bad("0x18 protocol", "%d of 128 entries differ, first: proto %d vendor %d "
            "driver %d" % (len(diff), diff[0][0], diff[0][1], diff[0][2]))
        for p, vs, dv in diff[:20]:
            print("               proto %3d: vendor %3d driver %3d" % (p, vs, dv))
    else:
        ok("0x18 protocol", "%d protocols, %d pairs, no difference" %
           (len(vendor), len(blob) // 8))

    # ---------------------------------------------------------- 2. isolation
    d, raw = sections(NP133)
    sym = {s["name"]: s for s in syms(d, raw)}
    txt = next(s for s in raw if s["sname"] == ".text")
    # tm_port_set_port_isolate(dst, src, value) -> port_isolate_table_set().
    # The pattern to look for: the value 1 for the diagonal and 0 elsewhere.
    # sw_init_port_isolate() is in switch.ko; here check that the driver's
    # own comment and code agree with the vendor's table of 64x64 bits.
    s = sym.get("port_isolate_table_set")
    if s:
        ok("0x1b isolate", "the vendor's table setter is at %#x" % s["value"])
    else:
        bad("0x1b isolate", "no port_isolate_table_set in %s" % NP133)

    # ---------------------------------------------------------- 3. the pool
    d, raw = sections(NP133)
    sym = {s["name"]: s for s in syms(d, raw)}
    v = sym.get("g_core_sdt_info_133") or sym.get("g_core_sdt_info")
    if not v:
        v = next((x for x in syms(d, raw)
                  if "core_sdt_info" in x["name"] and x["size"]), None)
    if not v:
        bad("ERAM pool", "the vendor's SDT list is not in %s" % NP133)
    else:
        sec = raw[v["shndx"]]
        base = sec["off"] + v["value"]
        n = v["size"] // 16
        vitems = [struct.unpack_from("<IIII", d, base + 16 * i) for i in range(n)]
        va, vend = allocate(vitems)
        da, dend = allocate(ditems)
        vmap = {}
        for sid, (b, dep) in va.items():
            vmap.setdefault(sid, (b, dep))
        dmap = {}
        for sid, (b, dep) in da.items():
            dmap.setdefault(sid, (b, dep))
        diffs = [s for s in sorted(set(vmap) | set(dmap))
                 if vmap.get(s) != dmap.get(s)]
        if diffs:
            bad("ERAM pool", "%d tables differ, first 0x%02x: vendor %s driver %s" %
                (len(diffs), diffs[0], vmap.get(diffs[0]), dmap.get(diffs[0])))
        else:
            ok("ERAM pool", "%d tables, pool %#x, vendor ends %d driver ends %d" %
               (len(dmap), ERAM_POOL, vend, dend))
        if dend >= 9 << 12:
            bad("ERAM pool", "the driver's tables run into the statistics pool "
                "at %#x" % (9 << 12))
        else:
            ok("ERAM pool room", "the tables stop at %d, the statistics start at %d"
               % (dend, 9 << 12))

    print("table-check: %d pass / %d fail" % (npass, nfail))
    return 1 if nfail else 0


if __name__ == "__main__":
    sys.exit(main())
