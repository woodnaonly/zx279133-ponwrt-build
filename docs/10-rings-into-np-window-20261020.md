# 10 - moving the rings and RX buffers into the NP window (design, 2026-10-20 12:29)

> **DO NOT IMPLEMENT - refuted the same afternoon by docs/11.** The vendor 4.19 transcript that had been
> sitting in `staging/ctl/serial.log` since 10-19 shows the working Linux path handing the NP a plain CPU
> DRAM pool (`idm_ddr_phy_start 0x80b23000, idm_ddr_size 0xee5000`), so nothing has to be allocated inside
> `0xb0000000` and the 4 MiB-vs-6.1 MiB sizing blocker in §3 does not exist. Kept for §2 (the two proofs
> that `0xb0000000` is not a `+0x30000000` alias, which still stand) and §6 (the CI drift postmortem).

Written while CI run 74 builds. Run 74 only corrects three register *constants* that the traffic-proven
bootloader disagrees with (`+0x0c`, `+0x10c/+0x110`, `+0x124`; see docs/09 §2b). If it boots with those
words right and the wire is still silent, the constants theory is spent and the remaining divergence is
the one this document is about: **where the rings and RX buffers live.**

## 1. The datum that makes this the next step

Measured 11:53-12:05 on the run-71 kit (`tools/rxproof.sh`, `tools/inboundproof.sh`):

- mainline's live IDM words are exactly what its source writes: `+0x04 = 0x9a080000`,
  `+0x08 = 0x9a100000` (plain DRAM `dma_addrs`), `+0x104/+0x108` likewise DRAM, `+0x0c = 0`,
  `+0x10c = +0x110 = 0x3d00`, `+0x124 = 0`.
- **neither direction moves a frame.** The board pinged `192.168.9.25` (8 packets, `tx_packets` 32,
  `ip neigh` FAILED) while the PC adapter's `rx_delta` stayed 0 for ~400 s after the TFTP ACK burst;
  a PC-initiated `ping -S 192.168.9.25 -n 24 192.168.9.2` answered `无法访问目标主机` (ARP went out on
  ifIndex 19, the board's `rx_packets` stayed 0). The same wire carries 11 MB of TFTP plus replies in
  the vendor bootloader, so PHY/SerDes/XMAC/cable are not in question.
- Consequence: driver counters (`xmac_tx_good_frames`, `idm_tx_done`, `idm+0xb0`) are **not** evidence
  of frames on the wire. Only `pcrxwatch` deltas and `rx_packets` are.

A fault that is silent in both directions at once is what "the NP never actually touched the lists the
CPU built" predicts, and the lists differ from the working ones in exactly one property: address space.

## 2. What "proven" means here, by measurement not by inference

`tools/npspace.sh` (2026-10-20 08:47) established `0xb0000000` is **not** the `+0x30000000` alias of CPU
DRAM: `mw 0x800280c0` is invisible at `0xb00280c0` and vice versa, and `md 0x80028000` reads `ffffffff`
while `md 0xb0028000` holds the NP's live descriptors. `tools/npwin.sh` showed the region is real memory
at least 64 MiB wide (markers written at `0xb0400000..0xb4000000` all read back), so
`zte,np-pool-size = <0x400000>` in the dts is a self-imposed limit, not a hardware one.

Therefore a `dma_alloc_coherent()` / `page_pool` address can never name a cell the NP reads, and the
only CPU path into the window is the `devm_ioremap_wc()` mapping the driver already creates
(`zx279133-main.c:384`, `eth->np_pool_mem`) and already uses for TX payload (`memcpy_toio` to
`ZX279133_NP_POOL_TX_OFF = 0x328000`).

## 3. The layout to copy, taken from the bootlader that carries traffic

| register | proven value | pool offset | size used by the vendor path |
| --- | --- | --- | --- |
| `+0x04` TX desc ring | `0xb0008000` | `+0x8000` | 0x400 entries x 0x20 = 32 KiB |
| `+0x08` RX desc ring | `0xb0028000` | `+0x28000` | `idm_desc_rx_depth = 0x400` x 0x20 = 32 KiB |
| `+0x104` RX normal buffer table | `0xb0000000` | `+0x0` | 256 entries (`+0x10c = 0x100`), buffers step by **0x900** |
| `+0x108` free ring 1 | `0xb0004000` | `+0x4000` | 256 entries (`+0x110 = 0x100`) |
| `+0x114` | `0x400` | (not written by mainline) | = vendor `idm_desc_rx_depth` |
| `+0x118/+0x11c/+0x408` | `0`, `0`, `0` | leave 0 | the working image does **not** program free rings 2/3/4 |

TX payload stays where it is (`+0x328000`, payload at buffer+0x80); RX payload is at buffer+**0x40**
and RX descriptor word1 is `0x07820000 | len` with the length **not** doubled, unlike TX's `len << 1`.

Sizing, because it is the reason not to reuse the current constants: 24 RX queues x 2048 entries x 0x20
= 1.5 MiB of ring plus 2048 x 0x940 = 4.6 MiB of buffers does **not** fit in 4 MiB, while the vendor
runs **one** RX queue whose buffers occupy ~184 KiB (`0xb00e8000..0xb0116000`). So the window question
is answered by shrinking to the vendor's shape, not by raising `np-pool-size` - and if the pool is
raised later, measure its real extent first (`mw`/`md` marching `0xb0000000 + n*0x100000` in a U-Boot
window, with `md 0xb0328000` as the positive control).

## 4. What implementing it means

1. Reserve the four ranges above out of `eth->np_pool_mem` at build/init time (no page, no dma_addr,
   just offsets), and program `+0x04/+0x08/+0x104/+0x108` with `eth->np_pool_base + offset` - the same
   arithmetic `netdev.c` already does for TX payload, which is why this is a layout change rather than
   a new API.
2. Descriptors are written and read with `writel`/`readl` on the `ioremap_wc` mapping, not as CPU
   structs. RX refill therefore becomes: build the 256-entry buffer table with `writel`, post it, and
   after the NP marks a descriptor complete, `memcpy_fromio` the frame out of `buffer + 0x40` into an
   `skb`. That is the same direction the TX path already goes in reverse, so `zx279133_idm_rx_*`
   needs the copy loop where it currently consumes a `page_pool` dma_addr.
3. `+0x118/+0x11c/+0x408` stop being programmed at all, and `+0x10c/+0x110` become 256 to match the
   table actually filled (this supersedes run 74's 4096 for these two words if the table is 256 deep -
   keep register and producer mask derived from one constant, which is why they were made to derive
   from `IDM_BP_RING_SIZE`).
4. Do not touch `+0x40c`: its proven value `0x1ffc00` is not an entry count of anything (docs/09 §2b).

## 5. Stop-loss

This is the last structural difference between mainline and a path that is known to move bytes. If it
lands and the wire is still silent, the remaining candidates are all in the switch/PPU configuration the
vendor `np_133.ko` owns (ERAM tables 0x22/0x23/0x24, `tm_port_route_mode_set`,
`tm_vlan_check_ena_set(5,6)`, `reason_map_cpu_qid(0,0,7)`, `glb_lan_up_en/port`) - docs/08 §5 and task
#42 - and the honest fallback is a vendor-4.19-based build rather than a mainline one, because none of
those tables have a measured mainline equivalent.

Related: docs/08 (descriptor format, the alias result), docs/09 (§2b: which diff rows are evidence),
tasks #43, #44, #13, #17.

## 6. The build is currently broken by header drift, and here is the exact graft

Runs 72, 73 and 74 all failed with the same 21 `error:` lines, every one in
`drivers/net/ethernet/zte/zx279133-offload.c` - a file `kernel-overlays` does **not** contain, so it
comes from cnjn's tree and is compiled against **our** `zx279133.h`. Run 74 (commit `e7375bd`) tried to
escape by pinning `KERNEL_SHA=96e5a241`; that failed identically, because 96e5a241 - and even its Oct 8
parent `ed93de4c` - already contains the new offload.c. The pin was the wrong diagnosis and is now
documented as such in the workflow itself.

Measured shape of the gap (computed against the cached tip files in `staging/tip/`, 12:25):

- Our header is 1012 lines, his is 912. Only **10** `#define`s exist in his and not in ours, and only
  **12** exist in ours and not in his (`ZX279133_NP_POOL_TX_{OFF,SIZE,SLOTS,STRIDE}`,
  `ZX279133_SOPC_{BASE,SMAC_READY,SMAC_SEND}`, `ZX279133_XMAC_BASE`,
  `ZX279133_XMAC_{EEE_CTRL,EEE_TIMER,SOPC_READY,SOPC_SEND}_OFF`).
- Our header also carries a `ZX279133_STAT_*` enum block that his does not have at all, and
  `zx279133-netdev.c` resolves **81** symbols from it. So "just use his header" would break netdev.c -
  the direction has to be graft-his-into-ours, not replace-ours-with-his.
- What `offload.c` needs that our header does not give: struct member
  `unsigned long lan_wan_vports;` in `struct zx279133_eth`; a complete
  `struct zx279133_wanid_profile` (error was "variable 'profile' has initializer but incomplete type");
  `ZX279133_WANID_PPPOE_PUSH`/`_POP`/`_DOWNSTREAM` (`1`, `2`, `BIT(4)` at his lines 297-299);
  `ZX279133_SPA_ONU_MAC_SLOTS` (`8`, his line 291); `ZX279133_XMAC0_BASE`/`_XMAC1_BASE`
  (`0x140000`/`0x180000`, his 452-453) with `ZX279133_XMAC1_EEE_CTRL`/`_EEE_TIMER` and
  `ZX279133_XMAC1_SOPC_READY`/`_SEND_ENABLE`/`_DUPLEX_MASK` (his 471-472, 501-503); and prototypes
  `zx279133_lan_vport_wan_set()`, `zx279133_wanid_get()`, `zx279133_wanid_put()`,
  `zx279133_wanid_debugfs_init()` (which needs his `#ifdef CONFIG_DEBUG_FS` / `static inline` pair, not
  just one line), `zx279133_flow_offload_reset()`.

Verification that must pass before this is pushed, because none of it can be compiled here:
1. every symbol the above list names is present exactly once in the grafted header, and `grep -c
   ZX279133_STAT_` in it stays at the current count (the enum survived);
2. for each of our five overlaid `.c` files, the set of `ZX279133_*` identifiers it references minus
   (grafted header `#define`s + its own `#define`s + the STAT enum) is empty;
3. the three functions we may define ourselves with different signatures (`zx279133_program_spa_uc_macs`,
   `zx279133_rx_mode_work`, `zx279133_shutdown`) must be checked against our `.c` definitions before a
   prototype is copied from his header - a mismatched prototype is a new compile error, which is exactly
   the failure mode this section exists to prevent.

**§6 step 3 decided it, and the answer is that the graft is not enough (measured 12:26 against
`staging/tip/`):** `zx279133_rx_mode_work` and `zx279133_shutdown` are defined in **his**
`zx279133-netdev.c`, and `zx279133_program_spa_uc_macs`, `zx279133_wanid_get`, `zx279133_wanid_put`,
`zx279133_lan_vport_wan_set`, `zx279133_wanid_debugfs_init` are defined in **his** `zx279133-np.c` - and
**none of them exist in our overlaid copies of those two files**. So adding his declarations to our
header removes the 21 compile errors and immediately replaces them with undefined-reference errors at
link time, because his non-overlaid `offload.c` calls functions our shadowed files dropped. The header
graft is therefore a trap, and the only correct repair is to re-sync `zx279133-netdev.c` (ours 2044
lines vs his 2025) and `zx279133-np.c` (ours 2255 vs his 2509) to his current revision and re-apply our
own edits on top - a port of two large files, not a patch of ten lines. Until that is done, **no CI run
can produce a kit at all**, so no board window should be spent waiting for one.
