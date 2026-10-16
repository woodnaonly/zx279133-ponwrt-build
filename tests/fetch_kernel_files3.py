#!/usr/bin/env python3
# 本地排演套件（local rehearsal suite）：依赖 staging/ 下的真实构建产物，
# 在只 clone 本仓库的环境里跑不起来，也**没有**接进 CI。
# 为什么、怎么跑、缺的那个夹具从哪来：同目录 README.md。
"""Second fetch round: who registers topcrm as a syscon, and what the inline lookup does."""
import os
import urllib.request
from concurrent.futures import ThreadPoolExecutor

REPO = 'cnjn/linux-mainline-zte-zxslc-sr1010'
BR = 'codex/sr1010-mainline'
RAW = 'https://raw.githubusercontent.com/%s/%s/' % (REPO, BR)
DST = '/mnt/c/ugit/SK-D840N4/staging/diag-20261014/kernelsrc'

FILES = [
    'drivers/clk/clk-zx279133.c',
    'include/linux/mfd/syscon.h',
    'drivers/mfd/syscon.c',
    'drivers/mfd/Kconfig',
    'drivers/mfd/Makefile',
    'drivers/net/ethernet/zte/zx279133-np.c',
    'arch/arm64/boot/dts/zte/zx279133-sr1010.dts',
    'Documentation/devicetree/bindings/soc/zte/zte,zx279133-pon-route.yaml',
    'Documentation/devicetree/bindings/soc/zte/zte,zx279133-idm-cci.yaml',
    'Documentation/devicetree/bindings/net/zte,zx279133-nppt.yaml',
]


def get(path):
    dest = os.path.join(DST, path.replace('/', '_'))
    last = ''
    for _ in range(4):
        try:
            with urllib.request.urlopen(RAW + path, timeout=120) as r:
                data = r.read()
                code = r.status
            if code == 200 and data:
                with open(dest, 'wb') as f:
                    f.write(data)
                return '%3d  %8d  %s' % (code, len(data), path)
            last = 'http %s, %d bytes' % (code, len(data))
        except Exception as exc:                       # noqa: BLE001
            last = '%s: %s' % (type(exc).__name__, exc)
    return '%3s  %8d  %s   [%s]' % ('ERR', 0, path, last)


os.makedirs(DST, exist_ok=True)
with ThreadPoolExecutor(max_workers=5) as pool:
    for line in pool.map(get, FILES):
        print(line, flush=True)
print('== done ==')
