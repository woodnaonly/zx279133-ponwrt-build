#!/usr/bin/env python3
"""Explicit, temporary Linux SSH test. NEVER a U-Boot or flash acceptance test.

Requires --run-device-test. Only fixed 192.168.9.1/root with the previously
verified PuTTY host key. Uploads one readonly executable to a private tmpfs
directory, uses loopback HTTP through SSH, then stops/removes only its files.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import http.client
import json
from pathlib import Path
import re
import shlex
import socket
import subprocess
import sys
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))
from host.core import BASE, Core, read_package
from fixtures import PREFIX, boot, sys_parts, system

HOST_KEY = "SHA256:qMbRHLs8sFv1u9gBZB18UY2j+0HO+AYbxkhypq9UQWo"
PUTTY = Path("C:/Program Files/PuTTY")


def digest(data):
    return hashlib.sha256(data).hexdigest()


def body_receipt(log, ordinal):
    """Find a complete harness delivery event; no transport timing assumption."""
    if ordinal < 1:
        raise ValueError("receipt ordinal must be positive")
    receipts = []
    for line in log.splitlines(keepends=True):
        if not line.endswith("\n") or not line.startswith("{"):
            continue  # cat may observe the last log line while it is being written
        event = json.loads(line)
        if event.get("event") == "body_received":
            receipts.append(event)
    return receipts[ordinal - 1] if len(receipts) >= ordinal else None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-device-test", action="store_true")
    parser.add_argument("--system-image", type=Path,
                        default=ROOT.parents[1] / "staging/trial/run85/flash-sysupgrade.bin")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if not args.run_device_test:
        parser.error("this contacts a real device; explicitly supply --run-device-test")
    if args.output and args.output.exists():
        parser.error("report already exists; choose a new output path")
    binary = ROOT / ".build/bootweb-linux-readonly"
    if not binary.is_file():
        parser.error("first run tools/build_ssh_harness.py")
    valid = read_package(args.system_image)
    expected = Core().validate(valid).report()
    session = uuid.uuid4().hex[:16]
    directory = "/tmp/bootweb-ssh-" + session
    executable = directory + "/bootweb-linux-readonly"
    output = args.output or ROOT / ".build" / ("ssh-" + session + ".json")
    report = {"schema": "sk-d840n-linux-ssh-readonly-v1", "utc": datetime.now(timezone.utc).isoformat(),
              "scope": "temporary Linux userspace test, NOT U-Boot/RAM recovery",
              "device": "192.168.9.1", "read_only_flash": True, "flashable_release": False,
              "native_uboot_verified": False, "vendor_ecc_badblock_api_verified": False,
              "reset_entry_verified": False, "serial_rescue_verified": False,
              "reboot_performed": False, "partition_changes": False,
              "harness_sha256": digest(binary.read_bytes()), "harness_bytes": binary.stat().st_size,
              "temporary_directory": directory,
              "cases": [], "pass": False, "cleanup_complete": False}
    common = [str(PUTTY / "plink.exe"), "-batch", "-ssh", "-T", "-hostkey", HOST_KEY,
              "-l", "root", "-pw", "", "192.168.9.1"]
    flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
    tunnel = None
    pid = None
    created = False
    code = 1

    def ssh(command, *, timeout=30):
        process = subprocess.run([*common, command], capture_output=True, text=True, encoding="utf-8",
                                 timeout=timeout, creationflags=flags)
        if process.returncode:
            raise RuntimeError(f"SSH command failed ({process.returncode}): {command}\n" +
                               process.stderr.strip() + " " + process.stdout.strip())
        return process.stdout

    def check(name, condition, **details):
        report["cases"].append({"name": name, "passed": bool(condition), **details})
        print(("PASS " if condition else "FAIL ") + name, flush=True)
        if not condition:
            print(json.dumps(details, ensure_ascii=False), flush=True)
            raise AssertionError(name)

    try:
        board = json.loads(ssh("ubus call system board"))
        check("board_identity", board["board_name"] == "skyworth,sk-d840n")
        report["linux_board"] = board
        report["boot_and_tags_before"] = ssh("sha256sum /dev/mtd1 /dev/mtd4").strip().splitlines()
        report["root_corrected_bits_before"] = int(ssh("cat /sys/class/mtd/mtd5/corrected_bits").strip())
        # No recursive removal, no modification of a pre-existing /tmp path.
        ssh("umask 077; awk '$2==\"/tmp\" && $3==\"tmpfs\" {f=1} END {exit !f}' /proc/mounts && "
            "command -v readlink >/dev/null && mkdir " + shlex.quote(directory))
        created = True
        subprocess.run([str(PUTTY / "pscp.exe"), "-batch", "-scp", "-hostkey", HOST_KEY, "-pw", "",
                        str(binary), "root@192.168.9.1:" + executable], check=True,
                       capture_output=True, text=True, timeout=30, creationflags=flags)
        check("executable_transfer_sha256", ssh("sha256sum " + executable).split()[0] == report["harness_sha256"])
        probe = ssh("chmod 700 " + executable + "; " + executable + " --probe")
        report["readonly_probe"] = [json.loads(line) for line in probe.splitlines() if line.startswith("{")]
        check("arm64_c_hashes_and_current_base", len(report["readonly_probe"]) == 3 and
              report["readonly_probe"][0]["stage2"] == BASE and
              report["readonly_probe"][0]["environment"] == report["readonly_probe"][1]["environment"] and
              report["readonly_probe"][2]["denied_write_calls"] == 0)
        pid_text = ssh("trap '' HUP; " + executable + " --serve </dev/null >" + directory + "/server.log 2>" +
                       directory + "/server.err & task_pid=$!; printf '%s\\n' \"$task_pid\"").strip()
        if not re.fullmatch(r"[1-9][0-9]*", pid_text):
            raise ValueError("invalid private server PID")
        pid = int(pid_text)
        # Reserve a local port only momentarily; PuTTY binds it for this session.
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            local_port = listener.getsockname()[1]
        tunnel = subprocess.Popen([*common[:-1], "-N", "-L",
                                   f"127.0.0.1:{local_port}:127.0.0.1:18088", common[-1]],
                                  stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                                  creationflags=flags)

        def request(method, path, body=None, *, host="192.168.9.1", token=None, origin=None):
            headers = {"Host": host}
            if method == "POST":
                headers["X-Bootweb-Token"] = token or state["token"]
                headers["Content-Type"] = "application/octet-stream"
            if origin:
                headers["Origin"] = origin
            connection = http.client.HTTPConnection("127.0.0.1", local_port, timeout=45)
            try:
                connection.request(method, path, body=body, headers=headers)
                response = connection.getresponse()
                data = response.read()
                content = json.loads(data) if "application/json" in response.getheader("Content-Type", "") else data
                return response.status, content
            finally:
                connection.close()

        state = None
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            if tunnel.poll() is not None:
                raise RuntimeError("SSH tunnel failed: " + tunnel.stderr.read().decode(errors="replace"))
            try:
                status, state = request("GET", "/api/state")
                break
            except (OSError, http.client.HTTPException):
                time.sleep(0.25)
        check("ssh_loopback_http_idle_locked", state is not None and status == 200 and state["state"] == "idle"
              and not state["can_confirm"] and not state["can_reboot"] and state["base"] == BASE)
        status, page = request("GET", "/")
        check("same_single_file_page", status == 200 and page == (ROOT / "web/index.html").read_bytes(),
              bytes=len(page), sha256=digest(page))
        check("host_rebinding_rejected", request("GET", "/api/state", host="evil.invalid")[0] == 409)
        check("cross_origin_rejected", request("POST", "/api/upload", b"", origin="http://evil.invalid")[0] == 409)
        check("bad_token_rejected", request("POST", "/api/upload", b"X" * 1536, token="b" * 64)[0] == 409)
        for iteration in range(10):
            with socket.create_connection(("127.0.0.1", local_port), timeout=10) as partial:
                partial.sendall(("POST /api/upload HTTP/1.1\r\nHost: 192.168.9.1\r\nContent-Length: " + str(len(valid)) +
                                 "\r\nX-Bootweb-Token: " + state["token"] + "\r\n\r\n").encode() + valid[:1024])
                # Immediate half-close may reach PuTTY before its forwarded
                # channel has delivered ANY data. Wait for an actual body-read
                # event on the device, so this tests a truncated upload rather
                # than a connection abandoned before the HTTP request arrived.
                receipt = None
                deadline = time.monotonic() + 10
                while time.monotonic() < deadline:
                    receipt = body_receipt(ssh("cat " + directory + "/server.log", timeout=5), iteration + 1)
                    if receipt is not None:
                        break
                    time.sleep(0.05)
                check(f"truncated_body_received_before_disconnect_{iteration+1}", receipt is not None and
                      0 < receipt["body_bytes"] <= 1024, receipt=receipt)
                partial.shutdown(socket.SHUT_WR)
                partial_reply = partial.recv(4096)
            status, state = request("GET", "/api/state")
            check(f"truncated_upload_not_ready_{iteration+1}", status == 200 and state["state"] == "failed" and "truncated" in state["error"],
                  http_status=status, observed_state=state.get("state"), observed_error=state.get("error"),
                  partial_reply_bytes=len(partial_reply), partial_reply_status=partial_reply.split(b"\r\n", 1)[0].decode(errors="replace")[:80])
            check(f"explicit_cancel_{iteration+1}", request("POST", "/api/cancel", b"")[0] == 200)
        started = time.monotonic()
        status, state = request("POST", "/api/upload", valid)
        expected_writes = [{key: item[key] for key in ("name", "length", "capacity", "sha256")}
                           for item in expected["writes"]]
        check("real_sysupgrade_validated_on_arm64", status == 200 and state["state"] == "ready" and
              state["result"]["sha256"] == expected["sha256"] and
              state["result"]["writes"] == expected_writes and not state["can_confirm"],
              package_bytes=len(valid), package_sha256=expected["sha256"], seconds=round(time.monotonic()-started, 3))
        report["system_validation_result"] = state["result"]
        confirm = (state["id"] + "\n" + state["result"]["sha256"] + "\n").encode()
        check("confirm_write_hard_locked", request("POST", "/api/confirm", confirm)[0] == 409)
        check("repeat_confirm_still_locked", request("POST", "/api/confirm", confirm)[0] == 409)
        check("reboot_unavailable", request("POST", "/api/reboot", b"")[0] == 409)
        check("cancel_ready", request("POST", "/api/cancel", b"")[0] == 200)
        wrong = sys_parts()
        wrong["CONTROL"] = b"BOARD=other\n"
        duplicate = system(extras=[(PREFIX + "/kernel", b"duplicate")])
        traversal = system(extras=[("../outside", b"X")])
        corrupted = bytearray(valid)
        corrupted[1024] ^= 1
        for name, data in (("wrong_board", system(wrong)), ("duplicate_tar_member", duplicate),
                           ("path_traversal", traversal), ("bad_fwtool_crc", bytes(corrupted)),
                           ("wrong_uboot_base", boot("0" * 64)),
                           ("full_boot_payload", boot(BASE, payload=b"C" * 0x200000))):
            status, state = request("POST", "/api/upload", data)
            check(name + "_rejected", status == 409 and state["state"] == "failed", reason=state.get("error"))
            if request("POST", "/api/cancel", b"")[0] != 200:
                raise AssertionError("cannot clear rejected task")
        report["pass"], code = True, 0
    except (OSError, ValueError, RuntimeError, AssertionError, subprocess.SubprocessError, http.client.HTTPException) as error:
        report["error"] = str(error)
        print("SSH read-only test:", error, file=sys.stderr, flush=True)
    finally:
        if tunnel is not None:
            tunnel.terminate()
            try:
                tunnel.wait(timeout=5)
            except subprocess.TimeoutExpired:
                tunnel.kill()
                tunnel.wait(timeout=5)
            if tunnel.stderr:
                tunnel.stderr.close()
        if created:
            try:
                if pid is not None:
                    # Check exact executable path BEFORE signaling this private PID.
                    ssh("if [ \"$(readlink /proc/" + str(pid) + "/exe)\" = " + shlex.quote(executable) +
                        " ]; then kill -TERM " + str(pid) + "; fi; "
                        "task_wait=0; while [ \"$(readlink /proc/" + str(pid) + "/exe)\" = " + shlex.quote(executable) +
                        " ] && [ \"$task_wait\" -lt 8 ]; do sleep 1; task_wait=$((task_wait+1)); done; "
                        "test \"$(readlink /proc/" + str(pid) + "/exe)\" != " + shlex.quote(executable), timeout=15)
                    log = ssh("cat " + directory + "/server.log; cat " + directory + "/server.err >&2")
                    events = [json.loads(line) for line in log.splitlines() if line.startswith("{")]
                    report["server_events"] = events
                    first = next(x for x in events if x["event"] == "before")
                    last = next(x for x in events if x["event"] == "after")
                    done = next(x for x in events if x["event"] == "finished")
                    check("protected_hashes_unchanged_and_no_write_callback", done["exit_code"] == 0 and
                          done["denied_write_calls"] == 0 and all(first[k] == last[k] for k in
                          ("stage1", "stage2", "environment", "boot_tail", "parameter_tags")))
                after = ssh("sha256sum /dev/mtd1 /dev/mtd4").strip().splitlines()
                report["boot_and_tags_after"] = after
                check("independent_full_boot_and_tags_unchanged", after == report["boot_and_tags_before"])
                report["root_corrected_bits_after"] = int(ssh("cat /sys/class/mtd/mtd5/corrected_bits").strip())
                # Literal, UUID-scoped leaf files ONLY. Never recursive rm or /tmp cleanup.
                ssh("rm -f -- " + " ".join(shlex.quote(directory + "/" + x) for x in
                    ("bootweb-linux-readonly", "server.log", "server.err")) + "; rmdir " + shlex.quote(directory))
                report["cleanup_complete"] = True
            except (OSError, ValueError, RuntimeError, AssertionError, StopIteration, subprocess.SubprocessError) as error:
                report["cleanup_error"], report["pass"], code = str(error), False, 1
                print("Read-only test cleanup:", error, file=sys.stderr)
                print("Private tmpfs directory may remain:", directory, file=sys.stderr)
        output.parent.mkdir(parents=True, exist_ok=True)
        with output.open("x", encoding="utf-8", newline="\n") as target:
            json.dump(report, target, indent=2, ensure_ascii=False)
            target.write("\n")
        print(str(output.resolve()))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
