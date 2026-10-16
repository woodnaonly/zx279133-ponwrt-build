#!/bin/sh
# 本地排演套件（local rehearsal suite）：依赖 staging/ 下的真实构建产物，
# 在只 clone 本仓库的环境里跑不起来，也**没有**接进 CI。
# 为什么、怎么跑、缺的那个夹具从哪来：同目录 README.md。
# UPGRADE_BACKUP: is it ever set? If not, the `if [ -n "$UPGRADE_BACKUP" ]` branch in
# our platform.sh is dead code, `mtd -j` never runs, and every sysupgrade silently drops
# the saved config archive - including the 192.168.9.1 that 02_network wrote.
#
# Both directions are checked: (a) is it set anywhere, and (b) does the control grep work,
# so an absence is never read as a broken path.
set -u
export LC_ALL=C

BF=/mnt/c/ugit/SK-D840N4/immortalwrt/package/base-files/files
SYSU="$BF/sbin/sysupgrade"

echo "=== 0. control: grep works on sysupgrade at all ==="
printf '  total lines: %s\n' "$(wc -l <"$SYSU")"
printf '  "platform_" hits (must be > 0): %s\n' "$(grep -c 'platform_' "$SYSU")"

echo
echo "=== 1. UPGRADE_BACKUP in sysupgrade ==="
grep -n 'UPGRADE_BACKUP' "$SYSU" && echo "  ^ found" || echo "  ZERO (no line in sysupgrade mentions it)"

echo
echo "=== 2. UPGRADE_BACKUP anywhere in the whole base-files package ==="
n=$(grep -rn 'UPGRADE_BACKUP' "$BF" 2>/dev/null | wc -l)
echo "  matching lines across $BF: $n"
[ "$n" -gt 0 ] && grep -rn 'UPGRADE_BACKUP' "$BF" 2>/dev/null | sed 's/^/    /'

echo
echo "=== 3. does any reference platform pass a config tar to mtd -j? ==="
for p in "$BF"/lib/upgrade/*.sh; do
	[ -f "$p" ] || continue
	if grep -q 'mtd .*-j\|-j .*write\|CONF_TAR\|conf_tar\|confbackup' "$p" 2>/dev/null; then
		echo "  --- ${p##*/}"
		grep -n 'mtd .*-j\|CONF_TAR\|conf_tar\|confbackup' "$p" 2>/dev/null | sed 's/^/      /'
	fi
done

echo
echo "=== 4. the canonical nand platform, in full (the thing to copy) ==="
if [ -f "$BF/lib/upgrade/nand.sh" ]; then
	grep -n '^[A-Za-z_][A-Za-z_0-9]*()' "$BF/lib/upgrade/nand.sh" | sed 's/^/  /'
	echo "  --- its platform_do_upgrade ---"
	sed -n '/^platform_do_upgrade/,/^}/p' "$BF/lib/upgrade/nand.sh" | sed 's/^/    /'
else
	echo "  nand.sh ABSENT - control for this section FAILED, do not trust section 3 either"
	ls "$BF/lib/upgrade/" | sed 's/^/    /'
fi

echo
echo "=== 5. how sysupgrade actually gets the config archive to the platform ==="
grep -n 'CONF_TAR\|CONF_RESTORE\|do_upgrade\|platform_do_upgrade' "$SYSU" | sed 's/^/  /'