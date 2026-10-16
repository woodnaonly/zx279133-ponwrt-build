#!/bin/sh
# 本地排演套件（local rehearsal suite）：依赖 staging/ 下的真实构建产物，
# 在只 clone 本仓库的环境里跑不起来，也**没有**接进 CI。
# 为什么、怎么跑、缺的那个夹具从哪来：同目录 README.md。
# Does the workflow still parse, and does the flash-set step still describe a consistent
# package? Editing YAML by hand is exactly where a tab or a bad indent silently costs a
# whole CI run, so parse it rather than eyeball it.
set -u
cd /mnt/c/ugit/SK-D840N4
export LC_ALL=C
WF=ci-build-repo/.github/workflows/build-sk-d840n.yml
pass=0; fail=0
ok()  { printf '  PASS  %-52s %s\n' "$1" "${2:-}"; pass=$((pass+1)); }
bad() { printf '  FAIL  %-52s %s\n' "$1" "${2:-}"; fail=$((fail+1)); }

echo "=== 1. YAML parses ==="
if python3 -c "
import yaml,sys
d=yaml.safe_load(open('$WF'))
steps=d['jobs'][list(d['jobs'])[0]]['steps']
print('steps=%d' % len(steps))
for s in steps:
    if 'name' in s: print('  -', s['name'])
" >/tmp/wf.out 2>/tmp/wf.err; then
	sed 's/^/  /' /tmp/wf.out
	ok "workflow parses and its steps enumerate" ""
else
	sed 's/^/  /' /tmp/wf.err
	bad "workflow parses" "python: $(head -1 /tmp/wf.err)"
fi

echo "=== 2. no tabs crept into the YAML ==="
t=$(tr -cd '\t' <"$WF" | wc -c)
if [ "$t" -eq 0 ]; then ok "no tab characters" ""
else bad "no tab characters" "$t tab bytes"; fi

echo
echo "=== 3. the flash-set step ships the manifest the flasher now looks for ==="
blk=$(sed -n '/- name: Package flash images/,/- name: "Gate: every dtb/p' "$WF")
if [ -z "$blk" ]; then
	bad "located the 'Package flash images' step" "the sed range matched nothing"
else
	ok "located the 'Package flash images' step" ""
printf '%s\n' "$blk" | grep -q 'sha256sums-release' \
	&& ok "manifest is generated" "" || bad "manifest is generated" ""
printf '%s\n' "$blk" | grep -q 'sha256sum -c sha256sums-release' \
	&& ok "manifest is self-verified before packing" "" || bad "manifest is self-verified" ""
printf '%s\n' "$blk" | grep -q 'tar xzf flash-set.tgz' \
	&& ok "the packed tarball is unpacked and re-verified" "" || bad "packed tarball re-verified" ""
# the cp is a backslash continuation, so the manifest and the destination land on the
# SECOND line - matching "cp .*sha256sums-release" would never fire
if printf '%s\n' "$blk" | tr -d '\\' | grep -q 'sha256sums-release[[:space:]]*flash-set/'; then
	ok "and it is copied into flash-set/" ""
else
	bad "and it is copied into flash-set/" "the flasher on the board would never see it"
fi
fi

echo
echo "=== 4. the release and the artifact both carry it ==="
tail -40 "$WF" | grep -q 'sha256sums-release' \
	&& ok "sha256sums-release appears in the release/artifact lists" "" \
	|| bad "sha256sums-release in the lists" "not published"

echo
echo "=== 5. the flasher and the manifest agree on the name ==="
grep -q 'sha256sums-release' ci-build-repo/templates/flash-sk-d840n.sh \
	&& ok "flasher looks for sha256sums-release" "" \
	|| bad "flasher looks for sha256sums-release" "name mismatch with the workflow"

echo
echo "=== 6. both edited shell files: syntax and line endings ==="
for f in ci-build-repo/templates/flash-sk-d840n.sh; do
	if sh -n "$f" 2>/dev/null; then ok "sh -n ${f##*/}" ""; else bad "sh -n ${f##*/}" ""; fi
	cr=$(tr -cd '\r' <"$f" | wc -c)
	[ "$cr" -eq 0 ] && ok "${f##*/} has no CR" "" || bad "${f##*/} has no CR" "$cr bytes"
done

echo
echo "=== pass=$pass fail=$fail ==="
[ "$fail" -eq 0 ] || exit 1
exit 0