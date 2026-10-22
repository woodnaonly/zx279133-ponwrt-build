# 48 - The buffer size the vendor's board runs, and the value the driver was writing instead

Date: 2026-10-22

## What this is about

The LAN receive path stops in the RED module: every frame that arrives from a
LAN port is forwarded in, and every frame is dropped on the way out with a
packet length of zero (`pktlen0 drop` at NPPT + 0x200b8 and `pktlen flt
drop` at NPPT + 0x200bc, with `red2qmg_pkt_len_err` set in the status word
at NPPT + 0x200b0).  That has been the blocker since the board first
forwarded a frame.

Two questions came up in this round: where the packet length comes from, and
what the buffer layout the hardware is told about looks like.  Both are
answered below, and both point at the same place: the buffer size.

## The packet length is not the NP1 path, and it is not in the BMU

The NP1 hypothesis is dead, and it is dead with evidence rather than with a
failed experiment:

* `np1_flag` and `pkt_len` are fields of the descriptor that the CPU hands
  to the traffic manager (NPPT + 0x503c..0x504d).  They are the *CPU to
  switch* direction.
* The vendor's `np_133.ko` only ever *reads* those descriptors.  No vendor
  module writes the NP1 descriptor window at all.
* The RED's NP1 queue thresholds (NPPT + 0x20010 and 0x20354..0x20368) have
  no caller in the vendor's module, so the numbers on the vendor's board are
  the hardware's own reset values: 32, 32, 16 and 15.

The report is in `staging/ctl/re-np1-pktlen.md`.

The BMU has no length register either.  Its registers are the enable word,
the buffer pool thresholds, the pool pointers and the counters
(`staging/ctl/re-bmu-map.md` has the full table).  The BPPE table holds a
16-bit buffer index per entry, not a length.

## The buffer size is one register, and the driver was writing two values into it

The buffer size lives in the global register block at NPPT + 0x68: the size
of one normal buffer in the low half, the size of one jumbo buffer in the
high half.  On the vendor's board it reads **0x3f000900**, and the six base
addresses that go with it are in the same block:

| NPPT | vendor | meaning |
|---|---|---|
| 0x54 | 0x81a08000 | BPPE (normal) base |
| 0x58 | 0x81a18000 | BPPE (jumbo) base |
| 0x5c | 0x83768000 | descriptor base |
| 0x60 | 0x81a28000 | normal buffer base |
| 0x64 | 0x83768000 | jumbo buffer base |
| 0x68 | 0x3f000900 | buffer size: 0x900 normal, 0x3f00 jumbo |
| 0x6c | 0x00000801 | usable size |

Those offsets are exactly the ones the mainline driver uses (the tree's
`zx279133.h` puts them at 0x54, 0x58, 0x5c, 0x60, 0x64, 0x68 and 0x6c), so
the driver was not writing the base registers to the wrong place.  An earlier
guess in this project that they belonged at 0x3c054..0x3c064 was wrong, and
`re-bmu-map.md` says so.

What was wrong is the **values**:

1. The tree's `zx279133_greg_prepare()` writes 0x27800940 into 0x68 and its
   `zx279133_bmu_prepare()` writes 0x26000840 into the same register, so the
   register ended up with a size of 0x840 bytes for a normal buffer where the
   vendor's board has 0x900.  The two writes also disagreed with each other,
   which is a bug in itself: one register, two numbers.

2. The usable size (0x6c, and the RED's copy of it at 0x20078) was 0x781
   where the vendor's board reads 0x801.

3. The tree writes 0x0104c040 into the BPPI threshold at 0x3c004 and then,
   after it has enabled the BMU, overwrites it with 0x01058080.  The vendor's
   np_133.ko writes 0x0104c040 and leaves it there, and that is what the
   vendor's board reads back.

The buffer count is left where the tree put it (0x1800 normal buffers).  The
count is a software decision - how many buffers the pool holds - while the
size is a property of the hardware, and the two have to agree with the
register at 0x68.  0x1800 buffers of 0x900 bytes still fit in the reserved
area, so the reserved area moves instead of the count.

## What the driver does now

* `zx279133.h`: `ZX279133_BMU_NORMAL_SIZE` 0x840 -> 0x900,
  `ZX279133_BMU_JUMBO_SIZE` 0x2600 -> 0x3f00,
  `ZX279133_GREG_BUFFER_SIZE_VALUE` 0x27800940 -> 0x3f000900 (so both writes
  to 0x68 now agree), `ZX279133_GREG_BUFFER_USABLE_VALUE` 0x781 -> 0x801,
  `ZX279133_BMU_REQUIRED_SIZE` 0x00ecc000 -> 0x0101e000,
  `ZX279133_RED_NP1_TH_VALUE` 272 -> 32 (the vendor's reset value).
* The device tree: the BMU's reserved area moves from 0x9f100000 (15 MiB) to
  0x9c200000 (21 MiB), the gap between the ramoops area and the IDM's pool.
  It had to move: 0x1800 buffers of 0x900 bytes plus the BPPE tables, the
  jumbo pool and the descriptor area need 0x101e000 bytes, and the old
  15 MiB no longer held that.
* `zx279133-main.c`: `zx279133_bmu_threshold_fixup()` puts 0x0104c040 back
  into 0x3c004 after the tree's `zx279133_tm_prepare()` has run.  The value
  the tree writes over it is a literal in the tree's `zx279133-tm.c`, which
  the overlay does not shadow.

## The measurements behind this

The vendor's whole register space was read out of the serial console log:
the vendor's `-all` dump prints one line per register, and the console log
keeps every boot, so the last complete block per module is the board's
current state.  `staging/ctl/parse_serial_mods.py` turns that into one file
per module under `staging/ctl/vendor-serial-mods/`.

That dump also settles the question of where each module's registers live,
which every earlier guess depended on.  The `-all` output names the base of
each module, and the base is a **word** index:

| module | base (words) | NPPT bytes |
|---|---|---|
| nppt_greg | 0x0000 | 0x00000 |
| red | 0x8000 | 0x20000 |
| qmg | 0x9000 | 0x24000 |
| bmu | 0xf000 | 0x3c000 |
| idm | 0xa0000 | 0x280000 |
| smac0..3 | 0x10000..0x40000 | 0x40000, 0x80000, 0xc0000, 0x100000 |

So the global block's word 0x15 is NPPT + 0x54, which is where the vendor
keeps the BPPE base.  Reading the same dump as byte offsets puts the base
addresses at 0x54..0x64, which is what the mainline driver does.

## One more thing this round found about the traffic itself

The frames that reach the RED are the ones the PC sends to the board's
address.  When the board reboots, the PC's ARP entry for it goes stale and
the frames stop: the board's own replies never leave, because the transmit
path out of the board is exactly what is broken.  So for the whole of the
next round the PC needs a static entry:

    arp -s 192.168.9.1 3e-8e-98-91-64-2a

Without it the board looks dead to the test PC and every experiment reads
"no traffic" instead of "no forwarding".
