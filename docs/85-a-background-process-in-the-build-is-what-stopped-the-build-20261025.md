# 85 — A background process in the build is what stopped the build

*2026-10-25, between the CI runs 179 and 180.*

## 1. What the runs did

Runs 168 to 179 never finished.  Their work was done.  The machine went
idle, the build's own log said `make[2] package/install` and then nothing
for the rest of the run, the root filesystem stopped growing at 622 files,
and the Compile step sat there until the ceiling took it.

This is what the live channel of run 179 showed, beat by beat:

| beat | time (UTC) | log | quiet | wrote | root filesystem |
|-----:|------------|----:|------:|------:|-----------------|
| 27 | 12:04:17 | 5 535 | 0 | yes | growing |
| 30 | 12:10:19 | 7 392 | 0 | yes | 622 files, 12M |
| 33 | 12:16:22 | 7 392 | 6 min | yes | 622 files, 12M |
| 36 | 12:22:24 | 7 392 | 12 min | yes | 622 files, 12M |

The process table in the same snapshot is the interesting part:

```
 430 S    make --no-print-directory -j1 package/install
 428 S    bash -c ( cd .../openwrt/build_dir/target-aarch64_cortex-a53_musl/root-zte; ...
 428 S    bash -c ( cd .../openwrt/build_dir/target-aarch64_cortex-a53_musl/root-zte; ...
```

Two copies of the package manager's post-install loop, alive and waiting,
while the load average of the machine was `0.08`.

## 2. The correlation, which is one variable wide

Run 167 is the last build that completed (2026-10-24T19:12Z).  The overlay
history of this repository is short enough to read whole:

```
2e9b4e6 2026-10-25 02:56:52 +0800 flash(nat): the flashed image had no firewall, so it could not route
51ac6c6 2026-10-24 03:27:41 +0800 fix(9132): this board's own microcode, and the frames that never reached the DSA ports
```

`2e9b4e6` was pushed at 2026-10-24 18:56:52 UTC.  Run 167 finished at
19:12Z, so **run 167 is the last build that did not have it**, and run 168
is the first build that did.  Every build since has had it, and every build
since has hung in the same place.

Nothing else moved: the base tree tip, the four feeds, the kernel SHA and
the Compile step itself were the same in run 167 and in run 179 (docs/82
§2).  The one change is the overlay.

## 3. What that change added

`2e9b4e6` is what put the NAT rule into the image, and with it this file,
`/etc/uci-defaults/99-sk-nat`:

```sh
mkdir -p /etc/rc.d
ln -sf ../init.d/sk-nat /etc/rc.d/S96sk-nat
setsid sh /etc/init.d/sk-nat >/tmp/sk-nat.log 2>&1 &
```

The last line starts the init script in the background, so that the first
boot would have the rule and not only the second one.  And
`/etc/init.d/sk-nat` is a loop that never ends:

```sh
cur=""
while true; do
	...
	sleep 15
done
```

**A build runs that uci-defaults inside the package manager's chroot.**  So
the build started a loop that never ends, in the middle of the build.

## 4. Why a background process stops a build

GNU make hands its jobserver to every recipe it runs, so that the recipes
share the `-j` limit.  The jobserver is a socket, and **make waits for it
to close before it finishes**.  A background process started by a recipe
inherits that socket, and it holds it for as long as it runs - which, for
`sk-nat`, is for ever.  The build's own work is finished, the machine is
idle, and make is waiting for a socket that a process it started is holding
open.  That is the whole hang.

It is also why the build looks the way it does from the outside: the log
stops at `make[2] package/install`, nothing is written, nothing is
computed, and no amount of waiting changes anything.

## 5. The proof that the uci-defaults runs in the build

**The proof is the correlation itself.**  Run 167 is the last build without
`2e9b4e6` and run 168 is the first one with it, and every build since then
stops in the same place, while nothing else about the build changed (the
base tree, the four feeds, the kernel's SHA and the Compile step itself are
all the same - docs/82 §2).

A file in the overlay can only affect the build if the build runs it.  The
uci-defaults is a file in the overlay, so if the build did not run it, adding
it could not have changed anything, and run 168 would have built as run 167
did.  It did not: it hung.  **So the build does run the uci-defaults**, and
running it starts the loop that holds make's jobserver open.

**Two claims that were made here once were wrong, and are corrected now**
(2026-10-25, after the timestamps on the board were read properly):

* *"The link is in the image, and it is newer than everything else in
  `/etc/rc.d`."*  On the board the link is dated `Oct 24 06:45` while every
  other link is dated `Oct 24 06:18` - but the `sk-nat` **init script itself**
  is dated `06:45:34` too, and that file is not in the image at all: it was
  copied onto the board by hand on 2026-10-25, when the board was still
  running the run 165 rootfs and the CI had not published anything with the
  NAT fix in it (see §9.14 of the HANDOFF).  The hand-made copy and the
  hand-made link carry the same timestamp, and the timestamp says nothing
  about the build.  Everything the image really carries is dated `06:18:27`,
  which is the base tree's commit time (`5996412`, 2026-10-23 22:18:27 UTC)
  - that is how a reproducible build stamps its files.
* *"`/etc/uci-defaults/` on the board is empty: the build removes the scripts
  it has run."*  The board's uci-defaults are empty because the **first boot**
  runs them and deletes them: `/etc/init.d/boot` carries its own
  `uci_apply_defaults()` and calls it at line 53.  That is the device's
  mechanism, and it says nothing about the build's.

**Whether the CI's image carries the link is a question about the image, and
the image can now be asked.**  jffs2 compresses the contents of a file but
not its name, so `tools/jffs2names.py` reads the names out of a rootfs and
`tools/preflash.py` asks about names instead of about bytes - see **docs/87**.

The fix itself does not depend on any of this: the uci-defaults no longer
starts anything, and the init script is guarded against running in a build's
chroot.  If the image carries the link, the init script runs on the first
boot exactly as it does now; if it does not, the rule is installed on the
second boot, and the acceptance does two cold boots before it reads the
counters.

## 6. The fix

`/etc/uci-defaults/99-sk-nat` no longer starts anything:

```sh
mkdir -p /etc/rc.d
ln -sf ../init.d/sk-nat /etc/rc.d/S96sk-nat
```

The link is what makes the init script run, and the link is what the build
needs to put in the image.  Nothing is lost: the rule is installed by the
init script, on the first boot and every boot after it.

The init script also grows a guard, so that anything which does start it
inside a build's chroot gets one pass and an exit instead of a loop:

```sh
in_build() {
	[ -n "${IPKG_INSTROOT:-}" ] && return 0
	[ -n "${PKG_ROOT:-}" ] && return 0
	[ -n "${CI:-}" ] && return 0
	return 1
}
```

A build's chroot has no `uci` and no `nft` for this architecture, so there
is nothing to install there anyway.

## 7. What this does not say

* It does not say the builds of runs 168 to 179 were otherwise healthy.
  They got as far as the root filesystem, and the hang was after that.
* It does not make the watchdog's own mistake smaller.  The watchdog killed
  run 177's build with a rule that could not see a copy that kept its
  modification time (docs/84), and the fix there is in the same commit
  range: `56b75ee` asks whether a file *changed* rather than whether it
  was written, stops the build's whole process group so that a killed
  build cannot keep a step alive, and needs the log, the files and the
  processes to agree before it stops anything.
* The two faults are independent and both had to be fixed: without the
  watchdog fix a build is killed for the wrong reason, and without this
  fix no build finishes at all.
