#!/bin/sh
# SPDX-License-Identifier: GPL-2.0-only
# Read-only survey of the SK-D840N running the 7.3 kernel.
#
# This is the board trial's instrument.  It answers the six questions the
# trial has to answer, and it answers them by reading the interfaces the
# ported drivers expose - no guessing, no writing, no configuration.
#
#   1. the LAN conduit netdev's RX counters move
#      (drivers/net/ethernet/zte/zx279133-lan-conduit.c names it
#      "lan-cpu%d"; the counter is what proves the BP pool and the
#      receive path work, and the frame offset of patch 110/120 is only
#      meaningful if frames land here at all.  Its rx_dropped counter is
#      read as well: idm.c counts there every frame the receive path
#      threw away because the DSA path was not up, so a counter that
#      moves while rx_packets does not says the frames are being dropped
#      instead of handed over)
#
#   2. the DSA user ports report a link
#      (the board's switch node declares port@4..port@8 with the labels
#      lan0, lan2, lan3, lan4 and lan1; a port that never appears means
#      the 9132 driver did not register.  The 2.5G jack is the fifth of
#      those - DSA port 8, the one the vendor calls the outerphy - so
#      the frames that come in on it are counted on that port's own
#      interface, and this survey reads them there)
#
#   3. the LOS lamp follows the optical RX signal detect
#      (main.c drives the lamp from the opsd GPIO: the lamp is off when
#      the GPIO says there is light.  The script prints both, and prints
#      them twice, because "the lamp is off" is only evidence next to
#      "there was light" - read once, it cannot tell a working lamp from
#      a board with no optical signal at all)
#
#   4. pon_state walks from O1 towards O5
#      (a sysfs attribute on the WAN netdev's device:
#      drivers/net/ethernet/zte/zx279133-pon.c creates it on ndev->dev.
#      It is coupled to question 3: zx279133_pon_note_rxsd() raises the
#      PON stack's LOS flag from the same reading, so with no light the
#      state machine is not supposed to walk past O1 - a pon_state that
#      does not move is only a fault if the lamp says there was light)
#
#   5. omci_transport's submitted and completed counters move
#      (the same file, the second attribute of the same device)
#
#   6. the driver publishes the four TX isolation counters
#      (ethtool -S on the WAN netdev: tx_invalid_completions,
#      tx_recovery_requests, tx_recovery_restarts and tx_recovery_failures.
#      They are the only thing that tells a ring that was quarantined from
#      a ring that never had a problem - the isolation model is invisible
#      without them, because a ring that was pulled out of the pool looks
#      exactly like a healthy one from the outside.  zx279133-main.c
#      installs the driver's ethtool_ops on the WAN netdev, which the
#      board's network configuration names eth0)
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
# The lamp the question is about, and the trigger the driver publishes
# the optical RX signal detect through (zx279133-main.c registers
# "zx279133-los" and drives the lamp with LED_OFF when there is light).
LOS_LED=${SK_LOS_LED:-skyworth:red:los}
LOS_TRIGGER=${SK_LOS_TRIGGER:-zx279133-los}
# The opsd line is gpio2 pin 1 in the board's device tree, and the SoC
# numbers its gpio chips in tens of pins, so the flat number is 65.  It
# can be overridden, because the flat number depends on how many gpio
# chips the kernel was given.
OPSD_GPIO=${SK_OPSD_GPIO:-65}

# The interface that carries the driver's own statistics, and the counters
# the TX isolation model publishes on it.  The names are the ones the
# driver's statistics table holds, so a name that is not there is a
# finding, not a formatting accident.
STATDEV=${SK_STATDEV:-eth0}
TXSTATS="tx_invalid_completions tx_recovery_requests tx_recovery_restarts tx_recovery_failures"

# The driver's own counters, read through ethtool.  Three different
# failures are told apart, because they are three different findings: no
# ethtool in the image at all, an interface that publishes no driver
# statistics, and a statistics table that does not carry the four names.
print_ethtool_stats() {
	if ! command -v ethtool >/dev/null 2>&1; then
		echo "  (no ethtool in this image - the TX isolation counters cannot be read here)"
		return 0
	fi
	out=$(ethtool -S "$STATDEV" 2>/dev/null) || {
		echo "  (ethtool -S $STATDEV failed - this interface publishes no driver statistics)"
		return 0
	}
	echo "  $STATDEV:"
	for n in $TXSTATS; do
		v=$(printf '%s\n' "$out" | awk -v k="$n" \
			'{ gsub(":", "", $1); if ($1 == k) { print $2; exit } }')
		if [ -n "$v" ]; then
			printf '    %-28s %s\n' "$n" "$v"
		else
			printf '    %-28s NOT PUBLISHED\n' "$n"
		fi
	done
}

# The level of one gpio line, read from the kernel's own gpio dump.
# The dump's exact shape has changed between kernel versions, so the
# line is matched loosely and the level is taken from the word that
# follows the "in".  It prints nothing when the line is not there.
#
# The pattern used to be "\[ *${1}\]|\(gpio ${1}\)".  In an extended
# regular expression a backslash before a bracket makes the bracket
# LITERAL, so that pattern asked for the literal text "[ *65]" and for
# "(gpio 65)" - and the kernel's dump writes "gpio-65 (gpio65)", with
# no space and with the number inside the parentheses.  Neither branch
# matched anything, so this function printed nothing on every board:
# the optical RX signal detect was never read, and the two criteria
# that depend on it (the lamp, and the PON state) could not be judged
# at all.  The bracket now carries the spaces inside it and the
# parentheses accept the space the dump does not have.
read_gpio_level() {
	[ -r "$DEBUG/gpio" ] || return 0
	line=$(grep -E "\[ *${1} *\]|\(gpio ?${1}\)" "$DEBUG/gpio" 2>/dev/null | head -n 1)
	[ -n "$line" ] || return 0
	level=$(printf '%s\n' "$line" | awk '{for (i = 1; i < NF; i++) if ($i == "in") { print $(i + 1); exit }}')
	case "$level" in
	high)   echo "high" ;;
	low)    echo "low" ;;
	unknown) echo "unknown" ;;
	*)      printf '%s\n' "$line" ;;
	esac
}

# The LOS lamp and the line behind it, side by side.
print_lamp() {
	for d in "$LEDS"/*; do
		[ -d "$d" ] || continue
		l=$(basename "$d")
		printf '  led %-24s brightness=%-4s trigger=%s\n' "$l" \
			"$(cat "$d/brightness" 2>/dev/null || echo '?')" \
			"$(cat "$d/trigger" 2>/dev/null || echo 'none')"
	done
	level=$(read_gpio_level "$OPSD_GPIO")
	if [ -n "$level" ]; then
		echo "  opsd (gpio $OPSD_GPIO): $level"
	elif [ -r "$DEBUG/gpio" ]; then
		echo "  opsd (gpio $OPSD_GPIO): not in the gpio dump"
	else
		echo "  opsd (gpio $OPSD_GPIO): no debugfs at $DEBUG/gpio"
	fi
	echo "  the driver drives the lamp through the '$LOS_TRIGGER' trigger:"
	echo "    light present -> lamp off, no light -> lamp on"
}

echo "=== 1. the netdevs ==="
printf '%-12s %-10s %-9s %-8s %-14s %-14s %-14s\n' \
	name operstate carrier mtu rx_packets rx_bytes rx_dropped
for d in "$CLASS"/*; do
	[ -d "$d" ] || continue
	n=$(basename "$d")
	op=$(cat "$d/operstate" 2>/dev/null || echo "?")
	ca=$(cat "$d/carrier" 2>/dev/null || echo "?")
	mt=$(cat "$d/mtu" 2>/dev/null || echo "?")
	rp=$(cat "$d/statistics/rx_packets" 2>/dev/null || echo "?")
	rb=$(cat "$d/statistics/rx_bytes" 2>/dev/null || echo "?")
	rd=$(cat "$d/statistics/rx_dropped" 2>/dev/null || echo "?")
	printf '%-12s %-10s %-9s %-8s %-14s %-14s %-14s\n' \
		"$n" "$op" "$ca" "$mt" "$rp" "$rb" "$rd"
done

echo
echo "=== 2. the DSA ports the switch node declares ==="
for n in lan0 lan1 lan2 lan3 lan4; do
	if [ -e "$CLASS/$n" ]; then
		printf '  %-6s present  operstate=%-10s carrier=%-3s speed=%-8s rx_packets=%-12s rx_dropped=%s\n' \
			"$n" \
			"$(cat "$CLASS/$n/operstate" 2>/dev/null || echo '?')" \
			"$(cat "$CLASS/$n/carrier" 2>/dev/null || echo '?')" \
			"$(cat "$CLASS/$n/speed" 2>/dev/null || echo '?')" \
			"$(cat "$CLASS/$n/statistics/rx_packets" 2>/dev/null || echo '?')" \
			"$(cat "$CLASS/$n/statistics/rx_dropped" 2>/dev/null || echo '?')"
	else
		printf '  %-6s MISSING - the 9132 driver did not register this port\n' "$n"
	fi
done

echo
echo "=== 3. the LOS lamp and the optical RX signal detect ==="
print_lamp
# The lamp the driver drives is the one whose trigger is the driver's, so
# that is what is looked for: the name in the device tree is a label, and
# a label can be spelled differently without the lamp ceasing to be the
# lamp.
driven=""
for d in "$LEDS"/*; do
	[ -d "$d" ] || continue
	tr=$(cat "$d/trigger" 2>/dev/null || echo 'none')
	if [ "$tr" = "$LOS_TRIGGER" ]; then
		driven=$(basename "$d")
		break
	fi
done
if [ -n "$driven" ]; then
	echo "  the '$driven' lamp is under the driver's trigger: it is the lamp that answers"
elif [ -r "$LEDS/$LOS_LED/trigger" ]; then
	echo "  the '$LOS_LED' lamp's trigger is '$(cat "$LEDS/$LOS_LED/trigger" 2>/dev/null || echo 'none')', not '$LOS_TRIGGER':"
	echo "    the driver is not driving this lamp (a user may have picked another trigger)"
else
	echo "  (no lamp under the driver's '$LOS_TRIGGER' trigger)"
fi
if [ ! -r "$DEBUG/gpio" ]; then
	echo "  (no debugfs at $DEBUG/gpio - mount it with: mount -t debugfs none /sys/kernel/debug)"
fi

echo
echo "=== 4. the PON state ==="
found=0
for f in "$CLASS"/*/device/pon_state; do
	[ -r "$f" ] || continue
	found=1
	echo "  $(basename "$(dirname "$(dirname "$f")")")/pon_state: $(cat "$f")"
done
[ "$found" = 1 ] || echo "  (no pon_state attribute - the PON stack did not attach to a netdev)"

echo
echo "=== 5. the OMCI transport counters ==="
found=0
for f in "$CLASS"/*/device/omci_transport; do
	[ -r "$f" ] || continue
	found=1
	echo "  $(basename "$(dirname "$(dirname "$f")")")/omci_transport: $(cat "$f")"
done
[ "$found" = 1 ] || echo "  (no omci_transport attribute - the PON stack did not attach to a netdev)"

echo
echo "=== 6. the driver's own statistics (ethtool -S $STATDEV) ==="
print_ethtool_stats

echo
echo "=== 7. the same counters ${INTERVAL}s later ==="
sleep "$INTERVAL"
for d in "$CLASS"/*; do
	[ -d "$d" ] || continue
	n=$(basename "$d")
	rp=$(cat "$d/statistics/rx_packets" 2>/dev/null || echo "?")
	rb=$(cat "$d/statistics/rx_bytes" 2>/dev/null || echo "?")
	rd=$(cat "$d/statistics/rx_dropped" 2>/dev/null || echo "?")
	printf '  %-12s rx_packets=%-14s rx_bytes=%-14s rx_dropped=%-14s operstate=%s\n' \
		"$n" "$rp" "$rb" "$rd" \
		"$(cat "$d/operstate" 2>/dev/null || echo '?')"
done
for f in "$CLASS"/*/device/pon_state "$CLASS"/*/device/omci_transport; do
	[ -r "$f" ] || continue
	echo "  $(basename "$(dirname "$(dirname "$f")")")/$(basename "$f"): $(cat "$f")"
done
echo "  -- and the driver's own counters again, so they can be compared with the first reading:"
print_ethtool_stats
echo "  -- and the lamp again, so it can be compared with the first reading:"
print_lamp

echo
echo "=== 8. the kernel's own idea of the drivers ==="
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
