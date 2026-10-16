#!/bin/sh
# 本地排演套件（local rehearsal suite）：依赖 staging/ 下的真实构建产物，
# 在只 clone 本仓库的环境里跑不起来，也**没有**接进 CI。
# 为什么、怎么跑、缺的那个夹具从哪来：同目录 README.md。
# flash-sysupgrade.bin's kernel member does not match staging/trial/flash-uImage
# byte-for-byte (dtb and root both match). Is that a real difference between two builds,
# or just an artefact of how the file was assembled? Compare uImage headers, which carry
# the kernel size, a CRC and the compression flag - all cheap to read and impossible to
# fake by accident.
set -u
cd /mnt/c/ugit/SK-D840N4
export LC_ALL=C

IMG=staging/trial/flash-sysupgrade.bin
W=$(mktemp -d)
member=$(tar tf "$IMG" | grep -m1 '/kernel$')
tar xf "$IMG" -C "$W" "$member"
A="$W/$member"          # the kernel inside the sysupgrade image
B=staging/trial/flash-uImage

echo "=== 1. sizes ==="
printf '  in-image kernel : %s bytes  (%s)\n' "$(wc -c <"$A")" "${A##*/}"
printf '  staged flash-uImage: %s bytes\n' "$(wc -c <"$B")"
printf '  difference     : %s bytes\n' "$(( $(wc -c <"$B") - $(wc -c <"$A") ))"

echo
echo "=== 2. uImage header fields ==="
# struct u_image_header: magic(4) hcrc(4) time(4) size(4) load(4) ep(4) dcrc(4)
#                        os(1) arch(1) type(1) comp(1) name(32) version[]
hdr() { # file
	od -An -tx1 -N64 "$1" | tr -s ' ' '\n' | grep -v '^$' | tr '\n' ' '
}
for f in "$A" "$B"; do
	echo "  --- ${f##*/}"
	h=$(hdr "$f")
	set -- $h
	printf '      magic=%s%s%s%s  time=0x%s%s%s%s  size=0x%s%s%s%s\n' \
		"$1" "$2" "$3" "$4" "$5" "$6" "$7" "$8" "$9" "${10}" "${11}" "${12}"
	printf '      load=0x%s%s%s%s  ep=0x%s%s%s%s  os=%s arch=%s type=%s comp=%s\n' \
		"${13}" "${14}" "${15}" "${16}" "${17}" "${18}" "${19}" "${20}" \
		"${21}" "${22}" "${23}" "${24}"
	printf '      name=%s%s%s%s%s%s%s%s%s%s%s%s%s%s%s%s\n' \
		"${25}" "${26}" "${27}" "${28}" "${29}" "${30}" "${31}" "${32}" \
		"${33}" "${34}" "${35}" "${36}" "${37}" "${38}" "${39}" "${40}"
done

echo
echo "=== 3. embedded version strings (the kernel says which build it is) ==="
for f in "$A" "$B"; do
	echo "  --- ${f##*/}"
	grep -a -o 'Linux version [0-9][^ ]*' "$f" 2>/dev/null | head -1 | sed 's/^/      /'
	grep -a -o '6\.18\.38[^ ]*' "$f" 2>/dev/null | head -2 | sed 's/^/      sub: /'
done

echo
echo "=== 4. where do the two first differ? ==="
n=$(wc -c <"$A"); m=$(wc -c <"$B")
lim=$(( n < m ? n : m ))
first=""
i=0
while [ "$i" -lt "$lim" ]; do
	cmp -s -n "$i" -i 0 "$A" "$B" 2>/dev/null || { break; }
	i=$(( i + 4096 ))
done
echo "  first differing 4 KiB block: offset $i (of $n / $m bytes)"
od -An -tx1 -j "$i" -N32 "$A" 2>/dev/null | sed 's/^/    A: /'
od -An -tx1 -j "$i" -N32 "$B" 2>/dev/null | sed 's/^/    B: /'

echo
echo "=== 5. mtimes ==="
ls -l --time-style=long-iso "$IMG" "$B" staging/trial/flash-dtb.bin staging/trial/flash-rootfs.jffs2 2>/dev/null | sed 's/^/  /'

echo
echo "=== 6. does the in-image kernel match anything else on disk? ==="
mine=$(sha256sum "$A" | cut -d' ' -f1)
echo "  in-image kernel sha256: $mine"
found=""
for f in staging/trial/* staging/rollback/* staging/ctl/* sk-d840n-openwrt/release/*; do
	[ -f "$f" ] || continue
	case "$f" in *.bin|*uImage|*Image) ;; *) continue ;; esac
	if [ "$(sha256sum "$f" | cut -d' ' -f1)" = "$mine" ]; then echo "    MATCHES $f"; found=1; fi
done
[ -n "$found" ] || echo "    no other staged file has these exact bytes"

echo
echo "=== 7. and the reverse: is flash-uImage a member of some other tar? ==="
mineB=$(sha256sum "$B" | cut -d' ' -f1)
echo "  staged flash-uImage sha256: $mineB"
found=""
for f in staging/trial/*.bin staging/trial/*.tgz; do
	[ -f "$f" ] || continue
	if tar tf "$f" >/dev/null 2>&1; then
		for m in $(tar tf "$f" 2>/dev/null | grep '/kernel$'); do
			rm -rf "$W/e"; mkdir -p "$W/e"; tar xf "$f" -C "$W/e" "$m" 2>/dev/null
			[ "$(sha256sum "$W/e/$m" | cut -d' ' -f1)" = "$mineB" ] && { echo "    $f -> $m MATCHES"; found=1; }
		done
	fi
done
[ -n "$found" ] || echo "    flash-uImage is not the kernel member of any staged tar"

rm -rf "$W"