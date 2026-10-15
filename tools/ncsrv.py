#!/usr/bin/env python3
"""Sink for the reports a RAM-root payload pushes over TCP.

The payload cannot be queried: the busybox that ships in the CI initramfs has no httpd and
no telnetd, so `/init` opens a connection to this PC and writes its report into it, several
times in a row. Everything else about a round stays blind without that channel - there is no
serial cable - and the alternative (reading LEDs) is what the owner has asked to stop doing.

Records every report with a timestamp to --log and echoes it, so a round can be scored from
the file afterwards, and so a PC that started listening late still gets one of the pushes.

	python tools/ncsrv.py [port] [logfile]
"""
import datetime
import socket
import sys

PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 5555
LOG = sys.argv[2] if len(sys.argv) > 2 else "staging/ctl/reports.log"


def stamp():
    return datetime.datetime.now().strftime("%H:%M:%S")


def main():
    # SO_REUSEADDR matters here: rounds are run back to back, and a listener in TIME_WAIT
    # would otherwise refuse to bind and the round would be scored as silence.
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("0.0.0.0", PORT))
    srv.listen(8)
    print("%s listening on 0.0.0.0:%d -> %s" % (stamp(), PORT, LOG), flush=True)
    n = 0
    while True:
        c, peer = srv.accept()
        c.settimeout(20)
        buf = b""
        try:
            while True:
                d = c.recv(4096)
                if not d:
                    break
                buf += d
        except socket.timeout:
            pass
        except OSError as e:
            print("%s recv error from %s: %s" % (stamp(), peer[0], e), flush=True)
        finally:
            c.close()
        if not buf:
            print("%s empty connection from %s (payload reachable, report not written?)"
                  % (stamp(), peer[0]), flush=True)
            continue
        n += 1
        txt = buf.decode("latin1").replace("\r\n", "\n")
        head = txt.splitlines()[0][:80]
        print("%s report #%d from %s: %s" % (stamp(), n, peer[0], head), flush=True)
        with open(LOG, "a", encoding="utf-8") as f:
            f.write("\n===== %s from %s =====\n%s" % (stamp(), peer[0], txt))
        sys.stdout.flush()


if __name__ == "__main__":
    main()
