#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""The switch engine's table set, and the bases that go with it.

The switch engine's microcode knows a fixed set of tables: for each one an
id, the width of an entry and the number of entries.  The board loads
np_133.ko, and that module's g_core_sdt_info - 53 entries - is that set.
The generic np.ko's set belongs to a module that is not on this board.

The 7.3 tree declared the generic set, and declared it with four table
bases written down as constants (17106, 21266, 23026 and 12288) that
belong to that set.  The MF extension table - SDT 0x7d, the mirror of the
SPA port attributes - is not in the generic set at all, so the switch
engine was never told about it, the driver had no base for it, and
zx279133_9132_port_init() failed with -EINVAL: the switch driver's probe
failed and the board came up with no network interface.

This gate says three things about the tree's np.c:

  * it declares the vendor's table set, item for item and in the vendor's
    order - the order is the allocation order, so a table that moves moves
    every table after it;
  * every table the driver writes exists, is an ERAM table and has the
    base the vendor's own module gives it; and
  * no table base is written down as a constant any more - each one is
    looked up by the table's id, out of the allocation the driver told
    the hardware about.

The reference it compares against is this repository's 6.18 generation's
np.c: the driver that boots on this board, with the vendor's list and the
same allocator.  Comparing the two generations against each other is the
point - a gate that carried its own copy of the table set would prove
nothing but that the copy did not change.
"""
import argparse
import re
import sys
from pathlib import Path

ZTE = "drivers/net/ethernet/zte"
NP = "zx279133-np.c"
REFERENCE = "kernel-overlays/drivers/net/ethernet/zte/zx279133-np.c"

# The tables this driver writes, and what each one is for.  The ids are the
# names the code uses; the bases are what the vendor's allocation gives
# them, and the gate computes those rather than carrying them here.
TABLES = {
    0x7d: "the MF extension table (the SPA port attributes)",
    0x00: "the VLAN action table",
    0x04: "the NPPT's own 16-bit protocol table",
    0x06: "the port type table",
    0x18: "the switch engine's protocol action table",
    0x19: "the reason to CPU queue map",
    0x22: "the global configuration table",
    0x27: "the outport map",
    0x1f: "the flood group table",
    0x1b: "the port isolation matrix",
    0x23: "the output VLAN check",
    0x24: "the port bind table",
    0x25: "the port PVID table",
}

# The width of an entry, chosen by p3.
BIT_LEN = {0: 1, 1: 32, 2: 64, 3: 128, 4: 2, 5: 4, 6: 8, 7: 16}

# The bases the vendor's own allocation gives these tables, as the board
# measured them (docs/31): the ERAM dump of a board that ran the vendor's
# module, block by block.  They are the anchor - a gate that only compared
# the tree with the other tree would follow it wherever it went.
PUBLISHED_BASES = {
    0x18: 19624, 0x19: 19752, 0x22: 19592, 0x1b: 19820, 0x23: 19860,
    0x24: 19861, 0x25: 19903, 0x27: 24071, 0x1f: 19975, 0x7d: 24737,
    0x00: 25089, 0x04: 29185, 0x06: 30209,
}

# The block the pool ends at: the vendor's 53 tables and this driver's own
# three take 17985 blocks of the 24576 the ERAM has (docs/31).
PUBLISHED_POOL_END = 30273

# The pool the ERAM tables are handed out of.  0x3000 is not a choice:
# se_smmu0_skip_ram_for_qmg() makes the vendor's own cursor jump over the
# queue manager's block, and the board says so - below 0x3000 a write does
# not land at all.
POOL_START = 0x3000

LIST_RE = re.compile(
    r"static const u32 zx279133_core_sdt_info\[\]\[4\] = \{(.*?)\n\};", re.S)
ENTRY_RE = re.compile(
    r"\{\s*(0x[0-9a-fA-F]+)\s*,\s*(\d+)\s*,\s*([^,]+?)\s*,\s*([^}]+?)\s*\}")


class Report:
    def __init__(self):
        self.failures = []
        self.notes = []

    def require(self, ok, message):
        print(f"  [{'OK ' if ok else 'FAIL'}] {message}")
        if not ok:
            self.failures.append(message)
        return ok

    def note(self, message):
        print(f"  [note] {message}")
        self.notes.append(message)


def read(path):
    with open(path, "r", encoding="utf-8", errors="replace") as handle:
        return handle.read()


def strip_comments(text):
    text = re.sub(r"/\*.*?\*/", " ", text, flags=re.S)
    text = re.sub(r"//[^\n]*", " ", text)
    return text


def parse_table_set(text):
    """The table set, as the list the driver declares it."""
    match = LIST_RE.search(text)
    if not match:
        return None
    body = strip_comments(match.group(1))
    entries = []
    for entry in ENTRY_RE.finditer(body):
        entries.append({
            "id": int(entry.group(1), 16),
            "type": int(entry.group(2)),
            "p3": entry.group(3).strip(),
            "num": entry.group(4).strip(),
        })
    return entries


def number(text):
    try:
        return int(text.rstrip("uU"), 0)
    except ValueError:
        return None


def allocate(entries):
    """The vendor's own allocator: out of one pool, strictly in list order."""
    eram = POOL_START
    stat = 9 << 12
    tables = {}
    for entry in entries:
        sid = entry["id"]
        p3 = number(entry["p3"])
        num = number(entry["num"])
        if p3 is None or num is None:
            tables[sid] = dict(entry, base=None, depth=None, bit_len=None)
            continue
        if entry["type"] in (1, 5):
            bit = BIT_LEN[p3 & 7]
            depth = (bit * num + 127) >> 7
            tables[sid] = dict(entry, base=eram, depth=depth, bit_len=bit)
            eram += depth
        elif entry["type"] == 4:
            mode = (num >> 24) >> 5
            bit = BIT_LEN[mode]
            depth = (bit * p3 + 127) >> 7
            tables[sid] = dict(entry, base=stat, depth=depth, bit_len=bit)
            stat += depth
        else:
            tables[sid] = dict(entry, base=None, depth=None, bit_len=None)
    return tables, eram, stat


def describe(entry):
    return "0x%02x type %s p3 %s num %s" % (
        entry["id"], entry["type"], entry["p3"], entry["num"])


def expression_before(text, pos):
    """The expression that ends at pos: '(a + b)' for '(a + b) << 7'."""
    depth = 0
    index = pos - 1
    while index >= 0:
        char = text[index]
        if char == ')':
            depth += 1
        elif char == '(':
            depth -= 1
            if depth == 0:
                return text[index:pos]
        elif char == ';' and depth == 0:
            return text[index + 1:pos]
        index -= 1
    return text[:pos]


def compare_lists(report, tree, reference):
    """Item for item and in order - the order is the allocation order."""
    if len(tree) != len(reference):
        report.require(False,
                       "the table set has %d entries, the vendor's has %d"
                       % (len(tree), len(reference)))
        return False
    for index, (got, want) in enumerate(zip(tree, reference)):
        if got == want:
            continue
        report.require(False,
                       "table set entry %d differs: the tree has %s, the "
                       "vendor's has %s" % (index, describe(got),
                                            describe(want)))
        return False
    report.require(True, "the table set is the vendor's, item for item "
                         "and in the vendor's order")
    return True


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tree", required=True,
                        help="the kernel tree whose np.c is checked")
    parser.add_argument("--repo", default=None,
                        help="the repository root, for the 6.18 reference")
    args = parser.parse_args()

    tree = Path(args.tree)
    report = Report()
    np_c = tree / ZTE / NP

    print(f"the 9132's table set in {tree}")
    if not report.require(np_c.is_file(), f"{ZTE}/{NP} is in the tree"):
        return finish(report)

    np_raw = read(np_c)
    np_text = strip_comments(np_raw)
    tree_entries = parse_table_set(np_raw)
    if not report.require(tree_entries is not None and tree_entries,
                          "np.c declares a table set"):
        return finish(report)

    reference = None
    if args.repo:
        ref_path = Path(args.repo) / REFERENCE
        if ref_path.is_file():
            reference = parse_table_set(read(ref_path))
        report.require(reference is not None and reference,
                       "the repository carries the 6.18 generation's reference")
    if not reference:
        report.note("no reference: the table set is only checked for shape")
        return finish(report)

    print("the table set")
    compare_lists(report, tree_entries, reference)

    tree_tables, tree_end, _ = allocate(tree_entries)
    ref_tables, ref_end, _ = allocate(reference)

    print("the pool")
    report.require(tree_end == ref_end,
                   "the ERAM pool ends at block %d, the vendor's at %d"
                   % (tree_end, ref_end))
    report.require(tree_end == PUBLISHED_POOL_END,
                   "the ERAM pool ends at block %d, the board measured %d"
                   % (tree_end, PUBLISHED_POOL_END))

    print("the tables this driver writes")
    for sid in sorted(TABLES):
        what = TABLES[sid]
        got = tree_tables.get(sid)
        want = ref_tables.get(sid)
        if not report.require(got is not None,
                              "the table set has 0x%02x (%s)" % (sid, what)):
            continue
        if got["type"] != 1:
            report.require(False,
                           "table 0x%02x (%s) is type %d, an ERAM table is 1"
                           % (sid, what, got["type"]))
            continue
        report.require(got["base"] is not None and got["base"] != 0,
                       "table 0x%02x (%s) has base %s"
                       % (sid, what,
                          "none" if got["base"] is None else got["base"]))
        if want is not None and want.get("base") is not None:
            report.require(got["base"] == want["base"]
                           and got["bit_len"] == want["bit_len"]
                           and got["depth"] == want["depth"],
                           "table 0x%02x (%s) is at %s with %s bits per "
                           "entry, the vendor's allocation says %s with %s"
                           % (sid, what, got["base"], got["bit_len"],
                              want["base"], want["bit_len"]))
        published = PUBLISHED_BASES[sid]
        report.require(got["base"] == published,
                       "table 0x%02x (%s) is at %s, the board measured %s"
                       % (sid, what, got["base"], published))

    print("no table base is written down as a constant")
    constants = re.findall(r"#\s*define\s+(ZX279133_\w*ERAM_BASE_BLOCK)\b",
                            np_text)
    report.require(not constants,
                   "np.c defines no ERAM base constant"
                   + (f" (it defines {sorted(set(constants))})" if constants
                      else ""))
    for sid, name in ((0x7d, "ZX279133_MF_ERAM_SDT_ID"),
                      (0x00, "ZX279133_VLAN_ERAM_SDT_ID"),
                      (0x04, "ZX279133_PRO_ERAM_SDT_ID"),
                      (0x06, "ZX279133_PORT_TYPE_ERAM_SDT_ID")):
        report.require("zx279133_eram_base_of(%s)" % name in np_text,
                       "the base of table 0x%02x is looked up by its id" % sid)

    print("the SE allocator's own writes")
    body = re.search(r"zx279133_se_sdt_create\s*\([^)]*\)\s*\{(.*?)\n\}",
                     np_text, re.S)
    sdt_body = body.group(1) if body else ""
    literals = []
    for match in re.finditer(r"<<\s*7", sdt_body):
        head = expression_before(sdt_body, match.start()).strip()
        found = re.match(r"^\(?\s*(\d+)", head)
        if found:
            literals.append(found.group(1))
    report.require(not literals,
                   "the SE allocator writes no table at a literal block"
                   + (f" (it writes {sorted(set(literals))})" if literals
                      else ""))

    print("the port attribute word")
    report.require("data[0] = attr;" in np_text,
                   "the MF extension entry's port attribute word is word 0")
    report.require("data[3] = attr;" not in np_text,
                   "the MF extension entry's port attribute word is not word 3")

    return finish(report)


def finish(report):
    print()
    if report.failures:
        print(f"FAILED: {len(report.failures)}")
        for failure in report.failures:
            print(f"  - {failure}")
        return 1
    print("the switch engine is given the vendor's table set")
    return 0


if __name__ == "__main__":
    sys.exit(main())
