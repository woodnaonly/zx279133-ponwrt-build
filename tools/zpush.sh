#!/bin/sh
# zpush - copy a file to the flashed SK-D840N and prove it arrived intact.
# dropbear on that box has no sftp-server and no scp, so this pipes the bytes
# through `ssh ... cat > path` and compares md5 on both ends.
#   sh tools/zpush.sh <local-file> [remote-path]     (default remote: /tmp/<basename>)
#   sh tools/zpush.sh firmware.bin && sh tools/ow.sh "zflash kernel /tmp/firmware.bin"
set -u
L=${1:-}
[ -n "$L" ] && [ -f "$L" ] || { grep '^# ' "$0" | sed 's/^# //'; exit 2; }
R=${2:-/tmp/$(basename "$L")}
# Was hardcoded to /c/Users/woodn/.ssh/id_onu - a path on someone else's machine, which
# happens to be the Git-Bash spelling of a Windows user directory. Every other tool here
# resolves $HOME with an overridable KEY, and this one silently did not, so it was the only
# tool that could not work on the machine it was committed from.
KEY=${KEY:-$HOME/.ssh/id_onu}
SSH="ssh -q -o BatchMode=yes -o IdentitiesOnly=yes -i $KEY -o UserKnownHostsFile=/dev/null -o StrictHostKeyChecking=no root@192.168.9.1"

MD5L=$(md5sum "$L" | awk '{print $1}')
SZ=$(stat -c%s "$L")
echo "pushing $L ($SZ bytes, md5 $MD5L) -> $R"
timeout 600 sh -c "$SSH \"cat > '$R'\"" < "$L" || { echo "push failed"; exit 1; }
GOT=$(timeout 60 sh -c "$SSH \"md5sum '$R' 2>/dev/null | cut -d' ' -f1; wc -c < '$R'\"")
MD5R=$(echo "$GOT" | tr -d '\r' | sed -n 1p)
SZR=$(echo "$GOT" | tr -d '\r' | sed -n 2p)
if [ "$MD5L" = "$MD5R" ]; then
	echo "OK: $R is $SZR bytes, md5 $MD5R"
else
	echo "*** MD5 MISMATCH *** remote $SZR bytes md5 $MD5R"; exit 1
fi
