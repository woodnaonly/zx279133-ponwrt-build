# The board's port-flow table is not the microcode's

**2026-10-24.** The frame stopped between the ISU and the ODMA, and the
vendor's own board, measured with the same flood, loses nothing at all:

| counter | vendor's board | our board |
|---|---|---|
| SMAC1 rx packets | +7500 | +7500 |
| ISU spa in | **+7517** | **+3064** |
| ISU out to PPU unicast | **+7517** | **+381** |
| ODMA ppu in | **+7517** | **0** |
| RED in idm drop | +7500 | 0 |

The flood is therefore a fair test - the vendor passes it with nothing
dropped - and the PPU is where our board stops: it takes 381 frames out of
the ISU and forwards none of them.

## What the PPU is told to do

The PPU runs a microcode blob, and the blob carries a **port-flow table**:
for every SPA port it says which entry program that port runs, and the
loader applies it to the SPA port attributes as it installs the code.

That table is *board* configuration. It says which of the SoC's internal
ports is the WAN, which four are the UNI jacks, which one the CPU talks to.
The blob this driver is given - the tree's
`target/linux/zte/zx279133/firmware/zte/zx279133/mcode_intel.bin` - carries
the table of the board it was taken from, the SR1010.

The vendor's own blob, read out of this board's flash
(`etc/mcode/mcode_133.bin`), carries this board's:

| port | SR1010's blob | this board |
|---|---|---|
| 0 (PON0, the WAN) | 0x00000463 | 0x0000a6a5 |
| 1..4 (the UNI jacks) | 0x00008015 | 0x0000a6a5 |
| 5 (XMAC0, the CPU's conduit) | 0x00008015 | **0x0000a6a5** |
| 6 (XMAC1), 7 (RGMII1) | 0x00008015 | 0x0000a6a5 |
| 8 | - | 0x00039b11 |
| 10 | 0x00c0800b | 0x0000a6a5 |
| 12 | 0x00ef8445 | 0x00009f99 |
| 14 | 0x00c00459 | 0x0000a6a5 |
| 15 (the CPU trap port) | 0x00008001 | **0x0000a767** |
| 16..48 (the WOE's queue ports) | 0x0000a4d3 | 0x0000a6a5 |
| 49, 50 | 0x0000a86d | 0x00052825, 0x00052827 |
| 51..54, 63 | 0x0000a86d, 0x00008009 | - (the vendor configures none) |

55 of the 64 ports differ. The two blobs are also different revisions of the
same microcode - the SR1010's is `0x10001.0x1` with 5272 instructions, the
vendor's is `0x10108.0x31ba6a03` with 5166.

The two agree on the ports the driver programs itself: the four jacks and
the CPU trap port are written from the vendor's own SPA memory
(`zx279133_9132_port_words_vendor()`), and the vendor's table says the same
thing. Every other port kept the SR1010's entry program - and port 5, the
conduit the CPU reaches the 9132 through, was running an entry program that
belongs to another board.

## The fix

The blob's port-flow tags are no longer applied. The board's own table, the
vendor's, is written instead: `zx279133_9132_port_flow[]` in
`zx279133-np.c`, applied by `zx279133_9132_port_flow_apply()` at the end of
`zx279133_ppu_mcode_prepare()`.

A port the vendor's table does not name - 9, 11, 13 and 51..63 - keeps
whatever the chip reset left in it, which is what the vendor's board has
too: the vendor's table does not touch them either.

The microcode itself is left alone. The RX hash the driver installs is a
hook into the microcode's own instruction stream at a fixed offset
(`zx279133_rx_hash_programs[]`), so the blob and the hook have to match;
the vendor's blob would need the hook moved, which is a separate change.

## What was ruled out on the way

* **The SMCT thresholds.** The SMCT's own words read back as the vendor's
  (0xc000 = 0x1e00, 0xc05c = 0x1d00, 0xc060 = 0x1fff1fff) even though the
  driver writes 0x1f00, 0x1a00 and 0x1a001a00 into them: those bits are
  read-only, the values the vendor writes are the chip's reset values, and
  the driver's writes are no-ops. Not the difference.
* **The ISU and the ODMA.** Every descriptor entry the vendor's init writes
  is written here with the same value, and the live board matches word for
  word (`0x10000` = 0x0000000a, `0x14000` = 0x00000001).
* **The driver's own "vendor" constants.** All 25 offset/value pairs in
  `zx279133-main.c`'s two tables match the vendor's live NPPT exactly
  (`staging/ctl/auditconst.py`).
* **The BMU.** Its base is NPPT 0x3c000, not 0xf000 - 0xf000 is a hole
  that reads back whatever the previous module left there. 50 of the first
  64 words match the vendor's; the 14 that differ are the buffer-pool
  geometry, which is the tree's own layout and is deliberate.

## How the vendor's numbers were taken

The vendor's board, its own system, its own instruments:

    python staging/ctl/vendor_ctr.sh ...   # over ssh, see staging/ctl/vrun3.py

`staging/ctl/vendor-ctr.txt` holds both passes: the counters are read with
the vendor's `module_reg`, the flood runs in between, and the words that
moved are the ones that carry traffic.
