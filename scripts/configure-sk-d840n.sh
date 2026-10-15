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

# pin_y <symbol>: make it =y in the target config whatever form the line is in (=m, "is
# not set", or absent). Writing only the =y form is not enough - appending a second line
# for a symbol the file already carries leaves the merge order to decide. The name is
# passed without the CONFIG_ prefix, because prefixing it twice produces a symbol that
# does not exist, and a symbol that does not exist is dropped by defconfig in silence:
# the first version of this helper wrote CONFIG_CONFIG_REALTEK_PHY=y and self-verified it
# by looking for the same misspelling.
pin_y() {
	sym=CONFIG_$1
	if grep -q "^$sym=" "$KCFG"; then
		sed -i "s|^$sym=.*|$sym=y|" "$KCFG"
	elif grep -q "^# $sym is not set$" "$KCFG"; then
		sed -i "s|^# $sym is not set\$|$sym=y|" "$KCFG"
	else
		printf '%s=y\n' "$sym" >>"$KCFG"
	fi
	grep -qx "$sym=y" "$KCFG" || { echo "could not pin $sym=y in $KCFG" >&2; exit 1; }
}
pin_y REALTEK_PHY

# The panel beacon needs the LED class devices to exist *in the RAM image*, and =m means
# they only appear if something loads kmod-leds-gpio first - which is exactly the
# userspace that the beacon is there to prove. So these two are pinned built-in: without
# LEDS_GPIO there is no /sys/class/leds entry per DTS node, and without the GPIO driver
# there is no /sys/class/gpio either, leaving the trial with no signal at all. (cnjn's
# config already has both =y; pinning them keeps that true if upstream changes it.)
pin_y LEDS_GPIO
pin_y GPIO_ZX279133

# pstore/ramoops is the closest thing this board can have to a console: the log goes into
# DRAM, a warm reset keeps it, and the flashed system dumps the region back out. Without
# it every failed round teaches us nothing beyond "it did not come back". ImmortalWrt's
# generic config ships "# CONFIG_PSTORE is not set", so all three are pinned here; the
# board DTS carries the matching no-map ramoops region.
pin_y PSTORE
pin_y PSTORE_CONSOLE
pin_y PSTORE_RAM

# The flash on this board is a Unim UM19A1HISW (READ ID b0 24 b0 24 b0) and no spi-nand
# table in this tree - or in mainline - has manufacturer 0xb0, so the driver rejects the
# chip and the board ends up with no MTD devices at all:
#   spi-nand spi0.0: unknown raw ID b024b024b0
#   spi-nand spi0.0: probe with driver spi-nand failed with error -95
# unim.c arrives through kernel-overlays; these two edits are what register it. They are
# done as targeted insertions rather than by shipping whole copies of core.c/Makefile
# because both of those are upstream-owned, and a stale copy of a 49 kB file wins silently
# and would take the flash with it.
SN=$KERNEL_TREE/drivers/mtd/nand/spi
[ -f "$SN/unim.c" ] || { echo "$SN/unim.c missing (kernel-overlays not applied?)" >&2; exit 1; }
grep -q 'unim_spinand_manufacturer' "$SN/core.c" ||
	sed -i '/spinand_manufacturers\[\] = {/a\
	&unim_spinand_manufacturer,' "$SN/core.c"
grep -q 'unim\.o' "$SN/Makefile" || printf 'spinand-objs += unim.o\n' >>"$SN/Makefile"
# Assert the outcome, not the intent: an unregistered manufacturer is indistinguishable
# from the -95 this patch exists to fix, and both show up as a board that never gets a
# root device.
grep -q '&unim_spinand_manufacturer,' "$SN/core.c" || {
	echo "core.c did not take the unim manufacturer" >&2; exit 1; }
grep -q 'unim\.o' "$SN/Makefile" || {
	echo "spi-nand Makefile did not take unim.o" >&2; exit 1; }
grep -E '^CONFIG_(REALTEK_PHY|LEDS_GPIO|GPIO_ZX279133|GPIO_SYSFS|PSTORE)' "$KCFG"

# cnjn's target config embeds the SoC's microcode into the kernel image, and points at
# it with a path that is only correct for the directory names on his own machine:
#   CONFIG_EXTRA_FIRMWARE="zte/zx279133/mcode_intel.bin"
#   CONFIG_EXTRA_FIRMWARE_DIR="../../immortalwrt/target/linux/zte/zx279133/firmware"
# This CI checks the tree out as `openwrt`, so the kernel build died in both modes with
# "No rule to make target '../../immortalwrt/...mcode_intel.bin'" (run 38193151039,
# 2026-10-12). The fix rewrites the value to this checkout, rather than clearing
# EXTRA_FIRMWARE: a kernel that lacks the microcode is precisely the kind of change that
# would leave the NPPT port dead and give no clue why. If upstream ever drops the symbol
# the block does nothing, and if it comes back with a different relative path the sed
# still pins it here.
if grep -q '^CONFIG_EXTRA_FIRMWARE=' "$KCFG"; then
	FWDIR=$(pwd)/target/linux/zte/zx279133/firmware
	[ -f "$FWDIR/zte/zx279133/mcode_intel.bin" ] || {
		echo "CONFIG_EXTRA_FIRMWARE is set but $FWDIR/zte/zx279133/mcode_intel.bin is absent" >&2
		exit 1; }
	grep -q '^CONFIG_EXTRA_FIRMWARE_DIR=' "$KCFG" || printf 'CONFIG_EXTRA_FIRMWARE_DIR=""\n' >>"$KCFG"
	sed -i "s|^CONFIG_EXTRA_FIRMWARE_DIR=.*|CONFIG_EXTRA_FIRMWARE_DIR=\"$FWDIR\"|" "$KCFG"
	grep -E '^CONFIG_EXTRA_FIRMWARE' "$KCFG"
fi
# GPIO_CDEV_V1 used to be appended here on the belief that it gates
# /sys/class/gpio/export. It does not: that interface is CONFIG_GPIO_SYSFS, which
# cnjn's target config already sets. GPIO_CDEV_V1 only selects the v1 ioctl ABI on
# /dev/gpiochipN, which nothing in the image uses, so it is not a config delta this
# board needs - and the trial beacon drives the panel through /sys/class/leds and
# /sys/class/gpio instead.
grep -E '^CONFIG_(REALTEK_PHY|GPIO_SYSFS)' "$KCFG"

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
