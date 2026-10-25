#!/bin/bash
# The watchdog that watches a build, and stops it when it has stopped.
#
#   compile-watchdog.sh <tree> <log> <pid file> [watchdog log]
#
# The workflow's Compile step starts this next to the build and kills it when
# the build ends.  The rehearsal (tools/watchdog-dryrun.sh) starts this next
# to a fake build.  They are the same file, on purpose: a rule that is
# rehearsed in one place and shipped in another is a rule that will be
# different from the one that was rehearsed, and the difference is only ever
# found out in a build that takes an hour.
#
# It watches the build two ways, because a build can stop in two ways:
#
#   1. the build's own log.  make prints the target it is working on, so a
#      log that has not grown is a build that is not working.
#   2. the build's files.  A build that is working writes files.
#
# The two have to AGREE before the build is stopped, because each of them on
# its own has a case where the build is perfectly well: a build compiling
# one big file is quiet, and a build that is downloading talks and writes
# nothing under build_dir (it writes under dl/).  One further rule is not
# the conjunction of the other two:
#
#   - nothing written for a long time, whatever it prints: this is the stall
#     that talks - a build stuck in a retry loop prints a line every few
#     seconds and writes nothing at all, and a watchdog that only watched
#     the log would watch it talk for ever.
#
# A build that is endless rather than stuck - one that keeps printing and
# keeps writing - is left to the ceiling, which is the right judge of it.
#
# It also publishes what it sees to a branch, so that a machine with no token
# can read a build that is still running: the job log is 403 without one, the
# artifact needs a session, and the annotations of a job are published only
# when the job ENDS - which is the one thing a build that is stuck cannot do.
#
# The timings are compressed by the rehearsal through the environment:
# BEAT_SECONDS, QUIET_BEATS, STALE_SECONDS, HARD_STALE_SECONDS.
set -u

TREE=${1:?the tree the build runs in}
LOG=${2:?the log of the build}
PIDF=${3:?the file the build wrote its pid into}
WDLOG=${4:-../compile-watchdog.log}

BEAT_SECONDS=${BEAT_SECONDS:-120}
# How many beats of silence mean the build has stopped talking.
QUIET_BEATS=${QUIET_BEATS:-6}
# How long "nothing has been written" means, in seconds.
STALE_SECONDS=${STALE_SECONDS:-1200}
# How long "nothing has been written" means when the build is still talking.
HARD_STALE_SECONDS=${HARD_STALE_SECONDS:-2400}

cd "$TREE" || exit 1

# Where the build keeps the files it writes.  dl/ and tmp/ are in the list
# because a build that is downloading writes there and nowhere else, and a
# watchdog that called that a stall would stop every build that fetched
# something.
DIRS="build_dir bin staging_dir dl tmp"

stop_the_build() {
	echo "::error::$1 - it is stuck, and it is being stopped"
	if [ -s "$PIDF" ]; then
		kill -TERM "$(cat "$PIDF")" 2>/dev/null || true
	fi
	sleep 5
}

publish() {
	[ -n "${PROGRESS_BRANCH:-}" ] && [ -n "${PROGRESS_FILE:-}" ] || return 0
	[ -n "${PROGRESS_WORKSPACE:-}" ] || return 0
	[ -f "$LOG" ] || return 0
	{
		echo "=== $(date -u +%H:%M:%S) beat $n log=$sz frozen=$frozen wrote=$wrote ==="
		echo "--- the last 30 lines of the build's own log ---"
		tail -n 30 "$LOG" 2>/dev/null || true
		echo "--- the build's own processes ---"
		ps -eo etimes,stat,args --sort=-etimes 2>/dev/null |
			grep -Ei 'make|cc1|gcc|clang|(^|/)ld |(^|/)ar |python|perl|cmake|ninja|tee|curl|wget|git|rsync|tar ' |
			grep -Ev 'ps -eo|compile-watchdog|grep -E' | head -14 || true
		echo "--- the oldest processes that are not kernel threads ---"
		ps -eo etimes,stat,args --sort=-etimes 2>/dev/null |
			awk 'NR > 1 && $3 !~ /^\[/' | head -8 || true
		echo "--- the directories the build touched lately ---"
		ls -t build_dir 2>/dev/null | head -8 || true
		echo "--- how big the root filesystem is getting ---"
		for r in build_dir/target-*/root-*; do
			[ -d "$r" ] || continue
			echo "  $r: $(find "$r" -type f 2>/dev/null | wc -l) files, $(du -sh "$r" 2>/dev/null | cut -f1)"
		done
		echo "--- memory and load ---"
		free -m | sed -n '2p' || true
		uptime || true
	} > ../progress.snapshot 2>/dev/null || true
	sh ../tools/publish-progress.sh "$PROGRESS_WORKSPACE" \
		"$PROGRESS_BRANCH" "$PROGRESS_FILE" ../progress.snapshot >/dev/null 2>&1 || true
}

n=0
last=-1
frozen=0
wrote=unknown
while :; do
	n=$((n + 1))
	if [ -f "$LOG" ]; then
		sz=$(wc -c < "$LOG" | tr -d ' ')
	else
		sz=0
	fi
	{
		echo "=== $(date -u +%H:%M:%S) beat $n log=$sz frozen=$frozen wrote=$wrote ==="
		uptime || true
		free -m | sed -n '2p' || true
		ps -eo etimes,stat,args --sort=-etimes |
			awk 'NR > 1 && $3 !~ /^\[/' | head -12 || true
		echo "--- the package directories the build touched lately ---"
		ls -t build_dir 2>/dev/null | head -6 || true
	} >> "$WDLOG" 2>&1 || true

	if [ "$sz" = "$last" ]; then
		frozen=$((frozen + 1)) || true
	else
		frozen=0
	fi
	last=$sz

	# The files.  Checked every third beat, because the scan is the one thing
	# here that costs the build some disk.  The window is a marker file whose
	# time is set back, and the test is "anything newer than that" - the
	# same question as "has anything been written in the last while", but it
	# can be rehearsed with a short window, which is the only way to find out
	# whether the rule fires when it should and stays quiet when it should.
	if [ $((n % 3)) -eq 0 ]; then
		touch -d "@$(( $(date +%s) - STALE_SECONDS ))" ../stale-marker 2>/dev/null || true
		if [ -n "$(find $DIRS -type f -newer ../stale-marker 2>/dev/null | head -1)" ]; then
			wrote=yes
		else
			wrote=no
		fi
		touch -d "@$(( $(date +%s) - HARD_STALE_SECONDS ))" ../stale-marker 2>/dev/null || true
		if [ -n "$(find $DIRS -type f -newer ../stale-marker 2>/dev/null | head -1)" ]; then
			hardwrote=yes
		else
			hardwrote=no
		fi
	else
		hardwrote=unknown
	fi

	if [ "$n" -ge 4 ] && [ "$frozen" -ge "$QUIET_BEATS" ] && [ "$wrote" = no ]; then
		stop_the_build "the build has printed nothing for $((QUIET_BEATS * BEAT_SECONDS / 60)) minutes and written nothing for $((STALE_SECONDS / 60)) minutes"
		frozen=0
	elif [ "$n" -ge 6 ] && [ "$hardwrote" = no ]; then
		stop_the_build "the build has written nothing for $((HARD_STALE_SECONDS / 60)) minutes, whatever it is printing"
	fi

	if [ $((n % 3)) -eq 0 ] || [ "$n" -le 2 ]; then
		longest=$(ps -eo etimes,args --sort=-etimes 2>/dev/null |
			grep -Ev 'ps -eo|compile-watchdog' | head -3 | cut -c1-220 | tr '\n' '|' || true)
		dirs=$(ls -t build_dir 2>/dev/null | head -4 | tr '\n' ' ' || true)
		mem=$(free -m | awk '/^Mem:/ {print $7"MB available"}' || true)
		echo "::notice::beat $n $(date -u +%H:%M:%S) log=$sz frozen=$frozen mem=$mem dirs=[$dirs] longest=[$longest]"
		publish
	fi
	sleep "$BEAT_SECONDS"
done
