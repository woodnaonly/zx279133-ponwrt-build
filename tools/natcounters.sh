#!/bin/sh
# The counters, and then the proof that the traffic went through the rule.
#
#   sh tools/natcounters.sh 192.168.137.215
#
# The rule's counter is the difference between "the rule is installed" and
# "the rule carried traffic".  A masquerade on a port with no cable is
# installed, looks right, and counts nothing.
set -e
cd "$(dirname "$0")"

WAN=${1:-192.168.137.215}
KEY="$HOME/.ssh/id_onu"
SSH="ssh -q -o BatchMode=yes -o PreferredAuthentications=publickey -o IdentitiesOnly=yes -o ConnectTimeout=8 -o UserKnownHostsFile=/dev/null -o StrictHostKeyChecking=no -i $KEY"

echo "=== the masquerade rule and its counter ==="
$SSH "root@$WAN" 'nft list table ip nat' 2>&1

echo
echo "=== the client's flow, with the address rewritten ==="
$SSH "root@$WAN" 'cat /proc/net/nf_conntrack 2>/dev/null | grep "src=192.168.9." | grep -v "dst=192.168.9." | head -5' 2>&1

echo
echo "=== the wan side's own counters ==="
$SSH "root@$WAN" 'cat /sys/class/net/lan2/statistics/tx_bytes; echo "--- rx"; cat /sys/class/net/lan2/statistics/rx_bytes' 2>&1

echo
echo "=== and the client, from inside its own namespace ==="
$SSH "root@$WAN" 'NS=$(ip netns list | awk "{print \$1}"); echo "namespace: $NS"; ip netns exec $NS ip -4 addr show; echo "--- its route"; ip netns exec $NS ip route show default; echo "--- a ping from it"; ip netns exec $NS ping -c 2 -W 4 223.5.5.5 2>&1 | tail -4' 2>&1
