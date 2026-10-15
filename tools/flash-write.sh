#!/bin/sh
# Device-side flash worker, run detached from the ssh session that armed it.
#
# Why it is not just a line in flash-direct.sh: erasing and programming ~9 MB of SPI-NAND
# blocks the whole controller, which includes the jffs2 that / lives on, for a minute or
# more. A foreground pipe through ssh therefore dies at the worst possible moment, and an
# interrupted write leaves either a 4.19 kernel with a mainline dtb or the reverse - neither
# mounts root, and this board has no console and no recovery boot. Detached, it finishes
# whether or not the session survives, and it logs to RAM so the PC can read the outcome at
# its leisure.
#
# Run as: setsid sh /tmp/flash/flash-write.sh &
# Expected layout: /tmp/flash/{flash-uImage,flash-dtb.bin} already staged and md5-verified.
set -u
D=${D:-/tmp/flash}
# The sysfs and device-tree roots are variables so the whole worker can be run against a
# fake tree on the PC (tools/simwrite.sh). This is the script that erases the two partitions
# the board boots from, and it had never been executed anywhere before that.
MTDSYS=${MTDSYS:-/sys/class/mtd}
DEVP=${DEVP:-/dev}
LOG=$D/write.log
: >"$LOG"
say() { echo "$(cut -d. -f1 /proc/uptime) $*" >>"$LOG"; }

say "start kernel=$(uname -r) uptime=$(cat /proc/uptime)"
# Labels are resolved from sysfs rather than by grepping /proc/mtd, so that nothing here has
# to carry a nested quote, and so a label that is missing yields an abort instead of an empty
# device name that dd would happily open as /dev/.
dev_of() {
	for j in $MTDSYS/mtd*; do
		[ -r "$j/name" ] || continue
		[ "$(cat "$j/name")" = "$1" ] && { basename "$j"; return 0; }
	done
	return 1
}

KDEV=$(dev_of kernel) || { say "ABORT: no partition labelled kernel"; exit 1; }
DDEV=$(dev_of dtb) || { say "ABORT: no partition labelled dtb"; exit 1; }
KSZ=$(cat $MTDSYS/$KDEV/size); DSZ=$(cat $MTDSYS/$DDEV/size)
KOFF=$(cat $MTDSYS/$KDEV/offset); DOFF=$(cat $MTDSYS/$DDEV/offset)
KU=$(wc -c <$D/flash-uImage); KD=$(wc -c <$D/flash-dtb.bin)
say "kernel=/dev/$KDEV off=$KOFF size=$KSZ image=$KU"
say "dtb   =/dev/$DDEV off=$DOFF size=$DSZ image=$KD"
# Two arithmetic guards, because the failure mode of getting either wrong is not an error
# message: writing past a partition end silently continues into the *next* partition on the
# same chip, and on this map the partition after dtb is "parameter tags" - the MAC/calibration
# data, which nothing on this board can regenerate.
[ "$KU" -le "$KSZ" ] || { say "ABORT: uImage $KU > kernel partition $KSZ"; exit 1; }
[ "$KD" -le "$DSZ" ] || { say "ABORT: dtb $KD > dtb partition $DSZ"; exit 1; }
[ "$KOFF" = 2097152 ] && [ "$DOFF" = 10485760 ] || { say "ABORT: unexpected offsets k=$KOFF d=$DOFF"; exit 1; }

# mtd_debug first, the label tool only as a fallback. Both end up in the same MEMERASE/
# MEMWRITE ioctls, but mtd_debug takes an explicit offset and length, so what gets erased and
# what gets programmed is stated in this file rather than decided by whichever `mtd` applet
# the running image happens to carry (its size semantics for an image smaller than the
# partition are not knowable from here, and the 2026-10-09 flash of this board was done with
# mtd_debug + nandwrite, so that path is the one with a track record on this hardware).
if command -v mtd_debug >/dev/null 2>&1; then
	W="mtd_debug"
	say "tool=mtd_debug"
elif command -v mtd >/dev/null 2>&1; then
	W="mtd"
	say "tool=mtd (ubox, label-based fallback)"
else
	say "ABORT: neither mtd_debug nor mtd is on the running system"
	exit 1
fi

do_dt() {
	if [ "$W" = mtd ]; then
		mtd erase dtb >>"$LOG" 2>&1 && mtd write "$D/flash-dtb.bin" dtb >>"$LOG" 2>&1
	else
		mtd_debug erase $DEVP/$DDEV 0 $DSZ >>"$LOG" 2>&1 &&
			mtd_debug write $DEVP/$DDEV 0 $KD "$D/flash-dtb.bin" >>"$LOG" 2>&1
	fi
}
do_k() {
	if [ "$W" = mtd ]; then
		mtd erase kernel >>"$LOG" 2>&1 && mtd write "$D/flash-uImage" kernel >>"$LOG" 2>&1
	else
		mtd_debug erase $DEVP/$KDEV 0 $KSZ >>"$LOG" 2>&1 &&
			mtd_debug write $DEVP/$KDEV 0 $KU "$D/flash-uImage" >>"$LOG" 2>&1
	fi
}

# Mask the watchdog's reset routing for the duration of the write, the same way the kexec
# rounds do (CTL's MASK=1). The argument for not needing it is that zx_wdt on this vendor
# kernel is fed by an *in-kernel* timer, so a userspace stall cannot miss a feeding window -
# but the same reasoning has to hold for a full minute of the SPI controller being busy, and
# the cost of being wrong is a reset between the dtb write and the kernel write, which leaves
# a mainline dtb under a 4.19 kernel that cannot use it and no way to run anything on the
# board again. CRM 0x10e10208 = 0x1F1 is the vendor value; 0x0 masks the reset routing and
# the next boot's own driver puts it back.
WDT=""
if command -v devmem2 >/dev/null 2>&1 || [ -x /sbin/devmem2 ]; then
	WDT=$(command -v devmem2 || echo /sbin/devmem2)
	say "crm before masking: $($WDT 0x10e10208 w 2>&1 | tail -1)"
	$WDT 0x10e10208 w 0 >/dev/null 2>&1 && say "watchdog reset routing masked" || say "could not mask the watchdog - continuing on the in-kernel feeder argument"
else
	say "no devmem2 on this system - relying on the in-kernel watchdog timer"
fi

# dtb first, kernel last: until the kernel write completes the board still boots the old,
# self-consistent pair.
say "erase+write dtb"
do_dt; rc=$?
say "dtb rc=$rc"
say "erase+write kernel"
do_k; rc2=$?
say "kernel rc=$rc2"
sync
[ -n "$WDT" ] && $WDT 0x10e10208 w 0x1F1 >/dev/null 2>&1 && say "watchdog reset routing restored"

RD() { dd if=$DEVP/$1 bs=64k 2>/dev/null | head -c $2 | md5sum | cut -d" " -f1; }
[ "$(RD $DDEV $KD)" = "$(md5sum <$D/flash-dtb.bin | cut -d' ' -f1)" ] && say "READ_OK dtb" || say "READ_FAIL dtb"
[ "$(RD $KDEV $KU)" = "$(md5sum <$D/flash-uImage | cut -d' ' -f1)" ] && say "READ_OK kernel" || say "READ_FAIL kernel"
say "DONE"
