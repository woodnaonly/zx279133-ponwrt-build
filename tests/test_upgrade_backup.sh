#!/bin/sh
# 本地排演套件（local rehearsal suite）：依赖 staging/ 下的真实构建产物，
# 在只 clone 本仓库的环境里跑不起来，也**没有**接进 CI。
# 为什么、怎么跑、缺的那个夹具从哪来：同目录 README.md。
# Does a sysupgrade actually carry the saved config across?
#
# sysupgrade writes the archive to CONF_TAR=/tmp/sysupgrade.tgz (sbin/sysupgrade:8) and
# hands it to the platform. do_stage2 runs after stage2 has -mount -o move-d the old /tmp
# into the ramfs, so that file is present when platform_do_upgrade runs. The tree's own
# nand.sh reads it from exactly that path (nand.sh:429, `local conf_tar="/tmp/sysupgrade.tgz"`).
#
# platform.sh gated that `mtd -j` behind `[ -n "$UPGRADE_BACKUP" ]`, but nothing in this
# tree ever assigns UPGRADE_BACKUP: 50 references across the tree, zero assignments, and
# the literal string is absent from procd's upgraded binary. So the gate never opens and
# the archive is dropped on every upgrade - silently, with rc=0.
#
# Four cases, both directions, including the one that must NOT use -j.
set -u
cd /mnt/c/ugit/SK-D840N4
export LC_ALL=C

SH=ci-build-repo/overlays/target/linux/zte/zx279133/base-files/lib/upgrade/platform.sh
W=$(mktemp -d)
mkdir -p "$W/bin"
CONF=/tmp/sysupgrade.tgz

pass=0; fail=0

# keep whatever is at $CONF, if anything
SAVED=""
HAD_CONF=no
if [ -e "$CONF" ]; then
	SAVED="$W/conf.saved"; cp -p "$CONF" "$SAVED"; HAD_CONF=yes
fi
cleanup() {
	if [ "$HAD_CONF" = yes ]; then cp -p "$SAVED" "$CONF"; else rm -f "$CONF"; fi
	rm -rf "$W"
}
trap cleanup EXIT INT TERM

cat >"$W/bin/mtd" <<'EOF'
#!/bin/sh
echo "mtd $*" >> "$MTD_LOG"
exit 0
EOF
chmod +x "$W/bin/mtd"

cat >"$W/functions.sh" <<'EOF'
v() { :; }
EOF

rm -rf "$W/pkg"; mkdir -p "$W/pkg/sysupgrade-sk_d840n"
printf 'ROOTFS'  >"$W/pkg/sysupgrade-sk_d840n/root"
printf 'DTB'     >"$W/pkg/sysupgrade-sk_d840n/dtb"
printf 'KERNEL'  >"$W/pkg/sysupgrade-sk_d840n/kernel"
tar czf "$W/upg.tar.gz" -C "$W/pkg" .

sed "s#^\. /lib/functions\.sh#. $W/functions.sh#" "$SH" >"$W/platform.sh"

# The call is `mtd [-j TAR] write SRC LABEL`, so SRC sits between -j TAR and the label.
# Matching the whole line here would silently never fire and turn every case into a
# false negative - match the flag and the archive path only.
used_j() {
	grep -q -- "-j $1 " "$W/mtd.log" 2>/dev/null
}

# partition label is the last field of each mtd line
labels() {
	awk '{print $NF}' "$W/mtd.log"
}

do_upgrade() { # $1 = env value for UPGRADE_BACKUP ("-" = unset)
	: >"$W/mtd.log"
	if [ "$1" = "-" ]; then
		env -u UPGRADE_BACKUP PATH="$W/bin:$PATH" MTD_LOG="$W/mtd.log" \
			sh -c ". $W/platform.sh; platform_do_upgrade \"\$1\"; echo rc=\$?" \
			sh "$W/upg.tar.gz" >"$W/out" 2>&1
	else
		env UPGRADE_BACKUP="$1" PATH="$W/bin:$PATH" MTD_LOG="$W/mtd.log" \
			sh -c ". $W/platform.sh; platform_do_upgrade \"\$1\"; echo rc=\$?" \
			sh "$W/upg.tar.gz" >"$W/out" 2>&1
	fi
}

check() { # $1 label  $2 expected yes|no  $3 actual yes|no
	if [ "$2" = "$3" ]; then
		printf '  PASS  %-52s (-j used: %s)\n' "$1" "$3"; pass=$((pass+1))
	else
		printf '  FAIL  %-52s (expected -j used: %s, got: %s)\n' "$1" "$2" "$3"; fail=$((fail+1))
	fi
}

echo "=== control: the harness can see a -j call at all ==="
printf 'EXPLICIT-ARCHIVE' >"$W/explicit-backup.tgz"
do_upgrade "$W/explicit-backup.tgz"
echo "  mtd log:"; sed 's/^/    /' "$W/mtd.log"
if grep -q 'mtd -j' "$W/mtd.log"; then
	printf '  PASS  %-52s (%s)\n' "harness detects -j when the var is set" "yes"; pass=$((pass+1))
else
	printf '  FAIL  %-52s (harness is blind - every other case is meaningless)\n' "harness detects -j"; fail=$((fail+1))
fi

echo
echo "=== the real configuration: no UPGRADE_BACKUP in the env, archive at CONF_TAR ==="
rm -f "$CONF"; printf 'CONFIG-ARCHIVE' >"$CONF"
do_upgrade -
echo "  mtd log:"; sed 's/^/    /' "$W/mtd.log"
rc=$(grep -o 'rc=[0-9]*' "$W/out" | tail -1)
if used_j "$CONF"; then a=yes; else a=no; fi
check "archive at /tmp/sysupgrade.tgz survives the upgrade" yes "$a"
# and the upgrade still reports success - this is why the loss was silent
printf '  note: platform_do_upgrade reported %s (a dropped config is not an error)\n' "$rc"

echo
echo "=== negative: no archive anywhere, must NOT invent one ==="
rm -f "$CONF"
do_upgrade -
if used_j /tmp/sysupgrade.tgz; then a=yes; else a=no; fi
check "no archive present -> plain write, no bogus -j" no "$a"

echo
echo "=== positive: the var is honoured when something really does set it ==="
do_upgrade "$W/explicit-backup.tgz"
if used_j "$W/explicit-backup.tgz"; then a=yes; else a=no; fi
check "UPGRADE_BACKUP is still respected" yes "$a"

echo
echo "=== fallback: var points at a file that is not there, CONF_TAR is ==="
rm -f "$CONF"; printf 'CONFIG-ARCHIVE' >"$CONF"
do_upgrade "$W/never-created.tgz"
if used_j "$CONF"; then a=yes; else a=no; fi
check "missing UPGRADE_BACKUP falls back to /tmp/sysupgrade.tgz" yes "$a"

echo
echo "=== var points nowhere and there is no CONF_TAR either ==="
rm -f "$CONF"
do_upgrade "$W/never-created.tgz"
if used_j "$W/never-created.tgz"; then a=yes; else a=no; fi
check "no archive at all -> plain write, no bogus -j" no "$a"

echo
echo "=== ordering is unchanged: root before dtb before kernel ==="
rm -f "$CONF"; printf 'CONFIG-ARCHIVE' >"$CONF"
do_upgrade -
order=$(labels | tr '\n' ' ')
if [ "$order" = "root dtb kernel " ]; then
	printf '  PASS  %-52s (%s)\n' "write order" "$order"; pass=$((pass+1))
else
	printf '  FAIL  %-52s (got: %s)\n' "write order" "$order"; fail=$((fail+1))
fi

echo
echo "=== pass=$pass fail=$fail ==="
[ "$fail" -eq 0 ] || exit 1
exit 0