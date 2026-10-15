#!/bin/sh
# Read-only copy of every partition the flash round is about to overwrite, before it runs.
#
# `restore_set/` holds the *stock vendor* set from 2026-10-09 (14 partitions, vendor names).
# It cannot restore the system that is installed now, which is the one the trial falls back
# to, so the community boot/kernel/dtb/tags quadruple gets its own snapshot. root (241 MiB)
# is deliberately not dumped: mode `write` never touches it, and if it ever has to be
# rewritten the image is reproducible from CI rather than from a backup.
#
# Each file is md5-compared on both ends: the device hashes what dd read, the PC hashes what
# arrived, and a transfer that ends early still has a plausible length, so both numbers are
# recorded in the manifest.
#
#	sh tools/preflashbackup.sh
set -eu
cd "$(dirname "$0")/.."
DEV=${DEV:-root@192.168.9.1}
KEY=${KEY:-$HOME/.ssh/id_onu}
OUT=backups/preflash-$(date +%Y%m%d)
SSH="ssh -q -o BatchMode=yes -o IdentitiesOnly=yes -i $KEY -o UserKnownHostsFile=/dev/null -o StrictHostKeyChecking=no $DEV"

mkdir -p "$OUT"
echo "# taken $(date '+%F %T') from $DEV - the PC side of the pair, device state follows" >"$OUT/MANIFEST"
$SSH "cat /proc/cmdline; head -8 /proc/mtd" >>"$OUT/MANIFEST"

# 1=boot 2=kernel 3=dtb 4="parameter tags" in the running 4.19's table (mtd0 is `whole`).
for i in 1 2 3 4; do
	name=$($SSH "grep -m1 '^mtd$i:' /proc/mtd | sed 's/.*\"\(.*\)\"/\1/' | tr ' ' _")
	f="$OUT/mtd$i-$name.bin.gz"
	printf 'mtd%d (%s) ... ' "$i" "$name"
	DEVMD5=$($SSH "dd if=/dev/mtd$i bs=64k 2>/dev/null | md5sum" | awk '{print $1}')
	$SSH "dd if=/dev/mtd$i bs=64k 2>/dev/null | gzip -9" >"$f"
	LOCALMD5=$(gunzip -c "$f" | md5sum | awk '{print $1}')
	printf 'device %s local %s ' "$DEVMD5" "$LOCALMD5"
	[ "$DEVMD5" = "$LOCALMD5" ] && echo "OK" || { echo "MISMATCH"; exit 1; }
	echo "mtd$i $name dev=$DEVMD5 local=$LOCALMD5 $(stat -c %s $f) B(gz)" >>"$OUT/MANIFEST"
done

echo "snapshot in $OUT:"; cat "$OUT/MANIFEST"
