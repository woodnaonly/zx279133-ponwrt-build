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

# Compiled straight with the cross toolchain, as mdread, rdreg and wrreg are:
# two files, no configure.
#
# $(TARGET_CPPFLAGS) is the part that was missing, and it is the part that
# matters.  It carries the toolchain's include directory, which is where the
# kernel's uapi headers live: zxpond.c includes <linux/genetlink.h>,
# <linux/netlink.h> and <linux/if_packet.h>, and without that directory the
# compiler stops with "linux/genetlink.h: No such file or directory".
# OpenWrt hands it to every package it builds (include/package-defaults.mk
# puts $(TARGET_CPPFLAGS) into both CFLAGS and CPPFLAGS), the other three
# packages in this tree ask for it by name, and this one was the only package
# in the tree that did not.
#
# -Wall and -Werror are deliberately not here yet, even though the other
# three packages use them.  This file has never been compiled by the cross
# compiler, so nobody has seen what it warns about, and one warning is
# certain to arrive: zxpond.c reads a netlink attribute through a char *, and
# -Wall turns -Wcast-align on, which the kernel itself switches off
# (KBUILD_CFLAGS carries cc-disable-warning, cast-align).  Turning that into
# an error would fail a build over a cast that is correct on this target.
# Once CI has compiled this file, the flags can follow the other three.
define Build/Compile
	$(TARGET_CC) $(TARGET_CFLAGS) $(TARGET_CPPFLAGS) -Os \
		-I$(PKG_BUILD_DIR) -o $(PKG_BUILD_DIR)/zxpond \
		$(PKG_BUILD_DIR)/zxpond.c $(PKG_BUILD_DIR)/zxpon.c
endef

define Package/zxpond/install
	$(INSTALL_DIR) $(1)/usr/sbin $(1)/etc/pon $(1)/etc/init.d
	$(INSTALL_BIN) $(PKG_BUILD_DIR)/zxpond $(1)/usr/sbin/zxpond
	$(INSTALL_DATA) ./files/identity $(1)/etc/pon/identity
	$(INSTALL_BIN) ./files/zxpond.init $(1)/etc/init.d/zxpond
endef

$(eval $(call BuildPackage,zxpond))