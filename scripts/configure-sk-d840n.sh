#!/bin/sh
# Configure ImmortalWrt for the Skyworth SK-D840N.
#   sh scripts/configure-sk-d840n.sh <openwrt-dir> <kernel-tree> <initramfs|flash>
# initramfs = same RAM-only FIT mode cnjn upstream ships (fast, needs a console or kexec)
# flash     = legacy uImage + separate dtb + jffs2 root on the community partition map
#             (what the board's existing U-Boot bootcmd boots with no serial cable)
set -eu

IW_DIR=${1:?openwrt dir}
KERNEL_TREE=${2:?kernel tree}
MODE=${3:?initramfs or flash}

[ -f "$KERNEL_TREE/Makefile" ] || { echo "invalid kernel tree: $KERNEL_TREE" >&2; exit 1; }
[ -f "$KERNEL_TREE/arch/arm64/boot/dts/zte/zx279133-sk-d840n.dts" ] || {
	echo "missing board dts in $KERNEL_TREE (kernel-overlays not applied?)" >&2; exit 1; }
grep -q 'zx279133-sk-d840n.dtb' "$KERNEL_TREE/arch/arm64/boot/dts/zte/Makefile" || {
	echo "board dts is not in arch/arm64/boot/dts/zte/Makefile" >&2; exit 1; }

cd "$IW_DIR"
[ -f configs/zte_zx279133_sr1010_initramfs.config ] || { echo "no sr1010 base config" >&2; exit 1; }
cp configs/zte_zx279133_sr1010_initramfs.config .config
printf 'CONFIG_EXTERNAL_KERNEL_TREE="%s"\n' "$(cd "$KERNEL_TREE" && pwd)" >>.config

cat >>.config <<EOF
# CONFIG_TARGET_zte_zx279133_DEVICE_zte_zxslc-sr1010 is not set
CONFIG_TARGET_zte_zx279133_DEVICE_skyworth_sk-d840n=y
# The external PHY on this board is a Realtek RTL8226B (running unit:
# /lib/modules/4.19.136+/rlt8226b.ko, /sys/module/rlt8226b, and the DT property
# soc:pon_plat/mdio_8226_id = 1, i.e. the bus at 14f02000). cnjn's
# target/linux/zte/zx279133/config-6.18 enables ZX279051_PHY and MARVELL_10G_PHY
# but no Realtek driver at all, so without this line phylib has nothing that can
# match the chip's read ID and the port cannot come up.
CONFIG_REALTEK_PHY=y
EOF

if [ "$MODE" = flash ]; then
	# Only jffs2 matters here: the root partition is written as a raw jffs2 image.
	# Leaving squashfs/targz on would emit extra -rootfs.jffs2 artifacts whose
	# payload is not jffs2 at all (Device/Build/image builds one per fs type).
	cat >>.config <<'EOF'
# CONFIG_TARGET_ROOTFS_INITRAMFS is not set
# CONFIG_TARGET_ROOTFS_SQUASHFS is not set
# CONFIG_TARGET_ROOTFS_TARGZ is not set
# CONFIG_TARGET_IMAGES_GZIP is not set
CONFIG_TARGET_ROOTFS_JFFS2=y
EOF
elif [ "$MODE" != initramfs ]; then
	echo "unknown mode: $MODE" >&2; exit 1
fi

make defconfig

echo "== resolved selection =="
grep -E 'CONFIG_TARGET_zte|CONFIG_EXTERNAL_KERNEL_TREE|CONFIG_TARGET_ROOTFS' .config || true
grep -q 'CONFIG_TARGET_zte_zx279133_DEVICE_skyworth_sk-d840n=y' .config || {
	echo "SK-D840N device was not selected by defconfig" >&2; exit 1; }
# A config line for a symbol that does not exist is dropped in silence, which is
# how a 70-minute build ended with no rootfs image at all. Verify the outcome
# instead of the intent.
if [ "$MODE" = flash ] && ! grep -q '^CONFIG_TARGET_ROOTFS_JFFS2=y' .config; then
	echo "CONFIG_TARGET_ROOTFS_JFFS2 is not enabled - the zte target needs jffs2 in FEATURES" >&2
	exit 1
fi
# Same rule for the PHY driver: no line, no driver, and the failure only shows up
# as a port that never links - which looks like a dead kernel from the outside.
grep -q '^CONFIG_REALTEK_PHY=y' .config || {
	echo "CONFIG_REALTEK_PHY was dropped by defconfig - RTL8226B cannot bind a driver" >&2
	exit 1
}
