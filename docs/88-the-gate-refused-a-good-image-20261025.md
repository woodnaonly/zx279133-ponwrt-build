# 88 — The gate refused a good image, and the build had finished

**Date:** 2026-10-25
**Subject:** run 180 built the kernel image in 37.8 minutes - the first build
since run 167 that finished - and the flash job was then refused by the CI's
own gate, because the gate was asking the image the wrong question.

---

## 1. What happened

Run 180 was the first build with the background process taken out of the
uci-defaults (docs/85).  Its Compile step ran from 12:34:40Z to 13:12:30Z -
**37.8 minutes - and completed.**  The initramfs job succeeded and published
its release.

The flash job did not.  It failed in the step that packages the images, on
its own gate:

```
::error::the built rootfs does not contain sk-nat - the flashed image would not masquerade
```

So the fix of docs/85 is confirmed: the build finishes now, and the build
finishes *in the time a build should take*.

## 2. The gate, and the question it asked

```sh
strings "$RS" | grep -qx 'sk-nat' || {
  echo "::error::the built rootfs does not contain sk-nat ..."; exit 1; }
strings "$RS" | grep -q 'sk-nat.log' || {
  echo "::error::the built rootfs does not contain the uci-defaults that starts sk-nat"; exit 1; }
```

Both questions are wrong, and the first one is wrong in a way that has
nothing to do with jffs2's compression.

**`strings` does not put a name on a line of its own.**  `strings` prints
the runs of printable characters in a file, and a jffs2 directory entry -
the node that carries the name of a file - has binary bytes on both sides
of the name.  So the name comes out as part of a longer run.  Measured on
the rootfs of run 166, which is a real image with 767 files in it:

```
network      exact-line=4    substring=25
dnsmasq      exact-line=0    substring=22
init.d       exact-line=0    substring=33
rc.d         exact-line=1    substring=1
```

`init.d` is in that image - it is one of the root's own directories - and
`strings | grep -cx 'init.d'` is **0**.  What `strings` prints is:

```
']init.d'
'../init.d/gpio_switch'
'{../init.d/log'
'V../init.d/urngd'
```

The gate was not asking "is the file in the image".  It was asking "is
there a line in the printable runs of the image that is exactly `sk-nat`",
and the answer to that question does not depend on what was built.  **The
gate would have refused every image, good or bad.**

**The second check was looking for a word in a comment.**  `sk-nat.log` was
the name of the file that the old uci-defaults redirected the init script's
output into:

```sh
setsid sh /etc/init.d/sk-nat >/tmp/sk-nat.log 2>&1 &
```

That line is gone (docs/85: it was the process that held GNU make's
jobserver open and stopped the build).  What is left of the name is a
sentence in the comment above it - and the contents of a file are exactly
what jffs2 compresses, so a word inside a file is not there to be found:

```
$ python -c "d=open('flash-rootfs.jffs2','rb').read(); print(d.count(b'sk-nat'), d.count(b'sk-nat.log'))"
0 0
```

## 3. The gate that asks the right question

jffs2 compresses the **contents** of a file but not its **name**: the
directory entry node that carries the name is stored as it is.  So the
names can be read out of the image without decompressing anything, and
the gate can ask about names.

`tools/jffs2names.py` does that.  Its layout was measured on the rootfs of
run 166, not taken from a book, and it was checked twice: the image yields
767 names, every one of them a real file name, and their inode numbers
cover every inode in the image - so the list is the whole of the file names
and not a sample of them.  `tools/test-jffs2names.py` is the regression
test for the other half of it: a reader that only ever says "no" would
pass the run 166 case and would refuse a correct image.

The gate now asks:

| what is looked for | what it means |
|---|---|
| `sk-nat` | the init script itself, by its exact name |
| `S96sk-nat` | the rc.d link that starts it - there if the build ran the uci-defaults |
| `99-sk-nat` | the uci-defaults that makes the link - there if the build did not |

One of the last two has to be there, or the script never runs.  The gate
prints the names it found, so the next failure says what is in the image
instead of saying that something is not.

## 4. The uci-defaults installs the rule as well

Which of the two the image carries is not something the uci-defaults can
know, and it makes a difference to the board:

* a build that **runs** the uci-defaults creates the link while the image
  is being made, so the first boot already has the link, and the init
  script runs on the first boot;
* a build that **does not** run it leaves the link to the first boot, and
  the links in `/etc/rc.d` are read when they are run - which has already
  happened by the time the uci-defaults run.  So the first boot would
  have the link and nothing running, and the rule would be a boot late.

So the uci-defaults now installs the rule itself as well as making the
link, and the first boot has the NAT either way.  Nothing in it runs
during a build: the whole block is skipped when `IPKG_INSTROOT`, `PKG_ROOT`
or `CI` is set, and it starts no process - the process that used to be
started there is what stopped runs 168 to 179.

The rule it installs is the same one the init script installs, with the
same table and the same name (`ip nat`), and the port is read from the
image's own configuration, so the counters are read with the same command
the acceptance uses.

## 5. The same mistake was in the bench's gate

`tools/preflash.py` - the gate that runs on this side, before anything is
written to the NAND - had the same two questions in the same shape, and
was fixed in the same way (docs/87).  The two gates are the same question
asked in two places, and they now have the same answer.

## 6. What this changes for the plan

Nothing about the plan's shape: the build finishing is what docs/85 was
about, and it happened.  What was still missing was a release to flash,
and the release could not be published because a gate that could not be
satisfied stood in the way of publishing it.
