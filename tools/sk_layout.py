#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Strict read-only SK-D840N layout validation, cross-tested with the target C tool."""
import struct

MAX_DTB = 1048576
LABELS = ("boot", "kernel", "dtb", "parameter tags", "root", "scratch")
OFFSETS = (0, 0x200000, 0xa00000, 0xb00000, 0xf00000, 0xff00000)
SIZES = (0x200000, 0x800000, 0x100000, 0x400000, 0, 0x100000)


def require(condition, message):
    if not condition:
        raise ValueError("DTB layout: " + message)


def parse_tree(blob):
    require(40 <= len(blob) <= MAX_DTB, "invalid file size")
    magic, total, start, strings, reserve, version, compat, _, nstrings, nstruct = struct.unpack(">10I", blob[:40])
    require(magic == 0xd00dfeed and version == 17 and 16 <= compat <= 17, "invalid header/version")
    require(40 <= total <= len(blob) and all(x in (0, 255) for x in blob[total:]), "invalid totalsize/padding")
    require(start % 4 == 0 and reserve % 8 == 0, "unaligned blocks")
    regions = [(0, 40), (start, nstruct), (strings, nstrings)]
    require(all(0 <= off <= total and 0 <= size <= total - off for off, size in regions), "block outside DTB")
    pos = reserve
    while True:
        require(40 <= pos <= total - 16, "unterminated reservations")
        item = blob[pos:pos + 16]
        pos += 16
        if not any(item):
            break
    regions.append((reserve, pos - reserve))
    for i, (off, size) in enumerate(regions):
        for other, count in regions[i + 1:]:
            require(not (off < other + count and other < off + size), "overlapping blocks")
    names = blob[strings:strings + nstrings]
    pos, end, stack, nodes, child_started, ended = start, start + nstruct, [], {}, set(), False
    while pos + 4 <= end:
        tag, = struct.unpack_from(">I", blob, pos)
        pos += 4
        if tag == 1:
            nul = blob.find(b"\0", pos, end)
            require(nul >= pos, "unterminated node")
            name = blob[pos:nul]
            require(all(32 <= c < 127 and c != 47 for c in name), "invalid node name")
            require(bool(name) if stack else not name, "invalid root/child name")
            if stack:
                child_started.add(stack[-1])
            path = (stack[-1].rstrip("/") + "/" + name.decode("ascii")) if stack else "/"
            require(path not in nodes and (stack or not nodes), "duplicate node/root")
            require(len(stack) < 32 and len(path) < 512 and len(nodes) < 2048, "tree limit exceeded")
            nodes[path] = {"props": {}, "parent": stack[-1] if stack else None, "children": []}
            if stack:
                nodes[stack[-1]]["children"].append(path)
            stack.append(path)
            pos = (nul + 4) & ~3
        elif tag == 2:
            require(bool(stack), "unbalanced node")
            stack.pop()
        elif tag == 3:
            require(stack and stack[-1] not in child_started and pos + 8 <= end, "invalid property order")
            size, offset = struct.unpack_from(">II", blob, pos)
            pos += 8
            require(offset < len(names) and size <= end - pos, "property outside blocks")
            nul = names.find(b"\0", offset)
            require(nul > offset, "invalid property name")
            name = names[offset:nul].decode("ascii")
            props = nodes[stack[-1]]["props"]
            require(name not in props, "duplicate property")
            props[name] = blob[pos:pos + size]
            pos = (pos + size + 3) & ~3
        elif tag == 4:
            pass
        elif tag == 9:
            require(not stack and nodes and not any(blob[pos:end]), "invalid structure end")
            ended = True
            break
        else:
            raise ValueError("DTB layout: unknown structure token")
        require(pos <= end, "structure overrun")
    require(ended, "missing structure end")
    return nodes


def string_list(value):
    require(value and value[-1:] == b"\0", "invalid string list")
    values = value[:-1].split(b"\0")
    require(all(values), "empty string list entry")
    return [v.decode("ascii") for v in values]


def inspect_layout(blob):
    nodes = parse_tree(blob)
    require(string_list(nodes["/"]["props"].get("compatible", b"")) ==
            ["skyworth,sk-d840n", "zte,zx279133"], "wrong board")
    args = nodes.get("/chosen", {}).get("props", {}).get("bootargs", b"")
    require(args[-1:] == b"\0" and all(32 <= x < 127 for x in args[:-1]), "invalid bootargs")
    words = args[:-1].decode("ascii").split(" ")
    require([w for w in words if w.startswith("root=")] == ["root=/dev/mtdblock5"] and
            [w for w in words if w.startswith("rootfstype=")] == ["rootfstype=jffs2"] and
            not any(w.startswith(("mtdparts=", "ubi.mtd=")) for w in words), "wrong root bootargs")
    tables = [path for path, node in nodes.items() if "compatible" in node["props"] and
              "fixed-partitions" in string_list(node["props"]["compatible"])]
    require(len(tables) == 1, "ambiguous partition table")
    table = nodes[tables[0]]
    require(all(table["props"].get(key) == struct.pack(">I", 1) for key in
                ("#address-cells", "#size-cells")), "unsupported partition cells")
    paths = table["children"]
    require(len(paths) in (5, 6), "wrong partition count")
    root = nodes[paths[4]]["props"].get("reg", b"")
    require(len(root) == 8, "invalid root reg")
    root_size, = struct.unpack(">I", root[4:])
    require((root_size, len(paths)) in ((0xf000000, 6), (0xf100000, 5)), "unsupported root capacity")
    aliases = nodes.get("/aliases", {}).get("props", {})
    require({key for key in aliases if key.startswith("mtd")} ==
            {f"mtd{i + 1}" for i in range(len(paths))}, "unexpected/missing mtd alias")
    parts = []
    for i, path in enumerate(paths):
        node, size = nodes[path], root_size if i == 4 else SIZES[i]
        props = node["props"]
        require(props.get("label") == LABELS[i].encode() + b"\0", "wrong partition label/order")
        require(props.get("reg") == struct.pack(">II", OFFSETS[i], size), "wrong partition boundary")
        require(props.get("read-only") == b"" if i in (0, 3) else "read-only" not in props,
                "wrong read-only flags")
        require(aliases[f"mtd{i + 1}"] == path.encode() + b"\0", "wrong mtd alias target")
        ancestor = path
        while ancestor is not None:
            require(nodes[ancestor]["props"].get("status", b"okay\0") in (b"okay\0", b"ok\0"), "disabled flash node")
            ancestor = nodes[ancestor]["parent"]
        parts.append({"label": LABELS[i], "offset": OFFSETS[i], "size": size, "mtd": i + 1})
    return {"layout": "native241" if len(paths) == 5 else "current240", "root_bytes": root_size, "partitions": parts}
