#!/usr/bin/env python3
"""Poll one GitHub Actions run and print every job/step state change.

    python tools/watchrun.py <run_id> [interval_sec]

Uses the unauthenticated API (60 req/h), so the default interval is generous.
Exits when the run reaches status=completed and mirrors its conclusion as the
exit code, which lets a caller chain follow-up work on success.
"""
import json
import subprocess
import sys
import time

REPO = "woodnaonly/zx279133-ponwrt-build"


def api(path):
    # encoding must be explicit: this PC's default codepage is cp936, which dies
    # on the UTF-8 bytes GitHub puts in run titles.
    out = subprocess.run(
        ["curl", "-sS", "-m", "60", f"https://api.github.com/repos/{REPO}{path}"],
        capture_output=True, text=True, encoding="utf-8", errors="replace")
    if out.returncode != 0:
        return None
    try:
        return json.loads(out.stdout)
    except json.JSONDecodeError:
        return None


def main():
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    run_id = sys.argv[1]
    interval = int(sys.argv[2]) if len(sys.argv) > 2 else 90
    seen = {}
    t0 = time.time()
    while True:
        run = api(f"/actions/runs/{run_id}")
        # A rate-limit or error payload parses fine but has no 'status'.
        if not run or "status" not in run:
            msg = (run or {}).get("message", "no response")
            print(f"{time.time()-t0:07.0f}s api unusable: {msg} "
                  f"(switch to tools/watchtags.py)", flush=True)
        else:
            # {} and not []: api() returns None when curl fails or the rate limit
            # answers, `or []` then made the next line raise AttributeError on a
            # list, and the watcher died mid-run - measured on run 186, where it
            # printed 1492 s of in_progress and then crashed with the build still
            # in flight, leaving no terminal state for anyone to read.
            jobs = api(f"/actions/runs/{run_id}/jobs") or {}
            line = (f"{time.time()-t0:07.0f}s {run['status']} {run['conclusion']} "
                    f":: {run.get('display_title','')[:50]}")
            print(line, flush=True)
            for j in jobs.get("jobs", []):
                for s in j.get("steps", []):
                    key = (j["name"], s["name"])
                    now = (s["status"], s.get("conclusion"))
                    if seen.get(key) != now:
                        seen[key] = now
                        print(f"    [{j['name']}] {s['name']}: {now[0]} {now[1]}",
                              flush=True)
            if run["status"] == "completed":
                return 0 if run["conclusion"] == "success" else 1
        time.sleep(interval)


if __name__ == "__main__":
    sys.exit(main())
