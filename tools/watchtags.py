#!/usr/bin/env python3
"""Wait for SK-D840N/SR1010 release tags to appear, without touching the REST API.

The unauthenticated api.github.com budget (60 req/h) is too small for polling two
matrix jobs, but a successful run always publishes a release tag, and tags are
readable over `git ls-remote` on port 22, which this PC can reach and which is not
rate limited.

    python tools/watchtags.py [interval_sec] [prefix ...]
"""
import subprocess
import sys
import time

REPO = "git@github.com:woodnaonly/zx279133-ponwrt-build.git"


def tags():
    out = subprocess.run(["git", "ls-remote", "--tags", REPO],
                         capture_output=True, text=True, encoding="utf-8",
                         errors="replace", timeout=120)
    if out.returncode != 0:
        return None
    return sorted(l.split("refs/tags/")[1].rstrip("^{}")
                  for l in out.stdout.splitlines() if "refs/tags/" in l)


def main():
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    args = sys.argv[1:]
    interval = int(args[0]) if args and args[0].isdigit() else 120
    prefixes = args[1:] or ["skd840n_", "sr1010_"]
    seen, t0 = set(), time.time()
    while True:
        cur = tags()
        if cur is None:
            print(f"{time.time()-t0:07.0f}s ls-remote failed", flush=True)
        else:
            fresh = [t for t in cur if t not in seen and any(t.startswith(p) for p in prefixes)]
            for t in fresh:
                seen.add(t)
                print(f"{time.time()-t0:07.0f}s NEW TAG {t}", flush=True)
            if seen:
                print(f"{time.time()-t0:07.0f}s tags so far: "
                      f"{sorted(x for x in seen if any(x.startswith(p) for p in prefixes))}",
                      flush=True)
        time.sleep(interval)


if __name__ == "__main__":
    sys.exit(main())
