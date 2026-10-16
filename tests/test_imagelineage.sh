#!/bin/sh
# 本地排演套件（local rehearsal suite）：依赖 staging/ 下的真实构建产物，
# 在只 clone 本仓库的环境里跑不起来，也**没有**接进 CI。
# 为什么、怎么跑、缺的那个夹具从哪来：同目录 README.md。
# tools/check-imagelineage.sh must refuse a stale rootfs and accept a current one.
#
# The tool's whole job is to be wrong in exactly one direction - saying OK about an
# image that predates the fixes - so this suite is built around the cases that would
# produce that:
#   * a PINNED stale image (the 2026-10-12 release, kept as a fixture) must be refused
#   * the currently staged image must be accepted
#   * a directory carrying the current overlay must be accepted
#   * a missing overlay counterpart must refuse, not skip (the first version of the
#     tool skipped it and then said OK, having compared nothing)
#   * ALLOW_UNVERIFIED=1 must exit 0 but say so loudly
#
# The stale case used to point at `staging/trial/flash-rootfs.jffs2` and assert "that is
# stale". That is a claim about the workspace, not about the tool: it could only ever pass
# while the workspace happened to hold the old build. On 2026-10-15 CI rebuilt, the staged
# set was refreshed to the current tree, and this suite went red with "the tool ACCEPTED a
# rootfs that predates every fix". The tool was right. The fixture is now release 21's own
# published rootfs, which does not change when the workspace does.
set -u
cd /mnt/c/ugit/SK-D840N4 || exit 1

# Both paths are absolute. They used to be relative to the workspace root, which was correct
# only while the tool still lived there: the shipped tool does `cd "$REPO"` before it reads
# its arguments, so a relative `ci-build-repo/overlays/...` asks for
# ci-build-repo/ci-build-repo/overlays/... and does not exist. The gate then refuses with
# "no overlay counterpart - cannot verify" for every file, and the real verdict - the stale
# comparison, with the address it would have come up on - never happens. Same defect class as
# e8f6036, reached from the caller this time.
REPO=$(cd "$(dirname "$0")/.." && pwd)
TOOL=$REPO/tools/check-imagelineage.sh
OVL=$REPO/overlays/target/linux/zte/zx279133/base-files
# Pinned to release 21's published rootfs, not to whatever is staged. The accept direction
# is proven against a constructed current directory further down, so neither direction here
# depends on the state of the workspace.
ROOTFS=$(dirname "$0")/fixtures/stale-set/flash-rootfs.jffs2

pass=0
ok()  { pass=$((pass+1)); printf 'PASS %s\n' "$1"; }
bad() { printf 'FAIL %s\n' "$1"; exit 1; }
note(){ printf 'note %s\n' "$1"; }

[ -f "$TOOL" ] || bad "$TOOL missing"
sh -n "$TOOL" || bad "$TOOL does not parse"
ok "the tool parses"

echo
echo "=== 1. the real staged image is stale and must be refused ==="
[ -f "$ROOTFS" ] || bad "$ROOTFS missing - this suite cannot prove the refusal"
command -v jefferson >/dev/null 2>&1 || { note "jefferson absent; the directory cases below still run"; }

if sh "$TOOL" "$ROOTFS" "$OVL" >/tmp/lin.out 2>&1; then
	bad "the tool ACCEPTED a rootfs that predates every fix"
else
	ok "it refuses (exit nonzero)"
fi
# The verdict carries a layer prefix (in-image/ ...) since the tool also checks the
# scripts that travel beside the image; see test_imagelineage2.sh for that layer.
grep -q 'REFUSE in-image/upgrade path' /tmp/lin.out && ok "and names the upgrade path" || bad "no verdict on platform.sh"
grep -q '192.168.9.1' /tmp/lin.out && ok "and says what would happen to the address" \
	|| bad "it refuses without saying the consequence"
grep -q 'saved configuration is dropped' /tmp/lin.out \
	&& ok "and says what would happen to the saved config" \
	|| bad "it refuses without saying the config consequence"
grep -q 'Do NOT flash' /tmp/lin.out && ok "and tells the operator not to flash it" || bad "no instruction"

echo
echo "=== 2. a directory carrying the current overlays must be accepted ==="
# The fake image is a copy of the overlay, not a hand-picked list of paths. It used to copy
# platform.sh and 02_network by name, which meant every check_one added to the tool broke
# this test - and a test that breaks when the tool grows is a test pinned to a stale fact
# rather than to the behaviour it claims to check. What is actually being asserted here is
# "an image matching the overlay is accepted", and cp -a of the overlay is that, always.
FAKE=$(mktemp -d)
cp -a "$OVL/." "$FAKE/"
if sh "$TOOL" "$FAKE" "$OVL" >/tmp/lin2.out 2>&1; then
	ok "it accepts an image whose files match the overlay"
	grep -q 'OK: this rootfs carries the current overlays' /tmp/lin2.out \
		&& ok "and says so" || bad "accepted but did not say so"
else
	bad "it rejected a current image"; sed 's/^/      /' /tmp/lin2.out
fi

echo
echo "=== 3. one stale file is enough to refuse ==="
cp "$ROOTFS" /dev/null 2>/dev/null || true
printf '#!/bin/sh\n# the pre-fix version\n' >"$FAKE/lib/upgrade/platform.sh"
if sh "$TOOL" "$FAKE" "$OVL" >/tmp/lin3.out 2>&1; then
	bad "it accepted an image with a stale platform.sh"
else
	ok "it refuses when platform.sh is stale"
fi
grep -q 'REFUSE in-image/network defaults\|REFUSE in-image/upgrade path' /tmp/lin3.out \
	&& ok "and names the stale file" || bad "refused without naming the file"
rm -rf "$FAKE"

echo
echo "=== 4. a missing overlay counterpart must refuse, not skip ==="
FAKE2=$(mktemp -d)
cp -a "$OVL/." "$FAKE2/"
EMPTY=$(mktemp -d)
if sh "$TOOL" "$FAKE2" "$EMPTY" >/tmp/lin4.out 2>&1; then
	bad "it accepted an image it could not compare against anything"
else
	ok "it refuses when there is nothing to compare against"
fi
grep -q 'cannot verify' /tmp/lin4.out \
	&& ok "and says why that is not a pass" || bad "refused without explaining"
rm -rf "$FAKE2" "$EMPTY"

echo
echo "=== 5. ALLOW_UNVERIFIED must exit 0 but still say it skipped ==="
if sh "$TOOL" "$ROOTFS" "$OVL" ALLOW_UNVERIFIED=1 >/tmp/lin5.out 2>&1; then
	note "ALLOW_UNVERIFIED=1 did not change the exit code"
else
	ok "it still refuses (the env var is not read from the argument list - correct)"
fi
out=$(JEFFERSON=/nonexistent ALLOW_UNVERIFIED=1 sh "$TOOL" "$ROOTFS" "$OVL" 2>&1); rc=$?
[ "$rc" -eq 0 ] && ok "with no jefferson and ALLOW_UNVERIFIED=1 it exits 0" \
	|| bad "expected exit 0 there, got $rc"
echo "$out" | grep -q 'WARNING' && ok "and warns loudly" || bad "it skipped silently"
echo "$out" | grep -q 'was NOT verified' && ok "saying the image was not verified" \
	|| bad "no explicit 'not verified' wording"
out=$(JEFFERSON=/nonexistent sh "$TOOL" "$ROOTFS" "$OVL" 2>&1); rc=$?
[ "$rc" -ne 0 ] && ok "without that escape hatch it fails closed" || bad "it passed with no way to read the image"

echo
echo "ALL OK - $pass checks"
