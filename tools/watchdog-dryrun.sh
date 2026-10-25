#!/bin/bash
# Rehearse the Compile step's watchdog against fake builds.
#
# The watchdog that ships is tools/compile-watchdog.sh, and this runs THAT
# file.  The two used to be separate copies, and they drifted: the shipped
# rule and the rehearsed rule were not the same rule, which is the one thing
# a rehearsal cannot be allowed to be.
#
# It watches the build three ways - the log, the files, the processes - and
# they have to agree before the build is stopped:
#
#   1. the build's own log - make prints the target it is working on, so a
#      log that has not grown is a build that is not working;
#   2. the build's files - a build that is working writes files;
#   3. the build's processes - a build that is working has started
#      something a moment ago.
#
# Each of them on its own has a case where the build is perfectly well, so
# the cases below are the point of the exercise:
#
#   - a build that goes QUIET but keeps writing is compiling one big file.
#     It is left alone, because the files say it is working.
#   - a build that keeps PRINTING but writes nothing is stuck in a retry
#     loop.  It is stopped, by the rule that watches the files and not the
#     log.
#   - a build that writes files whose MODIFICATION TIME it did not choose -
#     a root filesystem installed with cp -a, which keeps the mtime of the
#     original - is working, and has to be seen as working.  This case is
#     the one that was wrong once: the watchdog asked whether any file was
#     NEWER than a marker, which is a question about the modification time,
#     and a copy that keeps its modification time looks like it was never
#     written at all.  A build that was halfway through installing a root
#     filesystem was stopped for that reason, on a real build, an hour in.
#     The question that is right is whether a file CHANGED, which is the
#     inode's change time, and that is what the watchdog asks now.
#
# The contract, in one line: a build that is merely slow is left alone, and a
# build that has stopped is stopped.
#
#	bash tools/watchdog-dryrun.sh
#
# The timings are compressed so the rehearsal takes a couple of minutes: the
# workflow beats every 120 seconds and gives up after 20 of them, here it
# beats every second and gives up after BEAT_* of them.  The logic is the
# same code.
set -u
BEAT=${BEAT:-1}
QUIET_SECONDS=${QUIET_SECONDS:-5}
WRITE_SECONDS=${WRITE_SECONDS:-8}
PROC_SECONDS=${PROC_SECONDS:-5}
HARD_WRITE_SECONDS=${HARD_WRITE_SECONDS:-20}
ROOT=${ROOT:-/tmp/wdtest}
HERE=$(cd "$(dirname "$0")" && pwd)
rm -rf "$ROOT"
mkdir -p "$ROOT/tree/build_dir/pkg-a"
cd "$ROOT/tree" || exit 1

# A template with an old modification time.  A build that copies this with
# cp -a produces files whose mtime is years old and whose change time is
# now: exactly what an install phase does to a root filesystem.
touch -d '2020-01-01 00:00:00' template.o 2>/dev/null || true

# A stand-in for make: prints a line every second, writes a file every
# second, and can be told to go quiet, to stop writing, or to write only
# copies that keep the modification time of the original.
cat > fake.sh <<'EOF'
#!/bin/bash
i=0
while :; do
	i=$((i + 1))
	[ -f quiet ] || echo "CC [build_dir/pkg-a] building thing $i"
	if [ -f silent ]; then
		:
	elif [ -f archaic ]; then
		cp -a template.o build_dir/pkg-a/object-$i.o 2>/dev/null
	else
		touch build_dir/pkg-a/object-$i.o
	fi
	sleep 1
done
EOF
chmod +x fake.sh

run_case() {
	name=$1
	quiet=$2
	silent=$3
	expect=$4
	archaic=${5:-none}
	rm -f compile-parallel.log compile-make.pid quiet silent archaic
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
	if [ "$archaic" = archaic ]; then
		sleep 2
		touch archaic
	fi

	WD=
	trap 'if [ -n "$WD" ]; then kill "$WD" 2>/dev/null || true; fi' EXIT
	(
		BEAT_SECONDS="$BEAT" \
		QUIET_SECONDS="$QUIET_SECONDS" \
		WRITE_SECONDS="$WRITE_SECONDS" \
		PROC_SECONDS="$PROC_SECONDS" \
		HARD_WRITE_SECONDS="$HARD_WRITE_SECONDS" \
		bash "$HERE/compile-watchdog.sh" . ../compile-parallel.log \
		     ../compile-make.pid ../compile-watchdog.log
	) &
	WD=$!

	result=still-alive
	for i in $(seq 1 45); do
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
	# What the two ways of asking "was anything written?" say about the
	# files the build left behind.  This is the whole point of the case
	# that copies with cp -a: the modification time sees nothing, and the
	# change time sees the copy.
	touch -d "@$(( $(date +%s) - 20 ))" ../probe-marker 2>/dev/null || true
	echo "  the modification-time question: $(find build_dir -type f -newer ../probe-marker 2>/dev/null | wc -l) files"
	echo "  the change-time question:     $(find build_dir -type f -cnewer ../probe-marker 2>/dev/null | wc -l) files"
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
run_case "a build that writes only copies with an old modification time" quiet no still-alive archaic
run_case "a build that goes quiet and stops writing" quiet silent stopped
run_case "a build that prints but writes nothing" no silent stopped
if [ "$FAILED" = 0 ]; then
	echo "watchdog-dryrun: the contract holds"
else
	echo "watchdog-dryrun: THE CONTRACT DOES NOT HOLD"
	exit 1
fi
