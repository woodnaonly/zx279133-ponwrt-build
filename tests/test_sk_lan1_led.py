#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Full panel tests (legacy LAN1 entrypoint); --root uses shipped UCI under QEMU."""
import argparse
import copy
import importlib.util
import json
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
SHELL = r'C:\Program Files\Git\bin\bash.exe' if os.name == 'nt' else shutil.which('bash')


def default(name, sysfs, trigger, initial='0', **options):
    return dict(name=name, sysfs=sysfs, trigger=trigger, default=initial, **options)


# Independent expectations for board.json generation and retained-config migration.
DEFAULTS = [
    default('Power', 'skyworth:green:power', 'default-on', '1'),
    default('PON', 'skyworth:green:pon', 'zx279133-pon'),
    default('LOS', 'skyworth:red:los', 'zx279133-los'),
    default('Internet (PON)', 'skyworth:green:wan', 'netdev', dev='eth0', mode='link tx rx'),
    *[default('2.5G/LAN1' if i == 1 else f'LAN{i}', f'skyworth:green:lan{i}',
              'netdev', dev=f'lan{i}', mode='link tx rx') for i in range(1, 5)],
    default('FXS', 'skyworth:green:phone1', 'none'),
    default('USB', 'skyworth:green:usb1', 'usbport', port=['usb1-port1', 'usb2-port1']),
]
DEFAULT = DEFAULTS[4]


def run_script(source, directory, library, env=None, defaults=None):
    libpath = directory / 'functions.sh'
    libpath.write_text(library, encoding='utf-8', newline='\n')
    for original in ('/lib/functions.sh', '/lib/functions/uci-defaults.sh'):
        source = source.replace('. ' + original, '. ' + shlex.quote(libpath.as_posix()))
    source = source.replace('. /lib/functions/sk-d840n-leds.sh',
                            '. ' + shlex.quote((defaults or BASE / gate.FILES[2]).as_posix()))
    script = directory / 'run.sh'
    script.write_text(source, encoding='utf-8', newline='\n')
    return subprocess.run([SHELL, str(script)], cwd=directory,
                          env={**os.environ, 'IPKG_INSTROOT': '', 'PKG_ROOT': '',
                               'UCI_CONFIG_DIR': '', **(env or {})},
                          capture_output=True, text=True, timeout=30)


def board_tests():
    library = '''board_name() { echo "$LED_BOARD"; }
board_config_update() { :; }
board_config_flush() { :; }
record() { local tag="$1"; shift; printf '%s' "$tag"; printf '|%s' "$@"; printf '\\n'; }
ucidef_set_led_default() { record default "$@"; }
ucidef_set_led_netdev() { record netdev "$@"; }
ucidef_set_led_usbport() { record usbport "$@"; }
_ucidef_set_led_trigger() { record trigger "$@"; }
'''
    expected = []
    for key, led in zip(('power', 'pon', 'los', 'wan', 'lan1', 'lan2', 'lan3', 'lan4', 'fxs', 'usb'), DEFAULTS):
        args = [key, led['name'], led['sysfs']]
        expected.append('|'.join(['default', *args, led['default']]))
        trigger = led['trigger']
        if trigger == 'netdev':
            expected.append('|'.join(['netdev', *args, led['dev'], led['mode']]))
        elif trigger == 'usbport':
            expected.append('|'.join(['usbport', *args, *led['port']]))
        elif trigger != 'none':
            expected.append('|'.join(['trigger', *args, trigger]))
    sr1010 = ['netdev|wan|WAN|sr1010:wan|eth0|link'] + [
        f'netdev|lan{i}|LAN{i}|sr1010:lan{i}|lan{i}|link' for i in range(1, 5)]
    with tempfile.TemporaryDirectory(prefix='sk-led-board-') as tmp:
        for board, wanted in [('skyworth,sk-d840n', expected),
                              ('zte,zx279133-sr1010', sr1010), ('unrelated,board', [])]:
            result = run_script((BASE / gate.FILES[0]).read_text(encoding='utf-8'),
                                Path(tmp), library, {'LED_BOARD': board})
            assert result.returncode == 0, result.stderr
            assert result.stdout.splitlines() == wanted, result.stdout
    print('PASS: all panel board defaults; SR1010 and unrelated boards unchanged')


def mock_uci(path, args):
    """Execute the actual emitted batch against a stateful test database."""
    assert args == ['batch'], args
    if os.environ.get('LED_UCI_FAIL'):
        return 1
    state = json.loads(path.read_text(encoding='utf-8'))
    sections = state['sections']
    committed = False
    for line in sys.stdin:
        words = shlex.split(line)
        if not words:
            continue
        command, *args = words
        if command == 'add':
            assert args == ['system', 'led']
            sections.append({'id': f'cfgfixture{len(sections)}', 'type': 'led', 'options': {}})
        elif command == 'delete':
            package, name = args[0].split('.')
            assert package == 'system'
            sections[:] = [s for s in sections if s['id'] != name]
        elif command in ('set', 'add_list'):
            target, value = args[0].split('=', 1)
            package, name, option = target.split('.')
            assert package == 'system' and name == '@led[-1]'
            led = [s for s in sections if s['type'] == 'led'][-1]['options']
            if command == 'set':
                led[option] = value
            else:
                led.setdefault(option, []).append(value)
        else:
            assert command == 'commit' and args == ['system'], words
            committed = True
    assert committed
    state['commits'] += 1
    path.write_text(json.dumps(state, ensure_ascii=False), encoding='utf-8')
    return 0


def sections_for(options):
    return [{'id': 'system', 'type': 'system', 'options': {'hostname': 'preserve-me'}},
            *[{'id': 'led_lan1' if i == 0 else f'user{i}', 'type': 'led', 'options': led}
              for i, led in enumerate(options)]]


def assert_migrated(before, after):
    old_leds = [s['options'] for s in before if s['type'] == 'led']
    for old in before:
        if old['options'].get('sysfs') != 'skyworth:green:wps':
            assert old in after, ('changed user section', old, after)
    leds = [s['options'] for s in after if s['type'] == 'led']
    assert all(led.get('sysfs') != 'skyworth:green:wps' for led in leds)
    for expected in DEFAULTS:
        previous = [led for led in old_leds if led.get('sysfs') == expected['sysfs']]
        matches = [led for led in leds if led.get('sysfs') == expected['sysfs']]
        assert matches == (previous or [expected]), (expected, matches)


def migration_tests():
    source = MIGRATION.read_text(encoding='utf-8')
    custom = [default('Leave LOS dark', 'skyworth:red:los', 'none'),
              default('PON always on', 'skyworth:green:pon', 'default-on', '1'),
              default('Other USB', 'skyworth:green:usb1', 'usbport', port=['usb7-port3']),
              default('My FXS', 'skyworth:green:phone1', 'timer', delayon='123'),
              {**DEFAULT, 'name': 'My LAN', 'dev': 'custom0', 'mode': 'link'}]
    for options, board, load_ok, env, status in [
        ([], 'skyworth,sk-d840n', True, {}, 0),
        ([{**DEFAULT, 'name': 'LAN1'}], 'skyworth,sk-d840n', True, {}, 0),
        (custom, 'skyworth,sk-d840n', True, {}, 0),
        (DEFAULTS, 'skyworth,sk-d840n', True, {}, 0),
        ([{'sysfs': 'skyworth:green:wps'}, {'name': 'wps', 'sysfs': 'custom:lamp'}],
         'skyworth,sk-d840n', True, {}, 0),
        ([DEFAULT, {**DEFAULT, 'trigger': 'none'}], 'skyworth,sk-d840n', True, {}, 0),
        ([], 'zte,zx279133-sr1010', True, {}, 0),
        ([], 'skyworth,sk-d840n', False, {}, 1),
        ([], 'skyworth,sk-d840n', True, {'IPKG_INSTROOT': '/fixture'}, 1),
        ([], 'skyworth,sk-d840n', True, {'PKG_ROOT': '/fixture'}, 1),
        ([], 'skyworth,sk-d840n', True, {'LED_UCI_FAIL': '1'}, 1),
    ]:
        with tempfile.TemporaryDirectory(prefix='sk-led-migrate-') as tmp:
            directory = Path(tmp)
            database = directory / 'state.json'
            before = sections_for(options)
            database.write_text(json.dumps({'sections': before, 'commits': 0}), encoding='utf-8')
            original = database.read_bytes()
            for attempt in range(2):
                state = json.loads(database.read_text(encoding='utf-8'))
                library = f'board_name() {{ echo {shlex.quote(board)}; }}\n'
                library += f'config_load() {{ return {0 if load_ok else 1}; }}\n'
                library += 'config_get() { sysfs=; case "$2" in\n'
                for section in state['sections']:
                    library += f" {section['id']}) sysfs={shlex.quote(section['options'].get('sysfs', ''))} ;;\n"
                library += ' esac; }\nconfig_foreach() {\n :\n'
                for section in state['sections']:
                    if section['type'] == 'led':
                        library += f' "$1" {section["id"]}\n'
                library += '}\nuci() { ' + shlex.join([Path(sys.executable).as_posix(),
                            Path(__file__).as_posix(), '--mock-uci', database.as_posix()]) + ' "$@"; }\n'
                prior = database.read_bytes()
                result = run_script(source, directory, library, env)
                assert result.returncode == status, (result.stdout, result.stderr)
                if status == 0 and board == 'skyworth,sk-d840n':
                    assert_migrated(before, json.loads(database.read_text(encoding='utf-8'))['sections'])
                    if attempt:
                        assert database.read_bytes() == prior, 'second migration was not a no-op'
                else:
                    assert database.read_bytes() == original, 'guard/failure mutated config'
            if options == DEFAULTS:
                assert database.read_bytes() == original, 'complete config was rewritten'
    print('PASS: missing-only migration, custom/disabled/duplicate LEDs, exact WPS cleanup, idempotence and failure guards')


def beacon_tests():
    source = (ROOT / 'trial-files/usr/libexec/trial-blink').read_text(encoding='utf-8')
    with tempfile.TemporaryDirectory(prefix='sk-led-beacon-') as tmp:
        directory = Path(tmp)
        state = directory / 'led.state'
        led = directory / 'lamp'
        led.mkdir()
        (led / 'brightness').write_text('37', encoding='utf-8')
        (led / 'max_brightness').write_text('255', encoding='utf-8')
        source = source.replace('/var/run/led.state', state.as_posix())
        state.touch()
        result = subprocess.run([SHELL, '-c', source], capture_output=True, timeout=5)
        assert result.returncode == 0, result.stderr
        state.unlink()
        result = subprocess.run([SHELL, '-c', 'uci() { echo 1; }\n' + source], capture_output=True, timeout=5)
        assert result.returncode == 0, result.stderr
        state.touch()
        prefix = source[:source.index('# pulse_led')]
        for command in ('led_on', 'led_off'):
            state.unlink()
            script = 'uci() { return 1; }\n' + prefix + '\n: > ' + shlex.quote(state.as_posix()) + '\n'
            script += command + ' ' + shlex.quote(led.as_posix()) + '\nexit 42\n'
            result = subprocess.run([SHELL, '-c', script], capture_output=True, timeout=5)
            assert result.returncode == 0, (command, result.stderr)
            assert (led / 'brightness').read_text(encoding='utf-8') == '37', 'beacon overrode configured LED'
    print('PASS: RAM beacon yields before start and between pulses when the system LED service takes over')


def pack_dtb(nodes):
    """Serialize valid DTBs so negative tests exercise wiring, not corrupt blobs."""
    strings, offsets, tree, stack = bytearray(), {}, bytearray(), []
    for node in nodes:
        path = node['path']
        parts = [] if path == '/' else path.strip('/').split('/')
        while len(stack) > len(parts):
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


def dtb_fixture():
    cell = lambda *v: struct.pack('>' + 'I' * len(v), *v)
    nodes = [
        {'path': '/', 'props': {'compatible': b'skyworth,sk-d840n\0zte,zx279133\0'}},
        {'path': '/pinctrl', 'props': {'phandle': cell(1), 'compatible': b'zte,zx279133-pinctrl\0'}},
    ]
    for bank in range(5):
        nodes.append({'path': f'/gpio{bank}', 'props': {'phandle': cell(bank + 2),
            'compatible': b'zte,zx279133-gpio\0',
            'reg': cell(0, 0x10d10000 + bank * 0x40, 0, 0x40), '#gpio-cells': cell(2),
            'ngpios': cell(6 if bank == 4 else 16), 'gpio-ranges': cell(1, 0, 60, 6)}})
    nodes.append({'path': '/leds', 'props': {'compatible': b'gpio-leds\0'}})
    for name, (bank, pin, function, color, state, trigger) in gate.PANEL.items():
        props = {'label': (name + '\0').encode(), 'gpios': cell(bank + 2, pin, 1),
                 'default-state': (state + '\0').encode(), 'function': (function + '\0').encode(),
                 'color': cell(color)}
        if trigger:
            props['linux,default-trigger'] = (trigger + '\0').encode()
        nodes.append({'path': '/leds/led-' + name.rsplit(':', 1)[1], 'props': props})
    nodes += [{'path': '/nppt', 'props': {'compatible': b'zte,zx279133-nppt\0', 'opsd-gpios': cell(4, 1, 0)}},
              {'path': '/switch', 'props': {}}, {'path': '/switch/ports', 'props': {}}]
    for i, port in ((1, 8), (2, 5), (3, 6), (4, 7)):
        nodes.append({'path': f'/switch/ports/port@{port}', 'props': {'reg': cell(port), 'label': f'lan{i}\0'.encode()}})
    return nodes


def dtb_tests(dtb=None):
    cell = lambda *v: struct.pack('>' + 'I' * len(v), *v)
    nodes = gate.FDT(dtb.read_bytes()).nodes if dtb else dtb_fixture()
    gate.validate_dtb(pack_dtb(nodes))

    def rejected(broken):
        try:
            gate.validate_dtb(pack_dtb(broken))
        except (AssertionError, KeyError, ValueError):
            return
        raise AssertionError('accepted broken LED DTB')

    for index, node in enumerate(nodes):
        if node['props'].get('label', b'').rstrip(b'\0').decode() not in gate.PANEL:
            continue
        handle, pin, _ = gate.cells(node['props']['gpios'])
        for key, value in [('gpios', cell(handle, pin + 1, 1)), ('gpios', cell(handle, pin, 0)),
                           ('gpios', cell(0xdead, pin, 1)), ('default-state', b'keep\0'),
                           ('color', cell(7)), ('function', b'wrong\0'),
                           ('linux,default-trigger', b'wrong\0'), ('label', b'skyworth:green:wps\0')]:
            broken = copy.deepcopy(nodes)
            broken[index]['props'][key] = value
            rejected(broken)
        rejected(nodes[:index] + nodes[index + 1:])
        duplicate = copy.deepcopy(nodes)
        duplicate.insert(index + 1, {'path': '/leds/led-copy', 'props': dict(node['props'])})
        rejected(duplicate)
        rejected(nodes + [{'path': '/other-consumer', 'props': {'gpios': cell(handle, pin, 1)}}])
    for index, node in enumerate(nodes):
        if node['props'].get('reg') == cell(0, 0x10d10100, 0, 0x40):
            for key, value in [('gpio-ranges', cell(1, 0, 61, 6)),
                               ('compatible', b'wrong,gpio\0'), ('status', b'disabled\0')]:
                broken = copy.deepcopy(nodes)
                broken[index]['props'][key] = value
                rejected(broken)
        if node['props'].get('label') == b'lan2\0':
            broken = copy.deepcopy(nodes)
            broken[index]['props']['label'] = b'lan1\0'
            rejected(broken)
        if 'opsd-gpios' in node['props']:
            handle, _, _ = gate.cells(node['props']['opsd-gpios'])
            for key, value in [('opsd-gpios', cell(handle, 2, 0)), ('leds', cell(1))]:
                broken = copy.deepcopy(nodes)
                broken[index]['props'][key] = value
                rejected(broken)
    print('PASS: LED declaration negative controls reject changed GPIO/polarity/color/function/state/trigger, WPS, duplicates and conflicts; not physical verification')


def kernel_config_tests():
    with tempfile.TemporaryDirectory(prefix='sk-led-config-') as tmp:
        path = Path(tmp) / '.config'
        lines = [f'CONFIG_{symbol}=y' for symbol in gate.KERNEL_SYMBOLS]
        path.write_text('\n'.join(lines) + '\n', encoding='utf-8')
        gate.validate_config(path)
        for symbol in gate.KERNEL_SYMBOLS:
            for bad in (f'CONFIG_{symbol}=m', f'# CONFIG_{symbol} is not set'):
                path.write_text('\n'.join(bad if line == f'CONFIG_{symbol}=y' else line for line in lines), encoding='utf-8')
                try:
                    gate.validate_config(path)
                except AssertionError:
                    pass
                else:
                    raise AssertionError(f'accepted missing built-in LED support: {bad}')
    print('PASS: missing or modular panel GPIO/netdev/default-on/USB support rejected')


def parse_export(text):
    sections = []
    for line in text.splitlines():
        words = shlex.split(line)
        if not words or words[0] == 'package':
            continue
        if words[0] == 'config':
            sections.append({'id': words[2] if len(words) > 2 else f'anonymous{len(sections)}',
                             'type': words[1], 'options': {}})
        elif words[0] == 'option':
            sections[-1]['options'][words[1]] = words[2]
        else:
            assert words[0] == 'list'
            sections[-1]['options'].setdefault(words[1], []).append(words[2])
    return sections


def target_tests(root, qemu):
    """Run the image's libraries/UCI, never touching the staged root config."""
    functions = (root / 'lib/functions.sh').read_text(encoding='utf-8')
    uci_lib = (root / 'lib/config/uci.sh').read_text(encoding='utf-8')
    source = (root / gate.FILES[1]).read_text(encoding='utf-8')
    system = root / 'etc/config/system'
    original = system.read_bytes() if system.is_file() else None
    uci_exe = next(p for p in (root / 'sbin/uci', root / 'usr/sbin/uci') if p.is_file())
    for options, board in [([], 'skyworth,sk-d840n'), ([DEFAULT], 'skyworth,sk-d840n'),
                           ([default('Off', 'skyworth:red:los', 'none')], 'skyworth,sk-d840n'),
                           ([default('FXS', 'skyworth:green:phone1', 'timer', delayon='123')], 'skyworth,sk-d840n'),
                           ([default('USB', 'skyworth:green:usb1', 'usbport', port=['usb7-port3'])], 'skyworth,sk-d840n'),
                           ([{'sysfs': 'skyworth:green:wps'}, {'sysfs': 'other:lamp'}], 'skyworth,sk-d840n'),
                           (DEFAULTS, 'skyworth,sk-d840n'), ([], 'zte,zx279133-sr1010')]:
        with tempfile.TemporaryDirectory(prefix='sk-led-real-uci-') as tmp:
            directory = Path(tmp)
            conf, delta = directory / 'config', directory / 'delta'
            conf.mkdir()
            delta.mkdir()
            before = sections_for(options)
            text = ''
            for section in before:
                text += f"config {section['type']} {shlex.quote(section['id'])}\n"
                for key, value in section['options'].items():
                    for entry in value if isinstance(value, list) else [value]:
                        text += f" {'list' if isinstance(value, list) else 'option'} {key} {shlex.quote(entry)}\n"
            (conf / 'system').write_text(text, encoding='utf-8')
            command = [qemu, '-L', str(root), str(uci_exe), '-c', str(conf), '-t', str(delta)]
            libpath = directory / 'uci-lib.sh'
            libpath.write_text(uci_lib.replace('/sbin/uci', 'uci'), encoding='utf-8')
            library = 'uci() { ' + shlex.join(command) + ' "$@"; }\n'
            library += functions.replace('/lib/config/uci.sh', libpath.as_posix())
            library += '\nLOAD_STATE=\nboard_name() { echo ' + shlex.quote(board) + '; }\n'
            original_conf = (conf / 'system').read_bytes()
            for attempt in range(2):
                prior = (conf / 'system').read_bytes()
                result = run_script(source, directory, library, {'QEMU_LD_PREFIX': str(root)}, root / gate.FILES[2])
                assert result.returncode == 0, (result.stdout, result.stderr)
                if attempt:
                    assert (conf / 'system').read_bytes() == prior, 'real UCI migration is not idempotent'
            output = subprocess.check_output(command + ['export', 'system'], text=True)
            if board == 'skyworth,sk-d840n':
                assert_migrated(before, parse_export(output))
            else:
                assert (conf / 'system').read_bytes() == original_conf, 'other-board config changed'
            if options == DEFAULTS:
                assert (conf / 'system').read_bytes() == original_conf, 'complete config was rewritten'
    assert (system.read_bytes() if system.is_file() else None) == original, 'test modified staged root'
    print('PASS: shipped AArch64 UCI/libuci migration, user settings and WPS cleanup; staged root untouched')


if __name__ == '__main__':
    if len(sys.argv) > 1 and sys.argv[1] == '--mock-uci':
        sys.exit(mock_uci(Path(sys.argv[2]), sys.argv[3:]))
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dtb', type=Path)
    parser.add_argument('--root', type=Path)
    parser.add_argument('--qemu', default='qemu-aarch64')
    args = parser.parse_args()
    board_tests()
    migration_tests()
    beacon_tests()
    dtb_tests(args.dtb)
    kernel_config_tests()
    if args.root:
        target_tests(args.root.resolve(), args.qemu)
