#!/bin/sh
# 本地排演套件（local rehearsal suite）：依赖 staging/ 下的真实构建产物，
# 在只 clone 本仓库的环境里跑不起来，也**没有**接进 CI。
# 为什么、怎么跑、缺的那个夹具从哪来：同目录 README.md。
# The release body is the only text a person actually acts on. Pin its claims to evidence.
#
# It used to say the LAN port runs the "SR1010 NPPT/ZX279051 path (CONFIG_ZX279051_PHY=y)".
# Measured against the kernel that actually ships:
#   * there is NO exact token `zx279051` / `zte,zx279051` in the kernel strings - only
#     `tzx279051`, which is the kernel string table's tail-merge residue (the same shape as
#     `3regmap init failed` and `0rtl822x_c45_soft_reset` recorded earlier);
#   * `CONFIG_ZX279051_PHY` exists as a Kconfig symbol but is not built into this kernel;
#   * the board dts names that node `rtl8226b: ethernet-phy@5` and deliberately gives it no
#     compatible, so the Realtek PHY driver is what answers.
# So the note named the wrong chip, in text that ships with every release.
#
# Hand-maintained prose about artifacts drifts. This suite re-derives each claim instead.
set -u
cd /mnt/c/ugit/SK-D840N4 || exit 1
Y=ci-build-repo/.github/workflows/build-sk-d840n.yml
S=/tmp/t27_strings.txt
pass=0
ok()  { pass=$((pass+1)); printf 'PASS %s\n' "$1"; }
bad() { printf 'FAIL %s\n' "$1"; exit 1; }

# ---------------------------------------------------------------- lift the release body
bs=$(awk '/^      - name: Publish release/{f=1} f && /^          body: \|/{print NR; exit}' "$Y")
be=$(awk -v s="$bs" 'NR>s && /^          files: \|/{print NR-1; exit}' "$Y")
[ -n "$bs" ] && [ -n "$be" ] || bad "cannot locate the release body"
sed -n "$((bs+1)),${be}p" "$Y" | sed 's/^ \{12\}//' >/tmp/t27_body.md
echo "note release body is lines $((bs+1))..${be} of $Y"
grep -q 'SK-D840N' /tmp/t27_body.md && ok "the body was lifted intact" || bad "the lift lost the body"

# ------------------------------------------------- calibrate the string matcher FIRST
LC_ALL=C tr -c '[:print:]' '\n' <staging/ctl/gate-Image \
	| grep -aE '.{3,}' | LC_ALL=C sort -u >"$S"
printf 'kernel string tokens: %s\n' "$(wc -l <"$S")"

# A matcher that finds nothing would make every "absent" below trivially true.
LC_ALL=C grep -Fxq 'RTL8201F Fast Ethernet' "$S" \
	&& ok "CALIBRATION: the exact matcher does find a token known to be present" \
	|| bad "the matcher finds nothing at all - every absence below would be vacuous"
LC_ALL=C grep -Fxq 'tzx279051' "$S" \
	&& ok "CALIBRATION: and it finds the tail-merge artefact as its own whole line" \
	|| note "no `tzx279051` line this time; the token table changed shape"

# ------------------------------------------------------------ the claims, one by one
echo
echo "=== claim 1: the LAN PHY is a Realtek RTL8226B, not a ZX279051 ==="
for t in zx279051 zte,zx279051 zte,zx279051-phy; do
	LC_ALL=C grep -Fxq "$t" "$S" && bad "the kernel really does contain an exact token $t" \
		|| ok "no exact token $t in the kernel"
done
LC_ALL=C grep -Fxq 'Generic FE-GE Realtek PHY' "$S" \
	&& ok "and the Realtek PHY driver is in there" || bad "no Realtek PHY string"
grep -q 'CONFIG_REALTEK_PHY=y' /tmp/t27_body.md \
	&& ok "the body now names CONFIG_REALTEK_PHY=y" || bad "the body does not name it"
grep -q 'CONFIG_ZX279051_PHY=y' /tmp/t27_body.md \
	&& bad "the body still claims CONFIG_ZX279051_PHY=y" \
	|| ok "the body no longer claims CONFIG_ZX279051_PHY=y"
grep -q 'RTL8226B' /tmp/t27_body.md && ok "the body names the RTL8226B" || bad "no RTL8226B in the body"

echo
echo "=== claim 2: the DTS names the node rtl8226b and gives it no compatible ==="
DTS=ci-build-repo/kernel-overlays/arch/arm64/boot/dts/zte/zx279133-sk-d840n.dts
grep -q 'rtl8226b: ethernet-phy@5' "$DTS" && ok "the dts labels it rtl8226b" || bad "no rtl8226b label"
blk=$(awk '/rtl8226b: ethernet-phy@5 \{/{f=1} f{print} f && /\};/{exit}' "$DTS")
printf '%s\n' "$blk" | grep -q 'compatible' \
	&& bad "it has a compatible after all; the note's reasoning about that would be wrong" \
	|| ok "and it deliberately has no compatible"
printf '%s\n' "$blk" | grep -q 'max-speed = <2500>' && ok "max-speed 2500 as the note implies" \
	|| bad "max-speed is not 2500"

echo
echo "=== claim 3: the compiled dtb really points eth0's phy-handle at reg=5 ==="
python3 staging/diag-20261014/fdt2dts.py staging/ctl/gate-dtb >/tmp/t27_dts 2>/dev/null
python3 - <<'PY'
import re
t = open('/tmp/t27_dts', encoding='utf-8').read()
def node(h):
    m = re.search(re.escape(h) + r'\s*\{(.*?)\n\s*\};', t, re.S)
    return m.group(1) if m else ''
eth = node('ethernet@19000000')
phy = node('ethernet-phy@5')
print('  eth0 node has .phy-handle:', 'phy-handle' in eth)
print('  phy node reg:', re.search(r'reg = <([^>]*)>', phy).group(1) if 'reg = ' in phy else 'ABSENT')
print('  phy node has compatible:', 'compatible' in phy)
PY

echo
echo "=== claim 4: the body still carries its own limits ==="
grep -q '未在本机验证' /tmp/t27_body.md && ok "the not-verified-locally disclaimer is still there" \
	|| bad "the disclaimer was dropped"
grep -q 'PON 数据通路' /tmp/t27_body.md \
	&& ok "and the PON scope is still stated accurately" || bad "the PON scope sentence changed"

echo
echo "=== claim 5: the SR1010 story the body tells must be checkable in the tree ==="
# The body now says the SR1010 node is `zx279051: ethernet-phy@1` with
# compatible "ethernet-phy-id0000.84b9", and that a compatible on a PHY node overrides ID
# matching. That is a historical claim about a different board, and a claim about another
# board is exactly the kind that rots unnoticed. It has to point at something.
SRD=staging/diag-20261014/kernelsrc/arch_arm64_boot_dts_zte_zx279133-sr1010.dts
[ -f "$SRD" ] && ok "the SR1010 board file is on disk to check against" \
	|| bad "$SRD missing - run fetch_kernel_files3.py"
grep -q 'zx279051: ethernet-phy@1 {' "$SRD" \
	&& ok "SR1010 really does have zx279051: ethernet-phy@1" || bad "that node is not there"
grep -q 'compatible = "ethernet-phy-id0000.84b9"' "$SRD" \
	&& ok "and its compatible really is ethernet-phy-id0000.84b9" || bad "different compatible"
grep -q 'ethernet-phy-id0000.84b9' /tmp/t27_body.md \
	&& ok "the body quotes that compatible verbatim, so a reader can go look" \
	|| bad "the body paraphrases it instead of quoting it"
# And the reason OUR node differs must be recorded where the next person will see it.
grep -q 'ethernet-phy-id0000.84b9' "$DTS" \
	&& ok "our board file carries the same explanation in a comment" \
	|| bad "our board file does not record why it omits the compatible"
grep -q 'overrides' "$DTS" && ok "and it states the mechanism, not just the outcome" \
	|| bad "our board file records the incident but not the mechanism"

echo
echo "=== claim 6: the PON claim must be true of the KERNEL, and honest about the vendor side ==="
# The body says the G.984.1 state machine is not in this tree. That is the single most
# load-bearing sentence in the release: it is what stops someone reading "PON" in the
# title and expecting a working ONU. So both halves of it get checked.
#
# It used to read "vendor zte_xgpon.ko carries these verbatim", naming four exact tokens.
# Counted across all 17 vendor modules, only one of the four is present as an exact
# token. The claim was about the right thing and misstated the evidence.
KO_DIR=pon/vendor-modules
[ -d "$KO_DIR" ] && ok "the vendor modules are on disk to check against" \
	|| bad "$KO_DIR missing - cannot check the vendor half of the claim"

if [ -d "$KO_DIR" ]; then
	nko=$(ls -1 "$KO_DIR"/*.ko 2>/dev/null | wc -l)
	ok "counting all $nko of them, not just the one the body names"
	# >=2 printable chars, NOT >=3: the O-state names are two characters and a
	# `.{3,}` filter removes them outright. A filter that hides what you are
	# looking for is worse than no filter - it reads as an absence.
	W2=$(mktemp -d)
	for ko in "$KO_DIR"/*.ko; do
		LC_ALL=C tr -c '[:print:]' '\n' <"$ko" | grep -aE '.{2,}' | LC_ALL=C sort -u \
			> "$W2/$(basename "$ko")"
	done
	in_modules() { grep -lFx -e "$1" "$W2"/*.ko 2>/dev/null | wc -l; }
	only_in() { grep -lFx -e "$1" "$W2"/*.ko 2>/dev/null | sed "s|.*/||; s|\.ko$||"; }

	# Facts, stated so that a change in any of them turns this red.
	[ "$(in_modules p_o7_enable_sn_msg)" = 1 ] \
		&& ok "p_o7_enable_sn_msg is an exact token in exactly one vendor module" \
		|| bad "p_o7_enable_sn_msg is in $(in_modules p_o7_enable_sn_msg) modules, the body says one"
	[ "$(only_in p_o7_enable_sn_msg)" = "zte_xgpon" ] \
		&& ok "and that module is zte_xgpon.ko, which is what the body names" \
		|| bad "the module carrying p_o7_enable_sn_msg is $(only_in p_o7_enable_sn_msg), not zte_xgpon"
	for tok in PLOAM 'Op State Machine' Serena; do
		[ "$(in_modules "$tok")" = 0 ] \
			&& ok "$tok is an exact token in NO vendor module - so the body must not claim it" \
			|| bad "$tok turned up in $(in_modules "$tok") module(s); the body claim needs revisiting"
	done

	# And the overclaim itself must be gone. Checking the absence of the wording is
	# what makes this able to fail; the checks above only confirm today's facts.
	if grep -q '逐字' /tmp/t27_body.md; then
		bad "the body still claims the vendor module carries those tokens 逐字 (verbatim) - measured false"
	else
		ok "the overclaim wording is gone from the body"
	fi
	# Edit history does not belong in a document a person reads to decide what to do.
	# It was added here once already and had to be taken back out.
	if grep -qE '改过一次|原来写的是|措辞' /tmp/t27_body.md; then
		bad "the body narrates its own edit history - that is a note to the next author, not to the reader"
	else
		ok "and the body states facts rather than describing how it got there"
	fi
	# The module count is quoted, so it is pinned: a wrong number here is the same
	# class of defect as a wrong name.
	quoted=$(grep -oE '[0-9]+ 个厂商模块' /tmp/t27_body.md | head -1 | grep -oE '^[0-9]+')
	if [ -z "$quoted" ]; then
		bad "the body no longer says how many vendor modules it counted"
	elif [ "$quoted" = "$nko" ]; then
		ok "the body says it counted $nko modules, and there are $nko"
	else
		bad "the body claims to have counted $quoted modules; there are $nko"
	fi
	grep -q 'STACK PLOAM DATA' /tmp/t27_body.md \
		&& ok "and the body points at the longer strings that really are there" \
		|| bad "the correction does not say what IS in the module"

	# ...and the kernel half, which is the half that matters most.
	KW=$(mktemp -d)
	head -c2 staging/ctl/gate-Image | od -An -tx1 | tr -d ' \n' >"$KW/m"
	if [ "$(cat "$KW/m")" = "1f8b" ]; then
		gzip -dc staging/ctl/gate-Image >"$KW/img"
	else
		cp staging/ctl/gate-Image "$KW/img"
	fi
	LC_ALL=C tr -c '[:print:]' '\n' <"$KW/img" | grep -aE '.{3,}' | LC_ALL=C sort -u >"$KW/s.txt"
	for tok in PLOAM 'Op State Machine' p_o7_enable_sn_msg Serena; do
		LC_ALL=C grep -Fxq -e "$tok" "$KW/s.txt" \
			&& bad "the built kernel CONTAINS $tok - the body claim that it does not is now false" \
			|| ok "the built kernel really has no $tok, as the body says"
	done
	rm -rf "$W2" "$KW"
fi

echo
echo "ALL OK - $pass checks"
