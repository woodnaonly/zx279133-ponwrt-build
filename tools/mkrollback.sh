#!/bin/sh
# Carve the rollback pair out of the partition dumps, so the mainline flash is reversible.
#
# The direct round writes `dtb` then `kernel`; both are one-way without a console - U-Boot has
# no A/B and no verified failover, so a mainline that does not come up from `bootm` leaves the
# board unreachable and nothing here can be run against it again. Rollback has to be a
# command, staged and hashed, before that round is fired - not something improvised after.
#
# Source is the pair of dumps already taken off this board: staging/ctl/mtd2.bin is the whole
# 8 MiB `kernel` partition and mtd3.bin the 1 MiB `dtb` partition, both with the real object at
# offset 0. The sizes are read out of the object headers rather than assumed, so what gets
# written back is exactly the image - a padded 8 MiB write would erase into nothing here but
# costs a minute of SPI-NAND for no reason.
#
#	sh tools/mkrollback.sh
set -eu
cd "$(dirname "$0")/.."
OUT=staging/rollback
mkdir -p "$OUT"
python - "$OUT" <<'PY'
import hashlib, struct, sys, gzip, os

out = sys.argv[1]
k = open("staging/ctl/mtd2.bin", "rb").read()
magic, hcrc, ctime, dsize, load, ep, dcrc = struct.unpack(">7I", k[:28])
assert magic == 0x27051956, "mtd2 dump does not start with a legacy uImage (%08x)" % magic
name = k[32:64].split(b"\0")[0].decode("latin1")
assert k[64:66] == b"\x1f\x8b", "uImage body is not gzip"
img = gzip.decompress(k[64:64 + dsize])
assert img[0x38:0x3C] == b"ARMd", "inflated vendor Image has no arm64 magic"
# The banner is not at a fixed offset inside the Image, so search the text rather than slice it.
i = img.find(b"Linux version ")
banner = img[i:img.index(b"\0", i)].decode("latin1", "replace") if i >= 0 else "(not found)"
kernel = k[:64 + dsize]
open(os.path.join(out, "flash-uImage"), "wb").write(kernel)
print("kernel : name=%r data=%d load/ep=0x%08x -> %s (%d B)" % (name, dsize, load,
      os.path.join(out, "flash-uImage"), len(kernel)))
print("         banner: %s" % banner[:78])

d = open("staging/ctl/mtd3.bin", "rb").read()
assert d[:4] == b"\xd0\x0d\xfe\xed", "mtd3 dump does not start with an FDT"
totalsize, = struct.unpack(">I", d[4:8])
assert totalsize <= len(d), "FDT totalsize %d beyond the dump" % totalsize
open(os.path.join(out, "flash-dtb.bin"), "wb").write(d[:totalsize])
print("dtb    : totalsize=%d -> %s" % (totalsize, os.path.join(out, "flash-dtb.bin")))
for f in ("flash-uImage", "flash-dtb.bin"):
    p = os.path.join(out, f)
    print("md5    : %s  %s" % (hashlib.md5(open(p, "rb").read()).hexdigest(), f))
PY

echo "=== the rollback dtb's own partition table (this is what the 4.19 boots with) ==="
python tools/dtbparts.py "$OUT/flash-dtb.bin" root=0xf00000,0xf100000

# A gate round cannot grant the permit for a rollback - the gated bytes are the ones replacing
# these. So the rollback's provenance is the simpler, stronger thing: these bytes came off this
# board while it was up and booted from them. Two ways to show that, in order of strength:
#
#   live-partition-match  the head of the *current* kernel/dtb partitions equals the carve, so
#                         the board is reading these exact bytes right now
#   preflash-snapshot     the dumps are the ones tools/preflashbackup.sh took over the ssh
#                         session that was serving the old system - which covers the case that
#                         matters most: restoring after mainline has already been written, when
#                         the live partitions no longer hold these bytes
DEV=${DEV:-root@192.168.9.1}
KEY=${KEY:-$HOME/.ssh/id_onu}
SSH="ssh -q -o BatchMode=yes -o IdentitiesOnly=yes -i $KEY -o UserKnownHostsFile=/dev/null -o StrictHostKeyChecking=no $DEV"
PERMIT=${PERMIT:-staging/ctl/flash.permit}
UM=$(md5sum "$OUT/flash-uImage" | awk '{print $1}')
DM=$(md5sum "$OUT/flash-dtb.bin" | awk '{print $1}')
echo "=== which of these bytes can be proved to be what this board boots? ==="
PROVEN=""
if [ "${SKIP_BOARD:-0}" != 1 ] && $SSH "cat /proc/mtd" >/tmp/mtd-live.$$ 2>/dev/null; then
	ok=1
	for spec in kernel:flash-uImage dtb:flash-dtb.bin; do
		L=${spec%%:*}; F=${spec##*:}
		I=$(sed -n "s/^\(mtd[0-9]*\):.*\"$L\"/\1/p" /tmp/mtd-live.$$ | head -1 | tr -d 'mtd')
		[ -n "$I" ] || { echo "  no /dev/mtd* labelled $L on the board"; ok=0; continue; }
		DV=$($SSH "dd if=/dev/mtd$I bs=4096 count=1 2>/dev/null | md5sum | cut -d' ' -f1")
		LV=$(head -c 4096 "$OUT/$F" | md5sum | awk '{print $1}')
		printf '  live %-8s mtd%s device=%s carve=%s ' "$L" "$I" "$DV" "$LV"
		[ "$DV" = "$LV" ] && echo MATCH || { echo MISMATCH; ok=0; }
	done
	[ "$ok" = 1 ] && PROVEN=live-partition-match
	rm -f /tmp/mtd-live.$$
fi
[ -n "$PROVEN" ] || echo "  live check unavailable or the flash already holds other bytes - trying the snapshot"

SNAPK=$(ls -1 backups/preflash-*/mtd2-*.bin.gz 2>/dev/null | tail -1)
SNAPD=$(ls -1 backups/preflash-*/mtd3-*.bin.gz 2>/dev/null | tail -1)
if [ -z "$PROVEN" ]; then
	if [ -z "$SNAPK" ] || [ -z "$SNAPD" ]; then
		echo "  no preflash snapshot (backups/preflash-*/mtd2-*.bin.gz) - run tools/preflashbackup.sh"
	else
		ok=1
		for spec in "$SNAPK:flash-uImage" "$SNAPD:flash-dtb.bin"; do
			S=${spec%%:*}; F=${spec##*:}
			SV=$(gunzip -c "$S" | head -c 4096 | md5sum | awk '{print $1}')
			LV=$(head -c 4096 "$OUT/$F" | md5sum | awk '{print $1}')
			printf '  snapshot %-12s %s=%s carve=%s ' "$F" "${S##*/}" "$SV" "$LV"
			[ "$SV" = "$LV" ] && echo MATCH || { echo MISMATCH; ok=0; }
		done
		[ "$ok" = 1 ] && PROVEN=preflash-snapshot
	fi
fi

if [ -n "$PROVEN" ]; then
	mkdir -p "$(dirname "$PERMIT")"
	printf 'ts=%s uimage_md5=%s dtb_md5=%s proven=%s snapshot=%s\n' \
		"$(date +%FT%T)" "$UM" "$DM" "$PROVEN" "${SNAPK:-none}" >>"$PERMIT"
	echo "  $OUT is permitted for a write ($PROVEN), recorded in $PERMIT"
else
	echo "  NOT permitted: the carve matches neither the live partitions nor the snapshot. Re-run"
	echo "  tools/preflashbackup.sh - the dumps this was carved from are not this board's."
fi

cat <<EOF

Rollback is the same tools, pointed at this directory - no board-side improvising:
  SRC=$OUT EXPECT='root=0xf00000,0xf100000' sh tools/flash-direct.sh write
Keep $OUT out of every cleanup: it is the only copy of the pair this board boots with now.
EOF
