#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Check that the 7.3 tree's transport VID range covers the 2.5G jack.

The 9132 on SK-D840N has five jacks.  Four of them are the UNI ports,
and they carry the transport VIDs 59..62.  The fifth one is the 2.5G
jack: it is the vendor's outerphy, PPU 5, and it is DSA port 8, so it
carries the transport VID 55 + 8 = 63.

The DSA tagger on this board already knows about that port - the board
layer raises the tagger's own USER_PORT_MAX to 8 - so a frame that
comes in on the 2.5G jack arrives tagged with 63 and is dispatched to
the right port.  The receive side of the jack works.

The transmit side reads the tree's own copy of the range, and that copy
stopped at 62.  The tagger writes the transport VID of the port a frame
is for onto the frame as it leaves for a jack, so the frame the CPU
sends to the 2.5G jack leaves with a transport VID of 63 - and the LAN
egress (zx279133_lan_strip_transport_vlan()) only recognises the VIDs
inside the tree's range.  A VID of 63 is outside it, so the egress
neither takes the tag off the frame nor reads the chip port out of it:
the frame leaves the chip aimed at the fallback port, with the tag still
on it, and the client on the 2.5G jack sees nothing.  The board counts
the frame as sent.  That is the whole of the egress failure.

So what is checked here, and why each one is asked the way it is:

  * the tree's transport VID range covers the 2.5G jack's VID: the
    range is 59..63, which is what the 6.18 generation has and what
    its 2.5G jack works with.
  * the tagger accepts the port that VID names: the board layer's
    USER_PORT_MAX and the tree's VID_MAX have to be the same number,
    or the two directions do not agree on what a frame from the 2.5G
    jack is.
  * the chip port the VID names is the 2.5G jack's port: VID 63 has to
    name chip port 5, the outerphy, and the mapping has to be the one
    the egress actually uses.
  * the receive path maps the 2.5G jack onto the same VID: the PPU
    number 5 is turned into a transport VID, and it has to land on the
    VID the transmit side writes.
  * every transport VID has an MF extension entry: the table is
    indexed by the VID, so a logical port with no entry reads past the
    end of the array and writes whatever follows it into the switch.
  * the fifth entry is the 2.5G jack's: it is the one number in that
    table that is not a measurement, so it is written down here and
    compared against the 6.18 generation's entry, word for word.
  * the table cannot fall behind the range: the static assertion is
    what makes a fifth entry impossible to forget.
  * the LAN egress reads the VID out of the range it writes: a literal
    in the strip function would drift away from the header.

None of the driver can be compiled on the machine that pushes this
repository: there is no aarch64 cross toolchain there, and the build
only happens in CI, seventy minutes into a job.  So what a compiler
would say about the shape of the code is checked here instead, against
the tree the build will actually use.

Usage:
  check-sk-lan-vid63.py --tree TREE
"""
import argparse
import hashlib
import os
import re
import sys
from pathlib import Path

FILES = [
    "drivers/net/ethernet/zte/zx279133.h",
    "drivers/net/ethernet/zte/zx279133-netdev.c",
    "drivers/net/ethernet/zte/zx279133-idm.c",
    "drivers/net/ethernet/zte/zx279133-np.c",
    "drivers/net/ethernet/zte/zx279133-9132.h",
    "include/net/dsa_zx279133_rtl8372n.h",
]

# The fifth entry of the logical LAN ports' MF extension table: the one
# number in that table that is not a measurement.  It is the 6.18
# generation's entry, word for word.
FIFTH_ENTRY = ("0x1c8c2800", "0x31a37e00", "0x00000ad8", "0")

ENTRY_RE = re.compile(
    r"\{\s*(0x[0-9a-f]+),\s*(0x[0-9a-f]+),\s*(0x[0-9a-f]+),\s*(0x[0-9a-f]+|0)\s*\}")


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
    """What the files this gate read amount to."""
    out = []
    for name in names:
        path = Path(tree) / name
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


def define(text, name):
    """The value of a #define, as it is written in the file."""
    match = re.search(r"^#define\s+" + re.escape(name) + r"[ \t]+(\S+)",
                      text, re.M)
    return match.group(1) if match else None


def number(text, name):
    value = define(text, name)
    if value is None:
        return None
    try:
        return int(value, 0)
    except ValueError:
        return None


def mf_entries(np):
    """The entries of the logical LAN ports' MF extension table."""
    match = re.search(
        r"static const u32 zx279133_lan_vport_mf\[\]\[ZX279133_SMMU0_WORDS\]"
        r"\s*=\s*\{(.*?)\n\};", np, re.S)
    if not match:
        return []
    return ENTRY_RE.findall(match.group(1))


def check_tree(tree, report):
    """The checks, against the tree the build will compile."""
    texts = {}
    for name in FILES:
        path = Path(tree) / name
        if not path.is_file():
            report.require(False, f"the tree has {name}",
                           "the gate cannot read a file that is not there")
            return
        texts[name] = path.read_text(encoding="utf-8")

    hdr = texts["drivers/net/ethernet/zte/zx279133.h"]
    netdev = texts["drivers/net/ethernet/zte/zx279133-netdev.c"]
    idm = texts["drivers/net/ethernet/zte/zx279133-idm.c"]
    np = texts["drivers/net/ethernet/zte/zx279133-np.c"]
    h9132 = texts["drivers/net/ethernet/zte/zx279133-9132.h"]
    taghdr = texts["include/net/dsa_zx279133_rtl8372n.h"]

    vid_min = number(hdr, "ZX279133_LAN_TRANSPORT_VID_MIN")
    vid_max = number(hdr, "ZX279133_LAN_TRANSPORT_VID_MAX")
    uni_first = number(h9132, "ZX279133_9132_UNI_PPU_FIRST")
    outer_ppu = number(h9132, "ZX279133_9132_OUTERPHY_PPU")
    base = number(taghdr, "ZX279133_RTL8372N_TRANSPORT_VID_BASE")
    user_max = number(taghdr, "ZX279133_RTL8372N_USER_PORT_MAX")

    report.require(
        vid_min == 59 and vid_max == 63,
        "the tree's transport VID range covers the 2.5G jack's VID",
        "the range is not 59..63, so the VID 63 the tagger writes for the "
        "2.5G jack is not one of ours and the egress drops the frame's "
        "destination")

    report.require(
        base is not None and user_max == 8 and base + user_max == vid_max,
        "the tagger accepts the port that VID names",
        "the tagger's own range and the tree's range do not name the same "
        "port, so the two directions disagree about a frame from the "
        "2.5G jack")

    chip_port = None
    if None not in (vid_min, vid_max, uni_first, outer_ppu):
        chip_port = vid_max - (vid_min - uni_first)
    report.require(
        chip_port is not None
        and chip_port == outer_ppu
        and "vid - (ZX279133_LAN_TRANSPORT_VID_MIN -" in netdev
        and "ZX279133_9132_UNI_PPU_FIRST" in netdev,
        "the chip port the VID names is the 2.5G jack's port",
        "the transport VID does not name the outerphy's chip port, so the "
        "frame leaves by a port that has no cable in it")

    rx_vid = None
    if None not in (vid_min, uni_first, outer_ppu):
        rx_vid = vid_min + (outer_ppu - uni_first)
    report.require(
        rx_vid is not None
        and rx_vid == vid_max
        and "ZX279133_LAN_TRANSPORT_VID_MIN +" in idm
        and "ZX279133_9132_OUTERPHY_PPU" in idm
        and "ZX279133_9132_UNI_PPU_FIRST" in idm,
        "the receive path maps the 2.5G jack onto the same VID",
        "the receive path and the transmit path name different VIDs for "
        "the 2.5G jack, so a frame from it is counted on one port and "
        "sent to another")

    entries = mf_entries(np)
    expected = (vid_max - vid_min + 1) if None not in (vid_min, vid_max) else 0
    report.require(
        len(entries) == expected and expected > 0,
        "every transport VID has an MF extension entry",
        "the table has %d entries and the range needs %d, so a logical "
        "port reads past the end of the array" % (len(entries), expected))

    report.require(
        bool(entries) and entries[-1] == FIFTH_ENTRY,
        "the fifth entry is the 2.5G jack's",
        "the last entry of the table is not the 6.18 generation's entry "
        "for the fifth port")

    report.require(
        "static_assert(ARRAY_SIZE(zx279133_lan_vport_mf) ==" in np
        and "ZX279133_LAN_VPORT_LAST - ZX279133_LAN_VPORT_FIRST + 1" in np,
        "the table cannot fall behind the range",
        "without the assertion a fifth port can arrive without a fifth "
        "entry, and the table is read past its end")

    start = netdev.find("static int zx279133_lan_strip_transport_vlan(")
    strip = netdev[start:] if start >= 0 else ""
    report.require(
        "vid < ZX279133_LAN_TRANSPORT_VID_MIN ||" in strip
        and "vid > ZX279133_LAN_TRANSPORT_VID_MAX" in strip,
        "the LAN egress reads the VID out of the range it writes",
        "the strip function compares the VID against something else, so "
        "it drifts away from the header")


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
    print(f"the transport VID range of {tree}")
    check_tree(tree, report)
    write_summary(report,
                  "The transport VID range covers the 2.5G jack",
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
