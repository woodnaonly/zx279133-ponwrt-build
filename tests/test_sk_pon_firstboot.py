#!/usr/bin/env python3
"""Exercise one-time theme selection and the conservative legacy-NAT matcher."""
import argparse
import copy
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / 'overlays/target/linux/zte/zx279133/base-files'
THEME = ROOT / 'overlays/package/luci-app-sk-d840n-pon/root/etc/uci-defaults/95-skd840n-argon'
MARKER = 'luci.main.skd840n_argon_migrated'

# Model saved config separately from cached UCI deltas. In particular, a failed
# commit must not leave a cached v2 marker that would suppress the next attempt.
UCI_MOCK = '''import json
import os
from pathlib import Path
import sys

path = Path(os.environ['SK_TEST_STATE']) / 'state.json'
state = json.loads(path.read_text(encoding='utf-8'))
args = sys.argv[1:]
if args and args[0] == '-q':
    args = args[1:]
state['calls'].append(args)

def finish(code=0, output=None):
    path.write_text(json.dumps(state), encoding='utf-8')
    if output is not None:
        print(output)
    sys.exit(code)

failure = os.environ.get('SK_TEST_FAIL', '')
if failure == ' '.join(args) or (
        failure == 'marker-commit' and args == ['commit', 'luci'] and
        state['pending'].get('luci.main.skd840n_argon_migrated') == '2'):
    finish(1)

current = {**state['committed'], **state['pending']}
current = {key: value for key, value in current.items() if value is not None}
command = args[0]
if command == 'get':
    if args[1] not in current:
        finish(1)
    finish(output=current[args[1]])
elif command == 'add' and args[1:] == ['argon', 'global']:
    state['pending']['argon.@global[0]'] = 'global'
    finish(output='cfgfixture')
elif command == 'set':
    key, value = args[1].split('=', 1)
    state['pending'][key] = value
elif command == 'delete':
    key = args[1]
    keys = [item for item in current if item == key or item.startswith(key + '.')]
    if not keys:
        finish(1)
    for item in keys:
        state['pending'][item] = None
elif command == 'commit':
    prefix = args[1] + '.'
    for key in list(state['pending']):
        if not key.startswith(prefix):
            continue
        value = state['pending'].pop(key)
        if value is None:
            state['committed'].pop(key, None)
        else:
            state['committed'][key] = value
elif command == 'revert':
    state['pending'].pop(args[1], None)
else:
    finish(1)
finish()
'''


def theme_test():
    bash = shutil.which('bash')
    if os.name == 'nt':
        bash = r'C:\Program Files\Git\bin\bash.exe'
    with tempfile.TemporaryDirectory(prefix='sk-pon-boot-') as tmp:
        directory = Path(tmp)
        # All filesystem/UCI changes are confined to the fixture, never the host.
        (directory / 'uci').write_text('''#!/bin/sh
exec "$SK_TEST_PYTHON" "$SK_TEST_STATE/uci_mock.py" "$@"
''', encoding='utf-8', newline='\n')
        (directory / 'uci').chmod(0o755)
        (directory / 'uci_mock.py').write_text(UCI_MOCK, encoding='utf-8', newline='\n')
        source = THEME.read_text(encoding='utf-8').replace(
            '[ -f /www/luci-static/argon/css/cascade.css ]', '[ -f "$SK_TEST_STATE/cascade.css" ]')
        script = directory / 'theme.sh'
        script.write_text(source, encoding='utf-8', newline='\n')
        env = {**os.environ, 'IPKG_INSTROOT': '', 'PKG_ROOT': '',
               'SK_TEST_FAIL': '', 'SK_TEST_PYTHON': Path(sys.executable).as_posix(),
               'MSYS2_ARG_CONV_EXCL': '*',
               'SK_TEST_STATE': directory.as_posix(),
               'PATH': directory.as_posix() + os.pathsep + os.environ['PATH']}
        state_path = directory / 'state.json'
        base = {'luci.main': 'core', 'luci.main.mediaurlbase': '/luci-static/bootstrap',
                'luci.main.lang': 'en', 'luci.main.resourcebase': '/luci-static/resources',
                'luci.themes': 'internal', 'luci.themes.Bootstrap': '/luci-static/bootstrap',
                'luci.themes.BootstrapDark': '/luci-static/bootstrap-dark',
                'luci.themes.BootstrapLight': '/luci-static/bootstrap-light',
                'luci.themes.Material': '/luci-static/material',
                'argon.@global[0]': 'global', 'argon.@global[0].mode': 'normal',
                'argon.@global[0].primary': '#123456'}

        def reset(values):
            state_path.write_text(json.dumps({'committed': values, 'pending': {}, 'calls': []}),
                                  encoding='utf-8')

        def state():
            return json.loads(state_path.read_text(encoding='utf-8'))

        def run(expected=0, **overrides):
            result = subprocess.run([bash, str(script)], env={**env, **overrides},
                                    capture_output=True, text=True, timeout=30)
            assert result.returncode == expected, (result.returncode, result.stdout, result.stderr)
            return state()

        def check_defaults(saved, primary='#123456'):
            values = saved['committed']
            assert values[MARKER] == '2'
            assert values['argon.@global[0].mode'] == 'light'
            assert values['luci.main.lang'] == 'zh_cn'
            assert values['luci.main.mediaurlbase'] == '/luci-static/argon'
            assert values['luci.themes'] == 'internal'
            assert {k: v for k, v in values.items() if k.startswith('luci.themes.')} == {
                'luci.themes.Argon': '/luci-static/argon'}
            assert values['luci.main.resourcebase'] == base['luci.main.resourcebase']
            assert values.get('argon.@global[0].primary') == primary
            assert not saved['pending']
            marker_write = saved['calls'].index(['set', MARKER + '=2'])
            assert saved['calls'].index(['commit', 'argon']) < marker_write
            assert saved['calls'].index(['commit', 'luci']) < marker_write

        reset(base)
        saved = run(1)
        assert saved['committed'] == base and not saved['pending']
        assert all(call[0] == 'get' for call in saved['calls'])
        (directory / 'cascade.css').touch()
        for name in ('IPKG_INSTROOT', 'PKG_ROOT'):
            reset(base)
            assert not run(1, **{name: tmp})['calls']

        for marker, mode in ((None, 'normal'), (None, 'dark'), ('1', 'normal'), ('1', 'dark')):
            values = {**base, 'argon.@global[0].mode': mode}
            if marker is not None:
                values[MARKER] = marker
            reset(values)
            check_defaults(run())

        # A valid but empty Argon config gains its required global section.
        reset({key: value for key, value in base.items() if not key.startswith('argon.')})
        check_defaults(run(), primary=None)
        reset({key: value for key, value in base.items() if not key.startswith('luci.themes')})
        check_defaults(run())

        # Subsequent runs preserve the user's later language/appearance choices.
        previous = state()['committed']
        previous.update({'argon.@global[0].mode': 'dark', 'luci.main.lang': 'en'})
        reset(previous)
        saved = run()
        assert saved['committed'] == previous and not saved['pending']
        assert saved['calls'] == [['get', MARKER]]

        for failure in ('get luci.main', 'set argon.@global[0].mode=light', 'commit argon',
                        'delete luci.themes', 'set luci.themes=internal',
                        'set luci.themes.Argon=/luci-static/argon',
                        'set luci.main.mediaurlbase=/luci-static/argon',
                        'set luci.main.lang=zh_cn', 'commit luci',
                        'set ' + MARKER + '=2', 'marker-commit'):
            reset({**base, MARKER: '1'})
            saved = run(1, SK_TEST_FAIL=failure)
            assert saved['committed'][MARKER] == '1', failure
            assert saved['pending'].get(MARKER) != '2', failure
            check_defaults(run())
    print('PASS: Argon-only light/Chinese defaults, v1 upgrade, preserved later choices, build-host guards and failed-write retries')


def nat_test(ucode):
    script = BASE / 'usr/libexec/sk-nat-owned.uc'
    old = {'nftables': [
        {'metainfo': {'json_schema_version': 1}},
        {'table': {'family': 'ip', 'name': 'nat', 'handle': 1}},
        {'chain': {'family': 'ip', 'table': 'nat', 'name': 'postrouting', 'type': 'nat',
                   'hook': 'postrouting', 'prio': 100, 'policy': 'accept'}},
        {'rule': {'family': 'ip', 'table': 'nat', 'chain': 'postrouting', 'expr': [
            {'match': {'op': '==', 'left': {'meta': {'key': 'oifname'}}, 'right': 'eth0'}},
            # nftables 1.1.6 tests/py/ip/masquerade.t.json uses this exact key.
            {'counter': {'packets': 0, 'bytes': 0}}, {'masquerade': None}]}}]}

    def result(document):
        return subprocess.run([ucode, str(script)], input=json.dumps(document), text=True,
                              capture_output=True).returncode

    assert result(old) == 0
    for index in range(4):
        altered = copy.deepcopy(old)
        del altered['nftables'][index]
        # Metainfo is optional, the actual table/chain/rule are not.
        assert result(altered) == (0 if index == 0 else 1), index
    for added in ({'rule': copy.deepcopy(old['nftables'][-1]['rule'])},
                  {'set': {'family': 'ip', 'table': 'nat', 'name': 'user'}}):
        assert result({'nftables': [*old['nftables'], added]}) == 1
    for expression in ({'accept': None}, {'masquerade': {'flags': ['random']}}, {'masq': None}, {'drop': None}):
        altered = copy.deepcopy(old)
        altered['nftables'][-1]['rule']['expr'][-1] = expression
        assert result(altered) == 1
    for value in (None, [], {}, {'nftables': []}):
        assert result(value) == 1
    print('PASS: legacy sk-nat exact signature accepted; custom/extended/invalid rules preserved')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--ucode')
    args = parser.parse_args()
    theme_test()
    if args.ucode:
        nat_test(args.ucode)
