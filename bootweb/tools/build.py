#!/usr/bin/env python3
"""Build the exact C validator for the host, plus an unadapted AArch64 ELF.

Use a local Zig 0.14.1 path; nothing is installed system-wide or downloaded.
The separate synthetic test DLL can NEVER be linked into the AArch64 output.
"""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys
from audit_ram_elf import audit_elf

ROOT = Path(__file__).resolve().parents[1]
# Zig appends its CPU feature list AFTER Clang flags. -mgeneral-regs-only
# and -ffixed-x18 alone are therefore overridden in Zig 0.14.1. Set the
# actual Zig CPU features too; inspect the resulting machine code below.
NATIVE_FLAGS = ["-target", "aarch64-freestanding-none",
                "-mcpu=generic+reserve_x18+strict_align-fp_armv8-neon",
                "-ffreestanding", "-ffixed-x18", "-mgeneral-regs-only", "-mstrict-align",
                "-fno-vectorize", "-fno-slp-vectorize",
                "-fno-stack-protector", "-mno-outline-atomics", "-nostdlib"]
SOURCES = [ROOT / "src" / (name + ".c") for name in
           ("hash", "sha3", "identity", "resident_image", "install_image", "budget", "bounded_spi", "nand_gate", "json", "fdt", "package", "flash", "task", "http", "entry_gate")]
RAM_PINS = [(0x1100,0x800),(0x2600,0x404),(0x12c7c,64),(0x6677c,128),(0x65630,128),
            (0x18d34,0xc8),(0x192c0,64),(0x1aea0,40),(0x26220,256),(0x2592c,128),
            (0x67e00,0xe4),(0x36dac,0x178),(0x36f88,0x8c),(0x37364,0x31c),(0x3bcac,256)]
RAM_PINS += [(0x29bf8,0x40)]  # exact vendor "Disable watchdog" register stores
RAM_PINS += [(0x3a470,0xc0)]  # exact PHY mode-cache reads, branch and stores
SPI_PINS = [(0x53554,0x8c4),(0x56788,0x1e0),(0x56cb8,0x1b4),(0x2564c,0xbc)]
# The read/program chunk loops rely on adjust_op_size either rejecting a zero
# transfer or retaining a positive bounded length. Pin its ENTIRE function,
# plus supports_op and the claim/release trampolines, not just its caller.
SPI_PINS += [(0x56968,0x10),(0x56c80,0x38),(0x56e6c,0x9c),
             (0x255e8,0x64),(0x25708,0x64),(0x25a14,0x17c),
             (0x26220,0x338)]
NAND_WRITE_PINS = [(0x18cc0,0x74),(0x18dfc,0xb8),(0x1a9d4,0x1cc),
                   (0x25824,0x30),(0x25998,0x7c),(0x25b90,0x58),
                   (0x25c58,0x228),(0x25e80,0x9c),(0x25f1c,0x84),
                   (0x25fa0,0x280),(0x26558,0x1c4)]


def lwip_sources(path):
    core = [path / ("core/" + x + ".c") for x in
            ("init", "def", "inet_chksum", "ip", "mem", "memp", "netif", "pbuf",
             "stats", "sys", "tcp", "tcp_in", "tcp_out", "timeouts")]
    return (core + [path / ("core/ipv4/" + x + ".c") for x in ("etharp", "icmp", "ip4", "ip4_addr")]
            + [path / "netif/ethernet.c", ROOT / "adapter/lwip/bridge.c"])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--zig", type=Path, default=ROOT / ".build/toolchain/zig-x86_64-windows-0.14.1/zig.exe")
    parser.add_argument("--host-only", action="store_true")
    parser.add_argument("--build-dir", type=Path, default=ROOT / ".build",
                        help="separate outputs to retain previously hardware-tested artifacts unchanged")
    parser.add_argument("--ram-readonly", action="store_true", help="also build the immutable RAM-only artifact (vendor gate remains locked)")
    parser.add_argument("--ram-backup", action="store_true", help="separate RAM-only full physical ECC backup artifact; never writable")
    parser.add_argument("--backup-continuous", action="store_true", help="distinct read-only backup with explicit continuous power and a bounded 30-minute maximum")
    parser.add_argument("--ram-hardware", action="store_true", help="link the experimental pinned svn308776 RAM-only adapter")
    parser.add_argument("--resident-trial", action="store_true", help="RAM-only payload for the separately audited resident bootstrap; NOT FLASHABLE")
    parser.add_argument("--bounded-spi", action="store_true", help="experimental bounded native SPI FIFO replacement in the read-only target")
    parser.add_argument("--nand-abi-dryrun", action="store_true", help="exercise native erase/write ABI with mutating opcodes emulated, NEVER sent to NAND")
    parser.add_argument("--native-nand-preflight", action="store_true", help="exercise the distinct native NAND adapter across every permitted block/page, with ALL mutations emulated")
    parser.add_argument("--ram-probe-report", type=Path, help="successful minimal real RAM probe report; never enables NAND writes")
    parser.add_argument("--community-boot", type=Path, default=ROOT.parents[1] / "sk-d840n-openwrt/binary/boot.bin")
    parser.add_argument("--ram-address", type=lambda text: int(text, 0),
                        help="explicit native RAM link address; NOT a placement certificate")
    parser.add_argument("--test-readonly", action="store_true", help="also build RAM-only service tests with a synthetic base")
    parser.add_argument("--test-backup", action="store_true", help="also build synthetic RAM-only physical ECC backup tests")
    parser.add_argument("--test-core", action="store_true", help="also build isolated synthetic-NAND test DLL")
    parser.add_argument("--test-net", action="store_true", help="also build the in-memory Ethernet/TCP test DLL")
    parser.add_argument("--test-service", action="store_true", help="also build the isolated native HTTP service test DLL")
    parser.add_argument("--ubsan", action="store_true", help="trap undefined behavior in HOST libraries only")
    parser.add_argument("--lwip-dir", type=Path, default=ROOT.parents[1] / "uboot-an758x/lib/lwip/lwip/src",
                        help="BSD lwIP source tree only; no foreign board drivers are used")
    args = parser.parse_args()
    if args.ram_backup and not (args.ram_readonly and args.ram_hardware):
        parser.error("--ram-backup requires --ram-readonly --ram-hardware and remains NOT FLASHABLE")
    if args.backup_continuous and not (args.ram_backup and args.bounded_spi):
        parser.error("--backup-continuous requires the immutable --ram-backup --bounded-spi artifact")
    if args.ram_readonly and (args.ram_address is None or args.host_only):
        parser.error("--ram-readonly requires --ram-address and cannot be --host-only")
    if args.ram_address is not None and (not 0x80000000 <= args.ram_address < 0xa0000000 or args.ram_address % 64):
        parser.error("RAM address must be cache-line aligned inside the known DRAM envelope; runtime reservations still need verification")
    if args.ram_hardware:
        if not args.ram_readonly or args.ram_probe_report is None:
            parser.error("--ram-hardware requires a separate RAM-only target and a real probe report")
        probe = json.loads(args.ram_probe_report.read_text(encoding="utf-8"))
        if not all(probe.get(k) is True for k in ("ram_probe_pass", "alias_restored", "cache_restored")):
            parser.error("minimal RAM execution and cleanup have not passed")
    if args.resident_trial and not (args.ram_readonly and args.ram_hardware):
        parser.error("--resident-trial requires --ram-readonly --ram-hardware; never enables writes")
    if args.bounded_spi and not (args.ram_readonly and args.ram_hardware):
        parser.error("--bounded-spi requires a separate --ram-readonly --ram-hardware artifact")
    if args.nand_abi_dryrun and not args.bounded_spi:
        parser.error("--nand-abi-dryrun requires the read-only bounded SPI target")
    if args.native_nand_preflight and not args.nand_abi_dryrun:
        parser.error("--native-nand-preflight requires --nand-abi-dryrun; it cannot enable real mutations")
    zig = args.zig.resolve()
    if not zig.is_file():
        parser.error("provide --zig; no compiler was found")
    build = args.build_dir.resolve()
    build.mkdir(parents=True, exist_ok=True)
    if args.ram_hardware:
        dump = args.community_boot.read_bytes()
        if len(dump) != 0x200000 or hashlib.sha256(dump[0x20000:0x180000]).hexdigest() != "5b084490813b540c1a82ddf2e2d4de46080e6cfe47b15b2e6575bdf3fe198fe9":
            parser.error("pinned RAM adapter refuses an unknown static U-Boot baseline")
        header = "/* Exact immutable baseline code fingerprints; no runtime capabilities. */\nstatic const struct bw_pin bw_ram_pins[]={\n"
        for off,size in RAM_PINS:
            header += "{" + hex(off) + "," + str(size) + ',"' + hashlib.sha256(dump[0x20000+off:0x20000+off+size]).hexdigest() + '"},\n'
        (build / "bw_ram_pins.h").write_text(header + "};\n",encoding="utf-8",newline="\n")
        if args.bounded_spi:
            header="static const struct bw_spi_pin bw_spi_pins[]={\n"
            for off,size in SPI_PINS+(NAND_WRITE_PINS if args.nand_abi_dryrun else []):
                header+="{"+hex(off)+","+str(size)+',"'+hashlib.sha256(dump[0x20000+off:0x20000+off+size]).hexdigest()+'"},\n'
            (build/"bw_spi_pins.h").write_text(header+"};\n",encoding="utf-8",newline="\n")
    flags = ["-std=c11", "-O2", "-Wall", "-Wextra", "-Werror", "-fno-builtin",
             "-I" + str(ROOT / "include"), "-I" + str(ROOT / "src"), "-I" + str(build)]
    suffix = ".dll" if sys.platform == "win32" else ".so"
    fake = hashlib.sha256(b"B" * 0x160000).hexdigest()
    host_flags = [*flags, *(["-fsanitize=undefined", "-fsanitize-trap=undefined"] if args.ubsan else [])]
    command = [str(zig), "cc", *host_flags, *map(str, SOURCES), "-shared"]
    if sys.platform != "win32":
        command += ["-fPIC"]
    subprocess.run([*command, "-o", str(build / ("bootweb" + suffix))], check=True)
    if args.test_core:
        subprocess.run([*command, '-DBW_TEST_BASE="' + fake + '"', "-o",
                        str(build / ("bootweb-test" + suffix))], check=True)
    if not args.host_only or args.test_service or args.test_readonly or args.test_backup:
        html = (ROOT / "web/index.html").read_bytes()
        if len(html) > 32768:
            parser.error("single-file page exceeds 32 KiB")
        (build / "web.inc").write_text(
            "static const unsigned char bw_page[]={" + ",".join(map(str, html)) + "};\n",
            encoding="utf-8", newline="\n")
    if args.test_service:
        subprocess.run([*command, str(ROOT / "src/service.c"), '-DBW_TEST_BASE="' + fake + '"',
                        "-o", str(build / ("bootweb-service-test" + suffix))], check=True)
    if args.test_readonly:
        subprocess.run([*command, str(ROOT / "src/service.c"), "-DBW_RAM_READONLY=1",
                        str(ROOT / "tests/ram_policy_harness.c"),
                        '-DBW_TEST_BASE="' + fake + '"', "-o",
                        str(build / ("bootweb-ram-readonly-test" + suffix))], check=True)
    if args.test_backup:
        subprocess.run([*command, str(ROOT / "src/service.c"), str(ROOT / "src/backup.c"),
                        "-DBW_RAM_READONLY=1", "-DBW_RAM_BACKUP=1", str(ROOT / "tests/ram_policy_harness.c"),
                        '-DBW_TEST_BASE="' + fake + '"', "-o",
                        str(build / ("bootweb-ram-backup-test" + suffix))], check=True)
    if not args.host_only or args.test_net:
        lwip = args.lwip_dir.resolve()
        net_sources = lwip_sources(lwip)
        if not all(p.is_file() for p in net_sources):
            parser.error("provide --lwip-dir pointing to a complete lwIP src/ directory")
    if args.test_net:
        subprocess.run([str(zig), "cc", *host_flags, "-I" + str(ROOT / "adapter/lwip"),
                        "-I" + str(lwip / "include"), *map(str, net_sources),
                        str(ROOT / "src/hash.c"), "-shared", *([] if sys.platform == "win32" else ["-fPIC"]),
                        "-o", str(build / ("bootweb-net-test" + suffix))], check=True)
    if not args.host_only:
        elf = build / "bootweb-unadapted.elf"
        subprocess.run([str(zig), "cc", *flags, *NATIVE_FLAGS,
                        "-I" + str(ROOT / "adapter/lwip"), "-I" + str(lwip / "include"),
                        *map(str, net_sources),
                        *map(str, SOURCES), str(ROOT / "src/service.c"),
                        str(ROOT / "adapter/vendor_unverified.c"), str(ROOT / "adapter/entry.c"),
                        "-Wl,-T," + str(ROOT / "adapter/standalone.ld"),
                        "-Wl,-e,bootweb_command", "-o", str(elf)], check=True)
        subprocess.run([str(zig), "objcopy", "--output-target", "binary", str(elf),
                        str(build / "bootweb-unadapted.bin")], check=True)
        (build / "bootweb-unadapted-instructions.json").write_text(
            json.dumps(audit_elf(elf.read_bytes()), indent=2) + "\n", encoding="utf-8")
        if args.ram_readonly:
            ram_name = "bootweb-ram-backup" if args.ram_backup else "bootweb-ram-readonly"
            readonly_elf = build / (ram_name + ".elf")
            ram_script = build / "ram-readonly-placement.ld"
            ram_script.write_text("BW_RAM_BASE = " + hex(args.ram_address) + ";\n" +
                                  (ROOT / "adapter/ram-readonly.ld").read_text(encoding="utf-8"),
                                  encoding="utf-8", newline="\n")
            subprocess.run([str(zig), "cc", *flags, "-DBW_RAM_READONLY=1",
                            *(["-DBW_RAM_CONTINUOUS_POWER=1"] if args.backup_continuous else []),
                            *(["-DBW_RESIDENT_BOOTSTRAP=1"] if args.resident_trial else []),
                            *(["-DBW_NATIVE_NAND_DRYRUN=1"] if args.nand_abi_dryrun else []),
                            *(["-DBW_INSTALL_PREFLIGHT=1", str(ROOT/"adapter/vendor_nand.c")] if args.native_nand_preflight else []),
                            *(["-DBW_NATIVE_BOUNDED_SPI=1",str(ROOT/"adapter/vendor_bounded_spi.c")] if args.bounded_spi else []),
                            *(["-DBW_RAM_BACKUP=1", str(ROOT / "src/backup.c")] if args.ram_backup else []),
                            *NATIVE_FLAGS,
                            "-I" + str(ROOT / "adapter/lwip"), "-I" + str(lwip / "include"),
                            *map(str, net_sources), *map(str, SOURCES), str(ROOT / "src/service.c"),
                            str(ROOT / ("adapter/vendor_ram_readonly.c" if args.ram_hardware else "adapter/vendor_unverified.c")), str(ROOT / "adapter/entry.c"),
                            "-Wl,-T," + str(ram_script),
                            "-Wl,-e,bootweb_command", "-o", str(readonly_elf)], check=True)
            subprocess.run([str(zig), "objcopy", "--output-target", "binary", str(readonly_elf),
                            str(build / (ram_name + ".bin"))], check=True)
            (build / (ram_name + "-instructions.json")).write_text(
                json.dumps(audit_elf(readonly_elf.read_bytes()), indent=2) + "\n", encoding="utf-8")
            (build / "ram-build-policy.json").write_text(json.dumps({
                "ram_readonly": True, "ram_hardware": args.ram_hardware,
                "resident_bootstrap_trial": args.resident_trial, "nand_write_enabled": False,
                "power_mode": "continuous_required" if args.backup_continuous else "periodic20m",
                "max_service_ms": 1800000 if args.backup_continuous else 900000 if args.ram_backup else 180000,
                "native_bounded_spi_trial": args.bounded_spi,
                "native_nand_abi_dryrun": args.nand_abi_dryrun,
                "native_nand_preflight": args.native_nand_preflight,
                "payload_sha256": hashlib.sha256((build / (ram_name + ".bin")).read_bytes()).hexdigest(),
                "elf_sha256": hashlib.sha256(readonly_elf.read_bytes()).hexdigest(),
            }, indent=2) + "\n", encoding="utf-8")
            print("RAM-only artifact built; " + ("experimental pinned adapter, NOT HARDWARE-ACCEPTED." if args.ram_hardware else "vendor adapter is STILL UNVERIFIED and rejects entry.") + " NOT FLASHABLE.")
    print("Built C core. Native output is UNADAPTED / NOT FLASHABLE; no device was contacted.")


if __name__ == "__main__":
    main()
