# 01 — SK-D840N flash offset map (Skyworth ZX279133 GPON ONU, 256 MiB SPI-NAND)

**Status: reference only. Nothing in §4 has been executed. DO NOT EXECUTE §4 yet — it is gated on
the live checks in §7.**
Read-only companion docs: `docs/02-no-disassembly-reflash-plan.md`,
`docs/03-stock-uboot-recovery-probe.md` (written concurrently; they reach the same conclusion
about the partition map, and this document is consistent with them).

All facts below were derived by **static analysis only** (files on disk, no device access).
Tooling used: `devtools/fdt_dump.py` (pure-Python FDT reader, no `dtc`; supports `--at <offset>`
for FDTs embedded in containers and truncated FDTs).

Evidence labels: **[FACT]** = byte-level citation, **[INF]** = inference, **[H_B]** / **[H_A]** =
the two competing stock maps discussed in §2.

---

## 1. Executive summary (the three things that matter)

1. **[FACT] The real stock partition offsets are NOT the `/proc/mtd` listing order cumulated from
   `0x0`.** The stock bootloader's saved environment (`device-dumps/sk-d840n_b1.bin` @ `0x180146`),
   the vendor kernel DTB embedded in the same boot image (`sk-d840n_b1.bin` @ `0xc5330`, gzip →
   49 307-byte DTB, `part@256` node), and the kernel container header inside `kernel0` itself
   (`device-dumps/sk-d840n_k0.bin` @ `+0x70` / `+0x80`) **all three agree**, byte for byte, on a map
   where `parameter tags` sits at **`0x4800000`**, `kernel0` at **`0x200000`**, `kernel1` at
   **`0x2200000`**, `others` at **`0x4200000`**. See §2 and §3, table (a).
2. **[FACT] The community procedure's `0x04800000` "read the params" step is therefore *not* a
   wrong offset** — it is exactly the stock `parameter tags` start. The reason it appeared to read
   "all `0xFF`" is that the param jffs2 partition keeps its only non-erased erase block at
   *partition offset* `0x140000` (= raw `0x4940000`); the first 1.25 MiB of the partition is erased
   [FACT: `device-dumps/sk-d840n_mtd2.bin`, see §5.2]. Any read window shorter than `0x140000` at
   `0x4800000` returns all `0xFF`. → **This is open question Q1 in §7 and must be settled on the
   device before anything is erased.**
3. **[FACT] The community plan's real damage is step 6** (`nand erase 0x00f00000 0x0f100000`): it
   erases **everything from `0xf00000` to end-of-flash**, i.e. `kernel0` tail, `kernel1`, `others`,
   `parameter tags`, `usercfg`, `middleware`, `wlan` (Wi-Fi caldata), `phoneapp0/1` (VoIP),
   `osgi0/1`, `plugin_data`, `awifi`. Under the correct map the only thing rescued beforehand is
   `parameter tags`; **`others`, `wlan`, `middleware` and `usercfg` are destroyed with no
   relocation step at all** (§4, §5.6).

---

## 2. Two stock maps, and why one of them is wrong

`/proc/mtd` exposes `(dev, size, erasesize, name)` only — **no offsets**. The offsets used in the
task brief were *derived* by cumulating sizes in listing order; that derivation is **[FACT] invalid
on this unit**, because the vendor DT declares its partitions in *logical* order while assigning
*addresses* in a different order. Proof: the stock env's `mtdparts` string lists the 15 partitions
in the **same order** as `/proc/mtd` (`whole, boot, parameter tags, kernel0, middleware, usercfg,
kernel1, others, wlan, phoneapp0, osgi0, plugin_data, osgi1, phoneapp1, awifi`) with **identical
sizes**, but with explicit `@` offsets that are non-monotonic
(`sk-d840n_b1.bin` @ `0x180146`, verbatim string quoted in §3).

| | **H_A** (cumulated `/proc/mtd` order — *invalid*) | **H_B** (stock env + vendor DTB + kernel container — *authoritative*) |
|---|---|---|
| `parameter tags` | `0x200000` | **`0x4800000`** |
| `kernel0` | `0x600000` | **`0x200000`** |
| `kernel1` | `0x3200000` | **`0x2200000`** |
| `others` | `0x5200000` | **`0x4200000`** |
| `osgi0` | `0x5e00000` | **`0x6000000`** |
| `plugin_data` | `0x7c00000` | **`0x9c00000`** |

Three independent arguments for H_B, all on-disk:

* **[FACT] Bootloader env** `sk-d840n_b1.bin` @ `0x180146`: `mtdparts=...4096k@0x4800000(parameter
  tags),32768k@0x200000(kernel0)...32768k@0x2200000(kernel1)...` — and the 14 partition offsets tile
  `0x0 … 0x10000000` with no gap and no overlap (checked arithmetically; sum of sizes = 256 MiB).
* **[FACT] Vendor kernel DTB**: extracted from `sk-d840n_b1.bin` @ `0xc5330` (single gzip member,
  50 012 B decompressed = a 704-byte FIT stub + a 49 307-byte DTB). `part@256` →
  `partition@param { reg = <0x4800000 0x400000> }`, `partition@kern0 { reg = <0x200000 0x2000000> }`
  etc. — reproduces the env string exactly. This is the node the Linux driver logs as
  "dynamic partitions parse" (`compatible = "dynamic-partitions"`,
  `partitions = <0x30 0x31>`, `partition-names = "256MiB","128MiB"`,
  `devtools/uboot-stock.dts.txt:1422-1426`).
* **[FACT] `kernel0` self-description**: the container header at `sk-d840n_k0.bin` `+0x70` holds
  `{start=0x200000, size=0x2000000, end=0x2200000, 0}` and at `+0x80`
  `{start=0x2200000, size=0x2000000, end=0x4200000, 0}` — the vendor's own record of the two kernel
  slot addresses ([H_B] values; see §7). Under H_A the image sitting in `/dev/mtd3` would be at
  `0x600000` while claiming to be at `0x200000`.
* **[INF, strong] Boot-path argument**: the live `bootargs` end in `… 0x2200000 0x1 …` and the
  stock U-Boot builds that string with `"find kernel%d flash entry is 0x%x"`
  (`sk-d840n_b1.bin` @ `0xa4305`) after `"search kernel%d image failed"` / `"[WARN] kernel%d is not
  valid, "` / `"switch to kernel%d"` (@ `0xa40eb`, `0xa4109`, `0xa4128`). The device boots, so
  `0x2200000` must physically contain a valid kernel image — true under H_B (`kernel1` start),
  impossible under H_A (`0x2200000` is 28 MiB into `kernel0`, i.e. past its `0x129a1c0` =
  19,505,600 B container (§6.1), sitting in erased padding).

---

## 3. Table (a) — THIS unit's stock map, corrected (H_B)

Erase block = `0x20000` (128 KiB), page `0x800` (2048 B), OOB `0x80` (128 B) — Unim SPI-NAND.
Offsets are raw flash offsets (== `nand erase/write <off>` arguments in U-Boot).
"Mount" = where the live vendor Linux mounts it (per the verified device notes).

| mtd | label | offset | size | end | erase blks | mounted at (live) | what it holds | criticality |
|---|---|---|---|---|---|---|---|---|
| mtd0 | `whole` | `0x0` | `0x10000000` | `0x10000000` | 2048 | — | the entire chip | n/a |
| mtd1 | `boot` | `0x0` | `0x200000` | `0x200000` | 16 | — | BOOTROM/SPL stage-1 + U-Boot 2021.01-svn358069 (`"U-Boot 2021.01-svn358069 (Dec 01 2025 - 20:05:38 +0800)"` @ `b1.bin+0x92968`) + **saved env at `+0x180000`** + embedded kernel DTB at `+0xc5330` | **CRITICAL — bricking point**; env+DTB are inside it |
| mtd2 | `parameter tags` | **`0x4800000`** | `0x400000` | `0x4c00000` | 32 | `/tagparam` (rw jffs2) | jffs2: `paramtag` (344 B board data), `flag_type` (A/B slot state), `osgi_set` (`partion:1`) | **CRITICAL — board data + boot-slot bookkeeping**; only **1 of 32** erase blocks is dirty, at `+0x140000` (raw `0x4940000`) |
| mtd3 | `kernel0` | **`0x200000`** | `0x2000000` | `0x2200000` | 256 | — | ZTE/Sanechips container (magic `99999999 44444444 55555555 aaaaaaaa` @ `k0.bin+0x0`) + FIT (`d00dfeed` @ `+0x1e0`, totalsize `0x1299fdf`) with `kernel@133` = gzip arm64 Image, `fdt@133`, initrd | HIGH — recoverable: the same kernel is inside the community `uImage` (§6.6) |
| mtd4 | `middleware` | **`0x5400000`** | `0x400000` | `0x5800000` | 32 | `/important_bak` (rw jffs2) | `paramtag` + `paramtag_md5` + `flag_type_bak` + `db_backup_cfg_c.xml` (vendor's own backup of the board data) | **CRITICAL — second copy of board data; no relocation step in the community plan** |
| mtd5 | `usercfg` | **`0x4c00000`** | `0x800000` | `0x5400000` | 64 | `/userconfig` (rw jffs2) | running-config / csp database | HIGH (operator config) — **not relocated** |
| mtd6 | `kernel1` | **`0x2200000`** | `0x2000000` | `0x4200000` | 256 | — | the slot this unit booted from (live `bootargs` `0x2200000 0x1`); content not yet dumped | MEDIUM (redundant slot) |
| mtd7 | `others` | **`0x4200000`** | `0x600000` | `0x4800000` | 48 | — | vendor key/value store; the stock U-Boot reads/erases/writes a partition named **`others`** by name (`"others"` @ `b1.bin+0x9c16f`, immediately followed by `" [%s] read failed! iret=%d mtdpart=%s mtd_offset=0x%x mtd_length=0x%x"` @ `+0x9c176` and the erase/write twins @ `+0x9c1bd`, `+0x9c205`) → board_info/vid/`skyregioncode` class data (`board_info_name` `+0x9c511`, `board_info_id` `+0x9c521`, `skyregioncode` `+0xa223f`) [INF: name↔purpose] | **CRITICAL + IRREPLACEABLE — not relocated by the community plan** |
| mtd8 | `wlan` | **`0x5800000`** | `0x400000` | `0x5c00000` | 32 | `/wlan` (rw jffs2) | QCA98xx RF calibration / MAC (regulatory) | **CRITICAL — not relocated**; losing caldata is a hardware-level loss |
| mtd9 | `phoneapp0` | **`0x5c00000`** | `0x200000` | `0x5e00000` | 16 | `/usr/local/upgrade_module` | VoIP application slot 0 | MEDIUM |
| mtd13 | `phoneapp1` | **`0x5e00000`** | `0x200000` | `0x6000000` | 16 | — | VoIP application slot 1 | MEDIUM |
| mtd10 | `osgi0` | **`0x6000000`** | `0x1e00000` | `0x7e00000` | 240 | — | OSGi/JVM bundle root, slot 0 | MEDIUM |
| mtd12 | `osgi1` | **`0x7e00000`** | `0x1e00000` | `0x9c00000` | 240 | `/usr/local/osgi` (ro) | OSGi bundle root, slot **1 active** (`osgi_set` = `partion:1`, `mtd2.bin+0x140170`) | MEDIUM |
| mtd11 | `plugin_data` | **`0x9c00000`** | `0x6000000` | `0xfc00000` | 768 | `/usr/data` (rw jffs2) | persistent plugin/DB data | HIGH (user data) |
| mtd14 | `awifi` | `0xfc00000` | `0x400000` | `0x10000000` | 32 | — | awifi state | LOW-MEDIUM |

Notes on the table: [FACT] sizes/names/order are from live `/proc/mtd` as quoted in the task
brief and match the stock `mtdparts` string 1:1 (all 15). Offsets are the H_B values.
`0x04800000`-adjacent caution: the vendor's own `sky_bakeup_info` command in **both** bootloaders
uses hard-coded raw offsets (`"nand erase f000000 400000"`, `"nand read %x ad00000 300000"`,
`"nand read %x 1900000 400000"`, `"nand write %x f000000 300000"`, `"nand write %x f400000 400000"`)
— community `boot.bin` @ `0xa5a6c…0xa5aff`, stock `b1.bin` @ `0xa5af4…0xa5b87`. It reads
`0x1900000`/`0xad00000` and writes inside `plugin_data`; i.e. the vendor tooling itself assumes a
fixed physical map, another reason never to trust a derived offset list.

## 3b. Table (b) — the map the community `boot.bin` / `board.dtb` expect

Source 1: **embedded saved env** in `sk-d840n-openwrt/binary/boot.bin` @ `0x180000` (a real,
CRC-valid U-Boot env block — see §6.5). Verbatim at `+0x1801cf`:

```
mtdids=nand0=spi-nand0
mtdparts=mtdparts=spi-nand0:262144k@0x0(whole),2048k@0x0(boot),8192k@0x200000(kernel),
          1024k@0xa00000(dtb),4096k@0xb00000(parameter tags),246784k@0xf00000(root),
```

Source 2: `sk-d840n-openwrt/binary/board.dtb`, `/part@256` (and `/part@128`) —
`python devtools/fdt_dump.py sk-d840n-openwrt/binary/board.dtb --node /part@256`.

| mtd(block) idx | label | offset | size | end | erase blks | used by |
|---|---|---|---|---|---|---|
| 0 / mtdblock0 | `whole` | `0x0` | `0x10000000` | `0x10000000` | 2048 | — |
| 1 / mtdblock1 | `boot` | `0x0` | `0x200000` | `0x200000` | 16 | README step 2 |
| 2 / mtdblock2 | `kernel` | `0x200000` | `0x800000` | `0xa00000` | 64 | `bootcmd=mtd read kernel 0x88000000` (env @ `+0x1800c1`); README step 3 writes `uImage` (4 814 777 B ≤ 8 MiB ✔) |
| 3 / mtdblock3 | `dtb` | `0xa00000` | `0x100000` | `0xb00000` | 8 | `mtd read dtb 0x88800000`; README step 4 writes `board.dtb` (48 463 B ≤ 1 MiB ✔) |
| 4 / mtdblock4 | `parameter tags` | `0xb00000` | `0x400000` | `0xf00000` | 32 | README step 5 destination |
| 5 / mtdblock5 | `root` | `0xf00000` | `0xf100000` | `0x10000000` | 1928 | `bootargs=… root=/dev/mtdblock5 rootfstype=jffs2` (env @ `+0x180014`) ✔ consistent |

Cross-check: the two sources agree byte-for-byte on offsets/sizes/labels, so the community layout
is internally coherent. [FACT] `devtools/uboot-stock.dts.txt` vs
`board.dtb`: their DTB is the **vendor DTB with only the partition nodes swapped** (49 307 → 48 463
B, −844 B); a content-only diff (`fdt_dump` listings, offset notes stripped) shows **exactly** these
changes: `part@256` and `part@128` partition sets replaced, and **`partition-names` corrupted**
(§6.3). Everything else — `model = "ZTE 133"`, `compatible = "zte,133"`, `memory@80000000
reg = <0x80000000 0x20000000>` (512 MiB), `chosen/bootargs = "console=ttyAMA0,115200n8
rdinit=/sbin/init mem=1024M "`, `pon_plat`, all controller nodes — is identical to stock.

---

## 4. Corrected flashing sequence — **DOCUMENTATION ONLY, DO-NOT-EXECUTE-YET**

Gate: run §7 Q1–Q4 first. Every step below is annotated with the **stock partition it destroys
under H_B** (and under H_A where it differs). `filesize` values: `boot.bin` = `0x200000`,
`uImage` = `0x4977b9`, `board.dtb` = `0xbd4f`. All offsets are 128 KiB-aligned ✔ (U-Boot `nand
erase`/`nand write` require that).

**Phase 0 — mandatory backups (read-only), before any erase.** Without these, do not proceed.

| # | command (Linux side) | saves | why |
|---|---|---|---|
| 0.1 | `dd if=/dev/mtd1ro of=boot.bin` (2 MiB) | `boot` | already have ✔ (`device-dumps/sk-d840n_b1.bin`) |
| 0.2 | `dd if=/dev/mtd2ro of=param.bin` (4 MiB) | `parameter tags` | already have ✔ (`sk-d840n_mtd2.bin`) |
| 0.3 | `dd if=/dev/mtd4ro of=important_bak.bin` (4 MiB) | `middleware` | vendor copy of `paramtag`+`paramtag_md5`+`flag_type_bak` |
| 0.4 | `dd if=/dev/mtd5ro of=usercfg.bin` (8 MiB) | `usercfg` | operator config |
| 0.5 | `dd if=/dev/mtd7ro of=others.bin` (6 MiB) | `others` | **the U-Boot key/value store (§3); no community step relocates it** |
| 0.6 | `dd if=/dev/mtd8ro of=wlan.bin` (4 MiB) | `wlan` | **RF caldata; irreducible if lost** |
| 0.7 | `dd if=/dev/mtd3ro of=kernel0.bin` (32 MiB) and `dd if=/dev/mtd6ro of=kernel1.bin` (32 MiB) | both kernel slots | only 2 MiB of `kernel0` is captured today → rollback is incomplete |
| 0.8 | `dd if=/dev/mtd10ro…mtd14ro` | `osgi0/1`, `phoneapp0/1`, `plugin_data`, `awifi`, as space allows | 96 MiB `plugin_data` is user data (`/usr/data`) |

**Phase 1 — bootloader** (targets stock `boot`, `0x0–0x200000`; destroys the *stock env* at
`boot+0x180000` — this is why the shipped `boot.bin` carries its own env block [FACT:
`boot.bin+0x180000`, CRC validated §6.5]).

```
tftpboot 0x88000000 boot.bin
nand erase 0x00000000 0x00200000            # destroys: mtd1 boot (SPL+u-boot+env+embedded DTB)
nand write 0x88000000 0x00000000 ${filesize} # filesize=0x200000
```
*Same as README step 2 — no offset error there.* [FACT] `boot.bin+0x0 … +0x1ffff` is byte-identical
to `b1.bin+0x0 … +0x1ffff` (0 differing bytes over the first 128 KiB), so the vendor stage-1/SPL is
preserved; the differences are confined to `0x20000–0xd0000` (rebuilt U-Boot proper,
`svn308776` vs stock `svn358069` — i.e. **older** than the stock build) and the env at `0x180000`.

**Phase 2 — kernel** (their `kernel` = `0x200000+0x800000`):

```
tftpboot 0x88000000 uImage
nand erase 0x00200000 0x00800000            # H_B: destroys kernel0[0 .. 8 MiB] = container header + FIT head
                                            # H_A: destroys ALL of parameter tags (0x200000-0x600000)
                                            #        + kernel0 head   <-- the feared collision
nand write 0x88000000 0x00200000 ${filesize} # 0x4977b9 bytes
```

**Phase 3 — dtb** (`0xa00000+0x100000`): under H_B this is still inside `kernel0` (which phase 2
already overwrote) → harmless; under H_A it is inside `kernel0` too.

```
tftpboot 0x88000000 board.dtb
nand erase 0x00a00000 0x00100000
nand write 0x88000000 0x00a00000 ${filesize} # 0xbd4f
```

**Phase 4 — relocate the board data.** Corrected source for THIS unit:

```
# H_B source (authoritative):  nand read 0x88000000 0x04800000 0x00400000
#     → raw 0x4800000..0x4c00000 == the WHOLE parameter tags partition, whose only
#       dirty erase block sits at +0x140000 (raw 0x4940000). Reading <0x140000 from
#       0x4800000 yields all 0xFF and LOOKS like an empty partition. Verify §7 Q1
#       before trusting either number.
# H_A source (if Q1 proves the listing order):  nand read 0x88000000 0x00200000 0x00400000
nand erase 0x00b00000 0x00400000             # their parameter tags; H_B: inside kernel0 tail
nand write 0x88000000 0x00b00000 0x00400000
```

**Phase 5 — rootfs.** `nand erase 0x00f00000 0x0f100000` (= `0xf00000 … 0x10000000`, everything
left) destroys, under **H_B**: `kernel0` remainder, `kernel1` (all), `others` (all), `parameter tags`
(all, after the copy), `usercfg` (all), `middleware` (all), `wlan` (all), `phoneapp0`, `phoneapp1`,
`osgi0`, `osgi1`, `plugin_data`, `awifi` — **12 partitions**. Under **H_A** it likewise destroys
`kernel0` remainder, `middleware`, `usercfg`, `kernel1`, `others`, `wlan`, `phoneapp0`, `osgi0`,
`plugin_data`, `osgi1`, `phoneapp1`, `awifi`. Either way: nothing is spared except `boot` and the
relocated param block, and `others`/`wlan`/`middleware`/`usercfg` have **no relocation step**.

```
tftpboot 0x88000000 rootfs.jffs2             # mkfs.jffs2 -d rootfs -l -n -s 0x800 -e 0x20000 (their scripts/pack_to_jffs2.sh)
nand erase 0x00f00000 0x0f100000             # <-- the destructive step, see above
nand write 0x88000000 0x00f00000 ${filesize}
reset
```

**Phase 6 — recommended instead of Phase 5 (documentation only).** Keep the vendor table and take
only space that is provably spare, so `others`/`wlan`/`middleware`/`usercfg`/`parameter tags`
survive in place. This needs a *custom* `board.dtb` + env (the vendor `part@256` set is present in
`boot.bin+0xc52a8` unchanged, so it is the natural base):

| new label | offset | size | stock owner (H_B) | note |
|---|---|---|---|---|
| `boot` | `0x0` | `0x200000` | `boot` | unchanged |
| `kernel` | `0x200000` | `0x800000` | `kernel0` head | as community plan |
| `dtb` | `0xa00000` | `0x100000` | `kernel0` | as community plan |
| `root` | `0xb00000` | `0x7500000`… pick `0x2200000`+`0x2000000` = `kernel1` instead | `kernel1` | put OpenWrt in the **spare A/B slot** (32 MiB) and leave `others`+`parameter tags`+`usercfg`+`middleware`+`wlan` intact |
| preserved | `0x4200000/0x600000` `0x4800000/0x400000` `0x4c00000/0x800000` `0x5400000/0x400000` `0x5800000/0x400000` | | `others` `parameter tags` `usercfg` `middleware` `wlan` | keep the vendor labels in the DTB so the vendor kv store and caldata stay addressable |
| `rootfs2` (optional, big) | `0x9c00000` | `0x6000000` | `plugin_data` | only after 0.8 backup |

---

## 5. Mismatch findings (explicit)

### 5.1 The "0x200000 collides with parameter tags" claim
True **only under H_A**. Under H_B, `0x200000` is `kernel0`'s start and the README step-3 erase
(`0x200000`, 8 MiB) hits kernel0 only [FACT: three independent sources, §2]. The community plan
*does* still destroy the board data — just later, in phase 5. Under either map,
`nand erase 0x00f00000 0x0f100000` reaches past every remaining vendor partition.

### 5.2 The `0x04800000` "all-FF" source
[FACT] `device-dumps/sk-d840n_mtd2.bin` (4 MiB, the live `parameter tags`): exactly **one**
64 KiB window is non-erased, at `+0x140000`; the other 63 are all `0xFF`
(coverage scan: 1 used / 63 erased). The first non-`0xFF` byte is at `+0x140000`
(jffs2 `0x1985 0xe002` dirent header: `85 19 02 e0 44 00 00 00 1d fb f7 98 …`). So:
* a read of `0x4800000` for `0x100000` bytes → **all `0xFF`**, matching the observation;
* a read of `0x4800000` for the full `0x400000` (as the README does) → **must** contain that block
  if H_B holds;
* if H_A holds, `0x4800000` falls inside `kernel1` (`0x3200000…0x5200000`) and the whole 4 MiB read
  is legitimately `0xFF`.
**This single test discriminates H_A from H_B** → §7 Q1.

### 5.3 `parameter tags` contents are not the plaintext the brief assumes
[FACT] Searches over the whole 4 MiB of `sk-d840n_mtd2.bin`: `TAGH0201` 0 hits, `H0201` 0,
`D0458D` 0, `SK-D840N`/`SKD840N` 0, `231993` 0, `SKYWA` 0, `serial` 0, `password` 0, `GPON` 0.
The only readable strings in the entire partition (15 printable runs ≥ 6 chars) are jffs2 file
names / small values, all inside that one erase block:
`current : 324` @ `+0x140088`, `paramtag` @ `+0x1400bf`, `flag_type` @ `+0x1400f0`,
`osgi_set` @ `+0x140124`, `partion:1` @ `+0x140170`, plus older `flag_type` versions
`current : 0` @ `+0x140887`, `+0x141888`, `+0x144888`, `+0x148888`, `+0x149088`,
`current : 306` @ `+0x142887`, `current : 310` @ `+0x145888`. [INF] the `paramtag` payload is
obfuscated/encrypted (high-entropy bytes from `+0x140180`), so **the MAC prefix / serial / PON
calibration cannot be confirmed or re-derived from this dump** — and cannot be verified as present
until the plaintext source is found (see also `others`, §3 mtd7, and Q5/Q9).

### 5.4 dtb / kernel / rootfs placement under the community layout
* `kernel` @ `0x200000` **coincides with stock `kernel0`'s start**, but is only 8 MiB of the stock
  32 MiB slot; the rest of stock `kernel0` (`0xf00000` … `0x2200000`) is then erased in phase 5
  [FACT: sizes/offsets from both tables in §3].
* `dtb` @ `0xa00000` and `parameter tags` @ `0xb00000` both land **inside stock `kernel0`** under
  H_B ([INF] harmless only because kernel0 is already being overwritten). Note that under H_B the
  8 MiB `kernel` partition is *smaller* than the stock kernel image (`kernel0` content is
  0x129a1c0 = 19,505,600 B ≈ 18.6 MiB, §6.1) — so the community's kernel must be their 4.8 MB `uImage`,
  which it is.
* `root` @ `0xf00000` size `0xf100000` = 241 MiB — it covers 12 of the 15 stock partitions (§4
  phase 5). This is the whole problem with the plan: it is a *repartition*, not an overlay.

### 5.5 Where the params must be relocated, and who reads them after flashing
* The **new Linux** reads them from its DT: `board.dtb /part@256/partition@param { label =
  "parameter tags"; reg = <0xb00000 0x400000> }` → `/dev/mtd4`, mounted as `/tagparam` by the
  vendor userspace (not by the OpenWrt rootfs — see §5.7).
* The **new U-Boot** reads them from its saved env: `parameter tags @0xb00000 size 4096k`
  (`boot.bin+0x1801cf`) — same place ✔. So **destination `0xb00000` is correct** for the new layout,
  and the README's `nand erase 0x00b00000 0x00400000` + write is the right relocation form.
* **[FACT] risk:** `boot.bin`'s *embedded kernel DTB* (`boot.bin+0xc52a8`, 49 307 B) is
  **byte-identical to the stock one** (both inflate to exactly 50 012 B / 49 307 B; compare with the
  independently extracted copy already in the tree, `devtools/uboot-stock-b.dtb`)
  — i.e. it still carries the 15-partition `part@256` table with **no `kernel`/`dtb` labels**. If
  their U-Boot regenerates `mtdparts` from that DT at boot (the stock code path exists in both:
  `"partitions"`, `"partition-names"`, `"%s: failed to get reg in %s"`, `"%dk@0x%x(%s),"` at
  `b1.bin+0x9bfda…0x9c0a0`), symbol `board_mtdparts_default` @ `boot.bin+0x8de28` /
  `b1.bin+0x8de78`), the env's `kernel`/`dtb` names would disappear and
  `bootcmd=mtd read kernel …` would break. → §7 Q6.

### 5.6 `others` and `wlan` are erased with no relocation — **the worst mismatch**
[FACT] `others` @ `0x4200000` is inside phase 5's erase range and is not present in the community
table at all; the stock U-Boot's partition-key/value helper names it explicitly (`b1.bin+0x9c16f`,
read/erase/write error strings `+0x9c176/0x9c1bd/0x9c205`). Same for `wlan` (caldata),
`middleware` (the *second* copy of `paramtag` + `paramtag_md5`), `usercfg`. Any future attempt must
either back these up (§4 phase 0) or keep them in the new table (§4 phase 6).

### 5.7 The shipped rootfs has no partition knowledge
[FACT] `sk-d840n-openwrt/rootfs/` is a generic `armsr/armv8` OpenWrt 24.10.8 tree
(`/etc/openwrt_release`: `DISTRIB_TARGET='armsr/armv8'`, `r29233-443ec4032a`; `/etc/device_info`:
`DEVICE_PRODUCT='Generic'`). **No `fw_env.config`** anywhere in the tree, no board-specific
`/lib/upgrade/platform.sh` content for this flash, `/etc/fstab` is an empty template, and
`find rootfs -iname '*mtd*' -o -iname 'fw_env*'` yields only stock OpenWrt plumbing
(`sbin/mtd`, `sbin/sysupgrade`, `etc/sysupgrade.conf`). ⇒ after flashing you cannot `fw_printenv`
and `sysupgrade` will not know this layout; recovery is TTL-only.

### 5.8 Defects found in the community `board.dtb` (both new to this analysis)
1. [FACT] **`partition-names` corrupted.** Vendor: 14-byte property `32 35 36 4d 69 42 00
   31 32 38 4d 69 42 00` = `"256MiB","128MiB"` (`devtools/uboot-stock.dts.txt:1425`). Community:
   12-byte property `32 35 36 4d 69 42 0a 38 4d 69 42 00` = one string `"256MiB\n8MiB"` — the DTS
   source was almost certainly `"256MiB\0128MiB"`, and the compiler read `\012` as an **octal
   escape for newline**, eating the `12` of `128MiB`. Both `nand@01000000/partitions` and
   `spi-flash@0/partitions` carry it (`board.dtb` → `fdt_dump` lines for those nodes). [INF] Impact:
   the dynamic-partition selector formats the detected size as `"%lldMiB"` and looks it up in
   `partition-names`; `"256MiB\n8MiB" != "256MiB"` → no table may be selected. Must be re-checked on
   a console (§7 Q7).
2. [FACT] **`/part@128` is self-inconsistent**: `whole = <0x0 0x8000000>` (128 MiB) but `root =
   <0xf00000 0xf100000>` which ends at `0x10000000` (256 MiB) — it overruns a 128 MiB device by
   128 MiB. Only the `part@256` table is valid on this unit.
3. [FACT] **Both flash controllers are `status = "disabled"`** in the shipped `board.dtb`:
   `nand@01000000` (`compatible = "zxic,zx-denali-nand"`, status `disabled`) and
   `spifc@0x10d0f000` (`compatible = "zxic,zx-spifc"`, status `disabled`, child `spi-flash@0`
   `compatible = "spi-nand"`, `partitions { compatible = "dynamic-partitions"; partitions =
   <0x30 0x31>; }`). Identical to stock (the stock vendor DTB has the same `disabled` — this is
   therefore **not** a community regression, but it is unresolved: [INF] something — the vendor
   kernel driver ignoring `status`, or a U-Boot DT fixup — must be enabling the SPI-NAND path,
   otherwise no `/dev/mtd*` and no jffs2 root. §7 Q7.
4. [FACT] `chosen/bootargs = "console=ttyAMA0,115200n8 rdinit=/sbin/init mem=1024M "` — `mem=1024M`
   on a 512 MiB board (`memory@80000000 reg = <0x80000000 0x20000000>`; live dmesg reports
   `Memory: 405372K/524288K`). Inherited from the vendor DTB unchanged. [INF] U-Boot's `bootm`
   overwrites `/chosen/bootargs` from the env (env `bootargs` has no `mem=`), so it is probably
   inert — but if their U-Boot does *not* fix up `/chosen`, `mem=1024M` would be fatal. §7 Q8.

---

## 6. Extra reverse-engineering (evidence for §5, requested by the brief)

### 6.1 ZTE/Sanechips kernel container header (`device-dumps/sk-d840n_k0.bin` = first 2 MiB of `kernel0`)
All multi-byte fields are **little-endian**:

| offset | bytes / value | field (confidence) |
|---|---|---|
| `+0x00` | `99 99 99 99 44 44 44 44 55 55 55 55 aa aa aa aa` | container magic, 16 B **[FACT]** |
| `+0x10`–`+0x1f` | `00`×16 | reserved |
| `+0x24` | `V2.0.0\0…` | format/version tag (ASCII) |
| `+0x50` | `0x00000001` | header/image version [INF] |
| `+0x54` | `0x0129a1c0` (19 505 600) | **total container length** = `0x1e0` + `0x1299fe0` (16-byte-aligned payload) **[FACT: arithmetic]** |
| `+0x58` | `0x01299fdf` (19 505 119) | **payload length** = the embedded FIT's declared `totalsize` at `+0x1e0` **[FACT: exact match]** |
| `+0x5c` | `0x000001e0` | payload offset (= header size); the FIT starts here **[FACT]** |
| `+0x60` | `0x92efdacb` | checksum, **algorithm not identified** (tried crc32 over `+0x1e0..`, header with field zeroed, `+0x0..0x1e0`, `+0x64..0x1e0` — no match) |
| `+0x68` | `0x0129a1c0` | repeat of total length |
| `+0x70` `+0x74` `+0x78` `+0x7c` | `0x200000` `0x2000000` `0x2200000` `0` | **kernel slot 0 descriptor: {start, size, end, pad}** = H_B `kernel0` **[FACT: matches env/DTB]** |
| `+0x80` `+0x84` `+0x88` `+0x8c` | `0x2200000` `0x2000000` `0x4200000` `0` | **kernel slot 1 descriptor** = H_B `kernel1` **[FACT]** |
| `+0x90` | `"ZXIC 133 UNI V2.0.0 286632 CM OSGI"` | product/build string (36 B) |
| `+0x100`..`+0x10f` | `00×7 01 00 00 00 00 01 00 00 00` | small flags [UNK] |
| `+0x110` | `77 f8 c4 e6` then `"20251201200927"` | checksum/hash candidate + **build timestamp** — identical to the timestamp in the live `bootargs` **[FACT]** |
| `+0x130` | `00 00 00 10 ff ff 00 00` `"V2.0"` | hardware/version fields [UNK] |
| `+0x158` | `"133"` | board/SoC family |
| `+0x170` | `03 30 00 00 00 e0 00 02` `"CMNV1.0.024"` | CMNV version block + `0xe0` (=480, the header size ✔) |
| `+0x17c`.. | `ff` fill | padding to `+0x1e0` |

Payload at `+0x1e0` = **FIT** (`d00dfeed`, version 17, `off_dt_struct=0x38`,
`off_dt_strings=0x1299b80`, size `0x79`). Recovered with
`python devtools/fdt_dump.py device-dumps/sk-d840n_k0.bin --at 0x1e0` (truncated-FDT mode):
`description = "U-Boot uImage source file for 133 project"`; `images { kernel@133 { description =
"Linux kernel for 133"; data = <gzip> … } }`; a 4-byte property (`#name111`) = `0x692d85aa`
[INF: an `iMagicNum`-style marker — the same vendor code prints
`"Please check file downloaded, iMagicNum:%x"` @ `b1.bin+0xa1f05`]. Only ~2 MiB of the FIT was
captured, so the `fdt@133`/ramdisk image nodes and any `encryption` property are beyond the window
→ **do not conclude "not AES-encrypted" from absence; conclude it from what is visible**: the
`kernel@133` payload at `+0x2d4` is a **plain gzip stream** that inflates to a valid arm64 Image
(`text_offset = 0x80000`, `image_size = 0xaa3000`, 4 117 533 B inflated from the captured prefix)
**[FACT]**, so on this unit the kernel image is in the clear ⇒ the AES path
(`"Backup aes key for which image ? (uboot/kernel)"` @ `b1.bin+0xa2d51`, `set aes key` `+0xa43b4`,
`"non secure boot "` @ `+0xa45ce`) is **not** actively encrypting flash contents.

### 6.2 Stock boot image = vendor BOOTROM container
[FACT] `b1.bin+0x0 = 00 02 00 14 …`, `+0x10 "133s"`, `+0x80 "0001ZXIC.."`, `+0x90 "BOOTROM"`,
`+0x98 "RSA2048"`, `+0xac 0x00000004`, `+0xb0 0x00009480`; identical first 0x120 bytes in
`boot.bin`. Structure of both 2 MiB images (64 KiB granularity): data `0x0–0x10000`, zeros
`0x10000–0x20000`, code `0x20000–0xd0000`, `0xFF` `0xd0000–0x180000`, **env `0x180000–0x190000`**,
zeros `0x190000–0x1a0000`, `0xFF` to `0x200000`. Stock-vs-community diff: 447 393 bytes (21.3 %),
confined to `0x20000–0xd0000` (rebuilt U-Boot) + 672 bytes at `0x180000` (env). **Stage-1
`0x0–0x1ffff` is byte-identical.**

### 6.3 Stock U-Boot embedded DTB (the `zxboot` path)
`b1.bin+0xc5330` (and `boot.bin+0xc52a8`) hold one gzip member (50 012 B inflated) = a 704-byte
mini-FIT wrapper (`default = "conf@133"`, `conf@133 { description = "zx279133-00080001"; boardtype
= "133 Product Board"; fdt = "fdt@133"; }`, `fdt@133 { data-size = 0xc09b; data-offset = 0;
arch = "arm64"; compression = "none"; type = "flat_dt"; }`) followed by the 49 307-byte DTB.
Extraction one-liner (re-runnable):

```bash
python - <<'EOF'
import zlib, struct
d = open('device-dumps/sk-d840n_b1.bin','rb').read()
i = d.find(b'\x1f\x8b\x08'); blob = zlib.decompressobj(47).decompress(d[i:], 1<<24)
open('/tmp/stock_embedded.dtb','wb').write(blob[0x2c0:0x2c0+struct.unpack_from('>I',blob[0x2c0],4)[0]])
EOF
python devtools/fdt_dump.py /tmp/stock_embedded.dtb --node /part@256
```

### 6.4 The two bootloaders' environment variables (full saved env blocks)
`b1.bin` env @ `0x180004` (stock, live) — 16 vars: `baudrate=115200`;
`bootargs=console=ttyAMA0,115200n8 rdinit=/sbin/init U-Boot 2.0.0 20251201200927 0x2200000 0x1 0x83
0x83  UpModeInfo=0x00000040  regioncode=0x0 `; `bootcmd=zxboot`; `bootdelay=1`; `ethact=eth0`;
`ethaddr=00:29:47:00:00:32`; `ext_args=UpModeInfo=0x00000040`; `fdtcontroladdr=9f6efd40`;
`ipaddr=192.168.1.1`; `mtdids=nand0=spi-nand0`; `mtdparts=<H_B 15 entries>`; `serverip=192.168.1.101`;
`stderr=serial`; `stdin=serial`; `stdout=serial`. [FACT] equals the live `/proc/cmdline`.
Compiled-in default env (both images, `b1.bin+0x8e9d0` / `boot.bin+0x8e980`) is only
`bootcmd=zxboot, bootdelay=1, baudrate=115200, ipaddr=192.168.1.1, serverip=192.168.1.101` —
**no `mtdparts`, no `bootargs`, no `ethaddr`** → if the env block is lost, the board falls back to
that minimal default and cannot find any partition [FACT].

`boot.bin` env @ `0x180004` (community, 17 vars) — same minus `bootcmd`, plus
`bootcmd=mtd read kernel 0x88000000; mtd read dtb 0x88800000; bootm 0x88000000 - 0x88800000`,
`bootargs=… root=/dev/mtdblock5 rootfstype=jffs2 printk.time=1 U-Boot 2.0.0 20250417161359 0x200000
0x0 0x83 0x83 UpModeInfo=0x00000040 regioncode=0x0`, `mtdparts=<6-entry table>`,
`fileaddr=88000000`, `filesize=bd4f` (← leftovers: `0xbd4f` is **exactly `board.dtb`'s size**, so the
shipped env was saved on a unit mid-tftp-session [FACT]).

### 6.5 Env block CRC (both images validate)
[FACT] Layout is `[u32 LE crc32][name=value\0 …][0x00 padding]` with **`ENV_OFFSET = 0x180000`
inside `boot`, `ENV_SIZE = 0x20000` (one 128 KiB erase block, `0x180000…0x1a0000`)**: `zlib.crc32`
over the `0x1fffc` bytes following the CRC field reproduces `0xc638125c` (stock) and `0x3c568477`
(community) exactly. Consequence: a bad CRC ⇒ U-Boot discards the env and uses the minimal
default from §6.4 (which has no partition table).

### 6.6 Community `uImage` header (`sk-d840n-openwrt/binary/uImage`, 4 814 777 B)
Legacy U-Boot image (`ih_magic = 0x27051956`), 64-byte header (layout + `IH_*` enums verified
against `uboot-an758x/include/image.h:326-338` and the tables in `uboot-an758x/boot/image.c`):

| field | value | decode |
|---|---|---|
| `ih_hcrc` | `0x56fc2d1f` | **MISMATCH** — crc32 over the header with the field blanked = `0xc1bd7e0c` [FACT] |
| `ih_time` | `1785073719` = 2026-07-26 13:48:39 UTC | [FACT] a build-clock or post-hoc patch artefact |
| `ih_size` | `0x497779` (4 814 713) | 64 + 4 814 713 = 4 814 777 = file size, no trailing bytes ✔ |
| `ih_load` | `0x80080000` | matches the arm64 Image's own `text_offset = 0x80000` ✔ |
| `ih_ep` | `0x80080000` | ✔ |
| `ih_dcrc` | `0x59a6848b` | **MATCH** → payload intact [FACT] |
| `ih_os/arch/type/comp` | `5 / 22 / 2 / 1` | `IH_OS_LINUX / IH_ARCH_ARM64 / IH_TYPE_KERNEL / IH_COMP_GZIP` ✔ correct for this target |
| `ih_name` | `"Linux kernel for 133"` | **identical to the stock FIT's `kernel@133` description** (§6.1) |

Payload inflates to 10 676 232 B; contains a second `1f 8b 08` at `+0x31bd8c` that does **not**
inflate (`header crc mismatch`) → [INF] a boundary artefact, not a concatenated ramdisk.
Decoded text: `Linux version 4.19.136+ (fanqiang@skyworth) (gcc version 5.3.1 20160412 (Buildroot
2017.05)) #4 SMP Mon Jun 3 …` ⇒ **[FACT] the "OpenWrt" kernel they ship is the stock Skyworth
4.19.136+ kernel**, repackaged without the ZTE container header and without the initrd (stock root
arrives as initrd — `Freeing initrd memory: 14112K`; theirs must use a real root, hence
`root=/dev/mtdblock5 rootfstype=jffs2` ✔ consistent with their `bootcmd`).
**Fit with `bootcmd`:** ✔ `mtd read kernel 0x88000000` (8 MiB partition, 4.8 MB image),
`mtd read dtb 0x88800000` (1 MiB partition, 48 463 B DTB), `bootm 0x88000000 - 0x88800000`;
both load addresses sit in RAM `0x80000000+512 MiB` and below the reserved regions
(`ipsec_ddr@90000000` 2 MiB, `woe_ddr@91000000` 32 MiB, `pon_rsvmem` size `0x3245000` ≈ 50.3 MiB —
all from `board.dtb`). ⚠ The `ih_hcrc` mismatch matters: mainline `bootm` calls
`image_check_hcrc()` unconditionally for legacy images and aborts with `Bad Header Checksum`
(`uboot-an758x/boot/bootm.c:78`) → §7 Q4.

### 6.7 `bootargs` field decoding (brief item 4)
Format strings found in **both** bootloaders (`b1.bin` offsets; community equivalents at the same
relative positions: `0xa41c9`, `0xa41d2`, `0xa4063`…):

```
+0xa4229  "U-Boot %s %c%c%c%c%c%c%c%c%c%c%c%c%c%c "     <- literal + version + 14 chars
+0xa4251  "kernel%d"
+0xa425a  "0x%1x 0x%02x 0x%02x "
+0xa426f  " %s "            <- appends ext_args (UpModeInfo=...)
+0xa4274  "set bootargs,str is %s "
+0xa4296  " %s=0x%x "       <- UpModeInfo / regioncode
+0xa42a4  "#conf@131" "#conf@132" "#conf@133" "#conf@133E"
+0xa42e0  "bootm 0x%x%s "
+0xa4305  "find kernel%d flash entry is 0x%x"
+0xa4328  "header crc failed"
```

Mapping onto `U-Boot 2.0.0 20251201200927 0x2200000 0x1 0x83 0x83`:

| token | stock | community | meaning |
|---|---|---|---|
| `U-Boot %s` | `2.0.0` | `2.0.0` | [INF] vendor image/ABI tag, *not* the U-Boot version (`2021.01-svn…` is printed separately at `+0x92968`) |
| `%c×14` | `20251201200927` | `20250417161359` | [FACT] build timestamp — stock `…(Dec 01 2025 - 20:05:38)`, community `…(Apr 17 2025 - 16:09:35)`; and it equals the container's own `+0x114 "20251201200927"` (§6.1) ⇒ it is read from the image header, not from `__DATE__` |
| first `0x…` | `0x2200000` | `0x200000` | [FACT-ish] the **flash entry address of the kernel slot actually used**, per `"find kernel%d flash entry is 0x%x"` — under H_B: stock `kernel1` @ `0x2200000`, community `kernel` @ `0x200000` (the H_B `kernel0` start). Under H_A neither value names any partition |
| `0x%1x` | `0x1` | `0x0` | [INF, strong] the `kernel%d` slot index (`"search kernel%d image failed"`, `"switch to kernel%d"` → A/B fallback list) |
| `0x%02x 0x%02x` | `0x83 0x83` | `0x83 0x83` | [INF] two **single-byte board/SoC identifiers**, printed with `%02x`; both bootloaders report the same pair on the same board ⇒ board-derived, not offset-derived. `0x83 = 131`, and the sibling FIT configs are named `#conf@131/132/133/133E` ⇒ these belong to the 131…133 board-type family (which config to select is decided elsewhere: `"config string: %s"` @ `b1.bin+0xa42cd`). Not decidable offline → §7 Q3 |

`UpModeInfo=0x00000040`: [FACT] carried in `ext_args=UpModeInfo=0x00000040` in both envs, and the
shipped DTB's `pon_plat` node (identical in stock and community) declares
`neglect_bootargs_up_mode_info = <0x0>` / `kernel_default_up_mode_info = <0x40>` /
`uboot_default_up_mode_info = <0x40>` and `bosa_and_switch_ponmode_info = <0x5200040>`. [FACT] the
stock U-Boot decodes it as a **bitfield of upstream-port selection**:
`"[%s] UpModeInfo=0x%x cur_up_mode=0x%x up_mode_auto_en=%d  lan_up_port=%d  wlan_up_port=%d"`
(`b1.bin+0xa47b8`), `"[%s] UpModeInfo=0x%x ponmode_auto_en=%d upmode=0x%x lan_up_port=%d
wlan_up_port=%d"` (`+0xa4813`), `"sky_ponmode"` (`+0xa4633`), `"%s. upmode: 0x%x"` (`+0xa463f`),
plus `"set_bosa_mode is [xgpon]"` / `"gpon_rxlos,xgpon_rxlos"` (`+0x9bf00`, `+0x9bf1a`).
⇒ **UpModeInfo = which port (LAN/WLAN/which PHY) is the PON "uplink"/up-mode + auto-enable flags,
0x40 = bit 6**, and because `neglect_bootargs_up_mode_info = <0>` the DT *honours* the value passed
in bootargs (a stale `UpModeInfo` in env therefore actually changes behaviour). [INF]
`regioncode=0x0` is the vendor region/regulatory code (`"regioncode: %d"` `+0xa222f`,
`skyregioncode` `+0xa223f`, `"[regioncode] get regioncode error!"` `+0xa4c42`) and is written back
via `recoveredcfgcmd` (name @ `+0x9c422`; message `set skyregioncode error! recoveredcfgcmd=%s` @ `+0xa22c3`) ⇒ it lives in the same kv store as `others` (§5.6).

---

## 7. Live checks required before any flash (I will run these; all read-only)

**Q1 — settle H_A vs H_B (the single most important check).** Compare raw chip bytes against the
already-captured dumps. Expected values:

```
hexdump -C -s 0x200000  -n 16 /dev/mtd0ro   # H_B: 99 99 99 99 44 44 44 44 55 55 55 55 aa aa aa aa   (kernel0 container magic)
                                            # H_A: ff ff ... (erased: param tags +0x0)
hexdump -C -s 0x600000  -n 16 /dev/mtd0ro   # H_A: 99 99 ... ; H_B: gzip/FIT mid-stream garbage (not the magic)
hexdump -C -s 0x2200000 -n 16 /dev/mtd0ro   # H_B: kernel1 image start (99 99 ... if populated) ; H_A: ff (kernel0 padding)
hexdump -C -s 0x4800000 -n 16 /dev/mtd0ro   # both: ff ff ff ff ... (first 1.25 MiB of param is erased)
hexdump -C -s 0x4940000 -n 32 /dev/mtd0ro   # H_B: 85 19 02 e0 44 00 00 00 1d fb f7 98 03 00 00 00
                                            #      a9 01 00 00 b6 81 00 00 00 00 00 00 00 00 00 00
                                            #      == device-dumps/sk-d840n_mtd2.bin @ +0x140000, byte for byte
hexdump -C -s 0x340000  -n 32 /dev/mtd0ro   # H_A: same 32 bytes if the listing order is real
```
Also `mtd_debug info /dev/mtd2` / `cat /sys/class/mtd/mtd2/offset` (if present in this 4.19 build)
and `dmesg | grep -iE 'partition|spi-nand|denali|spifc'` — the "dynamic partitions parse" lines may
print the offsets directly.

**Q2 — full DT proof of the kernel's table** (no `dtc` needed):
```
for n in /sys/firmware/devicetree/base/soc/spifc*/spi*/partitions/partition*/label; do echo "$n: $(cat $n)"; done
find /sys/firmware/devicetree/base -path '*partitions*' -name reg | sort | while read f; do echo "$f"; hexdump -C "$f"; done
```
(If the live `/chosen` or `partitions` node shows the vendor `part@256` labels, H_B is confirmed for
the kernel too.)

**Q3 — bootloader truth:** on TTL, `printenv` (compare 1:1 with §6.4), `mtd list`, `bdinfo`, and
`printenv mtdparts` **twice** (before/after a reboot) → answers whether `mtdparts` is regenerated
from the embedded vendor DT (§5.5).

**Q4 — will their `bootm` accept the shipped `uImage`?** in U-Boot:
`tftpboot 0x88000000 uImage; iminfo 0x88000000` → look for `Bad Header Checksum` (§6.6,
`uboot-an758x/boot/bootm.c:78`). If it aborts, the `ih_hcrc` must be recomputed (4-byte patch, data
untouched) before any flash attempt.

**Q5 — boot-slot bookkeeping semantics:** `cat /proc/cmdline` (expected `0x2200000 0x1`),
`cat /tagparam/flag_type`, `ls -l /tagparam /important_bak`, `md5sum /tagparam/paramtag`.
[FACT] the captured jffs2 holds `flag_type` versions `current : 324 / 306 / 310 / 0` — if
`current : 0` means "slot 0 = kernel0 is current" while bootargs say slot `0x1`, one of the two is
not what we think (§6.7, and docs/03 §7-3).

**Q6 — where is the plaintext board data?** the brief's `TAGH0201`/`D0458D`/SN strings are **not in
`mtd2.bin`** (§5.3). Dump and grep `others` (mtd7, 6 MiB, raw `0x4200000`) and `middleware` (mtd4,
raw `0x5400000`) for them, plus `dd if=/dev/mtd2ro | strings` on the *live* mount (`/tagparam`) to
see whether the kernel decrypts on read (it does not) or the file is obfuscated.

**Q7 — partition selection / controller status on a booted community unit** (if one is available, or
from the author's log): `mtd list` in U-Boot with their env, and on Linux `cat /proc/mtd` +
`dmesg | grep -i 'partition-names\|dynamic'` → does the corrupted `partition-names` (§5.8.1) or the
`status = "disabled"` flash nodes (§5.8.3) prevent an mtd/root at all?

**Q8 — `mem=1024M` handling:** on TTL with their image, `fdt print /chosen bootargs` after `bootm`
prep, or boot and read `/proc/cmdline` → does U-Boot overwrite `/chosen/bootargs` from env?

**Q9 — write protection:** whether the SPI-NAND block-protect bits cover `boot`/`kernel*`
(unverifiable without an erase; docs/03 keeps this open). [FACT] `boot.bin` ships a stage-1 that is
byte-identical to the stock one (§6.2), so a failed `boot` write is *not* mitigated by a different
stage-1 — the BTL/xmodem recovery path in docs/03 is the only fallback.

**Q10 — rollback assets:** capture `kernel0` + `kernel1` in full (32 MiB each; only 2 MiB of
`kernel0` exists today) plus `others`, `middleware`, `usercfg`, `wlan` (§4 phase 0). Until those
exist, §4 is not executable at all.

---

## 8. RESOLVED on the device 2026-10-09 (main agent, read-only — supersedes the H_A/H_B open question)

H_B is **confirmed by five independent sources**; H_A is refuted.

1. **[FACT] Live kernel DT** — `/proc/device-tree/part@256/partition@*/reg` (big-endian addr/size pairs),
   read directly off the running unit: `boot 0x00000000/0x00200000`, `kern0 0x00200000/0x02000000`,
   `kern1 0x02200000/0x02000000`, `others 0x04200000/0x00600000`, `param 0x04800000/0x00400000`,
   `middleware 0x05400000/0x00400000`, `osgi0 0x06000000/0x01e00000`, `osgi1 0x07e00000/0x01e00000`.
   Node names present: `whole boot kern0 kern1 others param usercfg middleware wlan phoneapp0
   phoneapp1 osgi0 osgi1 plugin_data awifi`.
2. **[FACT] Raw chip magic bytes** (`/dev/mtdblock0`, partition-relative seek): `0x200000` =
   `99 99 99 99 44 44 44 44 55 55 55 55 aa aa aa aa` (kernel0 container magic, H_A would be `0xFF`);
   `0x2200000` = the same magic (kernel1 is populated — the slot this unit boots);
   `0x4200000` = ASCII `DownloadState=0x…` (the `others` profile, §3);
   `0x4800000` = `FF…` (param, its only dirty block is at `+0x140000`);
   `0x600000` = mid-image garbage (refutes H_A, which put kernel0 there).
3. **[FACT] Byte-identity test**: `mtdblock2+0x140000` == `mtdblock0+0x4940000` ==
   `85 19 02 e0 44 00 00 00 1d fb f7 98 03 00 00 00` → `/dev/mtdblock0` is linear with physical
   offsets and `parameter tags` starts at `0x4800000`.
4. **[FACT] Saved U-Boot env** at `boot`+`0x180000`, 128 KiB, no redundant copy: header CRC
   `0xc638125c` recomputed with plain crc32 over `body = block[4..0x20000]` and **matched exactly**;
   15 variables, including the full `mtdparts` string quoted in §3, `bootcmd=zxboot`,
   `bootdelay=1`, `ipaddr=192.168.1.1`, `serverip=192.168.1.101`, and a placeholder
   `ethaddr=00:29:47:00:00:32` (the real MAC `D0:45:8D:84:08:FC` comes from `others`).
5. **[FACT] Kernel0 container self-description** (§2, unchanged).

Answers to the open questions in §7:
* **Q1 / Q2** — settled above; no TTL needed for the map.
* **Q5 (slot semantics)** — active slot is **kernel1**: live `bootargs` `… 0x2200000 0x1 0x83 0x83`,
  `others` profile `LastBooting=0x00000001,BootImageNum=0x00000001`, and `/proc/zxic/versionstates`
  = `version1states : 0x83`, `version2states : 0x83` (so the two `0x83` in bootargs are those state
  words, not slot flags). `/tagparam/flag_type` = `current : 0` therefore does **not** name the
  kernel slot — treat it as unrelated bookkeeping until proven otherwise.
* **Q6 (why plaintext is missing from `mtd2.bin`)** — `TAGH0201` is present when the file is read
  through the jffs2 mount (`/tagparam/paramtag` head = `54 41 47 48 30 32 30 31 …`) but absent from
  the raw partition dump: the node at `+0x140000` begins `85 19 02 e0`, i.e. a **compressed** jffs2
  node, so the board data is stored compressed, not encrypted. Nothing is decrypting on read.
* **RO devices exist** — `/dev/mtd0ro` (char 90:1) and `/dev/mtd7ro` (90:15) are present, so backups
  can use the `…ro` nodes as §4 Phase 0 assumes.
* **Q3 / Q4 / Q8 / Q9** still require TTL serial or a write test → remain open, consistent with the
  user's `先只看不刷` constraint.

Caveat on this document: §5.8 (defects inside the community `board.dtb`) was produced with
`devtools/fdt_dump.py` while its node-inheritance handling was still being corrected — the author
agent was cut off mid-fix. Re-verify §5.8's specific claims (notably the `partition-names`
corruption and the disabled flash nodes) before relying on them; §3/§3b/§8 do not depend on them.
