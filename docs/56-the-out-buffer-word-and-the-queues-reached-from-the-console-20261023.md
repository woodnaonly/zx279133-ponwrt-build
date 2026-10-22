# 56 - The RED's OUT buffer word was shifted four bits too far, and the console can now reach the queues and the buffer pool

Date: 2026-10-23

This round turned the "pktlen0 drop" symptom into two hard facts and then
found a real bug in the code that was supposed to fix it.

## Fact one: where the error is detected

`red_monitor()` in the vendor's `np_133.ko` reads the RED's status word at
NPPT `0x200b0` and decodes it bit by bit (`ubfx x1, x19, #4, #1`):

| bit | name |
|---|---|
| 3 | `out_gua_rel_fail` |
| **4** | **`red2qmg_pkt_len_err`** |
| 9 | ... |
| 21 | `req_que_disc` |

The vendor's board reads `0x00040f00` - bit 4 clear.  The mainline board
reads `0x00200e10` - **bit 4 set**.  So the error is detected **at the
RED-to-QMG handover**, and the word is a status word, not a counter.  The
counters are elsewhere: `0x200b8` bits 23..16 hold `pktlen0 drop cnt` and
`0x200bc` bits 31..24 hold `pktlen flt drop cnt`.

The same report settles the direction of the four counters: on the vendor's
board `in fwd == out fwd` and `in drop == out drop`, and every source has an
`in X` and an `out X` pair.  "in" is the RED's input stage, "out" is its
output stage.  The mainline board's `in fwd` grows and `out fwd` stays at
zero, with `out drop == in fwd`: **every frame the RED takes in is dropped on
the way out.**

## Fact two: the length is zero for both directions

Two measurements on the same boot:

* the PC pinging: `in fwd` grows at 1.16 frames/s;
* **every ping process killed**: `in fwd` still grows at 0.72 frames/s.

So the frames that arrive with a length of zero are both the PC's frames
(coming in from a LAN port) **and the board's own transmissions** (going out
to the switch).  The two paths share nothing but the RED's input stage.

## The bug

The RED's per-queue geometry lives in three RAMs that are reached through an
indirect port.  The vendor's `red_set_out_buffer_queue_cfg()` packs a queue's
OUT buffer word as

    guard | (max_space << 12)

and `red_init` programs the 320 PON/UNI queues with `guard = 0x40` and
`max_space = 0x1000`, which is

    0x40 | (0x1000 << 12) = **0x01000040**

The tree's constant said `0x10000040` - the same digits with the "1" four
bits further to the left, i.e. `0x1000 << 16` instead of `0x1000 << 12`, so
**sixteen times too big**.  Every other word of the set was right:

| word | vendor | before | now |
|---|---|---|---|
| IN, q 0x000..0x13f | `0x01800020` | `0x01800020` | `0x01800020` |
| IN, q 0x140..0x17f | `0x00c00020` | `0x00c00020` | `0x00c00020` |
| IN, q 0x180 | `0x00400020` | `0x00400020` | `0x00400020` |
| **OUT, q 0x000..0x13f** | **`0x01000040`** | **`0x10000040`** | **`0x01000040`** |
| OUT, q 0x140..0x17f | `0x00800040` | `0x00800040` | `0x00800040` |
| OUT, q 0x187..0x18c | `0x00400020` | `0x00400020` | `0x00400020` |
| per-queue config | `0xff803fff 0x0100ff80 0x00100200 0x00000020` | same | same |

`staging/ctl/re-red-init.md` §2c lists the four per-queue words as
`0x00010020` for the third one; the disassembly of
`red_set_buffer_queue_cfg()` @ `0x621d4` and the vendor board's own readback
(`red reg=0x9` = NPPT `0x20024` = `0x00100200`) both say `0x00100200`, so the
report has the typo and the driver never had it.

## The console can now reach the two places that are left

Both are reached over an interface the plain register console cannot use, so
both are new debugfs commands:

* the RED's queue RAMs (indirect port):

      redq2 <value>                 the IN buffer queue RAM (ram 2)
      redq0 <value>                 the OUT buffer queue RAM (ram 0)
      redq4 <w0> <w1> <w2> <w3>     the per-queue configuration (ram 4)
      redqr <ram> <addr> <count>    read a RAM back

* the BMU's BPPE table (the map from a buffer index to a buffer, in the
  reserved memory the device tree gives the BMU):

      bppd <count>                  read the first entries
      bppw <count> <mode>           write the whole table:
                                    0 = 16-bit big endian (the tree's)
                                    1 = 16-bit little endian
                                    2 = 32-bit little endian
                                    3 = all zero

The BPPE table is the one thing in the RED's input path that **no** vendor
module touches: `np_133.ko`, the vendor kernel and the stock U-Boot all
leave it alone.  If the hardware maintains it, then the tree's filling of
it with 16-bit big-endian indices is the thing that breaks the lookup, and
mode 3 (leave it alone) is the test.

## What the DIAG dump prints now

`zx279133_diag_dump()` prints the QMG (`0x24000 .. 0x24070`) and the SMCT
(`0xc000 .. 0xc070`) word by word as well, so that every boot carries the
whole picture.

## The QMG and the SMCT

`docs/55` recorded the four QMG words and the ten SMCT words that differ
from the vendor's board, and `zx279133_tm_vendor_fixup()` now writes the
vendor's values.  Writing them at runtime with `regw` changed nothing: the
`in fwd` and `pktlen0 drop` counters stayed in step and `out fwd` stayed at
zero.  They are kept because they are the same board's numbers, but they are
not the difference.

## Reproducing

```sh
echo "regs 0x200b0 16" > /sys/kernel/debug/zx279133-9132   # bit 4 = the error
echo "regs 0x20200 8"  > /sys/kernel/debug/zx279133-9132   # in fwd / out fwd
echo "redqr 0 0 8"     > /sys/kernel/debug/zx279133-9132   # the OUT queue RAM
echo "bppd 8"          > /sys/kernel/debug/zx279133-9132   # the BPPE table
```
