# Run 128: the MAC the board owns, and the RED the way the vendor programs it

Date: 2026-10-23
Applies to: `kernel-overlays/` on the SK-D840N (Skyworth, ZTE ZX279133 + 9132)

## 1. Why this round exists

G2 - "the PC's frames reach the board and the board answers" - had been stuck
for two days.  Everything that could be measured on the board had been
measured, and every reading said the same thing: the frames arrive at the
9132 and are dropped before the CPU sees them.

Two facts found on 2026-10-23 decide what to do.

### 1.1 The bench works: the vendor's firmware answers

With the vendor firmware running, the PC on the same cable pings 192.168.9.1
with **zero loss** (six packets, 1-5 ms):

```
Pinging 192.168.9.1 with 32 bytes of data:
Reply from 192.168.9.1: bytes=32 time=3ms TTL=64
...
Packets: Sent = 6, Received = 6, Lost = 0 (0% loss),
```

So the cable, the jack, the RTL8372N-free path into the 9132, the 9132 itself
and the SoC's DMA engine are all able to move a frame.  Whatever is broken is
in the mainline driver's configuration of the 9132, not in the hardware.

### 1.2 The trial kernel's MAC is random, and the PC's ARP entry was wrong

`zx279133-main.c` takes the netdev's address from
`of_get_ethdev_address(dev->of_node, ndev)` and falls back to
`eth_hw_addr_random(ndev)` when the device tree has no MAC source.  The
SK-D840N device tree had none, so **every trial boot came up with a different
address** - three were observed in one evening:

```
8e:b6:59:47:42:39   23:57
46:72:3a:4e:9a:f4   00:03
d0:45:8d:84:08:ff   (an older image)
```

while the vendor's firmware always answers on **3e:8e:98:91:64:2a**, which is
what the PC's ARP cache holds for 192.168.9.1.

That address is not cosmetic.  It is:

  * what `zx279133_program_spa_cpu_mac()` writes into the 9132's SPA (the
    switch's MAC table), so it decides whether the 9132 forwards a frame to
    the CPU port at all;
  * what the XMAC0 in front of the CPU port compares an incoming frame's
    destination against.

With a random address the SPA holds an address nobody sends to, so the
9132 floods, the frame arrives at the CPU port with a destination address
that is not the port's, and it is dropped.  **Every unicast frame the outside
world sent to this board was unroutable**, and every "rx is always 0" reading
in docs/46 and docs/48 was measuring that, not the receive path.

## 2. What this round changes

### 2.1 The device tree gives the board its own address

`&nppt` now carries

```dts
local-mac-address = [3e 8e 98 91 64 2a];
```

which is the address this board answers on under the vendor firmware.  The
driver's existing `of_get_ethdev_address()` call picks it up, the SPA is
programmed with it, and the PC's existing static ARP entry becomes correct
for the trial image too - no more `macwatch.py` on the bench.

This is a per-device address.  A build for another unit needs its own; the
production answer is to parse the vendor's "parameter tags" partition (the
stock device tree has it at NAND 0x4800000), which is not done yet.

### 2.2 The RED is programmed the way this board's vendor programs it

The tree's `zx279133-tm.c` programs the RED for the SR1010.  Its code is in
the kernel and not in the overlay, so the whole RED configuration is written
again in the overlay, after `zx279133_tm_prepare()`, with the values the
vendor's own board reads back
(`staging/ctl/vendor-serial-mods/tm-red.txt`, word `w` is NPPT `0x20000+4w`).

| register | NPPT | vendor | tree (SR1010) |
|---|---|---|---|
| RED_CFG | 0x20004 | **0x9e** | 0x7e |
| IN_SHARE_MAX | 0x20040 | 0x0c00 | 0x1400 |
| ALL_SHARE_MAX | 0x20050 | 0x2600 | not written |
| UP_IN_SHARE_MAX | 0x20060 | 0x10000900 | 0x10001200 |
| DOWN_IN_SHARE_MAX | 0x20064 | 0x04000500 | 0x04000400 |
| ALL_OUT_SHARE_MAX | 0x2006c | 0x3000 | 0x4000 |
| DOWN_OUT_MAX | 0x20070 | 0x06001000 | 0x3000 |
| UP_OUT_MAX | 0x20074 | 0x14002000 | 0x1000 |
| DESC_WATERMARK | 0x20084 | 0x3f9f3e9f | 0x60002000 |
| IDM_DESC_WATERMARK | 0x20088 | 0x08bf07ff | 0x500 |
| IDM_IN_SHARE_MAX | 0x20344 | 0x100 | 0x500 |
| WOE_IN_SHARE_MAX | 0x20348 | 0x01900000 | 0x500 |
| WOE_OUT_SHARE_MAX | 0x2034c | 0x025807ff | 0x3000 |
| NP1_Q0_TH | 0x20010 | 0x00100020 | 0x00100020 |
| NP1_Q1_TH | 0x20354 | 0x00100020 | 0x00100020 |
| NP1_DESC_WM | 0x20368 | 0x00000010 | 0x00000100 |
| NP1 ISU q0..q5 | 0x20260..0x20274 | 0x0005000f | 0x0005000f |

Two of these are not cosmetic.

**RED_CFG.**  Bit 7 is `all_active_mode`; bits 5 and 6 are `dn_share_mode`
and `up_share_mode`.  The tree writes 0x7e, which sets the two share modes
and leaves `all_active_mode` clear.  The vendor writes 0x9e, the other way
round.  This is the tree's value for the SR1010, where the RED is shared
between a PON queue map and eight port blocks; on this board the CPU port's
queue is xmac0's block, 0x160.

**The queue-buffer geometry.**  The RED's per-queue buffer space lives in
three RAMs that are reached through an indirect port (command NPPT+0x20014,
data NPPT+0x2001c..0x20028, done NPPT+0x20018 bit 0).  The vendor programs:

```
ram_id 2 (IN buffer)   q 0x000-0x13f  0x20 | (0x600 << 14) = 0x01800020
                        q 0x140-0x17f  0x20 | (0x300 << 14) = 0x00c00020
                        q 0x180        0x20 | (0x100 << 14) = 0x00400020
                        q 0x187-0x18c  0
ram_id 0 (OUT buffer)  q 0x000-0x13f  0x40 | (0x1000 << 12) = 0x10000040
                        q 0x140-0x17f  0x40 | (0x800 << 12) = 0x00800040
                        q 0x187-0x18c  0x20 | (0x400 << 12) = 0x00400020
ram_id 4 (full cfg)    q 0x000-0x190  ff803fff 0100ff80 00100200 00000020
```

The tree programs, for every one of the 401 queues, the SR1010's geometry:
`0x20 | (0xc00 << 12)` and `0x20 | (0x834 << 14)` for the queues below 320,
`0x20 | (0xe00 << 12)` and `0x40 | (0x200 << 14)` for 320..359, and
`0x20 | (0xe00 << 12)` and `0x80 | (0x400 << 14)` for 360..375.  **Not one of
the 401 queues had the right buffer space**, including queue 0x160, which is
the one the CPU port's frames arrive on.

The overlay now writes the vendor's geometry over the tree's, through its own
copy of the indirect port (the tree's is a static function inside the kernel).

### 2.3 The SMAC's SOPC pulse

The vendor's `nppt_smac_init()` pulses the MAC's bit in the word at NPPT +
0x343f0 (set, then clear again) before it publishes the MAC.  The word's
steady value on the vendor's board is 0
(`staging/ctl/vendor-serial-mods/tm-sopc.txt`, word 0xfc), so the pulse is
the whole point of the two writes.  The overlay now does the same.

The rest of the SMAC sequence was already right, and the *end state* of the
SOPC publish already matched the vendor (ready words 0, send-enable 1 for all
eight channels).

### 2.4 A register dump that survives the power cut

The smart plug that feeds this board cuts its power every few minutes, and a
trial kernel lives in RAM.  A trial boot therefore often ends before a shell
exists, and anything that has to be asked for over the serial console is
measured on a random sample of the boots at best - which is how the earlier
readings were so contradictory.

The driver now prints the register image of the RED, the IDM, the four
internal-PHY MACs and the CPU port's MAC **once, at bring-up**, with the
lines prefixed `DIAG`.  The console log is on the PC, so the image of every
single boot is kept, whatever the plug does.  The values to compare against
are the vendor's, in `staging/ctl/vendor-serial-mods/`.

## 3. What was deliberately not changed

* **The IDM's G7-G16 differences** (`staging/ctl/re-idm-init-diff.md`) were
  re-checked against the overlay: G7 (`0x070` = depth - 0x18 = 0x3e8), G8
  (bit 24 set), G9 (bit 13 clear, bit 12 set), G10 (depth code 0), G11
  (`0x010` = 0x40), G12 (`0x014..0x034` = 0x00010001) and G13 (`0x0bc` never
  written) are already what the overlay does.  Only G14/G15/G16 - the
  buffer-pool refill pulses at `0x100`, `0x418` and `0x07c` - are still open,
  and they are not touched in this round.
* **The RED's ready/publish wait** is left at ten tries of 1 ms.  The vendor
  waits up to 43 s per MAC, but the vendor's board reads the ready words as
  **0** in steady state and publishes anyway, so the wait is not a gate; it
  would only make the boot four times slower.

## 4. How to read the result

The trial image boots, the console log has the `DIAG` lines, and the counters
to watch are the RED's:

| NPPT | meaning |
|---|---|
| 0x20204 | frames forwarded into the RED |
| 0x20208 | frames dropped entering the RED |
| 0x20210 | frames forwarded out of the RED |
| 0x20214 | frames dropped leaving the RED |
| 0x200b8 bits 23:16 | drops with a zero length |
| 0x200bc bits 31:24 | drops by the length filter |

A working board shows 0x20204 and 0x20210 moving together and the two drop
counters staying at zero.  The vendor's board reads 2925/7/2925/7 after a
few thousand pings.

## 5. Files

| file | change |
|---|---|
| `kernel-overlays/arch/arm64/boot/dts/zte/zx279133-sk-d840n.dts` | `local-mac-address` on `&nppt` |
| `kernel-overlays/drivers/net/ethernet/zte/zx279133.h` | the vendor's RED and SOPC constants |
| `kernel-overlays/drivers/net/ethernet/zte/zx279133-main.c` | the RED fixup, the indirect port, the DIAG dump |
| `kernel-overlays/drivers/net/ethernet/zte/zx279133-switch.c` | the SMAC's SOPC pulse |
| `docs/51-the-mac-the-trial-kernel-owns-20261023.md` | the MAC finding and the counter semantics |
