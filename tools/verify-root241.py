#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Verify a DTB-only current240 -> native241 transition; never modify a DTB/device."""
import argparse
import copy
import hashlib
import json
from pathlib import Path
import struct

from sk_layout import inspect_layout, parse_tree, require


def reservations(blob):
    offset, = struct.unpack_from(">I", blob, 16)
    items = []
    while True:
        item = struct.unpack_from(">QQ", blob, offset)
        offset += 16
        if item == (0, 0):
            return items
        items.append(item)


def verify_transition(before, after):
    old_layout, new_layout = inspect_layout(before), inspect_layout(after)
    require(old_layout["layout"] == "current240" and new_layout["layout"] == "native241", "not a 240 -> 241 transition")
    old, new = parse_tree(before), parse_tree(after)
    expected = copy.deepcopy(old)
    root, = [p for p, n in old.items() if n["props"].get("label") == b"root\0"]
    scratch, = [p for p, n in old.items() if n["props"].get("label") == b"scratch\0"]
    require(not old[scratch]["children"], "unexpected scratch children")
    expected[root]["props"]["reg"] = struct.pack(">II", 0xf00000, 0xf100000)
    del expected["/aliases"]["props"]["mtd6"]
    expected[expected[scratch]["parent"]]["children"].remove(scratch)
    del expected[scratch]
    require(expected == new, "DTB changes extend beyond root reg, scratch node and mtd6 alias")
    require(reservations(before) == reservations(after), "memory reservation map changed")
    require(before[20:32] == after[20:32], "FDT compatibility/boot CPU changed")
    return {"from": old_layout["layout"], "to": new_layout["layout"],
            "root_bytes": new_layout["root_bytes"], "root_mtd": 5,
            "old_sha256": hashlib.sha256(before).hexdigest(),
            "new_sha256": hashlib.sha256(after).hexdigest(), "semantic_diff_only_partition_merge": True}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("old", type=Path)
    parser.add_argument("new", type=Path)
    args = parser.parse_args()
    try:
        print(json.dumps(verify_transition(args.old.read_bytes(), args.new.read_bytes()), indent=2))
    except (ValueError, OSError, UnicodeError, struct.error) as error:
        parser.exit(1, str(error) + "\n")


if __name__ == "__main__":
    main()
