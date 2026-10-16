#!/bin/sh
# 本地排演套件（local rehearsal suite）：依赖 staging/ 下的真实构建产物，
# 在只 clone 本仓库的环境里跑不起来，也**没有**接进 CI。
# 为什么、怎么跑、缺的那个夹具从哪来：同目录 README.md。
# Where the G.984.1 protocol layer lives - on the vendor stack, and on mainline.
#
# The previous round left this as an even-handed either/or: the protocol state machines
# are "either inside mcode_intel.bin or absent". That was too generous to the tree, and
# it can be settled from what is on disk.
#
# Established here, with the detector calibrated against the modules that DO carry it:
#
#   vendor 4.19 : zte_xgpon.ko carries, verbatim -
#                   power P2--->P3 / power P5--->P6      (G.987.1 power machine)
#                   Op State Machine, o2_3-o7             (G.984.1 ONU machine)
#                   O4 p_o4_assigned_onu_id_msg, O7 p_o7_enable_sn_msg
#                   fi_sendPloamMsg, fi_Configure_Ponmac_Send_Omci_Msg
#                 i.e. the ITU state machines run on the CPU, in a loadable module.
#   np_133.ko   : loads /etc/mcode/mcode_133.bin, exposes np_downlaod_mcode
#   mainline    : zx279133-np.c loads zte/zx279133/mcode_intel.bin and comments on
#                 "np_downlaod_mcode stores each file pair high, low" - the vendor's own
#                 misspelled symbol. So the mainline loader was written by reading np_133.ko,
#                 and it reimplemented the packet-engine half only.
#   flashed kernel: ZERO occurrences of PLOAM / Op State Machine / p_o7_enable_sn_msg /
#                 fi_sendPloamMsg / Serena / fi_Configure_Ponmac_Send_Omci_Msg,
#                 whole-line and substring alike.
#
# What is still NOT settled: whether the PPU microcode independently implements GEM/T-CONT
# framing. Its tag stream has packet-type and port-flow tables, which is the shape a
# demultiplexer needs, but the header does not say what the tables encode, and reading
# them as PON rather than Ethernet would require np_133.ko's decoder. A boot log settles
# it; the source does not.
set -u
cd /mnt/c/ugit/SK-D840N4 || exit 1

S=/tmp/pon_proto_strings.txt
LC_ALL=C tr -c '[:print:]' '\n' < staging/ctl/gate-Image | grep -aE '.{3,}' | LC_ALL=C sort -u >"$S"

pass=0
ok()  { pass=$((pass+1)); printf 'PASS %s\n' "$1"; }
bad() { printf 'FAIL %s\n' "$1"; exit 1; }
note(){ printf 'note %s\n' "$1"; }

echo "=== 1. the string table exists and is not empty (calibrate the detector) ==="
n=$(wc -l <"$S")
[ "$n" -gt 10000 ] && ok "$n tokens extracted from the flashed kernel" \
	|| bad "only $n tokens - the detector is not calibrated"
for s in 'failed to load PPU microcode' 'zte/zx279133/mcode_intel.bin'; do
	grep -Fxq -e "$s" "$S" && ok "control: the NPPT driver really is in there ($s)" \
		|| bad "control failed: $s not found - the extraction is wrong"
done

echo
echo "=== 2. the vendor modules DO carry the protocol, so absence means something ==="
Z=pon/vendor-modules/zte_xgpon.ko
[ -f "$Z" ] || bad "$Z missing - the positive control cannot run"
t=$(mktemp)
LC_ALL=C tr -c '[:print:]' '\n' <"$Z" | grep -aE '.{4,}' | LC_ALL=C sort -u >"$t"
for s in 'power P2--->P3' 'power P5--->P6' 'Op State Machine' 'o2_3-o7' \
         'O4 p_o4_assigned_onu_id_msg' 'O7 p_o7_enable_sn_msg' \
         'fi_sendPloamMsg' 'fi_Configure_Ponmac_Send_Omci_Msg'; do
	grep -Fq -e "$s" "$t" && ok "zte_xgpon.ko carries: $s" || bad "zte_xgpon.ko lacks: $s"
done
rm -f "$t"

echo
echo "=== 3. np_133.ko is the vendor's microcode loader, and mainline cites it ==="
t=$(mktemp)
LC_ALL=C tr -c '[:print:]' '\n' < pon/vendor-modules/np_133.ko | grep -aE '.{4,}' | LC_ALL=C sort -u >"$t"
grep -Fq -e '/etc/mcode/mcode_133.bin' "$t" && ok "np_133.ko loads /etc/mcode/mcode_133.bin" \
	|| bad "np_133.ko no longer names the vendor microcode path"
grep -Fq -e 'np_downlaod_mcode' "$t" && ok "np_133.ko exports np_downlaod_mcode (vendor's own spelling)" \
	|| bad "np_133.ko no longer names np_downlaod_mcode"
rm -f "$t"
grep -Fq -e 'np_downlaod_mcode' staging/diag-20261014/kernelsrc/drivers_net_ethernet_zte_zx279133-np.c \
	&& ok "and the mainline driver cites that same symbol - the loader was written from it" \
	|| bad "main.c no longer cites np_downlaod_mcode"

echo
echo "=== 4. the flashed kernel has NO PON MAC protocol layer ==="
for s in 'PLOAM' 'ploam' 'Op State Machine' 'p_o7_enable_sn_msg' \
         'p_o4_assigned_onu_id_msg' 'fi_sendPloamMsg' 'Serena' \
         'fi_Configure_Ponmac_Send_Omci_Msg' 'Power State Machine'; do
	if grep -Fq -e "$s" "$S"; then
		bad "the flashed kernel DOES contain \"$s\" - a PON MAC may be built in after all"
	else
		ok "absent: $s"
	fi
done
# The vendor path must not be referenced either: mainline has its own blob.
grep -Fq -e '/etc/mcode' "$S" && bad "the kernel still references the vendor /etc/mcode path" \
	|| ok "absent: /etc/mcode (mainline has its own firmware blob)"

echo
echo "=== 5. so the even-handed wording must be gone from the docs ==="
INV=pon/INVENTORY.md
# The retracted sentence survives inside the retraction that quotes it, so a plain
# substring search is satisfied by the correction and stops meaning anything. Require
# that every occurrence sits on a line that also carries a retraction marker.
PH='either inside `mcode_intel\.bin` or absent'
MARK='first draft|was wrong|too generous|Rewritten|Corrected|rewritten'
left=$(grep -nE "$PH" "$INV" | grep -vE "$MARK" || true)
if [ -n "$left" ]; then
	echo "  lines asserting the old either/or without retracting it:"
	echo "$left" | sed 's/^/    /'
	bad "$INV still asserts the protocol is 'either in the microcode or absent'"
else
	n=$(grep -cE "$PH" "$INV" || true)
	ok "the old wording appears only where it is being retracted ($n mention(s))"
fi
grep -q 'zero occurrences' "$INV" \
	&& ok "and the document states the measurement" \
	|| bad "the measurement is not stated in $INV"
grep -q 'Op State Machine' "$INV" \
	&& ok "and quotes the token it actually searched for" \
	|| bad "$INV does not name the token that was searched"
grep -q 'zte_xgpon.ko' "$INV" \
	&& ok "and points at the module that does carry it" || bad "no pointer to zte_xgpon.ko"

echo
echo "=== NOT asserted, on purpose ==="
echo "  That the PPU microcode cannot do PON framing on its own. It carries packet-type"
echo "  and port-flow tables; what they encode is not readable from the header. Even if"
echo "  they do PON framing, the GEM/T-CONT/PLOAM/OMCI state machines would still have to"
echo "  exist as code, and none does. Deciding the microcode question needs a boot log."
echo
echo "ALL OK - $pass checks"
