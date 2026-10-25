#!/bin/sh
# Put the bench back the way it was.
#
#   sh tools/restore.sh [the board's wan address]
#
# Four things were changed for the NAT round and all four have to go back:
#
#   * the client (a veth pair and a network namespace) is removed, so the
#     board has no client behind it any more;
#   * lan2 goes back into the bridge and lan3 goes back to being the wan,
#     which is the image's own configuration;
#   * dropbear goes back to listening on the lan only, which is what the
#     image ships;
#   * and this PC's sharing goes off, so the wired adapter has its
#     192.168.9.25 back.
#
# The order is the point, and it is the reverse of the order the round
# changed things in:
#
#   * the port move comes FIRST, and it goes over ssh to the board's WAN
#     address - the address the round found the board on.  It cannot go to
#     192.168.9.1: lan2 is the wan at this point, so the board's LAN
#     address is not on this PC's segment and never was.
#   * dropbear goes back to the lan only AFTER that, because the ssh the
#     port move arrived on is the one that has to be listening on the wan
#     until the move is done.
#   * the client's namespace goes first of all, over the console, so it
#     works whether or not the board is reachable at all.
#
# Git bash rewrites an argument that looks like an absolute path into a
# Windows one before Python sees it, which turns "/etc/init.d/..." into
# "C:/Program Files/Git/etc/init.d/...".  MSYS_NO_PATHCONV turns that off
# for the calls that are meant for the board.
set -e
cd "$(dirname "$0")"
export MSYS_NO_PATHCONV=1

WAN=${1:-}

echo "=== removing the client ==="
# Over the console, so it works whether or not the board is reachable.
python q.py 'ip netns del labcli' 'ip link del labhost0' 2>&1 | tail -6

echo
echo "=== putting lan2 back into the bridge ==="
# Over ssh, to the address the round found the board on.  The port move
# itself used ssh too (the change and its commit are made before the
# network is restarted, so they have no reason to be on the fragile
# console path), and the restore is the same change in the other
# direction.
if [ -n "$WAN" ]; then
	# The source address has to be one this PC actually holds *at this
	# moment*.  The round restores while the sharing is still on, and turning
	# the sharing on reconfigures the wired adapter - it then holds
	# 192.168.137.1 and nothing else, so `ssh -b 192.168.9.25` fails with a
	# bind error before it reaches the board.  That used to be invisible: the
	# call was piped through `tail`, whose status is the one the script saw,
	# so the port was never put back, the board kept the committed wan, and
	# the log still said the round was done.  Measured 2026-10-26 01:12.
	if ipconfig | tr -d '\r' | grep -aq '192\.168\.137\.1'; then
		export SKD_BIND=192.168.137.1
		echo "the sharing is still on: this PC reaches the board's wan from 192.168.137.1"
	fi
	# No `| tail` here: `portmove.sh back` reads the bridge's port list back
	# over the console and exits 1 when it is not the list it was asked for,
	# and that judgement is the whole point of putting the port back.
	SKD_HOST="$WAN" sh portmove.sh back
else
	sh portmove.sh back
fi

echo
echo "=== dropbear back to the lan only ==="
# AFTER the port moves back: the ssh the port move arrived on is the one
# that was listening on the wan.
python q.py 'uci set dropbear.main.Interface="lan"' 'uci commit dropbear' \
	'/etc/init.d/dropbear restart' 2>&1 | tail -8

echo
echo "=== restarting the network so the board answers on its lan again ==="
python q.py 'setsid /etc/init.d/network restart >/tmp/netr.log 2>&1 &' 2>&1 | tail -4

sleep 12
echo
echo "=== the board, back on its own address ==="
sh bssh2.sh cmd-portstate.txt 2>&1 || true
