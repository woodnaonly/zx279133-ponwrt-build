#!/bin/sh
# Sweep the 9132 driver's LAN TX output port and say which jack answers.
#
#	sh tools/txpwalk.sh			# cable where it is
#	PORTS="4 5" sh tools/txpwalk.sh
#
# "txp N" writes the output port of the IDM TX descriptor's LAN path
# (zx279133-np.c:1538) live, with no rebuild and no flash write.  The compiled
# default is ZX279133_LAN_TX_PORT, so every frame the CPU sends to a LAN jack
# leaves through one jack whatever the DSA tag asked for.  Exactly one value
# can answer the PC with the cable where it is, and which value that is
# measures the descriptor's port number against the bench.
#
# The console is the control channel and the PC is the sensor.  ssh is not
# used on purpose: a frame that goes out the wrong jack is also a frame that
# answers no ARP, so the thing being measured would take the channel down with
# it and the round would read the failure as "the board is gone".
set -u
HERE=$(CDPATH= cd -- "$(dirname "$0")" && pwd)
CTL=${SKD_CTL:-$HERE/../../staging/ctl}
LOG=${LOG:-$CTL/txpwalk.log}
PORTS=${PORTS:-"1 2 3 4"}
PC=${PC:-192.168.9.25}
BOARD=${BOARD:-192.168.9.1}
DEBUG=${DEBUG:-/sys/kernel/debug/zx279133-9132}
DEFAULT_PORT=${DEFAULT_PORT:-2}
COUNT=${COUNT:-4}

q() { timeout 60 python "$HERE/q.py" "$1" 2>&1 | sed -e '1d' -e '/^$/d'; }
uptime() { q "cat /proc/uptime" | head -1 | cut -d. -f1; }

# Which jack the cable is in, from the PHY rather than from anyone's memory.
carry() {
	q "ip -br link" | awk '$1 ~ /^lan[0-9]@/ {
		print $1 "=" (index($0, "LOWER_UP") > 0 ? 1 : 0)
	}' | tr '\n' ' '
}

# The board's own ping needs ~5 s and the console hands the command over at
# once, so it goes to a file and the file is read after the PC has had its
# turn - which also makes the two directions overlap instead of serialise.
start_board_ping() {
	q "setsid sh -c 'ping -c $COUNT -W 1 $PC > /tmp/bp.log 2>&1' &" >/dev/null
}

pc_ping() {
	# Counted from the replies, not from the summary line: this PC writes
	# the summary in the console's codepage, and "TTL" is capitalised.  A
	# ping that could not run at all is said so rather than counted as 0,
	# because "0 replies" is the answer this script is looking for.
	out=$(ping -S "$PC" -4 -n "$COUNT" -w 1500 "$BOARD" 2>&1 | tr -d '\r')
	rc=$?
	if [ $rc -gt 1 ]; then
		echo "ping-failed($rc)"
		return
	fi
	printf '%s' "$out" | grep -aci 'ttl='
}

U0=$(uptime)
{
	echo "=== txpwalk $(date '+%F %T') uptime=${U0}s ports: $PORTS"
	echo "carrier: $(carry)"
} | tee -a "$LOG"

for p in $PORTS; do
	q "echo txp $p > $DEBUG" >/dev/null
	start_board_ping
	# A negative ARP entry left by the previous port would answer this ping
	# on the PC's behalf, so it is thrown away before measuring.
	arp -d "$BOARD" >/dev/null 2>&1
	tx=$(pc_ping)
	sleep 3
	rx=$(q "cat /tmp/bp.log" | grep -a 'packet loss')
	u=$(uptime)
	echo "txp=$p  pc->board=$tx/$COUNT  board->pc: ${rx:-no-answer}  uptime=${u}s" |
		tee -a "$LOG"
	if [ -n "$u" ] && [ "$u" -lt "$U0" ]; then
		echo "STOP: the board rebooted mid-sweep (uptime went backwards), so" |
			tee -a "$LOG"
		echo "STOP: txp is back at its compiled default and this table means" |
			tee -a "$LOG"
		echo "STOP: nothing.  Re-run it after the power settles." | tee -a "$LOG"
		exit 1
	fi
done

q "echo txp $DEFAULT_PORT > $DEBUG" >/dev/null
echo "=== done $(date '+%F %T'), txp left at the compiled default ($DEFAULT_PORT)" |
	tee -a "$LOG"
