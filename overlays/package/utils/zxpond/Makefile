include $(TOPDIR)/rules.mk

PKG_NAME:=zxpond
PKG_VERSION:=2
PKG_RELEASE:=1
PKG_LICENSE:=GPL-2.0

include $(INCLUDE_DIR)/package.mk

define Package/zxpond
  SECTION:=net
  CATEGORY:=Network
  TITLE:=SK-D840N XG-PON identity and OMCI daemon
  DEPENDS:=@TARGET_zte_zx279133 +libuci +libzxpon-omci +libpthread
endef

define Package/zxpond/description
  UCI identity and hardware-independent OMCI engine. Preserves the legacy
  identity file. Includes privileged queue-0 raw OMCI submission; reports
  missing optical activation, RX and integrity-key integration explicitly.
endef

# The sole daemon links the Rust engine statically; host tests use the same FFI.
define Build/Compile
	$(TARGET_CC) $(TARGET_CFLAGS) $(TARGET_CPPFLAGS) -Os -Wall -Wextra -Werror \
		-I$(PKG_BUILD_DIR) -o $(PKG_BUILD_DIR)/zxpond \
		$(PKG_BUILD_DIR)/zxpond.c $(PKG_BUILD_DIR)/zxpon-config.c $(PKG_BUILD_DIR)/zxpon-backend.c $(PKG_BUILD_DIR)/zxpon-transport.c \
		$(TARGET_LDFLAGS) -Wl,--gc-sections -lzxpon_omci -luci -lpthread -lm -ldl
endef

define Package/zxpond/install
	$(INSTALL_DIR) $(1)/usr/sbin $(1)/etc/pon $(1)/etc/init.d
	$(INSTALL_BIN) $(PKG_BUILD_DIR)/zxpond $(1)/usr/sbin/zxpond
	$(INSTALL_DATA) -m0600 ./files/identity $(1)/etc/pon/identity
	$(INSTALL_BIN) ./files/zxpond.init $(1)/etc/init.d/zxpond
endef

$(eval $(call BuildPackage,zxpond))
