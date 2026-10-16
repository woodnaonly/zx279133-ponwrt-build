#!/bin/sh
# 本地排演套件（local rehearsal suite）：依赖 staging/ 下的真实构建产物，
# 在只 clone 本仓库的环境里跑不起来，也**没有**接进 CI。
# 为什么、怎么跑、缺的那个夹具从哪来：同目录 README.md。
# Wrapper for two reasons: the regression loop runs `sh "$t"`, and this suite now also has to
# prove that all THREE workflows still parse - two of them were edited when the kernel
# version was collapsed into a single source, and nothing else was checking their YAML.
set -u
cd /mnt/c/ugit/SK-D840N4 || exit 1
W=ci-build-repo/.github/workflows

echo "=== all three workflows must still parse ==="
for f in build-sk-d840n.yml build-sr1010.yml diagnose-config.yml; do
	if python3 -c "
import sys, yaml
d = yaml.safe_load(open('$W/$f', encoding='utf-8'))
assert d.get('jobs'), 'no jobs in $f'
print('  ok   $f  (%d job(s))' % len(d['jobs']))
" 2>/tmp/y.err; then :; else
		printf 'FAIL %s does not parse as YAML\n' "$f"
		cat /tmp/y.err
		exit 1
	fi
done

echo
exec python3 staging/diag-20261014/test_kernel_version_single_source.py
