#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Check that the ported 9132 switch driver fits the Linux 7.3 tree.

None of the driver can be compiled on the machine that pushes this
repository: there is no aarch64 cross toolchain there, and the build only
happens in CI, seventy minutes into a job.  So the things a compiler would
say about it are checked here instead, against the tree the build will
actually use.

What is checked:

  * The driver and the two headers it brings are in the tree.
  * Every ZX279133_ and zx279133_ name the driver uses is defined - by the
    driver itself, by the tree's own headers, or by the private header the
    driver brings.  A name that is defined nowhere is a compile error, and
    it is the shape of error this port is most likely to produce: the two
    generations of the driver set spell some of the 9132's registers
    differently, and the 7.3 tree's zx279133.h is the SoC's header, not the
    switch's.
  * Every kernel function the driver calls exists in the tree.
  * The service contract holds: each zx279133_lan_service_* wrapper the
    driver calls is in the tree's lan.h, the operation it wraps is in the
    service's operation table, and the parent driver actually provides it.
    The last one is what a link error would say, seventy minutes later.
  * The build wiring is there: the Kconfig symbol, the Makefile object, the
    device tree node, and the pin that turns the symbol on.

Usage:
  check-sk-switch-api.py --tree TREE [--repo REPO]
"""
import argparse
import os
import re
import subprocess
import sys
from pathlib import Path

ZTE = "drivers/net/ethernet/zte"

# The files the 7.3 line's driver tree brings into the kernel.
DRIVER = "zx279133-switch.c"
PRIVATE_HEADER = "zx279133-9132.h"
LED_HEADER = "zx279133-lan1-led.h"

# The service operations the driver needs, and the ones it may use if the
# parent provides them.
REQUIRED_OPS = [
    "nppt_read",
    "nppt_write",
    "xmac_lock",
    "xmac_unlock",
    "control_begin",
    "control_end",
    "datapath_get",
    "datapath_set_ready",
    "datapath_put",
    "set_dsa_active",
    "port_wan_set",
]

# The service wrappers the driver calls.
USED_WRAPPERS = [
    "zx279133_lan_service_valid",
    "zx279133_lan_service_nppt_read",
    "zx279133_lan_service_nppt_write",
    "zx279133_lan_service_xmac_lock",
    "zx279133_lan_service_xmac_unlock",
    "zx279133_lan_service_control_begin",
    "zx279133_lan_service_control_end",
    "zx279133_lan_service_datapath_get",
    "zx279133_lan_service_datapath_set_ready",
    "zx279133_lan_service_datapath_put",
    "zx279133_lan_service_set_dsa_active",
    "zx279133_lan_service_port_wan_set",
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
    # Objects: a file's own driver struct, its operation table, its module
    # parameters.  They are definitions too, and a name that is defined only
    # here must not be reported as missing.
    names |= set(re.findall(
        r"^\s*(?:static\s+)?(?:const\s+)?[\w \*]*?\b((?:zx279133_|ZX279133_)\w+)"
        r"\s*(?:=|;|\[|\()", text, re.M))
    # Names a macro generates.  ATTRIBUTE_GROUPS(x) defines x_grp and x_groups,
    # and the LED's group table is named that way.
    for group in re.findall(r"^\s*ATTRIBUTE_GROUPS\(\s*(\w+)\s*\)", text, re.M):
        names.add(group + "_grp")
        names.add(group + "_groups")
    for group in re.findall(r"^\s*ATTRIBUTE_GROUP\(\s*(\w+)\s*\)", text, re.M):
        names.add(group + "_grp")
    return names


def tree_files(tree):
    zte = Path(tree) / ZTE
    return {
        "driver": zte / DRIVER,
        "private": zte / PRIVATE_HEADER,
        "led": zte / LED_HEADER,
        "lan_h": zte / "zx279133-lan.h",
        "eth_h": zte / "zx279133.h",
        "netdev_c": zte / "zx279133-netdev.c",
        "kconfig": zte / "Kconfig",
        "makefile": zte / "Makefile",
        "dsa_hdr": Path(tree) / "include" / "net" / "dsa_zx279133_rtl8372n.h",
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tree", required=True)
    parser.add_argument("--repo", default=None,
                        help="the repository root, for the pin that turns the "
                             "Kconfig symbol on")
    args = parser.parse_args()

    tree = Path(args.tree)
    report = Report()
    paths = tree_files(tree)

    print(f"the 9132 switch driver in {tree}")

    print("the files the driver brings")
    for key in ("driver", "private", "led"):
        report.require(paths[key].is_file(),
                       f"{paths[key].relative_to(tree)} is in the tree")

    if not paths["driver"].is_file():
        return finish(report)

    driver = strip_comments(read(paths["driver"]))
    driver_names = defines_of(driver)

    # Every name the tree's own headers provide.
    available = set()
    for path in sorted((tree / ZTE).glob("*")):
        if path.is_file() and path.suffix in (".c", ".h"):
            available |= defines_of(strip_comments(read(path)))
    if paths["dsa_hdr"].is_file():
        available |= defines_of(strip_comments(read(paths["dsa_hdr"])))

    print("the names the driver uses")
    used = set(re.findall(r"\bZX279133_[A-Z0-9_]+\b", driver))
    used |= set(re.findall(r"\bzx279133_[a-z0-9_]+\b", driver))
    missing = sorted(name for name in used
                     if name not in available and name not in driver_names)
    report.require(not missing,
                   "every ZX279133_/zx279133_ name the driver uses is defined"
                   + (f" (missing: {missing})" if missing else ""))

    print("the kernel functions the driver calls")
    called = set(re.findall(r"\b([a-z_][a-z0-9_]{2,})\s*\(", driver))
    called -= driver_names
    called -= {name for name in called if name.startswith("zx279133_")}
    called -= {name for name in called if name.startswith("ZX279133")}
    absent = sorted(name for name in called
                    if not name_in_tree(tree, name))
    report.require(not absent,
                   "every kernel function the driver calls exists in the tree"
                   + (f" (missing: {absent})" if absent else ""))

    print("the service contract")
    lan_h = strip_comments(read(paths["lan_h"])) if paths["lan_h"].is_file() else ""
    netdev_c = strip_comments(read(paths["netdev_c"])) if paths["netdev_c"].is_file() else ""
    for wrapper in USED_WRAPPERS:
        report.require(re.search(r"\b" + wrapper + r"\s*\(", lan_h) is not None,
                       f"lan.h provides {wrapper}")
    ops_block = ""
    match = re.search(r"struct zx279133_lan_service_ops \{(.*?)\n\};", lan_h, re.S)
    if match:
        ops_block = match.group(1)
    for op in REQUIRED_OPS:
        report.require(re.search(r"\(\*" + op + r"\)\s*\(", ops_block) is not None,
                       f"the service table has {op}")
        report.require(re.search(r"\.%s\s*=" % re.escape(op), netdev_c) is not None,
                       f"the parent provides {op}")
    report.require(re.search(r"service->ops->control_begin", lan_h) is not None,
                   "lan.h's validity check asks for control_begin")
    report.require(re.search(r"service->ops->control_end", lan_h) is not None,
                   "lan.h's validity check asks for control_end")

    print("the build wiring")
    kconfig = read(paths["kconfig"]) if paths["kconfig"].is_file() else ""
    report.require(re.search(r"^config ZTE_ZX279133_SWITCH\s*$", kconfig, re.M)
                   is not None,
                   "the Kconfig has ZTE_ZX279133_SWITCH")
    makefile = read(paths["makefile"]) if paths["makefile"].is_file() else ""
    report.require("obj-$(CONFIG_ZTE_ZX279133_SWITCH) += zx279133-switch.o" in makefile,
                   "the Makefile builds zx279133-switch.o")
    dts = tree / "arch" / "arm64" / "boot" / "dts" / "zte" / "zx279133-sk-d840n.dts"
    if dts.is_file():
        report.require('compatible = "zte,zx279133-switch"' in read(dts),
                       "the board device tree names the switch node")

    if args.repo:
        script = Path(args.repo) / "scripts" / "configure-sk-d840n.sh"
        if script.is_file():
            report.require(re.search(r"^\s*pin_y ZTE_ZX279133_SWITCH\s*$",
                                     read(script), re.M) is not None,
                           "the configure script pins ZTE_ZX279133_SWITCH")

    return finish(report)


def name_in_tree(tree, name):
    """Does the tree mention this name at all?"""
    pattern = re.compile(r"\b" + re.escape(name) + r"\b")
    for root in ("include", "kernel", "net", "drivers", "arch", "fs", "lib"):
        path = tree / root
        if not path.is_dir():
            continue
        try:
            out = subprocess.run(
                ["rg", "-q", "--no-messages", "-w", pattern.pattern, str(path)],
                capture_output=True, timeout=120)
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return True
        if out.returncode == 0:
            return True
    return False


def finish(report):
    print()
    if report.failures:
        print(f"FAILED: {len(report.failures)}")
        for failure in report.failures:
            print(f"  - {failure}")
        return 1
    print("the 9132 switch driver fits the tree")
    return 0


if __name__ == "__main__":
    sys.exit(main())
