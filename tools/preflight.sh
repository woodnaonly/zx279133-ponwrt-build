#!/bin/sh
# Everything that has to be true before the gate round, in one command, all of it read-only.
#
# The board is reachable for as long as the owner decides, so the pre-flight must be a single
# invocation with a fixed order rather than seven commands to remember under time pressure -
# and it must stop at the first thing that is wrong, because every step after it is either
# meaningless (a gate payload built from a dtb that fails its own partition table) or
# destructive (nothing here writes flash; the flash round is separate on purpose).
#
#	sh tools/preflight.sh
set -eu
cd "$(dirname "$0")/.."
# Whether the caller named a run. The default below is a hardcoded id, so "nobody said
# which build" has to be distinguishable from "the operator picked this build" - the first
# one is allowed to keep what is already staged, the second one is not.
RUN_EXPLICIT=0
[ -n "${RUN:-}" ] && RUN_EXPLICIT=1
RUN=${RUN:-38243906799}          # 3a29f4c: root at 0xf00000 *and* pinned to /dev/mtdblock5
KEY=${KEY:-$HOME/.ssh/id_onu}
# Which address the board answers on: 192.168.9.1 is the installed lan bridge, 192.168.9.2 is
# eth0's static address and the only one that can reply once mainline owns the board. Each
# step below defaults to the same value, so exporting this is what makes them agree.
DEV=${DEV:-root@192.168.9.1}
export DEV
SSH="ssh -q -o BatchMode=yes -o IdentitiesOnly=yes -i $KEY -o UserKnownHostsFile=/dev/null -o StrictHostKeyChecking=no -o ConnectTimeout=4 $DEV"

echo "=== 0. is the board up? ==="
if [ "${SKIP_BOARD:-0}" = 1 ]; then
	echo "  check skipped (SKIP_BOARD=1) - only useful for testing the steps below offline"
# A marker on the far end, not a pattern in uname's output: `uname -a` prints "Linux 4.19.136+
# #4 SMP ...", which never contains "Linux version" - that string lives in /proc/version. The
# old grep rejected a board that was answering ssh fine, and the round lost its window to it.
elif $SSH 'echo BOARD_ALIVE $(uname -r)' 2>&1 | grep -q BOARD_ALIVE; then
	echo "  yes - and nothing in this script writes flash"
else
	echo "  NO. Nothing below this line can fix that from the PC. In the order worth checking:"
	echo "  the wired adapter has no carrier (a Disconnected link is the cable or the ONU's power,"
	echo "  not the software - tools/backwatch.sh logs its state while you wait), or the board is"
	echo "  up on the other address, in which case rerun with DEV=root@192.168.9.2."
	exit 1
fi

echo "=== 1. build $RUN finished? ==="
STATUS=$(curl -sf --max-time 30 "https://api.github.com/repos/woodnaonly/zx279133-ponwrt-build/actions/runs/$RUN" |
	python -c "import sys,json;d=json.load(sys.stdin);print(d['status'],d.get('conclusion'),d['head_sha'][:7])")
echo "  $RUN: $STATUS"
PULL=1
case "$STATUS" in
completed*success*) ;;
*)
	# A board window is not reschedulable, so an unpublished build must not throw it away:
	# if a flash set is already staged and passes its own table check, it is usable, because
	# the gate round is what measures the partition numbering. What is *not* acceptable is
	# pulling an older published release over the staged set and silently changing the bytes
	# a previous verdict permitted.
	if [ -s staging/trial/flash-uImage ] && [ -s staging/trial/flash-dtb.bin ]; then
		echo "  $RUN is not published yet, but a flash set is already staged:"
		md5sum staging/trial/flash-uImage staging/trial/flash-dtb.bin | sed 's/^/    /'
		echo "  continuing with it - the gate round decides whether those bytes are flashable"
		PULL=0
	else
		echo "  not usable yet - the artifacts of a running build are not published, and nothing is staged"
		exit 1
	fi;;
esac

echo "=== 2. pull and verify its flash set ==="
# Before pulling anything, ask whether what is already staged would survive its own gate.
#
# On 2026-10-15 this pulled release 21 over a verified release-25 set and got away with it,
# because the hardcoded default RUN is itself a run that still reports `completed success`:
# step 1 matched, PULL became 1, and eight-day-old images replaced the current ones. Step
# 4d then refused - the safety net held - but by then the good set was already gone, and
# staging/trial was left holding trial files from one run and flash files from another.
# That mixed state is the exact failure this file's own comments warn about.
#
# So: when the caller did NOT name a run, and the staged set already carries the current
# tree, leave it alone. An explicit RUN is operator intent and still pulls.
if [ "$PULL" = 1 ] && [ "$RUN_EXPLICIT" = 0 ] && [ -s staging/trial/flash-uImage ] &&
   sh tools/check-imagelineage.sh >/dev/null 2>&1; then
	echo "  the staged set already carries the current tree - not pulling over it"
	echo "  (RUN was not set; set RUN=<id> to pull a different build on purpose)"
	PULL=0
fi
if [ "$PULL" = 1 ]; then
	sh tools/gettrial.sh flash "$RUN"
else
	echo "  skipped (would overwrite the staged set) - manifest verification happened at fetch time"
fi

echo "=== 3. the partition table the images will be booted with ==="
python tools/dtbparts.py staging/trial/flash-dtb.bin root=0xf00000,0xf000000

echo "=== 4. gate payload = exactly those bytes ==="
sh tools/mkgate.sh

echo "=== 4b. the writer, rehearsed against a fake flash ==="
# This is the script that erases the partitions the board boots from. Rehearsing it costs a
# second and needs no board; finding its bugs during a flash window does not.
sh tools/simwrite.sh
# ...and rehearsed again with the ROLLBACK pair, because the way back has to survive the same
# hazards as the way forward and those are different bytes (4814777 vs 8105331).
SRC=staging/rollback sh tools/simwrite.sh

echo "=== 4c. the gate payload's report path, rehearsed end to end ==="
# A round that cannot report looks exactly like a kernel that did not boot, and a board window
# is not reschedulable. This runs the real /init out of the built initrd and scores it.
sh tools/siminit.sh

echo "=== 4d. is the staged rootfs newer than the tree it came from? ==="
# This one stops the run, and it is placed here, before step 5, on purpose. Everything
# from step 5 on needs the board; this needs nothing but the staged files. Putting the
# most consequential refusal last meant that an offline rehearsal stopped at step 5 on a
# missing ssh and never reached it - a gate whose behaviour under time pressure was unknown.
#
# The staged set in staging/trial is the 2026-10-12 build. Read with jefferson, its
# lib/upgrade/platform.sh is upstream's 2172 B rather than the fixed 5118 B overlay, and its
# etc/board.d/02_network is an early revision of this project's own overlay carrying
# `ucidef_set_interface_lan "eth0"` and no 192.168.9.1 at all. Flashing that would bring the
# board up on 192.168.1.1 - which every tool in this project reports as a dead board - while
# carrying the defect that drops the saved configuration on the next sysupgrade and still
# returns 0.
#
# Not comparing dates or the release manifest: both can describe a build while the payload
# on disk is older, and sha256sums-release was in fact regenerated over an October 12 rootfs.
#
# The gate checks two layers: the files inside the rootfs image, and the scripts and the
# integrity manifest that travel beside it. The staged set is behind in both - its
# flash-sk-d840n.sh has no pre-flash sha256 check at all and still points the operator at
# restore_set/ for a rollback that cannot happen, its trial-kexec.sh gunzips 30 MB before
# verifying, and its sha256sums-release omits the flasher itself. Layer 2 is the one that
# survives a fresh rootfs: those files are loose, not inside the image.
ALLOW_UNVERIFIED=${ALLOW_UNVERIFIED:-0}
export ALLOW_UNVERIFIED
if [ "$ALLOW_UNVERIFIED" = 1 ]; then
	echo "  ALLOW_UNVERIFIED=1 - this step will only warn. Set it back to 0 afterwards."
	# The waiver covers "the image could not be read", not "the image was read and is
	# stale". Those are opposite findings. An earlier version ran the gate as
	# `... || echo "(skipped by operator decision)"`, so a positive refusal was filed as a
	# skip - the one thing this step exists to prevent, wearing the operator's own name.
	if ! sh tools/check-imagelineage.sh; then
		echo "  the waiver did NOT cover this: the gate read what it was given and still refuses."
		echo "  That is a finding, not a skip. Do not flash on the strength of ALLOW_UNVERIFIED=1."
	fi
else
	sh tools/check-imagelineage.sh
fi

echo "=== 5. bootloader in flash versus the image we would write ==="
sh tools/checkuboot.sh

echo "=== 6. snapshot of what the round is about to overwrite ==="
sh tools/preflashbackup.sh

echo "=== 6b. the rollback pair, staged and hashed before it is ever needed ==="
# There is no console and no A/B on this board, so "rollback" has to be a command that is
# already proven to exist - not something assembled after the board stops answering.
sh tools/mkrollback.sh

echo "=== 6c. is the rollback pair actually the one this board boots? ==="
# mkrollback.sh carves the pair and appends a permit line; it does not, and cannot, tell you
# whether the result still equals what was verified. This re-derives that from the pre-flash
# snapshots on every run, so a later regeneration that goes wrong fails here - before the
# board goes quiet - rather than after. It also states outright that restore_set/ is not a
# rollback set: that directory holds ZTE firmware image data, and there is no filesystem in
# its 241 MB. It was previously described here as "the verified stock set", which was wrong.
sh tools/checkrollback.sh

echo "=== 7. how the vendor kernel reads the head of root (ECC reference) ==="
sh tools/roothead.sh

echo "=== 8. will the post-flash system be reachable at all? ==="
sh tools/reachcheck.sh

echo "=== 9. what a write would be permitted for, right now ==="
# Informational: the gate round is what grants mainline's permit, so both of these failing
# here is the expected state. They are printed so nothing has to be remembered later, and
# because the rollback pair losing its permit would be a real surprise.
sh tools/gatecheck.sh staging/trial/flash-uImage staging/trial/flash-dtb.bin ||
	echo "  (expected before the gate round: K5 is what records this)"
sh tools/gatecheck.sh staging/rollback/flash-uImage staging/rollback/flash-dtb.bin ||
	echo "  (expected if step 6b ran with SKIP_BOARD=1 or the dumps were stale)"

echo "=== 9b. is a refusal above about the kernel, or only about the build timestamp? ==="
# The permit hashes the whole uImage, and mkimage rewrites the header's time field on every
# build, so pulling a rebuilt set breaks the permit line even when the kernel inside is
# bit-identical. That distinction decides whether a gate round is needed or only a decision, and
# it has to be answerable before someone is standing over the board - read-only, and it grants
# nothing either way.
sh tools/payload-id.sh staging/trial/flash-uImage ||
	echo "  (payload differs from the pinned one - the refusal above is real; see step 9)"
# The stale-rootfs refusal is step 4d, above: it needs no board, so it belongs before the
# steps that do.

cat <<'EOF'

PREFLIGHT COMPLETE - all read-only. Next is the gate round (task 10), which still writes
nothing:
  python tools/ncsrv.py 5555 staging/ctl/reports.log &
  CTLTAG=K5 IMAGE_LOCAL=staging/ctl/gate-Image REMOTE_IMAGE=gate-Image \
    DTB_LOCAL=staging/ctl/gate-dtb REMOTE_DTB=gate-dtb INITRD=ctl-initramfs.cpio.gz \
    sh tools/pushctl.sh
  CTLTAG=K5 sh tools/gatewatch.sh 900 &
  ssh root@192.168.9.1 'DELAY=45 MASK=0 CTLTAG=K5 setsid sh /tmp/ctl/ctl-exec.sh >/dev/null 2>&1 &'
gatewatch scores the report - CTLALIVE for tag K5, kver of the jumped kernel, root as mtd5 in
/proc/mtd, "root read: IDENTICAL" - and appends the flash permit for exactly those bytes to
staging/ctl/flash.permit. Only then:
  sh tools/flash-direct.sh dry
  sh tools/flash-direct.sh write
The round is what decides the numbering: it reports root's index as the *booting kernel*
registered it, and the permit requires 5. So a build whose dtb is unpinned (dtbparts prints
"NOT pinned") is still flashable if the round reports mtdblock5 - never spend a board window
waiting on a rebuild. If it reports anything else, the pinned build is mandatory, and the
window has answered the question either way.
And if the new system does not come back, the staged rollback is the same script:
  SRC=staging/rollback EXPECT='root=0xf00000,0xf100000' sh tools/flash-direct.sh write
EOF
