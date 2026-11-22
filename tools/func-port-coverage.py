#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Audit the 7.3 port by what the 7.3 tree actually has.

tools/func-surface-diff.py answers "which function names exist in only
one of the two trees".  That is the first question of the port, and it
is also a trap: the fifth slice moved the 9132 programming into the 7.3
tree, and the functions it moved are there - but the 6.18 generation's
names for the vendor compatibility work, the BMU thresholds, the RED
gate and the datapath model do not exist in the 7.3 tree at all, because
the 7.3 tree does that work its own way.  A name list therefore reports
a port that happened as missing, and reports work that was never needed
as missing too.

So this asks the question the name list cannot answer: for every
function only the 6.18 generation has, is it accounted for - by the
same name in the 7.3 tree, by a ledger entry that says where the work
ended up under another name, or by a disposition on record that says
why it is not coming?

The dispositions are not a list of excuses.  Each one has to say what
the 7.3 tree does instead, and every function has to end up in exactly
one of three buckets: carried over, carried over under another name, or
disposed of on record.  A function in none of the three is a gap, and
the tool says so and exits non-zero.

A body that looks like something in the 7.3 tree is not a bucket.  It
was one, and it was removed: two short functions that happen to share
most of their lines passed the check while the ledger had a hole in
it.  The nearest match is still printed next to a gap, because it is
the first place to look - but only the ledger decides.

  python3 tools/func-port-coverage.py <ours-dir> <theirs-dir>
  python3 tools/func-port-coverage.py <ours-dir> <theirs-dir> --json
"""
import argparse
import json
import os
import re
import sys
from collections import defaultdict

# The same shape func-surface-diff.py recognises: a definition at column
# zero, and a declaration or an assignment is not a definition.
FUNC_RE = re.compile(
    r"^(?:static\s+|inline\s+|extern\s+)*"
    r"(?:const\s+)?(?:struct\s+\w+\s*\*|void\s*\*|char\s*\*|u8\s*\*|u16\s*\*|u32\s*\*|u64\s*\*|"
    r"int|bool|long|unsigned|signed|size_t|ssize_t|void|u8|u16|u32|u64|s8|s16|s32|s64|"
    r"__be16|__be32|__le16|__le32|sk_buff|netdev|struct \w+|enum \w+)\s*\*?\s*"
    r"(?P<name>[A-Za-z_][A-Za-z0-9_]*)\s*\("
)

# The functions that came across under another name or in another shape.
# The 7.3 tree does not have the 6.18 generation's names for the vendor
# compatibility work, the BMU thresholds, the RED gate or the datapath
# model, because it does that work its own way.  Each entry says where
# the work ended up, so the entry can be checked.
CARRIED = {
    "zx279133_bmu_threshold_fixup":
        "zx279133_bmu_prepare() and zx279133_bmu_restore() in zx279133-tm.c",
    "zx279133_red_gate_clear":
        "zx279133_red_prepare() and zx279133_red_restore() in zx279133-tm.c",
    "zx279133_red_gate_restore":
        "zx279133_red_prepare() and zx279133_red_restore() in zx279133-tm.c",
    "zx279133_red_vendor_fixup":
        "zx279133_red_np1_prepare() and zx279133_red_np1_restore() in "
        "zx279133-tm.c",
    "zx279133_red_ind_read":
        "zx279133_red_ind_write() and zx279133_native_red_buffer_get() in "
        "zx279133-tm.c and zx279133-native.c",
    "zx279133_red_ram_read":
        "zx279133_native_red_buffer_get() and zx279133_native_red_usage_get() "
        "in zx279133-native.c",
    "zx279133_red_ram_write":
        "zx279133_native_red_buffer_set() in zx279133-native.c",
    "zx279133_tm_vendor_fixup":
        "zx279133_tm_prepare() and zx279133_tm_restore() in zx279133-tm.c",
    "zx279133_nppt_vendor_reconfigure":
        "zx279133_nppu_early_prepare() in zx279133-np.c and the "
        "zx279133_native_*_io() endpoints in zx279133-native.c",
    "zx279133_diag_dump":
        "the zx279133_diag_*() diagnostics in zx279133-offload-debug.h and "
        "zx279133_switch_lane_snapshot() in zx279133-switch.c",
    "zx279133_sopc_duplex_mask":
        "zx279133_sopc_prepare() and zx279133_sopc_restore() in "
        "zx279133-tm.c",
    "zx279133_sopc_ready":
        "zx279133_sopc_prepare() and zx279133_sopc_restore() in "
        "zx279133-tm.c",
    "zx279133_sopc_send_enable":
        "zx279133_sopc_prepare() in zx279133-tm.c and the direct write to "
        "ZX279133_XMAC1_SOPC_SEND_ENABLE in zx279133-mac.c",
    "zx279133_xmac_base":
        "zx279133_xmac_reset() in zx279133-mac.c",
    "zx279133_xmac_reset_mask":
        "zx279133_xmac_reset() in zx279133-mac.c",
    "zx279133_xpcs_dev":
        "zx279133_xpcs_2500basex_quirk() in zx279133-mac.c and the lane "
        "programming in zx279133-switch.c",
    "zx279133_debugfs_remove":
        "zx279133_wanid_debugfs_remove() in zx279133-np.c",
    "zx279133_lan_mmio_set":
        "zx279133_lan_nppt_write() and zx279133_lan_nppt_read() in "
        "zx279133-netdev.c, the LAN service's own way of writing NPPT",
    "zx279133_lan_strip_transport_vlan":
        "the PPU to transport VID mapping in zx279133_idm_rx_lan_vport(), "
        "zx279133-idm.c, added by the 150 patch",
    "zx279133_wan_phylink_start":
        "the phylink_start() call in zx279133-netdev.c",
    "zx279133_wan_phylink_stop":
        "the phylink_stop() call in zx279133-netdev.c",
    "zx279133_ppu_get_me_val":
        "zx279133_ppu_me_stats_read() in zx279133-np.c and "
        "zx279133-native.h",
    "zx279133_ppu_me_word":
        "zx279133_ppu_me_diagnostics_read() in zx279133-np.c and "
        "zx279133-native.h",
    "zx279133_soc_block_ok":
        "the block check inside the zx279133_native_*_io() endpoints in "
        "zx279133-native.c",
    "zx279133_9132_off_aligned":
        "the kernel's own get_unaligned_be16()/be32() helpers, which "
        "zx279133-np.c uses for the same registers",
    "zx279133_idm_bp_ring_cfg":
        "the pool size and free ring registers written by "
        "zx279133_idm_tx_configure(), which the 100 patch set to the "
        "measured values",
    "zx279133_idm_rx_find_header":
        "the fixed frame offset, 0x80, which the 110 patch introduced: "
        "the 7.3 tree does not have to look for the header",
    "zx279133_idm_rx_has_ethertype":
        "the eth_type_trans() call in zx279133-idm.c",
    "zx279133_idm_rx_is_jack":
        "the PPU to transport VID mapping in zx279133_idm_rx_lan_vport(), "
        "where PPU 5 is the outer PHY",
    "zx279133_idm_xdp_pass_valid":
        "the xdp_buff_has_frags() and length check in the XDP path of "
        "zx279133-netdev.c",
}

# The functions that are not coming, and why.  The key is a prefix, so a
# family is disposed of as a family.  Every disposition has to name what
# the 7.3 tree does instead, because "we did not port it" is not a
# reason - "the 7.3 tree does it this other way" is.
DISPOSITIONS = {
    "zx279133_rgmii_": (
        "the RGMII debugfs probe: the 0x15900000 window reads all zero on "
        "this board and the vendor Linux never brings it up, so the probe "
        "only proves that the block is dead"),
    "np_rgmii_reg_print": (
        "the RGMII debugfs probe: same disposition as the rest of that "
        "file, which is a read-only probe of a dead block"),
    "zx279133_9132_debugfs_init": (
        "the 9132 debugfs console: the 7.3 tree has its own native_*_io "
        "endpoints for the same registers"),
    "zx279133_9132_open": (
        "the 9132 debugfs console: the 7.3 tree has its own native_*_io "
        "endpoints for the same registers"),
    "zx279133_9132_show": (
        "the 9132 debugfs console: the 7.3 tree has its own native_*_io "
        "endpoints for the same registers"),
    "zx279133_9132_next_token": (
        "the 9132 debugfs console: the 7.3 tree has its own native_*_io "
        "endpoints for the same registers"),
    "zx279133_tx_recovery_": (
        "the TX fault isolation and recovery mechanism: it is built on the "
        "6.18 generation's datapath_attach/datapath_failed model, which "
        "the 7.3 tree does not have - the 7.3 tree clamps an illegal "
        "completion instead of isolating it, so porting it means porting "
        "the model with it"),
    "zx279133_tx_available_locked": (
        "the TX fault isolation and recovery mechanism: same disposition as "
        "the rest of tx_recovery_"),
    "zx279133_datapath_attach": (
        "the TX fault isolation and recovery mechanism: same disposition as "
        "the rest of tx_recovery_"),
    "zx279133_datapath_restore_failed_locked": (
        "the TX fault isolation and recovery mechanism: same disposition as "
        "the rest of tx_recovery_"),
    "zx279133_idm_find_broadcast": (
        "a debug print: the function reads the BMU's own map out of the "
        "registers and prints it, so it produces diagnostics and no "
        "behaviour, and the 7.3 tree has its own diagnostics"),
}


def functions_in(path):
    """name -> the line it is defined on, for every definition in a file."""
    names = {}
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            lines = fh.read().split("\n")
    except OSError:
        return names
    for number, line in enumerate(lines, start=1):
        if line[:1] in ("#", " ", "\t"):
            continue
        if "(" not in line or not line.rstrip().endswith((")", "{")):
            continue
        head = line.strip()
        if head.endswith(";"):
            continue
        if "=" in head.split("(")[0]:
            continue
        match = FUNC_RE.match(head)
        if match:
            names.setdefault(match.group("name"), number)
    return names


def collect(directory):
    """name -> file, over every C file and header in the directory."""
    found = {}
    if not os.path.isdir(directory):
        return found
    for name in sorted(os.listdir(directory)):
        if not name.endswith((".c", ".h")):
            continue
        for symbol, _ in functions_in(os.path.join(directory, name)).items():
            found.setdefault(symbol, name)
    return found


def load_body_reader():
    """The repository's own body reader, so the comparison is the same one
    the rest of the tools make: comments and blank lines dropped."""
    import importlib.util
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "func-body-diff.py")
    spec = importlib.util.spec_from_file_location("funcbodydiff", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def bodies(directory, reader):
    """name -> the code of the function, as a set of lines."""
    result = {}
    for name in sorted(os.listdir(directory)):
        if not name.endswith((".c", ".h")):
            continue
        for symbol, (_, body) in reader.bodies(
                os.path.join(directory, name)).items():
            lines = body.splitlines()
            if lines:
                lines[0] = lines[0].replace(symbol, "FUNC")
            result.setdefault(symbol, set(reader.normalize("\n".join(lines))))
    return result


def disposition_for(name):
    for prefix, reason in DISPOSITIONS.items():
        if name.startswith(prefix) or name == prefix:
            return reason
    return None


def named_functions(text):
    """The driver's own function names a sentence points at.

    A wildcard ("zx279133_native_*_io()") names no single function, so
    it is left out: the point of the check is to catch a ledger entry
    that points at a function the 7.3 tree does not have.
    """
    return set(re.findall(r"\b(zx279133_[A-Za-z0-9_]+)\(\)", text))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("ours")
    parser.add_argument("theirs")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    ours = collect(args.ours)
    theirs = collect(args.theirs)
    only_ours = sorted(set(ours) - set(theirs))

    reader = load_body_reader()
    our_bodies = bodies(args.ours, reader)
    their_bodies = bodies(args.theirs, reader)
    their_lines = sorted(their_bodies.items(), key=lambda kv: -len(kv[1]))

    carried = []
    disposed = []
    gaps = []
    for name in only_ours:
        if name in CARRIED:
            carried.append((name, ours[name], CARRIED[name]))
            continue
        reason = disposition_for(name)
        if reason:
            disposed.append((name, ours[name], reason))
            continue
        # No ledger entry and no disposition: that is a gap.
        #
        # A body that looks like something in the 7.3 tree is not
        # enough to close it.  The nearest match is reported, because
        # it is the first place to look, but a coincidence of a few
        # lines - two short functions share most of their lines - is
        # not a port.  Only the ledger says what was ported.
        a = our_bodies.get(name)
        best_ratio = 0.0
        best_name = ""
        for other, b in their_lines:
            if not b or not a:
                continue
            shared = len(a & b)
            ratio = shared / max(1, max(len(a), len(b)))
            if ratio > best_ratio:
                best_ratio = ratio
                best_name = other
        gaps.append((name, ours[name], best_name, best_ratio))

    # The ledger has to be true about the 7.3 tree: every driver's own
    # function it points at has to be there.  A ledger entry that names
    # a function the tree does not have is a claim, not a fact, and the
    # tool says so.
    #
    # The check is made against the body reader's list, not the name
    # list: a definition whose signature runs over two lines is a
    # definition, and the name reader does not see it.
    wrong = []
    for name, where, where_to in carried:
        for pointed in sorted(named_functions(where_to)):
            if pointed not in their_bodies:
                wrong.append((name, pointed))

    report = {
        "ours_functions": len(ours),
        "theirs_functions": len(theirs),
        "same_name": len(set(ours) & set(theirs)),
        "only_ours": len(only_ours),
        "carried_under_another_name": len(carried),
        "disposed_on_record": len(disposed),
        "gaps": len(gaps),
        "ledger_points_at_nothing": len(wrong),
    }

    if args.json:
        print(json.dumps(report, indent=2))
        for name, where, where_to in carried:
            print("carried  %-44s %-22s -> %s" % (name, where, where_to))
        for name, where, reason in disposed:
            print("disposed %-44s %-22s %s" % (name, where, reason))
        for name, where, hit, ratio in gaps:
            print("GAP      %-44s %-22s best match %-32s %3.0f%%"
                  % (name, where, hit, ratio * 100))
        return 1 if gaps else 0

    print("ours:  %s  (%d functions)" % (args.ours, len(ours)))
    print("theirs: %s  (%d functions)" % (args.theirs, len(theirs)))
    print("same name: %d   only in ours: %d" % (len(set(ours) & set(theirs)),
                                                len(only_ours)))
    print()
    print("only in ours, and accounted for:")
    print("  carried over under another name: %d" % len(carried))
    for name, where, where_to in carried:
        print("      %-42s %-22s -> %s" % (name, where, where_to))
    print("  disposed of on record:            %d" % len(disposed))
    for name, where, reason in disposed:
        print("      %-42s %-22s %s" % (name, where, reason))
    print("  NOT accounted for:                %d" % len(gaps))
    for name, where, hit, ratio in gaps:
        print("      %-42s %-22s best match %-30s %3.0f%%"
              % (name, where, hit, ratio * 100))
    print()
    if wrong:
        print("the ledger points at functions the 7.3 tree does not have:")
        for name, pointed in wrong:
            print("      %-42s -> %s" % (name, pointed))
        print()
    if gaps or wrong:
        if gaps:
            print("the functions above are in neither bucket: that is a gap")
        return 1
    print("every function only the 6.18 generation has is accounted for, and "
          "every name the ledger points at is in the 7.3 tree")
    return 0


if __name__ == "__main__":
    sys.exit(main())
