# The 9132's own tables, and what the vendor writes into them

**Date:** 2026-10-21
**Status:** the tables below are the ones the 9132 switch engine reads.
Everything here is either read out of the vendor's own code (offline, with
`staging/swspa.py`) or read out of the vendor's own running system
(`/proc/pon/switch_debug`, `/proc/pon/tm_debug`, and the kernel log over the
serial console).  Nothing here is guessed.

This supersedes the ERAM conclusions of `docs/28` and refines `docs/29`.

---

## 1. Why the tables matter at all

The 9132 is a switch engine with a CPU on one side.  Its forwarding
decisions are not in registers; they are in tables that live in the SoC's
DRAM ("ERAM"), and the switch engine reads them by index.  The vendor
fills them in its own load path:

```
np_133.ko   np_user_table_init()        -> 0x7d, 0x22, 0x27
switch.ko   sw_init_switch()
              sw_init_port_protocol_action -> 0x18
              sw_init_port_isolate         -> 0x1b, 0x7d
              sw_init_mac_lookup_ctrl      -> 0x7d
              sw_init_flood_ctrl           -> 0x1f, 0x7d
              sw_init_omci_trap_qid_init   -> 0x19
              sw_init_port_route_mode      -> 0x7d, 0x24
              sw_set_default_vlan_check_ctrl -> 0x7d, 0x23, 0x25
```

**The ERAM is DRAM.**  It comes up empty after a power cut.  A driver that
writes two of these tables and leaves the rest alone leaves the rest empty,
and an empty isolation matrix says that no port may talk to any other.

---

## 2. The addressing, checked instruction by instruction

`se_eram_table_set_by_sdt_info()` (np_133.ko 0x8ec98) is the only way a
table is written.  Read out of the disassembly:

```
  bit_len = se_sdt_eram_get_table_bit_len(sdt_info[7] >> 1 & 7)   ; entry width
  bit_off = bit_len * index
  addr    = (base << 7) + (bit_off & ~0x7f)                        ; the 128-bit window
  word    = (bit_off >> 5) & 3
  bit     = bit_off & 0x1f
```

and then the entry is merged into the window bit by bit, one of six ways
depending on the width:

| width | what the vendor does |
|---|---|
| 1 | one bit: `window[word] = (window[word] & ~(1 << bit)) \| (v & 1) << bit` |
| 2 | two bits: `mask = 3 << bit` |
| 4 | four bits: `mask = 0xf << bit` |
| 8 | one byte: `mask = 0xff << bit` |
| 16 | one halfword: `mask = 0xffff << bit`, the value read with `ldrh` |
| 32 | the whole word is replaced |
| 64 | two words are replaced |
| 128 | the whole 16-byte window is replaced |

The mode that selects the width is the low three bits of the descriptor's
third field, and the width table is

```
1, 32, 64, 128, 2, 4, 8, 16
```

This driver implements exactly this, in
`zx279133_eram_table_set_bits()` and `zx279133_eram_table_get_bits()`.

**The width of an entry is as load-bearing as the base.**  The hardware
looks an entry up at `(base << 7) + (width * index & ~0x7f)`; a table
declared with the wrong width is read at the wrong place.

---

## 3. The table set on this board

The board loads **np_133.ko**, and its `g_core_sdt_info` (`.data+0x11be0`)
has **53 entries** of `{id, type, p3, num}`.  The driver used to carry a
72-entry list that came from a generic `np.ko` that is not on this board at
all, so every base it computed belonged to a different table.  The list is
now np_133.ko's, in its own order, plus three tables of the driver's own
(0x00 VLAN, 0x04 protocol, 0x06 port type), whose ids are free in the
vendor's list.

The ERAM pool starts at block 0x1000 (`se_smmu0_reg_table` + 0x120), and
the bases that come out of the vendor's own allocation are

| table | base block | entry width | entries |
|---|---|---|---|
| 0x18 protocol action | 11432 (0x2CA8) | 2 | 8192 |
| 0x19 reason -> CPU queue | 11560 | 32 | 256 |
| 0x22 global config | 11400 | 128 | 32 |
| 0x1b isolation matrix | 11628 | 1 | 4096 |
| 0x1f flood group -> port | 11783 | 32 | 16384 |
| 0x23 output VLAN check | - | 1 | 64 |
| 0x24 port bind | - | 1 | 64 |
| 0x25 port PVID | - | 16 | 64 |
| 0x27 outport -> real port | 15887 | 16 | 64 |
| 0x7d port attributes | 16545 (0x40A1) | 128 | 64 |

Table **0x1c** (2 bits x 262144) is allocated and **never written** by any
of the 36 vendor modules: `se_eram_table_set` and `se_smmu0_write` are
defined only in np_133.ko, and no other module imports them.  It is a
reserved table, not a second action plane.

---

## 4. The live system's own state

Read out of the running 4.19 system with `/proc/pon/switch_debug`, whose
command grammar is `port=N` and whose answer lands in the kernel log.

| port | PVID | ingress mode | enable | isolate | link | speed | pro-action | stp-action | MTU |
|---|---|---|---|---|---|---|---|---|---|
| 0 | 0 | 0 | 1 | (not a UNI port) | 0 | 0 | 0 | 0 | 1996 |
| 1 | 0 | 0 | 1 | 0 | **1** | 2 | 0 | 0 | 1996 |
| 2 | 0 | 0 | 1 | 0 | 0 | 0 | 0 | 0 | 1996 |
| 3 | 0 | 0 | 1 | 0 | 0 | 0 | 0 | 0 | 1996 |
| 4 | 0 | 0 | 1 | 0 | 0 | 0 | 0 | 0 | 1996 |
| 5 | 0 | 0 | (error) | (error) | **1** | 6 | 0 | 0 | 1996 |
| 6 | 0 | 0 | (error) | (error) | **1** | 6 | 0 | 0 | 1996 |
| 7 | 0 | 0 | 1 | 0 | 0 | 0 | 0 | 0 | 1996 |

and every port's VLAN configuration reads `mbmask = 0x0, untagmask = 0x0`.

So: **no VLAN, no isolation, every port enabled, MTU 1996** - a transparent
MAC-learning bridge, as `docs/16` had it.

Ports 1, 5 and 6 have a link: **port 1 is the PC's jack** (speed 2 = 100M),
port 5 is XMAC0 (the CPU port) and port 6 is XMAC1.  The vendor's own
`sw_cap` says `SW_ETH_UNI[1] = 4, [2] = 1, [3] = 2, [4] = 3`, so UNI2 -
the jack the PC is in - is chip port 1.  The isolate getter refuses port 0
with `SW_UNI_MSK=30` (0x1e), i.e. the four UNI ports are 1, 2, 3, 4.

The system's MAC table holds exactly one entry,
`84:47:09:5b:bd:2c` on port 0x2, and it is *learned*, not static: the
9132 learns the PC's address on the way in.

---

## 5. What the vendor writes, table by table

### 0x19 - reason -> CPU queue id

`reason_cpuqid_map_table_set()` (np_133.ko 0xb18f4) computes
`index = reason + (cpu_instance << 7)` and writes one 32-bit word.
`tm_reason_map_cpu_qid_set()` (0x1153b4) puts the queue id in **bits 0..4
of byte 3** of that word, and adds 8 to the queue id when
`cpu_instance != 0`.

The only load-time write is `sw_init_omci_trap_qid_init()` (switch.ko
0x2a4c): `zxic_api_sw_reason_map_cpu_qid_set(direct=0, reason=0, qid=7)`.
So **entry 0 = 0x07000000**, and nothing else in the table is written at
load time.

### 0x22 - global config

One 128-bit entry, index 0, and the fields the vendor sets in it:

| field | where in the entry | value | written by |
|---|---|---|---|
| cpu_base_qid | bits 1..9 of the halfword at byte 14 | **0x193** | `glb_cfg_table_cpu_base_qid_set` (0xbdc5c) |
| mul_ip_mode | bit 5 of byte 13 | 1 | `glb_cfg_table_mul_ip_mode_set` (0xbe108) |
| ipv6_crc32_mode | bit 0 of byte 14 | 1 | `glb_cfg_table_ipv6_crc32_mode_set` (0xbdfa4) |
| wifi_ratelimit_en | bit 7 of byte 11 | 0 | `glb_cfg_table_wifi_ratelimit_en_set` (0xbd9f8) |
| port_wan | bits 2..7 of byte 15 | not written | only when upmode is 1 or 0x200; this board is 0x40 |

Byte 14's halfword is word 3 of the entry, bits 17..25; byte 13's bit 5 is
word 3 bit 13; byte 14's bit 0 is word 3 bit 16.

### 0x27 - outport -> real port

`outport_and_real_port_map_table_set()` (np_133.ko 0xc0598) writes a
16-bit entry: the real port in **bits 0..5** and the queue id in **bits
6..14**.

`outport_and_real_port_map_table_init(0)` (0xc06c8) - the non-WLAN branch,
which is this board - writes 64 entries, and the index is the outport:

| index | value |
|---|---|
| 0..14 | 0 |
| **15** | **0x193** (the CPU's own outport) |
| 16..48 | `map[6p-90] + 0x1a3`, where `map` is the kernel's `current_wlan_map` - not in the module set, so not knowable offline |
| 49, 50, 51 | 0x1b1, 0x1b3, 0x1b5 |
| 52..63 | 0 |

### 0x1b - isolation matrix

`port_isolate_table_set()` (0xb29b8): `index = src | (dst << 6)`, one bit
per entry.  `sw_init_port_isolate()` (switch.ko 0x26bc) writes the whole
64x64 matrix: **the diagonal is 1 and everything else is 0**.

Note the port namespace here: `zxic_api_sw_port_set_port_isolate()` calls
`sw_port_change_to_chip_port()` on **both** the source and the
destination, so this table is indexed by the **chip PPU number** (1..4 the
UNI jacks, 5 XMAC0, 15 the trap port), not by the raw switch port.

### 0x18 - the protocol action table

`port_reason_action_table_set()` (np_133.ko 0xb1074) computes
`index = protocol | (port << 7)` and writes the action into **bits 0..1** of
the word - two bits per entry, 8192 entries, 64 ports of 128 protocols.

The load-time data is two tables in switch.ko's own `.rodata`:

* **table A** (`.rodata+0x210`, 77 entries) is written for port 0, which
  is `g_wan_port_id`;
* **table B** (`.rodata+0x498`, 67 entries) is written for ports 1..7 and
  0x10..0x30.

Both hold only the actions 0 and 2, and **2 means "give this frame to the
CPU"**.

**The action is written as it is.**  `sw_pkt_protocol_action_change_to_chip_protocol_action()`
(np_133.ko 0x12770c) does translate (vendor 0 -> 0, vendor 1 -> 2,
vendor 2 -> 1), but it is on the *runtime* OMCI path
(`zxic_api_set_port_proation`), not on the load path.  The load path writes
the `.rodata` value unchanged, and the load path is the one that made the
vendor's own board work.

### 0x7d - the port attribute table

64 entries of 128 bits, keyed by the **SPA memory id** (1..4 the UNI
ports' MAC instances, 5 XMAC0, 15 the trap port).

The write is always the same shape - read the entry as four words, change
**one bit of one byte**, write the four words back:

```asm
se_eram_table_get(0x7d, mem_id, buf, 4)
ldrb  w0, [buf + 3]          ; byte 3 of the entry
bfi   w0, value, #bit, #1
se_eram_table_set(0x7d, mem_id, buf, 4)
```

`port_attr_def_brd_flood_en_set()` (0xbb658) is the example: byte 3, bit 1.
`port_attr_def_sa_lookup_learn_en_set()` (0xb9f4c): byte 3, bit 7.

**So the port attribute word is word 0 of the entry, and the "learning
enable" bit is bit 31 of it.**  The SPA's own copy of it is at SPA indirect
address 19, and the live system reads `0x96040001` there for every LAN
port - bits 0, 18, 25, 26, 28 and 31: unknown-DA flood number 1, input
VLAN check, broadcast flood enable, multicast flood enable, DA lookup
enable and SA learning enable.

The entry's full layout, with each field's setter:

| byte | bit | field |
|---|---|---|
| 0 | 0..5 | unknown-DA flood number |
| 1 | 5 | route mode enable |
| 1 | 6 | SFU process success flag |
| 1 | 7 | 802.1x success flag |
| 2 | 0 | 802.1x enable |
| 2 | 1 | mirror enable |
| 2 | 2 | input VLAN check enable |
| 2 | 3 | source is extended port flag |
| 2 | 4..7 | rate limit enables |
| 3 | 0 | rate limit enable |
| 3 | 1 | broadcast flood enable |
| 3 | 2 | multicast flood enable |
| 3 | 3 | isolate enable |
| 3 | 4 | DA lookup enable |
| 3 | 5 | MAC limit full flag |
| 3 | 6 | SA look-fail drop enable |
| 3 | 7 | **SA lookup learn enable** |
| 4..5 | | multicast flood group id |
| 6..7 | | unknown-DA flood group id |
| 8 | 4..9 | broadcast flood number |
| 9 | 2..7 | multicast flood number |
| 10..11 | | broadcast flood group id |
| 12..15 | | never written by any setter |

The three group ids are set at run time by the PON stack, not at load
time.  Their values are derived from the port: the group id of a port
starts at `p << 6` for unknown-DA, `(p + 0x10) << 6` for broadcast and
`(p + 0x20) << 6` for multicast, and ERAM table 0x1f maps the group id to
the destination port.

### 0x1f - flood group id -> destination port

`group_id_table_data_set()` (0xb9924) writes one 32-bit entry per group
id, and `group_id_table_port_id_set()` (0xb99c4) packs the destination port
into bits 0..5, with bit 7 as an SSID flag.

At load time the vendor writes three entries, all of them zero, for the
WAN port's groups: 0x000 (unknown-DA), 0x400 (broadcast) and 0x800
(multicast), because `g_wan_port_id` is 0 on this board (the upmode is
0x40, so `zte_api_sw_lan_up_set()` - the only writer of `g_wan_port_id` -
is never called).

### 0x23, 0x24, 0x25

| table | writer | what the vendor writes |
|---|---|---|
| 0x23 output VLAN check (1 bit per port) | `output_vlan_check_en_table_set` (0xb506c), via `tm_vlan_check_ena_set(port, type=1, en)` | **1** for ports 0..7 and 0x10..0x30; turned back to **0** for ports 5 and 6 only when the XMACs are extended - and on this board they are not, because those two ports' attribute word has bit 19 (the extend flag) clear |
| 0x24 port bind (1 bit per port) | `port_bind_table_set` (0xbd750) | **0** for ports 0..63; the vendor's loop runs to 64 and the write at port 64 fails, which is the `port(64) set table ... fail` in the live kernel log |
| 0x25 port PVID (16 bits per port) | `port_pvid_table_set` (0xb1fd8) | **0**, from `zxic_api_sw_vlan_set_port_vlancfg(port, 0, 0, 0xfff)`; the live system reads 0 for every port |

---

## 6. Two port namespaces, and where each is used

The vendor code has two port spaces and one explicit conversion,
`sw_port_change_to_chip_port()` (np_133.ko 0x1148ac):

```
0..6   ->  +1                 (the raw UNI ports become chip ports 1..7)
7      ->  g_wan_port_id
0x10..0x1f -> unchanged
0x3b   ->  g_hol_2p5g_virt_port
```

* **0x1b** is indexed by the **chip PPU number** - the conversion is done
  by `zxic_api_sw_port_set_port_isolate()`.
* **0x18** is indexed by the **raw switch port number** on the load path
  (ports 0..7 and 0x10..0x30) and by the **chip PPU number** on the
  runtime path (`zxic_api_set_port_proation()` does the conversion).  The
  vendor mixes the two spaces in one table; that is a fact about the code,
  not a guess.
* **0x7d** is keyed by the **SPA memory id**.

---

## 7. What the driver does about it

| table | what the driver does |
|---|---|
| 0x18 | writes the vendor's table B for ports 1..7, two bits per entry, index `protocol \| (port << 7)`, the action as it is |
| 0x7d | writes word 0 = 0x96040001 for the four UNI ports and the CPU port, in the same read-modify-write shape the vendor uses |
| 0x19 | entry 0 = queue 7 |
| 0x22 | entry 0: cpu_base_qid 0x193, mul_ip_mode 1, ipv6_crc32_mode 1 |
| 0x27 | outport 15 -> real port 15, queue 0x193 |
| 0x1b | the whole 64x64 matrix: the diagonal is 1, everything else 0 |
| 0x23 | 1 for ports 0..7 and 0x10..0x30 |
| 0x24 | 0 for ports 0..63 |
| 0x25 | 0 for ports 0..7 and 0x10..0x30 |

The debugfs console can now reach any entry of any of them by the table's
id, which is what turns the next round of experiments into seconds rather
than into a build:

```
echo "eg 18 128 2"   > /sys/kernel/debug/zx279133-9132   # read
echo "es 18 128 2 2" > /sys/kernel/debug/zx279133-9132   # write
```

---

## 8. Still not known

| item | why |
|---|---|
| `current_wlan_map`, the kernel symbol behind ERAM 0x27's entries 16..48 | it is a kernel symbol, not a module symbol; its contents are set by the PON stack |
| the three flood group ids of each port at run time | set by the PON stack, not by any module; the formula is known, the values are not |
| ERAM 0x1c | allocated, never written, semantics unknown |
| chip action 1 | the runtime translation maps vendor 2 to chip 1, but what chip action 1 does in the hardware is not knowable from the modules |
