#!/bin/sh
# Program the mainline SK-D840N triple (uImage + dtb + jffs2 rootfs) into the
# partition map the installed community U-Boot expects:
#   boot / kernel / dtb / "parameter tags" / root
# `boot` is never touched, so the bootloader and its env stay intact.
#
# Tooling: this uses /sbin/mtd (OpenWrt base), which takes a partition *label* and
# does erase/write/verify through the mtd char device. mtd_debug and mtd-utils are
# not in the image on this board, so they are not assumed here.
#
# Run it from the kexec'd RAM system (templates/trial-kexec.sh), NOT from the
# mounted OpenWrt root: `root` is the jffs2 your own / is mounted from, and
# erasing the flash under a mounted jffs2 oopses the kernel.
#
#   sh flash-sk-d840n.sh            # dry run: geometry, payload sizes, boot contract
#   sh flash-sk-d840n.sh --write    # erase + program + verify, kernel LAST
set -eu

D=${0%/*}
[ "$D" = "$0" ] && D=.
KERNEL_IMG=$D/flash-uImage
DTB_IMG=$D/flash-dtb.bin
ROOT_IMG=$D/flash-rootfs.jffs2
MODE=${1:-}

say() { echo "flasher: $*"; }
die() { echo "flasher: $*" >&2; exit 1; }

command -v mtd >/dev/null 2>&1 || die "/sbin/mtd not found - run this from the OpenWrt image"
for f in "$KERNEL_IMG" "$DTB_IMG" "$ROOT_IMG"; do
	[ -f "$f" ] || die "missing $f (run from the directory holding the images)"
done

mtd_of() { # label -> index, from the quoted name in /proc/mtd
	awk -F'"' -v n="$1" '$2==n { s=$1; sub(/^mtd/,"",s); sub(/:.*/,"",s); print s }' \
		/proc/mtd | head -1
}

geometry() {
	i=0
	while [ -e "/sys/class/mtd/mtd$i" ]; do
		printf 'mtd%s %-16s size=%-11s erase=%s\n' "$i" \
			"$(cat "/sys/class/mtd/mtd$i/name")" \
			"$(cat "/sys/class/mtd/mtd$i/size")" \
			"$(cat "/sys/class/mtd/mtd$i/erasesize")"
		i=$((i + 1))
	done
	for f in "$KERNEL_IMG" "$DTB_IMG" "$ROOT_IMG"; do
		printf '%11d  %s\n' "$(wc -c < "$f")" "$f"
	done
}

geometry

# The installed U-Boot boots with `root=/dev/mtdblock5`, which is a hard-coded
# index, not a label. If this kernel numbers the partitions differently the box
# will panic on mount with no serial console to see it, so check first.
ROOTIDX=$(mtd_of root)
[ -n "$ROOTIDX" ] || die "no 'root' partition in /proc/mtd"
if [ "$ROOTIDX" != 5 ]; then
	say "MISMATCH: root is mtd$ROOTIDX, but bootargs say root=/dev/mtdblock5"
	say "Fix the partition list in the board dts (or the env bootargs) before flashing."
	[ "${FORCE:-}" = 1 ] || exit 1
	say "FORCE=1 set, continuing anyway"
fi

# Do not saw off the root we are sitting on.
for m in / /overlay; do
	dev=$(awk -v m="$m" '$2==m {print $1}' /proc/mounts)
	case "$dev" in
	/dev/mtdblock*|/dev/root) die "$m is mounted from $dev - boot the RAM image first" ;;
	esac
done

[ "$MODE" = "--write" ] || { say "dry run only; pass --write to program flash"; exit 0; }
say "writing. Order is root, dtb, then kernel - until kernel is replaced the box"
say "still boots what it boots now, so any earlier mistake is recoverable."

program() { # label file
	label=$1
	src=$2
	idx=$(mtd_of "$label")
	[ -n "$idx" ] || die "no partition labelled $label"
	size=$(cat "/sys/class/mtd/mtd$idx/size")
	len=$(wc -c < "$src")
	[ "$len" -le "$size" ] || die "$label: $len bytes does not fit in $size"
	say "erase mtd$idx ($label)"
	mtd erase "$label"
	say "write $src -> $label"
	mtd write "$src" "$label"
	say "verify $label"
	mtd verify "$src" "$label" || die "$label: readback differs"
	say "$label ok"
}

program root   "$ROOT_IMG"
program dtb    "$DTB_IMG"
program kernel "$KERNEL_IMG"

sync
say "done. Reboot into it (or power-cycle). If it does not come back on the LAN,"
say "restore_set/ in this repo holds the verified stock set for a full rollback."
