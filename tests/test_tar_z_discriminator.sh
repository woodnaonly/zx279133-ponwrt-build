#!/bin/sh
# 本地排演套件（local rehearsal suite）：依赖 staging/ 下的真实构建产物，
# 在只 clone 本仓库的环境里跑不起来，也**没有**接进 CI。
# 为什么、怎么跑、缺的那个夹具从哪来：同目录 README.md。
# Does skd840n_tar_z actually discriminate - and would it have caught the od bug?
#
# The discriminator is one line of shell wrapped in a comment that claims it works. It ran
# green against a single uncompressed artifact, which is exactly the case where a broken
# discriminator looks correct: the answer it gets by accident is the right answer. So both
# directions are required, and so is the old implementation run side by side under the
# conditions the board actually has.
#
# The shipped function is extracted mechanically from platform.sh rather than copied here, so
# this test cannot drift away from the code it is testing.
set -u
cd /mnt/c/ugit/SK-D840N4

SH=ci-build-repo/overlays/target/linux/zte/zx279133/base-files/lib/upgrade/platform.sh
[ -s "$SH" ] || { echo "$SH missing" >&2; exit 1; }
command -v hexdump >/dev/null || { echo "host hexdump required (apt-get install bsdextrautils)" >&2; exit 1; }

pass=0
ok()  { pass=$((pass+1)); printf 'PASS %s\n' "$1"; }
bad() { echo "FAIL $1"; exit 1; }

T=$(mktemp -d); trap 'rm -rf "$T"' EXIT

# --- pull the real function out of the shipped file -----------------------------------------
awk '/^skd840n_tar_z\(\)/,/^}/' "$SH" >"$T/fn.sh"
grep -q '^skd840n_tar_z()' "$T/fn.sh" || bad "could not extract skd840n_tar_z from $SH"
grep -q 'hexdump' "$T/fn.sh" || bad "extracted function no longer uses hexdump"
ok "extracted skd840n_tar_z from the shipped platform.sh"
# shellcheck source=/dev/null
. "$T/fn.sh"

# --- the real artifact: a plain tar, so the answer is "" -------------------------------------
r=$(skd840n_tar_z staging/trial/flash-sysupgrade.bin)
[ -z "$r" ] || bad "uncompressed flash-sysupgrade.bin classified as '$r', want empty"
ok "real uncompressed sysupgrade.bin -> empty (no -z)"

# --- a real gzip must be seen ---------------------------------------------------------------
printf '\037\213\010\000rest' >"$T/gz.bin"
r=$(skd840n_tar_z "$T/gz.bin")
[ "$r" = z ] || bad "gzip fixture classified as '$r', want z"
ok "gzip fixture -> z"

# --- and a real gzip produced by gzip, not a hand-made header -------------------------------
printf 'sysupgrade-x/kernel payload\n' >"$T/member"
tar czf "$T/real.tgz" -C "$T" member
r=$(skd840n_tar_z "$T/real.tgz")
[ "$r" = z ] || bad "gzip(1)-produced tar classified as '$r', want z"
ok "gzip(1)-produced .tar.gz -> z"

# --- the old implementation, under the board's actual tool set --------------------------------
# The defect is not in the logic - on a host that has od the old code is correct. The defect is
# that od is not shipped on this board, so PATH has to be reproduced, not approximated. Two
# sandboxes are built from symlinks: one holding only what the board has, one holding only what
# a desktop has. Running both implementations in both sandboxes separates "the logic is wrong"
# from "the dependency is missing" - which is the distinction that made this survive review.
cat >"$T/old.sh" <<'OLD'
skd840n_old_tar_z() {
	case "$(head -c 2 "$1" 2>/dev/null | od -An -tx1 2>/dev/null | tr -d ' \n')" in
	1f8b) echo z ;;
	esac
}
OLD
# shellcheck source=/dev/null
. "$T/old.sh"

mkbin() { # dir tool...
	d="$1"; shift
	mkdir -p "$d"
	for t in "$@"; do
		p=$(command -v "$t" 2>/dev/null) || { echo "host is missing $t" >&2; exit 1; }
		ln -sf "$p" "$d/$t"
	done
}

# The board: hexdump present at /usr/bin/hexdump, od compiled in but never symlinked.
mkbin "$T/board" dd hexdump
# A desktop: od present, so the old code looks fine.
mkbin "$T/desktop" dd head od tr

board_path=$(cd "$T/board" && pwd)
desktop_path=$(cd "$T/desktop" && pwd)

run() { # path dir function file
	( PATH="$2"; export PATH; "$3" "$4" 2>/dev/null )
}

# 1. shipped code, board tool set, both directions
r=$(run x "$board_path" skd840n_tar_z "$T/real.tgz")
[ "$r" = z ] || bad "shipped code in the board tool set missed a real .tar.gz (got '$r')"
ok "shipped code + board PATH (no od): detects gzip"

# 2. shipped code, desktop tool set, both directions - it must not depend on the sandbox
r=$(run x "$desktop_path:/usr/bin:/bin" skd840n_tar_z "$T/real.tgz")
[ "$r" = z ] || bad "shipped code in the desktop tool set missed a real .tar.gz (got '$r')"
r=$(run x "$desktop_path:/usr/bin:/bin" skd840n_tar_z staging/trial/flash-sysupgrade.bin)
[ -z "$r" ] || bad "shipped code misread the plain tar as '$r'"
ok "shipped code + desktop PATH: detects gzip and still reads the plain tar as plain"

# 3. THE DEFECT: old code in the board tool set cannot see a gzip
command -v od >/dev/null || bad "host od needed to build the desktop sandbox"
r=$(run x "$board_path" skd840n_old_tar_z "$T/real.tgz")
[ -z "$r" ] || bad "the old code unexpectedly detected gzip in the board tool set; the demo is invalid"
ok "NEGATIVE: old od version + board PATH (no od): returns empty for a real .tar.gz"

# 4. ...and it also misreports the plain tar, for the same reason, which is why the failure is
#    invisible until the build switches to gzip
r=$(run x "$board_path" skd840n_old_tar_z staging/trial/flash-sysupgrade.bin)
[ -z "$r" ] || bad "unexpected"
ok "NEGATIVE: old od version + board PATH: also 'correct' on the plain tar - bug is dormant today"

# 5. CONTRAST: the same old code works on a desktop, which is why it looked right
r=$(run x "$desktop_path" skd840n_old_tar_z "$T/real.tgz")
[ "$r" = z ] || bad "with od present the old code should detect gzip, got '$r' - demo invalid"
ok "CONTRAST: old od version + desktop PATH: works - the logic was never wrong, the dependency was"

# --- the shipped function must survive a missing file -----------------------------------------
r=$(skd840n_tar_z "$T/does-not-exist" 2>/dev/null)
[ -z "$r" ] || bad "missing file classified as '$r'"
ok "missing file -> empty, no error"

echo "all $pass checks passed"
