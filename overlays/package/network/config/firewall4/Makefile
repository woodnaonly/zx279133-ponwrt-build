# OpenWrt firewall4 b6e5157 (ISC). Kernel nft components are BUILT IN on this
# external-kernel target, not represented by nonexistent kmod packages.
# scripts/configure-sk-d840n.sh selects and verifies the required kernel features.
include $(TOPDIR)/rules.mk

PKG_NAME:=firewall4
PKG_RELEASE:=5
PKG_SOURCE_PROTO:=git
PKG_SOURCE_URL:=https://git.openwrt.org/project/firewall4.git
PKG_SOURCE_DATE:=2025-03-17
PKG_SOURCE_VERSION:=b6e5157527d361f99ad52eaa6da273cb0f2dfd59
PKG_MIRROR_HASH:=fb563b493411922a8fbddf6a5a6766cffa47f87621e056b8dcf38309091835eb
PKG_MAINTAINER:=Jo-Philipp Wich <jo@mein.io>
PKG_LICENSE:=ISC

include $(INCLUDE_DIR)/package.mk
define Package/firewall4
  SECTION:=net
  CATEGORY:=Base system
  TITLE:=OpenWrt 4th gen firewall (ZX279133 built-in nft backend)
  DEPENDS:=+nftables +ucode +ucode-mod-fs +ucode-mod-ubus +ucode-mod-uci
  EXTRA_DEPENDS:=ucode (>=2022.03.22)
  PROVIDES:=uci-firewall
endef
define Package/firewall4/conffiles
/etc/config/firewall
/etc/nftables.d/
endef
define Package/firewall4/install
	$(CP) -a $(PKG_BUILD_DIR)/root/* $(1)/
endef
define Build/Compile
endef
$(eval $(call BuildPackage,firewall4))
