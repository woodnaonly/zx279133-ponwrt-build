#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Check that the 7.3 tree quarantines a TX ring whose counter cannot be trusted.

The 7.3 tree's driver believed the TX completion counter.  When the counter
reported more completions than the ring had frames in it, the driver clamped
the delta to the number of pending frames and carried on releasing slots and
unmapping DMA.  An untrusted counter cannot prove that the DMA has given the
buffers back, and freeing a buffer the DMA may still own is a use-after-free
that no counter can rule out.

The 6.18 generation does not believe it either: it quarantines the ring,
stops both logical TX devices and hands the ring to a recovery worker.  That
is the model the 160 patch moves into the 7.3 tree, and this is the gate that
says the move really happened.

None of the driver can be compiled on the machine that pushes this
repository: there is no aarch64 cross toolchain there, and the build only
happens in CI, seventy minutes into a job.  So what a compiler would say
about the shape of the code is checked here instead, against the tree the
build will actually use.

What is checked, and why each one is asked the way it is:

  * the fault branch does not release anything, and everything the function
    releases happens after it.  This is asked as a position, not as the
    presence of a string: a clamp that sits in the fault branch and a
    release that sits after it are the same code, and only the position
    tells them apart.
  * the fault branch hands the ring to the quarantine and returns.
  * the ring is excluded from reclaiming before its counter is even read.
  * the quarantine stops both logical TX devices, because they share the
    ring: one of them still sending would keep the faulted ring in use.
  * the recovery runs from a work queue and never waits for RTNL, so it
    cannot deadlock against a device teardown that holds it.
  * the recovery restarts the datapath at most once per real completion, so
    a watchdog that keeps firing cannot become a reset loop.
  * a datapath that could not be brought back up is marked failed, and the
    next user restores it.
  * the state is counted, so a fault is visible from the running board.

Usage:
  check-sk-tx-isolation.py --tree TREE
"""
import argparse
import hashlib
import os
import re
import sys
from pathlib import Path

ZTE = "drivers/net/ethernet/zte"

# The statements that give a frame back to the system.  A frame that the DMA
# may still own must not reach any of them.
RELEASES = [
    "dma_unmap_single",
    "dev_consume_skb_any",
    "xdp_return_frame",
    "xsk_buff_free",
    "xsk_tx_completed",
    "netdev_tx_completed_queue",
    "tx->done =",
    "tx->completed +=",
    "tx->pending--",
    "tx->consumer =",
]


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
    log cannot be downloaded from, so a red gate can be compared with
    a green one without fetching anything.
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
    """Put the verdict where the run page shows it.

    A job's log is a download, and an anonymous client cannot download one;
    the step summary is on the run's own page, where a red gate can be read
    without anything but a browser.  A gate that fails where nobody can see
    which check failed is a gate that costs a whole build cycle to diagnose.
    """
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
    lines.append(f"{report.checks} check(s), {len(report.failures)} failed.")
    lines.append("")
    if files:
        lines.append("The files this gate read:")
        lines.append("")
        for name, digest, size, count in files:
            lines.append(f"- `{name}` {digest} - {size} bytes, {count} lines")
        lines.append("")
    try:
        with open(path, "a", encoding="utf-8") as handle:
            handle.write("\n".join(lines) + "\n")
    except OSError as error:
        print(f"  [note] the step summary could not be written: {error}")


def strip_comments(text):
    """Blank out the comments, keeping the line structure intact.

    A comment that mentions a release would otherwise be read as one.
    """
    out = []
    i = 0
    n = len(text)
    while i < n:
        ch = text[i]
        if ch == "/" and i + 1 < n and text[i + 1] == "*":
            end = text.find("*/", i + 2)
            end = n if end < 0 else end + 2
            out.append("".join(c if c == "\n" else " " for c in text[i:end]))
            i = end
            continue
        if ch == "/" and i + 1 < n and text[i + 1] == "/":
            end = text.find("\n", i)
            end = n if end < 0 else end
            out.append(" " * (end - i))
            i = end
            continue
        if ch == '"':
            j = i + 1
            while j < n:
                if text[j] == "\\":
                    j += 2
                    continue
                if text[j] == '"':
                    j += 1
                    break
                if text[j] == "\n":
                    break
                j += 1
            out.append(text[i:j])
            i = j
            continue
        out.append(ch)
        i += 1
    return "".join(out)


TYPE_WORD = (r"(?:static|inline|void|bool|int|unsigned|signed|u\d+|s\d+|char|"
              r"long|short|size_t|const\s+struct\s+\w+|struct\s+\w+|enum\s+\w+)")


def is_definition(text, match):
    """Whether the name at `match` is a definition and not a call.

    A call can be followed by a brace - `if (check(x, y)) {` - so a brace
    after the argument list does not make it a definition.  What makes it
    one is the type in front of the name: the last statement before it
    ends with a type word.
    """
    prefix = text[max(0, match.start() - 300):match.start()]
    cut = max(prefix.rfind(";"), prefix.rfind("{"), prefix.rfind("}"))
    head = prefix[cut + 1:][-80:]
    return re.search(TYPE_WORD + r"\s*\*?\s*$", head) is not None


def function_body(text, name):
    """The body of a function definition, braces balanced.

    A declaration is not a definition: the parameters of a declaration end
    in a semicolon, and the search never crosses one.  A call is not a
    definition either - it carries no type in front of the name.
    """
    for match in re.finditer(r"\b" + re.escape(name) + r"\s*\([^;]*?\)\s*\{",
                             text, re.S):
        if not is_definition(text, match):
            continue
        start = match.end() - 1
        depth = 0
        for index in range(start, len(text)):
            if text[index] == "{":
                depth += 1
            elif text[index] == "}":
                depth -= 1
                if depth == 0:
                    return text[start + 1:index]
    return None


def if_blocks(body, condition):
    """Every `if (<condition>) { ... }` in the body, with its extent."""
    blocks = []
    for match in re.finditer(r"\bif\s*\(" + condition + r"\)\s*\{", body):
        start = match.end() - 1
        depth = 0
        for index in range(start, len(body)):
            if body[index] == "{":
                depth += 1
            elif body[index] == "}":
                depth -= 1
                if depth == 0:
                    blocks.append((start, index, body[start + 1:index]))
                    break
    return blocks


def release_positions(body):
    """Where the function gives a frame back, as (offset, what)."""
    pattern = re.compile("|".join(re.escape(item) for item in RELEASES))
    return [(match.start(), match.group(0)) for match in pattern.finditer(body)]


def read(tree, name):
    path = Path(tree) / ZTE / name
    if not path.is_file():
        return None
    return strip_comments(path.read_text(encoding="utf-8", errors="replace"))


def check(tree, report):
    reclaim = read(tree, "zx279133-idm.c")
    netdev = read(tree, "zx279133-netdev.c")
    main = read(tree, "zx279133-main.c")
    header = read(tree, "zx279133.h")
    if not all((reclaim, netdev, main, header)):
        report.require(False, "the driver's own files are in the tree")
        return report

    # 1. The fault branch does not release anything.
    body = function_body(reclaim, "zx279133_idm_tx_reclaim_queue_locked")
    if not report.require(body is not None,
                          "the tree has zx279133_idm_tx_reclaim_queue_locked"):
        return report
    blocks = if_blocks(body, re.escape("completed > tx->pending"))
    if not report.require(len(blocks) == 1,
                          "the reclaim has one branch for an impossible delta",
                          f"{len(blocks)} found"):
        return report
    start, end, inner = blocks[0]
    inside = [what for _, what in release_positions(inner)]
    report.require(not inside,
                   "the fault branch releases nothing",
                   ", ".join(sorted(set(inside))))
    report.require("return" in inner,
                   "the fault branch returns instead of falling through")
    outside = [(offset, what) for offset, what in release_positions(body)
               if offset < start]
    report.require(not outside,
                   "nothing is released before the fault branch is decided",
                   ", ".join(sorted({what for _, what in outside})))

    # 2. The fault branch hands the ring over and says it is a fault.
    report.require("zx279133_tx_recovery_request_locked" in inner,
                   "the fault branch asks for the ring to be quarantined")
    request = re.search(r"zx279133_tx_recovery_request_locked\s*\([^;]*?\)",
                         inner, re.S)
    report.require(request is not None and "true" in request.group(0),
                   "the fault branch says the completion was invalid",
                   request.group(0) if request else "no call")

    # 3. The ring is out of reclaiming before its counter is read.
    guard = body.find("tx_fault_queues")
    counted = re.search(r"\bdone\s*=\s*readl\s*\(", body)
    read_done = counted.start() if counted else -1
    report.require(0 <= guard < read_done,
                   "a quarantined ring is not reclaimed at all",
                   f"guard at {guard}, counter read at {read_done}")

    # 4. The quarantine stops both logical TX devices.
    quarantine = function_body(netdev, "zx279133_tx_recovery_request_locked")
    if report.require(quarantine is not None,
                      "the tree has zx279133_tx_recovery_request_locked"):
        report.require("tx_fault_queues |=" in quarantine,
                       "the quarantine marks the ring")
        report.require("tx_stopping" in quarantine,
                       "the quarantine stops the shared datapath")
        stops = re.findall(r"netif_tx_stop_all_queues\s*\(\s*([^)]*)\)",
                           quarantine)
        report.require(any("ndev" in arg for arg in stops),
                       "the quarantine stops the WAN device", str(stops))
        report.require(any("lan_ndev" in arg for arg in stops),
                       "the quarantine stops the LAN device as well", str(stops))

    # 5. The recovery is driven by a work queue.
    queue = function_body(netdev, "zx279133_tx_recovery_queue_locked")
    if report.require(queue is not None,
                      "the tree has zx279133_tx_recovery_queue_locked"):
        report.require("mod_delayed_work" in queue and "tx_recovery_work" in queue,
                       "the quarantine arms the recovery work")
    report.require("struct delayed_work tx_recovery_work;" in header,
                   "the eth carries the recovery work")

    # 6. The recovery never waits for RTNL, and it is counted.
    worker = function_body(netdev, "zx279133_tx_recovery_work")
    if report.require(worker is not None,
                      "the tree has zx279133_tx_recovery_work"):
        report.require("rtnl_trylock" in worker,
                       "the recovery does not wait for RTNL")
        report.require("rtnl_lock()" not in worker.replace("rtnl_trylock()", ""),
                       "the recovery does not take RTNL outright")
        report.require("rtnl_unlock" in worker,
                       "the recovery gives RTNL back")

    # 7. The recovery restarts the datapath at most once per real completion.
        report.require("tx_recovery_baseline" in worker,
                       "the recovery compares the completions with the baseline")
        report.require("tx_recovery_attempted" in worker,
                       "the recovery remembers that it already restarted")
        report.require(re.search(r"restart\s*=\s*!\s*eth->tx_recovery_attempted",
                                 worker) is not None,
                       "the restart is rearmed only by a real completion")

    # 8. The datapath's own failure is marked and restored.
    resume = function_body(netdev, "zx279133_datapath_resume")
    if report.require(resume is not None,
                      "the tree has zx279133_datapath_resume"):
        report.require("datapath_failed, false" in resume,
                       "a datapath that came back up is not marked failed")
        report.require("datapath_failed, true" in resume,
                       "a datapath that did not come up is marked failed")
    restore = function_body(netdev, "zx279133_datapath_restore_failed_locked")
    report.require(restore is not None,
                   "the tree has zx279133_datapath_restore_failed_locked")
    for opener in ("zx279133_open", "zx279133_lan_datapath_get"):
        body = function_body(netdev, opener)
        if body is None:
            report.require(False, f"the tree has {opener}")
            continue
        report.require("zx279133_datapath_restore_failed_locked" in body,
                       f"{opener} restores a failed datapath before using it")

    # 9. The state is counted, so a fault is visible from the running board.
    for field in ("tx_recovery_requests", "tx_recovery_restarts",
                  "tx_recovery_failures", "tx_invalid_completions"):
        report.require(field in header, f"the eth counts {field}")

    # 10. The recovery is wired into the driver's life cycle.
    report.require("zx279133_tx_recovery_init(eth)" in main,
                   "the probe arms the recovery work")
    report.require("zx279133_tx_recovery_cancel" in main,
                   "the driver cancels the recovery work when it goes away")
    report.require("devm_add_action_or_reset" in main,
                   "the cancellation is a device-managed action")
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tree", type=Path, required=True)
    args = parser.parse_args()
    report = Report()
    check(args.tree, report)
    write_summary(report, "The TX quarantine",
                  tree_fingerprint(args.tree, ("zx279133-idm.c",
                                               "zx279133-netdev.c",
                                               "zx279133-main.c",
                                               "zx279133.h")))
    if report.failures:
        print(f"\nFAILED: {len(report.failures)} of {report.checks} check(s)")
        for item in report.failures:
            print(f"  - {item}")
        return 1
    print(f"\nOK: {report.checks} check(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
