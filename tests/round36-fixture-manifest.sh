#!/bin/sh
# Finish the stale fixture: the manifest.
#
# release 21's own `sha256sums-release` would not download on any of the three routes, but
# the property the gate's manifest layer tests is a SHAPE, not a specific digest: "the
# manifest lists the payloads but not flash-sk-d840n.sh". That is reconstructed here from
# the fixture's own bytes, so it stays true if the rootfs is ever re-fetched.
#
# This is a reconstruction and is labelled as one. Nothing in the suite asserts a digest
# from it - the suite asserts that the tool refuses a set whose manifest omits a payload.
set -eu
F=/mnt/c/ugit/SK-D840N4/staging/diag-20261014/fixtures/stale-set
[ -s "$F/flash-rootfs.jffs2" ] || { echo "fixture rootfs missing"; exit 1; }
rm -f "$F/flash-rootfs.jffs2.partial"
( cd "$F" && sha256sum flash-rootfs.jffs2 > sha256sums-release )
echo "=== the reconstructed manifest ==="
cat "$F/sha256sums-release" | sed 's/^/  /'
echo "  entries: $(wc -l <"$F/sha256sums-release")"
echo
echo "  must NOT list flash-sk-d840n.sh - that is the whole point:"
grep -q 'flash-sk-d840n.sh' "$F/sha256sums-release" \
	&& { echo "  IT DOES - wrong fixture"; exit 1; } \
	|| echo "    confirmed absent"
echo
echo "=== the fixture as a whole ==="
ls -l "$F" | sed 's/^/  /'
echo
echo "=== now: does the gate refuse it? (the point of the fixture) ==="
cd /mnt/c/ugit/SK-D840N4
# Absolute: the tool cds to its own repository before reading $2, so the relative
# `ci-build-repo/overlays/...` this used to pass resolved to
# ci-build-repo/ci-build-repo/overlays/... and the gate's first line became
# "REFUSE overlay dir ... does not exist" - a refusal whose stated reason was false, printed
# above the three real findings. Same defect class as e8f6036, reached from the caller.
REPO=/mnt/c/ugit/SK-D840N4/ci-build-repo
if sh "$REPO/tools/check-imagelineage.sh" "$F/flash-rootfs.jffs2" \
     "$REPO/overlays/target/linux/zte/zx279133/base-files" >/tmp/fixcheck.out 2>&1; then
	echo "  FAIL - the gate ACCEPTED the fixture, so it is not a negative control"
	tail -20 /tmp/fixcheck.out | sed 's/^/    /'
	exit 1
else
	echo "  ok - the gate refuses it"
	grep -E '^(  )?REFUSE' /tmp/fixcheck.out | sed 's/^/    /'
fi
