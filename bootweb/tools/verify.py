#!/usr/bin/env python3
"""Build, test and record an OFFLINE development manifest. Never contacts devices.

Successful exit certifies these offline checks only, NOT permission to flash.
No command-line switch, JSON profile or passing test can unlock the adapter.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import io
import json
from pathlib import Path
import platform
import re
import struct
import subprocess
import sys
import time
import unittest

import build as builder

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from host.core import Core, read_package
from host.image import analyze, load_profile
from check_recovery_size import check as recovery_size


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def elf_info(data):
    if len(data) < 64 or data[:6] != b"\x7fELF\x02\x01":
        raise ValueError("native output is not little-endian ELF64")
    head = struct.unpack_from("<16sHHIQQQIHHHHHH", data)
    if head[1:4] != (2, 183, 1):
        raise ValueError("native output is not an AArch64 executable")
    entry, phoff, phsize, count = head[4], head[5], head[9], head[10]
    if head[8] != 64 or phoff < 64 or phsize != 56 or count > 64 or phoff + phsize * count > len(data):
        raise ValueError("invalid ELF64 program headers")
    loads = []
    for i in range(count):
        kind, flags, off, addr, physical, filesz, memsz, alignment = struct.unpack_from("<IIQQQQQQ", data, phoff + i * 56)
        if kind != 1:
            continue
        if off + filesz > len(data) or memsz < filesz:
            raise ValueError("invalid loadable ELF64 segment")
        loads.append({"address": f"0x{addr:x}", "file_bytes": filesz, "memory_bytes": memsz,
                      "flags": flags, "alignment": alignment})
    if not any(int(x["address"], 16) <= entry < int(x["address"], 16) + x["file_bytes"] and x["flags"] & 1 for x in loads):
        raise ValueError("entry is outside executable data")
    return {"machine": "AArch64", "entry": f"0x{entry:x}", "load_segments": loads,
            "ram_placement_verified": False, "vendor_container": False}


class Results(unittest.TextTestResult):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.cases = []

    def startTest(self, test):
        super().startTest(test)
        self.case_started = time.monotonic()

    def record(self, test, status, detail=None):
        item = {"id": test.id(), "status": status,
                "seconds": round(time.monotonic() - getattr(self, "case_started", time.monotonic()), 6)}
        if detail:
            item["detail"] = detail
        self.cases.append(item)

    def addSuccess(self, test):
        super().addSuccess(test)
        self.record(test, "passed")

    def addFailure(self, test, error):
        super().addFailure(test, error)
        self.record(test, "failed", self._exc_info_to_string(error, test))

    def addError(self, test, error):
        super().addError(test, error)
        self.record(test, "error", self._exc_info_to_string(error, test))

    def addSkip(self, test, reason):
        super().addSkip(test, reason)
        self.record(test, "skipped", reason)


def inputs():
    paths = [p for folder in ("adapter", "host", "include", "profiles", "src", "tests", "tools", "web", "LICENSES")
             for p in (ROOT / folder).rglob("*") if p.is_file() and "__pycache__" not in p.parts]
    values = {p.relative_to(ROOT).as_posix(): sha256(p.read_bytes()) for p in sorted(paths)}
    canonical = json.dumps(values, sort_keys=True, separators=(",", ":")).encode()
    return {"sha256": sha256(canonical), "files": values}


def dependency(lwip):
    paths = [p for p in builder.lwip_sources(lwip) if p.is_relative_to(lwip)]
    paths += sorted((lwip / "include").rglob("*.h"))
    values = {p.relative_to(lwip).as_posix(): sha256(p.read_bytes()) for p in sorted(paths)}
    version = (lwip / "include/lwip/init.h").read_text(encoding="utf-8")
    matches = [re.search(r"^#define LWIP_VERSION_" + name + r"\s+(\d+)", version, re.M)
               for name in ("MAJOR", "MINOR", "REVISION")]
    if not all(matches):
        raise ValueError("cannot identify the lwIP version")
    pieces = [match.group(1) for match in matches]
    license_data = (lwip.parent / "COPYING").read_bytes()
    return {"name": "lwIP", "version": ".".join(pieces), "license": "BSD-3-Clause",
            "vendor_hardware_drivers_linked": False,
            "license_sha256": sha256(license_data),
            "source_and_headers_sha256": sha256(json.dumps(values, sort_keys=True, separators=(",", ":")).encode()),
            "hashed_file_count": len(values), "linked_sources": [p.relative_to(lwip).as_posix() for p in paths if p.suffix == ".c"]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--zig", type=Path, default=ROOT / ".build/toolchain/zig-x86_64-windows-0.14.1/zig.exe")
    parser.add_argument("--lwip-dir", type=Path, default=ROOT.parents[1] / "uboot-an758x/lib/lwip/lwip/src")
    parser.add_argument("--output", type=Path, help="exclusive-create report path; default replaces .build/offline-verification.json")
    parser.add_argument("--no-ubsan", action="store_true", help="record that host undefined-behavior traps were NOT enabled")
    parser.add_argument("--compat-system", type=Path, help="optional existing sysupgrade sample; read-only format check, not a recommendation")
    parser.add_argument("--community-boot", type=Path, help="optional full boot dump for profile/hash cross-check")
    parser.add_argument("--stock-boot", type=Path, help="optional full factory dump for comparison only")
    parser.add_argument("--recovery-image", type=Path, help="optional fallback FIT for size-only check")
    args = parser.parse_args()
    if args.output and args.output.exists():
        parser.error("report exists; choose a new output path")
    report = {"schema": "sk-d840n-bootweb-offline-v1", "utc": datetime.now(timezone.utc).isoformat(),
              "scope": "OFFLINE DEVELOPMENT ONLY", "offline_pass": False,
              "flashable_release": False, "native_write_enabled": False,
              "hardware_acceptance": {name: False for name in
                  ("ram_command_entry", "relocation_and_x18", "ram_placement_and_cache", "ethernet_2p5g",
                   "nand_ecc_badblocks", "watchdog", "reset_hold_3s", "btl_rescue_known_good",
                   "ram_full_backup_and_config", "system_write_reboot", "uboot_write_reboot",
                   "damaged_system_reentry", "cold_boot")}}
    code = 1
    try:
        zig, lwip = args.zig.resolve(), args.lwip_dir.resolve()
        version = subprocess.check_output([str(zig), "version"], text=True).strip()
        if version != "0.14.1":
            raise ValueError("this reproducible build is pinned to Zig 0.14.1")
        locked = json.loads((ROOT / "tools/dependencies.lock.json").read_text(encoding="utf-8"))
        net_dependency = dependency(lwip)
        for key in ("version", "source_and_headers_sha256", "license_sha256", "hashed_file_count"):
            if net_dependency[key] != locked["lwip"][key]:
                raise ValueError("lwIP dependency differs from the recorded lock: " + key)
        source_before = inputs()
        report["toolchain"] = {"zig": version, "zig_executable_sha256": sha256(zig.read_bytes()),
                               "python": platform.python_version(), "host": platform.platform(),
                               "host_undefined_behavior_traps": not args.no_ubsan,
                               "native_fixed_x18": True, "warnings_as_errors": True}
        command = [sys.executable, str(ROOT / "tools/build.py"), "--zig", str(zig), "--lwip-dir", str(lwip),
                   "--test-core", "--test-net", "--test-service"]
        if not args.no_ubsan:
            command.append("--ubsan")
        subprocess.run(command, check=True)
        stream = io.StringIO()
        suite = unittest.defaultTestLoader.discover(str(ROOT / "tests"))
        result = unittest.TextTestRunner(stream=stream, verbosity=2, resultclass=Results).run(suite)
        print(stream.getvalue(), end="")
        report["tests"] = {"run": result.testsRun, "failed": len(result.failures), "errors": len(result.errors),
                           "skipped": len(result.skipped), "cases": result.cases}
        if not result.wasSuccessful() or result.skipped:
            raise ValueError("offline suite failed or skipped required coverage")
        report["source_inputs"] = inputs()
        if report["source_inputs"]["sha256"] != source_before["sha256"] or dependency(lwip) != net_dependency:
            raise ValueError("source/dependency files changed during verification; rebuild from a stable snapshot")
        report["network_dependency"] = net_dependency
        report["artifacts"] = {name: {"bytes": (ROOT / ".build" / name).stat().st_size,
                                       "sha256": sha256((ROOT / ".build" / name).read_bytes()),
                                       "flashable": False}
                               for name in ("bootweb-unadapted.elf", "bootweb-unadapted.bin")}
        report["native_elf"] = elf_info((ROOT / ".build/bootweb-unadapted.elf").read_bytes())
        page = (ROOT / "web/index.html").read_bytes()
        report["page"] = {"bytes": len(page), "limit_bytes": 32768, "sha256": sha256(page),
                          "visual_browser_qa": False}
        if args.compat_system:
            report["existing_system_format_check"] = Core().validate(read_package(args.compat_system)).report()
            report["existing_system_format_check"]["sample_name"] = args.compat_system.name
        for role, path, profile_name in (("community_profile", args.community_boot, "svn308776"),
                                         ("factory_comparison", args.stock_boot, "svn358069")):
            if path:
                profile = load_profile(ROOT / "profiles" / (profile_name + ".json"))
                dump = path.read_bytes()
                static = analyze(dump, profile)
                if not static["recognized_code"]:
                    raise ValueError(role + " sample does not match the recorded code hash")
                report[role] = {key: static[key] for key in
                                ("image_sha256", "region_sha256", "banners", "recognized_code")}
                report[role]["command_count"] = len(static["commands"])
                btl = json.loads((ROOT / "profiles/btl-shared-static.json").read_text(encoding="utf-8"))
                if static["region_sha256"]["stage1"] != btl["stage1_sha256"]:
                    raise ValueError(role + " has an unknown BTL")
                for name, area in btl["regions"].items():
                    start, end = int(area["start"], 16), int(area["end"], 16)
                    if not 0 <= start < end <= 0x20000 or sha256(dump[start:end]) != area["sha256"]:
                        raise ValueError(role + " has a mismatched static BTL region: " + name)
                report[role]["btl_static_regions_checked"] = len(btl["regions"])
        if args.recovery_image:
            # A failed fallback size gate is an expected finding, not a reason
            # to silently grow the partition or to fail unrelated native tests.
            report["fallback_size_only"] = recovery_size(args.recovery_image)
            report["fallback_size_only"]["sample_name"] = args.recovery_image.name
        report["offline_pass"], code = True, 0
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        report["error"] = str(error)
        print("bootweb offline verification:", error, file=sys.stderr)
    output = args.output or ROOT / ".build/offline-verification.json"
    try:
        output.parent.mkdir(parents=True, exist_ok=True)
        with output.open("x" if args.output else "w", encoding="utf-8", newline="\n") as target:
            json.dump(report, target, indent=2, ensure_ascii=False)
            target.write("\n")
    except OSError as error:
        parser.exit(1, f"cannot save report: {error}\n")
    print(str(output.resolve()))
    print("OFFLINE checks only. Hardware/write gates remain LOCKED; NOT FLASHABLE.")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
