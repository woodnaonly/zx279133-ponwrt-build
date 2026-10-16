#!/bin/sh
# Verify the staged rollback pair, at the moment an operator would rely on it.
#
# Why this is a tool and not a one-off check: staging/rollback/{flash-uImage,flash-dtb.bin}
# is the only copy of the pair this board boots with, and it is the single thing standing
# between "mainline did not come back" and a brick. Everything below is re-derived from the
# pre-flash snapshots on every run, so a later regeneration that goes wrong fails HERE rather
# than after the board has already gone quiet.
#
# Read-only. Run by tools/preflight.sh before a gate round; safe to run any time.
#
#	sh tools/checkrollback.sh
set -eu
cd "$(dirname "$0")/.."
OUT=${OUT:-staging/rollback}
EXPECT=${EXPECT:-root=0xf00000,0xf100000}
# LOCAL names the tree holding the board-derived state - the pre-flash snapshots and the
# vendor restore_set/. They are dumped off the board, so they cannot be in a clone, and on a
# machine where the repository sits inside a larger workspace they are one level up. Without
# this, sections 6 and 7 silently degrade: section 6 reports a missing snapshot as a FAIL and
# section 7 never runs, which reads as "the rollback pair is not usable" when the pair is
# fine and this tool simply looked in the wrong tree. Default "." is the single-clone layout.
LOCAL=${LOCAL:-.}
export LC_ALL=C

pass=0; fail=0; warn=0
ok()   { printf '  ok   %-52s %s\n' "$1" "${2:-}"; pass=$(( pass + 1 )); }
bad()  { printf '  FAIL %-52s %s\n' "$1" "${2:-}"; fail=$(( fail + 1 )); }
note() { printf '  .... %-52s %s\n' "$1" "${2:-}"; }
head2(){ printf '\n=== %s ===\n' "$1"; }

head2 "1. the pair is staged"
for f in flash-uImage flash-dtb.bin; do
	if [ -s "$OUT/$f" ]; then
		ok "$OUT/$f" "$(wc -c <"$OUT/$f") B  md5 $(md5sum <"$OUT/$f" | cut -c1-32)"
	else
		bad "$OUT/$f" "absent or empty - run tools/mkrollback.sh"
	fi
done
[ "$fail" -eq 0 ] || { echo; echo "ROLLBACK NOT USABLE"; exit 1; }

head2 "2. both are structurally what their partitions need"
mx=$(head -c 4 "$OUT/flash-uImage" | od -An -tx1 | tr -d ' \n')
[ "$mx" = 27051956 ] && ok "flash-uImage is a legacy uImage" "magic 27051956" \
	|| bad "flash-uImage is a legacy uImage" "magic ${mx:-none}"
mx=$(head -c 4 "$OUT/flash-dtb.bin" | od -An -tx1 | tr -d ' \n')
[ "$mx" = d00dfeed ] && ok "flash-dtb.bin is a flattened device tree" "magic d00dfeed" \
	|| bad "flash-dtb.bin is an FDT" "magic ${mx:-none}"

head2 "3. the carve is exact - no partition padding carried into the image"
python3 - "$OUT" <<'PY'
import struct, sys, os
out = sys.argv[1]
k = open(os.path.join(out, "flash-uImage"), "rb").read()
magic, hcrc, ctime, dsize, load, ep, dcrc = struct.unpack(">7I", k[:28])
name = k[32:64].split(b"\0")[0].decode("latin1", "replace")
want, got = 64 + dsize, len(k)
print("  %-52s %s" % ("uImage data %d + 64 header vs %d file bytes" % (dsize, got),
                      "exact" if want == got else "MISMATCH (want %d)" % want))
print("  %-52s %s" % ("uImage body is gzip", "yes" if k[64:66] == b"\x1f\x8b" else "NO"))
d = open(os.path.join(out, "flash-dtb.bin"), "rb").read()
totalsize, = struct.unpack(">I", d[4:8])
print("  %-52s %s" % ("FDT totalsize %d vs %d file bytes" % (totalsize, len(d)),
                      "exact" if totalsize == len(d) else "MISMATCH"))
print("  %-52s %r" % ("uImage name field", name))
sys.exit(0 if (want == got and k[64:66] == b"\x1f\x8b" and totalsize == len(d)) else 1)
PY
if [ $? -eq 0 ]; then ok "the carve is exact on all three counts" ""; else bad "the carve is exact" ""; fi

head2 "4. the kernel is the vendor one, not a mainline build"
ver=$(tail -c +65 "$OUT/flash-uImage" | gzip -dc 2>/dev/null | grep -a -o -m1 'Linux version [0-9][^ ]*' || true)
case "$ver" in
	*4.19*) ok "vendor 4.19 kernel" "$ver";;
	*6.18*) bad "vendor 4.19 kernel" "this is a mainline build: $ver - rolling back would NOT restore PON";;
	"")     note "kernel banner not readable" "the payload may not be gzip";;
	*)      bad "vendor 4.19 kernel" "unexpected: $ver";;
esac

head2 "5. the rollback dtb satisfies the table the vendor kernel boots with"
python tools/dtbparts.py "$OUT/flash-dtb.bin" "$EXPECT" | sed 's/^/  /'
if python tools/dtbparts.py "$OUT/flash-dtb.bin" "$EXPECT" >/dev/null 2>&1; then
	ok "flash-direct.sh's EXPECT gate will admit this pair" "$EXPECT"
else
	bad "flash-direct.sh's EXPECT gate will admit this pair" "$EXPECT - the rollback command would refuse"
fi

head2 "6. these bytes are what the board was booting before anything was changed"
SNAPK=$(ls -1 "$LOCAL"/backups/preflash-*/mtd2-*.bin.gz 2>/dev/null | tail -1)
SNAPD=$(ls -1 "$LOCAL"/backups/preflash-*/mtd3-*.bin.gz 2>/dev/null | tail -1)
if [ -z "$SNAPK" ] || [ -z "$SNAPD" ]; then
	bad "a pre-flash snapshot exists to compare against" "run tools/preflashbackup.sh"
else
	T=$(mktemp -d); trap 'rm -rf "$T"' EXIT
	gzip -dc "$SNAPK" >"$T/k"; gzip -dc "$SNAPD" >"$T/d"
	for spec in "flash-uImage:k:$SNAPK" "flash-dtb.bin:d:$SNAPD"; do
		f=${spec%%:*}; rest=${spec#*:}; s=${rest%%:*}; snap=${rest#*:}
		n=$(wc -c <"$OUT/$f")
		if head -c "$n" "$T/$s" | cmp -s - "$OUT/$f"; then
			# name the snapshot that pair was actually compared against - printing mtd2's
			# name for the dtb would be a false statement in the one place that matters
			ok "$f is a byte-exact prefix of ${snap##*/}" "$(wc -c <"$T/$s") B dump"
		else
			bad "$f matches ${snap##*/}" "differs from the pre-flash snapshot"
		fi
	done
	rm -rf "$T"
fi

head2 "7. the trap: restore_set/ is NOT a rollback set"
RS="$LOCAL/restore_set/root_stock.bin"
if [ -f "$RS" ]; then
	if grep -a -q 'ZXIC 133 UNI' "$RS" 2>/dev/null; then
		note "restore_set/root_stock.bin is ZTE firmware image data" "no filesystem in it"
		note "  it is not a substitute for $OUT" "the pair above is the only usable one"
	else
		note "restore_set/root_stock.bin identity not confirmed" "re-check before relying on it either way"
	fi
else
	note "restore_set/root_stock.bin absent" ""
fi

echo
echo "=== $pass ok, $fail FAIL, $warn warnings ==="
if [ "$fail" -eq 0 ]; then
	echo "ROLLBACK VERIFIED - these are the bytes that bring the board back:"
	echo "  SRC=$OUT EXPECT='$EXPECT' sh tools/flash-direct.sh write"
	exit 0
fi
echo "ROLLBACK NOT USABLE - fix the above before spending a board window."
exit 1