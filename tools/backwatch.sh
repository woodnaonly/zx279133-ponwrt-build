#!/bin/sh
# Watch for the board to become reachable again, and say which of the three possible
# identities it came back as. Only the user can make it reachable - the board is parked in
# a kexec payload with the CRM reset path masked, so nothing in software can eject it.
#
# Targets, and what each one means:
#   192.168.9.1  the flashed system's lan bridge (OpenWrt 24.10.8, what we roll back to)
#   192.168.9.2  eth0 = the RTL8226/2.5G jack on the flashed system, and the only jack that
#                can answer once mainline owns the board (the 9132 LAN switch has no driver)
#   192.168.1.1  a build that regenerated /etc/config/network from defaults instead of
#                keeping the 9.x config - would look like a dead board otherwise. This one
#                is NOT a trigger on its own: this PC sits on 192.168.1.0/24 twice (wired and
#                WLAN) and the user's router answers 192.168.1.1, so a ping there proves
#                nothing. A target only counts when ssh actually returns a uname line.
#
#	sh tools/backwatch.sh [minutes]      # default 40
set -u
cd "$(dirname "$0")/.."
KEY=${KEY:-$HOME/.ssh/id_onu}
LOG=staging/backwatch.log
END=$(( $(date +%s) + ${1:-40} * 60 ))
echo "$(date '+%F %T') watching for the board, until $(date -d "@$END" '+%T' 2>/dev/null || echo "+${1:-40}m")" | tee -a "$LOG"
# ssh is the only *trigger*, but it is not the only signal, and the three ways the board can
# be silent look identical without them:
#   link=<status of the PC adapter holding 192.168.9.190>  Disconnected means the cable is not
#      in a port the PC is linked to at all - the board can be fully alive and booting.
#   arp=yes|no  an ARP entry for 192.168.9.1/.2 or the board MAC means something answers on
#      layer 2. arp=yes + ssh=silent is a config problem (a payload that set no address, or
#      dropbear bound to an interface that does not exist), which is fixed differently from
#      "the board is off".
# Both are logged only when they change, so the file stays readable while a person works
# through the jacks one at a time.
LINKSTATE=""
l2state() {
	S=$(powershell -NoProfile -Command '$i=(Get-NetIPAddress -IPAddress 192.168.9.190 -ErrorAction SilentlyContinue).ifIndex; if($i){(Get-NetAdapter -IncludeHidden | Where-Object ifIndex -eq $i).Status}else{"no-9.190-address"}' 2>/dev/null | tr -d "\r")
	A=$(arp -a 2>/dev/null | grep -icE "192\.168\.9\.(1|2) |d0[-:.]45[-:.]8d[-:.]84[-:.]09[-:.]00")
	printf 'link=%s arp=%s' "${S:-unknown}" "$([ "$A" -gt 0 ] && echo yes || echo no)"
}
while [ "$(date +%s)" -lt "$END" ]; do
	S=$(l2state)
	[ "$S" = "$LINKSTATE" ] || { LINKSTATE="$S"; echo "$(date '+%T') $S" | tee -a "$LOG"; }
	for ip in 192.168.9.1 192.168.9.2 192.168.1.1; do
		OUT=$(timeout 12 ssh -q -o BatchMode=yes -o IdentitiesOnly=yes -i "$KEY" \
			-o UserKnownHostsFile=/dev/null -o StrictHostKeyChecking=no \
			-o ConnectTimeout=5 root@$ip \
			'echo "uname: $(uname -a)"; echo "cmdline: $(cat /proc/cmdline)"; head -8 /proc/mtd' 2>&1)
		case "$OUT" in *uname:*)
			echo "$(date '+%T') $ip ANSWERED over ssh:" | tee -a "$LOG"
			echo "$OUT" | tee -a "$LOG"
			echo "$(date '+%T') board is reachable on $ip - this is the window to run the round" | tee -a "$LOG"
			exit 0;;
		esac
	done
	sleep 12
done
echo "$(date '+%F %T') still silent after the window: no answer on 9.1, 9.2 or 1.1" | tee -a "$LOG"
echo "If the panel is pulsing, the payload is alive but the cable is in a jack mainline" >&2
echo "cannot reach - try the other jacks, one at a time, while this runs." >&2
exit 1
