#!/usr/bin/env python3
"""Pack the kernel into a gzip stream small enough for the 8 MiB partition.

WHY THIS EXISTS

The board's kernel partition is 8 MiB (8,388,608 bytes).  The image the build
produces is 8,395,923 bytes - 7,315 bytes too big.  `mtd write` truncates it
without saying a word; only `mtd verify` notices, and it says so after the
flash has already happened.

The partition is flashed with a legacy uImage and the loader (ZTE's own
U-Boot 2021.01) only implements gzip: lzma, lz4, zstd and bzip2 all come back
as "Unimplemented compression type N" (measured on the board, with a probe
image per type).  So the payload has to stay a gzip stream.

gzip -9 cannot get there on its own.  Measured on the 2026-10-25 kernel, the
best any zlib setting manages is 8,397,374 bytes (level 9, memLevel 8), still
8,766 over; the other strategies are far worse (Z_RLE 13.4 MB, Z_FIXED 9.4 MB,
Z_HUFFMAN_ONLY 13.8 MB, Z_FILTERED 9.3 MB).  zopfli is a deflate encoder that
searches harder for the same format and lands well below gzip -9: 8,069,537
bytes with ten iterations and block splitting, measured on the same kernel.

The kernel itself is untouched - the payload is the same raw Image, only the
container around it is smaller - so what is flashed is what was tested.  This
is the exact path the accepted image on the board came from.

WHY A SCRIPT AND NOT THE COMMAND LINE

The `zopfli` command in Ubuntu's archive is version 1.0.3 and its interface is
not what the build assumed: it has no `-q` and no `-o`, and it has no
`--splitting`.  The build called it with all three, and the command produced
nothing at all - run 167 failed with "zopfli produced nothing" after an hour
of compiling.  The Python binding has the knobs this needs by name
(numiterations, blocksplitting), is what the accepted board image was made
with, and installs from a manylinux wheel without a compiler.

    python3 tools/mkgzip.py <raw Image> <out.gz> [iterations]
"""
import gzip
import os
import sys
import time

import zopfli.gzip

# The uImage header is 64 bytes and sits in front of this payload.
HEADER = 64
PARTITION = 8 * 1024 * 1024


def main():
    if len(sys.argv) < 3:
        sys.stderr.write(__doc__)
        return 2
    src, dst = sys.argv[1], sys.argv[2]
    iters = int(sys.argv[3]) if len(sys.argv) > 3 else 10

    raw = open(src, "rb").read()
    print("kernel: %d bytes raw" % len(raw))

    # Block splitting is worth another 1.5% on this kernel (8,199,103 bytes
    # without it, 8,069,537 with it, measured on the 2026-10-25 kernel), so
    # both are tried and the smaller one is kept.  The cost is a second pass,
    # which is a couple of minutes - worth it against a partition that has
    # 8,388,544 bytes of room and a payload that wants 8,395,859.
    best = None
    for blocksplit in (False, True):
        t0 = time.time()
        out = zopfli.gzip.compress(raw, numiterations=iters,
                                   blocksplitting=blocksplit)
        print("zopfli iters=%d blocksplitting=%-5s : %d bytes (%.1fs)"
              % (iters, blocksplit, len(out), time.time() - t0))
        if best is None or len(out) < len(best):
            best = out
    payload = best

    # The payload has to still be a gzip stream the loader's gunzip can take,
    # and it has to give the same kernel back.
    if gzip.decompress(payload) != raw:
        sys.stderr.write("::error::the recompressed stream does not give the "
                         "same kernel back\n")
        return 1

    with open(dst, "wb") as fh:
        fh.write(payload)
    total = len(payload) + HEADER
    print("wrote %s: %d bytes" % (dst, len(payload)))
    print("the 8 MiB partition holds %d bytes with the uImage header: %s "
          "(spare %d)"
          % (PARTITION, "it fits" if total <= PARTITION else "IT DOES NOT FIT",
             PARTITION - total))
    if total > PARTITION:
        sys.stderr.write("::error::the compressed kernel is %d bytes over the "
                         "%d byte partition - mtd write would truncate it "
                         "silently\n" % (total - PARTITION, PARTITION))
        return 1
    if os.path.getsize(dst) != len(payload):
        sys.stderr.write("::error::the file on disk is not what was "
                         "compressed\n")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
