#!/bin/sh
# Boot the mainline SK-D840N build from the system that is running now, without
# writing a single byte of flash.
#
# Reversibility comes from not touching flash at all: whatever this does to the
# running kernel, a power cycle puts the current system back, because the kernel,
# dtb and rootfs partitions still hold what booted them.  No serial console and no
# watchdog trick needed.
#
# Usage, from the PC (a copy over ssh is the only reliable path on this unit: the
# image has no tftp client, and plain `scp` fails because /usr/libexec/sftp-server
# is not in it - `scp -O` or `cat | ssh "cat > f"` both work, 30 MB in ~1.5 s):
#       scp -O -i ~/.ssh/id_onu trial/* root@192.168.1.1:/tmp/trial/
#       ssh  root@192.168.1.1 sh /tmp/trial/trial-kexec.sh
# Everything the script needs may therefore already be sitting in $D, which is the
# mode it prefers.  If a file is missing and PC= is set it fetches it over HTTP
# with uclient-fetch, then tries tftp (kept for images that have it; this one does
# not, so without PC= pre-staged files are the only way in).
#
#       sh trial-kexec.sh                      # files pre-staged in /tmp/trial
#       PC=<ip> sh trial-kexec.sh              # fetch what is missing over HTTP
#       PC=<ip> TFTP=1 sh trial-kexec.sh       # fetch over tftp instead
#       KEXEC_ARGS=--load sh trial-kexec.sh    # load only, then run --exec by hand
# set -eu

D=${D:-/tmp/trial}
PC=${PC:-}
PORT=${PORT:-8080}
TPORT=${TPORT:-6969}
TFTP=${TFTP:-0}
KX=${KX:-}
CMDLINE=${CMDLINE:-'root=/dev/ram0 rw console=ttyAMA0,115200'}

say() { echo "trial: $*"; }
die() { echo "trial: $*" >&2; exit 1; }

mkdir -p "$D"; cd "$D" || exit 1

get_http() { # name
	say "fetching $1 over http from $PC:$PORT"
	uclient-fetch -q -O "$1.tmp" "http://$PC:$PORT/$1" || return 1
	[ -s "$1.tmp" ] || return 1
	mv "$1.tmp" "$1"
}

get_tftp() { # name
	say "fetching $1 over tftp from $PC:$TPORT"
	busybox tftp -g -r "$1" -l "$1.tmp" -b 1468 "$PC" "$TPORT" 2>/dev/null || return 1
	[ -s "$1.tmp" ] || return 1
	mv "$1.tmp" "$1"
}

# name - fetch one file into $D, preferring what is already there
fetch() {
	[ -s "$1" ] && { say "already on the box: $1 ($(wc -c < "$1") bytes)"; return 0; }
	[ -n "$PC" ] || die "$1 is not in $D and PC= is not set - copy the trial files over or give me a server address"
	if [ "$TFTP" = 1 ]; then
		get_tftp "$1" || die "tftp get $1 failed"
	else
		get_http "$1" || get_tftp "$1" || die "cannot fetch $1 from $PC (http:$PORT, tftp:$TPORT)"
	fi
	say "got $1 ($(wc -c < "$1") bytes)"
}

# die exits, and a function in an `if` condition is not a subshell in ash, so an
# optional fetch has to fork or a missing file would end the whole script.
try_get() { # name - same, but a miss is not fatal
	( fetch "$1" ) 2>/dev/null
}

# The release ships either trial-set.tgz (everything in one file) or loose files,
# so take the tarball whenever it is available and be content without it.
if [ -s trial-set.tgz ] || [ -n "$PC" ]; then
	if try_get trial-set.tgz; then
		tar xzf trial-set.tgz && say "unpacked trial-set.tgz"
	fi
fi

# kexec plus the shared objects it was linked against. This is NOT part of the CI
# release: no OpenWrt feed carries kexec-tools for aarch64, so the PC builds it
# from Alpine's musl packages with tools/getkexec.sh (verified to run on this
# board, as /tmp/kx/kexec here). The box has no WAN, so a missing .so could not be
# fixed after the fact - hence the libs travel with the binary.
if [ -z "$KX" ]; then
	if [ -s kexec ]; then
		KX=$D/kexec
	else
		if try_get trial-kx.tgz; then
			tar xzf trial-kx.tgz
			KX=$D/kexec
		else
			die "no kexec to boot with - copy trial-kx.tgz (built by tools/getkexec.sh on the PC) into $D or set KX=<path>"
		fi
	fi
fi
[ -x "$KX" ] || chmod +x "$KX"
[ -d lib ] && export LD_LIBRARY_PATH=$D/lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}

fetch trial-dtb.bin
fetch trial-Image.gz
gunzip -c trial-Image.gz > Image || die "gunzip failed"

# A half-copied payload boots into a corrupt kernel, and over ssh a copy can end
# early without anything complaining, so check the shipped hashes when both sides
# have them.
if [ -s sha256sums-trial ] && command -v sha256sum >/dev/null 2>&1; then
	say "checking sha256sums-trial"
	sha256sum -c sha256sums-trial || die "checksum mismatch - re-copy the trial files"
fi

say "kexec version: $("$KX" --version 2>&1 | head -1)"
say "loading Image ($(wc -c < Image) bytes) + dtb ($(wc -c < trial-dtb.bin) bytes)"
say "command line: $CMDLINE"

# arm64 loads a raw Image; the initramfs is already inside it, so no --initrd.
"$KX" ${KEXEC_ARGS:---load} Image --dtb=trial-dtb.bin --command-line="$CMDLINE" \
	|| die "kexec --load failed (check CONFIG_KEXEC on the running kernel)"

if [ "${KEXEC_ARGS:-}" = "--load" ]; then
	say "loaded, not executed. /proc/cmdline of the NEW kernel will be:"
	say "  $CMDLINE"
	say "Go on with:  $KX --exec   (or just reboot to stay where you are)"
	exit 0
fi

say "loaded. /proc/cmdline of the NEW kernel will be:"
say "  $CMDLINE"
say "Going to leave the running kernel in 5 s - Ctrl-C now to stop."
say "The only port mainline can drive is the uplink RJ45 (the vendor's wan/eth0),"
say "so the cable has to be in that one to see anything. If the new system stays"
say "silent: power-cycle the board; nothing was written."
sleep 5
"$KX" --exec || die "kexec --exec failed"
# not reached
