#!/bin/sh
# 本地排演套件（local rehearsal suite）：依赖 staging/ 下的真实构建产物，
# 在只 clone 本仓库的环境里跑不起来，也**没有**接进 CI。
# 为什么、怎么跑、缺的那个夹具从哪来：同目录 README.md。
# Does the flashed image preserve /etc/config across a sysupgrade?
#
# /lib/upgrade/keep.d/base-files lists /etc/config/ as kept, but that list is only
# *read* by platform_copy_config. Nothing in keep.d has any effect on its own, so if
# this platform neither defines platform_copy_config nor falls through to the generic one,
# the keep.d lists are decoration and a sysupgrade wipes the config - including the
# `ipaddr 192.168.9.1` that 02_network writes and that every tool here waits for.
set -u
export LC_ALL=C

BUILT=/tmp/staged-root2
REPO=/mnt/c/ugit/SK-D840N4/ci-build-repo/overlays/target/linux/zte/zx279133/base-files/lib/upgrade

echo "=== A. functions defined in the BUILT platform.sh ==="
if [ -f "$BUILT/lib/upgrade/platform.sh" ]; then
	grep -n '^[A-Za-z_][A-Za-z_0-9]*()' "$BUILT/lib/upgrade/platform.sh" || echo "  (none matched)"
	echo "  --- does it mention copy_config at all? ---"
	grep -n 'copy_config' "$BUILT/lib/upgrade/platform.sh" || echo "  NO  <-- platform_copy_config absent from the shipped file"
else
	echo "  built platform.sh ABSENT at $BUILT/lib/upgrade/platform.sh"
fi

echo
echo "=== B. functions defined in the REPO platform.sh (the fixed one) ==="
if [ -f "$REPO/platform.sh" ]; then
	grep -n '^[A-Za-z_][A-Za-z_0-9]*()' "$REPO/platform.sh" || echo "  (none matched)"
	echo "  --- does it mention copy_config at all? ---"
	grep -n 'copy_config' "$REPO/platform.sh" || echo "  NO  <-- platform_copy_config absent from the repo file too"
else
	echo "  repo platform.sh ABSENT at $REPO/platform.sh"
fi

echo
echo "=== C. is a generic platform_copy_config available to fall through to? ==="
for f in "$BUILT/lib/upgrade/common.sh" /mnt/c/ugit/SK-D840N4/immortalwrt/package/base-files/files/lib/upgrade/common.sh; do
	if [ -f "$f" ]; then
		echo "  --- $f"
		grep -n 'copy_config' "$f" || echo "      no copy_config here"
	else
		echo "  --- $f  ABSENT"
	fi
done

echo
echo "=== D. who calls platform_copy_config (the caller decides if it is optional) ==="
for f in /mnt/c/ugit/SK-D840N4/immortalwrt/package/base-files/files/sbin/sysupgrade \
         /mnt/c/ugit/SK-D840N4/immortalwrt/package/base-files/files/lib/upgrade/*.sh; do
	[ -f "$f" ] || continue
	hit=$(grep -n 'platform_copy_config' "$f" 2>/dev/null) || hit=""
	if [ -n "$hit" ]; then
		echo "  --- ${f##*/}"
		printf '%s\n' "$hit" | sed 's/^/      /'
	fi
done