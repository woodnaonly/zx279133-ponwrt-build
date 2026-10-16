#!/bin/sh
# Root shell on the flashed OpenWrt. The key is ~/.ssh/id_onu by default, overridable with
# KEY=, and the matching public half is what the overlay installs into
# /etc/dropbear/authorized_keys.
# Only the local side has a timeout: some /proc/pon nodes block, so keep those reads bounded by head.
# Usage: echo "cmd" | sh tools/ow.sh   |   sh tools/ow.sh "cmd"   |   sh tools/ow.sh < script.sh
# Was hardcoded to /c/Users/woodn/.ssh/id_onu - another machine's path, in Git-Bash spelling.
KEY=${KEY:-$HOME/.ssh/id_onu}
SRC=$(mktemp)
if [ $# -gt 0 ]; then printf '%s\n' "$*" > "$SRC"; else cat > "$SRC"; fi
timeout 90 ssh -q -o BatchMode=yes -o IdentitiesOnly=yes -i "$KEY" \
  -o UserKnownHostsFile=/dev/null -o StrictHostKeyChecking=no root@192.168.9.1 "sh" < "$SRC"
rc=$?
rm -f "$SRC"
exit $rc
