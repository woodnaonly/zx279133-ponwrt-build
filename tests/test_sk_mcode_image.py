#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Does the image-level microcode gate turn red where it is supposed to?

The gate answers a question about the artifact: does the kernel image that was
built carry the microcode the driver will take.  It can be wrong in two
directions, and each one has to be caught by its own check:

  * the image carries the other blob - the driver check must turn red
  * the image carries a blob that is one byte off - the carry check must
    turn red
  * the image carries no blob at all - the carry check must turn red

A gate that refuses the wrong case for the wrong reason is not a gate: it
would pass this file for the same reason it refuses a broken tree, so each
case here checks *which* check turned red, not merely that something did.

And the gate must not know the answer by itself: the crc32 it accepts is read
out of the driver's source, so a tree whose driver names another crc must
change the verdict.  A gate that hardcoded the crc would pass all of the
cases above for the wrong reason.

Usage:
  python3 tests/test_sk_mcode_image.py
"""
import contextlib
import gzip
import importlib.util
import io
import pathlib
import shutil
import struct
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[1]
GATE = ROOT / 'tools/check-sk-mcode-image.py'
TREE_GATE = ROOT / 'tools/check-sk-mcode.py'

GOOD_BLOB = (ROOT / 'overlays-73/target/linux/zte/zx279133/firmware/zte/'
                    'zx279133/mcode_intel.bin')
OTHER_BLOB = (ROOT / 'overlays/target/linux/zte/zx279133/firmware/zte/'
                      'zx279133/mcode_intel.bin')

CARRIES = 'the image carries the microcode'
ACCEPTS = 'the microcode is one the driver accepts'
NAMES = 'the image names the firmware'
FIRMWARE_NAME = 'zte/zx279133/mcode_intel.bin'

results = []


def check(name, ok, detail=''):
    results.append((name, ok, detail))
    print('  [%s] %s%s' % ('OK ' if ok else 'FAIL', name,
                           (' - ' + detail) if detail else ''))


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def uimage(payload, name=b'Linux'):
    """A legacy uImage with a gzip body, which is what the build produces."""
    body = gzip.compress(payload, 9, mtime=0)
    header = struct.pack('>7I', 0x27051956, 0, 0, len(body), 0x80000, 0x80000, 0)
    header += bytes([2, 2, 2, 1]) + name.ljust(32, b'\0')
    return header + body


def red_on(gate, argv):
    """Run the gate and return (code, the checks that turned red, the output)."""
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        code = gate.main(argv)
    text = out.getvalue()
    failed = [line.split(']', 1)[1].split(':', 1)[0].strip()
              for line in text.split('\n') if line.startswith('[FAIL]')]
    return code, failed, text


def main():
    gate = load(GATE, 'sk_mcode_image_gate')
    tree_gate = load(TREE_GATE, 'sk_mcode_tree_gate')

    tmp = pathlib.Path(tempfile.mkdtemp(prefix='sk-mcode-image-'))
    try:
        # A tree whose driver names the crc the 7.3 line's blob has.
        tree = tmp / 'tree'
        (tree / 'drivers/net/ethernet/zte').mkdir(parents=True)
        (tree / 'firmware/zte/zx279133').mkdir(parents=True)
        driver = tree / 'drivers/net/ethernet/zte/zx279133-np.c'
        driver.write_text(
            'if (fw_crc != 0xef1d7647) { release_firmware(fw); return -ESTALE; }\n',
            encoding='utf-8')

        # The two real blobs: the one the 7.3 driver takes, and the 6.18
        # line's one.  Using the real files keeps the fixture honest - a
        # synthetic blob with a made-up crc would only test the fixture.
        good = GOOD_BLOB.read_bytes()
        other = OTHER_BLOB.read_bytes()
        if len(good) != 103456 or len(other) != 103456:
            check('the two real blobs are here', False,
                  '%d and %d bytes' % (len(good), len(other)))
            return 1
        if tree_gate.crc32_le(good) != 0xef1d7647:
            check('the blob the 7.3 line carries is the one the driver takes',
                  False, 'crc32 0x%08x' % tree_gate.crc32_le(good))
            return 1
        (tree / 'firmware/zte/zx279133/mcode_intel.bin').write_bytes(good)

        broken = bytearray(good)
        broken[64] ^= 0xFF
        broken = bytes(broken)

        # 1. an image carrying the right blob passes
        image_ok = tmp / 'ok.uImage'
        image_ok.write_bytes(uimage(b'kernel text ' + good + b'\0' +
                                    FIRMWARE_NAME.encode()))
        code, failed, text = red_on(gate, ['--image', str(image_ok),
                                            '--tree', str(tree)])
        check('an image carrying the right microcode passes',
              code == 0 and not failed, '; '.join(failed) or text.strip()[-200:])

        # 1b. the bytes are there but the firmware is not named: the loader
        #     would not find it, so the image does not do what it looks like
        #     it does.
        image_unnamed = tmp / 'unnamed.uImage'
        image_unnamed.write_bytes(uimage(b'kernel text ' + good))
        code, failed, text = red_on(gate, ['--image', str(image_unnamed),
                                            '--tree', str(tree)])
        check('an image whose firmware is not named is refused on the name check',
              code != 0 and failed == [NAMES], '; '.join(failed) or 'no failure')

        # 2. the tree points at the other blob and the image carries it:
        #    the image does carry what the tree has, and the driver still
        #    refuses it - so this has to turn red on the driver check alone.
        tree_blob = tree / 'firmware/zte/zx279133/mcode_intel.bin'
        tree_blob.write_bytes(other)
        image_other = tmp / 'other.uImage'
        image_other.write_bytes(uimage(b'kernel text ' + other + b'\0' +
                                    FIRMWARE_NAME.encode()))
        code, failed, text = red_on(gate, ['--image', str(image_other),
                                            '--tree', str(tree)])
        check('an image carrying the other blob is refused on the driver check',
              code != 0 and failed == [ACCEPTS], '; '.join(failed) or 'no failure')
        tree_blob.write_bytes(good)

        # 3. an image carrying a blob that is one byte off: the carry check turns red
        image_broken = tmp / 'broken.uImage'
        image_broken.write_bytes(uimage(b'kernel text ' + broken + b'\0' +
                                    FIRMWARE_NAME.encode()))
        code, failed, text = red_on(gate, ['--image', str(image_broken),
                                            '--tree', str(tree)])
        check('an image carrying a blob one byte off is refused on the carry check',
              code != 0 and failed == [CARRIES], '; '.join(failed) or 'no failure')

        # 4. an image with no blob at all: the carry check turns red
        image_none = tmp / 'none.uImage'
        image_none.write_bytes(uimage(b'kernel text without any firmware' + b'\0' +
                                    FIRMWARE_NAME.encode()))
        code, failed, text = red_on(gate, ['--image', str(image_none),
                                            '--tree', str(tree)])
        check('an image with no microcode is refused on the carry check',
              code != 0 and failed == [CARRIES], '; '.join(failed) or 'no failure')

        # 5. the answer follows the driver, not the gate
        #    a tree whose driver names another crc must refuse the same image
        kept = driver.read_text(encoding='utf-8')
        driver.write_text(
            'if (fw_crc != 0x11111111) { release_firmware(fw); return -ESTALE; }\n',
            encoding='utf-8')
        code, failed, text = red_on(gate, ['--image', str(image_ok),
                                            '--tree', str(tree)])
        check('the gate follows the driver, not a hardcoded crc',
              code != 0 and failed == [ACCEPTS], '; '.join(failed) or 'no failure')
        driver.write_text(kept, encoding='utf-8')
        code, failed, text = red_on(gate, ['--image', str(image_ok),
                                            '--tree', str(tree)])
        check('and it passes again when the driver is restored',
              code == 0 and not failed, '; '.join(failed) or text.strip()[-200:])
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    failed = [name for name, ok, _ in results if not ok]
    print()
    if failed:
        print('FAILED: %d of %d checks' % (len(failed), len(results)))
        return 1
    print('PASS: %d checks' % len(results))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
