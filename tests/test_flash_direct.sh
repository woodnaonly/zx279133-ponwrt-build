#!/bin/sh
# 本地排演套件（local rehearsal suite）：依赖 staging/ 下的真实构建产物，
# 在只 clone 本仓库的环境里跑不起来，也**没有**接进 CI。
# 为什么、怎么跑、缺的那个夹具从哪来：同目录 README.md。
# Execute tools/flash-direct.sh - the tool that performs the rollback this session just
# declared feasible - against a fake device. It had never been run.
#
# The risk it carries is specific: it is the path an operator takes when the board has
# already gone quiet, so what it prints after a failed write matters more than usual.
# Line 126 is `ok=$(printf '%s\n' "$prev" | grep -c "READ_OK")` under `set -eu`. When the
# worker logs no READ_OK - which is exactly what ABORT and TIMEOUT look like - grep exits
# 1, the assignment fails, and set -e ends the script BEFORE the case statement that
# prints the verdict. That would leave an operator staring at a bare non-zero exit while
# the board is in an unknown state.
set -u
cd /mnt/c/ugit/SK-D840N4
export LC_ALL=C
pass=0; fail=0
ok()  { printf '  PASS  %-52s %s\n' "$1" "${2:-}"; pass=$((pass+1)); }
bad() { printf '  FAIL  %-52s %s\n' "$1" "${2:-}"; fail=$((fail+1)); }

# ---------------------------------------------------------------- the bug, minimally
echo "=== 0. the set -e + grep -c interaction, in isolation ==="
# Both spellings side by side. The bare one is what flash-direct.sh used to have, and it
# is kept here so that reintroducing it fails this test rather than passing quietly.
( set -eu
  prev="ABORT: refuse to write, flash untouched"
  ok=$(printf '%s\n' "$prev" | grep -c "READ_OK")
  echo "reached the verdict block"
) >/tmp/bare.out 2>&1
bare_rc=$?
( set -eu
  prev="ABORT: refuse to write, flash untouched"
  ok=$(printf '%s\n' "$prev" | grep -c "READ_OK" || true)
  echo "reached the verdict block"
) >/tmp/fixed.out 2>&1
fixed_rc=$?
printf '  bare form  rc=%s output: %s\n' "$bare_rc" "$(tr '\n' ' ' </tmp/bare.out)"
printf '  || true   rc=%s output: %s\n' "$fixed_rc" "$(tr '\n' ' ' </tmp/fixed.out)"
if [ "$bare_rc" -ne 0 ] && ! grep -q 'reached' /tmp/bare.out; then
	ok "the bare form really does swallow the verdict" "regression witness"
else
	note "the bare form no longer reproduces" "grep behaviour differs here - the end-to-end cases are what count"
fi
if [ "$fixed_rc" -eq 0 ] && grep -q 'reached the verdict block' /tmp/fixed.out; then
	ok "and the || true form reaches it" "this is what flash-direct.sh now uses"
else
	bad "the || true form reaches it" "rc=$fixed_rc"
	REPRO="broken"
fi
# and prove the shipped script really carries the fix, not just this test's copy
if grep -q 'grep -c "READ_OK" || true' ci-build-repo/tools/flash-direct.sh; then
	ok "tools/flash-direct.sh carries the fixed form" ""
else
	bad "tools/flash-direct.sh carries the fixed form" "it still has the bare grep -c"
fi
rm -f /tmp/bare.out /tmp/fixed.out

# ---------------------------------------------------------------- fake device
W=""
setup() { # $1 = what the worker log should say
	W=$(mktemp -d)
	# flash-direct.sh line 33 does `cd "$(dirname "$0")/.."`, so the relocated copy has to
	# live at <sandbox>/repo/tools/ or it walks out of the repo and every relative path
	# (staging/rollback, tools/dtbparts.py, tools/flash-write.sh) resolves to nothing.
	mkdir -p "$W/repo/tools" "$W/repo/staging/rollback" "$W/bin" "$W/dev/tmp/flash"
	cp staging/rollback/flash-uImage staging/rollback/flash-dtb.bin "$W/repo/staging/rollback/"
	cp ci-build-repo/tools/flash-write.sh ci-build-repo/tools/dtbparts.py "$W/repo/tools/"
	# the worker log is a template the fake device serves on every poll; arming must not
	# consume it, or every verdict case degenerates into the empty-log case
	printf '%s' "$1" >"$W/log.template"

	cat >"$W/bin/ssh" <<EOF
#!/bin/sh
# fake ssh: the command is the last argument
cmd=""
for a in "\$@"; do cmd="\$a"; done
DEV=$W/dev
case "\$cmd" in
	"mkdir -p /tmp/flash"*) mkdir -p "\$DEV/tmp/flash"; exit 0 ;;
	"cat > /tmp/flash/flash-uImage"*)  cat >"\$DEV/tmp/flash/flash-uImage";  exit 0 ;;
	"cat > /tmp/flash/flash-dtb.bin"*) cat >"\$DEV/tmp/flash/flash-dtb.bin"; exit 0 ;;
	"cat > /tmp/flash/flash-write.sh"*) cat >"\$DEV/tmp/flash/flash-write.sh"; exit 0 ;;
	"md5sum /tmp/flash/flash-uImage /tmp/flash/flash-dtb.bin"*)
		if [ "\$CORRUPT_COPY" = 1 ]; then
			printf 'truncated' >"\$DEV/tmp/flash/flash-uImage"
		fi
		md5sum "\$DEV/tmp/flash/flash-uImage" "\$DEV/tmp/flash/flash-dtb.bin" | awk '{print \$1}' | tr '\n' ' '
		exit 0 ;;
	"md5sum /tmp/flash/flash-write.sh"*)
		md5sum "\$DEV/tmp/flash/flash-write.sh" 2>/dev/null | awk '{print \$1}'; exit 0 ;;
	"cd /tmp/flash"*) printf 'armed\n'; exit 0 ;;
	"cat /tmp/flash/write.log"*) cat "$W/log.template" 2>/dev/null; exit 0 ;;
	*)
		# the /proc/mtd survey and the tool probe
		cat <<'MTD'
mtd2 kernel           off=2097152 size=8388608 erasesize=131072
mtd3 dtb              off=10485760 size=1048576 erasesize=131072
mtd5 root             off=15728640 size=252706816 erasesize=131072
tool mtd        /sbin/mtd
tool mtd_debug  -
tool nandwrite  -
tool flash_erase -
kernel size 8388608 vs uImage 4814777
MTD
		exit 0 ;;
esac
EOF
	chmod +x "$W/bin/ssh"

	# The poll loop is `while [ $n -lt 120 ] ... sleep 5`, so the timeout case would take
	# ten real minutes. A no-op sleep keeps the loop's shape and count intact.
	cat >"$W/bin/sleep" <<'EOF'
#!/bin/sh
exit 0
EOF
	chmod +x "$W/bin/sleep"

	# the two local gates are separate tools with their own tests; stub them so this
	# harness measures flash-direct.sh and not them
	sed -e 's#^\t*\[\[.*gatecheck.*#\t[ "${GATE_OK:-1}" = 1 ] || { echo "gate refused" >\&2; exit 1; }#' \
	    -e 's#^\tsh tools/gatecheck.sh.*#\t[ "${GATE_OK:-1}" = 1 ] || { echo "gate refused" >\&2; exit 1; }#' \
	    -e 's#^\t\[ "\$BACKUP" = 0 \].*#\t[ "${BACKUP_OK:-1}" = 1 ] || { echo "no snapshot" >\&2; exit 1; }#' \
	    -e '/^\t\t{ echo "refusing: no verified snapshot/d' \
	    tools/flash-direct.sh >"$W/repo/tools/fd.sh"
}

run() { # env... then dry|write
	OUT=$(PATH="$W/bin:$PATH" \
		GATE_OK="${GATE_OK:-1}" BACKUP_OK="${BACKUP_OK:-1}" CORRUPT_COPY="${CORRUPT_COPY:-0}" \
		SRC=staging/rollback EXPECT='root=0xf00000,0xf100000' \
		KEY=/dev/null DEV=root@192.168.9.1 \
		env "$@" sh "$W/repo/tools/fd.sh" "${MODE:-dry}" 2>&1)
	RC=$?
}

echo
echo "=== 1. dry run: surveys, writes nothing, exits 0 ==="
setup "irrelevant for dry"
MODE=dry run
echo "  rc=$RC"
printf '%s\n' "$OUT" | tail -6 | sed 's/^/    /'
if [ "$RC" -eq 0 ] && printf '%s' "$OUT" | grep -q 'DRY RUN'; then
	ok "dry run completes and says nothing was written" "rc=0"
else bad "dry run completes" "rc=$RC"; fi
if printf '%s' "$OUT" | grep -q 'mtd2 kernel'; then
	ok "and it reports the real partition geometry" ""
else bad "partition geometry reported" "missing"; fi
rm -rf "$W"

echo
echo "=== 2. write: happy path, two READ_OK lines and DONE ==="
setup 'mtd3 dtb write ok
READ_OK dtb 48463 bytes
mtd2 kernel write ok
READ_OK kernel 4814777 bytes
DONE'
MODE=write run
echo "  rc=$RC"
printf '%s\n' "$OUT" | tail -8 | sed 's/^/    /'
if [ "$RC" -eq 0 ] && printf '%s' "$OUT" | grep -q 'PASS: dtb and kernel both read back'; then
	ok "a confirmed write says PASS" ""
else bad "a confirmed write says PASS" "rc=$RC"; fi
rm -rf "$W"

echo
echo "=== 3. write: only ONE READ_OK - the verdict must say do not reboot ==="
setup 'mtd3 dtb write ok
READ_OK dtb 48463 bytes
DONE'
MODE=write run
echo "  rc=$RC"
printf '%s\n' "$OUT" | tail -6 | sed 's/^/    /'
if printf '%s' "$OUT" | grep -q 'NOT CONFIRMED'; then
	ok "a partial readback says NOT CONFIRMED" ""
else bad "a partial readback says NOT CONFIRMED" "rc=$RC - no verdict at all"; fi
rm -rf "$W"

echo
echo "=== 4. write: ABORT before the first erase ==="
setup 'ABORT: refusing, kernel partition size reads as 0'
MODE=write run
echo "  rc=$RC"
printf '%s\n' "$OUT" | tail -6 | sed 's/^/    /'
if printf '%s' "$OUT" | grep -q 'ABORTED before the first erase'; then
	ok "an abort says flash is untouched" ""
else bad "an abort says flash is untouched" "rc=$RC - the operator is told nothing"; fi
rm -rf "$W"

echo
echo "=== 5. write: worker never reports (timeout) ==="
setup 'started'
# make the log never contain DONE or ABORT so the poll loop runs to its limit
sed -i 's/^\(.*\)arm.*$/\1printf %s armed/' /dev/null 2>/dev/null || true
MODE=write run
echo "  rc=$RC"
printf '%s\n' "$OUT" | tail -6 | sed 's/^/    /'
if printf '%s' "$OUT" | grep -q 'TIMEOUT'; then
	ok "a timeout is announced with the command to read the log" ""
else bad "a timeout is announced" "rc=$RC"; fi
rm -rf "$W"

echo
echo "=== 6. a copy that ended early must be caught before any write ==="
setup 'irrelevant'
CORRUPT_COPY=1 MODE=write run
echo "  rc=$RC"
printf '%s\n' "$OUT" | tail -4 | sed 's/^/    /'
if [ "$RC" -ne 0 ] && printf '%s' "$OUT" | grep -qi 'md5 mismatch'; then
	ok "a truncated copy is refused before writing" "rc=$RC"
else bad "a truncated copy is refused before writing" "rc=$RC"; fi
rm -rf "$W"

echo
echo "=== 7. the gate refuses -> nothing is staged at all ==="
setup 'irrelevant'
GATE_OK=0 MODE=write run
echo "  rc=$RC"
if [ "$RC" -ne 0 ] && ! printf '%s' "$OUT" | grep -q 'md5 device'; then
	ok "a refused gate stops before the device is touched" "rc=$RC"
else bad "a refused gate stops early" "rc=$RC"; fi
rm -rf "$W"

echo
echo "=== 8. a failed pre-flash backup must also block the write ==="
setup 'irrelevant'
BACKUP_OK=0 MODE=write run
echo "  rc=$RC"
if [ "$RC" -ne 0 ]; then ok "no snapshot -> no write" "rc=$RC"
else bad "no snapshot -> no write" "rc=0"; fi
rm -rf "$W"

echo
echo "=== 9. the rollback pair passes the dtb gate on its own ==="
if python ci-build-repo/tools/dtbparts.py staging/rollback/flash-dtb.bin 'root=0xf00000,0xf100000' >/dev/null 2>&1; then
	ok "staging/rollback/flash-dtb.bin satisfies the vendor EXPECT" "TABLE OK"
else
	bad "vendor EXPECT satisfied" "gate rejects the rollback pair"
fi
if python ci-build-repo/tools/dtbparts.py staging/trial/flash-dtb.bin 'root=0xf00000,0xf100000' >/dev/null 2>&1; then
	bad "mainline dtb must NOT satisfy the vendor EXPECT" "it does - the gate is not discriminating"
else
	ok "mainline dtb is correctly rejected by the vendor EXPECT" "the gate discriminates"
fi

echo
echo "=== pass=$pass fail=$fail ==="
exit 0