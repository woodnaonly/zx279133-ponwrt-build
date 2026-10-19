include $(TOPDIR)/rules.mk

PKG_NAME:=mdread
PKG_VERSION:=1
PKG_RELEASE:=1
PKG_LICENSE:=GPL-2.0
PKG_MAINTAINER:=zx279133-ponwrt-build

include $(INCLUDE_DIR)/package.mk

# Same scope as the other overlays here: the controller register layout this tool drives is
# ZX279133's own (drivers/net/mdio/mdio-zx279133.c).
define Package/mdread
  SECTION:=utils
  CATEGORY:=Utilities
  TITLE:=Read an MDIO register through the ZX279133 controller
  DEPENDS:=@TARGET_zte_zx279133 +KERNEL_DEVMEM
endef

define Package/mdread/description
 Issues an MDIO read transaction against the controller at 0x14f02000 so the RTL8226B can be
 read from inside the running kernel. Nothing else on this board can do it: eth0 runs
 fixed-link, so phylib never attaches address 5 and SIOCGMIIREG has no phydev to ask, and the
 bootloader cannot be reached again without a power cut - which also resets the PHY, so U-Boot
 can only ever show the state before Linux, never after.
endef

define Build/Compile
	$(TARGET_CC) $(TARGET_CFLAGS) $(TARGET_CPPFLAGS) -Os -Wall -Werror \
		-o $(PKG_BUILD_DIR)/mdread $(PKG_BUILD_DIR)/mdread.c
endef

define Package/mdread/install
	$(INSTALL_DIR) $(1)/usr/bin
	$(INSTALL_BIN) $(PKG_BUILD_DIR)/mdread $(1)/usr/bin/mdread
endef

$(eval $(call BuildPackage,mdread))
