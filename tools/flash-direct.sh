#!/bin/sh
# Program kernel + dtb from the running system, no kexec payload involved.
#
# Why this exists next to flash-round.sh: the RAM-root flasher has to carry the 8.1 MB
# uImage *inside its initrd* (nothing survives the jump except the initrd), and the biggest
# initrd this kernel has ever been proven to load is 577 kB. `kexec --load` would tell us
# so, but only by failing a round. For a write that does not touch `root` there is no need
# to leave the running system at all:
#
#   - the kernel partition being overwritten is not in use - the running kernel is already
#     in RAM and nothing mmaps /dev/mtdblock2,
#   - the dtb partition is not mounted either,
#   - ssh stays up the whole time, so every rc and every dmesg line is read directly
#     instead of reconstructed from an http page.
#
# The RAM root stays the only correct way to rewrite `root`, which is mounted under /.
#
# And the standing rule "program flash from a RAM root, never from the mounted jffs2" is
# about exactly that case - it does not forbid writing *other* partitions in place. The one
# thing worth re-checking before trusting it is the watchdog: an erase of 8 MiB stalls the
# SPI-NAND controller, and therefore all of /, for a minute. zx_wdt on this vendor kernel is
# fed by an in-kernel timer (three instances armed at 8 s) and there is no /dev/watchdog node
# on this image, so nothing in userspace can miss a feeding window while the chip is busy.
#
# Order is dtb first, kernel LAST: until the kernel write completes the board still boots
# the old pair, so a session that dies half-way leaves a bootable system rather than a
# mainline kernel with the vendor dtb (which does not boot - the vendor 4.19 needs its own
# bindings, and the new mtd table would not match the labels it reads).
#
#	sh tools/flash-direct.sh dry     # probe tools/geometry, write nothing (default)
#	sh tools/flash-direct.sh write   # erase+write dtb, then kernel
set -eu
cd "$(dirname "$0")/.."
DEV=${DEV:-root@192.168.9.1}
KEY=${KEY:-$HOME/.ssh/id_onu}
D=${D:-/tmp/flash}
MODE=${1:-dry}
# Which directory the two images come from. `staging/rollback` holds the pair this board boots
# with today, so the same verified path that writes mainline also undoes it.
SRC=${SRC:-staging/trial}
# What the dtb's root partition must say. The mainline table puts root at 240 MiB, the vendor
# one at 241 MiB, and both are legitimate for their own kernel - so the expectation travels
# with the source rather than being hard-coded.
EXPECT=${EXPECT:-root=0xf00000,0xf000000}
# Snapshot what is about to be overwritten before overwriting it. BACKUP=0 only makes sense
# when tools/preflashbackup.sh already ran in this same window.
BACKUP=${BACKUP:-1}
SSH="ssh -q -o BatchMode=yes -o IdentitiesOnly=yes -i $KEY -o UserKnownHostsFile=/dev/null -o StrictHostKeyChecking=no $DEV"

for f in "$SRC/flash-uImage" "$SRC/flash-dtb.bin"; do
	[ -s "$f" ] || { echo "$f missing - mainline: sh tools/gettrial.sh flash <run> | rollback: sh tools/mkrollback.sh" >&2; exit 1; }
done
python tools/dtbparts.py "$SRC/flash-dtb.bin" $EXPECT \
	|| { echo "refusing: $SRC/flash-dtb.bin does not satisfy $EXPECT" >&2; exit 1; }
# The one-way door: a write is only allowed for bytes a previous round booted on this board.
# `dry` is unaffected, so the survey can still be run to decide what is worth gating.
if [ "$MODE" != dry ]; then
	sh tools/gatecheck.sh "$SRC/flash-uImage" "$SRC/flash-dtb.bin" || exit 1
	# The second lock on the same one-way door: `restore_set/` is the *vendor* quadruple, which
	# would not bring back the system running now, so the bytes about to be erased have to be
	# captured first. A backup that fails is a board that may already be dying - less still a
	# write.
	[ "$BACKUP" = 0 ] || DEV="$DEV" sh tools/preflashbackup.sh ||
		{ echo "refusing: no verified snapshot of the partitions being overwritten (BACKUP=0 to skip)"; exit 1; }
fi

echo "=== stage the images from $SRC into /tmp (tmpfs, nothing written to flash yet) ==="
$SSH "mkdir -p $D"
for spec in flash-uImage flash-dtb.bin; do
	printf '  %s ... ' "$spec"
	timeout 300 sh -c "$SSH \"cat > $D/$spec\"" <"$SRC/$spec" || { echo failed; exit 1; }
	echo done
done
# Two-sided md5: a copy that ends early would still be a plausible size, and writing half a
# kernel partition is exactly the failure this whole procedure is meant to avoid.
echo "=== verify ==="
LOCAL=$(md5sum "$SRC/flash-uImage" "$SRC/flash-dtb.bin" | awk '{print $1}' | tr '\n' ' ')
REMOTE=$($SSH "md5sum $D/flash-uImage $D/flash-dtb.bin | awk '{print \$1}' | tr '\n' ' '")
echo "local : $LOCAL"
echo "device: $REMOTE"
[ "$(echo $LOCAL)" = "$(echo $REMOTE)" ] || { echo "md5 mismatch - refusing to write" >&2; exit 1; }

echo "=== what the running system says the target partitions are ==="
$SSH "for i in \$(grep -E '\"(kernel|dtb|root)\"' /proc/mtd | cut -d: -f1 | tr -d 'mtd '); do
	echo \"mtd\$i \$(cat /sys/class/mtd/mtd\$i/name) off=\$(cat /sys/class/mtd/mtd\$i/offset) size=\$(cat /sys/class/mtd/mtd\$i/size) erasesize=\$(cat /sys/class/mtd/mtd\$i/erasesize)\";
done
for t in mtd mtd_debug nandwrite flash_erase; do printf 'tool %-10s %s\n' \$t \"\$(command -v \$t || echo -)\"; done
printf 'kernel size 8388608 vs uImage %s\n' \"\$(wc -c <$D/flash-uImage | tr -d ' ')\""

if [ "$MODE" = dry ]; then
	cat <<EOF

DRY RUN - nothing was erased and nothing was written. To proceed:
  sh tools/flash-direct.sh write
Requires: /proc/mtd shows kernel@2097152 and dtb@10485760, uImage <= 8388608 B, and either
'mtd' or 'mtd_debug'+'nandwrite' present. 'write' additionally refuses unless a gate round has
recorded these exact bytes as booted:
  sh tools/gatecheck.sh $SRC/flash-uImage $SRC/flash-dtb.bin
and starts by dumping boot/kernel/dtb/tags to backups/preflash-<date>/ (BACKUP=0 to skip that).
EOF
	exit 0
fi

echo "=== hand the write to a detached worker ==="
# The worker runs under setsid and logs to RAM: an ssh session that dies mid-erase must not
# be able to leave the board half-written, and reading the log afterwards is not a race.
timeout 120 sh -c "$SSH \"cat > $D/flash-write.sh\"" <tools/flash-write.sh
echo "worker md5 device : $($SSH "md5sum $D/flash-write.sh" | awk '{print $1}')"
echo "worker md5 local  : $(md5sum tools/flash-write.sh | awk '{print $1}')"
$SSH "cd $D && rm -f write.log && setsid sh $D/flash-write.sh >/dev/null 2>&1 & echo armed"

echo "=== polling $D/write.log (the MTD chip is busy for a minute; a stalling shell is expected) ==="
prev=""
n=0
while [ $n -lt 120 ]; do
	n=$(( n + 1 ))
	LOG=$($SSH "cat $D/write.log 2>/dev/null" 2>/dev/null || true)
	if [ "$LOG" != "$prev" ]; then
		printf '%s\n' "$LOG" | sed 's/^/  /'
		prev="$LOG"
	fi
	case "$prev" in *DONE*|*ABORT*) break ;; esac
	sleep 5
done

# `grep -c` exits 1 when it finds nothing, and under `set -eu` that failure propagates out
# of the assignment and kills the script HERE - before the case below can say anything.
# Zero READ_OK lines is exactly what ABORT and TIMEOUT look like, i.e. the two outcomes
# where "flash untouched" and "do not reboot" are the whole message. Swallow the status;
# the count itself is already 0.
ok=$(printf '%s\n' "$prev" | grep -c "READ_OK" || true)
case "$prev" in
*ABORT*) echo "ABORTED before the first erase (see the guards in flash-write.sh) - flash untouched";;
*DONE*) [ "$ok" = 2 ] \
		&& echo "PASS: dtb and kernel both read back byte-identical to the staged images" \
		|| echo "NOT CONFIRMED: only $ok of 2 READ_OK lines - do NOT reboot until this is resolved";;
*) echo "TIMEOUT after 10 min with no DONE - the worker may still be running. Keep reading:";
   echo "  ssh root@192.168.9.1 cat $D/write.log";;
esac


cat <<EOF

If the two READ_OK lines appeared, the flash now holds mainline. Reboot deliberately:
  ssh root@192.168.9.1 reboot
Then the only jack that can answer is the RTL8226/2.5G one. The flashed image puts it at
192.168.9.1: ci-build-repo's 02_network binds lan straight to eth0 and spells the address
out as 192.168.9.1, so there is no br-lan and nothing carries 192.168.9.2 - that address
belonged to the old community root, where lan was br-lan (ports eth1..eth3, which
mainline has no driver for) and eth0 was wan. ping/ARP at 192.168.9.1 means mainline
booted; if dropbear is bound to the 'lan' interface, tools/reachcheck.sh unwraps that
binding beforehand. The audit after rebooting is plain:
  sh tools/postflash.sh
with no HOST: it probes 192.168.9.1, then 192.168.9.2, then 192.168.1.1 and settles on
the one that returns a uname line. Passing HOST= pins it to a single address, so naming
192.168.9.2 there would report a healthy board as dead.
Going back is the same script pointed at the staged vendor pair, which tools/gatecheck.sh
already permits because those bytes came off this board:
  SRC=staging/rollback EXPECT='root=0xf00000,0xf100000' sh tools/flash-direct.sh write
Keep /tmp/flash and staging/rollback until the new system has been audited.
EOF
