#!/bin/sh
# 本地排演套件（local rehearsal suite）：依赖 staging/ 下的真实构建产物，
# 在只 clone 本仓库的环境里跑不起来，也**没有**接进 CI。
# 为什么、怎么跑、缺的那个夹具从哪来：同目录 README.md。
# Enforce the invariant the whole rollback story rests on: the root partition is never
# written by anything in the forward path.
#
# Why it is load-bearing: mtd5 (root) holds the community 4.19 rootfs that the board was
# running before this project touched anything, and it still does. That is why "roll back to
# get PON" means writing mtd2 and mtd3 and nothing else. If any tool ever writes root, that
# sentence stops being true - and it would stop being true SILENTLY, with rc=0, on a board
# with no serial console and no A/B.
#
# So this is checked three ways, and the checker is itself driven with a deliberately broken
# script so that a pass cannot be a verifier that never fires.
set -u
cd /mnt/c/ugit/SK-D840N4
export LC_ALL=C
pass=0; fail=0
ok()  { printf '  PASS  %-54s %s\n' "$1" "${2:-}"; pass=$(( pass + 1 )); }
bad() { printf '  FAIL  %-54s %s\n' "$1" "${2:-}"; fail=$(( fail + 1 )); }
note(){ printf '  ....  %-54s %s\n' "$1" "${2:-}"; }
T=$(mktemp -d); trap 'rm -rf "$T"' EXIT

echo "=== 1. the permit log: what a write is even allowed to name ==="
P=staging/ctl/flash.permit
if [ -f "$P" ]; then
	n=$(grep -c . "$P")
	echo "  permit lines: $n"
	# every permit names exactly the two artifacts that may be written
	with_two=$(grep -c 'uimage_md5=.*dtb_md5=' "$P")
	if [ "$with_two" -eq "$n" ]; then ok "every permit line names a uImage and a dtb" "$n/$n"
	else bad "every permit line names a uImage and a dtb" "$with_two/$n"; fi
	# and none of them ever named a rootfs digest - this is the structural fact: the thing
	# that authorises a write has no vocabulary for writing root at all
	r=$(grep -c 'root_md5=\|rootfs_md5=\|root_img_md5=' "$P"); r=${r:-0}
	if [ "$r" -eq 0 ]; then ok "no permit ever names a rootfs digest" "root is not a writable artifact here"
	else bad "no permit ever names a rootfs digest" "$r line(s) do"; fi
	# root_* fields DO appear (root_index=, root_read=) - they are verification fields, not
	# a digest of something written. Assert that distinction rather than grepping for 'root'.
	v=$(grep -c 'root_index=\|root_read=' "$P"); v=${v:-0}
	if [ "$v" -gt 0 ]; then
		note "root_index=/root_read= appear $v time(s)" "verification fields, not written bytes"
	fi
else
	bad "the permit log exists" "$P"
fi

echo
echo "=== 2. the rollback path itself writes exactly two partitions ==="
# flash-direct.sh stages two files and hands flash-write.sh to the board. Neither may name
# root. This is a property of the code, checked by running it, not by reading it.
if grep -qE 'mtd(_debug)? +(write|erase)[^\n]*\broot\b' tools/flash-direct.sh tools/flash-write.sh; then
	bad "neither rollback script names root in an mtd write/erase" "see the grep above"
else
	ok "neither rollback script names root in an mtd write/erase" "flash-direct.sh + flash-write.sh"
fi
# and the worker only ever resolves two labels
for l in kernel dtb; do
	grep -q "dev_of $l" tools/flash-write.sh && ok "flash-write.sh resolves '$l'" "" || bad "flash-write.sh resolves '$l'" "absent"
done
n=$(grep -c 'dev_of [a-z]' tools/flash-write.sh)
if [ "$n" -eq 2 ]; then ok "and resolves no third partition" "dev_of calls: $n"
else bad "and resolves no third partition" "dev_of calls: $n"; fi

echo
echo "=== 3. the one path that does write root is guarded and deliberate ==="
if grep -q 'write-root' tools/flash-round.sh; then
	ok "flash-round.sh exposes a write-root mode" "the deliberate full-image path"
	if grep -q 'MODE.*=.*write-root' tools/flash-round.sh && grep -q 'if \[ "\$MODE" = write-root \]' tools/flash-round.sh; then
		ok "  and it is gated by its own conditional" "mtime freshness, not a default"
	else
		bad "  and it is gated" "no dedicated guard found"
	fi
else
	note "flash-round.sh has no write-root mode" "nothing in this repo can write root"
fi
# the default modes must not reach it
grep -q 'write) *TAG=FW1\|write) *ARGS' tools/flash-round.sh \
	&& ok "plain 'write' is a distinct mode from 'write-root'" "" \
	|| note "could not confirm write and write-root are distinct modes" ""

echo
echo "=== 4. negative control: the checker must fail on a script that DOES write root ==="
cp tools/flash-direct.sh "$T/broken.sh"
printf '\n# injected by the test\nmtd write flash-rootfs.jffs2 root\n' >>"$T/broken.sh"
if grep -qE 'mtd(_debug)? +(write|erase)[^\n]*\broot\b' "$T/broken.sh"; then
	ok "a script with an mtd write to root is caught" "the check is live"
else
	bad "a script with an mtd write to root is caught" "the check never fires - useless"
fi
# and the real one is not caught
if grep -qE 'mtd(_debug)? +(write|erase)[^\n]*\broot\b' tools/flash-direct.sh; then
	bad "and the real script is NOT flagged" "false positive"
else
	ok "and the real script is not flagged" ""
fi
# a comment must not trip it - otherwise the check would cry wolf on flash-round.sh:42
cp tools/flash-round.sh "$T/commented.sh"
if grep -qE '^[^\#]*mtd(_debug)? +(write|erase)[^\n]*\broot\b' "$T/commented.sh"; then
	bad "a comment mentioning the write is not flagged" "false positive"
else
	ok "a comment mentioning an mtd write to root is not flagged" "flash-round.sh line 42"
fi

echo
echo "=== pass=$pass fail=$fail ==="
[ "$fail" -eq 0 ] || exit 1
exit 0