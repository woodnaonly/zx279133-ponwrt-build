#!/bin/sh
# Poll one Actions run until it finishes, so a long kernel build does not need to be
# watched by hand.  Prints "<time> <sha-short> <status> <conclusion>".
RUN=${1:?run id}
SHA=${2:-run}
i=0
while [ $i -lt 90 ]; do
	i=$((i + 1))
	line=$(curl -s -m 30 "https://api.github.com/repos/woodnaonly/zx279133-ponwrt-build/actions/runs/$RUN" |
		python -c 'import json,sys; d=json.load(sys.stdin); print(d.get("status"), d.get("conclusion"))' 2>/dev/null)
	echo "$(date +%H:%M:%S) $SHA $line"
	case "$line" in
		completed*) echo finished; exit 0 ;;
	esac
	sleep 60
done
echo "gave up polling $RUN"
