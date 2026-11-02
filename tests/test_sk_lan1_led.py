#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""LAN1 default/migration tests; --root also runs the shipped libuci under QEMU."""
import argparse
import copy
import importlib.util
import os
from pathlib import Path
import shlex
import shutil
import struct
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
spec = importlib.util.spec_from_file_location('led_gate', ROOT / 'tools/check-sk-lan1-led.py')
gate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gate)
BASE = gate.BASE
MIGRATION = BASE / gate.FILES[1]
DEFAULT = {'name': 'LAN1', 'sysfs': 'skyworth:green:lan1', 'trigger': 'netdev',
           'dev': 'lan1', 'mode': 'link tx rx', 'default': '0'}
# OpenWrt's shell library uses `export -n`, supported by ash/bash but not dash
# (Ubuntu's /bin/sh). The target UCI executable and library still run under QEMU.
SHELL = r'C:\Program Files\Git\bin\bash.exe' if os.name == 'nt' else shutil.which('bash')


def run_script(source, directory, library, env=None):
    (directory / 'functions.sh').write_text(library, encoding='utf-8', newline='\n')
    source = source.replace('. /lib/functions.sh', '. ' + shlex.quote((directory / 'functions.sh').as_posix()))
    source = source.replace('. /lib/functions/uci-defaults.sh',
                            '. ' + shlex.quote((directory / 'functions.sh').as_posix()))
    script = directory / 'run.sh'
    script.write_text(source, encoding='utf-8', newline='\n')
    return subprocess.run([SHELL, str(script)], cwd=directory,
                          env={**os.environ, 'IPKG_INSTROOT': '', 'PKG_ROOT': '',
                               'UCI_CONFIG_DIR': '', **(env or {})},
                          capture_output=True, text=True, timeout=30)


def board_tests():
    source = (BASE / gate.FILES[0]).read_text(encoding='utf-8')
    with tempfile.TemporaryDirectory(prefix='sk-led-board-') as tmp:
        directory = Path(tmp)
        library = '''board_name() { printf '%s\\n' "$LED_BOARD"; }
board_config_update() { :; }
board_config_flush() { :; }
ucidef_set_led_netdev() { printf '%s|%s|%s|%s|%s\\n' "$1" "$2" "$3" "$4" "$5"; }
'''
        for board, expected in [
            ('skyworth,sk-d840n', ['lan1|LAN1|skyworth:green:lan1|lan1|link tx rx']),
            ('zte,zx279133-sr1010', ['wan|WAN|sr1010:wan|eth0|link'] +
             [f'lan{i}|LAN{i}|sr1010:lan{i}|lan{i}|link' for i in range(1, 5)]),
            ('unrelated,board', []),
        ]:
            result = run_script(source, directory, library, {'LED_BOARD': board})
            assert result.returncode == 0, result.stderr
            assert result.stdout.splitlines() == expected, result.stdout
    print('PASS: board defaults bind only SK-D840N LAN1; upstream SR1010 defaults preserved')


def migration_tests():
    """Test decision branches with isolated callbacks; real UCI is tested below."""
    source = MIGRATION.read_text(encoding='utf-8')
    for sections, board, load_ok, instroot, should_add, status in [
        ([], 'skyworth,sk-d840n', True, '', True, 0),
        ([DEFAULT], 'skyworth,sk-d840n', True, '', False, 0),
        ([{**DEFAULT, 'trigger': 'none', 'dev': 'custom0', 'mode': ''}],
         'skyworth,sk-d840n', True, '', False, 0),
        ([{'sysfs': 'skyworth:green:pon'}], 'skyworth,sk-d840n', True, '', True, 0),
        ([], 'zte,zx279133-sr1010', True, '', False, 0),
        ([], 'skyworth,sk-d840n', False, '', False, 1),
        ([], 'skyworth,sk-d840n', True, '/fixture-build', False, 1),
    ]:
        with tempfile.TemporaryDirectory(prefix='sk-led-migrate-') as tmp:
            directory = Path(tmp)
            library = 'board_name() { echo ' + shlex.quote(board) + '; }\n'
            library += 'config_load() { return ' + ('0' if load_ok else '1') + '; }\n'
            library += 'config_get() { case "$2" in\n'
            for i, section in enumerate(sections):
                library += f' s{i}) sysfs={shlex.quote(section.get("sysfs", ""))} ;;\n'
            library += ' esac; }\nconfig_foreach() {\n :\n'
            for i in range(len(sections)):
                library += f' "$1" s{i}\n'
            library += '''}
uci() {
 case "$1" in
 add) echo cfgfixture; echo add >> calls ;;
 batch) cat > batch; echo batch >> calls ;;
 *) return 1 ;;
 esac
}
'''
            result = run_script(source, directory, library, {'IPKG_INSTROOT': instroot})
            assert result.returncode == status, result.stderr
            assert (directory / 'batch').exists() == should_add
            if should_add:
                batch = (directory / 'batch').read_text(encoding='utf-8').splitlines()
                assert batch == [f"set system.cfgfixture.{k}='{v}'" for k, v in DEFAULT.items()] + ['commit system']
                assert (directory / 'calls').read_text().splitlines() == ['add', 'batch']
            else:
                assert not (directory / 'calls').exists()
    print('PASS: retained config, disabled/custom LED, repeated defaults, other board, missing config and build-host guard')


def pack_dtb(nodes):
    """Serialize the parsed nodes so negative tests are valid DTBs, not corrupt blobs."""
    strings = bytearray()
    offsets = {}
    tree = bytearray()
    stack = []
    for node in nodes:
        path = node['path']
        parts = [] if path == '/' else path.strip('/').split('/')
        depth = len(parts)
        while len(stack) > depth:
            tree.extend(struct.pack('>I', 2))
            stack.pop()
        name = (parts[-1] if parts else '').encode() + b'\0'
        tree.extend(struct.pack('>I', 1) + name + b'\0' * (-len(name) % 4))
        stack.append(path)
        for key, raw in node['props'].items():
            if key not in offsets:
                offsets[key] = len(strings)
                strings.extend(key.encode() + b'\0')
            tree.extend(struct.pack('>III', 3, len(raw), offsets[key]) + raw + b'\0' * (-len(raw) % 4))
    tree.extend(struct.pack('>I', 2) * len(stack) + struct.pack('>I', 9))
    return struct.pack('>10I', 0xd00dfeed, 56 + len(tree) + len(strings), 56,
                       56 + len(tree), 40, 17, 16, 0, len(strings), len(tree)) + b'\0' * 16 + tree + strings


def dtb_tests(dtb=None):
    cell = lambda *v: struct.pack('>' + 'I' * len(v), *v)
    nodes = gate.FDT(dtb.read_bytes()).nodes if dtb else [
        {'path': '/', 'props': {'compatible': b'skyworth,sk-d840n\0zte,zx279133\0'}},
        {'path': '/pinctrl', 'props': {'phandle': cell(1), 'compatible': b'zte,zx279133-pinctrl\0'}},
        {'path': '/gpio4', 'props': {'phandle': cell(2), 'reg': cell(0, 0x10d10100, 0, 0x40),
         '#gpio-cells': cell(2), 'ngpios': cell(6), 'gpio-ranges': cell(1, 0, 60, 6)}},
        {'path': '/leds', 'props': {'compatible': b'gpio-leds\0'}},
        {'path': '/leds/led-lan1', 'props': {'label': gate.LABEL, 'gpios': cell(2, 2, 1),
         'default-state': b'off\0', 'function': b'lan\0', 'color': cell(2)}},
        {'path': '/switch', 'props': {}},
        {'path': '/switch/ports', 'props': {}},
        {'path': '/switch/ports/port@8', 'props': {'reg': cell(8), 'label': b'lan1\0'}},
    ]
    gate.validate_dtb(pack_dtb(nodes))
    led_index = next(i for i, n in enumerate(nodes) if n['props'].get('label') == gate.LABEL)
    handle, _, _ = gate.cells(nodes[led_index]['props']['gpios'])
    for key, value in [('gpios', cell(handle, 3, 1)), ('gpios', cell(handle, 2, 0)),
                       ('gpios', cell(0xdead, 2, 1)), ('default-state', b'on\0'),
                       ('label', b'skyworth:green:wrong\0')]:
        broken = copy.deepcopy(nodes)
        broken[led_index]['props'][key] = value
        try:
            gate.validate_dtb(pack_dtb(broken))
        except (AssertionError, KeyError):
            pass
        else:
            raise AssertionError(f'accepted broken LAN1 {key}: {value!r}')
    broken = copy.deepcopy(nodes)
    broken.append({'path': '/other-consumer', 'props': {'gpios': cell(handle, 2, 1)}})
    try:
        gate.validate_dtb(pack_dtb(broken))
    except AssertionError:
        pass
    else:
        raise AssertionError('accepted duplicate LAN1 GPIO owner')
    print('PASS: wrong pin, polarity, phandle, initial state, missing LED and GPIO conflict rejected')


def kernel_config_tests():
    symbols = ('LEDS_GPIO', 'GPIO_ZX279133', 'LEDS_TRIGGERS', 'LEDS_TRIGGER_NETDEV')
    with tempfile.TemporaryDirectory(prefix='sk-led-config-') as tmp:
        path = Path(tmp) / '.config'
        lines = [f'CONFIG_{symbol}=y' for symbol in symbols]
        path.write_text('\n'.join(lines) + '\n', encoding='utf-8')
        gate.validate_config(path)
        for symbol in symbols:
            for bad_line in (f'CONFIG_{symbol}=m', f'# CONFIG_{symbol} is not set'):
                broken = [bad_line if line == f'CONFIG_{symbol}=y' else line for line in lines]
                path.write_text('\n'.join(broken) + '\n', encoding='utf-8')
                try:
                    gate.validate_config(path)
                except AssertionError:
                    pass
                else:
                    raise AssertionError(f'accepted missing built-in LED support: {bad_line}')
    print('PASS: missing or modular GPIO LED/netdev trigger support rejected')


def target_tests(root, qemu):
    """Use the image's actual functions.sh, uci.sh, UCI executable and libuci."""
    functions = (root / 'lib/functions.sh').read_text(encoding='utf-8')
    uci_lib = (root / 'lib/config/uci.sh').read_text(encoding='utf-8')
    source = (root / gate.FILES[1]).read_text(encoding='utf-8')
    system = root / 'etc/config/system'
    original_system = system.read_bytes() if system.is_file() else None
    uci_exe = next(p for p in (root / 'sbin/uci', root / 'usr/sbin/uci') if p.is_file())
    for options, board in [(None, 'skyworth,sk-d840n'), (DEFAULT, 'skyworth,sk-d840n'),
                           ({**DEFAULT, 'trigger': 'none', 'dev': 'custom0'}, 'skyworth,sk-d840n'),
                           ({'sysfs': 'skyworth:green:pon'}, 'skyworth,sk-d840n'),
                           (None, 'zte,zx279133-sr1010')]:
        with tempfile.TemporaryDirectory(prefix='sk-led-real-uci-') as tmp:
            directory = Path(tmp)
            conf = directory / 'config'
            delta = directory / 'delta'
            conf.mkdir()
            delta.mkdir()
            text = "config system 'system'\n option hostname 'preserve-me'\n"
            if options is not None:
                # Deliberately collide with the board default's name.
                text += "config led 'led_lan1'\n" + ''.join(f' option {k} {shlex.quote(v)}\n' for k, v in options.items())
            (conf / 'system').write_text(text, encoding='utf-8')
            command = [qemu, '-L', str(root), str(uci_exe), '-c', str(conf), '-t', str(delta)]
            prefix = 'uci() { ' + shlex.join(command) + ' "$@"; }\n'
            # Redirect only the UCI compatibility library's executable and config
            # paths; the shipped migration, config parsing and callbacks stay real.
            libpath = directory / 'uci-lib.sh'
            libpath.write_text(uci_lib.replace('/sbin/uci', 'uci'), encoding='utf-8')
            library = prefix + functions.replace('/lib/config/uci.sh', libpath.as_posix())
            # Do not read the CI host's /var/state in the real uci_load callback.
            library += '\nLOAD_STATE=\n'
            library += '\nboard_name() { echo ' + shlex.quote(board) + '; }\n'
            before = (conf / 'system').read_bytes()
            for _ in range(2):
                result = run_script(source, directory, library, {'QEMU_LD_PREFIX': str(root)})
                assert result.returncode == 0, (result.stdout, result.stderr)
            output = subprocess.check_output(command + ['export', 'system'], text=True)
            assert "preserve-me" in output
            expected_add = board == 'skyworth,sk-d840n' and (options or {}).get('sysfs') != DEFAULT['sysfs']
            assert output.count(DEFAULT['sysfs']) == (1 if expected_add or options == DEFAULT or
                   (options or {}).get('sysfs') == DEFAULT['sysfs'] else 0), output
            if not expected_add:
                assert (conf / 'system').read_bytes() == before, 'custom/other-board config was rewritten'
            else:
                for key, value in DEFAULT.items():
                    assert f"option {key} '{value}'" in output, output
    assert (system.read_bytes() if system.is_file() else None) == original_system, 'test modified the staged root'
    print('PASS: shipped AArch64 UCI/libuci and real OpenWrt config callbacks; upgrades are idempotent and preserve custom LEDs')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dtb', type=Path)
    parser.add_argument('--root', type=Path)
    parser.add_argument('--qemu', default='qemu-aarch64')
    args = parser.parse_args()
    board_tests()
    migration_tests()
    dtb_tests(args.dtb)
    kernel_config_tests()
    if args.root:
        target_tests(args.root.resolve(), args.qemu)
