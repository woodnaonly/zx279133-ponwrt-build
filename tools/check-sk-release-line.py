#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Check that one line publishes, and that it is the 7.3 one.

Two lines build this board.  The 6.18 line (build-sk-d840n.yml) and the 7.3
line (build-sk-d840n-73.yml) both compile the firmware, and the 7.3 line
is the release line: it publishes the release, and the 6.18 line only
builds and uploads its artifacts.

That split is a decision, and a decision nobody wrote down is a decision
that can be undone by accident.  A publish step added to the 6.18 line
puts two lines' builds on the same Releases page; the 7.3 line's tag
changed to look like the 6.18 one makes the two indistinguishable,
because the two lines count their runs separately - "skd840n_flash_98_..."
does not say which of the two built it, and both lines have a run 98.

So the split is checked, and the check reads the workflow files as YAML and
compares what they say, not what the comment above them says.

The files a release ships are compared with the files the 7.3 build
actually produced.  That listing is not written here: it is the measured
listing of the artifacts of run #96, in tests/fixtures/sk-73-artifact-files.txt.
A release that ships a file the build never made is worse than no release
at all, and a check that compared the file list with a second copy of the
same list would only say that the copy agrees with itself.

The gates the workflows call are read as well: a gate that is not
in the tree, or that is called with an option it does not take, is a
gate that never ran, and a gate that never ran is a green light that
means nothing.

Usage:
  check-sk-release-line.py [--tree ROOT]
"""
import argparse
import ast
import json
import re
import sys
from pathlib import Path

try:
    import yaml
except ImportError:                                    # pragma: no cover
    print("python3-yaml is not installed", file=sys.stderr)
    sys.exit(2)


# The line that builds and uploads, and publishes nothing.
LEGACY = "build-sk-d840n.yml"
# The release line.
PUBLISH = "build-sk-d840n-73.yml"

RELEASE_ACTION = "softprops/action-gh-release"
TAG_PREFIX = "skd840n_73_"
FIXTURE = "tests/fixtures/sk-73-artifact-files.txt"

# The two files that pin which kernel each line builds.  The version a
# release announces is read out of them and never written here: a version
# written into a decision is right for one line and wrong for every other
# one, and the lint over this repository says so.
LOCK_SEVEN = "configs/sk-d840n-73.json"
LOCK_LEGACY = "configs/sk-d840n-stable.json"

# The GitHub expressions, written out in pieces so that this file's own
# text does not look like a template to the tools that read it.
DOLLAR = "$"
MODE_FIELD = DOLLAR + "{{ matrix.mode }}"
RUN_NUMBER_FIELD = DOLLAR + "{{ github.run_number }}"
RUN_ID_FIELD = DOLLAR + "{{ github.run_id }}"
LEGACY_ARTIFACT = "sk-d840n-" + MODE_FIELD

# The files only the 6.18 line builds.  A 7.3 release that names one of
# them names a file its own build never produced - and the 6.18 names are
# the ones a copy of the 6.18 release step would carry over.
LEGACY_ONLY = (
    "kernel-provenance.json",
    "flash-set.tgz",
    "trial-set.tgz",
    "trial-Image.gz",
    "trial-dtb.bin",
    "trial-kexec.sh",
    "flash-sk-d840n.sh",
    "sha256sums-trial",
    "sha256sums-release",
    "sk-d840n-initramfs.itb",
)

# The files the 7.3 build copies into the artifact of the flash mode.  They
# are read out of the workflow's own export step and compared with the
# measured listing: two sources that have to agree.
FLASH_FILES = (
    "flash-uImage",
    "flash-dtb.bin",
    "flash-rootfs.jffs2",
    "flash-sysupgrade.bin",
    "immortalwrt-sk-d840n-sysupgrade.bin",
    "sysupgrade-report.json",
    "sha256sums",
)


class Report:
    def __init__(self):
        self.failures = []
        self.checks = 0

    def require(self, ok, message, detail=""):
        self.checks += 1
        print(f"  [{'OK ' if ok else 'FAIL'}] {message}"
              + (f" - {detail}" if detail and not ok else ""))
        if not ok:
            self.failures.append(message)
        return ok

    def report(self, what):
        if self.failures:
            print(f"\nFAILED: {what} ({len(self.failures)} problem(s))")
            for item in self.failures:
                print(f"  - {item}")
            return 1
        print(f"\n{what}: OK")
        return 0


def steps_of(doc):
    """Every step of every job, with the name of the job it is in."""
    out = []
    for job_name, job in (doc.get("jobs") or {}).items():
        for step in job.get("steps") or []:
            out.append((job_name, step))
    return out


def publish_steps(doc):
    return [(job, step) for job, step in steps_of(doc)
            if RELEASE_ACTION in str(step.get("uses") or "")]


def step_run_text(doc, job_name, step_name):
    job = (doc.get("jobs") or {}).get(job_name) or {}
    for step in job.get("steps") or []:
        if str(step.get("name") or "") == step_name:
            return str(step.get("run") or "")
    return ""


def dispatch_inputs(doc):
    node = doc.get("on", doc.get(True)) or {}
    dispatch = node.get("workflow_dispatch") or {}
    return dispatch.get("inputs") or {}


def parse_fixture(text):
    """The measured listing, as a section name -> set of file names."""
    sections = {}
    current = None
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped.startswith("[") and stripped.endswith("]"):
            current = stripped[1:-1]
            sections.setdefault(current, set())
            continue
        if current is not None:
            sections[current].add(stripped)
    return sections


def logical_lines(text):
    """The commands of the run steps, with the continued lines joined."""
    out = []
    buf = ""
    for raw in text.splitlines():
        stripped = raw.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped.endswith(chr(92)):
            buf += stripped[:-1] + " "
            continue
        out.append(buf + stripped)
        buf = ""
    if buf:
        out.append(buf)
    return out


def repo_calls(text):
    """Every repo script the workflow runs, with the options it is given."""
    out = []
    for line in logical_lines(text):
        for part in line.replace("||", "&&").split("&&"):
            for chunk in part.split(";"):
                tokens = chunk.split()
                for index, token in enumerate(tokens):
                    if token not in ("python3", "python"):
                        continue
                    if index + 1 >= len(tokens):
                        continue
                    script = tokens[index + 1]
                    if not script.startswith(("tools/", "tests/")):
                        continue
                    options = []
                    for later in tokens[index + 2:]:
                        if not later.startswith("--"):
                            break
                        options.append(later)
                    out.append((script, options))
    return out


def declared_options(source):
    """The options the script declares, if it declares them at all.

    A script that reads its options with argparse says which options it
    takes in its own add_argument calls; a script that reads them by hand
    says them in its own text.  A script that does neither takes no
    options, and the caller must not give it any.
    """
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return None
    options = set()
    uses_argparse = False
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Attribute):
            name = func.attr
        elif isinstance(func, ast.Name):
            name = func.id
        else:
            name = ""
        if name == "ArgumentParser":
            uses_argparse = True
        elif name == "add_argument":
            uses_argparse = True
            for arg in node.args:
                if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                    if arg.value.startswith("-"):
                        options.add(arg.value)
                elif isinstance(arg, (ast.List, ast.Tuple)):
                    for item in arg.elts:
                        if isinstance(item, ast.Constant) and isinstance(item.value, str):
                            if item.value.startswith("-"):
                                options.add(item.value)
    return options if uses_argparse else None


def call_checks(root, texts, report):
    """The gates the workflows call are there and take what they are given.

    A gate the workflow calls with an option the gate does not take is a
    gate that never ran: the run dies on the option instead of on the
    tree, and the build stops before the later gates are reached.  That
    is not something anybody notices, because the build stops at the first
    gate and the gates after it are never read.
    """
    for name, text in sorted(texts.items()):
        for script, options in repo_calls(text):
            path = Path(root) / script
            if not path.is_file():
                report.require(False,
                               "the gates the workflows call are in the tree",
                               f"{name} calls {script}, which is not there")
                continue
            body = path.read_text(encoding="utf-8", errors="replace")
            declared = declared_options(body)
            for option in sorted(set(options)):
                if declared is None:
                    known = option in body
                else:
                    known = option in declared
                report.require(known,
                               "the gates take the options they are given",
                               f"{name} calls {script} with {option}, "
                               "which the script does not know")


def locked_version(root, relative, report):
    """The version a line pins, out of the file that pins it."""
    path = Path(root) / relative
    if not path.is_file():
        report.require(False, "the version locks are in the tree",
                       f"{relative} is not there")
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        report.require(False, "the version locks are in the tree", str(error))
        return None
    version = str(data.get("version") or "")
    report.require(bool(version), "the version locks say which version",
                   f"{relative} names no version")
    return version or None


def analyse(texts, fixture_text, root):
    """Everything the gate has to say about these workflow files."""
    report = Report()
    docs = {}
    for name, text in texts.items():
        try:
            docs[name] = yaml.safe_load(text)
        except yaml.YAMLError as error:
            report.require(False, f"{name} parses", str(error))
    legacy = docs.get(LEGACY)
    publish = docs.get(PUBLISH)
    if legacy is None or publish is None:
        report.require(False, "the two SK-D840N workflows are present")
        return report

    produced = parse_fixture(fixture_text)
    report.require("flash" in produced and "initramfs" in produced,
                   "the measured listing of the 7.3 artifacts is there",
                   f"the listing has these sections: {sorted(produced)}")

    version = locked_version(root, LOCK_SEVEN, report)
    other = locked_version(root, LOCK_LEGACY, report)
    if version is None or other is None:
        return report

    publishing = sorted(name for name, doc in docs.items() if publish_steps(doc))
    report.require(publishing == [PUBLISH],
                   "the 7.3 line is the only line that publishes",
                   f"these workflows publish: {publishing}")
    report.require(not publish_steps(legacy),
                   "the 6.18 line publishes nothing",
                   "the release line is the 7.3 one")

    steps = publish_steps(publish)
    report.require(bool(steps), "the 7.3 line publishes a release")

    for job, step in steps:
        with_ = step.get("with") or {}
        tag = str(with_.get("tag_name") or "")
        report.require(tag.startswith(TAG_PREFIX),
                       "the 7.3 release tag says which line built it",
                       f"tag_name is {tag!r}")
        missing = [field for field in (MODE_FIELD, RUN_NUMBER_FIELD, RUN_ID_FIELD)
                   if field not in tag]
        report.require(not missing,
                       "the 7.3 release tag names the mode and the run",
                       f"missing {missing} in {tag!r}")

        condition = str(step.get("if") or "")
        report.require("inputs.publish_release != false" in condition,
                       "the 7.3 release honours the publish switch",
                       f"the condition is {condition!r}")

        match = re.search(r"matrix\.mode\s*==\s*'([a-z]+)'", condition)
        mode = match.group(1) if match else None
        files = [line.strip() for line in str(with_.get("files") or "").splitlines()
                 if line.strip()]
        names = [Path(name).name for name in files]
        report.require(bool(names), "the 7.3 release ships files")

        section = produced.get(mode or "", set())
        unknown = sorted(set(name for name in names if name not in section))
        report.require(not unknown,
                       "the 7.3 release ships only files the 7.3 build produced",
                       f"the {mode} build produced no {unknown}")

        borrowed = sorted(set(name for name in names if name in LEGACY_ONLY))
        report.require(not borrowed,
                       "the 7.3 release does not ship a file only the 6.18 line builds",
                       f"these are 6.18 files: {borrowed}")

        body = str(with_.get("body") or "")
        report.require(version in body,
                       "the 7.3 release body names the kernel it ships",
                       f"the body does not say {version}")
        report.require(other not in body,
                       "the 7.3 release body does not name the other line's kernel",
                       f"the body mentions {other}")

        if mode == "flash":
            report.require("immortalwrt-sk-d840n-sysupgrade.bin" in names,
                           "the 7.3 flash release ships the direct-flash image")
            report.require("sysupgrade-report.json" in names,
                           "the 7.3 flash release ships the report of the image")
        if mode == "initramfs":
            report.require(any(name.endswith(".itb") for name in names),
                           "the 7.3 initramfs release ships the RAM image")

    report.require("publish_release" in dispatch_inputs(publish),
                   "the 7.3 line offers the publish switch")
    report.require("publish_release" not in dispatch_inputs(legacy),
                   "the 6.18 line has no publish switch",
                   "it publishes nothing, so a switch would be a lie")

    legacy_uploads = {str((step.get("with") or {}).get("name") or "")
                      for job, step in steps_of(legacy)
                      if "upload-artifact" in str(step.get("uses") or "")}
    report.require(LEGACY_ARTIFACT in legacy_uploads,
                   "the 6.18 line still uploads its firmware",
                   f"it uploads {sorted(legacy_uploads)}")

    export_text = step_run_text(publish, "build", "Validate and export the partitions")
    exported = [name for name in FLASH_FILES if name in export_text]
    unknown = sorted(name for name in exported if name not in produced.get("flash", set()))
    report.require(not unknown and len(exported) == len(FLASH_FILES),
                   "the 7.3 flash files are the ones the build copies into the artifact",
                   f"the export step names {exported}")
    call_checks(root, texts, report)
    return report


def workflow_texts(root):
    base = Path(root) / ".github" / "workflows"
    return {path.name: path.read_text(encoding="utf-8")
            for path in sorted(base.glob("*.yml"))}


def main() -> int:
    parser = argparse.ArgumentParser(description="check the release line")
    parser.add_argument("--tree", type=Path, default=None,
                        help="the repository root (default: the one this tool is in)")
    args = parser.parse_args()
    root = args.tree.resolve() if args.tree else Path(__file__).resolve().parents[1]
    fixture = root / FIXTURE
    if not fixture.is_file():
        print(f"FAILED: the measured listing is missing ({fixture})")
        return 1
    report = analyse(workflow_texts(root), fixture.read_text(encoding="utf-8"), root)
    return report.report("the release line")


if __name__ == "__main__":
    sys.exit(main())
