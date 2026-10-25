#!/bin/bash
# Rehearse the Compile step's watchdog against fake builds.
#
# The watchdog that ships is tools/compile-watchdog.sh, and this runs THAT
# file.  The two used to be separate copies, and they drifted: the shipped
# rule and the rehearsed rule were not the same rule, which is the one thing
# a rehearsal cannot be allowed to be.
#
# It watches the build two ways, and they have to agree before the build is
# stopped:
#
#   1. the build's own log - make prints the target it is working on, so a
#      log that has not grown is a build that is not working;
#   2. the build's files - a build that is working writes files.
#
# Each of them on its own has a case where the build is perfectly well, so
# the two cases below are the point of the exercise:
#
#   - a build that goes QUIET but keeps writing is compiling one big file.
#     It is left alone, because the files say it is working.
#   - a build that keeps PRINTING but writes nothing is stuck in a retry
#     loop.  It is stopped, by the rule that watches the files and not the
#     log.
#
# The contract, in one line: a build that is merely slow is left alone, and a
# build that has stopped is stopped.
#
#	bash tools/watchdog-dryrun.sh
#
# The timings are compressed so the rehearsal takes a minute: the workflow
# beats every 120 seconds and gives up after 6 of them, here it beats every
# second and gives up after BEAT_FREEZE of them.  The logic is the same code.
set -u
BEAT=${BEAT:-1}
BEAT_FREEZE=${BEAT_FREEZE:-5}
STALE_SECONDS=${STALE_SECONDS:-3}
HARD_STALE_SECONDS=${HARD_STALE_SECONDS:-8}
ROOT=${ROOT:-/tmp/wdtest}
HERE=$(cd "$(dirname "$0")" && pwd)
rm -rf "$ROOT"
mkdir -p "$ROOT/tree/build_dir/pkg-a"
cd "$ROOT/tree" || exit 1

# A stand-in for make: prints a line every second, writes a file every
# second, and can be told to go quiet or to stop writing.
cat > fake.sh <<'EOF'
#!/bin/bash
i=0
while :; do
	i=$((i + 1))
	[ -f quiet ] || echo "CC [build_dir/pkg-a] building thing $i"
	[ -f silent ] || touch build_dir/pkg-a/object-$i.o
	sleep 1
done
EOF
chmod +x fake.sh

run_case() {
	name=$1
	quiet=$2
	silent=$3
	expect=$4
	rm -f compile-parallel.log compile-make.pid quiet silent
	rm -rf build_dir/pkg-a
	mkdir -p build_dir/pkg-a
	echo "=== case: $name (expected: $expect) ==="
	# The compile, started the way the workflow starts it: a shell that
	# writes its own pid and then BECOMES make, so the pid in the file is
	# make's pid and killing it kills the build.
	timeout --kill-after=5s 90m bash -c 'echo $$ > ../compile-make.pid; exec ./fake.sh' 2>&1 | tee ../compile-parallel.log &
	CPID=$!
	if [ "$quiet" = quiet ]; then
		sleep 2
		touch quiet
	fi
	if [ "$silent" = silent ]; then
		sleep 2
		touch silent
	fi

	WD=
	trap 'if [ -n "$WD" ]; then kill "$WD" 2>/dev/null || true; fi' EXIT
	(
		BEAT_SECONDS="$BEAT" \
		QUIET_BEATS="$BEAT_FREEZE" \
		STALE_SECONDS="$STALE_SECONDS" \
		HARD_STALE_SECONDS="$HARD_STALE_SECONDS" \
		bash "$HERE/compile-watchdog.sh" . ../compile-parallel.log \
		     ../compile-make.pid ../compile-watchdog.log
	) &
	WD=$!

	result=still-alive
	for i in $(seq 1 40); do
		sleep 1
		if ! kill -0 "$CPID" 2>/dev/null; then
			echo "  the build ended after ${i}s"
			result=stopped
			break
		fi
	done
	kill "$WD" 2>/dev/null || true
	wait "$WD" 2>/dev/null || true
	if [ "$result" = still-alive ]; then
		kill "$CPID" 2>/dev/null || true
		pkill -f fake.sh 2>/dev/null || true
	fi
	echo "  RESULT: $result"
	echo "  the last watchdog lines:"
	tail -4 ../compile-watchdog.log | sed 's/^/    /'
	if [ "$result" = "$expect" ]; then
		echo "  as expected"
	else
		echo "  NOT AS EXPECTED - the rule and the rehearsal disagree"
		FAILED=1
	fi
	echo
}

FAILED=0
run_case "a build that prints and writes" no no still-alive
run_case "a build that goes quiet but keeps writing" quiet no still-alive
run_case "a build that goes quiet and stops writing" quiet silent stopped
run_case "a build that prints but writes nothing" no silent stopped
if [ "$FAILED" = 0 ]; then
	echo "watchdog-dryrun: the contract holds"
else
	echo "watchdog-dryrun: THE CONTRACT DOES NOT HOLD"
	exit 1
fi
