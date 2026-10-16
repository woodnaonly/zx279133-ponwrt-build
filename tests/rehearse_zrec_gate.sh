#!/bin/sh
# 本地排演套件（local rehearsal suite）：只在开发机上跑，**没有**接进 CI。
# 它跟别的套件不一样，不需要 staging/ 里的真实产物——它需要的只是 PyYAML，
# 加上一个由本脚本自己造的假构建树；真产物要 74 分钟才存在。
#
# Rehearse "Gate: the recovery service reached the image". The gate's whole job
# is to look at one directory and one file list, so every shape it has to cope
# with is built here and checked both ways; the alternative is a 74-minute build
# per attempt. The subject is the packer's input tree (build_dir/target-*/zrec-*/
# ipkg-*/zrec) plus the lib/apk/packages/zrec*.list the packer writes into it,
# because the .apk that gets produced is an "ADBd" container that no tool on the
# runner can open - see the comment on the step itself.
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

want="usr/sbin/zrec usr/sbin/zrec-run usr/libexec/zrec-ops usr/libexec/zrec-bringup lib/preinit/85_zrec etc/rc.d/S21zrec www/zrec/index.html"

mk_tree() { # case-name -> prints the tree root
	local name=$1
	local D=$G/$name
	local I=$D/openwrt/build_dir/target-aarch64_cortex_a53/zrec-1/ipkg-aarch64_cortex-a53/zrec
	mkdir -p "$I/usr/sbin" "$I/usr/libexec" "$I/lib/preinit" "$I/etc/rc.d" "$I/www/zrec" \
		"$I/lib/apk/packages" \
		"$D/openwrt/build_dir/target-aarch64_cortex_a53/base-files-1/ipkg-aarch64_cortex-a53/base-files/etc" \
		"$D/openwrt/bin/packages/aarch64_cortex-a53/base" "$D/openwrt/bin/targets/zte/zx279133"
	printf '#!/bin/sh\necho ops\n'    > "$I/usr/libexec/zrec-ops"
	printf '#!/bin/sh\necho bringup\n'> "$I/usr/libexec/zrec-bringup"
	printf '#!/bin/sh\necho run\n'    > "$I/usr/sbin/zrec-run"
	printf '#!/bin/sh\nexit 0\n'      > "$I/etc/rc.d/S21zrec"
	printf 'zrec_recovery() { sh /usr/libexec/zrec-bringup; }\nboot_hook_add preinit_main zrec_recovery\nboot_hook_add failsafe zrec_recovery\n' \
		> "$I/lib/preinit/85_zrec"
	printf '<html>\xe6\x81\xa2\xe5\xa4\x8d</html>\n' > "$I/www/zrec/index.html"
	# A stand-in for the cross-compiled binary. It starts with a shebang because this
	# PC's chmod only honours +x on files Git Bash treats as executable, and the exec
	# bit is one of the things this gate is for.
	printf '#!/bin/sh\n# aarch64 zrec, stripped\n/www/zrec/index.html /usr/libexec/zrec-ops\n' > "$I/usr/sbin/zrec"
	chmod 755 "$I/usr/sbin/zrec" "$I/usr/libexec/zrec-ops" "$I/usr/libexec/zrec-bringup" \
		"$I/usr/sbin/zrec-run" "$I/etc/rc.d/S21zrec"
	chmod 644 "$I/lib/preinit/85_zrec" "$I/www/zrec/index.html"
	# The decoy proves the locator selects the tree by content: base-files has an
	# ipkg-* directory too, and must not be mistaken for zrec's.
	printf 'netifd\n' > "$D/openwrt/build_dir/target-aarch64_cortex_a53/base-files-1/ipkg-aarch64_cortex-a53/base-files/etc/hostname"
	write_list "$D" "$I"
	# an opaque "ADBd" archive, exactly like the real one: present, never parsed
	printf 'ADBd\000\001\002\003garbage-not-a-tar\n' > "$D/openwrt/bin/packages/aarch64_cortex-a53/base/zrec-1-r1.apk"
	{ echo "zte_zx279133"; echo "zrec - 1-r1"; echo "base-files - 1-r1"; } \
		> "$D/openwrt/bin/targets/zte/zx279133/manifest"
	echo "$D"
}

write_list() { # tree-root idir -> regenerate the packer's own ownership list from disk
	local I=$2
	( cd "$I" && find . -type f,l -printf '/%P\n' 2>/dev/null | sort ) > "$I/lib/apk/packages/zrec.list"
}

pass=0; fail=0
chk() { # want-rc label actual-rc
	if [ "$3" = "$1" ]; then pass=$((pass+1)); printf '  PASS  %-56s rc=%s\n' "$2" "$3"
	else fail=$((fail+1)); printf '  FAIL  %-56s rc=%s want=%s\n' "$2" "$3" "$1"; fi
}
gate() { ( cd "$1" && sh "$GATE" "$2" ); }

echo "=== 1. positive: the packer's tree, its list, the archive, the manifest ==="
A=$(mk_tree pos); gate "$A" initramfs > "$A/log" 2>&1; chk 0 "everything in place passes" $?
grep -q 'recovery service is staged, listed and packaged' "$A/log" \
	&& echo "        and it said so" || echo "        (did not reach its final line)"
grep -q 'packed tree: openwrt/build_dir/target-aarch64_cortex_a53/zrec-1/ipkg-aarch64_cortex-a53/zrec' "$A/log" \
	&& echo "        located zrec's tree, not base-files' " || echo "        (locator line: $(head -1 "$A/log"))"

echo "=== 2. the four defects the gate exists to catch ==="
B=$(mk_tree notinlist); rm "$B/openwrt/build_dir/target-aarch64_cortex_a53/zrec-1/ipkg-aarch64_cortex-a53/zrec/www/zrec/index.html"
write_list "$B" "$B/openwrt/build_dir/target-aarch64_cortex_a53/zrec-1/ipkg-aarch64_cortex-a53/zrec"
gate "$B" initramfs > "$B/log" 2>&1; chk 1 "a wanted file that is simply absent is refused" $?

C=$(mk_tree listedonly)
Ci=$C/openwrt/build_dir/target-aarch64_cortex_a53/zrec-1/ipkg-aarch64_cortex-a53/zrec
rm "$Ci/usr/libexec/zrec-bringup"          # delete the file, leave the list claiming it
gate "$C" initramfs > "$C/log" 2>&1; chk 1 "a file the list claims but disk does not have is refused" $?

D=$(mk_tree notlisted); Dd=$D/openwrt/build_dir/target-aarch64_cortex_a53/zrec-1/ipkg-aarch64_cortex-a53/zrec
mv "$Dd/www/zrec/index.html" "$Dd/www/zrec/page.html"; write_list "$D" "$Dd"
gate "$D" initramfs > "$D/log" 2>&1; chk 1 "the page at the wrong install path is refused" $?
grep -q 'is not in the tree that was packed' "$D/log" && echo "        named: $(grep -a -m1 '::error' "$D/log")"

E=$(mk_tree noreg); printf 'zrec_recovery() { true; }\n' > "$E/openwrt/build_dir/target-aarch64_cortex_a53/zrec-1/ipkg-aarch64_cortex-a53/zrec/lib/preinit/85_zrec"
gate "$E" initramfs > "$E/log" 2>&1; chk 1 "a preinit file that registers no hook is refused" $?

F=$(mk_tree nolist); rm "$F/openwrt/build_dir/target-aarch64_cortex_a53/zrec-1/ipkg-aarch64_cortex-a53/zrec/lib/apk/packages/zrec.list"
gate "$F" initramfs > "$F/log" 2>&1; chk 1 "a package that owns nothing on record is refused" $?

# The exec-bit control has to be produced from the other direction on this PC:
# chmod 644 cannot clear +x from a file Git Bash thinks is executable, but chmod 755
# is equally refused for one it does not - so writing the file without a shebang
# leaves it 644, which is the defect. If the platform ever changes its mind, the
# control reports itself NOT RUN instead of quietly passing.
N=$(mk_tree noexecbit)
Ni=$N/openwrt/build_dir/target-aarch64_cortex_a53/zrec-1/ipkg-aarch64_cortex-a53/zrec
printf 'ELF\000\001not-a-script-no-shebang\n' > "$Ni/usr/libexec/zrec-ops"
chmod 755 "$Ni/usr/libexec/zrec-ops" 2>/dev/null
if [ -x "$Ni/usr/libexec/zrec-ops" ]; then
	printf '  ....  %-56s %s\n' "exec-bit control needs a Linux runner" "cannot make this file 644 here"
else
	gate "$N" initramfs > "$N/log" 2>&1; chk 1 "a lost exec bit is refused" $?
	grep -q 'lost its exec bit' "$N/log" && echo "        named: $(grep -a -m1 '::error' "$N/log")"
fi

echo "=== 3. the guessed-subject mistakes must not turn into a false green or a false red ==="
H=$(mk_tree nopackedtree)
rm -rf "$H/openwrt/build_dir/target-aarch64_cortex_a53/zrec-1"
gate "$H" initramfs > "$H/log" 2>&1; chk 1 "an archive with no staged tree still refuses" $?
grep -q 'was never packed into the RAM image' "$H/log" && echo "        named: $(grep -a -m1 '::error' "$H/log")"

I=$(mk_tree nomanifest); rm "$I/openwrt/bin/targets/zte/zx279133/"*manifest*
gate "$I" initramfs > "$I/log" 2>&1; chk 0 "a missing manifest warns instead of sinking the gate" $?
grep -q 'no target manifest' "$I/log" && echo "        and it warned instead"

J=$(mk_tree noarchive); rm "$J/openwrt/bin/packages/aarch64_cortex-a53/base/"zrec*
gate "$J" initramfs > "$J/log" 2>&1; chk 0 "a missing archive is reported, and the tree checks still stand on their own" $?
grep -q 'no zrec package archive' "$J/log" && echo "        and it warned instead"

echo "=== 4. flash mode, both directions ==="
K=$(mk_tree flashdirty); gate "$K" flash > "$K/log" 2>&1; chk 1 "flash mode that staged zrec refuses" $?
L=$(mk_tree flashclean)
rm -rf "$L/openwrt/build_dir/target-aarch64_cortex_a53/zrec-1" "$L/openwrt/bin/packages/aarch64_cortex-a53/base/"zrec*
rm -f "$L/openwrt/bin/targets/zte/zx279133/"*manifest*
gate "$L" flash > "$L/log" 2>&1; chk 0 "flash mode with no zrec anywhere passes" $?

echo
echo "=== what the positive run reported ==="
sed -n '1,12p' "$A/log"
echo
echo "pass=$pass fail=$fail"
[ "$fail" -eq 0 ]
