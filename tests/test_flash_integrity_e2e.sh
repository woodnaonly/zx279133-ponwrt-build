#!/bin/sh
# 本地排演套件（local rehearsal suite）：依赖 staging/ 下的真实构建产物，
# 在只 clone 本仓库的环境里跑不起来，也**没有**接进 CI。
# 为什么、怎么跑、缺的那个夹具从哪来：同目录 README.md。
# The integrity gate in flash-sk-d840n.sh, end to end, with the real artifacts.
#
# This is the fix whose failure mode is worst: without it, an image truncated by an
# interrupted copy is programmed into three partitions - kernel included - and reported as a
# success, on a board with no serial console. It has so far only been exercised as a unit.
# The checks below run the shipped script itself, in its own directory, against a real
# sha256sums-release, and stop at the first point where the script would need the board.
#
# Both directions matter, and the quiet one matters more:
#   corrupt image + manifest present -> must refuse, and say why
#   corrupt image + manifest absent  -> must NOT pretend to be verified; it may continue,
#                                       but only after saying so out loud
set -u
cd /mnt/c/ugit/SK-D840N4

S=ci-build-repo/templates/flash-sk-d840n.sh
[ -s "$S" ] || { echo "$S missing" >&2; exit 1; }

pass=0
ok()  { pass=$((pass+1)); printf 'PASS %s\n' "$1"; }
bad() { echo "FAIL $1"; exit 1; }

T=$(mktemp -d); trap 'rm -rf "$T"' EXIT

# The script's first act is `command -v mtd` (line 29), which is before the integrity gate
# (line 46). On this host that dies at the door, so the gate is never reached. A stub mtd lets
# the test get past the precondition and exercise what it is actually about; the precondition
# itself is a separate check and is not what this suite is measuring.
mkdir -p "$T/stub"
printf '#!/bin/sh\necho "stub mtd: $*" >&2\nexit 0\n' >"$T/stub/mtd"
chmod +x "$T/stub/mtd"
PATH="$T/stub:$PATH"; export PATH

# A working flash-set with a manifest that actually describes the files next to it.
mkdir -p "$T/set"
printf 'not a uImage, but the gate must not care about content - only about the manifest\n' >"$T/set/flash-uImage"
printf 'not an FDT either\n'                                            >"$T/set/flash-dtb.bin"
printf 'not a jffs2 either\n'                                           >"$T/set/flash-rootfs.jffs2"
cp "$S" "$T/set/flash-sk-d840n.sh"
chmod +x "$T/set/flash-sk-d840n.sh"
( cd "$T/set" && sha256sum flash-uImage flash-dtb.bin flash-rootfs.jffs2 >sha256sums-release )
cp "$T/set/sha256sums-release" "$T/manifest.good"

run() { # dir -> combined output; the script dies shortly after the gate, which is fine
	( cd "$1" && sh ./flash-sk-d840n.sh 2>&1 )
}

echo "=== the shipped script reaches the gate at all ==="
out=$(run "$T/set")
echo "$out" | sed 's/^/  /'
echo "$out" | grep -q 'verifying images against sha256sums-release' \
	|| bad "the manifest branch was not taken - the gate may have moved or been dropped"
ok "manifest present -> the verification branch runs"

echo
echo "=== a corrupted image must stop it ==="
printf 'corrupted after the manifest was made\n' >"$T/set/flash-dtb.bin"
out=$(run "$T/set")
echo "$out" | sed 's/^/  /'
echo "$out" | grep -q 'refusing to program flash' \
	|| bad "a corrupt image did not trigger the refusal"
echo "$out" | grep -q 'FAILED' || echo "$out" | grep -q 'does not match' \
	|| echo "  (note: sha256sum's own wording not matched, but it did refuse)"
ok "corrupt image -> refuses to program flash"

echo
echo "=== ...and it must have refused BEFORE any geometry work ==="
# The gate is lines 46-52; geometry and the partition checks are after it. If a refusal ever
# arrives after "flasher: /sbin/mtd not found" ordering or after /proc/mtd is touched, the
# gate is no longer the first thing standing between a bad image and the flash.
if echo "$out" | grep -q 'root is mtd\|no .root. partition in /proc/mtd'; then
	echo "  the script got past the gate into partition work:"
	echo "$out" | grep -n 'root is mtd\|no .root. partition' | sed 's/^/    /'
	bad "the gate ran after the partition checks - reordering would matter"
fi
ok "refusal comes before any partition/geometry work"

echo
echo "=== the honest-absent path: no manifest, corrupt image, must say so ==="
rm -f "$T/set/sha256sums-release"
printf 'corrupted, and nothing will check it\n' >"$T/set/flash-rootfs.jffs2"
out=$(run "$T/set")
echo "$out" | sed 's/^/  /'
echo "$out" | grep -q 'no sha256sums-release beside the images' \
	|| bad "the absent manifest was passed over in silence - that is the failure mode"
echo "$out" | grep -q 'integrity is UNVERIFIED' \
	|| bad "the absence was announced but not as unverified"
ok "absent manifest -> announced loudly, as UNVERIFIED"

echo
echo "=== an empty manifest must not read as 'verified' ==="
: >"$T/set/sha256sums-release"
out=$(run "$T/set")
echo "$out" | sed 's/^/  /'
if echo "$out" | grep -q 'verifying images against sha256sums-release' \
   && ! echo "$out" | grep -q 'does not match\|FAILED\|no properly formatted'; then
	bad "an empty manifest was treated as a successful verification"
fi
ok "empty manifest does not produce a silent 'verified'"

echo
echo "=== the manifest must resolve relative to the script, not to the cwd ==="
# sha256sums-release holds bare filenames. If the gate ran `sha256sum -c` without cd-ing to
# the script's own directory it would either fail to find the manifest or - worse - verify a
# different set of files that happens to share the names. A GOOD manifest is restored first, so
# this asserts a successful verification from an unrelated cwd rather than just that the gate ran.
cp "$T/manifest.good" "$T/set/sha256sums-release"
printf 'unchanged again\n' >"$T/set/flash-dtb.bin"
( cd "$T/set" && sha256sum flash-uImage flash-dtb.bin flash-rootfs.jffs2 >sha256sums-release )
mkdir -p "$T/elsewhere"
out=$( ( cd "$T/elsewhere" && sh "$T/set/flash-sk-d840n.sh" 2>&1 ) )
echo "$out" | sed 's/^/  /'
echo "$out" | grep -q 'flash-uImage: OK' \
	|| bad "from a foreign cwd the manifest did not verify the images next to the script"
echo "$out" | grep -q 'flash-dtb.bin: OK' \
	|| bad "from a foreign cwd flash-dtb.bin did not verify"
# Scoped to the manifest on purpose: this run also contains "cannot open /proc/mtd (No such
# file or directory)" from the host simply having no partition table, and a substring test
# over the whole output would fire on that instead of on anything about the gate.
echo "$out" | grep -q 'sha256sums-release.*No such file' \
	&& bad "the gate tried to open the manifest relative to the cwd"
ok "gate is cwd-independent (it cds to the script's own directory)"

echo
echo "all $pass checks passed"
