#!/usr/bin/env python3
# 本地排演套件（local rehearsal suite）：依赖 staging/ 下的真实构建产物，
# 在只 clone 本仓库的环境里跑不起来，也**没有**接进 CI。
# 为什么、怎么跑、缺的那个夹具从哪来：同目录 README.md。
"""Parallel fetch of the zx279133 network/PON sources from the prepared kernel tree.

Sequential curl was ~2 minutes per file on this link; 6 threads gets it done in one go.
Prints one line per file with the HTTP status and the byte count actually written,
so a 0-byte or error body can never be mistaken for a file that was fetched.
"""
import os
import sys
import urllib.request
import urllib.error
from concurrent.futures import ThreadPoolExecutor

REPO = 'cnjn/linux-mainline-zte-zxslc-sr1010'
BR = 'codex/sr1010-mainline'
RAW = 'https://raw.githubusercontent.com/%s/%s/' % (REPO, BR)
DST = '/mnt/c/ugit/SK-D840N4/staging/diag-20261014/kernelsrc'

FILES = [
    'drivers/net/ethernet/zte/zx279133-np.c',
    'drivers/net/ethernet/zte/zx279133-idm.c',
    'drivers/net/ethernet/zte/zx279133-main.c',
    'drivers/net/ethernet/zte/zx279133-mac.c',
    'drivers/net/ethernet/zte/zx279133-netdev.c',
    'drivers/net/ethernet/zte/zx279133.h',
    'drivers/phy/zte/phy-zx279133-pon-serdes.c',
    'drivers/phy/zte/phy-zx279133-uni-serdes.c',
    'drivers/phy/zte/Makefile',
    'drivers/phy/zte/Kconfig',
    'drivers/soc/zte/Makefile',
    'drivers/soc/zte/Kconfig',
    'drivers/soc/zte/zx279133-xpcs-nppt-audit.c',
    'Documentation/ABI/testing/sysfs-firmware-zx279133',
    'Documentation/devicetree/bindings/net/zte,zx279133-nppt.yaml',
    'Documentation/devicetree/bindings/phy/zte,zx279133-pon-serdes.yaml',
    'Documentation/devicetree/bindings/soc/zte/zte,zx279133-pon-route.yaml',
    'Documentation/devicetree/bindings/soc/zte/zte,zx279133-idm-cci.yaml',
    'Documentation/devicetree/bindings/soc/zte/zte,zx279133-xpcs-nppt-audit.yaml',
    'arch/arm64/boot/dts/zte/zx279133.dtsi',
    'arch/arm64/boot/dts/zte/zx279133-sr1010.dts',
]


def get(path):
    dest = os.path.join(DST, path.replace('/', '_'))
    last = ''
    for attempt in range(4):
        try:
            with urllib.request.urlopen(RAW + path, timeout=120) as r:
                data = r.read()
                code = r.status
            if code == 200 and data:
                with open(dest, 'wb') as f:
                    f.write(data)
                return '%3d  %8d  %s' % (code, len(data), path)
            last = 'http %s, %d bytes' % (code, len(data))
        except Exception as exc:                       # noqa: BLE001 - report, do not hide
            last = '%s: %s' % (type(exc).__name__, exc)
    with open(dest, 'wb') as f:
        f.write(b'')
    return '%3s  %8d  %s   [%s]' % ('ERR', 0, path, last)


def main():
    os.makedirs(DST, exist_ok=True)
    with ThreadPoolExecutor(max_workers=6) as pool:
        for line in pool.map(get, FILES):
            print(line, flush=True)
    print('== done ==')


if __name__ == '__main__':
    sys.exit(main())
