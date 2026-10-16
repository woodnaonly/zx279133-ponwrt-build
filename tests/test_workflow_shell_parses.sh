#!/bin/sh
# 本地排演套件（local rehearsal suite）：依赖 staging/ 下的真实构建产物，
# 在只 clone 本仓库的环境里跑不起来，也**没有**接进 CI。
# 为什么、怎么跑、缺的那个夹具从哪来：同目录 README.md。
# Wrapper: the regression loop runs `sh "$t"`, and this suite is python.
#
# Why it exists: check_workflow_yaml.py proved the workflows are valid YAML, and
# test_gate_endtoend.sh extracts only the *verdict* half of the driver gate. Neither can
# see that the driver gate's `run:` block does not parse as shell, because its awk program
# is wrapped in shell single quotes and a comment inside that program contained an
# apostrophe. The gate had never executed. YAML validity and shell validity are different
# properties, and only the second one decides whether a step runs.
set -u
cd /mnt/c/ugit/SK-D840N4 || exit 1

echo "=== the driver gate step must be shell, not merely YAML ==="
exec python3 staging/diag-20261014/test_workflow_shell_parses.py
