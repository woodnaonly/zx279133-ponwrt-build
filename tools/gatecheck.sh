#!/bin/sh
# The chokepoint in front of an irreversible flash.
#
#	sh tools/gatecheck.sh <flash-uImage> <flash-dtb.bin>
#
# 0 = this exact byte pair has been proven to boot on this board, 1 = it has not.
#
# Why a write needs proof rather than confidence: `kernel` + `dtb` are programmed from the
# running system, and if what lands there does not boot there is left no way to run anything
# against the board again - no serial, no U-Boot console, no A/B failover. Recovery would be
# an SPI programmer and desoldering. So only a pair that an earlier round already booted may
# be written:
#
#   proven=gate-<tag>            a kexec of these bytes reached userspace, registered root as
#                                /dev/mtdblock5, read the installed jffs2 to the same bytes the
#                                vendor kernel reads, and mounted that jffs2 read-only. Appended
#                                by tools/gatewatch.sh.
#   proven=live-partition-match  these bytes are what the board has in `kernel`/`dtb` right now
#                                and is booted from. Appended by tools/mkrollback.sh.
#   proven=preflash-snapshot     these bytes are the dumps taken off this board over the session
#                                that was serving the old system - which is how a rollback stays
#                                permitted after mainline has already replaced the live flash.
#
# There is deliberately no env override. Writing a pair that is not in the list means editing
# staging/ctl/flash.permit by hand while the board is on the table, so the decision shows up
# as something you typed instead of happening because a variable defaulted wrong.
#
# A gate record also says which kernel the round booted (kver=), and that has to be the
# kernel that is about to be written. The expected version is therefore read out of the
# uImage itself, not written down here: a record from one line must not vouch for the
# other line's kernel, and naming a series instead of a version would let it.
set -eu
cd "$(dirname "$0")/.."
U=${1:-staging/trial/flash-uImage}
DTB=${2:-staging/trial/flash-dtb.bin}
PERMIT=${PERMIT:-staging/ctl/flash.permit}
[ -s "$U" ] && [ -s "$DTB" ] || { echo "gatecheck: need both images ($U, $DTB)" >&2; exit 2; }
# GNU md5sum escapes its output when the file name contains a backslash or a newline -
# which is what a Windows path looks like to it - and puts a backslash in front of the
# hash. The hash is the same either way, so the escape is taken off here: without that,
# a permit written in the plain form would never match, and the gate would refuse
# every flash for a reason that has nothing to do with the flash.
UM=$(md5sum "$U" | awk '{print $1}' | sed 's/^\\//')
DM=$(md5sum "$DTB" | awk '{print $1}' | sed 's/^\\//')

LINE=$(grep -F "uimage_md5=$UM" "$PERMIT" 2>/dev/null | grep -F "dtb_md5=$DM" | tail -1 || true)
if [ -z "$LINE" ]; then
	echo "gatecheck: REFUSE - no record proves these bytes boot on this board"
	echo "  uImage $UM  $U"
	echo "  dtb    $DM  $DTB"
	if [ -s "$PERMIT" ]; then
		echo "  records in $PERMIT:"; sed 's/^/    /' "$PERMIT"
	else
		echo "  $PERMIT does not exist - run the gate round and score it with tools/gatewatch.sh"
	fi
	exit 1
fi

PROVEN=$(printf '%s\n' "$LINE" | sed -n 's/.*proven=\([^ ]*\).*/\1/p')
case "$PROVEN" in
live-partition-match|preflash-snapshot)
	;;
operator-waiver)
	# Not evidence - a decision, recorded separately so the permit never reads like a boot
	# proof. Used when the owner chooses to program an unproven pair and accept that a kernel
	# which does not boot leaves no way back on this board (no serial, no A/B, and this U-Boot
	# has no bootcount/altbootcmd/itest, so an unattended rollback cannot be installed either).
	echo "gatecheck: ALLOWED (operator-waiver - these bytes are NOT boot-proven)"
	echo "  $LINE"
	exit 0
	;;
gate-?*)
	# A gate record only means something if it says the *mainline* kernel came up, mounted
	# root where U-Boot's hard-coded bootargs point, and could read the jffs2 the real boot
	# has to mount. Anything less is not evidence about the flash - and "not the vendor
	# kernel" is too weak on its own, as a PC-side rehearsal of this very chain proved by
	# getting permitted with kver=3.6.3.
	KV=$(printf '%s\n' "$LINE" | sed -n 's/.* kver=\([^ ]*\).*/\1/p')
	# The version is not written down here: it is read out of the kernel that is about to be
	# written, so that the proof has to be about *that* kernel and not about some other one.
	# A hard-coded series would let a 6.x record vouch for a 7.x image - and the two lines
	# build different kernels out of different trees, so that proof would be about bytes
	# that were never booted.
	#
	# The banner is not at a fixed offset inside the Image - the linker puts the string
	# wherever .rodata lands - so the text is searched for, the way tools/checkrollback.sh
	# reads it. A version has to start with a digit, or a sentence that merely mentions
	# "Linux version" would pass for one.
	EXP=$(tail -c +65 "$U" 2>/dev/null | gzip -dc 2>/dev/null |
		grep -a -o -m1 'Linux version [0-9][0-9A-Za-z.+_-]*' 2>/dev/null |
		head -1 | sed 's/^Linux version //')
	if [ -z "$EXP" ]; then
		echo "gatecheck: refusing - the version of the kernel in $U cannot be read, so no gate record can be matched to it"
		exit 1
	fi
	case "$KV" in
	4.19*) echo "gatecheck: refusing - the gated payload reported kver=$KV, i.e. the vendor kernel booted, which proves nothing about mainline"; exit 1;;
	"$EXP") : ;;   # the record is about exactly the kernel that is about to be written
	*) echo "gatecheck: refusing - the gated payload reported kver=$KV, but the kernel in $U is $EXP"; exit 1;;
	esac
	for k in root_read=IDENTICAL root_index=5 jffs2_ro=OK; do
		printf '%s\n' "$LINE" | grep -q -- "$k" || { echo "gatecheck: refusing - the $PROVEN record lacks $k"; exit 1; }
	done
	;;
"") echo "gatecheck: refusing - record has no proven= field"; exit 1;;
*) echo "gatecheck: refusing - proven=$PROVEN is not a boot proof"; exit 1;;
esac

echo "gatecheck: ALLOWED ($PROVEN)"
echo "  $LINE"
