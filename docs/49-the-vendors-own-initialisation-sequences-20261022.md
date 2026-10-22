# The vendor's own initialisation sequences, read out of its module

*2026-10-22, after run 126 was queued and before its verdict was known.*

Two questions this document answers, both of them asked by looking at the
vendor's `np_133.ko` rather than at the board:

1. Does our driver program the BMU the way the vendor's driver does?
2. Does our driver program the four SMACs the way the vendor's driver does?

Both matter because the LAN data plane is still blocked: frames reach the 9132
and the RED drops every one of them on egress with the "packet length 0"
counter moving in step with the drop counter.  The vendor's module is the only
thing on this board that is known to move a frame in both directions, so its
initialisation order is the reference.

Everything below comes from `staging/vendor-full/kmodule/np_133.ko`
(aarch64, ET_REL) and from the register dumps the console kept
(`staging/ctl/vendor-serial-mods/`).  The disassembly helper is
`staging/swspa.py`; the string helper is `staging/ctl/rostr.py`.

---

## 1. The BMU: `tm_bmu_init`

`tm_bmu_init` is `.text+0x737ac`, 0x2c4 bytes.  It writes twenty registers,
in this order.  `fpga_nppt_write_reg(offset, value)` takes the offset as
`module << 12 | word`, so `0xf012` is module 15 (BMU, base `0x3c000`) word
`0x12`, i.e. byte `0x3c048`.

| # | NPPT offset | value | what it is |
|---|---|---|---|
| 1 | `0x3c000` | `0` | BMU enable, cleared first |
| 2 | — | — | the BPPE index tables are filled: `0x3400` 16-bit entries, byte-swapped, for the normal pool, and the same for the jumbo pool at `base + 0x10000`; the table is then flushed out of the CPU's cache |
| 3 | `0x3c004` | `0x0104c040` | BPPI threshold |
| 4 | `0x3c008` | `0x0104c040` | BPO threshold |
| 5 | `0x00054` | `region_base` | BPPE table base |
| 6 | `0x00058` | `region_base + 0x10000` | jumbo BPPE table base |
| 7 | `0x00060` | `region_base + bppe_size` | normal buffer base |
| 8 | `0x00064` | `region_base + bppe_size + normal_bp_size` | jumbo buffer base |
| 9 | `0x0005c` | `region_base + bppe_size + total_bp_size` | descriptor ring base |
| 10 | `0x00068` | `bp_size \| (jumbo_bp_size << 16)` | buffer sizes |
| 11 | `0x3c048` | `bppe_pool_size << 16` | normal pool size |
| 12 | `0x3c04c` | `jumbo_bppe_pool_size << 16` | jumbo pool size |
| 13 | `0x3c058` | `(bppe_pool_size >> 5) - 1` | normal index maximum |
| 14 | `0x3c05c` | `(jumbo_bppe_pool_size >> 5) - 1` | jumbo index maximum |
| 15 | `0x3c060` | `bppe_pool_size << 16` | normal release configuration |
| 16 | `0x3c064` | `jumbo_bppe_pool_size << 16` | jumbo release configuration |
| 17 | `0x20030` | `bppe_pool_size` | normal buffer count |
| 18 | `0x20034` | `jumbo_bppe_pool_size` | jumbo buffer count |
| 19 | `0x20078` | `0x801` | usable size |
| 20 | `0x3c000` | `1` | BMU enable |

The vendor's globals, read back out of the console dump:

| global | value | how it was confirmed |
|---|---|---|
| `g_bppe_size` | `0x20000` | `NORMAL_BASE - BPPE_BASE` = `0x81a28000 - 0x81a08000` |
| `g_bppe_pool_size` | `0x3400` | `0x3c060 >> 16`, and `0x20030` |
| `g_normal_bp_buffer_size` | `0x1d40000` | `JUMBO_BASE - NORMAL_BASE` = `0x83768000 - 0x81a28000`; also `0x3400 * 0x900` |
| `g_jumbo_bppe_pool_size` | `0` | `0x20034` is zero, and `JUMBO_BASE == DESC_BASE` |
| `g_bp_size` / `g_jumbo_bp_size` | `0x900` / `0x3f00` | `0x68` low and high half |

### 1.1 How our driver compares

`zx279133_bmu_prepare()` in the tree performs the same twenty writes in the
same order.  The only differences are the two counts:

| | vendor | ours (after `0c0086e`) |
|---|---|---|
| `bppe_pool_size` | `0x3400` | `0x1800` |
| `jumbo_bppe_pool_size` | `0` | `0x20` |
| buffer sizes | `0x900` / `0x3f00` | `0x900` / `0x3f00` |
| `0x3c004` final value | `0x0104c040` | `0x0104c040` (was `0x01058080`; `zx279133_bmu_threshold_fixup()` restores it) |
| `0x20078` | `0x801` | `0x801` |

**So after `0c0086e` the BMU programming is byte-for-byte the vendor's
sequence, with a smaller pool.**  That is the reason the BMU is no longer the
first suspect: it is now the same program with different numbers, and the
numbers are self-consistent (`NORMAL_COUNT * NORMAL_SIZE` is the distance the
driver puts between the normal pool and the jumbo pool, and it programs the
same number into `0x3c060`).

Two register values in the vendor's *runtime* dump do not match what its own
init wrote, and both are hardware-maintained rather than configuration:

* `0x3c048` holds `0x50` at runtime, not the `0x34000000` that was written.
  `0x3c080` holds `0x33b0`, and `0x3400 - 0x50 == 0x33b0`; the pair looks like
  "80 buffers in use, 13104 free".
* `0x3c058` holds `0x1a0` at runtime, not the `0x19f` that was written.
  `0x1a0 == 0x3400 >> 5`, the number of 32-buffer index groups.

Neither is written by the vendor's driver after init, so the tree's writes to
them are as harmless as the vendor's.

### 1.2 The geometry the two programs imply

```
vendor:  BPPE 0x20000 | normal 0x3400 * 0x900 = 0x1d40000 | jumbo 0 | descriptors 0x200000
         total 0x1f60000 (31 MiB)

ours:    BPPE 0x20000 | normal 0x1800 * 0x900 = 0xd80000  | jumbo 0x20 * 0x3f00 = 0x7e000
         | descriptors 0x200000
         total 0x101e000 (16 MiB)
```

Both fit in the 21 MiB `bmu_reserved` region the DTS now reserves.

---

## 2. The SMACs: `nppt_smac_init`

`nppt_smac_init` is `.text+0xda1c`, 0x4e4 bytes.  It loops over the four
SMACs, whose bases are `0x40000`, `0x80000`, `0xc0000` and `0x100000`, and for
each one does:

| # | offset from the SMAC base | value | what it is |
|---|---|---|---|
| 1 | — | — | the PHY's check callback for that port runs first |
| 2 | `0x14 + 4*mac` | bit cleared | `nppt_smac_set_uni_mode(mac, 0)` |
| 3 | `0x0000` | `0x00ba2203` | `an_ctrl`, back to its "reset" value |
| 4 | `0x2c0004` (shared) | this MAC's bit cleared, then set again, with a delay in between | `smac_reset(1 << mac)` |
| 5 | `0x0000` | `0x00fa2203` | `an_ctrl`, "running" |
| 6 | `0x0004` | `0x3fff` | `an_ctrl2` |
| 7 | `0x0008` | `0x80000000` | packet filter |
| 8 | `0x0b00` | `\|= 0x200` | port control |
| 9 | `0x00e0` | `0x00011200` | speed |
| 10 | `0x343f0` (shared) | this MAC's bit cleared when `an_ctrl` bit 13 is set, set otherwise | the switch's per-port enable word |
| 11 | `0x0000` | `\|= 2` | `an_ctrl` (a no-op: `0x03` already has bit 1) |

`smac_reset` (`.text+0xd29c`) reads the shared word at `NPPT + 0x2c0004`,
writes it with this MAC's bit cleared, waits, writes it back with the bit set,
and touches no other bit - so the XMAC and the PCS are not reset from here.

### 2.1 How our driver compares

| # | vendor | ours |
|---|---|---|
| 2 | uni mode bit cleared | same |
| 3 | `an_ctrl = 0x00ba2203` | same (added in `e3dbac2`) |
| 4 | `smac_reset(1 << mac)` | same (added in `e3dbac2`) |
| 5 | `an_ctrl = 0x00fa2203` | same |
| 6 | `an_ctrl2 = 0x3fff` | same |
| 7 | `0x0008 = 0x80000000` | **not written**; the register is read and printed |
| 8 | `0x0b00 \|= 0x200` | same |
| 9 | `0x00e0 = 0x00011200` | **not written**; the register is read and printed |
| 10 | `0x343f0` bit cleared | not written |
| 11 | `an_ctrl \|= 2` | not written, but it is a no-op |

Rows 7 and 9 are safe to leave alone.  The vendor's own *final* values on its
board are `0x80000001` (packet filter) and `0x00011000` (speed), and those are
exactly what this board holds straight out of reset - the header says so, and
it says that writing the vendor's init values over them destroys them.  The
vendor writes `0x80000000` at row 7 and `0x00011200` at row 9, and something
later puts the bits back; its net effect is the reset value.  Row 10 is a
no-op as well: `0x343f0` is zero on the vendor's board, and the vendor's own
`nppt_smac_enable()` only ever clears bits in it.

**So the SMAC programming is the vendor's sequence as well, minus two writes
that turn out to be no-ops.**

### 2.2 The one thing that was missing until `e3dbac2`

Rows 3 and 4 - `an_ctrl` back to `0x00ba2203` and the reset pulse - are what
the commit added.  They are the only part of the vendor's SMAC sequence that
was absent, and they are the part that matters for the symptom this project
is chasing:

> The pulse clears the MAC's own receive state - the buffer pointers, the
> descriptor watermarks, the length bookkeeping.  A frame that arrives at a
> MAC that was never reset is handed on without a length, and the switch drops
> it.  That is what the RED's "packet length 0" counter counts.

The pulse was in no successful build before `e3dbac2`: run 124 and run 125
both failed to compile, and the kernel on the live board is run 123's, which
predates it.

---

## 3. What this rules out

* The BMU is programmed the vendor's way; the remaining difference is the size
  of the pool, which is a software choice and is self-consistent.
* The SMACs are programmed the vendor's way.
* The IDM's buffer pools, free rings and pool-size registers are programmed
  the vendor's way (`docs/45`).

What is still open is the set of registers that neither driver writes but that
differ between the two boards.  The way to find them is to read the same
addresses on both, which is what `staging/ctl/exp96.txt` and
`staging/ctl/vdiff.py` do: `exp96.txt` reads every address the vendor's dump
has a non-zero value for (623 of them, in 201 runs), and `vdiff.py` compares
what our kernel read against the vendor's truth.
