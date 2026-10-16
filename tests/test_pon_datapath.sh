#!/bin/sh
# 本地排演套件（local rehearsal suite）：依赖 staging/ 下的真实构建产物，
# 在只 clone 本仓库的环境里跑不起来，也**没有**接进 CI。
# 为什么、怎么跑、缺的那个夹具从哪来：同目录 README.md。
# What the prepared kernel tree actually contains on the PON side, and why the
# previous two beliefs about it were both wrong.
#
# Belief 1 (INVENTORY.md section 5): "there is no PON MAC driver" - reached by
#   grepping drivers/net/ethernet/zte and finding only an NPPT "WAN path" driver.
#   Wrong: ZTE_ZX279133_ETH `depends on PHY_ZX279133_PON_SERDES`, the dtb node is
#   wired to pon_serdes with phy-mode 2500base-x and the PON MAC clock, and the
#   driver loads vendor PPU microcode for the packet engine.
#
# Belief 2 (this project's own binding analysis, and a comment shipped in the
#   release workflow): "syscon is not in this kernel, so zte,zx279133-pon-route,
#   -idm-cci and -wdt-reset bind to nothing". Also wrong, and for a subtler
#   reason: this tree's drivers/mfd/syscon.c carries no of_device_id table and no
#   platform_driver at all. It registers on demand -
#       syscon_node_to_regmap(np) -> device_node_get_regmap(np,
#                                                          of_device_is_compatible(np, "syscon"),
#                                                          true)
#   - so a node whose compatible list ends in "syscon" gets a regmap at lookup
#   time with no separate driver ever matching it. A node-to-driver table cannot
#   see that, which is exactly how a confident wrong answer is produced.
#
# What is asserted: source text, the built kernel image, the flashed dtb, and the
# firmware blob. What is NOT asserted: that PON works, registers, or links up.
# That is a runtime fact and needs the board.
set -u
cd /mnt/c/ugit/SK-D840N4

K=staging/diag-20261014/kernelsrc
IMG=staging/ctl/gate-Image
OLDIMG=staging/ctl/Image
DTSI=$K/arch_arm64_boot_dts_zte_zx279133.dtsi
FW=immortalwrt/target/linux/zte/zx279133/firmware/zte/zx279133/mcode_intel.bin
FW419=sk-d840n-openwrt/rootfs/etc/mcode/mcode_133.bin
WF=ci-build-repo/.github/workflows/build-sk-d840n.yml
INV=pon/INVENTORY.md

pass=0
ok()  { pass=$((pass+1)); printf 'PASS %s\n' "$1"; }
bad() { printf 'FAIL %s\n' "$1"; exit 1; }
note(){ printf 'note %s\n' "$1"; }

need() { [ -f "$1" ] || bad "$1 missing - rerun fetch_kernel_files2.py / fetch_kernel_files3.py"; }

echo "=== 1. the NPPT driver IS the PON MAC, by Kconfig ==="
need "$K/drivers_net_ethernet_zte_Kconfig"
need "$K/drivers_net_ethernet_zte_Makefile"
grep -q 'depends on PHY_ZX279133_PON_SERDES' "$K/drivers_net_ethernet_zte_Kconfig" \
	&& ok "ZTE_ZX279133_ETH depends on PHY_ZX279133_PON_SERDES" \
	|| bad "the NPPT driver no longer depends on the PON SERDES - belief 1 may be back"
grep -q 'zx279133-np\.o' "$K/drivers_net_ethernet_zte_Makefile" \
	&& ok "and it is built (zx279133-np.o under CONFIG_ZTE_ZX279133_ETH)" \
	|| bad "zx279133-np.o is not in the Makefile"

# Control: a symbol that must NOT exist, so this block cannot pass by matching anything.
if grep -qx 'CONFIG_ZTE_ZX279133_PON_MAC=y' immortalwrt/target/linux/zte/zx279133/config-6.18; then
	bad "a CONFIG_ZTE_ZX279133_PON_MAC appeared - the premise of this suite changed"
else
	ok "control: there is no separate CONFIG_ZTE_ZX279133_PON_MAC to find"
fi

echo
echo "=== 2. it runs vendor microcode, not a hand-written protocol stack ==="
need "$K/drivers_net_ethernet_zte_zx279133-np.c"
need "$K/drivers_net_ethernet_zte_zx279133.h"
grep -q 'request_firmware(&fw, "zte/zx279133/mcode_intel.bin"' \
	"$K/drivers_net_ethernet_zte_zx279133-np.c" \
	&& ok "zx279133-np.c loads zte/zx279133/mcode_intel.bin at probe" \
	|| bad "the NPPT driver no longer loads the PPU microcode"
grep -q 'define ZX279133_MCODE_MAGIC[[:space:]]*0xffffaaaa' "$K/drivers_net_ethernet_zte_zx279133.h" \
	&& ok "ZX279133_MCODE_MAGIC is 0xffffaaaa" || bad "MCODE_MAGIC changed"
grep -q 'define ZX279133_MCODE_INST_NUM[[:space:]]*12288' "$K/drivers_net_ethernet_zte_zx279133.h" \
	&& ok "ZX279133_MCODE_INST_NUM is 12288" || bad "MCODE_INST_NUM changed"

echo
echo "=== 3. the shipped blob passes the driver's own calibration check ==="
python3 staging/diag-20261014/check_ppu_mcode.py "$FW" >/tmp/mcode.out 2>&1
rc=$?
sed 's/^/  /' /tmp/mcode.out
[ $rc -eq 0 ] && ok "mcode_intel.bin passes size + CRC32 + header" || bad "mcode_intel.bin fails the driver's own check"
# Control: the same check must REJECT the other PPU build, or it cannot discriminate.
if python3 staging/diag-20261014/check_ppu_mcode.py "$FW419" >/tmp/mcode419.out 2>&1; then
	bad "the CRC check accepted the vendor 4.19 blob too - it proves nothing"
else
	ok "control: the same check rejects the vendor 4.19 mcode_133.bin"
fi

echo
echo "=== 4. zte,pon-route and zte,idm-cci are mandatory, not optional ==="
need "$K/drivers_net_ethernet_zte_zx279133-main.c"
grep -q 'syscon_regmap_lookup_by_phandle(dev->of_node,' "$K/drivers_net_ethernet_zte_zx279133-main.c" \
	&& ok "main.c uses syscon_regmap_lookup_by_phandle" || bad "that call is gone"
n=$(grep -c 'IS_ERR(eth->pon_route)' "$K/drivers_net_ethernet_zte_zx279133-main.c")
[ "$n" -ge 1 ] && ok "pon_route failure is fatal (IS_ERR check present)" || bad "pon_route became optional"
n=$(grep -c 'IS_ERR(eth->idm_cci)' "$K/drivers_net_ethernet_zte_zx279133-main.c")
[ "$n" -ge 1 ] && ok "idm_cci failure is fatal too" || bad "idm_cci became optional"
grep -q '^  - zte,pon-route$' "$K/Documentation_devicetree_bindings_net_zte,zx279133-nppt.yaml" \
	&& ok "the binding marks zte,pon-route required" || bad "binding no longer requires it"
grep -q '^  - zte,idm-cci$' "$K/Documentation_devicetree_bindings_net_zte,zx279133-nppt.yaml" \
	&& ok "the binding marks zte,idm-cci required" || bad "binding no longer requires it"
# Control: _optional() would have been the tell that these were not mandatory.
if grep -q 'syscon_regmap_lookup_by_phandle_optional' "$K/drivers_net_ethernet_zte_zx279133-main.c"; then
	note "main.c now uses the _optional variant somewhere - re-read the mandatory/optional claim"
else
	ok "control: no _optional() variant, so mandatory really means mandatory"
fi

echo
echo "=== 5. and they resolve, because syscon registers on demand ==="
need "$K/drivers_mfd_syscon.c"
grep -q 'of_device_is_compatible(np, "syscon")' "$K/drivers_mfd_syscon.c" \
	&& ok "syscon_node_to_regmap creates the regmap for any node compatible with syscon" \
	|| bad "drivers/mfd/syscon.c no longer self-registers - belief 2 may be back"
if grep -q 'of_device_id' "$K/drivers_mfd_syscon.c"; then
	note "syscon.c now has an of_device_id table; the on-demand path may be superseded"
else
	ok "control: there is no of_device_id table - no platform driver could match these nodes"
fi
need "$DTSI"
n=$(grep -c 'compatible = "zte,zx279133-[a-z0-9-]*", "syscon";' "$DTSI")
[ "$n" -ge 5 ] && ok "$n dtsi nodes carry the \"syscon\" fallback compatible" \
	|| bad "only $n nodes carry \"syscon\" - the on-demand path would not fire"
for nd in topcrm idm_cci pon_route; do
	grep -q "^[[:space:]]*$nd: " "$DTSI" && ok "dtsi defines $nd" || bad "dtsi lost $nd"
done

echo
echo "=== 6. the drivers are really in the kernel that gets flashed ==="
[ -f "$IMG" ] || bad "$IMG missing"
imgs() { LC_ALL=C tr -c '[:print:]' '\n' <"$1" | grep -aE '.{3,}' | LC_ALL=C sort -u; }
tmp=$(mktemp -d); imgs "$IMG" >"$tmp/new.txt"
for s in 'failed to load PPU microcode' 'invalid PPU microcode file' \
         'PPU microcode version %#x.%#x' \
         'uncalibrated PPU firmware; retaining native RX queues' \
         'zte/zx279133/mcode_intel.bin' \
         'failed to get TOPCRM syscon' 'failed to get PON route syscon' \
         'failed to get IDM CCI syscon' 'zte,zx279133-nppt' 'zte,zx279133-pon-serdes'; do
	grep -Fxq -e "$s" "$tmp/new.txt" && ok "flashed kernel contains: $s" \
		|| bad "flashed kernel lacks: $s (looked for the whole token)"
done
# The strings pipeline yields whole NUL-delimited runs, so a prefix such as
# "PPU microcode version" is NOT a token - it is "PPU microcode version %#x.%#x".
# Asserting the prefix would be a checker that can only ever fail.
grep -Fxq -e 'PPU microcode version' "$tmp/new.txt" \
	&& bad "the kernel contains a bare 'PPU microcode version' token; re-derive the literals" \
	|| ok "control: the prefix is not a token, which is why the full literal is asserted"
grep -Fxq -e '__regmap_init_mmio_clk' "$tmp/new.txt" \
	&& ok "regmap-mmio-with-clock is built - the backend drivers/mfd/syscon.c needs" \
	|| bad "no __regmap_init_mmio_clk; CONFIG_REGMAP_MMIO_CLK looks off"
grep -Fxq -e 'altr_sysmgr_regmap_lookup_by_phandle' "$tmp/new.txt" \
	&& ok "the syscon lookup API is exported and used (CONFIG_MFD_ALTERA_SYSMGR)" \
	|| bad "no syscon API consumer in the kernel"
# KNOWN TRAP, recorded because it already cost one wrong assertion in this file:
# the kernel's string table tail-merges, so a length byte can sit immediately
# before a string. 0x33 is printable, so the pipeline yields "3regmap init failed"
# and a whole-line match for "regmap init failed" can never succeed. Assert the
# substring here, and let the two exact matches above carry the discriminating power.
grep -Fq -e 'regmap init failed' "$tmp/new.txt" \
	&& ok "drivers/mfd/syscon.c's pr_err literal is present (substring; see the prefix trap)" \
	|| bad "drivers/mfd/syscon.c does not look built - CONFIG_MFD_SYSCON may be off"
grep -Fxq -e 'regmap init failed' "$tmp/new.txt" \
	&& note "the syscon literal has no merge prefix this time; the substring assertion is still correct" \
	|| ok "control: the literal really is prefix-merged, so whole-line matching would be the broken test"
# Control: the same probe on the other kernel must find none of them.
if [ -f "$OLDIMG" ]; then
	imgs "$OLDIMG" >"$tmp/old.txt"
	hits=0
	for s in 'failed to load PPU microcode' 'zte,zx279133-nppt' 'mcode_intel.bin'; do
		grep -Fxq -e "$s" "$tmp/old.txt" && hits=$((hits+1))
	done
	[ "$hits" -eq 0 ] && ok "control: none of those strings exist in the other kernel" \
		|| bad "the probe also hits in $OLDIMG - it does not discriminate"
else
	note "$OLDIMG absent; skipped the cross-kernel control"
fi
# Control: a string that cannot exist must not be found.
grep -Fxq -e 'zzz-not-a-kernel-string-zzz' "$tmp/new.txt" \
	&& bad "the strings probe matches impossible text" \
	|| ok "control: an impossible string is correctly not found"
rm -rf "$tmp"

echo
echo "=== 7. the flashed dtb enables the whole path ==="
python3 tools/fdtwalk.py staging/ctl/gate-dtb >/tmp/dtb.txt 2>&1 || bad "fdtwalk failed"
grep -q 'FDT totalsize=' /tmp/dtb.txt || bad "fdtwalk self-test did not pass"
node() { awk -v n="$1" '$0 ~ "NODE " n"$" {f=1;print;next} f&&/NODE /{exit} f{print}' /tmp/dtb.txt; }
for nd in '/soc/ethernet@19000000' '/soc/phy@16000000' '/soc/phy@16100000'; do
	blk=$(node "$nd")
	[ -n "$blk" ] || bad "$nd not in the flashed dtb"
	echo "$blk" | grep -q '6f 6b 61 79' && ok "$nd status = okay" || bad "$nd is not enabled"
done
node '/soc/ethernet@19000000' | grep -q 'zte,pon-route' \
	&& ok "the nppt node still carries zte,pon-route" || bad "nppt lost zte,pon-route"
node '/soc/ethernet@19000000' | grep -q 'zte,idm-cci' \
	&& ok "and zte,idm-cci" || bad "nppt lost zte,idm-cci"

echo
echo "=== 8. the wrong claims are gone from what ships ==="
# Match the *original wording*, not the bare phrase: the rewrite quotes the old
# sentence in order to retract it, so a substring search for the phrase itself
# would now be satisfied by the correction and would stop meaning anything.
grep -q 'there is no PON MAC driver' "$INV" \
	&& note "the phrase survives inside the retraction - checking the original wording instead" \
	|| ok "the original phrase is gone entirely"
for bad_line in \
  '## 5. What mainline has, for contrast' \
  'Searching that tree for PON gives only' \
  'the driver does not.'; do
	grep -qF -- "$bad_line" "$INV" && bad "INVENTORY.md still carries the old claim: $bad_line" \
		|| ok "old wording removed: $bad_line"
done
grep -q 'has been rewritten' "$INV" && ok "and the section says it was rewritten" \
	|| bad "the rewrite marker is missing - a reader cannot tell the text changed"
if grep -q 'syscon" is not' "$WF"; then
	bad "$WF still says syscon is not in this kernel"
else
	ok "the release workflow no longer says syscon is not in this kernel"
fi

echo
echo "=== NOT asserted, on purpose ==="
echo "  That PON registers, links, or carries traffic. Nothing in this suite touches"
echo "  hardware, and the protocol layer (G.984 PLOAM/T-CONT/GEM/OMCI) is not in the"
echo "  C driver - whether mcode_intel.bin carries it is still unmeasured."
echo
echo "ALL OK - $pass checks"
