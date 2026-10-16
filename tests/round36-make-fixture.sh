#!/bin/sh
# Fetch release 21's published artifacts to use as the pinned STALE fixture.
#
# Direct github.com downloads time out here (curl exit 28 after 300 s on a 4 MB file), which
# is exactly why tools/gettrial.sh carries a proxy and mirror fallback. Reuse that chain
# rather than rediscovering it.
set -u
R=woodnaonly/zx279133-ponwrt-build
F=$(cd "$(dirname "$0")" && pwd)/fixtures/stale-set
mkdir -p "$F"

# The staleness comparison at the end needs this repository's templates/. Where that is
# depends on where this script is being run from - ../templates from the delivered
# ci-build-repo/tests/, but ../../ci-build-repo/templates from the working copy at
# staging/diag-20261014/ - so it is located by walking up until the file is found rather
# than by counting levels. An earlier version used "$(dirname "$0")/../..", which resolves
# to the workspace root from tests/ and has no templates/ at all: the comparison silently
# printed two empty sizes instead of failing.
repo_root() {
	_d=$(cd "$(dirname "$0")" && pwd)
	while [ "$_d" != "/" ]; do
		[ -f "$_d/templates/flash-sk-d840n.sh" ] && { printf '%s\n' "$_d"; return 0; }
		_d=$(dirname "$_d")
	done
	echo "cannot find a repository root above $0 (no templates/flash-sk-d840n.sh)" >&2
	return 1
}
REPO=$(repo_root) || exit 1

# Stale .partial files are what fetch() leaves behind when an attempt dies, and it renames
# the previous good copy aside before trying again - so a few runs in leaves several
# megabytes of superseded copies next to the real ones. They are worse than none.
for stale in "$F"/*.partial; do
	[ -e "$stale" ] || continue
	mv "$stale" /tmp/"$(basename "$stale").$$" 2>/dev/null || true
done

fetch() { # dest url-path
	d=$1; p=$2
	# a partial file from a timed-out attempt is worse than none
	if [ -f "$F/$d" ]; then
		mv "$F/$d" "$F/$d.partial" 2>/dev/null || true
	fi
	url="https://github.com/$R/releases/download/$p/$d"
	echo "  $d"
	if curl -sfL --max-time 900 "$url" -o "$F/$d"; then
		echo "    direct ok ($(wc -c <"$F/$d") B)"
		return 0
	fi
	echo "    direct failed, trying the proxy"
	if curl -sfL --max-time 900 "https://ghproxy.net/$url" -o "$F/$d"; then
		echo "    proxy ok ($(wc -c <"$F/$d") B)"
		return 0
	fi
	echo "    proxy failed, trying the ghfast.top mirror"
	if curl -sfL --max-time 900 "https://ghfast.top/$url" -o "$F/$d"; then
		echo "    mirror ok ($(wc -c <"$F/$d") B)"
		return 0
	fi
	echo "    ALL THREE FAILED for $d"
	return 1
}

rc=0
fetch flash-rootfs.jffs2 skd840n_flash_21_38243906799 || rc=1
# The manifest is the pin, and it is the one file here that is tracked in this repository.
# Re-downloading it is redundant, and it is also the one file that cannot be fetched at
# all from this machine: measured 2026-10-16, all three routes fail for
# sha256sums-release while the 4 MiB rootfs goes through the proxy in seconds. Worse,
# fetch() renames whatever is already there to .partial before trying, so asking for the
# tracked pin throws away the only copy of the reference the fixture is checked against -
# and that is exactly what happened the first time this was run.
if [ -s "$F/sha256sums-release" ]; then
	echo "  sha256sums-release  already present ($(wc -c <"$F/sha256sums-release" | tr -d ' ') B, tracked) - not re-fetched"
else
	fetch sha256sums-release skd840n_flash_21_38243906799 || rc=1
fi
fetch flash-sk-d840n.sh   skd840n_initramfs_21_38243906799 || rc=1
fetch trial-kexec.sh      skd840n_initramfs_21_38243906799 || rc=1

echo
echo "=== the fixture ==="
ls -l "$F" | sed 's/^/  /'
[ "$rc" = 0 ] || { echo; echo "  INCOMPLETE - do not point a test at a half-built fixture"; exit 1; }

echo
echo "=== does it match the pin? (this is the whole reason those 85 bytes are tracked) ==="
# Without this, "the download finished" and "the download is the right file" are the same
# claim, and a proxy that returns a login page with a 200 would pass as a fixture.
if (cd "$F" && sha256sum -c sha256sums-release); then
	echo "  ok: the fixture is the release it claims to be"
else
	echo "  sha256 MISMATCH - do not point a test at this"
	rc=1
fi
[ "$rc" = 0 ] || { echo; echo "  INCOMPLETE - do not point a test at a half-built fixture"; exit 1; }

echo
echo "=== it must actually be stale, or it is not a negative control ==="
TMPL=$REPO/templates
printf '  %-22s fixture=%-7s current=%-7s\n' \
	flash-sk-d840n.sh "$(wc -c <"$F/flash-sk-d840n.sh")" "$(wc -c <"$TMPL/flash-sk-d840n.sh")"
printf '  %-22s fixture=%-7s current=%-7s\n' \
	trial-kexec.sh "$(wc -c <"$F/trial-kexec.sh")" "$(wc -c <"$TMPL/trial-kexec.sh")"
echo "  manifest entries       : $(wc -l <"$F/sha256sums-release") (current release: 5)"
grep -q 'flash-sk-d840n.sh' "$F/sha256sums-release" \
	&& echo "  manifest covers flash-sk-d840n.sh - NOT the old shape, wrong fixture" \
	|| echo "  manifest does NOT cover flash-sk-d840n.sh - correct, that is the old gap"
echo "  rootfs md5             : $(md5sum "$F/flash-rootfs.jffs2" | cut -c1-16)"

# The .partial copies fetch() leaves behind are superseded now that the pin has been
# checked. Left in place they double the apparent size of the directory and invite someone
# to wonder which file is the real one.
for stale in "$F"/*.partial; do
	[ -e "$stale" ] || continue
	mv "$stale" /tmp/"$(basename "$stale").$$" 2>/dev/null || true
done
echo
echo "  fixture now:"
ls -l "$F" | sed 's/^/    /'
