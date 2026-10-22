# The bench was addressing the wrong device, and the RED is dropping every
# frame for a zero length

Date: 2026-10-23. Board: Skyworth SK-D840N (ZX279133 + 9132). Kernel: the
mainline 6.18.38 trial image, run 127 (`bbd0c00`) and run 128 (`9d86987`).

Everything here was measured on the board or read out of this board's own
vendor module (`staging/vendor-full/kmodule/np_133.ko`) and the vendor's own
register dumps (`staging/ctl/vendor-serial-mods/`).

---

## 1. The static ARP entry was on the wrong adapter

### 1.1 What the PC was actually sending to

The test PC is attached to one of the board's four UNI jacks with a cable, and
the smart plug that feeds the board cuts its power every fifteen minutes; the
carrier on the wired adapter goes down and comes back with it
(`staging/ctl/plugphase.log`), so the wired adapter (ifIndex 19, 192.168.9.25)
is the adapter the board is on.

The bench installed the board's address with:

```
arp -s 192.168.9.1 3e-8e-98-91-64-2a
```

On this PC that lands the entry on the **WLAN** adapter (ifIndex 8), because
`arp -s` without an interface picks the one that owns the default route. The
wired adapter kept its own dynamic entry, learned from whichever device last
answered for 192.168.9.1 on the wire - and the answer came from the **vendor
firmware**, not from the trial kernel.

So every unicast frame the PC sent during every round ever run went to the
vendor firmware's bridge MAC, and the trial kernel was never addressed at all.
The evidence:

| what | value |
|---|---|
| the PC's neighbour entry for 192.168.9.1, on the wired adapter | `d0-45-8d-84-09-00` (dynamic) |
| the vendor firmware's own `ip -o link show` | `eth0` = `d0:45:8d:84:08:ff` |
| the same, on the bridge that owns 192.168.9.1 | `br-lan` = `d0:45:8d:84:09:00` |
| the trial kernel's MAC, run 127 | `ba:30:e9:43:fa:5b` (random, per boot) |
| the trial kernel's MAC, the next boot | `e6:d9:e8:e8:34:bc` (random, per boot) |

`3e:8e:98:91:64:2a` - the address run 128's device tree pins - is **one of the
trial kernel's random addresses**, not this board's factory address: it is
read out of a trial boot's own `/proc/net/dev`. The board's real address is
`d0:45:8d:84:08:ff` (eth0) with the LAN bridge one above it.

The confirmation is a single experiment. With the neighbour entry bound to the
wired adapter and pointing at an address the *vendor* firmware does not own:

```
$ ping -n 4 -w 1000 192.168.9.1
Request timed out.   (4 of 4)
```

100% loss against the vendor firmware, on a cable that is plugged into the
board. Before that change the same ping was 0% loss - because it was going to
the vendor firmware's own address, over the same cable.

### 1.2 The fix

`arp -s ... <ifaddr>` is refused on this machine ("Access is denied"), and
`New-NetNeighbor -InterfaceIndex` is not:

```
New-NetNeighbor -IPAddress 192.168.9.1 -LinkLayerAddress 3e-8e-98-91-64-2a \
    -InterfaceIndex 19 -State Permanent
```

`staging/ctl/arpwin.ps1` does that, and removes every other entry for the
address first. `staging/ctl/macfix.py` finds the trial kernel's address in the
console log and installs it the same way, which is what a round needs while
the kernel still has a random address.

---

## 2. With the address right, the frames arrive - and the RED drops them

### 2.1 What the board counted

One round, run 127's kernel, the PC pinging 192.168.9.1 with the neighbour
entry pointing at the address that boot owned.  The console's own reading, at
45 seconds apart:

| counter | before | after | delta |
|---|---|---|---|
| RED 0x20204 (frames that entered the RED) | 80 | 175 | **+95** |
| RED 0x200b8 bits 23:16 (pktlen0 drop) | 80 | 175 | **+95** |
| RED 0x200bc bits 31:24 (pktlen flt drop) | 80 | 175 | **+95** |
| RED 0x20208 (frames that left the RED) | 31 | 45 | +14 |
| `br-lan` rx_packets | - | 17 | - |

Every frame that entered the RED was dropped by the length filter, and the
filter's reason was a **zero length**: the pktlen0 counter and the length
filter counter move in step, one for one, with the frames that enter.

The vendor's own board, from `staging/ctl/vendor-serial-mods/tm-red.txt`:

```
nppt +0x020204 = 2925     nppt +0x020208 = 7
nppt +0x020200b8 = 0x00000000     nppt +0x020200bc = 0x00000000
```

2925 frames through the RED and **not one** length drop.

### 2.2 Why the tree's RED cannot do that

The RED keeps one buffer descriptor per queue, in three RAMs, reached through
the indirect port at NPPT + 0x20014.  The geometry of every one of the 401
queues is a word in that RAM:

```
ram 2 (in)   q 0x000-0x13f  0x20 | (0x600 << 14) = 0x01800020
             q 0x140-0x17f  0x20 | (0x300 << 14) = 0x00c00020
             q 0x180        0x20 | (0x100 << 14) = 0x00400020
ram 0 (out)  q 0x000-0x13f  0x40 | (0x1000 << 12) = 0x10000040
             q 0x140-0x17f  0x40 | (0x800 << 12) = 0x00800040
ram 4 (cfg)  every queue    ff803fff 0100ff80 00100200 00000020
```

The tree programs the SR1010's geometry for all 401 queues, so the CPU port's
queue - 0x160 - got `0x20 | (0xe00 << 12)` where this board needs
`0x20 | (0x300 << 14)`.  Run 128 writes the board's own geometry over it
through a copy of the indirect port in the overlay (`zx279133_red_ind_write()`,
protocol recovered from `np_133.ko`'s `red_set_indirect_rw_cmd`: the command
word is `addr | (ram << 22) | (rw << 27)`).

### 2.3 The XMAC0 image is different from the vendor's

Read live out of the trial kernel over the drivers' own debugfs, against the
vendor's own dump (`staging/ctl/vendor-serial-mods/smac-xmac0.txt`):

| NPPT | ours (trial) | vendor board |
|---|---|---|
| 0x140000 (TX_CTRL) | 0x60010001 | 0x00000000 |
| 0x140010 (RX_CTRL) | 0x3e8000c7 | 0x00000000 |
| 0x140020 (FRAME_CFG) | 0x80000000 | 0x00000000 |
| 0x140280 (MODE_CFG) | 0x00000000 (the write does not take) | 0x00000000 |
| 0x140500 (DUPLEX) | (the write does not take) | 0x00000100 |
| 0x143400 (MISC_CFG) | (IPC bit 9 set) | 0x00002000 |
| 0x140350 | - | 0x03e80000 |
| 0x140370 | - | 0x00000063 |
| 0x140440 | - | 0x00107630 |
| 0x140470 | - | 0x0401310a |
| 0x140c00 | - | 0x8000ffff |
| 0x142000 | - | 0x00000020 |

The overlay programs this block with the tree's RTL8372N driver's image,
pointed at the 9132's CPU port, plus the literals the vendor's own
`xamc_init_conf_by_speed()` uses.  The vendor's board has **zeros** in the
control words: it never programs them.  That difference is still open, and
it is the next thing to look at if the RED's geometry is not the whole
answer.

---

## 3. What this changes

1. **Every earlier round measured the vendor firmware.** "100% loss under the
   trial kernel" was the PC talking to an address the trial kernel does not
   own.  The fix is in the bench, not in the driver.
2. **The frames do reach the chip.** With the address right, the RED counts
   them, and the bridge counts them too.  The RED is where they die, and it
   says why: the length is zero.
3. **The RED's queue geometry is the first candidate**, and run 128 carries
   the board's own values for all 401 queues.
4. The board's real address is `d0:45:8d:84:08:ff`; run 128 pins a random
   one.  The pinned address has to become the factory address before the
   flashed image is the product.
