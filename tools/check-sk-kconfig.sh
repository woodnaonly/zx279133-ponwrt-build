#!/bin/sh
# SPDX-License-Identifier: GPL-2.0-only
# Parse the actual prepared kernel with its own Kconfig implementation before
# spending time building the cross-toolchain. No target code is compiled here.
set -eu

tree=$(cd "$1" && pwd)
scratch=$(mktemp -d)
trap 'rm -rf "$scratch"' EXIT HUP INT TERM

# Host GCC is deliberate: allnoconfig only builds/runs the host conf parser.
# It does not require the not-yet-built AArch64 toolchain or a newer host Clang.
if make -C "$tree" O="$scratch/kernel" ARCH=arm64 CC=gcc LD=ld HOSTCC=gcc \
	allnoconfig >"$scratch/kconfig.log" 2>&1; then
	cat "$scratch/kconfig.log"
else
	cat "$scratch/kconfig.log" >&2
	# Keep the complete dependency chain readable through public annotations.
	python3 - "$scratch/kconfig.log" <<'PY'
from pathlib import Path
import sys
text = Path(sys.argv[1]).read_text(encoding='utf-8', errors='replace')[-16000:]
text = text.replace('%', '%25').replace('\r', '').replace('\n', '%0A')
print('::error::Native kernel Kconfig preflight failed:%0A' + text)
PY
	exit 1
fi

python3 "$(dirname "$0")/../tests/test_sk_led_kconfig.py" \
	--conf "$scratch/kernel/scripts/kconfig/conf"
