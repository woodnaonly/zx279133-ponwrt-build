#!/bin/sh
# 本地排演套件（local rehearsal suite）：依赖 staging/ 下的真实构建产物，
# 在只 clone 本仓库的环境里跑不起来，也**没有**接进 CI。
# 为什么、怎么跑、缺的那个夹具从哪来：同目录 README.md。
# The DTS content gate (workflow "Apply SK-D840N overlays") has never run in CI: it is
# newer than the last release. Run it here, verbatim, against the real compiled board dtb.
#
# It ended at line 261 with `[ "$rc" -eq 0 ] || exit 1`, before Compile - so if it fails,
# a push produces no artifacts at all. Its root-compatible assertion looked for
#
#     compatible = "skyworth,sk-d840n"
#
# while dtc renders that property as
#
#     compatible = "skyworth,sk-d840n\0zte,zx279133";
#
# so the assertion could never match the real blob.
#
# The block is lifted by locating its delimiters. The ONLY substitution is `dtc`, and it is
# a stub that copies a prepared dtc-shaped text to whatever `-o` names - so the dtc call
# line itself is executed, not bypassed. The prepared text is produced by fdt2dts.py, which
# test_gate_endtoend.sh calibrates against genuine dtc output committed in the repo; this
# suite additionally refuses to run if that text is not in dtc's backslash-zero shape, so
# it cannot silently degrade into testing the comma spelling.
set -u
cd /mnt/c/ugit/SK-D840N4 || exit 1
Y=ci-build-repo/.github/workflows/build-sk-d840n.yml
R=staging/diag-20261014
W=/tmp/r25w
pass=0
ok()  { pass=$((pass+1)); printf 'PASS %s\n' "$1"; }
bad() { printf 'FAIL %s\n' "$1"; exit 1; }

rm -rf "$W"; mkdir -p "$W/bin"

# ------------------------------------------------------------------ lift the block
start=$(grep -n 'dtc -I dtb -O dts -o /tmp/sk-d840n-gate.dts' "$Y" | head -1 | cut -d: -f1)
end=$(awk -v s="$start" 'NR>s && /^ *\[ "\$rc" -eq 0 \] \|\| exit 1$/ {print NR; exit}' "$Y")
[ -n "$start" ] && [ -n "$end" ] || bad "cannot locate the DTS content gate block"
echo "note block is lines ${start}..${end} of $Y"
sed -n "${start},${end}p" "$Y" | sed 's/^ \{10\}//' >"$W/gate.sh"
grep -q 'dtc -I dtb -O dts' "$W/gate.sh" || bad "the lift lost the dtc call"
grep -q 'sk-d840n-gate.norm.dts' "$W/gate.sh" || bad "the lift lost the normalisation"
grep -q 'skyworth,sk-d840n' "$W/gate.sh" || bad "the lift lost the root-compatible assertion"
sh -n "$W/gate.sh" || bad "the lifted block does not parse"
ok "the block was lifted intact and parses"

# --------------------------------------------------------------- prepare the inputs
python3 "$R/fdt2dts.py" staging/ctl/gate-dtb >"$W/ours.dts"
python3 "$R/fdt2dts.py" ci-build-repo/dtb/sk-d840n-stock.dtb >"$W/vendor.dts"

# dtc-shaped means the list form is the backslash-zero form. If this ever stops holding,
# this suite would be testing a spelling dtc does not emit - which is exactly how the bug
# it is guarding against survived in the first place.
grep -q 'compatible = "skyworth,sk-d840n\\0' "$W/ours.dts" \
	&& ok "the prepared text really is in dtc's backslash-zero shape" \
	|| bad "the prepared text is not dtc-shaped; this suite would be testing a fake"

# ------------------------------------------------------------------ the dtc stub
cat >"$W/bin/dtc" <<'STUB'
#!/bin/sh
# Only honours -o. Everything else (-I, -O, input path) is ignored, because the point is
# to feed the gate a text in the shape dtc produces, which fdt2dts.py already emits.
out=
while [ $# -gt 0 ]; do
	case "$1" in
	-o) out=$2; shift 2 ;;
	*)  shift ;;
	esac
done
[ -n "$out" ] || exit 2
cat "$R25_DTS" >"$out" || exit 3
exit 0
STUB
chmod 755 "$W/bin/dtc"

run_gate() { # dts file -> output, returns the gate's exit status
	R25_DTS=$1 PATH="$W/bin:$PATH" sh "$W/gate.sh" 2>&1
}

echo
echo "=== 1. our board dtb: the gate must PASS ==="
out=$(run_gate "$W/ours.dts"); rc=$?
printf '%s\n' "$out" | sed 's/^/      /'
[ "$rc" -eq 0 ] && ok "exit 0 - CI will get past this step" \
	|| bad "exit $rc; the build dies here, before Compile"
for want in 'dtb root compatible names this board' 'ethernet node is enabled' \
            'ethernet node binds the nppt driver' 'mdio bus is enabled' \
            'mdio bus binds the mainline mdio driver' 'partition order matches'; do
	printf '%s\n' "$out" | grep -q "^ok   $want" && ok "  ok: $want" \
		|| bad "  missing: $want"
done
printf '%s\n' "$out" | grep -q '::error::' && bad "the gate printed an ::error:: yet exited 0" \
	|| ok "no ::error:: in the output"

echo
echo "=== 2. NEGATIVE CONTROL: the vendor dtb must FAIL this gate ==="
out=$(run_gate "$W/vendor.dts"); rc=$?
printf '%s\n' "$out" | grep -E '^(ok|::error)' | sed 's/^/      /'
[ "$rc" -ne 0 ] && ok "exit $rc - the gate really rejects a tree that does not name this board" \
	|| bad "the gate accepted the vendor dtb; it is not a gate"
printf '%s\n' "$out" | grep -q '::error::dtb root compatible names this board' \
	&& ok "and it says which assertion failed" || bad "no ::error:: naming the assertion"

echo
echo "=== 3. NEGATIVE CONTROL: partitions in the wrong order must be caught ==="
# Reorder the partition labels in our own tree and nothing else. mtd5 is decided solely by
# this order, so an out-of-order list is exactly the silent-field-failure this gate exists
# for.
awk '
  /^[[:space:]]*label = "boot";/      { boot=$0; next }
  /^[[:space:]]*label = "scratch";/  { scratch=$0; print scratch; print boot; next }
  { print }
' "$W/ours.dts" >"$W/swapped.dts"
if cmp -s "$W/ours.dts" "$W/swapped.dts"; then
	bad "the swap produced an identical file - the fixture does nothing"
fi
out=$(run_gate "$W/swapped.dts"); rc=$?
printf '%s\n' "$out" | grep -E '^::error::' | sed 's/^/      /'
[ "$rc" -ne 0 ] && ok "exit $rc - a boot/scratch swap is rejected" \
	|| bad "swapping two partition labels was not noticed"
printf '%s\n' "$out" | grep -q 'partition order is wrong' \
	&& ok "and the message names the partition order" || bad "wrong error message"

echo
echo "=== 4. the normalisation must still be present in the shipped workflow ==="
grep -q "sed 's/\\\\\\\\0/\", \"/g'" "$Y" && ok "it is" || bad "it is gone"
grep -q 'sk-d840n-gate.norm.dts' "$Y" && ok "and the assertions read the normalised copy" \
	|| bad "the assertions still read the raw text"

echo
echo "=== 5. the gate may not be skipped and still report success ==="
# The gate opens with `if [ -f <blob> ]; then`. Without a guard in front of it, an absent
# blob skips every assertion and the step still exits 0 - which is the same shape a passing
# run produces, so "the step was green" never proved the gate ran.
#
# Assert it by executing the real text, not by grepping for a string someone wrote: lift the
# guard plus the conditional it precedes, drop the blob in, and watch it refuse.
guard=$(grep -n 'if \[ ! -f /tmp/zx279133-sk-d840n\.dts\.dtb \]; then' "$Y" | head -1 | cut -d: -f1)
[ -n "$guard" ] && ok "the shipped workflow has a missing-blob guard at line $guard" \
	|| bad "no missing-blob guard: an absent blob skips the gate and the step still passes"

if [ -n "$guard" ]; then
	gend=$(awk -v s="$guard" 'NR>s && /^ *fi$/ {print NR; exit}' "$Y")
	[ -n "$gend" ] || bad "cannot find the end of the guard"
	sed -n "${guard},${gend}p" "$Y" | sed 's/^ \{10\}//' >"$W/guard.sh"
	sh -n "$W/guard.sh" || bad "the lifted guard does not parse"

	# (a) blob absent -> must refuse
	out=$(cd "$W" && sh "$W/guard.sh" 2>&1); rc=$?
	printf '%s\n' "$out" | sed 's/^/      /'
	[ "$rc" -ne 0 ] && ok "with no blob the guard exits $rc - the gate cannot be skipped" \
		|| bad "the guard exited 0 with no blob, so the gate would be skipped silently"
	printf '%s\n' "$out" | grep -q '::error::' \
		&& ok "and it says why" || bad "the refusal carries no ::error::"

	# (b) blob present -> must fall through, not refuse.
	#     The guard uses the ABSOLUTE path, exactly as CI does. An earlier version of this
	#     test created the blob under $W, so the guard correctly reported it missing and the
	#     test failed against correct behaviour.
	BLOB=/tmp/zx279133-sk-d840n.dts.dtb
	if [ -e "$BLOB" ]; then
		bad "$BLOB already exists; this test would be asserting against unknown bytes"
	fi
	: >"$BLOB"
	out=$(sh "$W/guard.sh" 2>&1); rc=$?
	rm -f "$BLOB"
	[ "$rc" -eq 0 ] && ok "with the blob present the guard falls through" \
		|| bad "the guard refused even though $BLOB exists - it would break every run"

	# Self-calibration: remove the guard from a copy and confirm this test notices.
	sed '/if \[ ! -f \/tmp\/zx279133-sk-d840n\.dts\.dtb \]; then/,/^ *fi$/d' "$Y" >"$W/unguarded.yml"
	if grep -q '! -f /tmp/zx279133-sk-d840n\.dts\.dtb' "$W/unguarded.yml"; then
		bad "the self-calibration did not actually remove the guard"
	fi
	ok "self-calibration: the guard can be removed from a copy, so its absence is detectable"
fi

echo
echo "=== rc=0, $pass checks ==="
exit 0
