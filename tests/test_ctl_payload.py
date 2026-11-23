#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Negative controls for the gate payload's kernel gate.

The announce payload writes its report into a RAM window that is *live* on the vendor
kernel, so it writes only for the kernel it was built for.  That kernel used to be a
hard-coded series ("6.*"), which means a payload built for one kernel reports
"off-kernel-is-not-the-payload" on any other one - and the RAM mailbox is the only
report channel a mainline payload has (it has no lan bridge, so its TCP push goes
nowhere).  A payload built for the wrong kernel therefore turns a round into silence.

These controls check that:
  * the version comes from the image that is about to be flashed, not from a constant,
  * a payload built for another kernel does not match the staged one,
  * the pattern that lands in the payload's case statement is the resolved one,
  * a file that is not a uImage yields no version rather than a wrong one.
"""
import argparse
import gzip
import importlib.util
import struct
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MAKER = ROOT / 'tools/mkctlram.py'
UIMAGE_MAGIC = 0x27051956

spec = importlib.util.spec_from_file_location('mkctlram', MAKER)
maker = importlib.util.module_from_spec(spec)
spec.loader.exec_module(maker)


def make_uimage(version=None):
    """A uImage whose kernel announces `version`."""
    image = bytearray(b'\x00' * 4096)
    image[0:4] = bytes.fromhex('1f2003d5')
    image[56:60] = b'ARM\x64'
    if version is not None:
        text = ('Linux version %s (builder@host) (aarch64-linux-gcc 13.3.0) 13.3.0, GNU ld 2.42\n'
                % version).encode()
        image[1024:1024 + len(text)] = text
    body = gzip.compress(bytes(image))
    name = ('ARM64 test Linux-%s' % (version or 'none')).encode()[:32]
    header = struct.pack('>7I', UIMAGE_MAGIC, 0, 0, len(body), 0x80000000, 0, 0)
    header = (header + name.ljust(32, b'\x00')).ljust(64, b'\x00')
    return header + body


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args()

    failures = []

    def check(name, condition, detail=''):
        if condition:
            print(f'PASS: {name}')
        else:
            print(f'FAIL: {name}: {detail}')
            failures.append(name)

    with tempfile.TemporaryDirectory(prefix='sk-payload-') as tmp:
        tmp = Path(tmp)
        u73 = tmp / 'flash-uImage.73'
        u73.write_bytes(make_uimage('7.3.4'))
        u618 = tmp / 'flash-uImage.618'
        u618.write_bytes(make_uimage('6.18.38+'))
        junk = tmp / 'not-a-uimage'
        junk.write_bytes(b'\x00' * 128)

        # The version comes from the image.
        check('the 7.3 image declares 7.3.4', maker.kernel_version(u73) == '7.3.4',
              repr(maker.kernel_version(u73)))
        check('the 6.18 image declares 6.18.38+', maker.kernel_version(u618) == '6.18.38+',
              repr(maker.kernel_version(u618)))
        check('a file that is not a uImage declares nothing',
              maker.kernel_version(junk) is None, repr(maker.kernel_version(junk)))
        check('a file that is not there declares nothing',
              maker.kernel_version(tmp / 'missing') is None)

        # The payload rides the image that is about to be flashed.
        check('the payload rides the staged 7.3 image',
              maker.resolve_kver(None, u73) == '7.3.4*', repr(maker.resolve_kver(None, u73)))
        check('the payload rides the staged 6.18 image',
              maker.resolve_kver(None, u618) == '6.18.38+*', repr(maker.resolve_kver(None, u618)))
        check('an explicit request wins over the image',
              maker.resolve_kver('9.9.9*', u73) == '9.9.9*', repr(maker.resolve_kver('9.9.9*', u73)))
        check('no image and no request yields no kernel',
              maker.resolve_kver(None, junk) is None, repr(maker.resolve_kver(None, junk)))

        # The pattern that lands in the payload is the resolved one.
        init = maker.INIT_TMPL.format(tag='K1', kver='7.3.4*')
        check('the payload gates on the resolved version',
              '7.3.4*) PAYLOAD=1' in init, 'the case statement does not carry the pattern')
        check('the payload no longer gates on a series',
              '6.*) PAYLOAD=1' not in init, 'the old hard-coded series is still in the template')
        check('the vendor kernel still does not match',
              '4.19' not in init.split('case "$(uname -r)" in')[1].split('esac')[0],
              'the vendor kernel would be admitted')

    if failures:
        print(f'{len(failures)} control(s) failed')
        return 1
    print('PASS: the gate payload rides the kernel it was built for')
    return 0


if __name__ == '__main__':
    sys.exit(main())
