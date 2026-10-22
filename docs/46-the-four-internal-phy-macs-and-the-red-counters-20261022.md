# 46 - The four internal-PHY MACs, and the RED counters that say where the frames stop (2026-10-22)

`docs/44` closed the LAN receive path as far as the board could show it: the
frames never reached the SoC. `docs/45` took the IDM apart. This round does
the other half - the four MACs in front of the four internal PHYs, and the
switch's own counters - because the answer to "where does the frame stop"
turned out to be readable in the 9132's RED block, and it was not where the
last round was looking.

Everything here comes from this board's own vendor module (`np_133.ko`), from
the vendor's own statistics functions, and from two live boards: the vendor
firmware, which works, and the trial kernel, which does not.

---

## 0. The short version

* **The driver never programmed the four internal-PHY MACs.** Each internal
  PHY sits behind a MAC of its own - the vendor's "smac0..3", a block at NPPT
  + `0x40000 * (mac + 1)` - and `an_ctrl` was still the reset value
  `0x00ba2200`. A frame arriving from a LAN port stopped at the PHY: it never
  reached the 9132, the RED's counters stayed at zero, the IDM's receive
  engine was never started and no receive interrupt was ever raised. Fixed in
  run 124.
* **The RED's counters have exact addresses, and they are not the ones the
  old notes used.** Decoded from the vendor's own statistics functions
  (`red_total_stats` @ `0x65930`, `red_discard_stats` @ `0x65a04`):

  | counter | NPPT byte | field |
  |---|---|---|
  | in fwd | `0x20204` | 32 bit |
  | in drop | `0x20208` | 32 bit |
  | out fwd | `0x20210` | 32 bit |
  | out drop | `0x20214` | 32 bit |
  | pktlen0 drop | `0x200b8` | bits 23..16 |
  | pktlen flt drop | `0x200bc` | bits 31..24 |

  (The two "pktlen" counters are the only two in that pair that are *not*
  full words: each is 8 bits wide inside a word of other counters.)
* **With the SMACs programmed, the frames now reach the 9132** - the "in fwd"
  counter moves at exactly the rate of the PC's pings - **and the 9132 drops
  every one of them at the RED's output stage**, with the two packet-length
  counters moving once per frame. `out drop == in fwd`, `out fwd == 0`.
* **The switch's own hardware says what a valid packet looks like.** NPPT
  `0x20188` counts, in four 4-bit fields, the packets that fail the NP1
  format check: "np1_flag 1 and pktlen = 0", "np1_flag 1 and pktlen > 128",
  "np1_flag 1 and qid < 427 or > 432", "np1_flag 1 and qid 433~438".
  **On the vendor's board both this register and the one next to it read
  zero** - the vendor never uses that path for a frame from a LAN port. On
  the trial kernel the frames are on that path, and they arrive there without
  a length.
* **The vendor's SMAC init does three things this driver did not do**: it
  puts `an_ctrl` back to `0x00ba2203` first, it pulses the MAC's bit in the
  shared soft-reset word at NPPT `+0x2c0004`, and only then does it write the
  `0x00fa2203` that says "running". The pulse is what clears the MAC's own
  receive state. Run 125 adds it.

---

## 1. The four SMAC blocks

`nppt_smac_init` @ `0xda1c` (1252 bytes) walks mac 0..3. Each block lives at
NPPT + `0x40000 * (mac + 1)`, so `0x40000`, `0x80000`, `0xc0000`, `0x100000`.
The sequence, decoded from the disassembly:

```
nppt_smac_set_uni_mode(mac, 0)        @0xd9b0
    read  NPPT word 4*(mac+5)           (0x14, 0x18, 0x1c, 0x20)
    write it back with 0xfc7fffff cleared

an_ctrl (base+0x00) = 0x00ba2203       the MAC, stopped

smac_reset(1 << mac)                  @0xd29c
    v = read(NPPT + 0x2c004)
    write(0x2c004, v & ~(1 << mac))    the reset is asserted
    <a printk>
    write(0x2c004, v | (1 << mac))     and released

an_ctrl (base+0x00) = 0x00fa2203       the MAC, running
an_ctrl2 (base+0x04) = 0x3fff
pkt_filter (base+0x08) = 0x80000000    (the board reads back 0x80000001)
port_ctrl (base+0xb00) |= 0x200
speed (base+0xe0) = 0x00011200         (the board reads back 0x00011000)

bitmap at NPPT + 0x343f0: if an_ctrl bit 13 is set, CLEAR bit(mac);
                           otherwise SET bit(mac); write it back.
then publish: wait for NPPT + 0x34294 + 4*mac, write NPPT + 0x342b0 + 4*mac
finally an_ctrl |= 2
```

Two details are worth keeping:

* **The reset word is shared**, but the vendor only ever touches bit(mac) of
  it, so the XMAC and the PCS are not reset from this path. On the live board
  the word reads `0x00000000`, so the pulse is the only thing that ever
  changes it.
* **The bitmap at `0x343f0` is `0` on the live board**, and the vendor's own
  step is a no-op there, so it is not worth writing.

What the driver does now (run 125) is the vendor's sequence, minus the two
registers that this board must not have written:

| register | vendor writes | board reads back | driver |
|---|---|---|---|
| `base+0x08` pkt_filter | `0x80000000` | `0x80000001` | not written, read only |
| `base+0xe0` speed | `0x00011200` | `0x00011000` | not written, read only |

Writing those two over the board's reset value destroys them: the hardware
then reads back `0x80000000` and `0x00010000`, with the enable bit and bit 12
gone. They are read, not written, and only to show what they are.

## 2. The eight registers that nobody had explained

The live SMAC0 and SMAC1 windows hold eight non-zero registers that the
vendor's `nppt_smac_init` does not touch:

| offset | value | in SMAC1 too? |
|---|---|---|
| `+0x0d4` | `0x03e80000` | yes, identical |
| `+0x0dc` | `0x00000063` | yes, identical |
| `+0x0e0` | `0x00011000` | yes, identical |
| `+0x0e4` | `0x00000008` | yes, identical |
| `+0x110` | `0x00001142` | yes, identical |
| `+0x11c` | `0x0401710f` | yes, identical |
| `+0x120` | `0x00800800` | yes, identical |
| `+0x300` | `0x8000ffff` | yes, identical |
| `+0x304` | `0xffffffff` | yes, identical |

That they are the same in SMAC0 and SMAC1 says they are not a per-port speed
or a per-port MAC address; they are common configuration. Which module
writes them, and what they mean, is still open - see section 7.

## 3. The switch's own packet-format counters

The vendor prints a block of descriptor counters from a function at `.text`
`0xe394c`. The register it reads first is NPPT `0x20188` (word `0x8062`):

```
  w19 = read(NPPT + 0x20188)
  bits  3..0   : "np1_flag 1 and pktlen = 0"
  bits  7..4   : "np1_flag 1 and pktlen > 128"
  bits 11..8   : "np1_flag 1 and qid < 427 or > 432"
  bits 15..12  : "np1_flag 1 and qid 433~438"

  w19 = read(NPPT + 0x2018c)
  bits  7..0   : "all red recv np1"
  bits 15..8   : "sch np1 release cnt"
  bits 23..16  : "np1 drop cnt"

  NPPT + 0x20190 : np1_ring0_desc_cnt (7..0), np1_ring1_desc_cnt (23..16)
  NPPT + 0x201bc : np1_ring2_desc_cnt (7..0), np1_ring3_desc_cnt (23..16)
  NPPT + 0x20198 : np1_ring4_desc_cnt (7..0)
```

So the switch's own hardware knows what a well-formed "NP1" packet is: the
NP1 flag set, a **length between 1 and 128**, and a queue id in one of two
ranges. A second function prints the fields of the descriptor itself, which
is where the names come from: `color`, `queue_id`, `pkt_len`,
`mf_bt_exception_code`, `output_list`, `move_flag`, `np1_flag`, `first_ptr`,
`omci_flag`, `gemport_llid`, `l3_en`, `wan_id`, `cast_type`, `pri`, `vlan`,
`tag_type`.

## 4. What the two boards say

Captured on the vendor's own board while the PC was pinging a LAN port
(`module_reg -r red reg=0x80 cnt=0x16`):

| counter | vendor board |
|---|---|
| in fwd (`0x20204`) | 1490 |
| in drop (`0x20208`) | 4 |
| out fwd (`0x20210`) | 1492 |
| out drop (`0x20214`) | 4 |
| pktlen0 drop (`0x200b8`[23..16]) | 0 |
| pktlen flt drop (`0x200bc`[31..24]) | 0 |
| NP1 format errors (`0x20188`) | 0 |
| all red recv np1 (`0x2018c`[7..0]) | 0 |

And the same traffic on the trial kernel, after the SMACs were programmed:

| counter | trial kernel |
|---|---|
| in fwd | grows at the rate of the pings |
| out fwd | 0 |
| out drop | == in fwd |
| pktlen0 drop | grows, in step with the traffic |
| pktlen flt drop | grows, in step with the traffic |
| in idm fwd | 0 |

**The vendor's board never puts a LAN frame on the NP1 path, and its
in-shaping counters are zero. Ours puts every frame on it, and the frames
arrive without a length.** That is the whole difference, and it is a
difference in how the MAC hands the frame over, not in the RED's
configuration.

## 5. What has been ruled out on the board

* **The RED's register values.** Run 89 wrote the vendor's whole RED register
  set - `0x20004`, `0x20010`, `0x20040`, `0x20050`, `0x20060`, `0x20064`,
  `0x2006c`, `0x20070`, `0x20074`, `0x20078`, `0x20084`, `0x20088`, `0x20278`,
  `0x2027c`, `0x20280`, `0x20344`, `0x20348`, `0x2034c`, `0x20354..0x20364`,
  `0x20368` - into the live chip, in two steps, and the frames kept being
  dropped with the packet-length counters moving. The RED's configuration is
  not the reason.
* **The NP1 thresholds.** `0x20010` and `0x20354..0x20364` hold the vendor's
  `0x00100020` on the vendor's board and `272` in the tree's driver; the
  vendor's value was written and nothing changed.
* **The per-port packet-length filter.** It is field 42 of the SPA port
  attribute table, word 10, and the driver already writes the vendor's value
  (`0x0fa0000e`, min 14, max 16000) for the four UNI ports and the conduit.
* **The TX port and the metadata**, and `RED_CFG = 0x9e` (run 87).
* **The packet filter and the speed word** in each SMAC block: the board's
  own reset value is already the vendor's value.

## 6. What is still open

| # | open | how it will be settled |
|---|---|---|
| 1 | Who writes the eight SMAC registers of section 2, and what they mean | the vendor module's write sites (`tools` in `staging/ctl/redx_regwr.py`); until then, writing the vendor's values is a guess |
| 2 | What puts a frame on the NP1 path, and where `pkt_len` comes from | the same module: the NP1 header is inserted by somebody, and the driver's answer has to name the register |
| 3 | The RED's three queue RAMs | the tree's driver programs them with another board's geometry; the vendor's values are known (RAM 2: `0x20|(0x600<<14)` for q 0..0x13f, RAM 0: `0x40|(0x1000<<12)` for the same range, RAM 4: four words, identical for all 401 queues) but they have never been written on a live board |
| 4 | The QMG's five configuration registers | the vendor's values are known (`0x24014 = 0x0bb80fa0`, `0x24018 = 0x80003000`, `0x24020 = 0x80000000`, `0x24060 = 0x10001000`, `0x20088 = 0x08bf07ff`); whether they are computed from a memory geometry or are fixed is not |

## 7. How to reproduce

```sh
# The vendor's RED window, live, while the PC pings:
echo "-r red reg=0x0 cnt=0x40"  > /proc/pon/tm_debug/module_reg
echo "-r red reg=0x60 cnt=0x10" > /proc/pon/tm_debug/module_reg
echo "-r red reg=0x80 cnt=0x16" > /proc/pon/tm_debug/module_reg
dmesg | grep -a " red reg="

# The same registers on the trial kernel:
echo "regs 0x20204 5"  > /sys/kernel/debug/zx279133-9132
echo "regs 0x200b0 2"  > /sys/kernel/debug/zx279133-9132
echo "regs 0x200b8 2"  > /sys/kernel/debug/zx279133-9132
echo "regs 0x20188 2"  > /sys/kernel/debug/zx279133-9132
```
