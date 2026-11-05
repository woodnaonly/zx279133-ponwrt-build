#!/usr/bin/env python3
"""Boot the current system from a clean backup session and export config.

Explicit continuous power is required. No reboot, environment mutation,
firmware write, old-hook replay or automatic retry is performed. Linux writes
the export only to /tmp; the verified archive is kept on the computer.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from host.core import Core
from host.resident_runtime import command_record
from host.uart import Console, TrialError, open_serial, parse_md
from ram_probe import field
from resident_cold_trial import CONFIG_EXPORT_COMMAND, decode_config_export, terminal_plain

BOOTCMD = "ping 192.168.9.25; mtd read kernel 0x88000000; mtd read dtb 0x88800000; bootm 0x88000000 - 0x88800000"


def clean_backup(native, audit):
    if (native.get("backup_pass") is not True or native.get("native_write_enabled") is not False or
            any(native.get(key) is not True for key in
                ("native_return_pass", "alias_restored", "cache_restored", "full_flash_backup")) or
            audit.get("snapshot_audit_pass") is not True or
            native.get("backup_sha256") != audit.get("sha256") or
            native.get("installed_sha256") != audit.get("installed_sha256")):
        raise TrialError("current complete ECC backup has no clean, identity-bound return")


def main():
    p = argparse.ArgumentParser(description=__doc__)
    for name in ("capture-dir", "backup-audit", "current-resident-dir", "output-dir"):
        p.add_argument("--" + name, type=Path, required=True)
    p.add_argument("--confirm-continuous-power", action="store_true")
    p.add_argument("--port", default="COM3")
    a = p.parse_args()
    if not a.confirm_continuous_power:
        p.error("explicit continuous-power confirmation is required")
    a.output_dir.mkdir(parents=True, exist_ok=False)
    report = {"schema": "sk-d840n-current-linux-config-export-v1",
              "utc": datetime.now(timezone.utc).isoformat(), "nand_written": False,
              "reboot_requested": False, "configuration_export_pass": False,
              "power_mode": "continuous_required", "events": []}
    port = None
    started = time.monotonic()
    status = 1
    with (a.output_dir / "rx.bin").open("xb") as raw, \
            (a.output_dir / "events.jsonl").open("x", encoding="utf-8", newline="\n") as log:
        def emit(event, **fields):
            item = {"event": event, "elapsed": round(time.monotonic() - started, 3), **fields}
            report["events"].append(item)
            line = json.dumps(item, ensure_ascii=False)
            log.write(line + "\n"); log.flush(); print(line, flush=True)

        def record(data):
            raw.write(data); raw.flush()

        try:
            native_file = a.capture_dir / "report.json"
            native = json.loads(native_file.read_text(encoding="utf-8"))
            audit = json.loads(a.backup_audit.read_text(encoding="utf-8"))
            clean_backup(native, audit)
            if not 0 <= time.time() - native_file.stat().st_mtime <= 120:
                raise TrialError("clean backup console handoff is stale; no command replay")
            backup_file = a.capture_dir / "nand-backup/nand-physical-ecc.bin"
            whole = hashlib.sha256()
            with backup_file.open("rb") as source:
                current = source.read(0x200000); whole.update(current)
                for data in iter(lambda: source.read(0x100000), b""):
                    whole.update(data)
            if backup_file.stat().st_size != 0x10000000 or whole.hexdigest() != audit["sha256"]:
                raise TrialError("current saved backup length/hash changed")
            identity = Core().resident_identity(current[0x20000:0x180000])
            if identity.installed_sha256.decode() != audit["installed_sha256"]:
                raise TrialError("current saved installed identity changed")
            name, expected = command_record(current, 0x9ff3c000, resident_dir=a.current_resident_dir)
            port = open_serial(a.port)
            console = Console(port, emit=emit, raw=record, continuous_power_confirmed=True)
            console.window.boot(); console.prompt = True
            _, _, board = console.identify()
            if field(board, "relocaddr") != 0x9ff3c000 or field(board, "reloc off") != 0x17f3c000:
                raise TrialError("current U-Boot relocation changed")
            observed = parse_md(console.inspect("md.b 9ffd2c50 38"), 0x9ffd2c50, 56)
            if observed != expected:
                raise TrialError("current installed command record changed")
            if "bootcmd=" + BOOTCMD not in console.inspect("printenv bootcmd").splitlines():
                raise TrialError("bootcmd is not the known read/normal-boot sequence")
            for cache in ("icache", "dcache"):
                if "Cache is ON" not in console.inspect(cache):
                    raise TrialError("clean backup did not retain normal cache state")
            report["backup_sha256"] = audit["sha256"]
            report["installed_sha256"] = audit["installed_sha256"]
            emit("current_normal_boot_started", command="run bootcmd", current_command=name)
            console.prompt = False
            console.write(b"run bootcmd\r")
            activated = False
            boot = bytearray()
            end = time.monotonic() + 240
            while time.monotonic() < end:
                boot.extend(console.read())
                if len(boot) > 0x200000:
                    raise TrialError("current system boot output exceeded capture bound")
                if b"Starting kernel" in boot and b"Please press Enter to activate this console." in boot and not activated:
                    console.write(b"\r"); activated = True
                if activated and re.search(rb"(?:^|[\r\n])root@[^\r\n]*:[^\r\n]*# ?$", terminal_plain(boot)):
                    break
            else:
                raise TrialError("current system did not reach the expected Linux root prompt")
            report["current_system_boot_pass"] = True
            emit("current_linux_prompt_verified")

            def exchange(command, marker, seconds, bound):
                console.write(command.encode("ascii") + b"\r")
                answer = bytearray(); deadline = time.monotonic() + seconds
                pattern = re.compile(rb"(?:^|[\r\n])" + marker.encode("ascii") + rb"\r?\r?\n")
                while time.monotonic() < deadline:
                    answer.extend(console.read())
                    if len(answer) > bound:
                        raise TrialError("current Linux export exceeded bounded capture")
                    if Console.BOOT.search(answer):
                        raise TrialError("unexpected boot during configuration export; no replay")
                    if pattern.search(answer):
                        return bytes(answer)
                raise TrialError("configuration export timed out; no automatic retry")

            info = exchange("printf 'BW_CURRENT_LINUX_BEGIN\\n'; uname -a; cat /proc/uptime; ubus call system board; cat /proc/mtd; sha256sum /etc/config/*; printf 'BW_CURRENT_LINUX_END\\n'",
                            "BW_CURRENT_LINUX_END", 30, 0x40000)
            (a.output_dir / "current-linux.txt").write_bytes(info)
            archive, entries = decode_config_export(exchange(CONFIG_EXPORT_COMMAND, "BW_CONFIG_END", 120, 0x500000))
            (a.output_dir / "current-config.tar.gz").write_bytes(archive)
            report["configuration_export"] = {"verified": True, "bytes": len(archive),
                "sha256": hashlib.sha256(archive).hexdigest(), "entries": entries}
            report["configuration_export_pass"] = True
            emit("configuration_exported", bytes=len(archive), sha256=hashlib.sha256(archive).hexdigest())
            status = 0
        except (Exception, KeyboardInterrupt) as error:
            report["error"] = str(error) or type(error).__name__
            emit("configuration_export_stopped", reason=report["error"])
        finally:
            if port:
                port.close()
            with (a.output_dir / "report.json").open("x", encoding="utf-8", newline="\n") as output:
                json.dump(report, output, ensure_ascii=False, indent=2); output.write("\n")
    return status


if __name__ == "__main__":
    raise SystemExit(main())
