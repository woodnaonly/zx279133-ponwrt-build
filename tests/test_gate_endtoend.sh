#!/bin/sh
# 本地排演套件（local rehearsal suite）：依赖 staging/ 下的真实构建产物，
# 在只 clone 本仓库的环境里跑不起来，也**没有**接进 CI。
# 为什么、怎么跑、缺的那个夹具从哪来：同目录 README.md。
# Run the CI driver gate end to end, offline, with ONE substitution - and calibrate that
# substitution against genuine dtc output before trusting anything it says.
#
# The gate is the last step of a full build and runs under `set -euo pipefail`. Its whole
# extraction stage had never been executed on a real device tree: no machine here has dtc,
# and `run_gate_fallback_verbatim.py` only exercised the syscon *verdict* block against
# hand-made input. If the extraction stage were broken, CI would take the
#       total == 0 -> "the scan found no zte,zx279133-* compatibles - it is broken, not clean"
# branch and fail an otherwise good build. That is a whole CI cycle spent proving nothing.
#
# WHAT IS SUBSTITUTED, precisely and only:
#       dtc -I dtb -O dts -o "$WORK/dts" "$DTB"
# becomes
#       python3 staging/diag-20261014/fdt2dts.py "$DTB" > "$WORK/dts"
# Everything else - the awk program, the strings extraction, has_driver, the whole decision
# loop, the nppt assertion and `exit "$rc"` - is lifted verbatim out of the workflow by line.
#
# The substitution is calibrated first, on a dtb where genuine dtc output exists: the
# inspect-dtb workflow committed real renderings of the stock and community 4.19 trees to
# ci-build-repo/decompiled/. The renderer must produce text the gate's awk reads IDENTICALLY
# to dtc's own text for the same input. Without that, everything below proves only that the
# shim agrees with itself.
set -u
cd /mnt/c/ugit/SK-D840N4 || exit 1
Y=ci-build-repo/.github/workflows/build-sk-d840n.yml
R=staging/diag-20261014
rc=0
pass=0
ok()  { pass=$((pass+1)); printf 'PASS %s\n' "$1"; }
bad() { printf 'FAIL %s\n' "$1"; rc=1; }

# ---------------------------------------------------------------- lift the gate verbatim
awk_start=$(grep -n "^ *awk '\$" "$Y" | head -1 | cut -d: -f1)
awk_end=$(grep -n '^ *'"'"' "\$WORK/dts"' "$Y" | head -1 | cut -d: -f1)
[ -n "$awk_start" ] && [ -n "$awk_end" ] || { echo "FAIL cannot locate the awk block"; exit 1; }
sed -n "$((awk_start+1)),$((awk_end-1))p" "$Y" | sed 's/^ \{10\}//' >/tmp/r24_awk.awk
grep -q 'function flush' /tmp/r24_awk.awk || { echo "FAIL the awk lift lost the program"; exit 1; }

# The decision loop: from the gzip sniff of $IMG through `exit "$rc"`.
loop_start=$(grep -n 'case "\$(od -An -tx1 -N2 "\$IMG"' "$Y" | head -1 | cut -d: -f1)
loop_end=$(awk 'NR>'"$loop_start"' && /^ *exit "\$rc"$/ {print NR; exit}' "$Y")
[ -n "$loop_start" ] && [ -n "$loop_end" ] || { echo "FAIL cannot locate the decision loop"; exit 1; }
sed -n "${loop_start},${loop_end}p" "$Y" | sed 's/^ \{10\}//' >/tmp/r24_loop.sh
grep -q 'has_driver()' /tmp/r24_loop.sh || { echo "FAIL the loop lift lost has_driver"; exit 1; }
grep -q 'gate verdict' /tmp/r24_loop.sh || { echo "FAIL the loop lift lost the verdict line"; exit 1; }
sh -n /tmp/r24_loop.sh || { echo "FAIL the lifted loop does not parse"; exit 1; }
echo "note awk = lines $((awk_start+1))..$((awk_end-1)); loop = lines ${loop_start}..${loop_end}"
ok "both blocks lifted verbatim out of the workflow"

WORK=/tmp/r24work
rm -rf "$WORK"; mkdir -p "$WORK"

strings_from() { # image -> $WORK/strings.txt, using the workflow's own pipeline
	IMG=$1
	case "$(od -An -tx1 -N2 "$IMG" | tr -d ' \n')" in
	1f8b) gzip -dc "$IMG" >"$WORK/img.raw" ;;
	*)    cat "$IMG" >"$WORK/img.raw" ;;
	esac
	test -s "$WORK/img.raw"
	LC_ALL=C tr -c '[:print:]' '\n' <"$WORK/img.raw" | grep -aE '.{3,}' \
		| LC_ALL=C sort -u >"$WORK/strings.txt"
}

compat_of() { # dts -> $WORK/compat.txt, using the workflow's own awk
	awk -f /tmp/r24_awk.awk "$1" | LC_ALL=C sort -u >"$WORK/compat.txt"
}

# ------------------------------------------------------- 1. calibrate the substitution
echo
echo "=== 1. CALIBRATE fdt2dts.py against genuine dtc output ==="
echo "    (same input dtb; dtc's own rendering is committed in ci-build-repo/decompiled/)"
for pair in "sk-d840n-community-4.19" "sk-d840n-stock"; do
	dtb="ci-build-repo/dtb/$pair.dtb"
	ref="ci-build-repo/decompiled/$pair.dts"
	[ -f "$dtb" ] && [ -f "$ref" ] || { bad "missing $dtb or $ref"; continue; }

	python3 "$R/fdt2dts.py" "$dtb" >"$WORK/mine.dts" || { bad "renderer failed on $pair"; continue; }

	# Measure what the gate actually consumes: the awk's reading of the text.
	compat_of "$WORK/mine.dts"; cp "$WORK/compat.txt" "$WORK/compat.mine"
	compat_of "$ref";            cp "$WORK/compat.txt" "$WORK/compat.dtc"

	if cmp -s "$WORK/compat.mine" "$WORK/compat.dtc"; then
		ok "$pair: the gate reads the shim's text IDENTICALLY to dtc's ($(
			wc -l <"$WORK/compat.mine") rows)"
	else
		bad "$pair: shim and dtc disagree"
		diff -u "$WORK/compat.dtc" "$WORK/compat.mine" | head -20 | sed 's/^/      /'
	fi

	# And independently: the raw compatible lines themselves must be the same set.
	grep -o '"zte,zx279133-[a-z0-9-]*"' "$WORK/mine.dts" | tr -d '"' | LC_ALL=C sort -u >/tmp/r24_a
	grep -o '"zte,zx279133-[a-z0-9-]*"' "$ref"            | tr -d '"' | LC_ALL=C sort -u >/tmp/r24_b
	if cmp -s /tmp/r24_a /tmp/r24_b; then
		ok "$pair: and the raw compatible strings match without going through awk"
	else
		bad "$pair: raw compatible strings differ"
		diff -u /tmp/r24_b /tmp/r24_a | head -20 | sed 's/^/      /'
	fi
done

# ------------------------------------------------- 2. our board dtb through the gate
echo
echo "=== 2. our board dtb through the whole gate (the build CI will do) ==="
DTB=staging/ctl/gate-dtb
IMG=staging/ctl/gate-Image
[ -f "$DTB" ] || { bad "$DTB missing"; exit 1; }
[ -f "$IMG" ] || { bad "$IMG missing"; exit 1; }
python3 "$R/fdt2dts.py" "$DTB" >"$WORK/dts" || bad "renderer failed on our dtb"
compat_of "$WORK/dts"
total=$(wc -l <"$WORK/compat.txt")
echo "     SoC compatibles in the compiled dtb: $total"
sed 's/^/       /' "$WORK/compat.txt"
[ "$total" -eq 0 ] && bad "the scan found nothing - CI would fail here with 'it is broken, not clean'"
[ "$total" -gt 0 ] && ok "the scan finds $total rows, so CI will not take the broken-scan branch"

echo
echo "--- the fallback column, which the real dtc trees never exercised ---"
fb=$(cut -d'|' -f3 "$WORK/compat.txt" | grep -cv '^-$')
echo "     rows with a fallback compatible: $fb"
[ "$fb" -gt 0 ] && ok "the fallback path IS exercised by this input" \
	|| note "no fallback column in this dtb - the syscon branch stays unexercised"

echo
strings_from "$IMG"
echo "     kernel strings: $(wc -l <"$WORK/strings.txt")"
export WORK
# No `set -e` anywhere in this script. An earlier version turned it on around section 2 and
# left it on, so section 3's `( . loop.sh )` returning 1 - which is the CORRECT answer for
# the vendor tree - killed the script before it could assert on that answer.
loop_rc=0
# Do NOT read `$?` after a pipe: it is the exit status of the LAST stage, i.e. sed, and
# would report 0 for a gate that said 1. Redirect to a file and read it from there.
( . /tmp/r24_loop.sh ) >"$WORK/ours.out" 2>&1
loop_rc=$?
sed 's/^/     /' "$WORK/ours.out"
echo
echo "     loop exit status: $loop_rc (from the subshell, not from a pipe)"
[ "$loop_rc" -eq 0 ] && ok "the gate would PASS on our dtb + this kernel" \
	|| bad "the gate would FAIL on our dtb + this kernel"
for want in zte,zx279133-pon-route zte,zx279133-idm-cci zte,zx279133-wdt-reset \
            zte,zx279133-topcrm zte,zx279133-lsp-clk; do
	grep -q "^ok   $want " "$WORK/ours.out" \
		&& ok "  and it reaches a verdict on $want - one of the nodes the old awk dropped" \
		|| bad "  no verdict line for $want; it was not scanned at all"
done

# ------------------------------------------- 3. negative control: the vendor dtb
echo
echo "=== 3. NEGATIVE CONTROL: the vendor dtb must be rejected ==="
echo "    (the workflow claims this run is what makes it a gate rather than decoration)"
VDTB=ci-build-repo/dtb/sk-d840n-stock.dtb
python3 "$R/fdt2dts.py" "$VDTB" >"$WORK/dts.v" || bad "renderer failed on the vendor dtb"
compat_of "$WORK/dts.v"
echo "     vendor rows: $(wc -l <"$WORK/compat.txt")"
( . /tmp/r24_loop.sh ) >"$WORK/vendor.out" 2>&1
vrc=$?
sed 's/^/       /' "$WORK/vendor.out" | grep -E '::error|gate verdict' || true
if [ "$vrc" -ne 0 ]; then
	ok "the vendor dtb is rejected (exit $vrc)"
else
	bad "the gate passed on a tree whose drivers are not in this kernel"
fi
for want in zte,zx279133-pon zte,zx279133-se-ddr zte,zx279133-woe-ddr; do
	if grep -q "::error::$want " "$WORK/vendor.out"; then
		ok "  and it names $want"
	else
		bad "  it did not name $want, which the gate's own comment says it must"
	fi
done
grep -q '::error::zte,zx279133-nppt is not in the kernel' "$WORK/vendor.out" \
	&& ok "  and raises the nppt assertion on its own" \
	|| note "  nppt assertion did not fire - this kernel does carry zte,zx279133-nppt"

echo
echo "=== rc=$rc, $pass checks ==="
echo "=== NOT proven: that the real dtc binary emits the same text as fdt2dts.py."
echo "===   The awk's correctness on genuine dtc output is proven separately by"
echo "===   round24_gate_awk_on_real_dtc.sh (13/13 names, exact agreement with grep)."
exit $rc
