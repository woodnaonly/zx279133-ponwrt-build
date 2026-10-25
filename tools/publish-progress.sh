#!/bin/sh
# Publish the build's progress to a branch, so that a machine with no token
# can read a build that is still running.
#
#   publish-progress.sh <workspace> <branch> <file> <source file>
#
# Why a branch and not something else:
#
#   The job log is 403 without a token on the machine that watches these
#   builds, and the check-run annotations are only published when a job
#   ENDS - which is the one thing a build that is stuck cannot do.  The
#   failure artifact needs a session to download (401), so it is no help
#   either.  A branch is the one thing that answers while the job runs:
#   `git fetch` speaks the git protocol, not the REST API, so it costs no
#   API quota and needs no session.
#
# The file is written into the workspace's own checkout and pushed to a
# branch named after the run, so two runs never fight over the same ref.
#
# Every step of this is allowed to fail.  A progress report that cannot be
# published is not a reason to fail a build that is otherwise fine.
set -u

WS=${1:-}
BRANCH=${2:-}
FILE=${3:-}
SRC=${4:-}

[ -n "$WS" ] && [ -n "$BRANCH" ] && [ -n "$FILE" ] && [ -n "$SRC" ] || exit 0
[ -d "$WS/.git" ] || exit 0
[ -f "$SRC" ] || exit 0

cd "$WS" || exit 0

case "$FILE" in
  *..*|/*) exit 0 ;;   # a path that could leave the workspace
esac

mkdir -p "$(dirname "$FILE")" 2>/dev/null || exit 0
cp "$SRC" "$FILE" 2>/dev/null || exit 0

git add -f -- "$FILE" >/dev/null 2>&1 || exit 0
git -c user.name="ci progress" -c user.email="ci@localhost" \
    commit -q -m "progress: $FILE" -- "$FILE" >/dev/null 2>&1 || exit 0
git push -q --force origin "HEAD:refs/heads/$BRANCH" >/dev/null 2>&1 || exit 0
exit 0
