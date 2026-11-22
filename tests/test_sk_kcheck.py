#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Check the parts of the compiler check that decide what a verdict is.

tools/kcheck.py asks the host's clang to parse the drivers this
repository owns, on the tree the build is about to use.  The interesting
part of that tool is not the compiler call - it is the rule that decides
whether the compiler's output is a verdict about our files or a remark
about the tree.

The rule matters because the compiler in CI is older than the kernel it
is reading.  When clang cannot read one of the tree's own headers, the
errors it then reports in our files are a consequence of that, not a
mistake of ours, and failing the build on them would be wrong.  So the
errors are sorted by the file they are in, and only the ones in the files
we asked about can fail the check.

These tests do not need a kernel tree, a compiler or a board: they
check the rule itself.
"""
import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def load_tool():
    """The repository's own compiler check, loaded as a module."""
    path = ROOT / "tools" / "kcheck.py"
    spec = importlib.util.spec_from_file_location("kcheck", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main():
    kcheck = load_tool()
    failures = []

    def check(ok, message):
        print(f"  [{'OK ' if ok else 'FAIL'}] {message}")
        if not ok:
            failures.append(message)
        return ok

    print("\nthe tool is in the repository and loads")
    check((ROOT / "tools" / "kcheck.py").is_file(),
          "tools/kcheck.py is in the repository")
    check((ROOT / "tools" / "kshadow.py").is_file(),
          "tools/kshadow.py is in the repository")

    print("\nan error is recognised by the file it is in")
    target = Path("linux-7.3.4/drivers/net/ethernet/zte/zx279133-np.c")
    check(kcheck.error_is_in(
        "linux-7.3.4/drivers/net/ethernet/zte/zx279133-np.c:123:45: error: x",
        target),
        "the error in the file we asked about is ours")
    check(kcheck.error_is_in(
        "/home/runner/work/repo/repo/linux-7.3.4/drivers/net/ethernet/zte/"
        "zx279133-np.c:123:45: error: x", target),
        "the same error with an absolute path is ours too")
    check(not kcheck.error_is_in(
        "linux-7.3.4/include/linux/skbuff.h:12:3: error: x", target),
        "an error in the tree's own header is not ours")
    check(not kcheck.error_is_in(
        "linux-7.3.4/drivers/net/ethernet/zte/zx279133-np.c:123:45: warning: x",
        target),
        "a warning is not an error")
    check(not kcheck.error_is_in("some free text", target),
        "a line that is not an error line is not ours")

    print("\nthe configuration fragments are read the way the build reads them")
    settings = kcheck.parse_config(
        "# CONFIG_DEVMEM is not set\n"
        "CONFIG_ZTE_ZX279133_ETH=y\n"
        "CONFIG_MODULES=n\n"
        "CONFIG_PHYSICAL_START=0x80000000\n")
    check(settings.get("CONFIG_ZTE_ZX279133_ETH") == "y", "a y is a y")
    check(settings.get("CONFIG_MODULES") == "n", "a value is a value")
    check(settings.get("CONFIG_PHYSICAL_START") == "0x80000000",
          "a number keeps its text")
    check(settings.get("CONFIG_DEVMEM") is None,
          "a symbol that is not set is not a value")
    check("CONFIG_NOT_A_SYMBOL" not in settings,
          "a line that is not a symbol is not read")

    print("\na choice group keeps only the symbol that was chosen")
    group = kcheck.CHOICE_GROUPS[0]
    choices = {name: "y" for name in group}
    kcheck.resolve_choices(choices)
    still_set = [name for name in group if choices.get(name) in ("y", "m")]
    check(len(still_set) == 1,
          "a choice group that had every member set keeps one")
    check(still_set and still_set[0] == group[-1],
          "the one that wins is the last one the group lists")
    choices = {group[0]: "y"}
    kcheck.resolve_choices(choices)
    check(choices.get(group[0]) == "y",
          "the symbol that was set on its own stays")
    check(choices.get(group[1]) is None,
          "the other symbols of the group are cleared")

    print("\nthe compiler that is used is one that is there")
    complete = {"arch": 3, "syscall": 5, "shadow": 1, "bounds": 1}
    check(kcheck.incomplete_environment(complete) == [],
          "a complete environment judges")
    for missing in ("arch", "syscall", "shadow", "bounds"):
        broken = dict(complete)
        broken[missing] = 0
        problems = kcheck.incomplete_environment(broken)
        check(bool(problems),
              f"a missing {missing} means the environment cannot judge")

    compiler = kcheck.newest_clang()
    if compiler is None:
        print("  (no clang on this machine: the tool would say so and stop)")
    else:
        check(kcheck.clang_version(compiler) != (),
              "the compiler reports a version")
        check(kcheck.newest_clang("clang") == "clang",
              "an explicit compiler wins over the search")

    if failures:
        print(f"\nFAILED: kcheck ({len(failures)} problem(s))")
        for item in failures:
            print(f"  - {item}")
        return 1
    print("\nkcheck: OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
