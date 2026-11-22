#!/bin/sh
# SPDX-License-Identifier: GPL-2.0-only
#
# Copy the SK-D840N board layer into a prepared Linux 7.3 tree.
#
# The board layer is the same on both kernel lines: the device tree, the
# Unim NAND driver and the DSA transport-VID range.  What is NOT the same is
# the ethernet driver, and that is why this script exists:
#
#   * kernel-overlays/drivers/net/ethernet/zte/ is the 6.18 GENERATION of the
#     ZX279133 driver (the split np/mac/netdev/main/tm/idm set) plus this
#     repository's own switch/pon/omci/rgmii work.  Linux 7.3 ships a
#     rewritten driver with a different module layout - one composite npu.o
#     made of main/tm/np/idm/mac/netdev/offload-api/ptp/host/native/meter/qos
#     - so copying those files over the 7.3 tree would shadow the new driver
#     with files that do not build against it.
#   * kernel-overlays/drivers/net/phy/zte/ is this repository's Uni SerDes
#     PHY, written for the 6.18 tree.  Linux 7.3 keeps the same PHY under
#     drivers/phy/zte/, and the two implementations are not interchangeable.
#
# So the copy states what it leaves out, and it checks afterwards that the
# 7.3 driver is still the one in the tree.
#
# usage: sh tools/apply-sk-overlay-73.sh <kernel-tree>

set -eu

tree=${1:?kernel tree}
script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
repo=$(CDPATH= cd -- "$script_dir/.." && pwd)

[ -f "$tree/Makefile" ] || { echo "not a kernel tree: $tree" >&2; exit 1; }

# The 6.18 driver set and this repository's 6.18-era PHY must stay out.
( cd "$repo/kernel-overlays" && tar cf - \
	--exclude='./drivers/net/ethernet/zte' \
	--exclude='./drivers/net/phy/zte' \
	--exclude='./.sk-series' \
	--exclude='./.sk-series-applied.json' \
	. ) | ( cd "$tree" && tar xf - )

# The 7.3 line's own driver tree.  It is not the 6.18 generation: the 9132
# LAN switch driver is written against the 7.3 driver set's DSA API and its
# LAN service, and it is the only file of ours that goes into the 7.3 tree
# so far.  The other two files are its private header and the LAN1 LED
# helper, which are self-contained.
( cd "$repo/kernel-overlays-73" && tar cf - . ) | ( cd "$tree" && tar xf - )

# The NPU microcode.  config-7.3 sets CONFIG_EXTRA_FIRMWARE="zte/zx279133/
# mcode_intel.bin" with CONFIG_EXTRA_FIRMWARE_DIR="firmware", which the
# kernel resolves against its own source tree, so the file has to sit in the
# tree we hand to OpenWrt - not in the OpenWrt checkout.
fw="$repo/overlays/target/linux/zte/zx279133/firmware/zte/zx279133/mcode_intel.bin"
[ -f "$fw" ] || { echo "missing NPU microcode: $fw" >&2; exit 1; }
mkdir -p "$tree/firmware/zte/zx279133"
cp "$fw" "$tree/firmware/zte/zx279133/mcode_intel.bin"

# What the copy has to have produced.
[ -f "$tree/arch/arm64/boot/dts/zte/zx279133-sk-d840n.dts" ] || {
	echo "the board device tree did not reach the tree" >&2; exit 1; }
grep -q 'zx279133-sk-d840n.dtb' "$tree/arch/arm64/boot/dts/zte/Makefile" || {
	echo "the board dtb is not in the dts Makefile" >&2; exit 1; }
[ -f "$tree/drivers/mtd/nand/spi/unim.c" ] || {
	echo "the Unim NAND driver did not reach the tree" >&2; exit 1; }
grep -q 'ZX279133_RTL8372N_USER_PORT_MAX[[:space:]]*8' \
	"$tree/include/net/dsa_zx279133_rtl8372n.h" || {
	echo "the DSA transport-VID range was not widened to port 8" >&2; exit 1; }
[ -f "$tree/firmware/zte/zx279133/mcode_intel.bin" ] || {
	echo "the NPU microcode did not reach the tree" >&2; exit 1; }

# What the copy must NOT have produced: the 6.18 driver files are still
# absent, and the 7.3 driver is still the one that is there.
for leftover in \
	drivers/net/ethernet/zte/zx279133-pon.c \
	drivers/net/ethernet/zte/zxpon.c \
	drivers/net/ethernet/zte/zx279133-omci.c \
	drivers/net/ethernet/zte/zx279133-rgmii.c \
	drivers/net/phy/zte/phy-zx279133-uni-serdes.c ; do
	[ ! -e "$tree/$leftover" ] || {
		echo "the 6.18 driver file $leftover reached the 7.3 tree" >&2; exit 1; }
done
for kept in \
	drivers/net/ethernet/zte/zx279133.h \
	drivers/net/ethernet/zte/zx279133-host.c \
	drivers/net/ethernet/zte/zx279133-native.c \
	drivers/net/ethernet/zte/zx279133-offload.c \
	drivers/net/ethernet/zte/zx279133-idm.c \
	drivers/net/ethernet/zte/zx279133-offload-api.c \
	drivers/net/ethernet/zte/zx279133-switch.c \
	drivers/net/ethernet/zte/zx279133-lan1-led.h \
	drivers/net/ethernet/zte/zx279133-9132.h \
	drivers/phy/zte/phy-zx279133-uni-serdes.c ; do
	[ -f "$tree/$kept" ] || {
		echo "the 7.3 driver file $kept is missing from the tree" >&2; exit 1; }
done

echo "board layer applied to $tree; the 7.3 driver set is intact"
