#!/bin/sh
# The whole NAT acceptance round on the flashed image, in one go.
#
#   sh tools/natround.sh
#
# The board is on a smart plug that cuts its power every fifteen and a half
# minutes, so the round is built to fit in one power cycle and to survive
# being interrupted: every step that changes the board is made over the
# serial console, which does not depend on the network, and every change is
# committed, so a power cut in the middle of the round leaves the board in a
# state the next run can still work with.
#
# The steps, and why each one is where it is:
#
#   1. the WAN port moves to lan2, over the console.  The board's LAN
#      address leaves this PC's segment when lan2 leaves the bridge, so the
#      change cannot be made over ssh - and a change made over ssh was
#      measured going with the power when the plug cut the board before the
#      lease could be read.
#   2. this PC turns on the sharing, so it becomes the upstream with a DHCP
#      server on 192.168.137.x.
#   3. the board takes a lease on that subnet; it is found by asking it, not
#      by reading the neighbour table (Windows left an entry for every
#      address in the subnet, with the board's MAC on all of them).
#   4. dropbear is told to listen on the wan as well, because it listens on
#      the lan only, and the lan address is on the other side of the router
#      now.
#   5. a client is put behind the router - a veth pair in a network
#      namespace of the board's own making - and it takes a lease from the
#      board's own DHCP server.
#   6. the evidence is read: the masquerade rule and its counter, the
#      client's flow in the conntrack table with the address rewritten, the
#      client's own ping to the internet.
#   7. everything is put back: the client, the port, dropbear, and this
#      PC's sharing.
set -e
set -o pipefail
# The round leaves the bench in a state that only this script knows how to
# undo: the sharing on, one port out of the bridge and committed into flash,
# dropbear listening on the wan.  It used to put those back only on the way
# through the end of the script, and the script has `set -e`, so any step that
# failed left all of it behind - and a board committed in wan mode answers
# nothing on 192.168.9.1 after the next power cut, which is the address every
# other tool on this bench uses.  So: put the bench back whatever happens, and
# let the original status travel afterwards.  RESTORED is what keeps the happy
# path from running the restore twice.
ICS_ON=0
RESTORED=0
natround_cleanup() {
	rc=$?
	if [ "$RESTORED" = 0 ]; then
		echo "natround: the round stopped before the restore - putting the bench back" >&2
		sh restore.sh "${WAN:-}" >&2 2>&1 ||
			echo "natround: the restore itself failed; the board may still be in wan mode - use the console (portmove.sh back)" >&2
	fi
	if [ "$ICS_ON" = 1 ]; then
		if command -v pwsh >/dev/null 2>&1; then
			pwsh -NoProfile -File "$UPSTREAM" -Mode off >&2 2>&1 || true
		else
			powershell -NoProfile -ExecutionPolicy Bypass -File "$UPSTREAM" -Mode off >&2 2>&1 || true
		fi
	fi
	exit $rc
}
trap natround_cleanup EXIT
cd "$(dirname "$0")"

HERE=$(pwd)
# HERE is .../SK-D840N/ci-build-repo/tools, so the repository is one level
# up and the bench's own tools (the upstream script) are two levels up.
REPO=$(dirname "$HERE")
ROOT=$(dirname "$REPO")
# This repository carries its own copy of the upstream script, because the
# round is worth running from a checkout and not only from the bench it was
# written on.  The bench's copy wins when it is there - it is the one that
# has been measured against this machine's adapters - and the repository's
# copy is the fallback.
UPSTREAM="$HERE/labupstream.ps1"
if [ -f "$ROOT/tools/labupstream.ps1" ]; then
	UPSTREAM="$ROOT/tools/labupstream.ps1"
fi
UPSTREAM_POSIX=$UPSTREAM
# A Windows path, because that is what pwsh wants.  A POSIX path handed to
# pwsh is a path it cannot open - which shows up as pwsh printing its own
# help instead of running the script.
if command -v cygpath >/dev/null 2>&1; then
	UPSTREAM=$(cygpath -w "$UPSTREAM" 2>/dev/null || echo "$UPSTREAM")
else
	UPSTREAM=$(echo "$UPSTREAM" | sed -e 's|^/c/|C:/|' -e 's|^/|C:/|')
fi

# Git bash rewrites an argument that looks like an absolute path into a
# Windows one before Python sees it, which turns "/etc/init.d/..." into
# "C:/Program Files/Git/etc/init.d/...".  The board's paths must not be
# rewritten, so the conversion is turned off only around the calls that are
# meant for the board, and left alone for the calls that are meant for this
# PC.
noconv() { MSYS_NO_PATHCONV=1 python "$@"; }

say() { echo; echo "############ $* ############"; }

# The whole round is written to a file as it happens, because the evidence
# is only readable while the board is in wan mode - and by the time the
# round is over it has been put back, so a file written afterwards would
# say nothing.
#
# The file is written by the caller, which pipes this script's output
# through tee: the evidence is in the output, and the output is the only
# thing that has it while the board is in wan mode.
LOG=${NATROUND_LOG:-"$ROOT/staging/ctl/g4nat-round.txt"}
mkdir -p "$(dirname "$LOG")"
echo "the round is being written to $LOG"

say "0. the bench"
echo "repository: $REPO"
echo "upstream script: $UPSTREAM"
test -f "$UPSTREAM_POSIX" || {
	echo "no upstream script at $UPSTREAM_POSIX"; exit 1; }

say "1. the WAN port moves to lan2, over the console"
sh portmove.sh move 2>&1 | tail -30

say "2. this PC becomes the upstream"
if command -v pwsh >/dev/null 2>&1; then
	pwsh -NoProfile -File "$UPSTREAM" -Mode on 2>&1 | tail -6
else
	powershell -NoProfile -ExecutionPolicy Bypass -File "$UPSTREAM" -Mode on 2>&1 | tail -6
fi
# The sharing's own output cannot be trusted as a verdict: labupstream.ps1
# prints "already shared" from a catch block, and the `| tail` above takes
# the pipe's status rather than the script's.  So the state is read instead
# of assumed - the sharing either owns 192.168.137.1 on this PC or the round
# has no upstream, and finding that out here costs nothing, while finding it
# out in step 3 costs the moved port and two minutes of console polling.
if ipconfig | tr -d '\r' | grep -aq '192\.168\.137\.1'; then
	echo "natround: the sharing is really on - this PC holds 192.168.137.1"
	ICS_ON=1
else
	echo "natround: the sharing is NOT on (this PC has no 192.168.137.1) - stopping before the board is left with no upstream"
	exit 1
fi

say "3. the board takes a lease on the shared subnet"
# Moving the port is not the same as the wan interface being brought up:
# netifd has to be told to bring the new wan up, or it stays down and the
# board never asks the sharing's DHCP server for a lease.  The console is
# used for that because the board's LAN address is already gone from this
# PC's segment by this point.
noconv q.py 'ifup wan' 2>&1 | tail -4

# The board's address on the shared subnet is read over the CONSOLE, not by
# scanning for it with ssh.  Two reasons, and both of them measured:
#
#   * dropbear listens on the lan only, so an ssh scan of the shared subnet
#     cannot find the board at all - the ssh port is not open there until
#     step 4, and step 4 needs the address that step 3 was going to find;
#   * the neighbour table is useless: Windows left an entry for every
#     address in 192.168.137.0/24 with the board's own MAC on all of them.
#
# The console answers while the board is booting, while its DHCP client is
# still asking, and while it is unreachable from this PC at all.
WAN=""
for i in $(seq 1 30); do
	addr=$(noconv q.py 'ip -4 addr show lan2 | grep inet' 2>/dev/null |
		grep -o '192\.168\.137\.[0-9]*' | head -1 || true)
	if [ -n "$addr" ]; then
		WAN=$addr
		break
	fi
	sleep 4
done
if [ -z "$WAN" ]; then
	echo "the board has no address on the shared subnet yet"
	echo "the board's own view:"
	noconv q.py 'ip link show lan2' 'ip route show default' 2>&1 | tail -12
	exit 1
fi
echo "the board is at $WAN"

say "4. dropbear listens on the wan as well"
noconv q.py 'uci set dropbear.main.Interface="lan wan"' 'uci commit dropbear' \
	'/etc/init.d/dropbear restart' 2>&1 | tail -8

say "5. a client behind the router"
sh clientrun.sh "$WAN" 2>&1 | tail -20

say "6. the evidence"
sh natcounters.sh "$WAN" 2>&1
echo
sh natevidence.sh "$WAN" 2>&1

say "7. putting everything back"
# The sharing is still on here on purpose: the board's wan address is the one
# the restore talks to, and that address only exists while the sharing holds
# 192.168.137.1.  No `| tail` either - the restore's own verdict (did the
# port really go back, proven by reading the bridge over the console) has to
# reach this script's exit status.
sh restore.sh "$WAN"
RESTORED=1
if command -v pwsh >/dev/null 2>&1; then
	pwsh -NoProfile -File "$UPSTREAM" -Mode off 2>&1 | tail -4
else
	powershell -NoProfile -ExecutionPolicy Bypass -File "$UPSTREAM" -Mode off 2>&1 | tail -4
fi
ICS_ON=0

# Turning the sharing off does not always give the wired adapter its
# 192.168.9.25 back.  On 2026-10-25 it was left holding the sharing address
# 192.168.137.1 and nothing else, and the board - which was fine, with
# br-lan up and 192.168.9.1 on it - stopped answering pings because the PC
# was no longer on its segment.  The board's address and the PC's have to be
# on the same segment for anything to work.
if [ -f "$ROOT/staging/ctl/ensurebenchip.ps1" ]; then
	echo
	echo "=== making sure this PC is back on the board's segment ==="
	if command -v pwsh >/dev/null 2>&1; then
		pwsh -NoProfile -File "$(cygpath -w "$ROOT/staging/ctl/ensurebenchip.ps1")" 2>&1 | tail -4
	else
		powershell -NoProfile -ExecutionPolicy Bypass -File "$(cygpath -w "$ROOT/staging/ctl/ensurebenchip.ps1")" 2>&1 | tail -4
	fi
fi

echo
echo "the round is done; the board's own address is $WAN on the shared subnet"
