#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""The instrument and the judge, end to end, on a synthetic board.

The judge in tools/trialsurvey-verdict.py is fed a transcript.  So far the
transcripts were written by hand, which proves the judge can read a
transcript that looks like the instrument's output - it does not prove the
instrument produces such a transcript.

This runs the real instrument (tools/trialsurvey.sh) against the synthetic
board from tests/sk_trial_survey_board.py, feeds what it printed to the real
judge, and checks that every criterion that the synthetic board can answer
got an answer - and that the right ones failed when the board was broken.

The instrument says in its own header that its paths are variables "so that
the script can be rehearsed against a synthetic tree before it is put on the
board".  This is that rehearsal.

    python3 tests/test_trialsurvey_endtoend.py
"""

import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SURVEY = ROOT / "tools" / "trialsurvey.sh"
VERDICT = ROOT / "tools" / "trialsurvey-verdict.py"
sys.path.insert(0, str(Path(__file__).resolve().parent))

from sk_trial_survey_board import Board, LOS_TRIGGER_FILE_PON  # noqa: E402


def find_sh():
    """A POSIX shell to run the instrument with."""
    found = shutil.which('sh')
    if found:
        return found
    for candidate in (r'C:\Program Files\Git\bin\sh.exe',
                      r'C:\Program Files\Git\usr\bin\sh.exe'):
        if Path(candidate).exists():
            return candidate
    return None


def survey(board, seconds=2, on_second_sample=None, script=None):
    """Run the real instrument against the board and return its transcript.

    `on_second_sample` is called the moment the instrument announces its
    second sample.  That is the exact moment the board has to change: the
    first sample is already taken, and the instrument sleeps before the
    second one.  Watching the output stream is the only trigger that does
    not depend on how fast the machine is - timing the switch against the
    sleep interval instead would put it in the wrong place on a slow host.
    """
    shell = find_sh()
    if not shell:
        raise SystemExit("no POSIX shell to run the instrument with")
    env = board.env()
    proc = subprocess.Popen(
        [shell, str(script or SURVEY), str(seconds)],
        cwd=str(board.root), env=env,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    collected = []

    def reader():
        for line in proc.stdout:
            collected.append(line)
            if on_second_sample is not None and "=== 7." in line:
                on_second_sample()

    t = threading.Thread(target=reader, daemon=True)
    t.start()
    proc.wait()
    t.join(timeout=10)
    err = proc.stderr.read()
    proc.stdout.close()
    proc.stderr.close()
    return "".join(collected), err, proc.returncode


def judge(text):
    """Run the real judge and return {cid: (status, detail)}."""
    proc = subprocess.run([sys.executable, str(VERDICT)],
                           input=text, capture_output=True, text=True)
    if proc.returncode not in (0, 1, 2):
        raise SystemExit("the judge crashed (rc=%d):\n%s"
                         % (proc.returncode, proc.stderr))
    out = {}
    for line in proc.stdout.splitlines():
        m = re.match(r"^VERDICT\s+(\S+)\s+\S+\s+(\S+)\s+(.*)$", line)
        if m:
            out[m.group(1)] = (m.group(2), m.group(3))
    return out, proc.returncode


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args(argv)

    failures = []
    checks = 0
    tmp = Path(tempfile.mkdtemp(prefix="sk-survey-"))
    # The instrument sleeps this long between its two samples.  The board
    # switches from O1 to O5 in the middle of that window.
    INTERVAL = 6

    try:
        # ---- the instrument reads the synthetic board ------------------
        board = Board(tmp / "board").build().start(interval=0.4)
        try:
            text, err, rc = survey(board, seconds=INTERVAL,
                                   on_second_sample=board.advance)
        finally:
            board.shutdown()

        if args.verbose:
            print(text)

        if rc != 0:
            failures.append("the instrument exited %d:\n%s" % (rc, err))
        checks += 1

        # ---- the board has to be in the state the window promised ------
        # If the machine was too slow for the window, the first sample saw
        # the later state.  That has to be reported as a failed expectation
        # about the fixture, not as a broken judge.
        checks += 1
        first_pon = re.search(r"^  eth0/pon_state: (\S+)$", text, re.M)
        if not first_pon:
            failures.append("the instrument did not print the PON state")
        elif first_pon.group(1) != "O1":
            failures.append("the first sample saw pon_state=%s, expected O1 - "
                            "the machine was too slow for the fixture's "
                            "window, so the judge was judged on the wrong "
                            "input" % first_pon.group(1))

        # ---- the judge reads what the instrument printed ----------------
        verdicts, jrc = judge(text)
        if args.verbose:
            for cid, (status, detail) in sorted(verdicts.items()):
                print("  %s %-13s %s" % (cid, status, detail))

        # The synthetic board has light, frames and a ready OMCI channel,
        # so those five criteria have to be answered.
        for cid in ("C1", "C2", "C3", "C4", "C5", "C6"):
            checks += 1
            status = verdicts.get(cid, (None, ""))[0]
            if status != "PASS":
                failures.append("%s is %r, expected PASS (detail: %s)"
                                % (cid, status, verdicts.get(cid, ("", ""))[1]))

        # C7 reads the kernel's symbol table.  The synthetic board carries
        # one, so the criterion has to be answered - and it is the only
        # criterion that can see the drivers themselves.
        checks += 1
        status = verdicts.get("C7", (None, ""))[0]
        if status != "PASS":
            failures.append("C7 is %r, expected PASS (the synthetic board "
                            "carries a symbol table with the four driver "
                            "symbols in it)" % status)

        # ---- and the trigger in effect has to be resolved -------------
        # The kernel writes the trigger in effect in square brackets, so
        # the instrument has to work it out of the trigger file.  It used
        # to print the whole file, and the judge used to compare that
        # whole file with the driver's trigger name - which never matches,
        # so a healthy lamp was reported as not driven by the driver.
        checks += 1
        if "trigger=zx279133-los" not in text:
            failures.append("the instrument did not resolve the trigger in "
                            "effect of the LOS lamp:\n%s" % text)
        checks += 1
        if "trigger=none default" in text:
            failures.append("the instrument printed the whole trigger file "
                            "instead of the trigger in effect:\n%s" % text)
        checks += 1
        if "the 'los' lamp is under the driver's trigger" not in text:
            failures.append("the instrument did not find the lamp under the "
                            "driver's trigger:\n%s" % text)

        # The exit code has to say what happened: every criterion was
        # answered and none of them failed, so the round is complete - 0.
        if jrc != 0:
            failures.append("the judge exited %d, expected 0 (every "
                            "criterion was answered)" % jrc)

        # ---- and the same board with the light gone -------------------
        board2 = Board(tmp / "board2").build().start(interval=0.4)
        board2.set_light(False)
        try:
            text2, err2, rc2 = survey(board2, seconds=INTERVAL,
                                       on_second_sample=board2.advance)
        finally:
            board2.shutdown()
        verdicts2, _ = judge(text2)

        # Without light the lamp is on and the PON state may stay at O1:
        # neither is a failure, and the judge has to say so.
        checks += 1
        status = verdicts2.get("C4", (None, ""))[0]
        if status not in ("N/A", "PASS"):
            failures.append("C4 is %r without light, expected N/A (the PON "
                            "stack raises its LOS flag from the same reading)"
                            % status)
        checks += 1
        status = verdicts2.get("C3", (None, ""))[0]
        if status != "PASS":
            failures.append("C3 is %r without light, expected PASS (no light "
                            "-> lamp on)" % status)

        # ---- and the instrument must really read the signal detect -----
        checks += 1
        if "opsd (gpio 65): high" not in text:
            failures.append("the instrument did not read the optical RX "
                            "signal detect:\n%s" % text)
        checks += 1
        if "opsd (gpio 65): low" not in text2:
            failures.append("the instrument did not read the signal detect "
                            "when it was low:\n%s" % text2)

        # ---- the counters have to move between the two samples --------
        checks += 1
        first = re.search(r"^lan-cpu0\s+up\s+\S+\s+\S+\s+(\d+)", text, re.M)
        second = re.search(r"^  lan-cpu0\s+rx_packets=(\d+)", text, re.M)
        if not first or not second:
            failures.append("the instrument did not print the conduit's "
                            "counters in both samples")
        elif int(second.group(1)) <= int(first.group(1)):
            failures.append("the conduit's counters did not move: %s -> %s"
                            % (first.group(1), second.group(1)))

        # ---- and the gpio pattern has to be the fixed one --------------
        # The instrument used to ask for the literal text "[ *65]" and for
        # "(gpio 65)", so it never found the line and the optical RX signal
        # detect was never read.  Putting that pattern back has to make this
        # check fail - otherwise the check cannot tell the two apart.
        checks += 1
        script = SURVEY.read_text(encoding="utf-8")
        fixed = r'grep -E "\[ *${1} *\]|\(gpio ?${1}\)"'
        old = r'grep -E "\[ *${1}\]|\(gpio ${1}\)"'
        if fixed not in script:
            failures.append("the instrument does not carry the fixed gpio "
                            "pattern, so the signal detect cannot be read")
        else:
            broken = tmp / "trialsurvey-broken.sh"
            broken.write_text(script.replace(fixed, old), encoding="utf-8",
                              newline="\n")
            board3 = Board(tmp / "board3").build().start(interval=0.4)
            try:
                text3, err3, rc3 = survey(board3, seconds=1,
                                          script=broken)
            finally:
                board3.shutdown()
            if "opsd (gpio 65): high" in text3:
                failures.append("the old gpio pattern still reads the signal "
                                "detect, so this check cannot tell the two "
                                "apart")
            if "opsd (gpio 65)" not in text3:
                failures.append("the broken instrument did not even mention "
                                "the signal detect:\n%s" % text3)

        # ---- and a driver that is not in the kernel --------------------
        # The same board with one of the four driver symbols taken out of
        # the kernel's symbol table.  That is what a board whose driver
        # was not built in looks like, and only C7 can see it - so only C7
        # may turn red.  Without this control the check above would pass
        # with a judge that never reads the symbol table at all.
        board4 = Board(tmp / "board4",
                       missing_symbol="zx279133_offload_attach").build()
        board4.start(interval=0.4)
        try:
            text4, err4, rc4 = survey(board4, seconds=INTERVAL,
                                      on_second_sample=board4.advance)
        finally:
            board4.shutdown()
        verdicts4, jrc4 = judge(text4)
        checks += 1
        status = verdicts4.get("C7", (None, ""))[0]
        if status != "FAIL":
            failures.append("C7 is %r when a driver symbol is missing from "
                            "the kernel's symbol table, expected FAIL"
                            % status)
        red = sorted(k for k, v in verdicts4.items() if v[0] == "FAIL")
        checks += 1
        if red != ["C7"]:
            failures.append("a missing driver symbol turned red on %r, "
                            "expected only C7" % red)
        checks += 1
        if jrc4 != 1:
            failures.append("the judge exited %d with a missing driver "
                            "symbol, expected 1" % jrc4)

        # ---- and a lamp that is not on the driver's trigger -----------
        # The same board with another trigger in effect on the LOS lamp.
        # This is the negative control for the whole chain: the lamp is
        # there, the driver is registered, the counters move and the PON
        # state walks - so only C3 may turn red.  It is also the case the
        # instrument used to get wrong: it compared the whole trigger
        # file with the driver's trigger name, and the kernel writes the
        # trigger in effect in square brackets, so the comparison never
        # matched and the lamp was reported as not driven by the driver.
        board5 = Board(tmp / "board5",
                       los_trigger=LOS_TRIGGER_FILE_PON).build()
        board5.start(interval=0.4)
        try:
            text5, err5, rc5 = survey(board5, seconds=INTERVAL,
                                      on_second_sample=board5.advance)
        finally:
            board5.shutdown()
        verdicts5, jrc5 = judge(text5)
        checks += 1
        status = verdicts5.get("C3", (None, ""))[0]
        if status != "FAIL":
            failures.append("C3 is %r when the LOS lamp is on another "
                            "trigger, expected FAIL" % status)
        red = sorted(k for k, v in verdicts5.items() if v[0] == "FAIL")
        checks += 1
        if red != ["C3"]:
            failures.append("a lamp on another trigger turned red on %r, "
                            "expected only C3" % red)
        checks += 1
        if jrc5 != 1:
            failures.append("the judge exited %d with the LOS lamp on "
                            "another trigger, expected 1" % jrc5)

    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    if failures:
        for f in failures:
            print("FAIL: %s" % f)
        print("%d of %d checks failed" % (len(failures), checks))
        return 1
    print("trialsurvey end to end: %d checks, 0 failed" % checks)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
