#!/bin/sh
# Copy the trial kit onto the running system and kexec --load it - load only, so
# nothing executes and the current system stays in charge.
#
#	sh tools/pushtrial.sh          # copy + --load, print what the kernel says
#	sh tools/pushtrial.sh unload   # drop the loaded image again
#
# Loading is the part that can fail for reasons no log will show (no console), so
# it is done while ssh still works and /sys/kernel/kexec_loaded is the evidence.
set -eu
cd "$(dirname "$0")/.."

DEV=${DEV:-root@192.168.9.1}
KEY=${KEY:-$HOME/.ssh/id_onu}
D=/tmp/trial
SSH="ssh -q -o BatchMode=yes -o IdentitiesOnly=yes -i $KEY -o UserKnownHostsFile=/dev/null -o StrictHostKeyChecking=no $DEV"

if [ "${1:-load}" = unload ]; then
	$SSH "export LD_LIBRARY_PATH=$D/lib; [ -x $D/kexec ] && $D/kexec --unload; echo kexec_loaded=\$(cat /sys/kernel/kexec_loaded)"
	exit 0
fi

# The script that runs on the box is the one in this repo, not necessarily the one
# inside the released tarball - the release we are testing against was built before
# the copy path and the fetch order were fixed, and pushing the older one would
# test code we already know is wrong.
cp ci-build-repo/templates/trial-kexec.sh staging/trial/trial-kexec.sh
cp tools/arm-exec.sh staging/trial/arm-exec.sh
need="trial-Image.gz trial-dtb.bin trial-kexec.sh trial-kx.tgz arm-exec.sh"
# Not in the release yet: a RAM rootfs with the trial-only files applied
# (python tools/mktrialram.py). With it the round can tell "mainline hung" from
# "mainline booted but this port is not its eth0"; without it the image is silent
# for both. CI's initramfs job stages the same file as trial-initramfs.cpio.gz.
[ -s staging/trial/trial-initramfs.cpio.gz ] && need="$need trial-initramfs.cpio.gz"
for f in $need; do
	[ -s "staging/trial/$f" ] || { echo "staging/trial/$f is missing - run sh tools/gettrial.sh first" >&2; exit 1; }
done

$SSH "mkdir -p $D"
for f in $need; do
	printf 'copying %s ... ' "$f"
	# ssh pipe rather than scp: this image has no /usr/libexec/sftp-server, so the
	# default scp protocol fails, and 30 MB takes about 1.5 s this way.
	cat "staging/trial/$f" | $SSH "cat > $D/$f"
	echo done
done
$SSH "cd $D && sha256sum -c sha256sums-trial 2>/dev/null || true; df -h /tmp | tail -1"

echo "=== kexec --load ==="
$SSH "cd $D && KEXEC_ARGS=--load sh trial-kexec.sh; echo kexec_loaded=\$(cat /sys/kernel/kexec_loaded)"

cat <<EOF

Loaded, not running. Two things before --exec, in this order:

1. Mask the watchdog reset path, or the board reboots ~15-20 s into mainline: the
   4.19 arms three zx_wdt at 8 s and feeds them in-kernel, and the reset routing is
   the volatile CRM register 0x10e10208 (a normal vendor system reads 0x1F1). Write
   it with /sbin/devmem2 as 0x0 - 0x101 (per-WDT bits only) merely delays the reset
   to ~44 s here, so bits 0 and 8 are the chip-reset path. Once masked nothing
   self-recovers: power-cycle brings the flashed system back, and flash stays
   untouched either way.

2. The cable has to be in the jack whose netdev is eth0 - the NPPT + RTL8226 port.
   There is no WAN jack on this board (4x LAN + PON); the flashed image only *calls*
   eth0 "wan" because its config came from a router default, so eth0 is one of the
   LAN jacks while eth1..eth3 are 9132 ports with no mainline driver. Identify it
   with sh tools/jackprobe.sh: the jack that shows link but answers no ARP/ping/ssh
   is eth0 (the bridge learns no MACs here, so brctl cannot tell them apart).

3. That also removes the last cable move: bridge eth0 into the lan bridge first
   (`ip link set eth0 master br-lan`, runtime only, gone on reboot) and arm and
   observe through that one jack, connected before and after the switch-over.

Arm over ssh (arm-exec.sh writes the mask itself, as late as it can), then move the
cable - or stay put if you bridged eth0 in step 3:

	$SSH "DELAY=240 setsid sh $D/arm-exec.sh & echo armed"

Watch with: sh tools/wait4net.sh   then   python tools/ramcheck.py

If trial-initramfs.cpio.gz was copied, the RAM image also carries etc/rc.d/S15trial:
it puts 192.168.9.1 on every netdev the kernel creates, starts ssh without waiting
for the lan, and reboots itself 300 s after userspace starts (PSCI, so not the
watchdog). That reboot is the point: seeing the link flap and the flashed system
come back proves mainline booted even if no port ever answered, which is the
difference between a board DTS to fix and a kernel to debug. Touch
/tmp/trial-noreboot from the ssh shell to cancel it.
EOF
