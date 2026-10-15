#!/bin/sh
# Run the announce payload's real /init on the PC and score what it reports.
#
# Why: a board window is not schedulable - it happens when the owner powers the unit on, and
# the gate round is the step that must not be wasted. The round is scored entirely from one
# text report, so a broken /init, a report gatewatch cannot parse, or a permit that never
# gets written all look identical from here: "no answer". This exercises the whole chain
# against the bytes that will actually run - the /init extracted from the built initrd, its
# push, the parser, the permit, and the check the flash asks for first.
#
#	sh tools/siminit.sh [path/to/ctl-initramfs.cpio.gz]
#
# Substitutions made to the extracted copy, and only these: /www, /proc/cmdline and /proc/mtd
# point into a scratch directory, /dev/mtdblock5 points at a file whose head is the ECC
# reference, and PATH gains the stub directory so the payload's `nc` is the local sink. The
# device paths that do not exist on this OS (leds, netdevs, kmsg) simply do nothing.
set -u
cd "$(dirname "$0")/.."
ARCH=${1:-staging/ctl/ctl-initramfs.cpio.gz}
# One scratch directory per process: this harness is called both by hand and from
# tools/preflight.sh, and a second instance rm-rf-ing and re-writing the first one's blocks is
# how a green payload turned into ten phantom FAILs at 11:17 on 2026-10-13 - none of them about
# the board. Set T=... to keep a directory for debugging.
T=${T:-/tmp/siminit-$$}
TAG=${CTLTAG:-K5}
[ -s "$ARCH" ] || { echo "$ARCH missing - python tools/mkctlram.py --mode announce --tag $TAG" >&2; exit 1; }
[ -s staging/trial/flash-uImage ] || { echo "need the staged flash set - sh tools/gettrial.sh flash <run>" >&2; exit 1; }
rm -rf "$T"; mkdir -p "$T/bin" "$T/www"

# Stop the payload from the previous stage.
#
# `kill $(jobs -p)` looked like the obvious way to do this and silently did nothing: a
# non-interactive `sh` (dash on Debian, and the harness is invoked as `sh tools/siminit.sh`)
# has job control off, so `jobs -p` prints nothing even with a background job running. The
# payload therefore survived every "teardown" and kept pushing from its `while :` beacon loop
# for its full 75 s timeout, including into the stages that deliberately ran a different
# uname stub. The vendor round then read the 6.x report the armed payload had left behind and
# reported mbox_write=ARMED, which is why "and says why" failed while the guard it tests was
# provably correct.
#
# The PID comes from $! at the moment the job is started, which needs no job control. SIGTERM
# first so the payload can exit on its own, SIGKILL as the backstop, then wait for the child to
# be gone: returning early is what let the old process write after the next stage had cleared.
RUNPID=""
stop_run() {
	[ -n "$RUNPID" ] || return 0
	kill "$RUNPID" 2>/dev/null
	sleep 1
	kill -9 "$RUNPID" 2>/dev/null
	wait "$RUNPID" 2>/dev/null
	RUNPID=""
}

python - "$ARCH" "$T/init" >"$T/tag" <<'PY'
import gzip, struct, sys, re
src, dst = sys.argv[1], sys.argv[2]
d = gzip.decompress(open(src, "rb").read())
pos = 0
while True:
    if d[pos:pos + 6] != b"070701":
        raise SystemExit("desynced at %d - not newc" % pos)
    f = [int(d[pos + 6 + i * 8: pos + 14 + i * 8], 16) for i in range(13)]
    namesize, filesize = f[11], f[6]
    name = d[pos + 110:pos + 110 + namesize - 1].decode("latin1")
    pos += 110 + namesize; pos += (-pos) % 4
    if name == "init":
        body = d[pos:pos + filesize]
        open(dst, "wb").write(body)
        print("extracted /init: %d B from %s" % (filesize, src), file=sys.stderr)
        # The tag is baked into this /init by tools/mkctlram.py --tag, so the default below
        # has to follow the artefact rather than a constant: the staged initrd keeps whatever
        # round last built it (K13LE2 at the time of writing), while $TAG defaulted to K5.
        # Ten checks then compared K5 against K13LE2 and failed for a reason that had nothing
        # to do with the payload. CTLTAG still overrides, which is what a fresh round wants.
        m = re.search(rb"^TAG=([A-Za-z0-9]+)", body, re.M)
        print(m.group(1).decode() if m else "")
        raise SystemExit(0)
    pos += filesize; pos += (-filesize) % 4
PY
[ -s "$T/init" ] || { echo "no /init inside $ARCH" >&2; exit 1; }

# Adopt the tag the initrd actually carries unless the caller named one explicitly.
if [ -z "${CTLTAG:-}" ] && [ -s "$T/tag" ]; then
	TAG=$(cat "$T/tag")
	echo "tag: $TAG (from the initrd's /init; CTLTAG overrides)"
fi

# the head of "root" the payload has to hash identically to the vendor kernel's reading
dd if=/dev/zero bs=1k count=512 2>/dev/null | tr "\0" "S" >"$T/mtdblock5"
dd if="$T/mtdblock5" bs=64k count=8 2>/dev/null | md5sum | cut -d" " -f1 >"$T/ref.line"
{ echo "# simulated 4.19 reference (the same 512 KiB the payload will read)"; cat "$T/ref.line"; } >"$T/roothead.md5"
echo "console=ttyAMA0,115200n8 root=/dev/mtdblock5 rootfstype=jffs2 ctltag=$TAG ctlpc=127.0.0.1" >"$T/cmdline"
# the table the alias-pinned board dts is expected to produce: boot=1 kernel=2 dtb=3
# "parameter tags"=4 root=5 scratch=6, and no master device because partitions-only
{
	echo "dev:    size   erasesize  name"
	echo "mtd1: 00200000 00020000 \"boot\""
	echo "mtd2: 00800000 00020000 \"kernel\""
	echo "mtd3: 00100000 00020000 \"dtb\""
	echo "mtd4: 00400000 00020000 \"parameter tags\""
	echo "mtd5: 0f000000 00020000 \"root\""
	echo "mtd6: 00100000 00020000 \"scratch\""
} >"$T/mtd"

# The substitutions below are path *prefixes*, so a name in /init that starts like one of them
# rewrites into nonsense: `/tmp/mnterr` matched the `/mnt` rule and became
# `/tmp/<T>/mnterr`, a redirect that cannot be created, so the mount probe "failed" without the
# stub ever being called. Keep the payload's scratch files off the substituted namespaces.
sed -e "s|/dev/mtdblock5|$T/mtdblock5|g" -e "s|/proc/mtd|$T/mtd|g" -e "s|/proc/cmdline|$T/cmdline|g" \
	-e "s|/dev/mem|$T/devmem|g" -e "s|/proc/iomem|$T/iomem|g" \
	-e "s|/www|$T/www|g" -e "s|/mnt|$T/mnt|g" -e "s|PATH=/bin:/sbin|PATH=$T/bin:/bin:/sbin|" "$T/init" >"$T/init.run"
sh -n "$T/init.run" || { echo "the payload's own /init does not parse after substitution" >&2; exit 1; }
# sh -n on an empty file is a pass, which is how one broken sed expression here reported a
# clean /init of 0 B.
[ -s "$T/init.run" ] || { echo "substitution produced an empty /init - check the sed expression list" >&2; exit 1; }
echo "extracted /init parses (sh -n), $(wc -c <"$T/init.run") B"

cat >"$T/bin/nc" <<EOF
#!/bin/sh
echo "nc \$1 \$2 <- \$3" >>"$T/pushes"
cat >"$T/pushed.\$2"
exit 0
EOF
cat >"$T/bin/uname" <<EOF
#!/bin/sh
# The report line's value only has to be *this kernel's* version, and the point of the field
# is that gatecheck demands the 6.x build being flashed - so state it, rather than letting
# the PC kernel 3.6.3 pass as evidence about a board kernel.
echo "6.18.38+"
EOF
chmod +x "$T/bin/nc" "$T/bin/uname"

# mainline's jffs2, as far as a PC can stand in for it. The probe's whole purpose is that the
# payload states whether the mount *worked*, so the stub succeeds only for exactly the
# read-only jffs2 mount /init asks for and fails anything else - which also keeps the sim from
# "passing" if that line ever changes shape.
cat >"$T/bin/mount" <<EOF
#!/bin/sh
echo "mount \$*" >>"$T/mounts"
case "\$*" in
"-t jffs2 -o ro "*) exit 0;;
*) exit 1;;
esac
EOF
cat >"$T/bin/umount" <<EOF
#!/bin/sh
echo "umount \$*" >>"$T/mounts"
exit 0
EOF
chmod +x "$T/bin/mount" "$T/bin/umount"
# The RAM mailbox writes with `dd of=/dev/mem bs=1024 seek=<block>`, and 0x9c180000/1024 is
# 2557440: a real dd here would ask this PC for a 2.6 GB file. The stub keeps the two facts that
# matter - which block a write landed in, and what the payload reads back from it - and passes
# every other dd call (the mtdblock5 hashes) through to the real binary.
cat >"$T/bin/dd" <<EOF
#!/bin/sh
A=" \$* "
case "\$A" in
*" of=$T/devmem "*)
	SK=\$(echo "\$A" | tr " " "\n" | sed -n "s/^seek=//p")
	IF=\$(echo "\$A" | tr " " "\n" | sed -n "s/^if=//p")
	cat "\$IF" >"$T/devmem.\$SK" 2>/dev/null; exit 0;;
*" if=$T/devmem "*)
	SK=\$(echo "\$A" | tr " " "\n" | sed -n "s/^skip=//p")
	[ -s "$T/devmem.\$SK" ] || exit 1
	cat "$T/devmem.\$SK";;
*) exec /usr/bin/dd "\$@";;
esac
EOF
chmod +x "$T/bin/dd"
# busybox on the board has hexdump; this PC does not, and the payload uses it to prove the reset
# routing write landed. Emulate only the one invocation it makes: -e '1/4 "%08x"' over 4 bytes.
cat >"$T/bin/hexdump" <<"EOF"
#!/bin/sh
W=$(dd bs=1 count=4 2>/dev/null | od -An -tx1 | tr -d " 
")
printf '%s%s%s%s' "${W:6:2}" "${W:4:2}" "${W:2:2}" "${W:0:2}"
EOF
chmod +x "$T/bin/hexdump"
mkdir -p "$T/mnt/sbin" "$T/mnt/bin"
echo '#!/bin/sh' >"$T/mnt/sbin/init"
echo '#!/bin/sh' >"$T/mnt/bin/sh"
# -x is what the probe asks, and MSYS honours the emulated exec bit
chmod +x "$T/mnt/sbin/init" "$T/mnt/bin/sh"
# the two facts the payload reads off the installed root: what it is, and whether the shell
# survives a kernel that has no ports on the lan bridge
mkdir -p "$T/mnt/etc/config" "$T/mnt/lib/modules/4.19.136+"
printf "option Interface 'lan'\n" >"$T/mnt/etc/config/dropbear"
printf "DISTRIB_DESCRIPTION='OpenWrt 24.10.8'\n" >"$T/mnt/etc/openwrt_release"

timeout 75 sh "$T/init.run" >/dev/null 2>&1 &
RUNPID=$!
n=0
while [ $n -lt 60 ] && [ ! -s "$T/pushed.5555" ]; do n=$(( n + 1 )); sleep 1; done
[ -s "$T/pushed.5555" ] || { echo "the payload never pushed a report - that is the failure this sim exists to catch" >&2; exit 1; }
echo "=== what the payload pushed ==="; sed 's/^/  | /' "$T/pushed.5555"

fails=0
check() { if eval "$2"; then echo "  ok   $1"; else echo "  FAIL $1"; fails=$(( fails + 1 )); fi; }
P="$T/pushed.5555"
check "carries this round's tag"        'grep -q "CTLALIVE tag=$TAG" "$P"'
check "reports its kernel version"      'grep -q "^kver=6.18.38+$" "$P"'
check "hashes the head of root"         'grep -q "first512k_md5=$(cat $T/ref.line)" "$P"'
check "read the whole 512 KiB"          'grep -q "^root_bytes_read=524288$" "$P"'
check "reports the partition list"      'grep -q "\"root\"" "$P"'
check "says the installed root mounted" 'grep -q "^jffs2_ro_mount=OK$" "$P"'
check "and what it found behind it"     'grep -q "^jffs2_userspace=.\+" "$P"'
check "really asked for a ro jffs2 mount" 'grep -q "^mount -t jffs2 -o ro $T/mtdblock5 $T/mnt$" "$T/mounts"'
check "names the installed release"      'grep -q "^installed_release=OpenWrt_24.10.8$" "$P"'
check "reports the installed kmods"      'grep -q "^installed_kmods=4.19.136+$" "$P"'
check "counts dropbear's Interface lines" 'grep -q "^installed_dropbear_Interface=1$" "$P"'
check "reads the PC address off cmdline" 'test "$(grep -c "^nc 127.0.0.1 5555" $T/pushes)" -gt 0'
# This pass has no /proc/iomem substitute, so it is the vendor-4.19 case: the window is
# ordinary RAM there, and writing 4 KiB into it from a control round could hit any page the
# running system owns. The guard has to leave the mailbox off *and say so in the report*.
check "report states the mailbox verdict" 'grep -q "^mbox_write=off-window-not-reserved$" "$P"'
check "an unreserved window is not written" 'test ! -e "$T/devmem.2557440"'

# score it exactly the way a live round is scored, then ask the flash what it thinks
: >"$T/log"
( sleep 2; cat "$P" >>"$T/log" ) &
LOGF="$T/log" REF="$T/roothead.md5" PERMIT="$T/permit" GATED_DTB=staging/ctl/gate-dtb \
	SRC=staging/trial CTLTAG="$TAG" sh tools/gatewatch.sh 25 2>&1 | tee "$T/gw.out" | sed 's/^/  > /'
echo "=== permit written by that verdict ==="; sed 's/^/  | /' "$T/permit" 2>/dev/null || echo "  (none)"
check "permit names these exact bytes" \
	'grep -q "uimage_md5=$(md5sum staging/trial/flash-uImage | cut -d" " -f1)" "$T/permit"'
check "permit says root is mtdblock5"   'grep -q "root_index=5" "$T/permit"'
check "permit says the ECC read matched" 'grep -q "root_read=IDENTICAL" "$T/permit"'
check "permit says root mounted"        'grep -q "jffs2_ro=OK" "$T/permit"'
check "the scorer warns about the shell" 'grep -q "dropbear names an Interface" "$T/gw.out"'
if PERMIT="$T/permit" sh tools/gatecheck.sh staging/trial/flash-uImage staging/trial/flash-dtb.bin; then
	echo "  ok   a flash of exactly these bytes would be permitted"
else
	echo "  FAIL the round scored but the flash still refuses"; fails=$(( fails + 1 ))
fi

# The negative case, which is the one that matters: gatewatch records what a round said, and
# gatecheck is what decides. A round that came up on the *vendor* kernel states facts about
# those bytes and must still not buy a permit for mainline.
sed -e 's|^kver=.*|kver=4.19.136+|' "$P" >"$T/pushed.vendor"
: >"$T/log2"
( sleep 2; cat "$T/pushed.vendor" >>"$T/log2" ) &
LOGF="$T/log2" REF="$T/roothead.md5" PERMIT="$T/permit2" GATED_DTB=staging/ctl/gate-dtb \
	SRC=staging/trial CTLTAG="$TAG" sh tools/gatewatch.sh 20 >/dev/null 2>&1 || true
check "a vendor-kernel round is still recorded as it reported" 'grep -q "kver=4.19.136+" "$T/permit2"'
if PERMIT="$T/permit2" sh tools/gatecheck.sh staging/trial/flash-uImage staging/trial/flash-dtb.bin >/dev/null 2>&1; then
	echo "  FAIL a gate round that ran the vendor kernel would permit the flash"; fails=$(( fails + 1 ))
else
	echo "  ok   a gate round that ran the vendor kernel does NOT permit the flash"
fi

# The second negative that matters, and the one the mount probe exists for: a kernel that reads
# root *identically* to the vendor one and still cannot build a filesystem out of it. Every
# other fact in the report stays good, so a refusal here is attributable to the mount alone.
sed -e 's|^jffs2_ro_mount=.*|jffs2_ro_mount=FAIL|' -e 's|^jffs2_err=.*|jffs2_err=wrong:filesystem|' \
	"$P" >"$T/pushed.nomount"
: >"$T/log3"
( sleep 2; cat "$T/pushed.nomount" >>"$T/log3" ) &
LOGF="$T/log3" REF="$T/roothead.md5" PERMIT="$T/permit3" GATED_DTB=staging/ctl/gate-dtb \
	SRC=staging/trial CTLTAG="$TAG" sh tools/gatewatch.sh 20 >/dev/null 2>&1 || true
check "a round that cannot mount root is recorded as it reported" 'grep -q "jffs2_ro=FAIL" "$T/permit3"'
if PERMIT="$T/permit3" sh tools/gatecheck.sh staging/trial/flash-uImage staging/trial/flash-dtb.bin >/dev/null 2>&1; then
	echo "  FAIL a root mainline cannot mount would still permit the flash"; fails=$(( fails + 1 ))
else
	echo "  ok   a root mainline cannot mount does NOT permit the flash"
fi

# --- the armed case, on these same real /init bytes ---------------------------------------
# The board DTS is what makes 0x9c000000-0x9dffffff a reserved range, so hand the guard that
# line and run the payload a second time. This is the only way to find out here whether the
# mailbox writes at all, and which block each of the two reports lands in - a mistake in those
# two dd calls would otherwise only show up as a silent board window.
printf '9c000000-9dffffff : Reserved memory\n' >"$T/iomem"
rm -f "$T/devmem.2557440" "$T/devmem.2557442" "$T/devmem.2557444"
# One pass at a time: the payload's /init ends in a beacon loop that keeps pushing, so a second
# run started while the first is alive reads the *first* one's report and both verdicts land on
# whichever stub happens to be in place. stop_run (see its definition above) is what actually
# ends the previous one.
stop_run
rm -f "$T/devmem.2557440" "$T/devmem.2557442" "$T/devmem.2557444" "$T/pushed.5555"
timeout 75 sh "$T/init.run" >/dev/null 2>&1 &
RUNPID=$!
n=0
while [ $n -lt 60 ] && [ ! -e "$T/devmem.2557444" ]; do n=$(( n + 1 )); sleep 1; done
check "early marker is in the window before any flash read" 'grep -q "CTLALIVE tag=$TAG mbox=early" "$T/devmem.2557440"'
check "the pre-mount block has the root hash and the mtd table" \
	'grep -q "first512k_md5=$(cat $T/ref.line)" "$T/devmem.2557442" && grep -q "\"root\"" "$T/devmem.2557442"'
check "the finished report is four blocks past the early marker" 'grep -q "CTLALIVE tag=$TAG" "$T/devmem.2557444"'
check "and it says the window was armed"                    'grep -q "^mbox_write=ARMED$" "$T/devmem.2557444"'
check "the readback proves /dev/mem took the write"         'grep -q "^mbox_readback=CTLALIVE tag=$TAG mbox=early" "$T/devmem.2557444"'
check "root is still hashed in the armed round"             'grep -q "first512k_md5=$(cat $T/ref.line)" "$T/devmem.2557444"'
# 0x90000000/1024 = 2359296: the first of the four addresses the payload writes because a marker
# test measured them still holding content after a late read. If the extras ever stop being
# written, the whole reason for having them (the window being recycled) goes unnoticed.
check "the extras get the early marker too"  'grep -q "CTLALIVE tag=$TAG mbox=early" "$T/devmem.2359296"'
check "the extras get the finished report too" 'grep -q "CTLALIVE tag=$TAG" "$T/devmem.2359300"'
# 0x10e10208/4 = 70795394: the CRM reset-routing word (the payload divides it itself, and
# this PC has no hexdump, so the stub below answers the readback the way busybox would). The payload masks it to get past the
# watchdog's 8 s and hands it back on a timer, so this write - and only this write - may happen
# on the payload kernel.
check "the payload pokes the reset routing"      'test -e "$T/devmem.70795394"'
check "and the report records what it read back" 'grep -q "^crm_now=" "$T/devmem.2557444"'


# --- the reader, on the blocks that armed run just wrote ----------------------------------
# A live round is scored by pulling these blocks out of a 2 MiB RAM dump, so the extraction is
# tested against the region shape it will really see: null filler everywhere, the three mailbox
# blocks at 0x180000/0x180800/0x181000, and a pstore console copy of the finished report with
# [timestamp] prefixes at the prz offset. The last hit has to be the *finished* block, and the
# line that quotes the early marker back ("mbox_readback=CTLALIVE tag=...") must not be what
# the reader anchors on.
TW=$(cygpath -m "$T" 2>/dev/null || echo "$T")
python - "$TW" <<'PY'
import os, sys
T = sys.argv[1]
blob = bytearray(b"\0" * 0x200000)
for blk in (2557440, 2557442, 2557444):
    p = "%s/devmem.%d" % (T, blk)
    if not os.path.exists(p):
        continue
    d = open(p, "rb").read()
    off = 0x180000 + (blk - 2557440) * 1024
    blob[off:off + len(d)] = d
rep = open("%s/devmem.2557444" % T, "rb").read()
con = b"".join(b"[    3.%06d] " % (i * 100) + l + b"\n" for i, l in enumerate(rep.split(b"\n")))
blob[0x44000:0x44000 + len(con)] = con
open("%s/fake-region.bin" % T, "wb").write(bytes(blob))
print("built a 2 MiB region: 3 mailbox blocks + a console copy at the prz offset")
PY
LOGF="$TW/log.mbox" python tools/pstore2report.py "$TW/fake-region.bin" "$TAG" --append >"$T/mbox.out" 2>&1
check "the reader anchors on a report, not on the readback quote" \
	'grep -q "^report recovered" "$T/mbox.out" && grep -q "^jffs2_ro_mount=OK" "$T/mbox.out"'
check "and it keeps the mailbox fields"    'grep -q "^mbox_write=ARMED" "$T/mbox.out"'
check "the recovered record reaches the log" 'grep -q "root_mtdblock5_first512k_md5=" "$T/log.mbox"'
# The half-round case, which is the reason there are three blocks: only the pre-mount one was
# written before the watchdog fired. The reader must show what it did get instead of a blank.
python - "$TW" <<'PY'
import os, sys
T = sys.argv[1]
blob = bytearray(b"\0" * 0x200000)
d = open("%s/devmem.2557442" % T, "rb").read()
blob[0x180800:0x180800 + len(d)] = d
open("%s/fake-partial.bin" % T, "wb").write(bytes(blob))
PY
LOGF="$TW/log.part" python tools/pstore2report.py "$TW/fake-partial.bin" "$TAG" >"$T/part.out" 2>&1
check "a pre-mount block is recovered, and named as a partial round" \
	'grep -q "root_mtdblock5_first512k_md5=" "$T/part.out" && grep -q "not in the recovered block" "$T/part.out"'

# The other half of the guard, and the one that protects a *running* system: on a 4.19 control
# round this same /init has a window that is reserved in nothing and RAM that is full of live
# pages, so with the reservation line present but the kernel not 6.x, not one write may happen.
echo '#!/bin/sh' >"$T/bin/uname"; echo 'echo "4.19.136+"' >>"$T/bin/uname"
chmod +x "$T/bin/uname"
rm -f "$T/devmem."* "$T/pushed.5555" "$T/pushed.419"
# One pass at a time: the payload's /init ends in a beacon loop that keeps pushing, so a second
# run started while the first is alive reads the *first* one's report and both verdicts land on
# whichever stub happens to be in place. stop_run (see its definition above) is what actually
# ends the previous one.
stop_run
rm -f "$T/devmem."* "$T/pushed.5555" "$T/pushed.419"
timeout 75 sh "$T/init.run" >/dev/null 2>&1 &
RUNPID=$!
n=0
while [ $n -lt 60 ] && [ ! -s "$T/pushed.5555" ]; do n=$(( n + 1 )); sleep 1; done
[ -s "$T/pushed.5555" ] && mv "$T/pushed.5555" "$T/pushed.419"
sleep 3
[ -s "$T/pushed.419" ] || echo "  (the vendor round pushed no report at all - the checks below will say so)"
check "a vendor kernel writes nothing to /dev/mem" 'test ! -e "$T/devmem.2557440" && test ! -e "$T/devmem.2359296" && test ! -e "$T/devmem.70795394"'
check "and says why" 'grep -q "^mbox_write=off-kernel-is-not-the-payload$" "$T/pushed.419"'
echo '#!/bin/sh' >"$T/bin/uname"; echo 'echo "6.18.38+"' >>"$T/bin/uname"
chmod +x "$T/bin/uname"

echo
[ "$fails" = 0 ] && echo "SIM OK - payload report to flash permit, all on the real /init bytes" || echo "SIM FAILURES: $fails"
exit "$fails"
