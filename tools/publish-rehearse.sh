#!/bin/sh
# Rehearse tools/publish-progress.sh against a throwaway repository.
#
# The script is the one thing a stuck build depends on to be heard, so it is
# rehearsed here rather than found out about in a run that takes an hour.
#
#   sh tools/publish-rehearse.sh
set -eu
HERE=$(cd "$(dirname "$0")" && pwd)
SCRIPT="$HERE/publish-progress.sh"
WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT

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

echo "publish-progress: rehearsal done"
