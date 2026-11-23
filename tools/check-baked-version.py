#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Find the places where a kernel version is written into a decision.

    python3 tools/check-baked-version.py [--root DIR]... [--allow FILE]

Why this exists
---------------
Three gates in this project once refused the wrong image for the same reason:
they had the kernel's version written into them as a literal.  The flash gate
read `case "$KV" in 6.*)`, the gate payload rode `case "$(uname -r)" in 6.*)`,
and the pre-flash gate asked whether the kernel announces "6.18.38".  All three
were right about the release line and wrong about every other one, and the
wrongness is silent: the gate refuses a good image and says nothing about why.

The same shape of mistake is waiting in the scripts that decide whether the
*running* board is on the mainline kernel - "the board answered '7.3.4' - not
the mainline kernel" is a sentence a script can say about a perfectly good
board.

What is found
-------------
A version literal in a **decision position**: a shell case arm, the pattern of
a `case`, a string that is an operand of a comparison or a membership test, or
a string that is an argument to a pattern function (grep, match, startswith,
...).  A literal that is only in a message, in a docstring or in prose is not
a finding - "the 6.18 generation" in an error message says which generation
the message is about, it does not decide anything.

The major version has to look like a kernel's (4 to 99), which keeps the
fwtool metadata versions ("1.1", "1.0") and the IP addresses out of the way.

What is found has to be listed in the allow list with a reason, or the check
fails.  The allow list is the record of the places where a version really
belongs: the vendor kernel is 4.19 and always will be, so the places that
refuse it are allowed to say so.
"""
import argparse
import ast
import json
import re
import sys
from pathlib import Path

# A kernel version: 6.18, 6.18.38, 7.3.4, 4.19*.  The dots may be escaped,
# because a version inside a grep pattern is written "6\\.18\\.38".  The major
# version has to look like a kernel's; an IP address does not match because the
# digits around the dots are part of one number.
VERSION = re.compile(r'(?<![\w.])((?:[4-9]|\d{2})\\?\.\d+(?:\\?\.\d+)?\*?)(?![\w.])')

# A shell case arm: the line begins with the pattern.
CASE_ARM = re.compile(r'^\s*[\w*."$@\[\]{}/+-]*\)(?:\s|$)')
# A shell case statement: the pattern is between " in " and ")".
CASE_START = re.compile(r'^\s*case\b')
# A quoted string, with its quotes.
STRING = re.compile(r'"([^"\n]*)"|\'([^\'\n]*)\'')
# What can follow a string for it to be an operand.
OPERANDS = re.compile(r'^\s*(==|!=|=~|\bin\b)')
# What can precede a string for it to be an operand: a comparison.
OPERATOR_BEFORE = re.compile(r'(?<![=!<>])(==|!=|=~)\s*$')
# What can precede a string for it to be a pattern argument.  The flags of a
# grep sit between the name and the pattern ("grep -q '...'"), and a call has
# its parenthesis in between ("startswith('...')"), so both are allowed.
PATTERN_ARG = re.compile(r'(grep|match|search|startswith|endswith|find)\b[^()]*\(?\s*$')

SUFFIXES = ('.sh', '.py', '.ps1')
SKIP_DIRS = {'.git', '__pycache__', 'node_modules', '.venv'}


def shell_code_lines(text):
    """(number, text) for the code lines of a shell script."""
    for n, raw in enumerate(text.splitlines(), 1):
        line = raw.rstrip()
        if not line.strip() or line.lstrip().startswith('#'):
            continue
        out = []
        quote = None
        for i, ch in enumerate(line):
            if quote:
                out.append(ch)
                if ch == quote:
                    quote = None
                continue
            if ch in '"\'':
                quote = ch
                out.append(ch)
                continue
            if ch == '#' and (i == 0 or line[i - 1] in ' \t'):
                break
            out.append(ch)
        code = ''.join(out).rstrip()
        if code.strip():
            yield n, code


def python_code_lines(text):
    """(number, text) for the code lines of a python file, docstrings removed.

    A docstring is prose about the version; a string that is an argument of a
    call is the version being decided about.  The ast tells them apart.
    """
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return shell_code_lines(text)
    spans = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef,
                             ast.ClassDef)):
            body = getattr(node, 'body', None)
            if not body:
                continue
            first = body[0]
            if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant) \
                    and isinstance(first.value.value, str):
                spans.append((first.lineno, first.end_lineno))
    for n, raw in enumerate(text.splitlines(), 1):
        line = raw.rstrip()
        if not line.strip():
            continue
        if any(lo <= n <= hi for lo, hi in spans):
            continue
        if line.lstrip().startswith('#'):
            continue
        yield n, line


def code_lines(path):
    text = path.read_text(encoding='utf-8', errors='replace')
    if path.suffix == '.py':
        return python_code_lines(text)
    return shell_code_lines(text)


def decision_spans(line):
    """The ranges of the line that are used in a decision."""
    spans = []
    if CASE_ARM.match(line):
        end = line.find(')')
        if end > 0:
            spans.append((0, end))
    start = CASE_START.match(line)
    if start:
        after = line.find(' in ', start.end())
        if after >= 0:
            end = line.find(')', after)
            if end > after:
                spans.append((after + 4, end))
    for match in STRING.finditer(line):
        begin, finish = match.start(), match.end()
        before = line[:begin]
        after = line[finish:]
        body = match.group(0)
        # A string that carries a grep command of its own is a pattern
        # argument even when the grep is inside the quotes.
        if 'grep' in body:
            spans.append((begin + 1, finish - 1))
            continue
        # A pattern that expects a particular kernel's version line.
        if 'Linux version' in body:
            spans.append((begin + 1, finish - 1))
            continue
        if OPERANDS.match(after) or PATTERN_ARG.search(before) \
                or OPERATOR_BEFORE.search(before):
            spans.append((begin + 1, finish - 1))
    return spans


def load_allow(path):
    if not path:
        return []
    try:
        data = json.loads(Path(path).read_text(encoding='utf-8'))
    except (OSError, ValueError) as exc:
        raise SystemExit('%s: cannot read the allow list: %s' % (path, exc))
    return data.get('allowed') or []


def allowed_hit(allow, file_name, line):
    """The reason this hit is allowed, if it is.

    The file is matched on its tail, not on its whole path: the same script
    exists in the repository and in the bench's working directory, and both
    copies are the same place as far as this question goes.
    """
    for entry in allow:
        wanted = entry.get('file', '')
        if not wanted:
            continue
        tail = wanted.replace('\\', '/')
        if not (file_name == tail or file_name.endswith('/' + tail)):
            continue
        needle = entry.get('contains', '')
        if needle and needle in line:
            entry['_matched'] = True
            return entry.get('reason', '')
    return None


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', action='append', default=[],
                        type=Path,
                        help='a directory to scan (default: the repository tools/)')
    parser.add_argument('--allow', type=Path,
                        help='the allow list (default: configs/baked-version-allow.json)')
    parser.add_argument('--report', type=Path, help='write the findings as JSON here')
    args = parser.parse_args(argv)

    here = Path(__file__).resolve().parent.parent
    roots = args.root or [here / 'tools']
    allow_path = args.allow or (here / 'configs/baked-version-allow.json')
    allow = load_allow(allow_path)

    findings = []
    allowed = []
    for root in roots:
        if not root.is_dir():
            print('check-baked-version: %s is not a directory' % root)
            return 2
        for path in sorted(root.rglob('*')):
            if path.suffix not in SUFFIXES or not path.is_file():
                continue
            if any(part in SKIP_DIRS for part in path.parts):
                continue
            for n, text in code_lines(path):
                spans = decision_spans(text)
                if not spans:
                    continue
                hits = []
                for match in VERSION.finditer(text):
                    begin, finish = match.span(1)
                    if any(begin < hi and lo < finish for lo, hi in spans):
                        hits.append(match.group(1))
                if not hits:
                    continue
                name = path.as_posix()
                try:
                    name = path.relative_to(here).as_posix()
                except ValueError:
                    pass
                entry = {"file": name, "line": n, "versions": sorted(set(hits)),
                         "text": text.strip()}
                reason = allowed_hit(allow, name, text)
                if reason:
                    entry["allowed_because"] = reason
                    allowed.append(entry)
                else:
                    findings.append(entry)

    for entry in findings:
        print('%s:%d  %s  | %s' % (entry['file'], entry['line'],
                                    ','.join(entry['versions']), entry['text'][:100]))
    for entry in allowed:
        print('allowed %s:%d  %s  (%s)' % (entry['file'], entry['line'],
                                          ','.join(entry['versions']),
                                          entry['allowed_because']))

    # An allowance that matches nothing is a stale one, and a stale allowance
    # is a hole: the place it was written for is gone, and the next place
    # that looks like it would be allowed as well.
    stale = [e for e in allow if not e.get('_matched')]
    for entry in stale:
        print('stale allowance %s (%s) - it matches no line in the scanned tree'
              % (entry.get('file', '?'), entry.get('contains', '')))

    print('\n%d version literal(s) in a decision, %d allowed' % (len(findings), len(allowed)))
    if findings:
        print('a version written into a decision is right for one line and wrong for')
        print('every other one: read the version out of the image or the report')
        print('instead, or list the place in configs/baked-version-allow.json with')
        print('the reason it belongs there')
        return 1
    print('no version is written into a decision')
    if args.report:
        args.report.write_text(json.dumps({"findings": findings, "allowed": allowed},
                                          indent=2) + '\n', encoding='utf-8')
    return 0


if __name__ == '__main__':
    sys.exit(main())
