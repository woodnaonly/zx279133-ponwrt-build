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
EOF

# The external PHY on this board is a Realtek RTL8226B. Evidence on the running unit:
# /lib/modules/4.19.136+/rlt8226b.ko, /sys/module/rlt8226b, dmesg "Start insmod
# R8226b_init!", and the DT property soc:pon_plat/mdio_8226_id = <1>, which names
# 14f02000.mdio (dmesg "MDIO id = 1") - the bus this PHY sits on, at address 5.
# cnjn's target config enables ZX279051_PHY and MARVELL_10G_PHY but has no Realtek
# driver at all, so phylib has nothing that can match the chip's read ID and the one
# port that matters cannot come up.
#
# Driver symbols belong in target/linux/<target>/<subtarget>/config-<version>, which is
# what OpenWrt merges into the kernel build; putting one in the top-level .config is
# dropped by defconfig in silence. That mistake cost a 7-minute Configure failure here
# rather than a 70-minute build with a dead port, which is the trade to remember.
KCFG=$(ls target/linux/zte/zx279133/config-* 2>/dev/null | head -1)
[ -n "$KCFG" ] || { echo "no target/linux/zte/zx279133/config-* to extend" >&2; exit 1; }
grep -q '^CONFIG_REALTEK_PHY=' "$KCFG" || printf '\nCONFIG_REALTEK_PHY=y\n' >>"$KCFG"
grep '^CONFIG_REALTEK_PHY=' "$KCFG" || { echo "CONFIG_REALTEK_PHY not in $KCFG" >&2; exit 1; }

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
# Same rule for the PHY driver, but it cannot be checked here: the top-level .config
# never carries driver symbols, so the real assertion is against the kernel's own
# .config under build_dir/ after the compile step.
