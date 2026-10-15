#!/usr/bin/env python
"""Pull the pstore/ramoops region out of the flashed system and print what mainline wrote.

There is no serial console on this board, so a kexec round that does not come back leaves
no evidence at all. The trial image reserves a no-map ramoops region (board DTS:
0x9c000000, 2 MiB) and boots without masking CRM 0x10e10208, so the vendor's already-armed
zx_wdt resets the chip ~15-20 s after the jump. A watchdog reset is warm: DRAM keeps its
contents, the flashed 4.19 system comes back, and this script reads the region out of
/dev/mem before the page allocator gets near the top of memory.

    python tools/getramoops.py [outfile]

Which boot the bytes belong to is proved by the unique trialtag=... that
ci-build-repo/templates/trial-kexec.sh puts on the new kernel's command line: the kernel
echoes its own cmdline into its log, so the tag either appears in the dump or the dump is
not from this round. (An earlier design blanked the region before the jump instead, and
that is what ruined the round - the blanking ran after kexec --load, and the arm64 loader
had placed the new kernel's text at the very address the DTS reserved for pstore.)
"""
import gzip
import os
import re
import subprocess
import sys
import time

HOST = os.environ.get("ONU", "192.168.9.1")
KEY = os.path.expanduser("~/.ssh/id_onu")
SKIP_MIB = 2496  # 0x9c000000 / 1 MiB - must match the board DTS
SIZE_MIB = 2
# The payload's other mailbox addresses (MBX in tools/mkctlram.py): four places a marker test
# measured to still hold written content after a normal reboot *and* after the latency of a late
# read. 64 KiB each is plenty - a report is ~1 KiB and the stages sit 4 KiB apart - and dumping
# them is what tells a round which address actually survived, instead of assuming the ramoops
# window does. Set EXTRA=0 to read only the window.
EXTRAS = [0x90000000, 0x94000000, 0x98000000, 0x9d000000]
EXTRA_BYTES = 65536


def fetch(cmd, what):
    """Read the region through the ssh CLI rather than paramiko: OpenSSH against this
    dropbear works and paramiko's auth does not, and the payload is a pipe either way."""
    ssh = ["ssh", "-q", "-o", "BatchMode=yes", "-o", "IdentitiesOnly=yes",
           "-i", KEY, "-o", "UserKnownHostsFile=/dev/null",
           "-o", "StrictHostKeyChecking=no", "-o", "ConnectTimeout=8",
           "root@" + HOST, cmd]
    p = subprocess.run(ssh, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=180)
    if p.returncode != 0 or not p.stdout:
        raise SystemExit("ssh/dd failed for %s (rc=%s): %s" %
                         (what, p.returncode, p.stderr.decode()[:200]))
    return gzip.decompress(p.stdout)


def console_records(blob):
    """pstore/ramcore keeps each record behind a 40-byte-ish header; the console text is
    plain lines, so lift out anything that looks like a kernel log line and drop the rest.
    Sorting on the bracketed timestamp reconstructs the order across the ring buffer."""
    text = blob.decode("utf-8", "replace")
    lines = re.findall(r"\[\s*\d+\.\d+\][^\x00\r\n]{4,200}|\[\s*T?\d+\][^\x00\r\n]{4,200}", text)
    if not lines:
        return []
    def key(s):
        m = re.search(r"\[\s*(\d+)\.", s)
        return int(m.group(1)) if m else 0
    out = []
    seen = set()
    for s in sorted(lines, key=key):
        if s not in seen:
            seen.add(s)
            out.append(s)
    return out


def main():
    out_path = sys.argv[1] if len(sys.argv) > 1 else "staging/ramoops-%s.bin" % time.strftime("%H%M%S")
    blob = fetch("dd if=/dev/mem bs=1048576 count=%d skip=%d 2>/dev/null | gzip -9"
                 % (SIZE_MIB, SKIP_MIB), "the ramoops window")
    open(out_path, "wb").write(blob)
    nz = sum(1 for b in blob if b)
    print("read %d bytes from the ramoops region, %d of them non-zero -> %s" % (len(blob), nz, out_path))
    if nz == 0:
        print("all zero: the region was blanked before the jump and nothing wrote to it - "
              "either mainline never got far enough to touch pstore, or the reset was cold.")
        return
    head = blob[:64].hex()
    print("first bytes: %s" % head)
    dump_extras(out_path)
    # The kernel prints its own command line into the log, and both payload loaders put a
    # unique tag there (ctl-kexec.sh uses ctltag=, the older trial-kexec.sh trialtag=), so
    # this is the only claim that survives a reboot that the bytes came from the round we are
    # looking at rather than from an older boot.
    tag = re.search(rb"(?:ctl|trial)tag=[0-9A-Za-z:._-]+", blob)
    print("tag in region: %s" % (tag.group(0).decode() if tag else "NOT FOUND - stale or clobbered"))
    recs = console_records(blob)
    if not recs:
        print("non-zero but no kernel log lines: %s holds whatever the vendor had there" % out_path)
        print("Non-zero with no log lines and no tag: something other than pstore owns the")
        print("region - inspect the bytes:")
        print("  od -c -N 512 %s" % out_path)
        return
    print("%d log lines, first 60:" % len(recs))
    for s in recs[:60]:
        print(s)
    print("... last 20:")
    for s in recs[-20:]:
        print(s)


def dump_extras(out_path):
    """Read the four addresses the payload also writes to, one file each, so a report that did
    land somewhere is found wherever it is and the round learns which address survives."""
    if os.environ.get("EXTRA", "1") == "0" or "extras=0" in sys.argv:
        return
    for addr in EXTRAS:
        path = "%s-%x.bin" % (out_path[:-4] if out_path.endswith(".bin") else out_path, addr)
        try:
            data = fetch("dd if=/dev/mem bs=%d count=1 skip=%d 2>/dev/null | gzip -9"
                         % (EXTRA_BYTES, addr // EXTRA_BYTES), "0x%x" % addr)
        except SystemExit as e:
            print("  extra 0x%x: %s" % (addr, e))
            continue
        open(path, "wb").write(data)
        nz = sum(1 for b in data if b)
        tag = re.search(rb"(?:ctl|trial)tag=[0-9A-Za-z:._-]+", data)
        print("  extra 0x%08x -> %s: %d B, %d non-zero, tag %s" %
              (addr, os.path.basename(path), len(data), nz,
               tag.group(0).decode() if tag else "not present"))


if __name__ == "__main__":
    main()
