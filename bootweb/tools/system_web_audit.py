#!/usr/bin/env python3
"""Read-only audit of an already executed native241 Web system update.

Reads saved UART evidence and the current HTTP state. Never opens a UART,
sends a POST, retries an operation, writes NAND, or requests a reboot.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import http.client
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from host.core import Core, read_package


def require(value, reason):
    if not value:
        raise ValueError(reason)


def audit(observe, resident, ready, installation, package):
    data = read_package(package)
    package_hash = hashlib.sha256(data).hexdigest()
    require(package_hash == ready["package_sha256"], "package differs from approved upload")
    image = (resident / "resident-boot-development.bin").read_bytes()
    manifest = json.loads((resident / "manifest.json").read_text(encoding="utf-8"))
    core = Core()
    identity = core.resident_identity(image[0x20000:0x180000])
    installed = identity.installed_sha256.decode()
    require(installed == manifest["installed_sha256"] == ready["resident_installed_sha256"] ==
            installation["candidate_and_readback_sha256"], "installed image binding differs")
    result = core.validate_identity(data, identity, layout="native241")
    spans = list(result.writes[:result.count])
    require([s.name.decode() for s in spans] == ["root", "dtb", "kernel"], "wrong write order")
    live = json.loads((observe / "live-ready.json").read_text(encoding="utf-8"))
    require(live["fresh_boot_observed"] is True and live["mode"] == "reset-web" and
            live["uart_bound_web"]["installed_sha256"] == installed, "fresh cold Web binding missing")
    raw = (observe / "rx.bin").read_bytes()
    text = raw.decode("ascii", errors="replace")
    tokens = re.findall(r"BW_RESIDENT_SESSION token=([0-9a-f]{64})", text)
    require(len(tokens) == 1 and hashlib.sha256(tokens[0].encode()).hexdigest() ==
            ready["device_session_token_sha256"], "UART session changed or unbound")
    require("BW_RESIDENT_RESET entry=1 raw_bit=0 hold_ms=3000 gpio_written=0" in text,
            "physical Reset entry evidence missing")
    prepared = re.findall(r"BW_RESIDENT_SYSTEM_PREPARED .*package_sha256=([0-9a-f]{64})", text)
    require(prepared == [package_hash], "missing/repeated/different accepted package")
    require(not re.search(r"BW_(?:INSTALL_ECC_ERROR|BOUNDED_SPI_ERROR|BOUNDED_SPI_DENIED|"
                          r"NAND_PREFLIGHT_DENIED|RESIDENT_TASK_FINISHED success=0)", text),
            "native error or failed task was reported")
    operations = re.findall(r"BW_NAND_REAL_END ([^\r\n]+)", text)
    erases, page_samples = [], []
    for operation in operations:
        match = re.fullmatch(r"rc=0 kind=(erase|page) offset=([0-9a-f]+) wren=1 "
                             r"loads=([01]) executes=1 hardware_mutations=1", operation)
        require(match is not None, "incomplete/failed native write operation")
        kind, offset, loads = match.groups()
        require(loads == ("0" if kind == "erase" else "1"), "invalid NAND gate state")
        (erases if kind == "erase" else page_samples).append(int(offset, 16))
    expected_erases, expected_samples = [], []
    targets = []
    for index, span in enumerate(spans, 1):
        name, digest = span.name.decode(), span.sha256.decode()
        expected_erases.extend(range(span.offset, span.offset + span.capacity, 0x20000))
        payload = data[span.data_offset:span.data_offset + span.length]
        for offset in range(0, span.length, 0x20000):
            if any(value != 255 for value in payload[offset:offset + 2048]):
                expected_samples.append(span.offset + offset)
        for phase in ("image-verified", "tail-verified"):
            marker = (f"BW_RESIDENT_VERIFIED phase={phase} part={name} offset={span.offset:x} "
                      f"capacity={span.capacity} length={span.length} sha256={digest} compared_by_core=1")
            require(text.count(marker) == 1, f"{name} {phase} evidence missing/repeated")
        for phase in ("write", "verify"):
            require(re.search(rf"BW_RESIDENT_PROGRESS phase={phase} done={span.length} "
                              rf"total={span.length} uptime_ms=\d+", text), f"{name} {phase} incomplete")
        tail = [int(value) for value in re.findall(
            rf"BW_RESIDENT_PROGRESS phase=verify-tail done=(\d+) total={span.capacity} uptime_ms=\d+", text)]
        expected_tail = list(range(span.length + 0x20000, span.capacity, 0x20000))
        if span.length < span.capacity:
            expected_tail.append(span.capacity)
        require(tail == expected_tail, f"{name} erased-tail coverage incomplete")
        targets.append({"name": name, "offset": f"0x{span.offset:08x}", "capacity": span.capacity,
                        "length": span.length, "candidate_and_ecc_readback_sha256": digest,
                        "image_readback_pass": True, "unused_tail_bytes": span.capacity - span.length,
                        "entire_unused_tail_ff_pass": True})
    require(erases == expected_erases, "actual erase sequence does not exactly cover system targets")
    require(page_samples == expected_samples, "block-first-page hardware samples differ from package")
    hardware = re.findall(r"BW_NAND_REAL_HARDWARE ([^\r\n]+)", text)
    require(hardware == ["attempts=1 controller_completed=1"] * len(operations),
            "sampled controller completion evidence missing or failed")
    boot_hash = manifest["boot_sha256"]
    tags_hash = installation["protected_regions_unchanged"]["parameter_tags"]
    protected = (f"BW_RESIDENT_PROTECTED boot_bytes=2097152 boot_sha256={boot_hash} "
                 f"tags_sha256={tags_hash} unchanged=1 compared_by_core=1")
    require(text.count(protected) == 1, "whole BOOT/parameter protected audit missing or changed")
    finish = ("BW_RESIDENT_TASK_FINISHED success=1 further_writes_locked=1 "
              "automatic_boot=0 manual_reboot_allowed=1")
    require(text.count(finish) == 1 and "BW_RESIDENT_PROGRESS phase=complete done=3 total=3 " in text,
            "final device success evidence missing or repeated")
    wire = http.client.HTTPConnection("192.168.9.1", timeout=8)
    try:
        wire.request("GET", "/api/state", headers={"Host": "192.168.9.1", "Connection": "close"})
        response = wire.getresponse()
        body = response.read(65537)
        require(response.status == 200 and len(body) <= 65536, "final HTTP state unavailable/too large")
        state = json.loads(body)
    finally:
        wire.close()
    require(state.get("token") == tokens[0] and state.get("board") == "skyworth_sk-d840n" and
            state.get("installed_sha256") == installed and state.get("mode") == "native-resident" and
            state.get("layout") == "native241" and
            state.get("power_mode") == "continuous_required" and state.get("id") == ready["task_id"] and
            state.get("state") == "success" and state.get("error") == "" and
            state.get("can_confirm") is False and state.get("can_reboot") is True and
            state.get("result", {}).get("sha256") == package_hash, "final HTTP/UART task binding failed")
    state["token"] = "<device-session-token>"
    release = re.search(r"BW_INSTALL_NAND_RELEASE rc=0 reads=(\d+) corrected_reads=(\d+) "
                        r"erases=(\d+) pages=(\d+) skipped_ff=(\d+) .*failed=0", text)
    return {"schema": "sk-d840n-native241-system-web-evidence-audit-v1",
            "utc": datetime.now(timezone.utc).isoformat(), "audit_pass": True,
            "verification_path": "On-device C core ECC/hash/FF comparison; host audits saved UART and current HTTP state",
            "installed_resident_sha256": installed, "package_sha256": package_hash,
            "actual_system_write_and_readback_pass": True, "targets": targets,
            "actual_erase_blocks": len(erases), "erase_order_and_exact_boundaries_pass": True,
            "block_first_page_hardware_samples": len(page_samples),
            "page_samples_are_not_total_program_page_count": True,
            "native_release_statistics_available": release is not None,
            "corrected_reads": int(release[2]) if release else None,
            "observed_native_error_markers": 0,
            "whole_boot_sha256_unchanged": boot_hash, "parameter_tags_sha256_unchanged": tags_hash,
            "protected_audit_pass": True, "final_http_state": state,
            "uart_snapshot_bytes": len(raw), "uart_snapshot_sha256": hashlib.sha256(raw).hexdigest(),
            "ram_payload_loaded": False, "reboot_requested_by_auditor": False,
            "cold_system_boot_verified": False, "post_install_bootweb_reentry_verified": False,
            "overall_plan_complete": False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("observe-dir", "resident-dir", "ready-report", "installation-record", "package", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    args = parser.parse_args()
    try:
        ready = json.loads(args.ready_report.read_text(encoding="utf-8"))
        installation = json.loads(args.installation_record.read_text(encoding="utf-8"))
        report = audit(args.observe_dir, args.resident_dir, ready, installation, args.package)
        with args.output.open("x", encoding="utf-8", newline="\n") as output:
            json.dump(report, output, ensure_ascii=False, indent=2)
            output.write("\n")
    except (OSError, ValueError, KeyError, TypeError, http.client.HTTPException) as error:
        parser.exit(1, str(error) + "\n")
    print(args.output.resolve())
    print("Actual Web write/ECC/tail/protection evidence passed; cold system boot and re-entry still required.")


if __name__ == "__main__":
    main()
