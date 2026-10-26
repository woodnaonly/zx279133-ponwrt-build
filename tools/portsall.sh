#!/bin/sh
# Put every 9132 UNI jack in br-lan, and leave no jack carved out as WAN.
#
#	sh tools/portsall.sh
#
# This is the shape the image's own default produces (board.d/02_network), and the
# owner's rule behind it, stated 2026-10-26: 「默认都是 lan 口 需要 wan口的
# 用户自己去设置」.  It is also what tools/restore.sh runs at the end of a NAT
# round, so the default is held in exactly one script and a round cannot leave a
# jack reserved behind it.
#
# Why it exists at all: a jack that netifd has taken for WAN answers nothing on
# 192.168.9.1, so a measurement that walks the panel jacks without first putting
# them all in the bridge reports a working jack as dead.  That is how the LAN
# jacks were being mis-read before the egress defect was found (docs/90).
#
# The WAN interface is pointed at eth0 rather than deleted, because eth0 is
# the NPPT netdev and not one of the four UNI jacks: it cannot steal a jack
# from the bridge.  It takes no lease, which is the same no-op it has always
# been, and the round - or a user - that wants a WAN port moves it.
#
# `uci commit` is not optional: a running config that was never committed is gone
# at the next power cut, and the plug is now on the owner's hands (the timer was
# switched off on 2026-10-26, so a cut comes when they decide, not on a schedule
# that could be timed around).  portmove.sh's header carries the record of the
# change that was lost this way on 2026-10-25.
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
# The WAN half is compared, not printed.  A board whose bridge came back right and
# whose wan still names a UNI jack is the exact state this script exists to remove,
# and it is the one state that makes a panel jack look dead - so a read-back that
# only echoes cannot be the proof.  (It used to echo: `uci set network.wan.device=`
# on an image with no wan section fails quietly, the bridge check passed anyway, and
# the script said "done".)
got_wan=$(echo 'uci -q get network.wan.device' | board | tail -1 | tr -d '\r')
if [ "$got_wan" != "$WAN" ]; then
	echo "portsall: the wan is '$got_wan', and it should be '$WAN'" >&2
	echo "portsall: a wan on a UNI jack is a LAN port the user did not agree to lose" >&2
	exit 1
fi
got_wan6=$(echo 'uci -q get network.wan6.device' | board | tail -1 | tr -d '\r')
if [ "$got_wan6" != "$WAN" ]; then
	echo "portsall: the wan6 is '$got_wan6', and it should be '$WAN'" >&2
	exit 1
fi
echo "portsall: wan and wan6 are both on '$WAN'"
# Then the file, not only the running config.  Every read-back above went through
# `uci`, which merges the staged delta with the file, so it cannot tell a committed
# change from one that never landed - and a bridge that exists only until the next
# power cut is the failure this script's header says it exists to avoid (the plug
# is the owner's to pull, and they pull it).
#
# Checked per name, not by counting `^list ports` lines: uci writes the option with
# a leading tab, so an anchored count came back 0 on a file that held all five
# ports (measured 2026-10-26 16:5x, where this check failed a restore that had
# actually worked).  `echo file-ok` at the end is the positive control - without it
# a script that never ran and a file that matched would both print nothing.
file=$(
	{
		echo "for p in $WANT_PORTS; do grep -q \"list ports '\$p'\" /etc/config/network || echo \"missing \$p\"; done"
		echo "grep -q \"option device '$WAN'\" /etc/config/network || echo missing-wan"
		echo file-ok
	} | board 2>&1 | tr -d '\r'
)
case "$file" in
*file-ok*) : ;;
*)
	echo "portsall: the committed file was never read - the check itself did not run" >&2
	printf '%s\n' "$file" | sed 's/^/  /' >&2
	exit 1
	;;
esac
case "$file" in
*missing*)
	echo "portsall: /etc/config/network is not the default this script just wrote:" >&2
	printf '%s\n' "$file" | grep -a missing | sed 's/^/  /' >&2
	exit 1
	;;
esac
echo "portsall: the committed file carries $WANT_PORTS and the wan on $WAN"

# The restart goes over the console and detached, because the board's LAN
# address leaves this PC's segment while it runs and the session it arrived
# on dies with it.
python q.py 'setsid /etc/init.d/network restart >/tmp/netr.log 2>&1 &' >/dev/null 2>&1
sleep 12

echo "portsall: after the restart, the bridge on the running system is"
# The proof goes over the CONSOLE.  Handing the jacks back to the LAN is precisely
# the moment this PC's route to the board changes: after a NAT round the wan is
# eth0 again, so the address the restore arrived on (192.168.137.100) no longer
# exists on the board at all, and the sharing has not been switched off yet, so
# 192.168.9.25 does not exist on this PC either.  An ssh read-back there is not a
# proof of the change, it is a second and unrelated failure - measured 2026-10-26
# 16:2x, where the port list and `wan.device=eth0` were already correct, this
# check failed on ssh, and natround's trap then ran the whole restore a second
# time and reported a board still in wan mode when none was.
#
# Tested for content, never for a status: an empty answer and a wrong answer must
# not both look like success.
now=$(
	python q.py 'uci -q get "network.@device[0].ports"' 2>/dev/null |
		sed -n '/^### /,$p' | tail -n +2 | grep -av '^[[:space:]]*$' |
		head -1 | tr -d '\r'
)
if [ "$now" != "$WANT_PORTS" ]; then
	echo "portsall: the running bridge is '$now', and it should be '$WANT_PORTS'" >&2
	echo "portsall: the file on flash can be right while netifd never read it -" >&2
	echo "portsall: that is what this check exists for, so it stops the run" >&2
	exit 1
fi
echo "portsall: the running bridge is '$now'"
python q.py 'cat /sys/class/net/lan*/carrier' 2>&1 | tail -6
echo "portsall: done"
