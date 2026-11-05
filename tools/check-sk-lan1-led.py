#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Check nine GPIO panel LEDs and the verified LAN1 PHY backend, not real lamps."""
import argparse
from pathlib import Path
import struct

from fdtwalk import FDT

BASE = Path(__file__).resolve().parents[1] / 'overlays/target/linux/zte/zx279133/base-files'
FILES = ('etc/board.d/01_leds', 'etc/uci-defaults/99-sk-lan1-led',
         'lib/functions/sk-d840n-leds.sh')
PLUGINS = ('zx279133-los', 'zx279133-pon', 'usbport')
KERNEL_SYMBOLS = ('LEDS_CLASS', 'LEDS_GPIO', 'GPIO_ZX279133', 'LEDS_TRIGGERS',
                  'LEDS_TRIGGER_NETDEV', 'LEDS_TRIGGER_DEFAULT_ON', 'USB_LEDS_TRIGGER_USBPORT')
# name: GPIO bank, offset, function, color, initial state, default trigger
PANEL = {
    **{f'skyworth:green:lan{i}': (4, i + 1, 'lan', 2, 'off', None) for i in range(2, 5)},
    'skyworth:green:power': (0, 10, 'power', 2, 'on', None),
    'skyworth:green:pon': (3, 15, 'wan', 2, 'off', 'zx279133-pon'),
    'skyworth:red:los': (0, 9, 'wan', 1, 'on', 'zx279133-los'),
    'skyworth:green:wan': (1, 8, 'lan', 2, 'off', None),
    'skyworth:green:usb1': (1, 5, 'usb', 2, 'off', None),
    'skyworth:green:phone1': (2, 3, 'phone', 2, 'off', None),
}
LAN1_NAME = 'skyworth:green:lan1'


def cells(raw):
    assert len(raw) % 4 == 0, 'unaligned DT cells'
    return struct.unpack('>' + 'I' * (len(raw) // 4), raw)


def validate_dtb(blob):
    nodes = FDT(blob).nodes
    bypath = {node['path']: node['props'] for node in nodes}
    handles = {}
    for node in nodes:
        for key in ('phandle', 'linux,phandle'):
            if key in node['props']:
                handles[cells(node['props'][key])[0]] = node
    assert b'skyworth,sk-d840n\0' in bypath['/']['compatible'], 'wrong board'
    led_nodes = [n for n in nodes if n['path'].startswith('/leds/')]
    assert len(led_nodes) == len(PANEL), 'wrong panel LED count (no WPS/WLAN on this SKU)'
    assert {n['props'].get('label', b'').rstrip(b'\0').decode() for n in led_nodes} == set(PANEL), 'missing/extra panel LEDs'
    all_panel = [n for n in nodes if n['props'].get('label', b'').startswith(b'skyworth:')]
    assert len(all_panel) == 10, 'nine GPIO plus exactly one PHY LED required'
    assert {n['props']['label'].rstrip(b'\0').decode() for n in all_panel} == {*PANEL, LAN1_NAME}, 'duplicate/unknown panel label'
    phy_leds = [n for n in all_panel if n['props']['label'] == (LAN1_NAME + '\0').encode()]
    assert len(phy_leds) == 1 and phy_leds[0]['path'].endswith('/port@8/leds/led@0'), 'LAN1 must be port-8 PHY channel zero'
    phy = phy_leds[0]['props']
    assert cells(phy['reg']) == (0,) and 'gpios' not in phy, 'LAN1 must not use GPIO4_2'
    assert phy.get('default-state') == b'off\0' and phy.get('function') == b'lan\0', 'wrong LAN1 initial state/function'
    assert phy.get('color') == struct.pack('>I', 2) and 'linux,default-trigger' not in phy, 'wrong LAN1 color/trigger'
    phy_parent = bypath[phy_leds[0]['path'].rsplit('/', 1)[0]]
    assert cells(phy_parent['#address-cells']) == (1,) and cells(phy_parent['#size-cells']) == (0,), 'invalid PHY LED bus cells'
    owners = {}
    for candidate in nodes:
        for key, raw in candidate['props'].items():
            if key != 'gpios' and not key.endswith('-gpios'):
                continue
            values = cells(raw)
            i = 0
            while i < len(values):
                controller = handles.get(values[i])
                assert controller is not None, 'unresolved GPIO controller'
                count, = cells(controller['props']['#gpio-cells'])
                assert i + count < len(values), 'truncated GPIO specifier'
                owners.setdefault((values[i], values[i + 1]), []).append((candidate['path'], key))
                i += count + 1
    gpio4 = [handle for handle, node in handles.items()
             if node['props'].get('reg') == struct.pack('>4I', 0, 0x10d10100, 0, 0x40)]
    assert len(gpio4) == 1 and (gpio4[0], 2) not in owners, 'stale GPIO4_2 LAN1 consumer'
    for node in led_nodes:
        props = node['props']
        name = props['label'].rstrip(b'\0').decode()
        bank, offset, function, color, state, trigger = PANEL[name]
        assert bypath[node['path'].rsplit('/', 1)[0]]['compatible'] == b'gpio-leds\0'
        assert props.get('default-state') == state.encode() + b'\0', f'{name}: wrong initial state'
        assert props.get('color') == struct.pack('>I', color), f'{name}: wrong color'
        assert props.get('function') == function.encode() + b'\0', f'{name}: wrong function'
        assert props.get('linux,default-trigger') == (trigger.encode() + b'\0' if trigger else None), f'{name}: wrong trigger'
        handle, pin, flags = cells(props['gpios'])
        assert (pin, flags) == (offset, 1), f'{name}: wrong pin/polarity'
        gpio = handles[handle]['props']
        assert b'zte,zx279133-gpio\0' in gpio.get('compatible', b''), f'{name}: unsupported GPIO controller'
        assert gpio.get('status', b'okay\0') in (b'okay\0', b'ok\0'), f'{name}: disabled GPIO controller'
        assert cells(gpio['reg']) == (0, 0x10d10000 + bank * 0x40, 0, 0x40), f'{name}: wrong bank'
        assert cells(gpio['ngpios']) == ((6 if bank == 4 else 16),), f'{name}: wrong bank size'
        assert owners[(handle, pin)] == [(node['path'], 'gpios')], f'{name}: GPIO has another consumer'
        if bank == 4:
            ranges = cells(gpio['gpio-ranges'])
            mapped = []
            for i in range(0, len(ranges), 4):
                controller, first, base, count = ranges[i:i + 4]
                if first <= pin < first + count:
                    assert b'zte,zx279133-pinctrl\0' in handles[controller]['props']['compatible']
                    mapped.append(base + pin - first)
            assert mapped == [60 + offset], f'{name}: wrong pinctrl mapping'
    for i, port in ((1, 8), (2, 5), (3, 6), (4, 7)):
        ports = [n for n in nodes if n['path'].endswith(f'/port@{port}') and
                 n['props'].get('label') == f'lan{i}\0'.encode() and
                 cells(n['props']['reg']) == (port,)]
        assert len(ports) == 1, f'DSA port {port} must remain lan{i}'
    nppt, = [n['props'] for n in nodes if b'zte,zx279133-nppt\0' in n['props'].get('compatible', b'')]
    handle, pin, flags = cells(nppt['opsd-gpios'])
    assert cells(handles[handle]['props']['reg'])[1] == 0x10d10080 and (pin, flags) == (1, 0), 'optical RX_SD wiring changed'
    assert 'leds' not in nppt and 'led-names' not in nppt, 'stale direct LED ownership'
    print('PASS: nine GPIO LEDs plus unique port-8 PHY LAN1, ownership, triggers and DSA mapping; real lamps require observation')


def validate_root(root):
    for name in FILES:
        path = root / name
        assert path.is_file(), f'image missing {name}'
        assert path.read_bytes() == (BASE / name).read_bytes(), f'stale image file: {name}'
        if name.startswith('etc/'):
            assert path.stat().st_mode & 0o111, f'image file is not executable: {name}'
    assert (root / 'etc/init.d/led').is_file(), 'image missing standard LED service'
    assert (root / 'www/luci-static/resources/view/system/leds.js').is_file(), 'image missing LuCI system LED page'
    for plugin in PLUGINS:
        path = root / f'www/luci-static/resources/view/system/led-trigger/{plugin}.js'
        # LuCI may minify JS at image assembly, so raw source equality is wrong.
        assert path.is_file() and b'addFormOptions' in path.read_bytes(), f'missing LED plugin: {plugin}'
    print('PASS: image contains shared defaults/migration, LED service, LuCI LED page and all 3 plugins')


def validate_fit(blob):
    images = [node for node in FDT(blob).nodes if node['path'].startswith('/images/')
              and node['props'].get('type') == b'flat_dt\0']
    assert len(images) == 1, 'expected one inline board DTB in the released FIT'
    validate_dtb(images[0]['props']['data'])
    print('PASS: complete panel wiring is embedded in the released initramfs FIT')


def validate_config(path):
    config = set(path.read_text(encoding='utf-8').splitlines())
    for symbol in KERNEL_SYMBOLS:
        assert f'CONFIG_{symbol}=y' in config, f'final kernel must build in {symbol}'
    print('PASS: shipped kernel has built-in GPIO LEDs, netdev/default-on and USB port triggers')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dtb', type=Path)
    parser.add_argument('--root', type=Path)
    parser.add_argument('--fit', type=Path)
    parser.add_argument('--kernel-config', type=Path)
    args = parser.parse_args()
    if not any((args.dtb, args.fit, args.root, args.kernel_config)):
        parser.error('supply a compiled DTB, image root or final kernel config')
    if args.dtb:
        validate_dtb(args.dtb.read_bytes())
    if args.root:
        validate_root(args.root)
    if args.fit:
        validate_fit(args.fit.read_bytes())
    if args.kernel_config:
        validate_config(args.kernel_config)
