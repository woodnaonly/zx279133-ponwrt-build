#!/bin/sh
# 本地排演套件（local rehearsal suite）：依赖 staging/ 下的真实构建产物，
# 在只 clone 本仓库的环境里跑不起来，也**没有**接进 CI。
# 为什么、怎么跑、缺的那个夹具从哪来：同目录 README.md。
# The gate must now ACCEPT the real flash-sysupgrade.bin (which is an uncompressed tar,
# as scripts/sysupgrade-tar.sh produces) while still REJECTING everything that is not a
# sysupgrade image. And the upgrade itself must still write by label, in order.
set -u
cd /mnt/c/ugit/SK-D840N4
export LC_ALL=C

SH=ci-build-repo/overlays/target/linux/zte/zx279133/base-files/lib/upgrade/platform.sh
W=$(mktemp -d); mkdir -p "$W/bin"
echo '. /dev/null' >"$W/functions.sh"
sed "s#^\. /lib/functions\.sh#. $W/functions.sh#" "$SH" >"$W/platform.sh"

# a mainline /proc/mtd the way the real board registers it
cat >"$W/proc-mtd" <<'EOF'
mtd1: 00200000 00020000 "boot"
mtd2: 00800000 00020000 "kernel"
mtd3: 00100000 00020000 "dtb"
mtd4: 00400000 00020000 "parameter tags"
mtd5: 0f000000 00020000 "root"
mtd6: 00100000 00020000 "scratch"
EOF

# fake mtd that records calls
cat >"$W/bin/mtd" <<'EOF'
#!/bin/sh
echo "mtd $*" >> "$MTD_LOG"
exit 0
EOF
chmod +x "$W/bin/mtd"

check() { # label file expected-rc
	MSG_LOG="$W/msg.log"; export MSG_LOG; : >"$MSG_LOG"
	PATH="$W/bin:$PATH" sh -c '
		v() { echo "v: $*" >> "$MSG_LOG"; }
		. '"$W"'/platform.sh
		# platform.sh reads the real /proc/mtd, which only exists on the board. It is
		# resolved at call time, so overriding it here isolates the tar handling - which
		# is what this fix is about - from a check that cannot pass off-device.
		skd840n_has_part() { grep -q "\"$1\"" '"$W"'/proc-mtd; }
		platform_check_image "$1"
		echo "rc=$?"' sh "$2" >"$W/o.txt" 2>&1
	rc=$(sed -n 's/^rc=//p' "$W/o.txt" | tail -1)
	want=$3
	verdict=$( [ "$rc" = 0 ] && echo ACCEPT || echo REJECT )
	if [ "$rc" = "$want" ]; then
		printf '  ok   %-46s rc=%s  %s\n' "$1" "$rc" "$verdict"
	else
		printf '  FAIL %-46s rc=%s  %s (want rc=%s)\n' "$1" "$rc" "$verdict" "$want"
		sed 's/^/        /' "$W/o.txt"
	fi
	[ -s "$MSG_LOG" ] && sed 's/^/        /' "$MSG_LOG"
	return $( [ "$rc" = "$want" ] && echo 0 || echo 1 )
}

fail=0
echo "=== format of the artifact under test ==="
printf '  first 2 bytes: %s   (1f8b = gzip)\n' "$(head -c 2 staging/trial/flash-sysupgrade.bin | od -An -tx1 | tr -d ' \n')"
echo "  tar entries (no z): $(tar tf staging/trial/flash-sysupgrade.bin 2>/dev/null | wc -l)"
echo "  tar entries (with z): $(tar tzf staging/trial/flash-sysupgrade.bin 2>/dev/null | wc -l)"
echo "  first entries:"
tar tf staging/trial/flash-sysupgrade.bin 2>/dev/null | head -6 | sed 's/^/    /'

echo
echo "=== the gate ==="
check "real flash-sysupgrade.bin" staging/trial/flash-sysupgrade.bin 0 || fail=1
check "flash-set.tgz (not a sysupgrade image)" staging/trial/flash-set.tgz 1 || fail=1
check "flash-uImage (raw kernel)" staging/trial/flash-uImage 1 || fail=1
check "flash-dtb.bin (raw dtb)" staging/trial/flash-dtb.bin 1 || fail=1
check "flash-rootfs.jffs2 (raw jffs2)" staging/trial/flash-rootfs.jffs2 1 || fail=1
check "a text file" staging/trial/sha256sums-release 1 || fail=1

echo
echo "=== gzip round-trip: a gzipped copy must be accepted too ==="
gzip -c staging/trial/flash-sysupgrade.bin >"$W/gz.tar.gz"
printf '  first 2 bytes: %s\n' "$(head -c 2 "$W/gz.tar.gz" | od -An -tx1 | tr -d ' \n')"
check "the same image, gzipped" "$W/gz.tar.gz" 0 || fail=1

echo
echo "=== and the upgrade itself on the real artifact ==="
MTD_LOG="$W/mtd.log"; export MTD_LOG; : >"$MTD_LOG"
PATH="$W/bin:$PATH" sh -c '
	. '"$W"'/platform.sh
	platform_do_upgrade "$1"
	echo "rc=$?"' sh staging/trial/flash-sysupgrade.bin 2>&1 | sed 's/^/  /'
echo "  mtd calls:"
sed 's/^/    /' "$MTD_LOG"

echo
[ "$fail" -eq 0 ] && echo "OVERALL: PASS" || echo "OVERALL: FAIL"
rm -rf "$W"
exit $fail