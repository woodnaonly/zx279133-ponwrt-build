#!/usr/bin/env python3
"""Run the shipped ucode RPCs against isolated sysfs/proc fixtures (no router)."""
import argparse
import json
from pathlib import Path
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
RPC = ROOT / "overlays/package/luci-app-sk-d840n-pon/root/usr/share/rpcd/ucode"


def run_tests(ucode):
    with tempfile.TemporaryDirectory(prefix="sk-pon-rpc-") as temporary:
        fixture = Path(temporary)

        def write(path, value):
            destination = fixture / path.lstrip("/")
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_text(value, encoding="utf-8")

        def call(name):
            source = (RPC / name).read_text(encoding="utf-8").split("\n", 1)[1]
            source = source.replace("import { readfile } from 'fs';", "")
            source = source.replace("import { readfile, glob } from 'fs';", "")
            program = fixture / "rpc.uc"
            program.write_text(
                "import { readfile as read_fixture, glob as glob_fixture } from 'fs';\n"
                + "const FIXTURE = " + json.dumps(str(fixture)) + ";\n"
                + "function readfile(p) { return read_fixture(FIXTURE + p); }\n"
                + "function glob(p) { return glob_fixture(FIXTURE + p); }\n"
                # glob produces absolute fixture paths; avoid prefixing them twice.
                + "let api = (function() {\n"
                + source.replace("readfile(path +", "read_fixture(path +").replace("number(path +", "number(substr(path, length(FIXTURE)) +")
                + "\n})();\nprint(sprintf('%J', api[" + json.dumps(name) + "].status.call()));\n",
                encoding="utf-8",
            )
            return json.loads(subprocess.check_output([ucode, str(program)], text=True))

        empty = call("luci.skd840n-pon")
        assert empty["ports"] == [] and empty["service"] is None
        assert empty["pon"]["registered"] is None and empty["pon"]["optical"] is None
        assert empty["acceleration"]["flows"] is None
        cpu = call("luci.skd840n-system")
        assert cpu["cpu"]["cores"] is None and cpu["temperature_mC"] is None

        write("/proc/uptime", "100.25 200.00\n")
        write("/proc/stat", "cpu 0 0 0 1\ncpu0 0 0 0 1\ncpu1 0 0 0 1\n")
        write("/proc/123/stat", "123 (zxpond (test)) " + " ".join(["S"] + ["0"] * 18 + ["12345"] + ["0"] * 10))
        marker = {"schema": 1, "pid": 123, "process_start": "12345", "updated_s": 99,
                  "enabled": True, "configuration_valid": True, "identity_configured": True,
                  "engine_ready": True, "transport_ready": True, "error": -95,
                  "registration_id": "SECRET", "password": "SECRET", "psk": "SECRET"}
        write("/var/run/zxpond-status.json", json.dumps(marker))
        for key, value in {"ifindex": "2", "carrier": "1", "speed": "2500", "statistics/rx_packets": "0"}.items():
            write("/sys/class/net/lan1/" + key, value + "\n")
        write("/sbin/fw4", "#!/bin/sh\n")
        current = call("luci.skd840n-pon")
        assert current["service"]["engine_ready"] is True
        assert current["service"]["transport_ready"] is False  # No trust in a stale/forged marker capability.
        assert "SECRET" not in json.dumps(current)
        assert current["ports"][0]["rx_packets"] == 0 and current["ports"][0]["tx_packets"] is None
        assert current["ports"][0]["speed_mbps"] == 2500
        assert current["acceleration"]["firewall4"] is True
        for field, bad in [("updated_s", 80), ("updated_s", 101), ("pid", "123"), ("schema", 2), ("process_start", "999")]:
            write("/var/run/zxpond-status.json", json.dumps({**marker, field: bad}))
            assert call("luci.skd840n-pon")["service"] is None, field
        write("/var/run/zxpond-status.json", json.dumps(marker))
        write("/proc/123/stat", "123 (zxpond) Z " + " ".join(["0"] * 18 + ["12345"]))
        assert call("luci.skd840n-pon")["service"] is None
        write("/var/run/zxpond-status.json", "{broken")
        assert call("luci.skd840n-pon")["service"] is None
        write("/sys/class/net/lan1/speed", "-1\n")
        assert call("luci.skd840n-pon")["ports"][0]["speed_mbps"] is None
        write("/sys/class/thermal/thermal_zone0/type", "soc-thermal\n")
        write("/sys/class/thermal/thermal_zone0/temp", "-5000\n")
        cpu = call("luci.skd840n-system")
        assert cpu["cpu"]["cores"] == 2 and cpu["cpu"]["current_khz"] is None
        assert cpu["temperature_mC"] == -5000 and cpu["cpu"]["frequency_control"] is False
        write("/sys/devices/system/cpu/cpufreq/policy0/scaling_cur_freq", "900000\n")
        assert call("luci.skd840n-system")["cpu"]["current_khz"] == 900000
        print("PASS: real ucode RPCs, missing nodes, PID reuse/zombie/stale state, null values, temperature, no credentials")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("ucode", help="path to the build's host ucode executable")
    run_tests(parser.parse_args().ucode)
