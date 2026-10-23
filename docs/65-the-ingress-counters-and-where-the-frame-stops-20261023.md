# 65 - The ingress counters, the vendor's oracle, and where the frame actually stops (2026-10-23)

This file records what the 2026-10-23 rounds established, in the order the
evidence arrived, and it states the instrument each step used.  The short
version is at the bottom.

Every number here is from a live board.  The vendor's numbers are from the
vendor's own stock system on the same cable, the same PC and the same board.

---

## 1. The vendor's system, measured with its own oracle

The vendor's stock OpenWrt exposes a statistics oracle:
`/proc/pon/debug/start_statistics` and `.../stop_statistics` print the 9132's
own counters to the kernel log.  A live run with the PC pinging
(`staging/ctl/vendor-oracle.sh`, `staging/ctl/vendor-oracle-20261023.out`):

```
Ping statistics for 192.168.9.1:  Sent = 2, Received = 2, Lost = 0 (0% loss)

######## MCODE ########
port id  2 input uni cnt(32bit)                       11
port id  2 input mul cnt(32bit)                        2
port id  2 input brd cnt(32bit)                        5
port id 15 input uni cnt(32bit)                       16
port id 15 input brd cnt(32bit)                        7
protocol action trap cnt(32bit)                       18
mcode_all_drop_cnt                                     0
```

and, further up the same dump, the SoC's own side:

```
######## SPA ########
SPA2SMCT SOP CNT(8bit)      41      spa in cnt(32bit)      41
######## ISU ########
spa in cnt(32bit)           41      out to ppu unicast cnt  41
```

So on the vendor's board:

* the frames arrive **at chip port 2** (the jack the PC is in),
* **all of them are trapped** by the protocol action table (18 traps),
* they are delivered to **chip port 15**, the CPU port,
* and the SoC's SPA receives them.

Two more facts came out of the same window:

* `sw_mac_show_macaddress` prints the FDB: the PC's own address
  `84:47:09:5b:bd:2c` is **learned on port_id 0x2**.  The chip sees the frame.
* `sw_port_get_port_enable` for port 2 prints `enable = 1`.

The vendor's debug entries print to the kernel log, not back into the proc
file - a probe that reads the proc file sees nothing, which is what made the
first snapshot of this window look empty.

## 2. The PHY is not the difference

`staging/ctl/re-phy-vendor-vs-ours-20261023.md`: the vendor's whole PHY
bring-up for the four internal PHYs is **one register and one bit**, BMCR
(0x00) bit 11.  Our driver does the same thing (it clears `BMCR_PDOWN`), and
the extra `BMCR_ANENABLE` it sets is already set on this board, so our write
is a no-op.  Two side findings:

* the vendor reads the link from register **0x1a** (inside a
  `write(0x1e, 0)` / restore sequence), not from BMSR bit 2 - a difference in
  the read path, not in the write path;
* the `m`/`mw` debug command failed with "Invalid argument" because
  `kstrtou32(tok, 0, ...)` rejects the literal `0b`; the address has to be
  written `0x0b`.  The argument order was right all along.

## 3. The per-port table is now the vendor's, and it did not help

The vendor's live SPA table (`docs/18`) says:

| memory | word 0 (entry program) | word 19 (attribute) |
|---|---|---|
| smac_0..smac_3 | 0x0000a6a5 | 0x96040001 |
| xmac_0 (the conduit) | 0x0000a6a5 | 0x96040001 |
| trap (cpu) | 0x0000a767 | 0x00000000 |

Round 148 (`staging/ctl/exp148.txt`, run-138 kernel) put all of those rows
where the vendor has them - the trap port's row on memory 15, the xmac_0 row
on memory 5 - and did the vendor's `smac_init` for mac 1 (the jack the PC is
in) by hand.  The writes took (every word read back), SMAC1's ready word came
up, and:

```
lan2     RX: bytes 0 packets 0       TX: bytes 2902 packets 25
lan-cpu0 RX: bytes 0 packets 0
```

**No ingress.**  So the per-port configuration is not the difference either.

The same round produced the clearest picture of the SOPC array yet:

```
0x34294..0x342ac  = 0     (the four SMACs' and the two XMACs' ready words)
0x342b0..0x342bc  = 1     (the SMACs' send-enable words)
0x342c0..0x342cc  = 1     (the XMACs' send-enable words)
0x342d0..0x342dc  = 0
```

## 4. Round 149: the 9132's own ingress counters, and two interventions

The instrument is the vendor's own "receive cpu pkt cnt from DMA" counter and
the per-queue counters of the USCH, read out of the NPPT with the driver's
`regs` command:

```
NPPT + 0x280180  the vendor's "receive cpu pkt cnt from DMA"
NPPT + 0x28c4 + 4*queue  the vendor's per-queue packet counts
NPPT + 0x280240 + 4*queue  the IDM's RX descriptor write pointers
```

Three measurements, 25 s of PC traffic in each:

| step | 0x280180 | the USCH words | the IDM pointers | lan-cpu0 RX |
|---|---|---|---|---|
| before | 0 | all 0 | all 0 | 0 |
| after 25 s | 0 | all 0 | all 0 | 0 |
| after `trapall 2` | 0 | all 0 | all 0 | 0 |
| after `cqid 0x3 0x3` | 0 | all 0 | all 0 | 0 |

and the PC: `Packets: Sent = 4, Received = 0, Lost = 4 (100% loss)`, neighbour
state `Unreachable`.

Three things are therefore **falsified**:

1. **The protocol number.** Trapping every one of the 128 protocols on port 2
   (`trapall 2`) changes nothing, so the frame is not being trapped under the
   wrong protocol number.
2. **The queue id.** A queue id inside the RED's range (`cqid 0x3 0x3`) makes
   no difference, so the frame is not lost to an out-of-range queue id.  (The
   vendor's own value is 0x193 = 403 while the vendor's RED covers queues
   0..0x190, so that number was never a RED queue id to begin with.)
3. **The CPU path inside the 9132.** The vendor's own CPU-side counter does
   not move, so the frame never gets as far as the 9132's queue that feeds the
   CPU.

**The frame stops inside the 9132, before the queue.**

## 5. What is left: the RED's counters

`docs/54` already measured the RED on our board while the PC was pinging:

```
0x20204 (in fwd)      = 138
0x20210 (out fwd)     = 2
0x200b8 (pktlen0 drop)  = 136
0x200bc (pktlen flt drop) = 136
```

**138 frames were handed to the RED and 136 of them were dropped because the
length they were handed was zero.**  The vendor's board has the same two
filter words (0x20038 = 0x00000001, 0x2003c = 0x00000006) and a
`pktlen0 drop cnt` of zero, so the filter is not the difference: on the
vendor's board the length is real and on ours it is zero.

That is the shape of the bug.  A frame that arrives at a MAC which was never
reset is handed on without a length - which is what `zx279133-switch.c` says
about the reset pulse, and it is why run 138 added the pulse at the right
address (0x2c0004, not 0x2c004).  Round 138's pulse, however, has **no
delay**: the vendor's `smac_reset()` waits `__const_udelay(1718000)` - about
1.7 ms - between the two writes, and run 139/140 add exactly that.

Round 150 therefore measures the RED's counters before and after the PC's
traffic, and again after the vendor's own `smac_init` for mac 1 written by
hand, so that the 1.7 ms question is answered on the board without waiting
for a build.

## 6. The state of the gate

* G2 (the 9132 LAN data plane) is **not** passed.  The PC's frames reach the
  9132 and are dropped there with a length of zero; nothing has ever reached
  the SoC.
* Everything above the RED has been ruled out by measurement: the PHY, the
  per-port table, the protocol action table, the queue id, the trap, the
  CPU-side counters.
* The three false leads of the day, so they are not walked again: the flood
  group table (0x1f) was right all along, the ARP trap entry was right, and
  the "ready" word array belongs to the SMACs (0x34294 + 4*mac), not to the
  XMACs.

## 7. The files of this round

| file | what it holds |
|---|---|
| `staging/ctl/vendor-oracle.sh` | the vendor's statistics oracle, with the PC pinging |
| `staging/ctl/vendor-oracle-20261023.out` | its output, the vendor's ingress counters |
| `staging/ctl/vendor-nppt.sh`, `vendor-nppt2.sh` | the vendor's debug entries, and what they print |
| `staging/ctl/vendor-env.sh` | what the vendor's system answers on the wire |
| `staging/ctl/vendor-snap.sh`, `go-vendor-snap.sh` | the two snapshots, 40 s apart |
| `staging/ctl/exp148.txt` | the per-port rows, put back where the vendor has them |
| `staging/ctl/exp149.txt` | the ingress counters and the two interventions |
| `staging/ctl/exp150.txt` | the RED's counters around the vendor's `smac_init` |
| `staging/ctl/re-phy-vendor-vs-ours-20261023.md` | the PHY bring-up, vendor against ours |
