# zx279133 builds (SK-D840N effort)

CI-only repo: nothing is compiled here locally, GitHub Actions assembles the
upstream trees on the runner and uploads the images.

Phase 1 targets the **ZTE ZXSLC SR1010** board (same SoC as the Skyworth
SK-D840N ONU: Sanechips ZX279133, 2x Cortex-A53, 512 MiB DDR, 256 MiB SPI-NAND).
Phase 2 adds an SK-D840N board overlay (own DTS + device profile) once the
pipeline is green.

## Upstreams

| repo | role |
| --- | --- |
| [cnjn/ImmortalWrt](https://github.com/cnjn/ImmortalWrt) `sr1010-zx279133` | ImmortalWrt v25.12.1 fork adding `target/linux/zte/zx279133` (device `zte_zxslc-sr1010`, RAM-only FIT initramfs, SPI-NAND kept read-only) |
| [cnjn/linux-mainline-zte-zxslc-sr1010](https://github.com/cnjn/linux-mainline-zte-zxslc-sr1010) `build` | wrapper repo; its `linux-6.18.38` submodule (branch `codex/sr1010-mainline`) is the mainline kernel with the ZX279133 NPPT/IDM ethernet driver, ZX279051 2.5G PHY and RTL8372N DSA driver |
| [pbs05/ponwrt](https://github.com/pbs05/ponwrt) | build/CI pattern (its `release.yml`) and the PON feeds `pbs05/openwrt-pon-drivers` + `openwrt-pon-userspace`; those packages are Airoha AN7581/AN7583 only, so they are not selected on `zte/zx279133` |
| [huxiangjs/SK-D840N-OpenWRT](https://github.com/huxiangjs/SK-D840N-OpenWRT) | the 4.19 BSP image currently flashed on the SK-D840N (boot.bin + uImage + board.dtb + rootfs.jffs2) and its flash layout |

## Layout on the runner

```
$GITHUB_WORKSPACE/
├── openwrt/                                  # cnjn/ImmortalWrt
└── linux-mainline-zte-zxslc-sr1010/
    └── linux-6.18.38/                        # submodule = the kernel tree
```

That sibling layout is deliberate: `openwrt/scripts/configure-sr1010-initramfs.sh`
defaults `KERNEL_TREE` to `../linux-mainline-zte-zxslc-sr1010/linux-6.18.38`, so the
script needs no override.

## Build

Workflow `Build SR1010` (`workflow_dispatch`, also runs on the first push).
Output: `bin/targets/zte/zx279133/immortalwrt-zte-zx279133-zte_zxslc-sr1010-initramfs.itb`.

Booting that image as upstream intends it needs a serial console (U-Boot:
`tftpboot 0x88000000 <itb>` then `bootm 0x88000000`). The SK-D840N has no USB-TTL
cable, so phase 2 replaces that step with `kexec` from the system that is running.

## Phase 2: SK-D840N without a serial console

Workflow `ZX279133 SK-D840N Build`, matrix mode `initramfs` and `flash`
(`workflow_dispatch` lets you run one of them).

| artifact | what it is |
| --- | --- |
| `sk-d840n-initramfs.itb` | RAM-only FIT, same shape as SR1010's |
| `trial-set.tgz` | `trial-Image.gz` + `trial-dtb.bin` + both scripts, one download |
| `trial-Image.gz`, `trial-dtb.bin` | the kernel with the initramfs embedded, unpacked for `kexec`, and its dtb |
| `flash-uImage` / `flash-dtb.bin` / `flash-rootfs.jffs2` | the triple the installed U-Boot bootcmd reads (`mtd read kernel`/`mtd read dtb`, `bootm`, `root=/dev/mtdblock5`) |
| `flash-sysupgrade.bin` | the same triple as a sysupgrade tar, for flashing from inside a running system |
| `flash-set.tgz` | those four plus `flash-sk-d840n.sh` |

`kexec` is **not** in these releases: no OpenWrt feed ships kexec-tools for
aarch64 (verified against the 24.10 `aarch64_generic` base manifest and a full
`feeds install`). Build it on the PC with `sh tools/getkexec.sh` (parent project),
which takes Alpine's musl `kexec-tools` plus the `libz`/`liblzma` its binary needs -
`kexec --version` returning `kexec-tools 2.0.31` was confirmed on this board - and
writes `staging/trial/trial-kx.tgz`. Copy it next to the trial artifacts (it is not
in the release, so `trial-kexec.sh` takes it as a local file or fetches it).

## Upgrading from inside the system (`flash-sysupgrade.bin`)

`sysupgrade.bin` is an **uncompressed sysupgrade tar** whose members are
`sysupgrade-skyworth_sk-d840n/{CONTROL,kernel,dtb,root}` - the exact triple above.
`overlays/target/linux/zte/zx279133/base-files/lib/upgrade/platform.sh` writes them
back by partition label, so no offset is hard-coded on the device, and it keeps the
`kernel` partition for last for the same reason the flasher does.

    sysupgrade -n flash-sysupgrade.bin        # or the LuCI firmware page

This is safe with respect to the mounted root because `/sbin/sysupgrade` hands the
work to `/lib/upgrade/do_stage2`, which runs from `/tmp/root` (`RAM_ROOT`) after the
real root is no longer in use - the config archive is passed on with `mtd -j`, so
`sysupgrade` without `-n` keeps the settings.

It is **not** safe with respect to the kernel: a sysupgrade that replaces `kernel`
with something that hangs has the same outcome as any other bad kernel - no
console, programmer required. Trial the kernel with kexec first; use sysupgrade for
the second and later changes once the image is proven on this unit.

Order of operations, reversible up to step 5 because nothing is written before it:

1. Copy the trial files onto the running system and start them there. The image on
   this unit has **no tftp client** (`/bin/uclient-fetch` is the only fetcher) and
   its `scp` cannot serve the default SFTP protocol (`/usr/libexec/sftp-server` is
   not in the image), so push with `scp -O` or a plain ssh pipe:
   `scp -O -i ~/.ssh/id_onu trial/* root@192.168.1.1:/tmp/trial/` then
   `ssh root@192.168.1.1 sh /tmp/trial/trial-kexec.sh` (30 MB lands in ~1.5 s this
   way). `PC=<ip>` makes the script fetch whatever is missing itself over HTTP,
   `TFTP=1` over tftp. It then runs `kexec --load` / `--exec` into the mainline RAM
   image. That load path is not assumed: a mainline 6.18 arm64 Image `--load`ed on
   this board returned 0 with `/sys/kernel/kexec_loaded` going 0 -> 1 (then
   `--unload`, nothing executed) - the running 4.19 has both
   `__arm64_sys_kexec_load` and `__arm64_sys_kexec_file_load` in `/proc/kallsyms`.

   **Mask the watchdog's reset path before `--exec`.** The running 4.19 arms three
   `zx_wdt` instances at boot - `zxwdt[0]: heartbeat 8 sec, clock 2048` - and feeds
   them in-kernel, so any kernel that takes over has no feeder and the board is
   rebooted ~15-20 s later. `zx_wdt` is built in (nothing to unload) and
   `/proc/watchdog/ctrl` ignores every write syntax tried, but the reset routing
   lives in a single CRM register:

       devmem2 0x10e10208 w 0x0          # a normal vendor system reads 0x1F1 here

   Measured on this unit: `0x101` (only the per-WDT bits 4-7, the ones the DTB calls
   `rst-mask`, cleared) postponed the reboot to ~44 s but did not prevent it, while
   `0x0` let mainline run indefinitely - bits 0 and 8 are the actual chip-reset path.
   `nowatchdog` on the kernel command line changes nothing, because this is the
   *vendor* kernel's timer. The register is volatile: every boot sets it back to
   `0x1F1`, so it has to be written again per attempt, and once it is masked a hang
   no longer recovers itself - step 4 (power cycle) is the way back.

   Ordering that follows from all this: `--exec` has to be armed over ssh *before*
   the cable moves to the `eth0` jack, because the moment mainline takes over the
   LAN ports die - e.g. `setsid sh -c 'sleep 240; devmem2 0x10e10208 w 0x0;
   cd /tmp/trial && LD_LIBRARY_PATH=lib ./kexec --exec' &`. Every reset also wipes
   `/tmp/trial`, so each attempt needs the kit re-pushed.
2. Observe it from the PC, from the one jack `eth0` is wired to. This unit has **no
   WAN/uplink RJ45** - the front panel is 4x LAN plus the fibre PON - and the running
   image calls `eth0` `wan` only because its config came from a router default
   (`lan bridge = eth1 eth2 eth3`, `wan.device = eth0`). So `eth0` (NPPT + RTL8226)
   is one of those four LAN jacks, while `eth1..eth3` are "9132" ports mainline
   cannot drive. The vendor driver prints the port number with the netdev, which is
   how to find the jack: `dmesg` shows `[eth0] port 4 link down` / `[eth1] port 1
   link up:1000M`, so move a cable and watch. `overlays/.../board.d/02_network`
   puts `eth0` alone in `lan`, which gives it 192.168.1.1 (base-files'
   default address). An ICMP/ARP answer from there already proves kernel + NPPT +
   PHY + TCP/IP. The image is then reached the same way as the running one
   (`ssh root@192.168.1.1`, blank password - that is base-files' own default, no key
   or password is baked into these artifacts). If the new system ever ships a
   locked root, rebuild with a root password rather than with an authorized key: a
   released image must not carry anyone's key.
3. In that shell `/proc/mtd` must show `root` as **mtd5**, the index hard-coded in
   the U-Boot env. If it does not, fix the partition list (or the env) before
   writing anything.
4. Power-cycle to get the current system back - flash is untouched.
5. From the RAM system, `tar xzf flash-set.tgz && sh flash-sk-d840n.sh --write`:
   `root` first, then `dtb`, then `kernel` last, each erase+write+verify through
   `/sbin/mtd` by partition label (there is no `mtd_debug` or `nandwrite` in this
   image, so the flasher does not assume them).

There is deliberately no "write the boot log into a spare partition" channel: a
partial flash write needs the MEMERASE ioctl and this image has no tool that can
erase a sub-partition range - `/sbin/mtd` always erases the whole partition, which
would take the fallback uImage with it.

Gaps this cannot close: mainline has no GPON driver, and the four GE LAN ports sit
behind the SoC-internal "9132" switch, which the vendor drives without phylib or
DSA (`/dev/ethdriver` + `ethdrv_dev_ioctl brdev_set.port_id`). Only the external
RTL8226 port is describable in mainline, so the trial is planned around one RJ45.
`restore_set/` in the parent project keeps the md5-verified stock set for rollback.
