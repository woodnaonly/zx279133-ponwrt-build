# 101 — 把 SK-D840N 挪到 Linux 7.3.4：逆向结果、基线验证与升级通道

> 本文承接 `docs/100-the-other-build-npu-driver-and-the-7-3-upgrade-20261122.md`。
> 100 号记录的是「别的人固件」的拆解与驱动定位，本文记录的是**据此动手改仓库**的结果：
> 7.3.4 基线是否成立、我们自己的驱动增量到底有多大、升级通道怎么接、以及哪些东西
> 这一轮**没有**搬过去。

---

## 一、判决

1. **7.3.4 这条基线是成立的，而且已经在本机验证过。** 原生 `linux-7.3.4.tar.xz`
   的 sha256 与 kernel.org 官方清单逐字一致；cnjn 的 39 个 `patches-7.3` 补丁
   在这棵 vanilla 树上 **39/39 全部干净 apply**，无一需要 fuzz 或 reject。
   证据见第三节。
2. **升级不是「换个内核版本号」，是换一整套驱动架构。** 6.18 世代是拆分成
   `np/mac/netdev/main/tm/idm` 的老结构；7.3 世代被重写成**一个组合模块
   `npu.o`**（`main+tm+np+idm+mac+netdev+offload-api+ptp+host+native+meter+qos`）。
   目标固件里的 `npu.ko` 正是这个结构。
3. **我们自己的增量有 171 个函数、约 7900 行，是这次升级的主要成本。**
   其中 **9132 交换表/LED 63 个、PON/OMCI 栈 56 个、RGMII 8 个、实测健壮性修正
   约 44 个**。7.3 公版里**一个都没有对应物**——因为 cnjn 公开的 7.3 树**没有 PON 模块**。
4. **CI 走「外部树」模式，与已验证的 6.18 线同构。** cnjn 自己的 zte target 在
   master 上明确禁止 `CONFIG_EXTERNAL_KERNEL_TREE`（它要 OpenWrt 自己下载底包并打
   `patches-7.3`）；我们反过来：自己准备「vanilla 7.3.4 + 39 补丁」，再把树交给
   OpenWrt。好处是内核字节与补丁字节都被本仓库钉死、可离线复核。
5. **本轮只搬了板级层，没搬驱动增量。** 7.3 通道先跑通「cnjn 的 7.3 驱动 +
   我们的板级层」，我们的 NPU/9132/PON 增量按 IDM → 9132/switch → PON/OMCI
   三片重打，每片一次 CI。这是本轮**最重要的一条未完成项**。

---

## 二、这条线与 6.18 线的关系

| | 6.18 线（现行发布线） | 7.3 线（本文） |
|---|---|---|
| 内核来源 | `cnjn/linux-mainline-zte-zxslc-sr1010` 的 `linux-6.18.38` 子模块 | vanilla `linux-7.3.4.tar.xz`（sha256 钉死） |
| 驱动来源 | 直接在 fork 的核心里（内置式 `zx279133.o`） | `kernel-patches/7.3/` 的 39 个补丁（cnjn `patches-7.3`） |
| 驱动形态 | 拆分模块 np/mac/netdev/main/tm/idm | 组合模块 `npu.o` + `fnoffload.o` + `lan-conduit` + `rtl8372n` |
| 挂接方式 | `CONFIG_EXTERNAL_KERNEL_TREE` | `CONFIG_EXTERNAL_KERNEL_TREE`（同构） |
| OpenWrt 树 | `cnjn/ImmortalWrt` `sr1010-zx279133` @ `1b50aaf4` | `cnjn/ImmortalWrt` `master` @ `31e2ac4` |
| target 配置片段 | `config-6.18` | `config-7.3` |
| 发布 | 发布 release | **不发布**，只出镜像看 |

两条线共用同一个仓库、同一套 `overlays/`（OpenWrt 侧）、同一批 `tests/`。
只有三处按内核代际分叉，本轮全部落地：

* `kernel-overlays/`（内核树覆盖层）——**6.18 专用**，见第五节；
* `overlays-73/target/linux/zte/Makefile`——7.3 线的 zte target；
* `configs/sk-d840n-73.json` + `tools/prepare-sk-kernel-73.py`——7.3 线的锁与准备工具。

---

## 三、证据

### 3.1 目标固件

* `别的人固件/immortalwrt-zte-zx279133-star_sg350h-initramfs.itb`
  sha256 `c6e5010d4b59dbb28d94b6ead8c44d94024c912d761b93fd5186b2d1855fc046`
* initrd 里 `lib/modules/7.3.4/`，`npu.ko` / `npu-pon.ko` / `fnoffload.ko` /
  `zx279133-ge.ko` 四个模块，`npu.ko` 388,632 B，vermagic 7.3.4。

详见 100 号文档第一节，此处不重复。

### 3.2 驱动源码定位

* `cnjn/immortalwrt` master `31e2ac4ded9c8896c7aefd3dcb9a46ef77440fbd`
  （2026-11-10）的 `target/linux/zte/patches-7.3/`，39 个补丁。
* 逆向出的 99 个 `npu.ko` 导出与源码的 84 个 `EXPORT_SYMBOL*` 对照：
  **69 个同名命中**（含逐字一致的 `zte_nppt_ppu_me_diagnostics_get`）；
  **30 个只在逆向里**（PON 钩子：`zx279133_pon_rx_fixup_hook`、
  `zx279133_omcc_gem_id`、`zx279133_tx_pon_control`、`zx279133_pon_flow_gem_hook` …）
  ⇒ **目标固件比公开 master 新**；15 个只在源码里（`zte_nppt_is_wan`、
  `zte_nppt_native_red_usage_get`、`zte_nppt_ppu_me_stats_get`、`zte_nppt_shader_clear` …）。

### 3.3 7.3.4 基线可用性（本机实测）

```
tarball sha256 = b11b6df8295e8e991ff040b71aaa4994a8b55caa23b6b092907f195b5840fd1f
官方   sha256 = b11b6df8295e8e991ff040b71aaa4994a8b55caa23b6b092907f195b5840fd1f
校验: 一致
大小: 162439264 bytes
```

* 官方清单来自 `mirror.nju.edu.cn/kernel.org/linux/kernel/v7.x/sha256sums.asc`
  （即 kernel.org 的镜像，内容与 `cdn.kernel.org` 同源）。
* 下载走南京大学镜像：**3.1 秒 / 51,581 KB/s**。`cdn.kernel.org` 在本机只有
  30–50 KB/s，这是本轮最大的单点提速。
* 补丁 apply：`git apply --check` 后再 `git apply`，**39 个全部干净 apply，0 失败**。
  逐条输出见 `tools/prepare-sk-kernel-73.py` 的实跑日志。
* apply 之后正向校验（不是「git 说 OK」就算数）：树里必须出现
  `zx279133.h`、`zx279133-host.c`、`zx279133-native.c`、`zx279133-offload.c`、
  `arch/arm64/boot/dts/zte/zx279133.dtsi`、`net/dsa/tag_zx279133_rtl8372n.c`，
  少一个就报错退出。

### 3.4 两代驱动集的结构差异

7.3 的 `drivers/net/ethernet/zte/Makefile`（补丁 410 产出）：

```make
obj-$(CONFIG_ZTE_ZX279133_ETH) += npu.o
npu-y := zx279133-main.o zx279133-tm.o zx279133-np.o zx279133-idm.o \
	       zx279133-mac.o zx279133-netdev.o zx279133-offload-api.o \
	       zx279133-ptp.o
npu-y += zx279133-host.o zx279133-native.o zx279133-meter.o zx279133-qos.o
npu-$(CONFIG_ZTE_ZX279133_NATIVE_DEBUG) += zx279133-native-debug.o
obj-$(CONFIG_ZTE_ZX279133_LAN_CONDUIT) += zx279133-lan-conduit.o
obj-$(CONFIG_ZTE_ZX279133_RTL8372N) += zx279133-rtl8372n.o
obj-$(CONFIG_ZTE_ZX279133_FNOFFLOAD) += fnoffload.o
fnoffload-y := zx279133-offload.o
```

与目标固件的 `npu.ko` + `fnoffload.ko` 结构一一对应。

函数级对照（我方 overlay 对 7.3 抽取树，脚本 `tools/func-surface-diff.py`）：

```
ours: ci-build-repo/kernel-overlays/drivers/net/ethernet/zte
theirs: linux-7.3.4/drivers/net/ethernet/zte
functions: ours=327 theirs=367 common=156 only_ours=171 only_theirs=211
```

### 3.5 我们自己的增量清单（171 个函数）

| 板块 | 函数数 | 主要文件 | 7.3 公版有没有 |
|---|---|---|---|
| 9132 交换表 / 端口 / 洪泛 / MF 镜像 | 24 | `zx279133-np.c` | 没有 |
| 9132 交换芯片 / LED / MDIO / XPCS | 39 | `zx279133-switch.c` | 没有 |
| PON / OMCI 栈（PLOAM MIC、T-CONT、GEM、OMCI 队列、AES） | 56 | `zxpon.c`、`zx279133-pon.c`、`zx279133-omci.c` | 没有 |
| RGMII 调试 | 8 | `zx279133-rgmii.c` | 没有 |
| IDM 实测修正（BP 环配置、RX 头定位、广播查找） | 6 | `zx279133-idm.c` | 没有 |
| TX 恢复 / phylink 生命周期 / LAN 控制 | 12 | `zx279133-netdev.c` | 没有 |
| BMU 阈值 / RED 网关 / vendor 重配 / 诊断转储 | 10 | `zx279133-main.c` | 没有 |
| SOPC 就绪 / XPCS 设备 / XMAC 复位掩码 | 6 | `zx279133-mac.c` | 没有 |

7.3 公版**没有 PON 模块**（`npu-pon.ko` 在目标固件里有、公开树里没有），
这意味着：我们自研的 PON/OMCI 栈不是「被 7.3 取代的东西」，而是**站在公开树
前面**的东西。

### 3.6 板级层重基结论

* **DTS 的 27 个 label 引用全部可解析**：14 个来自 7.3 的 `zx279133.dtsi`
  （`gpio0-4`、`mdio0/1`、`nppt`、`pon_serdes`、`pvt`、`spifc`、`uart0`、
  `uni_serdes`、`usb3`、`watchdog0/1`、`xpcs0/1`），其余 13 个在我们自己的板级
  DTS 里定义。7.3 的 `zx279133.dtsi` 相对 6.18 改了 CPU 策略
  （固定 2.0 GHz PLL → OPP 表 + PWM 调压，最高 1.4 GHz）、topcrm 增加了
  `cpu-pll` 寄存器窗口、PWM 由 `disabled` 改 `okay`——**这些改动不影响我们的板级文件**。
* **DSA tagger 两代逐字节一致**：`net/dsa/tag_zx279133_rtl8372n.c` 在 6.18 树
  与 7.3 树里都是 2,774 B。我们把 `include/net/dsa_zx279133_rtl8372n.h` 的
  `USER_PORT_MAX` 从 7 提到 8（9132 的第五个 UNI 口要 transport VID 63）
  这个改动在 7.3 上照样成立。
* **Unim NAND 接线前提成立**：7.3 树里 `drivers/mtd/nand/spi/{core.c,Makefile}`
  与 `include/linux/mtd/spinand.h` 都在，而 `scripts/configure-sk-d840n.sh`
  对 `unim.o` 的接线是**在构建时改这三个文件**，与内核代际无关。
* **NPU 微码要放进内核树**：`config-7.3` 里
  `CONFIG_EXTRA_FIRMWARE="zte/zx279133/mcode_intel.bin"` +
  `CONFIG_EXTRA_FIRMWARE_DIR="firmware"`，内核按自己的源码树解析，所以
  `mcode_intel.bin` 必须落在准备好的树里（cnjn 是在他的 `Kernel/Prepare` 里拷的，
  我们在自己的脚本里拷）。

---

## 四、CI 通道怎么接

### 4.1 为什么不是 OpenWrt 原生模式

cnjn master 的 `target/linux/zte/Makefile` 写着：

```make
ifneq ($(DUMP),1)
  ifneq ($(call qstrip,$(CONFIG_EXTERNAL_KERNEL_TREE))$(call qstrip,$(CONFIG_KERNEL_GIT_CLONE_URI)),)
    $(error The ZTE target uses its Linux 7.3.1 patch series. Clear CONFIG_EXTERNAL_KERNEL_TREE and CONFIG_KERNEL_GIT_CLONE_URI)
  endif
endif
```

原生模式下 OpenWrt 自己下载 7.3.1、自己打 `patches-7.3`。我们不走这条路，
理由有三条：

1. **可离线复核。** 底包字节、补丁字节、补丁顺序、OpenWrt 元数据，全部被本仓库
   的锁文件钉死；本机不装 arm64 工具链也能把整棵树准备出来（已实测）。
2. **不依赖别人的 OpenWrt 语义。** `include/kernel-defaults.mk` 里
   `CONFIG_EXTERNAL_KERNEL_TREE` 分支会 `ln -s` 外部树并**跳过 target 补丁**，
   于是「谁打补丁」是我们说了算，不会出现两边都打或者都没打。
3. **版本口径干净。** cnjn 在 `include/kernel.mk` 里专门为
   `BOARD:zte KERNEL_PATCHVER:7.3` 留了
   `LINUX_UNAME_VERSION:=$(LINUX_VERSION)`——外部树模式下
   `$(LINUX_DIR)/include/config/kernel.release` 此刻还不存在，这条分支正是为此
   而写。我们的 target Makefile 保持 `KERNEL_PATCHVER:=7.3`，正好落进这个分支。

### 4.2 一次构建的完整链路

```
configs/sk-d840n-73.json          锁：7.3.4 / tarball sha256 / 镜像地址 / 上游 commit
        │
tools/prepare-sk-kernel-73.py     下载 → 校验 sha256 → 解包 → 逐个 apply 39 补丁
        │                         → 正向校验产物 → 改写 openwrt 的 kernel-7.3 元数据
        │                         → 写 kernel-provenance-73.json
        ▼
linux-7.3.4/                      vanilla + cnjn patches-7.3
        │
tools/apply-sk-overlay-73.sh      板级层：板级 DTS + Unim NAND + DSA VID 范围
        │                         + mcode_intel.bin；并断言 6.18 驱动文件没有混进来
        ▼
overlays/ + overlays-73/           OpenWrt 侧（target Makefile 换 7.3 版）
        │
scripts/configure-sk-d840n.sh     复用现有配置脚本（只把一处硬编码的 config-6.18
                                  改成版本无关的 $KCFG）
        ▼
CONFIG_EXTERNAL_KERNEL_TREE=<树>   OpenWrt 把这棵树当自己的内核目录
```

### 4.3 幂等与「树被别的东西改过」

工具用**标记文件** `.sk-series-applied.json` 判定幂等，**不用**「反向 apply 是否成功」
来判——因为系列里 450 与 460 两个补丁都改 `net/netfilter/nf_flow_table_offload.c`，
后者会让前者反向失败，用反向判据会把一棵完整的树误判成半成品。
若树里已经有驱动却没有标记文件，工具**拒绝**再打一遍并说明原因。

---

## 五、本轮落地的文件

| 文件 | 作用 |
|---|---|
| `configs/sk-d840n-73.json` | 7.3 线的锁：版本、tarball sha256/大小、镜像、上游 commit、OpenWrt 元数据改写口径 |
| `tools/prepare-sk-kernel-73.py` | 准备 7.3.4 树：下载校验 → 解包 → 逐补丁 apply → 正向校验 → 改写 OpenWrt 元数据 → 写 provenance |
| `tools/apply-sk-overlay-73.sh` | 把板级层拷进 7.3 树，并**显式排除**6.18 世代驱动文件；拷完逐条断言正反两面 |
| `overlays-73/target/linux/zte/Makefile` | 7.3 线的 zte target（`KERNEL_PATCHVER:=7.3` + 外部树要求） |
| `.github/workflows/build-sk-d840n-73.yml` | 7.3 通道：手动触发 + 7.3 相关路径推送触发；只出镜像，不发布 |
| `scripts/configure-sk-d840n.sh` | 一处改动：把硬编码的 `target/linux/zte/zx279133/config-6.18` 换成版本无关的 `$KCFG` |
| `kernel-patches/7.3/` | cnjn 的 39 个补丁的 vendored 副本 + `MANIFEST.json`（上游 repo/branch/commit/date + 每补丁 sha256/bytes） |
| `kernel-patches/7.3-local/` | **我们自己的**补丁，applied 在 vendored 系列之后；当前是 IDM 实测修正那一片 |
| `tools/func-surface-diff.py` | 两代驱动的函数面对照，输出「只在我们这边」与「只在 7.3 那边」两张工作清单 |
| `pon/npu-reverse/` | 逆向产物留档：99 个 API、rodata、函数表、initrd 清单、目标 DTS |

**没有动的东西**：`kernel-overlays/`（6.18 世代驱动，一行未改）、`overlays/`（OpenWrt 侧，
一行未改）、现行 `build-sk-d840n.yml`（发布线一行未改）。

---

## 六、未做 / 风险

1. **我们自己的 171 个函数一个都没搬。** 7.3 通道目前是「cnjn 的 7.3 驱动 + 我们的板级层」。
   后果是明确的：**四个 GE LAN 口（9132 交换芯片）在 7.3 上没有驱动**
   （cnjn 的 `zx279133-rtl8372n.c` 是给 SR1010 的 RTL8372N 用的，我们的板级 DTS 本来
   就把 SR1010 的 DSA 节点去掉了），**PON/OMCI 也没有**。7.3 线目前只能验证
   NPPT WAN 路径与 2.5G 口。
2. **没有编译验证。** 本机无 arm64 交叉工具链，编译只能走 CI；`build-sk-d840n-73.yml`
   是**第一次**运行，结果未知。
3. **DTS 没有在本地编译过。** 本机没有 `dtc`，只能用「label 可解析 + 属性存在」
   做静态核对。CI 里有 dtc 门禁，第一次跑就知道结果。
4. **驱动是模块而非内置。** 7.3 的 `config-7.3` 里 `CONFIG_ZTE_ZX279133_ETH=m`，
   而 6.18 线是内置。模块化意味着 `npu.ko` 要被打包并在启动时加载，
   `kmod-*` 依赖链需要在 7.3 线上重新确认（cnjn 的 `target.mk` 没有列 kmod，
   依赖可能来自 OpenWrt 的自动 kmod 推导）。
5. **目标固件的 PON 模块仍未公开。** 我们逆向出的 30 个 PON 钩子导出只在二进制里；
   等 cnjn 公开 `npu-pon.ko` 源码，我们自研的 PON/OMCI 栈要么对齐、要么让位——
   这个决策应该在 7.3 的 NPU 驱动跑通之后再做。

---

## 七、7.3 通道的 CI 首跑记录

7.3 通道第一次运行（run #1）的结果与处理，按时间顺序：

| 阶段 | 结果 |
|---|---|
| `repository gates` | ✅ 通过 |
| `initramfs` / `flash` 的 `Configure` | ❌ 失败：`Missing SK-D840N target kernel config` |
| 本机复现 | ✅ 复现成功（用准备好的 7.3 树 + IW master 的稀疏检出 + 我们的 overlays 跑同一个脚本） |
| 根因 | `scripts/sk-d840n-offload.sh` 把三个配置片段名写死成 `config-6.18` |
| 修法 | 系列号从**内核树自己**的 `VERSION`/`PATCHLEVEL` 读出来；两个系列都验证过（7.3 树 → 7.3，6.18 fork → 6.18，发布线行为不变） |
| run #2 | 待观察 |

这条记录本身就是这套门禁存在的理由：**首跑失败点由一个断言精确指出，
而不是让构建跑到一半才炸**。本机没有 arm64 工具链，但 Configure 这一步
只改配置片段、不编译，所以它能在本机完整复现——这让修一轮的成本从
「一次 CI（约两小时）」降到「一次本地脚本运行」。

---

---

## 八、下一步（按片重打，每片一次 CI）

**已落地：第 1 片（IDM 实测修正）的寄存器部分**，见
`kernel-patches/7.3-local/100-sk-idm-measured-fixes.patch`：

* 控制字：bit 24 打开（`zx279133_idm_cfg_bit24` 0 → 1）、bit 13 去掉；
* `+0x0010` = 0x40（`g_idm_rx_bp_retev_num_th`，来自原厂 DT）；
* `+0x0014..+0x0034` 路由字 = 0x00010001；
* `+0x0038` = 0x00001000；`+0x0070` = 深度 − 0x18（24 个队列头）、
  `+0x0074/+0x0090/+0x0094` = 0；
* 中断掩码 `+0x0040` = 0，IRQ 路由 `+0x44/+0x48/+0x4c/+0x50` 照抄厂商
  `sg_idm_irq_mask_conf(0x71ffbfb 0x200000 0x400000 0x800404)`，
  `+0x006c` = 0x800；
* `+0x0490` = 0、`+0x2c8` 字段 = 0x12、`+0x0124` = 0x80、`+0x00bc` = 0；
* 头文件 `ZX279133_IDM_TX_CFG_DEPTH` 0 → 0x400。

打上之后，本机逐条比对确认：**两代之间剩下的写入差异只剩 BP 池布局**
（`+0x0108` 的 jumbo 池基址、`+0x010c/+0x0110` 的按深度环大小）。

**没搬的，按片来：**

1. **IDM 的 RX 缓冲池与投递协议**（第 1 片的剩余部分）。这不是改常量：
   * 7.3 树只有**一个** coherent 缓冲数组（`FREE_RING_SIZE` = 0x4000 项），
     硬件看到的是 4 个 free ring（`+0x108/+0x118/+0x11c/+0x408`），
     环下标按 `BP_RING_SIZE`（4096）取模，池大小寄存器写死 0x3d00；
   * 6.18 世代是**两块**（`BP_RING_SIZE` 8192 + `JUMBO_BP_RING_SIZE` 4096，
     一次分配、jumbo 紧跟 normal），硬件看到的是 normal 池在 `+0x104`、
     **jumbo 池在 `+0x108`**、3 个 free ring 在 `+0x118/+0x11c/+0x408`，
     池大小按**环深度**算（normal 0x203d00 / jumbo 0x3d00，与厂商板实测一致）；
   * 更要紧的是**投递协议**不同：厂商的 `idm_new_bp_update()` 每 1024 个
     缓冲写一次 0x400 脉冲，再把余数按「normal 在 bits 15:0、jumbo 在
     bits 31:16」写进同一个寄存器——写数量本身什么也没 announcing，
     而数量是 1024 的倍数等于 announcing 零个缓冲。7.3 的实现没有这一套。
   所以这一片是**重写 RX 缓冲池路径**，不是移植几个常量。
2. **IDM 的其余重写**（RX page 跟踪、XSK、校验和判定、TX 回收等，约 78 个 hunk）。
   这一块比我们 overlay 与 6.18 fork 的差异还大，属于驱动重写而不是移植。
3. **9132 交换表 / switch.c**（约 63 个函数）。这是 SK-D840N 四个 GE 口能不能活的问题。
   需要把 6.18 世代的 `zx279133_9132_*` 挂到 7.3 的 `native.c`/`host.c` 结构上。
4. **PON / OMCI**（约 56 个函数）。工作量最大，且和第 5 条「cnjn 会不会公开
   npu-pon.ko」强相关。
5. **RGMII 调试**（8 个函数）与 **netdev/main/mac 的健壮性修正**（约 28 个函数），
   随 2~4 一起带。

验收口径与 6.18 线一致：**CI 绿 + 门禁脚本全过 + 实机启动后
`/proc/net/dev` 有预期网口、`ethtool -S` 计数在涨**。

---

## 九、复现本文结论的命令

```sh
# 1) 7.3.4 底包 + 39 补丁（本机已跑通，约 3 分钟）
python3 ci-build-repo/tools/prepare-sk-kernel-73.py \
        --tree linux-7.3.4 --version 7.3.4 \
        --openwrt <openwrt-checkout> --report kernel-provenance-73.json

# 2) 板级层拷进 7.3 树
sh ci-build-repo/tools/apply-sk-overlay-73.sh linux-7.3.4

# 3) 函数级迁移面对照
python3 ci-build-repo/tools/func-surface-diff.py \
        ci-build-repo/kernel-overlays/drivers/net/ethernet/zte \
        linux-7.3.4/drivers/net/ethernet/zte
```
