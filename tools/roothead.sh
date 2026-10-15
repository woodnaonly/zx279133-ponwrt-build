#!/bin/sh
# Record how the *running vendor 4.19* reads the head of the root partition, so the gate
# round can be scored on more than "did mainline reach /init".
#
# What the gate round cannot otherwise answer: mainline's spi-nand driver must produce the
# same bytes from the same cells as the driver that wrote them. ECC scheme and OOB layout are
# driver-configurable, so a mainline that boots perfectly from an initrd can still be unable
# to mount the jffs2 it will boot from for real - and that failure only appears after the
# flash has been changed, with no console. mtdblock reads exercise exactly the ECC path and
# nothing else, so the first 512 KiB of /dev/mtdblock5 hashed on both kernels is the cheapest
# decisive test available without a serial cable.
#
#	sh tools/roothead.sh
set -eu
cd "$(dirname "$0")/.."
DEV=${DEV:-root@192.168.9.1}
KEY=${KEY:-$HOME/.ssh/id_onu}
SSH="ssh -q -o BatchMode=yes -o IdentitiesOnly=yes -i $KEY -o UserKnownHostsFile=/dev/null -o StrictHostKeyChecking=no $DEV"

OUT=staging/ctl/roothead-419.md5
$SSH "echo kernel=\$(uname -r); dd if=/dev/mtdblock5 bs=64k count=8 2>/dev/null | md5sum | cut -d' ' -f1" >"$OUT.tmp"
kver=$(head -1 "$OUT.tmp" | cut -d= -f2)
md5=$(sed -n '2p' "$OUT.tmp")
[ -n "$md5" ] || { echo "no md5 back - /dev/mtdblock5 unreadable on this system?"; cat "$OUT.tmp"; exit 1; }
mv "$OUT.tmp" "$OUT"
echo "4.19 side recorded ($kver): first 512 KiB of /dev/mtdblock5 md5=$md5"
echo "  -> $OUT ; gatewatch.sh compares it against the payload's line"
