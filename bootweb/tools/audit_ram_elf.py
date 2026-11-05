#!/usr/bin/env python3
"""Fail-closed instruction audit for cache/MMU-off AArch64 RAM trials.

This is NOT a proof of driver correctness or RAM placement. It checks every
executable instruction for complete decoding, SIMD/FP use and x18 clobbers.
The compiler must additionally use strict alignment for packed byte access.
"""
import hashlib
import re
import struct


def audit_instructions(code, address):
    import capstone
    if capstone.__version__ != "5.0.7":
        raise ValueError("RAM instruction audit requires Capstone 5.0.7")
    engine = capstone.Cs(capstone.CS_ARCH_ARM64, capstone.CS_MODE_LITTLE_ENDIAN)
    engine.detail = True
    consumed = count = reads_x18 = 0
    for ins in engine.disasm(code, address):
        if ins.address != address + consumed or ins.size != 4:
            raise ValueError("non-contiguous AArch64 decode")
        reads, writes = ins.regs_access()
        names = [ins.reg_name(r) for r in (*reads, *writes)]
        # Do not mistake 'sp' for an FP scalar 's0'.
        if any(re.fullmatch(r"[bhdqsvzp][0-9]+", name) for name in names):
            raise ValueError(f"SIMD/FP forbidden at {ins.address:#x}: {ins.mnemonic} {ins.op_str}")
        if any(ins.reg_name(r) in ("x18", "w18") for r in writes):
            raise ValueError(f"U-Boot gd/x18 clobber at {ins.address:#x}: {ins.mnemonic} {ins.op_str}")
        reads_x18 += any(ins.reg_name(r) in ("x18", "w18") for r in reads)
        consumed += ins.size
        count += 1
    if not code or consumed != len(code):
        raise ValueError("executable bytes were not completely decoded")
    return {"bytes": consumed, "instructions": count, "x18_read_instructions": reads_x18}


def audit_elf(data):
    if len(data) < 64 or data[:6] != b"\x7fELF\x02\x01":
        raise ValueError("expected little-endian ELF64")
    head = struct.unpack_from("<16sHHIQQQIHHHHHH", data)
    if head[1:4] != (2, 183, 1) or head[11] != 64 or not 0 < head[12] < 128:
        raise ValueError("invalid AArch64 section table")
    off, count = head[6], head[12]
    if off < 64 or off + 64 * count > len(data):
        raise ValueError("truncated ELF section table")
    sections = [struct.unpack_from("<IIQQQQIIQQ", data, off + i * 64) for i in range(count)]
    symbols = {}
    for section in sections:
        if section[1] != 2:
            continue
        _, _, _, _, start, size, link, _, _, entry_size = section
        if entry_size != 24 or size % 24 or start + size > len(data) or link >= count:
            raise ValueError("invalid ELF symbol table")
        names = sections[link]
        if names[4] + names[5] > len(data):
            raise ValueError("invalid ELF strings")
        strings = data[names[4]:names[4]+names[5]]
        for pos in range(start, start + size, 24):
            name, info, other, index, value, length = struct.unpack_from("<IBBHQQ", data, pos)
            key = strings[name:].split(b"\0", 1)[0].decode("ascii", errors="replace")
            if key in ("__bss_start", "__bss_end", "__ram_stack_bottom", "__ram_stack_top", "__image_end"):
                symbols[key] = hex(value)
    checked = []
    for section in sections:
        _, kind, flags, address, start, size, *_ = section
        if not flags & 4:
            continue
        if kind != 1 or address % 4 or size % 4 or start + size > len(data):
            raise ValueError("invalid executable section")
        checked.append({"address": hex(address), **audit_instructions(data[start:start+size], address)})
    if not checked:
        raise ValueError("ELF has no executable section")
    return {"passed": True, "elf_sha256": hashlib.sha256(data).hexdigest(),
            "simd_fp_forbidden": True, "x18_clobber_forbidden": True,
            "strict_alignment_required": True, "executable_sections": checked,
            "layout_symbols": symbols}


def verify_payload(data, payload, base=0x89000000, limit=0x89200000):
    """Bind the audited ELF instructions to the exact XMODEM flat binary."""
    audit = audit_elf(data)
    head = struct.unpack_from("<16sHHIQQQIHHHHHH", data)
    if head[4] != base or head[9] != 56 or not 0 < head[10] <= 16:
        raise ValueError("invalid RAM ELF program headers or entry")
    spans = []
    for i in range(head[10]):
        at = head[5] + i * 56
        if at + 56 > len(data):
            raise ValueError("truncated program header")
        kind, flags, start, address, physical, size, memory, alignment = struct.unpack_from("<IIQQQQQQ", data, at)
        if kind != 1:
            continue
        if not base <= address < address + memory <= limit or size > memory or start + size > len(data):
            raise ValueError("load segment outside validated image reservation")
        if any(address < old[0] + old[2] and old[0] < address + memory for old in spans):
            raise ValueError("overlapping ELF load segments")
        spans.append((address, data[start:start+size], memory))
    if not spans:
        raise ValueError("no load segments")
    # A pure NOLOAD/BSS segment has memory but contributes NO file bytes.
    # Its aligned start must not manufacture trailing bytes in objcopy output.
    end = max(address + len(code) for address, code, memory in spans if code)
    wanted = bytearray(end - base)
    for address, code, memory in spans:
        wanted[address-base:address-base+len(code)] = code
    if payload != wanted:
        raise ValueError("flat payload differs from the instruction-audited ELF")
    return {**audit, "payload_sha256": hashlib.sha256(payload).hexdigest(), "payload_matches_elf": True}
