#!/usr/bin/env python3
"""Build the control round's initramfs. One job: pulse the panel in a rhythm the flashed
boot never produces, so "the kexec'd kernel reached userspace" becomes something a person
can look at at leisure instead of something inferred from a blackout.

Why an initrd: every payload tried so far mounted the flashed jffs2 root, which needs
7.18-7.24 s to reach its own zx_wdt feed while the armed heartbeat is 8 s - those rounds
were decided by milliseconds, not by the handoff. An initrd reaches /init in 1-2 s and
needs no driver, no mount and no network to announce itself.

Why a lamp and not a byte in RAM (measured 2026-10-12): a tag stamped into the vendor
dtb's own reserved pool - 0x83d68f00, the zeroed tail of `80b23000-83d68fff : reserved` -
survived 30 s of normal operation and came back ALL ZEROS after one warm reset. The PON
driver re-initialises that window every boot, so a RAM mailbox is not durable on this
board; tools/stampcheck.py is what proved it, before a round was spent on the idea.

Why root=/dev/ram0, with no jffs2 fallback kept in the command line: when the initrd is
honoured, /init is PID 1 and the flashed procd never runs. Leaving root=/dev/mtdblock5 in
the line only matters if the kernel ignores the initrd, and procd's boot() on a ram
overlay is exactly what issues `reboot -f` - a reboot through U-Boot reads the same as a
failed handoff. The vendor Image does carry the initramfs unpacker (strings
`init/initramfs.c`, `Trying to free ramdisk memory ...`, `junk in compressed archive`,
`incorrect cpio method`), so the initrd is honoured and the round keeps one signal.

newc cpio by hand because this PC has no cpio binary: "070701" + 13 hex fields + name +
pad-to-4 + data (or symlink target) + pad, closed by TRAILER!!!. The device's own busybox
and both libraries it links are used, so the binary is one this kernel has already run.

  python tools/mkctlram.py --tag K4
"""
import argparse
import gzip
import hashlib
import os
import re
import stat
import sys

INIT_TMPL = r"""#!/bin/sh
# Control round /init: runs 1-2 s after a kexec jump on the vendor 4.19, from a RAM root
# holding nothing but busybox. The round is fired with the CRM reset path masked, so this
# loop IS the state the board stays in until power is cycled - the signal has no deadline,
# which is the entire point of using an initrd instead of the flashed root.
PATH=/bin:/sbin
TAG={tag}

mount -t proc proc /proc 2>/dev/null
mount -t sysfs sysfs /sys 2>/dev/null
mount -t devtmpfs devtmpfs /dev 2>/dev/null
# Report whether sysfs actually came up, and retry it out loud if it did not.
# model/leds/netdevs came back blank in every earlier round while the /proc-backed fields
# were fine, which is ONE failed mount explaining all three at once rather than three
# findings: /proc/device-tree is a symlink into /sys/firmware/devicetree/base, so it dies
# with sysfs too. A blank model must not be filed as "this dtb has no model" - it is what a
# dead sysfs looks like, and it is the whole reason the eth0 question could not be closed
# from a round. Made a reported field so the next round cannot be ambiguous either way.
MOUNTS=$(grep " /sys " /proc/mounts 2>/dev/null)
if [ -z "$MOUNTS" ]; then
	SYSERR=$(mount -t sysfs sysfs /sys 2>&1 | tr " " "_")
	MOUNTS=$(grep " /sys " /proc/mounts 2>/dev/null)
fi
if [ -n "$MOUNTS" ]; then SYSFS_STATE=mounted; else SYSFS_STATE="NOT-MOUNTED:${{SYSERR:-no-output}}"; fi
# The archive carries five directories, and everything else this script writes into is created
# here. Without the line, `echo ... >/tmp/mb1` fails behind its own 2>/dev/null and the stage it
# belongs to simply never appears in the region - indistinguishable from a payload that did not
# start. Found by reading the record list rather than by the sim, because this PC has a /tmp.
mkdir /tmp /www /mnt 2>/dev/null

U=$(cut -d. -f1 /proc/uptime)
echo "ctl: /init alive tag=$TAG uptime=$U kver=$(uname -r)" >/dev/kmsg 2>/dev/null

# RAM mailbox: the report channel that needs no jack, no pstore and no observer.
#
# The PC only ever reaches this board on 192.168.9.1, which is the lan bridge, and a mainline
# payload has no lan bridge - it has eth0 and whatever the switch driver binds to, and the
# switch driver is not in this kernel. So every TCP report this project has pushed went to a
# cable in a port the payload cannot send on, and the only other channel - pstore - carries
# exactly what a *registered* pstore wrote, which is a claim about driver probe order rather
# than about the payload reaching userspace. Writing the report straight into RAM with dd
# answers the actual question ("did /init run, and what did it see") with no driver in the path.
#
# Several addresses, because the address that is safe for mainline is not safe until the read.
# 0x9c000000-0x9dffffff is no-map in our dts, so mainline cannot reuse it and no prz of pstore's
# reaches 0x180000 in - but the installed 4.19 does not reserve it, and round K9MB (2026-10-13)
# read it at uptime 77 s to find the vendor system's own page cache in it (fw4 template text,
# jffs2 format strings), which is mainline's bytes gone whichever channel wrote them. The four
# extra addresses below are the ones a marker test measured to hold written content across a
# normal reboot *and* a late read; they are not reserved in our dts, so mainline can take them
# back after the write, which is why every stage of the report is rewritten on each beacon cycle
# - the copy that survives is the freshest one, and the eject is only seconds away.
#
# Gated twice over, because this same /init also runs on vendor-4.19 control rounds where all of
# this RAM is live: only a 6.x kernel writes at all (the kernel that has nothing left to corrupt
# but its own round), and the ramoops window additionally requires the reservation to be really
# there. mbox_write names which gate stopped it.
MBW=2557440
MBX="0x90000000 0x94000000 0x98000000 0x9d000000"
PAYLOAD=0
MBSTATE=off-kernel-is-not-the-payload
case "$(uname -r)" in
6.*) PAYLOAD=1
     grep -qi '9c000000-9dffffff' /proc/iomem 2>/dev/null && MBSTATE=ARMED
     [ "$MBSTATE" = ARMED ] || MBSTATE=off-window-not-reserved;;
esac
# mbox <file> <stage>: one 4 KiB block per address, stages 4 blocks apart inside each region.
mbox() {{
	[ "$MBSTATE" = ARMED ] || return 0
	n=$(( $2 * 2 ))
	dd if="$1" of=/dev/mem bs=1024 seek=$(( MBW + n )) count=4 2>/dev/null
	for A in $MBX; do
		dd if="$1" of=/dev/mem bs=1024 seek=$(( A / 1024 + n )) count=4 2>/dev/null
	done
}}
echo "CTLALIVE tag=$TAG mbox=early uptime=$U kver=$(uname -r)" >/tmp/mb1 2>/dev/null
echo "mbnote=/init started, nothing has been read from flash yet" >>/tmp/mb1 2>/dev/null
echo "mbaddrs: $MBW $MBX" >>/tmp/mb1 2>/dev/null
mbox /tmp/mb1 0
# Read the first bytes back so the round says whether /dev/mem accepted the write: a kernel
# built without CONFIG_DEVMEM otherwise looks exactly like a payload that never started.
MBRB=""
[ "$MBSTATE" = ARMED ] && MBRB=$(dd if=/dev/mem bs=1024 skip=$MBW count=1 2>/dev/null | head -c 32 | tr -dc 'A-Za-z0-9=_ ')
echo "ctl: tag=$TAG mailbox=$MBSTATE readback=$MBRB" >/dev/kmsg 2>/dev/null
# /dev/kmsg is what puts the report into pstore's console prz. devtmpfs normally creates it,
# but if this kernel has no devtmpfs the node is simply missing and the kmsg channel dies
# quietly behind a 2>/dev/null - which is exactly the ambiguity this round exists to remove.
[ -e /dev/kmsg ] || mknod /dev/kmsg c 10 11 2>/dev/null

# Buy the round some time without asking anyone to sit at the power switch.
#
# The jump is fired with the vendor CRM reset routing (0x10e10208 = 0x1F1) untouched, which is
# what makes a silent payload self-recovering: the zx_wdt that 4.19 armed and fed is left
# unfed, bites ~8 s after the jump and warm-resets back into the flashed system. But 8 s is also
# the whole budget the report has to fit in, and building a jffs2 node list for the 240 MiB root
# does not fit in it - the vendor kernel needed 7.2 s just to reach its own watchdog feed.
#
# So the payload masks that routing itself, as its first act past the mailbox, and hands it back
# on a timer. Each branch stays recoverable without a hand: /init never runs, the armed watchdog
# ejects exactly as before; /init runs and hangs, the timer puts the routing back and the same
# watchdog ejects ~3 min in; /init reaches its beacon loop, the loop restores and reboots.
# Three minutes rather than the minimum needed is the human's observation window: the beacon is
# the only sensor on this board that works with no jack, no /dev/mem and no pstore, so it has to
# be running long enough for someone at the panel to answer "did the lamps ever move". The one
# case that needs someone to pull power is a kernel that never reaches user space at all - and
# that is precisely the question the round is asking, so it is the answer worth having.
#
# Written with dd rather than devmem2 because devmem2 is not in this archive, and gated on the
# same "this is the payload kernel" test as the mailbox: masking the routing from a *running*
# vendor system is how a control round ends with nobody able to get the board back.
CRM=$(( 0x10e10208 / 4 ))
printf '\000\000\000\000' >/tmp/z4 2>/dev/null
printf '\361\001\000\000' >/tmp/f14 2>/dev/null
crm() {{
	[ "$PAYLOAD" = 1 ] || return 0
	dd if="$1" of=/dev/mem bs=4 seek=$CRM count=1 2>/dev/null
}}
if [ "$PAYLOAD" = 1 ]; then
	( n=0
	  while [ $n -lt 180 ]; do n=$(( n + 1 )); sleep 1; done
	  crm /tmp/f14
	  sync >/dev/null 2>&1
	  # Last-resort log flush, and the one channel that needs no /dev/mem at all: panicking
	  # makes the kernel kmsg_dump() its whole log buffer into pstore's kmsg prz, so a payload
	  # that started but never finished still leaves a boot log behind to read - which is the
	  # difference between "the ethernet stack ate it" and a round that says nothing at all.
	  echo 1 >/proc/sysrq-trigger 2>/dev/null
	  echo c >/proc/sysrq-trigger 2>/dev/null
	  reboot -f >/dev/null 2>&1
	  echo 1 >/proc/sysrq-trigger 2>/dev/null
	  echo b >/proc/sysrq-trigger 2>/dev/null ) &
	crm /tmp/z4
fi
CRMSET=$(dd if=/dev/mem bs=4 skip=$CRM count=1 2>/dev/null | hexdump -e '1/4 "%08x"')
echo "ctl: tag=$TAG crm_now=$CRMSET payload=$PAYLOAD deadline=180s" >/dev/kmsg 2>/dev/null

# zx-leds-gpio is built into this kernel (it binds soc:leds and soc:leds_5 as platform
# drivers), so the class is there before /init runs. /sys/class/gpio does NOT exist on
# this kernel (no CONFIG_GPIO_SYSFS; only /dev/gpiochipN), so there is no raw-pin fallback
# worth writing: if the LED class is missing, the round reports dark and the reason is in
# the log instead of guessed at afterwards.
# Two channels, so the round does not depend on anyone being at the panel:
#
#  a) blink. Do NOT hard-code one name: the vendor class is led_power while our board DTS
#     names the same lamp through LED_FUNCTION/colour (green:power), so a payload on
#     mainline would have no writable led_power and the round would read as dead even if
#     it booted. Walk whatever the class exposes instead, one lamp per cycle.
#  b) answer on the wire. Give every netdev the test /24 and start a telnet shell, which
#     tools/catchctl.sh can score from the PC without an observer.
for L in /sys/class/leds/*/brightness; do
	[ -w "$L" ] || continue
	echo 255 >$L 2>/dev/null
done
sleep 10
for L in /sys/class/leds/*/brightness; do
	[ -w "$L" ] || continue
	echo 0 >$L 2>/dev/null
done
sleep 5

for N in /sys/class/net/*; do
	D=$(basename $N)
	[ "$D" = lo ] && continue
	ifconfig $D 192.168.9.1 netmask 255.255.255.0 up 2>/dev/null ||
		ip addr add 192.168.9.1/24 dev $D 2>/dev/null; ip link set $D up 2>/dev/null
done
mkdir /www 2>/dev/null
# ECC/OOB compatibility check, and the reason this payload exists beyond "did it boot":
# the flash was written by the vendor 4.19 driver with its own ECC scheme, and mainline's
# spi-nand driver has to read the same bytes the same way or the jffs2 it mounts at real
# boot time will be garbage. mtdblock reads go through exactly that path without touching
# the filesystem, so md5 of the first 512 KiB of root is comparable across the two kernels.
# tools/roothead.sh records the 4.19 side; tools/gatewatch.sh compares.
ROOT512=$(dd if=/dev/mtdblock5 bs=64k count=8 2>/dev/null | md5sum | cut -d" " -f1)
JFS=$(dd if=/dev/mtdblock5 bs=4k count=128 2>/dev/null | wc -c)
mbox_part() {{
	echo "CTLALIVE tag=$TAG mbox=$1 uptime=$(cut -d. -f1 /proc/uptime)" >/tmp/mbp 2>/dev/null
	echo "kver=$(uname -r)" >>/tmp/mbp 2>/dev/null
	echo "cmdline: $(cat /proc/cmdline)" >>/tmp/mbp 2>/dev/null
	echo "netdevs: $(ls /sys/class/net)" >>/tmp/mbp 2>/dev/null
	echo "leds: $(ls /sys/class/leds)" >>/tmp/mbp 2>/dev/null
	echo "root_mtdblock5_first512k_md5=$ROOT512" >>/tmp/mbp 2>/dev/null
	echo "root_bytes_read=$JFS" >>/tmp/mbp 2>/dev/null
	echo "dev:    size   erasesize  name" >>/tmp/mbp 2>/dev/null
	cat /proc/mtd >>/tmp/mbp 2>/dev/null
	mbox /tmp/mbp $2
}}
# Everything before the jffs2 mount, in the window, now. The mount is the one step the payload
# cannot promise a budget for - building a jffs2 node list for a 241 MiB partition took 7.2 s on
# the vendor kernel, and with the reset routing left at the vendor value this payload has only
# the armed watchdog's few seconds. Which of the three blocks is readable after the round is
# what says where the time went, instead of leaving that to be guessed at from silence.
mbox_part partial 1
# Reading the bytes and mounting them are two different claims, and only the second one is what
# the real boot needs: U-Boot hands this jffs2 to mainline's jffs2 as /. The hash above can say
# IDENTICAL while the filesystem still fails to build its node lists (a jffs2 that new code
# rejects, a flash whose ECC passes but whose raw nodes do not), and that failure appears after
# the flash, with no console. So mount it here, look for a bootable userspace behind it, and
# unmount. Read-only on purpose: this is the root the *installed* system runs from, and an
# rw mount lets mainline's jffs2 start writing/erasing while the round is still deciding.
mkdir /mnt 2>/dev/null
RO=FAIL
US=none
if mount -t jffs2 -o ro /dev/mtdblock5 /mnt 2>/tmp/ro.err; then
	RO=OK
	MERR=-
	if [ -x /mnt/sbin/init ] && [ -x /mnt/bin/sh ]; then US=init+sh
	elif [ -x /mnt/sbin/init ]; then US=init-no-shell
	elif [ -x /mnt/bin/sh ]; then US=shell-no-init
	else US=empty-or-no-userspace
	fi
	# Two things the *next* boot depends on that only the mounted root can answer, read here
	# because the round that can read them is this one: dropbear bound to an Interface means a
	# mainline board with no lan bridge has no shell at all, and the module directory says
	# whether the installed kmods can even load under the kernel being flashed.
	DBIND=$(grep -c "Interface" /mnt/etc/config/dropbear 2>/dev/null)
	# Trimmed with sed rather than shell parameter expansion: this body goes through
	# str.format(), and a brace pair anywhere in it - even in a comment - is a placeholder.
	KMOD=$(ls /mnt/lib/modules 2>/dev/null | head -4 | tr "\n" " " | sed "s/ $//")
	REL=$(sed -n "s/DISTRIB_DESCRIPTION='\(.*\)'/\1/p" /mnt/etc/openwrt_release 2>/dev/null | tr " " "_" | head -1)
	[ -n "$DBIND" ] || DBIND=none
	[ -n "$KMOD" ] || KMOD=none
	[ -n "$REL" ] || REL=none
	ls /mnt >/tmp/ro.ls 2>/dev/null
else
	DBIND=nomount
	KMOD=nomount
	REL=nomount
	MERR=$(sed -n 1p /tmp/ro.err 2>/dev/null | tr " " "_")
	[ -n "$MERR" ] || MERR=no-message
fi
umount /mnt 2>/dev/null
# Read the interfaces a second way, on purpose. /proc/net/dev is filled from the kernel's own
# device list and never touches sysfs, so a non-empty result here proves interfaces exist even
# in a round where sysfs could not be mounted. That is the whole difference between "the driver
# registered nothing" and "we were unable to look", which earlier rounds could not state.
NETDEV_SYS=$(ls /sys/class/net 2>/dev/null | tr "\n" " ")
# Never leave a field blank: a blank and "we could not look" are different facts, and
# conflating them is what made every earlier round unreadable.
[ -n "$NETDEV_SYS" ] || NETDEV_SYS=none
NETDEV_PROC=$(awk "NR>2 {{sub(/:$/,\"\"); printf \"%s \", \$1}}" /proc/net/dev 2>/dev/null)
[ -n "$NETDEV_PROC" ] || NETDEV_PROC=none
# /sys/firmware/devicetree/base/model is what /proc/device-tree points at. Read the real path so
# a symlink problem cannot masquerade as a blank model a second time.
DTMODEL=$(cat /sys/firmware/devicetree/base/model 2>/dev/null | tr "\n" "_")
[ -n "$DTMODEL" ] || DTMODEL=none
# Which platform driver, if any, claimed the ethernet node. /sys/bus/platform/devices holds one
# entry per bound device and each has an of_node symlink; match on that OF path. The interface
# NAME this driver hands out is what 02_network hardcodes as "eth0", so "did a driver claim the
# node" and "what will it be called" are the two facts that decide whether a flashed board is
# reachable at all - and until now neither had ever been read off a board.
ETHBOUND=$(for d in /sys/bus/platform/devices/*; do
	[ -e "$d/of_node" ] || continue
	t=$(readlink -f "$d/of_node" 2>/dev/null)
	case "$t" in
	*/soc/ethernet@*) printf "%s " "$(basename "$d")";;
	esac
done)
[ -n "$ETHBOUND" ] || ETHBOUND=none
{{ echo "CTLALIVE tag=$TAG"; echo "kver=$(uname -r)"; echo "uptime=$(cut -d. -f1 /proc/uptime)"; echo "sysfs: $SYSFS_STATE"; echo "model: $(cat /proc/device-tree/model)"; echo "devtree_model: $DTMODEL"; echo "leds: $(ls /sys/class/leds 2>/dev/null | tr "\n" " ")"; echo "netdevs: $NETDEV_SYS"; echo "netdevs_proc: $NETDEV_PROC"; echo "eth_bound_driver: $ETHBOUND"; echo "cmdline: $(cat /proc/cmdline)"; echo "root_mtdblock5_first512k_md5=$ROOT512"; echo "root_bytes_read=$JFS"; echo "jffs2_ro_mount=$RO"; echo "jffs2_userspace=$US"; echo "jffs2_err=$MERR"; echo "installed_dropbear_Interface=$DBIND"; echo "installed_kmods=$KMOD"; echo "installed_release=$REL"; echo "jffs2_top: $(cat /tmp/ro.ls 2>/dev/null | tr "\n" " ")"; echo; echo "-- mtd as this kernel registers it (index = /dev/mtdblockN) --"; cat /proc/mtd; }} >/www/index.html 2>/dev/null
# The finished report goes four blocks past the early marker, so a round that started and then
# stopped (a flash read that never returns is the candidate) leaves the early block readable
# while the final one is still whatever the loader left there. The two mbox lines are appended
# before the copy goes to /dev/kmsg so the pstore version of the report carries them too.
{{ echo "mbox_write=$MBSTATE"; echo "mbox_readback=$MBRB"; echo "crm_now=$CRMSET"; echo "mbnote=window block $MBW, extras $MBX"; }} >>/www/index.html 2>/dev/null
cat /www/index.html >/dev/kmsg 2>/dev/null
mbox /www/index.html 2
# Third channel into the same region: pstore's pmsg prz. It needs ramoops to have probed but
# not the console to have registered, so a kernel that booted with CONFIG_DEVMEM off still
# reports here. tools/pstore2report.py greps the whole 2 MiB and finds it either way.
cat /www/index.html >/dev/pmsg0 2>/dev/null
# This busybox has no httpd and no telnetd - checked by scanning the exact binary the
# initrd carries: tftp, nc, ping and logger are in it, httpd/telnetd are not. A payload
# therefore cannot be *queried*, it has to push: one short TCP connection per report,
# repeated, so a PC that arrives late still catches one and a round is not lost to timing.
PC=$(sed -n 's/.*ctlpc=\([^ ]*\).*/\1/p' /proc/cmdline 2>/dev/null)
[ -n "$PC" ] || PC=192.168.9.190
echo "ctl: tag=$TAG reporting to $PC:5555 kver=$(uname -r)" >/dev/kmsg 2>/dev/null
i=0
while [ $i -lt 8 ]; do
	i=$(( i + 1 ))
	nc $PC 5555 </www/index.html 2>/dev/null
	sleep 3
done

n=0
while :; do
	n=$(( n + 1 ))
	for L in /sys/class/leds/*/brightness; do
		[ -w $L ] || continue
		echo 255 >$L 2>/dev/null
	done
	# Rewrite the mailbox on every cycle. Four of the five addresses are not reserved in our dts,
	# so mainline's own allocator can take them back at any moment, and the copy that outlives the
	# round is the last one written before the reset - not the one written minutes earlier.
	mbox /www/index.html 2
	sleep 10
	for L in /sys/class/leds/*/brightness; do
		[ -w $L ] || continue
		echo 0 >$L 2>/dev/null
	done
	nc $PC 5555 </www/index.html 2>/dev/null
	sleep 5
	# Twelve 15-second cycles = the same three minutes the CRM timer gives the round, so the
	# beacon and the eject agree however long /dev/mem took.
	if [ $n -ge 12 ]; then
	# Ways out that do not need a hand on the power switch. `reboot -f` on this vendor kernel
	# may route through the zx_wdt restart handler, which this round masked, so the CRM bits go
	# back first and SysRq-b is the second, independent attempt.
	crm /tmp/f14
	devmem2 0x10e10208 w 0x1F1 >/dev/null 2>&1
	reboot -f 2>/dev/null
	echo 1 >/proc/sysrq-trigger 2>/dev/null
	echo b >/proc/sysrq-trigger 2>/dev/null
	sleep 600
		fi
done
"""

FLASH_INIT = r"""#!/bin/sh
# Flasher /init: program the mainline triple from a RAM root, on the vendor 4.19 kernel
# that this round has already proven reaches userspace.
#
# Two reasons this cannot run from the mounted system:
#   - `root` is the jffs2 your own / is mounted from; erasing it under a mounted jffs2
#     oopses the kernel before the writes finish.
#   - in a RAM root nothing is mounted from flash, so all three partitions are free.
#
# The watchdog is masked BEFORE anything is written: the zx_wdt armed by the kernel we
# kexec'd away from is still counting at 8 s, and erase+write of 13 MiB takes longer than
# that. Masking costs the self-eject, which is fine here because a board that dies mid
# -write has to be dealt with by hand either way, and nothing here can reboot it into a
# half-written state on purpose.
PATH=/bin:/sbin
TAG={tag}
DO_WRITE={do_write}
DO_ROOT={do_root}
# Defined even when the images are embedded, so the gate below has one rule. Note that this
# script is produced by str.format(), so a brace pair anywhere in it - even in a comment - is
# a placeholder: keep shell parameter defaults out of the text and initialise here instead.
stagefail=0

mount -t proc proc /proc 2>/dev/null
mount -t sysfs sysfs /sys 2>/dev/null
mount -t devtmpfs devtmpfs /dev 2>/dev/null
echo "ctl-flash: /init tag=$TAG write=$DO_WRITE root=$DO_ROOT uptime=$(cut -d. -f1 /proc/uptime)" >/dev/kmsg 2>/dev/null

# 1. take the reset path away, then prove it
devmem2 0x10e10208 w 0x0 >/dev/null 2>&1
echo "ctl-flash: crm_10e10208 = $(devmem2 0x10e10208 w 2>&1 | tail -1)" >/dev/kmsg 2>/dev/null

# 2. announce on the panel so a human can see the round is alive at all
for L in /sys/class/leds/*/brightness; do [ -w "$L" ] && echo 255 >$L 2>/dev/null; done

{stage}
# 3. geometry: what the kernel says the partitions are, versus what we are about to write
cat /proc/mtd >/ctl-flash.log 2>&1
# /proc/mtd gives sizes but no offsets, and the whole safety argument for writing by label
# rests on *where* the label sits. /sys/class/mtd/mtdN/offset is decimal, so 0xf00000 must
# read as 15728640 before any erase is issued.
for M in /sys/class/mtd/mtd*; do
	echo "$(basename $M) name=$(cat $M/name 2>/dev/null) offset=$(cat $M/offset 2>/dev/null) size=$(cat $M/size 2>/dev/null) erasesize=$(cat $M/erasesize 2>/dev/null)" >>/ctl-flash.log 2>&1
done
for f in p/flash-uImage p/flash-dtb.bin p/flash-rootfs.jffs2; do
	[ -s /$f ] || echo "ctl-flash: MISSING /$f" >>/ctl-flash.log
done
wc -c <p/flash-uImage >>/ctl-flash.log 2>&1
ls -l /dev/mtd* >>/ctl-flash.log 2>&1
sleep 3

# Hand the survey back to the PC: it needs no host key, no observer and no ssh at all.
report() {{
	mkdir /www 2>/dev/null
	{{ echo "CTLFLASH tag=$TAG write=$DO_WRITE kver=$(uname -r) uptime=$(cut -d. -f1 /proc/uptime)"
	   echo; cat /ctl-flash.log 2>/dev/null; echo; ls -l /dev/mtd* 2>&1; echo; cat /proc/mtd; }} >/ctl-report.txt 2>/dev/null
	# Pushed, not served: the busybox in this initrd has no httpd and no telnetd (its usable
	# applets are tftp/nc/ping/logger), so an outbound connection is the only channel out of
	# a RAM root. Repeated because the PC may not be listening yet when the first one opens.
	PC=$(sed -n 's/.*ctlpc=\([^ ]*\).*/\1/p' /proc/cmdline 2>/dev/null)
	[ -n "$PC" ] || PC=192.168.9.190
	i=0
	while [ $i -lt ${{1:-8}} ]; do
		i=$(( i + 1 ))
		nc $PC 5555 </ctl-report.txt 2>/dev/null
		sleep 3
	done
}}
for N in /sys/class/net/*; do
	D=$(basename $N)
	[ "$D" = lo ] && continue
	ifconfig $D 192.168.9.1 netmask 255.255.255.0 up 2>/dev/null
done
report

# GO/NO-GO, computed from what the running kernel actually registers rather than from a
# comment in a dts. Two things have to be true before an erase is issued: the three labels
# exist, and `root` sits at 0xf00000 = 15728640 - the offset the new kernel's own table
# gives root, so that a write-by-label lands where the new system will mount from.
ok=1
[ "$stagefail" = 0 ] || {{ echo "ctl-flash: staging failed, refusing to write" >>/ctl-flash.log; ok=0; }}
for L in kernel dtb root; do
	grep -q "\"$L\"" /proc/mtd 2>/dev/null || {{ echo "ctl-flash: no \"$L\" in /proc/mtd" >>/ctl-flash.log; ok=0; }}
done
ROFF=$(for M in /sys/class/mtd/mtd*; do
	[ "$(cat $M/name 2>/dev/null)" = root ] && cat $M/offset 2>/dev/null
done)
echo "ctl-flash: root offset=$ROFF (must be 15728640) kernel/dtb/root present=$ok" >>/ctl-flash.log
[ "$ROFF" = 15728640 ] || ok=0
report

if [ "$DO_WRITE" != 1 ] || [ "$ok" != 1 ]; then
	echo "ctl-flash: $([ "$DO_WRITE" = 1 ] && echo 'GATE FAILED, refusing to write' || echo 'dry run'), nothing written" >/dev/kmsg 2>/dev/null
	sleep 150
	# Ways out that do not need a hand on the power switch. `reboot -f` on this vendor kernel
	# may route through the zx_wdt restart handler, which this round masked, so the CRM bits go
	# back first and SysRq-b is the second, independent attempt.
	devmem2 0x10e10208 w 0x1F1 >/dev/null 2>&1
	reboot -f 2>/dev/null
	echo 1 >/proc/sysrq-trigger 2>/dev/null
	echo b >/proc/sysrq-trigger 2>/dev/null
	sleep 600
	# Structurally terminal: a dry run that fails to reboot must still never reach the
	# erase/write block below. Without this exit the writes were only *usually* skipped.
	echo "ctl-flash: reboot attempts failed, exiting rather than writing" >/dev/kmsg 2>/dev/null
	exit 0
	fi

# 4. program. Order: root (optional), dtb, kernel LAST, so the system that is running now
#    stays bootable until the final write. /sbin/mtd works on partition labels from the
#    board's own mtd table, so an offset typo cannot land a write in the bootloader.
#
# DO_ROOT=0 is the first flash to try, and it is not only less destructive - it is a
# different image: mainline's own table now puts `root` at 0xf00000, which is exactly where
# the installed OpenWrt 24.10.8 jffs2 already is. That rootfs is userspace, kernel-neutral
# (procd/dropbear/LuCI run fine on 6.18; only the 4.19 kmods stop loading), so kernel+dtb
# alone yields a bootable mainline with a working shell - and the 239 MiB of jffs2 that is
# also our rollback material stays byte-identical.
if [ "$DO_ROOT" = 1 ]; then
	mtd erase root          >>/ctl-flash.log 2>&1 && mtd write p/flash-rootfs.jffs2 root >>/ctl-flash.log 2>&1
	echo "root rc=$?" >>/ctl-flash.log 2>&1
else
	echo "root: skipped (DO_ROOT=0), installed jffs2 left intact" >>/ctl-flash.log 2>&1
fi
mtd erase dtb           >>/ctl-flash.log 2>&1 && mtd write p/flash-dtb.bin dtb       >>/ctl-flash.log 2>&1
echo "dtb rc=$?" >>/ctl-flash.log 2>&1
mtd erase kernel        >>/ctl-flash.log 2>&1 && mtd write p/flash-uImage kernel     >>/ctl-flash.log 2>&1
echo "kernel rc=$?" >>/ctl-flash.log 2>&1
cp /ctl-flash.log /dev/kmsg 2>/dev/null
report 14
# the pushes above run ~42 s: long enough for the PC to have caught the write log before the
# board changes identity

# 5. let the panel say it is done, then hand the board to U-Boot
for i in 1 2 3 4 5 6; do
	for L in /sys/class/leds/*/brightness; do [ -w "$L" ] && echo 0 >$L 2>/dev/null; done
	sleep 1
	for L in /sys/class/leds/*/brightness; do [ -w "$L" ] && echo 255 >$L 2>/dev/null; done
	sleep 1
done
sync
reboot -f
sleep 60
"""


APPLET_LINKS = ("sh", "ls", "cat", "dd", "sleep", "printf", "mount", "umount", "cut",
                "uname", "echo", "true", "insmod", "lsmod", "ifconfig", "ip", "nc",
                "hostname", "route", "reboot", "poweroff", "dmesg", "cp", "mkdir", "basename",
                "pwd", "grep", "head", "tr", "expr", "wc", "df", "date", "rm", "sed",
                "md5sum", "hexdump", "ping", "tftp", "logger")


def makedev(major, minor):
    """newc stores rdev as (major << 8) | minor. os.makedev does not exist on Windows."""
    return ((major << 8) | minor) & 0xFFFFFFFF


def newc(name, data, mode, rdev=0):
    """One newc record. `data` is the file body, the symlink target, or None.

    The header is 6 magic bytes + 13 eight-hex fields = 110 bytes:
    ino mode uid gid nlink mtime filesize devmajor devminor rmajor rminor namesize chksum.
    Getting the field count wrong is not a warning: every record after the first lands at
    the wrong offset, the kernel unpacks nothing, and a round is read as a failed handoff.
    """
    filesize = len(data) if data is not None else 0
    fields = [1, mode, 0, 0, 1, 0, filesize, 0, 0, rdev >> 8, rdev & 0xFF,
              len(name) + 1, 0]
    hdr = "070701" + "".join("%08X" % v for v in fields)
    assert len(hdr) == 110, len(hdr)
    rec = hdr.encode() + name.encode() + b"\x00"
    rec += b"\x00" * ((-len(rec)) % 4)
    if data is not None:
        rec += data
        rec += b"\x00" * ((-len(data)) % 4)
    return rec


def verify(blob):
    """Re-parse the archive with an independent loop and print the table, so a padding or
    field mistake cannot reach the board and be read as a failed handoff. The kernel's own
    reader walks the same way: 110 ASCII header bytes, name padded to 4, data padded to 4.
    """
    pos, n = 0, 0
    while pos < len(blob):
        if blob[pos:pos + 6] != b"070701":
            print("  !! record at %d does not start with 070701" % pos)
            return False
        f = [int(blob[pos + 6 + i * 8:pos + 14 + i * 8], 16) for i in range(13)]
        mode, filesize, namesize = f[1], f[6], f[11]
        rdev = (f[9] << 8) | f[10]
        name = blob[pos + 110:pos + 110 + namesize - 1].decode()
        pos += 110 + namesize
        pos += (-pos) % 4
        pos += filesize
        pos += (-filesize) % 4
        kind = {0o040000: "d", 0o120000: "l", 0o020000: "c", 0o100000: "-"}.get(mode & 0o170000, "?")
        print("  %s %-24s size=%-9d mode=%07o rdev=0x%04x" % (kind, name, filesize, mode, rdev))
        n += 1
        if name == "TRAILER!!!":
            break
    print("  parsed %d records, %d B of %d B consumed, tail %r" % (n, pos, len(blob), blob[pos:pos + 8]))
    return pos == len(blob)


def check_dirs(blob, init):
    """Every directory /init writes into must exist in the archive or be created by the script.

    `echo ... >/tmp/mb1 2>/dev/null` inside an image with no /tmp fails quietly, and the stage of
    the report it belongs to then reads on the PC as a payload that never started - the one
    failure mode this project has spent rounds trying to tell apart from success. Checked at
    build time because the PC cannot: this OS has a /tmp, the initrd did not.
    """
    names, pos = set(), 0
    while blob[pos:pos + 6] == b"070701":
        f = [int(blob[pos + 6 + i * 8:pos + 14 + i * 8], 16) for i in range(13)]
        namesize, filesize = f[11], f[6]
        name = blob[pos + 110:pos + 110 + namesize - 1].decode()
        pos += 110 + namesize; pos += (-pos) % 4
        pos += filesize; pos += (-filesize) % 4
        names.add(name.split("/")[0])
        if name == "TRAILER!!!":
            break
    made = set()
    for line in re.findall(r"mkdir (?:-p )?([^&|;]+)", init):
        made.update(x for x in line.split() if x.startswith("/"))
    need = set()
    for path in re.findall(r">+\s*/([A-Za-z0-9_./-]+)", init):
        parts = path.split("/")
        if len(parts) >= 2:
            need.add("/" + parts[0])
    have = {"/" + n for n in names} | made
    bad = sorted(need - have)
    print("write paths: in image %s, mkdir-ed %s, needed %s -> %s" %
          (sorted("/" + n for n in names if "/" not in n and n != "TRAILER!!!"),
           sorted(made), sorted(need), "all covered" if not bad else "MISSING " + ", ".join(bad)))
    return not bad


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--busybox", default="staging/ctl/rootfs/busybox")
    ap.add_argument("--lib", action="append", default=[
        "staging/ctl/rootfs/ld-musl-aarch64.so.1", "staging/ctl/rootfs/libgcc_s.so.1"])
    ap.add_argument("--tag", default="K4")
    ap.add_argument("--out", default=None,
                    help="default staging/ctl/ctl-initramfs.cpio.gz (announce) or ctl-flash.cpio.gz (flash)")
    ap.add_argument("--mode", choices=["announce", "flash"], default="announce")
    ap.add_argument("--write", action="store_true",
                    help="flash mode: actually erase and program; without it, dry run")
    ap.add_argument("--root", action="store_true",
                    help="also erase+program the root partition (default: leave it alone)")
    ap.add_argument("--extra", action="append", default=[],
                    help="dest=local-file, e.g. sbin/mtd=staging/ctl/rootfs/mtd")
    ap.add_argument("--payload", action="append", default=[],
                    help="dest=local-file under /p for flash mode")
    ap.add_argument("--stage", choices=["embed", "net"], default="embed",
                    help="flash mode: carry the images in the initrd, or fetch them from the PC")
    ap.add_argument("--netbase", type=int, default=5561,
                    help="first port fileserve.py must serve, in --payload order")
    a = ap.parse_args()
    if a.out is None:
        a.out = "staging/ctl/ctl-flash.cpio.gz" if a.mode == "flash" else "staging/ctl/ctl-initramfs.cpio.gz"

    pspecs = []
    for spec in a.payload:
        if "=" not in spec:
            sys.exit("--payload wants dest=local-file, got %r" % spec)
        pspecs.append(tuple(spec.split("=", 1)))

    stage_txt = ""
    if a.mode == "flash" and a.stage == "net":
        # Fetched from the PC instead of carried: the largest initrd this vendor kernel has
        # been *proven* to load is 577 kB, and kernel+dtb+rootfs together are 12 MB. Every
        # file is hashed on arrival, because a truncated transfer would otherwise be written
        # into flash and read back as a boot failure of the new kernel.
        lines = [
            "# --- net staging, generated by tools/mkctlram.py --stage net ---",
            'PC=$(sed -n \'s/.*ctlpc=\\([^ ]*\\).*/\\1/p\' /proc/cmdline 2>/dev/null)',
            '[ -n "$PC" ] || PC=192.168.9.190',
            "mkdir -p p",
            "stagefail=0",
        ]
        for i, (dest, local) in enumerate(pspecs):
            d = dest.lstrip("/")
            want = hashlib.md5(open(local, "rb").read()).hexdigest()
            port = a.netbase + i
            lines += [
                'echo "ctl-flash: fetching %s from $PC:%d" >>/ctl-flash.log' % (d, port),
                "nc $PC %d >%s 2>/dev/null" % (port, d),
                'got=$(md5sum <%s | cut -d" " -f1)' % d,
                'if [ "$got" = "%s" ]; then echo "ctl-flash: STAGED %s $got $(wc -c <%s) B" '
                '>>/ctl-flash.log; else echo "ctl-flash: STAGE_FAIL %s got=$got want=%s" '
                ">>/ctl-flash.log; stagefail=1; fi" % (want, d, d, d, want),
            ]
            print("  serve %-22s on port %d  md5 %s" % (d, port, want[:16]))
        stage_txt = "\n".join(lines) + "\n"

    if a.mode == "flash":
        init = FLASH_INIT.format(tag=a.tag, do_write=1 if a.write else 0,
                                 do_root=1 if a.root else 0, stage=stage_txt)
    else:
        init = INIT_TMPL.format(tag=a.tag)
    recs = [
        newc("dev", None, stat.S_IFDIR | 0o755),
        newc("dev/console", None, stat.S_IFCHR | 0o600, makedev(5, 1)),
        newc("dev/null", None, stat.S_IFCHR | 0o666, makedev(1, 3)),
        newc("proc", None, stat.S_IFDIR | 0o755),
        newc("sys", None, stat.S_IFDIR | 0o755),
        newc("bin", None, stat.S_IFDIR | 0o755),
        newc("lib", None, stat.S_IFDIR | 0o755),
        newc("bin/busybox", open(a.busybox, "rb").read(), stat.S_IFREG | 0o755),
    ]
    for l in a.lib:
        recs.append(newc("lib/" + os.path.basename(l), open(l, "rb").read(),
                         stat.S_IFREG | 0o755))
    for n in APPLET_LINKS:
        recs.append(newc("bin/" + n, b"busybox\x00", stat.S_IFLNK | 0o777))
    # --extra/--payload carry the flasher's tools and the three images. The kernel's
    # initramfs unpacker creates missing parent directories, so no dir records are needed.
    # In net staging the payloads are deliberately absent - that is the whole point.
    embed = list(a.extra) + (
        [] if (a.mode == "flash" and a.stage == "net") else list(a.payload))
    for spec in embed:
        if "=" not in spec:
            sys.exit("--extra/--payload want dest=local-file, got %r" % spec)
        dest, local = spec.split("=", 1)
        blob_in = open(local, "rb").read()
        recs.append(newc(dest.lstrip("/"), blob_in, stat.S_IFREG | 0o755))
        print("  + %-22s <- %-40s %d B" % (dest, local, len(blob_in)))
    recs.append(newc("init", init.encode(), stat.S_IFREG | 0o755))
    recs.append(newc("TRAILER!!!", b"", 0))
    blob = b"".join(recs)

    os.makedirs(os.path.dirname(a.out) or ".", exist_ok=True)
    with gzip.GzipFile(a.out, "wb", mtime=0) as f:
        f.write(blob)
    back = len(gzip.decompress(open(a.out, "rb").read()))
    print("%s: cpio %d B -> file %d B, md5 %s" % (
        a.out, len(blob), os.path.getsize(a.out),
        hashlib.md5(open(a.out, "rb").read()).hexdigest()))
    print("self-check: gunzip -> %d B (%s), %d entries" % (
        back, "OK" if back == len(blob) else "MISMATCH", len(recs)))
    print("tag=%s  /init is the %d-byte script inside it" % (a.tag, len(init)))
    verify(blob)
    if not check_dirs(blob, init):
        sys.exit("refusing to ship an initrd whose /init writes into directories it does not have")


if __name__ == "__main__":
    main()
