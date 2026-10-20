# SPDX-License-Identifier: GPL-2.0-only
#
# The feed's own nftables package, with one dependency changed: the
# +kmod-nft-core dependency is gone.
#
# Why: the kernel this image boots is built with nf_tables *in* (the symbols
# are pinned =y by scripts/configure-sk-d840n.sh), so there is no
# nf_tables.ko for a kmod package to ship - and this feed has no netfilter
# kmod packages at all: package/kernel/modules/nf_tables.ko, nft_masq.ko,
# nf_nat.ko and nf_conntrack.ko are all 404 in this branch. A package that
# depends on a kmod that does not exist cannot be selected at all, so the
# feed's nftables package is unusable as it stands and the NAT the plan asks
# for would have neither a kernel half nor a userspace half.
#
# The rest of the file is the feed's recipe verbatim (version 1.1.6, its
# source hash, its configure arguments, its install layout), with the variants
# dropped: there is one package here, named nftables, and it is the one
# CONFIG_PACKAGE_nftables=y selects.
include $(TOPDIR)/rules.mk

PKG_NAME:=nftables
PKG_VERSION:=1.1.6
PKG_RELEASE:=2

PKG_SOURCE:=$(PKG_NAME)-$(PKG_VERSION).tar.xz
PKG_SOURCE_URL:=https://netfilter.org/projects/$(PKG_NAME)/files
PKG_HASH:=372931bda8556b310636a2f9020adc710f9bab66f47efe0ce90bff800ac2530c

PKG_MAINTAINER:=
PKG_LICENSE:=GPL-2.0
PKG_LICENSE_FILES:=COPYING

PKG_INSTALL:=1
PKG_FIXUP:=autoreconf

PKG_BUILD_FLAGS:=lto

include $(INCLUDE_DIR)/package.mk

DISABLE_NLS:=

CONFIGURE_ARGS += \
        --disable-debug \
        --disable-man-doc \
        --with-mini-gmp \
        --without-cli \
        --disable-python

define Package/nftables
  SECTION:=net
  CATEGORY:=Network
  SUBMENU:=Firewall
  TITLE:=nftables userspace utility
  DEPENDS:=+libnftnl
  URL:=http://netfilter.org/projects/nftables/
endef

define Build/InstallDev
	$(INSTALL_DIR) $(1)/usr/lib $(1)/usr/include
	$(CP) $(PKG_INSTALL_DIR)/usr/lib/*.so* $(1)/usr/lib/
	$(CP) $(PKG_INSTALL_DIR)/usr/include/nftables $(1)/usr/include/
	$(INSTALL_DIR) $(1)/usr/lib/pkgconfig
	$(CP) $(PKG_INSTALL_DIR)/usr/lib/pkgconfig/libnftables.pc \
		$(1)/usr/lib/pkgconfig/
endef

define Package/nftables/install
	$(INSTALL_DIR) $(1)/usr/sbin
	$(CP) $(PKG_INSTALL_DIR)/usr/sbin/nft $(1)/usr/sbin/
	$(INSTALL_DIR) $(1)/usr/lib
	$(CP) $(PKG_INSTALL_DIR)/usr/lib/*.so* $(1)/usr/lib/
endef

$(eval $(call BuildPackage,nftables))
