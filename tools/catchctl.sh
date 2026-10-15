#!/bin/sh
# Watch the control round and read back the one fact that decides it.
#
# Waits for the board to stop answering (the jump), then for it to answer again, and asks
# it immediately for /proc/cmdline, /proc/uptime and uname -v.  `ctltag=` in that cmdline
# is the only token a boot through U-Boot cannot produce, because it was added by
# kexec --command-line and lives nowhere in flash or in the environment.
#
#	sh tools/catchctl.sh [seconds]        # default 420
set -u
cd "$(dirname "$0")/.."
WAIT=${1:-420}
TAG=${CTLTAG:-K1}
KEY=${KEY:-$HOME/.ssh/id_onu}
GONE=0
T0=$(date +%s)
END=$(( T0 + WAIT ))
LOG=staging/ctl/round-$(date +%H%M%S).log

ssh0() { timeout 8 ssh -q -o BatchMode=yes -o IdentitiesOnly=yes -i "$KEY" \
	-o UserKnownHostsFile=/dev/null -o StrictHostKeyChecking=no -o ConnectTimeout=4 "root@$1" "$2" 2>/dev/null; }

reachable() {
	for ip in 192.168.9.1 192.168.9.2; do
		ssh0 "$ip" true >/dev/null && { echo "$ip"; return 0; }
	done
	return 1
}

echo "$(date +%H:%M:%S) waiting for the jump (board must stop answering); tag=$TAG"
while [ "$(date +%s)" -lt "$END" ]; do
	if [ "$GONE" = 0 ]; then
		if ! reachable >/dev/null; then
			GONE=1
			echo "$(date +%H:%M:%S) gone at +$(( $(date +%s) - T0 ))s - control payload has the board, or it hung"
		fi
	else
		ip=$(reachable)
		if [ -n "${ip:-}" ]; then
			echo "$(date +%H:%M:%S) back via $ip at +$(( $(date +%s) - T0 ))s - reading the verdict"
			{
				echo "== answered at $(date -u +%FT%TZ) via $ip, blackout ended +$(( $(date +%s) - T0 ))s after we first saw silence"
				ssh0 "$ip" 'date; echo "-- cmdline"; cat /proc/cmdline; echo "-- uptime"; cat /proc/uptime; echo "-- uname"; uname -a; echo "-- dtb chosen"; cat /proc/device-tree/chosen/bootargs 2>/dev/null; echo'
			} | tee "$LOG"
			CL=$(ssh0 "$ip" 'cat /proc/cmdline')
			UP=$(ssh0 "$ip" 'cut -d. -f1 /proc/uptime')
			CTL=$(printf '%s' "$CL" | sed -n 's/.*ctltag=\([^ ]*\).*/\1/p')
			echo "gone=$GONE uptime=${UP}s ctltag=${CTL:-none}" >>"$LOG"
			if [ "${CTL:-none}" = "$TAG" ]; then
				echo
				echo "VERDICT: kexec handoff WORKS - this system was started by kexec, not by U-Boot."
				echo "         The 8 s watchdog heartbeat, not the handoff, is what killed control round 1,"
				echo "         and every mainline round still has to be repeated under this condition."
			elif [ "${GONE}" = 1 ]; then
				echo
				echo "VERDICT: the board rebooted with ctltag=${CTL:-absent}, not $TAG -> the payload never became"
				echo "         the running system. Under MASK=1 that means the handoff itself is broken."
			else
				echo
				echo "VERDICT: no jump observed (uptime ${UP:-?}s) - --exec never took control."
			fi
			echo "log kept in $LOG"
			exit 0
		fi
	fi
	sleep 2
done
echo "$(date +%H:%M:%S) timed out after ${WAIT}s (gone=$GONE)"
if [ "$GONE" = 1 ]; then
	echo "VERDICT: the board left the flashed system and never came back. With the reset path masked that is"
	echo "         the expected shape of a payload that does not boot at all - cycle power to get the flashed"
	echo "         system back; nothing was written to flash."
else
	echo "VERDICT: the board never went away, so --exec did not run."
fi
exit 1
