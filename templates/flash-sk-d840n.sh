#!/bin/sh
# Write the mainline SK-D840N triple from the system that is running now.
# Target map (the community U-Boot that is already flashed on this board):
#   mtd1 boot / mtd2 kernel / mtd3 dtb / mtd4 "parameter tags" / mtd5 root
# mtd1 is never touched, so the bootloader and its env stay intact.
#
# Order: root, dtb, then kernel LAST - until the kernel partition is replaced the
# box still boots what it is booting now, so every earlier mistake is recoverable
# from the live shell.
#
#   sh flash-sk-d840n.sh            # dry run: geometry + payload sizes only
#   sh flash-sk-d840n.sh --write    # erase + program + verify
set -eu

D=${0%/*}
[ "$D" = "$0" ] && D=.
UIMAGE=$D/flash-uImage
DTB=$D/flash-dtb.bin
ROOTFS=$D/flash-rootfs.jffs2
PAGE=2048
MODE=$1
TMP=/tmp/skd840n

say() { echo "flasher: $*"; }
die() { echo "flasher: $*" >&2; exit 1; }

for f in "$UIMAGE" "$DTB" "$ROOTFS"; do
	[ -f "$f" ] || die "missing $f (run this script from the directory holding the images)"
done
command -v mtd_debug >/dev/null 2>&1 || die "mtd_debug not found on the device"
for p in kernel dtb root; do
	grep -q "\"$p\"\|	$p\|^ *[0-9]*:.*\"$p\"" /proc/mtd || die "partition $p missing in /proc/mtd"
done

part_of() { # label -> mtd index (/proc/mtd line 1 is the header, line 2 is mtd0)
	line=$(grep -n "\"$1\"" /proc/mtd | head -1 | cut -d: -f1)
	[ -n "$line" ] || return 1
	echo $((line - 2))
}

pad_to_page() { # src dst : append 0xFF up to a page boundary like nand write does
	src=$1
	dst=$2
	len=$(wc -c < "$src")
	rem=$((len % PAGE))
	cp "$src" "$dst"
	if [ "$rem" != 0 ]; then
		dd if=/dev/zero bs=1 count=$((PAGE - rem)) 2>/dev/null | tr '\0' '\377' >> "$dst"
	fi
}

show_geometry() {
	i=0
	while [ -e "/sys/class/mtd/mtd$i" ]; do
		printf 'mtd%d %-16s offset=%-9s size=%-11s erase=%s\n' "$i" \
			"$(cat /sys/class/mtd/mtd$i/name)" "$(cat /sys/class/mtd/mtd$i/offset)" \
			"$(cat /sys/class/mtd/mtd$i/size)" "$(cat /sys/class/mtd/mtd$i/erasesize)"
		i=$((i + 1))
	done
	for f in "$UIMAGE" "$DTB" "$ROOTFS"; do
		printf '%10d  %s\n' "$(wc -c < "$f")" "$f"
	done
}

show_geometry

[ "$MODE" = "--write" ] || { say "dry run only; pass --write to program flash"; exit 0; }

mkdir -p "$TMP"
for mnt in /overlay /www; do
	mountpoint -q "$mnt" 2>/dev/null && say "warning: $mnt is mounted (jffs2 in active use)"
done

pad_to_page "$ROOTFS" "$TMP/rootfs.jffs2"
pad_to_page "$DTB"    "$TMP/dtb.bin"
pad_to_page "$UIMAGE" "$TMP/uImage"

write_one() { # label file
	label=$1
	src=$2
	dev=$(part_of "$label") || die "cannot resolve mtd index for $label"
	base=$(cat "/sys/class/mtd/mtd$dev/offset")
	size=$(cat "/sys/class/mtd/mtd$dev/size")
	len=$(wc -c < "$src")
	[ "$len" -le "$size" ] || die "$label: $len bytes does not fit in $size"
	[ $((len % PAGE)) -eq 0 ] || die "$label: payload not page aligned"
	[ $((base % PAGE)) -eq 0 ] || die "$label: partition offset not page aligned"
	say "erase mtd$dev ($label) @0x$(printf %x "$base") len=$len ; write $src"
	mtd_debug erase "/dev/mtd$dev" "$base" "$len"
	mtd_debug write "/dev/mtd$dev" "$base" "$len" "$src"
	sync
	cp "$src" "$TMP/head.bin"
	mtd_debug read "/dev/mtd$dev" "$base" "$PAGE" "$TMP/rb_$dev.bin"
	cmp -n "$PAGE" "$TMP/head.bin" "$TMP/rb_$dev.bin" \
		|| die "$label: readback of the first page differs"
	say "$label verified (first page)"
}

write_one root   "$TMP/rootfs.jffs2"
write_one dtb    "$TMP/dtb.bin"
write_one kernel "$TMP/uImage"

say "done. Do NOT reboot blindly: this kernel has not run on this board yet."
say "Trial it first (initramfs .itb over kexec/serial) and keep restore_set/ for rollback."
