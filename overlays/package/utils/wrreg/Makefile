include $(TOPDIR)/rules.mk

PKG_NAME:=wrreg
PKG_VERSION:=1
PKG_RELEASE:=1
PKG_LICENSE:=GPL-2.0
PKG_MAINTAINER:=zx279133-ponwrt-build

include $(INCLUDE_DIR)/package.mk

define Package/wrreg
  SECTION:=utils
  CATEGORY:=Utilities
  TITLE:=Write one ZX279133 network register word through mmap of /dev/mem
  DEPENDS:=@TARGET_zte_zx279133 +rdreg
endef

define Package/wrreg/description
 Pokes a single register and reads it back, inside the network blocks only (0x14000000 up to
 0x1d000000), so that a register hypothesis can be tested from the console in seconds instead
 of by a build that takes eighty minutes. It cannot reach the SPI-NAND controller, the
 watchdog/CRG or GPIO. Paired with rdreg, which reads the same space.
endef

define Build/Compile
	$(TARGET_CC) $(TARGET_CFLAGS) $(TARGET_CPPFLAGS) -Os -Wall -Werror \
		-o $(PKG_BUILD_DIR)/wrreg $(PKG_BUILD_DIR)/wrreg.c
endef

define Package/wrreg/install
	$(INSTALL_DIR) $(1)/usr/bin
	$(INSTALL_BIN) $(PKG_BUILD_DIR)/wrreg $(1)/usr/bin/wrreg
endef

$(eval $(call BuildPackage,wrreg))
