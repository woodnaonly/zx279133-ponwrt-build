#!/usr/bin/env python3
"""Rehash a completed live ECC backup; record exact protected-region hashes."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from host.backup_audit import audit_capture


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--capture-dir", type=Path, required=True, help="one completed ram_web output directory")
    parser.add_argument("--output", type=Path, required=True, help="new audit JSON; never overwrites")
    args = parser.parse_args()
    try:
        native = json.loads((args.capture_dir / "report.json").read_text(encoding="utf-8"))
        backup = args.capture_dir / "nand-backup"
        manifest = json.loads((backup / "manifest.json").read_text(encoding="utf-8"))
        with (backup / "nand-physical-ecc.bin").open("rb") as source:
            report = audit_capture(source, manifest, native)
        report["utc"] = datetime.now(timezone.utc).isoformat()
        report["capture_directory"] = str(args.capture_dir.resolve())
        with args.output.open("x", encoding="utf-8", newline="\n") as output:
            json.dump(report, output, indent=2)
            output.write("\n")
    except (OSError, ValueError, TypeError) as error:
        parser.exit(1, str(error) + "\n")
    print(args.output.resolve())
    print("Saved ECC capture only; no configuration export or tested recovery/write permission.")


if __name__ == "__main__":
    main()
