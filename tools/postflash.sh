#!/bin/sh
# What "it worked" has to mean after the flash, measured over ssh.
#
# This exists because the earlier checker for a RAM system (tools/ramcheck.py) logs in with
# paramiko, and paramiko's auth does not complete against this dropbear build - so the one
# command that would have confirmed a booting system was the one that could not run. Plain
# ssh with the key works on both the vendor 4.19 and the mainline system, so everything here
# is a single ssh session whose output is also written to staging/postflash-<date>.txt.
#
# The checks are chosen to answer the questions the no-serial method actually leaves open:
#   uname -r            - which kernel owns the board now (6.18.38+ means mainline booted)
#   root= in cmdline    - U-Boot passed mtdblock5, i.e. the dtb's numbering is right in practice
#   mount of /          - jffs2 mounted *off flash*, which is the ECC/OOB question the gate
#                         round only answers for an initrd boot
#   /proc/mtd           - the registration order, for the record
#   dmesg               - spi-nand / nppt / rtl8226 attached, and what failed
#   leds                - gpio-leds bound, i.e. our dts is the one in use
#   readback of mtd2    - the kernel partition still holds the image we wrote (md5 compare is
#                         done here on the PC, so a reboot mid-round cannot be mistaken for
#                         "flashed")
#
#	HOST=192.168.9.2 sh tools/postflash.sh
#	sh tools/postflash.sh          # now also probes the addresses this board can answer on
#
# It also carries a PON verdict block, because that is the one thing this script exists to
# settle and the rest of it cannot answer it. See "PON verdict" below.
set -eu
cd "$(dirname "$0")/.."
KEY=${KEY:-$HOME/.ssh/id_onu}
SSHOPT="-q -o BatchMode=yes -o IdentitiesOnly=yes -i $KEY -o UserKnownHostsFile=/dev/null -o StrictHostKeyChecking=no -o ConnectTimeout=6"
OUT=staging/postflash-$(date +%Y%m%d-%H%M).txt

# With no HOST given, try each address this board can be on, in the order that keeps the
# fastest answer first, and use the one where ssh actually returns a uname line.
#
# The order matters and 192.168.1.1 has to be in it. Our own flashed root answers on .1.1,
# not on .9.x: immortalwrt's dropbear package ships `option Interface 'lan'` by default,
# ci-build-repo's 02_network puts eth0 alone in lan, and config_generate then applies lan the
# stock default (base-files/files/bin/config_generate: "lan) ipad=${ipaddr:-192.168.1.1}").
# Nothing in the build overrides it. A board that is up and serving ssh on .1.1 would
# otherwise be reported as a failed flash.
#
# .1.1 is tried LAST and is only accepted on a real ssh answer, never on a ping: this PC sits
# on 192.168.1.0/24 twice (wired and WLAN) and the owner's own router answers .1.1 there.
if [ -n "${HOST:-}" ]; then
	CANDIDATES=$HOST
else
	CANDIDATES=${HOSTS:-"192.168.9.1 192.168.9.2 192.168.1.1"}
fi
HOST=""
for h in $CANDIDATES; do
	if ssh $SSHOPT "root@$h" 'echo BOARD_ALIVE $(uname -r)' 2>/dev/null | grep -q BOARD_ALIVE; then
		echo "board answered on $h: $(ssh $SSHOPT "root@$h" 'uname -r' 2>/dev/null)"
		HOST=$h
		break
	fi
done
if [ -z "$HOST" ]; then
	echo "no candidate answered over ssh: $CANDIDATES" | tee "$OUT"
	echo "  a ping is not evidence - the owner's own router answers 192.168.1.1 on this LAN." | tee -a "$OUT"
	exit 1
fi
SSH="ssh $SSHOPT root@$HOST"

echo "=== $HOST ===" | tee "$OUT"
if ! $SSH "echo uname: \$(uname -a);
	echo cmdline: \$(cat /proc/cmdline);
	echo uptime: \$(cut -d. -f1 /proc/uptime);
	echo root-mount:; mount | grep ' / ' || echo '  NO ROOT MOUNT LINE';
	echo; echo proc-mtd:; cat /proc/mtd;
	echo; echo sysfs-mtd:; for j in /sys/class/mtd/mtd*; do echo \"\$(basename \$j) \$(cat \$j/name) off=\$(cat \$j/offset) size=\$(cat \$j/size)\"; done;
	echo; echo netdevs:; ls /sys/class/net; ifconfig -a 2>&1 | grep -E '^[a-z]|inet ';
	echo; echo leds:; ls /sys/class/leds;
	echo; echo dmesg-interesting:; dmesg 2>/dev/null | grep -iE 'spi-nand|spinand|nand|jffs2|mtd|nppt|xmac|rtl822|phy|led|watchdog|error|fail|warn' | tail -40;
	echo; echo kmods:; ls /lib/modules 2>/dev/null | head -3; lsmod 2>/dev/null | head -5;
	echo; echo release:; cat /etc/openwrt_release 2>/dev/null | head -4" 2>&1 | tee "$OUT"; then
	echo "  no ssh on $HOST - see the discriminators below before concluding anything."
fi

# ---------------------------------------------------------------------------------------
# PON verdict.
#
# The board is a GPON ONU, and the mainline kernel has no PON MAC driver in it (verified
# offline against the actual Image: the vendor 4.19 has ~43 WOE/NPPT datapath symbols,
# 7 optical/BOSA symbols and 2 OMCI symbols, mainline has 2 NPPT strings and both are
# LAN-side). So the question "is the optical side alive" is not a detail - it decides
# whether this board is an ONU or an ssh-reachable brick with a dead modem - and nothing
# above the PON marker answers it.
#
# Three states are kept apart on purpose:
#   alive   - a PON driver bound and a pon-side interface exists
#   absent  - the kernel has no PON driver: positively checked, not inferred from silence
#   unknown - the evidence could not be read (no sysfs, no dmesg, no /proc/net/dev)
# "unknown" is never collapsed into "absent". That conflation is what made an earlier round
# of this project report three blank fields as a finding when they were one failed mount.
# ---------------------------------------------------------------------------------------
echo "=== PON verdict ===" | tee -a "$OUT"
PON=$($SSH '
	echo "SYSFS_NET=$( [ -d /sys/class/net ] && echo present || echo absent )"
	echo "DMESG_LINES=$( dmesg 2>/dev/null | wc -l )"
	echo "--- netdevs (all, with state) ---"
	if [ -d /sys/class/net ]; then
		for n in /sys/class/net/*; do
			printf "  %-10s oper=%-4s carrier=%-4s mac=%s\n" \
				"$(basename $n)" \
				"$(cat $n/operstate 2>/dev/null)" \
				"$(cat $n/carrier 2>/dev/null)" \
				"$(cat $n/address 2>/dev/null)"
		done
	else
		echo "  (sysfs class/net absent)"
	fi
	echo "--- /proc/net/dev (independent of sysfs) ---"
	cat /proc/net/dev 2>/dev/null || echo "  (absent)"
	echo "--- platform drivers that claimed a zx279133 / pon / nppt device ---"
	for d in /sys/bus/platform/devices/*; do
		[ -e "$d/of_node" ] || continue
		p=$(readlink -f "$d/of_node" 2>/dev/null)
		case "$p" in
		*zx279133*|*pon*|*nppt*|*serdes*|*mdio*) printf "  %s -> %s\n" "$(basename $d)" "$p";;
		esac
	done
	echo "--- dmesg, PON family only ---"
	dmesg 2>/dev/null | grep -iE "pon|gpon|xgpon|omci|bosa|optical|woe|nppt|idm|xmac|laser|rxd|tx_disable" | tail -30
' 2>&1) || PON=""
printf '%s\n' "$PON" | tee -a "$OUT"

# Verdict, derived from the explicit SYSFS_NET / DMESG_LINES markers plus the evidence
# lines - never from the absence of output. An earlier round of this project read three
# blank fields as a finding when they were one failed mount; a verdict that can say
# "absent" without having proved it is that same mistake wearing a nicer coat.
PONV=unknown
PONEVID=""
SY=$(printf '%s' "$PON" | sed -n 's/^SYSFS_NET=//p' | head -1)
DL=$(printf '%s' "$PON" | sed -n 's/^DMESG_LINES=//p' | head -1)
# A count we cannot compare as a number must not reach an integer test: dash errors on
# `x -eq 0` and, under `set -e` inside a condition, that error would be indistinguishable
# from a verdict. Treat it as "count unknown" instead.
DLN=""
case "$DL" in
''|*[!0-9]*) ;;
*) DLN=$DL ;;
esac
if [ -z "$PON" ]; then
	PONV=unknown; PONEVID="ssh returned nothing at all"
elif [ -z "$SY" ] || [ -z "$DL" ]; then
	# Output came back but not in the shape this block emits. That means the capture
	# did not come from this script - and nothing was observed either way, so it is
	# NOT "absent". Falling through to the absence branch here is the exact conflation
	# this block exists to prevent.
	PONV=unknown; PONEVID="the capture has no SYSFS_NET/DMESG_LINES markers, so it did not come from this block - nothing was observed"
elif [ "$SY" = absent ] && [ -n "$DLN" ] && [ "$DLN" -eq 0 ]; then
	PONV=unknown; PONEVID="sysfs class/net is absent and dmesg read 0 lines - nothing was observed"
elif [ "$SY" != present ] || [ -z "$DLN" ]; then
	PONV=unknown; PONEVID="sysfs=$SY dmesg_lines=${DL:-unreadable} - not enough was readable to judge"
elif printf '%s' "$PON" | grep -qE '^[[:space:]]*(pon[0-9]*|gpon[0-9]*|xgpon[0-9]*)[[:space:]]' \
	 && printf '%s' "$PON" | grep -qE '^[[:space:]]*(pon[0-9]*|gpon[0-9]*|xgpon[0-9]*):'; then
	PONV=alive; PONEVID="a PON-side interface appears in both /sys/class/net and /proc/net/dev"
elif printf '%s' "$PON" | grep -qE '^[[:space:]]*[0-9a-f.]+\.(pon|nppt|gpon)[0-9]*[[:space:]]*->[[:space:]]*/soc/pon'; then
	PONV=alive; PONEVID="a PON platform device claimed /soc/pon@... (no netdev name seen, so this is weaker)"
elif printf '%s' "$PON" | grep -qiE 'woe_|OpticalI2c|bosa|omci|gpon state|pon state'; then
	PONV=absent; PONEVID="the PON driver printed messages but no PON interface exists - the link is not up"
else
	PONV=absent; PONEVID="no PON interface, no PON platform device, no PON driver messages"
fi
{
	echo
	echo "PON: $PONV"
	echo "  because: $PONEVID"
	case "$PONV" in
	absent)
		echo "  mainline has no PON MAC driver, so this is expected on 6.18.38+ and is not a"
		echo "  flashing mistake. To get the optical side back, write the vendor pair back:"
		echo "    SRC=staging/rollback EXPECT='root=0xf00000,0xf100000' sh tools/flash-direct.sh write"
		echo "  (that value matches the VENDOR dtb; the mainline one is 0xf000000, 1 MiB shorter)"
		;;
	alive)
		echo "  the optical side is up - PON was NOT lost, and the earlier offline reading of the"
		echo "  mainline Image as PON-less needs revisiting against this output."
		;;
	unknown)
		echo "  the evidence could not be read. Do NOT read this as 'no PON': the same blank"
		echo "  reading previously came from a failed sysfs mount, not from a missing driver."
		;;
	esac
} | tee -a "$OUT"

# Read back the kernel partition so "we wrote it" is not inferred from "it boots".
# Devices are resolved by label from sysfs, never by number. The mtd1..mtd6 aliases in the
# board dts put kernel at mtd2 and dtb at mtd3 under mainline - the same numbers the 4.19 used
# - but resolving by label keeps this correct even if that pinning changes again.
echo "=== readback of the two programmed partitions ===" | tee -a "$OUT"
S1=$(stat -c %s staging/trial/flash-uImage); S2=$(stat -c %s staging/trial/flash-dtb.bin)
B1=$(( (S1 + 65535) / 65536 )); B2=$(( (S2 + 65535) / 65536 ))
$SSH "k=\$(for j in /sys/class/mtd/mtd*; do [ \"\$(cat \$j/name)\" = kernel ] && basename \$j; done);
d=\$(for j in /sys/class/mtd/mtd*; do [ \"\$(cat \$j/name)\" = dtb ] && basename \$j; done);
echo \"kernel=\$k want=$S1 B  dtb=\$d want=$S2 B\";
dd if=/dev/\$k bs=64k count=$B1 2>/dev/null | head -c $S1 | md5sum;
dd if=/dev/\$d bs=64k count=$B2 2>/dev/null | head -c $S2 | md5sum" 2>&1 | tee -a "$OUT" || true
cat <<EOF

Expected, md5 of the first N bytes of the partition that carries that label:
  kernel  $(md5sum staging/trial/flash-uImage 2>/dev/null | awk '{print $1}')  (first $(stat -c %s staging/trial/flash-uImage 2>/dev/null) B)
  dtb     $(md5sum staging/trial/flash-dtb.bin 2>/dev/null | awk '{print $1}')  (first $(stat -c %s staging/trial/flash-dtb.bin 2>/dev/null) B)
Labels, not numbers. The pinned table gives kernel=mtd2 and dtb=mtd3 under mainline, as the
4.19 did, but if the pinning ever changes a numbered comparison would silently hash the
wrong partition.
If uname says 6.18.38+ and the root mount is jffs2 on /dev/mtdblock5, the objective's second
half is met. If it says 4.19.136+, the flash did not land or U-Boot read the old pair.
If nothing answers at all: the only port mainline has is eth0 (RTL8226), so the cable may be
in a jack driven by the 9132 switch, which mainline cannot use - try the others.
EOF
