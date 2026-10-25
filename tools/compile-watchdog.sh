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
# It watches the build three ways, because a build can stop in three ways:
#
#   1. the build's own log.  make prints the target it is working on, so a
#      log that has not grown is a build that is not working.
#   2. the build's files.  A build that is working writes files.
#   3. the build's processes.  A build that is working has something running
#      that started a moment ago.
#
# The three have to AGREE before the build is stopped, because each of them on
# its own has a case where the build is perfectly well:
#
#   - a build compiling one big file is quiet, and writes one file in a while.
#   - a build that is downloading talks and writes nothing under build_dir.
#   - a build that is installing a root filesystem is quiet for a long time.
#
# The way the files are counted is the part that matters most, and it is the
# part that was wrong once.  The first version asked whether any file under
# the build's directories was NEWER THAN A MARKER - which compares the
# modification time.  The install phase of a build copies the root
# filesystem into place with cp -a, which PRESERVES the modification time of
# every file it copies: the copy has the mtime of the original, hours old.
# So the build looked like it had written nothing at all for forty minutes,
# and the watchdog stopped a build that was halfway through assembling a
# root filesystem.  The right question is not "when was this file written"
# but "when did this file change", and that is the inode's change time -
# which the kernel sets to the moment of the copy, because a kernel does not
# let a program set it.  So the marker is compared with -cnewer: the
# modification time of the marker against the CHANGE time of the file.
#
# One further rule is not the conjunction of the others:
#
#   - nothing written for a long time, whatever it prints: this is the stall
#     that talks - a build stuck in a retry loop prints a line every few
#     seconds and writes nothing at all, and a watchdog that only watched the
#     log would watch it talk for ever.
#
# A build that is endless rather than stuck - one that keeps printing and
# keeps writing - is left to the ceiling, which is the right judge of it.
#
# When it stops the build it signals the build's PROCESS GROUP, and not only
# the make process.  `timeout` starts the build in a process group of its
# own, so the group is the build and everything it started.  Signalling only
# make leaves its children holding the build's output pipe open, and the
# step then waits for a pipe that will never close: the build is dead and the
# job is not, and the runner is wasted until the step's own ceiling.
#
# It also publishes what it sees to a branch, so that a machine with no token
# can read a build that is still running: the job log is 403 without one, the
# artifact needs a session, and the annotations of a job are published only
# when the job ENDS - which is the one thing a build that is stuck cannot do.
#
# The timings are compressed by the rehearsal through the environment:
# BEAT_SECONDS, QUIET_SECONDS, WRITE_SECONDS, PROC_SECONDS,
# HARD_WRITE_SECONDS.
set -u

TREE=${1:?the tree the build runs in}
LOG=${2:?the log of the build}
PIDF=${3:?the file the build wrote its pid into}
WDLOG=${4:-../compile-watchdog.log}

BEAT_SECONDS=${BEAT_SECONDS:-120}
# How long "the build has printed nothing" means, in seconds.
QUIET_SECONDS=${QUIET_SECONDS:-1200}
# How long "the build has written nothing" means, in seconds.
WRITE_SECONDS=${WRITE_SECONDS:-1800}
# How long "the build has started nothing new" means, in seconds.
PROC_SECONDS=${PROC_SECONDS:-900}
# How long "the build has written nothing" means when the build still talks.
HARD_WRITE_SECONDS=${HARD_WRITE_SECONDS:-5400}

cd "$TREE" || exit 1

# Where the build keeps the files it writes.  dl/ and tmp/ are in the list
# because a build that is downloading writes there and nowhere else, and a
# watchdog that called that a stall would stop every build that fetched
# something.
DIRS="build_dir bin staging_dir dl tmp"

# The marker is a file whose MODIFICATION time is set back into the past, and
# the build's files are compared against it with -cnewer: a file whose change
# time is newer than the marker's modification time was touched inside the
# window.  The marker lives outside the tree, because a marker inside one of
# the watched directories would be a file that is always newer than itself.
MARKER=../stale-marker

# The comparison that is used, decided once.  -cnewer is the one that sees a
# copy that kept its modification time; -newer is the fallback for a find
# that does not know it.
: > .wd-probe 2>/dev/null || true
if find .wd-probe -maxdepth 0 -cnewer .wd-probe >/dev/null 2>&1; then
	CMP=-cnewer
else
	CMP=-newer
fi
rm -f .wd-probe 2>/dev/null || true

# Has anything in the build's directories changed inside the window?
wrote_in() {
	touch -d "@$(( $(date +%s) - $1 ))" "$MARKER" 2>/dev/null || return 1
	[ -n "$(find $DIRS $CMP "$MARKER" 2>/dev/null | head -1)" ]
}

# How long ago did the build start something new?  The youngest process that
# belongs to the build is the answer: a build that is working always has
# something young, and a build that is stuck has only the old processes it
# started an hour ago.  The watchdog's own tools are not in the list - a
# find that the watchdog itself runs must not look like the build working.
youngest_build_process() {
	ps -eo etimes,args 2>/dev/null |
		grep -Ei 'make|cc1|gcc|clang|(^|/)ld |(^|/)ar |(^|/)cp |(^|/)mv |(^|/)tar |(^|/)strip |objcopy|fakeroot|cmake|ninja|curl|wget|rsync|python|perl|bison|flex|protoc|zopfli|gzip|pigz|xz|bzip2|apk' |
		grep -Ev 'ps -eo|compile-watchdog|grep -E|publish-progress' |
		awk '{ v = $1 + 0; if (m == "" || v < m) m = v } END { print (m == "" ? 999999 : m) }'
}

# The build's process group, if it has one of its own.  `timeout` gives the
# build a group of its own, so the group is the build; signalling it stops
# the build and everything the build started.  The group of the watchdog is
# never signalled, whatever the numbers say.
build_group() {
	pid=$(cat "$PIDF" 2>/dev/null || true)
	[ -n "${pid:-}" ] || return 1
	kill -0 "$pid" 2>/dev/null || return 1
	pgid=$(ps -o pgid= -p "$pid" 2>/dev/null | tr -d ' ')
	mine=$(ps -o pgid= -p $$ 2>/dev/null | tr -d ' ')
	[ -n "${pgid:-}" ] || return 1
	[ "${pgid:-0}" -gt 1 ] 2>/dev/null || return 1
	[ "${pgid:-0}" != "${mine:-0}" ] || return 1
	echo "$pgid"
}

stop_the_build() {
	echo "::error::$1 - it is stuck, and it is being stopped"
	pgid=$(build_group || true)
	pid=$(cat "$PIDF" 2>/dev/null || true)
	if [ -n "${pgid:-}" ]; then
		kill -TERM -"$pgid" 2>/dev/null || true
		echo "::notice::the build's process group $pgid was signalled"
	fi
	if [ -n "${pid:-}" ]; then
		kill -TERM "$pid" 2>/dev/null || true
	fi
	sleep 5
	# Whatever is left of the build is signalled too.  A child that is still
	# holding the build's output pipe open would keep the step waiting for a
	# pipe that will never close.
	if [ -n "${pgid:-}" ]; then
		kill -KILL -"$pgid" 2>/dev/null || true
	fi
}

publish() {
	[ -n "${PROGRESS_BRANCH:-}" ] && [ -n "${PROGRESS_FILE:-}" ] || return 0
	[ -n "${PROGRESS_WORKSPACE:-}" ] || return 0
	[ -f "$LOG" ] || return 0
	{
		echo "=== $(date -u +%H:%M:%S) beat $n log=$sz frozen=$frozen wrote=$wrote ==="
		echo "--- signals: quiet=${quiet_age}s writes=${WRITE_SECONDS}s newproc=${PROC_SECONDS}s hard=${HARD_WRITE_SECONDS}s cmp=$CMP proc=${proc_age}s ---"
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
sz=0
frozen=0
quiet_age=0
wrote=unknown
hardwrote=unknown
proc_age=unknown
log_changed=$(date +%s)
while :; do
	n=$((n + 1))
	now=$(date +%s)

	# Is the build still there?  A build that has ended needs no watching,
	# and a watchdog that keeps running after the build holds the step's
	# output open - which is a step that never finishes.
	if [ -f "$PIDF" ]; then
		build_pid=$(cat "$PIDF" 2>/dev/null || true)
		if [ -n "${build_pid:-}" ] && ! kill -0 "$build_pid" 2>/dev/null; then
			echo "=== $(date -u +%H:%M:%S) beat $n: the build is gone, the watchdog stops ==="
			break
		fi
	fi

	if [ -f "$LOG" ]; then
		sz=$(wc -c < "$LOG" 2>/dev/null | tr -d ' ')
	else
		sz=0
	fi
	if [ "$sz" != "$last" ]; then
		log_changed=$now
	fi
	last=$sz
	quiet_age=$(( now - log_changed ))
	frozen=$(( quiet_age / 60 ))
	{
		echo "=== $(date -u +%H:%M:%S) beat $n log=$sz frozen=$frozen wrote=$wrote ==="
		echo "  quiet=${quiet_age}s proc=${proc_age}s hard=${hardwrote}"
		uptime || true
		free -m | sed -n '2p' || true
		ps -eo etimes,stat,args --sort=-etimes |
			awk 'NR > 1 && $3 !~ /^\[/' | head -12 || true
		echo "--- the package directories the build touched lately ---"
		ls -t build_dir 2>/dev/null | head -6 || true
	} >> "$WDLOG" 2>&1 || true

	# The files and the processes are checked every third beat, because the
	# scan is the one thing here that costs the build some disk.
	if [ $((n % 3)) -eq 0 ]; then
		if wrote_in "$WRITE_SECONDS"; then
			wrote=yes
		else
			wrote=no
		fi
		if wrote_in "$HARD_WRITE_SECONDS"; then
			hardwrote=yes
		else
			hardwrote=no
		fi
		proc_age=$(youngest_build_process)

		if [ "$n" -ge 4 ] && [ "$quiet_age" -ge "$QUIET_SECONDS" ] &&
		   [ "$wrote" = no ] && [ "${proc_age:-999999}" -ge "$PROC_SECONDS" ]; then
			stop_the_build "the build has printed nothing for $((QUIET_SECONDS / 60)) minutes, written nothing for $((WRITE_SECONDS / 60)) minutes, and started nothing new for $((PROC_SECONDS / 60)) minutes"
		elif [ "$n" -ge 6 ] && [ "$hardwrote" = no ]; then
			stop_the_build "the build has written nothing for $((HARD_WRITE_SECONDS / 60)) minutes, whatever it is printing"
		fi
	fi

	if [ $((n % 3)) -eq 0 ] || [ "$n" -le 2 ]; then
		longest=$(ps -eo etimes,args --sort=-etimes 2>/dev/null |
			grep -Ev 'ps -eo|compile-watchdog' | head -3 | cut -c1-220 | tr '\n' '|' || true)
		dirs=$(ls -t build_dir 2>/dev/null | head -4 | tr '\n' ' ' || true)
		mem=$(free -m | awk '/^Mem:/ {print $7"MB available"}' || true)
		echo "::notice::beat $n $(date -u +%H:%M:%S) log=$sz quiet=${quiet_age}s wrote=$wrote proc=${proc_age}s mem=$mem dirs=[$dirs] longest=[$longest]"
		publish
	fi
	sleep "$BEAT_SECONDS"
done
