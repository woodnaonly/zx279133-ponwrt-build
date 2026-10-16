#!/bin/sh
# 回归套件：check-imagelineage.sh 的三个默认值必须真的指得到东西。
#
# 这个 bug 我修过一次又退了回来。docs/07 补四记的是它的上一版：overlay 根写成
# `ci-build-repo/overlays`（高了一级），于是找不到对照物，打印「skipped」然后报
# **OK** —— 什么都没比却显示通过。那一版更坏，因为它绿了。
#
# 2026-10-16 实测的这一版方向相反但同样错：第 55 行的 cd 已经把人带进
# ci-build-repo 里面，57–58 行的默认值却还写着 `ci-build-repo/...`，于是去找
# ci-build-repo/ci-build-repo/...；而 ROOT 默认值 `staging/trial/...` 也找不到，
# 因为仓库里根本没有 staging/，暂存集在工作区上一层。两处都编成了相对路径，
# 而两个 cd 回退落在**不同**的目录里。
#
# 后果不是「没检查」，是**理由错误的拒绝**：照 HANDOFF.md 第 4d 步那条无参数命令
# 跑，出来的是三条编造的 REFUSE，而这个 set 真正的问题（镜像里没有
# etc/dropbear/authorized_keys，所以它早于那笔 overlay 提交）被埋在下面。
# 拒绝本身是安全的，但一个没人能照着改的拒绝等于没有门。
#
# 每一条都带反向对照。「字符串不见了」只有在「该拒的仍然拒」的前提下才算数，
# 否则也可能只是检查器没跑。
#
# 依赖说明（重要）：templates 那一层的检查只在 ROOT 是**真文件**时才会跑到——
# ROOT 不是文件时 layer 2 直接打印 NOT APPLICABLE 就退出了。所以下面凡是查
# templates 的断言都必须喂一个真镜像进去。找不到镜像时，这里打印 NOTE 并跳过
# 那两条，**不会**拿一个空转的 PASS 充数。
set -u
d=$(cd "$(dirname "$0")" && pwd)
REPO=$(cd "$d/.." && pwd)
T=$REPO/tools/check-imagelineage.sh
LC_ALL=C; export LC_ALL

rc=0
pass() { echo "  PASS  $1"; }
fail() { echo "  FAIL  $1"; rc=1; }
note() { echo "  NOTE  $1"; }

[ -f "$T" ] || { echo "  FAIL  cannot find $T"; exit 1; }

# 一个能真的被 jefferson 读出来的镜像。找得到就用，找不到就明说。
IMG=
for c in "$d/fixtures/stale-set/flash-rootfs.jffs2" \
         "$REPO/../staging/trial/flash-rootfs.jffs2"; do
	[ -f "$c" ] && { IMG=$c; break; }
done

# 1. 无参数调用时，它报的路径必须是绝对的。
#    绝对 = 从任意 cwd 调用都指向同一个文件；相对 = 只在某一个 cwd 下碰巧对。
out=$(sh "$T" 2>&1)
hdr=$(printf '%s\n' "$out" | sed -n '1s/^=== image lineage: //p')
case "$hdr" in
	/*) pass "no-arg run resolves ROOT to an absolute path: $hdr" ;;
	'')  fail "the no-arg run printed no lineage header at all - it died before resolving anything" ;;
	*)   fail "the no-arg run still reports a RELATIVE root '$hdr', so the verdict depends on cwd" ;;
esac

# 2. overlay 默认值。overlay 那层的检查在 jefferson 之前，所以不需要镜像。
if printf '%s\n' "$out" | grep -qF 'overlay dir ci-build-repo/overlays'; then
	fail "still emits the bogus overlay path 'overlay dir ci-build-repo/overlays'"
else
	pass "no longer emits the bogus overlay path"
fi

# 3. 反向对照 A：overlay 目录真的不存在时，仍然必须拒绝，而且要点名那个路径。
#    没有这一条，第 2 条「字符串不见了」可能只是比较整个没跑。
b=$(sh "$T" /nonexistent/root.jffs2 /nonexistent/ovl 2>&1); brc=$?
if [ "$brc" -ne 0 ] && printf '%s\n' "$b" | grep -qF 'overlay dir /nonexistent/ovl does not exist'; then
	pass "reverse: a genuinely missing overlay dir still refuses and names it (exit $brc)"
else
	fail "reverse: a missing overlay dir did NOT produce the expected refusal (exit $brc)"
fi

# 4/5. templates 那一层：需要真镜像，否则 layer 2 压根不会跑。
if [ -z "$IMG" ]; then
	note "no jffs2 image found - the two templates assertions were NOT run."
	note "  looked for: $d/fixtures/stale-set/flash-rootfs.jffs2"
	note "           and $REPO/../staging/trial/flash-rootfs.jffs2"
	note "  build one with round36-make-fixture.sh, or stage a release, then re-run."
else
	note "using image $IMG"

	# 4. 默认 templates 路径不能再吐那条编造的名字。
	o2=$(sh "$T" "$IMG" 2>&1)
	if printf '%s\n' "$o2" | grep -qF 'templates dir ci-build-repo/templates'; then
		fail "still emits the bogus templates path 'templates dir ci-build-repo/templates'"
	else
		pass "no longer emits the bogus templates path"
	fi

	# 5. 反向对照 B：templates 目录真的不存在时，仍然必须拒绝并点名。
	c=$(TEMPLATES=/nonexistent/tpl sh "$T" "$IMG" 2>&1); crc=$?
	if [ "$crc" -ne 0 ] && printf '%s\n' "$c" | grep -qF 'templates dir /nonexistent/tpl does not exist'; then
		pass "reverse: a genuinely missing templates dir still refuses and names it (exit $crc)"
	else
		fail "reverse: a missing templates dir did NOT produce the expected refusal (exit $crc)"
		printf '%s\n' "$c" | tail -8 | sed 's/^/        /'
	fi
fi

# 6. 反向对照 C：REPO 覆盖成一个不存在的目录时必须回退，而不是改用调用者的坏路径。
e=$(REPO=/nonexistent sh "$T" /nonexistent/root.jffs2 2>&1)
if printf '%s\n' "$e" | grep -qF 'overlay dir'; then
	fail "REPO=/nonexistent was trusted instead of falling back to the real tree"
	printf '%s\n' "$e" | grep -E 'REFUSE' | sed 's/^/        /'
else
	pass "REPO=/nonexistent falls back to the real tree instead of using the bad path"
fi

exit $rc
