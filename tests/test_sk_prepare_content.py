#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Negative controls for the content check in tools/prepare-sk-kernel-73.py.

`git apply` reports success when the hunks matched.  That is not the same
promise as "the content is in the file the gates will read", and the gap
between the two is invisible until a gate turns red about a symbol the
patch is supposed to have added.  The prepare step therefore looks the
added lines up in the tree after every patch it applies.

This file proves that the check bites.  Each control below takes the
content away in a different way, and the check has to name it - not merely
turn red somewhere.

The controls build their own trees, so they run without a kernel tree and
without a compiler:

    python3 tests/test_sk_prepare_content.py
"""
import argparse
import importlib.util
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PREPARE = ROOT / "tools/prepare-sk-kernel-73.py"
LOCAL = ROOT / "kernel-patches" / "7.3-local"
PATCHES = (
    "160-sk-tx-fault-isolation.patch",
    "170-sk-rx-delivery-safety.patch",
)


def load_prepare(path: Path = PREPARE):
    spec = importlib.util.spec_from_file_location("prepare73", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def fake_tree(prepare, payload, tmp: Path) -> Path:
    """A tree that carries exactly what the patch adds."""
    tree = tmp / "linux-7.3.4"
    for section in prepare.patch_sections(payload):
        rel = section["path"]
        if rel == "/dev/null":
            continue
        path = tree / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("\n".join(section["added"]) + "\n", encoding="utf-8")
    return tree


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prepare", type=Path, default=PREPARE,
                        help="the prepare script under test; the default is "
                             "the one this repository carries")
    args = parser.parse_args()
    prepare = load_prepare(args.prepare)

    failures = []
    checks = 0

    def check(ok, message, detail=""):
        nonlocal checks
        checks += 1
        print(f"  [{'OK ' if ok else 'FAIL'}] {message}"
              + (f" - {detail}" if detail and not ok else ""))
        if not ok:
            failures.append(message)

    payloads = {}
    for name in PATCHES:
        path = LOCAL / name
        if not path.is_file():
            print(f"  [FAIL] the patch {name} is not in the repository")
            failures.append(f"the patch {name} is missing")
            checks += 1
            continue
        payloads[name] = path.read_bytes()

    tmp = Path(tempfile.mkdtemp(prefix="sk-prepare-content-"))
    try:
        for name, payload in payloads.items():
            sections = prepare.patch_sections(payload)
            check(bool(sections), f"{name} names the files it writes")
            for section in sections:
                check(section["path"].startswith("drivers/"),
                      f"{name} names {section['path']} as a driver file",
                      section["path"])

            # A tree that carries what the patch adds must pass.
            tree = fake_tree(prepare, payload, tmp / name.replace(".", "_"))
            problems = prepare.verify_patch_content(tree, name, payload)
            check(not problems,
                  f"a tree that carries {name} is accepted",
                  "; ".join(problems))

            # One line taken away must be named.
            target = None
            for section in sections:
                if section["added"]:
                    target = section
                    break
            if target is None:
                continue
            short = [line for line in target["added"] if line and len(line) <= 40]
            victim = short[0] if short else None
            if victim is None:
                continue
            broken = tree / target["path"]
            text = broken.read_text(encoding="utf-8")
            broken.write_text(text.replace(victim + "\n", ""), encoding="utf-8")
            problems = prepare.verify_patch_content(tree, name, payload)
            check(any(target["path"] in item for item in problems),
                  f"a tree without one line of {name} is rejected",
                  "; ".join(problems))
            check(any(repr(victim) in item for item in problems),
                  f"the rejected {name} names the line that is missing",
                  "; ".join(problems))

            # A file that is not there at all must be named.
            broken.unlink()
            problems = prepare.verify_patch_content(tree, name, payload)
            check(any(target["path"] in item for item in problems),
                  f"a tree without {target['path']} is rejected",
                  "; ".join(problems))

        # A blank added line is a blank line, not a missing one.
        fake = b"--- a/drivers/net/ethernet/zte/x.c\n+++ b/drivers/net/ethernet/zte/x.c\n@@ -0,0 +1,2 @@\n+\n+int a;\n"
        blank_tree = tmp / "blank"
        (blank_tree / "drivers/net/ethernet/zte").mkdir(parents=True, exist_ok=True)
        (blank_tree / "drivers/net/ethernet/zte/x.c").write_text(
            "\nint a;\n", encoding="utf-8")
        problems = prepare.verify_patch_content(blank_tree, "blank", fake)
        check(not problems,
              "a blank added line does not count as a missing one",
              "; ".join(problems))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    if failures:
        print(f"\nFAILED: {len(failures)} of {checks} check(s)")
        for item in failures:
            print(f"  - {item}")
        return 1
    print(f"\nOK: {checks} check(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
