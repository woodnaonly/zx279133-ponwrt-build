#!/bin/sh
# 本地排演套件（local rehearsal suite）：依赖 staging/ 下的真实构建产物，
# 在只 clone 本仓库的环境里跑不起来，也**没有**接进 CI。
# 为什么、怎么跑、缺的那个夹具从哪来：同目录 README.md。
# Follow-up to kernel_mismatch_probe.sh, whose header fields were printed with an
# off-by-one byte index (it showed hcrc as "time", size as "load", and the uImage name as
# garbage). Parse by byte offset instead, and - the actual question - compare the PAYLOAD,
# everything after the 64-byte header. Identical payload with a different header means the
# two files are the same kernel re-wrapped, which is fine. Different payload means the
# staged flash-set and the sysupgrade image disagree, which is not fine.
set -u
cd /mnt/c/ugit/SK-D840N4
export LC_ALL=C

IMG=staging/trial/flash-sysupgrade.bin
W=$(mktemp -d)
member=$(tar tf "$IMG" | grep -m1 '/kernel$')
tar xf "$IMG" -C "$W" "$member"
A="$W/$member"
B=staging/trial/flash-uImage

byte() { od -An -tu1 -j "$2" -N1 "$1" 2>/dev/null | tr -d ' \n'; }
be32() { # big-endian 32-bit at offset
	od -An -tx1 -j "$2" -N4 "$1" | tr -d ' \n'
}

echo "=== 1. uImage header, decoded by byte offset (u_image_header) ==="
# u32 magic, hcrc, time, size, load, ep, dcrc; u8 os, arch, type, comp; char name[32]; ver[32]
printf '  %-10s %-36s %s\n' "field" "kernel in sysupgrade image" "staging/trial/flash-uImage"
row() {
	printf '  %-10s %-36s %s\n' "$1" "$2" "$3"
}
row magic     "0x$(be32 "$A" 0)"        "0x$(be32 "$B" 0)"
row hcrc      "0x$(be32 "$A" 4)"        "0x$(be32 "$B" 4)"
row time      "0x$(be32 "$A" 8)"        "0x$(be32 "$B" 8)"
row size      "0x$(be32 "$A" 12)"       "0x$(be32 "$B" 12)"
row load      "0x$(be32 "$A" 16)"       "0x$(be32 "$B" 16)"
row ep        "0x$(be32 "$A" 20)"       "0x$(be32 "$B" 20)"
row dcrc      "0x$(be32 "$A" 24)"       "0x$(be32 "$B" 24)"
row os        "$(byte "$A" 28)/0x$(printf '%x' "$(byte "$A" 28)")" "$(byte "$B" 28)/0x$(printf '%x' "$(byte "$B" 28)")"
row arch      "$(byte "$A" 29)/0x$(printf '%x' "$(byte "$A" 29)")" "$(byte "$B" 29)/0x$(printf '%x' "$(byte "$B" 29)")"
row type      "$(byte "$A" 30)/0x$(printf '%x' "$(byte "$A" 30)")" "$(byte "$B" 30)/0x$(printf '%x' "$(byte "$B" 30)")"
row comp      "$(byte "$A" 31)/0x$(printf '%x' "$(byte "$A" 31)")" "$(byte "$B" 31)/0x$(printf '%x' "$(byte "$B" 31)")"
na=$(dd if="$A" bs=1 skip=32 count=32 2>/dev/null | tr -d '\000')
nb=$(dd if="$B" bs=1 skip=32 count=32 2>/dev/null | tr -d '\000')
row name      "\"$na\"" "\"$nb\""

echo
echo "=== 2. build timestamps, decoded ==="
for f in "$A" "$B"; do
	t=$(be32 "$f" 8)
	# interpret as epoch if it looks like one (2020..2035)
	dec=$(( 0x$t ))
	if [ "$dec" -gt 1577836800 ] && [ "$dec" -lt 2073744000 ]; then
		when=$(date -u -d "@$dec" '+%Y-%m-%d %H:%M:%S UTC' 2>/dev/null)
	else
		when="(not an epoch second)"
	fi
	printf '  %-34s 0x%08x = %s\n' "${f##*/}" "$dec" "$when"
done

echo
echo "=== 3. THE QUESTION: is the payload after the header identical? ==="
printf '  payload size A (image kernel) : %s\n' "$(( $(wc -c <"$A") - 64 ))"
printf '  payload size B (flash-uImage) : %s\n' "$(( $(wc -c <"$B") - 64 ))"
if cmp -s -i 64:64 "$A" "$B"; then
	echo "  PAYLOAD IDENTICAL -> same kernel, two different uImage headers"
else
	echo "  PAYLOAD DIFFERS"
	# locate the first differing byte properly
	first=$(cmp "$A" "$B" 2>&1 | sed -n 's/.*differ: byte \([0-9]*\).*/\1/p')
	echo "  first difference at byte: ${first:-unknown}"
	[ -n "$first" ] && [ "$first" -ge 64 ] && echo "  (that is payload offset $(( first - 64 )))"
fi

echo
echo "=== 4. do the two headers' data CRC agree with their own payloads? ==="
# uImage stores dcrc = CRC32 of the payload. If both headers describe the same payload,
# dcrc must match even when hcrc/time/name differ.
dA=$(be32 "$A" 24); dB=$(be32 "$B" 24)
if [ "$dA" = "$dB" ]; then
	echo "  dcrc matches (0x$dA) - consistent with one shared payload"
else
	echo "  dcrc differs: 0x$dA vs 0x$dB - different payloads"
fi

echo
echo "=== 5. embedded kernel version, both files ==="
for f in "$A" "$B"; do
	printf '  %-20s ' "${f##*/}"
	grep -a -o -m1 'Linux version [0-9][^ ]*' "$f" 2>/dev/null || echo "(compressed - not readable in plain grep)"
done

echo
echo "=== 6. what else is in the flash-set, and does it agree with the sysupgrade tar? ==="
ls -l --time-style=long-iso staging/trial/ | sed 's/^/  /'
if [ -f staging/trial/flash-set.tgz ]; then
	echo "  --- flash-set.tgz contents ---"
	tar tzvf staging/trial/flash-set.tgz 2>/dev/null | sed 's/^/    /'
fi

rm -rf "$W"