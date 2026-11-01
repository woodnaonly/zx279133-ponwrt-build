# 这颗 SoC 里有 NPU，但没有人驱动它（2026-10-31）

## 0. 一句话

**ZX279133 的 8 核 NPU 是真的**（8 核 64 线程的包处理引擎簇），mainline 里也确实接上了一半（厂商微码在跑、PPU 在转发），但**引擎分派表没有按 flow 编程、PPS 的两条中断没有申请、用户态没有 `tc`** —— 这三样缺一，那 8 个核就一个也没在跑网络转发。

本轮做完了两件事：**把「不可见」变成「可见可编程」**（ME 表与 PPS 窗口的观测面 + 厂商等价的写接口），**把两个被写错的结论改掉**（`docs/94` 的「交换机硬件转发 开」与「8 核 NPU 没有网络转发用途」）。

同时本轮**证伪**了一个被广泛接受的成因：「CPU 侧 FDB 不同步 ⇒ 交换机学不到 ⇒ 泛洪给 CPU」—— 不是这个原因，见 §3。

---

## 1. 那个 NPU 是什么

### 1.1 芯片身份

ZX279133 = 双核 Cortex-A53 @1GHz + **8 核 NPU（网络处理器单元）**，行业资料描述为「8 核 64 线程的 NPU」，用途是包转发/QoS 的硬件加速。

> 来源说明（2026-11-01 订正）：这一段的**数字部分现在有一手证据**，不再只靠二手资料 ——
> **8 个 ME 核**来自厂商 `ppu_debug_get_spec_me_int_flag()`（`np_133.ko` 0x80c44）里的
> `cmp w22, #7` + `b.ls`（ME 号大于 7 直接被拒），
> **64 个 flow** 来自 ME 表的 16 字 × 4 槽 × 8 bit 与厂商
> `ppu_cluster_set_me_val_by_flow_num()` 的 `cmp w20, #0x3f`（flow 号大于 63 被拒），
> 「64 线程」对应这 64 个槽。分派链的完整形态（端口 → flow → ME 核）见 **docs/99 §1**。
>
> 但**公开渠道没有厂商的数据手册或规格页**：中兴官网的路由器产品页只写
> 「新一代高性能处理器」，从未出现 NPU 的核数；「8 核 64 线程」的说法只出现在
> 中文二手媒体（如 2025-12-17《WiFi路由器芯片全解》）里，那些文章也没有给出
> 原始链接。所以「一手原始文档 URL」这一项的答案是**不存在**，
> 取而代之的是上面那三条来自芯片自身代码的证据。
>
> 原文（2026-10-31）的来源说明保留在下面：这一段来自当时的网络检索
> （会话缓存 `reports/tool-outputs/71bc2d6c….readable.v1.jsonl`），
> **没有取到可引用的原始 URL，按二手资料标注**。它只用来解释「8 核」这个说法从哪来，
> 不作为任何设计依据 —— 下面所有结论都来自代码与实测。

### 1.2 厂商 DT 里没有叫 `npu` 的节点

全仓库 `npu` 标识符命中数为 0。厂商 DT 里对应的是这几个块（`staging/stock-dtb.txt:1485-1545`）：

| 厂商节点 | 地址 | 是什么 | mainline 对应 |
|---|---|---|---|
| `pps@18000000`（interrupts SPI 61/62） | 0x18000000 | **PPU 本体**：ME 表、指令存储、算法块 | `nppt` 节点的 `reg-names = "nppt","pps"` 第二块 reg |
| `nppt@19000000` | 0x19000000 | 包收发 | `nppt` 节点 |
| `idm@19280000` | — | 内部描述符存储 | `zx279133-idm.c` 已实现 |
| `woe@19240000` / `se@18040000` / `bmu@1903c000` | — | WOE / 安全引擎 / 缓冲管理 | 只有预留区，无驱动 |

mainline 侧：`staging/ktree-git/arch/arm64/boot/dts/zte/zx279133.dtsi:462-493`。

### 1.3 厂商内核里的真名：PPU cluster + ME

`pon/vendor-modules/np_133.ko` 导出的一串函数名说明了结构：

```
ppu_cluster_set_me_val_by_flow_num()   ppu_cluster_set_me_select_en()
ppu_cluster_set_me_busy_threshold()    ppu_cluster_set_thread_swicth_en()
ppu_cluster_set_pkt_type_detail()      ppu_set_inst()
pps_glb_set_ppu_core_agclk_en()         ppu_debug_get_me_int_stat
```

ME = microcode engine（微码引擎）。

### 1.4 ME 表的布局（反汇编实测）

`ppu_cluster_set_me_val_by_flow_num(flow_num, me_val)`（np_133.ko @0x829dc，aarch64，capstone 反汇编）：

```
cmp  w20, #0x3f          ; flow_num <= 63
cmp  w22, #0xff          ; me_val   <= 255
lsr  w23, w20, #2        ; 字索引 = flow_num >> 2
ubfiz w2, w20, #3, #2    ; 槽位   = (flow_num & 3) * 8
bic/orr                  ; 每槽 8 位地写 ME 值
```

⇒ **ME 表 = 16 个 32 位字 × 4 槽 = 64 个 flow 槽**，与「8 核 64 线程」吻合（ME 值选 8 个核之一）。mainline 侧对应常量在 `zx279133.h`：`ZX279133_PPU_CLUSTER_ME_TABLE 0x90350` / `_ME_WORDS 16`。

### 1.5 厂商微码里没有 ME 表

`firmware/zte/zx279133/mcode_intel.bin`（103456 B）的 tag 流按驱动自己的格式解析（magic `0xffffaaaa`，inst_num 12288，first 5166）：

| tag | 名字 | 条数 |
|---|---|---|
| 0xbdbdbdbd | PKT_TYPE | 512 |
| 0xcccccccc | DUP | 64 |
| 0xbbbbbbbb | PORT_FLOW | 64 |
| 0xaaaaaaaa | VERSION | 1 |

**没有 ME 表的 tag，也没有未知 tag。** 也就是说：厂商微码只带指令流和这三张表，ME 表是运行时才有的东西。

### 1.6 厂商的 ME 表由谁写：只有调试控制台

在 np_133.ko 里把 PLT 还原后逐个 `bl`/`b` 找调用点，结果：

- `ppu_set_inst()` 有 7 个调用点（其中 `np_downlaod_mcode` 就是 mainline 对照的那个）；
- **`ppu_cluster_set_me_val_by_flow_num()` 的调用点只有两处**，都在 @0xd49e8 / @0xd4ab8 —— 两个 tail call 的包装函数，参数从栈上取两个 u32（`ldr w0, w1, [x29,#0x20]` / `[x29,#0x24]`），也就是**从用户态传进来的裸数字**。

⇒ 厂商没有「按 flow 自动分派」的代码路径；它只有一个 debug console 命令（`/proc/pon/tm_debug` 那一套）让人手填 flow 号和引擎号。

### 1.7 flow 号是什么：PPU 自己的 64 个条目，不是软件 flow

ME 表的 64 个槽与厂商微码里那 **64 条 PORT_FLOW** 一一对应（顺序相同），也与 mainline 自己那张 `zx279133_9132_port_flow[]`（64 项，本板专用表，替换掉 blob 里的）同序。

⇒ **「flow」是 PPU 自己的条目编号。** TC flower 那条路（`zx279133-offload.c`）写的是 ZCAM、SE hash DDR 与 IKEY 三张表，**与 ME 表不在一个编号空间里**，也不经过它。

补充（2026-11-01，docs/99 §1）：**端口是怎么指向某个 flow 的**，这一节当时还没说清。
现在知道了 —— 每个 SPA 端口的属性字 0 的 bits 15..20 就是一个 6 bit 的 flow 号（0..63），
bits 1..14 是 start PC，bit 0 是 Tbl Vld。
另外，`zx279133_9132_port_flow[]` 这张表与 blob 里那 64 条 `PORT_FLOW` **逐位相同**
（48 个 blob 命名的端口全对，0 处不同），所以它不是「替换掉 blob 的另一张表」，
而是 blob 自己那张表的副本。

---

## 2. mainline 现状（本轮改之前）

| 能力 | 状态 | 证据 |
|---|---|---|
| PPU 微码装载 | ✅ | `zx279133-np.c:5448-5517`（`request_firmware("zte/zx279133/mcode_intel.bin")` + 指令块写入） |
| pkt-type / dup 表 | ✅ 从 blob 搬运 | `np.c:5535-5558` |
| port-flow 表 | ⚠️ blob 的 tag **被丢弃**，改用本板表 | `np.c:5570-5577` → `:5588` |
| **ME 分派表** | ❌ **16 个字统一写 `0x00000001`** | `np.c:189-219` |
| **PPS 中断（SPI 61/62）** | ❌ 未申请（厂商也没申请：`g_pps_irq` 全模块零引用） | `zx279133-main.c:54-60`、`:1304`/`:1309` |
| TC flower 落地 | ✅ | tree 的 `zx279133-offload.c:1516-1523`、`:1377-1402` |
| **TC flower ↔ ME 槽** | ❌ 零关联 | `offload.c` 内 `ME_TABLE|0x90350|flow_num` grep 零命中 |
| 用户态 `tc` | ❌ 没有（只有 `ip-full`） | 本轮已 pin |
| 用户态 `fw4` | ❌ 没有 | `build-sk-d840n.yml:238-244` |

板上的对应实测（2026-10-31 23:4x，ImmortalWrt 6.18.38）：

- `/proc/interrupts`：整机**只有一条**网口中断 `22: 29 0 GICv3 33 Level 19000000.ethernet`（GIC 33 = SPI 1 = nppt 的 `rx`）。PPS 的两条一次都没出现。
- `/sys/kernel/debug/zx279133-flows`：**空的**（没有 `tc`，没有规则可下发）。
- `dmesg`：`PPU microcode version 0x10001.0x1`。

---

## 3. 9132 二层转发：本轮读数与判决

### 3.1 板上读数（只读，2026-10-31 00:2x）

| 读数 | 值 | 怎么读的 |
|---|---|---|
| flood group 表 0x1f | PPU 1..5 的 **15 个组全是 `0x0000000f`**（目的口 = CPU trap 口 15） | 板上 `echo fg > /sys/kernel/debug/zx279133-9132` |
| 端口属性字（MF 副本）0x7d entry 5 | `0x96040001` —— bit31 `sa_lookup_learn_en` **开着**，bit28 `da_lookup_en` 也开着 | `eram_read 0x7d 5 32` |
| 协议动作表 0x18（2 bit/项） | index 640（proto 0 \| port 5<<7）= **2 = trap** | `eram_read 0x18 640 2` |
| 隔离矩阵 0x1b（1 bit/项） | entry 3 / 5 / 15 全 **0** | `eram_read 0x1b {3,5,15} 1` |
| PVID 表 0x25 | entry 5 = **0** | `eram_read 0x25 5 16` |

ERAM 表清单（板上 `cat /sys/kernel/debug/zx279133-9132`）：`0x18` base 19624 / 2 bit，`0x1b` base 19820 / 1 bit，`0x1f` base 19975 / 32 bit。

### 3.2 判决

**「CPU 侧 FDB 不同步 ⇒ 交换机学不到 MAC ⇒ 泛洪给 CPU」这个成因是错的。**

1. 硬件的 SA 学习本来就是开着的（属性字 `0x96040001`，bit31 置位），不是被 FDB 不同步破坏的。
2. 真正的挡路者是两条**芯片/厂商方案**层面的限制：
   - **flood group 一条组只装一个目的口**，而驱动把 15 个组全指到 CPU trap 口 15 ⇒ UNI↔UNI 没有可指的目的口；
   - **协议动作表 0x18 的 trap 取代转发**（action=2）。
3. 这与 `docs/92` §96.36 的判决 AA（`:13119-13122`）「**9132 根本不转发 UNI 口之间的帧**」、§96.38.1（`:13262`）「芯片只把 CPU 口发出的广播 flood 到 UNI 口」一致。

⇒ **按计划里的「先证伪」分支收口：本项结论是「芯片/厂商方案限制」，不做 DSA 侧的 `port_fdb_add/del` 与 `tx_fwd_offload`。** 理由不是「做不动」，而是「按那个成因去修，修不到点上」。

顺带纠正一处会误导人的因果：`tx_fwd_offload` 管的是**CPU 发出的帧**（上游 `net/dsa/port.c:509-515`），不是 UNI↔UNI；而 `docs/94` 的 95.8% 是 **PC → 板子 CPU** 的流量（`docs/94:13-15`），这类流量按设计必须进 CPU —— **二层硬件转发不会降低那个数字**。

**没做的**：UNI↔UNI 单播的干净实测。条件是 LAN1 通 + 至少两只口插线，而台面上 PC 只有一根有线网卡（板子只有 2.5G 口插着线），凑不出「两头主机」。这一条要等环境具备再补。

---

## 4. 本轮做了什么（代码）

全部落在 `ci-build-repo/kernel-overlays/` 与 `ci-build-repo/scripts/`、`ci-build-repo/.github/workflows/`（overlay 用 `cp -a` 覆盖 kernel tree，见 `build-sk-d840n.yml:202-203`）。

### 4.1 W1：`switch: lane` 日志止血

- `kernel-overlays/drivers/net/ethernet/zte/zx279133-switch.c:2097`：`dev_info()` → `dev_dbg()`。
- `scripts/configure-sk-d840n.sh`：`pin_y DYNAMIC_DEBUG` + `pin_y_build KERNEL_DYNAMIC_DEBUG`。
  后者是必须的：`LINUX_CONF_CMD` 把 build-root `.config` 里所有 `CONFIG_KERNEL_*` 去前缀后**追加**在 fragment 之后，build-root 的 `# CONFIG_KERNEL_DYNAMIC_DEBUG is not set` 会压过 fragment 的 `=y` —— 这正是 `CONFIG_DEVMEM` 在 run 42/43 上丢掉的同一种坑（`configure-sk-d840n.sh:455-473`）。
- **还要 `DYNAMIC_DEBUG_CORE`（第二轮补的，见 §4.7）**：只钉 `DYNAMIC_DEBUG` 会得到一个假的成功。
- `.github/workflows/build-sk-d840n.yml`：把 `DYNAMIC_DEBUG` 加进 kernel config 门禁的 **required** 列表（不是 reported）—— `dev_dbg` 在没有 DYNAMIC_DEBUG 时会编译成 `no_printk`，那样这条日志不是变成可开关，而是**彻底消失**。
- 顺带：`zx279133-switch.c:1028` 的 `dev_info("the lane was programmed again")` 在「PCS 保持空块」时会每个 20 ms pass 写一条（上限 50 行/秒）。改成**只在一轮重试的第一次打印**（episode 本身已经由 `:1013` 的边沿告警宣告过，重复的那部分没有信息量）。

**代价与补偿**：降级后，`nppt 88`/`8c` 的连续历史、稳定期证据（`docs/92` §96.47）、613/306 统计（§96.53）、§96.52 的方法载体**默认不再产生**。信息没有永久丢失 —— 任何需要这些证据的测量轮执行

```sh
echo 'file zx279133-switch.c +p' > /sys/kernel/debug/dynamic_debug/control
```

即可原样恢复。一次性快照（`outerphy_dump()` 每 boot 19 行、`lane_snapshot()` probe 前后各 26 行）不动。

**这条日志的真实形状**（本轮实测，修正了「每 0.8 秒刷屏」的粗略说法）：它是 `delayed_work` 在 probe 后 30 秒的快窗里按 750 ms 打印一次，**在 t≈32.4 s 停止**；本次开机 61 行、327 字节/行，历史上 1129 次开机每次 31–39 行、无一行晚于 32.6 s。它不是无限洪水，但占的是启动期 ring buffer 最金贵的窗口。

### 4.2 W2：ME 表的观测面与厂商等价写接口

- `zx279133-np.c` 新增：`zx279133_ppu_me_word()` / `zx279133_ppu_me_word_set()` /
  `zx279133_ppu_set_me_val()` / `zx279133_ppu_get_me_val()`，布局与厂商
  `ppu_cluster_set_me_val_by_flow_num()` 逐位一致（字 = flow>>2、槽 = (flow&3)*8、8 位）。
- probe 时**先打印再覆盖**：新加的 `ppu: factory ME word N = ...` 与
  `ppu: factory pkt-type detail = ...` 在驱动覆写之前把继承来的值留在启动日志里
  —— 这是这张表**唯一**还能看到「继承值」的地方（厂商侧只有 debug console 能看）。
- 新增 debugfs 动词：
  - `me` —— 打印 16 个字，并把每个字解成 4 个 flow 的引擎号；
  - `mes <flow> <me>` —— 写一个 flow 的引擎号并回读（厂商等价，带同样的
    flow<64 / me<256 限制）；
  - `pr <offset> [count]` —— **新增**：直接读 PPS 窗口（0x18000000）。ME 表
    （+0x90350）、簇指令存储（+0x90d0c）、PPU 中断状态都在这个窗口里，而在它
    之前这些寄存器在 Linux 侧**完全不可见**（驱动只用自己的 helper 碰它们）。
    偏移必须写成 **`0x90350`** 这样的十六进制（见 §4.2 末尾「本轮被证伪的一条」）。
- `zx279133.h`：新增 `ZX279133_PPU_CLUSTER_ME_SLOTS 64` / `_ME_BITS 8` /
  `ZX279133_PPS_BASE` / `_PPS_WINDOW_SIZE`。
- 边界检查按「最后一个字还在窗口内」写，不写成 `off + 4 * n > size`：`off` 由
  `kstrtou32()` 解析，最大 0xffffffff，那个加法会绕回小值从而放行一个 4 GB 的
  偏移，紧接着就是一次越界的 `readl()`。

**本轮实测的两条约束**（都写在代码旁边）：

1. **`/dev/mem` 读不到 PPS 窗口**。`/proc/iomem` 里明明有
   `18000000-18ffffff : 19000000.ethernet pps`，但 `dd if=/dev/mem bs=4 skip=...`
   报 `Bad address`（NPPT 窗口同样如此）。原因是 arm64 上这些设备资源被标了
   exclusive，`devmem_is_allowed()` 直接拒。所以「Linux 侧读 PPS」**只能**走驱动
   自己的 `pr` 动词 —— 这反过来就是 `pr` 必须存在的理由，不是「多一个方便
   的命令」。
2. **PPS 窗口的大小与 DT 一致**：DT 的 `pps@18000000` 是
   `reg = 0x18000000 0x1000000`（16 MiB），与 `ZX279133_PPS_WINDOW_SIZE` 对得上，
   `pr` 的上界按这个值卡。SE hash 窗口另有一份映射（`devm_ioremap_wc(...,
   0x1000000)`），所以 `cam` 的上界用 `ZX279133_SE_HASH_REQUIRED_SIZE`。

**本轮被证伪的一条（本文件下面两处示例原本是错的，已改）**：上面写的
`pr 90350 16` 里的 `90350` 会被 `kstrtou32(tok, 0, ...)` 按**十进制**读成 90350
（= 0x160ee），而不是常量 `ZX279133_PPU_CLUSTER_ME_TABLE` 写的那个 **0x90350**。
两者差了一个数量级，而十进制那一个**连字对齐都不是** —— 11-01 那次实机验收里
`pr 90350 4` 直接把板子读崩了（`FSC = 0x21: alignment fault`，调用链是
`readl → zx279133_9132_write`）。正确的写法是 `pr 0x90350 16`。
细节与同族动词的审计见 `docs/96`。

**没有做的**（证据不支持，见 §1.7）：把 TC flower 条目映射到 ME 槽。计划里那
条「按流表条目索引线性映射到 0..63」被本轮的证据否掉了 —— 厂商的 flow 号是
**PPU 自己的 64 个条目**（与 blob 里 64 条 PORT_FLOW 同序），与 TC flower /
ZCAM / IKEY 的编号空间无关。按那个映射去写，就是拿一个没有依据的猜测去改 NPU。

### 4.3 W4.2：CAM 的观测面（只读）

- 新增 debugfs 动词 `cam`（可带 `<offset> <count>`）：打印算法块的 hash 寄存器
  （CRC / bulk / depth0 / depth1 / SMMU1 base）与 SE hash 窗口的原始字。
- 原因：9132 的 MAC 表是 SDT 0x32（**type 3 = hash**，不是 ERAM 表），`er`/`ew`
  那套够不着它；而「交换机到底学到没有」这个问题，在没有读口之前无法回答
  （`docs/37` §7 已把它列为观测缺口）。
- 桶的落点由硬件 CRC 决定、桶内布局无文档，所以这个命令**不解码**，只给原始材料。

### 4.4 W5：`tc` 进构建

- `scripts/configure-sk-d840n.sh`：`CONFIG_PACKAGE_tc=y`（与 nftables 同款写进
  build-root `.config`），并在 defconfig 之后加**存活断言**（`tc` 被 defconfig
  静默丢掉的话，镜像里就没有 flower 客户端）。
- NPPT 的流表卸载接口是 TC flower（`TC_SETUP_BLOCK` / `TC_SETUP_FT` /
  `TC_SETUP_CLSFLOWER`），没有客户端，内核侧做得再对也没人能下发规则。

### 4.5 W6：文档订正

- `docs/94`：§3 的「交换机硬件转发 **开**」改为「**未开**（且实测 9132 根本不转发
  UNI↔UNI）」并附本轮读数；§5 第 4/5/8 条按 §3 的结论与 §1 的 NPU 事实改写。
- 本文（docs/95）。
- `docs/37` §7 的 CAM 观测缺口：**读口已补**（`cam` 动词），但桶内布局仍无文档。
- `docs/92`：CPU trap 口是 **PPU 15**（`zx279133.h:144`），`docs/92:13041` 写的
  「PPU9（CPU）」与代码不一致；2.5G 口的内核名按 DT 是 **`lan1`**（`port@8`），
  `docs/94`/`docs/92` 里的 `lan5` 是 CI run #292 之前的旧名。

### 4.6 实机取证记录：读口找到了，板子却被我自己重启停了

这一节记本轮在板子上的实际动作，包括一次失手。失手的原因是**操作方法**，不是
方案，结论写在最后。

**做了的（只读）**：

- `echo fg > /sys/kernel/debug/zx279133-9132`：PPU 1..5 的 15 个 flood group 全是
  `0x0000000f`，目的口 15 = CPU trap 口（§3.1）。
- `eram_read 0x7d 5 32` = `0x96040001`：属性字里 bit31 `sa_lookup_learn_en` 是
  **开**的 —— 「CPU 侧 FDB 不同步所以交换机学不到」这条假设被证伪（§3.2）。
- `cat /proc/iomem`：PPS 与 NPPT 两个窗口都在。
- `dd if=/dev/mem ...` 读 PPS 与 NPPT：两个都是 `Bad address`（§4.2）。

**失手的**：

- 为了在厂商 U-Boot 阶段读 ME 表（唯一能拿到「硬件复位之后」那个值的路径），
  走了 `echo b > /proc/sysrq-trigger`。内核照做了，打印 `sysrq: Resetting`，
  然后**板子再没有任何串口输出**：`serial.log` 里从那一刻起只有 relay 自己写的
  `sent:` 标记，`ping` 不到，`arp` 里也没有它。
- 试过的恢复手段全部无效：
  - bench 的 DTR/RTS 复位（`board_reset`，900 / 400 / 3200 ms 三次）：relay 确实
    执行了序列（`serial.log` 里有 `reset: the modem-control lines are being
    toggled`），板子毫无反应 —— 这条复位线没接到板子的复位脚；
  - `staging/ctl/ser-nostop` **存在**，所以 relay 不写空格、不会去打断 autoboot，
    板子本来应该一路引导进 Linux（这条链路是好的）；
  - relay 没有报任何读错误（`read error` / `the port was reopened` 的最后一条在
    20:56，早于本轮），所以不是「只写不读」；
  - 板子既不在 Linux（没有 shell 回显）也不在 U-Boot（没有 banner）。
- 另外，bench 的 `board_reset` 对这块板子**从来就没复位成功过**：在 Linux 下试的
  两次，uptime 是连续的（791.716246 s 没断），板子照常回命令。

**结论与教训**：这块板子**只能用物理断电/复位键**回来。sysrq 的 `b` 在这台板上
不是一条能用的重启路径（它让机器停在了 U-Boot 之前），bench 的 DTR/RTS 复位对
它是空操作。下次要重启板子，**先断电**，不要用 sysrq。要在 U-Boot 阶段取证，
顺序是：断电 → 上电 → 在倒计时里打断 autoboot → `md` 读 → `boot` 回来。

### 4.7 门禁会绿的一个假成功：`DYNAMIC_DEBUG` 还差 `DYNAMIC_DEBUG_CORE`

这一条是本轮提交之后、CI 在跑的时候查出来的，它会让 §4.1 的降级**看起来成功而实际失效**，
所以单独记一节。

**现象**：`CONFIG_DYNAMIC_DEBUG` 钉了，`CONFIG_DYNAMIC_DEBUG_CORE` 没钉，而后者是前者的
**前提**，不是可选项。

**证据**（上游 v6.18 源码行号，不是印象）：

- `lib/Kconfig.debug`：`config DYNAMIC_DEBUG` … `select DYNAMIC_DEBUG_CORE`。
  **是 `select`，不是 `depends on`** —— kconfig 里用户自己给的值优先于 select，
  所以 fragment 里显式的 `# CONFIG_DYNAMIC_DEBUG_CORE is not set` 会**压过**这个 select。
  而 `target/linux/generic/config-6.18:1876` 正好就带着这一行。
- `include/linux/dynamic_debug.h:137`：`#if defined(CONFIG_DYNAMIC_DEBUG) || …`
  —— `dev_dbg()` 的调用点变成可运行时开关，靠的是 `CONFIG_DYNAMIC_DEBUG`。
- `lib/Makefile`：`obj-$(CONFIG_DYNAMIC_DEBUG_CORE) += dynamic_debug.o`
  —— `lib/dynamic_debug.c` 整个文件只有 CORE 开着时才编。
- `lib/dynamic_debug.c:1384/1494`：`dynamic_debug_init_control()` 是 `fs_initcall()`，
  它创建 `/sys/kernel/debug/dynamic_debug/control`。

**后果**：只钉 `DYNAMIC_DEBUG` 时，CORE 留在 n ⇒ 那条被降级的 lane 转储编译成
`no_printk`（**彻底消失**），而 `/sys/kernel/debug/dynamic_debug/control` 压根没有生成
（**没有地方写 `+p`**）。门禁当时只查 `CONFIG_DYNAMIC_DEBUG=y`，会**绿**——绿在一个假的
成功上，正是这个符号本来要防的那件事。

**修**：两边都钉（fragment + build-root，理由与 `KERNEL_DEVMEM` 相同：合并顺序在 fragment
之后），并把 `DYNAMIC_DEBUG_CORE` 也放进 CI 门禁的 **required** 列表（卡住式）。
门禁现在查两个符号，缺一个就红。

### 4.8 CI 一开跑就撞上的另外两处

这一节记的是「代码写完、推上去、CI 开始跑」之后才暴露的两处。两处都不是本轮 NPU
计划的内容，但都挡着 CI，而 CI 是本轮唯一的验证通道。

**一、`CONFIG_PACKAGE_tc` 在这个树里不存在（run 294 的 Configure 红了）**

- `scripts/configure-sk-d840n.sh` 写的是 `CONFIG_PACKAGE_tc=y`，断言也是查它。
- 但 `cnjn/ImmortalWrt`（`sr1010-zx279133`）的 `package/network/utils/iproute2/Makefile`
  里**没有 `Package/tc`**：只有 `Package/tc-tiny`、`Package/tc-bpf`、`Package/tc-full`，
  三者都带 `PROVIDES:=tc`。
- 于是 `tc` 是一个**虚拟包** —— 三个包提供的那个名字 —— 而虚拟包不能被单独选中：
  `CONFIG_PACKAGE_tc=y` 被 defconfig **静默丢掉**（正是「给一个不存在的符号写一行
  配置」的那一种静默），存活断言随之失败，Configure 在 step 14 exit 1。
- **修（第一版，不够）**：改成 `CONFIG_PACKAGE_tc-full=y`，断言跟着改。**结果 run 296
  又红在同一处** —— 见下面第二条。

**二、这个分支没有 `kmod-*` 包，于是 tc 的三个变体一个都选不上（run 296 的 Configure
又红了）**

- `cnjn/ImmortalWrt` 的 `sr1010-zx279133` 分支**没有 `package/kernel/modules/` 目录**
  （`package/kernel/linux/` 在，`kmod-*` 的包不在）。
- 而 iproute2 的三个 tc 变体都写着 `DEPENDS:=+kmod-sched-core ...`。指向一个**不存在
  的包**的强制依赖，包就选不上 —— 三个变体无一例外。
- **修**：照 `overlays/package/network/utils/nftables/Makefile` 的既有做法（那份也是
  feed 的配方去掉一个指向不存在的 kmod 的依赖），新增
  `overlays/package/network/utils/iproute2/Makefile`：feed 的 iproute2 配方逐字照抄
  （6.18.0、源、哈希、configure 参数、变体开关、安装布局），只去掉 `+kmod-sched-core`。
  - 去掉它丢的是 `tc qdisc add ... netem` 用的 `net/sched/sch_*.ko`；flower 过滤器
    不需要它（`tc filter add ... ingress flower` 是 netlink 上对 cls_flower 的操作，
    不是队列模块）。
  - 变体开关一个字没动：它只切 `TC_CONFIG_XT` / `TC_CONFIG_XT_OLD` /
    `TC_CONFIG_XT_OLD_H` 与 `TC_CONFIG_IPSET`，**flower 分类器在每个变体里都有**。
- 最终选的符号是 **`tc-tiny`**：三个变体都有 flower，而 tiny 不把 libxtables 与
  libbpf 拉进镜像（本板用不到那些 action）。
- **一条查证过的事实**（免得再走一遍）：`libxtables` **是存在的**，在
  `package/network/utils/iptables/Makefile` 的 `Package/libxtables` 里，不在
  `package/network/config/` 下；`libbpf` 在 `package/libs/libbpf`。先前「config 下没有
  iptables ⇒ 没有 libxtables」的推断是错的。

**二、ZX-PON netlink 的同名碰撞（run 293 的 Compile 就红了，来自上一轮提交）**

- `zx279133-pon.c:127` 的 netlink handler 叫 `zxpon_set_identity`，而 `zxpon.c:325`
  已经有一个 `zxpon_set_identity(zxpon *, sn, regid)`。同名 ⇒ 编译器把它们当成一个
  函数：调用处报 `too many arguments`，定义处报 `conflicting types`。
- 同时 `zxpon_family` 的定义在 222 行，110 行的回复路径却要用它 —— 定义在使用之后，
  报 `'zxpon_family' undeclared`。
- **修**：handler 改名 `zxpon_nl_set_identity`（`nl_` 前缀说明是 netlink 那一侧），
  ops 表里的 `.doit` 跟着改；`zxpon_family` 加一个前置的 tentative definition，真
  正那个定义仍留在 ops 表旁边 —— 两者必须分开：ops 表要先存在，family 才能引用
  它，而 handler 又要先有 family 才能回复。
- **怎么验的**：本机没有 gcc / WSL / docker，只有 MSVC。于是搭了一套桩头
  （`linux/*.h`、`net/*.h` 按内核真实语义写，比如 `NLA_POLICY_EXACT_LEN` 展开成自带
  大括号的 `NLA_POLICY_RANGE`），把三个文件各编一遍：库 `zxpon.c`、修复后的
  `zx279133-pon.c`、以及 `git show 3bfadd1:...zx279133-pon.c`（run 293 真正编的那一版）。
  - 修复后：**通过**；
  - 3bfadd1 那版：**失败，而且精确复现了 CI 的两条错** ——
    `old_pon.c(110): error C2065: "zxpon_family": 未声明的标识符`、
    `old_pon.c(127): error C2371: "zxpon_set_identity": 重定义；不同的基类型`。
  - 对照组是这套桩的可信度证明：只有「修复版能过」而对照组不过，说明不了任何事。
- `zxpon_family` 的前置 tentative definition 这种写法有上游先例：
  `staging/ktree-git/drivers/net/macsec.c:1521` 就是 `static struct genl_family
  macsec_fam;` 这么写的，而内核没有开 `-Wredundant-decls`。

**三、`zxpond` 这个包一次都没有被编译过**

- `zxpond` 是 `3bfadd1` 带进来的用户态守护进程，而 `3bfadd1` 就是 run 293 那个提交，
  它在 **Compile** 就红了 ⇒ **这个包从来没有被真正编译过**。最后一个绿的 run 292 的树
  里没有它。
- 它的 `Build/Compile` 是本仓库**唯一一个不带 `$(TARGET_CPPFLAGS)`** 的包：

  ```make
  $(TARGET_CC) $(TARGET_CFLAGS) -I$(PKG_BUILD_DIR) -o ... zxpond.c zxpon.c
  ```

- **先更正一条走错路的推断**：上一条提交信息里写的是「少了 `$(TARGET_CPPFLAGS)`，编译器
  会停在 `linux/genetlink.h: No such file or directory`」。**那是错的。** run 297 的构建
  （那份树里还没有这个改动）在 02:33 的快照里已经把 `package/utils/zxpond compile`
  跑完了 —— 那个目标已经不在进程表里，日志里也没有任何 error 行 —— 也就是说
  **toolchain 的 include 目录本来就在编译器的搜索路径里**，少一份 flag 也编得过。
- **那为什么还加上它**：OpenWrt 对**每个**包都给这份 flag（`include/package-defaults.mk:130`
  放进 `CFLAGS`，走 configure 的那条路在 `:105` 放进 `CPPFLAGS`），本仓库的 `mdread` /
  `rdreg` / `wrreg` 三个包也都写着它。`zxpond` 是唯一的例外，而「唯一的例外」正是那种
  没人记得为什么、却会让下一个人花掉一小时去找的差异。
- **`-Wall -Werror` 先不加**（另外三个包都加了）：这个文件从没被带着告警选项编译过，而
  这里唯一能编译它的编译器是 CI —— 一次只因为一条告警而失败的构建，而那条告警只写在
  没有 token 就读不到的 job 日志里，说明不了代码有没有问题。
  能从源码上看出来的那一条已经改掉了：`zxpond.c` 里 `a->nla_len >= sizeof(*a)` 是
  `__u16`（提升成 `int`）与 `size_t` 的比较，`-Wall` 会报 `-Wsign-compare`，
  现在写成了 `(size_t)a->nla_len >= sizeof(*a)`。
  （顺带更正另一条：`-Wcast-align` **不在** `-Wall` 里 —— GCC 16.2 手册的 `-Wall`
  列表里没有它，它属于「不被 `-Wall` 隐含」的那一批，要 `-Wextra` 或单独打开。
  上一条提交信息把它说成是 `-Wall` 打开的，是错的。）

**四、这两类编译错误，各加一道本地门禁**

CI 每暴露一类编译期错误，就在本地加一道门禁，免得再花一小时才发现。这两类现在各有一道：

- `tools/check-c-name-collision.py`（新）：一个 `.c` 里用 `static` 定义的函数名，如果
  同一个 `.c` 用 `#include "..."` 引进来的头文件已经声明了同名函数 ⇒ 报出来。C 只有一个
  命名空间，编译器把两者当成同一个函数：定义处 `conflicting types`，调用处
  `too many arguments`。
- `tools/check-c-use-before-decl.py`（加宽）：原来只查 static 函数「先用后声明」，现在
  也查 file-scope 的 static 对象。问的是同一个问题：编译器在这一行知不知道这个名字。
  判据取**文件里第一次提到这个名字的地方** —— 第一次提到如果是声明（`int name`、
  `int *name`、参数表里的名字），编译器知道它，什么都不说，而那正是一个同名的局部
  变量或参数；第一次提到如果是使用（`&name`、`name.field`、`name = ...`），编译器还
  不知道它。
- 两个门禁都有自测：`tools/test-check-c-gates.py`（夹具在临时目录里，用完就删）。
  自测里各有一个「就是 run 293 那个形状」的夹具，和几个「必须安静」的夹具 —— 同名的
  局部变量、先声明后使用的对象、别人结构体里的成员。
- **怎么验的**：两个门禁都拿 **3bfadd1（run 293 真正编的那棵树）** 和当前 HEAD 对照跑：
  3bfadd1 报出 `zxpon_set_identity`（`zx279133-pon.c:127`）和 `zxpon_family`
  （`zx279133-pon.c:110`）—— 正是 CI 的那两条错；当前 HEAD 报 0 条。
- **门禁在 CI 里也跑过了**：CI 的 step 7 跑的就是 `tools/prepush.py`，所以这两道门禁
  在下一个 run 里就会在构建开始前先过一遍，而不是等一小时后才知道。

**五、run 293 的那两条编译错已经不存在了**

- 修法在 `b90081c`（handler 改名 + `zxpon_family` 前置 tentative definition）。
  本地没有 gcc，能做的只有上面那条 MSVC + 桩头的对照编译；真正的证据来自 CI：
  - **run 296 / 297 / 298 / 300 的 Compile 全过**，四个 run 全绿；
  - **run 298 是自 run 292 之后第一个全绿的 run**：两个 job、每一个 step 都 success，
    从 Compile 一路走到「Package flash images」「Publish release」。
  - run 300 带上了本轮新加的两道门禁（CI 的 step 7 跑的就是 `tools/prepush.py`），
    两个 job 也都全绿 —— 也就是说，那两道门禁在 CI 里跑过了，而且没有误报。
- 产物在 release `skd840n_flash_298_41225913440` 与 `skd840n_initramfs_298_41225913440`：
  `flash-uImage`（8.3 MB）、`flash-dtb.bin`、`flash-rootfs.jffs2`（4.8 MB）、
  `flash-sysupgrade.bin`（13.5 MB）、`sk-d840n-initramfs.itb`（12.6 MB）、`trial-set.tgz`。
- 顺带核了发布出来的 flash rootfs 里有什么：`zxpond` 在里面，`mdread` / `rdreg` /
  `wrreg` 都在，而 `zrec` **不在** —— 这正是 workflow 定的规矩（flash 版不得带刷写器，
  只有 initramfs 版带）。
- ⇒ 顺带解决了一个悬着的问题：`zxpon.c`（800 行）和 `zx279133-pon.c`（357 行）都是
  `3bfadd1` 带进来的，**同样一次都没有被编译过**（run 293 死在编内核之前）。现在两个都
  编过了，run 293 报的那两条错也不复存在。

---

## 5. 没做的，和为什么

| 项 | 为什么不在这轮做 |
|---|---|
| **W3：申请 PPS 中断（SPI 61/62）** | 厂商 `np_133.ko` 自己也**不申请**（`g_pps_irq` 全模块零引用），没有任何先例，也就没有已知语义。语义搞错 = 中断风暴 = 打挂唯一的管理通路 eth0。本轮只把读取面铺好（PPS 窗口动词），等拿到中断语义再接线 |
| **TC flower ↔ ME 槽的绑定** | 见 §4.2：两个编号空间无关，没有依据 |
| **UNI↔UNI 单播的干净实测** | 台面上只有一根网线（§3.2 末） |
| **把 W1 升级成「变化驱动 + 汇总行」** | 机主选的是「降级 dev_dbg + DYNAMIC_DEBUG」。升级方案备料见计划 §6.9：驱动状态加 15 个值的快照，正常 boot 可从 34 行降到 1–3 行且信息量不减 |

## 6. 下一轮要什么

1. **CI build + kexec 试跑**，验收清单：
   - **CI build 这一半已经做完**：run 298 全绿（见 §4.8 第五条），产物在 release
     `skd840n_flash_298_41225913440` / `skd840n_initramfs_298_41225913440`：
     `flash-uImage` / `flash-dtb.bin` / `flash-rootfs.jffs2` / `flash-sysupgrade.bin` /
     `flash-set.tgz` / `sk-d840n-initramfs.itb` / `trial-set.tgz`。剩下的是把它写进
     板子：先用 kexec 试 RAM 镜像（不写 flash，断电即回原系统），确认 SSH 通了再谈刷写；
   - 剩下的（要在板上做）：
   - 合并后的 kernel `.config` 含 `CONFIG_DYNAMIC_DEBUG=y` **与** `CONFIG_DYNAMIC_DEBUG_CORE=y`（门禁两个都卡，见 §4.7）；
   - `dmesg | grep -c 'switch: lane'` = 0；`dynamic_debug/control` 的 `+p` 后原样复现；
   - 板上 `/sys/kernel/debug/dynamic_debug/control` 存在；
   - `echo me > /sys/kernel/debug/zx279133-9132` 打出 16 行 ME 表与 64 个 flow 的引擎号；
   - `echo pr 0x90350 16 > /sys/kernel/debug/zx279133-9132` 读回 ME 表（偏移是十六进制）；
   - `echo cam > /sys/kernel/debug/zx279133-9132` 打出算法块寄存器；
   - `tc filter add dev eth0 ingress flower ...` 可执行（客户端是 `tc-full`，见 §4.8），`zx279133-flows` 非空；
   - 板子 br-lan / eth0 正常，ping 通。
2. **ME 表的写实验**（机主已授权读写 ME 表与 PPS 中断相关寄存器）：写前先 `me`
   读出原值，写后 `me` 回读；只在 kexec 试跑轮里做，失败即断电复位。
   —— 但**板子当前要先物理断电复位才能用**（§4.6），这一步排在最前面。
3. **PPS 中断语义**：用 `pr` 动词扫 PPS 0x90xxx 段，找中断状态/使能寄存器；厂商
   符号 `ppu_debug_get_me_int_stat`、`ppu_me_fifo_int_status` 是线索。
4. **UNI↔UNI 实测**：需要两只口插线 + 第二台主机。

## 7. 复现

```sh
# 板上（只读）
echo fg  > /sys/kernel/debug/zx279133-9132   # flood group 表
echo me  > /sys/kernel/debug/zx279133-9132   # ME 表（本轮新增）
echo pr 0x90350 16 > /sys/kernel/debug/zx279133-9132   # PPS 窗口（本轮新增；偏移要写十六进制）
echo cam > /sys/kernel/debug/zx279133-9132   # CAM/算法块（本轮新增）
grep -i -e 19000000 -e 18000000 /proc/interrupts
```

ERAM 表可以直接按 (table id, index, width) 读：`0x1f` 是 flood group（32 bit/项）、
`0x18` 是协议动作（2 bit/项，index = proto \| port<<7）、`0x1b` 是隔离矩阵（1 bit/项）、
`0x25` 是 PVID、`0x7d` 是 MF 属性字副本。

U-Boot 阶段（**先断电再上电**，在倒计时里打断 autoboot；`md` 的地址是字节地址）：

```sh
md 0x1890350 16     # PPS + 0x90350：ME 表，16 个字（flow = word*4 + 槽）
md 0x189045c 1      # PPS + 0x9045c：pkt-type detail
boot                # 回来
```
