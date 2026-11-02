#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Exercise the actual SK-D840N theme config/image gates with isolated fixtures."""
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
HELPER = ROOT / 'scripts/sk-d840n-theme.sh'
SHELL = r'C:\Program Files\Git\bin\bash.exe' if os.name == 'nt' else shutil.which('bash')
PACKAGES = ('luci-theme-argon', 'luci-i18n-base-zh-cn',
            'luci-i18n-argon-config-zh-cn', 'luci-i18n-firewall-zh-cn')
FILES = ('etc/config/argon', 'etc/uci-defaults/95-skd840n-argon',
         'www/luci-static/argon/css/cascade.css',
         'usr/lib/lua/luci/i18n/base.zh-cn.lmo',
         'usr/lib/lua/luci/i18n/argon-config.zh-cn.lmo',
         'usr/lib/lua/luci/i18n/firewall.zh-cn.lmo')


def invoke(function, *paths, expected=0):
    command = '. ' + shlex.quote(HELPER.as_posix()) + '\n' + function + ' "$@"'
    result = subprocess.run([SHELL, '-ec', command, 'theme-test',
                             *(path.as_posix() for path in paths)],
                            capture_output=True, text=True, timeout=20)
    assert result.returncode == expected, (function, result.returncode, result.stdout, result.stderr)
    return result.stdout + result.stderr


def config_tests():
    good = 'CONFIG_PACKAGE_luci-theme-argon=y\nCONFIG_LUCI_LANG_zh_Hans=y\n'
    with tempfile.TemporaryDirectory(prefix='sk theme config ') as tmp:
        tree = Path(tmp)
        target = tree / 'target/linux/zte/zx279133/target.mk'
        target.parent.mkdir(parents=True)
        target.write_text('DEFAULT_PACKAGES += \\\n\tluci-base luci-theme-bootstrap luci-theme-material \\\n\tuhttpd\n',
                          encoding='utf-8', newline='\n')
        config = tree / '.config'
        config.write_text(good + 'CONFIG_PACKAGE_luci-theme-bootstrap=y\n'
                          'CONFIG_PACKAGE_luci-theme-material=m\nCONFIG_PACKAGE_uhttpd=y\n',
                          encoding='utf-8', newline='\n')
        invoke('skd840n_select_theme', tree)
        selected = config.read_text(encoding='utf-8')
        defaults = target.read_text(encoding='utf-8')
        assert '# CONFIG_PACKAGE_luci-theme-bootstrap is not set' in selected
        assert '# CONFIG_PACKAGE_luci-theme-material is not set' in selected
        assert 'CONFIG_PACKAGE_uhttpd=y' in selected
        assert set(re.findall(r'luci-theme-[\w-]+', defaults)) == {'luci-theme-argon'}
        assert 'luci-base' in defaults and 'uhttpd' in defaults
        invoke('skd840n_check_theme_config', config)
        invoke('skd840n_select_theme', tree)
        assert config.read_text(encoding='utf-8') == selected
        assert target.read_text(encoding='utf-8') == defaults

        for theme in ('bootstrap', 'material', 'openwrt', 'argon-dark'):
            for value in ('y', 'm'):
                config.write_text(good + f'CONFIG_PACKAGE_luci-theme-{theme}={value}\n',
                                  encoding='utf-8', newline='\n')
                output = invoke('skd840n_check_theme_config', config, expected=1)
                assert 'Only Argon may be selected' in output
        for invalid in (good.replace('argon=y', 'argon=m'),
                        good.replace('CONFIG_PACKAGE_luci-theme-argon=y\n', ''),
                        good.replace('CONFIG_LUCI_LANG_zh_Hans=y\n', '')):
            config.write_text(invalid, encoding='utf-8', newline='\n')
            invoke('skd840n_check_theme_config', config, expected=1)
    print('PASS: default/seed themes replaced, idempotent selection, extra built-in/modular themes and missing Chinese rejected')


def image_tests():
    with tempfile.TemporaryDirectory(prefix='sk theme image ') as tmp:
        tree = Path(tmp)
        root = tree / 'root-zte'
        manifest = tree / 'firmware.manifest'
        for relative in FILES:
            path = root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b'fixture\n')
        lines = [f'{package} - 3.0.0-r1\n' for package in PACKAGES]
        # The integrated config compatibility package is not a second theme.
        lines.append('luci-app-argon-config - 3.0.0-r1\n')
        good = ''.join(lines)
        manifest.write_text(good, encoding='utf-8', newline='\n')
        invoke('skd840n_check_theme_image', root, manifest)
        for theme in ('bootstrap', 'bootstrap-dark', 'bootstrap-light', 'material', 'argon-dark'):
            manifest.write_text(good + f'luci-theme-{theme} - 1.0\n',
                                encoding='utf-8', newline='\n')
            output = invoke('skd840n_check_theme_image', root, manifest, expected=1)
            assert 'Only Argon may be installed' in output
        for package in PACKAGES:
            manifest.write_text(''.join(line for line in lines if not line.startswith(package + ' ')),
                                encoding='utf-8', newline='\n')
            output = invoke('skd840n_check_theme_image', root, manifest, expected=1)
            assert 'is missing ' + package in output
        manifest.write_text(good, encoding='utf-8', newline='\n')
        for relative in FILES:
            path = root / relative
            path.unlink()
            output = invoke('skd840n_check_theme_image', root, manifest, expected=1)
            assert 'is missing ' + relative in output
            path.write_bytes(b'')
            invoke('skd840n_check_theme_image', root, manifest, expected=1)
            path.write_bytes(b'fixture\n')
    print('PASS: image manifest rejects every extra theme and missing translation; missing/empty shipped assets rejected')


def wiring_tests():
    configure = (ROOT / 'scripts/configure-sk-d840n.sh').read_text(encoding='utf-8')
    gate = (ROOT / 'scripts/check-sk-d840n-management.sh').read_text(encoding='utf-8')
    workflow = (ROOT / '.github/workflows/build-sk-d840n.yml').read_text(encoding='utf-8')
    assert '. "$SCRIPT_DIR/sk-d840n-theme.sh"' in configure
    assert configure.index('skd840n_select_theme .\n') < configure.index('make defconfig\n')
    assert configure.index('make defconfig\n') < configure.index('skd840n_check_theme_config .config\n')
    assert '. "$SCRIPT_DIR/sk-d840n-theme.sh"' in gate
    assert 'skd840n_check_theme_image "$ROOT" "$MANIFEST"' in gate
    assert '      - scripts/sk-d840n-theme.sh\n' in workflow
    assert 'python3 tests/test_sk_pon_theme.py &&' in workflow
    sr1010 = (ROOT / '.github/workflows/build-sr1010.yml').read_text(encoding='utf-8')
    assert 'sk-d840n-theme' not in sr1010
    print('PASS: shared configure/image policy and early regression reached by SK-D840N CI only')


if __name__ == '__main__':
    config_tests()
    image_tests()
    wiring_tests()
