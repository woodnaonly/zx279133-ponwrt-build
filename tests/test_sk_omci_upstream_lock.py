#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""The control on the gate that holds the vendored OMCI engine in place.

A gate that says nothing is a gate that cannot fail, and a gate that cannot
fail is worse than no gate at all: it is a green light that means nothing.

So every check in tools/check-omci-upstream-lock.py is broken here, one at
a time, and the gate has to notice each of them.  The breakings are made on
a copy of the tree, and the gate is pointed at that copy - a control that
handed the gate a tree it had built itself would only prove that the
control and the gate agree about a tree the control made up.

The gate has to be offline: it runs in the gates job of both build lines,
and the answer it gives has to be the same every time.  That is checked
here on the gate's own source, and the check itself is checked by breaking
the source and seeing that the checker notices.
"""
import argparse
import contextlib
import importlib.util
import io
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

try:
    import yaml
except ImportError:                                    # pragma: no cover
    print("python3-yaml is not installed", file=sys.stderr)
    sys.exit(2)

DEFAULT_ROOT = Path(__file__).resolve().parents[1]

PACKAGE = "overlays/package/libs/libzxpon-omci/omci"
LOCK = PACKAGE + "/UPSTREAM.lock.json"
UPSTREAM = PACKAGE + "/UPSTREAM"
GATE = "tools/check-omci-upstream-lock.py"
REFRESH = "tools/refresh-omci-upstream.py"
CONTROL = "tests/test_sk_omci_upstream_lock.py"

WORKFLOWS = ("build-sk-d840n.yml", "build-sk-d840n-73.yml")

# The ways a program reaches out to the network.  The gate that holds the
# vendored copy in place has to work on a machine that cannot: a gate that
# fetches something is a gate whose answer depends on the day.
NETWORK_MARKERS = ("import urllib", "import requests", "import socket",
                    "import http", "urlopen(", "HTTPConnection", "httpx",
                    "curl ", "wget ")


def tree_and_args(argv):
    """The tree the control works on, and the arguments left for unittest.

    The gate takes --tree, and the control that watches the gate takes it
    too: a control that could only ever look at its own tree could not
    watch a gate that was pointed at another one.  This control was
    called with --tree by the workflow and did not take it, so the build
    died on the option instead of on the tree.
    """
    parser = argparse.ArgumentParser(description="the control on the OMCI lock gate")
    parser.add_argument("--tree", type=Path, default=None,
                        help="the repository the gate is checked against")
    args, rest = parser.parse_known_args(argv)
    root = args.tree.resolve() if args.tree else DEFAULT_ROOT
    return root, rest


def load_gate(tool):
    spec = importlib.util.spec_from_file_location("check_omci_upstream_lock", tool)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def network_calls(text):
    """Every way the text reaches out to the network."""
    return [marker for marker in NETWORK_MARKERS if marker in text]


def push_paths(text):
    """The paths the workflow's push filter names."""
    document = yaml.safe_load(text)
    node = document.get("on", document.get(True)) or {}
    push = node.get("push") or {}
    return [str(entry) for entry in (push.get("paths") or [])]


class UpstreamLockControl(unittest.TestCase):
    root = DEFAULT_ROOT

    @classmethod
    def setUpClass(cls):
        cls.gate = load_gate(cls.root / GATE)
        cls.gate_source = (cls.root / GATE).read_text(encoding="utf-8")
        cls.workflows = {name: (cls.root / ".github" / "workflows" / name).read_text(encoding="utf-8")
                         for name in WORKFLOWS}

    def fresh_tree(self):
        """A copy of the package, so a breaking cannot touch the repository."""
        base = Path(tempfile.mkdtemp(prefix="sk-omci-lock-"))
        self.addCleanup(shutil.rmtree, base, True)
        shutil.copytree(self.root / PACKAGE, base / PACKAGE)
        return base

    def run_gate(self, root):
        with contextlib.redirect_stdout(io.StringIO()):
            report = self.gate.analyse(root)
        return report.failures

    def write_lock(self, tree, lock):
        (tree / LOCK).write_text(json.dumps(lock, indent=2, ensure_ascii=False) + "\n",
                                 encoding="utf-8", newline="\n")

    def read_lock(self, tree):
        return json.loads((tree / LOCK).read_text(encoding="utf-8"))

    def test_the_repository_passes_its_own_gate(self):
        self.assertEqual(self.run_gate(self.root), [])

    def test_a_vendored_file_that_changed_is_noticed(self):
        tree = self.fresh_tree()
        target = tree / PACKAGE / "src" / "wire.rs"
        target.write_bytes(target.read_bytes() + b"\n// touched by the control\n")
        failures = self.run_gate(tree)
        self.assertIn("the vendored src/wire.rs is the bytes the lock names", failures)
        self.assertIn("the vendored src/wire.rs is the blob the lock names", failures)

    def test_a_file_dropped_from_the_lock_is_noticed(self):
        tree = self.fresh_tree()
        lock = self.read_lock(tree)
        lock["files"] = [entry for entry in lock["files"]
                         if entry.get("path") != "src/mib.rs"]
        self.write_lock(tree, lock)
        failures = self.run_gate(tree)
        self.assertIn("every vendored source is named in the lock file", failures)

    def test_a_file_dropped_from_the_tree_is_noticed(self):
        tree = self.fresh_tree()
        (tree / PACKAGE / "src" / "schema.rs").unlink()
        failures = self.run_gate(tree)
        self.assertIn("every source the lock file names is in the tree", failures)

    def test_a_file_added_to_the_tree_without_the_lock_is_noticed(self):
        tree = self.fresh_tree()
        (tree / PACKAGE / "src" / "extra.rs").write_text("// not in the lock\n",
                                                        encoding="utf-8")
        failures = self.run_gate(tree)
        self.assertIn("every vendored source is named in the lock file", failures)

    def test_the_upstream_note_and_the_lock_disagreeing_is_noticed(self):
        tree = self.fresh_tree()
        note = tree / UPSTREAM
        text = note.read_text(encoding="utf-8")
        self.assertIn("airoha-pon-daemons/src/src/omci/schema.rs", text)
        note.write_text(text.replace("airoha-pon-daemons/src/src/omci/schema.rs",
                                     "airoha-pon-daemons/src/src/omci/other.rs"),
                        encoding="utf-8", newline="\n")
        failures = self.run_gate(tree)
        self.assertIn("the lock file and the UPSTREAM note take the same files", failures)

    def test_an_upstream_part_dropped_without_a_reason_is_noticed(self):
        tree = self.fresh_tree()
        lock = self.read_lock(tree)
        lock["not_taken"] = [entry for entry in lock["not_taken"]
                             if entry.get("id") != "daemon-runtime"]
        self.write_lock(tree, lock)
        failures = self.run_gate(tree)
        self.assertIn("the lock file still says why daemon-runtime was not taken", failures)

    def test_a_change_nobody_explained_is_noticed(self):
        tree = self.fresh_tree()
        lock = self.read_lock(tree)
        lock["files"][0]["local_changes"] = "x"
        self.write_lock(tree, lock)
        failures = self.run_gate(tree)
        self.assertIn("the lock file explains the change in src/mib.rs", failures)

    def test_a_drift_against_the_upstream_is_noticed(self):
        tree = self.fresh_tree()
        lock = self.read_lock(tree)
        lock["drift"] = [{"path": "src/mib.rs", "pinned_blob": "a" * 40,
                          "current_blob": "b" * 40}]
        self.write_lock(tree, lock)
        failures = self.run_gate(tree)
        self.assertIn("the upstream has not drifted from the pin", failures)

    def test_a_token_in_the_lock_is_noticed(self):
        tree = self.fresh_tree()
        lock = self.read_lock(tree)
        lock["repository"] = "https://github.com/pbs05/openwrt-pon-userspace?token=ghp_0123456789abcdef"
        self.write_lock(tree, lock)
        failures = self.run_gate(tree)
        self.assertIn("the lock file carries no token", failures)

    def test_the_gate_reaching_out_to_the_network_is_noticed(self):
        self.assertEqual(network_calls(self.gate_source), [],
                         "the gate fetches something")
        broken = self.gate_source + "\nimport urllib.request\n"
        self.assertNotEqual(network_calls(broken), [],
                            "the network check does not notice a network call")

    def test_both_lines_call_the_gate_and_the_control(self):
        for name, text in sorted(self.workflows.items()):
            self.assertIn("python3 " + GATE + " --tree", text, name)
            self.assertIn("python3 " + CONTROL + " --tree", text, name)

    def test_both_lines_register_the_new_files_in_their_paths(self):
        for name, text in sorted(self.workflows.items()):
            paths = push_paths(text)
            for entry in (GATE, REFRESH, CONTROL):
                self.assertIn(entry, paths, f"{name} does not register {entry}")


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    root, rest = tree_and_args(argv)
    UpstreamLockControl.root = root
    unittest.main(argv=[sys.argv[0]] + rest)


if __name__ == "__main__":
    main()
