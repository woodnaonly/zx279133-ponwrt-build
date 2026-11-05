"""Static resident-slot checks for the exact svn308776 baseline, NOT a patcher.

Proves only NAND load bounds, original copy/BSS exclusions and erased padding.
No candidate is generated; cold execution, RAM placement and rescue stay locked.
"""
import hashlib
import struct

from host.btl import STAGE1_SHA256
from host.core import BASE
from host.image import regions, STAGE2_START, STAGE2_END

LINK = 0x88000000
HOOK_OFFSET = 0xa098
SLOT_OFFSET = 0x100000
ORIGINAL_HOOK = bytes.fromhex("fd7bbfa9fd030091f9fbff97ffffff17")
DEVELOPMENT_HOOK = struct.pack("<IIQ", 0x58000050, 0xd61f0200, LINK + SLOT_OFFSET)
DESCRIPTOR_OFFSET = 0x15ff00


def resident_descriptor(bootstrap, *, profile="legacy"):
    if not bootstrap or len(bootstrap)%64 or len(bootstrap)>DESCRIPTOR_OFFSET-SLOT_OFFSET:
        raise ValueError("resident payload leaves the audited descriptor slot")
    descriptor=bytearray(256)
    descriptor[:16]=b"BW-RESIDENT-V1\0\0"
    struct.pack_into("<II",descriptor,16,1,len(bootstrap))
    profiles={"legacy":b"bootweb-resident-v1\0", "native241":b"bootweb-native241\0\0\0"}
    if profile not in profiles:
        raise ValueError("unknown resident descriptor profile")
    descriptor[24:44]=profiles[profile]
    descriptor[44:109]=hashlib.sha256(bootstrap).hexdigest().encode()+b"\0"
    return bytes(descriptor)


def add_resident_descriptor(patched,bootstrap, *, profile="legacy"):
    """Only add format metadata. This does NOT grant flash authorization."""
    descriptor=resident_descriptor(bootstrap,profile=profile)
    at=STAGE2_START+DESCRIPTOR_OFFSET
    if len(patched)!=0x200000 or patched[STAGE2_START+HOOK_OFFSET:STAGE2_START+HOOK_OFFSET+16]!=DEVELOPMENT_HOOK or patched[STAGE2_START+SLOT_OFFSET:STAGE2_START+SLOT_OFFSET+len(bootstrap)]!=bootstrap or patched[at:at+256]!=b"\xff"*256:
        raise ValueError("descriptor requires the exact constrained development patch")
    return patched[:at]+descriptor+patched[at+256:]


def _address_pair(stage2, offset, register):
    """Decode the actual AArch64 ADRP + unshifted ADD-immediate pair."""
    if offset < 0 or offset + 8 > len(stage2) or offset % 4:
        raise ValueError("truncated/unaligned address instruction pair")
    page, add = struct.unpack_from("<II", stage2, offset)
    if page & 0x9f00001f != 0x90000000 | register:
        raise ValueError("expected ADRP to the measured register")
    if add & 0xffc003ff != 0x91000000 | register << 5 | register:
        raise ValueError("expected unshifted ADD to the same register")
    immediate = ((page >> 5 & 0x7ffff) << 2) | (page >> 29 & 3)
    if immediate & 1 << 20:
        immediate -= 1 << 21
    return ((LINK + offset) & ~0xfff) + (immediate << 12) + (add >> 10 & 0xfff)


def _slot(stage2, offset, size, copy_end, bss_end):
    if not isinstance(offset, int) or not isinstance(size, int) or offset % 64 or size <= 0:
        raise ValueError("resident slot needs an aligned offset and nonempty payload")
    end = offset + size
    if not 0 <= copy_end <= bss_end <= offset < end <= STAGE2_END - STAGE2_START:
        raise ValueError("resident slot overlaps vendor image/BSS or leaves the write window")
    if end > len(stage2) or any(value != 0xff for value in stage2[offset:end]):
        raise ValueError("resident slot is not verified erased baseline padding")
    return {"stage2_relative_start": hex(offset), "stage2_relative_end": hex(end),
            "physical_start": hex(STAGE2_START + offset), "physical_end": hex(STAGE2_START + end),
            "initial_dram_start": hex(LINK + offset), "bytes": size,
            "automatically_relocated": False, "explicit_copy_required": True}


def audit_resident_slot(data, offset, size):
    parts = regions(data)
    stage2 = parts["stage2"]
    if hashlib.sha256(stage2).hexdigest() != BASE or hashlib.sha256(parts["stage1"]).hexdigest() != STAGE1_SHA256:
        raise ValueError("resident layout audit refuses an unknown vendor/BTL baseline")
    text_base, image_end, bss_start, bss_end = struct.unpack_from("<QQQQ", stage2, 8)
    if text_base != LINK:
        raise ValueError("unexpected vendor link address")
    copy_end = _address_pair(stage2, 0x1a04, 2) - LINK
    rela_start = _address_pair(stage2, 0x1a20, 2) - LINK
    rela_end = _address_pair(stage2, 0x1a28, 3) - LINK
    if not 0 < copy_end == rela_start < rela_end == image_end == bss_start < bss_end < len(stage2):
        raise ValueError("inconsistent vendor copy/relocation/BSS extents")
    if (rela_end - rela_start) % 24:
        raise ValueError("invalid vendor RELA table size")
    copied_end = (copy_end + 15) & ~15  # measured LDP/STP loop copies 16 bytes
    for at in range(rela_start, rela_end, 24):
        target, info, addend = struct.unpack_from("<QQQ", stage2, at)
        if info != 1027 or target % 8 or not LINK <= target <= LINK + copied_end - 8:
            raise ValueError("unknown/out-of-range vendor relative relocation")
    # These BSS literals are themselves relocated; match them to the header.
    if struct.unpack_from("<QQ", stage2, 0x19c8) != (LINK + bss_start, LINK + bss_end):
        raise ValueError("vendor BSS-clear literals differ from header")
    slot = _slot(stage2, offset, size, copied_end, bss_end)
    return {"schema": "sk-d840n-resident-static-layout-v1", "baseline_boot_sha256": hashlib.sha256(data).hexdigest(),
            "vendor_abi_sha256": BASE, "stage1_sha256": STAGE1_SHA256,
            "vendor_link_address": hex(LINK), "vendor_copy_end": hex(LINK + copy_end),
            "vendor_copy_end_rounded": hex(LINK + copied_end),
            "vendor_relocation_table": [hex(LINK + rela_start), hex(LINK + rela_end)],
            "vendor_relocation_count": (rela_end - rela_start) // 24,
            "vendor_bss": [hex(LINK + bss_start), hex(LINK + bss_end)],
            "btl_spi_physical_load": ["0x20000", "0x1a0000"],
            "permitted_physical_write": ["0x20000", "0x180000"], "payload_slot": slot,
            "static_space_pass": True, "cold_entry_verified": False,
            "runtime_ram_placement_verified": False, "serial_rescue_verified": False,
            "flashable_release": False, "native_write_enabled": False}


def _patch_hook_and_slot(stage2, bootstrap, copy_end, bss_end, rela_start, rela_end):
    """Mechanically constrained patch; no ability to select another hook/slot."""
    if not isinstance(bootstrap, bytes) or not bootstrap or len(bootstrap) % 64:
        raise ValueError("bootstrap must be nonempty and padded to 64 bytes")
    _slot(stage2, SLOT_OFFSET, len(bootstrap), copy_end, bss_end)
    if stage2[HOOK_OFFSET:HOOK_OFFSET+16] != ORIGINAL_HOOK:
        raise ValueError("cold-entry hook is not the exact original run_main_loop")
    if not 0 <= rela_start < rela_end <= bss_end or (rela_end-rela_start) % 24:
        raise ValueError("invalid relocation interval")
    for at in range(rela_start, rela_end, 24):
        target, info, addend = struct.unpack_from("<QQQ", stage2, at)
        if target < LINK+HOOK_OFFSET+16 and target+8 > LINK+HOOK_OFFSET:
            raise ValueError("a vendor relocation would overwrite the cold-entry hook")
    result = bytearray(stage2)
    result[HOOK_OFFSET:HOOK_OFFSET+16] = DEVELOPMENT_HOOK
    result[SLOT_OFFSET:SLOT_OFFSET+len(bootstrap)] = bootstrap
    return bytes(result)


def patch_resident_development(data, bootstrap, *, vendor_reference=None, core=None):
    """Create a NON-FLASHABLE development image for an exact baseline only.

    Vendor ABI identity and the candidate's installed hash are distinct. The
    latter is computed here and later read with ECC; never embedded into itself.
    """
    current=regions(data)
    if vendor_reference is not None:
        from host.core import Core
        core=core or Core()
        identity=core.resident_identity(current["stage2"])
        template=regions(vendor_reference)
        if hashlib.sha256(template["stage2"]).hexdigest()!=BASE:
            raise ValueError("resident rebuild requires the exact immutable vendor reference")
        # Build from verified original code, preserving CURRENT BTL/env/tail.
        # Never patch over an old resident tail, even if the new one is shorter.
        baseline=data[:STAGE2_START]+template["stage2"]+data[STAGE2_END:]
        current_hash=identity.installed_sha256.decode()
    else:
        baseline=data
        current_hash=hashlib.sha256(current["stage2"]).hexdigest()
    audit = audit_resident_slot(baseline, SLOT_OFFSET, len(bootstrap))
    stage2 = regions(baseline)["stage2"]
    start, end = (int(x,16)-LINK for x in audit["vendor_relocation_table"])
    patched = _patch_hook_and_slot(stage2, bootstrap,
        int(audit["vendor_copy_end_rounded"],16)-LINK,
        int(audit["vendor_bss"][1],16)-LINK, start, end)
    output = data[:STAGE2_START] + patched + data[STAGE2_END:]
    if len(output) != len(data) or output[:STAGE2_START] != data[:STAGE2_START] or output[STAGE2_END:] != data[STAGE2_END:]:
        raise ValueError("development patch escaped the permitted physical window")
    return output, {**audit,
        "schema": "sk-d840n-resident-development-candidate-v1",
        "source_installed_sha256":current_hash,
        "installed_sha256": hashlib.sha256(patched).hexdigest(),
        "boot_sha256": hashlib.sha256(output).hexdigest(),
        "bootstrap_sha256": hashlib.sha256(bootstrap).hexdigest(),
        "cold_hook": {"stage2_offset": hex(HOOK_OFFSET), "bytes": 16,
                      "before": ORIGINAL_HOOK.hex(), "after": DEVELOPMENT_HOOK.hex(),
                      "has_vendor_relocation": False},
        "changes_outside_stage2": False, "cold_entry_verified": False,
        "reset_verified": False, "device_entropy_verified": False,
        "normal_openwrt_boot_verified": False, "flashable_release": False,
        "native_write_enabled": False, "nand_written": False}
