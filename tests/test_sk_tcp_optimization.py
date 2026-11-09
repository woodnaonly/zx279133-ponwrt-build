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
MAIN = (DRIVER / "zx279133-main.c").read_text(encoding="utf-8")
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


def block(text, anchor):
    start = text.index(anchor)
    end = text.index("{", start) + 1
    depth = 1
    while depth:
        if text[end] == "{":
            depth += 1
        elif text[end] == "}":
            depth -= 1
        end += 1
    return text[start:end]


class TcpTests(unittest.TestCase):
    def test_shipping_checksum_default_is_disabled(self):
        self.assertRegex(HEADER, r"(?m)^#define\s+zx279133_tx_hw_csum\s+0\s*$")
        enabled = block(MAIN, "if (zx279133_tx_hw_csum)")
        self.assertEqual(MAIN.count("NETIF_F_HW_CSUM"), 3)
        for field in ("features", "hw_features", "vlan_features"):
            self.assertIn(f"ndev->{field} |= NETIF_F_HW_CSUM;", enabled)
            self.assertEqual(TEXT.count(f"ndev->{field} = eth->ndev->{field};"), 1)
        self.assertNotIn("NETIF_F_HW_CSUM", TEXT)

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

    def test_disabled_publication_keeps_validated_firmware_downloadable(self):
        doc = yaml.load((ROOT / ".github/workflows/build-sk-d840n.yml").read_text(encoding="utf-8"), Loader=yaml.BaseLoader)
        steps = doc["jobs"]["build"]["steps"]
        for event in ("push", "workflow_dispatch"):
            for publish in (False, True):
                for mode in ("flash", "initramfs"):
                    for step in steps:
                        if step.get("name") not in ("Upload outputs", "Publish release"):
                            continue
                        expr = step["if"].replace("success()", "True")
                        substitutions = {"github.event_name": event, "github.ref": "refs/heads/main",
                                         "inputs.publish_release": publish, "matrix.mode": mode,
                                         "steps.pick.outputs.run": "true",
                                         "steps.package_flash.outputs.validated": "true",
                                         "steps.package_initramfs.outputs.validated": "true"}
                        for name, value in substitutions.items():
                            expr = expr.replace(name, repr(value))
                        expr = expr.replace("&&", " and ").replace("||", " or ").replace("!= false", "!= False").replace("!startsWith(", "not startsWith(")
                        actual = eval(expr, {"__builtins__": {}, "startsWith": str.startswith})
                        expected = step["name"] == "Upload outputs" or event == "push" or publish
                        self.assertEqual(actual, expected, (event, publish, mode, step["name"]))

    def harness(self):
        enum = re.search(r"enum zx279133_tx_csum_fallback\s*\{.*?\};", HEADER, re.S)[0]
        return (ROOT / "tests/sk_tcp_harness.h").read_text(encoding="utf-8").replace("/* DRIVER_FUNCTIONS */", enum + "\n" + function("zx279133_tx_csum_reason") + "\n" + function("zx279133_lan_strip_transport_vlan"))

    def test_compiled_driver_functions(self):
        # An explicitly enabled model retains the old predicate/tag coverage;
        # it does not assert that real IDM checksum generation is correct.
        self.compile_harness(self.harness())

    def test_compiled_disabled_features_and_software_fallback(self):
        source = self.harness().split("int main(void)", 1)[0]
        source = source.replace("#define zx279133_tx_hw_csum 1", "#define zx279133_tx_hw_csum 0")
        feature_init = block(MAIN, "if (zx279133_tx_hw_csum)")
        inheritance = "\n".join(f"ndev->{field} = eth->ndev->{field};"
                                 for field in ("features", "hw_features", "vlan_features"))
        for line in inheritance.splitlines():
            self.assertIn(line, TEXT)
        fallback = block(TEXT, "if (skb->ip_summed == CHECKSUM_PARTIAL)")
        source += r'''
#define NETIF_F_HW_CSUM 1
struct net_device { unsigned long features, hw_features, vlan_features; };
struct zx279133_eth { struct net_device *ndev; unsigned int tx_csum_help_errors; };
static void init_features(struct net_device *ndev) { /* INIT_FEATURES */ }
static void inherit_features(struct net_device *ndev, struct zx279133_eth *eth) {
    /* INHERIT_FEATURES */
}
static unsigned int help_calls;
static bool help_fail;
static int skb_checksum_help(struct sk_buff *skb) {
    help_calls++;
    if (help_fail) return -EINVAL;
    skb->ip_summed = 0;
    return 0;
}
static void atomic64_inc(unsigned int *value) { ++*value; }
static int checksum_path(struct sk_buff *skb, struct zx279133_eth *eth) {
    bool hw_csum = false, sw_csum = false;
    enum zx279133_tx_csum_fallback csum_reason = ZX279133_TX_CSUM_OK;
    /* CHECKSUM_PATH */
    return hw_csum ? 1 : sw_csum ? 2 : 0;
drop:
    return -1;
}
int main(void) {
    struct net_device parent, lan;
    struct zx279133_eth eth;
    struct sk_buff *s = &packet;
    zero(&parent, sizeof(parent)); zero(&lan, sizeof(lan)); zero(&eth, sizeof(eth));
    parent.features = parent.hw_features = parent.vlan_features = 2;
    eth.ndev = &parent;
    init_features(&parent);
    inherit_features(&lan, &eth);
    CHECK(parent.features == 2 && parent.hw_features == 2 && parent.vlan_features == 2);
    CHECK(lan.features == 2 && lan.hw_features == 2 && lan.vlan_features == 2);
    for (unsigned int vid = 59; vid <= 63; vid++) {
        for (unsigned int customer = 0; customer <= 1; customer++) {
            u8 port;
            init(5, vid, customer);
            CHECK(zx279133_lan_strip_transport_vlan(s, &port) == 1);
            CHECK(zx279133_tx_csum_reason(s) == ZX279133_TX_CSUM_DISABLED);
            CHECK(checksum_path(s, &eth) == 2 && s->ip_summed == 0);
        }
    }
    CHECK(help_calls == 10 && eth.tx_csum_help_errors == 0);
    init(5, 59, false); s->protocol = htons(ETH_P_IPV6);
    CHECK(checksum_path(s, &eth) == 2);
    init(5, 59, false); s->ip_summed = 0;
    CHECK(checksum_path(s, &eth) == 0 && help_calls == 11);
    init(5, 59, false); help_fail = true;
    CHECK(checksum_path(s, &eth) == -1 && help_calls == 12 && eth.tx_csum_help_errors == 1);
    return failed;
}
'''
        source = source.replace("/* INIT_FEATURES */", feature_init)
        source = source.replace("/* INHERIT_FEATURES */", inheritance)
        source = source.replace("/* CHECKSUM_PATH */", fallback)
        self.compile_harness(source)

    def compile_harness(self, source):
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
