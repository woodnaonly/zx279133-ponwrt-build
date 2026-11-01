#!/usr/bin/env python3
"""Exercise one-time theme selection and the conservative legacy-NAT matcher."""
import argparse
import copy
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / 'overlays/target/linux/zte/zx279133/base-files'
THEME = ROOT / 'overlays/package/luci-app-sk-d840n-pon/root/etc/uci-defaults/95-skd840n-argon'


def theme_test():
    bash = shutil.which('bash')
    if os.name == 'nt':
        bash = r'C:\Program Files\Git\bin\bash.exe'
    with tempfile.TemporaryDirectory(prefix='sk-pon-boot-') as tmp:
        directory = Path(tmp)
        # All filesystem/uci changes are confined to the fixture, never the host.
        (directory / 'uci').write_text('''#!/bin/sh
if [ "$1" = '-q' ]; then
    [ -f "$SK_TEST_STATE/marker" ] || exit 1
    echo 1
elif [ "$1" = batch ]; then
    tee "$SK_TEST_STATE/batch"
    touch "$SK_TEST_STATE/marker"
else exit 1
fi
''', encoding='utf-8')
        (directory / 'uci').chmod(0o755)
        source = THEME.read_text(encoding='utf-8').replace(
            '[ -f /www/luci-static/argon/css/cascade.css ]', '[ -f "$SK_TEST_STATE/cascade.css" ]')
        script = directory / 'theme.sh'
        script.write_text(source, encoding='utf-8')
        env = {**os.environ, 'IPKG_INSTROOT': '', 'PKG_ROOT': '',
               'SK_TEST_STATE': directory.as_posix(),
               'PATH': directory.as_posix() + os.pathsep + os.environ['PATH']}
        assert subprocess.run([bash, str(script)], env=env, capture_output=True).returncode == 1
        assert not (directory / 'batch').exists()
        (directory / 'cascade.css').touch()
        subprocess.run([bash, str(script)], env=env, check=True, capture_output=True)
        batch = (directory / 'batch').read_text(encoding='utf-8')
        assert "set luci.main.mediaurlbase='/luci-static/argon'" in batch
        (directory / 'batch').unlink()
        # A later user-selected theme is left untouched once the marker exists.
        subprocess.run([bash, str(script)], env=env, check=True, capture_output=True)
        assert not (directory / 'batch').exists()
        assert subprocess.run([bash, str(script)], env={**env, 'IPKG_INSTROOT': tmp}, capture_output=True).returncode == 1
    print('PASS: absent theme, first install, retained later user theme, no build-host UCI mutation')


def nat_test(ucode):
    script = BASE / 'usr/libexec/sk-nat-owned.uc'
    old = {'nftables': [
        {'metainfo': {'json_schema_version': 1}},
        {'table': {'family': 'ip', 'name': 'nat', 'handle': 1}},
        {'chain': {'family': 'ip', 'table': 'nat', 'name': 'postrouting', 'type': 'nat',
                   'hook': 'postrouting', 'prio': 100, 'policy': 'accept'}},
        {'rule': {'family': 'ip', 'table': 'nat', 'chain': 'postrouting', 'expr': [
            {'match': {'op': '==', 'left': {'meta': {'key': 'oifname'}}, 'right': 'eth0'}},
            {'counter': {'packets': 0, 'bytes': 0}}, {'masq': None}]}}]}

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
    for expression in ({'accept': None}, {'masq': {'flags': ['random']}}, {'drop': None}):
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
