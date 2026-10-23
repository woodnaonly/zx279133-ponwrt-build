# 72 - The whole NPPT, the counter names, and where the frame really stops

Date: 2026-10-24

## Why this round exists

Round 71 ended with the frame arriving at the SMCT and stopping there.  That
was based on one register address that had been read wrong and on a set of
counters that were interpreted without knowing what the vendor's own code
calls them.  This round replaced both with measurements:

* the vendor's **complete** NPPT - all 23 modules, 39,680 words, taken twice
  with the LAN flooded in between, so that every counter the flood moves is
  known;
* the **names** of those counters, recovered from the vendor's statistics
  code, so that a register is no longer just a number;
* the vendor's **switch tables**, read through the vendor's own debugfs
  entries.

## The vendor's NPPT, in full

`staging/ctl/vendor_full.sh` walks every module through
`/proc/pon/tm_debug/module_reg`, 256 words at a time, twice, with a 300 pps
broadcast flood running in between.  The MAC blocks turned out to be 4096
words wide and had never been read past word 1024 by anybody; the SMCT, the
ISU, the ODMA, the RED, the QMG and the rest are 1024 words each.

Result: 39,680 registers, of which **329 moved** while the flood ran.

The modules are:

    bmu 512   crm 256   dma 1024  idm 1024  isu 1024  odma 1024
    qmg 1024  red 1024  reorder 512  sdet 512  sipc 512  smac0..3 4096
    smct 1024 soam 512  sopc 1024  spa 2048  ssch 1024  woe 512
    woe_sch 512  xmac0..1 4096

There is no PPU module in the NPPT: the PPU's microcode lives in its own
memory, not in the register file.

## The names

`staging/ctl/elfsym.py` reads the symbol table of the vendor's objects and
`staging/ctl/npptw.py` and `staging/ctl/callers.py` walk the relocations, so
that every call to `fpga_nppt_write_reg()` can be attributed to the function
that made it and to its two arguments.  With that, the statistics code's
`printk` calls can be tied to the word each one reads.

The result is `staging/ctl/vstat-map.tsv`: 376 counter names, 362 of them
with an exact NPPT byte address, 97.2% of them confirmed against the
vendor's own live dump.

The ones that matter for the LAN path:

    0x002180  SDET in_eop        0x002184  SDET in_sop
    0x008c00  SPA2SMCT PKT / SIPC2SPA PKT (high half)
    0x008c04  SMCT2SPA DESC / SPA2ISU DESC
    0x008c98  SPA2SMCT EOP / SOP
    0x008ca0  SPA2SMCT SLICE
    0x00c0f0  smct drop cnt(8bit) + spa drop cnt(8bit)
    0x00c104  dma drop eop/sop cnt(8bit)
    0x010080  ISU spa in cnt(32bit)
    0x01008c  ISU out to ppu uc cnt(32bit)
    0x014044  ODMA ppu in cnt      0x014048  ODMA read smct cnt
    0x0201cc  in idm drop cnt     0x020214  out drop cnt
    0x080780  SMAC1 rx packets

Two things follow from the names that the numbers alone did not say.

**`0x00c0f0` is a drop counter, and it is packed two counters to a word.**
Byte 0 is `smct drop cnt`, byte 1 is `spa drop cnt`, and each is eight bits
wide.  An earlier round read that word as a 32-bit counter and drew a
conclusion from a delta of +16973 that was really two eight-bit counters
wrapping.  Everything that was inferred from that delta has been thrown away
and re-measured.

**`0x008c00` and `0x008c04` are not one counter each either.**  They hold
two 32-bit counters side by side, and the same is true of most of the
counters in this chip.  Reading them as single 32-bit words is what made the
SPA look like it was forwarding everything while the ISU saw nothing.

## The protocol action table

The vendor's `sw_init_port_protocol_action()` in `switch.ko` copies 616
bytes out of `.rodata` and walks them as 77 entries of two words, calling
`tm_port_protocol_pktdeal_set()` for each.  Those 616 bytes are the table:
77 (chip protocol type, action) pairs.

The actions in it are in the **chip's** encoding, not the tm API's:
`np_133.ko`'s `chip_pkt_protocol_action_change_to_sw_protocol_action()` maps
chip 0 to FORWARD, chip **1** to DROP and chip **2** to TRAP.  The tm API
calls those 0, 2 and 1, which is how the two look like they are swapped.

So the vendor's table says:

    type      0, 1, 8..43, 45, 48, 50..52, 72, 74, 75, 80..82, 84..88,
              96..98                 -> 2 (TRAP)
    type      53..71, 73, 76, 83      -> 0 (FORWARD)
    every other type                  -> no entry at all

and this driver's `zx279133_9132_proto_action[128]` matches it entry for
entry, 255 being "no entry".

The vendor's own debugfs agrees: `port_proation_get` reads the table back
and prints `action:1` (TRAP) for **every** port 0..7 and **every** protocol
type 0..11, and `sw_mac_onu_proaction_get` prints `action:1` for every type
as well.

The one thing that was not the same was the **port groups**.  The vendor's
load path writes the groups 1..7 and then the groups 0x10..0x30.  This
driver wrote the groups 0..7 only.  A frame that arrives on a jack looks
its action up in one of the three banks a port has - the flood words say a
port p carries the flood group ids `p<<6`, `(p+0x10)<<6` and `(p+0x20)<<6`,
which are the port numbers multiplied by 64 - and the groups this driver left
empty are exactly the ones the frame looks in.  A frame that finds no action
is neither forwarded nor trapped: it is dropped inside the chip.

`zx279133_9132_proto_action_set()` now writes all three banks.

## Where the frame really stops

`staging/ctl/exp198.py` reads the counters around the break before and after
a 25 second, 7500 frame flood, and reports the delta of **every byte** of
every word, because the counters are packed.  On the trial kernel (run145):

    SMAC1 rx packets   0x080780   +7500   every frame arrived
    SDET in_eop        0x002180   +7500
    SPA2SMCT PKT       0x008c00   +7500
    ISU spa in         0x010080   +3064
    ISU out to ppu uc  0x01008c    +381
    ODMA ppu in        0x014044      +0
    RED                0x020xxx      +0
    QMG / DMA / IDM                 +0
    smct drop cnt      0x00c0f0     +99   (byte 0, eight bits)
    spa drop cnt       0x00c0f0     +86   (byte 1, eight bits)

The vendor, over a ten minute flood that put 79,049 frames into the ISU:

    ISU spa in         0x010080   +79049
    ISU out to ppu uc  0x01008c   +79049
    ODMA ppu in        0x014044   +79051
    RED out / in drop  0x020xxx   +0
    IDM                0x028060   +78400
    smct drop cnt      0x00c0f0    +170  (byte 0, eight bits)

So the SMCT's own drop counters are **not** the story: on both boards they
move by tens, not by thousands.  The frames are not being dropped at the
SMCT.  They are getting *into* the ISU and then stopping there: on the
vendor every frame that reaches the ISU leaves it towards the PPU, on ours
12% does, and nothing that leaves the ISU ever reaches the ODMA.

That moves the break one stage down the chain, from "the SMCT" to
"the ISU and the PPU behind it", and it is the first measurement that puts
the loss where the loss is rather than where the first counter that stopped
moving happens to be.

## What the next round has to answer

1. Why does the ISU forward 88% of what it receives nowhere?  The vendor's
   own gate functions are the place to look: `set_isu_out_en`,
   `set_isu_sp_en`, `set_odma_trap_en`, `set_spa_sp_en` and
   `lp_set_trap_cpu` are all in `np_133.ko` and none of them is called by
   anything this driver knows about.
2. What does the ISU do with a frame whose destination MAC is not in the
   table?  The vendor's FDB held exactly one entry when the bench was
   running - the PC's own MAC, `84:47:09:5b:bd:2c`, learned on port 2 -
   so the vendor's switch had learned the bench PC exactly like ours has to.
3. Whether the `smct_set_np1_th`, `smct_set_idm_th` and `smct_set_ssch_th`
   thresholds, and the SMCT word `0x00c064` that is still the only
   configuration difference between the two boards, belong to the path that
   is now broken.

## The tools this round added

    staging/ctl/elfsym.py      ELF64 symbols and disassembly, with
                                relocations resolved, for the vendor's .ko
    staging/ctl/npptw.py       every NPPT write the vendor's .ko performs,
                                with the value
    staging/ctl/callers.py     who calls a given vendor function
    staging/ctl/vfull.py       parse the vendor's full two-pass dump
    staging/ctl/vtables.py     parse the vendor's switch table dump
    staging/ctl/proto_table.py the 77 protocol action pairs out of
                                switch.ko's .rodata
    staging/ctl/exp198.py      byte-level delta of the counters around the
                                break
    staging/ctl/vendor_full.sh the vendor's complete two-pass NPPT dump
    staging/ctl/vendor_tables.sh the vendor's switch tables, through its own
                                debugfs entries
