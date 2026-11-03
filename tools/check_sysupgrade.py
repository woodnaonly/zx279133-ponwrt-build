#!/usr/bin/env python3
"""Validate the native SK-D840N sysupgrade; export, never rewrap, its bytes.

Native OpenWrt owns the metadata/signatures. Keeping the original image intact
and deriving the maintenance files from it prevents two kernel pipelines from
shipping different compression streams again.
"""
import argparse
import gzip
import hashlib
import io
import json
from pathlib import Path
import shutil
import struct
import subprocess
import tarfile
import zlib

from sk_layout import inspect_layout

BOARD = "skyworth_sk-d840n"
PREFIX = "sysupgrade-" + BOARD
LIMITS = {"kernel": 8 * 1024 * 1024, "dtb": 1024 * 1024,
          "root": 241 * 1024 * 1024}
MAIN_IMAGE = "immortalwrt-sk-d840n-sysupgrade.bin"
EXPORTS = {"kernel": "flash-uImage", "dtb": "flash-dtb.bin",
           "root": "flash-rootfs.jffs2"}


def require(condition, message):
    if not condition:
        raise ValueError(message)


def fwtool_metadata(data):
    """Read fwtool's trailer chain, including any native signature chunk.

    fwtool's CRC starts at ~0 and has no final xor, unlike zlib's API. It
    covers the whole image preceding each 16-byte trailer, not just its JSON.
    This checks integrity, not the cryptographic validity of a signature.
    """
    end = len(data)
    signed = False
    while end >= 16:
        magic, crc, kind, size = struct.unpack(">IIB3xI", data[end - 16:end])
        require(magic == 0x46577830, "missing fwtool metadata trailer")
        require(16 <= size <= end, "invalid fwtool chunk size")
        actual = zlib.crc32(memoryview(data)[:end - 16]) ^ 0xFFFFFFFF
        require(actual == crc, "fwtool CRC mismatch")
        start = end - size
        if kind == 1:
            chunk = data[start:end - 16]
            require(len(chunk) >= 8 and chunk[:4] == bytes(4),
                    "invalid fwtool metadata header")
            metadata = json.loads(chunk[8:])
            require(metadata.get("metadata_version") == "1.1",
                    "unsupported fwtool metadata version")
            require(metadata.get("compat_version", "1.0") == "1.0",
                    "unexpected compatibility version")
            require({"skyworth,sk-d840n", "zte,133"}.issubset(
                metadata.get("supported_devices", [])),
                "missing SK-D840N compatibility identifiers")
            version = metadata.get("version", {})
            require(version.get("board") == BOARD and
                    version.get("target") == "zte/zx279133",
                    "wrong board/target in fwtool metadata")
            return metadata, signed
        require(kind == 0, "unknown fwtool chunk type")
        signed = True
        end = start
    raise ValueError("missing fwtool metadata")


def inspect_kernel(data, raw_kernel=None):
    require(len(data) >= 64, "truncated uImage header")
    fields = struct.unpack(">7I4B32s", data[:64])
    magic, hcrc, _, size, load, entry, dcrc, os_, arch, kind, comp, _ = fields
    require(magic == 0x27051956, "kernel is not a legacy uImage")
    require((os_, arch, kind, comp) == (5, 22, 2, 1),
            "kernel must be an ARM64 Linux gzip uImage")
    require(load == entry == 0x80200000, "wrong uImage load/entry address")
    require(len(data) == 64 + size, "uImage declared size does not match file")
    header = data[:4] + bytes(4) + data[8:64]
    require(zlib.crc32(header) == hcrc, "uImage header CRC mismatch")
    payload = data[64:]
    require(zlib.crc32(payload) == dcrc, "uImage data CRC mismatch")
    raw = gzip.decompress(payload)
    require(bool(raw), "empty decompressed kernel")
    if raw_kernel is not None:
        require(raw == Path(raw_kernel).read_bytes(),
                "uImage does not decompress to the built raw Image")
    return {"raw_bytes": len(raw), "raw_sha256": hashlib.sha256(raw).hexdigest()}


def inspect_image(image, raw_kernel=None):
    data = Path(image).read_bytes()
    metadata, signed = fwtool_metadata(data)
    blobs = {}
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:*") as archive:
        for member in archive:
            if member.name.rstrip("/") == PREFIX and member.isdir():
                continue
            name = member.name.removeprefix(PREFIX + "/")
            require(member.name == PREFIX + "/" + name and
                    name in {"CONTROL", *LIMITS},
                    "unexpected archive member: " + member.name)
            require(name not in blobs, "duplicate member: " + name)
            require(member.isfile(), "not a regular file: " + name)
            capacity = LIMITS.get(name, 4096)
            require(0 < member.size <= capacity,
                    f"{name}: size={member.size} capacity={capacity}: empty or oversized")
            blobs[name] = archive.extractfile(member).read()
    require(set(blobs) == {"CONTROL", *LIMITS}, "missing sysupgrade members")
    require(blobs["CONTROL"].strip() == ("BOARD=" + BOARD).encode(),
            "wrong CONTROL board")
    kernel = inspect_kernel(blobs["kernel"], raw_kernel)
    dtb = blobs["dtb"]
    layout = inspect_layout(dtb)
    require(len(blobs["root"]) <= layout["root_bytes"], "root image exceeds DTB capacity")
    require(blobs["root"][:2] == bytes.fromhex("8519"),
            "root is not a little-endian JFFS2 image")
    report = {"image": str(image), "sizes": {k: len(blobs[k]) for k in LIMITS},
              "kernel": kernel, "metadata": metadata,
              "native_signature_present": signed, "layout": layout}
    return blobs, report


def export_image(image, directory, blobs):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    for name, filename in EXPORTS.items():
        target = directory / filename
        target.write_bytes(blobs[name])
        require(target.read_bytes() == blobs[name], "export mismatch: " + filename)
    source = Path(image).resolve()
    for filename in (MAIN_IMAGE, "flash-sysupgrade.bin"):
        target = directory / filename
        if source != target.resolve():
            shutil.copyfile(source, target)
        require(target.read_bytes() == source.read_bytes(),
                "sysupgrade copy mismatch: " + filename)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--image", type=Path)
    source.add_argument("--source-dir", type=Path)
    parser.add_argument("--raw-kernel", type=Path)
    parser.add_argument("--export-dir", type=Path)
    parser.add_argument("--fwtool", type=Path,
                        help="also cross-check metadata with native host fwtool")
    args = parser.parse_args()
    try:
        image = args.image
        if args.source_dir:
            matches = sorted(args.source_dir.glob("*skyworth_sk-d840n-jffs2-128k-sysupgrade.bin"))
            require(len(matches) == 1,
                    f"expected one SK-D840N jffs2-128k sysupgrade, found {len(matches)}")
            image = matches[0]
        blobs, report = inspect_image(image, args.raw_kernel)
        if args.fwtool:
            result = subprocess.run([str(args.fwtool), "-i", "-", str(image)],
                                    check=True, capture_output=True)
            require(json.loads(result.stdout) == report["metadata"],
                    "native fwtool metadata disagrees with trailer parser")
        if args.export_dir:
            export_image(image, args.export_dir, blobs)
        print(json.dumps(report, indent=2))
        return 0
    except (ValueError, OSError, EOFError, tarfile.TarError,
            subprocess.CalledProcessError) as error:
        parser.exit(1, f"sysupgrade: {error}\n")


if __name__ == "__main__":
    raise SystemExit(main())
