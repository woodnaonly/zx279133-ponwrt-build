#!/bin/sh
# SPDX-License-Identifier: GPL-2.0-only
# Shared by board.json generation and the retained-config migration.
# Callback: id, display name, sysfs, trigger, default, netdev, mode, USB ports.
skd840n_led_defaults() {
	local i name
	skd840n_led power Power skyworth:green:power default-on 1 || return 1
	skd840n_led pon PON skyworth:green:pon zx279133-pon 0 || return 1
	# Boot fail-safe is in DTS; UCI default=0 also makes switching to none dark.
	skd840n_led los LOS skyworth:red:los zx279133-los 0 || return 1
	skd840n_led wan 'Internet (PON)' skyworth:green:wan netdev 0 eth0 'link tx rx' || return 1
	for i in 1 2 3 4; do
		name="LAN$i"
		[ "$i" != 1 ] || name='2.5G/LAN1'
		skd840n_led "lan$i" "$name" "skyworth:green:lan$i" netdev 0 "lan$i" 'link tx rx' || return 1
	done
	# There is no voice-state driver yet; do not fake a registration/call lamp.
	skd840n_led fxs FXS skyworth:green:phone1 none 0 || return 1
	# One xHCI controller, USB2 and USB3 companion root hubs, one physical socket.
	skd840n_led usb USB skyworth:green:usb1 usbport 0 '' '' 'usb1-port1 usb2-port1'
}
