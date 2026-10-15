#!/bin/sh
# Wait for the newest published skd840n initramfs release to move past a given run.
#
# tools/watchrelease.sh needs the exact run id, and finding one means api.github.com -
# which this PC exhausted on 2026-10-12 mid-build. A push can queue several runs, and the
# kit wanted is simply the newest, so this polls the release feed (no quota) for an
# initramfs tag whose trailing run id is greater than the one already superseded.
#
#	sh tools/watchnewer.sh <run id already have> [minutes]
set -u
HAVE=${1:?run id of the newest kit already on disk}
MIN=${2:-90}
REPO=woodnaonly/zx279133-ponwrt-build
FEED=https://github.com/$REPO/releases.atom
END=$(( $(date +%s) + MIN * 60 ))
while [ "$(date +%s)" -lt "$END" ]; do
	tags=$(curl -s -m 40 -x http://127.0.0.1:10808 "$FEED" |
		grep -o 'releases/tag/skd840n_initramfs_[^"<]*' |
		sed 's|releases/tag/||')
	new=$(echo "$tags" | awk -F_ -v h="$HAVE" '$4+0 > h+0' | sort -t_ -k4 -nr | head -3)
	if [ -n "$new" ]; then
		echo "$(date +%H:%M:%S) newer release(s) published:"
		echo "$new"
		exit 0
	fi
	echo "$(date +%H:%M:%S) newest initramfs tag still at or below run $HAVE"
	sleep 120
done
echo "timed out waiting for a run newer than $HAVE"
exit 1
