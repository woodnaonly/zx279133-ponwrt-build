#!/bin/sh
# Put the bench back the way it was.
#
#   sh tools/restore.sh
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
# The port move and the dropbear change are done over the console, because
# the console does not depend on the network: the board's LAN address is
# gone from this PC's segment while lan2 is the wan, so ssh cannot be used
# to undo what ssh did.
#
# Git bash rewrites an argument that looks like an absolute path into a
# Windows one before Python sees it, which turns "/etc/init.d/..." into
# "C:/Program Files/Git/etc/init.d/...".  MSYS_NO_PATHCONV turns that off
# for the calls that are meant for the board.
set -e
cd "$(dirname "$0")"
export MSYS_NO_PATHCONV=1

echo "=== removing the client ==="
# Over the console, so it works whether or not the board is reachable.
python q.py 'ip netns del labcli' 'ip link del labhost0' 2>&1 | tail -6

echo
echo "=== dropbear back to the lan only ==="
# BEFORE the port moves back.  Once lan2 is in the bridge again the board's
# address on the shared subnet is gone, and the ssh session this round has
# been using arrives over that address - so the change is made while the
# session still has somewhere to arrive.
python q.py 'uci set dropbear.main.Interface="lan"' 'uci commit dropbear' \
	'/etc/init.d/dropbear restart' 2>&1 | tail -8

echo
echo "=== putting lan2 back into the bridge ==="
sh portmove.sh back 2>&1 | tail -20

echo
echo "=== restarting the network so the board answers on its lan again ==="
python q.py 'setsid /etc/init.d/network restart >/tmp/netr.log 2>&1 &' 2>&1 | tail -4

sleep 12
echo
echo "=== the board, back on its own address ==="
sh bssh2.sh cmd-portstate.txt 2>&1 || true
