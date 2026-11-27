include $(TOPDIR)/rules.mk

LUCI_TITLE:=SK-D840N ONU management and truthful platform status
LUCI_DEPENDS:=+luci-base +luci-mod-system +luci-mod-network +luci-mod-status +luci-i18n-base-zh-cn +rpcd-mod-ucode +ucode-mod-fs +zxpond +luci-theme-argon +luci-i18n-argon-config-zh-cn
LUCI_PKGARCH:=all
PKG_VERSION:=1.0.0
PKG_RELEASE:=5
PKG_LICENSE:=GPL-2.0-only
CONFIG_LUCI_CSSTIDY:=

include $(TOPDIR)/feeds/luci/luci.mk

# LuCI's JavaScript minifier rewrites every .js of the package through
#
#     jsmin < f > f.o ; mv f.o f
#
# and never puts the mode back: luci.mk's JsMin macro ends at the mv, while
# the .ut path above it ends with a chmod 0644.  So the mode of the file
# that ships is the mode of the .o - and a .o that is already there keeps
# its own mode, because > truncates in place instead of creating a new
# file.  A leftover f.js.o at 0600 therefore ships f.js at 0600.
#
# That is not a cosmetic detail.  uhttpd hands out a file only when it is
# readable by everyone - file.c tests the file's own mode (S_IROTH) and
# answers 403 Forbidden for everything else, whatever the process running
# the server may read.  So a file at 0600 is a page that does not open,
# and the browser says "HTTP error 403 while loading class file", which
# names the URL and not the reason.
#
# The image this repository built shipped zx279133-los.js at 0600 - the
# only file in the whole rootfs without the world-read bit - and the LED
# page died with exactly that error.  Nothing in the build looked at the
# modes of what it shipped, so nothing said so.
#
# So the tree is put right here, before the package is staged: the
# leftovers go, and the modes go back to what a file the web server hands
# out has to be.  The gate that asks the built rootfs about it is
# tools/check-sk-luci-readable.py.
define Build/Compile
	find $(PKG_BUILD_DIR)/htdocs -type f -name '*.js.o' -delete
	find $(PKG_BUILD_DIR)/htdocs -type f -exec chmod 0644 {} +
endef

# call BuildPackage - OpenWrt buildroot scan signature
