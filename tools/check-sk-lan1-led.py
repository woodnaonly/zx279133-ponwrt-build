#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Check the compiled LAN1 wiring, shipped defaults and final kernel config."""
import argparse
from pathlib import Path
import struct

from fdtwalk import FDT

BASE = Path(__file__).resolve().parents[1] / 'overlays/target/linux/zte/zx279133/base-files'
FILES = ('etc/board.d/01_leds', 'etc/uci-defaults/99-sk-lan1-led')
LABEL = b'skyworth:green:lan1\0'


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
    matches = [n for n in nodes if n['props'].get('label') == LABEL]
    assert len(matches) == 1, 'LAN1 LED missing or declared twice'
    node = matches[0]
    props = node['props']
    assert bypath[node['path'].rsplit('/', 1)[0]]['compatible'] == b'gpio-leds\0'
    assert props.get('default-state') == b'off\0', 'LAN1 must start off'
    assert props.get('color') == struct.pack('>I', 2), 'LAN1 must be green'
    assert props.get('function') == b'lan\0', 'LAN1 must be a LAN LED'
    handle, offset, flags = cells(props['gpios'])
    assert offset == 2 and flags == 1, 'LAN1 must use GPIO4 pin 2, active-low'
    gpio = handles[handle]
    assert cells(gpio['props']['reg']) == (0, 0x10d10100, 0, 0x40), 'wrong GPIO bank'
    assert cells(gpio['props']['ngpios']) == (6,), 'unexpected GPIO4 size'
    ranges = cells(gpio['props']['gpio-ranges'])
    mapped = []
    for i in range(0, len(ranges), 4):
        controller, first, pin, count = ranges[i:i + 4]
        if first <= offset < first + count:
            assert b'zte,zx279133-pinctrl\0' in handles[controller]['props']['compatible']
            mapped.append(pin + offset - first)
    assert mapped == [62], 'GPIO4 pin 2 must map to pinctrl pin 62 (p4-6)'
    owners = []
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
                if values[i] == handle and values[i + 1] == offset:
                    owners.append((candidate['path'], key))
                i += count + 1
    assert owners == [(node['path'], 'gpios')], 'LAN1 GPIO has another consumer'
    ports = [n for n in nodes if n['path'].endswith('/port@8') and
             n['props'].get('label') == b'lan1\0']
    assert len(ports) == 1, 'physical DSA port 8 must remain lan1'
    print('PASS: compiled LAN1 DTB: GPIO4.2/p4-6, active-low, unique owner, DSA lan1 unchanged')


def validate_root(root):
    for name in FILES:
        path = root / name
        assert path.is_file(), f'image missing {name}'
        assert path.read_bytes() == (BASE / name).read_bytes(), f'stale image file: {name}'
        assert path.stat().st_mode & 0o111, f'image file is not executable: {name}'
    assert (root / 'etc/init.d/led').is_file(), 'image missing standard LED service'
    print('PASS: image contains executable LAN1 defaults/migration and standard LED service')


def validate_fit(blob):
    images = [node for node in FDT(blob).nodes if node['path'].startswith('/images/')
              and node['props'].get('type') == b'flat_dt\0']
    assert len(images) == 1, 'expected one inline board DTB in the released FIT'
    validate_dtb(images[0]['props']['data'])
    print('PASS: LAN1 wiring is in the DTB embedded in the released initramfs FIT')


def validate_config(path):
    config = set(path.read_text(encoding='utf-8').splitlines())
    for symbol in ('LEDS_GPIO', 'GPIO_ZX279133', 'LEDS_TRIGGERS', 'LEDS_TRIGGER_NETDEV'):
        assert f'CONFIG_{symbol}=y' in config, f'final kernel must build in {symbol}'
    print('PASS: shipped kernel has built-in GPIO LED and netdev trigger support')


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
