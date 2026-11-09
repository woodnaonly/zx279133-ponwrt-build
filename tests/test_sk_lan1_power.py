#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Compile actual LAN1 consumer functions against the pinned generic PHY core.

Provider/PCS/PM operations are mocked. This proves reference ownership and
error paths, not PHY register programming, throughput, or board acceptance.
"""
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
TEXT = (ROOT / "kernel-overlays/drivers/net/ethernet/zte/zx279133-switch.c").read_text(encoding="utf-8")
REQUIRE = "--require-compiler" in sys.argv
if REQUIRE:
    sys.argv.remove("--require-compiler")


def function(name):
    matches = list(re.finditer(r"static\s+(?:int|void)\s+" + name + r"\([^;]*?\)\s*\{", TEXT))
    if len(matches) != 1:
        raise AssertionError("Expected one driver function: " + name)
    start, end, depth = matches[0].start(), matches[0].end(), 1
    while depth:
        depth += (TEXT[end] == "{") - (TEXT[end] == "}")
        end += 1
    return TEXT[start:end]


OWNED_OFF = """\tif (priv->serdes_powered) {
\t\tret = phy_power_off(priv->serdes);
\t\tif (ret)
\t\t\treturn ret;
\t\tpriv->serdes_powered = false;
\t}"""


class Lan1PowerTests(unittest.TestCase):
    def run_contract(self, mutation=None, expected_success=True):
        consumer = function("zx279133_switch_outerphy_apply_speed_locked")
        self.assertEqual(consumer.count(OWNED_OFF), 1)
        if mutation:
            consumer = consumer.replace(OWNED_OFF, mutation)
        driver = "\n".join((consumer, function("zx279133_switch_outerphy_apply_speed"),
                              function("zx279133_switch_release")))
        source = (ROOT / "tests/sk_lan1_power_harness.h").read_text(encoding="utf-8")
        source = source.replace("/* PHY_CORE_FUNCTIONS */", (ROOT / "tests/fixtures/phy-core-power.inc").read_text(encoding="utf-8"))
        source = source.replace("/* DRIVER_FUNCTIONS */", driver)
        compiler = shutil.which("cc") or shutil.which("gcc")
        ndk = Path("C:/Dev/Tools/Android/SDK/ndk/android-ndk-r27d/toolchains/llvm/prebuilt/windows-x86_64/bin")
        with tempfile.TemporaryDirectory(prefix="sk-lan1-power-") as temporary:
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
            result = subprocess.run([str(exe)], timeout=10)
            if expected_success:
                self.assertEqual(result.returncode, 0, "PHY ownership contract failed at generated C line " + str(result.returncode))
            else:
                self.assertNotEqual(result.returncode, 0, "The regression mutant unexpectedly passed")

    def test_actual_driver_power_lifecycle_and_failures(self):
        self.run_contract()

    def test_legacy_unconditional_initial_off_is_rejected(self):
        self.run_contract("\tphy_power_off(priv->serdes);", expected_success=False)

    def test_ignored_power_off_failure_is_rejected(self):
        self.run_contract(OWNED_OFF.replace("\t\tif (ret)\n\t\t\treturn ret;\n", ""), expected_success=False)

    def test_stale_ownership_after_mode_failure_is_rejected(self):
        self.run_contract(OWNED_OFF.replace("\t\tpriv->serdes_powered = false;\n", ""), expected_success=False)


if __name__ == "__main__":
    unittest.main(verbosity=2)
