#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Check that the 7.3 tree's RX delivery path proves what it hands over.

The 7.3 tree handed frames to the stack on the strength of what the
descriptor said and of what the frame's own header happened to look like.
Both are weaker than the stack needs.

The sharp one is the L2 rewrite.  The 9132 is a transparent MAC-learning
bridge, so the frame the CPU receives still has the Ethernet header the
client sent; zx279133_idm_rx_restore_lan_l2() exists for the other shape, a
frame whose type field the PPU took off.  Applied to a frame that has one,
it shifts the payload four bytes to the right and writes the type and the
IP version word twice, so the IP header the stack reads starts four bytes
too far in and every IPv4 and IPv6 frame is dropped for a total length of
0x0800.  The 6.18 generation guards the rewrite with
zx279133_idm_rx_has_ethertype(); the 7.3 tree called it unconditionally.
That guard moves with it.

The rest of the path had three more places where it read past the end of
what it had: the ARP branch of the rewrite read the hardware type and the
protocol type without asking how long the frame was, the delivery path
never asked for ETH_HLEN before reading the frame as an Ethernet header,
and an XDP program that moved the frame's head left the stack with a
pointer outside the buffer.

Two more places changed what the frame said about itself.  The tree marked a
frame CHECKSUM_UNNECESSARY when the PPU's "parsed" flag was set and the
header's shape checked out, but a parsed header is not a verified checksum,
and CHECKSUM_UNNECESSARY tells the stack to skip the check.  And a buffer
was posted back to the hardware without the device-side synchronisation:
the IDM is not coherent with the CPU's cache, and the window the hardware
writes into starts where the driver posted the buffer, not where the frame
was.

None of the driver can be compiled on the machine that pushes this
repository: there is no aarch64 cross toolchain there, and the build only
happens in CI, seventy minutes into a job.  So what a compiler would say
about the shape of the code is checked here instead, against the tree the
build will actually use.

What is checked, and why each one is asked the way it is:

  * the L2 rewrite is guarded by the ethertype test, and the guard is
    asked as a position: the condition that holds the call has to name the
    test, not merely the file.
  * the ARP branch asks how long the frame is before it reads the two
    fields behind the addresses, again as a position.
  * the delivery path asks for ETH_HLEN before the frame is read as an
    Ethernet header, and it does so before the ethertype test reads it.
  * an XDP PASS is checked against the buffer before the frame's data
    pointer is taken from it, in both the AF_XDP and the page-pool path.
  * the frame is handed up as CHECKSUM_NONE, and nothing in the file marks
    a frame CHECKSUM_UNNECESSARY.
  * the device-side synchronisation covers the whole window the hardware
    writes into, and it happens where the buffer is handed back - the one
    place every path goes through - and before the buffer is posted.

Usage:
  check-sk-rx-delivery.py --tree TREE
"""
import argparse
import hashlib
import os
import re
import sys
from pathlib import Path

ZTE = "drivers/net/ethernet/zte"


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


def call_sites(text, name):
    """Where a function is called, as offsets in the text.

    The declaration and the definition are not calls, so both are taken
    out: a definition opens a brace, and a declaration carries a type in
    front of the name.  A call ends in a semicolon like a declaration
    does, so the semicolon alone does not tell them apart.
    """
    sites = []
    for match in re.finditer(r"\b" + re.escape(name) + r"\s*\(", text):
        tail = text[match.end():match.end() + 400]
        close = tail.find(")")
        if close < 0:
            continue
        after = tail[close + 1:close + 40].lstrip()
        if after.startswith("{"):
            continue
        prefix = text[max(0, match.start() - 200):match.start()]
        if re.search(r"\bstatic\b[^;{}]*$", prefix) is not None:
            continue
        sites.append(match.start())
    return sites


def enclosing_condition(body, position):
    """The condition that holds the statement at `position`.

    The nearest `if (` or `else if (` above the statement is the one that
    decides it; a condition that is written further up belongs to an
    outer statement and says nothing about this one.
    """
    best = -1
    for match in re.finditer(r"\b(?:else\s+)?if\s*\(", body[:position]):
        best = match.start()
    return best


def case_block(body, position):
    """The switch case the statement at `position` sits in, and its start.

    The start is an offset into the body, not into the case, so that a
    position inside the case can be compared with a position in the body.
    """
    start = body.rfind("case ", 0, position)
    if start < 0:
        return 0, body
    nxt = body.find("case ", position)
    return start, body[start:nxt if nxt > 0 else len(body)]


def read(tree, name):
    path = Path(tree) / ZTE / name
    if not path.is_file():
        return None
    return strip_comments(path.read_text(encoding="utf-8", errors="replace"))


def check(tree, report):
    idm = read(tree, "zx279133-idm.c")
    if not report.require(idm is not None,
                          "the tree has zx279133-idm.c"):
        return report

    process = function_body(idm, "zx279133_idm_rx_process_queue")
    if not report.require(process is not None,
                          "the tree has zx279133_idm_rx_process_queue"):
        return report

    # 1. The ethertype test exists and asks what it needs to ask.
    has_ethertype = function_body(idm, "zx279133_idm_rx_has_ethertype")
    if report.require(has_ethertype is not None,
                      "the tree has zx279133_idm_rx_has_ethertype"):
        report.require(re.search(r"skb->len\s*<\s*ETH_HLEN", has_ethertype)
                       is not None,
                       "the ethertype test refuses a frame shorter than a header")
        report.require(re.search(
            r"get_unaligned_be16\s*\(\s*skb->data\s*\+\s*2\s*\*\s*ETH_ALEN\s*\)"
            r"\s*>=\s*ETH_P_802_3_MIN", has_ethertype) is not None,
            "the ethertype test reads the type field")

    # 2. The L2 rewrite is guarded by that test.  The guard is asked as a
    #    position: the condition that holds the call has to name the test.
    rewrite_sites = call_sites(process, "zx279133_idm_rx_restore_lan_l2")
    ethertype_use = -1
    if report.require(len(rewrite_sites) == 1,
                      "the delivery path rewrites the L2 header once",
                      f"{len(rewrite_sites)} call site(s)"):
        condition = enclosing_condition(process, rewrite_sites[0])
        guard = process[condition:rewrite_sites[0]] if condition >= 0 else ""
        report.require("zx279133_idm_rx_has_ethertype" in guard,
                       "the L2 rewrite only runs on a frame without a type field",
                       guard.strip()[:120] if guard else "no enclosing condition")
        offset = guard.find("zx279133_idm_rx_has_ethertype")
        if offset >= 0:
            ethertype_use = condition + offset

    # 3. The ARP branch asks how long the frame is before it reads it.
    restore = function_body(idm, "zx279133_idm_rx_restore_lan_l2")
    if report.require(restore is not None,
                      "the tree has zx279133_idm_rx_restore_lan_l2"):
        arp = restore.find("== ETH_P_IP")
        if report.require(arp >= 0,
                          "the L2 rewrite has an ARP branch"):
            condition = enclosing_condition(restore, arp)
            chain = restore[condition:arp] if condition >= 0 else ""
            report.require(re.search(r"skb->len\s*>=\s*2\s*\*\s*ETH_ALEN\s*\+\s*4",
                                     chain) is not None,
                           "the ARP branch asks how long the frame is",
                           chain.strip()[:120] if chain else "no condition")

    # 4. The delivery path asks for ETH_HLEN before it reads the header.
    pull = process.find("pskb_may_pull(skb, ETH_HLEN)")
    report.require(pull >= 0,
                   "the delivery path asks for a whole Ethernet header")
    if pull >= 0:
        report.require("dev_kfree_skb_any" in process[pull:pull + 400],
                       "a frame that cannot be pulled is released")
        report.require("goto release_desc" in process[pull:pull + 400],
                       "a frame that cannot be pulled leaves the receive path")
        first_use = process.find("zx279133_idm_rx_has_ethertype")
        report.require(0 <= pull < first_use,
                       "the header is asked for before the ethertype test reads it",
                       f"pull at {pull}, test at {first_use}")
        report.require(0 <= pull < ethertype_use,
                       "the header is asked for before the rewrite tests the frame",
                       f"pull at {pull}, test at {ethertype_use}")

    # 5. An XDP PASS is checked against the buffer before it is taken from it.
    valid = function_body(idm, "zx279133_idm_xdp_pass_valid")
    if report.require(valid is not None,
                      "the tree has zx279133_idm_xdp_pass_valid"):
        for needle, message in (
                ("xdp_buff_has_frags", "the XDP check refuses a fragmented frame"),
                ("data_hard_start", "the XDP check knows where the buffer starts"),
                ("end >= data", "the XDP check refuses an empty frame"),
                ("end <= hard_end", "the XDP check refuses a frame past the buffer"),
                ("end - data >= ETH_HLEN", "the XDP check insists on a whole header")):
            report.require(needle in valid, message)
    for argument, label in (("xdp, true", "the AF_XDP path"),
                            ("&xdp, false", "the page-pool path")):
        needle = f"zx279133_idm_xdp_pass_valid({argument})"
        site = process.find(needle)
        if not report.require(site >= 0,
                              f"{label} checks the frame before it hands it over"):
            continue
        block_start, block = case_block(process, site)
        taken = block.find("data = xdp")
        report.require(0 <= site < block_start + taken,
                       f"{label} takes the frame's data pointer after the check",
                       f"check at {site}, data at {block_start + taken}")

    # 6. The frame is handed up as CHECKSUM_NONE and nothing claims the
    #    stack may skip the checksum.
    report.require("CHECKSUM_UNNECESSARY" not in idm,
                   "no frame is marked as having a verified checksum")
    report.require("zx279133_idm_rx_parsed" not in idm,
                   "the parsed-descriptor test is gone with the claim it carried")
    report.require("u64_stats_inc(&stats->rx_hw_csum_packets)" not in idm,
                   "no frame is counted as a hardware checksum")
    none = process.find("skb->ip_summed = CHECKSUM_NONE;")
    report.require(none >= 0,
                   "the frame is handed up with the checksum left to the stack")
    if pull >= 0 and none >= 0:
        report.require(pull < none,
                       "the checksum is set after the header is in place")

    # 7. The buffer is handed back with the whole window synchronised.
    post = function_body(idm, "__zx279133_idm_rx_post_page")
    if report.require(post is not None,
                      "the tree has __zx279133_idm_rx_post_page"):
        sync = post.find("zx279133_idm_rx_sync_for_device(")
        posted = post.find("WRITE_ONCE(eth->rx_normal_bp[")
        report.require(sync >= 0,
                       "a buffer is synchronised before it goes back")
        report.require(0 <= sync < posted,
                       "the synchronisation happens before the buffer is posted",
                       f"sync at {sync}, posted at {posted}")
    sync_def = function_body(idm, "zx279133_idm_rx_sync_for_device")
    if report.require(sync_def is not None,
                      "the tree has zx279133_idm_rx_sync_for_device"):
        report.require(re.search(
            r"unsigned\s+int\s+size\s*=\s*zx279133_idm_rx_frame_offset\s*\+"
            r"\s*ZX279133_IDM_RX_FRAME_LIMIT", sync_def) is not None,
            "the synchronisation covers the whole window the hardware writes into")
        report.require("dma, size, DMA_BIDIRECTIONAL" in sync_def,
                       "the whole window is what is handed over")
        report.require("dma_sync_single_range_for_device" not in sync_def,
                       "the window is not handed over by the range helper")
        report.require(re.search(
            r"(__dma_sync_single_for_device|dma_sync_single_for_device)\s*\("
            r"\s*eth->dev", sync_def) is not None,
            "the window is handed over with the call that ignores the tree's flag")
    sites = call_sites(idm, "zx279133_idm_rx_sync_for_device")
    report.require(len(sites) == 1,
                   "the device-side synchronisation has exactly one place",
                   f"{len(sites)} call site(s)")
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tree", type=Path, required=True)
    args = parser.parse_args()
    report = Report()
    check(args.tree, report)
    write_summary(report, "The RX delivery path",
                  tree_fingerprint(args.tree, ("zx279133-idm.c",)))
    if report.failures:
        print(f"\nFAILED: {len(report.failures)} of {report.checks} check(s)")
        for item in report.failures:
            print(f"  - {item}")
        return 1
    print(f"\nOK: {report.checks} check(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
