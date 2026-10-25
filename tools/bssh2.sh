#!/bin/sh
# One ssh to the board, with the command read from a file.
#
#   sh tools/bssh2.sh tools/cmd-portafter.txt
#
# The reason this exists: a command with quotes inside it, sent through
# `bash -lc "..."` from PowerShell, needs three levels of escaping and breaks
# at the third.  Writing the command in a file and sending it with a here-doc
# removes every level but one.
#
# The command file is fed to the board's shell on its standard input, so it
# is ordinary shell text - no argument quoting at all.
#
# The source address is pinned.  This PC has a static neighbour entry for
# 192.168.9.1 on the WLAN adapter, left over from an earlier experiment, so
# an unpinned packet to the board leaves through the radio and never comes
# back: the board was up and answering pings while ssh said "Connection
# timed out" (measured 2026-10-25 02:50).
set -e
BIND=${SKD_BIND:-192.168.9.25}
HOST=${SKD_HOST:-192.168.9.1}
KEY="$HOME/.ssh/id_onu"
CMD="$1"
test -n "$CMD" || { echo "usage: bssh2.sh <command file>" >&2; exit 2; }
ssh -q -b "$BIND" \
	-o BatchMode=yes -o PreferredAuthentications=publickey \
	-o IdentitiesOnly=yes -o ConnectTimeout=8 \
	-o ServerAliveInterval=5 -o ServerAliveCountMax=2 \
	-o UserKnownHostsFile=/dev/null -o StrictHostKeyChecking=no \
	-i "$KEY" "root@$HOST" sh -s < "$CMD"
