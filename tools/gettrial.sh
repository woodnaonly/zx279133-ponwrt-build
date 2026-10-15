#!/bin/sh
# Fetch the SK-D840N trial kit from the newest CI release that has it, into
# staging/trial, and verify it before anything is allowed to use it.
#
#	sh tools/gettrial.sh              # initramfs mode (the kexec trial kit)
#	sh tools/gettrial.sh flash        # also grab flash-set.tgz from the flash build
#
# Releases are public, so no token is needed - and none is read, which matters
# because this file is in the repo.
set -eu

REPO=woodnaonly/zx279133-ponwrt-build
MODE=${1:-initramfs}
# Pin a run when the newest release is not the one that may be flashed. Two builds were
# live at once on 2026-10-12 and the older one's dtb is unflashable (root at mtdblock4),
# so "newest" is not a safe rule once a fix is in flight.
WANT_RUN=${2:-}
D=staging/trial
case "$MODE" in
initramfs) FILE=trial-set.tgz ;;
flash) FILE=flash-set.tgz ;;
*) echo "mode is initramfs or flash" >&2; exit 1 ;;
esac

mkdir -p "$D"
cd "$D"

# python3 on this PC is a stub, so ask for `python`.
#
# Do not trust the API's order: on 2026-10-10 it listed three releases from 02:37
# *above* the one from 08:50, so taking the first match fetched a kit whose kernel
# still had the PHY description that had just been fixed - and a stale artifact
# downloaded over a network that verifies sha256 of the wrong bytes looks like a
# clean result. Rank by the run id in the tag, which is monotonic. A packaging
# failure can still publish a tag with no assets, so keep walking.
set -- $(curl -sf "https://api.github.com/repos/$REPO/releases?per_page=40" |
	python -c "
import json, sys
want = 'skd840n_$MODE'
want_run = '$WANT_RUN'
found = []
for r in json.load(sys.stdin):
    tag = r.get('tag_name', '')
    if not tag.startswith(want):
        continue
    if want_run and not tag.endswith('_' + want_run):
        continue
    assets = {a['name']: a['browser_download_url'] for a in r.get('assets', [])}
    if '$FILE' in assets:
        found.append((tag, assets['$FILE'], assets.get('sha256sums', '-')))
def runid(tag):
    tail = tag.rsplit('_', 1)[-1]
    return int(tail) if tail.isdigit() else -1
for tag, url, man in sorted(found, key=lambda t: runid(t[0]), reverse=True):
    print(tag, url, man)
    break
")
[ $# -ge 2 ] || { echo "no published release carrying $FILE for mode=$MODE yet" >&2; exit 1; }
rel=$1
url=$2
man=${3:--}

echo "downloading $url"
# Measured from this PC against these assets: a direct connection dies with curl
# rc=56 (reset) after ~19 s, ghfast.top just times out, and the local SOCKS/HTTP
# proxy did the full 12,039,512-byte trial-set.tgz in under 45 s (~270 KB/s) - so
# the proxy is the second attempt, not a last resort.
dl() { # url dest
	rm -f "$2"
	curl -sfL -m 60 -o "$2" "$1" && [ -s "$2" ] && return 0
	echo "direct failed, trying the proxy"
	curl -sfL -m 900 -x http://127.0.0.1:10808 -o "$2" "$1" && [ -s "$2" ] && return 0
	echo "proxy failed, trying the ghfast.top mirror"
	curl -sfL -m 900 -o "$2" "https://ghfast.top/$1" && [ -s "$2" ]
}
if [ -s "$FILE" ] && grep -q "^$rel  " "$FILE.source" 2>/dev/null; then
	echo "  reusing the $FILE already in staging/trial (from release $rel)"
else
	dl "$url" "$FILE"
fi
# The release's own manifest is a separate asset, and it is the one that means something:
# CI hashed the very bytes it uploaded. Measured on _17/_18, the `sha256sums-trial` inside
# flash-set.tgz lists only trial-* members and two of them do not even match (that manifest
# is written by the initramfs job but packaged by the flash job), so the flash triple would
# arrive completely unverified without this file.
#
# BOTH releases publish an asset called `sha256sums-release`, and they are not the same
# file: the flash release covers its 5 payloads, the initramfs release covers its 1 .itb.
# They both land in this one directory, so writing both to the same local name made the
# second fetch silently replace the first - and tools/check-imagelineage.sh, which checks
# the flash set against whatever `sha256sums-release` happens to be, then refused all five
# flash files with "NOT listed" on a set that was the newest one built. The refusal is safe
# but its reason is a lie, and after fetching both halves it could never pass at all.
#
# So: the flash manifest keeps the plain name that check-imagelineage reads, and the
# initramfs one is stored under a mode-qualified name instead of overwriting it.
MANLOCAL=sha256sums-release
[ "$MODE" = flash ] || MANLOCAL=sha256sums-initramfs-release
[ "$man" = "-" ] || dl "$man" "$MANLOCAL"
echo "$rel  $url" > "$FILE.source"
ls -l "$FILE"

# Verify in a scratch directory, and only move files into place once every check has passed.
# The previous order - extract, then checksum - left a failed pull as a *mix* of two releases
# in staging/trial, which is exactly how an unflashable dtb ends up in a payload.
rm -rf v
mkdir -p v
tar xzf "$FILE" -C v
if [ ! -f "$MANLOCAL" ]; then
	echo "WARNING: no release manifest was fetched - nothing below is verified against CI"
fi
fail=0
if [ -f "$MANLOCAL" ]; then
	for f in $(cd v && ls -1); do
		want=$(awk -v F="$f" '$2 == F {print $1}' "$MANLOCAL")
		[ -n "$want" ] || continue
		got=$(sha256sum < "v/$f" | cut -d' ' -f1)
		if [ "$got" = "$want" ]; then
			printf '  OK       %-28s %s\n' "$f" "$(echo "$got" | cut -c1-16)"
		else
			printf '  MISMATCH %-28s got=%s want=%s\n' "$f" "$(echo "$got" | cut -c1-16)" "$(echo "$want" | cut -c1-16)"
			fail=1
		fi
	done
	# Only flash mode can require the flash images. In initramfs mode the release has no
	# flash-uImage or flash-dtb.bin AT ALL, so demanding they be covered by the manifest
	# made `gettrial.sh initramfs` refuse every release unconditionally - on 2026-10-15 it
	# said "NOT COVERED: flash-uImage is in no manifest - refusing" for a run whose
	# initramfs job had published correctly, and left staging/trial with the previous
	# trial-kexec.sh, which is what check-imagelineage then refuses to flash. The comment
	# on the else branch below already said this split was intended; it just was never
	# guarded by mode.
	[ "$MODE" = flash ] && {
		for must in flash-uImage flash-dtb.bin; do
			grep -qE "(^| )$must\$" "$MANLOCAL" ||
				{ echo "  NOT COVERED: $must is in no manifest - refusing"; fail=1; }
		done
	}
else
	# initramfs mode: fall back to the manifest inside the tarball
	for m in v/sha256sums-trial v/sha256sums; do
		[ -f "$m" ] || continue
		echo "verifying $(basename "$m") from release $rel"
		(cd v && sha256sum -c "$(basename "$m")") || fail=1
	done
fi
[ -f v/sha256sums-trial ] && {
	echo "  (informational) in-tarball sha256sums-trial:"
	(cd v && sha256sum -c sha256sums-trial 2>&1 | sed 's/^/    /')
	[ "$MODE" = flash ] && echo "    ^ written by the initramfs job; flash mode trusts the release manifest"
}
[ "$fail" = 0 ] || { echo "verification failed for release $rel - staging/trial left untouched" >&2; exit 1; }

mv -f v/* . 2>/dev/null || true
rmdir v
ls -l trial-Image.gz trial-dtb.bin flash-uImage flash-dtb.bin 2>/dev/null || true
echo "next: $( [ "$MODE" = flash ] && echo 'python tools/dtbparts.py staging/trial/flash-dtb.bin root=0xf00000,0xf000000 && sh tools/mkgate.sh' || echo 'sh tools/pushtrial.sh' )"
