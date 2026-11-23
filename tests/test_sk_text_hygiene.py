#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""The repository's documents must not carry characters no reader can see.

A U+FFFD in a document is a character the reader cannot read, and it does
more than look wrong: ripgrep treats a file that carries one as binary,
so every search against that document comes back with "no match" -
silently.  A false negative in the tool this repository uses to find
things is worse than a visible typo, because it costs a whole debugging
session instead of a glance.

A NUL byte does the same thing, and a control character that is neither a
tab nor a newline is just as unreadable.  So the same rule covers all
three: what a document may not carry, and what it must not carry.

    python3 tests/test_sk_text_hygiene.py
"""
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
BAD = chr(0xFFFD)
SKIP = {".git", "node_modules", ".venv"}
# What a text document may carry below the printable range: a tab, a
# newline and a carriage return are text.
ALLOWED_CONTROL = {"\t", "\n", "\r"}


def main() -> int:
    failures = []
    checked = 0
    for path in sorted(ROOT.rglob("*.md")):
        if SKIP & set(path.parts):
            continue
        try:
            data = path.read_bytes()
        except OSError as error:
            failures.append(f"{path.relative_to(ROOT)}: cannot be read ({error})")
            continue
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError as error:
            failures.append(f"{path.relative_to(ROOT)}: cannot be read ({error})")
            continue
        checked += 1
        count = text.count(BAD)
        if count:
            lines = [index for index, line in enumerate(text.split("\n"), 1)
                     if BAD in line]
            failures.append(
                f"{path.relative_to(ROOT)}: {count} replacement character(s) "
                f"on line(s) {lines[:5]}")
        # A control character that is not a tab, a newline or a carriage
        # return: invisible to the reader, and enough to make ripgrep
        # treat the file as binary.
        control = {}
        for index, line in enumerate(text.split("\n"), 1):
            for char in line:
                if ord(char) < 0x20 and char not in ALLOWED_CONTROL:
                    control.setdefault(char, []).append(index)
        for char, lines in sorted(control.items()):
            failures.append(
                f"{path.relative_to(ROOT)}: {len(lines)} control character(s) "
                f"U+{ord(char):04X} on line(s) {lines[:5]}")
    for item in failures:
        print(f"  [FAIL] {item}")
    verdict = "FAILED" if failures else "OK"
    print(f"\n{verdict}: {len(failures)} problem(s) in {checked} file(s)")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
