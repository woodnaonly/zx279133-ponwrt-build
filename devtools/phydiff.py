"""Diff the RTL8226B's own state between U-Boot and Linux, inside one power cycle.

Why this exists: the lane is dead under mainline in both directions while the same cable and the
same counter carry 11 MB under U-Boot. The strongest remaining explanation is phylib: of_mdiobus
instantiates `14f02000.mdio:05` and binds `RTL8221B-VB-CG`, and a soft reset of that chip throws
away the DSP patch that `rlt8226b.ko` downloads at init (devad 31 reg 0xa436/0xa438 are touched 32
and 34 times inside `Rtl8226b_phy_init`), leaving a chip that no longer forwards either way. That is
a claim about registers, so it needs a register measurement, and it needs *both* halves in one boot:
a snapshot from a different power cycle cannot separate "Linux changed it" from "it was never like
that", which is exactly the mistake 补七十八 had to take back out of the record.

tools/lanecheck.sh therefore reads seven registers before `bootm` with U-Boot's `mii 45read` and the
same seven after with `mdread c45`, and this file diffs those two halves out of the one log. The
seven are the ones the vendor's own code names, so unlike the nine raw words in
staging/ctl/traffic-proven-phy-uboot.txt each row means something (see REGS below).

Both sides carry a hard control, because an empty result here looks exactly like "the chip is
untouched": `mii 45read` does not accept ranges and parses devad/reg as hex, and plain `mii read`
(C22) on this bus returns FFFF for every register including the PHY ID. If one half of the round
produced no rows, that is a broken send or a missing applet, not a finding, so this exits 2.

	python ci-build-repo/devtools/phydiff.py staging/ctl/serial.log
"""
import re
import sys

# (devad, reg) -> what the vendor driver uses it for. Recovered by disassembling rlt8226b.ko, not
# guessed: tools/ponDis.py pon/vendor-modules/rlt8226b.ko Rtl8226_serdes_rst (and _autoNego_set,
# _link_get, Rtl8226b_phy_init).
REGS = {
    (30, 0x7581): "serdes reset: Rtl8226_serdes_rst clears BIT(4) and polls for it to self-clear",
    (30, 0x7588): "serdes auto-negotiation: Rtl8226_serdes_autoNego_set writes through here",
    (30, 0x758B): "serdes link status, first of the pair Rtl8226_serdes_link_get reads",
    (30, 0x758D): "serdes link status, second of that pair (read first in the function)",
    (30, 0xA436): "patch-adjacent control also used inside Rtl8226b_phy_init",
    (31, 0xA436): "DSP patch window: used 32 times in Rtl8226b_phy_init",
    (31, 0xA438): "DSP patch window: used 34 times in Rtl8226b_phy_init",
}

UB_ROW = re.compile(r"addr_45:\s*0x5,\s*devad_45:\s*0x([0-9a-fA-F]+),\s*reg_45:\s*0x([0-9a-fA-F]+)")
UB_VAL = re.compile(r"^\s*([0-9a-fA-F]{4})\s*$")
LX_ROW = re.compile(r"^(\d+)\.([0-9a-fA-F]{4}) = ([0-9a-fA-F]{4})")


def read_text(path):
    """Same decoder lanediff.py uses: the relay logs bytes, and serial data is latin-1 by
    convention with NULs from the adapter's padding."""
    with open(path, "rb") as f:
        raw = f.read()
    return raw.decode("latin-1", errors="replace").replace("\x00", "")


def uboot_side(text):
    """(devad, reg) -> value, from between the round's LC-UBPHY markers."""
    a, b = text.rfind("LC-UBPHY-BEGIN"), text.rfind("LC-UBPHY-END")
    if a < 0 or b < a:
        return None
    out = {}
    lines = text[a:b].splitlines()
    for i, line in enumerate(lines):
        m = UB_ROW.search(line)
        if m and i + 1 < len(lines):
            v = UB_VAL.match(lines[i + 1])
            if v:
                out[(int(m.group(1), 16), int(m.group(2), 16))] = int(v.group(1), 16)
    return out


def linux_side(text):
    """(devad, reg) -> value, from after LC-PHY2BEGIN."""
    a = text.rfind("LC-PHY2BEGIN")
    if a < 0:
        return None
    out = {}
    for line in text[a:].splitlines():
        m = LX_ROW.match(line.strip())
        if m:
            out[(int(m.group(1)), int(m.group(2), 16))] = int(m.group(3), 16)
    return out


def main():
    log = sys.argv[1] if len(sys.argv) > 1 else "staging/ctl/serial.log"
    text = read_text(log)
    ub, lx = uboot_side(text), linux_side(text)

    if ub is None:
        print("phydiff: no LC-UBPHY-BEGIN/END in this log - the round never took the U-Boot half. "
              "That is not a result, it is a missing send.")
        return 2
    if lx is None:
        print("phydiff: no LC-PHY2BEGIN - Linux never got far enough to read the chip back.")
        return 2
    # Both halves have to come from the same boot. Diffing this round's Linux rows against an older
    # round's U-Boot rows still prints a tidy table of SAMEs, and a match that spans a power cycle is
    # worth nothing - which is precisely the mistake 补七十七 had to take back out of the record.
    ub_at, lx_at = text.rfind("LC-UBPHY-BEGIN"), text.rfind("LC-PHY2BEGIN")
    if lx_at < ub_at:
        print("phydiff: the newest LC-PHY2BEGIN sits BEFORE the newest LC-UBPHY-BEGIN - these two "
              "halves are from different boots, so any diff between them is meaningless. Refusing.")
        return 2
    for name, side in (("U-Boot (mii 45read)", ub), ("Linux (mdread)", lx)):
        if not side:
            print("phydiff: %s produced ZERO rows. Treat that as a broken reader, not as an answer: "
                  "this bus is known to hand back nothing useful to the wrong command (plain C22 "
                  "`mii read` returns FFFF for all 32 registers including the PHY ID)." % name)
            return 2

    changed = same = missing = 0
    rc = 0
    print("== RTL8226B, U-Boot (before bootm) vs Linux (after), one power cycle ==")
    for key in sorted(REGS):
        d, r = key
        u, l = ub.get(key), lx.get(key)
        if u is None or l is None:
            miss = "U-Boot" if u is None else "Linux"
            print("  %2d.%04x  ub %-6s lin %-6s  %s missing - cannot diff one half"
                  % (d, r, "-" if u is None else "%04x" % u,
                     "-" if l is None else "%04x" % l, miss))
            missing += 1
            continue
        tag = "SAME" if u == l else "DIFFER"
        changed += l != u
        same += l == u
        print("  %2d.%04x  ub %04x  lin %04x  %s   %s" % (d, r, u, l, tag, REGS[key]))

    print("phydiff: %d same, %d differ, %d not diffable over %d registers"
          % (same, changed, missing, len(REGS)))
    if missing:
        # One DIFFER among seven rows where five could not be compared is not an answer; it is half a
        # round. Saying "not enough" is the only honest output, and the fix is to re-send the missing
        # half rather than to interpret what is left.
        print("  -> INCONCLUSIVE: %d of %d rows have only one side. Re-run the missing reads in the "
              "same boot before drawing a conclusion." % (missing, len(REGS)))
        rc = 1
    elif changed:
        print("  -> Linux reconfigured the chip. The patch-window rows (devad 31) say whether the "
              "DSP download survived; if they differ, a fixed link alone cannot bring this lane up "
              "and the PHY bring-up has to be reproduced in the driver.")
    else:
        print("  -> the chip is byte-identical to what the working U-Boot lane left, so phylib is "
              "NOT reprogramming it and the divergence is upstream of the PHY (nppt/SPA side).")
    return rc


if __name__ == "__main__":
    sys.exit(main())
