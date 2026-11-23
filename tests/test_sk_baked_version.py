#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Negative controls for the check that finds a version written into a decision.

The check (tools/check-baked-version.py) is a lint over this repository's own
scripts, so the first thing it has to get right is what counts as a finding.
A lint that flags every number that looks like a version is unusable - it
would be silenced by the allow list instead of by the fix - and a lint that
misses the shape the bug actually had is worse than no lint.

So both halves are checked here:

  * the shapes that are findings: a shell case arm, a comparison, a grep
    pattern, a pattern that expects a particular kernel's version line,
  * the shapes that are not: a version in a message, in a docstring, in an IP
    address, in a fwtool metadata version, in a variable that is only named.

And the allow list is checked as well: a place that is allowed because it is
about the vendor kernel stays allowed, and a place that is merely similar
does not sneak through.
"""
import json
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
GATE = ROOT / 'tools/check-baked-version.py'

CASES = []


def case(name):
    def wrap(fn):
        CASES.append((name, fn))
        return fn
    return wrap


def run(tmp, files, allow=None):
    """Run the check over a directory of synthetic scripts."""
    root = Path(tmp) / 'scripts'
    root.mkdir(exist_ok=True)
    for name, text in files.items():
        (root / name).write_text(text, encoding='utf-8', newline='\n')
    allow_path = Path(tmp) / 'allow.json'
    allow_path.write_text(json.dumps({'allowed': allow or []}),
                          encoding='utf-8', newline='\n')
    command = [sys.executable, str(GATE), '--root', str(root), '--allow', str(allow_path)]
    result = subprocess.run(command, capture_output=True, text=True)
    return result.returncode, result.stdout + result.stderr


@case('a clean script: nothing is found')
def t_clean(tmp):
    rc, out = run(tmp, {'a.sh': '#!/bin/sh\ncase "$v" in\n4.19*) : ;;\nesac\n'})
    assert rc == 1, out  # the vendor kernel is a finding unless it is allowed
    assert '4.19' in out, out


@case('a shell case arm with a version: it is found')
def t_case_arm(tmp):
    rc, out = run(tmp, {'a.sh': '#!/bin/sh\ncase "$KV" in\n7.3.4*) : ;;\nesac\n'})
    assert rc == 1, out
    assert 'a.sh:3' in out, out
    assert '7.3.4' in out, out


@case('a comparison with a version: it is found')
def t_comparison(tmp):
    rc, out = run(tmp, {'a.py': 'x = "6.18.38"\nassert x == "6.18.38"\n'})
    assert rc == 1, out
    assert '6.18.38' in out, out


@case('a grep pattern with a version: it is found')
def t_grep(tmp):
    rc, out = run(tmp, {'a.sh': "#!/bin/sh\ngrep -q 'kver=4.19.136+' \"$P\"\n"})
    assert rc == 1, out
    assert '4.19.136' in out, out


@case("a pattern that expects a kernel's version line: it is found")
def t_version_line(tmp):
    rc, out = run(tmp, {'a.sh': "#!/bin/sh\ncheck 'booted' 'Linux version 6\\.18\\.38' \"$WAIT\"\n"})
    assert rc == 1, out
    assert '6' in out and '18' in out, out


@case('a version in a message: it is not a finding')
def t_message(tmp):
    rc, out = run(tmp, {'a.sh': '#!/bin/sh\necho "the 6.18 generation does it this way"\n'})
    assert rc == 0, out
    assert 'no version is written into a decision' in out, out


@case('a version in a docstring: it is not a finding')
def t_docstring(tmp):
    rc, out = run(tmp, {'a.py': '"""The 6.18 generation does it this way."""\nprint(1)\n'})
    assert rc == 0, out


@case('an IP address: it is not a finding')
def t_ip(tmp):
    rc, out = run(tmp, {'a.sh': '#!/bin/sh\ncurl -s "http://192.168.9.1/" >/dev/null\n'})
    assert rc == 0, out


@case('a fwtool metadata version: it is not a finding')
def t_metadata_version(tmp):
    rc, out = run(tmp, {'a.py': 'require(metadata.get("metadata_version") == "1.1", "x")\n'})
    assert rc == 0, out


@case('a version in a variable that is only named: it is not a finding')
def t_variable(tmp):
    rc, out = run(tmp, {'a.sh': '#!/bin/sh\nFV=6.18.38+\necho "the fixture says $FV"\n'})
    assert rc == 0, out


@case('an allowed place stays allowed')
def t_allowed(tmp):
    rc, out = run(tmp, {'a.sh': '#!/bin/sh\ncase "$K" in\n4.19*) : ;;\nesac\n'},
                  allow=[{'file': 'scripts/a.sh', 'contains': '4.19*)',
                          'reason': 'the vendor kernel is 4.19'}])
    assert rc == 0, out
    assert 'allowed' in out, out


@case('an allowance for a different place does not cover this one')
def t_allow_does_not_match(tmp):
    rc, out = run(tmp, {'a.sh': '#!/bin/sh\ncase "$K" in\n4.19*) : ;;\nesac\n'},
                  allow=[{'file': 'scripts/other.sh', 'contains': '4.19*)',
                          'reason': 'the vendor kernel is 4.19'}])
    assert rc == 1, out
    assert '4.19' in out, out


@case('the allowance has to match the line, not only the file')
def t_allow_needs_the_line(tmp):
    rc, out = run(tmp, {'a.sh': '#!/bin/sh\ncase "$K" in\n7.3.4*) : ;;\nesac\n'},
                  allow=[{'file': 'scripts/a.sh', 'contains': '4.19*)',
                          'reason': 'the vendor kernel is 4.19'}])
    assert rc == 1, out
    assert '7.3.4' in out, out


@case('an allowance that matches nothing is reported as stale')
def t_stale_allowance(tmp):
    rc, out = run(tmp, {'a.sh': '#!/bin/sh\necho "nothing here"\n'},
                  allow=[{'file': 'scripts/gone.sh', 'contains': '4.19*)',
                          'reason': 'the vendor kernel is 4.19'}])
    assert rc == 0, out
    assert 'stale allowance' in out, out


@case('this repository itself is clean')
def t_repository(tmp):
    result = subprocess.run([sys.executable, str(GATE)], cwd=str(ROOT),
                            capture_output=True, text=True)
    out = result.stdout + result.stderr
    assert result.returncode == 0, out


def main():
    failures = 0
    for name, fn in CASES:
        with tempfile.TemporaryDirectory(prefix='sk-baked-version-') as tmp:
            try:
                fn(tmp)
            except AssertionError as exc:
                failures += 1
                print('FAIL  %s\n      %s' % (name, exc))
            else:
                print('ok    %s' % name)
    print('\n%d of %d control(s) behaved as required' % (len(CASES) - failures, len(CASES)))
    return 1 if failures else 0


if __name__ == '__main__':
    sys.exit(main())
