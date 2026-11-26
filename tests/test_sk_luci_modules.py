#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Prove that the LuCI module gate bites.

Every mutation below breaks one property of a LuCI module, and the gate
has to name that property when it runs on the mutated tree.  A gate that
stays green under a mutation it was meant to catch is not a gate.

The first mutation is the defect itself: the WAN module ended with a plain
object instead of a class, and the page died with 'factory yields invalid
constructor'.

The tree is copied before each mutation, so the original is never touched.
"""
import argparse
import pathlib
import shutil
import subprocess
import sys
import tempfile

NL = chr(10)
HERE = pathlib.Path(__file__).resolve().parent
CHECK = HERE.parent / "tools" / "check-sk-luci-modules.py"

MODULE = "skd840n/wanport.js"
VIEW = "view/skd840n/wanport.js"


def drop_export(text):
    """The text without its export."""
    lines = text.splitlines(True)
    for i, line in enumerate(lines):
        if line.startswith("return "):
            return "".join(lines[:i])
    return text


def drop_require(text):
    """The text without the require of the class it extends."""
    return "".join(line + NL for line in text.splitlines()
                    if line.strip() != "'require baseclass';")


MUTATIONS = [
    (
        "every module exports a class",
        MODULE,
        lambda text: text.replace("return baseclass.extend({", "return {"),
    ),
    (
        "every module exports a class",
        VIEW,
        lambda text: text.replace("return view.extend({", "return {"),
    ),
    (
        "every module exports a class",
        MODULE,
        lambda text: text.replace("return baseclass.extend({",
                               "return baseclass.extendX({"),
    ),
    (
        "every module exports a class",
        MODULE,
        lambda text: text.replace("return baseclass.extend({",
                               "return ui.extend({"),
    ),
    (
        "every module exports a class",
        MODULE,
        drop_export,
    ),
    (
        "the class module is required",
        MODULE,
        drop_require,
    ),
    (
        "every module exports exactly one thing",
        MODULE,
        lambda text: text + "return baseclass.extend({});" + NL,
    ),
]


def resource_roots(tree):
    """The LuCI resource roots under the tree, as paths relative to it."""
    root = pathlib.Path(tree)
    roots = []
    for marker in sorted(root.rglob("luci-static")):
        if not marker.is_dir():
            continue
        resources = marker / "resources"
        if resources.is_dir():
            roots.append(marker.relative_to(root))
    return roots


def copy_resources(tree, dest):
    """Copy every LuCI resource root into a fresh tree."""
    root = pathlib.Path(tree)
    dest = pathlib.Path(dest)
    for relative in resource_roots(root):
        target = dest / relative / "resources"
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(root / relative / "resources", target)


def run_gate(tree):
    proc = subprocess.run(
        [sys.executable, str(CHECK), "--tree", str(tree)],
        capture_output=True,
        text=True,
    )
    failed = set()
    for line in proc.stdout.splitlines():
        if line.startswith("FAIL "):
            failed.add(line[5:].split(":")[0].strip())
    return failed, proc.stdout


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tree", required=True, help="the repository")
    args = parser.parse_args(argv)

    bad = 0

    failed, out = run_gate(args.tree)
    if failed:
        print("the untouched tree must pass the gate, but these failed: "
              + ", ".join(sorted(failed)))
        print(out)
        bad += 1
    else:
        print("ok   the untouched tree passes every check")

    with tempfile.TemporaryDirectory() as tmp:
        for index, (name, relative, mutate) in enumerate(MUTATIONS):
            tree = pathlib.Path(tmp) / ("%d_%s" % (index, name.replace(" ", "_")))
            tree.mkdir(parents=True)
            copy_resources(args.tree, tree)
            targets = [tree / r / "resources" / relative
                       for r in resource_roots(args.tree)]
            targets = [t for t in targets if t.is_file()]
            if not targets:
                print("FAIL the mutation for '%s' found no %s - "
                      "the tree does not look the way the test expects"
                      % (name, relative))
                bad += 1
                continue
            changed = False
            for target in targets:
                text = target.read_text(encoding="utf-8")
                new = mutate(text)
                if new != text:
                    changed = True
                    target.write_bytes(new.encode("utf-8"))
            if not changed:
                print("FAIL the mutation for '%s' did not change %s - "
                      "the tree does not look the way the test expects"
                      % (name, relative))
                bad += 1
                continue
            failed, out = run_gate(tree)
            if name in failed:
                print("ok   the gate names '%s' when it is broken" % name)
            else:
                print("FAIL the gate stayed quiet when '%s' was broken" % name)
                print(out)
                bad += 1

    if bad:
        print("test_sk_luci_modules: %d problem(s)" % bad)
        return 1
    print("test_sk_luci_modules: every mutation was caught")
    return 0


if __name__ == "__main__":
    sys.exit(main())
