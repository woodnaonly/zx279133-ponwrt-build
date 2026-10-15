#!/bin/sh
# Root shell on the flashed OpenWrt (key: ~/.ssh/id_onu, installed in /etc/dropbear/authorized_keys).
# Only the local side has a timeout: some /proc/pon nodes block, so keep those reads bounded by head.
# Usage: echo "cmd" | sh tools/ow.sh   |   sh tools/ow.sh "cmd"   |   sh tools/ow.sh < script.sh
SRC=$(mktemp)
if [ $# -gt 0 ]; then printf '%s\n' "$*" > "$SRC"; else cat > "$SRC"; fi
timeout 90 ssh -q -o BatchMode=yes -o IdentitiesOnly=yes -i /c/Users/woodn/.ssh/id_onu \
  -o UserKnownHostsFile=/dev/null -o StrictHostKeyChecking=no root@192.168.9.1 "sh" < "$SRC"
rc=$?
rm -f "$SRC"
exit $rc
