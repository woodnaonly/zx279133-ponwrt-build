# 引擎分派链与 PPS 中断语义定案（2026-11-01）

## 0. 一句话

**引擎分派是三段，不是两段**：**端口 → flow → ME 核**。这一轮把中间那一段挖出来了 ——
每个 SPA 端口的属性字 0 里有一个 **6 bit 的 flow 号**，它就是 PPU 那 64 个 flow 之一；
flow 再由 ME 表指到 8 个 ME 核之一。于是：

- **端口 → flow 这段运行期可写**（SPA 端口字 0 就是寄存器），这轮用新动词 `pf` 暴露出来；
- **flow → ME 这段运行期不可写**（ME 表是 cluster 的只读镜像，docs/97 已定案），
  真要改只能动 PPU 微码 blob 的指令流。

顺带把 **PPS 中断语义定案**：三个字（`PPS+0x90000` / `0x90004` / `0x90008`）各是什么、
哪一位属于哪个 ME 核，全部用「厂商描述符行 + 反汇编调用点 + 板上读数」三方对齐后落定，
新动词 `mi` 只读地把它们和 cluster 的 PC 一起打出来。

同时订正了两处被这轮证伪的旧结论（§2、§3），
并把一个更要紧的结论**降级为「没有定论」**（§1.3）：
「flow 号 → ME 核」这个映射，在 CPU 侧没有一手依据 ——
**主线自己的模型建立在厂商一个坏掉的函数上**（§7.1）。

---

## 1. 分派链：三段，不是一段

docs/95 §1.7 已经把 ME 表的 64 个槽和 blob 里的 64 条 `PORT_FLOW` 对上了，
但那时把 **flow 号** 当成了「PPU 自己的条目编号」这一层事实，没有说清
**端口是怎么指向某个 flow 的**。这一轮补上了这一段。

### 1.1 端口属性字 0 的位布局

每个 SPA 端口（= PPU 的 64 个 memory id，0..63）有一组属性字，字 0 就是它的入口：

| 位 | 含义 | 宽度 |
|---|---|---|
| bit 0 | Tbl Vld（表有效） | 1 |
| bits 1..14 | **start PC**（进 PPU 后跑哪段入口程序） | 14 |
| bits 15..20 | **flow 号**（PPU 的 64 个 flow 之一） | 6 |
| bits 21..23 | SPA 属性 4 | 3 |

三条独立证据把这条布局钉死：

**① 厂商自己的代码与注释**（`zx279133-np.c` 里照抄厂商行为的两个函数）

```c
/* zx279133_lan_port_bringup() */
/* Table valid, start PC 10, flow number 1, as on the factory system. */
reg = (reg & ~GENMASK(23, 0)) | 0x8015;

/* zx279133_wan_port_bringup() */
/* Tbl Vld + start Pc 561 (SPA address 0 = 0x463, observed on the factory system) */
reg = (reg & ~GENMASK(23, 0)) | 0x463;
```

按上表拆：

| 字 | bit 0 | bits 1..14 | bits 15..20 | bits 21..23 |
|---|---|---|---|---|
| `0x8015` | 1 | **10** | **1** | 0 |
| `0x0463` | 1 | **561** | 0 | 0 |

和注释里写的「start PC 10, flow number 1」「Tbl Vld + start Pc 561」**逐字对得上**。
注意这两处都是 `GENMASK(23, 0)`，也就是 **bits 0..23 是一个整体**，
bits 24 以上另有字段 —— 这与 blob 的解码公式（见下面 ②）也一致。

**② 厂商 blob 的 `PORT_FLOW` 解码公式**

厂商加载器把每条 tag `0xbbbbbbbb` 的条目做成一次读改写：

```
port  = value >> 26           （高 6 位是端口号）
valid = value & (1 << 25)
word  = 1                       （bit 0 = Tbl Vld）
      | (value & 0x3fff) << 1   （blob bits 0..13  → word bits 1..14 = start PC）
      | ((value >> 16) & 0x3f) << 15  （blob bits 16..21 → word bits 15..20 = flow）
      | ((value >> 22) & 0x7) << 21   （blob bits 22..24 → word bits 21..23 = 属性 4）
```

blob 的三个字段和 word 的三个字段一一对应，**位置完全吻合**。

**③ 厂商 blob 里那 64 条 `PORT_FLOW` 与主线的 64 项表同序**

docs/95 §1.7 已经对上了「同序」；这一轮把「同序」升级成
「**逐位相同**」—— `staging/ctl/portflow-cmp.py` 把 blob 的 64 条按上面这个公式
解码，和主线硬编码的 `zx279133_9132_port_flow[64]` 逐项对比，
**48 个端口一致，0 个不同**（blob 没命名的 16 个端口，主线写 0 并跳过）。

**⇒ 结论：端口字 0 的 bits 15..20 是一个 6 bit 的编号（0..63），
它把端口接到 PPU 的 64 个条目之一上。**
这个 6 bit 字段就叫「flow 号」—— 主线一直这么叫，厂商的注释
（"flow number 1"）也这么叫。

**至于那 64 个 flow 各自归哪个核，CPU 侧没有一手依据** ——
厂商唯一给出这个映射的函数是坏的，详见 §1.3 与 §7.1。

**④ 这个字在运行期是可写的（实测）**

这是「引擎分派可编程」这件事里**唯一一段已经被证明可改**的地方。
在空闲口 55（word 0 = `0x00000000`，vld 恒为 0，不会有任何流量经过它）上：

```
[  678.375598] 9132 spa: mem 55 addr 0 = 0x00000000      <- 写之前
[  764.100080] 9132 spa: mem 55 addr 0 = 0x00000002      <- 写 0x00000002 之后的回读
[  765.102818] 9132 spa: mem 55 addr 0 = 0x00000002      <- 另一条命令单独再读一次
[  775.395036] 9132 spa: mem 55 addr 0 = 0x00000000      <- 写回 0x00000000 之后
[  776.397658] 9132 spa: mem 55 addr 0 = 0x00000000      <- 再读一次
```

⇒ **写进去当场就读得出来**。此前只知道「驱动在 probe 时写过它」，
这一条才是「运行期可改」的直接证据（`pf` 动词的写路径走的就是同一条
SPA indirect 通道，见 §5.2）。

被改的正是 flow 号与 start PC 所在的那个字 ⇒ **「端口 → flow」这一段是真的可编程的**；
「flow → 核」那一段仍然写不进去、也没有稳定可读表示（§1.2 / §1.3）。

### 1.2 板上的实测：`0x90350` 这一片是 **cluster 指令寄存器的投影**，不是表

主板子 `me` 动词读的那 16 个字（`PPS+0x90350` 起 16 个字）**不稳定**。
这一轮在同一次开机里就看到它换过好几种值：

| 时刻（uptime） | 16 个字 | 谁写的 |
|---|---|---|
| 0.45 s（probe 时打印的「factory ME word」） | `0x00000000` | 芯片复位值 |
| 260 s / 409 s / 737 s | `0x00000001` | **不是驱动写的**（见下） |
| 764 s | `0x00000303` | 没有人写它 |
| 807 s / 822 s | `0x00000000` | 没有人写它 |
| 823 s 起 | `0x00000303` | 没有人写它 |
| 1088 s | `0x00000180` | 没有人写它 |
| 1111 s 起 | `0x00180000` | 没有人写它 |

**驱动写的那个 0x1 看不出来**，因为那一带本来就是 1：
`pr 0x902c0 96` 读出的 **0x902c0..0x9041c 整整 88 个字全是 `0x00000001`**，
而驱动的 probe 只写了 0x90350..0x9038c 这 16 个。
再往后（0x90420 起）就变成各不相同的值了（0x3c、0x3c、0x06、0x04、0x06、0x02、0x06、0x00…），
`0x9043c` 正是驱动自己写过的 `ZX279133_PPU_IKEY_AFULL`（掩码 5 bit，读回 0 ✓）。

**而且这一片地址读的是同一个会变的值**：1 Hz 连采 25 轮，四个相距很远的地址

```
0x90300 / 0x90350 / 0x90370 / 0x9002c
```

**在每一轮里都读到同一个值**，而且一起从 `0x00000000` 变成 `0x00000303`：

```
[  822.835062] pps reg 0x090350 = 0x00000000
[  822.835166] pps reg 0x090370 = 0x00000000
[  822.835213] pps reg 0x09002c = 0x00000303
[  822.835259] pps reg 0x090300 = 0x00000303
[  823.837979] pps reg 0x090350 = 0x00000303
[  823.838074] pps reg 0x090370 = 0x00000303
[  823.838122] pps reg 0x09002c = 0x00000303
[  823.838168] pps reg 0x090300 = 0x00000303
```

⇒ **这块窗口在读一个由硬件驱动的活值，不是一张每个槽位各有内容的静态表。**

**那这个活值是什么？—— 是 cluster 的指令寄存器。**

这是这一轮最重的一条。`PPS+0x90d0c` / `PPS+0x90d10` 是 cluster 的
INST_LO / INST_HI（仓库头文件 `ZX279133_PPU_CLUSTER_INST_LO/HI`），
而 `0x90350` 这一片读出来的，**恰好就是那两个 32 位半字**：

```
[ 1110.536615] pps reg 0x090350 = 0x00000180
[ 1110.536716] pps reg 0x090d0c = 0x00000180      <- INST_LO
[ 1110.536727] pps reg 0x090d10 = 0x00180000      <- INST_HI
[ 1111.539455] pps reg 0x090350 = 0x00180000      <- 0x90350 换成了另一半
[ 1111.539544] pps reg 0x090d0c = 0x00000180
[ 1111.539555] pps reg 0x090d10 = 0x00180000
[ 1112.542109] pps reg 0x090350 = 0x00180000
[ 1113.544787] pps reg 0x090350 = 0x00180000
...（到 1117 s 一直是 0x00180000；0x90350 与 0x90354 同值）
```

⇒ **`PPS+0x90350` 既不是 16 个寄存器，也不是 1 个寄存器，
而是一个 64 位量（INST）被投到窗口里的 32 位视图。**
它跟着 cluster 的指令指针走，随微码执行而变；CPU 写不进去。

**这里没有「表」，因为从来就没有过表。**
厂商描述符表里指向 `0x90350` 的**唯一一行**（id 67）本身就是
`type = 1`（只读）、`maxoff = 8`、`mask = 0xffffffff`（见 §7.1）——
厂商自己也只是把它当一个**只读调试视图**用
（`ppu_debug_get_me_state()`，按 ME 序号读）。
厂商想拿它写 ME 值的那个函数（id 66）在出厂二进制里是一行**全零空洞**。

**⇒ 结论：**
**主线「`0x90350` 是 64 个 flow 的 ME 分派表」这个模型，在硬件行为上没有支撑。**
它不是「只读镜像」那么温和 —— **它连「一张表」都不是，
它是 cluster 指令寄存器在窗口里的别名。**

再看两条旁证：

- blob 里**没有 `0x303` 的字面量**（大小端都搜过，0 命中）⇒ 那个值不是微码里的常数；
  （`0x180` 在 blob 里出现过，但它同样是随时间变的值，所以也不能当成常数）
- `mes 7 3`（把 flow 7 指到 ME 3）写完**立刻回读，值不变** ⇒ CPU 写不进去。

⇒ 所以 **「flow → 核」这一段，在 CPU 侧既不能写，也没有可读的稳定表示。**
能观测、能改的是**端口 → flow** 那一段（§1.1），这正是 `pf` 动词做的事。

### 1.3 「flow → 核」这一段：没有一手定论

这一段是这一轮最麻烦的地方，必须说清楚。

`PPS+0x90350` 这块数组，**厂商代码对它有两种互不相容的读法**：

| 厂商函数 | 怎么用 id 67 | 它认为这块数组是 |
|---|---|---|
| `ppu_debug_get_me_state(me, out)`（0x85124） | `cmp w0, #7`（me ≤ 7）后 `np_onu_reg_read(67, out, me)` | **每个 ME 核一个状态字**，共 8 个（加第 9 个 word） |
| `ppu_cluster_set_me_val_by_flow_num(flow, me_val)`（0x829dc） | `np_onu_reg_read(66, out, flow>>2)` + 清 `(flow&3)*8` 起的 8 bit | **flow → ME 映射**，16 个字 × 4 flow × 8 bit |

描述符表那边对**读者**是有利的：第 67 行 `maxoff = 8`，
正好是「8 个核 + 1 个 word」，而不是 16。

**而那个写者 —— 也就是唯一给出「flow → 核」映射的那个函数 —— 用的 id 是
`0x42` = 66，而描述符表的第 66 行是**全零的一行**（§7.1 有完整证据）。**
**⇒ 厂商自己的「把 flow 指到某个核」这个操作，在厂商自己的系统里也写不进去。**

**这一轮把「为什么」查到底了**，结论比「表坏了」更硬：

- **访问层根本没有终止符逻辑**。`np_onu_reg_read/write` 是
  `row = id * 28`，然后只比对该行的 id 字段 —— 从不线性扫表。
  第 66 行必须物理存在，否则第 67 行会前移，`id*28` 寻址就错位了。
  ⇒ 第 66 行是**空洞（hole）**，不是终止行。
- **出厂的 `.ko` 里，指向 `0x90350` 的只有 id 67 一行**
  （`type = 1` 只读、`base = 0x240d4`、`maxoff = 8`、`mask = 0xffffffff`）。
  「同一地址发过两个 id」是**重建**出来的假象，不是二进制里的事实。
- **「同址多 id」在厂商别的表里是正常手法**，不是 bug：
  `ppu_glb_reg_table` 有 14 个 id 落在同一个字上，
  `nppt_glb_reg_table` 有 21 个。所以这件事本身不构成错误。

⇒ **主线自己的「64 flow × 8 bit」模型，建立在一个厂商自己都用不了的函数上。**
这不影响 §1.1 的位布局（那是从厂商**注释**和 blob 的解码公式来的，
两个独立来源，都不依赖那个坏函数），
但**「flow 号 → ME 核」这个映射本身，在 CPU 侧没有一手依据**。

**而 §1.2 给了它一个更彻底的解释**：那个地址上根本不是表，
是 cluster 的指令寄存器 —— **一个只读状态寄存器**。
厂商要往那里写 ME 值，即便当初那行描述符没坏，也**写不进一个状态寄存器**。
所以这不是「厂商少写了一行」，而是**这条 CPU 侧通路从来就不存在**。

本轮的两条处理：

1. `pf` 动词**不印**「哪个核」那一列 —— 印一个没人确认过的模型没有意义；
2. `me` 动词照旧印它的解码（那是主线自己的模型，是既有行为，本轮不动），
   但文档里说清那是**主线的模型**，不是厂商证明过的。

**怎么才能定案**：把 id → 描述符行 → 地址这条链彻底走一遍，
弄清楚 `base = 0x240d4` 那一行到底是什么、
那张表里 id 66 原本该是什么（按第 67 行的样子推测是 `0x240d4`，但那是推测），
以及厂商为什么给同一个地址发两个 id。
这是下一轮的活。

---

---

## 2. 订正一：树里的微码 blob 是**本板自己的**，不是别家板的

`zx279133-np.c` 里那段注释（写在本轮之前）说：

> The blob in the tree … **is the SR1010's**. Its table and this board's differ on 55 of the 64 ports

**这句话是错的。** 三条证据：

1. **md5 相同**：树里 `overlays/target/linux/zte/zx279133/firmware/zte/zx279133/mcode_intel.bin`
   与从**本板 flash 里读出来的**厂商 `etc/mcode/mcode_133.bin`
   md5 都是 `8b2b3726862b4b063d765bc4ab3f8bd3`，**逐字节相同**。
2. **解码后逐位相同**：`staging/ctl/portflow-cmp.py` 把 blob 的 64 条 `PORT_FLOW`
   按 §1.2 的公式解码，和驱动里硬编码的 `zx279133_9132_port_flow[64]` 逐项对比 ——
   **48 个端口一致，0 个不同**（blob 没有命名的另外 16 个端口，驱动写 0 并跳过）。
   ⇒ 驱动那张表**就是 blob 自己的表**的副本。
3. **旁证**：`0x00008015` 不是别家板的入口程序 —— 那是**本板 factory** 对 memory 5 的值
   （start PC 10, flow 1），驱动自己的注释 `zx279133_wan_port_bringup()` 里就是这么写的。

⇒ np.c 里那段注释已改写成「blob 是本板自己的，这张表是它的副本」；
同一文件里另一处把 `0x00008015` 说成「SR1010's entry program」的话也一并订正。

**这条订正为什么重要**：原来的结论是「blob 是别家板的，所以必须用驱动自己的表替换掉它」。
事实是 **blob 里的表和驱动自己的表一模一样**，替换与不替换结果相同 ——
驱动跳过 blob 的 tag（`case ZX279133_MCODE_TAG_PORT_FLOW: break`）不是「不得已」，
只是「不想依赖 blob 的 tag 解析器」。这是个冗余，不是缺陷。

---

## 3. 订正二：端口字的**实际值**与驱动那张表并不一致（7 处），全部有出处

用已有的通用 `r <mem> <addr>` 动词把 64 个端口的字 0 全读了一遍（板上实测），
和驱动硬编码表对比，**7 个端口对不上**：

| 端口 | 硬件实测 | 驱动表 | 谁写的 |
|---|---|---|---|
| 0 | `0x00000463` | `0x0000a6a5` | `zx279133_wan_port_bringup()`：start PC 561 |
| 6 | `0x00000463` | `0x0000a6a5` | `zx279133_mf_port_startpc_copy(eth, 6, 0)`：从端口 0 抄 |
| 10 | `0x00c0800b` | `0x0000a6a5` | `zx279133_lan_port_bringup()`：直接写死 |
| 49 | `0x00e52825` | `0x00052825` | `zx279133_spa_port_attr_init()`：属性 4 = 7 |
| 50 | `0x00e52827` | `0x00052827` | 同上 |
| 51..54 | `0x00e00000` | `0x00000000` | 同上（驱动表是 0，apply 时被跳过） |

**7 处差异全部是驱动自己在表应用完之后又写了一遍**，没有一处是缺陷：

- `zx279133_9132_port_flow_apply()` 在 probe 时写表；
- 之后 `zx279133_wan_port_bringup()` 把端口 0 的 word 0 换成 start PC 561，
  再用 `zx279133_mf_port_startpc_copy(eth, 6, 0)` 抄给 WAN 端口 6；
- `zx279133_lan_port_bringup()` 把端口 10 的 word 0 写成 `0x00c0800b`；
- `zx279133_spa_port_attr_init()` 给 PON 侧端口 49..54 的 word 0 写「属性 4 = 7」。

端口 5 之所以还是表里的 `0x0000a6a5`，是因为 `zx279133_lan_port_bringup()`
（会把端口 5 的 word 0 改成 `0x8015`）**在这块板上没跑过** —— 没有 WAN 链路。

⇒ **只看驱动那张表会以为端口现在跑的是哪段程序，那是错的**。
`pf` 动词是唯一能一次看全 64 个端口真实值的读法。

---

## 4. PPS 中断语义：定案

docs/95 §1.9 记的是「PPS 的两条中断没有申请」，但没说**那些中断到底在讲什么**。
这一轮用「厂商描述符行 + 反汇编调用点 + 板上读数」三方对齐，定案如下。

### 4.1 三个字是哪三个

厂商 `np_133.ko` 的 `ppu_cluster_reg_table`（`.data` 0xc820，68 行 × 28 B）
里，`base` 是**字下标**，字节偏移 = `base * 4`。与中断有关的行：

| id | PPS 字节偏移 | 掩码 | 厂商函数 | 含义 |
|---|---|---|---|---|
| 12 | `0x90000` | `0xff` | `ppu_debug_get_me_int_flag()` | **每个 ME 核一个中断标志**（位 N = ME N） |
| 13 | `0x90004` | `0xff` | `ppu_debug_get_me_int_stat()` | ME 中断状态字 |
| 14 | `0x90008` | `0xff` | `ppu_debug_get_me_int_mask()` / `ppu_debug_set_me_int_mask()` | **ME 中断使能**（位掩码） |
| 26 | `0x90014` | `0x1` | `ppu_cluster_set_thread_swicth_en()` | thread switch 使能 |
| 48 | `0x90458` | `0x1` | `ppu_cluster_set_mf_protect_detail()` | MF protect detail |

反汇编把这几行**直接钉在调用点上**：

```asm
; ppu_debug_get_me_int_stat (0x80a88)
mov   w0, #0xd          ; id 13
bl    #0x7ff48          ; np_onu_reg_read

; ppu_debug_get_me_int_mask (0x80d40)
mov   w0, #0xe          ; id 14
bl    #0x7ff08          ; np_onu_reg_read

; ppu_debug_set_me_int_mask (0x80dfc)
mov   w0, #0xe          ; id 14
bl    #0x7ff28          ; np_onu_reg_write

; ppu_debug_get_me_int_flag (0x80b44)
mov   w0, #0xc          ; id 12
bl   #0x7ff08          ; np_onu_reg_read

; ppu_cluster_set_thread_swicth_en (0x816cc)
mov   w0, #0x1a         ; id 26
bl    #0x7ff28          ; np_onu_reg_write
```

### 4.2 位序：**bit N = ME N**（有直接证据）

`ppu_debug_get_spec_me_int_flag(me, out)`（`0x80c44`）：

```asm
cmp   w22, #7          ; me 必须 <= 7   ← 8 个 ME 核
b.ls  #0x80cb0
...
bl    ppu_debug_get_me_int_flag
ldr   w0, [x29, #0x34]
lsr   w2, w0, w22      ; out = flag >> me
str   w2, [x19]
```

⇒ **id 12 那个 8 bit 字的第 N 位就是 ME N 的中断标志**，而且**芯片确实只有 8 个 ME 核**
（`me > 7` 直接被拒）。这是本仓库第一次拿到「8 核」的**一手**证据 ——
不是营销话术，是厂商代码里的一个比较指令。

id 14（掩码）是同宽的 8 bit 字，厂商用它开关各核的中断；
**它哪一位属于哪个核，厂商代码里没有像 flag 那样直接读出来**，本文按同宽推定同序，
文档里标注为「推定」。

### 4.3 板上读数（run 302 镜像，uptime ≈ 409 s）

```
pps reg 0x090000 = 0x00000000     ← id 12，每个 ME 核的标志：全 0（无待处理）
pps reg 0x090004 = 0x00000000     ← id 13，状态字：0
pps reg 0x090008 = 0x000000ff     ← id 14，使能：8 核全开
pps reg 0x09000c = 0x000000ff
pps reg 0x090010 = 0x000000ff
pps reg 0x090014 = 0x00000000     ← id 26，thread switch：关
pps reg 0x090018 = 0x00000000
pps reg 0x09001c = 0x00002fff
pps reg 0x090020 = 0x00000000
pps reg 0x090024 = 0x00000000
pps reg 0x090028 = 0x00000000
pps reg 0x09002c = 0x00000303
```

⇒ **「8 核全开、无一挂起」**，与 ME 空闲一致。
顺带注意 `0x9002c = 0x00000303`，和 ME 表的字长得一样 —— 值得后续查，
本轮没有结论。

### 4.4 中断线：DT 给了 5 条，主线一条都没申请

`zx279133.dtsi` 的 `nppt: ethernet@19000000` 节点带 **5 条 `GIC_SPI`**：

```
interrupt-names = "nppt", "rx", "idm", "buffer-release", "local-test";
```

而主线驱动**一条都没申请**（`platform_get_irq` / `devm_request_irq` 在这棵树里零命中）。
`/proc/interrupts` 里和这块相关的是：

```
22:  0  0  GICv3  33  Level  19000000.ethernet     ← 9132 的线，0 次
```

⇒ **Linux 里从来没有人见过一个 ME 中断**。`mi` 动词是现在唯一的观测口。

### 4.5 厂商自己读不到 PPS

厂商的 `fpga_pps_read_reg()` 在 `pps_power_off_flag` 为真时**整段跳过** PPS 访问，
而 `ppu_me_fifo_int_status(me)` 读的那组偏移（`0x24200 + 2*me` 等）在板上实测**全 0**，
无法区分「空闲」和「地址空间猜错」。

⇒ **这不是「厂商有、主线没有」的能力，是两边都没有。**
docs/97 已记过这一条，本轮再确认一次。

---

## 5. 两个新动词

### 5.1 `mi` —— ME 中断与 cluster 状态（只读）

```
# echo mi > /sys/kernel/debug/zx279133-9132
ppu me int flag = 0x00000000  stat = 0x00000000  mask = 0x000000ff
ppu mf protect detail = 0x00000000  thread switch en = 0x00000000
ppu cluster pc = 0x00000001  rdy = 0x00000001  inst block = 0x00000bff
ppu me 0: flag=0 enabled=1
...
ppu me 7: flag=0 enabled=1
```

（上面这段是 run309 镜像上的**真机输出**，与 §4.3 的实测换算逐字一致；完整实录见 §11。）

### 5.2 `pf` —— 64 个端口的分派字（可读，**可写**）

```
# echo pf  > /sys/kernel/debug/zx279133-9132      # 全部 64 个端口
# echo pf 5 > /sys/kernel/debug/zx279133-9132      # 一个端口
# echo pf 5 0x0000a6a5 > /sys/kernel/debug/zx279133-9132   # 写并回读
```

每个端口一行，把 §1.1 的位布局拆开：

```
ppu pf port  0 = 0x00000463  vld=1 start_pc=  561 flow= 0 attr4=0
ppu pf port  5 = 0x0000a6a5  vld=1 start_pc= 4946 flow= 1 attr4=0
ppu pf port  8 = 0x00039b11  vld=1 start_pc= 3464 flow= 7 attr4=0
ppu pf port 49 = 0x00e52825  vld=1 start_pc= 5138 flow=10 attr4=7
```

**这一行里没有「哪个核」那一列**，是有原因的：`flow → 核` 那一段
**至今没有一手定论**（§1.2 / §7.1），拿一个没人确认过的模型印在板上没有意义。
ME 那张表有自己的动词（`me`）。

（端口字本身是稳的；上面是 run309 镜像上的**真机输出**，
64 行的全表见 §11。）

**这三行样例值以前写错过**：端口 5 的 start PC 一度写成 4914（真值 **4946**），
端口 8 写成 7560 / flow 8 / attr4 1（真值 **3464 / 7 / 0**）——
手算 bits 1..14 时算错了，已按真机输出订正。

**写是数据面**：端口的流量从下一帧起就换一个入口程序、换一个 flow 进去。
值是**整个字**，字段要自己拼（位布局见 §1.1）。

**而这条写路径已经被实测证明是通的**（§1.1 证据 ④：在空闲口 55 上
`0x00000000 → 0x00000002 → 0x00000000`，写进去当场读得出来）。

守卫：端口号必须 < 64；越界返回 `-EINVAL`，不碰硬件。

### 5.3 验收口径

| 项 | 期望 | run309 实测 |
|---|---|---|
| `mi` | rc=0；`flag=0x00000000`、`mask=0x000000ff`；8 行 per-ME，`enabled=1` | ✅ 与 §5.1 的样例**逐字一致** |
| `pf` | rc=0；64 行；端口 0 = `0x00000463`、端口 5 = `0x0000a6a5`、端口 49 = `0x00e52825` | ✅ 64 行；**与刷机前基线逐字节相同**；行内**无 `me=` 列** |
| `pf 5` | rc=0；一行；与 `pf` 全量里的端口 5 一致 | ✅ |
| `pf 64` / `pf 999` / `pf 0x40` | rc=1（`Invalid argument`），**不 panic** | ✅ 三次都 rc=1，板子活着 |
| `pf 5 <板子自报的数>` | rc=0；回读值与写入值相同（写回原值，不改变数据面） | ✅ 写回 `0x0000a6a5` |
| `pf 55 0x00000002` | rc=0；回读 `0x00000002`（vld 仍为 0，不会有流量经过） | ✅ 写进去当场读得出来；再写 `0x00000000` 复原 |
| `lane: far` 规则 | 在 `dynamic_debug/control` 里，且是禁用状态 | ✅ 1 条，状态 `=_`；dmesg 里 `switch: lane: far` = **0** |
| `tc` / `nft` | 可用 | ✅ iproute2-6.18.0 / nftables v1.1.6 |

---

## 6. 二层转发闭环：还差什么

docs/95 §3.2 已经判了：**9132 的 UNI↔UNI 转发是芯片/厂商方案限制**
（flood group 一组只装一个目的口；协议动作表用 trap 取代转发），
所以**不做 DSA 侧的 `port_fdb` / `tx_fwd_offload`**。

### 6.1 2026-11-01 夜：这条不再是「实测缺口」，而是**芯片的属性**（2.5G 口通了之后才看得见）

2.5G 口（= 9132 的 PPU 5 = outerphy 端口 5 = PC 插着的那个口）修通之后
（`docs/UPSTREAM-2G5-LANE-FIX-20261031.md` §11、§12），PC 与板子之间的流量
**全程走得通**（20/20，0% 丢包，0–3 ms）。而这条路径是**看得见的** ——
驱动每次开机都把自己的 DIAG 快照打进日志：

```
[  0.465706] 9132 flood groups of PPU 1..4 point at the CPU port 15
[  0.474231] switch ports 0..7 have the protocol action table at ERAM block 19624 (ARP trapped to the CPU)
[  0.475459] 9132 CPU path: base queue 403, outport 15 -> real port 15 queue 403, reason 0 -> queue 7
```

⇒ **PC 的帧进到 PPU 5 之后，去的是 port 15 = CPU 口**，不是另一个用户口。
这与厂商自己的统计口径在**同一块板、同一根线**上量到的一致（docs/65 §1：
帧在 chip port 2 进来、被协议动作表 trap、走 port 15 交到 CPU）。

⇒ **9132 不做用户口↔用户口转发**；用户口之间的转发是 **Linux 桥**做的
（CPU 收下再从桥上泛洪出去）。**所以「UNI↔UNI 转发」不是缺一次实测，
而是这块芯片的结构**——第二台主机从来就不是判它的必要条件。

**还缺的只有一件事**：一个「帧没有从另一个 UNI 口出来」的**直接**观测。
mainline 不暴露厂商那套 MCODE 分端口计数（`/proc/pon/debug/*_statistics` 是厂商驱动的），
所以要么等第二台主机 + 第二个 UNI 口插线，要么把 MCODE 计数器的地址找出来。

### 6.2 剩下的实测环境缺口（仍然要第二台主机）

**本轮不在台面上执行** —— 一根网卡跑不出「两台主机」这个条件，
硬跑出来的「不转发」不能区分「芯片不支持」和「没插线」。
脚本骨架（等网线能换到 GE 口时直接跑）：

```sh
# 板侧：确认两个 UNI 口的物理链路与端口号
ip -o link show | grep -e lan1 -e lan2
ethtool lan1 ; ethtool lan2
cat /sys/class/net/lan1/carrier ; cat /sys/class/net/lan2/carrier

# 主机侧（PC，插 lan2）：固定地址，准备 ping
ip addr add 192.168.9.50/24 dev eth0
# 板侧 br-lan 摘掉 CPU 口之外的角色，只留 lan1
ip link set lan1 up ; ip link set lan2 down
ping -c 3 192.168.9.50

# 判据： lan1 的 RX 计数涨、CPU 的 eth0 RX 不涨 ⇒ 交换机自己在转
cat /proc/net/dev | grep -e lan1 -e eth0
```

---

## 7. 还剩什么：flow → ME 这一段，**CPU 侧压根没有那张表**

现在分派链三段的状态是：

| 段 | 在哪 | 运行期可写？ | 怎么改 |
|---|---|---|---|
| 端口 → flow | SPA 端口属性字 0 | ✅ **可以**（§1.1 证据 ④ 实测） | `pf <port> <val>`（本轮新增） |
| flow → ME | **CPU 窗口里没有这张表** | ❌ **没有可写的对象** | 只能动 PPU 内部的微码 |

docs/97 当初判的是「ME 表是 cluster 的只读镜像」。**这一轮把它查到底了，
比「只读镜像」更硬**：那个地址根本不是表（§1.2）。

**这一轮补上一条关键线索**：厂商自己改 ME 表走的是
`ppu_cluster_set_me_val_by_flow_num(flow_num, me_val)`，它**不是直接写寄存器**，
而是：

```
读 id 0x42 的第 (flow>>2) 个字  →  清掉 (flow&3)*8 起的 8 bit  →  填入 me_val  →  写回
```

也就是说厂商走的是**寄存器访问层的「读改写」**，不是裸写。
主线是**裸写** `PPS+0x90350`。

### 7.1 厂商那条路也是断的（这一轮查到底了）

把厂商的访问层读完，结论比「还没闭环」更干脆：

**访问层**（`np_onu_reg_read` / `np_onu_reg_write`，`np_133.ko` 0x108824）是这么查表的：

```asm
umull  x5, w0, #28        ; row = id * 28
ldrh   w1, [x3, x5]       ; row->id
cmp    w0, w1
b.ne   -> 返回 1（出错）    ; 必须 row->id == 请求的 id
```

**28 字节一行的确切布局**（逐字段由反汇编确认）：

| 偏移 | 类型 | 字段 | 语义 |
|---|---|---|---|
| 0x00 | u16 | `id` | 请求 id；访问层读它并与请求 id 比较，不等即失败 |
| 0x02 | u16 | — | 填充，访问层从不读 |
| 0x04 | u32 | `type` | 1 = 只读，2 = 只写，3 = 读写 |
| 0x08 | u32 | `base` | **字下标**（不是字节偏移） |
| 0x0c | u32 | `mask` | 读出值 = (reg >> shift) & mask |
| 0x10 | u16 | `shift` | 位移 |
| 0x12 | u16 | `maxoff` | 允许的最大 offset 下标 |
| 0x14 | u32 | `stride` | 相邻 offset 的字距 |
| 0x18 | u32 | `counter` | 运行期使用计数（访问层每次读写 +1） |

**base → 绝对地址的换算**（`base` 是**字下标**）：

```
phys = 0x18000000 + (base + offset * stride) * 4
```

块 id 1 = **PPS**，物理基址 `0x18000000`（厂商 init 从 DT 的 `pps@18000000`
`of_iomap` 拿；`nppt@19000000` 是块 id 2，另一个块）。
⇒ 所以 `0x90350` 是 **PPS 窗口内的字节偏移**，CPU 物理地址是 `0x18090350`。

**表**是 `ppu_cluster_reg_table`（`.data` 0xc820，68 行 × 28 B）。
三参版访问层（`np_onu_reg_read(id, out, index)`，0x7ff08）**把表指针写死**成这一张
（重定位 addend 0xc820 → `ppu_cluster_reg_table`）。

**厂商的 ME 写函数用的是 id `0x42` = 66**，而这张表的**第 66 行是全零的一行**：

| 行 | id 字段 | type | base | 字节偏移 |
|---|---|---|---|---|
| 64 | 64 | 3 | `0x24126` | `0x90498` |
| 65 | 65 | 3 | `0x24127` | `0x9049c` |
| **66** | **0** | 0 | `0x0` | `0x0` ← **全零空洞，id 66 不存在** |
| 67 | 67 | **1（只读）** | `0x240d4` | `0x90350` ← **指向 0x90350 的唯一一行** |

⇒ 访问层拿 id 66 查到第 66 行、发现 `row->id` 是 0 ≠ 66，**直接返回错误**。
**厂商的 `ppu_cluster_set_me_val_by_flow_num()` 在这个 np_133.ko 里根本走不通。**

**「第 66 行是终止行」这个说法本身要改**（上一稿的错）：

- 访问层**没有**任何终止符逻辑：`row = id * 28`，只比对该行的 id 字段，
  从不线性扫表。row 67 物理上在零行**之后**却工作正常 ⇒ 零行不终止任何东西。
- row 66 **必须物理存在**，否则 row 67 会前移到 index 66，`id*28` 寻址就错位。
⇒ 正确说法：**row 66 是一个空洞（hole），不是终止符。**

**「厂商给同一地址发两个 id」这个说法也要改**（上一稿的错）：

- 出厂的 `.ko` 里，**指向 `0x90350` 的只有 id 67 一行**。
  「两个 id」是**重建**出来的假象，不是二进制里的事实。
- 而且「同址多 id」在厂商别的表里是**正常手法**，不是错误：
  `ppu_glb_reg_table` 有 **14 个 id** 落在同一个字上（PPS+0x1C），
  `nppt_glb_reg_table` 有 **21 个 id**。
  本表 68 行的 base 互不相同 —— 本表没用这个手法。

同一个毛病还有一处：`ppu_cluster_set_me_select_en()` 用 id `0x31` = 49，
而**第 49 行的 id 字段写着 47**（base `0x24117` → `0x9045c`，即 pkt-type detail）。
⇒ 它也过不了 id 检查。
（`0x90458` 那一行是 id **48**，厂商用它的函数是
`ppu_cluster_set_mf_protect_detail()` —— 这个函数是好的。）

**读的那一半是好的**：第 67 行（id 67，base `0x240d4` → `0x90350`）的 id 字段
是对的，而且**它自己就声明了 type = 1（只读）**，
厂商的 `ppu_debug_get_me_state(me)`（`cmp w0, #7` 后
`np_onu_reg_read(67, out, me)`）读的就是它。

⇒ **出厂二进制里，`0x90350` 只有一个只读视图，没有可写视图。**
而 §1.2 已经测出：那一片地址读出来的是 **cluster 指令寄存器的投影**，
CPU 写不进去。**两条路同时堵死，而且不是「厂商少写了一行」那么表面。**

**所以「ME 表写不进去」这件事，两边都写不进去**：

| | 怎么写 | 结果 |
|---|---|---|
| 厂商 CPU 侧 | 访问层 id 0x42 → 读改写 | **访问层就报错**，写不到 |
| mainline | 裸写 `PPS+0x90350` | **写不进**（板上有界实验：写后立刻回读，值不变） |
| PPU 微码 | 装载 blob 时由微码自己算 | ✅ **但那是状态，不是分派表** |

⇒ 板上那串会变的值（`0x00000001` / `0x00000303` / `0x00000180` / `0x00180000`，见 §1.2）
**是 cluster 的指令指针**，不是「微码写进去的分派表」。

**下一步（比原来小得多，也比原来更硬）**：不用再找「ME 表到底能不能写」——
**答案是能写也没用，因为那里压根不是表。**
要改 flow → ME 的分派，只剩一条路：

1. **改微码 blob**：找到 PPU 指令流里决定「flow 归哪个核」的那段逻辑 ——
   但它**不在 CPU 可见的窗口里**，得进微码内部找，深 RE，本轮没做。

（上一稿还列过「改厂商那张描述符表，把第 66 行的 id 字段从 0 改成 66」这条路。
**这一轮把它划掉**：即便补上 id 66，
按 id 66 的语义（16 个字 × 4 flow × 8 bit、可读写）
它要的是一个 `maxoff ≥ 15`、`type = 3` 的可写数组；
而硬件在那个位置是**只读状态寄存器**（§1.2 实测），补 id 也写不进去。）

**另外弄清了的一件事**：厂商为什么「看起来」给同一个地址发两个 id ——
**它没有**。出厂二进制里 `0x90350` 只有 id 67 一行。
「同址多 id」是厂商别的表里的手法（14 个 / 21 个），不是本表的。

**未能离线判定的**：row 66 原本的 base 到底该填什么
（候选是 `0x240d4`，即与 id 67 同一块；也可能是 `0x24128`，即接在
pkt_type_detail 序列之后）。两条候选都自洽，**离线无法区分**。

---

## 8. 「8 核 64 线程」的一手出处

docs/95 §1.1 写的是「行业资料描述」，并注明「没有取到可引用的原始 URL，按二手资料标注」。

这一轮又找了一遍，**结论是：中兴没有公开发布过 ZX279133 的数据手册或 NPU 规格页**。
中兴官网的路由器产品页（BE3600 / BE3600 Pro）规格表里只写「新一代高性能处理器」，
**从头到尾没有出现 NPU 的核数**；「8 核 64 线程」这个说法在中文媒体里反复出现
（2025-12-17《WiFi路由器芯片全解》等），但那些文章没有给出数据手册链接。

⇒ **所以一手出处不存在，但这个结论本身现在有一手证据了** ——
**不是靠营销话术，是靠芯片自己的代码和寄存器**：

| 事实 | 一手证据 |
|---|---|
| **8 个 ME 核** | 厂商 `ppu_debug_get_spec_me_int_flag()` 的 `cmp w22, #7` + `b.ls`（ME 号大于 7 被拒）；`ppu_debug_get_me_state()` 的 `cmp w0, #7` 同证 |
| **64 个 flow / 槽** | 端口字 flow 字段 6 bit（0..63）；blob 里 64 条 `PORT_FLOW`；厂商 `ppu_cluster_set_me_val_by_flow_num()` 的 `cmp w20, #0x3f`（flow > 63 被拒）——**但这个函数本身是坏的**（§7.1），所以这一条要打折 |
| **端口 → flow 这段** | SPA 端口属性字 0 的 bits 15..20（§1.1），三个独立来源对齐 |
| **flow → ME 这段** | **没有一手定论**（§1.3）：CPU 侧那块数组厂商只有「读」的份，语义待查 |

⇒ docs/95 §1.1 的来源说明已改写成「一手证据来自芯片自身的代码与寄存器，
公开渠道没有厂商的数据手册或规格页」。

---

## 9. 复现命令

```sh
# PC 端
python staging/ctl/regdesc-range.py 0x90000 0x90040 ppu_cluster
python staging/ctl/dump-regdesc.py | head -70
python staging/ctl/elfsym.py pon/vendor-modules/np_133.ko disasm ppu_debug_get_spec_me_int_flag 65
python staging/ctl/elfsym.py pon/vendor-modules/np_133.ko disasm ppu_cluster_set_me_val_by_flow_num 80
python staging/ctl/elfsym.py pon/vendor-modules/np_133.ko disasm ppu_debug_get_me_int_mask 45
python staging/ctl/portflow-cmp.py \
  'ci-build-repo\overlays\target\linux\zte\zx279133\firmware\zte\zx279133\mcode_intel.bin' \
  'ci-build-repo\kernel-overlays\drivers\net\ethernet\zte\zx279133-np.c'

# 板侧（偏移一律带 0x 前缀）
echo mi > /sys/kernel/debug/zx279133-9132
echo pf > /sys/kernel/debug/zx279133-9132
echo pf 5 > /sys/kernel/debug/zx279133-9132
echo pf 64 > /sys/kernel/debug/zx279133-9132      # 期望 rc=1，不 panic
echo 'pr 0x90000 12' > /sys/kernel/debug/zx279133-9132
dmesg | grep -e 'ppu me int' -e 'ppu pf port' -e 'ppu cluster' -e 'pps reg 0x0900'
```

---

## 10. 本轮改了什么

| 文件 | 改动 |
|---|---|
| `kernel-overlays/drivers/net/ethernet/zte/zx279133-np.c` | 新增 `mi` / `pf` 两个动词；订正「blob 是 SR1010 的」那段注释；订正「0x00008015 是 SR1010 的入口程序」那句；**把「ME 表 = cluster 的只读镜像」从注释里拿掉**（该模型已被 §1.2 证伪） |
| `kernel-overlays/drivers/net/ethernet/zte/zx279133.h` | 新增 PPS 中断三字、thread-switch、mf-protect-detail、cluster inst-block 的常量；新增 SPA 端口字 0 的位域常量；新增 `ZX279133_9132_PORT_COUNT`；`ME_SELECT_EN` → `MF_PROTECT_DETAIL` |
| `docs/95-the-npu-is-there-but-nothing-drives-it-20261031.md` | §1.1 的来源说明改成一手证据（§8）；开头加订正横幅；§1.4/§1.5/§1.7 就地标注 |
| `docs/97-the-me-table-is-read-only-20261101.md` | 开头加订正横幅：「ME 表在 CPU 侧根本不存在」 |
| `docs/99-the-dispatch-chain-and-the-ppu-interrupts-20261101.md` | §1.2 重写（`0x90350` 不是表）；§5.2 样例值订正；§5.3 验收口径换成实测值；**新增 §11 run309 验收实录** |

**没有新增 CI 门禁**（`ci-build-repo/tests/README.md:70` 已定「收敛，不再加新门禁」），
**没有动 apply 路径**（端口分派表的写入时机和内容一字未改），
**没有动 ME 表的写路径** —— 但 docs/97 的**结论本身变了**：
「ME 表是只读镜像」这句话被 §1.2 证伪，已经改成「那个地址根本不是表」。

**本轮刷了机**：run309（head `154034c`）的 kernel + root（dtb 与板上逐字节相同，未刷），
板侧验收结果见 §11。

---

## 11. run309 验收实录（2026-11-01）

本节是**板子上的实测记录**，不是推演。镜像：CI run #41288921286（run309，
head `154034c`），release `skd840n_flash_309_41288921286`。

### 11.1 刷了什么

| 项 | 值 |
|---|---|
| 刷的分区 | kernel + root（**dtb 没刷**：与板上 mtd3 逐字节相同，md5 `ee287b5c1bc47c26c35cdaa2a462176d`） |
| 刷机时刻 | 17:25:34 拿到 U-Boot 提示符 → 17:26:54 全部写完，**80 秒** |
| kernel | 下载 crc `50393251` ✓；擦除后头尾 0xFF crc `f154670a` ✓；写后回读 `50393251` ✓ |
| root | 下载 crc `73eb4d36` ✓；写后回读 ✓；`mk=DONE-root` |
| 刷机后的启动 | 17:28:31 U-Boot `autoboot: a boot is starting` → 17:28:35 内核 |
| 插座冷启动 | **17:46:04**（与 17:25:34 相隔 1230 s，节拍严格对上）→ 17:46:08 内核 |

**产物预检救了一命**：run305 的 initramfs `.itb` 解出来的内核里，
`pf` 那行仍带 `me=` 列、`mi` 里仍有 `ppu me select en` ——
那与本文档 §5.2「没有 `me=` 列」直接矛盾。**因此放弃 run305，改刷 run309。**

### 11.2 版本

```
root@ImmortalWrt:~# uname -a
Linux ImmortalWrt 6.18.38+ #0 SMP Mon Oct 26 04:58:01 2026 aarch64 GNU/Linux
root@ImmortalWrt:~# md5sum /etc/openwrt_release
c389ce2c7a45d4246ed85c8e99a8d8a2  /etc/openwrt_release
```

### 11.3 `mi`（只读）—— 与 §5.1 的预测逐字一致

```
[   42.062889] ppu me int flag = 0x00000000  stat = 0x00000000  mask = 0x000000ff
[   42.062930] ppu mf protect detail = 0x00000000  thread switch en = 0x00000000
[   42.062940] ppu cluster pc = 0x00000001  rdy = 0x00000001  inst block = 0x00000bff
[   42.062949] ppu me 0: flag=0 enabled=1
...
[   42.063008] ppu me 7: flag=0 enabled=1
```

rc=0。**没有 `ppu me select en` 那一行**（`dmesg | grep -c 'ppu me select en'` = 0）。

### 11.4 `pf`（可读可写）

- 全量 rc=0，**64 行**；
- 行内**没有 `me=` 列**（`dmesg | grep 'ppu pf port' | grep -c 'me='` = **0**）；
- **与刷机前基线逐字节一致**（基线是 `staging/ctl/baseline-20261101.md` 里那 64 口的 word 0 全表）：
  端口 0 = `0x00000463`、6 = `0x00000463`、5 = `0x0000a6a5`、8 = `0x00039b11`、
  10 = `0x00c0800b`、12 = `0x00009f99`、15 = `0x0000a767`、49 = `0x00e52825`、
  50 = `0x00e52827`、51..54 = `0x00e00000`、55..63 = `0x00000000`；
- `pf 5` rc=0，一行，与全量里的端口 5 一致。

**越界守卫**（端口号必须 < 64）：

```
root@ImmortalWrt:~# echo "pf 64" > /sys/kernel/debug/zx279133-9132
ash: write error: Invalid argument      rc=1
root@ImmortalWrt:~# echo "pf 999" > /sys/kernel/debug/zx279133-9132
ash: write error: Invalid argument      rc=1
root@ImmortalWrt:~# echo "pf 0x40" > /sys/kernel/debug/zx279133-9132
ash: write error: Invalid argument      rc=1
```

三次都 rc=1，**板子没 panic**（`0x40` = 64，同样被挡）。

**写路径分两级验**：

1. **零风险**：把板子自己报的数写回去 —— `pf 5 0x0000a6a5` → rc=0，回读 `0x0000a6a5`。
2. **证可编程**：空闲口 **55**（word 0 恒 0，vld=0，不会有任何流量经过它）：

```
[   62.225221] ppu pf port 55 = 0x00000002  vld=0 start_pc=    1 flow= 0 attr4=0
[   62.250691] ppu pf port 55 = 0x00000000  vld=0 start_pc=    0 flow= 0 attr4=0
```

写进去当场读得出来；写回 `0x00000000` 即复原。端口 5 未受影响。

### 11.5 PPS 原始读数（`pr`）

```
[   73.125745] pps reg 0x090000 = 0x00000000    ME int flag
[   73.125798] pps reg 0x090004 = 0x00000000    ME int stat
[   73.125807] pps reg 0x090008 = 0x000000ff    ME int mask（8 个核全开）
[   73.125834] pps reg 0x090014 = 0x00000000    thread switch en
[   73.125886] pps reg 0x09002c = 0x00000303
[   73.125950] pps reg 0x090d00 = 0x00000001    cluster pc
[   73.125962] pps reg 0x090d04 = 0x00000001    cluster rdy
[   73.125970] pps reg 0x090d08 = 0x00000bff    cluster inst block
[   73.125979] pps reg 0x090d0c = 0x00000180    INST_LO
[   73.125987] pps reg 0x090d10 = 0x00180000    INST_HI
[   73.125996] pps reg 0x090d14 = 0x000001a0
[   73.126005] pps reg 0x090d18 = 0x00180000
[   73.126013] pps reg 0x090d1c = 0x000001c0
[   73.126059] pps reg 0x090350 = 0x000001c0
[   73.126069] pps reg 0x090354 = 0x000001c0
[   73.126078] pps reg 0x090358 = 0x000001c0
[   73.126086] pps reg 0x09035c = 0x000001c0
```

⇒ **§1.2 的结论在冷启动的新镜像上再次复现**：`0x90350` 这一片读出来
不是表，而是一个跟着 cluster 走的活值 —— 此刻是 `0x000001c0`（与 `0x90d1c` 相等），
而前一次暖启动的同一地址读到的是 `0x00000180`，`me`/`mes` 读到的是 `0x00000303`。
（这一批读数是**顺序**采的，cluster 在读的当口还在往前走，
所以具体配到哪个 INST 寄存器会随时刻漂移；**变的是它本身**这件事才是结论。）
四个相邻的字地址读到**同一个值**，与「每个槽位各有内容的表」不相容。

### 11.6 `cam` 与 `me`/`mes`

```
[   90.574441] cam: alg crc 0x00000000 bulk 0x00000002 depth0 0x00000012 depth1 0x00000000 smmu1 0x0009e100
```

rc=0。`mes 7 3` 也是 rc=0，但 16 个字读回来全是同一个活值 `0x00000303`
（`ppu me word 0 = 0x00000303  flows 0:3 1:3 2:0 3:0` …）——
**「CPU 写不进去」这条负结果在冷启动上照样复现。**

### 11.7 dynamic_debug 与 lane 刷屏

- `/sys/kernel/debug/dynamic_debug/control` 存在，**2772 行**；
- `lane: far` 规则**在**里面（1 条，状态 `=_` 即禁用）；
- `dmesg | grep -c 'switch: lane: far'` = **0**；`dmesg | grep -c 'switch: lane'` = **15**（一次性转储）。

⇒ 刷屏归零这件事在冷启动上照样成立。

### 11.8 用户态

```
root@ImmortalWrt:~# tc -V
tc utility, iproute2-6.18.0
root@ImmortalWrt:~# nft -v
nftables v1.1.6 (Commodore Bullmoose #7)
```

`tc qdisc show dev br-lan` 正常；`nft list ruleset` 列出 nat/dnsmasq 表。

### 11.9 网络状态（与刷机前基线一致）

```
  eth0:       0       0    0    0    0     0          0         0      260       2    0    5    0     0       0          0
br-lan:       0       0    0    0    0     0          0         0     1364      10    0    0    0     0       0          0
```

`eth0` 仍是 `NO-CARRIER … state DOWN`（板上没有 PON 链路、没有 2.5G 链路），
`/proc/interrupts` 里 `zx279133-idm-local` 计数 **0**（9132 的 ethernet 中断没有申请过）。

### 11.10 这一轮验到了什么、没验到什么

**验到了**：

- `mi` / `pf` 两个新动词在**冷启动的新镜像**上工作正常，输出与预测逐字一致；
- `pf` 的写路径真的能把端口字写进硬件（写进去当场读得出来）；
- 越界守卫生效，不 panic；
- 64 个端口的属性字与刷机前**逐字节一致** ⇒ 换内核没有动数据面；
- `switch: lane: far` 刷屏归零；`tc` / `nft` 可用。

**没验到**：

- **UNI↔UNI 二层转发**（§6）：需要两个 UNI 口插线 + 第二台主机，这个台面只有一根网卡；
- **flow → 核**这一段：CPU 侧没有可写对象（§1.2 / §7.1），`mes` 的负结果依旧；
- **PPU 的 ME 中断**：`mask = 0x000000ff`（8 个核全开）但 `flag = 0` ——
  中断使能是开的，可是一根也没来。这与 §4.4「DT 给了 5 条 IRQ，主线一条都没申请」一致：
  **中断语义定了，但中断还没接上。**
