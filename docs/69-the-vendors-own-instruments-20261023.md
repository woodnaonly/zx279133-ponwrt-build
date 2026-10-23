# The vendor's own instruments: where a frame from the PC goes, and where ours stops

Date: 2026-10-23 (later rounds: 175)

## 0. What happened, and what this round had to do without

The smart plug cut the power at 20:07, and with it the CH340's USB
enumeration.  The serial port did not come back:

```
USB-SERIAL CH340 (COM3)   Status: Unknown       (removed, "not connected")
USB\VID_1A40&PID_0101\6&3dd761e&0&1   the hub it sits on
```

`pnputil /restart-device` on the CH340 says the device is not connected.
Disabling and enabling the hub, and disabling and enabling the PCI USB
controller, both leave it away, and `pnputil /remove-device` on the hub
answers **"System reboot is needed to complete configuration operations!"**.
So the port comes back only after a reboot of the PC.  Everything below was
therefore done over ssh, which needs no serial port - the board was running
the vendor's own 4.19 system at that moment, and it answers on
192.168.9.1.

The one thing that cannot be done over ssh is the U-Boot prompt.  The
environment is a block inside `mtd1` ("boot"), and its `bootcmd` is:

```
bootcmd=mtd read kernel 0x88000000; mtd read dtb 0x88800000; bootm 0x88000000 - 0x88800000
ipaddr=192.168.1.1
serverip=192.168.1.101
```

The built-in default in the U-Boot binary is `bootcmd=zxboot`, `ipaddr=192.168.1.1`,
`serverip=192.168.1.101`.  Both copies were read and kept
(`staging/ctl/vendor-env-primary.bin`, `vendor-env-backup.bin`).  A backup of
the active environment is the way to make the board boot our initramfs on its
own at the next power cycle, with no serial console at all - U-Boot has
`tftpboot`, and the PC's TFTP server is listening on UDP 69.  That is the
fallback if the serial port does not come back.

## 1. The CPU port's own twenty words

The CPU port's row in the vendor's indirect-RAM map is called
`spa_port_attr_trap(cpu)`, with the "(cpu)" in the name.  Without it the read
prints "cmd success!" and nothing else, which is what the first attempt did.

The vendor's values, and ours:

| word | vendor | ours | note |
|---|---|---|---|
| 0 | 0xa767 | 0xa767 | |
| 1 | 0x88f | 0x88f | |
| 2..9 | 0 | 0 | |
| 10 | 0x0fa0000e | 0x0fa0000e | |
| 11, 12 | 0 | 0 | |
| 13 | 0x8008 | 0x8008 | |
| 14..16 | 0 | 0 | |
| **17** | **0x07c00000** | **0** | |
| **18** | **0x03c00bc0** | **0** | |
| 19 | 0 | 0 | |

Round172 wrote word 17 as 0x07c00410 - the queue numbers at the bottom were
a guess, and the guess was wrong: the CPU port has no queue-count field at
all, its word 17 is the port group (7) and the queue base (0xc0) only.
Word 18 was guessed right.

The pattern that produced the guess is worth keeping: for the other ports,
word 17 is `(group << 24) | (queue base << 16) | (4 TX queues) | (16 RX
queues)`, the group being `4 + floor(ram id / 4)` and the queue base
`0x40 * (ram id % 4)`.  The CPU port is the exception: it is the trap, it
has no queues of its own, and the two bottom fields are zero.

**The CPU port's row is the only one of the thirty-six that our board still
differs from the vendor's in.**  The four UNI jacks (ram ids 1..4) match
word for word, and the twenty-eight differing words are all on ports this
board does not use.

## 2. Where a frame from the PC goes, on the vendor's board

The vendor's `start_statistics` window prints the 9132's own counters.  With
the PC pinging the board (19 unicast frames in, 8 of 8 bursts with zero
loss), the chain reads:

| block | counter | value |
|---|---|---|
| SMAC | smac1 up frame total pkt | **19** |
| SMAC | smac1 up 64 / 65to127 / 128to255 / 1024tomax | 7 / 10 / 1 / 1 |
| SMAC | smac1 dn all pkt | 18 |
| SDET | smac1 in_sop, in_eop | 19, 19 |
| SDET | smac1 success_in_eop, success_out_eop | 19, 19 |
| SDET | out_drop_eop, out_mac_err_eop, pkt_len_*_err, fifo_full_drop | 0 |
| SIPC | smac in byte cnt(rc) | 3013 |
| SIPC | sipc to spa pkt cnt | 46 |
| SPA | SIPC2SPA SOP/EOP, SPA2SMCT SOP/EOP | 46, 46 |
| SPA | UNTAG CNT | 19 |
| SPA | SPA CRC ERR DROP, SPA PON IN ERR, SPA SIPC IN DROP, SPA PKT FILTER | 0 |
| ISU | spa in cnt, out to ppu unicast cnt | 46, 46 |
| ISU | ppu in cnt, out to odma cnt | 0, 0 |
| ODMA | ppu in cnt, read smct cnt, out to red cnt | 46, 46, 46 |
| PPU | mf in vld per cluster | 48, 40, 40, 48, 48, 48, 48, 48 |
| MCODE | **port id 2 input uni cnt** | **19** |
| MCODE | port id 15 input uni / input brd | 18, 9 |
| MCODE | **mac da lookup fail cnt** | **19** |
| MCODE | **protocol action trap cnt** | **19** |
| MCODE | port id 2 output uni, port id 3 output brd, port id 15 output uni | 18, 9, 19 |
| MCODE | every drop counter | 0 |
| RED | **in fwd cnt** | **46** |
| RED | **in idm fwd cnt** | **19** |
| RED | in dn fwd cnt | 27 |
| RED | **out fwd cnt** | **46** |
| RED | **out idm fwd cnt** | **19** |
| RED | out dn fwd cnt | 27 |
| RED | in drop, out drop | 0, 0 |
| WSCH | idm vld, burst eop idm, wsch enq | 19, 19, 19 |
| SOPC | sopc2smac uni eop, smac1 / smac2 | 18, 9 |

Three things in that table matter more than the rest.

**The frame is trapped by the protocol action table, not by the forwarding
table.**  All nineteen frames miss in the MAC's destination lookup -
`mac da lookup fail cnt` is 19, one for every frame - and all nineteen are
still trapped: `protocol action trap cnt` is 19.  On this board the CPU
port's own entry in the forwarding table is not what carries the frames; the
chip's protocol action table is.  A destination lookup that fails does not
stop a trap.

**Everything that could drop a frame on the way is zero.**  Every error,
overflow, rate-limit, ACL and flood-disable counter on the vendor's board is
zero while those nineteen frames pass.

**The chain is unbroken from the PHY to the IDM.**  The frame crosses the
MAC, the SDET, the SIPC, the SPA, the ISU, the ODMA, the PPU, the MCODE and
the RED, and nineteen frames leave through the RED towards the IDM.

## 3. The RED block, all of it

The vendor's `module_reg` prints the RED with its base in the vendor's word
indices: `nppt tm red flag=2 base=0x8000 reg_cnt=0x100`.  The base is a word
index, so the byte offset is `0x8000 * 4 = 0x20000` - exactly the RED block
this driver already addresses.  Register `n` of the vendor's dump is at
`NPPT + 0x20000 + 4 * n`, and the register this project has been calling
"in fwd", `0x20204`, is the vendor's red register `0x81`.

The complete 256 words are in `pon/vendor-red-run175.txt`.  The ones that
decide anything:

| register | NPPT | vendor | ours (run142) |
|---|---|---|---|
| 0x01 | 0x20004 | **0x9e** | **0x7e** |
| 0x02 | 0x20008 | 0x0 | |
| 0x03 | 0x2000c | 0x7f | |
| 0x04 | 0x20010 | 0x100020 | |
| 0x05 | 0x20014 | 0x1000190 | |
| 0x06 | 0x20018 | 0x1 | |
| 0x07 | 0x2001c | 0xff803fff | |
| 0x08 | 0x20020 | 0x100ff80 | |
| 0x09 | 0x20024 | 0x100200 | |
| 0x0a | 0x20028 | 0x20 | |
| 0x0b | 0x2002c | 0x0 | |
| 0x0c | 0x20030 | 0x3400 | |
| 0x0d | 0x20034 | 0x0 | |
| 0x0e | 0x20038 | 0x1 | |
| 0x0f | 0x2003c | 0x6 | |
| 0x10 | 0x20040 | 0xc00 | |
| 0x11, 0x12 | 0x20044, 0x20048 | 0xfff0fff | |
| 0x13 | 0x2004c | 0x0 | |
| 0x14 | 0x20050 | 0x2600 | |
| 0x15, 0x16 | 0x20054, 0x20058 | 0xffffffff | |
| 0x17 | 0x2005c | 0x0 | |
| 0x18 | 0x20060 | 0x10000900 | |
| 0x19 | 0x20064 | 0x4000500 | |
| 0x1a | 0x20068 | 0x10001 | |
| 0x1b | 0x2006c | 0x3000 | |
| 0x1c | 0x20070 | 0x6001000 | |
| 0x1d | 0x20074 | 0x14002000 | |
| 0x1e | 0x20078 | 0x801 | the RED's usable-buffer word, already known |
| 0x1f | 0x2007c | 0x0 | |
| 0x20..0x2f | 0x20080..0x200bc | see the file | |
| 0x3c | 0x200f0 | 0x138 | |
| 0x3d | 0x200f4 | 0xffff | |
| 0x3f | 0x200fc | 0x10001 | |
| 0x40..0x5f | 0x20100..0x2017c | see the file | |
| 0x70 | 0x201c0 | 0x638 | |
| 0x76 | 0x201e0 | 0x638 | |
| 0x80 | 0x20200 | 0x0 | 0x0 |
| **0x81** | **0x20204** | **0xa31** | **0x5** |
| 0x82 | 0x20208 | 0x3 | 0x4 |
| 0x83 | 0x2020c | 0x0 | 0x0 |
| **0x84** | **0x20210** | **0xa31** | **0x0** |
| 0x85 | 0x20214 | 0x3 | 0x9 |
| 0x86 | 0x20218 | 0x0 | 0x0 |
| **0x87** | **0x2021c** | **0xa34** | **0x9** |
| 0x88..0x8a | 0x20220..0x20228 | 0x0 | 0x0 |
| 0x8b | 0x2022c | 0x1089 | |
| 0x8c | 0x20230 | 0x7b7 | |

**The configuration half of the RED block - registers 0x00 to 0x7f, sixty-four
words of it - has never been compared word for word against the vendor.**
Every experiment so far has read one word of it (0x20004) or a few counters.
The vendor's true values for all sixty-four are now known, and the next
round reads all of them on our board in one go.

Note also that the vendor's `red reg=0x1` is 0x9e, which confirms the
finding in `zx279133.h`: the tree's `zx279133_tm_prepare()` writes the
SR1010 value 0x7e, and the vendor's 9132 board runs 0x9e with bit 7 -
all_active_mode - set.

## 4. What our board shows, and where the two diverge

Our board on the run142 kernel, with the PC sending:

| | vendor | ours |
|---|---|---|
| RED_CFG | 0x9e | 0x7e |
| red 0x81, in fwd | 2609 (moving) | 5 (never moves) |
| red 0x84, out fwd | 2609 (moving) | 0 |
| red 0x87 | 2612 | 9 |
| SMAC smac1 up frame total | 19 | never measured |
| SDET smac1 success_in_eop | 19 | never measured |
| lan-cpu0 RX bytes | frames reach the CPU | 0 |

The divergence is **before** the RED.  On the vendor's board the frame crosses
the MAC and the SDET and the SPA and the ISU and the ODMA and the PPU and the
MCODE before it reaches the RED; on ours the RED's in-forward counter never
moves, so the frame never got there.  Every hypothesis about the RED's
configuration, the protocol action table, the trap port's queue words and
the MF extension table has been a hypothesis about a stage the frame has not
reached.

**The next round has to measure the earlier stages on our own board**, with
the same instruments the vendor just used:

1. read the whole RED block on our board - `regs 0x020000 256` - and
   compare all 256 words against `pon/vendor-red-run175.txt`;
2. read the SDET and the SIPC blocks on our board (`0x2000` and `0x4000`) and
   compare them against the vendor's dumps, the same way;
3. find the SMAC's own counters and read them while the PC sends, so that
   "the frame never reached the MAC" and "the frame reached the MAC but not
   the switch engine" become different readings;
4. only then look at the RED's configuration, the protocol action table and
   the trap port again.

The vendor's `module_reg` interface is also worth using for the blocks this
driver has no name for: `echo -r sdet reg=0 cnt=64` and
`echo -r sipc reg=0 cnt=64` are already captured, with their bases
(`sdet` 0x800, `sipc` 0x1000, in word indices, so 0x2000 and 0x4000 in
bytes).

## 5. Files

| file | what |
|---|---|
| `pon/vendor-red-run175.txt` | the vendor's RED block, all 256 registers |
| `pon/vendor-stats-run175.txt` | the vendor's statistics window under PC traffic |
| `pon/vendor-spa-attr-run175.txt` | the vendor's 37 SPA per-port attribute RAMs |
| `pon/vendor-env-primary.bin` | the U-Boot environment as it stands in mtd1 |
| `pon/vendor-env-backup.bin` | the copy at the second offset |
| `staging/ctl/vendor_trap.sh` | reads the CPU port's twenty words |
| `staging/ctl/vendor_stats.sh` | opens and closes the statistics window |
| `staging/ctl/vendor_mods.sh` | dumps red, sdet, smac and sipc |
| `staging/ctl/vendor_red.sh` | dumps the whole RED block |
| `staging/ctl/env_probe.sh` | finds the U-Boot environment |
