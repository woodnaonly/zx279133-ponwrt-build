#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Check that the 7.3 line's sk-bridge puts the DSA user ports into the LAN bridge.

THE DEFECT THIS CHECKS FOR

/etc/config/network is not in the image: it is generated on the first boot, in
the preinit stage, right after the kernel pivots into the new rootfs.  On this
board the 9132's DSA ports are registered about a second LATER than that, so
the generation sees none of the panel jacks, takes its fallback branch and
publishes the LAN as the conduit alone:

	config device
		option name 'br-lan'
		list ports 'lan-cpu0'

A DSA bridge may hold either the conduit or the DSA user ports, never both.
The kernel says so, and says it with EINVAL.  Measured on the running 7.3.4
board on 2026-11-24, 17:45:

	ip link add br-t type bridge
	ip link set dev lan0 master br-t        -> RC=0
	ip link set dev lan-cpu0 master br-t    -> RTNETLINK answers: Invalid argument
	ip link set dev lan0 master br-lan      -> RTNETLINK answers: Invalid argument

The second refusal is on a bridge that already holds the conduit, which is
what the generation leaves behind.  So the list the generation produces is
the one list the kernel will not accept the jacks into, and a repair that
only ADDS to it cannot work: the bridge ends up holding the conduit and
none of the user ports, which is the dead LAN.

The bridge has to hold the five DSA user ports (lan0..lan4) and must not
hold the conduit.  That is what this check asks for.

WHAT THIS CHECK DOES

It runs the shipped script, unmodified apart from the two absolute paths it
is given, in a sandbox with a fake uci and a fake /sys/class/net, and reads
back the port list the script committed.  It is a behavioural check and not a
text match: a script that has the words "lan-cpu0" in it and does not repair
anything fails this check.

Usage:
  python3 tools/check-sk-lan-bridge-members.py [--script PATH] [--ports LIST]
"""

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SCRIPT = (ROOT / "overlays-73" / "target" / "linux" / "zte" / "zx279133" /
                  "base-files" / "etc" / "init.d" / "sk-bridge")

# The DSA user ports of the 9132: the four panel jacks (lan1..lan4) and the
# jackless one (lan0).  These are what the LAN bridge has to hold.
WANTED = ("lan0", "lan1", "lan2", "lan3", "lan4")
# The conduit is the CPU-facing netdev.  A DSA bridge may hold it OR the user
# ports, never both, so it must not be in the list.
FORBIDDEN = ("lan-cpu0",)

# The uci the script runs against.  It is a shell function rather than a
# program on PATH, so the sandbox needs no PATH and the check works the same
# on a Linux runner and on a Windows box with Git's sh.
#
# The state lives in $UCI_STATE.ports: the port list as it stands, and the
# script's own "uci get" reads it back, so a script that adds a port and
# then looks at the list sees the addition.
FAKE_UCI = r"""
uci() {
	if [ "$1" = "-q" ]; then
		shift
	fi
	case "$1" in
	get)
		case "$2" in
		"network.@device[0]") echo device ;;
		"network.@device[0].type") echo bridge ;;
		"network.@device[0].ports") cat "$UCI_STATE.ports" ;;
		network.wan) [ -f "$UCI_STATE.wan" ] || return 1; echo interface ;;
		*) return 1 ;;
		esac ;;
	add_list)
		case "$2" in
		"network.@device[0].ports="*)
			_p=${2#*=}
			printf ' %s' "$_p" >>"$UCI_STATE.ports"
			printf '%s\n' "$_p" >>"$UCI_STATE.added"
			;;
		*) return 1 ;;
		esac ;;
	set)
		case "$2" in
		network.wan) : >"$UCI_STATE.wan" ;;
		"network.@device[0].ports="*)
			_p=${2#*=}
			printf '%s' "$_p" >"$UCI_STATE.ports"
			;;
		*) return 1 ;;
		esac ;;
	commit) : ;;
	esac
	return 0
}
"""


def find_sh():
    """The shell to run the script with."""
    found = shutil.which("sh")
    if not found:
        print("FAIL  no sh on PATH; the check runs the shipped script and "
              "needs one")
        return None
    return found


def prepare(script, tmp, ports, netdevs):
    """Write the sandbox: the script, the fake uci and the netdevs.

    The two absolute paths in the script are rewritten to relative ones and
    the script is run with the sandbox as its working directory, so the same
    sandbox works on a Linux runner and on a Windows box.
    """
    (tmp / "sys" / "class" / "net").mkdir(parents=True, exist_ok=True)
    (tmp / "etc" / "config").mkdir(parents=True, exist_ok=True)
    for name in netdevs:
        (tmp / "sys" / "class" / "net" / name).mkdir(parents=True, exist_ok=True)

    (tmp / "etc" / "config" / "network").write_text(
        "config device\n\toption name 'br-lan'\n\toption type 'bridge'\n"
        "\tlist ports '%s'\n" % ports, encoding="utf-8", newline="\n")

    body = script.read_text(encoding="utf-8", errors="replace")
    body = body.replace("/etc/config/network", "etc/config/network")
    body = body.replace("/sys/class/net/", "sys/class/net/")
    # The script is an rc.common init script: on the board it is started
    # through /etc/rc.common, which is what calls its start().  Run with a
    # plain sh, nothing would happen at all - so the sandbox calls it.
    runnable = tmp / "sk-bridge"
    runnable.write_text(FAKE_UCI + body + "\nstart\n", encoding="utf-8",
                        newline="\n")
    return runnable


def run(script, tmp, ports, netdevs, sh):
    """Run the script and return the port list it left behind."""
    runnable = prepare(script, tmp, ports, netdevs)

    state = tmp / "uci"
    (state.with_suffix(".ports")).write_text(ports, encoding="utf-8", newline="")
    (state.with_suffix(".added")).write_text("", encoding="utf-8", newline="")

    env = dict(os.environ)
    env["UCI_STATE"] = str(state)

    proc = subprocess.run([sh, str(runnable)], cwd=str(tmp), env=env,
                          capture_output=True, text=True, timeout=120)
    final = (state.with_suffix(".ports")).read_text(encoding="utf-8")
    return final.split(), proc


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--script", type=Path, default=DEFAULT_SCRIPT,
                    help="the sk-bridge to check (default: the 7.3 line's)")
    ap.add_argument("--ports", default="lan-cpu0",
                    help="the port list the LAN bridge starts with")
    args = ap.parse_args(argv)

    script = args.script
    if not script.is_file():
        print("FAIL  no sk-bridge at %s" % script)
        return 1

    sh = find_sh()
    if sh is None:
        return 1

    tmp = Path(tempfile.mkdtemp(prefix="sk-lan-bridge-"))
    try:
        # The case that matters: the config the preinit-stage generation
        # leaves behind - the conduit alone - and the DSA ports registered
        # by the time the script runs.
        ports, proc = run(script, tmp, args.ports,
                          ("lan0", "lan1", "lan2", "lan3", "lan4", "lan-cpu0"), sh)
        missing = [p for p in WANTED if p not in ports]
        kept = [p for p in FORBIDDEN if p in ports]
        if missing or kept:
            if missing:
                print("FAIL  the LAN bridge is missing %s after the script ran"
                      % ", ".join(missing))
            if kept:
                print("FAIL  the LAN bridge still holds %s after the script ran"
                      % ", ".join(kept))
            print("      the bridge holds: %s"
                  % (" ".join(ports) if ports else "(nothing)"))
            if proc.stdout.strip():
                print("      the script said:\n%s" % proc.stdout.strip())
            if proc.stderr.strip():
                print("      the script complained:\n%s" % proc.stderr.strip())
            return 1
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print("PASS  %s puts every DSA user port into the LAN bridge and leaves "
          "the conduit out of it" % script)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
