#!/bin/sh
# 本地排演套件（local rehearsal suite）：依赖 staging/ 下的真实构建产物，
# 在只 clone 本仓库的环境里跑不起来，也**没有**接进 CI。
# 为什么、怎么跑、缺的那个夹具从哪来：同目录 README.md。
# Byte-level end-to-end check of the upgrade path on the REAL artifact.
#
# test_platform_check.sh already proves the gate accepts flash-sysupgrade.bin and that
# platform_do_upgrade asks mtd to write root, dtb and kernel. It only checks the LABELS
# though: writing the dtb member into the kernel partition, or writing a truncated member,
# would still pass that test. On a board with no serial console the partition you write is
# the last thing you can check, so the bytes matter as much as the label.
#
# The fake mtd here copies the source file it was handed and records
#   <label> <sha256 of what it was asked to write>
# so the comparison is against the tar members themselves and against the standalone
# flash-set files, not against anything this test invented.
set -u
cd /mnt/c/ugit/SK-D840N4
export LC_ALL=C

SH=ci-build-repo/overlays/target/linux/zte/zx279133/base-files/lib/upgrade/platform.sh
IMG=staging/trial/flash-sysupgrade.bin
W=$(mktemp -d); mkdir -p "$W/bin" "$W/written"
echo '. /dev/null' >"$W/functions.sh"
sed "s#^\. /lib/functions\.sh#. $W/functions.sh#" "$SH" >"$W/platform.sh"

pass=0; fail=0
ok()   { printf '  PASS  %-48s %s\n' "$1" "$2"; pass=$((pass+1)); }
bad()  { printf '  FAIL  %-48s %s\n' "$1" "$2"; fail=$((fail+1)); }
short() { printf '%s' "$1" | cut -c1-16; }
cmp_sha() { # label expected-sha actual-sha
	if [ "$2" = "$3" ]; then ok "$1" "$(short "$2")"
	else bad "$1" "expected $(short "$2") got $(short "$3")"; fi
}
sha() { sha256sum "$1" 2>/dev/null | cut -d' ' -f1; }

cat >"$W/bin/mtd" <<EOF
#!/bin/sh
# emulate: mtd [-j TAR] write SRC LABEL
# Last positional arg is the label; the source is the word after "write".
# \${!#} is a bashism and expands to nothing under dash, which silently emptied the label
# earlier - take the last argument in a loop instead.
label=""
for a in "\$@"; do label="\$a"; done
src=""
prev=""
for a in "\$@"; do
	if [ "\$prev" = "write" ]; then src="\$a"; break; fi
	prev="\$a"
done
echo "\$label \$(sha256sum "\$src" | cut -d' ' -f1)" >> "\$MTD_LOG"
cp -f "\$src" "$W/written/\$label"
exit 0
EOF
chmod +x "$W/bin/mtd"

echo "=== 0. sanity gate: the artifact exists and is the uncompressed tar we think ==="
if [ ! -f "$IMG" ]; then echo "  MISSING $IMG - nothing below is meaningful"; exit 1; fi
printf '  %s  %s bytes\n' "$IMG" "$(wc -c <"$IMG")"
printf '  first 2 bytes: %s (7379 = plain tar, 1f8b = gzip)\n' \
	"$(head -c 2 "$IMG" | od -An -tx1 | tr -d ' \n')"
printf '  members: %s\n' "$(tar tf "$IMG" | tr '\n' ' ')"

echo
echo "=== 1. run the real upgrade path, capturing the bytes handed to mtd ==="
# no /tmp/sysupgrade.tgz here: this run is about partition contents, and an absent archive
# must not change which bytes go where. test_upgrade_backup.sh covers the archive.
rm -f /tmp/sysupgrade.tgz
: >"$W/mtd.log"
MTD_LOG="$W/mtd.log" PATH="$W/bin:$PATH" sh -c '
	. '"$W"'/platform.sh
	platform_do_upgrade "$1"
	echo "rc=$?"' sh "$IMG" >"$W/out" 2>&1
echo "  $(grep -o 'rc=[0-9]*' "$W/out" | tail -1)"
sed 's/^/    /' "$W/mtd.log"

echo
echo "=== 2. every expected partition was written, and nothing else ==="
labels=$(cut -d' ' -f1 "$W/mtd.log" | sort | tr '\n' ' ')
if [ "$labels" = "dtb kernel root " ]; then
	ok "exactly root, dtb, kernel were written" "$labels"
else
	bad "exactly root, dtb, kernel were written" "got: $labels"
fi

echo
echo "=== 3. each partition got the member of the same name, byte for byte ==="
for lbl in root dtb kernel; do
	if [ ! -f "$W/written/$lbl" ]; then bad "member $lbl" "never written"; continue; fi
	got=$(sha "$W/written/$lbl")
	# pull the same-named member straight out of the image and hash it independently
	member=$(tar tf "$IMG" | grep -m1 "/$lbl\$")
	rm -rf "$W/x"; mkdir -p "$W/x"
	tar xf "$IMG" -C "$W/x" "$member" 2>/dev/null
	want=$(sha "$W/x/$member")
	cmp_sha "mtd wrote the $lbl member verbatim" "$want" "$got"
done

echo
echo "=== 4. the three blobs are structurally what their partitions need ==="
# uImage magic 0x27051956, big endian on disk
k=$(head -c 4 "$W/written/kernel" 2>/dev/null | od -An -tx1 | tr -d ' \n')
if [ "$k" = "27051956" ]; then ok "kernel is a uImage" "magic $k"; else bad "kernel is a uImage" "magic ${k:-<none>}"; fi

# FDT magic 0xd00dfeed, big endian on disk
d=$(head -c 4 "$W/written/dtb" 2>/dev/null | od -An -tx1 | tr -d ' \n')
if [ "$d" = "d00dfeed" ]; then ok "dtb is a flattened device tree" "magic $d"; else bad "dtb is a flattened device tree" "magic ${d:-<none>}"; fi

# JFFS2 magic is 0x1985, stored little endian
if [ -f "$W/written/root" ]; then
	j=$(head -c 2 "$W/written/root" | od -An -tx1 | tr -d ' \n')
	if [ "$j" = "8519" ] || [ "$j" = "1985" ]; then ok "root is a jffs2 image" "magic $j"; else bad "root is a jffs2 image" "magic $j"; fi
fi

echo
echo "=== 5. do these match the standalone flash-set files that go in by hand? ==="
# dtb and root are compared whole - they have no header to re-wrap.
for pair in "dtb staging/trial/flash-dtb.bin" "root staging/trial/flash-rootfs.jffs2"; do
	lbl=${pair%% *}; f=${pair#* }
	if [ ! -f "$f" ]; then printf '  ....  %-48s not staged\n' "$lbl vs $(basename "$f")"; continue; fi
	cmp_sha "$lbl == staged $(basename "$f")" "$(sha "$f")" "$(sha "$W/written/$lbl")"
done

# The kernel is deliberately NOT compared whole. flash-uImage and the tar's kernel member
# are the same kernel wrapped twice: same size, same load/ep, same dcrc 0x456019e7, and
# `cmp -i 64:64` reports the payloads identical - only the 64-byte uImage header differs
# (name "OpenWrt mainline 6.18.38 sk-d840" vs "ARM64 ImmortalWrt Linux-6.18.38", and a
# different build timestamp). U-Boot does not read the name field, so the payloads are
# what decide what runs. Compare the payload.
payload_sha() { tail -c +65 "$1" | sha256sum | cut -d' ' -f1; }
kp=$(payload_sha "$W/written/kernel"); ku=$(payload_sha staging/trial/flash-uImage)
cmp_sha "kernel payload == staged flash-uImage payload" "$ku" "$kp"
if [ "$(sha staging/trial/flash-uImage)" != "$(sha "$W/written/kernel")" ]; then
	printf '  note  whole-file sha differs by design (uImage name+timestamp only)\n'
fi

echo
echo "=== 6. negative control: step 3 must be able to FAIL ==="
# Swap the members inside the tar and step 3 would still pass, because it compares what
# mtd was handed against the tar's same-named member - both move together. So break the
# thing that can actually go wrong instead: the platform script writing the wrong member
# to a partition. That is what step 3 exists to catch, so prove it catches it.
sed 's#mtd write "$sub/dtb" dtb#mtd write "$sub/kernel" dtb#' "$W/platform.sh" \
	>"$W/platform-broken.sh"
if cmp -s "$W/platform.sh" "$W/platform-broken.sh"; then
	bad "the mutation actually changed platform.sh" "sed did not match - control is void"
else
	ok "the mutation actually changed platform.sh" "dtb write now targets the kernel member"
fi

rm -rf "$W/written"; mkdir -p "$W/written"; : >"$W/mtd.log"
MTD_LOG="$W/mtd.log" PATH="$W/bin:$PATH" sh -c '
	. '"$W"'/platform-broken.sh
	platform_do_upgrade "$1"' sh "$IMG" >/dev/null 2>&1
mem_dtb=$(tar tf "$IMG" | grep -m1 '/dtb$')
rm -rf "$W/z"; mkdir -p "$W/z"; tar xf "$IMG" -C "$W/z" "$mem_dtb"
if [ "$(sha "$W/z/$mem_dtb")" = "$(sha "$W/written/dtb")" ]; then
	bad "a mis-targeted write makes step 3 fail" "step 3 passed on wrong bytes - it measures nothing"
else
	ok "a mis-targeted write makes step 3 fail" "test is live"
fi

echo
echo "=== pass=$pass fail=$fail ==="
rm -rf "$W"
[ "$fail" -eq 0 ] || exit 1
exit 0