#!/usr/bin/env python
"""Liveness probe for the SK-D840N trial, through the port the flashed system answers on.

    python tools/watchlan.py [seconds]

Why this exists: with the cable left in a LAN port, mainline has no driver for that
port, so "mainline is running" and "the board hung" look identical from here - but
"the board was reset by the watchdog" does not: the 4.19 system comes back and its
ssh port reopens ~40 s later. So watching this one port answers the question that
decides the trial, without asking anyone to move a cable:

  * port closes and never reopens  -> the jump happened and nothing reset the board
  * port closes then reopens       -> something (watchdog) forced a reboot back to 4.19

Probes are bound to the wired adapter's address. The board used to answer 192.168.1.1,
which is also this PC's WiFi router, so an unbound connect reported that router as if the
board had come back; the lan is 192.168.9.1 now and that subnet exists only on the wired
side, but the binding stays because it is what makes the answer about the board.
"""
import socket
import sys
import time

IP = "192.168.9.1"
SRC = sys.argv[2] if len(sys.argv) > 2 else "192.168.9.190"
SECS = int(sys.argv[1]) if len(sys.argv) > 1 else 420


def up():
    s = socket.socket()
    s.settimeout(0.7)
    try:
        s.bind((SRC, 0))
        s.connect((IP, 22))
        return True
    except OSError:
        return False
    finally:
        s.close()


t0 = time.time()
state = None
while time.time() - t0 < SECS:
    now = up()
    if now != state:
        print("t=+%6.1fs  ssh:%s %s" % (time.time() - t0, IP, "UP" if now else "DOWN"), flush=True)
        state = now
    time.sleep(1.0)
print("done after %ds, last state=%s" % (SECS, "UP" if state else "DOWN"), flush=True)
