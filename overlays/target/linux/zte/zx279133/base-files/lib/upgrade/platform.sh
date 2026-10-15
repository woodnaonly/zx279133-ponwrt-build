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

# Where the saved configuration archive is, echoed for `mtd -j`; nothing if there is none.
#
# sbin/sysupgrade writes it to CONF_TAR=/tmp/sysupgrade.tgz (sbin/sysupgrade:8) and hands
# that path to the platform. stage2 gets there first and carries the old /tmp into the
# ramfs with `mount -o noatime,move`, so the archive is still present when do_stage2 runs
# platform_do_upgrade. The tree's own nand platform reads exactly this path -
# nand.sh:429, `local conf_tar="/tmp/sysupgrade.tgz"` - which is the idiom followed here.
#
# It is deliberately NOT gated on $UPGRADE_BACKUP. That name has 50 references in this
# tree and zero assignments: sysupgrade, stage2 and do_stage2 never set it, and the
# literal string is absent from procd's upgraded binary, which is what carries the json.
# A gate on it therefore never opens, `mtd -j` never runs, and the saved config is dropped
# on every upgrade while platform_do_upgrade still returns 0 - a silent wipe of whatever
# /etc/config/network held, including the 192.168.9.1 that /etc/board.d/02_network sets.
# UPGRADE_BACKUP is still honoured if some caller ever does set it.
skd840n_conf_tar() {
	if [ -n "$UPGRADE_BACKUP" ] && [ -f "$UPGRADE_BACKUP" ]; then
		echo "$UPGRADE_BACKUP"
	elif [ -f /tmp/sysupgrade.tgz ]; then
		echo /tmp/sysupgrade.tgz
	fi
}

# tar's -z flag, decided by looking at the file rather than by assuming.
#
# scripts/sysupgrade-tar.sh in this tree builds the image with
#   tar --sort=name ... -cvf sysupgrade.tar sysupgrade-<board>
# - no -z, so the artifact is an UNCOMPRESSED tar. Reading it with `tar tzf` fails with
# "not in gzip format", and platform_check_image then refuses every image this build
# produces - verified against the real flash-sysupgrade.bin, whose first two bytes are 73 79
# ("sy") and whose ustar magic sits at offset 257, i.e. a plain tar.
#
# hexdump, not od. The first version of this used `head -c 2 | od -An -tx1`, which cannot work
# on this board: od is compiled into busybox but no /bin/od, /usr/bin/od or /sbin/od symlink is
# shipped, so it is not on PATH, and `2>/dev/null` turned that into a silent empty result - the
# function returned "" for every input, which is indistinguishable from "not gzipped" and is
# the right answer only by accident. od is also absent from the list stage2 copies into the
# upgrade RAM root (stage2:42: md5sum hexdump cat zcat dd tar gzip). hexdump is present at
# /usr/bin/hexdump, is on that list, and is what the tree itself reads magic with -
# common.sh:89 get_image, common.sh:111 get_magic_word, common.sh:115 get_magic_long, all
# `dd | hexdump -v -n N -e '1/1 "%02x"'`.
skd840n_tar_z() { # file; echoes "z" for gzip, nothing for a plain tar
	local magic
	magic="$(dd if="$1" bs=2 count=1 2>/dev/null | hexdump -v -n 2 -e '1/1 "%02x"')"
	case "$magic" in
	1f8b*) echo z ;;
	esac
}

platform_check_image() {
	[ "$#" -gt 1 ] && return 1
	local file="$1"
	local z

	z=$(skd840n_tar_z "$file")
	tar t${z}f "$file" 2>/dev/null | grep -q 'sysupgrade-[^/]*/kernel' || {
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
	local file="$1" dir sub conf_tar
	local z

	z=$(skd840n_tar_z "$file")
	dir=$(mktemp -d) || return 1
	tar x${z}f "$file" -C "$dir" || { rm -rf "$dir"; return 1; }
	sub=$(find "$dir" -maxdepth 1 -type d -name 'sysupgrade-*' | head -1)
	if [ -z "$sub" ]; then
		v "No sysupgrade-<board> directory in image"
		rm -rf "$dir"
		return 1
	fi

	if [ -f "$sub/root" ]; then
		# -j gives the mtd tool the saved config archive so it is appended to the
		# freshly written jffs2 instead of being lost with the old filesystem.
		conf_tar=$(skd840n_conf_tar)
		if [ -n "$conf_tar" ]; then
			mtd -j "$conf_tar" write "$sub/root" root || { rm -rf "$dir"; return 1; }
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
