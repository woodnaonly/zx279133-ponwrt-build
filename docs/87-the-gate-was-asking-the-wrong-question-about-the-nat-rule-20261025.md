# 87 — The gate was asking the wrong question about the NAT rule

**Date:** 2026-10-25
**Subject:** the pre-flash gate looked for two strings in the rootfs, and both of
them were the wrong question - one of them would have refused a correct image,
and the other could not tell the init script from the link that starts it.

---

## 1. What the gate asked

`tools/preflash.py` is the one thing that stands between a release and the
NAND, and the flash is the one action in this project that does not undo
itself.  Its rootfs check was this:

```python
note(b"sk-nat" in blob, "the rootfs carries the NAT rule", ...)
note(b"sk-nat.log" in blob,
     "the rootfs carries the uci-defaults that starts it")
```

Both of those are questions about **bytes**, and the NAT rule is a question
about **files**.

## 2. Why "sk-nat.log" was the wrong question

`sk-nat.log` was the name of the file that the old uci-defaults redirected
the init script's output into:

```sh
setsid sh /etc/init.d/sk-nat >/tmp/sk-nat.log 2>&1 &
```

That line is gone (docs/85: it was the process that held GNU make's jobserver
open and stopped the build).  What is left of it is the sentence in the
comment above the line - so, in the current image, `sk-nat.log` exists only
as a word inside a comment, in a file that:

* is deleted once it has run (a uci-defaults that has run is removed), and
* is **compressed** when it is in the image.

And that last point is the one that matters.  **jffs2 compresses the contents
of a file.**  A word inside a file is not there to be found:

```
$ python -c "d=open('flash-rootfs.jffs2','rb').read(); print(d.count(b'sk-nat'), d.count(b'sk-nat.log'))"
0 0
```

A gate that looks for a word in a comment is a gate that fails on a correct
image and passes on a wrong one.  The check was written when the uci-defaults
really did redirect its output to that file, and it was never revised when
the redirect was removed - the check outlived the thing it was checking.

## 3. Why "sk-nat" was not enough either

The name of the init script is `sk-nat` and the name of the link that starts
it is `S96sk-nat`.  The string `sk-nat` is inside the second one, so a
question about the string cannot tell whether the script is in the image or
only the link that points at it.

And the link alone is not enough: a link to a script that is not there
starts nothing, and a script that is not linked starts nothing either.

## 4. What jffs2 actually stores

In jffs2 only the **data** of a file is compressed.  The directory entry node
- the node that carries the *name* of a file - is stored as it is.  So the
names of the files in an image can be read out of it without decompressing
anything, and the gate can ask about a name.

`tools/jffs2names.py` does that.  The layout it uses was measured on the
rootfs of run 166, not taken from a book:

```
0   magic(2) type(2) totlen(4) hdr_crc(4)
12  crc(4) ino(4) mode(4) uid_or_flags(4) usn(4)
28  namelen(1) flags(1) unused(2)
32  unused(8)
40  name(namelen)
```

The image is a list of nodes on four byte boundaries, each starting with the
magic `0x1985` (little endian) and a type; `0xe001` is the node that carries
a name, and the name is the last thing in the node - the node's length is 40
plus the length of the name, with no padding.

What came out of run 166's rootfs:

```
the image holds 839 names
name nodes: 839, distinct ino: 839
other nodes: 2987, distinct ino: 841
name-node inos with no other node: 2 [0, 1]
other inos with no name node: 4
```

That is the whole list and not a sample of it: the inode numbers of the name
nodes cover every inode in the image, and the first of them are the root's
own children in order - `bin`, `dev`, `etc`, `lib`, `lib64`, `mnt`,
`overlay`, `proc`.

## 5. What the gate asks now

```python
names = jffs2names.list_names(blob)
has_init = "sk-nat" in names          # /etc/init.d/sk-nat
has_link = "S96sk-nat" in names       # /etc/rc.d/S96sk-nat
has_udef = "99-sk-nat" in names       # /etc/uci-defaults/99-sk-nat

note(has_init, "the rootfs carries the NAT init script", "/etc/init.d/sk-nat")
note(has_link or has_udef, "the rootfs starts the NAT rule at boot", ...)
```

The second check asks the real question - *is there anything in this image
that starts the rule* - and it accepts either of the two things that can do
it.  If the image cannot be read as a jffs2 image at all, the gate says so
and falls back to the bytes, because a check that quietly stops checking is
worse than a weak one.

The gate's verdict on run 166's release, which is the image this project
already knows to be wrong:

```
  OK   flash-uImage matches the manifest
  OK   flash-dtb.bin matches the manifest
  OK   flash-rootfs.jffs2 matches the manifest
  FAIL the uImage fits the 8 MiB kernel partition - 8395923 bytes, -7315 to spare
  OK   the uImage has the right magic - 0x27051956
  OK   the payload is gzip, which is all the loader reads - compression type 1
  OK   the payload decompresses - 20568072 bytes of kernel, md5 e3ef0bbc67198c691f6b42da9488894b
  OK   the kernel announces 6.18.38 - Linux version 6.18.38+ (runner@runnervmwhx2h) ...
  OK   the kernel's version line is complete - U ld (GNU Binutils) 2.44) #0 SMP Fri Oct
  FAIL the rootfs carries the NAT init script - /etc/init.d/sk-nat
  FAIL the rootfs starts the NAT rule at boot - neither the rc.d link nor the uci-defaults is in the image
       the names in the rootfs with 'nat' in them - dropbear.alternatives, ip-full.alternatives, ...
       the rootfs holds 767 names
  OK   the dtb has the dtb magic - d00dfeed

preflash: 3 problem(s): the uImage fits the 8 MiB kernel partition; the rootfs carries the NAT init script; the rootfs starts the NAT rule at boot
```

Both of the things that are wrong with that image are caught, and each of them
for its own reason.

There is one more thing in the gate now: a file named
`staging/ctl/preflash.allow`, holding the tag of one release, says that this
release is to be flashed even though a check failed.  It is written by a
person, never by the gate, and it is the only way past a failed check.

## 6. The board could not answer the question

The obvious way to find out whether the build runs the uci-defaults is to
look at the board: if `/etc/rc.d/S96sk-nat` is in the image, it has a
different timestamp from the rest of the image, and that difference says when
it was made.

On this board:

```
lrwxrwxrwx 1 root root 16 2026-10-24 06:18:27 +0800 S96network -> ../init.d/network
lrwxrwxrwx 1 root root 16 2026-10-24 06:45:34 +0800 S96sk-nat  -> ../init.d/sk-nat
-rwxr-xr-x 1 root root 2817 2026-10-24 06:45:34 +0800 /etc/init.d/sk-nat
```

27 minutes after everything else - and that is *not* the build.  The images
are stamped with the base tree's commit time, `5996412`, 2026-10-23
22:18:27 UTC, which is 2026-10-24 06:18:27 in the board's own time zone; so
everything in the image that is not stamped with that time was made later by
something else.  And what made it was not the build: **the `sk-nat` files on
this board were put there by hand**, during the NAT round of 2026-10-25,
which is why the init script itself carries the same 06:45:34 stamp as the
link.

So the board's timestamps say nothing about what the CI's image holds, and
the gate has to read the image itself.  It can now.

## 7. The other bug in the same round: the release's name

While the chain was being checked, one more thing turned up in the automatic
round, and it is worth writing down because it would have fired at exactly
the wrong moment.

`staging/ctl/g4auto.sh` took the release's name out of the fetch log with

```sh
TAG=$(grep -m1 'the release is there:' "$OUT" | awk '{print $NF}')
```

and the line it was matching was

```
=== the release is there: skd840n_flash_180_40193314412 ===
```

`$NF` is the last field of the line - which is `===`.  The automatic round
would have fetched the release, found that the log did not name it, and
stopped - at the moment the release appeared, which is the moment the round
cannot afford to waste.

The line now ends with the tag, and the name is also asked of git when the
log does not give it:

```sh
TAG=$(grep -m1 'the release is there:' "$OUT" | awk '{print $NF}' |
	sed -e 's/===//g')
case "$TAG" in
skd840n_*) ;;
*)	TAG=$(git ls-remote --tags ... | ... ) ;;
esac
```

git answers that without spending the API's quota, which is the same reason
the tag is watched with git in the first place.

## 8. What this changes for the plan

Nothing about the plan's shape.  It makes the last gate ask the right
question about the one thing the image exists for, and it removes a failure
that would have happened at the worst possible moment.
