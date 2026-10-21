# docs/40 — What the live board says, and what that rules out

**Date:** 2026-10-21
**Subject:** the SK-D840N 9132 LAN datapath, measured on the board rather than
inferred from the vendor's code.

---

## 0. TL;DR

The datapath is **not** broken in the IDM, and it is **not** broken in the
9132's per-port register file. Both of those were the two most likely
remaining suspects after run117, and both are now closed by measurement:

| suspect | verdict | how it was closed |
| --- | --- | --- |
| the IDM control word's bit 24 (the vendor sets it, we do not) | **not it** | our running kernel reads `IDM+0x000 = 0x01ff1007`, which is *the vendor's* value bit for bit, and the IDM transmits with it and with the bit-24 variant alike |
| the IDM's descriptor rings sit in `dmam_alloc_coherent` memory (0x9a080000), not in the vendor's NP DDR pool (0x80b23000) | **not it** | the IDM's own TX-done counter advances for every frame, so the hardware reads those rings |
| the 9132's per-port register file (twenty words per port) | **not it** | all five ports' twenty words were read out of the vendor's live system and out of ours, and they are **identical** |
| the 9132's flood groups (ERAM 0x1f) were empty, and run119 filled them with the CPU port | **not it** | the vendor's own live flood group table is **entirely zero** — the vendor does not use the flood path to reach the CPU either; it uses the protocol trap |

What is left is the one thing that has never been measured on either system:
**the 9132's own forwarding decision for a frame that arrives on its CPU
port.** The frames reach the SoC (32 of the PC's 40 pings arrived at
`br-lan`), the SoC answers them, and the answers leave the IDM — and **not one
byte reaches the PC's network card**.

The single most informative number of the whole round:

```
PC receive bytes before the board's pings : 82685877
PC receive bytes after  the board's pings : 82685877
PC receive bytes delta                    : 0
```

The frames stop somewhere between the 9132's CPU port and the PC's PHY.

---

## 1. The board has two systems on it, and the working one is in flash

The stock ZTE system is in the board's flash and it **works**: with it, the PC
pings the board at 192.168.9.1 with no loss at all, and the board's own
`eth1` (a 9132 UNI jack) and `sw` (the 9132's CPU port) counters move.

```
$ ping -n 3 -w 1500 192.168.9.1
Reply from 192.168.9.1: bytes=32 time=1ms TTL=64      (3 of 3, 0% loss)

$ cat /proc/net/dev          # on the vendor system
  eth1: 38093 rx bytes, 519 rx packets | 34156 tx bytes, 351 tx packets
    sw: 46307 rx bytes, 531 rx packets | 49780 tx bytes, 400 tx packets
  idm0: 0 rx | 220 tx
  idm1: 0 rx | 220 tx
```

That is the reference every measurement below is compared against. It also
settles the architecture, which the earlier rounds had wrong:

> **The vendor's LAN path is `eth1` (a 9132 UNI jack) ↔ `sw` (the 9132's CPU
> port) ↔ `idm0`/`idm1` (the SoC's IDM). It does not go through an XMAC at
> all.**

Proof: on the vendor system, *both* XMACs read back as all zero —
`0x19140000` (XMAC0) and `0x19180000` (XMAC1) are `0x00000000` for TX_CTRL,
RX_CTRL, FRAME_CFG, MODE_CFG and every counter — while the LAN works. The
XMACs belong to the PON side of the chip, which has no light in the lab.

This retires a whole line of work: run117's "the XMAC on the lane to the 9132
was never configured" is a true statement about a register that has nothing
to do with the LAN path. Programming it was harmless; it was also never the
fix. **`XMAC0 TX frames = 0` is the correct value on a board whose LAN works,
and its being 0 on ours is not a symptom.**

---

## 2. The vendor's live state is readable, and that is what made this round possible

The vendor system carries three windows into the chip that the mainline tree
does not have. All three are read-only in the sense that they were only ever
used to *read*.

### 2.1 The NPPT register space, through `/dev/mem`

`/sbin/devmem2` and `/dev/mem` are present and the NPPT window at
`0x19000000` is mapped. **The window is sparse**: an offset with no register
behind it raises SIGBUS (`Bus error`), so a read of the whole window is not
possible; only the defined offsets are.

```
$ /sbin/devmem2 0x19280000 w
Value at address 0x19280000 (0x7f9e23b000): 0x80BE300001FF1007
```

(the tool reads eight bytes and prints them as one 64-bit value; the low
word is the register at the address asked for.)

### 2.2 The 9132's per-port attribute RAM, through `/proc/pon/tm_debug`

```
echo -r spa_port_attr_smac_0 id=0x0 cnt=0xa > /proc/pon/tm_debug/module_ind_ram
```

The RAM names are `spa_port_attr_smac_0` … `spa_port_attr_smac_3` (the four
UNI jacks), `spa_port_attr_xmac_0` (the 9132's CPU port), `spa_port_attr_pon_0`
and `spa_port_attr_xmac_1`. `id` and `cnt` are hexadecimal, `cnt=0xa` is ten
words, and the answers come back in the kernel log.

`staging/ctl/vlive5.sh` reads all five ports (100 words) and
`staging/ctl/vlive9132.py` prints the table and the diffs.

### 2.3 The 9132's flood group table and its ERAM

```
echo 64 > /proc/pon/debug/multicast_groupid_table
echo 31 64 > /proc/pon/debug/eram_table_get
```

---

## 3. The vendor's live values, and ours, side by side

### 3.1 The 9132's per-port register file — identical

The vendor's live twenty words per port, and the same twenty words read out
of our mainline kernel with the driver's own `r <mem> <addr>` command:

| word | what it is | vendor PPU1 | PPU2 | PPU3 | PPU4 | PPU5 (CPU) | ours |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 0 | the port's entry program | 0x0000a6a5 | same | same | same | same | **same** |
| 1 | queue/priority config | 0x0000088f | same | same | same | same | **same** |
| 2 | the untag SVID | 0 | 0 | 0 | 0 | 0 | **same** |
| 3–9 | | 0 | 0 | 0 | 0 | 0 | **same** |
| 10 | | 0x0fa0000e | same | same | same | same | **same** |
| 11–12 | | 0 | 0 | 0 | 0 | 0 | **same** |
| 13 | | 0x00008008 | same | same | same | same | **same** |
| 14–16 | | 0 | 0 | 0 | 0 | 0 | **same** |
| 17 | the broadcast flood word | 0x04400410 | 0x04800410 | 0x04c00410 | 0x05000410 | 0x05400410 | **same** |
| 18 | the unknown-DA / multicast flood word | 0x00400840 | 0x00800880 | 0x00c008c0 | 0x01000900 | 0x01400940 | **same** |
| 19 | the port attribute word | 0x96040001 | same | same | same | same | **same** |

Word 1 was the one open question: the driver never writes it ("the same on
every port; left alone"), and the vendor's value is 0x88f — the queue and
priority configuration, `ICU_COS_GET_EN = 7`, `ICU_PRI_CFG = 273`. **Our
board already reads back 0x88f**, so the hardware's reset value is the
vendor's value and "left alone" was right. Writing it anyway changed
nothing.

### 3.2 The IDM — identical, and transmitting

| register | vendor (live, devmem2) | ours (live, `reg`) | |
| --- | --- | --- | --- |
| `+0x000` control | **0x01FF1007** | **0x01FF1007** | same |
| `+0x004` TX descriptor ring | 0x80BE3000 | 0x9A080000 | different memory, both work |
| `+0x008` RX descriptor ring | 0x80B23000 | 0x9A100000 | different memory |
| `+0x00c` TX config depth | 0x04000000 | 0x04000000 | same |
| `+0x010` | 0x40 | 0x80 | RX threshold |
| `+0x070` | 0x3E8 | 0x7FF | RX depth, self-consistent |
| `+0x074` | 0x210 | 0x210 | same |
| `+0x044`/`+0x048`/`+0x050` interrupt masks | 0x071ffbfb / 0x00200000 / 0x00800404 | identical | same |
| `+0x05c0` | 0x7 | 0x7 | same |

The control word deserves a note, because it reads like a difference and is
not one. The driver computes it as

```c
value = saved;            /* what the bootloader left */
value |= 0x000f0000;
value = (value & 0xf00fffff) | 0x00f00000;   /* bits 20..23 */
value |= 0x00003000;                          /* bits 12 and 13 */
value = (value & 0x8fffffff) | (RX_CFG_DEPTH << 28);
if (zx279133_idm_cfg_bit24) value |= BIT(24);
```

which cannot produce 0x01ff1007 (bit 13 and bit 28 would both be set), and
yet the running kernel reads exactly 0x01ff1007. The vendor's own words are
`0x01ff1007` — so what the board runs is the **vendor's** control word, not
the driver's. The driver has a `zx279133_idm_tx_deconfigure()` that writes the
saved values back over the block, and the saved value is the bootloader’s.
(One of the two readings is wrong; the register accepts a write — writing
0x11ff3007 reads back 0x11ff3007 — so the word is being *restored* rather
than never written. Either way, the word the IDM runs with is the vendor's,
so it cannot be the reason the data path is dead.)

**The IDM's TX path works.** `IDM+0x0b0` is the TX-done counter of the queue
the driver uses, and it advances for every frame the board sends:

```
0x2b → 0x2e → 0x32 → 0x34 → 0x37 → 0x3a     (across one round of pings)
```

so the frames leave the SoC. The descriptor rings in `dmam_alloc_coherent`
memory at 0x9a080000 are reached by the hardware; the "the IDM can only DMA
into the vendor's NP pool at 0x80b23000" theory is dead.

### 3.3 The 9132's flood groups — empty on the vendor too

`multicast_groupid_table` on the vendor system, read for every index the
driver uses and for the neighbourhood:

```
index 0x0 → data 0x0      index 0x440 → data 0x0
index 0x3f → data 0x0      index 0x480 → data 0x0
index 0x40 → data 0x0      index 0x4c0 → data 0x0
index 0x41 → data 0x0      index 0x500 → data 0x0
index 0x7f → data 0x0      index 0x540 → data 0x0
index 0x80 → data 0x0      index 0x840 → data 0x0
index 0xc0 → data 0x0      index 0x880 → data 0x0
index 0x100 → data 0x0     index 0x8c0 → data 0x0
index 0x140 → data 0x0
```

**Every one of them is zero.** The vendor's flood group table is empty, so
every unknown-unicast, multicast and broadcast frame the vendor's 9132
receives is dropped on the floor — the same hole docs/37 describes. The
vendor's CPU port is fed by the **protocol trap** (ERAM table 0x18, ARP
trapped to the CPU), not by the flood path, and by source-address learning.

That makes **run119's flood-group fallback a deviation that cannot help**: it
puts the CPU port into the four jacks' groups, where the vendor has nothing.
The change is safe (it can only make the chip send more frames to the CPU)
but it is not the vendor's behaviour and it is not the fix.

---

## 4. Where the frames actually stop

Measured on our mainline kernel, with the PC pinging the board and the board
pinging the PC, both at once:

```
                 rx packets        tx packets
br-lan              32                 62
labhost0            32                 51
lan-cpu0             0                 71      (the conduit: CPU → 9132)
lan1                 0                  0      rx drop 8
lan2                 0                 52      rx drop 14
lan3                 0                  0      rx drop 14
lan4                 0                  0      rx drop 4
eth0                 0                  9
```

Reading that:

* **The PC → board direction works up to the bridge.** 32 of the PC's 40
  pings arrived at `br-lan`, so the 9132 delivered them to its CPU port and
  the IDM handed them to the stack. The ARP trap does its job.
* **The board → PC direction leaves the SoC** (`lan-cpu0` tx = 71, and the
  IDM's TX-done counter agrees).
* **Nothing arrives at the PC.** The PC's receive byte counter does not move
  at all, and the PC has no ARP entry for 192.168.9.1.
* The `rx drop` on the four jacks is the DSA core dropping the frames it
  receives on a user port without a tag it can use.

`lan2 tx = 52` is the **DSA netdev's** transmit counter, i.e. frames the
bridge handed to the DSA driver. It is *not* evidence that the 9132 put them
on the wire. Nothing in this round shows the 9132 forwarding anything
towards a jack.

---

## 5. What the next round should measure

The gap is now narrow and it is inside the 9132, so:

1. **The 9132's own statistics.** The chip has a statistics engine (SDT type
   4). The per-port TX counters would say whether the chip decided to send a
   frame to the jack and the PHY did not put it on the wire, or whether the
   chip never decided to send it at all. Those are different bugs.
2. **The learning table.** The board's answers are unicast to the PC's MAC.
   For them to reach the jack, the 9132 has to have learned the PC's MAC on
   that jack, and the MAC table is SDT 0x32 — a **hash** table in the 9132's
   alg engine, not in the ERAM pool (docs/37). The driver creates the table
   with the vendor's parameters but cannot reach its contents. If the hash
   table's parameters are not exactly the vendor's, learning silently does
   not happen, the board's answers are unknown-unicast, and the empty flood
   groups drop them. **This is now the leading candidate.**
3. **The IDM's destination port id.** The driver puts port id 5 in the TX
   descriptor (`ZX279133_LAN_TX_PORT`). What the vendor puts there has never
   been read out of the binary.

---

## 6. The bench, and the things that were wrong with it

The round was lost several times to the bench rather than to the chip. These
are fixed now and are worth keeping:

| file | what it is for |
| --- | --- |
| `staging/ctl/sshcmd.sh` | run a script on the board over SSH, on stdin, so nothing has to survive the local shell's quoting |
| `staging/ctl/sshdbg.sh` | run a list of debugfs commands on the board over SSH in one session |
| `staging/ctl/manual_trial.sh` | put the trial kernel on the board by hand: reboot from the console, catch the U-Boot prompt, `tftpboot`, `bootm` |
| `staging/ctl/killloops.sh` | kill the console-typing loops **by pid** |
| `staging/ctl/loops.sh`, `staging/ctl/loopctl.sh` | start/stop the trialloop that keeps the board on the trial kernel |
| `staging/ctl/vlive5.sh` | read the vendor's live per-port attribute RAM for all five ports |
| `staging/ctl/vgroup.sh` | read the vendor's live flood group table |
| `staging/ctl/spaexp.sh` | read our per-port register file port by port |
| `staging/ctl/bittest.sh` | the IDM control word experiment |
| `staging/ctl/getkit.sh` | fetch a run's artifacts; **falls back to the `releases` pages when `releases.atom` does not carry the tag** (the atom feed only has the last ten releases, which is why the flash releases looked missing) |

Three bench facts that cost time and are now written down:

* **Git for Windows has no `pgrep`.** `ps -W` shows the program name, not the
  script, so a loop cannot be found by its script name. The loops have to be
  killed by pid. While one of them was still alive it kept overwriting
  `sercmd.txt`, and the console commands went to the wrong place.
* **The console takes about five hundred bytes on one line.** A command that
  is longer is truncated at the UART and the shell then runs a garbled line.
  Everything has to be split into separate lines — the twenty reads of one
  port are a `for` loop of their own, and each port is its own line.
* **The trial kernel cannot forward a frame, so SSH does not answer while it
  is up.** The board has to be rebooted from the *console* to get back to the
  installed system, and `reboot -f` over SSH only works while the installed
  system is the one that is running.

---

## 7. What the plan's gates look like now

| gate | state |
| --- | --- |
| M0, G1 | passed |
| G2 — the 9132 LAN datapath | **not passed.** The IDM is alive, the 9132's register file matches the vendor's, and the frames stop between the 9132's CPU port and the jack |
| G3 — NAT / M1 | blocked on G2 |
| G4 — flash and roll back | the run119 flash artifacts are downloaded and checked; the flash itself is not done, because the board's stock system is the only working reference there is and it should not be overwritten before G2 is understood |
| G5 — the PON state machine | unchanged (frozen) |
