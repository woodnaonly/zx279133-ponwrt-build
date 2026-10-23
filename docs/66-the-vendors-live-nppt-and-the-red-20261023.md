# 66 - The vendor's live NPPT, and the first block that does not match

Date: 2026-10-23. Board: ZTE ZX279133 / ZTE 9132, SK-D840N.
The question this document answers: the LAN data plane receives nothing on the
port the test PC is in, and the vendor's own system receives everything on
the same port.  What is different?

## 0. TL;DR

* The PC is on the vendor's **mac 1** - the vendor's own dmesg says
  `mac 1 link up` / `mac 1 phy status changed: 1000M full-duplex`, and that
  is `uni_phy[1]` = PHY 0x0b = SMAC1 = DSA `lan2`.  Our mapping was right all
  along; the MCODE's `port id 2` is a different numbering.
* The vendor's running system **can dump its own NPPT**:
  `/proc/pon/tm_debug/module_reg` with `-all` or `-r <module> reg=<n>
  cnt=<n>`, and each module prints its own base.  This is the reference
  this work never had.
* The vendor's bases are in the vendor's own units; ours are four times
  those - `tm red` 0x8000 -> 0x20000, `tm sopc` 0xd000 -> 0x34000,
  `nppu spa` 0x2000 -> 0x8000, `nppu idm` 0xa0000 -> 0x280000,
  `smac0` 0x10000 -> 0x40000, `smac1` 0x20000 -> 0x80000.  Every offset this
  driver uses lines up with that, so the two register images can be compared
  word for word.
* Compared word for word, with the PC pinging the vendor's board:
  * the **SOPC block matches** (31 of 32 words identical; the one difference
    is XMAC1's ready bit),
  * the **SMAC1 configuration matches** exactly (an_ctrl 0x00fa2203,
    an_ctrl2 0x3fff, pkt_filter 0x80000001, speed 0x00011000, port_ctrl
    0x00002200, ...),
  * the **SPA block matches** - not one word differs,
  * the **RED block does not match**: about twenty configuration words differ,
    and for every one of them this driver *writes the vendor's value* and
    the board reads something else.  The RED's configuration word reads
    0x7e where the vendor reads 0x9e - 0x7e being the value the tree's own
    `zx279133_red_prepare()` writes.
  * the QMG, SIPC and SMCT blocks differ in a handful of words as well, but
    there the difference is one or two hardware-derived bits.
* The driver's `zx279133_red_vendor_fixup()` declares
  `static const u32 cfg[4] = { 0xff803fff, 0x0100ff80, 0x00100200,
  0x00000020 }` and **never uses it**.  Those four values are not
  configuration: on the vendor's board they sit in the RED's *indirect
  command and data* registers (0x20014, 0x2001c, 0x20020), which keep the
  leftovers of the last indirect access.
* The PPU's per-port counters - the vendor's oracle - live in the PPU's
  SMMU0 RAM, read through the PPS indirect path, and they are **not** in the
  NPPT.  The table is created by the vendor's `np_133.ko`, which this board
  does not have: the whole 0x8f00..0x90ff block reads zero here.  That
  instrument is not available to us.
* The "clear-over-ready" word is a red herring: the vendor **reads** it and
  never writes it (an exhaustive scan of `np_133.ko` shows no store to
  NPPT+0x34294..0x342a0), and the vendor's *live* board reads 0 for
  SMAC1's ready word, exactly as ours does.

## 1. Reading the vendor's live registers

`/proc/pon/tm_debug/module_reg` takes

```
-r <module_name> reg=<offset> cnt=<count>
-all
```

and prints into the kernel log.  Each module announces itself first:

```
[  623.796988] pps ppu cluster_group0_csr_1800_dbg flag=1 base=0x25000 reg_cnt=0x800
[  630.908125] nppt nppt nppt_greg flag=2 base=0x0 reg_cnt=0x100
[  631.757047] nppt nppu sdet flag=2 base=0x800 reg_cnt=0x180
[  652.390226] nppt tm red flag=2 base=0x8000 reg_cnt=0x100
[  658.620498] nppt tm sopc flag=2 base=0xd000 reg_cnt=0x100
[  660.894731] nppt smac smac0 flag=2 base=0x10000 reg_cnt=0x1000
```

`flag=2` is the NPPT window, `flag=1` the PPS window.  The dump of every
module is 813 kB, which does not survive an SSH pipe; it does survive the
serial console, so it is captured that way
(`staging/ctl/vendor-all2.sh`).

The captured image is parsed into `staging/ctl/vendor-live.json`, and
`staging/ctl/tmp_livediff.py` compares it with what this driver reads.

## 2. The modules, and the four-times scale

| module | vendor base | our NPPT offset |
|---|---|---|
| nppt/nppt_greg | 0x000000 | 0x000000 |
| nppu/sdet | 0x000800 | 0x002000 |
| nppu/sipc | 0x001000 | 0x004000 |
| nppu/spa | 0x002000 | 0x008000 |
| nppu/smct | 0x003000 | 0x00c000 |
| nppu/isu | 0x004000 | 0x010000 |
| nppu/odma | 0x005000 | 0x014000 |
| nppu/reorder | 0x006000 | 0x018000 |
| nppu/soam | 0x007000 | 0x01c000 |
| tm/red | 0x008000 | 0x020000 |
| tm/qmg | 0x009000 | 0x024000 |
| tm/ssch | 0x00b000 | 0x02c000 |
| tm/woe_sch | 0x00b200 | 0x02c800 |
| tm/sopc | 0x00d000 | 0x034000 |
| tm/dma | 0x00e000 | 0x038000 |
| tm/bmu | 0x00f000 | 0x03c000 |
| smac/smac0..3 | 0x010000..0x040000 | 0x040000..0x100000 |
| nppu/idm | 0x0a0000 | 0x280000 |
| nppu/crm | 0x0b0000 | 0x2c0000 |

The XMAC blocks follow the same rule: this driver has XMAC0 at +0x140000
and XMAC1 at +0x180000, which is the vendor's 0x50000 and 0x60000.

## 3. What matches

* **SOPC** (`0x34000`): 31 of the 32 words of the window `0x34280..0x342fc`
  are identical to the vendor's live board.  The only difference is
  `0x342a8` - XMAC1's ready word - which reads 1 here and 0 there.
* **SMAC1** (`0x80000`): the configuration words are identical, word for
  word, with the vendor's live board:

  | word | byte | vendor | ours |
  |---|---|---|---|
  | 0x00 | +0x000 | 0x00fa2203 | 0x00fa2203 |
  | 0x01 | +0x004 | 0x00003fff | 0x00003fff |
  | 0x02 | +0x008 | 0x80000001 | 0x80000001 |
  | 0x35 | +0x0d4 | 0x03e80000 | 0x03e80000 |
  | 0x37 | +0x0dc | 0x00000063 | 0x00000063 |
  | 0x38 | +0x0e0 | 0x00011000 | 0x00011000 |
  | 0x39 | +0x0e4 | 0x00000008 | 0x00000008 |

  So the "pkt_filter and speed are destroyed by writing them" story is
  wrong: the vendor's live board has exactly the values this board has
  without writing them, and the vendor's `np_133.ko` does write 0x80000001
  to the filter and 0x00011200 to the speed word.  Both of those bits are
  write-one-to-clear, and the vendor's own board ends up with the same
  values - because the vendor's dump of the block was taken *before* its
  Linux driver wrote them.
* **SPA** (`0x8000`): not one word differs.  The per-port attributes this
  driver writes (docs/18) are the vendor's.

## 4. What does not match: the RED

The vendor's `tm-red.txt` holds eight snapshots of the same 256 words.  A
word that never changes is configuration; a word that changes is a counter.
The live dump says what the vendor's board has right now, with the traffic
running.  These are the configuration words where our board differs:

| our offset | vendor | ours | what this driver writes |
|---|---|---|---|
| 0x020004 | 0x0000009e | 0x0000007e | 0x0000009e |
| 0x020030 | 0x00003400 | 0x00001800 | - |
| 0x020034 | 0x00000000 | 0x00000020 | - |
| 0x020040 | 0x00000c00 | 0x00001400 | 0x00000c00 |
| 0x020050 | 0x00002600 | 0x0000ffff | 0x00002600 |
| 0x020060 | 0x10000900 | 0x10001200 | 0x10000900 |
| 0x020064 | 0x04000500 | 0x04000400 | 0x04000500 |
| 0x020068 | 0x00010001 | 0x00000000 | - |
| 0x02006c | 0x00003000 | 0x00004000 | 0x00003000 |
| 0x020070 | 0x06001000 | 0x06003000 | 0x06001000 |
| 0x020074 | 0x14002000 | 0x14001000 | 0x14002000 |
| 0x020084 | 0x3f9f3e9f | 0x60002000 | 0x3f9f3e9f |
| 0x020088 | 0x08bf07ff | 0x5e000500 | 0x08bf07ff |
| 0x0200b0 | 0x00040f00 | 0x00200e10 | - |
| 0x020278 | 0x70006000 | 0x7000400f | - |
| 0x020344 | 0x00000100 | 0x00000500 | 0x00000100 |
| 0x020348 | 0x01900000 | 0x01900500 | 0x01900000 |
| 0x02034c | 0x025807ff | 0x02583000 | 0x025807ff |
| 0x020368 | 0x00000010 | 0x00000100 | 0x00000010 |

Every row where this driver writes something is a row where the board reads
**something else**.  The driver's constants are the vendor's values - the
offsets and the values are right - and the writes do not survive.

The configuration word is the one to watch: 0x7e is the value the tree's own
`zx279133_red_prepare()` writes (the vendor's value 0x9e is that word with
`all_active_mode` set), and the comment in `zx279133-main.c` already says so:
the board read back the tree's 0x7e thirty-four seconds after the probe.

The four words in `cfg[4]` are **not** part of that: 0xff803fff,
0x0100ff80, 0x00100200 and 0x00000020 are what the vendor's board has in
its *indirect command and data* registers, which keep the leftovers of the
last indirect access the vendor's driver made.  This driver's
`zx279133_red_vendor_fixup()` declares that array and never uses it, which
is harmless; but it is also the reason a previous round believed the RED
needed four more words written.

## 5. The other blocks

| block | our offset | the words that differ |
|---|---|---|
| tm/qmg | 0x024000 | 0x005, 0x006, 0x008, 0x018, 0x03e, 0x087 |
| nppu/sipc | 0x004000 | 0x005, 0x006 |
| nppu/smct | 0x00c000 | 0x00c, 0x00d, 0x00e, 0x032, 0x033 |

In these the difference is one or two hardware-derived bits (for instance
0x2bb82fa0 against the vendor's 0x0bb80fa0 - bit 29), so the writes are
landing there.

## 6. The next experiment

`staging/ctl/exp156.txt` writes the vendor's RED, QMG, SIPC and SMCT words
on the board by hand, reads them back, and watches the SMAC's own counters
across the write.  Three questions:

1. does a write to the RED stick at all (0x020004 -> 0x9e, read back)?
2. do the words survive the next twenty seconds, or does something put the
   tree's values back?
3. do the frames move?

## 7. Files

* `staging/ctl/vendor-all2.sh` - the vendor's live NPPT, captured through the
  serial console.
* `staging/ctl/vendor-live.json` - the parsed image of it.
* `staging/ctl/tmp_vendor_live.py` - the parser.
* `staging/ctl/tmp_livediff.py` - the comparison against our board.
* `staging/ctl/tmp_redclass.py` - which of the vendor's words are
  configuration and which are counters.
* `staging/ctl/tmp_blockdiff.py` - lines a vendor block dump up with ours.
* `staging/ctl/re-smac-init-writes-20261023.md` - the vendor's SMAC
  initialisation, word for word.
* `staging/ctl/re-ppu-counters-20261023.md` - where the PPU's per-port
  counters live, and why they are not available here.
