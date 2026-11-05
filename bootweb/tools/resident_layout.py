#!/usr/bin/env python3
"""Audit an existing ELF/bin against stock NAND padding; never patch or flash."""
import argparse
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from audit_ram_elf import verify_payload
from host.resident import audit_resident_slot


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-boot", type=Path, required=True, help="exact full baseline, not a substitute for a fresh device backup")
    parser.add_argument("--elf", type=Path, required=True)
    parser.add_argument("--bin", type=Path, required=True)
    parser.add_argument("--payload-offset", type=lambda value: int(value, 0), required=True,
                        help="stage2-relative offset, checked against actual baseline padding")
    parser.add_argument("--output", type=Path, required=True, help="new evidence file; never overwrites")
    args = parser.parse_args()
    try:
        payload, elf = args.bin.read_bytes(), args.elf.read_bytes()
        audit = verify_payload(elf, payload)
        report = audit_resident_slot(args.base_boot.read_bytes(), args.payload_offset, len(payload))
        report["instruction_audit"] = audit
        report["payload_sha256"] = hashlib.sha256(payload).hexdigest()
        with args.output.open("x", encoding="utf-8", newline="\n") as output:
            json.dump(report, output, indent=2)
            output.write("\n")
    except (OSError, ValueError) as error:
        parser.exit(1, str(error) + "\n")
    print(args.output.resolve())
    print("Static space/instructions only. Explicit cold-copy entry is still unverified; NOT FLASHABLE.")


if __name__ == "__main__":
    main()
