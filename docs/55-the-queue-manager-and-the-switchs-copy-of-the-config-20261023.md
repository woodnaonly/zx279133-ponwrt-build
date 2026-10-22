# 55 - The queue manager and the switch's copy of the configuration, compared with the vendor's board

Date: 2026-10-23

`docs/52` put the RED the way this board's vendor firmware has it.  This round
asks a different question: **are the other blocks that belong to the same
queue the same board's?**  The RED, the QMG (the queue manager) and the SMCT
(the switch's own copy of the configuration) are the three halves of one
decision - which queue a frame goes to, how long it is and what colour it is
- and the tree's driver programs all three for a *different* board
(the SR1010).  The vendor's `np_133.ko` programs the RED for this board; the
QMG and the SMCT were left at the tree's numbers.

## What was measured

The trial kernel's own register image was read over the debugfs console
(`regs <offset> <n>`) and compared, word by word, with the vendor board's
console dump under `staging/ctl/vendor-serial-mods/`.  The comparison is
mechanical: `staging/ctl/regdiff.py <our dump> <block base> <vendor file>`.

The blocks that came out **identical** to the vendor's board:

| block | NPPT | result |
|---|---|---|
| SMAC0's low window | `0x40000 .. 0x400ff` | identical, except `port_ctrl` |
| SOPC | `0x34000 .. 0x3403c` | identical |
| the global block's buffer geometry | `0x68`, `0x6c` | identical (`0x3f000900`, `0x801`) |
| the QMG's per-port queue table | `0x24044 .. 0x2405c` | identical (`0x140`, `0x148`, ... `0x170`) |
| the QMG's virtual-rate gap | `0x24068` | identical (`0x01182832`) |
| the SMCT's control words | `0xc004`, `0xc008` | identical (`0x1fff`, `0x09`) |
| the IDM's three ring words | `0x280180 .. 0x280190` | identical |

## The two blocks that did not match

### The queue manager (NPPT `0x24000`)

| offset | vendor | mainline | what the tree writes |
|---|---|---|---|
| `0x24014` | `0x0bb80fa0` | `0x2bb82fa0` | `0x2bb82fa0` (SR1010) |
| `0x24018` | `0x80003000` | `0x80004e00` | `0x80004e00` (SR1010) |
| `0x24020` | `0x80000000` | `0x80000fa0` | `0x80000fa0` (SR1010) |
| `0x24060` | `0x10001000` | `0x40003000` | `0x40003000` (SR1010) |

These four are the QMG's watermarks and its descriptor counters: the high
half of `0x24014` and the low half of `0x24018` are the "out" side, the low
half of `0x24060` the "in" side.  They are the numbers that tell the QMG how
much room a queue has, and they have to agree with the RED's own share
limits - which `docs/52` already set to the vendor's board.

Note that the vendor's `0x24018` low half, `0x3000`, is the RED's
`all out share max` (`0x2006c`), and the vendor's `0x24014` low half,
`0x0fa0`, is the QMG's own "out" watermark.  The mainline pair had the RED
on one board's geometry and the QMG on another's.

### The switch's own configuration (NPPT `0xc000`)

| offset | vendor | mainline |
|---|---|---|
| `0xc000` | `0x1e00` | `0x1f00` |
| `0xc028` .. `0xc030` | `0x19001900` | `0x1b001b00` |
| `0xc034` | `0x1b001fff` | `0x1d001fff` |
| `0xc038` | `0x1fff0d00` | `0x1fff1c00` |
| `0xc03c` .. `0xc058` | `0x1fff1fff` (eight words) | `0x1c001c00` and friends |
| `0xc05c` | `0x1d00` | `0x1a00` |
| `0xc060` | `0x1fff1fff` | `0x1a001a00` |

`0xc028 .. 0xc034` are the per-COS thresholds, `0xc038` the multicast
threshold, `0xc03c .. 0xc058` the eight per-input-port thresholds, `0xc05c`
the NP1 threshold and `0xc060` the IDM scheduler's threshold.  The vendor
runs the input-port thresholds at their maximum, `0x1fff`; the mainline board
ran them at `0x1c00`, which is a different queueing decision.

## What the driver does now

`zx279133_tm_vendor_fixup()` in `zx279133-main.c` writes the vendor's words
into the QMG and the SMCT, right after `zx279133_red_vendor_fixup()` has put
the RED's half back, so all three blocks come from the same board.

The QMG's WOE watermark at `0x20088` is shared with the RED's own copy of it
and was already written by the RED's fixup.

## What was left alone, and why

* **The SMAC's `port_ctrl` (`0x400b00`).**  The vendor's board reads `0x2200`
  and the mainline board `0x7fff`.  The vendor's own `nppt_smac_init()` only
  ORs `0x200` into it, so the vendor's board already held `0x2000` before the
  module ran - something else had written it.  The mainline value is a
  *superset* of the vendor's (every bit the vendor has, the mainline board
  has as well), and the SMACs do receive: the RED's "in fwd" counter moves at
  the rate of the PC's pings.  Clearing bits that are set is a guess, and a
  wrong guess here stops the receive path outright.
* **The BMU's pool geometry.**  The vendor's board runs a pool of `0x3400`
  buffers (`0x20030`, `0x3c060`) and the mainline board `0x1800`, with the
  matching index limits (`0x3c058`, `0x3c05c`).  The vendor's pool needs about
  31 MB of reserved memory and the device tree reserves 21 MB, so matching it
  means moving the memory map.  `staging/ctl/re-bmu-map.md` says a wrong BMU
  geometry produces "wrong buffer addresses / starvation / spurious BMU
  errors" (`0x3c0e0`, `0x3c0e4`) and **not** a zero length at the RED, so it
  is a separate piece of work.
* **The RED's status word at `0x200b0`** (`0x00040f00` on the vendor,
  `0x00200e10` on the mainline board) and the word below it (`0x200b4`).  The
  vendor's `red_monitor()` prints the first as `red2qmg_pkt_len_err`, which
  is the error this whole round is about - but the bit names inside the word
  are not known yet, so nothing was written to it.

## The DIAG dump grew

`zx279133_diag_dump()` now prints the QMG (`0x24000 .. 0x24070`) and the SMCT
(`0xc000 .. 0xc070`) word by word, so every boot prints the whole picture and
the comparison against the vendor's dump can be made from the console log
alone - the smart plug cuts this board's power every few minutes and a trial
kernel lives in RAM, so most boots are over before a shell exists.

## Reproducing

```sh
# our side
echo "regs 0x24000 32" > /sys/kernel/debug/zx279133-9132
echo "regs 0xc000 32"  > /sys/kernel/debug/zx279133-9132

# the vendor's side
echo "-r qmg  reg=0x0 cnt=0x20" > /proc/pon/tm_debug/module_reg
echo "-r smct reg=0x0 cnt=0x20" > /proc/pon/tm_debug/module_reg
dmesg | grep -a " reg="

# the comparison
python staging/ctl/regdiff.py <our dump> 0x24000 staging/ctl/vendor-serial-mods/tm-qmg.txt
python staging/ctl/regdiff.py <our dump> 0x0c000 staging/ctl/vendor-serial-mods/nppu-smct.txt
```
