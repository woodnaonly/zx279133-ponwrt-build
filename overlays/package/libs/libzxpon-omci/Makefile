include $(TOPDIR)/rules.mk

PKG_NAME:=libzxpon-omci
PKG_VERSION:=0.8.4
PKG_RELEASE:=1
PKG_LICENSE:=GPL-2.0-only
PKG_BUILD_PARALLEL:=1

include $(INCLUDE_DIR)/package.mk

define Package/libzxpon-omci
  SECTION:=libs
  CATEGORY:=Libraries
  TITLE:=Hardware-independent OMCI engine for ZX279133
  DEPENDS:=@TARGET_zte_zx279133
endef

ZX_CARGO?=$(shell command -v cargo)

define Build/Prepare
	$(INSTALL_DIR) $(PKG_BUILD_DIR)/src
	$(CP) ./omci/src/* $(PKG_BUILD_DIR)/src/
	$(CP) ./omci/Cargo.toml ./omci/Cargo.lock $(PKG_BUILD_DIR)/
endef

define Build/Compile
	cd $(PKG_BUILD_DIR) && RUSTC="$(dir $(ZX_CARGO))rustc" \
		RUSTFLAGS="-C target-cpu=cortex-a53" $(ZX_CARGO) build --release --locked --target aarch64-unknown-linux-musl
endef

define Build/InstallDev
	$(INSTALL_DIR) $(1)/usr/include $(1)/usr/lib
	$(CP) ./zxpon-omci.h $(1)/usr/include/
	$(CP) $(PKG_BUILD_DIR)/target/aarch64-unknown-linux-musl/release/libzxpon_omci.a $(1)/usr/lib/
endef

# The engine is statically linked into the sole zxpond process.
define Package/libzxpon-omci/install
	$(INSTALL_DIR) $(1)/usr/share/zxpon
	$(INSTALL_DATA) ./omci/LICENSE ./omci/COPYING ./omci/UPSTREAM $(1)/usr/share/zxpon/
endef

$(eval $(call BuildPackage,libzxpon-omci))
