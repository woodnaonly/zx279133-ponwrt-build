#!/usr/bin/env python3
# 本地排演套件（local rehearsal suite）：依赖 staging/ 下的真实构建产物，
# 在只 clone 本仓库的环境里跑不起来，也**没有**接进 CI。
# 为什么、怎么跑、缺的那个夹具从哪来：同目录 README.md。
"""Emit a dtc-style .dts from an FDT, so the CI content gate's awk/grep logic
can be exercised locally without dtc installed.

Formatting mirrors dtc closely enough for the gate's patterns (tab indent,
`name@addr {`, `prop = value;`, `};` terminator, bare `/` for root). It is a
test harness, not a general-purpose decompiler.
"""
import struct
import sys
import importlib.util

spec = importlib.util.spec_from_file_location(
    "fdtdump", "/mnt/c/ugit/SK-D840N4/staging/diag-20261014/fdtdump.py")
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

FDT_BEGIN_NODE, FDT_END_NODE, FDT_PROP, FDT_NOP, FDT_END = 1, 2, 3, 4, 9


def _is_stringish(raw):
    """DT string props are printable ASCII separated/terminated by NULs."""
    body = raw.rstrip(b"\0")
    if not body or not raw.endswith(b"\0"):
        return False
    return all(b == 0 or 32 <= b < 127 for b in body)


def fmt(raw):
    if len(raw) == 0:
        return ""
    # A NUL-separated string list is still "string-ish".
    #
    # Render it the way dtc ACTUALLY renders it, which is one quoted run with a literal
    # two-character \0 between entries:
    #
    #     compatible = "zte,zx279133-topcrm\0syscon";
    #
    # This file used to emit `"a", "b"` instead, and that is not a cosmetic difference:
    # the CI gate's awk splits the line on `"` and tests each token against
    # ^zte,zx279133-[a-z0-9-]+$. Against `"a", "b"` the tokens are `a` and `b` and every
    # node with a fallback is found. Against dtc's real output the single token is
    # `a\0b`, which matches nothing, and every node carrying a fallback compatible is
    # silently dropped from the scan. So a renderer that "looked right" proved the gate
    # works, and proved nothing about the gate. Genuine dtc output is committed in
    # ci-build-repo/decompiled/ and is what this rendering is calibrated against.
    #
    # Other dtc escaping (hex width, leading underscores) is not reproduced here; it does
    # not affect the three fields the gate reads - node name, compatible, status - and
    # the calibration compares exactly those.
    if _is_stringish(raw):
        return '"%s"' % b"\\0".join(raw.rstrip(b"\0").split(b"\0")).decode("ascii")
    if len(raw) % 4 == 0:
        n = len(raw) // 4
        return "<" + " ".join("0x%x" % v for v in struct.unpack(">%dI" % n, raw)) + ">"
    return raw.hex()


def emit(path):
    blob = open(path, "rb").read()
    f = m.Fdt(blob)
    out, depth = [], -1
    off, in_node = f.off_dt_struct, []
    while True:
        (tok,) = struct.unpack(">I", blob[off:off + 4])
        off += 4
        if tok == FDT_BEGIN_NODE:
            end = blob.index(b"\0", off)
            name = blob[off:end].decode("utf-8", "replace")
            off = (end + 4) & ~3
            depth += 1
            out.append("\t" * depth + (name + " {" if name else "/ {"))
            in_node.append(True)
        elif tok == FDT_END_NODE:
            out.append("\t" * depth + "};")
            depth -= 1
            in_node.pop()
        elif tok == FDT_PROP:
            plen, nameoff = struct.unpack(">II", blob[off:off + 8])
            off += 8
            raw = blob[off:off + plen]
            v = fmt(raw)
            out.append("\t" * (depth + 1) + f.s(nameoff) + (" = " + v if v else "") + ";")
            off = (off + plen + 3) & ~3
        elif tok == FDT_NOP:
            continue
        elif tok == FDT_END:
            return "\n".join(out) + "\n"
        else:
            raise ValueError("bad token %d" % tok)


if __name__ == "__main__":
    sys.stdout.write(emit(sys.argv[1]))