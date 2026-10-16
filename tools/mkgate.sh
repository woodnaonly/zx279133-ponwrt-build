#!/bin/sh
# Build the gate payload out of exactly the bytes that are about to be flashed.
#
# The gate round answers one question - does the mainline kernel this board is about to boot
# reach userspace - and it only answers it if the payload is the same code as the flash
# target. So this derives gate-Image from flash-uImage rather than from any other kernel
# build, and gate-dtb is flash-dtb.bin byte for byte.
#
# flash-uImage is a U-Boot legacy image: 64-byte header (magic 0x27051956, then the load and
# entry addresses) followed by a gzip arm64 Image. kexec wants the *raw* arm64 Image, so the
# body is inflated here - which is also why the two files differ in size by ~2x.
#
#	sh tools/mkgate.sh
set -eu
cd "$(dirname "$0")/.."
[ -s staging/trial/flash-uImage ] || { echo "staging/trial/flash-uImage missing - sh tools/gettrial.sh flash 38235539279" >&2; exit 1; }
[ -s staging/trial/flash-dtb.bin ] || { echo "flash-dtb.bin missing" >&2; exit 1; }
# staging/ctl is gitignored, so on any fresh checkout it does not exist - and nothing else in
# tools/ creates it (grep mkdir tools/ finds no writer for it). Without this line the payload
# below dies on a bare FileNotFoundError for staging/ctl/gate-Image, which reads like "you have
# not run a gate round yet, i.e. you have no board" and stops the boardless preflight rehearsal
# at step 4 for a reason that has nothing to do with the board. Same defensive mkdir the permit
# writers already use (gatewatch.sh:111, mkrollback.sh:107).
mkdir -p staging/ctl

python - <<'EOF'
import gzip, hashlib, struct, time

u = open("staging/trial/flash-uImage", "rb").read()
# struct image_header, all big-endian: magic, hcrc, time, data_size, load, ep, dcrc, then
# os/arch/type/comp as single bytes at 28..31 and ih_name[32] at 32. Reading this at the
# wrong offset is silent - the sizes still look plausible - so the magic is asserted.
magic, hcrc, ctime, dsize, load, ep, dcrc = struct.unpack(">7I", u[:28])
assert magic == 0x27051956, "not a legacy uImage (%08x)" % magic
name = u[32:64].split(b"\0")[0].decode("latin1")
os_, arch, itype, comp = u[28], u[29], u[30], u[31]
raw = u[64:64 + dsize] if dsize else u[64:]
assert raw[:2] == b"\x1f\x8b", "uImage body is not gzip (%r)" % raw[:4]
img = gzip.decompress(raw)
assert img[0x38:0x3C] == b"ARMd", "inflated body has no arm64 Image magic"
text_off, image_size = struct.unpack("<2Q", img[0x08:0x18])
open("staging/ctl/gate-Image", "wb").write(img)
dtb = open("staging/trial/flash-dtb.bin", "rb").read()
assert dtb[:4] == b"\xd0\x0d\xfe\xed", "flash-dtb.bin is not a raw dtb"
open("staging/ctl/gate-dtb", "wb").write(dtb)
UM, IM, DM = (hashlib.md5(x).hexdigest() for x in (u, img, dtb))
# Provenance record: tools/gatewatch.sh refuses to write a flash permit unless the
# flash-uImage it is about to hash still matches the one this payload was inflated from.
# Without it, a round scored after a newer artifact lands would license bytes that never ran.
open("staging/ctl/gate.src", "w").write(
    "ts=%s uimage_md5=%s image_md5=%s dtb_md5=%s\n" % (time.strftime("%FT%T"), UM, IM, DM))
print("uImage   %10d B  name=%-28r load=0x%08x ep=0x%08x os/arch/type/comp=%d/%d/%d/%d" %
      (len(u), name, load, ep, os_, arch, itype, comp))
print("Image    %10d B  text_offset=0x%x image_size=0x%x  md5=%s" %
      (len(img), text_off, image_size, IM))
print("dtb      %10d B  md5=%s" % (len(dtb), DM))
print("the gate kernel IS the flash kernel: inflated flash-uImage md5 == gate-Image md5 (%s)" % IM)
EOF

echo "gate-Image/gate-dtb staged - next: CTLTAG=K5 IMAGE_LOCAL=staging/ctl/gate-Image \
REMOTE_IMAGE=gate-Image DTB_LOCAL=staging/ctl/gate-dtb REMOTE_DTB=gate-dtb \
INITRD=ctl-initramfs.cpio.gz sh tools/pushctl.sh"
