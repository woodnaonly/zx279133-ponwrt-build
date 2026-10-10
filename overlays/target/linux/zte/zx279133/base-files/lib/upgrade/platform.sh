# SPDX-License-Identifier: GPL-2.0-only

. /lib/functions.sh

# Skyworth SK-D840N under the community U-Boot that is already flashed:
#   bootcmd = mtd read kernel ...; mtd read dtb ...; bootm  -  with
#   bootargs ... root=/dev/mtdblock5 rootfstype=jffs2
# so the three things a sysupgrade has to replace are the uImage in `kernel`, the
# bare dtb in `dtb` and the jffs2 filesystem in `root`. ImmortalWrt's
# scripts/sysupgrade-tar.sh puts exactly those into sysupgrade-<board>/{kernel,
# dtb,root}, and do_stage2 runs this from /tmp/root, so writing the partition that
# normally holds / is safe here.
#
# Order is deliberate: root first, kernel LAST. Until the kernel partition is
# replaced the board still boots the kernel it booted before, and on a unit with
# no serial console that window is the only recovery there is.

skd840n_has_part() { # label
	grep -q "\"$1\"" /proc/mtd
}

platform_check_image() {
	[ "$#" -gt 1 ] && return 1
	local file="$1"

	tar tzf "$file" 2>/dev/null | grep -q 'sysupgrade-[^/]*/kernel' || {
		v "Invalid image: not a sysupgrade tar with a kernel member"
		return 1
	}
	skd840n_has_part kernel && skd840n_has_part root || {
		v "This kernel exposes no kernel/root mtd partitions - wrong board?"
		return 1
	}
	return 0
}

platform_do_upgrade() {
	local file="$1" dir sub
	dir=$(mktemp -d) || return 1
	tar xzf "$file" -C "$dir" || { rm -rf "$dir"; return 1; }
	sub=$(find "$dir" -maxdepth 1 -type d -name 'sysupgrade-*' | head -1)
	if [ -z "$sub" ]; then
		v "No sysupgrade-<board> directory in image"
		rm -rf "$dir"
		return 1
	fi

	if [ -f "$sub/root" ]; then
		# -j gives the mtd tool the saved config archive so it is appended to the
		# freshly written jffs2 instead of being lost with the old filesystem.
		if [ -n "$UPGRADE_BACKUP" ]; then
			mtd -j "$UPGRADE_BACKUP" write "$sub/root" root || { rm -rf "$dir"; return 1; }
		else
			mtd write "$sub/root" root || { rm -rf "$dir"; return 1; }
		fi
	fi
	if [ -f "$sub/dtb" ]; then
		mtd write "$sub/dtb" dtb || { rm -rf "$dir"; return 1; }
	fi
	if [ -f "$sub/kernel" ]; then
		mtd write "$sub/kernel" kernel || { rm -rf "$dir"; return 1; }
	fi

	sync
	rm -rf "$dir"
	return 0
}
