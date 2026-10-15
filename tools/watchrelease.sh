#!/bin/sh
# Wait for a specific Actions run to publish its release, without api.github.com.
#
# The REST API is 60 requests/hour per IP unauthenticated, and this PC hit that limit
# on 2026-10-12 while a build was running - a poller on /actions/runs then reports
# "None None" forever, which looks like a hung build but is only a quota. The release
# feed is plain HTML/XML with no such limit, and the run id is part of every tag this
# workflow pushes, so the tag itself is the completion signal.
#
#	sh tools/watchrelease.sh <run id> [minutes]
set -u
RUN=${1:?run id}
MIN=${2:-90}
REPO=woodnaonly/zx279133-ponwrt-build
FEED=https://github.com/$REPO/releases.atom
# github.com itself is reset on a direct connection from this PC (curl http=000), while
# the local proxy serves it; api.github.com works direct but is quota-limited.
get() { curl -s -m 40 -x http://127.0.0.1:10808 "$FEED" || curl -s -m 40 "$FEED"; }
END=$(( $(date +%s) + MIN * 60 ))
while [ "$(date +%s)" -lt "$END" ]; do
	tags=$(get | grep -o 'releases/tag/[^"<]*' | sed 's|releases/tag/||' | head -20)
	if echo "$tags" | grep -q "$RUN"; then
		echo "$(date +%H:%M:%S) release published:"
		echo "$tags" | grep "$RUN"
		exit 0
	fi
	echo "$(date +%H:%M:%S) no tag for run $RUN yet"
	sleep 120
done
echo "timed out waiting for run $RUN"
exit 1
