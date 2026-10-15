#!/bin/sh
# Watch for the kexec'd RAM system to answer, from the PC side.
#
# There is no serial console on this unit, so "the new kernel is alive" can only
# mean "something owns 192.168.9.1 on the wired port". Two things make that check
# have to be done carefully here:
#
#   * the PC is co-homed: it has 192.168.1.0/24 on both the wired adapter and WLAN, and
#     the board used to answer 192.168.1.1 there too - the same address as the user's
#     router - so an unbound ping or ssh could be answered over WiFi and look like
#     success while the wired port was dead. The board's lan is 192.168.9.1 now, a subnet
#     that exists only on the wired adapter, and both probes below stay bound anyway.
#   * while the flashed system runs with the cable in the uplink RJ45 (its wan
#     port), 192.168.9.1 lives only in its lan bridge, which that port is not part
#     of - so a bound answer can only come from the new system.
#
#	sh tools/wait4net.sh [seconds]     # default 300
#
# When it reports an answer, run: python tools/ramcheck.py
set -eu
cd "$(dirname "$0")/.."

IP=${IP:-192.168.9.1}
SECS=${1:-300}
REC=staging/trial/hostkey-before.txt
END=$(( $(date +%s) + SECS ))

before=$(ssh-keygen -lf "$REC" 2>/dev/null | awk '{print $2}' | sort | tr '\n' ' ')
beforemac=$(sed -n 's/^MAC=//p' "$REC" 2>/dev/null | tr -d '\r' | tail -1)
echo "recorded before the trial: mac=${beforemac:-?} hostkeys=${before:-?}"

# "<srcip> <neighbor mac> <link status>" for the wired adapter only, so a WiFi
# answer can never be mistaken for the trial working.
#
# The source address is picked by *subnet match* against the target rather than as the
# adapter's first address, because this NIC now carries 192.168.1.190 (the user's router
# subnet, which the board used to live on) as well as the 192.168.9.190 the board moved
# to. "First address" would have bound the probe to the wrong subnet and sent it out the
# WLAN, which is the exact failure this watcher is meant to be able to tell apart.
psprobe() {
	powershell.exe -NoProfile -Command '
		$dst = "192.168.9.1"
		$p = (([regex]::Match($dst, "^\d+\.\d+\.\d+")).Value)
		$a = Get-NetAdapter | Where-Object { $_.PhysicalMediaType -eq "802.3" } | Select-Object -First 1
		if (-not $a) { "NOWIRED - -"; exit }
		$ip = (Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue |
			Where-Object { $_.IPAddress -like "$p.*" -and $_.PrefixOrigin -ne "WellKnown" } |
			Select-Object -First 1).IPAddress
		if (-not $ip) { $ip = (Get-NetIPAddress -InterfaceIndex $a.IfIndex -AddressFamily IPv4 -ErrorAction SilentlyContinue |
			Select-Object -First 1).IPAddress }
		$mac = (Get-NetNeighbor -IPAddress $dst -InterfaceIndex $a.IfIndex -ErrorAction SilentlyContinue |
			Select-Object -Last 1).LinkLayerAddress
		"{0} {1} {2}" -f $(if ($ip) { $ip } else { "NOPREFIX" }), $(if ($mac) { $mac } else { "-" }), $a.Status
	' 2>/dev/null | tr -d '\r\n'
}

# TCP connect to 22 with the wired address as the source: this is what proves the
# answer came in over the port the cable was moved to.
bound22() {
	python -c "
import socket
try:
    s = socket.create_connection(('$IP', 22), timeout=2, source_address=('$srcip', 0))
    s.close()
    print('OPEN')
except Exception:
    print('')
" 2>/dev/null
}

while [ "$(date +%s)" -lt "$END" ]; do
	now=$(date +%H:%M:%S)
	srcip=- mac=- wired=-
	IFS=' ' read -r srcip mac wired <<EOF || true
$(psprobe)
EOF
	[ -n "${srcip:-}" ] || srcip=-
	alive=no
	if [ "$srcip" != "-" ] && [ "$srcip" != NOPREFIX ]; then
		ping -n 1 -w 400 -S "$srcip" "$IP" >/dev/null 2>&1 && alive=yes
		port=$(bound22)
	else
		port=
	fi
	if [ -n "$port" ]; then
		key=$(ssh-keyscan -q -T 3 "$IP" 2>/dev/null | ssh-keygen -lf - 2>/dev/null | awk '{print $2}' | sort | tr '\n' ' ')
		if [ "$key" != "$before" ]; then
			echo "$now ssh answered on the wired port with a host key that is not the flashed one"
			echo "        src=$srcip mac=$mac link=$wired  ->  this is the RAM system"
			echo "        now: python tools/ramcheck.py"
			exit 0
		fi
		echo "$now src=$srcip ping=$alive mac=$mac ssh=the flashed system's key (still in charge)"
	else
		echo "$now src=$srcip ping=$alive mac=$mac link=$wired ssh=no answer"
	fi
	sleep 3
done

echo "timed out after ${SECS}s: nothing new owns $IP over the wired port."
echo "Check the cable is in the uplink RJ45 (the vendor calls it wan/eth0) - the LAN"
echo "ports sit behind the internal 9132 switch, which mainline cannot drive."
echo "Power-cycle the board and the flashed system comes back; nothing was written."
exit 1
