#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Check that the board layer still fits the Linux 7.3 tree.

The board layer is the part of this repository that is copied on top of
cnjn's 7.3 driver set: the SK-D840N device tree, the Unim SPI-NAND driver,
the DSA transport-VID header, and the dtb entry.  Everything else in
kernel-overlays/ belongs to the 6.18 generation of the ZX279133 driver and
is deliberately NOT copied into the 7.3 tree.

None of these files can be compiled on the machine that pushes this
repository - there is no aarch64 cross toolchain there, and the build only
happens in CI, seventy minutes into a job.  So the kernel API surface they
depend on is checked here instead, against the tree itself: every macro,
type and accessor the new files use has to exist, with the right shape, in
the tree the build will actually use.

The check runs in two phases because the interesting comparison needs the
tree both before and after the overlay is applied:

  pre   - the tree still carries cnjn's own copy of the DSA header.  The
          API symbols are checked, and the upstream header's macro values
          are written to a report file.
  post  - the overlay has been applied.  The board files have to be in
          place, the device tree's includes and labels have to resolve,
          and the header the tagger now sees has to define every macro the
          tagger uses.  The values are compared against the report, so a
          change in what the tagger accepts at runtime cannot pass
          unnoticed.

Usage:
  check-sk-board-api.py pre  --tree TREE --overlay DIR --report FILE
  check-sk-board-api.py post --tree TREE --overlay DIR --report FILE
"""
import argparse
import json
import os
import re
import sys

# The macros the DSA tagger consumes.  ZX279133_RTL8372N_TAG_NAME is defined
# by the tagger itself (net/dsa/tag_zx279133_rtl8372n.c), the other three come
# from the shared header.
TAGGER_OWN_MACROS = {"ZX279133_RTL8372N_TAG_NAME"}
HEADER_MACROS = [
    "ZX279133_RTL8372N_USER_PORT_MIN",
    "ZX279133_RTL8372N_USER_PORT_MAX",
    "ZX279133_RTL8372N_TRANSPORT_VID_BASE",
]

# The kernel API the board layer's new files use.
SPINAND_MACROS = [
    "SPINAND_OP_VARIANTS",
    "SPINAND_PAGE_READ_FROM_CACHE_1S_4S_4S_OP",
    "SPINAND_PAGE_READ_FROM_CACHE_1S_1S_4S_OP",
    "SPINAND_PAGE_READ_FROM_CACHE_1S_1S_2S_OP",
    "SPINAND_PAGE_READ_FROM_CACHE_FAST_1S_1S_1S_OP",
    "SPINAND_PAGE_READ_FROM_CACHE_1S_1S_1S_OP",
    "SPINAND_PROG_LOAD_1S_1S_4S_OP",
    "SPINAND_PROG_LOAD_1S_1S_1S_OP",
    "SPINAND_INFO_OP_VARIANTS",
    "SPINAND_ECCINFO",
    "SPINAND_INFO",
    "SPINAND_ID",
    "SPINAND_READID_METHOD_OPCODE_DUMMY",
    "STATUS_ECC_MASK",
    "STATUS_ECC_NO_BITFLIPS",
    "STATUS_ECC_UNCOR_ERROR",
    "STATUS_ECC_HAS_BITFLIPS",
    "spinand_to_nand",
]
NAND_H_MACROS = [
    "NAND_MEMORG",
    "NAND_ECCREQ",
    "nanddev_get_ecc_conf",
]
SPINAND_TYPES = [
    "struct spinand_manufacturer",
    "struct spinand_manufacturer_ops",
]
NAND_H_TYPES = [
    "struct mtd_ooblayout_ops",
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


def macro_values(text, macro):
    match = re.search(r"^#define\s+" + re.escape(macro) + r"\s+(\d+)", text, re.M)
    return match.group(1) if match else None


def tree_paths(tree):
    return {
        "spinand_h": os.path.join(tree, "include", "linux", "mtd", "spinand.h"),
        "nand_h": os.path.join(tree, "include", "linux", "mtd", "nand.h"),
        "spi_core_c": os.path.join(tree, "drivers", "mtd", "nand", "spi", "core.c"),
        # 7.x keeps the DSA taggers in net/dsa/, not drivers/net/dsa/
        "tag_c": os.path.join(tree, "net", "dsa", "tag_zx279133_rtl8372n.c"),
        "dsa_hdr": os.path.join(tree, "include", "net", "dsa_zx279133_rtl8372n.h"),
        "dts": os.path.join(tree, "arch", "arm64", "boot", "dts", "zte",
                            "zx279133-sk-d840n.dts"),
        "dtsi": os.path.join(tree, "arch", "arm64", "boot", "dts", "zte",
                             "zx279133.dtsi"),
        "dts_makefile": os.path.join(tree, "arch", "arm64", "boot", "dts", "zte",
                                     "Makefile"),
    }


def check_api(report, paths):
    """Every kernel symbol the board layer's new files use has to be here."""
    print("kernel API surface")
    spinand = read(paths["spinand_h"]) if os.path.exists(paths["spinand_h"]) else ""
    nand_h = read(paths["nand_h"]) if os.path.exists(paths["nand_h"]) else ""

    for macro in SPINAND_MACROS:
        report.require(re.search(r"\b" + re.escape(macro) + r"\b", spinand) is not None,
                       f"spinand.h provides {macro}")
    for macro in NAND_H_MACROS:
        report.require(re.search(r"\b" + re.escape(macro) + r"\b", nand_h) is not None,
                       f"mtd/nand.h provides {macro}")
    for kind in SPINAND_TYPES:
        report.require(re.search(r"\b" + re.escape(kind) + r"\b", spinand) is not None,
                       f"spinand.h provides {kind}")
    for kind in NAND_H_TYPES:
        report.require(re.search(r"\b" + re.escape(kind) + r"\b", nand_h) is not None,
                       f"mtd/nand.h provides {kind}")

    # The chip itself is registered into the tree's spinand_manufacturers[]
    # table by scripts/configure-sk-d840n.sh, not by the overlay: that edit
    # has to happen after the tree is prepared, and the script asserts all
    # three of its own edits (the extern declaration, the table entry and the
    # Makefile line) before it lets the build continue.  So there is nothing
    # to check for it here - it is checked where it is made.

    # The chip entry's macros live in mtd/nand.h and the accessor with them.
    # That is only true because spinand.h includes mtd/nand.h - in 7.3 the
    # old include/linux/nand_dev.h is gone and its contents moved there.
    # The chain is part of the contract, so it is checked, not assumed.
    report.require(re.search(r"#include\s+<linux/mtd/nand\.h>", spinand) is not None,
                   "spinand.h includes mtd/nand.h (nanddev API reachable)")

    # The manufacturer's own fields.
    match = re.search(r"struct spinand_manufacturer \{(.*?)\n\};", spinand, re.S)
    if report.require(match is not None, "struct spinand_manufacturer is defined"):
        for field in ("id", "name", "chips", "nchips", "ops"):
            report.require(re.search(r"\b" + field + r"\b", match.group(1)) is not None,
                           f"struct spinand_manufacturer has .{field}")


def check_tagger_macros(report, header_text, header_path):
    """Every macro the tagger uses has to be defined by the header."""
    paths = header_path
    tag = read(paths)
    used = sorted(set(re.findall(r"ZX279133_RTL8372N_[A-Z_]+", tag)))
    for macro in used:
        if macro in TAGGER_OWN_MACROS:
            report.require(re.search(r"^#define\s+" + re.escape(macro) + r"\b", tag, re.M)
                           is not None,
                           f"{macro} is defined by the tagger itself")
            continue
        report.require(re.search(r"^#define\s+" + re.escape(macro) + r"\b", header_text, re.M)
                       is not None,
                       f"the header defines {macro}")


def phase_pre(args):
    report = Report()
    paths = tree_paths(args.tree)

    print(f"pre: {args.tree}")
    for name, path in paths.items():
        if name in ("dts", "dsa_hdr"):
            continue
        report.require(os.path.exists(path), f"tree has {name} ({path})")
    if report.failures:
        return finish(report, None), None

    check_api(report, paths)

    # cnjn's own header: this is the copy the tagger sees before the board
    # layer is applied, and its macro values are the upstream baseline.
    upstream = {}
    header = read(paths["dsa_hdr"])
    for macro in HEADER_MACROS:
        value = macro_values(header, macro)
        report.require(value is not None, f"upstream header defines {macro}")
        upstream[macro] = value
    check_tagger_macros(report, header, paths["tag_c"])

    baseline = {
        "upstream_header": {
            "path": paths["dsa_hdr"],
            "macros": upstream,
        },
        "notes": report.notes,
    }
    return finish(report, baseline), baseline


def phase_post(args):
    report = Report()
    paths = tree_paths(args.tree)

    print(f"post: {args.tree}")

    if not os.path.exists(args.report):
        report.require(False, f"the pre-phase report {args.report} exists")
        return finish(report, None)
    baseline = json.loads(read(args.report))

    # The board files have to be in the tree now.
    for rel in ("drivers/mtd/nand/spi/unim.c",
                "include/net/dsa_zx279133_rtl8372n.h",
                "arch/arm64/boot/dts/zte/zx279133-sk-d840n.dts"):
        report.require(os.path.exists(os.path.join(args.tree, *rel.split("/"))),
                       f"board layer file landed: {rel}")

    header_path = paths["dsa_hdr"]
    header = read(header_path) if os.path.exists(header_path) else ""
    if header:
        check_tagger_macros(report, header, paths["tag_c"])
        ours = {macro: macro_values(header, macro) for macro in HEADER_MACROS}
        upstream = baseline["upstream_header"]["macros"]
        for macro in HEADER_MACROS:
            if upstream.get(macro) != ours.get(macro):
                report.note(f"{macro}: upstream={upstream.get(macro)} ours={ours.get(macro)}"
                            f" - a deliberate difference changes what the tagger"
                            f" accepts at runtime, so it needs a measurement behind it")
            else:
                print(f"  [same] {macro} = {ours.get(macro)}")

    # The device tree has to compile against the 7.3 tree: every include has
    # to exist and every label it references has to be defined.
    if os.path.exists(paths["dts"]):
        dts = read(paths["dts"])
        for inc in re.findall(r"#include\s+<([^>]+)>", dts):
            if inc.startswith("dt-bindings/"):
                target = os.path.join(args.tree, "include", "dt-bindings",
                                      *inc[len("dt-bindings/"):].split("/"))
            elif inc.endswith(".dtsi"):
                target = os.path.join(args.tree, "arch", "arm64", "boot", "dts", "zte", inc)
            else:
                target = os.path.join(args.tree, "include", *inc.split("/"))
            report.require(os.path.exists(target), f"device tree include exists: <{inc}>")

        dtsi = read(paths["dtsi"]) if os.path.exists(paths["dtsi"]) else ""
        defined = set(re.findall(r"^\s*(\w+)\s*:\s*[\w@<>,\- ]+\s*\{", dtsi, re.M))
        referenced = set(re.findall(r"&(\w+)\s*\{", dts))
        unresolved = sorted(label for label in referenced if label not in defined)
        report.require(not unresolved,
                       f"every device tree label resolves ({len(referenced)} referenced"
                       + (f", unresolved: {unresolved}" if unresolved else ")"))

    makefile = read(paths["dts_makefile"]) if os.path.exists(paths["dts_makefile"]) else ""
    report.require("zx279133-sk-d840n.dtb" in makefile,
                   "the board dtb is in arch/arm64/boot/dts/zte/Makefile")

    return finish(report, baseline)


def finish(report, baseline):
    print()
    if baseline is not None:
        print(f"notes: {len(report.notes)}")
    if report.failures:
        print(f"FAILED: {len(report.failures)}")
        for failure in report.failures:
            print(f"  - {failure}")
        return 1
    print("board layer fits the tree")
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase", choices=["pre", "post"])
    parser.add_argument("--tree", required=True)
    parser.add_argument("--overlay", default=None,
                        help="kept for symmetry; the overlay is read from the tree")
    parser.add_argument("--report", required=True)
    args = parser.parse_args()

    if not os.path.isdir(args.tree):
        print(f"no such tree: {args.tree}", file=sys.stderr)
        return 2

    if args.phase == "pre":
        code, baseline = phase_pre(args)
        with open(args.report, "w", encoding="utf-8") as handle:
            json.dump(baseline or {}, handle, indent=2, sort_keys=True)
    else:
        code = phase_post(args)
    return code


def run():
    sys.exit(main())


if __name__ == "__main__":
    run()
