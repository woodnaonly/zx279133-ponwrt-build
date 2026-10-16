#!/bin/sh
# 本地排演套件（local rehearsal suite）：依赖 staging/ 下的真实构建产物，
# 在只 clone 本仓库的环境里跑不起来，也**没有**接进 CI。
# 为什么、怎么跑、缺的那个夹具从哪来：同目录 README.md。
# The CI gate's awk must read BOTH spellings of a multi-string `compatible`:
#
#     dtc 1.6 emits:  compatible = "zte,zx279133-topcrm\0syscon";   <- ONE quoted run
#     older/other:    compatible = "zte,zx279133-topcrm", "syscon";
#
# It used to handle only the second. Against real dtc output the single token
# "zte,zx279133-topcrm\0syscon" matches no zte,zx279133-* pattern, so every node carrying a
# fallback compatible was dropped from the scan in silence - eight of the twenty-five in
# this board's dtb, including zte,zx279133-pon-route and -idm-cci, which are `required:`
# in their bindings. The scan still returned rows, so the "the scan found nothing - it is
# broken, not clean" guard never fired and the gate reported clean.
#
# The old program is kept below as a FIXTURE and is asserted to fail, so this suite fails
# if the normalisation is ever removed - and so the bug cannot quietly become folklore.
set -u
cd /mnt/c/ugit/SK-D840N4 || exit 1
Y=ci-build-repo/.github/workflows/build-sk-d840n.yml
pass=0
ok()  { pass=$((pass+1)); printf 'PASS %s\n' "$1"; }
bad() { printf 'FAIL %s\n' "$1"; exit 1; }

start=$(grep -n "^ *awk '\$" "$Y" | head -1 | cut -d: -f1)
end=$(grep -n '^ *'"'"' "\$WORK/dts"' "$Y" | head -1 | cut -d: -f1)
[ -n "$start" ] && [ -n "$end" ] || bad "cannot locate the awk block"
sed -n "$((start+1)),$((end-1))p" "$Y" | sed 's/^ \{10\}//' >/tmp/t24_new.awk
printf '' | awk -f /tmp/t24_new.awk >/dev/null 2>&1 || bad "the current awk does not run"
ok "the awk was lifted out of the workflow and runs"

# The pre-fix program, verbatim, as a fixture.
sed 's/^ *//' >/tmp/t24_old.awk <<'FIXTURE'
function flush() {
  if (name != "") printf "%s|%s|%s|%s\n", name, node, (fb != "" ? fb : "-"), st
  name = ""; fb = ""
}
/^[[:space:]]*[A-Za-z0-9_,@-]+[[:space:]]*\{/ {
  line = $0; sub(/^[[:space:]]*/, "", line); sub(/[[:space:]]*\{.*$/, "", line)
  node = line; st = "-"
}
/^[[:space:]]*compatible[[:space:]]*=/ {
  line = $0; n = split(line, parts, "\"")
  generic = ""
  for (i = 2; i <= n; i += 2) {
    q = parts[i]
    if (q ~ /^zte,zx279133-[a-z0-9-]+$/) { flush(); name = q }
    else if (i < n) { generic = q }
  }
  fb = generic
}
/^[[:space:]]*status[[:space:]]*=/ {
  s = $0; sub(/^[^"]*"/, "", s); sub(/".*$/, "", s); st = s
}
/^[[:space:]]*\};/ { flush() }
END { flush() }
FIXTURE
printf '' | awk -f /tmp/t24_old.awk >/dev/null 2>&1 || bad "the fixture awk does not run"

# --- input 1: dtc 1.6 spelling, the one CI actually produces ---------------------
cat >/tmp/t24_dtc.dts <<'DTS'
/dts-v1/;
/ {
	soc {
		syscon@10e00078 {
			compatible = "zte,zx279133-idm-cci\0syscon";
			reg = <0x0 0x10e00078 0x0 0x100>;
		};
		nppt@19000000 {
			compatible = "zte,zx279133-nppt";
			status = "okay";
		};
	};
};
DTS

# --- input 2: the comma spelling, kept working on purpose ----------------------
cat >/tmp/t24_comma.dts <<'DTS'
/dts-v1/;
/ {
	soc {
		syscon@10e00078 {
			compatible = "zte,zx279133-idm-cci", "syscon";
			reg = <0x0 0x10e00078 0x0 0x100>;
		};
		nppt@19000000 {
			compatible = "zte,zx279133-nppt";
			status = "okay";
		};
	};
};
DTS

echo
echo "=== 1. the FIXTURE reproduces the bug: it drops the node ==="
n=$(awk -f /tmp/t24_old.awk /tmp/t24_dtc.dts | grep -c '^zte,zx279133-idm-cci' || true)
[ "$n" -eq 0 ] && ok "the pre-fix awk really does drop zte,zx279133-idm-cci" \
	|| bad "the fixture no longer reproduces the bug - it was never the bug"

echo
echo "=== 2. the CURRENT awk reads dtc's backslash-zero form ==="
out=$(awk -f /tmp/t24_new.awk /tmp/t24_dtc.dts)
printf '%s\n' "$out" | sed 's/^/      /'
printf '%s\n' "$out" | grep -q '^zte,zx279133-idm-cci|syscon@10e00078|syscon|-$' \
	&& ok "it finds the node AND its fallback" \
	|| bad "it did not find idm-cci with fb=syscon from dtc's spelling"
printf '%s\n' "$out" | grep -q '^zte,zx279133-nppt|nppt@19000000|-|okay$' \
	&& ok "and it still reads the single-string node and its status" \
	|| bad "it lost the single-string case"

echo
echo "=== 3. the comma form still works (older dtc, hand-written dts) ==="
out=$(awk -f /tmp/t24_new.awk /tmp/t24_comma.dts)
printf '%s\n' "$out" | sed 's/^/      /'
printf '%s\n' "$out" | grep -q '^zte,zx279133-idm-cci|syscon@10e00078|syscon|-$' \
	&& ok "same result from the comma spelling" || bad "the comma form regressed"

echo
echo "=== 4. the two spellings must agree exactly ==="
if awk -f /tmp/t24_new.awk /tmp/t24_dtc.dts | LC_ALL=C sort >/tmp/t24_a; then :; fi
awk -f /tmp/t24_new.awk /tmp/t24_comma.dts | LC_ALL=C sort >/tmp/t24_b
cmp -s /tmp/t24_a /tmp/t24_b && ok "dtc spelling and comma spelling produce identical rows" \
	|| bad "the two spellings disagree"

echo
echo "=== 5. the normalisation is still present in the workflow ==="
grep -q 'gsub(/\\\\0/' /tmp/t24_new.awk \
	&& ok "the awk still normalises the backslash-zero form" \
	|| bad "the normalisation is gone from the workflow"

echo
echo "=== 6. and the real dtc output really does use that spelling ==="
# The committed rendering contains ONE backslash followed by a zero. In a BRE a literal
# backslash is \\ , so that is what the pattern needs. An earlier version of this suite
# wrote \\\\0 - two literal backslashes - and reported "genuine dtc output does not use the
# backslash-zero form", which was a bug in the pattern, not a fact about dtc.
F=ci-build-repo/decompiled/sk-d840n-stock.dts
n=$(grep -c 'compatible = "zte,zx279133-[a-z0-9-]*\\0' "$F" || true)
[ "$n" -gt 0 ] && ok "$n list-form compatible lines in the committed dtc output" \
	|| bad "genuine dtc output does not use the backslash-zero form; revisit this suite"

echo
echo "=== 7. and those are exactly the nodes the fixture loses ==="
# Which zte compatibles does the real tree carry in list form, that the fixture never sees?
grep -o '"zte,zx279133-[a-z0-9-]*\\0' "$F" \
	| sed 's/^"//; s/\\0$//' | LC_ALL=C sort -u >/tmp/t24_listform.txt
awk -f /tmp/t24_old.awk "$F" | cut -d'|' -f1 | LC_ALL=C sort -u >/tmp/t24_fixture_saw.txt
LC_ALL=C sort -u /tmp/t24_fixture_saw.txt -o /tmp/t24_fixture_saw.txt
echo "      in list form in the real tree:"; sed 's/^/        /' /tmp/t24_listform.txt
echo "      the fixture saw none of them:"
while read -r c; do
	[ -n "$c" ] || continue
	if grep -qxF "$c" /tmp/t24_fixture_saw.txt; then
		bad "  the fixture did see $c - it is not reproducing the bug"
	fi
done < /tmp/t24_listform.txt
ok "confirmed: every list-form compatible is invisible to the pre-fix awk"
echo "      total rows the fixture got: $(wc -l <"$F") lines of dts -> $(awk -f /tmp/t24_old.awk "$F" | wc -l) rows"

echo
echo "ALL OK - $pass checks"
