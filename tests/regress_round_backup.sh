#!/bin/sh
# 本地排演套件（local rehearsal suite）：依赖 staging/ 下的真实构建产物，
# 在只 clone 本仓库的环境里跑不起来，也**没有**接进 CI。
# 为什么、怎么跑、缺的那个夹具从哪来：同目录 README.md。
# Regression sweep after the conf_tar change: the tar -z fix, the check_image gate, the
# new config-preservation test, plus syntax and line-ending hygiene.
set -u
# 自己所在的目录。必须在下面那句 cd 之前算好，否则 cd 之后它就指错了地方。
d=$(cd "$(dirname "$0")" && pwd)
cd /mnt/c/ugit/SK-D840N4
export LC_ALL=C

SH=ci-build-repo/overlays/target/linux/zte/zx279133/base-files/lib/upgrade/platform.sh
rc_all=0

echo "=== 1. syntax ==="
if sh -n "$SH" 2>&1; then echo "  sh -n: OK"; else echo "  sh -n: FAIL"; rc_all=1; fi

echo
echo "=== 2. line endings (CR must be 0; .gitattributes pins LF for this path) ==="
cr=$(tr -cd '\r' <"$SH" | wc -c)
echo "  CR bytes: $cr"
[ "$cr" -eq 0 ] || rc_all=1
grep -n 'platform.sh' .gitattributes ci-build-repo/.gitattributes 2>/dev/null | sed 's/^/  /'

echo
echo "=== 3. sh -n on every diag script touched this round ==="
for f in $d/test_upgrade_backup.sh \
         $d/copy_config_check.sh \
         $d/sysupgrade_locate.sh \
         $d/upgrade_backup_var.sh \
         $d/upgraded_identity.sh \
         $d/upgrade_backup_origin.sh \
         $d/upgraded_strings_check.sh \
         $d/sysupgrade_conf_check.sh \
         $d/test_sysupgrade_bytes.sh \
         $d/kernel_mismatch_probe.sh \
         $d/kernel_payload_compare.sh; do
	if sh -n "$f" 2>/dev/null; then echo "  OK   ${f##*/}"; else echo "  FAIL ${f##*/}"; rc_all=1; fi
done

echo
echo "=== 3b. CR hygiene on the files edited for the release ==="
for f in ci-build-repo/templates/flash-sk-d840n.sh \
         ci-build-repo/templates/trial-kexec.sh \
         tools/flash-direct.sh tools/flash-write.sh tools/simwrite.sh \
         tools/mkrollback.sh tools/checkrollback.sh tools/preflight.sh \
         tools/payload-id.sh tools/fdtwalk.py tools/check-imagelineage.sh \
         $d/fdt2dts.py \
         $d/test_phy_gate_symbols.py \
         $d/test_phy_gate_symbols.sh \
         $d/test_dts_content_gate.sh \
         $d/test_gate_ships_what_it_checks.sh \
         $d/test_release_body_claims.sh \
         $d/test_kernel_version_single_source.sh \
         $d/test_kernel_version_single_source.py \
         $d/test_workflow_shell_parses.sh \
         $d/test_workflow_shell_parses.py \
         $d/round29_real_compat_scan.py \
         ci-build-repo/.github/workflows/build-sr1010.yml \
         ci-build-repo/.github/workflows/diagnose-config.yml \
         ci-build-repo/.github/workflows/build-sk-d840n.yml; do
	c=$(tr -cd '\r' <"$f" | wc -c)
	if [ "$c" -eq 0 ]; then echo "  OK   ${f##*/} (0 CR)"; else echo "  FAIL ${f##*/} ($c CR)"; rc_all=1; fi
done

echo
echo "=== 4. pre-existing suites must still pass ==="
ran=0
verdicts=0
for t in $d/test_platform_sh.sh \
         $d/test_platform_check.sh \
         $d/test_upgrade_backup.sh \
         $d/test_sysupgrade_bytes.sh \
         $d/test_flash_script.sh \
         $d/test_flash_direct.sh \
         $d/test_simwrite_pairs.sh \
         $d/test_trial_kexec.sh \
         $d/test_overlay_delivery.sh \
         $d/overlay_path_proof.sh \
         $d/rollback_truth.sh \
         $d/test_mkrollback_reproducible.sh \
         $d/test_workflow_packaging.sh \
         $d/test_gates_reachable.sh \
         $d/test_checkrollback_tool.sh \
         $d/test_root_untouched.sh \
         $d/test_payload_id.sh \
         $d/test_tar_z_discriminator.sh \
         $d/test_net_config_reach.sh \
         $d/test_ramroot_deps.sh \
         $d/test_flash_integrity_e2e.sh \
         $d/test_led_beacon.sh \
         $d/test_kernel_source_premise.sh \
         $d/test_network_binding.sh \
         $d/test_pon_datapath.sh \
         $d/test_eth0_reachability.sh \
         $d/test_imagelineage.sh \
         $d/test_imagelineage2.sh \
         $d/test_preflight_4d_verbatim.sh \
         $d/test_pon_protocol_absence.sh \
         $d/test_pon_table_layer.sh \
         $d/test_gate_dtc_listform.sh \
         $d/test_gate_endtoend.sh \
         $d/test_dts_content_gate.sh \
         $d/test_phy_gate_symbols.sh \
         $d/test_gate_ships_what_it_checks.sh \
         $d/test_release_body_claims.sh \
         $d/test_kernel_version_single_source.sh \
         $d/test_workflow_shell_parses.sh \
         $d/test_readme_refs.sh; do
	echo "  --- ${t##*/}"
	ran=$((ran+1))
	if sh "$t" >/tmp/reg.out 2>&1; then
		# anchor on the report prefix: a section title containing the word "FAIL"
		# is not a failing assertion
		verdicts=$((verdicts+1))
		p=$(grep -c '^  PASS  ' /tmp/reg.out); f=$(grep -c '^  FAIL  ' /tmp/reg.out)
		if [ "$p" -eq 0 ] && [ "$f" -eq 0 ]; then
			echo "      exit 0  | (own verdict format, no PASS/FAIL markers - exit code is the signal)"
		else
			echo "      exit 0  | $p PASS, $f FAIL"
		fi
	else
		verdicts=$((verdicts+1))
		echo "      NONZERO EXIT"
		grep -E 'FAIL|REJECT|rc=' /tmp/reg.out | head -15 | sed 's/^/        /'
		rc_all=1
	fi
done

echo
echo "=== 4b. the release workflow still parses, and the gate's own verdict block ==="
if python3 $d/check_workflow_yaml.py >/tmp/yaml.out 2>&1; then
	sed 's/^/      /' /tmp/yaml.out
else
	echo "      YAML PARSE FAILED"; sed 's/^/        /' /tmp/yaml.out; rc_all=1
fi
# Runs the gate's fallback-verdict block extracted verbatim from the workflow, against
# the real kernel's strings, plus negative controls. Needs no dtc.
if python3 $d/run_gate_fallback_verbatim.py >/tmp/gatefrag.out 2>&1; then
	grep -E '^(ok|note|::error)' /tmp/gatefrag.out | sed 's/^/      /'
	grep -E 'negative control|    ok: it said note|    FAIL' /tmp/gatefrag.out | sed 's/^/      /'
else
	echo "      GATE FRAGMENT FAILED"; tail -20 /tmp/gatefrag.out | sed 's/^/        /'; rc_all=1
fi

echo
echo "=== 5. no stray CR in the diag scripts either ==="
for f in $d/*.sh; do
	c=$(tr -cd '\r' <"$f" | wc -c)
	[ "$c" -eq 0 ] || { echo "  CR in ${f##*/}: $c"; rc_all=1; }
done
echo "  (blank above = all clean)"

echo
echo "=== 6. preflight still runs offline and step 9c is what stops it ==="
# Not optional: a gate that is never reached in the offline rehearsal is a gate whose
# behaviour under time pressure is unknown.
if sh $d/check_preflight_9c.sh >/tmp/pfcheck.out 2>&1; then
	# An empty section here reads as "the gate was never exercised". So print what the
	# suite actually emits: its own stop line and its own verdict. (The previous pattern
	# asked for two leading spaces, which this suite does not produce - it printed nothing
	# on a passing run, for two rounds, and nobody noticed.)
	grep -E 'stopped at|ALL OK|FAILED|checks$' /tmp/pfcheck.out | sed 's/^/    /'
	grep -qE 'ALL OK' /tmp/pfcheck.out || { echo "    it exited 0 without an ALL OK line"; rc_all=1; }
else
	echo "    FAILED"; tail -25 /tmp/pfcheck.out | sed 's/^/      /'; rc_all=1
fi

echo
echo "=== 7. this log accounts for itself ==="
# Twice now the suite count I reported did not match the count in the log, both times
# because the counting script was wrong. So the log now states the number itself, and
# asserts that every suite it ran left exactly one verdict behind.
# Note: the expected count is derived from the loop body with grep -o, never with tr.
# tr -s ' \t' in single quotes has a backslash and a 't' in its delimiter set, so it
# shreds any path containing "t" - that is what silently dropped the last suite.
# Read the file by its path from the repo root, not by "$0": this script cd's to the
# root, so $0 is a bare filename that no longer resolves (it printed
# "sed: can't read regress_round_backup.sh" and then compared against 0).
self=$d/regress_round_backup.sh
[ -f "$self" ] || { echo "      cannot find $self to read my own loop list"; rc_all=1; }
# 循环头已经从 `for t in staging/diag-20261014/...` 变成 `for t in $d/...`，
# 所以范围起点必须跟着改。留着旧的 /^for t in staging/ 的话，这段会数出 0，
# 然后自己判自己 MISMATCH——一个数错分母的自检比没有自检更坏。
expected=$(sed -n '/^for t in \$d\//,/; do$/p' "$self" | grep -o '[A-Za-z0-9_.-]*\.sh' | wc -l | sed 's/[[:space:]]//g')
case "$expected" in
	''|*[!0-9]*) echo "      could not count the loop entries (got '$expected')"; rc_all=1 ;;
esac
echo "      suites listed in the loop: $expected"
echo "      suites actually run:       $ran"
echo "      verdicts recorded:         $verdicts"
if [ "$ran" -ne "$expected" ]; then
	echo "      MISMATCH: the loop ran $ran of $expected"
	rc_all=1
else
	echo "      ok: the loop ran every suite it lists"
fi
if [ "$verdicts" -ne "$ran" ]; then
	echo "      MISMATCH: ran $ran, but only $verdicts verdict line(s) were recorded"
	rc_all=1
else
	echo "      ok: every suite left a verdict"
fi

echo
echo "=== rc_all=$rc_all ==="
exit $rc_all