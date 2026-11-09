#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Compile actual provider diagnostics against clock/lock lifecycle mocks.

This checks observation safety and unchanged error paths, not PLL hardware.
"""
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
TEXT = (ROOT / "kernel-overlays/drivers/phy/zte/phy-zx279133-uni-serdes.c").read_text(encoding="utf-8")
REQUIRE = "--require-compiler" in sys.argv
if REQUIRE:
    sys.argv.remove("--require-compiler")


def block(pattern):
    matches = list(re.finditer(pattern, TEXT))
    if len(matches) != 1:
        raise AssertionError("Expected one provider definition: " + pattern)
    start, end, depth = matches[0].start(), matches[0].end(), 1
    while depth:
        depth += (TEXT[end] == "{") - (TEXT[end] == "}")
        end += 1
    return TEXT[start:end]


def function(name):
    return block(r"static\s+(?:int|void)\s+" + name + r"\([^;]*?\)\s*\{")


class SerdesDiagnosticTests(unittest.TestCase):
    def run_contract(self, mutation=None):
        body = function("zx279133_uni_serdes_power_on")
        snapshot = function("zx279133_uni_serdes_rate_snapshot")
        if mutation:
            body, snapshot = mutation(body, snapshot)
        definitions = block(r"struct zx279133_uni_serdes\s*\{") + ";\n"
        definitions += "\n".join(re.findall(r"(?m)^#define ZX279133_UNI_[^\n]+", TEXT))
        for name in ("zx279133_uni_1g_profile", "zx279133_uni_1gbase_x_profile"):
            definitions += "\n" + block(r"static const u32 " + name + r"\[\]\s*=\s*\{") + ";\n"
        source = (ROOT / "tests/sk_serdes_diag_harness.h").read_text(encoding="utf-8")
        source = source.replace("/* PROVIDER_DEFINITIONS */", definitions)
        source = source.replace("/* PROVIDER_FUNCTIONS */", snapshot + "\n" + body)
        compiler = shutil.which("cc") or shutil.which("gcc")
        ndk = Path("C:/Dev/Tools/Android/SDK/ndk/android-ndk-r27d/toolchains/llvm/prebuilt/windows-x86_64/bin")
        with tempfile.TemporaryDirectory(prefix="sk-serdes-diag-") as temporary:
            directory = Path(temporary)
            src, exe = directory / "test.c", directory / "test.exe"
            src.write_text(source, encoding="utf-8")
            flags = ["-std=c11", "-Wall", "-Wextra", "-Werror", "-Wno-unused-function", "-fno-builtin"]
            if compiler:
                subprocess.run([compiler, *flags, str(src), "-o", str(exe)], check=True, timeout=60)
            elif (ndk / "clang.exe").is_file():
                obj = directory / "test.obj"
                subprocess.run([str(ndk / "clang.exe"), "--target=x86_64-pc-windows-msvc", "-ffreestanding",
                                "-fno-stack-protector", *flags, "-c", str(src), "-o", str(obj)], check=True, timeout=60)
                subprocess.run([str(ndk / "ld.lld.exe"), "-flavor", "link", "/entry:main", "/subsystem:console",
                                "/nodefaultlib", "/out:" + str(exe), str(obj)], check=True, timeout=60)
            elif REQUIRE:
                self.fail("A host C compiler is required")
            else:
                self.skipTest("No host C compiler")
            return subprocess.run([str(exe)], timeout=10).returncode

    def test_actual_provider_clock_lock_errors_and_log_bound(self):
        self.assertEqual(self.run_contract(), 0)

    def test_snapshot_before_clock_enable_is_rejected(self):
        def mutation(body, snapshot):
            anchor = "\t\tif (!was_powered) {"
            self.assertEqual(body.count(anchor), 1)
            return body.replace(anchor, '\t\tzx279133_uni_serdes_rate_snapshot(priv, "unsafe", 0);\n' + anchor), snapshot
        self.assertNotEqual(self.run_contract(mutation), 0)

    def test_snapshot_after_clock_release_is_rejected(self):
        def mutation(body, snapshot):
            anchor = "out_unlock:\n"
            self.assertEqual(body.count(anchor), 1)
            return body.replace(anchor, anchor + '\tzx279133_uni_serdes_rate_snapshot(priv, "unsafe", ret);\n'), snapshot
        self.assertNotEqual(self.run_contract(mutation), 0)

    def test_diagnostic_register_write_is_rejected(self):
        self.assertNotEqual(self.run_contract(lambda body, snapshot: (
            body, snapshot.replace("\tlockdep_assert_held(&priv->lock);", "\tlockdep_assert_held(&priv->lock);\n\twritel(0, priv->serdes);"))), 0)

    def test_diagnostic_reset_pulse_is_rejected(self):
        self.assertNotEqual(self.run_contract(lambda body, snapshot: (
            body, snapshot.replace("\tlockdep_assert_held(&priv->lock);", "\tlockdep_assert_held(&priv->lock);\n\tzx279133_uni_serdes_reset_pulse(priv);"))), 0)

    def test_snapshot_without_provider_mutex_is_rejected(self):
        self.assertNotEqual(self.run_contract(lambda body, snapshot: (
            body, snapshot.replace("\tlockdep_assert_held(&priv->lock);", "\tlockdep_assert_held(&priv->lock);\n\tmutex_unlock(&priv->lock);"))), 0)

    def test_unbounded_retry_logging_is_rejected(self):
        self.assertNotEqual(self.run_contract(lambda body, snapshot: (
            body.replace("priv->rate_diag_count < ZX279133_UNI_RATE_DIAG_LIMIT", "true"), snapshot)), 0)

    def test_ci_requires_compiled_diagnostics(self):
        workflow = (ROOT / ".github/workflows/build-sk-d840n.yml").read_text(encoding="utf-8")
        self.assertIn("python3 tests/test_sk_serdes_diag.py --require-compiler", workflow)


if __name__ == "__main__":
    unittest.main(verbosity=2)
