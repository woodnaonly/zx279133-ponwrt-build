#!/usr/bin/env python3
"""Create/inspect candidate packages. Does NOT produce a flashable release."""
import argparse
import hashlib
import io
import json
from pathlib import Path
import sys
import tarfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from host.core import BASE, Core, LAYOUTS, ValidationError, read_package
from host.image import regions


def make_uboot(current, candidate, *, core=None):
    # Programmatic tests may inject the separately marked synthetic core.
    # The CLI ALWAYS uses Core() and exposes no test-base override.
    core = core or Core()
    old, new = regions(current), regions(candidate)
    if hashlib.sha256(old["stage2"]).hexdigest() != core.base:
        raise ValidationError("current boot dump is not the adapted community code base")
    for name in ("stage1", "environment", "tail"):
        if old[name] != new[name]:
            raise ValidationError("candidate changes protected boot region: " + name)
    payload = new["stage2"]
    digest = hashlib.sha256(payload).hexdigest()
    control = ("FORMAT=sk-d840n-uboot-v1\nBOARD=skyworth_sk-d840n\n"
               f"BASE_SHA256={core.base}\nPAYLOAD_SHA256={digest}\nPAYLOAD_BYTES={len(payload)}\n").encode()
    sums = (hashlib.sha256(control).hexdigest() + "  CONTROL\n" + digest + "  uboot.bin\n").encode()
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w", format=tarfile.USTAR_FORMAT) as archive:
        for name, data in (("CONTROL", control), ("uboot.bin", payload), ("sha256sums", sums)):
            member = tarfile.TarInfo(name)
            member.size, member.mode, member.mtime = len(data), 0o644, 0
            archive.addfile(member, io.BytesIO(data))
    package = output.getvalue()
    core.validate(package)
    return package


def write_candidate(path, package):
    """Exclusive creation only; validation must precede this local file write."""
    with Path(path).open("xb") as output:
        output.write(package)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    inspect = commands.add_parser("inspect")
    inspect.add_argument("image", type=Path)
    inspect.add_argument("--base", default=BASE)
    inspect.add_argument("--layout", choices=LAYOUTS, default="current240")
    make = commands.add_parser("make-uboot")
    make.add_argument("--current-boot", required=True, type=Path, help="full 2 MiB current readback")
    make.add_argument("--candidate-boot", required=True, type=Path, help="full 2 MiB candidate with protected bytes unchanged")
    make.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    try:
        core = Core()
        if args.command == "inspect":
            report = core.validate(read_package(args.image), base=args.base, layout=args.layout).report()
        else:
            package = make_uboot(args.current_boot.read_bytes(), args.candidate_boot.read_bytes())
            # Never overwrite a user's existing package.
            write_candidate(args.output, package)
            report = core.validate(package).report()
            report["candidate"] = str(args.output.resolve())
            report["warning"] = "Vendor container/BTL loading and RAM/serial rescue remain unverified. NOT FLASHABLE."
        print(json.dumps(report, indent=2, ensure_ascii=False))
    except (ValueError, OSError, RuntimeError) as error:
        parser.exit(1, f"bootweb: {error}\n")


if __name__ == "__main__":
    main()
