#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Check that the 7.3 tree does not delegate the transmit checksum away.

This SoC's transmit path can add up the pseudo header of a TCP segment,
but nothing writes the finished checksum into the header of the frame
that leaves for a client.  The driver used to ask for NETIF_F_HW_CSUM on
that basis, so the stack handed the checksum over instead of computing it
and the frames went out with a placeholder in the TCP header.

Measured on the board, with the client on the 2.5G jack: every TCP frame
the board sent carried a checksum that does not verify - the same value in
every retransmission, while the timestamp option inside those same frames
changed every time.  The peer dropped those frames without a word, while
ICMP over the very same path was perfect in both directions.  The board's
own counters agreed: tx_hw_csum_packets was 127, tx_sw_csum_packets was 0.

What is checked here, and why each one is asked the way it is:

  * the transmit checksum is not delegated: the flag in the header has to
    read 0, so the stack computes the checksum in software.
  * the feature is not declared unconditionally: the declaration has to
    stay inside the guard, so a later change that turns the flag back on
    cannot silently bring the feature with it.
  * the software path is still there: a frame that arrives with
    CHECKSUM_PARTIAL anyway has to be finished in the driver.
  * the decision still reads the flag: a literal true would be the same
    defect written differently.
  * no other file in the driver declares the feature: one declaration in
    one place is what makes the property checkable.
  * the header says why: a flag that is off needs its reason next to it,
    so the next reader does not turn it on to "fix" the CPU load.

None of the driver can be compiled on the machine that pushes this
repository: there is no aarch64 cross toolchain there, and the build only
happens in CI, seventy minutes into a job.  So what a compiler would say
about the shape of the code is checked here instead, against the tree the
build will actually use.

Usage:
  check-sk-lan-tcp-csum.py --tree TREE
"""
import argparse
import pathlib
import re
import sys

NL = chr(10)
ZTE = "drivers/net/ethernet/zte"


def read(tree, name):
    path = pathlib.Path(tree) / ZTE / name
    if not path.is_file():
        return None
    return path.read_text(encoding="utf-8", errors="replace")


def guarded(lines, guard_text):
    """The line numbers of the block a guard opens, and where it closes."""
    starts = [i for i, line in enumerate(lines) if guard_text in line]
    spans = []
    for start in starts:
        depth = 0
        opened = False
        for i in range(start, len(lines)):
            depth += lines[i].count("{") - lines[i].count("}")
            if "{" in lines[i]:
                opened = True
            if opened and depth == 0:
                spans.append((start, i))
                break
    return spans


def code_lines(text):
    """The lines of a file that are code, not a comment."""
    keep = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("*") or stripped.startswith("/*") or \
                stripped.startswith("//"):
            continue
        keep.append(line)
    return keep


def run_checks(tree):
    results = []

    header = read(tree, "zx279133.h")
    main_c = read(tree, "zx279133-main.c")
    netdev_c = read(tree, "zx279133-netdev.c")
    if header is None or main_c is None or netdev_c is None:
        return [("tree", False, "the 7.3 driver files are missing under " + ZTE)]

    match = re.search(r"#define\s+zx279133_tx_hw_csum\s+(\d+)", header)
    value = match.group(1) if match else None
    results.append((
        "the transmit checksum is not delegated",
        value == "0",
        "zx279133_tx_hw_csum reads " + (value if value else "nothing at all"),
    ))

    main_lines = code_lines(main_c)
    decls = [i for i, line in enumerate(main_lines) if "NETIF_F_HW_CSUM" in line]
    spans = guarded(main_lines, "if (zx279133_tx_hw_csum)")
    inside = bool(spans) and all(
        any(start < i < end for start, end in spans) for i in decls
    ) and len(decls) == 3
    results.append((
        "the feature is not declared unconditionally",
        inside,
        "the NETIF_F_HW_CSUM declarations sit inside the guard" if inside
        else "the NETIF_F_HW_CSUM declarations are not inside the guard",
    ))

    has_help = "skb_checksum_help(skb)" in netdev_c
    results.append((
        "the software path is still there",
        has_help,
        "skb_checksum_help(skb) finishes a partial checksum" if has_help
        else "skb_checksum_help(skb) is gone - a partial checksum would go out unfinished",
    ))

    assigns = re.findall(r"hw_csum\s*=\s*([^;]+);", netdev_c)
    reads_flag = any("zx279133_tx_hw_csum" in a for a in assigns)
    results.append((
        "the decision still reads the flag",
        reads_flag,
        "the hw_csum decision reads the flag" if reads_flag
        else "the hw_csum decision does not read the flag",
    ))

    stray = []
    for path in sorted((pathlib.Path(tree) / ZTE).glob("*.c")) + \
            sorted((pathlib.Path(tree) / ZTE).glob("*.h")):
        text = path.read_text(encoding="utf-8", errors="replace")
        if path.name == "zx279133-main.c":
            continue
        if any("NETIF_F_HW_CSUM" in line for line in code_lines(text)):
            stray.append(path.name)
    results.append((
        "no other file in the driver declares the feature",
        not stray,
        "the feature is declared in one place only" if not stray
        else "the feature is also declared in " + ", ".join(stray),
    ))

    lines = header.splitlines()
    idx = [i for i, line in enumerate(lines) if "zx279133_tx_hw_csum" in line and "#define" in line]
    reason = False
    if idx:
        window = NL.join(lines[max(0, idx[0] - 30): idx[0]])
        reason = "checksum" in window.lower()
    results.append((
        "the header says why",
        reason,
        "the flag carries its reason next to it" if reason
        else "the flag has no reason next to it",
    ))

    partial = "skb->ip_summed == CHECKSUM_PARTIAL" in netdev_c
    results.append((
        "a partial checksum is still handled",
        partial,
        "the driver still handles CHECKSUM_PARTIAL" if partial
        else "the driver does not handle CHECKSUM_PARTIAL at all",
    ))

    return results


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tree", required=True, help="the prepared 7.3 tree")
    args = parser.parse_args(argv)

    bad = 0
    for name, ok, detail in run_checks(args.tree):
        if ok:
            print("ok   " + name + ": " + detail)
        else:
            print("FAIL " + name + ": " + detail)
            bad += 1
    if bad:
        print("check-sk-lan-tcp-csum: %d check(s) failed" % bad)
        return 1
    print("check-sk-lan-tcp-csum: every check passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
