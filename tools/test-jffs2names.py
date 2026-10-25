"""A test for the jffs2 name reader: does it find a name that IS there?

The negative case is already measured - the rootfs of run 166 holds 767
names, none of them "sk-nat", and the gate says the image has no NAT.  This
is the other half: a name that is there must be found, or the gate would
refuse a correct image.

A synthetic image is built here: the same node layout that was measured on
the real one, with a few names in it.

    python tools/test-jffs2names.py
"""
import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import jffs2names

MAGIC = 0x1985
NAME_NODE = 0xE001


def node(name, ino):
    """A name node, in the layout that was measured on the real image.

    The offsets are the node's, and the payload starts at 12:

        12 crc      16 ino      20 mode     24 usn
        28 namelen  29 flags    30 unused   32 unused
        40 name
    """
    payload = bytearray(28)
    struct.pack_into("<I", payload, 0, 0x75AABBCC)      # crc
    struct.pack_into("<I", payload, 4, ino)             # ino
    struct.pack_into("<I", payload, 8, 0x55)             # mode
    struct.pack_into("<I", payload, 12, 0x6ADBDD33)      # usn
    payload[16] = len(name)                             # namelen
    payload[17] = 0x08                                  # flags
    payload += name.encode("utf-8")
    hdr = struct.pack("<HHI", MAGIC, NAME_NODE, 12 + len(payload)) + b"\0" * 4
    raw = hdr + bytes(payload)
    # The nodes of a jffs2 image start on four byte boundaries, so a node
    # whose length is not a multiple of four is followed by padding.
    return raw + b"\0" * ((-len(raw)) & 3)


def main():
    names = ["sk-nat", "S96sk-nat", "99-sk-nat", "network", "init.d"]
    blob = b""
    for i, n in enumerate(names):
        blob += node(n, i + 2)
    # And something that is not a node, to make sure the scan skips it.
    blob += b"\xde\xad\xbe\xef" * 4

    got = jffs2names.list_names(blob)
    print("the image holds %d names" % len(got))
    for n in sorted(got):
        print("   %s" % n)
    ok = True
    for n in names:
        if n not in got:
            print("MISSING: %s" % n)
            ok = False
    if len(got) != len(names):
        print("the reader found %d names, expected %d" % (len(got), len(names)))
        ok = False
    print("the test %s" % ("passes" if ok else "FAILS"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
