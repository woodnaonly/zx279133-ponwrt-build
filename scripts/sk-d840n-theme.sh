#!/bin/sh
# SPDX-License-Identifier: GPL-2.0-only
# Shared by the SK-D840N configure/image gates; never used by the SR1010 build.

skd840n_select_theme() (
	set -eu
	cd "$1"
	# The seed config and the subtarget defaults both select Bootstrap upstream.
	# Change only the runner's SK-D840N tree, not the standalone SR1010 pipeline.
	sed -i -E 's/\<luci-theme-[[:alnum:]_-]+\>/luci-theme-argon/g' \
		target/linux/zte/zx279133/target.mk
	sed -i -E '/^CONFIG_PACKAGE_luci-theme-argon=/!s/^(CONFIG_PACKAGE_luci-theme-[^=[:space:]]+)=[ym]$/# \1 is not set/' .config
	grep -qx '# CONFIG_PACKAGE_luci-theme-bootstrap is not set' .config ||
		printf '%s\n' '# CONFIG_PACKAGE_luci-theme-bootstrap is not set' >>.config
)

skd840n_check_theme_config() (
	set -eu
	grep -qx 'CONFIG_PACKAGE_luci-theme-argon=y' "$1" || {
		echo 'Argon is not built into the resolved SK-D840N config' >&2; exit 1;
	}
	grep -qx 'CONFIG_LUCI_LANG_zh_Hans=y' "$1" || {
		echo 'Simplified Chinese was lost after defconfig' >&2; exit 1;
	}
	if grep -E '^CONFIG_PACKAGE_luci-theme-[^=[:space:]]+=[ym]$' "$1" |
		grep -vx 'CONFIG_PACKAGE_luci-theme-argon=y'; then
		echo 'Only Argon may be selected in the SK-D840N config' >&2; exit 1
	fi
)

skd840n_check_theme_image() (
	set -eu
	root=$1
	manifest=$2
	for package in luci-theme-argon luci-i18n-base-zh-cn \
		luci-i18n-argon-config-zh-cn luci-i18n-firewall-zh-cn; do
		grep -q "^$package " "$manifest" || {
			echo "Image manifest is missing $package" >&2; exit 1;
		}
	done
	if awk '$1 ~ /^luci-theme-/ && $1 != "luci-theme-argon" { print; found=1 }
		END { exit !found }' "$manifest"; then
		echo 'Only Argon may be installed in the SK-D840N image' >&2; exit 1
	fi
	for file in etc/config/argon etc/uci-defaults/95-skd840n-argon \
		www/luci-static/argon/css/cascade.css \
		usr/lib/lua/luci/i18n/base.zh-cn.lmo \
		usr/lib/lua/luci/i18n/argon-config.zh-cn.lmo \
		usr/lib/lua/luci/i18n/firewall.zh-cn.lmo; do
		[ -s "$root/$file" ] || {
			echo "Image root is missing $file" >&2; exit 1;
		}
	done
)
