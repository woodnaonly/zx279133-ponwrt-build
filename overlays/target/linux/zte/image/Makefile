# SPDX-License-Identifier: GPL-2.0-only

include $(TOPDIR)/rules.mk
include $(INCLUDE_DIR)/image.mk

KERNEL_LOADADDR := 0x80200000
KERNEL_ENTRY := 0x80200000

define Device/Default
  PROFILES := Default
  KERNEL_NAME := Image
  KERNEL = kernel-bin | gzip | \
	fit gzip $$(KDIR)/image-$$(firstword $$(DEVICE_DTS)).dtb
  KERNEL_INITRAMFS = kernel-bin | gzip | \
	fit gzip $$(KDIR)/image-$$(firstword $$(DEVICE_DTS)).dtb
  KERNEL_SUFFIX := .itb
  KERNEL_INITRAMFS_SUFFIX := .itb
  DEVICE_DTS_DIR := $(DTS_DIR)/zte
  IMAGES :=
endef

define Device/zte_zxslc-sr1010
  DEVICE_VENDOR := ZTE
  DEVICE_MODEL := ZXSLC SR1010
  DEVICE_DTS := zx279133-sr1010
  SUPPORTED_DEVICES := zte,zx279133-sr1010
endef
TARGET_DEVICES += zte_zxslc-sr1010

# Skyworth SK-D840N ONU. The flashable profile mirrors the community U-Boot
# contract that is currently running on this unit:
#   bootcmd = mtd read kernel 0x88000000; mtd read dtb 0x88800000;
#             bootm 0x88000000 - 0x88800000
# i.e. a legacy uImage in the "kernel" partition, the bare DTB in the "dtb"
# partition and a jffs2 rootfs in "root" - so the box boots over SSH with no
# serial console attached.
define Device/skyworth_sk-d840n
  DEVICE_VENDOR := Skyworth
  DEVICE_MODEL := SK-D840N
  DEVICE_DTS := zx279133-sk-d840n
  SUPPORTED_DEVICES := skyworth,sk-d840n
  KERNEL_NAME := Image
  KERNEL = kernel-bin | gzip | uImage gzip
  KERNEL_SUFFIX := .uImage
  BLOCKSIZE := 128k
  PAGESIZE := 2048
  # include/image.mk names the artifact $(DEVICE_IMG_PREFIX)-$(fstype)-$(imagename),
  # i.e. ...-skyworth_sk-d840n-jffs2-128k-rootfs.jffs2. `Image/mkfs/jffs2/sub`
  # already appends the EOF mark, so the rootfs only needs to be concatenated.
  IMAGE/rootfs.jffs2 := append-rootfs
  IMAGES := rootfs.jffs2
endef
TARGET_DEVICES += skyworth_sk-d840n

$(eval $(call BuildImage))
