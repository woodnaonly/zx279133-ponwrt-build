include $(TOPDIR)/rules.mk

PKG_NAME:=rdreg
PKG_VERSION:=1
PKG_RELEASE:=1
PKG_LICENSE:=GPL-2.0
PKG_MAINTAINER:=zx279133-ponwrt-build

include $(INCLUDE_DIR)/package.mk

# Scoped to this subtarget for the same reason as every other overlay here: the addresses it
# is used against are ZX279133's.
define Package/rdreg
  SECTION:=utils
  CATEGORY:=Utilities
  TITLE:=Read a ZX279133 register word through mmap of /dev/mem
  DEPENDS:=@TARGET_zte_zx279133 +KERNEL_DEVMEM
endef

define Package/rdreg/description
 Reads physical register words from inside the running kernel. /dev/mem read() answers
 EFAULT for MMIO here and the shipped busybox carries no devmem applet, so without this
 the only register values reachable from Linux are the ones the netdev driver volunteers
 through ethtool -S - one new address per 70-minute build. Read-only, no write path.
endef

# Compiled straight with the cross toolchain, as zrec is: one file, no configure. -Werror
# because the first real compile of this file happens 40 minutes into a build.
define Build/Compile
	$(TARGET_CC) $(TARGET_CFLAGS) $(TARGET_CPPFLAGS) -Os -Wall -Werror \
		-o $(PKG_BUILD_DIR)/rdreg $(PKG_BUILD_DIR)/rdreg.c
endef

define Package/rdreg/install
	$(INSTALL_DIR) $(1)/usr/bin
	$(INSTALL_BIN) $(PKG_BUILD_DIR)/rdreg $(1)/usr/bin/rdreg
endef

$(eval $(call BuildPackage,rdreg))
