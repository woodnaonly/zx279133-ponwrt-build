# 02 — Reflashing bootloader / kernel partitions **without opening the case** (SK-D840N, ZX279133)

> **STATUS: PLAN ONLY — NOTHING HERE HAS BEEN RUN AGAINST THE DEVICE.**
> `先只看不刷`. No connection to `192.168.1.1` was made while writing this, no module was
> built, nothing was erased or programmed. The toolchain is not installed on this machine,
> so `devtools/zx_mtd_rw.ko` does not exist yet either.
> Every executable step is dry-run-first: `devtools/zx_reflash.sh` refuses to write, erase
> or load-with-`commit=1` unless `--commit` is passed explicitly, and prints what it would do.

> **AMENDMENT (2026-10-09, after this doc was written) — the central premise below is wrong.**
> This doc was built on "every `mtdN` reports `flags = 0x400` and writes therefore fail with
> `EROFS`, so an unlock module is mandatory". That premise came from the main agent's survey, which
> mis-read the flag word — **it never ran an erase or a write to test it**. What is actually
> established:
> * In mainline `include/uapi/mtd/mtd-abi.h`, `0x400` *is* `MTD_WRITEABLE` (`uboot-an758x/include/mtd/mtd-abi.h:93`).
> * Six partitions are mounted **rw jffs2** and are being written by the running system right now —
>   proven by md5 drift between two raw reads 45 minutes apart on `mtd4_middleware`, `mtd5_usercfg`
>   and `mtd11_plugin_data` chunk 3 (see `backups/MANIFEST.md5`). A read-only MTD cannot do that.
> * The vendor in-system flasher `/bin/fw_flashing` (pulled to
>   `device-dumps/sk-d840n_fw_flashing.bin`) calls `MEMERASE` and writes `/dev/mtd1` (boot),
>   `mtd3`/`mtd6` (kernel slots) and `mtd7` (`others`) as ordinary production flow — string
>   `!!! start to flash boot`, `Erasing boot 0x%x - 0x%x`.
>
> ⇒ **`devtools/zx_mtd_rw.c` is probably unnecessary.** Keep it as a contingency for the case where
> a specific partition turns out to carry `MTD_NO_ERASE` or the SPI-NAND block-protect bits are set
> (`docs/01` §7 Q9 — still open, and only an actual erase can prove it; do not probe that on `boot`).
> What still stands in this doc: the ECC/OOB reasoning for writing whole eraseblocks through
> `/dev/mtdblockN` rather than `nandwrite`, the bad-block handling requirement, the
> backup-before-anything ordering, and the read-only verification steps.
>
> **Offset correction:** the physical map is in the stock U-Boot env `mtdparts`, verified five ways —
> `parameter tags` is at **`0x4800000`**, not `0x200000`, and the active kernel slot is **kernel1**
> (`0x2200000`). See `docs/01` §8; anything below that assumed the listing-order map is superseded.

**Companion files**

| File | Purpose |
|---|---|
| `devtools/zx_mtd_rw.c` | the module that lifts the in-kernel write permission |
| `devtools/Makefile` | out-of-tree build (`KDIR`/`CROSS_COMPILE` overridable, refuses a wrong-vermagic tree) |
| `devtools/build_hint.md` | what a build host needs; why there is no userspace-only route |
| `devtools/zx_reflash.sh` | on-unit BusyBox-ash helper, dry-run by default, `--commit` gate |
| `devtools/recv.cjs` | PC-side file receiver (HTTP PUT/GET, port 8099), already running |

**Ideas reused from the AN758x reference implementation (`stock2ubi/`)**

* `stock2ubi/an758x_mtd_rw.c:24-27,43-55` — pass `get_mtd_device`/`put_mtd_device` addresses
  as module parameters, walk mtd indices, OR the write bit back, keep the parent driver's
  ECC/bad-block path (header comment lines 1-9 say exactly why that is safe).
* `stock2ubi/src/unlock.rs:26-35` — derive the addresses from `/proc/kallsyms` at runtime and
  hand them as `get_mtd_device_addr=0x… put_mtd_device_addr=0x…`.
* `stock2ubi/src/mtd.rs:192-227` — write **whole eraseblocks through `/dev/mtdblockN`**,
  `fsync` the block cache, then read the same range back through `/dev/mtdN` and compare.
* `stock2ubi/src/mtd.rs:181-190` (`MEMGETBADBLOCK` probe) and `:276-326`
  (`find_earliest_good_run`) — never assume a range is bad-block-free.
* `stock2ubi/src/ubi.rs:140-142` + `stock2ubi/src/install.rs:51-67` — eraseblock images built
  from an all-`0xFF` template, written one eraseblock at a time, verified as they go.
* `stock2ubi/src/install.rs:6-10` — `mlockall(MCL_CURRENT)` before overwriting the rootfs the
  writer itself lives in. **Not needed here**: our writer runs from `/tmp` (RAM initramfs) and
  the partitions we touch (`boot`, `kernel0`, `kernel1`) are not the mounted root.

---

## 1. Ground truth used

| Item | Value | Source |
|---|---|---|
| SoC / arch | Sanechips (ZXiC) ZX279133, 2× Cortex-A53, aarch64 | live survey |
| Kernel | `Linux skyworth 4.19.136+ #4 SMP Wed Apr 24 15:34:26 CST 2024 aarch64` | live survey |
| Module vermagic to match | `4.19.136+ SMP mod_unload aarch64` | `.modinfo` of `device-dumps/nf_nat_ftp.ko` (copied from `/kmodule/`) |
| CRC (modversions) checking | **off** — that same `.ko` has no `__versions` section and no `modversions` token | ELF section table of `nf_nat_ftp.ko` (29 sections) |
| Symbols needed | `get_mtd_device` @ `ffffff80083f2f58`, `put_mtd_device` @ `ffffff80083f3840` **this boot** | `/proc/kallsyms` |
| Flash | Unim SPI-NAND 256 MiB, eraseblock 128 KiB (0x20000), page 2048 B, OOB 128 B, `spi1.0`, 15 dynamic partitions | kernel log |
| Partition flags | every `mtdN` reports `flags = 0x400`; `flash_eraseall` / `nandwrite` then fail | live survey |
| Rootfs | `/` is a RAM initramfs, `/tmp` tmpfs (~239 MiB free, volatile) | live survey |
| Persistent RW jffs2 | `/tagparam`=mtd2, `/userconfig`=mtd5, `/wlan`=mtd8, `/important_bak`=mtd4, `/usr/data`=mtd11, `/usr/local/upgrade_module`=mtd9 | live survey |
| Bootloader | U-Boot 2021.01-svn358069 (Dec 01 2025 20:05:38 +0800), vendor `zxboot` | string at `0x92968` of `device-dumps/sk-d840n_b1.bin` |
| PC ↔ unit | PC `192.168.1.2:8099` (`recv.cjs`), unit `192.168.1.1`; `curl -T` up, `curl -o` down | live survey |

## 2. The mechanism (and the one thing that must be settled empirically)

The MTD core gates every write path — `mtd_write()`, `mtd_erase()`, the `MEMERASE`/
`MEMWRITE*` ioctls, and `mtdblock`'s flush — on a per-device flag bit. The stock kernel
registers these partitions without it, so all user tools fail **before** the SPI-NAND driver
is ever asked to program anything.

Which bit, exactly, is not something we can assert from this machine:

* mainline `include/uapi/mtd/mtd-abi.h` defines `MTD_WRITEABLE = 0x400` (that header is copied
  verbatim into `uboot-an758x/include/mtd/mtd-abi.h:93-102`);
* yet this kernel reports `0x400` on partitions that *refuse* writes, and at the same time
  several of those partitions are mounted read-write as jffs2.

So this BSP uses a vendor flag split (`MTD_PROGRAM` / `MTD_ERASEABLE` / `MTD_WRITEABLE`) whose
values only exist in the vendor header. The module therefore:

* ORs `MTD_WRITEABLE` (+ `MTD_ERASEABLE` **only if** the header defines it) — never
  `MTD_BIT_WRITEABLE`, never clearing `MTD_NO_ERASE` (that would let tools program pages that
  were never erased and would produce ECC garbage);
* prints, per partition, `flags` **before and after**, the compiled value of `MTD_WRITEABLE`,
  and whether `mtd->_write` / `mtd->_erase` / `mtd->_block_isbad` are non-NULL;
* accepts `set_flags=<hex>` so a wrong guess is fixed with a module parameter, not a rebuild.

**Acceptance test (no flash access):** after `insmod … commit=1`,
`cat /sys/class/mtd/mtd3/flags` (or `mtd_debug info /dev/mtd3`) must show a word that differs
from `0x400` by exactly the value the module printed as its `MTD_WRITEABLE`. If the word does
not change, or changes in a way that disagrees with the module's printout, stop: the struct
layout or the flag header is wrong (`build_hint.md`, "What stays unknown").

## 3. **The flash map — settled offline, but verify it live before writing**

`/dev/mtdblockN` writes are *partition-relative*, so the write path itself does not care about
offsets. Every **absolute-address** instruction (U-Boot `nand erase/write`, the community
README, `dd of=/dev/mtdblock0 seek=…`) does, and the offsets recorded in the device survey
("cumulative offsets: parameter tags 0x200000, kernel0 0x600000, …") turn out not to be the
real ones: they are the partition *sizes* summed in `/proc/mtd` listing order, which is
registration order (DT node order), not flash order.

H_A = that size-cumulation hypothesis. H_B = the map below, which three independent sources
agree on:

1. **The stock U-Boot environment**, read out of the existing backup
   `device-dumps/sk-d840n_b1.bin` at offset `0x180000` (env size 128 KiB, `crc32` over the
   128 KiB area after the 4-byte little-endian CRC field: stored `5c 12 38 c6` =
   `crc32=0xc638125c` — **verified against our own dump**, so the env block is intact and the
   backup is trustworthy):

```
mtdparts=spi-nand0:262144k@0x0(whole),2048k@0x0(boot),4096k@0x4800000(parameter tags),
32768k@0x200000(kernel0),4096k@0x5400000(middleware),8192k@0x4c00000(usercfg),
32768k@0x2200000(kernel1),6144k@0x4200000(others),4096k@0x5800000(wlan),
2048k@0x5c00000(phoneapp0),30720k@0x6000000(osgi0),98304k@0x9c00000(plugin_data),
30720k@0x7e00000(osgi1),2048k@0x5e00000(phoneapp1),4096k@0xfc00000(awifi)
```

| /proc/mtd | name | size (B) | H_A offset | H_B offset |
|---|---|---|---|---|
| mtd0 | whole | 268435456 | 0x0 | 0x0 |
| mtd1 | boot | 2097152 | 0x0 | 0x0 |
| mtd2 | parameter tags | 4194304 | 0x200000 | **0x4800000** |
| mtd3 | kernel0 | 33554432 | 0x600000 | **0x200000** |
| mtd4 | middleware | 4194304 | 0x2600000 | **0x5400000** |
| mtd5 | usercfg | 8388608 | 0x2A00000 | **0x4C00000** |
| mtd6 | kernel1 | 33554432 | 0x3200000 | **0x2200000** |
| mtd7 | others | 6291456 | 0x5200000 | 0x4200000 |
| mtd8 | wlan | 2097152 | 0x5800000 | 0x5800000 |
| mtd9 | phoneapp0 | 2097152 | 0x5A00000 | 0x5C00000 |
| mtd10 | osgi0 | 31457280 | 0x5C00000 | 0x6000000 |
| mtd11 | plugin_data | 100663296 | 0x7A00000 | 0x9C00000 |
| mtd12 | osgi1 | 31457280 | 0xDA00000 | 0x7E00000 |
| mtd13 | phoneapp1 | 2097152 | 0xF800000 | 0x5E00000 |
| mtd14 | awifi | 4194304 | 0xFA00000 | **0xFC00000** |

Evidence favouring H_B, all from files already on disk:

2. **The `dynamic-partitions` node in the bootloader's own DTB** — the same `mtd1` backup
   contains U-Boot's flattened device tree, whose `partitions { compatible =
   "dynamic-partitions"; … }` block carries one child per partition with `reg = <offset size>`.
   Decoded by the parallel analysis in this directory (`devtools/uboot-stock.dts.txt:2044-2101`,
   produced from `devtools/uboot-stock.dtb`): `param 0x4800000/0x400000`,
   `kern0 0x200000/0x2000000`, `kern1 0x2200000/0x2000000`, `usercfg 0x4c00000/0x800000`,
   `middleware 0x5400000/0x400000`, `others 0x4200000/0x600000`, `wlan 0x5800000/0x400000`,
   `phoneapp0 0x5c00000/0x200000`, `phoneapp1 0x5e00000/0x200000`, `osgi0 0x6000000/0x1e00000`,
   `osgi1 0x7e00000/0x1e00000`, `plugin_data 0x9c00000/0x6000000`, `awifi 0xfc00000/0x400000` —
   byte-for-byte the env table above, and this parser is literally what the kernel log names
   ("dynamic partitions parse", "15 dynamic-partitions partitions found on MTD device spi1.0").
   The DTB also holds a **second** table for 128 MiB flash parts (`part@128`, different offsets
   and even different labels, e.g. `GN25L95_datas`), selected at run time by detected flash
   size — which is exactly the kind of table a naive size-cumulation would confuse.
3. H_B tiles `0x0 … 0x10000000` **exactly**; H_A leaves a 2 MiB hole and has to invent a gap the
   vendor would not normally leave.
4. The bootargs stored in that same env block read
   `… U-Boot 2.0.0 20251201200927 0x2200000 0x1 0x83 0x83  UpModeInfo=0x00000040  regioncode=0x0`
   → this unit was booted from **0x2200000 = kernel1, slot 1** in H_B.
5. The kernel image we dumped from `mtd3` starts with a vendor header whose little-endian u32 at
   `0x78` is `0x2200000` (`device-dumps/sk-d840n_k0.bin`, see Appendix B) — the sibling-slot
   address of H_B, embedded in the image itself.
6. Partition **sizes** agree in both hypotheses; only the offsets differ, which is what you get
   when the listing order is DT-node order (Linux) rather than address order.

None of the above was read from the live unit, so treat H_B as *the working map* and confirm it
before any absolute-address command: `sh devtools/zx_reflash.sh verifymap` dumps 16 bytes at
each candidate offset from `/dev/mtd0` and looks for the kernel preamble
`99 99 99 99 44 44 44 44 55 55 55 55 aa aa aa aa` and for the jffs2 node magic `85 19`; and/or
`ls -l /proc/device-tree/*/partitions/*/` + `xxd` of each `reg` (pure reads).

**Consequence for the plan:** kernel0 (`mtd3`, 0x200000) is the *inactive* slot on this unit —
the bootargs say the running kernel came from 0x2200000 = kernel1. Writing kernel0 therefore
does not change what boots, which makes it the correct first target; it also means "reflashed
kernel0 and nothing happened" is the *expected* result, not a failure.

---

## 4. (a) Mandatory verification reads — before anything else

All read-only. Run them, paste the output into the ticket, and only then continue.
`sh devtools/zx_reflash.sh probe` performs 1-11; the numbered commands are also listed inline
so they can be typed by hand.

1. **Kernel identity** — `cat /proc/version; uname -a`
   Expect `4.19.136+`. The module's vermagic must match the *string*, not the number.
2. **Bootloader state (decisive for §9)** — `cat /proc/cmdline`
   Look for: `skysec=1` (the string exists in the bootloader at `0xa428d`); a `root=` (H_B's
   env has `rdinit=/sbin/init` and **no `root=`**, consistent with the RAM initramfs); the
   `0x…  0x1` slot stamp; `UpModeInfo=0x…`; `regioncode=0x…`.
   **If `skysec=1` is present, secure/verified boot is live: stop and do not write any
   non-stock image.**
3. **Live partition table** — `cat /proc/mtd` and `ls /sys/class/mtd/mtd1/`
   The second command tells us whether `offset` even exists as an attribute on this 4.19
   (`stock2ubi/src/mtd.rs:72-78` reads it, but on the AN758x's 5.4; mainline only grew
   `mtd->offset`/the sysfs file later). If absent, offsets come from `verifymap` + the DT.
4. **Flags** — `for i in 0 1 2 3 4 5 6 7 8 9 10 11 12 13 14; do echo "mtd$i $(cat /sys/class/mtd/mtd$i/flags)"; done`
   Cross-check two of them with `mtd_debug info /dev/mtd1` and `mtd_debug info /dev/mtd3`.
   Record this table: it is the *before* half of the §2 acceptance test.
5. **Device nodes and majors** — `ls -l /dev/mtd* ; cat /proc/devices`
   We need `90 mtd` **and** `31 mtdblock`, plus `/dev/mtdblockN` nodes. If the block nodes are
   missing, the write path in §7 is unavailable and we must fall back to `mtd_debug write` —
   check `mknod` availability at the same time (not in the confirmed applet list).
6. **kallsyms** — `grep -w -E 'get_mtd_device|put_mtd_device' /proc/kallsyms`
   Must give two non-zero addresses (reject `0000000000000000`, which would mean
   `kptr_restrict` hid them — same check as `stock2ubi/src/unlock.rs:19-22`). Re-read on every
   boot; these are per-build values.
7. **Spare RAM** — `df -h /tmp; free` — 256 MiB will not fit; this constrains §5.
8. **Applet inventory** — the loop in `zx_reflash.sh probe` (`dd od xxd grep curl nanddump
   nandwrite flash_eraseall mtd_debug insmod rmmod modprobe cmp cksum sha256sum seq mknod
   dmesg`). Two answers matter a lot: `sha256sum` (§8) and `cmp`/`cksum` (the per-block
   readback in §7 — the script **refuses to write** if neither exists).
9. **vermagic cross-check** — `grep -a -o 'vermagic=[-+._ A-Za-z0-9]*' /kmodule/*.ko`
   All 12 vendor modules should say `4.19.136+ SMP mod_unload aarch64`. Also skim the list for
   anything named like an upgrade/writable-enabler module before assuming we need ours.
10. **Map arbitration** — `sh devtools/zx_reflash.sh verifymap` (§3).
11. **Backup integrity of the evidence we already hold** — re-read the first eraseblock of
    `mtd1` and `mtd3` and compare against `device-dumps/sk-d840n_b1.bin` /
    `device-dumps/sk-d840n_k0.bin` on the PC (`dd if=/dev/mtd3 bs=131072 count=1 | curl -T -`
    is *not* reliable with BusyBox curl; write a temp file instead). A mismatch here means
    flash changed under us or the dump method differs — resolve before writing.
12. **Optional functional test of the write path (only after §5 is complete and §2 passed)** —
    erase + write + readback **one eraseblock at the very end of the inactive slot**
    (`mtd3`, i.e. `dd` a 128 KiB all-`0xFF`-except-first-block image with
    `seek=<last block>`). Loud, reversible (we hold the bytes), and it proves `mtd_write`
    really got past the flag check without touching anything that boots. Skip it if you have
    not finished §5.

## 5. (b) Full backup of all partitions to the PC

PC side, already running (or start it):

```bat
node devtools\recv.cjs device-dumps 8099
```

Unit side (reads only; `--commit` is required by the helper only so that one rule covers every
command it can issue):

```sh
sh devtools/zx_reflash.sh backup            # prints the plan
sh devtools/zx_reflash.sh backup --commit   # dd + curl -T + rm, one partition at a time
```

Which is, per `mtdN` (N = 1…14):

```sh
dd if=/dev/mtdN of=/tmp/mtdN.bin bs=131072            # exact partition size, 128 KiB blocks
curl -T /tmp/mtdN.bin http://192.168.1.2:8099/sk-d840n_mtd<N>_pre.bin
rm -f /tmp/mtdN.bin                                    # /tmp is RAM: never keep two at once
```

Sizes to expect (from the table in §3; total **266,338,304 B = 254 MiB** for mtd1…mtd14):
`2097152, 4194304, 33554432, 4194304, 8388608, 33554432, 6291456, 2097152, 2097152, 31457280,
100663296, 31457280, 2097152, 4194304`. `recv.cjs` prints `wrote <name> <n> bytes` for each —
**that number is the first integrity check**; it must equal the table above exactly.

Notes:

* With the working map H_B the 14 partitions tile the flash, so mtd1…mtd14 *is* a complete
  backup. If `verifymap` instead proves H_A, there is a 2 MiB tail nobody covers → additionally
  dump `mtd0` in four 64 MiB chunks
  (`dd if=/dev/mtd0 of=/tmp/cN.bin bs=131072 skip=$((N*512)) count=512`, N = 0…3, each
  67,108,864 B). Do not try to stage 256 MiB in `/tmp`.
* Order: firmware slots first (`mtd1`, `mtd3`, `mtd6`), state partitions **last**
  (`mtd2`, `mtd5`, `mtd8`, `mtd11`) — they are mounted RW and jffs2 GC moves data while you
  read. `sync` immediately before those four dumps.
* `dd` is the primary method because its output length is exactly the partition size and it
  keeps bad-block positions in place. If `dd` aborts on a page whose ECC is uncorrectable
  (`-EBADMSG`), dump that eraseblock with `nanddump` instead — and check its flags with
  `nanddump --help` first: BusyBox `nanddump` needs the *pad* option (not the *skip-bad* one)
  to keep the dump aligned; a skipping dump silently shifts everything after the bad block.
* Already on the PC and reusable: `device-dumps/sk-d840n_b1.bin` (mtd1, full 2 MiB),
  `device-dumps/sk-d840n_mtd2.bin` (mtd2, full 4 MiB), `device-dumps/sk-d840n_k0.bin`
  (mtd3, **first 2 MiB only** — re-dump it in full).

**Time estimate.** 256 MiB = 131,072 pages of 2 KiB = 2048 eraseblocks. The read is
page-serialised through the `spi-nand` driver, so it is bounded by `tR` (≈ 0.2–0.4 ms/page for
SPI-NAND with on-die ECC) plus SPI transfer: quad-output read at ~100 MHz ≈ 25–45 MB/s
realistic, single-line read ≈ 3–7 MB/s. Expect the full 254 MiB in **roughly 15–90 s of flash
read**, plus 10–30 s of HTTP PUT over 1 GbE (partially overlapping), so budget **2–5 minutes**
wall-clock, and re-plan if the observed rate is below 1 MB/s. Measure instead of guessing:

```sh
time dd if=/dev/mtd0 of=/dev/null bs=131072 count=64      # 8 MiB
# seconds × 32 ≈ full-flash read time; the unit's `time` may be an ash keyword — if `time`
# is absent use:  t0=$(cut -d. -f1 /proc/uptime); dd … ; t1=$(cut -d. -f1 /proc/uptime)
```

**Flash-life cost of the backup: zero erase cycles.** Reads do not consume P/E endurance
(typical SPI-NAND SLC-mode spec is 100,000 erase cycles per block); the residual read mechanism
of concern is *read disturb*, which needs far more than a handful of passes and is corrected
by the on-die ECC in every read here. The plan's writes cost **≤ 1–3 erase cycles** on the
touched blocks (boot = 16 blocks, a kernel slot = 256 blocks), i.e. ≲ 0.003 % of life.

Record hashes **on the PC** (the unit's `sha256sum` is unconfirmed, §8):

```bat
certutil -hashfile device-dumps\sk-d840n_mtd3_pre.bin SHA256
```
```sh
node -e "const c=require('crypto'),f=require('fs');for(const p of process.argv.slice(1))\
 console.log(c.createHash('sha256').update(f.readFileSync(p)).digest('hex'),p)" \
 device-dumps/sk-d840n_mtd*_pre.bin device-dumps/sk-d840n_b1.bin
```

## 6. (c) Load the module

Build elsewhere (see `devtools/build_hint.md`), verify `modinfo -F vermagic` says
`4.19.136+ SMP mod_unload aarch64`, hash it, then hand it to the unit:

```sh
curl -o /tmp/zx_mtd_rw.ko http://192.168.1.2:8099/zx_mtd_rw.ko
od -An -tx1 -N8 /tmp/zx_mtd_rw.ko        # must start 7f 45 4c 46 02 01 01 00 (ELF64 LE)
```

**Pass 1 — dry run (default; changes nothing).** The module prints the whole geometry table
and `would go 0x… -> 0x…`, so this pass doubles as verification step §4.4/§4.6:

```sh
G=$(grep -w get_mtd_device /proc/kallsyms | cut -d' ' -f1); P=$(grep -w put_mtd_device /proc/kallsyms | cut -d' ' -f1); insmod /tmp/zx_mtd_rw.ko get_mtd_device_addr=0x$G put_mtd_device_addr=0x$P
```

Read it back (`dmesg` if present; otherwise `/tmp` is not where it goes — the kernel log is
only reachable through `dmesg`, so if that applet is missing the dry run tells you nothing and
you must use §4.4 instead):

```sh
dmesg | grep zx_mtd_rw
```

Check: `compiled MTD_WRITEABLE=…`, per-partition `flags=0x400 … write=yes erase=yes isbad=yes`,
and `N MTD device(s) would change`. If `write=no`/`erase=no` appears anywhere, the parent
driver cannot program it at all and this route is dead — no flag flip fixes a NULL method.

**Pass 2 — commit.** Parameters are `0400` and are read at init, so the commit run means
`rmmod` + `insmod` again, not a sysfs write:

```sh
G=$(grep -w get_mtd_device /proc/kallsyms | cut -d' ' -f1); P=$(grep -w put_mtd_device /proc/kallsyms | cut -d' ' -f1); rmmod zx_mtd_rw; insmod /tmp/zx_mtd_rw.ko get_mtd_device_addr=0x$G put_mtd_device_addr=0x$P commit=1 targets=3
```

* Restrict with `targets=` to the indices you actually intend to write (`3` = kernel0,
  `6` = kernel1, `1` = boot). `targets` defaults to all 15 only because the reference walks all
  indices (`stock2ubi/an758x_mtd_rw.c:43`); on this unit the wide default would also unlock
  the partitions that carry live jffs2 data.
* `rmmod` was not in the confirmed applet list — if it is missing use `modprobe -r zx_mtd_rw`.
* Apply the §2 acceptance test now (`cat /sys/class/mtd/mtd3/flags`).
* Keep the module loaded only while working; `zx_mtd_rw`'s exit restores the original words, and
  unloading **while a jffs2 mount is writing** makes that write fail with `EROFS`. Do not leave
  it loaded across normal use, and remember it vanishes on reboot anyway (`/` and `/tmp` are RAM).

## 7. (d) The write path (still: not executed, gated behind `--commit`)

Target of the first real write: **`mtd3` = kernel0 = the inactive slot** (§3). Order of
operations is fixed:

```
§5 backup of that partition verified on the PC  ->  §6 pass 2  ->  bad-block scan  ->
erase  ->  write in CHUNK-sized dd passes  ->  per-eraseblock readback  ->  §8 hash compare  ->
reboot  (only after the hashes match)
```

`sh devtools/zx_reflash.sh write 3 /tmp/kernel0.img` prints this plan and stops; `--commit`
runs it. The equivalent by hand, using **only applets that exist on the unit**
(`flash_eraseall`, `nandwrite`, `mtd_debug`, `dd`, `od`, `xxd`, `grep`, `curl`):

```sh
# 1. image onto the unit (96 MiB max in /tmp; a 32 MiB slot is fine)
curl -o /tmp/kernel0.img http://192.168.1.2:8099/<file>
wc -c < /tmp/kernel0.img                  # must be <= 33554432

# 2. bad blocks in the target range, read-only
nanddump -f /dev/null /dev/mtd3           # report of bad/uncorrectable blocks (confirm
flash_eraseall --help                     #  exact option names on the unit first!)

# 3. erase (busybox flash_eraseall, no separate flash_erase applet)
flash_eraseall /dev/mtd3

# 4. write whole eraseblocks through the block device: 1 MiB per dd pass = 8 blocks,
#    which is what keeps mtdblock on its erase+write path (it uses the
#    read-modify-write cache for requests of <= 2 erasesize)
i=0; while [ $i -lt 32 ]; do
  dd if=/tmp/kernel0.img of=/dev/mtdblock3 bs=1048576 skip=$i seek=$i conv=fsync || break
  i=$((i+1)); done; sync

# 5. per-eraseblock readback compare (see zx_reflash.sh verify / blockcmp)
```

Why `/dev/mtdblockN` and not `nandwrite` (this is the reference's choice too,
`stock2ubi/src/install.rs:51-67` + `src/mtd.rs:192-227`):

* **OOB / ECC.** OOB is 128 B and holds the vendor's bad-block markers, ECC, and (in the first
  blocks) metadata the BootROM/U-Boot reads. The `mtdblock` → `mtd_write` path writes in-band
  data only and lets `nand_base`/`spi_nand` place the ECC itself, so the pages come out
  indistinguishable from factory ones. `nandwrite` is built around the `MEMWRITEOOB`/auto-OOB
  ioctls and its own block arithmetic; a wrong `--autoplace`/OOB-layout assumption writes into
  spare bytes and can destroy a BBT marker — the block then reads as good to Linux and as bad
  (or as garbage) to U-Boot.
* **Erasure.** `mtdblock` erases as part of a full-eraseblock write; `mtd_debug write` and
  `nandwrite` do not erase (hence `flash_eraseall` first), and programming a non-erased page
  yields bit-ANDed garbage that still *reads back* with "corrected" ECC errors.
* **Bad blocks.** `mtd_block_write()` **skips** a bad block silently and drops that block's
  data (offsets stay correct, content does not). That is precisely why per-block readback is
  mandatory here and why the reference verifies every block it writes
  (`stock2ubi/src/mtd.rs:213-225`). It is also why the alternative — a whole-file hash only —
  is enough to *detect* but too late to *locate*; the per-block loop names the offset.
* `mtd_debug write /dev/mtd3 <offset> <len> <file>` is the fallback writer if `/dev/mtdblock3`
  turns out to be missing (§4.5). It fails loudly on a bad block instead of skipping, at the
  cost of needing an explicit erase and one call per eraseblock.

**Bad block inside the target range → abort, do not write from Linux.** The AN758x tool can
relocate (it searches for the earliest run of consecutive good blocks,
`stock2ubi/src/mtd.rs:276-326`, and builds the image from all-`0xFF` block templates,
`src/ubi.rs:140-142`, because it defines the layout and points the bootloader at the new run).
A stock slot cannot be relocated: `zxboot` looks the slot up by address
(`find kernel%d flash entry is 0x%x`, `header crc failed`, `failed to boot kernel` — all in
`device-dumps/sk-d840n_b1.bin`) and reads it linearly, so a hole in the middle is a dead
kernel. Options, in order of preference: (1) write the *other* slot, if its blocks are good;
(2) accept the bad block only if it sits in the unused tail past the real image length
(`0x1e0 + FIT totalsize`, see Appendix B) — verify with the same readback logic; (3) TTL
`nand write`, where U-Boot does its own bad-block handling. Do **not** `block_markbad`/
`flash_eraseall -j` your way around it: the BBT is vendor-owned.

**Never** write `mtd1` (boot) from Linux on a first attempt: see §8.

## 8. (e) Readback and hash comparison

```sh
# unit: dump the partition you just wrote and hand it to the PC
sync; dd if=/dev/mtd3 of=/tmp/mtd3.rb.bin bs=131072
curl -T /tmp/mtd3.rb.bin http://192.168.1.2:8099/sk-d840n_mtd3_rb.bin; rm /tmp/mtd3.rb.bin
```

```bat
:: PC: authoritative compare — the source image and the readback are both local here
certutil -hashfile device-dumps\sk-d840n_mtd3_rb.bin SHA256
certutil -hashfile <the image you wrote> SHA256
fc /b device-dumps\sk-d840n_mtd3_rb.bin <image>
```

* **`sha256sum` on the unit: unknown.** It was not in the reported applet list, but that list
  covers only the tools relevant to this plan, and BusyBox builds usually include it. Check
  with `command -v sha256sum` / `busybox --list | grep -i sha` in §4.8. The design above does
  not depend on it: the comparison happens on the PC, which holds both byte streams. If the
  unit has `md5sum`/`crc32`/`cksum`, an in-place hash is a useful *pre-filter* only — a jffs2
  or ECC-corrected read can hide a marginal cell, so the PC-side byte compare is the one that
  counts.
* Length must match the source (`wc -c`, and `recv.cjs`'s `wrote <name> <n> bytes` line) before
  you bother hashing.
* Only after a byte-identical readback: `sync; reboot`. Anything else → §9.

## 9. (f) Rollback

**Interrupted write (power loss, `curl` stall, kernel panic mid-write) — the unit is still
running and nothing is lost yet:** the kernel and rootfs you are typing on live in RAM, so an
aborted flash write does not affect the current session. Do **not** reboot. Re-run
`flash_eraseall` + the write loop for that partition from the §5 backup, verify per block, then
reboot. Partially-written *cells* are not recoverable by any means other than a rewrite, and
that is exactly what the retry does.

**Rebooted into a broken slot:**

* `mtd3`/`mtd6` (kernel slots): the other slot still holds the untouched stock kernel, and the
  bootloader iterates entries and reports `header crc failed` / `failed to boot kernel`, which
  implies it tries the next one (**plausible, not verified** — §10). Power-cycle 2-3× and watch
  for the ONU to come back on `192.168.1.1`. If it does not: TTL console, then restore from the
  backup with the community recipe (`sk-d840n-openwrt/README.md` §Firmware flashing: enter `u`
  at boot to stay in U-Boot, `tftpboot 0x88000000 192.168.1.40:<file>`, `nand erase <abs> <len>`,
  `nand write <addr> <abs> ${filesize}`) — **using the H_B absolute addresses from §3, not the
  README's own addresses**, which belong to a re-flashed community layout.
* `mtd1` (boot): **this is the one that cannot be recovered without the case open.** The env
  lives *inside* `mtd1` at `0x180000`, one eraseblock, single copy (blocks at `0x1A0000`,
  `0x1C0000`, `0x1E0000` in our dump are all `0xFF`, so there is no redundant env), and the
  bootloader code is blocks 0-6. An interrupted `mtd1` write therefore destroys bootloader
  *and* `ethaddr`/`bootargs`/`mtdparts`/`UpModeInfo` together. Recovery = TTL/xmodem
  (`REC: pls x-Modem send uboot.bin` exists in the bootrom part of `mtd1`) or ISP, restoring
  `device-dumps/sk-d840n_b1.bin` byte-for-byte. Hence: **boot is the last thing you write, or
  you do not write it from Linux at all.** The promise of "no disassembly" holds for kernel
  slots; for `mtd1` it holds only as long as the write does not fail.
* Restoring `mtd1` from **our own** backup is byte-identical and preserves the env (its CRC
  validates, §3). Restoring it from the community `binary/boot.bin` would install *their* env —
  different `mtdparts`, different `bootcmd` (`mtd read kernel 0x88000000; mtd read dtb …; bootm`),
  different bootargs — i.e. their unit's map and slot stamp on our flash. Their `ethaddr` happens
  to equal ours (`00:29:47:00:00:32`, so the MAC is not unit-unique in this fleet), but that is
  not a licence to import their env: after it, the stock kernel is not findable by their
  `bootcmd` and vice versa.
* `mtd2`, `mtd5`, `mtd8`, `mtd11` are jffs2: they survive power loss structurally (log-
  structured, GC replays), so an interrupted write costs data, not the mount. Restore by
  `flash_eraseall` + `mtdblock` write of the dump — and `umount` the partition first
  (`umount /tagparam` etc.), which for `/` -adjacent mounts may not be possible on a live
  system; a jffs2 partition must not be rewritten while mounted RW.

**Why the `mtd2` (parameter tags) backup is the single most important artifact.** Everything
else in the flash is reproducible from a firmware package; `mtd2` is not. It is the `/tagparam`
jffs2 store, and in the copy we already hold
(`device-dumps/sk-d840n_mtd2.bin`, 4 MiB) the data occupies **one eraseblock** —
`0x140000`, 38,865 non-`0xFF` bytes — with jffs2 node names **`paramtag`, `flag_type`,
`osgi_set`** and payload lines like `current : 324`, `current : 310`, `current : 0`,
`partion:1`. That is this unit's *state*, written by the vendor stack, and the bootloader has
matching accessors — `up_mode_get`, `up_mode_set`, `up_mode_check_verify`,
`modify_bootargs_UpModeInfo`, `uboot_default_up_mode_info`, `get_dts_bosa_and_switch_ponmode_info`
(all in `mtd1`) — i.e. the boot/up-mode selection is stored in exactly this kind of tag. Lose
`mtd2` and you may lose the slot/up-mode/region state that no OTA image restores, even though
the partition holds only a few kilobytes. Cheap to protect: it is a 4 MiB read, 38 KiB of which
matters, and we already have two generations of it. Re-take it immediately before any write
that could touch it, and treat `partion:1`/`current : N` as the ground truth for which slot the
vendor stack thinks is active (compare against §3's `0x2200000 0x1`).

**Free safety net of this device:** `/` is a RAM initramfs. Anything we install (`/tmp/*.ko`,
`/tmp/*.sh`, `/kmodule/` copies) disappears on reboot, and a reboot restores the stock rootfs.
Nothing in this plan needs to be un-installed, and a misbehaving module is one power cycle away.

## 10. (g) Two failure modes we cannot rule out

**1. Vendor signature / AES expectations on boot.** Evidence from `device-dumps/sk-d840n_b1.bin`
(offsets are within the 2 MiB `mtd1` dump):

* `mtd1` starts with a signed-image header: `00 02 00 14 … "0001ZXIC" … "BOOTROM" … "RSA2048"`
  at `0x80-0xb0`, followed by ARM64 code in blocks 0-6 (the file's overall entropy is
  3.27 bits/byte, with 1.16 MB of `0xFF` filler — not the profile of a ciphertext blob).
* Boot/U-Boot strings: `verify ok !!!`, `non secure boot`, `Failed to detect code hash !!`,
  `Successful detection code hash !!`, `header crc failed`, `chip id err! id =0x%x should be 0x%x`,
  `backup header!!`, `SEC_BOOT_PROTECT:`, `AES_KEY0..3`, `Backup_AES_KEY0..3`, `AES KEY VAILD`,
  `Safety boot enable/disable`, `set aes key`, `submpasswd`, `swsfmd`, and the prompt the
  operator saw: `Backup aes key for which image ? (uboot/kernel)` (`0xa2d51`).
* A vendor upgrade path exists **inside U-Boot**: `downver`, `upgrade images for chip`,
  `uboot.bin`, `update uboot,filesize:%u`, `update kernel0 failed!`, `update kernel1 failed!`,
  `sky_check_image_only_upgrade_boot not include uboot!!!!`, plus `Only aes operations: …MBps`
  and `Aes and hash operations: …MBps` — i.e. the *package* is AES+hash processed on the way in.
* Counter-evidence that keeps the kernel slots writable-by-us plausible: the on-flash kernel
  content is **not encrypted**. `device-dumps/sk-d840n_k0.bin` = vendor header at `0x0`
  (`99999999 44444444 55555555 aaaaaaaa`, `"V2.0.0"`, `"ZXIC 133 UNI V2.0.0 286632 CM OSGI"`,
  build stamp `20251201200927`, `"CMNV1.0.024"`), then a **plaintext U-Boot FIT at `0x1e0`**
  (`d00dfeed`, `totalsize = 0x1299fdf` ≈ 18.6 MiB) whose structure names `images`,
  `kernel@133`, `Linux kernel for 133`, with a gzip member at `0x2d4`. Its high entropy is the
  compressed kernel, not encryption.

What remains unknown, and therefore a risk: whether this exact unit is fused/unlocked such
that the RSA2048 + code-hash path is enforced (`skysec=1`, §4.2), and what produces a valid
vendor header CRC — we tested the obvious candidates and none of them matches the header field
at `0x60` (`0x92efdacb` vs `crc32(FIT) = c761ff9d`), so **we cannot currently manufacture a
header for a foreign kernel image**. Practical consequence: *restoring stock bytes is safe;
installing third-party firmware into a stock slot is a different project* — the community image
only works because its `boot.bin` also replaces the env (their `bootcmd` reads a plain uImage
from a re-mapped partition table; Appendix A). That env replacement is precisely what makes
their recipe unsafe to copy onto this unit.

**2. `UpModeInfo` / bootargs coupling.** The env we hold contains
`ext_args=UpModeInfo=0x00000040`, `regioncode=0x0`, and a bootargs string that has been written
back into the env block *with the boot-time kernel address and slot baked in*
(`0x2200000 0x1`). U-Boot's own symbol/string set shows the machinery that reads, sets,
verifies and rewrites it: `up_mode_set`, `up_mode_get`, `up_mode_check_verify`,
`modify_bootargs_UpModeInfo`, `get_dts_neglect_bootargs_up_mode_info`,
`uboot_default_up_mode_info` (read from the **device tree**, per
`[%s] dts uboot_default_up_mode_info = 0x%x`), `[%s] check UpModeInfo error! UpModeInfo=0x%x
check_UpModeInfo=0x%x`, `[%s] up_mode_get error! ret=%d use default config`,
`[%s] ponmode err! ponmode=0x%x set to XGPON`, `[%s] gpon gpio select` / `xgpon gpio select`,
`[%s] UpModeInfo=0x%x cur_up_mode=0x%x up_mode_auto_en=%d lan_up_port=%d wlan_up_port=%d`.
Consequences for a reflash:

* `UpModeInfo` selects the PON mode and the upstream port (GPON vs XGPON, which LAN port, auto
  select). It is *not* derivable from the firmware image; it comes from the DTS default plus
  the tag store (§9) and, when the tags fail, `use default config`. Wipe `mtd1`'s env or `mtd2`
  and the unit can boot a perfectly good kernel and still be useless on your OLT.
* The bootargs embed the loaded-kernel **address and slot**, so the flash map of §3 is *also*
  encoded in a writeable variable. A kernel written at an address the env does not name will
  simply never be tried; conversely an env that names an address where nothing valid sits gives
  `failed to boot kernel`. Any change to the map must therefore be made in **`mtd1`'s env**, in
  the same operation as the kernel write — which is exactly what "reflash boot from Linux"
  means, and why §9 says to leave `mtd1` for last.
* Unknown: whether the vendor stack also rewrites these tags from userspace during an OTA
  (`fw_flashing`, and the `/usr/local/upgrade_module`, `/important_bak` mounts suggest it
  does). If it does, a restore of `mtd2` from an old dump could make the next auto-upgrade
  "succeed" into the wrong slot.
* `fw_flashing` — **no file in this checkout mentions it** (`grep -ril fw_flashing .` returns
  nothing). It is the most likely vendor-intended upgrade entry point, and the cheapest next
  read-only step is to pull it to the PC through the 8099 receiver
  (`ls -l $(command -v fw_flashing)`, then `curl -T <path> http://192.168.1.2:8099/fw_flashing`)
  and analyse it there — the unit has no `strings`, but `xxd`/`od` do exist. Its package format
  is what U-Boot's `downver` expects, so a working `fw_flashing` invocation could replace the
  whole module+dd approach.

## 11. Honest list of what is still unknown

| Unknown | Why it matters | How to settle it (all read-only) |
|---|---|---|
| Which map is real (H_B strongly indicated offline, §3) | every absolute-address instruction | §4.10 `verifymap`; `ls /sys/class/mtd/mtdN/offset`; `xxd /proc/device-tree/*/partitions/*/reg` |
| Real value/meaning of the `0x400` flag word on this BSP | whether one bit flip is enough | module's before/after print + `mtd_debug info` (§2) |
| `sha256sum` / `cmp` / `cksum` on the unit | per-block verify and in-place hashing | §4.8 applet inventory |
| Exact BusyBox `nanddump` / `nandwrite` / `flash_eraseall` option letters (they differ from mtd-utils) | aligned padded dumps, bad-block behaviour | `<applet> --help` on the unit |
| Whether `/dev/mtdblockN` exists and `mtdblock` is built in | the chosen write path | §4.5 |
| Whether `insmod -f` works (`CONFIG_MODULE_FORCE_LOAD`) | escape hatch if vermagic is 99 % right | test only with a deliberately harmless module |
| Whether U-Boot falls back to the other kernel slot on a failed boot | rollback story for a bad write | only observable over TTL; assume **no** and keep the case-opening plan ready |
| Purpose of `mtd1` blocks 7-11 (all `0xFF`) and the partly-used block 6 | spare/bootloader backup copies? | compare with a second unit's `mtd1`, or read `zxboot`'s `backup header!!` path on TTL |
| The vendor header CRC algorithm | repacking a foreign kernel | offline reverse-engineering from `sk-d840n_k0.bin` + more samples |
| `fw_flashing` and its package format | may make this whole plan unnecessary | ship it to the PC (§10.2) |

**Bottom line.** The permission problem is solvable in Linux with a ~150-line module and no
vendor code (that is what `stock2ubi` proves on the AN758x). The *content* problem is separate:
as long as we cannot generate a valid vendor image header, and as long as `mtd1`'s env is the
single copy of the map/bootargs/up-mode state, the safe scope of this plan is
**"restore the stock bytes, and stage a known-good kernel image into the inactive slot"** —
with a full, PC-verified backup, per-eraseblock readback, `boot` left alone, and TTL as the
documented last resort.

---

## Appendix A — local evidence index

| File | What it proves |
|---|---|
| `device-dumps/nf_nat_ftp.ko` | vermagic `4.19.136+ SMP mod_unload aarch64`; ELF64/EM_AARCH64; **no `__versions`** → `CONFIG_MODVERSIONS=n`; `intree=Y`, `depends=nf_conntrack_ftp` |
| `device-dumps/sk-d840n_b1.bin` | `mtd1` = signed boot image (blocks 0-6) + **U-Boot env at `0x180000`, 128 KiB, CRC validated** → `mtdparts` (H_B), `bootargs`, `bootcmd=zxboot`, `ethaddr`, `UpModeInfo`, `ext_args`; U-Boot banner `2021.01-svn358069 (Dec 01 2025)`; the RSA2048/AES/`downver`/`up_mode_*` strings |
| `device-dumps/sk-d840n_k0.bin` | `mtd3` = vendor header (`ZXIC 133 UNI V2.0.0 286632 CM OSGI`, `CMNV1.0.024`, stamp `20251201200927`, u32 `0x2200000` @ `0x78`) + plaintext FIT (`d00dfeed` @ `0x1e0`, `totalsize 0x1299fdf`) + gzip kernel @ `0x2d4` |
| `device-dumps/sk-d840n_mtd2.bin` | `mtd2` = jffs2, one live eraseblock at `0x140000` (38,865 B), nodes `paramtag` / `flag_type` / `osgi_set`, values `current : 324`, `partion:1` |
| `sk-d840n-openwrt/binary/boot.bin` | exactly 2 MiB, same first bytes as our `mtd1` → the community ships the stock bootloader **with their own env block** (their env has a different `mtdparts`/`bootcmd`/`bootargs`, `filesize=bd4f` leftovers from a tftp session) |
| `sk-d840n-openwrt/binary/uImage` | plain uImage (`0x27051956`, 4,814,777 B, "Linux kernel for …") with **no vendor header** → only boots under their replaced env/`bootcmd` |
| `sk-d840n-openwrt/binary/board.dtb` | FIT-wrapped DTB (`d00dfeed`, 48,463 B) — loaded separately by their `bootcmd` |
| `sk-d840n-openwrt/README.md` | the TTL/U-Boot recipe (case open), the community layout addresses, and the OpenWrt `armsr/armv8` SDK + `gcc-13.3.0` toolchain pointer used in `build_hint.md` |
| `devtools/uboot-stock.dts.txt:2044-2101` | DTB extracted from the `mtd1` dump by the parallel analysis in this directory: the `dynamic-partitions` node (256 MiB table) whose `reg = <offset size>` pairs reproduce the env's `mtdparts` exactly, plus a second 128 MiB table (`part@128`) selected by detected flash size → §3's map |
| `uboot-an758x/include/mtd/mtd-abi.h:93-102` | mainline flag values (`MTD_WRITEABLE = 0x400`) — the reason we treat the observed `0x400` as a vendor split and verify it empirically |
| `stock2ubi/an758x_mtd_rw.ko` | `.llvm_addrsig` present → clang-built, still loads → the compiler is not part of the module contract; vermagic `5.4.55 SMP mod_unload aarch64` |

## Appendix B — `mtd3` vendor header fields we could read

Little-endian u32s from `device-dumps/sk-d840n_k0.bin`:
`0x00 0x99999999`, `0x04 0x44444444`, `0x08 0x55555555`, `0x0c 0xaaaaaaaa` (preamble),
`0x24 "V2.0"`, `0x50 0x1`, `0x54 0x0129a1c0` (total image length; = `0x1e0 + 0x1299fdf + 1`),
`0x58 0x01299fdf` (FIT length), `0x5c 0x1e0` (payload offset), `0x60 0x92efdacb` (CRC field,
algorithm not identified), `0x68 0x0129a1c0`, `0x70 0x200000` (this slot's flash address — H_B),
`0x74 0x2000000` (slot size), `0x78 0x2200000` (sibling slot — H_B), `0x84 "ZXIC 133 UNI V2.0.0
286632 CM OSGI"`, `0x110 "20251201200927"`, payload FIT at `0x1e0` (`totalsize 0x1299fdf`,
off_struct `0x38`, off_strings `0x1299b80`).
