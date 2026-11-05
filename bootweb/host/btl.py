"""Read-only BTL boot-selection observations; never a rescue/write unlock.

The three status registers are read by the pinned stage-1 code itself. Their
values observed from U-Boot do not prove a hardware entry path into BTL, nor
that a UART rescue image can run. No boot-mode or security register is written.
"""
import struct

from host.uart import TrialError, parse_md

STAGE1_SHA256 = "d4d7a4bf90fc26cc61c76000bb272fac3113c14525ab7119c3d10fbd477231f4"
SELECTORS = (("boot_mode", 0x10e10170), ("security_ready", 0x14f11080),
             ("security_selector", 0x14f1101c))


def inspect_boot_selectors(console):
    if not console.identified or not console.prompt or console.transferring:
        raise TrialError("BTL status reads require a freshly identified U-Boot prompt")
    values, reads = {}, []
    for name, address in SELECTORS:
        if name == "security_selector" and not values["security_ready"]:
            break  # Do not spin on hardware readiness or read an unready selector.
        response = console.inspect(f"md.b {address:x} 4")
        data = parse_md(response, address, 4)
        values[name] = struct.unpack("<I", data)[0]
        reads.append({"name": name, "address": hex(address), "hex": data.hex()})
    mode = values["boot_mode"] & 3
    secure = bool(values["security_selector"] & 1) if values["security_ready"] else None
    return {"schema": "sk-d840n-btl-status-observation-v1",
            "expected_stage1_sha256": STAGE1_SHA256,
            "reads": reads, "boot_media_bits": mode,
            "boot_media_interpretation": {0: "uart", 1: "nand-controller", 2: "fallback", 3: "spi-loader"}[mode],
            "static_boot_dispatch_target": {0: "0x2ea0", 1: "0x242c", 2: None, 3: "0x1f54"}[mode],
            "secure_selector_ready": bool(values["security_ready"]),
            "secure_container_selected": secure,
            "serial_rescue_verified": False, "cold_power_timestamp_verified": False,
            "reset_time_selector_verified": False,
            "native_write_enabled": False,
            "note": "Status snapshot only; no boot-mode write, rescue entry or execution was attempted"}
