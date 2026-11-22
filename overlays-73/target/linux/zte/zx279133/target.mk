# SPDX-License-Identifier: GPL-2.0-only
#
# The 7.3 line's subtarget defaults.  This file is the one place where the
# two lines differ on the OpenWrt side, and it is here because of the theme.
#
# OpenWrt's own include/target.mk seeds DEFAULT_PACKAGES.tweak with
# block-mount, default-settings-chn, kmod-nf-nathelper and luci.  The "luci"
# there is the LuCI metapackage, and the metapackage pulls luci-light, and
# luci-light depends on luci-theme-bootstrap:
#
#   collections/luci      LUCI_DEPENDS:= +luci-light +luci-app-package-manager
#   collections/luci-light LUCI_DEPENDS:= ... +luci-theme-bootstrap ...
#
# so a tree that keeps the tweak defaults ends up with Bootstrap selected
# next to Argon, and the theme check in scripts/sk-d840n-theme.sh refuses
# that: "Only Argon may be selected in the SK-D840N config".  Run 45012472909
# is that check failing on the 7.3 line, with the resolved .config carrying
# both CONFIG_PACKAGE_luci-theme-argon=y and
# CONFIG_PACKAGE_luci-theme-bootstrap=y.
#
# The 6.18 line never hit it, because cnjn's sr1010-zx279133 branch clears the
# tweak list and names its own packages - that branch's target.mk has
# "DEFAULT_PACKAGES.tweak:=" and then a short list with
# luci-theme-bootstrap in it, which scripts/sk-d840n-theme.sh rewrites to
# Argon.  The 7.3 line builds from cnjn's master, whose zx279133 target.mk
# has no tweak list at all, so the upstream defaults stay.
#
# This file is the same move for the 7.3 line: clear the tweak defaults and
# name the packages this board wants.  Argon is spelled out here rather than
# left to the theme script, so that the list is right even if the rewrite
# does not run - and it is the same list the 6.18 line ships, which is the
# one that has been running on this board.
ARCH:=aarch64
SUBTARGET:=zx279133
BOARDNAME:=ZX279133 based boards
CPU_TYPE:=cortex-a53
KERNELNAME:=Image dtbs

# Keep the RAM image focused while providing a minimal LuCI administration UI.
DEFAULT_PACKAGES.tweak:=
DEFAULT_PACKAGES += \
	dnsmasq ethtool ip-full \
	luci-base luci-mod-admin-full luci-theme-argon \
	uhttpd uhttpd-mod-ubus \
	openssh-sftp-server

define Target/Description
	Build squashfs firmware and initramfs FIT images for the ZTE ZX279133 SoC family.
endef
