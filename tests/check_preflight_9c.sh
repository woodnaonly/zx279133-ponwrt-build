#!/bin/sh
# 本地排演套件（local rehearsal suite）：依赖 staging/ 下的真实构建产物，
# 在只 clone 本仓库的环境里跑不起来，也**没有**接进 CI。
# 为什么、怎么跑、缺的那个夹具从哪来：同目录 README.md。
# Step 4d of preflight must exist, must sit before the first step that needs the board,
# and must be wired to run the lineage gate.
#
# This is asserted structurally rather than by running preflight to completion, because
# step 2 fetches the release list over the network and fails intermittently with a
# truncated JSON body - a flaky rehearsal must not be what decides the regression. The
# gate's own behaviour is covered by test_imagelineage.sh; what is checked here is where
# it sits, since "a gate nobody reaches" is the failure that matters.
set -u
cd /mnt/c/ugit/SK-D840N4 || exit 1
PF=tools/preflight.sh
# A stale set that does not change when the workspace does: release 21's own published
# artifacts, kept under fixtures/ for exactly this purpose.
STALE=$(dirname "$0")/fixtures/stale-set

pass=0
ok()  { pass=$((pass+1)); printf 'PASS %s\n' "$1"; }
bad() { printf 'FAIL %s\n' "$1"; exit 1; }

echo "=== syntax ==="
sh -n "$PF" && ok "preflight.sh parses" || bad "preflight.sh does not parse"
sh -n tools/check-imagelineage.sh && ok "check-imagelineage.sh parses" || bad "check-imagelineage.sh does not parse"

echo
echo "=== 1. the step is there and calls the gate ==="
# preflight emits its headings through echo, so the line starts with `echo "=== ...`,
# not with `=== `. Matching the heading alone would miss the file it lives in.
grep -q '^echo "=== 4d\.' "$PF" && ok "step 4d exists" || bad "step 4d is missing"
grep -q 'sh tools/check-imagelineage.sh' "$PF" && ok "and it runs tools/check-imagelineage.sh" \
	|| bad "step 4d does not run the lineage gate"
grep -q '^	sh tools/check-imagelineage.sh$' "$PF" \
	&& ok "unconditionally (the bare call is there, not only inside an if)" \
	|| note "the gate call is not bare; check it cannot be silently skipped"

echo
echo "=== 2. it sits before the first step that needs the board ==="
# Line numbers, so "before" is a fact about the file rather than about run order on a day
# when the network happens to be up. The ssh line to compare against is the one that
# *runs* ssh, not the SSH= definition near the top.
l_gate=$(grep -n '^echo "=== 4d\.' "$PF" | cut -d: -f1)
# No leading-tab anchor: a single-quoted \t is a literal backslash-t, not a tab, and a
# pattern that can never match is how this check failed the first time.
l_ssh=$(grep -n 'sh tools/checkuboot.sh' "$PF" | head -1 | cut -d: -f1)
[ -n "$l_gate" ] || bad "cannot locate step 4d"
[ -n "$l_ssh" ] || bad "cannot locate any ssh-using step in preflight"
echo "  step 4d at line $l_gate; first ssh-using step at line $l_ssh"
[ "$l_gate" -lt "$l_ssh" ] \
	&& ok "the gate comes before anything that needs the board" \
	|| bad "the gate is after an ssh step, so an offline run never reaches it"

echo
echo "=== 3. it can be overridden, but only loudly ==="
grep -q 'ALLOW_UNVERIFIED' "$PF" && ok "an escape hatch exists" || bad "no escape hatch (a hard gate with no way to proceed is a bug)"
grep -q 'ALLOW_UNVERIFIED=1 - this step will only warn' "$PF" \
	&& ok "and it announces the override in the output" || bad "the override is silent"
grep -q 'skipped by operator decision' "$PF" \
	&& ok "and records the refusal as an operator decision" || bad "no operator-decision record"

echo
echo "=== 4. the gate itself refuses a stale set (independent of preflight) ==="
# Pinned to the release-21 fixture, not to `staging/trial`. This used to call the gate with
# no argument - i.e. on whatever is staged - and assert it refuses. That is a claim about the
# workspace, not about the gate: on 2026-10-15, after CI rebuilt and the set was refreshed to
# the current tree, the gate correctly ACCEPTED it and this reported
# "the gate accepts the stale staged rootfs". The gate was right; the input had changed.
if sh tools/check-imagelineage.sh "$STALE/flash-rootfs.jffs2" >/tmp/cl.out 2>&1; then
	bad "the gate accepts a stale rootfs"
else
	ok "it refuses, as it must"
fi
grep -q 'Do NOT flash' /tmp/cl.out && ok "and says not to flash it" || bad "refused without an instruction"
grep -q '192.168.1.1' /tmp/cl.out && ok "and names the wrong address it would come up on" \
	|| bad "refused without naming the consequence"

echo
echo "=== 5. informational: a live preflight run, if the network cooperates ==="
SKIP_BOARD=1 sh tools/preflight.sh >/tmp/pf.txt 2>&1
rc=$?
last=$(grep -oE '^=== [0-9a-z]+\. ' /tmp/pf.txt | tail -1)
if [ "$rc" -eq 0 ]; then
	echo "  completed - the staged set is current (not the case right now)"
elif grep -q 'Do NOT flash' /tmp/pf.txt; then
	echo "  stopped at $last with the lineage refusal  <- the intended outcome"
elif grep -qiE 'JSONDecodeError|json.decoder|Unterminated string' /tmp/pf.txt; then
	echo "  stopped at $last on a network/JSON failure in step 2 - unrelated to 4d,"
	echo "  and exactly why this file asserts structure instead of run order"
else
	echo "  stopped at $last for some other reason; the structural checks above still hold"
fi
echo "  (not a pass/fail signal - see the header)"

echo
echo "ALL OK - $pass checks"
