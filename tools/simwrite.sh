#!/bin/sh
# Run tools/flash-write.sh - the script that erases the two partitions this board boots from -
# against a fake flash, on the PC, before it is ever run on the hardware.
#
# Why: on the board there is no second chance and no console, so "it looks right" is not a
# test. The worker's whole value is its guards (label resolution, offset check, oversize
# check, dtb-before-kernel order, watchdog masking, readback), and every one of those can be
# exercised here. The fake devices are one file per partition, which is exactly how the worker
# uses them: erase 0..size, write at 0 for len bytes, read 0..len back.
#
#	sh tools/simwrite.sh
set -eu
cd "$(dirname "$0")/.."
T=${T:-/tmp/flashsim}
# Which pair to simulate. Defaults to the mainline images, but the ROLLBACK runs on the
# vendor pair in staging/rollback - different files, different sizes - and that is the one
# written when a board has to be brought back, so it gets simulated too:
#   SRC=staging/rollback sh tools/simwrite.sh
SRC=${SRC:-staging/trial}
KIMG_FILE=$SRC/flash-uImage
[ -s "$KIMG_FILE" ] || { echo "need $KIMG_FILE (sh tools/gettrial.sh flash <run>)" >&2; exit 1; }
KIMG=$(wc -c <"$KIMG_FILE")
echo "=== simulating the pair in $SRC ==="

world() { # $1 = kernel partition offset, $2 = label for mtd3 (the dtb partition)
	rm -rf "$T"; mkdir -p "$T/sys/class/mtd/mtd1" "$T/sys/class/mtd/mtd2" "$T/sys/class/mtd/mtd3" \
		"$T/sys/class/mtd/mtd4" "$T/sys/class/mtd/mtd5" "$T/dev" "$T/bin" "$T/flash"
	for d in 1 2 3 4 5; do : >"$T/dev/mtd$d"; done
	echo boot >"$T/sys/class/mtd/mtd1/name"; echo 0        >"$T/sys/class/mtd/mtd1/offset"; echo 2097152  >"$T/sys/class/mtd/mtd1/size"
	echo kernel >"$T/sys/class/mtd/mtd2/name"; echo $1     >"$T/sys/class/mtd/mtd2/offset"; echo 8388608  >"$T/sys/class/mtd/mtd2/size"
	echo "${2:-dtb}" >"$T/sys/class/mtd/mtd3/name"; echo 10485760 >"$T/sys/class/mtd/mtd3/offset"; echo 1048576 >"$T/sys/class/mtd/mtd3/size"
	echo "parameter tags" >"$T/sys/class/mtd/mtd4/name"; echo 11534336 >"$T/sys/class/mtd/mtd4/offset"; echo 4194304 >"$T/sys/class/mtd/mtd4/size"
	echo root >"$T/sys/class/mtd/mtd5/name"; echo 15728640 >"$T/sys/class/mtd/mtd5/offset"; echo 252706816 >"$T/sys/class/mtd/mtd5/size"
	for d in 1 2 3 4 5; do echo 131072 >"$T/sys/class/mtd/mtd$d/erasesize"; done
	cat >"$T/bin/mtd_debug" <<EOF
#!/bin/sh
echo "mtd_debug \$*" >>"$T/calls"
# mtd_debug's own argument order is erase|write <device> <offset> <length> [file]
[ "\$1" = write ] || exit 0
head -c "\$4" "\$5" > "\$2"
exit 0
EOF
	cat >"$T/bin/mtd" <<EOF
#!/bin/sh
echo "mtd \$*" >>"$T/calls"
[ "\$1" = write ] || exit 0
dev=mtd3; [ "\$3" = kernel ] && dev=mtd2
cp "\$2" "$T/dev/\$dev"
exit 0
EOF
	cat >"$T/bin/devmem2" <<EOF
#!/bin/sh
echo "devmem2 \$*" >>"$T/calls"
echo "Value at address 0x10E10208 = 0x1F1"
exit 0
EOF
	chmod +x "$T/bin"/*
	rm -f "$T/calls"
	cp "$SRC/flash-uImage" "$SRC/flash-dtb.bin" "$T/flash/"
}

run() { MTDSYS=$T/sys/class/mtd DEVP=$T/dev D=$T/flash PATH=$T/bin:$PATH sh tools/flash-write.sh || true; }
first() { grep -n "$1" "$T/calls" | head -1 | cut -d: -f1; }	# line number of a call
fails=0
check() {
	if eval "$2"; then echo "  ok   $1"; else echo "  FAIL $1"; fails=$(( fails + 1 )); fi
}

echo "=== 1. the honest case: correct offsets, images that fit ==="
world 2097152
run
sed 's/^/  | /' "$T/flash/write.log"
check "prefers mtd_debug over the label tool"  'grep -q "tool=mtd_debug" $T/flash/write.log'
check "masks the watchdog before erasing"      'grep -q "routing masked" $T/flash/write.log && test "$(first devmem2)" -lt "$(first erase)"'
check "erases the dtb partition before writing it" 'test "$(first "erase.*mtd3")" -lt "$(first "write.*mtd3")"'
check "erases the kernel partition before writing it" 'test "$(first "erase.*mtd2")" -lt "$(first "write.*mtd2")"'
check "the dtb partition is programmed first"  'test "$(first "write.*mtd3")" -lt "$(first "write.*mtd2")"'
check "the kernel partition is programmed last" 'test "$(first "write.*mtd2")" -lt "$(grep -c . $T/calls)"'
check "restores the watchdog afterwards"       'test "$(grep -n "routing restored" $T/flash/write.log | cut -d: -f1)" -gt "$(grep -n "kernel rc=" $T/flash/write.log | cut -d: -f1)"'
check "both partitions read back byte-identical" 'test "$(grep -c READ_OK $T/flash/write.log)" = 2'
check "reaches DONE"                           'grep -q DONE $T/flash/write.log'
check "the kernel image landed"                'cmp -s $T/dev/mtd2 "$SRC/flash-uImage"'
check "the dtb landed"                         'cmp -s $T/dev/mtd3 "$SRC/flash-dtb.bin"'
check "nothing was written past the image"     'test "$(wc -c <$T/dev/mtd2)" = "$KIMG"'
check "the erase length was the whole partition" 'grep -q "erase .*mtd2 0 8388608" $T/calls'
check "the write length was the image, padded to the erase block by nothing" 'grep -q "write .*mtd2 0 $KIMG " $T/calls'

echo "=== 2. the kernel partition is not where the vendor table says ==="
world 2228224
run
check "aborts"                 'grep -q "ABORT: unexpected offsets" $T/flash/write.log'
check "names the offset it saw" 'grep -q "unexpected offsets k=2228224" $T/flash/write.log'
check "erased nothing"         'test ! -e $T/calls'

echo "=== 3. an image larger than its partition (would overrun into parameter tags) ==="
world 2097152
cat $T/flash/flash-uImage $T/flash/flash-uImage >$T/flash/x && mv $T/flash/x $T/flash/flash-uImage
run
check "aborts on size"   'grep -q "ABORT: uImage .* > kernel partition" $T/flash/write.log'
check "erased nothing"   'test ! -e $T/calls'

echo "=== 4. a table whose dtb partition has been renamed ==="
world 2097152 dtb-moved
run
check "aborts on the missing label" 'grep -q "ABORT: no partition labelled dtb" $T/flash/write.log'
check "erased nothing"              'test ! -e $T/calls'

echo "=== 5. a system that only carries the label tool ==="
world 2097152
rm -f $T/bin/mtd_debug
run
check "falls back to mtd"     'grep -q "tool=mtd (ubox" $T/flash/write.log'
check "still reaches DONE"    'grep -q DONE $T/flash/write.log'
check "still verifies the dtb" 'grep -q "READ_OK dtb" $T/flash/write.log'

echo "=== 6. a chip that does not store what it was given ==="
# The only scenario that can produce READ_FAIL. It matters more than it looks: READ_FAIL
# is what makes flash-direct.sh say "NOT CONFIRMED ... do NOT reboot", and every other
# scenario here has the fake device storing bytes perfectly, so a readback that could
# never fail would still pass all of them.
world 2097152
cat >"$T/bin/mtd_debug" <<EOF
#!/bin/sh
echo "mtd_debug \$*" >>"$T/calls"
# mtd_debug's own argument order is erase|write <device> <offset> <length> [file]
[ "\$1" = write ] || exit 0
head -c "\$4" "\$5" > "\$2"
# scribble over one byte the chip was told to keep
printf 'X' | dd of="\$2" bs=1 seek=1000 conv=notrunc 2>/dev/null
exit 0
EOF
chmod +x "$T/bin/mtd_debug"
run
sed 's/^/  | /' "$T/flash/write.log"
check "reports READ_FAIL for both partitions" 'test "$(grep -c READ_FAIL $T/flash/write.log)" = 2'
check "never claims READ_OK"                 'test "$(grep -c READ_OK $T/flash/write.log)" = 0'
check "still reaches DONE"                   'grep -q DONE $T/flash/write.log'
check "the write itself did happen"          'grep -q "write .*mtd2 0 " $T/calls'

echo "=== 7. the same failure through the label-tool fallback ==="
world 2097152
rm -f "$T/bin/mtd_debug"
cat >"$T/bin/mtd" <<EOF
#!/bin/sh
echo "mtd \$*" >>"$T/calls"
[ "\$1" = write ] || exit 0
dev=mtd3; [ "\$3" = kernel ] && dev=mtd2
cp "\$2" "\$T/dev/\$dev"
printf 'X' | dd of="\$T/dev/\$dev" bs=1 seek=1000 conv=notrunc 2>/dev/null
exit 0
EOF
chmod +x "$T/bin/mtd"
run
check "the fallback path also reports READ_FAIL" 'test "$(grep -c READ_FAIL $T/flash/write.log)" = 2'
check "and never READ_OK"                        'test "$(grep -c READ_OK $T/flash/write.log)" = 0'

echo
[ "$fails" = 0 ] && echo "SIM OK - every guard fired when it had to and nothing was erased when it must not be" || echo "SIM FAILURES: $fails"
exit "$fails"
