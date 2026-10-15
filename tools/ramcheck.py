#!/usr/bin/env python
"""Read the facts that decide whether the kexec'd RAM image is worth flashing.

Run after trial-kexec.sh has exec'd and the board answers on 192.168.9.1:

    python tools/ramcheck.py

The RAM image deliberately carries no authorized key and no password, which is
exactly what base-files ships: root with an empty password. So this logs in with
'' - it does not read or send any secret of ours, and nothing about it ends up in
an artifact.

Everything here is read-only. The point is mtd numbering: /proc/mtd must show
"root" as mtd5, because that is the index the installed U-Boot bootargs hard-code.
If it does not, the partition list or the env has to be fixed before anything is
written to flash.
"""
import socket
import sys

import paramiko

HOST = sys.argv[1] if len(sys.argv) > 1 else "192.168.9.1"

CMDS = [
    ("what booted", "uname -a; cat /proc/cmdline"),
    ("board identity", "cat /proc/device-tree/model 2>/dev/null | tr -d '\\0'; echo; "
                        "cat /proc/device-tree/compatible 2>/dev/null | tr '\\0' ' '; echo"),
    ("mtd numbering", "cat /proc/mtd"),
    ("partition labels", "for d in /sys/class/mtd/mtd*; do printf '%s %s %s\\n' "
                         "${d##*/} $(cat $d/size) $(cat $d/label); done"),
    ("network", "ifconfig -a 2>/dev/null | grep -E '^[a-z]|HWaddr' | head -20; "
                "cat /sys/class/net/*/carrier 2>/dev/null | tr '\\n' ' '"),
    ("ethernet driver + phy", "dmesg | grep -iE 'zx279133|nppt|idm|mdio|phy|eth' | tail -25"),
    ("flash tools", "ls -l /sbin/mtd 2>&1; command -v kexec mtd_debug nandwrite sysupgrade"),
    ("memory", "free | head -2; df -h /tmp | tail -1"),
]


def wait_for_port(host, port=22, timeout=300):
    end = timeout
    while end > 0:
        try:
            with socket.create_connection((host, port), timeout=2):
                return True
        except OSError:
            end -= 3
            print("  ... %s:%d not answering (%ds left)" % (host, port, end), flush=True)
            if end <= 0:
                return False


def main():
    if not wait_for_port(HOST):
        print("no ssh on %s - the RAM system is not reachable from here." % HOST)
        print("If the cable is not in the uplink RJ45 (vendor wan/eth0), move it and retry;")
        print("power-cycling the board always brings the flashed system back.")
        return 2

    cli = paramiko.SSHClient()
    cli.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        cli.connect(HOST, username="root", password="", look_for_keys=False,
                    allow_agent=False, timeout=15, auth_timeout=15)
    except Exception as e:
        print("ssh to root@%s failed: %s" % (HOST, e))
        print("A dropbear that answers but refuses an empty password means the new")
        print("image shipped a locked root - rebuild with a root password, do not")
        print("bake in a key.")
        return 3

    print("=== logged in to %s (empty password) ===" % HOST)
    for label, cmd in CMDS:
        _, out, err = cli.exec_command(cmd, timeout=30)
        text = out.read().decode("utf-8", "replace").strip()
        errt = err.read().decode("utf-8", "replace").strip()
        print("\n--- %s ---" % label)
        print(text or "(no output)")
        if errt:
            print("stderr: %s" % errt)
    cli.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
