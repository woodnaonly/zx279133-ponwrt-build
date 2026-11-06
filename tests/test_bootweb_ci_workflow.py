"""The bootweb job must stay independent and fail closed at publication."""
import ast
from pathlib import Path
import re
from types import SimpleNamespace
import unittest

import yaml

ROOT = Path(__file__).resolve().parents[1]


def condition(expression, event, mode):
    expression = expression.replace("||", "or").replace("&&", "and")
    tree = ast.parse(expression, mode="eval")
    allowed = (ast.Expression, ast.BoolOp, ast.Or, ast.And, ast.Compare, ast.Eq, ast.NotEq,
               ast.Attribute, ast.Name, ast.Load, ast.Constant)
    if any(not isinstance(node, allowed) for node in ast.walk(tree)):
        raise ValueError("unexpected workflow condition")
    return eval(compile(tree, "<workflow-condition>", "eval"), {"__builtins__": {}},
                {"github": SimpleNamespace(event_name=event), "inputs": SimpleNamespace(mode=mode)})


class BootwebWorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = (ROOT / ".github/workflows/build-sk-d840n.yml").read_text(encoding="utf-8")
        # BaseLoader deliberately keeps YAML 1.1's `on` and `true` as strings.
        cls.doc = yaml.load(cls.text, Loader=yaml.BaseLoader)
        cls.jobs = cls.doc["jobs"]
        cls.job = cls.jobs["bootweb"]

    def test_modes_and_pushes_select_the_correct_independent_jobs(self):
        mode_input = self.doc["on"]["workflow_dispatch"]["inputs"]["mode"]
        self.assertEqual(mode_input["default"], "all")
        self.assertEqual(set(mode_input["options"]), {"all", "both", "initramfs", "flash", "bootweb"})
        for event, mode, firmware, bootweb in (("push", "", True, True),
                ("workflow_dispatch", "all", True, True), ("workflow_dispatch", "both", True, False),
                ("workflow_dispatch", "initramfs", True, False), ("workflow_dispatch", "flash", True, False),
                ("workflow_dispatch", "bootweb", False, True)):
            with self.subTest(event=event, mode=mode):
                self.assertEqual(condition(self.job["if"], event, mode), bootweb)
                for job in ("check-led-gates", "build"):
                    self.assertEqual(condition(self.jobs[job]["if"], event, mode), firmware)
        self.assertNotIn("needs", self.job)
        self.assertEqual(self.jobs["build"]["needs"], "check-led-gates")

    def test_legacy_firmware_steps_remain_in_the_firmware_job(self):
        steps = self.jobs["build"]["steps"]
        self.assertGreater(len(steps), 30)
        names = {s.get("name") for s in steps}
        self.assertTrue({"Compile", "Package flash images", "Package initramfs image", "Publish release",
                         "Direct firmware download summary"} <= names)
        pick = next(s["run"] for s in steps if s.get("id") == "pick")
        self.assertIn("want=all", pick)
        self.assertIn('[ "$want" = all ] || [ "$want" = both ]', pick)
        self.assertEqual(self.jobs["build"]["strategy"]["matrix"]["mode"], ["initramfs", "flash"])

    def test_bootweb_and_its_workflow_tests_trigger_push_builds(self):
        paths = self.doc["on"]["push"]["paths"]
        self.assertIn("bootweb/**", paths)
        self.assertIn("tests/test_bootweb_ci_workflow.py", paths)

    def test_runner_versions_and_dependency_cache_are_explicit(self):
        self.assertEqual(self.job["runs-on"], "windows-2022")
        self.assertEqual(self.job["defaults"]["run"]["shell"], "pwsh")
        steps = self.job["steps"]
        self.assertEqual(next(s for s in steps if s.get("uses", "").startswith("actions/setup-python@"))["with"]["python-version"], "3.13")
        self.assertEqual(next(s for s in steps if s.get("uses", "").startswith("actions/setup-node@"))["with"]["node-version"], "22")
        commands = "\n".join(s.get("run", "") for s in steps)
        self.assertIn("capstone==5.0.7", commands)
        cache = next(s for s in steps if s.get("uses", "").startswith("actions/cache@"))["with"]
        self.assertIn("dependencies.lock.json", cache["key"])
        self.assertNotIn("ci-native241", cache["path"])

    def test_publication_requires_a_validated_successful_package(self):
        steps = self.job["steps"]
        gate_index = next(i for i, s in enumerate(steps) if s.get("id") == "package_bootweb")
        gate = steps[gate_index]["run"]
        self.assertLess(gate.index("ci.py build"), gate.index("ci.py validate-dist"))
        self.assertLess(gate.index("ci.py validate-dist"), gate.index("validated=true"))
        self.assertEqual(gate.count("if ($LASTEXITCODE -ne 0)"), 2)
        outputs = [s for s in steps if s.get("name") in
                   ("Upload validated bootweb outputs", "Publish bootweb candidate release", "Bootweb download summary")]
        self.assertEqual(len(outputs), 3)
        for step in outputs:
            self.assertGreater(steps.index(step), gate_index)
            expected = "success() && steps.package_bootweb.outputs.validated == 'true'"
            if step["name"] != "Upload validated bootweb outputs":
                expected += " && (github.event_name != 'workflow_dispatch' || inputs.publish_release != false)"
            self.assertEqual(step["if"], expected)
        release = next(s for s in outputs if "action-gh-release" in s.get("uses", ""))["with"]
        self.assertEqual(release["prerelease"], "true")
        self.assertEqual(release["fail_on_unmatched_files"], "true")
        self.assertIn("skd840n_bootweb_", release["tag_name"])
        self.assertEqual(len(release["files"].strip().splitlines()), 3)
        self.assertNotIn("*", release["files"])
        self.assertNotIn("resident-boot-development.bin", release["files"])

    def test_failure_upload_is_reports_only_and_no_device_operation_runs(self):
        diagnostic = next(s for s in self.job["steps"] if s.get("name") == "Upload bootweb failure diagnostics")
        self.assertEqual(diagnostic["if"], "failure()")
        self.assertEqual(diagnostic["with"]["path"], "bootweb/.build/ci-native241/reports/")
        commands = "\n".join(s.get("run", "") for s in self.job["steps"])
        self.assertIsNone(re.search(r"installer_trial|ram_web|resident_live|--current-backup|--probe-report|--user-directed", commands))


if __name__ == "__main__":
    unittest.main()
