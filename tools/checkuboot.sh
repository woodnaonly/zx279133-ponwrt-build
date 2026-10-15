#!/bin/sh
# Step "刷入 uboot", verified rather than assumed.
#
# The board boots the community image today, which is evidence that a working U-Boot is
# already in the `boot` partition - but evidence is not a comparison, and overwriting the
# bootloader is the one write on this device with no way back without a serial cable. So
# this reads /dev/mtd1 and diffs it against staging/boot.bin (md5 daa42186c3b2c5be70a749cbfd9dd69a).
#
# A raw md5 mismatch does NOT mean the flasher has to run: U-Boot's environment lives at
# boot+0x180000, inside this same 2 MiB, and U-Boot rewrites it while the board is used
# (bootcount, ethaddr). So the differences are localised in 64-byte blocks and reported
# with their ranges - a diff confined to 0x180000..0x200000 is the env, and the code is
# identical. Anything outside that band is a real divergence and gets listed.
#
#	sh tools/checkuboot.sh
set -eu
cd "$(dirname "$0")/.."
DEV=${DEV:-root@192.168.9.1}
KEY=${KEY:-$HOME/.ssh/id_onu}
SSH="ssh -q -o BatchMode=yes -o IdentitiesOnly=yes -i $KEY -o UserKnownHostsFile=/dev/null -o StrictHostKeyChecking=no $DEV"
OUT=backups/live-20261012
mkdir -p "$OUT"

[ -s staging/boot.bin ] || { echo "staging/boot.bin missing" >&2; exit 1; }
echo "local boot.bin : $(stat -c %s staging/boot.bin) B  md5 $(md5sum staging/boot.bin | awk '{print $1}')"

echo "reading /dev/mtd1 (2 MiB) from the board..."
$SSH "dd if=/dev/mtd1 bs=64k 2>/dev/null | gzip -9" >"$OUT/mtd1-boot.bin.gz"
gunzip -c "$OUT/mtd1-boot.bin.gz" >"$OUT/mtd1-boot.bin"
LIVE=$(md5sum "$OUT/mtd1-boot.bin" | awk '{print $1}')
echo "live  /dev/mtd1: $(stat -c %s $OUT/mtd1-boot.bin) B  md5 $LIVE"

for f in "$OUT/mtd1-boot.bin" staging/boot.bin; do
	printf 'banner %-28s : ' "$(basename $f)"
	python - "$f" <<'EOF'
import re, sys
d = open(sys.argv[1], "rb").read()
m = re.search(rb"U-Boot 20[0-9.]+[^0-9]{0,80}", d)
print((m.group(0)[:80].decode("latin1").strip() if m else "no U-Boot banner"))
EOF
done

python - "$OUT/mtd1-boot.bin" staging/boot.bin <<'EOF'
import sys
a, b = (open(p, "rb").read() for p in sys.argv[1:3])
n = min(len(a), len(b))
print("sizes: live=%d file=%d" % (len(a), len(b)))
if a == b:
    print("IDENTICAL - the community U-Boot in flash is byte-for-byte staging/boot.bin."
          "\n  -> nothing to write for step 1; go straight to the mainline kernel/dtb.")
    sys.exit(0)
BLK = 64
runs, start = [], None
for i in range(0, n - BLK + 1, BLK):
    diff = a[i:i + BLK] != b[i:i + BLK]
    if diff and start is None:
        start = i
    elif not diff and start is not None:
        runs.append((start, i)); start = None
if start is not None:
    runs.append((start, n))
env_lo, env_hi = 0x180000, 0x200000
outside = [(s, e) for (s, e) in runs if not (s >= env_lo and e <= env_hi)]
inenv = [(s, e) for (s, e) in runs if s >= env_lo and e <= env_hi]
print("differing 64-byte runs: %d, total %d B" % (len(runs), sum(e - s for s, e in runs)))
for s, e in (runs if len(runs) <= 12 else runs[:12]):
    print("  0x%06x..0x%06x (%d B)%s" % (s, e, e - s, "  [env area]" if (s, e) in inenv else ""))
if not outside:
    print("ALL DIFFERENCES ARE IN THE ENV AREA (0x180000..0x200000) - bootloader code is"
          "\n  identical, so U-Boot does not need reflashing. Note %d env block(s)." % len(inenv))
else:
    print("%d run(s) OUTSIDE the env area, %d inside:" % (len(outside), len(inenv)))
    for s, e in outside[:20]:
        print("  0x%06x..0x%06x (%d B)" % (s, e, e - s))
    print("  -> real divergence. Read it before writing anything: this is the one partition"
          "\n     whose failure mode has no recovery without a serial console.")
EOF
