#!/bin/sh
# 本地排演套件（local rehearsal suite）：依赖 staging/ 下的真实构建产物，
# 在只 clone 本仓库的环境里跑不起来，也**没有**接进 CI。
# 为什么、怎么跑、缺的那个夹具从哪来：同目录 README.md。
# Are the gates actually WIRED TO RUN?
#
# Their logic has been tested hard, but logic that is correct and never executes is
# indistinguishable from no gate at all - and a build that skips a gate reports success.
# Nothing so far has checked that the `if:` conditions can ever be true.
#
# The two gates in this workflow:
#   * the dtb content gate, embedded in "Apply SK-D840N overlays"
#   * "Gate: every dtb compatible has a driver in this kernel"
# and both depend on step "Skip by input mode" writing run=true.
set -u
cd /mnt/c/ugit/SK-D840N4
export LC_ALL=C
WF=ci-build-repo/.github/workflows/build-sk-d840n.yml
pass=0; fail=0
ok()  { printf '  PASS  %-52s %s\n' "$1" "${2:-}"; pass=$((pass+1)); }
bad() { printf '  FAIL  %-52s %s\n' "$1" "${2:-}"; fail=$((fail+1)); }
note(){ printf '  ....  %-52s %s\n' "$1" "${2:-}"; }

python3 - "$WF" <<'PY' > /tmp/wfsteps.txt
import sys, yaml
d = yaml.safe_load(open(sys.argv[1]))
job = d['jobs']['build']
print("MATRIX", job['strategy']['matrix']['mode'])
for i, s in enumerate(job['steps']):
    print("STEP\t%d\t%s\t%s" % (i, s.get('name', s.get('uses', '?')), s.get('if', '')))
PY
cat /tmp/wfsteps.txt | sed 's/^/  /'

echo
echo "=== 1. reproduce the 'Skip by input mode' logic for each dispatched input ==="
# the step body, verbatim in behaviour:
#   want=${inputs.mode}; [ -z "$want" ] && want=both
#   if [ "$want" = both ] || [ "$want" = "$matrix.mode" ]; then run=true else run=false
for want in both initramfs flash; do
	for mode in initramfs flash; do
		if [ "$want" = both ] || [ "$want" = "$mode" ]; then r=true; else r=false; fi
		printf '  inputs.mode=%-9s matrix.mode=%-9s -> run=%s\n' "$want" "$mode" "$r"
	done
done

echo
echo "=== 2. evaluate each step's if: for a flash-mode build (what our gates depend on) ==="
# `steps.pick.outputs.run` expands to a STRING in GitHub expressions, so `run` here must be
# the string 'true'/'false'. Using a Python bool made every `run == 'true'` false and the
# whole table came out SKIP - a broken harness looking exactly like a broken workflow.
python3 - "$WF" <<'PY'
import sys, re, yaml
d = yaml.safe_load(open(sys.argv[1]))
job = d['jobs']['build']
ctx = {'run': 'true', 'inputs': {'mode': 'both', 'clean_cache': 'false'}, 'matrix': {'mode': 'flash'}}
ctx['success'] = lambda: True
ctx['failure'] = lambda: False
ctx['cancelled'] = lambda: False
bad = []
for s in job['steps']:
    name = s.get('name', s.get('uses', '?'))
    cond = s.get('if')
    if cond is None:
        verdict, why = True, 'no condition - always runs'
    else:
        # `!` must only become `not` when it is a unary negation: a blanket replace turns
        # `!=` into ` not =`, which is a syntax error and made this step look unparsable.
        c = re.sub(r'!(?!=)', ' not ', cond)
        c = c.replace('&&', ' and ').replace('||', ' or ')
        c = (c.replace('steps.pick.outputs.run', "run")
              .replace('matrix.mode', "matrix['mode']")
              .replace('inputs.mode', "inputs['mode']")
              .replace('inputs.clean_cache', "inputs['clean_cache']"))
        try:
            verdict = eval(c, ctx)
            why = c
        except Exception as e:
            verdict = False; why = 'UNPARSABLE: %s' % e
    print("  %-4s %-50s %s" % ('run ' if verdict else 'SKIP', name[:50], why))
    if 'Gate' in name and not verdict:
        bad.append(name)
print()
print("  named gates that would NOT run in a flash build:", bad or "none")
sys.exit(1 if bad else 0)
PY
gates_ok=$?
if [ "$gates_ok" -eq 0 ]; then ok "every gate named 'Gate ...' runs in a flash build" ""
else bad "a gate would be skipped in a flash build" "see above"; fi

echo
echo "=== 3. and the reverse: a gate that only makes sense for a flash build must not run in initramfs mode ==="
# WHY THE RULE IS NOT "every Gate step carries matrix.mode == 'flash'".
#
# That was the rule here until 2026-10-16, and it was wrong. It flagged
# "Gate: the recovery service reached the image" (159a036) because the step
# carries no matrix.mode condition - and the correct fix was NOT to add one.
# That step's own body branches on the mode:
#
#   if [ "${{ matrix.mode }}" = initramfs ]; then   # zrec MUST be in the RAM image
#   else                                             # zrec must NOT be in the flash root
#
# Restricting it to matrix.mode == 'flash' would have deleted both halves: the
# RAM-image checks AND the flash-side "flash root carries no recovery service,
# as intended". So the invariant this section actually protects is narrower and
# more honest: a gate may fire in initramfs mode if it either stays away on its
# own, or decides for itself which mode it is in. What it may not do is fire in
# initramfs mode while checking flash-only things with no idea which mode it is.
#
# The classifier is exercised on four synthetic steps before it is allowed to
# rule on the real workflow. Without that, "no leaks" and "the classifier is
# broken" look identical - which is how this section came to be wrong in the
# first place.
python3 - "$WF" <<'PY'
import sys, re, yaml


def cond_true(cond, ctx):
    c = re.sub(r'!(?!=)', ' not ', cond)
    c = c.replace('&&', ' and ').replace('||', ' or ')
    c = (c.replace('steps.pick.outputs.run', "run")
             .replace('matrix.mode', "matrix['mode']")
             .replace('inputs.mode', "inputs['mode']")
             .replace('inputs.clean_cache', "inputs['clean_cache']"))
    try:
        return bool(eval(c, ctx))
    except Exception:
        return False


def leaks(step, ctx):
    """(is this a problem, why) for one step, evaluated in initramfs mode."""
    name = step.get('name', step.get('uses', '?'))
    cond = step.get('if')
    if cond is None or 'Gate' not in name:
        return False, 'not a gate, or no condition to leak'
    if not cond_true(cond, ctx):
        return False, 'stays away from initramfs mode on its own'
    if 'matrix.mode' in (step.get('run') or ''):
        return False, 'its body branches on matrix.mode, so it knows which mode it is in'
    return True, 'fires in initramfs mode with no idea it is not a flash build'


# --- the classifier's own test, before it is trusted with the real workflow
CTX = {'run': 'true', 'inputs': {'mode': 'both', 'clean_cache': 'false'},
       'matrix': {'mode': 'initramfs'}}
CTX['success'] = lambda: True
CTX['failure'] = lambda: False
CTX['cancelled'] = lambda: False
BRANCHING = 'if [ "${{ matrix.mode }}" = initramfs ]; then\n  true\nelse\n  true\nfi\n'
synthetic = [
    ('a gate with no mode logic at all must be flagged',
     {'name': 'Gate: bare', 'if': "steps.pick.outputs.run == 'true'"}, True),
    ('a gate restricted to flash must not be flagged',
     {'name': 'Gate: flash only', 'if': "steps.pick.outputs.run == 'true' && matrix.mode == 'flash'"}, False),
    ('a gate that branches on the mode itself must not be flagged',
     {'name': 'Gate: both modes', 'if': "steps.pick.outputs.run == 'true'", 'run': BRANCHING}, False),
    ('a non-gate step is not this section business',
     {'name': 'Package flash images', 'if': "steps.pick.outputs.run == 'true'"}, False),
]
bad = 0
for what, step, want in synthetic:
    got, why = leaks(step, CTX)
    mark = 'ok  ' if got == want else 'FAIL'
    if got != want:
        bad += 1
    print(f"  {mark} self-test: {what}  [{why}]")
if bad:
    print(f"  the classifier disagrees with itself on {bad} synthetic case(s) - refusing to rule on the workflow")
    sys.exit(2)

d = yaml.safe_load(open(sys.argv[1]))
job = d['jobs']['build']
leaked = []
for s in job['steps']:
    is_bad, why = leaks(s, CTX)
    name = s.get('name', s.get('uses', '?'))
    if is_bad:
        leaked.append(name)
        print(f"  LEAK  {name}: {why}")
    elif 'Gate' in name and cond_true(s.get('if') or '', CTX):
        print(f"  ok    {name}: {why}")
print("  gates that fire in initramfs mode without deciding which mode they are in:",
      leaked or "none")
sys.exit(1 if leaked else 0)
PY
rc=$?
if [ $rc -eq 0 ]; then ok "no gate checks flash-only things while unaware it is not a flash build" ""
elif [ $rc -eq 2 ]; then bad "the classifier failed its own self-test" "see above"
else bad "a gate leaks into initramfs mode" ""; fi

echo
echo "=== 4. the dtb content gate is not a named step - confirm its assertions are really here ==="
# It lives inside "Apply SK-D840N overlays". A rename of that step would not be caught by
# anything above, so match its actual assertions. Fixed-string: the workflow text contains a
# backslash before each dot, and a regex would be counting escape layers instead of content.
ov=$(sed -n '/- name: Apply SK-D840N overlays/,/- name: Cache dl/p' "$WF")
if [ -z "$ov" ]; then bad "located the 'Apply SK-D840N overlays' step" "sed range empty"
else
	ok "located the 'Apply SK-D840N overlays' step" "$(printf '%s\n' "$ov" | wc -l) lines"
	n_fail_markers=0
	for m in 'zx279133-sk-d840n.dtb' 'skyworth,sk-d840n' 'zte,zx279133-sr1010' \
	         'ipaddr "192\.168\.9\.1"' "tr -cd '\r'" '::error::'; do
		c=$(printf '%s\n' "$ov" | grep -cF -- "$m"); c=${c:-0}
		if [ "$c" -gt 0 ]; then
			printf '  PASS  assertion present: %-38s %s line(s)\n' "$m" "$c"
		else
			printf '  FAIL  assertion MISSING: %-38s\n' "$m"
			n_fail_markers=$(( n_fail_markers + 1 ))
		fi
	done
	if [ "$n_fail_markers" -eq 0 ]; then ok "every overlay-step assertion is present" ""
	else bad "overlay-step assertions" "$n_fail_markers missing"; fi
fi

echo
echo "=== pass=$pass fail=$fail ==="
rm -f /tmp/wfsteps.txt
[ "$fail" -eq 0 ] || exit 1
exit 0