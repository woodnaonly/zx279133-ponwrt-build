#!/usr/bin/env python3
"""Say whether a flash-uImage really carries the driver lines a commit added.

    python tools/checkimagestrings.py <flash-uImage> [needle ...]

With no needles it checks the LAN1 bring-up set: the three sentences f5d573e added and
the cap sentence 17fe20f added.

Why this exists: the release that gets flashed has to be identified by its bytes, not by
its run number or by the kernel banner. The banner's build date is pinned by the build
system - a board running a pre-f5d573e kernel answered with "SMP Mon Oct 26 04:58:01 2026"
in a boot where the newest release was #286 - and a run number says nothing about which
overlays were in it. The strings in .rodata do.

Exit 0 only when every needle is present. A needle that is missing is printed as MISS and
the exit is 1, so this can be run against an image that is expected to lack the feature
(that is how it was checked: #286 carries the AN restart lines and not the cap line).
"""
import gzip
import hashlib
import io
import struct
import sys

DEFAULT_NEEDLES = (
    "auto-negotiation restarted",
    "auto-negotiation is complete",
    "did not complete in",
    "no more auto-negotiation restarts this boot",
)


def image_of(uimage_path):
    """Return (inflated kernel bytes, header info) for a legacy uImage."""
    raw = open(uimage_path, "rb").read()
    magic, hcrc, itime, dsize = struct.unpack(">4I", raw[:16])
    if magic != 0x27051956:
        raise SystemExit("not a legacy uImage (magic %08x)" % magic)
    name = raw[32:64].split(b"\0")[0].decode("latin1")
    body = raw[64:64 + dsize] if dsize else raw[64:]
    if body[:2] != b"\x1f\x8b":
        raise SystemExit("uImage body is not gzip (%r)" % body[:4])
    info = {
        "uimage_md5": hashlib.md5(raw).hexdigest(),
        "ih_name": name,
        "ih_time": itime,
        "data_size": dsize,
    }
    return gzip.decompress(body), info


def main():
    if len(sys.argv) < 2:
        raise SystemExit(__doc__)
    path = sys.argv[1]
    needles = sys.argv[2:] or list(DEFAULT_NEEDLES)

    img, info = image_of(path)
    print("%s: %d B inflated, uImage md5=%s" % (path, len(img), info["uimage_md5"]))
    print("  ih_name=%r ih_time=%d body=%d B" % (info["ih_name"], info["ih_time"], info["data_size"]))

    missing = []
    for needle in needles:
        hit = needle.encode() in img
        print("  %s  %s" % ("HIT " if hit else "MISS", needle))
        if not hit:
            missing.append(needle)

    if missing:
        print("RESULT: %d of %d needle(s) MISSING - this image is not the version under test"
              % (len(missing), len(needles)))
        return 1
    print("RESULT: all %d needle(s) present" % len(needles))
    return 0


if __name__ == "__main__":
    sys.exit(main())
