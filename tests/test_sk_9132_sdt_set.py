#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""The controls for tools/check-sk-9132-sdt-set.py.

The gate says the switch engine is given the vendor's table set and that
no table base is written down as a constant.  A gate that cannot fail is
not a gate, so this test puts each of the defects back and says the gate
still notices - and says it notices the right one, because a gate that
goes red for the wrong reason is as useless as one that stays green.

The tree it works on is built here: the 6.18 generation's np.c, which is
the driver that boots on this board, is copied into a directory shaped
like a kernel tree and then broken in one place at a time.  No kernel and
no board are needed, so this runs in the gates job, where a broken gate is
reported in minutes instead of after an hour of compiling.

    python3 tests/test_sk_9132_sdt_set.py
"""
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GATE = ROOT / "tools/check-sk-9132-sdt-set.py"
REFERENCE = "kernel-overlays/drivers/net/ethernet/zte/zx279133-np.c"
ZTE = "drivers/net/ethernet/zte"
NP = "zx279133-np.c"


def run_gate(tree, repo):
    return subprocess.run(
        [sys.executable, str(GATE), "--tree", str(tree), "--repo", str(repo)],
        capture_output=True, text=True, encoding="utf-8", errors="replace")


def main() -> int:
    checks = 0
    failures = []

    def check(ok, what, detail=""):
        nonlocal checks
        checks += 1
        if ok:
            print("  ok   %s" % what)
        else:
            print("  FAIL %s%s" % (what, (" - " + detail) if detail else ""))
            failures.append(what)

    source = ROOT / REFERENCE
    if not source.is_file():
        print("  FAIL %s is in the repository" % REFERENCE)
        print("1 checks, 1 failed")
        return 1
    original = source.read_text(encoding="utf-8", errors="replace")

    with tempfile.TemporaryDirectory(prefix="sk-9132-sdt-") as tmp:
        work = Path(tmp)
        tree = work / "tree"
        repo = work / "repo"
        (tree / ZTE).mkdir(parents=True)
        (repo / Path(REFERENCE).parent).mkdir(parents=True)
        tree_np = tree / ZTE / NP
        repo_np = repo / REFERENCE

        def put(text, reference=None):
            tree_np.write_text(text, encoding="utf-8")
            repo_np.write_text(reference if reference is not None else text,
                               encoding="utf-8")

        print("a tree that is right")
        put(original)
        result = run_gate(tree, repo)
        check(result.returncode == 0, "the gate passes on the reference",
              (result.stdout + result.stderr)[-400:])

        print("the MF extension table is not in the table set")
        put(original.replace("\t{ 0x7d, 1, 0x3, 0x40 },\t\t{ 0x28, 1, 0x5, 0x2000 },\n",
                              "\t{ 0x28, 1, 0x5, 0x2000 },\n"))
        result = run_gate(tree, repo)
        out = result.stdout + result.stderr
        check(result.returncode != 0, "the gate fails without table 0x7d")
        check("0x7d" in out, "the gate names table 0x7d", out[-400:])

        print("a table is declared with another type")
        put(original.replace("{ 0x22, 1, 0x3, 0x20 },", "{ 0x22, 4, 0x3, 0x20 },"))
        result = run_gate(tree, repo)
        out = result.stdout + result.stderr
        check(result.returncode != 0, "the gate fails when 0x22 is not ERAM")
        check("0x22" in out, "the gate names table 0x22", out[-400:])

        print("a table is declared with another width")
        put(original.replace("{ 0x1f, 1, 0x1, 0x4000 },", "{ 0x1f, 1, 0x3, 0x4000 },"))
        result = run_gate(tree, repo)
        out = result.stdout + result.stderr
        check(result.returncode != 0, "the gate fails when 0x1f is wider")
        check("0x1f" in out, "the gate names table 0x1f", out[-400:])

        print("the tables are in another order")
        put(original.replace(
            "\t{ 0x00, 1, 0x3, 0x1000 },\t{ 0x04, 1, 0x7, 0x2000 },\n",
            "\t{ 0x04, 1, 0x7, 0x2000 },\t{ 0x00, 1, 0x3, 0x1000 },\n"))
        result = run_gate(tree, repo)
        out = result.stdout + result.stderr
        check(result.returncode != 0, "the gate fails when the order changes")
        check("entry" in out, "the gate names the entry that moved", out[-400:])

        print("the port attribute word goes back to word 3")
        put(original.replace("data[0] = attr;", "data[3] = attr;"))
        result = run_gate(tree, repo)
        out = result.stdout + result.stderr
        check(result.returncode != 0, "the gate fails with the attribute word "
                                     "in word 3")
        check("attribute word" in out,
              "the gate names the port attribute word", out[-400:])

        print("a table base is written down as a constant again")
        put(original.replace(
            "#define ZX279133_SDT_ID_MAX\t0x100\n",
            "#define ZX279133_SDT_ID_MAX\t0x100\n"
            "#define ZX279133_MF_ERAM_BASE_BLOCK\t17106\n"))
        result = run_gate(tree, repo)
        out = result.stdout + result.stderr
        check(result.returncode != 0, "the gate fails with a base constant")
        check("ERAM base constant" in out, "the gate names the constant",
              out[-400:])

        print("the SE allocator writes a table at a literal block")
        put(original.replace("(ptype_base + tabs[idx].entry) << 7",
                             "(23026 + tabs[idx].entry) << 7"))
        result = run_gate(tree, repo)
        out = result.stdout + result.stderr
        check(result.returncode != 0, "the gate fails with a literal block")
        check("23026" in out, "the gate names the literal block", out[-400:])

        print("both generations drift together")
        # The tree and the reference are changed the same way, so the
        # comparison between them says nothing.  The bases the board
        # measured are the anchor, and they have to catch this.
        drift = original.replace("{ 0x7d, 1, 0x3, 0x40 },", "{ 0x7d, 1, 0x3, 0x20 },")
        put(drift, drift)
        result = run_gate(tree, repo)
        out = result.stdout + result.stderr
        check(result.returncode != 0,
              "the gate fails when the tree and the reference drift together")
        check("the board measured" in out,
              "the gate names the measured base", out[-400:])

    print("%d checks, %d failed" % (checks, len(failures)))
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
