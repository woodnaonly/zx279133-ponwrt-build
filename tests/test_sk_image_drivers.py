#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Negative controls for the image gate that asks whether a kernel image
carries this board's drivers.

The gate (tools/check-sk-image-drivers.py) answers one question about the
artifact that is about to be flashed: is the code we wrote in it?  A gate that
only looked at the image would pass on a needle that came from somewhere else
in the kernel, so both halves are checked and both halves have to be able to
turn the gate red:

  * a needle that is missing from the image fails (the code was not built in),
  * a needle that is missing from the source it is attributed to fails as a
    stale probe, not as a pass (the source was reworded and the probe now
    means something else),
  * a probe whose source file does not exist fails (the file was moved),
  * a file that is not a legacy uImage fails,
  * a uImage whose body is not gzip fails,
  * a manifest without probes fails.

One more thing is checked, because it is a way of losing data quietly: the
kernel payload can be a gzip stream with several members, and a reader that
stops at the first one would see a kernel that is half the size and would not
say so.  The needle is therefore put in the second member, and the gate still
has to find it.
"""
import gzip
import json
from pathlib import Path
import struct
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
GATE = ROOT / 'tools/check-sk-image-drivers.py'

NEEDLE = 'ZX279133 9132 internal PHY'


def make_uimage(payloads):
    """A legacy uImage whose body is the gzip of the given byte strings.

    Each part becomes its own gzip member: that is what a multi-member
    stream is, and it is how the kernel payload is built when the compressor
    runs in more than one thread.
    """
    body = b''
    for part in payloads:
        body += gzip.compress(part)
    header = bytearray(64)
    struct.pack_into('>4I', header, 0, 0x27051956, 0, 0, len(body))
    header[32:64] = b'ARM64 test'.ljust(32, b'\x00')
    return bytes(header) + body


def write_manifest(path, probes):
    path.write_text(json.dumps({'line': 'test', 'probes': probes}),
                    encoding='utf-8', newline='\n')


def probe(needle=NEEDLE, sources=None):
    if sources is None:
        sources = [{'root': 'board', 'path': 'driver.c'}]
    return {'label': 'a probe', 'needle': needle, 'sources': sources,
            'why': 'because the gate is under test'}


def run(tmp, image_bytes, probes, source_text=None, source_name='driver.c'):
    """Run the gate over a synthetic image; return (rc, output)."""
    board = Path(tmp) / 'board'
    board.mkdir(exist_ok=True)
    if source_text is None:
        source_text = '/* %s */\n/* a string nobody has */\n' % NEEDLE
    (board / source_name).write_text(source_text, encoding='utf-8', newline='\n')

    manifest = Path(tmp) / 'manifest.json'
    write_manifest(manifest, probes)

    image = Path(tmp) / 'flash-uImage'
    image.write_bytes(image_bytes)

    command = [sys.executable, str(GATE),
               '--image', str(image),
               '--manifest', str(manifest),
               '--board-layer', str(board)]
    result = subprocess.run(command, capture_output=True, text=True)
    return result.returncode, result.stdout + result.stderr


CASES = []


def case(name):
    def wrap(fn):
        CASES.append((name, fn))
        return fn
    return wrap


@case('the needle is in the image and in the source: the gate passes')
def t_positive(tmp):
    rc, out = run(tmp, make_uimage([b'kernel ' + NEEDLE.encode()]),
                  [probe()])
    assert rc == 0, out
    assert 'OK: all 1 probe(s) hold' in out, out


@case('the needle is missing from the image: the gate fails')
def t_missing_in_image(tmp):
    rc, out = run(tmp, make_uimage([b'a kernel without our driver']),
                  [probe()])
    assert rc == 1, out
    assert 'does not carry' in out, out


@case('the needle is missing from the source: the probe is stale and fails')
def t_stale_probe(tmp):
    rc, out = run(tmp, make_uimage([b'kernel ' + NEEDLE.encode()]),
                  [probe()], source_text='/* the message was reworded */\n')
    assert rc == 1, out
    assert 'stale' in out, out


@case('the source file does not exist: the gate fails')
def t_no_source(tmp):
    rc, out = run(tmp, make_uimage([b'kernel ' + NEEDLE.encode()]),
                  [probe(sources=[{'root': 'board', 'path': 'gone.c'}])])
    assert rc == 1, out
    assert 'no source file found' in out, out


@case('a file that is not a uImage: the gate fails')
def t_not_a_uimage(tmp):
    rc, out = run(tmp, b'x' * 128, [probe()])
    assert rc != 0, out
    assert 'not a legacy uImage' in out, out


@case('a uImage whose body is not gzip: the gate fails')
def t_not_gzip(tmp):
    header = bytearray(64)
    struct.pack_into('>4I', header, 0, 0x27051956, 0, 0, 4)
    rc, out = run(tmp, bytes(header) + b'raw!', [probe()])
    assert rc != 0, out
    assert 'not gzip' in out, out


@case('a manifest without probes: the gate fails')
def t_manifest_without_probes(tmp):
    board = Path(tmp) / 'board'
    board.mkdir(exist_ok=True)
    manifest = Path(tmp) / 'manifest.json'
    manifest.write_text(json.dumps({'line': 'test'}), encoding='utf-8')
    image = Path(tmp) / 'flash-uImage'
    image.write_bytes(make_uimage([b'kernel']))
    command = [sys.executable, str(GATE), '--image', str(image),
               '--manifest', str(manifest)]
    result = subprocess.run(command, capture_output=True, text=True)
    out = result.stdout + result.stderr
    assert result.returncode != 0, out
    assert 'no probes list' in out, out


@case('the needle is in the second gzip member: the gate still finds it')
def t_second_member(tmp):
    rc, out = run(tmp, make_uimage([b'first member ', b'second ' + NEEDLE.encode()]),
                  [probe()])
    assert rc == 0, out
    assert 'OK: all 1 probe(s) hold' in out, out


@case('one probe of two fails: the gate fails')
def t_one_of_two(tmp):
    rc, out = run(tmp, make_uimage([b'kernel ' + NEEDLE.encode()]),
                  [probe(),
                   {'label': 'a second probe', 'needle': 'a string nobody has',
                    'sources': [{'root': 'board', 'path': 'driver.c'}],
                    'why': 'it is not in the image'}])
    assert rc == 1, out
    assert 'does not carry' in out, out


def main():
    failures = 0
    for name, fn in CASES:
        with tempfile.TemporaryDirectory(prefix='sk-image-drivers-') as tmp:
            try:
                fn(tmp)
            except AssertionError as exc:
                failures += 1
                print('FAIL  %s\n      %s' % (name, exc))
            else:
                print('ok    %s' % name)
    print('\n%d of %d control(s) behaved as required' % (len(CASES) - failures, len(CASES)))
    return 1 if failures else 0


if __name__ == '__main__':
    sys.exit(main())
