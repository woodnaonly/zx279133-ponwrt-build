#!/usr/bin/env python3
"""Does the fetch still say yes when the release does not carry the flasher?

The flasher (`flash-sk-d840n.sh`) is in the tree, not in the release's
assets, and the release's manifest names it - so a fetch that believes the
manifest has to take it from the repository.  It used to do that only when
the asset download answered 404, and run 181 was fetched through the other
branch and came back "the release did not check out" over a file the
release was never going to carry.

    python tools/test-getrel.py

Nothing here touches the network: the check is a function, and the function
is what the fetch calls.
"""
import hashlib
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import getrel  # noqa: E402


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def build(tmp, files):
    """A directory that looks like a fetched release, and the templates dir."""
    root = tempfile.mkdtemp(prefix="case-", dir=tmp)
    out = os.path.join(root, "out")
    tpl = os.path.join(root, "templates")
    os.makedirs(out)
    os.makedirs(tpl)
    for name, data in files.items():
        with open(os.path.join(out, name), "wb") as f:
            f.write(data)
    return out, tpl


def manifest(out, entries):
    """The release's own manifest: a hash and a name, one per line."""
    with open(os.path.join(out, "sha256sums-release"), "w",
              encoding="utf-8", newline="\n") as f:
        for name, data in entries:
            f.write("%s  %s\n" % (sha256(data), name))


def main():
    bad = 0
    tmp = tempfile.mkdtemp(prefix="test-getrel-")
    try:
        # 1. The flasher is not in the release, and the manifest names it.
        #    The repository's copy is taken, and it has to be the right one.
        uimage = b"the kernel image"
        flasher = b"#!/bin/sh\n# the flasher\n"
        out, tpl = build(tmp, {"flash-uImage": uimage})
        with open(os.path.join(tpl, "flash-sk-d840n.sh"), "wb") as f:
            f.write(flasher)
        # The manifest names the flasher, so its hash is needed in the
        # manifest - but the file itself is not in the release.
        manifest(out, [("flash-uImage", uimage), ("flash-sk-d840n.sh", flasher)])
        n = getrel.check_manifest(out, tpl)
        got = os.path.exists(os.path.join(out, "flash-sk-d840n.sh"))
        print("1. the flasher is not in the release: %d problems, the "
              "repository's copy was taken: %s" % (n, got))
        if n != 0 or not got:
            bad += 1

        # 2. The repository's copy is NOT the release's copy: the manifest
        #    says so, and the check has to say so too.
        out, tpl = build(tmp, {"flash-uImage": uimage})
        with open(os.path.join(tpl, "flash-sk-d840n.sh"), "wb") as f:
            f.write(b"a different flasher\n")
        manifest(out, [("flash-uImage", uimage), ("flash-sk-d840n.sh", flasher)])
        n = getrel.check_manifest(out, tpl)
        left = os.path.exists(os.path.join(out, "flash-sk-d840n.sh"))
        print("2. the repository's copy is a different one: %d problems, the "
              "wrong file was taken away: %s" % (n, not left))
        if n != 1 or left:
            bad += 1

        # 3. A file that is in the release and is not the one the manifest
        #    names: it is a problem, and it is taken away so that the next
        #    fetch tries again.
        out, tpl = build(tmp, {"flash-uImage": uimage})
        manifest(out, [("flash-uImage", b"something else")])
        n = getrel.check_manifest(out, tpl)
        left = os.path.exists(os.path.join(out, "flash-uImage"))
        print("3. the file is not what the manifest names: %d problems, the "
              "wrong file was taken away: %s" % (n, not left))
        if n != 1 or left:
            bad += 1
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print("test-getrel: %d problems" % bad)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
