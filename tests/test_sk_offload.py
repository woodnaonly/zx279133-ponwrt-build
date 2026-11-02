#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Exercise the real pinning helpers and reject incomplete kernel/image outputs."""
import argparse
import contextlib
import importlib.util
import io
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('offload_gate', ROOT / 'tools/check-sk-offload.py')
gate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gate)
HELPER = ROOT / 'scripts/sk-d840n-offload.sh'
SHELL = r'C:\Program Files\Git\bin\bash.exe' if os.name == 'nt' else shutil.which('sh')
KERNEL_GIT = None


def quiet_call(function, *args):
    with contextlib.redirect_stdout(io.StringIO()):
        return function(*args)


class OffloadTests(unittest.TestCase):
    def test_profile_contains_both_offload_paths(self):
        required = set(gate.required_symbols())
        self.assertTrue({'NETFILTER_INGRESS', 'NF_FLOW_TABLE', 'NF_FLOW_TABLE_INET',
                         'NFT_FLOW_OFFLOAD', 'NET_SCHED', 'NET_CLS_ACT', 'NET_CLS_FLOWER',
                         'NET_SCH_INGRESS', 'NET_XGRESS', 'NET_ACT_MIRRED', 'NET_ACT_PEDIT',
                         'NET_ACT_CSUM'} <= required)
        self.assertNotIn('NET_ACT_CT', required)  # No unsupported action is implied by enabling TC.

    def test_profile_rejects_empty_module_unknown_and_duplicate_lines(self):
        with tempfile.TemporaryDirectory(prefix='sk-offload-profile-') as tmp:
            path = Path(tmp) / 'profile'
            for content in ('# empty\n', 'CONFIG_NF_FLOW_TABLE=m\n', 'typo\n',
                            'CONFIG_NET_SCHED=y\nCONFIG_NET_SCHED=y\n'):
                path.write_text(content, encoding='utf-8')
                with self.subTest(content=content), self.assertRaises(ValueError):
                    gate.required_symbols(path)

    def test_merged_gate_checks_every_symbol_and_last_override(self):
        required = gate.required_symbols()
        good = ''.join(f'CONFIG_{name}=y\n' for name in required)
        quiet_call(gate.validate_config, good)
        for name in required:
            original = f'CONFIG_{name}=y\n'
            for replacement in ('', f'CONFIG_{name}=m\n', f'# CONFIG_{name} is not set\n'):
                with self.subTest(symbol=name, replacement=replacement), self.assertRaises(ValueError):
                    quiet_call(gate.validate_config, good.replace(original, replacement))
        with self.assertRaisesRegex(ValueError, 'CONFIG_NETFILTER_INGRESS=n'):
            quiet_call(gate.validate_config, good + '# CONFIG_NETFILTER_INGRESS is not set\n')

    def test_cli_checks_stdin_and_rejects_incomplete_arguments(self):
        program = str(ROOT / 'tools/check-sk-offload.py')
        good = gate.PROFILE.read_text(encoding='utf-8')
        for source, status in ((good, 0), (good + '# CONFIG_NET_CLS_FLOWER is not set\n', 1)):
            result = subprocess.run([sys.executable, program, '--kernel-config', '-'],
                                    input=source, text=True, capture_output=True, timeout=10)
            self.assertEqual(result.returncode, status, result.stdout + result.stderr)
        self.assertIn('CONFIG_NET_CLS_FLOWER=n', result.stderr)
        result = subprocess.run([sys.executable, program, '--root', '.'],
                                text=True, capture_output=True, timeout=10)
        self.assertEqual(result.returncode, 2)

    def run_helper(self, directory, commands):
        result = subprocess.run([SHELL, '-c', 'set -eu\n. "$1"\n' + commands,
                                 'offload-test', HELPER.as_posix(), directory.as_posix(),
                                 gate.PROFILE.as_posix()], capture_output=True, text=True, timeout=60)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_real_fragment_and_build_root_merge_is_idempotent(self):
        with tempfile.TemporaryDirectory(prefix='sk offload merge ') as tmp:
            directory = Path(tmp)
            fragments = [directory / f'target/linux/{name}/config-6.18'
                         for name in ('generic', 'zte', 'zte/zx279133')]
            for path in fragments:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text('# keep comment\nCONFIG_UNRELATED=m\nCONFIG_NET_CLS_ACT=m\n'
                                '# CONFIG_NET_CLS_ACT is not set\n# CONFIG_NETFILTER_INGRESS is not set\n',
                                encoding='utf-8', newline='\n')
            build = directory / '.config'
            build.write_text('CONFIG_PACKAGE_tc-tiny=y\n# CONFIG_KERNEL_NETFILTER_INGRESS is not set\n'
                             'CONFIG_KERNEL_NET_CLS_ACT=m\nCONFIG_KERNEL_NET_CLS_ACT=n\n'
                             '# CONFIG_KERNEL_UNRELATED is not set\n', encoding='utf-8', newline='\n')
            command = 'skd840n_pin_offload_fragments "$2" "$3"\nskd840n_pin_offload_build_config "$2/.config" "$3"\n'
            self.run_helper(directory, command)
            files = fragments + [build]
            first = [p.read_bytes() for p in files]
            self.run_helper(directory, command)
            self.assertEqual(first, [p.read_bytes() for p in files])
            for path in fragments:
                source = path.read_text(encoding='utf-8')
                quiet_call(gate.validate_config, source)
                self.assertIn('CONFIG_UNRELATED=m\n', source)
                for name in gate.required_symbols():
                    self.assertEqual(source.count(f'CONFIG_{name}=y\n'), 1)
            root_config = build.read_text(encoding='utf-8')
            self.assertIn('CONFIG_KERNEL_NETFILTER_INGRESS=y\n', root_config)
            self.assertIn('CONFIG_KERNEL_NET_CLS_ACT=y\n', root_config)
            self.assertIn('# CONFIG_KERNEL_UNRELATED is not set\n', root_config)
            self.assertNotIn('CONFIG_KERNEL_NET_CLS_FLOWER', root_config)
            # Reproduce include/kernel-defaults.mk, not just the fragment pin.
            merged = ''.join(p.read_text(encoding='utf-8') for p in fragments)
            merged += '\n'.join(line.replace('CONFIG_KERNEL_', 'CONFIG_', 1)
                                for line in root_config.splitlines()
                                if re.match(r'^(# )?CONFIG_KERNEL_', line))
            quiet_call(gate.validate_config, merged)

    def test_source_symbol_gate(self):
        with tempfile.TemporaryDirectory(prefix='sk-offload-kconfig-') as tmp:
            tree = Path(tmp)
            for name in gate.KCONFIGS:
                path = tree / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text('', encoding='utf-8')
            config = tree / gate.KCONFIGS[0]
            definitions = ''.join(f'config {name}\n\tbool\n' for name in gate.required_symbols())
            config.write_text(definitions, encoding='utf-8')
            quiet_call(gate.validate_kernel_tree, tree)
            config.write_text(definitions.replace('config NET_SCH_INGRESS\n', 'config WRONG_NAME\n'), encoding='utf-8')
            with self.assertRaisesRegex(ValueError, 'NET_SCH_INGRESS'):
                quiet_call(gate.validate_kernel_tree, tree)

    def test_missing_target_config_fails_without_touching_another_file(self):
        with tempfile.TemporaryDirectory(prefix='sk-offload-missing-') as tmp:
            tree = Path(tmp)
            config = tree / 'target/linux/generic/config-6.18'
            config.parent.mkdir(parents=True)
            config.write_bytes(b'# CONFIG_NET_CLS_ACT is not set\n')
            result = subprocess.run([SHELL, '-c', '. "$1"\nskd840n_pin_offload_fragments "$2" "$3"',
                                     'offload-test', HELPER.as_posix(), tree.as_posix(), gate.PROFILE.as_posix()],
                                    text=True, capture_output=True, timeout=10)
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(config.read_bytes(), b'# CONFIG_NET_CLS_ACT is not set\n')

    def test_image_requires_tools_and_manifest_entries(self):
        with tempfile.TemporaryDirectory(prefix='sk-offload-image-') as tmp:
            root = Path(tmp)
            manifest = root / 'image.manifest'
            manifest.write_text('\n'.join(name + ' - 1.0' for name in gate.PACKAGES), encoding='utf-8')
            tools = [root / name for name in ('usr/libexec/tc-tiny', 'usr/sbin/nft', 'sbin/fw4')]
            for path in tools:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b'tool\n')
                path.chmod(0o755)
            # NTFS chmod cannot express POSIX executable bits. The shipped
            # root gate runs on Linux; adapt only the test fixture's stat.
            original_stat = Path.stat

            def executable_stat(path, *args, **kwargs):
                result = original_stat(path, *args, **kwargs)
                if os.name == 'nt' and path in tools:
                    values = list(result)
                    values[0] |= 0o111
                    return os.stat_result(values)
                return result

            with mock.patch.object(Path, 'stat', executable_stat):
                quiet_call(gate.validate_image, root, manifest)
                for path in tools:
                    path.write_bytes(b'')
                    with self.subTest(path=path), self.assertRaises(ValueError):
                        quiet_call(gate.validate_image, root, manifest)
                    path.write_bytes(b'tool\n')
                for package in gate.PACKAGES:
                    manifest.write_text('\n'.join(p + ' - 1.0' for p in gate.PACKAGES if p != package), encoding='utf-8')
                    with self.subTest(package=package), self.assertRaises(ValueError):
                        quiet_call(gate.validate_image, root, manifest)

    def test_configure_and_ci_reach_the_gates(self):
        configure = (ROOT / 'scripts/configure-sk-d840n.sh').read_text(encoding='utf-8')
        self.assertIn('. "$SCRIPT_DIR/sk-d840n-offload.sh"', configure)
        self.assertIn('skd840n_pin_offload_fragments . "$OFFLOAD_PROFILE"', configure)
        self.assertIn('make defconfig\nskd840n_pin_offload_build_config .config "$OFFLOAD_PROFILE"', configure)
        workflow = (ROOT / '.github/workflows/build-sk-d840n.yml').read_text(encoding='utf-8')
        self.assertIn('python3 tests/test_sk_offload.py', workflow)
        self.assertIn('python3 tools/check-sk-offload.py --kernel-tree "$KT"', workflow)
        self.assertIn('python3 tools/check-sk-offload.py --kernel-config "$KC"', workflow)
        self.assertIn('tests/*sk_offload*', workflow)
        image_gate = (ROOT / 'scripts/check-sk-d840n-management.sh').read_text(encoding='utf-8')
        self.assertIn('python3 tools/check-sk-offload.py --root "$ROOT" --manifest "$MANIFEST"', image_gate)

    def test_profile_against_real_pinned_kconfig(self):
        if KERNEL_GIT is None:
            self.skipTest('optional pinned-kernel checkout not supplied')
        workflow = (ROOT / '.github/workflows/build-sk-d840n.yml').read_text(encoding='utf-8')
        pin = re.search(r'^  KERNEL_SHA: ([0-9a-f]{40})$', workflow, re.M)[1]
        head = subprocess.check_output(['git', '-C', str(KERNEL_GIT), 'rev-parse', 'HEAD'], text=True).strip()
        self.assertEqual(head, pin, 'source validation must use the CI-pinned kernel')
        with tempfile.TemporaryDirectory(prefix='sk-offload-pinned-') as tmp:
            tree = Path(tmp)
            for name in gate.KCONFIGS:
                data = subprocess.check_output(['git', '-C', str(KERNEL_GIT), 'show', 'HEAD:' + name])
                path = tree / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(data)
            quiet_call(gate.validate_kernel_tree, tree)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument('--kernel-git', type=Path)
    options, remainder = parser.parse_known_args()
    KERNEL_GIT = options.kernel_git
    unittest.main(argv=[sys.argv[0], *remainder])
