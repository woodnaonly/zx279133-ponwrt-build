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
