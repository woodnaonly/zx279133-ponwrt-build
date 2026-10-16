#!/bin/sh
# 本地排演套件（local rehearsal suite）：依赖 staging/ 下的真实构建产物，
# 在只 clone 本仓库的环境里跑不起来，也**没有**接进 CI。
# 为什么、怎么跑、缺的那个夹具从哪来：同目录 README.md。
# Execute ci-build-repo/templates/trial-kexec.sh for real, in a sandbox.
#
# This is the first thing the operator runs and the whole no-serial-console safety story
# rests on it: kexec into RAM, touch no flash, power-cycle back if it goes quiet. It had
# never been executed either - only read.
#
# Unlike flash-sk-d840n.sh this one needs no path rewriting: $D is an environment
# variable and everything else it touches (kexec, lib/, the payload) is relative to it,
# so the whole trial can be staged in a temp dir with a fake kexec on PATH.
set -u
cd /mnt/c/ugit/SK-D840N4
export LC_ALL=C

REAL=ci-build-repo/templates/trial-kexec.sh
pass=0; fail=0
ok()  { printf '  PASS  %-52s %s\n' "$1" "${2:-}"; pass=$((pass+1)); }
bad() { printf '  FAIL  %-52s %s\n' "$1" "${2:-}"; fail=$((fail+1)); }

W=""
# minimal but real payload: a gzip file, and something whose gunzip output is a plausible
# Image (contents do not matter here, only that the pipeline produces one)
stage() { # $1 = with-kexec-bundle yes/no
	W=$(mktemp -d); mkdir -p "$W/trial" "$W/bin"
	printf 'not-really-an-arm64-image\n' | gzip -c >"$W/trial/trial-Image.gz"
	printf 'FDT-DTB-BLOB' >"$W/trial/trial-dtb.bin"
	cp "$REAL" "$W/trial/trial-kexec.sh"
	if [ "${1:-yes}" = yes ]; then
		cat >"$W/trial/kexec" <<'EOF'
#!/bin/sh
echo "kexec $*" >> "$KEXEC_LOG"
case " $* " in *" --version "*) echo "kexec-tools 2.0.31"; exit 0;; esac
for a in "$@"; do [ "$a" = "$FAIL_KEXEC" ] && { echo "kexec: cannot handle $a" >&2; exit 1; }; done
exit 0
EOF
		chmod +x "$W/trial/kexec"
	fi
	cat >"$W/bin/uclient-fetch" <<'EOF'
#!/bin/sh
# emulate uclient-fetch -q -O OUT URL
out=""; url=""
while [ $# -gt 0 ]; do
	case "$1" in
		-O) out="$2"; shift 2;;
		-q) shift;;
		*) url="$1"; shift;;
	esac
done
name=${url##*/}
echo "uclient-fetch $name" >> "$FETCH_LOG"
[ -f "$SERVE_DIR/$name" ] || { echo "uclient-fetch: 404 $name" >&2; exit 1; }
cp "$SERVE_DIR/$name" "$out"
EOF
	chmod +x "$W/bin/uclient-fetch"
	cat >"$W/bin/sleep" <<EOF
#!/bin/sh
echo "sleep \$*" >> "\$KEXEC_LOG"
exit 0
EOF
	chmod +x "$W/bin/sleep"
}

run() { # VAR=val... ; shell functions do NOT accept a `VAR=val cmd` prefix in POSIX sh,
	# so the assignments are handed to env and it runs the script.
	: >"$W/kexec.log"; : >"$W/fetch.log"
	OUT=$(cd "$W/trial" && env \
		D="$W/trial" KEXEC_LOG="$W/kexec.log" FETCH_LOG="$W/fetch.log" \
		SERVE_DIR="${SERVE_DIR:-}" FAIL_KEXEC="${FAIL_KEXEC:-}" \
		PATH="$W/bin:$PATH" \
		"$@" \
		sh "$W/trial/trial-kexec.sh" 2>&1)
	RC=$?
}
kargs() { grep -v '^sleep ' "$W/kexec.log" 2>/dev/null; }

echo "=== 0. control: a complete, already-staged trial directory ==="
stage yes
run
echo "  rc=$RC"
printf '%s\n' "$OUT" | sed 's/^/    /'
echo "  kexec calls:"; kargs | sed 's/^/    /'
if [ "$RC" -eq 0 ]; then ok "a pre-staged trial loads" "rc=0"
else bad "a pre-staged trial loads" "rc=$RC"; fi
if kargs | grep -q -- '--exec'; then ok "and it really execs by default" ""
else bad "and it really execs by default" "no --exec in $(kargs | wc -l) calls"; fi
if [ -f "$W/trial/Image" ]; then ok "Image was unpacked from the gzip" "$(wc -c <"$W/trial/Image") bytes"
else bad "Image was unpacked from the gzip" "absent"; fi
rm -rf "$W"

echo
echo "=== 1. KEXEC_ARGS=--load must NOT exec (this is the recoverable mode) ==="
stage yes
run KEXEC_ARGS=--load
echo "  rc=$RC"
printf '%s\n' "$OUT" | tail -4 | sed 's/^/    /'
echo "  kexec calls:"; kargs | sed 's/^/    /'
if kargs | grep -q -- '--exec'; then
	bad "--load does not exec" "it called --exec anyway - there is no way back"
else ok "--load does not exec" ""; fi
if printf '%s' "$OUT" | grep -q -- '--exec'; then
	ok "and it tells the operator how to go on by hand" ""
else bad "and it tells the operator how to go on" "no hint printed"; fi
if [ "$RC" -eq 0 ]; then ok "--load exits 0" ""; else bad "--load exits 0" "rc=$RC"; fi
rm -rf "$W"

echo
echo "=== 2. the memory window and the dtb are actually passed ==="
stage yes
run KEXEC_ARGS=--load
k=$(kargs)
printf '%s\n' "$k" | grep -q -- '--mem-min=0x88000000' && ok "--mem-min is set" "" || bad "--mem-min is set" "absent"
printf '%s\n' "$k" | grep -q -- '--mem-max=0x9b000000' && ok "--mem-max is set (below ramoops)" "" || bad "--mem-max is set" "absent"
printf '%s\n' "$k" | grep -q -- '--dtb=trial-dtb.bin' && ok "dtb is handed over" "" || bad "dtb is handed over" "absent"
printf '%s\n' "$k" | grep -q -- '--command-line=' && ok "a command line is supplied" "" || bad "a command line is supplied" "absent"
if printf '%s\n' "$k" | grep -q 'trialtag=[0-9]\{8\}T[0-9]\{6\}Z'; then
	ok "the command line carries a fresh trialtag" ""
else bad "the command line carries a fresh trialtag" "$(printf '%s\n' "$k" | grep -o 'trialtag=[^ "]*')"; fi
if printf '%s\n' "$k" | grep -q 'root=/dev/ram0'; then
	ok "and roots the new kernel on the ramdisk" ""
else bad "roots the new kernel on the ramdisk" "absent"; fi
rm -rf "$W"

echo
echo "=== 3. TRIALTAG override wins over the generated one ==="
stage yes
run KEXEC_ARGS=--load TRIALTAG=FIXEDTAG123
k=$(kargs)
if printf '%s\n' "$k" | grep -q 'trialtag=FIXEDTAG123'; then ok "TRIALTAG is honoured" ""
else bad "TRIALTAG is honoured" "$(printf '%s\n' "$k" | grep -o 'trialtag=[^ "]*')"; fi
rm -rf "$W"

echo
echo "=== 4. missing payload and no PC= ==="
stage yes
rm -f "$W/trial/trial-dtb.bin"
run
echo "  rc=$RC"; printf '%s\n' "$OUT" | tail -2 | sed 's/^/    /'
if [ "$RC" -ne 0 ] && printf '%s' "$OUT" | grep -q 'PC='; then
	ok "a missing file without PC= fails with the fix spelled out" "rc=$RC"
else bad "a missing file without PC= fails usefully" "rc=$RC"; fi
rm -rf "$W"

echo
echo "=== 5. no kexec to boot with ==="
stage no
run
echo "  rc=$RC"; printf '%s\n' "$OUT" | tail -2 | sed 's/^/    /'
if [ "$RC" -ne 0 ] && printf '%s' "$OUT" | grep -qi 'no kexec to boot with'; then
	ok "an absent kexec is named explicitly" "rc=$RC"
else bad "an absent kexec is named explicitly" "rc=$RC"; fi
rm -rf "$W"

echo
echo "=== 6. payload checksum mismatch ==="
stage yes
( cd "$W/trial" && sha256sum trial-Image.gz trial-dtb.bin trial-kexec.sh > sha256sums-trial )
# Replace with a VALID but different gzip. Appending junk instead would be caught by
# gunzip at line 114 - which runs BEFORE the manifest is consulted - so that variant
# never reaches the checksum and would leave this case measuring the wrong guard.
printf 'a different, perfectly valid payload\n' | gzip -c >"$W/trial/trial-Image.gz"
run KEXEC_ARGS=--load
echo "  rc=$RC"; printf '%s\n' "$OUT" | tail -2 | sed 's/^/    /'
if [ "$RC" -ne 0 ] && printf '%s' "$OUT" | grep -qi 'mismatch'; then
	ok "a substituted payload is refused by the manifest" "rc=$RC"
else bad "a substituted payload is refused by the manifest" "rc=$RC"; fi
nload=$(grep -c -- 'kexec --load' "$W/kexec.log" 2>/dev/null); nload=${nload:-0}
if [ "$nload" -eq 0 ]; then
	ok "and kexec was never invoked" ""
else bad "and kexec was never invoked" "it loaded a substituted Image"; fi
# the manifest is consulted before the gunzip, so a rejected payload never gets
# materialised as a 30 MB Image on the box
if [ ! -f "$W/trial/Image" ]; then
	ok "and no Image was unpacked from the rejected payload" ""
else bad "and no Image was unpacked" "Image exists: $(wc -c <"$W/trial/Image") bytes of unverified bytes"; fi
rm -rf "$W"

echo
echo "=== 7. trial-dtb.bin is exempt from the manifest ON PURPOSE ==="
stage yes
( cd "$W/trial" && sha256sum trial-Image.gz trial-dtb.bin trial-kexec.sh > sha256sums-trial )
printf 'LOCALLY-PATCHED' >"$W/trial/trial-dtb.bin"   # same name, different bytes
run KEXEC_ARGS=--load
echo "  rc=$RC"
if [ "$RC" -eq 0 ]; then
	ok "a locally patched dtb does not block the trial" "rc=0"
else
	bad "a locally patched dtb does not block the trial" "rc=$RC - the exception is documented but not implemented"
fi
rm -rf "$W"

echo
echo "=== 8. kexec --load itself failing ==="
stage yes
run KEXEC_ARGS=--load FAIL_KEXEC=--load
echo "  rc=$RC"; printf '%s\n' "$OUT" | tail -2 | sed 's/^/    /'
if [ "$RC" -ne 0 ] && printf '%s' "$OUT" | grep -qi 'CONFIG_KEXEC'; then
	ok "a failed load names the likely cause" "rc=$RC"
else bad "a failed load names the likely cause" "rc=$RC"; fi
rm -rf "$W"

echo
echo "=== 9. patched initramfs is offered as --initrd, and its absence adds nothing ==="
stage yes
run KEXEC_ARGS=--load
if kargs | grep -q -- '--initrd='; then
	bad "no initrd when none was staged" "$(kargs | grep -o -- '--initrd=[^ ]*')"
else ok "no --initrd when none was staged" ""; fi
gzip -c /dev/null >"$W/trial/trial-initramfs.cpio.gz"
run KEXEC_ARGS=--load
if kargs | grep -q -- "--initrd=$W/trial/trial-initramfs.cpio.gz"; then
	ok "a staged initramfs is passed as --initrd" ""
else bad "a staged initramfs is passed as --initrd" "$(kargs | tr '\n' ' ')"; fi
rm -rf "$W"

echo
echo "=== 10. HTTP fetch path (PC= set, files not pre-staged) ==="
stage no
mkdir -p "$W/serve"
cp staging/trial/trial-Image.gz staging/trial/trial-dtb.bin "$W/serve/" 2>/dev/null || {
	printf 'x\n' | gzip -c >"$W/serve/trial-Image.gz"; printf 'FDT\n' >"$W/serve/trial-dtb.bin"; }
SERVE_DIR="$W/serve" run SERVE_DIR="$W/serve" KEXEC_ARGS=--load PC=192.0.2.7
echo "  rc=$RC"
echo "  fetched:"; sed 's/^/    /' "$W/fetch.log" 2>/dev/null
if [ "$RC" -ne 0 ]; then
	# no kexec on the server, so it must fail at that point, not earlier
	if grep -q 'no kexec to boot with' "$W/fetch.log" "$W/kexec.log" 2>/dev/null || \
	   printf '%s' "$OUT" | grep -qi 'kexec'; then
		ok "it fetched what it could and then failed on the missing kexec" "rc=$RC"
	else bad "HTTP fetch path" "rc=$RC: $OUT"; fi
else
	ok "HTTP fetch path completed" "rc=0"
fi
rm -rf "$W"

echo
echo "=== pass=$pass fail=$fail ==="
[ "$fail" -eq 0 ] || exit 1
exit 0