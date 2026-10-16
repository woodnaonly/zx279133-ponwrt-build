#!/bin/sh
# 本地排演套件（local rehearsal suite）：依赖 staging/ 下的真实构建产物，
# 在只 clone 本仓库的环境里跑不起来，也**没有**接进 CI。
# 为什么、怎么跑、缺的那个夹具从哪来：同目录 README.md。
# The vendor's PON split is finer than "protocol in a module, rest in the microcode".
#
# np_133.ko - the module that LOADS the PPU microcode, and which binds zte,zx279133-nppt
# exactly as the mainline driver does - also exports the PON hardware table programming:
#
#   spa_pon_static_llid_set, epon_get_llid_state, xepon_get_llid_state
#   pon_sta_llid_{rx,tx,err}_stats_get, se_llid_stat_table_data_get
#   uopc_set_tcont_mode, uopc_set_init_tcont_req, uopc_set_tcont_sch_active_ena
#   tm_tcont_*  (352 tm_ functions: queue scheduling, sharp, secondary scheduler)
#   red_set_tcont_{in,out}_share_max
#   gemport_vlan_map_table_{get,update,del_by_node_info}
#   ppu_cluster_set_pkt_type_detail ("set ok. pkt_type_num = %d")
#   spa_set_pkt_type_table, ppu_set_dup_start_num, ppu_debug_hash_set_tbl
#   ppu_set_inst (the microcode loader itself)
#
# So the demux and shaping are CPU-programmed hardware tables that the microcode runs
# against. Against that, the prepared mainline tree programs only two of them:
#
#   present : packet-type table, dup table
#   absent  : LLID, T-CONT, GEM port, alloc-id, LPM, PLOAM, OpState
#
# That reframes the pending PON decision: the board measurement everyone was going to
# make ("does the microcode do GEM demux?") no longer decides anything, because mainline
# programs no LLID or T-CONT table for a microcode to act on. What is missing is code,
# and its absence is a fact about the tree, not a measurement.
set -u
cd /mnt/c/ugit/SK-D840N4 || exit 1
KO=pon/vendor-modules/np_133.ko

pass=0
ok()  { pass=$((pass+1)); printf 'PASS %s\n' "$1"; }
bad() { printf 'FAIL %s\n' "$1"; exit 1; }
note(){ printf 'note %s\n' "$1"; }

echo "=== 1. the vendor module really is the PON platform driver and the microcode loader ==="
[ -f "$KO" ] || bad "$KO missing"
# Printable-run extraction rather than `ponelf.py strings`, whose filter argument did not
# behave as its header suggests and silently matched nothing. The direct way was already
# proven earlier in this session.
kostr=$(mktemp)
LC_ALL=C tr -c '[:print:]' '\n' <"$KO" | grep -aE '.{4,}' | LC_ALL=C sort -u >"$kostr"
echo "  tokens in np_133.ko: $(wc -l <"$kostr")"
[ "$(wc -l <"$kostr")" -gt 1000 ] || bad "the string extraction produced almost nothing"
grep -Fxq -e 'zte,zx279133-pon' "$kostr" \
	&& ok "np_133.ko claims zte,zx279133-pon" || bad "np_133.ko no longer claims that node"
grep -Fxq -e 'zte,zx279133-nppt' "$kostr" \
	&& ok "and zte,zx279133-nppt - the same node the mainline driver binds" \
	|| bad "np_133.ko does not claim zte,zx279133-nppt"
rm -f "$kostr"
python3 tools/ponelf.py "$KO" symbols 'np_downlaod_mcode$' 2>/dev/null | grep -q 'np_downlaod_mcode' \
	&& ok "and exports np_downlaod_mcode" || bad "np_133.ko lost np_downlaod_mcode"
python3 tools/ponelf.py "$KO" xref 'np_downlaod_mcode$' 2>/dev/null | grep -q 'ppu_set_inst' \
	&& ok "whose job is to hand the blob to ppu_set_inst" || bad "np_downlaod_mcode no longer calls ppu_set_inst"

echo
echo "=== 2. it also programs the PON tables (so this is not just a loader) ==="
for s in spa_pon_static_llid_set uopc_set_tcont_mode \
         red_set_tcont_in_share_max gemport_vlan_map_table_update \
         ppu_cluster_set_pkt_type_detail spa_set_pkt_type_table \
         ppu_set_dup_start_num ppu_debug_hash_set_tbl; do
	python3 tools/ponelf.py "$KO" symbols "$s\$" 2>/dev/null | grep -q "$s" \
		&& ok "np_133.ko exports $s" || bad "np_133.ko lost $s"
done
n=$(python3 tools/ponelf.py "$KO" symbols '^tm_' 2>/dev/null | grep -c FUNC || true)
[ "$n" -gt 100 ] && ok "and a whole T-CONT queue scheduler: $n tm_ functions" \
	|| bad "only $n tm_ functions - the scheduler is not there"
n=$(python3 tools/ponelf.py "$KO" symbols '^llid|llid' 2>/dev/null | grep -c FUNC || true)
[ "$n" -gt 0 ] && ok "plus $n LLID functions" || bad "no LLID functions at all"

echo
echo "=== 3. mainline programs only the packet-type and dup tables ==="
out=$(python3 staging/diag-20261014/count_mainline_pon_tables.py 2>&1) || {
	echo "$out"; bad "the counter refused to run (calibration failed)"; }
echo "$out" | grep -q 'CALIBRATION FAILED' && { echo "$out" | head -8; bad "calibration failed"; }
ok "the counter is calibrated: np.c carries 129 PPU-vocabulary hits"
echo "$out" | grep -qE '^ +pkt type table +[1-9]' \
	&& ok "mainline programs the packet-type table" || bad "mainline does not touch the packet-type table"
echo "$out" | grep -qE '^ +dup table +[1-9]' \
	&& ok "mainline programs the dup table" || bad "mainline does not touch the dup table"

echo
echo "=== 4. and none of the PON provisioning tables ==="
for v in 'LLID' 'T-CONT' 'GEM port' 'alloc id' 'LPM' 'PLOAM' 'OpState'; do
	# The counter indents by four spaces, so `^ !` never matched and every vocabulary
	# looked "not reported absent" regardless of what the counter said.
	if echo "$out" | grep -qE "^ +! +$v +0\$"; then
		ok "absent from mainline: $v"
	elif echo "$out" | grep -qE "^ +$v +[1-9]"; then
		bad "$v now appears in mainline - the table layer may have been added"
	else
		bad "$v is not reported at all by the counter; the output shape changed"
	fi
done

echo
echo "=== 5. the docs must not still say the microcode question is what to measure ==="
INV=pon/INVENTORY.md
grep -q 'ppu_set_inst' "$INV" \
	&& ok "INVENTORY.md names ppu_set_inst (the vendor loader)" \
	|| note "INVENTORY.md does not yet mention ppu_set_inst"
grep -q 'program' "$INV" \
	&& ok "and talks about table programming" || note "no mention of table programming yet"

echo
echo "=== NOT asserted, on purpose ==="
echo "  That the microcode executes those tables as GEM/T-CONT demux. The symbols show"
echo "  the tables exist and the CPU programs them; not that the PPU interprets them as"
echo "  PON. But it no longer decides anything: mainline programs none of the tables, so"
echo "  there is nothing for the microcode to act on either way."
echo
echo "ALL OK - $pass checks"
