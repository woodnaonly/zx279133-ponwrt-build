#!/bin/sh
# SPDX-License-Identifier: GPL-2.0-only
# A small native interpreter catches RPC syntax/fixture errors before the long
# toolchain/kernel build. The final gate repeats this with the image's host ucode.
set -eu
cd "$(dirname "$0")/.."
BUILD=$(mktemp -d "${RUNNER_TEMP:-/tmp}/sk-pon-ucode.XXXXXX")
trap 'rm -rf "$BUILD"' EXIT HUP INT TERM
# Same ucode revision as the SK-D840N base tree's package/utils/ucode recipe.
PIN=85922056ef7abeace3cca3ab28bc1ac2d88e31b1
git init -q "$BUILD/source"
git -C "$BUILD/source" fetch -q --depth 1 https://github.com/jow-/ucode.git "$PIN"
git -C "$BUILD/source" checkout -q --detach FETCH_HEAD
cmake -S "$BUILD/source" -B "$BUILD/build" -DCMAKE_INSTALL_PREFIX="$BUILD/install" \
	-DCMAKE_INSTALL_LIBDIR=lib -DCMAKE_INSTALL_RPATH="$BUILD/install/lib" \
	-DFS_SUPPORT=ON -DMATH_SUPPORT=ON -DSTRUCT_SUPPORT=ON -DUBUS_SUPPORT=OFF \
	-DUCI_SUPPORT=OFF -DULOOP_SUPPORT=OFF -DNL80211_SUPPORT=OFF -DRTNL_SUPPORT=OFF \
	-DDEBUG_SUPPORT=OFF -DLOG_SUPPORT=OFF -DIO_SUPPORT=OFF -DSOCKET_SUPPORT=OFF \
	-DRESOLV_SUPPORT=OFF -DZLIB_SUPPORT=OFF -DDIGEST_SUPPORT=OFF
cmake --build "$BUILD/build" -j2
cmake --install "$BUILD/build"
python3 tests/test_sk_pon_rpc.py "$BUILD/install/bin/ucode"
python3 tests/test_sk_pon_firstboot.py --ucode "$BUILD/install/bin/ucode"

# Exercise exactly the firewall4 source/patches that will reach the image,
# before committing the runner to a long toolchain and kernel compilation.
git init -q "$BUILD/firewall"
git -C "$BUILD/firewall" fetch -q --depth 1 https://github.com/openwrt/firewall4.git b6e5157527d361f99ad52eaa6da273cb0f2dfd59
git -C "$BUILD/firewall" checkout -q --detach FETCH_HEAD
for patchfile in overlays/package/network/config/firewall4/sk-patches/*.patch; do
	patch -p1 -d "$BUILD/firewall" --input="$PWD/$patchfile"
done
(cd "$BUILD/firewall" && PATH="$BUILD/install/bin:$PATH" bash run_tests.sh)
