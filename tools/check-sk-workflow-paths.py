#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Check that every file a workflow runs is covered by that workflow's paths.

A workflow's paths: list says which changes start a build.  A file the
workflow *runs* but does not list is a file whose change is never checked:
the gate that reads it is only as good as the last build, and editing the
gate does not start a build, so the edit sits there unverified.

This has already happened.  tests/test_sk_workflows.py - the control that
decides which steps this line has and which paths start a build - was not in
the 7.3 line's paths, so a change to it never started a build; and ten more
files that the 7.3 line runs (the LED gate, the dtb-compat gate, the
partition export, four of the repository's own controls, the UI script) were
missing the same way.  The 6.18 release line was missing two.

So the list is checked mechanically now: every file the workflow invokes has
to be covered by an entry, either exactly or by a glob that matches it.

The tool reads the workflow as text.  It does not need a YAML parser, and it
does not need one: the two workflows use one form of invocation - a literal
"python3 <path>" or "sh <path>" - and there is no path built out of a
variable anywhere in either file.  What it cannot see, it says: the report
names the number of invocations it found, so a reader can tell the difference
between "there is nothing to check" and "this tool looked at all of them".

Usage:
  check-sk-workflow-paths.py --workflow .github/workflows/build-sk-d840n.yml
                             [--workflow ...] [--root ROOT]
"""
import argparse
import fnmatch
import os
import re
import sys
from pathlib import Path

# A repository file the workflow runs.  The first group is the interpreter
# or the shell, the second the file.
INVOKE = re.compile(
    r"(?:^|[;&|]\s*|\s)(?:python3?|sh|bash)\s+"
    r"((?:tools|tests|scripts|configs|templates|overlays|kernel-overlays|"
    r"kernel-overlays-73|kernel-patches|bootweb|trial-files|docs)/"
    r"[A-Za-z0-9_./-]+\.(?:py|sh))")

# A repository path the workflow names in a command line.  This is wider
# than INVOKE: a script loaded from inside a "python -c" string, a file
# named after a variable interpreter, a directory that is copied into the
# OpenWrt tree - all of them are inputs the build reads, and none of them
# is an invocation the interpreter form would find.
NAMED = re.compile(
    r"(?<![\w./-])((?:tools|tests|scripts|configs|templates|overlays|"
    r"overlays-73|kernel-overlays|kernel-overlays-73|kernel-patches|"
    r"bootweb|trial-files|docs)/[A-Za-z0-9_./-]+)")

PATH_ENTRY = re.compile(r"^\s*-\s+(\S+)\s*$")
GLOB_CHARS = ("*", "?", "[")

# Files the build reads without the workflow naming them.  .gitattributes
# decides the bytes of every checkout, so a change to it changes what the
# build reads - and a change to it alone would not start a build if the
# paths: block did not collect it.  The damage it does is quiet: the image
# is built from a checkout with different rules than a fresh clone would
# produce, and the gate that reads the tree cannot tell.
ALWAYS = ('.gitattributes',)

# A workflow that runs the text hygiene gate reads every document in the
# repository, so it has to collect the documents: otherwise a document can
# grow a character no reader can see, and the run that would have noticed
# never starts.  The rule is about the workflow that reads the files, not
# about the workflow that happens to collect them today.
READS_EVERY_DOCUMENT = 'tests/test_sk_text_hygiene.py'
DOCUMENT_DIR = 'docs'


class Report:
    def __init__(self):
        self.failures = []
        self.checks = 0
        self.results = []

    def require(self, ok, message, detail=""):
        self.checks += 1
        self.results.append((ok, message, detail))
        print(f"  [{'OK ' if ok else 'FAIL'}] {message}"
              + (f" - {detail}" if detail and not ok else ""))
        if not ok:
            self.failures.append(message)
        return ok


def write_summary(report, title, files=None):
    """Put the verdict where the run page shows it."""
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if not path:
        return
    lines = [f"### {title}", ""]
    for ok, message, detail in report.results:
        mark = "PASS" if ok else "FAIL"
        lines.append(f"- **{mark}** {message}")
        if detail and not ok:
            lines.append(f"  - {detail}")
    lines.append("")
    lines.append(f"{report.checks} check(s), {len(report.failures)} failed.")
    lines.append("")
    if files:
        lines.append("The files this gate read:")
        lines.append("")
        for name, digest, size in files:
            lines.append(f"- `{name}`: {digest} ({size} bytes)")
        lines.append("")
    Path(path).write_text("\n".join(lines), encoding="utf-8")


def strip_comments(lines):
    """The lines that are not a whole-line comment."""
    out = []
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        out.append(line)
    return out


def parse_paths(text):
    """The entries of the push trigger's paths: list.

    A comment inside the block does not end it - the workflows explain each
    entry in a comment above it - and an entry may carry a glob.
    """
    lines = text.splitlines()
    entries = []
    blocks = 0
    index = 0
    while index < len(lines):
        if re.match(r"^\s*paths:\s*$", lines[index]):
            blocks += 1
            index += 1
            while index < len(lines):
                stripped = lines[index].strip()
                if not stripped or stripped.startswith("#"):
                    index += 1
                    continue
                if not stripped.startswith("-"):
                    break
                match = PATH_ENTRY.match(lines[index])
                if match:
                    # A path that starts with a glob has to be quoted in
                    # YAML ('*' opens an alias there), so the quotes are
                    # syntax and not part of the path.
                    entries.append(match.group(1).strip("'\""))
                index += 1
            continue
        index += 1
    return entries, blocks


def parse_invocations(text):
    """The repository files the workflow runs, in order of appearance."""
    body = "\n".join(strip_comments(text.splitlines()))
    found = []
    for match in INVOKE.finditer(body):
        name = match.group(1)
        if name not in found:
            found.append(name)
    return found


def outside_paths_block(text):
    """The lines that are not the paths: list and not a comment.

    The paths: list names the files on purpose; it is not a use of them, so
    a file that is listed there and nowhere else is not a file the build
    reads.
    """
    out = []
    in_block = False
    for line in text.splitlines():
        if re.match(r"^\s*paths:\s*$", line):
            in_block = True
            continue
        stripped = line.strip()
        if in_block:
            if not stripped or stripped.startswith("#"):
                continue
            if not stripped.startswith("-"):
                in_block = False
            else:
                continue
        if not stripped or stripped.startswith("#"):
            continue
        out.append(line)
    return out


def parse_named(text):
    """The repository paths the workflow names in a command line.

    A pattern (a path with a glob in it) is not a file, and the only place
    one appears is a cache key, so it is left out: a cache key says when
    the cache is stale, not what the build reads.
    """
    found = []
    for line in outside_paths_block(text):
        for match in NAMED.finditer(line):
            name = match.group(1)
            if any(char in name for char in "*?["):
                continue
            if name not in found:
                found.append(name)
    return found


def required_files(text):
    """Every file this workflow reads, by name.

    The workflow's own run lines and command lines, plus the files that every
    build reads whether it says so or not.  Both the gate and its negative
    controls ask this question here, so a change to the rule cannot leave one
    of them answering an older question.
    """
    required = list(parse_invocations(text))
    for item in parse_named(text):
        if item not in required:
            required.append(item)
    for item in ALWAYS:
        if item not in required:
            required.append(item)
    return required


def covered_by(name, entries):
    """Whether one of the entries covers this path."""
    for entry in entries:
        if entry == name:
            return entry
        if any(char in entry for char in GLOB_CHARS):
            if fnmatch.fnmatch(name, entry):
                return entry
        elif name.startswith(entry.rstrip("/") + "/"):
            # An entry that names a directory covers what is under it.
            return entry
    return None


def covers_documents(entries):
    """The paths entry that collects the documents, if there is one."""
    return covered_by(DOCUMENT_DIR + '/', entries)


def check_workflow(path, root, report):
    """The checks for one workflow."""
    text = path.read_text(encoding="utf-8")
    name = path.as_posix().split("/")[-1]
    entries, blocks = parse_paths(text)

    report.require(blocks > 0,
                   f"{name}: the workflow has a paths: block",
                   "a workflow without paths never starts a build on a change")
    report.require(bool(entries),
                   f"{name}: the paths: block is not empty")

    invocations = parse_invocations(text)
    report.require(bool(invocations),
                   f"{name}: the workflow runs at least one file of this repository",
                   "the gate found nothing to check, so it cannot say anything")

    named = parse_named(text)
    required = required_files(text)

    for item in required:
        entry = covered_by(item, entries)
        if item in invocations:
            how = "the workflow runs it"
        elif item in named:
            how = "the workflow names it in a command line"
        else:
            how = "the checkout rules decide its bytes"
        report.require(entry is not None,
                       f"{name}: {item} is covered by paths",
                       f"{how}, but no path entry covers it; editing it would "
                       f"not start a build, so the gate that reads it would "
                       f"never run again")

    # An entry that names a file which is not there can never match, so it
    # says nothing while looking like it says something.
    for entry in entries:
        if any(char in entry for char in GLOB_CHARS):
            continue
        target = Path(root) / entry
        report.require(target.exists(),
                       f"{name}: {entry} exists",
                       "the paths: list names a file that is not in the repository")

    # A gate that reads files the workflow does not name still has to be
    # reachable: the workflow that runs it has to be started by a change to
    # those files.
    if any(item.endswith(READS_EVERY_DOCUMENT) for item in invocations):
        entry = covers_documents(entries)
        report.require(entry is not None,
                       f"{name}: the documents are covered by paths",
                       "this workflow runs the text hygiene gate, which reads "
                       "every document in the repository, but no path entry "
                       "collects them; editing a document would then not "
                       "start a build that would notice")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workflow", type=Path, action="append", required=True,
                        help="a workflow file to check (repeatable)")
    parser.add_argument("--root", type=Path, default=None,
                        help="the repository root the paths are relative to")
    args = parser.parse_args()

    root = args.root
    if root is None:
        root = Path(__file__).resolve().parents[1]
    root = root.resolve()

    report = Report()
    read = []
    for workflow in args.workflow:
        path = workflow if workflow.is_absolute() else (root / workflow)
        path = path.resolve()
        if not path.is_file():
            report.require(False, f"{workflow} exists")
            continue
        data = path.read_bytes()
        read.append((path.as_posix(), __import__("hashlib").sha256(data).hexdigest(),
                     len(data)))
        check_workflow(path, root, report)

    write_summary(report, "Every file a workflow runs is covered by its paths",
                  read)
    if report.failures:
        print(f"\nFAILED: {len(report.failures)} of {report.checks} check(s)")
        for item in report.failures:
            print(f"  - {item}")
        return 1
    print(f"\nOK: {report.checks} check(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
