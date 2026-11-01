#!/usr/bin/env python3
"""Run the shipped AArch64/musl daemon's real libuci/Rust path, without hardware."""
import argparse
import os
from pathlib import Path
import subprocess
import tempfile


def run(root, qemu):
    with tempfile.TemporaryDirectory(prefix='sk-pon-target-') as tmp:
        directory = Path(tmp)

        def check(config, expected):
            (directory / 'pon').write_text(config, encoding='utf-8')
            result = subprocess.run([qemu, '-L', str(root), str(root / 'usr/sbin/zxpond'), '--check', tmp],
                                    cwd=tmp, env={**os.environ, 'QEMU_LD_PREFIX': str(root)},
                                    capture_output=True, text=True, timeout=20)
            assert result.returncode == expected, (result.returncode, result.stderr)
            assert not result.stdout and not result.stderr

        valid = """config pon 'pon'
    option enabled '1'
    option mode 'xgpon'
    option serial_number 'TEST01020304'
    option registration_id_format 'text'
    option registration_id 'fixture-registration'
    option equipment_id 'SK-D840N'
    option omci_psk '000102030405060708090a0b0c0d0e0f'
"""
        check(valid, 0)
        for before, after in [
            ('TEST01020304', '5445535401020304'),
            ("option enabled '1'", "option enabled '0'"),
            ("option registration_id 'fixture-registration'", "option registration_id ''"),
            ("option equipment_id 'SK-D840N'", "option equipment_id '设备'"),
        ]:
            check(valid.replace(before, after), 0)
        for before, after in [
            ('TEST01020304', 'TEST0102030x'),
            ("config pon 'pon'", "config wrong 'pon'"),
            ("option enabled '1'", "option enabled '2'"),
            ("option mode 'xgpon'", "option mode 'epon'"),
            ("option registration_id_format 'text'", "option registration_id_format 'hex'"),
            ('fixture-registration', 'x' * 37),
            ("option equipment_id 'SK-D840N'", "option equipment_id '设备设备设备设备'"),
            ('000102030405060708090a0b0c0d0e0f', '0001'),
        ]:
            check(valid.replace(before, after), 1)
        assert not (directory / 'zxpond-status.json').exists()
    print('PASS: shipped AArch64 daemon, musl/libuci and Rust initialization, UTF-8 byte limits and invalid credentials')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('root', type=Path)
    parser.add_argument('--qemu', default='qemu-aarch64')
    args = parser.parse_args()
    run(args.root.resolve(), args.qemu)
