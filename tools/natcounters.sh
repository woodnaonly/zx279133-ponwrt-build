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
# The netdev is asked for, not assumed.  This line used to name lan2, which was
# only right while the round always took lan2; read against a different jack it
# reports "the wan carried nothing" about a wan that is working fine.
wanif=$($SSH "root@$WAN" 'uci -q get network.wan.device' 2>&1 | tr -d '\r' | tail -1)
case "$wanif" in
"") echo "natcounters: network.wan.device did not come back from $WAN" >&2; exit 1 ;;
*[^a-zA-Z0-9._-]*) echo "natcounters: refusing the device name '$wanif'" >&2; exit 1 ;;
esac
echo "the wan is $wanif"
$SSH "root@$WAN" "cat /sys/class/net/$wanif/statistics/tx_bytes; echo '--- rx'; cat /sys/class/net/$wanif/statistics/rx_bytes" 2>&1

echo
echo "=== and the client, from inside its own namespace ==="
$SSH "root@$WAN" 'NS=$(ip netns list | awk "{print \$1}"); echo "namespace: $NS"; ip netns exec $NS ip -4 addr show; echo "--- its route"; ip netns exec $NS ip route show default; echo "--- a ping from it"; ip netns exec $NS ping -c 2 -W 4 223.5.5.5 2>&1 | tail -4' 2>&1
