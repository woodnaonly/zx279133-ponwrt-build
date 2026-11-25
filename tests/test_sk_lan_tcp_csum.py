#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Prove that the transmit-checksum gate bites.

Every mutation below breaks one property of the 7.3 tree, and the gate has
to name that property when it runs on the mutated tree.  A gate that stays
green under a mutation it was meant to catch is not a gate.

The tree is copied before each mutation, so the original is never touched.
"""
import argparse
import pathlib
import shutil
import subprocess
import sys
import tempfile

NL = chr(10)
TAB = chr(9)
HERE = pathlib.Path(__file__).resolve().parent
CHECK = HERE.parent / "tools" / "check-sk-lan-tcp-csum.py"
ZTE = "drivers/net/ethernet/zte"

FLAG = "#define zx279133_tx_hw_csum" + TAB + TAB + "0"
FLAG_ON = "#define zx279133_tx_hw_csum" + TAB + TAB + "1"

REASON = """/*
 * The transmit checksum is only half there.
 *
 * This SoC's own data path can add up the pseudo header, but nothing writes
 * the finished TCP checksum into the header of the frame that leaves for a
 * client.  Asking for NETIF_F_HW_CSUM on that basis makes the stack hand
 * the checksum over instead of computing it, and the frame goes out with a
 * placeholder in the TCP header.
 *
 * Measured on the board: every TCP frame the board sent carried a checksum
 * that does not verify - the same value in every retransmission, while the
 * timestamp option inside those same frames changed each time, which is
 * what a placeholder looks like.  The peer dropped those frames without a
 * word: its SYN-ACK came back over the wire and the connection never
 * completed, while ICMP over the very same path was perfect.  The board's
 * own counters agreed - tx_hw_csum_packets 127, tx_sw_csum_packets 0.
 *
 * So the flag goes: with it off the stack computes the checksum in
 * software, the frames carry a correct one, and TCP works on the jack.  The
 * price is the checksum arithmetic in the CPU, which is what the hardware
 * was supposed to save.
 */
"""

MUTATIONS = [
    (
        "the transmit checksum is not delegated",
        "zx279133.h",
        lambda text: text.replace(FLAG, FLAG_ON),
    ),
    (
        "the feature is not declared unconditionally",
        "zx279133-main.c",
        lambda text: text.replace("if (zx279133_tx_hw_csum) {", "if (1) {"),
    ),
    (
        "the software path is still there",
        "zx279133-netdev.c",
        lambda text: text.replace("if (skb_checksum_help(skb))", "if (0)"),
    ),
    (
        "the decision still reads the flag",
        "zx279133-netdev.c",
        lambda text: text.replace(
            "hw_csum = zx279133_tx_hw_csum &&", "hw_csum = true &&"),
    ),
    (
        "no other file in the driver declares the feature",
        "zx279133-np.c",
        lambda text: text + NL + "/* the gate has to notice this */" + NL +
        "static void zx279133_gate_probe(struct net_device *ndev)" + NL +
        "{" + NL + "\tndev->features |= NETIF_F_HW_CSUM;" + NL + "}" + NL,
    ),
    (
        "the header says why",
        "zx279133.h",
        lambda text: text.replace(REASON, ""),
    ),
    (
        "a partial checksum is still handled",
        "zx279133-netdev.c",
        lambda text: text.replace(
            "skb->ip_summed == CHECKSUM_PARTIAL",
            "skb->ip_summed == CHECKSUM_NONE"),
    ),
]


def copy_tree(tree, dest):
    shutil.copytree(pathlib.Path(tree) / ZTE, pathlib.Path(dest) / ZTE)


def run_gate(tree):
    proc = subprocess.run(
        [sys.executable, str(CHECK), "--tree", str(tree)],
        capture_output=True,
        text=True,
    )
    failed = set()
    for line in proc.stdout.splitlines():
        if line.startswith("FAIL "):
            failed.add(line[5:].split(":")[0].strip())
    return failed, proc.stdout


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tree", required=True, help="the prepared 7.3 tree")
    args = parser.parse_args(argv)

    bad = 0

    failed, out = run_gate(args.tree)
    if failed:
        print("the untouched tree must pass the gate, but these failed: "
              + ", ".join(sorted(failed)))
        print(out)
        bad += 1
    else:
        print("ok   the untouched tree passes every check")

    with tempfile.TemporaryDirectory() as tmp:
        for name, filename, mutate in MUTATIONS:
            tree = pathlib.Path(tmp) / name.replace(" ", "_")
            tree.mkdir(parents=True)
            copy_tree(args.tree, tree)
            target = tree / ZTE / filename
            text = target.read_text(encoding="utf-8")
            changed = mutate(text)
            if changed == text:
                print("FAIL the mutation for '%s' did not change %s - "
                      "the tree does not look the way the test expects"
                      % (name, filename))
                bad += 1
                continue
            target.write_bytes(changed.encode("utf-8"))
            failed, out = run_gate(tree)
            if name in failed:
                print("ok   the gate names '%s' when it is broken" % name)
            else:
                print("FAIL the gate stayed quiet when '%s' was broken" % name)
                print(out)
                bad += 1

    if bad:
        print("test_sk_lan_tcp_csum: %d problem(s)" % bad)
        return 1
    print("test_sk_lan_tcp_csum: every mutation was caught")
    return 0


if __name__ == "__main__":
    sys.exit(main())
