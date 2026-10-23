# The whole chip, compared with the vendor, word for word

Date: 2026-10-23 (rounds 174-175)

## 0. Where this starts

The vendor's own dump of all twenty-four of the 9132's modules is in
`pon/vendor-live-full.json`: for each module, its base in the vendor's
word indices and every register.  The driver's console prints the same
registers on our board as

```
soc reg 0x1902c004 (nppt +0x02c004) = 0x0ee6b280
```

so the two can be compared directly.  `staging/ctl/cmp_nppt.py` does that:
it takes the last value the console printed for every offset, groups the
offsets by the module they fall in, and prints the differences.

That turns a question that used to need a whole round ("what does the
RED's configuration look like, and is it the vendor's?") into one command.

The bases, in bytes:

| module | base | | module | base |
|---|---|---|---|---|
| sdet | 0x002000 | | red | 0x020000 |
| sipc | 0x004000 | | qmg | 0x024000 |
| spa | 0x008000 | | ssch | 0x02c000 |
| smct | 0x00c000 | | sopc | 0x034000 |
| isu | 0x010000 | | dma | 0x038000 |
| odma | 0x014000 | | bmu | 0x03c000 |
| reorder | 0x018000 | | idm | 0x280000 |
| soam | 0x01c000 | | smac0..3 | 0x040000, 0x080000, 0x0c0000, 0x100000 |

## 1. What was written on the board, and what it did

run143 - the run that rewrites the vendor's NPPT configuration after the
last reset pulse - was booted on 2026-10-23 and measured.  It did **not**
fix the RED's configuration word:

```
soc reg 0x19020004 (nppt +0x020004) = 0x0000007e      the vendor's is 0x9e
```

and it did not make the counters move:

```
soc reg 0x19020204 (nppt +0x020204) = 0x00000007      in forward: never moves
soc reg 0x19020210 (nppt +0x020210) = 0x00000000      out forward: always zero
```

**What did not fix it, measured on the board, with the traffic running:**

| what was written | result |
|---|---|
| `RED_CFG` (0x20004) = 0x9e, the vendor's value | in forward stayed at 7 |
| the CPU port's words 17 and 18 = 0x07c00000 and 0x03c00bc0, the vendor's values | the PC still received nothing |
| all nineteen differing words of the RED's configuration half, written with the vendor's values | in forward stayed at 6, out forward stayed at 0 |
| the SSCH's, the QMG's and the SOPC's differing words, and the BMU's | in forward stayed at 6, out forward stayed at 0 |

So the RED's configuration, the CPU port's queue words and the scheduling
blocks are **not** what stops the frame.

## 2. The differences that are real

The board's own values against the vendor's, on the run143 kernel, with
the traffic running.  Only the words that are *configuration* are listed
here; the counters differ because the two boards have seen different
traffic.

### The RED, 0x020000

| offset | board | vendor | |
|---|---|---|---|
| 0x020014 | 0x08000190 | 0x01000190 | bit 27 |
| **0x02001c** | **0x00000000** | **0xff803fff** | never written |
| **0x020020** | **0x00000000** | **0x0100ff80** | never written |
| **0x020024** | **0x00000000** | **0x00100200** | never written |
| **0x020028** | **0x00000000** | **0x00000020** | never written |
| 0x020030 | 0x00001800 | 0x00003400 | |
| 0x020034 | 0x00000020 | 0x00000000 | |
| 0x020040 | 0x00001400 | 0x00000c00 | |
| 0x020050 | 0x0000ffff | 0x00002600 | |
| 0x020060 | 0x10001200 | 0x10000900 | |
| 0x020064 | 0x04000400 | 0x04000500 | |
| 0x02006c | 0x00004000 | 0x00003000 | |
| 0x020070 | 0x06003000 | 0x06001000 | |
| 0x020074 | 0x14001000 | 0x14002000 | |
| 0x020084 | 0x60002000 | 0x3f9f3e9f | eight bytes of weights |
| 0x020088 | 0x5e000500 | 0x08bf07ff | eight bytes of weights |
| 0x02009c | 0x000b0004 | 0x00000000 | |
| 0x0200a4 | 0x00000007 | 0x08160816 | |
| **0x0200a8** | **0x00000000** | **0x089d089d** | never written |
| 0x0200b0 | 0x00200e10 | 0x00040f00 | the RED's own status word |

### The SSCH, 0x02c000

| offset | board | vendor | |
|---|---|---|---|
| **0x02c040** | **0x00000000** | **0x02020202** | never written |
| **0x02c044** | **0x00000000** | **0x02020202** | never written |
| **0x02c048** | **0x00000000** | **0x81818181** | never written |
| **0x02c050** | **0x00000000** | **0x02020202** | never written |
| **0x02c200** | **0x00000000** | **0x87878787** | never written |

These are the scheduling words for the eight UNI queues.  The vendor's
0x02020202 is four bytes of 0x02, and 0x81818181 is four bytes of 0x81 -
the weights and the shape of a port's queue.

### The QMG, 0x024000

| offset | board | vendor | |
|---|---|---|---|
| **0x0240c4** | **0x00000000** | **0x00000148** | |
| **0x0240d0** | **0x00000000** | **0x111e111e** | |
| **0x0240d4** | **0x00000000** | **0x0000001e** | |
| **0x0240d8** | **0x00000000** | **0x001e0000** | |
| **0x0240dc** | **0x00000000** | **0x001e0000** | |
| **0x0240e8** | **0x00000000** | **0x00000202** | |
| **0x0240ec** | **0x00000000** | **0x0000caca** | |
| **0x0240f0** | **0x00000000** | **0x00008080** | |
| **0x0240f8** | **0x00000000** | **0x00000202** | |

### The SOPC, 0x034000

| offset | board | vendor | |
|---|---|---|---|
| **0x034084** | **0x00000000** | **0xe3481d62** | |
| **0x034088** | **0x00000000** | **0x00000001** | |
| **0x03408c** | **0x00000000** | **0x00000080** | |
| **0x0340a8** | **0x00000000** | **0x00020002** | |
| **0x0340ac** | **0x00000000** | **0x00790079** | |
| **0x0340b0** | **0x00000000** | **0x00830083** | |
| **0x0340b8** | **0x00000000** | **0x00020002** | |
| **0x0340c8** | **0x00000000** | **0x00020002** | |
| **0x0340cc** | **0x00000000** | **0x00790079** | |
| **0x0340d0** | **0x00000000** | **0x00830083** | |
| **0x0340f4** | **0x00000000** | **0x00020002** | |
| **0x0340f8** | **0x00000000** | **0x00d600d6** | |
| **0x0340fc** | **0x00000000** | **0x00850085** | |

### The BMU, 0x03c000

| offset | board | vendor | |
|---|---|---|---|
| 0x03c040 | 0x00900039 | 0x00500009 | |
| 0x03c044 | 0x00200009 | 0x00000000 | |
| 0x03c048 | 0x00300090 | 0x00000050 | |
| 0x03c054 | 0x00000008 | 0x0000000a | |
| 0x03c058 | 0x000000c0 | 0x000001a0 | |
| 0x03c05c | 0x00000001 | 0x00000800 | |
| 0x03c060 | 0x18000000 | 0x34000000 | |
| 0x03c064 | 0x00200000 | 0x00000000 | |
| 0x03c080 | 0x000017a0 | 0x000033b0 | |
| 0x03c088 | 0x00000057 | 0x00000047 | |
| 0x03c08c | 0x00000017 | 0x00000000 | |
| 0x03c094 | 0x00000009 | 0x00000000 | |
| 0x03c0b4 | 0x00000009 | 0x00000000 | |

Writing all of these on the board changes nothing: the in-forward
counter stays where it is and the PC still receives nothing.

## 3. The measurement that does not add up

The DSA ports' own counters are always zero on this board - the DSA
driver has no `get_stats64` (docs/43, section 2.2) - and `lan-cpu0`'s
receive counter is zero as well.  The bridge's own counters are not:

```
br-lan: RX  bytes 3720  packets 47  mcast 11
         TX  bytes 5654  packets 49
lan-cpu0: RX bytes 0  packets 0
         TX bytes 4280  packets 38
```

The bridge receives frames, and the board learns an address - its ARP
table holds `192.168.9.198  3e:88:3b:98:23:9d  br-lan` - and yet the
RED's in-forward counter does not move.

**The address in the bridge's table is not the PC's.**  The PC's hardware
address is `84:47:09:5b:bd:2c` - that is the one the vendor's FDB holds,
learned on port 2 - while `3e:88:3b:98:23:9d` is a random one.  The
trial image carries a NAT rehearsal of its own, a `labhost0` port and a
`labcli0` client in a network namespace, and that is what the bridge is
receiving.  docs/13 saw the same thing on the vendor's board: in an idle
window, twenty-four frames on `smac1`, called "environment traffic".

### The proof from the PC's own side

`pktmon` on the PC, with the traffic running:

```
84-47-09-5B-BD-2C > FF-FF-FF-FF-FF-FF, ethertype ARP (0x0806), length 42:
    Request who-has 192.168.9.1 tell 192.168.9.25, length 28
```

repeated, and **nothing else on the wire at all** - no reply, no frame of
any kind.  The PC's frames leave the PC, reach the cable, and the board
does not answer them.  The PC's receive counter stayed at zero the whole
time and its neighbour entry stayed `Unreachable` with a zero address.

**So the entrance does not work at all.**  Everything measured inside the
chip - the RED's in-forward counter, the SSCH's queue counters, the
bridge's own receive count - was measuring the board's own traffic and
never the PC's.

### What was written, and what it did not do

| what was written | result |
|---|---|
| `RED_CFG` (0x20004) = 0x9e, the vendor's value | in forward stayed at 7 |
| the CPU port's words 17 and 18 = 0x07c00000 and 0x03c00bc0 | the PC received nothing |
| all nineteen differing words of the RED's configuration half | in forward stayed at 6, out forward at 0 |
| the SSCH's, the QMG's and the SOPC's differing words, and the BMU's | in forward stayed at 6, out forward at 0 |
| **SMAC1's speed word (0x800e0) = 0x00011000**, the vendor's value - our driver already wrote that, and the board read back 0x00011200 | the PC received nothing |
| **the SDET's port mask (0x2040) = 0x0fa0001f**, whose low five bits are the four UNI jacks and the CPU port | the PC received nothing |

The last two are worth keeping.  The SDET's mask at 0x2040 was
0x0200000c on our board against 0x0fa0001f on the vendor's, and the speed
word had one bit set that the vendor's does not have.  Both are the right
values now, and neither of them was the fault.

### What is now exactly the vendor's

The SMAC1 block, which belongs to the jack the PC is plugged into, has
twelve non-zero registers on the vendor's board, and **all twelve now
match ours word for word**:

| offset | value | |
|---|---|---|
| 0x080000 | 0x00fa2203 | the MAC's control word |
| 0x080004 | 0x00003fff | |
| 0x080008 | 0x80000001 | the packet filter |
| 0x0800d4 | 0x03e80000 | |
| 0x0800dc | 0x00000063 | |
| 0x0800e0 | 0x00011000 | the speed word |
| 0x0800e4 | 0x00000008 | |
| 0x080110 | 0x00001142 | |
| 0x08011c | 0x0401710f | |
| 0x080120 | 0x00800800 | |
| 0x080300 | 0x8000ffff | |
| 0x080304 | 0xffffffff | |

The port attributes in the SPA memory - the twenty words per port - have
matched the vendor since round 169.  So the MAC the PC's jack belongs to
is programmed the way the vendor programs it, and the frame still does
not arrive.

## 4. What the next round has to do

1. **Find the SMAC's counters.**  The vendor's statistics name them -
   `up frame total pkt`, `up all pkt`, `up unicast pkt` - and on the
   vendor's board they move when the PC sends.  The SMAC block's twelve
   non-zero registers are all configuration, so the counters are further
   up the block.  The vendor's own `module_reg` can dump `smac1`, and that
   dump is the next thing to take from the vendor's live board.
2. **Read the block twice on our board** and find the words that moved
   while the PC was sending.  If none of them moved, the frame never
   reaches the MAC, and everything above the MAC is irrelevant - which
   would put the fault in the PHY or in the path between the PHY and the
   MAC.
3. **The PHY is the place to look next.**  Its registers have never been
   compared with the vendor's either.  The vendor has
   `sw_phyid_read_reg` and `sw_phy_reg_set_readreg` for exactly this.
4. Only then go back to the RED, the SSCH, the QMG and the SOPC - but now
   with the knowledge that the difference list in section 2 is a list of
   things to write **once the path is known**, not a list of suspects.

## 5. Files

| file | what |
|---|---|
| `staging/ctl/cmp_nppt.py` | compares the console's registers with the vendor's dump |
| `staging/ctl/vendor-live-full.json` | the vendor's twenty-four modules |
| `staging/ctl/exp175.txt` | the board-side script that writes the vendor's words |
| `staging/ctl/exp176.txt` | reads the SMAC block twice, thirty seconds apart |
| `staging/ctl/vendor_red.sh` | dumps the vendor's RED block |
| `staging/ctl/vendor_mods.sh` | dumps the vendor's red, sdet, smac and sipc |
| `staging/ctl/serbatch.py` | sends a list of commands, one every 2.5 s |
