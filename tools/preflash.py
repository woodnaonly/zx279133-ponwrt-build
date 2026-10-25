"""The gate before the flash: is this release safe to write into the NAND?

The flash is the one thing in this project that cannot be undone easily, so
nothing goes in without being looked at first.  Every check here is about a
way the release could be wrong in a way that only shows up on the board:

  * the manifest the release ships does not describe the files next to it -
    the flasher checks the images against it on the board, and if it does not
    match, the flash is written anyway and only noticed afterwards;
  * the uImage is bigger than the 8 MiB kernel partition - `mtd write`
    truncates it without a word, and the board then comes up with "wrong
    image header";
  * the uImage does not carry a gzip stream the loader can read - the board's
    U-Boot answers "Unimplemented compression type N" for everything else;
  * the kernel inside is not the 6.18.38 this project is about;
  * the rootfs does not carry the NAT rule, so the image would come up as a
    box that answers ssh and routes nothing.

    python staging/ctl/preflash.py <flash tag>
"""
import gzip
import hashlib
import io
import os
import struct
import sys

ROOT = r"C:\UGit\openwrt\SK-D840N"
TRIAL = os.path.join(ROOT, "staging", "trial")
KPART = 8388608
HDR = 64
MAGIC = 0x27051956

problems = []


def note(ok, what, detail=""):
    print("  %-4s %s%s" % ("OK" if ok else "FAIL", what,
                           (" - " + detail) if detail else ""))
    if not ok:
        problems.append(what)


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main():
    tag = sys.argv[1] if len(sys.argv) > 1 else None
    if not tag:
        print("usage: preflash.py <flash tag>")
        return 2
    d = os.path.join(TRIAL, tag)
    print("the release: %s" % d)
    if not os.path.isdir(d):
        print("  there is no such directory")
        return 2

    # The manifest.
    man = os.path.join(d, "sha256sums-release")
    if os.path.isfile(man):
        listed = {}
        with io.open(man, encoding="utf-8") as fh:
            for line in fh:
                parts = line.split()
                if len(parts) == 2:
                    listed[parts[1].lstrip("*")] = parts[0]
        for name in ("flash-uImage", "flash-dtb.bin", "flash-rootfs.jffs2"):
            p = os.path.join(d, name)
            if name not in listed:
                note(False, "%s is not in the manifest" % name)
            elif not os.path.isfile(p):
                note(False, "%s is missing" % name)
            else:
                got = sha256(p)
                note(got == listed[name], "%s matches the manifest" % name,
                     "" if got == listed[name] else "%s != %s" % (got, listed[name]))
    else:
        note(False, "the release ships no manifest")

    # The kernel image.
    kimg = os.path.join(d, "flash-uImage")
    if os.path.isfile(kimg):
        size = os.path.getsize(kimg)
        note(size <= KPART, "the uImage fits the 8 MiB kernel partition",
             "%d bytes, %d to spare" % (size, KPART - size))
        with open(kimg, "rb") as fh:
            raw = fh.read(HDR)
            (magic, hcrc, ts, isize, load, ep, dcrc, os_, arch, typ, comp) = \
                struct.unpack(">IIIIIIIBBBB", raw[:32])
            name = raw[32:64].split(b"\0")[0].decode("utf-8", "replace")
            note(magic == MAGIC, "the uImage has the right magic",
                 "0x%08x" % magic)
            note(comp == 1, "the payload is gzip, which is all the loader reads",
                 "compression type %d" % comp)
            fh.seek(HDR)
            payload = fh.read(isize)
        try:
            kernel = gzip.decompress(payload)
            note(True, "the payload decompresses",
                 "%d bytes of kernel, md5 %s" % (len(kernel),
                                                 hashlib.md5(kernel).hexdigest()))
            k = kernel
            i = k.find(b"Linux version")
            if i >= 0:
                text = k[i:i + 160].split(b"\x00")[0].decode("utf-8", "replace")
                note("6.18.38" in text, "the kernel announces 6.18.38", text[:90])
                note("SMP" in text, "the kernel's version line is complete",
                     text[-40:])
            else:
                note(False, "the kernel carries no version line")
        except Exception as exc:
            note(False, "the payload decompresses", repr(exc)[:120])
    else:
        note(False, "flash-uImage is missing")

    # The rootfs.
    rfs = os.path.join(d, "flash-rootfs.jffs2")
    if os.path.isfile(rfs):
        with open(rfs, "rb") as fh:
            blob = fh.read()
        note(b"sk-nat" in blob, "the rootfs carries the NAT rule",
             "%d bytes" % len(blob))
        note(b"sk-nat.log" in blob,
             "the rootfs carries the uci-defaults that starts it")
    else:
        note(False, "flash-rootfs.jffs2 is missing")

    # The dtb.
    dtb = os.path.join(d, "flash-dtb.bin")
    if os.path.isfile(dtb):
        with open(dtb, "rb") as fh:
            head = fh.read(4)
        note(head == b"\xd0\x0d\xfe\xed", "the dtb has the dtb magic",
             head.hex())
    else:
        note(False, "flash-dtb.bin is missing")

    print("")
    if problems:
        print("preflash: %d problem(s): %s" % (len(problems), "; ".join(problems)))
        return 1
    print("preflash: the release is fit to be flashed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
