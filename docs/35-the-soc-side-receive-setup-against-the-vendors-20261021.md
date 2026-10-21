# 35 - the SoC-side receive setup, against the vendor


*This is the offline reverse-engineering report behind docs/34.  It was written
against the driver as it stood before run 117; the copy in
staging/ctl/ is the original.*
Offline reverse-engineering comparison. **No board, no serial console, no writes to
`ci-build-repo`.** Everything below is derived from reading four things:

| input | what it is |
| --- | --- |
| `pon/vendor-modules/np_133.ko` (md5 `92B68212542930114769E3C69EFE64B6`) | the vendor module that runs on the stock board. **The only** module in the stock rootfs that touches the IDM register block (verified: `netdriver.ko` and `woe.ko` both contain **0** IDM-base markers under `staging/idmgap.py`). The `staging/vendor-full/kmodule/` copy is byte-identical, so there is no ambiguity about the reference. |
| `ci-build-repo/kernel-overlays/drivers/net/ethernet/zte/zx279133-{idm,np,switch,mac,netdev}.c` and `zx279133.h` | the driver under review (canonical repo; `ci-wanport` ignored). |
| `docs/09-idm-proven-image-20261020.md` | the **traffic-proven** U-Boot IDM image (a boot in which `ping 192.168.9.25` answered), i.e. the values the working stock system actually held. |
| `staging/swspa.py` + a local `vscan.py` (see §7) | the disassembly helpers. |

**Caveat that colours everything below:** `staging/idmgap.py` has a real bug — its
store-offset regex is `\[(%s)(?:, #(-?0x[0-9a-f]+))?\]` and therefore misses every
Capstone offset printed in **decimal** (`str w0, [x3, #8]`). The script's own
"CONTROL negative idm+0x004/+0x008 absent" is a false negative for the same reason:
`idm_rx_desc_init` *does* store the RX descriptor base at `idm+0x008`
(`00018d48  str w0, [x3, #8]`), so that row is **not** a discrepancy. Every
conclusion below was re-derived by hand from the disassembly, not from that script's
verdicts.

---

## 1. Executive summary

**No — the driver's SoC-side receive setup is not a faithful copy of the vendor's, and
the reason is structural rather than accidental.**

The driver's whole IDM register image was reconstructed from a *different* vendor
module. `zx279133-idm.c:1389-1390` says so in its own words:

```
 * Exact CPU133 vendor-Linux idm_init() register image, recovered from
 * plat_132.ko disassembly and kernel-2b5.elf data:
```

`plat_132.ko` is **not in this repository**, so the image could not be checked against
the module that actually runs here. Comparing the driver's image against
`np_133.ko`'s own `idm_init()` — the only IDM bring-up on this board — shows the two
disagree on at least seven register words, three of them on the RX-critical
buffer-pool and queue-routing blocks.

**Top three suspects for "frames reach the 9132's CPU port but never appear on
`lan-cpu0`":**

1. **`IDM+0x14 … +0x34` — the RX queue → CPU/thread map is overwritten with a value
   the vendor never uses.** These nine words are the per-queue routing fields for
   **RX queues 6…23**. `np_133.ko` does **not** write them at all (so the working
   stock board ran with the reset default `0x00010001`, which is what the proven
   image shows); the driver stamps **`0x00800080`** into all nine. The driver also
   uses `np_133.ko`'s *routing* words at `IDM+0x44…0x50` — but those are indexed by
   the same per-queue field, so the two halves of the vendor's scheme are now
   mutually inconsistent. **This is the single most suspicious item**: if the 16-bit
   field selects the interrupt group / CPU that a queue's completion is reported on,
   `0x80` selects a group that does not exist, and no completion ever reaches SPI1.

2. **`IDM+0x10c` / `IDM+0x110` — the buffer-pool ring registers are written in the
   wrong encoding.** The vendor always ends up with
   `((depth-1) << 10) | 0x100` — **the `0x100` bit is set in every code path**
   (`idm_rx_bp_init` `orr w4, w4, #0x100` @ `0x18f4c`; `idm_rev_bp_init`
   `orr w1, w1, #0x100` @ `0x1936c`). The driver writes the raw count `0x1000`
   (`ZX279133_IDM_BP_RING_SIZE`), which **clears the vendor's `0x100` flag** and puts
   a 1 into the bit position the vendor uses for the *depth field*. The proven,
   traffic-carrying image holds `0x00000100` at both words.

3. **`IDM+0x70` — the RX descriptor-ring depth word.** The vendor stores
   `depth - 0x18` (`sub w1, w1, #0x18` @ `0x18d24`, `str w1, [x3, #0x70]` @ `0x18d44`);
   the driver stores `depth - 1` = `0x7ff` (`zx279133-idm.c:1428`). With the vendor
   DT's depth of 1024 the vendor's value is 1000 = `0x3e8`, which is exactly what the
   proven image holds. If the hardware sizes the ring from this word, the driver runs
   the ring 24 descriptors deeper than the vendor's own layout.

A fourth structural gap is not a register value at all and is worth the same
attention: **the driver polls only 16 of the hardware's 24 RX queues.**
`zx279133_idm_rx_poll_order[]` (`zx279133-idm.c:993-995`) contains 16 entries
(`7,15,6,14,…,0,8`), the two NAPI masks cover only queues 0–15
(`ZX279133_IDM_LOCAL_MASK` = 4–11, `ZX279133_IDM_DIRECT_RX_MASK` = 0–3 + 12–15), and
`zx279133_idm_rx_count()` can only decode counters for queues 0–15. Queues 16–23
exist in hardware (the vendor builds 24 descriptor queues) and are **never drained by
anyone**. If the 9132's CPU-bound frames land in one of them, `lan-cpu0` stays
empty no matter how correct the register image is.

---

## 2. Where the vendor's SoC-side RX bring-up actually is

The call chain was established from the `.ko`'s own CALL26 relocations (a `bl` in a
relocatable object is a relocation placeholder, so the target comes from the
relocation, not the immediate):

```
init_module                 np_133.ko:.init.text 0x8ac
 └─ plat_initModule         .init.text 0x000   @0x20  → nppt_init
     └─ nppt_init           .text 0xc8d4
         ├─ plat_sipc_init  @0xc8ec
         ├─ greg_init       @0xc8f4   (writes GREG+0x68/+0x6c, +0x20078)
         ├─ nppt_smac_init  @0xc8fc   → xmac_init_by_port(4) / (5)
         └─ idm_init        @0xc904
             ├─ idm_qmg_desc_rd_mode_cfg   @0x19638   (str wzr → idm+0x490)
             ├─ idm_rx_bp_init             @0x1966c   (→ idm+0x104/0x108/0x10c/0x110)
             ├─ idm_tx_bp_init             @0x19670
             ├─ idm_rev_bp_init            @0x19674   (→ idm+0x10c/0x110/0x118/0x11c/0x408/0x40c)
             ├─ idm_rx_desc_init           @0x1969c   (→ idm+0x008, idm+0x070)
             ├─ idm_tx_desc_init           @0x196a0
             ├─ idm_queue_rx_int_num_thread(7, 1) @0x196b4  (→ idm+0x14)
             ├─ idm_int_mask_configure_init @0x196cc  (→ idm+0x44/0x48/0x4c/0x50)
             ├─ idm_net_init               @0x196d0   (→ idm+0x40, 4 IRQs)
             └─ request_threaded_irq × 4
```

So `np_133.ko`'s `idm_init()` **is** the live IDM bring-up on this board — it runs
when the module loads, via `plat_initModule`. There is no second candidate.

`nppt_smac_init` reaches `xmac_init_by_port(4)` → `xmac_init_by_work_mode(0, …)` →
`xamc_init_conf_by_speed(0)`, and the alternate `xmac0_to_extend` path
(`xmac_hsgmii_to_extend_init`, `0x1b6e4`, `xamc_init_conf_by_speed` @ `0x1b74c`)
reaches the *same* function. Both therefore produce the same XMAC0 image, so the
XMAC0 comparison in §3 is meaningful.

---

## 3. Discrepancy table

Vendor values are `np_133.ko`'s own instructions (address quoted) and, where the
vendor does not write the word at all, the **proven traffic-carrying value** from
`docs/09 §1`. Driver values are `ci-build-repo` with `file:line`.

### 3.A — the vendor SETS it, the driver never sets it

| # | Register / field | Vendor (evidence) | Driver | Class | Pri |
| --- | --- | --- | --- | --- | --- |
| A1 | `IDM+0x0bc` | `0001964c  str wzr, [x0, #0xbc]` in `idm_init` — a bring-up zero. (Queue 8's TX doorbell; TX-side, so low RX relevance, but it *is* a vendor bring-up write the driver omits.) | never written at bring-up; only reached through the per-queue TX doorbell helper `zx279133_idm_tx_doorbell_reg()` | missing | P3 |
| A2 | `IDM+0x14` upper half (RX queue 7's CPU/thread field) | `idm_queue_rx_int_num_thread(7, 1)` → `idm+0x14` upper 16 bits = 1 (`0x193fc`: `orr w3, w3, w1, lsl #16`). On the live board this is a **no-op**, because the reset default already holds 1 — which is itself the evidence that the field should read 1. | `0x00800080` → upper 16 bits = `0x0080` | value differs | **P1** |

### 3.B — the value differs

| # | Register / field | Vendor (evidence) | Driver | Class | Pri |
| --- | --- | --- | --- | --- | --- |
| B1 | `IDM+0x14 … +0x34` — **RX queues 6…23 → CPU/thread** (2 fields per word, 18 queues) | `np_133.ko` **never writes these nine words**; the working stock board therefore held the reset default `0x00010001` (proven image `19280014: 00010001 00010001 00010001 00010001` … through `19280034`). `idm_int_mask_configure_init` then derives the per-group masks `IDM+0x44…0x50` from the *same* field. | `for (i = 0x14; i <= 0x34; i += 4) writel(0x00800080, idm + i);` — `zx279133-idm.c:1421-1422` | value differs; the driver's value is the `plat_132.ko` image, not `np_133.ko`'s | **P1** |
| B2 | `IDM+0x10c`, `IDM+0x110` — BP ring depth/flags | `idm_rx_bp_init`: `((depth>>12)-1)<<21 \| 0x100` (`0x18f40-0x18f4c`); then `idm_rev_bp_init` RMWs it to `((free_ring_depth-1)<<10) \| 0x100` (`0x19340-0x1935c`, mask `0xffe003ff` keeps bits 0–9 and 22–31). **`0x100` is set in every path.** Proven image: `0x00000100` at both words. | `writel(ZX279133_IDM_BP_RING_SIZE, idm + 0x010c);` / `… 0x0110);` = `0x1000` — `zx279133-idm.c:1499-1500` | value differs: driver's `0x1000` clears the vendor's `0x100` flag and puts a 1 into the vendor's depth field | **P1** |
| B3 | `IDM+0x70` — RX descriptor ring depth | `idm_rx_desc_init`: `sub w1, w1, #0x18` (`0x18d24`) then `str w1, [x3, #0x70]` (`0x18d44`) ⇒ `depth − 0x18`. With the vendor DT's `idm_desc_rx_depth = 0x400` that is `0x3e8` = 1000, which is exactly the proven image's value. | `writel(ZX279133_IDM_RX_QUEUE_DESC_DEPTH - 1, idm + 0x0070);` = `0x7ff` — `zx279133-idm.c:1428` | value differs: `0x7ff` vs `depth−0x18` (2024 at depth 2048) | **P1** |
| B4 | `IDM+0x40c` — free-ring count/flags | `idm_rev_bp_init`: `orr w1, w1, #0x100; str w1, [x0, #0x40c]` (`0x1936c-0x19374`) ⇒ `((depth−1)<<10) \| 0x100`. Proven image holds `0x001ffc00` (U-Boot's value). | `writel(0x00003d00, idm + 0x040c);` — `zx279133-idm.c:1501` | value differs (depth field; `0x3d00` decodes as 15, not a ring size) | P2 |
| B5 | `IDM+0x10` — RX BP "receive threshold" | `idm_init`: `ldr w1, [g_idm_rx_bp_retev_num_th]; str w1, [x0, #0x10]` (`0x19688-0x1968c`). Proven image: `0x00000020`. | `writel(128, idm + 0x0010);` — `zx279133-idm.c:1420` | value differs | P3 |
| B6 | `IDM+0x6c` | not written by `np_133.ko`; proven image `0x00000800` | `writel(0x000007c1, idm + 0x006c);` — `zx279133-idm.c:1488` | value differs vs the proven image | P4 |
| B7 | `IDM+0x38` | not written; proven image `0x00001000` | `writel(50000, idm + 0x0038);` — `zx279133-idm.c:1423` | value differs vs the proven image | P4 |
| B8 | `IDM+0x74`, `0x90`, `0x94`, `0x5c0` | not written; proven image `0x00000000`, `0`, `0`, `0x00000067` | `0x210` / `20` / `1` / `7` — `zx279133-idm.c:1429-1431`, `:1463` | value differs vs the proven image | P4 |
| B9 | `XMAC0+0x10` RX_CTRL, **bit 6 (0x40)** | `xamc_init_conf_by_speed`: `mov w2, #0xc6; movk w2, #0x3e80, lsl #16; str w2, [x1, x0]` (`0x1a240-0x1a24c`) ⇒ `0x3e8000c6`, bit 6 **set** | `0x3e800086 \| ZX279133_XMAC_RX_IPC` = `0x3e800286` — `zx279133-switch.c:613-614`, `zx279133-mac.c:429-430` | value differs: the driver **clears** a bit the vendor sets | P2 |
| B10 | `XMAC0+0x20` FRAME_CFG, bit 0 | `0x80000000` — `mov w2, #-0x7fffffff; str w2, [x1, x0]` (`0x1a258-0x1a260`) | `0x80000001` — `zx279133-switch.c:615-616`, `zx279133-mac.c:431` | value differs (driver sets bit 0) | P3 |

### 3.C — the driver sets what the vendor does not

| # | Register / field | Vendor | Driver | Class | Pri |
| --- | --- | --- | --- | --- | --- |
| C1 | `IDM+0x000` **bit 13** | `idm_init` does `orr w1, w1, #0x1000` (`0x1960c-0x19610`) — bit 12 only; bit 13 is left at 0. The proven image's `IDM+0x000 = 0x01ff0007` confirms bit 13 = 0. | `value \|= 0x00003000;` — `zx279133-idm.c:1412` — sets bits 12 **and** 13 | driver sets extra | P2 |
| C2 | `XMAC0+0x10` RX_CTRL, **bit 9 (0x200)** — the driver's "IPC" | `0x3e8000c6` — bit 9 **clear** | `0x3e800286` — bit 9 set (`ZX279133_XMAC_RX_IPC`, `zx279133.h:477`) | driver sets extra. **May well be correct**: on this board XMAC0's far side is the 9132's internal CPU port, a different use of XMAC0 than the vendor's PON/external-PHY lane. Flagged, not asserted. | P2 |
| C3 | `IDM+0x0004` — TX descriptor base | `np_133.ko` never stores it (`idm_tx_desc_init` writes `0x000c`, not `0x0004`) | `writel(lower_32_bits(eth->tx_descs_dma), … + ZX279133_IDM_TX_BASE)` — `zx279133-idm.c:1545-1546` | driver sets extra (TX) | P4 |
| C4 | `IDM+0x38`, `0x6c`, `0x74`, `0x90`, `0x94`, `0x5c0` | not written by `np_133.ko` | see B6–B8 | driver sets extra (all from the `plat_132.ko` image) | P4 |
| C5 | RX queue coverage | vendor builds **24** RX descriptor queues (`idm_rx_desc_init` loop `x0` from `bss+0xe0` to `bss+0x560` step `0x30` ⇒ (0x560−0xe0)/0x30 = **24** entries, each with its own base/depth/len) and its cpuid table spans 24 queues | the driver *allocates* 24 queues (`ZX279133_IDM_RX_QUEUES 24`, `zx279133.h:412`) but **polls only 16** (`zx279133_idm_rx_poll_order[]`, `zx279133-idm.c:993-995`), and `zx279133_idm_rx_count()` (`zx279133-idm.c:39-50`) can only decode counters for queues 0–15 | driver covers less than the vendor | **P1** |
| C6 | The **RX descriptor base** is programmed from the TX path | `idm_rx_desc_init` owns `idm+0x008` | `writel(lower_32_bits(eth->rx_descs_dma), idm + 0x0008);` sits inside `zx279133_idm_tx_configure()` — `zx279133-idm.c:1418` — reached only via `zx279133_idm_tx_prepare()` | ordering smell: on the enable path it happens to run first (`zx279133-netdev.c:30-35`), but the RX ring base lives in a function named `tx` | P3 |
| C7 | **TX teardown restores the whole shared IDM block, RX included** | the vendor has no such split — `idm_init` owns everything | `zx279133_idm_cfg_offsets[]` (`zx279133-idm.c:1370-1386`) includes the RX-only words `0x0008`, `0x0014…0x0034`, `0x0044…0x0050`, `0x0104`, `0x0108`, `0x010c`, `0x0110`, `0x0118`, `0x011c`, `0x0408`, `0x040c`; `zx279133_idm_tx_release()` → `zx279133_idm_tx_deactivate()` → `zx279133_idm_tx_deconfigure()` restores all of them (`zx279133-idm.c:1504-1511`, `:1629-1630`). `zx279133_shared_idm_abort()` calls `zx279133_idm_rx_release()` **then** `zx279133_idm_tx_release(eth, true)` (`zx279133-netdev.c:57-58`). | driver sets extra / coupling bug: the WAN netdev going down tears the RX rings and interrupt routing out from under a still-up LAN netdev | P2 |

### 3.D — things that **match** (recorded so they are not re-litigated)

* `IDM+0x008` RX descriptor base — vendor `str w0, [x3, #8]` (`0x18d48`), driver
  `zx279133-idm.c:1418`. **Match.** (`idmgap.py`'s "absent" verdict is the
  decimal-offset regex bug.)
* `IDM+0x000` bits 31:28 (RX config depth) — vendor `orr w0, w4, w0, lsl #28` with
  `w0 = f(depth)` (`0x18d30-0x18d3c`), driver `(value & 0x8fffffff) | (1 << 28)`
  (`zx279133-idm.c:1413`). Both give `0x10000000`. **Match.**
* `IDM+0x000` bit 24 — the vendor's `idm_init` sets it (`|0x1f00000`) but
  `idm_rx_desc_init`'s `& 0x8fffffff` (`0x18d34`) clears it again; the driver's
  `0x00f00000` leaves it clear too. **Match on the final value.**
* `IDM+0x000c` = `0x04000000` — matches the proven image and the vendor's
  `idm_tx_desc_init`.
* `IDM+0x0040` = `0x07ffffff` — vendor `idm_net_init` (`0x18928`), driver
  `zx279133-idm.c:1471`. **Match.**
* `IDM+0x0044…0x0050` = `0x071ffbfb / 0x00200000 / 0x00400000 / 0x00800404` —
  vendor `idm_int_mask_configure_init` (`0x13f5c-0x13f7c`), driver
  `zx279133-idm.c:1484-1487`. **Match** (the driver's comment records that these
  came from the vendor's own printed `sg_idm_irq_mask_conf` line).
* `IDM+0x088` RX release encoding — vendor `idm_pkt_recv_update`:
  `orr w1, w1, w0, lsl #12; str w1, [x2, #0x88]` (`0x1492c-0x14930`) ⇒
  `count | (queue << 12)`; driver `writel(rx_work | (queue << 12), idm + ZX279133_IDM_RX_RELEASE);`
  (`zx279133-idm.c:901`). **Match.**
* `IDM+0x0100` BP refill — vendor `idm_new_bp_update` (`0xe234`) writes a *compound*
  word (`0x400` pulses for each 1024, then `(c2 & 0x3ff) << 16 | (c1 & 0x3ff)`);
  the driver writes a plain count (`zx279133-idm.c:241`). Under either plausible
  hardware decode (additive doorbell, or a `units<<10 | remainder` field split at
  bit 10) a plain count decodes to the same number, so this is **probably benign** —
  but see §5.
* BP ring entry byte order — vendor `rev w3, w3` (`0x18ff0`, big-endian), driver
  `cpu_to_be32(...)` (`zx279133-idm.c:185`). **Match.**
* RX descriptor geometry — 24 queues × `depth` descriptors × 32-byte descriptor:
  vendor `idm_rx_desc_init` (24 entries of `depth` × `g_idm_desc_len`), driver
  `sizeof(struct zx279133_idm_desc) * 24 * 2048` (`zx279133-idm.c:1157-1158`) with a
  matching per-queue stride of `ZX279133_IDM_RX_RING_SIZE` (`zx279133-idm.c:514-515`).
  **Match** (apart from `IDM+0x70`, B3, which tells the hardware the depth).
* `XMAC0` base — vendor `xmac_rx_enable`:
  `ubfiz x0, x0, #0x12, #0xe; add x0, x0, #0x140, lsl #12; add x0, x0, #0x10`
  (`0x1a0d4-0x1a0dc`) ⇒ port 0 → `0x140000 + 0x10`; driver `ZX279133_XMAC0_BASE 0x140000`
  (`zx279133.h:473`). **Match.**
* `XMAC0+0x00` TX_CTRL `0x00010000`, `XMAC0+0x280` MODE_CFG `2`, `XMAC0+0x3400`
  MISC_CFG `|= BIT(9)` — **Match** with `xamc_init_conf_by_speed`
  (`0x1a218-0x1a28c`).

---

## 4. Ranked list of things to try, with the evidence for each

### 1. Stop overwriting `IDM+0x14…0x34`; leave the reset default alone
*Evidence.* `np_133.ko` writes none of those nine words (verified by hand and by
`idmgap.py`'s store list, which contains no `0x14`…`0x34`); the proven,
traffic-carrying image holds `0x00010001` at all nine. The only vendor write is
`idm_queue_rx_int_num_thread(7, 1)`, which sets `idm+0x14`'s **upper** half to 1 —
already the value the reset default holds, i.e. a no-op on this board. The driver's
`0x00800080` (`zx279133-idm.c:1421-1422`) is the `plat_132.ko` value
(`zx279133-idm.c:1393` says "`0x014..0x034 = 0x800080`").
*How to test.* Write `0x00010001` to all nine words instead of `0x00800080`, and
re-check whether the `rx` IRQ (SPI1) starts counting and whether `lan-cpu0` sees
frames. This is one line.

### 2. Re-encode `IDM+0x10c` / `IDM+0x110` the way the vendor does
*Evidence.* `idm_rev_bp_init` `0x19340-0x19370`: the depth field lives at bits
10–21 and the flag bit `0x100` is set unconditionally; `idm_rx_bp_init` `0x18f4c`
sets `0x100` too. The proven image holds `0x00000100` at both words.
*How to test.* Set `((depth-1) << 10) | 0x100` at both `0x10c` and `0x110` instead
of `0x1000`, i.e. make `ZX279133_IDM_BP_RING_SIZE` produce the vendor's encoding.
Note the driver's current reasoning ("4096 is the self-consistent value and 256 was
U-Boot's smaller table", `docs/09 §4`) assumes the register is a raw entry count;
the vendor's own code says it is not.

### 3. Fix `IDM+0x70` to `depth − 0x18`
*Evidence.* `idm_rx_desc_init` `0x18d24` `sub w1, w1, #0x18` → `0x18d44`
`str w1, [x3, #0x70]`. The proven image's `0x3e8` = 1024 − 24, matching the vendor
DT's `idm_desc_rx_depth = 0x400` (`docs/09 §1`). The driver writes `depth − 1`
(`zx279133-idm.c:1428`).
*How to test.* Write `ZX279133_IDM_RX_QUEUE_DESC_DEPTH - 0x18` and see whether
descriptors start being consumed. Also re-check `ZX279133_IDM_RX_RING_SIZE` /
`ZX279133_IDM_RX_QUEUE_DESC_DEPTH` (both currently 2048) against the vendor DT.

### 4. Extend the NAPI to the hardware's 24 queues
*Evidence.* `idm_rx_desc_init` builds 24 RX descriptor queues
((0x560−0xe0)/0x30 = 24). `idm_int_mask_configure_init` walks
`g_idm_ring_free_queue_cnt + 0x18` entries of `sg_idm_queue_cpuid_map_table` — i.e.
24 when `g_idm_ring_free_queue_cnt = 6`. The driver polls 16
(`zx279133-idm.c:993-995`), and its two masks leave 16–23 uncovered.
*How to test.* Grow `zx279133_idm_rx_poll_order[]` to 24 entries and extend
`zx279133_idm_rx_count()` to the counter registers for queues 16–23 (the register
index arithmetic in `zx279133-idm.c:39-50` is what has to be extended).

### 5. Determine where the 9132's qid `0x193` actually lands
*Evidence.* The driver writes the vendor's ERAM tables 0x22 / 0x27 / 0x19
(`zx279133-np.c:2568-2612`, base qid `0x193` at `zx279133-np.c:2565`), but **nothing
in the driver maps that qid onto an IDM RX queue**, and nothing in `np_133.ko` shows
the mapping either: the only bridge is `sg_idm_queue_cpuid_map_table`, which is a
`SHN_COMMON` symbol (`vscan.py info` → `sec=` empty, `value=0x0 size=0x0`) and
therefore has **no contents in the .ko**. See §5 item 1.
*How to test.* Read `idm+0x014…0x034` on the **stock** system (the vendor's own
`/proc/pon/tm_debug` interface) and compare, field by field, with the driver's
`0x00800080`. That single dump also tells you which cpuid class the CPU-bound
queue belongs to, and hence which of `IDM+0x44…0x50` carries it.

### 6. Take the vendor's XMAC0 image, then re-add IPC deliberately
*Evidence.* `xamc_init_conf_by_speed` @ `0x1a240-0x1a24c` gives
`XMAC0+0x10 = 0x3e8000c6` and `XMAC0+0x20 = 0x80000000`. The driver uses
`0x3e800286` / `0x80000001` (`zx279133-switch.c:613-616`). The driver's `0x200`
(IPC) is *not* in the vendor's image, and its `0x40` bit is.
*How to test.* Try `0x3e8000c6 | 0x200` (vendor bit 6 restored, IPC kept) and
`0x3e800086 | 0x200` separately, and read back `XMAC0+0x10` after the lane comes up
(`zx279133_switch_lane_snapshot()` already prints it). Note honestly that the
vendor's XMAC0 on the stock board is the **PON/external-PHY** lane, so this is the
item where the vendor is the *weakest* reference.

### 7. Decouple the RX words from the TX teardown path
*Evidence.* `zx279133_idm_cfg_offsets[]` mixes RX-only words into the TX
save/restore set (`zx279133-idm.c:1370-1386`), and
`zx279133_shared_idm_abort()` calls `zx279133_idm_tx_release(eth, true)` after
`zx279133_idm_rx_release()` (`zx279133-netdev.c:57-58`), which restores
`0x0014…0x0034`, `0x0104…0x040c`, `0x0044…0x0050` and `0x0008` to their pre-driver
values.
*How to test.* Bring `lan-cpu0` up and the WAN netdev down (and vice versa) and
re-read `idm+0x104`, `idm+0x14`, `idm+0x44`. If they revert, RX is being torn
down by an unrelated close.

---

## 5. What could **not** be determined offline

1. **The contents of `sg_idm_queue_cpuid_map_table`** — the vendor's switch-queue-id
   → IDM-queue map. It is a `SHN_COMMON` symbol in `np_133.ko`
   (`vscan.py info sg_idm_queue_cpuid_map_table` → `sec=` (none), `value=0x0`,
   `size=0x0`), i.e. a tentative definition with no bytes in the object file, and
   nothing in `np_133.ko` writes it (the only reference is the *read* in
   `idm_int_mask_configure_init` @ `0x13ed4`). It must live in another object file
   or be filled in at runtime.
   **Settled by:** a dump of that array from the running stock system (gdb on
   `kernel-2b5.elf` / the vendor's `module_ind_ram` debug interface), or by
   reading `idm+0x14…0x34` on the stock board, which is the same information
   already folded into the masks at `idm+0x44…0x50`.
2. **The runtime values of every `g_idm_*` global** — `g_idm_desc_rx_depth`,
   `g_idm_rx_normal_bp_ring_depth`, `g_idm_rx_jumbo_bp_ring_depth`,
   `g_idm_free_ring_depth`, `g_idm_ring_free_queue_cnt`, `g_idm_rx_queue_num`,
   `g_idm_bp_width`, `g_bp_size`, `g_jumbo_bp_size`, `g_idm_rx_normal_buffer_num`,
   `g_idm_rx_jumbo_buffer_num`, `g_idm_ring_*_size`, `g_idm_payload_buffer_*`.
   They all live in `.bss` (`vscan.py info` → no section, value 0) and are set at
   runtime from the vendor device tree; `greg_init` (`0xcb04`) only *reads*
   `g_bp_size`/`g_jumbo_bp_size`.
   **Settled by:** the vendor's DT (`idm_desc_rx_depth`, `idm_rx_normal_bp_ring_depth`,
   `bppe_pool_size`, `idm_rx_normal_buffer_num` — the ones quoted in `docs/09 §1`),
   or a runtime dump of the globals.
3. **The register semantics of `IDM+0x70`, `0x10c/0x110`, `0x14…0x34`, `0x40c`**
   (ring depth? pipeline headroom? per-queue CPU id? interrupt group?). The
   *values* are known from the disassembly, the *meaning* is not. The strongest
   indirect evidence is that the vendor always sets `0x100` in `0x10c/0x110/0x40c`
   and never writes `0x14…0x34`.
   **Settled by:** a ZX279133 datasheet, or an A/B experiment on hardware.
4. **How the 9132's 9-bit switch queue id (`0x193`, `7`) becomes an IDM RX queue.**
   `0x193` is too large to be an IDM queue index (the IDM has 24); it fits the SPA
   word-1 `QUEUE_ID(14..22)` field (`zx279133-np.c:1902`) and the ERAM outport
   map's 9-bit queue field. No translation table is visible in `np_133.ko`.
   **Settled by:** injecting a tagged frame into a UNI port and reading which
   IDM RX queue's counter increments (the counters at `idm+0xc4…0xe0`), or by
   reading the queue-id field of the descriptor the vendor's own driver receives.
5. **Whether the vendor's XMAC0 image (`0x3e8000c6`) is what the stock board
   actually held.** `nppt_smac_init` reaches `xmac_init_by_port(4)` only inside the
   HOL branch (`g_hol_enable == 1 && g_hol_port == 5`, `0xdc40-0xdc88`); the
   `xmac0_to_extend` branch reaches the same `xamc_init_conf_by_speed`
   (`0x1b74c`); but the normal branch calls `outerphy_phy_init` instead, i.e. on the
   stock board XMAC0 was the **external PHY** port. The values `g_hol_enable`,
   `g_hol_port`, `g_hol_mpw_flag`, `xmac0_to_extend` are runtime values.
   **Settled by:** a dump of `XMAC0+0x10` on the stock board.
6. **Whether `plat_132.ko` is the same silicon as `np_133.ko`.** `plat_132.ko` is not
   in this repository (`Get-ChildItem -Recurse -Filter 'plat_*.ko'` → nothing), so
   the driver's source image could not be compared directly — only against
   `np_133.ko`.
   **Settled by:** obtaining `plat_132.ko` and running `staging/idmgap.py` on it.
7. **Whether the `IDM+0x100` BP-refill doorbell's compound encoding matters.** The
   vendor writes `0x400` pulses plus a remainder; the driver writes a plain count.
   Under both plausible hardware decodes the plain count yields the same number, so
   this is *probably* benign, but it could not be settled offline.

---

## 6. Summary of the mismatch classes

* **Missing configuration:** `IDM+0x0bc` (vendor `str wzr` in `idm_init`).
* **Value differs (RX-critical):** `IDM+0x14…0x34` (queue→CPU/thread map),
  `IDM+0x10c`/`0x110` (BP ring depth/flag encoding), `IDM+0x70` (RX descriptor ring
  depth), `XMAC0+0x10` bit 6.
* **Value differs (other):** `IDM+0x40c`, `0x10`, `0x6c`, `0x38`, `0x74`, `0x90`,
  `0x94`, `0x5c0`, `XMAC0+0x20` bit 0.
* **Driver sets what the vendor does not:** `IDM+0x000` bit 13, `XMAC0+0x10` bit 9
  (IPC), `IDM+0x0004`, and the whole `plat_132.ko`-derived tail
  (`0x38/0x6c/0x74/0x90/0x94/0x5c0`).
* **Driver covers less than the vendor:** RX queues 16–23 are allocated but never
  polled, and the RX descriptor base and the RX half of the shared IDM block are
  owned by functions named `tx`.

---

## 7. Appendix — how the evidence was produced, and how to reproduce it

```sh
cd C:/UGit/openwrt/SK-D840N
export PYTHONIOENCODING=utf-8

# vendor symbol / disassembly (the helper this repo already has)
python staging/swspa.py pon/vendor-modules/np_133.ko syms 'xmac|idm|rx_|ring|qid|qmg|ssch|usch|woe|red_'
python staging/swspa.py pon/vendor-modules/np_133.ko dis idm_init
python staging/swspa.py pon/vendor-modules/np_133.ko dis idm_rx_desc_init
python staging/swspa.py pon/vendor-modules/np_133.ko dis idm_rx_bp_init
python staging/swspa.py pon/vendor-modules/np_133.ko dis idm_rev_bp_init
python staging/swspa.py pon/vendor-modules/np_133.ko dis idm_int_mask_configure_init
python staging/swspa.py pon/vendor-modules/np_133.ko dis idm_queue_rx_int_num_thread
python staging/swspa.py pon/vendor-modules/np_133.ko dis idm_pkt_recv_update
python staging/swspa.py pon/vendor-modules/np_133.ko dis idm_new_bp_update
python staging/swspa.py pon/vendor-modules/np_133.ko dis xamc_init_conf_by_speed
python staging/swspa.py pon/vendor-modules/np_133.ko dis xmac_rx_enable
python staging/swspa.py pon/vendor-modules/np_133.ko dis nppt_init
python staging/swspa.py pon/vendor-modules/np_133.ko modinit

# vendor store-site diff against the driver (READ ONLY; needs the decimal-offset
# regex bug in staging/idmgap.py fixed before its verdicts are trustworthy)
python staging/idmgap.py pon/vendor-modules/np_133.ko \
    ci-build-repo/kernel-overlays/drivers/net/ethernet/zte/zx279133-idm.c \
    ci-build-repo/kernel-overlays/drivers/net/ethernet/zte/zx279133-netdev.c \
    ci-build-repo/kernel-overlays/drivers/net/ethernet/zte/zx279133-main.c
```

A small local helper was used for two things `staging/swspa.py` cannot do:
finding the *callers* of a symbol (its `callers` mode does not strip the
`<.text>` suffix that `rela_map()` appends, so it never matches) and dumping
`SHN_COMMON` symbols. It lives outside the repository at
`C:/UGit/openwrt/SK-D840N/.tmpresearch/vscan.py` and is **not** part of the
deliverable; the repo itself was not modified.

```
python .tmpresearch/vscan.py pon/vendor-modules/np_133.ko callers idm_init
python .tmpresearch/vscan.py pon/vendor-modules/np_133.ko info sg_idm_queue_cpuid_map_table
```

**Vendor symbol / address index used in this report**

| symbol | address | size |
| --- | --- | --- |
| `init_module` / `np_init_module` | `.init.text` `0x8ac` | `0x368` |
| `plat_initModule` | `.init.text` `0x000` | `0x030` |
| `nppt_init` | `0xc8d4` | `0x05c` |
| `nppt_smac_init` | `0xda1c` | `0x4e4` |
| `idm_init` | `0x194e4` | `0x298` |
| `idm_rx_desc_init` | `0x18c4c` | `0x108` |
| `idm_tx_desc_init` | `0x18d54` | `0x114` |
| `idm_rx_bp_init` | `0x18e68` | `0x278` |
| `idm_tx_bp_init` | `0x190e0` | `0x164` |
| `idm_rev_bp_init` | `0x19244` | `0x148` |
| `idm_queue_rx_int_num_thread` | `0x1938c` | `0x08c` |
| `idm_qmg_desc_rd_mode_cfg` | `0x194cc` | `0x018` |
| `idm_int_mask_configure_init` | `0x13e88` | `0x114` |
| `idm_net_init` | `0x18900` | `0x308` |
| `idm_pkt_recv_update` | `0x1490c` | `0x05c` |
| `idm_rx_pkts_update` | `0x14968` | `0x068` |
| `idm_new_bp_update` | `0xe234` | `0x060` |
| `np_free_bp` | `0xef90` | `0x11c` |
| `greg_init` | `0xcb04` | `0x048` |
| `xamc_init_conf_by_speed` | `0x1a200` | `0x098` |
| `xmac_rx_enable` | `0x1a0d0` | `0x024` |
| `xmac_init_by_port` | `0x1b0b4` | `0x01c` |
| `xmac_init_by_work_mode` | `0x1ae20` | `0x184` |
| `xmac_hsgmii_to_extend_init` | `0x1b6e4` | `0x120` |
| `glb_cfg_table_cpu_base_qid_set` | `0xbdc5c` | `0x0c4` |
| `reason_cpuqid_map_cpu_qid_set` | `0xb1bf8` | `0x0ec` |

**Driver file:line index used in this report**

| location | what |
| --- | --- |
| `zx279133-idm.c:39-50` | `zx279133_idm_rx_count()` — counter decode, queues 0–15 only |
| `zx279133-idm.c:184-187` | BP ring entry publication (big-endian) |
| `zx279133-idm.c:241` | `IDM+0x0100` BP-refill doorbell |
| `zx279133-idm.c:514-515` | per-queue RX descriptor base (stride = `RX_RING_SIZE`) |
| `zx279133-idm.c:576-577` | `lan_source` classification (`queue < 8` ⇒ LAN) |
| `zx279133-idm.c:901` | `IDM+0x0088` RX release |
| `zx279133-idm.c:993-995` | `zx279133_idm_rx_poll_order[]` — 16 queues |
| `zx279133-idm.c:1005-1006` | per-NAPI queue masks |
| `zx279133-idm.c:1155-1197` | `zx279133_idm_rx_prepare()` |
| `zx279133-idm.c:1370-1386` | `zx279133_idm_cfg_offsets[]` (save/restore set) |
| `zx279133-idm.c:1388-1398` | the comment naming `plat_132.ko` as the image's source |
| `zx279133-idm.c:1409-1416` | `IDM+0x000` RMW (`\|0x3000`) |
| `zx279133-idm.c:1418-1421` | `IDM+0x008`, `0x00c`, `0x010`, `0x014…0x034 = 0x00800080` |
| `zx279133-idm.c:1423-1432` | `0x038`, `0x054…0x060`, `0x070 = depth-1`, `0x074`, `0x090`, `0x094`, `0x0c0` |
| `zx279133-idm.c:1446-1463` | `0x490`, `0x2c8`, `0x124`, `0x3fc`, `0x5c0` |
| `zx279133-idm.c:1471-1488` | `0x040`, `0x044…0x050`, `0x06c` |
| `zx279133-idm.c:1490-1501` | `0x104`, `0x108`, `0x118`, `0x11c`, `0x408`, `0x10c`, `0x110`, `0x40c` |
| `zx279133-idm.c:1504-1511` | `zx279133_idm_tx_deconfigure()` — restores the shared block |
| `zx279133-idm.c:1613-1632` | `zx279133_idm_tx_release()` → `tx_deactivate()` |
| `zx279133-netdev.c:26-48` | `zx279133_shared_idm_prepare()` — tx_prepare **then** rx_prepare |
| `zx279133-netdev.c:50-59` | `zx279133_shared_idm_abort()` |
| `zx279133-switch.c:595-622` | `zx279133_switch_xmac_program()` — XMAC0 image |
| `zx279133-mac.c:410-436` | `zx279133_xmac_program()` — XMAC1 image (same constants) |
| `zx279133-np.c:2561-2612` | ERAM 0x22 / 0x27 / 0x19 — the qid `0x193` CPU path |
| `zx279133-np.c:1902` | SPA word 1 `QUEUE_ID(14..22)` |
| `zx279133.h:382-455` | IDM base, masks, queue/ring constants, depth constants |
| `zx279133.h:473-491` | XMAC0 base and field defines |
