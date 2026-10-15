#!/bin/sh
# Refuse to flash a set that predates this project's fixes.
#
# WHY THIS EXISTS - there are two layers, and the first version only had one.
#
# Layer 1, inside the rootfs image. The staged flash set in staging/trial is the
# 2026-10-12 build. Reading it with jefferson (jffs2 nodes are zlib-compressed, so
# grep -a on the image proves nothing) shows it carries:
#
#   lib/upgrade/platform.sh    2172 B   upstream's, not the 5118 B fixed overlay
#   etc/board.d/02_network     1080 B   an EARLY revision of this project's own overlay,
#                                         with `ucidef_set_interface_lan "eth0"` and no
#                                         192.168.9.1 at all
#
# Two concrete consequences, neither of which announces itself:
#   1. the board would come up on 192.168.1.1 (config_generate's default), and every tool
#      in this project waits on 192.168.9.x - so it would be reported dead while up;
#   2. UPGRADE_BACKUP is never assigned anywhere in this tree, so platform_do_upgrade
#      would drop the saved configuration on the next sysupgrade and still return 0.
#
# Layer 2, beside the image. The scripts that DO the flashing are not inside the rootfs;
# they are loose files that travel next to the images, and CI copies them verbatim:
#
#   cp templates/flash-sk-d840n.sh flash-sk-d840n.sh          (workflow line 359)
#   cp templates/trial-kexec.sh templates/flash-sk-d840n.sh trial/   (workflow line 554)
#
# No substitution, no templating - so byte equality with templates/ is the right
# invariant, not an approximation. The staged copies are both behind:
#
#   flash-sk-d840n.sh  3648 B vs 5378 B. The staged one has NO pre-flash sha256 check at
#       all, and its closing message still says `restore_set/ in this repo holds the
#       verified stock set for a full rollback` - a directory that holds ZTE firmware
#       image data with no jffs2 magic anywhere in its 241 MB, i.e. not a filesystem.
#   trial-kexec.sh  7409 B vs 7675 B. The staged one gunzips the 30 MB Image onto the
#       board FIRST and verifies sha256sums-trial afterwards.
#
# Checking only layer 1 would have printed "OK: this rootfs carries the current
# overlays" for a set that then programs kernel+dtb+root with no integrity check on a
# unit that has no serial console. That is the failure mode this tool exists to stop,
# so it checks both, and says which layer each verdict came from.
#
# The third thing checked is the manifest. `sha256sum -c` only ever verifies the files
# named in the manifest, so a manifest that omits a payload file reports success while
# leaving that file unverified. The staged set ships 4 entries where the workflow hashes
# 5: flash-sk-d840n.sh is missing from it. And release 21's flash-set.tgz does not
# contain the manifest at all, which is the whole reason the workflow started copying
# it into the tarball - the flasher runs on the board and can only check integrity
# against a manifest that arrived with the images.
#
# Usage:
#   sh tools/check-imagelineage.sh [rootfs-or-dir] [overlay-dir]
#   JEFFERSON=/path/to/jefferson sh tools/check-imagelineage.sh
#   ALLOW_UNVERIFIED=1 sh tools/check-imagelineage.sh     # refuse silently? no - warn loudly
set -u
cd "$(dirname "$0")/.." 2>/dev/null || cd /mnt/c/ugit/SK-D840N4
ROOT=${1:-staging/trial/flash-rootfs.jffs2}
OVL=${2:-ci-build-repo/overlays/target/linux/zte/zx279133/base-files}
TEMPLATES=${TEMPLATES:-ci-build-repo/templates}
JEFFERSON=${JEFFERSON:-jefferson}

fail=0
note() { printf '  %s\n' "$1"; }
refuse() { printf 'REFUSE %s\n' "$1"; fail=1; }

printf '=== image lineage: %s\n' "$ROOT"
[ -e "$ROOT" ] || refuse "$ROOT does not exist"
[ -d "$OVL" ] || refuse "overlay dir $OVL does not exist"

WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT

if [ -d "$ROOT" ]; then
	SRC="$ROOT (already extracted)"
	IMGROOT="$ROOT"
else
	if ! command -v "$JEFFERSON" >/dev/null 2>&1; then
		if [ "${ALLOW_UNVERIFIED:-0}" = 1 ]; then
			note "WARNING: jefferson not found and ALLOW_UNVERIFIED=1 - the image was NOT verified"
			note "         pass a directory instead, or set JEFFERSON=/path/to/jefferson"
			exit 0
		fi
		refuse "jefferson not found, so the rootfs cannot be read. jffs2 nodes are"
		refuse "zlib-compressed; grep -a on the image would prove nothing. Install it,"
		refuse "set JEFFERSON=, or pass an already-extracted directory."
		exit 1
	fi
	"$JEFFERSON" -f -d "$WORK/img" "$ROOT" >"$WORK/jefferson.log" 2>&1 \
		|| { refuse "jefferson could not read $ROOT"; tail -15 "$WORK/jefferson.log"; exit 1; }
	IMGROOT="$WORK/img"
	SRC="$ROOT ($(find "$IMGROOT" -type f | wc -l) entries)"
fi
note "source: $SRC"

# ---------------------------------------------------------------- layer 1: in the image
check_one() { # relpath, human name, consequence when stale
	rel=$1; what=$2; harm=$3
	a="$IMGROOT/$rel"
	b="$OVL/$rel"
	if [ ! -f "$a" ]; then
		refuse "in-image/$what: $rel is not in the image at all"
		return
	fi
	if [ ! -f "$b" ]; then
		# A missing counterpart is NOT a pass. The first version of this tool printed
		# "no overlay counterpart - skipped" and then said OK, because the overlay root
		# defaulted one directory too high - a gate that compared nothing and passed.
		refuse "in-image/$what: no overlay counterpart at $b - cannot verify, and skipping is not verifying"
		return
	fi
	as=$(wc -c <"$a"); bs=$(wc -c <"$b")
	am=$(md5sum "$a" | cut -d' ' -f1); bm=$(md5sum "$b" | cut -d' ' -f1)
	if cmp -s "$a" "$b"; then
		note "ok   in-image/$what: $rel matches the overlay ($as B)"
	elif [ "$as" = "$bs" ] && [ "$am" = "$bm" ]; then
		note "ok   in-image/$what: $rel matches the overlay"
	else
		refuse "in-image/$what: $rel in the image is $as B (md5 $am); the overlay is $bs B (md5 $bm)"
		note "     -> flashing this would mean: $harm"
		note "     -> the image has to be rebuilt from the current tree first"
	fi
}

check_one \
	lib/upgrade/platform.sh \
	'upgrade path' \
	'the saved configuration is dropped on every sysupgrade while platform_do_upgrade still returns 0'

check_one \
	etc/board.d/02_network \
	'network defaults' \
	'the board comes up on 192.168.1.1 instead of 192.168.9.1, so every tool in this project reports it dead'

# ------------------------------------------------- layer 2: beside the image, and the
# ---------------------------------------------------------------------- integrity manifest
#
# The set directory is NOT `dirname $ROOT` in general. An earlier version did exactly
# that, and when ROOT was an already-extracted tree it walked up one level too far and
# inspected the parent - printing "not part of this set" for files it never looked at.
# So: derive it only when ROOT is an image file, and let it be named explicitly.
SETDIR=${SETDIR:-}
if [ -z "$SETDIR" ]; then
	if [ -f "$ROOT" ]; then SETDIR=$(dirname "$ROOT"); fi
fi
layer2=0

echo
if [ -z "$SETDIR" ]; then
	echo "=== layer 2: NOT APPLICABLE"
	note "$ROOT is an already-extracted tree, not an image file, so there is no set"
	note "beside it to inspect. Pass SETDIR=<dir> to check the scripts and manifest that"
	note "travel with an image. This is not a pass - the set-level check did not run."
elif [ ! -d "$SETDIR" ]; then
	refuse "beside-image: set dir $SETDIR does not exist"
else
	layer2=1
	echo "=== layer 2: the set that travels with the image, in $SETDIR"
	if [ ! -d "$TEMPLATES" ]; then
	# Same rule as the missing overlay counterpart above: not being able to check is not
	# the same as there being nothing wrong.
	refuse "beside-image: templates dir $TEMPLATES does not exist - cannot verify the scripts beside the images"
	else
	check_shipped() { # name, human name, consequence when stale
		n=$1; what=$2; harm=$3
		a="$SETDIR/$n"; b="$TEMPLATES/$n"
		if [ ! -f "$a" ]; then
			note "ok   beside-image/$what: $n is not part of this set, so it cannot be stale"
			return
		fi
		if cmp -s "$a" "$b"; then
			note "ok   beside-image/$what: $n matches templates/ ($(wc -c <"$a") B)"
			return
		fi
		refuse "beside-image/$what: the $n beside the images is $(wc -c <"$a") B (md5 $(md5sum "$a" | cut -d' ' -f1)); templates/ has $(wc -c <"$b") B (md5 $(md5sum "$b" | cut -d' ' -f1))"
		note "     -> running this would mean: $harm"
		note "     -> CI copies templates/ verbatim, so take the script from the new release"
	}

	check_shipped \
		flash-sk-d840n.sh \
		'flasher' \
		'kernel, dtb and root get programmed with no integrity check, and the closing message sends you to restore_set/ for a rollback that cannot happen - it holds ZTE firmware image data, not a filesystem. On a unit with no serial console that is a brick found only when the box does not come back'

	check_shipped \
		trial-kexec.sh \
		'RAM trial launcher' \
		'the 30 MB Image is gunzipped onto the board before sha256sums-trial is consulted, so a truncated or corrupted payload is materialised before anything can reject it'

	# The manifest only ever covers what it names. A payload file it omits is unverified
	# while `sha256sum -c` still reports success, which is worse than having no manifest
	# because the operator sees a green check.
	MAN="$SETDIR/sha256sums-release"
	if [ -f "$MAN" ]; then
		note "-- integrity manifest beside the images:"
		# Normalise once. A manifest that came through a CRLF checkout would otherwise fail
		# every name below and be reported as covering nothing - a gate that cries wolf.
		tr -d '\r' <"$MAN" >"$WORK/manifest.norm"
		missing=0
		for n in flash-uImage flash-dtb.bin flash-rootfs.jffs2 flash-sysupgrade.bin \
		         flash-sk-d840n.sh; do
			[ -f "$SETDIR/$n" ] || continue
			if grep -q "[[:space:]]$n\$" "$WORK/manifest.norm"; then
				note "ok   manifest/$n: covered"
			else
				refuse "manifest/$n: present in the set but NOT listed in sha256sums-release - sha256sum -c reports success while leaving it unverified"
				missing=1
			fi
		done
		[ "$missing" -eq 0 ] && note "ok   manifest: every payload file beside it is covered"
	else
		note "-- no sha256sums-release beside the images: integrity is UNVERIFIED for the whole set"
		note "   (the manifest must travel INSIDE the tarball - the flasher runs on the board)"
	fi
	fi
fi

echo
if [ "$fail" -ne 0 ]; then
	cat <<'EOF'
  This set is older than the tree. Do NOT flash it from staging/ as-is.
  Push the ci-build-repo changes, let CI rebuild, take the new flash-set, and
  re-run this check. ALLOW_UNVERIFIED=1 exists but only to record that you skipped it.
EOF
	exit 1
fi
if [ "$layer2" -eq 1 ]; then
	echo "OK: this rootfs carries the current overlays, and the scripts beside it are current."
else
	echo "OK: this rootfs carries the current overlays."
	echo "    (layer 2 did NOT run - the set beside the image was not inspected)"
fi
exit 0
