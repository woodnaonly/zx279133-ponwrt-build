#!/bin/sh
# 本地排演套件（local rehearsal suite）：依赖 staging/ 下的真实构建产物，
# 在只 clone 本仓库的环境里跑不起来，也**没有**接进 CI。
# 为什么、怎么跑、缺的那个夹具从哪来：同目录 README.md。
# Exercise staging/trial/flash-sk-d840n.sh for real, against a fake /proc, /sys and mtd.
#
# This is the script that actually erases and programs the board, on a unit with no serial
# console, where a mistake is a brick. Reading it is not enough: the interesting cases are
# what it does when something is not what it assumed, and one of those assumptions is
# sysfs being mounted - which this project has already been bitten by once (the empty
# model/leds/netdevs readings were an unmounted sysfs, not absent hardware).
#
# The relocated copy and the payload images live in the SAME directory, because the script
# resolves its images as ${0%/*}/flash-*; absolute paths are rewritten with sed and the
# body is otherwise untouched. The control case proves the relocation works.
set -u
cd /mnt/c/ugit/SK-D840N4
export LC_ALL=C

REAL=ci-build-repo/templates/flash-sk-d840n.sh
STAGED=staging/trial/flash-sk-d840n.sh
pass=0; fail=0
ok()  { printf '  PASS  %-50s %s\n' "$1" "${2:-}"; pass=$((pass+1)); }
bad() { printf '  FAIL  %-50s %s\n' "$1" "${2:-}"; fail=$((fail+1)); }

W=""
mkmtd() { # $1 = index of the root partition
	W=$(mktemp -d)
	mkdir -p "$W/bin"
	# Indices must be CONTIGUOUS: the flasher's geometry loop stops at the first gap
	# (`while [ -e .../mtd$i ]`), so a hole would silently truncate the listing and the
	# later "mtdN size=" assertions would be measuring the sandbox, not the script.
	{
		printf 'mtd0: 00200000 00020000 "boot"\n'
		printf 'mtd1: 00800000 00020000 "kernel"\n'
		printf 'mtd2: 00100000 00020000 "dtb"\n'
		printf 'mtd3: 00400000 00020000 "parameter tags"\n'
		i=4
		while [ "$i" -lt "$1" ]; do
			printf 'mtd%s: 00010000 00020000 "spare%s"\n' "$i" "$i"
			i=$(( i + 1 ))
		done
		printf 'mtd%s: 0f000000 00020000 "root"\n' "$1"
		printf 'mtd%s: 00100000 00020000 "scratch"\n' "$(( $1 + 1 ))"
	} >"$W/proc-mtd"

	# sysfs mirrors it: one dir per partition with name/size/erasesize
	mkdir -p "$W/class/mtd"
	i=0
	while [ "$i" -le "$(( $1 + 1 ))" ]; do
		line=$(grep "^mtd$i: " "$W/proc-mtd")
		[ -n "$line" ] || { i=$(( i + 1 )); continue; }
		name=${line#*\"}; name=${name%%\"*}
		sizehex=$(printf '%s' "$line" | awk '{print $2}')
		mkdir -p "$W/class/mtd/mtd$i"
		printf '%s' "$name"   >"$W/class/mtd/mtd$i/name"
		printf '%d' "$(( 0x$sizehex ))" >"$W/class/mtd/mtd$i/size"
		printf '%d' "$(( 0x20000 ))"       >"$W/class/mtd/mtd$i/erasesize"
		i=$(( i + 1 ))
	done

	printf 'rootfs /rootfs rw 0 0\ntmpfs /tmp tmpfs rw 0 0\n' >"$W/proc-mounts"

	cat >"$W/bin/mtd" <<'EOF'
#!/bin/sh
echo "mtd $*" >> "$MTD_LOG"
for a in "$@"; do
	if [ "$a" = "$FAIL_ON" ]; then echo "mtd: injected write failure on $a" >&2; exit 1; fi
done
if [ "$1" = verify ] && [ -n "$FAIL_VERIFY" ]; then
	for a in "$@"; do
		[ "$a" = "$FAIL_VERIFY" ] && { echo "mtd: readback differs" >&2; exit 1; }
	done
fi
exit 0
EOF
	chmod +x "$W/bin/mtd"

	# images AND the relocated script in one directory
	cp staging/trial/flash-uImage staging/trial/flash-dtb.bin staging/trial/flash-rootfs.jffs2 "$W/"
	cp staging/trial/flash-sysupgrade.bin "$W/"
	sed -e "s#/proc/mtd#$W/proc-mtd#g" \
	    -e "s#/proc/mounts#$W/proc-mounts#g" \
	    -e "s#/sys/class/mtd#$W/class/mtd#g" \
	    "$REAL" >"$W/flash-sk-d840n.sh"
}

run() { # args...; sets RC and OUT
	: >"$W/mtd.log"
	OUT=$(cd "$W" && MTD_LOG="$W/mtd.log" FAIL_ON="$FAIL_ON" FAIL_VERIFY="$FAIL_VERIFY" \
		PATH="$W/bin:$PATH" FORCE="$FORCE" sh "$W/flash-sk-d840n.sh" "$@" 2>&1)
	RC=$?
}
ncalls() { wc -l <"$W/mtd.log" 2>/dev/null || echo 0; }

echo "=== 0. control: relocation produced a script that still runs ==="
mkmtd 5; FAIL_ON=; FAIL_VERIFY=; FORCE=
# give the sandbox a manifest that matches, so the integrity gate is exercised normally
( cd "$W" && sha256sum flash-uImage flash-dtb.bin flash-rootfs.jffs2 flash-sysupgrade.bin > sha256sums-release )
run
echo "  rc=$RC"
printf '%s\n' "$OUT" | sed 's/^/    /'
echo "  mtd calls (a dry run must make none):"
sed 's/^/      /' "$W/mtd.log" 2>/dev/null
if [ "$RC" -eq 0 ] && printf '%s' "$OUT" | grep -q 'dry run only'; then
	ok "relocated script runs, dry run, rc=0"
else bad "relocated script runs, dry run, rc=0" "rc=$RC"; fi
if [ "$(ncalls)" -eq 0 ]; then ok "a dry run touches nothing" ""; else bad "a dry run touches nothing" "$(ncalls) mtd calls"; fi
if printf '%s' "$OUT" | grep -q 'mtd5 *root *size=251658240'; then
	ok "sandbox geometry is the mainline layout" "mtd5 root 0f000000"
else bad "sandbox geometry is the mainline layout" "$(printf '%s' "$OUT" | tr '\n' '|')"; fi
rm -rf "$W"

echo
echo "=== 1. sysfs NOT mounted (the failure mode this project already hit once) ==="
mkmtd 5; rm -rf "$W/class/mtd"; FAIL_ON=; FAIL_VERIFY=; FORCE=
run --write
echo "  rc=$RC"; printf '%s\n' "$OUT" | sed 's/^/    /'
echo "  mtd calls:"; sed 's/^/      /' "$W/mtd.log"
if [ "$(ncalls)" -eq 0 ]; then ok "no partition touched when sysfs is missing" "fail-safe"
else bad "no partition touched when sysfs is missing" "$(ncalls) mtd calls"; fi
if [ "$RC" -ne 0 ]; then ok "missing sysfs is fatal, not silent" "rc=$RC"
else bad "missing sysfs is fatal, not silent" "rc=0 - would look like success"; fi
if printf '%s' "$OUT" | grep -qi 'sysfs\|class/mtd'; then
	ok "and the message names the cause" "$(printf '%s' "$OUT" | grep -i 'sysfs\|class/mtd' | head -1)"
else bad "and the message names the cause" "operator only gets a raw cat(1) error"; fi
rm -rf "$W"

echo
echo "=== 2. payload larger than its partition ==="
mkmtd 5
i=0; while [ "$i" -le 6 ]; do printf '1024' >"$W/class/mtd/mtd$i/size"; i=$((i+1)); done
FAIL_ON=; FAIL_VERIFY=; FORCE=
run --write
echo "  rc=$RC"; printf '%s\n' "$OUT" | grep -i 'does not fit' | sed 's/^/    /'
if [ "$RC" -ne 0 ] && printf '%s' "$OUT" | grep -q 'does not fit'; then
	ok "oversized payload refused before any write" "rc=$RC"
else bad "oversized payload refused before any write" "rc=$RC"; fi
if [ "$(ncalls)" -eq 0 ]; then ok "and nothing was erased first" ""; else bad "and nothing was erased first" "$(ncalls) mtd calls"; fi
rm -rf "$W"

echo
echo "=== 3. root is not mtd5 - U-Boot panics on a hard-coded index ==="
mkmtd 4; FAIL_ON=; FAIL_VERIFY=; FORCE=
run
echo "  rc=$RC"; printf '%s\n' "$OUT" | grep -i 'MISMATCH' | sed 's/^/    /'
if [ "$RC" -ne 0 ] && printf '%s' "$OUT" | grep -q 'MISMATCH'; then
	ok "wrong root index refuses the whole run" "rc=$RC"
else bad "wrong root index refuses the whole run" "rc=$RC"; fi
echo "  --- FORCE=1, then an actual write"
FORCE=1; run --write
echo "  rc=$RC"; printf '%s\n' "$OUT" | grep -i 'FORCE' | sed 's/^/    /'
if [ "$RC" -eq 0 ]; then ok "FORCE=1 lets it proceed" "rc=0"; else bad "FORCE=1 lets it proceed" "rc=$RC"; fi
rm -rf "$W"

echo
echo "=== 4. refuse to erase the filesystem we are running from ==="
mkmtd 5
printf '/dev/mtdblock5 / jffs2 rw 0 0\n' >"$W/proc-mounts"
FAIL_ON=; FAIL_VERIFY=; FORCE=
run --write
echo "  rc=$RC"; printf '%s\n' "$OUT" | grep -i 'RAM image' | sed 's/^/    /'
if [ "$RC" -ne 0 ] && [ "$(ncalls)" -eq 0 ]; then
	ok "mounted / refused, nothing written" "rc=$RC"
else bad "mounted / refused, nothing written" "rc=$RC"; fi
rm -rf "$W"

echo
echo "=== 5. a failed write stops the sequence ==="
mkmtd 5; FAIL_ON=root; FAIL_VERIFY=; FORCE=
run --write
echo "  rc=$RC"; echo "  mtd calls:"; sed 's/^/      /' "$W/mtd.log"
if [ "$RC" -ne 0 ]; then ok "write failure is fatal" "rc=$RC"; else bad "write failure is fatal" "rc=0"; fi
if grep -q ' dtb$' "$W/mtd.log"; then bad "nothing after a failed root write" "it carried on to dtb"
else ok "nothing after a failed root write" ""; fi
rm -rf "$W"

echo
echo "=== 6. a failed verify stops the sequence ==="
mkmtd 5; FAIL_ON=; FAIL_VERIFY=root; FORCE=
run --write
echo "  rc=$RC"; echo "  mtd calls:"; sed 's/^/      /' "$W/mtd.log"
if [ "$RC" -ne 0 ] && printf '%s' "$OUT" | grep -q 'readback differs'; then
	ok "verify failure is fatal and named" "rc=$RC"
else bad "verify failure is fatal and named" "rc=$RC"; fi
if grep -q ' dtb$' "$W/mtd.log"; then bad "nothing after a failed verify" "it carried on to dtb"
else ok "nothing after a failed verify" ""; fi
rm -rf "$W"

echo
echo "=== 7. happy path: three partitions, kernel last ==="
mkmtd 5; FAIL_ON=; FAIL_VERIFY=; FORCE=
run --write
echo "  rc=$RC"; echo "  mtd calls:"; sed 's/^/      /' "$W/mtd.log"
order=$(grep '^mtd erase' "$W/mtd.log" | awk '{print $3}' | tr '\n' ' ')
if [ "$order" = "root dtb kernel " ]; then ok "erase order is root, dtb, kernel" "$order"
else bad "erase order is root, dtb, kernel" "got: $order"; fi
if [ "$RC" -eq 0 ]; then ok "happy path rc=0" ""; else bad "happy path rc=0" "rc=$RC"; fi
rm -rf "$W"

echo
echo "=== 8. image integrity: mtd verify cannot do this job ==="
# `mtd verify` only proves what was just written reads back, so it confirms garbage and
# does not notice a truncation (both sides end up the same length). Only a manifest that
# travelled with the images can catch that, and the flasher has to use it.

echo "  --- 8a. manifest matches -> the run proceeds"
mkmtd 5; FAIL_ON=; FAIL_VERIFY=; FORCE=
( cd "$W" && sha256sum flash-uImage flash-dtb.bin flash-rootfs.jffs2 flash-sysupgrade.bin > sha256sums-release )
run --write
echo "    rc=$RC"
if [ "$RC" -eq 0 ] && printf '%s' "$OUT" | grep -q 'verifying images against sha256sums-release'; then
	ok "matching manifest is verified and the run proceeds" "rc=0"
else bad "matching manifest is verified and the run proceeds" "rc=$RC"; fi
if [ "$(ncalls)" -gt 0 ]; then ok "and the three partitions were written" "$(ncalls) mtd calls"
else bad "and the three partitions were written" "none"; fi
rm -rf "$W"

echo "  --- 8b. a corrupted image -> refused before any erase"
mkmtd 5; FAIL_ON=; FAIL_VERIFY=; FORCE=
( cd "$W" && sha256sum flash-uImage flash-dtb.bin flash-rootfs.jffs2 flash-sysupgrade.bin > sha256sums-release )
printf 'CORRUPT' >>"$W/flash-dtb.bin"
run --write
echo "    rc=$RC"; printf '%s\n' "$OUT" | grep -i 'mismatch\|checksum' | sed 's/^/      /'
if [ "$RC" -ne 0 ] && printf '%s' "$OUT" | grep -qi 'mismatch'; then
	ok "corrupted image is refused" "rc=$RC"
else bad "corrupted image is refused" "rc=$RC"; fi
if [ "$(ncalls)" -eq 0 ]; then ok "and not one eraseblock was touched" "fail-safe"
else bad "and not one eraseblock was touched" "$(ncalls) mtd calls"; fi
rm -rf "$W"

echo "  --- 8c. a truncated image -> also refused"
mkmtd 5; FAIL_ON=; FAIL_VERIFY=; FORCE=
( cd "$W" && sha256sum flash-uImage flash-dtb.bin flash-rootfs.jffs2 flash-sysupgrade.bin > sha256sums-release )
# a short copy still fits its partition, so the size check cannot see it
dd if=staging/trial/flash-dtb.bin of="$W/flash-dtb.bin" bs=1024 count=4 2>/dev/null
run --write
echo "    rc=$RC"
if [ "$RC" -ne 0 ]; then ok "truncated image is refused" "rc=$RC"
else bad "truncated image is refused" "rc=0 - the size check passes because it still fits"; fi
rm -rf "$W"

echo "  --- 8d. no manifest at all -> announced, not silently skipped"
mkmtd 5; FAIL_ON=; FAIL_VERIFY=; FORCE=
run --write
echo "    rc=$RC"; printf '%s\n' "$OUT" | grep -i 'UNVERIFIED\|WARNING' | sed 's/^/      /'
if printf '%s' "$OUT" | grep -q 'UNVERIFIED'; then
	ok "a missing manifest is announced" ""
else bad "a missing manifest is announced" "silent - operator assumes it was checked"; fi
if [ "$RC" -eq 0 ]; then ok "and the run still proceeds (an older release must keep working)" "rc=0"
else bad "and the run still proceeds" "rc=$RC"; fi
rm -rf "$W"

echo
echo "=== 9. the shipped manifest matches the shipped files ==="
if (cd staging/trial && sha256sum -c sha256sums-release >/dev/null 2>&1); then
	ok "sha256sums-release verifies against staging/trial" "4/4"
else
	bad "sha256sums-release verifies against staging/trial" "manifest does not match"
fi

echo
echo "=== 10. the rollback advice the flasher prints must be true ==="
# This line used to tell the operator "restore_set/ in this repo holds the verified
# stock set for a full rollback". It does not: restore_set/root_stock.bin is ZTE
# firmware image data ("ZXIC 133 UNI V2.0.0"), carries no filesystem magic in its
# 241 MB, and no tool here ever writes root. Assert both halves - that the false
# claim is gone, and that the replacement names the command that actually works.
mkmtd 5; FAIL_ON=; FAIL_VERIFY=; FORCE=
run --write
if printf '%s' "$OUT" | grep -q 'verified stock set'; then
	bad "the false restore_set claim is gone" "still printed"
else
	ok "the false restore_set claim is gone" ""
fi
if printf '%s' "$OUT" | grep -q 'flash-direct.sh write'; then
	ok "and the real rollback command is named" ""
else
	bad "the real rollback command is named" "operator is left without a way back"
fi
if printf '%s' "$OUT" | grep -q 'Do NOT rely on restore_set'; then
	ok "and the restore_set trap is called out explicitly" ""
else
	bad "the restore_set trap is called out" ""
fi
rm -rf "$W"

echo
echo "=== 11. restore_set/ really is not a rollback set (re-check, not a comment) ==="
R=restore_set/root_stock.bin
if [ -f "$R" ]; then
	hits=$(grep -a -c -o $'\x85\x19' "$R" 2>/dev/null); hits=${hits:-0}
	if [ "$hits" -eq 0 ]; then
		ok "root_stock.bin has no jffs2 node magic" "not a filesystem image"
	else
		note "root_stock.bin does contain jffs2 magic" "$hits lines - re-evaluate the claim"
	fi
	# the ZTE firmware banner is what makes it image data rather than a rootfs
	if head -c 26214400 "$R" | tr -c '[:print:]' '\n' | grep -q 'ZXIC 133 UNI'; then
		ok "and it carries the ZTE firmware banner" "'ZXIC 133 UNI V2.0.0 ... CM OSGI'"
	else
		note "no ZTE banner found" "identity unconfirmed - re-check"
	fi
else
	note "restore_set/root_stock.bin absent" ""
fi

echo
echo "=== 12. template vs the copy inside the downloaded release ==="
if cmp -s "$REAL" "$STAGED"; then
	ok "staged flash-sk-d840n.sh matches the template" "same bytes"
else
	printf '  note  template and the 2026-10-12 release copy differ\n'
	printf '        template: %s\n' "$(sha256sum "$REAL" | cut -c1-16)"
	printf '        staged  : %s\n' "$(sha256sum "$STAGED" | cut -c1-16)"
	printf '        expected: the staged one is a release artifact and stays as it is;\n'
	printf '        this test measures the TEMPLATE, which is what CI ships next.\n'
fi

echo
echo "=== pass=$pass fail=$fail ==="
[ "$fail" -eq 0 ] || exit 1
exit 0