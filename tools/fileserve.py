#!/usr/bin/env python3
"""Serve the flash images to a RAM root, one file per TCP port.

Why not tftp: the busybox that ships in the CI initramfs does list a `tftp` applet, but its
usage strings are stripped (only two `Usage:` blocks survive in the whole 458 KB binary), so
the flags it accepts cannot be established from the artifact - and a wrong flag inside a RAM
root fails silently with no console. `nc IP PORT >file` is the client form that every busybox
implements, so this server needs nothing from the client but a connection: it writes the file
and closes, and closing on EOF is what makes the payload's `nc` terminate deterministically
without a timeout option.

Each port serves exactly one file, in the order passed, so a payload can fetch
kernel/dtb/rootfs without any protocol beyond "connect and read":

	python tools/fileserve.py 5561 staging/trial/flash-uImage 5562 staging/trial/flash-dtb.bin \
	                              5563 staging/trial/flash-rootfs.jffs2

One connection per file is enough, but a round retries, so keep serving until interrupted and
log every byte count - a short send is the truncation this design exists to catch, and the
payload checks md5 before it will write anything.
"""
import os
import socket
import sys
import time


def stamp():
    return time.strftime("%H:%M:%S")


def serve(port, path):
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("0.0.0.0", port))
    srv.listen(4)
    size = os.path.getsize(path)
    print("%s %d <- %s (%d B)" % (stamp(), port, path, size), flush=True)
    return srv, size


def main():
    argv = sys.argv[1:]
    if len(argv) < 2 or len(argv) % 2:
        print(__doc__)
        return 2
    pairs = []
    for i in range(0, len(argv), 2):
        port, path = int(argv[i]), argv[i + 1]
        if not os.path.isfile(path):
            print("missing %s" % path)
            return 1
        srv, size = serve(port, path)
        pairs.append({"port": port, "path": path, "srv": srv, "size": size})
    print("%s ready on %s - Ctrl-C to stop" %
          (stamp(), " ".join(str(p["port"]) for p in pairs)), flush=True)
    # Poll rather than accept on one socket at a time: the payload connects to three ports in
    # sequence, and a single-threaded accept loop would block port A's retry while B is asked.
    while True:
        for p in pairs:
            p["srv"].settimeout(0.2)
            try:
                c, peer = p["srv"].accept()
            except socket.timeout:
                continue
            except OSError as e:
                print("%s %d: %s" % (stamp(), p["port"], e), flush=True)
                continue
            with c:
                with open(p["path"], "rb") as f:
                    data = f.read()
                c.sendall(data)
                c.shutdown(socket.SHUT_WR)
                print("%s sent %d/%d B of %s to %s%s" %
                      (stamp(), len(data), p["size"], os.path.basename(p["path"]), peer[0],
                       "" if len(data) == p["size"] else "  SIZE MISMATCH"), flush=True)
        time.sleep(0.05)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\nstopped", flush=True)
