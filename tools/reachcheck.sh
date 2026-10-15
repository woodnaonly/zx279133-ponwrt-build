#!/bin/sh
# Make sure the system that exists *after* the flash can actually be reached - read-only
# until APPLY=1.
#
# This is part of "刷入升级内核的 openwrt", not a nicety: mainline has no driver for the
# SoC-internal 9132 switch, so three of the four LAN jacks go dark the moment it owns the
# board, and the only live interface is eth0 (RTL8226). If the installed userspace reaches
# its shell through the lan bridge rather than through eth0, the flash succeeds and nobody
# can prove it - with no serial, "flashed but unreachable" is indistinguishable from
# "flashed and dead", which is the exact ambiguity this project has been paying to remove.
#
# Two things decide it, and both are measured here rather than assumed:
#   1. whether ssh answers on EITHER address the board can have - 192.168.9.1 on the lan bridge
#      or 192.168.9.2 on eth0 directly. Which one exists depends on the installed root, and
#      since the overlay pins lan to 192.168.9.1 over a br-lan whose only port is eth0, the
#      flashed system answers on .9.1 and .9.2 is not configured at all;
#   2. whether dropbear is bound to a single interface (Interface 'lan' is the OpenWrt default
#      and survives a kernel swap), which would leave ssh off every address the board has.
#
# Anything it would change is copied to backups/live-config-<date>/ first. The change is a
# userspace config file, not a partition: the jffs2 stays the rollback material.
#
#	sh tools/reachcheck.sh              # survey + verdict
#	APPLY=1 sh tools/reachcheck.sh      # also unbind dropbear from one interface
set -eu
cd "$(dirname "$0")/.."
KEY=${KEY:-$HOME/.ssh/id_onu}
NETDEV=${NETDEV:-eth0}
APPLY=${APPLY:-0}
OPTS="-o BatchMode=yes -o IdentitiesOnly=yes -i $KEY -o UserKnownHostsFile=/dev/null -o StrictHostKeyChecking=no"
# Two addresses, because which one carries a shell depends on how the *installed* root is
# configured, and the flashed root changed the answer:
#
#   192.168.9.1  the lan bridge. With the current overlay (02_network pins lan to this address
#                 over a br-lan whose only port is eth0) this is where the flashed system lands,
#                 and eth0's jack reaches it through the bridge.
#   192.168.9.2  eth0's own static address. The image on the unit right now gives eth0 one
#                 directly, and that is what made ssh reachable without unbinding anything.
#
# Before the overlay change only .9.2 existed for the mainline-owned board, and this script was
# written around that. Probing one fixed address now would report "unreachable" for a board that
# is up and answering - and tools/runround.sh treats a non-zero here as "refusing to write".
LANSSH="ssh -q $OPTS -o ConnectTimeout=6 root@192.168.9.1"
ETHSSH="ssh -q $OPTS -o ConnectTimeout=6 root@192.168.9.2"
OUT=backups/live-config-$(date +%Y%m%d-%H%M)
mkdir -p "$OUT"
REP="$OUT/ssh-eth0.txt"

echo "=== 1. does either address serve ssh? ==="
# Reachability first, then the narrower question. They are not the same thing: with lan bridged
# over eth0, .9.1 answers while .9.2 does not exist at all, and the flashed system is fine.
LAN_SSH=0
$LANSSH 'echo YES' >"$OUT/ssh-lan.txt" 2>&1 && LAN_SSH=1
ETH0_SSH=0
$ETHSSH 'echo YES; uname -r' >"$REP" 2>&1 && ETH0_SSH=1
[ "$LAN_SSH" = 1 ] && echo "  ssh on 192.168.9.1 (lan bridge) works" || \
	echo "  ssh on 192.168.9.1 (lan bridge) does not work: $(head -1 "$OUT/ssh-lan.txt" 2>/dev/null)"
if [ "$ETH0_SSH" = 1 ]; then
	echo "  ssh on 192.168.9.2 (eth0 direct) works: $(tr '\n' ' ' <"$REP")"
else
	echo "  ssh on 192.168.9.2 (eth0 direct) does not work: $(head -1 "$REP" 2>/dev/null)"
fi
# Any ssh that answers is a shell the operator can use and that a flash could be verified from.
SHELL_ANY=0
[ "$LAN_SSH" = 1 ] || [ "$ETH0_SSH" = 1 ] && SHELL_ANY=1

echo "=== 2. dump the config that decides it ==="
for c in network dropbear system; do
	$LANSSH "cat /etc/config/$c" >"$OUT/$c" 2>/dev/null || echo "(none)" >"$OUT/$c"
	printf '  /etc/config/%-8s %d B\n' "$c" "$(wc -c <"$OUT/$c")"
done
$LANSSH "uci show network 2>&1; echo; uci show dropbear 2>&1; echo;
	ifconfig -a 2>&1 | grep -E '^[a-z]|inet '; echo; netstat -ltnp 2>&1 | head -15; echo;
	ls /sys/class/net; echo; brctl show 2>&1 | head -8" >"$OUT/state.txt" 2>&1 ||
	echo "(the board did not answer this survey - step 1's answer is what decides)" >>"$OUT/state.txt"
sed 's/^/  /' "$OUT/state.txt"

echo "=== 3. verdict ==="
# eth0_ssh is kept as the field name because tools/runround.sh greps for it and treats a 0 as
# "refusing to write". Its meaning is now "a shell exists that a flash can be verified from",
# not "ssh answers on 192.168.9.2 specifically": with the current overlay the flashed lan bridge
# carries 192.168.9.1 and .9.2 does not exist, so the old single-address test would block every
# write on a board that is perfectly reachable. The two per-address results are printed alongside
# so nothing is hidden by the collapse.
echo "REACH eth0_ssh=$SHELL_ANY lan_ssh=$LAN_SSH eth0_direct=$ETH0_SSH"
if [ "$SHELL_ANY" = 1 ]; then
	if [ "$ETH0_SSH" = 1 ]; then
		echo "  a shell already answers on eth0's own address (192.168.9.2) -> nothing to change."
	else
		echo "  a shell answers on the lan bridge (192.168.9.1) and eth0 is a port under it ->"
		echo "  that is the shape the current overlay produces, and it is reachable from the 2.5G jack."
	fi
else
	echo "  no shell on either address. After a mainline flash the board keeps only eth0, so this"
	echo "  has to be fixed BEFORE rebooting or the round cannot be verified at all."
	grep -q Interface "$OUT/dropbear" &&
		echo "  cause found: /etc/config/dropbear binds one Interface (lan is the OpenWrt default)."
	echo "  APPLY=1 removes that binding and restarts dropbear, keeping the original in $OUT."
fi
# The interface name is not a given: mainline's NPPT netdev is expected to be eth0, and the
# gate report prints its own netdevs: list - if that differs, pass NETDEV=<name>.
grep -qE "'$NETDEV'" "$OUT/network" ||
	echo "  check: /etc/config/network should carry $NETDEV on a static interface (see $OUT/network)"

if [ "$APPLY" = 1 ] && [ "$SHELL_ANY" != 1 ]; then
	echo "=== applying: dropbear on all interfaces ==="
	$LANSSH "cp -a /etc/config/dropbear /tmp/dropbear.bak.orig;
	uci delete dropbear.@dropbear[0].Interface 2>&1; uci commit dropbear;
	/etc/init.d/dropbear restart 2>&1; sleep 2; uci show dropbear 2>&1 | head -6"
	if $LANSSH 'echo YES; uname -r' || $ETHSSH 'echo YES; uname -r'; then
		echo "  NOW REACHABLE - the post-flash system is usable from the 2.5G jack."
	else
		echo "  still no shell; the address may have to sit on that interface itself:"
		echo "    ssh root@192.168.9.1 \"uci set network.wan.ifname=$NETDEV; uci commit network\""
	fi
fi
echo "  originals of anything changed here are in $OUT"
