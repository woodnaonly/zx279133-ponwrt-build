#!/usr/bin/env python3
"""Print a redacted static bootloader report; never execute or flash its input."""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from host.image import analyze, load_profile


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("image", type=Path)
    parser.add_argument("--profile", type=Path)
    parser.add_argument("--output", type=Path, help="write a local static JSON report, never a flash image")
    args = parser.parse_args()
    try:
        profile = load_profile(args.profile) if args.profile else None
        report = json.dumps(analyze(args.image.read_bytes(), profile), indent=2, ensure_ascii=False)
        if args.output:
            with args.output.open("x", encoding="utf-8", newline="\n") as output:
                output.write(report + "\n")
            print(str(args.output.resolve()))
        else:
            print(report)
    except (ValueError, OSError) as error:
        parser.exit(1, f"audit: {error}\n")


if __name__ == "__main__":
    main()
