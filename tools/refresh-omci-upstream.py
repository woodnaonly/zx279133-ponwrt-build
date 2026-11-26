#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Refresh the vendored OMCI lock against the upstream it was pinned to.

The lock file in the package says which upstream commit the copy was taken
from, and what every file looked like there.  Upstream keeps moving, and a
copy that is three commits behind is not a problem by itself - what is a
problem is a copy that has drifted without anybody knowing, because the
next merge of an upstream change is then a merge into four hundred lines
of our own that nobody remembers writing.

So the drift is looked for here, and written into the lock file.  This
script never touches the vendored sources: taking a new upstream file is
a decision somebody makes, not something a refresh does.  What the
refresh does is record which upstream commit the copy was last compared
with, and which of the vendored files differ from that commit.

The recorded drift is what makes the gate in tools/check-omci-upstream-lock.py
red: a vendored file that no longer matches the upstream it was taken from
has to be looked at before the next build.

The repository is public, so no token is needed.  A token raises the rate
limit; it is read from GITHUB_TOKEN and is never written anywhere.

Usage:
  refresh-omci-upstream.py [--tree ROOT] [--ref REF] [--apply]
"""
import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

PACKAGE = "overlays/package/libs/libzxpon-omci/omci"
LOCK_RELATIVE = PACKAGE + "/UPSTREAM.lock.json"
API = "https://api.github.com/repos/"
AGENT = "sk-d840n-upstream-refresh"


def repository_slug(url):
    """The owner/name of a github URL."""
    text = str(url or "").strip()
    for prefix in ("https://github.com/", "http://github.com/", "git@github.com:"):
        if text.startswith(prefix):
            text = text[len(prefix):]
            break
    text = text.rstrip("/")
    if text.endswith(".git"):
        text = text[:-4]
    return text


def api_get(url, token):
    """One GET against the API, as JSON."""
    request = urllib.request.Request(url, headers={
        "Accept": "application/vnd.github+json",
        "User-Agent": AGENT,
    })
    if token:
        request.add_header("Authorization", "Bearer " + token)
    with urllib.request.urlopen(request, timeout=60) as response:
        return json.loads(response.read().decode("utf-8"))


def blob_at(slug, path, ref, token):
    """The blob id of a path at a ref, or None when it is not there."""
    url = API + slug + "/contents/" + path + "?ref=" + ref
    try:
        return str(api_get(url, token)["sha"])
    except urllib.error.HTTPError as error:
        if error.code == 404:
            return None
        raise


def head_of(slug, ref, token):
    """The commit a ref names."""
    url = API + slug + "/commits/" + ref
    return str(api_get(url, token)["sha"])


def refresh(root, ref, token):
    """What the upstream says now, and what that means for the lock."""
    lock_path = root / LOCK_RELATIVE
    lock = json.loads(lock_path.read_text(encoding="utf-8"))
    slug = repository_slug(lock.get("repository"))
    if not slug:
        raise ValueError("the lock file names no repository")
    head = head_of(slug, ref, token)
    print(f"upstream {slug} at {ref} is {head}")
    drift = []
    for entry in lock.get("files") or []:
        path = str(entry.get("upstream_path") or "")
        pinned = str(entry.get("upstream_blob") or "")
        current = blob_at(slug, path, head, token)
        if current is None:
            print(f"  {entry.get('path')}: GONE upstream")
            drift.append({"path": str(entry.get("path") or ""),
                          "upstream_path": path,
                          "pinned_blob": pinned,
                          "current_blob": None})
            continue
        if current == pinned:
            print(f"  {entry.get('path')}: unchanged")
            continue
        print(f"  {entry.get('path')}: CHANGED {pinned} -> {current}")
        drift.append({"path": str(entry.get("path") or ""),
                      "upstream_path": path,
                      "pinned_blob": pinned,
                      "current_blob": current})
    return lock, head, drift


def main() -> int:
    parser = argparse.ArgumentParser(description="refresh the vendored OMCI lock")
    parser.add_argument("--tree", type=Path, default=None,
                        help="the repository root (default: the one this tool is in)")
    parser.add_argument("--ref", default="main",
                        help="the upstream ref to compare against (default: main)")
    parser.add_argument("--apply", action="store_true",
                        help="write the comparison into the lock file")
    args = parser.parse_args()
    root = args.tree.resolve() if args.tree else Path(__file__).resolve().parents[1]
    token = os.environ.get("GITHUB_TOKEN", "").strip()
    lock, head, drift = refresh(root, args.ref, token)
    if not args.apply:
        print("\nnothing was written; run it again with --apply to record this")
        return 1 if drift else 0
    lock["refreshed_at_commit"] = head
    lock["drift"] = drift
    path = root / LOCK_RELATIVE
    path.write_text(json.dumps(lock, indent=2, ensure_ascii=False) + "\n",
                    encoding="utf-8", newline="\n")
    print(f"\nrecorded {head} in {LOCK_RELATIVE} with {len(drift)} drifted file(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
