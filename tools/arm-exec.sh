#!/bin/sh
# Delayed kexec --exec for the SK-D840N, with the watchdog's reset path masked.
#
# Run it from the PC side (tools/pushtrial.sh copies this file to the box), never by
# hand over ssh, so the whole body is not one nested quoting problem.
#
# The delay exists because the switch cannot be triggered over the network it is
# about to move onto: the trial is observed from the eth0 jack, and until mainline
# takes over that jack carries nothing (the flashed image leaves eth0 as a "wan"
# with no address). So arm here, move the cable, and let it fire.
#
# The mask exists because the running 4.19 arms three zx_wdt instances at boot
# (heartbeat 8 s) and feeds them in-kernel; kexec removes the feeder, and CRM
# register 0x10e10208 routes the expiry to the chip reset - bits 0 and 8 are the
# chip-reset path, so 0x101 only delays the reboot (~44 s) while 0x0 prevents it.
# Written as late as possible: the register is volatile and belongs to the *old*
# kernel, so there is nothing to hand over.
#
# Once masked the board no longer recovers by itself - a hang then means power
# cycling. Nothing here touches flash.
#
# MASK=0 inverts that on purpose, for the rounds whose evidence is a pstore/ramoops
# console dump: leaving CRM alone means the vendor's watchdog bites some tens of seconds
# after the jump and the board comes back to the flashed system by itself, warm, with
# mainline's log still in DRAM - and tools/getramoops.py reads it out of /dev/mem.
#
# There is deliberately no "blank the ramoops region before the jump" step. The first round
# had one and it destroyed that round: the dd ran *after* kexec --load, and on this board
# the arm64 loader had put the new kernel's own text at exactly the address the DTS
# reserved for pstore (no-map binds the new kernel, not the loader), so blanking it blanked
# part of the image about to boot. Freshness is proved instead by the trialtag=... that
# trial-kexec.sh puts on the command line: the kernel echoes its own cmdline into the log,
# so the dump either carries this round's tag or it is not from this boot.
D=${D:-/tmp/trial}
DELAY=${DELAY:-240}
MASK=${MASK:-1}

echo "armed at uptime=$(cut -d. -f1 /proc/uptime) s, firing in $DELAY s (mask=$MASK)" >$D/arm.log
sleep $DELAY
if [ "$MASK" = 1 ]; then
	/sbin/devmem2 0x10e10208 w 0x0 >/dev/null 2>&1
	echo "crm_10e10208=$(/sbin/devmem2 0x10e10208 w 2>&1 | tail -1) kexec_loaded=$(cat /sys/kernel/kexec_loaded) at uptime=$(cut -d. -f1 /proc/uptime) s" >>$D/arm.log
else
	echo "crm_10e10208 left as the vendor set it, so its watchdog ejects us back at $(cut -d. -f1 /proc/uptime)" >>$D/arm.log
fi
cd $D || exit 1
# -f: without it this kexec calls /sbin/shutdown (see the strings in the binary and
# "--help: -f Force an immediate kexec, don't call shutdown"), so `--exec` becomes an
# ordinary reboot through U-Boot and the loaded image is never run. Rounds fired before
# this flag was added tested nothing - the board went silent and came back exactly as a
# plain reboot does.
LD_LIBRARY_PATH=$D/lib ./kexec -f --exec
echo "kexec -f --exec returned $? at uptime=$(cut -d. -f1 /proc/uptime) s - readable only if the jump failed" >>$D/arm.log
