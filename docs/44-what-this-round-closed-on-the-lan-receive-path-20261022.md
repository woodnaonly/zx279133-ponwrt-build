# 44 - What this round closed on the LAN receive path (2026-10-22)

This is the G2 state after the measurements of 2026-10-22 morning. It
records the one change that went into the tree, the three root causes that
were falsified on the board, the architecture as it now stands, and the
board's own table contents read back with the debug console.

Everything here comes from a live board, from the vendor module
`np_133.ko`, or from the tree itself.

---

## 0. The short version

* The change that went in: **ERAM table 0x19 (the reason map) and ERAM
  table 0x27 (the outport map) are now written in full**, with the vendor's
  own values - 254 entries in 0x19 and all 64 in 0x27. Before this the
  driver wrote exactly one entry in each (0x19 index 0 -> queue 7, and 0x27
  index 15 -> real port 15 queue 403). Commit `2881fc6`, build run 122.
* **That was not the break.** The tables were poured into a live board with
  the debug console's `ew` command, all 64 and all 254 of them, and the
  IDM's receive descriptors stayed empty.
* The SPA window differences were poured in as well (all thirteen of them,
  including the trap eth-type words the driver had zeroed) - and the IDM's
  receive descriptors stayed empty again.
* What is now established: **the frames do not reach the SoC at all.**
  Not the IDM's per-queue counters, not the IDM's global receive counter,
  not the USCH's per-queue counters, not a single interface's receive
  counter. The board's own bridge only ever receives from the veth pair it
  creates for itself.
* The architecture is settled, and it is the **IDM**, not the SerDes MAC:
  `lan-cpu0` is the IDM driver's netdev (it carries the same MAC address as
  `eth0` and `br-lan`), the IDM driver already has the LAN queues (0..7)
  and the LAN L2 restore in it, and the DSA tagger then hands each frame to
  the right jack by the transport VID.

---

## 1. The change that went into the tree

`zx279133_9132_cpu_path_init()` now calls two new writers instead of the
two single-entry writes it used to do:

* `zx279133_9132_reason_map_init()` writes ERAM table 0x19 for reasons 0
  and 1. The index is `cpu_qid + (reason << 7)`, the entry is 32 bits and
  the queue id is in bits 24..28. The vendor programs 127 cpu_qids per
  reason and never writes cpu_qid 50; the queue id of reason 1 is always the
  queue id of reason 0 plus eight. The table itself is
  `zx279133_9132_reason_qid[128]`, taken from
  `staging/ctl/re-idm-queue-map.md`.
* `zx279133_9132_outport_map_init()` writes ERAM table 0x27. The index is
  the outport (0..63) and the entry is 16 bits: the real port in bits 0..5
  and the queue id in bits 6..14. The vendor writes all 64, with real port
  15 everywhere, queue id 0x1a3 (419) by default, and four exceptions:
  outport 15 -> 0x193 (403), 49 -> 0x1b1 (433), 50 -> 0x1b3 (435), 51 ->
  0x1b5 (437).

Both go through `zx279133_smmu0_write()`, the same path the debug
console's `ew` uses, so a table written by the driver reads back with `er`
and a table written by hand with `ew` reads back with `er`.

## 2. Falsified on the board

| experiment | what it changed | result |
| --- | --- | --- |
| exp49 | poured the vendor's whole ERAM 0x19 (64 blocks) and 0x27 (8 blocks) into a live board with `ew`, then read them back with `er` | every block read back exactly as written; the IDM's RX write pointers (`IDM+0x240+4q`) and per-queue counters stayed 0 |
| exp54 | wrote the vendor's thirteen differing SPA words back (0x8000, the TPID table, 0x82c0, and the three trap eth-type words at 0x82e4/8/c as 0xffffffff) and read them back | every word read back exactly as written; the IDM stayed empty, `br-lan` still received nothing |
| exp56 | read the board's own ERAM tables back | the trap table and the CPU base queue id are right; the reason map and the outport map are the two one-entry tables described above - exactly what run 122 replaces |
| exp57 | read the USCH per-queue counters (`nppt + 0x28c4 + 4q`) and the IDM global receive counter (`nppt + 0x28180`) | all zero, like everything else |

So neither the ERAM tables nor the SPA window is the break.

### 2.1 The interface counters, and what they do mean

On the trial kernel with the PC pinging the board:

```
  eth0:       0       0    0    0    0     0          0         0     1046       9 ...
lan-cpu0:     0       0    0    0    0     0          0         0     2490      21 ...
  lan2:       0       0    0    0    0     0          0         0     1254       9 ...
br-lan:    2264      26    0    0    0     0          0        10     3554      29 ...
```

* `br-lan` does receive frames, and they are **all** from `labhost0`, the
  veth pair the trial image creates for itself: `labhost0` and `br-lan`
  move the same number of packets. Not one of them came off a jack.
* `lan2` has a link (1 Gbps, full duplex) and its transmit counter moves,
  but it never receives: a DSA user port's receive counter does not count
  the frames that arrive on it and are handed to the CPU port, so this
  counter says nothing either way.
* `lan-cpu0` is the one that matters, and it is zero. Nothing reached the
  SoC.
* On the PC side the Ethernet adapter is up at 1 Gbps with 192.168.9.25/24
  and has **no** ARP entry for 192.168.9.1, so the frames are going out
  and are not coming back. The physical link is fine; the switch is not
  delivering.

## 3. The architecture, settled

```
PC -> jack -> 9132 internal PHY (SMAC0..3) -> switch engine
     -> ERAM 0x18: (port, protocol) -> action 2 = "to the CPU"
     -> outport 15 -> ERAM 0x27: outport 15 -> real port 15, queue id 0x193
     -> ERAM 0x22: the CPU base queue id is 0x193
     -> the SoC's conduit: the IDM (NPPT + 0x280000)
     -> the IDM's RX descriptor ring in DDR
     -> the IDM driver's NAPI, LAN queues 0..7
     -> the DSA tagger (transport VID) -> lan1..lan4 and br-lan
```

* `lan-cpu0` is **not** a SerDes MAC. It is the IDM driver's netdev: it
  carries the same MAC address as `eth0` and `br-lan`
  (`7a:e5:4b:49:0a:82` in this round), and the IDM driver already contains
  the LAN-side code (`ZX279133_LAN_RX_QUEUE_COUNT` is 8,
  `zx279133_idm_rx_restore_lan_l2()`, `zx279133_idm_rx_lan_vport()`).
* The vendor's `eth1` is a virtual netdev
  (`/sys/devices/virtual/net/eth1/device`), and the vendor's `sw` is the
  IDM netdev that `np_133.ko`'s `idm_net_init()` creates. The vendor's
  `pon` and `sw` are the two ends of the same IDM path, one for the PON
  and one for the 9132.
* The vendor never programs XMAC0 on this board (its six control words at
  `0x19140000` read as zero, and its own boot log says "mac 4 link down"),
  so XMAC0 is not a road to the LAN. Our own XMAC0 programming is
  equally dead: `MODE_CFG` (`nppt + 0x140280`) reads back 0 after a write
  of 2, the XMAC link word (`nppt + 0x0084`) is 0, and the XMAC0 receive
  statistics at `nppt + 0x142400` are all zero. The PCS under it is fine -
  `XPCS0 status=0x380b`, link up - and the SoC-to-9132 direction works,
  but the MAC layer above it is not running.

## 4. The 9132's own tables, read back from the board

Read with the debug console's `er` command; the values are the ones the
driver writes, and they are the vendor's.

```
ERAM 0x18 (the protocol action table), blocks 19624..19631
  19626 = 0aaa000a aaaa8aaa 08aaaaaa 000002a2
  19627 = 00a20000 0002aa2a 0000002a 00000000
  19628..19631 identical to 19626..19627 - the same table on ports 1..7
ERAM 0x22 (the CPU base queue id), block 19592
  word 3 = 0x03272000        (0x193 << 17 | bit 16 | bit 13)
ERAM 0x27 (the outport map), blocks 24071..24078
  24071 = 00000000 00000000 00000000 00000000     (nothing written yet)
  24072 = 00000000 00000000 00000000 64cf0000     (outport 15 only)
ERAM 0x19 (the reason map), blocks 19752..19815
  19752 = 07000000 00000000 00000000 00000000     (index 0 only)
  19784 = 00000000 00000000 00000000 00000000     (reason 1: nothing)
```

The trap table is the one that has to be right for a frame from a jack to
reach the CPU at all, and it is right: entries 0, 1, 2, 4, 5, 6, 14 and 15
of port 1 read back as action 2 ("to the CPU"), the rest as 0 or 1. Run 122
fills in the two tables above that are still one-entry tables.

### 4.1 The 9132 is a transparent bridge

Each PPU's words, read back:

```
PPU 0 word 0 (entry-pc) = 00000463      <- PPU 0 is the PON and is special
PPU 1..5 word 0         = 00008015
PPU * word 1 (pvid-pri) = 0000088f
PPU * word 2 (pvid-vid) = 00000000      <- no untagged SVID
PPU * word 9 (vlan-filter) = 00000000   <- no VLAN filter
PPU * word 19 (attr)    = 00000000
```

The untagged SVID is 0 and the VLAN filter is 0 on every port, and that is
deliberate (`zx279133_9132_vendor_words[]`): the 9132 is a MAC-learning
bridge, not a VLAN switch. The transport VID never appears on the wire
between the 9132 and the SoC; the IDM driver and the DSA tagger handle it
in software.

(The entry program we write, 0x0000a6a5, reads back as 0x00008015 - worth
a separate look, but it is not what stops the frames.)

## 5. The console's line-length limit is not 40 characters

Earlier rounds assumed a console line could not be longer than about 40
characters, which ruled out the debug console's `ew` writes (54 characters
of payload, 66 with the `echo "..." > /d` around them).

That is wrong. A 66-character line was measured end to end on 2026-10-22:
the board echoed the whole command and the `ew` command read its own
write back correctly.

```
=== >>> 07:49:44 sent: echo "ew 19760 0x06000000 0x06000000 0x06000000 0x06000000" > /d ===
[   53.553713] eram block 19760 = 06000000 06000000 06000000 06000000
```

All 72 writes of the vendor's ERAM tables went in on one line each and
every one of them read back correctly. `staging/ctl/multi3.sh` and
`staging/ctl/runexp3.sh` now send a Ctrl-C before every line long enough
to be at risk, so a line that *does* get cut cannot swallow the ones after
it.

## 6. Still open

1. **Where the frame stops.** The IDM's receive descriptor write pointers
   (`IDM+0x240+4q`), the IDM's global receive counter (`IDM+0x180`), the
   IDM's per-queue counters (`IDM+0xc4+4q`) and the USCH's per-queue
   counters (`nppt+0x28c4+4q`) are all zero, so the frame never reaches
   the SoC's schedulers. The remaining question is whether the 9132
   delivers the trapped frame to the CPU port at all.
2. **The queue id.** The outport map hands the CPU a queue id of 0x193
   (403). The tree's RED block is configured for 401 queues
   (`ZX279133_RED_QUEUE_COUNT`, used by `zx279133_red_buffer_prepare()` in
   `staging/upstream/zx279133-tm.c`), and the vendor has explicit
   "illegal qid" handling (`red_set_inqid_err_en_cfg`,
   `red_set_illegal_qid_drop_cfg`). A queue id outside the configured
   range is dropped there, with no counter moving anywhere - which fits
   what the board does.
3. **The flood groups.** The four UNI ports' flood groups point at PPU 5
   (the CPU port) instead of the vendor's PPU 0 (the PON), so that a frame
   addressed to the board before the 9132 has learned its address is
   delivered rather than dropped. The vendor does not need this because
   its CPU port is fed by the protocol trap. Whether the flood path and
   the trap path are both needed on this board is not yet established.
