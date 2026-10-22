# 57 - The blocks nobody had compared: the schedulers, the GREG window and the RED's ingress classes

Date: 2026-10-23

The register comparison had walked the RED, the QMG, the SMCT, the BMU, the
GREG, the SOPC and the SMACs.  It had never walked the four scheduler blocks,
and it had only looked at the first half of the GREG window.  A bench round
(`staging/ctl/exp119.txt`, `staging/ctl/ourdiff.py`) dumped all of them and
diffed them against the vendor's own console dumps in
`staging/ctl/vendor-serial-mods/`.

The result is a short list of real differences, and one of them explains the
whole symptom.

## The RED's ingress classes: our frames take the wrong way in

The RED keeps an `in X` / `out X` pair of counters per ingress class.  On the
vendor's board (`tm-red.txt`):

| word | counter | vendor |
|---|---|---|
| 0x70 | `in idm fwd cnt` | 0x5ff |
| 0x76 | `out idm fwd cnt` | 0x5ff |
| 0x29 | `in dn fwd cnt` / `out dn fwd cnt` | 0x0563 / 0x0563 |
| 0x81 | `in fwd cnt` | 0xb6d |
| 0x84 | `out fwd cnt` | 0xb6d |

On our board:

| word | counter | ours |
|---|---|---|
| 0x70 | `in idm fwd cnt` | 0 |
| 0x76 | `out idm fwd cnt` | 0 |
| 0x29 | `in dn fwd cnt` / `out dn fwd cnt` | 0x0167 / 0 |
| 0x81 | `in fwd cnt` | grows |
| 0x84 | `out fwd cnt` | 0 |

Two things follow.

1. **All of our traffic enters the RED through the `dn` class**, and none of it
   leaves: `in dn fwd` grows, `out dn fwd` stays at zero, and the aggregate
   `out drop` equals the aggregate `in fwd`.  The vendor's board moves the same
   class through the RED without losing a frame.
2. **Nothing of ours uses the `idm` class at all** - the vendor's board has
   1535 frames in it.

So the frame is not lost on the way in, and it is not lost because of a wrong
class.  It is lost between the RED's input stage and its output stage, and the
output stage's per-queue space accounting is the OUT buffer queue RAM.

## Which queue, and why the old word was fatal

The OUT buffer queue RAM (ram_id 0) is programmed by
`red_set_out_buffer_queue_cfg(q, guart_space, max_space)` in the vendor's
`np_133.ko`, whose packing is (disassembly of `0x61abc`):

```
00061b64  add      x2, x29, #0x60
00061b68  add      w20, w22, w20, lsl #12      ; value = arg2 | (arg1 << 12)
```

with `arg2 <= 0xfff` and `arg1 <= 0xffff`, i.e. **the word is
`guart_space | (max_space << 12)`** - twelve bits of guard and sixteen bits of
space.

`red_init` programs it like this:

| queues | arguments | word |
|---|---|---|
| 0x000..0x13f | 0x40, 0x1000 | 0x01000040 |
| 0x140..0x17f | 0x40, 0x0800 | 0x00800040 |
| 0x187..0x18c | 0x20, 0x0400 | 0x00400020 |

The mainline driver had `0x10000040` for the first range.  In the vendor's
layout that word is `guard = 0x040`, `max_space = 0` - the `0x10000000` bit
sits four bits above where the space field ends.  **All 320 PON/UNI queues had
zero egress space**, which is exactly the "every frame is accepted and every
frame is dropped on the way out" signature, with the length filter reporting
the empty queue as a length of zero.

The two other ranges were already the vendor's values, so the fix only moved
the UNI range - and the fact that the symptom was there at all says the
traffic is on a UNI queue rather than on one of the 0x140..0x17f queues,
which were never wrong.

`a462fb1` changes that one constant to 0x01000040.

## The scheduler blocks

| block | differences | what |
|---|---|---|
| USCH (0x28000) | 1 | `USCH_CFG` is 0x7f on ours, 0x77 on the vendor - bit 3 is the only difference.  The driver ORs `ZX279133_USCH_CFG_MASK` (0x7f) into the saved value, so it forces that bit on. |
| SSCH (0x2c000) | 6 | `SSCH_AGING_TIME` 0x0ee6b281 vs 0x00989680 (250 ms vs 10 ms); `SSCH_SPEND_BYTE` 0x0814 vs 0x1018; four words at 0x2c040..0x2c050 that the vendor has (0x02020202, 0x45454545, 0x23232323, 0x02020202) and we have as zero. |
| WSCH (0x2c400) | 0 | identical |
| WOE_SCH (0x2c800) | 0 | identical |
| UOPC (0x30000) | 0 | identical |
| DMA (0x38000) | 0 | identical |
| SOPC (0x34000) | 13 | all of them zero on our side (0x34084..0x340fc) |

`SSCH_SPEND_BYTE` is the vendor's own `tm_ssch_initial()`:

```
00116b64  mov      w0, #0x10
00116b68  bl       #0x6a628   <ssch_set_loop_spend_byte>   ; the high byte
00116b70  mov      w0, #0x18
00116b74  bl       #0x6a7dc   <ssch_set_spend_byte>         ; the low six bits
```

0x10 and 0x18, which together are 0x1018 - the vendor's value.  The driver
writes 0x0814.

None of these is on the path that is broken (the SSCH is the PON-side
scheduler and the SOPC words are downstream of the QMG), so they are recorded
rather than changed.

## The GREG window

The first half of the GREG window, compared word by word:

| NPPT | vendor | ours | note |
|---|---|---|---|
| 0x10 | 0x000f8146 | 0x000f804e | bit 8 and bit 3 |
| 0x54..0x64 | 0x81a08000 ... | 0x9c200000 ... | the BMU's own base addresses - different DDR layout, same layout rules |
| 0x88 | 0x71 | 0x671 | |
| 0x8c | 0x71 | 0x670 | |
| 0x94 | 0x380b | 0x280b | |
| 0xc0 | 0 | 0x8000 | |
| 0xc4 | 0x440 | 0x400 | |
| 0xd0, 0xd4 | 0x400 | 0x410 | |
| 0xd8 | 0x08020000 | 0x00020000 | bit 27 |
| 0x184, 0x188 | counters | counters | live |

The BMU's base addresses are worth a note, because they settle an old puzzle.
`tm_bmu_init` in the vendor's `np_133.ko` writes five base addresses, and it
writes them with `fpga_nppt_write_reg(0x15 ... 0x19, value)`.  The register
numbers look like GREG word indices, not like the BMU block's own
`0xf015..0xf019`, which is what made the earlier reports read them as "a
register nobody writes".  They are GREG word indices: the GREG block is module
0 of the NPPT window, so word 0x15 of module 0 is NPPT byte 0x54, and the
vendor's board reads 0x81a08000 there.

That also fixes how `fpga_nppt_write_reg(w0, w1)` has to be read.  Its
implementation (disassembly of `0x107c18`) is

```
00107c2c  ubfiz    x2, x0, #2, #0x20      ; x2 = w0 << 2
00107c30  ldr      x3, [x3]               ; x3 = nppt_base
00107c34  str      w1, [x3, x2]           ; nppt_base[w0 * 4] = w1
```

so **the first argument is a flat global NPPT word index and the byte address
is `w0 * 4`** - 0xf000 is NPPT 0x3c000 (the BMU), 0x800c is NPPT 0x20030 (the
BMU's pool counter) and 0x0015 is NPPT 0x54 (the BMU's BPPE base in the GREG
window).  Reading the high half of the argument as a "module number" gives the
same addresses, but it is the same number seen twice, not two fields.

And the vendor **does** write the BPPE table, which two earlier reports said
it did not:

```
000738ac  ldr      w3, [x4]                   ; the pool size
000738b8  rev16    w3, w1                      ; 16-bit big endian
000738bc  strh     w3, [x0, x1, sxtw #1]       ; at the BPPE base, 2 bytes per entry
```

which is what the tree's `zx279133_bmu_prepare()` does with
`iowrite16be(i, eth->bmu_mem + 2 * i)`.  The vendor's pool is 0x3400 entries,
the tree's is 0x1800; the format is the same.  The BPPE table holds no length
field, so a wrong entry there resolves the wrong buffer rather than a wrong
length - it is not the cause of a zero length.

## What is left

The RED is now programmed exactly the way the vendor's board is programmed:
`RED_CFG` 0x9e, all nine share-max registers at the vendor's values, the IN
and OUT buffer queue RAMs at the vendor's words, the per-queue config RAM at
the vendor's four words, the QMG's four words at the vendor's values and the
SMCT's ten words at the vendor's values.

If `a462fb1` does not move `out fwd`, the next candidates are the ones this
round did not settle: the BMU's pool geometry (0x1800 against the vendor's
0x3400 buffers, which needs a larger reserved-memory area), the BPPE table's
entry width, and the `dn` ingress class itself.
