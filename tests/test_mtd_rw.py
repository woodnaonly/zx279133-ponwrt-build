#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Offline package/image regressions and real patched module logic on fake MTDs.

No insmod, MTD nodes, serial port, network or device access. Requires patch and
a host C compiler (Windows defaults to the existing bootweb Zig toolchain;
SKD840N_TEST_ZIG can select an existing Zig executable in another checkout).
"""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests/fixtures/mtd-rw"
PACKAGE = ROOT / "overlays/package/kernel/mtd-rw"
spec = importlib.util.spec_from_file_location("mtd_rw_gate", ROOT / "tools/check-sk-mtd-rw.py")
gate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gate)
RELEASE = "6.18.fixture+"
KCONFIG = "CONFIG_MODULES=y\nCONFIG_MODULE_UNLOAD=y\nCONFIG_MTD=y\n"
MANIFEST = "kernel - fixture\nkmod-mtd-rw - fixture-r2\n"


def elf(release=RELEASE, extra=b""):
    names = b"\0.shstrtab\0.modinfo\0"
    info = (b"name=mtd_rw\0vermagic=" + release.encode() + b" SMP mod_unload aarch64\0"
            b"parmtype=i_want_a_brick:bool\0" + extra)
    ident = b"\x7fELF\x02\x01\x01" + bytes(9)
    head = struct.pack("<16sHHIQQQIHHHHHH", ident, 1, 183, 1, 0, 0, 64, 0, 64, 0, 0, 64, 3, 1)
    table = (bytes(64) + struct.pack("<IIQQQQIIQQ", names.index(b".shstrtab"), 3, 0, 0, 256, len(names), 0, 0, 1, 0) +
             struct.pack("<IIQQQQIIQQ", names.index(b".modinfo"), 1, 0, 0, 256 + len(names), len(info), 0, 0, 1, 0))
    return head + table + names + info


def image(root):
    module = root / "lib/modules" / RELEASE / "mtd-rw.ko"
    module.parent.mkdir(parents=True)
    module.write_bytes(elf())
    return module


class MtdRwTests(unittest.TestCase):
    def test_source_pin_and_manual_load_only_recipe(self):
        source = (FIXTURE / "upstream.c").read_bytes()
        self.assertEqual(hashlib.sha256(source).hexdigest(),
                         "8d69d4a58bdb628e97ba13902f21355adafca129934598f9243495d621049073")
        recipe = (PACKAGE / "Makefile").read_text(encoding="utf-8")
        self.assertIn("PKG_SOURCE_VERSION:=e87767395a6d27380196702f5f7bf98e92774f3f", recipe)
        self.assertIn("PKG_MIRROR_HASH:=984218d7a8e1252419c45ef313f23fb6e5edfa83088f68a4a356b795444ab381", recipe)
        self.assertIn('$(KERNEL_MAKE) M="$(PKG_BUILD_DIR)"', recipe)
        self.assertIn("$(call KernelPackage,mtd-rw)", recipe)
        self.assertNotRegex(recipe, r"(?m)^\s*(?:AUTOLOAD|MODPARAMS(?:\.[\w-]+)?)\s*[:+?]?=")

    def test_patched_upstream_c_on_sparse_fake_mtds(self):
        patch = shutil.which("patch")
        if not patch and os.name == "nt":
            patch = "C:/Program Files/Git/usr/bin/patch.exe"
        cc = shutil.which("cc")
        zig = os.environ.get("SKD840N_TEST_ZIG") or str(ROOT / "bootweb/.build/toolchain/zig-x86_64-windows-0.14.1/zig.exe")
        compiler = [cc] if cc else [zig, "cc"]
        with tempfile.TemporaryDirectory(prefix="mtd-rw host test ") as directory:
            tree = Path(directory)
            for name in ("host-test.c", "test-kernel.h"):
                shutil.copyfile(FIXTURE / name, tree / name)
            for name in ("kernel.h", "module.h", "mtd/mtd.h", "err.h"):
                path = tree / "linux" / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text('#include "test-kernel.h"\n', encoding="utf-8", newline="\n")
            module = tree / "mtd-rw.c"
            shutil.copyfile(FIXTURE / "upstream.c", module)
            flags = ["-std=c11", "-O1", "-Wall", "-Wextra", "-Werror", "-Wno-sign-compare",
                     "-fsanitize=undefined", "-fsanitize-undefined-trap-on-error", "-I", str(tree)]
            binary = tree / ("host-test.exe" if os.name == "nt" else "host-test")

            def run_model():
                built = subprocess.run([*compiler, *flags, str(tree / "host-test.c"), "-o", str(binary)],
                                       capture_output=True, text=True, timeout=90)
                self.assertEqual(built.returncode, 0, built.stdout + built.stderr)
                return subprocess.run([str(binary)], capture_output=True, text=True, timeout=10)

            original = run_model()
            self.assertNotEqual(original.returncode, 0, "upstream must reproduce the missing-mtd0 regression")
            change = (PACKAGE / "patches/010-sparse-mtd-and-64bit-unlock-map.patch").read_text(encoding="utf-8")
            applied = subprocess.run([patch, "--batch", "--forward", "--fuzz=0", "-p1"], cwd=tree,
                                     input=change, capture_output=True, text=True, timeout=20)
            self.assertEqual(applied.returncode, 0, applied.stdout + applied.stderr)
            fixed = run_model()
            self.assertEqual(fixed.returncode, 0, fixed.stdout + fixed.stderr)
            self.assertIn("PASS: opt-in, missing mtd0, sparse aliases, 64-bit restore", fixed.stdout)

    def test_kernel_prerequisites_must_be_built_in(self):
        gate.validate_kernel(KCONFIG)
        for name in ("MTD", "MODULES", "MODULE_UNLOAD"):
            for replacement in ("", f"CONFIG_{name}=m\n", f"# CONFIG_{name} is not set\n"):
                with self.subTest(name=name, replacement=replacement), self.assertRaises(ValueError):
                    gate.validate_kernel(KCONFIG.replace(f"CONFIG_{name}=y\n", replacement))
        with self.assertRaises(ValueError):
            gate.validate_kernel(KCONFIG + "# CONFIG_MODULES is not set\n")

    def test_manifest_and_exact_module_release_are_required(self):
        with tempfile.TemporaryDirectory(prefix="mtd-rw image ") as directory:
            root = Path(directory)
            module = image(root)
            result = gate.validate_image(root, MANIFEST, RELEASE)
            self.assertTrue(result["manual_load_only"])
            self.assertFalse(result["hardware_load_verified"])
            for manifest in ("", MANIFEST + MANIFEST, "kmod-mtd-rw-extra - fixture\n"):
                with self.subTest(manifest=manifest), self.assertRaises(ValueError):
                    gate.validate_image(root, manifest, RELEASE)
            for release in ("wrong-kernel", "", "../../boot", "two words"):
                with self.subTest(release=release), self.assertRaises(ValueError):
                    gate.validate_image(root, MANIFEST, release)
            module.unlink()
            with self.assertRaises(ValueError):
                gate.validate_image(root, MANIFEST, RELEASE)

    def test_bad_arch_truncated_metadata_and_duplicate_vermagic_rejected(self):
        with tempfile.TemporaryDirectory(prefix="mtd-rw ELF ") as directory:
            root = Path(directory)
            module = image(root)
            wrong_machine = bytearray(elf())
            struct.pack_into("<H", wrong_machine, 18, 62)
            for data in (b"", elf()[:100], bytes(wrong_machine), elf("wrong-kernel"),
                         elf(extra=b"vermagic=duplicate\0"), elf().replace(b"parmtype=", b"notatype=")):
                module.write_bytes(data)
                with self.subTest(data=data[:40]), self.assertRaises(ValueError):
                    gate.validate_image(root, MANIFEST, RELEASE)

    def test_automatic_loading_is_rejected_in_both_directories(self):
        with tempfile.TemporaryDirectory(prefix="mtd-rw autoload ") as directory:
            root = Path(directory)
            image(root)
            for folder in ("etc/modules.d", "etc/modules-boot.d"):
                entry = root / folder / "99-test"
                entry.parent.mkdir(parents=True)
                for name in ("mtd-rw", "mtd_rw"):
                    entry.write_text(name + " i_want_a_brick=1\n", encoding="utf-8")
                    with self.assertRaisesRegex(ValueError, "must not be auto-loaded"):
                        gate.validate_image(root, MANIFEST, RELEASE)
                entry.write_text("# mtd-rw stays manual\nother-module\n", encoding="utf-8")
                gate.validate_image(root, MANIFEST, RELEASE)

    def test_gate_uses_built_kernel_not_a_hardcoded_version(self):
        with tempfile.TemporaryDirectory(prefix="mtd-rw CLI ") as directory:
            tree = Path(directory)
            kernel = tree / "external kernel"
            (kernel / "include/config").mkdir(parents=True)
            (kernel / ".config").write_text(KCONFIG, encoding="utf-8", newline="\n")
            (kernel / "include/config/kernel.release").write_text(RELEASE + "\n", encoding="utf-8")
            (tree / ".config").write_text('CONFIG_PACKAGE_kmod-mtd-rw=y\nCONFIG_EXTERNAL_KERNEL_TREE="' +
                                           kernel.as_posix() + '"\n', encoding="utf-8", newline="\n")
            root = tree / "root-zte"
            image(root)
            manifest = tree / "firmware.manifest"
            manifest.write_text(MANIFEST, encoding="utf-8", newline="\n")
            command = [sys.executable, str(ROOT / "tools/check-sk-mtd-rw.py"), "--tree", str(tree),
                       "--root", str(root), "--manifest", str(manifest)]
            result = subprocess.run(command, capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertEqual(json.loads(result.stdout.splitlines()[0])["kernel_release"], RELEASE)
            (kernel / "include/config/kernel.release").write_text("other-kernel\n", encoding="utf-8")
            result = subprocess.run(command, capture_output=True, text=True, timeout=10)
            self.assertNotEqual(result.returncode, 0)

    def test_both_modes_select_and_gate_shipped_module(self):
        configure = (ROOT / "scripts/configure-sk-d840n.sh").read_text(encoding="utf-8")
        self.assertLess(configure.index("printf 'CONFIG_PACKAGE_kmod-mtd-rw=y"), configure.index("make defconfig\n"))
        self.assertIn("for package in kmod-mtd-rw ", configure)
        self.assertIn("pin_y MODULES\n", configure)
        self.assertIn("pin_y MODULE_UNLOAD\n", configure)
        management = (ROOT / "scripts/check-sk-d840n-management.sh").read_text(encoding="utf-8")
        self.assertIn('tools/check-sk-mtd-rw.py --tree "$TREE" --root "$ROOT" --manifest "$MANIFEST"', management)
        workflow = (ROOT / ".github/workflows/build-sk-d840n.yml").read_text(encoding="utf-8")
        self.assertIn('"$PY" tests/test_mtd_rw.py\n', workflow)
        self.assertIn("- tests/test_mtd_rw.py\n", workflow)


if __name__ == "__main__":
    unittest.main(verbosity=2)
