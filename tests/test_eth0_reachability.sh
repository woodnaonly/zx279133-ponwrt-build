#!/bin/sh
# 本地排演套件（local rehearsal suite）：依赖 staging/ 下的真实构建产物，
# 在只 clone 本仓库的环境里跑不起来，也**没有**接进 CI。
# 为什么、怎么跑、缺的那个夹具从哪来：同目录 README.md。
# Will eth0 ever come up?
#
# On SK-D840N this is the load-bearing question for the whole project, because the
# management address is on eth0 and eth0 is the only netdev mainline drives. The
# chain is:
#
#   /soc/ethernet@19000000 (zte,zx279133-nppt, okay)
#       .phy-handle = 0x18
#   /soc/mdio@14f02000/ethernet-phy@5
#       .reg = 5, .max-speed = 2500, and NO compatible
#       -> phylib has to match the chip by PHY ID, which means
#          drivers/net/phy/realtek.c must be in the kernel
#
# The "no compatible" is deliberate and was a real bug once: SR1010's node says
# "ethernet-phy-id0000.84b9" (the ZX279051 ID), and a compatible on a PHY node
# overrides ID matching, so the first mainline trial bound the ZX279051 driver to a
# Realtek chip and wrote ZX279051 registers into it. That reads on the board as
# "the board ran but the one cable that matters stayed dead". So the absence of a
# compatible is asserted here, not assumed.
#
# NOT asserted: that the link comes up, or that the cable is in the right jack.
# Both are board facts. See the OPEN ITEMS block in the SK-D840N dts.
set -u
cd /mnt/c/ugit/SK-D840N4 || exit 1

DTS=ci-build-repo/kernel-overlays/arch/arm64/boot/dts/zte/zx279133-sk-d840n.dts
NET=ci-build-repo/overlays/target/linux/zte/zx279133/base-files/etc/board.d/02_network
WF=ci-build-repo/.github/workflows/build-sk-d840n.yml
DTB=staging/ctl/gate-dtb
IMG=staging/ctl/gate-Image

pass=0
ok()  { pass=$((pass+1)); printf 'PASS %s\n' "$1"; }
bad() { printf 'FAIL %s\n' "$1"; exit 1; }

for f in "$DTS" "$NET" "$WF" "$DTB" "$IMG"; do
	[ -f "$f" ] || bad "$f missing"
done

echo "=== 1. the compiled dtb: nppt points at MDIO address 5 ==="
python3 ci-build-repo/tools/fdtwalk.py "$DTB" >/tmp/dtb.txt 2>&1 || bad "fdtwalk failed"
grep -q 'FDT totalsize=' /tmp/dtb.txt || bad "fdtwalk self-test did not pass"
node() { awk -v n="$1" '$0 ~ "NODE " n"$" {f=1;print;next} f&&/NODE /{exit} f{print}' /tmp/dtb.txt; }
NP=$(node '/soc/ethernet@19000000')
[ -n "$NP" ] || bad "no nppt node in $DTB"
echo "$NP" | grep -q '6f 6b 61 79' && ok "nppt status = okay" || bad "nppt is not enabled"
echo "$NP" | grep -q 'phy-handle' || bad "nppt has no phy-handle in the compiled dtb"
# Compare the byte strings as fdtwalk prints them, not as bare hex: the property line
# reads ".phy-handle = 00 00 00 18" and the node line reads ".phandle  = 00 00 00 18",
# so stripping the spaces on one side only is how this check failed the first time.
phraw=$(echo "$NP" | grep 'phy-handle' | sed 's/^.*= *//; s/ *$//')
ok "nppt phy-handle = [$phraw]"

PHY=$(node '/soc/mdio@14f02000/ethernet-phy@5')
[ -n "$PHY" ] || bad "no ethernet-phy@5 node in the compiled dtb"
echo "$PHY" | grep -q '00 00 00 05' && ok "the PHY is at MDIO address 5" || bad "PHY reg is not 5"
echo "$PHY" | grep -q "phandle.*= *$phraw\$" && ok "and its phandle is the one nppt references" \
	|| bad "the phandle does not match (nppt wants [$phraw])"
if echo "$PHY" | grep -q '\.compatible'; then
	bad "the PHY node grew a compatible - that overrides ID matching and binds the wrong driver"
else
	ok "it has NO compatible, so phylib matches the real chip by ID"
fi

echo
echo "=== 2. the Realtek PHY driver is built into the kernel, not a module ==="
tmp=$(mktemp -d)
LC_ALL=C tr -c '[:print:]' '\n' <"$IMG" | grep -aE '.{3,}' | LC_ALL=C sort -u >"$tmp/s.txt"
# Whole-line: the driver prints its .name strings, so their absence means the driver
# is not there. Note the table's first entry carries a tail-merge prefix ("0rtl822x..."),
# which is why a prefix test would be the wrong test here.
for s in 'Generic FE-GE Realtek PHY' 'RTL8226 2.5Gbps PHY' 'RTL8226B_RTL8221B 2.5Gbps PHY'; do
	grep -Fxq -e "$s" "$tmp/s.txt" && ok "flashed kernel contains: $s" || bad "flashed kernel lacks: $s"
done
grep -Fxq -e 'RTL8226B_RTL8221B 2.5Gbps PHY' "$tmp/s.txt" \
	&& ok "the RTL8226B is in the driver's match table, not just some Realtek PHY" \
	|| bad "no RTL8226B entry - a generic Realtek PHY is not enough"
grep -Fxq -e 'zzz-not-a-kernel-string-zzz' "$tmp/s.txt" \
	&& bad "the strings probe matches impossible text" \
	|| ok "control: an impossible string is correctly not found"
rm -rf "$tmp"

echo
echo "=== 3. the SK-D840N dts still says what it must ==="
grep -q 'phy-handle = <&rtl8226b>' "$DTS" && ok "&nppt points at rtl8226b" || bad "&nppt lost its phy-handle"
grep -q 'rtl8226b: ethernet-phy@5 {' "$DTS" && ok "the rtl8226b node is still at address 5" || bad "the PHY node moved"
# The block between the label and its closing brace must not gain a compatible.
blk=$(awk '/rtl8226b: ethernet-phy@5 \{/,/^\t\};/' "$DTS")
if echo "$blk" | grep -q 'compatible'; then
	bad "the source dts gives the PHY a compatible - see the ZX279051-on-Realtek failure"
else
	ok "and still carries no compatible in the source either"
fi
grep -q 'Deliberately \*\*no compatible\*\*' "$DTS" \
	&& ok "and the reason is still written down next to it" \
	|| note "the 'Deliberately no compatible' comment is gone - re-read why"

echo
echo "=== 4. the 9132 LAN ports are absent on purpose, not by accident ==="
# Structural patterns only. A plain grep for "lan-conduit" matches the OPEN ITEMS prose
# that explains why it was left out, which is the opposite of what it should report -
# so match DTS node/label syntax, which a comment line cannot produce.
if grep -qE 'lan-conduit[[:space:]]*:' "$DTS" \
   || grep -qE 'compatible.*zx279133-lan-conduit' "$DTS"; then
	bad "$DTS now has a lan-conduit node; SR1010's CPU port does not exist on this board"
else
	ok "no lan-conduit label or compatible: SR1010's DSA CPU port is not on this board"
fi
if grep -qE '^[[:space:]]*switch([[:space:]]*:[^ ]*)?[[:space:]]*\{' "$DTS"; then
	bad "$DTS now has a switch node; the 9132 has no mainline DSA driver"
else
	ok "no switch node: the internal 9132 has no mainline driver"
fi
# Control: the two patterns above must be able to fire, or they prove nothing.
printf 'x {\n\tlan-conduit: y {\n\t\tcompatible = "zte,zx279133-lan-conduit";\n\t};\n\tswitch {\n\t};\n};\n' >/tmp/fake.dts
n=$(grep -cE 'lan-conduit[[:space:]]*:|compatible.*zx279133-lan-conduit' /tmp/fake.dts)
m=$(grep -cE '^[[:space:]]*switch([[:space:]]*:[^ ]*)?[[:space:]]*\{' /tmp/fake.dts)
if [ "$n" -eq 2 ] && [ "$m" -eq 1 ]; then
	ok "control: both patterns fire on a dts that does have those nodes"
else
	bad "the structural patterns cannot detect the nodes they are looking for"
fi
rm -f /tmp/fake.dts
grep -q '9132' "$DTS" && ok "and the reason (the 9132) is still written down" \
	|| note "the 9132 explanation is gone"

echo
echo "=== 5. the management address is on eth0, and says so ==="
grep -q 'ucidef_set_interface "lan" device "eth0"' "$NET" \
	&& ok "SK-D840N puts lan on eth0" || bad "the SK-D840N case no longer names eth0"
grep -q 'ipaddr "192.168.9.1"' "$NET" && ok "at 192.168.9.1" || bad "the address is no longer 192.168.9.1"
grep -q 'ucidef_set_interfaces_lan_wan "lan1 lan2 lan3 lan4" "eth0"' "$NET" \
	&& ok "the sr1010 case is preserved verbatim (lan1-4, wan eth0)" \
	|| bad "the sr1010 case was changed"
grep -q 'no mainline equivalent' "$NET" || grep -q 'has no mainline driver' "$NET" \
	&& ok "the file still explains why eth1..eth3 are not named" \
	|| note "the explanation about eth1..eth3 is gone"

echo
echo "=== 6. the CI gate reads the merged .config, not the edited file ==="
# This is the check that caught the wrong mental model: CONFIG_REALTEK_PHY and
# CONFIG_MFD_SYSCON are both absent from config-6.18 and both present in the built
# kernel, so any gate that reads the edited file can be wrong in both directions.
#
# Asserted on the STEP, not on a literal. An earlier version grepped for the exact string
# `linux-zte_zx279133/linux-*/.config`, so pinning that lookup to $KVER - which is the
# correct fix, and which still reads the merged config - broke a suite whose *claim* was
# never in doubt. What has to hold is: the PHY gate step reads a .config that lives under
# build_dir, and it does not read target/linux/.../config-6.18.
PHYS=$(awk '/^      - name: Assert the kernel really has the PHY driver$/{f=1} f{print} f && /exit 1; \}/{exit}' "$WF")
[ -n "$PHYS" ] && ok "the PHY gate step was found" || bad "the PHY gate step is gone"
printf '%s\n' "$PHYS" | grep -q 'build_dir' \
	&& ok "it reads the config under build_dir, i.e. the kernel's own merged .config" \
	|| bad "the PHY gate no longer reads the merged .config"
printf '%s\n' "$PHYS" | grep -q '\.config' \
	&& ok "and it is a .config" || bad "it stopped reading a .config"
printf '%s\n' "$PHYS" | grep -q 'config-6\.18' \
	&& bad "it reads the EDITED target file, which can be wrong in both directions" \
	|| ok "and it does not read the edited config-6.18"
printf '%s\n' "$PHYS" | grep -q 'linux-\$KVER' \
	&& ok "and the build-dir lookup is pinned to linux-\$KVER, not whatever is there" \
	|| note "the lookup is not pinned to \$KVER"
grep -q 'REALTEK_PHY' "$WF" \
	&& ok "and it asserts CONFIG_REALTEK_PHY=y" || bad "the PHY gate stopped asserting REALTEK_PHY"
grep -q 'PHY driver' "$WF" || bad "the PHY gate step is gone"

echo
echo "=== NOT asserted, on purpose ==="
echo "  That eth0 links, that a cable in any particular jack works, or that the board"
echo "  answers on 192.168.9.1. All three need the hardware. The dts OPEN ITEMS block"
echo "  names the way to tell the jacks apart: move a cable and read dmesg."
echo
echo "ALL OK - $pass checks"
