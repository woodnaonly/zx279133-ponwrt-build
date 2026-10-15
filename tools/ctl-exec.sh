#!/bin/sh
# Fire the control jump after a delay, from a detached shell so the ssh session that
# armed it can close first.
#
# Two ways this round can end, and the CRM reset-routing register chooses which one we
# get to see:
#
#   MASK=0 - leave 0x10e10208 at the vendor value (0x1F1). The already-armed zx_wdt
#     (4.19 arms four at 8 s and feeds them in-kernel, the first at uptime 7.2 s) bites
#     ~15-20 s after the jump if the payload has not reached its own watchdog driver
#     yet, and resets warm back into U-Boot. Self-recovering - and also the reason
#     control round 1 was inconclusive: a payload that needs 7.2 s to get to the feed
#     cannot be told apart from a payload that never started.
#   MASK=1 - write 0x0, the same bit the vendor driver itself calls
#     top_crm wdt_rst_mask. Nothing resets, so the payload gets unlimited time, and if
#     it really does not boot the board stays hung until power is cycled. Nothing here
#     touches flash, so a cycle always brings the installed system back.
#
# Read back: ctltag in /proc/cmdline -> the handoff works and the 8 s heartbeat was the
# whole story. Silence with MASK=1 -> the handoff itself is broken, and every kexec
# round before it proved nothing about mainline.
D=${D:-/tmp/ctl}
DELAY=${DELAY:-150}
CTLTAG=${CTLTAG:-K1}
MASK=${MASK:-0}

echo "control armed at uptime=$(cut -d. -f1 /proc/uptime) s, firing in $DELAY s (mask=$MASK ctltag=$CTLTAG)" >$D/exec.log
sleep $DELAY
if [ "$MASK" = 1 ]; then
	/sbin/devmem2 0x10e10208 w 0x0 >/dev/null 2>&1
	echo "crm_10e10208 = $(/sbin/devmem2 0x10e10208 w 2>&1 | tail -1) at uptime=$(cut -d. -f1 /proc/uptime) s: no reset will eject us" >>$D/exec.log
else
	echo "crm_10e10208 = $(/sbin/devmem2 0x10e10208 w 2>&1 | tail -1), left as the vendor set it" >>$D/exec.log
fi
echo "firing at uptime=$(cut -d. -f1 /proc/uptime) s, kexec_loaded=$(cat /sys/kernel/kexec_loaded)" >>$D/exec.log
cd $D || { echo "cd $D failed" >>$D/exec.log 2>/dev/null; exit 1; }
# -f is not a nicety: `strings` on this kexec shows /sbin/shutdown, /etc/shutdown,
# /bin/shutdown, and --help says "-f, --force  Force an immediate kexec, don't call
# shutdown". Without it, `kexec --exec` on an OpenWrt root that HAS /sbin/shutdown runs a
# normal reboot instead of jumping - which is what control round 1 did, and what every
# mainline round before it did too. Their "board went silent, came back with no trace" is
# exactly a plain U-Boot reboot, so none of them ever tested the payload.
LD_LIBRARY_PATH=$D/lib ./kexec -f --exec
rc=$?
echo "kexec -f --exec returned $rc at uptime=$(cut -d. -f1 /proc/uptime) s - readable only if the jump failed" >>$D/exec.log
cat /sys/kernel/kexec_loaded >>$D/exec.log 2>/dev/null
