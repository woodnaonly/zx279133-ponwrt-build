include $(TOPDIR)/rules.mk

PKG_NAME:=sk-port-status
PKG_VERSION:=1
PKG_RELEASE:=1
PKG_LICENSE:=GPL-2.0
PKG_MAINTAINER:=zx279133-ponwrt-build

include $(INCLUDE_DIR)/package.mk

# Installed after luci-mod-status because of the dependency, so this copy of
# 29_ports.js replaces the stock overview row. Netdev names are unchanged.
define Package/sk-port-status
  SECTION:=utils
  CATEGORY:=Utilities
  TITLE:=SK-D840N LuCI port names
  DEPENDS:=+luci-mod-status @TARGET_zte_zx279133
endef

define Package/sk-port-status/description
  The status overview shows the four panel jacks as LAN1..LAN4. LAN1 is
  netdev lan5. The jackless lan1, the CPU conduit and the PON MAC are not
  drawn. Speeds still come from the kernel.
endef

define Build/Compile
endef

define Package/sk-port-status/install
	$(INSTALL_DIR) $(1)/www/luci-static/resources/view/status/include
	$(INSTALL_DATA) ./files/29_ports.js \
		$(1)/www/luci-static/resources/view/status/include/29_ports.js
endef

$(eval $(call BuildPackage,sk-port-status))