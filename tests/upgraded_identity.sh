#!/bin/sh
# 本地排演套件（local rehearsal suite）：依赖 staging/ 下的真实构建产物，
# 在只 clone 本仓库的环境里跑不起来，也**没有**接进 CI。
# 为什么、怎么跑、缺的那个夹具从哪来：同目录 README.md。
# Two things to nail down before calling this a bug:
#   1. what /sbin/upgraded actually is (section 3 of the last script printed nothing for
#      it, which is itself suspicious - verify it is a real file with real content);
#   2. an UNRESTRICTED full-tree search for the assignment - the last one filtered on
#      *.sh and so could have missed a C source or a file with no extension.
set -u
export LC_ALL=C

R=/tmp/staged-root2
T=/mnt/c/ugit/SK-D840N4/immortalwrt

echo "=== 1. identity of /sbin/upgraded ==="
ls -l "$R/sbin/upgraded" 2>/dev/null | sed 's/^/  /'
printf '  size: %s bytes\n' "$(wc -c <"$R/sbin/upgraded" 2>/dev/null)"
printf '  file: %s\n' "$(file "$R/sbin/upgraded" 2>/dev/null | sed "s|$R/sbin/upgraded: ||")"
echo "  --- first 20 lines ---"
head -20 "$R/sbin/upgraded" 2>/dev/null | sed 's/^/    /'
echo "  --- control: grep 'sh' hits in it (must be >0 if it is a script) ---"
printf '  sh-hits=%s json-hits=%s backup-hits=%s\n' \
	"$(grep -c 'sh' "$R/sbin/upgraded" 2>/dev/null)" \
	"$(grep -c 'json' "$R/sbin/upgraded" 2>/dev/null)" \
	"$(grep -c 'backup' "$R/sbin/upgraded" 2>/dev/null)"

echo
echo "=== 2. unrestricted full-tree search for the assignment ==="
echo "  (no --include filters this time)"
out=$(grep -rn 'UPGRADE_BACKUP=' "$T" 2>/dev/null | head -30)
if [ -n "$out" ]; then
	printf '%s\n' "$out" | sed "s|$T/|  |"
else
	echo "  ZERO assignments to UPGRADE_BACKUP anywhere in the source tree"
fi

echo
echo "=== 3. control for the full-tree search: it must find a known assignment ==="
grep -rn 'SAVE_OVERLAY_PATH=' "$T" 2>/dev/null | head -3 | sed "s|$T/|  |"
echo "  (end of control)"

echo
echo "=== 4. who calls do_stage2 / passes IMAGE, in the built image ==="
grep -rln 'do_stage2' "$R" 2>/dev/null | sed "s|$R|  <rootfs>|"
echo "  --- the caller(s) in detail ---"
for f in $(grep -rln 'do_stage2' "$R" 2>/dev/null); do
	echo "  --- ${f#$R}"
	grep -n 'UPGRADE_BACKUP\|do_stage2\|IMAGE=\|export' "$f" | sed 's/^/      /'
done

echo
echo "=== 5. so where does procd pick up the json? search for the handler ==="
grep -rln 'system sysupgrade\|sysupgrade.json\|/tmp/sysupgrade' "$R" 2>/dev/null | sed "s|$R|  <rootfs>|"