#!/usr/bin/env python3
"""Read/patch a U-Boot text environment block (ENV_IS_IN_NAND, no flags byte).

Format used by the SK-D840N community U-Boot at boot+0x180000 (verified: a
rebuilt block is byte-identical to the one on the device):
  4-byte little-endian CRC32, then ENV_SIZE-4 bytes of payload
  payload = NUL-separated "name=value", terminating NUL, NUL padding

Only produces a file; it never touches a device.
  python tools/ubootenv.py dump <image> [--offset 0x180000] [--size 0x20000]
  python tools/ubootenv.py set  <image> --out <newimage> bootdelay=0 'bootargs=...'
"""
import argparse
import re
import sys
import zlib


def read_block(img, offset, env_size):
    payload = img[offset + 4:offset + env_size]
    stored = int.from_bytes(img[offset:offset + 4], 'little')
    ok = (zlib.crc32(payload) & 0xFFFFFFFF) == stored
    end = payload.find(b'\x00\x00')
    raw = payload[:end] if end >= 0 else payload.rstrip(b'\x00')
    pairs = [p.decode('utf-8', 'replace') for p in raw.split(b'\x00') if p]
    return stored, ok, pairs


def build_block(pairs, env_size):
    body = ('\x00'.join(pairs) + '\x00\x00').encode('utf-8')
    if len(body) > env_size - 4:
        sys.exit(f'env payload too big: {len(body)} > {env_size - 4}')
    payload = body + b'\x00' * (env_size - 4 - len(body))
    crc = zlib.crc32(payload) & 0xFFFFFFFF
    return crc.to_bytes(4, 'little') + payload


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('cmd', choices=['dump', 'set'])
    ap.add_argument('image')
    ap.add_argument('--offset', default='0x180000')
    ap.add_argument('--size', default='0x20000')
    ap.add_argument('--out')
    ap.add_argument('vars', nargs='*')
    a = ap.parse_args()

    offset, env_size = int(a.offset, 0), int(a.size, 0)
    img = bytearray(open(a.image, 'rb').read())
    stored, ok, pairs = read_block(img, offset, env_size)
    print(f'# block @0x{offset:x} ENV_SIZE=0x{env_size:x} crc=0x{stored:08x} {"VALID" if ok else "INVALID"}')

    if a.cmd == 'dump':
        for p in pairs:
            print(p)
        return

    if not a.out:
        sys.exit('set needs --out')
    kv = {}
    for i, p in enumerate(pairs):
        k, _, v = p.partition('=')
        kv[k] = v
    for arg in a.vars:
        m = re.fullmatch(r'([A-Za-z_][A-Za-z0-9_]*)=(.*)', arg)
        if not m:
            sys.exit(f'bad assignment: {arg}')
        kv[m.group(1)] = m.group(2)
    order = [p.split('=', 1)[0] for p in pairs] + [k for k in kv if k not in
                                                   [p.split('=', 1)[0] for p in pairs]]
    new_pairs = [f'{k}={kv[k]}' for k in order if k in kv]
    block = build_block(new_pairs, env_size)
    img[offset:offset + env_size] = block
    open(a.out, 'wb').write(bytes(img))

    again = read_block(bytearray(open(a.out, 'rb').read()), offset, env_size)
    print(f'# wrote {a.out}: crc {"VALID" if again[1] else "INVALID"}, {len(again[2])} vars')
    old = dict(p.split('=', 1) for p in pairs)
    new = dict(p.split('=', 1) for p in again[2])
    for k in new:
        if old.get(k) != new[k]:
            print(f'  {k}:\n    - {old.get(k)}\n    + {new[k]}')


main()
