#!/usr/bin/env python3
"""Ask the board one question over the serial console and print the answer.

    python tools/q.py "cat /proc/net/dev"
    python tools/q.py "brctl showmacs br-lan" "ip addr show br-lan"

One command at a time, and nothing long: the console is a 115200 baud PL011
with a sixteen-word receive FIFO, and a burst overruns it - the board drops the
byte that carried the newline, the shell waits for the rest of the line, and
every command after that is swallowed as a continuation.

The answer is whatever the board printed between the moment the command was
handed to the relay and the moment the prompt came back.

WHERE THE CONSOLE FILES ARE

The relay's work directory is the bench's staging area, not this
repository: the relay reads staging/ctl/sercmd.txt and writes
staging/ctl/serial.log, and the scripts that use the console have to find
those two files wherever the bench keeps them.  SKD_CTL points at that
directory; it defaults to the one next to this repository's parent, which
is where the bench keeps it.
"""
import os
import re
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
_default = os.path.join(os.path.dirname(os.path.dirname(HERE)), "staging", "ctl")
CTL = os.environ.get("SKD_CTL", _default)
LOG = os.path.join(CTL, "serial.log")
CMD = os.path.join(CTL, "sercmd.txt")

ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


def log_size():
    try:
        return os.path.getsize(LOG)
    except OSError:
        return 0


def send(cmd, timeout=40, settle=1.2):
    off = log_size()
    with open(CMD, "wb") as fh:
        fh.write(cmd.encode("utf-8", "replace") + b"\r\n")
    t0 = time.time()
    while os.path.exists(CMD) and time.time() - t0 < timeout:
        time.sleep(0.05)
    time.sleep(settle)
    return off


def read_from(off):
    with open(LOG, "rb") as fh:
        fh.seek(off)
        return fh.read().decode("utf-8", "replace")


def main():
    for cmd in sys.argv[1:]:
        # The console is a 115200 baud PL011 with a sixteen-word receive
        # FIFO, and the board drops bytes when the FIFO overruns: the
        # kernel says "ttyAMA ttyAMA0: N input overrun(s)" and the command
        # arrives in pieces - measured 2026-10-25 01:14, "echo pc >
        # /etc/labwan; cat /etc/labwan" reached the board as "echo pc > /e"
        # and "tc/labwan; c", and the instruction was simply lost.
        #
        # The console echoes what it receives, so the echo is the proof:
        # if the echo is not the whole command, the command did not arrive
        # whole and it is sent again.  Short commands and a quiet console
        # make it rare, but it has to be checked rather than hoped for.
        text = ""
        for attempt in range(1, 6):
            off = send(cmd)
            text = ANSI.sub("", read_from(off))
            flat = text.replace("\r", "").replace("\n", " ")
            if cmd.strip() in flat:
                break
            if attempt < 5:
                # The shell is sitting on half a line now, and a line that
                # arrives while it waits is swallowed as a continuation of
                # the broken one.  Ctrl-C throws the half-line away.
                with open(CMD, "wb") as fh:
                    fh.write(b"\x03")
                time.sleep(1.5)
                time.sleep(2.5)
        # Drop the echo of the command and the prompt line.
        lines = [ln.rstrip() for ln in text.splitlines()]
        keep = []
        for ln in lines:
            if ln.startswith("=== >>>"):
                continue
            if ln.strip() in (cmd.strip(), "root@ImmortalWrt:~#", "#"):
                continue
            keep.append(ln)
        while keep and not keep[0]:
            keep.pop(0)
        while keep and not keep[-1]:
            keep.pop()
        print("### %s" % cmd)
        for ln in keep:
            print(ln)
        print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
