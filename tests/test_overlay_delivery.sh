#!/bin/sh
# 本地排演套件（local rehearsal suite）：依赖 staging/ 下的真实构建产物，
# 在只 clone 本仓库的环境里跑不起来，也**没有**接进 CI。
# 为什么、怎么跑、缺的那个夹具从哪来：同目录 README.md。
# Does the CI overlay step actually deliver our fixes?
#
# Everything SK-D840N-specific reaches the build through two `cp -a` lines. The step does
# assert things about the board dts and about 02_network, but it never checks that the
# files we have changed are (a) present in the overlay, (b) landing at a path the tree
# really has, and (c) actually different from what the upstream tree ships. If any of
# those three failed, the build would still be green and the board would get stock
# behaviour - the worst possible outcome, because every gate would pass.
#
# Simulated here against the real local tree, plus a dry-run of the copy itself.
set -u
cd /mnt/c/ugit/SK-D840N4
export LC_ALL=C

REPO=ci-build-repo
IW=$REPO/../immortalwrt          # the upstream tree as cloned locally
pass=0; fail=0
ok()  { printf '  PASS  %-56s %s\n' "$1" "${2:-}"; pass=$((pass+1)); }
bad() { printf '  FAIL  %-56s %s\n' "$1" "${2:-}"; fail=$((fail+1)); }

echo "=== 0. sanity gate: the tree we are comparing against exists ==="
if [ ! -f "$IW/target/linux/zte/zx279133/base-files/etc/board.d/02_network" ]; then
	echo "  upstream tree not where expected: $IW"
	ls -d "$IW" 2>/dev/null || echo "  (absent)"
	exit 1
fi
printf '  upstream tree: %s\n' "$IW"
printf '  overlays/ files: %s   kernel-overlays/ files: %s\n' \
	"$(find "$REPO/overlays" -type f | wc -l)" \
	"$(find "$REPO/kernel-overlays" -type f | wc -l)"

echo
echo "=== 1. overlay files whose directory the tree does not have yet ==="
# NOT an error: `cp -a overlays/. tree/` creates directories as it goes, and that is
# exactly how target/linux/zte/zx279133/base-files/lib/upgrade/ comes to exist - the
# upstream tree has no lib/upgrade there, and in this tree there is no generic
# package/base-files/lib/upgrade/platform.sh either, so the overlay is the ONLY source.
# Whether the path is really consumed is settled against the built image in
# overlay_path_proof.sh, which checks for skd840n_has_part - a name that exists nowhere
# else. Here we only report what will be created.
n=0; created=0
for f in $(cd "$REPO" && find overlays -type f | sort); do
	rel=${f#overlays/}
	dest="$IW/$rel"
	n=$(( n + 1 ))
	parent=$(dirname "$dest")
	if [ ! -d "$parent" ]; then
		printf '    created by cp -a: %s\n' "$rel"
		created=$(( created + 1 ))
	fi
done
if [ "$created" -eq 0 ]; then
	ok "all $n overlay files land in directories that already exist" ""
else
	ok "of $n overlay files, $created need a directory created for them" "cp -a does this"
	printf '      proof that these still reach the image: overlay_path_proof.sh\n'
fi

echo
echo "=== 2. the kernel overlay likewise ==="
KROOT=$IW/../linux-mainline-zte-zxslc-sr1010/linux-6.18.38
n=0; missing=0
for f in $(cd "$REPO" && find kernel-overlays -type f | sort); do
	rel=${f#kernel-overlays/}
	n=$(( n + 1 ))
	printf '    %s\n' "$rel"
done
printf '  %s kernel-overlay file(s). The kernel tree is not checked out locally,\n' "$n"
printf '  so their destinations can only be checked in CI; the step already asserts\n'
printf '  the dts exists and is registered in its Makefile.\n'

echo
echo "=== 3. do our four fixes actually differ from upstream? (else the overlay is dead weight) ==="
# path-in-overlay : path-in-upstream : what the fix is
check_fix() { # $1 label $2 overlay-rel $3 upstream-rel $4 marker
	ov="$REPO/overlays/$2"; up="$IW/$3"
	if [ ! -f "$ov" ]; then bad "$1 exists in overlays/" "$2"; return; fi
	if [ ! -f "$up" ]; then
		# a file upstream does not have is a pure addition, which is also fine
		ok "$1: new file, upstream has none" "$2"
		return
	fi
	if grep -q -- "$4" "$ov" && ! grep -q -- "$4" "$up"; then
		ok "$1: fix present in overlay, absent upstream" "$2"
	elif cmp -s "$ov" "$up"; then
		bad "$1: overlay copy is identical to upstream" "$2 - the fix would not ship"
	else
		ok "$1: overlay copy differs from upstream" "$2"
	fi
}
check_fix "platform.sh conf_tar" \
	"target/linux/zte/zx279133/base-files/lib/upgrade/platform.sh" \
	"target/linux/zte/zx279133/base-files/lib/upgrade/platform.sh" \
	'skd840n_conf_tar'
check_fix "platform.sh tar -z" \
	"target/linux/zte/zx279133/base-files/lib/upgrade/platform.sh" \
	"target/linux/zte/zx279133/base-files/lib/upgrade/platform.sh" \
	'skd840n_tar_z'
check_fix "02_network lan address" \
	"target/linux/zte/zx279133/base-files/etc/board.d/02_network" \
	"target/linux/zte/zx279133/base-files/etc/board.d/02_network" \
	'192\.168\.9\.1'

# The templates are repo files, not tree files - "differs from upstream" has no meaning
# for them. Just assert the fix is in the template that CI copies onto the board.
check_template() { # $1 label $2 rel $3 marker
	f="$REPO/$2"
	if [ ! -f "$f" ]; then bad "$1 exists" "$2"; return; fi
	if grep -q -- "$3" "$f"; then ok "$1 is in the shipped template" "$2"
	else bad "$1 is in the shipped template" "$2 - marker '$3' absent"; fi
}
check_template "flasher integrity gate" "templates/flash-sk-d840n.sh" 'sha256sums-release'
check_template "kexec trial: checksum before gunzip" "templates/trial-kexec.sh" 'BEFORE the gunzip'

echo
echo "=== 4. simulate the copy itself, exactly as CI does it ==="
SIM=$(mktemp -d)
mkdir -p "$SIM/openwrt"
# a minimal stand-in tree with the same shape, so an overlay path that does not exist
# upstream would have nothing to copy onto
cp -a "$IW/." "$SIM/openwrt/" 2>/dev/null
cp -a "$REPO/overlays/." "$SIM/openwrt/"
echo "  simulated cp -a overlays/. openwrt/ over $(find "$SIM/openwrt" -type f | wc -l) files"
for probe in \
	"target/linux/zte/zx279133/base-files/lib/upgrade/platform.sh" \
	"target/linux/zte/zx279133/base-files/etc/board.d/02_network"
do
	if [ -f "$SIM/openwrt/$probe" ]; then
		same=$(cmp -s "$REPO/overlays/$probe" "$SIM/openwrt/$probe" && echo yes || echo no)
		if [ "$same" = yes ]; then ok "landed: $probe" ""
		else bad "landed: $probe" "content differs after copy"; fi
	else
		bad "landed: $probe" "absent after the copy"
	fi
done

echo
echo "=== 5. the assertions CI makes, re-run on the simulated tree ==="
NET="$SIM/openwrt/target/linux/zte/zx279133/base-files/etc/board.d/02_network"
if [ -f "$NET" ]; then
	grep -q 'zte,zx279133-sr1010' "$NET" && ok "CI check: sr1010 case preserved" "" || bad "CI check: sr1010 case" "missing"
	grep -q 'skyworth,sk-d840n' "$NET"    && ok "CI check: sk-d840n case present" "" || bad "CI check: sk-d840n case" "missing"
	grep -q 'ipaddr "192\.168\.9\.1"' "$NET" && ok "CI check: lan pinned to 192.168.9.1" "" || bad "CI check: lan address" "missing"
	cr=$(tr -cd '\r' <"$NET" | wc -c)
	[ "$cr" -eq 0 ] && ok "CI check: 02_network has 0 CR bytes" "" || bad "CI check: CR bytes" "$cr"
else
	bad "02_network present in the simulated tree" "$NET"
fi
# platform.sh must be the fixed one, not the upstream one
PL="$SIM/openwrt/target/linux/zte/zx279133/base-files/lib/upgrade/platform.sh"
if [ -f "$PL" ]; then
	grep -q 'skd840n_conf_tar' "$PL" && ok "simulated platform.sh carries the conf_tar fix" "" || bad "simulated platform.sh" "fix missing after the copy"
	sh -n "$PL" && ok "simulated platform.sh parses" "" || bad "simulated platform.sh parses" ""
else
	bad "platform.sh present in the simulated tree" "$PL"
fi
rm -rf "$SIM"

echo
echo "=== 6. nothing in the overlay tree carries a CR ==="
crf=0
for f in $(find "$REPO/overlays" "$REPO/kernel-overlays" "$REPO/templates" -type f); do
	c=$(tr -cd '\r' <"$f" | wc -c)
	[ "$c" -eq 0 ] || { printf '    %s has %s CR\n' "$f" "$c"; crf=$(( crf + 1 )); }
done
[ "$crf" -eq 0 ] && ok "every overlay and template file is LF-only" "" \
	|| bad "CR bytes in overlay files" "$crf file(s)"

echo
echo "=== pass=$pass fail=$fail ==="
[ "$fail" -eq 0 ] || exit 1
exit 0