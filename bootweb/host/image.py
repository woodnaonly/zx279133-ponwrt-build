"""Read-only analysis of the two known SK-D840N bootloader images.

Addresses in reports are link-time addresses, NOT verified runtime call targets.
In particular the old ubnppt.py comment misidentifies backups/mtd1_boot.bin:
that file is svn358069, not the installed community svn308776 build.
"""
from __future__ import annotations

import hashlib
import json
import re
import struct
from pathlib import Path

BOOT_SIZE = 0x200000
ERASE_SIZE = 0x20000
STAGE2_START = 0x20000
STAGE2_END = 0x180000
ENV_START = 0x180000
ENV_END = 0x1A0000
LINK_DELTA = 0x87FE0000
BOARD = "skyworth_sk-d840n"


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def require(ok: bool, message: str) -> None:
    if not ok:
        raise ValueError(message)


def regions(data: bytes) -> dict[str, bytes]:
    require(len(data) == BOOT_SIZE, "boot image must be exactly 2 MiB")
    return {"stage1": data[:STAGE2_START],
            "stage2": data[STAGE2_START:STAGE2_END],
            "environment": data[ENV_START:ENV_END],
            "tail": data[ENV_END:]}


def cstring(data: bytes, address: int, limit: int = 256, whitespace: bool = False) -> str | None:
    off = address - LINK_DELTA
    if not STAGE2_START <= off < STAGE2_END:
        return None
    end = data.find(b"\0", off, min(len(data), off + limit))
    if end < 0:
        return None
    raw = data[off:end]
    if not raw or any((c < 0x20 or c > 0x7E) and not (whitespace and c in (9, 10, 13)) for c in raw):
        return None
    return raw.decode("ascii")


def command_candidates(data: bytes) -> list[dict]:
    """Find structurally valid ARM64 cmd_tbl entries; do not infer their ABI.

    This binary uses the cmd_rep form, NOT the old integer repeatable form.
    The dispatch at svn308776 file+0x32c98 calls record+0x10; its repeat
    wrapper loads the implementation from record+0x18. Keep both addresses.
    """
    result = []
    for off in range(STAGE2_START, min(STAGE2_END, len(data)) - 56, 8):
        name, maxargs, padding, wrapper, handler, usage, help_, complete = struct.unpack_from("<QIIQQQQQ", data, off)
        if not 1 <= maxargs <= 255 or padding != 0:
            continue
        name_text = cstring(data, name, 40)
        if not name_text or not re.fullmatch(r"[A-Za-z0-9_.?+\-]{1,32}", name_text):
            continue
        handler_off = handler - LINK_DELTA
        wrapper_off = wrapper - LINK_DELTA
        if (handler % 4 or not STAGE2_START <= handler_off < 0x90000 or
                wrapper % 4 or not STAGE2_START <= wrapper_off < 0x90000):
            continue
        usage_text = cstring(data, usage, 1024, whitespace=True)
        if usage_text is None:
            continue
        result.append({"name": name_text, "record_offset": f"0x{off:x}",
                       "handler_link_address": f"0x{handler:x}",
                       "handler_file_offset": f"0x{handler_off:x}",
                       "wrapper_link_address": f"0x{wrapper:x}",
                       "maxargs": maxargs, "record_bytes": 56,
                       "usage": usage_text,
                       "evidence": "static-structure-only; runtime ABI unverified"})
    return result


def analyze(data: bytes, profile: dict | None = None) -> dict:
    parts = regions(data)
    banners = sorted(set(x.decode("ascii") for x in re.findall(
        rb"U-Boot 20[0-9]{2}\.[0-9]{2}[A-Za-z0-9.\-]* \([^\x00\r\n)]{1,100}\)", data)))
    commands = command_candidates(data)
    matches = profile is not None and sha256(parts["stage2"]) == profile.get("stage2_sha256")
    return {"board": BOARD, "bytes": len(data), "image_sha256": sha256(data),
            "region_sha256": {key: sha256(value) for key, value in parts.items()},
            "banners": banners, "link_delta": f"0x{LINK_DELTA:x}",
            "recognized_code": matches, "commands": commands,
            "command_prefix_sha256": {cmd["name"]: sha256(data[int(cmd["handler_file_offset"], 16):int(cmd["handler_file_offset"], 16)+64]) for cmd in commands},
            "native_write_enabled": False,
            "runtime_verified": False,
            "warnings": ["Static command candidates are not a safe function-call table.",
                         "BootROM/BTL container validation and relocated addresses still require verification."]}


def load_profile(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    require(value.get("board") == BOARD, "wrong profile board")
    require(re.fullmatch(r"[0-9a-f]{64}", value.get("stage2_sha256", "")) is not None,
            "profile has no valid code fingerprint")
    return value
