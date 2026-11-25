#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Check that the 7.3 tree programs the 2.5G jack's lane again after the bring-up.

Two boots of the same kernel on the same board, on 2026-11-25, and the
difference between them was not in the lane's configuration but in how many
times the lane had been programmed.

The good boot (17:29:19) had the lane programmed twice: once by the probe,
and once at 8.99 s, when the lane watch's first pass found that the far
end's SerDes class had changed and put the lane back.  Its egress carried
bytes.

The bad boot (18:12:20) had the lane programmed once.  Its egress was dead:
the board received the test PC's frames, nothing it sent reached the PC,
and the PC's ARP entry for the board never resolved.  The far end's state
was the same in both boots, word for word, when each had finished its own
work - the SerDes class 0x9402, the lane's own word 0x801e, the PCS's BMCR
0x1140, the XMAC0's six words - and the near end's image was the loader's
in both, which is the right one (docs/92 §79.2).

So the lane has to be programmed again once the bring-up has settled, and
this gate asks whether the tree still does that.  It asks four things of
the pass, because a pass that only prints a line is not a pass:

  * the watch programs the lane again after the bring-up, once, without
    waiting for the far end's class to change - that class change is what
    used to make the second pass, and it only happens when the driver
    re-latched the far end itself.
  * the pass really programs the lane: it calls the lane's programming,
    not a log line.
  * the pass is bounded: a cap on how many times one boot may do it, and
    a cooldown between two of them.  A pass costs about a second and
    disturbs the lane, so an unbounded one is a way to break the boot.
  * the pass does not restart the far end's auto-negotiation.  That path
    is this driver's own self-harm: every restart takes the far end out of
    latch and drops the 0x40 bit of its status word (docs/92 §12.8).
  * the far end's class change goes through the same pass, so the two
    paths cannot run one after the other and cut each other short.

None of the driver can be compiled on the machine that pushes this
repository: there is no aarch64 cross toolchain there.  So what a compiler
would say about the shape of the code is checked here instead, against the
tree the build will actually use.

Usage:
  check-sk-lane-reapply.py --tree TREE
"""
import argparse
import hashlib
import os
import re
import sys
from pathlib import Path

ZTE = "drivers/net/ethernet/zte"
FILES = ["zx279133-switch.c"]

REAPPLY = "zx279133_switch_lane_reapply("
APPLY_SPEED = "zx279133_switch_outerphy_apply_speed("
SERDES_SET = "zx279133_switch_outerphy_serdes_set("

WATCH = ("static void zx279133_switch_lane_watch("
          "struct work_struct *work)\n{")
HELPER = "static void zx279133_switch_lane_reapply("


class Report:
    def __init__(self):
        self.failures = []
        self.checks = 0
        self.results = []

    def require(self, ok, message, detail=""):
        self.checks += 1
        self.results.append((ok, message, detail))
        print(f"  [{'OK ' if ok else 'FAIL'}] {message}"
              + (f" - {detail}" if detail and not ok else ""))
        if not ok:
            self.failures.append(message)
        return ok

    def note(self, message):
        print(f"  [note] {message}")


def tree_fingerprint(tree, names):
    """What the files this gate read amount to.

    A gate that reports a symbol missing and a tree that does not have
    it are two different stories, and the two are told apart by what is
    in the files.  The hashes go on the run's own page, where the job
    log cannot be downloaded from.
    """
    out = []
    for name in names:
        path = Path(tree) / ZTE / name
        if not path.is_file():
            out.append((name, "missing", 0, 0))
            continue
        data = path.read_bytes()
        out.append((name, hashlib.sha256(data).hexdigest(),
                    len(data), data.count(b"\n")))
    return out


def write_summary(report, title, files=None):
    """Put the verdict where the run page shows it."""
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if not path:
        return
    lines = [f"### {title}", ""]
    for ok, message, detail in report.results:
        mark = "PASS" if ok else "FAIL"
        lines.append(f"- **{mark}** {message}")
        if detail and not ok:
            lines.append(f"  - {detail}")
    lines.append("")
    if files:
        lines.append("| file | sha256 | bytes | lines |")
        lines.append("|---|---|---|---|")
        for name, digest, size, count in files:
            lines.append(f"| {name} | {digest} | {size} | {count} |")
        lines.append("")
    lines.append(f"{report.checks} check(s), {len(report.failures)} failed.")
    Path(path).write_text("\n".join(lines), encoding="utf-8")


def function_body(text, signature):
    """The body of one function, from its signature to the closing brace.

    A brace count is crude, but this file has no braces inside strings in
    the part the gate reads, and the crude version fails loudly (the
    caller sees an empty body) instead of quietly matching something else.

    The signature has to be the definition and not the forward
    declaration: the watch has one of those above its definition, and
    the forward declaration has no braces, so the count would end at
    the first closing brace of whatever follows it.
    """
    start = text.find(signature)
    if start < 0:
        return ""
    depth = 0
    seen = False
    for index in range(start, len(text)):
        char = text[index]
        if char == "{":
            depth += 1
            seen = True
        elif char == "}":
            depth -= 1
            if seen and depth == 0:
                return text[start:index + 1]
    return ""


def check_tree(tree, report):
    """The checks, against the tree the build will compile."""
    zte = Path(tree) / ZTE
    for name in FILES:
        if not (zte / name).is_file():
            report.require(False, f"the tree has {name}",
                           "the gate cannot read a file that is not there")
            return
    driver = (zte / "zx279133-switch.c").read_text(encoding="utf-8")

    watch = function_body(driver, WATCH)
    helper = function_body(driver, HELPER)

    if not report.require(watch, "the tree has the lane watch"):
        return
    if not report.require(helper, "the tree has the lane re-apply pass"):
        return

    report.require(
        re.search(r"if \(!priv->lane_reapplies\)\s*\n\s*"
                  r"zx279133_switch_lane_reapply\(", watch) is not None,
        "the watch programs the lane again after the bring-up",
        "the second pass is left to the far end's class change, which "
        "only happens when the driver re-latched the far end itself - a "
        "boot that inherited the lane from the loader never gets it")

    report.require(
        APPLY_SPEED in helper,
        "the re-apply pass programs the lane",
        "the pass only says it happened; the lane's programming is what "
        "brings the egress back")

    report.require(
        ">= ZX279133_SWITCH_LANE_REAPPLY_MAX" in helper
        and "time_before_eq(" in helper,
        "the re-apply pass is bounded",
        "a pass costs about a second and disturbs the lane, so one boot "
        "may only do a few of them and only a long time apart")

    report.require(
        SERDES_SET not in helper,
        "the re-apply pass does not restart the far end's auto-negotiation",
        "every restart takes the far end out of latch and drops the 0x40 "
        "bit of its status word, and that word is not a judge of whether "
        "the lane carries code (docs/92 §12.8)")

    rearm = watch[watch.find("rearm:"):] if "rearm:" in watch else ""
    report.require(
        REAPPLY in rearm and APPLY_SPEED not in rearm,
        "the far end's class change goes through the same pass",
        "the two paths have to share the pass, or the cooldown between the "
        "passes cannot keep them from cutting each other short")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tree", type=Path, required=True,
                        help="the prepared kernel tree")
    args = parser.parse_args()
    tree = args.tree
    if not (tree / "Makefile").is_file():
        print(f"not a kernel tree: {tree}")
        return 2

    report = Report()
    print(f"the lane's second programming pass in {tree}")
    check_tree(tree, report)
    write_summary(report, "The lane is programmed again after the bring-up",
                  tree_fingerprint(tree, FILES))
    if report.failures:
        print(f"\nFAILED: {len(report.failures)} of {report.checks} check(s)")
        for item in report.failures:
            print(f"  - {item}")
        return 1
    print(f"\nPASSED: {report.checks} check(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
