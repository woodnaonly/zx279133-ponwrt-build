# 对方的固件：逆向出来的 NPU 驱动，以及内核升到 7.3.4 这条路（2026-11-22）

> 目标：`别的人固件` → 逆向出 NPU 驱动 → 修补 `ci-build-repo`（内核可以升到 7.3.4）。
> 本文是这一轮的取证与结论；所有数字都能在 `pon/npu-reverse/` 里的逆向产物上复核。

---

## 0. 一句话

**「别的人固件」是 cnjn 给 ZTE ZX279133（星云max/SR1010 系）做的 ImmortalWrt 构建，
NPU 驱动不是黑盒：它的源码公开在 `cnjn/immortalwrt` 的 `target/linux/zte/patches-7.3` 里。**

于是这件事的结论和最初的设想不一样，而且更好：

| 原设想 | 实际情况 |
|---|---|
| 只能从二进制逆向出 NPU 驱动 | **驱动源码是公开的**（39 个补丁 / 168 个内核文件），逆向只剩「验证」作用 |
| 驱动是某个二进制里的代码 | 它是 **linux 7.3.x + 一套重写过的 NPU 驱动**（`npu.ko` 组合模块 + `npu-pon.ko` + `fnoffload.ko`） |
| 我们的 `np.c` 是最新 | 我们的 overlay 相对上游 6.18 驱动 **加了约 7900 行实测修正**，要**重基**到 7.3，而不是丢掉 |

**判决**：升级到 7.3.4 是可做的（上游有完整的 7.3 补丁系列），但工作量集中在
**「我方实测修正重打到 7.3 驱动集上」**，不在编译本身。

---

## 1. 固件身份（已验证）

| 项 | 值 |
|---|---|
| 文件 | `别的人固件/immortalwrt-zte-zx279133-star_sg350h-initramfs.itb` |
| sha256 | `c6e5010d4b59dbb28d94b6ead8c44d94024c912d761b93fd5186b2d1855fc046` |
| 大小 | 20,665,604 B |
| 姊妹文件 | `…-squashfs-sysupgrade.bin`，sha256 `b78243ff48e61c4ce855d1e04e02e4806b57b9547ac4355f8bf61dca3c96bc0d`，18,633,715 B |

FIT（`tools` 无关，纯 Python 解析）：

| image | 内容 | 大小 |
|---|---|---|
| `kernel-1` | gzip，`ARM64 OpenWrt Linux-7.3.4` | 解压后 16,992,264 B |
| `initrd-1` | `ARM64 OpenWrt star_sg350h initrd` | 13,441,191 B |
| `fdt-1` | `ARM64 OpenWrt star_sg350h device tree blob` | 17,548 B |

- initrd 是 newc cpio：**1971 条记录 / 34,247,596 B 载荷**，模块目录 `lib/modules/7.3.4/`。
- **模块版本串 `7.3.4` 就是内核版本**（`vermagic=7.3.4 SMP preempt mod_unload aarch64`）。
- 目标/板型：**target `zte` / subtarget `zx279133` / profile `star_sg350h`**（OpenWrt 产物命名规则）。
- 固件里的 DTS 是 `star_sg350h` 那块板子的，不是我们的 SK-D840N。

**作者是 cnjn**（恩山贴「中兴自研CPU ZX279133 星云MAX主路由 适配主线内核」，2026-07-23 起；
GitHub `cnjn/immortalwrt`、`cnjn/linux-mainline-zte-zxslc-sr1010`）。我们仓库的 CI 用的
`cnjn/ImmortalWrt` 与 `cnjn/linux-mainline-zte-zxslc-sr1010` 就是他 6.18 时期的产物。

---

## 2. 逆向方法与两个必须记住的坑

工具（都在工作区 `scratch/`，纯 Python + capstone）：

| 脚本 | 作用 |
|---|---|
| `scratch/re_other_fw.py` | FIT 解析 + newc cpio 解包（无 cpio/dumpimage/unsquashfs 的机器上用） |
| `scratch/kmod.py` | ELF64 模块解析：段表、modinfo、符号表、字符串 |
| `scratch/npudis.py` | 反汇编（带符号/重定位标注） |
| `scratch/kcalls.py` | 从 `.rela.text` 还原调用图 |

### 坑 1：ET_REL 模块里「符号地址」不是文件位置

`.text` 段 `sh_addr = 0`、`sh_offset = 0x2ae8`。**符号值是段内偏移**，
所以「函数在文件里的位置」= `段文件偏移 + 符号值`。
按「符号值直接当文件偏移」读会读到别处的代码 —— 而且那段代码**看起来完全合理**
（同样是一个 `ldr`/`cbz` 开头的小包装函数），这就是它危险的地方。

### 坑 2：`bl` 的立即数在未链接目标里没有意义

- 对**外部/全局符号**的调用：`bl` 的编码字段是 0，真目标在 `.rela.text`
  （`R_AARCH64_CALL26`，2022 条），`r_offset` 是段内偏移，可以据此把调用点归属到函数。
- 对**同段局部符号**的调用：汇编器当场算好偏移，**没有重定位**，目标就写在指令里。

两种调用要分别取，否则调用图会一半是空的一半是错的。

（这条对我们自己的仓库同样成立：`docs/95` 里那句「厂商 `.ko` 里 `BL` 是重定位占位符而不是目标」
说的就是坑 2 的前半段。）

---

## 3. 逆向出来的 NPU 驱动结构

### 3.1 模块清单（initrd `lib/modules/7.3.4/`）

| 模块 | 大小 | sha256 | 说明 |
|---|---|---|---|
| `npu.ko` | 388,632 | `cd847d840fca68e181ccaf1a0b36eaf6f16603b4c0dab543a9ae8eaa99e9351c` | NPU/以太网主驱动 |
| `npu-pon.ko` | 134,720 | `6dd84a70777091fbca851f6b40687505143ff02f5bcc178e344c86be6ca329ed` | PON 光口模块（`depends=npu`） |
| `fnoffload.ko` | 95,712 | `7286aa0499ac0085ccf041d68c201897a90c3c9687f8e96298314f3306285598` | 流卸载模块 |
| `zx279133-ge.ko` | 14,792 | `ec77dee915c971edb31164ecc14353cff4b6582d235e41be905e84c3678cbb6f` | 直通千兆口（`platform:zx279133-sg351-ge`） |

### 3.2 `npu.ko` 的 modinfo（要点）

- `description=ZTE ZX279133 NPPT Ethernet driver`
- `alias=of:N*T*Czte,zx279133-npptC*` / `zte,zx279132-npptC*`
- `firmware=zte/zx279133/mcode_intel.bin` ← **和我们仓库里那份微码同名同尺寸**
  （`overlays/target/linux/zte/zx279133/firmware/zte/zx279133/mcode_intel.bin`，103,456 B）。
- `depends=`（空）、`intree=Y`、`license=GPL`
- modparam：`lan_up_port_zero_based`、`pon_sdt_wan_port`、`rx_hash_ipv6`、`rx_hash`、
  `vlan_offload`、`experimental_vid_key`、`tx_pon_control`、`experimental_sqm`

`npu-pon.ko`：`alias=of:N*T*Czte,zx279133-sg351-ponC*` / `zte,zx279133-ponC*`，
modparam：`pon_tx_pcp_queue`、`omci_ik_mode`、`omci_rx_mode`、`omci_tx_mode`、`lp_gem_shift`。

### 3.3 导出 API：99 个（逆向结果，完整表见 `pon/npu-reverse/npu.ko.api.txt`）

按用途归类：

| 组 | 数量 | 例子 |
|---|---|---|
| 对象/锁 | 6 | `zte_nppt_get/put/begin/try_begin/end/get_info` |
| 客户端注册 | 2 | `zte_nppt_register_client/unregister_client` |
| ZCAM（流/区） | 3 | `zte_nppt_zcam_alloc/release/write` |
| Host（主机/流上下文） | 6 | `zte_nppt_host_get/put/lookup_get/limit/watch/hosts_show` |
| DDR / IKEY | 8 | `zte_nppt_ddr_write/debug_ddr_read/debug_ikey_read/ddr_clear/ikey_write/age_read_clear/stats_read` |
| WAN id | 2 | `zte_nppt_wanid_get/put` |
| 帧长上限 | 1 | `zte_nppt_frame_limits_get` |
| **原生 QoS/SQM** | 20 | `native_qos_gates_*`、`native_plcr_profile/binding/timing_*`、`native_red_buffer/usage_full_*`、`native_ssch_global/mode/weight/queue/port_*`、`sqm_enabled` |
| VLAN 卸载 | 9 | `vlan_status/get/put/abandon`、`native_vlan_get/set/mode_*`、`native_pvid_*`、`native_invlan_*` |
| Meter（限速） | 6 | `meter_get/put/abandon/mac_get/flow_capacity/bucket/flow_id` |
| PPU | 4 | `ppu_context_get/ppu_capture/ppu_vid_probe/ppu_me_diagnostics_get` |
| SEDDR | 4 | `seddr_mb/port_hint_set/flow_qos_sync/port_hits` |
| PON 钩子 | 8 | `zx279133_pon_prepare/pon_detach/pon_tx_ctrl_hook/pon_flow_gem_hook/pon_rx_fixup_hook/pon_rx_omci_hook/idm_omci_xmit_ppu/omcc_gem_id` |
| 端口/转发表 | 7 | `program_spa_uc_macs/multi_hw_port_present/np_set_global_wan_port/np_set_pon_mode/multi_sync_port_modes/flow_offload_flush/wan_port_schedule_sync` |

**这份接口面是新的**：我们 `np.c` 里没有 `zte_nppt_*` 这一层，也没有 host/meter/qos/SQM 这一族。

### 3.4 DT 契约（从 rodata 反推）

- **时钟 6 个**：`idm-aclk`、`tm-aclk`、`pclk`、`smac-wclk`、`woe1-wclk`、`mac-wclk`
  （`"failed to get clocks"`）
- **保留内存 3 块**：BMU / SE hash / IDM，各自检查「缺失/非法/太小/超 32 位 DMA」
- `memory-region-names` 里取 **`se-ddr`**；没有就退回「0-extra-RAM SRAM SQM」模式
- 另外要 `zte,pon-route`、`zte,idm-cci` 两个 syscon，`serdes`/`pcs-handle`（XPCS），
  `nvmem-cells`（出厂 MAC）

### 3.5 初始化顺序（从 rodata 顺序还原）

```
MMIO + 保留内存 → clocks → SE PLCR → SE 表描述符 → SE-DDR → 统计表
→ PPU 微码(mcode_intel.bin) → PPU 表 → SPA 属性 → NPPU early(ISU)
→ SMCT → NPPU late → SDET → SIPC 调度器 → MF 端口 PPU
→ SG351 端口起停 → LAN 口起停 → 共享资源管理器 → XPCS/phylink
→ netdev 注册(WAN + LAN 子设备) → XDP/AF_XDP → NAPI/RX 页池 → PHC → 出厂 MAC
```

微码装载有两个分支，值得记：
- `PPU microcode version %#x.%#x`（正常）
- `uncalibrated PPU firmware; retaining native RX queues`（微码不是标定版时的退路）

---

## 4. 交叉验证：驱动源码在哪儿

**`cnjn/immortalwrt`（master，commit `31e2ac4ded9c8896c7aefd3dcb9a46ef77440fbd`，2026-11-10）**

- `target/linux/zte/Makefile`：`KERNEL_PATCHVER := 7.3`，
  且把 `package/firmware/zte/flies/zx279133-npu.bin` 装成 `firmware/zte/zx279133/mcode_intel.bin`
- `target/linux/zte/patches-7.3/`：**39 个补丁**
- `target/linux/zte/zx279133/`：`config-7.3`（174,911 B）、`target.mk`、`base-files/`
- `target/linux/zte/dts/zx279133-sr1010-openwrt.dts`

补丁系列共触及 **168 个内核文件**，其中驱动部分是**重写**：

```
drivers/net/ethernet/zte/
  zx279133-np.c        (NPU 核心，模块 npu.o)
  zx279133-host.c/.h   zx279133-meter.c/.h   zx279133-qos.c/.h
  zx279133-native.c/.h zx279133-offload.c zx279133-offload-api.c
  zx279133-client-limits.h  zx279133-policy.h  zx279133-native-debug.c
  zx279133-idm.c zx279133-mac.c zx279133-main.c zx279133-netdev.c
  zx279133-lan-conduit.c zx279133-rtl8372n.c zx279133-ptp.c
Documentation/devicetree/bindings/net/zte,zx279133-nppt.yaml   ← DT 绑定
arch/arm64/boot/dts/zte/zx279133.dtsi  zx279133-sr1010.dts
```

`drivers/net/ethernet/zte/Makefile`（410 补丁）：

```
obj-$(CONFIG_ZTE_ZX279133_ETH) += npu.o
npu-$(CONFIG_ZTE_ZX279133_NATIVE_DEBUG) += zx279133-native-debug.o
obj-$(CONFIG_ZTE_ZX279133_LAN_CONDUIT) += zx279133-lan-conduit.o
obj-$(CONFIG_ZTE_ZX279133_RTL8372N) += zx279133-rtl8372n.o
obj-$(CONFIG_ZTE_ZX279133_FNOFFLOAD) += fnoffload.o
```

### 4.1 逆向 ↔ 源码 的数字对照

| 对照项 | 数字 |
|---|---|
| 逆向得到 `npu.ko` 的导出符号 | **99** |
| 源码里 `EXPORT_SYMBOL*` 的去重符号 | **84** |
| 两者同名命中 | **69** |
| 只在逆向结果里（源码未导出） | **30** |
| 只在源码里（固件未导出） | **15** |

- 命中里包含逐字对上的 `zte_nppt_ppu_me_diagnostics_get` —— 这条正是我们 `docs/95`
  追了半个月的那个「ME 表只读镜像」诊断口。
- **只在逆向里的 30 个**几乎全是 `zx279133_pon_*` / `zx279133_omcc_gem_id` /
  `zx279133_tx_pon_control` 这类 PON 钩子，公开 master 里**根本没有**这些符号。
  ⇒ **那份固件比公开 master 更新**：PON 一侧的新工作还没进公开树。
- 只在源码里的 15 个里有 `zte_nppt_is_wan`、`zte_nppt_native_red_usage_get`、
  `zte_nppt_ppu_me_stats_get`、`zte_nppt_shaper_clear` —— 命名与我们逆向到的
  `zte_nppt_ppu_me_diagnostics_get` 并不一致 ⇒ **固件是 master 前后某个快照，API 在动**。

**结论**：公开 master 是目前能拿到的最好源码（覆盖固件 API 的约 70%，且核心全在），
剩下的是 PON 钩子那部分，只能等上游公开或继续从二进制补。

---

## 5. 与我方 overlay 的差异

我方 `kernel-overlays/drivers/net/ethernet/zte/` 相对**上游 6.18 同名文件**的净改动
（`scratch/overlay_delta.py` 跑出来的）：

| 文件 | 上游行 | 我方行 | 增 | 删 | 性质 |
|---|---|---|---|---|---|
| `zx279133-np.c` | 2509 | 6448 | **+4052** | -113 | NPU/9132 表/微码/ME 观测面 |
| `zx279133.h` | 912 | 1705 | +824 | -31 | 常量与结构 |
| `zx279133-idm.c` | 1573 | 2205 | +759 | -127 | IDM 复位实测修正 |
| `zx279133-main.c` | 705 | 1394 | +700 | -11 | probe/时钟/中断 |
| `zx279133-netdev.c` | 2025 | 2632 | +695 | -88 | netdev/队列 |
| `zx279133-mac.c` | 688 | 780 | +115 | -23 | MAC |
| `Kconfig` / `Makefile` / `lan.h` / `tm.c` | — | — | +109 | -3 | 配置与杂项 |
| **只在我方** | | | | | `zx279133-switch.c`(159K)、`zx279133-pon.c`、`zx279133-rgmii.c`、`zx279133-omci.c`、`zxpon.c/.h`、`zx279133-lan1-led.h` |

合计约 **+7900 行**。其中「只在我方」的那几个文件（switch/pon/rgmii/omci）在 7.3 里
分别被上游的 `zx279133-native.c`（原生表）、`npu-pon.ko`（PON）、MAC 内的 RGMII 取代。

### 5.1 7.3 系列里**依然存在**的实测缺陷（这是最直接可打的补丁）

对照我方 `zx279133-idm.c` 的实测修正，7.3 系列里同一个驱动仍是旧值：

| 位置 | 7.3 系列 | 我方实测值 | 依据 |
|---|---|---|---|
| `ZX279133_IDM_TX_CFG_DEPTH` | `0` | `0x400` | docs/09 §2b、docs/10 §6 |
| `idm + 0x0124` | `writel(0, …)` | `writel(0x00000080, …)` | 厂商 `np_133.ko` 的 `idm_init()` 字面量 |
| `idm + 0x0010` | `writel(128, …)` | `writel(0x00000040, …)`（= `bp_retev_num_th`） | 同上 |
| `idm + 0x010c/0x0110` | 写死 `0x00003d00` | 按深度计算（normal `0x203d00` / jumbo `0x3d00`） | 厂商板子上就是这两个值 |
| `ZX279133_IDM_BP_RING_SIZE` | `4096` | `8192`（= 出厂 DT 的 `0x2000`） | 同上 |

我方 `idm.c` 里那段注释把来源写得很清楚（`zx279133_idm_bp_ring_cfg()`）：
`idm_rx_bp_init()` 写 `((bp_depth >> 12) - 1) << 21`，`idm_rev_bp_init()` 再加
`((free_depth >> 12) - 1) << 10`，两边都带 bit 8；用出厂 DT 的深度
（normal 0x2000、jumbo 0x1000、free 0x10000）算出 **0x203d00 / 0x3d00**，
「和厂商板子上读到的完全一样」。

⇒ **这五条是升级到 7.3 后必须重打的第一批补丁**，而且它们是我方独有、上游没有的实测结论。

---

## 6. 升到 7.3.4 的方案（分步 + 判据）

### 步骤 1｜内核源换成上游 7.3.x

- 现在：`cnjn/linux-mainline-zte-zxslc-sr1010` 的 `build` 分支 → 子模块 `linux-6.18.38`
  @ `96e5a24`（驱动已在树内），再由 `tools/prepare-sk-kernel.py` 打官方 stable 到 6.18.60。
- 7.3：**上游 linux-7.3.x**（kernel.org）+ `kernel-patches/7.3/`（本轮已 vendored 进仓，
  `MANIFEST.json` 记录来源 commit `31e2ac4` 与每个补丁的 sha256）。
- 判据：39 个补丁能按序干净 apply；打完的树里
  `drivers/net/ethernet/zte/Makefile` 有 `npu.o`、`Documentation/devicetree/bindings/net/zte,zx279133-nppt.yaml` 存在。

### 步骤 2｜OpenWrt 树换到 7.3 口径

- 现在：`cnjn/ImmortalWrt` 分支 `sr1010-zx279133` @ `1b50aaf`（`config-6.18`）。
- 7.3：`cnjn/immortalwrt` master（`config-7.3`、`target/linux/zte/zx279133`）。
- 判据：`target/linux/zte/zx279133/config-7.3` 存在；我方 `overlays/target/linux/zte/zx279133/`
  仍能盖住 base-files 且不与上游冲突。

### 步骤 3｜我方 overlay 取舍

| 类别 | 处置 | 理由 |
|---|---|---|
| SK-D840N DTS、LED/PHY 板级、rootfs/网络脚本 | **保留**（要改绑到 7.3 的 dtsi/绑定） | 这是我们自己的板级资产 |
| `kernel-overlays/drivers/net/ethernet/zte/*`（6.18 世代的驱动副本） | **不再直接覆盖**，改为「7.3 上游 + 我方实测修正重打」 | 上游 7.3 已重写，直接盖会把新驱动打回旧版（6.18 时就是这么炸过一轮：21 个错全在 `zx279133-offload.c`） |
| 我方实测修正（IDM 表见 §5.1、9132 表、PON、RGMII、ME 观测面） | **逐项重打** | 这是我们相对上游的唯一增量 |
| `overlays/target/linux/zte/zx279133/firmware/…/mcode_intel.bin` | 保留 | 与对方固件引用的是同一份厂商微码 |

### 步骤 4｜门禁

仓库现有门禁（`tests/test_sk_*.py`、`tools/check-sk-*.py`、`tests/kernelsrc/` 夹具）
都是按 6.18 世代文件写的，升级时必须跟着改成 7.3 口径，否则门禁会「测一个不再存在的文件」。

---

## 7. 这一轮做到了什么、没做到什么

**做到了**

1. 固件完整拆解，NPU 驱动三件套（`npu.ko`/`npu-pon.ko`/`fnoffload.ko`/`zx279133-ge.ko`）提取并哈希留档；
2. 逆向出 99 个导出 API、DT 契约、初始化顺序、微码退路；
3. **定位到驱动源码**（`cnjn/immortalwrt` 的 `patches-7.3`），并做 99↔84 的符号级交叉验证；
4. 把 39 个补丁 vendored 进 `kernel-patches/7.3/`（带 `MANIFEST.json` 溯源）；
5. 把逆向产物落进 `pon/npu-reverse/`（API 表、rodata 字符串、函数表、initrd 清单、对方 DTS）；
6. 量化我方 overlay 相对上游的增量（+7900 行 / 11 个文件 + 6 个只在我方的文件）；
7. 指出 **7.3 系列仍带我们已修掉的五个 IDM 实测缺陷** —— 这是升级后第一批要重打的补丁。

**没做到（下一步）**

1. 本地没有落地 linux-7.3.4 树（kernel.org 下载慢，克隆在跑）⇒ **补丁系列能不能干净 apply 尚未验证**；
2. CI 还没切（`KERNEL_URL`/`KERNEL_SHA`/`KVER`/`IW_*` 仍是 6.18 口径）；
3. 我方 overlay **还没有**重基到 7.3 驱动集；
4. 没有编译验证（CI 是本仓库唯一编译通道）。

**风险**

- 对方固件比公开 master 新（PON 钩子那 30 个符号公开树没有）⇒ 以公开 master 为基线会缺一块；
- 7.3 是大版本跳跃，我方 overlay 里 `zx279133-np.c` 的 4052 行增量要重新找落点，
  不是机械 rebase；
- 板子当前处于「有别的会话在用 bench」状态，本轮没有做任何实机验证。

---

## 8. 逆向产物清单（`pon/npu-reverse/`）

| 文件 | 内容 |
|---|---|
| `npu.ko.api.txt` | 逆向得到的 99 个导出符号 |
| `npu.ko.rodata.txt` | `npu.ko` 的 `.rodata` 字符串（含 DT 属性名、错误串、初始化顺序痕迹） |
| `npu.ko.funcs.txt` | `npu.ko` 的函数名表（272 个） |
| `npu-pon.ko.rodata.txt` / `.funcs.txt` | PON 模块的字符串与函数名 |
| `zx279133-ge.ko.rodata.txt` | 直通千兆口模块 |
| `initrd-listing.txt` | initrd 全量清单（1971 条） |
| `fdt-fdt-1.txt` | 对方 `star_sg350h` 的设备树（扁平属性表） |
