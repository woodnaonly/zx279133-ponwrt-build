#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Negative controls for tools/check-sk-rx-delivery.py.

The gate asks whether the 7.3 tree's RX delivery path proves what it hands
over.  Each control below breaks one of the rules it asks about, and the
gate has to turn red *on the check that names that rule* - not merely red
somewhere.

The tree is the one the build will use, so the controls run against it: a
synthetic driver would only prove that the gate reads a synthetic driver.

    python3 tests/test_sk_rx_delivery.py --tree TREE
"""
import argparse
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GATE = ROOT / "tools/check-sk-rx-delivery.py"
ZTE = "drivers/net/ethernet/zte"

FILES = ["zx279133-idm.c"]

# (name, file, old, new, the check that has to go red)
MUTATIONS = [
    ("the ethertype guard is taken off the rewrite",
     "zx279133-idm.c",
     "\t\t\tif (queue < ZX279133_LAN_RX_QUEUE_COUNT &&\n"
     "\t\t\t    !zx279133_idm_rx_has_ethertype(skb))\n",
     "\t\t\tif (queue < ZX279133_LAN_RX_QUEUE_COUNT)\n",
     "the L2 rewrite only runs on a frame without a type field"),
    ("the ethertype test stops asking how long the frame is",
     "zx279133-idm.c",
     "\tif (skb->len < ETH_HLEN)\n\t\treturn false;\n\n",
     "",
     "the ethertype test refuses a frame shorter than a header"),
    ("the ethertype test reads the wrong field",
     "zx279133-idm.c",
     "get_unaligned_be16(skb->data + 2 * ETH_ALEN) >= ETH_P_802_3_MIN",
     "get_unaligned_be16(skb->data + 2 * ETH_ALEN) == ETH_P_IP",
     "the ethertype test reads the type field"),
    ("the ARP branch reads the frame without asking how long it is",
     "zx279133-idm.c",
     "\t\t   skb->len >= 2 * ETH_ALEN + 4 &&\n",
     "",
     "the ARP branch asks how long the frame is"),
    ("the delivery path stops asking for a whole header",
     "zx279133-idm.c",
     "\t\t\tif (unlikely(!pskb_may_pull(skb, ETH_HLEN))) {\n",
     "",
     "the delivery path asks for a whole Ethernet header"),
    ("a frame that cannot be pulled is not released",
     "zx279133-idm.c",
     "\t\t\t\tdev_kfree_skb_any(skb);\n\t\t\t\tgoto release_desc;\n",
     "\t\t\t\tgoto release_desc;\n",
     "a frame that cannot be pulled is released"),
    ("a frame that cannot be pulled stays in the receive path",
     "zx279133-idm.c",
     "\t\t\t\tdev_kfree_skb_any(skb);\n\t\t\t\tgoto release_desc;\n",
     "\t\t\t\tdev_kfree_skb_any(skb);\n",
     "a frame that cannot be pulled leaves the receive path"),
    ("the ethertype test runs before the header is in place",
     "zx279133-idm.c",
     "\t\t\tif (unlikely(!pskb_may_pull(skb, ETH_HLEN))) {\n",
     "\t\t\tif (zx279133_idm_rx_has_ethertype(skb))\n"
     "\t\t\t\tlen += (u16)0;\n"
     "\t\t\tif (unlikely(!pskb_may_pull(skb, ETH_HLEN))) {\n",
     "the header is asked for before the ethertype test reads it"),
    ("the XDP check is gone",
     "zx279133-idm.c",
     "static bool zx279133_idm_xdp_pass_valid(struct xdp_buff *xdp, bool xsk)",
     "static bool zx279133_idm_xdp_pass_valid_x(struct xdp_buff *xdp, bool xsk)",
     "the tree has zx279133_idm_xdp_pass_valid"),
    ("the XDP check stops refusing a fragmented frame",
     "zx279133-idm.c",
     "\treturn !xdp_buff_has_frags(xdp) && data >= start && end >= data &&\n",
     "\treturn data >= start && end >= data &&\n",
     "the XDP check refuses a fragmented frame"),
    ("the XDP check stops refusing a frame past the buffer",
     "zx279133-idm.c",
     "\t       end <= hard_end && end - data >= ETH_HLEN;",
     "\t       end >= hard_end && end - data >= ETH_HLEN;",
     "the XDP check refuses a frame past the buffer"),
    ("the AF_XDP path stops checking the frame",
     "zx279133-idm.c",
     "\t\t\t\tif (unlikely(!zx279133_idm_xdp_pass_valid(xdp, true))) {\n"
     "\t\t\t\t\tzx279133_stats_rx_error(eth, ndev);\n"
     "\t\t\t\t\tzx279133_stats_rx_length_error(eth, ndev);\n"
     "\t\t\t\t\tzx279133_stats_rx_dropped(eth, ndev);\n"
     "\t\t\t\t\tgoto xsk_recycle;\n"
     "\t\t\t\t}\n",
     "",
     "the AF_XDP path checks the frame before it hands it over"),
    ("the page-pool path stops checking the frame",
     "zx279133-idm.c",
     "\t\t\t\t\tif (unlikely(!zx279133_idm_xdp_pass_valid(&xdp, false))) {\n"
     "\t\t\t\t\t\tzx279133_stats_rx_error(eth, ndev);\n"
     "\t\t\t\t\t\tzx279133_stats_rx_length_error(eth, ndev);\n"
     "\t\t\t\t\t\tzx279133_stats_rx_dropped(eth, ndev);\n"
     "\t\t\t\t\t\tgoto xdp_recycle;\n"
     "\t\t\t\t\t}\n",
     "",
     "the page-pool path checks the frame before it hands it over"),
    ("the AF_XDP path takes the frame before it checks it",
     "zx279133-idm.c",
     "\t\t\t\tif (unlikely(!zx279133_idm_xdp_pass_valid(xdp, true))) {\n"
     "\t\t\t\t\tzx279133_stats_rx_error(eth, ndev);\n"
     "\t\t\t\t\tzx279133_stats_rx_length_error(eth, ndev);\n"
     "\t\t\t\t\tzx279133_stats_rx_dropped(eth, ndev);\n"
     "\t\t\t\t\tgoto xsk_recycle;\n"
     "\t\t\t\t}\n"
     "\t\t\t\tdata = xdp->data;\n",
     "\t\t\t\tdata = xdp->data;\n"
     "\t\t\t\tif (unlikely(!zx279133_idm_xdp_pass_valid(xdp, true))) {\n"
     "\t\t\t\t\tzx279133_stats_rx_error(eth, ndev);\n"
     "\t\t\t\t\tzx279133_stats_rx_length_error(eth, ndev);\n"
     "\t\t\t\t\tzx279133_stats_rx_dropped(eth, ndev);\n"
     "\t\t\t\t\tgoto xsk_recycle;\n"
     "\t\t\t\t}\n",
     "the AF_XDP path takes the frame's data pointer after the check"),
    ("a frame is marked as having a verified checksum again",
     "zx279133-idm.c",
     "\t\t\tskb->ip_summed = CHECKSUM_NONE;\n",
     "\t\t\tskb->ip_summed = CHECKSUM_UNNECESSARY;\n",
     "no frame is marked as having a verified checksum"),
    ("the parsed-descriptor test comes back",
     "zx279133-idm.c",
     "static u16 zx279133_idm_rx_restore_lan_l2(struct sk_buff *skb, u32 metadata0)",
     "static bool zx279133_idm_rx_parsed(u32 word1, bool lan_source)\n"
     "{\n\treturn word1 != 0;\n}\n\n"
     "static u16 zx279133_idm_rx_restore_lan_l2(struct sk_buff *skb, u32 metadata0)",
     "the parsed-descriptor test is gone with the claim it carried"),
    ("the hardware checksum counter is fed again",
     "zx279133-idm.c",
     "\t\t\tskb->ip_summed = CHECKSUM_NONE;\n",
     "\t\t\tskb->ip_summed = CHECKSUM_NONE;\n"
     "\t\t\tu64_stats_inc(&stats->rx_hw_csum_packets);\n",
     "no frame is counted as a hardware checksum"),
    ("the frame is handed up without saying anything about the checksum",
     "zx279133-idm.c",
     "\t\t\tskb->ip_summed = CHECKSUM_NONE;\n",
     "",
     "the frame is handed up with the checksum left to the stack"),
    ("a buffer goes back to the hardware without a synchronisation",
     "zx279133-idm.c",
     "\tzx279133_idm_rx_sync_for_device(eth, dma);\n",
     "",
     "a buffer is synchronised before it goes back"),
    ("the buffer is posted before it is synchronised",
     "zx279133-idm.c",
     "\tzx279133_idm_rx_sync_for_device(eth, dma);\n"
     "\tWRITE_ONCE(eth->rx_normal_bp[eth->rx_bp_prod],\n"
     "\t\t   cpu_to_be32(lower_32_bits(dma)));\n",
     "\tWRITE_ONCE(eth->rx_normal_bp[eth->rx_bp_prod],\n"
     "\t\t   cpu_to_be32(lower_32_bits(dma)));\n"
     "\tzx279133_idm_rx_sync_for_device(eth, dma);\n",
     "the synchronisation happens before the buffer is posted"),
    ("the synchronisation covers only the frame",
     "zx279133-idm.c",
     "\tunsigned int size = zx279133_idm_rx_frame_offset +\n"
     "\t\t\t    ZX279133_IDM_RX_FRAME_LIMIT;",
     "\tunsigned int size = ZX279133_IDM_RX_FRAME_LIMIT;",
     "the synchronisation covers the whole window the hardware writes into"),
    ("the window is handed over by the range helper",
     "zx279133-idm.c",
     "\tdma_sync_single_for_device(eth->dev, dma, size, DMA_BIDIRECTIONAL);",
     "\tdma_sync_single_range_for_device(eth->dev, dma, 0, size,\n"
     "\t\t\t\t\t    DMA_BIDIRECTIONAL);",
     "the window is not handed over by the range helper"),
    ("the device-side synchronisation has a second place",
     "zx279133-idm.c",
     "\t\t\t\tzx279133_idm_rx_sync_xsk_for_device(eth, xdp);\n",
     "\t\t\t\tzx279133_idm_rx_sync_xsk_for_device(eth, xdp);\n"
     "\t\t\t\tzx279133_idm_rx_sync_for_device(eth, 0);\n",
     "the device-side synchronisation has exactly one place"),
]


def failed_checks(tree):
    """The checks the gate reported as failed."""
    result = subprocess.run([sys.executable, str(GATE), "--tree", str(tree)],
                            capture_output=True, text=True)
    names = set()
    for line in (result.stdout + result.stderr).splitlines():
        match = re.match(r"^\s*\[FAIL\]\s+(.+?)(?:\s+-\s+.*)?$", line)
        if match:
            names.add(match.group(1))
    return names, result.returncode


def copy_tree(tree, work):
    """A tree with just the files the gate reads."""
    for name in FILES:
        source = Path(tree) / ZTE / name
        if not source.is_file():
            raise SystemExit("the tree has no %s" % source)
        target = work / ZTE / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
    return work


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tree", type=Path, required=True)
    args = parser.parse_args()

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

    with tempfile.TemporaryDirectory(prefix="sk-rx-delivery-") as tmp:
        work = Path(tmp)

        # The tree as it is has to pass.
        good = copy_tree(args.tree, work / "good")
        names, rc = failed_checks(good)
        check(rc == 0, "the tree as it is passes the gate",
              ", ".join(sorted(names)[:3]))

        for name, target, old, new, expected in MUTATIONS:
            broken = copy_tree(args.tree,
                               work / ("bad-" + name[:12].replace(" ", "-")))
            path = broken / ZTE / target
            text = path.read_text(encoding="utf-8")
            if old not in text:
                check(False, name + " - the text to mutate is gone")
                continue
            path.write_text(text.replace(old, new, 1), encoding="utf-8")
            names, rc = failed_checks(broken)
            if expected in names:
                check(True, name + " -> red on '%s'" % expected)
            else:
                check(False, name + " -> the check '%s' did not go red" % expected,
                      ", ".join(sorted(names)[:3]))

    print("%d checks, %d failed" % (checks, len(failures)))
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
