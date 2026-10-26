#!/bin/sh
# Take one panel jack away from the LAN bridge and hand it to the WAN, and commit it.
#
#   sh tools/portmove.sh move [wan-port]      # default wan-port: lan2
#
# This is the bench's NAT round asking for a topology the *image default refuses
# to give*: the shipped default leaves every UNI jack in br-lan and points the
# `wan` slot at eth0 (board.d/02_network), because the owner's rule is 「默认都是
# lan 口 需要 wan口的 用户自己去设置」.  So this script is one of those "users",
# it says which jack it wants, and tools/portsall.sh puts the default back when
# the round is over.  There used to be a `back` mode here that hard-coded the old
# default - it restored `lan1 lan2 lan4 lan-cpu0` plus `wan=lan3`, and every NAT
# round therefore wrote a LAN-only user's worst case back into flash.  It is gone,
# and one default lives in one place.
#
# The bridge is read from the board rather than written from a constant, and the
# jack that is *currently* the WAN is put back into it first.  Both of those are
# about the same thing: a round that assumes "the bridge is what the last round
# left" measures a jack that is not really a LAN port, and reports it dead.
#
# WHY THE CONSOLE
#
# The change has to survive the board's LAN address leaving this PC's segment,
# and it has to survive a power cut - the plug is on the owner's hands now (they
# switched the timer off on 2026-10-26), which makes it a thing that *can* happen
# mid-round rather than a schedule to race.  Measured on 2026-10-25: the port was
# moved over ssh at 10:25, the board rebooted before the lease could be read, and
# it came back with the last committed WAN - the change had gone with the power.
#
# The console is the way out of that for the steps that need it: it does not
# depend on the network at all.
#
# WHY THE QUOTES
#
# `uci delete network.@device[0].ports` fails on the console with a parse
# error, and the `uci add_list` after it then appends to the list that was
# already there.  The cause is the shell, not uci: `@device[0]` is a glob
# pattern, and the console's shell expands it against a directory that has
# no such name, so what reaches uci is not what was typed.  With the
# argument quoted, uci sees it literally:
#
#     uci -q get "network.@device[0].ports"   ->   lan1 lan2 lan3 lan4 lan-cpu0
#
# WHY ONE COMMAND AT A TIME
#
# The console's receive buffer is sixteen words.  A burst overruns it, and
# the board then swallows the rest of the line as a continuation.  The echo
# is checked, but a command that arrives *whole* can still arrive *late*:
# measured on 2026-10-25 12:00, a batch of four commands left the port list
# as
#
#     lan1 lan2 lan4 lan-cpu0 lan1 lan4 lan-cpu0
#
# - the delete had not happened and the three adds had, so the bridge kept
# lan2 and the board's lan address stayed on the wan port.  The same four
# commands, one at a time, produce the list that was asked for.  So each
# command goes on its own, and the list is read back and compared with what
# was wanted before anything else is done.
#
# WHY THE COMMIT
#
# Without `uci commit` the change is in the running config only, and the
# board's next boot reads the file on flash and puts the default back.  The
# commit is what makes the move survive a power cut - and it is also what
# makes it survive this round, if the round has to be redone.
#
# Git bash rewrites an argument that looks like an absolute path into a
# Windows one before Python sees it, which turns "/etc/init.d/..." into
# "C:/Program Files/Git/etc/init.d/...".  MSYS_NO_PATHCONV turns that off
# for the calls that are meant for the board.
set -e
cd "$(dirname "$0")"
export MSYS_NO_PATHCONV=1

MODE=${1:-move}
WAN_PORT=${2:-lan2}
# Refuse the two names that would make this script destroy the LAN instead of
# borrowing a jack from it.  `lan-cpu0` is the switch's CPU port: the bridge loses
# it, every jack stops reaching the router, and the `uci commit` below makes that
# survive the next power cut - so the board comes back with no LAN at all and no
# ssh, from a config that looks perfectly reasonable.  `eth0` is not in the bridge
# to begin with (it is the default wan), so "moving" the wan there changes nothing
# while reporting success.
case "$WAN_PORT" in
lan1|lan2|lan3|lan4) : ;;
lan-cpu0)
	echo "portmove: lan-cpu0 is the DSA CPU port, not a jack that can be the wan." >&2
	echo "portmove: it stays in the bridge on every round, or no jack reaches the" >&2
	echo "portmove: router at all - and the commit would keep it that way." >&2
	exit 2
	;;
*)
	echo "portmove: the wan must be one of lan1 lan2 lan3 lan4, not '$WAN_PORT'" >&2
	exit 2
	;;
esac

# The board over ssh, with the source address pinned: this PC has a static
# neighbour entry for 192.168.9.1 on the WLAN adapter, so an unpinned packet
# to the board leaves through the radio and never comes back.
BIND=${SKD_BIND:-192.168.9.25}
HOST=${SKD_HOST:-192.168.9.1}
KEY="$HOME/.ssh/id_onu"
SSH="ssh -q -b $BIND -o BatchMode=yes -o PreferredAuthentications=publickey \
	-o IdentitiesOnly=yes -o ConnectTimeout=8 -o ServerAliveInterval=5 \
	-o ServerAliveCountMax=3 -o UserKnownHostsFile=/dev/null \
	-o StrictHostKeyChecking=no -i $KEY root@$HOST"

# One command, on its own, over the console.
console() {
	python q.py "$1" 2>&1 | tail -4
}

# Set the bridge's port list to exactly the ports given, and prove it.
#
# The list is written as a delete followed by one add per port, because that
# is the only way uci will write a list: `uci set` would store the whole
# thing as one string with spaces in it, and the bridge would then be told
# about a port called "lan1 lan3 lan4 lan-cpu0".
#
# The commands go over SSH, and they used to go over the console.  Over the
# console they did not take effect on 2026-10-25 at 23:16: the delete
# reported nothing, changed nothing, and the three adds appended to the list
# that was already there - so the bridge kept lan2, the board's lan address
# stayed on the wan port, and the round went on with a bridge that was not
# the one it had asked for.  The delete works over ssh on the same board, in
# the same second, with the same arguments.
#
# What still has to be the console is the network restart: that is the step
# that takes the board's LAN address off this PC's segment, and the session
# it arrives on dies with it.
set_ports() {
	want="$*"
	got=$(
		{
			echo 'uci -q delete "network.@device[0].ports"'
			for p in $want; do
				echo "uci add_list \"network.@device[0].ports\"=$p"
			done
			echo 'uci -q get "network.@device[0].ports"'
		} | $SSH sh -s 2>&1 | tail -1 | tr -d '\r'
	)
	if [ "$got" != "$want" ]; then
		echo "the port list is '$got', and it should be '$want'" >&2
		echo "the round stops here rather than going on with a bridge" >&2
		echo "that is not the one that was asked for" >&2
		exit 1
	fi
	echo "the port list is '$got'"
}

# The wan port, over ssh, and the proof that it took.
set_wan() {
	want=$1
	got=$(
		{
			echo "uci set network.wan.device=$want"
			echo "uci set network.wan6.device=$want"
			echo 'uci -q get network.wan.device'
		} | $SSH sh -s 2>&1 | tail -1 | tr -d '\r'
	)
	if [ "$got" != "$want" ]; then
		echo "the wan port is '$got', and it should be '$want'" >&2
		exit 1
	fi
	echo "the wan port is '$got'"
}

echo "=== before ==="
sh bssh2.sh cmd-portstate.txt 2>&1 || true

case "$MODE" in
move)
	echo
	echo "=== the move, over ssh: $WAN_PORT becomes the wan ==="
	# The bridge as the board has it, plus the jack that is the wan right now.
	# Read, never assumed: the default keeps every UNI in the bridge, but a user
	# may have moved the wan to a jack of their own before this round started,
	# and a jack left behind outside the bridge looks, to the next measurement,
	# exactly like a jack that is broken.
	cur=$(
		{
			echo 'uci -q get "network.@device[0].ports"'
			echo 'uci -q get network.wan.device'
		} | $SSH sh -s 2>&1 | tr -d '\r'
	)
	ports=$(printf '%s\n' "$cur" | head -1)
	old_wan=$(printf '%s\n' "$cur" | sed -n '2p')
	case "$ports" in
	*lan*) ;;
	*)
		echo "portmove: the bridge's port list did not come back ('$ports')" >&2
		echo "portmove: refusing to write a list it had to guess" >&2
		exit 1
		;;
	esac
	want=""
	for d in $ports; do
		want="$want${want:+ }$d"
	done
	# The jack the round is leaving behind goes back in, but only if it was one of
	# the panel's jacks: on the shipped default the wan sits on eth0, and eth0 must
	# not be dragged into the LAN bridge by a round that only meant to move a jack.
	case "$old_wan" in
	lan*)
		case " $want " in
		*" $old_wan "*) : ;;
		*) want="$want${want:+ }$old_wan" ;;
		esac
		;;
	esac
	# The jack this round takes goes out - whatever the list came from.
	rest=""
	for d in $want; do
		[ "$d" = "$WAN_PORT" ] && continue
		rest="$rest${rest:+ }$d"
	done
	want=$rest
	# And the CPU port is never a candidate for the wan, because a bridge without
	# it reaches the jacks and never the router (board.d/02_network has the reason).
	case " $want " in
	*" lan-cpu0 "*) ;;
	*) want="$want${want:+ }lan-cpu0" ;;
	esac
	case " $ports " in
	*" $WAN_PORT "*) ;;
	*) echo "portmove: note, $WAN_PORT was not in the bridge to begin with" >&2 ;;
	esac
	set_ports "$want"
	set_wan "$WAN_PORT"
	# The commit is proven from /etc/config/network, not from `uci get`.  Both
	# read-backs above run *before* it, and a `uci` invocation merges the staged
	# delta with the file - so `uci get` after a commit that failed (a read-only
	# mount, a full jffs2) still answers with the new value while the board comes
	# back after the next power cut with the old one.  That is the failure this
	# script's own header was written about, and it was invisible.
	committed=$(
		{
			echo 'uci commit network'
			echo "grep -a -A3 \"interface .wan.\" /etc/config/network | tr -d '\\r'"
		} | $SSH sh -s 2>&1
	)
	case "$committed" in
	*"device '$WAN_PORT'"*) : ;;
	*)
		echo "portmove: /etc/config/network does not carry the wan on $WAN_PORT" >&2
		echo "portmove: the running config says one thing and the flash another -" >&2
		echo "portmove: the next power cut puts the board back, mid-round" >&2
		printf '%s\n' "$committed" | tail -5 >&2
		exit 1
		;;
	esac
	echo "the wan in the committed file is $WAN_PORT"
	# The network is restarted, because a changed config is not a changed
	# network: netifd reads the file when it is told to, and until then the
	# old bridge is still the bridge and the wan interface is still down.
	# The restart is detached, because the board's LAN address leaves this
	# PC's segment while it runs - the session it arrived on dies with it.
	echo
	echo "=== restarting the network, over the console ==="
	console 'setsid /etc/init.d/network restart >/tmp/netr.log 2>&1 &'
	sleep 10
	;;
*)
	echo "usage: portmove.sh move [wan-port]" >&2
	exit 2
	;;
esac

echo
echo "=== after ==="
sh bssh2.sh cmd-portstate.txt 2>&1 || true
