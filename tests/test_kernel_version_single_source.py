#!/usr/bin/env python3
# 本地排演套件（local rehearsal suite）：依赖 staging/ 下的真实构建产物，
# 在只 clone 本仓库的环境里跑不起来，也**没有**接进 CI。
# 为什么、怎么跑、缺的那个夹具从哪来：同目录 README.md。
"""One source of truth for the kernel version, and proof nothing else holds one.

`6.18.38` was spelled out in eight places across three workflow files, plus twice in text
that ships to the operator. A version bump meant editing ten strings, and missing one gave a
build that used one kernel and announced another - the same shape as the dtb finding (a
check, or a label, about something other than the thing shipped), one level up.

THE RULE THIS ENFORCES, which is not simply "no literals anywhere":

  GitHub does NOT expose the `env` context inside a step's `name:`, inside an
  `inputs.<id>.description:`, or inside an action's `with:` values. `${{ env.KVER }}`
  written in any of those places renders as an EMPTY STRING. So:

  * inside a `run:` block - executable - a path that names the kernel tree must read $KVER,
    which does come from the workflow-level env block;
  * everywhere else, a version may only appear as a literal, and that literal must equal
    KVER. The test enforces the equality, so a bump cannot leave the two apart.

It also checks the build-dir lookup, which used to be a bare `linux-*` glob: whatever the
build left in that directory got packaged and gated, while the release text still named the
pinned version.
"""
import os
import re
import subprocess
import sys
import tempfile

WF = '/mnt/c/ugit/SK-D840N4/ci-build-repo/.github/workflows'
FILES = ['build-sk-d840n.yml', 'build-sr1010.yml', 'diagnose-config.yml']
MAIN = 'build-sk-d840n.yml'

VER = re.compile(r'linux-(\d+\.\d+[\w.]*)')
BARE = re.compile(r'linux-\*')

fail = 0
checks = 0


def ok(m):
    global checks
    checks += 1
    print('PASS %s' % m)


def bad(m):
    global checks, fail
    checks += 1
    fail += 1
    print('FAIL %s' % m)


def note(m):
    print('note %s' % m)


def read(p):
    with open(p, encoding='utf-8') as f:
        return f.read()


def classify(lines):
    """Yield (lineno, text, in_run, is_comment).

    `#` is a comment inside a `run:` block - that is shell - and CONTENT inside a `body: |`
    or `description: |` block, where it is a Markdown heading. A single "starts with #" test
    is right in one and wrong in the other, and being wrong in the other silently skips
    exactly the release text this project keeps getting wrong. That is the seventh variant
    of the family: a heuristic that is correct somewhere and assumed everywhere.
    """
    block = None          # None | 'run' | 'scalar'
    block_indent = 0
    for i, raw in enumerate(lines, 1):
        stripped = raw.strip()
        if block is None:
            if not stripped:
                yield i, raw, False, False
                continue
            m = re.match(r'^(\s*)run:\s*\|', raw)
            if m:
                block, block_indent = 'run', len(m.group(1))
                yield i, raw, False, False
                continue
            m = re.match(r'^(\s*)(body|description):\s*\|', raw)
            if m:
                block, block_indent = 'scalar', len(m.group(1))
                yield i, raw, False, False
                continue
            yield i, raw, False, stripped.startswith('#')
            continue
        indent = len(raw) - len(raw.lstrip())
        if stripped and not stripped.startswith('#') and indent <= block_indent:
            block = None
            yield i, raw, False, stripped.startswith('#')
            continue
        yield i, raw, block == 'run', (block == 'run' and stripped.startswith('#'))


def find_body_leaks(lines, where='body'):
    """Lines inside a `body:`/`description:` block that would be published as Markdown.

    A `#` line there is not a comment: it is content, and `${{ ... }}` inside it is still
    evaluated. Returns a list of (lineno, reason, text).
    """
    out = []
    in_body = False
    body_indent = 0
    for i, raw in enumerate(lines, 1):
        stripped = raw.strip()
        if in_body:
            indent = len(raw) - len(raw.lstrip())
            if stripped and indent <= body_indent:
                in_body = False
            elif stripped.startswith('#') and not stripped.startswith('##'):
                out.append((i, 'a `#` line that is not a Markdown heading', stripped))
            elif 'env.KVER' in raw or 'test_kernel_version_single_source' in raw:
                out.append((i, 'an internal note leaked into the published text', stripped))
            continue
        m = re.match(r'^(\s*)(%s):\s*\|' % where, raw)
        if m:
            in_body = True
            body_indent = len(m.group(1))
    return out


def main():
    texts = {f: read(os.path.join(WF, f)) for f in FILES}

    print('=== 1. every workflow declares KVER exactly once, at workflow level ===')
    kvers = {}
    for f, t in texts.items():
        m = re.search(r"^  KVER:\s*'([^']+)'\s*$", t, re.M)
        if not m:
            bad('%s does not define KVER in env:' % f)
            continue
        n = len(re.findall(r"^  KVER:", t, re.M))
        if n != 1:
            bad('%s defines KVER %d times' % (f, n))
        else:
            ok('%s defines KVER: %s' % (f, m.group(1)))
        kvers[f] = m.group(1)
    if len(set(kvers.values())) > 1:
        bad('the three workflows disagree on the version: %s' % kvers)
    elif kvers:
        ok('all three agree: %s' % sorted(set(kvers.values()))[0])
    KVER = sorted(set(kvers.values()))[0] if kvers else ''

    print('\n=== 2. ${{ env.KVER }} must not appear: that context is absent there ===')
    # Comment lines are excluded for the same reason as in section 4: the explanatory notes
    # name `${{ env.KVER }}` precisely to record that it must not be used there, and a
    # checker that reads the sentence instead of the code reports its own documentation.
    hits = 0
    for f, t in texts.items():
        for i, raw, in_run, is_comment in classify(t.splitlines()):
            if is_comment:
                continue
            if not in_run and 'env.KVER' in raw:
                bad('%s:%d uses ${{ env.KVER }} outside a run: block, where GitHub does '
                    'not expose env - it would render empty' % (f, i))
                hits += 1
    if hits == 0:
        ok('no ${{ env.KVER }} in executable YAML; shell sites use $KVER, YAML sites use literals')

    print('\n=== 3. executable sites read $KVER; non-executable literals must equal KVER ===')
    bad_exec = 0
    for f, t in texts.items():
        for i, raw, in_run, is_comment in classify(t.splitlines()):
            if is_comment:
                continue
            for m in VER.finditer(raw):
                v = m.group(1)
                if in_run:
                    if 'linux-$KVER' not in raw:
                        bad('%s:%d is executable and hardcodes %s: %s'
                            % (f, i, m.group(0), raw.strip()[:70]))
                        bad_exec += 1
                elif v != KVER:
                    bad('%s:%d is a non-executable literal %s but KVER is %s'
                        % (f, i, m.group(0), KVER))
                    bad_exec += 1
    if bad_exec == 0:
        ok('every version mention is either $KVER in a run: block, or a literal equal to KVER')

    print('\n=== 4. the build-dir glob is pinned, not "whatever is there" ===')
    t = texts[MAIN]
    # Strip comment lines first: the explanatory comments name `linux-*` in order to say it
    # is gone, and counting those made this section report its own prose. That is the fifth
    # time in this project a check matched the sentence explaining the fix.
    # Section 4 looks only at executable lines, so it uses the classified view rather than a
    # blanket "drop lines starting with #" - which would also drop the release body heading.
    code = '\n'.join(l for i, l, in_run, is_c in classify(t.splitlines())
                     if in_run and not is_c)
    # `linux-*` is only forbidden where it SELECTS. A failure message that lists the whole
    # directory so the operator can see the mismatch must not be pinned - that is the whole
    # point of it - and such a listing is not inside a command substitution. So the rule is
    # about `$( ... )`, which is where a choice actually gets made.
    selecting = [l for l in code.splitlines() if BARE.search(l) and '$(' in l]
    listing = [l for l in code.splitlines() if BARE.search(l) and '$(' not in l]
    if selecting:
        for l in selecting:
            bad('build-sk-d840n.yml selects with a bare linux-* glob: %s' % l.strip()[:80])
    else:
        ok('no bare linux-* glob is used to select anything')
    if listing:
        ok('%d bare linux-* listing(s) kept - failure diagnostics, not selections' % len(listing))
    else:
        note('no bare linux-* listings either')
    n_pinned = len(re.findall(r'linux-\$KVER', code))
    if n_pinned >= 3:
        ok('%d build-dir lookups are pinned to linux-$KVER' % n_pinned)
    else:
        bad('only %d pinned lookups; expected at least 3' % n_pinned)
    if 'no build dir for linux-$KVER' in code:
        ok('and each refuses loudly, naming the version it wanted')
    else:
        bad('no loud refusal on a missing linux-$KVER directory')

    print('\n=== 5. the pinned lookup cannot be pre-empted by set -euo pipefail ===')
    blocks = re.findall(r'KT=\$\( \{ ([^\n]*) \} \| head -1 \)', code)
    if len(blocks) < 3:
        bad('expected at least 3 guarded KT lookups, found %d' % len(blocks))
    else:
        ok('%d guarded KT lookups' % len(blocks))
        for b in blocks:
            if '|| true' in b:
                ok('  and it carries the `|| true` that keeps set -e from pre-empting it')
            else:
                bad('  a KT lookup lacks the `|| true` guard: %s' % b.strip()[:70])

    print('\n=== 6. the pinned form actually selects the right directory ===')
    with tempfile.TemporaryDirectory() as td:
        bd = os.path.join(td, 'openwrt', 'build_dir', 'target-x', 'linux-zte_zx279133')
        os.makedirs(os.path.join(bd, 'linux-' + KVER))
        os.makedirs(os.path.join(bd, 'linux-0.0.0'))
        expr = ('KT=$( { ls -d openwrt/build_dir/target-*/linux-zte_zx279133/linux-%s '
                '2>/dev/null || true; } | head -1 ); printf "%%s" "$KT"' % KVER)
        out = subprocess.run(['sh', '-c', expr], cwd=td, capture_output=True,
                             text=True).stdout
        if out.endswith('linux-' + KVER):
            ok('with both versions present it selects linux-%s' % KVER)
        else:
            bad('it selected %r' % out)
        miss = subprocess.run(
            ['sh', '-c', expr.replace(KVER, '9.9.9')
             + '; [ -n "$KT" ] && echo NONEMPTY || echo EMPTY'],
            cwd=td, capture_output=True, text=True).stdout.strip()
        if miss == 'EMPTY':
            ok('and with the pinned version absent the check fires (KT empty)')
        else:
            bad('a missing pinned version still yielded something')
        bare = subprocess.run(
            ['sh', '-c',
             'ls -d openwrt/build_dir/target-*/linux-zte_zx279133/linux-* | head -1'],
            cwd=td, capture_output=True, text=True).stdout.strip()
        note('the old bare glob on this tree returned: %s' % (os.path.basename(bare) or '(nothing)'))
        note('  i.e. it would have taken linux-0.0.0 - which is the drift this pin removes')

    print('\n=== 7. implementation notes must not leak into a published body ===')
    # CALIBRATE FIRST: a detector that has never fired is not evidence. Feed it the exact
    # mistake this round made and require it to be caught.
    synthetic = [
        '      - name: Publish release',
        '        uses: some/action@v2',
        '        with:',
        '          body: |',
        '            ## SK-D840N - mainline 6.18.38',
        '            # ^ literal, not $KVER: the env context is absent here',
        '            - a real bullet',
    ]
    leaks = find_body_leaks(synthetic)
    if leaks:
        ok('CALIBRATION: the leak detector catches a `#` note inside body: (%s)'
           % leaks[0][1])
    else:
        bad('the leak detector finds nothing - section 7 would pass vacuously')

    clean = [
        '        with:',
        '          body: |',
        '            ## SK-D840N - mainline 6.18.38',
        '            - a real bullet',
        '      - name: next step',
    ]
    if find_body_leaks(clean):
        bad('the leak detector fires on a clean body')
    else:
        ok('CALIBRATION: and it stays quiet on a clean body')

    for f, t in texts.items():
        for i, reason, txt in find_body_leaks(t.splitlines()):
            bad('%s:%d %s: %s' % (f, i, reason, txt[:60]))
    ok('no `#` prose and no internal note inside any real body: block')

    print('\n=== rc=%d, %d checks ===' % (fail, checks))
    return 1 if fail else 0


if __name__ == '__main__':
    sys.exit(main())
