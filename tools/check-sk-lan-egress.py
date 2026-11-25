#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Check that the 7.3 tree's LAN egress names the jack the frame is for.

The board's LAN jacks are the 9132's four UNI ports.  A frame the CPU
sends to a jack has to name that jack: the IDM TX descriptor's output
port field is the only thing that says which port the frame leaves by,
and the vendor's own transmit descriptors put the target UNI jack's chip
port there - 1..4, never 5.

The 7.3 tree wrote one constant there, 5, which is XMAC0: a 10G SerDes
MAC with no peer on this board and that the vendor never programs.  So
every frame the CPU sent to a jack was aimed at a MAC with no peer.  The
frames left the CPU - the bridge's and the conduit's TX counters rose -
entered the 9132 and never came out of a jack.  That is what the board
showed: it received from every jack and nothing it sent reached one.

The destination was not unknown.  The DSA tagger on this SoC's LAN
conduit pushes an 802.1Q header with the transport VID of the user port
the frame is for onto every frame that leaves for a jack, and the 9132's
PPU ports 1..4 carry the VIDs 59..62.  The 6.18 generation reads that
VID off the frame, takes the tag off it and puts the chip port it names
into the descriptor.  The 7.3 tree neither read it nor took it off.

So what is checked here, and why each one is asked the way it is:

  * the LAN egress names the chip port the transport VID carries: the
    strip is there, it is called with an out port, and the port that
    goes into the descriptor is the one it returns.
  * the chip port the VID names is the one the receive path names: both
    directions use the same VID range and the same first-chip-port
    constant, so they cannot drift apart.
  * the transport tag comes off the frame before the descriptor is
    filled: a frame that leaves the chip tagged is a frame the client
    cannot use.
  * the LAN path writes the vendor's fourth metadata word: 0x003e0000,
    the GEM port's LLID id plus its queue id, where the tree wrote the
    PON control word on both paths.
  * a frame that carries no transport VID goes out of a UNI port: the
    fallback cannot be 5, because 5 is a MAC with no peer.
  * the tag is only taken off after the ring capacity check: a
    NETDEV_TX_BUSY return has to leave the tag intact, because the tag
    names the destination jack on a retry.

None of the driver can be compiled on the machine that pushes this
repository: there is no aarch64 cross toolchain there, and the build
only happens in CI, seventy minutes into a job.  So what a compiler
would say about the shape of the code is checked here instead, against
the tree the build will actually use.

Usage:
  check-sk-lan-egress.py --tree TREE
"""
import argparse
import hashlib
import os
import re
import sys
from pathlib import Path

ZTE = "drivers/net/ethernet/zte"
FILES = ["zx279133.h", "zx279133-netdev.c", "zx279133-idm.c"]

STRIP_CALL = "zx279133_lan_strip_transport_vlan(skb, &lan_out_port)"


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


def check_tree(tree, report):
    """The checks, against the tree the build will compile."""
    zte = Path(tree) / ZTE
    for name in FILES:
        if not (zte / name).is_file():
            report.require(False, f"the tree has {name}",
                           "the gate cannot read a file that is not there")
            return
    netdev = (zte / "zx279133-netdev.c").read_text(encoding="utf-8")
    hdr = (zte / "zx279133.h").read_text(encoding="utf-8")
    idm = (zte / "zx279133-idm.c").read_text(encoding="utf-8")

    fn = netdev.find("static netdev_tx_t zx279133_start_xmit_common(")
    body = netdev[fn:] if fn >= 0 else ""
    strip = body.find(STRIP_CALL)
    busy = body.find("return NETDEV_TX_BUSY;")
    meta = body.find("desc->metadata[0] = cpu_to_le32(metadata0);")

    report.require(
        "static int zx279133_lan_strip_transport_vlan(" in netdev
        and strip >= 0
        and re.search(r"tx_port = lan_out_port \? lan_out_port : "
                      r"ZX279133_LAN_TX_PORT;", netdev) is not None,
        "the LAN egress names the chip port the transport VID carries",
        "the output port is still a constant, so every frame leaves by the "
        "same jack - 5, which is a MAC with no peer on this board")

    report.require(
        "ZX279133_LAN_TRANSPORT_VID_MIN -" in netdev
        and "ZX279133_9132_UNI_PPU_FIRST" in netdev
        and "ZX279133_LAN_TRANSPORT_VID_MIN +" in idm
        and "ZX279133_9132_UNI_PPU_FIRST" in idm,
        "the chip port the VID names is the one the receive path names",
        "the transmit and the receive side do not use the same VID range "
        "and the same first chip port, so a frame can leave by one port "
        "and be counted as another")

    report.require(
        "__skb_vlan_pop(skb, &tci)" in netdev
        and 0 <= strip < meta,
        "the transport tag comes off the frame before the descriptor is filled",
        "the frame would leave the chip with the tag still on it, and a "
        "tagged frame is a frame the client cannot use")

    word4 = re.search(r"#define ZX279133_LAN_TX_WORD4[ \t]+(\S+)", hdr)
    report.require(
        word4 is not None
        and word4.group(1) == "0x003e0000"
        and "ZX279133_LAN_TX_WORD4" in netdev
        and "zx279133_tx_pon_control" in netdev,
        "the LAN path writes the vendor's fourth metadata word",
        "the fourth metadata word is the PON control word on the LAN path "
        "as well; the vendor writes 0x003e0000 there")

    port = re.search(r"#define ZX279133_LAN_TX_PORT[ \t]+(\d+)", hdr)
    report.require(
        port is not None and 1 <= int(port.group(1)) <= 4,
        "a frame with no transport VID goes out of a UNI port",
        "the fallback port is not one of the 9132's four UNI ports")

    report.require(
        0 <= strip and busy >= 0 and strip > busy,
        "the tag is only taken off after the ring capacity check",
        "a NETDEV_TX_BUSY return would have to leave the tag intact: the "
        "tag names the destination jack on a retry")


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
    print(f"the LAN egress of {tree}")
    check_tree(tree, report)
    write_summary(report, "The LAN egress names the jack the frame is for",
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
