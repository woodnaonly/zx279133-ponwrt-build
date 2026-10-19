#!/usr/bin/env python3
"""Score a lanecheck round against the two traffic-proven references, mechanically.

    python devtools/lanediff.py staging/ctl/serial.log

Both sides of each comparison come from a board that really carried frames through this jack,
which is the whole point of this script: the register *interpretation* is not the evidence, the
11,464,708 bytes are. References:

  PHY   staging/ctl/traffic-proven-phy-uboot.txt - U-Boot `mii 45read 5 <devad> <reg>` in the
        power cycle whose autoboot moved 11 MB, before any Linux touched the chip. Its own header
        warns that plain C22 `mii read` returned FFFF for all 32 registers including the PHY ID,
        so only the 45 blocks mean anything.
  MMIO  the values typed in below, read with `md` from the same kind of cycle (run 65 08:54-08:56
        POST, run 69 09:41), i.e. U-Boot after a real transfer and before eth_halt's effect on
        the MAC enable bits (see the caveat in docs/07: the +0x00/+0x10 words read 0 after any
        U-Boot command exits, so they are NOT comparable and are excluded on purpose).

Output is one line per address with the two values and SAME/DIFFER/MISSING. A MISSING row means
the round never read that address, which is the failure mode this file exists to catch: a
half-run round otherwise reads as "everything matched".
"""
import io
import os
import re
import sys

SNAPSHOT = "staging/ctl/traffic-proven-phy-uboot.txt"
for _cand in (SNAPSHOT, "../" + SNAPSHOT):
    if os.path.exists(_cand):
        SNAPSHOT = _cand
        break

# Traffic-proven MMIO reference. Keys are absolute addresses, values the word U-Boot printed in
# a snapshot taken right after `Bytes transferred = 11464708`.
#
# Provenance correction (docs 补七十八): these three SPA words were first framed as "0 before the
# transfer, non-zero after". That is false - all 35 reads of 0x19008000 ever logged say 68000000,
# and warmcmp's cold column equals its traffic column. They are boot-early config that neither
# U-Boot's code nor mainline writes at runtime, so this row is now a check on one question only:
# did Linux change something nobody programs?
MMIO_REF = {
    0x19008000: 0x68000000,
    0x19008014: 0x06111113,
    0x19008018: 0x01111111,
    0x19000000: 0x000000AA,
    0x190000BC: 0x00000022,
    0x1900C0F0: 0x000000FD,
    0x190340BC: 0x0014001A,
    0x1902C054: 0x7B7B1A7B,
    0x19002438: 0x00000003,
}
# Anything that moved between run 65's own PRE and POST snapshots is a counter or a status word,
# not a configuration word, so comparing it against a *different* transfer would report a
# difference that means nothing. Only the SPA trio is held to a hard match, and that is exactly
# the block hypothesis 2 is about.
#
# 0x19000000 is demoted for a second, concrete reason: the snapshot file says 000000aa while every
# live U-Boot read of it in serial.log says 000000ff, so the reference itself is not settled there.
MMIO_SOFT = {0x19000000, 0x190000BC, 0x1900C0F0, 0x190340BC, 0x1902C054, 0x19002438}
SPA_TRIO = (0x19008000, 0x19008014, 0x19008018)

# Only these rows are compared: they are the ones tools/lanecheck.sh actually asks mdread for.
# The snapshot also holds `1.0011` and a stray `48.20f3` (devad 0x30, from the round that
# mis-parsed `30` as hex), and comparing them would report MISSING on an otherwise good round.
PHY_WANTED = {(1, 0x00), (1, 0x01), (1, 0x02), (7, 0x00), (7, 0x01), (7, 0x1C), (7, 0x20),
              (7, 0x22), (30, 0x00)}

SNAPSHOT_ADDR_RE = re.compile(
    r"addr_45:\s*0x([0-9a-f]+),\s*devad_45:\s*0x([0-9a-f]+),\s*reg_45:\s*0x([0-9a-f]+)"
)
VALUE_RE = re.compile(r"^\s*([0-9a-fA-F]{4})\s*$")
MDREAD_RE = re.compile(r"^(\d+)\.([0-9a-fA-F]{4}) = ([0-9a-fA-F]{4})")
RDREG_RE = re.compile(r"^0x([0-9a-fA-F]{8}): 0x([0-9a-fA-F]{8})")


def read_text(path):
    with open(path, "rb") as f:
        raw = f.read()
    return raw.decode("latin-1", errors="replace").replace("\x00", "")


def parse_snapshot(path):
    """{ (devad, reg): value } for the address-5 PHY, keyed with ints."""
    out = {}
    lines = read_text(path).splitlines()
    pending = None
    for line in lines:
        m = SNAPSHOT_ADDR_RE.search(line)
        if m:
            addr, devad, reg = (int(x, 16) for x in m.groups())
            pending = (devad, reg) if addr == 5 else None
            continue
        if pending is not None:
            v = VALUE_RE.match(line)
            if v:
                out[pending] = int(v.group(1), 16)
                pending = None
    return out


def parse_board(text):
    """Everything mdread/rdreg printed in this slice."""
    phy, mmio = {}, {}
    for line in text.splitlines():
        s = line.strip()
        m = MDREAD_RE.match(s)
        if m:
            # mdread prints devad in decimal; the snapshot is hex. Both are the same number.
            phy[(int(m.group(1)), int(m.group(2), 16))] = int(m.group(3), 16)
            continue
        m = RDREG_RE.match(s)
        if m:
            mmio[int(m.group(1), 16)] = int(m.group(2), 16)
    return phy, mmio


def main():
    log = sys.argv[1] if len(sys.argv) > 1 else "staging/ctl/serial.log"
    text = read_text(log)
    # Only the newest lanecheck round counts, otherwise a previous round's reads silently
    # "match" the reference forever. The round opens with LCMARK, so that is the boundary; the
    # inner markers are ordered SPAW then XMAC then PHY then PING and taking the *last* of them
    # would drop every rdreg line that came before it.
    start = text.rfind("LCMARK")
    if start < 0:
        found = [text.rfind(m) for m in ("LC-SPAWBEGIN", "LC-XMACBEGIN", "LC-PHYBEGIN",
                                        "LC-PINGBEGIN")]
        found = [f for f in found if f >= 0]
        start = min(found) if found else -1
    if start < 0:
        print("lanediff: no lanecheck markers in the log - this is not a lanecheck round")
        return 1
    seg = text[start:]

    ref = parse_snapshot(SNAPSHOT)
    phy, mmio = parse_board(seg)

    rc = 0
    print("== PHY (mdread c45 vs the traffic-proven U-Boot snapshot) ==")
    if not phy:
        print("  MISSING: mdread printed nothing. Either the applet is not in this image "
              "(`which mdread`) or every transaction timed out.")
        rc = 1
    missing_ref = [k for k in PHY_WANTED if k not in ref]
    if missing_ref:
        print("  the snapshot does not hold %s - refusing to call those rows fine"
              % ", ".join("%d.%04x" % k for k in missing_ref))
        rc = 1
    for key in sorted(k for k in ref if k in PHY_WANTED):
        want = ref[key]
        got = phy.get(key)
        if got is None:
            # The snapshot keys devad as the number U-Boot echoed, which is hex-parsed text:
            # `1e` there is 30 here. Look the same reg up under both readings before deciding.
            got = phy.get((key[0], key[1]))
            if got is None:
                print("  %d.%04x  ref %04x  board MISSING" % (key[0], key[1], want))
                rc = 1
                continue
        tag = "SAME" if got == want else "DIFFER"
        if got != want:
            rc = 1
        print("  %d.%04x  ref %04x  board %04x  %s" % (key[0], key[1], want, got, tag))

    print("== MMIO (rdreg vs U-Boot's post-transfer snapshot) ==")
    if not mmio:
        print("  MISSING: rdreg printed nothing.")
        rc = 1
    for addr in sorted(MMIO_REF):
        want = MMIO_REF[addr]
        got = mmio.get(addr)
        if addr in MMIO_SOFT:
            # Never a verdict, and a missing one is not a failure either: these are counters and
            # status words that moved between U-Boot's own PRE and POST snapshots, so they can
            # only be read as context for the rows that are hard.
            print("  %#010x  ref %08x  board %s  (counter, informational)"
                  % (addr, want, "%08x" % got if got is not None else "not read"))
            continue
        if got is None:
            print("  %#010x  ref %08x  board MISSING" % (addr, want))
            rc = 1
            continue
        tag = "SAME" if got == want else "DIFFER"
        if got != want:
            rc = 1
        print("  %#010x  ref %08x  board %08x  %s" % (addr, want, got, tag))

    # The one decision this round has to make on the spot, in the few minutes of window left after
    # the read: tools/spapoke.sh writes 68000000/06111113/01111111 into these three words. If Linux
    # already reads them back at exactly those values the poke cannot change anything, and spending
    # the window on it would cost the only other measurement still open (mdread's PHY rows).
    trio = [(a, mmio.get(a), MMIO_REF[a]) for a in SPA_TRIO]
    if all(got == want for _, got, want in trio):
        print("steer: the SPA trio already equals the proven state -> spapoke.sh would be a no-op, "
              "skip it and spend the rest of the window on the mdread rows")
    elif any(got is None for _, got, _ in trio):
        print("steer: SPA trio not fully read -> no A/B on an unread baseline; re-send rdreg first")
    else:
        print("steer: SPA trio differs (%s) -> Linux changed a block nobody programs; run "
              "tools/spapoke.sh while the window lasts"
              % ", ".join("%#x %08x!=%08x" % (a, got, want) for a, got, want in trio if got != want))

    print("lanediff: %s" % ("every compared word matches the traffic-proven state"
                            if rc == 0 else
                            "at least one row differs or was never read - see above"))
    return rc


if __name__ == "__main__":
    sys.exit(main())
