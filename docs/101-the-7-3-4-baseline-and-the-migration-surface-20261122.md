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
   按文件分：**9132 相关的 73 个**（`zx279133-np.c` 30、`zx279133-switch.c` 39、
   `zx279133-lan1-led.h` 4）、**PON/OMCI 栈 56 个**（`zxpon.c` 37、
   `zx279133-pon.c` 17、`zx279133-omci.c` 2）、**RGMII 调试 8 个**、
   **其余健壮性修正 34 个**（netdev 12、main 10、idm 6、mac 6）。
   7.3 公版里**一个都没有对应物**——因为 cnjn 公开的 7.3 树**没有 PON 模块**。
   这 171 个函数**逐条都有归属**，账本见 3.5 与八之七。
4. **CI 走「外部树」模式，与已验证的 6.18 线同构。** cnjn 自己的 zte target 在
   master 上明确禁止 `CONFIG_EXTERNAL_KERNEL_TREE`（它要 OpenWrt 自己下载底包并打
   `patches-7.3`）；我们反过来：自己准备「vanilla 7.3.4 + 39 补丁」，再把树交给
   OpenWrt。好处是内核字节与补丁字节都被本仓库钉死、可离线复核。
5. **驱动增量已按片重打五片，第 6 片是实机对拍。** 顺序是
   IDM（1~2 片）→ 9132 交换表驱动（3）→ PON/OMCI 栈（4）→ **9132 芯片本身的编程（5）**。
   每片一个补丁、每片一扇编译前的门禁、每片一次 CI。
   **这五片的代码在本机已全部通过 clang 的语法与符号检查**
   （现在是仓库的工具 `tools/kcheck.py`，见 7.17），但**还没有被真正编译过**
   （本机无 arm64 交叉工具链），所以下一步是
   CI 结论与实机对拍。
   剩下的两项（RGMII 调试、TX 故障隔离/恢复）已各自判明，见八之五.9。
6. **7.3 线 run #1~#13 的 Compile 失败与内核无关**：失败的是我们自己的 Rust 包
   `libzxpon-omci`，因为这条线少了装 Rust 工具链的步骤（6.18 线有）。已修，见 7.11。
7. **7.3 线即使编译过了，镜像里也不会有 NPU**：cnjn 的 `config-7.3` 把驱动定成
   模块，而本仓库没有加载它们的机制；`pin_y` 也救不了，因为 kconfig 会把
   「依赖是模块」的 tristate 夹成 `=m`。已修（四个符号钉成 `=y`），见 7.12。
8. **7.3 线此前只查了 4 个驱动符号，没有 6.18 线那道「读合并配置、断言板子全部
   要求」的门禁**：一份丢了 Realtek PHY、丢了整族 netfilter、或者带着 SR1010 的
   `mtdblock7` 内核命令行的配置，照样能编出一个「构建成功但不好用」的镜像，
   而且每一种失败在板子上都长得像「内核起不来」。已补上，并先在本机用
   kconfig 自己的三值依赖逻辑证明了那 39 个符号在这棵 7.3 树上**不会被夹成
   `=m`**。见 7.14。
9. **7.3 线此前也没有「dtb 的每个 compatible 都有驱动」这道门禁**：一个没有驱动
   的 compatible 会被内核**静默跳过**，板子照常起来，只是没有那个口。
   这道门禁不能照抄 6.18 的版本（会因 RGMII 那个节点变红），
   所以重写成了 `tools/check-sk-dtb-compat.py`，而且问的是更强的问题：
   **compatible 字符串在不在已编译的内核镜像里**——所以它同时是
   「驱动有没有退回成模块」的探测器。见 7.15。

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

这张表是**实测**出来的，不是手写的：口径是「6.18 世代的
`kernel-overlays/drivers/net/ethernet/zte` 对 vanilla `linux-7.3.4`
里没有的函数」，工具是仓库自己的 `tools/func-surface-diff.py`，
复跑命令见第十节。

| 文件 | 函数数 | 这一块是什么 |
|---|---|---|
| `zx279133-switch.c` | 39 | 9132 交换芯片、LED、MDIO、XPCS |
| `zxpon.c` | 37 | PON 协议核心：PLOAM MIC、T-CONT、GEM、OMCI 队列、AES |
| `zx279133-np.c` | 30 | 9132 交换表 / 端口 / 洪泛 / MF 镜像，**加 ERAM 与 PPU 的基础设施** |
| `zx279133-pon.c` | 17 | PON 栈与网设备的接线 |
| `zx279133-netdev.c` | 12 | TX 恢复 / phylink 生命周期 / LAN 控制 |
| `zx279133-main.c` | 10 | BMU 阈值 / RED 网关 / vendor 重配 / 诊断转储 |
| `zx279133-rgmii.c` | 8 | RGMII 调试 |
| `zx279133-idm.c` | 6 | IDM 实测修正：BP 环配置、RX 头定位、广播查找 |
| `zx279133-mac.c` | 6 | SOPC 就绪 / XPCS 设备 / XMAC 复位掩码 |
| `zx279133-lan1-led.h` | 4 | LAN1 那盏灯的 claim / read / set / restore |
| `zx279133-omci.c` | 2 | OMCI 队列的准备与回收 |
| **合计** | **171** | |

**上一版这张表是错的**：只有 8 行、相加 161，比总数少 10 个——
`zx279133-np.c` 的 6 个（ERAM/PPU 基础与 debugfs 的拆除），
以及整个 `zx279133-lan1-led.h`（4 个）没有被记账。
**总数 171 是对的，分解是错的**，而分解正是「要不要搬」的清单，
所以这个错不是排版问题。

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
| `kernel-patches/7.3-local/` | **我们自己的**补丁，applied 在 vendored 系列之后；当前是 IDM 实测修正三片（`100` 寄存器值、`110` RX 帧落点、`120` BP 投递协议）**加第 3 片的 `130` LAN 控制事务**、**第 4 片的 `140` PON/OMCI 接线**与**第 5 片的 `150` 9132 芯片编程** |
| `kernel-overlays-73/drivers/net/ethernet/zte/` | **7.3 线专有的驱动树**：9132 交换表驱动本体 + `zx279133-9132.h`（9132 自己的寄存器图**与 4 个 PPU 号**）+ `zx279133-lan1-led.h`，**加第 4 片的 PON/OMCI 五个文件**（`zxpon.c`/`zxpon.h`/`zx279133-pon.c`/`zx279133-omci.c`/`zx279133-omci-wire.h`）。与 6.18 世代的 `kernel-overlays/` 并存，互不覆盖（见八之三、八之四、八之五） |
| `tools/check-sk-switch-api.py` | 第 3 片的编译前门禁：驱动用到的名字、内核 API、**LAN 服务契约**、构建接线，四项逐条核对（见八之三.3） |
| `tools/check-sk-pon-api.py` | 第 4 片的编译前门禁：协议核心闭合、**内核名字在包含闭包里可见**、树侧契约、四个钩子、构建接线、LED 依赖（见八之四.5） |
| `tools/check-sk-9132-api.py` | 第 5 片的编译前门禁：编程在树里、**ERAM 描述符在分配处被记录**、名字闭合、内核 API 在、**没有重定义**、调用顺序、板级 DTS 的 PHY 总线（见八之五.7） |
| `tests/test_sk_workflows.py` | 工作流自身的门禁：每个工作流文件可解析、7.3 线依赖的步骤都在、门禁点名的测试文件都存在、7.3 线的路径过滤覆盖这条线的输入（见 7.9） |
| `overlays-73/target/linux/zte/zx279133/target.mk` | 7.3 线的 subtarget 默认包：**清空 OpenWrt 的 `DEFAULT_PACKAGES.tweak`**，否则其中的 `luci` 元包会拉进 `luci-theme-bootstrap`（见 7.8） |
| `tools/func-surface-diff.py` | 两代驱动的函数面对照，输出「只在我们这边」与「只在 7.3 那边」两张工作清单 |
| `tools/func-port-coverage.py` | **按能力对账**：171 个函数逐条必须有归属（同名 / 账本写明搬到哪 / 有处置记录），未记账即非零退出；账本指着的目标函数必须真在 7.3 树里（见八之七） |
| `tools/kcheck.py` + `tools/kshadow.py` | **编译预言机**：用宿主 clang 对 7.3 树做 `-fsyntax-only`，生成树自己的头文件与影子头文件；7.3 线在 Compile 之前先跑它（见 7.17） |
| `tests/test_sk_kcheck.py` | 上面那道检查的**判决规则**的单元测试：哪些错误算我们的、哪些算编译器读不动树 |
| `tools/idm-register-diff.py` | 以 `writel(...)` 语句为单位逐条对比两代的寄存器写入 |
| `pon/npu-reverse/` | 逆向产物留档：99 个 API、rodata、函数表、initrd 清单、目标 DTS |

**没有动的东西**：`kernel-overlays/`（6.18 世代驱动，一行未改）、`overlays/`（OpenWrt 侧，
一行未改）、现行 `build-sk-d840n.yml`（发布线一行未改）。

---

## 六、未做 / 风险

1. **我们自己的 171 个函数逐条都有归属，但「搬了」不等于「同名」。**
   7.3 通道目前是「cnjn 的 7.3 驱动 + 我们的板级层 + IDM 三片 + 9132 交换表驱动
   + PON/OMCI 栈 + **9132 芯片编程**」。
   实测对账（八之七，`tools/func-port-coverage.py`）：
   **125 个同名对上**、**27 个由 7.3 树以别的名字、别的形状实现**
   （vendor 兼容重配、BMU 阈值、RED 网关、SOPC、数据面模型，7.3 各换了机制）、
   **19 个有明确处置**：RGMII 的 8 个（**取舍不是缺口**，见八之五.9）、
   TX 故障隔离/恢复的 8 个（**不整体移植**，见八之五.9）、
   9132 debugfs 控制台的 4 个（7.3 树有自己的 `native_*_io` 端点），
   外加 1 个只做调试打印的函数。**未记账 0 个。**
   ⇒ 「剩下的是 RGMII 与 TX 恢复」这个说法**按函数名看是对的、按能力看是错的**：
   那些函数名之所以不在 7.3 树里，是因为 7.3 树用另一套机制做同一件事。
   **但这五片还没有一次成功的编译记录**——本机无交叉工具链，编译只能走 CI；
   7.3 线此前每一轮都死在下载阶段（见 7.16），所以第一轮完整编译的结果还不知道。
   实机对拍是它之后的下一道。
   **PON/OMCI 的上行没有接**——但 6.18 侧同样没有接（见八之四.7），
   所以这不是 7.3 线独有的缺口。
   **PON MAC 窗口一个寄存器都没碰**（厂商模块没有
   `mode_xgpon_nsyn_cfg_133`），所以第 4 片只在没有光纤时也成立：
   LOS 灯会跟着 `opsd` 翻，carrier 停在 O1，OMCI 队列空转。
2. **没有编译验证。** 本机无 arm64 交叉工具链，编译只能走 CI；`build-sk-d840n-73.yml`
   是**第一次**运行，结果未知。
3. **DTS 没有在本地编译过。** 本机没有 `dtc`，只能用「label 可解析 + 属性存在」
   做静态核对。CI 里有 dtc 门禁，第一次跑就知道结果。
4. ~~**驱动是模块而非内置。**~~ **已在本轮判明并修掉**，见 7.12：
   7.3 的 `config-7.3` 里 `CONFIG_ZTE_ZX279133_ETH=m`，而 6.18 线是内置；
   模块化不只是「要重新确认 kmod 依赖链」，而是**镜像里根本不会有驱动**——
   本仓库没有加载它们的机制，`pin_y` 也会被 kconfig 夹掉。7.3 线现在把
   `ZTE_ZX279133_ETH`/`_LAN_CONDUIT`/`_SWITCH` 钉成 `=y`，与 6.18 线一致。
   **剩下的**：`ZTE_ZX279133_RGMII` 在 7.3 树里**没有 Kconfig**，
   配置脚本钉的符号会被 defconfig 静默丢掉（6.18 线上的 RGMII 只读探针
   在 7.3 上不存在）。
5. **目标固件的 PON 模块仍未公开。** 我们逆向出的 30 个 PON 钩子导出只在二进制里；
   等 cnjn 公开 `npu-pon.ko` 源码，我们自研的 PON/OMCI 栈要么对齐、要么让位——
   这个决策应该在 7.3 的 NPU 驱动跑通之后再做。
6. ~~**7.3 线现在只编译，不产出镜像。**~~ **本轮补上了**：
   7.3 线现在会收集 `bin/targets/zte/zx279133`、用 6.18 线同一个工具
   （`tools/check_sysupgrade.py`）校验 sysupgrade 镜像并导出三个分区文件
   （`flash-uImage` / `flash-dtb.bin` / `flash-rootfs.jffs2`），
   再把镜像与分区文件一起作为 artifact 上传。
   **仍然没有的**：6.18 线那套**发版**流程（Release、免串口直刷下载、
   `flash-set.tgz` 维护包、initramfs 的 `.itb`）——7.3 线只出镜像不发布，
   这是有意的。`matrix.mode` 目前只影响 `configure-sk-d840n.sh` 的第三个参数，
   initramfs/flash 两路走的是同一条编译链。
   **要注意的**：NPU（加上 flow offload）现在在 `vmlinux` 里，
   要塞进固定 8 MiB 的 `kernel` 分区；镜像 profile 的 `KERNEL_SIZE := 8192k`
   会让 OpenWrt 自己拒绝超尺寸的镜像，但「Collect the images」步会把字节数
   打进 annotation，超了就一眼看得见。
7. **6.18 线那道「管理面进了镜像」的门禁只搬了一半**：
   `Gate: ONU UI, protocol service and firewall4 reached the image`
   跑的是 `scripts/check-sk-d840n-management.sh`，它要连带 8 个工具与测试
   （staged root 的文件清单、主机侧的 ucode 解释器、firewall4 自己的规则生成器
   测试套件）。那一整套问的是**发布线的产品面**，不是这次迁移；
   而它的另一半——「包是不是真的装进了镜像，而不只是被选中」——
   已经搬成 7.3 线的一道轻量门禁（`Gate: the image carries the packages
   the board needs`，读 `bin/targets/**.manifest`）。
   另：「7.3 线要不要把整套 netfilter 钉成内置」——**不需要，它本来就是内置的**，
   见 7.14.2。

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

### 7.10 run #6 的 Compile 失败，和一个诊断盲区

**run #6（head `edc3db7b`）在 Compile 步失败，退出码 2。**
`initramfs` 作业跑了 50 分 14 秒，`flash` 作业同样。前面 16 个步骤全绿
（含 Configure、主题门禁、Download packages），失败点在 `make` 内部。

**但诊断是空的**：annotations 只有 GitHub 自己那句
「Process completed with exit code 2」。原因是

- 作业日志 `/actions/jobs/<id>/logs` 返回 **403**；
- 构建日志 artifact `/actions/artifacts/<id>/zip` 返回 **401**
  （列表公开，下载要鉴权）；
- 浏览器打开作业页显示「**Sign in to view logs**」。

**⇒ 盲区不在编译上，在诊断管线上。** 6.18 发布线早就有解法：Compile 步把
编译器自己那几行**逐条重发成 `::error::cc:` annotation**，
并把同样的内容写进 job summary。**annotation 是公开的，作业日志不是**——
这一条差别决定了「能不能在没登录的机器上查出一个失败的原因」。
7.3 线那一步是照搬 6.18 写的，但没有带这段诊断。

修法：把 6.18 的做法搬进 7.3 的 Compile 步——

```bash
{
  grep -naE "fatal error:|\berror:|Error [0-9]+|implicit declaration|undefined reference|undefined symbol|No such file|not defined" \
    ../compile.log 2>/dev/null | head -n 12 || true
} | while IFS= read -r line; do
  echo "::error::cc: $(printf '%s' "$line" | sed -e 's/%/%25/g' -e 's/#/%23/g' | tr -d '\r')"
done
```

**逐条发而不是发一个转义后的大块**：GitHub 对一个步骤只保留前 10 条 annotation，
而一个 3500 字符的转义块既是**一条**（十行的上限对它不适用），读者也没法读。
同样的内容再写进 `$GITHUB_STEP_SUMMARY`——那里没有条数上限。

本机用一份假的编译日志验证过这段 shell：它确实逐条吐出带行号的错误行，
`%` 与 `#` 的转义也对。

**⇒ 第一次修完还是不够，run #12 给出了第二层。** run #12（head `dabf958`，
第一次带诊断的那一轮）的 annotation 里有内容了，但只有两行：

```
cc: 114:make: *** [.../openwrt/include/toplevel.mk:233: world] Error 1
```

**仍然不是编译器的原话。** 原因有两个，都与 OpenWrt 的日志布局有关：

1. **顶层日志里没有编译器的输出。** OpenWrt 把每个包自己的编译输出放在
   `logs/` 下，顶层日志只留摘要——7.3 那轮的 `compile.log` 一共**只有 114 行**，
   而它跑了 50 分钟。**编译器的原话在 `openwrt/logs/` 里，不在 `compile.log` 里。**
2. **grep 是大小写敏感的。** OpenWrt 自己的失败行是
   `ERROR: failed to build package/linux-7.3.4 (...)`——**大写的 ERROR**，
   而模式里写的是小写 `\berror:`，于是连「是哪个包失败」这一句也没匹配上。

第二版把两处都补上：`-i` 让搜索不区分大小写、并且**先读 `logs/` 下的包日志**
（编译器的话在那里），再读顶层日志（哪个包失败）。本机用一份假的包日志树
验过：annotation 的第一条就是编译器的原话，第二条是失败的包名。

> 这一层的教训和上一层一样：**诊断要按失败信息实际所在的位置去找**。
> 6.18 线只读顶层日志就够了，是因为它那条线的失败消息本来就落在顶层日志里；
> 7.3 线照搬过去就会漏。两条线长得像，失败的位置不一样。

### 7.11 run #13：编译失败的真因不在内核，在 Rust 包的工具链

run #13（head `3f90c50`）第一次让诊断说出了**是哪个包**失败：

```
cc: 110:    ERROR: package/libs/libzxpon-omci failed to build.
cc: 112:make: *** [.../openwrt/include/toplevel.mk:233: world] Error 1
```

**判决：失败的不是内核，是我们自己的用户态包，而且失败与 7.3 无关。**

证据链：

1. **失败的是 `package/libs/libzxpon-omci`**，它是本仓库
   `overlays/package/libs/libzxpon-omci/` 里的一个 **Rust** 包（`omci/Cargo.toml`、
   `Cargo.lock`，16 个 crate，直接依赖 `aes` 与 `cmac`）。
2. 它的 `Build/Compile` 是这样的：
   ```make
   ZX_CARGO?=$(shell command -v cargo)
   define Build/Compile
   	cd $(PKG_BUILD_DIR) && RUSTC="$(dir $(ZX_CARGO))rustc" \
   		RUSTFLAGS="-C target-cpu=cortex-a53" $(ZX_CARGO) build --release --locked \
   		--target aarch64-unknown-linux-musl
   endef
   ```
   工具链**只从环境变量 `ZX_CARGO` 来**，机器上找不到 `cargo` 就没有第二来源。
3. **6.18 发布线有装工具链的步骤，7.3 线没有。**
   `build-sk-d840n.yml` 的 build 作业里有「Rust 1.96.1 and offline ONU regression tests」：
   ```sh
   rustup toolchain install 1.96.1 --profile minimal
   rustup target add --toolchain 1.96.1 aarch64-unknown-linux-musl
   export ZX_CARGO="$(rustup which --toolchain 1.96.1 cargo)"
   echo "ZX_CARGO=$ZX_CARGO" >> "$GITHUB_ENV"
   ```
   `build-sk-d840n-73.yml` 里**一个 rust 字都没有**（逐行比过两个工作流的步骤表）。
   ⇒ 7.3 线上 `ZX_CARGO` 为空 ⇒ `cargo build` 没有 cargo 可执行 ⇒ 包构建失败 ⇒
   顶层 `make` 以 `world Error 1` 收场。
4. **这解释了 7.3 线从来没编成功过**：run #1 到 #13 的 Compile 失败是同一件事，
   与我们搬的五个片无关。run #6（只有 IDM 三片）与 run #9（加第 3/4 片）失败在
   同一处，也正是因为那时就已经缺工具链了。

**⇒ 修复**：7.3 线补上同样的步骤，并**带断言**（cargo 存在、rustc 在旁边、
musl target 已装），缺一件就地失败，而不是把这件事留到 50 分钟之后、包日志里。

**⇒ 顺带把诊断的 pattern 补宽**：原来的 pattern 里没有 `command not found`，
也没有 `error[`——cargo 的错误长成 `error[E0308]: ...`，两样都匹配不上，
所以包日志里明明有原因，annotation 里却只有「哪个包失败」。现在 pattern 里有
`error\[`、`command not found`、`not found`、`failed to resolve`、`cannot find`。

**⇒ 门禁**：`tests/test_sk_workflows.py` 新增一节「the OMCI engine's toolchain is
installed on both lines」，两条线都要有 `rustup toolchain install`、要有
`aarch64-unknown-linux-musl`、要有 `ZX_CARGO=`。反向验证过：把 7.3 的
`rustup toolchain install` 换掉，门禁立刻转红。

**⇒ 本机这一轮的收获**：把 7.3 树用宿主 clang 做 `-fsyntax-only` 的检查器
（`scratch/kcheck.py`）打通之后，**我们补丁碰过的 12 个驱动文件全部通过**
（`np.c`/`idm.c`/`switch.c`/`pon.c`/`zxpon.c`/`omci.c`/`netdev.c`/`main.c`/`mac.c`/
`offload.c`/`host.c`/`lan-conduit.c`），cnjn 那 39 个补丁碰过的 40 多个 `.c`
也全部通过，DTS 预处理通过。**⇒ 内核这一侧本来就没有编译错误**，
这与「失败的是 `libzxpon-omci`」互相印证。检查器还能编出目标码并按 modpost
的规则查符号闭合（11 条疑似缺符号全是宏生成导出造成的误报，逐条核实过）。

---

### 7.12 7.3 线的驱动是模块：镜像里不会有 NPU，而且 `pin_y` 会被 kconfig 夹掉

**判决**：7.3 线**即使编译过了，出来的镜像里也没有 NPU 驱动**。
这不是「kmod 依赖链要重新确认」这种软问题，是硬缺口；而 `configure-sk-d840n.sh`
里那句 `pin_y ZTE_ZX279133_SWITCH` **在 7.3 线上不生效**——它写的 `=y` 会被
kconfig 静默夹成 `=m`。已修：7.3 线把三个符号钉成 `=y`，与 6.18 发布线一致。

证据链：

1. **cnjn 的 `config-7.3` 把驱动定成模块。**
   `target/linux/zte/zx279133/config-7.3:2513` 是 `CONFIG_ZTE_ZX279133_ETH=m`，
   `:2517` 是 `CONFIG_ZTE_ZX279133_RTL8372N=m`。
   合并后的三个片段里，`ZTE_ZX279133_SWITCH` **一个字都没有**（既不是 `=m`
   也不是「is not set」），它是 `scripts/configure-sk-d840n.sh` 的 `pin_y` 加进去的。
2. **本仓库没有任何机制加载这些模块。**
   - `overlays/`、`overlays-73/` 下没有 `kmod-*` 包（只有 `mtd-rw` 一个自研包）；
   - `overlays/target/linux/zte/zx279133/base-files/` 下**没有** `etc/modules.d/`；
   - OpenWrt **不会**把树内模块放进 rootfs——只有被 `kmod-*` 包声明过的模块才会
     被装进 `/lib/modules/<版本>/`，而没有 `/etc/modules.d/` 条目就没人 `modprobe`。
   ⇒ `=m` 的后果不是「模块没自动加载」，而是**镜像里根本没有 `npu.ko`**。
3. **6.18 发布线不是这样，而且有实机读数。**
   `docs/07-sk-d840n-status-20261014.md:335` 记着从这台板子的 `/proc/config.gz` 里读到的
   `CONFIG_ZTE_ZX279133_ETH=y  <-- 网卡驱动内建`；同一份记录里
   `/lib/modules/6.18.38+/` 下 **0 个 `.ko`**、`/etc/modules.d` **整个不存在**
   （`docs/07:11358-11360`）——所有需要的驱动都是内建的，镜像里一个可加载模块都没有。
   ⇒ 6.18 线能在这块板子上跑起来，靠的就是「内建」，不是「打包 + 加载」。
4. **厂商固件靠的是另一套东西，我们没有。**
   从目标固件的 initrd（`fit/initrd-1.bin`，gzip + newc cpio）里挖出来的
   `etc/modules.d/30-zte-npu` 内容是 `npu vlan_offload=1`，
   `etc/modules-boot.d/30-zte-npu` 是指向它的符号链接；另有
   `etc/modules.d/32-zte-npu-pon`（`npu-pon`）与
   `etc/modules.d/42-zte-fnoffload`（`fnoffload vlan_offload=1`）。
   那是**厂商自己的 rootfs** 里的 OpenWrt 模块装载机制，
   而我们的 7.3 线是从 OpenWrt 现装的包树里长出来的，没有这一步。
5. **`pin_y` 为什么救不了。**
   `ZTE_ZX279133_SWITCH` 是 tristate 且 `depends on ZTE_ZX279133_ETH`。
   kconfig 会把「依赖是模块」的 tristate 夹成 `=m`：
   `scripts/kconfig/symbol.c:471-474` 取的是
   `EXPR_AND(用户给的值, 符号的可见性)`，而依赖为模块时该符号的可见性是 `mod`
   而不是 `yes`，所以 `EXPR_AND(yes, mod) = mod`。
   ⇒ `=y` 被夹成 `=m`，**全程不报任何错**。
6. **板级 DTS 确实点了这个交换芯片。**
   `arch/arm64/boot/dts/zte/zx279133-sk-d840n.dts` 里有
   `switch { compatible = "zte,zx279133-switch"; ... }`，
   而 `zx279133-switch.c` 的 `of_match_table` 正是这个 compatible。
   模块不会被自动加载 ⇒ 这个节点**永远不会 bind** ⇒ 四个 DSA 用户口不会出现。

**⇒ 修复**：在 7.3 工作流里加一步「Build the zte drivers into the kernel」，
把 `ZTE_ZX279133_ETH`、`ZTE_ZX279133_LAN_CONDUIT`、`ZTE_ZX279133_SWITCH`
和 `ZTE_ZX279133_FNOFFLOAD` 写进**最后合并的那个片段**
（`target/linux/zte/zx279133/config-7.3`），每写一个就断言一次；
再在 Compile 之后加一道门禁，对着**合并后的内核 `.config`**（不是被改的片段）
检查这四个符号是 `=y`。

**⇒ 第四个符号（flow offload）为什么也钉**：
cnjn 的 7.3 树把 offload 从 NPU 驱动里拆成独立的 `fnoffload.o`
（`obj-$(CONFIG_ZTE_ZX279133_FNOFFLOAD) += fnoffload.o`），
而 6.18 树把它放在复合目标 `zx279133-y` 里面（`kernel-overlays/.../Makefile:12`）
⇒ 6.18 线上它是**内建**的，7.3 线上它是模块 ⇒ 不在镜像里。
它自己的依赖 `NF_FLOW_TABLE` 由 `configs/sk-d840n-offload.config`
（`CONFIG_NF_FLOW_TABLE=y`）在 Configure 步钉成内置
（`scripts/sk-d840n-offload.sh` 的 `skd840n_pin_offload_fragments`
把整份档子写进三个片段），所以 `=y` 是够得着的。
不钉它，**整份 netfilter 档子就白进内核了**——那几十个符号是给 flow offload 用的。

**⇒ 四个驱动并进同一个内核会不会符号冲突**：本机用编译预言机编出的目标码
直接查过——`zx279133-offload.c.o`、`zx279133-switch.c.o`、
`zx279133-lan-conduit.c.o` **一个全局符号都不定义**（里面的函数全是 `static`，
`nm` 出来全是小写 `t`/`d`/`b`），全局名字只来自 NPU 驱动自己的那些目标码
（`np.c.o` 有 23 个 `T`）。⇒ 四个链接单元合成一个镜像不会撞名。
（`scratch/dup_syms.py` 就是干这个的。注意：**函数的 `static` 常常写在上一行**，
用行级正则找「非 static 定义」会看漏——目标的 `nm` 结果才是准的。）

**⇒ 为什么改在 7.3 工作流里而不是 `scripts/configure-sk-d840n.sh`**：
那个脚本两条线共用，`KCFG` 在 7.3 线上是 `config-7.3`、在 6.18 线上是 `config-6.18`；
在脚本里加 `pin_y ZTE_ZX279133_ETH` 会**同时改动 6.18 发布线的配置片段**。
6.18 线本来就是 `=y`（上面第 3 条有读数），改了也是空操作，但发布线不该由
一次 7.3 线的排查去碰。放在 7.3 工作流里，6.18 发布线一个字节都不动。

**⇒ 门禁**：
- `tests/test_sk_workflows.py` 新增一节「the 7.3 line builds the zte drivers into
  the kernel」：钉的三个符号一个都不能少、必须写进最后合并的片段、
  门禁必须读**合并后的 `.config`**（不许读片段）、必须断言 `=y`（不许只断言「存在」）。
- **反向验证做过**：五处变异逐个转红且转红在正确的检查上——
  ① 不钉 `LAN_CONDUIT`；② 钉到 `generic/config-7.3` 而不是 subtarget 的；
  ③ 门禁改读片段；④ 门禁改成「存在即可」；⑤ 门禁不查 `SWITCH`。
  （第一次跑时第 ② 项**没有转红**，因为 `/dev/mem` 那一步里也有同一个片段名，
  变异打错了地方；把 pin 步的变量改名成 `DRVCFG` 之后才真正打到点上。
  **这正是「门禁必须做反向验证」的意义：自己写的门禁自己也可能测不到点上。**）

**⇒ 本机这一轮的收获**（四个 scratch 工装，都是为了不花 50 分钟去问 CI）：
- `scratch/kobjmap73.py`：把 Kbuild 的 `obj-$(CONFIG_X)`、复合目标（`npu-y`）
  和条件成员（`npu-$(CONFIG_X)`）解出来，回答「**哪些 `.c` 真会被编进内核**」。
  写这个是因为「`obj-$(CONFIG_X)` 对应的 CONFIG 没开 ⇒ 那个文件被静默跳过」
  这件事编译器不会告诉你，clang 检查器也不会——它只查被点名的文件。
  本机跑出来的结论：7.3 树里 `zx279133-switch.c`、四个 `npu-y` 成员
  **本来都会被编**（前提是 CONFIG 打开），`dinghai/en_pf.c` 与
  `zx279133-native-debug.c` 不会（对应 CONFIG 没开）。
- `scratch/cpio_pick.py`：按 magic 自动选解压器（gzip/xz/zstd/lz4）后读 newc cpio，
  从目标固件的 initrd 里把 `/etc/modules.d/*` 挖出来——厂商怎么加载模块就是这么看见的。
- `scratch/pin_step_check.py`：把工作流里那一步的 shell **从 YAML 里原样取出来**
  （不复制，避免两边漂移），在 cnjn 的真实 `config-7.3` 副本上跑一遍，
  验证「三种写法（有值 / is not set / 根本没有）」都能被改成 `=y`。
  第一次跑就抓到我校验脚本自己写反了（查的是改**前**的行）——**工装也会骗人**。
- `scratch/reverse_check_workflow.py`：门禁的反向验证器，五处变异逐个跑。

**未做 / 遗留**：
1. **`ZTE_ZX279133_RGMII` 在 7.3 树里没有 Kconfig。**
   `scripts/configure-sk-d840n.sh` 的 `pin_y ZTE_ZX279133_RGMII` 会往
   `config-7.3` 里写一个**不存在的符号**，defconfig 静默丢掉。
   ⇒ 6.18 线上那个「RGMII MAC 寄存器块只读探针」在 7.3 上不存在。
   这是取舍还是缺口，等对拍时看能不能读寄存器再判。
2. **内核体积。** NPU（加上 flow offload）进 `vmlinux` 之后要放进固定的
   8 MiB `kernel` 分区。`-Os` 已经由 `skd840n_pin_kernel_size` 钉住，
   但 7.3 的底子比 6.18 大，**flash 那一路可能撑不下**；
   撑不下的话是 `Image too big` 报错，不是静默失败。
   7.3 线的「Collect the images」步会把内核镜像的字节数打进 annotation。
3. **`ZTE_ZX279133_RTL8372N` 仍是 `=m`。** 它是 SR1010 那块外挂 RTL8372N 的驱动，
   这块板子上没有对应节点，模块不加载即可，没有副作用。

---

### 7.13 7.3 线的镜像阶段缺 zopfli：内核编完了也会死在打包上

**判决**：7.3 线**即使内核编译成功**，镜像阶段也会失败，
因为压缩内核的脚本要 `import zopfli.gzip`，而这条线既没装 zopfli，
也没告诉镜像 profile 该用哪个 python。

证据链：

1. **镜像的内核是用 zopfli 压的，不是 gzip。**
   `overlays/target/linux/zte/image/Makefile:15-18` 的 `Build/skd840n-gzip` 是
   ```make
   define Build/skd840n-gzip
   	$(SKD840N_HOST_PYTHON) -u $(TOPDIR)/scripts/skd840n-mkgzip.py $@ $@.new 10
   	mv $@.new $@
   endef
   ```
   而 `SKD840N_HOST_PYTHON ?= python3`——**默认是系统的 python3**。
2. **`scripts/skd840n-mkgzip.py` 顶层就是 `import zopfli.gzip`，没有兜底。**
   为什么非要 zopfli：这块板的 `kernel` 分区是固定 8 MiB（8,388,608 字节），
   脚本自己的注释记着——`gzip -9` 最好的一次是 8,397,374 字节，**还差 8,766 字节**
   （zlib 其它策略更差：Z_RLE 13.4 MB、Z_FIXED 9.4 MB、Z_HUFFMAN_ONLY 13.8 MB、
   Z_FILTERED 9.3 MB），而 zopfli 十次迭代加分块是 8,069,537 字节。
   `mtd write` 会**无声地截断**超长的镜像，只有 `mtd verify` 才看得出来。
   ⇒ gzip 不是「更好的选择」，是**装不下**。
3. **6.18 发布线有这一步，7.3 线没有。**
   `build-sk-d840n.yml:180-190` 有「Prepare firmware tools and upgrade regression tests」：
   建 venv、`pip install 'zopfli==0.4.3' 'PyYAML==6.0.3'`、
   `echo "SKD840N_HOST_PYTHON=$PY" >> "$GITHUB_ENV"`。
   `build-sk-d840n-73.yml` 里 **zopfli 一个字都没有**，
   `SKD840N_HOST_PYTHON` 也没有 ⇒ 镜像阶段必然
   `ModuleNotFoundError: No module named 'zopfli'`。
4. **这个失败点很贵。** 它在 `make` 的**最后**（`target/install` 出镜像），
   也就是说内核已经编译完、用户态包也装完了，才在最后一步炸。
   前 13 轮之所以没报这个错，是因为它们更早就死在 `libzxpon-omci` 上。

**⇒ 修复**：7.3 线补上同样的一步（「The firmware tools」），
并带**两条断言**：zopfli 的绑定能被 import；
`tools/mkgzip.py` 自己能被 import（它的 import 在顶层，所以「模块能加载」就是那条断言）。
还断言 `SKD840N_HOST_PYTHON` 真的进了 `$GITHUB_ENV`——不然后面那两条都白做。

**⇒ 门禁**：`tests/test_sk_workflows.py` 新增一节「the 7.3 line has the tools the
image stage needs」：必须钉 `zopfli==0.4.3`、必须把 `SKD840N_HOST_PYTHON=$PY`
写进 `GITHUB_ENV`、必须检查镜像自己的脚本能被加载。
反向验证里对应三处变异，逐一转红在对应的检查上。

**⇒ 本机这一轮的收获**：`scratch/mkgzip_import_check.py` 在本机跑那条断言
（本机没有 zopfli，用一个桩替身顶上——要验的是**模块加载那段代码**，不是编码器），
并且**同时验了反面**：一个顶层 import 会失败的脚本**必须**加载失败，
否则这条断言就是空的。

---

### 7.14 7.3 线没有 6.18 那道「合并配置」门禁：镜像会编出来，但不一定能用

**判决**：7.3 线此前只有一道「四个 zte 驱动符号是不是 `=y`」的门禁，
而 6.18 发布线有一道更宽的门禁：它读**实际出货那颗内核**的合并 `.config`，
断言 **39 个符号**是 `=y`，另外断言优化级别、`PANIC_TIMEOUT` 和内建命令行。
⇒ 一份丢了 Realtek PHY、丢了整族 netfilter、或者带着 SR1010 的 `mtdblock7`
内核命令行的配置，**照样能编出一个「构建成功」的镜像**，
而这几种失败在板子上全都长得像「内核起不来」。
已补上这道门禁；补之前先在本机证明了它在这棵 7.3 树上**不会永远变红**。

#### 7.14.1 证据：两条线的步骤差在哪

`scratch/stepdiff.py`（新增）把两条工作流的步骤清单逐条对照：
6.18 线 44 步、7.3 线 30 步。6.18 独有的步骤里，有三道问的是
「镜像到底好不好用」，而不是「镜像有没有编出来」：

| 6.18 独有步骤 | 它问什么 |
|---|---|
| `Assert the kernel really has the PHY driver`（`build-sk-d840n.yml:1202-1342`） | 合并 `.config` 里那 39 个符号是不是 `=y`；`CC_OPTIMIZE_FOR_PERFORMANCE` 是不是没开；`PANIC_TIMEOUT` 是不是 0；`CONFIG_CMDLINE` 是不是那条钉死的命令行；有没有 `mtdblock7` |
| `Gate: ONU UI, protocol service and firewall4 reached the image` | 根文件系统里有没有 LuCI、协议服务、firewall4 |
| `Gate: every dtb compatible has a driver in this kernel` | 板级 DTS 里每个 `compatible` 在这颗内核里是不是真有驱动 |

7.3 线当时**一道都没有**。第一道是本节补的；第二、三道见第六节未做项。

#### 7.14.2 一个曾被挂着的未决项，现在有答案

「7.3 线要不要把整套 netfilter 也钉成内置（6.18 的做法）？」——**答案是：已经是了。**

- `configs/sk-d840n-offload.config` 里有 **49 个** `=y` 符号，整族 netfilter 都在里面
  （`NETFILTER` / `NF_CONNTRACK` / `NF_NAT` / `NF_TABLES` / `NFT_*` / `NF_FLOW_TABLE` /
  `NFT_FLOW_OFFLOAD` / `VETH` / `NET_SCH_*`）。
- `scripts/sk-d840n-offload.sh` 的 `skd840n_pin_offload_fragments` 把它**写进三个
  `config-7.3` 片段**（`target/linux/generic`、`zte`、`zte/zx279133`），
  每个片段里都先删掉旧行再追加 `=y`。
- 所以「裸片段里 netfilter 是 `=m`」这件事只描述 **Configure 之前**的状态；
  Configure 之后（也就是编译时看到的那份配置）它们全是 `=y`。
  本机预言机重放整条序列后实测：39 个必需符号 + 4 个 zte 符号，**全部 `=y`**。

⇒ 换句话说，7.3 线在 netfilter 这件事上**从来没有落后过 6.18 线**；
落后的只是「有没有一道门禁去确认它」。

#### 7.14.3 「写了 `=y`」不等于「会是 `=y`」

kconfig 把请求值和符号的**可见性**取 AND（`scripts/kconfig/symbol.c`）：
依赖是模块，tristate 就被夹成 `=m`，bool 就被夹成 `=n`——**全程无警告无报错**。
所以要证明某个符号真的是 `=y`，必须对着**整条依赖链**证明，不能对着被改的片段证明。

本机为此做了两件工具（都在 `scratch/`，不进仓库）：

- `scratch/cfg73_sim.py`：**重放整条配置序列**并产出合并后的 `.config`。
  它的每一个钉子都是从仓库自己的文件里**读出来**的，不是抄在这里的：
  三个 `config-7.3` 片段来自 cnjn 的克隆，工作流的 `/dev/mem` 编辑、
  四个驱动钉子来自 7.3 工作流，`pin_y` 名单、`CC_OPTIMIZE_FOR_SIZE`、
  `PANIC_TIMEOUT=0`、`CONFIG_CMDLINE` 来自 `scripts/configure-sk-d840n.sh`，
  49 个 netfilter 符号来自 `configs/sk-d840n-offload.config`。
  合并规则是 kconfig 的规则：**后写的赢**。
- `scratch/kdeps73.py`：拿**树自己的 Kconfig 文件**走依赖闭包，
  按 kconfig 的三值逻辑求值（`y`=真、`m`=模、`n`/未定义=假；
  `!` 翻转真假但不动模；`&&`/`||` 按 kconfig 的规则归约）。

**结论：47 个符号（39 必需 + 4 zte + 4 抽查）全部 `value=y` 且 `visibility=yes`，
没有任何一个符号的依赖链上是 `=m`** ⇒ 它们在 7.3 线上**不会被夹**，
这道门禁**不会永远变红**。

> 预言机自己也错了四次，每一个都记在这里，因为它们全都让结论**看起来比实际更绿**：
> ① `fragments[2] = subtarget` 把后来的 offload 钉子覆盖回去了；
> ② `pin()` 会再加一次 `CONFIG_` 前缀，于是 `CONFIG_CMDLINE` 变成了
> `CONFIG_CONFIG_CMDLINE`；
> ③ `arch/um/Kconfig`（User Mode Linux，不参与 arm64 构建）也定义了
> `MAGIC_SYSRQ`，而且 `depends on MCONSOLE`，解析器先抢到了它；
> ④ 未在本树定义的符号被当成「真」，而 kconfig 里它是 `n`——
> 于是 `depends on !UML` 被算成不成立。
> 前两条让 22 个符号看起来是红的，后两条让 `MAGIC_SYSRQ` 看起来是红的。
> **教训**：预言机的结论要自己挑错，数字对不上时先怀疑工具。

#### 7.14.4 ⇒ 修复

7.3 线新增一步 **`Gate: the merged kernel config carries the board requirements`**：
读合并后的内核 `.config`，断言

- 那 **39 个符号**是 `=y`（清单**与 6.18 线逐字相同**）；
- `# CONFIG_CC_OPTIMIZE_FOR_PERFORMANCE is not set`；
- `CONFIG_PANIC_TIMEOUT=0`；
- `CONFIG_CMDLINE="console=ttyAMA0,115200n8 root=/dev/mtdblock5 rootfstype=jffs2 ro rootwait clk_ignore_unused pd_ignore_unused"`；
- 配置里**不得出现** `mtdblock7`；

失败时用 `::error::` 逐条报出**那个符号的实际状态**（`=y` / `=m` / `is not set` /
`.config` 里根本没有），并且把整张清单的状态一起打出来——
这四种情况从外面看一模一样，而它们是四个不同的原因。
仪器类符号（`DEVMEM` / `DETECT_HUNG_TASK` / `IKCONFIG_PROC` / `VETH` …）
照 6.18 的做法**只警告不拦**。

#### 7.14.5 ⇒ 门禁与验证

- `tests/test_sk_workflows.py` 新增一节「the 7.3 line asserts the release line's
  kernel requirements」。它**不是**把 39 个名字抄一份——抄的会漂。
  它把两条工作流里的 `required="..."` 各自读出来**逐字比较**，
  所以任何一边改了清单而另一边没改，门禁立刻转红。
  另外还断言：门禁读的是合并 `.config`（不是被钉的片段）、
  断言的是 `=y`（不是「出现过」）、并且 `-O2` / `PANIC_TIMEOUT` /
  内建命令行 / `mtdblock7` 四条都在。
- **反向验证**：新增 7 处变异（少一个符号、把 `=y` 放宽成「出现过」、
  改去读片段、去掉 `-O2` 拒绝、去掉 panic 拒绝、改掉内建命令行、
  整步删掉），逐个转红在**指定**的那条检查上。连同原有的 15 处，共 **22 处**全绿。
- **行为级演练**（新增 `scratch/boardgate_check.py`）：把工作流里那一步的 shell
  原样取出来，喂给它**预言机产出的那份真实合并配置**——
  ①必须通过（这证明门禁接受这条线真要编出来的那份配置）；
  然后逐个破坏：Realtek PHY 变模块 / netfilter 变模块 / 优化级别变 `-O2` /
  `PANIC_TIMEOUT` 变 1 / 命令行改成 `mtdblock7` / 符号在 `.config` 里根本不存在 /
  压根没有 `.config`——七种都必须转红，**并且红的原因要说对**。
  本机七关全绿。

#### 7.14.6 未做

6.18 那道 `Gate: ONU UI, protocol service and firewall4 reached the image`
**只搬了一半**：搬过来的是「包是不是真的装进了镜像」这一半
（7.3 线的新步 `Gate: the image carries the packages the board needs`，
读 `bin/targets/**.manifest`，要求 `zxpond` / `libzxpon-omci` /
`luci-app-sk-d840n-pon` / `luci-mod-system` / `firewall4` / `nftables` /
`tc-tiny` / `luci-app-firewall` / `rpcd-mod-ucode` / `ucode-mod-fs` 都在里面）；
**没搬的是** staged root 的文件清单、主机侧 ucode 解释器、
firewall4 自己的规则生成器测试套件——那三项问的是**发布线的产品面**，
不是这次迁移，而它们失败的话 7.3 线连镜像都出不来。

6.18 的另一道（`Gate: every dtb compatible has a driver in this kernel`）
**已经搬了，但不是照抄**：见 7.15。

### 7.15 dtb compatible 有驱动：这道门禁不能照抄，所以重写了一遍

**判决**：6.18 线有一道门禁问「板级 DTS 里每个 `compatible` 在这颗内核里是不是
真有驱动」。它**不能照抄**到 7.3 线上——照抄会因为一个节点变红。
但它问的问题恰恰是 7.3 线最该问的，所以**重写了一个本仓库自己的工具**。

#### 7.15.1 为什么不能照抄

6.18 那道门禁扫的是 **编译出来的 dtb**（`dtc -I dtb -O dts`），并且对
**每一个** `zte,zx279133-*` 的 compatible 做**整行匹配**
（`grep -Fxq`，不是子串——因为子串搜索会把 `zte,zx279133-pon` 判成"存在"，
只因为 `zte,zx279133-pon-serdes` 存在）。

本机把这道门禁的**内容**先查了一遍（`scratch/dtb_compat73.py`，读板级 DTS 源码，
因为本机没有 dtc）：7.3 板级 DTS 一共 **8 个 compatible**，
其中 7 个在这棵 7.3 树里都有驱动，**只有 `zte,zx279133-rgmii` 没有**——
而那正是八之五.9 判明「取舍不是缺口」的那一项（6.18 有只读探针，7.3 没有）。

⇒ 照抄会在这一个节点上变红。所以例外清单必须**明写理由**，
而不是把检查放宽。

#### 7.15.2 新工具问的是更强的问题

`tools/check-sk-dtb-compat.py` 问的不是「驱动在不在树里」，而是
**「compatible 字符串在不在已编译的内核镜像里」**：

- 驱动是**内建**时，它的 `of_match_table` 字符串在 `vmlinux` 里；
- 驱动是**模块**时，字符串在 `.ko` 里，不在 `vmlinux` 里。

⇒ 所以这道门禁同时是「驱动有没有退回成模块」的探测器，
而那正是 7.3 线**已经犯过一次**的错（NPU 驱动 `=m` ⇒ 镜像里根本没有 NPU，
也没有任何东西会加载它）。

#### 7.15.3 写这个工具时抓到的四个坑（都在设备树解析上）

1. **节点可以带标签**：`nppt: ethernet@19000000 {`。正则不认标签，
   这个节点就**根本没被打开**，它的 compatible 记到了**父节点**头上——
   而父节点是 `soc`。
2. **板级树用 `&label { status = "okay"; }` 打开节点**。这个覆盖必须落到
   它命名的那个节点上；不落的话，nppt 节点会保留 dtsi 里的 `"disabled"`，
   于是门禁**跳过它**——而那恰恰是整道门禁最该看的一个节点。
   这是**假放过**，比假报错更糟。
3. **compatible 列表可以跨行**：`compatible = "zte,zx279133-uart", "arm,pl011",`
   后面还有几行。只看单行会漏掉整个 uart 节点。
4. **注释里也会出现 compatible**：板级树把厂商树的那一段**引用**在注释块里，
   不剥注释就会凭空多出一个节点。

另外两条是**规则**而不是 bug：
5. **只查 `zte,zx279133-*` 这一族**。设备树里有大量**不是**设备 compatible 的
   字符串——`arm,cortex-a53` 是 CPU 类型，cpu 驱动用 `cpu_type` 匹配，
   根本没有 `of_match_table`；要求它有驱动字符串是在要求不可能的事。
   6.18 那道门禁画的是同一条线，而这一族正是风险所在（每一个都是我们手搬的，
   或者我们依赖的）。
6. **有 fallback 的节点由 fallback 服务**：primary 没有驱动但 fallback 有，
   不是错误（内核会绑到 fallback）；fallback 也没有才是错误。

#### 7.15.4 ⇒ 门禁与验证

- 7.3 线新增步 **`Gate: every compatible the device tree names has a driver`**，
  跑 `python3 tools/check-sk-dtb-compat.py --dts <板级 DTS> --image <编出来的 Image>`。
- 例外清单只有一条（`zte,zx279133-rgmii`），**带理由**；
  契约测试**直接 import 工具**读那个清单（不在测试里抄第二份，抄的会漂），
  并断言：清单**恰好**是这一条、每条理由不短于 80 字符。
- **反向验证**：新增 6 处变异（工作流 4 处 + 工具 2 处），
  其中「例外清单多一条」与「例外不写理由」都要转红。
  连同之前的 22 处，共 **29 处**全绿。
- **本机行为级演练**（`scratch/dtbcompat_check.py`）：
  真实板级树必须过（且 RGMII 的例外要被**打印出来**）；
  去掉 `zte,zx279133-nppt` / `zte,zx279133-switch` 的驱动字符串 ⇒ 转红并点名；
  去掉 primary 但留着 fallback ⇒ 仍然绿并说明「falls back to 'arm,pl011'」；
  去掉 fallback ⇒ 转红并点名；注释里的那个 RGMII 副本**不得**被算成第二个节点。

---

---

### 7.16 run #14：失败点前移到 `libbpf`，而 6.18 线同一个 head 全部成功

**判决**：run #14（head `0af2270`）**不再**报 Rust 包 `libzxpon-omci`——
7.11 那个工具链问题过去了。失败点**前移**到 `package/libs/libbpf`。
而这**不是内核的问题，也不是这个提交的问题**：6.18 发布线
**同一个 head `0af2270`** 的 run #364 **所有作业全部成功**。

#### 7.16.1 证据

1. run #14 的两个作业（flash / initramfs）都死在 **Compile** 步，
   而 annotation 只有四条，实质信息只有一行：
   `ERROR: package/libs/libbpf failed to build.`
2. **对照实验**：6.18 线 run #364（head `0af2270`，**同一个提交**）
   的四个作业（LED CI regression / native241 bootweb / initramfs / flash）
   **全部 success**。⇒ 仓库内容、包集、overlay 全都一样
   ⇒ **libbpf 在这个项目里是能编出来的**。
3. **libbpf 为什么会进镜像**：本仓库自己的 overlay
   `overlays/package/network/utils/iproute2/Makefile`：
   `Package/ip` 是 `DEPENDS:=+libnl-tiny +libbpf ...`，
   `tc-tiny` / `tc-bpf` / `tc-full` 也都带 `+libbpf`。
   而这个 overlay **两条线共用**。
4. **它的源码是从 github 克隆的**（cnjn master `31e2ac4` 的
   `package/libs/libbpf/Makefile`）：
   ```
   PKG_VERSION:=1.6.2
   PKG_SOURCE_PROTO:=git
   PKG_SOURCE_URL:=https://github.com/libbpf/libbpf
   ```
5. **两条线克隆的是同一个 OpenWrt 树**（同样的 `IW_URL` / `IW_SHA`）
   ⇒ 需要的包完全相同。
6. **6.18 线有 `dl/` 下载缓存，7.3 线没有。**
   6.18 线的缓存里早就躺着 libbpf，每轮直接复用；
   7.3 线每轮都重新克隆，而且**没有缓存兜底**。
7. **为什么「包日志里什么都没有」**：一个**从没被编译过**的包**没有自己的日志**；
   而下载/克隆失败的措辞是 "could not resolve" / "unable to access" / "fatal:"，
   **没有一个**落在旧的诊断模式（"fatal error:"、"error:"、"command not found"…）里
   ⇒ **诊断自己把原因漏掉了**，留下的只有包名。

#### 7.16.2 ⇒ 修复一：让构建不再依赖那一次克隆

7.3 线补上与发布线**同一把钥匙**的 `dl/` 缓存：

```yaml
      - name: Cache dl/
        if: inputs.clean_cache != 'true'
        uses: actions/cache@v4
        with:
          path: openwrt/dl
          key: skd840n-dl-${{ matrix.mode }}-${{ hashFiles('configs/*.config', 'overlays/**') }}
          restore-keys: skd840n-dl-
```

钥匙由**构建模式**与 `configs/` + `overlays/` 的哈希决定，而这两处在两条线里
是同一批文件 ⇒ **两条线共用同一份缓存**，7.3 线会直接拿到发布线已经下好的 libbpf。
（顺带：7.3 线每轮都在重新下载整个 feed，加了缓存之后到 Compile 的等待也短得多。）

#### 7.16.3 ⇒ 修复二：让下一次失败能说出原因

Compile 步现在按这个顺序取证：

1. 从顶层日志里读出**失败的是哪个包**；
2. 读那个包**自己的日志**的最后几行（**如果它有**）；
3. **专门**搜下载/克隆失败的措辞（"could not resolve"、"unable to access"、
   "RPC failed"、"fatal:"……）；
4. 再退回到编译错误的模式。

失败时用 `::error::cc:` 逐条发出（每步只留前 10 条，所以顺序有讲究：
先包自己的日志，再下载失败，最后才是编译错误）。

#### 7.16.4 本机验证

- 提取包名的 `sed` 在**真实的日志行**上跑通：
  `ERROR: package/libs/libbpf failed to build.` → `libs/libbpf`
  → `basename` → `logs/libbpf.txt`。
- 工作流里每一个 `run:` 块仍然都能 `bash -n`（`scratch/wf_shell_parse.py`）。
- 反向验证新增 4 处（缓存步没了、缓存钥匙与发布线不同、
  Compile 步不读包日志、Compile 步不搜下载失败），
  逐个转红在**指定**的那条检查上。
  其中「Compile 步不读包日志」**第一次没有转红**：
  契约测试查的是 `logs/$(basename`，而变异只改掉了两处出现中的一处
  （另一处没有 `PKGLOG=` 前缀）⇒ **又一次是「门禁比的是字符串而不是断言」**。

### 7.17 把编译预言机收进仓库，并接进 CI

**判决**：本机那套「用宿主 clang 检查 7.3 树」的检查器
（原来只活在 `scratch/kcheck.py` 里）现在**是仓库的工具**，
而且 **7.3 线在真正编译之前会先跑它**。

**为什么要收进仓库**：`scratch/` 是跨会话累积的工装区，
**换一台机器它就不在**，下一个会话也不会知道它存在过。
收进 `tools/` 之后它有版本、有门禁、有复跑命令。

**现在长什么样**：

| 文件 | 是什么 |
|---|---|
| `tools/kcheck.py` | 检查器本体：用宿主 clang 对 7.3 树做 `-fsyntax-only`，并按 modpost 的规则查符号闭合（`--objdir`） |
| `tools/kshadow.py` | 它依赖的「影子头文件」生成器：clang 不接受内核头里那种匿名成员，得给它一个等价形状，否则布局断言全挂 |
| `tests/test_sk_kcheck.py` | 判决规则的单元测试（不需要内核树、不需要编译器、不需要板子） |

**CI 里怎么用**：7.3 线的 build 作业里，在四个符号钉成 `=y` 之后、
**Compile 之前**多了一步「Check the drivers with the host compiler」。

**为什么这一步放在 build 作业、而不是 check-gates 作业**：
check-gates 作业**只有本仓库**，手里没有内核树；
要让它检查驱动，就得在那里重新下载 7.3.4 的 tarball、克隆 OpenWrt、
再打那 39 个补丁——而这**正是 build 作业开头已经做过的事**，
check-gates 的超时却只有 10 分钟。
⇒ 放在**树已经备好、Compile 还没开始**的时刻：
既不重复准备，又把「语法错误」从「五十分钟后」提前到「一分钟后」。
契约测试保证这一步**不会消失**，也保证它**排在 Compile 之前**。

**检查的是哪些文件**：不是手写的清单，而是
**本仓库的补丁系列碰过的每一个 `.c`，加上板级层新增的每一个 `.c`**。
契约测试从 `kernel-patches/7.3-local/*.patch` 与 `kernel-overlays-73/`
**算出来**这个集合，再和工作流里的清单比对
⇒ 新补丁碰了别的驱动，契约测试立刻转红，工作流里的清单不会漏。

**它也是这条线的输入**：7.3 线是靠路径过滤决定要不要起构建的，
而检查器与它的影子头文件生成器**就是构建要执行的东西**
⇒ `tools/kcheck.py`、`tools/kshadow.py`、`tests/test_sk_kcheck.py`
都在过滤里，契约测试也逐个断言。
（漏掉的话，改了检查器也不会有构建去跑它。）

**判决规则（关键）**：只看**我们自己的文件**里的错误。
编译器报在**内核自己的头文件**里的错误，说明**编译器读不动这棵树**
（CI runner 的 clang 比它读的 7.3.4 老），
这时我们文件里的错误可能是它的**后果**而不是我们的错 ⇒
工具把这类文件标成 `WARN` 并**不据此判红**。
这条规则是必须的，否则这道门禁会在一个**与我们的代码无关**的地方变红。

**本机验证**：

- **正向**：9 个文件（补丁碰过的 5 个 + 板级层新增的 4 个）全部 `OK`，
  退出码 0；
- **反向**：在树里复制一份驱动目录并注入一处语法错误 ⇒
  工具报 `FAIL`、退出码 1；
- **契约测试反向验证两处**：
  少列一个文件 ⇒ 转红在
  「the check names exactly the drivers this repository touches」；
  把这一步挪到 Compile 之后 ⇒ 转红在
  「the compiler check runs before the Compile step」。
  两条都是**只红在指定的那一条**。

**未做**：

- CI 里这一道的编译器是 runner 自带的（Ubuntu 22.04 的 `clang` 包），
  **比本机的 clang 23 老**。真出现「编译器读不动树」时，
  这一步会标 `WARN` 而不是判红 ⇒ **它可能放过一类错误**，
  但**绝不会因为编译器太老而误伤构建**。
- 本机那一版还带 `--objdir`（编出目标码、按 modpost 的规则查符号闭合），
  **CI 里没有开**：那 11 条疑似缺符号全是宏生成导出造成的误报，
  在逐条核实之前不适合当门禁。

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

### 八之三.5 第 4 片的预先量化：PON / OMCI 栈（74 个函数）

第 4 片的代码还没写，但**面已经量清楚了**，写之前先把账算出来：

| 文件 | 函数 | 字节 | 性质 |
|---|---|---|---|
| `zxpon.c` / `zxpon.h` | 48 | 18078 | **协议核心**（AES-128/CMAC、PLOAM、OMCI 状态机） |
| `zx279133-pon.c` | 23 | 10796 | 内核胶水：netlink、`omci` netdev、carrier、sysfs、LED 触发器 |
| `zx279133-omci.c` | 3 | 3131 | 把 OMCI 帧投进 IDM 的**队列 0**（管理队列） |
| `zx279133-omci-wire.h` | — | 635 | 三个描述符字（长度/控制/GEM），照抄 |

**内核 API 缺口：0。** 三个文件调到的内核函数，7.3 树里全有；`zxpon_state` 与
`zxpon_state_name` 是**我们自己**的 `zxpon.h` 里的（不是内核符号）。

`zx279133-omci.c` 用到的 `struct zx279133_eth` 字段，7.3 树里也全有：
`dev`、`base`、`datapath_lock`、`tx_descs`、`tx_prepared`、`tx_stopping`，
以及 `ZX279133_IDM_BASE`、`ZX279133_IDM_TX_DEPTH`、
`zx279133_idm_tx_done_reg()`、`zx279133_idm_tx_doorbell_reg()`、
`struct zx279133_idm_desc`。**只差 8 个 `omci_*` 字段**（`omci_buffer`、
`omci_dma`、`omci_done`、`omci_producer`、`omci_pending`、
`omci_submitted`、`omci_completed`、`omci_error`）。

**真正的移植工作不在代码，在四个钩子的落点**：

| 钩子 | 6.18 世代的调用点 | 7.3 树的落点 | 难度 |
|---|---|---|---|
| `zx279133_pon_attach(ndev)` | `main.c:1336` | `main.c` 里 `devm_register_netdev()` 之后 | 直接 |
| `zx279133_pon_carrier_kick(eth->ndev)` | `mac.c:730` | 7.3 用 **phylink**，`mac.c` 的 `link_up`/`link_down` | 要找准回调 |
| `zx279133_pon_note_rxsd(light)` | `main.c:824` | **7.3 没有对应物** | 见下 |
| `zx279133_pon_stop()` | `main.c:808`（清理动作） | `main.c` 的 `devm_add_action_or_reset()` | 直接 |

**`rxsd` 是第 4 片唯一的真问题。** 它是**光模块的接收信号检测**，在我们侧
不是寄存器而是一个 **GPIO**：`eth->opsd_gpio` 读 `opsd-gpios = <&gpio2 1
GPIO_ACTIVE_HIGH>`（板级 DTS 第 396 行，**板级层已经在 7.3 树里了**）。
7.3 的 `zx279133-main.c` 里**一个 GPIO 都没有**（实测：`gpio`/`gpiod` 零命中），
所以这个轮询得随第 4 片一起写进去：不写，`zxpon_set_los()` 永远不会被调用，
PON 的 carrier 永远不起来，OMCI 整条路径是死的。

**另外两件要一起做的事**：LOS 灯的 LED 触发器（板级 DTS 用
`linux,default-trigger = "zx279133-los"`，触发器名字由驱动注册），
以及 `npu.ko` 的 Makefile 挂法（6.18 是 `zx279133-y`，7.3 是 `npu-y`，
三个文件都没有自己的 `module_init`，所以进同一个模块即可）。

**用户态那半边已经在 7.3 线上了**：`overlays/package/utils/zxpond/` 与
`overlays/package/luci-app-sk-d840n-pon/` 由工作流的 `cp -a overlays/. openwrt/`
带进去，`zxpond` 用的 `zxpon.c` 与内核侧是**同一份代码**（`#ifdef __KERNEL__`
双栖）。

**⇒ 第 4 片的判决**：**可以搬**，而且大部分是照抄；要新写的是
`opsd` GPIO 的轮询与 LOS 触发器，以及 7.3 树侧的四个钩子落点。

---

## 八之四、第 4 片：PON / OMCI 栈（协议核心照抄 + 树侧四个钩子 + 一次 GPIO 轮询）

八之三.5 把面量清楚了。这一节是把它做掉之后的记录。

### 8之四.1 判决

1. **协议核心照抄。** `zxpon.c`（48 个函数，18078 B）、`zxpon.h`（3778 B）
   **逐字节**搬进 `kernel-overlays-73/`：文件里只有 `memcpy`/`memset`
   和 AES/CMAC/PLOAM/OMCI 的算术，`#ifdef __KERNEL__` 那一半只多包了
   `<linux/string.h>`。
2. **两个内核文件照抄。** `zx279133-pon.c`（23 个函数，10796 B）、
   `zx279133-omci.c`（3 个函数，3131 B）、
   `zx279133-omci-wire.h`（635 B）**逐字节**搬进 7.3 专有驱动树。
3. **树侧只差四个钩子和一次轮询**，写成补丁
   `140-sk-pon-omci-transport.patch`（9129 B）。
4. **「队列 0 是管理队列」这个前提在 7.3 里同样成立**——这是第 4 片唯一
   可能整体翻车的假设，所以先查了它（见 8之四.2）。

### 8之四.2 证据：描述符布局两代相同

OMCI 把帧写进 `eth->tx_descs + eth->omci_producer`，`omci_producer`
是 `0..TX_DEPTH-1`。如果 7.3 的数据面也用 0 号环，这一帧就会和数据帧
抢描述符。实测两边都是：

| | 数据面描述符下标 | 管理队列 |
|---|---|---|
| 6.18 | `tx_descs + (ZX279133_IDM_CPU_TX_FIRST + queue) * ZX279133_IDM_TX_DEPTH + producer`（`zx279133-netdev.c:650`） | 0 号环 |
| 7.3 | `tx_descs + (ZX279133_IDM_CPU_TX_FIRST + queue) * ZX279133_IDM_TX_DEPTH + producer`（`zx279133-netdev.c:389`） | 0 号环 |

`ZX279133_IDM_CPU_TX_FIRST` 两边都是 **1**（6.18 的 `zx279133.h:28`、
7.3 的 `zx279133.h:26`），`ZX279133_IDM_TX_DEPTH` 两边都是 1024。
⇒ 0 号环在两代里都是留给管理队列的，**OMCI 的下标不用改**。

### 8之四.3 证据：内核 API 在 7.3 树里都还在

本机没有 arm64 交叉工具链，编译只能走 CI（Compile 在作业第 70 分钟）。
所以先用**包含图可达性**把编译器会问的问题问掉：对 7.3.4 树做
`#include` 图的 BFS，看每个文件自己能不能看见它调用的每个名字。

| 文件 | 用到的内核名字 | 结论 |
|---|---|---|
| `zxpon.c` | `memcpy`/`memset`/`uint8_t`/`uint16_t`/`uint32_t` | 全部可见（`<linux/string.h>` → `<linux/types.h>`） |
| `zx279133-omci.c` | `readl`/`writel`/`lower_32_bits`/`upper_32_bits`/`cpu_to_le32`/`dmam_alloc_coherent`/`readl_poll_timeout`/`dma_rmb`/`wmb`/`mutex_*` | 全部可见（`readl`/`writel` 走 `<linux/iopoll.h>` → `<linux/io.h>`；`lower_32_bits` 在 `<linux/wordpart.h>`） |
| `zx279133-pon.c` | 43 个（netlink、netdev、LED、sysfs、workqueue…） | 全部可见 |

三个**在 7.3 里变过形状**的地方，单独查过：

- **通用 netlink**：7.3 的 `net/netlink/Kconfig` 里**已经没有**
  `CONFIG_GENERIC_NETLINK` 这个符号了——通用 netlink 变成无条件编译
  （`net/netlink/Makefile` 的 `obj-y := af_netlink.o genetlink.o policy.o`），
  `genl_register_family` 仍然以 `EXPORT_SYMBOL` 导出
  （`net/netlink/genetlink.c:840`）。⇒ **7.3 线不需要任何 netlink 的
  Kconfig 开关**，6.18 侧那句 `CONFIG_GENERIC_NETLINK` 的依赖在 7.3 里
  是多余的。
- **LED 触发器**：`led_trigger_register_simple` 在
  `include/linux/leds.h:479` 被 `#ifdef CONFIG_LEDS_TRIGGERS` 包着，
  而 `LEDS_TRIGGERS` 是 `menuconfig`、默认 n。cnjn 的
  `target/linux/zte/zx279133/config-7.3` 里有 `CONFIG_LEDS_CLASS=y`
  与 `CONFIG_LEDS_TRIGGERS=y`，**但这是第 4 片新引入的依赖**，
  所以门禁里逐条查这两行。
- **`dev->stats`**：`include/linux/netdevice.h:2292` 还在
  （注释写着「not used by modern drivers」），`zxpon_omci_xmit()` 的
  `dev->stats.tx_dropped++` 因此不用改。

### 8之四.4 补丁 140：树侧到底改了什么

`kernel-patches/7.3-local/140-sk-pon-omci-transport.patch`
（sha256 `99a75ffe459bf519f8ddaa3adcbe112d525309b5b798085629d26942fb857b28`），
改 5 个文件、119 行：

| 文件 | 改动 | 为什么 |
|---|---|---|
| `zx279133.h` | 加 8 个 `omci_*` 字段 + `pon_led_work`/`opsd_gpio`/`los_trigger`；加 7 个函数声明 | 两个内核文件要用的字段和入口 |
| `zx279133-main.c` | 加 `zx279133_pon_led_work`（200 ms 轮询 GPIO）、`zx279133_pon_cleanup`、`zx279133_pon_led_cancel`；probe 里取 GPIO、注册 LOS 触发器；`devm_register_netdev()` 之后 `zx279133_pon_attach()` | **7.3 的 main.c 里一个 GPIO 都没有**，不写这轮询，`zxpon_set_los()` 永远不会被调用，PON 的 carrier 永远不起来 |
| `zx279133-mac.c` | `mac_link_up()` 末尾 `zx279133_pon_carrier_kick(eth->ndev)` | carrier 跟着 PON 状态走，链路起来时要重算一次 |
| `zx279133-idm.c` | `zx279133_idm_tx_prepare()` 里先 `zx279133_omci_prepare()` 再 `memset` | 管理队列要和描述符数组的清零对齐，和 6.18 侧同序 |
| `Makefile` | `npu-y += zx279133-pon.o zx279133-omci.o zxpon.o` | 三个文件没有自己的 `module_init`，进同一个模块 |

**`mac_link_down()` 没有加 kick**，和 6.18 侧一致：6.18 的
`zx279133_mac_link_down()` 里也没有。原因是 carrier 跟的是 PON 状态机，
状态机自己每 100 ms 醒一次重算 carrier，link_down 的 kick 是冗余的。

**GPIO 的取法也照抄 6.18**：`devm_gpiod_get_optional(dev, "opsd", GPIOD_IN)`，
`-EPROBE_DEFER` 就返回（等 gpio2 注册），其它错误就当没有这根线。
板级 DTS 第 396 行有 `opsd-gpios = <&gpio2 1 GPIO_ACTIVE_HIGH>`，
7.3 树的 `arch/arm64/include/asm/…`（`zx279133.dtsi` 第 274 行）
`gpio2: gpio@10d10080` 没有 `status` 属性（默认 okay），所以这根线拿得到。

### 8之四.5 编译前的门禁：`tools/check-sk-pon-api.py`

和第 3 片同一个路子，把编译器会问的问题在 CI 编译之前问掉：

1. 五个文件在树里，且**与仓库带的那份逐字节相同**（sha256 比对）。
2. **协议核心闭合**：`zxpon.h` 声明的每个函数在 `zxpon.c` 里有定义，
   反之亦然。（`zxpon.c` 同时被用户态的 `zxpond` 编译，单边缺名字是那边的
   链接错误。）
3. **内核名字可见**：对每个文件算包含闭包，检查它调用的每个内核名字在
   闭包里**有定义**——不是「某个头文件里有」，而是「这个文件看得见」。
4. **内核侧契约**：树的 `zx279133.h` 声明那 7 个函数，
   `struct zx279133_eth` 带那 11 个字段。
5. **四个钩子在位**：`main.c` 的 attach/stop、`mac.c` 的 carrier kick、
   `idm.c` 的 `zx279133_omci_prepare()`。
6. **构建接线**：三个对象在 `npu-y` 里。
7. **新引入的依赖**：板级 DTS 有 `opsd-gpios` 与
   `linux,default-trigger = "zx279133-los"`；7.3 配置片段有
   `CONFIG_LEDS_CLASS=y` 与 `CONFIG_LEDS_TRIGGERS=y`。

### 8之四.6 验证（本机）

- **补丁 140 在「vanilla+39+100+110+120+130」基准树上 `git apply --check` 通过**，
  apply 之后的五个文件与手工编辑**逐字节一致**。
- `tools/apply-sk-overlay-73.sh` 在测试树 `scratch/test73c` 上跑通：
  「board layer applied …; the 7.3 driver set is intact」。脚本现在**多断言**
  八个由 7.3 线带进树的文件与仓库那份逐字节相同（`cmp -s`）——
  因为 PON/OMCI 的文件名与 6.18 世代同名，光靠「文件在不在」分不出是哪一代。
- `tools/check-sk-pon-api.py` 全绿（含内核名字可见性、协议核心闭合、
  四个钩子、构建接线、LED 依赖）。
- 本机四条门禁测试全绿：`test_sk_workflows.py` OK、
  `test_sk_kernel.py` 10/10、`test_sk_uboot.py` 21/21、
  `test_sk_offload.py` 10/10（skipped=1）、`tools/prepush.py` 0 problems。
- MANIFEST.json 里四个补丁的 sha256/bytes 全部校验 OK。

### 8之四.7 未做

1. **没有编译。** 这四个文件是 CI 第一次编译它们。
2. **没有实机验证。** bench 被别的会话占着，本轮没做任何实机操作。
   板上有光纤时才能验：LOS 灯随 `opsd` 翻转、`pon_state` 从 O1 往 O5 走、
   `omci_transport` 的 `submitted`/`completed` 在涨。
3. **上行（RX 方向的 PLOAM/OMCI 帧）没有接**——这不是第 4 片的缺口：
   `zxpon_rx_ploam()`/`zxpon_rx_omci()` 在 **6.18 侧同样没有任何调用点**
   （`kernel-overlays/` 全目录零命中）。OMCI 的上行是用户态的 `zxpond`
   走 netlink 的 `OMCI_TX` 命令下去的。第 4 片忠实复制了 6.18 的状态。
4. **PON MAC 窗口仍然没有编程。** `zx279133-pon.c` 的头注释写得很清楚：
   厂商模块只有 `mode_xgpon_nsyn_cfg`、没有 `mode_xgpon_nsyn_cfg_133`，
   所以那 49 字的 `xgpon_nsyn` 镜像不是这块板子该写的，`0x17000000`
   的 PON MAC 窗口一个寄存器都没碰。

---

## 八之五、第 5 片：9132 芯片本身的编程（`150-sk-9132-switch-programming.patch`）

### 8之五.1 判决：第 3 片搬的是 DSA 前端，**芯片的编程没有搬**

**判决：第 5 片的全部内容就是「把 9132 芯片本身的编程搬进 7.3 树」。**

第 3 片把 `zx279133-switch.c`（DSA 前端）和 `zx279133-9132.h`（9132 自己的寄存器图）
搬进了 7.3 线。但**芯片的编程不在 `switch.c` 里，在 6.18 侧的 `zx279133-np.c` 里**：

- 6.18 的 `switch.c` 里 `zx279133_eram_table_set_bits` / `zx279133_eram_table_get_bits`
  **零命中** ⇒ `switch.c` 不写 9132 的 ERAM 表；
- 6.18 的 `zx279133-np.c` 里，9132 的编程是一整段：每端口属性字、CPU 通路、
  flood 组、outport map、隔离矩阵、协议动作表、PHY 使能。

所以 7.3 线上的 9132 会注册出 DSA 口，但**芯片不会被配置**：
`zx279133_spa_indirect_read(eth, ppu, ZX279133_PPU_ATTR_REG, &attr)` 一次都没跑过，
没有镜像、没有 CPU 路径、没有 flood 组指向 CPU 口 ⇒ 四个 GE 口与 2.5G 口不通。

这一片把这段编程**逐字**搬过去（它是在这块板子上实测出来的），外加两处小改动。

### 8之五.2 证据一：两代 `np.c` 的函数面对照

| | 6.18 侧 | 7.3 侧 |
|---|---|---|
| `zx279133-np.c` 行数 | 6448 | 3108 |
| 函数总数 | 114 | 90 |
| 两边同名 | 74 | 74 |
| 只在 6.18 侧 | **40** | — |
| 只在 7.3 侧 | — | 16 |

只在 6.18 侧的 40 个函数里：

- **26 个**是本片要搬的 9132 编程（22 个 `zx279133_9132_*` + 4 个 `zx279133_eram_*`）；
- 4 个是 PPU 的 ME 助手（`zx279133_ppu_me_word` 等）——**本片不搬**，被搬的代码不用它们；
- 其余是 6.18 世代的其他增量。

只在 7.3 侧的 16 个里有 `zx279133_native_eram_io` / `zx279133_native_spa_io`
等——它们是 7.3 树自己的 debugfs 端点，替代 6.18 的 9132 控制台，
**所以 6.18 的 `zx279133_9132_show`/`_write`/`_next_token`/`_xbasex_word`/
`_off_aligned`/`_open`/`zx279133_9132_debugfs_init` 这一整套控制台不搬**。

### 8之五.3 证据二：被搬的代码在 7.3 树里的依赖

对 10 个入口函数做**调用闭包**（`scratch/closure9132.py`）：

- 闭包共 **32 个函数**：26 个要搬 + 6 个 7.3 树里已经有了
  （`zx279133_spa_indirect_read/write`、`zx279133_smmu0_read/write`、
  `zx279133_mf_eram_attr_set`、`zx279133_mf_eram_entry_write`）；
- 这 6 个助手的**签名在两代完全相同**（逐字比对，见 `scratch/check_port_dups.py`），
  所以搬过来的代码一个字都不用改；
- 用到的 `struct zx279133_eth` 字段只有两个：`dev`、`np_ind_lock`——**两个都在**；
- 用到的内核 API 只有 7 个：`mdiobus_read`、`mdiobus_write`、`of_mdio_find_bus`、
  `of_parse_phandle`、`of_node_put`，加两个宏 `MII_BMSR`、`BMSR_LSTATUS`
  （后两个在 `include/uapi/linux/mii.h`，7.3 树里都在）；
- 用到的 `ZX279133_*` 常量 35 个：3 个（`ZX279133_SMMU0_CMD_READ/WRITE`、
  `ZX279133_SMMU0_WORDS`）7.3 树里已有，4 个是 9132 自己的 PPU 号（放进我们自己的
  `zx279133-9132.h`），**其余 28 个由补丁带进 `np.c`**。

### 8之五.4 补丁 150：逐文件说明

`kernel-patches/7.3-local/150-sk-9132-switch-programming.patch`
（71010 字节，sha256 `d7d6cc44d1f265bfaae9d842e17c7c1af98a2e5f53c13eb12634ac2fee8a206e`），
改 2 个文件、1686 行新增 / 65 行删除：

| 文件 | 改了什么 |
|---|---|
| `drivers/net/ethernet/zte/zx279133-np.c` | 加 4 个头（`<linux/mdio.h>`、`<linux/mii.h>`、`<linux/of.h>`、`<linux/of_mdio.h>`）与 `#include "zx279133-9132.h"`；加 ERAM 表描述符与 4 个位操作助手；加 22 个 9132 编程函数与 6 张数据表；`zx279133_se_sdt_create()` 里记录 ERAM 描述符（4 行）；`zx279133_np_prepare()` 里按 6.18 的顺序调用这 10 步 |
| `drivers/net/ethernet/zte/zx279133-idm.c` | `zx279133_idm_rx_lan_vport()` 加 PPU→transport VID 映射（7 行）；`zx279133_idm_tx_drain()` 回收 OMCI 管理队列并等 `omci_pending`（2 行） |

**调用顺序必须与 6.18 一致**，其中一处容易放错：
「端口字基线读取」（`zx279133_9132_port_words_read()`）在 6.18 里是
**`zx279133_spa_prepare()` 之后、`zx279133_nppu_early_prepare()` 之前**——
它的用途是读出 bootloader 留下的每端口字，放晚了读到的就是驱动自己写的值。
门禁里有一条专门盯这个位置（反向验证已实测：挪到 SMCT 之后门禁转红）。

搬运是**逐字**的：从 6.18 的 `np.c` 里按 44 个区块（1419 行，含注释）原样取出，
按依赖顺序拼进 7.3 的 `np.c`。脚本 `scratch/port_9132.py` 会在写盘前拒绝两件事：

1. **重定义**——补丁带来的 30 个 `ZX279133_*` 宏若与树里已有的撞名就中止；
2. **前向引用**——拼装后若有函数先于它调用的函数出现就中止。

### 8之五.5 ERAM 表描述符：为什么必须一起搬

9132 的表在 ERAM 里，而 **ERAM 表的基址不是常数**：`zx279133_se_sdt_create()` 用一个
顺序分配器把块发出去，从块 12288 开始，每张表拿走剩下的块、下���张接在后面。
硬件按 `(base << 7) + (width * index & ~0x7f)` 找表项——

- 基址写错 ⇒ 硬件根本找不到那张表；
- 表项宽度写错 ⇒ 硬件在错的位置读表项。

6.18 的做法是：**在分配的那一刻把描述符记下来**（`zx279133_eram[sdt_no].base` /
`.bit_len`），之后按表 id 查。7.3 的 `se_sdt_create()` 与 6.18 的**逐字相同**，
只差那 4 行记录——所以补丁 150 在同一个位置加上它们，
而不是在 `np.c` 里另抄一份分配逻辑。

### 8之五.6 两个小改动

**PPU → transport VID。** IDM 把帧的源端口放在描述符的 word 1 里，而在 9132 这条路上
那个数是**芯片自己的 PPU 号**（四个 UNI 口是 PPU 1..4，2.5G 口的 lane 是 PPU 5），
不是 transport VID。7.3 树的 `zx279133_idm_rx_lan_vport()` 只认 transport VID，
于是从网口进来的帧**打不上任何标签**。补丁把 PPU 1..5 映射到 transport VID 59..63
（`ZX279133_LAN_TRANSPORT_VID_MIN` + PPU − `ZX279133_9132_UNI_PPU_FIRST`）。

**OMCI 管理队列。** OMCI 走的是 IDM TX 环的 **0 号队列**（管理队列，在数据面队列之下）。
7.3 的 `zx279133_idm_tx_drain()` 只回收数据面队列，管理队列手里捏着描述符不放。
补丁加了 `zx279133_omci_reclaim(eth)`，并把 `!eth->omci_pending` 一起等上。

### 8之五.7 编译前的门禁：`tools/check-sk-9132-api.py`

新增一扇门（与第 3、4 片同构），在 CI 里紧跟在 PON/OMCI 那扇之后跑，核对：

- 编程的 26 个函数在树里；ERAM 表描述符与数组在；
- **`se_sdt_create()` 里记录了基址与宽度**；
- 块里用到的每个 `ZX279133_`/`zx279133_` 名字都有定义；
- 私有头里有 4 个 PPU 号，且 `np.c` 与 `idm.c` 都 include 了它；
- 树自己的 6 个助手在；
- 7 个内核 API 在树里；
- **`np.c` 与 `idm.c` 里没有名字被定义两次**（前置声明不算，`u64_stats_inc()`
  这种宏调用不算）；
- **`np_prepare()` 按 6.18 的顺序调用这 10 步**；
- 板级 DTS 里有 `zx279133,uni-phy-bus`（PHY 使能要靠它找总线）；
- 树里的私有头与仓库携带的那份**逐字节相同**。

**双向验证**：对着打过补丁的树全绿；对着未打补丁的 vanilla 树**必须红**（已实测）。

### 8之五.8 验证（本机）

| 检查 | 结果 |
|---|---|
| 补丁 150 在 +140 基准树上 `git apply --check` | 通过 |
| 整链（+120 → 130 → 140 → 150）apply | 通过，2 个文件 1685+/65− |
| `check-sk-9132-api.py`（+150 + overlay 树） | 全绿 |
| `check-sk-9132-api.py`（vanilla 树） | 按预期红 |
| `check-sk-switch-api.py` / `check-sk-pon-api.py` | 未受影响，仍全绿 |
| `tests/test_sk_workflows.py` | OK（新步骤与新路径过滤都在） |
| `tests/test_sk_kernel.py` / `test_sk_uboot.py` | 10/10、21/21 |
| `tools/prepush.py` | 0 problems |
| 补丁文件换行 | 纯 LF（CR = 0） |

### 8之五.9 未做

1. **没有编译。** 这是 CI 第一次编译这段代码。
2. **没有实机验证。** bench 被别的会话占着。
   本片上板后应看到：四个 DSA 用户口的 link 状态正确、
   conduit（XMAC0）的 RX 计数在涨、2.5G 口能收发。
3. **TX 故障隔离与恢复机制不整体移植。** 它建立在 6.18 世代的
   `zx279133_datapath_attach`/`datapath_failed` 模型上，**7.3 树没有这个模型**
   （7.3 用 LAN service 的 `datapath_get`/`set_ready`/`put`），
   而且 7.3 的 `tx_reclaim_queue_locked()` 对非法完成增量是**钳位**
   （`completed = tx->pending`）而不是隔离。要搬就得连模型一起搬，是独立的一片。
4. **RGMII（`zx279133-rgmii.c`）不搬。** 它只是 `0x15900000` 窗口的 debugfs
   读写端点，而该块在本机全零、厂商 Linux 从不 bring up、2.5G 口其实是 9132 的铜口
   ⇒ 它的价值只是「可复现地看到这块是死的」。这是一个**取舍**，不是缺口。
5. **9132 的 debugfs 控制台不搬**（见 8之五.2），7.3 树有自己的 `native_*_io` 端点。

### 八之六、对拍的仪器：`tools/trialsurvey.sh`

对拍要回答的是五个问题，**每一个都有确定的读数位置**，这个脚本就是去读它们：

| 问题 | 读数位置 | 出处 |
|---|---|---|
| conduit 的 RX 计数在涨？ | `/sys/class/net/lan-cpu0/statistics/rx_{packets,bytes}` | `zx279133-lan-conduit.c:176` 把网设备名写成 `"lan-cpu%d"` |
| 四个 DSA 用户口 link 状态正确？ | `/sys/class/net/lan{0,1,2,3,4}/{operstate,carrier,speed}` | 板级 DTS 的 `switch` 节点声明 `port@4..port@8`，label 是 `lan0`/`lan2`/`lan3`/`lan4`/`lan1` |
| LOS 灯随 `opsd` 翻转？ | `/sys/class/leds/*/{brightness,trigger}` + `/sys/kernel/debug/gpio` 里 **gpio 65** 的电平 | `zx279133-main.c:297-299` 用 `gpiod_get_value_cansleep()` 读 opsd，再用 `led_trigger_event()` 点灯；opsd 在 DTS 里是 `gpio2 pin 1`（`GPIO_ACTIVE_HIGH`），平铺编号 65 |
| `pon_state` 从 O1 往 O5 走？ | `/sys/class/net/<wan>/device/pon_state` | `zx279133-pon.c:351` 在 `ndev->dev` 上建这个属性 |
| `omci_transport` 的 submitted/completed 在涨？ | `/sys/class/net/<wan>/device/omci_transport` | 同上，`zx279133-pon.c:353` |

脚本还做三件对拍必须的事：

1. **取两次样本**（默认隔 10 秒），这样「没动」是一个结论而不是一次读数。
2. **把 opsd 的电平与灯的亮度并排打出来，而且打两次**——
   「灯随光信号翻」这句话只有在两者同时可见、且前后两次都可见时才成立；
   只读一次，分不清「灯在工作」和「板上根本没有光信号」。
3. **PON 属性带上它属于哪个 netdev**（`eth0/pon_state: O5` 而不是 `device: O5`），
   因为 WAN 口和 conduit 口都在同一个 sysfs 根下。

**本机已演练**（`scratch/trialsurvey_check.py`）：合成一棵 `/sys/class/net` 树，
脚本要在完整树上把五个问题都答出来，且在四种坏树上**不能死**——
① 没有 PON 属性的树（驱动没挂上时的样子）；
② 某个网设备没有 `statistics` 目录；
③ 灯的 trigger 被用户换成了别的（脚本要如实说「驱动没在管这盏灯」）；
④ gpio dump 里没有 opsd 那一行（脚本要说的是「不在 dump 里」，不是「低」）。
并且演练**反过来也要成立**：把脚本改坏四处
（去掉 opsd 读数、去掉第二次采样、去掉 trigger 判据、去掉 netdev 名），
检查必须转红。
脚本的路径是变量（`SK_CLASS_NET`/`SK_DEBUGFS`/`SK_LEDS`/`SK_LOS_LED`/`SK_OPSD_GPIO`），
就是为了能在本机用合成目录跑。

#### 八之六.1 写这个仪器时抓到的三个坑（都是「测的是自己的假设」）

**坑一：opsd 的读数位置写错了，而旧的演练把它掩盖了。**
第一版的脚本是这样找 opsd 的：

```sh
sed -n 's/^\([0-9]*\).*opsd.*/    gpio \1: opsd/p' "$DEBUG/gpio"
grep -o 'opsd[^|]*' "$DEBUG/gpio"
```

**内核的 debugfs gpio dump 里没有 "opsd" 这个字样**——
它写的是 gpio chip 和 pin：

```
GPIOs 64-95 (gpiochip2):
 [65] 65-66 (gpio 65)  1c280000.digital  in  high  IRQ ACTIVE HIGH
```

consumer 的名字（`opsd-gpios`）只存在于设备树里，dump 里不会有。
⇒ 这两行**永远匹配不到**，是死代码。

为什么没被发现？因为**旧的演练喂给脚本的 gpio 文件里写着 "opsd"**：

```
gpio-0  (opsd) in hi ACTIVE HIGH
```

演练和脚本互相印证了一个谁也没去核实的假设。
现在演练改成喂内核真正的形状，断言改成「opsd (gpio 65): high」，
并加了一条反向验证：把脚本里的 opsd 读数删掉，检查必须转红。

**坑二：灯只采了一次，「翻转」这个动作根本没被观测到。**
原来第 3 节打印灯和电平，第 6 节（第二次采样）只打印计数器。
⇒ 「灯随 opsd 翻转」这个命题一次都没被验证过。
现在第二次采样也打印灯与电平，演练断言 `opsd` 至少出现两次。

**坑三：PON 属性打的是 `device: O5`，看不出是哪个口。**
`$CLASS/*/device/pon_state` 的 `dirname` 是 `.../eth0/device`，
`basename` 出来是 `device`。现在取两级，打成 `eth0/pon_state: O5`。

#### 八之六.2 对拍时必须一起看的一对读数：LOS 与 `pon_state`

`zx279133_pon_note_rxsd()`（`zx279133-pon.c:321-329`）把**同一个** opsd 读数
写进 PON 状态机：

```c
void zx279133_pon_note_rxsd(int light)
{
	...
	zxpon_set_los(&zx_state, light ? 0 : 1);
	...
	zxpon_apply_carrier();
}
```

而 `zx279133-main.c:299` 用它点灯：`rxsd ? LED_OFF : LED_FULL`
⇒ **有光时 LOS 灯灭，无光时 LOS 灯亮**（LOS = Loss of Signal，红灯亮就是没光）。

⇒ 所以：**板上没有光信号时，`pon_state` 本来就不会往 O5 走。**
判读必须是成对的：

| opsd | LOS 灯 | `pon_state` 不动 | 结论 |
|---|---|---|---|
| low（无光） | 亮 | 是 | **正常**：无光时状态机不会前进，不是驱动故障 |
| high（有光） | 灭 | 是 | 驱动/PON 栈的问题 |
| high（有光） | 灭 | 否，O1→O5 在走 | 通过 |

只看 `pon_state` 不动就判「移植失败」，会把「这根光纤没插」误判成故障。

**未做**：没有在真机上跑过这个脚本（本机没有板子，也没有 7.3 镜像）；
演练用的是合成树，**不能替代实机读数**。

### 八之七、按能力对账，而不是按函数名

**判决：171 这个数字是对的，但「按函数名核对」这条办法是错的，
所以本文此前的「剩下的是 RGMII 与 TX 恢复」这句话也是错的。**

**为什么按名字对不行。** 第 1~2 片的补丁改的是 7.3 树里**已经存在**的函数：
`100` 改 `zx279133_idm_tx_configure()` 的寄存器值，
`110` 改 `zx279133_idm_rx_page_slot()` 用的帧落点，
`120` 改 `zx279133_idm_rx_flush_refill()`。
6.18 世代那三个对应的函数叫 `zx279133_idm_bp_ring_cfg()`、
`zx279133_idm_rx_find_header()`、`zx279133_idm_find_broadcast()`——
**一个名字都不在 7.3 树里**。
⇒ 按名字核对，这三个会被判成「没搬」；实际上第 1~2 片搬了，
而且 7.3 树**根本不需要**「找头」这个函数，因为帧落点是固定的 0x80。

**那 7.3 树自己怎么做的。** 7.3 树不是缺这些能力，是用另一套机制做：

| 6.18 世代的函数 | 7.3 树里的对应物 |
|---|---|
| `zx279133_bmu_threshold_fixup()` | `zx279133_bmu_prepare()` / `zx279133_bmu_restore()`（`zx279133-tm.c`） |
| `zx279133_red_gate_clear()` / `_restore()` | `zx279133_red_prepare()` / `zx279133_red_restore()`（`zx279133-tm.c`） |
| `zx279133_red_vendor_fixup()` | `zx279133_red_np1_prepare()` / `_restore()`（`zx279133-tm.c`） |
| `zx279133_tm_vendor_fixup()` | `zx279133_tm_prepare()` / `zx279133_tm_restore()`（`zx279133-tm.c`） |
| `zx279133_sopc_duplex_mask()` / `_ready()` / `_send_enable()` | `zx279133_sopc_prepare()` / `zx279133_sopc_restore()`（`zx279133-tm.c`） |
| `zx279133_xmac_base()` / `zx279133_xmac_reset_mask()` | `zx279133_xmac_reset()`（`zx279133-mac.c`） |
| `zx279133_ppu_get_me_val()` / `zx279133_ppu_me_word()` | `zx279133_ppu_me_stats_read()` / `zx279133_ppu_me_diagnostics_read()`（`zx279133-np.c`） |
| `zx279133_datapath_attach()` | `zx279133_lan_datapath_get()` / `zx279133_lan_datapath_put()`（`zx279133-netdev.c`） |
| `zx279133_lan_mmio_set()` | `zx279133_lan_nppt_write()` / `_read()`（第 3 片的 LAN 控制事务） |
| `zx279133_lan_strip_transport_vlan()` | 第 5 片的 PPU → transport VID 映射（`zx279133_idm_rx_lan_vport()`） |
| `zx279133_wan_phylink_start()` / `_stop()` | `zx279133-netdev.c` 里直接调 `phylink_start()` / `phylink_stop()` |
| `zx279133_idm_rx_has_ethertype()` | `eth_type_trans()`（`zx279133-idm.c`） |
| `zx279133_idm_xdp_pass_valid()` | `zx279133-netdev.c` 的 XDP 路径里的 `xdp_buff_has_frags()` 与长度检查 |

**账本。** 新工具 `tools/func-port-coverage.py` 把这件事变成可复跑的检查：
它拿 6.18 世代的驱动目录与 7.3 树对照，对**只有 6.18 有的每一个函数**，
要求它落进三个桶之一：

1. **同名在 7.3 树里** —— 125 个；
2. **账本里写着它搬到了哪里**（`CARRIED` 表，27 个）——
   **表里写的目标函数必须真的在 7.3 树里**，否则工具报「账本指着不存在的东西」；
3. **有处置记录**（`DISPOSITIONS` 表，19 个）——
   每条处置都必须说清 7.3 树用的是什么，不是「我们没搬」。

三个桶都不在 ⇒ **缺口**，工具非零退出。

**反向验证**（`scratch/reverse_check_port_coverage.py`，本机已跑通）：
删掉一条账本条目、删掉一条处置、把账本里的目标函数改成树里没有的名字，
工具**都必须转红**，且必须点名它丢了哪一条。

**为什么相似度不能当判据。** 工具本来还有一个「函数体相似度 ≥ 60% 就算搬过」的兜底，
实测发现它会**掩盖账本的缺失**：`zx279133_bmu_threshold_fixup()` 与某个
不相干的短函数恰好有 60% 的行重合，删掉账本条目后工具照样绿。
⇒ 已删掉这个兜底：**只有账本算数**，相似度只作为「最接近的是谁」的线索打印。

**未做**：
- 账本目前是**人工核实**的结论（本机逐个读过 6.18 侧的实现、在 7.3 树里找到出处）；
  没有进 CI（它需要 vanilla 7.3 树，而 CI 只准备打过补丁的树）。
- 27 个「换名搬过去」的对应物，是**能力层面**的对应，不是逐行等价；
  它们能不能在板子上真的工作，要等实机对拍。
- 「125 个同名对上」只说明名字在，**不说明实现没变**；
  同名的那些函数里有多少是逐字相同、多少是重写，需要另用
  `tools/func-body-diff.py` 逐个文件比（IDM 那一个文件已经比过，见第九节）。

### 八之八、第 6 片（实机对拍）：机械清单

**判决**：前五片落成补丁，第 6 片是**板子上的对拍**。
这一节是**操作顺序**：每一步该看什么、看到什么算过、不过怎么办。

#### 八之八.1 前置：镜像与三件套

CI 的「Validate and export the partitions」步会校验 sysupgrade 镜像
（fwtool 元数据、CRC、板型兼容、各成员大小、JFFS2 魔数、
以及 uImage 解压后**正好等于**编出来的那个 Image）并**原样导出**三个分区文件：

| 文件 | 去处 | 多大 |
|---|---|---|
| `flash-uImage` | 内核分区 | 8 MiB |
| `flash-dtb.bin` | DTB 分区 | 约 13 KiB |
| `flash-rootfs.jffs2` | rootfs 分区 | 约 4.7 MiB |

`staging/trial/flash-set.tgz` 是这三件套（6.18 线那一版的打包）。

**怎么拿到手**：CI 把它们作为 artifact 上传（`sk-d840n-73-images-flash`）。
artifact 的**列表**是公开的，**内容要登录态**
⇒ 用带登录态的浏览器下载那个 zip，解开即得六个文件：
三个分区文件、sysupgrade 镜像、校验报告，以及**一份 `sha256sums` 清单**。

**先校验再刷**：解开后先 `sha256sum -c sha256sums`；
清单对不上就不要碰板子。
这份清单是 CI 那边跟文件一起生成的，**而且它自己也在 CI 里被校验过一遍**
（导出步末尾的 `sha256sum -c`）——
手取下来的东西必须能自证没坏。

#### 八之八.2 刷机（两条路）

| 路径 | 命令 | 什么时候用 |
|---|---|---|
| 只换内核与 DTB | `sh tools/flash-round.sh write` | 板子上装着的 jffs2 已经是 7.3 线的那套 |
| 全量 | `sh tools/flash-round.sh write-root` | 第一次上 7.3 线，rootfs 也要换 |
| 从正在运行的系统直接写 | `sh tools/flash-direct.sh write` | 已经证明能进 userspace，想少动一次 flash |

设备是 `root@192.168.9.1`，密钥 `~/.ssh/id_onu`。
`flash-round.sh` 的**第一次必须先跑 `dry`**：它只勘察 flash 并把载荷挂在
`http://192.168.9.1/` 上，**一个字节都不写**。

#### 八之八.3 第一次启动：三个观察点

1. **串口**：内核 banner 的版本必须是 `7.3.4`（门禁已保证产物是它，
   但 banner 是板子上真正跑的那一个）；
2. **LOS 灯**：**有光 ⇒ 灯灭，无光 ⇒ 灯亮**（驱动是 `rxsd ? LED_OFF : LED_FULL`）；
3. **网络口**：四个 DSA 用户口都要出现（`lan0 lan1 lan2 lan3 lan4`）。

#### 八之八.4 对拍仪器

```sh
sh tools/trialsurvey.sh 10
```

它一次回答五个问题（见工具头部），每个计数**采两次**，
灯与 `opsd` **也采两次**。判读表：

| 观察 | 期望 | 不符时先查 |
|---|---|---|
| `lan-cpu0` 的 `rx_packets` 在涨 | 涨 | BP 池投递（补丁 120）、帧落点 0x80（补丁 110） |
| `lan0..lan4` 都在、`carrier` 正确 | 都在 | 9132 驱动没注册（`dmesg \| grep 9132`） |
| LOS 灯与 `opsd` **成对**读 | 有光⇒灭，无光⇒亮 | 只读灯不说事：必须同时有 `opsd` 的读数 |
| `pon_state` 从 O1 往 O5 走 | 有光时走 | **无光时本就不该走** ⇒ 必须与 LOS 成对判读 |
| `omci_transport` 的 `submitted`/`completed` 在涨 | 涨 | OMCI 队列（补丁 140） |

**注意第三条与第四条是耦合的**：`zx279133_pon_note_rxsd()` 用的是**同一个**读数，
所以「无光 ⇒ pon_state 不动」**不是故障**。

#### 八之八.5 三片的专项判据

| 片 | 判据 | 怎么读 |
|---|---|---|
| 第 3 片（9132 交换表驱动） | 交换表被写成厂商那张表 | bench 的 ERAM 读（`eram_read`）与文档 30/31/32 的表对 |
| 第 4 片（PON/OMCI 栈） | `omci_transport` 两个计数在涨 | `trialsurvey.sh` 第 5 节 |
| 第 5 片（9132 芯片编程） | 帧按 PPU→VID 映射落到 lan-cpu0 | `lan-cpu0` 的 RX 计数在涨，**且**收到的帧的 VLAN 与补丁 150 里那张 PPU→VID 映射一致 |

#### 八之八.6 不符时的取证顺序

1. `dmesg | grep -iE 'zx279133|9132|lan conduit|nppu'`（仪器第 7 节已经带这一条）；
2. `/proc/net/dev` 与 `ethtool -S lan-cpu0`（看有没有 RX 欠载）；
3. `cat /sys/kernel/debug/gpio`（`opsd` 的原始读数）；
4. bench 的寄存器读（`nppt_reg` / `soc_read` / `eram_read`）。

**不要**因为对拍不过就去整体移植 TX 故障隔离/恢复（**判为不整体移植**，见八之五.9），
也不要顺手去动 RGMII（**取舍不是缺口**）。

**未做**：这一节是**清单**，不是结论——它一行都还没有在 7.3 的镜像上跑过，
因为镜像还没出来（见 7.16 与 7.17 的 CI 轮次）。

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
4. **PON / OMCI**（约 56 个函数）。**第 4 片已落地**（见八之四）：协议核心与两个
   内核文件逐字节搬进 `kernel-overlays-73/`，
   `kernel-patches/7.3-local/140` 给 7.3 的树补上四个钩子、一次 `opsd` GPIO 轮询
   与 LOS 触发器。**未编译验证**——等 CI 的 Compile。
   这一片剩下的还是**实机对拍**：LOS 灯随 `opsd` 翻、`pon_state` 走 O1→O5、
   `omci_transport` 的 `submitted`/`completed` 在涨。
   和第 5 条「cnjn 会不会公开 npu-pon.ko」的强相关仍然存在：我们的栈只做
   状态机与下行，上行是用户态 `zxpond` 走 netlink 下去的。
5. **9132 芯片本身的编程**（26 个函数 + 6 张表）。**第 5 片已落地**（见八之五）：
   从 6.18 的 `np.c` 逐字搬进 7.3 的 `np.c`，并把 ERAM 表描述符的记录点补上；
   `kernel-patches/7.3-local/150` 一个补丁改 2 个文件。
   **未编译验证**——等 CI 的 Compile。这一片上板后的判据是
   **四个 DSA 用户口的 link 状态正确、conduit 的 RX 计数在涨**。
6. **RGMII 调试**（8 个函数）：**判为取舍，不搬**——它只是 `0x15900000` 窗口的
   debugfs 端点，该块在本机全零、厂商 Linux 从不 bring up（见八之五.9）。
7. **netdev/main/mac 的健壮性修正**（约 28 个函数）：核心是 **TX 故障隔离与恢复
   机制**，**判为不整体移植**——它建立在 6.18 世代的 `datapath_attach`/`datapath_failed`
   模型上，7.3 树没有这个模型，且 7.3 的 `tx_reclaim_queue_locked()` 对非法完成增量
   是钳位而非隔离（见八之五.9）。这一条要单独成片，且要连模型一起搬。

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

# 8) 第 4 片：PON / OMCI 栈的编译前门禁（overlay 拷入之后）
python3 ci-build-repo/tools/check-sk-pon-api.py \
        --tree linux-7.3.4 --repo ci-build-repo --openwrt <openwrt-checkout>

# 9) 第 5 片：9132 芯片编程的编译前门禁（overlay 拷入之后）
python3 ci-build-repo/tools/check-sk-9132-api.py \
        --tree linux-7.3.4 --repo ci-build-repo

# 10) 工作流自身的门禁（两条线都在推之前跑）
python3 ci-build-repo/tests/test_sk_workflows.py

# 11) 本机的编译预言机：clang 对 7.3 树做语法检查（秒级，代替 50 分钟的 CI）
#     仓库自己的工具，7.3 线在 Compile 之前跑的是同一个（见 7.17）。
#     配置片段直接给三个（工具自己合并，缺的那个会跳过）——
#     CI 里读的是 OpenWrt 克隆里的同一批文件。
python3 ci-build-repo/tools/kcheck.py --tree scratch/test73c \
        --config scratch/cnjn-iw/target/linux/generic/config-7.3 \
        --config scratch/cnjn-iw/target/linux/zte/config-7.3 \
        --config scratch/cnjn-iw/target/linux/zte/zx279133/config-7.3 \
        --file drivers/net/ethernet/zte/zx279133-np.c \
        --file drivers/net/ethernet/zte/zx279133-idm.c \
        --gen scratch/kgen73
# 加 --objdir scratch/kobj 会编出目标码并按 modpost 的规则查符号闭合；
# --raw 打印编译器的原话。
python3 ci-build-repo/tests/test_sk_kcheck.py   # 判决规则的单元测试

# 12) 哪些 .c 真会被编进内核（Kbuild 的 obj-$(CONFIG_X) 解析）
python3 scratch/kobjmap73.py --tree scratch/test73c \
        --config scratch/cfg73/config-7.3.merged

# 13) 驱动在 7.3 里是内建还是模块（决定镜像里有没有它）
python3 scratch/kcfg73.py

# 14) 厂商固件怎么加载模块：从目标固件的 initrd cpio 里挖出来
python3 scratch/cpio_pick.py scratch/re_other_fw/fit/initrd-1.bin \
        etc/modules.d/30-zte-npu etc/modules-boot.d/30-zte-npu

# 15) 在本机跑工作流里那一步的 shell（从 YAML 原样取出，不复制）
python3 scratch/pin_step_check.py

# 16) 门禁的反向验证：九处变异逐个转红
python3 scratch/reverse_check_workflow.py

# 17) 四个驱动并进同一个内核会不会撞名（用本机编出的目标码）
python3 scratch/dup_syms.py --objdir scratch/kobj \
        --nm <llvm-nm.exe>

# 18) 两条工作流的步骤清单对照（谁有谁没有）
python3 scratch/stepdiff.py --repo ci-build-repo

# 19) 7.3 线的合并配置预言机：重放整条配置序列，产出合并后的 .config
python3 scratch/cfg73_sim.py --repo ci-build-repo \
        --openwrt scratch/cnjn-iw --tree scratch/test73c

# 20) kconfig 的三值依赖闭包：证明那 39+4 个符号在这棵树上不会被夹成 =m
python3 scratch/kdeps73.py ZTE_ZX279133_ETH ZTE_ZX279133_SWITCH \
        ZTE_ZX279133_FNOFFLOAD ZTE_ZX279133_LAN_CONDUIT REALTEK_PHY \
        --tree scratch/test73c --config scratch/cfg73/config-7.3.sim --quiet

# 21) 合并配置门禁的行为级演练：喂真配置必须过，七种破坏必须红
python3 scratch/boardgate_check.py

# 22) 工作流里每一个 run: 块都必须能当 shell 解析
python3 scratch/wf_shell_parse.py \
        --workflow ci-build-repo/.github/workflows/build-sk-d840n-73.yml

# 23) dtb compatible 门禁的演练：真板级树必须过，缺驱动必须红
python3 scratch/dtbcompat_check.py

# 24) 板级 DTS 的 compatible 与 7.3 树的驱动对照
python3 scratch/dtb_compat73.py --tree scratch/test73c

# 25) 171 个函数逐条对账：每个都要有归属（同名 / 账本写明搬到哪 / 有处置），
#     未记账即非零退出；账本指着的目标函数必须真在 7.3 树里
python3 ci-build-repo/tools/func-port-coverage.py \
        ci-build-repo/kernel-overlays/drivers/net/ethernet/zte \
        scratch/test73c/drivers/net/ethernet/zte

# 26) 这道对账的反向验证：改坏四处必须转红
python3 scratch/reverse_check_port_coverage.py
```

第十节的 18~24 号命令里，`cfg73_sim.py` 与 `kdeps73.py` 是**一对**：
前者产出「这条线真会编出来的合并配置」，后者证明那 39+4 个符号在这棵树上
不会被 kconfig 夹成 `=m`。`boardgate_check.py` 吃的正是前者的产物，
所以这两条命令合起来回答的是同一个问题：
**「7.3 线新增的那道合并配置门禁，会不会永远变红」**——答案是不会。

25 与 26 号命令是**另一对**：25 号回答「171 个函数有没有落到地上」，
26 号证明这个回答不是靠「没查所以没报」蒙混过去的。
