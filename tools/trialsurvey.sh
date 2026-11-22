#!/bin/sh
# SPDX-License-Identifier: GPL-2.0-only
# Read-only survey of the SK-D840N running the 7.3 kernel.
#
# This is the board trial's instrument.  It answers the five questions the
# trial has to answer, and it answers them by reading the interfaces the
# ported drivers expose - no guessing, no writing, no configuration.
#
#   1. the LAN conduit netdev's RX counters move
#      (drivers/net/ethernet/zte/zx279133-lan-conduit.c names it
#      "lan-cpu%d"; the counter is what proves the BP pool and the
#      receive path work, and the frame offset of patch 110/120 is only
#      meaningful if frames land here at all)
#
#   2. the DSA user ports report a link
#      (the board's switch node declares port@4..port@8 with the labels
#      lan0, lan2, lan3, lan4 and lan1; a port that never appears means
#      the 9132 driver did not register)
#
#   3. the LOS lamp follows the optical RX signal detect
#      (main.c drives the lamp from the opsd GPIO: the lamp is off when
#      the GPIO says there is light.  The script prints both, so the
#      correlation is visible in one place)
#
#   4. pon_state walks from O1 towards O5
#      (a sysfs attribute on the WAN netdev's device:
#      drivers/net/ethernet/zte/zx279133-pon.c creates it on ndev->dev)
#
#   5. omci_transport's submitted and completed counters move
#      (the same file, the second attribute of the same device)
#
# Two samples are taken, with the interval between them, so that a
# counter which does not move says so instead of being read once and
# looked at.
#
# Usage: sh tools/trialsurvey.sh [seconds between the two samples]
set -u

INTERVAL=${1:-10}
# The paths are variables so that the script can be rehearsed against a
# synthetic tree before it is put on the board.
CLASS=${SK_CLASS_NET:-/sys/class/net}
DEBUG=${SK_DEBUGFS:-/sys/kernel/debug}
# The LED class sits next to the netdev class, not below it.
LEDS=${SK_LEDS:-$(dirname "$CLASS")/leds}

echo "=== 1. the netdevs ==="
printf '%-12s %-10s %-9s %-8s %-14s %-14s\n' \
	name operstate carrier mtu rx_packets rx_bytes
for d in "$CLASS"/*; do
	[ -d "$d" ] || continue
	n=$(basename "$d")
	op=$(cat "$d/operstate" 2>/dev/null || echo "?")
	ca=$(cat "$d/carrier" 2>/dev/null || echo "?")
	mt=$(cat "$d/mtu" 2>/dev/null || echo "?")
	rp=$(cat "$d/statistics/rx_packets" 2>/dev/null || echo "?")
	rb=$(cat "$d/statistics/rx_bytes" 2>/dev/null || echo "?")
	printf '%-12s %-10s %-9s %-8s %-14s %-14s\n' "$n" "$op" "$ca" "$mt" "$rp" "$rb"
done

echo
echo "=== 2. the DSA ports the switch node declares ==="
for n in lan0 lan1 lan2 lan3 lan4; do
	if [ -e "$CLASS/$n" ]; then
		printf '  %-6s present  operstate=%-10s carrier=%-3s speed=%s\n' \
			"$n" \
			"$(cat "$CLASS/$n/operstate" 2>/dev/null || echo '?')" \
			"$(cat "$CLASS/$n/carrier" 2>/dev/null || echo '?')" \
			"$(cat "$CLASS/$n/speed" 2>/dev/null || echo '?')"
	else
		printf '  %-6s MISSING - the 9132 driver did not register this port\n' "$n"
	fi
done

echo
echo "=== 3. the LOS lamp and the optical RX signal detect ==="
for d in "$LEDS"/*; do
	[ -d "$d" ] || continue
	l=$(basename "$d")
	printf '  led %-24s brightness=%-4s trigger=%s\n' "$l" \
		"$(cat "$d/brightness" 2>/dev/null || echo '?')" \
		"$(cat "$d/trigger" 2>/dev/null || echo 'none')"
done
if [ -r "$DEBUG/gpio" ]; then
	echo "  -- the gpio lines the board has:"
	sed -n 's/^\([0-9]*\).*opsd.*/    gpio \1: opsd/p' "$DEBUG/gpio" 2>/dev/null
	echo "  -- the raw gpio values:"
	grep -o 'opsd[^|]*' "$DEBUG/gpio" 2>/dev/null | head -4
else
	echo "  (no debugfs at $DEBUG/gpio - mount it with: mount -t debugfs none /sys/kernel/debug)"
fi

echo
echo "=== 4. the PON state ==="
found=0
for f in "$CLASS"/*/device/pon_state; do
	[ -r "$f" ] || continue
	found=1
	echo "  $(dirname "$f" | sed 's#.*/##'): $(cat "$f")"
done
[ "$found" = 1 ] || echo "  (no pon_state attribute - the PON stack did not attach to a netdev)"

echo
echo "=== 5. the OMCI transport counters ==="
found=0
for f in "$CLASS"/*/device/omci_transport; do
	[ -r "$f" ] || continue
	found=1
	echo "  $(dirname "$f" | sed 's#.*/##'): $(cat "$f")"
done
[ "$found" = 1 ] || echo "  (no omci_transport attribute - the PON stack did not attach to a netdev)"

echo
echo "=== 6. the same counters ${INTERVAL}s later ==="
sleep "$INTERVAL"
for d in "$CLASS"/*; do
	[ -d "$d" ] || continue
	n=$(basename "$d")
	rp=$(cat "$d/statistics/rx_packets" 2>/dev/null || echo "?")
	rb=$(cat "$d/statistics/rx_bytes" 2>/dev/null || echo "?")
	printf '  %-12s rx_packets=%-14s rx_bytes=%-14s operstate=%s\n' "$n" "$rp" "$rb" \
		"$(cat "$d/operstate" 2>/dev/null || echo '?')"
done
for f in "$CLASS"/*/device/pon_state "$CLASS"/*/device/omci_transport; do
	[ -r "$f" ] || continue
	echo "  $(basename "$f"): $(cat "$f")"
done

echo
echo "=== 7. the kernel's own idea of the drivers ==="
if [ -r /proc/modules ]; then
	grep -E '^(npu|fnoffload|zx279133)' /proc/modules || \
		echo "  (no zte modules loaded - on this line the drivers are built into the kernel)"
fi
if [ -r /proc/config.gz ]; then
	echo "  the built-in drivers:"
	zcat /proc/config.gz 2>/dev/null | grep -E '^CONFIG_ZTE_ZX279133_(ETH|LAN_CONDUIT|SWITCH|FNOFFLOAD)=' || \
		echo "  (not found in /proc/config.gz)"
fi
echo "  dmesg:"
dmesg 2>/dev/null | grep -iE 'zx279133|9132|lan conduit|nppu' | tail -25 || \
	echo "  (no dmesg access)"
