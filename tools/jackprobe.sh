#!/bin/sh
# One-shot: what does this RJ45 give me?
#
#	sh tools/jackprobe.sh
#
# Move the cable to a jack, run this, and read the verdict. It matters because the
# flashed image keeps eth1..eth3 in br-lan and leaves eth0 (NPPT + the external
# RTL8226) as its "wan" with no address - and eth0 is the only port mainline can
# drive, so the trial has to be run from that jack. This board has no WAN jack (4x
# LAN + PON), so which jack it is can only be found by trying them.
#
# Verdicts:
#   reachable (any eth1..eth3)  -> a lan jack, fine for ssh but useless for mainline
#   link up, nothing answers    -> the eth0 jack, which is the one we want
#   no link at all              -> the cable, the PHY or a dead port
#
# The probes are bound to, or filtered by, the wired adapter. The board's lan used to be
# 192.168.1.1, which this PC's WiFi router also owns - an unbound ping then reported the
# router and looked exactly like the ONU - so on 2026-10-12 the lan moved to 192.168.9.1,
# a subnet that exists only on the wired adapter. Binding stays: it is what turns an
# answer into a statement about the board rather than about the network.
set -u
IP=${IP:-192.168.9.1}
KEY=${KEY:-$HOME/.ssh/id_onu}

# The source is chosen by matching the target's /24 rather than as "the adapter's first
# address": the wired NIC now holds 192.168.1.190 (the router subnet the board used to be
# on) as well as 192.168.9.190, and binding to the wrong one would send the probe out
# over WLAN and make a dead jack look alive - the failure mode this script exists to see.
ifinfo=$(powershell.exe -NoProfile -Command "
	\$a = Get-NetAdapter | Where-Object { \$_.PhysicalMediaType -eq '802.3' } | Select-Object -First 1
	\$p = ([regex]::Match('$IP', '^\d+\.\d+\.\d+')).Value
	\$ip = (Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue |
		Where-Object { \$_.IPAddress -like \"\$p.*\" -and \$_.SuffixOrigin -ne 'Link' } |
		Select-Object -First 1).IPAddress
	if (-not \$ip) { \$ip = (Get-NetIPAddress -InterfaceIndex \$a.IfIndex -AddressFamily IPv4 -ErrorAction SilentlyContinue |
		Where-Object { \$_.SuffixOrigin -ne 'Link' } | Select-Object -First 1).IPAddress }
	\"{0}|{1}|{2}\" -f \$a.Status, \$(if (\$ip) { \$ip } else { '-' }), \$a.IfIndex" 2>/dev/null | tr -d '\r\n')
link=${ifinfo%%|*}
rest=${ifinfo#*|}
wired=${rest%%|*}
ifidx=${rest##*|}
echo "wired adapter: link=$link source=$wired ifindex=$ifidx"

[ "$link" = "Up" ] || { echo "verdict: no link - check the cable/port"; exit 0; }
[ "$wired" != "-" ] || { echo "verdict: linked, but no IPv4 address to bind to"; exit 0; }

ping -n 3 -w 400 -S "$wired" "$IP" >/dev/null 2>&1 && pings=yes || pings=no
# -InterfaceIndex is the whole point: without it the WiFi router's entry for the
# same address shows up here and a dead jack looks alive.
mac=$(powershell.exe -NoProfile -Command "(Get-NetNeighbor -IPAddress $IP -InterfaceIndex $ifidx -ErrorAction SilentlyContinue | Where-Object { \$_.LinkLayerAddress -notmatch '^00-00' } | Select-Object -First 1 -ExpandProperty LinkLayerAddress)" 2>/dev/null | tr -d '\r\n')
who=$(timeout 8 ssh -b "$wired" -o BatchMode=yes -o ConnectTimeout=4 -o IdentitiesOnly=yes \
	-i "$KEY" -o UserKnownHostsFile=/dev/null -o StrictHostKeyChecking=no "root@$IP" \
	'uname -r; cut -d. -f1 /proc/uptime' 2>/dev/null | tr '\n' ' ')

echo "bound ping=$pings wired-arp=${mac:-none} bound ssh=[${who:-no}]"
if [ -n "$who" ]; then
	echo "verdict: lan jack (br-lan, one of eth1..eth3) - good for ssh, dead under mainline"
elif [ "$pings" = yes ]; then
	echo "verdict: ping answered without a wired ARP entry - not this segment; re-check"
else
	echo "verdict: linked and silent - this is the eth0 jack, the one to run the trial from"
fi
