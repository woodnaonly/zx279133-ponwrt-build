#!/bin/sh
# 本地排演套件（local rehearsal suite）：依赖 staging/ 下的真实构建产物，
# 在只 clone 本仓库的环境里跑不起来，也**没有**接进 CI。
# 为什么、怎么跑、缺的那个夹具从哪来：同目录 README.md。
# Does tools/checkrollback.sh actually fail when it should?
#
# A verifier that only ever prints "ok" is decoration. Every check in it is driven here with
# a deliberately broken rollback directory, because the whole point of the tool is to catch
# a bad pair BEFORE a board window is spent on it.
set -u
cd /mnt/c/ugit/SK-D840N4
export LC_ALL=C
pass=0; fail=0
ok()  { printf '  PASS  %-52s %s\n' "$1" "${2:-}"; pass=$(( pass + 1 )); }
bad() { printf '  FAIL  %-52s %s\n' "$1" "${2:-}"; fail=$(( fail + 1 )); }
T=$(mktemp -d); trap 'rm -rf "$T"' EXIT

# run the tool against a given directory, report rc and whether it said VERIFIED
# $2 is absolutised here: checkrollback.sh does `cd "$(dirname "$0")/.."`, so a relative OUT
# would resolve against the repository root instead of this workspace, and the suite would
# quietly verify ci-build-repo/staging/rollback - which is not where the pair it just printed
# the size of lives.
run() { # $1 label  $2 dir
	d=$(cd "$2" 2>/dev/null && pwd) || d=$2
	OUT="$d" LOCAL="$PWD" sh ci-build-repo/tools/checkrollback.sh >"$T/out" 2>&1; rc=$?
	verdict=$(grep -c 'ROLLBACK VERIFIED' "$T/out" || true)
	printf '  rc=%-3s %s\n' "$rc" "$1"
	sed 's/^/      /' "$T/out" | grep -E '^\s+(ok|FAIL|\.\.\.\.)' | head -4 | sed 's/^/  /'
	RC=$rc; VERIFIED=${verdict:-0}
}

echo "=== 1. the real pair passes ==="
run "staging/rollback as shipped" staging/rollback
if [ "$RC" -eq 0 ] && [ "$VERIFIED" -ge 1 ]; then ok "the shipped pair is reported verified" ""
else bad "the shipped pair is reported verified" "rc=$RC verdict=$VERIFIED"; fi

echo
echo "=== 2. an empty directory is refused ==="
mkdir -p "$T/empty"
run "empty dir" "$T/empty"
if [ "$RC" -ne 0 ] && [ "$VERIFIED" -eq 0 ]; then ok "an absent pair is refused" "rc=$RC"
else bad "an absent pair is refused" "rc=$RC verdict=$VERIFIED"; fi

echo
echo "=== 3. a pair that is not this board's bytes is refused ==="
mkdir -p "$T/wrong"; cp staging/rollback/flash-uImage staging/rollback/flash-dtb.bin "$T/wrong/"
# flip one byte deep inside the kernel payload
python3 - "$T/wrong/flash-uImage" <<'PY'
import sys
p = sys.argv[1]
b = bytearray(open(p,'rb').read())
b[100000] ^= 0xff
open(p,'wb').write(bytes(b))
PY
run "one byte flipped in the kernel" "$T/wrong"
if [ "$RC" -ne 0 ] && [ "$VERIFIED" -eq 0 ]; then
	ok "a tampered pair is refused" "rc=$RC"
	grep -q 'byte-exact prefix' "$T/out" && ok "  and the reason is named" "prefix mismatch" \
		|| bad "  and the reason is named" "no prefix line"
else
	bad "a tampered pair is refused" "rc=$RC verdict=$VERIFIED"
fi

echo
echo "=== 4. a mainline kernel in the rollback slot is refused ==="
# Rolling back to 6.18 would not restore PON, so the tool must say so rather than pass it.
mkdir -p "$T/mainline"; cp staging/trial/flash-uImage staging/rollback/flash-dtb.bin "$T/mainline/"
run "mainline 6.18 in the rollback slot" "$T/mainline"
if grep -q 'rolling back would NOT restore PON' "$T/out"; then
	ok "a mainline kernel is called out by name" ""
else
	bad "a mainline kernel is called out" "the tool would let it pass silently"
fi

echo
echo "=== 5. the restore_set trap is stated ==="
run "real pair, checking section 7" staging/rollback
if grep -q 'restore_set/root_stock.bin is ZTE firmware image data' "$T/out"; then
	ok "restore_set is named as not a rollback set" ""
else
	bad "restore_set is named as not a rollback set" "the warning is missing"
fi
# The tool prints "not a substitute for $OUT". This used to grep the literal
# 'not a substitute for staging/rollback', which made the assertion a claim about how OUT
# happens to be spelled rather than about the behaviour: checkrollback.sh is now handed an
# absolute OUT, prints the same sentence with the same directory in it, and the check went red
# over a leading /mnt/c. The directory it was given is the thing that must appear.
realpair=$(cd staging/rollback && pwd)
if grep -q "not a substitute for $realpair" "$T/out"; then
	ok "and the usable alternative is named" ""
else
	bad "the usable alternative is named" "the tool did not name $realpair as the usable pair"
fi

echo
echo "=== 6. the preflight entry point actually calls it ==="
if grep -q 'tools/checkrollback.sh' ci-build-repo/tools/preflight.sh; then
	ok "preflight.sh runs checkrollback.sh" ""
else
	bad "preflight.sh runs checkrollback.sh" "an operator would never see the check"
fi
if grep -q 'SRC=staging/rollback sh tools/simwrite.sh' ci-build-repo/tools/preflight.sh; then
	ok "and rehearses the writer with the rollback pair too" ""
else
	bad "preflight rehearses the rollback pair" "only the mainline pair is rehearsed"
fi
if grep -q 'was previously described here as' ci-build-repo/tools/preflight.sh; then
	ok "and records that the old restore_set claim was wrong" ""
else
	bad "preflight records the corrected claim" ""
fi

echo
echo "=== 7. and the tool is read-only ==="
before=$(md5sum staging/rollback/flash-uImage staging/rollback/flash-dtb.bin | md5sum)
OUT="$PWD/staging/rollback" LOCAL="$PWD" sh ci-build-repo/tools/checkrollback.sh >/dev/null 2>&1
after=$(md5sum staging/rollback/flash-uImage staging/rollback/flash-dtb.bin | md5sum)
if [ "$before" = "$after" ]; then ok "running it changes nothing" ""
else bad "running it changes nothing" "the pair was modified"; fi

echo
echo "=== pass=$pass fail=$fail ==="
[ "$fail" -eq 0 ] || exit 1
exit 0