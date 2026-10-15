#!/bin/sh
# Assemble the kexec loader for the SK-D840N trial kit on the PC.
#
# Why this exists: the trial boots a mainline kernel with kexec from the system
# that is running now, so nothing has to be written to flash first. But
#  - the running image has no kexec binary,
#  - it has no WAN, so nothing can be installed, and
#  - neither ImmortalWrt's nor upstream OpenWrt's package feed carries
#    kexec-tools for aarch64 at all (checked: zero hits in the 24.10
#    aarch64_generic base manifest, and `find feeds -iname 'kexec*'` after a full
#    feeds install is empty).
# Alpine's community repo builds kexec-tools against musl for aarch64, which is
# the same libc family OpenWrt uses, so its binary runs here unmodified - verified
# on the board: `kexec --version` -> "kexec-tools 2.0.31", rc=0.
#
# It needs two libraries the stock image does not ship (libz.so.1, liblzma.so.5),
# so they travel with it and are loaded via LD_LIBRARY_PATH from /tmp; nothing is
# installed into the real rootfs.
#
#   sh tools/getkexec.sh            # -> staging/trial/trial-kx.tgz
# set PROXY to override the download route.
set -eu

VER=v3.22
KEXEC_APK=kexec-tools-2.0.31-r0.apk
ZLIB_APK=zlib-1.3.2-r1.apk
XZ_APK=xz-libs-5.8.4-r0.apk
# sha256 of what was fetched and tested on 2026-10-10
SUMS='2c3fea334f6f6c60cc0874236289106118ceaeb38a0f615a346e6a13eace550f  kexec-tools-2.0.31-r0.apk
8a64b55223f29e24f934ccbc8167d535b913bf923dab5e14de7946a7c36091a7  xz-libs-5.8.4-r0.apk
2d274cb1b2a36d0e8347d802e6dea8648cab952ea5fae1989ab72e725842a2c3  zlib-1.3.2-r1.apk'

OUT=${1:-staging/trial}
DL=https://dl-cdn.alpinelinux.org/alpine/$VER
PROXY=${PROXY:--x http://127.0.0.1:10808}

mkdir -p "$OUT"
cd "$OUT"

fetch() { # repo-path  filename
	[ -s "$2" ] && { echo "have $2"; return; }
	echo "fetching $2"
	# dl-cdn does not resolve on this PC without the local proxy; fall back to a
	# direct attempt so the script still works elsewhere.
	curl -sS -f -m 300 $PROXY "$DL/$1/$2" -o "$2" \
		|| { echo "retrying without proxy"; curl -sS -f -m 300 "$DL/$1/$2" -o "$2"; }
}

fetch community/aarch64 "$KEXEC_APK"
fetch main/aarch64 "$ZLIB_APK"
fetch main/aarch64 "$XZ_APK"

printf '%s\n' "$SUMS" | sha256sum -c -

rm -rf x kexec lib trial-kx.tgz
mkdir -p x lib
# A modern .apk is one gzip'd tar with the files at their real paths (there is no
# inner data.tar.gz any more). Take only the real shared objects, not the
# libz.so.1 symlinks: git-bash cannot create symlinks on Windows by default, and
# the soname files are named explicitly below anyway.
tar xzf "$KEXEC_APK" -C x usr/sbin/kexec usr/sbin/vmcore-dmesg
tar xzf "$ZLIB_APK" -C x usr/lib/libz.so.1.3.2
tar xzf "$XZ_APK" -C x usr/lib/liblzma.so.5.8.4

cp x/usr/sbin/kexec kexec
cp x/usr/lib/libz.so.1.3.2 lib/libz.so.1
cp x/usr/lib/liblzma.so.5.8.4 lib/liblzma.so.5
rm -rf x
# Tarred from Windows the exec bit does not survive; trial-kexec.sh chmods it on
# the device, but set it here too so the archive is right when made on Linux.
chmod +x kexec 2>/dev/null || true
tar czf trial-kx.tgz kexec lib
ls -l kexec lib trial-kx.tgz
sha256sum trial-kx.tgz | tee trial-kx.sha256
