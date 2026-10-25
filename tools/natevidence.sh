#!/bin/sh
# Read the NAT evidence from the board on its WAN address.
#
#   sh tools/natevidence.sh 192.168.137.215
#
# The board's LAN address is not reachable from this PC any more (lan2 left
# the bridge), so everything is read over its WAN address on the shared
# subnet, where this PC's firewall lets TCP through.
#
# What is looked for, and why each one matters:
#
#   the rule      the masquerade must be on the port the traffic actually
#                 leaves by, which is the wan port - a rule on a port with
#                 no cable masquerades nothing while looking exactly like a
#                 rule that works;
#   the counters  the rule's counter has to have moved, which is the only
#                 proof that packets went through it and not past it;
#   conntrack     the flow itself, with the client's address rewritten to
#                 the board's wan address;
#   the client    a client behind the router, in its own network namespace,
#                 with a lease from the board and a ping that came back.
set -e
cd "$(dirname "$0")"

WAN=${1:-192.168.137.215}
KEY="$HOME/.ssh/id_onu"
SSH="ssh -q -o BatchMode=yes -o PreferredAuthentications=publickey -o IdentitiesOnly=yes -o ConnectTimeout=6 -o UserKnownHostsFile=/dev/null -o StrictHostKeyChecking=no -i $KEY"

echo "=== the board on $WAN ==="
$SSH "root@$WAN" 'uname -r; echo "--- the masquerade rule"; nft list table ip nat; echo "--- forwarding"; cat /proc/sys/net/ipv4/ip_forward; echo "--- the wan address"; ip -4 addr show lan2 | grep inet; echo "--- the default route"; ip route show default' 2>&1

echo
echo "=== the client's own evidence ==="
$SSH "root@$WAN" 'cat /tmp/labclient.status 2>/dev/null; echo "--- the namespace"; ip netns list' 2>&1

echo
echo "=== the traffic that went through ==="
$SSH "root@$WAN" 'cat /proc/net/nf_conntrack 2>/dev/null | grep -v "dst=192.168.9\." | head -10; echo "--- the wan tx counter"; cat /sys/class/net/lan2/statistics/tx_bytes' 2>&1
