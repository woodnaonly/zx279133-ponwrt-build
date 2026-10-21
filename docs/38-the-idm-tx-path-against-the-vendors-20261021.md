# re-idm-tx — np_133.ko vs the driver's SoC-side transmit setup

Offline reverse-engineering comparison. **No board, no serial console, no writes to
`ci-build-repo`.** Everything below is derived from reading four things:

| input | what it is |
| --- | --- |
| `pon/vendor-modules/np_133.ko` (md5 `92B68212542930114769E3C69EFE64B6`) | the vendor module that runs on the stock board, and the only one that touches the IDM block. `staging/vendor-full/kmodule/np_133.ko` is byte-identical, so the reference is unambiguous. |
| `ci-build-repo/kernel-overlays/drivers/net/ethernet/zte/zx279133-{idm,netdev,main,switch}.c` and `zx279133.h` | the driver under review (canonical repo; `ci-wanport` and `tests/kernelsrc` ignored except where noted). |
| `devtools/uboot-stock.dts.txt` (lines 1629-1663) and `ci-wanport/decompiled/sk-d840n-stock.dts` (lines 1821-1856) | the stock device tree, i.e. the **runtime values** of the `g_idm_*` globals that `np_133.ko` reads from `.bss`. This is what makes several vendor values computable offline. |
| `docs/09-idm-proven-image-20261020.md`, `docs/11-vendor-linux-bringup-transcript-20261020.md` | the traffic-proven U-Boot IDM image and the vendor Linux bring-up transcript (`idm_ddr_phy_start 0x80b23000`, docs/11 line 21). |

**Caveat that colours everything below:** Capstone prints an aarch64 memory offset in
**decimal** when it is below 10 (`str w0, [x2, #4]`) and in hex otherwise (`[x2, #0x10]`).
`staging/npidmsites.py` and `staging/idmgap.py` both match their store-offset regex against
`#(0x[0-9a-f]+)` only, so **every IDM access at offset 0…9 is invisible to them**. One of
those offsets is `IDM+0x004`, the TX descriptor base. A corrected scanner
(`staging/ctl/scratch-idmsites.py`, written for this report and deleted afterwards) finds it
at `idm_tx_desc_init` `0x18e5c`. Two rows of `staging/ctl/re-idm-rx.md` (§3.C C3 and §3.B B4)
are affected by that blindness and are corrected in §8.2.

---

## 1. Executive summary

**The driver's TX bring-up is much closer to the vendor's than the RX side is, and the
TX-critical registers are, with two exceptions, correct.** Of the five vendor TX-path
register writes, three are reproduced exactly (`0x004`, `0x00c`'s final value, `0x40c`),
one is omitted (`0x00bc`), and one differs in three bits (`0x000`).

* `IDM+0x004` — the **TX descriptor ring base**. The vendor writes it
  (`idm_tx_desc_init` `0x18e5c`); so does the driver (`zx279133-idm.c:1574-1575`). **Match in
  role.** (`re-idm-rx.md` §3.C C3's claim that "np_133.ko never stores `idm+0x0004`" is a
  false negative from the decimal-offset bug.)
* `IDM+0x00c` — the **TX descriptor depth field**. The vendor encodes a log2 code in bits
  16-19 (`0x18e48-0x18e54`); with `idm_desc_tx_depth = 0x400` the code is 0, and the
  driver writes `0x400 << 16 = 0x04000000`, whose bits 16-19 are also 0. With U-Boot's
  `0x04000000` already in the word, **the final value is identical**. The driver replaces
  the whole word where the vendor read-modify-writes two fields, which is a latent bug, not
  a present one.
* `IDM+0x40c` — the **free-ring count/flags**. Vendor `0x00003D00`
  (`idm_rev_bp_init` `0x1936c-0x19374`); driver `0x00003d00` (`zx279133-idm.c:1530`).
  **Exact match.** (`re-idm-rx.md` §3.B B4 called this a difference; it is not — the
  driver's number is the vendor's encoding computed with `idm_free_ring_depth = 0x10000`.)
* `IDM+0x00bc` — the vendor zeroes the **TX doorbell of TX queue 8** at bring-up
  (`idm_init` `0x1964c`); the driver never writes it. Harmless today (U-Boot also leaves it
  0), but it is the one vendor TX-path write the driver omits.
* `IDM+0x000` — the IDM control word. The driver and the vendor disagree in **three bits**:
  the driver sets **bit 13**, leaves **bit 24** clear (behind a module parameter that
  defaults to 0), and writes **bits 31:28 = 1** where the vendor writes 0. The vendor sets
  bit 12 only and bit 24 always; the traffic-proven U-Boot image has bits 12, 13 and 24
  exactly as the vendor leaves them.

**Nothing in the vendor's TX bring-up is a "TX enable" bit.** `idm_init` has no enable
write: the vendor's TX path is enabled by *pointing the IDM at the rings* (`IDM+0x004`,
`IDM+0x00c`) and by *ringing the per-queue doorbell* at runtime. There is no vendor
counterpart to "enable TX in a CFG register", so there is nothing for the driver to be
missing there; the driver's `0x3000` on `IDM+0x000` is the only "enable-shaped" difference,
and it is a difference in the *wrong* direction (the driver sets a bit the vendor does not).

**The two differences that can plausibly stop a frame from reaching the XMAC0 TX datapath**
are both in the `IDM+0x000` control word and both are of the "the driver writes a bit the
vendor never writes, or fails to write a bit the vendor always writes" kind:

1. **`IDM+0x000` bit 24 clear (driver) / set (vendor, and the traffic-proven image).**
   The vendor's `idm_init` writes `(v & 0xf00fffff) | 0x01f00000` — bits 20…24 — and
   `idm_rx_desc_init`'s `& 0x8fffffff` **preserves** bit 24 (it clears bits 30, 29, 28 and
   keeps bit 31). The driver writes `(v & 0xf00fffff) | 0x00f00000` — bits 20…23 — so bit 24
   ends up 0 unless `zx279133_idm_cfg_bit24` is set, and that parameter defaults to 0
   (`zx279133.h:48`). The proven working image holds `0x01ff0007`, whose bit 24 is set.
   (`re-idm-rx.md` §3.D says the two "match on the final value"; that conclusion is wrong
   for the same reason as C3 — the mask was mis-read.)
2. **`IDM+0x000` bit 13 set (driver) / clear (vendor and the proven image).**
   The driver's `value |= 0x00003000` sets bits 12 **and** 13; the vendor's `orr #0x1000`
   sets bit 12 only, and `0x01ff0007` has bits 12 and 13 clear.

**The mirror image of the RX findings.** Three of the RX report's items have an exact TX
counterpart in the same registers, and one RX conclusion turns out to be wrong:

| RX finding (`re-idm-rx.md`) | TX mirror |
| --- | --- |
| B2 — `IDM+0x10c`/`0x110` written in the wrong encoding (driver `0x1000` vs the vendor's `((depth>>12)-1)<<21 \| 0x100` and `((depth>>12)-1)<<10`) | **Same two registers, same bug.** The driver writes the raw count `0x1000` to both; the vendor writes `0x00203C00` and `0x00003C00` — two *different* words, each with the `0x100` flag set. The driver clears that flag in both. |
| B3 — `IDM+0x70` = `depth-1` where the vendor writes `depth-0x18` | **`IDM+0x00c`.** The driver writes a linear "depth << 16" where the vendor writes a log2 *code* in bits 16-19. Same shape of mistake: a raw quantity in a field that encodes something else. It happens to land on the same field value (0) for depth 0x400, so it is latent, not present. |
| C1 — the driver sets `IDM+0x000` bit 13, which the vendor leaves clear | **The same word, and it is the TX control word.** Same bit, same direction. This is the same single line of driver code (`zx279133-idm.c:1436`) as the RX one. |
| B4 — "`IDM+0x40c` differs" | **Wrong.** `0x00003D00` is exactly the vendor's `((0x10000>>12)-1)<<10 \| 0x100`. |
| C3 — "the driver sets `IDM+0x0004`, the vendor does not" | **Wrong.** The vendor writes it at `0x18e5c`. The decimal-offset blindness hid it. |

---

## 2. Where the driver's TX bring-up is

`zx279133_idm_tx_configure()` (`zx279133-idm.c:1424-1531`) is the whole register image; it
is reached from `zx279133_idm_tx_prepare()` (`zx279133-idm.c:1542-1579`), which is reached
from `zx279133_shared_idm_prepare()` (`zx279133-netdev.c:30`). The TX descriptor base is
written by `tx_prepare` itself, one line after `tx_configure()` returns.

### 2.A — `zx279133_idm_tx_configure()`, in execution order

`idm` = `eth->base + 0x280000`; the absolute address of `idm+X` is `0x19280000 + X`.

| # | file:line | offset | absolute | value written | TX-relevant |
| --- | --- | --- | --- | --- | --- |
| 0 | `zx279133-idm.c:1430-1431` | (all of `zx279133_idm_cfg_offsets[]`) | — | **read only**: saves the pre-driver value of 40 words | save/restore set |
| 1 | `:1433-1440` | `0x0000` | `0x19280000` | RMW: `\|= 0x000f0000`; `(v & 0xf00fffff) \| 0x00f00000`; `\|= 0x00003000`; `(v & 0x8fffffff) \| (1 << 28)`; `\|= BIT(24)` only if `zx279133_idm_cfg_bit24` (default **0**) | **control word** |
| 2 | `:1442` | `0x0008` | `0x19280008` | `lower_32_bits(eth->rx_descs_dma)` | RX |
| 3 | `:1443` | `0x000c` | `0x1928000c` | `ZX279133_IDM_TX_CFG_DEPTH << 16` = `0x04000000` | **TX depth** |
| 4 | `:1444` | `0x0010` | `0x19280010` | `128` | RX |
| 5 | `:1450-1451` | `0x0014`…`0x0034` (9 words) | `0x19280014`… | `0x00010001` | RX |
| 6 | `:1452` | `0x0038` | `0x19280038` | `50000` | — |
| 7 | `:1453` | `0x0054` | `0x19280054` | `0x06060606` | — |
| 8 | `:1454` | `0x0058` | `0x19280058` | `0x00060606` | — |
| 9 | `:1455` | `0x005c` | `0x1928005c` | `0x07070707` | — |
| 10 | `:1456` | `0x0060` | `0x19280060` | `0x07070707` | — |
| 11 | `:1457` | `0x0070` | `0x19280070` | `ZX279133_IDM_RX_QUEUE_DESC_DEPTH - 1` = `0x7ff` | RX |
| 12 | `:1458` | `0x0074` | `0x19280074` | `0x00000210` | — |
| 13 | `:1459` | `0x0090` | `0x19280090` | `20` | — |
| 14 | `:1460` | `0x0094` | `0x19280094` | `1` | — |
| 15 | `:1461` | `0x00c0` | `0x192800c0` | `ZX279133_IDM_DESC_MODE` = `0` | — |
| 16 | `:1475` | `0x0490` | `0x19280490` | `0` | — |
| 17 | `:1489` | `0x02c8` | `0x192802c8` | `(readl(idm+0x2c8) & 0xc0ffffff) \| 0x12000000` | — |
| 18 | `:1490` | `0x0124` | `0x19280124` | `0x00000080` | — |
| 19 | `:1491` | `0x03fc` | `0x192803fc` | `0x00000f49` | — |
| 20 | `:1492` | `0x05c0` | `0x192805c0` | `7` | — |
| 21 | `:1500` | `0x0040` | `0x19280040` | `0x07ffffff` | INT mask |
| 22 | `:1513` | `0x0044` | `0x19280044` | `0x071ffbfb` | IRQ routing |
| 23 | `:1514` | `0x0048` | `0x19280048` | `0x00200000` | IRQ routing |
| 24 | `:1515` | `0x004c` | `0x1928004c` | `0x00400000` | IRQ routing |
| 25 | `:1516` | `0x0050` | `0x19280050` | `0x00800404` | IRQ routing |
| 26 | `:1517` | `0x006c` | `0x1928006c` | `0x000007c1` | — |
| 27 | `:1519` | `0x0104` | `0x19280104` | `lower_32_bits(eth->rx_normal_bp_dma)` | RX BP base |
| 28 | `:1520-1521` | `0x0108` | `0x19280108` | `lower_32_bits(eth->idm_base + 0x1a4000)` | free ring 1 |
| 29 | `:1522-1523` | `0x0118` | `0x19280118` | `lower_32_bits(eth->idm_base + 0x1a8000)` | free ring 2 |
| 30 | `:1524-1525` | `0x011c` | `0x1928011c` | `lower_32_bits(eth->idm_base + 0x1ec000)` | free ring 3 |
| 31 | `:1526-1527` | `0x0408` | `0x19280408` | `lower_32_bits(eth->idm_base + 0x22c000)` | free ring 4 |
| 32 | `:1528` | `0x010c` | `0x1928010c` | `ZX279133_IDM_BP_RING_SIZE` = `0x1000` | BP ring depth/flag |
| 33 | `:1529` | `0x0110` | `0x19280110` | `ZX279133_IDM_BP_RING_SIZE` = `0x1000` | BP ring depth/flag |
| 34 | `:1530` | `0x040c` | `0x1928040c` | `0x00003d00` | free-ring count/flag |

`eth->idm_base` is the **physical** base of the IDM reserved-memory node
(`zx279133-main.c:359`, `of_reserved_mem_lookup(idm_np)`), so rows 28-31 are physical
addresses, not offsets.

### 2.B — the rest of the driver's TX path (outside `tx_configure()`)

| # | file:line | offset | value | note |
| --- | --- | --- | --- | --- |
| 35 | `zx279133-idm.c:1554` | — | `memset(eth->tx_descs, 0, size)` | 4 rings × 1024 descriptors × 32 B |
| 36 | `:1556-1557` | `0x0004` | **read**, saved in `eth->idm_tx_base_saved` | |
| 37 | `:1561-1563` | `0x00ac`, `0x00b0` | **read** `& 0xffff` | TX done counters of IDM TX queues 1 and 2 |
| 38 | `:1574-1575` | `0x0004` | `lower_32_bits(eth->tx_descs_dma)` | **the TX descriptor ring base** |
| 39 | `zx279133-idm.c:1583-1584` | `0x0004` | `eth->idm_tx_base_saved` | on `tx_deactivate()` |
| 40 | `zx279133-idm.c:1538-1539` | all of `zx279133_idm_cfg_offsets[]` | the saved pre-driver values | on `tx_deconfigure()` — **restores 30 RX-side words too** (`re-idm-rx.md` §3.C C7) |
| 41 | `zx279133-idm.c:1216-1218` | `0x00a0`, `0x00a4` | `count << 17` | **TX doorbells**, queues 1 and 2, at runtime |
| 42 | `zx279133-idm.c:1237-1238` | `0x00ac`, `0x00b0` | **read** `& 0xffff` | TX done counters, at runtime |

Queue mapping (all from `zx279133.h`): `ZX279133_IDM_CPU_TX_FIRST = 1` (`:24`),
`ZX279133_CPU_TX_QUEUES = 2` (`zx279133-lan.h:11`),
`zx279133_idm_tx_doorbell_reg(q) = q == 0 ? 0x0080 : 4*(q+39)` (`:471-474`),
`zx279133_idm_tx_done_reg(q) = q == 0 ? 0x0084 : 4*(q+42)` (`:476-479`).

So the driver's two netdev TX queues are:

| netdev queue | IDM TX queue | descriptor ring | doorbell | done counter |
| --- | --- | --- | --- | --- |
| 0 | 1 | `tx_descs[1024…2047]` (`zx279133-netdev.c:388-389`) | `0x00a0` | `0x00ac` |
| 1 | 2 | `tx_descs[2048…3071]` | `0x00a4` | `0x00b0` |

Both rings are inside the 4-ring allocation
(`zx279133-main.c:391-392`, `ZX279133_IDM_TX_QUEUES 4` × `ZX279133_IDM_TX_DEPTH 1024`),
and neither the doorbell nor the done register of any driver queue collides with the
other's.

---

## 3. The vendor's TX bring-up, reconstructed

### 3.A — the call chain

```
init_module                     .init.text 0x8ac
 └─ plat_initModule             .init.text 0x000
     └─ nppt_init               0xc8d4
         └─ idm_init            0x194e4
             ├─ idm_qmg_desc_rd_mode_cfg  0x194cc  → idm+0x490
             ├─ idm_rx_bp_init            0x18e68  → idm+0x104/0x108/0x10c/0x110
             ├─ idm_tx_bp_init            0x190e0  → (no IDM write; software table only)
             ├─ idm_rev_bp_init           0x19244  → idm+0x10c/0x110/0x118/0x11c/0x408/0x40c
             ├─ idm_rx_desc_init          0x18c4c  → idm+0x000/0x008/0x070
             ├─ idm_tx_desc_init          0x18d54  → idm+0x004/0x00c
             ├─ idm_queue_rx_int_num_thread(7,1) 0x1938c → idm+0x014 (upper half)
             ├─ idm_int_mask_configure_init 0x13e88 → idm+0x044/0x048/0x04c/0x050
             └─ idm_net_init              0x18900  → idm+0x040
```

`idm_tx_bp_init` is worth a line of its own: it is 0x164 bytes of pointer arithmetic and
stores **only into `.bss`** (`bss+0x6fa0+0x810…0x830` and a buffer table at
`bss+0x6fa0+0x830`). **It writes no IDM register.** The TX buffer pool is therefore not
programmed through a TX-specific register at all — the TX side reuses the shared
`0x10c/0x110/0x118/0x11c/0x408/0x40c` words that `idm_rx_bp_init` and `idm_rev_bp_init`
write.

### 3.B — every vendor IDM write in `idm_init` and its callees, in order

| # | vendor instruction | offset | absolute | value | TX-relevant |
| --- | --- | --- | --- | --- | --- |
| V1 | `idm_init` `0x195ec-0x195f4` | `0x0000` | `0x19280000` | RMW `\| 0x000f0000` | **control** |
| V2 | `idm_init` `0x195f8-0x19604` | `0x0000` | | RMW `(v & 0xf00fffff) \| 0x01f00000` ⇒ bits 20…24 = 1 | **control** |
| V3 | `idm_init` `0x19608-0x19610` | `0x0000` | | RMW `\| 0x00001000` ⇒ bit 12 = 1 | **control** |
| V4 | `idm_init` `0x19614-0x19618` | `0x0054` | `0x19280054` | `0x06060606` | — |
| V5 | `idm_init` `0x1961c-0x19624` | `0x0058` | `0x19280058` | `0x06060606` | — |
| V6 | `idm_init` `0x19628-0x1962c` | `0x005c` | `0x1928005c` | `0x07070707` | — |
| V7 | `idm_init` `0x19630` | `0x0060` | `0x19280060` | `0x07070707` | — |
| V8 | `idm_init` `0x19634` | `0x05c0` | `0x192805c0` | `7` (`w21 = #7` at `0x195c8`) | — |
| V9 | `idm_qmg_desc_rd_mode_cfg` `0x194d8` | `0x0490` | `0x19280490` | `0` | — |
| V10 | `idm_init` `0x19640-0x19648` | `0x03fc` | `0x192803fc` | `0x00000f49` | — |
| V11 | `idm_init` `0x1964c` | `0x00bc` | `0x192800bc` | `0` | **TX doorbell of TX queue 8** |
| V12 | `idm_init` `0x19650-0x19660` | `0x02c8` | `0x192802c8` | RMW `(v & 0xc0ffffff) \| 0x12000000` | — |
| V13 | `idm_init` `0x19664-0x19668` | `0x0124` | `0x19280124` | `0x00000080` | — |
| V14 | `idm_rx_bp_init` `0x18f40-0x18f50` | `0x010c` | `0x1928010c` | `((idm_rx_normal_bp_ring_depth >> 12) - 1) << 21 \| 0x100` = **`0x00200100`** | BP ring |
| V15 | `idm_rx_bp_init` `0x18f54-0x18f68` | `0x0110` | `0x19280110` | `((idm_rx_jumbo_bp_ring_depth >> 12) - 1) << 21 \| 0x100` = **`0x00000100`** | BP ring |
| V16 | `idm_rx_bp_init` `0x18f70` | `0x0104` | `0x19280104` | RX normal BP base, `w6` = runtime physical address | BP base |
| V17 | `idm_rx_bp_init` `0x18f78` | `0x0108` | `0x19280108` | RX jumbo BP base, `w12` = runtime physical address | BP base |
| V18 | `idm_rev_bp_init` `0x19350-0x1935c` | `0x010c` | `0x1928010c` | RMW `(v & 0xffe003ff) \| ((idm_free_ring_depth>>12 - 1) << 10)` ⇒ **`0x00203C00`** | BP ring |
| V19 | `idm_rev_bp_init` `0x19360-0x19370` | `0x0110` | `0x19280110` | RMW `(v & 0xffe003ff) \| ((idm_free_ring_depth>>12 - 1) << 10)` ⇒ **`0x00003C00`** | BP ring |
| V20 | `idm_rev_bp_init` `0x1936c-0x19374` | `0x040c` | `0x1928040c` | `((idm_free_ring_depth>>12 - 1) << 10) \| 0x100` = **`0x00003D00`** | free-ring count |
| V21 | `idm_rev_bp_init` `0x19378` | `0x0118` | `0x19280118` | free-ring base, `w5` = runtime physical address | free ring |
| V22 | `idm_rev_bp_init` `0x1937c` | `0x011c` | `0x1928011c` | free-ring base, `w8` = runtime physical address | free ring |
| V23 | `idm_rev_bp_init` `0x19380` | `0x0408` | `0x19280408` | free-ring base, `w4` = runtime physical address | free ring |
| V24 | `idm_init` `0x19688-0x1968c` | `0x0010` | `0x19280010` | `g_idm_rx_bp_retev_num_th` = `0x40` (DT `bp_retev_num_th`) | RX |
| V25 | `idm_init` `0x19694-0x19698` | `0x00c0` | `0x192800c0` | `g_idm_desc_mode` = `0` (DT) | — |
| V26 | `idm_rx_desc_init` `0x18d30-0x18d3c` | `0x0000` | `0x19280000` | RMW `(v & 0x8fffffff) \| (rx_depth_code << 28)`; `rx_depth_code = 0` for `idm_desc_rx_depth = 0x400` ⇒ bits 31:28 = **0** | **control** |
| V27 | `idm_rx_desc_init` `0x18d24`,`0x18d44` | `0x0070` | `0x19280070` | `idm_desc_rx_depth - 0x18` = **`0x000003E8`** | RX |
| V28 | `idm_rx_desc_init` `0x18d40`,`0x18d48` | `0x0008` | `0x19280008` | RX descriptor base = `idm_ddr_phy_start` (= `0x80b23000`, docs/11 line 21) | RX |
| V29 | `idm_tx_desc_init` `0x18e48-0x18e54` | `0x000c` | `0x1928000c` | RMW `(v & 0xfff8ffff) \| (tx_depth_code << 16)`; `tx_depth_code = 0` for `idm_desc_tx_depth = 0x400` ⇒ bits 16:19 = **0**, rest of the word preserved | **TX depth** |
| V30 | `idm_tx_desc_init` `0x18e58-0x18e5c` | `0x0004` | `0x19280004` | **TX descriptor base** = `idm_ddr_phy_start + g_idm_ring_rx_desc_size` (+ per-queue `i × tx_depth × desc_len` in the software table) = `0x80b23000 + 0xC0000` = **`0x80BC3000`** | **TX ring base** |
| V31 | `idm_queue_rx_int_num_thread` `0x193f0-0x1940c` | `0x0014` | `0x19280014` | `(v & 0xffff) \| (1 << 16)` — the **upper** half only | RX |
| V32 | `idm_int_mask_configure_init` `0x13f5c-0x13f7c` | `0x0044`,`0x0048`,`0x004c`,`0x0050` | | four words from `.bss+0x6fa0+0x758/0x75c/0x760/0x764` (runtime; the vendor's own dmesg prints them as `0x71ffbfb 0x200000 0x400000 0x800404`) | IRQ routing |
| V33 | `idm_net_init` `0x18928` | `0x0040` | `0x19280040` | `0x07ffffff` | INT mask |

The runtime values in V14-V20 and V27-V30 are computable because the stock DT supplies
the globals:

| global | DT property | value |
| --- | --- | --- |
| `g_idm_desc_rx_depth` | `idm_desc_rx_depth` | `0x400` |
| `g_idm_desc_tx_depth` | `idm_desc_tx_depth` | `0x400` |
| `g_idm_rx_normal_bp_ring_depth` | `idm_rx_normal_bp_ring_depth` | `0x2000` |
| `g_idm_rx_jumbo_bp_ring_depth` | `idm_rx_jumbo_bp_ring_depth` | `0x1000` |
| `g_idm_free_ring_depth` | `idm_free_ring_depth` | `0x10000` |
| `g_idm_tx_queue_num` | `idm_tx_queue_num` | `4` |
| `g_idm_rx_queue_num` | `idm_rx_queue_num` | `0x18` |
| `g_idm_desc_len` | (fixed) | `0x20` |

(`devtools/uboot-stock.dts.txt:1651-1663`; the same values are in
`ci-wanport/decompiled/sk-d840n-stock.dts:1843-1855`.)

The depth-code tables in `idm_rx_desc_init` (`0x18ccc-0x18d18`) and `idm_tx_desc_init`
(`0x18de8-0x18e34`) are the same: `0x400→0, 0x800→1, 0x1000→2, 0x2000→3, 0x4000→4,
0x8000→5, 0x10000→6`, i.e. `log2(depth) - 10`, and **0** for every other depth.

### 3.C — the vendor's TX datapath at runtime (not bring-up, but the register map the driver must match)

| what | vendor | driver |
| --- | --- | --- |
| TX doorbell value | `mov w5, #0x20000` → `str w5, [idm, #0x80]` (`idm_net_lan_tx_direct` `0x132c0-0x132cc`) and `str w5, [idm, #0xa4]` (`0x11dcc-0x11dd8`) — a **literal `0x00020000` = 1 frame, count in bits 23:17** | `count << 17` (`zx279133-idm.c:1216-1218`) — same encoding |
| TX doorbell registers used | `0x0080` (q0), `0x00a0` (q1), `0x00a4` (q2), `0x00a8` (q3) | `0x00a0`, `0x00a4` (queues 1, 2) |
| TX done counters | `ldr w25, [idm, #0x84]` + `and #0xffff` (`0x13328-0x1332c`, queue 0) and `ldr w25, [idm, #0xb0]` + `and #0xffff` (`0x11e34-0x11e38`, the queue whose doorbell is `0xa4`) | `readl(idm + done_reg) & 0xffff` — **same registers, same mask** |
| TX queue range | the module's own debug prints say it: `idm_tx_ring_manage_info_show txq is out of 0 - 3` and `idm_tx_rev_ring_manage_info_show txq is out of 0 - 3` (`.rodata.str1.1`), and the DT property `idm_tx_queue_num = 0x4` | the driver uses TX queues **1 and 2** only |
| ring regions | the DT property names the driver looks up are `sg_idm_desc_ring_rx`, `sg_idm_bp_ring_rx_normal`, `sg_idm_bp_ring_rx_jumbo`, `sg_idm_desc_ring_tx`, `sg_idm_bp_ring_free` — five reserved-memory regions, i.e. the vendor's IDM memory layout is declared by the DT, not computed | the driver supplies its own coherent RX normal BP table and TX descriptor array, and points the free rings at its own reserved memory |
| descriptor geometry | 4 TX queues × `idm_desc_tx_depth` (0x400) × `g_idm_desc_len` (0x20) = 0x8000 per queue, contiguous, base at `idm_ddr_phy_start + 0xC0000` | 4 rings × `ZX279133_IDM_TX_DEPTH` (1024) × 32 B = 0x8000 per ring, contiguous, base = `tx_descs_dma`; rings 1 and 2 used |

The doorbell/done pair observed in the vendor (`0xa4` ↔ `0xb0`) is exactly the driver's
formula pair for IDM TX queue 2 (`4*(2+39) = 0xa4`, `4*(2+42) = 0xb0`), and `0x80` ↔ `0x84`
is the formula pair for queue 0. **The driver's register map is the vendor's.**

---

## 4. The difference list

### 4.A — the vendor sets it, the driver does not

| # | register | vendor (evidence) | driver | class | TX rank |
| --- | --- | --- | --- | --- | --- |
| A1 | `IDM+0x000` **bit 24** | `idm_init` `0x19600-0x19604`: `(v & 0xf00fffff) \| 0x01f00000` ⇒ bits 20…24 = 1. `idm_rx_desc_init`'s `& 0x8fffffff` (`0x18d34`) clears bits 30, 29, 28 and **keeps bit 24** (0x8fffffff = `1000 1111 …`). Proven image `0x01ff0007` has bit 24 set. | `(v & 0xf00fffff) \| 0x00f00000` ⇒ bits 20…23 only; bit 24 only if `zx279133_idm_cfg_bit24` — **default 0** (`zx279133.h:48`, `zx279133-idm.c:1435-1439`) | value differs | **1** |
| A2 | `IDM+0x00bc` — TX doorbell of TX queue 8 | `idm_init` `0x1964c`: `str wzr, [x0, #0xbc]` — a bring-up zero | never written; `0x00bc` is not in `zx279133_idm_cfg_offsets[]` and is not the doorbell of any driver queue | missing | 4 |

### 4.B — the value differs

| # | register | vendor (evidence) | driver | class | TX rank |
| --- | --- | --- | --- | --- | --- |
| B1 | `IDM+0x000` **bit 13** | `idm_init` `0x1960c`: `orr w1, w1, #0x1000` — bit 12 only, bit 13 left at 0. Proven image `0x01ff0007` has bits 12 and 13 clear. | `value \|= 0x00003000` (`zx279133-idm.c:1436`) — bits 12 **and** 13 | driver sets a bit nobody else sets | **2** |
| B2 | `IDM+0x010c` | `idm_rx_bp_init` `0x18f40-0x18f50` ⇒ `((0x2000>>12)-1)<<21 \| 0x100` = `0x00200100`; then `idm_rev_bp_init` `0x19350-0x1935c` RMW `(v & 0xffe003ff) \| (15<<10)` ⇒ **`0x00203C00`** | `writel(ZX279133_IDM_BP_RING_SIZE, idm + 0x010c)` = `0x1000` (`zx279133-idm.c:1528`) | wrong encoding: depth code 1 instead of 15, bit 8 (`0x100`, the DT's `red_idm_in_share_max`) cleared, bits 22-31 zeroed | 3 |
| B3 | `IDM+0x0110` | `idm_rx_bp_init` `0x18f54-0x18f68` ⇒ `((0x1000>>12)-1)<<21 \| 0x100` = `0x00000100`; then `idm_rev_bp_init` `0x19360-0x19370` ⇒ **`0x00003C00`** | `writel(ZX279133_IDM_BP_RING_SIZE, idm + 0x0110)` = `0x1000` (`zx279133-idm.c:1529`) — **the same value as `0x010c`, where the vendor writes two different words** | wrong encoding, plus an asymmetry the driver does not have | 3 |
| B4 | `IDM+0x000` **bits 31:28** | `idm_rx_desc_init` `0x18d30-0x18d3c`: `(v & 0x8fffffff) \| (rx_code << 28)` with `rx_code = 0` for `idm_desc_rx_depth = 0x400` ⇒ bits 31:28 = **0**. Proven image `0x01ff0007` agrees. | `(v & 0x8fffffff) \| (ZX279133_IDM_RX_CFG_DEPTH << 28)` with `ZX279133_IDM_RX_CFG_DEPTH = 1` (`zx279133.h:462`) ⇒ bits 31:28 = **1** | value differs; RX-depth code, so it is the RX mirror of B3 | 5 |
| B5 | `IDM+0x0010` | `g_idm_rx_bp_retev_num_th` = `0x40` (DT `bp_retev_num_th = 0x40`, `uboot-stock.dts.txt:1632`) — read from `.bss` at `0x19688` | `writel(128, idm + 0x0010)` = `0x80` (`zx279133-idm.c:1444`) | value differs; RX threshold | 6 |
| B6 | `IDM+0x0070` | `idm_rx_desc_init` `0x18d24` `sub w1, w1, #0x18` ⇒ `0x400 - 0x18` = `0x000003E8` | `ZX279133_IDM_RX_QUEUE_DESC_DEPTH - 1` = `0x7ff` (`zx279133-idm.c:1457`) | value differs; RX depth | 6 |

### 4.C — the driver sets what the vendor does not

| # | register | vendor | driver | class | TX rank |
| --- | --- | --- | --- | --- | --- |
| C1 | `IDM+0x0038`, `0x0074`, `0x0090`, `0x0094`, `0x006c`, `0x0004` (the *depth* part), `0x05c0` | `np_133.ko` writes none of these except `0x05c0` (= 7, `0x19634`) and `0x0004` (the TX base). `0x0038`, `0x0074`, `0x0090`, `0x0094`, `0x006c` are never written by the vendor, so they keep their reset value. | `50000`, `0x210`, `20`, `1`, `0x7c1` (`zx279133-idm.c:1452`, `:1458`, `:1459`, `:1460`, `:1517`) — the `plat_132.ko` image | driver sets extra, all RX-side or non-TX | 7 |
| C2 | `IDM+0x0108` | `idm_rx_bp_init` `0x18f78` writes the **RX jumbo BP base** | `eth->idm_base + 0x1a4000` (free ring 1) (`zx279133-idm.c:1520-1521`) | role differs; the driver has no jumbo RX, so this is a deliberate substitution | 7 |
| C3 | `IDM+0x00c`, write style | `idm_tx_desc_init` RMWs only bits 16-19 and preserves the rest of the word | the driver replaces the whole word with `0x04000000` | latent; today the two agree because U-Boot left `0x04000000` and the code is 0 | 7 |
| C4 | the whole `IDM+0x000` write is a whole-word replace | the vendor's RMW chain preserves bits 31-28 and 19-0 at each step (`& 0xf00fffff` clears only 27-20, `& 0x8fffffff` clears only 30-28) | the driver also replaces bits the vendor preserves, but ends up with the same three bit differences as B1/A1/B4 | latent | 7 |

### 4.D — things that **match** (recorded so they are not re-litigated)

* **`IDM+0x004`, the TX descriptor base.** Vendor `idm_tx_desc_init` `0x18e58-0x18e5c`
  writes `idm_ddr_phy_start + g_idm_ring_rx_desc_size`; the driver writes
  `lower_32_bits(eth->tx_descs_dma)` (`zx279133-idm.c:1574-1575`). **Both program it, with a
  physical address, at the same point in the sequence** (the vendor inside `idm_init`, the
  driver right after `tx_configure()`). **Match in role.** The only difference is *which*
  memory: the vendor's rings live in the NP's DDR pool at `0x80b23000`, the driver's in a
  `dmam_alloc_coherent` block — which is the same address-space question docs/08-11 already
  settled for RX, and it is the same question for TX.
* **`IDM+0x00c`, final value.** Vendor bits 16:19 = 0 (depth 0x400) and everything else
  preserved; U-Boot left `0x04000000`; the driver writes `0x04000000`. **Final value
  `0x04000000` either way.**
* **`IDM+0x040c`.** Vendor `0x00003D00` (`0x1936c-0x19374`), driver `0x00003d00`
  (`zx279133-idm.c:1530`). **Exact match.**
* **`IDM+0x0490`.** Vendor `0` (`idm_qmg_desc_rd_mode_cfg` `0x194d8`), driver `0`
  (`zx279133-idm.c:1475`). **Match.**
* **`IDM+0x0124`.** Vendor `0x80` (`0x19664-0x19668`), driver `0x00000080`
  (`zx279133-idm.c:1490`). **Match** (the proven image's `0x40` is U-Boot's value, from
  the DT's `bp_retev_num_th`; the vendor Linux hard-codes `0x80`, and the driver follows
  the vendor).
* **`IDM+0x040`.** Vendor `0x07ffffff` (`idm_net_init` `0x18928`), driver `0x07ffffff`
  (`zx279133-idm.c:1500`). **Match.**
* **`IDM+0x044`…`0x050`.** Vendor's four words (`idm_int_mask_configure_init`
  `0x13f68-0x13f7c`), driver `0x071ffbfb / 0x00200000 / 0x00400000 / 0x00800404`
  (`zx279133-idm.c:1513-1516`). **Match** (the vendor's dmesg prints the same four).
* **`IDM+0x05c0`.** Vendor `7` (`0x19634`), driver `7` (`zx279133-idm.c:1492`). **Match** —
  and note that the driver's comment calls `0x5c0 = 7` "the CPU133-only" value
  (`zx279133-idm.c:1397`), which is wrong: `np_133.ko` writes 7 there too.
* **`IDM+0x0014`…`0x0034`.** Vendor writes one field of `0x0014` only (upper half = 1,
  `0x193f0-0x1940c`) and leaves the other eight words alone; the driver now writes
  `0x00010001` to all nine (`zx279133-idm.c:1450-1451`), i.e. exactly the reset default
  the vendor relies on. **Match on the final value.**
* **TX doorbell encoding.** Vendor literal `0x00020000` (= 1 << 17); driver `count << 17`.
  **Match.**
* **TX done-counter registers and mask.** Vendor reads `0x0084`/`0x00b0` and masks `0xffff`;
  driver reads `0x00ac`/`0x00b0` and masks `0xffff`. **Match in form**; the driver reads
  `0x00ac` (TX queue 1) where the vendor reads `0x0084` (TX queue 0) because the driver
  starts at queue 1 — see §6.4.
* **TX descriptor geometry.** 4 queues × 1024 descriptors × 32 bytes, contiguous, base in
  `IDM+0x004`. **Match.**

---

## 5. The five named suspicions, answered

**(a) `IDM+0x10c` and `IDM+0x110`.** **The vendor does encode a depth field and a `0x100`
flag, and the driver does not — but the vendor's encoding is not the one the task assumed.**
The vendor's `idm_rx_bp_init` writes `((ring_depth >> 12) - 1) << 21 | 0x100` into
`0x10c`/`0x110` (`0x18f48`, `0x18f60` — note `lsl #0x15`, i.e. **<< 21**, not << 10), and
`idm_rev_bp_init` then overwrites bits 10-21 of both with
`((idm_free_ring_depth >> 12) - 1) << 10` while preserving bits 0-9 and 22-31
(`0x19350-0x19370`, mask `0xffe003ff`). With the stock DT the final values are
**`0x00203C00`** and **`0x00003C00`**. The driver writes the raw count `0x1000` to both
(`zx279133-idm.c:1528-1529`), which (i) puts `1` in the free-ring depth field where the
vendor has `15`, (ii) zeroes the `0x100` flag the vendor sets in both words, and (iii) makes
the two words identical where the vendor's are not. The `0x100` flag is the DT's
`red_idm_in_share_max` (`uboot-stock.dts.txt:1645`).

**(b) `IDM+0x70`.** **Confirmed as stated.** `idm_rx_desc_init` does
`sub w1, w1, #0x18` (`0x18d24`) and stores it (`0x18d44`) ⇒ `idm_desc_rx_depth - 0x18` =
`0x400 - 0x18` = `0x3E8`. The driver stores `ZX279133_IDM_RX_QUEUE_DESC_DEPTH - 1` = `0x7ff`
(`zx279133-idm.c:1457`). This is RX-only and is the RX report's B3, unchanged.

**(c) `IDM+0x14`…`0x34`.** **The premise no longer holds: the driver no longer writes
`0x00800080`.** `zx279133-idm.c:1450-1451` now writes `0x00010001` to all nine words, which
is the reset default the vendor leaves them at — the `re-idm-rx.md` B1 row has been fixed in
the tree since that report was written. The vendor's only write in that range is
`idm_queue_rx_int_num_thread(7, 1)` (`0x193f0-0x1940c`), which sets the **upper** half of
`0x0014` to 1 and preserves the lower half — i.e. it leaves `0x00010001` unchanged. **The
driver and the vendor now agree on all nine words.**

**(d) Does the driver program a TX descriptor ring base? Does the vendor?** **Both do, and
the RX report's contrary claim is a tool artefact.** The driver:
`writel(lower_32_bits(eth->tx_descs_dma), eth->base + ZX279133_IDM_BASE + ZX279133_IDM_TX_BASE)`
at `zx279133-idm.c:1574-1575`, i.e. `IDM+0x004 = <the DMA address of a 4-ring × 1024-descriptor
array>`. The vendor: `idm_tx_desc_init` `0x18e58-0x18e5c` does
`ldr w0, [x1, #0x560]` (the first TX queue's base address from the table it just built) and
`str w0, [x2, #4]` where `x2 = nppt_base + 0x280000` — i.e. **`IDM+0x004` = the physical
address of the first TX descriptor ring, `idm_ddr_phy_start + g_idm_ring_rx_desc_size`
(=`0x80b23000 + 0xC0000` = `0x80BC3000`)**. Neither side leaves a placeholder or a zero:
both write a real address, both write it once, and both use it as the base of a
4-queue contiguous TX ring array.

**(e) Does either side enable TX?** **There is no TX-enable bit in the IDM's TX
configuration, in the vendor's code or in the driver's.** The vendor's `idm_init` contains
no enable write of any kind; the IDM's TX path is armed by (i) pointing `IDM+0x004` at the
ring, (ii) the depth code in `IDM+0x00c`, and (iii) the runtime per-queue doorbell
(`0x0080`/`0x00a0`/`0x00a4`/`0x00a8`). The driver does all three. The closest thing to an
"enable" in the shared control word is `IDM+0x000` bit 12, which **both** set (vendor
`0x1960c`, driver `zx279133-idm.c:1436`) — and bit 13, which only the driver sets. The
XMAC side is a different story and already matches: the vendor's `xmac_tx_enable`
(`0x1a08c-0x1a0a4`) sets bit 0 of `XMAC0+0x00`, and the driver's `zx279133-switch.c:630-642`
writes the same `0x00010000` + speed-code image the vendor's `xamc_init_conf_by_speed`
(`0x1a200`) writes.

---

## 6. Ranked list of things to try, with the evidence for each

### 6.1 Set `IDM+0x000` bit 24
*Evidence.* The vendor's `idm_init` `0x19600-0x19604` sets bits 20…24
(`| 0x01f00000`), and the mask it uses afterwards, `& 0x8fffffff` at `0x18d34`, keeps
bit 24 (it clears bits 30, 29, 28 and keeps bit 31). The traffic-proven image holds
`0x01ff0007`, whose bit 24 is set, and bits 20-23 are set there too. The driver writes
`| 0x00f00000` (`zx279133-idm.c:1435`) and only adds bit 24 behind
`zx279133_idm_cfg_bit24`, which is `0` (`zx279133.h:48`).
*How to test.* Change `0x00f00000` to `0x01f00000` (or set the module parameter to 1) and
re-read `idm+0x000` before and after, then send a frame and watch `xmac_tx_frames`.
This is one constant.

### 6.2 Clear `IDM+0x000` bit 13
*Evidence.* The vendor sets bit 12 only (`orr #0x1000`, `0x1960c`); the driver sets
`0x3000` (`zx279133-idm.c:1436`); the proven image has both clear. The bit's meaning is
not recoverable offline — nothing in `np_133.ko` ever reads or writes bit 13 of `IDM+0x000`
except this one RMW.
*How to test.* Change `0x00003000` to `0x00001000` and re-read `idm+0x000`. If a frame
starts moving, bit 13 was diverting the TX path; if nothing changes, it is a spare bit.

### 6.3 Re-encode `IDM+0x10c` and `IDM+0x110` the way the vendor does
*Evidence.* `idm_rx_bp_init` `0x18f40-0x18f68` and `idm_rev_bp_init` `0x19340-0x19370`
(§5a). The two words must differ: `0x00203C00` and `0x00003C00` with the stock DT.
*How to test.* Write those two values instead of `0x1000`/`0x1000` and re-read. Note that
the driver's own ring is 4096 entries deep (`ZX279133_IDM_BP_RING_SIZE`, `zx279133.h:424`)
and its RX normal BP table is `ZX279133_IDM_FREE_RING_SIZE` = 0x4000 bytes = 4096 entries
(`zx279133.h:445`), whereas the vendor's DT asks for `idm_rx_normal_bp_ring_depth = 0x2000`
= 8192 entries and `bppe_pool_size = 0x3400`. So the *geometry* also differs and the
encoding change alone may not be the whole story; see §8.1.

### 6.4 Decide whether IDM TX queue 0 should be used
*Evidence.* The vendor uses TX queues 0-3 (`idm_tx_queue_num = 4`; the module's own
`idm_tx_ring_manage_info_show txq is out of 0 - 3` string; doorbells `0x0080`, `0x00a0`,
`0x00a4`, `0x00a8` — `0x0080` is written by `idm_net_olt_tx` `0x146dc` and by
`idm_net_lan_tx_direct` `0x132cc`). The driver starts at queue 1
(`ZX279133_IDM_CPU_TX_FIRST = 1`, `zx279133.h:24`) and uses queues 1 and 2, with queue 0
left to the vendor's PON/OLT path. The descriptor layout is consistent either way (ring *q*
sits at `base + q × 0x8000` in both), so this is a **choice**, not an error — but it means
the driver's TX queue 1 reads its done counter from `0x00ac`, a register the vendor's
queue-1 path never touched in the capture.
*How to test.* Read `idm+0x84` (TX queue 0's done counter) and `idm+0xac` after a burst;
if the frames are completing, queue 1 is fine.

### 6.5 Consider writing `IDM+0x00bc = 0` at bring-up
*Evidence.* `idm_init` `0x1964c`. The register is the doorbell of TX queue 8; U-Boot also
leaves it 0 (docs/09 §2b lists `+0xbc` among the vendor bootloader's twenty writes, value
0), so on a real boot it is already 0 and this is a no-op.
*How to test.* Read `idm+0xbc` after the driver has brought the IDM up; if it is non-zero,
something else rang that doorbell.

---

## 7. Fix list

Ordered, minimal, each with its evidence and its status.

| # | action | value / offset | evidence | status |
| --- | --- | --- | --- | --- |
| F1 | Set bit 24 of the IDM control word | `IDM+0x000`: use `\| 0x01f00000` instead of `\| 0x00f00000` (`zx279133-idm.c:1435`), or set `zx279133_idm_cfg_bit24 = 1` (`zx279133.h:48`) | vendor `idm_init` `0x19600-0x19604`, preserved by `& 0x8fffffff` at `0x18d34`; proven image `0x01ff0007` | **CONFIRMED** (the vendor's bit is proven; that bit 24 is *TX*-critical is **HYPOTHESIS**) |
| F2 | Clear bit 13 of the IDM control word | `IDM+0x000`: `value \|= 0x00001000` instead of `0x00003000` (`zx279133-idm.c:1436`) | vendor `orr #0x1000` at `0x1960c`; proven image bits 12:13 = 0 | **CONFIRMED** (the vendor's bit is proven; that bit 13 hurts TX is **HYPOTHESIS**) |
| F3 | Write the vendor's two BP-ring words | `IDM+0x010c = 0x00203C00`, `IDM+0x0110 = 0x00003C00` (instead of `0x1000`/`0x1000` at `zx279133-idm.c:1528-1529`) | `idm_rx_bp_init` `0x18f40-0x18f68` + `idm_rev_bp_init` `0x19340-0x19370`, with `idm_rx_normal_bp_ring_depth = 0x2000`, `idm_rx_jumbo_bp_ring_depth = 0x1000`, `idm_free_ring_depth = 0x10000` from the stock DT | **CONFIRMED** as the vendor's value; TX-criticality **HYPOTHESIS** |
| F4 | Zero the TX-queue-8 doorbell at bring-up | `IDM+0x00bc = 0` | vendor `idm_init` `0x1964c` | **CONFIRMED** (value proven); effect **HYPOTHESIS**, and likely a no-op |
| F5 | Make the `IDM+0x00c` write a read-modify-write of bits 16-19 | `IDM+0x00c = (v & 0xfff8ffff) \| (code << 16)`, `code = log2(TX_DEPTH) - 10` (0 for 1024), instead of replacing the word with `0x400 << 16` (`zx279133-idm.c:1443`) | vendor `idm_tx_desc_init` `0x18e48-0x18e54`; the depth-code table at `0x18de8-0x18e34` | **CONFIRMED** as the vendor's mechanism; the **value is unchanged today** (both end at `0x04000000`), so this is a robustness fix, not a behaviour change |
| F6 | Leave `IDM+0x004` alone | — | vendor writes it (`0x18e5c`) and the driver writes it (`zx279133-idm.c:1574-1575`); **match** | **CONFIRMED — no change wanted** |
| F7 | Leave `IDM+0x040c` alone | — | vendor `0x00003D00` (`0x19374`), driver `0x00003d00` (`zx279133-idm.c:1530`); **match** | **CONFIRMED — no change wanted** |
| F8 | Leave `IDM+0x0070` alone *in this report* | — | `0x3E8` vs `0x7FF` is real (`0x18d24`/`0x18d44` vs `zx279133-idm.c:1457`) but it is RX-only and is `re-idm-rx.md`'s B3; changing it here would be out of scope | deferred to the RX side |

**Nothing in this list changes the TX doorbell, the TX done counters, the TX descriptor
base or the TX descriptor geometry** — those already match the vendor.

---

## 8. The mirror image of `re-idm-rx.md`, and three corrections to it

### 8.1 The mirrored pairs

1. **`IDM+0x10c` / `0x110` — encoding.** RX report B2: the driver writes the raw count
   `0x1000` where the vendor encodes a depth field plus a `0x100` flag. **TX mirror: the
   same two registers, the same mistake, and the same two words the driver writes
   identically where the vendor writes two different values.** The RX side and the TX side
   share these words; a wrong value here is a wrong value for both directions, which is why
   it is worth F3 even though the words are RX-named.
2. **`IDM+0x0070` vs `IDM+0x00c` — depth encoding.** RX report B3: the driver stores
   `depth - 1` where the vendor stores `depth - 0x18`. **TX mirror: the driver stores
   `TX_CFG_DEPTH << 16` where the vendor stores a log2 code in bits 16-19.** Both are "a raw
   quantity in a field that encodes something else". The TX one is currently harmless
   because `0x400`'s low nibble is 0 and the code for 1024 is 0 — it becomes wrong the
   moment `ZX279133_IDM_TX_DEPTH` stops being 1024.
3. **`IDM+0x000` bit 13.** RX report C1: the driver sets a bit the vendor leaves clear.
   **TX mirror: the same bit in the same word** — and this word is the one that also gates
   the TX side, so on the TX side it is arguably the more dangerous half of the pair.
4. **Provenance.** The driver inherited this whole image from `plat_132.ko`, a CPU133
   variant that is not this board (`zx279133-idm.c:1388-1391`). The TX-relevant items that
   came from it are exactly: `IDM+0x00c` (F5), `IDM+0x10c`/`0x0110` (F3), `IDM+0x000` bit 13
   (F2), and the bit-24 omission (F1). The vendor writes `0x05c0 = 7` as well, so the
   driver's comment calling that value "CPU133-only" (`zx279133-idm.c:1397`) is wrong.

### 8.2 Three corrections to `re-idm-rx.md`

* **§3.C C3 ("the driver sets `IDM+0x0004`, the vendor does not") is wrong.**
  `np_133.ko` *does* write `IDM+0x0004`, at `idm_tx_desc_init` `0x18e5c`. The RX report's
  verdict came from the decimal-offset blindness described at the top of this report: Capstone
  prints `[x2, #4]`, and the scan's regex only accepted `#(0x[0-9a-f]+)`.
* **§3.B B4 ("`IDM+0x40c` differs, `0x3d00` decodes as 15, not a ring size") is wrong.**
  `0x00003D00` is exactly the vendor's encoding,
  `((idm_free_ring_depth >> 12) - 1) << 10 | 0x100` with `idm_free_ring_depth = 0x10000`
  (`uboot-stock.dts.txt:1663`): `(16 - 1) << 10 | 0x100 = 0x3C00 | 0x100 = 0x3D00`. The
  "decodes as 15" observation is correct and is the *point* — 15 is
  `0x10000 >> 12 - 1`, the free-ring depth in 4 KiB units.
* **§3.D ("`IDM+0x000` bit 24 … Match on the final value") is wrong**, for the mask-reading
  reason in §4.A/A1: `& 0x8fffffff` preserves bit 24, so the vendor's final word has bit 24
  **set** and the driver's does not.

### 8.3 One RX finding that no longer applies

The RX report's B1 (`IDM+0x14`…`0x34` = `0x00800080`) has already been fixed in the tree:
`zx279133-idm.c:1450-1451` now writes `0x00010001`, the reset default the vendor leaves
those nine words at. The TX side has no counterpart to that item — the vendor writes none
of `IDM+0x14`…`0x34` except the upper half of `0x0014`, and the driver now matches.

---

## 9. What could **not** be determined offline

1. **What bit 12, bit 13 and bit 24 of `IDM+0x000` actually do.** Nothing in `np_133.ko`
   reads them back; the module carries no field-name table for the IDM control word
   (`idm_reg_table`, `.data` `0x7380`, 0x214 bytes, is a table of per-queue register
   *values and masks* — it names no field). **Settled by:** a ZX279133 datasheet, or an A/B
   on hardware (F1 and F2 are each one constant).
2. **Whether the IDM computes the per-queue TX ring address itself** (base + q ×
   depth × 32) or has hidden per-queue base registers that `np_133.ko` never writes. The
   vendor writes exactly one TX base (`IDM+0x004`) for four TX queues, and the driver
   assumes the same arithmetic; the assumption is consistent with both sides but is not
   proven. **Settled by:** a datasheet, or by moving `IDM+0x004` and watching which
   descriptor the hardware reads.
3. **The runtime physical addresses the vendor puts in `IDM+0x004/0x008/0x104/0x108/0x118/0x11c/0x408`.**
   They are `idm_ddr_phy_start + <offset>` with `idm_ddr_phy_start = 0x80b23000`
   (docs/11 line 21) and the offsets computed from DT sizes that are themselves computed at
   runtime (`g_idm_ring_*_size`). Only `IDM+0x004`'s offset could be computed here
   (24 RX queues × 1024 × 32 = 0xC0000). **Settled by:** a runtime dump of the `g_idm_*`
   globals, or the vendor's own `tm_debug` register dump.
4. **The four interrupt-routing words at `IDM+0x044`…`0x050`.** The vendor loads them from
   `.bss+0x6fa0+0x758/0x75c/0x760/0x764` (`idm_int_mask_configure_init` `0x13f5c-0x13f7c`),
   i.e. runtime values; the driver's `0x071ffbfb / 0x00200000 / 0x00400000 / 0x00800404`
   are the values the vendor's own `printk` shows (`sg_idm_irq_mask_conf(...)`), which is
   strong but not byte-level proof. These words route *interrupts*; the driver's TX
   completion is polled (`zx279133_idm_tx_reclaim_work`), so they are not TX-critical in
   this driver.
5. **The TX descriptor *contents*.** This report covers register writes only. The vendor's
   TX descriptor format (the `metadata[0] = 0x00400000 | port << 16` word the driver writes
   at `zx279133-netdev.c:397-398`, the `length_flags` layout, and the `metadata[4]` word)
   was not compared against `np_133.ko`'s descriptor builder. **Settled by:** the same
   kind of disassembly, applied to `idm_net_lan_tx_direct`'s descriptor writes.

---

## 10. Appendix — how the evidence was produced, and how to reproduce it

```sh
cd C:/UGit/openwrt/SK-D840N
export PYTHONIOENCODING=utf-8

# vendor symbol / disassembly (the helper this repo already has)
python staging/swspa.py pon/vendor-modules/np_133.ko syms 'idm'
python staging/swspa.py pon/vendor-modules/np_133.ko dis idm_init
python staging/swspa.py pon/vendor-modules/np_133.ko dis idm_tx_desc_init
python staging/swspa.py pon/vendor-modules/np_133.ko dis idm_tx_bp_init
python staging/swspa.py pon/vendor-modules/np_133.ko dis idm_rx_bp_init
python staging/swspa.py pon/vendor-modules/np_133.ko dis idm_rev_bp_init
python staging/swspa.py pon/vendor-modules/np_133.ko dis idm_rx_desc_init
python staging/swspa.py pon/vendor-modules/np_133.ko dis idm_net_init
python staging/swspa.py pon/vendor-modules/np_133.ko dis idm_int_mask_configure_init
python staging/swspa.py pon/vendor-modules/np_133.ko dis idm_queue_rx_int_num_thread
python staging/swspa.py pon/vendor-modules/np_133.ko dis xmac_tx_enable
python staging/swspa.py pon/vendor-modules/np_133.ko calls idm_init

# address ranges without a symbol (the vendor's statics)
python staging/ctl/swat.py pon/vendor-modules/np_133.ko 0x13f40 0x13f90
python staging/ctl/swat.py pon/vendor-modules/np_133.ko 0x132a0 0x13340
python staging/ctl/swat.py pon/vendor-modules/np_133.ko 0x11db0 0x11e50
```

The complete map of `np_133.ko`'s IDM accesses (every store and load, with the owning
function) was produced by a **local** scanner, `staging/ctl/scratch-idmsites.py`, written
for this report because `staging/npidmsites.py` misses Capstone's decimal offsets. It is
a copy of `staging/npidmsites.py` with one change — the store/load offset regex accepts
`#(-?(?:0x[0-9a-f]+|\d+))` instead of `#(-?0x[0-9a-f]+)` — plus a literal-value tracker.
It was deleted after this report was written; the repository was not modified.

Positive control for the fixed scanner: it reports `idm+0x0004 n=1 idm_tx_desc_init
@0x018e5c`, which the unmodified `staging/npidmsites.py` does not report at all.

Two pieces of evidence in this report come from the module's string table rather than from
code, and were extracted with a second local scratch script
(`staging/ctl/scratch-strings.py`, also deleted afterwards):

```sh
python staging/ctl/scratch-strings.py pon/vendor-modules/np_133.ko 'sg_idm'
python staging/ctl/scratch-strings.py pon/vendor-modules/np_133.ko 'txq is out of'
```

**Vendor symbol / address index used in this report**

| symbol | address | size |
| --- | --- | --- |
| `init_module` | `.init.text` `0x8ac` | `0x368` |
| `plat_initModule` | `.init.text` `0x000` | `0x030` |
| `nppt_init` | `0xc8d4` | `0x05c` |
| `idm_init` | `0x194e4` | `0x298` |
| `idm_qmg_desc_rd_mode_cfg` | `0x194cc` | `0x018` |
| `idm_rx_desc_init` | `0x18c4c` | `0x108` |
| `idm_tx_desc_init` | `0x18d54` | `0x114` |
| `idm_rx_bp_init` | `0x18e68` | `0x278` |
| `idm_tx_bp_init` | `0x190e0` | `0x164` |
| `idm_rev_bp_init` | `0x19244` | `0x148` |
| `idm_queue_rx_int_num_thread` | `0x1938c` | `0x08c` |
| `idm_int_mask_configure_init` | `0x13e88` | `0x114` |
| `idm_net_init` | `0x18900` | `0x308` |
| `idm_net_lan_tx_direct` | `0x1197c` | `0x628` |
| `idm_net_olt_tx` | `0x1423c` | `0x6d0` |
| `xmac_tx_enable` | `0x1a08c` | `0x020` |
| `xmac_tx_rx_enable` | `0x1a114` | `0x020` |
| `xamc_init_conf_by_speed` | `0x1a200` | `0x098` |

**Driver file:line index used in this report**

| location | what |
| --- | --- |
| `zx279133-idm.c:1206-1223` | `zx279133_idm_tx_flush_queue_locked()` — the TX doorbell write |
| `zx279133-idm.c:1233-1314` | `zx279133_idm_tx_reclaim_queue_locked()` — the TX done-counter read |
| `zx279133-idm.c:1370-1386` | `zx279133_idm_cfg_offsets[]` (save/restore set) |
| `zx279133-idm.c:1388-1422` | the comment naming `plat_132.ko` as the image's source |
| `zx279133-idm.c:1424-1531` | `zx279133_idm_tx_configure()` — the whole register image |
| `zx279133-idm.c:1433-1440` | `IDM+0x000` — the control word |
| `zx279133-idm.c:1443` | `IDM+0x00c` — the TX depth word |
| `zx279133-idm.c:1450-1451` | `IDM+0x014`…`0x034 = 0x00010001` |
| `zx279133-idm.c:1457` | `IDM+0x070 = depth - 1` |
| `zx279133-idm.c:1519-1530` | the BP / free-ring words |
| `zx279133-idm.c:1533-1540` | `zx279133_idm_tx_deconfigure()` — restores the shared block |
| `zx279133-idm.c:1542-1579` | `zx279133_idm_tx_prepare()` — the `IDM+0x004` write |
| `zx279133-idm.c:1581-1586` | `zx279133_idm_tx_deactivate()` |
| `zx279133-netdev.c:388-408` | the TX descriptor write (ring index, `metadata[0]`) |
| `zx279133-main.c:359-368` | `eth->idm_base` — the IDM reserved-memory physical base |
| `zx279133-main.c:391-392`, `:413-415` | the TX descriptor array allocation (4 × 1024 × 32 B) |
| `zx279133.h:24` | `ZX279133_IDM_CPU_TX_FIRST = 1` |
| `zx279133.h:48` | `zx279133_idm_cfg_bit24 = 0` |
| `zx279133.h:391-395` | IDM base, `IDM_TX_BASE = 0x0004`, `IDM_TX_QUEUES = 4`, `IDM_TX_DEPTH = 1024` |
| `zx279133.h:424` | `ZX279133_IDM_BP_RING_SIZE = 4096` |
| `zx279133.h:445-453` | the free-ring layout inside the IDM reserved memory |
| `zx279133.h:461-464` | `RX_QUEUE_DESC_DEPTH = 2048`, `RX_CFG_DEPTH = 1`, `DESC_MODE = 0`, `TX_CFG_DEPTH = 0x400` |
| `zx279133.h:471-479` | the doorbell and done-counter register formulas |
| `zx279133.h:539-543` | `struct zx279133_idm_desc` (32 bytes) |
| `zx279133-lan.h:11-12` | `ZX279133_CPU_TX_QUEUES = 2`, `ZX279133_CPU_RX_QUEUES = 2` |
| `devtools/uboot-stock.dts.txt:1629-1663` | the stock IDM device-tree properties |
