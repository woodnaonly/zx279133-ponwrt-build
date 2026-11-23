#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Check the machine code of the conduit's open: the restore has to come
before the check for the hardware.

The tree's gate (tools/check-sk-datapath-restore.py) says the restore comes
before the check for the hardware.  That is a statement about the source.
This one is a statement about the machine code the board runs, and about the
bytes that go on the board.

Why it has to be asked twice
----------------------------
A datapath that failed leaves the hardware unprepared, so a check for the
hardware that sits in front of the restore refuses the open before any
restore could run.  The source says the order is right.  The compiler gets
the last word, and the board gets the compiler's word.

Two things could still go wrong between the source and the board:

  * the compiler could put the check in front of the restore.  It cannot:
    the restore is a call whose result decides whether the open fails, so the
    check cannot be moved in front of it without changing what the board
    does - but "it cannot" is an argument, and a gate is evidence.
  * the tree that was checked is not the tree that was compiled.  A patch
    that applies without landing leaves the older driver in the image, and
    the older driver has no restore in this function at all.

So the gate reads the compiled function and asks where the two things are.

How it decides
--------------
The kernel is built with debug information, so the compiler's line table
says which source line every instruction came from.  The two source lines
are found in the source itself - the line of the restore call and the line
of the check for the hardware - and the machine code is asked where those
lines landed.

  * the restore is the call, or, when the compiler inlined it, the code of
    the helper the call went to; either way it is the instructions the
    restore's source lines produced,
  * the check for the hardware is the instructions of the line that tests
    it, and the branch that acts on the loaded value is what matters: a
    load can be moved, a branch that decides whether the open fails cannot
    be moved in front of a restore that can change what it tests.

The order that is checked is therefore the branch against the restore, not
a load against a load.

The bytes
---------
The machine code that was read is the one in the kernel that was linked,
and the gate then looks for exactly those bytes in the image that is about
to be flashed.  The image is the artifact: a tree that is not the image
says nothing about a board.

Usage:
  check-sk-kernel-order.py --vmlinux VMLINUX --image flash-uImage --tree TREE
                            [--objdump PATH] [--nm PATH] [--report FILE]

Exit status: 0 when every check holds, 1 otherwise.
"""
import argparse
import gzip
import io
import json
import re
import struct
import subprocess
import sys
from pathlib import Path

ZTE = "drivers/net/ethernet/zte"
NETDEV = "zx279133-netdev.c"
OPEN = "zx279133_lan_netdev_open"
RESTORE = "zx279133_datapath_restore_failed_locked"

UIMAGE_MAGIC = 0x27051956
UIMAGE_HEADER_LEN = 64
PT_LOAD = 1

# The branch that decides whether the open fails.  A load can be moved
# around, a conditional branch that acts on it cannot be moved in front of
# a restore that can change what it tests.
BRANCHES = ("cbz", "cbnz", "tbz", "tbnz")

LOADS = ("ldrb", "ldrh", "ldr", "ldrsb", "ldrsh", "ldrsw")


class Report:
    """The checks this gate made, and the ones that did not hold."""

    def __init__(self):
        self.failures = []
        self.checks = 0
        self.results = []

    def require(self, ok, message, detail=""):
        self.checks += 1
        self.results.append((bool(ok), message, detail))
        print(f"  [{'OK ' if ok else 'FAIL'}] {message}"
              + (f" - {detail}" if detail and not ok else ""))
        if not ok:
            self.failures.append(message)
        return bool(ok)


# --------------------------------------------------------------------------
# the source
# --------------------------------------------------------------------------

def function_span(lines, name):
    """The first and the last line of a function's body, 1-based.

    The body is found by counting braces from the definition, which is what
    the tree's own gate does as well.  A brace inside a string or a comment
    would confuse it, and the driver does not have one in the two functions
    this gate looks at.

    A declaration is not a definition: the driver declares the restore
    helper at the top of the file and defines it in the middle, and the
    declaration is followed by a semicolon and not by a brace.  A name that
    is only a declaration is skipped, and so is a call.
    """
    pattern = re.compile(r"\b" + re.escape(name) + r"\s*\(")
    for start in range(len(lines)):
        if not pattern.search(lines[start]):
            continue
        depth = 0
        opened = False
        for index in range(start, len(lines)):
            for char in lines[index]:
                if char == ";" and not opened:
                    # A declaration or a call: not the body.
                    depth = None
                    break
                if char == "{":
                    depth += 1
                    opened = True
                elif char == "}":
                    depth -= 1
                    if opened and depth == 0:
                        return start + 1, index + 1
            if depth is None or (opened and depth == 0):
                break
    return None


def source_lines(text):
    """The source lines the gate asks about.

    Returns (restore_line, check_line, helper_lines) where the line numbers
    are 1-based line numbers of the driver's source file:

      * restore_line is the line of the call to the restore helper inside the
        conduit's open,
      * check_line is the line that tests the hardware inside the same
        function,
      * helper_lines is the set of lines of the helper's body, which is what
        the compiler's line table shows when it inlines the helper.
    """
    lines = text.splitlines()
    span = function_span(lines, OPEN)
    if span is None:
        return None, None, set()
    first, last = span
    body = lines[first - 1:last]

    restore_line = None
    for offset, line in enumerate(body):
        if re.search(r"\b" + re.escape(RESTORE) + r"\s*\(", line):
            restore_line = first + offset
            break

    check_line = None
    for offset, line in enumerate(body):
        if "hardware_prepared" in line:
            check_line = first + offset
            break

    helper = function_span(lines, RESTORE)
    helper_lines = set(range(helper[0], helper[1] + 1)) if helper else set()
    return restore_line, check_line, helper_lines


# --------------------------------------------------------------------------
# the machine code
# --------------------------------------------------------------------------

def parse_disassembly(text):
    """The instructions of an objdump -d -l run.

    Each instruction becomes a dict with its address, the file and line it
    came from, its mnemonic and its operands.  The line is the one the
    compiler's line table gives, which is what makes the machine code
    comparable with the source.
    """
    records = []
    line = None
    file_name = None
    for raw in text.splitlines():
        if not raw.strip():
            continue
        if "\t" not in raw:
            match = re.match(r"^(.*\.[ch]):(\d+)(?:\s.*)?$", raw.strip())
            if match:
                file_name = match.group(1)
                line = int(match.group(2))
            continue
        parts = raw.split("\t")
        if len(parts) < 3:
            continue
        address = parts[0].strip().rstrip(":")
        if not re.fullmatch(r"[0-9a-fA-F]+", address):
            continue
        mnemonic = parts[2].strip()
        operands = parts[3].strip() if len(parts) > 3 else ""
        if " " in mnemonic:
            # A tool that separates the mnemonic and the operands with a
            # space instead of a tab.
            head, _, tail = mnemonic.partition(" ")
            mnemonic = head
            operands = (tail + " " + operands).strip()
        records.append({
            "addr": int(address, 16),
            "file": file_name,
            "line": line,
            "mnemonic": mnemonic,
            "operands": operands,
        })
    return records


def register_of(operands):
    """The register a load writes, e.g. "w8" out of "w8, [x19, #72]"."""
    head = operands.split(",")[0].strip()
    return head if re.fullmatch(r"[wx]\d+", head) else None


def branch_on(records, start, register):
    """The first conditional branch after start that acts on register."""
    for record in records:
        if record["addr"] <= start:
            continue
        if record["mnemonic"] not in BRANCHES:
            continue
        operands = record["operands"]
        if register and re.search(r"(?<![a-z0-9])" + re.escape(register) + r"(?![0-9])", operands):
            return record
    return None


def distinct_lines(records):
    """The source lines the machine code of a function came from."""
    lines = sorted({r["line"] for r in records if r["line"]})
    shown = ", ".join(str(line) for line in lines[:60])
    if len(lines) > 60:
        shown += ", ..."
    return shown


def analyze(records, restore_line, check_line, helper_lines, address):
    """Ask the machine code where the two things are.

    The checks are the gate's, and they are named so that a control can say
    which one it turned red.
    """
    report = Report()
    driver = NETDEV
    mine = [r for r in records
            if r["file"] and Path(r["file"]).name == driver]

    # The disassembly has to be the function's own machine code.  The column
    # objdump prints in front of an instruction is its address, and the
    # function starts at the address the symbol table gives - so the first
    # instruction has to be at that address.  If that column ever held
    # something else (a source line number, say), every comparison below
    # would be about the wrong thing and would still look plausible, which
    # is why this is a check of its own and not a comment.
    first = records[0]["addr"] if records else None
    report.require(first is not None and abs(first - address) <= 0x1000,
                   "the machine code is the function's own",
                   "the disassembly starts at %s, the symbol is at 0x%x"
                   % (("0x%x" % first) if first is not None else "nothing",
                      address))

    # A kernel without line information cannot answer the question at all,
    # and that is worth saying plainly: the build has to carry debug
    # information for the compiler's line table to exist.
    report.require(any(r["line"] for r in records),
                   "the kernel carries line information",
                   "the disassembly names no source line at all - the build "
                   "has to have CONFIG_DEBUG_INFO=y for the compiler's line "
                   "table to exist")

    restore = [r for r in mine
               if r["line"] == restore_line or r["line"] in helper_lines]
    check = [r for r in mine if r["line"] == check_line]

    if not report.require(bool(restore),
                          "the compiled function carries the restore",
                          "no instruction of the restore was found in the "
                          "conduit's open; the function's lines are: %s"
                          % distinct_lines(mine)):
        return report
    if not report.require(bool(check),
                          "the compiled function asks about the hardware",
                          "no instruction of the hardware check was found in "
                          "the conduit's open; the function's lines are: %s"
                          % distinct_lines(mine)):
        return report

    load = None
    for record in check:
        if record["mnemonic"] in LOADS:
            load = record
            break
    if not report.require(load is not None,
                          "the hardware check loads the flag it tests",
                          "the check's instructions load nothing"):
        return report

    register = register_of(load["operands"])
    # The branch is looked for over the whole function and not only over the
    # instructions of the check's line: the compiler may attribute the
    # branch to another line of the check, and what matters is where the
    # decision is made, not which line the decision is written down on.
    branch = branch_on(mine, load["addr"], register)
    if not report.require(branch is not None,
                          "the hardware check branches on the loaded value",
                          f"the load of {load['operands']} is never tested"):
        return report

    first_restore = min(r["addr"] for r in restore)
    report.require(branch["addr"] > first_restore,
                   "the restore comes before the hardware check in the machine code",
                   f"the restore is at 0x{first_restore:x}, the branch that "
                   f"refuses the open is at 0x{branch['addr']:x}")
    report.require(min(r["addr"] for r in check) >= first_restore,
                   "the hardware check is not hoisted in front of the restore",
                   f"the check starts at 0x{min(r['addr'] for r in check):x}, "
                   f"the restore is at 0x{first_restore:x}")
    return report


# --------------------------------------------------------------------------
# the kernel and the image
# --------------------------------------------------------------------------

def elf_segments(data):
    """The program headers of an ELF64 file."""
    if data[:4] != b"\x7fELF":
        raise SystemExit("not an ELF file")
    if data[4] != 2:
        raise SystemExit("only ELF64 is supported")
    phoff = struct.unpack_from("<Q", data, 0x20)[0]
    phentsize = struct.unpack_from("<H", data, 0x36)[0]
    phnum = struct.unpack_from("<H", data, 0x38)[0]
    segments = []
    for index in range(phnum):
        offset = phoff + index * phentsize
        if offset + 56 > len(data):
            break
        p_type, _flags, p_offset, p_vaddr, _paddr, p_filesz, p_memsz, _align = \
            struct.unpack_from("<IIQQQQQQ", data, offset)
        segments.append({"type": p_type, "offset": p_offset, "vaddr": p_vaddr,
                         "filesz": p_filesz, "memsz": p_memsz})
    return segments


def va_to_offset(segments, address, size=1):
    """The file offset of an address in a loadable segment."""
    for segment in segments:
        if segment["type"] != PT_LOAD:
            continue
        if segment["vaddr"] <= address and \
                address + size <= segment["vaddr"] + segment["filesz"]:
            return segment["offset"] + (address - segment["vaddr"])
    return None


def elf_symbols(data, name):
    """The address and the size of a symbol in an ELF64 file.

    The kernel that was linked still carries its symbol table, so the
    function this gate reads is found there - no external tool has to agree
    about what a symbol looks like.
    """
    if data[:4] != b"\x7fELF" or data[4] != 2:
        raise SystemExit("not an ELF64 file")
    shoff = struct.unpack_from("<Q", data, 0x28)[0]
    shentsize = struct.unpack_from("<H", data, 0x3A)[0]
    shnum = struct.unpack_from("<H", data, 0x3C)[0]
    sections = []
    for index in range(shnum):
        offset = shoff + index * shentsize
        if offset + 64 > len(data):
            break
        (sh_name, sh_type, _flags, sh_addr, sh_offset, sh_size,
         sh_link, _info, _align, sh_entsize) = struct.unpack_from(
            "<IIQQQQIIQQ", data, offset)
        sections.append({"name": sh_name, "type": sh_type, "addr": sh_addr,
                         "offset": sh_offset, "size": sh_size, "link": sh_link,
                         "entsize": sh_entsize})
    for section in sections:
        if section["type"] != 2 or not section["entsize"]:
            continue
        if section["link"] >= len(sections):
            continue
        strings = sections[section["link"]]
        table = data[strings["offset"]:strings["offset"] + strings["size"]]
        for index in range(section["size"] // section["entsize"]):
            offset = section["offset"] + index * section["entsize"]
            st_name, _info, _other, _shndx, st_value, st_size = \
                struct.unpack_from("<IBBHQQ", data, offset)
            if not st_name:
                continue
            end = table.find(b"\0", st_name)
            if table[st_name:end].decode("latin1", "replace") == name:
                return st_value, st_size
    return None, None


def disassemble(objdump, vmlinux, address, size, name):
    """The instructions of one function, with the lines they came from."""
    start = "0x%x" % address
    stop = "0x%x" % (address + size if size else address + 0x4000)
    cmd = [objdump, "-d", "-l",
           "--start-address=" + start, "--stop-address=" + stop, str(vmlinux)]
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, check=True).stdout
    except (OSError, subprocess.CalledProcessError) as exc:
        raise SystemExit("objdump failed: %s" % exc)
    if not out.strip():
        cmd = [objdump, "-d", "-l", "--disassemble=" + name, str(vmlinux)]
        try:
            out = subprocess.run(cmd, capture_output=True, text=True, check=True).stdout
        except (OSError, subprocess.CalledProcessError) as exc:
            raise SystemExit("objdump failed: %s" % exc)
    return out


def inflate_uimage(path: Path) -> bytes:
    """The inflated payload of a legacy uImage."""
    raw = path.read_bytes()
    if len(raw) < UIMAGE_HEADER_LEN:
        raise SystemExit("%s: too short to be a uImage" % path)
    magic, _hcrc, _itime, dsize = struct.unpack(">4I", raw[:16])
    if magic != UIMAGE_MAGIC:
        raise SystemExit("%s: not a legacy uImage (magic %08x)" % (path, magic))
    body = raw[UIMAGE_HEADER_LEN:UIMAGE_HEADER_LEN + dsize] if dsize else raw[UIMAGE_HEADER_LEN:]
    if body[:2] != b"\x1f\x8b":
        raise SystemExit("%s: the uImage body is not gzip" % path)
    out = io.BytesIO()
    with gzip.GzipFile(fileobj=io.BytesIO(body)) as gz:
        while True:
            chunk = gz.read(1 << 20)
            if not chunk:
                break
            out.write(chunk)
    return out.getvalue()


def find_bytes(haystack: bytes, needle: bytes) -> int:
    """Where the needle is, or -1."""
    if not needle:
        return -1
    return haystack.find(needle)


def image_carries(image: bytes, code: bytes):
    """Whether the image that is about to be flashed carries these bytes.

    Returns (ok, offset, detail).  The image is the artifact: a tree that
    is not the image says nothing about a board.
    """
    offset = find_bytes(image, code)
    if offset < 0:
        return False, -1, ("the function's bytes are in the kernel but not "
                           "in the image that is about to be flashed")
    return True, offset, ""


def main(argv=None) -> int:
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
                             "running objdump - the controls use it to feed "
                             "the gate a machine code that is broken")
    parser.add_argument("--report", type=Path,
                        help="write the verdict as JSON here")
    args = parser.parse_args(argv)

    if not args.vmlinux.is_file():
        raise SystemExit("%s: no such kernel" % args.vmlinux)
    if not args.image.is_file():
        raise SystemExit("%s: no such image" % args.image)

    source = args.tree / ZTE / NETDEV
    if not source.is_file():
        raise SystemExit("%s: the tree has no %s" % (args.tree, NETDEV))

    objdump = str(args.objdump) if args.objdump else "objdump"

    text = source.read_text(encoding="utf-8", errors="replace")
    restore_line, check_line, helper_lines = source_lines(text)
    print("source: %s" % source)
    print("  the restore call is on line %s" % restore_line)
    print("  the hardware check is on line %s" % check_line)
    print("  the helper's body is on lines %s..%s"
          % (min(helper_lines) if helper_lines else None,
             max(helper_lines) if helper_lines else None))
    if restore_line is None or check_line is None:
        print("FAIL: the tree's source does not have the two things this "
              "gate compares")
        return 1

    data = args.vmlinux.read_bytes()
    segments = elf_segments(data)
    address, size = elf_symbols(data, OPEN)
    if address is None:
        raise SystemExit("%s: the kernel has no symbol %s" % (args.vmlinux, OPEN))
    print("kernel: %s" % args.vmlinux)
    print("  %s is at 0x%x, %s bytes"
          % (OPEN, address, size if size else "of unknown size"))

    records = []
    if args.disassembly:
        records = parse_disassembly(args.disassembly.read_text(
            encoding="utf-8", errors="replace"))
        print("  the machine code was read from %s" % args.disassembly)
    else:
        records = parse_disassembly(disassemble(objdump, args.vmlinux,
                                                 address, size, OPEN))
    print("  %d instruction(s) read" % len(records))

    report = analyze(records, restore_line, check_line, helper_lines, address)

    # The bytes: the machine code that was read has to be the machine code
    # that is on the board.
    image = inflate_uimage(args.image)
    offset = va_to_offset(segments, address, size or 4)
    found = -1
    if offset is None:
        report.require(False, "the shipped image carries this exact code",
                       "the kernel's address is in no loadable segment")
    else:
        code = data[offset:offset + (size or 4)]
        ok, found, detail = image_carries(image, code)
        report.require(ok, "the shipped image carries this exact code", detail)
    if found >= 0:
        print("  the function is at offset 0x%x of the image" % found)

    if args.report:
        args.report.write_text(json.dumps({
            "vmlinux": str(args.vmlinux),
            "image": str(args.image),
            "function": OPEN,
            "address": "0x%x" % address,
            "size": size,
            "restore_line": restore_line,
            "check_line": check_line,
            "image_offset": found,
            "checks": report.checks,
            "failed": len(report.failures),
            "results": [{"ok": ok, "check": message, "detail": detail}
                        for ok, message, detail in report.results],
        }, indent=2) + "\n", encoding="utf-8")

    if report.failures:
        print("\nFAILED: %d of %d check(s)" % (len(report.failures), report.checks))
        for item in report.failures:
            print("  - %s" % item)
        return 1
    print("\nOK: %d check(s)" % report.checks)
    return 0


if __name__ == "__main__":
    sys.exit(main())
