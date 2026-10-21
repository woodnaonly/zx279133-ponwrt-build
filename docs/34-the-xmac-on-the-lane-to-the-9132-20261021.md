# 34 — the XMAC on the lane to the 9132, and what run 116 proved (2026-10-21)

Two things happened in this round: the ERAM fix was measured on the board
and it worked, and the reason the data path is still dead turned out to be
somewhere the last six rounds were not looking.

## 1. The ERAM pool fix is on the board, and the tables are where they belong

Run 116 changed one line: the ERAM pool starts at block **0x3000**, not at
0x1000.  The measurement on the board says the change is in the running
kernel and that the tables really are there:

```
ERAM tables (id, base block, bits per entry)
  0x18   19624    2        <- the protocol action table
  0x19   19752   32
  0x1b   19820    1
  0x22   19592  128
  0x27   24071   16
  0x7d   24737  128
```

and the raw blocks at those addresses hold data, where in run 113 they
were empty:

```
eram block 19592 = 00000000 00000000 00000000 03272000
eram block 19624 = 0000002a 00000000 00000000 00000000
eram block 19752 = 07000000 00000000 00000000 00000000
eram block 19820 = 00000001 00000000 00000002 00000000
eram block 19860 = ffff00ff 0001ffff 00000000 00000000
eram block 24737 = 00060000 00000000 00000000 00000000
```

So the sixth-layer root cause is closed: the 9132's tables were not
misplaced any more.  (The blocks read at the *old* addresses were empty,
which is the other half of the same fact.)  Everything the vendor's own
byte-level comparison had said about those tables - the 53 of them, the
0x18 protocol action table, the 0x1b isolate matrix, the 0x7d port
attribute - is now on the chip.

**And the data path is still dead.**  That is the point of this document.

## 2. Where the frame stops

The measurement of run 116, on the board, with the PC pinging 192.168.9.1:

| counter | before | after |
|---|---|---|
| `lan-cpu0` rx | 0 | 0 |
| `br-lan` rx | 0 | 8 (the lab's own veth pair, not the 9132) |
| `lan2` tx | 9 | 21 |
| `lan-cpu0` tx | 21 | 33 |
| XMAC0 `tx frames` | 00000000 | 00000000 |

and on the board itself: `ip neigh` says `192.168.9.25 FAILED`, the
board's ARP requests for the PC went out (`lan2` tx and `lan-cpu0` tx both
went up by twelve) and never came back.

So the stack queued twelve frames towards the PC, the switch's conduit
counted them, and the XMAC's own hardware transmit counter stayed at zero.
**The frames never reached the wire.**

The board's own log says why, and it had been saying it since the first
round:

```
lane after: XMAC0 tx=0x00000000 rx=0x00000000 frame=0x00000000
            mode=0x00000000 duplex=0x00000100 misc=0x00002000
```

Those six numbers are the XMAC0's own control words: TX_CTRL, RX_CTRL,
FRAME_CFG, MODE_CFG, DUPLEX and MISC_CFG.  **Four of them are zero.**  The
MAC on the far side of the lane - the one that faces the 9132's CPU port
- was not configured by anybody: not by the bootloader, and not by this
driver.

## 3. The seventh root cause: the XMAC was skipped with the PCS

The driver had two ways to bring the lane up:

```c
	if ((status & 0x3800) == 0x3800) {
		/* the bootloader left the PCS link up; inherit it */
		zx279133_switch_sopc_publish(priv);
		goto report;                    /* <- and nothing else */
	}
	... program the PCS ...
	zx279133_switch_xmac_configure(priv);
```

The reasoning behind the `goto` was right and is worth keeping: the PCS
must not be reprogrammed once the bootloader has brought it up, because
the 1G SGMII profile takes it from 0x380b to 0x280b and a lane that was
carrying traffic stops carrying it.  But the XMAC is a *different block*.
It is programmed by `zx279133_switch_xmac_configure()`, which was only
called on the other branch - so on this board, where the bootloader
always leaves the PCS up, the XMAC was never programmed at all.  A MAC
whose control words are all zero is not a MAC that transmits.

The vendor's own module programs it at load time:

```
nppt_smac_init -> xmac_init_by_port(4) / (5)
                 -> xamc_init_conf_by_speed()   (np_133.ko 0x1a200)
                 -> xmac_tx_rx_enable()          (np_133.ko 0x1a114)
```

`xamc_init_conf_by_speed()` is six stores and nothing else:

| register | value |
|---|---|
| TX_CTRL (+0x0000) | 0x00010000, then the speed bits |
| RX_CTRL (+0x0010) | **0x3e8000c6** |
| FRAME_CFG (+0x0020) | **0x80000000** |
| MODE_CFG (+0x0280) | 2 |
| MISC_CFG (+0x3400) | \|= 0x200 |

and `xmac_tx_rx_enable()` then sets bit 0 of TX_CTRL and of RX_CTRL -
which is the enable, confirmed by its own disable functions clearing
exactly that bit.

The values this driver used before were the tree's RTL8372N driver's:
RX_CTRL `0x3e800086 | IPC` and FRAME_CFG `0x80000001`.  That driver drives
the same MAC towards a *different* far side - an external RTL8372N, not
the 9132's CPU port - and the "IPC" bit `0x200` is not even in the same
register there: the vendor puts it in MISC_CFG.  Two values were wrong,
and neither had ever been exercised on this board, because the function
was never called on it.

**Run 117 (1df7f54) programs the XMAC on the inherited lane as well**,
with the vendor's values, and does not repeat the reset (the soft reset
word is shared with the PCS; the XMAC is at its reset state already).

## 4. One more word of the IDM image

The driver had taken its whole IDM register image from `plat_132.ko`, the
"CPU133" variant.  That is not this board's module: the only IDM bring-up
on this board is `np_133.ko`'s own `idm_init()`, and it does not write
the nine words at IDM +0x014..+0x034 at all.  Those words are the
per-queue routing fields of RX queues 6..23 - which interrupt group and
which thread a queue's completion is reported on - and the hardware keeps
its reset default of `0x00010001`, which is what U-Boot leaves behind and
what a working board runs with.

The driver was stamping `plat_132.ko`'s `0x00800080` into all nine, while
using `np_133.ko`'s interrupt-group words at +0x044..+0x050 at the same
time - and those are derived from the very field that had just been
overwritten.  The two halves of the vendor's scheme contradicted each
other, and a completion reported on a group that does not exist never
reaches the driver.

Run 117 writes the reset default instead.  The full diff of the IDM
bring-up is in `staging/ctl/re-idm-rx.md`.

## 5. The console can now reach the SoC's own registers

The kernel's `/dev/mem` refuses every device address, so on a running
trial kernel there was no way to read or write a register outside the
SPA window - and the XMAC and the IDM are exactly where the questions
are.  Run 117 adds a way in:

```
echo "reg 0x140010"    > /sys/kernel/debug/zx279133-9132   # read one word
echo "regw 0x140010 0x3e8000c6" > /sys/kernel/debug/zx279133-9132
echo "regs 0x280000 8" > /sys/kernel/debug/zx279133-9132   # dump a range
```

The offset is relative to the network processing block's base at
0x19000000, so: the SPA window at +0x8000, the XMAC0 at +0x140000, the
XMAC1 at +0x180000, the IDM at +0x280000.  The absolute address is
printed with every value.

**The numbers need the 0x prefix.**  The console parses a number with
`kstrtou32(tok, 0, ...)`, so a bare `140010` is read as decimal.

This is what turns the next round of this from a seventy-five-minute
build into a one-minute experiment: a register can be put back to the
vendor's value and watched, without a rebuild.

## 6. What is still open

* The IDM's other differences from `np_133.ko`: the buffer-pool ring
  words at +0x10c/+0x110 (the vendor writes `((depth-1) << 10) | 0x100`,
  the driver writes a bare `0x1000`), the RX descriptor ring depth at
  +0x70 (the vendor writes `depth - 0x18`), and the fact that the driver
  polls 16 of the hardware's 24 RX queues.  All of them can now be
  tested live.
* ERAM table 0x1f, the flood group id table, which the driver never
  writes - see `staging/ctl/re-spa-attrs.md`.  It is probably not what
  stops the board: the vendor's own flood group holds the WAN port and
  nothing else, and the CPU receives frames through the protocol action
  trap (0x18), not through flooding.
* Whether the 9132's trap path works at all once the XMAC is up.  That
  is the first thing run 117's measurement will say.
