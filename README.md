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
`tftpboot 0x88000000 <itb>` then `bootm 0x88000000`). The SK-D840N currently has
no USB-TTL cable, so flashing/booting a mainline kernel on the real unit is a
phase-2 design problem, tracked in this repo's history.
