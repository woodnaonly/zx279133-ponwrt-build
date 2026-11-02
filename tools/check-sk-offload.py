#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Check offload prerequisites in the real kernel config and packaged image.

These gates prove availability, not hardware hits or throughput. Hardware
offload remains opt-in until bidirectional traffic is tested on the board.
"""
import argparse
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
PROFILE = ROOT / 'configs/sk-d840n-offload.config'
PACKAGES = ('tc-tiny', 'nftables', 'firewall4')
KCONFIGS = ('net/Kconfig', 'net/netfilter/Kconfig',
            'net/ipv4/netfilter/Kconfig', 'net/ipv6/netfilter/Kconfig',
            'net/sched/Kconfig', 'drivers/net/Kconfig')


def required_symbols(profile=PROFILE):
    symbols = []
    for line in profile.read_text(encoding='utf-8').splitlines():
        if not line.strip() or line.startswith('#'):
            continue
        match = re.fullmatch(r'CONFIG_([A-Z0-9_]+)=y', line)
        if not match or match[1] in symbols:
            raise ValueError(f'invalid or duplicate offload profile entry: {line!r}')
        symbols.append(match[1])
    if not symbols:
        raise ValueError('empty offload profile')
    return symbols


def config_values(source):
    values = {}
    for line in source.splitlines():
        entry = re.fullmatch(r'CONFIG_([A-Z0-9_]+)=(.*)', line)
        disabled = re.fullmatch(r'# CONFIG_([A-Z0-9_]+) is not set', line)
        if entry:
            values[entry[1]] = entry[2]
        elif disabled:
            values[disabled[1]] = 'n'
    return values


def validate_config(source, symbols=None):
    values = config_values(source)
    failures = [f'CONFIG_{name}={values.get(name, "<absent>")}'
                for name in (required_symbols() if symbols is None else symbols)
                if values.get(name) != 'y']
    if failures:
        raise ValueError('offload prerequisites must be built in:\n  ' + '\n  '.join(failures))
    print('PASS: merged kernel has built-in nft flowtable and TC flower/clsact/actions')


def validate_kernel_tree(tree):
    definitions = set()
    for name in KCONFIGS:
        path = tree / name
        if not path.is_file():
            raise ValueError(f'missing pinned-kernel Kconfig: {path}')
        definitions.update(re.findall(r'^\s*(?:menu)?config\s+([A-Z0-9_]+)\s*$',
                                      path.read_text(encoding='utf-8'), re.M))
    missing = set(required_symbols()) - definitions
    if missing:
        raise ValueError('offload profile names nonexistent kernel symbols: ' + ', '.join(sorted(missing)))
    print('PASS: every offload profile symbol exists in the pinned kernel')


def validate_image(root, manifest):
    packages = {line.split()[0] for line in manifest.read_text(encoding='utf-8').splitlines() if line.strip()}
    missing = set(PACKAGES) - packages
    if missing:
        raise ValueError('image manifest lacks offload userspace: ' + ', '.join(sorted(missing)))
    # Inspect concrete tc-tiny, not an absolute alternatives symlink that
    # would resolve against the build host instead of the image root.
    for name in ('usr/libexec/tc-tiny', 'usr/sbin/nft', 'sbin/fw4'):
        path = root / name
        if not path.is_file() or path.stat().st_size == 0 or not path.stat().st_mode & 0o111:
            raise ValueError(f'image lacks executable offload tool: {name}')
    print('PASS: image contains executable tc-tiny/nft/fw4 and their manifest entries')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--kernel-tree', type=Path)
    parser.add_argument('--kernel-config', help='merged kernel .config, or - for zcat /proc/config.gz on stdin')
    parser.add_argument('--root', type=Path)
    parser.add_argument('--manifest', type=Path)
    args = parser.parse_args()
    if not any((args.kernel_tree, args.kernel_config, args.root)):
        parser.error('supply --kernel-tree, --kernel-config, or --root with --manifest')
    if bool(args.root) != bool(args.manifest):
        parser.error('--root and --manifest must be supplied together')
    try:
        if args.kernel_tree:
            validate_kernel_tree(args.kernel_tree)
        if args.kernel_config:
            validate_config(sys.stdin.read() if args.kernel_config == '-' else
                            Path(args.kernel_config).read_text(encoding='utf-8'))
        if args.root:
            validate_image(args.root, args.manifest)
    except (OSError, ValueError) as error:
        print(f'FAIL: {error}', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
