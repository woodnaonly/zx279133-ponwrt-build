#!/usr/bin/env python3
"""Inspect the Linux generation just proven by resident_live --mode normal.

One UART owner, read-only shell queries, no config changes, restart or retry.
Reject a new boot marker or an uptime inconsistent with the supplied anchor.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import secrets
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from host.uart import Console, TrialError, open_serial
from resident_cold_trial import terminal_plain


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cold-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--port", default="COM3")
    args = parser.parse_args()
    anchor_raw = (args.cold_dir / "report.json").read_bytes()
    anchor = json.loads(anchor_raw)
    if (anchor.get("mode") != "normal" or anchor.get("normal_linux_boot_pass") is not True or
            anchor.get("observation_pass") is not True or anchor.get("ram_payload_loaded") is not False or
            anchor.get("flash_commands_sent") is not False or anchor.get("reset_command_sent") is not False):
        parser.error("requires completed read-only normal cold-boot evidence")
    boot_time = datetime.fromisoformat(anchor["fresh_power_on_utc"])
    age = (datetime.now(timezone.utc) - boot_time).total_seconds()
    if not 0 <= age < 3600:
        parser.error("Linux generation anchor is not recent")
    args.output_dir.mkdir(parents=True, exist_ok=False)
    report = {"schema": "sk-d840n-linux-network-observation-v1",
              "utc": datetime.now(timezone.utc).isoformat(),
              "cold_report_sha256": hashlib.sha256(anchor_raw).hexdigest(),
              "config_changed": False, "flash_commands_sent": False, "reboot_requested": False,
              "identity_pass": False, "network_ready": False}
    port = None
    status = 1
    with (args.output_dir / "rx.bin").open("xb") as raw:
        def record(data):
            raw.write(data)
            raw.flush()
            if Console.BOOT.search(data) or b"Resetting CPU" in data or b"Synchronous Abort" in data:
                raise TrialError("new boot/fault observed; no query replay")

        try:
            port = open_serial(args.port)
            console = Console(port, raw=record, continuous_power_confirmed=True)
            print("serial_open dtr=0 rts=0 read_only_linux_queries=1", flush=True)
            pending = bytearray()
            until = time.monotonic() + 2
            while time.monotonic() < until:
                pending.extend(console.read())
            if Console.BOOT.search(pending) or b"Linux version" in pending:
                raise TrialError("boot generation changed before Linux prompt; no input")
            console.write(b"\r")
            until = time.monotonic() + 10
            while time.monotonic() < until:
                pending.extend(console.read())
                if re.search(rb"(?:^|[\r\n])root@[^\r\n]*:[^\r\n]*# ?$", terminal_plain(pending)):
                    break
            else:
                raise TrialError("known Linux root prompt was not reacquired")

            def query(command, name):
                tag = "BW_LINUX_" + secrets.token_hex(8).upper()
                command = f"printf '{tag}_BEGIN\\n'; {command}; printf '\\n{tag}_END\\n'"
                if len(command) > 480:
                    raise TrialError("read-only query exceeds verified short UART shell line")
                console.write(command.encode("ascii") + b"\r")
                answer = bytearray()
                until = time.monotonic() + 30
                while time.monotonic() < until:
                    answer.extend(console.read())
                    if len(answer) > 0x80000:
                        raise TrialError("bounded Linux query capture exceeded")
                    if Console.BOOT.search(answer) or b"Resetting CPU" in answer:
                        raise TrialError("Linux generation changed during query; no replay")
                    plain = terminal_plain(answer)
                    if re.search(rb"(?:^|[\r\n])" + tag.encode() + rb"_END\r?\r?\n", plain):
                        (args.output_dir / (name + ".txt")).write_bytes(answer)
                        match = re.search(rb"(?:^|[\r\n])" + tag.encode() +
                                          rb"_BEGIN\r?\r?\n(.*?)\r?\r?\n" + tag.encode() + rb"_END", plain, re.S)
                        if not match:
                            raise TrialError("Linux query framing incomplete")
                        return match[1].decode("utf-8", errors="replace")
                raise TrialError("read-only Linux query timed out; no retry")

            identity = query("uname -a; printf 'BW_UPTIME '; cut -d' ' -f1 /proc/uptime; "
                             "ubus call system board; cat /etc/openwrt_release", "identity")
            uptime = re.search(r"(?:^|[\r\n])BW_UPTIME ([0-9.]+)", identity)
            if (not uptime or '"board_name": "skyworth,sk-d840n"' not in identity or
                    "DISTRIB_REVISION='r0-1b50aaf'" not in identity or "6.18.60+" not in identity):
                raise TrialError("current Linux identity differs from #337 cold evidence")
            delta = (datetime.now(timezone.utc) - boot_time).total_seconds() - float(uptime[1])
            if not -5 <= delta <= 45:
                raise TrialError("current Linux uptime does not match this physical cold boot")
            report["identity_pass"] = True
            report["linux_uptime_seconds"] = float(uptime[1])
            report["host_boot_age_minus_linux_uptime_seconds"] = round(delta, 3)
            network = query("ip -4 addr show; printf '\\nBW_LAN_STATUS\\n'; "
                "ubus call network.interface.lan status; printf '\\nBW_LAN_CONFIG\\n'; "
                "for key in proto device ipaddr netmask; do printf '%s=' $key; uci -q get network.lan.$key; done", "network")
            lan = re.search(r"BW_LAN_STATUS\s*(.*?)\s*BW_LAN_CONFIG", network, re.S)
            try:
                state = json.loads(lan[1]) if lan else None
            except ValueError:
                state = None
            report["lan_status"] = state
            report["network_ready"] = bool(state and state.get("up") is True and state.get("ipv4-address"))
            report["ipv4_addresses"] = re.findall(r"\binet ([0-9.]+/\d+)", network)
            services = query("pidof procd netifd ubusd rpcd uhttpd dropbear; "
                "ls -l /etc/config/network /etc/config/system /etc/board.json /www/cgi-bin/luci /www/index.html; "
                "logread | tail -n 45", "services")
            report["observation_pass"] = True
            print(identity, flush=True)
            print(network, flush=True)
            print(services, flush=True)
            status = 0
        except (OSError, ValueError, TrialError) as error:
            report["error"] = str(error)
            report["observation_pass"] = False
            print(str(error), flush=True)
        finally:
            if port:
                port.close()
            with (args.output_dir / "report.json").open("x", encoding="utf-8", newline="\n") as output:
                json.dump(report, output, ensure_ascii=False, indent=2)
                output.write("\n")
    return status


if __name__ == "__main__":
    raise SystemExit(main())
