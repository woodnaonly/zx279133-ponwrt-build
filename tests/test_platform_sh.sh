#!/bin/sh
# 本地排演套件（local rehearsal suite）：依赖 staging/ 下的真实构建产物，
# 在只 clone 本仓库的环境里跑不起来，也**没有**接进 CI。
# 为什么、怎么跑、缺的那个夹具从哪来：同目录 README.md。
# Run the SHIPPED platform.sh against a fake `mtd` that records every call, so the
# upgrade path is measured rather than read. Three things matter:
#   - which partitions it would write, and in what order
#   - whether it refuses a tar that is not a sysupgrade tar
#   - where it stops if one of the writes fails, because on a board with no serial
#     console a half-finished upgrade is the whole risk of pressing the button
set -u
cd /mnt/c/ugit/SK-D840N4
export LC_ALL=C

SH=ci-build-repo/overlays/target/linux/zte/zx279133/base-files/lib/upgrade/platform.sh
W=$(mktemp -d)
mkdir -p "$W/bin"

# fake mtd: log argv; honour FAIL_ON so a chosen write can be made to fail
cat >"$W/bin/mtd" <<'EOF'
#!/bin/sh
echo "mtd $*" >> "$MTD_LOG"
for a in "$@"; do
	if [ "$a" = "$FAIL_ON" ]; then
		echo "mtd: refusing $a (injected failure)" >&2
		exit 1
	fi
done
exit 0
EOF
chmod +x "$W/bin/mtd"

# fake /lib/functions.sh providing v(), which the script uses for messages
cat >"$W/functions.sh" <<'EOF'
v() { echo "v: $*" >> "$MSG_LOG"; }
EOF

build_tar() { # $1 = dir name under sysupgrade-*, $2 = which members exist
	d=$1; members=$2
	rm -rf "$W/pkg"; mkdir -p "$W/pkg/$d"
	case "$members" in
	all)
		printf 'ROOTFS'  >"$W/pkg/$d/root"
		printf 'DTB'    >"$W/pkg/$d/dtb"
		printf 'KERNEL' >"$W/pkg/$d/kernel" ;;
	noDTB)
		printf 'ROOTFS'  >"$W/pkg/$d/root"
		printf 'KERNEL' >"$W/pkg/$d/kernel" ;;
	esac
	tar czf "$W/upg.tar.gz" -C "$W/pkg" .
}

run() { # $1 label  $2 members  $3 fail-on (or -)
	label=$1; members=$2; failon=$3
	build_tar "sysupgrade-sk_d840n" "$members"
	MTD_LOG="$W/mtd.log"; MSG_LOG="$W/msg.log"; FAIL_ON="$failon"
	: >"$MTD_LOG"; : >"$MSG_LOG"
	export MTD_LOG MSG_LOG FAIL_ON

	# platform.sh sources the absolute path /lib/functions.sh; relocate it instead of
	# writing to the real /lib. The body of platform.sh is otherwise untouched.
	sed "s#^\. /lib/functions\.sh#. $W/functions.sh#" "$SH" >"$W/platform.sh"

	# a plausible /proc/mtd as the mainline kernel registers it
	cat >"$W/proc-mtd" <<'EOF'
dev:    size   erasesize  name
mtd1: 00200000 00020000 "boot"
mtd2: 00800000 00020000 "kernel"
mtd3: 00100000 00020000 "dtb"
mtd4: 00400000 00020000 "parameter tags"
mtd5: 0f000000 00020000 "root"
mtd6: 00100000 00020000 "scratch"
EOF

	echo
	echo "--- $label  (fail-on: $failon)"
	out=$(PATH="$W/bin:$PATH" MTD_LOG="$MTD_LOG" MSG_LOG="$MSG_LOG" FAIL_ON="$failon" \
		sh -c "v() { echo \"v: \$*\" >> \"\$MSG_LOG\"; }
		          . $W/platform.sh
		          platform_do_upgrade \"\$1\"
		          echo \"rc=\$?\"" sh "$W/upg.tar.gz" 2>&1)
	echo "$out" | sed 's/^/    /'
	echo "    mtd calls:"
	if [ -s "$MTD_LOG" ]; then sed 's/^/      /' "$MTD_LOG"; else echo "      (none)"; fi
	echo "    messages:"
	if [ -s "$MSG_LOG" ]; then sed 's/^/      /' "$MSG_LOG"; else echo "      (none)"; fi
}

run "happy path: root+dtb+kernel, nothing fails" all -
run "kernel write fails" all kernel
run "dtb write fails" all dtb
run "root write fails" all root
run "image without a dtb member" noDTB -
rm -rf "$W"