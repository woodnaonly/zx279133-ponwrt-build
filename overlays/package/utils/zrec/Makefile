include $(TOPDIR)/rules.mk

PKG_NAME:=zrec
PKG_VERSION:=1
PKG_RELEASE:=1
PKG_LICENSE:=GPL-2.0
PKG_MAINTAINER:=zx279133-ponwrt-build

include $(INCLUDE_DIR)/package.mk

# Scoped to this subtarget on purpose: the target list carries the partitions of
# one particular board layout, and an image for another ZX279133 board would get
# a browser-triggerable flasher whose labels do not exist there.
define Package/zrec
  SECTION:=utils
  CATEGORY:=Utilities
  TITLE:=Browser upload and flash for the SK-D840N recovery image
  DEPENDS:=@TARGET_zte_zx279133
endef

define Package/zrec/description
 HTTP server for the RAM recovery image: uploads an image, programs it by
 /proc/mtd label, reads it back and compares the hash. Speaks the same
 /api contract as uboot-an758x's lwIP httpd, so the page is shared.
endef

# Compiled straight with the cross toolchain rather than through a build system:
# one file, no configure, no host-path leakage. Warnings are errors here because
# the first real compile of this file happens 40 minutes into a build, and an
# unused-variable typo should not be what costs that build.
define Build/Compile
	$(TARGET_CC) $(TARGET_CFLAGS) $(TARGET_CPPFLAGS) -Os -Wall -Werror \
		-o $(PKG_BUILD_DIR)/zrec $(PKG_BUILD_DIR)/zrec.c
endef

define Package/zrec/install
	$(INSTALL_DIR) $(1)/usr/sbin $(1)/usr/libexec $(1)/www/zrec $(1)/etc/rc.d \
		$(1)/lib/preinit
	$(INSTALL_BIN) $(PKG_BUILD_DIR)/zrec $(1)/usr/sbin/zrec
	$(INSTALL_BIN) ./files/usr/sbin/zrec-run $(1)/usr/sbin/zrec-run
	$(INSTALL_BIN) ./files/usr/libexec/zrec-ops $(1)/usr/libexec/zrec-ops
	$(INSTALL_BIN) ./files/usr/libexec/zrec-bringup $(1)/usr/libexec/zrec-bringup
	$(INSTALL_DATA) ./files/lib/preinit/85_zrec $(1)/lib/preinit/85_zrec
	$(INSTALL_BIN) ./files/etc/rc.d/S21zrec $(1)/etc/rc.d/S21zrec
	$(INSTALL_DATA) ./files/www/zrec/index.html $(1)/www/zrec/index.html
endef

$(eval $(call BuildPackage,zrec))
