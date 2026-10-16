#!/bin/sh
# 本地排演套件（local rehearsal suite）：依赖 staging/ 下的真实构建产物，
# 在只 clone 本仓库的环境里跑不起来，也**没有**接进 CI。
# 为什么、怎么跑、缺的那个夹具从哪来：同目录 README.md。
# The panel beacon, asserted rather than assumed.
#
# This matters more than a cosmetic detail suggests. On this board there is no serial console,
# so a blinking LED is the only human-visible sign that a round is alive - init-flash.sh and
# flash-round.sh both announce progress by writing /sys/class/leds/*/brightness. A dtb with no
# /leds node does not merely look worse; it means a board window passes with nothing to look at
# and no way to tell "still working" from "hung at the erase".
#
# The negative control is the point of the suite. An LED node whose `gpios` phandle does not
# resolve is exactly what a broken overlay produces, and it fails silently at probe time - so
# the check has to be shown refusing such a tree, not just accepting a good one.
set -u
cd /mnt/c/ugit/SK-D840N4
DTB=${1:-staging/trial/flash-dtb.bin}
[ -s "$DTB" ] || { echo "$DTB missing" >&2; exit 1; }

pass=0
ok()  { pass=$((pass+1)); printf 'PASS %s\n' "$1"; }
bad() { printf 'FAIL %s\n' "$1"; exit 1; }

echo "=== the walker's own self-test ==="
out=$(python3 tools/fdtwalk.py "$DTB" --leds 2>&1) || bad "fdtwalk.py failed: $(echo "$out" | tail -3)"
echo "$out" | grep -q 'walker works' || bad "fdtwalk.py refuses to report: $(echo "$out" | head -5)"
ok "fdtwalk.py calibrated itself and answered"

echo
echo "=== the /leds node and its children ==="
echo "$out" | grep -q '^  /leds$' || bad "no /leds node in this tree"
ok "/leds node present"
n=$(echo "$out" | grep -cE '^  /leds/led-')
[ "$n" -ge 1 ] || bad "/leds has no children"
echo "  $n LEDs declared"
[ "$n" -ge 7 ] && ok "all seven panel LEDs are declared ($n)" \
	|| echo "  note: fewer than the 7 seen in the built dtb ($n) - is this a different dtb?"

echo
echo "=== every LED is wired to a real GPIO controller ==="
# The walker resolves each phandle to a node path and prints UNRESOLVED when it cannot.
unres=$(echo "$out" | grep -c 'UNRESOLVED')
[ "$unres" -eq 0 ] || bad "$unres LED(s) reference a phandle this tree does not define"
ok "no dangling phandle references"
badctl=$(echo "$out" | grep 'gpios' | grep -cv 'gpio@')
[ "$badctl" -eq 0 ] || bad "$badctl LED(s) point at something that is not a gpio@ controller"
ok "every LED points at a zte,zx279133-gpio controller"

echo
echo "=== every LED has a pin and a polarity ==="
echo "$out" | grep 'gpios' | grep -q 'pin=' || bad "no LED reports a pin"
echo "$out" | grep 'gpios' | grep -q 'active-' || bad "no LED reports a polarity"
ok "pins and polarity present on every LED"

echo
echo "=== the beacon is on without anyone writing to sysfs ==="
echo "$out" | grep -A3 '/leds/led-power' | grep -q 'default-state    on' \
	|| bad "led-power does not default to on - the panel would be dark until something writes sysfs"
ok "led-power default-state=on"

echo
echo "=== the scripts really drive that path ==="
# Not flash-round.sh: that is the PC-side driver, and the beacon code is not its job. The code
# that writes brightness lives in the generated RAM-root inits - mkctlram.py is what emits them,
# and init-flash.sh is a checked-in copy of one. Naming flash-round.sh here was wrong.
for f in staging/ctl/init-flash.sh tools/mkctlram.py; do
	[ -f "$f" ] || bad "$f missing"
	grep -q '/sys/class/leds/\*/brightness' "$f" \
		&& ok "$(basename "$f") drives /sys/class/leds/*/brightness" \
		|| bad "$f no longer drives the beacon"
done

echo
echo "=== NEGATIVE: a dtb with one unresolvable phandle must be refused ==="
python3 - "$DTB" <<'PY'
import struct, sys
sys.path.insert(0, 'tools')
from fdtwalk import FDT

fdt = FDT(open(sys.argv[1], 'rb').read())
blob = bytearray(open(sys.argv[1], 'rb').read())

# Find the first gpios property in the struct block and repoint its phandle to one nothing
# defines, which is what a mis-edited overlay looks like.
off = fdt.off_struct
for n in fdt.nodes:
    if '/leds/led-' in n['path'] and 'gpios' in n['props']:
        target = n['props']['gpios']
        # locate this property's value bytes in the struct block
        idx = blob.find(target, off, off + fdt.size_struct)
        if idx < 0:
            print('  could not locate the gpios value; skipping')
            sys.exit(0)
        struct.pack_into('>I', blob, idx, 0xdead)   # no node has this phandle
        print('  repointed %s to phandle 0xdead' % n['path'])
        break
else:
    print('  no LED with a gpios property found; the control is vacuous')
    sys.exit(0)

open('/tmp/fake-dangling.dtb', 'wb').write(bytes(blob))
PY

[ -s /tmp/fake-dangling.dtb ] || bad "could not build the negative-control tree"
bad_out=$(python3 tools/fdtwalk.py /tmp/fake-dangling.dtb --leds 2>&1)
echo "$bad_out" | grep -q 'UNRESOLVED' \
	|| bad "the walker did not notice a dangling phandle - it cannot be trusted to notice a real fault"
echo "  walker says: $(echo "$bad_out" | grep UNRESOLVED | head -1 | sed 's/^ *//')"
ok "NEGATIVE: a dangling phandle is reported as UNRESOLVED"

# And the suite's own verdict must flip on that tree.
if python3 tools/fdtwalk.py /tmp/fake-dangling.dtb --leds 2>&1 | grep -q UNRESOLVED; then
	ok "NEGATIVE: the acceptance condition fails on the broken tree, as it must"
else
	bad "the broken tree still passes"
fi

echo
echo "all $pass checks passed"
