# 03 — Can the stock U-Boot of the SK-D840N be reached **without opening the case**?

Question: does any part of the vendor bootloader (password-gated `uboot menu`, `REC` XMODEM
recovery, upgrade machinery, `UpModeInfo` handling) have a *software-only* entry point on this
unit — no case opening, no TTL adapter, no JTAG.

Method: **local, read-only analysis only.** Nothing in this document came from the live device;
no connection to `192.168.1.1` was made, `C:\UGit\SSH` was not used, no file on the unit was
touched. Everything below is derived from the dumps already on disk.

Artefacts analysed (all offsets in this doc are **file offsets inside the dump**, which for
`sk-d840n_b1.bin` = absolute flash offsets, because mtd1 `boot` starts at `0x0`):

| file | what |
|---|---|
| `device-dumps/sk-d840n_b1.bin` | complete 2 MiB mtd1 `boot` (stage-1 `BTL` loader + U-Boot 2021.01-svn358069 + saved env) |
| `device-dumps/sk-d840n_k0.bin` | first 2 MiB of mtd3 `kernel0` (vendor image header + FIT) |
| `device-dumps/sk-d840n_mtd2.bin` | 4 MiB mtd2 `parameter tags` (jffs2) |
| `sk-d840n-openwrt/binary/{boot.bin,board.dtb,uImage}` | community replacement bootloader/DTB/kernel (same SDK lineage) |

Tooling written for this analysis (all offline helpers, in `devtools/`):
`strscan.py` (strings + file offsets), `mapimg.py` (magic scanner), `fdtprint.py` (a
flattened-DTB reader, because `dtc`/`fdtdump` are not installed), `armdis.py` (aarch64 linear
disassembly via `python-capstone` 5.0.x with skipdata), `xref.py` (ADRP+ADD cross-reference),
`blcallers.py` (`bl` caller finder), `adrphist.py`/`findbase.py` (address-map recovery),
`pwdb.py` (digest brute force), plus extracted artefacts `uboot-stock.dtb` /
`uboot-stock.dts.txt` / `k0-fit.dtb`.

Label convention used throughout: **[FACT]** = directly readable in a file on disk, **[INF]** =
inference from that evidence, **[DOC]** = from a cited external source, **[UNK]** = not
determined.

---

## 0. Bottom line first

1. The `uboot menu` is **not a hotkey menu**. It is a *safe-boot* maintenance menu inside
   U-Boot, gated at boot time by **an efuse bit**, and protected by a password that is stored as
   a key/value entry (`.sfmenupsd` / `.ubmenupsd`) in U-Boot's "private profile" store, which
   lives in the **`others` MTD partition** — not in the U-Boot binary, not derived from
   MAC/serial in any way we could prove. **[FACT]** for the storage mechanism, **[UNK]** for the
   actual password value.
2. `REC: pls x-Modem send uboot.bin.` is **not U-Boot** — it is the **stage-1 `BTL#` monitor**
   in the first ~40 KiB of the `boot` partition, entered from a *boot-failure* routine keyed on
   the CRU register **`0x10e10170 & 3`**. It only speaks UART. **[FACT]**
3. There **is** a Linux-side route into U-Boot behaviour, and it is not the menu: the **saved
   U-Boot environment sits at mtd1 `+0x180000`, 128 KiB, plain CRC32** — a block we can rewrite
   from Linux, and which is where `bootcmd` / `bootdelay` / `bootargs` come from. This is the
   only *headless* control channel found. **[FACT]** for the layout and the CRC, **[INF]** for
   "U-Boot actually loads it at every boot" (strong but not conclusive from the dump alone —
   §5.3 gives the check that settles it).
4. `neglect_bootargs_up_mode_info` + `UpModeInfo` is a **red herring as a security mechanism**:
   `UpModeInfo` is the **PON/ports mode word** (GPON vs XGPON vs EPON, which LAN/WLAN port is
   the uplink), read from U-Boot's own device tree and a flash profile, and
   `neglect_bootargs_up_mode_info` is a **build-time DT property = 0 on this board** (also 0 in
   the community DTB). It cannot be flipped from Linux without replacing a DTB/U-Boot. **[FACT]**
5. Dead ends for the no-disassembly goal: the safe-boot menu (needs UART + unknown password),
   the `REC`/XMODEM monitor (UART only), `swsfmd`/`up_mode`/`mcupg`/`loadx` (U-Boot console ⇒
   UART), JTAG (efuse-lockable and anyway needs disassembly), and "Checkerboard" (it is a
   `memtester` pattern name, not a flash checker). **[FACT]**

Ranked answer to "can any of it be reached without opening the case": **the menu and the recovery
monitor cannot — they are serial-only.** The *behaviour* of the bootloader can be steered from
Linux through the environment block (§5), and the vendor's own A/B slot logic gives you a
reversible target (§6, path 3). That is the only realistic no-disassembly route, and it is
still a *flash* route, not a *menu* route.

---

## 1. What the `boot` partition actually contains

`[FACT]` Structure of `device-dumps/sk-d840n_b1.bin` (2 MiB):

| offset | content | evidence |
|---|---|---|
| `0x0000` | stage-1 image header: `00 02 00 14`, board tag **`133s`** at `0x10`, `0001ZXIC..` `BOOTROM` `RSA2048` at `0x80`/`0x90`/`0xa0`, u32 `4` at `0xb4`, u32 `0x9480` at `0xb8` | hexdump |
| `0x0000–0x20000` | **stage-1 `BTL` loader/monitor** (its strings are the block at `0x97xx–0x9bxx`) | §3 |
| `0x20000` | U-Boot proper entry (stage-1 does `mov x0,#0x88000000; blr x0`) — see address map note below | disasm `0x1868`, `0x4cd4`, `0x4d08` |
| `0x08e9d0` | **compiled-in default environment**: `bootcmd=zxboot`, `bootdelay=1`, `baudrate=115200`, `ipaddr=192.168.1.1`, `serverip=192.168.1.101` (and nothing else — no `bootargs`, no `mtdparts`, no `ethaddr`) | strings |
| `0x0a224d–0x0a5bff` | vendor text: safe-boot menu, efuse/AES commands, `up_mode`, `zxvid`, `mcupg`, `sky_bakeup_info`, `zxboot`, `Checkerboard`… | §2, §4 |
| `0x0b15bc`, `0x0b15cc`, `0x0b1630` | three 16-byte binary constants used by the password comparison (see §2.4) | hexdump + disasm |
| `0x0c5330` | **gzip stream** (`1f 8b 08 08`, FNAME `u-boot.dtb`, mtime `0x692d8492` = 2025-11-27) containing **a FIT wrapper + the U-Boot control DTB**; inflated = 50 012 B, two FDTs concatenated (704 B FIT + 49 308 B DTB) | `devtools/uboot-stock.dtb`, `.dts.txt` |
| `0x0c7f00` | U-Boot image trailer/header: `UUUUDDDD`, `2.0.0`, `2025-12-01 20:09:27`, u32 `0x0a7f40`; at `0x0c7fc0` the vendor magic `9999 9999 44 44 44 44 55 55 55 55 aa aa aa aa` + `V2.0.0` | hexdump |
| **`0x180000`** | **saved U-Boot environment**, 128 KiB (zero-padded to the erase-block boundary at `0x1a0000`, `0xFF` from there) | §5 |

**Address map [FACT]:** `.data` pointers inside the dump are `VA = file_offset + 0x87FE0000`
(e.g. the safe-menu help pointer `0x880843b4` → file `0x0a43b4` = `"set aes key"`). So U-Boot is
linked for `0x88000000` (= `0x20000 + 0x87FE0000`) and the XMODEM target `0x87FE0000` is the
partition's link base. This is why the README's `tftpboot 0x88000000` works and why the stage-1
help text reads `x [addr]` / `Boot at 0x88000000`.

**Partition map.** This dump adds a third independent confirmation of hypothesis **H_B** in
[doc 02 §3](02-no-disassembly-reflash-plan.md): U-Boot's own DTB carries two literal partition
tables, `part@256` and `part@128` (`[FACT]`, `devtools/uboot-stock.dts.txt`), and `part@256`
matches the env `mtdparts` byte for byte:

```
part@256  boot 0x0/0x200000 · kern0 0x200000/0x2000000 · kern1 0x2200000/0x2000000
          others 0x4200000/0x600000 · param 0x4800000/0x400000 · usercfg 0x4c00000/0x800000
          middleware 0x5400000 · wlan 0x5800000 · phoneapp0 0x5c00000 · osgi0 0x6000000
          osgi1 0x7e00000 · plugin_data 0x9c00000 · phoneapp1 0x5e00000 · awifi 0xfc00000
part@128  boot 0x0/0x200000 · kern0 0x2000000/0x3200000 · kern1 0x3400000/0x3200000
          usercfg 0x6600000 · others 0x7000000 · wlan 0x7100000 · GN25L95_data 0x7c00000
          param 0x7500000/0x300000
```

and the mtd3 image we dumped (`sk-d840n_k0.bin`) embeds its own slot table in the vendor header
at `0x60…0x7f` (LE u32): `0x00200000, 0x02000000, 0x02200000, 0, 0x02200000, 0x02000000,
0x04200000, 0` → (kernel0 off,size) and (kernel1 off,size) = **H_B**. `[FACT]`
So U-Boot's `nand`/`mtd` absolute addresses follow H_B, while `/proc/mtd` lists partitions in
*DT order* (which is how H_A was mis-derived). `part@128` is selected by the flash size; this
unit is 256 MiB → `part@256`. `[INF]`.

---

## 2. The `uboot menu` — what it is, what triggers it, where the password lives

### 2.1 It is a "safe boot" menu implemented as its own mini-CLI

`[FACT]` strings + disassembly (all in the U-Boot proper part of the dump):

| offset | string | meaning |
|---|---|---|
| `0x0a3fba` | `Safe mode menu as follow:` | menu banner, printed at `0x62414` |
| `0x0a3fd5` | `[Swsfmd]: ` | the prompt, printed at `0x624c4` |
| `0x0a3b2f`/`0x0a3b66`/`0x0a3f4d`/`0x0a3f85` | `Duplicate sws command registered…`, `Could not allocate new sws_command table`, `sws_parse: command buffer overflow`, `sws_parse: too many arguments` | an **own command parser** (`sws_*`), independent of the U-Boot CLI |
| `0x0a3b90` | `Print this help list` · `0x0a3bbd` `Perform reset of the cpu` | menu entries |

Menu/commands found in the block `0x0a3bfb…0x0a4468` (name → help):
`unlock` → *"Download unlock file by x-Modem and try to unlock debug mode"*;
`bkernel` → *"Start to boot kernel which in flash"*;
`umenu` → *"Return to the original uboot menu"*;
`efuseinfo` → *"Displays the details of the efuse register"*;
`saeskey`, `sbaeskey`, `awprotect`, `arprotect`, `bwprotect`, `brprotect`, `taeskey`,
`ubaeskey`, `sjprotect` (*Set cpu jtag protect*), `senable` (*Set safety boot enable*),
`sprotect`, `ssfpassword` (*Set safety boot menu password*), `tipsec` (*Test ipsec
performance*); and, as ordinary **U-Boot CLI commands**, `submpasswd` (*"Set uboot menu password
for safe boot mode"*, `0x0a4355`) and `swsfmd` (*"Manually switch to safe boot mode"*, `0x0a438b`).

The menu loop is at **file `0x6240c`** (banner → line input → `sws_parse` at `0x62084` → repeat),
and the gate in front of it is at **`0x62508`**:

```
0x62508  bl 0x64140          ; condition
0x62514  cbz w0, 0x62548     ; condition false -> "original uboot menu" path
0x62518  mov w0,#0 ; bl 0x61670      ; ask password (w0=0 selects .sfmenupsd)
0x62520  cbz w0, 0x6252c     ; ok -> 0x61c34 ; enter menu 0x6240c
0x62524  bl 0x29060 ; b 0x62528   ; wrong -> reset/hang loop
```

### 2.2 What triggers it: an **efuse bit**, plus a chip-ID bypass — not a hotkey

`[FACT]` the condition function `0x64140` is an efuse read:

```
0x64148 bl 0x5bbc4            ; chip id
0x6414c sub w0,w0,#0x44,lsl #12 ; -#0x25b   -> compare with 0x4425B (279131)
0x64160 mov w1,#0x425d ; movk w1,#4,lsl 16  -> 0x4425D (279133)
0x64180 mov w1,#0x25de ; movk w1,#0x44,lsl 16-> 0x4425DE
0x64170 x1 = 0x14f11080   (279133 path)  /  0x64190 x1 = 0x14f11040 (other)
0x6419c ldr w0,[x1] ; tbz w0,#0 -> poll bit0
0x641a4 x0 = 0x14f1101c ; ldr w1,[x0] ; and w1,w1,#1 ; return w1
```

`0x14f11000` is a real node in the same DTB — `efuse@14f11000 { compatible =
"zte,efuse","zxic,zx-efuse","syscon"; reg = <0x14f11000 0x1000>; }` (`[FACT]`
`devtools/uboot-stock.dts.txt` L1337), and the DTB also declares
`sec_module@0x08f00000 { compatible = "zxic,zx-sec"; }` (L1343) — the AES/HASH/RSA engine that
the stage-1 code pokes at `0x08f00350`/`0x08f00370` and that the `tipsec`/`AES timeout`/
`RSA timeout`/`HASH timeout` strings belong to. So **the choice between "safe menu + password"
and "original uboot menu" is decided by bit 0 of efuse register `0x14f1101c`** after poking the
fuse-bank read trigger at `0x14f11040`/`0x14f11080`.
The rest of the same routine family names that fuse (`senable`/`sprotect`/`sjprotect`,
`Safety boot enable/disable`, `Cpu jtag portected`, `SEC_BOOT_PROTECT`, `AES_KEY0..3`,
`Backup_AES_KEY0..3`, `Key_vld`, strings at `0x0a25cb–0x0a29c9`). `[FACT]`

Its **caller** is reached from the boot flow at `0x29084` (which reads `bootcmd`/`bootdelay`
strings at `0x96d08`/`0x96df2` and contains `Hit any key to stop autoboot: %2d` nearby at
`0x96e2c`), i.e. it runs **automatically every boot**:

```
0x290c4 bl 0x5bbc4 ; cmp with 0x4425B / 0x27912700 / 0x27912800 ; b.eq -> skip the gate
0x29100 bl 0x62508        ; <- ZX279133 (0x4425D) reaches here
0x29104 bl 0x322a8        ; autoboot/abort
```

`[INF]` Consequences:
* On **ZX279133** the gate always executes at boot. There is no env variable, no boot flag and
  no Linux-visible knob that turns the menu on; only the fuse (or replacing U-Boot) changes it.
* **The menu is not the console lock.** The right-hand branch (`0x62548 → 0x61d60`) is the
  "original uboot menu" path, and the community flow ([README], `[DOC]` right.com.cn
  thread-8412569: TTL reaches a `=>` prompt where `help` lists `submpasswd` and `swsfmd`)
  proves the normal U-Boot CLI is **not** password-gated on these units. The gate only decides
  whether you additionally get the *safe-boot maintenance menu*.
* Because both the community `boot.bin` **and** the stock build carry the identical
  `.sfmenupsd`/`.ubmenupsd`/`swsfmd`/`up_mode` code (`[FACT]` same strings at
  `sk-d840n-openwrt/binary/boot.bin` offsets `0x0a370d`, `0x0a3718`, `0x0a4303`, `0x0a42d8`),
  these are **SDK defaults of the Sanechips fork, not a Skyworth/CMCC hardening step**. Flashing
  the community bootloader would not remove them, and would not add anything.

### 2.3 Second password: the `uboot menu` password proper

`[FACT]` the same verification function handles two different passwords, selected by an
argument (`w22`):

| prompt | storage key | used by |
|---|---|---|
| `Input safe menu password : ` (`0x0a3acc`) | `.sfmenupsd` (`0x0a3795`) | `ssfpassword`, gate `0x62518` |
| `Input uboot menu password : ` (`0x0a3ae9`) | `.ubmenupsd` (`0x0a37a0`) | `submpasswd`, `umenu` |

Rules printed by the setter (`0x0a347a–0x0a375e`): a banner box
`S A F E B O O T   P A S S W O R D   N O T I C E` / `U B O O T   P A S S W O R D   N O T I C E`,
*"Use letters, Numbers, and special symbols"*, *"Password maxnumber 16 bytes"*, *"Input timeout:
no timeout"*, *"Max input retry: %d times"* (the loop compares a counter with **6**,
`cmp w25, #6` at `0x618d4`), `Retry too much times, booting with no safe menu!!` /
`…return to safe menu!!`.

### 2.4 Where the password is *stored* — the private profile in the `others` partition

`[FACT]` This is the important structural discovery. U-Boot has a key/value store implemented by
`zx_set_private_profile_uint` / `zx_get_private_profile_uint` /
`zx_set_private_profile_string` / `zx_get_private_profile_string` (symbol strings at
`0x08de90–0x08def0`) whose backing store is **an MTD partition named `others`**, addressed as a
`name=value,` text blob:

```
0x09c108 "%s=0x%08x,"            0x09c16f "others"
0x09c176 "[%s] read failed! iret=%d mtdpart=%s mtd_offset=0x%x mtd_length=0x%x"
0x09c1bd "[%s] erase failed! …"  0x09c205 "[%s] write other failed! …"
0x09c2af "[%s] mtdpart uninit. mtdpart=%s mtd_offset=0x%x mtd_length=%d"
0x09c3a7 "%s=%s,"                0x09c292 "[%s] pKeyName=%s not found!"
```
("others" is referenced from `0x49194, 0x493bc, 0x49654, 0x49a00, 0x62590, 0x627e0, 0x6b280,
0x6b8cc` — i.e. from the profile helpers, the boot-slot code and the upgrade code.)

Keys observed being read/written through it (`[FACT]`, string + call sites):

| key | purpose (from surrounding strings) |
|---|---|
| `BootImageNum` (default `0x00000001`) | which `kernel%d` slot to boot (`0x0a3ff4`, `0x0a402a`) |
| `MlUpCloseState` (default `0x00000001`) | multicast-upgrade close state (`0x0a404b`) |
| `up_mode` / `UpModeInfo` | PON mode word, `up_mode get/set` (`0x0a4df9`, `0x0a4e32`) |
| `regioncode` / `skyregioncode` | region (`0x0a4c65`, `0x0a224d`) |
| `factoryrestore` | *"factoryrestore is %d, reset upmode"* (`0x0a4c89`) |
| `recoveredcfgcmd` | config-recovery command line run by U-Boot (`0x09c422`, `0x0a22c3`) |
| `.sfmenupsd`, `.ubmenupsd` | the two menu passwords |
| VID | `zxvid` writes `ZXICVID` into a `zxicconfig` area (`0x0a59fd–0x0a5a69`) |

`[FACT]` The password is **not stored in clear**: the check compares a **16-byte** value
(`mov x2,#0x10` at `0x61844`/`0x61850`/`0x61874`), and if the profile entry's text length is
**32** it goes through a converter (`bl 0x5fd08`, `0x61874`) — i.e. **16-byte binary digest,
hex-ASCII in flash** (MD5 shape; 32 hex chars ↔ 16 bytes). `bl 0x28d88` at `0x61830` does the
hex→bin conversion of the stored value.

`[FACT]` When the profile entry is **missing/empty**, the code does **not** skip authentication;
it compares against a **compiled-in constant**:

| file offset | 16 bytes | role (from `0x617e4`–`0x61874`) |
|---|---|---|
| `0x000b15bc` | `c2 33 e6 70 3d fd bf 61 a5 34 c5 21 ba b2 53 79` | fallback operand when the profile value is empty |
| `0x000b15cc` | `64 db 3e d1 81 ea c1 29 f2 76 e3 a4 b6 a5 fe ed` | operand paired with `.sfmenupsd` |
| `0x000b1630` | `eb 1a 65 5f 5c a9 74 6e a6 f4 96 e1 f0 ec 5d fd` | operand paired with `.ubmenupsd` |

Note `0x0b15bc…0x0b15db` is a contiguous **32-byte** value (`c233e670…b6a5fe ed`), so it may be
one SHA-256 or two MD5s; the 16-byte halves are the ones actually passed as compare operands.

`[UNK]` Whether these are digests of a vendor default password or AES key material.
`devtools/pwdb.py` tested ~120 candidate words (`admin`, `root`, `uboot`, `debug`, `skyworth`,
`zte`, `cmcc`, `telecomadmin`, the model/serial/MAC strings `SK-D840N`, `231993CA7XP9CAT742CCT`,
`SKYWA9B132D3`, `D0458D…`, `00:29:47:00:00:32`, `133s`, the build date `20251201200927`, …)
against MD5/SHA-1/SHA-256 (hex, upper, first-16, utf-8 and utf-16le): **no match**. SHA-256 over
the concatenated 32-byte value was *not* effectively covered (the candidate list only held
32-hex strings) — that is a cheap offline follow-up (§8). `[FACT]` the negative result.

**So, answer to Q1:** the menu is triggered by an efuse bit at every boot on this chip and is
reachable only over the serial console; there is **no Linux-side / env-side route into the menu
itself**. The password is a 16-byte digest stored as `.ubmenupsd`/`.sfmenupsd` in the `others`
partition's key/value profile, with compiled-in defaults in the bootloader; nothing indicates a
MAC/serial derivation (contrast: `[DOC]` the SK-D841L thread reports the *new* u-boot/TTL password
as unsolved too).

---

## 3. The `REC` XMODEM recovery — where it lives and what enters it

`[FACT]` `REC: pls x-Modem send uboot.bin. Save addr:` is at file **`0x00994a`**, inside the
**stage-1** string block (`0x0097xx–0x009bxx`), i.e. in the `BTL` monitor, **before** U-Boot
(whose text starts at `0x20000`). Neighbours: `enter bootloader...` `0x9899`,
`crpm init ok` `0x98af`, `ddr init ok` `0x98be`, `bootloader err!!!` `0x98cb`,
`Trigger debug mode !` `0x98de`, `ZXICVID` `0x98f4`, `x 0x87fe0000` `0x98ff`,
`xmodem receive at 0x87fe0000` `0x990d`, `Boot at 0x88000000` `0x992b`, `BTL# ` `0x993f`,
monitor help: `Boot at ` `0x9983`, `x [addr]` `0x998c`, `b <n|o|s|u>` `0x9995`,
`r addr [size]` `0x99a1`, `w addr data` `0x99af`, `g addr` `0x99bb`, and
`HASH timeout`/`RSA timeout`/`AES timeout` `0x99d1–0x99ec`, `uE3…uE15` error codes
`0x9a03–0x9a44`, `verify OK!` `0x9a4a`, `secure uboot ` `0x9a56`,
`chip id err! id =0x%x should be 0x%x` `0x9a65`, `backup header!!` `0x9a8b`, `Jump` `0x9a9c`,
`not enter wps key` `0x9b0c`.

So stage-1 is a **self-contained loader + monitor**: it can xmodem-receive a binary at
`0x87FE0000`, read/write/`go` arbitrary addresses, and boot from nand/other/source/uboot
(`b <n|o|s|u>`). U-Boot proper *also* has `loadx` (`## Ready for binary (xmodem) download to
0x%08lX at %d bps…`, `0x094b04`) — a second, independent XMODEM.

### 3.1 Who enters the debug monitor

`[FACT]` the routine at file `0x1858`:

```
0x1858 mov x0,#0x170 ; movk x0,#0x10e1,lsl 16   ; x0 = 0x10E10170
0x1868 ldr w0,[x0] ; and w0,w0,#3               ; boot-mode bits, mask 3
0x1870 cmp w0,#1 / cbz w0 / cmp w0,#3           ; dispatch 0,1,2,3
        0 -> bl 0x2ea0
        1 -> bl 0x2aec ; bl 0x242c
        3 -> bl 0x2aec ; bl 0x1f54
        2 -> (nothing)
0x18a0 puts("bootloader err!!!") ; puts("Trigger debug mode !") ; bl 0x2ea0 ; return 0
```
and `0x1858` itself is called from the stage-1 start path at `0x0894`, right after `bl 0x17cc`
(= `enter bootloader... / crpm init ok / DDR-config retry loop / ddr init ok`) and after a
CPU-index table lookup (`0x08a8`, `mpidr_el1`) that hangs non-matching cores at `0x8a4`.

**The DTB tells us what that register is:** every board node
(`bdinfo@0`, `tdy0a_4@0`, … `compatible = "zxic,zx279133-board","zxic,zx-board"`) carries
`boot-mode = <0x10e10170 0x3>;` `[FACT]` — the *same* address and *same* mask the code uses.
`0x10e10000` is the Sanechips **clock/reset (CRU) block** in this DTB
(`zxic,zx-pll` at `0x10e10080`, `zxic,zx-mux` at `0x10e10000…0x10e10010`). `[INF]` So `REC` is
selected by a **reset/boot-mode register in the CRU** (a warm-reset-surviving scratch/mode
register), read *and* acted on by stage-1 before U-Boot is ever reached, and the monitor is
entered when the boot attempt for the selected mode fails (`bootloader err!!!`).

Other hypotheses from the task brief, checked:

| hypothesis | verdict |
|---|---|
| failed image CRC / header check | **[INF] plausible but not proven for stage-1.** U-Boot has `header crc failed` (`0x0a4328`), `crc:%x, image crc:%x` (`0x0a1fa1`) and `search kernel%d image failed` / `[WARN] kernel%d is not valid, switch to kernel%d` (`0x0a40eb–0x0a4137`), i.e. **A/B fallback happens in U-Boot**, not stage-1. `[DOC]` on the sibling ZX279127S/28S the CRC/hash check lives in the bootloader and prints `vmlinuz crc error` / `code hash error !!`. |
| "Checkerboard" as a flash checker | **DEAD — [FACT]** `Checkerboard` at `0x0a537a` is surrounded by `Random Value`, `Compare XOR/SUB/MUL/DIV/OR/AND`, `Sequential Increment`, `Solid Bits`, `Block Sequential`, `Bit Spread`, `Bit Flip`, `Walking Ones`, `Walking Zeroes` and the `memtester` command (`0x0a5260`). It is a **memory-test pattern name**. |
| a GPIO/button | **[FACT] the WPS GPIO is consulted by stage-1**: `not enter wps key` `0x9b0c`, referenced from `0x9728` (function tail; the same function pokes GPIO controller registers `0x10d10000`/`0x10d1001c`/`0x10d10080` and the ISI block `0x14f00901c/0a01c/0b01c/0c01c` with `0x12340000`, then spins). The DTB `keys` node gives `keys@wps` = gpio-chip `0x50` offset `7`, `keys@restore` = chip `0x52` offset `0`, `keys@wlan` = `0x50` offset `6`, with `key_map_*` codes (`0x0a404` wps etc.) — `[INF]` a **button-driven** entry to the monitor exists (hold WPS across reset), but the exact polarity/hold time is not resolved from static code. |
| watchdog-forced reset distinguishing wdt vs power-on | **[INF] likely, via the same `0x10e10170` field.** The four `zx_wdt` hearts beat and the CRU holds the reset/mode bits, so a wdt reset *can* leave a different value in those bits than a power-on. `0x290c4`-style chip-ID dispatch and `Disable watchdog.` (`0x09c432`) / `reset val =0x%x` (`0x0a14be` area) show watchdog handling in U-Boot. **Not proven** which bit pattern means "wdt reset". |
| `bootdelay`/abort on serial | **[FACT]** `Hit any key to stop autoboot: %2d` (`0x096e2c`) and env `bootdelay=1` exist; `[DOC]` the community presses `u` at power-on. This is a **U-Boot-level** abort, unrelated to `REC`, and needs UART. |
| an env flag / magic value at a fixed flash offset | **[FACT] no env/flash magic for stage-1**: stage-1 (0x0–0x20000) contains no reference to the env block at `0x180000`, and no `REC`-related string mentions an env variable. The only inputs it demonstrably uses are the CRU register and its own image headers. |

### 3.2 Can `REC` be *caused* from Linux? (and does that help?)

`[FACT]` Yes, mechanically: the community repo ships `cmd/devmem2/devmem2.c` for this exact
board, so `/dev/mem` access is expected to work on the vendor kernel; `0x10e10170` is a plain MMIO
register and its low 2 bits are what stage-1 reads. A `devmem2 0x10e10170 w <value>` followed by a
reboot would drive that dispatch, and `up_mode`/`factoryrestore`/`BootImageNum` are writable by
U-Boot itself through the `others` profile (and raw-writable from Linux with `nandwrite`).

`[INF]` **But it buys nothing without a UART.** Every action of the `BTL#` monitor — `x [addr]`
(XMODEM), `r`/`w`, `b`, `g` — is **serial I/O at 115200**. There is no network, no USB, no
`eth` boot in stage-1 (its only strings are UART/XMODEM/nand). So causing `REC` from Linux turns
a working ONU into a device waiting for a programmer on a header you have not soldered. It is an
**anti-goal**: it converts "no disassembly" into "bricked, must disassemble". The one place it
*is* useful is as the **salvage path after TTL exists** (it can reload `uboot.bin` even when the
`boot` partition is trashed — that is what it is *for*).

---

## 4. The vendor image-upgrade machinery as a substitute for JTAG/TTL

### 4.1 What U-Boot itself can flash

`[FACT]` U-Boot commands and helpers found in the dump:

| thing | offset | notes |
|---|---|---|
| `zxboot` (*"boot zx soc images"*, `zxboot mode <safemode>`, `- options for zxboot command`) | `0x0a433b`, `0x0b1568–0x0b15b1` | the whole boot path: `BootImageNum` → `kernel%d` → `find kernel%d flash entry is 0x%x` → header CRC → `bootm 0x%x%s` with `-F #conf@133`-style config selection (`#conf@131/132/133/133E` at `0x0a42a4–0x0a42c2`) |
| `upgrade` / `downver <name>` / `upgrade_all` (*"upgrade images for chip"*) | `0x0b1540`, `0x0b1553`, `0x08df30` | file names it accepts: `upgrade.bin`, `uboot.bin`, `vmlinuz.bin`, `rootfs.img`, `osgi.img`, `phapp.img`, `osgi`, `phoneapp`, `rootfs` (`0x0a2353–0x0a23c9`); `sky_check_image_only_upgrade_boot not include uboot!!!!` (`0x0a2034`); erase of `GN25L95_datas` (`0x096289`); `step1: nand erase %s / step2: nand write %s / step3: nand read %s` (`0x0964e9–0x096515`) |
| `mcupg` (*multicast upgrade*, Skyworth) | `0x0a5639`, `0x0a53bf–0x0a55cc` | receives a file **over Ethernet** (`192.168.1.1`, `Receive multicast packet failed`, `Send mutil upgrade success packet`, `led_pon`/`led_los` GPIOs, `Wait 5 seconds...`, `Skyworth multiupgrade success/failed`, `please used right file[1.include uboot or 2.version matched], or call wuzhiming to fixed this BUG`); `multi_handle_downloaded_image` (`0x08de38`) |
| `sky_bakeup_info` | `0x0a5ba4` | literal scripts `nand erase f000000 400000`, `nand read %x ad00000 300000`, `nand write %x f000000 300000`… — **hard-coded for another flash map**, do not reuse |
| `zxvid get|set|getall|getinddr` | `0x0a5a87` | writes `ZXICVID` into a `zxicconfig` partition |
| `memtester`, `nand`, `mtd read/write/erase[.spread]/dump`, `env default/delete/edit/print/run/save/set`, `setenv`/`printenv`/`saveenv`, `tftpboot`, `ping`, `loadx/loady/loadb`, `loop`, `erase` | `0x0a5260`, `0x0aa364–0x0aab89`, `0x0aabC0–0x0aad33`, `0x096801–0x096ccf`, `0x094ab9–0x094c96` | **no `dhcp`, no `ifconfig`, no `httpd`/`telnet`/`wget`/`curl`/`dfu`/`webrecovery`** (those
strings are absent from the whole 2 MiB dump; `DHCP`/`bootfile`/`rootpath`/`netmask`/`gatewayip`
appear only as env-variable *names* at `0x0967be`/`0x0a7c8b`, not as a command) — networking is
TFTP-**client** only, using static `ipaddr=192.168.1.1` / `serverip=192.168.1.101` / `ethaddr` |

**Meaning:** U-Boot can write `uboot.bin`, `vmlinuz.bin`, `rootfs.img`, `osgi.img`, `phapp.img`
from **TFTP or a multicast packet**, i.e. once you can *drive* U-Boot, re-flashing is a solved
problem and JTAG is unnecessary. The blocker is only *how to drive it* without a console. See
§5.

### 4.2 Are images required to be signed / AES-encrypted?

Evidence in the dump, strongest first:

1. `[FACT]` **The boot chain is designed for signatures but the enforcement is fuse-driven.**
   The stage-1 header literally says `BOOTROM` / `RSA2048`; stage-1 has `verify OK!`,
   `secure uboot`, `chip id err! id=0x%x should be 0x%x`, `HASH timeout`, `RSA timeout`,
   `AES timeout`. U-Boot has the ZTE RSA path (`ksq_e`, `use_ztersakey 0`, `puk rsa p is %p`,
   `boot_inf->signature[0] 0x%x`, `code_hash_temp_val_1/2`, `verify_run`,
   `read partition %s`, `mtd read %s 0x%lx 0x%x 0x%x`) plus
   `non secure boot` (`0x0a45ce`), `Failed to detect code hash !!` /
   `Successful detection code hash !!`, `verify ok !!!`, and it appends **`skysec=1`** to the
   kernel command line (`0x0a428d`).
2. `[FACT]` **AES keys live in efuse, not in flash**: `AES_KEY0..3`, `Backup_AES_KEY0..3`,
   `AES KEY VAILD`, `Key_vld`, `SEC_BOOT_PROTECT`, `ATE_*`/`ATE_IP_*` fuse dump (`0x0a264e–0x0a29c9`),
   commands `saeskey`/`sbaeskey` (*"Set safety boot (backup) aes secret key in efuse"*),
   read/write-protect commands, `Only aes 128 CBC mode is supported!!!`,
   `The security aes secret key need to upgrade first!!!`.
   The strings quoted in the brief — `Backup aes key for which image ? (uboot/kernel)`,
   `Please input "debug_uboot.bin" for check aes key:` — belong to `taeskey`/check flows whose
   purpose is **verifying that the fuse keys decrypt a known-good debug file**; they are
   factory-programming aids, not boot-time checks. `[INF]`
3. `[FACT]` The **production state of this unit is unknown from the dump**, but two strong
   indications say the security path is *off*:
   * the boot-flow gate reads a **fuse** to decide safe-menu vs normal-menu, and the normal path
     (which the community demonstrably reaches) proceeds to a plain `=>` CLI — a build with
     `Safety boot enable` fused would not normally leave the CLI open. `[INF]`
   * `zx_get_private_profile_uint`/`regioncode`/`up_mode`/`BootImageNum` all live in **plain
     ASCII `key=value,` text** in `others` — a device with security fused on would not keep its
     boot-slot selector in an unsigned text blob. `[INF]`
   * `[DOC]` For the sibling ZX279127S/ZX279128S, the community reports the integrity check
     *is* enforced and prints `vmlinuz crc error` / `code hash error !!`, and that *"只要自己算出来
     再patch不就ok了吗"* — i.e. the protection is a **recomputable CRC/hash seal, not a
     signature you cannot reproduce** (no private key needed for CRC32; RSA would be needed only
     if hash+RSA were enforced *and* the public key were fused).
4. `[FACT]` The **image formats are documented by our own dumps**, so a self-built image can be
   made to match:
   * kernel slot image = vendor header (512 B: magic `99999999 44444444 55555555 aaaaaaaa`,
     `V2.0.0` at `+0x24`, image name `ZXIC 133 UNI V2.0.0 286632 CM OSGI` at `+0x80`, size
     `0x0129a1c0`/FIT size `0x01299fdf` at `+0x44/+0x48`, FIT offset `0x1e0`, the slot
     (off,size) table at `+0x60`, a checksum-ish u32 `0x92efdacb` at `+0x50`) **+ a FIT**
     (`d00dfeed` at `+0x1e0`, configs `#conf@131/132/133/133E` selected by chip/board id).
   * Our 2 MiB slice is too short to contain the FIT **strings block** (`off_strings` =
     `0x1299b80`), so **whether the FIT configs carry `signature`/`hash` sub-nodes is [UNK]** —
     that needs one read-only dump of `kernel0`+`0x129 9b80` (see §7). The U-Boot strings
     `key-name-hint`, `sha1`, `sha256`, `rsa,sha256`-style FIT verification are present, so if
     such nodes exist U-Boot *can* enforce them.

**Answer to "is the upgrade machinery a viable substitute for JTAG/TTL":** the *flashing* part
yes (TFTP/multicast/`upgrade.bin`/`nand write` from U-Boot, or `nandwrite` from Linux), the
*trust* part unresolved: a self-built image must reproduce the vendor header **CRC**
(recomputable) and possibly a FIT hash (recomputable) — but *not* an RSA signature, unless the
fuse is on, in which case nothing self-built ever boots and this whole line dies.

### 4.3 `neglect_bootargs_up_mode_info` + `UpModeInfo` — the promised lead, explained

`[FACT]` what the code does (function strings `up_mode_set`/`up_mode_get`/`up_mode_check_verify`/
`default_up_mode_get`/`get_dts_neglect_bootargs_up_mode_info`/
`get_dts_bosa_and_switch_ponmode_info`/`modify_bootargs_UpModeInfo`/`zx_pon_mode_auto_select`/
`do_up_mode`, all at `0x08d8d8–0x08d9e0`, invoked from `zxboot`):

* `UpModeInfo` is a **bit field describing the uplink/ONU mode**, documented by the CLI usage
  string at `0x0a4e32`:
  `0x1:lan_up 0x4:epon 0x8:gpon 0x10:xe 0x20:xes 0x40:xg 0x80:xgs 0x200:wlan_up 0x400:4G/5G_up`,
  plus `ponmode_auto_en`, `lan_up_port`, `wlan_up_port`.
  Our cmdline `UpModeInfo=0x00000040` = **xg (XG-PON)**, and the console command to change it is
  `up_mode set [upmode] [ponmode_auto_en] [lan_up_port] [wlan_up_port]`.
* Its authoritative default comes from **U-Boot's own DTB**, node `pon_plat`:
  `neglect_bootargs_up_mode_info = <0>`, `kernel_default_up_mode_info = <0x40>`,
  `uboot_default_up_mode_info = <0x40>`, `bosa_and_switch_ponmode_info = <0x05200040>`,
  `hff_chip_cap`, `mdio_8226_id`. The **community `board.dtb` has the identical values**
  (`neglect_bootargs_up_mode_info = <0>`). `[FACT]`
* Runtime flow: `up_mode_get()` reads the profile; if it fails → *"use default config"*; the DT
  default is used; if `auto_en` is 0 or the port is unconnected → force gpon/xgpon Bosa;
  `up_mode_check_verify()` compares and on mismatch → *"modify config"*; `factoryrestore` set →
  *"reset upmode"*; `modify_bootargs_UpModeInfo()` rewrites `ext_args`/`bootargs`
  (`%s=0x%08x`) so the **kernel cmdline field is generated, not copied**.
* `neglect_bootargs_up_mode_info` is read with `ofnode_read_u32` and, when non-zero, prints
  *"neglect_bootargs_up_mode_info=%d. do not check up mode!"* — it **disables the consistency
  check between the bootargs `UpModeInfo=` and the profile/DT value**. `[FACT]`

`[INF]` What that implies, and why it is *not* a way in:
* It is a **board-configuration compatibility guard** (so a GPON firmware isn't booted on an
  XGPON BOSA, and so the LAN/WLAN uplink port stays consistent), *not* an authentication
  mechanism. A wrong `UpModeInfo` at worst makes U-Boot rewrite it (`modify config`) — the
  strings never mention refusing to boot.
* Because it is a **DT property of the bootloader's own control DTB, hard-coded to 0**, there is
  no env variable, no profile key and no Linux file that turns it on. The only way to change it
  is to replace U-Boot or its appended DTB — which is exactly the thing we are trying to avoid.
  **So the "most promising lead" is a dead end for entry purposes**, and its real value to us is
  negative information: it shows the vendor boot path is *config-driven*, which supports the
  env-tampering route instead.
* The **safe way to test** it (still requiring a console, so: TTL, not this session): read
  `up_mode get` and the profile, then set `UpModeInfo=0x00000008` (gpon) in `ext_args`/`bootargs`
  from the console (`setenv ext_args UpModeInfo=0x00000008`) **without saving env**, `boot`, and
  observe whether the printed `[%s] check UpModeInfo error! …` / `[%s] up_mode_check_verify
  error! ret=%d modify config` lines lead to a boot refusal or only a rewrite. A refusal would
  mean a *second* guard exists in the kernel/csp layer. Never test this by writing the env
  block, and never with `factoryrestore` (it resets the PON mode, i.e. could de-configure the
  optical BOSA and drop the ISP link).

---

## 5. The one real software-only channel: the environment block at mtd1+0x180000

### 5.1 `[FACT]` exact layout, with a verified CRC

```
device-dumps/sk-d840n_b1.bin @ 0x180000   (mtd1 "boot", absolute flash offset 0x180000)
  0x180000  u32 LE CRC32                    = 0xc638125c   (bytes 5c 12 38 c6)
  0x180004  name=value\0 ...                = 0x312 bytes used, ends 0x180316
  0x180317  .. 0x1A0000  zero padding       (area total 0x20000 = 128 KiB = one erase block)
  0x1A0000  0xFF …                          (erased)

crc32(buf[0x180004 : 0x1A0000]) == 0xc638125c   ← verified by brute-forcing every end
```

i.e. **`ENV_OFFSET = 0x180000` inside mtd1, `ENV_SIZE = 0x20000`, `ENV_CRC` over the whole
128 KiB minus the 4-byte field, no `ENV_FLAGS` byte, no redundant copy** (0x1a0000 and 0x1c0000
are erased). Same location used by the community `boot.bin` (its env is also at `0x180000`),
`[FACT]` — so the location is a family constant, and the CRC recipe is reproducible offline
(`python -c "import zlib;…"`), which means **a valid env blob can be built on the PC.**

Content (`[FACT]`, 14 variables): `baudrate=115200`, `bootargs=console=ttyAMA0,115200n8
rdinit=/sbin/init U-Boot 2.0.0 20251201200927 0x2200000 0x1 0x83 0x83  UpModeInfo=0x00000040
regioncode=0x0`, `bootcmd=zxboot`, `bootdelay=1`, `ethact=eth0`, `ethaddr=00:29:47:00:00:32`,
`ext_args=UpModeInfo=0x00000040`, `fdtcontroladdr=9f6efd40`, `ipaddr=192.168.1.1`,
`mtdids=nand0=spi-nand0`, `mtdparts=<H_B table, 15 entries>`, `serverip=192.168.1.101`,
`stderr=serial`, `stdin=serial`, `stdout=serial`.
No `preboot`, no `altbootcmd`, no `recover`/`boot_once`.

Note the community env, same block, for contrast: `bootcmd=mtd read kernel 0x88000000; mtd read
dtb 0x88800000; bootm 0x88000000 - 0x88800000`, `bootargs=… root=/dev/mtdblock5 … 0x200000 0x0
0x83 0x83 …`, `mtdparts=…:8192k@0x200000(kernel),1024k@0xa00000(dtb),4096k@0xb00000(parameter
tags),246784k@0xf00000(root)`. So the `bootargs` fields decode as
`… <U-Boot version> <14-char build stamp> <active kernel flash offset> <slot nibble> <0x83>
<0x83> UpModeInfo=… regioncode=…`, generated by `zxboot` (`0x%1x 0x%02x 0x%02x ` at `0x0a425a`).
`[INF]` (formats are inferred; the offset/slot difference between the two builds is fact.)

### 5.2 Why this is the only viable lever

`[FACT]` U-Boot's env loading is compiled in (`Loading Environment from %s... ` `0x0a7b9a`,
`Saving Environment to %s... ` `0x0a7bd8`, `bad CRC` `0x0a7b2c`,
`## Resetting to default environment` `0x09696c`, `Environment size: %d/%ld bytes` `0x096930`)
and `saveenv`/`env save` exist. `fdtcontroladdr` inside the block is a **runtime-generated
address**, so this block was written by a running U-Boot at least once. `[FACT]`

`[INF]` If U-Boot loads it at each reset (that is the normal `ENV_IS_IN_*` behaviour), then
rewriting this one erase block from Linux gives us **`bootcmd`**, i.e. arbitrary *headless*
U-Boot action on the next reset, e.g.:

```
bootcmd=tftpboot 0x88000000 uImage; nand erase.spread kernel0; nand write 0x88000000 kernel0 ${filesize}; zxboot
bootdelay=-1          (or 100, so a human can still take over on UART later)
```

with `ipaddr=192.168.1.1`/`serverip=192.168.1.101` already present and a working `ethaddr` —
so a PC at `192.168.1.101` running TFTP is enough; no console, no soldering.

Safety properties that make it the *least bad* first experiment:
* The env block is the **last erase block of mtd1** (`0x180000–0x1A0000`) and the U-Boot code
  ends at ~`0x0c8000` → erasing/writing it does **not** touch the bootloader or any kernel. `[FACT]`
* A bad CRC is a *self-healing* error: U-Boot prints `bad CRC`, resets to the compiled-in
  default env (`bootcmd=zxboot`, `bootdelay=1`) and boots stock firmware normally — the vendor
  boot path is code-driven (`zxboot` builds `bootargs` itself from DT+profile, since the default
  env has no `bootargs`). `[INF]`, and it is the single most important assumption to verify.
* Everything is backed up (`device-dumps/sk-d840n_b1.bin`), and the block can be restored
  byte-for-byte from that file. `[FACT]`

Risks / unknowns (must not be understated):
* **[UNK] Does the vendor Linux rewrite the env?** If `cspd`/an init script calls a `fw_setenv`
  equivalent (there is no `fw_printenv` in the image), our edit could be silently reverted at
  shutdown, or — worse — a *stale* env write could be re-normalised every boot, making the
  experiment non-reproducible. Read-only check in §7-4.
* **[UNK] Is the SPI-NAND `boot` partition block-locked?** All mtd flags say `MTD_WRITEABLE`
  and jffs2 writes work, but the SPI-NAND lock bits are only provable by an erase. Read-only
  proxies in §7-5.
* **[UNK] Is `0x180000` really `ENV_OFFSET`** as opposed to "a stray copy the factory put there"?
  Decisive test in §7-1 (`printenv` + `bdinfo` on the console, or compare live vs dump after a
  reboot).
* **[INF] Writing env does not bypass anything if security is fused on** — a changed `bootcmd`
  still cannot make U-Boot accept an image it refuses; but it *can* make U-Boot erase the
  partition holding a good kernel. The failure mode is "needs TTL", not "unrecoverable".

### 5.3 Ordering note

If the env route works, the safest sequence is **not** "flash OpenWrt into kernel0". It is:
(1) env edit → `bootdelay` large + `bootcmd` harmless, confirm U-Boot really honours it
(observable *from the ISP side only*: still boots, plus version string change in
`/proc/cmdline` on the next boot — see §7-2); (2) then use U-Boot to back up and write the
**inactive** slot (`kernel0` @ `0x200000` on this unit, since cmdline says slot 1/`0x2200000` is
active) so a bad image falls back automatically (`[FACT]` U-Boot searches and switches slots:
`[WARN] kernel%d is not valid, switch to kernel%d`); (3) only then consider flipping the slot
selector (`BootImageNum` in the `others` profile, and/or the jffs2 `flag_type` = `current : 0`
in `/tagparam`).

---

## 6. Candidate no-disassembly paths, ranked by feasibility × risk

Feasibility = evidence strength that the path exists at all; Risk = chance of needing the very
disassembly we are avoiding.

| # | path | status | feasibility | risk | exact **read-only** check that confirms or kills it |
|---|---|---|---|---|---|
| 1 | **Rewrite U-Boot env at mtd1+0x180000 from Linux** → drive `bootcmd` headlessly (tftp + `nand`/`mtd write`) | [FACT] layout+CRC known, [UNK] load/lock/clobber | **high** | **medium** (only that one erase block; bad CRC → default env fallback) | `printenv` on the TTL console and compare 1:1 with §5.1; `bdinfo`; on Linux: `cat /proc/mtd`, `mtd_debug info /dev/mtd1`, re-read `0x180000` twice across a reboot and diff vs our dump (§7-1…7-4) |
| 2 | **`nandwrite`/`mtd_debug write` straight into mtd1/mtd3 from Linux** (skip U-Boot entirely), e.g. install community `boot.bin` | [FACT] tools + `MTD_WRITEABLE` + jffs2 writes work; [UNK] NAND lock bits | medium-high | **high** (a failed boot-partition write is a brick, and the *only* recovery is `REC` over UART = disassembly) | read-only proxies: `mtd_debug info /dev/mtdN`; `cat /sys/class/mtd/mtd*/{name,size,erasesize,flags,writeable}`; check for a lock/WP sysfs or `spinand` driver params; `grep -i 'lock\|write_protect\|WP' ` in `dmesg` and in the `spi-nand` driver's DT node (`/proc/device-tree/**`) (§7-5) |
| 3 | **Write the *inactive* A/B slot + flip the slot selector** (keep `boot` untouched, automatic fallback) | [FACT] `BootImageNum`/`kernel%d … switch to kernel%d` fallback, `flag_type=current : 0` | medium | **low-medium** (slot fallback is vendor-designed) | confirm which slot is active: decode `cat /proc/cmdline` (`0x2200000 0x1`) against the H_B map; `dd` 16 B at `0x200000` and `0x2200000` from `/dev/mtd0ro` and compare the header/version; dump the `others` partition and look for `BootImageNum=` text (§7-3, 7-6) |
| 4 | **Vendor upgrade machinery** (`fw_flashing`, `siupgrade`, TR-069/CU image server, `mcupg`) with a *repackaged* payload | [FACT] `upgrade.bin/uboot.bin/vmlinuz.bin/rootfs.img/osgi.img/phapp.img`, header-CRC seals are recomputable; [UNK] FIT signature nodes, [UNK] package signing on the Linux side | medium (needs the package format + probably a valid CMCC signature) | medium | `fw_flashing --help`; `siupgrade --help`; `grep -ri 'fw_flashing\|siupgrade\|upgrade' /etc /usr/local /scripts`; list `/usr/local/upgrade_module` contents + `file` each; check for a public key/cert on the filesystem; read the FIT strings block of `kernel0` (§7-7, 7-8) |
| 5 | **`UpModeInfo` / `neglect_bootargs_up_mode_info`** as an entry or as a way to skip checks | [FACT] DT value is `0` in *both* stock and community builds; it is a PON-config guard, not an authz check | **low** | n/a | nothing to run on the device: the DTB is already read (value 0). Killed unless a replacement DTB/U-Boot is flashed. Offline-only follow-up: build an ITS with the property set to 1 and see if U-Boot *accepts* a self-built image at all (§4.3) |
| 6 | **efuse as a key source** (`zx-efuse 14f11000.efuse`) — read whether safety boot / AES keys / jtag lock are programmed | [FACT] nvmem driver present in the running kernel | high (read side) | **low** to read; fatal to write | `ls /sys/devices/platform/*/nvmem /sys/bus/nvmem/devices/*/nvmem`; `od -An -tx1` the relevant ranges (never write), and map the result against the fuse field names in the dump (`SEC_BOOT_PROTECT`, `AES_KEY0..3`, `Backup_AES_KEY0..3`, `Key_vld`, `ATE_*`) — this single read **decides whether paths 1–4 can ever boot a self-built image** (§7-9) |
| 7 | **Trigger `REC`/BTL from Linux** (`devmem2 0x10e10170`, or hold WPS across reset, or wdt-forced reset) | [FACT] register + dispatch located; [INF] WPS GPIO consulted by stage-1 | high (mechanically) | **catastrophic** — it *requires* the UART you don't have | none needed for feasibility; the check is about the *risk*: confirm whether the low 2 bits of `0x10e10170` are readable and what they read (`devmem2 0x10e10170 w` = read-only) and whether `zx_wdt` exposes a reset-cause file (§7-10). **Do not write it.** |
| 8 | **Safe-boot menu / `uboot menu` password** (crack or read `.ubmenupsd`) | [UNK] value; [FACT] it is a 16-byte digest in the `others` profile with compiled-in defaults | **low** for a no-disassembly entry (menu is serial-only anyway) | low (reading) | dump `others` (`mtd_debug read /dev/mtd7 0 <size>`), grep for `.ubmenupsd=`; offline: crack the three constants at `0x0b15bc/0x0b15cc/0x0b1630` (MD5 + SHA-256 + salted-with-serial/MAC wordlists) (§7-6, §8) |
| 9 | **JTAG** | `sjprotect` = *"Set cpu jtag protect"* exists as an efuse command | low (and it is disassembly anyway) | — | read the efuse dump for the jtag-protect bit (same command as row 6) |

---

## 7. Dead ends, stated explicitly

1. **`uboot menu` without a serial console** — dead. The menu is reached only from inside
   U-Boot's boot flow (`0x62508`), whose only interactive surface is `stdin/stdout=serial`;
   there is no network console in this build (no `dhcp`/`http`/`telnet`/`webrecovery` strings),
   and the trigger is an efuse bit, so no boot flag or env variable opens it. **[FACT/INF]**
2. **`REC` XMODEM as a no-disassembly recovery** — dead. Stage-1's monitor is UART-only
   (`x [addr]`, `r/w/g`, `b <n|o|s|u>`); there is no network path into it. It is a *post-TTL*
   tool. **[FACT]**
3. **"Checkerboard" as evidence of a flash integrity/recovery checker** — dead; it is a
   `memtester` pattern name (`0x0a52f5–0x0a53b6`). **[FACT]**
4. **`neglect_bootargs_up_mode_info` as a soft switch** — dead for entry: it is a build-time DT
   constant = 0 in the stock U-Boot DTB *and* in the community DTB. **[FACT]**
5. **Flashing the community `boot.bin` to get rid of the vendor lock** — pointless and
   counter-productive: the community build contains the same `swsfmd`/`.sfmenupsd`/`up_mode`
   code and the same safe-boot gate; it changes only the bootcmd/partition map. **[FACT]**
6. **Reusing AN758x material** (`uboot-an758x/net/lwip/httpd.c` web recovery,
   `stock2ubi/an758x_mtd_rw.c`) — **not applicable** to this unit (different BootROM, no FIP/TB
   chain, no Airoha driver); its `httpd` idea is only interesting as a *feature to want* in a
   future ZX279133 build we do not have sources for. **[FACT]** (this repo's own conclusion, kept
   for the record).
7. **`sky_bakeup_info`'s hard-coded `nand erase f000000 …` offsets** — do not reuse: they belong
   to a different flash map than H_B. **[FACT]**
8. **The prior flash map H_A** (param at `0x200000`, kernel0 at `0x600000`) — **wrong**; three
   independent artefacts (U-Boot DTB `part@256`, the mtd3 image header slot table, the
   community env/README's `0x04800000` param read) all give H_B. Any absolute-offset command
   built on H_A would have written the wrong partition. **[FACT]** (supersedes the note in
   memory; also note "0x04800000 reads all 0xFF" contradicts H_B and should be re-measured,
   §7-3.)

---

## 8. Still unknown / offline work

* **[UNK]** The two menu password digests are not cracked. Next offline step (no device needed):
  extend `devtools/pwdb.py` to (a) SHA-256 the 32-byte constant `0x0b15bc…0x0b15db`,
  (b) salt with `SK-D840N`, `ZX279133`, `133s`, `board_info_id=2`, `fs_type_id=0xb`,
  `optical_info_vendor=GN28L97`, the paramtag serial/MAC, and CMCC/Skyworth/ZTE wordlists,
  (c) treat `0x0b15cc`/`0x0b1630` as **AES-128 keys** and test AES-128-CBC self-consistency
  (the 32-byte pattern `key||ciphertext` shape at `0x0b15bc` fits that reading).
* **[UNK]** Whether the `kernel0` FIT has `signature`/`hash` nodes → needs the tail of the FIT
  (strings block at image offset `0x1299b80`).
* **[UNK]** Whether U-Boot loads env from `0x180000` on *every* boot, and whether the vendor
  Linux rewrites it.
* **[UNK]** SPI-NAND block protection on `boot`/`kernel*`.
* **[UNK]** The exact polarity/hold-time of the WPS key path in stage-1 (`not enter wps key`),
  and which `0x10e10170` value means "wdt reset" vs "power on".
* Not investigated here (out of scope): `fw_flashing`/`siupgrade` argument semantics, TR-069
  hooks, `/userconfig` `defaultcmd`/`testcmd`/`RestoreFlag` semantics.

---

## 9. Sources

Local, primary: the four dumps and the community repo listed in §Method; every claim above is
traceable to a file offset in `devtools/*.txt` outputs.
External:
* [TTL连接SK D840n光猫出现U-BOOT指令，有请大佬出手！ — right.com.cn thread-8412569](https://www.right.com.cn/forum/thread-8412569-1-1.html) — TTL reaches `=>`; `help` lists `submpasswd`/`swsfmd`; author's final verdict *"840n目前依然无解"*.
* [SK-D841L 2025年V3.0 新版Uboot密码和TTL密码无解 — right.com.cn thread-8450469](https://www.right.com.cn/forum/thread-8450469-1-1.html) — sibling model, new u-boot password reported unsolved.
* [SK-D840N 开启telnet 方法（UART） — right.com.cn thread-8471395](https://www.right.com.cn/forum/thread-8471395-1-1.html), [2025年10月创维SK-D840N清分区开Telnet — thread-8448705](https://www.right.com.cn/forum/thread-8448705-1-1.html), [9月移动创维SK-D840N用TTL也无法开启TELNET — thread-8412438](https://www.right.com.cn/forum/thread-8412438-1-1.html), [输错telnet密码之后…无解 — thread-8415410](https://www.right.com.cn/forum/thread-8415410-1-1.html).
* [中兴ZTE ZX279128S芯片固件解密 — bbs.kanxue.com thread-276970](https://bbs.kanxue.com/thread-276970.htm) — "uboot有Hash和RSA校验", gzip/LZMA payloads, uImage magic + CRC32, failure text `code hash error !!`.
* [中兴ZX279127S/ZX279128S rootfs CRC校验过程研究 — right.com.cn thread-8478418](https://www.right.com.cn/forum/thread-8478418-1-1.html) — CRC enforced in the bootloader (`vmlinuz crc error`), `binheader @ 0x114`, patched images require recomputing the seals.
* [huxiangjs/SK-D840N-OpenWRT README](https://github.com/huxiangjs/SK-D840N-OpenWRT) (local clone `sk-d840n-openwrt/`) — press `u` at power-on, `tftpboot`+`nand erase/write` offsets, ships `cmd/devmem2`.
