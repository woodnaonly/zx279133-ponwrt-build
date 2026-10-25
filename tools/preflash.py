"""The gate before the flash: is this release safe to write into the NAND?

The flash is the one thing in this project that cannot be undone easily, so
nothing goes in without being looked at first.  Every check here is about
a way the release could be wrong in a way that only shows up on the board:

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

    python tools/preflash.py <flash tag>

THE ROOTFS, AND WHY THE NAMES ARE READ OUT OF IT

The NAT rule is what this image exists for, so the gate asks about the init
script that installs it and about the thing that starts that script.  It
used to ask by looking for the bytes "sk-nat" and "sk-nat.log" in the image,
and both of those were the wrong question:

  * "sk-nat" is also part of the name of the rc.d link ("S96sk-nat"), so the
    answer could not tell the script from the link;
  * "sk-nat.log" was the name of the file the old uci-defaults redirected
    the init script's output to - a word in a comment, in a file that the
    build deletes once it has run it, in a file whose contents jffs2
    compresses.  A gate that looks for that fails on a correct image.

So the names are read out of the image instead (tools/jffs2names.py):
jffs2 compresses the contents of a file but not its name, so the names of
the files are there in plain text, and the gate can ask about a name.

THE OVERRIDE

A file named staging/ctl/preflash.allow, holding the tag of one release,
says that this release is to be flashed even though a check failed, and why.
It is written by a person, not by this script, and it is the only way past a
failed check.
"""
import gzip
import hashlib
import io
import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import jffs2names

ROOT = r"C:\UGit\openwrt\SK-D840N"
TRIAL = os.path.join(ROOT, "staging", "trial")
ALLOW = os.path.join(ROOT, "staging", "ctl", "preflash.allow")
KPART = 8388608
HDR = 64
MAGIC = 0x27051956

problems = []
allowed = False


def note(ok, what, detail=""):
    """A check.  A check that failed is a problem, unless the release has
    been given permission to be flashed anyway."""
    if ok:
        print("  %-4s %s%s" % ("OK", what, (" - " + detail) if detail else ""))
        return
    if allowed:
        print("  %-4s %s%s" % ("WARN", what, (" - " + detail) if detail else ""))
        print("       (flashed anyway: %s allows this release)" % ALLOW)
        return
    print("  %-4s %s%s" % ("FAIL", what, (" - " + detail) if detail else ""))
    problems.append(what)


def info(what, detail=""):
    """Something that is worth saying, and is not a check."""
    print("  %-4s %s%s" % ("", what, (" - " + detail) if detail else ""))


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main():
    global allowed
    tag = sys.argv[1] if len(sys.argv) > 1 else None
    if not tag:
        print("usage: preflash.py <flash tag>")
        return 2
    d = os.path.join(TRIAL, tag)
    print("the release: %s" % d)
    if not os.path.isdir(d):
        print("  there is no such directory")
        return 2

    # The permission to flash this release even though a check failed.
    if os.path.isfile(ALLOW):
        with io.open(ALLOW, encoding="utf-8") as fh:
            allow_text = fh.read()
        if tag in allow_text:
            allowed = True
            print("  NOTE  %s allows this release to be flashed" % ALLOW)

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

    # The rootfs, and the NAT rule in it.
    rfs = os.path.join(d, "flash-rootfs.jffs2")
    if os.path.isfile(rfs):
        with open(rfs, "rb") as fh:
            blob = fh.read()
        names = jffs2names.list_names(blob)
        if len(names) < 100:
            # The image could not be read as a jffs2 image, so the names
            # cannot be asked.  The bytes are all that is left, and they are
            # a weaker question - so it is said out loud.
            names = set()
            info("the rootfs could not be read as a jffs2 image",
                 "%d bytes; the names are unknown, so the bytes are asked"
                 % len(blob))
            has_init = b"sk-nat" in blob
            has_link = b"S96sk-nat" in blob
            has_udef = b"99-sk-nat" in blob
        else:
            has_init = "sk-nat" in names
            has_link = "S96sk-nat" in names
            has_udef = "99-sk-nat" in names
        note(has_init, "the rootfs carries the NAT init script",
             "/etc/init.d/sk-nat")
        note(has_link or has_udef,
             "the rootfs starts the NAT rule at boot",
             "the rc.d link is in the image" if has_link else
             ("the uci-defaults is in the image" if has_udef else
              "neither the rc.d link nor the uci-defaults is in the image"))
        if names:
            nat = sorted(n for n in names if "nat" in n.lower())
            info("the names in the rootfs with 'nat' in them",
                 ", ".join(nat) if nat else "(none)")
            info("the rootfs holds %d names" % len(names))
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
