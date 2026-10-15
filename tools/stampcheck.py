#!/usr/bin/env python3
"""Read/write a fixed DRAM address on the running board, over the ssh pipe.

Used for two things before a control round is spent:
  sample  - what is in a candidate stamp window right now (all-zero / all-0xFF / live data)
  write   - plant a marker, reboot, and read it back to prove Linux never allocates that
            window and that a warm reset does not clear it
  read    - pull a window back and print any printable records in it

/dev/mem reads are permitted on this 4.19 (2 MiB came back over an ssh pipe in 0.48 s).
Everything here is RAM only - no flash is touched. Note the seek/skip values are in
BS-byte blocks, not MiB and not sectors: 0x83d68f00 / 64 = 8582520.

  python tools/stampcheck.py sample 0x83d68f00 [bytes]
  python tools/stampcheck.py write  0x83d68f00 PRE1234 [blocks]
  python tools/stampcheck.py read   0x83d68f00 [bytes]
"""
import gzip
import os
import re
import subprocess
import sys

HOST = os.environ.get("ONU", "192.168.9.1")
KEY = os.path.expanduser(os.environ.get("KEY", "~/.ssh/id_onu"))
BS = 64


def ssh_cmd(extra=None):
    cmd = ["ssh", "-q", "-o", "BatchMode=yes", "-o", "IdentitiesOnly=yes", "-i", KEY,
           "-o", "UserKnownHostsFile=/dev/null", "-o", "StrictHostKeyChecking=no",
           "-o", "ConnectTimeout=6", "root@" + HOST]
    return cmd + list(extra or [])


def run(remote, timeout=60):
    return subprocess.run(ssh_cmd([remote]), capture_output=True, timeout=timeout)


def addr_args(addr):
    a = int(addr, 16) if str(addr).startswith("0x") else int(addr)
    if a % BS:
        sys.exit("address %s is not %d-byte aligned" % (addr, BS))
    return a, a // BS


def main():
    if len(sys.argv) < 3:
        print(__doc__)
        sys.exit(2)
    mode = sys.argv[1]
    addr, seek = addr_args(sys.argv[2])

    if mode == "write":
        tag = (sys.argv[3] if len(sys.argv) > 3 else "MARK")[:32]
        blocks = int(sys.argv[4]) if len(sys.argv) > 4 else 1
        filler = "".join("%02X" % (i % 256) for i in range(blocks * BS))
        rec = ("CTLWR %s @%s %s" % (tag, hex(addr), filler)).encode()[:blocks * BS]
        rec = rec.ljust(blocks * BS, b"\x00")
        p = subprocess.Popen(ssh_cmd([
            "cat >/tmp/stamprec && dd if=/tmp/stamprec of=/dev/mem bs=%d seek=%d "
            "conv=notrunc 2>&1 | tail -2" % (BS, seek)]), stdin=subprocess.PIPE)
        out, err = p.communicate(rec)
        print("wrote %d B at %s (seek=%d, bs=%d) rc=%d %s %s" %
              (len(rec), hex(addr), seek, BS, p.returncode,
               (out or b"").decode(errors="replace").strip(),
               (err or b"").decode(errors="replace").strip()))
        return

    n = int(sys.argv[3]) if len(sys.argv) > 3 else 256
    blocks = max(1, n // BS)
    r = run("dd if=/dev/mem bs=%d skip=%d count=%d 2>/dev/null | gzip -9"
            % (BS, seek, blocks))
    if r.returncode != 0 or not r.stdout:
        print("read failed rc=%d stderr=%s" % (r.returncode, r.stderr[:300].decode(errors="replace")))
        sys.exit(1)
    data = gzip.decompress(r.stdout)
    nz = sum(1 for b in data if b)
    printable = [m.group().decode() for m in re.finditer(rb"[\x20-\x7e]{6,}", data)]
    print("%s: asked %d got %d bytes, %d non-zero, first 32 = %s" %
          (hex(addr), blocks * BS, len(data), nz, data[:32].hex()))
    for s in printable[:8]:
        print("   | " + s)
    if any(("CTLWR" in s) or ("CTLALIVE" in s) or ("STAMPCHECK" in s) for s in printable):
        print("MARKER PRESENT")
    else:
        print("no marker in this window")


if __name__ == "__main__":
    main()
