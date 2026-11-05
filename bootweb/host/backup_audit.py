"""Offline audit of one complete, cleanly returned native ECC capture.

No serial, network, NAND, repair or write-unlock backend exists here. A raw
capture is not a configuration export, an OOB clone or a tested rescue chain.
"""
import hashlib
import re

from host.backup import CHUNK, ERASE, FLASH
from host.btl import STAGE1_SHA256
from host.core import BASE,Core,ValidationError

REGIONS = {
    "btl": (0, 0x20000),
    "stage2": (0x20000, 0x180000),
    "env": (0x180000, 0x1a0000),
    "boot_tail": (0x1a0000, 0x200000),
    "boot": (0, 0x200000),
    "kernel": (0x200000, 0xa00000),
    "dtb": (0xa00000, 0xb00000),
    "tags": (0xb00000, 0xf00000),
    "root": (0xf00000, 0xff00000),
    "scratch": (0xff00000, FLASH),
}


def digest_valid(value):
    return isinstance(value, str) and re.fullmatch("[0-9a-f]{64}", value) is not None


def audit_stream(source, chunks, size, regions):
    """Check contiguous physical chunks and hashes again from the saved file.

    The size/range parameters allow small offline fixtures; the public capture
    audit below ALWAYS requires the entire 256 MiB physical device.
    """
    if not isinstance(size, int) or not 0 < size <= FLASH or size % CHUNK:
        raise ValueError("invalid capture size")
    if not isinstance(chunks, list) or len(chunks) != size // CHUNK:
        raise ValueError("missing/duplicate capture chunks")
    if any(not 0 <= start < end <= size for start, end in regions.values()):
        raise ValueError("region outside capture")
    whole = hashlib.sha256()
    hashes = {name: hashlib.sha256() for name in regions}
    bad = []
    for index, chunk in enumerate(chunks):
        offset = index * CHUNK
        if not isinstance(chunk, dict) or chunk.get("offset") != offset or chunk.get("bytes") != CHUNK or not digest_valid(chunk.get("sha256")):
            raise ValueError("non-contiguous/invalid physical chunk metadata")
        blocks = chunk.get("bad_blocks")
        if not isinstance(blocks, list) or any(type(block) is not int for block in blocks) or \
                blocks != sorted(set(blocks)) or any(block % ERASE or not offset <= block < offset+CHUNK for block in blocks):
            raise ValueError("invalid physical bad-block map")
        data = source.read(CHUNK)
        if len(data) != CHUNK or hashlib.sha256(data).hexdigest() != chunk["sha256"]:
            raise ValueError("saved physical chunk length/SHA256 mismatch")
        for block in blocks:
            if data[block-offset:block-offset+ERASE] != b"\xff" * ERASE:
                raise ValueError("saved bad-block placeholder is not FF")
        bad.extend(blocks)
        whole.update(data)
        for name, (start, end) in regions.items():
            left, right = max(start, offset), min(end, offset+CHUNK)
            if left < right:
                hashes[name].update(data[left-offset:right-offset])
    if source.read(1):
        raise ValueError("saved capture has trailing bytes")
    return {"bytes": size, "sha256": whole.hexdigest(), "bad_blocks": bad,
            "regions": {name: {"start": hex(start), "end": hex(end),
                               "bytes": end-start, "sha256": hashes[name].hexdigest()}
                        for name, (start, end) in regions.items()}}


def audit_capture(source, manifest, native):
    if manifest.get("schema") != "sk-d840n-physical-ecc-backup-v1" or \
            any(manifest.get(key) is not True for key in ("capture_complete", "full_flash_backup", "protected_repeat_pass")) or \
            manifest.get("expected_bytes") != FLASH or manifest.get("bytes") != FLASH or \
            manifest.get("nand_written") is not False or manifest.get("oob_included") is not False or \
            manifest.get("badblock_policy") != "physical-offsets-FF-placeholder-with-explicit-bitmap":
        raise ValueError("incomplete or unsupported physical ECC capture")
    if native.get("schema") != "sk-d840n-native-ram-web-v1" or \
            any(native.get(key) is not True for key in ("full_flash_backup", "nand_readonly_verified",
                "native_return_pass", "watchdog_verified", "alias_restored", "cache_restored")) or \
            native.get("native_write_enabled") is not False or native.get("linux_userspace_used") is not False:
        raise ValueError("capture has no clean native read-only return evidence")
    if not digest_valid(manifest.get("sha256")) or native.get("backup_sha256") != manifest["sha256"]:
        raise ValueError("capture and native-session hash are not bound")
    audit = audit_stream(source, manifest.get("chunks"), FLASH, REGIONS)
    if audit["sha256"] != manifest["sha256"] or audit["bad_blocks"] != manifest.get("bad_blocks"):
        raise ValueError("saved capture differs from completed manifest")
    for name in ("boot", "tags"):
        digest = audit["regions"][name]["sha256"]
        if manifest.get("protected_repeat_sha256", {}).get(name) != digest or any(
                native.get("nand_hashes", {}).get(phase+"/"+name) != digest for phase in ("before", "repeat", "after")):
            raise ValueError("protected native/file/repeat hashes disagree: "+name)
    # A patched image must be validated structurally, never accepted by a
    # supplied installed hash or by a copied 'known baseline' boolean.
    identity=None
    source.seek(0x20000)
    try:
        identity=Core().resident_identity(source.read(0x160000))
    except ValidationError:
        pass
    if identity is not None and identity.installed_sha256.decode()!=audit["regions"]["stage2"]["sha256"]:
        raise ValueError("stage2 identity differs from the independently streamed backup")
    audit.update({"schema": "sk-d840n-saved-ecc-audit-v1", "snapshot_audit_pass": True,
                  "vendor_abi_matches_known_baseline": identity is not None,
                  "vendor_abi_sha256":BASE if identity is not None else None,
                  "installed_sha256":identity.installed_sha256.decode() if identity is not None else None,
                  "installed_resident":identity is not None and identity.installed_sha256.decode()!=BASE,
                  "btl_matches_known_baseline": audit["regions"]["btl"]["sha256"] == STAGE1_SHA256,
                  "configuration_exported": False, "serial_rescue_verified": False,
                  "nand_written": False, "flashable_release": False, "oob_included": False})
    return audit
