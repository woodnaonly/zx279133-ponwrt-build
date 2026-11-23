#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Ask the flash gate whether it accepts the bytes this build produced.

tools/gatecheck.sh is the one-way door in front of an irreversible flash: it lets a
pair of bytes through only when a gate round already booted exactly those bytes on
this board.  tests/test_gatecheck.py proves the gate has teeth, using uImages it
builds itself - which is the right way to test the gate's *logic*, but it cannot
answer the question the person carrying the board to the table has: does the gate
accept *these* bytes?

That is what this does.  It takes the pair of bytes the build produced, reads the
kernel version out of the uImage the same way the gate does, writes the permit
record a scored gate round would have written, and asks the gate:

  * the positive case - a record about exactly this kernel and these bytes - has
    to be ALLOWED, and the gate's own output has to name the version that was
    read out of the image, so a gate that allowed it for another reason cannot
    pass here;
  * a record about another kernel's version has to be refused, and the refusal
    has to name both versions - that is the control which the old "same first
    digit" check could not pass, and it is the one that matters most for a line
    whose images are 7.3 while the records on the bench are from 6.18;
  * a record whose root was not read back identically has to be refused;
  * a record about other bytes has to be refused.

Nothing here writes to a board and nothing outside the temporary directory is
touched: the gate is asked, it does not flash.
"""
import argparse
import hashlib
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GATE = Path(os.environ.get('SK_GATECHECK') or (ROOT / 'tools/gatecheck.sh'))
KVER_TOOL = ROOT / 'tools/uimage-kver.py'


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


def md5(path):
    """The md5 of a file, as md5sum would print it."""
    return hashlib.md5(Path(path).read_bytes()).hexdigest()


def kernel_version(uimage):
    """The version the uImage carries, read by the repository's own reader."""
    result = subprocess.run([sys.executable, str(KVER_TOOL), str(uimage)],
                            capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError('the version of %s cannot be read: %s'
                           % (uimage, (result.stdout + result.stderr).strip()))
    return result.stdout.strip()


def record(uimage, dtb, version, **overrides):
    """One permit record, in the dialect tools/gatewatch.sh writes."""
    fields = {
        'ts': '2026-11-23T00:00:00Z',
        'uimage_md5': md5(uimage),
        'image_md5': md5(uimage),
        'dtb_md5': md5(dtb),
        'kver': version,
        'root_index': '5',
        'root_read': 'IDENTICAL',
        'jffs2_ro': 'OK',
        'proven': 'gate-KTEST',
    }
    fields.update(overrides)
    return ' '.join('%s=%s' % (key, value) for key, value in fields.items())


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--uimage', type=Path, required=True,
                        help='the uImage this build produced')
    parser.add_argument('--dtb', type=Path, required=True,
                        help='the device tree this build produced')
    args = parser.parse_args(argv)

    shell = find_sh()
    if not shell:
        print('FAIL: no POSIX shell to run the gate with')
        return 1
    # The paths are resolved here because the gate is run with the repository as
    # its working directory, and a relative path would then be looked up in the
    # wrong tree.
    uimage = args.uimage.resolve()
    dtb = args.dtb.resolve()
    for path in (uimage, dtb):
        if not path.is_file() or path.stat().st_size == 0:
            print('FAIL: %s is missing or empty' % path)
            return 1

    try:
        version = kernel_version(uimage)
    except RuntimeError as exc:
        print('FAIL: %s' % exc)
        return 1
    print('the uImage carries kernel %s' % version)
    print('  uimage %s  md5 %s' % (uimage, md5(uimage)))
    print('  dtb    %s  md5 %s' % (dtb, md5(dtb)))

    failures = []

    def check(name, condition, detail=''):
        if condition:
            print('PASS: %s' % name)
        else:
            print('FAIL: %s: %s' % (name, detail))
            failures.append(name)

    with tempfile.TemporaryDirectory(prefix='sk-permit-') as tmp:
        permit = Path(tmp) / 'flash.permit'

        def gate(line):
            """Run the real gate against one permit record; return (rc, output)."""
            permit.write_text(line + '\n', encoding='utf-8')
            result = subprocess.run(
                [shell, str(GATE), str(uimage), str(dtb)],
                cwd=str(ROOT),
                env=dict(os.environ, PERMIT=str(permit)),
                capture_output=True, text=True,
            )
            return result.returncode, result.stdout + result.stderr

        rc, output = gate(record(uimage, dtb, version))
        check('the gate allows the pair of bytes this build produced', rc == 0, output)
        check('the gate names the kernel it read out of the image',
              version in output, output)
        check('the gate says which record it allowed', 'ALLOWED (gate-' in output, output)

        # A record about another kernel: the control that matters most here, because
        # the records on the bench are from the 6.18 line and the images are 7.3.
        other = version + '-not-this-one'
        rc, output = gate(record(uimage, dtb, other))
        check('a record about another kernel is refused', rc != 0, output)
        check('the refusal names both versions',
              other in output and version in output, output)

        # A record whose root was not read back identically proves nothing about the
        # partition the board would boot from.
        rc, output = gate(record(uimage, dtb, version, root_read='DIFFERENT'))
        check('a record whose root was not read back is refused', rc != 0, output)

        # A record about other bytes.
        rc, output = gate(record(uimage, dtb, version, uimage_md5='0' * 32))
        check('a record about other bytes is refused', rc != 0, output)

    if failures:
        print('%d control(s) failed' % len(failures))
        return 1
    print('PASS: the flash gate accepts this build\'s bytes, and only them')
    return 0


if __name__ == '__main__':
    sys.exit(main())
