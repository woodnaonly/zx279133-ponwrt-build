# 83 — The build publishes its own progress, because a stuck build cannot be asked

**Date:** 2026-10-25
**Subject:** the CI compile step hangs, and every channel that could say where
has been measured; so the build now writes down where it is, while it is there.

---

## 1. What was measured, and what it says

The Compile step of the last run that finished, and of the runs that did not
(the step's own start and end, from the workflow API):

| run | job | Compile step | outcome |
|---|---|---|---|
| 160 | flash | 55.7 – 67.5 min | success |
| 161 | both | 67.2 / 67.5 min | success |
| 162 | both | 69.2 / 69.1 min | success |
| 163 | both | 37.9 / 60.2 min | success |
| 164 | both | 37.9 / 34.5 min | success |
| 165 | both | 39.3 / 40.1 min | success |
| 166 | both | 62.3 / 69.1 min | success |
| **167** | **both** | **51.2 / 32.9 min** | **success** |
| 168 | both | 353.4 / 353.3 min | cancelled by the job's own limit |
| 169 | both | 200.2 / 200.2 min | killed by the ceiling |

Run 167 is the last run whose **Compile step succeeded**.  The run as a whole
failed later, in the packaging step, on the zopfli call — which is a separate
thing, and was fixed separately (run 169).

**The boundary is sharp.**  Run 167's compile ended at `2026-10-24T19:12Z`.
Run 168's compile started at `2026-10-24T19:04Z` — forty-eight minutes
later — and never ended.  Everything that hangs, hangs from that moment on.

## 2. What did not change across that boundary

The only commit between the last good run and the first bad one is `bc923d6`,
and it does not touch the Compile step.  It touches the step *after* it (the
packaging step: the zopfli call and the 8 MiB gate) and two documents.  The
Compile step of run 167 and the Compile step of run 175 are the same code.

The inputs were checked against the tree itself, not against a note of what
they were supposed to be:

| input | value | changed? |
|---|---|---|
| base tree | `cnjn/ImmortalWrt` @ `sr1010-zx279133`, tip `5996412` (2026-10-23 22:18:27 UTC) | no — it has not moved since before run 160 |
| `packages` feed | pinned `e93a938` | no |
| `luci` feed | pinned `ed7692cb` | no |
| `routing` feed | pinned `76c93390` | no |
| `telephony` feed | pinned `2618106d` | no |
| `zte_mesh` feed | floating, tip `327101a` (2026-10-19 16:14 UTC) | no — the same tip was in run 167 |
| kernel | `cnjn/linux-mainline-zte-zxslc-sr1010` @ `96e5a241` | no |
| the board's own `DISTRIB_REVISION` | `r0-5996412` | — it agrees with the base tree |

`feeds.conf.default` pins four of the five feeds by commit and leaves
`zte_mesh` floating; that is the only input that is not nailed down, and its
tip did not move between run 167 and run 168.  The `zte-mesh` package's
`Build/Prepare` unpacks a tarball that is committed next to its Makefile —
there is no download in it, so there is nothing in that feed that can wait
for a network.

**So the tree, the feeds, the kernel and the Compile step are the same in the
run that compiled in 33 minutes and the run that never finished.**  What
changed is the world the build runs in.

## 3. The channels that could say where, and what each of them does

Every one of these was measured on this machine, on a run that was still in
flight, on 2026-10-25:

| channel | result |
|---|---|
| the job log (`api.github.com/.../actions/jobs/<id>/logs`) | **403** without a token |
| the job page's HTML | not fetchable (the request times out) |
| the run page's HTML | works, and carries the run's status — but its job list is loaded by script, so it carries no step data and no live log |
| the artifacts **listing** (`/actions/runs/<id>/artifacts`) | **200**, anonymous |
| the artifact's **content** (`/actions/artifacts/<id>/zip`) | **401** — a session is needed |
| the check-run **annotations** of a job that is still running | **empty** |

That last row is the one that matters, and it was measured twice.  Run 170
was in flight while the build had already printed
`::notice::the build starts with 97G free` into its log, and the
annotations endpoint returned zero rows.  Run 174 was in flight again: its
first pass was killed by the ceiling at 16:55, the step printed its
`::error::evidence:` lines — the tail of the parallel log and the tail of the
watchdog's record — and at 17:04 the annotations endpoint still returned
zero rows for both of its jobs.

**Annotations are published when the job ends.**  A build that is stuck is a
job that does not end, so the one channel that carries the evidence is
closed for exactly the case it was built for.

## 4. So the build writes it down itself

The build now publishes what it is doing to a branch, every sixth minute:

```
ci-progress/<run number>-<mode>       the branch
progress/<run number>-<mode>.txt       the report
```

The report carries the three things that answer "where is it":

- the **tail of the build's own log** — what it was building;
- the **processes that have been running the longest** — whether it is
  compiling or waiting for something, and on what (a stalled transfer shows
  its URL in the command line);
- the **directories it touched lately** — which phase of the build it is in;

plus the log's size, how long it has been quiet, whether anything has been
written, and the memory and the load.

**Why a branch and not something else.**  The git protocol is not the REST
API: reading it costs no API quota, needs no session, and works while the
job is running.  The branch is named after the run, so two runs never fight
over the same ref, and the file lives under `progress/`, which the
workflow's own `push:` filter does not match — so publishing progress cannot
start another build, which is the one way a diagnostic could make the thing
it diagnoses worse.

`tools/publish-progress.sh` does the publishing, and it is rehearsed by
`tools/publish-rehearse.sh` against a throwaway repository: a report is
published, read back off the remote, overwritten by the next one, and a
missing source file and a workspace that is not a repository are both
refused without failing anything.

**The rehearsal also drives the whole path the workflow uses** — the
watchdog, in a tree, publishing into a workspace that is a repository — and
that is where the first version of the script was caught: it copied its
source *after* it had changed directory, so a relative source was looked for
one directory above the workspace, and nothing was ever published.  The
rehearsal that passed an absolute path had not noticed, because the bug was
in the path and the test was not.

**The watchdog is a file of its own too** — `tools/compile-watchdog.sh` — and
the workflow and the rehearsal both run *that* file.  They used to be two
copies, and they had already drifted: the rule that was shipped and the rule
that was rehearsed were not the same rule, which is the one thing a
rehearsal cannot be allowed to be.

The reader is one command:

```
python staging/ctl/progress.py <run> <mode>
```

## 5. The stall rules, and the ceiling

The watchdog's rules were two, and they were both "the build has stopped
saying anything".  That is not enough: a build compiling one big file says
nothing for as long as that file takes, and a build that is downloading says
everything and writes nothing.  Neither of those is stuck.

The rules are now:

| what | threshold | why |
|---|---|---|
| the log has not grown | 12 minutes | `make` prints the target it is working on |
| **and** nothing under `build_dir`, `bin`, `staging_dir`, `dl` or `tmp` has been written | 20 minutes | a build that is working writes files |
| nothing has been written, whatever it prints | 40 minutes | the stall that talks: a build in a retry loop prints a line every few seconds and writes nothing at all |

The first two have to be true together before the build is stopped.  The
third is the rule that catches the build that talks and writes nothing: it
is not a conjunction, because a build that is downloading is in the same
shape, and it is saved by `dl/` being one of the directories that count.

A build that is *endless* rather than stuck — one that keeps printing and
keeps writing — is left to the ceiling, which is the right judge of it.

**The rehearsal caught the rule being wrong.**  The first version of the
rules had a third clause of its own — "quiet for half an hour, whatever the
files say" — and the rehearsal stopped a build that had gone quiet but
kept writing, which is the one thing the rules must never do: that build is
compiling one big file.  The clause was removed, and the rehearsal now
checks four cases:

| the build | expected |
|---|---|
| prints and writes | left alone |
| **goes quiet but keeps writing** | **left alone** |
| goes quiet and stops writing | stopped |
| prints and writes nothing | stopped |

**The ceiling is back up, and it is a backstop rather than a judge.**  The
first pass was capped at 90 minutes, which was a guess: the good runs take
33 to 69 minutes, and every run that hangs was killed by that cap while it
was still working.  A ceiling that guesses wrong about a slow build kills
the build it was meant to measure.  The first pass is now capped at 240
minutes, and the watchdog — which watches the build work rather than the
clock — is the judge.

**A build that was stopped is not tried again.**  A build that *failed* is a
different thing: `make` is incremental, and the same tree is often one
target away from the answer, so the retry stays for that case.  But a build
that was *stopped* — by the watchdog or by the ceiling — will stop in the
same place, and the fifty minutes the retry and the serial pass would take
are fifty minutes before the annotations are published.  So the step ends
there, and the evidence is out.

## 6. The transfers, everywhere they happen

`scripts/download.pl` gives curl and wget a connect timeout and no limit on
the transfer itself, so a mirror that accepts the connection and then stops
sending leaves the build waiting for ever.  `CURL_OPTIONS` and
`WGET_OPTIONS` were given to the Compile step; they are now given to the
**Download packages** step as well, and the **Install feeds** step gets
git's own low-speed limits, because a feed clone that stalls has the same
shape.

## 7. What this does not say

It does not say what the hang is.  It says where the build is, while the
build is there — and it is the first channel that can.  The next run with
this change in it either finishes, or says, in public, within six minutes of
each beat, what it was doing when it stopped.
