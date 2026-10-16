#!/bin/sh
# 本地排演套件（local rehearsal suite）：依赖 staging/ 下的真实构建产物，
# 在只 clone 本仓库的环境里跑不起来，也**没有**接进 CI。
# 为什么、怎么跑、缺的那个夹具从哪来：同目录 README.md。
# Decisive search: where does UPGRADE_BACKUP come from?
# Chain so far: sysupgrade -> ubus(system sysupgrade, json{backup=...}) -> /sbin/upgraded
# (ELF, procd) -> exec COMMAND=lib/upgrade/stage2 -> do_stage2 -> platform_do_upgrade.
# Neither stage2 nor do_stage2 nor sysupgrade assigns it. upgraded's string table has no
# "backup" either. So it must be set from procd's C source, or it genuinely does not exist.
set -u
export LC_ALL=C
T=/mnt/c/ugit/SK-D840N4/immortalwrt
R=/tmp/staged-root2

echo "=== 1. every UPGRADE_BACKUP in the ENTIRE source tree (any form, any case) ==="
out=$(grep -rni 'UPGRADE_BACKUP' "$T" 2>/dev/null)
n=$(printf '%s' "$out" | grep -c . )
echo "  lines: $n"
[ "$n" -gt 0 ] && printf '%s\n' "$out" | sed "s|$T/|    |" | head -40

echo
echo "=== 2. control: the tree really is searchable at this depth ==="
printf '  files under package/system/procd: %s\n' "$(find "$T/package/system/procd" -type f 2>/dev/null | wc -l)"
grep -rn 'system sysupgrade\|sysupgrade' "$T/package/system/procd" 2>/dev/null | head -5 | sed "s|$T/|    |"
echo "  (end of control)"

echo
echo "=== 3. case-insensitive strings in the upgraded ELF ==="
for pat in backup UPGRADE json path prefix command force; do
	printf '  %-10s : %s\n' "$pat" "$(grep -aci "$pat" "$R/sbin/upgraded" 2>/dev/null)"
done
echo "  --- the actual printable strings mentioning any of those ---"
strings "$R/sbin/upgraded" 2>/dev/null | grep -iE 'backup|upgrade_|^UPGRADE' | sed 's/^/    /' | head -20
echo "  (end of strings)"

echo
echo "=== 4. who implements the ubus method 'system sysupgrade'? ==="
grep -rln 'sysupgrade' "$T/package/system/procd" "$T/package/system/ubusd" 2>/dev/null | sed "s|$T/|    |"
echo "  --- search C sources for the json key handling ---"
grep -rn '"backup"' "$T/package/system/procd" 2>/dev/null | sed "s|$T/|    |" | head -20
echo "  (end)"

echo
echo "=== 5. DECISION INPUT: what do the tree's OWN platforms rely on? ==="
echo "  emmc.sh:44 and legacy-sdcard.sh:81 read \$UPGRADE_BACKUP."
echo "  If nothing sets it, those two platforms are equally broken - which is implausible"
echo "  for shipping OpenWrt, so treat this as 'the assignment is somewhere I have not"
echo "  looked yet' until positively disproven."
grep -rn 'UPGRADE_BACKUP' "$T/package/base-files/files/lib/upgrade/common.sh" | sed 's/^/    /'