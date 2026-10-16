#!/bin/sh
# 本地排演套件（local rehearsal suite）：依赖 staging/ 下的真实构建产物，
# 在只 clone 本仓库的环境里跑不起来，也**没有**接进 CI。
# 为什么、怎么跑、缺的那个夹具从哪来：同目录 README.md。
# Sanity-gated follow-up: section D of copy_config_check.sh returned nothing. That is only
# meaningful if the file I grepped actually exists AND grep works on it. Prove both before
# believing any "absent" conclusion.
set -u
export LC_ALL=C

BF=/mnt/c/ugit/SK-D840N4/immortalwrt/package/base-files/files

echo "=== 0. sanity gate: is the tree where I think it is? ==="
echo "  base-files/files exists: $([ -d "$BF" ] && echo yes || echo NO)"
echo "  --- top level of files/ ---"
ls "$BF" 2>/dev/null | sed 's/^/    /' || echo "    (ls failed)"

echo
echo "=== 1. locate the real sysupgrade binary (control: does this grep hit at all?) ==="
found=""
for p in "$BF/sbin/sysupgrade" "$BF/usr/sbin/sysupgrade" /tmp/staged-root2/sbin/sysupgrade \
         /tmp/staged-root2/usr/sbin/sysupgrade /tmp/staged-root2/usr/libexec/sysupgrade; do
	if [ -f "$p" ]; then
		echo "  FOUND $p  ($(wc -c <"$p") bytes)"
		found="$p"
		break
	fi
done
[ -n "$found" ] || echo "  sysupgrade NOT FOUND at any of the guessed paths"

echo
echo "=== 2. control grep: a string that MUST be in sysupgrade ==="
if [ -n "$found" ]; then
	printf '  "upgrade" hits: %s\n' "$(grep -c 'upgrade' "$found")"
	printf '  "platform_" hits: %s\n' "$(grep -c 'platform_' "$found")"
	echo "  --- every platform_* the script references ---"
	grep -o 'platform_[a-z_]*' "$found" | sort -u | sed 's/^/      /'
fi

echo
echo "=== 3. the actual config-preservation path in sysupgrade ==="
if [ -n "$found" ]; then
	echo "  --- lines mentioning conf_tar / keep.d / config ---"
	grep -n 'conf_tar\|keep\.d\|conf_save\|CONFIG' "$found" | sed 's/^/      /'
fi

echo
echo "=== 4. every copy_config definition anywhere in the base-files package ==="
hits=$(grep -rn 'copy_config()' "$BF" 2>/dev/null) || hits=""
if [ -n "$hits" ]; then
	printf '%s\n' "$hits" | sed 's/^/  /'
else
	echo "  ZERO definitions of copy_config() in $BF"
	echo "  (grep found $(grep -rc 'platform_' "$BF" 2>/dev/null | awk -F: '$2>0' | wc -l) files mentioning platform_ at all,"
	echo "   so grep is working - this is a real absence, not a broken path)"
fi