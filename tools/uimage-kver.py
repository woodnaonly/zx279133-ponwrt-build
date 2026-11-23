#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Print the kernel version a uImage carries.

The flash gate has to know which kernel a pair of bytes claims to be, so
that the proof it accepts is the proof of *that* kernel and not of some
other one.  The version is taken from the kernel's own banner - "Linux
version 7.3.4 (...)" - which is the same string the running system reports
as uname -r, minus the trailing "+" that CONFIG_LOCALVERSION_AUTO adds.

The uImage is a legacy U-Boot image: a 64-byte header followed by a
compressed arm64 Image, so the banner is only visible after inflating the
body.

    python tools/uimage-kver.py <flash-uImage>
"""
import argparse
import gzip
import re
import struct
import sys
from pathlib import Path

HEADER = 64
MAGIC = 0x27051956
BANNER = re.compile(rb"Linux version ([0-9][^\x00\s]*)")


def kernel_version(path):
    """The version string in the kernel the uImage carries."""
    blob = path.read_bytes()
    if len(blob) < HEADER:
        raise ValueError("the file is shorter than a uImage header")
    magic, _, _, size, _, _, _ = struct.unpack(">7I", blob[:28])
    if magic != MAGIC:
        raise ValueError("not a legacy uImage (%08x)" % magic)
    body = blob[HEADER:HEADER + size] if size else blob[HEADER:]
    if body[:2] != b"\x1f\x8b":
        raise ValueError("the uImage body is not gzip")
    image = gzip.decompress(body)
    match = BANNER.search(image)
    if not match:
        raise ValueError("the kernel carries no version banner")
    return match.group(1).decode("utf-8", "replace")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("uimage", type=Path)
    args = parser.parse_args(argv)
    try:
        version = kernel_version(args.uimage)
    except Exception as exc:                             # noqa: BLE001
        print("uimage-kver: %s" % exc, file=sys.stderr)
        return 1
    print(version)
    return 0


if __name__ == "__main__":
    sys.exit(main())
