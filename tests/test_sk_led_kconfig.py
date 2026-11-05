#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Use Linux's real conf parser to check the switch/LED tristate contract."""
import argparse
from pathlib import Path
import re
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
OVERLAY = ROOT / 'kernel-overlays/drivers/net/ethernet/zte/Kconfig'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--conf', type=Path, required=True)
    args = parser.parse_args()
    conf = args.conf.resolve()
    overlay = OVERLAY.read_text(encoding='utf-8')
    switch = re.search(r'^config ZTE_ZX279133_SWITCH\n(.*?)(?=^config |\Z)',
                       overlay, re.M | re.S).group(1)
    assert '\tdepends on LEDS_CLASS\n' in switch
    assert '\tselect LEDS_CLASS\n' not in switch
    configure = (ROOT / 'scripts/configure-sk-d840n.sh').read_text(encoding='utf-8')
    for name in ('NEW_LEDS', 'LEDS_CLASS', 'ZTE_ZX279133_SWITCH'):
        assert re.search(r'^pin_y ' + name + r'$', configure, re.M), name

    with tempfile.TemporaryDirectory(prefix='sk-led-kconfig-') as tmp:
        work = Path(tmp)
        # These are only the platform inputs; the complete driver definition
        # is the real overlay, not a duplicate of its dependency expression.
        prefix = 'config MODULES\n\tbool "modules"\n\tmodules\n'
        for name in ('OF', 'HAS_IOMEM', 'HAS_DMA', 'COMMON_CLK_ZX279133',
                     'PHY_ZX279133_PON_SERDES', 'PHY_ZX279133_UNI_SERDES',
                     'PCS_XPCS', 'NET_DSA', 'NEW_LEDS'):
            prefix += f'config {name}\n\tbool "{name}"\n\tdefault y\n'
        prefix += ('if NEW_LEDS\nconfig LEDS_CLASS\n\ttristate "LED class"\n'
                   'endif\n')
        (work / 'Kconfig').write_text(prefix + overlay, encoding='utf-8', newline='\n')
        for leds, requested, expected in (('y', 'y', 'y'), ('m', 'y', 'm'),
                                          ('m', 'm', 'm'), ('n', 'y', 'n')):
            (work / '.config').write_text(
                'CONFIG_MODULES=y\nCONFIG_NET_VENDOR_ZTE=y\n'
                'CONFIG_ZTE_ZX279133_ETH=y\n'
                f'CONFIG_LEDS_CLASS={leds}\n'
                f'CONFIG_ZTE_ZX279133_SWITCH={requested}\n',
                encoding='utf-8', newline='\n')
            result = subprocess.run([str(conf), '--olddefconfig', 'Kconfig'],
                                    cwd=work, capture_output=True, text=True, timeout=30)
            assert result.returncode == 0, result.stdout + result.stderr
            assert 'unmet direct dependencies' not in result.stderr, result.stderr
            output = (work / '.config').read_text(encoding='utf-8')
            symbol = 'CONFIG_ZTE_ZX279133_SWITCH'
            actual = re.search(r'^' + symbol + r'=([ym])$', output, re.M)
            actual = actual.group(1) if actual else 'n'
            assert actual == expected, (leds, requested, expected, actual, output)
            class_value = re.search(r'^CONFIG_LEDS_CLASS=([ym])$', output, re.M)
            actual_class = class_value.group(1) if class_value else 'n'
            assert actual_class == leds, (leds, actual_class)
            print(f'PASS native Kconfig: LEDS_CLASS={leds}, switch={requested} -> {actual}')


if __name__ == '__main__':
    main()
