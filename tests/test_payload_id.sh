#!/bin/sh
# 本地排演套件（local rehearsal suite）：依赖 staging/ 下的真实构建产物，
# 在只 clone 本仓库的环境里跑不起来，也**没有**接进 CI。
# 为什么、怎么跑、缺的那个夹具从哪来：同目录 README.md。
# Harness for tools/payload-id.sh: syntax, encoding, and a positive/negative pair per exit path.
#
# A tool that only ever succeeds is not a tool. Every branch here has to be shown refusing
# something it should refuse, otherwise "it printed a verdict" proves nothing about the verdict.
set -eu
cd "$(dirname "$0")/../.."
T=tools/payload-id.sh
T1=$(tr -cd '\r' < "$T" | wc -c)
[ "$T1" = 0 ] || { echo "FAIL: $T carries $T1 CR bytes"; exit 1; }
sh -n "$T"

pass=0
ok()   { pass=$((pass+1)); printf 'PASS %s\n' "$1"; }
bad()  { echo "FAIL $1"; exit 1; }

tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT

# --- the staged image fingerprints, and the fingerprint is the payload, not the file ----------
out=$(sh "$T" 2>&1) || bad "plain run exited $?"
echo "$out" | grep -q 'payload md5' || bad "no payload md5 printed"
echo "$out" | grep -q 'load/entry' || bad "no load/entry printed"
# The file digest must appear, but it must not be what the tool hangs its verdict on.
echo "$out" | grep -q 'file md5' || bad "no file md5 printed"
ok "fingerprint of the staged uImage"

# --- --record pins it, and says why the file md5 is not pinned -------------------------------
out=$(sh "$T" --record 2>&1) || bad "--record exited $?"
[ -s staging/ctl/uimage-payload-id.txt ] || bad "--record wrote no reference"
grep -q '^payload_md5=' staging/ctl/uimage-payload-id.txt || bad "reference has no payload_md5="
grep -q 'timestamp' staging/ctl/uimage-payload-id.txt || bad "reference does not say why file md5 is absent"
ok "--record pins the payload identity"

# --- comparing a rebuild of the same payload to itself: SAME, exit 0 -------------------------
# Fabricate exactly what a later mkimage would produce: same payload, new header timestamp.
python3 - staging/trial/flash-uImage "$tmp/rebuild" <<'PYEOF'
import struct, sys, zlib, time
d = bytearray(open(sys.argv[1], 'rb').read())
new = bytearray(d); new[8:12] = struct.pack('>I', int(time.time()) + 86400)
struct.pack_into('>I', new, 4, 0)
s = bytearray(new[:64]); s[4:8] = b'\x00\x00\x00\x00'
struct.pack_into('>I', new, 4, zlib.crc32(bytes(s)) & 0xFFFFFFFF)
open(sys.argv[2], 'wb').write(bytes(new))
PYEOF
cmp -s staging/trial/flash-uImage "$tmp/rebuild" && bad "the rebuild fixture is identical to the original"
out=$(sh "$T" "$tmp/rebuild" 2>&1) && rc=0 || rc=$?
[ "$rc" = 0 ] || bad "same-payload rebuild should be SAME/exit 0, got exit $rc"
echo "$out" | grep -q 'SAME PAYLOAD' || bad "same-payload rebuild was not called SAME"
echo "$out" | grep -q 'timestamp only' || bad "SAME verdict does not mention the timestamp"
ok "timestamp-only rebuild reads as SAME payload, exit 0"

# --- a genuinely different payload: DIFFERENT, exit 1 ---------------------------------------
python3 - staging/trial/flash-uImage "$tmp/different" <<'PYEOF'
import struct, sys, zlib
d = bytearray(open(sys.argv[1], 'rb').read())
size = struct.unpack('>I', d[12:16])[0]
d[64 + size // 2] ^= 0xff          # one flipped payload byte, nothing else
struct.pack_into('>I', d, 24, zlib.crc32(bytes(d[64:64+size])) & 0xFFFFFFFF)
struct.pack_into('>I', d, 4, 0)
s = bytearray(d[:64]); s[4:8] = b'\x00\x00\x00\x00'
struct.pack_into('>I', d, 4, zlib.crc32(bytes(s)) & 0xFFFFFFFF)
open(sys.argv[2], 'wb').write(bytes(d))
PYEOF
out=$(sh "$T" "$tmp/different" 2>&1) && rc=0 || rc=$?
[ "$rc" = 1 ] || bad "different payload should exit 1, got exit $rc"
echo "$out" | grep -q 'DIFFERENT PAYLOAD' || bad "different payload was not called DIFFERENT"
echo "$out" | grep -q 'only a gate round' || bad "DIFFERENT verdict does not point at the gate round"
ok "altered payload reads as DIFFERENT, exit 1"

# --- negative: not a uImage ------------------------------------------------------------------
head -c 4096 /dev/urandom > "$tmp/junk"
sh "$T" "$tmp/junk" >"$tmp/o" 2>&1 && bad "random bytes were accepted as a uImage"
grep -q 'not a legacy uImage' "$tmp/o" || bad "wrong refusal for random bytes: $(cat "$tmp/o")"
ok "random bytes refused"

# --- negative: a truncated uImage must not have its payload believed -------------------------
head -c 2000 staging/trial/flash-uImage > "$tmp/short"
sh "$T" "$tmp/short" >"$tmp/o" 2>&1 && bad "truncated uImage was accepted"
grep -q 'truncated' "$tmp/o" || bad "wrong refusal for truncation: $(cat "$tmp/o")"
ok "truncated uImage refused"

# --- negative: a corrupted payload CRC must not be reported as a readable identity ------------
cp staging/trial/flash-uImage "$tmp/badcrc"
python3 - "$tmp/badcrc" <<'PYEOF'
import sys
p = sys.argv[1]
b = bytearray(open(p, 'rb').read())
b[100] ^= 0xff          # payload byte only; both stored CRCs now lie
open(p, 'wb').write(bytes(b))
PYEOF
sh "$T" "$tmp/badcrc" >"$tmp/o" 2>&1 && bad "uImage with a bad payload CRC was accepted"
grep -q 'CRC does not check out' "$tmp/o" || bad "wrong refusal for bad CRC: $(cat "$tmp/o")"
ok "payload-CRC corruption refused"

echo "all $pass checks passed"
