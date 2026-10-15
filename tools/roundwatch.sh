#!/bin/sh
# One-shot evidence watcher for a SK-D840N kexec round, run from the PC.
#
# It records the wired link state and tries ssh at the trial address, and the first
# answer cancels the RAM image's canary reboot. The link column is not decoration: the
# three GE jacks only lose link when mainline takes the board over (no 9132 driver), so
# "lan jack, no link" distinguishes "mainline is running but silent" from "still 4.19"
# without a serial console.
#
#	sh tools/roundwatch.sh [minutes]
set -u
cd "$(dirname "$0")/.."
MIN=${1:-15}
IP=${IP:-192.168.9.1}
KEY=${KEY:-$HOME/.ssh/id_onu}
IFIDX=${IFIDX:-19}
LOG=staging/roundwatch-$(date +%H%M%S).log
END=$(( $(date +%s) + MIN * 60 ))
prev=""
while [ "$(date +%s)" -lt "$END" ]; do
	ts=$(date +%H:%M:%S)
	st=$(powershell.exe -NoProfile -Command "(Get-NetAdapter -InterfaceIndex $IFIDX).MediaConnectionState" 2>/dev/null | tr -d '\r\n')
	ans=$(timeout 8 ssh -q -o BatchMode=yes -o IdentitiesOnly=yes -i "$KEY" \
		-o UserKnownHostsFile=/dev/null -o StrictHostKeyChecking=no -o ConnectTimeout=4 \
		"root@$IP" 'touch /tmp/trial-noreboot; echo "KERN=$(uname -r) U=$(cut -d. -f1 /proc/uptime) MODEL=$(tr -d \0 < /proc/device-tree/model) LOADED=$(cat /sys/kernel/kexec_loaded 2>/dev/null)"' 2>/dev/null)
	if [ -n "$ans" ]; then
		line="$ts link=$st ANSWER: $ans"
	else
		line="$ts link=$st no-answer"
	fi
	[ "$line" = "$prev" ] || echo "$line" | tee -a "$LOG"
	prev=$line
	if [ -n "$ans" ]; then echo "canary cancelled; keep this window's log: $LOG" | tee -a "$LOG"; exit 0; fi
	sleep 8
done
echo "no answer within ${MIN}m; log: $LOG" | tee -a "$LOG"
exit 1
