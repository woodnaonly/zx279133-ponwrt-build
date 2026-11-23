#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Negative controls for tools/check-sk-kernel-order.py.

The gate asks the machine code of the conduit's open where the restore is
relative to the check for the hardware.  Each control below breaks one of
the things it asks about, and the gate has to turn red *on the check that
names that thing* - not merely red somewhere.

The order matters most of all: a datapath that failed leaves the hardware
unprepared, so a check for the hardware that sits before the restore
refuses the open before any restore could run.  There is a control for
exactly that, and it breaks the machine code rather than the source: the
source is already covered by tests/test_sk_datapath_restore.py, and this
gate's claim is about the code the board runs.

The controls run against the kernel that was built and the image that is
about to be flashed, so they do not prove that the gate reads a synthetic
driver - they prove that the gate reads *this* driver and that it bites.

    python3 tests/test_sk_kernel_order.py --vmlinux VMLINUX --image flash-uImage \\
        --tree TREE [--objdump PATH] [--nm PATH]
"""
import argparse
import gzip
import importlib.util
import re
import struct
import subprocess
import sys
import tempfile
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GATE = ROOT / "tools/check-sk-kernel-order.py"

UIMAGE_MAGIC = 0x27051956


def load_gate():
    """The gate itself - a control that copies its rules proves nothing."""
    spec = importlib.util.spec_from_file_location("sk_kernel_order", GATE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def failed_checks(args, extra):
    """The checks the gate reported as failed."""
    cmd = [sys.executable, str(GATE)] + args + extra
    result = subprocess.run(cmd, capture_output=True, text=True)
    names = set()
    for line in (result.stdout + result.stderr).splitlines():
        match = re.match(r"^\s*\[FAIL\]\s+(.+?)(?:\s+-\s+.*)?$", line)
        if match:
            names.add(match.group(1))
    return names, result.returncode, result.stdout + result.stderr


def emit(records):
    """The machine code as an objdump run would print it."""
    out = []
    current = None
    for record in records:
        key = (record["file"], record["line"])
        if key != current:
            out.append("%s:%s" % (record["file"] or "?", record["line"] if record["line"] else 0))
            current = key
        out.append("  %x:\t00000000 \t%s\t%s"
                   % (record["addr"], record["mnemonic"], record["operands"]))
    return "\n".join(out) + "\n"


def renumber(records):
    """The records with fresh addresses, in their own order."""
    out = []
    address = 0x1000
    for record in records:
        copy = dict(record)
        copy["addr"] = address
        address += 4
        out.append(copy)
    return out


def write_uimage(path: Path, payload: bytes) -> None:
    """A legacy uImage around a payload."""
    header = bytearray(64)
    struct.pack_into(">I", header, 0, UIMAGE_MAGIC)
    struct.pack_into(">I", header, 4, 0)
    struct.pack_into(">I", header, 8, 0)
    struct.pack_into(">I", header, 12, len(payload))
    header[32:64] = b"sk-d840n-control".ljust(32, b"\0")
    struct.pack_into(">I", header, 4, zlib.crc32(bytes(header)) & 0xFFFFFFFF)
    path.write_bytes(bytes(header) + gzip.compress(payload))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--vmlinux", required=True, type=Path,
                        help="the linked kernel the image was built from")
    parser.add_argument("--image", required=True, type=Path,
                        help="the uImage that is about to be flashed")
    parser.add_argument("--tree", required=True, type=Path,
                        help="the kernel tree that was compiled")
    parser.add_argument("--objdump", type=Path,
                        help="the aarch64 objdump to read the machine code with")
    parser.add_argument("--disassembly", type=Path,
                        help="read the machine code from this file instead of "
                             "running objdump")
    args = parser.parse_args()

    gate = load_gate()

    def gate_args(image=None, disassembly=None):
        """The gate's command line, with the image or the machine code swapped."""
        out = ["--vmlinux", str(args.vmlinux),
               "--image", str(image if image else args.image),
               "--tree", str(args.tree)]
        if args.objdump:
            out += ["--objdump", str(args.objdump)]
        if args.disassembly:
            out += ["--disassembly", str(args.disassembly)]
        if disassembly:
            out += ["--disassembly", str(disassembly)]
        return out

    base = gate_args()

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

    # The machine code of the kernel that was built.
    source = args.tree / gate.ZTE / gate.NETDEV
    text = source.read_text(encoding="utf-8", errors="replace")
    restore_line, check_line, helper_lines = gate.source_lines(text)
    if restore_line is None or check_line is None:
        check(False, "the tree's source has the two things the gate compares")
        print("%d checks, %d failed" % (checks, len(failures)))
        return 1

    data = args.vmlinux.read_bytes()
    segments = gate.elf_segments(data)
    objdump = str(args.objdump) if args.objdump else "objdump"
    address, size = gate.elf_symbols(data, gate.OPEN)
    if address is None:
        check(False, "the kernel has the function the gate reads")
        print("%d checks, %d failed" % (checks, len(failures)))
        return 1
    records = []
    if args.disassembly:
        records = gate.parse_disassembly(args.disassembly.read_text(
            encoding="utf-8", errors="replace"))
    else:
        records = gate.parse_disassembly(
            gate.disassemble(objdump, args.vmlinux, address, size, gate.OPEN))
    check(bool(records), "the machine code of the function was read",
          "%d instruction(s)" % len(records))

    # The kernel as it is has to pass.
    names, rc, output = failed_checks(base, [])
    check(rc == 0, "the kernel as it is passes the gate",
          ", ".join(sorted(names)[:3]) or output[-400:])

    with tempfile.TemporaryDirectory(prefix="sk-kernel-order-") as tmp:
        work = Path(tmp)

        def groups(records):
            restore = [r for r in records
                       if r["line"] == restore_line or r["line"] in helper_lines]
            check_group = [r for r in records if r["line"] == check_line]
            return restore, check_group

        # 1. The hardware check in front of the restore.
        restore, check_group = groups(records)
        if not restore or not check_group:
            check(False, "the machine code has both things to reorder")
        else:
            first = records.index(restore[0])
            moved = [r for r in records if r not in check_group]
            broken = renumber(moved[:first] + check_group + moved[first:])
            path = work / "reordered.dis"
            path.write_text(emit(broken), encoding="utf-8")
            names, rc, output = failed_checks(base, ["--disassembly", str(path)])
            expected = "the restore comes before the hardware check in the machine code"
            check(expected in names,
                  "the hardware check in front of the restore -> red on %r" % expected,
                  ", ".join(sorted(names)[:3]))

        # 2. No restore at all.
        restore, check_group = groups(records)
        broken = renumber([r for r in records if r not in restore])
        path = work / "no-restore.dis"
        path.write_text(emit(broken), encoding="utf-8")
        names, rc, output = failed_checks(base, ["--disassembly", str(path)])
        expected = "the compiled function carries the restore"
        check(expected in names,
              "no restore in the machine code -> red on %r" % expected,
              ", ".join(sorted(names)[:3]))

        # 3. No hardware check at all.
        restore, check_group = groups(records)
        broken = renumber([r for r in records if r not in check_group])
        path = work / "no-check.dis"
        path.write_text(emit(broken), encoding="utf-8")
        names, rc, output = failed_checks(base, ["--disassembly", str(path)])
        expected = "the compiled function asks about the hardware"
        check(expected in names,
              "no hardware check in the machine code -> red on %r" % expected,
              ", ".join(sorted(names)[:3]))

        # 4. An image that does not carry the function.
        image = gate.inflate_uimage(args.image)
        offset = gate.va_to_offset(segments, address, size or 4)
        if offset is None:
            check(False, "the function's bytes are in the kernel")
        else:
            code = data[offset:offset + (size or 4)]
            found = image.find(code)
            if found < 0:
                check(False, "the image carries the function's bytes")
            else:
                broken_image = bytearray(image)
                broken_image[found + 8] ^= 0x40
                path = work / "broken-uImage"
                write_uimage(path, bytes(broken_image))
                names, rc, output = failed_checks(gate_args(image=path), [])
                expected = "the shipped image carries this exact code"
                check(expected in names,
                      "an image without the function -> red on %r" % expected,
                      ", ".join(sorted(names)[:3]))

    print("%d checks, %d failed" % (checks, len(failures)))
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
