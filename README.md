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
| `trial/trial-Image.gz`, `trial/trial-dtb.bin` | the same kernel with the initramfs embedded, unpacked for `kexec` |
| `trial/trial-kx.tgz` | `kexec` plus the `.so` files it needs, because the box has no WAN |
| `flash-uImage` / `flash-dtb.bin` / `flash-rootfs.jffs2` | the triple the installed U-Boot bootcmd reads (`mtd read kernel`/`mtd read dtb`, `bootm`, `root=/dev/mtdblock5`) |
| `flash-set.tgz` | those three plus `flash-sk-d840n.sh` |

Order of operations, reversible up to step 4 because nothing is written before it:

1. `sh templates/trial-kexec.sh` on the running system (needs `PC=<tftp-ip>`) - it
   fetches `trial-kx.tgz`, `trial-dtb.bin`, `trial-Image.gz`, then
   `kexec --load` / `--exec` into the mainline RAM image.
2. Observe it from the PC: an ICMP/ARP answer from 192.168.1.1 on the wired
   segment already proves kernel + NPPT + PHY + TCP/IP. The image is then reached
   the same way as the running one (`ssh root@192.168.1.1`, blank password - that
   is base-files' own default, no key or password is baked into these artifacts).
   If the new system ever ships a locked root, rebuild with a root password rather
   than with an authorized key: a released image must not carry anyone's key.
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
