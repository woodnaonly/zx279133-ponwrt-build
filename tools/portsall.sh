#!/bin/sh
# Put every 9132 UNI jack in br-lan, and leave no jack carved out as WAN.
#
#	sh tools/portsall.sh
#
# The LAN four-port measurement needs all four jacks to be the same kind of
# port.  The board is still in the shape the NAT round left it in - br-lan is
# lan1 lan2 lan4 lan-cpu0 and `network.wan.device` is lan3 - and a jack that
# netifd has taken for WAN answers nothing on 192.168.9.1, which would be
# read as "that jack is dead" when it is only a different interface.
#
# The WAN interface is pointed at eth0 rather than deleted, because eth0 is
# the NPPT netdev and not one of the four UNI jacks: it cannot steal a jack
# from the bridge.  It takes no lease, which is the same no-op it has always
# been, and the round that wants a WAN port moves it back afterwards.
#
# `uci commit` is not optional: the smart plug cuts this board for twenty
# seconds every fifteen and a half minutes, and a running config that was
# never committed is gone at the next cut (measured 2026-10-25, portmove.sh's
# header has the record).
set -eu
cd "$(dirname "$0")"
export MSYS_NO_PATHCONV=1

WANT_PORTS=${WANT_PORTS:-"lan1 lan2 lan3 lan4 lan-cpu0"}
WAN=${WAN:-eth0}
BIND=${SKD_BIND:-192.168.9.25}
HOST=${SKD_HOST:-192.168.9.1}
KEY="$HOME/.ssh/id_onu"
SSH="ssh -q -b $BIND -o BatchMode=yes -o PreferredAuthentications=publickey \
	-o IdentitiesOnly=yes -o ConnectTimeout=8 -o ServerAliveInterval=5 \
	-o ServerAliveCountMax=3 -o UserKnownHostsFile=/dev/null \
	-o StrictHostKeyChecking=no -i $KEY root@$HOST"

board() { $SSH sh -s; }

# A frame the CPU sends leaves through the port the IDM descriptor names, and
# that is compiled as one number, so the board can be linked on a jack and
# still answer nothing.  Say what to do about it instead of timing out.
if ! echo 'echo alive' | board >/dev/null 2>&1; then
	echo "portsall: the board does not answer ssh on $HOST from $BIND." >&2
	echo "portsall: if a jack links but nothing answers, sweep the LAN TX" >&2
	echo "portsall: output port with tools/txpwalk.sh and re-run this." >&2
	exit 1
fi

got=$(
	{
		echo 'uci -q delete "network.@device[0].ports"'
		for p in $WANT_PORTS; do
			echo "uci add_list \"network.@device[0].ports\"=$p"
		done
		echo "uci set network.wan.device=$WAN"
		echo "uci set network.wan6.device=$WAN"
		echo 'uci -q get "network.@device[0].ports"'
	} | board 2>&1 | tail -1 | tr -d '\r'
)
if [ "$got" != "$WANT_PORTS" ]; then
	echo "portsall: the port list is '$got', and it should be '$WANT_PORTS'" >&2
	echo "portsall: stopping here rather than measuring a bridge that is not" >&2
	echo "portsall: the one that was asked for" >&2
	exit 1
fi
echo "portsall: the port list is '$got'"

echo 'uci commit network' | board | tail -2
echo "portsall: wan is now $(echo 'uci -q get network.wan.device' | board | tail -1 | tr -d '\r')"

# The restart goes over the console and detached, because the board's LAN
# address leaves this PC's segment while it runs and the session it arrived
# on dies with it.
python q.py 'setsid /etc/init.d/network restart >/tmp/netr.log 2>&1 &' >/dev/null 2>&1
sleep 12

echo "portsall: after the restart, the running bridge is"
# Tested for content, not for a status: the read-back ends in `tail`, whose
# exit code is 0 even when the ssh in front of it never connected, so a
# status test here would pass on a dead board.
after=$(
	{
		echo 'uci -q get "network.@device[0].ports"'
		echo 'cat /sys/class/net/lan*/carrier | tr "\n" " "'
		echo 'echo'
	} | board 2>&1 | tail -3
)
if [ -z "$after" ]; then
	echo "portsall: ssh did not come back after the restart" >&2
	echo "portsall: the board is still on the console - tools/q.py" >&2
	exit 1
fi
echo "$after"
echo "portsall: done"
