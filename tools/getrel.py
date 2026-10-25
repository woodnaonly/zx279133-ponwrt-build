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
import shutil
import subprocess
import sys
import time
import urllib.request

REPO = "woodnaonly/zx279133-ponwrt-build"
API = "https://api.github.com/repos/%s/releases" % REPO
PROXY = "http://127.0.0.1:10808"
_HERE = os.path.dirname(os.path.abspath(__file__))
_default = os.path.join(os.path.dirname(os.path.dirname(_HERE)), "staging", "trial")
BASE = os.environ.get("SKD_TRIAL", _default)
PARTITION = 8388608
ASSETS = ["flash-uImage", "flash-dtb.bin", "flash-rootfs.jffs2", "flash-sysupgrade.bin",
          "flash-sk-d840n.sh", "sk-d840n-initramfs.itb", "sha256sums-release"]


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


def tag_exists(tag):
    """Is there a release for this tag?  Git answers that, and git does not
    spend the API's quota."""
    try:
        out = subprocess.run(
            ["git", "ls-remote", "--tags", "git@github.com:woodnaonly/zx279133-ponwrt-build.git"],
            capture_output=True, text=True, timeout=120).stdout
    except Exception as exc:
        print("git ls-remote failed: %s" % exc, file=sys.stderr)
        return False
    return ("refs/tags/%s\n" % tag) in out or ("refs/tags/%s" % tag) in out


def check_manifest(out, templates):
    """Check a fetched release against its own manifest.

    `out` is the directory the release was fetched into and `templates` is
    the repository's own directory of the files that are built rather than
    published.  The number of problems is returned; zero means the release
    is what it says it is.
    """
    bad = 0
    man = os.path.join(out, "sha256sums-release")
    if not os.path.exists(man):
        print("no manifest - nothing to check against")
        return 1
    for line in open(man):
        parts = line.split()
        if len(parts) != 2:
            continue
        want, name = parts
        path = os.path.join(out, name)
        if not os.path.exists(path):
            # The release does not carry this file, but the manifest names
            # it.  That is the flasher: it is in the tree, and the release's
            # assets are the images.  It comes from this repository, which
            # is where the release was built from - and the manifest's
            # checksum is what proves that: if the repository's copy is not
            # the release's copy, the check below says so.
            #
            # This used to be done only when the asset download answered
            # 404, which is the branch that runs when the release list is
            # not available.  When the list IS available the flasher is
            # simply not in it, and the fetch ended with "the release did
            # not check out" - over a file the release was never going to
            # carry.  Run 181 was fetched that way, on 2026-10-25.
            src = os.path.join(templates, name)
            if os.path.exists(src):
                shutil.copyfile(src, path)
                print("   %-22s (not published as an asset; taken from %s)"
                      % (name, src))
            else:
                print("   %-22s missing" % name)
                bad += 1
                continue
        got = sha256(path)
        ok = got == want
        bad += 0 if ok else 1
        print("   %-22s %s" % (name, "OK" if ok else "MISMATCH %s != %s" % (got, want)))
        if not ok:
            # The bytes here are not the bytes the release published.  The
            # file is taken away, so that the next run fetches it again
            # instead of failing on the same wrong file for ever.
            try:
                os.remove(path)
            except OSError:
                pass
    return bad


def main():
    if len(sys.argv) < 2:
        list_releases()
        return
    tag = sys.argv[1]
    out = os.path.abspath(os.path.join(BASE, tag))
    os.makedirs(out, exist_ok=True)
    o = opener()

    # The asset URLs are a fixed shape, so the API is not needed to download
    # anything: what it is needed for is to find out WHICH assets a release
    # has, and that is answered by trying each one.
    #
    # The API's anonymous quota is sixty requests an hour per address, and
    # on this machine both the direct address and the proxy's are spent
    # most of the time - so a fetch that needs the API to start is a fetch
    # that cannot start at all.  The release list is tried first, because it
    # is cheaper and more precise; when it is not available, the assets are
    # fetched by URL and the ones that are not in the release say so.
    have = {}
    try:
        # The listing is a nicety, not a necessity, so it is not allowed to
        # hold the fetch up: the proxy answered a spent quota with a four
        # minute hang on 2026-10-25, and four minutes is a lot to pay for a
        # list that the asset URLs do not need.
        with o.open(API, timeout=20) as r:
            for item in json.load(r):
                if item["tag_name"] == tag:
                    have = {a["name"]: a["browser_download_url"]
                            for a in item.get("assets", [])}
                    break
    except Exception as exc:
        print("the release list is not available (%s); the assets are fetched "
              "by URL instead" % exc)
        print("(the tag itself is confirmed with git, which spends no quota)")
    if not have:
        if not tag_exists(tag):
            print("no release named", tag, file=sys.stderr)
            return 1
        for name in ASSETS:
            have[name] = ("https://github.com/woodnaonly/zx279133-ponwrt-build/"
                          "releases/download/%s/%s" % (tag, name))

    for name in ASSETS:
        if name not in have:
            print("   (no %s in this release)" % name)
            continue
        dst = os.path.join(out, name)
        if os.path.exists(dst) and os.path.getsize(dst) > 0:
            # Already fetched.  The proxy is not a reliable route and the
            # release is tens of megabytes, so a file that is already here
            # is not fetched again - and if it turns out to be the wrong
            # bytes, the manifest check below says so and takes it away,
            # so the next run fetches it properly.
            print("fetching %-22s -> %s" % (name, dst), flush=True)
            print("   (already here: %d bytes)" % os.path.getsize(dst))
            continue
        print("fetching %-22s -> %s" % (name, dst), flush=True)
        # The bytes are written as they arrive, not at the end.
        #
        # The download goes through the proxy, and the proxy is slow: 222
        # kB/s measured on 2026-10-25, so the biggest asset takes a minute
        # and the whole release takes several.  Writing it all at the end
        # would leave an empty file for the whole of that time, and an
        # empty file and a download that is still going look exactly the
        # same on disk.
        got = 0
        # The proxy drops connections.  Measured on 2026-10-25: a fetch of
        # this release died with "Remote end closed connection without
        # response" after three assets had arrived, so a single attempt per
        # asset is not enough - the proxy is the only route to the release
        # and it is not a reliable one.
        for attempt in range(1, 4):
            got = 0
            try:
                with o.open(have[name], timeout=600) as r, open(dst, "wb") as f:
                    while True:
                        chunk = r.read(1 << 16)
                        if not chunk:
                            break
                        f.write(chunk)
                        got += len(chunk)
                print("   %d bytes" % got)
                break
            except Exception as exc:
                # An asset the release does not have answers 404, and that is
                # not a failure of the fetch - the release simply does not
                # carry it.  Anything else is worth another try.
                print("   %d bytes, then %s" % (got, repr(exc)[:120]))
                if got == 0:
                    try:
                        os.remove(dst)
                    except OSError:
                        pass
                if "404" in repr(exc):
                    # The flasher is not published as a release asset, but
                    # the release's own manifest lists it - the manifest is
                    # made over the staging directory, and the flasher is
                    # in it.  So the release cannot be checked against its
                    # manifest without it, and the board's own
                    # "sha256sum -c sha256sums-release" needs it too.
                    #
                    # It comes from this repository, which is where the
                    # release was built from - and the manifest's checksum
                    # is what proves that: if the repository's copy is not
                    # the release's copy, the check below says so.
                    src = os.path.join(os.path.dirname(os.path.dirname(
                        os.path.abspath(__file__))), "templates", name)
                    if os.path.exists(src):
                        shutil.copyfile(src, dst)
                        print("   (not published as an asset; taken from %s)"
                              % src)
                        break
                    print("   (not in this release)")
                    break
                if attempt == 3:
                    raise
                time.sleep(5)

    templates = os.path.join(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))), "templates")
    bad = check_manifest(out, templates)

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
