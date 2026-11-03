#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Cross-check the exact target C parser and independent host parser."""
import copy
import importlib.util
import os
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import unittest
import shlex
import shutil

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
from sk_layout import inspect_layout
from sk_layout_fixture import TABLE, at, build_checker, dtb, pack, put, tree

SPEC = importlib.util.spec_from_file_location("transition", ROOT / "tools/verify-root241.py")
transition = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(transition)


class LayoutTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix="sk-layout-")
        cls.addClassCleanup(cls.temp.cleanup)
        cls.directory = Path(cls.temp.name)
        cls.checker = build_checker(cls.directory)

    def check(self, blob, expected=None):
        image = self.directory / "input.dtb"
        image.write_bytes(blob)
        result = subprocess.run([self.checker, str(image)], capture_output=True, text=True, timeout=5)
        if expected:
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout.strip(), expected)
            self.assertEqual(inspect_layout(blob)["layout"], expected)
        else:
            self.assertNotEqual(result.returncode, 0, result.stdout)
            with self.assertRaises((ValueError, UnicodeError, struct.error)):
                inspect_layout(blob)

    def test_both_supported_layouts_and_padding(self):
        for name in ("current240", "native241"):
            for pad in (b"", b"\0" * 16, b"\xff" * 16):
                with self.subTest(name=name, padding=pad):
                    self.check(dtb(name) + pad, name)

    def test_each_offset_size_alias_and_label(self):
        baseline = tree()
        for i, node in enumerate(at(baseline, TABLE)[2]):
            for defect in ("offset", "size", "alias", "label", "read-only", "disabled"):
                with self.subTest(part=i, defect=defect):
                    candidate = copy.deepcopy(baseline)
                    part = at(candidate, TABLE)[2][i]
                    props = dict(part[1])
                    if defect in ("offset", "size"):
                        reg = list(struct.unpack(">II", props["reg"]))
                        reg[defect == "size"] += 131072
                        put(part, "reg", struct.pack(">II", *reg))
                    elif defect == "alias":
                        put(at(candidate, "/aliases"), f"mtd{i + 1}", b"/wrong\0")
                    elif defect == "label":
                        put(part, "label", b"wrong\0")
                    elif defect == "read-only":
                        if i in (0, 3): part[1][:] = [(k, v) for k, v in part[1] if k != "read-only"]
                        else: put(part, "read-only", b"")
                    else:
                        put(part, "status", b"disabled\0")
                    self.check(pack(candidate))

    def test_stale_scratch_alias_extra_missing_and_reordered_parts(self):
        for defect in ("scratch", "alias", "missing", "reordered", "duplicate-node", "duplicate-property", "second-table"):
            with self.subTest(defect=defect):
                candidate = tree()
                parts = at(candidate, TABLE)[2]
                if defect == "scratch": parts.append(copy.deepcopy(at(tree("current240"), TABLE)[2][-1]))
                elif defect == "alias": put(at(candidate, "/aliases"), "mtd6", b"/stale\0")
                elif defect == "missing": parts.pop()
                elif defect == "reordered": parts[0], parts[1] = parts[1], parts[0]
                elif defect == "duplicate-node": parts.append(copy.deepcopy(parts[0]))
                elif defect == "duplicate-property": parts[0][1].append(("label", b"boot\0"))
                else:
                    extra = copy.deepcopy(at(candidate, TABLE)); extra[0] = "other-table"
                    candidate[2].append(extra)
                self.check(pack(candidate))

    def test_wrong_board_root_args_cells_and_disabled_ancestor(self):
        for path, name, value in (("/", "compatible", b"zte,133\0"),
                                   ("/chosen", "bootargs", b"root=/dev/mtdblock4 rootfstype=jffs2\0"),
                                   ("/chosen", "bootargs", b"root=/dev/mtdblock5 rootfstype=jffs2 mtdparts=bad\0"),
                                   (TABLE, "#size-cells", struct.pack(">I", 2)),
                                   ("/soc", "status", b"disabled\0")):
            with self.subTest(path=path, name=name):
                candidate = tree(); put(at(candidate, path), name, value)
                self.check(pack(candidate))

    def test_truncated_corrupt_oversized_and_invalid_padding(self):
        valid = dtb()
        for blob in (b"", valid[:39], valid[:-1], b"BAD!" + valid[4:],
                     valid + b"garbage", valid + bytes(1048577),
                     valid[:8] + struct.pack(">I", 0xfffffff0) + valid[12:]):
            with self.subTest(length=len(blob)):
                self.check(blob)
        for offset, value in ((8, 40), (12, 56), (16, 0), (20, 18), (24, 15), (36, 0xfffffff0)):
            with self.subTest(header_field=offset):
                self.check(valid[:offset] + struct.pack(">I", value) + valid[offset + 4:])

    def test_only_partition_merge_is_allowed(self):
        before, after = dtb("current240"), dtb("native241")
        self.assertEqual(transition.verify_transition(before, after)["root_bytes"], 252706816)
        changed = tree(); put(changed, "model", b"different\0")
        with self.assertRaisesRegex(ValueError, "beyond root"):
            transition.verify_transition(before, pack(changed))

    def test_live_alias_geometry_and_no_extra_partition(self):
        shell = shutil.which("sh") or "C:/Program Files/Git/bin/sh.exe"
        quote = lambda p: shlex.quote(str(p).replace("\\", "/"))
        source = (ROOT / "overlays/target/linux/zte/zx279133/base-files/lib/upgrade/platform.sh").read_text(encoding="utf-8")
        for layout in ("current240", "native241"):
            for defect in (None, "offset", "index", "extra", "erase", "capacity"):
                with self.subTest(layout=layout, defect=defect), tempfile.TemporaryDirectory(prefix="sk-live-") as tmp:
                    directory = Path(tmp); sysfs = directory / "sysfs"; proc = directory / "mtd"
                    records = []
                    parts = inspect_layout(dtb(layout))["partitions"]
                    for part in parts:
                        index = 7 if defect == "index" and part["mtd"] == 5 else part["mtd"]
                        node = sysfs / f"mtd{index}"; node.mkdir(parents=True)
                        values = {"name": part["label"], "size": part["size"], "offset": part["offset"], "erasesize": 131072}
                        if index == 5:
                            if defect == "offset": values["offset"] += 131072
                            if defect == "erase": values["erasesize"] = 4096
                            if defect == "capacity": values["size"] += 131072
                        for key, value in values.items(): (node / key).write_text(str(value) + "\n", encoding="utf-8")
                        records.append(f'mtd{index}: {part["size"]:08x} 00020000 "{part["label"]}"')
                    if defect == "extra": records.append('mtd8: 00100000 00020000 "extra"')
                    proc.write_text('dev: size erasesize name\n' + '\n'.join(records) + '\n', encoding="utf-8")
                    text = source.replace(". /lib/functions.sh", ":").replace("/proc/mtd", str(proc).replace("\\", "/"))
                    text = text.replace("/sys/class/mtd", str(sysfs).replace("\\", "/"))
                    script = directory / "live.sh"; script.write_text(text, encoding="utf-8", newline="\n")
                    result = subprocess.run([shell, "-c", ". " + quote(script) + "; skd840n_live_layout"], capture_output=True, text=True, timeout=10)
                    self.assertEqual(result.returncode == 0, defect is None, result.stdout + result.stderr)
                    if defect is None: self.assertEqual(result.stdout.strip(), layout)


if __name__ == "__main__":
    unittest.main(verbosity=2)
