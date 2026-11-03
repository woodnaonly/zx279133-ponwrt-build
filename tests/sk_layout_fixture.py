# SPDX-License-Identifier: GPL-2.0-only
"""Small, real FDTs; no dtc, external firmware, network or device dependency."""
import os
from pathlib import Path
import struct
import subprocess

TABLE = "/soc/spi@10d0f000/flash@0/partitions"
LABELS = ("boot", "kernel", "dtb", "parameter tags", "root", "scratch")
OFFSETS = (0, 0x200000, 0xa00000, 0xb00000, 0xf00000, 0xff00000)
SIZES = (0x200000, 0x800000, 0x100000, 0x400000, 0xf100000, 0x100000)


def tree(layout="native241"):
    count = 5 if layout == "native241" else 6
    parts, aliases = [], []
    for i in range(count):
        name = f"partition@{OFFSETS[i]:x}"
        size = 0xf000000 if i == 4 and count == 6 else SIZES[i]
        props = [("label", LABELS[i].encode() + b"\0"), ("reg", struct.pack(">II", OFFSETS[i], size))]
        if i in (0, 3):
            props.append(("read-only", b""))
        parts.append([name, props, []])
        aliases.append((f"mtd{i + 1}", f"{TABLE}/{name}".encode() + b"\0"))
    table = ["partitions", [("compatible", b"fixed-partitions\0"),
                            ("#address-cells", struct.pack(">I", 1)),
                            ("#size-cells", struct.pack(">I", 1))], parts]
    return ["", [("compatible", b"skyworth,sk-d840n\0zte,zx279133\0")], [
        ["aliases", aliases, []],
        ["chosen", [("bootargs", b"console=ttyAMA0,115200n8 root=/dev/mtdblock5 rootfstype=jffs2 ro rootwait\0")], []],
        ["soc", [], [["spi@10d0f000", [], [["flash@0", [], [table]]]]]]]]


def at(root, path):
    node = root
    for name in path.strip("/").split("/") if path != "/" else []:
        node = next(n for n in node[2] if n[0] == name)
    return node


def put(node, name, value):
    node[1][:] = [(key, val) for key, val in node[1] if key != name]
    node[1].append((name, value))


def pack(root):
    strings, offsets = bytearray(), {}

    def align(data):
        return data + bytes((-len(data)) % 4)

    def emit(node):
        out = struct.pack(">I", 1) + align(node[0].encode() + b"\0")
        for name, value in node[1]:
            if name not in offsets:
                offsets[name] = len(strings)
                strings.extend(name.encode() + b"\0")
            out += struct.pack(">III", 3, len(value), offsets[name]) + align(value)
        out += b"".join(emit(child) for child in node[2])
        return out + struct.pack(">I", 2)

    body = emit(root) + struct.pack(">I", 9)
    header = struct.pack(">10I", 0xd00dfeed, 56 + len(body) + len(strings),
                         56, 56 + len(body), 40, 17, 16, 0, len(strings), len(body))
    return header + bytes(16) + body + strings


def dtb(layout="native241"):
    return pack(tree(layout))


def build_checker(directory):
    if os.environ.get("SK_LAYOUT_CHECK"):
        return os.environ["SK_LAYOUT_CHECK"]
    binary = Path(directory) / "sk-layout-check"
    source = Path(__file__).resolve().parents[1] / "overlays/package/utils/sk-layout-check/src/sk-layout-check.c"
    subprocess.run(["cc", "-std=c11", "-Wall", "-Wextra", "-Werror", "-O2",
                    str(source), "-lfdt", "-o", str(binary)], check=True)
    return str(binary)
