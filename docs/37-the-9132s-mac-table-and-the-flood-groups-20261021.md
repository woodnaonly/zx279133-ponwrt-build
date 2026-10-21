# re-fdb — the 9132's MAC forwarding table: where it lives, what the vendor puts into it, and what the mainline DSA driver is missing

Offline reverse engineering of the shipped vendor modules. **No board, no serial console,
no network access, no writes to `ci-build-repo`, no git write command.** Everything below
is derived from reading code and data out of the two `.ko` files and from the driver's
own source.

| input | what it is |
|---|---|
| `pon/vendor-modules/switch.ko` (853 280 B, md5 `993f012407847954805d89efaf51110d`) | the switch glue: the `sw_mac_*` library and `sw_init_switch()` |
| `pon/vendor-modules/np_133.ko` (5 208 496 B, md5 `92b68212542930114769e3c69efe64b6`) | the 9132's own driver: the hash/CAM engine, the ERAM tables, the SPA registers. `staging/vendor-full/kmodule/np_133.ko` is byte-identical, so there is no ambiguity about the reference. |
| `ci-build-repo/kernel-overlays/drivers/net/ethernet/zte/zx279133-{np,switch}.c`, `zx279133.h` | the driver under review (canonical repo; `ci-wanport` ignored) |
| `staging/swspa.py`, `staging/ctl/swat.py` | the ELF/relocation-aware disassemblers |

Two earlier reports in this directory are prerequisites and are **not** repeated here:
`re-idm-rx.md` (the SoC-side receive setup) and `re-spa-attrs.md` (the per-port SPA
register file, the port attribute word, and the flood "group id" mechanism).

**Reading guide.** Every claim is tied to a vendor symbol and an address. Where the
evidence is ambiguous — and it is ambiguous in one important place, the byte order of
the FDB entry on read-back — the text says so and gives both sides.

---

## 0. TL;DR — the top three findings

1. **The 9132's MAC forwarding table is not an ERAM table at all.** It is **SDT 0x32**,
   and in the vendor's own table list it is **type 3 = hash**, not type 1 = ERAM:
   `g_core_sdt_info` (`np_133.ko` `.data` 0x11be0) index 37 is
   `sdt=0x32 type=3 a=0x8 b=0x64`, and the driver's own
   `zx279133_core_sdt_info[]` (`zx279133-np.c:3178`) has the byte-identical entry
   `{ 0x32, 3, 0x8, 0x64 }`. The table lives in the 9132's **alg (hash/CAM) engine**,
   not in the ERAM pool that `tools/eram-map.py` prints. **Consequence:** the driver's
   `zx279133_eram_table_set_bits()` cannot write it — `zx279133_eram[0x32].base` and
   `.bit_len` are only filled for type 1/5 tables, so the helper returns `-EINVAL` for
   0x32.

2. **`sw_mac_add_cpu_macaddress` does not touch the FDB.** The chain is
   `switch.ko:sw_mac_add_cpu_macaddress` (0x4ab8) → `np_133.ko:tm_onu_mac_addr_set`
   (0x1128b0) → `spa_set_Onu_MacAddr` (0x24de0) → `spa_local_onu_mac_set` (0x22adc) →
   `np_onu_reg_write(4|5, …)`: the CPU port's MAC goes into the **ONU MAC address
   registers** (the ONT's own source MAC) and into a *software* table
   `g_Cpu_Mac` in `switch.ko`'s `.bss`. The vendor never writes a static FDB entry
   for it. Worse for the "does the vendor do it" question: **the whole FDB API family
   in `np_133.ko` is an 8-byte no-op stub** — `zxic_api_sw_mac_add_port_macaddress`
   (0x128498), `zxic_api_sw_mac_del_port_macaddress` (0x1284c0),
   `zxic_api_sw_mac_get_mactable` (0x1285e8), `zxic_api_sw_mac_clear_port_mactable`
   (0x1285a8), `zxic_api_sw_mac_set_onu_agingtime` (0x128464),
   `zxic_api_sw_mac_set_port_learnlimit` (0x1284d8), `tm_mactable_add_set` (0x128660),
   `tm_hash_mode_set` (0x128670) — all eight bytes of `mov w0, #0 ; ret`.
   The **only** live FDB write path in the whole vendor stack is the
   `/proc/pon/tm_debug/tm_mactable_fdb_add` console command, which calls
   `tm_mactable_fdb_add_set` (0x112a28) → `mac_table_set` (0xb5440) →
   `se_hash_table_set(0x32, …)` (0x92b88).

3. **The mainline DSA driver has no FDB support at all, and that is not the bug.**
   `zx279133_switch_dsa_ops` (`zx279133-switch.c:1499-1513`) implements
   `port_bridge_join/leave`, `port_enable/disable`, `port_vlan_filtering`,
   `port_vlan_add/del`, `port_stp_state_set` — and **no** `port_fdb_add`,
   `port_fdb_del`, `port_mdb_add` or `port_mdb_del`; the driver has no
   `switchdev_ops` at all. So the DSA bridge's FDB (which normally carries the
   conduit's MAC on the CPU port) never reaches the 9132. **But the vendor does not
   do it either** — the vendor's live FDB was *also* pure hardware learning
   (`docs/16 §2.2`: exactly one entry, the PC's MAC, `port_id 2`, `vid 0` — a
   *learned* entry; the board's own MAC `d0:45:8d:84:08:fc` was **not** in it).
   The 9132's FDB is filled by its own SA-learning engine, which the driver *does*
   enable: it writes the vendor's attribute word `0x96040001` (bit 31 =
   `sa_lookup_learn_en`, bit 28 = `da_lookup_en`) to PPU 1..4 **and** to the
   conduit's PPU 5 (`zx279133-np.c:2116-2177`).

**So the honest answer to "does the mainline driver need to do the same?" is: no, not
the same way.** The vendor installs nothing statically into the FDB, so there is
nothing for the driver to replicate. What the driver *is* missing is the fallback
that keeps a not-yet-learned unicast frame addressed to the board from being lost —
see §6.

---

## 1. The chain from `sw_mac_add_cpu_macaddress` down to the hardware

### 1.1 `switch.ko` level

`sw_mac_add_cpu_macaddress` — `switch.ko` `.text` 0x4ab8, size 0x1d4. It takes one
argument: a pointer to a structure whose **6-byte MAC is at offset 4**.

```
00004b3c  add      x6, x20, #4          ; x6 = &arg->mac[0]
00004b40  ldr      w0, [x20, #4]        ; mac[0..3]
00004b4c  stur     w0, [x29, #0x31]     ; -> stack buffer +1
00004b54  ldur     w0, [x6, #2]         ; mac[4..5]
00004b58  stur     w0, [x29, #0x33]     ; -> stack buffer +3
```

It then looks for a free slot in a **software** table and, only if the MAC is not
already there, calls into `np_133.ko`:

```
00004b5c  adrp     x0, #0x4000          ; g_Cpu_Mac (.bss 0x178, 48 bytes)
00004b60  add      x2, x0, #0
00004b64  add      x2, x2, #0x32        ; x2 = &g_Cpu_Mac[0x2a] = slot 7's MAC
00004b68  mov      w1, #0
00004b6c  mov      w5, #1
00004b70  mov      w4, #0
00004b74  add      x9, x29, #0x30
00004b78  ldrb     w7, [x2, x1]         ; existing slot byte i
00004b7c  add      x9, x9, x1
00004b80  add      x1, x1, #1
00004b84  cmp      w7, #0
00004b88  csel     w4, w4, w8, eq       ; w4 = 1 if the slot is occupied
00004b8c  ldrb     w9, [x9, #1]         ; new MAC byte i
00004b90  cmp      w9, w7
00004b94  csel     w5, w5, wzr, eq      ; w5 = 1 if all 6 bytes match
00004b98  cmp      x1, #6
00004b9c  b.ne     #0x4b74
00004ba0  cmp      w5, #1
00004ba4  b.ne     #0x4bbc              ; not an exact match -> keep looking
...
00004bbc  cmp      w4, #0
00004bc0  sub      x2, x2, #6           ; next slot downwards
00004bc4  csel     w19, w19, w3, ne     ; free slot -> w19 = slot index
00004bc8  sub      w3, w3, #1
00004bcc  cmn      w3, #1
00004bd0  b.ne     #0x4b68              ; 8 slots, w3 = 7..0
```

`g_Cpu_Mac` is `switch.ko` `.bss` **0x178, size 0x30** = 48 bytes = **8 slots of 6
bytes**; slot 7 is at `g_Cpu_Mac+0x2a` and slot 0 at `g_Cpu_Mac+0x00`.

If the MAC is already present, the function returns 0 **without touching the
hardware** (the `cmp w5,#1` branch at 0x4ba4 goes straight to `mov w19, #0` at
0x4c5c). Otherwise it builds a 7-byte descriptor — `{u8 slot; u8 mac[6]}` — and calls
into `np_133.ko`:

```
00004c04  strb     w19, [x29, #0x30]    ; slot index
00004c20  bl       tm_onu_mac_addr_set
```

`switch.ko` has **no** `port_fdb` of its own: `sw_mac_table_op_init` (0x56f4) only
fills a five-entry function-pointer table and hands it to `register_mac_ops`:

```
00005714  adrp     x0, #0x5000          ; sw_mac_create
00005720  adrp     x0, #0x5000          ; sw_mac_delete
0000572c  adrp     x0, #0x5000          ; sw_mac_use
00005738  adrp     x0, #0x5000          ; sw_mac_port_full_set
00005744  adrp     x0, #0x5000          ; sw_mac_update
00005750  add      x0, x29, #0x20
00005754  bl       register_mac_ops
```

### 1.2 `np_133.ko` level

`tm_onu_mac_addr_set` — `np_133.ko` `.text` 0x1128b0, size 0x178. It validates the
slot index (0..15) and calls `spa_set_Onu_MacAddr`:

```
001128fc  ldrb     w1, [x0, #1]         ; mac[1]
00112900  ldrb     w2, [x0]             ; the slot index
00112904  strb     w1, [x29, #0x20]
...
00112934  b.hi     #0x1129c0            ; index > 15 -> error
00112940  ldrb     w0, [x0, w2, uxtw]    ; jump table on the index
...
001129e0  add      x1, x29, #0x20       ; the 6 MAC bytes
001129e4  bl       spa_set_Onu_MacAddr
```

`spa_set_Onu_MacAddr` — 0x24de0, size 0xd4 — byte-swaps and calls
`spa_local_onu_mac_set`:

```
00024e58  ldrh     w1, [x19]            ; mac[0..1]
00024e5c  mov      w0, w21              ; the slot index
00024e60  ldur     w2, [x19, #2]        ; mac[2..5]
00024e68  rev16    w1, w1
00024e6c  rev      w2, w2
00024e70  uxth     w1, w1
00024e74  bl       spa_local_onu_mac_set
```

`spa_local_onu_mac_set` — 0x22adc, size 0x114 — is where the hardware write happens,
and it writes **ONU registers 4 and 5**, not the FDB:

```
00022b30  adrp     x20, #0x22000        ; .data+0x830 (a shadow copy)
00022b34  mov      w22, w2             ; mac[0..1]
00022b3c  mov      w21, w1             ; mac[2..5]
00022b40  mov      w2, w0              ; the slot index
00022b44  mov      x3, x20
00022b48  mov      w1, w22
00022b4c  mov      w4, #2              ; 2 words
00022b50  mov      w0, #4              ; <-- ONU register 4
00022b54  bl       np_onu_reg_write
...
00022b60  mov      w2, w19             ; the slot index
00022b64  mov      w1, w21
00022b68  mov      w4, #2
00022b6c  mov      w0, #5              ; <-- ONU register 5
00022b70  bl       np_onu_reg_write
```

`np_onu_reg_write` is `np_133.ko` 0x1088b0.

### 1.3 What this chain is, and what it is not

* It is the **CPU/ONU MAC address**: the source MAC the ONT presents upstream and the
  identity of the 9132's CPU port. On this board it is the same 6 bytes as the DSA
  conduit's `lan-cpu0` address, `d0:45:8d:84:08:fc` (`docs/16 §2.5`).
* It is **not** an FDB entry. Nothing in this chain reaches `se_hash_table_set()`,
  `mac_table_set()`, or the alg engine.

### 1.4 The rest of the `sw_mac_*` family — what it is worth

Every FDB-manipulating `sw_mac_*` entry point in `switch.ko` bottoms out in a
`zxic_api_sw_*` function in `np_133.ko`, and every one of those is a **stub**. The
stub is literally eight bytes:

```
00128498  mov      w0, #0               ; zxic_api_sw_mac_add_port_macaddress
0012849c  ret
```

| `switch.ko` wrapper | address | `np_133.ko` callee | callee address | size | effect |
|---|---|---|---|---|---|
| `sw_mac_add_port_macaddress` | 0x4268 | `zxic_api_sw_mac_add_port_macaddress` | 0x128498 | 8 | **no-op** |
| `sw_mac_del_port_macaddress` | 0x4410 | `zxic_api_sw_mac_del_port_macaddress` | 0x1284c0 | 8 | **no-op** |
| `sw_mac_get_mactable` | 0x413c | `zxic_api_sw_mac_get_mactable` | 0x1285e8 | 8 | **no-op** (the wrapper then errors out with `w2 = 0xbe`) |
| `sw_mac_clear_port_mactable` | 0x4770 | `zxic_api_sw_mac_clear_port_mactable` | 0x1285a8 | 8 | **no-op** |
| `sw_mac_set_onu_agingtime` | 0x3d14 | `zxic_api_sw_mac_set_onu_agingtime` | 0x128464 | 8 | **no-op** |
| `sw_mac_get_onu_agingtime` | 0x3d8c | `zxic_api_sw_mac_get_onu_agingtime` | 0x128630 | 8 | **no-op** |
| `sw_mac_set_port_learnlimit` | 0x3e4c | `zxic_api_sw_mac_set_port_learnlimit` | 0x1284d8 | 8 | **no-op** |
| `sw_mac_get_port_learnlimit` | 0x3fac | `zxic_api_sw_mac_get_port_learnlimit` | 0x128474 | 8 | **no-op** |
| `sw_mac_set_mac_hashmode` | 0x5544 | `tm_hash_mode_set` | 0x128670 | 8 | **no-op** |
| `sw_mac_get_onu_macentrynum` | 0x49d8 | `tm_mac_learning_number_get` | 0x128568 | 8 | **no-op** (it sums per-port learning counters that always read 0) |
| `sw_mac_get_port_learnnum` | 0x489c | `tm_mac_learning_number_get` | 0x128568 | 8 | **no-op** (same stub) |

The **only** two `sw_mac_*` functions that reach real hardware are
`sw_mac_add_cpu_macaddress` (→ the ONU MAC registers, §1.2) and
`sw_mac_clear_macaddress` (→ `zxic_api_sw_mac_clear_macaddress`, 0x1154d0, 52 bytes,
a real function).

The `sw_mac_*` family is exported, and `sw_mac_add_cpu_macaddress` has no
`switch_sw_mac_add_cpu_macaddress_{help,detail}` companion in `switch.ko` (unlike
`sw_mac_add_port_macaddress`, which has both), so it is not reachable from the `tm`
console — it is an API for the vendor's user-space stack.

---

## 2. Where the FDB is, and what one entry looks like

### 2.1 SDT 0x32 is a **hash** table, not an ERAM table

`g_core_sdt_info` — `np_133.ko` `.data` **0x11be0**, size **0x350** = 848 bytes =
53 entries of 16 bytes (`{u32 sdt_no, u32 type, u32 a, u32 b}`). Entry 37 is:

```
  idx 37: sdt=0x32 type=3 a=0x8 b=0x64
```

and its neighbours:

```
  idx 35: sdt=0x1f type=1 a=0x1 b=0x4000   (the flood group table, ERAM)
  idx 36: sdt=0x27 type=1 a=0x7 b=0x40
  idx 37: sdt=0x32 type=3 a=0x8 b=0x64      <-- the MAC table
  idx 38: sdt=0x38 type=3 a=0x6 b=0x64
  idx 39: sdt=0x35 type=3 a=0x8 b=0x40
  ...
  idx 42: sdt=0xfc type=4 a=0x1 b=0x60000000   (type 4 = statistics)
```

The driver's own list is the same list, entry for entry and in the same order
(`zx279133-np.c:3159-3190`):

```c
	{ 0x27, 1, 0x7, 0x40 },		{ 0x32, 3, 0x8, 0x64 },
	{ 0x38, 3, 0x6, 0x64 },		{ 0x35, 3, 0x8, 0x40 },
```

`zx279133-np.c:3149` states the type encoding: *"type 1 is ERAM, 3 is hash, 4 is
statistics"*, and `zx279133-np.c:3475-3517` implements it: only `case 1`/`case 5`
take blocks out of the ERAM pool and only they fill `zx279133_eram[sdt_no]`; `case 3`
calls `zx279133_se_hash_sdt_create()`.

**This is why `tools/eram-map.py --map` does not list 0x32.** Its output lists the 38
ERAM tables out of the 53 in the vendor's list (`0x0a`, `0x15`..`0x2b`,
`0x6f`..`0x7d` less `0x73`) and 0x32 is not among them, because 0x32 is not an ERAM
table.

`a` and `b` for a hash table decode as (from the driver's own
`zx279133_se_hash_sdt_create()`, `zx279133-np.c:3267-3297`):

```
  low    = b & 0xff          = 0x64
  width  = (low >> 6) & 3    = 1
  as_rsp = (low >> 2) & 3    = 1
  multi  = (b >> 4) & 1      = 0
  age    = (b >> 5) & 1      = 1
  key_base = key_base_tab[1] = 1
  key_len  = a = 8  ->  field = (key_len / key_base) & 0xf = 8
```

⇒ **the key is 8 bytes wide** and the table has an age table. Both facts are
confirmed independently in §2.5 below.

### 2.2 The write path, from the console command down to the alg engine

The only working writer in the vendor stack is the `tm` console command
`/proc/pon/tm_debug/tm_mactable_fdb_add`. Its own help text (`np_133.ko`
0xf8c04, strings at `.rodata.str1.1` + 0x6c726) is:

```
echo samc_ctrl=xx dmac_ctrl=xx macaddr[0]=xx macaddr[1]=xx macaddr[2]=xx macaddr[3]=xx
     macaddr[4]=xx macaddr[5]=xx vlanid=xx port=xx > /proc/pon/tm_debug/tm_mactable_fdb_add
  samc_ctrl= 0: Match mac sa pass  1: Match mac sa drop
  dmac_ctrl= 0: Match mac da pass  1: Match mac sa drop
  macaddr:   mac address
  vlanid: 0~1023
  port: 0~63
```

The parameter-name table in `.rodata.str1.1` gives the argument order
**smac_ctrl, dmac_ctrl, vlanid, port, macaddr[0..5]**.

`tm_mactable_fdb_add_set` — 0x112a28, size 0x12c — takes the 20-byte argument
structure and splits it into a 16-byte key/value pair:

```
00112a84  ldur     w0, [x19, #6]         ; macaddr[0..3]
00112a88  ldp      w1, w2, [x19, #0xc]    ; w1 = vlanid, w2 = port
00112a8c  rev      w0, w0                ; byte-swap
00112a90  str      w0, [x29, #0x24]       ; key[4..7]
00112a94  ldrh     w0, [x19, #0xa]        ; macaddr[4..5]
00112a98  and      w1, w1, #0xfff         ; the 12-bit vid
00112a9c  rev16    w0, w0
00112aa0  strh     w0, [x29, #0x22]       ; key[2..3]
00112aa4  ldrh     w0, [x29, #0x20]
00112aa8  bfxil    w0, w1, #0, #0xc
00112aac  strh     w0, [x29, #0x20]       ; key[0..1] = the vid
00112ab0  ldrb     w0, [x29, #0x2d]
00112ab4  bfxil    w0, w2, #0, #6         ; port & 0x3f
00112abc  strb     w0, [x29, #0x2d]       ; value[5] = the port id
00112ac0  ldrh     w0, [x29, #0x2e]
00112ac4  bfxil    w0, w1, #0, #0xc       ; the vid again
00112ac8  ldrb     w1, [x19, #4]          ; smac_ctrl
00112acc  strh     w0, [x29, #0x2e]       ; value[6..7]
00112ad0  lsr      x0, x0, #8
00112ad4  bfi      w0, w1, #6, #1         ; value[7] bit 6 = smac_ctrl
00112ad8  ldrb     w1, [x19, #5]          ; dmac_ctrl
00112adc  bfi      w0, w1, #5, #1         ; value[7] bit 5 = dmac_ctrl
00112ae0  add      x1, x29, #0x28
00112ae4  strb     w0, [x29, #0x2f]       ; value[7]
00112ae8  add      x0, x29, #0x20
00112aec  bl       mac_table_set
```

So the 16-byte record is:

| bytes | content |
|---|---|
| **key[0..1]** | the 12-bit **VID** (`vlanid & 0xfff`) |
| **key[2..3]** | `rev16(macaddr[4..5])` = `macaddr[5], macaddr[4]` |
| **key[4..7]** | `rev32(macaddr[0..3])` = `macaddr[3], macaddr[2], macaddr[1], macaddr[0]` |
| value[0..4] | 0 |
| value[4] | 0; **bit 7 is overwritten** by `mac_table_set` with the `port_is_ssid_flag` |
| **value[5]** | **the port id**, 6 bits (`port & 0x3f`) |
| value[6] | the vid, low 8 bits |
| value[7] | the vid, bits 8..11 in bits 0..3; **bit 5 = `dmac_ctrl`**, **bit 6 = `smac_ctrl`** |

⇒ the **MAC is stored byte-reversed**: `key[2..7] = mac[5], mac[4], mac[3], mac[2],
mac[1], mac[0]`.

`mac_table_set` — 0xb5440, size 0xe4 — turns that into a hash-table set:

```
000b5460  ldrb     w0, [x20, #5]         ; value[5] = the port id
000b5478  and      w0, w0, #0x3f
000b547c  str      w21, [x29, #0x38]     ; node_info.key_len = 8
000b5480  bl       port_is_ssid_flag_get ; (0xbcf24)
000b5488  ldrb     w1, [x20, #4]         ; value[4]
000b5490  add      x2, x29, #0x48
000b5494  str      w21, [x29, #0x48]     ; other_info.val_len = 8
000b5498  bfi      w1, w0, #7, #1        ; value[4] bit 7 = the SSID flag
000b549c  mov      w0, #0x32             ; <-- SDT 0x32
000b54a0  strb     w1, [x20, #4]
000b54a4  add      x1, x29, #0x38
000b54a8  bl       se_hash_table_set
```

`node_info` is `{u32 key_len = 8, u32 pad, void *key}` — the key pointer is stored at
`node_info+8`, and `se_hash_table_set_by_sdt_info`'s helper reads it from there
(`ldr x0, [x1, #8]` at 0x90e4c). `other_info` is `{u32 val_len = 8, u32 pad,
void *value}`.

`se_hash_table_set` — 0x92b88:

```
00092bec  mov      w0, w20
00092bf0  bl       se_sdt_get_hash       ; 0x8cbb8 - the SDT's hash descriptor
00092bf4  cbnz     x0, #0x92c24
...
00092c24  ldrb     w5, [x0, #1]
00092c34  ubfx     x5, x5, #2, #1
00092c44  bl       se_hash_table_set_by_sdt_info
```

`se_hash_table_set_by_sdt_info` — 0x920b8 — calls the static helper at 0x90e2c
(which fills the geometry: `se_sdt_hash_get_table_key_byte_len`,
`se_sdt_hash_get_table_bit_len`, `se_sdt_hash_get_table_rsp_bit_len`) and then the
real worker at 0x91c60, under a spinlock (`.bss`+0xbee8).

The two places where bytes actually land in the hardware are:

* `se_alg_hash_table_write` — 0x85c44 — the plain CAM write:

  ```
  00085cc8  ldrb     w0, [x21, #2]
  00085ccc  and      w0, w0, #0xf
  00085cd0  bl       se_hash_get_word32_offset_from_mask
  00085cd8  ldrb     w0, [x21, #2]
  00085ce0  bl       se_hash_get_table_word32_size_from_mask
  00085cf4  bl       se_alg_write          ; 0x85578
  ```

* `se_hash_zcam_table_write` — 0x8be38 — the software-managed variant, which also
  allocates a free CAM cell and maintains a "cell in use" bitmap at `.bss`+0x96c8:

  ```
  0008bf34  b.eq     #0x8bf4c
  0008bf38  mov      x1, x22              ; the entry data
  0008bf3c  add      x0, x29, #0x30
  0008bf40  bl       se_alg_hash_table_write
  0008bf44  cbz      w0, #0x8bf5c
  0008bf4c  add      x0, x29, #0x30
  0008bf50  bl       #0x8b9d0             ; pick the key format (13/14/15/16 bytes)
  0008bf54  cbz      w0, #0x8bf38
  ```

The alg engine's own register block is described by `se_alg_reg_table`
(`np_133.ko` `.data` 0xcf90, 0x1a4 bytes); its first entries are
`0x00014001`, `0x00014002`, `0x00014012` — i.e. the alg block sits at **0x14000** in
the 9132's own register space. The last step of an FDB write is a
read-modify-write of that register file:

```
000854f0  add      x0, x25, w23, uxtw #2    ; &se_alg_reg_table[i]
000854f8  mov      x3, x26                 ; x3 = se_alg_reg_table
000854fc  mov      w2, w20                 ; the value
00085500  ldur     w1, [x0, #-4]           ; the register index from the table
00085504  mov      w0, w4                  ; w0 = 1  (the register "base")
00085508  bl       np_onu_reg_write        ; 0x1088b0
```

`np_onu_reg_write` (0x1088b0) validates the index against the table, computes the
bit position, and ends in `fpga_read_reg` / `fpga_write_reg` (0x108780 / 0x1086f0).
`fpga_write_reg` dispatches on the register index to a per-block writer
(`fpga_pon_write_reg`, `fpga_pps_write_reg`, `fpga_nppt_write_reg`,
`fpga_rgmii_write_reg`, `fpga_gmac_write_reg`, `fpga_xgmac_write_reg`,
`fpga_emac_write_reg`, `fpga_xeumac_write_reg`, `fpga_xedmac_write_reg`,
`fpga_crm_write_reg`, …). **This is the 9132's FPGA register file — a different
window from the parser's SMMU0 window that the mainline driver talks to** (see §5).

### 2.3 The entry as the vendor prints it back

`mac_table_show_one_info` — 0xb5288 — is the vendor's own pretty-printer, and its
format strings give the field names:

```
  key : 
    mac_addr = %02x:%02x:%02x:%02x:%02x:%02x
    vid = 0x%04x
  result : 
    sa_mac_ctrl = 0x%x
    da_mac_ctrl = 0x%x
    vid = 0x%04x
    port_id = %u
    port_is_ssid_flag = 0x%x
```

and the guard at the top is the entry width:

```
000b528c  cmp      w1, #0x10            ; "mac table len is not 128bit"
```

⇒ **one FDB entry is 128 bits = an 8-byte key + an 8-byte value.**

The field extraction in the printer:

```
000b52c0  ldp      w4, w1, [x19, #8]    ; entry[8..11], entry[12..15]
000b52cc  ubfx     x1, x1, #0, #0x18
000b52d0  ubfx     x4, x4, #8, #0x18    ; -> the six MAC bytes, entry[9..14]
...
000b52f0  ldrb     w0, [x19, #8]
000b52f4  ldrb     w1, [x19, #7]
000b52f8  ubfiz    w0, w0, #8, #4
000b52fc  add      w1, w0, w1           ; -> the key's vid, 12 bits
...
000b5314  ldrb     w1, [x19, #4]
000b5320  ubfx     w1, w1, #4, #1       ; -> sa_mac_ctrl
000b5328  ldrb     w1, [x19, #4]
000b5334  ubfx     x1, x1, #3, #1       ; -> da_mac_ctrl
...
000b5358  ldr      w1, [x19]
000b5364  ubfx     x1, x1, #0xe, #6      ; -> port_id, 6 bits
000b536c  ldrb     w1, [x19, #1]        ; -> port_is_ssid_flag
```

**Ambiguity, stated explicitly.** The *write* layout (§2.2) and the *read-back*
layout are **not** the same byte order:

| field | write layout (key/value, 8 + 8 bytes) | read-back layout (16 bytes, entry[0..15]) |
|---|---|---|
| MAC | `key[2..7]`, reversed | `entry[9..14]`, reversed (printed `e14:e13:e12:e11:e10:e9`) |
| key vid | `key[0..1]` (12 bits) | `entry[7]` + `entry[8] & 0xf` |
| port id | `value[5] & 0x3f` | `entry[0..3]` bits 14..19 |
| sa/da ctrl | `value[7]` bits 6 / 5 | `entry[4]` bits 4 / 3 |
| result vid | `value[6..7]` bits 0..11 | `entry[2..3]` bits 6..15 + `entry[4]` bits 0..1 |
| ssid flag | `value[4]` bit 7 | `entry[1]` |

Both readings are certain; the mapping between them is **not** determined here. It
does not matter for writing an entry (use the write layout) but it does matter for
reading one back with the driver's own debugfs.

### 2.4 How a MAC becomes an index: a hardware hash + a CAM

There is **no software-visible index formula**. The 9132's MAC table is a
**hash table with a content-addressable memory**, and the bucket is chosen by the
alg engine:

* the key is 8 bytes (§2.1: `a = 8`, `key_base = 1` → 8 bytes; §2.2: the vendor's
  own printer calls the entry 128 bits and the code splits it 8 + 8);
* the alg engine hashes the key itself. The hash is a **CRC32** — the module has
  `se_hash_ddr_crc32_mode_set` (0x8b380) and `se_alg_set_hash_ext_crc_cfg` (0x85e88)
  to program it, and `se_alg_set_hash_ext_depth` (0x86228) to program the depth;
* the key is written into a **CAM cell** (`se_alg_hash_table_write` →
  `se_alg_write`, 0x85578) whose word offset comes from the table's mask
  (`se_hash_get_word32_offset_from_mask`, `se_hash_get_table_word32_size_from_mask`);
* the vendor's "zcam" variant additionally tracks which CAM cell holds which key in
  a bitmap at `.bss`+0x96c8, using the geometry in
  `g_se_hash_zcam_range_table` (`.data` 0x11bd0 = `{0xa4, 0x04030002}`) and
  `g_zcam_range_num` (`.data` 0x11bc8 = **1**). The key *format* is chosen by the
  static helper at 0x8b9d0, which selects a 13-, 14-, 15- or 16-byte key
  according to the table's width class.

So the answer to "hash? CAM? bucket chain?" is: **a hash table whose buckets are
CAM cells, matched by the alg engine's CRC; the software only ever supplies a key and
a value, and (in the zcam variant) which free cell to use.**

### 2.5 Ageing, and the "valid" bit

* There is **no valid bit inside the 128-bit entry.** Validity is the occupancy of
  the CAM cell.
* Ageing is kept **outside** the entry, in a separate **1-bit-per-entry age table**
  in ERAM: `se_hash_ddr_set_bulk_age_info` (0x8ab44), `se_hash_ddr_get_age_num`
  (0x8ad0c), `se_hash_ddr_enable_set` (0x8ad18), `se_hash_ddr_init` (0x8b448),
  `se_hash_ddr_get_available_space` (0x8adcc). The per-entry age flag is read back
  through `tm_mactable_get_age_flag` (0x114c74).
* The age table's base is programmed by `se_parser_set_age_table_base_addr` (called
  32 times from `se_hash_creat_other_info_by_sdt`, 0x92fd8), and the parser's age
  depth register by `se_age_manage_init` (0xa2318). The driver already does both:
  `zx279133-np.c:3305-3315` performs the 32 parser base-address writes with the age
  base 0, and `zx279133-np.c:3467` writes `2047` to `eth->pps_base + 0x400c4`
  ("parser age table depth register").
* The vendor's own live measurement (`docs/16 §2.2`, `tm_mactable_fdb_show`) shows
  the age table in use: the board's FDB held exactly **one** entry, the PC's MAC
  `84:47:09:5b:bd:2c`, `vid = 0x0000`, `port_id = 2` — a **learned** entry, and
  notably **not** the board's own MAC.

### 2.6 The `sw_mac_get_onu_macentrynum` / `sw_mac_get_port_learnnum` counters

`sw_mac_get_onu_macentrynum` (0x49d8) sums `tm_mac_learning_number_get(port)` over
the ports; `sw_mac_get_port_learnnum` (0x489c) reads the per-port count. These are
the "how many MACs has this port learned" counters — i.e. the vendor's own view
that the FDB is filled by the hardware's learning engine, not by software.

---

## 3. What the vendor installs at init

### 3.1 `sw_init_switch()` — the full init list

`switch.ko` 0x2e74. For `current_cputype` 6 or 8 (this board) the call list is:

```
00002f10  bl   sw_init_port_protocol_action
00002f14  bl   sw_set_default_vlan_check_ctrl
00002f18  bl   sw_init_port_isolate
00002f1c  bl   sw_init_mac_lookup_ctrl
00002f20  bl   sw_init_flood_ctrl
00002f24  bl   sw_init_omci_trap_qid_init
00002f28  bl   sw_init_port_route_mode
00002f2c  bl   sw_reg_set_onu_swcap
00002f30  bl   sw_mac_table_op_init          <-- registers function pointers only
00002f34  bl   sw_multi_config_init
```

**Not one of these writes an FDB entry.** `sw_mac_table_op_init` (0x56f4) only
installs a five-entry function-pointer table (§1.1).

### 3.2 What `sw_init_mac_lookup_ctrl` (0x2938) does — the actual MAC-lookup config

```
00002950  uxtb     w20, w19
00002954  mov      w1, #1
00002958  mov      w0, w20
0000295c  bl       zxic_api_sw_set_sa_lookup_learn_en    ; learning ON
00002968  bl       zxic_api_sw_set_mac_limit_full_flag
00002970  add      w19, w19, #1
00002974  cmp      w19, #8
00002978  b.ne     #0x294c              ; ports 0..7
...
0000297c  mov      w19, #0x10
00002988  mov      w1, #1
0000298c  mov      w0, w20
00002990  bl       zxic_api_sw_set_sa_lookup_learn_en    ; ports 16..48
...
000029b4  bl       zxic_api_sw_set_da_lookup_en           ; DA lookup ON, ports 0..7
000029d0  adrp     x1, #0x2000          ; rgmii_to_extend
000029e4  mov      w1, #0
000029e8  mov      w0, #7
000029ec  bl       zxic_api_sw_set_da_lookup_en           ; port 7 off if RGMII is extended
000029f4  mov      w19, #0x10
000029fc  bl       zxic_api_sw_set_da_lookup_en           ; ports 16..48
```

⇒ the vendor's "MAC table configuration" at init is **three per-port bits**:
`sa_lookup_learn_en = 1`, `da_lookup_en = 1`, `mac_limit_full_flag = 0`. No entries,
no ageing time (the ageing API is a stub), no hash mode (also a stub), no learn
limit (stub).

These land in **SPA register 19**, the port attribute word, and are mirrored by the
vendor into **ERAM SDT table 0x7d word 0** — see `re-spa-attrs.md` §1.2. The value
`0x96040001` is what the driver already writes.

### 3.3 The list the task asks for

| what | where it comes from | where it ends up |
|---|---|---|
| **the CPU port's own MAC** | a 6-byte MAC handed in by the vendor's user-space stack (it is the board's MAC, `d0:45:8d:84:08:fc` on this board) | `switch.ko` `.bss` `g_Cpu_Mac` (8 slots × 6 B) **and** the **ONU MAC registers 4 and 5** (`np_onu_reg_write`) — **not the FDB** |
| **the WAN/pon port's MAC** | — | **nowhere.** There is no code path that installs a MAC for port 0 into the FDB. The WAN port's identity is the PON side's own MAC (PON MAC `00:d0:d0:61:62:00`, `docs/16 §2.5`), programmed in the PON MAC block, not in the 9132's FDB. |
| **anything else at init** | — | **nothing.** The FDB is empty at init on the vendor board. |

The live confirmation is `docs/16 §2.2`: the vendor's own
`/proc/pon/tm_debug/tm_mactable_fdb_show` on the running board showed exactly **one**
entry — the PC's MAC on `port_id 2` — which is a *learned* entry, and the board's own
CPU MAC was **not** in the table.

**Conclusion for Q3: the vendor does not install the CPU port's own MAC into the
FDB, by any route.** The `sw_mac_add_cpu_macaddress` name is misleading: it
programs the ONU MAC address registers.

---

## 4. Q4 — does the mainline driver need to do the same?

### 4.1 The DSA driver has no FDB hooks at all

`zx279133_switch_dsa_ops` — `zx279133-switch.c:1499-1513`:

```c
static const struct dsa_switch_ops zx279133_switch_dsa_ops = {
	.get_tag_protocol = zx279133_switch_get_tag_protocol,
	.setup = zx279133_switch_setup,
	.teardown = zx279133_switch_teardown,
	.port_bridge_join = zx279133_switch_port_bridge_join,
	.port_bridge_leave = zx279133_switch_port_bridge_leave,
	.port_enable = zx279133_switch_port_enable,
	.port_disable = zx279133_switch_port_disable,
	.port_vlan_filtering = zx279133_switch_port_vlan_filtering,
	.port_vlan_add = zx279133_switch_port_vlan_add,
	.port_vlan_del = zx279133_switch_port_vlan_del,
	.port_stp_state_set = zx279133_switch_port_stp_state_set,
	.phylink_get_caps = zx279133_switch_phylink_get_caps,
	.phylink_fixed_state = zx279133_switch_phylink_fixed_state,
};
```

There is **no** `port_fdb_add`, `port_fdb_del`, `port_mdb_add`, `port_mdb_del`, and
the driver registers **no** `switchdev_ops` at all (grep for `port_fdb|port_mdb`
over `kernel-overlays/drivers/net/ethernet/zte/` returns nothing).

**So: yes, the CPU port's MAC is never installed in the 9132's FDB by the driver,
and the DSA bridge's FDB (which normally carries the conduit's MAC on the CPU port)
cannot reach the hardware.** That part of the question is answered plainly.

### 4.2 … but the vendor does not do it either, so this is not a regression

The vendor's equivalent hook — `zxic_api_sw_mac_add_port_macaddress` — is an
8-byte no-op stub (§1.4). The vendor's FDB is filled exclusively by the 9132's own
SA-learning engine, which the driver *does* enable, because it writes the vendor's
own attribute word to every port it owns:

* `zx279133_9132_vendor_words[]` (`zx279133-np.c:2041-2048`) contains
  `{ 19, ZX279133_9132_VENDOR_ATTR_WORD }` with
  `#define ZX279133_9132_VENDOR_ATTR_WORD 0x96040001`
  (`zx279133-np.c:1929`);
* `zx279133_9132_port_init()` (`zx279133-np.c:2116-2177`) writes it to
  `ZX279133_9132_UNI_PPU_FIRST..LAST` = **PPU 1..4** (the four jacks) **and** to
  `ZX279133_9132_CONDUIT_PPU` = **PPU 5** (the CPU port, `zx279133.h:40`), and
  mirrors it into ERAM 0x7d word 0 (`zx279133_9132_mf_mirror()`);
* `0x96040001` = `sa_lookup_learn_en` (bit 31) + `da_lookup_en` (bit 28) +
  `brd_flood_en` (25) + `mul_flood_en` (26) + `input_vlan_check_en` (18) +
  `unk_da_flood_num = 1` (bits 0..5) — see `re-spa-attrs.md` §1.2 for the full
  field map.

⇒ The 9132 learns the board's MAC from the frames the SoC transmits through PPU 5,
exactly as it does on the vendor board. The missing `port_fdb_add` is therefore
**not** the reason a unicast frame addressed to the board does not arrive.

### 4.3 The consequence for a unicast frame addressed to the board

A frame with DA = the board's MAC arriving on a UNI port (PPU 1..4) goes through
the 9132's DA lookup:

1. **The board's MAC is in the FDB** (learned from the CPU port's egress traffic):
   the entry says "port 5", the frame is forwarded to PPU 5, it crosses XMAC0 into
   the SoC, and `lan-cpu0` sees it. **This is the normal case and it works on both
   the vendor board and the mainline board.**
2. **The board's MAC is not (yet) in the FDB** — e.g. right after a power cut,
   before the SoC has sent a single frame through the 9132. The DA lookup misses,
   the frame is an *unknown unicast*, and it is flooded to the port's
   **unknown-DA flood group**:
   * **on the vendor board**: the group holds exactly one member, the WAN port 0
     (`re-spa-attrs.md` §3.5) — so the frame is sent **out of the WAN port** and
     never reaches the CPU;
   * **on the mainline board**: the driver writes **ERAM table 0x1f not at all**
     (`re-spa-attrs.md` §3.7 gap A) and does not mirror the flood counts and group
     ids into ERAM 0x7d words 1/2 (gap B) — so the group is **empty** and the
     frame is **dropped**.

So the practical difference between the two systems is: **a not-yet-learned unicast
frame addressed to the board is misdirected to the WAN on the vendor board and
dropped on the mainline board.** Neither reaches the CPU. The mainline behaviour is
strictly safer (it cannot leak the frame out of the WAN port) but it does not
deliver the frame either.

**If the symptom under investigation is "the board does not answer a unicast frame
that arrives on a jack", then the FDB is not the place to look** — the frame never
gets as far as a DA-lookup hit; look at the flood path and at the CPU port's
learning, exactly as `re-spa-attrs.md` §4 already concluded.

---

## 5. Q5 — is the FDB write path reachable from the driver's existing machinery?

**No, not with what the driver has today.** Concretely:

| driver facility | can it reach SDT 0x32? | why |
|---|---|---|
| `zx279133_eram_table_set_bits(eth, sdt_id, index, width, value)` (`zx279133-np.c:489`) | **no** | it needs `zx279133_eram[sdt_id].base` and `.bit_len`; those are only filled in the `case 1:`/`case 5:` branch of the SDT loop (`zx279133-np.c:3489-3492`). SDT 0x32 is `type 3`, so `zx279133_eram[0x32]` stays `{0, 0}` and the helper returns `-EINVAL` at `zx279133-np.c:498-501`. |
| `zx279133_eram_window_patch()` (`zx279133-np.c:571-596`) | **no** | same reason — it looks the base up in `zx279133_eram[]`. |
| `zx279133_smmu0_write(eth, addr_ind, data, words, cmd)` (`zx279133-np.c:415`) | **not as it stands** | the driver's path is the 9132's **parser SMMU0** indirect window. The vendor's FDB write ends somewhere else entirely: `se_alg_write` (0x85578) → `__se_alg_write` (0x853d0) → `np_onu_reg_write(1, <index>, value, se_alg_reg_table, 1)` (0x1088b0) → `fpga_write_reg` (0x1086f0), i.e. a read-modify-write of the 9132's **FPGA register file** through a per-block dispatcher (`fpga_pon_write_reg`, `fpga_pps_write_reg`, `fpga_nppt_write_reg`, `fpga_crm_write_reg`, …), with the register index and the bit position taken from the vendor's `se_alg_reg_table`. The driver has no equivalent of that register-file machinery, and no way to reach the alg block's registers through the SMMU0 window it does have. |
| `zx279133_se_hash_sdt_create(eth, sdt_no, key_len, tf)` (`zx279133-np.c:3267`) | **creates the table, cannot fill it** | the driver already calls it for SDT 0x32 (`{ 0x32, 3, 0x8, 0x64 }`), with the same `a`/`b` the vendor uses, so the FDB **hash table is brought up in hardware exactly as the vendor brings it up**. What it does is *configure* the table (hash id, key length, the age table and the 32 parser age base-address writes). There is no counterpart of `se_hash_table_set()` / `se_alg_hash_table_write()` in the driver. |
| `zx279133_spa_indirect_write/read()` | **no** | the SPA window is the per-port register file (registers 0..19), not the FDB. |

**Summary: the table exists, the driver creates it, and the driver has no way to
put an entry in it.** The vendor has the same problem — its own FDB-write API is a
stub — so nothing in the vendor stack is a model to copy; the only model is the
vendor's *console* path (`tm_mactable_fdb_add` → `mac_table_set` →
`se_hash_table_set` → `se_alg_hash_table_write` → `se_alg_write`).

---

## 6. The smallest correct change

### 6.1 What **not** to do

* **Do not** try to reach the FDB through `zx279133_eram_table_set_bits()`. SDT 0x32
  is a hash table; the ERAM helper cannot address it and would silently write into
  whatever ERAM table the allocator handed to some other id.
* **Do not** copy the vendor's `sw_mac_add_cpu_macaddress` semantics into the
  driver. That function programs the **ONU MAC registers** (ONU regs 4 and 5), which
  are the ONT's own source MAC for the PON side — a completely different thing from
  the FDB, and on this board the DSA conduit's MAC is set by the SoC's own MAC
  driver, not by the 9132.
* **Do not** add a `port_fdb_add` that writes a fabricated 128-bit blob somewhere
  and hope. The bucket is chosen by the alg engine's CRC and the CAM cell is
  allocated by the hardware; a wrong guess corrupts an unrelated entry.

### 6.2 The smallest change that actually helps: give the CPU port a flood fallback

This is the same conclusion `re-spa-attrs.md` §4 items 1 and 2 reached, and it is
the right one here as well, because it is the only change that uses machinery the
driver already has.

**(1) Mirror the flood group ids and counts into ERAM 0x7d words 1 and 2.** The
driver already has the values (SPA registers 17 and 18) and already has
`zx279133_eram_window_patch()` (`zx279133-np.c:571-596`), which reads the 128-bit
window, patches one word and writes it back:

| field | ERAM 0x7d position | value for port p |
|---|---|---|
| `mul_flood_group_id` | word 1, bits 0..15 | `(p + 0x20) << 6` |
| `unk_da_flood_group_id` | word 1, bits 16..31 | `p << 6` |
| `brd_flood_num` | word 2, bits 4..9 | 1 |
| `mul_flood_num` | word 2, bits 10..15 | 1 |
| `brd_flood_group_id` | word 2, bits 16..31 | `(p + 0x10) << 6` |

(word 1 is bit-for-bit SPA register 18 and word 2 is bit-for-bit SPA register 17 —
`re-spa-attrs.md` §3.6, so this is a copy, not a re-derivation.)

**(2) Populate ERAM table 0x1f with the CPU port as a flood destination.** This
*is* reachable with the existing helper, and the exact index and value are known:

| ERAM 0x1f index | meaning | value to write |
|---|---|---|
| `p << 6` | the unknown-DA group of port p | **5** (PPU 5 = the CPU port) |
| `(p + 0x10) << 6` | the broadcast group of port p | **5** |
| `(p + 0x20) << 6` | the multicast group of port p | **5** |

for `p` in 1..4 (the four UNI jacks) and, if the conduit should also receive
broadcasts, p = 5. The value format is one 32-bit ERAM word: the destination port
id in **bits 0..5**, bit 7 = the "SSID port" flag (leave 0) — evidence
`group_id_table_port_id_set` (0xb99c4) → `group_id_table_data_set` (0xb9924) →
`se_eram_table_set(0x1f, index, 1, buf)` (`re-spa-attrs.md` §3.3). The group id *is*
the ERAM index, so:

```c
	zx279133_eram_table_set_bits(eth, 0x1f, p << 6,       32, 5);
	zx279133_eram_table_set_bits(eth, 0x1f, (p + 0x10) << 6, 32, 5);
	zx279133_eram_table_set_bits(eth, 0x1f, (p + 0x20) << 6, 32, 5);
```

This is the one change that **deliberately differs from the vendor** (the vendor's
group holds only the WAN port 0). It is safe in the sense that it can only make the
9132 send *more* frames to the port the CPU is listening on.

**What this buys:** a unicast frame addressed to the board whose MAC is not (yet) in
the 9132's FDB is treated as an unknown unicast and is flooded to the CPU port
instead of being dropped — so the CPU can answer it (and the answer teaches the
9132 the entry through normal SA learning). Without it, the frame is lost at the
9132 and the CPU never even sees it to reply.

### 6.3 If a *static* FDB entry for the CPU MAC is wanted anyway

Everything that can be determined offline is:

* **table id**: **SDT 0x32** (`type 3` = hash, `a = 8`, `b = 0x64`), 128-bit entries,
  8-byte key + 8-byte value;
* **entry value**, for the board's MAC `d0:45:8d:84:08:fc`, vid 0, port id 5
  (PPU 5), sa/da ctrl 0, ssid flag 0:

  ```
  key   = 00 00 fc 08 84 8d 45 d0     ; vid(12b) | mac reversed
  value = 00 00 00 00 00 05 00 00     ; value[5] = the port id
  ```

* **index computation**: **not determinable offline.** The bucket is computed by the
  alg engine's CRC32 over the 8-byte key; the CRC's configuration is programmed by
  `se_alg_set_hash_ext_crc_cfg` (0x85e88) and the depth by
  `se_alg_set_hash_ext_depth` (0x86228), and the software-managed "zcam" path picks
  a free cell out of the zblock/zcell/zreg geometry in
  `g_se_hash_zcam_range_table` / `g_zcam_range_num`. There is no formula in the
  module that turns a MAC into an address, because there is not one: the CAM matches
  the key.

So the smallest *correct* implementation of `.port_fdb_add` would be a new helper
modelled on the vendor's `se_hash_table_set(0x32, …)` → `se_alg_hash_table_write`
→ `se_alg_write`, i.e. a new "write one CAM entry" path into the **alg block at
0x14000**, plus a read of the alg block's free-cell bitmap. That is a substantial
new subsystem, it is **not** reachable through the driver's existing ERAM helpers,
and it is not needed to make the board work: the vendor does not have it either.

**Recommendation: do 6.2 (the flood fallback), and leave the FDB to the hardware's
learning engine, which the driver already enables with the vendor's own attribute
word.**

---

## 7. What could NOT be determined offline, and what would settle it

| item | why it is open | what would settle it |
|---|---|---|
| The **hash/bucket function** of SDT 0x32 (the CRC32 polynomial, seed, and the bucket count) | the bucket is computed by the alg engine in hardware; the module only *programs* the CRC configuration (`se_alg_set_hash_ext_crc_cfg`, 0x85e88) and the depth (`se_alg_set_hash_ext_depth`, 0x86228). No formula exists in the module. | dump the alg block's registers after `se_hash_init()` on the vendor board (`/proc/pon/tm_debug`), or the 9132 datasheet. |
| The **exact byte mapping** between the write layout (§2.2) and the read-back layout (§2.3) | the two layouts are each certain, but the permutation between them is not derivable from the module. | add one entry with `tm_mactable_fdb_add` on the vendor board and read it back with `tm_mactable_fdb_show`; the two dumps give the permutation directly. |
| Whether `smac_ctrl` is input[4] and `dmac_ctrl` input[5] (vs. the reverse) | the `tm` parameter table gives the *order* of the names but not their struct offsets; the code reads input[4] and input[5] without saying which is which. | the same add-then-show experiment, with `smac_ctrl=1` and `dmac_ctrl=0`. |
| The **CAM-cell allocation protocol** of the zcam path (how a free zcell/zreg is found and reserved) | it is a bitmap at `.bss`+0x96c8 indexed by a geometry built from `g_se_hash_zcam_range_table` = `{0xa4, 0x04030002}` and `g_zcam_range_num` = 1; the index arithmetic is spread over `se_hash_zcam_get_zcell_available_space` (0x8ba70) and `se_hash_zcam_get_zreg_available_space` (0x8bd78) and was not decoded here. | decode those two functions, or read the bitmap after one `tm_mactable_fdb_add`. |
| Which **per-block writer** of `fpga_write_reg` the alg engine's registers live in | `fpga_write_reg` (0x1086f0) dispatches on the register index to ten different block writers, and `se_alg_reg_table` is a 28-byte-per-entry table whose "base" and "index" fields were not fully decoded (it is keyed by the `w0 = 1` base that `__se_alg_write` passes). | decode `se_alg_reg_table` and the `w0 = 1` entry of it, then find which `fpga_*_write_reg` the index lands in. |
| Whether the 9132's **DA lookup consults the FDB at all on the CPU port's own MAC**, i.e. whether there is a second, non-FDB "CPU MAC" register | the vendor's ONU MAC registers 4/5 are the ONT's *source* MAC; whether the switch engine also matches the DA against them is not visible in the module. | on the vendor board, add a static FDB entry for the board's own MAC and remove it again, and watch whether a unicast frame to the board still arrives at the CPU. |
| The exact **contents of the vendor's FDB on the live board at the time of a test** | `docs/16 §2.2` has one snapshot (one learned entry, the PC's MAC, port 2). | `tm_mactable_fdb_show` on the running vendor board while traffic flows. |
| Whether the **9212/9132 learns the CPU port's MAC from egress traffic** on the mainline board | the attribute word the driver writes enables `sa_lookup_learn_en` on PPU 5, and the vendor's own board reaches the same conclusion by learning; but no mainline-side measurement of the 9132's FDB exists, and the driver has no way to read it. | on the mainline board, with the §6.2 flood fallback in place, send one frame from the CPU to a jack and watch whether the answer comes back — or read the 9132's CAM via the alg block (needs the §7 CAM-cell protocol first). |
| Whether `sw_mac_add_cpu_macaddress` is called at all in the stock image, and by whom | it is exported from `switch.ko`, has no `tm` console wrapper, and no other module in `pon/vendor-modules/` references it (byte search over every `.ko` in `pon/vendor-modules/` and `staging/vendor-full/`). The module that provides `register_mac_ops` is not in this tree either. | read the stock rootfs's `S99modules` / init scripts and the vendor's `sh`/`cspd` binaries for the call, or find the module that owns `register_mac_ops`. |

---

## 8. Method

* `staging/swspa.py <ko> syms|dis|calls|cargs|allsym|ddata` — symbol tables and
  disassembly with the `.rela.text` / `.rela.init.text` relocations resolved
  (a `bl` in a relocatable object is a relocation placeholder, so call targets come
  from the relocation, not from the immediate).
* `staging/ctl/swat.py <ko> <lo> <hi>` — disassembly of an address range, for the
  vendor's static functions.
* Raw byte searches over every `.ko` in `pon/vendor-modules/` and
  `staging/vendor-full/` for symbol names, to find which module references which API.
* `.rodata.str1.1` extraction by section offset, to recover the vendor's own format
  strings (`mac table len is not 128bit`, `    port_id = %u`, the
  `tm_mactable_fdb_add` usage text, …).
* `ci-build-repo/tools/eram-map.py --map` — to confirm which tables are ERAM and
  which are not.

No file under `ci-build-repo` was modified; no git write command was run; the board,
the serial console and `staging/ctl/sercmd.txt` were not touched.

---

## 8. What was done about it (commit `cab7015`)

§6.2's two writes are now in the driver:

* `zx279133_9132_mf_mirror()` writes the port's flood words into ERAM table
  0x7d as well - word 1 = SPA register 18, word 2 = SPA register 17 - next to
  the attribute word it already mirrored.
* `zx279133_9132_flood_cpu_port()` writes the CPU port (PPU 5) into the
  unknown-DA, broadcast and multicast group of each of the four UNI jacks, at
  the group indexes `p<<6`, `(p+0x10)<<6` and `(p+0x20)<<6` of ERAM table 0x1f.

The value is the destination port id itself, not a bitmap: the vendor's
`group_id_table_port_id_set()` masks the port id to six bits and sets bit 7
only for an SSID port, so the entry holds one destination port. That is also
why the group cannot hold "the WAN port and the CPU port" - it holds one, and
this driver gives it the CPU port.

Both writes are reachable with the machinery the driver already had:
`zx279133_eram_window_patch()` for the 0x7d words and
`zx279133_eram_table_set_bits()` for the 0x1f entries, both of which the
console's `es`/`eg` commands already expose - so the same two writes can be
made on a running board, one command at a time, without a rebuild.
