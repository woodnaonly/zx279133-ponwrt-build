#!/bin/sh
# Control round loader: kexec the *vendor* 4.19 kernel that is running right now, with
# the DTB it was booted with, and one unmistakable extra command-line token.
#
# Why this exists: every kexec round so far ends the same way - the board goes silent,
# then comes back - and that blackout is predicted just as well by "mainline owns the
# board" as by "kexec --exec never transferred control". Nothing observed so far can
# separate the two, so the question has to be answered with a payload we already know
# boots on this hardware. If ctltag shows up in the returned system's /proc/cmdline, the
# handoff works and mainline is the thing that fails; if it never shows up while the board
# does come back, the handoff itself is broken and every earlier mainline round was
# measuring nothing.
#
# The payload is not a rebuild: staging/ctl/Image was verified byte-identical to
# gunzip(uImage body of /dev/mtd2), and its banner matches `uname -v` of the running
# kernel (4.19.136+ #4 SMP Mon Jun 3 23:31:29 CST 2024). The DTB is /sys/firmware/fdt
# read off the live kernel, so it carries U-Boot's own fixups rather than the flash copy
# (the mtd3 dump contains no d00dfeed magic at all).
#
# Round 1 (no initrd) could not answer the question: that payload mounted the flashed jffs2
# root, which needs 7.18-7.24 s to reach its own zx_wdt feed while the armed heartbeat is
# 8 s, so the round was decided by milliseconds rather than by the handoff. INITRD=<name>
# switches to a RAM root whose /init pulses the panel 1-2 s after the jump - inside the
# window, and with MASK=1 it keeps pulsing until power is cycled, so nobody has to be
# watching at a specific second.
set -eu
D=${D:-/tmp/ctl}
CTLTAG=${CTLTAG:-K1}
# Where the payload pushes its report. It cannot be queried - no httpd/telnetd in that
# busybox - so the address has to travel with the kernel command line, and /init reads it
# back out of /proc/cmdline. Override with CTLPC=<ip> if the wired adapter moved.
CTLPC=${CTLPC:-192.168.9.190}
INITRD=${INITRD:-}
# IMAGE/DTB name the payload inside $D. The gate round points them at the mainline 6.18.38
# triple that is about to be flashed (gate-Image, gate-dtb), because "kexec works" was proven
# with the vendor kernel and that does not transfer to the payload we actually care about.
IMAGE=${IMAGE:-Image}
DTB=${DTB:-fdt}

for f in kexec $IMAGE $DTB; do
	[ -s "$D/$f" ] || { echo "$D/$f missing - push it first (sh tools/pushctl.sh)" >&2; exit 1; }
done

if [ -n "$INITRD" ]; then
	[ -s "$D/$INITRD" ] || { echo "$D/$INITRD missing - built by tools/mkctlram.py" >&2; exit 1; }
	# root=/dev/ram0 and nothing jffs2-ish: leaving root=/dev/mtdblock5 in the line would
	# only matter if the kernel ignored the initrd, and the flashed procd on a ram overlay
	# is what issues `reboot -f` - which reads exactly like a failed handoff.
	# ctltag/ctlpc are stripped before being re-appended: the base line is read from the
	# *running* kernel, so a second round in the same boot would otherwise inherit the first
	# round's tag and the readback would score the wrong payload while looking valid.
	BASE=$(sed -e 's/ ctltag=[^ ]*//g' -e 's/ ctlpc=[^ ]*//g' /proc/cmdline)
	CL="$(echo "$BASE" | sed -e 's|root=/dev/mtdblock5 rootfstype=jffs2|root=/dev/ram0 rw|') ctltag=$CTLTAG ctlpc=$CTLPC init=/init"
	IARG="--initrd=$INITRD"
else
	CL="$(sed -e 's/ ctltag=[^ ]*//g' -e 's/ ctlpc=[^ ]*//g' /proc/cmdline) ctltag=$CTLTAG ctlpc=$CTLPC"
	IARG=""
fi
echo "payload: image=$IMAGE($(wc -c <$D/$IMAGE) B) dtb=$DTB($(wc -c <$D/$DTB) B) initrd=${INITRD:-none}"
echo "cmdline: $CL"
echo "crm_10e10208 (reset routing, must stay at the vendor value): $(/sbin/devmem2 0x10e10208 w 2>&1 | tail -1)"

cd "$D"
# A previous round's image may still be loaded (--load twice in one boot is an EEXIST we
# would not see without a console, and the round would silently test the old tag).
LD_LIBRARY_PATH=$D/lib ./kexec --unload 2>/dev/null || true
LD_LIBRARY_PATH=$D/lib ./kexec --load $IMAGE --dtb=$DTB $IARG --command-line="$CL"
echo "kexec_loaded=$(cat /sys/kernel/kexec_loaded)"
echo
echo "Loaded only - nothing has run yet. To jump:"
echo "  CTLTAG=$CTLTAG setsid sh $D/ctl-exec.sh &   (see ctl-exec.sh; DELAY= in seconds)"
