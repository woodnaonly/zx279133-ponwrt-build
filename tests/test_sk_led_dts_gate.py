#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Run the workflow's actual DTS harness and LED failure reporting off-device."""
import argparse
from pathlib import Path
import shlex
import shutil
import struct
import subprocess
import sys
import tempfile

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
from fdtwalk import FDT

SHELL = shutil.which('bash') or 'C:/Program Files/Git/bin/bash.exe'
WORKFLOW = ROOT / '.github/workflows/build-sk-d840n.yml'


def run(script, directory):
    # Actions enables errexit by default. The workflow must explicitly preserve
    # the failing gate's status across tee before exiting with that same status.
    return subprocess.run([SHELL, '-e', '-c', script], cwd=directory,
                          capture_output=True, text=True, timeout=30)


def compile_tests(steps, cpp, dtc):
    source = next(s['run'] for s in steps if s.get('name') == 'Apply SK-D840N overlays')
    start = source.index('gate() {\n')
    harness = source[start:source.index('\n}\n', start) + 3]
    harness = harness.replace('if dtc ', 'if ' + shlex.join(dtc) + ' ')
    harness = harness.replace('&& dtc ', '&& ' + shlex.join(dtc) + ' ')
    harness = harness.replace('cpp -nostdinc ', shlex.join(cpp) + ' -nostdinc ')
    with tempfile.TemporaryDirectory(prefix='sk-led-dtc-') as tmp:
        directory = Path(tmp)
        (directory / 'panel.h').write_text(
            '#define PANEL_PIN 2\n#define ACTIVE_LOW 1\n'
            '#define GREEN 2\n#define LAN "lan"\n', encoding='utf-8')
        board = directory / 'panel.dts'
        board.write_text('/dts-v1/;\n#include "panel.h"\n'
                         '/ { gpios = <PANEL_PIN ACTIVE_LOW>; '
                         'color = <GREEN>; function = LAN; };\n', encoding='utf-8')
        # Keep all artifacts private to this test instead of writing CI's /tmp
        # filenames. Commands and their preprocessor flags remain verbatim.
        harness = harness.replace('/tmp/', directory.as_posix() + '/')
        script = ('set -uo pipefail\n' + harness + '\n' +
                  '\n'.join(f'{key}={shlex.quote(directory.as_posix())}'
                            for key in ('DTS', 'PFX', 'KT')) +
                  '\ngate ' + shlex.quote(board.as_posix()) + '\n')
        result = run(script, directory)
        assert result.returncode == 0, result.stdout + result.stderr
        props, = [n['props'] for n in FDT((directory / 'panel.dts.dtb').read_bytes()).nodes]
        assert props['gpios'] == struct.pack('>II', 2, 1), props
        assert props['color'] == struct.pack('>I', 2), props
        assert props['function'] == b'lan\0', props
        # Prove this fixture catches the original bug, not just a missing dtc.
        broken = script.replace('-nostdinc ', '-nostdinc -fdirectives-only ')
        result = run(broken, directory)
        assert result.returncode != 0, 'directives-only incorrectly passed the DTS gate'
        board.write_text('/dts-v1/;\n/ { broken = <; };\n', encoding='utf-8')
        result = run(script, directory)
        assert result.returncode != 0, 'invalid DTS incorrectly passed the gate'
        board.write_text('/dts-v1/;\n#include "missing.h"\n/ {};\n', encoding='utf-8')
        result = run(script, directory)
        assert result.returncode != 0, 'missing include incorrectly passed the gate'
    print('PASS: workflow DTS harness expands real macros and rejects directives-only, syntax and include failures')


def reporting_tests(steps):
    source = next(s['run'] for s in steps if s.get('name') ==
                  'Gate: complete panel LED wiring and negative controls')
    commands = ('python3 tools/check-sk-lan1-led.py --dtb /tmp/zx279133-sk-d840n.dts.dtb',
                'python3 tests/test_sk_lan1_led.py --dtb /tmp/zx279133-sk-d840n.dts.dtb')
    python = shlex.quote(Path(sys.executable).as_posix())
    for statuses in ((0, 0), (7, 0), (0, 9)):
        with tempfile.TemporaryDirectory(prefix='sk-led-report-') as tmp:
            script = source
            for index, (command, status) in enumerate(zip(commands, statuses)):
                program = f'print("gate{index}: 50%\\nline2"); raise SystemExit({status})'
                script = script.replace(command, python + ' -c ' + shlex.quote(program))
            script = script.replace('python3 -c ', python + ' -c ')
            result = run(script, tmp)
            assert result.returncode == (statuses[0] or statuses[1]), result.stdout + result.stderr
            log = (Path(tmp) / 'sk-panel-led-gate.log').read_text(encoding='utf-8')
            assert 'gate0: 50%' in log, log
            assert ('gate1: 50%' in log) == (statuses[0] == 0), log
            if any(statuses):
                assert '::error::Panel LED gate failed:' in result.stdout, result.stdout
                assert '50%25%0Aline2%0A' in result.stdout, result.stdout
            else:
                assert '::error::' not in result.stdout, result.stdout
    upload = next(s for s in steps if s.get('name') == 'Upload logs on failure')
    assert 'sk-panel-led-gate.log' in upload['with']['path'].splitlines()
    print('PASS: LED gate logs/annotations preserve both failures, escape messages and stop after a failed DTB check')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cpp', default='cpp', help='preprocessor command, e.g. "zig cc -E"')
    parser.add_argument('--dtc', nargs='+', default=['dtc'])
    args = parser.parse_args()
    steps = yaml.safe_load(WORKFLOW.read_text(encoding='utf-8'))['jobs']['build']['steps']
    compile_tests(steps, shlex.split(args.cpp), args.dtc)
    reporting_tests(steps)
