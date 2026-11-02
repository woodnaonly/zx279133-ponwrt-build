#!/bin/sh
# SPDX-License-Identifier: GPL-2.0-only
# Sourced by configure-sk-d840n.sh; the profile is also the final-image gate.

skd840n_offload_symbols() {
	sed -n 's/^CONFIG_\([A-Z0-9_]*\)=y$/\1/p' "$1"
}

skd840n_pin_offload_fragments() {
	sk_off_tree=$1
	sk_off_profile=$2
	[ -s "$sk_off_profile" ] || { echo "Missing offload profile: $sk_off_profile" >&2; return 1; }
	[ -f "$sk_off_tree/target/linux/zte/zx279133/config-6.18" ] || {
		echo 'Missing SK-D840N target kernel config' >&2; return 1;
	}
	for sk_off_file in target/linux/generic/config-6.18 \
		target/linux/zte/config-6.18 target/linux/zte/zx279133/config-6.18; do
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
