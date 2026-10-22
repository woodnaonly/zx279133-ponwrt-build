# The MAC the trial kernel owns, the counters that were read wrong, and the
# vendor's SOPC publish step

Date: 2026-10-23. Board: Skyworth SK-D840N (ZX279133 + 9132). Kernel: the
mainline 6.18.38 trial image, run 127 (`bbd0c00`).

Everything here was measured on the board or read out of this board's own
vendor module (`staging/vendor-full/kmodule/np_133.ko`). Nothing outside the
repository was touched, and the board was only read and written through the
drivers' own debugfs.

---

## 1. The static ARP entry was pointing at a stranger

### 1.1 What the board says about its own address

The trial image's kernel has no MAC source for this board: the DTS gives the
NPPT node no `nvmem-cells` and no `local-mac-address`, so
`zx279133_main()` falls back to `eth_hw_addr_random()`:

```c
	ret = of_get_ethdev_address(dev->of_node, ndev);
	if (ret == -EPROBE_DEFER ||
	    (ret && of_property_present(dev->of_node, "nvmem-cells")))
		return dev_err_probe(dev, ret, "failed to read factory MAC address\n");
	if (ret)
		eth_hw_addr_random(ndev);
```

`eth_hw_addr_random()` is seeded per boot, so **the address changes on every
boot**. Three consecutive boots of the same image, all read from the board
itself:

| boot | `br-lan` / `eth0` / `lan-cpu0` / `lan1..lan4` |
|------|-----------------------------------------------|
| 23:57 | `8e:b6:59:47:42:39` |
| 00:03 | `46:72:3a:4e:9a:f4` |
| 23:57 (previous image) | `d0:45:8d:84:08:ff` |

The vendor's 4.19 system, by contrast, uses one address for all of its LAN
ports: `3e:8e:98:91:64:2a` (from the vendor system's own `ip addr` on the
console).

### 1.2 Why that broke every round

The test PC sits on a LAN jack of the board and reaches it through the
router's own IP, 192.168.9.1. The board's replies cannot leave (that is the
thing under test), so the PC keeps a **static** ARP entry for the board:

```
arp -s 192.168.9.1 3e-8e-98-91-64-2a
```

That address is the *vendor* firmware's. The trial kernel does not own it.
Every unicast frame the PC sent was therefore addressed to a MAC nobody on the
board answers to: the DSA bridge received it, found no FDB entry for it, and
flooded it back out to the LAN ports.

The measurable consequence, from the board's own counters during a ping:

```
MAC eth0 8e:b6:59:47:42:39      RX eth0 0 0
MAC lan-cpu0 8e:b6:59:47:42:39  RX lan-cpu0 0 0
MAC br-lan 8e:b6:59:47:42:39    RX br-lan 0 0
MAC lan1 8e:b6:59:47:42:39      RX lan1 0 0
MAC lan2 8e:b6:59:47:42:39      RX lan2 0 0
MAC lan4 8e:b6:59:47:42:39      RX lan4 0 0
```

**Every receive counter is zero, in every round ever run.** With the entry
pointed at the address the kernel actually owns, the same board answers the
ping. So the "the frames reach the chip but never come out" story that drove
the last several rounds was, at least for the unicast direction, a property of
the *test harness*, not of the driver.

`staging/ctl/macwatch.py` is the fix for the lab: it reads the newest
`MAC br-lan xx:xx` line out of the console log, installs it as the static ARP
entry, and keeps the ping going. `staging/ctl/g2round.sh` runs a whole round
with it.

The image itself still needs a stable address (a `local-mac-address` in the
DTS, or the vendor's "parameter tags" nvmem layout). That is a separate item
and it belongs to G4, not to the data plane.

---

## 2. The RED counters, decoded from the vendor module

The names in `red_total_stats()` (np_133.ko `.text+0x65930`) are the only
authority for what the words mean. It reads, in order:

| NPPT word | NPPT byte | name |
|---|---|---|
| 0x8081 | 0x20204 | in fwd cnt |
| 0x8082 | 0x20208 | out fwd cnt |
| 0x8084 | 0x20210 | (the out-side pair) |
| 0x8085 | 0x20214 | (the out-side pair) |

`red_get_stats()` (`.text+0x66070`) reads the same two words, and
`red_discard_stats()` (`.text+0x65a04`) reads 0x802e and 0x802f, whose fields
are named in the strings themselves:

| NPPT byte | bits | name |
|---|---|---|
| 0x200b8 | 31..24 | woe pkt len err cnt |
| 0x200b8 | 23..16 | **pktlen0 drop cnt** |
| 0x200b8 | 15..0 | in qid err cnt |
| 0x200bc | 31..24 | **pktlen flt drop cnt** |
| 0x200bc | 23..16 | up link drop cnt |
| 0x200bc | 15..8 | omci drop cnt |
| 0x200bc | 7..0 | woe band drop cnt |

### 2.1 The vendor's live values

From `staging/ctl/vendor-serial-mods/tm-red.txt` (the vendor's own dump of
this board, taken while its own system was running):

```
nppt +0x020204 (in fwd)  = 2925
nppt +0x020208 (out fwd) = 7
nppt +0x020210           = 2925
nppt +0x020214           = 7
nppt +0x02021c           = 2932
nppt +0x020b0           = 0x00040f00
nppt +0x020b4           = 0x00000000
nppt +0x020b8           = 0x00000000
nppt +0x020bc           = 0x00000000
```

### 2.2 What the mainline board shows, with the right MAC

```
nppt +0x020204 (in fwd)  = 0
nppt +0x020208 (out fwd) = 49      <- grows with every ping
nppt +0x020210           = 0
nppt +0x020214           = 49      <- grows in step
nppt +0x020b0           = 0x00200e00
nppt +0x020b4           = 0x00060000
nppt +0x020b8           = 0x00000000
nppt +0x020bc           = 0x00000000
```

Two things follow, and the second one contradicts what the earlier rounds
believed:

1. The frames do reach the RED and are dropped there: the drop counter moves
   with the traffic and the forward counter stays at zero.
2. **They are not dropped by the length filters.** `pktlen0 drop cnt`
   (0x200b8 bits 23..16) and `pktlen flt drop cnt` (0x200bc bits 31..24) are
   both **zero**. The "the descriptor carries pkt_len == 0" conclusion in
   `docs/46` was read off counters that are not the ones that say that.

What is left as the difference is `red_monitor()`'s two words, 0x200b0 and
0x200b4: the vendor's board holds 0x00040f00 / 0x00000000, ours holds
0x00200e00 / 0x00060000. `red_monitor()` prints them as
`woe_ring_in_out_band_err(r5-r0)`, `qmg2red_watermark_woe`,
`qmg2red_aful_woe`, `sch2red_desc_error`, `red_used_out_gua_space`,
`red_used_in_gua_space`, and more - the word is a bundle of sub-counters, and
the bit layout is still to be split.

---

## 3. The vendor's SOPC publish step, in full

`nppt_smac_init()` (np_133.ko `.text+0xda1c`) walks mac 0..3. For each one,
whose block is at NPPT + 0x40000 * (mac + 1):

```
nppt_smac_set_uni_mode(mac, 0)
an_ctrl  (SMAC+0x0000) = 0x00ba2203
smac_reset(1 << mac)                 ; the shared soft-reset pulse
an_ctrl  (SMAC+0x0000) = 0x00fa2203
an_ctrl2 (SMAC+0x0004) = 0x3fff
pkt_filter (SMAC+0x0008) = 0x80000000
port_ctrl (SMAC+0x0b00) |= 0x200
speed    (SMAC+0x00e0) = 0x00011200
SOPC+0x3f0: set this MAC's bit (an_ctrl bit 13 clear) or clear it
<publish>                              ; see below
an_ctrl |= 2
```

The publish step is the static function at `.text+0xcf8c`, and it is the one
that matters for a LAN frame:

```
w20 = 0x34000 + 4 * (mac + 0xa5)      ; the ready word
     = NPPT + 0x34294 + 4 * mac
loop up to 10 times:
        v = readl(nppt_base + w20)    ; "SOPC_CLR_OVER_READY_SMAC"
        printk("read SOPC_CLR_OVER_READY_SMAC = 0x%x, mac = %d", v, mac)
        if (v & 1) break
        __const_udelay(0x418958)      ; about 4.3 seconds between tries
w20 = 0x34000 + 4 * (mac + 0xac)      ; the send-enable word
     = NPPT + 0x342b0 + 4 * mac
writel(nppt_base + w20, 1)
printk("write SOPC_SEND_EN_CFG_SMAC, mac = %d", mac)
```

Two things follow:

* the two offsets this driver already uses (`ZX279133_SMAC_SOPC_READY_BASE`
  0x34294 and `ZX279133_SMAC_SOPC_SEND_BASE` 0x342b0) are **right**;
* the vendor waits **up to about 43 seconds** for the ready bit, while this
  driver waits 10 milliseconds. The ready word reads 0 on the vendor's own
  board in steady state (`tm-sopc.txt`: 0x34294..0x342a8 are all zero), so
  the warning this driver prints is not, by itself, a fault - but the wait is
  far shorter than the vendor's and it happens right after the PHY is powered
  up, which is exactly when the PHY is still negotiating.

---

## 4. A hazard in the debugfs console

`echo "regs <off> <n>" > /sys/kernel/debug/zx279133-9132` answers for most
offsets, but these return nothing at all - no register line, no error on the
console, and the shell carries on:

```
0x20188  0x280180  0x28018c  0x280190  0x40700
```

while these work:

```
0x200b0  0x200b4  0x200b8  0x200bc  0x20204..0x20220  0x40780..0x407fc
```

The offsets that fail are exactly the ones a G2 verdict wants: the IDM's
receive counters live at 0x280180..0x280190, and the NP1 counter word at
0x20188. The reads are issued inside the kernel, so the failure is not in the
console, and the shell answered `ALIVE-42` right after one of them, so the
board does not hang either. Until that is understood, an experiment cannot
read the IDM's receive state through this console.

---

## 5. What this changes for the next round

1. Every previous round's traffic was unicast to the wrong address. **Any
   conclusion drawn from a ping in this project has to be re-measured with
   `macwatch.py` in the loop**, including the ones that looked negative.
2. The RED is dropping the frames, and the length filters are not the reason.
   The next candidates are the RED's own state (the queue RAM geometry, which
   is still the tree's and not this board's) and the SMAC's own publish step.
3. The SMAC's ready wait is ten milliseconds where the vendor's is forty
   seconds, and the vendor's SMAC init also writes SOPC+0x3f0, which this
   driver does not.
