#!/bin/sh
# 本地排演套件（local rehearsal suite）：依赖 staging/ 下的真实构建产物，
# 在只 clone 本仓库的环境里跑不起来，也**没有**接进 CI。
# 为什么、怎么跑、缺的那个夹具从哪来：同目录 README.md。
# Decisive: does the image's /lib/upgrade/platform.sh come from our overlay, or is it the
# generic one that package/base-files installs?
#
# The marker is a function name that appears nowhere upstream. If the built image carries
# `skd840n_has_part`, the overlay path is delivered; if it does not, every platform.sh
# fix is dead weight.
#
# Note the image is the 2026-10-12 build, so it PREDATES today's two fixes and must NOT be
# asked for skd840n_tar_z / skd840n_conf_tar. Those absences are evidence of an old build,
# not of a broken path.
set -u
cd /mnt/c/ugit/SK-D840N4
export LC_ALL=C

R=/tmp/staged-root2
IMG=$R/lib/upgrade/platform.sh
OV=ci-build-repo/overlays/target/linux/zte/zx279133/base-files/lib/upgrade/platform.sh
GEN=immortalwrt/package/base-files/files/lib/upgrade/platform.sh
pass=0; fail=0
ok()  { printf '  PASS  %-52s %s\n' "$1" "${2:-}"; pass=$((pass+1)); }
bad() { printf '  FAIL  %-52s %s\n' "$1" "${2:-}"; fail=$((fail+1)); }

echo "=== 1. the three candidates ==="
for f in "$IMG" "$OV" "$GEN"; do
	if [ -f "$f" ]; then
		printf '  %-72s %6s bytes  sha %s\n' "$f" "$(wc -c <"$f")" "$(sha256sum "$f" | cut -c1-12)"
	else
		printf '  %-72s ABSENT\n' "$f"
	fi
done

echo
echo "=== 2. the marker: a function name that exists only in our overlay ==="
if grep -q 'skd840n_has_part' "$OV" 2>/dev/null; then
	ok "the overlay defines skd840n_has_part" ""
else bad "overlay defines skd840n_has_part" "marker not found - pick another"; fi
if grep -q 'skd840n_has_part' "$GEN" 2>/dev/null; then
	bad "the generic base-files platform.sh does NOT define it" "marker is not distinctive"
else
	ok "the generic base-files platform.sh does not" "marker is distinctive"
fi
if grep -q 'skd840n_has_part' "$IMG" 2>/dev/null; then
	ok "the BUILT IMAGE carries it" "the overlay path is delivered"
else
	bad "the BUILT IMAGE carries it" "the overlay path is NOT being consumed - every platform.sh fix is dead"
fi

echo
echo "=== 3. so which file is in the image: ours or the generic one? ==="
if cmp -s "$IMG" "$OV" 2>/dev/null; then
	ok "the image copy is byte-identical to the overlay" ""
elif cmp -s "$IMG" "$GEN" 2>/dev/null; then
	bad "the image copy is byte-identical to the GENERIC base-files one" "our overlay never reached it"
else
	ok "the image copy is neither (it predates today's edits)" "expected: built 10-12"
	# process substitution is a bashism; dash rejects <(...), so use a temp file
	T1=$(mktemp)
	diff "$IMG" "$OV" >"$T1" 2>/dev/null || true
	printf '      it differs from today'"'"'s overlay in %s changed line(s)\n' "$(grep -c '^[<>]' "$T1")"
	rm -f "$T1"
fi

echo
echo "=== 4. today's two fixes must be ABSENT from a 10-12 build - assert that, so a"
echo "       future run can tell the two situations apart ==="
for m in skd840n_tar_z skd840n_conf_tar; do
	if grep -q "$m" "$IMG" 2>/dev/null; then
		printf '  note  %s IS in the image - this build is newer than expected\n' "$m"
	else
		ok "$m absent from the 10-12 build" "as expected"
	fi
done
# and the OLD bug must be present, which is what proves it is the pre-fix build
if grep -q 'UPGRADE_BACKUP' "$IMG" 2>/dev/null; then
	ok "the pre-fix UPGRADE_BACKUP branch is still there" "confirms this is the 10-12 build"
else
	printf '  note  no UPGRADE_BACKUP in the image - newer build?\n'
fi

echo
echo "=== 5. and 02_network, for the same reason ==="
IO=$R/etc/board.d/02_network
NO=ci-build-repo/overlays/target/linux/zte/zx279133/base-files/etc/board.d/02_network
NU=immortalwrt/target/linux/zte/zx279133/base-files/etc/board.d/02_network
for f in "$IO" "$NO" "$NU"; do
	if [ -f "$f" ]; then
		printf '  %-76s %6s bytes  sha %s\n' "$f" "$(wc -c <"$f")" "$(sha256sum "$f" | cut -c1-12)"
	else printf '  %-76s ABSENT\n' "$f"; fi
done
if [ -f "$IO" ] && [ -f "$NU" ]; then
	if cmp -s "$IO" "$NU"; then
		ok "the image's 02_network is the upstream one" "confirms the overlay DOES replace it in CI"
	else
		ok "the image's 02_network differs from upstream" "overlay delivered it"
	fi
fi
if [ -f "$IO" ] && [ -f "$NO" ]; then
	if cmp -s "$IO" "$NO"; then ok "and matches today's overlay exactly" ""
	else printf '  note  image predates the 02_network fix (expected for 10-12)\n'; fi
fi

echo
echo "=== pass=$pass fail=$fail ==="
[ "$fail" -eq 0 ] || exit 1
exit 0