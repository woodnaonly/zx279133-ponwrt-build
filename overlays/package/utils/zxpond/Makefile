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
# $(TARGET_CPPFLAGS) is what OpenWrt hands every package it builds
# (include/package-defaults.mk puts it into both CFLAGS and CPPFLAGS), and the
# other three packages in this tree ask for it by name.  This one did not, and
# it was the only package in the tree that left it out.
#
# It is put back here NOT because a header was missing: run 297's build
# compiled this package without it and its log has no error - the toolchain's
# include directory is on the compiler's own search path.  It is put back
# because being the one exception is how the next person loses an hour to a
# difference nobody remembers.
#
# -Wall and -Werror are still not here, although the other three packages
# have them.  This file has never been compiled with the warning flags, and
# the only compiler here is CI: a build that fails over one warning, whose
# text lands only in a job log that cannot be read without a token, says
# nothing about whether the code is right.  The one warning that can be named
# from the source - zxpond.c comparing an nla_len against a sizeof() - is
# fixed there.  Once a build has run with -Wall and that log has been read,
# -Werror can follow the other three.
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