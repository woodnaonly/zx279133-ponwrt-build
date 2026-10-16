#!/bin/sh
# 本地排演套件（local rehearsal suite）：依赖 staging/ 下的真实构建产物，
# 在只 clone 本仓库的环境里跑不起来，也**没有**接进 CI。
# 为什么、怎么跑、缺的那个夹具从哪来：同目录 README.md。
# Thin wrapper: the regression loop runs `sh "$t"`, and this suite is Python.
# The wrapper also states the precondition loudly rather than letting a missing
# kernelsrc/ look like a clean pass.
set -u
cd /mnt/c/ugit/SK-D840N4 || exit 1
D=staging/diag-20261014
for f in kernelsrc/drivers_net_phy_Kconfig \
         kernelsrc/drivers_net_phy_realtek_Kconfig \
         kernelsrc/drivers_leds_Kconfig \
         kernelsrc/drivers_gpio_Kconfig \
         kernelsrc/fs_pstore_Kconfig; do
	if [ ! -f "$D/$f" ]; then
		printf 'FAIL %s is missing - run: python3 %s/fetch_kernel_kconfig.py\n' \
			"$f" "$D"
		printf 'FAIL and: python3 %s/fetch_phy_kconfig.py\n' "$D"
		exit 1
	fi
done
exec python3 "$D/test_phy_gate_symbols.py"
