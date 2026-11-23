#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Check that the four driver objects are in the kernel's modules.builtin.

The kernel's build writes a list of the objects it linked into the kernel.
It is the strongest of the three answers to "are the drivers in this
kernel": the merged .config says the driver was configured in, and this
list says the object was really linked.

The list exists in two shapes, and they are not the same file:

  - the kernel's own list, in the build directory, names every object by
    its path: "kernel/drivers/net/ethernet/zte/npu.ko";
  - the copy OpenWrt installs into the image has been rewritten to bare
    names: "npu.ko".

So the check compares the *base name* of every line, and not the whole
line: a check that matches the bare name alone finds nothing in the
kernel's own list, and one that matches the path alone finds nothing in
the image's copy.

Usage:
  python3 tools/check-sk-modules-builtin.py --list <modules.builtin>
"""

import argparse
import sys
from pathlib import Path

# The four driver objects this line pins to =y, one per CONFIG symbol:
#
#   CONFIG_ZTE_ZX279133_ETH           -> npu.o
#   CONFIG_ZTE_ZX279133_LAN_CONDUIT  -> zx279133-lan-conduit.o
#   CONFIG_ZTE_ZX279133_SWITCH        -> zx279133-switch.o
#   CONFIG_ZTE_ZX279133_FNOFFLOAD     -> fnoffload.o
#
# The names are the ones the kernel's Makefile gives them, without the
# .o suffix.
DRIVER_OBJECTS = (
    "npu",
    "zx279133-lan-conduit",
    "zx279133-switch",
    "fnoffload",
)


def objects_in(path):
    """The object names the list carries, with the paths and suffixes gone."""
    out = set()
    text = path.read_text(encoding="utf-8", errors="replace")
    for line in text.splitlines():
        name = line.strip()
        if not name:
            continue
        name = name.rsplit("/", 1)[-1]
        if name.endswith(".ko"):
            name = name[:-3]
        out.add(name)
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--list", type=Path, required=True,
                    help="the kernel's modules.builtin")
    args = ap.parse_args(argv)

    if not args.list.is_file():
        print("FAIL  no modules.builtin at %s" % args.list)
        return 1

    found = objects_in(args.list)
    missing = [o for o in DRIVER_OBJECTS if o not in found]
    if missing:
        print("FAIL  these driver objects are not in %s: %s"
              % (args.list, ", ".join(missing)))
        print("      the list carries %d object(s); the ones that look "
              "like ours:" % len(found))
        for name in sorted(found):
            if any(part in name for part in
                   ("npu", "zx279133", "fnoffload")):
                print("        %s" % name)
        return 1

    print("PASS  %s names all four driver objects" % args.list)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
