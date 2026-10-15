#!/bin/sh
# Catch the pstore/ramoops region of a self-ejecting kexec round, at the moment it is
# safe to read.
#
# The round is armed with MASK=0 (CRM left alone), so the vendor's already-armed zx_wdt -
# armed by 4.19 at 8 s and fed in-kernel - bites ~15-20 s after the jump and resets the
# chip *warm*: mainline's console log is still sitting in DRAM at the ramoops address when
# the flashed system comes back. This script therefore waits for the board to disappear
# (the jump), then for it to answer again (the eject), and dumps immediately - before the
# flashed system's page allocator wanders into the top of memory.
#
#	arm it alongside the jump:  DELAY=90 MASK=0 WIPE=1 setsid sh /tmp/trial/arm-exec.sh &
#	then run:  sh tools/catchramoops.sh [seconds]
#
# Both addresses are tried because which one answers depends on which jack the cable is
# in: 192.168.9.2 is eth0 (the 2.5G port, given a static address on 2026-10-12 so the
# flashed system is reachable through the same jack mainline uses), 192.168.9.1 is the lan
# bridge.
set -u
cd "$(dirname "$0")/.."
WAIT=${1:-420}
STAMP=$(date +%H%M%S)
BIN=staging/ramoops-$STAMP.bin
OUT=$BIN.txt
GONE=0
END=$(( $(date +%s) + WAIT ))

reachable() {
	for ip in 192.168.9.2 192.168.9.1; do
		timeout 6 ssh -q -o BatchMode=yes -o IdentitiesOnly=yes -i "$HOME/.ssh/id_onu" \
			-o UserKnownHostsFile=/dev/null -o StrictHostKeyChecking=no -o ConnectTimeout=3 \
			"root@$ip" true 2>/dev/null && { echo "$ip"; return 0; }
	done
	return 1
}

echo "$(date +%H:%M:%S) waiting for the jump (board must stop answering)"
while [ "$(date +%s)" -lt "$END" ]; do
	if [ "$GONE" = 0 ]; then
		if ! reachable >/dev/null 2>&1; then
			GONE=1
			echo "$(date +%H:%M:%S) gone - mainline has the board"
		fi
	else
		ip=$(reachable)
		if [ -n "${ip:-}" ]; then
			echo "$(date +%H:%M:%S) back via $ip - dumping the ramoops region now"
			if ONU=$ip python tools/getramoops.py "$BIN" >"$OUT" 2>&1; then
				cat "$OUT"
				echo "raw region kept in $BIN"
				exit 0
			fi
			echo "dump failed:"; cat "$OUT"; exit 1
		fi
	fi
	sleep 2
done
echo "$(date +%H:%M:%S) timed out after ${WAIT}s (gone=$GONE) - the board never came back on its own"
exit 1
