#!/usr/bin/env python3
"""Fetch the flash release of one CI run and check it against its own manifest.

    python tools/getrel.py                 # list the releases
    python tools/getrel.py <tag>           # fetch one of them

The assets have to come from the GitHub API, not from the release page: the
HTML page does not list every asset, and an asset that is quietly missing is
an image that is quietly wrong.

The manifest (`sha256sums-release`) is the release's own, and it is what
`templates/flash-sk-d840n.sh` checks on the board before it erases anything.
Here it is checked on the PC as well, before the images are put anywhere near
the board: a truncated download is caught here, not after the flash.

The kernel image is checked against the 8 MiB partition as well. The build
now refuses to publish an oversized one, but a release from before that gate
is still a possibility, and `mtd write` would truncate it silently.

WHERE THE IMAGES GO

They are left in the bench's trial directory, next to the other images the
bench keeps, because that is where the flasher looks for them.  SKD_TRIAL
points at that directory.
"""
import hashlib
import json
import os
import sys
import urllib.request

REPO = "woodnaonly/zx279133-ponwrt-build"
API = "https://api.github.com/repos/%s/releases" % REPO
PROXY = "http://127.0.0.1:10808"
_HERE = os.path.dirname(os.path.abspath(__file__))
_default = os.path.join(os.path.dirname(os.path.dirname(_HERE)), "staging", "trial")
BASE = os.environ.get("SKD_TRIAL", _default)
PARTITION = 8388608
ASSETS = ["flash-uImage", "flash-dtb.bin", "flash-rootfs.jffs2", "flash-sysupgrade.bin",
          "sk-d840n-initramfs.itb", "sha256sums-release"]


def opener():
    o = urllib.request.build_opener(
        urllib.request.ProxyHandler({"http": PROXY, "https": PROXY}))
    o.addheaders = [("User-Agent", "sk-d840n-fetch")]
    return o


def list_releases():
    o = opener()
    with o.open(API, timeout=120) as r:
        data = json.load(r)
    for rel in data[:12]:
        names = [a["name"] for a in rel.get("assets", [])]
        print("%-34s %-12s %s" % (rel["tag_name"], rel["published_at"][:10],
                                  " ".join(sorted(names))))
    return data


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main():
    if len(sys.argv) < 2:
        list_releases()
        return
    tag = sys.argv[1]
    out = os.path.abspath(os.path.join(BASE, tag))
    os.makedirs(out, exist_ok=True)
    o = opener()

    rel = None
    with o.open(API, timeout=120) as r:
        for item in json.load(r):
            if item["tag_name"] == tag:
                rel = item
                break
    if rel is None:
        print("no release named", tag, file=sys.stderr)
        list_releases()
        return 1

    have = {a["name"]: a["browser_download_url"] for a in rel.get("assets", [])}
    for name in ASSETS:
        if name not in have:
            print("   (no %s in this release)" % name)
            continue
        dst = os.path.join(out, name)
        print("fetching %-22s -> %s" % (name, dst), flush=True)
        with o.open(have[name], timeout=600) as r, open(dst, "wb") as f:
            f.write(r.read())
        print("   %d bytes" % os.path.getsize(dst))

    man = os.path.join(out, "sha256sums-release")
    if not os.path.exists(man):
        print("no manifest - nothing to check against")
        return 1
    bad = 0
    for line in open(man):
        parts = line.split()
        if len(parts) != 2:
            continue
        want, name = parts
        path = os.path.join(out, name)
        if not os.path.exists(path):
            print("   %-22s missing" % name)
            bad += 1
            continue
        got = sha256(path)
        ok = got == want
        bad += 0 if ok else 1
        print("   %-22s %s" % (name, "OK" if ok else "MISMATCH %s != %s" % (got, want)))

    kimg = os.path.join(out, "flash-uImage")
    if os.path.exists(kimg):
        n = os.path.getsize(kimg)
        print("kernel uImage: %d bytes, the kernel partition holds %d: %s"
              % (n, PARTITION, "it fits" if n <= PARTITION else "IT DOES NOT FIT"))
        if n > PARTITION:
            bad += 1
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
