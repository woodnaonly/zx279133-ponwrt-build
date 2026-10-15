#!/bin/sh
# The part of a uImage that decides what boots.
#
#	sh tools/payload-id.sh                 # fingerprint the staged flash-uImage
#	sh tools/payload-id.sh --record        # pin that fingerprint next to the permit
#	sh tools/payload-id.sh <other>         # compare <other> against the pinned fingerprint
#
# Why this exists: staging/ctl/flash.permit records md5 of the *whole* uImage, and
# `Package flash images` runs mkimage over the payload on every build. mkimage writes the wall
# clock into the header, so the same kernel comes back as a different file every time - which
# means a rebuild stops matching the permit line for a reason that cannot affect booting.
#
# That distinction is the whole point. Two uImages whose payload, load address and entry point
# match boot the same way no matter what their headers say, so a boot proof for one transfers to
# the other exactly. Two whose payloads differ do not, and the refusal they get is real. The
# permit cannot tell those apart on its own - it hashes bytes that include a build timestamp -
# so the question has to be answerable before someone is standing over a board.
#
# Read-only. This never edits the permit and never grants anything: it says whether the bytes
# are boot-identical, and what to do about that stays the owner's decision.
set -eu
cd "$(dirname "$0")/.."

REF=staging/ctl/uimage-payload-id.txt

payload_id() {
	python3 - "$1" <<'PYEOF'
import struct, sys, zlib, hashlib

def hcrc_of(h):
    s = bytearray(h[:64]); s[4:8] = b'\x00\x00\x00\x00'
    return zlib.crc32(bytes(s)) & 0xFFFFFFFF

p = sys.argv[1]
d = open(p, 'rb').read()
if len(d) < 64 or d[0:4] != b'\x27\x05\x19\x56':
    sys.exit('%s: not a legacy uImage (bad magic or too short)' % p)

hcrc, ts, size, load, ep, dcrc = struct.unpack('>IIIIII', d[4:28])
os_, arch, typ, comp = struct.unpack('>BBBB', d[28:32])
name = d[32:64].rstrip(b'\x00').decode('latin1', 'replace')

# Refuse to fingerprint a file whose own integrity fields do not check out, rather than
# reporting a payload that was misread. hcrc covers the whole 64-byte header including magic,
# with its own field zeroed; dcrc is a plain crc32 of the payload. Both were confirmed against
# the two unrelated uImages in this tree (a CI build and the vendor's own).
if hcrc_of(d) != hcrc:
    sys.exit('%s: header CRC does not check out - not a readable uImage' % p)
if len(d) < 64 + size:
    sys.exit('%s: truncated - header claims %d payload bytes, file holds %d'
             % (p, size, len(d) - 64))
if (zlib.crc32(d[64:64+size]) & 0xFFFFFFFF) != dcrc:
    sys.exit('%s: payload CRC does not check out' % p)

print('source   %s' % p)
print('  file md5     %s' % hashlib.md5(d).hexdigest())
print('  payload md5  %s' % hashlib.md5(d[64:64+size]).hexdigest())
print('  payload crc  %08x  (%d bytes)' % (dcrc, size))
print('  load/entry   0x%08x / 0x%08x' % (load, ep))
print('  os/arch/typ  %d/%d/%d  comp=%d  name=%r' % (os_, arch, typ, comp, name))
print('  built at     %d' % ts)
PYEOF
}

MODE=${1:-}
# Every branch is spelled out, including the empty one. A `case` with no `*)` and an unset $1
# falls off the end and exits 0 having printed nothing, which is the worst possible outcome for a
# tool whose whole job is to print a verdict - a caller checking only the exit code reads that
# as success.
case "$MODE" in
'') payload_id staging/trial/flash-uImage ;;
--record)
	echo "=== pin the staged payload identity ==="
	payload_id staging/trial/flash-uImage | sed 's/^/  /'
	{
		echo "# Boot-relevant identity of the uImage staging/ctl/flash.permit permits."
		echo "# Written by tools/payload-id.sh --record. Compare against it after a rebuild:"
		echo "#   sh tools/payload-id.sh staging/trial/flash-uImage"
		echo "# file md5 is deliberately absent - mkimage rewrites the header timestamp on every"
		echo "# build, so the file digest moves even when nothing that boots has changed."
		echo "payload_md5=$(payload_id staging/trial/flash-uImage | sed -n 's/.*payload md5 *//p')"
	} > "$REF"
	echo "  written $REF"
	cat "$REF"
	;;
*)
	if [ -n "$MODE" ] && [ -s "$MODE" ]; then OTHER=$MODE; else OTHER=staging/trial/flash-uImage; fi
	if [ -s "$REF" ]; then
		echo "=== pinned payload identity ==="
		cat "$REF" | sed 's/^/  /'
	else
		echo "=== no pinned payload identity ($REF missing) ==="
		echo "  run: sh tools/payload-id.sh --record"
		exit 2
	fi
	echo
	echo "=== staged now ==="
	payload_id "$OTHER" | sed 's/^/  /'
	echo
	PINNED=$(sed -n 's/^payload_md5=//p' "$REF")
	NOW=$(payload_id "$OTHER" | sed -n 's/.*payload md5 *//p')
	if [ "$PINNED" = "$NOW" ]; then
		echo "SAME PAYLOAD - these bytes boot the same way. A permit refusal here is about"
		echo "  the header timestamp only. Either run the gate round for a real proof, or record"
		echo "  an operator waiver; which one is the owner's call, not this script's."
		exit 0
	fi
	echo "DIFFERENT PAYLOAD - the kernel itself changed. A refusal here is real: the board has"
	echo "  never run these bytes, and only a gate round can prove they come up."
	exit 1
	;;
esac
