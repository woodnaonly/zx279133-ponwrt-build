# 85 — The CI's own release, flashed and booted twice, and routing

**Date:** 2026-10-25
**Subject:** the last link in the chain - the image that GitHub Actions
built, downloaded, written into the NAND, and booted twice on its own, with
the NAT rule inside it rather than added by hand.

---

## 1. What was left to close

The G4 acceptance had already been met once, on 2026-10-25 between 01:35 and
03:05: the board was flashed from the RAM environment, the plug cut the
power twice, and both times the board came up by itself and answered - 14/14
on the mainline gate and 5/5 on `verify_router.sh`.

What that round did not close was the last link.  The image it flashed was
built on this PC: the kernel came out of the run 165 release, and the
compression into the 8 MiB partition was done here with the same zopfli the
CI now uses, and the bytes were checked against the CI's own output
byte for byte.  That is a strong argument, and it is still an argument -
the CI's own release was never fetched, never written, never booted.

So the round this document records did one thing: it took the release that
GitHub Actions published, wrote it into the flash exactly as the CI says
to, and put the board through the two cold boots and the NAT round again.

## 2. The release

<<RELEASE>>

## 3. The bytes are the same kernel, and not the same bytes

The image that was flashed on 2026-10-25 at 01:35 - the one that passed the
two cold boots - was:

| file | bytes | md5 |
|---|---|---|
| `flash-uImage` (zopfli'd on this PC) | 8069601 | `329f1bbc1b9b5af2d6d5ba634c2d3bdf` |
| `flash-dtb.bin` | 13040 | `8c460c9794558ac7e18bcb18d903a228` |
| `flash-rootfs.jffs2` | 4587524 | `55ee805ae7013fe333419577daf9e130` |

<<CI_BYTES>>

Two things are worth saying about the comparison, because they are the two
ways one can get this wrong.

**The uImage can never be byte-identical.**  A uImage header holds the time
the image was made, so two images built from the same kernel at different
moments are different files.  What has to match is the payload - the gzip
stream, and the kernel that comes out of it.

**The kernel cannot be byte-identical either, and for a reason that is not
about the build.**  The kernel's version line carries the name of the
machine that built it:

```
Linux version 6.18.38+ (runner@runnervmwhx2h) (aarch64-openwrt-linux-musl-gcc ...) #0 SMP Fri Oct 23 22:18:27 2026
```

GitHub's runners have a different hostname each time, so the kernel binary
differs from run to run even when the source, the configuration and the
toolchain are identical.  The date in that line, on the other hand, is the
base tree's commit time - `5996412`, 2026-10-23 22:18:27 UTC - which is
pinned, and which is why the *source* is provably the same.

So the right claim is not "the CI's image is the image that was accepted".
It is: **the same sources, the same configuration and the same toolchain,
built by the CI, flashed by the CI's own flasher, and booted by the board
itself.**  `tools/preflash.py` checks what can be checked before anything is
written, and `staging/ctl/cmpuimage.py` compares what can be compared after
the fetch.

## 4. The flash

<<FLASH>>

## 5. Cold boot 1

<<COLD1>>

## 6. Cold boot 2

<<COLD2>>

## 7. The NAT round

<<NAT>>

## 8. What is still open

G5, the PON state machine, is still frozen.  Everything in this document is
M1: a lab router on Ethernet, with the 9132's jacks behind it, masquerading
one client and serving another.  Nothing here says anything about the PON
data path.
