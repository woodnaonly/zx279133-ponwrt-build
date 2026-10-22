# The bench, the length filter, and the address the board answers on

Date: 2026-10-23.  Scope: the G2 gate - "the PC can reach the board over the
9132's LAN ports".  Everything below was measured on the board; the reference
values come from the vendor's own board
(`staging/ctl/vendor-serial-mods/`).

## 1. The summary

Three separate things were wrong, and only the third was the one the earlier
rounds were looking for.

1. **The bench's own ARP entry was on the wrong adapter.**  Fixed in the
   previous round (`New-NetNeighbor -InterfaceIndex 19`); the frames now reach
   the board at all.

2. **The RED's length filter is not the difference.**  The mainline board and
   the vendor's board hold the *same* value in the two words that enable the
   length drops, so the "pktlen0 drop" counter was never telling us that the
   filter was mis-set - it was telling us that the length the RED was handed
   was zero.  Clearing the filter stops the drops and lets the frames
   through, but it only hides the symptom, so the words are left alone.

3. **The board was answering on an address nobody was using.**  The device
   tree carried `local-mac-address = 3e:8e:98:91:64:2a`, which was not a
   factory address: it was read out of one trial boot's `/proc/net/dev`,
   where the kernel had already made a random one (the locally-administered
   bit is set).  The factory address the vendor firmware puts on the wire for
   192.168.9.1 is `d0:45:8d:84:09:00`.

## 2. The control experiment on the vendor firmware

The board was running its vendor firmware (the trial kernel lives in RAM and
the smart plug cuts the power every fifteen minutes).  The PC's neighbour
entry was pointed at one vendor address, then at the other, and the same
ping was run both times:

```
arp entry d0:45:8d:84:09:00 -> ping 192.168.9.1: 4 replies, 0% loss, 1-3 ms
arp entry d0:45:8d:84:08:ff -> ping 192.168.9.1: 0 replies, 100% loss
```

`d0:45:8d:84:08:ff` is eth0's own address and answers for nothing;
`d0:45:8d:84:09:00` is br-lan's, and 192.168.9.1 lives on br-lan.  So the
PC and the board are on the same wire and the path is sound - the trial
kernel was simply not answering on any address the outside world was using,
and a random per-boot address cannot fix that.

The device tree now carries the factory address:

```
local-mac-address = [d0 45 8d 84 09 00];
```

## 3. What the RED's counters say

The trial kernel's debugfs can write a register and read it back
(`regw <nppt-offset> <value>`), so the two enable words were changed on a live
board and watched.

Before the write (uptime 132 s):

```
0x20038 = 0x00000001   0x2003c = 0x00000006
0x20204 (in fwd) = 138  0x20210 (out fwd) = 2
0x200b8 (pktlen0 drop) = 136  0x200bc (pktlen flt drop) = 136
```

After writing the vendor's own read-back values into them (`0x2003c =
0x03000300`, `0x20038 = 0x00000040`; the hardware masks the read-only bits,
so both words read back as zero):

```
0x20038 = 0x00000000   0x2003c = 0x00000000
0x20204 (in fwd) = 157  0x20210 (out fwd) = 21
0x200b8 (pktlen0 drop) = 136  0x200bc (pktlen flt drop) = 136
```

The drop counters froze and `out fwd` started to move in step with `in fwd`
(+16 and +16 over the next twenty-three seconds).  So:

* the length filter was dropping **every** frame, and
* the frames were fine once the filter was out of the way.

The vendor's board reads the same two words (`tm-red.txt`, words 0x0e and
0x0f: `0x00000001` and `0x00000006`) and its `pktlen0 drop cnt` is zero.
**The filter is therefore not the difference**: on the vendor's board the
length the RED is handed is a real length, and on ours it is zero.  The
filter is left alone and the words are printed by the bring-up dump instead.

## 4. The reference image: the RED, word by word

`staging/ctl/vendor-serial-mods/tm-red.txt` is a word-indexed dump of the
vendor's RED block, the NPPT byte offset being `0x20000 + 4 * word`.  The
mainline board's values, read with the same `regs` command in the same
session:

| word | NPPT | vendor | mainline | what it is |
|---|---|---|---|---|
| 0x01 | 0x20004 | 0x0000009e | 0x0000007e | RED_CFG |
| 0x04 | 0x20010 | 0x00100020 | 0x00100020 | NP1 queue 0 threshold |
| 0x07-0x0a | 0x2001c-0x20028 | 0xff803fff, 0x0100ff80, 0x00100200, 0x00000020 | same | the per-queue buffer configuration |
| 0x0c | 0x20030 | 0x00003400 | 0x00001800 | BMU pool count (meaning not established) |
| 0x0d | 0x20034 | 0x00000000 | 0x00000020 | BMU pool count, jumbo |
| 0x0e | 0x20038 | 0x00000001 | 0x00000001 | illegal-queue-id drop enable |
| 0x0f | 0x2003c | 0x00000006 | 0x00000006 | length-drop enables |
| 0x10 | 0x20040 | 0x00000c00 | 0x00001400 | IN share max |
| 0x11-0x12 | 0x20044-0x20048 | 0x0fff0fff | 0x0fff0fff | |
| 0x14 | 0x20050 | 0x00002600 | | ALL share max |
| 0x18-0x19 | 0x20060-0x20064 | 0x10000900, 0x04000500 | | up/down IN share max |
| 0x1b-0x1d | 0x2006c-0x20074 | 0x00003000, 0x06001000, 0x14002000 | | out share max |
| 0x1e | 0x20078 | 0x00000801 | | RED's usable buffer count |
| 0x21-0x22 | 0x20084-0x20088 | 0x3f9f3e9f, 0x08bf07ff | 0x00042000, 0x00060000 | descriptor watermarks |
| 0x23 | 0x2008c | 0x00000040 | | minimum packet length the length filter accepts |
| 0x93 | 0x2024c | 0x000a0001 | | CPU133_CFG |

The queue-buffer geometry (the indirect RAM 0, 2 and 4) is programmed by the
overlay with the vendor's numbers: the PON/UNI queues 0x000-0x13f get an IN
buffer of 0x600 and an OUT buffer of 0x1000, the port blocks 0x140-0x17f -
which is where smac0-3 and the CPU port live - get 0x300 and 0x800, and
queue 0x180 gets 0x100 and 0x400.

## 5. The IDM, for the record

The IDM's bring-up image matches the vendor's proven one
(`ci-build-repo/docs/09`), word for word: `+0x00 = 0x01ff1007`, `+0x0c =
0x04000000`, `+0x10 = 0x40`, `+0x38 = 0x1000`, `+0x6c = 0x800`, `+0x70 =
0x3e8` (the RX descriptor count, depth minus the 24 queue headers), `+0x44 =
0x071ffbfb`, `+0x48 = 0x200000`, `+0x4c = 0x400000`, `+0x50 = 0x00800404`,
`+0x54..0x60 = 0x06060606, 0x00060606, 0x07070707, 0x07070707`,
`+0x10c/0x110 = 0x203d00/0x3d00`, `+0x40c = 0x3d00`.

The vendor's `np_133.ko` also writes two registers this driver never touches,
and they are worth knowing about:

* `IDM + 0x07c` - the count of buffers the driver has taken back from the
  hardware, split into a normal half and a jumbo half;
* `IDM + 0x418` - the same for the third ("extral") buffer size.

The vendor's NAPI poll reads the hardware's own counters (`+0x08c` "recv
normal bp num", `+0x0f8` "recv jumbo bp num", `+0x41c` "recv extral bp num")
and writes the same numbers back to `+0x07c` and `+0x418`.  This driver does
not, which means the hardware is never told that the buffers were freed.  It
is not the cause of the zero length (the drops stop and the frames flow when
only the length filter is cleared), but it is a real difference and it is
recorded here.

## 6. What is still open

The PC's frames reach the board and are counted (`in fwd` moves in step with
`out fwd` once the filter is out of the way), and br-lan's receive counter
moves - but the board still cannot complete an ARP exchange with the PC, so
the frames that arrive are not the PC's.

The next round has to answer:

* which of the board's four LAN ports the PC is on, and whether that port's
  receive counter moves while the PC pings (the DSA port counters are read
  per port);
* what the 9132's port attributes and its FDB hold for the CPU port, against
  the vendor's;
* whether the frames that arrive carry the transport VLAN the DSA tagger
  requires.
