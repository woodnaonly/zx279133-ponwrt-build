# 82. The build hung twice, and the ceiling was in the wrong place

**Date:** 2026-10-25
**Subject:** the CI's Compile step stopped finishing, twice in a row, and the
step timeout that was supposed to keep a hang from costing the whole job was
the thing that made the hang unrecoverable.

---

## 1. What happened

Two runs in a row sat in `Compile` and never came out of it.

| run | commit | Compile started (UTC) | Compile ended | how it ended |
|---|---|---|---|---|
| 167 | `bc923d6` | 18:21:10 | 19:12:24 | 51.2 min, success |
| 168 | `2e9b4e6` | 19:04:15 | 00:57:36 | 353.4 min, killed by the job's own 6 h limit |
| 169 | `bf0e503` | 02:23:28 | - | killed by the step's 200 min ceiling |

Run 168's two matrix jobs were killed at the same second - 353.4 and 353.3
minutes - by the job limit, which GitHub reports as `cancelled`. That is not
an error and it says nothing: a build that is slow, a build that is stuck on
a fetch and a build that is out of memory all look the same from here.

Run 169's Compile was still running at 116 minutes when it was last read,
against a step ceiling of 200 minutes.

For comparison, the same step on the same tree:

| run | 160 | 161 | 162 | 163 | 164 | 165 | 166 | 167 |
|---|---|---|---|---|---|---|---|---|
| minutes | 55.7 / 67.5 | 67.5 / 67.2 | - | 37.9 / 60.2 | 37.9 / 34.5 | 39.3 / 40.1 | 62.3 / 69.1 | 51.2 / 32.9 |

So the good runs are 33 to 69 minutes, and 116 minutes is already past the
worst of them by a factor of two.

## 2. What is not the cause

The obvious suspect is the change that came with run 168: `2e9b4e6` added the
`sk-nat` overlay files and the assertions that check them. Those assertions
are in two steps - the tree check before `Compile` and the built-rootfs check
after it - and the tree check is not the problem either: in run 169 it
completed in 0.0 minutes, and so did every step before `Compile`.

The steps before `Compile` are the same shape and the same length in run 167
and in run 169:

```
                          run 167            run 169
Free disk space          1.9 min            2.8 min
Init build environment   3.5 min            1.9 min
Clone sources            0.8 min            1.0 min
Install feeds            1.2 min            1.4 min
Configure                0.5 min            0.5 min
Download packages        1.1 min            1.8 min
Compile                  51.2 min           (running)
```

`Download packages` is the interesting one: it is the step that pulls the
whole `dl/` tree, and it finished in 1.8 minutes, so the sources were all
there before the compile started. A stalled fetch is therefore not what the
compile is waiting for either.

## 3. What cannot be said from here

The logs are not readable on this machine.

* `GET /repos/{owner}/{repo}/actions/jobs/{job_id}/logs` answers **403**
  without a token - the endpoint wants the `actions:read` scope.
* `GET /repos/{owner}/{repo}/actions/runs/{id}/job/{id}/logs` answers **404**.
* The job page in a browser says **"Sign in to view logs"**, for a public
  repository, which is GitHub's way of saying the same thing.

There is no `gh` on this machine and no token in the environment, so the
compile's own output cannot be read while the job is running or after it
has failed. The only things that can be read anonymously are the run and
job metadata (through the proxy, which has an address of its own and its own
quota) and the check-run annotations.

The check-run annotations are empty for runs 168 and 169: the `Compile` step
emits one `::notice::` before it starts, and the step that would have said
*which file* broke never ran. So the hang cannot be attributed to a file, a
package or a fetch from here. It is recorded as unexplained.

## 4. What was wrong with the ceiling

The 200 minute ceiling was on the **step**, and the retry that was supposed
to rescue a failed build was **inside the step**:

```yaml
timeout-minutes: 200
run: |
  make -j"$(nproc)" || rc=$?
  ...
  make -j"$(nproc)" || rc=$?      # the retry - inside the same step
```

So the ceiling did not fail the step and let the next line run. It killed
the step, and with it the retry, and with it every chance the tree had of
being finished. Run 168 showed the same thing at the job's 6 hour limit:
353 minutes in `Compile`, and the retry never got its turn.

The ceiling was in the wrong place, and it was in the wrong place in a way
that made the failure look like a slow runner rather than like a step that
was never given the chance to do its second half.

## 5. What was changed

The ceilings are now inside the step, and the step's own limit is the sum of
them with a margin:

```yaml
timeout-minutes: 340
run: |
  timeout --kill-after=60s 250m make -j"$(nproc)" 2>&1 | tee ../compile-parallel.log || rc=$?
  if [ "$rc" = 0 ]; then exit 0; fi
  if [ "$rc" = 124 ]; then
    echo "::warning::the first pass was killed after 250 minutes - it was not slow, it was stuck"
  fi
  timeout --kill-after=60s 70m make -j"$(nproc)" 2>&1 | tee -a ../compile-parallel.log || rc=$?
  if [ "$rc" = 0 ]; then exit 0; fi
  make -j1 V=s 2>&1 | tee ../compile-v1.log || rc=$?
```

Four things changed, and each of them is there for a reason that was measured:

1. **The first pass has its own ceiling, of 250 minutes.** The build has been
   measured at 33 to 69 minutes on this runner, so 250 is nearly four times
   the worst good run - which is what it takes to survive a runner that is
   three times slower than the ones the good runs had. A build that is
   genuinely stuck now fails at 250 minutes instead of eating the job.

2. **The retry gets 70 minutes, and it runs.** `make` is incremental, so the
   second pass only has to finish the tree the first pass left behind. The
   old shape never got here: the step's ceiling took the retry with it.

3. **The parallel build's own output goes to a file.**
   `compile-parallel.log` is now in the failure artifact, next to
   `compile-v1.log` and `openwrt/logs`. Runs 168 and 169 both ended with a
   `Compile` that had said nothing for hours and nothing to read afterwards;
   the next one will leave its last words behind.

4. **The status `124` is named out loud.** `timeout` returns 124 when it
   fires, which is neither 0 nor a compiler's own code, and a reader who
   sees only the annotations should not have to work that out.

### The trade-off in (1)

Killing `make` can leave a half-written object file behind: the compiler is
signalled while it is writing its `.o`, and the file is then newer than its
source, so the next `make` will not rebuild it.

That failure is loud, not silent. A truncated object file fails at the link,
the link error names the file, and the serial pass - which is exactly the
pass whose job is to name the failing file - is still there to be run. What
cannot happen is a corrupt object going into an image without anything
saying so.

The alternative - no ceiling on the first pass at all - is the shape that
cost run 168 its whole job.

## 6. What this does not change

Nothing here fixes a runner that stops answering. It only makes a build that
stops answerable:

* it fails in a bounded time instead of taking the job with it;
* it leaves a log behind;
* it gives the incremental retry a chance to finish the tree.

If the next run hangs again, the log will say where, and the answer will be
in that file rather than in a guess.

## 7. The sources were not the difference

Before the watchdog, the question was still open: what changed between run
167, which compiled in 51 minutes, and run 168, which sat in `Compile` until
the job's own six-hour limit took it?  Everything that feeds the build was
checked against the two dates.

* **The base tree.** `git clone --depth 1 -b sr1010-zx279133` takes the
  branch tip, so a moving branch would make every run a different tree.  The
  tip of `cnjn/ImmortalWrt` is `5996412`, committed on 2026-10-23 at 22:18
  UTC - before run 165, the first of the three good runs, and still the tip
  now.  Every run from 165 on compiled the same base tree.
* **The kernel.**  Pinned: `KERNEL_SHA` is
  `96e5a2413524742e177e383fe523ef21de5ed238`, checked out by hand after the
  clone, with its submodules updated from that commit.
* **The feeds.**  Four of the five are pinned to a commit as well -
  `packages` `e93a938`, `luci` `ed7692cb`, `routing` `76c93390`,
  `telephony` `2618106d`.  The fifth, `zte_mesh`, floats, and its newest
  commit is from 2026-10-19 - six days before the first good run.

So the inputs were the same for the runs that compiled and the runs that did
not.  What is left between them is the repository itself: run 168 is the
first run of commit `2e9b4e6`, which added the two `base-files` scripts and
the assertions around them.  Nothing in that commit plausibly hangs a
compile - it is two shell scripts in the rootfs and a few `grep`s in a step
that took 0.0 minutes - and the same cannot be said for a runner.

What it can be is a stalled fetch.  A build that waits on a package mirror
looks exactly like a build that is slow, from the outside, and the two are
told apart by what the build is *doing* - which nothing was reporting.

## 8. The build now says where it is

So the build is now watched while it runs.

A watchdog starts with the compile.  Every two minutes it writes a snapshot
of the process table, the load and the memory to `compile-watchdog.log`, and
every sixth it says one line out loud as a notice: the processes that have
been running the longest, the package directories the build has touched
lately, and how much memory is left.

```
::notice::beat 6 07:41:12 mem=912MB available dirs=[target-... linux-6.18.38] longest=[2203 curl -o ... https://... | 1190 cc1 -O2 ...]
```

That one line separates the two cases that look identical from outside.
Compilers that are working are young and there are many of them; a fetch
that is stuck is one process that has been running for an hour, and it
carries the URL it is waiting for.

The notices are annotations, and a check run's annotations are readable
without a token - the same channel the error annotations already use.  So a
build that is stuck now explains itself within minutes rather than at the
end of a ceiling.  The watchdog log is uploaded with the failure artifacts
as well, and the step that reports what the build was doing when it stopped
prints it: the log tail says what was being built, the watchdog says whether
it was being built or waited for.

The change is diagnostic only.  The ceilings, the retry and the serial pass
are untouched, so a build that was going to succeed still succeeds and a
build that was going to fail still fails - it just says so while it is doing
it.

## 9. The build stops itself, and the download gives up

Two things were measured on 2026-10-25 that turned "the build is stuck" from
a guess into a thing the build can be asked.

**The first: annotations are published when a job ends, not while it runs.**
Run 170 had been sitting in `Compile` for two hours with its "the build
starts with 97G free" notice already printed, and the API returned *zero*
annotations for it.  A notice is therefore not a live channel, however often
it is printed, and the watchdog's notices are worth something only after the
job they belong to has finished - which is exactly when they are least
wanted.

**So the build stops itself.**  The build's own log is the proof of life:
`make` prints the target it is working on, so a log that has not grown in
twenty-four minutes is a build that is not working.  The watchdog watches
that, and when the log goes quiet it stops the build - the `make` process,
by the pid the build wrote for itself on the way in.  The step then fails,
the job ends, and the annotations are published with the evidence in them:
the tail of the parallel log, and the watchdog's own record of what the
process table looked like at the moment the build was stopped.

The rehearsal for this is `tools/watchdog-dryrun.sh`, which runs the
watchdog against a fake build twice - one that keeps printing, and one that
goes quiet.  The first is left alone and the second is stopped, which is the
whole contract: **a build that is merely slow keeps printing and is never
touched.**

**The second: a download that has connected and then gone quiet is given up
on.**  `scripts/download.pl` gives curl and wget a connect timeout - five
seconds - and no limit on the transfer itself.  So a mirror that accepts the
connection and stops sending leaves the build waiting forever, and a build
waiting forever is indistinguishable from a slow one from the outside.  The
two variables `download.pl` already reads, `CURL_OPTIONS` and
`WGET_OPTIONS`, are now set in the Compile step: a transfer that stays under
100 bytes a second for two minutes is given up on, and the build says which
URL it was waiting for.

**And the ceilings came down.**  The first pass is 90 minutes instead of
250, the retry 40 instead of 70, and the serial pass 10 instead of
unbounded.  The good runs of this build took 33 to 69 minutes, and the
watchdog catches a build that has stopped making progress long before
90 minutes - so the long ceiling was buying nothing except the time it took
to find out.
