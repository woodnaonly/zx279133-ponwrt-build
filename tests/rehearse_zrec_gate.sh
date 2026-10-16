#!/bin/sh
# 本地排演套件（local rehearsal suite）：只在开发机上跑，**没有**接进 CI。
# 它跟别的套件不一样，不需要 staging/ 里的真实产物——它需要的只是 PyYAML，
# 加上一个由本脚本自己造的假构建树；真产物要 74 分钟才存在。
#
# Rehearse "Gate: the recovery service reached the image" against a fake build
# tree holding a synthetic package archive. The step's whole job is to read one
# archive, so each shape it has to cope with is built here and checked both
# ways; the alternative is a 74-minute build per attempt.
#
# Run from anywhere:  sh tests/rehearse_zrec_gate.sh
set -u
cd "$(dirname "$0")/.."
G=$PWD/gate-rehearse
rm -rf "$G" && mkdir -p "$G"

# The interpreter has to be the one that actually imports PyYAML. `python3` is a
# broken stub on this PC (it exits 49 printing nothing), and a script that dies
# silently looks like a workflow with no gate step.
PY=""
for c in ${PY_CANDIDATES:-} python3 python /c/Python313/python.exe C:/Python313/python.exe; do
	"$c" -c 'import yaml' >/dev/null 2>&1 && { PY=$c; break; }
done
if [ -z "$PY" ]; then echo "  cannot find a python with PyYAML - refusing to report"; exit 1; fi

# --- the step body, extracted from the workflow, ${{ matrix.mode }} left as $MODE
"$PY" - "$G" <<'PY'
import io, sys, yaml
out = sys.argv[1]
d = yaml.safe_load(io.open('.github/workflows/build-sk-d840n.yml', encoding='utf-8', newline=''))
steps = d['jobs']['build']['steps']
s = [x for x in steps if x.get('name') == 'Gate: the recovery service reached the image']
assert len(s) == 1, [x.get('name') for x in steps]
body = s[0]['run'].replace('${{ matrix.mode }}', '$MODE')
io.open(out + '/gate.sh', 'w', encoding='utf-8', newline='\n').write('MODE="$1"\n' + body)
print("extracted %d lines of step body" % body.count('\n'))
PY
GATE=$G/gate.sh

mk_tree() { # case-name ext -> prints the tree root; a package with all seven files
	local name=$1 ext=${2:-apk}
	local D=$G/$name
	mkdir -p "$D/data/usr/sbin" "$D/data/usr/libexec" "$D/data/lib/preinit" \
		"$D/data/etc/rc.d" "$D/data/www/zrec" \
		"$D/openwrt/bin/packages/aarch64_core/base" "$D/openwrt/bin/targets/zte/zx279133" \
		"$D/openwrt/build_dir/target-aarch64_glibc"
	printf '#!/bin/sh\necho ops\n'        > "$D/data/usr/libexec/zrec-ops"
	printf '#!/bin/sh\necho bringup\n'    > "$D/data/usr/libexec/zrec-bringup"
	printf '#!/bin/sh\necho run\n'        > "$D/data/usr/sbin/zrec-run"
	printf '#!/bin/sh\nexit 0\n'          > "$D/data/etc/rc.d/S21zrec"
	printf 'zrec_recovery() { sh /usr/libexec/zrec-bringup; }\nboot_hook_add preinit_main zrec_recovery\nboot_hook_add failsafe zrec_recovery\n' \
		> "$D/data/lib/preinit/85_zrec"
	printf '<html>\xe6\x81\xa2\xe5\xa4\x8d</html>\n' > "$D/data/www/zrec/index.html"
	# the shipped binary is what the page path is compiled into, so the stand-in has
	# to carry the string the gate greps for. It starts with a shebang because this
	# PC's chmod only honours +x on files Git Bash considers executable (a binary
	# full of NULs stays 644 no matter what you chmod), and the exec bit is exactly
	# what case 3 has to be able to strip.
	printf '#!/bin/sh\n# ELF stand-in\n/www/zrec/index.html /usr/libexec/zrec-ops\n' > "$D/data/usr/sbin/zrec"
	chmod 755 "$D/data/usr/sbin/zrec" "$D/data/usr/libexec/zrec-ops" \
		"$D/data/usr/libexec/zrec-bringup" "$D/data/usr/sbin/zrec-run" "$D/data/etc/rc.d/S21zrec"
	chmod 644 "$D/data/lib/preinit/85_zrec" "$D/data/www/zrec/index.html"
	pack "$D" "$ext"
	{ echo "zte_zx279133"; echo "zrec - 1.0"; echo "base-files - 29-r30000"; } \
		> "$D/openwrt/bin/targets/zte/zx279133/manifest"
	echo "$D"
}

pack() { # tree-root ext -> rebuild data.tar.gz and the outer archive from what is on disk
	local D=$1 ext=${2:-apk}
	local P=$D/openwrt/bin/packages/aarch64_core/base/zrec_1.0_aarch64_core.$ext
	rm -f "$P" "$D/data.tar.gz"
	( cd "$D/data" && tar -czf "$D/data.tar.gz" . )
	printf 'pkgname = zrec\npkgver = 1.0-r1\narch = aarch64_core\n' > "$D/.PKGINFO"
	( cd "$D" && tar -czf "$P" .PKGINFO data.tar.gz )
	rm -f "$D/.PKGINFO" "$D/data.tar.gz"
}

pass=0; fail=0
chk() { # want-rc label actual-rc
	if [ "$3" = "$1" ]; then pass=$((pass+1)); printf '  PASS  %-56s rc=%s\n' "$2" "$3"
	else fail=$((fail+1)); printf '  FAIL  %-56s rc=%s want=%s\n' "$2" "$3" "$1"; fi
}
gate() { ( cd "$1" && sh "$GATE" "$2" ); }

echo "=== 1. positive ==="
A=$(mk_tree pos apk);   gate "$A" initramfs > "$A/log" 2>&1; chk 0 "well-formed .apk, initramfs mode" $?
grep -q 'recovery service is packaged for the RAM image' "$A/log" \
	&& echo "        and it said so" || echo "        (did not reach its final line)"

echo "=== 2. the extension is not assumed ==="
B=$(mk_tree ipk ipk);   gate "$B" initramfs > /dev/null 2>&1; chk 0 "the same package named .ipk is found too" $?

echo "=== 2b. member names without the ./ prefix, which is the other shape tar can write ==="
N=$(mk_tree noprefix apk)
P=$N/openwrt/bin/packages/aarch64_core/base/zrec_1.0_aarch64_core.apk
printf 'pkgname = zrec\n' > "$N/.PKGINFO"
( cd "$N/data" && tar -czf "$N/data.tar.gz" usr lib www etc )
( cd "$N" && rm -f "$P" && tar -czf "$P" .PKGINFO data.tar.gz )
rm -f "$N/.PKGINFO" "$N/data.tar.gz"
gate "$N" initramfs > "$N/log" 2>&1; chk 0 "names listed without a ./ prefix are still matched" $?

echo "=== 3. negative controls, each one a defect the gate must catch ==="
C=$(mk_tree nopage apk); rm "$C/data/www/zrec/index.html"; pack "$C" apk
gate "$C" initramfs > "$C/log" 2>&1; chk 1 "a missing page is refused" $?
grep -q 'www/zrec/index.html is not in' "$C/log" && echo "        named: $(grep -a -m1 '::error' "$C/log")"

D=$(mk_tree noexecd apk); chmod 644 "$D/data/usr/libexec/zrec-ops"
if [ -x "$D/data/usr/libexec/zrec-ops" ]; then
	# Not a passing check, and not a failing one: this PC's chmod cannot clear the
	# exec bit on a file it considers executable, so the defect this case exists to
	# provoke cannot be produced here. Reported as NOT RUN rather than quietly green -
	# a control that never fires and a control that passes look identical otherwise.
	printf '  ....  %-56s %s\n' "exec-bit control needs a Linux runner" "chmod 644 was a no-op here"
else
	pack "$D" apk
	gate "$D" initramfs > "$D/log" 2>&1; chk 1 "a lost exec bit is refused" $?
	grep -q 'not executable in the package' "$D/log" && echo "        named: $(grep -a -m1 '::error' "$D/log")"
fi

E=$(mk_tree noreg apk); printf 'zrec_recovery() { true; }\n' > "$E/data/lib/preinit/85_zrec"; pack "$E" apk
gate "$E" initramfs > "$E/log" 2>&1; chk 1 "a preinit file that registers no hook is refused" $?

F=$(mk_tree badpath apk); mv "$F/data/www/zrec/index.html" "$F/data/www/zrec/page.html"; pack "$F" apk
gate "$F" initramfs > "$F/log" 2>&1; chk 1 "the page at the wrong install path is refused" $?

echo "=== 4. the guessed-subject mistakes must not become failures ==="
H=$(mk_tree nopkg apk); rm -f "$H/openwrt/bin/packages/aarch64_core/base/zrec_1.0_aarch64_core.apk"
gate "$H" initramfs > "$H/log" 2>&1; chk 1 "no package at all still refuses, and says so" $?
grep -q 'produced no package' "$H/log" && echo "        named: $(grep -a -m1 '::error' "$H/log")"

I=$(mk_tree flashclean apk); rm -f "$I/openwrt/bin/packages/aarch64_core/base/"zrec_* "$I/openwrt/bin/targets/zte/zx279133/manifest"
gate "$I" flash > "$I/log" 2>&1; chk 0 "flash mode with no zrec package passes" $?
J=$(mk_tree flashdirty apk); gate "$J" flash > "$J/log" 2>&1; chk 1 "flash mode that built zrec refuses" $?

echo "=== 5. a manifest that is absent is a warning, not a failure ==="
K=$(mk_tree nomanifest apk); rm -f "$K/openwrt/bin/targets/zte/zx279133/"*manifest*
gate "$K" initramfs > "$K/log" 2>&1; chk 0 "no manifest found does not sink the gate" $?
grep -q 'no target manifest' "$K/log" && echo "        and it warned instead"

echo
echo "=== what the positive run reported ==="
sed -n '1,20p' "$A/log"
echo
echo "pass=$pass fail=$fail"
[ "$fail" -eq 0 ]
