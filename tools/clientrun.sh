#!/bin/sh
# Put a client behind the router and make it reach the internet.
#
#   sh tools/clientrun.sh 192.168.137.215
#
# The bench has one cable, in the board's port that is now the WAN.  A
# router needs an upstream on one port and a client on another, so the
# client has to be one the board makes for itself: a veth pair, one end in
# the board's root namespace and the other in a network namespace of its
# own, with the board's own DHCP server answering on it.
#
# That is what the trial image's S27labclient does.  The flashed image does
# not carry it (it is a trial file), so it is copied over.
#
# The proof is not "the client has an address".  It is: the client has an
# address FROM THE BOARD, and a ping from it comes back through the board's
# own masquerade - which shows up as the board's wan address in the
# conntrack table, and as a counter on the masquerade rule that moved.
set -e
cd "$(dirname "$0")"

WAN=${1:-192.168.137.215}
HERE=$(pwd)
# HERE is .../SK-D840N/ci-build-repo/tools, so the repository - which is
# where trial-files/ lives - is one level up.
REPO=$(dirname "$HERE")
CLIENT="$REPO/trial-files/etc/rc.d/S27labclient"
KEY="$HOME/.ssh/id_onu"
SSH="ssh -q -o BatchMode=yes -o PreferredAuthentications=publickey -o IdentitiesOnly=yes -o ConnectTimeout=8 -o UserKnownHostsFile=/dev/null -o StrictHostKeyChecking=no -i $KEY"

echo "=== copying the client over ==="
# The bind goes through ssh's -o BindAddress: scp has no -b of its own, and
# an unpinned packet to the board leaves through the radio and never comes
# back.
scp -O -q -o BindAddress="${SKD_BIND:-192.168.137.1}" -i "$KEY" \
	-o BatchMode=yes -o UserKnownHostsFile=/dev/null -o StrictHostKeyChecking=no \
	"$CLIENT" "root@$WAN:/tmp/S27labclient"
echo "copied"

echo
echo "=== starting it ==="
$SSH "root@$WAN" 'chmod 755 /tmp/S27labclient; sh -n /tmp/S27labclient && echo "it parses"; setsid sh /tmp/S27labclient >/tmp/labclient.log 2>&1 &' 2>&1

echo
echo "=== waiting for the client to take a lease from the board ==="
i=0
while [ "$i" -lt 40 ]; do
	i=$((i + 1))
	status=$($SSH "root@$WAN" 'cat /tmp/labclient.status 2>/dev/null' 2>/dev/null || true)
	case "$status" in
	*lease:*192.168.9.*)
		break
		;;
	esac
	sleep 3
done
STATUS=$($SSH "root@$WAN" 'cat /tmp/labclient.status 2>/dev/null' 2>&1 | tr -d '')
echo "$STATUS"

# The loop above breaks on a lease; reaching here without one is the failure,
# and it used to be a success: this script ended in `|| true` and exited 0, so
# the round continued to the counters, printed an empty conntrack, and the only
# thing that caught it was natverdict.py.  natround.sh runs with `set -e` and
# `set -o pipefail`, so a non-zero here now stops the round and its trap puts
# the bench back - which is worth the minutes the round would otherwise waste.
case "$STATUS" in
	*lease:*192.168.9.*)	;;
	*) echo "clientrun: the client took no lease in $((i * 3)) s - the round cannot show routing without one" >&2
	   exit 1 ;;
esac
