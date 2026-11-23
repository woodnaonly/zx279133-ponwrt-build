#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""End-to-end test for tools/check-sk-kernel-order.py, without a kernel.

The gate reads a linked kernel: a symbol table to find the function, the
compiler's line table to say which instruction came from which source line,
and the machine code itself to ask where the restore is relative to the
check for the hardware.  Only the CI has a linked kernel, so the gate's own
logic would otherwise only be exercised once per build, at the very end of
the flash job.

This test builds a small ELF64 that has the shape the gate reads - a
loadable segment, a symbol table, the function's bytes - and a machine code
that has the shape the gate expects, and runs the gate and its controls
against them.  It needs no kernel, no cross compiler and no board, so it
can run in the gates job, where a broken gate is reported in minutes
instead of after an hour of compiling.

The source lines it asks about are read out of the real tree, so the test
follows the tree when the driver changes.

    python3 tests/test_sk_kernel_order_local.py --tree TREE
"""
import argparse
import gzip
import importlib.util
import struct
import subprocess
import sys
import tempfile
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GATE = ROOT / "tools/check-sk-kernel-order.py"

UIMAGE_MAGIC = 0x27051956
TEXT_VA = 0xFFFF800080001000
TEXT_OFF = 0x1000


def load_gate():
    """The gate itself - a test that copies its rules proves nothing."""
    spec = importlib.util.spec_from_file_location("sk_kernel_order", GATE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def build_elf(code: bytes, name: str) -> bytes:
    """An ELF64 with one loadable segment and one symbol."""
    label = name.encode()
    strtab = b"\0" + label + b"\0"
    strtab_off = TEXT_OFF + 0x200
    symtab_off = (strtab_off + len(strtab) + 7) & ~7
    symtab = b"\0" * 24 + struct.pack("<IBBHQQ", 1, 0x02, 0, 1, TEXT_VA, len(code))
    shoff = (symtab_off + len(symtab) + 63) & ~63

    ident = b"\x7fELF" + bytes([2, 1, 1, 0]) + b"\0" * 8
    header = ident + struct.pack(
        "<HHIQQQIHHHHHH",
        2, 183, 1, TEXT_VA, 64, shoff, 0, 64, 56, 1, 64, 3, 2)
    phdr = struct.pack("<IIQQQQQQ", 1, 5, TEXT_OFF, TEXT_VA, TEXT_VA,
                       len(code), len(code), 0x1000)
    sections = [
        struct.pack("<IIQQQQIIQQ", 0, 0, 0, 0, 0, 0, 0, 0, 0, 0),
        struct.pack("<IIQQQQIIQQ", 0, 2, 0, 0, symtab_off, len(symtab), 2, 1, 8, 24),
        struct.pack("<IIQQQQIIQQ", 0, 3, 0, 0, strtab_off, len(strtab), 0, 0, 1, 0),
    ]
    out = bytearray(shoff + len(sections))
    out[0:64] = header
    out[64:120] = phdr
    out[TEXT_OFF:TEXT_OFF + len(code)] = code
    out[strtab_off:strtab_off + len(strtab)] = strtab
    out[symtab_off:symtab_off + len(symtab)] = symtab
    out[shoff:shoff + len(sections)] = b"".join(sections)
    return bytes(out)


def build_disassembly(path: str, restore_line: int, helper_line: int,
                      check_line: int) -> str:
    """An objdump run of a function whose restore comes before the check.

    The shape is the one GNU objdump 2.44 prints for `objdump -d -l`: the
    function's name on a line of its own, then a "file:line" header every
    time the source line changes, and then the instructions - each with its
    address in the first column, then the bytes, then the mnemonic and the
    operands.  binutils/objdump/objdump.c shows it: show_line() prints the
    header, and the address column is the vma.
    """
    lines = [
        "zx279133_lan_netdev_open():",
        "%s:%d" % (path, restore_line),
        "  ffff800080001000:\td63f0000 \tbl\t2000 <zx279133_datapath_restore_failed_locked>",
        "%s:%d" % (path, helper_line),
        "  ffff800080001004:\t39400048 \tldrb\tw8, [x18, #72]",
        "  ffff800080001008:\t34000108 \tcbz\tw8, 3000 <zx279133_lan_netdev_open+0x1000>",
        "%s:%d" % (path, check_line),
        "  ffff80008000100c:\t39400048 \tldrb\tw8, [x18, #73]",
        "  ffff800080001010:\t34000108 \tcbz\tw8, 3000 <zx279133_lan_netdev_open+0x1000>",
    ]
    return "\n".join(lines) + "\n"


def write_uimage(path: Path, payload: bytes) -> None:
    """A legacy uImage around a payload."""
    header = bytearray(64)
    struct.pack_into(">I", header, 0, UIMAGE_MAGIC)
    struct.pack_into(">I", header, 8, 0)
    struct.pack_into(">I", header, 12, len(payload))
    header[32:64] = b"sk-d840n-local".ljust(32, b"\0")
    struct.pack_into(">I", header, 4, zlib.crc32(bytes(header)) & 0xFFFFFFFF)
    path.write_bytes(bytes(header) + gzip.compress(payload))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tree", required=True, type=Path,
                        help="the kernel tree whose source the gate reads")
    args = parser.parse_args()

    gate = load_gate()
    checks = 0
    failures = []

    def check(ok, what, detail=""):
        nonlocal checks
        checks += 1
        if ok:
            print("  ok   %s" % what)
        else:
            print("  FAIL %s%s" % (what, (" - " + detail) if detail else ""))
            failures.append(what)

    source = args.tree / gate.ZTE / gate.NETDEV
    if not source.is_file():
        check(False, "the tree has %s" % gate.NETDEV)
        print("%d checks, %d failed" % (checks, len(failures)))
        return 1
    text = source.read_text(encoding="utf-8", errors="replace")
    restore_line, check_line, helper_lines = gate.source_lines(text)
    check(restore_line is not None and check_line is not None and helper_lines,
          "the source has the two things the gate compares",
          "restore=%s check=%s helper=%s" % (restore_line, check_line,
                                             sorted(helper_lines)[:1]))
    if restore_line is None or check_line is None or not helper_lines:
        print("%d checks, %d failed" % (checks, len(failures)))
        return 1

    code = bytes(range(0x40, 0x40 + 0x40))
    with tempfile.TemporaryDirectory(prefix="sk-kernel-order-") as tmp:
        work = Path(tmp)
        elf_path = work / "vmlinux"
        elf_path.write_bytes(build_elf(code, gate.OPEN))
        dis_path = work / "func.dis"
        dis_path.write_text(build_disassembly(
            str(source), restore_line, min(helper_lines) + 4, check_line),
            encoding="utf-8")
        image_path = work / "flash-uImage"
        write_uimage(image_path, b"\0" * 0x100 + code + b"\0" * 0x100)

        base = ["--vmlinux", str(elf_path), "--image", str(image_path),
                "--tree", str(args.tree), "--disassembly", str(dis_path)]

        result = subprocess.run(
            [sys.executable, str(GATE)] + base,
            capture_output=True, text=True, encoding="utf-8", errors="replace")
        check(result.returncode == 0,
              "the gate passes on a kernel that is right",
              (result.stdout + result.stderr)[-400:])

        result = subprocess.run(
            [sys.executable, str(ROOT / "tests/test_sk_kernel_order.py"),
             "--vmlinux", str(elf_path), "--image", str(image_path),
             "--tree", str(args.tree), "--disassembly", str(dis_path)],
            capture_output=True, text=True, encoding="utf-8", errors="replace")
        check(result.returncode == 0,
              "the controls pass on a kernel that is right",
              (result.stdout + result.stderr)[-600:])

    print("%d checks, %d failed" % (checks, len(failures)))
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
