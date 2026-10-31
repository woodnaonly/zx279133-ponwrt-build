include $(TOPDIR)/rules.mk

PKG_NAME:=zxpond
PKG_VERSION:=1
PKG_RELEASE:=1
PKG_LICENSE:=GPL-2.0

include $(INCLUDE_DIR)/package.mk

define Package/zxpond
  SECTION:=net
  CATEGORY:=Network
  TITLE:=SK-D840N XG-PON identity and OMCI daemon
  DEPENDS:=@TARGET_zte_zx279133
endef

define Package/zxpond/description
  Reads /etc/pon/identity and arms the kernel XG-PON state machine.
  Answers the small G.988 MIB. An empty identity leaves the ONU in O1.
endef

define Build/Compile
	$(TARGET_CC) $(TARGET_CFLAGS) -I$(PKG_BUILD_DIR) -o $(PKG_BUILD_DIR)/zxpond \
		$(PKG_BUILD_DIR)/zxpond.c $(PKG_BUILD_DIR)/zxpon.c
endef

define Package/zxpond/install
	$(INSTALL_DIR) $(1)/usr/sbin $(1)/etc/pon $(1)/etc/init.d
	$(INSTALL_BIN) $(PKG_BUILD_DIR)/zxpond $(1)/usr/sbin/zxpond
	$(INSTALL_DATA) ./files/identity $(1)/etc/pon/identity
	$(INSTALL_BIN) ./files/zxpond.init $(1)/etc/init.d/zxpond
endef

$(eval $(call BuildPackage,zxpond))