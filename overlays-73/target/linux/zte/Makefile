# SPDX-License-Identifier: GPL-2.0-only
#
# The ZTE target on the Linux 7.3 line.
#
# This is the 6.18 target file with one substantive change and a lot of
# context, so read the difference before copying anything from it:
#
#   * KERNEL_PATCHVER is 7.3, not 6.18.  That is what makes OpenWrt look for
#     target/linux/generic/config-7.3 and target/linux/zte/zx279133/config-7.3,
#     and it is also what puts the tree on cnjn's special path in
#     include/kernel.mk:
#
#         ifeq ($(BOARD):$(KERNEL_PATCHVER),zte:7.3)
#           LINUX_UNAME_VERSION:=$(LINUX_VERSION)
#         endif
#
#     Without that, the module directory name is read out of a tree that does
#     not exist yet and the packaged modules end up under a version nobody
#     booted.
#
#   * The tree is still an EXTERNAL tree, prepared by
#     tools/prepare-sk-kernel-73.py: vanilla linux-7.3.4 plus cnjn's
#     vendored patches-7.3 series, which is where the whole ZX279133 NPU
#     driver lives.  cnjn's own zte target refuses CONFIG_EXTERNAL_KERNEL_TREE
#     because it wants OpenWrt to download the base kernel and apply that
#     series itself; this repository does the preparation itself instead, so
#     that the exact tarball and the exact patch bytes are pinned here and can
#     be checked without a build.
#
#   * Everything else - the FEATURES line, the image profile in
#     ../image/Makefile, the base-files - comes from the shared overlays/
#     directory and is the same on both lines.

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
KERNEL_PATCHVER:=7.3

ifeq ($(DUMP),)
  ifeq ($(strip $(CONFIG_EXTERNAL_KERNEL_TREE)),)
    $(error The ZTE 7.3 target requires CONFIG_EXTERNAL_KERNEL_TREE pointing to the prepared Linux 7.3.4 tree)
  endif
endif

# The first SR1010 image is intentionally a small RAM-boot environment.
DEVICE_TYPE:=basic

include $(INCLUDE_DIR)/target.mk

define Target/Description
	Build RAM-boot images for supported ZTE SoCs.
endef

$(eval $(call BuildTarget))
