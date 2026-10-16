#!/bin/sh
# 本地排演套件（local rehearsal suite）：依赖 staging/ 下的真实构建产物，
# 在只 clone 本仓库的环境里跑不起来，也**没有**接进 CI。
# 为什么、怎么跑、缺的那个夹具从哪来：同目录 README.md。
# libfstools prints "root filesystem changed, keeping only the settings" when the rootfs
# id changes - which is exactly what flashing a new `root` partition does - and then tries
# to reset the overlay. On this board the reset is refused (no /.owrt), so nothing is
# deleted. That makes /etc/sysupgrade.conf the only thing standing between a user and a
# wiped configuration after an upgrade, so read it as built.
set -u
cd /mnt/c/ugit/SK-D840N4
export LC_ALL=C
R=/tmp/staged-root2

echo "=== 1. the shipped /etc/sysupgrade.conf ==="
if [ -f "$R/etc/sysupgrade.conf" ]; then
	cat "$R/etc/sysupgrade.conf" | sed 's/^/  /'
	echo "  --- active (non-comment) lines ---"
	sed -ne '/^[[:space:]]*$/d; /^#/d; p' "$R/etc/sysupgrade.conf" | sed 's/^/    /' \
		|| echo "    (none - nothing is preserved)"
else
	echo "  ABSENT"
fi

echo
echo "=== 2. what OpenWrt keeps by default (keep.d) ==="
if [ -d "$R/lib/upgrade/keep.d" ]; then
	ls "$R/lib/upgrade/keep.d" | sed 's/^/  /'
	echo "  --- contents ---"
	for f in "$R/lib/upgrade/keep.d"/*; do
		[ -f "$f" ] || continue
		echo "  --- ${f##*/}"
		sed 's/^/      /' "$f"
	done
else
	echo "  /lib/upgrade/keep.d absent"
fi

echo
echo "=== 3. does sysupgrade restore what keep.d says? (the other half) ==="
ls "$R/etc/sysupgrade.d" 2>/dev/null | sed 's/^/  /' || echo "  /etc/sysupgrade.d absent"
for f in "$R/etc/sysupgrade.d"/*; do
	[ -f "$f" ] || continue
	echo "  --- ${f##*/}"
	sed 's/^/      /' "$f"
done

echo
echo "=== 4. our platform.sh's own -j handling ==="
grep -n 'UPGRADE_BACKUP' "$R/lib/upgrade/platform.sh" 2>/dev/null | sed 's/^/  /' \
	|| echo "  (built image has no platform.sh - it was built before the fix)"