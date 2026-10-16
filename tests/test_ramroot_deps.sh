#!/bin/sh
# 本地排演套件（local rehearsal suite）：依赖 staging/ 下的真实构建产物，
# 在只 clone 本仓库的环境里跑不起来，也**没有**接进 CI。
# 为什么、怎么跑、缺的那个夹具从哪来：同目录 README.md。
# Three archives, three scripts, three different tool sets - and each has to keep having what
# its own script needs.
#
# This mapping is not written down anywhere else, and it is easy to get wrong in both
# directions. flash-sk-d840n.sh uses sha256sum and awk; a glance at ctl-flash.cpio.gz says
# neither is in it, and the natural conclusion is a broken flasher - but that script does not
# run there. It runs in the kexec'd RAM root, which is trial-initramfs.cpio.gz, and there both
# are present. Meanwhile ctl-flash.cpio.gz really is short of sha256sum and awk, and that is
# fine because init-flash.sh writes with mtd directly.
#
# These archives get rebuilt whenever the five fixes land. If a rebuild quietly drops one
# binary, the write path stops working on the board - with no error until the round is spent.
# So the mapping is asserted here rather than remembered.
set -u
cd /mnt/c/ugit/SK-D840N4

pass=0
miss=0
ok()  { pass=$((pass+1)); printf 'PASS %s\n' "$1"; }
no()  { miss=$((miss+1)); printf 'FAIL %s\n' "$1"; }

has() { # archive tool
	# No -x here. `-x` demands a whole-line match, which cancels the (^|/) alternation: the
	# entry is "sbin/mtd", and with -x neither "^mtd" nor "/mtd$" matches the entire line, so
	# every tool reads as missing. Without -x the "/mtd$" branch matches the suffix, while
	# "lib/apk/packages/mtd.list" still does not match because it ends in ".list".
	gzip -dc "$1" 2>/dev/null | cpio -t 2>/dev/null | grep -qE "(^|/)$2\$"
}

# A matcher that never fires reports everything missing and looks like a catastrophic finding,
# which is exactly what happened the first time. So it is calibrated against entries whose
# presence is known, both ways, before any verdict from it is believed.
echo "=== calibrating the matcher ==="
L=/tmp/rrd.cal
gzip -dc staging/trial/trial-initramfs.cpio.gz 2>/dev/null | cpio -t 2>/dev/null >"$L" 2>/dev/null
if [ -s "$L" ]; then
	grep -qE '(^|/)mtd$' "$L"   && echo "  calibration: sbin/mtd found (good)"   || { echo "  matcher cannot find a tool that IS there - refusing to report"; exit 1; }
	grep -qE '(^|/)definitely_not_a_real_tool_xyz$' "$L" && { echo "  matcher matches a tool that is absent - it is broken"; exit 1; }
	echo "  calibration: absent tool correctly not found (good)"
	has staging/trial/trial-initramfs.cpio.gz mtd && ok "has() agrees with a direct grep" \
		|| no "has() disagrees with a direct grep on the same input"
	# The concern is a false positive, so look at the matched set rather than asking whether
	# some other name happens to be findable: querying "mtd" must select the tool and must not
	# drag in lib/apk/packages/mtd.list, which merely ends in the same letters.
	matched=$(grep -E '(^|/)mtd$' "$L")
	echo "$matched" | grep -qx 'sbin/mtd' \
		&& ok "querying mtd selects sbin/mtd" || no "querying mtd did not select sbin/mtd"
	echo "$matched" | grep -q 'mtd\.list' \
		&& no "querying mtd also matched mtd.list - the path component is not being anchored" \
		|| ok "querying mtd does not match mtd.list"
else
	echo "  could not list the archive - refusing to report"
	exit 1
fi

echo "=== flash-sk-d840n.sh -> trial-initramfs.cpio.gz (the kexec'd RAM root) ==="
A=staging/trial/trial-initramfs.cpio.gz
if [ ! -s "$A" ]; then
	no "$A missing - cannot verify the RAM root the flasher runs in"
else
	for t in mtd sha256sum awk head wc cat sync; do
		if has "$A" "$t"; then ok "$t present in the RAM root"; else no "$t MISSING from the RAM root - the flasher would die there"; fi
	done
fi

echo
echo "=== init-flash.sh -> ctl-flash.cpio.gz (built by mkctlram.py --mode flash) ==="
B=staging/ctl/ctl-flash.cpio.gz
if [ ! -s "$B" ]; then
	no "$B missing - build it with: python3 tools/mkctlram.py --mode flash --tag T --write --stage embed \\
	--extra sbin/mtd=staging/ctl/rootfs/mtd --extra sbin/devmem2=staging/ctl/rootfs/devmem2 \\
	--payload p/flash-uImage=staging/trial/flash-uImage --payload p/flash-dtb.bin=staging/trial/flash-dtb.bin"
else
	# These two arrive via --extra, so their absence means flash-round.sh stopped passing them.
	for t in mtd devmem2; do
		if has "$B" "$t"; then ok "sbin/$t injected into the flasher archive"; else no "sbin/$t MISSING - flash-round.sh must pass --extra sbin/$t"; fi
	done
	# init-flash.sh programs with mtd alone; it must not start depending on what is absent here.
	for t in sha256sum awk sync tail; do
		if has "$B" "$t"; then
			printf '     note: %s is present now; init-flash.sh does not use it, so this is harmless\n' "$t"
		fi
	done
	echo "     init-flash.sh uses only mtd/devmem2 here - the other tools are not needed"
fi

echo
echo "=== the gate payload -> ctl-initramfs.cpio.gz ==="
C=staging/ctl/ctl-initramfs.cpio.gz
if [ -s "$C" ]; then
	for t in nc ping logger md5sum hexdump; do
		has "$C" "$t" && ok "gate payload has $t" || no "gate payload lost $t"
	done
else
	echo "     (absent; the gate payload is rebuilt per round - not an error)"
fi

echo
echo "=== the three-way mapping is stated once, so it stops being folklore ==="
printf '  %-22s %-34s %s\n' flash-sk-d840n.sh "trial-initramfs.cpio.gz" "kexec'd RAM root"
printf '  %-22s %-34s %s\n' init-flash.sh "ctl-flash.cpio.gz" "mkctlram.py --mode flash"
printf '  %-22s %-34s %s\n' trial-kexec.sh "the RUNNING system" "runs over ssh; kexecs INTO the RAM root"

echo
echo "=== $pass passed, $miss failed ==="
[ "$miss" -eq 0 ]
