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
set -eu
cd "$(dirname "$0")/.."
U=${1:-staging/trial/flash-uImage}
DTB=${2:-staging/trial/flash-dtb.bin}
PERMIT=${PERMIT:-staging/ctl/flash.permit}
[ -s "$U" ] && [ -s "$DTB" ] || { echo "gatecheck: need both images ($U, $DTB)" >&2; exit 2; }
UM=$(md5sum "$U" | awk '{print $1}')
DM=$(md5sum "$DTB" | awk '{print $1}')

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
gate-*)
	# A gate record only means something if it says the *mainline* kernel came up, mounted
	# root where U-Boot's hard-coded bootargs point, and could read the jffs2 the real boot
	# has to mount. Anything less is not evidence about the flash - and "not the vendor
	# kernel" is too weak on its own, as a PC-side rehearsal of this very chain proved by
	# getting permitted with kver=3.6.3.
	KV=$(printf '%s\n' "$LINE" | sed -n 's/.* kver=\([^ ]*\).*/\1/p')
	case "$KV" in
	6.*) : ;;   # the flash target is the 6.18.38 build this dtb was made for
	4.19*) echo "gatecheck: refusing - the gated payload reported kver=$KV, i.e. the vendor kernel booted, which proves nothing about mainline"; exit 1;;
	*) echo "gatecheck: refusing - the gated payload reported kver=$KV, not the 6.x kernel being flashed"; exit 1;;
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
