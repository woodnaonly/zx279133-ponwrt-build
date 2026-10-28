# 93 — The photos, the board.dtb, and the silk-screen (a cross-check, not a new claim)

**Date:** 2026-10-28
**Subject:** a physical/silk-screen inventory of SK-D840N derived from the repo's
`images/` photos and a full parse of `binary/board.dtb`, cross-checked against
the 92 documents already in `docs/`. **This document asserts nothing new about
the datapath.** It records what the board *physically* is, so that later
documents can stop re-deriving it — and it explicitly marks the two places
where the photos and the existing docs disagree.

---

## 1. What this document is, and what it is not

The upstream repo `sk-d840n-openwrt/` ships a set of board photographs
(`images/PCB.JPG`, `images/SoC.png`, `images/Serial_Port.png`,
`images/DDR_TYPE1.JPG`, `images/DDR_TYPE2.JPG`, `images/NAND_FLASH.JPG`,
`images/PHY_2.5G.JPG`, `images/VERSION_2024.JPG`, `images/VERSION_2025.JPG`).
Until now nothing in `docs/` had read them.

This document does three things:

1. reads the silk-screen off the photos;
2. parses `binary/board.dtb` in full and reports the node/property inventory;
3. states where the two disagree with `docs/`, and where the existing docs
   are *more* precise than anything the photos can support.

**It does not** revisit the LAN datapath, the 9132, the XMAC0/XMAC1 question,
the PPU mapping, or the PON stack. Those are settled in `docs/16`, `docs/15`,
`docs/92` and `docs/91`, and nothing here changes them. Where a photo
suggested something that would have contradicted those documents, that is
recorded in §6 as a **rejected** reading, not as a finding.

---

## 2. The board

| | |
|---|---|
| Board | SK-D840N — 10Gbit/s 无源光网络用户端设备 (XG-PON ONU), 智能家庭网关 类型十九 |
| PCB silk | `5800-2ATDYOA` |
| Maker | 深圳…（铭牌型号 0CTC，MAC 前缀 74:A5:82） |
| Hardware revs | V2024 / V2025 (`images/VERSION_2024.JPG`, `images/VERSION_2025.JPG`) |
| Power | 12V ⎓ 1A |
| Default mgmt IP | 192.168.1.1 |

The 2024 and 2025 labels differ in the 进网许可 field (AC880 vs 0CA) and in
the default password; the board layout is otherwise the same family.

---

## 3. Silk-screen inventory (from the photos)

| Part | Marking | Where on the board |
|---|---|---|
| SoC | `ZXIC` / `ZX279133` (0106, TN8C2200, 2412JJC2D8019, AE2TNV10001) | `images/SoC.png`; under the main heatsink on `PCB.JPG` |
| DDR3 (type 1) | Zenteel `A3T4GF40BBF-HP` | `images/DDR_TYPE1.JPG` |
| DDR3 (type 2) | RamNker `RS35128Q-18E 2517K` | `images/DDR_TYPE2.JPG` |
| NAND | GigaDevice `AU2415 5F2GM7UEY1G` (2Gb SLC) | `images/NAND_FLASH.JPG`, silkscreen `D5A7` |
| 2.5G PHY | Realtek `RTL8221B` | `images/PHY_2.5G.JPG`, silkscreen `US01` |
| RAM vendor logo | RamNker (dragon logo) on the SOIC-16 next to the heatsink | `PCB.JPG` — marking reads `RS35128Q-18E`, i.e. **this is the DDR3, not a switch chip** |

Two details worth recording because they are easy to get wrong from the
photos alone:

- The RamNker part adjacent to the heatsink is **`RS35128Q-18E`** — the same
  DDR3 as `DDR_TYPE2.JPG`. At low resolution the `RS35128Q` line reads like
  `RS512802C`; rotating the crop 90° and raising contrast resolves it. **Do
  not report a RamNker `RS512802C` switch chip on this board** — that reading
  was checked against the pixels and is wrong.
- The `4C01`-marked QFN at the top right (`TR01`/`JL01` area, next to the
  yellow FPC) is under thermal compound and a white coax lead. Its full
  part number **could not be read**; `4C01` is the only legible fragment.
  Its role is **not asserted here** — see §6.

The 13 identical 2-pin-pair groups in a row above the heatsink are identical
SOT-23 + 2×SMD circuits, one per network port group; they are not identified
here and the docs do not identify them either.

### Board-edge connectors (silkscreen, `PCB.JPG`)

Left edge, top to bottom: `SP01` (DC barrel), `JP01` (USB), `X6` (shielded),
`X4` (shielded), `SN01` (marked `RST` = reset button). A metal-can module and
an electrolytic marked `EL 16 000µV` sit on the left half.

---

## 4. The serial header — the operationally important part

`images/Serial_Port.png` at 8× shows two 1×4 headers, 2.54mm pitch:

**`JM01`** (the one framed in the upstream README) — pin 1 is the square pad
at the top, and the silkscreen column above it reads top-to-bottom:

| Pin | Signal |
|---|---|
| 1 | **GND** |
| 2 | **VCC** |
| 3 | **RX** |
| 4 | **TX** |

**`JM02`** — a second 1×4 header to the right: `GND` / `TXD` / `FS0` / `GND`.
`FS0` is a boot-mode strap, so `JM02` is the vendor's recovery/force-boot
access, not the console.

**`JM01` is the console**: `board.dtb`'s `/chosen` has
`stdout-path = /soc/serial@10d0d000` and `bootargs = console=ttyAMA0,115200n8`,
and `UART0` is the only `status = "okay"` of the three (`serial@10d0d000`
okay; `serial@10d0e000` and `serial@10d11000` disabled). `boot.bin` contains
`baudrate=115200`.

> **VCC on `JM01` is 3.3V logic level.** Use a 3.3V USB-TTL adapter. 5V
> will damage the SoC. (115200 8N1, no flow control.)

This is the one fact in this document that is not otherwise recoverable from
the repo, and it is the one needed before any flashing step.

---

## 5. `binary/board.dtb` — full parse

274 nodes, 1550 properties. `model = "ZTE 133"`, `compatible = "zte,133"`.

| Item | Value |
|---|---|
| CPU | `/cpus/cpu@0` + `cpu@1`, `compatible = arm,cortex-a53 | arm,armv8`, `clock-frequency = 0x3b9aca00` = **1 GHz** |
| OPP table | 500 MHz … 1.8 GHz (`/opp_table0/opp@500000000` … `opp@1800000000`) |
| Memory | `/memory@80000000`, `reg` length `0x20000000` = **512 MiB** |
| UART0 | `/soc/serial@10d0d000`, `arm,sbsa-uart | arm,pl011`, **okay** |
| UART1/2 | `/soc/serial@10d0e000`, `/soc/serial@10d11000` — **disabled** |
| `/chosen` | `bootargs = console=ttyAMA0,115200n8 rdinit=/sbin/init mem=1024M` |
| NAND | `/soc/nand@01000000`, `zxic,zx-denali-nand` — **disabled** |
| SPI flash | `/soc/spifc@0x10d0f000` — **disabled** |
| MDIO | `/soc/mdio@14f01000` and `@14f02000` — both **okay** |
| PCIe | `/soc/pcie@15200000`, `zte,zx279133-pcie`, `num-lanes = 1` — **okay** |
| USB | `/soc/usb3@15008000`, `/soc/usb3@15010000` (`generic-xhci`) — both **okay** |
| PON | `/soc/pon@17000000` (`zte,zx279133-pon`) — **okay** |
| **RGMII** | `/soc/rgmii@15900000` — **disabled** |
| **Uni-SerDes** | `/soc/uni_serdes@16100000` — **disabled** |
| **gEphy** | `/soc/gephy_apb@15400000` — **disabled** |
| **XPCS** | `/soc/xpcs@1a000000` — **disabled** |
| LED groups | `/soc/leds`, `/soc/leds_2`, `/soc/leds_5` — each with `led_lan0..3`, `led_wan`, `led_pon`, `led_los`, `led_wlan`, `led_wps`, `led_power`, `led_usb1/2`, `led_phone1` |
| Keys | `/soc/keys/keys@restore`, `keys@wps`, `keys@wlan` |

### 5.1 The `disabled` row is not a bug, and it is not new

`rgmii` / `uni_serdes` / `gephy_apb` / `xpcs` are all disabled **in the
vendor's own `board.dtb`**. This is the same fact `docs/07` already
established, and `docs/16`/`docs/92` build on it: `uni_serdes` is the 10G
channel toward the LAN switch, and it accepts only USXGMII/10GBASER — it is
not the 1G copper side.

**`board.dtb` is the *vendor* device tree, not the mainline one.** The
mainline tree is `ci-build-repo/kernel-overlays/arch/arm64/boot/dts/zte/zx279133-sk-d840n.dts`
(presently modified in the working tree). Reading `board.dtb` tells you what
the *stock* device declared, which is a useful reference but is **not** the
tree the CI image boots. The `disabled` nodes above must not be read as
"mainline left the LAN ports unconfigured".

### 5.2 The `mem=1024M` vs 512 MiB mismatch is real

`/chosen`'s `bootargs` says `mem=1024M`, but `/memory@80000000` describes
**512 MiB** of actual DRAM. `mem=` on the command line overrides the DT
memory node's size in the kernel's early boot, so the stock kernel is told
there is twice the memory there is. The upstream README also says
`DDR3 512MiB`. Worth noting; it is inherited vendor text, not a bug
introduced here, and `docs/` has not previously recorded it.

### 5.3 The partition map matches the upstream flashing procedure

`/part@256/partition@root` has `reg = 00f000000f100000` — NAND offset
`0x00f00000`, size `0x0f100000` — which is exactly the rootfs erase/write in
the upstream README's flashing steps. The same node set repeats under
`/part@128`.

---

## 6. Readings that were checked and **rejected**

Recording these so nobody re-derives them:

1. **"RamNker `RS512802C` is the LAN switch chip."** Rejected. The part is
   `RS35128Q-18E` (the DDR3). Rotating and enhancing the crop resolves it.
2. **"The `4C01` QFN is the PON optical device."** Not asserted. The marking
   is unreadable beyond `4C01`, and the existing docs identify the 10G PON
   side from the *register map* (`/soc/pon@17000000`, `pon_serdes`,
   `pon_serdes_pll`), not from a silk-screen. Note `pon_serdes@16000000` and
   `pon_serdes_pll@16010000` are **disabled** in `board.dtb`, consistent with
   the stock configuration muxing PON SerDes to Ethernet mode (see
   `docs/91`).
3. **"The 13 SOT-23 groups are the PON light-drive circuits."** Not asserted.
   They are 13 identical per-port circuits; the docs do not identify them.
4. **"The board is a generic router."** Rejected — it is an XG-PON ONU
   (10G). This matters because the whole datapath effort in `docs/` is about
   a PON-capable SoC with an external LAN switch.

---

## 7. Firmware binaries, for reference

- `binary/boot.bin` — U-Boot `2021.01-svn308776 (Apr 17 2025 - 16:09:35 +0800)`;
  contains `CPU : ZX279133@A53x2`, `bootcmd=zxboot`, `baudrate=115200`,
  `DDR3 %dbit %d Mbps`, `DDR4 %d Mbps`.
- `binary/uImage` — gzip-wrapped, kernel
  `Linux version 4.19.136+ (fanqiang@skyworth) (gcc 5.3.1 20160412, Buildroot 2017.05) #4 SMP Mon Jun 3 23:31:29 CST 2024`
  — the **vendor** kernel, not the mainline one.
- `binary/board.dtb` — the vendor device tree parsed in §5.

Note the upstream README's rootfs template is
`generic-ext4-rootfs.img.gz` (armsr/armv8, OpenWrt 24.10.8) while the
shipped `uImage` is a vendor 4.19 kernel; the two are from different
provenance and the README's flashing steps use `rootfs.jffs2` on NAND. The
CI repo's own `docs/05`/`docs/06` cover the procedure actually in use.

---

## 8. What a future session should take from this

- The console is on **`JM01`**, pins `GND`/`VCC`/`RX`/`TX` top-to-bottom,
  **3.3V only**, 115200 8N1. `JM02` is the boot-strap header, not the console.
- `board.dtb` is the **vendor** tree. The mainline tree is
  `ci-build-repo/kernel-overlays/arch/arm64/boot/dts/zte/zx279133-sk-d840n.dts`.
- The LAN datapath conclusions live in `docs/16`, `docs/15`, `docs/92`,
  `docs/46`; the PON side in `docs/91`, `docs/20`. **Read those instead of
  re-deriving them from the photos.**
- The `4C01` part, the 13 SOT-23 groups, and the `RS35128Q` vs `RS512802C`
  ambiguity are recorded here so they are not re-litigated.

**Provenance:** all readings in this document come from the photographs in
`sk-d840n-openwrt/images/` and from parsing `sk-d840n-openwrt/binary/board.dtb`.
No live board was touched. Where a photo could not settle a question, this
document says so rather than guessing.
