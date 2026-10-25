# 84 — The watchdog must see a copy that kept its modification time

*2026-10-25, between the CI runs 177 and 179.*

## 1. What happened to run 177

Run 177 (commit `db4fb9f`, the flash job) started its Compile step at
`2026-10-25T09:47:15Z` and was still "in progress" at `10:51Z`, an hour
later, with the build itself dead since `10:45:45Z`.

The build's own log tells the story, and the progress branch
(`ci-progress/177-flash`) carries every beat of it:

| beat | time (UTC) | log size | quiet | wrote | what the log says |
|-----:|------------|---------:|------:|------:|--------------------|
| 15 | 10:15:29 | 3 662 | 0 | yes | `make[4] scripts/config/conf` |
| 18 | 10:21:32 | 7 392 | 0 | yes | `make[2] package/install` |
| 21 | 10:27:34 | 7 392 | 3 | yes | unchanged |
| 24 | 10:33:36 | 7 392 | 6 | yes | unchanged |
| 27 | 10:39:38 | 7 392 | 9 | yes | unchanged |
| 30 | 10:45:45 | 7 392 | 0 | **no** | `make[2] package/install` |
| 33 | 10:51:47 | **7 516** | 2 | no | `make: *** [.../toplevel.mk:233: world] Terminated` |

Two numbers in that table are the whole case.

* The log grew from 7 392 bytes to 7 516 bytes — exactly 124 bytes — between
  beats 27 and 30. The line that was added is the one make prints when it is
  signalled: `make: *** [/…/include/toplevel.mk:233: world] Terminated`.
  The build was stopped by something that sent it `SIGTERM`.
* The only thing in the Compile step that sends `SIGTERM` to make is our own
  watchdog. At beat 30 the watchdog's second rule fired — "the build has
  written nothing for 40 minutes, whatever it is printing" — and it printed
  `::error::the build has written nothing for 40 minutes, whatever it is
  printing - it is stuck, and it is being stopped`.

So the watchdog killed a healthy build.

## 2. Why it thought the build had written nothing

The rule asked whether any file under the build's directories was **newer than
a marker**:

```sh
find build_dir bin staging_dir dl tmp -type f -newer ../stale-marker
```

That is a question about the **modification time** of a file. And the install
phase of an OpenWrt build copies the root filesystem into place with `cp -a`,
which **preserves the modification time of every file it copies**: the copy
carries the mtime of the original, which is minutes or hours old. The files
the build was writing fastest were exactly the files the watchdog could not
see.

The rule was right about the question it asked and wrong about the question
it needed to ask. The question it needed is not *"was this file written"* but
*"has this file **changed**"*.

## 3. The question that is right

The change time of a file is its inode's `ctime`: the moment the inode was
last changed. A kernel sets it, and a program cannot set it — `cp -a` can
preserve the modification time, the owner and the mode, but the change time
of the copy is the moment of the copy. That is what makes it the right
question here.

`find` can ask it: `-cnewer` compares the **change time** of the found file
against the **modification time** of the reference file. The marker file's
modification time is set back into the past with `touch -d`, so

```sh
find build_dir bin staging_dir dl tmp -cnewer ../stale-marker
```

answers "has anything in the build's directories changed in the last while",
and a `cp -a` copy counts.

Measured on this machine, with five copies made with `cp -a` from a template
whose modification time is `1970-01-01`:

```
-newer  -> 0 files
-cnewer -> 5 files
  build_dir/pkg-a/object-0.o mtime=2020-01-01 ctime=2026-10-25
  build_dir/pkg-a/object-1.o mtime=2020-01-01 ctime=2026-10-25
  ...
```

The old question says the build wrote nothing. The new one says the build
wrote five files. On run 177 the difference was the difference between a
finished build and a stopped one.

## 4. Two more things the same run taught

### 4.1 One signal is not enough, and now there are three

The rule as it stood had two signals — the log and the files — and the two
have to agree. It now has three: the log, the files, **and the processes**.

* the log has not grown for **20 minutes**;
* nothing in the build's directories has changed for **30 minutes**;
* the build has started **nothing new for 15 minutes** — the youngest
  process that belongs to the build is older than that.

A build that is compiling one big file is quiet but is writing and is
starting compilers. A build that is downloading talks but is writing into
`dl/`. A build that is installing a root filesystem is quiet for a long time
but is writing and is starting `cp`. The three signals agree only when the
build really has stopped.

The rule that catches the stall that *talks* — a build stuck in a retry loop
that prints a line every few seconds and writes nothing — is not part of that
conjunction, and it now allows **90 minutes** of writing nothing instead of
40.

### 4.2 A build that is dead must not keep the step alive

After the watchdog signalled make at `10:45:45Z`, the Compile step stayed
`in_progress` for more than seven minutes. The build was dead; the job was
not.

The reason is the pipe. The step runs

```sh
timeout --kill-after=60s 280m bash -c '… exec make -j"$(nproc)"' 2>&1 | tee ../compile-parallel.log
```

and the watchdog signalled **make**, not the build. The children make had
started kept the write end of that pipe open, so `tee` never saw EOF, so the
pipeline never ended, so the step never ended — and the runner sat on a
dead build until the step's own ceiling, four hours later.

`timeout` starts the build in a process group of its own, so the group *is*
the build and everything the build started. The watchdog now signals that
group, and after five seconds whatever is left of it, and it stops watching
when the build is gone.

## 5. The rehearsal, which is the same code

The rehearsal (`tools/watchdog-dryrun.sh`) runs the shipped
`tools/compile-watchdog.sh` — the same file the workflow runs — against fake
builds, with the timings compressed. It has five cases now:

| case | expected | result |
|------|----------|--------|
| prints and writes | left alone | left alone |
| goes quiet but keeps writing | left alone | left alone |
| **writes only copies with an old modification time** | **left alone** | **left alone** |
| goes quiet and stops writing | stopped | stopped |
| prints but writes nothing | stopped | stopped |

The third case is the one that was wrong. In it the fake build writes nothing
but `cp -a` copies of a template whose modification time is `2020-01-01`,
and the rehearsal prints both questions at the end of the case:

```
  the modification-time question: 0 files
  the change-time question:     19 files
```

That is the difference between the rule that stopped run 177 and the rule
that ships now.

(The rehearsal runs on a machine whose `ps` cannot answer `-eo etimes,args`,
so the third signal reads `999999` there — the youngest-process rule is
inert in the rehearsal and live in the runner. A signal that cannot be
measured makes the rule *more* willing to stop, not less, and the two rules
it can measure are the ones that matter.)

## 6. What this does not say

* It does not explain runs 168–172. Those builds had no live channel, so
  nothing could be said about where they were, and nothing here changes
  that.
* It does not say the build of run 177 was healthy in every other respect.
  It says the watchdog had no business stopping it, and that a build which
  is stopped for no reason cannot be looked at afterwards.
* The ceiling is still the backstop: 280 minutes for the first pass, 360 for
  the step, 400 for the job. A build that is endless rather than stuck is
  left to the ceiling, which is the right judge of it.
