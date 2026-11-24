#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Does the built kernel image carry the PPU microcode the driver will take?

The tree-level gate (tools/check-sk-mcode.py) answers a question about the
source tree: is the blob that is about to be built in the one this driver
accepts.  That is not the same question as the one the board asks, because the
microcode is a *built-in* firmware: CONFIG_EXTRA_FIRMWARE names a file and
CONFIG_EXTRA_FIRMWARE_DIR says where to look for it, and the kernel build
embeds what it finds there.  The file can be right in the tree and still not be
the one in the image - the path can point somewhere else, the name can be
misspelled, the configuration can be rewritten after the tree was checked.

So this asks the artifact instead of the intent: the kernel image is inflated,
and the blob that is supposed to be in it is looked for byte for byte.  The
crc32 the driver compares against is still read out of the driver source, so
the answer does not depend on this repository knowing which blob is right.
The driver knows two of them - this board's own and the one the RX-hash hook
may be installed on - so the check against this board's own CRC is the one
that tells them apart.

The microcode is stored uncompressed (the kernel's own Kconfig says so:
"NOTE: Compressed files are not supported in EXTRA_FIRMWARE"), which is why
the blob can be found as plain bytes in the inflated image.

The bytes alone are not enough, though: the kernel's builtin firmware
Makefile emits the file's *name* next to the blob, and that string is what
makes the blob a firmware the loader can find by name.  An image that
carried the bytes some other way would have them in it and still leave
request_firmware() with nothing to hand over.  So the name has to be there
as well, and it is read out of the configuration the build used rather than
written down here.

Usage:
  python3 tools/check-sk-mcode-image.py --image IMAGE --tree TREE [--blob FILE]
                                        [--config CONFIG] [--name NAME]
"""
import argparse
import gzip
import importlib.util
import re
import struct
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
UIMAGE_MAGIC = 0x27051956
DEFAULT_NAME = "zte/zx279133/mcode_intel.bin"


def firmware_name(config):
    """The firmware name the configuration asks for, if it says one."""
    if config is None:
        return None
    try:
        text = Path(config).read_text(encoding='utf-8', errors='replace')
    except OSError:
        return None
    match = re.search(r'^CONFIG_EXTRA_FIRMWARE="([^"]*)"', text, re.M)
    return match.group(1) if match else None


def load_tree_gate():
    """The tree-level gate, loaded rather than copied.

    Both gates have to agree about what the driver wants, so they must read
    it the same way.  A second implementation of the same rule is a second
    answer to the same question.
    """
    spec = importlib.util.spec_from_file_location(
        'sk_mcode_tree_gate', HERE / 'check-sk-mcode.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def inflate_uimage(path):
    """The kernel image inside a legacy uImage."""
    raw = Path(path).read_bytes()
    if len(raw) < 64 or raw[0:4] != b'\x27\x05\x19\x56':
        raise ValueError('%s is not a legacy uImage' % path)
    size = struct.unpack('>I', raw[12:16])[0]
    body = raw[64:64 + size]
    if body[:2] != b'\x1f\x8b':
        raise ValueError('%s: the uImage body is not gzip' % path)
    return gzip.decompress(body)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--image', type=Path, required=True,
                        help='the kernel image that was built (a legacy uImage)')
    parser.add_argument('--tree', type=Path, required=True,
                        help='the prepared kernel tree, for the driver source')
    parser.add_argument('--blob', type=Path, default=None,
                        help='the microcode that should be in the image '
                             '(default: the one in the tree)')
    parser.add_argument('--config', type=Path, default=None,
                        help='the kernel configuration the build used; the '
                             'firmware name is read out of it')
    parser.add_argument('--name', default=None,
                        help='the firmware name to look for (default: the '
                             'one in --config, or the board\'s own)')
    args = parser.parse_args(argv)

    gate = load_tree_gate()
    failures = []

    def check(ok, what, detail=''):
        print('%s %s%s' % ('[OK ]' if ok else '[FAIL]', what,
                           '' if ok else ': ' + detail))
        if not ok:
            failures.append(what)

    try:
        wanted = gate.demanded_crcs(args.tree)
    except RuntimeError as exc:
        print('FAIL: %s' % exc)
        return 1
    if not wanted:
        print('FAIL: the driver says nothing about which microcode it wants')
        return 1
    print('the driver wants: %s' % ', '.join('0x%08x' % v for v in sorted(wanted)))

    blob = args.blob or (args.tree / gate.BLOB_IN_TREE)
    if not blob.is_file():
        print('FAIL: no microcode to look for: %s' % blob)
        return 1
    data = blob.read_bytes()
    digest = gate.crc32_le(data)
    print('the microcode: %s (%d B) crc32 0x%08x' % (blob, len(data), digest))

    try:
        image = inflate_uimage(args.image)
    except (OSError, ValueError, struct.error) as exc:
        print('FAIL: %s' % exc)
        return 1
    print('the image: %s (%d B inflated)' % (args.image, len(image)))

    check(digest in wanted,
          'the microcode is one the driver accepts',
          'crc32 0x%08x is not in %s'
          % (digest, ', '.join('0x%08x' % v for v in sorted(wanted))))
    check(digest == gate.BOARD_CRC,
          "the microcode is this board's own",
          'crc32 0x%08x is not this board\'s 0x%08x'
          % (digest, gate.BOARD_CRC))

    at = image.find(data)
    check(at >= 0, 'the image carries the microcode',
          'the %d bytes are not in the image' % len(data))
    if at >= 0:
        print('     found at offset %d (0x%x)' % (at, at))

    # The name is what makes the blob a firmware the loader can find, so the
    # bytes alone do not answer the question.
    name = args.name or firmware_name(args.config) or DEFAULT_NAME
    where = 'the configuration' if args.name is None and args.config else 'the default'
    print('the firmware name: %s (%s)' % (name, where))
    named = image.find(name.encode())
    check(named >= 0, 'the image names the firmware',
          '%s is not in the image, so request_firmware() would not find it'
          % name)
    if named >= 0:
        print('     found at offset %d (0x%x)' % (named, named))

    if failures:
        print('%d check(s) failed' % len(failures))
        return 1
    print("OK: the image carries the microcode this board's own driver will take")
    return 0


if __name__ == '__main__':
    sys.exit(main())
