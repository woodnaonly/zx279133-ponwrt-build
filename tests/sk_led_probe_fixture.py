#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Linux-only temporary filesystem and MDIO stand-ins for the real probe shell.

The copied shell retains its state machine, real flock locks, background guard
and restoration code. Only absolute device paths and the two diagnostic-file
writes are replaced; no board, SSH, physical GPIO or PHY is accessed.
"""

import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import time


MDIO_STUB = r'''import json
import os
from pathlib import Path
import sys
import time

root = Path(os.environ['SK_LED_FIXTURE_ROOT'])
request = sys.stdin.read().strip().split()
if len(request) not in (4, 5) or request[0] not in ('m45', 'm45w'):
    raise SystemExit('invalid simulated MDIO command')
kind, phy, devad, reg = request[:4]
address = (int(phy, 0), int(devad, 0), int(reg, 0))
entry = dict(kind=kind, phy=address[0], devad=address[1], reg=address[2])
if len(request) == 5:
    entry['value'] = int(request[4], 0)
with (root / 'history.jsonl').open('a', encoding='utf-8') as history:
    history.write(json.dumps(entry) + '\n')
if address[0] != 5:
    raise SystemExit('unexpected PHY')
if kind == 'm45w' and (len(request) != 5 or address[1] != 0x1f
                       or address[2] not in (0xd032, 0xd040, 0xd044)):
    raise SystemExit('non-LED write refused by fixture')
if kind == 'm45' and len(request) != 4:
    raise SystemExit('invalid read')
registers = json.loads((root / 'registers.json').read_text(encoding='utf-8'))
settings = json.loads((root / 'settings.json').read_text(encoding='utf-8'))
key = '%x/%04x' % (address[1], address[2])
if key not in registers:
    raise SystemExit('unexpected register')
if kind == 'm45w':
    if settings.get('fail_restore_reg') == reg and entry['value'] == {
            '0xd032': 0x00a7, '0xd040': 1, '0xd044': 0x00a7}[reg]:
        raise SystemExit('injected field restoration failure')
    if settings.get('fail_restore') and entry['value'] == 0x00a7:
        raise SystemExit('injected restoration failure')
    if (settings.pop('fail_write_once', False)
            or settings.get('fail_write_reg_once') == reg):
        settings.pop('fail_write_reg_once', None)
        (root / 'settings.json').write_text(json.dumps(settings), encoding='utf-8')
        raise SystemExit('injected MDIO write failure')
    value = entry['value']
    if (settings.pop('mismatch_write_once', False)
            or settings.get('mismatch_write_reg_once') == reg):
        settings.pop('mismatch_write_reg_once', None)
        value ^= 1
    registers[key] = '0x%04x' % value
    (root / 'settings.json').write_text(json.dumps(settings), encoding='utf-8')
    (root / 'registers.json').write_text(json.dumps(registers), encoding='utf-8')
if not settings.get('drop_response'):
    with (root / 'kernel.log').open('a', encoding='utf-8') as log:
        log.write('[%d] outer phy 5 devad %s reg %s = %s\n' %
                  (time.monotonic_ns(), devad, reg, registers[key]))
'''


class HelperFixture:
    """Run a temporary copy of the actual shell, with harmless hardware I/O."""

    def __init__(self, helper):
        self.helper = Path(helper)
        self.temporary = None
        self.states = []

    def __enter__(self):
        if os.name != 'posix':
            raise RuntimeError('The shell integration fixture requires Linux/POSIX')
        for executable in ('sh', 'flock', 'sleep'):
            if not shutil.which(executable):
                raise RuntimeError('Required integration dependency: ' + executable)
        self.temporary = tempfile.TemporaryDirectory(prefix='sk-led-helper-test-')
        self.root = Path(self.temporary.name)
        self.state = self.new_session('ABC123')
        self.commands = self.root / 'commands'
        self.commands.mkdir()
        self.script = self.root / 'probe.sh'
        self.env = {
            **os.environ,
            'PATH': str(self.commands) + os.pathsep + os.environ.get('PATH', ''),
            'SK_LED_FIXTURE_ROOT': str(self.root),
            'SK_LED_FIXTURE_PYTHON': sys.executable,
            'SK_LED_FIXTURE_REAL_SLEEP': shutil.which('sleep'),
        }
        files = {
            'board': 'skyworth,sk-d840n\n',
            'carrier': '1\n',
            'speed': '1000\n',
            'system': "config led 'lan1'\n\toption trigger 'default-on'\n",
            'gpio': 'gpiochip4: simulated GPIO ownership only\n',
            'diag': '',
            'kernel.log': '',
            'history.jsonl': '',
            'settings.json': '{}',
        }
        for name, content in files.items():
            self.write(name, content)
        registers = {
            '1f/d032': '0x00a7', '1f/d040': '0x0001',
            '1f/d044': '0x00a7', '1f/a434': '0x002c',
            '1e/697a': '0x0000', '1e/6a04': '0x0503',
            '1e/6f10': '0x086a', '1e/6f11': '0x0021',
            '1e/7580': '0x9416',
        }
        self.write('registers.json', json.dumps(registers))
        (self.root / 'pinctrl').mkdir()
        for name in ('lan1', 'lan2', 'lan3', 'lan4', 'usb1', 'phone1',
                     'power', 'wan', 'pon', 'los'):
            led = self.root / 'leds' / ('skyworth:%s:%s' %
                                        ('red' if name == 'los' else 'green', name))
            led.mkdir(parents=True)
            (led / 'brightness').write_text('1\n', encoding='utf-8')
            (led / 'trigger').write_text('none [default-on]\n', encoding='utf-8')
        ports = self.root / 'leds/skyworth:green:usb1/ports'
        ports.mkdir()
        for name in ('usb1-port1', 'usb2-port1'):
            (ports / name).write_text('1\n', encoding='utf-8')
        for name, speed in (('usb1', '480'), ('usb2', '5000')):
            hub = self.root / 'usb' / name
            hub.mkdir(parents=True)
            (hub / 'speed').write_text(speed + '\n', encoding='utf-8')
            (hub / 'product').write_text('simulated xHCI root hub\n', encoding='utf-8')
        self.write('mdio.py', MDIO_STUB)
        commands = {
            'sk-led-fixture-io': '#!/bin/sh\nexec "$SK_LED_FIXTURE_PYTHON" '
                                 '"$SK_LED_FIXTURE_ROOT/mdio.py"\n',
            'dmesg': '#!/bin/sh\ncat "$SK_LED_FIXTURE_ROOT/kernel.log"\n',
            # The 30-tick guard lasts 15 real seconds. A complete three-round
            # experiment holds phases for 12 ticks (six seconds), leaving nine
            # seconds for real subprocess/MDIO-fixture overhead, rather than
            # relying on a narrow timing race. Keep only integer sleep inputs.
            'sleep': '#!/bin/sh\ncase "$1" in\n'
                     '1) exec "$SK_LED_FIXTURE_REAL_SLEEP" 0.5 ;;\n'
                     '*) printf "unexpected simulated sleep\\n" >&2; exit 90 ;;\n'
                     'esac\n',
        }
        for name, content in commands.items():
            command = self.commands / name
            command.write_text(content, encoding='utf-8')
            command.chmod(0o700)
        source = self.helper.read_text(encoding='utf-8')
        # Do not wrap ls/stat or weaken permissions. Only the temporary helper
        # expects the fixture owner's actual UID; production still requires 0,
        # and the original drwx------ / no-symlink checks are left unchanged.
        if source.count('$3 == 0') != 1:
            raise RuntimeError('Fixture UID substitution must match exactly one root check')
        source = source.replace('$3 == 0', '$3 == %d' % os.getuid())
        if source.count('> "$diag"') != 2:
            raise RuntimeError('Fixture hardware substitution must cover exactly two writes')
        source = source.replace('> "$diag"', '| sk-led-fixture-io')
        substitutions = {
            '/tmp/sk-led-diag.': str(self.root / 'sk-led-diag.'),
            '/tmp/sk-led-phy-probe-lock': str(self.root / 'global-lock'),
            '/tmp/sysinfo/board_name': str(self.root / 'board'),
            '/sys/class/net/lan1/carrier': str(self.root / 'carrier'),
            '/sys/class/net/lan1/speed': str(self.root / 'speed'),
            '/etc/config/system': str(self.root / 'system'),
            '/sys/class/leds': str(self.root / 'leds'),
            '/sys/bus/usb/devices': str(self.root / 'usb'),
            '/sys/kernel/debug/gpio': str(self.root / 'gpio'),
            '/sys/kernel/debug/pinctrl': str(self.root / 'pinctrl'),
            '/sys/kernel/debug/zx279133-9132': str(self.root / 'diag'),
        }
        for original, replacement in substitutions.items():
            if original not in source:
                raise RuntimeError('Fixture substitution no longer matches: ' + original)
            source = source.replace(original, replacement)
        self.script.write_text(source, encoding='utf-8')
        return self

    def write(self, name, content):
        (self.root / name).write_text(content, encoding='utf-8')

    def configure(self, **settings):
        self.write('settings.json', json.dumps(settings))

    def new_session(self, suffix):
        if re.fullmatch(r'[A-Za-z0-9]{6}', suffix) is None:
            raise ValueError('Fixture session suffix must be six ASCII letters/digits')
        state = self.root / ('sk-led-diag.' + suffix)
        state.mkdir()
        self.states.append(state)
        return state

    def run(self, action, state=None):
        state = self.state if state is None else state
        if state not in self.states:
            raise ValueError('Refusing a state directory not owned by this fixture')
        return subprocess.run(
            ['sh', str(self.script), str(state), action],
            capture_output=True, text=True, encoding='utf-8',
            env=self.env, timeout=8)

    def writes(self):
        entries = (self.root / 'history.jsonl').read_text(encoding='utf-8')
        return [entry for line in entries.splitlines()
                if (entry := json.loads(line))['kind'] == 'm45w']

    def word(self):
        registers = json.loads((self.root / 'registers.json').read_text(encoding='utf-8'))
        return registers['1f/d044']

    def fields(self):
        registers = json.loads((self.root / 'registers.json').read_text(encoding='utf-8'))
        return {reg: registers['1f/' + reg] for reg in ('d032', 'd040', 'd044')}

    def wait_for_guard(self, state=None, timeout=20):
        state = self.state if state is None else state
        if state not in self.states:
            raise ValueError('Refusing a state directory not owned by this fixture')
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            done = state / 'guard.done'
            if done.exists():
                return done.read_text(encoding='utf-8')
            time.sleep(0.02)
        log = state / 'guard.log'
        details = log.read_text(encoding='utf-8') if log.exists() else '(no guard log)'
        raise AssertionError('Guard did not finish: ' + details)

    def __exit__(self, _typ, _value, _traceback):
        if self.temporary is None:
            return
        # Only this fixture's private temporary state is touched. Do not kill
        # PIDs read from a file: allow the guard to release its own flock.
        self.write('settings.json', '{}')
        for state in self.states:
            (state / 'cancelled').write_text('fixture cleanup\n', encoding='utf-8')
        for state in self.states:
            deadline = time.monotonic() + 2
            while (state / 'guard.ready').exists() and time.monotonic() < deadline:
                log = state / 'guard.log'
                text = log.read_text(encoding='utf-8') if log.exists() else ''
                if 'GUARD_CANCELLED' in text or (state / 'guard.done').exists():
                    break
                time.sleep(0.02)
        self.temporary.cleanup()
