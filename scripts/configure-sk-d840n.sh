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

# Reading a stalled board needs three things, and this build had none of them:
#   * MAGIC_SYSRQ - on 2026-10-16 the RAM trial printed "- generating board file -" and
#     then went fully mute: not one byte back for a break+w/t/l, and a ^C that used to be
#     echoed by the tty layer stopped being echoed too. Without sysrq there is no way to
#     tell "console output wedged" from "cpus gone", which is the only question that
#     decides whether to keep probing or to power-cycle.
#   * DETECT_HUNG_TASK + SOFTLOCKUP_DETECTOR - the kernel says out loud, 120 s in, which
#     task is stuck and in which function, instead of leaving us to infer it from the last
#     line of userspace output.
#   * IKCONFIG_PROC - /proc/config.gz was absent, so every guess about what this kernel
#     actually contains had to come from grepping the 28 MB Image.
# These are bring-up instruments, not features: they cost a little RAM and a taint flag on
# the hung-task print, and they are what makes the next silent round legible.
#
# What is known about whether they arrive: MAGIC_SYSRQ and MAGIC_SYSRQ_SERIAL do (the gate
# reads =y in the merged .config). DETECT_HUNG_TASK did not, in runs 28/29/30/31, and the
# reason is still not established - pin_y writes the line into the subtarget fragment, that
# fragment is the last thing merged, and DEBUG_KERNEL is =y in target/linux/generic, so the
# two candidate explanations (a dependency this tree turns off, or a merge order that beats
# the append) are not separable from the file being edited. The gate therefore reports these
# four instead of blocking on them, and prints the merged line either way; blocking here
# produced four red runs and no image to test the one thing that actually gates the trial.
pin_y MAGIC_SYSRQ
# The break path in the serial core is what makes a TTL line able to reach sysrq at all,
# and it is MAGIC_SYSRQ_SERIAL that compiles it (Kconfig: "Enable magic SysRq key over
# serial", default y once MAGIC_SYSRQ is on). cnjn's base config carries it as "is not set"
# only because MAGIC_SYSRQ was off, so this pin is insurance against a default moving -
# without it, a board with sysrq built in is still unreachable from the wire, which reads
# exactly like a dead board.
pin_y MAGIC_SYSRQ_SERIAL
pin_y DETECT_HUNG_TASK
pin_y SOFTLOCKUP_DETECTOR
pin_y IKCONFIG
pin_y IKCONFIG_PROC

# Reading MMIO from the board is the only way to compare a lane that works with a lane that
# does not, and on 2026-10-17 that comparison was impossible: /proc/config.gz (the symbol
# pinned just above is what made it readable) says "# CONFIG_DEVMEM is not set", so
# `mknod /dev/mem c 1 1` creates the node and `open()` still fails with ENXIO. The same
# round proved the initramfs has no `devmem` and no `od` applet, and that the nppt driver
# implements no ethtool register dump ("Cannot get register dump: Not supported"), so there
# was no path at all to a register value from inside Linux. CONFIG_STRICT_DEVMEM is off in
# this tree, so turning DEVMEM on leaves /dev/mem reads of MMIO allowed.
#
# Why this is pinned rather than reached by a default: every register reading this session
# that changed a conclusion came from U-Boot's `md`, i.e. from the one environment that had
# no way to be scriptable, reproducible or diffable. The driver-level claim under test now is
# that nppt programs the PON serdes/XPCS pair while this board's LAN lane is the UNI pair
# (main.c:429/434 take only "serdes" and "pcs-handle"; "lan-serdes"/zte,lan-pcs-handle are
# consumed only by the rtl8372n switch driver this board does not have) - and proving or
# disproving that from the running kernel needs /dev/mem.
pin_y DEVMEM

# CONFIG_PANIC_TIMEOUT=1 (cnjn's generic config) means a panic prints and then reboots one
# second later. Measured 2026-10-16: a ^C that killed /init in the RAM trial came back as a
# clean-looking U-Boot banner ~30 s later with nothing left to read, because the panic text
# had already scrolled past a reboot. Zero leaves the message on the console forever, which
# is the difference between "the kernel said why" and "the board is cycling".
sym=CONFIG_PANIC_TIMEOUT
if grep -q "^$sym=" "$KCFG"; then
	sed -i "s|^$sym=.*|$sym=0|" "$KCFG"
else
	printf '%s=0\n' "$sym" >>"$KCFG"
fi
grep -qx "$sym=0" "$KCFG" || { echo "could not pin $sym=0 in $KCFG" >&2; exit 1; }

# The board hangs at `Waiting for root device /dev/mtdblock7...`, and for a while that looked
# like the community U-Boot's `ft_fixup_flash-spinand`, because that function does print
# itself into the boot log and does own a `root=/dev/mtdblock%d rootfstype=%s` template. It
# is not the source. Three readings say so:
#   - "rootwait" is not a string anywhere in the boot partition (backups/mtd1_boot.bin);
#   - our built dtb carries no /chosen/bootargs at all;
#   - the literal `console=ttyAMA0,115200n8 root=/dev/mtdblock7 rootfstype=squashfs ro
#     rootwait` is inside the kernel image, next to the other early-params literals.
# So it is CONFIG_CMDLINE, inherited from the SR1010 profile, and SR1010's flash layout is
# not ours: on this board U-Boot's own table (`mtdparts` in env) is
# whole,boot,kernel,dtb,parameter tags,root -> root is mtdblock5, and our DTS pins rootpart
# to alias mtd5, so 5 is what mainline really creates.
#
# Both files are searched because a kernel symbol may live in the subtarget config, in the
# target's generic one, or in the profile copy under configs/. Which one is irrelevant to the
# sed and vital to the assertion afterwards: a leftover mtdblock7 anywhere in the tree boots
# a board that waits forever, and the two cases look identical from outside.
# Why the two clock flags are in the built-in line, and why they cannot be put anywhere else:
# the cold-boot log of this board reads (the 21:27 boot in staging/ctl/serial.log)
#   [    0.232717] zx279133-eth 19000000.ethernet: WAN netdev and LAN child devices registered
#   [    0.235546] clk: Disabling unused clocks
#   [    0.235606] PM: genpd: Disabling unused power domains
# and `clk_disable_unused` is a late_initcall: it reaps every clock the bootloader left enabled
# that no driver has *requested* by then. This lane's nppt/PPU/SerDes clocks are only taken
# inside its bring-up, which runs when the netdev is opened - seconds later in a RAM round (our
# `ip link set eth0 up`), and later still on a flashed boot. So the reap lands exactly between
# "driver registered" and "lane wanted", and what it can take away is the datapath U-Boot moved
# 11 MB through. It also matches the one shape we have measured from inside a booted kernel:
# run 50 counted `xmac_tx_good_frames 12` with not one RX descriptor - the MAC's clock claimed,
# the packet path's not.
#
# env `bootargs` cannot carry these flags: CONFIG_CMDLINE_FORCE=y means the bootloader's line
# never reaches the kernel (measured 2026-10-18 - `setenv bootargs ... clk_ignore_unused`, then
# `printenv bootargs` shows it and the kernel still prints the built-in one). So the only place
# this board's flags can live is here.
CMDLINE='console=ttyAMA0,115200n8 root=/dev/mtdblock5 rootfstype=jffs2 ro rootwait clk_ignore_unused pd_ignore_unused'
hits=$(grep -rl --include='config-*' '^CONFIG_CMDLINE=' target/linux/ 2>/dev/null || true)
[ -n "$hits" ] || { echo "no CONFIG_CMDLINE= in any target/linux/**/config-* - where does the built-in cmdline come from?" >&2; exit 1; }
for f in $hits; do
	printf 'patching built-in cmdline in %s\n' "$f"
	sed -i "s|^CONFIG_CMDLINE=.*|CONFIG_CMDLINE=\"$CMDLINE\"|" "$f"
done
left=$(grep -rn --include='config-*' 'mtdblock7' target/linux/ 2>/dev/null || true)
[ -z "$left" ] || { echo "a mtdblock7 cmdline survived: $left" >&2; exit 1; }
grep -rn --include='config-*' '^CONFIG_CMDLINE' target/linux/

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
HDR=$KERNEL_TREE/include/linux/mtd/spinand.h
[ -f "$SN/unim.c" ] || { echo "$SN/unim.c missing (kernel-overlays not applied?)" >&2; exit 1; }
[ -f "$HDR" ] || { echo "$HDR missing - the manufacturer externs live there" >&2; exit 1; }
# Three edits, not two. core.c's spinand_manufacturers[] refers to the symbol, and the only
# reason it compiles for the other vendors is that spinand.h declares each one extern:
# without that line the array entry is a plain compile error
#   core.c:1273: error: 'unim_spinand_manufacturer' undeclared here; did you mean
#   'xtx_spinand_manufacturer'?
# which is how run 22/23 died an hour into the kernel build (2026-10-15). Anchor on any
# sibling declaration rather than on `xtx_...` specifically, because a rename upstream would
# otherwise take this patch down with it - and assert each edit separately, because all three
# failures look identical on the board: a chip that is not in any table has no MTD devices.
grep -q 'unim_spinand_manufacturer;' "$HDR" || {
	# Not sed: `0,/regex/i` is a *range*, and a range address runs the command once per line
	# inside it, so the first rehearsal of this inserted the declaration 362 times - once
	# before every line up to the first sibling extern. awk with a latch inserts exactly once.
	awk '!done && /^extern const struct spinand_manufacturer /{
		print "extern const struct spinand_manufacturer unim_spinand_manufacturer;"
		done = 1
	}
	{print}' "$HDR" >"$HDR.new" && mv "$HDR.new" "$HDR"; }
grep -q '&unim_spinand_manufacturer' "$SN/core.c" ||
	sed -i '/spinand_manufacturers\[\] = {/a\
	&unim_spinand_manufacturer,' "$SN/core.c"
grep -q 'unim\.o' "$SN/Makefile" || printf 'spinand-objs += unim.o\n' >>"$SN/Makefile"
# Assert the outcome, not the intent: an unregistered manufacturer is indistinguishable
# from the -95 this patch exists to fix, and both show up as a board that never gets a
# root device.
grep -q '^extern const struct spinand_manufacturer unim_spinand_manufacturer;$' "$HDR" || {
	echo "spinand.h did not take the unim declaration" >&2; exit 1; }
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

# zrec - the browser upload+flash service - belongs in the RAM image and nowhere
# else. It is the answer to "I want to push an image and have it programmed", at
# the stage this board lets us control (there is no rebuildable U-Boot for
# ZX279133), and it is only reachable on the test network of an image that is
# already running from RAM. A flashed root must not carry a page that can erase
# kernel and root, so the symbol is pinned off outside initramfs mode.
if [ "$MODE" = initramfs ]; then
	printf 'CONFIG_PACKAGE_zrec=y\n' >>.config
else
	printf '# CONFIG_PACKAGE_zrec is not set\n' >>.config
fi

# rdreg belongs in both images. It is the register reader the driver's ethtool -S list cannot
# replace: /dev/mem read() answers EFAULT for MMIO on this SoC, `hexdump -s` gets the same
# EFAULT, and the shipped busybox has no devmem applet (measured 2026-10-19 on the run-62
# failsafe shell: "devmem: applet not found", `grep -c devmem /bin/busybox` = 0). Until now the
# only in-kernel view of a register was the ~13 addresses a build happens to name in
# netdev.c's stat table, so every new address cost a 70-minute build. mmap is what makes it
# work, and that is also why this is a package rather than a shell recipe.
# Unlike zrec it cannot touch flash, so the released root can carry it.
printf 'CONFIG_PACKAGE_rdreg=y\n' >>.config

make defconfig

# The same rule that this file already states twice: a config line for a symbol
# that does not exist is dropped by defconfig in silence. Assert the outcome.
if [ "$MODE" = initramfs ]; then
	grep -qx 'CONFIG_PACKAGE_zrec=y' .config || {
		echo "CONFIG_PACKAGE_zrec did not survive defconfig - is package/utils/zrec/Makefile in the tree?" >&2
		ls -d package/utils/zrec 2>/dev/null || echo "package/utils/zrec is NOT in the tree" >&2
		echo "--- what defconfig knows about zrec ---" >&2
		grep -i 'zrec' .config || echo "(nothing at all)" >&2
		exit 1; }
elif grep -q '^CONFIG_PACKAGE_zrec=y' .config; then
	echo "flash mode selected zrec - the released root must not ship a flasher" >&2
	exit 1
fi

# Same assertion, same reason: a symbol defconfig does not know is dropped without a word, and
# the build that costs 70 minutes is the one that finds out.
grep -qx 'CONFIG_PACKAGE_rdreg=y' .config || {
	echo "CONFIG_PACKAGE_rdreg did not survive defconfig - is package/utils/rdreg/Makefile in the tree?" >&2
	ls -d package/utils/rdreg 2>/dev/null || echo "package/utils/rdreg is NOT in the tree" >&2
	grep -i 'rdreg' .config || echo "(nothing at all)" >&2
	exit 1; }

# CONFIG_DEVMEM lost three rounds, and the winner is not the fragment merge. Read out of
# include/kernel-defaults.mk, define Kernel/Configure/Default:
#
#   $(LINUX_CONF_CMD) > $(LINUX_DIR)/.config.target
#   awk '/^(#[[:space:]]+)?CONFIG_KERNEL/{sub("CONFIG_KERNEL_","CONFIG_");print}' \
#	$(TOPDIR)/.config >> $(LINUX_DIR)/.config.target
#   $(SCRIPT_DIR)/kconfig.pl 'm+' '+' $(LINUX_DIR)/.config.target /dev/null \
#	$(LINUX_DIR)/.config.override > $(LINUX_DIR)/.config.set
#
# Every CONFIG_KERNEL_* line of the build-root .config is re-emitted with the prefix
# stripped and appended to .config.target - after generic/target/subtarget, because
# LINUX_CONF_CMD wrote those first. An appended "# CONFIG_DEVMEM is not set" therefore
# beats whatever the fragments pin, which is why three seed-side asserts went green while
# the shipped Image kept saying "is not set" in its own IKCFG_ST block. config/
# Config-kernel.in declares `config KERNEL_DEVMEM` with no default, so defconfig writes
# the "is not set" form here unless it is switched on in the file the awk reads: the
# build-root .config, after `make defconfig`. The neighbouring ZTE board's own build-root
# config (upstream-read/an7581.config:435) says `CONFIG_KERNEL_DEVMEM=y` - the community
# reached /dev/mem through this symbol, not through a fragment.
pin_y_build() {
	sym=CONFIG_$1
	if grep -q "^$sym=" .config; then
		sed -i "s|^$sym=.*|$sym=y|" .config
	elif grep -q "^# $sym is not set\$" .config; then
		sed -i "s|^# $sym is not set\$|$sym=y|" .config
	else
		printf '%s=y\n' "$sym" >>.config
	fi
	grep -qx "$sym=y" .config || {
		echo "could not pin $sym=y in the build-root .config" >&2
		grep -n "$sym" .config || echo "	$sym does not appear there at all" >&2
		exit 1; }
}
pin_y_build KERNEL_DEVMEM
# The applet as well: kernel /dev/mem with no `devmem` in busybox still means mknod + dd,
# which reads one register per invocation. Only pin it if defconfig knows the symbol -
# appending a line for a symbol that is not there would assert green and ship nothing, and
# the only proof that counts is `devmem` answering on the board.
if grep -qE '^(# )?CONFIG_BUSYBOX_CONFIG_DEVMEM' .config; then
	pin_y_build BUSYBOX_CONFIG_DEVMEM
else
	echo "note: no CONFIG_BUSYBOX_CONFIG_DEVMEM in the build-root config; the RAM image keeps mknod+dd" >&2
fi

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
