# 09 - traffic-proven IDM image (2026-10-20 10:54) and mainline's diff against it

This is the reference column for everything RX-related from here on. Captured by `tools/idmband.sh`
from U-Boot at 10:54:28-10:55:30, **in a boot where the lane demonstrably carried traffic** (`ping
192.168.9.25` answered `host 192.168.9.25 is alive`, three times in the same slice). Read-only: 16-word
`md` chunks, because two 64-word reads in the earlier `tools/idmimage.sh` (10:45) were lost to the
console relay and a lost read must never be recorded as a zero.

## 1. The image

```
19280000: 01ff0007 b0008000 b0028000 04000000
19280010: 00000020 00010001 00010001 00010001
19280020: 00010001 00010001 00010001 00010001
19280030: 00010001 00010001 00001000 00000000
19280040: 07ffffff 07ffffff 00000000 00000000
19280050: 00000000 06060606 00060606 07070707
19280060: 07070707 00000000 00000000 00000800
19280070: 000003e8 00000000 00000000 00000000
19280080: 00020000 00000009 00000001 00000000
19280090: 00000000 00000000 00000000 00000000
192800a0..00c0: 0
192800c0..00e0: 0
192800f0: 00000000 00001000 00000000 00000000
19280100: 00000001 b0000000 b0004000 00000100
19280110: 00000100 00000400 00000000 00000000
19280120: 000003af 00000040 04000010 00000100
19280130: 000000a0 00000000 00000000 00000000
19280140: 01111111 11111111 00000000 00000000
19280180: 0000004b 00000000 00000000 0000004b
19280190: 00040047 00000004 00100010 00960096
192801a0: b0111a00 0782004a c0a80919 c0a80901     <- scratch descriptor, live frame
192801b0: 00000000 00000c00 b0328000 0f800054
192801c0: 00440000 ...   192801d0: 00000000 d8000000 00000004 b0008060
192801e0: b0328080 00040004 00000404 00080008
192801f0: b0028940 b0111a40 00000000 00009440(=00094440)
19280200: 00006000 08002000 00000000 00000000
192803c0..03e8: 0 ... 192803ec: b0111140
192803f0: 1450050a 00000000 00000000 00000f49
192805c0: 00000067 00000000 0000006d 00000001
192805d0: 00006000 00002000 0000004b 00000000
```

Buffer tables the block points at (same boot): `0xb0000000: 00800eb0 00890eb0 00920eb0 009b0eb0` —
buffer addresses stepping by **0x900**, exactly `bp_size = 0x900` from the vendor DT;
`0xb0004000: 008044b0 00bf44b0 00fe44b0 003d45b0` — stepping by 0x3f0000.

Self-consistency that makes the layout trustworthy: `+0x104` names a 256-entry table (`+0x10c = 0x100`)
of 0x900-byte buffers = 0x24000 bytes, which ends at 0xb0024000 — just below `+0x04 = 0xb0008000`
(TX ring) and `+0x08 = 0xb0028000` (RX ring). `+0x114 = 0x400` equals the vendor DT's
`idm_desc_rx_depth = 0x400`, which is a strong hint that U-Boot programs this block **from the same
DT numbers** the vendor Linux driver uses.

## 2. mainline vs this image

Source of the right column: `tests/kernelsrc/drivers_net_ethernet_zte_zx279133-idm.c:1364-1410`, `:1454`
(and the constants in `kernel-overlays/drivers/net/ethernet/zte/zx279133.h`).

| reg | proven (U-Boot) | mainline writes | note |
| --- | --- | --- | --- |
| +0x00 | `01ff0007` | RMW: `\|0x000f0000`, `&~0x00f00000\|0x00f00000`, `\|0x3000`, bits31:28=RX_CFG_DEPTH(1) | mainline sets bits 13:12 and bit 28 that the proven image leaves 0 |
| +0x04 | `b0008000` | `tx_descs_dma` (DRAM) | address space, see §3 |
| +0x08 | `b0028000` | `rx_descs_dma` (DRAM) | address space, see §3 |
| **+0x0c** | **`04000000`** | `IDM_TX_CFG_DEPTH << 16` = **0** | header says "uIDM_TX_CFG_DEPTH = 0 (no writer found)" (zx279133.h:474) - the board says otherwise |
| +0x10 | `00000020` | `128` | |
| **+0x14..+0x34** | **`00010001`** x9 | `0x00800080` | 80x on two 16-bit fields |
| +0x38 | `00001000` | `50000` (0xc350) | |
| +0x3c | `00000000` | (not written) | the vendor .ko ANDs this word with the TX ring base before storing it |
| +0x40 | `07ffffff` | `07ffffff` | match |
| +0x44 | `07ffffff` | `DIRECT_RX_MASK` | check |
| +0x48 | `00000000` | `0x00ff0000` | |
| +0x4c | `00000000` | `0` | match |
| +0x50 | `00000000` | `LOCAL_MASK` | |
| +0x54,+0x58,+0x5c,+0x60 | `06060606 00060606 07070707 07070707` | same | **all four match** |
| **+0x6c** | **`00000800`** | `0x000007c1` | |
| **+0x70** | **`000003e8`** (1000) | `IDM_RX_QUEUE_DESC_DEPTH - 1` = 2047 | and the vendor DT says `idm_desc_rx_depth = 0x400` |
| +0x74 | `00000000` | `0x00000210` | |
| +0x90,+0x94 | `0`, `0` | `20`, `1` | |
| +0xc0 | `0` | `DESC_MODE` = 0 | match |
| **+0x100** | `00000001` (0x20 in the 10:31 boot) | `BP_REFILL` count | varies with traffic - status, not config |
| +0x104 | `b0000000` | `rx_normal_bp_dma` (DRAM) | address space, §3 |
| +0x108 | `b0004000` | `idm_base + FREE_RING1` | |
| **+0x10c** | **`00000100`** (256) | **`0x00003d00`** (15616) | mainline's own table is `IDM_FREE_RING_SIZE 0x4000` B = 4096 entries. 15616 matches **no** documented number; the vendor DT has `bppe_pool_size = 0x3400`, `idm_rx_normal_buffer_num = 0x1000`, `idm_rx_normal_bp_ring_depth = 0x2000`. `3d00` vs `3400` is a transposed nibble |
| **+0x110** | **`00000100`** | **`0x00003d00`** | same |
| +0x114 | `00000400` | (not written) | = vendor `idm_desc_rx_depth` |
| +0x118,+0x11c | `0`, `0` | `idm_base + FREE_RING2/3` | proven image leaves these 0 |
| +0x120,+0x124 | `000003af`, **`00000040`** | mainline writes `0` at +0x124 | |
| +0x128,+0x12c,+0x130 | `04000010`, `00000100`, `000000a0` | (not written) | |
| +0x140,+0x144 | `01111111`, `11111111` | (not written) | |
| +0x180..+0x19c | `0000004b … 00960096` | (not written) | |
| +0x3fc | `00000f49` | `0x00000f49` | match |
| **+0x408,+0x40c** | (band not read this round) | `idm_base + FREE_RING4`, `0x3d00` | read +0x400/+0x410 before changing |
| +0x5c0 | **`00000067`** | `7` | |
| +0x5c4,+0x5cc | `0`, `00000001` | (not written) | |

## 3. What is NOT the explanation any more

- **"The NP cannot reach CPU DRAM" is not supported.** With ring bases holding plain `dma_addrs`,
  mainline's own queue completion word at `idm+0xb0` moved `0x01 -> 0x13` across exactly the 18 frames
  of a posted burst (run 68) - the hardware read and completed descriptors that live in DRAM.
  `tools/npdram.sh` (10:23 and 10:31) could not test it either way: the 10:23 attempt re-pointed the RX
  ring at a 1 KiB copy when the ring is 0x400 x 0x20 = 32 KiB, and its restore was incomplete
  (only +0x08 was written back, +0x104 was left at the DRAM table), so control E died and the negative
  is worthless. At 10:31 overwriting +0x104 with a DRAM table did **not** stop ingress on that boot -
  the NP had already cached the buffer list - so that half is inconclusive, not a refutation.
- The `0xb0000000` region is real and large: `tools/npwin.sh` wrote markers at 0xb0400000, 0xb0600000,
  0xb0800000, 0xb0c00000, 0xb1000000, 0xb1800000, 0xb2000000, 0xb4000000 and every one read its own
  value back, so dts `zte,np-pool-size = <0x400000>` is a self-imposed limit, not hardware.
  (That round also poisoned U-Boot's buffer pool - those offsets are inside it - which is why the
  10:54 image had to be taken on a later clean boot.)

## 4. Next

Fix, in this order and as one kit, the constants that are wrong against this table
(`+0x0c`, `+0x10`, `+0x14..0x34`, `+0x38`, `+0x6c`, `+0x70`, `+0x74`, `+0x90`, `+0x94`, `+0x124`,
`+0x5c0`, and above all the `+0x10c/+0x110` buffer count, sized to what the driver actually allocates
instead of 15616). Before editing, confirm that `tests/kernelsrc/...zx279133-idm.c` is byte-identical to
the file the kernel submodule actually builds - the overlay copies whole files, and copying a stale
snapshot over the tree would silently revert other fixes.

Related: `docs/08-np-address-space-20261020.md` (descriptor format + the alias result), tasks #43 and #44.
