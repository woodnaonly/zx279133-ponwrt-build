# The live experiment runbook

Every command in this document is one line typed into the board's console, and
every one of them can be undone or left alone: the board is on the trial
kernel, in RAM, and the power is cut every ninety seconds or so, so nothing
here survives a power cycle anyway.

## The channel

The board's only network path in from the PC runs through the 9132, and the
9132 is what is being fixed, so SSH is not an option on a trial kernel that
cannot forward a frame.  Everything is therefore typed into the console and
read back out of the serial log.

```
staging/ctl/sercmd.txt      the file the relay reads and types
staging/ctl/serial.log      everything the board says
staging/ctl/dbg.sh <file>   type a list of commands, show the answer
staging/ctl/survey.sh       the whole register survey in one go
staging/ctl/accept5.sh      the G2 acceptance
```

`dbg.sh` joins the lines of its file with `; ` and types them as one line,
so the whole list is one round trip - the board does not live long enough
for one round trip per command.

## The two rules the console imposes

**Numbers with a prefix.**  The console parses a number with
`kstrtou32(tok, 0)`, and base 0 means the prefix decides: `0x140000` is
hexadecimal, `140000` is decimal, `010` is octal.  Every register offset
has to carry the `0x` prefix, and so does every ERAM table id.

**The table id is not the table number.**  `eg 18 128 2` reads entry 128 of
table **0x12**, because 18 is decimal.  The switch protocol table is 0x18,
so it is `eg 0x18 128 2`.  The console's own help text gets this wrong
("eg 18 128 2" is printed as the example), which is how the mistake gets
made.

## The registers that matter

| what | offset | what it tells you |
|---|---|---|
| XMAC0 TX_CTRL | `0x140000` | bit 0 is the enable; bits 29..31 are the speed |
| XMAC0 RX_CTRL | `0x140010` | bit 0 is the enable; the rest is the vendor's value |
| XMAC0 FRAME_CFG | `0x140020` | |
| XMAC0 MODE_CFG | `0x140280` | |
| XMAC0 DUPLEX | `0x140500` | bit 24 is half duplex |
| XMAC0 MISC_CFG | `0x143400` | bit 9 is the IPC bit |
| XMAC0 TX frames | `0x142070` | **the counter that says whether a frame moved** |
| XMAC0 TX good | `0x1420b0` | |
| NPPT XMAC link | `0x0084` | |
| NPPT XPCS0 status | `0x0090` | bits 11..13 are the PCS link |
| NPPT XMAC error | `0x00fc` | |
| IDM configuration | `0x280000` | bit 24 and bit 13 differ from the vendor |
| IDM RX queue counters | `0x2800c4`..`0x280104` | one word per hardware queue |
| IDM BP ring | `0x28010c`, `0x280110` | the vendor's encoding differs from the driver's |
| SOPC ready array | `0x34294` + 4*i | the "ready" bit of SerDes output port i |
| SOPC send array | `0x342b0` + 4*i | the send enable of the same |

The ERAM tables the driver lays out are printed by `cat
/sys/kernel/debug/zx279133-9132`: the base block and the width of every
entry.  The two that matter here are

* table **0x1f** - the flood group table - base block 19975, 32 bits per
  entry, so `es 0x1f <group id> 32 <port id>` writes one group's
  destination port;
* table **0x7d** - the MF extension table - base block 24737, 128 bits per
  entry, one entry per SPA memory id.  Word 0 is the port attribute word,
  word 1 is SPA register 18 and word 2 is SPA register 17.

## The experiments

### E1 - did the XMAC come up?

Read it:

```
echo "reg 0x140000" > /sys/kernel/debug/zx279133-9132
echo "reg 0x140010" > /sys/kernel/debug/zx279133-9132
echo "reg 0x142070" > /sys/kernel/debug/zx279133-9132
```

The values the vendor's own `xamc_init_conf_by_speed()` writes are TX_CTRL
`0x00010000` with the speed bits, RX_CTRL `0x3e8000c6`, FRAME_CFG
`0x80000000`, MODE_CFG 2, and MISC_CFG with bit 9 set; the enable is bit 0
of TX_CTRL and of RX_CTRL.  If the words read back as anything else, the
program did not happen or something overwrote it.

### E2 - did the frame get out of the MAC?

`reg 0x142070` is the counter of frames the XMAC put on the wire.  If it
stays at zero while the netdev's own tx counter moves, the frame never
reached the MAC.

### E3 - did the frame get into the IDM?

`reg 0x2800c4`..`reg 0x280104` are the per-queue receive counters.  They
are the only evidence of a frame arriving that the chip will ever give.

### E4 - the flood groups (the receive path)

The four UNI jacks' flood groups hold the WAN port in the vendor's
configuration, so a unicast frame addressed to the board is dropped at the
9132 before the CPU sees it.  Put the CPU port into them:

```
echo "es 0x1f 0x40 32 5" > /sys/kernel/debug/zx279133-9132
echo "es 0x1f 0x80 32 5" > /sys/kernel/debug/zx279133-9132
echo "es 0x1f 0xc0 32 5" > /sys/kernel/debug/zx279133-9132
echo "es 0x1f 0x100 32 5" > /sys/kernel/debug/zx279133-9132
echo "es 0x1f 0x440 32 5" > /sys/kernel/debug/zx279133-9132
echo "es 0x1f 0x480 32 5" > /sys/kernel/debug/zx279133-9132
echo "es 0x1f 0x4c0 32 5" > /sys/kernel/debug/zx279133-9132
echo "es 0x1f 0x500 32 5" > /sys/kernel/debug/zx279133-9132
echo "es 0x1f 0x840 32 5" > /sys/kernel/debug/zx279133-9132
echo "es 0x1f 0x880 32 5" > /sys/kernel/debug/zx279133-9132
echo "es 0x1f 0x8c0 32 5" > /sys/kernel/debug/zx279133-9132
echo "es 0x1f 0x900 32 5" > /sys/kernel/debug/zx279133-9132
```

The three indexes of a port p are `p<<6`, `(p+0x10)<<6` and `(p+0x20)<<6`
- the unknown-DA, broadcast and multicast groups - and the value is the
destination port id itself, in bits 0..5, not a bitmap.  Port 5 is the CPU
port.  The vendor's `group_id_table_port_id_set()` masks the port id to six
bits and sets bit 7 only for an SSID port, and its
`se_eram_table_set(0x1f, group_id, 1, &port_id)` addresses the entry at
`bit_len * index`, which is exactly what the driver's helper does.

The group ids also have to be in the table the switch engine reads: ERAM
0x7d's word 1 (SPA register 18) and word 2 (SPA register 17).  Read the
entry first, then write the whole block back with `ew`:

```
echo "er 24738 1" > /sys/kernel/debug/zx279133-9132
echo "ew 24738 <word0> <word1> <word2> <word3>" > /sys/kernel/debug/zx279133-9132
```

with word 1 and word 2 replaced by the vendor's own flood words of that
port: PPU 1 `0x00400840` / `0x04400410`, PPU 2 `0x00800880` /
`0x04800410`, PPU 3 `0x00c008c0` / `0x04c00410`, PPU 4 `0x01000900` /
`0x05000410`.

### E5 - the IDM's shared words

The driver writes `0x1000` to IDM+0x10c and IDM+0x110 where the vendor
writes `0x00203c00` and `0x00003c00`; the driver sets bit 13 of IDM+0x000
where the vendor sets bit 24 and clears bit 13.  Each is one write:

```
echo "reg 0x280000" > /sys/kernel/debug/zx279133-9132
echo "regw 0x28010c 0x00203c00" > /sys/kernel/debug/zx279133-9132
echo "regw 0x280110 0x00003c00" > /sys/kernel/debug/zx279133-9132
```

### E6 - the vendor's XMAC values, put back by hand

```
echo "regw 0x140000 0x38000000" > /sys/kernel/debug/zx279133-9132
echo "regw 0x140010 0x3e8000c7" > /sys/kernel/debug/zx279133-9132
echo "regw 0x140020 0x80000000" > /sys/kernel/debug/zx279133-9132
echo "regw 0x140280 0x00000002" > /sys/kernel/debug/zx279133-9132
```

TX_CTRL `0x38000000` is `0x00010000` with the 1G speed code 3 in bits
29..31 and the enable in bit 0; RX_CTRL is the vendor's `0x3e8000c6` with
the enable in bit 0.  The speed code is 3 for 1G, which is what this lane
runs at.

## What each answer means

| observation | what it says |
|---|---|
| XMAC0 TX frames moves, lan-cpu0 rx does not | the frame left the SoC and the 9132 did not give it back - the 9132's own forwarding |
| XMAC0 TX frames does not move, the netdev's tx does | the frame never reached the MAC - the IDM or the XMAC's programming |
| lan-cpu0 rx moves, the PC sees nothing | the frame arrived and the reply did not go out - the reverse direction |
| the IDM queue counters move, lan-cpu0 rx does not | the frame is in the IDM and nobody polled that queue |
