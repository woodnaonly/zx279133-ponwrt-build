#!/bin/sh
# 本地排演套件（local rehearsal suite）：依赖 staging/ 下的真实构建产物，
# 在只 clone 本仓库的环境里跑不起来，也**没有**接进 CI。
# 为什么、怎么跑、缺的那个夹具从哪来：同目录 README.md。
# The previous run said `strings | grep UPGRADE` printed nothing while `grep -aci UPGRADE`
# on the same file said 4. Those cannot both be true, so `strings` is probably missing and
# the pipeline silently produced nothing - the exact failure mode that has bitten this
# session repeatedly. Redo it with grep -a -o only, and calibrate first.
set -u
export LC_ALL=C
R=/tmp/staged-root2
ELF="$R/sbin/upgraded"

echo "=== 0. is the helper I leaned on actually installed? ==="
printf '  command -v strings : %s\n' "$(command -v strings || echo MISSING)"
printf '  command -v od      : %s\n' "$(command -v od || echo MISSING)"

echo
echo "=== 1. calibration: extract printable tokens with grep -a -o (no strings needed) ==="
printf '  UPGRADE_[A-Z_]* tokens: '
grep -a -o 'UPGRADE_[A-Z_]*' "$ELF" 2>/dev/null | sort -u | tr '\n' ' '
echo
printf '  *_BACKUP tokens     : '
grep -a -o '[A-Z_]*BACKUP[A-Z_]*' "$ELF" 2>/dev/null | sort -u | tr '\n' ' '
echo

echo
echo "=== 2. THE QUESTION: is the literal UPGRADE_BACKUP in upgraded? ==="
if grep -aq 'UPGRADE_BACKUP' "$ELF" 2>/dev/null; then
	echo "  PRESENT - procd's upgraded exports it, so our platform.sh branch is live"
else
	echo "  ABSENT  <-- upgraded does not carry the name"
fi

echo
echo "=== 3. what env-ish UPPER_CASE names ARE in there (to see what it does set) ==="
grep -a -o '\b[A-Z][A-Z_]\{3,\}\b' "$ELF" 2>/dev/null | sort -u | sed 's/^/    /' | head -40

echo
echo "=== 4. sanity: prove grep -a can find a string I know is in the file ==="
printf '  "sysupgrade" present: %s\n' "$(grep -aq 'sysupgrade' "$ELF" && echo yes || echo no)"
printf '  "prefix"     present: %s\n' "$(grep -aq 'prefix' "$ELF" && echo yes || echo no)"

echo
echo "=== 5. cross-check with a second extractor (tr + grep) to avoid trusting one tool ==="
tr -c '[:print:]' '\n' < "$ELF" 2>/dev/null | grep -a -E 'UPGRADE|BACKUP' | sort -u | sed 's/^/    /'
echo "  (end)"