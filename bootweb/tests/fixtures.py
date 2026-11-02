"""Synthetic images only; contains no proprietary firmware or device identifiers."""
import gzip
import hashlib
import io
import json
import struct
import tarfile
import zlib

BOARD = "skyworth_sk-d840n"
PREFIX = "sysupgrade-" + BOARD


def uimage(payload=None, **overrides):
    if payload is None:
        payload = gzip.compress(b"synthetic arm64 kernel" * 128, mtime=0)
    values = dict(magic=0x27051956, hcrc=0, time=0, size=len(payload),
                  load=0x80200000, entry=0x80200000, dcrc=zlib.crc32(payload),
                  os=5, arch=22, kind=2, comp=1)
    values.update(overrides)
    head = struct.pack(">7I4B32s", *values.values(), b"bootweb synthetic kernel")
    return head[:4] + struct.pack(">I", zlib.crc32(head)) + head[8:] + payload


def root(length=12):
    head = struct.pack("<HHI", 0x1985, 0x2003, 12)
    return head + struct.pack("<I", zlib.crc32(head, 0xffffffff) ^ 0xffffffff) + b"R" * (length - 12)


def fdt(layout="current240", *, board="skyworth,sk-d840n", changes=None,
        alias_changes=None, extra=None, readonly=True,
        bootargs="console=ttyAMA0,115200n8 root=/dev/mtdblock5 rootfstype=jffs2 ro rootwait",
        flash_disabled=False, table_disabled=False, extra_root=False,
        duplicate_aliases=False, duplicate_chosen=False, ambiguous_table=False):
    parts = {"boot": (0, 0x200000), "kernel": (0x200000, 0x800000),
             "dtb": (0xa00000, 0x100000), "parameter tags": (0xb00000, 0x400000),
             "root": (0xf00000, {"current240": 0xf000000, "native241": 0xf100000,
                                  "recovery237": 0xed00000}[layout])}
    if layout == "current240":
        parts["scratch"] = (0xff00000, 0x100000)
    if layout == "recovery237":
        parts["recovery"] = (0xfc00000, 0x400000)
    parts.update(changes or {})
    if extra:
        parts.update(extra)
    names = {label: "partition@" + format(region[0], "x") for label, region in parts.items()}
    aliases = {"mtd" + str(i + 1): ("/flash/partitions/" + names[label]).encode() + b"\0"
               for i, label in enumerate(parts)}
    aliases.update(alias_changes or {})
    strings = bytearray()
    offsets = {}

    def prop(name, value):
        if name not in offsets:
            offsets[name] = len(strings)
            strings.extend(name.encode() + b"\0")
        return struct.pack(">III", 3, len(value), offsets[name]) + value + bytes(-len(value) % 4)

    def node(name, properties, children=()):
        text = name.encode() + b"\0"
        return (struct.pack(">I", 1) + text + bytes(-len(text) % 4) +
                b"".join(prop(*p) for p in properties.items()) + b"".join(children) + struct.pack(">I", 2))

    partition_nodes = []
    for label, (off, size) in parts.items():
        props = {"label": label.encode() + b"\0", "reg": struct.pack(">II", off, size)}
        if label in ("boot", "parameter tags") and readonly:
            props["read-only"] = b""
        partition_nodes.append(node(names[label], props))
    table_props = {"compatible": b"fixed-partitions\0", "#address-cells": struct.pack(">I", 1),
                   "#size-cells": struct.pack(">I", 1)}
    if table_disabled:
        table_props["status"] = b"disabled\0"
    table = node("partitions", table_props, partition_nodes)
    chosen = {} if bootargs is None else {"bootargs": bootargs.encode() + b"\0"}
    children = [node("aliases", aliases), node("chosen", chosen),
                node("flash", {"status": b"disabled\0"} if flash_disabled else {}, [table])]
    if duplicate_aliases:
        children.append(node("aliases", {}))
    if duplicate_chosen:
        children.append(node("chosen", {}))
    if ambiguous_table:
        children.append(node("flash2", {}, [node("partitions", {"compatible": b"fixed-partitions\0fixed-partitions\0"})]))
    tree = node("", {"compatible": board.encode() + b"\0zte,zx279133\0"}, children)
    if extra_root:
        tree += node("", {})
    tree += struct.pack(">I", 9)
    return (struct.pack(">10I", 0xd00dfeed, 56 + len(tree) + len(strings), 56,
                        56 + len(tree), 40, 17, 16, 0, len(strings), len(tree)) + bytes(16) + tree + strings)


def tar(items, *, directory=None, format=tarfile.USTAR_FORMAT):
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w", format=format) as archive:
        if directory:
            info = tarfile.TarInfo(directory)
            info.type = tarfile.DIRTYPE
            archive.addfile(info)
        for name, data in items:
            if isinstance(data, tarfile.TarInfo):
                archive.addfile(data)
            else:
                info = tarfile.TarInfo(name)
                info.size = len(data)
                archive.addfile(info, io.BytesIO(data))
    return output.getvalue()


def meta():
    return {"metadata_version": "1.1", "compat_version": "1.0",
            "supported_devices": ["skyworth,sk-d840n", "zte,133"],
            "version": {"board": BOARD, "target": "zte/zx279133"}}


def trailer(data, payload, kind=1):
    body = data + payload
    return body + struct.pack(">IIB3xI", 0x46577830, zlib.crc32(body) ^ 0xffffffff, kind, len(payload) + 16)


def sys_parts(layout="current240"):
    return {"CONTROL": ("BOARD=" + BOARD + "\n").encode(), "kernel": uimage(),
            "dtb": fdt(layout), "root": root(0x20007)}


def system(parts=None, *, layout="current240", metadata=None, metadata_raw=None,
           extras=(), directory=PREFIX + "/", format=tarfile.USTAR_FORMAT):
    parts = sys_parts(layout) if parts is None else parts
    data = tar([(PREFIX + "/" + name, value) for name, value in parts.items()] + list(extras),
               directory=directory, format=format)
    payload = json.dumps(meta() if metadata is None else metadata).encode() if metadata_raw is None else metadata_raw
    return trailer(data, bytes(8) + payload)


def boot_parts(base, payload=None, *, changes=None):
    payload = b"C" * 0x160000 if payload is None else payload
    digest = hashlib.sha256(payload).hexdigest()
    control = ("FORMAT=sk-d840n-uboot-v1\nBOARD=" + BOARD + "\nBASE_SHA256=" + base +
               "\nPAYLOAD_SHA256=" + digest + "\nPAYLOAD_BYTES=1441792\n").encode()
    parts = {"CONTROL": control, "uboot.bin": payload,
             "sha256sums": (hashlib.sha256(control).hexdigest() + "  CONTROL\n" + digest + "  uboot.bin\n").encode()}
    parts.update(changes or {})
    return parts


def boot(base, **kwargs):
    return tar(boot_parts(base, **kwargs).items())


def nand_seed():
    return b"A" * 0x20000 + b"B" * 0x160000 + b"E" * 0x20000 + b"T" * 0x60000
