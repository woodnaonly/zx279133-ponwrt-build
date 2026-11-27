#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Prove that the readable-file gate bites.

The defect this gate exists for was a file the image shipped at 0600:
uhttpd refuses to serve a file that is not readable by everyone, so the
LED page died with "HTTP error 403 while loading class file".  Nothing
in the build looked at the modes, so nothing said so.

The rule is checked first, on the modes themselves, because that is the
part of the gate that has an answer on any system.  Then the tree is
built and taken apart again:

  * the file at 0600 - the defect itself,
  * the directory at 0700 - uhttpd cannot walk into it,
  * the document root with no file in it - the gate has to say it found
    nothing,
  * a tree with no document root - the same.

The POSIX modes are the subject, so the tree mutations need a system that
has them.  On a system that does not, they are reported as skipped
rather than passed: a control that cannot run must not say it passed.
"""
import argparse
import importlib.util
import os
import pathlib
import subprocess
import sys
import tempfile

HERE = pathlib.Path(__file__).resolve().parent
CHECK = HERE.parent / "tools" / "check-sk-luci-readable.py"

TRIGGER = "luci-static/resources/view/system/led-trigger/zx279133-los.js"
OTHER = "luci-static/resources/view/system/led-trigger/usbport.js"

# The mode, whether it is a directory, what uhttpd does with it, and why.
RULES = [
    (0o644, False, True, "a file at 0644 is served"),
    (0o640, False, False, "a file at 0640 is not: the world cannot read it"),
    (0o600, False, False, "a file at 0600 is not: this is the defect"),
    (0o400, False, False, "a file at 0400 is not"),
    (0o755, True, True, "a directory at 0755 is walked into"),
    (0o750, True, False,
     "a directory at 0750 is not: the world cannot walk into it"),
    (0o700, True, False,
     "a directory at 0700 is not: uhttpd cannot walk into it"),
]


def load_gate():
    """The gate as a module, so its rule can be asked directly."""
    spec = importlib.util.spec_from_file_location("check_sk_luci_readable",
                                                  CHECK)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def build_tree(root):
    """A document root that looks like the one this line ships."""
    for relative in (TRIGGER, OTHER):
        path = root / "www" / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"'use strict';\n")
    return root


def run_gate(root):
    proc = subprocess.run(
        [sys.executable, str(CHECK), "--root", str(root)],
        capture_output=True,
        text=True,
    )
    return proc.returncode, proc.stdout


def modes_available():
    """Whether this system can put a mode on a file that means something."""
    if os.name != "posix":
        return False
    with tempfile.TemporaryDirectory() as tmp:
        path = pathlib.Path(tmp) / "probe"
        path.write_bytes(b"x")
        os.chmod(path, 0o600)
        return (path.stat().st_mode & 0o777) == 0o600


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args(argv)

    bad = 0
    skipped = 0
    posix = modes_available()

    gate = load_gate()
    for mode, is_dir, expected, what in RULES:
        if gate.served(mode, is_dir) != expected:
            print("FAIL the gate says otherwise about %s" % what)
            bad += 1
        else:
            print("ok   the gate says %s" % what)

    with tempfile.TemporaryDirectory() as tmp:
        base = pathlib.Path(tmp)

        clean = build_tree(base / "clean")
        code, out = run_gate(clean)
        if code != 0:
            print("FAIL the untouched tree must pass the gate:")
            print(out)
            bad += 1
        else:
            print("ok   the untouched tree passes every check")

        if not posix:
            print("skip this system has no POSIX modes, so the mode "
                  "mutations cannot run here")
            skipped += 2
        else:
            mode = build_tree(base / "mode")
            os.chmod(mode / "www" / TRIGGER, 0o600)
            code, out = run_gate(mode)
            if code == 0:
                print("FAIL the gate stayed quiet about a file at 0600:")
                print(out)
                bad += 1
            elif 'zx279133-los.js' not in out:
                print("FAIL the gate did not name the file at 0600:")
                print(out)
                bad += 1
            else:
                print("ok   the gate names the file at 0600")

            walk = build_tree(base / "walk")
            os.chmod(walk / "www" / "luci-static" / "resources" / "view"
                     / "system" / "led-trigger", 0o700)
            code, out = run_gate(walk)
            if code == 0:
                print("FAIL the gate stayed quiet about a directory at 0700:")
                print(out)
                bad += 1
            else:
                print("ok   the gate names the directory at 0700")

        empty = base / "empty"
        (empty / "www").mkdir(parents=True)
        code, out = run_gate(empty)
        if code == 0:
            print("FAIL the gate stayed quiet about a document root with no "
                  "file in it:")
            print(out)
            bad += 1
        else:
            print("ok   the gate says a document root with no file in it is "
                  "not a check")

        noroot = base / "noroot"
        noroot.mkdir(parents=True)
        code, out = run_gate(noroot)
        if code == 0:
            print("FAIL the gate stayed quiet about a tree with no document "
                  "root:")
            print(out)
            bad += 1
        else:
            print("ok   the gate says a tree with no document root is not a "
                  "check")

    if bad:
        print("test_sk_luci_readable: %d problem(s)" % bad)
        return 1
    if skipped:
        print("test_sk_luci_readable: %d mutation(s) skipped, the rest were "
              "caught" % skipped)
        return 0
    print("test_sk_luci_readable: every mutation was caught")
    return 0


if __name__ == "__main__":
    sys.exit(main())
