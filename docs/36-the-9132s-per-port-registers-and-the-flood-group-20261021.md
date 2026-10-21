# 36 - the 9132 per-port registers and the flood group


*This is the offline reverse-engineering report behind docs/34.  It was written
against the driver as it stood before run 117; the copy in
staging/ctl/ is the original.*
Offline reverse engineering of the shipped vendor modules. No board, serial console or
network access was used; nothing in `ci-build-repo` was modified; no git write command was run.

Modules examined (canonical copies, `pon/vendor-modules/`):

| module | role in this document |
|---|---|
| `switch.ko` (853 280 B) | the switch glue: `sw_init_switch()` and the per-port init helpers that call into `np_133.ko` |
| `np_133.ko` (5 208 496 B) | the 9132's own driver: the SPA per-port register file, the port-attribute helpers, the ERAM tables |

Tooling: `staging/swspa.py` (ELF/relocation aware disassembler) and `staging/ctl/swat.py`
(disassemble an address range with relocations resolved).  Scratch helpers written for this
task, all read-only: `staging/ctl/spaattrmap.py`, `staging/ctl/spaids.py`,
`staging/ctl/spacallers.py`.

**Reading guide.** Every claim below is tied to a vendor symbol and address. Where the
evidence is ambiguous it says so rather than asserting.

---

## 0. TL;DR

1. **The "port attribute word" is SPA register 19**, and its layout is now fully known:
   20 named bit fields, all of them set by `port_attr_def_*_set()` in `np_133.ko`.
   The driver's existing bit names (13 route mode, 18 in-VLAN-check, 25 brd-flood,
   26 mul-flood, 27 isolate, 28 DA-lookup, 29 MAC-limit, 31 SA-learn) are **all correct**.
   The driver's "bit 0 = unnamed live bit" is **not** unnamed: bit 0 is the LSB of
   `unk_da_flood_num`, a 6-bit field at bits 0..5.
2. **The driver's set of per-port SPA registers is right.** The registers it does not write
   (1, 11, 12, 14, 15, 16) all read **0** on the vendor's live system, except reg 1, which
   the driver correctly leaves alone.
3. **The flood mechanism is a "group id" table, not a bitmap.** The driver's `a17`/`a18`
   are **not** flood bitmaps: they are the *broadcast-flood* and *multicast/unknown-DA
   flood* registers, and their payload is **group ids + counts**. The group id indexes
   **ERAM SDT table 0x1f**, whose entries hold the destination port ids.
   The driver allocates table 0x1f but **never writes an entry into it**, and it mirrors
   only **word 0** of the ERAM 0x7d entry — never the group ids and flood counts that live
   in words 1 and 2 of that same entry. That is where the flooding is lost.

---

## Q1 — The port attribute word (SPA register 19), field by field

### 1.1 How the vendor addresses a per-port register

The vendor never writes SPA register 19 directly. It goes through a descriptor table.

`spa_port_attr_tbl_set(value, port, id)` — `np_133.ko` 0x2343c:

```
000234ec  adrp     x19, <.data>+0x1930
000234f0  ubfiz    x2, x2, #4, #0x20      ; x2 = id * 16
000234f4  add      x19, x19, #0
000234f8  mov      w1, w22               ; port
000234fc  add      x19, x19, #0x48        ; -> spa_port_attr_table
00023500  add      x0, x29, #0x34
00023504  add      x19, x19, x2
00023508  ldr      w2, [x19, #0xc]        ; table[id].spa_reg
0002350c  bl       do_spa_port_attr_tbl_get
...
0002352c  ldp      w0, w2, [x19, #4]      ; table[id].mask, table[id].bit_offset
...
0002354c  mov      w1, w22               ; port
00023554  bl       do_spa_port_attr_tbl_set
```

So the descriptor table `spa_port_attr_table` (`.data` 0x1978, 74 entries of 16 bytes) is
`{ id, mask, bit_offset, spa_reg }`, and an "attribute id" selects
`(mask, bit_offset)` inside **one SPA register** (`spa_reg`).

Full dump of the 74 entries (`staging/ctl/spaattrmap.py <ko> table`); ids 6, 15, 18 and 24
are zero-mask placeholders and are omitted below:

```
  id      mask        bit_off  spa_reg  bits(mask)@off
    0  0x00000001      0      0   0
    1  0x00003fff      1      0   1..13
    2  0x00000001     30      0   30
    3  0x0000003f     15      0   15
    4  0x00000007     21      0   21
    5  0x0000003f     24      0   24
    7  0x00000001     31      0   31
    8  0x00000007      0      1   0..2
    9  0x000001ff      3      1   3..8
   10  0x00000003     12      1   12
   11  0x000001ff     14      1   14
   12  0x00000001     23      1   23
   13  0x00000001     24      1   24
   14  0x00000007     25      1   25
   16  0x00000fff     18      2   18
   17  0x00000003     30      2   30
   19  0x00ffffff      0      4   0..23
   20..23 0xffffffff  0      5..8
   25  0x00000001     19      9   19
   26  0x00000003     17      9   17
   27  0x00000003     15      9   15
   28  0x00000001     23      9   23
   29  0x00000001     14      9   14
   30  0x00000003     12      9   12
   31  0x00000003     10      9   10
   32  0x00000001     22      9   22
   33  0x00000001      9      9   9
   34..41 (rate limit fields)    9
   42  0x0fffffff      0     10
   43  0x00000007     28     10
   44  0x00ffffff      0     11
   45  0x000000ff     24     11
   46  0x0000ffff      0     12
   47  0x0000ffff     16     12
   48..55 (8 x 3 bits)  0..21  13
   56..63 (8 x 3 bits)  0..21  14
   68  0xffffffff      0     16
   69  0xffffffff      0     17
   70  0xffffffff      0     18
   71  0xffffffff      0     19
   72  0x0000ffff     12     15
```

Three ids matter for this task:

| attribute id | SPA register | meaning |
|---|---|---|
| `0x45` (69) | **17** | the broadcast-flood register |
| `0x46` (70) | **18** | the multicast / unknown-DA flood register |
| `0x47` (71) | **19** | the port attribute word |

### 1.2 The 20 named fields of the attribute word

Each `port_attr_def_<field>_set()` does the same four things:

1. `spa_port_attr_tbl_get(port, &attr)` — read the register,
2. patch **one byte** of it (`ldrb` / `strb`),
3. `spa_port_attr_tbl_set(port, <id>, &attr)` — write it back,
4. mirror the same byte/bit into **ERAM SDT table 0x7d**, word 0.

Example, `port_attr_def_brd_flood_en_set` (0xbb658):

```
000bb65c  mov      w2, #0x47                ; attribute id -> SPA reg 19
...
000bb6a4  bl       spa_port_attr_tbl_get    ; SPA buffer at [x29,#0x44]
000bb6a8  ldrb     w1, [x29, #0x47]         ; byte 3 of the 32-bit word
000bb6ac  bfi      w1, w23, #1, #1          ; -> bit 24+1 = bit 25
000bb6b0  strb     w1, [x29, #0x47]
...
000bb6f4  bl       spa_port_attr_tbl_set
000bb6fc  add      x3, x29, #0x48           ; ERAM buffer at [x29,#0x48]
000bb708  mov      w0, #0x7d
000bb70c  bl       se_eram_table_get
000bb714  ldrb     w0, [x29, #0x4b]         ; byte 3 of word 0
000bb724  bfi      w0, w23, #1, #1          ; same bit, mirrored
000bb730  bl       se_eram_table_set
```

Buffer bases: **SPA buffer at `x29+0x44`** (bytes 0x44..0x47 = word 0 bytes 0..3) and
**ERAM 0x7d buffer at `x29+0x48`** (bytes 0x48..0x4b = word 0 bytes 0..3). Every setter
therefore touches **word 0** of the 128-bit ERAM entry, never word 3.

Absolute bit positions recovered from all 25 `port_attr_def_*_set` functions
(`staging/ctl/spaattrmap.py <ko> setters`, plus the raw `bfi`/`bfxil`/`strh`/`ldrh`
instructions in `staging/ctl/spaattr.dis`):

| bits | width | field | setter symbol | setter addr | ERAM 0x7d mirror |
|---|---|---|---|---|---|
| 0..5 | 6 | `unk_da_flood_num` | `port_attr_def_unk_da_flood_num_set` | 0xbba30 | word0 bits 0..5 |
| 13 | 1 | `route_mode_en` | `port_attr_def_route_mode_en_set` | 0xbc1e0 | word0 bit 13 |
| 14 | 1 | `flag_success_sfu_prcecss` | `port_attr_def_flag_success_sfu_prcecss_set` | 0xbbff4 | word0 bit 14 |
| 15 | 1 | `flag_success_8021x` | `port_attr_def_flag_success_8021x_set` | 0xbbe08 | word0 bit 15 |
| 16 | 1 | `en_8021x` | `port_attr_def_en_8021x_set` | 0xbbc1c | word0 bit 16 |
| 17 | 1 | `mirror_en` | `port_attr_def_mirror_en_set` | 0xbacbc | word0 bit 17 |
| 18 | 1 | `input_vlan_check_en` | `port_attr_def_input_vlan_check_en_set` | 0xbaad0 | word0 bit 18 |
| 19 | 1 | `src_is_extend_port_flag` | `port_attr_def_src_is_extend_port_flag_set` | 0xbc3cc | word0 bit 19 |
| 20 | 1 | `unk_unida_ratelimit_en` | `port_attr_def_unk_unida_ratelimit_en_set` | 0xbb46c | word0 bit 20 |
| 21 | 1 | `unk_sa_ratelimit_en` | `port_attr_def_unk_sa_ratelimit_en_set` | 0xbaea8 | word0 bit 21 |
| 22 | 1 | `brd_ratelimit_en` | `port_attr_def_brd_ratelimit_en_set` | 0xbb094 | word0 bit 22 |
| 23 | 1 | `mul_ratelimit_en` | `port_attr_def_mul_ratelimit_en_set` | 0xbb280 | word0 bit 23 |
| 24 | 1 | `ratelimit_en` | `port_attr_def_ratelimit_en_set` | 0xba8e4 | word0 bit 24 |
| 25 | 1 | `brd_flood_en` | `port_attr_def_brd_flood_en_set` | 0xbb658 | word0 bit 25 |
| 26 | 1 | `mul_flood_en` | `port_attr_def_mul_flood_en_set` | 0xbb844 | word0 bit 26 |
| 27 | 1 | `isolate_en` | `port_attr_def_isolate_en_set` | 0xba6f8 | word0 bit 27 |
| 28 | 1 | `da_lookup_en` | `port_attr_def_da_lookup_en_set` | 0xba50c | word0 bit 28 |
| 29 | 1 | `mac_limit_full_flag` | `port_attr_def_mac_limit_full_flag_set` | 0xba320 | word0 bit 29 |
| 30 | 1 | `sa_lookfail_drop_en` | `port_attr_def_sa_lookfail_drop_en_set` | 0xba134 | word0 bit 30 |
| 31 | 1 | `sa_lookup_learn_en` | `port_attr_def_sa_lookup_learn_en_set` | 0xb9f4c | word0 bit 31 |

All 25 setters use attribute id `0x47` **except** the three flood group-id setters
(`brd`/`mul`/`unk_da`), which use `0x45`/`0x46`/`0x46`, and the two flood-count setters
(`brd_flood_num`, `mul_flood_num`), which use `0x45`.

**Bits 6..12 (7 bits) have no setter and are unexplained.** All 25 `port_attr_def_*_set`
functions were enumerated (52 `port_attr_def_*` symbols = 25 setters + 25 getters +
`_init` + `_show`), and the only other writer of id `0x47` in `np_133.ko` is
`port_attr_def_init` (0xbcf34), which writes **0**. So bits 6..12 of register 19 are
never written by the vendor module at all.

### 1.3 Decode of the live values

Sources used: `staging/ctl/vlive9132-cpu-20261021.txt` (the vendor's own `tm_debug`
dump of the live 4.19 system, `spa_port_attr_smac_0` = PPU 1 and `spa_port_attr_xmac_0`
= PPU 5), and the mainline board's debugfs dump quoted in the task.

#### `0x96040001` — the attribute word of PPU 1..5 (vendor live, and what the driver writes)

| field | value |
|---|---|
| `unk_da_flood_num` (0..5) | **1** |
| `route_mode_en` (13) | 0 |
| `flag_success_sfu_prcecss` (14) | 0 |
| `flag_success_8021x` (15) | 0 |
| `en_8021x` (16) | 0 |
| `mirror_en` (17) | 0 |
| `input_vlan_check_en` (18) | **1** |
| `src_is_extend_port_flag` (19) | 0 |
| `unk_unida_ratelimit_en` (20) | 0 |
| `unk_sa_ratelimit_en` (21) | 0 |
| `brd_ratelimit_en` (22) | 0 |
| `mul_ratelimit_en` (23) | 0 |
| `ratelimit_en` (24) | 0 |
| `brd_flood_en` (25) | **1** |
| `mul_flood_en` (26) | **1** |
| `isolate_en` (27) | 0 |
| `da_lookup_en` (28) | **1** |
| `mac_limit_full_flag` (29) | 0 |
| `sa_lookfail_drop_en` (30) | 0 |
| `sa_lookup_learn_en` (31) | **1** |
| bits 6..12 | 0 (unexplained, unused) |

Reading: *learn, look up the destination MAC, flood broadcast and multicast, check the
ingress VLAN, and flood one unknown-DA destination.* That is a normal learning bridge
port. The driver's `#define ZX279133_PPU_ATTR_LIVE BIT(0)` comment
(`zx279133-np.c:1845-1867`) is **wrong in its reasoning but right in its effect**: bit 0
is not an anonymous bit, it is `unk_da_flood_num` = 1.

#### `0x00060000` — PPU 0 (PON0) on the mainline board

| field | value |
|---|---|
| `unk_da_flood_num` (0..5) | 0 |
| `mirror_en` (17) | **1** |
| `input_vlan_check_en` (18) | **1** |
| everything else | 0 |

Note: this is the **bootloader** value plus the driver's own
`zx279133_mf_port_attr_set(eth, 0, 14, BIT(17) | BIT(18))`
(`zx279133-np.c:1745`), *not* the vendor's runtime value. The vendor's live PON0 word is
`0x90040000` (`staging/ctl/msg-np-baseline.txt:22`), i.e. `sa_lookup_learn_en`,
`da_lookup_en` and `input_vlan_check_en` set and **no** flood enables. The difference
matters: the vendor's PON stack re-programs port 0 after `switch.ko`'s init, so the
flood bits `switch.ko` put there are overwritten.

#### `0x148e0000` — PPU 6 (XMAC1) on the mainline board

| field | value |
|---|---|
| `unk_da_flood_num` (0..5) | 0 |
| `mirror_en` (17) | **1** |
| `input_vlan_check_en` (18) | **1** |
| `src_is_extend_port_flag` (19) | **1** |
| `mul_ratelimit_en` (23) | **1** |
| `mul_flood_en` (26) | **1** |
| `da_lookup_en` (28) | **1** |
| `sa_lookup_learn_en` (31) | 0 |

Multicast floods but broadcast does not, and there is **no SA learning** — an
uplink-style port. (Also a bootloader value: the driver never writes PPU 6.)

#### `0x00042000` — PPU 10 (XMAC_B)

`route_mode_en` (13) = **1**, `input_vlan_check_en` (18) = **1**, all else 0.

#### `0x00040000` — PPU 15 (CPU trap port)

`input_vlan_check_en` (18) = **1** only. No learning, no lookup, no flooding — as
expected for a trap destination.

### 1.4 What is *not* explained

* **Bits 6..12 of register 19** (7 bits): no setter exists in `np_133.ko`. They read 0
  on every live port, so they are very likely unused.
* The driver comment at `zx279133-np.c:1907-1908` says *"0x011 USER_DEF_AREA1 — where the
  vendor's flood tables live / 0x012 USER_DEF_AREA2 — where the vendor's flood group ids
  live"*. **That is wrong.** Per the descriptor table above, id `0x2c` → register 11 is
  written by `spa_set_ip_spd_limit_mode()` and id `0x2e` → register 12 by
  `spa_set_ip_spd_limit_vld()`. The flood group ids are **not** in registers 11/12; see Q3.
* The driver's comment at `zx279133-np.c:1906` says register 9 holds "the IP rate-limit
  fields and TAG_FILTER(24..29)". Only the TAG_FILTER part is right (id `0x29` → reg 9,
  mask `0x3f`, offset 24); the rate-limit fields are registers 10/11/12.

---

## Q2 — Which per-port SPA registers does the vendor write that the driver does not?

### 2.1 The vendor's write path

`sw_init_switch()` (`switch.ko` 0x2e74) calls, in this order:

```
00002f10  bl   sw_init_port_protocol_action
00002f14  bl   sw_set_default_vlan_check_ctrl
00002f18  bl   sw_init_port_isolate
00002f1c  bl   sw_init_mac_lookup_ctrl
00002f20  bl   sw_init_flood_ctrl
00002f24  bl   sw_init_omci_trap_qid_init
00002f28  bl   sw_init_port_route_mode
00002f2c  bl   sw_reg_set_onu_swcap
00002f30  bl   sw_mac_table_op_init
00002f34  bl   sw_multi_config_init
```

Each of those lands in `np_133.ko` and lands on a per-port SPA register:

| init step | vendor helper | what it writes | SPA register |
|---|---|---|---|
| protocol action | `tm_port_protocol_pktdeal_set` → `port_reason_action_table_set` | ERAM 0x18, not a SPA register | — (ERAM) |
| default VLAN check | `tm_vlan_check_ena_set` → `port_attr_def_input_vlan_check_en_set` | attribute word | **19** |
| port isolate | `tm_port_set_port_isolate_enable` → `port_attr_def_isolate_en_set` | attribute word | **19** |
| port isolate | `tm_port_set_port_isolate` → `port_isolate_table_set` | ERAM 0x1b | — (ERAM) |
| MAC lookup | `zxic_api_sw_set_sa_lookup_learn_en` → `port_attr_def_sa_lookup_learn_en_set` | attribute word | **19** |
| MAC lookup | `zxic_api_sw_set_mac_limit_full_flag` → `port_attr_def_mac_limit_full_flag_set` | attribute word | **19** |
| MAC lookup | `zxic_api_sw_set_da_lookup_en` → `port_attr_def_da_lookup_en_set` | attribute word | **19** |
| flood ctrl | `zxic_api_sw_port_{mul,unk_da,brd}_flood_dest_port` | flood registers | **17**, **18** |
| flood ctrl | `zxic_api_sw_port_{mul,unk_da,brd}_flood_en` | attribute word | **19** |
| omci trap qid | `tm_reason_map_cpu_qid_set` → `reason_cpuqid_map_table_set` | ERAM 0x19 | — (ERAM) |
| route mode | `tm_port_route_mode_set` → `port_attr_def_route_mode_en_set` | attribute word | **19** |
| route mode | `tm_port_route_mode_set` → `port_bind_table_set` | ERAM 0x24 | — (ERAM) |

Everything else per-port is done by `spa_init()` (`np_133.ko` 0x27834) and the static
setters next to it.

### 2.2 Complete per-port SPA register map of `np_133.ko`

From `staging/ctl/spaids.py`, which scans every `bl spa_port_attr_tbl_set` in `.text`,
recovers the attribute id and maps it through the descriptor table:

| SPA reg | attribute id(s) | what it is | who writes it |
|---|---|---|---|
| 0 | 0, 1, 3, 4 | the port's **entry program**: TLB_VLD(0), PC(1..14), FLOWNUM(15..20), COS(21..23), OUTPUT_PORT(24..29), NPPU_DIR_EN(30), CRC_ADD_EN(31) | `spa_init`, `ppu_msoft_debug_dev_ioctl` |
| 1 | 8, 9, 0xa, 0xb, 0xc, 0xd, 0xe | queue/priority config: ICU_COS_GET_EN(0..2), ICU_PRI_CFG(3..11), VLAN_FILTER_MOD(12..13), QUEUE_ID(14..22), BPDU_TRAP_EN(23), PAE_TRAP_EN(24), DFT_PRI(25..27) — i.e. bits 0..27; bit 28 ("LLC_EN" in the driver's comment) has **no** descriptor entry (id `0x0f` is a zero-mask placeholder) and is the driver's own guess | `spa_init`, `spa_set_pt_pkt_filter`, `spa_set_pt_dft_vlan_pri`, `tm_port_802x_authen_set` |
| 2 | 0x10 | PORT_PVID_UNTAG(18..29) — the untag SVID | `spa_set_pt_upstream_untag_svid` |
| 9 | 0x29 | TAG_FILTER(24..29) — the VLAN filter | `spa_set_pt_vlan_filter`, `spa_set_tag_filter` |
| 10 | 0x2a, 0x2b | bits 0..30 (rate limit / per-port filter) | `spa_init` |
| 11 | 0x2c | IP speed-limit mode (24 bits) | `spa_set_ip_spd_limit_mode` |
| 12 | 0x2e | IP speed-limit valid (16 bits) | `spa_set_ip_spd_limit_vld` |
| 13 | 0x30..0x37 | 8 x 3-bit fields (offsets 0,3,6,9,12,15,18,21) | static setters 0x235a0.. (HOL / priority map A) |
| 14 | 0x38..0x3f | 8 x 3-bit fields (offsets 0,3,6,9,12,15,18,21) | static setters 0x237b0.. (HOL / priority map B) |
| 15 | 0x48 (bits 12..15) | — | **never written by `np_133.ko`** |
| 16 | 0x44 | undocumented 32-bit word | `port_attr_def_init` (writes 0) |
| 17 | 0x45 | **broadcast-flood register** (see Q3) | `port_attr_def_brd_flood_{group_id,num}_set`, `port_attr_def_mul_flood_num_set` |
| 18 | 0x46 | **multicast / unknown-DA flood register** (see Q3) | `port_attr_def_{mul,unk_da}_flood_group_id_set` |
| 19 | 0x47 | **the port attribute word** (see Q1) | 20 `port_attr_def_*_set` functions |

### 2.3 The diff

Driver set: **{0, 2, 9, 10, 13, 17, 18, 19}** (`zx279133_9132_vendor_words[]` plus the
two flood words in `zx279133-np.c:1986-2007`).

| SPA reg | vendor writes it? | driver writes it? | live value on the vendor board (PPU 1 / PPU 5) | verdict |
|---|---|---|---|---|
| 0 | yes | **yes** (`0xa6a5`) | `0xa6a5` | ok |
| 1 | yes (`spa_init` + `spa_set_pt_*`) | no | `0x88f` | **benign** — the driver leaves the bootloader/vendor value alone. Note `QUEUE_ID(14..22) = 0` in it. |
| 2 | yes | **yes** (`0`) | `0` | ok |
| 9 | yes | **yes** (`0`) | `0` | ok |
| 10 | yes (`spa_init`) | **yes** (`0x0fa0000e`) | `0x0fa0000e` | ok |
| 11 | yes (`spa_set_ip_spd_limit_mode`) | no | `0` | **benign** — live value is 0 |
| 12 | yes (`spa_set_ip_spd_limit_vld`) | no | `0` | **benign** — live value is 0 |
| 13 | yes (static setters) | **yes** (`0x00008008`) | `0x00008008` | ok |
| 14 | yes (static setters) | no | `0` | **benign** — live value is 0 |
| 15 | no (`np_133.ko` never writes it) | no | `0` | ok |
| 16 | yes (`port_attr_def_init` → 0) | no | `0` | **benign** — live value is 0 |
| 17 | yes | **yes** (flood word) | see Q3 | **see Q3 — the group id is right, the table behind it is not written** |
| 18 | yes | **yes** (flood word) | see Q3 | **see Q3** |
| 19 | yes | **yes** (`0x96040001`) | `0x96040001` | ok in the SPA memory; **ERAM 0x7d word 1/2 are not mirrored** (see Q3) |

**Conclusion for Q2: the driver writes the right set of per-port SPA registers, and every
register it omits reads 0 on the vendor's live system.** The gap is not in the SPA
register file — it is in the ERAM tables (Q3).

The vendor live values used for this comparison are the vendor's own `tm_debug` dump,
`staging/ctl/vlive9132-cpu-20261021.txt`:

```
spa_port_attr_xmac_0 (ram_id=5):  reg0=0xa6a5 reg1=0x88f reg2=0x0  reg9=0x0
                                 reg10=0xfa0000e reg11=0x0 reg12=0x0 reg13=0x8008
                                 reg14=0x0 reg15=0x0 reg16=0x0
                                 reg17=0x5400410 reg18=0x1400940 reg19=0x96040001
```

---

## Q3 — The flood "group id" mechanism

### 3.1 `a17` and `a18` are not bitmaps

The driver calls them "the port's broadcast flood word" and "the port's
unknown-unicast / multicast flood word" (`zx279133-np.c:1995-1999`) and treats them as
destination bitmaps. They are **not** bitmaps. Decoded from the vendor's setters:

**SPA register 17 — the broadcast-flood register**

| bits | width | field | setter |
|---|---|---|---|
| 4..9 | 6 | `brd_flood_num` (how many destinations) | `port_attr_def_brd_flood_num_set` (0xbcd38) |
| 10..15 | 6 | `mul_flood_num` | `port_attr_def_mul_flood_num_set` (0xbcb4c) |
| 16..31 | 16 | `brd_flood_group_id` | `port_attr_def_brd_flood_group_id_set` (0xbc970) |
| 0..3 | 4 | unused | — |

**SPA register 18 — the multicast / unknown-DA flood register**

| bits | width | field | setter |
|---|---|---|---|
| 0..15 | 16 | `mul_flood_group_id` | `port_attr_def_mul_flood_group_id_set` (0xbc794) |
| 16..31 | 16 | `unk_da_flood_group_id` | `port_attr_def_unk_da_flood_group_id_set` (0xbc5b8) |

The `group id` setters are 16-bit stores, not bitmaps:

```
port_attr_def_brd_flood_group_id_set (0xbc970):
000bc974  mov      w2, #0x45              ; attribute id -> SPA reg 17
000bc994  uxth     w22, w1                ; 16-bit group id
000bc9a8  add      x0, x29, #0x44
000bc9c0  bl       spa_port_attr_tbl_get
000bc9c4  strh     w22, [x29, #0x46]      ; bytes 2..3 -> bits 16..31 of reg 17
000bc9fc  ldr      w0, [x29, #0x44]
000bca08  bl       spa_port_attr_tbl_set
000bca10  add      x3, x29, #0x48
000bca20  bl       se_eram_table_get      ; ERAM 0x7d
000bca38  strh     w22, [x29, #0x52]      ; bytes 10..11 -> word 2, bits 16..31
000bca3c  bl       se_eram_table_set
```

### 3.2 The group id formula

`port_attr_def_init()` (`np_133.ko` 0xbcf34) computes the group id of every port from the
port number and writes it into registers 19/18/17/16 for all 64 ports:

```
000bcf6c  mov      w0, w19                ; w19 = port number, 0..63
000bcf70  bl       group_id_table_get_brd_flood_group_id_start
000bcf74  bfi      x23, x0, #0x30, #0x10
000bcf78  mov      w0, w19
000bcf7c  bl       group_id_table_get_mul_flood_group_id_start
000bcf80  bfxil    x22, x0, #0, #0x10
000bcf84  mov      w0, w19
000bcf88  bl       group_id_table_get_unk_da_flood_group_id_start
000bcf8c  bfi      x22, x0, #0x10, #0x10
000bcf90  mov      w1, w19
000bcf94  mov      w2, #0x47
000bcf98  mov      w0, #0
000bcf9c  bl       spa_port_attr_tbl_set   ; reg 19 = 0
000bcfac  mov      w0, x22
000bcfb0  mov      w2, #0x46
000bcfb4  bl       spa_port_attr_tbl_set   ; reg 18 = group ids
000bcfc8  lsr      x0, x23, #0x20
000bfcc  bl       spa_port_attr_tbl_set   ; reg 17 = brd group id
000bcfdc  mov      w2, #0x44
000bcfe0  bl       spa_port_attr_tbl_set   ; reg 16 = 0
```

and the three helpers are three instructions each:

```
group_id_table_get_unk_da_flood_group_id_start (0xb9864):  lsl w0, w0, #6      -> p << 6
group_id_table_get_brd_flood_group_id_start    (0xb986c):  add w0, w0, #0x10
                                                           lsl w0, w0, #6      -> (p+0x10) << 6
group_id_table_get_mul_flood_group_id_start    (0xb9878):  add w0, w0, #0x20
                                                           lsl w0, w0, #6      -> (p+0x20) << 6
```

**So for SPA memory id `p`:**

| flood type | group id | ERAM 0x1f index range |
|---|---|---|
| unknown-DA | `p << 6` | `p*64` .. `p*64+63` |
| broadcast | `(p + 0x10) << 6` | `(p+16)*64` .. `+63` |
| multicast | `(p + 0x20) << 6` | `(p+32)*64` .. `+63` |

**This is confirmed against the live values, exactly.** For PPU 1..5 the live registers 17
and 18 are (vendor live dump; the mainline board shows the same numbers because the
driver copies them):

| PPU | reg 17 | → `brd_flood_group_id` | reg 18 | → `mul_flood_group_id` / `unk_da_flood_group_id` |
|---|---|---|---|---|
| 1 | `0x04400410` | `0x0440` = 1088 = (1+0x10)<<6 ✓ | `0x00400840` | `0x0840` = 2112 = (1+0x20)<<6 ✓ / `0x0040` = 64 = 1<<6 ✓ |
| 2 | `0x04800410` | 1152 = (2+0x10)<<6 ✓ | `0x00800880` | 2176 ✓ / 128 ✓ |
| 3 | `0x04c00410` | 1216 ✓ | `0x00c008c0` | 2240 ✓ / 192 ✓ |
| 4 | `0x05000410` | 1280 ✓ | `0x01000900` | 2304 ✓ / 256 ✓ |
| 5 | `0x05400410` | 1344 ✓ | `0x01400940` | 2368 ✓ / 320 ✓ |

All five match to the bit. The low half of reg 17 (`0x0410`) is `brd_flood_num = 1`
(bits 4..9) and `mul_flood_num = 1` (bits 10..15).

### 3.3 Where the table physically lives: **ERAM SDT table 0x1f**

`group_id_table_data_set()` (`np_133.ko` 0xb9924):

```
000b992c  add      x3, x29, #0x40
000b9950  str      w1, [x3, #-0xc]!      ; the port id, at [x29,#0x34]
000b9954  mov      w1, w21               ; the index
000b9958  mov      w2, #1                ; one word
000b995c  mov      w0, #0x1f             ; <-- ERAM SDT table 0x1f
000b9960  bl       se_eram_table_set
```

`group_id_table_port_id_set()` (0xb99c4) packs the destination port:

```
000b99d4  and      w0, w1, #0x3f         ; port id, 6 bits
000b99dc  bl       port_is_ssid_flag_get
000b99e4  orr      w1, w19, #0x80        ; bit 7 = "SSID port" flag
000b99ec  csel     w1, w1, w19, eq
000b99f0  bl       group_id_table_data_set
```

So **one 32-bit ERAM entry per group id**, holding **one destination port id in bits 0..5**
(plus a flag in bit 7). A flood group of *N* destinations is *N* consecutive entries
starting at the group id.

And that is exactly how the vendor fills it — `zxic_api_sw_port_brd_flood_dest_port()`
(`np_133.ko` 0x11e930):

```
00011e988  bl       port_attr_def_brd_flood_num_set
00011e994  cbnz     w20, #0x11e9a0        ; num != 0 ?
00011e9b0  bl       port_attr_def_brd_flood_group_id_get   ; -> current group id
00011e9c4  ldr      w0, [x29, #0x34]      ; group id
00011e9c8  ldrb     w1, [x22, x20]        ; destination port id, list[i]
00011e9cc  add      w0, w2, w0            ; index = group_id + i
00011e9d0  bl       group_id_table_port_id_set
```

⇒ **ERAM 0x1f entry index = group id + i**, value = the i-th destination port id.

### 3.4 The table's geometry, and that the driver already allocates it

`g_core_sdt_info` (`np_133.ko` `.data` 0x11be0, 53 entries of 16 bytes) contains

```
  sdt 0x1f type=1 a=0x1 b=0x4000     ; ERAM, 32-bit entries, 16384 entries
  sdt 0x7d type=1 a=0x3 b=0x40       ; ERAM, 128-bit entries, 64 entries
```

and the driver's own list (`zx279133-np.c:3104-3135`) has the **identical** entries:

```c
{ 0x1f, 1, 0x1, 0x4000 },     /* line 3122 */
{ 0x7d, 1, 0x3, 0x40 },       /* line 3130 */
```

So the driver allocates ERAM table 0x1f with the right base and the right entry width
(32 bits), and its `zx279133_eram_table_set_bits(eth, 0x1f, index, 32, value)` helper can
already address any entry of it. **It simply never calls it.** A grep of the driver
source for `0x1f` finds only bit masks and a port-type index — there is no group-id
table writer.

### 3.5 What the vendor puts into it on the stock configuration

`sw_init_flood_ctrl()` (`switch.ko` 0x2780):

```
000027b0  ldr      w0, [x20]              ; g_wan_port_id
000027b4  strb     w0, [x21, #-0x10]!     ; the destination list: ONE byte
000027bc  bl       zxic_api_sw_port_mul_flood_dest_port   ; (wan, 0, list)
000027c8  mov      w1, #0
000027cc  bl       zxic_api_sw_port_mul_flood_en          ; (wan, 0)
000027d8  mov      w23, #0
000027dc  ldr      w0, [x24]              ; g_wan_port_id
000027e0  cmp      w0, w23
000027e4  ccmp     w19, #0, #0, ne
000027e8  b.ne     #0x2814
000027ec  mov      x2, x21
000027f0  mov      w0, w23
000027f4  mov      w1, #1                 ; one destination
000027f8  bl       zxic_api_sw_port_mul_flood_dest_port
00002804  mov      w0, w23
00002808  mov      w1, #1
0000280c  bl       zxic_api_sw_port_mul_flood_en
00002814  add      w23, w23, #1
00002818  cmp      w23, #8
0000281c  b.ne     #0x27dc
```

(the same shape is repeated for `unk_da` at 0x2838..0x286c and for `brd` at 0x2880..0x28b4.)

* The destination list is a **single byte** containing `g_wan_port_id`. Everything else in
  that stack block is garbage, but `num = 1` so only byte 0 is read.
* ⇒ **every flood group on the vendor board contains exactly one member: the WAN port.**
* `g_wan_port_id` is a global in `switch.ko` that `switch.ko` only ever *reads*; on this
  board it is **0** (PON0) — see `ci-build-repo/docs/30` §"0x1f" and the comment there
  that the only writer, `zte_api_sw_lan_up_set()`, is never called because the upmode is
  0x40.
* ⇒ the entries the vendor writes into ERAM 0x1f are, on this board, **all zero**:

| ERAM 0x1f index | written by | value on this board |
|---|---|---|
| `p << 6` (unknown-DA group of port p) | `zxic_api_sw_port_unk_da_flood_dest_port` | 0 (= port 0) |
| `(p+0x10) << 6` (broadcast group of port p) | `zxic_api_sw_port_brd_flood_dest_port` | 0 |
| `(p+0x20) << 6` (multicast group of port p) | `zxic_api_sw_port_mul_flood_dest_port` | 0 |

with `p` running over 0..7. For port 0 (the WAN port) those three indices are exactly
`0x000`, `0x400` and `0x800`, matching `docs/30`.

**Disagreement with `docs/27`/`docs/30`, stated explicitly.** `docs/27` §4.1 describes the
loop as `for p in 0..7, p != wan`. The disassembly does not support that: the skip
instruction is `b.ne` after `ccmp w19, #0, #0, ne`, whose condition is
`(wan != p) && (w19 != 0)`; `w19` is the error accumulator and is 0 on success
(`zxic_api_sw_port_mul_flood_en` returns 0 on success, `np_133.ko` 0x11fe4c), so the set
path is taken for the WAN port as well. The encodings were checked byte-wise:

```
0x27e0 = 0x6b17001f   subs wzr, w0, w23          ; cmp w0, w23
0x27e4 = 0x7a401a60   ccmp w19, #0, #0, ne       ; cond=NE, nzcv=0, Rn=w19
0x27e8 = 0x54000161   b.ne #0x2814               ; imm19=11 -> 0x27e8+44
```

This changes **nothing** for the practical conclusion (the group still holds one member,
the WAN port), but it does mean the vendor writes **24** entries into ERAM 0x1f, not
three. The "three entries" figure in `docs/30` counts only the WAN port's own groups.

### 3.6 Is the bitmap a different, legacy mechanism the chip also honours?

**No.** The evidence for "group id table only":

1. The only writer of a flood destination anywhere in `np_133.ko` is
   `group_id_table_data_set()` → `se_eram_table_set(0x1f, index, 1, buf)`. There is no
   per-port 64-bit destination mask in any SPA register: registers 17 and 18 are fully
   accounted for by the 3 + 2 fields above (bits 0..3 of register 17 are the only
   unassigned bits and are 0 on every live port).
2. Every `port_attr_def_*_set()` mirrors the field it sets into **ERAM SDT table 0x7d**
   — i.e. the vendor treats ERAM 0x7d as the copy the switch engine reads. The
   flood-related fields land in words 1 and 2 of that 128-bit entry:

   | ERAM 0x7d entry | bytes | content |
   |---|---|---|
   | word 0 | 0..3 | the whole port attribute word (SPA reg 19) |
   | word 1 | 4..7 | bits 0..15 = `mul_flood_group_id`, bits 16..31 = `unk_da_flood_group_id` |
   | word 2 | 8..11 | bits 4..9 = `brd_flood_num`, bits 10..15 = `mul_flood_num`, bits 16..31 = `brd_flood_group_id` |
   | word 3 | 12..15 | never written by any setter |

   (byte offsets 0x4c/0x4e/0x50/0x51/0x52 relative to the ERAM buffer at `x29+0x48`,
   taken from the `strh`/`ldrb`/`bfi` instructions of the five flood setters.)

   **A useful consequence: ERAM 0x7d word 1 is bit-for-bit SPA register 18, and ERAM 0x7d
   word 2 is bit-for-bit SPA register 17.** The vendor copies each 16-bit group id and
   each 6-bit count into the same bit position in both places, so the driver can build
   the ERAM words by copying the SPA registers rather than re-deriving anything:

   | ERAM 0x7d entry | SPA register | value for PPU 1 | value for PPU 5 |
   |---|---|---|---|
   | word 1 | reg 18 | `0x00400840` | `0x01400940` |
   | word 2 | reg 17 | `0x04400410` | `0x05400410` |

   (Verified mechanically with `staging/ctl/spaattrmap.py <ko> decode`.)
3. The chip therefore has to read, per port: the flood enable bits and the count from
   ERAM 0x7d word 0/word 2, the group id from ERAM 0x7d word 1/word 2, and the
   destination port ids from ERAM 0x1f.

### 3.7 What the driver does — and the two gaps

The driver **does**:
* allocate ERAM table 0x1f with the right geometry (`zx279133-np.c:3122`);
* write the right values into SPA registers 17 and 18 for PPUs 1..5
  (`zx279133_9132_vendor_flood[]`, `zx279133-np.c:2000-2007`);
* write the right attribute word into SPA register 19 **and mirror it into ERAM 0x7d
  word 0** (`zx279133_9132_mf_mirror()` → `zx279133_mf_eram_attr_set()`,
  `zx279133-np.c:1694-1719`, which sets `data[0] = attr`).

The driver **does not**:
* **(A) write ERAM table 0x1f at all** — no group ever gets a destination;
* **(B) mirror the flood fields into ERAM 0x7d words 1 and 2** — `zx279133_mf_eram_attr_set()`
  only replaces word 0 and leaves words 1..3 as they were found.

Gap (B) is the more likely immediate killer: if the switch engine reads the counts and
group ids out of ERAM 0x7d (which is what the vendor's mirroring says), then after the
driver's init ERAM 0x7d word 2 has `brd_flood_num = 0`, `mul_flood_num = 0` and
`brd_flood_group_id = 0`, and word 1 has two zero group ids — **flooding is switched off
for every port, whatever the SPA memory says.**

Gap (A) means that even with (B) fixed, the group would point at ERAM 0x1f entries that
the driver never wrote — i.e. at whatever the ERAM happened to contain (0 after a power
cut), which would be *destination port 0* (PON0), not the CPU port.

**And note what the vendor itself does:** the vendor's stock flood group also contains
only the WAN port (0). **The vendor never floods to the CPU port either** — on the vendor
board the CPU receives frames through the *protocol-action trap*, not through flooding
(`ci-build-repo/docs/27` §4). So a mainline driver that wants a UNI jack to reach the
CPU port by flooding has to *deliberately differ* from the vendor here.

---

## 4. What to do next — ranked, concrete, safe

Each item is a change to `ci-build-repo/kernel-overlays/drivers/net/ethernet/zte/zx279133-np.c`
with the vendor evidence it rests on. Nothing here has been applied; this document is
analysis only.

### 1. Mirror the flood group ids and counts into ERAM 0x7d words 1 and 2 — highest value, lowest risk

For every PPU `p` in 1..5 (the four UNI jacks and the conduit), patch the 128-bit ERAM
0x7d entry at index `p` so that:

| field | ERAM 0x7d position | value for port p |
|---|---|---|
| `mul_flood_group_id` | word 1, bits 0..15 | `(p + 0x20) << 6` |
| `unk_da_flood_group_id` | word 1, bits 16..31 | `p << 6` |
| `brd_flood_num` | word 2, bits 4..9 | 1 |
| `mul_flood_num` | word 2, bits 10..15 | 1 |
| `brd_flood_group_id` | word 2, bits 16..31 | `(p + 0x10) << 6` |

i.e. for PPU 1: word1 = `0x0040_0840`, word2 = `0x0440_0410`; for PPU 5: word1 =
`0x0140_0940`, word2 = `0x0540_0410`.

**Simplification: word 1 is exactly SPA register 18 and word 2 is exactly SPA register 17**
(see Q3.6) — the driver already has both values in hand when it writes the per-port
words, so this is a copy, not a re-derivation.

Evidence: `port_attr_def_mul_flood_group_id_set` (0xbc794) writes `strh` at buffer
`+0x4c`; `port_attr_def_unk_da_flood_group_id_set` (0xbc5b8) at `+0x4e`;
`port_attr_def_brd_flood_num_set` (0xbcd38) at `+0x50` with `bfi #4,#6`;
`port_attr_def_mul_flood_num_set` (0xbcb4c) at `+0x51` with `bfi #2,#6`;
`port_attr_def_brd_flood_group_id_set` (0xbc970) at `+0x52`.

Implementation note: `zx279133_eram_window_patch()` already exists
(`zx279133-np.c:571-596`) and does exactly this shape — read the 128-bit window, patch
one word, write it back. It needs three calls per port (word 1, word 2), or two if the
group ids and counts are packed first. It already knows the base of table 0x7d.

### 2. Populate ERAM table 0x1f with the CPU port as the flood destination

Write the destination port id of the CPU port (**PPU 5**, the 9132's XMAC0, the SGMII side
the SoC's MAC reaches it through — `zx279133-np.c:1813-1832`) into ERAM 0x1f:

| ERAM 0x1f index | value |
|---|---|
| `p << 6` (unknown-DA group of p) | 5 |
| `(p + 0x10) << 6` (broadcast group of p) | 5 |
| `(p + 0x20) << 6` (multicast group of p) | 5 |

for `p` in 1..5 (or 1..4 if the conduit should keep flooding only to the WAN).

The value format is one 32-bit word: destination port id in bits 0..5, bit 7 = "SSID
port" flag (leave 0). Evidence: `group_id_table_port_id_set` (0xb99c4) → `and #0x3f`,
optional `orr #0x80`, then `group_id_table_data_set` (0xb9924) → `se_eram_table_set(0x1f,
index, 1, buf)`.

Implementation note: `zx279133_eram_table_set_bits(eth, 0x1f, index, 32, value)` already
works for this table (bit_len 32 ⇒ `bit_off = 32*index`, word index `index & 3`,
bit position 0). The group id *is* the ERAM index — no extra scaling.

**Safety:** this is the one change that deliberately differs from the vendor. The vendor's
group contains only the WAN port (0), so on the vendor board this write *adds* the CPU
port to the flood set. It is safe in the sense that it can only cause the 9132 to send
more frames to the CPU port, which is the port the CPU is listening on. It can be undone
by writing the entry back to 0.

**Verification without a rebuild:** the driver's debugfs already supports
`echo "eg 1f <index> 32" > /sys/kernel/debug/zx279133-9132` and
`echo "es 1f <index> 32 <value>"` (`zx279133-np.c:1445-1475`). Reading entries
`0x40`, `0x440`, `0x840` (PPU 1's three groups) before and after is a seconds-long
experiment, not a 70-minute build.

### 3. Keep the flood counts consistent between the SPA memory and ERAM 0x7d

The SPA-side counts are already right (the driver writes the vendor's `0x...0410` into
register 17 and `0x96040001` into register 19, which carry `brd_flood_num = 1`,
`mul_flood_num = 1` and `unk_da_flood_num = 1`). Item 1 puts the same numbers into ERAM
0x7d. If the two ever disagree the chip will flood to the wrong group — so any change to
the counts must be made in **both** places, or better, derived from one source.

### 4. Consider writing SPA register 1's `QUEUE_ID` for the conduit

`spa_set_pt_*` writes SPA register 1, whose `QUEUE_ID` field is bits 14..22 (id `0x0b`,
mask `0x1ff`, offset 14 — present in the descriptor table but **never written by
`np_133.ko`**). The live value on every port is `0x88f`, i.e. `QUEUE_ID = 0`. If a frame
that the switch hands to the CPU port has to land on the queue the SoC's IDM reads, this
may need to be non-zero. This is speculative: it is the only per-port field in
registers 1..16 that is neither written by the vendor nor zero-by-construction, and the
vendor leaves it at 0. Low priority; do it only after items 1 and 2 are measured.

### 5. Optional: write SPA register 16 as well

`port_attr_def_init` writes 0 into register 16 (id `0x44`) for every port, and the live
value is 0, so leaving it alone is harmless. Not worth changing.

### 6. Optional: correct the driver's comments

`zx279133-np.c:1836-1843` (bit names — all correct) and `zx279133-np.c:1845-1867`
(`ZX279133_PPU_ATTR_LIVE` — bit 0 is `unk_da_flood_num`, not an anonymous bit);
`zx279133-np.c:1906-1912` (registers 9/11/12 — wrong); `zx279133-np.c:1995-1999`
("broadcast flood word"/"unknown-unicast / multicast flood word" — these are the flood
registers but their payload is group ids + counts, not bitmaps). Comment-only, no
behaviour change; worth doing so the next reader does not repeat the bitmap theory.

### Explicitly *not* recommended

* Do not write a destination bitmap into registers 17/18. There is no such field.
* Do not touch SPA registers 11/12/14/15/16: the live values are 0 and the vendor's
  writes are no-ops.
* Do not change the DSA/VLAN scheme in `zx279133-switch.c` on the strength of this
  document; nothing here says the transport-VID handling is wrong.

---

## 5. What could not be determined offline, and what would settle it

| item | why it is open | what would settle it |
|---|---|---|
| Does the switch engine read the flood count / group id from **ERAM 0x7d words 1..2** or from the **SPA registers 17/18** directly? | The vendor mirrors every field into 0x7d, which strongly implies 0x7d is the copy the engine reads, and the driver already relies on that for the attribute word. But nothing in the module proves the engine reads words 1..2 of 0x7d. | On the mainline board: write ERAM 0x7d word 2 = 0 (counts 0) while leaving SPA reg 17 alone, and see whether flooding stops. Then the converse. One debugfs write plus a traffic test. |
| The exact contents of ERAM 0x1f on the **vendor** board at run time | Only the WAN port's three groups are described in `docs/30`, and the module's write path suggests 24 entries (all zero). The vendor's own ERAM dump for table 0x1f is not in `staging/`. | Read ERAM 0x1f on the vendor board with the vendor's `tm_debug` (the `es`/`eg` console the driver copies). The mainline driver's `eg 1f <index> 32` is the equivalent once the base is right. |
| The value of `g_wan_port_id` at the moment `sw_init_flood_ctrl()` runs | `switch.ko` only reads it; the only writer (`zte_api_sw_lan_up_set()`) is never called on this board, so it is 0 — but that is `docs/30`'s conclusion, not something re-derived here from the writer. | Read the symbol on the vendor board (`tm_debug` or `/proc/kallsyms` + the variable's address). |
| Bits 6..12 of SPA register 19 | No setter exists in `np_133.ko`; they are probably reserved. | Read them on the vendor board (they read 0 everywhere observed) and look for a datasheet. |
| Whether the chip honours a *legacy* bitmap flooding path at all | Nothing in `np_133.ko` writes a per-port destination mask, so the answer is very likely "no", but the chip's behaviour cannot be proven from the module. | A live experiment: set `brd_flood_num = 1` with the group id pointing at an ERAM 0x1f entry that holds a different port id, and see where the frame comes out. If a bitmap also existed, setting the "bitmap" half of the register would change the outcome. |
| Whether the CPU port (PPU 5) should be in the flood group at all | The vendor deliberately keeps it out and uses the protocol-action trap instead. Whether flooding to it is safe alongside the trap is a system decision, not a fact. | Measure: with the trap path active, count frames arriving on the CPU port with and without ERAM 0x1f entries pointing at port 5. |

---

## 6. Evidence index

| claim | vendor symbol | address |
|---|---|---|
| attribute id → (mask, bit offset, SPA register) | `spa_port_attr_table` / `spa_port_attr_tbl_set` | `.data` 0x1978 / 0x2343c |
| the port attribute word's 20 fields | `port_attr_def_*_set` | 0xb9f4c .. 0xbce78 |
| flood group id and count fields | `port_attr_def_{brd,mul,unk_da}_flood_{group_id,num}_set` | 0xbc5b8 .. 0xbce78 |
| group id formula | `port_attr_def_init` + `group_id_table_get_*_group_id_start` | 0xbcf34 / 0xb9864, 0xb986c, 0xb9878 |
| the group id table | `group_id_table_data_set` → `se_eram_table_set(0x1f, …)` | 0xb9924 |
| one destination per group entry | `group_id_table_port_id_set` | 0xb99c4 |
| group id → ERAM index | `zxic_api_sw_port_brd_flood_dest_port` | 0x11e930 |
| the vendor's flood init | `sw_init_flood_ctrl` | `switch.ko` 0x2780 |
| the vendor's per-port init | `sw_init_switch` | `switch.ko` 0x2e74 |
| the ERAM table list | `g_core_sdt_info` | `np_133.ko` `.data` 0x11be0 |
| the vendor's live per-port values | `spa_port_attr_xmac_0` / `spa_port_attr_smac_0` | `staging/ctl/vlive9132-cpu-20261021.txt` |
| the driver's current tables | `zx279133_9132_vendor_words[]`, `zx279133_9132_vendor_flood[]` | `zx279133-np.c` 1986 / 2000 |
| the driver's ERAM list | `zx279133_core_sdt_info[]` | `zx279133-np.c` 3104 |
| the driver's flood gap | `zx279133_mf_eram_attr_set` (word 0 only) | `zx279133-np.c` 1694 |
