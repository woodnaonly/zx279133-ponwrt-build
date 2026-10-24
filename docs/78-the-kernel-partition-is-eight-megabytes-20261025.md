# The kernel partition is eight megabytes, and gzip is the only thing that fits

**2026-10-25.** The G4 flash ran into a wall that has nothing to do with
drivers, NAT or the kernel: the image the build produced does not fit in the
partition it is meant to go into, and the tool that writes it does not say
so. This is the whole story of that wall, and the fix.

## The geometry, read out of the board itself

The board's own U-Boot environment lives in the `boot` partition and is
readable from Linux (`/dev/mtd1`), which is where the numbers below come
from - they are not read out of a datasheet:

```
bootcmd=mtd read kernel 0x88000000; mtd read dtb 0x88800000; bootm 0x88000000 - 0x88800000
bootargs=console=ttyAMA0,115200n8 root=/dev/mtdblock5 rootfstype=jffs2 printk.time=1 U-Boot 2.0.0 20250417161359 0x200000 0x0 0x83 0x83 UpModeInfo=0x00000040 regioncode=0x0
mtdparts=spi-nand0:262144k@0x0(whole),2048k@0x0(boot),8192k@0x200000(kernel),1024k@0xa00000(dtb),4096k@0xb00000(parameter tags),246784k@0xf00000(root),
bootdelay=1
```

Three things follow.

1. The loader reads the partition **by label**, and the label `kernel` is
   `8192k` at offset `0x200000`. The kernel image therefore has to be at
   most **8,388,608 bytes**, and it has to be a legacy uImage, because the
   bootcmd ends in `bootm <addr> - <dtb addr>` and there is no `boot` command
   in this U-Boot at all.
2. `root=/dev/mtdblock5` is hard-coded in the environment. The mainline dtb
   that goes into the `dtb` partition has to number its partitions the same
   way, and it does: `boot`=1, `kernel`=2, `dtb`=3, `parameter tags`=4,
   `root`=5, `scratch`=6, with `root` at `0xf00000` and the same 8 MiB
   `kernel` at `0x200000` the environment names. The dtb splits the
   environment's one big `root` partition into `root` (240 MiB) plus
   `scratch` (1 MiB), which together are exactly the environment's
   `246784k` - so the two layouts agree, and `root=/dev/mtdblock5` is the
   right partition on the mainline kernel too.
3. The partition table is in the `boot` partition, and **no flasher in this
   project touches `boot`**. So the geometry cannot be changed from this
   side. The image has to shrink.

## What the build produced, and why nothing said so

The 2026-10-25 build (run 165) produced:

| artefact | bytes |
|---|---|
| raw `Image` | 20,568,072 |
| `Image.gz` (gzip -9) | 8,395,859 |
| `flash-uImage` (uImage header + that gzip) | **8,395,923** |
| the `kernel` partition | 8,388,608 |

7,315 bytes over. `mtd write` **truncates without a word** - it writes what
fits and reports success. The first flash on the board reported success for
all three partitions and the board still would not boot, and the only thing
that caught it was `mtd verify`, which said:

```
Verifying kernel against /tmp/flash-uImage ...
3c95ebcf... - kernel
e1bf4dd8... - /tmp/flash-uImage
```
(the two hashes differ: the partition holds a truncated image).

This is the worst kind of failure, because the flash *says* it worked.

## The loader only knows gzip

The obvious way out is a smaller kernel: lzma, lz4, zstd, bzip2. None of
them are available. The board's U-Boot (`U-Boot 2021.01-svn308776`, ZTE's own
build) was probed with one image per compression type, each tftp-loaded and
booted:

| `IH_COMP` | compression | what the loader said |
|---|---|---|
| 1 | gzip | boots |
| 2 | bzip2 | `Unimplemented compression type 2` |
| 3 | lzma | `Unimplemented compression type 3` |
| 4 | lzo | `Unimplemented compression type 4` |
| 5 | lz4 | `Unimplemented compression type 5` |
| 6 | zstd | `Unimplemented compression type 6` |

The binary is stripped, so there is no symbol to look for either. **gzip is
the only compression this loader implements**, and the payload has to stay a
gzip stream.

An lzma uImage was built anyway (6,173,605 bytes, which fits with room to
spare) and it is what the board was left with for a while - it fails with
`Unimplemented compression type 3` on every boot, which is at least an
unambiguous symptom.

## gzip cannot be improved on by another gzip

With the format fixed, the only lever is the encoder. Every zlib setting was
tried on the same raw `Image`:

| encoder | bytes | over the limit by |
|---|---|---|
| gzip -9 (the build's own) | 8,395,859 | 7,251 |
| zlib level 9, memLevel 9 | 8,397,374 | 8,766 |
| zlib level 9, Z_FILTERED | 9,271,933 | 883,325 |
| any other level/memLevel/strategy pair | worse | - |

**gzip -9 is already the best a gzip implementation can do here**, and it is
7,251 bytes over the line. No amount of tuning gets there.

## zopfli

zopfli is a DEFLATE encoder that searches much harder than zlib does. It
writes a **standard gzip stream** - the same container, the same decoder on
the board - and it is normally 3-8% below gzip -9. On this kernel:

| encoder | gzip payload | uImage | against the 8 MiB partition |
|---|---|---|---|
| gzip -9 | 8,395,859 | 8,395,923 | 7,315 over |
| zopfli, 10 iterations, no block splitting | 8,199,103 | 8,199,167 | 189,441 over |
| zopfli, 10 iterations, **with block splitting** | **8,069,537** | **8,069,601** | **319,007 to spare** |

The kernel is untouched: the payload is the *same* raw `Image` the trial
kernel booted and passed G2 and G3 on - only the container around it is
different. That matters for the evidence: what is flashed is the kernel
that was tested, byte for byte, and not a differently-configured build.

The reproduction is `staging/ctl/mkgzipuimage.py` (in the working tree, not
in the repository): it takes the uImage the build produced, strips the
64-byte header, decompresses the gzip payload back to the raw `Image`,
re-compresses it with zopfli, rebuilds the uImage header with the right
CRC and size, and checks that the result still decompresses to the same
kernel.

## The gate

A build that ships an image that does not fit is worse than no build at
all, because the board will report a successful flash and then fail to
boot with nothing to go on. The workflow now refuses to publish one:

```yaml
KPART=8388608
KSIZE=$(stat -c %s flash-uImage)
if [ "$KSIZE" -gt "$KPART" ]; then
  echo "::error::flash-uImage is $KSIZE bytes and the kernel partition holds only $KPART - mtd write would truncate it silently"
  exit 1
fi
```

and the compression step that makes it fit is in the same workflow:

```yaml
"$ZOPFLI" -q --i5 --splitting --gzip -o "$IMG" "$RAW"
gzip -dc "$IMG" | cmp - "$RAW" || { echo "::error::the zopfli stream does not decompress to the kernel"; exit 1; }
```

The `cmp` is not decoration: the loader only knows gzip, so a stream that
the board's gunzip cannot take would fail as `Unimplemented compression
type N` at boot, with nothing in the build log to say why.

The board-side flasher (`templates/flash-sk-d840n.sh`) already refuses an
oversized image ("$label: $len bytes does not fit in $size"), but that is
the last line of defence, and it happens on the board, after the release
has already been published.

## What is on the board now

The kernel partition holds the zopfli image, written and verified:

```
Verifying kernel against /tmp/flash-uImage.gzipfit ...
bc1b9f32f25a9b1b63bad5d6df3b2d4c - kernel
bc1b9f32f25a9b1b63bad5d6df3b2d4c - /tmp/flash-uImage.gzipfit
Success
```

and an independent read-back of the partition - `dd` the first 8,069,601
bytes of the partition that carries the label `kernel` and hash them -
gives the same md5 as the file on the PC
(`329f1bbc1b9b5af2d6d5ba634c2d3bdf`).

## Two smaller things that cost time here

* **`mtd`'s argument order is `<imagefile> <device>`**, not the other way
  round. `mtd write kernel /tmp/image 8069601` prints the usage and writes
  nothing - and it still exits 0 in a script that only checks the exit
  status of the whole chain.
* **A static neighbour entry for 192.168.9.1 on this PC's WLAN adapter**
  (from an earlier experiment) sends every unpinned packet to the board
  out of the radio. The board was up and answering pings while `ssh` said
  `connect to host 192.168.9.1 port 22: Connection timed out`. Every tool
  that talks to the board now pins the source address: `-b` for ssh, `-S`
  for ping, `--interface` for curl.
