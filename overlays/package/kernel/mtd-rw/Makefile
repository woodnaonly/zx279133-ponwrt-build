# SPDX-License-Identifier: GPL-2.0-only
# Based on openwrt/packages kernel/mtd-rw (Joseph C. Lehner).
include $(TOPDIR)/rules.mk
include $(INCLUDE_DIR)/kernel.mk

PKG_NAME:=mtd-rw
PKG_RELEASE:=2

PKG_SOURCE_PROTO:=git
PKG_SOURCE_URL:=https://github.com/jclehner/mtd-rw
PKG_SOURCE_DATE:=2021-02-28
PKG_SOURCE_VERSION:=e87767395a6d27380196702f5f7bf98e92774f3f
PKG_MIRROR_HASH:=984218d7a8e1252419c45ef313f23fb6e5edfa83088f68a4a356b795444ab381
PKG_LICENSE:=GPL-2.0
PKG_LICENSE_FILES:=LICENSE
PKG_MAINTAINER:=Joseph C. Lehner <joseph.c.lehner@gmail.com>

include $(INCLUDE_DIR)/package.mk

define KernelPackage/mtd-rw
  SUBMENU:=Other modules
  TITLE:=Manual write-enabler for MTD partitions
  DEPENDS:=@TARGET_zte_zx279133
  FILES:=$(PKG_BUILD_DIR)/mtd-rw.ko
  # Deliberately no AUTOLOAD or MODPARAMS: installing must not unlock flash.
endef

define KernelPackage/mtd-rw/description
 Temporarily makes read-only MTD partitions writeable after an explicit
 insmod mtd-rw i_want_a_brick=1. Unloading restores flags changed by this
 module. This does not provide a flash writer, ECC adapter or bootloader.
endef

define Build/Compile
	$(KERNEL_MAKE) M="$(PKG_BUILD_DIR)" CONFIG_MTD_RW=m modules
endef

$(eval $(call KernelPackage,mtd-rw))
