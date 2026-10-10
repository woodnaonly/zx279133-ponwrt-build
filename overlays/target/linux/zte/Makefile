# SPDX-License-Identifier: GPL-2.0-only

include $(TOPDIR)/rules.mk

BOARD:=zte
BOARDNAME:=ZTE SoCs
SUBTARGETS:=zx279133
# `jffs2` is the reason this file is overlaid: FEATURES is what makes
# CONFIG_TARGET_ROOTFS_JFFS2 exist at all, and cnjn's target only lists the RAM
# path (ramdisk/cpiogz). Without it `CONFIG_TARGET_ROOTFS_JFFS2=y` in .config is
# silently dropped by defconfig, no -rootfs.jffs2 is ever built, and the flash
# job has nothing to write into the root partition.
FEATURES:=dt gpio ramdisk cpiogz usb jffs2
KERNEL_PATCHVER:=6.18

ifeq ($(DUMP),)
  ifeq ($(strip $(CONFIG_EXTERNAL_KERNEL_TREE)),)
    $(error The ZTE target requires CONFIG_EXTERNAL_KERNEL_TREE pointing to the prepared ZX279133 kernel)
  endif
endif

# The first SR1010 image is intentionally a small RAM-boot environment.
DEVICE_TYPE:=basic

include $(INCLUDE_DIR)/target.mk

define Target/Description
	Build RAM-boot images for supported ZTE SoCs.
endef

$(eval $(call BuildTarget))
