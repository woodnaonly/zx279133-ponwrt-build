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
import traceback

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


def clone_tests(steps):
    source = next(s['run'] for s in steps if s.get('name') == 'Clone sources')
    tree = 'linux-mainline-zte-zxslc-sr1010/linux-6.18.38'
    with tempfile.TemporaryDirectory(prefix='sk-led-pin-') as tmp:
        dtsi = Path(tmp) / tree / 'arch/arm64/boot/dts/zte/zx279133.dtsi'
        dtsi.parent.mkdir(parents=True)
        dtsi.touch()
        prefix = f'''IW_BRANCH=fixture
IW_URL=https://example.invalid/openwrt
KERNEL_BRANCH=build
KERNEL_URL=https://example.invalid/kernel
KERNEL_SHA={'a' * 40}
IW_SHA={'b' * 40}
KERNEL_SOURCE_DIR={shlex.quote(tree)}
KVER=6.18.60
git() {{
  if [ "$1" = -C ]; then
    if [ "$2" = openwrt ]; then
      [ "$3" != rev-parse ] || printf '%s\\n' "$IW_SHA"
      return 0
    fi
    [ "$2" = {shlex.quote(tree)} ] || return 41
    [ "$3" != rev-parse ] || printf '%s\\n' "$KERNEL_SHA"
  fi
}}
'''
        result = run(prefix + source, tmp)
        assert result.returncode == 0, result.stdout + result.stderr
        assert 'Pinned Linux tree: ' + 'a' * 40 in result.stdout, result.stdout
        broken = source.replace('-C "$KT"', '-C linux-mainline-zte-zxslc-sr1010')
        result = run(prefix + broken, tmp)
        assert result.returncode != 0, 'pinning the wrapper instead of Linux was accepted'
    print('PASS: workflow pins and verifies the Linux submodule, not the wrapper repository')


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
                         'color = <GREEN>; function = LAN; '
                         'linux,default-trigger = "default-on"; };\n', encoding='utf-8')
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
        assert props['linux,default-trigger'] == b'default-on\0', props
        predefines = subprocess.run(cpp + ['-dM', '-x', 'assembler-with-cpp', '-'],
                                    input='', capture_output=True, text=True, timeout=30)
        assert predefines.returncode == 0, predefines.stderr
        if any(line.startswith('#define linux ') for line in predefines.stdout.splitlines()):
            # The original Linux failure: cpp's predefined linux=1 silently
            # renames a valid DT property; dtc accepts the resulting wrong name.
            result = run(script.replace('-undef ', ''), directory)
            assert result.returncode == 0, result.stdout + result.stderr
            broken, = [n['props'] for n in FDT((directory / 'panel.dts.dtb').read_bytes()).nodes]
            assert 'linux,default-trigger' not in broken, broken
            assert broken['1,default-trigger'] == b'default-on\0', broken
            print('PASS: removing -undef reproduces linux,default-trigger becoming 1,default-trigger')
        else:
            print('NOTE: preprocessor has no Linux builtin; use a Linux target for the macro-collision control')
        # Raw dtc cannot expand these includes/macros. Do not assume that GCC
        # and Clang interpret -fdirectives-only identically in assembler mode.
        result = subprocess.run(dtc + ['-q', '-I', 'dts', '-O', 'dtb', '-o',
                                str(directory / 'raw.dtb'), str(board)],
                                capture_output=True, text=True, timeout=30)
        assert result.returncode != 0, 'raw DTS incorrectly passed without preprocessing'
        board.write_text('/dts-v1/;\n/ { broken = <; };\n', encoding='utf-8')
        result = run(script, directory)
        assert result.returncode != 0, 'invalid DTS incorrectly passed the gate'
        board.write_text('/dts-v1/;\n#include "missing.h"\n/ {};\n', encoding='utf-8')
        result = run(script, directory)
        assert result.returncode != 0, 'missing include incorrectly passed the gate'
    print('PASS: workflow DTS harness expands real macros and rejects raw DTS, syntax and include failures')


def reporting_tests(steps):
    source = next(s['run'] for s in steps if s.get('name') ==
                  'Gate: declared LED topology and software negative controls')
    commands = ('python3 tools/check-sk-lan1-led.py --dtb /tmp/zx279133-sk-d840n.dts.dtb',
                'python3 tests/test_sk_lan1_led.py --dtb /tmp/zx279133-sk-d840n.dts.dtb')
    python = shlex.quote(Path(sys.executable).as_posix())
    for statuses in ((0, 0), (7, 0), (0, 9)):
        with tempfile.TemporaryDirectory(prefix='sk-led-report-') as tmp:
            # Replace the diagnostic interpreter before adding mock commands;
            # otherwise /usr/bin/python3 -c would be replaced a second time.
            script = source.replace('python3 -c ', python + ' -c ')
            for index, (command, status) in enumerate(zip(commands, statuses)):
                program = f'print("gate{index}: 50%\\nline2"); raise SystemExit({status})'
                script = script.replace(command, python + ' -c ' + shlex.quote(program))
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


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cpp', default='cpp', help='preprocessor command, e.g. "zig cc -E"')
    parser.add_argument('--dtc', nargs='+', default=['dtc'])
    args = parser.parse_args()
    steps = yaml.safe_load(WORKFLOW.read_text(encoding='utf-8'))['jobs']['build']['steps']
    clone_tests(steps)
    compile_tests(steps, shlex.split(args.cpp), args.dtc)
    reporting_tests(steps)


if __name__ == '__main__':
    try:
        main()
    except Exception:
        # Public Actions annotations remain readable when raw logs require login.
        message = traceback.format_exc()[-6000:]
        message = message.replace('%', '%25').replace('\r', '').replace('\n', '%0A')
        print('::error::LED CI regression failed: ' + message, flush=True)
        raise
