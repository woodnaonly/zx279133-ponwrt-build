#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""The control on the gate that says which line publishes.

A gate that says nothing is a gate that cannot fail, and a gate that
cannot fail is worse than no gate at all: it is a green light that means
nothing.

So every check in tools/check-sk-release-line.py is broken here, one at a
time, and the gate has to notice each of them.  The breakings are made on
the workflow text and the tree is parsed again afterwards, the same way the
gate parses it: a control that handed the gate a tree it had built itself
would only prove that the control and the gate agree about a tree the
control made up.
"""
import contextlib
import importlib.util
import io
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / "tools/check-sk-release-line.py"
FIXTURE = ROOT / "tests/fixtures/sk-73-artifact-files.txt"

LEGACY = "build-sk-d840n.yml"
PUBLISH = "build-sk-d840n-73.yml"

DOLLAR = "$"
MODE = DOLLAR + "{{ matrix.mode }}"

# The publish switch of the 7.3 line, as it is written in the workflow.
PUBLISH_INPUT = (
    "      publish_release:\n"
    "        description: '发布 Release（RAM 对照测试请关闭）'\n"
    "        required: false\n"
    "        default: true\n"
    "        type: boolean\n"
)

# A publish step of the 6.18 kind, put back into the 6.18 line.
PUBLISH_STEP = (
    "      - name: Publish release\n"
    "        uses: softprops/action-gh-release@v2\n"
)


def load_gate():
    spec = importlib.util.spec_from_file_location("check_sk_release_line", TOOL)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class ReleaseLineControl(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.gate = load_gate()
        cls.fixture = FIXTURE.read_text(encoding="utf-8")
        base = ROOT / ".github" / "workflows"
        cls.texts = {path.name: path.read_text(encoding="utf-8")
                     for path in sorted(base.glob("*.yml"))}

    def run_gate(self, texts):
        with contextlib.redirect_stdout(io.StringIO()):
            report = self.gate.analyse(texts, self.fixture)
        return report.failures

    def mutate(self, name, old, new):
        texts = dict(self.texts)
        self.assertIn(old, texts[name], f"the text to break is not in {name}")
        texts[name] = texts[name].replace(old, new, 1)
        self.assertNotEqual(texts[name], self.texts[name], "the breaking did not happen")
        return texts

    def test_the_repository_passes_its_own_gate(self):
        self.assertEqual(self.run_gate(self.texts), [])

    def test_a_publish_step_in_the_6_18_line_is_noticed(self):
        texts = dict(self.texts)
        self.assertIn("\n  bootweb:\n", texts[LEGACY])
        texts[LEGACY] = texts[LEGACY].replace("\n  bootweb:\n",
                                              "\n" + PUBLISH_STEP + "\n  bootweb:\n", 1)
        failures = self.run_gate(texts)
        self.assertIn("the 6.18 line publishes nothing", failures)
        self.assertIn("the 7.3 line is the only line that publishes", failures)

    def test_the_7_3_tag_without_the_line_in_it_is_noticed(self):
        texts = self.mutate(PUBLISH, "tag_name: skd840n_73_", "tag_name: skd840n_")
        self.assertIn("the 7.3 release tag says which line built it",
                      self.run_gate(texts))

    def test_a_6_18_only_file_in_the_7_3_release_is_noticed(self):
        texts = self.mutate(PUBLISH,
                            "            images/sysupgrade-report.json\n",
                            "            images/sysupgrade-report.json\n"
                            "            images/kernel-provenance.json\n")
        failures = self.run_gate(texts)
        self.assertIn("the 7.3 release does not ship a file only the 6.18 line builds",
                      failures)
        self.assertIn("the 7.3 release ships only files the 7.3 build produced",
                      failures)

    def test_the_7_3_line_without_the_publish_switch_is_noticed(self):
        texts = self.mutate(PUBLISH, PUBLISH_INPUT, "")
        self.assertIn("the 7.3 line offers the publish switch",
                      self.run_gate(texts))

    def test_the_7_3_body_naming_the_other_kernel_is_noticed(self):
        # Every mention of the kernel version goes, not only the one in the
        # title: the body names the kernel twice, and a breaking that leaves
        # the second mention standing would not break what the gate reads.
        texts = dict(self.texts)
        self.assertIn("7.3.4", texts[PUBLISH])
        texts[PUBLISH] = texts[PUBLISH].replace("7.3.4", "6.18.60")
        self.assertNotEqual(texts[PUBLISH], self.texts[PUBLISH], "the breaking did not happen")
        failures = self.run_gate(texts)
        self.assertIn("the 7.3 release body names the kernel it ships", failures)
        self.assertIn("the 7.3 release body does not name the other line's kernel",
                      failures)

    def test_the_6_18_line_that_stopped_uploading_is_noticed(self):
        texts = self.mutate(LEGACY, "name: sk-d840n-" + MODE, "name: sk-d840n-nothing")
        self.assertIn("the 6.18 line still uploads its firmware",
                      self.run_gate(texts))


if __name__ == "__main__":
    unittest.main()
