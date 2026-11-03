# SPDX-License-Identifier: GPL-2.0-only

. /lib/functions.sh

# stage2 copies awk/wc/tar itself, but not the mktemp used by both preflights.
RAMFS_COPY_BIN="${RAMFS_COPY_BIN:-} mktemp /usr/sbin/sk-layout-check"

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

skd840n_part_size() { # label -> size in bytes, from the running kernel
	local index size
	index=$(awk -F'"' -v name="$1" '$2 == name {
		sub(/^mtd/, "", $1); sub(/:.*/, "", $1); print $1
	}' /proc/mtd)
	case "$index" in ''|*[!0-9]*) return 1 ;; esac
	size=$(cat "/sys/class/mtd/mtd$index/size" 2>/dev/null) || return 1
	case "$size" in ''|*[!0-9]*|0) return 1 ;; esac
	printf '%s\n' "$size"
}

# Read the actual aliases/geometry, not just a label or the root capacity alone.
# Unknown layouts fail closed. Layout migration is a separate DTB-only procedure.
skd840n_live_layout() {
	local root_size count index label offset size actual value field expected layout
	root_size=$(cat /sys/class/mtd/mtd5/size 2>/dev/null) || return 1
	case "$root_size" in
	251658240) layout=current240; count=6 ;;
	252706816) layout=native241; count=5 ;;
	*) return 1 ;;
	esac
	[ "$(awk 'NR>1 && $1 != "mtd0:" {n++} END {print n+0}' /proc/mtd)" = "$count" ] || return 1
	for index in 1 2 3 4 5 6; do
		[ "$index" -le "$count" ] || break
		case "$index" in
		1) label=boot; offset=0; size=2097152 ;;
		2) label=kernel; offset=2097152; size=8388608 ;;
		3) label=dtb; offset=10485760; size=1048576 ;;
		4) label='parameter tags'; offset=11534336; size=4194304 ;;
		5) label=root; offset=15728640; size=$root_size ;;
		6) label=scratch; offset=267386880; size=1048576 ;;
		esac
		actual=$(awk -F'"' -v name="$label" '$2 == name {
			sub(/^mtd/, "", $1); sub(/:.*/, "", $1); id=$1; n++
		} END {if (n != 1) exit 1; print id}' /proc/mtd) || return 1
		[ "$actual" = "$index" ] || return 1
		[ "$(cat "/sys/class/mtd/mtd$index/name" 2>/dev/null)" = "$label" ] || return 1
		for field in offset size erasesize; do
			case "$field" in offset) expected=$offset ;; size) expected=$size ;; erasesize) expected=131072 ;; esac
			value=$(cat "/sys/class/mtd/mtd$index/$field" 2>/dev/null) || return 1
			[ "$value" = "$expected" ] || return 1
		done
	done
	printf '%s\n' "$layout"
}

skd840n_validate_layout() { # extracted dtb; required again in upgrade ramfs
	local image_layout live_layout
	[ -x /usr/sbin/sk-layout-check ] || {
		v "Invalid image: required sk-layout-check is missing; no writes permitted"
		return 1
	}
	image_layout=$(/usr/sbin/sk-layout-check "$1") || {
		v "Invalid image: unsupported or corrupt DTB layout"
		return 1
	}
	live_layout=$(skd840n_live_layout) || {
		v "Invalid image: live MTD layout is unknown"
		return 1
	}
	case "$image_layout:$live_layout" in
	current240:current240|native241:native241) return 0 ;;
	*) v "Invalid image: layout change $live_layout -> $image_layout requires a separate migration"; return 1 ;;
	esac
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

# Check ALL members before root (or any other partition) can be erased.
# Called both from the preflight and again inside the upgrade RAM root.
skd840n_validate_parts() { # directory containing kernel, dtb and root
	local part size capacity
	for part in kernel dtb root; do
		capacity=$(skd840n_part_size "$part") || {
			v "Invalid image: $part partition capacity is unavailable"
			return 1
		}
		size=0
		if [ ! -f "$1/$part" ] || [ -L "$1/$part" ]; then
			v "Invalid image: $part size=$size capacity=$capacity: not a regular file"
			return 1
		fi
		size=$(wc -c < "$1/$part") || return 1
		if [ "$size" -eq 0 ] || [ "$size" -gt "$capacity" ]; then
			v "Invalid image: $part size=$size capacity=$capacity: empty or oversized"
			return 1
		fi
	done
	skd840n_validate_layout "$1/dtb"
}

# Stream only expected regular members to fixed temporary filenames. Never
# extract arbitrary archive paths or links into the upgrade RAM filesystem.
skd840n_unpack_image() { # image, temporary directory
	local file="$1" dir="$2" z listing member part
	local prefix=sysupgrade-skyworth_sk-d840n
	z=$(skd840n_tar_z "$file")
	listing=$(tar t${z}f "$file" 2>/dev/null) || {
		v "Invalid image: cannot list sysupgrade tar"
		return 1
	}
	for member in $listing; do
		case "$member" in
		"$prefix"|"$prefix/"|"$prefix/CONTROL"|"$prefix/kernel"|"$prefix/dtb"|"$prefix/root") ;;
		*) v "Invalid image: unexpected archive member $member"; return 1 ;;
		esac
	done
	# Both BusyBox and GNU tar put the file type first in verbose listings.
	if tar t${z}vf "$file" 2>/dev/null | grep -q '^[^d-]'; then
		v "Invalid image: archive contains a link or special file"
		return 1
	fi
	for part in CONTROL kernel dtb root; do
		if [ "$(printf '%s\n' "$listing" | grep -Fxc "$prefix/$part")" != 1 ]; then
			v "Invalid image: missing or duplicate $part member"
			return 1
		fi
		tar x${z}Of "$file" "$prefix/$part" > "$dir/$part" || {
			v "Invalid image: cannot read $part member"
			return 1
		}
	done
	[ "$(cat "$dir/CONTROL")" = 'BOARD=skyworth_sk-d840n' ] || {
		v "Invalid image: wrong CONTROL board"
		return 1
	}
	skd840n_validate_parts "$dir"
}

platform_check_image() {
	[ "$#" -eq 1 ] || return 1
	local dir rc=0
	dir=$(mktemp -d) || return 1
	skd840n_unpack_image "$1" "$dir" || rc=1
	rm -rf "$dir"
	return "$rc"
}

platform_do_upgrade() {
	local file="$1" dir sub conf_tar
	dir=$(mktemp -d) || return 1
	# Revalidate before any write, even when preflight was bypassed or the
	# image was replaced after platform_check_image returned successfully.
	skd840n_unpack_image "$file" "$dir" || { rm -rf "$dir"; return 1; }
	sub=$dir

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
