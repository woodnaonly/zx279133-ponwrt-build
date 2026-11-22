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
| `kernel-patches/7.3-local/` | **我们自己的**补丁，applied 在 vendored 系列之后；当前是 IDM 实测修正三片（`100` 寄存器值、`110` RX 帧落点、`120` BP 投递协议）**加第 3 片的 `130` LAN 控制事务** |
| `kernel-overlays-73/drivers/net/ethernet/zte/` | **7.3 线专有的驱动树**：9132 交换表驱动本体 + `zx279133-9132.h`（9132 自己的寄存器图）+ `zx279133-lan1-led.h`。与 6.18 世代的 `kernel-overlays/` 并存，互不覆盖（见八之三） |
| `tools/check-sk-switch-api.py` | 第 3 片的编译前门禁：驱动用到的名字、内核 API、**LAN 服务契约**、构建接线，四项逐条核对（见八之三.3） |
| `tests/test_sk_workflows.py` | 工作流自身的门禁：每个工作流文件可解析、7.3 线依赖的步骤都在、门禁点名的测试文件都存在、7.3 线的路径过滤覆盖这条线的输入（见 7.9） |
| `overlays-73/target/linux/zte/zx279133/target.mk` | 7.3 线的 subtarget 默认包：**清空 OpenWrt 的 `DEFAULT_PACKAGES.tweak`**，否则其中的 `luci` 元包会拉进 `luci-theme-bootstrap`（见 7.8） |
| `tools/func-surface-diff.py` | 两代驱动的函数面对照，输出「只在我们这边」与「只在 7.3 那边」两张工作清单 |
| `tools/idm-register-diff.py` | 以 `writel(...)` 语句为单位逐条对比两代的寄存器写入 |
| `pon/npu-reverse/` | 逆向产物留档：99 个 API、rodata、函数表、initrd 清单、目标 DTS |

**没有动的东西**：`kernel-overlays/`（6.18 世代驱动，一行未改）、`overlays/`（OpenWrt 侧，
一行未改）、现行 `build-sk-d840n.yml`（发布线一行未改）。

---

## 六、未做 / 风险

1. **我们自己的 171 个函数只搬了第 1~3 片。** 7.3 通道目前是「cnjn 的 7.3 驱动 +
   我们的板级层 + IDM 三片 + 9132 交换表驱动」。
   后果是明确的：**PON/OMCI 还没有**（cnjn 的树里没有 `zx279133-pon.c`、
   `zxpon.c`、`zx279133-omci.c`），7.3 线目前只能验证 NPPT WAN 路径、
   2.5G 口与（待编译验证的）9132 LAN 口。
   **9132 驱动本身还没有被编译过**（本机无交叉工具链），实机对拍是它之后的下一道。
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

## 七、7.3 通道的 CI 运行记录

### 7.1 run #1（2026-11-22 09:50Z）

| 阶段 | 结果 |
|---|---|
| `repository gates` | ✅ 通过 |
| `initramfs` / `flash` 的 `Configure` | ❌ 失败：`Missing SK-D840N target kernel config` |
| 本机复现 | ✅ 复现成功（用准备好的 7.3 树 + IW master 的稀疏检出 + 我们的 overlays 跑同一个脚本） |
| 根因 | `scripts/sk-d840n-offload.sh` 把三个配置片段名写死成 `config-6.18` |
| 修法 | 系列号从**内核树自己**的 `VERSION`/`PATCHLEVEL` 读出来；两个系列都验证过（7.3 树 → 7.3，6.18 fork → 6.18，发布线行为不变） |

### 7.2 run #2（2026-11-22 09:59Z，head `3dbf3003`）

| 阶段 | 结果 |
|---|---|
| `repository gates` | ✅ 通过 |
| `Prepare the Linux 7.3.4 tree` | ✅ 通过（39 个 vendored 补丁 + 1 个本地补丁，CI 上干净 apply） |
| `Apply the board layer` | ✅ 通过（板级 DTS / Unim / DSA 头 / 微码全部落地） |
| `Apply the OpenWrt-side overlays` | ✅ 通过 |
| `Enable /dev/mem in the 7.3 fragments` | ✅ 通过 |
| `Check the overlay for what a compiler would have caught` | ✅ 通过 |
| `initramfs` / `flash` 的 `Configure` | ❌ 失败：`Argon is not built into the resolved SK-D840N config` |

**判决**：run #2 把失败点从「配置片段名写死」推进到了「defconfig 之后的
主题断言」。前六步全绿说明**树准备与板级层重基在 CI 上是成立的**；
剩下的失败与 7.3 内核无关，是 OpenWrt 侧少了一个步骤。

**根因**：`jerrykuku/luci-theme-argon` 的 Makefile 最后一行是

```
include $(TOPDIR)/feeds/luci/luci.mk      # Makefile:54
```

7.3 工作流**没有「Install feeds」这一步**（6.18 发布线有，
`build-sk-d840n.yml:782`），于是 `feeds/luci/luci.mk` 不存在 ⇒ Argon 的
配方无法解析 ⇒ 这个包不进包数据库 ⇒ `make defconfig` 把
`CONFIG_PACKAGE_luci-theme-argon=y` **静默丢弃**（和「符号不存在」的
处理方式一样）⇒ 主题断言报错。

CI 警告里还留着同一件事的另一个侧面：

```
WARNING: Makefile 'package/emortal/default-settings/Makefile' has a dependency on 'luci', which does not exist
```

`luci` 不存在 = luci feed 没装 = Argon 依赖的 `luci-base` 也不存在，
即使 `luci.mk` 在也选不上。

**修法**：在 7.3 工作流里补上与 6.18 线同构的 `Install feeds` 步骤
（`feeds update -a` + `feeds install -a`），位置放在门禁之后、Configure
之前，并对 `feeds/luci/luci.mk` 与 `package/feeds/luci/luci-base/Makefile`
做存在性断言。

### 7.3 这一轮暴露的方法论错误：本机复现的边界被高估了

run #1 的「本机复现成功」是**假通过**。本机复现环境
`scratch/ow73run` 里没有 `feeds/`、也没有 `package/luci-theme-argon`，
但它的 `.config` 里**仍然留着** `CONFIG_PACKAGE_luci-theme-argon=y`——
因为本机没有 `make`，`make defconfig` 根本没跑，那行配置也就从没被
丢弃过。本机复现只走到了 `defconfig` **之前**。

所以边界要写清楚：

| 本机可复现 | 本机不可复现 |
|---|---|
| `Prepare` / `Apply board layer` / `Apply overlays` / `Enable /dev/mem` / `Check the overlay` | `make defconfig` 及其之后的**全部**断言 |
| 任何「改配置片段」的脚本逻辑 | **符号不存在 → 被静默丢弃**这一类行为 |

`defconfig` 丢弃无效符号时不出一声，所以「本地 .config 里还有那行」
和「CI 上那行不见了」之间的差别，本机原理上看不见——除非先装上 feeds
再跑 defconfig，而 defconfig 需要 make。

这条边界就是「失败点由断言精确指出，而不是让构建跑到一半才炸」的价值
所在：run #2 的报错落在 `skd840n_check_theme_config` 这一句断言上，
而不是七十多分钟后的某个编译错误。

### 7.4 拿日志的手段（无 token 的情况）

本机没有 GitHub token，实测：

* `GET /actions/jobs/<id>/logs` → **403**；
* `GET /actions/artifacts/<id>/zip` → **401 Requires authentication**；
* `GET /commits/<sha>/check-runs` → **可读**（偶发 504，重试即可）；
* `GET /check-runs/<id>/annotations` → **可读**，工作流里
  `echo "::error::$message"` 的正文就在这里。

所以失败诊断走「check-run → annotations」，不是日志下载。

---

### 7.5 run #3：feeds 修复生效，失败点前进了

**判决**：`Install feeds` 那一补**是对的**。run #3 的 Configure 仍然失败，
但**报错换了**，而且是从「包根本没进来」变成「包进来了但多了一个」：

| | run #2 | run #3 |
|---|---|---|
| Configure 的最后一句 | `Argon is not built into the resolved SK-D840N config` | `Only Argon may be selected in the SK-D840N config` |
| 含义 | Argon 的配方**没被解析**，`feeds/luci/luci.mk` 根本不存在 | Argon **已经**在 `.config` 里了，但 **bootstrap 主题也在** |

也就是说 run #3 越过了「Argon 不存在」这一关，撞上了下一关：
`.config` 里同时有 `CONFIG_PACKAGE_luci-theme-argon=y` 和
`CONFIG_PACKAGE_luci-theme-bootstrap=y`，而断言要求**只有** Argon。

**已经排除的路径**（都查过，都不成立）：

| 怀疑 | 查证 | 结论 |
|---|---|---|
| master 的 `DEVICE_PACKAGES` 注入了 bootstrap | `target/linux/zte/image/Makefile` 里确实有 `DEVICE_PACKAGES := ... luci-theme-bootstrap ...`，但我们的 overlay 用自己的 image/Makefile **覆盖**了它——CI 的断言 `grep -q 'Device/skyworth_sk-d840n' openwrt/target/linux/zte/image/Makefile` 就是覆盖生效的证据 | 排除 |
| `skd840n_select_theme` 的 sed 没生效 | 它的第二行 `s/^(CONFIG_PACKAGE_luci-theme-[^=[:space:]]+)=[ym]$/...` 对 `CONFIG_PACKAGE_luci-theme-bootstrap=y` 是匹配的，第三行还额外追加了 `# CONFIG_PACKAGE_luci-theme-bootstrap is not set` | 排除 |
| 两线的初始 `.config` 不同 | master 与 sr1010-zx279133 分支的 `configs/zte_zx279133_sr1010_initramfs.config` **都**有 `CONFIG_PACKAGE_luci-theme-bootstrap=y`（master 在第 25 行） | 排除 |
| `skd840n_select_theme` 第一行 sed 替换的主题名来源不同 | **这才是两线的差别**：6.18 线分支的 `target/linux/zte/zx279133/target.mk` 里有 `DEFAULT_PACKAGES += ... luci-theme-bootstrap`，那一行 sed 把它改成 argon，于是 defconfig 选 argon；**master 的 target.mk 里没有 `DEFAULT_PACKAGES`**，那条 sed 无事可做 | 部分成立，但这只能解释「argon 没被自动选上」，解释不了「bootstrap 又变回 y」 |
| 某个被选中的包 select 了 bootstrap | **已排除**：把两个 luci 树（`immortalwrt/luci@ed7692cb` 与 `fxfall/luci@d438535`，各 243 个构建文件）完整扫了一遍，提到 `luci-theme-bootstrap` 的**只有四处**，而且**两个树完全一样**：`collections/luci-layer2`、`collections/luci-light`、`collections/luci-nginx` 依赖它，加上它自己的定义。依赖链是 `Package/luci`（元包，标题带 "(default)"）→ `+luci-light` → `+luci-theme-bootstrap` | 链存在，但**两线一样**，所以它不是两线的差异源 |

**还缺的一环**：defconfig 之后 `.config` 里**到底哪些 luci 包被选中**。
这正是断言报错时看不见的东西——工作流只取日志尾部 65 行，看不到全貌。

**修法（诊断先行）**：把 `skd840n_check_theme_config` 的报错补成打印**全部**
`CONFIG_PACKAGE_luci*` 行。这两行 grep 能一次分开两种情况：

* 打印出来**只有** bootstrap ⇒ 它没被 unset 掉，是 sed 没作用到它；
* 打印出来**还有** `luci` / `luci-light` ⇒ 是依赖链在 defconfig 期间把它拉了回来，
  要从「谁选了 luci 元包」入手。

修法确定之后，下一轮 CI 就能带上决定性的那一行输出。

---

### 7.6 板级层的编译前提：本机预检（在 Compile 之前）

**判决**：板级层在 7.3 树上的内核 API 前提**全部满足**，本机可查的范围内
没有编译风险；但**真正的编译只能在 CI 里发生**。

**为什么要单独做这一步**：本机没有 arm64 交叉工具链，`Compile` 一失败就是
一轮七十分钟。而板级层（板级 DTS / Unim NAND / DSA 头）是这次唯一**由我们
新增**、且**在 7.3 公版里不存在**的代码——公版驱动集编译得再干净，也不能
替我们保证这几个文件。

**工具**：`tools/check-sk-board-api.py`，两个阶段。

| 阶段 | 跑在什么时候 | 查什么 |
|---|---|---|
| `pre` | 树备好、overlay 还没拷进去 | 板级层要用的每个宏/类型/访问器在树里存在且形态正确；记下 cnjn 公版 DSA 头的宏值 |
| `post` | overlay 拷完之后 | 板级文件已落位；DTS 的 include 与 label 全可解析；tagger 用到的宏我们的头里都有；宏值与公版逐个对照 |

**本机结果**：两阶段 exit=0。

**证据（关键的几条，都是会咬人的）**：

* **7.3 把 `include/linux/nand_dev.h` 删了**，它的内容并进了
  `include/linux/mtd/nand.h`。`unim.c` 调用的 `nanddev_get_ecc_conf()`
  现在在 `mtd/nand.h:634`，芯片条目用的 `NAND_MEMORG` / `NAND_ECCREQ`
  也在 `mtd/nand.h:41` / `:199`。它们可见**只因为**
  `include/linux/mtd/spinand.h:5` 包含了 `mtd/nand.h`——这条包含链是契约，
  所以工具查它，而不是假定它。
* `struct spinand_manufacturer` 在 **spinand.h**（不是 nand.h），
  字段 `id / name / chips / nchips / ops` 齐全，我们 overlay 的 `.id/.name/
  .chips/.nchips/.ops` 五个赋值都有对应。
* **7.x 把 DSA tagger 放在 `net/dsa/`**，不是 6.x 的 `drivers/net/dsa/`。
  `net/dsa/tag_zx279133_rtl8372n.c` 与 6.18 树**逐字节一致**
  （2774 B，sha256 前 16 位 `CEF5DB6538A1C789`）⇒ tagger 本身不用移植。
* **cnjn 公版已经带 `include/net/dsa_zx279133_rtl8372n.h`**（303 B，
  sha256 前 16 位 `1E5AFBCD378A1E88`，与 6.18 树那份逐字节一致），
  我们的 overlay 会**覆盖**它。宏名相同 ⇒ 不会编译不过；宏值有一处**有意**
  不同：

  | 宏 | 公版 | 我们 | 出处 |
  |---|---|---|---|
  | `ZX279133_RTL8372N_USER_PORT_MIN` | 4 | 4 | 相同 |
  | `ZX279133_RTL8372N_USER_PORT_MAX` | **7** | **8** | docs/92 §90：2.5G jack 是 9132 的第 5 个 UNI 口（DSA port 8），transport VID 55+8=**63**，超出公版的 55+7=62，公版会把这帧丢掉 |
  | `ZX279133_RTL8372N_TRANSPORT_VID_BASE` | 55 | 55 | 相同 |

  这正是 `post` 阶段要把两边逐个对照的原因：改这个值会改 tagger 在运行期
  接受哪些 VID，**不能靠编译通过来判定它对**。
* 板级 DTS 引用 13 个 label，在 7.3 的 `zx279133.dtsi` 里**全部可解析**；
  三个 `#include` 的 dt-bindings 头都存在；dtb 进了
  `arch/arm64/boot/dts/zte/Makefile`。

**未做**：

* 没有真正编译——本机没有 arm64 工具链，`Compile` 只能在 CI 里发生。
* `unim_spinand_manufacturer` 往 `spinand_manufacturers[]` 的登记**不在
  这个工具的检查范围**：那是 `scripts/configure-sk-d840n.sh` 在树备好之后
  做的三处编辑（`spinand.h` 的 extern 声明、`core.c` 的表项、Makefile 的
  `spinand-objs += unim.o`），脚本自己断言了这三处，所以在它改的地方查
  它就够了。

---

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

### 7.7 一次改动打红了 6.18 发布线（已修）

**判决**：6e6992b2（系列号改为从内核树推导）**打红了 6.18 发布线**，
run 45010155031（head `3dbf3003`）在「Rust 1.96.1 and offline ONU
regression tests」这一步就红了，`Configure` 之后的步骤全被跳过。
原因已定位并修好。

**现象**：

```
FAIL: test_real_fragment_and_build_root_merge_is_idempotent (__main__.OffloadTests)
AssertionError: 1 != 0 : Missing SK-D840N target kernel config
```

**根因**：那个提交把 `skd840n_pin_offload_fragments` 的系列号来源从
写死的名字改成**内核树**，并把内核树设成了必填的第三个参数：

```sh
sk_off_series=$(skd840n_target_config_series "$sk_off_tree" "$sk_off_kernel") || {
	echo 'Missing SK-D840N target kernel config' >&2; return 1; }
```

而仓库自己的门禁测试是这么调它的：

```sh
skd840n_pin_offload_fragments "$2" "$3"     # 只有两个参数
```

**没有内核树** ⇒ 推导失败 ⇒ 报「缺 SK-D840N 内核配置」。
真实构建路径一直带着内核树，所以构建本身没坏，坏的是门禁测试——
而门禁测试恰恰是发布线上的**第一道**关卡。

**修法**：内核树仍然是首选来源，但不再是唯一来源。新增回退
`skd840n_target_config_series_from_fragments()`：没有内核树时，从
**subtarget 片段**（`target/linux/*/*/config-<series>`）取系列号。

回退**不能用 `target/linux/generic`**：cnjn master 的 generic 目录里
`config-6.12`、`config-6.18`、`config-7.3` **三个系列并存**，从那里取
是三选一的猜测。subtarget 目录只有一个（master 的 `zx279133/` 里
只有 `config-7.3`），万一出现多于一个，回退**拒绝而不是猜**。

**证据**（本机四条路径全部验证）：

| 场景 | 结果 |
|---|---|
| 内核树 = 7.3.4 | `7.3` |
| 内核树 = 6.18 fork | `6.18` |
| 无内核树，subtarget 一个片段 | `6.18` |
| 无内核树，subtarget 两个候选 | **拒绝**（不猜） |

`tests/test_sk_offload.py` 回到 10/10 通过（本地与 CI 同一份测试）。

**教训**：改一个**共享**脚本时，它的调用方不止构建路径——**仓库自己的
门禁测试也是调用方**。而门禁测试跑在发布线的最前面，所以它在发布线
上的优先级高于任何构建步骤。

---

### 7.8 run #4：主题残留的根因，和一次被推翻的「排除」

run #4（45012472909，head `1581854a`）在 Configure 失败，但这次的
**诊断输出是决定性的**：

```
.config:30:warning: override: TARGET_zte_zx279133_DEVICE_skyworth_sk-d840n changes choice state
#
# configuration written to .config
#
CONFIG_PACKAGE_luci-theme-bootstrap=y
Only Argon may be selected in the SK-D840N config
--- the themes in the resolved config ---
CONFIG_PACKAGE_luci-theme-argon=y
CONFIG_PACKAGE_luci-theme-bootstrap=y
--- the LuCI packages in the resolved config ---
CONFIG_PACKAGE_luci=y
CONFIG_PACKAGE_luci-light=y
...
```

**判决**：resolved config 里同时有 argon 和 bootstrap，**而且有 `luci`
元包和 `luci-light`**。这两条是 bootstrap 的来源。

**根因（这次查到底了）**：

```
include/target.mk（OpenWrt 自己，两个分支都有）：
  # For easy usage
  DEFAULT_PACKAGES.tweak:=
  	block-mount \
  	default-settings-chn \
  	kmod-nf-nathelper \
  	luci              ← LuCI 元包
```

而 luci 树里：

```
collections/luci        LUCI_DEPENDS:= +luci-light +luci-app-package-manager
collections/luci-light  LUCI_DEPENDS:= ... +luci-theme-bootstrap ...
```

**⇒ `luci`（默认包）→ `luci-light` → `luci-theme-bootstrap`。**

**为什么 6.18 线没有这个问题**：cnjn 的 `sr1010-zx279133` 分支在
`target/linux/zte/zx279133/target.mk` 里**清空**了 tweak 列表并给了自己
的清单：

```make
DEFAULT_PACKAGES.tweak:=
DEFAULT_PACKAGES += \
	dnsmasq ethtool ip-full \
	luci-base luci-mod-admin-full luci-theme-bootstrap \
	uhttpd uhttpd-mod-ubus \
	openssh-sftp-server
```

里面的 `luci-theme-bootstrap` 被 `scripts/sk-d840n-theme.sh` 的 sed 改成
argon，所以 6.18 线的 resolved config 里只有 argon。

**而 7.3 线构建自 cnjn 的 master，它的 `zx279133/target.mk` 根本没有
tweak 清单** ⇒ 上游默认的 `luci` 留下 ⇒ 主题检查失败。

**⇒ 修法**：7.3 线补一个 `overlays-73/target/linux/zte/zx279133/target.mk`，
照 6.18 的做法清空 tweak 并给出自己的清单（主题直接写 argon，不依赖 sed）。
CI 里加两条断言：overlay 文件里必须有 `DEFAULT_PACKAGES.tweak:=`；
resolved config 里**不得**有 `CONFIG_PACKAGE_luci=y`（否则主题检查可能是
因为错误的原因才通过的，镜像里会同时装两个主题）。

**这一轮被推翻的「排除」**：上一轮记过「已排除 luci feed 依赖差异
（两个 luci 树各 243 个构建文件，提到 bootstrap 的只有 4 处且两树完全
一样）」。**这个排除是错的**：

* 243 这个数字来自 **GitHub trees API 的截断**（recursive 查询有截断，
  脚本没有检查 `truncated` 标志）。用 `git clone` 取完整树后是
  **260 个构建文件**，多出来的那几个正是 cnjn 自己加进 fxfall/luci 的
  包（`luci-app-passwall`、`luci-app-rclone` 等）。
* 而且「两树完全一样」这个结论本身也不成立：两棵树的 `collections/luci`、
  `collections/luci-light`、`themes/luci-theme-bootstrap` 和 `luci.mk`
  **确实逐字相同**，但那不重要——**问题根本不在 luci 树里**，而在
  OpenWrt 树的 `DEFAULT_PACKAGES.tweak`。

**⇒ 方法论**：用「树 API 拉文件列表 + 逐个 raw 抓内容」做全量对比时，
**必须先确认列表没有被截断**，否则「两树一样」只是「两边都被截成了
一样的前 243 个文件」。

### 7.9 run #5 与 run #6：工作流自己解析失败，以及 Configure 通过

**run #5（2026-11-22，head `94335616`）报失败，但一个作业都没有。**
GitHub 在**解析工作流文件**失败时的行为就是：建一个 run、判它失败、不建任何
作业。所以唯一的证据只有 run 本身的结论。

根因是那一步的**未加引号的 YAML 标量里带了冒号和空格**：

```yaml
      - name: Gate: the resolved config carries exactly one theme
```

整个文件因此不可解析，那一轮**根本没走到 Configure**——本来是要验证
tweak 包修复的那一轮，什么也没验证。

修法是给标量加引号，外加一个门禁把这件事挡住：`tests/test_sk_workflows.py`
在**推仓库的这台机器上**检查两条线的形状——每个工作流文件都能解析、7.3 线
依赖的步骤都还在、门禁作业点名的测试文件都存在、7.3 线的路径过滤覆盖这条线
的输入（**补丁落在过滤之外就什么都不会构建，而那一轮绿灯什么都不能说明**）。
它接在两条线的门禁作业里，在任何构建之前跑。

> 仓库里早就有 `tests/check_workflow_yaml.py`，但它自己的注释写着「**没有**
> 接进 CI」，路径写死成某一台机器的 `/mnt/c/ugit/SK-D840N4/...`，而且只看
> 发布线那一条。它防不住这次的失败。

**run #6（head `edc3db7b`）把 Configure 走通了。** 顺序是：
`repository gates` 绿 → 准备 7.3.4 树 → 板级层预检 → 板级层拷入 → 板级层
复检 → OpenWrt 侧 overlay → `/dev/mem` 片段 → feeds → **Configure 通过**
→ 主题门禁通过 → Download → Compile。

**⇒ 7.8 的根因判定成立**：`DEFAULT_PACKAGES.tweak` 清空之后，resolved config
里不再有 `CONFIG_PACKAGE_luci=y`，`luci-theme-bootstrap` 也不再出现，主题
只剩 argon。**7.3 线第一次拿到可编译的源码树。**

---

## 八、BP 池：逐槽位对照（第 1 片剩余部分的结论）

**判决**：**BP 池布局在 7.3 上不需要移植。** 我们 6.18 世代那套写法是
**为 6.18 的 free ring 内存布局写的**，把它搬到 7.3 会指向错误的内存。
第 1 片的「BP 池布局」这一项可以关掉。

**两代的 free ring 内存布局本来就不同**（这是全部矛盾的根源）：

| | 6.18 世代（我们 overlay） | 7.3 公版 |
|---|---|---|
| `FREE_RING_SIZE` | `0x40000`（五个环**每个**都这么长） | `0x4000`（RX 两个）/ `0x40000`（TX 三个） |
| `FREE_RING0` 起 | 0x1a0000，每环 +0x40000 | 0x1a0000，RX 两环各 +0x4000，TX 三环各 +0x40000 |
| 环的语义 | FR0…FR4 | FR0 = RX normal，FR1 = RX jumbo，FR2/FR3/FR4 = TX normal / TX jumbo / CPU133 |

**五个硬件槽位逐个对照**：

| 槽位 | 7.3 公版 | 我们 6.18 世代 | 判定 |
|---|---|---|---|
| `+0x104` | `rx_normal_bp_dma`（独立分配） | `rx_normal_bp_dma`（独立分配） | 相同 |
| `+0x108` | `FREE_RING1`（RX jumbo 位，**软件不用它**） | `rx_jumbo_bp_dma`（独立分配） | 语义不同 |
| `+0x118` | `FREE_RING2` | `FREE_RING1` | **错位一位** |
| `+0x11c` | `FREE_RING3` | `FREE_RING2` | **错位一位** |
| `+0x408` | `FREE_RING4` | `FREE_RING3` | **错位一位** |

**为什么我们 6.18 世代那套不能搬**：在 6.18 的布局里 `FREE_RING1` 是
`FR0 + 0x40000`，在 7.3 的布局里 `FREE_RING1` 是 `FR0 + 0x4000`——
**同一个名字在两代指向的内存差 0x3C000 字节**。照搬过去，三个 TX
retrieval 环会指向 RX jumbo 环和 TX normal 环的位置。
而且 7.3 公版的映射是**按厂商布局一一对应**的（FR1↔`+0x108`、
FR2↔`+0x118`、FR3↔`+0x11c`、FR4↔`+0x408`），**公版是对的**，
我们 6.18 世代那套才是错位的一方。

**池大小寄存器也不需要改**：按我们自己的实测编码规则
`(((depth >> 12) - 1) << 21) | (((free_depth >> 12) - 1) << 10) | BIT(8)`，
7.3 的 `BP_RING_SIZE` = 4096 ⇒ `((1 - 1) << 21) | 0x3C00 | 0x100` =
**`0x3d00`**——**正是公版写死的那个值**。公版在这里和我们的规则一致，
因为 normal 环深度从 8192 变成 4096 时 `(depth >> 12) - 1` 正好是 0。

**内存占用**：我们 `(8192 + 4096) × 4` = **48 KiB**（两块池），
公版 `0x4000` = **16 KiB**（一块，4096 项）。

**为什么跟随公版是对的**：**目标固件（cnjn 自己发布的 7.3 固件）用的就是
公版的单池 4096 布局**——那份 `npu.ko` 就是 7.3 公版驱动集。跟随公版
等于跟随目标固件，而不是跟随我们 6.18 世代那次改动。

**未做 / 风险**：

* 我们 6.18 世代的 **8192 深度**没有带到 7.3。cnjn 自己的固件用 4096 跑得通，
  但那是公版的深度，不是这块板在 2.5G 满载下的实测深度。
  **判据**：实机跑起来后看 `ethtool -S` 的 RX 欠载/丢包计数；
  若出现欠载，再把 normal 池加深到 8192（那时 `+0x010c` 要改成 `0x203d00`，
  `+0x108` 指向独立分配的 jumbo 池，且 TX 三个环必须保持公版的 FR2/FR3/FR4）。
* **「布局」和「投递协议」是两件事，这里只判了布局。** 7.3 **有**
  `zx279133_idm_rx_flush_refill`，只是它写的是 `writel(count, BP_REFILL)`，
  而厂商的 `idm_new_bp_update()` 写的是「每 1024 个缓冲一次 0x400 脉冲，
  再写余数（normal 在 bits 15:0、jumbo 在 bits 31:16）」。
  **单池时 `writel(count)` 是否等价，没有实测依据**——厂商之所以按 1024
  记账，很可能是因为硬件内部以 1024 为单位。
  **⇒ 这一条要单独判**，判据是实机 RX 是否真的收得到帧
  （`/proc/net/dev` 的 RX 计数在涨、`ethtool -S` 无欠载）。
  我们的 6.18 实现在这里已经不是「布局」，而是**实测修正**，
  与第 1 片同源，应当随第 1 片一起在 7.3 上验证。

---

## 八之二、第 2 片：RX 帧落点与 BP 投递协议（两个补丁）

**判决**：两件事都**要移植**，各一个补丁。

### 八之二.1 `110-sk-idm-rx-frame-offset.patch`：帧不在缓冲区的头

**证据（本板自己的字节，run 160）**：驱动交给 IDM 的是「页 + 256 字节」，
回来的描述符指的就是这个地址（驱动自己的检查坚持这一点，每一帧都通过），
**而帧在这个地址再往后 0x80**。run 160 把整页打了出来：

```
page +0180: ffffffffffffff8447095bbd2c08060001
page +0190: 0800060400018447095bbd2cc0a80919
page +01a0: 000000000000c0a80901000000000000
```

完整读出来就是测试 PC 发来的 ARP request。**帧前面那 128 字节是页原来
留下的内容**（一次运行是 0，另一次是 0xff）——驱动读的是一个从未被写
过的页，交上去的每一帧都是那个页的残留（docs/77）。

**为什么这条能直接搬到 7.3**：第 1 片已经把 IDM 寄存器对齐到本板的实测
值，而 run 160 的测量**是在那次对齐之后**做的。所以它描述的是同一套
硬件，而 7.3 线上的寄存器就是同一套。

**四处必须一起动**（同一个地址的四种写法）：

| 位置 | 改动 |
|---|---|
| page pool 的 `.offset` | 加帧偏移。缓存失效（`page_pool_dma_sync_for_cpu()` 会自己加 pool 的 offset）与设备侧同步（`page_pool_put_full_page()`）都从它取 |
| 帧的 `data` 指针 | 加帧偏移 |
| XDP 缓冲 | 加帧偏移 |
| AF_XDP 池的尺寸检查 | 加上偏移后仍要放得下 |

**做成只读模块参数**：`zx279133.rx_frame_offset`，默认 `0x80`。
只读是故意的——一个池里所有缓冲的偏移必须一致，而已经交给硬件的池
不能挪。**一次 `zx279133.rx_frame_offset=0` 的启动就能在板子上把两种
形状分开**，不用在纸上争。

### 八之二.2 `120-sk-idm-bp-refill-protocol.patch`：门铃要写编码，不写计数

**判决**：上一轮判「单池时是否等价没有实测依据」，这一轮判掉了。

`IDM+0x100` 不收缓冲计数。厂商的 `idm_new_bp_update()` 写的是
**每 1024 个缓冲一次 `0x400` 脉冲，再写余数**（normal 在 bits 15:0、
jumbo 在 bits 31:16），硬件从这些字段里读出计数。**写计数本身等于没写
缓冲；计数是 1024 的倍数时等于写了零个缓冲**（docs/45 §2.3）。

**而 7.3 在启动时正好投 2048 个缓冲**（`ZX279133_IDM_RX_BUFFER_COUNT`），
**2048 正是 1024 的倍数** ⇒ 公版那一次写等于告诉硬件「池是空的」。

**这不影响 cnjn 的目标固件**（它投的也是 2048，但用的是厂商自己的
`npu.ko`），**也不影响公版在其他板上的表现**——它只说明**我们这条线**
的 7.3 镜像不能照抄公版的那一次写。

**7.3 只有单池**，所以只写厂商序列的 normal 字段。

---

## 八之三、第 3 片：9132 交换表驱动（`switch.c` 整体移植）

**判决**：**可以整体搬过去，搬法是「照抄 + 换名字」，不是重写。**
这一片是 76 个函数、4150 行，和第 1、2 片（寄存器级的小改）不是一个量级，
所以先量化，结论和预期相反。

**先纠一个比错的对象**：7.3 树里**没有** `zx279133-switch.c`，只有
`zx279133-rtl8372n.c`（243299 B）。乍看像「整体新写」，但
`zx279133-rtl8372n.c` 是给**另一颗交换芯片**用的——Realtek RTL8372N 是
SR1010 的 LAN 交换芯片；我们板上的 LAN 交换芯片是 Marvell 9132。7.3 树里
`zx279133-rtl8372n.c` 的文件头自己就写着「the RTL8372N sits on the SR1010」。

真正的对照对象是**我们自己**的 `kernel-overlays/.../zx279133-switch.c`
（159177 B，76 个函数）。逐项量化：

| 检查面 | 结果 |
|---|---|
| switch.c 用到的 `ZX279133_*`/`zx279133_*` 名字 | 201 个共用，**22 个只有我们这边有** |
| switch.c 调到的**内核 API** | 46 个，**3 个 7.3 树里没有**（全是我们自己的 LED 辅助函数） |
| DSA 操作表成员 | 13 个，**7.3 的 `dsa_switch_ops` 全部有，签名一致** |
| LAN 服务算子 | 11 个里只有 `control_begin`/`control_end` 两代不同 |

**为什么 DSA 那一项是「全部有」**：我们这份 `switch.c` 用的**已经是新代
DSA API**——`port_bridge_join` 的第二个参数是 `struct dsa_bridge`（按值
传）、带 `tx_fwd_offload` 与 `extack` 出参——而 7.3 树
`include/net/dsa.h` 的 `dsa_switch_ops` 正是这一代。注册入口也一样：两边
都是 `devm_kzalloc()` + `dsa_register_switch()`，树里的
`zx279133-rtl8372n.c` 就是这么注册的。

**22 个名字的来龙去脉**：其中 21 个是**9132 自己的寄存器图**
（`ZX279133_SMAC_*` 那组内部 PHY MAC 块、`ZX279133_9132_OUTERPHY_PPU`、
`ZX279133_XMAC0_SOPC_SEND_ENABLE`），它们在我们 6.18 世代的 `zx279133.h`
里；7.3 的 `zx279133.h` 是**SoC 的**头文件，里面没有这些。⇒ 新开
`zx279133-9132.h` 装这 21 个，值与出处逐字照抄。

剩下 1 个是 `zx279133_lan_service_control_begin`，见下。

### 8之三.1 `control_begin`/`control_end`：我们自己加的服务能力，要一起搬

**它是什么**：一个**跨 XMAC 窗口与 PCS 窗口**的控制事务。4 处用到：lane
监视器读远端、release 时的「恢复 XMAC 镜像 + 恢复 XPCS 镜像」、probe 里的
两段。

**为什么不能用 XMAC 锁代替**：控制序列里**恢复 XPCS 镜像那一步自己会取
XMAC 锁**（`zx279133_switch_xmac_restore()` 内部 `xmac_lock`/`unlock`）。
拿 `xmac_lock` 当事务锁会**自锁死**。

**7.3 树里没有**：7.3 的 `zx279133-lan.h` 的服务算子表里没有这两个；我们
6.18 世代的树里有（`zx279133-netdev.c` 的 `zx279133_lan_control_begin()`
拿 `eth->lan_control_lock`）。

⇒ 随第 3 片一起搬：`kernel-patches/7.3-local/130-sk-lan-control-transaction.patch`
给 7.3 的树加：

* `struct zx279133_eth` 里一把 `lan_control_lock`（紧挨 `xmac_lock`）；
* 服务算子 `control_begin`/`control_end` 两个槽位 + 两个内联包装；
* `zx279133_lan_service_valid()` 改为**要求**这两个算子——子驱动要了一个
  父驱动没有的事务，要在 probe 时就被拒，而不是等到第一次恢复；
* 父驱动侧的实现（加锁 / 解锁）。

**树自己的 RTL8372N 驱动不需要这个事务**（同族芯片，序列自己取 XMAC 锁），
所以**树的行为一点没变**，服务只是长出了两个算子。

**Kconfig 与 Makefile 也在这一个补丁里**，因为服务长出来的原因就是 9132
驱动：`CONFIG_ZTE_ZX279133_SWITCH` 与
`obj-$(CONFIG_ZTE_ZX279133_SWITCH) += zx279133-switch.o`。

> `scripts/configure-sk-d840n.sh` 早就 `pin_y ZTE_ZX279133_SWITCH` 了，
> 在这条线上**一直是空转**——defconfig 会把不认识的符号**静默丢掉**。
> 符号存在之后，这个 pin 才开始生效。

### 8之三.2 落地位置：新的 `kernel-overlays-73/`

**不覆盖 `kernel-overlays/`**：那是 6.18 世代的驱动集，发布线在用，一行都不能动。
7.3 线的自有驱动树另开：

| 文件 | 说明 |
|---|---|
| `kernel-overlays-73/drivers/net/ethernet/zte/zx279133-switch.c` | 9132 驱动本体（159 KB），文件头改成 7.3 的语境 |
| `kernel-overlays-73/drivers/net/ethernet/zte/zx279133-9132.h` | 9132 的寄存器图（21 个宏），私有 |
| `kernel-overlays-73/drivers/net/ethernet/zte/zx279133-lan1-led.h` | LAN1 LED 辅助（自包含，照抄） |

`tools/apply-sk-overlay-73.sh` 改成**拷两份**：`kernel-overlays/`（去掉 6.18
驱动目录与 PHY 目录）**和** `kernel-overlays-73/`；正反两面的断言清单同步
更新——`zx279133-switch.c` 与 `zx279133-lan1-led.h` 从「不得出现」挪到
「必须出现」。

`.gitattributes` 加 `kernel-overlays-73/** text eol=lf`：**同一个理由**——
`core.autocrlf=true` 的 Windows 检出会把驱动源码变成 CRLF，而编译器读到的
文件就不是门禁读到的那个文件。

### 8之三.3 编译前的门禁：`tools/check-sk-switch-api.py`

本机没有 arm64 交叉工具链，编译只能走 CI，**而 Compile 在作业的第 70
分钟**。所以把编译器会说的话先问一遍：

1. 驱动带的三个文件都在树里；
2. 驱动用到的每个 `ZX279133_*`/`zx279133_*` 名字都有定义（驱动自己 / 树的头文件 / 私有头）；
3. 驱动调到的每个**内核函数**在树里存在；
4. **服务契约**：驱动调用的每个 `zx279133_lan_service_*` 包装在树里、
   它包的那个算子在算子表里、**父驱动真的提供了它**——最后这一条是链接
   器七十分钟后才会说的话；
5. 构建接线：Kconfig 符号、Makefile 对象、板级 DTS 节点、`configure` 脚本
   的 pin，四者对得上。

**本机实测**：在 `scratch/test73c`（vanilla + 39 补丁 + 100/110/120 + 板级层
+ 130 + `kernel-overlays-73/`）上**全部通过**。

### 8之三.4 验证

* 补丁 130 在「vanilla + 39 + 100 + 110 + 120」的基准树上**干净 apply**，
  且 apply 后的结果与手工编辑**逐字节一致**（`git diff` 为空）。
* `tools/apply-sk-overlay-73.sh` 在真树上跑通，文件清单断言全过。
* 符号闭合检查：22 → **0**，内核 API 3 → **0**。
* **未做**：本机没有编译器，**这份 `switch.c` 还没有被真正编译过**；
  CI 的 Compile 是第一次编译。

---

## 九、下一步（按片重打，每片一次 CI）

**没搬的，按片来：**

1. **IDM 的 RX 缓冲池**：**布局已结论——不移植；投递协议已判——移植**。
   * *布局*（五个槽位指向谁、池大小寄存器）：跟随 7.3 公版。逐槽位对照见
     第八节——公版的映射按厂商布局一一对应，我们 6.18 世代那套是**为 6.18
     的 free ring 内存布局写的**，搬过来会指向错误的内存。
   * *投递协议*（refill 怎么通知硬件）：**已判，已落成第 2 片的第二个补丁**
     `120-sk-idm-bp-refill-protocol.patch`。7.3 启动时投 2048 个缓冲，
     正好是 1024 的倍数，公版的 `writel(count)` 在厂商的解码下等于
     「零个缓冲」。
   * **RX 帧落点 0x80**（本板实测，run 160）是第 2 片的第一个补丁
     `110-sk-idm-rx-frame-offset.patch`，见第八之二节。它不是「池布局」，
     是**帧在缓冲区里的位置**，两件事分开判才对。
   * 第 1 片的寄存器修正已落地，打完之后两代 `zx279133_idm_tx_configure` 的
     33 条 `writel` 里**只有 6 条不同，且全是 BP 池那几条**（见下）。
   * **观察项**：我们 6.18 世代的 8192 深度没有带过来；若实机 `ethtool -S`
     出现 RX 欠载，再加深（做法见第八节末）。
   * **判据（实机）**：`/proc/net/dev` 的 RX 计数在涨、`ethtool -S` 无欠载；
     若 RX 全 0，用 `zx279133.rx_frame_offset=0` 再启一次，把两种形状
     在板子上分开。
2. **IDM 的其余部分**。这一条原来是「约 78 个 hunk 的重写」，那是按 diff
   规模估的，**不精确**。按函数体逐个对照之后（`tools/func-body-diff.py`，
   两代 `zx279133-idm.c`），实际工作量小得多，也清楚得多：

   | 判定 | 个数 | 含义 |
   |---|---|---|
   | IDENTICAL | 27 | 去掉注释与空行后**逐字相同**，不用动 |
   | similar（60–92%） | 9 | 大部分行相同，看一眼差异即可 |
   | REWRITE（25–52%） | 6 | 真正要重写 |
   | 只在 6.18 世代 | 10 | 7.3 里没有，要作为新代码写进去 |
   | 只在 7.3 世代 | 4 | 7.3 新增，其中一个是我们改过名的 |

   要重写的 6 个（我们行数 / 7.3 行数，相同行占比）：

   | 函数 | 我们 | 7.3 | 占比 | 备注 |
   |---|---|---|---|---|
   | `zx279133_idm_tx_configure` | **129** | 58 | 25% | **第 1 片已解决**，见下 |
   | `zx279133_idm_rx_flush_refill` | 27 | 13 | 41% | 真要重写 |
   | `zx279133_idm_rx_lan_vport` | 12 | 6 | 33% | 真要重写 |
   | `zx279133_idm_rx_preserve_dsa_vlan` | 21 | 21 | 48% | 真要重写 |
   | `zx279133_idm_rx_sync_for_device` | 15 | 8 | 27% | 真要重写 |
   | `zx279133_idm_tx_drain` | 25 | 16 | 52% | 真要重写 |

   **`tx_configure` 划掉的理由**：函数体差异大，是因为**注释和 BP 池那几行**，
   不是寄存器值。按语句逐条比（`tools/idm-register-diff.py`，把
   `writel(...)` 当比较单位）：

   ```
   zx279133_idm_tx_configure: ours 33 writes, theirs 33 writes
   6 of 33 statements differ
   ```

   **那 6 条全部是 BP 池**：`+0x108`（jumbo 池 vs `FREE_RING1`）、
   `+0x118/+0x11c/+0x408`（三个 retrieval 环错位一位）、
   `+0x010c/+0x0110`（按深度编码 vs 写死 0x3d00）。
   **其余 27 条——控制字、`+0x0010`、路由字、`+0x0038`、`+0x0070/+0x74/+0x90/+0x94`、
   `+0x00bc`、`+0x0124`、`+0x02c8`、`+0x0490`、中断掩码与 IRQ 路由——两代逐条相同**，
   因为第 1 片已经把它们改成了实测值。

   **⇒ 第 1 片之后的 IDM 寄存器初始化已经拉齐，只剩 BP 池，而 BP 池跟随公版。**


   只在 6.18 世代、要作为新代码写进 7.3 的 10 个：

   * `zx279133_idm_bp_ring_cfg`（**就是第 1 片剩下的 BP 池布局**）
   * `zx279133_idm_dump_frame` / `zx279133_idm_dump_nonzero`（寄存器转储）
   * `zx279133_idm_find_broadcast` / `zx279133_idm_rx_find_header`
   * `zx279133_idm_rx_has_ethertype` / `zx279133_idm_rx_is_jack`
   * `zx279133_idm_xdp_pass_valid`
   * `zx279133_idm_give_frame_back`
   * `zx279133_idm_rx_sync_xsk_for_cpu` —— 7.3 里叫
     `zx279133_idm_rx_sync_xsk_for_device`，**是改名不是新增**。

   只在 7.3 侧的 4 个：`zx279133_idm_rx_count`、`zx279133_idm_rx_parsed`、
   `zx279133_idm_rx_prepare_dsa`、`zx279133_idm_rx_sync_xsk_for_device`。

   所以这一片可以按「先 6 个 REWRITE、再 10 个新增」切，**不是整块重写**。
3. **9132 交换表 / switch.c**（76 个函数，4150 行）。这是 SK-D840N 四个 GE 口能不能活的问题。
   **第 3 片已落地**（见八之三）：`kernel-overlays-73/` 带驱动本体与两个私有头，
   `kernel-patches/7.3-local/130` 给 7.3 的 LAN 服务补上控制事务并接好
   Kconfig/Makefile。**未编译验证**——等 CI 的 Compile。
   接下来这一片剩下的是**实机对拍**：驱动能在 7.3 上编译不等于 LAN 口能通，
   判据是 conduit netdev 的 RX 计数在涨、四个 DSA 用户口的 link 状态正确。
4. **PON / OMCI**（约 56 个函数）。工作量最大，且和第 5 条「cnjn 会不会公开
   npu-pon.ko」强相关。
5. **RGMII 调试**（8 个函数）与 **netdev/main/mac 的健壮性修正**（约 28 个函数），
   随 2~4 一起带。

验收口径与 6.18 线一致：**CI 绿 + 门禁脚本全过 + 实机启动后
`/proc/net/dev` 有预期网口、`ethtool -S` 计数在涨**。

---

## 十、复现本文结论的命令

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

# 4) 板级层的编译前提预检（overlay 拷入前 / 拷入后各一次）
python3 ci-build-repo/tools/check-sk-board-api.py pre \
        --tree linux-7.3.4 --report board-api.json
sh ci-build-repo/tools/apply-sk-overlay-73.sh linux-7.3.4
python3 ci-build-repo/tools/check-sk-board-api.py post \
        --tree linux-7.3.4 --report board-api.json

# 5) IDM 的移植工作单：同名函数逐个比函数体
python3 ci-build-repo/tools/func-body-diff.py \
        ci-build-repo/kernel-overlays/drivers/net/ethernet/zte/zx279133-idm.c \
        linux-7.3.4/drivers/net/ethernet/zte/zx279133-idm.c

# 6) IDM 的寄存器初始化：按 writel 语句逐条比（函数体差异的真实来源）
python3 ci-build-repo/tools/idm-register-diff.py \
        ci-build-repo/kernel-overlays/drivers/net/ethernet/zte/zx279133-idm.c \
        linux-7.3.4/drivers/net/ethernet/zte/zx279133-idm.c \
        zx279133_idm_tx_configure

# 7) 第 3 片：9132 交换表驱动的编译前门禁（overlay 拷入之后）
python3 ci-build-repo/tools/check-sk-switch-api.py \
        --tree linux-7.3.4 --repo ci-build-repo

# 8) 工作流自身的门禁（两条线都在推之前跑）
python3 ci-build-repo/tests/test_sk_workflows.py
```
