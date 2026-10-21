# 43 - Where the LAN receive path actually stops (2026-10-22)

This is the state of the G2 gate (9132 LAN data plane) after the
measurements of 2026-10-21/22. It records what is now established, what has
been falsified on the board, and what is still open. Every number here comes
either from the vendor module `pon/vendor-modules/np_133.ko` (md5
`92B68212542930114769E3C69EFE64B6`) or from a reading of a live board.

---

## 0. The short version

* The topology is the same as the vendor's: the four RJ45 jacks are the 9132's
  internal PHYs, the 9132's conduit to the SoC is the **IDM** (NPPT +
  0x280000), and the SoC's own GMAC (`eth0`, RGMII to an RTL8226 on jack 4)
  has nothing to do with the LAN data path.
* The vendor's firmware receives frames on that conduit. Our driver does not:
  the IDM's own global receive counter (`IDM+0x180`, the vendor's string
  "receive cpu pkt cnt from DMA") stays at 0 on our board and moves on the
  vendor's.
* The frame therefore never reaches the SoC. It stops inside the 9132 or in
  the SoC's conduit front end, **before** the IDM.
* Four plausible root causes have been tested and falsified on the board
  (SMAC bring-up, IDM control word / depth / BP-ring encoding, CPU-port
  word 0, SIPC RX enable).
* The most specific lead left is a **queue-id** problem: the 9132 hands a
  frame to the conduit with a qid, and the SoC's schedulers decide what to do
  with that qid. Our driver copies the vendor's numbers (base qid 0x193,
  reason 0 -> qid 7) but nothing on our side shows that a frame with that qid
  is ever accepted.

---

## 1. The vendor's receive path, from the module

### 1.1 The frame's journey

```
LAN jack -> 9132 internal PHY -> SMAC0..3 -> switch engine (SE)
          -> ERAM 0x18 (port, reason) -> action 2 = "to the CPU"
          -> outport 15 (the CPU trap port)
          -> ERAM 0x27: outport 15 -> real port 15, queue id 0x193
          -> ERAM 0x22: the CPU base queue id is 0x193
          -> the SoC's conduit (the IDM)
          -> IDM RX descriptor ring in DDR
          -> the vendor's per-CPU NAPI worker
          -> switch_skb_recv() (a .bss callback slot, .bss+0x7798)
          -> netdriver.ko's pdt_ethdrv_recv() -> the "sw" netdev -> br-lan
```

Evidence: `np_133.ko`'s `np_user_table_init()` (0x117930) calls
`port_attr_def_init`, `glb_cfg_table_cpu_base_qid_set` (0xbdc5c),
`outport_and_real_port_map_table_init` (0xc06c8); the ERAM table ids and
their writers are listed in `staging/ctl/re-idm-bringup.md` and in the
vendor-IDM reverse-engineering report in `staging/ctl/`.

### 1.2 Where the vendor's CPU reads the frame count from

The vendor's NAPI worker is an unnamed function at `.text+0x15c64`
(`idm_rx_test` has a bogus size that ends exactly there). Its per-queue
"packets available" read is:

```
0001634c  mov  x0, #0xe0
00016350  movk x0, #0x28, lsl #16      ; x0 = 0x28e0
00016354  str  x0, [x29, #0x98]
00016398  adrp x1, nppt_base
0001639c  ldr  x2, [x29, #0x98]
000163a4  ldr  x0, [x1]
000163a8  ldr  w23, [x0, x2]           ; <-- the poll read
000163ac  and  w2, w23, #0xffff        ; low 16 bits = packet count
```

so the per-queue counter word is **`nppt_base + 0x28c4 + 4*queue`** (queue 7
-> 0x28e0, queue 11 -> 0x28f0). `nppt_base` is the NPPT window at
0x19000000, so the addresses are 0x190028c4..0x19002924. The NPPT block that
contains them is the **USCH** (unicast scheduler), whose configuration
register our own header puts at NPPT + 0x28000
(`ZX279133_USCH_CFG`), with SSCH at 0x2c000, WSCH at 0x2c400, WOE_SCH at
0x2c800 and UOPC at 0x30000.

The IDM's own descriptor-ring write pointer is at **`IDM+0x240+4*queue`**
(`dump_rx_ring_manage_info`, 0xeac4), and the IDM's global receive counter
is at **`IDM+0x180`**.

### 1.3 The vendor's SoC-side init

`tm_initial()` (0x119f04) is the vendor's bring-up order:

```
spic_set_rx_en(0)
smac_get_cfg / smac_set_cfg for mac 0..3
spa_ind_lock_init()
np_tm_init()            0x1178a8
se_init()               0xa2c88
np_ppu_init()           0x117910
tm_acl_init()           0x10e110
np_user_table_init()    0x117930
np_nppu_init()          0x119ec4
nppt_glb_pon_ptl_en_cfg(0x3e)
tm_rate_limit_init()    0x117a3c
sipc_init()             0x1f290
```

and `np_tm_init()` is

```
dma_init()            0x7458c
tm_bmu_init()         0x737ac
tm_qmg_init()         0x117318
sopc_init()           0x75a98
red_init()            0x116f20
tm_ssch_initial()     0x116aec
tm_wsch_initial()     0x116bb8
tm_woe_sch_initial()  0x116c34
tm_usch_initial()     0x116a10
tm_uopc_initial()     0x116ce0
```

Upstream's `zx279133_tm_prepare()`
(`staging/upstream/zx279133-tm.c`) covers the same list, and the PPU
microcode is loaded by our driver as well: the boot log says
`PPU microcode version 0x10001.0x1`, so `zte/zx279133/mcode_intel.bin` is
present in the image and the PPU has its program.

---

## 2. The live comparison: vendor (working) vs ours (broken)

All four columns are the same register, read the same way. "vendor" is the
vendor's own firmware with the PC pinging the board; "ours" is the trial
kernel with the same PC on the same cable.

| register | meaning | vendor | ours |
| --- | --- | --- | --- |
| `IDM+0x180` | "receive cpu pkt cnt from DMA" | 0x1465 -> 0x15b3 (moves) | 0x00000000 |
| `IDM+0x184` | next stat word | moves | 0x00000000 |
| `IDM+0x188` | next stat word | 0x146600000000 -> 0x15b500000000 | 0x00000000 |
| `IDM+0x240` | RX descriptor write pointer, queue 0 | 0xc000000016 -> 0xd300000016 | 0x00000000 |
| `IDM+0x258` | RX descriptor write pointer, queue 6 | 0x2c1 -> 0x3fe | 0x00000000 |
| `IDM+0xc4..0xf0` | what **our** driver polls | does not move | 0x00000000 |
| `IDM+0x180` | global RX counter | moves | 0 |

**The decisive observation**: on the *working* vendor board the registers our
driver polls (`IDM+0xc4..0xf0`) do not move either. Our driver's
`zx279133_idm_rx_count()` is upstream's own code (unchanged by our overlay)
and reads `idm + 4*((queue >> 1) + 49)`, i.e. `IDM+0xc4`. The vendor polls
the USCH's per-queue words at `nppt+0x28c4+4*queue` instead. Two different
counters in two different blocks.

On our board **all** of them read 0, so the frame does not even reach the
SoC's schedulers.

### 2.1 The link itself is up

PHY 0x0b (the jack the PC is on, MDIO address 0x0b) on our board:

| register | value | note |
| --- | --- | --- |
| 0x00 (BMCR) | 0x1140 | auto-negotiation enabled, 1000M, not powered down |
| 0x01 (BMSR) | 0x796d | link up, auto-negotiation complete |
| 0x15 | 0x000c | (the other three PHYs read 0x000e) |
| 0x16 | 0x8f08 | identical on all four PHYs |
| 0x17 | 0x3048 | |
| 0x1c / 0x1d | 0x210a / 0x1855 | |

The other three PHYs read 0x7949 in register 0x01 (link down), which is
correct: nothing is plugged into them. A full 32-register double snapshot of
PHY 0x0b with the PC pinging changed exactly one register (0x06, 0x006f ->
0x006d), so the PHY does not expose a receive counter we can watch.

### 2.2 The DSA port counters prove nothing

`lan1..lan4` show `rx 0` on our board, but our DSA driver has no
`get_stats64`, so those counters are always zero and say nothing about what
the 9132 received.

### 2.3 The PC's frames are not addressed to our board

The vendor firmware's MAC is `d0:45:8d:84:09:00`; the trial kernel's is
`6e:e1:1a:a1:cc:9b` (it changes with the boot). A PC that has learned the
vendor's MAC sends *unicast* frames the 9132 has no entry for, so they are
flooded - and our flood groups point at PPU 5, not at the vendor's PPU 0.
For a clean test the PC's ARP entry has to be dropped so that it broadcasts.

---

## 3. Falsified on the board

| experiment | what it changed | result |
| --- | --- | --- |
| exp25 | the vendor's SMAC bring-up for SMAC0..2 (MAC CFG 0xfa2203, +0x04 0x3fff, pkt filter +0x08 0x80000000, AN ctrl +0xe0 0x11200, publish 0x342b0+4*mac) | RX still 0 |
| exp26 | the CPU port's word 0 | RX still 0 |
| exp27 | the vendor's IDM register image (control word 0x01ff1007) | RX still 0 |
| exp31 | IDM control word bit 24, `+0x070 = depth-0x18`, `+0x10c/+0x110` BP-ring encoding 0x00203D00/0x00003D00 | RX still 0 |
| exp32 | an XMAC0 reset pulse (NPPT+0x2c0004 bit 10) and a re-program of the six XMAC0 words | `MODE_CFG` (0x140280) still reads 0 after a write of 2 |
| upstream `zx279133-tm.c` | SIPC: 0x4000 bits 2:0 = 0b011, 0x4010 = 0x1042, which is byte-identical to the vendor's `sipc_init()` end state | already in place, not the gap |

Also cleared:

* **SIPC** is not the gap. The vendor's `sipc_init()` does four writes
  (`spic_set_rxsop_fifo_gap_cfg(0x1042)` -> 0x4010,
  `spic_set_spa_13clk_en(1)` -> 0x4000 bit 1, `sipc_set_sch_mode_sw(0)` ->
  0x4000 bit 2, `spic_set_rx_en(1)` -> 0x4000 bit 0); upstream's
  `zx279133-tm.c` already leaves 0x4000 = 0b011 and 0x4010 = 0x1042.
* **The PPU program is loaded.** `PPU microcode version 0x10001.0x1` is in the
  boot log, so `mcode_intel.bin` (the same 103456-byte file the vendor keeps
  in `/etc/mcode/mcode_133.bin`) is present and installed.
* **XMAC0 is a red herring.** On the vendor's board XMAC0's link bit
  (NPPT+0x0084 bit 0) is 0 as well - "mac 4 link down" in the vendor's own
  boot log - and the vendor never programs XMAC0 on this board at all.

---

## 4. Still open

1. **The qid.** The 9132 gives a frame to the conduit with a queue id: the
   outport map (ERAM 0x27) says 0x193 and the reason map (ERAM 0x19) says 7
   for reason 0. The vendor's RED block is configured for 401 queues by
   upstream's driver, and 0x193 is 403 - outside that range. The vendor has
   explicit "illegal qid" handling (`red_set_inqid_err_en_cfg` 0x62bb8,
   `red_set_illegal_qid_drop_cfg`), so a qid the SoC does not accept is
   dropped exactly here, with no counter moving anywhere.
2. **The USCH and the other schedulers.** The vendor's `tm_usch_initial()`
   writes eight USCH registers (dwrr enable, secondary-scheduler dwrr,
   queue/tcont/pq sharp enables, queue age enable, spend byte 0x14, hardware
   age time 0xee6b2800) through `np_onu_reg_write` (module id 5/6); upstream's
   `zx279133_usch_prepare()` writes two (the configuration mask and the
   ageing time). Whether the two are equivalent is not yet established -
   the vendor reaches its registers through a per-module descriptor table in
   `.data` that has not been decoded yet.
3. **The flood destination.** Our driver deliberately points the four UNI
   ports' flood groups at PPU 5 (`xmac_0`) instead of the vendor's PPU 0, on
   the assumption that PPU 0 is the WAN/PON port and PPU 5 is the conduit.
   If PPU 0 is in fact the SoC-side conduit, every flooded frame - and every
   unicast frame the 9132 has not learned - is being sent to a dead SerDes
   MAC. This is a one-line change and is the cheapest thing left to test.
4. **The reason map.** Only reason 0 (the vendor's OMCI) is mapped to qid 7.
   A trapped ARP frame's reason is not known, so its qid is whatever the
   table happens to hold.

---

## 5. How to reproduce the measurements

```sh
# the vendor's side (SSH into 192.168.9.1 with the vendor firmware)
sh staging/ctl/vs13.sh > staging/ctl/vs13.out 2>&1

# our side: boot the trial kernel and run an experiment file
sh staging/ctl/runexp2.sh staging/ctl/exp38.txt staging/ctl/exp38.out

# single NPPT read/write on the trial kernel
echo "reg 0x280180"  > /sys/kernel/debug/zx279133-9132   # read
echo "regw 0x280180 1" > /sys/kernel/debug/zx279133-9132 # write and read back
```

`reg`/`regw` take NPPT-relative offsets, `regw` writes one word and reads it
back; both print `soc reg 0x%08x (nppt +0x%06x) = 0x%08x` into the kernel
log.

---

## 6. Reports this builds on

| file | what it holds |
| --- | --- |
| `staging/ctl/re-xmac0.md` | the vendor's SMAC/XMAC bring-up, and why XMAC0 is not the LAN path |
| `staging/ctl/re-idm-bringup.md` | the IDM bring-up, register by register |
| `staging/ctl/re-spa-attrs.md` | the SPA per-port attributes and the flood groups |
| `staging/ctl/re-fdb-api.md` | the vendor's FDB and per-port statistics API |
| `staging/ctl/re-conduit-qid.md` | the qid space and the conduit's scheduler registers |
| `docs/15-switchko-init-diff-20261020.md` | the switch.ko init difference (G1) |
| `docs/42-the-vendor-truth-and-the-regression-20261022.md` | the vendor truth table used by the driver |
