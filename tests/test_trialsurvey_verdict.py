#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Controls for tools/trialsurvey-verdict.py.

The verdict script turns the trial's read-out table into something a machine
judges.  A table of relations that a machine judges is worth nothing if the
judge cannot tell a healthy board from a broken one, so each control below
breaks exactly one reading and the verdict has to turn red *on that
criterion* - and on no other.

Two controls are the other way round, and they matter more than the rest:

  - with no optical signal the PON state is not supposed to move past O1, so
    a pon_state that stayed at O1 must NOT be reported as a failure.  A judge
    that reports it as one will train the operator to ignore it.
  - with the OMCI channel not ready, nothing is submitted, so the counters
    must NOT be reported as a failure either.

A judge that cries wolf on those two is worse than no judge.

    python3 tests/test_trialsurvey_verdict.py
"""

import argparse
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VERDICT = ROOT / "tools" / "trialsurvey-verdict.py"


def healthy():
    """A transcript of a board that passes every criterion.

    The shape is the one tools/trialsurvey.sh prints: the netdev table, the
    DSA port list, the lamp and the signal detect, the two sysfs attributes,
    the ethtool counters, the same counters a second time, and the kernel's
    own view.
    """
    return """\
=== 1. the netdevs ===
name          operstate  carrier  mtu      rx_packets      rx_bytes        rx_dropped
lan-cpu0      up         1        1500     1200            96000           0
lan1          up         1        1500     300             24000           0
lan2          up         1        1500     150             12000           0
lan3          down       0        1500     0               0               0
lan4          down       0        1500     0               0               0
eth0          up         1        1500     450             36000           0

=== 2. the DSA ports the switch node declares ===
  lan0    present  operstate=up       carrier=1  speed=1000    rx_packets=300        rx_dropped=0
  lan1    present  operstate=up       carrier=1  speed=1000    rx_packets=300        rx_dropped=0
  lan2    present  operstate=up       carrier=1  speed=1000    rx_packets=150        rx_dropped=0
  lan3    present  operstate=down     carrier=0  speed=1000    rx_packets=0          rx_dropped=0
  lan4    present  operstate=down     carrier=0  speed=1000    rx_packets=0          rx_dropped=0

=== 3. the LOS lamp and the optical RX signal detect ===
  led skyworth:red:los        brightness=0    trigger=zx279133-los
  led skyworth:green:link     brightness=1    trigger=none
  opsd (gpio 65): high
  the driver drives the lamp through the 'zx279133-los' trigger:
    light present -> lamp off, no light -> lamp on
  the 'skyworth:red:los' lamp is under the driver's trigger: it is the lamp that answers

=== 4. the PON state ===
  eth0/pon_state: O1

=== 5. the OMCI transport counters ===
  eth0/omci_transport: implemented=1 ready=1 rx_ready=0 mic_ready=0 submitted=0 completed=0 error=0

=== 6. the driver's own statistics (ethtool -S eth0) ===
  eth0:
    tx_invalid_completions   0
    tx_recovery_requests     0
    tx_recovery_restarts     0
    tx_recovery_failures     0

=== 7. the same counters 10s later ===
  lan-cpu0    rx_packets=1400   rx_bytes=112000   rx_dropped=0   operstate=up
  lan1        rx_packets=450    rx_bytes=36000    rx_dropped=0   operstate=up
  lan2        rx_packets=300    rx_bytes=24000    rx_dropped=0   operstate=up
  lan3        rx_packets=0      rx_bytes=0        rx_dropped=0   operstate=down
  lan4        rx_packets=0      rx_bytes=0        rx_dropped=0   operstate=down
  eth0        rx_packets=600    rx_bytes=48000    rx_dropped=0   operstate=up
  eth0/pon_state: O5
  eth0/omci_transport: implemented=1 ready=1 rx_ready=0 mic_ready=0 submitted=12 completed=10 error=0
  -- and the driver's own counters again, so they can be compared with the first reading:
  eth0:
    tx_invalid_completions   0
    tx_recovery_requests     0
    tx_recovery_restarts     0
    tx_recovery_failures     0
  -- and the lamp again, so it can be compared with the first reading:
  led skyworth:red:los        brightness=0    trigger=zx279133-los
  led skyworth:green:link     brightness=1    trigger=none
  opsd (gpio 65): high
  the driver drives the lamp through the 'zx279133-los' trigger:
    light present -> lamp off, no light -> lamp on

=== 8. the kernel's own idea of the drivers ===
  (no zte modules loaded - on this line the drivers are built into the kernel)
  the built-in drivers:
CONFIG_ZTE_ZX279133_ETH=y
CONFIG_ZTE_ZX279133_LAN_CONDUIT=y
CONFIG_ZTE_ZX279133_SWITCH=y
CONFIG_ZTE_ZX279133_FNOFFLOAD=y
  dmesg:
zx279133-lan-conduit: conduit netdev lan-cpu0 registered
zx279133-switch: 9132 switch driver probed

=== 9. the drivers in the kernel's symbol table ===
  kallsyms (/proc/kallsyms):
    zx279133_lan_netdev_open              FOUND
    zx279133_lan_conduit_get_stats64       FOUND
    zx279133_switch_get_tag_protocol       FOUND
    zx279133_offload_attach                FOUND
"""


def without_symbols(text):
    """Drop section 9, so the judge has to fall back to the configuration.

    The judge reads the kernel's symbol table first and the configuration
    only when the table cannot be read.  A control that wants to exercise
    the configuration has to take the table away first.
    """
    i = text.find("=== 9.")
    if i < 0:
        raise SystemExit("the control cannot find section 9")
    return text[:i]


def without_config(text):
    """Drop the CONFIG_ZTE_ZX279133_* lines, so the configuration is gone."""
    out = [line for line in text.splitlines()
           if not line.startswith("CONFIG_ZTE_ZX279133_")]
    return "\n".join(out) + "\n"


def run(text):
    """Run the judge on a transcript and return (status, {cid: status})."""
    proc = subprocess.run(
        [sys.executable, str(VERDICT)],
        input=text, capture_output=True, text=True)
    if proc.returncode not in (0, 1, 2):
        raise SystemExit("the judge crashed (rc=%d):\n%s" % (proc.returncode, proc.stderr))
    statuses = {}
    for line in proc.stdout.splitlines():
        m = re.match(r"^VERDICT\s+(\S+)\s+\S+\s+(\S+)\s", line)
        if m:
            statuses[m.group(1)] = m.group(2)
    return proc.returncode, statuses


def mutate(text, old, new, count=1):
    if text.count(old) < count:
        raise SystemExit("the control cannot find %r in the transcript" % old)
    return text.replace(old, new, count)


# (name, the broken transcript, the criterion that has to turn red)
FAILING = [
    ("conduit-rx-does-not-move",
     # the frames arrive on lan1, the conduit does not count them
     mutate(healthy(), "lan-cpu0    rx_packets=1400", "lan-cpu0    rx_packets=1200"),
     "C1"),
    ("conduit-drops-what-it-received",
     # the conduit counts the frames and throws them away
     mutate(healthy(), "lan-cpu0    rx_packets=1400   rx_bytes=112000   rx_dropped=0",
            "lan-cpu0    rx_packets=1200   rx_bytes=96000   rx_dropped=17"),
     "C1"),
    ("a-dsa-port-is-missing",
     mutate(healthy(),
            "  lan3    present  operstate=down     carrier=0  speed=1000    rx_packets=0          rx_dropped=0",
            "  lan3    MISSING - the 9132 driver did not register this port"),
     "C2"),
    ("the-lamp-is-the-wrong-way-round",
     # there is light, and the lamp is on
     mutate(healthy(), "led skyworth:red:los        brightness=0    trigger=zx279133-los",
            "led skyworth:red:los        brightness=1    trigger=zx279133-los"),
     "C3"),
    ("the-lamp-changed-under-a-steady-signal",
     mutate(healthy(), "led skyworth:red:los        brightness=0    trigger=zx279133-los\n  led skyworth:green:link",
            "led skyworth:red:los        brightness=1    trigger=zx279133-los\n  led skyworth:green:link"),
     "C3"),
    ("the-driver-is-not-driving-the-lamp",
     # The lamp is there, but the trigger in effect is another one.  The
     # kernel writes the trigger in effect in square brackets, so this is
     # what the board really reads - and it is the case the judge used to
     # get wrong: it compared the whole trigger file with the driver's
     # trigger name, which never matches, so a healthy lamp was reported
     # as not driven by the driver.
     mutate(healthy(), "trigger=zx279133-los",
            "trigger=none default rfkill-any rfkill-none timer heartbeat "
            "default-on netdev usbport [zx279133-pon] zx279133-los", 2),
     "C3"),
    ("the-pon-state-does-not-walk-with-light",
     # there is light, and the state stays at O1 in both samples
     mutate(healthy(), "  eth0/pon_state: O5\n", "  eth0/pon_state: O1\n"),
     "C4"),
    ("the-omci-counters-do-not-move-while-ready",
     # the channel was ready in both samples, and nothing was submitted
     mutate(healthy(),
            "ready=1 rx_ready=0 mic_ready=0 submitted=12 completed=10 error=0",
            "ready=1 rx_ready=0 mic_ready=0 submitted=0 completed=0 error=0"),
     "C5"),
    ("a-tx-counter-is-not-published",
     mutate(healthy(), "    tx_recovery_restarts     0",
            "    tx_recovery_restarts     NOT PUBLISHED"),
     "C6"),
    ("a-driver-is-not-built-into-the-kernel",
     # the symbol table is gone, so the configuration is what is left
     mutate(without_symbols(healthy()), "CONFIG_ZTE_ZX279133_SWITCH=y",
            "CONFIG_ZTE_ZX279133_SWITCH=m"),
     "C7"),
    ("a-driver-is-missing-from-the-kernel-config",
     mutate(without_symbols(healthy()), "CONFIG_ZTE_ZX279133_FNOFFLOAD=y\n", ""),
     "C7"),
    ("a-driver-is-not-in-the-kernel-symbol-table",
     # the configuration says all four are built in, and the symbol table
     # says one of them is not there: the table is the stronger of the two,
     # so it is the one that decides
     mutate(healthy(), "    zx279133_offload_attach                FOUND",
            "    zx279133_offload_attach                NOT IN THE SYMBOL TABLE"),
     "C7"),
]

# (name, the transcript, the criterion that must NOT turn red)
NOT_FAILING = [
    ("no-light-so-the-pon-state-may-stay-at-o1",
     mutate(healthy(), "  opsd (gpio 65): high", "  opsd (gpio 65): low")
         .replace("led skyworth:red:los        brightness=0",
                  "led skyworth:red:los        brightness=1")
         .replace("  eth0/pon_state: O5\n", "  eth0/pon_state: O1\n")
         .replace("ready=1 rx_ready=0 mic_ready=0 submitted=12 completed=10 error=0",
                  "ready=0 rx_ready=0 mic_ready=0 submitted=0 completed=0 error=-19"),
     "C4"),
    ("the-trigger-is-written-in-the-kernel's-own-format",
     # The transcript carries the trigger file the way the kernel writes
     # it, with the trigger in effect in square brackets.  The judge has
     # to read the trigger in effect out of it, not take the whole line
     # for it - and the lamp is still the lamp.
     mutate(healthy(), "trigger=zx279133-los",
            "trigger=none default rfkill-any rfkill-none timer heartbeat "
            "default-on netdev usbport [zx279133-los] zx279133-pon", 2),
     "C3"),
    ("the-omci-channel-is-not-ready-so-nothing-is-submitted",
     mutate(healthy(), "ready=1 rx_ready=0 mic_ready=0 submitted=12 completed=10 error=0",
            "ready=0 rx_ready=0 mic_ready=0 submitted=0 completed=0 error=-19"),
     "C5"),
    ("no-traffic-so-the-conduit-need-not-move",
     # nothing moves anywhere: that is not a fault, it is a round with no
     # traffic in it
     re.sub(r"(rx_packets=)(\d+)", lambda m: m.group(1) + "0", healthy())
     .replace("rx_packets=1200", "rx_packets=1200"),
     "C1"),
    ("neither-the-symbol-table-nor-the-configuration",
     # Neither source can be read.  That is not a finding about the board -
     # it is a round that could not be judged, and the judge has to say so.
     without_symbols(without_config(healthy())),
     "C7"),
]


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args(argv)

    failures = []
    checks = 0

    # ---- the healthy board passes everything -------------------------
    rc, st = run(healthy())
    checks += 1
    if st and any(v != "PASS" for v in st.values()):
        failures.append("the healthy transcript did not pass everything: %r" % st)
    if rc != 0:
        failures.append("the healthy transcript exited %d, expected 0" % rc)
    if args.verbose:
        print("healthy: rc=%d %r" % (rc, st))

    # ---- and the configuration is the fallback ------------------------
    # With the symbol table gone, the judge has to fall back to the
    # configuration.  If it did not, the judge would report "could not be
    # judged" on a board whose kernel does carry the drivers.
    checks += 1
    rc, st = run(without_symbols(healthy()))
    if st.get("C7") != "PASS":
        failures.append("without the symbol table the configuration has to "
                        "decide, and C7 is %r" % st.get("C7"))
    if args.verbose:
        print("fallback: rc=%d C7=%r" % (rc, st.get("C7")))

    # ---- each broken board turns red on its own criterion ------------
    for name, text, cid in FAILING:
        checks += 1
        rc, st = run(text)
        if st.get(cid) != "FAIL":
            failures.append("%s: %s is %r, expected FAIL"
                            % (name, cid, st.get(cid)))
        red = sorted(k for k, v in st.items() if v == "FAIL")
        if red != [cid]:
            failures.append("%s: red on %r, expected only %s"
                            % (name, red, cid))
        if args.verbose:
            print("%s: rc=%d red=%r" % (name, rc, red))

    # ---- and the two that must stay quiet ----------------------------
    for name, text, cid in NOT_FAILING:
        checks += 1
        rc, st = run(text)
        if st.get(cid) == "FAIL":
            failures.append("%s: %s turned red but must not" % (name, cid))
        if args.verbose:
            print("%s: rc=%d %s=%r" % (name, rc, cid, st.get(cid)))

    if failures:
        for f in failures:
            print("FAIL: %s" % f)
        print("%d of %d controls failed" % (len(failures), checks))
        return 1
    print("trialsurvey-verdict: %d controls, 0 failed" % checks)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
