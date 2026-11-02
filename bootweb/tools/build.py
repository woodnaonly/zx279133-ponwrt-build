#!/usr/bin/env python3
"""Build the exact C validator for the host, plus an unadapted AArch64 ELF.

Use a local Zig 0.14.1 path; nothing is installed system-wide or downloaded.
The separate synthetic test DLL can NEVER be linked into the AArch64 output.
"""
import argparse
import hashlib
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
SOURCES = [ROOT / "src" / (name + ".c") for name in
           ("hash", "json", "fdt", "package", "flash", "task", "http", "entry_gate")]


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
    parser.add_argument("--test-core", action="store_true", help="also build isolated synthetic-NAND test DLL")
    parser.add_argument("--test-net", action="store_true", help="also build the in-memory Ethernet/TCP test DLL")
    parser.add_argument("--test-service", action="store_true", help="also build the isolated native HTTP service test DLL")
    parser.add_argument("--ubsan", action="store_true", help="trap undefined behavior in HOST libraries only")
    parser.add_argument("--lwip-dir", type=Path, default=ROOT.parents[1] / "uboot-an758x/lib/lwip/lwip/src",
                        help="BSD lwIP source tree only; no foreign board drivers are used")
    args = parser.parse_args()
    zig = args.zig.resolve()
    if not zig.is_file():
        parser.error("provide --zig; no compiler was found")
    build = ROOT / ".build"
    build.mkdir(exist_ok=True)
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
    if not args.host_only or args.test_service:
        html = (ROOT / "web/index.html").read_bytes()
        if len(html) > 32768:
            parser.error("single-file page exceeds 32 KiB")
        (build / "web.inc").write_text(
            "static const unsigned char bw_page[]={" + ",".join(map(str, html)) + "};\n",
            encoding="utf-8", newline="\n")
    if args.test_service:
        subprocess.run([*command, str(ROOT / "src/service.c"), '-DBW_TEST_BASE="' + fake + '"',
                        "-o", str(build / ("bootweb-service-test" + suffix))], check=True)
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
        subprocess.run([str(zig), "cc", *flags, "-target", "aarch64-freestanding-none",
                        "-ffreestanding", "-ffixed-x18", "-fno-stack-protector", "-mno-outline-atomics", "-nostdlib",
                        "-I" + str(ROOT / "adapter/lwip"), "-I" + str(lwip / "include"),
                        *map(str, net_sources),
                        *map(str, SOURCES), str(ROOT / "src/service.c"),
                        str(ROOT / "adapter/vendor_unverified.c"), str(ROOT / "adapter/entry.c"),
                        "-Wl,-T," + str(ROOT / "adapter/standalone.ld"),
                        "-Wl,-e,bootweb_command", "-o", str(elf)], check=True)
        subprocess.run([str(zig), "objcopy", "--output-target", "binary", str(elf),
                        str(build / "bootweb-unadapted.bin")], check=True)
    print("Built C core. Native output is UNADAPTED / NOT FLASHABLE; no device was contacted.")


if __name__ == "__main__":
    main()
