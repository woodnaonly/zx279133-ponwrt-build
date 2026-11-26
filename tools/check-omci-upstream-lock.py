#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Check that the vendored OMCI engine still is what the lock file says it is.

The engine in this repository is a copy of six files from
pbs05/openwrt-pon-userspace, with about four hundred lines of our own
changes on top of them.  Upstream keeps moving, so the question is not
"is the copy current" - nobody can answer that without reading every diff
- but "is the copy still the copy we decided to take, and is our part of
it still there".

UPSTREAM.lock.json records that decision: the upstream commit the copy was
taken from, the blob id of every file at that commit, the blob id and the
SHA256 of the file as it sits in this tree, and what we changed in it.  This
gate reads that file and checks it against the tree.

The gate is offline on purpose.  It runs in the gates job of both build
lines, on a machine that may have no way out, and it has to give the same
answer every time.  Refreshing the lock against upstream is a separate,
explicit act: tools/refresh-omci-upstream.py.

What the gate refuses:

  * a vendored file whose bytes are not the bytes the lock names,
  * a file in the vendored source tree the lock does not name, or a lock
    entry whose file is not in the tree,
  * an UPSTREAM note and a lock file that take different files,
  * an upstream part that was dropped without saying why,
  * a change the lock records without saying what it was,
  * a recorded drift between the pin and the upstream it was refreshed
    against.

Usage:
  check-omci-upstream-lock.py [--tree ROOT]
"""
import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

PACKAGE = "overlays/package/libs/libzxpon-omci/omci"
LOCK_RELATIVE = PACKAGE + "/UPSTREAM.lock.json"
UPSTREAM_RELATIVE = PACKAGE + "/UPSTREAM"
SOURCE_RELATIVE = PACKAGE + "/src"

BLOB = re.compile(r"^[0-9a-f]{40}$")

# The upstream parts that stay out of this tree, and why.  The ids are
# what the gate insists on: a part that is dropped keeps its id and its
# reason, so that "we took it out" is a decision somebody wrote down
# rather than a diff that happened.
REQUIRED_NOT_TAKEN = ("oam-runtime", "daemon-runtime")

# A change nobody explained is a change nobody made.
MINIMUM_EXPLANATION = 12

# The lock file is not a place to keep a token.
TOKEN_MARKERS = ("ghp_", "gho_", "ghu_", "ghs_", "ghr_", "github_pat_")


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


def git_blob(data):
    """The name git gives a file's contents."""
    header = b"blob " + str(len(data)).encode("ascii") + b"\0"
    return hashlib.sha1(header + data).hexdigest()


def read_upstream_paths(text):
    """The paths the UPSTREAM note says were taken.

    The note carries a Paths block: a "Paths:" line at the left margin and
    one indented path per line under it.  Reading the block and comparing
    it with the lock file is what keeps the two from saying different
    things about the same copy.
    """
    paths = []
    inside = False
    for line in text.splitlines():
        if not inside:
            if line.rstrip() == "Paths:":
                inside = True
            continue
        if not line.strip():
            continue
        if not line.startswith(" "):
            break
        paths.append(line.strip())
    return paths


def load_lock(lock_path, report):
    """The lock file, or None when it cannot be read."""
    if not report.require(lock_path.is_file(), "the lock file is in the tree",
                          str(lock_path)):
        return None
    try:
        raw = lock_path.read_text(encoding="utf-8")
    except OSError as error:
        report.require(False, "the lock file is readable", str(error))
        return None
    try:
        lock = json.loads(raw)
    except ValueError as error:
        report.require(False, "the lock file is readable JSON", str(error))
        return None
    report.require(True, "the lock file is readable JSON")
    return lock


def analyse(root):
    """Everything the gate has to say about this tree."""
    report = Report()
    root = Path(root)
    lock_path = root / LOCK_RELATIVE
    note_path = root / UPSTREAM_RELATIVE
    package_root = root / PACKAGE
    source_root = package_root / "src"

    lock = load_lock(lock_path, report)
    if lock is None:
        return report

    report.require(bool(lock.get("repository")),
                   "the lock file names the upstream it is pinned to")
    pinned = str(lock.get("pinned_commit") or "")
    report.require(bool(BLOB.match(pinned)),
                   "the lock file pins an upstream commit", pinned)
    report.require(bool(lock.get("license")), "the lock file names the license")
    report.require(bool(lock.get("hash_algorithm")),
                   "the lock file says how it hashes")

    entries = lock.get("files")
    if not report.require(isinstance(entries, list) and bool(entries),
                          "the lock file lists the vendored sources"):
        return report
    ours = lock.get("local_sources") or []
    report.require(isinstance(ours, list),
                   "the lock file lists the sources that are ours")

    present = []
    if source_root.is_dir():
        present = sorted(path.relative_to(package_root).as_posix()
                         for path in source_root.rglob("*.rs"))
    report.require(bool(present), "the vendored source tree is in the tree",
                   str(source_root))

    named = {}
    for entry in list(entries) + list(ours):
        if not isinstance(entry, dict):
            continue
        named[str(entry.get("path") or "")] = entry
    unnamed = [path for path in present if path not in named]
    report.require(not unnamed,
                   "every vendored source is named in the lock file",
                   f"not named: {unnamed}")
    gone = [path for path in named if path not in present]
    report.require(not gone,
                   "every source the lock file names is in the tree",
                   f"not there: {gone}")

    for path, entry in sorted(named.items()):
        source = package_root / path
        data = source.read_bytes() if source.is_file() else b""
        report.require(hashlib.sha256(data).hexdigest() ==
                       str(entry.get("local_sha256") or ""),
                       f"the vendored {path} is the bytes the lock names")
        report.require(git_blob(data) == str(entry.get("local_blob") or ""),
                       f"the vendored {path} is the blob the lock names")
        changes = str(entry.get("local_changes") or "").strip()
        report.require(len(changes) >= MINIMUM_EXPLANATION,
                       f"the lock file explains the change in {path}",
                       f"the explanation is {len(changes)} characters long")

    upstream_paths = set()
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        path = str(entry.get("path") or "")
        source = str(entry.get("upstream_path") or "")
        report.require(bool(source),
                       f"the lock file names the upstream of {path}")
        report.require(bool(BLOB.match(str(entry.get("upstream_blob") or ""))),
                       f"the lock file names the upstream blob of {path}")
        upstream_paths.add(source)

    if not report.require(note_path.is_file(), "the UPSTREAM note is in the tree",
                          str(note_path)):
        return report
    listed = read_upstream_paths(note_path.read_text(encoding="utf-8"))
    report.require(bool(listed), "the UPSTREAM note lists the files that were taken")
    report.require(set(listed) == upstream_paths,
                   "the lock file and the UPSTREAM note take the same files",
                   f"the note says {sorted(set(listed) - upstream_paths)}, "
                   f"the lock says {sorted(upstream_paths - set(listed))}")

    not_taken = lock.get("not_taken")
    if not report.require(isinstance(not_taken, list) and bool(not_taken),
                          "the lock file says what was not taken"):
        return report
    ids = {str(entry.get("id") or "") for entry in not_taken
           if isinstance(entry, dict)}
    for required in REQUIRED_NOT_TAKEN:
        report.require(required in ids,
                       f"the lock file still says why {required} was not taken")
    for entry in not_taken:
        if not isinstance(entry, dict):
            continue
        name = str(entry.get("id") or "?")
        reason = str(entry.get("reason") or "").strip()
        report.require(len(reason) >= MINIMUM_EXPLANATION,
                       f"the lock file explains why {name} was not taken",
                       f"the reason is {len(reason)} characters long")
        source = str(entry.get("upstream_path") or "").strip()
        report.require(bool(source), f"the lock file names the upstream of {name}")
        report.require(source not in upstream_paths,
                       f"the upstream of {name} is not also taken")

    drift = lock.get("drift")
    report.require(isinstance(drift, list) and not drift,
                   "the upstream has not drifted from the pin",
                   f"drifted: {[item.get('path') for item in drift if isinstance(item, dict)]}")

    raw = lock_path.read_text(encoding="utf-8")
    found = [marker for marker in TOKEN_MARKERS if marker in raw]
    report.require(not found, "the lock file carries no token", f"found {found}")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="check the vendored OMCI lock")
    parser.add_argument("--tree", type=Path, default=None,
                        help="the repository root (default: the one this tool is in)")
    args = parser.parse_args()
    root = args.tree.resolve() if args.tree else Path(__file__).resolve().parents[1]
    return analyse(root).report("the vendored OMCI engine")


if __name__ == "__main__":
    sys.exit(main())
