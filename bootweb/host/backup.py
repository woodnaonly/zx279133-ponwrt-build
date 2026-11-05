"""Bounded native ECC backup framing and host capture; no device write API."""
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import struct
import time

HEADER, CHUNK, ERASE, FLASH = 128, 0x100000, 0x20000, 0x10000000


def decode_chunk(packet, offset, length):
    if not 0 < length <= CHUNK or offset % ERASE or length % ERASE or not 0 <= offset <= FLASH - length:
        raise ValueError("invalid expected physical backup range")
    if len(packet) < HEADER or len(packet) > HEADER + length or packet[:8] not in (b"BWROBK01", b"BWROBK02"):
        raise ValueError("truncated/unknown native ECC backup frame")
    observed, size, bitmap, header = struct.unpack_from("<IIII", packet, 8)
    if (observed, size, header) != (offset, length, HEADER):
        raise ValueError("native backup range/header mismatch")
    if bitmap >> (length // ERASE):
        raise ValueError("out-of-range bad-block bitmap")
    if packet[:8] == b"BWROBK01":
        if len(packet) != HEADER + length or any(packet[88:HEADER]):
            raise ValueError("invalid uncompressed native backup frame")
        data = packet[HEADER:]
    else:
        codec, packed = struct.unpack_from("<II", packet, 88)
        if codec != 1 or not 0 < packed < length or packed % 3 or len(packet) != HEADER + packed or any(packet[96:HEADER]):
            raise ValueError("invalid bounded RLE backup frame")
        decoded = bytearray()
        for at in range(HEADER, len(packet), 3):
            run = struct.unpack_from("<H", packet, at)[0]
            if not run or run > length - len(decoded):
                raise ValueError("RLE backup run overflows the physical chunk")
            decoded.extend(bytes([packet[at+2]]) * run)
        if len(decoded) != length:
            raise ValueError("short decoded native backup frame")
        data = bytes(decoded)
    digest = hashlib.sha256(data).hexdigest()
    if packet[24:88] != digest.encode("ascii"):
        raise ValueError("native backup SHA256 mismatch")
    bad = [offset + block * ERASE for block in range(length // ERASE) if bitmap & (1 << block)]
    for block in bad:
        if data[block-offset:block-offset+ERASE] != b"\xff" * ERASE:
            raise ValueError("bad-block placeholder is not explicitly FF")
    return data, {"offset": offset, "bytes": length, "sha256": digest, "bad_blocks": bad}


def download_backup(reader, directory, deadline, *, emit=lambda **fields: None,
                    clock=time.monotonic, size=FLASH):
    """reader(offset, length, timeout) must bind EACH response to the live UART
    token and immutable read-only mode. Never reconnect/retry a failed capture.
    A failed/partial bin is retained with capture_complete=false, not reused.
    This is ECC data, not an OOB/raw clone or proof of a working recovery chain.
    """
    if not math.isfinite(deadline) or not 0 < size <= FLASH or size % CHUNK:
        raise ValueError("invalid backup deadline/size")
    directory = Path(directory)
    directory.mkdir(exist_ok=False)
    report = {"schema": "sk-d840n-physical-ecc-backup-v1", "utc": datetime.now(timezone.utc).isoformat(),
              "expected_bytes": size, "bytes": 0, "chunks": [], "capture_complete": False,
              "full_flash_backup": False, "configuration_exported": False,
              "serial_rescue_verified": False, "nand_written": False,
              "badblock_policy": "physical-offsets-FF-placeholder-with-explicit-bitmap",
              "oob_included": False, "uncorrectable_or_short_read_policy": "fail-no-retry",
              "bad_blocks": [], "protected_repeat_pass": False}
    regions = {"boot": (0, 0x200000), "tags": (0xb00000, 0xf00000)}
    hashes = {name: hashlib.sha256() for name, (start, end) in regions.items() if end <= size}
    whole = hashlib.sha256()

    def receive(offset, length):
        left = deadline - clock()
        if left <= 10:
            raise TimeoutError("backup deadline leaves insufficient time; no new request")
        return decode_chunk(reader(offset, length, min(30, left)), offset, length)

    try:
        path = directory / "nand-physical-ecc.bin"
        with path.open("xb") as output:
            for offset in range(0, size, CHUNK):
                data, metadata = receive(offset, CHUNK)
                output.write(data)
                output.flush()
                whole.update(data)
                report["bytes"] += len(data)
                report["chunks"].append(metadata)
                report["bad_blocks"].extend(metadata["bad_blocks"])
                for name, digest in hashes.items():
                    start, end = regions[name]
                    if start <= offset < end:
                        digest.update(data)
                emit(event="backup_chunk", **metadata)
        report["sha256"] = whole.hexdigest()
        local = hashlib.sha256()
        with path.open("rb") as source:
            for data in iter(lambda: source.read(CHUNK), b""):
                local.update(data)
        if path.stat().st_size != size or local.hexdigest() != report["sha256"]:
            raise ValueError("saved backup length/SHA256 differs from received data")
        repeated = {}
        for name, original in hashes.items():
            start, end = regions[name]
            again, bad = hashlib.sha256(), []
            for offset in range(start, end, CHUNK):
                data, metadata = receive(offset, CHUNK)
                again.update(data)
                bad.extend(metadata["bad_blocks"])
            expected_bad = [block for block in report["bad_blocks"] if start <= block < end]
            if again.hexdigest() != original.hexdigest() or bad != expected_bad:
                raise ValueError("protected region changed during native ECC backup: " + name)
            repeated[name] = original.hexdigest()
        report["protected_repeat_sha256"] = repeated
        report["protected_repeat_pass"] = set(repeated) == {"boot", "tags"}
        report["capture_complete"] = True
        report["full_flash_backup"] = size == FLASH and report["protected_repeat_pass"]
        return report
    except Exception as error:
        report["error"] = str(error) or type(error).__name__
        raise
    finally:
        with (directory / "manifest.json").open("x", encoding="utf-8", newline="\n") as output:
            json.dump(report, output, ensure_ascii=False, indent=2)
            output.write("\n")
