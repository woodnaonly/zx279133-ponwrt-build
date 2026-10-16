#!/bin/sh
# 本地排演套件（local rehearsal suite）：依赖 staging/ 下的真实构建产物，
# 在只 clone 本仓库的环境里跑不起来，也**没有**接进 CI。
# 为什么、怎么跑、缺的那个夹具从哪来：同目录 README.md。
# The kernel this project flashes is NOT upstream Linux, and it carries out-of-tree ZX279133
# drivers - including PON ones. That fact has to stay pinned down, because a wrong belief about
# it is what the pending PON decision was built on.
#
# What is asserted here:
#   1. the target refuses to build without CONFIG_EXTERNAL_KERNEL_TREE - i.e. it cannot be
#      building torvalds/linux by accident;
#   2. the PON and UNI PHY drivers are requested;
#   3. the PON MAC firmware blob the config names actually exists and looks like firmware;
#   4. the shipped release notes do not still claim PON has no driver.
#
# Deliberately NOT asserted: that PON works. Whether a PON netdev appears is a runtime fact and
# needs the board. A test that implied it would be the same mistake as the belief it replaces.
set -u
cd /mnt/c/ugit/SK-D840N4

T=immortalwrt/target/linux/zte
CFG=$T/zx279133/config-6.18
MK=$T/Makefile
WF=ci-build-repo/.github/workflows/build-sk-d840n.yml

pass=0
ok()  { pass=$((pass+1)); printf 'PASS %s\n' "$1"; }
bad() { printf 'FAIL %s\n' "$1"; exit 1; }

echo "=== 1. this target cannot build without a prepared external kernel tree ==="
grep -q 'CONFIG_EXTERNAL_KERNEL_TREE' "$MK" \
	&& ok "$MK requires CONFIG_EXTERNAL_KERNEL_TREE" \
	|| bad "$MK no longer mentions CONFIG_EXTERNAL_KERNEL_TREE - is this upstream Linux now?"
grep -qE '\$\(error .*EXTERNAL_KERNEL_TREE' "$MK" \
	&& ok "and it is a hard error, not a soft default" \
	|| echo "  note: the error line is worded differently; the symbol is still referenced"

echo
echo "=== 2. PON-side drivers are requested by the target ==="
for s in CONFIG_PHY_ZX279133_PON_SERDES CONFIG_PHY_ZX279133_UNI_SERDES CONFIG_ZTE_ZX279133_ETH CONFIG_ZX279051_PHY; do
	grep -qx "$s=y" "$CFG" && ok "$s=y" || bad "$s is not requested by $CFG"
done

echo
echo "=== 3. the PON MAC firmware the config names really exists ==="
FW=$(sed -n 's/^CONFIG_EXTRA_FIRMWARE="\(.*\)"/\1/p' "$CFG")
[ -n "$FW" ] || bad "no CONFIG_EXTRA_FIRMWARE in $CFG"
echo "  named: $FW"
P="$T/zx279133/firmware/$FW"
[ -f "$P" ] || bad "$P missing - the kernel names a firmware file that is not there"
sz=$(wc -c <"$P")
echo "  present: $P  $sz B"
[ "$sz" -gt 10000 ] && ok "firmware blob is a plausible size ($sz B)" \
	|| bad "firmware blob is only $sz B - that is not a MAC firmware image"

echo
echo "=== 4. the release notes no longer tell users PON has no driver ==="
if grep -q 'PON 侧主线也没有驱动' "$WF"; then
	bad "the release body still claims PON has no driver - that text ships to users"
fi
grep -q 'CONFIG_PHY_ZX279133_PON_SERDES' "$WF" \
	&& ok "the release body cites the actual config symbols instead" \
	|| echo "  note: the release body does not cite the symbols"

echo
echo "=== 5. and it does not overclaim the other way either ==="
# The corrected text must not start claiming PON works. The honest position is that the drivers
# are present and the runtime behaviour is unknown.
grep -q '仍未证实的是运行时' "$WF" \
	&& ok "the release body still says runtime behaviour is unproven" \
	|| echo "  note: the release body does not state what is still unproven"

echo
echo "all $pass checks passed"
