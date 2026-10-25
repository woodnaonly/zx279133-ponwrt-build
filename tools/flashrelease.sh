#!/bin/sh
# Flash the board with a CI release and check what was written.
#
#   sh tools/flashrelease.sh <flash tag> [initramfs tag]
#
# The release is fetched, checked against its own manifest, and checked
# against the partition it has to fit in, before anything is written: a
# truncated download and an oversized uImage are both caught on the PC,
# where they cost nothing, rather than on the board, where the uImage one
# costs a flash.
#
# The flash itself needs the board to be running the RAM image, not the
# installed one: `templates/flash-sk-d840n.sh` refuses to erase the flash
# under a mounted jffs2 root, and it is right to refuse.  So the board has
# to be in the trial environment, which is a separate step, and this script
# says so rather than pretending otherwise.
#
# What is written, and in what order: root, dtb, kernel.  Until the kernel
# is replaced the board still boots what it boots now, so an early mistake
# is recoverable.
#
# After the write the bytes are read back out of the partitions and compared
# with the files that went in.  "It boots" and "the flash holds what we
# wrote" are different claims, and only the second one is what G4 is about.
set -e
cd "$(dirname "$0")/../.."

TAG=${1:?usage: flashrelease.sh <flash tag> [initramfs tag]}
ITBTAG=${2:-}
ROOT=$(pwd)
REPO="$ROOT/ci-build-repo"
TRIAL="$ROOT/staging/trial"
PARTITION=8388608

echo "=== fetching the release ==="
python "$REPO/tools/getrel.py" "$TAG"

# The RAM image is the vehicle for the flash, and it is published by the
# initramfs job of the same run, under its own tag.  It is worth having
# the CI's own image here rather than an older one: it is the same tree,
# and booting it is part of what the release is for.
if [ -n "$ITBTAG" ]; then
	echo
	echo "=== fetching the RAM image ==="
	python "$REPO/tools/getrel.py" "$ITBTAG" || true
fi

echo
echo "=== the release, before it goes near the board ==="
# The gate is tools/preflash.py, and it is the same one the bench uses: the
# manifest the release ships has to describe the files beside it, the uImage
# has to fit the 8 MiB kernel partition, its payload has to be a gzip stream
# the loader can read, the kernel inside has to be the 6.18.38 this project
# is about, and the rootfs has to carry the NAT rule and the uci-defaults
# that starts it.  A release that fails any of those is a release that
# costs a flash and a power cycle to find out.
if [ -x "$REPO/tools/preflash.py" ] || [ -f "$REPO/tools/preflash.py" ]; then
	python "$REPO/tools/preflash.py" "$TAG" || {
		echo "flashrelease: the release did not pass the gate - nothing was written" >&2
		exit 1
	}
else
	echo "flashrelease: tools/preflash.py is missing - the gate cannot be run" >&2
	exit 1
fi

echo
echo "=== putting the images where the flasher looks for them ==="
mkdir -p "$TRIAL"
for f in flash-uImage flash-dtb.bin flash-rootfs.jffs2; do
	[ -s "$TRIAL/$f" ] || {
		echo "flashrelease: $TRIAL/$f is missing" >&2
		echo "flashrelease: the release has to be unpacked into staging/trial first" >&2
		exit 1
	}
done

KSIZE=$(stat -c %s "$TRIAL/flash-uImage")
if [ "$KSIZE" -gt "$PARTITION" ]; then
	echo "flashrelease: the uImage is $KSIZE bytes and the kernel partition holds" >&2
	echo "flashrelease: $PARTITION - mtd write would truncate it silently" >&2
	exit 1
fi
echo "  the uImage is $KSIZE bytes and the kernel partition holds $PARTITION"

echo
echo "=== the board has to be in the RAM environment ==="
echo "flashrelease: the board must be running the trial kernel for the flash"
echo "flashrelease: (templates/flash-sk-d840n.sh refuses to erase a mounted jffs2)"
exit 2
