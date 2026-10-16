#!/bin/sh
# 本地排演套件（local rehearsal suite）：依赖 staging/ 下的真实构建产物，
# 在只 clone 本仓库的环境里跑不起来，也**没有**接进 CI。
# 为什么、怎么跑、缺的那个夹具从哪来：同目录 README.md。
# Run the CI driver gate's awk, VERBATIM out of the workflow, on REAL dtc output.
#
# Why: the gate is the last step of the build and it runs under `set -euo pipefail`. Its
# extraction stage (workflow lines 409-436) is `dtc -I dtb -O dts` piped into an awk
# program, and if that awk finds nothing on real dtc text the gate takes the
#       total == 0 -> ::error::the scan found no zte,zx279133-* compatibles
# branch and the entire CI build fails on a build that was actually fine. No local
# machine has dtc, so this stage has never been exercised against genuine dtc output.
#
# ci-build-repo/decompiled/*.dts are genuine dtc renderings (produced by the
# inspect-dtb workflow, committed to the repo), so they are real input for this test.
#
# The awk is lifted by locating its delimiters, not by a fixed line range, and the
# extraction is checked before it is believed.
set -u
cd /mnt/c/ugit/SK-D840N4 || exit 1
Y=ci-build-repo/.github/workflows/build-sk-d840n.yml
rc=0

pass=0
ok()  { pass=$((pass+1)); printf 'PASS %s\n' "$1"; }
bad() { printf 'FAIL %s\n' "$1"; rc=1; }

start=$(grep -n "^ *awk '\$" "$Y" | head -1 | cut -d: -f1)
end=$(grep -n '^ *'"'"' "\$WORK/dts"' "$Y" | head -1 | cut -d: -f1)
[ -n "$start" ] && [ -n "$end" ] || { echo "FAIL cannot locate the awk block in $Y"; exit 1; }
echo "note awk block is lines $((start+1))..$((end-1)) of $Y"

sed -n "$((start+1)),$((end-1))p" "$Y" | sed 's/^ \{10\}//' >/tmp/r24_awk.awk

# Prove the extraction before trusting it.
grep -q 'function flush' /tmp/r24_awk.awk && ok "the awk program was lifted intact" \
	|| { echo "FAIL the lift lost the program"; sed -n '1,5p' /tmp/r24_awk.awk; exit 1; }
awk -f /tmp/r24_awk.awk /dev/null >/dev/null 2>&1
[ $? -le 1 ] || true
if printf '' | awk -f /tmp/r24_awk.awk >/dev/null 2>&1; then
	ok "the awk program parses and runs"
else
	echo "FAIL the lifted awk does not even run on empty input"; exit 1
fi

run_gate_awk() { # dts file -> compat.txt on stdout
	awk -f /tmp/r24_awk.awk "$1" | LC_ALL=C sort -u
}

for f in ci-build-repo/decompiled/sk-d840n-stock.dts \
         ci-build-repo/decompiled/sk-d840n-community-4.19.dts; do
	[ -f "$f" ] || { echo "FAIL $f missing"; rc=1; continue; }
	echo
	echo "=== $f"
	run_gate_awk "$f" >/tmp/r24_compat.txt
	total=$(wc -l </tmp/r24_compat.txt)
	echo "  total=$total"
	sed 's/^/    /' /tmp/r24_compat.txt
	if [ "$total" -eq 0 ]; then
		bad "the awk found NOTHING in a real dtc rendering - CI would fail the build here"
	fi
done

echo
echo "=== sanity: the awk against a plain grep of the same files ==="
# NOTE: this comparison used to assert EXACT agreement, and it passed - which was itself the
# bug. A plain `grep -o '"zte,zx279133-[a-z0-9-]*"'` cannot see a line dtc rendered as
#     compatible = "zte,zx279133-topcrm\0syscon";
# because the closing quote is not right after the name. So the naive grep and the broken
# awk missed exactly the same nodes, and "they agree" looked like corroboration.
#
# The correct relation is: the awk must be a SUPERSET of the naive grep, and the names it
# has beyond it must be exactly the list-form ones. Anything else means something was lost.
for f in ci-build-repo/decompiled/sk-d840n-stock.dts \
         ci-build-repo/decompiled/sk-d840n-community-4.19.dts; do
	grep -o '"zte,zx279133-[a-z0-9-]*"' "$f" | tr -d '"' | LC_ALL=C sort -u >/tmp/r24_grep.txt
	grep -o '"zte,zx279133-[a-z0-9-]*\\0' "$f" \
		| sed 's/^"//; s/\\0$//' | LC_ALL=C sort -u >/tmp/r24_list.txt
	run_gate_awk "$f" | cut -d'|' -f1 | LC_ALL=C sort -u >/tmp/r24_awk_names.txt
	cat /tmp/r24_grep.txt /tmp/r24_list.txt | LC_ALL=C sort -u >/tmp/r24_want.txt
	printf '  %s\n' "${f##*/}"
	printf '    naive grep %s | list-form %s | awk found %s\n' \
		"$(wc -l </tmp/r24_grep.txt)" "$(wc -l </tmp/r24_list.txt)" \
		"$(wc -l </tmp/r24_awk_names.txt)"
	if cmp -s /tmp/r24_want.txt /tmp/r24_awk_names.txt; then
		ok "awk found exactly naive-grep + list-form, no more and no fewer"
	else
		bad "awk disagrees with the union of the two:"
		LC_ALL=C comm -3 /tmp/r24_want.txt /tmp/r24_awk_names.txt | sed 's/^/      /'
	fi
done

echo
echo "=== rc=$rc, $pass checks ==="
exit $rc
