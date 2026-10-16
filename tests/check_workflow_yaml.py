#!/usr/bin/env python3
# 本地排演套件（local rehearsal suite）：依赖 staging/ 下的真实构建产物，
# 在只 clone 本仓库的环境里跑不起来，也**没有**接进 CI。
# 为什么、怎么跑、缺的那个夹具从哪来：同目录 README.md。
"""Parse the release workflow so a YAML edit cannot ship broken.

Also prints the step count and the names of the two gates, so a silently mangled
`run:` block cannot pass as "still valid YAML".
"""
import sys

import yaml

WF = '/mnt/c/ugit/SK-D840N4/ci-build-repo/.github/workflows/build-sk-d840n.yml'

doc = yaml.safe_load(open(WF, encoding='utf-8'))
jobs = doc.get('jobs', {})
print('YAML OK')
print('jobs:', list(jobs.keys()))
total = 0
names = []
for jn, job in jobs.items():
    steps = job.get('steps') or []
    total += len(steps)
    for s in steps:
        n = s.get('name') or s.get('uses') or '?'
        names.append('%s :: %s' % (jn, n))
print('total steps:', total)
print('--- steps mentioning a gate ---')
for n in names:
    low = n.lower()
    if 'gate' in low or 'driver' in low or 'rollback' in low or 'manifest' in low:
        print(' ', n)
sys.exit(0)
