#!/usr/bin/env python3
"""List the file names inside a jffs2 image.

    python staging/ctl/jffs2names.py <image>

WHY THIS EXISTS

The flash gate asks "is /etc/init.d/sk-nat in this rootfs?", and the way it
used to ask was to look for the bytes "sk-nat" in the image.  That is the
wrong question in two ways:

  * the string "sk-nat" is also part of the name of the rc.d link
    ("S96sk-nat"), so the answer cannot tell the script from the link;
  * jffs2 compresses the *contents* of a file, so a string that is only in a
    file's text - say a word in a comment - is not there to be found.  A gate
    that looks for a word in a comment is a gate that fails on a correct
    image and passes on a wrong one.

The names themselves are a different matter.  In jffs2 only the data of a
file is compressed: the directory entry node, which carries the name of a
file, is stored as it is.  So the names can be read out of the image without
decompressing anything, and the gate can ask about a name instead of about
a word.

THE IMAGE

A jffs2 image is a list of nodes, each one starting on a four byte
boundary.  Every node starts with the magic 0x1985 (little endian) and a
type, and the node that carries a name (type 0xe001) is laid out like this:

    0   magic(2) type(2) totlen(4) hdr_crc(4)
    12  crc(4) ino(4) mode(4) uid_or_flags(4) usn(4)
    28  namelen(1) flags(1) unused(2)
    32  unused(8)
    40  name(namelen)

The name is the last thing in the node and it is not padded: the node's
length is 40 plus the length of the name.  This was measured on the
rootfs of run 166 (2026-10-25): 839 names came out of it, every one of
them a real file name, and their inode numbers covered every inode in the
image - so the list is the whole of the file names, not a sample of them.
"""
import struct
import sys

MAGIC = 0x1985
NAME_NODE = 0xE001
COMMON = 12
NAMELEN_OFF = 28
NAME_OFF = 40


def list_names(blob):
    """The names of the files in the image, as a set of strings.

    An empty set means the image could not be read as a jffs2 image; the
    caller has to decide what to do about that.
    """
    names = set()
    off = 0
    end = len(blob)
    while off + COMMON <= end:
        magic, nodetype, totlen = struct.unpack_from("<HHI", blob, off)
        if magic != MAGIC:
            # Not a node here.  The image is padded at the end, so a scan
            # that stopped at the first gap would stop early.
            off += 4
            continue
        if totlen < COMMON or off + totlen > end:
            off += 4
            continue
        if nodetype == NAME_NODE and totlen >= NAME_OFF:
            namelen = blob[off + NAMELEN_OFF]
            if 0 < namelen <= 255 and off + NAME_OFF + namelen <= off + totlen:
                raw = blob[off + NAME_OFF:off + NAME_OFF + namelen]
                names.add(raw.decode("utf-8", "replace"))
        off += (totlen + 3) & ~3
    return names


def read_names(path):
    """The names in the image at this path."""
    with open(path, "rb") as fh:
        blob = fh.read()
    return list_names(blob)


def main():
    args = [a for a in sys.argv[1:]]
    plain = False
    if "--list" in args:
        plain = True
        args.remove("--list")
    if not args:
        print("usage: jffs2names.py [--list] <image>")
        print()
        print("  --list  one name per line and nothing else, so that the list")
        print("          can be grepped - this is what the CI's gate does")
        return 2
    names = read_names(args[0])
    if plain:
        for n in sorted(names):
            print(n)
        return 0
    print("the image holds %d names" % len(names))
    for n in sorted(names):
        print("  %s" % n)
    return 0


if __name__ == "__main__":
    sys.exit(main())
