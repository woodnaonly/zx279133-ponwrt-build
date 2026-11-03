# SPDX-License-Identifier: GPL-2.0-only
include $(TOPDIR)/rules.mk

PKG_NAME:=sk-layout-check
PKG_VERSION:=1
PKG_RELEASE:=1
PKG_LICENSE:=GPL-2.0-only

include $(INCLUDE_DIR)/package.mk

define Package/sk-layout-check
  SECTION:=utils
  CATEGORY:=Utilities
  TITLE:=Read-only SK-D840N DTB layout validator
  DEPENDS:=@TARGET_zte_zx279133 +libfdt
endef

define Build/Compile
	$(TARGET_CC) $(TARGET_CFLAGS) $(TARGET_CPPFLAGS) -Os -Wall -Wextra -Werror \
		-o $(PKG_BUILD_DIR)/sk-layout-check $(PKG_BUILD_DIR)/sk-layout-check.c \
		$(TARGET_LDFLAGS) -lfdt
endef

define Package/sk-layout-check/install
	$(INSTALL_DIR) $(1)/usr/sbin
	$(INSTALL_BIN) $(PKG_BUILD_DIR)/sk-layout-check $(1)/usr/sbin/sk-layout-check
endef

$(eval $(call BuildPackage,sk-layout-check))
