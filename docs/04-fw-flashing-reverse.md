# 04 — `/bin/fw_flashing` reverse (SK-D840N, ZX279133)

Object: `device-dumps/sk-d840n_fw_flashing.bin` (72 964 B) = vendor in-system flasher.
Read-only static analysis; the binary was never executed and no device was contacted.

**Main-agent verification (2026-10-09, after this doc was written)** — re-checked independently:
ELF header is `ELFCLASS32 / e_machine = 40 (EM_ARM) / ET_EXEC / e_entry = 0x15480` ✔ (so this
Arm32 binary runs on the aarch64 kernel as AArch32 EL0 — a replacement tool must be built Arm32
EABI5 or aarch64-static); the optstring `d:r:p:t:f:m:`, `Magic:CU97865`, `/etc/cert/ct_priv.pem`,
`BOOTV000`, `upgrade boot is not support!`, `erase OTHER_MTDDEV failed!`, `/proc/pon/upmode`,
`/tmp/upgrade_runningver`, `unsupport nantong!` are all present ✔. One paraphrase to correct:
there is **no format string `FIRSTCRC:%08x`** — the actual `.rodata` around `file+0xeded` is
`Magic:CU97865\0` `sky: certify magic head failed!\n\0` `FIRSTCRC:\0` `can not find crc pos\0`
`first crc from cert is [%08x], first crc from file is [%08x]\n\0` `cert crc check fail…`, i.e. the
cert trailer carries a literal `FIRSTCRC:` label whose value is parsed separately. Substance of §2
(4-byte binding, replayable) is unchanged.

**Offset convention** — the file is **ELF32, `e_machine = 0x28 (EM_ARM)`, `e_type = ET_EXEC`,
`e_flags = 0x05000200` (EABI v5 + `EF_ARM_SOFT_FLOAT`)**, link base `0x10000` (fields at file
`0x12`/`0x24`). [FACT] It is *not* aarch64 — the Aarch64 kernel runs it as AArch32 EL0.
For `.text`/`.rodata`, **`vaddr = file_offset + 0x10000`**; all offsets below are **vaddr**.
`.text` `0x12e28..0x1eb88` (file `0x2e28..0xeb88`), `.rodata` `0x1eb90..0x20d5c`
(file `0xeb90..0x10d5c`). `.symtab` is stripped, but `.dynsym` (file `0x888`, **116 sized
functions**) exports every local function by name — that is where all symbol names come from.
Helpers: `devtools/arm32/fw32.py` (`dis`/`xref`/`sweep`/`calls`) run under `C:\Python313`
(`capstone 5.0.7` — `python3` on PATH is the broken stub), plus one new helper
`devtools/adrstr.py`, needed because `fw32.analyse()` drops the
`ldr rT,[pc,#k] … add rT,pc,rT [add rT,rT,#imm]` "adr-delta" form that carries most of the
`mtduserapi.c` strings.

---

## 1. Invocation interface

* [FACT] `main` = vaddr `0x12e28` (size 9816, file `0x2e28`). First action:
  `InitAppComm(0x320000, 0)` @`0x12e78`; on failure it logs `fw_flashing init fail!`
  (str `0x1ef11`) and exits → **the IPC/oss framework must be up**.
* [FACT] Argument parsing is plain `getopt` @`0x12ed4` with optstring **`"d:r:p:t:f:m:"`**
  (str `0x1ef42` = file `0xef42`); all six take a required argument. The `switch` jump table is
  the literal block `0x12ef4..0x12f34` (17 entries for `'d'`…`'t'`):

  | opt | handler | action | target |
  |---|---|---|---|
  | `-d` | `0x12eb4` | `atoi(optarg)` | `g_delay_time` (`0x31bec`); `sleep()`ed before anything else @`0x12fd8` |
  | `-r` | `0x12f38` | `atoi(optarg)` | unnamed `.bss` word `0x3177c` — **non-zero ⇒ reboot at exit** (test @`0x15284`) |
  | `-p` | `0x12f4c` | `atoi(optarg)` | `g_iFwMediaPid` (`0x31bf0`) — echoed back in IPC/`printf`, also selects media-protocol handling @`0x14ffc` |
  | `-t` | `0x12f60` | `atoi(optarg)` | `s32UpgradeType` (`0x31be8`) |
  | `-f` | `0x12f74` | `strncpy(fp-0x1cc, optarg, 0x7f)` | **source image path**, 128-byte buffer (`memset` @`0x12e5c`) |
  | `-m` | `0x12f8c` | `atoi(optarg)` | unnamed `.bss` word `0x31780` — gates the progress-message/`UserLedAction` blocks read @`0x13314`, `0x13778`, `0x13bf0` (osgi/phapp/software "please wait" + LED `0x9500`) |
  | any other char | `0x12fa4` | logs `fw_flashing:no parameter` (str `0x1ef29`, level 8, `fw_flashing_product.c:0x83d`) and **continues** | — |

* [FACT] There is **no usage/help text at all**: a full `.rodata` string sweep for
  `usage|help|option|argv|-%c` returns only `fw_flashing:no parameter` and `invalid param!`
  (`0x1ff17`). No stdin is read (`fgets` is used only on `/proc/...` files).
* [FACT] It is designed to be called *by* the upgrade daemon and reports back over IPC:
  `asend()` ids `0xa461` @`0x152ec`, `0xa462` @`0x1532c`, `0xa463` @`0x1538c`, all with the
  literal task name **`upgrade_mgr`** (str `0x1f95f`). Exit triage @`0x15280`: `-r != 0` →
  `asend(0xa461)` + `FreeAppComm` + `sleep(10)` @`0x152f8` + **`reboot(0x1234567)`** @`0x15300`;
  `-r == 0` and `-m != 0` → `printf("%s\n", success|failure)` @`0x153b4..0x153c8`;
  `-r == 0` and `-m == 0` → `asend(0xa462|0xa463)` to `upgrade_mgr` @`0x1532c/0x1538c`.
* [FACT] On exit it **deletes the source image**: `unlink(<-f path>)` @`0x15254`, skipped only
  when `s32UpgradeType == 7` (test @`0x1524c`). A manual run therefore consumes its input file.
* [FACT] Partition/slot selection is **not** an argv option. The target comes from
  (a) `g_wFirmwareType`, a u16 read out of the image container's version header — dispatch
  @`0x1327c`: `== 9` → boot-only image → logs `!!! start to flash boot` (`0x1f043`) **and then**
  `upgrade boot is not support!` (`0x1f05b`) and exits @`0x132cc`; `== 7` → osgi writer;
  `== 8` → phapp writer (LED `0x9500` @`0x137a8`/`0x13c20`); any other value → the combined
  software path (`check_ver_board` → `write_version_File`, inlined from `0x13b48` to `0x153e8`)
  which is the path that can rewrite `boot`, `vmlinuz`, `jffs`, `osgi`, `phapp`;
  (b) the A/B slot, from `/proc/zxic/versionstates` (§4).
* [INF] A human can call it deliberately — e.g. `fw_flashing -f /tmp/x.img -t 0 [-p <pid>] [-m 1] [-d 0] [-r 0]`
  — provided `InitAppComm()` succeeds. No file in the extracted rootfs mentions `fw_flashing`
  (`grep -rl` over `sk-d840n-openwrt/rootfs/` → no hits), so the real caller
  (`upgrade_mgr`/web UI/TR-069 handler) is not in the snapshot we hold.

## 2. What is validated before `boot` is written

Gate chain, in execution order, all inside `main`:

1. [FACT] **`sky_certify(<-f>)` @`0x13208`** — the only cryptographic gate, and it applies to
   *every* type (both the `sec` branch @`0x1310c` and the `no sec` branch converge here).
   Return `!= 0` → `mov sb,#1` @`0x13168` → exit path @`0x1527c`: **aborts before any open/erase**.
   `sky_certify` = vaddr `0x157e4` (file `0x57e4`):
   * reads the first `0x40` bytes of the image (`fread` @`0x15860`; failure →
     `sky_certify get first crc failed` `0x1ed32`);
   * `fseek(END)`; requires length `> 0x80` (`file length is error` @`0x15890`);
   * reads the **last `0x80` bytes** (`fseek(len-0x80)` @`0x158cc`, `fread` @`0x158e4`);
   * `sky_rsa_decrypt(buf, key="/etc/cert/ct_priv.pem", 0x80)` @`0x1590c` → vaddr `0x156c4`:
     `PEM_read_RSAPrivateKey` @`0x15710`, **requires `RSA_size()==0x80`** (cmp @`0x15734`,
     else `datalen != RSA_LEN error`), `RSA_private_decrypt(..., padding=1)` @`0x15790`.
     So this is **decrypt-with-private-key**, i.e. the vendor "signed" the cert blob with the
     private key and the device holds that same private key. The *verify-with-public-key*
     functions exist but are **dead**: `sky_rsa_encrypt` (uses `PEM_read_RSA_PUBKEY`) has
     **zero call sites** in `.text`.
   * `memcmp(plain, "Magic:CU97865"` (`0x1eded`)`, 13)` @`0x1593c`;
   * `strstr(plain,"FIRSTCRC:")` @`0x15954` → `sscanf("%08x")` @`0x15978` → compared with the
     **little-endian u32 at image offset `0x08`** (`[fp-0x54]` = header+8, load @`0x15980`).
     Mismatch → `cert crc check failed!, the file has been modified` (`0x1ee7a`) → return 1.
   * [FACT] Nothing else in the image is bound to the cert block. There is **no CRC32 over any
     payload**: `crc32_init` (`0x15a14`) and `crc32_accumulate` (`0x15aac`) have **zero call
     sites** in `.text` (verified by a full `.text` call sweep). The complete set of CRC strings
     in `.rodata` is the four `sky_certify` ones listed above.
     [INF] ⇒ the 128-byte "signature" is replayable: copy the last 128 bytes of *any* genuine
     vendor image plus the matching u32 at offset `0x08` of that same genuine image, and
     `sky_certify` passes for an arbitrary payload. No key material needed. If
     `/etc/cert/ct_priv.pem` is missing, `sky_rsa_decrypt` returns NULL (`fopen` failure path
     @`0x156ec`) → `sky_rsa_decrypt failed!` → `sky_certify` returns 1 → **nothing can be
     flashed at all**, including vendor upgrades.
2. [FACT] `open(-f,O_RDONLY)` @`0x1321c` → `GetVersionInfo(fd)` @`0x13250` (vaddr `0x16654`):
   walks the container header (`0x40`-byte records, big-endian magic + BE size, payload padded
   to 64 B — chain walk @`0x166f0..0x16730`, and the same walk inlined in `main` @`0x13cc4..0x13d6c`
   with magics `0x89c45a`, `0x189a549`, `0x2d821b4`, `0x3dcff32`, `0x3dcaa32`); validates
   per-record magics (`invalid magic! ulPos: 0x%x, headerMagic:` `0x1fce2`); fills
   `g_wFirmwareType`/`g_wFirmwareForm`/`g_upgrading_vhdr`; writes `/tmp/ugrswversion` (`0x1fa02`,
   `fopen(..,"w")` @`0x16a2c/0x16a5c`); and — **only when `g_zxic_current_security_flag != 0`**
   (test @`0x16730`) — parses the region id out of `/tagparam/flag_type` (`current` token,
   @`0x1677c..0x168ac`) and requires `== 199` (`cmp #0xc7` @`0x168b4`) else
   `sky: the version and the upgrade file secure unmatched, and region is[%d] not 199, abort!`
   (`0x1fcc4`). `g_zxic_current_security_flag` is forced to **0** on the "no sec" branch
   (`str r6,#0` @`0x13194`), which is the branch our unit takes (`sec_check_state()==0`
   @`0x12ff0`; kernel log `no sec mode, read mtd`) → [INF] the 199 gate is dead here.
3. [FACT] `g_wFirmwareType == 9` → refused (see §1). Boot can only be rewritten as a *record
   inside* a combined software image.
4. [FACT] `check_ver_board()` @`0x13b48` (vaddr `0x173f8`) — the version guard. Reads
   `/proc/zxic/boardtype`, `/proc/zxic/softVersion`, `/proc/zxic/vendorinfo`,
   `/proc/zxic/hardVersion` and `/etc/customInfo` (`getCustomInfo`, which uses `popen` @`0x171b8`),
   `sscanf`s the image header's 7 version fields with `"%15s %15s %15s %31s %15s %15s %15s"`
   @`0x176f8`, and on mismatch prints `board type not match, type is:%s; software type is:%s`
   (`0x20101`),
   **`%s-%s software can not upgraded by %s-%s version!`** (str `0x20138`, emitted @`0x17820`) or
   `svnlwlimit:%s software can not upgraded by %s version!` @`0x17894`, then returns non-zero.
   `main` @`0x13b4c`: `subs r7,r0; beq 0x13b7c` → non-zero →
   `check software version error` (`0x1f385`) → `mov sb,#1` @`0x13b74` → exit @`0x1527c`.
   [FACT] **It aborts; it does not merely log.**
5. [FACT] `GetVersionOffset(&running,&upgrading)` @`0x13c30` (vaddr `0x15f4c`) reads
   `/proc/zxic/versionstates` keys `current`, `version1states`, `version2states`
   (@`0x15fb0`, `0x16004`, `0x1605c`). Then `access("/tmp/upgrade_runningver", F_OK)` @`0x13db0`:
   if present → `upgrading` is overwritten with `running` (`str` @`0x13dbc/0x13dc0`) =
   **in-place overwrite of the live slot**.
6. [FACT] Boot record handling (`write_version_File`, inlined `0x141bc..0x145e8`):
   presence test `[fp-0x230] != 0` @`0x141bc` (the `0x89c45a` record's size field) →
   `start upgrade bootloader!` @`0x141c8`; `lseek(fd, data_off+size-0x40)` @`0x141f8` +
   `read 0x40` @`0x14234` → short read = `Read bootloader header failed` (`0x1f4d8`) → abort;
   the trailer's `+0x38`/`+0x3c` are logged as `btFlashOffset`/`btFlashSize`
   (@`0x14388`, `0x143b0`; loaded @`0x14268`/`0x14278`); `/proc/zxic/bootVersion` is read with
   `fopen`/`fgets` @`0x14284/0x142bc`; `malloc(size+4)` @`0x143cc`, `read` @`0x14440` —
   short read = `Read bootloader data failed` (`0x1f597`) → abort.
7. [FACT] **`check_boot_version(buf)` @`0x1448c`** — the last gate before the erase, and it is
   skipped entirely when `gmtd[0] == 4` (`ldrb` @`0x1447c`; `gmtd` (`0x31b88`) is referenced
   exactly **once** in `.text`, never written here → [INF] it is `0`, so the check runs).
   vaddr `0x164c8`: `strncmp(buf+0x40, "BOOTV000"` (`0x1fbe1`)`, 8) == 0` → return **0 = pass**;
   otherwise `open("/dev/mem", 0x101002)` (`0x16508`; low bits = `O_RDWR`, the high bits are the
   vendor's sync/no-cache flags) then
   `mmap` of **`0x90900000`** @`0x16550`, `ver = +(0x10)`, `pkt = +(0x300)` (@`0x16580/0x16584`);
   `(ver&0xff)==1` → pass; `(pkt&1)==0` → pass; else `unsupport nantong!` (`0x1fc35`) → return 1.
   `main` @`0x14490`: `cmp r0,#1` → equal → `check boot failed!` (`0x1f5b3`) @`0x1449c` →
   `free` → exit @`0x15244`. [FACT] **Aborts, and it aborts before the erase.**
   [INF] This is a *chip-family* gate (is this SoC the right variant), **not** an integrity
   check: any boot blob carrying the ASCII tag `BOOTV000` at offset `0x40` passes unconditionally.
8. [FACT] Only then: `Erasing boot 0x%x - 0x%x` (`0x1f5c7`) logged @`0x144d8` with arguments
   **`(0, 0x200000)`** (`str r5=0` @`[sp+8]`, `movw/mov sb,#0x200000` @`0x144d4`/`0x144e0`) →
   `fwerase("/dev/mtd1"` (`0x1f5e0`)`, 0, 0x200000, 0)` @`0x14500`; `erase failed!` → abort
   (post-erase). `fwwrite_("/dev/mtd1", fd, file_off, 0, len)` @`0x14568` → `write_file`
   (`0x15aec`) → `CspUserMtdWrite` (`0x18528`); `write boot failed!` → abort.
9. [FACT] **`Check bootloader failed!`** (`0x1f61c`) comes from `fw_check_file(...)` @`0x145a8`
   → `check_file` (`0x16120`) which is a pure **read-back memcmp** (4 KiB chunks,
   `CspUserMtdRead` @`0x16230` / `CspUserMtdReadForSquashfs` @`0x161f8`, `memcmp` @`0x16268`,
   diagnostics `buf1[%d] is %d` / `buf2[%d] is %d` / `memcmp wrong`). It runs **after** the
   program, so it does not prevent the write — it only makes the tool report failure and skip
   the bookkeeping/`BootImageNum` update (`b 0x15238` → `sb=1`).
   [FACT] Same pattern for every partition: `fw_check_file` has 9 call sites
   (`0x1365c`, `0x13a74`, `0x145a8`, `0x14840`, `0x14998`, `0x14a5c`, `0x14ab8`, `0x14c54`,
   `0x14e2c`).

`/etc/cert/ct_priv.pem`: **not present in any artifact we hold.** `sk-d840n-openwrt/rootfs/etc/`
has no `cert` directory (only `/etc/ssl/cert.pem`); byte-scans of
`device-dumps/sk-d840n_{b1,k0,mtd2}.bin`, `staging/rootfs.jffs2`, `staging/parts/p.0{0,1}` for
`BEGIN RSA PRIVATE KEY` / `ct_priv` hit nothing — the only occurrences of the path are inside
`fw_flashing`'s own `.rodata` (`0x1edab`). [INF] Since field upgrades evidently work on these
units, the file must exist on the live (non-dumped) rootfs; [UNK] its key size (must be 1024-bit
for `RSA_size()==0x80`).

## 3. The mtd7 write amplifier — VERIFIED

* [FACT] `WritePrivateProfileInt` = vaddr `0x19c60` (file `0x9c60`), the only profile-int writer.
  Its call sites are exactly the 4 inside the inlined `SetVersionUpgradeBit`:
  `0x15080`, `0x150c0`, `0x15140`, `0x151b8` (`RemovePrivateProfileItem`, `WritePrivateProfileString`
  and `GetAllPrivateProfileItem` are not called from this build's main flow).
* [FACT] Per call, for a single integer:
  1. `GetPrivateProfileIntLocal(key)` @`0x19d4c` — if the value already matches it returns early
     (`WRITE_OTHER_MTDDEV leave because of equality!` @`0x19d58`), so a no-op write costs nothing;
  2. `fwread("/dev/mtd7"` (`0x20633`) `, 0, buf, 0x1000)` @`0x19df0..0x19e04` — reads **4 KiB**;
  3. builds `"<key>=0x%08x,"` @`0x19d98`, checks the terminator `,` @`0x19dac`, `memcpy` splice
     @`0x19f38` into the same 4 KiB stack buffer (`sub sp,sp,#0x1000` @`0x19c70`);
  4. **`fwerase("/dev/mtd7", 0, 0x80000, 0)`** — length literal `#0x80000` = **524 288 B = 512 KiB**
     at `0x19f40`, `mov r1,#0`/`mov r3,#0` @`0x19f48/0x19f4c`, call @`0x19f50`
     (error text `erase OTHER_MTDDEV failed` @`0x20657`);
  5. `fwwrite("/dev/mtd7", r8, buf, 0x1000)` @`0x19f90..0x19fa0` — writes back **4 KiB**
     (`#0x1000` @`0x19f94`; `r8 = 0x1000 - strlen("<key>=0x%08x,")` computed @`0x19e30`,
     [UNK] whether that is the intended mtd offset — it does not change the erase/write sizes).
     [FACT] `fwwrite` (`0x18e18`) = `open(dev,O_RDWR)` + `flock(LOCK_EX)` + `lseek(fd, off, 0)`
     @`0x18e98` + `CspUserMtdWrite(fd, buf, len)` @`0x18ea8` (`CspUserMtdWrite` = `0x18528`,
     `MEMGETINFO` @`0x18564` then page-program at the file position); success test is
     `cmp len, ret` @`0x18eac`.
  ⇒ **512 KiB erased to rewrite ~4 KiB; 508 KiB of the partition is destroyed and never restored.**
  The `0x80000` is a compile-time constant — it is *not* derived from `mtd_info.size` nor from
  `erasesize` (the partition is `others@0x4200000` = 6 MiB).
* [FACT] `fwerase` (`0x18d08`) = `open(dev,O_RDWR)` + `flock(LOCK_EX)` @`0x18d78` +
  `lseek(fd, arg4)` + `CspUserMtdErase(fd, base, len)` @`0x18d98`, then `flock(UNLOCK)` + `close`.
  `CspUserMtdErase` (`0x17b98`) does `ioctl(MEMGETINFO=0x80204d01)` @`0x17be4`, takes
  `erasesize = info+0xc` @`0x17c30`, rejects non-multiple lengths @`0x17c3c`, and for
  `type == 4` (NAND) loops `MEMGETBADBLOCK(0x40084d0b)` @`0x17cec` + `MEMERASE(0x40084d02)`
  @`0x17d90` per 128 KiB eraseblock, logging `Skipping bad block(%d) at 0x%lx` (`0x20427`) and
  `ERR(%d): fail to do ioctl(MEMERASE) at blk(%d) 0x%08x` (`0x2044a`).
  ⇒ the 512 KiB is 4 × 128 KiB SPI-NAND eraseblocks, bad-block-skipped.
* [FACT] The same amplifier applies to every `others` key the flasher touches, and the whole
  `SetVersionUpgradeBit` step issues **2–4** such calls (`Image0Status`/`Image1Status` +
  `BootImageNum`), i.e. up to ~2 MiB of `others` erased per upgrade.
* [INF] Practical consequence for rollback: `others` holds `ethaddr`, `BootImageNum`,
  `Image0/1Status` and board info; every bookkeeping write opens a power-loss window in which
  512 KiB (including anything in the first 512 KiB that is not inside the 4 KiB buffer) is gone.
  This argues for either (a) `touch /tmp/upgrade_runningver` to skip bookkeeping (§4), or (b) not
  using `fw_flashing` at all and replicating the ioctls directly.

## 4. Version / bookkeeping

* [FACT] `/proc/pon/upmode` (`0x20a5a`) and `/proc/pon/up_mode_info` (`0x20a43`) are **read-only
  in this binary**, via `GetUpMode` (`0x1ca74`) and `GetUpModeInfo` (`0x1c9d4`). Both open the
  file with an internal read helper (`0x1b170`) and `strtoul`:
  `GetUpMode` requires `(v-1) <= 0x800` (@`0x1cadc/0x1cae0`) and stores one u16;
  `GetUpModeInfo` bit-unpacks one u32 into 4 u16 fields — bits 0..14, bit 16, bits 17..22,
  bits 23..27 (@`0x1ca40..0x1ca5c`).
  [FACT] **Neither function is reachable**: `GetUpMode`'s only caller is `IsXgsponMode`
  (`0x1cb00`, returns `mode != 0x80`), and `IsXgsponMode` has **zero call sites**; `GetUpModeInfo`
  has zero call sites. No code in `fw_flashing` ever writes `/proc/pon/*` (the strings occur only
  at the two addresses above). ⇒ [FACT] within `fw_flashing` these are **not** switches that
  influence the flash or the next boot.
  [INF] They *are* kernel-exported PON boot-mode state (set by the PON driver/U-Boot handoff), so
  they may matter to the boot chain — but that cannot be established from this binary.
* [FACT] `SetVersionUpgradeBit` is an inlined block of `main`, log tag at `0x15420`, body
  ≈ `0x14fbc..0x15234`. Behaviour:
  1. logs `running_ver_offset=%x,upgrading_ver_offset=%x` @`0x14fdc`;
  2. if `g_iFwMediaPid` equals the *address* of the literals `eoamback`/`omciUpgrade`
     (@`0x14ff4..0x1500c`, `cmp r2,r3`/`cmpne r5,r3`) → logs
     `\r==omci/oam g_iFwMediaPid :0x%x\n` and **returns without touching the profile**
     (@`0x15038`);
  3. otherwise branches on `g_wFirmwareForm` (u16 @`0x31bc0`, test @`0x1506c`/`0x150f0`):
     `form==0` → `WritePrivateProfileInt("Image0Status"` (`0x1eee6`)`, 0xFFFFFFFF)` @`0x15080`
     then `WritePrivateProfileInt("BootImageNum"` (`0x1f8e6`)`, r5)` @`0x150c0`;
     `form∈{1,2}` → key = `g_zxic_current_version_number==1 ? "Image1Status" : "Image0Status"`
     (@`0x15124..0x1513c`) written with **`0xFFFFFFFF`** (`mvn r1,#0` @`0x1512c`), then
     `BootImageNum = (g_zxic_current_version_number == 1) ? 1 : 0`
     (`sub r1,r1,#1; clz; lsr #5` @`0x151a0..0x151b4`, write @`0x151b8`).
     Any `WritePrivateProfileInt` failure → `SetVersionUpgradeBit error!` (@`0x151fc`) →
     `sb=1` → non-zero exit @`0x15240`.
  [INF] So the "boot slot flip" is exactly two profile ints in `others`/mtd7: `BootImageNum` plus
  an `ImageNStatus` marker; U-Boot consumes them (`BootImageNum`, `Image0Status`, `Image1Status`
  also appear as `/proc/zxic/versionstates`-style keys in the `others` dump).
* [FACT] **`/tmp/upgrade_runningver` (`0x1f41b`) is a user-createable override in tmpfs**, tested
  with `access(..., F_OK)` at two points:
  @`0x13db0` — if it exists, `upgrading_offset := running_offset` (@`0x13dbc/0x13dc0`), i.e. the
  image is programmed into the **currently running** slot instead of the standby slot
  (log `upgrade the first version` `0x13f40` / `upgrade the second version` `0x13f3c`);
  @`0x14fb0` — if it exists, `SetVersionUpgradeBit` is **skipped entirely** and control jumps to
  `flash everything success!` (`0x1f988`) @`0x153e8`.
  [FACT] `WritePrivateProfileInt` is therefore never called when the flag file exists ⇒ **no
  512 KiB mtd7 erase** (§3) in that mode.
* [FACT] `/tmp/ugrswversion` (`0x1fa02`) is **written** by `GetVersionInfo` (`fopen(...,"w")`
  @`0x16a2c/0x16a5c`, error `write upgradeVersion error!` `0x1fd30`) with the version string taken
  from the upgrade image's header — a breadcrumb for the UI/daemon, no effect on the flash logic.
* [FACT] `BOOTV000` (`0x1fbe1`) is used **only** by `check_boot_version` @`0x164d0/0x164e0`, as
  `strncmp(boot_payload + 0x40, "BOOTV000", 8)`. It is a tag inside the boot blob (i.e. at
  `0x40` past the start of the boot record's data), not a flash-map or /proc key. See §2 step 7.
* [FACT] Downgrade-guard reachability: `check_ver_board` is called from exactly one site
  (`0x13b48`) on the combined-software path, i.e. after `sky_certify`/`GetVersionInfo` and
  **before** `GetVersionOffset`, the TLV walk and every erase. The guard strings
  `%s-%s software can not upgraded by %s-%s version!` (`0x20138`) and
  `svnlwlimit:%s …` are emitted at `0x17820`/`0x17894` and make the caller abort (§2 step 4).
  [INF] Because the comparison inputs are `/proc/zxic/{boardtype,softVersion,vendorinfo,hardVersion}`
  and `/etc/customInfo` vs. plain text fields in the image header, a custom image can satisfy the
  guard by copying those 7 fields from a genuine image header (`"%15s %15s %15s %31s %15s %15s %15s"`
  layout @`0x176f8`) — no crypto involved.

## 5. Verdict — survivable no-disassembly route to a custom u-boot / kernel

**Yes, with conditions.** Two routes, both without disassembling anything further:

**A. Drive `fw_flashing` with a hand-built container.** Required artifact: a file laid out as
`0x40`-byte header (u32 LE `FIRSTCRC` at `+0x08`) → a chain of `0x40`-byte BE-magic records
(size BE at `+0x04`, payload padded to 64 B, magic `0x89c45a` = the boot record per the test at
`0x141bc`; boot record ends with its own `0x40` trailer carrying `btFlashOffset`/`btFlashSize` at
trailer `+0x38`/`+0x3c`) → **the last 128 bytes = a captured cert trailer from any genuine vendor
image whose header u32 at `+0x08` we copy into ours** (the only field the cert binds, §2 step 1).
Preconditions: `/etc/cert/ct_priv.pem` present and 1024-bit (else nothing, not even vendor
firmware, can be flashed); `g_wFirmwareType != 9` (boot must ride as a record inside a
"software" image); image version fields matching `/proc/zxic/*` + `/etc/customInfo`; boot payload
carrying `BOOTV000` at `+0x40`; `/tagparam/flag_type` region check skipped (`sec_check_state()==0`).
Failure modes: boot slot is erased (`/dev/mtd1`, `0..0x200000`) before programming → an erase
success + write failure = **no boot loader**; `fw_check_file` failing means the tool only reports
error *after* the flash is already rewritten; bookkeeping erases 512 KiB × 2–4 of `others`
(`ethaddr`/`BootImageNum` at risk); the tool `unlink()`s the image and, with `-r != 0`,
`sleep(10); reboot(0x1234567)`; the tool never validates that the boot blob is a ZX279133-bootable
image (it just programs bytes) → **any u-boot image we feed it must already be in the vendor's
on-flash framing**. Mitigation available offline-side: `touch /tmp/upgrade_runningver` before the
run to skip all `others` writes (and to force the running slot for kernel), and never pass `-r`.

**B. Replicate its ioctls directly (`mtd_debug` / `flash_eraseall` / `nandwrite`, already on the
unit).** [FACT] Everything `fw_flashing` does to `boot` reduces to: `open("/dev/mtd1",O_RDWR)`,
`flock`, `lseek(0)`, `ioctl(MEMGETINFO)`, then per 128 KiB block `MEMGETBADBLOCK(0x40084d0b)` +
`MEMERASE(0x40084d02)`, then `write()` of the blob through `CspUserMtdWrite`, then read-back
compare. That is exactly `flash_eraseall /dev/mtd1` + `nandwrite /dev/mtd1 <file>` semantics.
[INF] Route B is strictly safer for *experimenting* (no mtd7 amplifier, no unlink, no forced
reboot, no version guard), but it also gives no protection: same non-atomic erase→write.
For a custom kernel, the slot must be chosen from `/proc/zxic/versionstates` (`current==0` →
`0x200000`, `current==1` → `0x2200000`, per @`0x1306c..0x13088`) — and this unit's active slot is
**kernel1**, so writing kernel0 keeps a fallback.

**Confidence:** §1, §2 (gate list, dead `crc32_*`/`sky_rsa_encrypt`, abort-vs-log semantics),
§3 (512 KiB vs 4 KiB) and §4 = **high** — each claim is anchored to a cited instruction.
"Cert trailer is replayable" = **medium-high** (depends only on the two reads at `0x15860`/`0x158e4`
and the compare at `0x15994`, which I traced end-to-end, plus the absence of any other
integrity call). "Route A/B will produce a bootable custom u-boot" = **low-medium**: nothing in
this binary tells us what the boot ROM/U-Boot require beyond byte layout.

**Could not be determined without running it (or without the other DSOs):**
- whether `/etc/cert/ct_priv.pem` exists on the unit and whether `RSA_size()` is 128 — decides
  whether `fw_flashing` is usable *at all*;
- whether the kernel actually grants write on these MTDs from userspace
  (`docs/01`/`docs/02` report `flags = 0x400` with no `MTD_WRITEABLE`); if `MEMERASE` returns
  `-EPERM`, `fw_flashing` fails with `erase failed!` and route A is dead while route B is equally
  dead — this is empirical, not static;
- the numeric catalogue of `g_wFirmwareType`/`g_wFirmwareForm` (only `7`, `8`, `9`, `form 0/1/2`
  observed) and the exact header field offsets feeding `check_ver_board`
  (`GetVersionInfo`, 1240 B, not fully traced);
- what `sec_check_state()`/`sec_check_flash()` do (imported from the vendor libs, not in this
  binary) and hence whether a given unit takes the `region 199` branch;
- whether `gmtd[0]` can be made `4` (the `check_boot_version` bypass) by a library — `gmtd`
  (`0x31b88`) is written nowhere in this `.text`;
- whether `CspUserMtdWrite` writes OOB/ECC layout acceptable to the ZX279133 boot ROM, and
  whether `fwerase`'s NAND path handles the `boot` partition's 2 MiB vs 128 KiB-block geometry the
  way U-Boot expects;
- whether the real upgrade caller (web UI / TR-069 `upgrade_mgr`) passes extra argv or requires
  the 3.2 MiB `InitAppComm(0x320000,0)` shared-memory region to be serviced — no caller exists in
  the rootfs snapshot we hold;
- whether U-Boot re-validates the programmed `boot` partition (our `boot` dump shows plaintext
  `0001ZXIC`/`BOOTROM`/`RSA2048` tags and entropy 0.360, so no key container was found).
