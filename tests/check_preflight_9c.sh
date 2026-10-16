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
# The tool under test lives in the repository, not in the workspace root. The workspace root
# still carries a `tools/` directory, and it is a FOSSIL: it predates e8f6036 (absolute $REPO
# defaults), c1766e9 (the mkgate mkdir) and the 2026-10-16 lineage-argument fix, so three of the
# files differ from what ships. Every suite here cd'd to the workspace root and then said
# `tools/...`, which resolved to that copy - so this suite had been asserting on code that is
# not the code. Measured 2026-10-16 with cmp: 72 files identical, 3 different
# (check-imagelineage.sh, mkgate.sh, preflight.sh), plus ponctl.c only in the repo.
# Artifacts still come from the workspace root - that is where the full staged set lives
# (staging/rollback and staging/ctl do not exist in the repository at all).
PF=ci-build-repo/tools/preflight.sh
# A stale set that does not change when the workspace does: release 21's own published
# artifacts, kept under fixtures/ for exactly this purpose.
STALE=$(dirname "$0")/fixtures/stale-set

pass=0
ok()  { pass=$((pass+1)); printf 'PASS %s\n' "$1"; }
bad() { printf 'FAIL %s\n' "$1"; exit 1; }
# `note` was called by the unconditional-call check below but never defined, so that branch
# ran `note: not found` (exit 127) and the assertion quietly did nothing - a check that cannot
# fail. A note is informational and must not change the verdict either way.
note() { printf 'note %s\n' "$1"; }

echo "=== syntax ==="
sh -n "$PF" && ok "preflight.sh parses" || bad "preflight.sh does not parse"
sh -n ci-build-repo/tools/check-imagelineage.sh && ok "check-imagelineage.sh parses" \
	|| bad "check-imagelineage.sh does not parse"

echo
echo "=== 1. the step is there and calls the gate ==="
# preflight emits its headings through echo, so the line starts with `echo "=== ...`,
# not with `=== `. Matching the heading alone would miss the file it lives in.
grep -q '^echo "=== 4d\.' "$PF" && ok "step 4d exists" || bad "step 4d is missing"
grep -q 'sh tools/check-imagelineage.sh' "$PF" && ok "and it runs tools/check-imagelineage.sh" \
	|| bad "step 4d does not run the lineage gate"
# "Unconditionally" means not only inside an `if`. The call used to be a bare
# `sh tools/check-imagelineage.sh`; it now names the rootfs, so the anchored pattern has to
# allow the argument or this check reports a regression that is not one.
grep -q '^	sh tools/check-imagelineage.sh\( \|$\)' "$PF" \
	&& ok "unconditionally (the statement-level call is there, not only inside an if)" \
	|| note "the gate call is not statement-level; check it cannot be silently skipped"

echo
echo "=== 1b. every call site names the rootfs it is validating ==="
# The defect this asserts against is real and was silent. With no argument the gate defaults
# to $WS/staging/trial, and $WS is the workspace root - while preflight stages, checks the
# partition table of, and builds the payload from ci-build-repo/staging/trial. The gate was
# therefore validating one tree while the script acted on another, and it passed, because the
# other tree is usually a slightly older build that also passes.
#
# Count calls that omit the argument. 0 is the assertion; anything else is a gate that is
# looking somewhere other than where the work happens. The argument pattern excludes space,
# `>` and `;` because a bare call with a redirect - `sh tools/check-imagelineage.sh >/dev/null`
# - would otherwise have its `>` counted as the rootfs and pass as named.
calls=$(grep -c 'sh tools/check-imagelineage\.sh' "$PF")
named=$(grep -c 'sh tools/check-imagelineage\.sh[[:space:]][[:space:]]*[^[:space:]>;/]' "$PF")
echo "  $calls call site(s), $named of them name a rootfs"
[ "$calls" -gt 0 ] || bad "no call sites found - the grep pattern itself is wrong"
[ "$calls" = "$named" ] \
	&& ok "no call site falls back to the other tree" \
	|| bad "$((calls - named)) call site(s) still let the gate pick its own tree"

# Reverse control: the counter above must be able to fail. Take a copy with one argument
# deleted and confirm the same arithmetic rejects it - otherwise "0 unnamed" would be a
# pattern that cannot match anything rather than a property of the file.
T=$(mktemp -d)
sed 's|sh tools/check-imagelineage\.sh staging/trial/flash-rootfs\.jffs2|sh tools/check-imagelineage.sh|g' \
	"$PF" >"$T/preflight.sh"
c2=$(grep -c 'sh tools/check-imagelineage\.sh' "$T/preflight.sh")
n2=$(grep -c 'sh tools/check-imagelineage\.sh[[:space:]][[:space:]]*[^[:space:]>;/]' "$T/preflight.sh")
rm -rf "$T"
echo "  reverse control: $c2 call site(s), $n2 named"
[ "$c2" != "$n2" ] \
	&& ok "and the counter does reject a call site with the argument removed" \
	|| bad "the counter accepted a broken file - it cannot fail"

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
if sh ci-build-repo/tools/check-imagelineage.sh "$STALE/flash-rootfs.jffs2" >/tmp/cl.out 2>&1; then
	bad "the gate accepts a stale rootfs"
else
	ok "it refuses, as it must"
fi
grep -q 'Do NOT flash' /tmp/cl.out && ok "and says not to flash it" || bad "refused without an instruction"
grep -q '192.168.1.1' /tmp/cl.out && ok "and names the wrong address it would come up on" \
	|| bad "refused without naming the consequence"

echo
echo "=== 5. informational: a live preflight run, if the network cooperates ==="
SKIP_BOARD=1 sh ci-build-repo/tools/preflight.sh >/tmp/pf.txt 2>&1
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
