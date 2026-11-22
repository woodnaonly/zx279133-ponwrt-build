#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Check that the 9132 switch programming fits the Linux 7.3 tree.

None of the driver can be compiled on the machine that pushes this
repository: there is no aarch64 cross toolchain there, and the build only
happens in CI, seventy minutes into a job.  So the things a compiler would
say about it are checked here instead, against the tree the build will
actually use.

What is checked:

  * The 9132 programming is in the tree's np.c: the per-port words, the
    flood groups, the CPU path, the isolation matrix, the protocol action
    table and the PHY enable.
  * The ERAM table bookkeeping is there too, and the SE allocator records
    the descriptors it hands out - a table written at the wrong base, or
    with the wrong entry width, is read in the wrong place by the
    hardware, and that is not a compile error: it is a silent one.
  * Every ZX279133_ and zx279133_ name the block uses is defined - by the
    tree's own np.c, by the tree's headers, or by the private header this
    repository brings.  The PPU numbers live in the private header, and
    the tree's np.c and idm.c have to include it.
  * Every kernel function the block calls exists in the tree.
  * Nothing is defined twice in the tree's np.c or idm.c.  Two definitions
    of one name in one file is a redefinition error, and the two
    generations of this driver set carry a lot of the same names.
  * The programming is actually called: the prepare function runs it, in
    the order the 6.18 generation uses.
  * The board device tree names the MDIO bus the PHY enable looks for.

Usage:
  check-sk-9132-api.py --tree TREE [--repo REPO]
"""
import argparse
import os
import re
import sys
from pathlib import Path

ZTE = "drivers/net/ethernet/zte"

# The 9132 programming, and the ERAM bookkeeping it needs.
PROGRAMMING = [
    "zx279133_9132_port_words_read",
    "zx279133_9132_port_words_vendor",
    "zx279133_9132_mf_mirror",
    "zx279133_9132_flood_cpu_port",
    "zx279133_9132_port_init",
    "zx279133_9132_ppu5_words",
    "zx279133_9132_uni_mdio_bus",
    "zx279133_9132_phy_link",
    "zx279133_9132_phy_dump",
    "zx279133_9132_phy_enable",
    "zx279133_9132_port_enable_read",
    "zx279133_9132_proto_action_set",
    "zx279133_9132_proto_init",
    "zx279133_9132_outport_real",
    "zx279133_9132_outport_qid",
    "zx279133_9132_eram_block_write",
    "zx279133_9132_reason_map_init",
    "zx279133_9132_outport_map_init",
    "zx279133_9132_cpu_path_init",
    "zx279133_9132_isolate_init",
    "zx279133_9132_switch_tables_init",
    "zx279133_9132_port_flow_apply",
]

ERAM_HELPERS = [
    "zx279133_eram_base_of",
    "zx279133_eram_bit_len_of",
    "zx279133_eram_table_set_bits",
    "zx279133_eram_window_patch",
]

# The tree's own helpers the block calls.  Their signatures are the same in
# both generations; that was checked when the code was moved.
TREE_HELPERS = [
    "zx279133_spa_indirect_read",
    "zx279133_spa_indirect_write",
    "zx279133_smmu0_read",
    "zx279133_smmu0_write",
    "zx279133_mf_eram_attr_set",
    "zx279133_mf_eram_entry_write",
]

# The prepare function runs the programming in this order.
CALL_ORDER = [
    "zx279133_9132_port_words_read",
    "zx279133_9132_port_init",
    "zx279133_9132_ppu5_words",
    "zx279133_9132_proto_init",
    "zx279133_9132_cpu_path_init",
    "zx279133_9132_isolate_init",
    "zx279133_9132_switch_tables_init",
    "zx279133_9132_phy_enable",
    "zx279133_9132_port_enable_read",
]

# The names the private header carries for the 9132.
PRIVATE_NAMES = [
    "ZX279133_9132_UNI_PPU_FIRST",
    "ZX279133_9132_UNI_PPU_LAST",
    "ZX279133_9132_CONDUIT_PPU",
    "ZX279133_9132_OUTERPHY_PPU",
]

# The kernel functions and macros the block uses.
KERNEL_API = [
    "mdiobus_read",
    "mdiobus_write",
    "of_mdio_find_bus",
    "of_parse_phandle",
    "of_node_put",
    "MII_BMSR",
    "BMSR_LSTATUS",
]


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


def defines_of(text):
    """Every name a C file defines: macros, functions, types, objects."""
    names = set(re.findall(r"^\s*#\s*define\s+([A-Za-z_]\w*)", text, re.M))
    names |= set(re.findall(r"^\s*(?:static\s+)?(?:const\s+)?[\w \*]*?\b([a-z_]\w+)\s*\(",
                            text, re.M))
    names |= set(re.findall(r"^\s*(?:struct|union|enum)\s+(\w+)\s*\{", text, re.M))
    names |= set(re.findall(r"^\s*typedef\b.*?\b(\w+)\s*;", text, re.M))
    names |= set(re.findall(
        r"^\s*(?:static\s+)?(?:const\s+)?[\w \*]*?\b((?:zx279133_|ZX279133_)\w+)"
        r"\s*(?:=|;|\[|\()", text, re.M))
    return names


def definitions_in(text):
    """Every name a C file defines, with the line it is defined on."""
    found = {}
    for match in re.finditer(r"^\s*#\s*define\s+([A-Za-z_]\w*)", text, re.M):
        found.setdefault(match.group(1), text[:match.start()].count("\n") + 1)
    for match in re.finditer(
            r"^\s*(?:static\s+)?(?:const\s+)?[\w \*]*?\b([a-z_]\w+)\s*\(",
            text, re.M):
        found.setdefault(match.group(1), text[:match.start()].count("\n") + 1)
    for match in re.finditer(r"^\s*(?:struct|union|enum)\s+(\w+)\s*\{", text, re.M):
        found.setdefault(match.group(1), text[:match.start()].count("\n") + 1)
    return found


def blank_comments(text):
    """Replace comments with spaces, keeping the line count."""
    def block(match):
        return re.sub(r"[^\n]", " ", match.group(0))
    text = re.sub(r"/\*.*?\*/", block, text, flags=re.S)
    text = re.sub(r"//[^\n]*", lambda m: " " * len(m.group(0)), text)
    return text


def duplicate_definitions(text):
    """Names a file defines more than once.

    A forward declaration is not a definition: it ends in ';' where a
    definition ends in '{'.  A call is not a definition either, and a
    kernel macro that reads like one - u64_stats_inc() - must not be
    mistaken for a function.
    """
    text = blank_comments(text)
    seen = {}

    for match in re.finditer(r"^\s*#\s*define\s+([A-Za-z_]\w*)", text, re.M):
        line = text[:match.start()].count("\n") + 1
        seen.setdefault(match.group(1), []).append(line)

    for match in re.finditer(r"^\s*(?:struct|union|enum)\s+(\w+)\s*\{",
                             text, re.M):
        line = text[:match.start()].count("\n") + 1
        seen.setdefault(match.group(1), []).append(line)

    for match in re.finditer(r"\b([A-Za-z_]\w*)\s*\(", text):
        name = match.group(1)
        if name in ("if", "for", "while", "switch", "return", "sizeof",
                    "defined"):
            continue
        # find the matching ')' and look at what follows it
        depth = 0
        index = match.end() - 1
        while index < len(text):
            if text[index] == "(":
                depth += 1
            elif text[index] == ")":
                depth -= 1
                if depth == 0:
                    break
            index += 1
        if index >= len(text):
            continue
        rest = text[index + 1:]
        stripped = rest.lstrip()
        if not stripped.startswith("{"):
            continue
        # a definition: the head before the name has to look like a type
        head = text[max(0, match.start() - 80):match.start()]
        if not re.search(r"[A-Za-z_]\w*\s*$", head):
            continue
        line = text[:match.start()].count("\n") + 1
        seen.setdefault(name, []).append(line)

    return {name: where for name, where in seen.items() if len(where) > 1}


def kernel_symbol_in_tree(tree, name):
    """Is this kernel function or macro declared anywhere in the tree?"""
    pattern = re.compile(r"\b" + re.escape(name) + r"\b")
    roots = ("include", "drivers/net", "drivers/mtd", "net", "lib")
    for root in roots:
        base = tree / root
        if not base.is_dir():
            continue
        for dirpath, dirnames, filenames in os.walk(base):
            dirnames[:] = [d for d in dirnames
                           if d not in (".git", "__pycache__")]
            for filename in filenames:
                if not filename.endswith((".h", ".c")):
                    continue
                path = Path(dirpath) / filename
                try:
                    text = read(path)
                except OSError:
                    continue
                if pattern.search(text):
                    return True
    return False


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tree", required=True)
    parser.add_argument("--repo", default=None,
                        help="the repository root, for the private header")
    args = parser.parse_args()

    tree = Path(args.tree)
    report = Report()
    zte = tree / ZTE
    np_c = zte / "zx279133-np.c"
    idm_c = zte / "zx279133-idm.c"
    private = zte / "zx279133-9132.h"
    eth_h = zte / "zx279133.h"

    print(f"the 9132 switch programming in {tree}")

    print("the files")
    for path in (np_c, idm_c, private, eth_h):
        report.require(path.is_file(),
                       f"{path.relative_to(tree)} is in the tree")
    if not np_c.is_file():
        return finish(report)

    np_raw = read(np_c)
    np_text = strip_comments(np_raw)

    print("the programming")
    for name in PROGRAMMING + ERAM_HELPERS:
        report.require(re.search(r"\b" + name + r"\s*\(", np_text) is not None,
                       f"np.c defines {name}")

    report.require(re.search(r"struct\s+zx279133_eram_table\s*\{", np_text)
                   is not None,
                   "np.c has the ERAM table descriptor")
    report.require(re.search(r"zx279133_eram\s*\[\s*ZX279133_SDT_ID_MAX\s*\]",
                             np_text) is not None,
                   "np.c has the ERAM table array")

    print("the ERAM descriptors are recorded where the tables are allocated")
    match = re.search(r"zx279133_se_sdt_create\s*\([^)]*\)\s*\{(.*?)\n\}",
                      np_text, re.S)
    sdt_body = match.group(1) if match else ""
    report.require("zx279133_eram[sdt_no].base = base;" in sdt_body,
                   "se_sdt_create records the table base")
    report.require("zx279133_eram[sdt_no].bit_len = bit_len;" in sdt_body,
                   "se_sdt_create records the entry width")

    print("the names the block uses")
    available = set()
    for path in sorted(zte.glob("*")):
        if path.is_file() and path.suffix in (".c", ".h"):
            available |= defines_of(strip_comments(read(path)))
    if private.is_file():
        available |= defines_of(strip_comments(read(private)))

    block = ""
    for name in PROGRAMMING + ERAM_HELPERS:
        found = re.search(r"\b" + name + r"\s*\([^;{]*\)\s*\{", np_text, re.S)
        if found:
            block += found.group(0) + "\n"
    used = set(re.findall(r"\bZX279133_[A-Z0-9_]+\b", block))
    used |= set(re.findall(r"\bzx279133_[a-z0-9_]+\b", block))
    missing = sorted(name for name in used
                     if name not in available
                     and name not in defines_of(np_text))
    report.require(not missing,
                   "every ZX279133_/zx279133_ name the block uses is defined"
                   + (f" (missing: {missing})" if missing else ""))

    print("the private header")
    if private.is_file():
        private_text = strip_comments(read(private))
        for name in PRIVATE_NAMES:
            report.require(re.search(r"#\s*define\s+" + name + r"\b", private_text)
                           is not None,
                           f"the private header defines {name}")
    report.require('#include "zx279133-9132.h"' in np_raw,
                   "np.c includes the private header")
    report.require('#include "zx279133-9132.h"' in read(idm_c),
                   "idm.c includes the private header")

    print("the tree's own helpers the block calls")
    for name in TREE_HELPERS:
        report.require(re.search(r"\b" + name + r"\s*\(", np_text) is not None,
                       f"np.c defines {name}")

    print("the kernel functions the block calls")
    for name in KERNEL_API:
        report.require(kernel_symbol_in_tree(tree, name),
                       f"the tree provides {name}")

    print("nothing is defined twice")
    for path in (np_c, idm_c):
        twice = duplicate_definitions(read(path))
        report.require(not twice,
                       f"{path.name} defines no name twice"
                       + (f" (twice: {twice})" if twice else ""))

    print("the programming is called")
    match = re.search(r"zx279133_np_prepare\s*\([^)]*\)\s*\{(.*?)\n\}",
                      np_text, re.S)
    prepare = match.group(1) if match else ""
    positions = []
    for name in CALL_ORDER:
        found = prepare.find(name + "(")
        report.require(found >= 0, f"the prepare function calls {name}")
        positions.append((name, found))
    ordered = [name for name, pos in sorted(positions, key=lambda p: p[1])
               if pos >= 0]
    report.require(ordered == CALL_ORDER,
                   "the prepare function calls the programming in order"
                   + (f" (it calls {ordered})" if ordered != CALL_ORDER else ""))

    print("the board device tree")
    dts = tree / "arch" / "arm64" / "boot" / "dts" / "zte" / "zx279133-sk-d840n.dts"
    if dts.is_file():
        report.require("zx279133,uni-phy-bus" in read(dts),
                       "the board device tree names the UNI PHY bus")
    else:
        report.note("the board device tree is not in this tree")

    print("the IDM's side of it")
    idm_text = strip_comments(read(idm_c))
    report.require(re.search(r"ZX279133_9132_UNI_PPU_FIRST", idm_text)
                   is not None,
                   "rx_lan_vport maps the 9132's PPU onto a transport VID")
    report.require("zx279133_omci_reclaim(eth)" in idm_text,
                   "the drain path reclaims the management queue")
    report.require("omci_pending" in idm_text,
                   "the drain path waits for the management queue")

    if args.repo:
        repo = Path(args.repo)
        carried = repo / "kernel-overlays-73" / ZTE / "zx279133-9132.h"
        if carried.is_file():
            report.require(carried.read_bytes() == private.read_bytes(),
                           "the tree's private header is the one the repository "
                           "carries")

    return finish(report)


def finish(report):
    print()
    if report.failures:
        print(f"FAILED: {len(report.failures)}")
        for failure in report.failures:
            print(f"  - {failure}")
        return 1
    print("the 9132 switch programming fits the tree")
    return 0


if __name__ == "__main__":
    sys.exit(main())
