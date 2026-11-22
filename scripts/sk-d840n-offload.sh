#!/bin/sh
# SPDX-License-Identifier: GPL-2.0-only
# Sourced by configure-sk-d840n.sh; the profile is also the final-image gate.

skd840n_offload_symbols() {
	sed -n 's/^CONFIG_\([A-Z0-9_]*\)=y$/\1/p' "$1"
}

skd840n_target_config_series() {
	# The kernel series comes from the kernel tree itself, not from a name
	# written down here: cnjn's master carries target/linux/generic/config-6.18
	# AND config-7.3 side by side, so a fragment that is merely "the one that
	# exists" would be the wrong one half the time.  The tree says which
	# series it is.
	sk_ts_tree=$1
	sk_ts_kernel=$2
	[ -f "$sk_ts_kernel/Makefile" ] || return 1
	sk_ts_version=$(sed -n 's/^VERSION[ \t]*=[ \t]*\([0-9]*\).*/\1/p; s/^PATCHLEVEL[ \t]*=[ \t]*\([0-9]*\).*/\1/p' "$sk_ts_kernel/Makefile" | tr '\n' '.')
	sk_ts_version=${sk_ts_version%.}
	case "$sk_ts_version" in
	[0-9]*.[0-9]*) ;;
	*) return 1 ;;
	esac
	printf '%s\n' "$sk_ts_version"
}

skd840n_target_config_series_from_fragments() {
	# The fallback for a caller that has no kernel tree to read: pick the
	# series out of the subtarget fragment, which is the fragment the board
	# actually uses.
	#
	# It has to be the subtarget one and not target/linux/generic, because
	# the generic directory carries every series the tree supports at once -
	# cnjn's master has config-6.12, config-6.18 and config-7.3 side by side -
	# so a series taken from there would be a guess among three.  The
	# subtarget directory carries exactly one, and if it ever carries more
	# than one this refuses rather than guessing.
	sk_tf_tree=$1
	sk_tf_series=$(ls "$sk_tf_tree"/target/linux/*/*/config-* 2>/dev/null |
		sed -n 's|.*/config-\([0-9][0-9.]*\)$|\1|p' | sort -u)
	[ -n "$sk_tf_series" ] || return 1
	set -- $sk_tf_series
	[ $# -eq 1 ] || return 1
	printf '%s\n' "$1"
}

skd840n_pin_offload_fragments() {
	sk_off_tree=$1
	sk_off_profile=$2
	sk_off_kernel=${3:-}
	[ -s "$sk_off_profile" ] || { echo "Missing offload profile: $sk_off_profile" >&2; return 1; }
	if [ -n "$sk_off_kernel" ]; then
		sk_off_series=$(skd840n_target_config_series "$sk_off_tree" "$sk_off_kernel") || {
			echo 'Missing SK-D840N target kernel config' >&2; return 1; }
	else
		sk_off_series=$(skd840n_target_config_series_from_fragments "$sk_off_tree") || {
			echo 'Missing SK-D840N target kernel config' >&2; return 1; }
	fi
	for sk_off_file in "target/linux/generic/config-$sk_off_series" \
		"target/linux/zte/config-$sk_off_series" \
		"target/linux/zte/zx279133/config-$sk_off_series"; do
		[ -f "$sk_off_tree/$sk_off_file" ] || continue
		for sk_off_sym in $(skd840n_offload_symbols "$sk_off_profile"); do
			sed -i -e "/^CONFIG_$sk_off_sym=/d" \
				-e "/^# CONFIG_$sk_off_sym is not set\$/d" "$sk_off_tree/$sk_off_file"
			printf 'CONFIG_%s=y\n' "$sk_off_sym" >>"$sk_off_tree/$sk_off_file"
		done
	done
}

skd840n_pin_offload_build_config() {
	# OpenWrt appends CONFIG_KERNEL_* after the target fragments. Replace
	# existing overrides after defconfig, but do not invent nonexistent
	# build-root symbols for networking options owned only by the kernel.
	sk_off_config=$1
	sk_off_profile=$2
	for sk_off_sym in $(skd840n_offload_symbols "$sk_off_profile"); do
		if grep -qE "^(CONFIG_KERNEL_$sk_off_sym=|# CONFIG_KERNEL_$sk_off_sym is not set\$)" "$sk_off_config"; then
			sed -i -e "/^CONFIG_KERNEL_$sk_off_sym=/d" \
				-e "/^# CONFIG_KERNEL_$sk_off_sym is not set\$/d" "$sk_off_config"
			printf 'CONFIG_KERNEL_%s=y\n' "$sk_off_sym" >>"$sk_off_config"
		fi
	done
}
