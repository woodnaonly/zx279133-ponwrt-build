#!/bin/sh
# 本地排演套件（local rehearsal suite）：不依赖 staging/ 的任何产物，纯 clone 就能跑。
# 为什么、怎么跑、缺的那个夹具从哪来：同目录 README.md。
# Wrapper: the regression loop runs `sh "$t"`, and this suite is python.
#
# Deliberately NOT `cd /mnt/c/ugit/SK-D840N4`: this suite builds its own FIT and cpio in
# memory, so it has nothing to be standing next to. Anchoring on the script's own
# directory is what keeps it honest on the third machine as well as the first two.
set -u
d=$(cd "$(dirname "$0")" && pwd)

echo "=== the kit-content reader must parse fixtures it can also prove wrong ==="
if python3 -c 'import sys' >/dev/null 2>&1; then
	exec python3 "$d/test_itbcheck_parsers.py"
fi
exec python "$d/test_itbcheck_parsers.py"
