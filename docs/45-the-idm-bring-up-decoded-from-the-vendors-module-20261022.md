# 45 - The IDM bring-up, decoded from this board's own vendor module (2026-10-22)

This is the second half of the G2 work. `docs/44` recorded where the LAN
receive path stops as far as the board could say: the frames never reach
the SoC, and everything that had been changed until then - the ERAM reason
and outport maps, the SPA window differences - was falsified on the board.

This round went after the other half: the IDM block itself. The driver
brings the IDM up with a register image that was reconstructed from
`plat_132.ko`, another product's module. This board's own `np_133.ko`
programs the same block differently, and the differences turn out to be
exactly the ones that decide whether a received frame can land anywhere.

Everything here comes from the vendor module, from the tree, and from the
vendor's own boot log. The board has not been re-measured yet - the build
that carries the change is run 123.

---

## 0. The short version

* The driver's IDM image is now **the vendor's image**: the control word
  is `0x01ff1007` (the vendor's board reads back exactly that), the two
  buffer-pool size registers are `0x203d00` and `0x3d00` (the vendor's
  board reads back exactly those), and the pool doorbell uses the vendor's
  pulse protocol instead of a raw count.
* Three structural errors in the pool programming are fixed:
  1. **`IDM+0x108` is the RX jumbo pool base**, not a free-ring pointer.
     The driver was pointing the hardware's second pool at a free ring.
  2. **The three free rings are at `IDM+0x118`, `+0x11c` and `+0x408`** -
     rings 1, 2 and 3. The driver pointed them at rings 2, 3 and 4, so
     the whole free-ring chain was shifted by one slot.
  3. **The pool-size registers carry ring depths, not a byte count.** The
     driver wrote the literal `4096`, which in the vendor's encoding means
     "no pool at all": the encoding is `((depth >> 12) - 1) << 21` for the
     pool, `((free_depth >> 12) - 1) << 10` for the free ring, and bit 8
     as the "this ring exists" flag.
* **The pool doorbell was announcing nothing.** `IDM+0x100` does not take
  a buffer count. The vendor's `idm_new_bp_update()` writes one `0x400`
  pulse per 1024 buffers and then the two remainders - the normal pool in
  bits 15:0 and the jumbo pool in bits 31:16. The hardware reads the count
  out of those fields, so writing the count itself announces no buffers at
  all, and a count that is a multiple of 1024 announces *zero* buffers.
  The driver posts 2048 buffers at start-up, which is exactly such a
  multiple.
* The per-queue RX counter is now read the vendor's way: **one word per
  queue at `IDM+0xc4+4*queue`, packets in bits 15:0**. The old code read
  two queues out of every word, so it read the wrong half for every queue
  above zero and never read the words of queues 8..15 at all.
* The ring geometry follows the vendor's: 24 RX queues of 1024
  descriptors, a normal pool of `0x2000` entries, a jumbo pool of `0x1000`
  entries and free rings of `0x10000` entries.
* The tree change is commit `e334697`; the build is run 123
  (id 39711246918).

---

## 1. How the vendor's bring-up was read

The vendor's module is a relocatable object with no symbol table on this
machine, so it was read with the tools in `staging/ctl/`:

* `staging/ctl/idmddr.py` - symbol table dump, plus the code around every
  reference to a named global, with the module's own relocations applied
  first (a `.ko` has no fixed addresses, so every `bl` is a placeholder
  until the relocations are put back).
* `staging/ctl/idmmap.py` - locates every call to `ioremap`,
  `platform_get_resource`, `of_address_to_resource` and friends.
* `staging/ctl/idmstores.py` - disassembles one function with the
  relocation names attached, which is what turns a wall of `adrp`/`ldr`
  pairs into "this load is `g_idm_rx_normal_bp_ring_depth`".

The full gap list, with the reasoning behind the ranking, is in
`staging/ctl/re-idm-init-diff.md` (a read-only sub-agent's report over the
same module). The findings that matter are reproduced here, because every
one of them is a claim about what the hardware is being told.

### 1.1 The vendor's DDR pool

The vendor's boot log prints it (`staging/ctl/boot419-window.txt`):

```
1 idm_ddr_base is 00000000000cc807, phy addr is 0x80b23000
ddr size(0xee5000), idm used(0xee5000)
```

So the IDM's DDR pool starts at physical `0x80b23000` and is `0xee5000`
bytes (15.6 MiB) long, and the IDM's rings fill it exactly. The pool comes
from the stock DT's `reserved-memory/pon_rsvmem`, which is a dynamic
reservation (`size = 0x3245000`, `alignment = 0x1000`) that the vendor's
PON driver carves out of DDR at boot; the DT gives no fixed address. The
vendor's `idm_init()` computes the pool's offset inside DDR by subtracting
`memstart_addr` and the `0x4000000000` device-bus alias from the mapped
pointer.

This matters for one reason: it says the IDM's DMA addresses are **ordinary
CPU DDR addresses**, the same address space the driver already uses. The
"NPPT + `0x280000`" register block is the IDM's own control registers; the
ring and pool memory it points at is plain DDR.

### 1.2 The vendor's stock DT sizes

From the vendor's `soc/pon_plat_rsv_mem_cfg` node:

| property | value | what it sizes |
|---|---|---|
| `idm_rx_queue_num` | `0x18` (24) | RX descriptor queues |
| `idm_desc_rx_depth` | `0x400` (1024) | descriptors per RX queue |
| `idm_desc_tx_depth` | `0x400` (1024) | descriptors per TX queue |
| `idm_tx_queue_num` | `0x04` | TX descriptor queues |
| `idm_rx_normal_bp_ring_depth` | `0x2000` (8192) | RX normal pool ring, in entries |
| `idm_rx_jumbo_bp_ring_depth` | `0x1000` (4096) | RX jumbo pool ring, in entries |
| `idm_free_ring_depth` | `0x10000` (65536) | each free ring, in entries |
| `idm_bp_width` | `0x04` | bytes per pool entry |
| `bp_retev_num_th` | `0x40` | the pool retire threshold |

With a 32-byte descriptor, 24 RX queues of 1024 descriptors occupy
`0xC0000` bytes - and that is exactly the gap between the vendor's RX
descriptor base (`0x80b23000`) and its TX descriptor base
(`0x80be3000`). The layout is not a guess; it falls out of these numbers.

---

## 2. The register-by-register gap

The vendor's own board was read with its `module_reg` debug interface
(`staging/ctl/vendor-idm-regs.txt`, 512 words). The register numbering in
that file is a hexadecimal word index, so word `0x43` is `IDM+0x10c`.

| IDM offset | vendor | driver (before) | what it is |
|---|---|---|---|
| `0x0000` | `0x01ff1007` | `0x10ff3007` | control word |
| `0x0008` | `0x80b23000` | `rx_descs_dma` | RX descriptor ring base |
| `0x000c` | `0x04000000` | `0x04000000` | TX config depth |
| `0x0010` | `0x00000040` | `0x00000080` | `g_idm_rx_bp_retev_num_th` |
| `0x0038` | `0x00001000` | `0x0000c350` | never written by the vendor |
| `0x0040` | `0x00000000` | `0x07ffffff` | interrupt mask |
| `0x006c` | `0x00000800` | `0x000007c1` | never written by the vendor |
| `0x0070` | `0x000003e8` | `0x000007ff` | RX descriptor count |
| `0x0074` | `0x00000000` | `0x00000210` | never written by the vendor |
| `0x0090` | `0x00000000` | `0x00000014` | never written by the vendor |
| `0x0094` | `0x00000000` | `0x00000001` | never written by the vendor |
| `0x00bc` | `0x00000000` | not written | TX queue-8 doorbell, cleared at bring-up |
| `0x0104` | `0x80c03000` | `rx_normal_bp_dma` | **RX normal pool base** |
| `0x0108` | `0x80c0b000` | free ring 1 | **RX jumbo pool base** |
| `0x010c` | `0x00203d00` | `0x00001000` | normal pool size, encoded |
| `0x0110` | `0x00003d00` | `0x00001000` | jumbo pool size, encoded |
| `0x0118` | `0x80c10000` | free ring 2 | **free ring 1** |
| `0x011c` | `0x80c50000` | free ring 3 | **free ring 2** |
| `0x0408` | `0x80c90000` | free ring 4 | **free ring 3** |
| `0x040c` | `0x00003d00` | `0x00003d00` | TX pool size, encoded |

### 2.1 The control word

The vendor's `idm_init()` does, in order:

```
orr  w1, w1, #0xf0000          ; bits 16..19
and  w1, w1, #0xf00fffff       ; clear bits 20..27
orr  w1, w1, #0x1f00000        ; bits 20..24
orr  w1, w1, #0x1000           ; bit 12
```

and then `idm_rx_desc_init()` does `and w4, w4, #0x8fffffff` and ORs in
`depth_code << 28`, where the depth code comes from a comparison chain:

```
0x400 -> 0    0x800 -> 1    0x1000 -> 2    0x2000 -> 3
0x4000 -> 4   0x8000 -> 5   0x10000 -> 6
```

With the stock DT's `idm_desc_rx_depth = 0x400` the code is 0, so from the
hardware's reset value of `0x00000007` the vendor's control word is
`0x01ff1007` - bit 24 set, bit 13 clear, depth code 0.

The driver's image was `0x10ff3007`: bit 24 clear (it was behind a module
parameter that defaulted to 0), bit 13 set (it ORed `0x3000` where the
vendor ORs `0x1000`), and depth code 1 (its own depth of 2048 descriptors).

### 2.2 The pool-size registers

`idm_rx_bp_init()` writes the two pool bases and then the two size
registers:

```
w4 = (bp_ring_depth >> 12) - 1
w4 = (w4 << 21) | 0x100
str w4, [idm, #0x10c]        ; and the same for #0x110 with the jumbo depth
```

`idm_rev_bp_init()` then adds the free-ring depth to both:

```
w1 = (free_ring_depth >> 12) - 1
w1 = w1 << 10
w2 = [idm, #0x10c] & 0xffe003ff     ; clear bits 10..20
w2 |= w1
str w2, [idm, #0x10c]               ; and the same for #0x110
str w1 | 0x100, [idm, #0x40c]       ; the TX pool size
```

With the stock depths - `0x2000` normal, `0x1000` jumbo, `0x10000` free -
that is `0x200000 | 0x3c00 | 0x100 = 0x203d00` and `0x3d00`, which is
what the vendor's board reads back, and `0x3d00` at `+0x40c`.

The driver wrote the literal `4096` into `0x10c` and `0x110`. Decoded the
vendor's way, `0x1000` says: no pool pages, no free-ring pages, and bit 8
clear - **no pool at all**.

### 2.3 The pool doorbell

`idm_new_bp_update()` is the only writer of `IDM+0x100`:

```
for (i = 0; i < (normal_count >> 10); i++)
        writel(0x400, idm + 0x100);
for (i = 0; i < (jumbo_count >> 10); i++)
        writel(0x4000000, idm + 0x100);
writel(((jumbo_count & 0x3ff) << 16) | (normal_count & 0x3ff), idm + 0x100);
```

The count is not written to the register; it is *encoded* into it, and the
hardware decodes it. The driver wrote the count itself, which the hardware
reads as "the number of new buffers is `count & 0x3ff`". The driver posts
2048 buffers at start-up - a multiple of 1024 - so the hardware was told
there were **zero** buffers in the pool, every time.

### 2.4 The descriptor count

`idm_rx_desc_init()` writes `IDM+0x70 = depth - 0x18` - the descriptor
count minus the 24 queue headers - and `IDM+0x08` = the RX descriptor ring
base. The driver wrote `depth - 1`, i.e. 2047 where the vendor has 1000.

---

## 3. The read side

`docs/43` §2 concluded that the vendor polls a different block's counters
from the driver. That conclusion rested on an address slip: the vendor's
per-queue counters are at `NPPT + 0x2800c4 + 4*queue` = `IDM + 0xc4 +
4*queue`, one word per queue, with the packet count in bits 15:0 and the
descriptor budget in bits 31:16. `exp57` read `regs 0x28c4 16`, which is
`NPPT+0x28c4` - the USCH block, not the IDM.

The driver's version read two queues out of every word and masked the queue
index to three bits, so it read the wrong half for every queue above zero
and never read the words of queues 8..15 at all, while
`zx279133_idm_rx_poll_order[]` polls exactly those queues.

The read side is now the vendor's: `IDM+0xc4+4*queue`, bits 15:0.

---

## 4. What went into the tree

`kernel-overlays/drivers/net/ethernet/zte/zx279133-idm.c`:

* the control word is the vendor's `idm_init()` sequence, with the depth
  code taken from the ring size;
* the pool bases, the free-ring bases and the two encoded pool-size
  registers are the vendor's;
* the pool doorbell uses the vendor's pulse protocol;
* the per-queue counter is read the vendor's way;
* the registers the vendor never writes are left at their reset value.

`kernel-overlays/drivers/net/ethernet/zte/zx279133.h`:

* `ZX279133_IDM_RX_RING_SIZE` 2048 -> 1024, `ZX279133_IDM_RX_QUEUE_DESC_DEPTH`
  2048 -> 1024, `ZX279133_IDM_RX_CFG_DEPTH` 1 -> 0;
* `ZX279133_IDM_BP_RING_SIZE` 4096 -> 8192 and a new
  `ZX279133_IDM_JUMBO_BP_RING_SIZE` 4096, so that the two pool rings have
  the depths the pool-size registers announce;
* `ZX279133_IDM_FREE_RING_SIZE` `0x4000` -> `0x40000`, with
  `ZX279133_IDM_FREE_RING_DEPTH` `0x10000`, and the free-ring offsets
  re-laid out so that the three rings the vendor uses are 0x40000 bytes
  apart.

`kernel-overlays/drivers/net/ethernet/zte/zx279133-main.c`:

* the normal and the jumbo pool ring are one allocation, because the
  hardware expects the jumbo pool immediately after the normal one.

The IDM reserved-memory region in the device tree (10 MiB at
`0x9d700000`) still holds everything the free rings need: the three free
rings now end at `0x2e0000` inside it.

---

## 5. What is still unproven

* **The board has not been re-measured.** Run 123 carries the change; the
  measurement plan is in `staging/ctl/exp66.txt`: the whole IDM register
  image twice, twelve seconds apart, with the PC pinging in between, plus
  the write-pointer area and the per-queue counter words. The three
  numbers that decide it are `IDM+0x240` (the RX write pointer),
  `IDM+0x180` (the global RX counter) and `lan-cpu0`'s receive counter.
* **If the write pointer still does not move**, the remaining candidate is
  the 9132 side: the frames have to be delivered to the CPU port at all,
  and that has never been measured directly - every 9132-side table the
  driver writes has only been compared against the vendor's, never
  observed moving.
* **The pool address itself has not been challenged.** The vendor's pool
  is at `0x80b23000`, the first 11 MiB of DDR; the driver's is at
  `0x9d700000`, near the top of the 512 MiB of DDR. Both are ordinary DDR
  and both are inside the range the vendor's own `idm_ddr_size` check
  covers, but if the IDM's memory port turns out to have a narrower
  aperture than the CPU's view of DDR, moving the pool down to the
  vendor's neighbourhood is the next experiment.
* The three free rings and the TX pool are programmed as the vendor
  programs them, but this driver never reads the free rings: it returns
  buffers through its own page pool instead. That difference is not
  exercised by anything yet.

---

## 6. Where the earlier round's assumptions were wrong

Two things this round had to correct, both of them in the tree's own
records:

* `docs/43` §2's "the vendor reads a different block's counters" - an
  address slip, corrected in §3 above.
* The idea that the driver's per-queue counter word holds two queues. It
  holds one. The driver's layout was inherited from `plat_132.ko` and was
  never checked against the module that actually runs on this board.
