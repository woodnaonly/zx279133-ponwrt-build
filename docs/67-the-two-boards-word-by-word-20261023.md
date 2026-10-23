# 67 - The two boards, word by word (2026-10-23)

This is the record of one day of comparing our board with the vendor's,
and of what it changed about the search for the LAN data path.  Nothing
here is a theory that has not been measured; where a measurement is missing
it says so.

The short version: the four internal-PHY MACs are *identical* between the
two boards, configuration and counters both.  Everything that differs is in
the traffic manager, and every one of those words was already written with
the vendor's own value in `zx279133.h` - so the write itself was never the
question.  The question was *when* the write happened, and it turns out that
the LAN child devices are probed after this driver and reset the chip's
MACs on the way, which takes the traffic manager's configuration with it.

## 1. The vendor's live NPPT, in full

The vendor's 4.19 system exposes `/proc/pon/tm_debug/module_reg`, which
prints a register window and the module's own header:

    nppt smac smac2 flag=2 base=0x30000 reg_cnt=0x1000
      smac2 reg=0x0 val=0xfa2203

The header carries the base, so one pass over the module names gives both
the names and where they live.  The base is a **word** index, so the byte
offset is `base x 4`: `smac0 base=0x10000` is byte `0x40000`, `sopc
base=0xd000` is byte `0x34000`.  Every one of the module bases was checked
against the vendor's own disassembly and against the offsets this driver
uses, and all of them agree.

Two things had to be got right for the capture to be worth anything:

* **The kernel log is a ring buffer.**  A dump of 256 words is 256 lines,
  and after a dozen modules the buffer is full: its line count stops
  changing, so "the log since line N" comes back empty and every module
  after that one looks as if it had no registers at all.  The buffer is
  therefore cleared (`dmesg -c`) before every dump.
* **The capture has to run over ssh, not over the serial console.**  The
  console capture of 2026-10-23 17:40 was 813 kB and was truncated in the
  middle of `smac0`; the ssh capture gets all twenty-four modules and their
  counter windows in one pass.

`staging/ctl/vendor_full.py` does it, `staging/ctl/vendor-live-full.json`
is the result: twenty-four modules, 256 words each, plus the counter
window (registers `0x1c0`..`0x21f`) of the six MAC blocks.  The PC pings
the board for the whole pass, so the counter words are the counters of a
working data path.

The modules and their bases, in the vendor's units and in ours:

| module | vendor base | our byte offset |
|---|---|---|
| nppt | 0x0 | 0x000000 |
| sdet | 0x800 | 0x002000 |
| sipc | 0x1000 | 0x004000 |
| spa | 0x2000 | 0x008000 |
| smct | 0x3000 | 0x00c000 |
| isu | 0x4000 | 0x010000 |
| odma | 0x5000 | 0x014000 |
| reorder | 0x6000 | 0x018000 |
| soam | 0x7000 | 0x01c000 |
| red | 0x8000 | 0x020000 |
| qmg | 0x9000 | 0x024000 |
| ssch | 0xb000 | 0x02c000 |
| woe_sch | 0x81000 | 0x204000 |
| sopc | 0xd000 | 0x034000 |
| dma | 0xe000 | 0x038000 |
| bmu | 0xf000 | 0x03c000 |
| idm | 0xa0000 | 0x280000 |
| crm | 0xb0000 | 0x2c0000 |
| smac0..3 | 0x10000..0x40000 | 0x040000..0x100000 |
| xmac0, xmac1 | 0x50000, 0x60000 | 0x140000, 0x180000 |

## 2. The comparison

`staging/ctl/tmp_livediff2.py` puts the two on the same footing and prints
the differences.  The result of the first pass (round 157, the four MACs'
configuration and counter windows, the red, the queue manager, the sipc and
the switch's own copy of the configuration):

| block | words that differ |
|---|---|
| smac0, smac1, smac2, smac3 | **0** |
| xmac0, xmac1 | 12 each, but see below |
| red | 28 |
| qmg | 15 |
| sipc | 6 |
| smct | 19 |

The MAC blocks being identical is the important half of that table.  It
means the four internal PHYs' MACs are programmed exactly as the vendor's
are - the same `an_ctrl` (`0x00fa2203`), the same `an_ctrl2` (`0x3fff`), the
same packet filter (`0x80000001`), the same speed word, the same port
control (`0x2200`) - and that their counter windows agree as well.

The xmac0/xmac1 differences are a mistake in the comparison, not in the
board: `0x140000` is the **XPCS**, not the MAC's configuration block.  The
vendor's own dmesg says "xmac0_pcs_base is 0x140000", and the vendor's
live xmac0 block is all zeroes, which is what a PCS that nobody programs
looks like.  Our driver does program it (it has to, the lane needs it).

## 3. The words that differ, and what is written into them

Every one of the ninety-two differing words was checked against
`zx279133.h`.  **Every one of them is already written with the vendor's
own value.**  Examples:

| offset | our constant | vendor's board | what we read back |
|---|---|---|---|
| 0x020004 | `0x9e` | `0x9e` | `0x7e` |
| 0x020084 | `0x3f9f3e9f` | `0x3f9f3e9f` | `0x60002000` |
| 0x020088 | `0x08bf07ff` | `0x08bf07ff` | `0x5e000500` |
| 0x024014 | `0x0bb80fa0` | `0x0bb80fa0` | `0x2bb82fa0` |
| 0x00c000 | `0x1e00` | `0x1e00` | `0x1f00` |
| 0x00c028 | `0x19001900` | `0x19001900` | `0x1b001b00` |

So the driver is not writing the wrong numbers.  Two possibilities remained:
the write is refused, or the write is undone afterwards.

## 4. The write is not refused

Round 160 answered that on the board.  The debugfs `regw` command writes a
word and reads it back; the gate register at NPPT + 0x00b8 was read first
(it is 0, so the auto-gate is not what stands in the way):

    0x020004 = 0x0000009e   ->  reads back 0x0000009e
    0x020084 = 0x3f9f3e9f   ->  reads back 0x3f9f3e9f
    0x020088 = 0x08bf07ff   ->  reads back 0x08bf07ff
    0x020040 = 0x00000c00   ->  reads back 0x00000c00

and they stayed.  The same write, minutes later, on the same board, on the
same register - the value stays.

Six words in the red block are the exception: they read back the old value
whatever is written into them, so they are derived, not configuration:

| offset | written | read back |
|---|---|---|
| 0x02009c | 0 | 0x00070004 |
| 0x0200a4 | 0x08160816 | 0x00000003 |
| 0x0200a8 | 0x089d089d | 0x00000000 |
| 0x0200b0 | 0x00040f00 | 0x00200e10 |
| 0x0200b4 | 0 | 0x00060000 |
| 0x0200b8 | 0 | 0x00030000 |

## 5. The write is undone afterwards

The driver's own register image is printed at the very end of its probe -
after the LAN child devices have been probed - and it reads the red's
configuration word as **0x7e**, which is the value the tree's own
`zx279133_red_prepare()` writes and the only thing that ever writes it.
The vendor's board reads 0x9e.

The LAN child devices are probed after this driver, and the switch driver
resets the chip's MACs while it does that: the reset word at NPPT +
0x2c0004 is pulsed per module.  The configuration is therefore written
again after every driver has had its say, and the register image that is
printed then is the one worth reading.

## 6. The physical layer is fine, and now it is measured

The DSA ports of this board carry a fixed-link, so their carrier is
declared rather than measured - `lan2` says "up" whether or not a cable is
in it.  The claim "the PHY link is fine" had therefore never been tested.
Round 158 read the four internal PHYs directly:

| PHY | BMCR (reg 0) | BMSR (reg 1) |
|---|---|---|
| 0x0a | 0x1040 | 0x7949 |
| **0x0b** | **0x1140** | **0x796d** |
| 0x0c | 0x1040 | 0x7949 |
| 0x0d | 0x1040 | 0x7949 |

0x0b is the only one that finished its auto-negotiation (BMSR bit 2, the
link status, and bit 5, "auto-negotiation complete", are the two that
differ), and it is the only one whose BMCR has bit 8 set, the 100 Mb/s
full-duplex capability.  All four answer 0x84b9:0x5032, the chip's own
id.  The PC is on PHY 0x0b, which is SMAC1, which is DSA's `lan2`, and the
link is up and negotiated.

The vendor reads the link out of register 0x1a of each PHY; on PHY 0x0b it
reads 0x1ac7.

## 7. Two false leads, closed

* **The SOPC ready word is not a lead.**  The vendor's own live board reads
  **0** in all four ready words (`0x34294 + 4 * mac`), although its own
  kernel log shows them as 1 at init.  Our board reading 0 is therefore
  exactly what the vendor's board does.
* **The speed word is not a lead either.**  The vendor writes
  `base + 0xe0 = 0x00011200` and reads back `0x00011000` - bit 9 does not
  stick on the vendor's board either.  Our board reads back `0x00011000`
  as well, which is the same value the vendor's board reads.

And one more thing that is not a lead but was measured: the speed and the
duplex are not in a register of their own.  They are in `an_ctrl` bits 13
and 15:14, and the vendor's own init value `0x00fa2203` already says 1000
Mb/s full duplex.  There is no separate RGMII delay register for the
internal PHYs either: the only two words that exist (`NPPT + 0x24` and
`NPPT + 0x30`) belong to a path this board does not take.

## 8. What the counters say

The word at SMAC1 + 0x714 is not a frame counter - it goes *down* on the
vendor's board, so it is some kind of credit or quota.  It is worth having
because it moves: 0x80e39 after round 157, 0x80f07 after round 158, and it
does not move again in the next thirty seconds.  Something reached the MAC
while the PC received nothing at all.

## 9. The bench, and two fixes to it

* **The recorder now stops the loader's autoboot by itself.**  The loader's
  countdown runs for a couple of seconds after its banner, and the board
  then boots the vendor's firmware by itself.  A runner that polls for the
  banner from the outside is always a few seconds too late, which is how
  round 157 and round 160 lost their boards.  The recorder sees the banner
  the moment it arrives and sends the space itself.
* **A trial kernel's reboot does not come back to the loader on this
  board.**  The kernel prints "reboot: Restarting system" and the board
  never speaks again - no U-Boot banner, no shell, and magic-sysrq gets no
  answer either, so the CPUs are gone.  A reboot of the *vendor's* system,
  over ssh, puts the whole U-Boot banner in the log (measured 18:03), so
  the board is not dead in general; it is this kernel's reboot that does
  not come back.  The recovery is the smart plug, which cuts the board's
  power every fifteen minutes or so, and the recorder's new reset line
  (DTR and RTS, which the board's own kernel watches as "RESET").

## 10. Files

| file | what it is |
|---|---|
| `staging/ctl/vendor_full.py` | reads the vendor's whole live NPPT over ssh |
| `staging/ctl/vendor-live-full.json` | the result: 24 modules, config and counters |
| `staging/ctl/tmp_livediff2.py` | compares our board with it, word by word |
| `staging/ctl/tmp_livediff3.py` | the same, over every module |
| `staging/ctl/exp161.txt` | the experiment that writes the vendor's words by hand |
| `staging/ctl/re-uni-port-init-20261023.md` | the reverse engineering of the UNI ports |
