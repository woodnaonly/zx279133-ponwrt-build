#!/usr/bin/env python3
"""TCP sink on the PC, used as the peer of the board's busybox `nc IP PORT` sender.

The board's busybox nc is the basic build, so it can only open a connection and
push stdin - that is the board -> PC direction.  This sink counts the bytes so
the board's tx rate can be measured.

    python tools/tcpsink.py --port 9998 --seconds 60
"""
import argparse
import socket
import time


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--host', default='0.0.0.0')
    ap.add_argument('--port', type=int, default=9998)
    ap.add_argument('--seconds', type=float, default=120.0)
    args = ap.parse_args()

    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind((args.host, args.port))
    srv.listen(1)
    srv.settimeout(args.seconds)
    print('listening on %s:%d' % (args.host, args.port), flush=True)

    try:
        conn, peer = srv.accept()
    except socket.timeout:
        print('no connection arrived within %.0fs' % args.seconds)
        return
    print('peer=%s:%d' % peer, flush=True)

    conn.settimeout(args.seconds)
    total = 0
    t0 = time.monotonic()
    deadline = t0 + args.seconds
    while time.monotonic() < deadline:
        try:
            chunk = conn.recv(1 << 20)
        except socket.timeout:
            break
        if not chunk:
            break
        total += len(chunk)
    elapsed = time.monotonic() - t0
    conn.close()
    srv.close()
    print('bytes=%d seconds=%.1f Mbit=%d Mbps=%.1f' %
          (total, elapsed, total * 8 / 1e6, total * 8 / elapsed / 1e6))


if __name__ == '__main__':
    main()
