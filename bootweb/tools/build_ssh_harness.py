#!/usr/bin/env python3
"""Build a static AArch64 Linux READ-ONLY harness. Does not contact a device.

Not a recovery kernel, U-Boot adapter or flashable image; no write capability.
"""
import argparse
from pathlib import Path
import subprocess

from build import ROOT, SOURCES


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--zig", type=Path, default=ROOT / ".build/toolchain/zig-x86_64-windows-0.14.1/zig.exe")
    args = parser.parse_args()
    build = ROOT / ".build"
    build.mkdir(exist_ok=True)
    page = (ROOT / "web/index.html").read_bytes()
    if len(page) > 32768:
        parser.error("page exceeds 32 KiB")
    (build / "web.inc").write_text("static const unsigned char bw_page[]={" + ",".join(map(str, page)) + "};\n",
                                   encoding="utf-8", newline="\n")
    output = build / "bootweb-linux-readonly"
    subprocess.run([str(args.zig.resolve()), "cc", "-target", "aarch64-linux-musl", "-static", "-std=c11",
                    "-O2", "-Wall", "-Wextra", "-Werror", "-fno-builtin", "-D_POSIX_C_SOURCE=200809L",
                    "-I" + str(ROOT / "include"), "-I" + str(ROOT / "src"), "-I" + str(build),
                    *map(str, SOURCES), str(ROOT / "src/service.c"), str(ROOT / "tests/device/ssh_harness.c"),
                    "-o", str(output)], check=True)
    print(output)
    print("Linux userspace test only; all flash and reboot capabilities are disabled.")


if __name__ == "__main__":
    main()
