# 80 — The CI did not fail on the compression, it failed on the compiler's command line

**Date:** 2026-10-25
**Runs:** 167 (failed), 168 (cancelled)

## What the two runs actually did

Run 167 and run 168 looked like one failure seen twice. They were two
different failures, and the second one was hiding the first.

Run 167 stopped in `Package flash images` with the annotation

```
::error::zopfli produced nothing
```

Run 168 was reported as

```
cancelled — The job has exceeded the maximum execution time of 6h0m0s
```

which reads like the same problem, one run later and one build longer.

The step timings say otherwise. Run 168's two jobs both reached `Compile` at
19:04 and were still in it at 00:57, when the job's own limit killed them:

| job | step | from | to | result |
|---|---|---|---|---|
| initramfs | Compile | 19:04:15 | 00:57:36 | cancelled |
| flash | Compile | 19:04:17 | 00:57:37 | cancelled |

So run 168 never reached the compression at all. It spent 5h53m in the OpenWrt
build and was killed there. The `zopfli` line was not the thing that failed;
it was the thing that had not been reached yet.

For scale, the same step in the two runs before:

| run | initramfs Compile | flash Compile |
|---|---|---|
| 166 | 62:20 | 69:05 |
| 167 | 51:14 | 32:55 |

33 to 69 minutes. Run 168's 5h53m is not a slow build, it is a build that
stopped.

## Why zopfli produced nothing

The build called the distribution's `zopfli` command like this:

```sh
"$ZOPFLI" -q --i5 --splitting --gzip -o "$IMG" "$RAW"
```

Ubuntu's archive has zopfli 1.0.3, and its command line is not that. It has:

```
-h  -c  -v  --i#  --gzip  --zlib  --deflate  --splitlast
```

There is no `-q` (quiet), no `-o` (output file) and no `--splitting`. The
command therefore rejected its own arguments, wrote nothing, and left the
next line - the check for the file - to report the only thing it could see:
that no file was there. The message pointed at zopfli; the fault was the way
zopfli was called.

## What replaces it

`tools/mkgzip.py`, which does the compression through zopfli's Python
binding:

```sh
python3 tools/mkgzip.py "$RAW" "$IMG" 10
```

The binding has the knobs by name (`numiterations`, `blocksplitting`), and it
installs from a manylinux wheel - no compiler, no header package.

The reasons for choosing the binding over fixing the command line:

* **It is the path that was already accepted.** The image on the board is
  8,069,601 bytes, made by this binding with `numiterations=10`. The tool in
  the CI was checked against it: `tools/mkgzip.py` on the same kernel produces
  8,069,537 bytes of payload, and the payload is **byte for byte the same**
  as the payload inside the image the board booted. So the CI and the bench
  make the same image from the same kernel, and the acceptance that was
  earned on the board still holds for what the CI ships.

* **The knobs are named rather than positional.** The binding's
  `blocksplitting=True` is the thing that was worth another 1.5% (8,199,103
  bytes without it, 8,069,537 with it, measured on the 2026-10-25 kernel).
  A command line that cannot express it was never going to produce the
  accepted size.

* **Nothing about the kernel changes.** The payload is the same raw `Image`,
  only the container around it is smaller. What is flashed is what was
  tested.

The script also refuses to write a payload that does not fit, so the size
gate is now in two places: the script, and the 8 MiB check on the finished
`flash-uImage` that has been in the build since run 167.

## Why the build stopped, and what stops it next time

A build that runs for hours without finishing has no cause of its own; the
cause is whatever it was waiting on. OpenWrt's build fetches over the
network, and **git has no default timeout**: a connection that opens and
then stops delivering leaves the fetch blocked indefinitely. That is the
shape of a six-hour build with no output.

Two things were changed, neither of which changes what is built:

* `GIT_HTTP_LOW_SPEED_LIMIT=1000` and `GIT_HTTP_LOW_SPEED_TIME=60` on the
  compile step: a transfer that is delivering under 1 KB/s for a minute gives
  up, so a stalled fetch fails and can be retried instead of hanging.

* The compile step has its own `timeout-minutes: 200`, and the job's 6h
  limit is left as the outer backstop. The step ceiling is three times the
  worst good run (69 minutes), so it only fires on something that is
  already wrong - and when it fires it names the step that stopped, instead
  of reporting the whole job as "cancelled", which says nothing about
  whether the compile was slow, stuck on a fetch, or out of memory.

* One retry of the parallel build on the same tree before the slow
  diagnostic pass. `make` is incremental, so a second attempt continues from
  where the first stopped, and a failure that was one stalled fetch is
  usually gone by the second pass.

## The shape of the thing that was actually wrong

Two different faults, in two different steps, one push apart:

* a command line with three options that do not exist, which failed in
  twenty seconds and said "zopfli produced nothing";
* a build that stopped, which failed after six hours and said nothing.

The second is the one worth remembering. A step that reports its own
timeout, and a fetch that is allowed to fail, are what turn "the build
stopped for six hours and said nothing" into "the build stopped, and here is
where".
