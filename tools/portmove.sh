#!/bin/sh
# Move the board's WAN port over the serial console, and commit it.
#
#   sh tools/portmove.sh [move|back]
#
# WHY THE CONSOLE
#
# The board is on a smart plug that cuts its power for about fifteen seconds
# every fifteen and a half minutes.  That is how the bench produces the cold
# boots the acceptance needs, and it also means a change that waits for
# something is racing the plug.  Measured on 2026-10-25: the port was moved
# over ssh at 10:25, the board rebooted before the lease could be read, and
# it came back with wan=lan3 - the change had gone with the power.
#
# The console is the way out of that: it does not depend on the network at
# all, so the change survives the port leaving the bridge (which is what
# takes the board's LAN address off this PC's segment).
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
#     uci -q get "network.@device[0].ports"   ->   lan1 lan2 lan4 lan-cpu0
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
# board's next boot reads the file on flash, which still says lan3.  The
# commit is what makes the change survive the plug's power cut - and it is
# also what makes it survive this round, if the round has to be redone.
#
# Git bash rewrites an argument that looks like an absolute path into a
# Windows one before Python sees it, which turns "/etc/init.d/..." into
# "C:/Program Files/Git/etc/init.d/...".  MSYS_NO_PATHCONV turns that off
# for the calls that are meant for the board.
set -e
cd "$(dirname "$0")"
export MSYS_NO_PATHCONV=1

MODE=${1:-move}

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

# The bridge's port list, as the board has it right now.
read_ports() {
	python q.py 'uci -q get "network.@device[0].ports"' 2>&1 |
		sed -n '/^### /,$p' | sed -e '1d' -e '/^$/d' | head -1
}

# Set the bridge's port list to exactly the ports given, and prove it.
#
# The list is written as a delete followed by one add per port, because that
# is the only way uci will write a list: `uci set` would store the whole
# thing as one string with spaces in it, and the bridge would then be told
# about a port called "lan1 lan4 lan-cpu0".
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
	echo "=== the move, over ssh ==="
	set_ports "lan1 lan4 lan-cpu0"
	set_wan lan2
	{
		echo 'uci commit network'
	} | $SSH sh -s 2>&1 | tail -2
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
back)
	echo
	echo "=== putting lan2 back into the bridge ==="
	set_ports "lan1 lan2 lan4 lan-cpu0"
	set_wan lan3
	{
		echo 'uci commit network'
	} | $SSH sh -s 2>&1 | tail -2
	echo
	echo "=== restarting the network, over the console ==="
	console 'setsid /etc/init.d/network restart >/tmp/netr.log 2>&1 &'
	sleep 10
	;;
*)
	echo "usage: portmove.sh [move|back]" >&2
	exit 2
	;;
esac

echo
echo "=== after ==="
sh bssh2.sh cmd-portstate.txt 2>&1 || true
