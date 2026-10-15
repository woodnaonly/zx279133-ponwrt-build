#!/bin/sh
# Map this ONU's four LAN jacks to its four netdevs, from the PC side only.
#
#	sh tools/sweepjack.sh [seconds]     # default 900
#
# Why it has to be done from here: the flashed image puts eth1..eth3 in br-lan and
# leaves eth0 (NPPT + the external RTL8226) as "wan" with no address, so the jack
# wired to eth0 is the one that shows link but never answers 192.168.9.1 - and that
# is also the only jack mainline can use. Move the cable jack by jack, ~20 s each,
# and the log distinguishes them without any labelling on the case.
#
# Probes stay bound to the wired adapter's address. The lan used to be 192.168.1.1, the
# same address this PC's WiFi router answers as, which made an unbound probe report the
# router instead of the board; the lan is 192.168.9.1 now and that subnet exists only on
# the wired side, so a bound answer can only have come from the ONU.
set -eu
cd "$(dirname "$0")/.."

SECS=${1:-900}
IP=192.168.9.1
KEY=${KEY:-$HOME/.ssh/id_onu}
# Read the wired address rather than assuming it, and match it to the target's /24: this
# NIC also carries 192.168.1.190, the subnet the board left on 2026-10-12, and binding a
# probe to that would send it out the WLAN.
wired=$(powershell.exe -NoProfile -Command "\$p = ([regex]::Match('$IP', '^\d+\.\d+\.\d+')).Value; (Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue | Where-Object { \$_.IPAddress -like \"\$p.*\" -and \$_.SuffixOrigin -ne 'Link' } | Select-Object -First 1).IPAddress" 2>/dev/null | tr -d '\r\n')
[ -n "$wired" ] || { echo "no 802.3 adapter with an IPv4 address - cannot bind probes" >&2; exit 1; }
echo "probing $IP bound to wired source $wired for ${SECS}s; move the cable jack by jack"

while [ "$SECS" -gt 0 ]; do
	SECS=$(( SECS - 5 ))
	link=$(powershell.exe -NoProfile -Command '(Get-NetAdapter | Where-Object { $_.PhysicalMediaType -eq "802.3" } | Select-Object -First 1).Status' 2>/dev/null | tr -d '\r\n')
	neigh=$(powershell.exe -NoProfile -Command "(Get-NetNeighbor -IPAddress $IP -ErrorAction SilentlyContinue | Where-Object { \$_.LinkLayerAddress -notmatch '^00-00' } | Select-Object -ExpandProperty LinkLayerAddress) -join ','" 2>/dev/null | tr -d '\r\n')
	ping=$(ping -n 1 -w 300 -S "$wired" "$IP" 2>&1 | grep -ac "TTL=" || true)
	pcmac=$(powershell.exe -NoProfile -Command '(Get-NetAdapter | Where-Object { $_.PhysicalMediaType -eq "802.3" } | Select-Object -First 1).MacAddress' 2>/dev/null | tr -d '\r\n')
	# brctl reports the bridge port that learned the PC's own MAC, which is the
	# jack -> netdev mapping in one number; carrier/ethtool are useless here because
	# this vendor driver reports carrier=1 on every port.
	pcmac=$(echo "$pcmac" | tr 'A-Z-' 'a-z:')
	who=$(timeout 6 ssh -b "$wired" -o BatchMode=yes -o ConnectTimeout=3 -o IdentitiesOnly=yes \
		-i "$KEY" -o UserKnownHostsFile=/dev/null -o StrictHostKeyChecking=no "root@$IP" \
		"echo -r=\$(uname -r) -up=\$(cut -d. -f1 /proc/uptime) -port=\$(brctl showmacs br-lan 2>/dev/null | grep '$pcmac' | awk '{print \$2}' | head -1)" 2>/dev/null || true)
	printf '%s link=%s ping=%s ssh=[%s] neigh=%s\n' "$(date +%H:%M:%S)" "${link:-?}" "$ping" "${who:-no}" "${neigh:-none}"
done
