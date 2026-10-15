#!/bin/bash
# Fetch /tmp/fw20/parts/p.NN from the VPS HTTP server through the local SOCKS proxy,
# retrying until each part is exactly the expected size (a truncated part must never
# be accepted -- that is what silently corrupted the first attempt).
cd "$(dirname "$0")/.." || exit 1
URL=http://103.167.150.121:8099/parts
tot=11812720; n=$(( (tot + 1048575) / 1048576 ))
fail=0
for ((i=0;i<n;i++)); do
  idx=$(printf '%02d' $i); want=$(( tot > (i+1)*1048576 ? 1048576 : tot - i*1048576 ))
  out=staging/parts/p.$idx
  for ((t=1;t<=8;t++)); do
    got=$(stat -c %s "$out" 2>/dev/null || echo 0)
    [ "$got" = "$want" ] && break
    rm -f "$out"
    curl -sS --proxy socks5h://127.0.0.1:10808 --max-time 120 -o "$out" "$URL/p.$idx" </dev/null >/dev/null 2>&1
    got=$(stat -c %s "$out" 2>/dev/null || echo 0)
    echo "p.$idx attempt $t got=$got want=$want"
    [ "$got" = "$want" ] && break
    sleep 3
  done
  got=$(stat -c %s "$out" 2>/dev/null || echo 0)
  [ "$got" = "$want" ] || { echo "p.$idx FAILED after retries"; fail=1; }
done
if [ $fail -eq 0 ]; then
  cat staging/parts/p.* > staging/rootfs.jffs2
  echo "assembled=$(stat -c %s staging/rootfs.jffs2) expect=$tot"
  md5sum staging/rootfs.jffs2; echo "want_md5=0fd03a1115374f5335d7fc207b14a849"
else echo "PARTS INCOMPLETE"; fi
