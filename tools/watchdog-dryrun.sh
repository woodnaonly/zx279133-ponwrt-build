#!/bin/bash
# Rehearse the Compile step's watchdog against a fake build.
#
# The workflow's Compile step starts a watchdog that watches the build's own
# log: `make` prints the target it is working on, so a log that has not grown
# in twenty-four minutes is a build that is not working, and the watchdog
# stops it.  If that is wrong it either kills a healthy build or never fires,
# so it is rehearsed here - once against a build that keeps printing, and
# once against one that goes quiet.
#
# The contract, in one line: a build that is merely slow is left alone, and a
# build that has stopped is stopped.
#
#	bash tools/watchdog-dryrun.sh
#
# The timings are compressed so the rehearsal takes a minute: the workflow
# beats every 120 seconds and gives up after 12 of them, here it beats every
# second and gives up after BEAT_FREEZE of them.  The logic is the same code.
set -u
BEAT=${BEAT:-1}
BEAT_FREEZE=${BEAT_FREEZE:-5}
ROOT=${ROOT:-/tmp/wdtest}
rm -rf "$ROOT"
mkdir -p "$ROOT/build_dir/pkg-a"
cd "$ROOT" || exit 1

# A stand-in for make: prints a line every second, or goes quiet on demand.
cat > fake.sh <<'EOF'
#!/bin/bash
i=0
while :; do
	i=$((i + 1))
	if [ -f quiet ]; then
		sleep 1
	else
		echo "CC [build_dir/pkg-a] building thing $i"
		sleep 1
	fi
done
EOF
chmod +x fake.sh

run_case() {
	name=$1
	quiet=$2
	rm -f compile-parallel.log compile-make.pid quiet
	echo "=== case: $name ==="
	# The compile, started the way the workflow starts it: a shell that
	# writes its own pid and then BECOMES make, so the pid in the file is
	# make's pid and killing it kills the build.
	timeout --kill-after=5s 90m bash -c 'echo $$ > ../compile-make.pid; exec ./fake.sh' 2>&1 | tee ../compile-parallel.log &
	CPID=$!
	if [ "$quiet" = quiet ]; then
		sleep 2
		touch quiet
	fi

	WD=
	trap 'if [ -n "$WD" ]; then kill "$WD" 2>/dev/null || true; fi' EXIT
	(
		n=0
		last=-1
		frozen=0
		while :; do
			n=$((n + 1))
			if [ -f ../compile-parallel.log ]; then
				sz=$(wc -c < ../compile-parallel.log | tr -d ' ')
			else
				sz=0
			fi
			{
				echo "=== beat $n log=$sz frozen=$frozen ==="
				ps -eo etimes,stat,args --sort=-etimes 2>/dev/null |
					grep -Ev 'ps -eo|compile-watchdog' | head -6 || true
			} >> ../compile-watchdog.log 2>&1 || true
			if [ "$sz" = "$last" ]; then
				frozen=$((frozen + 1)) || true
			else
				frozen=0
			fi
			last=$sz
			if [ "$n" -ge 4 ] && [ "$frozen" -ge "$BEAT_FREEZE" ]; then
				echo "::error::the build has printed nothing - it is stuck, and it is being stopped"
				if [ -s ../compile-make.pid ]; then
					kill -TERM "$(cat ../compile-make.pid)" 2>/dev/null || true
				fi
				sleep 5
				frozen=0
			fi
			sleep "$BEAT"
		done
	) &
	WD=$!

	for i in $(seq 1 90); do
		sleep 1
		if ! kill -0 "$CPID" 2>/dev/null; then
			echo "  the build ended after ${i}s"
			break
		fi
	done
	kill "$WD" 2>/dev/null || true
	if kill -0 "$CPID" 2>/dev/null; then
		echo "  RESULT: still alive"
		kill "$CPID" 2>/dev/null || true
		pkill -f fake.sh 2>/dev/null || true
	else
		echo "  RESULT: stopped"
	fi
	echo "  the last watchdog lines:"
	tail -4 ../compile-watchdog.log | sed 's/^/    /'
	echo
}

run_case "a build that keeps printing" no
run_case "a build that goes quiet" quiet
echo "done"
