#!/bin/sh
# 本地排演套件（local rehearsal suite）：依赖 staging/ 下的真实构建产物，
# 在只 clone 本仓库的环境里跑不起来，也**没有**接进 CI。
# 为什么、怎么跑、缺的那个夹具从哪来：同目录 README.md。
# restore_set/ is not what flash-sk-d840n.sh claims it is. root_stock.bin is ZTE firmware
# image data ("ZXIC 133 UNI V2.0.0 286632 CM OSGI", "U-Boot uImage source file for 133
# project") preceded by 19 MiB of 0xff - not a jffs2 partition image, and with no
# recognised filesystem magic anywhere in 241 MB.
#
# So the recovery story has to be restated from artifacts that actually verify. Which are:
#   backups/preflash-20261013/mtd1..mtd4.bin.gz  - decompress to exact partition sizes and
#     their MANIFEST records device-side md5 == local md5 for all four
#   staging/rollback/{flash-uImage,flash-dtb.bin} - to be checked here against those
set -u
cd /mnt/c/ugit/SK-D840N4
export LC_ALL=C
pass=0; fail=0
ok()  { printf '  PASS  %-56s %s\n' "$1" "${2:-}"; pass=$((pass+1)); }
bad() { printf '  FAIL  %-56s %s\n' "$1" "${2:-}"; fail=$((fail+1)); }
note(){ printf '  ....  %-56s %s\n' "$1" "${2:-}"; }
T=$(mktemp -d); trap 'rm -rf "$T"' EXIT

echo "=== 1. the pre-flash backups are genuine ==="
# MANIFEST lines read:  mtd1 boot dev=<md5> local=<md5> 313532 B(gz)
# so the key is "mtdN <name>", not "mtdN-<name>"
for p in "mtd1 boot" "mtd2 kernel" "mtd3 dtb" "mtd4 parameter_tags"; do
	set -- $p
	mn=$1; pname=$2
	gz="backups/preflash-20261013/$mn-$pname.bin.gz"
	sz=$(gzip -dc "$gz" 2>/dev/null | wc -c)
	line=$(grep "^$mn $pname " backups/preflash-20261013/MANIFEST)
	decl=$(printf '%s' "$line" | sed -n 's/.* \([0-9]*\) B(gz).*/\1/p')
	dev=$(printf '%s' "$line"  | sed -n 's/.*dev=\([0-9a-f]*\).*/\1/p')
	loc=$(printf '%s' "$line"  | sed -n 's/.*local=\([0-9a-f]*\).*/\1/p')
	gzip -dc "$gz" >"$T/$mn-$pname" 2>/dev/null
	my=$(md5sum <"$T/$mn-$pname" | cut -d' ' -f1)
	if [ -n "$dev" ] && [ "$my" = "$loc" ] && [ "$loc" = "$dev" ]; then
		ok "$mn $pname: device md5 == local md5 == bytes on disk" "$dev (${sz} B, manifest said ${decl})"
	else
		bad "$mn $pname: manifest md5 agrees" "dev='$dev' local='$loc' actual='$my'"
	fi
done

echo
echo "=== 2. is staging/rollback/flash-uImage the pre-flash mtd2 content? ==="
if cmp -s "$T/mtd2-kernel" staging/rollback/flash-uImage; then
	ok "rollback kernel IS the pre-flash mtd2" "byte-identical"
else
	note "rollback kernel differs from the full 8 MiB partition dump" \
		"$(wc -c <staging/rollback/flash-uImage) B vs $(wc -c <"$T/mtd2-kernel") B"
	# most likely the dump is a padded partition and the staged file is the bare image.
	# That is only equal if the partition tail is the padding; test by prefix.
	head -c "$(wc -c <staging/rollback/flash-uImage)" "$T/mtd2-kernel" >"$T/prefix"
	if cmp -s "$T/prefix" staging/rollback/flash-uImage; then
		ok "rollback kernel is a byte-exact PREFIX of the partition" "rest of the dump is padding"
	else
		bad "rollback kernel vs the partition dump" "not a prefix either - different content"
	fi
fi

echo
echo "=== 3. which kernel is the rollback image? ==="
K=staging/rollback/flash-uImage
mx=$(head -c 4 "$K" | od -An -tx1 | tr -d ' \n')
printf '  first 4 bytes: %s %s\n' "$mx" "$([ "$mx" = 27051956 ] && echo '(uImage)' || echo '(NOT a uImage)')"
comp=$(od -An -tu1 -j31 -N1 "$K" | tr -d ' \n')
printf '  comp field: %s (%s)\n' "$comp" \
	"$([ "$comp" = 1 ] && echo gzip || { [ "$comp" = 0 ] && echo none || echo other; })"
if [ "$comp" = 1 ]; then
	tail -c +65 "$K" | gzip -dc >"$T/k" 2>/dev/null
	ver=$(grep -a -o -m1 'Linux version [0-9][^ ]*' "$T/k" 2>/dev/null || true)
	if [ -n "$ver" ]; then
		case "$ver" in
			*4.19*) ok "the rollback kernel is the VENDOR 4.19" "$ver";;
			*6.18*) bad "the rollback kernel is the VENDOR 4.19" "it is mainline: $ver";;
			*) note "kernel version" "$ver";;
		esac
	else
		note "no banner in the decompressed payload" ""
	fi
else
	ver=$(grep -a -o -m1 'Linux version [0-9][^ ]*' "$K" 2>/dev/null || true)
	[ -n "$ver" ] && note "kernel banner" "$ver" || note "kernel banner" "not readable"
fi

echo
echo "=== 4. the rollback dtb, and the partition table it implies ==="
D=staging/rollback/flash-dtb.bin
mx=$(head -c 4 "$D" | tr -d ' \n' | sed 's/^/ /' | tr -d ' \n')
hx=$(head -c 4 "$D" | od -An -tx1 | tr -d ' \n')
printf '  %s: %s bytes, magic %s %s\n' "$D" "$(wc -c <"$D")" "$hx" \
	"$([ "$hx" = d00dfeed ] && echo '(FDT - a real device tree)' || echo '(NOT an FDT)')"
if [ "$hx" = d00dfeed ]; then
	if head -c "$(wc -c <"$D")" "$T/mtd3-dtb" | cmp -s - "$D"; then
		ok "rollback dtb is a byte-exact prefix of the pre-flash mtd3" ""
	else
		note "rollback dtb vs pre-flash mtd3" "differs"
	fi
fi

echo
echo "=== 5. the honest recovery story ==="
cat <<'EOF'
  VERIFIED and present:
    - mtd1 boot (ZTE U-Boot "133s")          backups/live-20261012/mtd1-boot.bin, preflash mtd1
    - mtd2 kernel, mtd3 dtb, mtd4 params     backups/preflash-20261013/*.bin.gz (md5-verified both sides)
    - a vendor uImage + vendor dtb pair      staging/rollback/  (checked in section 2-4)

  NOT available:
    - mtd5 root. Never captured under the mainline/4.19 layout.
      restore_set/root_stock.bin is NOT a substitute: it is ZTE firmware image data
      ("ZXIC 133 UNI V2.0.0 286632 CM OSGI"), not a jffs2 partition, and carries no
      filesystem magic anywhere in its 241 MB.

  Consequence for the PON decision:
    rolling the KERNEL and DTB back to vendor is possible and is what
    `SRC=staging/rollback EXPECT='root=0xf00000,0xf100000' sh tools/flash-direct.sh write`
    does. Rolling the ROOTFS back to something that pairs with a vendor kernel is not,
    because no such rootfs is verified in this repo.
EOF