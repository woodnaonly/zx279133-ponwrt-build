#!/bin/sh
# Rehearse the two things a stuck build depends on to be heard: the progress
# it publishes to a branch, and the watchdog that publishes it.
#
#   sh tools/publish-rehearse.sh
#
# The first part drives tools/publish-progress.sh directly.  The second part
# drives the whole path the workflow uses - the watchdog, in a tree, writing
# into a workspace that is a repository - because the first version of the
# publishing script copied its source AFTER it had changed directory, so a
# relative source was looked for one directory above the workspace, and the
# rehearsal - which passed an absolute path - never noticed.
set -eu
HERE=$(cd "$(dirname "$0")" && pwd)
SCRIPT="$HERE/publish-progress.sh"
WATCHDOG="$HERE/compile-watchdog.sh"
WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT

# ---------------------------------------------------------------- part one
cd "$WORK"
git init -q work
cd work
git config user.email a@b.c
git config user.name tester
echo readme > README.md
git add README.md
git commit -q -m first
git init -q --bare "$WORK/remote.git"
git remote add origin "$WORK/remote.git"
git push -q origin HEAD:refs/heads/main

printf 'beat 1 log=123\nbeat 2 log=456\n' > "$WORK/snapshot"

sh "$SCRIPT" "$WORK/work" "ci-progress/42-flash" \
   "progress/42-flash.txt" "$WORK/snapshot"

echo "--- the branches on the remote ---"
git --git-dir="$WORK/remote.git" for-each-ref --format='%(refname)' refs/heads

echo "--- the file on the remote ---"
git --git-dir="$WORK/remote.git" show refs/heads/ci-progress/42-flash:progress/42-flash.txt

echo "--- a second publication overwrites the file ---"
printf 'beat 3 log=789\n' > "$WORK/snapshot"
sh "$SCRIPT" "$WORK/work" "ci-progress/42-flash" \
   "progress/42-flash.txt" "$WORK/snapshot"
git --git-dir="$WORK/remote.git" show refs/heads/ci-progress/42-flash:progress/42-flash.txt

echo "--- a missing source file is not an error ---"
sh "$SCRIPT" "$WORK/work" "ci-progress/42-flash" \
   "progress/42-flash.txt" "$WORK/nothing-here" && echo "exit 0, as it should"

echo "--- a workspace that is not a repository is not an error ---"
mkdir -p "$WORK/plain"
sh "$SCRIPT" "$WORK/plain" "ci-progress/42-flash" \
   "progress/42-flash.txt" "$WORK/snapshot" && echo "exit 0, as it should"

# ---------------------------------------------------------------- part two
# The path the workflow uses: the watchdog runs in the tree, the source it
# publishes is relative to the tree, and the workspace is one directory up.
echo
echo "=== the watchdog publishing, the way the workflow does it ==="
rm -rf "$WORK/ws"
mkdir -p "$WORK/ws/tree/build_dir/pkg-a"
cd "$WORK/ws"
git init -q .
git config user.email a@b.c
git config user.name tester
echo readme > README.md
git add README.md
git commit -q -m first
git init -q --bare "$WORK/remote2.git"
git remote add origin "$WORK/remote2.git"
git push -q origin HEAD:refs/heads/main
# The workspace carries the tools, the way the workflow's workspace does -
# the watchdog calls ../tools/publish-progress.sh, and ".." from the tree is
# the workspace.
mkdir -p "$WORK/ws/tools"
cp "$HERE/publish-progress.sh" "$WORK/ws/tools/"
cd "$WORK/ws/tree"
# A build that prints and writes, the way make does.
(
	i=0
	while :; do
		i=$((i + 1))
		echo "CC [build_dir/pkg-a] building thing $i" >> ../compile-parallel.log
		touch build_dir/pkg-a/object-$i.o
		sleep 1
	done
) &
FAKE=$!
PROGRESS_WORKSPACE="$WORK/ws" \
PROGRESS_BRANCH="ci-progress/42-flash" \
PROGRESS_FILE="progress/42-flash.txt" \
BEAT_SECONDS=1 \
QUIET_BEATS=60 \
STALE_SECONDS=30 \
HARD_STALE_SECONDS=60 \
	bash "$WATCHDOG" . ../compile-parallel.log ../compile-make.pid ../compile-watchdog.log &
WD=$!
sleep 6
kill "$WD" 2>/dev/null || true
kill "$FAKE" 2>/dev/null || true
echo "--- the branch the watchdog published to ---"
git --git-dir="$WORK/remote2.git" for-each-ref --format='%(refname)' refs/heads
echo "--- what it published ---"
git --git-dir="$WORK/remote2.git" show refs/heads/ci-progress/42-flash:progress/42-flash.txt || {
	echo "publish-rehearse: THE WATCHDOG PUBLISHED NOTHING" >&2
	exit 1
}
echo "publish-progress: rehearsal done"
