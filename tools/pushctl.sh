#!/bin/sh
# Stage the control round on the running system and load it. Nothing executes here -
# the current kernel stays in charge until ctl-exec.sh fires.
#
#	sh tools/pushctl.sh              # copy everything, kexec --load, print what the kernel says
#	sh tools/pushctl.sh unload      # drop the loaded image
#
# Inputs (all verified, not rebuilt):
#   staging/probe-Image        == gunzip(the uImage body of /dev/mtd2), banner matches
#                                the running kernel's `uname -v`
#   staging/ctl/fdt-live.bin   == /sys/firmware/fdt of the running kernel, i.e. the DTB
#                                with U-Boot's fixups rather than the flash copy
#   staging/trial/trial-kx.tgz == kexec-tools 2.0.31 + its libz/liblzma, from the CI release
set -eu
cd "$(dirname "$0")/.."
DEV=${DEV:-root@192.168.9.1}
KEY=${KEY:-$HOME/.ssh/id_onu}
D=${D:-/tmp/ctl}
CTLTAG=${CTLTAG:-K1}
# INITRD=ctl-initramfs.cpio.gz switches the payload to the RAM root that pulses the panel.
INITRD=${INITRD:-}
SSH="ssh -q -o BatchMode=yes -o IdentitiesOnly=yes -i $KEY -o UserKnownHostsFile=/dev/null -o StrictHostKeyChecking=no $DEV"

if [ "${1:-load}" = unload ]; then
	$SSH "export LD_LIBRARY_PATH=$D/lib; [ -x $D/kexec ] && $D/kexec --unload; echo kexec_loaded=\$(cat /sys/kernel/kexec_loaded)"
	exit 0
fi

[ -s staging/probe-Image ] || { echo "staging/probe-Image missing" >&2; exit 1; }
[ -s staging/ctl/fdt-live.bin ] || { echo "staging/ctl/fdt-live.bin missing - pull it: ssh root@... 'cat /sys/firmware/fdt | gzip -9'" >&2; exit 1; }
cp tools/ctl-kexec.sh staging/ctl/ctl-kexec.sh
cp tools/ctl-exec.sh staging/ctl/ctl-exec.sh
# IMAGE_LOCAL/DTB_LOCAL choose which triple gets staged: the vendor kernel for the control
# round, or the mainline flash payload for the gate round.
IMAGE_LOCAL=${IMAGE_LOCAL:-staging/probe-Image}
DTB_LOCAL=${DTB_LOCAL:-staging/ctl/fdt-live.bin}
REMOTE_IMAGE=${REMOTE_IMAGE:-Image}
REMOTE_DTB=${REMOTE_DTB:-fdt}
# cp refuses to copy a file onto itself, and the gate triple is already staged under the very
# name the board loads it by (tools/mkgate.sh writes staging/ctl/gate-Image), so staging is
# skipped when source and destination are the same path rather than failing the round.
[ "$IMAGE_LOCAL" = "staging/ctl/$REMOTE_IMAGE" ] || cp "$IMAGE_LOCAL" staging/ctl/$REMOTE_IMAGE
[ "$DTB_LOCAL" = "staging/ctl/$REMOTE_DTB" ] || cp "$DTB_LOCAL" staging/ctl/$REMOTE_DTB

# name=local-path pairs copy a file under a different remote name; a bare name is read
# from staging/ctl/
need="$REMOTE_IMAGE $REMOTE_DTB kx.tgz=staging/trial/trial-kx.tgz ctl-kexec.sh ctl-exec.sh"
if [ -s staging/ctl/ctl-initramfs.cpio.gz ]; then need="$need ctl-initramfs.cpio.gz"; fi
if [ -s staging/ctl/ctl-flash.cpio.gz ]; then need="$need ctl-flash.cpio.gz"; fi
$SSH "mkdir -p $D"
for spec in $need; do
	case "$spec" in
	*=*) B=${spec%%=*}; L=${spec#*=} ;;
	*)   B=$spec; L=staging/ctl/$spec ;;
	esac
	[ -s "$L" ] || { echo "missing $L" >&2; exit 1; }
	R=$D/$B
	printf 'copying %s -> %s ... ' "$L" "$R"
	timeout 300 sh -c "$SSH \"cat > '$R'\"" < "$L" || { echo "failed"; exit 1; }
	echo done
done

echo "=== md5 both ends, then compare ==="
$SSH "cd $D && tar xzf kx.tgz && chmod +x kexec ctl-kexec.sh ctl-exec.sh; df -h /tmp | tail -1; ls -l kexec"

# Compare, do not just print, and include the initrd: a copy that ends early still has a
# plausible size, `kexec --load` accepts it without complaint, and the failure only appears
# after the jump - with the board running a payload that cannot mount its own root, the reset
# routing masked, and nobody able to reach it.
CHK="$REMOTE_IMAGE $REMOTE_DTB"
[ -n "$INITRD" ] && CHK="$CHK $INITRD"
LOCAL_FULL=$(cd staging/ctl && md5sum $CHK | tr -d "\r")
REMOTE_FULL=$($SSH "cd $D && md5sum $CHK" | tr -d "\r")
printf '%s\n' "$LOCAL_FULL" | sed 's/^/  local  /'
printf '%s\n' "$REMOTE_FULL" | sed 's/^/  device /'
# The hash column is what is compared, one line per file. Keeping the newlines matters: the
# first version of this check ran `echo $LOCAL | awk '{print $1}'`, which flattens three lines
# into one and therefore compares only the first hash - a deliberately truncated initrd then
# passed it, which is exactly the silent failure the check exists to catch.
LH=$(printf '%s\n' "$LOCAL_FULL" | awk '{print $1}' | tr "\n" " ")
RH=$(printf '%s\n' "$REMOTE_FULL" | awk '{print $1}' | tr "\n" " ")
[ "$LH" = "$RH" ] || { echo "md5 mismatch on the staged payload - refusing to load it" >&2; exit 1; }

echo "=== kexec --load (vendor 4.19 + live dtb + ctltag=$CTLTAG + initrd=${INITRD:-none}) ==="
$SSH "cd $D && CTLTAG=$CTLTAG INITRD=${INITRD:-} IMAGE=${REMOTE_IMAGE} DTB=${REMOTE_DTB} sh ctl-kexec.sh"

cat <<EOF

Loaded, not running. Next, in this order:

1.  sh tools/catchctl.sh              # PC side: watches for the jump and reads back the verdict
2.  ssh root@192.168.9.1 'DELAY=150 CTLTAG=$CTLTAG setsid sh /tmp/ctl/ctl-exec.sh &'   # arm the jump

The CRM reset-routing register is left alone on purpose, so a jump that does not land
gets the board reset back to the flashed system by the vendor watchdog by itself.
EOF
