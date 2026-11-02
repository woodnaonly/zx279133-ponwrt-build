#!/usr/bin/env python3
"""Hard 4 MiB fallback size gate; does not carve a partition or certify a FIT."""
import argparse
import json
from pathlib import Path
import struct

PARTITION = 4 * 1024**2
ERASE_BLOCK = 128 * 1024
MAX_IMAGE = PARTITION - 2 * ERASE_BLOCK


def check(path):
    path = Path(path)
    size = path.stat().st_size
    with path.open("rb") as source:
        head = source.read(40)
    if len(head) < 40 or struct.unpack_from(">I", head)[0] != 0xd00dfeed:
        raise ValueError("not a FIT/FDT header")
    declared = struct.unpack_from(">I", head, 4)[0]
    if not 40 <= declared <= size:
        raise ValueError("truncated FIT/FDT")
    report = {"image_bytes": size, "partition_bytes": PARTITION, "maximum_image_bytes": MAX_IMAGE,
              "reserved_erase_blocks": 2, "size_pass": size <= MAX_IMAGE,
              "hardware_boot_verified": False, "partition_creation_allowed": False,
              "flashable_release": False,
              "note": "Size only; FIT hashes/configurations, RAM boot and rescue entry still require validation."}
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("image", type=Path)
    args = parser.parse_args()
    try:
        report = check(args.image)
        print(json.dumps(report, indent=2))
        return 0 if report["size_pass"] else 1
    except (OSError, ValueError) as error:
        parser.exit(1, f"recovery size: {error}\n")


if __name__ == "__main__":
    raise SystemExit(main())
