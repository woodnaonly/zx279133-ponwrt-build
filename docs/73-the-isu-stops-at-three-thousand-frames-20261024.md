# 73 - The ISU stops at three thousand frames

Date: 2026-10-24

## What round 72 left open

Round 72 replaced the guesswork with measurements: the vendor's complete
NPPT (39,680 words, taken twice with a flood in between), the names of its
counters (376 of them, 97.2% confirmed), and the vendor's switch tables read
through the vendor's own debugfs entries.

The measurement it ended on was this.  7500 frames were flooded at the
board, and the counters said:

    SMAC1 rx packets   0x080780   +7500
    SDET in_eop        0x002180   +7500
    SPA2SMCT PKT       0x008c00   +7500
    ISU spa in         0x010080   +3064
    ISU out to ppu uc  0x01008c    +381
    ODMA ppu in        0x014044      +0
    RED, QMG, DMA, IDM             +0

## The number that is not a number

The ISU's `spa in cnt(32bit)` at NPPT + 0x10080 is not a counter that goes
up with the traffic.  It is the **occupancy of the ISU's input queue**, and
it stops at 3195.

    round 198: before the flood 130, after it 3194
    round 201: before the flood 3195, after it 3195

3195, again, from a completely different boot, with a completely different
flood.  It is a queue that fills up and then stops taking frames, and the
frames that do not fit are simply not accepted: the SMCT's own drop
counters barely move (99 and 86 out of 7500, byte 0 and byte 1 of NPPT +
0xc0f0, both eight bits wide).

So the picture is not "the switch drops the frames".  It is "the switch
accepts about three thousand frames, queues them, and then stops".

## What the ISU is queued behind

The ISU hands frames to the PPU, the PPU hands them to the ODMA, the ODMA
hands them to the RED, and the RED hands them to the queue manager and from
there to the IDM, which is the CPU's descriptor ring.  Every one of those
stages is empty on our board.

Three things were measured about that in this round.

**The RED and the queue manager have never been compared with the vendor.**
The earlier comparisons covered the first 64 words of the SMCT, the ISU, the
ODMA, the SPA and the IDM, and nothing else at all.  Reading the RED and the
QMG in full - 1024 words each - against the vendor's dump found **296
configuration differences**: the RED's configuration word reads 0x7e where the
vendor's reads 0x9e, the descriptor watermark reads 0x60002000 where the
vendor's reads 0x3f9f3e9f, the IDM descriptor watermark reads 0x5e000500
where the vendor's reads 0x08bf07ff, the queue manager's watermark reads
0x2bb82fa0 where the vendor's reads 0x0bb80fa0, and the differences repeat
every 0x400 bytes because the RED and the QMG have four port blocks each.

Every one of those numbers is already in the tree's own header as the
vendor's value.  The overlay writes them - `zx279133_red_vendor_fixup()` and
`zx279133_tm_vendor_fixup()` - and the board reads something else back.

**Writing the vendor's values by hand works.**  exp201 put nineteen of the
twenty six configuration words into the board with the auto-gate at NPPT +
0xb8 (bit 10 is **clear** on our board, so the gate is not what stops them)
and they stayed.  Seven did not take, and the ones that did take made the
chain **worse**: the SPA's counter kept moving (+7135) while the ISU's
stopped moving altogether.

That is worth stating plainly: the RED's configuration is not what is
stopping the frames, because the vendor's configuration stops them even
harder.  Whatever the RED's words say, the ISU's queue fills up behind
something else.

**The order is wrong in the driver, not the values.**  The driver's own notes
say it: "the configuration has to be written after the last reset, not
before it".  The overlay writes the vendor's RED configuration in
`zx279133_hardware_prepare()`, and the tree's own `zx279133_red_prepare()`
runs after it and puts the tree's SR1010 values back.  The RED's values were
already wrong for exactly this reason once before; the fix landed for the
RED's indirect RAMs but not for the direct words.

## The protocol action table, which was right

The vendor's `sw_init_port_protocol_action()` copies 616 bytes out of
`switch.ko`'s `.rodata` and walks them as 77 entries of two words.  Those
are the 77 (chip protocol type, action) pairs of the protocol action table,
and this driver's `zx279133_9132_proto_action[128]` matches them entry for
entry, 255 meaning "no entry".

The actions are in the chip's encoding, which is the mirror image of the tm
API's: `chip_pkt_protocol_action_change_to_sw_protocol_action()` maps chip 0
to FORWARD, chip **1** to DROP and chip **2** to TRAP, while the API calls
those 0, 2 and 1.  The vendor's own `port_proation_get` confirms it: every
port 0..7 and every protocol type 0..11 reads back `action:1`, which is
TRAP.

What was missing was the **port banks**.  A port p is also p + 0x10 and
p + 0x20, the switch engine's flood words say so - a port p carries the
flood group ids `p<<6`, `(p+0x10)<<6` and `(p+0x20)<<6` - and the protocol
action table is indexed by (port << 7) | protocol, so a frame looks its
action up in one of the three banks.  The vendor's load path writes the
groups 1..7 and 0x10..0x30.  This driver wrote the groups 0..7 only, so the
groups a frame really looks in were empty, and a frame that finds no action
is neither forwarded nor trapped: it is dropped inside the chip.

`zx279133_9132_proto_action_set()` now writes all three banks.  That is
commit 5a6ff3e.

## What is still open

1. **What the ISU's input queue is made of.**  3195 is suspiciously close to
   the size of a buffer pool.  The BMU owns that pool, the BMU has never
   been compared with the vendor either, and the queue filling up behind a
   pool that is too small fits the symptom exactly.
2. **The SMCT's one remaining difference.**  NPPT + 0xc064 reads 0x30000200
   where the vendor reads 0x10000200, in all four of the SMCT's port
   blocks.  The vendor's `smct_set_reg2state_clk_minus_h_th()` writes that
   word as a read-modify-write of a threshold: the low fourteen bits are
   kept and the high half is replaced by the threshold, which the vendor
   sets to 0x1000 where the reset leaves 0x3000.  A write of the vendor's
   value into that word does not stick on our board, and nothing in
   `np_133.ko` writes it during the ordinary bring-up - it comes from the
   low-power path, which the vendor's board does not run either.
3. **The PPU.**  The ISU forwards 381 frames to the PPU and the PPU forwards
   none of them to the ODMA.  The PPU is a separate packet engine with its
   own microcode; the tree's `mcode_intel.bin` and the vendor's
   `mcode_133.bin` are the same program built twice (same magic, same
   instruction count, same size, different CRC32), and the difference
   between the two builds has never been looked at.

## The tools this round added

    staging/ctl/exp198.py   byte-level delta of the counters around the break
    staging/ctl/exp199.py   read a whole module and diff it with the vendor
    staging/ctl/exp200.py   the same at three flood rates
    staging/ctl/exp201.py   put the vendor's RED and QMG words in by hand
    staging/ctl/proto_table.py  the 77 protocol action pairs, decoded
    staging/ctl/vstat-map.tsv    376 counter names and their addresses
    staging/ctl/vfull1.json      the vendor's complete NPPT
    staging/ctl/vendor-full.txt  the raw two-pass dump it came from
