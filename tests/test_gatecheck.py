#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Negative controls for the flash gate.

tools/gatecheck.sh is the one-way door in front of an irreversible flash: it lets a
pair of bytes through only when a gate round already booted exactly those bytes on
this board.  The record it reads says which kernel that was (kver=), and that record
has to be about the kernel which is about to be written - not about a kernel of the
same family, and not about the vendor kernel.

The control that matters is the one the old check could not pass.  The version used
to be matched by its first digit ("6."), so a gate record from the 6.18 line
vouched for a 7.3 image - a proof about bytes that were never booted.  The expected
version is now read out of the uImage that is about to be written, so:

  * a record whose kver is the image's own version is allowed,
  * a record whose kver is another version is refused,
  * a record that claims the vendor kernel is refused whatever the image is,
  * an image whose version cannot be read is refused - the gate fails closed,
  * the three fields that make a gate record evidence at all (root_read,
    root_index, jffs2_ro) are still required.

The uImages here are built to look like the real ones: a 64-byte U-Boot header and
a gzip-compressed arm64 Image whose kernel banner sits in .rodata, where the linker
puts it - not at the fixed offset a first attempt assumed, which is why the gate
searches the text instead of slicing it.
"""
import argparse
import gzip
import hashlib
import os
import shutil
import struct
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
# The gate under test.  The environment variable exists so that a mutation of the
# gate can be run against these controls without editing the repository - the
# controls have to be able to turn red, or they are decoration.
GATE = Path(os.environ.get('SK_GATECHECK') or (ROOT / 'tools/gatecheck.sh'))
KVER_TOOL = ROOT / 'tools/uimage-kver.py'
UIMAGE_MAGIC = 0x27051956


def find_sh():
    """A POSIX shell to run the gate with."""
    found = shutil.which('sh')
    if found:
        return found
    for candidate in (r'C:\Program Files\Git\bin\sh.exe',
                      r'C:\Program Files\Git\usr\bin\sh.exe'):
        if Path(candidate).exists():
            return candidate
    return None


def make_uimage(version=None, compressed=True):
    """A uImage whose kernel announces `version` - no banner at all if version is None."""
    image = bytearray(b'\x00' * 4096)
    image[0:4] = bytes.fromhex('1f2003d5')            # the kernel's first instruction
    image[56:60] = b'ARM\x64'                          # the arm64 image magic
    if version is not None:
        text = ('Linux version %s (builder@host) (aarch64-linux-gcc 13.3.0) 13.3.0, GNU ld 2.42\n'
                % version).encode()
        image[1024:1024 + len(text)] = text
    body = gzip.compress(bytes(image)) if compressed else bytes(image)
    name = ('ARM64 test Linux-%s' % (version or 'none')).encode()[:32]
    header = struct.pack('>7I', UIMAGE_MAGIC, 0, 0, len(body), 0x80000000, 0, 0)
    header = (header + name.ljust(32, b'\x00')).ljust(64, b'\x00')
    return header + body


def md5(path):
    """The md5 of a file, as md5sum would print it."""
    return hashlib.md5(Path(path).read_bytes()).hexdigest()


def record(uimage, dtb, **overrides):
    """One permit record, in the dialect tools/gatewatch.sh writes."""
    fields = {
        'ts': '2026-11-23T00:00:00Z',
        'uimage_md5': md5(uimage),
        'image_md5': md5(uimage),
        'dtb_md5': md5(dtb),
        'kver': '7.3.4',
        'root_index': '5',
        'root_read': 'IDENTICAL',
        'jffs2_ro': 'OK',
        'proven': 'gate-KTEST',
    }
    fields.update(overrides)
    return ' '.join('%s=%s' % (key, value) for key, value in fields.items())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args()

    shell = find_sh()
    if not shell:
        print('FAIL: no POSIX shell to run the gate with')
        return 1

    failures = []

    def check(name, condition, detail=''):
        if condition:
            print(f'PASS: {name}')
        else:
            print(f'FAIL: {name}: {detail}')
            failures.append(name)

    with tempfile.TemporaryDirectory(prefix='sk-gate-') as tmp:
        tmp = Path(tmp)
        uimage = tmp / 'flash-uImage'
        dtb = tmp / 'flash-dtb.bin'
        permit = tmp / 'flash.permit'
        uimage.write_bytes(make_uimage('7.3.4'))
        dtb.write_bytes(b'\xd0\x0d\xfe\xed' + b'\x00' * 512)

        def gate(lines=None, image=None, permit_body=None):
            """Run the real gate; return (rc, output)."""
            if permit_body is None:
                permit_body = ''.join(line + '\n' for line in (lines or []))
            permit.write_text(permit_body, encoding='utf-8')
            target = str(image or uimage)
            result = subprocess.run(
                [shell, str(GATE), target, str(dtb)],
                cwd=str(ROOT),
                env=dict(os.environ, PERMIT=str(permit)),
                capture_output=True, text=True,
            )
            return result.returncode, result.stdout + result.stderr

        # The positive case: a record about exactly the kernel in the image.
        rc, output = gate([record(uimage, dtb, kver='7.3.4')])
        check('a record about this kernel allows the flash', rc == 0, output)
        check('the gate says what it allowed', 'ALLOWED (gate-KTEST)' in output, output)

        # The same gate, with a 6.18 image and a 6.18 record: the release line
        # keeps working, which is the point of reading the version instead of
        # hard-coding one.
        old = tmp / 'flash-uImage.618'
        old.write_bytes(make_uimage('6.18.38+'))
        rc, output = gate([record(old, dtb, kver='6.18.38+')], image=old)
        check('a 6.18 record allows a 6.18 image', rc == 0, output)

        # The control the old check could not pass: a 6.x record vouching for a
        # 7.3 image.
        rc, output = gate([record(uimage, dtb, kver='6.18.38+')])
        check('a 6.18 record does NOT allow a 7.3 image', rc != 0, output)
        check('the refusal names both versions',
              '6.18.38+' in output and '7.3.4' in output, output)

        # The other way round: a 7.3 record vouching for a 6.18 image.
        rc, output = gate([record(old, dtb, kver='7.3.4')], image=old)
        check('a 7.3 record does NOT allow a 6.18 image', rc != 0, output)

        # The vendor kernel proves nothing about mainline, whatever the image is.
        rc, output = gate([record(uimage, dtb, kver='4.19.136+')])
        check('a vendor-kernel record does NOT allow a mainline image', rc != 0, output)
        check('the refusal says why', 'vendor kernel' in output, output)
        vendor = tmp / 'flash-uImage.vendor'
        vendor.write_bytes(make_uimage('4.19.136+'))
        rc, output = gate([record(vendor, dtb, kver='4.19.136+')], image=vendor)
        check('a vendor-kernel record does NOT allow the vendor image', rc != 0, output)

        # The gate fails closed: if the version cannot be read, nothing is proved.
        blank = tmp / 'flash-uImage.nobanner'
        blank.write_bytes(make_uimage(None))
        rc, output = gate([record(blank, dtb, kver='7.3.4')], image=blank)
        check('an image without a banner is refused', rc != 0, output)
        check('the refusal says the version is unreadable',
              'cannot be read' in output, output)
        raw = tmp / 'flash-uImage.raw'
        raw.write_bytes(make_uimage('7.3.4', compressed=False))
        rc, output = gate([record(raw, dtb, kver='7.3.4')], image=raw)
        check('an image that is not gzip is refused', rc != 0, output)

        # The fields that make a gate record evidence are still required.
        for field, value in (('root_read', 'DIFFERENT'), ('root_index', '4'),
                             ('jffs2_ro', 'FAIL')):
            rc, output = gate([record(uimage, dtb, **{field: value})])
            check(f'a record with {field}={value} is refused', rc != 0, output)

        # A record without a version cannot be matched to anything.
        rc, output = gate([record(uimage, dtb, kver='')])
        check('a record without a kver is refused', rc != 0, output)

        # The dialects that are not boot proofs.
        for dialect in ('operator-waiver', 'live-partition-match', 'preflash-snapshot'):
            rc, output = gate([record(uimage, dtb, proven=dialect)])
            check(f'proven={dialect} is allowed', rc == 0, output)

        # Anything else is not a proof.
        for dialect in ('gate-', 'preflash', 'guessed'):
            rc, output = gate([record(uimage, dtb, proven=dialect)])
            check(f'proven={dialect} is refused', rc != 0, output)

        # A record with no proven= field at all.
        bare = record(uimage, dtb)
        rc, output = gate([bare.replace(' proven=gate-KTEST', '')])
        check('a record without a proven field is refused', rc != 0, output)

        # No permit at all, and a permit about other bytes.
        rc, output = gate([], permit_body='')
        check('an empty permit is refused', rc != 0, output)
        rc, output = gate([record(uimage, dtb, uimage_md5='0' * 32)])
        check('a permit about other bytes is refused', rc != 0, output)

        # The shell reader the gate uses and the python reader the CI uses have to
        # read the same version out of the same bytes.
        if KVER_TOOL.exists():
            for image, expected in ((uimage, '7.3.4'), (old, '6.18.38+')):
                result = subprocess.run([sys.executable, str(KVER_TOOL), str(image)],
                                        capture_output=True, text=True)
                check(f'the python reader agrees on {expected}',
                      result.stdout.strip() == expected,
                      result.stdout + result.stderr)
        else:
            check('tools/uimage-kver.py is present', False, 'the file is missing')

    if failures:
        print(f'{len(failures)} control(s) failed')
        return 1
    print('PASS: the flash gate has teeth')
    return 0


if __name__ == '__main__':
    sys.exit(main())
