#!/bin/sh
# 本地排演套件（local rehearsal suite）：依赖 staging/ 下的真实构建产物，
# 在只 clone 本仓库的环境里跑不起来，也**没有**接进 CI。
# 为什么、怎么跑、缺的那个夹具从哪来：同目录 README.md。
# The driver gate must inspect the dtb that the release actually ships.
#
# "Package flash images" copies a dtb to flash-dtb.bin in the working directory, and that
# file is what flash-sk-d840n.sh programs into mtd3. The gate used to pick its dtb with
#
#     find "$KT/arch/arm64/boot/dts/zte" -name '*sk-d840n*.dtb' | head -1
#
# i.e. some other copy of a similarly-named file. OpenWrt also stages a copy under
# build_dir, and OpenWrt patches dtbs on the way to an image, so "a file whose name matches"
# is not the same claim as "the dtb we ship". A gate that inspects one copy while the
# release carries another passes and means nothing - the same failure shape as the two dtc
# bugs: a check that is real, runs, and is about something other than what it claims.
#
# Both blocks are lifted verbatim from the workflow by delimiter and executed here.
set -u
cd /mnt/c/ugit/SK-D840N4 || exit 1
Y=ci-build-repo/.github/workflows/build-sk-d840n.yml
pass=0
ok()  { pass=$((pass+1)); printf 'PASS %s\n' "$1"; }
bad() { printf 'FAIL %s\n' "$1"; exit 1; }
note(){ printf 'note %s\n' "$1"; }

# ------------------------------------------------------------------ lift block 1
b1s=$(grep -n '^          DTB=$' "$Y" | head -1 | cut -d: -f1)
b1e=$(awk -v s="$b1s" 'NR>s && /dtb   : \$DTB/ {print NR; exit}' "$Y")
[ -n "$b1s" ] && [ -n "$b1e" ] || bad "cannot locate the gate's DTB selection"
sed -n "${b1s},${b1e}p" "$Y" | sed 's/^ \{10\}//' >/tmp/t26_pick.sh
echo "note gate DTB selection is lines ${b1s}..${b1e} of $Y"
grep -q 'flash-dtb.bin' /tmp/t26_pick.sh && ok "it knows about the shipped file" \
	|| bad "it never mentions flash-dtb.bin"
sh -n /tmp/t26_pick.sh || bad "the lifted selection does not parse"

# ------------------------------------------------------------------ lift block 2
b2s=$(grep -n 'DTB=$(find "\$KT/arch/arm64/boot/dts/zte" -name' "$Y" | head -1 | cut -d: -f1)
b2e=$(awk -v s="$b2s" 'NR>s && /^          done$/ {print NR; exit}' "$Y")
[ -n "$b2s" ] && [ -n "$b2e" ] || bad "cannot locate the packaging DTB selection"
sed -n "${b2s},${b2e}p" "$Y" | sed 's/^ \{10\}//' >/tmp/t26_pack.sh
echo "note packaging DTB selection is lines ${b2s}..${b2e} of $Y"
grep -q '::warning::staged copy differs' /tmp/t26_pack.sh \
	&& ok "it announces a diverging staged copy" || bad "no divergence warning"
grep -q 'cmp -s' /tmp/t26_pack.sh && ok "and it actually compares them" || bad "it does not compare"
sh -n /tmp/t26_pack.sh || bad "the lifted packaging block does not parse"

# ------------------------------------------------------------------ exercise them
W=$(mktemp -d)
KT="$W/kt"
mkdir -p "$KT/arch/arm64/boot/dts/zte" "$W/openwrt/build_dir"
cp staging/ctl/gate-dtb "$KT/arch/arm64/boot/dts/zte/zx279133-sk-d840n.dtb"
cp staging/ctl/gate-dtb "$W/openwrt/build_dir/image-zx279133-sk-d840n.dtb"

echo
echo "=== 1. the shipped file wins over the kernel-tree copy ==="
# Deliberately make the kernel-tree copy DIFFERENT, so picking the wrong one is visible.
printf 'not the shipped dtb' >"$KT/arch/arm64/boot/dts/zte/zx279133-sk-d840n.dtb"
cp staging/ctl/gate-dtb "$W/flash-dtb.bin"
out=$(cd "$W" && KT="$KT" sh /tmp/t26_pick.sh 2>&1); rc=$?
printf '%s\n' "$out" | sed 's/^/      /'
[ "$rc" -eq 0 ] && ok "exit 0" || bad "exit $rc"
printf '%s\n' "$out" | grep -q '^dtb   : flash-dtb.bin' \
	&& ok "it selected the SHIPPED file" \
	|| bad "it did not select flash-dtb.bin"
printf '%s\n' "$out" | grep -q "$(sha256sum staging/ctl/gate-dtb | cut -c1-16)" \
	&& ok "and it prints that file's hash, so the log names the bytes it checked" \
	|| bad "no hash in the output"

echo
echo "=== 2. with no shipped file it falls back to the kernel tree ==="
rm -f "$W/flash-dtb.bin"
out=$(cd "$W" && KT="$KT" sh /tmp/t26_pick.sh 2>&1); rc=$?
printf '%s\n' "$out" | sed 's/^/      /'
[ "$rc" -eq 0 ] && ok "exit 0 - the initramfs path still has a dtb to check" || bad "exit $rc"
printf '%s\n' "$out" | grep -q '^dtb   : .*zx279133-sk-d840n.dtb' \
	&& ok "it fell back to the kernel build" || bad "no fallback"

echo
echo "=== 3. with neither, it must fail loudly ==="
rm -f "$KT/arch/arm64/boot/dts/zte/zx279133-sk-d840n.dtb"
out=$(cd "$W" && KT="$KT" sh /tmp/t26_pick.sh 2>&1); rc=$?
printf '%s\n' "$out" | sed 's/^/      /'
[ "$rc" -ne 0 ] && ok "exit $rc" || bad "it passed with no dtb at all"
printf '%s\n' "$out" | grep -q 'no sk-d840n dtb to check' \
	&& ok "and says which thing is missing" || bad "no message"

echo
echo "=== 4. packaging: identical staged copy is announced as identical ==="
cp staging/ctl/gate-dtb "$KT/arch/arm64/boot/dts/zte/zx279133-sk-d840n.dtb"
out=$(cd "$W" && KT="$KT" sh /tmp/t26_pack.sh 2>&1); rc=$?
printf '%s\n' "$out" | sed 's/^/      /'
[ "$rc" -eq 0 ] && ok "exit 0" || bad "exit $rc"
printf '%s\n' "$out" | grep -q 'staged copy identical' \
	&& ok "it says the copies agree" || bad "no identical-case message"
printf '%s\n' "$out" | grep -q '::warning::' \
	&& bad "it warned even though they are identical" || ok "and it did not warn"

echo
echo "=== 5. packaging: a diverging staged copy must be announced ==="
printf 'a patched dtb' >"$W/openwrt/build_dir/image-zx279133-sk-d840n.dtb"
out=$(cd "$W" && KT="$KT" sh /tmp/t26_pack.sh 2>&1); rc=$?
printf '%s\n' "$out" | sed 's/^/      /'
[ "$rc" -eq 0 ] && ok "still exit 0 - the kernel build is what ships" || bad "exit $rc"
printf '%s\n' "$out" | grep -q '::warning::staged copy differs' \
	&& ok "it warns about the divergence" || bad "no divergence warning"

echo
echo "=== 6. the dtb every other suite tests IS a real shipped artifact ==="
a=$(sha256sum staging/ctl/gate-dtb | cut -d' ' -f1)
b=$(sha256sum staging/trial/flash-dtb.bin | cut -d' ' -f1)
[ "$a" = "$b" ] && ok "staging/ctl/gate-dtb == staging/trial/flash-dtb.bin ($a)" \
	|| bad "they differ: the fixtures are not the shipped bytes"
c=$(sha256sum staging/trial/sha256sums-release >/dev/null 2>&1; grep 'flash-dtb.bin' \
	staging/trial/sha256sums-release | cut -d' ' -f1)
[ "$c" = "$a" ] && ok "and it is the one the release manifest names" \
	|| note "manifest lists $c, fixture is $a (release 21 predates the current tree)"

rm -rf "$W"
echo
echo "ALL OK - $pass checks"
