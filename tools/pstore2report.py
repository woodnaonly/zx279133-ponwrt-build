#!/usr/bin/env python
"""Lift the announce payload's report out of a ramoops dump and make it look like a push.

Why this exists: the payload's /init writes its report to /dev/kmsg *before* it tries the
network, and the board DTS reserves a 2 MiB no-map `ramoops` region at 0x9c000000 with
console-size 1 MiB. So a round whose kernel booted happily on a jack mainline cannot drive -
which is every lan jack, since there is no 9132 driver - still leaves the whole report behind.
Read it out with tools/getramoops.py and feed it here:

	python tools/pstore2report.py staging/ctl/ramoops-K5.bin [tag]   # print the record
	python tools/pstore2report.py staging/ctl/ramoops-K5.bin K5 --append

`--append` writes it to $LOGF (default staging/ctl/reports.log) in exactly the shape
tools/ncsrv.py records pushes in, which is what tools/gatewatch.sh scores - so the permit is
granted by the same code path and the same checks, whether the report arrived over TCP or out
of DRAM.

Without a matching CTLALIVE the dump is still worth reading, so the same bytes get printed as
"the last thing this kernel said": that is the diagnosis for a round that died in a driver,
which no channel other than pstore can show at all.
"""
import os
import re
import sys
import time

KMSG = re.compile(r"^\s*(?:<\d+>)?\[\s*[0-9T][^\]]{0,15}\]\s?(.*)$")
# The fields tools/mkctlram.py's announce /init prints, in the order it prints them. Anything
# else in the region is the vendor kernel's own memory, so it is not admitted to a report.
FIELDS = (
    "CTLALIVE tag=", "kver=", "uptime=", "model:", "leds:", "netdevs:", "cmdline:",
    "root_mtdblock5_first512k_md5=", "root_bytes_read=", "jffs2_ro_mount=",
    "jffs2_userspace=", "jffs2_err=", "installed_dropbear_Interface=", "installed_kmods=",
    "installed_release=", "jffs2_top:", "mtd", "dev:    size",
    # The RAM mailbox block (tools/mkctlram.py) is the same report written by dd into the
    # reserved window instead of through pstore, so it carries these two extra lines.
    "mbox_write=", "mbox_readback=", "mbnote=", "crm_now=",
)


def lines(blob):
    """Every line, with pstore's [timestamp] prefix removed.

    The ring is newline-separated inside and null-filled outside, so a null is treated as
    another separator: truncating at the first one would drop the whole report, which is what
    a synthetic blob caught (the leading filler is 20 KiB of zeros).
    """
    out = []
    for raw in blob.replace("\x00", "\n").split("\n"):
        m = KMSG.match(raw)
        out.append((m.group(1) if m else raw).rstrip("\r"))
    return out


def main():
    if len(sys.argv) < 2:
        raise SystemExit(__doc__)
    path, tag = sys.argv[1], (sys.argv[2] if len(sys.argv) > 2 and not sys.argv[2].startswith("--") else "K5")
    # The region is raw RAM, and on Windows the console codec is GBK: one replacement character
    # in the junk lines raised UnicodeEncodeError and lost the whole verdict.
    try:
        sys.stdout.reconfigure(errors="replace")
    except Exception:
        pass
    blob = open(path, "rb").read().decode("utf-8", "replace")
    ls = lines(blob)
    want = "CTLALIVE tag=%s" % tag
    # Anchor on a line that *starts* with the marker, not one that contains it: the finished
    # mailbox block carries "mbox_readback=CTLALIVE tag=K... upti", the first bytes of the early
    # marker read back off /dev/mem, and a substring search there starts the report on a line of
    # its own middle, which recovers one field and calls the rest of the round missing.
    at = re.compile(r"^\s*%s" % re.escape(want))
    hits = [i for i, l in enumerate(ls) if at.match(l)]
    if not hits:
        print("no %r in %s - the payload never reached the line that prints its report." % (want, path))
        keep = [l for l in ls if len(l.strip()) > 8]
        print("%d non-empty line(s) in the region; last 40:" % len(keep))
        for l in keep[-40:]:
            print("  | %s" % l[:160])
        if not keep:
            print("  (the region is empty: nothing was written, or the vendor system already")
            print("   recycled it - that memory is ordinary RAM to 4.19.)")
        else:
            print("  If none of that is a 6.18.38 log line the capture was simply late: grab it")
            print("  the moment the board answers - tools/pstorecatch.sh is built to do that.")
        return 2
    ls_at = hits[-1]
    # The /init writes its whole report in one shot, so its lines are contiguous inside one
    # pstore record - but the ring pads records with nulls, which become hundreds of empty
    # lines here, and each record carries a binary header. So collect by field name across a
    # wide window and let the *next* CTLALIVE be the boundary, not a run of blanks.
    rec, seen_self = [], 0
    for l in ls[ls_at:ls_at + 20000]:
        if at.match(l):
            seen_self += 1
            if seen_self > 1:
                break
        if l.startswith(FIELDS) or re.match(r"^mtd[0-9]+:", l):
            rec.append(l)
            if len(rec) >= 40:
                break
    print("report recovered from pstore at line %d of %s (%d field lines):" % (ls_at, path, len(rec)))
    body = "\n".join(rec)
    print(body)
    missing = [f for f in ("kver=", "root_mtdblock5_first512k_md5=", "jffs2_ro_mount=")
               if not any(r.startswith(f) for r in rec)]
    if missing:
        print("  NOTE: %s not in the recovered block - the ring wrapped or the /init did not get"
              " that far" % ",".join(missing))
    if "--append" in sys.argv:
        logf = os.environ.get("LOGF", "staging/ctl/reports.log")
        with open(logf, "a", encoding="utf-8") as f:
            f.write("\n===== %s from pstore(%s) =====\n%s\n" %
                    (time.strftime("%H:%M:%S"), os.path.basename(path), body + "\n"))
        print("  appended to %s - tools/gatewatch.sh scores it like a TCP push" % logf)
    return 0


if __name__ == "__main__":
    sys.exit(main())
