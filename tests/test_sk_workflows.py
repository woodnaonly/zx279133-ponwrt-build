#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Check that the workflows themselves are sound before a build is spent on them.

A workflow file is parsed by GitHub before any job starts.  When that parse
fails, the run is created, it is reported as failed, and it has no jobs at all
- so the only evidence is the run's conclusion.  That is exactly what happened
to the 7.3 line in run #5 (2026-11-22, head 94335616): the step

    - name: Gate: the resolved config carries exactly one theme

carried a colon and a space in an unquoted YAML scalar, which made the whole
file unparseable.  The build never started, and the CI slot was spent.

So the parse is checked here, on the machine that pushes the repository, in
the gate job that runs before every build.  The checks below are about the
shape of the two kernel lines:

  * every workflow file parses,
  * every job and step the lines depend on is still there,
  * every test file a gate job runs exists in tests/,
  * the 7.3 line still rebuilds when the inputs of that line change.

The last one matters as much as the parse: the 7.3 line is driven by a path
filter, so a patch series that lands outside the filter builds nothing and the
green run says nothing about it.
"""
import re
import sys
from pathlib import Path

try:
    import yaml
except ImportError:                                    # pragma: no cover
    print("python3-yaml is not installed", file=sys.stderr)
    sys.exit(2)

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github" / "workflows"
TESTS = ROOT / "tests"

# The workflow of the release line and the one of the 7.3 bring-up line.
RELEASE = "build-sk-d840n.yml"
BRINGUP = "build-sk-d840n-73.yml"

# The steps the release line cannot lose: the gates, the build and the
# bootweb job that produces the offline candidate.
RELEASE_JOBS = {"check-led-gates", "build", "bootweb"}
BRINGUP_JOBS = {"check-gates", "build"}

# The steps of the 7.3 line that carry the whole point of the line.
BRINGUP_STEPS = [
    "Prepare the Linux 7.3.4 tree",
    "Check the board layer against the 7.3 tree",
    "Apply the board layer",
    "Check the applied board layer",
    "Check the 9132 switch driver against the 7.3 tree",
    "Apply the OpenWrt-side overlays",
    "Enable /dev/mem in the 7.3 fragments",
    "Install feeds",
    "Configure",
    "Download packages",
    "Compile",
    "Gate: the built kernel is 7.3.4",
    "Gate: the board device tree compiles against the 7.3 tree",
]

# The paths that decide whether a change to the 7.3 line starts a build.
BRINGUP_TRIGGERS = [
    "kernel-patches/**",
    "kernel-overlays-73/**",
    "overlays-73/**",
    "tools/prepare-sk-kernel-73.py",
    "tools/apply-sk-overlay-73.sh",
    "tools/check-sk-board-api.py",
    "tools/check-sk-switch-api.py",
    "scripts/configure-sk-d840n.sh",
    "configs/sk-d840n-73.json",
]

# The gates both lines run before they build anything.
SHARED_GATES = [
    "tests/test_sk_kernel.py",
    "tests/test_sk_led_dts_gate.py",
    "tests/test_sk_layout.py",
    "tests/test_sk_uboot.py",
]


class Failures:
    def __init__(self):
        self.items = []

    def check(self, ok, message):
        print(f"  [{'OK ' if ok else 'FAIL'}] {message}")
        if not ok:
            self.items.append(message)
        return ok

    def report(self, what):
        if self.items:
            print(f"\nFAILED: {what} ({len(self.items)} problem(s))")
            for item in self.items:
                print(f"  - {item}")
            return 1
        print(f"\n{what}: OK")
        return 0


def load(path, failures):
    """Parse one workflow file and report the parse error in its own words."""
    raw = path.read_bytes()
    if raw.startswith(b"\xef\xbb\xbf"):
        failures.check(False, f"{path.name} has a UTF-8 BOM")
        return None
    try:
        doc = yaml.safe_load(raw.decode("utf-8"))
    except yaml.YAMLError as error:
        mark = getattr(error, "problem_mark", None)
        where = f"line {mark.line + 1}" if mark is not None else "unknown line"
        failures.check(False, f"{path.name} does not parse ({where}): {error}")
        return None
    if not isinstance(doc, dict):
        failures.check(False, f"{path.name} does not parse into a mapping")
        return None
    failures.check(True, f"{path.name} parses")
    return doc


def triggers_of(doc):
    """The push path filter.  YAML 1.1 reads the key "on" as the boolean True."""
    node = doc.get("on", doc.get(True))
    if not isinstance(node, dict):
        return []
    push = node.get("push") or {}
    paths = push.get("paths") or []
    return [str(path) for path in paths]


def steps_of(doc, job_name):
    job = (doc.get("jobs") or {}).get(job_name) or {}
    return job.get("steps") or []


def step_names(doc, job_name):
    return [str(step.get("name") or step.get("uses") or "?")
            for step in steps_of(doc, job_name)]


def run_text(doc, job_name):
    """Everything the job's steps execute, concatenated."""
    parts = []
    for step in steps_of(doc, job_name):
        body = step.get("run")
        if body:
            parts.append(str(body))
    return "\n".join(parts)


def referenced_tests(text):
    return sorted(set(re.findall(r"tests/[\w./-]+\.(?:py|sh)", text)))


def main():
    failures = Failures()

    print("the workflow files parse")
    docs = {}
    for path in sorted(WORKFLOWS.glob("*.yml")):
        doc = load(path, failures)
        if doc is not None:
            docs[path.name] = doc
    if failures.items:
        return failures.report("workflows")

    release = docs.get(RELEASE)
    bringup = docs.get(BRINGUP)
    failures.check(release is not None, f"{RELEASE} is present")
    failures.check(bringup is not None, f"{BRINGUP} is present")
    if release is None or bringup is None:
        return failures.report("workflows")

    print("\nthe release line")
    release_jobs = set((release.get("jobs") or {}).keys())
    for job in sorted(RELEASE_JOBS):
        failures.check(job in release_jobs, f"{RELEASE} has the job {job}")
    failures.check(
        str(((release.get("jobs") or {}).get("check-led-gates") or {}).get("name", ""))
        == "LED CI regression",
        f"{RELEASE} names its gate job")
    release_gate = run_text(release, "check-led-gates")
    for gate in SHARED_GATES:
        failures.check(gate in release_gate,
                       f"the release line runs {gate}")

    print("\nthe 7.3 bring-up line")
    bringup_jobs = set((bringup.get("jobs") or {}).keys())
    for job in sorted(BRINGUP_JOBS):
        failures.check(job in bringup_jobs, f"{BRINGUP} has the job {job}")
    names = step_names(bringup, "build")
    for step in BRINGUP_STEPS:
        failures.check(step in names, f"{BRINGUP} has the step {step!r}")

    bringup_gate = run_text(bringup, "check-gates")
    for gate in SHARED_GATES:
        failures.check(gate in bringup_gate,
                       f"the 7.3 line runs {gate}")

    print("\nthe path filter of the 7.3 line")
    triggers = triggers_of(bringup)
    for path in BRINGUP_TRIGGERS:
        failures.check(path in triggers,
                       f"the 7.3 line rebuilds on {path}")

    print("\nthe gate jobs only name tests that exist")
    for workflow, job in ((RELEASE, "check-led-gates"), (BRINGUP, "check-gates")):
        for test in referenced_tests(run_text(docs[workflow], job)):
            failures.check((ROOT / test).is_file(),
                           f"{workflow} runs {test}, which exists")

    return failures.report("workflows")


if __name__ == "__main__":
    sys.exit(main())
