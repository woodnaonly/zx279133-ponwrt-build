#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Negative controls for tools/check-sk-workflow-paths.py.

The gate asks whether every file a workflow runs or names is covered by that
workflow's paths: list.  A file that is used but not listed is a file whose
change is never checked: the gate that reads it stops being checked the
moment somebody edits it, because the edit does not start a build.

Each control below breaks one of the things the gate asks about, and the
gate has to turn red *on the check that names that thing* - not merely red
somewhere.  One control goes the other way: it takes a file out of the
workflow's run lines and the gate has to stop asking about that file, which
is what proves that the gate reads the run lines at all and does not just
compare the paths: list against itself.

The controls read the gate's own parser rather than a second copy of it: a
control that re-implements the rule would keep passing when the rule
changed, and would prove nothing about the gate that runs.

    python3 tests/test_sk_workflow_paths.py [--workflow FILE ...]
"""
import argparse
import importlib.util
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GATE = ROOT / "tools/check-sk-workflow-paths.py"

DEFAULT_WORKFLOWS = [
    ".github/workflows/build-sk-d840n.yml",
    ".github/workflows/build-sk-d840n-73.yml",
]


def load_gate():
    """The gate itself, not a copy of it."""
    spec = importlib.util.spec_from_file_location("sk_workflow_paths", GATE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


GATE_MODULE = load_gate()


def failed_checks(workflow, root):
    """The checks the gate reported as failed.

    The gate publishes its verdict to GITHUB_STEP_SUMMARY, and it writes that
    file rather than appending to it.  A control runs the gate on a
    deliberately broken copy, so it must not publish: with the summary file in
    place, the broken copy's verdict replaces the real one on the run page and
    a green job reads as red.  The run that the control drives therefore gets
    no summary file at all.
    """
    env = dict(os.environ)
    env.pop("GITHUB_STEP_SUMMARY", None)
    result = subprocess.run(
        [sys.executable, str(GATE), "--workflow", str(workflow),
         "--root", str(root)],
        capture_output=True, text=True, env=env)
    names = set()
    for line in (result.stdout + result.stderr).splitlines():
        match = re.match(r"^\s*\[FAIL\]\s+(.+?)(?:\s+-\s+.*)?$", line)
        if match:
            names.add(match.group(1))
    return names, result.returncode


def copy_as(text, work, tag, name):
    """A mutated copy that keeps the workflow's own file name.

    The gate names its checks after the file it read, so the copy has to
    keep the name or the check names would not match.
    """
    target = work / tag / name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(text.encode("utf-8"))
    return target


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workflow", action="append", default=None,
                        help="a workflow to control (repeatable)")
    args = parser.parse_args()

    workflows = args.workflow or DEFAULT_WORKFLOWS
    checks = 0
    failures = []

    def check(ok, what, detail=""):
        nonlocal checks
        checks += 1
        if ok:
            print("  ok   %s" % what)
        else:
            print("  FAIL %s%s" % (what, (" - " + detail) if detail else ""))
            failures.append(what)

    for relative in workflows:
        source = ROOT / relative
        if not source.is_file():
            check(False, "%s exists" % relative)
            continue
        text = source.read_text(encoding="utf-8")
        name = source.name

        entries, _blocks = GATE_MODULE.parse_paths(text)
        invocations = GATE_MODULE.parse_invocations(text)
        named = GATE_MODULE.parse_named(text)
        required = GATE_MODULE.required_files(text)

        with tempfile.TemporaryDirectory(prefix="sk-wf-paths-") as tmp:
            work = Path(tmp)

            # The workflow as it is has to pass.
            good = copy_as(text, work, "good", name)
            names, rc = failed_checks(good, ROOT)
            check(rc == 0, "%s: the workflow as it is passes the gate" % name,
                  ", ".join(sorted(names)[:3]))

            # 1. An entry that names a file the workflow uses is dropped.
            target = None
            for item in required:
                if item in entries:
                    target = item
                    break
            if target is None:
                check(False, "%s: a file the workflow uses is listed exactly"
                      % name)
            else:
                broken = copy_as(
                    text.replace("      - %s\n" % target, "", 1),
                    work, "drop", name)
                names, rc = failed_checks(broken, ROOT)
                expected = "%s: %s is covered by paths" % (name, target)
                check(expected in names,
                      "%s: dropping the entry for %s -> red on %r"
                      % (name, target, expected),
                      ", ".join(sorted(names)[:3]))

            # 2. A glob that no longer covers what it used to.  The glob has
            #    to be one that covered something: a glob that covered
            #    nothing would change nothing, and the control would be
            #    proving nothing.
            import fnmatch
            useful = None
            for entry in entries:
                if not any(char in entry for char in "*?["):
                    continue
                if any(GATE_MODULE.covered_by(item, [entry]) for item in required):
                    useful = entry
                    break
            if useful is None:
                # This workflow names every file it uses, so there is no glob
                # to break.  Say that, and check the stronger thing that
                # makes it true: each file is covered by its own entry.
                by_name = all(any(entry == item for entry in entries)
                              for item in required)
                check(by_name,
                      "%s: every file the workflow uses is listed by name, so "
                      "there is no glob to break" % name,
                      ", ".join(item for item in required
                               if not any(entry == item
                                          for entry in entries))[:200])
            else:
                broken = copy_as(
                    text.replace("      - %s\n" % useful,
                                 "      - %s\n" % useful.replace("*", "nothing-here-*"),
                                 1), work, "glob", name)
                names, rc = failed_checks(broken, ROOT)
                red = [n for n in names if n.startswith("%s: " % name)
                       and "is covered by paths" in n]
                check(bool(red),
                      "%s: the glob %s no longer covers what it covered -> red"
                      % (name, useful),
                      ", ".join(sorted(names)[:3]))

            # 3. The paths: block is gone.
            broken = copy_as(
                text.replace("    paths:\n", "    paths: []\n", 1),
                work, "nopaths", name)
            names, rc = failed_checks(broken, ROOT)
            expected = "%s: the workflow has a paths: block" % name
            check(expected in names,
                  "%s: no paths: block -> red on %r" % (name, expected),
                  ", ".join(sorted(names)[:3]))

            # 4. An entry names a file that is not there.
            exact = [entry for entry in entries
                     if not any(char in entry for char in "*?[")]
            if not exact:
                check(False, "%s: the paths: list names a file" % name)
            else:
                entry = exact[0]
                ghost = (entry.replace(".py", "-ghost.py") if entry.endswith(".py")
                         else entry + "-ghost")
                broken = copy_as(
                    text.replace("      - %s\n" % entry,
                                 "      - %s\n" % ghost, 1), work, "ghost", name)
                names, rc = failed_checks(broken, ROOT)
                expected = "%s: %s exists" % (name, ghost)
                check(expected in names,
                      "%s: an entry that names nothing -> red on %r"
                      % (name, expected),
                      ", ".join(sorted(names)[:3]))

            # 5. A file the workflow ran is no longer run: the gate has to
            #    stop asking about it.  This is the control that says the
            #    gate reads the run lines and not only the paths: list.
            victim = None
            mutated = None
            for item in invocations:
                lines = text.splitlines(keepends=True)
                kept = []
                dropped = 0
                for line in lines:
                    if dropped == 0 and item in line and \
                            re.search(r"(?:python3?|sh|bash)\s+", line):
                        dropped += 1
                        continue
                    kept.append(line)
                if dropped == 0:
                    continue
                candidate = "".join(kept)
                # The file is named by the very line that runs it, so the
                # question is whether it is named anywhere else: if it is,
                # taking the run line away does not make the gate stop
                # asking about it, and the control would prove nothing.
                if item in GATE_MODULE.parse_named(candidate):
                    continue
                victim = item
                mutated = candidate
                break
            if victim is None:
                check(False, "%s: a file the workflow runs and names nowhere "
                             "else" % name)
            else:
                broken = copy_as(mutated, work, "norun", name)
                names, rc = failed_checks(broken, ROOT)
                expected = "%s: %s is covered by paths" % (name, victim)
                check(expected not in names,
                      "%s: %s is no longer run -> the gate stops asking "
                      "about it" % (name, victim),
                      ", ".join(sorted(names)[:3]))

            # 6. A file the workflow only names - it is loaded from inside a
            #    "python -c" string, or named after a variable interpreter,
            #    or a directory that is copied into the tree - and its entry
            #    is dropped.  The gate has to notice, because such a file is
            #    an input of the build like any other.
            named_only = None
            for item in named:
                if item not in invocations and item in entries:
                    named_only = item
                    break
            if named_only is None:
                check(False, "%s: a file the workflow names and does not run"
                      % name)
            else:
                broken = copy_as(
                    text.replace("      - %s\n" % named_only, "", 1),
                    work, "dropnamed", name)
                names, rc = failed_checks(broken, ROOT)
                expected = "%s: %s is covered by paths" % (name, named_only)
                check(expected in names,
                      "%s: dropping the entry for the named %s -> red on %r"
                      % (name, named_only, expected),
                      ", ".join(sorted(names)[:3]))

            # 7. The file every build reads whether the workflow says so or
            #    not: .gitattributes decides the bytes of the checkout, so a
            #    change to it alone must still start a build.  The gate has to
            #    ask about it, and the control has to be able to take the
            #    entry away.
            always = list(GATE_MODULE.ALWAYS)
            if not always:
                check(False, "%s: the gate names the files every build reads"
                      % name)
            else:
                victim = always[0]
                if victim not in entries:
                    check(False, "%s: %s is listed in paths" % (name, victim))
                else:
                    broken = copy_as(
                        text.replace("      - %s\n" % victim, "", 1),
                        work, "dropalways", name)
                    names, rc = failed_checks(broken, ROOT)
                    expected = "%s: %s is covered by paths" % (name, victim)
                    check(expected in names,
                          "%s: dropping the entry for %s -> red on %r"
                          % (name, victim, expected),
                          ", ".join(sorted(names)[:3]))

            # 8. A workflow that reads every document has to collect them.
            #    The text hygiene gate reads every .md in the repository, so
            #    the workflow that runs it has to be started by a change to
            #    a document - otherwise a document can grow a character no
            #    reader can see and no run would ever look at it.
            runs_hygiene = any(item.endswith(GATE_MODULE.READS_EVERY_DOCUMENT)
                               for item in invocations)
            entry = GATE_MODULE.covers_documents(entries)
            if not runs_hygiene:
                check(True,
                      "%s: it does not run the text hygiene gate, so the "
                      "documents are not its business" % name)
            elif entry is None:
                check(False, "%s: the documents are collected" % name)
            else:
                broken = copy_as(
                    text.replace("      - %s\n" % entry, "", 1),
                    work, "dropdocs", name)
                names, rc = failed_checks(broken, ROOT)
                expected = "%s: the documents are covered by paths" % name
                check(expected in names,
                      "%s: dropping the entry that collects the documents -> "
                      "red on %r" % (name, expected),
                      ", ".join(sorted(names)[:3]))

            # 9. A control must not publish a verdict.  The gate writes its
            #    report to GITHUB_STEP_SUMMARY, and a control runs the gate on
            #    a broken copy: if that run publishes, the broken copy's
            #    verdict is the one the run page shows, and a green job reads
            #    as red.  This control runs the gate with a summary file in
            #    place and requires that the file stays empty.
            with tempfile.TemporaryDirectory(prefix="sk-wf-summary-") as tmp:
                summary = Path(tmp) / "summary.md"
                previous = os.environ.get("GITHUB_STEP_SUMMARY")
                os.environ["GITHUB_STEP_SUMMARY"] = str(summary)
                try:
                    failed_checks(source, ROOT)
                finally:
                    if previous is None:
                        os.environ.pop("GITHUB_STEP_SUMMARY", None)
                    else:
                        os.environ["GITHUB_STEP_SUMMARY"] = previous
                check(not summary.exists(),
                      "%s: a control does not publish a verdict to the run "
                      "page" % name,
                      "the control's gate run wrote %s" % summary)

    print("%d checks, %d failed" % (checks, len(failures)))
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
