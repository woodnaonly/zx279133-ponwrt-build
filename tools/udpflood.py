#!/usr/bin/env python3
"""UDP flood sender for the SK-D840N bench.

The board has no iperf3 and its busybox nc is the basic build (no -u/-l/-p), so the
load generator lives here on the PC and the board is the target.

    python tools/udpflood.py --host 192.168.9.1 --port 9999 --threads 4 --seconds 60

Prints the offered rate so it can be compared with the link speed and with the
board's own /proc/net/dev counters.
"""
import argparse
import socket
import threading
import time


def worker(host, port, payload, stop_at, out, idx):
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 4 << 20)
    dst = (host, port)
    n = 0
    b = 0
    err = 0
    while time.monotonic() < stop_at:
        try:
            s.sendto(payload, dst)
            n += 1
            b += len(payload)
        except OSError:
            err += 1
    s.close()
    out[idx] = (n, b, err)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--host', default='192.168.9.1')
    ap.add_argument('--port', type=int, default=9999)
    ap.add_argument('--size', type=int, default=1472, help='UDP payload bytes (no IP fragmentation)')
    ap.add_argument('--threads', type=int, default=4)
    ap.add_argument('--seconds', type=float, default=60.0)
    args = ap.parse_args()

    payload = b'\x00' * args.size
    out = [None] * args.threads
    stop_at = time.monotonic() + args.seconds
    threads = []
    for i in range(args.threads):
        t = threading.Thread(target=worker,
                             args=(args.host, args.port, payload, stop_at, out, i))
        t.start()
        threads.append(t)

    t0 = time.monotonic()
    for t in threads:
        t.join()
    elapsed = time.monotonic() - t0

    pkts = sum(o[0] for o in out)
    byts = sum(o[1] for o in out)
    errs = sum(o[2] for o in out)
    mbps = byts * 8 / elapsed / 1e6
    print('host=%s:%d size=%d threads=%d seconds=%.1f' %
          (args.host, args.port, args.size, args.threads, elapsed))
    print('packets=%d bytes=%d errors=%d' % (pkts, byts, errs))
    print('offered_Mbps=%.1f offered_pps=%.0f' % (mbps, pkts / elapsed))


if __name__ == '__main__':
    main()
