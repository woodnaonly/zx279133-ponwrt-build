#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Negative controls for the apply context in tools/prepare-sk-kernel-73.py.

`git apply` inside a repository resolves a patch that carries a `diff --git`
header against the repository root.  The kernel tree is a directory inside
the checkout, so such a patch falls outside the current subdirectory and git
drops it: exit code 0, nothing written, and the tree keeps the older driver
without anybody noticing.  A patch without that header is resolved against the
current directory and lands.  Two patches of the 7.3 series were dropped that
way, so the tree the build compiled was quietly the older driver.

The controls below build a checkout with a tree inside it - the shape the
build machine has - and prove three things: that the patch lands now, that it
did not land before, and that a patch git drops is refused out loud instead
of being counted as applied.

They build their own checkout, so they run without a kernel tree and without
a compiler:

    python3 tests/test_sk_prepare_apply_context.py
"""
import argparse
import importlib.util
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PREPARE = ROOT / "tools/prepare-sk-kernel-73.py"

GIT_HEADER_PATCH = b"""\
diff --git a/drivers/net/ethernet/zte/zx279133-demo.c b/drivers/net/ethernet/zte/zx279133-demo.c
index 1111111..2222222 100644
--- a/drivers/net/ethernet/zte/zx279133-demo.c
+++ b/drivers/net/ethernet/zte/zx279133-demo.c
@@ -1,3 +1,4 @@
 int demo(void)
 {
+\treturn 0;
 }
"""

PLAIN_PATCH = b"""\
--- a/drivers/net/ethernet/zte/zx279133-demo2.c
+++ b/drivers/net/ethernet/zte/zx279133-demo2.c
@@ -1,3 +1,4 @@
 int demo2(void)
 {
+\treturn 0;
 }
"""

BEFORE = "int demo(void)\n{\n}\n"
BEFORE2 = "int demo2(void)\n{\n}\n"
ADDED = "\treturn 0;"


def load_prepare(path: Path = PREPARE):
    spec = importlib.util.spec_from_file_location("prepare73", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def checkout(tmp: Path, name: str) -> tuple[Path, Path]:
    """A checkout with a tree inside it, the way the build machine has it."""
    root = tmp / name
    root.mkdir(parents=True)
    subprocess.run(["git", "init", "-q"], cwd=root, check=True,
                   capture_output=True)
    tree = root / "linux-7.3.4"
    (tree / "drivers" / "net" / "ethernet" / "zte").mkdir(parents=True)
    return root, tree


def raw_apply(tree: Path, payload: bytes, name: str, env=None):
    """The apply as it was written before the ceiling: no context, no cwd."""
    patch = tree / ".sk-series" / "local" / name
    patch.parent.mkdir(parents=True, exist_ok=True)
    patch.write_bytes(payload)
    return subprocess.run(
        ["git", "-c", "core.autocrlf=false", "-C", str(tree), "apply",
         "--verbose", "--whitespace=nowarn", str(patch)],
        capture_output=True, text=True, cwd=str(tree.parent), env=env)


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

    tmp = Path(tempfile.mkdtemp(prefix="sk-apply-context-"))
    try:
        # 1. The patch with a git header has to land in a tree that is a
        #    directory inside a checkout.
        root, tree = checkout(tmp, "git-header")
        (tree / "drivers/net/ethernet/zte/zx279133-demo.c").write_bytes(
            BEFORE.encode("utf-8"))
        env = prepare.apply_context(tree)
        try:
            prepare.apply_one_patch(tree, "local", "demo.patch",
                                     GIT_HEADER_PATCH, env)
            landed = True
            error = ""
        except ValueError as exc:
            landed = False
            error = str(exc)
        text = (tree / "drivers/net/ethernet/zte/zx279133-demo.c").read_text(
            encoding="utf-8")
        check(landed, "a patch with a git header lands in a tree inside a "
                      "checkout", error)
        check(ADDED in text, "the tree carries the line the patch adds",
              repr(text))

        # 2. The patch without a git header has to land as well.
        root, tree = checkout(tmp, "plain")
        (tree / "drivers/net/ethernet/zte/zx279133-demo2.c").write_bytes(
            BEFORE2.encode("utf-8"))
        try:
            prepare.apply_one_patch(tree, "local", "demo2.patch",
                                     PLAIN_PATCH, prepare.apply_context(tree))
            landed = True
            error = ""
        except ValueError as exc:
            landed = False
            error = str(exc)
        text = (tree / "drivers/net/ethernet/zte/zx279133-demo2.c").read_text(
            encoding="utf-8")
        check(landed, "a patch without a git header lands in a tree inside a "
                      "checkout", error)
        check(ADDED in text, "the tree carries the line the patch adds",
              repr(text))

        # 3. The context has to keep the checkout out of git's reach.
        env = prepare.apply_context(tree)
        ceilings = env.get("GIT_CEILING_DIRECTORIES", "")
        check(str(tree.parent.resolve()) in ceilings.split(os.pathsep),
              "the apply context keeps the checkout out of git's reach",
              ceilings)

        # 4. Without that context git drops the patch and says so.  This is
        #    the failure the ceiling exists to end, reproduced on purpose.
        root, tree = checkout(tmp, "dropped")
        target = tree / "drivers/net/ethernet/zte/zx279133-demo.c"
        target.write_bytes(BEFORE.encode("utf-8"))
        before = target.read_bytes()
        proc = raw_apply(tree, GIT_HEADER_PATCH, "demo.patch")
        after = target.read_bytes()
        check(proc.returncode == 0,
              "git reports success when it drops a patch", proc.stderr)
        check(before == after,
              "git writes nothing when it drops a patch",
              f"{len(before)} bytes before, {len(after)} after")
        check("Skipped patch" in proc.stderr + proc.stdout,
              "git says it dropped the patch", proc.stderr)

        # 5. A patch git dropped must be refused, not counted as applied.
        root, tree = checkout(tmp, "refused")
        target = tree / "drivers/net/ethernet/zte/zx279133-demo.c"
        target.write_bytes(BEFORE.encode("utf-8"))
        raised = ""
        try:
            # The context as it was before the ceiling: the checkout is
            # reachable and the patch is dropped.
            prepare.apply_one_patch(tree, "local", "demo.patch",
                                     GIT_HEADER_PATCH, dict(os.environ))
        except ValueError as exc:
            raised = str(exc)
        check(bool(raised), "a patch git dropped is refused")
        check("demo.patch" in raised,
              "the refusal names the patch that was dropped", raised)
        check("Skipped patch" in raised,
              "the refusal says what git did", raised)

        # 6. The tree that git did not touch is still the tree that counts.
        text = target.read_text(encoding="utf-8")
        check(ADDED not in text,
              "the refused tree does not carry the patch", repr(text))
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
