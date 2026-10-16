#!/bin/sh
# 本地排演套件（local rehearsal suite）：依赖 staging/ 下的真实构建产物，
# 在只 clone 本仓库的环境里跑不起来，也**没有**接进 CI。
# 为什么、怎么跑、缺的那个夹具从哪来：同目录 README.md。
# tools/check-imagelineage.sh must refuse a stale set and accept a current one, in BOTH
# layers: the files inside the rootfs image, and the scripts and manifest that travel
# beside it.
#
# The new layer is the one that could have been written to always pass, so it gets the
# most controls:
#   * calibration of the manifest matcher itself, before any verdict rests on it
#   * a complete set (current rootfs files + current scripts + complete manifest) -> OK
#   * ONE stale script in that same set -> must refuse and name the script
#   * ONE payload file dropped from the manifest -> must refuse and say why
#   * a set with no scripts at all -> must not invent a failure (absence != stale)
set -u
cd /mnt/c/ugit/SK-D840N4 || exit 1

# Absolute, for the reason in test_imagelineage.sh: the tool cds to $REPO before reading its
# arguments, so a relative `ci-build-repo/...` resolves one level too deep and every file
# refuses as "no overlay counterpart - cannot verify" instead of being compared.
REPO=$(cd "$(dirname "$0")/.." && pwd)
TOOL=$REPO/tools/check-imagelineage.sh
OVL=$REPO/overlays/target/linux/zte/zx279133/base-files
TMPL=$REPO/templates
# The STALE case is pinned to release 21's own published set, not to whatever the workspace
# happens to be holding. This suite used to assert "the staged set is stale", which is a
# statement about the workspace rather than about the tool; it passed for as long as the
# workspace stayed old, and went red on 2026-10-15 with "it ACCEPTED the stale set" the
# moment CI rebuilt and the set was refreshed. The tool was right both times.
ROOTFS=$(dirname "$0")/fixtures/stale-set/flash-rootfs.jffs2

pass=0
ok()  { pass=$((pass+1)); printf 'PASS %s\n' "$1"; }
bad() { printf 'FAIL %s\n' "$1"; exit 1; }
note(){ printf 'note %s\n' "$1"; }

[ -f "$TOOL" ] || bad "$TOOL missing"
sh -n "$TOOL" || bad "$TOOL does not parse"
ok "the tool parses"
grep -c "$(printf '\r')" "$TOOL" >/dev/null 2>&1
[ "$(tr -cd '\r' <"$TOOL" | wc -c)" -eq 0 ] && ok "the tool has no CR" || bad "CR in the tool"

# Build a set that is current in every respect, then take it apart one piece at a time.
# The image half is a copy of the overlay rather than a named list, for the same reason as in
# test_imagelineage.sh: this suite asserts that a current set is ACCEPTED, and a fixture that
# names only the files it happens to know about stops testing that the moment the tool grows.
# It did exactly that on 2026-10-16 when a third check_one was added, and the suite went red
# against correct behaviour - which is the failure mode this project's own comment above
# describes for a different assertion.
GOOD=$(mktemp -d)
cp -a "$OVL/." "$GOOD/"
for n in flash-uImage flash-dtb.bin flash-rootfs.jffs2 flash-sysupgrade.bin \
         flash-sk-d840n.sh trial-kexec.sh; do
	: >"$GOOD/$n"
done
cp "$TMPL/flash-sk-d840n.sh" "$GOOD/flash-sk-d840n.sh"
cp "$TMPL/trial-kexec.sh"    "$GOOD/trial-kexec.sh"
( cd "$GOOD" && sha256sum flash-uImage flash-dtb.bin flash-rootfs.jffs2 \
	flash-sysupgrade.bin flash-sk-d840n.sh > sha256sums-release )

echo
echo "=== 1. CALIBRATION: the manifest matcher, before any verdict rests on it ==="
m() { tr -d '\r' | grep -q "[[:space:]]$1\$"; }
printf '%s  flash-uImage\n' 0000 | m flash-uImage && ok "it matches a real manifest line" \
	|| bad "the matcher cannot even match a correct line"
printf '%s  flash-uImage\r\n' 0000 | m flash-uImage && ok "it survives CRLF line endings" \
	|| bad "a CRLF manifest would make it report false coverage"
printf '%s  myflash-uImage\n' 0000 | m flash-uImage \
	&& bad "it matched a substring - every name would be over-reported as covered" \
	|| ok "it does not match a name that is only a suffix of another"
printf '%s  flash-uImage\n' 0000 | m flash-dtb.bin \
	&& bad "it matched a different payload" || ok "it does not match a different payload"
printf '%s  flash-uImage.bak\n' 0000 | m flash-uImage \
	&& bad "it matched a longer filename" || ok "it does not match a longer filename"

echo
echo "=== 2. the real staged set is stale in BOTH layers and both must be named ==="
[ -f "$ROOTFS" ] || bad "$ROOTFS missing - cannot prove the refusal"
if command -v jefferson >/dev/null 2>&1; then
	sh "$TOOL" "$ROOTFS" "$OVL" >/tmp/lin.out 2>&1 && bad "it ACCEPTED the stale set"
	ok "it refuses (exit nonzero)"
	grep -q 'REFUSE in-image/upgrade path' /tmp/lin.out \
		&& ok "layer 1 is reported" || bad "no layer-1 verdict"
	grep -q 'REFUSE beside-image/flasher' /tmp/lin.out \
		&& ok "layer 2 is reported too - the flasher is caught, not just the rootfs" \
		|| bad "it only looked inside the image"
	grep -q 'REFUSE beside-image/RAM trial launcher' /tmp/lin.out \
		&& ok "the RAM trial launcher is caught" || bad "trial-kexec.sh not checked"
	grep -q 'REFUSE manifest/flash-sk-d840n.sh' /tmp/lin.out \
		&& ok "and the manifest gap is caught" || bad "manifest coverage not checked"
	grep -q '192.168.9.1' /tmp/lin.out && ok "it says what would happen to the address" \
		|| bad "refuses without the consequence"
	grep -q 'saved configuration is dropped' /tmp/lin.out \
		&& ok "and what would happen to the saved config" || bad "no config consequence"
	grep -q 'no serial console' /tmp/lin.out \
		&& ok "and the consequence of the missing integrity check" || bad "no brick-risk wording"
	grep -q 'Do NOT flash' /tmp/lin.out && ok "and tells the operator not to flash it" || bad "no instruction"
else
	note "jefferson absent - the in-image verdicts cannot run here"
fi

echo
echo "=== 3. a complete current set must be ACCEPTED (the gate is not always-red) ==="
if SETDIR="$GOOD" sh "$TOOL" "$GOOD" "$OVL" >/tmp/lin_good.out 2>&1; then
	ok "a set that matches the tree everywhere is accepted"
	grep -q 'OK: this rootfs carries the current overlays, and the scripts beside it are current' \
		/tmp/lin_good.out && ok "and says so" || bad "accepted but did not say so"
	grep -c 'beside-image/flasher: flash-sk-d840n.sh matches templates/' /tmp/lin_good.out \
		| grep -q '^1$' && ok "and it really compared the flasher" || bad "layer 2 did not run"
	grep -c 'manifest: every payload file beside it is covered' /tmp/lin_good.out \
		| grep -q '^1$' && ok "and really checked manifest coverage" || bad "manifest layer did not run"
else
	bad "it rejected a fully current set"; sed 's/^/      /' /tmp/lin_good.out
fi

echo
echo "=== 4. ONE stale script in an otherwise current set must refuse ==="
cp "$GOOD/flash-sk-d840n.sh" "$GOOD/flash-sk-d840n.sh.keep"
printf '#!/bin/sh\n# the version without the sha256 check\n' >"$GOOD/flash-sk-d840n.sh"
SETDIR="$GOOD" sh "$TOOL" "$GOOD" "$OVL" >/tmp/lin_stale.out 2>&1 && bad "it accepted a stale flasher"
ok "it refuses"
grep -q 'REFUSE beside-image/flasher' /tmp/lin_stale.out && ok "and names the flasher" \
	|| bad "refused without naming it"
grep -q 'no integrity check' /tmp/lin_stale.out && ok "and states what that costs" \
	|| bad "no consequence stated"
grep -q 'REFUSE in-image' /tmp/lin_stale.out && bad "layer 1 fired on a current image" \
	|| ok "and layer 1 stayed quiet - the verdicts are independent"
mv "$GOOD/flash-sk-d840n.sh.keep" "$GOOD/flash-sk-d840n.sh"

echo
echo "=== 5. the shipped trial launcher must refuse when it verifies after gunzip ==="
printf '#!/bin/sh\n# gunzip first, verify later\n' >"$GOOD/trial-kexec.sh"
SETDIR="$GOOD" sh "$TOOL" "$GOOD" "$OVL" >/tmp/lin_tk.out 2>&1 && bad "it accepted a stale launcher"
grep -q 'REFUSE beside-image/RAM trial launcher' /tmp/lin_tk.out && ok "and names the launcher" \
	|| bad "refused without naming it"
grep -q 'gunzipped onto the board' /tmp/lin_tk.out && ok "and says it verifies too late" \
	|| bad "no wording about the ordering"
cp "$TMPL/trial-kexec.sh" "$GOOD/trial-kexec.sh"

echo
echo "=== 6. a payload file missing from the manifest must refuse ==="
grep -v 'flash-sk-d840n.sh' "$GOOD/sha256sums-release" >"$GOOD/m2" && mv "$GOOD/m2" "$GOOD/sha256sums-release"
SETDIR="$GOOD" sh "$TOOL" "$GOOD" "$OVL" >/tmp/lin_man.out 2>&1 && bad "it accepted an uncovered payload file"
grep -q 'REFUSE manifest/flash-sk-d840n.sh' /tmp/lin_man.out && ok "and names the uncovered file" \
	|| bad "refused without naming it"
grep -q 'reports success while leaving it unverified' /tmp/lin_man.out \
	&& ok "and explains why that is worse than no manifest" || bad "no explanation"
( cd "$GOOD" && sha256sum flash-uImage flash-dtb.bin flash-rootfs.jffs2 \
	flash-sysupgrade.bin flash-sk-d840n.sh > sha256sums-release )
SETDIR="$GOOD" sh "$TOOL" "$GOOD" "$OVL" >/dev/null 2>&1 && ok "restoring the entry clears the verdict" \
	|| bad "still refuses after the manifest was completed"

echo
echo "=== 7. a set with no scripts beside it must not invent a failure ==="
BARE=$(mktemp -d)
cp -a "$OVL/." "$BARE/"
if SETDIR="$BARE" sh "$TOOL" "$BARE" "$OVL" >/tmp/lin_bare.out 2>&1; then
	ok "a bare extracted rootfs is still accepted"
	grep -q 'is not part of this set, so it cannot be stale' /tmp/lin_bare.out \
		&& ok "and absence is reported as absence, not as staleness" || bad "wrong wording"
else
	bad "it demanded scripts that are not part of this set"; sed 's/^/      /' /tmp/lin_bare.out
fi
rm -rf "$BARE"

echo
echo "=== 8. missing counterparts still fail closed ==="
EMPTY=$(mktemp -d)
D1=$(mktemp -d); cp -a "$OVL/." "$D1/"
SETDIR="$D1" sh "$TOOL" "$D1" "$EMPTY" >/tmp/lin_nocmp.out 2>&1 && bad "it compared against nothing and passed"
grep -q 'cannot verify' /tmp/lin_nocmp.out && ok "overlay side: refuses when it cannot compare" \
	|| bad "overlay side did not fail closed"
TEMPLATES=/nonexistent SETDIR="$GOOD" sh "$TOOL" "$GOOD" "$OVL" >/tmp/lin_notmpl.out 2>&1 \
	&& bad "it passed with no templates dir"
grep -q 'beside-image: templates dir' /tmp/lin_notmpl.out \
	&& ok "script side: refuses when it cannot compare either" || bad "script side did not fail closed"
rm -rf "$EMPTY" "$D1"

echo
echo "=== 9. ALLOW_UNVERIFIED still exits 0 but says it skipped ==="
if command -v jefferson >/dev/null 2>&1; then
	out=$(JEFFERSON=/nonexistent ALLOW_UNVERIFIED=1 sh "$TOOL" "$ROOTFS" "$OVL" 2>&1); rc=$?
	[ "$rc" -eq 0 ] && ok "with no jefferson and ALLOW_UNVERIFIED=1 it exits 0" || bad "expected 0, got $rc"
	echo "$out" | grep -q 'was NOT verified' && ok "and says the image was not verified" \
		|| bad "it skipped silently"
	JEFFERSON=/nonexistent sh "$TOOL" "$ROOTFS" "$OVL" >/dev/null 2>&1 \
		&& bad "it passed with no way to read the image" || ok "without that hatch it fails closed"
else
	note "jefferson absent - skipped"
fi

echo
echo "=== 8b. with no SETDIR the set-level check must say it did not run ==="
# The failure this prevents: an extracted tree with no set beside it, where the tool
# either stays silent about layer 2 or - worse - prints the same "scripts beside it are
# current" sentence it prints when it really did compare them.
sh "$TOOL" "$GOOD" "$OVL" >/tmp/lin_na.out 2>&1 && ok "the in-image verdict alone is still an accept"
grep -q 'layer 2: NOT APPLICABLE' /tmp/lin_na.out && ok "it announces that layer 2 did not apply" \
	|| bad "layer 2 went unmentioned"
grep -q 'This is not a pass' /tmp/lin_na.out && ok "and refuses to call that a pass" \
	|| bad "it implied the set was checked"
grep -q 'layer 2 did NOT run' /tmp/lin_na.out && ok "and the summary line says so too" \
	|| bad "the summary still claims the scripts were checked"
grep -q 'scripts beside it are current' /tmp/lin_na.out \
	&& bad "it used the full-accept wording for a partial check" \
	|| ok "and does not use the full-accept wording"

echo
rm -rf "$GOOD"
echo
echo "ALL OK - $pass checks"
