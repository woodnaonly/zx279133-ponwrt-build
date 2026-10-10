#!/bin/sh
# Boot the mainline SK-D840N build from the system that is running now, without
# writing a single byte of flash.
#
# Reversibility comes from not touching flash at all: whatever this does to the
# running kernel, a power cycle puts the current system back, because the kernel,
# dtb and rootfs partitions still hold what booted them.  No serial console and no
# watchdog trick needed.
#
# On the PC, serve the release's trial/ directory over TFTP (same helper used for
# the plan-A flash: it listens on 6969 and survives client resets).
# On the device:
#       PC=<tftp-server-ip> sh trial-kexec.sh
#       PC=<ip> KEXEC_ARGS='--load' sh trial-kexec.sh   # load only, then run
# set -eu

D=/tmp/trial
PC=${PC:?set PC to the tftp server address}
PORT=${PORT:-6969}
CMDLINE=${CMDLINE:-'root=/dev/ram0 rw console=ttyAMA0,115200'}

say() { echo "trial: $*"; }
die() { echo "trial: $*" >&2; exit 1; }

fetch() { # name
	rm -f "$D/$1"
	busybox tftp -g -r "$1" -l "$D/$1" -b 1468 "$PC" "$PORT" || die "tftp get $1 failed"
	[ -s "$D/$1" ] || die "tftp got an empty $1"
	say "got $1 ($(wc -c < "$D/$1") bytes)"
}

try_fetch() { # name - optional file
	rm -f "$D/$1"
	busybox tftp -g -r "$1" -l "$D/$1" -b 1468 "$PC" "$PORT" 2>/dev/null || return 1
	[ -s "$D/$1" ] || return 1
	say "got $1 ($(wc -c < "$D/$1") bytes)"
}

mkdir -p "$D"; cd "$D" || exit 1

# The release ships either trial-set.tgz (everything in one file) or the loose
# trial/ directory. Take whichever is being served.
if try_fetch trial-set.tgz; then tar xzf trial-set.tgz; fi

# kexec plus the shared objects it was linked against, as one tarball. This is NOT
# part of the CI release: no OpenWrt feed carries kexec-tools for aarch64, so the
# PC builds it from Alpine's musl packages with tools/getkexec.sh (it was verified
# to run on this board). The box has no WAN, so a missing .so could not be fixed
# after the fact - hence libs travel with the binary.
if ! try_fetch trial-kx.tgz; then
	die "trial-kx.tgz is not being served - run tools/getkexec.sh on the PC and serve its staging/trial directory"
fi
tar xzf trial-kx.tgz
KX=$D/kexec
[ -x "$KX" ] || chmod +x "$KX"
export LD_LIBRARY_PATH=$D/lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}

need() { # name - fetch only if the tarball did not already provide it
	[ -s "$D/$1" ] || fetch "$1"
}

need trial-dtb.bin
need trial-Image.gz
gunzip -c trial-Image.gz > Image || die "gunzip failed"

say "kexec version: $("$KX" --version 2>&1 | head -1)"
say "loading Image ($(wc -c < Image) bytes) + dtb ($(wc -c < trial-dtb.bin) bytes)"
say "command line: $CMDLINE"

# arm64 loads a raw Image; the initramfs is already inside it, so no --initrd.
"$KX" --load Image --dtb=trial-dtb.bin --command-line="$CMDLINE" \
	|| die "kexec --load failed (check CONFIG_KEXEC on the running kernel)"

say "loaded. /proc/cmdline of the NEW kernel will be:"
say "  $CMDLINE"
say "Going to leave the running kernel in 5 s - Ctrl-C now to stop."
say "If the new system has no working LAN: power-cycle the board; nothing was written."
sleep 5
"$KX" --exec || die "kexec --exec failed"
# not reached
