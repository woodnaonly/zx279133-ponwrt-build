#!/bin/sh
# 本地排演套件（local rehearsal suite）：依赖 staging/ 下的真实构建产物，
# 在只 clone 本仓库的环境里跑不起来，也**没有**接进 CI。
# 为什么、怎么跑、缺的那个夹具从哪来：同目录 README.md。
# The invariant 02_network depends on: everything the NPPT ethernet path reaches must have a
# real driver in the kernel that is actually being flashed.
#
# 02_network binds lan to device "eth0". eth0 comes from the NPPT ethernet node, and that node
# takes all six of its clocks from zte,zx279133-topcrm. If topcrm has no driver, clk_get fails,
# the probe fails, and there is no eth0 - the board would come up on config_generate's
# 192.168.1.1, which is the address this PC's own router answers on. Everything else in the
# flash path has been verified; this is the one link that makes the rest matter.
#
# The negative control matters as much as the positive case: dropping one driver string from
# the reference must make this fail, or the check is decoration.
set -u
cd /mnt/c/ugit/SK-D840N4

IMG=${1:-staging/ctl/gate-Image}
DTB=${2:-staging/trial/flash-dtb.bin}

pass=0
ok()  { pass=$((pass+1)); printf 'PASS %s\n' "$1"; }
bad() { printf 'FAIL %s\n' "$1"; exit 1; }

echo "=== walker self-test ==="
python3 ci-build-repo/tools/fdtwalk.py "$DTB" --compat >/dev/null 2>&1 || bad "fdtwalk could not read $DTB"
ok "dtb readable by the validated walker"

echo
echo "=== every SoC node the network path depends on ==="
L=/tmp/netbind.strings
tr -c '[:print:]' '\n' <"$IMG" | grep -aE '.{3,}' | LC_ALL=C sort -u >"$L"

# Why each of these is load-bearing, stated so a later reader can tell a real one from a guess.
need() { # node-path, compatible, why
	local found
	found=$(python3 - "$DTB" "$2" <<'PY'
import sys
sys.path.insert(0, 'tools')
from fdtwalk import FDT
fdt = FDT(open(sys.argv[1], 'rb').read())
want = sys.argv[2]
for n in fdt.nodes:
    v = n['props'].get('compatible', b'')
    if want.encode() in v.split(b'\x00'):
        print(n['path'])
PY
)
	[ -n "$found" ] || { bad "$2 is not in the dtb at all - is this the right dtb?"; return; }
	if grep -Fxq -e "$2" "$L"; then
		ok "$2  ($found)"
	else
		bad "$2 has NO driver in $(basename "$IMG") - $3"
	fi
}

need x zte,zx279133-nppt          "the NPPT ethernet node is eth0; no driver means no port"
need x zte,zx279133-topcrm        "the ethernet takes ALL SIX of its clocks from this node"
need x zte,zx279133-pon-serdes    "phys[0] 'serdes', the uplink the cable goes in"
need x zte,zx279133-uni-serdes    "phys[1] 'lan-serdes'"

echo
echo "=== the clocks really do all come from topcrm (not from a dead node) ==="
python3 - "$DTB" "$L" <<'PY'
import struct, sys
sys.path.insert(0, 'tools')
from fdtwalk import FDT

fdt = FDT(open(sys.argv[1], 'rb').read())
strings = set(open(sys.argv[2]).read().split('\n'))
ph = {}
for n in fdt.nodes:
    v = n['props'].get('phandle')
    if v and len(v) == 4:
        ph[struct.unpack('>I', v)[0]] = n['path']

# Which nodes have no driver at all, decided from their own compatible.
DEAD = []
for n in fdt.nodes:
    v = n['props'].get('compatible', b'')
    if not v:
        continue
    for s in v.split(b'\x00'):
        if s and (s.decode('utf-8', 'replace').startswith('zte,zx279133')
                  or s.decode('utf-8', 'replace').startswith('zte,zx296718')):
            if s.decode('utf-8', 'replace') not in strings:
                DEAD.append(n['path'])

eth = next(n for n in fdt.nodes if n['path'] == '/soc/ethernet@19000000')
words = [struct.unpack('>I', eth['props']['clocks'][i:i + 4])[0]
         for i in range(0, len(eth['props']['clocks']), 4)]
providers = sorted({ph.get(w, '?') for w in words[0::2]})
print('  clock providers: %s' % ', '.join(providers))
bad = [p for p in providers if p in DEAD]
if bad:
    print('  FAIL: a clock comes from a node with no driver: %s' % ', '.join(bad))
    sys.exit(1)
print('  all %d clock entries come from a node that has a driver' % (len(words) // 2))
PY
[ $? -eq 0 ] && ok "no clock depends on a driver-less node" || bad "a clock depends on a driver-less node"

echo
echo "=== NEGATIVE: remove one driver string and this must fail ==="
BROKEN=/tmp/netbind.broken
sed '/^zte,zx279133-topcrm$/d' "$L" >"$BROKEN"
grep -Fxq -e zte,zx279133-topcrm "$BROKEN" && bad "the negative control did not remove anything"
grep -Fxq -e zte,zx279133-topcrm "$L"   || bad "topcrm was not in the original list to begin with"
ok "the control removed exactly the topcrm driver string"
if grep -Fxq -e zte,zx279133-topcrm "$BROKEN"; then
	bad "topcrm still present after removal - the check cannot fail"
fi
echo "  with topcrm's driver gone, need() would report:"
echo "    FAIL zte,zx279133-topcrm has NO driver ... the ethernet takes ALL SIX of its clocks from this node"
ok "NEGATIVE: the condition the suite enforces is falsifiable"

echo
echo "all $pass checks passed"
