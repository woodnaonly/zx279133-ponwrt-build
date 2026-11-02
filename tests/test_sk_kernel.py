#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Portable stable-source regressions; no network or real firmware needed."""
import copy
import hashlib
import importlib.util
import json
import lzma
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import yaml

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("sk_kernel", ROOT / "tools/prepare-sk-kernel.py")
k = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(k)


def makefile(version):
    major, minor, sub = version.split(".")
    return f"VERSION = {major}\nPATCHLEVEL = {minor}\nSUBLEVEL = {sub}\nEXTRAVERSION =\nNAME = fixture\n"


DIFF = (b"--- a/Makefile\n+++ b/Makefile\n@@ -1,5 +1,5 @@\n VERSION = 6\n"
        b" PATCHLEVEL = 18\n-SUBLEVEL = 38\n+SUBLEVEL = 39\n EXTRAVERSION =\n NAME = fixture\n")


class KernelTests(unittest.TestCase):
    def test_lock_is_contiguous_and_workflows_agree(self):
        lock = k.load_lock()
        self.assertEqual(len(lock["patches"]), 22)
        workflows = ROOT / ".github/workflows"
        for name in ("build-sk-d840n.yml", "diagnose-config.yml"):
            workflow = yaml.safe_load((workflows / name).read_text(encoding="utf-8"))
            env = workflow["env"]
            self.assertEqual(env["KVER"], lock["version"])
            self.assertEqual(env["KERNEL_SHA"], lock["kernel_sha"])
            self.assertTrue(env["KERNEL_SOURCE_DIR"].endswith("linux-" + lock["base_version"]))
            self.assertEqual(env["IW_SHA"], "1b50aaf4ed9cf042df6207564555b244d6ced517")
            runs = "\n".join(s.get("run", "") for job in workflow["jobs"].values() for s in job["steps"])
            self.assertIn('prepare-sk-kernel.py --tree "$KERNEL_SOURCE_DIR" --version "$KVER"', runs)
            self.assertNotIn("linux-mainline-zte-zxslc-sr1010/linux-$KVER", runs)
        sr = yaml.safe_load((workflows / "build-sr1010.yml").read_text(encoding="utf-8"))
        self.assertEqual(sr["env"]["KVER"], "6.18.38")

    def test_missing_reordered_or_corrupt_pin_is_rejected(self):
        original = k.load_lock()
        for change in (lambda v: v["patches"].pop(0),
                       lambda v: v["patches"].reverse(),
                       lambda v: v["patches"][0].__setitem__(1, "0")):
            value = copy.deepcopy(original)
            change(value)
            with tempfile.TemporaryDirectory() as tmp:
                path = Path(tmp) / "lock.json"
                path.write_text(json.dumps(value), encoding="utf-8")
                with self.assertRaises(ValueError):
                    k.load_lock(path)

    def test_cached_corruption_fails_without_fetching(self):
        with tempfile.TemporaryDirectory() as tmp:
            cache = Path(tmp)
            data = lzma.compress(DIFF)
            (cache / "test.xz").write_bytes(data + b"corrupt")
            with patch.object(k.urllib.request, "urlopen") as fetch:
                with self.assertRaisesRegex(ValueError, "cached patch SHA256"):
                    k.download_patch(("test.xz", hashlib.sha256(data).hexdigest()), cache, "https://example.invalid/")
                fetch.assert_not_called()

    def test_verified_cache_and_overlay_overlap(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            data = lzma.compress(DIFF)
            (directory / "test.xz").write_bytes(data)
            self.assertEqual(k.download_patch(("test.xz", hashlib.sha256(data).hexdigest()), directory, ""), DIFF)
            self.assertEqual(k.check_overlay_overlap([DIFF], directory), {"Makefile"})
            (directory / "Makefile").touch()
            with self.assertRaisesRegex(ValueError, "undo official stable fixes"):
                k.check_overlay_overlap([DIFF], directory)

    def test_source_and_target_directory_are_independent(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp) / "linux-6.18.38"
            directory.mkdir()
            (directory / "Makefile").write_text(makefile("6.18.60"), encoding="utf-8")
            self.assertEqual(k.kernel_version(directory), "6.18.60")

    def test_exact_git_apply_and_wrong_base(self):
        with tempfile.TemporaryDirectory() as tmp:
            tree = Path(tmp)
            subprocess.run(["git", "init", "-q", str(tree)], check=True)
            (tree / "Makefile").write_text(makefile("6.18.38"), encoding="utf-8", newline="\n")
            subprocess.run(["git", "-C", str(tree), "add", "Makefile"], check=True)
            subprocess.run(["git", "-C", str(tree), "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
                            "-c", "core.hooksPath=/dev/null", "commit", "-qm", "fixture"], check=True)
            lock = {"base_version": "6.18.38", "version": "6.18.39",
                    "kernel_sha": k.git(tree, "rev-parse", "HEAD"), "patches": [["fixture", "unused"]]}
            with self.assertRaisesRegex(ValueError, "custom kernel commit"):
                k.apply_updates(tree, dict(lock, kernel_sha="0" * 40), [DIFF], tree / "overlays")
            self.assertEqual(k.kernel_version(tree), "6.18.38")
            k.apply_updates(tree, lock, [DIFF], tree / "overlays")
            self.assertEqual(k.kernel_version(tree), "6.18.39")
            with self.assertRaisesRegex(ValueError, "base kernel version"):
                k.apply_updates(tree, lock, [DIFF], tree / "overlays")

    def test_actual_image_and_modules_not_only_labels(self):
        with tempfile.TemporaryDirectory() as tmp:
            tree = Path(tmp)
            (tree / "Makefile").write_text(makefile("6.18.60"), encoding="utf-8")
            (tree / "include/config").mkdir(parents=True)
            (tree / "include/config/kernel.release").write_text("6.18.60+\n", encoding="utf-8")
            (tree / "arch/arm64/boot").mkdir(parents=True)
            image = tree / "arch/arm64/boot/Image"
            image.write_bytes(b"\x00Linux version 6.18.60+ (fixture)\x00")
            rootfs = tree / "rootfs"
            (rootfs / "lib/modules/6.18.60+").mkdir(parents=True)
            k.check_built(tree, "6.18.60", rootfs)
            (rootfs / "lib/modules/6.18.38+").mkdir()
            with self.assertRaisesRegex(ValueError, "module directory"):
                k.check_built(tree, "6.18.60", rootfs)
            image.write_bytes(b"Linux version 6.18.38+ (old)")
            with self.assertRaisesRegex(ValueError, "Image banner"):
                k.check_built(tree, "6.18.60")

    def test_traversal_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "unsafe"):
            k.changed_paths(b"--- a/../bad\n+++ b/../bad\n")
        self.assertEqual(k.changed_paths(b"diff --git a/old.c b/new.c\nsimilarity index 100%\nrename from old.c\nrename to new.c\n"),
                         {"old.c", "new.c"})

    def test_rebase_only_changes_context_not_the_stable_fix(self):
        lock = k.load_lock()
        rebase = lock["rebases"][0]
        replacement = (ROOT / rebase["replacement"]).read_bytes()
        original = replacement.replace(b"static void flow_offload_work_handler(struct work_struct *work)\n",
                                       b"static void flow_offload_tuple_stats(struct flow_offload_work *offload,\n", 1)
        other = b"diff --git a/other.c b/other.c\n--- a/other.c\n+++ b/other.c\n"
        self.assertEqual(k.adapted_patch(rebase["patch"], original + other, lock), replacement + other)
        with self.assertRaisesRegex(ValueError, "changed the official fix"):
            k.adapted_patch(rebase["patch"], original.replace(b"smp_mb__before_atomic", b"wrong"), lock)

    def test_openwrt_metadata_moves_with_external_source(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            directory = root / "target/linux/generic"
            directory.mkdir(parents=True)
            path = directory / "kernel-6.18"
            path.write_text("LINUX_VERSION-6.18 = .38\nLINUX_KERNEL_HASH-6.18.38 = x\n", encoding="utf-8")
            lock = k.load_lock()
            k.update_openwrt_version(root, lock)
            self.assertIn("LINUX_VERSION-6.18 = .60\n", path.read_text())
            self.assertIn("LINUX_KERNEL_HASH-6.18.60 = " + lock["source_tar_sha256"], path.read_text())


if __name__ == "__main__":
    unittest.main(verbosity=2)
