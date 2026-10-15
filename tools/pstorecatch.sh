#!/bin/sh
# Pull mainline's pstore log out of the board the second it is back on its own kernel.
#
# The announce payload prints its whole report to /dev/kmsg before it tries the network, and the
# board DTS reserves a 2 MiB no-map ramoops window at 0x9c000000 (console-size 1 MiB), so that
# text survives the warm watchdog reset. What does not survive is *waiting*: to the 4.19 system
# that window is ordinary RAM, and its page allocator recycles it within about a minute - the
# three rounds on 2026-10-13 were read 80-140 s after the eject and came back as vendor junk.
# So this polls until the installed kernel answers and grabs the region immediately, then hands
# it to tools/pstore2report.py, which appends it to $LOGF so tools/gatewatch.sh scores a pstore
# round exactly like a TCP one.
#
#	CTLTAG=K5 sh tools/pstorecatch.sh &      # start before firing the jump
set -u
cd "$(dirname "$0")/.."
HOSTS=${HOSTS:-"192.168.9.1 192.168.9.2"}
KEY=${KEY:-$HOME/.ssh/id_onu}
WAIT=${WAIT:-420}
# Two phases, because "the installed kernel answers" is its normal state - the first round this
# ran captured the region three seconds in, before the jump had even fired, and read the vendor
# system's own live log buffer. So: prove control left the board first, then catch it returning.
LEAVE=${LEAVE:-240}
TAG=${CTLTAG:-K5}
LOGF=${LOGF:-staging/ctl/reports.log}
OUT=${OUT:-staging/ctl/ramoops-$(date +%H%M%S).bin}
say() { echo "$(date '+%T') pstorecatch: $*"; }

sshq() { timeout 8 ssh -q -n -o BatchMode=yes -o IdentitiesOnly=yes -i "$KEY" \
	-o UserKnownHostsFile=/dev/null -o StrictHostKeyChecking=no -o ConnectTimeout=3 "root@$1" "$2" 2>/dev/null; }

# Who answers, as the installed kernel. Any of the addresses may be it: 192.168.9.1 on the lan
# bridge under 4.19, and eth0's static 192.168.9.2 as well.
who() {
	for h in $HOSTS; do
		K=$(sshq "$h" 'uname -r' | head -1)
		case "$K" in 4.19*) echo "$h"; return;; esac
	done
}

say "phase 1: waiting up to $LEAVE s for the installed kernel to LOSE the board"
end=$(( $(date +%s) + LEAVE * 60 ))
GONE=""
while [ "$(date +%s)" -lt "$end" ]; do
	[ -z "$(who)" ] && { GONE=1; say "no installed kernel answering at $(date '+%T') - control has moved"; break; }
	sleep 2
done
[ -n "$GONE" ] || { say "the board never left 4.19 within $LEAVE s: the jump did not happen. Nothing captured."; exit 1; }

say "phase 2: waiting up to $WAIT s for it to come back, then grabbing the window immediately"
end=$(( $(date +%s) + WAIT * 60 ))
HIT=""
while [ "$(date +%s)" -lt "$end" ]; do
	H=$(who)
	if [ -n "$H" ]; then HIT=$H; break; fi
	sleep 2
done
[ -n "$HIT" ] || { say "still away after $WAIT s: the payload owns the board and answers nothing - which with MASK=1 is the useful case, and nothing was written to flash"; exit 1; }

say "$HIT is back as $(sshq "$HIT" 'uname -r' | head -1) at uptime $(sshq "$HIT" 'cut -d. -f1 /proc/uptime') s - capturing now"
ONU=$HIT python tools/getramoops.py "$OUT" 2>&1 | sed 's/^/  | /'
# Every region the payload could have written, not just the ramoops window: the window is no-map
# in *our* dts but the installed 4.19 does not reserve it, so a capture taken a minute after the
# eject finds vendor page cache there. The four extras are addresses a marker test measured to
# survive that long, and which of them still holds this round's tag is the fact worth having.
BASE=${OUT%.bin}
FILES="$OUT"
for f in $BASE-*.bin; do
	[ -s "$f" ] && FILES="$FILES $f"
done
for f in $FILES; do
	say "reading $f for tag=$TAG"
	LOGF="$LOGF" python tools/pstore2report.py "$f" "$TAG" --append 2>&1 | sed 's/^/  | /'
done
say "raw window kept in $OUT"
