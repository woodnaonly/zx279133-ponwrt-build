#!/usr/bin/env python3
"""Observe an actually installed native241 resident after a NEW power-on.

Never loads RAM code, resets, changes environment or sends flash commands.
Serial-web invokes only the verified installed bootweb command. Reset-web is
passive and requires a real physical hold. Browser writes remain independent.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import http.client
import json
from pathlib import Path
import re
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from host.core import Core, BASE
from host.resident_runtime import command_record
from host.uart import Console, TrialError, open_serial, parse_md
from build import RAM_PINS
from ram_probe import field
from resident_cold_trial import terminal_plain


VERSION = "bootweb-resident-native241-v1"
BOARD = "skyworth_sk-d840n"
INFO_COMMAND = "printf 'BW_COLD_INFO_BEGIN\\n'; uname -a; ubus call system board; cat /etc/openwrt_release; cat /proc/mtd; ip -4 addr show; printf 'BW_COLD_INFO_END\\n'"


def installed_binding(manifest_raw, candidate, installation):
    manifest = json.loads(manifest_raw)
    if (len(candidate) != 0x200000 or
            hashlib.sha256(candidate[0x20000:0x180000]).hexdigest() != manifest.get("installed_sha256") or
            hashlib.sha256(candidate).hexdigest() != manifest.get("boot_sha256") or
            manifest.get("native241_service") is not True or
            manifest.get("power_mode") != "continuous_required" or
            installation.get("installer_actual_pass") is not True or
            installation.get("actual_write_and_readback_pass") is not True or
            installation.get("protected_audit_pass") is not True or
            installation.get("candidate_sha256") != manifest.get("installed_sha256") or
            installation.get("resident_manifest_sha256") != hashlib.sha256(manifest_raw).hexdigest()):
        raise TrialError("no exact-candidate actual installation/readback/protection binding")
    return manifest


def verify_web_state(state, token, installed):
    if (state.get("board") != BOARD or state.get("vendor_abi_sha256") != BASE or
            state.get("mode") != "native-resident" or state.get("resident") is not True or
            state.get("version") != VERSION or state.get("layout") != "native241" or
            state.get("power_mode") != "continuous_required" or
            state.get("token") != token or state.get("installed_sha256") != installed or
            state.get("can_reboot") is not False or state.get("state") != "idle"):
        raise TrialError("HTTP state is not this fresh UART-bound installed native241 session")
    return state


def main():
    p = argparse.ArgumentParser(description=__doc__)
    for name in ("resident-dir", "installer-report", "output-dir"):
        p.add_argument("--" + name, type=Path, required=True)
    p.add_argument("--mode", choices=("normal", "reset-web", "serial-web"), required=True)
    p.add_argument("--confirm-continuous-power", action="store_true")
    p.add_argument("--port", default="COM3")
    p.add_argument("--max-seconds", type=int, default=28800)
    p.add_argument("--boot-wait-seconds", type=int, default=600,
                   help="passive wait for a new physical power-on; normal Linux boot is separately limited to 300 seconds")
    a = p.parse_args()
    if not a.confirm_continuous_power:
        p.error("continuous power must remain confirmed throughout observation/upgrade")
    if not 600 <= a.max_seconds <= 43200:
        p.error("observation maximum must be 600..43200 seconds")
    if not 600 <= a.boot_wait_seconds <= 43200:
        p.error("physical power-on wait must be 600..43200 seconds")
    a.output_dir.mkdir(parents=True, exist_ok=False)
    report = {"schema": "sk-d840n-installed-native241-observation-v1",
              "utc": datetime.now(timezone.utc).isoformat(), "mode": a.mode,
              "ram_payload_loaded": False, "flash_commands_sent": False,
              "reset_command_sent": False, "observation_pass": False,
              "system_update_acceptance_claimed": False, "events": []}
    port = None
    start = time.monotonic()
    tail = bytearray()
    pending = bytearray()
    native = bytearray()
    post_entry = bytearray()
    token = None
    fresh = False
    status = 1
    with (a.output_dir / "rx.bin").open("xb") as raw, \
            (a.output_dir / "events.jsonl").open("x", encoding="utf-8", newline="\n") as log:
        def emit(event, **fields):
            item = {"event": event, "elapsed": round(time.monotonic() - start, 3), **fields}
            report["events"].append(item)
            line = json.dumps(item, ensure_ascii=False)
            log.write(line + "\n"); log.flush(); print(line, flush=True)

        def record(data):
            nonlocal token, fresh
            raw.write(data); raw.flush()
            if token is not None:
                post_entry.extend(data)
                if Console.BOOT.search(post_entry) or any(x in post_entry for x in
                        (b"Resetting CPU", b"Synchronous Abort", b"Linux version")):
                    raise TrialError("resident UART generation changed; no command or confirmation replay")
                if len(post_entry) > 1024:
                    del post_entry[:-1024]
            tail.extend(data)
            if len(tail) > 0x20000:
                del tail[:-0x20000]
            if not fresh and Console.BOOT.search(tail):
                fresh = True
                report["fresh_power_on_utc"] = datetime.now(timezone.utc).isoformat()
                emit("fresh_boot_observed", no_old_commands_replayed=True)
            pending.extend(data)
            while b"\n" in pending:
                line, _, rest = pending.partition(b"\n"); pending[:] = rest
                line = line.strip(b"\r")
                if not line.startswith(b"BW_"):
                    continue
                native.extend(line + b"\n")
                if len(native) > 0x200000:
                    raise TrialError("native observation exceeded bounded evidence capture")
                match = re.fullmatch(rb"BW_RESIDENT_SESSION token=([0-9a-f]{64})", line)
                if match:
                    if token is not None:
                        raise TrialError("new/repeated native session; no resume or replay")
                    token = match[1].decode("ascii")
                    emit("device_session", token_sha256=hashlib.sha256(match[1]).hexdigest())
                else:
                    emit("native_progress", text=line.decode("ascii", errors="replace"))
            if len(pending) > 8192:
                del pending[:-8192]

        try:
            manifest_raw = (a.resident_dir / "manifest.json").read_bytes()
            candidate = (a.resident_dir / "resident-boot-development.bin").read_bytes()
            installation = json.loads(a.installer_report.read_text(encoding="utf-8"))
            manifest = installed_binding(manifest_raw, candidate, installation)
            identity = Core().resident_identity(candidate[0x20000:0x180000])
            installed = identity.installed_sha256.decode()
            if installed != manifest["installed_sha256"]:
                raise TrialError("candidate production identity changed")
            report["installed_sha256"] = installed
            report["resident_manifest_sha256"] = hashlib.sha256(manifest_raw).hexdigest()
            port = open_serial(a.port)
            console = Console(port, emit=emit, raw=record, continuous_power_confirmed=True)
            emit("serial_open", dtr=False, rts=False, queued_commands=False)
            if a.mode == "serial-web":
                console.wait_boot_prompt(600)
                _, _, board = console.identify()
                if field(board, "relocaddr") != 0x9ff3c000 or field(board, "reloc off") != 0x17f3c000:
                    raise TrialError("fresh resident relocation changed")
                name, expected = command_record(candidate, 0x9ff3c000, resident_dir=a.resident_dir)
                if name != "bootweb" or parse_md(console.inspect("md.b 9ffd2c50 38"), 0x9ffd2c50, 56) != expected:
                    raise TrialError("fresh installed bootweb command differs from exact ELF")
                for off, length in RAM_PINS:
                    at = 0x9ff3c000 + off
                    if parse_md(console.inspect(f"md.b {at:x} {length:x}", timeout=15), at, length) != candidate[0x20000+off:0x20000+off+length]:
                        raise TrialError("fresh installed instruction fingerprint mismatch")
                for cache in ("icache", "dcache"):
                    if "Cache is ON" not in console.inspect(cache):
                        raise TrialError("fresh installed cache state is not normal")
                console.prompt = False
                duration = manifest.get("payload_build_policy", {}).get("default_service_timeout_ms", 180000)
                if duration not in (180000, 1800000):
                    raise TrialError("installed service duration is outside verified build policies")
                command = f"bootweb {duration}"
                emit("installed_command_started", command=command, ram_loading=False)
                console.write(command.encode("ascii") + b"\r")
                tail.clear()
            deadline = time.monotonic() + (a.boot_wait_seconds + 300 if a.mode == "normal" else a.max_seconds)
            boot_limit = time.monotonic() + a.boot_wait_seconds
            normal_boot_end = None
            ready = False
            activated = False
            heartbeat = time.monotonic()
            while time.monotonic() < deadline:
                console.read()
                if not fresh:
                    if time.monotonic() >= boot_limit:
                        raise TrialError("no new power-on observed; no commands sent")
                    continue
                if a.mode == "normal":
                    if normal_boot_end is None:
                        normal_boot_end = time.monotonic() + 300
                    if time.monotonic() >= normal_boot_end:
                        raise TrialError("new normal Linux boot exceeded 300 seconds")
                    if b"Starting kernel" in tail and b"Please press Enter to activate this console." in tail and not activated:
                        console.write(b"\r"); activated = True
                    if activated and re.search(rb"(?:^|[\r\n])root@[^\r\n]*:[^\r\n]*# ?$", terminal_plain(tail)):
                        console.write(INFO_COMMAND.encode("ascii") + b"\r")
                        answer = bytearray(); info_end = time.monotonic() + 30
                        while time.monotonic() < info_end:
                            answer.extend(console.read())
                            if len(answer) > 0x40000:
                                raise TrialError("Linux information exceeded bounded capture")
                            if Console.BOOT.search(answer):
                                raise TrialError("boot changed during read-only Linux observation")
                            if re.search(rb"(?:^|[\r\n])BW_COLD_INFO_END\r?\r?\n", answer):
                                (a.output_dir / "linux-info.txt").write_bytes(answer)
                                report["normal_linux_boot_pass"] = True
                                report["observation_pass"] = True; status = 0
                                break
                        else:
                            raise TrialError("read-only Linux information timed out")
                        break
                else:
                    if b"Starting kernel" in tail or b"Linux version" in tail:
                        raise TrialError("kernel stage observed instead of requested resident Web; no input")
                    if b"BW_RESIDENT_NETWORK_STARTED " in native and not ready:
                        if not token or (a.mode == "reset-web" and b"BW_RESIDENT_RESET entry=1 " not in native):
                            raise TrialError("physical Reset/device entropy evidence missing")
                        wire = http.client.HTTPConnection("192.168.9.1", timeout=8)
                        try:
                            wire.request("GET", "/api/state", headers={"Host": "192.168.9.1", "Connection": "close"})
                            reply = wire.getresponse(); body = reply.read(65537)
                            if reply.status != 200 or len(body) > 65536:
                                raise TrialError("resident state request failed/exceeded bound")
                            state = verify_web_state(json.loads(body), token, installed)
                        finally:
                            wire.close()
                        state["token"] = "<device-session-token>"
                        with (a.output_dir / "live-ready.json").open("x", encoding="utf-8", newline="\n") as out:
                            json.dump({"utc": datetime.now(timezone.utc).isoformat(), "mode": a.mode,
                                       "fresh_boot_observed": True, "uart_bound_web": state}, out, indent=2); out.write("\n")
                        ready = True; report["uart_bound_web_pass"] = True
                        emit("installed_web_ready", url="http://192.168.9.1", installed_sha256=installed)
                    expected_exit = (b"BW_RESIDENT_BOOTSTRAP_EXIT rc=0 cold=1 cache_restored=1 record_restored=0 stack_ok=1"
                                     if a.mode == "reset-web" else
                                     b"BW_RESIDENT_BOOTSTRAP_EXIT rc=0 cold=0 cache_restored=1 record_restored=1 stack_ok=1")
                    if ready and (Console.PROMPT.search(tail) or expected_exit in native):
                        if b"BW_RESIDENT_NATIVE_EXIT rc=0 " not in native or expected_exit not in native:
                            raise TrialError("resident did not return with verified native/cache/record restoration")
                        report["resident_return_pass"] = True
                        report["observation_pass"] = True; status = 0; break
                if time.monotonic() - heartbeat >= 30:
                    emit("observation_wait", web_ready=ready, input_queued=False)
                    heartbeat = time.monotonic()
            else:
                raise TrialError("bounded observation ended; no interrupts, reboot or command replay")
        except (Exception, KeyboardInterrupt) as error:
            report["error"] = str(error) or type(error).__name__
            emit("observation_stopped", reason=report["error"], automatic_retry=False)
        finally:
            if port:
                port.close()
            report["elapsed_seconds"] = round(time.monotonic() - start, 3)
            with (a.output_dir / "report.json").open("x", encoding="utf-8", newline="\n") as out:
                json.dump(report, out, ensure_ascii=False, indent=2); out.write("\n")
    return status


if __name__ == "__main__":
    raise SystemExit(main())
