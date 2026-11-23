#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Turn a trialsurvey.sh transcript into a verdict.

tools/trialsurvey.sh reads the board and prints what it saw.  The reading is
raw: a table of netdevs, a list of DSA ports, a lamp and a GPIO, two sysfs
attributes, four driver counters.  What it does not do is say which of those
readings mean the board is working, because that judgement needs the relations
between them - and a relation is exactly what a person reading a table of
numbers under time pressure gets wrong.

This script makes those relations explicit.  It reads a transcript (from a
file or on stdin) and prints one line per criterion, each with its own stable
id, its own verdict and the evidence it was taken from.

The criteria are the ones the trial's read-out table lists (docs/101, section
8.8.4), and they are coupled on purpose:

  C1  the LAN conduit's RX counters move
  C2  the four DSA user ports are there
  C3  the LOS lamp follows the optical RX signal detect
  C4  pon_state walks from O1 towards O5
  C5  omci_transport's submitted and completed counters move
  C6  the driver publishes the four TX isolation counters
  C7  the kernel's own idea of the drivers agrees

Two of them are only meaningful next to the lamp:

  - the lamp is off when there is light, so a lamp that is off while there is
    no light is a fault, and a lamp that is on while there is light is a
    fault; but with no light at all the lamp says nothing on its own.
  - the PON state machine raises its LOS flag from the same reading, so with
    no light the state is not supposed to move past O1.  A pon_state that
    does not move is therefore NOT a finding unless the lamp says there was
    light.

A criterion that cannot be judged says so (INCONCLUSIVE) instead of guessing.
That distinction matters: "the lamp is off" on a board with no optical signal
is not the same finding as "the lamp is off on a board that has signal".

Exit status:
  0  every criterion passed
  1  at least one criterion failed
  2  nothing failed, but at least one criterion could not be judged
"""

import argparse
import re
import sys
from pathlib import Path

# The verdict vocabulary.  A criterion is never anything else.
PASS = "PASS"
FAIL = "FAIL"
UNKNOWN = "INCONCLUSIVE"
NA = "N/A"

# The criteria, in the order they are reported.  The id is the stable name a
# control refers to, so it must not change.
CRITERIA = (
    ("C1", "conduit-rx"),
    ("C2", "dsa-ports"),
    ("C3", "los-lamp"),
    ("C4", "pon-state"),
    ("C5", "omci-transport"),
    ("C6", "tx-isolation-stats"),
    ("C7", "driver-registration"),
)

# The conduit's netdev.  The driver names it "lan-cpu%d" and the kernel
# resolves the %d when the device is registered, so the published name is
# lan-cpu0.
CONDUIT = "lan-cpu0"

# The DSA user ports the switch node declares.
DSA_PORTS = ("lan0", "lan1", "lan2", "lan3", "lan4")

# The lamp the driver drives, and the trigger it drives it through.
LOS_TRIGGER = "zx279133-los"

# The four TX isolation counters, and the order the gate expects them in.
TX_STATS = (
    "tx_invalid_completions",
    "tx_recovery_requests",
    "tx_recovery_restarts",
    "tx_recovery_failures",
)

# The PON states, in the order they are walked through.  The values are not
# comparable as numbers - O2-3 is numbered 23 in the enum but sits between O1
# and O4 - so the order is spelled out.
PON_ORDER = {"O1": 0, "O2-3": 1, "O4": 2, "O5": 3, "O6": 4, "O7": 5}

# The marker that separates the first sample from the second one.
SECOND_MARKER = re.compile(r"^===\s+7\.\s+the same counters")


class Verdict:
    """One criterion's outcome, with the evidence that produced it."""

    def __init__(self, cid, name, status, detail, evidence=None):
        self.cid = cid
        self.name = name
        self.status = status
        self.detail = detail
        self.evidence = evidence or []

    def line(self):
        ev = ""
        if self.evidence:
            ev = "  [%s]" % "; ".join(self.evidence)
        return "VERDICT %-3s %-20s %-13s %s%s" % (
            self.cid, self.name, self.status, self.detail, ev)


class Survey:
    """The parsed transcript.

    Every reading keeps the line it came from, so a verdict can show its own
    evidence.  A verdict that cannot show where its numbers came from is a
    verdict nobody can check.
    """

    def __init__(self, text):
        self.lines = text.splitlines()
        split = len(self.lines)
        for i, line in enumerate(self.lines):
            if SECOND_MARKER.match(line.strip()):
                split = i
                break
        self.first = self.lines[:split]
        self.second = self.lines[split:]
        # The line numbers a verdict shows have to be the numbers in the
        # file the operator is looking at, not the numbers inside one half
        # of it.  Every reader below therefore gets the offset of its half.
        self.first_at = 0
        self.second_at = split

        self.netdevs_first = self._netdev_table(self.first, self.first_at)
        self.netdevs_second = self._netdev_table(self.second, self.second_at)
        self.ports = self._dsa_ports(self.first, self.first_at)
        self.lamp_first = self._lamp(self.first, self.first_at)
        self.lamp_second = self._lamp(self.second, self.second_at)
        self.pon_first = self._attr(self.first, "pon_state", self.first_at)
        self.pon_second = self._attr(self.second, "pon_state", self.second_at)
        self.omci_first = self._attr(self.first, "omci_transport", self.first_at)
        self.omci_second = self._attr(self.second, "omci_transport", self.second_at)
        self.stats_first = self._ethtool(self.first, self.first_at)
        self.stats_second = self._ethtool(self.second, self.second_at)
        # The kernel's own view is section 8, which comes after section 7 -
        # so it is in the second half of the transcript.
        self.config = self._kernel_config(self.second, self.second_at)
        self.dmesg = self._dmesg(self.second, self.second_at)

    # ---- the netdev table (section 1 and section 7) -------------------

    def _netdev_table(self, lines, at):
        out = {}
        for lineno, line in enumerate(lines, 1):
            s = line.strip()
            if not s or s.startswith("===") or s.startswith("name "):
                continue
            if s.startswith("name "):
                continue
            # section 7 prints "  name rx_packets=... rx_bytes=... ..."
            m = re.match(r"^(\S+)\s+rx_packets=", s)
            if m:
                vals = dict(re.findall(r"(\w+)=(\S+)", s))
                out[m.group(1)] = {"line": at + lineno, "raw": s,
                                   "rx_packets": vals.get("rx_packets"),
                                   "rx_bytes": vals.get("rx_bytes"),
                                   "rx_dropped": vals.get("rx_dropped"),
                                   "operstate": vals.get("operstate"),
                                   "carrier": None}
                continue
            # section 1 prints a plain column table
            if s.startswith("led ") or s.startswith("opsd ") or s.startswith("the "):
                continue
            parts = s.split()
            if len(parts) < 7:
                continue
            name = parts[0]
            if not re.match(r"^[a-z0-9_.:-]+$", name):
                continue
            if not parts[3].isdigit() and parts[3] != "?":
                continue
            out[name] = {"line": at + lineno, "raw": s,
                         "operstate": parts[1],
                         "carrier": parts[2],
                         "mtu": parts[3],
                         "rx_packets": parts[4],
                         "rx_bytes": parts[5],
                         "rx_dropped": parts[6]}
        return out

    # ---- the DSA port list (section 2) --------------------------------

    def _dsa_ports(self, lines, at):
        out = {}
        for lineno, line in enumerate(lines, 1):
            s = line.strip()
            m = re.match(r"^(lan\d)\s+(present|MISSING)\b(.*)$", s)
            if m:
                out[m.group(1)] = {"present": m.group(2) == "present",
                                   "line": at + lineno, "raw": s}
        return out

    # ---- the lamp and the trigger (section 3 and section 7) -----------

    def _lamp(self, lines, at):
        lamps = {}
        opsd = None
        opsd_line = None
        for lineno, line in enumerate(lines, 1):
            s = line.strip()
            m = re.match(r"^led\s+(\S+)\s+brightness=(\S+)\s+trigger=(\S+)", s)
            if m:
                lamps[m.group(1)] = {"brightness": m.group(2),
                                     "trigger": m.group(3),
                                     "line": at + lineno, "raw": s}
                continue
            m = re.match(r"^opsd \(gpio \d+\):\s*(.*)$", s)
            if m:
                opsd = m.group(1).strip()
                opsd_line = at + lineno
        return {"lamps": lamps, "opsd": opsd, "opsd_line": opsd_line}

    # ---- the two sysfs attributes (sections 4, 5 and 7) --------------

    def _attr(self, lines, name, at):
        for lineno, line in enumerate(lines, 1):
            s = line.strip()
            m = re.match(r"^(\S+)/%s:\s*(.*)$" % re.escape(name), s)
            if m:
                return {"value": m.group(2).strip(), "line": at + lineno, "raw": s}
        return None

    # ---- the ethtool statistics (sections 6 and 7) --------------------

    def _ethtool(self, lines, at):
        out = {"stats": {}, "no_ethtool": False, "failed": False, "line": None}
        for lineno, line in enumerate(lines, 1):
            s = line.strip()
            if s.startswith("(no ethtool in this image"):
                out["no_ethtool"] = True
                out["line"] = at + lineno
                continue
            if s.startswith("(ethtool -S"):
                out["failed"] = True
                out["line"] = at + lineno
                continue
            m = re.match(r"^(tx_\w+)\s+(\S+)$", s)
            if m:
                out["stats"][m.group(1)] = {"value": m.group(2), "line": at + lineno}
        return out

    # ---- the kernel's own idea of the drivers (section 8) -------------

    def _kernel_config(self, lines, at):
        """The CONFIG_ZTE_ZX279133_* lines the survey prints.

        This is the reliable part of section 8: it comes from
        /proc/config.gz, and the four symbols have to be =y on this line
        because the drivers are built into the kernel.  The dmesg lines
        beside it are not reliable evidence of anything on their own (see
        _dmesg), so they are read separately.
        """
        out = {"configs": {}, "available": False, "not_found": False, "line": None}
        for lineno, line in enumerate(lines, 1):
            s = line.strip()
            if s.startswith("(not found in /proc/config.gz)"):
                out["not_found"] = True
                out["line"] = at + lineno
                continue
            m = re.match(r"^(CONFIG_ZTE_ZX279133_\w+)=(.*)$", s)
            if m:
                out["configs"][m.group(1)] = {"value": m.group(2), "line": at + lineno}
                out["available"] = True
        return out

    def _dmesg(self, lines, at):
        """The dmesg lines the survey prints.

        The survey pipes dmesg through grep and tail, so the pipeline's exit
        status is tail's - which means "dmesg could not be read" and "grep
        matched nothing" both end up as an empty block.  The two cannot be
        told apart from the transcript, so an empty block is reported as
        unknown and never as a failure.
        """
        found = []
        in_dmesg = False
        for lineno, line in enumerate(lines, 1):
            s = line.strip()
            if s == "dmesg:":
                in_dmesg = True
                continue
            if in_dmesg and s:
                found.append((at + lineno, s))
        return {"lines": found}


def as_int(value):
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def kv_of(text):
    """Pull key=value pairs out of a sysfs attribute's text."""
    out = {}
    for k, v in re.findall(r"(\w+)=(\S+)", text or ""):
        out[k] = v
    return out


def judge(survey):
    verdicts = []

    # ---- C1: the conduit's RX counters move --------------------------
    v = Verdict("C1", "conduit-rx", UNKNOWN, "")
    a = survey.netdevs_first.get(CONDUIT)
    b = survey.netdevs_second.get(CONDUIT)
    if a is None:
        v.status = FAIL
        v.detail = ("the conduit netdev %s is not in the netdev table - "
                    "the 9132 or the conduit driver did not register" % CONDUIT)
        v.evidence = ["no %s in section 1" % CONDUIT]
    elif b is None:
        v.status = UNKNOWN
        v.detail = ("the second sample carries no %s, so nothing can be "
                    "compared with the first" % CONDUIT)
    else:
        first = as_int(a.get("rx_packets"))
        second = as_int(b.get("rx_packets"))
        if first is None or second is None:
            v.status = UNKNOWN
            v.detail = "the conduit's rx_packets is not a number in one of the samples"
        elif second > first:
            v.status = PASS
            v.detail = ("rx_packets %d -> %d (+%d)"
                        % (first, second, second - first))
            v.evidence = ["L%d" % a["line"], "L%d" % b["line"]]
            dropped = as_int(b.get("rx_dropped"))
            if dropped is not None and dropped > as_int(a.get("rx_dropped") or 0):
                v.detail += ("; rx_dropped also moved (%s -> %s)"
                             % (a.get("rx_dropped"), b.get("rx_dropped")))
        else:
            moving = []
            for name in sorted(set(survey.netdevs_first) & set(survey.netdevs_second)):
                f = as_int(survey.netdevs_first[name].get("rx_packets"))
                s = as_int(survey.netdevs_second[name].get("rx_packets"))
                if f is not None and s is not None and s > f:
                    moving.append(name)
            # A conduit whose rx_dropped moves has seen frames and thrown
            # them away: that is traffic too, and it is the shape the trial's
            # read-out table calls out ("a counter that moves while
            # rx_packets does not says the frames are being dropped instead
            # of handed over").
            drop_first = as_int(a.get("rx_dropped"))
            drop_second = as_int(b.get("rx_dropped"))
            dropped = (drop_first is not None and drop_second is not None
                       and drop_second > drop_first)
            if moving or dropped:
                v.status = FAIL
                if dropped and not moving:
                    why = ("its rx_dropped moved (%d -> %d) while rx_packets "
                           "did not - the frames are being dropped instead of "
                           "handed over" % (drop_first, drop_second))
                elif dropped:
                    why = ("its rx_dropped moved (%d -> %d) while rx_packets "
                           "did not - the frames are being dropped instead of "
                           "handed over" % (drop_first, drop_second))
                else:
                    why = ("the frames arrive on %s but do not reach the "
                           "conduit" % ", ".join(moving))
                v.detail = "rx_packets did not move (%d -> %d) while %s" % (
                    first, second, why)
                v.evidence = ["L%d" % a["line"], "L%d" % b["line"]]
            else:
                v.status = UNKNOWN
                v.detail = ("rx_packets did not move (%d -> %d), and no other "
                            "netdev moved either - there was no traffic to "
                            "measure" % (first, second))
                v.evidence = ["L%d" % a["line"], "L%d" % b["line"]]
    verdicts.append(v)

    # ---- C2: the four DSA user ports are there ------------------------
    v = Verdict("C2", "dsa-ports", UNKNOWN, "")
    missing = [p for p in DSA_PORTS
               if p not in survey.ports or not survey.ports[p]["present"]]
    if not survey.ports:
        v.status = UNKNOWN
        v.detail = "section 2 lists no DSA port at all"
    elif missing:
        v.status = FAIL
        v.detail = ("missing: %s - the 9132 driver did not register %s"
                    % (", ".join(missing),
                       "that port" if len(missing) == 1 else "those ports"))
        v.evidence = ["L%d" % survey.ports[p]["line"] for p in missing if p in survey.ports]
    else:
        v.status = PASS
        v.detail = "all of %s are present" % " ".join(DSA_PORTS)
        v.evidence = ["L%d" % survey.ports[p]["line"] for p in DSA_PORTS]
    verdicts.append(v)

    # ---- C3: the LOS lamp follows the optical RX signal detect --------
    v = Verdict("C3", "los-lamp", UNKNOWN, "")
    driven = [name for name, d in survey.lamp_first["lamps"].items()
              if d["trigger"] == LOS_TRIGGER]
    driven2 = [name for name, d in survey.lamp_second["lamps"].items()
               if d["trigger"] == LOS_TRIGGER]
    opsd = survey.lamp_first["opsd"]
    opsd2 = survey.lamp_second["opsd"]
    if not driven and not driven2:
        v.status = FAIL
        v.detail = ("no lamp is under the driver's '%s' trigger, so the "
                    "driver is not driving the lamp" % LOS_TRIGGER)
    elif opsd is None or opsd2 is None:
        v.status = UNKNOWN
        v.detail = ("the optical RX signal detect was not read, so the lamp "
                    "cannot be judged against it")
    elif opsd != opsd2:
        v.status = UNKNOWN
        v.detail = ("the signal detect changed between the samples (%s -> %s), "
                    "so the lamp cannot be judged against it"
                    % (opsd, opsd2))
    elif opsd not in ("high", "low"):
        v.status = UNKNOWN
        v.detail = "the signal detect reads %r, which is not high or low" % opsd
    else:
        name = (driven or driven2)[0]
        first = survey.lamp_first["lamps"].get(name)
        second = survey.lamp_second["lamps"].get(name)
        if first is None or second is None:
            v.status = UNKNOWN
            v.detail = "the lamp %s was not read in both samples" % name
        else:
            b1, b2 = first["brightness"], second["brightness"]
            if b1 != b2:
                v.status = FAIL
                v.detail = ("the lamp's brightness changed between the "
                            "samples (%s -> %s) while the signal detect did "
                            "not" % (b1, b2))
                v.evidence = ["L%d" % first["line"], "L%d" % second["line"]]
            else:
                lit = b1 not in ("0", "")
                want_lit = (opsd == "low")
                if lit == want_lit:
                    v.status = PASS
                    v.detail = ("signal detect %s, lamp brightness %s (%s)"
                                % (opsd, b1,
                                   "no light -> lamp on" if want_lit
                                   else "light -> lamp off"))
                    v.evidence = ["L%d" % first["line"],
                                  "L%d" % survey.lamp_first["opsd_line"]]
                else:
                    v.status = FAIL
                    v.detail = ("signal detect %s but the lamp's brightness is "
                                "%s (%s) - the driver has it the other way "
                                "round"
                                % (opsd, b1,
                                   "light -> lamp off" if want_lit
                                   else "no light -> lamp on"))
                    v.evidence = ["L%d" % first["line"],
                                  "L%d" % survey.lamp_first["opsd_line"]]
    verdicts.append(v)

    # ---- C4: pon_state walks from O1 towards O5 ------------------------
    v = Verdict("C4", "pon-state", UNKNOWN, "")
    if survey.pon_first is None and survey.pon_second is None:
        v.status = FAIL
        v.detail = ("there is no pon_state attribute - the PON stack did not "
                    "attach to a netdev")
    else:
        opsd = survey.lamp_first["opsd"]
        if opsd is None:
            v.status = UNKNOWN
            v.detail = ("the optical RX signal detect was not read, and the "
                        "PON state is only meaningful next to it")
        elif opsd == "low":
            v.status = NA
            v.detail = ("no light: the PON stack raises its LOS flag from the "
                        "same reading, so a state that does not move past O1 "
                        "is expected here")
        elif opsd != "high":
            v.status = UNKNOWN
            v.detail = "the signal detect reads %r, which is not high or low" % opsd
        else:
            p1 = (survey.pon_first or {}).get("value", "")
            p2 = (survey.pon_second or {}).get("value", "")
            o1 = PON_ORDER.get(p1)
            o2 = PON_ORDER.get(p2)
            if o1 is None or o2 is None:
                v.status = UNKNOWN
                v.detail = ("the PON state reads %r / %r, which is not one of "
                            "%s" % (p1, p2, " ".join(PON_ORDER)))
            elif o1 > 0:
                v.status = PASS
                v.detail = "the PON state is already at %s" % p1
                v.evidence = ["L%d" % (survey.pon_first or {}).get("line", 0)]
            elif o2 > o1:
                v.status = PASS
                v.detail = "the PON state walked %s -> %s" % (p1, p2)
                v.evidence = ["L%d" % (survey.pon_first or {}).get("line", 0),
                              "L%d" % (survey.pon_second or {}).get("line", 0)]
            else:
                v.status = FAIL
                v.detail = ("there is light but the PON state stayed at %s - "
                            "the state machine did not walk" % p2)
                v.evidence = ["L%d" % (survey.pon_first or {}).get("line", 0),
                              "L%d" % (survey.pon_second or {}).get("line", 0)]
    verdicts.append(v)

    # ---- C5: omci_transport's counters move ---------------------------
    v = Verdict("C5", "omci-transport", UNKNOWN, "")
    if survey.omci_first is None and survey.omci_second is None:
        v.status = FAIL
        v.detail = ("there is no omci_transport attribute - the PON stack did "
                    "not attach to a netdev")
    else:
        kv1 = kv_of((survey.omci_first or {}).get("value", ""))
        kv2 = kv_of((survey.omci_second or {}).get("value", ""))
        s1 = as_int(kv1.get("submitted"))
        c1 = as_int(kv1.get("completed"))
        s2 = as_int(kv2.get("submitted"))
        c2 = as_int(kv2.get("completed"))
        r1 = kv1.get("ready")
        r2 = kv2.get("ready")
        if None in (s1, c1, s2, c2):
            v.status = UNKNOWN
            v.detail = "the omci_transport attribute does not carry the counters"
        elif s2 > s1 or c2 > c1:
            v.status = PASS
            v.detail = ("submitted %d -> %d, completed %d -> %d"
                        % (s1, s2, c1, c2))
            v.evidence = ["L%d" % (survey.omci_first or {}).get("line", 0),
                          "L%d" % (survey.omci_second or {}).get("line", 0)]
        elif r1 is None and r2 is None:
            v.status = FAIL
            v.detail = ("the attribute carries no ready field - the image's "
                        "driver is not the one that publishes this")
        elif r1 != "1" or r2 != "1":
            # The channel has to have been ready in BOTH samples.  A channel
            # that was ready in the first sample and not in the second is a
            # channel that went away, and a channel that was never ready
            # submits nothing - neither is a queue that failed to deliver.
            v.status = NA
            v.detail = ("the OMCI channel was not ready in both samples "
                        "(ready=%s then ready=%s), so nothing is submitted - "
                        "the counters are not supposed to move" % (r1, r2))
            v.evidence = ["L%d" % (survey.omci_first or {}).get("line", 0)]
        else:
            v.status = FAIL
            v.detail = ("the channel was ready but submitted %d -> %d and "
                        "completed %d -> %d" % (s1, s2, c1, c2))
            v.evidence = ["L%d" % (survey.omci_first or {}).get("line", 0),
                          "L%d" % (survey.omci_second or {}).get("line", 0)]
    verdicts.append(v)

    # ---- C6: the four TX isolation counters are published -------------
    v = Verdict("C6", "tx-isolation-stats", UNKNOWN, "")
    if survey.stats_first["no_ethtool"] or survey.stats_second["no_ethtool"]:
        v.status = UNKNOWN
        v.detail = ("there is no ethtool in the image, so the TX isolation "
                    "counters cannot be read here")
    elif survey.stats_first["failed"] or survey.stats_second["failed"]:
        v.status = FAIL
        v.detail = ("ethtool -S on the interface failed - this interface "
                    "publishes no driver statistics")
    else:
        absent = [n for n in TX_STATS
                  if n not in survey.stats_first["stats"]
                  or survey.stats_first["stats"][n]["value"] == "NOT PUBLISHED"]
        if absent:
            v.status = FAIL
            v.detail = ("not published: %s - the image's driver is not the "
                        "one that carries the TX isolation counters"
                        % ", ".join(absent))
            v.evidence = ["L%d" % survey.stats_first["stats"][n]["line"]
                          for n in absent if n in survey.stats_first["stats"]]
        else:
            v.status = PASS
            v.detail = "all four names are published"
            v.evidence = ["L%d" % survey.stats_first["stats"][n]["line"]
                          for n in TX_STATS]
    verdicts.append(v)

    # ---- C7: the kernel's own idea of the drivers ---------------------
    v = Verdict("C7", "driver-registration", UNKNOWN, "")
    if survey.config["not_found"] or not survey.config["available"]:
        v.status = UNKNOWN
        v.detail = ("/proc/config.gz carries no CONFIG_ZTE_ZX279133_* line, so "
                    "the kernel's own view cannot be read")
    else:
        wanted = ("CONFIG_ZTE_ZX279133_ETH", "CONFIG_ZTE_ZX279133_LAN_CONDUIT",
                  "CONFIG_ZTE_ZX279133_SWITCH", "CONFIG_ZTE_ZX279133_FNOFFLOAD")
        missing = [w for w in wanted
                   if w not in survey.config["configs"]]
        not_y = [w for w in wanted
                 if w in survey.config["configs"]
                 and survey.config["configs"][w]["value"] != "y"]
        if missing:
            v.status = FAIL
            v.detail = ("not built into the kernel: %s - the image is not the "
                        "one this line builds" % ", ".join(missing))
            v.evidence = ["L%d" % survey.config["line"]] if survey.config["line"] else []
        elif not_y:
            v.status = FAIL
            v.detail = ("not =y: %s" % ", ".join(
                "%s=%s" % (w, survey.config["configs"][w]["value"]) for w in not_y))
            v.evidence = ["L%d" % survey.config["configs"][w]["line"] for w in not_y]
        else:
            v.status = PASS
            v.detail = "all four drivers are built into the kernel"
            v.evidence = ["L%d" % survey.config["configs"][w]["line"]
                          for w in wanted if w in survey.config["configs"]]
            hits = [n for n, s in survey.dmesg["lines"]
                    if re.search(r"9132|zx279133|lan conduit|nppu", s, re.I)]
            if hits:
                v.detail += " (%d kernel log lines mention them)" % len(hits)
    verdicts.append(v)

    return verdicts


def summarise(verdicts):
    counts = {PASS: 0, FAIL: 0, UNKNOWN: 0, NA: 0}
    for v in verdicts:
        counts[v.status] = counts.get(v.status, 0) + 1
    parts = ["%d %s" % (counts[k], k) for k in (PASS, FAIL, UNKNOWN, NA) if counts[k]]
    return "SUMMARY " + ", ".join(parts)


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="judge a trialsurvey.sh transcript")
    ap.add_argument("--input", type=Path, default=None,
                    help="the transcript to read (default: stdin)")
    args = ap.parse_args(argv)

    if args.input is not None:
        text = args.input.read_text(encoding="utf-8", errors="replace")
    else:
        text = sys.stdin.read()

    survey = Survey(text)
    verdicts = judge(survey)
    for v in verdicts:
        print(v.line())
    print(summarise(verdicts))

    if any(v.status == FAIL for v in verdicts):
        return 1
    if any(v.status == UNKNOWN for v in verdicts):
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
