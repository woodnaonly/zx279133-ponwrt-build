# The vendor's truth, and the regression that came with it

Written 2026-10-22. Continues `docs/41-the-flood-tables-and-the-missing-handover-20261021.md`.

This is the round that went back to the vendor's own running system with a
question that had been open since `docs/40`: the board's LAN data plane still
does not move, and everything that had been checked against the vendor was
identical, so the remaining question could only be answered by experiment.

The round produced four pieces of vendor truth, one regression in this
driver's own code, and one hard measurement about where the frames die.

## 1. The vendor's layout of an entry of ERAM table 0x7d

`docs/41` and `staging/ctl/re-eram-7d.md` gave the layout of the MF extension
table as

```
	word1 bytes 4..5    the multicast flood group id
	word1 bytes 6..7    the unknown-DA flood group id
	word2 bytes 10..11  the broadcast flood group id
```

and `docs/41` read the last line as "word 2's *low* half".  That reading is
wrong, and it is wrong in a way that costs the broadcast path, so it was
checked against the vendor's own code rather than against the report.

In `np_133.ko` the entry buffer is a 16 byte array at `x29+0x48`, and the
three setters that write the group ids store a halfword into it:

```
	strh w22, [x29, #0x4c]	multicast, bytes 4..5
	strh w22, [x29, #0x4e]	unknown-DA, bytes 6..7
	strh w22, [x29, #0x52]	broadcast, bytes 10..11
```

* `port_attr_def_mul_flood_group_id_set`, .text 0xbc794
* `port_attr_def_unk_da_flood_group_id_set`, .text 0xbc5b8
* `port_attr_def_brd_flood_group_id_set`, .text 0xbc970

`0x52 - 0x48 = 10`, so the broadcast group id goes to bytes 10..11 of the
entry.  The entry is little endian and word 2 is bytes 8..11, so bytes
10..11 are word 2's **most** significant halfword - not its low half.

The consequence for the driver is exact.  SPA register 17 holds the
broadcast group id in its high half and the vendor's flood count 0x0410 in
its low half, and the entry wants

```
	word2 bytes 8..9    0x0410, the flood count
	word2 bytes 10..11  the broadcast group id
```

which is SPA register 17 **as it stands, unchanged**.  Word 1 is likewise
register 18 unchanged: its low half is the multicast group id and its high
half the unknown-DA one, which is bytes 4..5 and 6..7.

`run120` believed the report's "low half" and swapped the halves of register
17 on the way in.  That put the broadcast group id on bytes 8..9, where no
setter ever put it, and ran the broadcast flood of a port on 0x0410 - a
number that is not a group id at all - instead of on `(p+0x10)<<6`.

`run121` puts it back.

## 2. The vendor's flood group table is empty

The vendor's own system was asked, over `/proc/pon/debug/multicast_groupid_table`,
for the group id table - ERAM table 0x1f, the table that maps a flood group
id to the one destination port that group has.  The vendor's answers, read
one index at a time, are:

```
	gid 64, 128, 192, 256, 320          all 0x0
	gid 1088 .. 1344                   all 0x0
	gid 2112 .. 2368                   all 0x0
	gid 1040 .. 1045                   all 0x0
	gid 0 .. 68                        all 0x0
```

Every entry is zero, at every index the five ports use.  The group ids
themselves come out of the ports' own attribute words, and the vendor's
running system has

```
	spa_port_attr_smac_0   word17 0x04400410   word18 0x00400840
	spa_port_attr_smac_1   word17 0x04800410   word18 0x00800880
	spa_port_attr_smac_2   word17 0x04c00410   word18 0x00c008c0
	spa_port_attr_smac_3   word17 0x05000410   word18 0x01000900
	spa_port_attr_xmac_0   word17 0x05400410   word18 0x01400940
```

so the group id of a port p is `p<<6` (unknown-DA), `(p+0x10)<<6` (broadcast)
and `(p+0x20)<<6` (multicast), and the CPU port's three are 320, 1344 and
2368.  With the table empty, the vendor's flood goes nowhere: **the vendor
does not use the flood group table to reach the CPU.**  The only road a frame
from a jack has into the CPU is the protocol trap, ERAM table 0x18, and the
driver's own comment on `zx279133_9132_proto_init()` already said so.

## 3. The vendor's network topology

The vendor's system on the board has these network devices:

```
	sw     d0:45:8d:84:08:fc   the board's own MAC
	idm0   d0:45:8d:84:08:fe
	idm1   d0:45:8d:84:08:fd
	eth0   d0:45:8d:84:08:ff
	eth1   d0:45:8d:84:09:00   the only member of br-lan
	eth2   d0:45:8d:84:09:01
	eth3   d0:45:8d:84:09:02   down
	pon, pon0, omci
```

`br-lan` has exactly one port, `eth1`, and the board's address
192.168.9.1/24 is on it.  The client is plugged into that same port, and a
ping from the board to the client moves the receive counter of `sw`, of
`eth1` and of `br-lan` by the same ten packets - so all three are on the one
path, and the client's frames reach the CPU through the port the client is
plugged into.

The two `idm` devices are idle: their receive counters never move.  The
vendor's LAN path does not run through the IDM.

## 4. Where the frames die: they never reach the IDM

On the trial kernel, with the client pinging, every counter that could show a
frame was read:

| counter | where | value |
|---|---|---|
| `lan-cpu0` rx | the DSA conduit | 0 |
| `br-lan` rx | the bridge | 0 |
| `lan1..lan4` rx | the four jacks | 0 |
| `eth0` rx | the WAN device | 0 |
| the IDM's own queue counters | `idm+0xc4 ..` | all 0 |
| the client adapter's `ReceivedBytes` | the PC | unchanged, and no ARP entry for 192.168.9.1 |

The client sees exactly nothing: its receive byte counter does not move by
one byte, and it never learns the board's address.  The board's own ping to
the client is answered by nothing.

So the frame does not reach the IDM at all.  Everything downstream of the
IDM - the L2 rewrite, the DSA tagger, the `lan_dsa_active` gate, the port
the descriptor names - is not yet the problem, because nothing has got that
far.

## 5. Two things that were certainly wrong in this driver

### 5.1 The broadcast group id (section 1)

Fixed in `run121`.

### 5.2 The L2 rewrite on the LAN queues

`zx279133_idm_rx_restore_lan_l2()` runs for every frame that comes off an
IDM queue below 8, and it inserts four bytes at offset 12 and writes the
Ethernet type and the IP version word there.

It is for a frame whose type field the PPU took off.  The 9132 is a
transparent MAC-learning bridge (docs/16 §2.2): it does not take the
Ethernet header off, so the frame the CPU is handed still has one.  Applied
to such a frame the rewrite shifts the payload four bytes to the right and
writes the version word twice, so the IP header the stack reads starts four
bytes too far in and the total length it finds there is 0x0800 - 2048 - and
every IPv4 and IPv6 frame is dropped.

The rest of the same file already assumes the frame has its header: the
checksum path reads the IP header at `ETH_HLEN` and the DSA path reads the
frame as a `vlan_ethhdr`.  `run121` makes the rewrite run only on a frame
that has no type field of its own.

## 6. Four knobs, because the rest is decided by experiment

The protocol action table the 9132 traps on is ERAM table 0x18, one entry
per (port, protocol), and the protocol number the chip uses is not written
down anywhere in this tree - the vendor's pairs were copied out of
`switch.ko`'s own `.rodata`, so the numbering is the chip's.  The CPU's own
queue id is the vendor's 0x193, and nothing here says how that number
becomes one of the IDM's sixteen CPU queues.

```
	trapall <port> [action]
		  set all 128 protocol entries of a port.  The action is the
		  vendor's own: 2 hands the frame to the CPU, 0 leaves it to
		  the normal path.

	cqid <base> <out>
		  the CPU base queue id (ERAM table 0x22) and the queue id the
		  outport map gives the CPU's own outport 15 (ERAM table 0x27).
		  The vendor's values are 0x193 and 0x193.

	rmap <reason> <qid>
		  the reason map (ERAM table 0x19): the CPU queue a frame with
		  a given reason is delivered on.

	rxd <queue> <count>
		  the IDM's receive descriptors.  The rings live in coherent
		  memory, so the console can read them: a descriptor the
		  hardware has filled in has an address and a length in it, and
		  a descriptor the driver has released has an address of zero.
		  This is the one piece of evidence that says whether a frame
		  ever reached the IDM at all, and which queue it landed in.
```

## 7. The queue id map, measured and disassembled

`staging/ctl/re-qmap.md` took the queue-id question apart in `np_133.ko`
itself, and the answer is that this driver's three ERAM writes are **bit for
bit the vendor's**:

| what | vendor | ours |
| --- | --- | --- |
| ERAM 0x22, entry 0, word 3, bits 17..25 | `0x193` (`bfi w0, w21, #1, #9` on the halfword at entry byte 14) | `0x193` |
| ERAM 0x27, entry 15 (the CPU's own outport) | real port 15, queue id `0x193` - word `0x64cf` | `0x0f \| (0x193 << 6)` |
| ERAM 0x19, entry 0 (trap reason 0), byte 3 bits 0..4 | `7` | `7` |

The "419" that the vendor's boot log prints

```
[   18.896186] [outport_and_real_port_map_table_init]real_port: 15 real_queue_id:419
```

is **not** a second base.  `outport_and_real_port_map_table_init()`
(.text 0xc06c8) fills ERAM 0x27 with

```
	real_queue_id(p) = 0x193                       p == 15   (the CPU's outport)
	                 = 0x1a3 + map[6p-90]         16 <= p <= count+15
	                 = 0x1b1 / 0x1b3 / 0x1b5     p == 49 / 50 / 51
	                 = 0                           otherwise
```

and the eight dynamic entries (outports 16..23) come out of
`current_wlan_map`, which is a **kernel** symbol that is all zeros at boot.
So the vendor prints 419 eight times because it fills the eight WLAN CPU
queues with `0x193 + 16` and the offset is zero.  Those are the WLAN stack's
queues, not the wired CPU port's: **the number 419 is not what the CPU port
should use, and putting it there was never the right fix.**

Two id spaces are involved and the vendor never converts between them:

* the **TM queue id** is 9 bits wide (0x191, 0x193, 0x1a3, 0x1b1 ...).  It is
  the index of the traffic manager's statistics table and of the per-CPU-queue
  rate-limit table.  ERAM 0x22 and ERAM 0x27 both live in this space.
* the **CPU queue id** is 0..23 and is a DMA channel:
  `cpu_qid_ratelimit_table_set()` (.text 0xb0b44) rejects anything above
  0x17, and the IDM builds exactly **24** RX descriptor queues.

`glb_cfg_table_cpu_base_qid_get()` and `outport_and_real_port_map_table_get()`
have **no caller at all** inside `np_133.ko` - a relocation-level scan of every
`.rela*` section finds only the getter's own show function.  Both tables are
read by the hardware alone, so the mapping from a TM queue id to an IDM RX
queue lives in the silicon, not in any module.  The only queue number the
vendor's software ever writes is the **CPU queue id 7**, for trap reason 0.

## 8. Two experiments that ruled out the tables

`run121`'s four knobs were used on a live board, with the PC pinging through
the jack it is plugged into.

```
=== >>> sent: echo "trapall 2 2" > /d ===
[   746.042989] port 2 traps every protocol with action 2
...
=== >>> sent: echo "cqid 0x193 0x1a3" > /d ===
[   755.605348] the CPU base queue id is now 403 and the outport 15 queue id 419
```

Both knobs did exactly what they said, and neither changed anything:

| read | value |
| --- | --- |
| `lan-cpu0` rx bytes / packets | 0 / 0 |
| `eth0` rx bytes / packets | 0 / 0 |
| every IDM queue counter, queues 0..23 (`nppt+0x2800c4` .. `nppt+0x2800f0`) | 0 |
| `rxd 3 / 19 / 21 / 23` (four descriptors each) | address 0, length 0, metadata 0 |

So the **protocol action table** and the **queue id** are both ruled out: the
frame does not arrive at the CPU queue whatever the trap table says and
whatever the queue id is.  (The 0xf4 and 0x100 words read 0x1000 and 0x800;
those are the ring-depth registers, and they do not move either.)

## 9. Where the frames die, and which way does work

The same board, the same run, with the PC pinging:

```
  eth0:       0       0    0    0    0     0          0         0     1046       9    0    0    0     0       0          0
lan-cpu0:       0       0    0    0    0     0          0         0     2490      21    0    0    0     0       0          0
  lan2:       0       0    0    0    0     0          0         0     1254       9    0    0    0     0       0          0
```

* **Outbound works.**  `lan-cpu0` transmitted 21 packets and `lan2` 9: the
  board's own frames cross the 9132's CPU port and come out of the jack the PC
  is in.  The 9132's CPU port therefore works, the SGMII lane works in that
  direction, and the 9132 forwards to the UNI ports.
* **Inbound does not.**  Every receive counter on the board is zero and every
  IDM queue counter is zero.

That is a one-way link: the CPU's frames reach the 9132 and the PC, and the
PC's frames reach the 9132 and stop there.  Nothing in this driver's table
programming explains that, because the tables only decide *where* a frame goes
once it is in the chip.

## 10. The XMAC0 window is not a plain register file

Reading XMAC0's own words out of the NPPT window, with the trial kernel up:

```
soc reg 0x19140000 (nppt +0x140000) = 0x60010001
soc reg 0x19140004 (nppt +0x140004) = 0x60010001
soc reg 0x19140008 (nppt +0x140008) = 0x60010001
soc reg 0x1914000c (nppt +0x14000c) = 0x60010001
soc reg 0x19140010 (nppt +0x140010) = 0x3e8000c7
... the same four times ...
soc reg 0x19140020 (nppt +0x140020) = 0x80000000
... the same four times ...
soc reg 0x19140030 (nppt +0x140030) = 0x00000000      ... up to 0x14007c
soc reg 0x19140280 (nppt +0x140280) = 0x00000000      ... up to 0x14029c
```

Every 16 byte block reads back the **same word four times**, and everything
else is zero.  So the XMAC0 register block is not a word-per-register file the
way this driver writes it: the four values that do come back are exactly the
three this driver wrote (tx ctrl, rx ctrl, frame cfg) plus their repeats, and
the mode word it writes 2 into reads back 0.

The same block on the vendor side is where the SGMII lane is brought up:

```
[   16.388463] smac_init: smac_pkt_filter = 0x80000001, mac = 0x0
[   16.394286] write smac an ctrl ,mac = 0x0
[   16.398296] read SOPC_CLR_OVER_READY_SMAC = 0x1, mac = 0x0
[   16.404771] write SOPC_SEND_EN_CFG_SMAC,mac = 0x0
[   16.409475] sopc send enable ,mac = 0x0
```

- the vendor's packet filter is **0x80000001**, and this driver writes
  **0x80000000** into the same word (the tree's own XMAC1 program writes
  0x80000001);
- the vendor **writes an auto-negotiation control word per MAC** before it
  reads the ready word, and this driver never does;
- the vendor's ready word reads **1 for all four MACs**, and this driver's
  reads **0 for MACs 0, 1 and 2** and 1 for MAC 3:

```
[    1.666979] zx279133-switch: SOPC mac 0 never became ready (0x00000000); not publishing it
[    1.678054] zx279133-switch: SOPC mac 1 never became ready (0x00000000); not publishing it
[    1.689126] zx279133-switch: SOPC mac 2 never became ready (0x00000000); not publishing it
```

and the vendor's own link report for the same block is

```
[   18.696114] xmac0 is used
[   18.714138] mac 0 link down
[   18.717208] mac 1 link up
[   18.719820] mac 1 phy status changed: 1000M full-duplex
```

whereas this driver's lane snapshot says

```
lane after: XPCS0 status=0x380b link=up XMAC link=0x0000
```

So the next round is the XMAC0 bring-up, not the tables: the packet filter's
low bit, the per-MAC auto-negotiation control write the vendor does before it
reads the ready word, and the link word that reads zero here.

## 11. Two lab mistakes that cost a round each

* **`regs 0x140000 32` does not hang the board.**  It looked like a hang -
  the console stopped answering and never came back - and the obvious reading
  was that reading XMAC0 through the NPPT window takes the AXI bus down.  It
  does not: the board had simply lost power, and it came back into the
  vendor's firmware.  Reading that address works; the whole 32 word dump above
  is from it.
* **`Test-Connection` reports success for a host that is not there.**  The PC
  side ping loop said "ok" 695 times in a row while the board was off and
  `ping.exe` was losing 100% of its packets.  The loop now uses `ping.exe` and
  its exit code.  Every earlier conclusion that rested on "the PC could reach
  the board during the trial kernel" has to be re-taken: the trial kernel
  never answered, and the 695 "ok"s were the vendor's firmware answering
  between the power cuts.

## 12. What the lab needs to survive a power cycle

The board's power is cut every 70 to 660 seconds by a smart plug, and every
experiment has to fit inside one of those windows.  Three things had to be
fixed to make that possible.

* **A console line longer than about forty characters comes out
  truncated.**  A truncated line leaves the shell at its continuation
  prompt, and every command after it is swallowed - which cost several
  rounds before it was understood.  The fix is to hand the board a *script*
  instead of a command: the trial image has busybox's `tftp`, and the loader
  has already proved that the board can reach the PC's tftp server, so the
  experiment is fetched with `tftp -g -r e -l /tmp/e 192.168.9.25` and run
  with `sh /tmp/e`.
* **The trial console needs one Enter before it gives a prompt.**  It prints
  `Please press Enter to activate this console.` and then nothing, so a script
  that waits for `root@ImmortalWrt:~#` waits forever and the power window is
  spent doing nothing.  `staging/ctl/runexp.sh` sends newlines until the
  prompt appears.
* **The trial image has no `tftp` after all**, and the PC's HTTP server is
  unreachable from the board ("Failed to send request: Operation not
  permitted"), so the experiment is typed over the console after all - but one
  short line at a time, at about one line a second.
* **The board spends a lot of its window at the loader's prompt** - the
  autoboot countdown is two seconds wide and the probe has to be quick.  The
  board's loader says `xmac0 is used` while it comes up, which is a marker
  worth having.
* **Only one script may touch the console at a time.**  Two of them
  fighting over `sercmd.txt` cost a round: one of them ate the
  `setenv serverip` line and the loader fell back to the server address it
  had in its environment.
