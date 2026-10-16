#!/bin/sh
# 本地排演套件（local rehearsal suite）：依赖 staging/ 下的真实构建产物，
# 在只 clone 本仓库的环境里跑不起来，也**没有**接进 CI。
# 为什么、怎么跑、缺的那个夹具从哪来：同目录 README.md。
# Run preflight's step 4d block VERBATIM, against a stub gate, in both directions.
#
# The block is lifted out of tools/preflight.sh by line range and executed unmodified, the
# same way run_gate_fallback_verbatim.py lifts the workflow's gate out of the YAML. The
# point is that preflight is where an operator reads the verdict, so the wording there is
# as load-bearing as the gate's - and an earlier version of this line read
#
#     sh tools/check-imagelineage.sh || echo "  (skipped by operator decision)"
#
# which filed a positive refusal as an operator skip. Asserting on the gate alone would
# never have caught that, because the gate was behaving correctly; the misreport was in
# the wrapper.
set -u
cd /mnt/c/ugit/SK-D840N4 || exit 1

# The preflight under test is the one in the repository. The workspace root also has a
# `tools/` directory and it is a fossil copy - predating the 2026-10-16 lineage-argument fix -
# so pointing at it verified a block that is not the one that ships.
PF=ci-build-repo/tools/preflight.sh
pass=0
ok()  { pass=$((pass+1)); printf 'PASS %s\n' "$1"; }
bad() { printf 'FAIL %s\n' "$1"; exit 1; }

start=$(grep -n '^ALLOW_UNVERIFIED=' "$PF" | head -1 | cut -d: -f1)
[ -n "$start" ] || bad "cannot find the 4d block in $PF"
# Take the block from its guard up to the next step header rather than a fixed line count:
# a hardcoded length silently truncates the block and the suite then "passes" on a
# fragment that would not run.
next=$(grep -n '^echo "=== 5\.' "$PF" | head -1 | cut -d: -f1)
[ -n "$next" ] || bad "cannot find step 5 in $PF"
end=$((next - 1))
[ "$end" -gt "$start" ] || bad "step 4d block is empty or inverted ($start..$end)"
echo "note block is lines $start..$end of $PF"
sed -n "${start},${end}p" "$PF" >/tmp/r23_block.sh
# And prove the extraction was complete rather than merely long enough.
grep -q '^fi$' /tmp/r23_block.sh && ok "the block is balanced" || bad "the block is truncated"

# The static greps below read executable lines only. The block's own comment quotes the
# old wording on purpose - it is there to record what was fixed - and grepping the raw
# text matches that comment. That trap has already been sprung once in this project:
# searching for a phrase you are withdrawing hits the line where you withdraw it.
grep -v '^[[:space:]]*#' /tmp/r23_block.sh >/tmp/r23_code.sh

# The extraction itself has to be right, or the rest of this suite proves nothing.
grep -q 'ALLOW_UNVERIFIED' /tmp/r23_code.sh || bad "the lifted block lost its guard"
grep -q 'check-imagelineage.sh' /tmp/r23_code.sh || bad "the lifted block never calls the gate"
grep -q 'if ! sh tools/check-imagelineage.sh' /tmp/r23_code.sh \
	&& ok "the block distinguishes a waiver from a refusal" \
	|| bad "the block is back to treating any nonzero exit as a skip"
grep -q 'skipped by operator decision' /tmp/r23_code.sh \
	&& bad "the misleading skip wording is back in executable code" \
	|| ok "the misleading skip wording is gone from executable code"
grep -q 'warning the old phrasing' /tmp/r23_code.sh \
	&& bad "the gate failure is being masked by a message that makes it look optional" \
	|| ok "the gate failure is not masked"
sh -n /tmp/r23_block.sh || bad "the lifted block does not parse"
ok "the lifted block parses"

W=$(mktemp -d)
mkdir -p "$W/tools"

run_block() { # stub exit code -> output on stdout
	printf '#!/bin/sh\necho "STUB GATE: verdict"\nexit %s\n' "$1" >"$W/tools/check-imagelineage.sh"
	chmod +x "$W/tools/check-imagelineage.sh"
	( cd "$W" && ALLOW_UNVERIFIED=1 sh /tmp/r23_block.sh 2>&1 )
}

echo
echo "=== 1. waiver set, gate exits 0 (the waiver did its job) ==="
out=$(run_block 0)
echo "$out" | sed 's/^/      /'
echo "$out" | grep -q 'ALLOW_UNVERIFIED=1' && ok "the waiver is announced before anything else" \
	|| bad "the waiver is not announced"
echo "$out" | grep -q 'waiver did NOT cover this' \
	&& bad "it announced a refusal that did not happen" \
	|| ok "and it does not claim a refusal that did not happen"

echo
echo "=== 2. waiver set, gate exits 1 (the real refusal) ==="
out=$(run_block 1)
echo "$out" | sed 's/^/      /'
echo "$out" | grep -q 'waiver did NOT cover this' \
	&& ok "it says the waiver did not cover it" \
	|| bad "a refusal was passed off as a skip"
echo "$out" | grep -q 'finding, not a skip' \
	&& ok "and says a finding is not a skip" || bad "no 'not a skip' wording"
echo "$out" | grep -q 'skipped by operator decision' \
	&& bad "the old misreport is back" || ok "and it never calls the refusal a skip"
echo "$out" | grep -q 'Do not flash' \
	&& ok "and tells the operator not to flash" || bad "no instruction to stop"

echo
echo "=== 3. the stub was really what ran ==="
out=$(run_block 0)
echo "$out" | grep -q 'STUB GATE' && ok "the block really invokes tools/check-imagelineage.sh" \
	|| bad "the gate was not invoked - the block was not exercised"

rm -rf "$W"
echo
echo "ALL OK - $pass checks"
