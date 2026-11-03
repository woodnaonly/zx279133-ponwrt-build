#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Check the shipped optional module, not permission to unlock or flash a device."""
import argparse
import json
from pathlib import Path
import re
import struct
import sys


def module_metadata(data):
    if len(data) < 64 or data[:7] != b"\x7fELF\x02\x01\x01":
        raise ValueError("mtd-rw is not a little-endian ELF64 module")
    header = struct.unpack_from("<16sHHIQQQIHHHHHH", data)
    if header[1:4] != (1, 183, 1) or header[8] != 64:
        raise ValueError("mtd-rw is not an AArch64 relocatable module")
    offset, size, count, names_index = header[6], header[11], header[12], header[13]
    if size != 64 or not 1 < count <= 512 or not 0 < names_index < count or offset < 64 or offset + size * count > len(data):
        raise ValueError("invalid module section headers")
    sections = [struct.unpack_from("<IIQQQQIIQQ", data, offset + i * size) for i in range(count)]

    def contents(section):
        at, length = section[4:6]
        if at > len(data) or length > len(data) - at:
            raise ValueError("truncated module section")
        return data[at:at + length]

    if sections[names_index][1] != 3:
        raise ValueError("invalid module section-name table")
    names = contents(sections[names_index])
    modinfo = []
    for section in sections:
        at = section[0]
        end = names.find(b"\0", at)
        if at >= len(names) or end < 0:
            raise ValueError("invalid module section name")
        if names[at:end] == b".modinfo":
            if section[1] != 1:
                raise ValueError("invalid .modinfo section type")
            modinfo.append(contents(section))
    if len(modinfo) != 1 or not modinfo[0].endswith(b"\0"):
        raise ValueError("module must contain exactly one complete .modinfo section")
    result = {}
    for field in modinfo[0].split(b"\0"):
        if not field:
            continue
        key, separator, value = field.partition(b"=")
        if not separator:
            raise ValueError("invalid module metadata")
        result.setdefault(key.decode("ascii"), []).append(value.decode("utf-8"))
    return result


def validate_kernel(config):
    values = {}
    for line in config.splitlines():
        match = re.fullmatch(r"CONFIG_([A-Z0-9_]+)=(.*)", line)
        disabled = re.fullmatch(r"# CONFIG_([A-Z0-9_]+) is not set", line)
        if match:
            values[match[1]] = match[2]
        elif disabled:
            values[disabled[1]] = "n"
    for symbol in ("MTD", "MODULES", "MODULE_UNLOAD"):
        if values.get(symbol) != "y":
            raise ValueError(f"CONFIG_{symbol}=y is required for manual load/unload")


def validate_image(root, manifest, release):
    root = Path(root)
    if not release or any(c.isspace() or c in "/\\" for c in release):
        raise ValueError("invalid built kernel release")
    packages = [line for line in manifest.splitlines() if line.split()[:1] == ["kmod-mtd-rw"]]
    if len(packages) != 1:
        raise ValueError("image manifest must contain kmod-mtd-rw exactly once")
    modules = sorted((root / "lib/modules").glob("*/mtd-rw.ko"))
    if len(modules) != 1 or modules[0].is_symlink() or modules[0].parent.name != release:
        raise ValueError("image must contain one mtd-rw.ko under the built kernel release")
    module = modules[0]
    metadata = module_metadata(module.read_bytes())
    magic = metadata.get("vermagic", [])
    if len(magic) != 1 or not magic[0].split() or magic[0].split()[0] != release:
        raise ValueError("mtd-rw vermagic does not match the built kernel release")
    if metadata.get("parmtype", []).count("i_want_a_brick:bool") != 1:
        raise ValueError("mtd-rw must retain its explicit i_want_a_brick parameter")
    for folder in ("etc/modules.d", "etc/modules-boot.d"):
        for entry in sorted((root / folder).glob("*")):
            if entry.is_file() and re.search(r"^\s*mtd[-_]rw(?:\s|$)", entry.read_text(encoding="utf-8"), re.M):
                raise ValueError("mtd-rw must not be auto-loaded: " + str(entry.relative_to(root)))
    return {"module": module.relative_to(root).as_posix(), "kernel_release": release,
            "vermagic": magic[0], "manual_load_only": True, "hardware_load_verified": False,
            "flash_write_tested": False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tree", required=True, type=Path)
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--manifest", required=True, type=Path)
    args = parser.parse_args()
    try:
        tree = args.tree.resolve()
        selected = (tree / ".config").read_text(encoding="utf-8")
        if not re.search(r"^CONFIG_PACKAGE_kmod-mtd-rw=y$", selected, re.M):
            raise ValueError("kmod-mtd-rw was not selected after defconfig")
        external = re.findall(r'^CONFIG_EXTERNAL_KERNEL_TREE="([^"\n]+)"$', selected, re.M)
        if len(external) != 1:
            raise ValueError("expected one external kernel tree in the resolved build config")
        kernel = Path(external[0])
        if not kernel.is_absolute():
            kernel = tree / kernel
        validate_kernel((kernel / ".config").read_text(encoding="utf-8"))
        release = (kernel / "include/config/kernel.release").read_text(encoding="utf-8").strip()
        report = validate_image(args.root, args.manifest.read_text(encoding="utf-8"), release)
        print(json.dumps(report, sort_keys=True))
        print("PASS: matching optional mtd-rw module is in the image; no auto-load/unlock")
        return 0
    except (OSError, ValueError, UnicodeError, struct.error) as error:
        print("mtd-rw image gate: " + str(error), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
