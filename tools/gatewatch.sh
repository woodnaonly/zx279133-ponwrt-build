#!/bin/sh
# Score a kexec round from the PC, with no observer and no host key.
#
# The channel is a *push*, not a query: the payload's busybox has no httpd and no telnetd
# (scanned in the exact binary the initrd carries), so `/init` connects out to this PC and
# writes its report into the socket. Start the sink first:
#
#	python tools/ncsrv.py &              # listens on 0.0.0.0:5555, appends reports.log
#	sh tools/gatewatch.sh [seconds]      # default 600
#
# What the report proves, in order of value: that the jumped-to kernel reached userspace
# (CTLALIVE/CTLFLASH at all), *which* kernel did it (kver=), that this round's payload is the
# one running (tag=), how that kernel registered the partitions (the /proc/mtd list, which is
# the empirical form of the "root must be mtdblock5" rule), and whether it can read the
# jffs2 the real boot must mount (root_mtdblock5_first512k_md5 vs the 4.19 reference).
#
# A ping on 192.168.9.x is NOT the signal and neither is 192.168.1.1: this PC is dual-homed
# on 192.168.1.0/24 and the owner's router answers 192.168.1.1 over WLAN, which is how one
# watch run ended early as a false positive. Only the pushed report body decides.
set -u
cd "$(dirname "$0")/.."
WAIT=${1:-600}
TAG=${CTLTAG:-K5}
LOGF=${LOGF:-staging/ctl/reports.log}
REF=${REF:-staging/ctl/roothead-419.md5}
# The bytes the gate round was built from, so a scored round can grant the flash permit.
# gate-dtb is what the payload actually booted with; flash-uImage is the container gate-Image
# was inflated from (tools/mkgate.sh derives one from the other and asserts both magics).
SRC=${SRC:-staging/trial}
GATED_DTB=${GATED_DTB:-staging/ctl/gate-dtb}
SRCREC=${SRCREC:-staging/ctl/gate.src}
PERMIT=${PERMIT:-staging/ctl/flash.permit}
END=$(( $(date +%s) + WAIT ))
GONE=0
BEFORE=$(wc -c <"$LOGF" 2>/dev/null || echo 0)
echo "$(date +%H:%M:%S) watching $LOGF (currently $BEFORE B) for tag=$TAG for ${WAIT}s"
i=0
while [ "$(date +%s)" -lt "$END" ]; do
	SIZE=$(wc -c <"$LOGF" 2>/dev/null || echo 0)
	if [ "$SIZE" -gt "$BEFORE" ]; then
		NEW=$(tail -c +$(( BEFORE + 1 )) "$LOGF" | tr -d '\000' | grep -av "CNXN\|features=" || true)
		BEFORE=$SIZE
		printf '%s\n' "$NEW" | sed 's/^/   /'
		case "$NEW" in
		*CTLALIVE*|*CTLFLASH*)
			case "$NEW" in
			*"tag=$TAG"*) : ;;
			*) echo "$(date +%H:%M:%S) a payload reported but its tag is not $TAG - keeping watch, read the report above"
			   continue;;
			esac
			echo "$(date +%H:%M:%S) VERDICT: this round's payload is alive -> the handoff worked for it"
			PM=$(printf '%s\n' "$NEW" | sed -n 's/.*first512k_md5=\([0-9a-f]*\).*/\1/p' | head -1)
			KVER=$(printf '%s\n' "$NEW" | sed -n 's/^kver=\(.*\)/\1/p' | head -1)
			ROOTIDX=$(printf '%s\n' "$NEW" | sed -n 's/^\(mtd[0-9]*\):.*"root"/\1/p' | head -1 | tr -d 'mtd')
			WANT=$(sed -n '2p' "$REF" 2>/dev/null)
			if [ -z "$PM" ]; then
				RR=NOHASH; echo "   root read: no mtdblock5 hash in the report - mainline has no mtdblock for root?"
			elif [ -z "$WANT" ]; then
				RR=NOREF; echo "   root read: payload md5=$PM, but no 4.19 reference ($REF) - run tools/roothead.sh"
			elif [ "$PM" = "$WANT" ]; then
				RR=IDENTICAL; echo "   root read: IDENTICAL to the vendor 4.19 -> the two kernels' ECC/OOB layouts agree on root"
			else
				RR=DIFFERENT
				echo "   root read: DIFFERENT mainline=$PM vendor=$WANT -> DO NOT FLASH, the ECC/OOB"
				echo "   layouts disagree: this kernel boots from an initrd but cannot read this root."
			fi
			echo "   kver=$KVER root=/dev/mtdblock${ROOTIDX:-?}"
			# The claim the hash cannot make: reading the same bytes is not building a
			# filesystem out of them. The payload mounts root read-only and says so.
			JRO=$(printf '%s\n' "$NEW" | sed -n 's/^jffs2_ro_mount=\(.*\)/\1/p' | head -1)
			JUS=$(printf '%s\n' "$NEW" | sed -n 's/^jffs2_userspace=\(.*\)/\1/p' | head -1)
			JERR=$(printf '%s\n' "$NEW" | sed -n 's/^jffs2_err=\(.*\)/\1/p' | head -1)
			case "$JRO" in
			OK) echo "   root mount: mainline mounted the installed jffs2 read-only (userspace $JUS)";;
			"") echo "   root mount: the report carries no jffs2_ro_mount - the announce initrd predates that probe";;
			*) echo "   root mount: FAILED (${JRO:-?}) $JERR - mainline reads the bytes but cannot mount the root it would boot from";;
			esac
			# What the installed root says about the boot *after* this one, read during the same
			# round because no later round gets to ask before the board is already reflashed.
			DB=$(printf '%s\n' "$NEW" | sed -n 's/^installed_dropbear_Interface=\(.*\)/\1/p' | head -1)
			KM=$(printf '%s\n' "$NEW" | sed -n 's/^installed_kmods=\(.*\)/\1/p' | head -1)
			RL=$(printf '%s\n' "$NEW" | sed -n 's/^installed_release=\(.*\)/\1/p' | head -1)
			echo "   installed root: ${RL:-?} kmods=${KM:-?} dropbear_Interface_lines=${DB:-?}"
			case "$DB" in
			0) echo "   dropbear binds nothing -> a shell survives on any netdev, including eth0";;
			nomount|none|"") : ;;
			*) echo "   dropbear names an Interface: mainline has no ports on the lan bridge, so unless that is"
			   echo "   eth0 the flashed board has NO shell. Fix it deliberately: APPLY=1 sh tools/reachcheck.sh";;
			esac
			# A scored gate round is also the thing that *permits* the flash, because the write
			# cannot be undone here. Only an announce report can do that: a CTLFLASH report says
			# a flasher came up, not that the kernel being flashed boots.
			case "$NEW" in
			*CTLALIVE*)
				UM=$(md5sum "$SRC/flash-uImage" 2>/dev/null | awk '{print $1}')
				DM=$(md5sum "$GATED_DTB" 2>/dev/null | awk '{print $1}')
				RU=$(sed -n 's/.*uimage_md5=\([0-9a-f]*\).*/\1/p' "$SRCREC" 2>/dev/null | head -1)
				RI=$(sed -n 's/.*image_md5=\([0-9a-f]*\).*/\1/p' "$SRCREC" 2>/dev/null | head -1)
				if [ -z "$UM" ] || [ -z "$DM" ]; then
					echo "   permit NOT granted: no hash of the gated pair ($SRC/flash-uImage, $GATED_DTB)"
				elif [ -z "$RU" ]; then
					echo "   permit NOT granted: $SRCREC carries no provenance record, so nothing ties the"
					echo "   bytes about to be licensed to the payload that just ran. Build it: sh tools/mkgate.sh"
				elif [ "$RU" != "$UM" ]; then
					echo "   permit NOT granted: the gate payload was inflated from uimage_md5=$RU, and this"
					echo "   round would license $UM - $SRC/flash-uImage changed after the payload was built."
					echo "   Rebuild and rerun: sh tools/mkgate.sh && sh tools/runround.sh"
				else
					[ -s "$SRC/flash-dtb.bin" ] && [ "$(md5sum "$SRC/flash-dtb.bin" | awk '{print $1}')" = "$DM" ] ||
						echo "   note: $SRC/flash-dtb.bin is NOT the dtb that booted ($GATED_DTB) - the permit below is for the one that did"
					mkdir -p "$(dirname "$PERMIT")"
					printf 'ts=%s uimage_md5=%s image_md5=%s dtb_md5=%s kver=%s root_index=%s root_read=%s jffs2_ro=%s proven=gate-%s\n' \
						"$(date +%FT%T)" "$UM" "${RI:-?}" "$DM" "${KVER:-?}" "${ROOTIDX:-?}" "$RR" "${JRO:-?}" "$TAG" >>"$PERMIT"
					echo "   recorded in $PERMIT - tools/gatecheck.sh is what reads it"
				fi;;
			*) echo "   a flasher reported, not an announce payload - no permit";;
			esac
			exit 0;;
		*) echo "$(date +%H:%M:%S) report without our marker - not this payload";;
		esac
	fi
	i=$(( i + 1 ))
	if [ $(( i % 10 )) = 0 ] && [ "$GONE" = 0 ]; then
		if ! timeout 8 ssh -q -o BatchMode=yes -o IdentitiesOnly=yes -i "$HOME/.ssh/id_onu" \
			-o UserKnownHostsFile=/dev/null -o StrictHostKeyChecking=no -o ConnectTimeout=3 \
			root@192.168.9.1 true 2>/dev/null; then
			GONE=1; echo "$(date +%H:%M:%S) the flashed system stopped answering - control has moved"
		fi
	fi
	sleep 3
done
echo "$(date +%H:%M:%S) timed out after ${WAIT}s, no report (flashed-system-gone=$GONE)."
echo "gone=0: the jump never happened, or nothing is listening on 5555 (start tools/ncsrv.py)."
echo "gone=1: the payload booted but could not push - check the panel: /init lights every led"
echo "it can write, so a lit rhythm means userspace was reached and only the netdev path failed."
exit 1
