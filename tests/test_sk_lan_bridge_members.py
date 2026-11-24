#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Controls for tools/check-sk-lan-bridge-members.py.

The gate says the 7.3 line's sk-bridge puts every panel jack and the conduit
into the LAN bridge, which is what a bridge has to hold for the router to be
reachable from a cable.

A gate that says that has to be sensitive to the thing it names, and the
way to show that is to take the repair away and watch the gate bite:

  * the 6.18 generation's own copy of the script, which cannot repair the
    degenerate bridge at all - the gate has to fail on it and name the
    panel jacks.  This is the control group: the defect this gate checks
    for is a real difference between the two copies, not a difference
    between two texts.
  * the 7.3 copy with the repair taken out of it - the gate has to fail and
    name the members that are missing.
  * the 7.3 copy that adds the panel jacks but forgets the conduit - the
    gate has to name lan-cpu0, because a bridge without the conduit is a
    bridge the router cannot be reached through, however many jacks it has.

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


def run(script, ports=None):
    """Run the gate against one script; return (returncode, stdout)."""
    cmd = [sys.executable, str(GATE), "--script", str(script)]
    if ports is not None:
        cmd += ["--ports", ports]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    return proc.returncode, proc.stdout + proc.stderr


def missing_members(out):
    """The members the gate says are missing, read out of its own message.

    The gate names them, and the control has to check that it names the
    right ones - a gate that says "something is wrong" sends the reader
    hunting.  The list is read out of the message rather than matched as a
    phrase, so that the order it happens to print them in does not matter.
    """
    for line in out.splitlines():
        if "is missing" in line and "after the script ran" in line:
            head = line.split("is missing", 1)[1]
            return head.split("after the script ran", 1)[0]
    return ""


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
        cut = body.replace(
            """			if ! has_panel_jack "$ports"; then
				wait_for_panel_jacks
				for p in $(panel_jacks); do
					if [ -e "/sys/class/net/$p" ]; then
						uci add_list "network.@device[$i].ports=$p"
					fi
				done
				ports=$(uci -q get "network.@device[$i].ports")
			fi
""", "")
        if cut == body:
            failures.append("the repair block was not found in the shipped "
                            "sk-bridge; the control cannot take it away")
        else:
            broken = tmp / "sk-bridge-no-repair"
            broken.write_text(cut, encoding="utf-8", newline="\n")
            rc, out = run(broken)
            checks += 1
            if rc == 0:
                failures.append("the gate passed on a sk-bridge with the "
                                "repair taken out of it")
            for jack in ("lan1", "lan2", "lan3", "lan4"):
                checks += 1
                if jack not in missing_members(out):
                    failures.append("the gate did not name %s for the script "
                                    "without the repair:\n%s" % (jack, out))
            if args.verbose:
                print("no repair: rc=%d %s" % (rc, out.strip()))

        # ---- the conduit forgotten ---------------------------------------
        # The bridge starts with nothing at all here, so that the conduit
        # has to be added by the script: with the degenerate configuration
        # the conduit is already in the list, and a script that forgot to
        # add it would look correct.
        body = SHIPPED.read_text(encoding="utf-8")
        cut = body.replace(
            """			for p in lan0 lan-cpu0; do
				case " $ports " in
				*" $p "*) ;;
				*)
					if [ -e "/sys/class/net/$p" ]; then
						uci add_list "network.@device[$i].ports=$p"
						ports="$ports $p"
					fi
					;;
				esac
			done
""", "")
        if cut == body:
            failures.append("the block that adds lan0 and lan-cpu0 was not "
                            "found in the shipped sk-bridge")
        else:
            broken = tmp / "sk-bridge-no-conduit"
            broken.write_text(cut, encoding="utf-8", newline="\n")
            rc, out = run(broken, ports="")
            checks += 1
            if rc == 0:
                failures.append("the gate passed on a sk-bridge that leaves "
                                "the conduit out of the bridge")
            checks += 1
            missing = missing_members(out)
            if "lan-cpu0" not in missing:
                failures.append("the gate did not name lan-cpu0 for the script "
                                "that leaves the conduit out:\n%s" % out)
            checks += 1
            if "lan0" not in missing:
                failures.append("the gate did not name lan0 for the script "
                                "that leaves the jackless port out:\n%s" % out)
            if args.verbose:
                print("no conduit: rc=%d %s" % (rc, out.strip()))

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
