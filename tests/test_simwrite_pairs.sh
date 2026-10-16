#!/bin/sh
# 本地排演套件（local rehearsal suite）：依赖 staging/ 下的真实构建产物，
# 在只 clone 本仓库的环境里跑不起来，也**没有**接进 CI。
# 为什么、怎么跑、缺的那个夹具从哪来：同目录 README.md。
# simwrite.sh simulates the device-side worker against a fake flash. It must pass for
# BOTH image pairs, because they are different files:
#   staging/trial/     mainline 6.18 + mainline dtb   - the way forward
#   staging/rollback/  vendor 4.19   + vendor   dtb   - the way back
# Running only the first would leave the rollback path simulated against bytes nobody
# ever writes.
set -u
cd /mnt/c/ugit/SK-D840N4
export LC_ALL=C
pass=0; fail=0

for pair in staging/trial staging/rollback; do
	echo
	echo "############ $pair ############"
	if [ ! -f "$pair/flash-uImage" ]; then
		printf '  SKIP  %s has no flash-uImage\n' "$pair"
		continue
	fi
	printf '  kernel %s bytes, dtb %s bytes\n' \
		"$(wc -c <"$pair/flash-uImage")" "$(wc -c <"$pair/flash-dtb.bin")"
	if SRC="$PWD/$pair" sh ci-build-repo/tools/simwrite.sh >/tmp/sw.$$ 2>&1; then
		n_ok=$(grep -c '^  ok ' /tmp/sw.$$)
		n_bad=$(grep -c '^  FAIL ' /tmp/sw.$$)
		echo "  exit 0 | $n_ok ok, $n_bad FAIL"
		grep '^  FAIL ' /tmp/sw.$$ | sed 's/^/    /'
		pass=$(( pass + 1 ))
	else
		echo "  NONZERO EXIT"
		grep -E '^  FAIL |SIM FAIL' /tmp/sw.$$ | sed 's/^/    /'
		fail=$(( fail + 1 ))
	fi
	rm -f /tmp/sw.$$
done

echo
echo "=== $pair pass / $pair fail (of the pairs that exist) ==="
[ "$fail" -eq 0 ] || exit 1
exit 0