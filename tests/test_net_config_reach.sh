#!/bin/sh
# 本地排演套件（local rehearsal suite）：依赖 staging/ 下的真实构建产物，
# 在只 clone 本仓库的环境里跑不起来，也**没有**接进 CI。
# 为什么、怎么跑、缺的那个夹具从哪来：同目录 README.md。
# Does the real 02_network actually reach the SK-D840N branch and set 192.168.9.1?
#
# Four findings in this project have now been resolved the same way: something was reported
# missing because it was not in the file I happened to grep, while it was in fact reachable
# through the sourcing graph. v() was in common.sh, pulled in by do_stage2. board_name is in
# functions.sh, pulled in by uci-defaults.sh. rc.common is in /etc, not /lib. So this test does
# not reason about the sourcing graph - it runs the shipped script and looks at what it calls.
#
# The image's own uci-defaults.sh is used, with the json/uci layer stubbed because it needs
# aarch64 ubus binaries. Only absolute /lib paths inside the scripts are rewritten, to the
# sandbox - the control flow under test is byte-identical to what ships.
#
# What has to be true for the board to be reachable:
#   board_name resolves to the dtb compatible, and
#   ucidef_set_interface is called with the right device and ipaddr 192.168.9.1.
# A miss here is not a cosmetic problem: config_generate's fallback is 192.168.1.1, which is
# the address this PC's own router answers on.
#
# Cases A-D ask whether the branch is reached at all.  Cases E and F ask what it
# decides once it is, and they are the reason this file is runnable from anywhere:
# E asserts that every UNI jack stays in the LAN bridge and that the WAN names no
# jack of the panel (the owner's default, 2026-10-26), F that the `wan` slot is not
# invented on a port that does not exist.  Against the file as it was before that
# change E fails, printing `ucidef_set_interface wan device lan3` - which is the
# control group, and it is why the case exists.
#
# Run:  sh tests/test_net_config_reach.sh
#       NET=/tmp/02_network.old sh tests/test_net_config_reach.sh   # another copy
# The working copy is found from this script's own path, and /tmp/rootcheck holds
# the unpacked root (set ROOTCHECK_DIR to move it).
set -u
# Where the working copy is, derived rather than remembered.  This used to be a
# literal WSL path (cd /mnt/c/ugit/SK-D840N4), and on any other machine the `cd`
# fails, `set -u` does not stop the script, and the next line reports
# "…/02_network missing" - which reads exactly like a missing fixture and is
# really a missing `cd`.
HERE=$(CDPATH= cd -- "$(dirname "$0")" && pwd)
cd "$HERE/../.." || { echo "cannot reach the working copy from $HERE" >&2; exit 1; }

OUT=${ROOTCHECK_DIR:-/tmp/rootcheck}
# NET= lets a second copy of the script be put through the same cases, which is
# how the control group runs: the file before the change must fail case E.
S=${NET:-ci-build-repo/overlays/target/linux/zte/zx279133/base-files/etc/board.d/02_network}
[ -f "$S" ] || { echo "$S missing" >&2; exit 1; }
# Self-contained: extract the image if it is not already unpacked, so this suite can be run on
# its own. jefferson refuses an existing destination.
if [ ! -f "$OUT/lib/functions/uci-defaults.sh" ]; then
	echo "extracting the image (first run)..."
	rm -rf "$OUT"
	# The two stubs are not a shortcut.  This PC's python is a native Windows build
	# and its `os` has neither makedev nor mknod, so the stock CLI died at
	# dev/console having written only bin/ and dev/ - every file this suite reads
	# was still missing, and because the call hid its own output the whole report
	# was "jefferson failed".  A device node in the sandbox is of no interest here,
	# so it becomes an empty file and the walk continues; the check after this block
	# is what proves the extraction really produced the libraries.
	python -c '
import os
os.makedev = lambda ma, mi: (ma << 8) | mi
os.mknod = lambda path, mode=0o600, *a: open(path, "wb").close()
from jefferson.cli import main
main()
' -d "$OUT" staging/trial/flash-rootfs.jffs2 2>&1 | tail -3
	test -f "$OUT/lib/functions/uci-defaults.sh" \
		|| { echo "the image did not unpack - python -c 'import jefferson' must work" >&2; exit 1; }
fi
[ -f "$OUT/lib/functions/uci-defaults.sh" ] || { echo "image extraction produced no uci-defaults.sh" >&2; exit 1; }

pass=0
ok()  { pass=$((pass+1)); printf 'PASS %s\n' "$1"; }
bad() { echo "FAIL $1"; exit 1; }

T=$(mktemp -d); trap 'rm -rf "$T"' EXIT
mkdir -p "$T/lib/functions" "$T/usr/share/libubox" "$T/etc/board.d" "$T/tmp/sysinfo"

# The image's real shell libraries, with /lib/... and /tmp/sysinfo repointed into the sandbox.
# /tmp/sysinfo has to move too: board_name() reads that absolute path to decide which case to
# take, so leaving it alone would test the host's leftovers, not this board.
for f in lib/functions.sh lib/functions/uci-defaults.sh lib/functions/system.sh; do
	sed 's#^\. /lib/#. '"$T"'/lib/#; s#^\. /usr/share/#. '"$T"'/usr/share/#; s#/tmp/sysinfo#'"$T"'/tmp/sysinfo#g' \
		"$OUT/$f" >"$T/$f.raw" || exit 1
done
# jshn.sh needs ubus binaries; an empty file satisfies the ". file" without running anything.
: >"$T/usr/share/libubox/jshn.sh"

# The stubs stand in for ubus/uci and record what the script asked for. They are APPENDED to
# the library rather than sourced alongside it: 02_network sources uci-defaults.sh itself, so
# anything defined before that gets redefined. Last definition wins, so appending is what makes
# the stubs stick.
cat >"$T/stubs.sh" <<'STUB'
json_init() { :; }
json_is_a() { return 1; }
json_select_object() { :; }
json_select() { :; }
json_close_object() { :; }
json_add_string() { echo "json_add_string $*" >>"$CFGLOG"; }
json_add_array() { echo "json_add_array $*" >>"$CFGLOG"; }
json_dump() { echo "json_dump $*" >>"$CFGLOG"; }
ucidef_set_interface() { echo "ucidef_set_interface $*" >>"$CFGLOG"; }
ucidef_set_interfaces_lan_wan() { echo "ucidef_set_interfaces_lan_wan $*" >>"$CFGLOG"; }
ucidef_set() { echo "ucidef_set $*" >>"$CFGLOG"; }
STUB
cat "$T/lib/functions/uci-defaults.sh.raw" "$T/stubs.sh" >"$T/lib/functions/uci-defaults.sh"
cp "$T/lib/functions.sh.raw" "$T/lib/functions.sh"
cp "$T/lib/functions/system.sh.raw" "$T/lib/functions/system.sh"

# The script under test, unmodified apart from the same path rewrite.
#
# /sys/class/net moves into the sandbox too, and that is not cosmetic: the script
# asks `[ -e /sys/class/net/lan1 ]`, so with the absolute path left alone every
# case was answering from the *host's* netdevs.  On this PC (git bash, no /sys)
# that made case A pass for the wrong reason, and on a Linux box running our own
# kernel it would have made case A take the UNI branch and fail.  Fabricating the
# directory is what lets a case say "this board has these four jacks up".
sed 's#^\. /lib/#. '"$T"'/lib/#; s#/sys/class/net/#'"$T"'/sys/class/net/#g' \
	"$S" >"$T/etc/board.d/02_network"
chmod +x "$T/etc/board.d/02_network"

run_case() { # board_name [netdev-to-fabricate...] -> CFGLOG contents
	rm -rf "$T/sys"
	mkdir -p "$T/sys/class/net"
	board=$1
	shift
	for n in "$@"; do
		mkdir -p "$T/sys/class/net/$n"
	done
	echo "$board" >"$T/tmp/sysinfo/board_name"
	: >"$T/cfg.log"
	(
		export CFGLOG="$T/cfg.log" CFG="$T/board.json"
		sh "$T/etc/board.d/02_network" 2>&1 | sed 's/^/      /'
	) >/dev/null
	cat "$T/cfg.log"
}

echo "=== case A: this board (skyworth,sk-d840n) ==="
out=$(run_case "skyworth,sk-d840n")
echo "$out" | sed 's/^/  /'
echo "$out" | grep -q 'ucidef_set_interface lan device eth0 protocol static ipaddr 192.168.9.1 netmask 255.255.255.0' \
	|| bad "the lan stanza did not reach ucidef_set_interface with 192.168.9.1"
ok "skyworth,sk-d840n -> lan/eth0/192.168.9.1"

echo
echo "=== case B: the upstream sr1010 case must be preserved ==="
out=$(run_case "zte,zx279133-sr1010")
echo "$out" | sed 's/^/  /'
# The arguments arrive at the stub already word-split, so they are logged without the quoting
# the source shows. Match what the function actually receives, not how it is written.
echo "$out" | grep -q 'ucidef_set_interfaces_lan_wan lan1 lan2 lan3 lan4 eth0' \
	|| bad "the sr1010 branch was lost when this file was overlaid"
ok "zte,zx279133-sr1010 -> upstream lan/wan preserved"

echo
echo "=== case C: an unknown board must fall through, not invent an address ==="
out=$(run_case "some,vendor-we-do-not-know")
echo "$out" | sed 's/^/  /'
echo "$out" | grep -q 'ucidef_set' && bad "an unknown board still set interfaces"
ok "unknown board -> no interface set (falls to *)"

echo
echo "=== case D: no /tmp/sysinfo/board_name - proves board_name itself is reachable ==="
# board_name() is defined in functions.sh and returns the literal "generic" when the file is
# absent - it never returns empty and never errors. If it were undefined here, this would
# print "board_name: not found" and the case would match empty. So this case is the positive
# evidence that the sourcing graph works, rather than a hypothetical failure.
rm -f "$T/tmp/sysinfo/board_name"
: >"$T/cfg.log"
err=$( ( export CFGLOG="$T/cfg.log" CFG="$T/board.json"
         sh "$T/etc/board.d/02_network" ) 2>&1 )
echo "$err" | sed 's/^/  /'
if echo "$err" | grep -q 'board_name: not found'; then
	bad "board_name is NOT defined by the sourcing graph - the real script would misfire"
fi
ok "board_name resolved (no 'not found'), so the sourcing graph is intact"
# board_config_flush still runs and still writes board.json in the unsupported case - that is
# upstream behaviour and harmless. What must not happen is any interface being configured.
if grep -q 'ucidef_set' "$T/cfg.log"; then
	echo "  it configured:"; sed 's/^/    /' "$T/cfg.log"
	bad "an unidentified board still set interfaces"
fi
ok "generic board -> no ucidef_set* call (falls through to *)"

echo
echo "=== case E: the flashed board - every UNI jack is LAN, and no jack is a WAN ==="
# The case that did not exist, and the one the default changed for.  The owner's
# rule, 2026-10-26: 「默认都是 lan 口 需要 wan口的 用户自己去设置」.  The file used
# to carve lan3 out of the bridge and hand it to the WAN, and nothing in this suite
# could see it: case A has no netdevs at all, so it never reached the branch where
# the carving happens.
out=$(run_case "skyworth,sk-d840n" lan1 lan2 lan3 lan4 lan-cpu0 eth0)
echo "$out" | sed 's/^/  /'
echo "$out" | grep -qx 'ucidef_set_interface lan device lan1 lan2 lan3 lan4 lan-cpu0 protocol static ipaddr 192.168.9.1 netmask 255.255.255.0' \
	|| bad "the LAN bridge is not every UNI jack plus the CPU port"
ok "all four UNI jacks and lan-cpu0 are in the LAN bridge"
# The decisive half, and it has to be the negative one: a WAN whose device is a
# lanN is a panel jack taken away from the user, which looks like a dead port.
if echo "$out" | grep -q 'ucidef_set_interface wan device lan'; then
	echo "  it did:"; sed 's/^/    /' "$out"
	bad "a UNI jack was carved out as the WAN"
fi
ok "no UNI jack is carved out as the WAN"
# The slot still exists, on the one port that is not a 9132 UNI.  Both a user and
# tools/portmove.sh write into this section with `uci set network.wan.device=`, so
# deleting it would break the thing the owner asked for.
echo "$out" | grep -qx 'ucidef_set_interface wan device eth0 protocol dhcp' \
	|| bad "there is no wan section for a user to point at an uplink"
ok "the wan slot exists on eth0 (the panel's LAN1, not a 9132 UNI)"

echo
echo "=== case F: no eth0 - the wan slot is not invented out of nothing ==="
out=$(run_case "skyworth,sk-d840n" lan1 lan2 lan3 lan4 lan-cpu0)
echo "$out" | sed 's/^/  /'
echo "$out" | grep -qx 'ucidef_set_interface lan device lan1 lan2 lan3 lan4 lan-cpu0 protocol static ipaddr 192.168.9.1 netmask 255.255.255.0' \
	|| bad "the LAN bridge lost ports because eth0 was missing"
if echo "$out" | grep -q 'ucidef_set_interface wan'; then
	echo "  it did:"; sed 's/^/    /' "$out"
	bad "a wan section was created on a netdev that does not exist"
fi
ok "no eth0 -> LAN unchanged and no wan stanza"

echo
echo "all $pass checks passed"
