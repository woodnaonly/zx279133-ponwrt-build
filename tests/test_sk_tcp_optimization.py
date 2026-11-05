#!/usr/bin/env python3
"""TX source/CI contracts plus compiled driver-function tests with an skb model."""
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

import yaml

ROOT = Path(__file__).resolve().parents[1]
DRIVER = ROOT / "kernel-overlays/drivers/net/ethernet/zte"
TEXT = (DRIVER / "zx279133-netdev.c").read_text(encoding="utf-8")
HEADER = (DRIVER / "zx279133.h").read_text(encoding="utf-8")
REQUIRE = "--require-compiler" in sys.argv
if REQUIRE:
    sys.argv.remove("--require-compiler")


def function(name):
    matches = list(re.finditer(r"(?:static\s+(?:enum\s+\w+|int)\s+)" + name + r"\([^;]*?\)\s*\{", TEXT))
    if len(matches) != 1:
        raise AssertionError("Expected exactly one definition: " + name)
    start = matches[0].start()
    end = matches[0].end()
    depth = 1
    while depth:
        if TEXT[end] == "{":
            depth += 1
        elif TEXT[end] == "}":
            depth -= 1
        end += 1
    return TEXT[start:end]


class TcpTests(unittest.TestCase):
    def test_exactly_one_private_tag_and_no_manual_checksum_adjustment(self):
        body = function("zx279133_lan_strip_transport_vlan")
        code = re.sub(r"/\*.*?\*/", "", body, flags=re.S)
        self.assertEqual(code.count("__skb_vlan_pop(skb, &tci)"), 1)
        self.assertNotIn("skb_vlan_pop(skb)", code)
        self.assertNotIn("memmove", code)
        self.assertNotIn("skb->csum", code)
        self.assertNotIn("skb_linearize", code)
        self.assertIn("return err;", code)
        self.assertIn("vid > ZX279133_LAN_TRANSPORT_VID_MAX", code)

    def test_busy_return_precedes_any_tag_mutation_and_errors_drop(self):
        start = TEXT.index("static netdev_tx_t zx279133_start_xmit_common")
        end = TEXT.index("static netdev_tx_t zx279133_start_xmit(", start)
        common = TEXT[start:end]
        self.assertLess(common.index("return NETDEV_TX_BUSY"), common.index("zx279133_lan_strip_transport_vlan(skb"))
        self.assertLess(common.index("zx279133_lan_strip_transport_vlan(skb"), common.index("skb_linearize(skb)"))
        self.assertIn("atomic64_inc(&eth->tx_vlan_pop_errors);\n\t\t\tgoto drop;", common)
        wrapper = TEXT[TEXT.index("zx279133_lan_netdev_xmit("):]
        self.assertNotIn("zx279133_lan_strip_transport_vlan(skb", wrapper)

    def test_fallback_counters_are_appended_without_losing_existing_names(self):
        enum = re.search(r"enum zx279133_tx_csum_fallback\s*\{(.*?)\};", HEADER, re.S)[1]
        count = len(re.findall(r"\bZX279133_TX_CSUM_\w+", enum)) - 2
        names = re.search(r"zx279133_tx_csum_names\[\].*?=\s*\{(.*?)\};", TEXT, re.S)[1]
        self.assertEqual(len(re.findall(r'"tx_csum_fb_', names)), count)
        self.assertIn('"tx_hw_csum_packets"', TEXT)
        self.assertIn('"tx_sw_csum_packets"', TEXT)
        self.assertLess(TEXT.index('ethtool_puts(&data, "rx_hash_loads")'), TEXT.index("memcpy(data, zx279133_tx_csum_names"))

    def test_publish_switch_leaves_artifacts_available(self):
        doc = yaml.load((ROOT / ".github/workflows/build-sk-d840n.yml").read_text(encoding="utf-8"), Loader=yaml.BaseLoader)
        switch = doc["on"]["workflow_dispatch"]["inputs"]["publish_release"]
        self.assertEqual(switch["type"], "boolean")
        self.assertEqual(switch["default"], "true")
        for job in doc["jobs"].values():
            for step in job.get("steps", []):
                if "action-gh-release" in step.get("uses", ""):
                    self.assertIn("inputs.publish_release != false", step["if"])
                if "upload-artifact" in step.get("uses", ""):
                    self.assertNotIn("inputs.publish_release", step["if"])

    def test_compiled_driver_functions(self):
        enum = re.search(r"enum zx279133_tx_csum_fallback\s*\{.*?\};", HEADER, re.S)[0]
        source = (ROOT / "tests/sk_tcp_harness.h").read_text(encoding="utf-8").replace("/* DRIVER_FUNCTIONS */", enum + "\n" + function("zx279133_tx_csum_reason") + "\n" + function("zx279133_lan_strip_transport_vlan"))
        compiler = shutil.which("cc") or shutil.which("gcc")
        ndk = Path("C:/Dev/Tools/Android/SDK/ndk/android-ndk-r27d/toolchains/llvm/prebuilt/windows-x86_64/bin")
        with tempfile.TemporaryDirectory(prefix="sk-tcp-test-") as tmp:
            directory = Path(tmp)
            src = directory / "test.c"
            exe = directory / "test.exe"
            src.write_text(source, encoding="utf-8")
            if compiler:
                subprocess.run([compiler, "-std=c11", "-Wall", "-Wextra", "-Werror", "-Wno-unused-function", "-fno-builtin", str(src), "-o", str(exe)], check=True)
            elif (ndk / "clang.exe").is_file():
                obj = directory / "test.obj"
                subprocess.run([str(ndk / "clang.exe"), "--target=x86_64-pc-windows-msvc", "-ffreestanding", "-fno-builtin", "-fno-stack-protector", "-std=c11", "-Wall", "-Wextra", "-Werror", "-Wno-unused-function", "-c", str(src), "-o", str(obj)], check=True)
                subprocess.run([str(ndk / "ld.lld.exe"), "-flavor", "link", "/entry:main", "/subsystem:console", "/nodefaultlib", "/out:" + str(exe), str(obj)], check=True)
            elif REQUIRE:
                self.fail("A host C compiler is required")
            else:
                self.skipTest("No host C compiler")
            result = subprocess.run([str(exe)], timeout=10)
            self.assertEqual(result.returncode, 0, "Compiled skb-model contract failed (exit code is generated source line)")


if __name__ == "__main__":
    unittest.main(verbosity=2)
