#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Controls for tools/check-sk-lan-bridge-members.py.

The gate says the 7.3 line's sk-bridge puts the five DSA user ports into
the LAN bridge and leaves the conduit out of it, which is the only list the
kernel accepts for this board's DSA switch.

A gate that says that has to be sensitive to the thing it names, and the
way to show that is to take the repair away and watch the gate bite:

  * the 6.18 generation's own copy of the script, which cannot repair the
    degenerate bridge at all - the gate has to fail on it and name the
    panel jacks.  This is the control group: the defect this gate checks
    for is a real difference between the two copies, not a difference
    between two texts.
  * the 7.3 copy with the repair taken out of it - the gate has to fail and
    name every user port that is missing.
  * the 7.3 copy with the conduit-removal taken out of it - the gate has to
    name lan-cpu0 as still held, because a bridge that holds the conduit
    takes no user port at all.
  * the 7.3 copy, unchanged, on a bridge that already holds the five user
    ports - the gate has to pass, or the script would keep rewriting the
    list on every boot.

    python3 tests/test_sk_lan_bridge_members.py
"""

import argparse
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GATE = ROOT / "tools" / "check-sk-lan-bridge-members.py"
SHIPPED = (ROOT / "overlays-73" / "target" / "linux" / "zte" / "zx279133" /
           "base-files" / "etc" / "init.d" / "sk-bridge")
SHARED = (ROOT / "overlays" / "target" / "linux" / "zte" / "zx279133" /
          "base-files" / "etc" / "init.d" / "sk-bridge")

# The prose the shipped script uses to mark the two blocks this check cuts
# out.  They are comments, so the shipped script carries no test scaffolding,
# and they are unique, so the cut lands where it means to.
REPAIR_START = "# THE DEGENERATE CASE:"
REPAIR_END = "# THE CONDUIT MUST NOT BE IN THE BRIDGE."
CONDUIT_END = "\t\t\tbreak\n"


def run(script, ports=None):
    """Run the gate against one script; return (returncode, stdout)."""
    cmd = [sys.executable, str(GATE), "--script", str(script)]
    if ports is not None:
        cmd += ["--ports", ports]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    return proc.returncode, proc.stdout + proc.stderr


def named_members(out, phrase):
    """The members the gate names, read out of its own message.

    The gate names them, and the control has to check that it names the
    right ones - a gate that says "something is wrong" sends the reader
    hunting.  The list is read out of the message rather than matched as a
    phrase, so that the order it happens to print them in does not matter.
    """
    for line in out.splitlines():
        if phrase in line and "after the script ran" in line:
            head = line.split(phrase, 1)[1]
            return head.split("after the script ran", 1)[0]
    return ""


def missing_members(out):
    """The members the gate says are missing."""
    return named_members(out, "is missing")


def kept_members(out):
    """The members the gate says are still held."""
    return named_members(out, "still holds")


def cut_between(body, start_anchor, end_anchor):
    """Cut the block between two anchors out of the script."""
    i = body.index(start_anchor)
    j = body.index(end_anchor, i + len(start_anchor))
    return body[:i] + body[j:]


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args(argv)

    failures = []
    checks = 0
    tmp = Path(tempfile.mkdtemp(prefix="sk-lan-ctl-"))

    try:
        # ---- the shipped script has to pass -----------------------------
        rc, out = run(SHIPPED)
        checks += 1
        if rc != 0:
            failures.append("the gate did not pass on the 7.3 line's own "
                            "sk-bridge (rc=%d):\n%s" % (rc, out))
        if args.verbose:
            print("shipped: rc=%d %s" % (rc, out.strip()))

        # ---- the script on a bridge that is already right ----------------
        # The repair has to be idempotent: on every boot after the first one
        # the bridge already holds the five user ports, and the script must
        # leave that list alone instead of failing on it.
        rc, out = run(SHIPPED, ports="lan0 lan1 lan2 lan3 lan4")
        checks += 1
        if rc != 0:
            failures.append("the gate did not pass on a bridge that already "
                            "holds the five user ports (rc=%d):\n%s"
                            % (rc, out))
        if args.verbose:
            print("already right: rc=%d %s" % (rc, out.strip()))

        # ---- the 6.18 copy cannot repair it: the control group ----------
        if not SHARED.is_file():
            failures.append("the 6.18 generation's sk-bridge is missing at %s"
                            % SHARED)
        else:
            rc, out = run(SHARED)
            checks += 1
            if rc == 0:
                failures.append("the gate passed on the 6.18 generation's "
                                "sk-bridge, which cannot repair a degenerate "
                                "bridge - the gate does not see the defect")
            for jack in ("lan1", "lan2", "lan3", "lan4"):
                checks += 1
                if jack not in missing_members(out):
                    failures.append("the gate did not name %s when it failed "
                                    "on the 6.18 copy:\n%s" % (jack, out))
            if args.verbose:
                print("shared: rc=%d %s" % (rc, out.strip()))

        # ---- the repair taken out of the 7.3 copy ------------------------
        body = SHIPPED.read_text(encoding="utf-8")
        try:
            cut = cut_between(body, REPAIR_START, REPAIR_END)
        except ValueError as exc:
            failures.append("the repair block was not found in the shipped "
                            "sk-bridge (%s); the control cannot take it away"
                            % exc)
        else:
            broken = tmp / "sk-bridge-no-repair"
            broken.write_text(cut, encoding="utf-8", newline="\n")
            rc, out = run(broken)
            checks += 1
            if rc == 0:
                failures.append("the gate passed on a sk-bridge with the "
                                "repair taken out of it")
            for port in ("lan0", "lan1", "lan2", "lan3", "lan4"):
                checks += 1
                if port not in missing_members(out):
                    failures.append("the gate did not name %s for the script "
                                    "without the repair:\n%s" % (port, out))
            if args.verbose:
                print("no repair: rc=%d %s" % (rc, out.strip()))

        # ---- the conduit-removal taken out of the 7.3 copy --------------
        # This is the block that takes lan-cpu0 out of the list.  A script
        # without it leaves the conduit in the bridge, and a DSA bridge that
        # holds the conduit takes no user port at all - so the gate has to
        # name lan-cpu0 as still held.
        body = SHIPPED.read_text(encoding="utf-8")
        try:
            cut = cut_between(body, REPAIR_END, CONDUIT_END)
        except ValueError as exc:
            failures.append("the conduit-removal block was not found in the "
                            "shipped sk-bridge (%s); the control cannot take "
                            "it away" % exc)
        else:
            broken = tmp / "sk-bridge-no-conduit-removal"
            broken.write_text(cut, encoding="utf-8", newline="\n")
            rc, out = run(broken)
            checks += 1
            if rc == 0:
                failures.append("the gate passed on a sk-bridge that leaves "
                                "the conduit in the LAN bridge")
            checks += 1
            if "lan-cpu0" not in kept_members(out):
                failures.append("the gate did not name lan-cpu0 as still held "
                                "for the script that leaves it in:\n%s" % out)
            if args.verbose:
                print("no conduit removal: rc=%d %s" % (rc, out.strip()))

    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    if failures:
        for f in failures:
            print("FAIL: %s" % f)
        print("%d of %d controls failed" % (len(failures), checks))
        return 1
    print("LAN bridge members: %d controls, 0 failed" % checks)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
