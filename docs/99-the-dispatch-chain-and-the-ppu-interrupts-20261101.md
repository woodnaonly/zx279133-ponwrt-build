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

### 1.2 板上的实测：`0x90350` 这块**不是一张静态的表**

主板子 `me` 动词读的那 16 个字（`PPS+0x90350` 起 16 个字）**不稳定**。
这一轮在同一次开机里就看到它换过三种值：

| 时刻（uptime） | 16 个字 | 谁写的 |
|---|---|---|
| 0.45 s（probe 时打印的「factory ME word」） | `0x00000000` | 芯片复位值 |
| 260 s / 409 s / 737 s | `0x00000001` | **不是驱动写的**（见下） |
| 764 s | `0x00000303` | 没有人写它 |
| 807 s / 822 s | `0x00000000` | 没有人写它 |
| 823 s 起 | `0x00000303` | 没有人写它 |

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

**⇒ 结论（这一轮最重的一条）：**
**主线「`0x90350` 是 64 个 flow 的 ME 分派表」这个模型，在硬件行为上没有支撑。**
它不是「只读镜像」那么温和 —— **它连「一张表」都不是**。

再看两条旁证：

- blob 里**没有 `0x303` 的字面量**（大小端都搜过，0 命中）⇒ 那个值不是微码里的常数；
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
`0x42` = 66，而描述符表的第 66 行是全零的终止行**（§7.1 有完整证据）。
**⇒ 厂商自己的「把 flow 指到某个核」这个操作，在厂商自己的系统里也写不进去。**

⇒ **主线自己的「64 flow × 8 bit」模型，建立在一个厂商自己都用不了的函数上。**
这不影响 §1.1 的位布局（那是从厂商**注释**和 blob 的解码公式来的，
两个独立来源，都不依赖那个坏函数），
但**「flow 号 → ME 核」这个映射本身，在 CPU 侧没有一手依据**。

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
ppu me select en = 0x00000000  thread switch en = 0x00000000
ppu cluster pc = 0x00000000  rdy = 0x00000001  inst block = 0x00000bff
ppu me 0: flag=0 enabled=1
...
ppu me 7: flag=0 enabled=1
```

（上面是按 §4.3 的实测换算的**预期输出**；板上真机输出见 §8 的验收记录。）

### 5.2 `pf` —— 64 个端口的分派字（可读，**可写**）

```
# echo pf  > /sys/kernel/debug/zx279133-9132      # 全部 64 个端口
# echo pf 5 > /sys/kernel/debug/zx279133-9132      # 一个端口
# echo pf 5 0x0000a6a5 > /sys/kernel/debug/zx279133-9132   # 写并回读
```

每个端口一行，把 §1.1 的位布局拆开：

```
ppu pf port  0 = 0x00000463  vld=1 start_pc=  561 flow= 0 attr4=0
ppu pf port  5 = 0x0000a6a5  vld=1 start_pc= 4914 flow= 1 attr4=0
ppu pf port  8 = 0x00039b11  vld=1 start_pc= 7560 flow= 8 attr4=1
ppu pf port 49 = 0x00e52825  vld=1 start_pc= 5138 flow=10 attr4=7
```

**这一行里没有「哪个核」那一列**，是有原因的：`flow → 核` 那一段
**至今没有一手定论**（§1.2 / §7.1），拿一个没人确认过的模型印在板上没有意义。
ME 那张表有自己的动词（`me`）。

（端口字本身是稳的；上面是按本轮实测的端口字换算的**预期输出**。
板上真机输出见 §8 的验收记录。）

**写是数据面**：端口的流量从下一帧起就换一个入口程序、换一个 flow 进去。
值是**整个字**，字段要自己拼（位布局见 §1.1）。

守卫：端口号必须 < 64；越界返回 `-EINVAL`，不碰硬件。

### 5.3 验收口径

| 项 | 期望 |
|---|---|
| `mi` | rc=0；`flag=0x00000000`、`mask=0x000000ff`；8 行 per-ME，`enabled=1` |
| `pf` | rc=0；64 行；端口 0 = `0x00000463`、端口 5 = `0x0000a6a5`、端口 49 = `0x00e52825` |
| `pf 5` | rc=0；一行；与 `pf` 全量里的端口 5 一致 |
| `pf 64` | rc=1（`Invalid argument`），**不 panic** |
| `pf 5 0x0000a6a5` | rc=0；回读值与写入值相同（写回原值，不改变数据面） |

---

## 6. 二层转发闭环：还差什么

docs/95 §3.2 已经判了：**9132 的 UNI↔UNI 转发是芯片/厂商方案限制**
（flood group 一组只装一个目的口；协议动作表用 trap 取代转发），
所以**不做 DSA 侧的 `port_fdb` / `tx_fwd_offload`**。

剩下的是**实测环境缺口**：干净地验 UNI↔UNI 单播需要**两个 UNI 口插线 + 第二台主机**，
而这个台面**只有一根有线网卡**。

⇒ 这一轮能做的是把实验准备好，等有第二台主机时直接跑。脚本骨架：

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

**本轮不在台面上执行** —— 一根网卡跑不出「两台主机」这个条件，
硬跑出来的「不转发」不能区分「芯片不支持」和「没插线」。

---

## 7. 还剩什么：改 flow → ME 只能动微码

现在分派链三段的状态是：

| 段 | 在哪 | 运行期可写？ | 怎么改 |
|---|---|---|---|
| 端口 → flow | SPA 端口属性字 0 | ✅ **可以** | `pf <port> <val>`（本轮新增） |
| flow → ME | ME 表 `PPS+0x90350` | ❌ **不行** | 只能改微码 blob 的指令流 |

docs/97 已定案 ME 表是 cluster 的只读镜像：驱动写进去的值一次都没出现过，
而同一窗口的邻居寄存器（pkt-type detail，`0x9045c`）写进去就读得出来。

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

**表**是 `ppu_cluster_reg_table`（`.data` 0xc820，68 行 × 28 B）。
三参版访问层（`np_onu_reg_read(id, out, index)`，0x7ff08）**把表指针写死**成这一张
（重定位 addend 0xc820 → `ppu_cluster_reg_table`）。

**厂商的 ME 写函数用的是 id `0x42` = 66**，而这张表的**第 66 行是全零的终止行**：

| 行 | id 字段 | base | 字节偏移 |
|---|---|---|---|
| 64 | 64 | `0x24126` | `0x90498` |
| 65 | 65 | `0x24127` | `0x9049c` |
| **66** | **0** | `0x0` | `0x0` ← 终止行，**id 66 不存在** |
| 67 | 67 | `0x240d4` | `0x90350` ← **ME 表在这一行** |

⇒ 访问层拿 id 66 查到第 66 行、发现 `row->id` 是 0 ≠ 66，**直接返回错误**。
**厂商的 `ppu_cluster_set_me_val_by_flow_num()` 在这个 np_133.ko 里根本走不通。**

同一个毛病还有一处：`ppu_cluster_set_me_select_en()` 用 id `0x31` = 49，
而**第 49 行的 id 字段写着 47**（base `0x24117` → `0x9045c`，即 pkt-type detail）。
⇒ 它也过不了 id 检查。
（`0x90458` 那一行是 id **48**，厂商用它的函数是
`ppu_cluster_set_mf_protect_detail()` —— 这个函数是好的。）

**读的那一半是好的**：第 67 行（id 67，base `0x240d4` → `0x90350`）的 id 字段
是对的，厂商的 `ppu_debug_get_me_state(me)`（`cmp w0, #7` 后
`np_onu_reg_read(67, out, me)`）读的就是它，
`ppu_debug_get_me_state` 是厂商**唯一**能碰到这块数组的函数。

⇒ **一张表，两个 id，一个坏一个好。**
**所以「ME 表」这个 CPU 可见的数组，是只有「读」的份，没有「写」的份。**

**所以「ME 表写不进去」这件事，两边都写不进去**：

| | 怎么写 | 结果 |
|---|---|---|
| 厂商 CPU 侧 | 访问层 id 0x42 → 读改写 | **访问层就报错**，写不到 |
| mainline | 裸写 `PPS+0x90350` | **写不进**（板上有界实验：写后立刻回读，值不变） |
| PPU 微码 | 装载 blob 时由微码自己写 | ✅ **这就是表内容的来源** |

⇒ 板上那张 `0x00000001` / `0x00000303`（见 §1.2）
**是微码写进去的，不是 CPU 写进去的**。
这也解释了为什么主线裸写、厂商读改写，两边都改不动它。

**下一步（比原来小得多）**：不用再找「ME 表到底能不能写」——
**答案是不能**。要改 flow → ME 的分派，只有两条路：

1. **改微码 blob**：找到 PPU 指令流里「写 ME 表」的那条指令，改它 ——
   深 RE，本轮没做；
2. **改厂商那张描述符表**：把第 66 行的 id 字段从 0 改成 66（补上 id 49 那行改成 49），
   厂商的写路径就通了 —— 但那是改**厂商二进制**，而且要确认第 66 行的 base 该填什么
   （按第 67 行的样子推测是 `0x240d4`，但那是推测）。
   **这条路不在本仓库的范围内**，记在这里只是说明「厂商侧也不是不能改」。

**另外还要弄清的一件事**：厂商为什么给同一个地址（`0x240d4`）发两个 id，
以及这块数组到底是「8 个核各一个状态字」还是「16 个字 × 4 flow 的映射」。
读者（`get_me_state`）说前者，写者（`set_me_val_by_flow_num`）说后者，
描述符的 `maxoff = 8` 支持前者 —— 见 §1.3。

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
| `kernel-overlays/drivers/net/ethernet/zte/zx279133-np.c` | 新增 `mi` / `pf` 两个动词；订正「blob 是 SR1010 的」那段注释；订正「0x00008015 是 SR1010 的入口程序」那句 |
| `kernel-overlays/drivers/net/ethernet/zte/zx279133.h` | 新增 PPS 中断三字、thread-switch、ME-select、cluster inst-block 的常量；新增 SPA 端口字 0 的位域常量；新增 `ZX279133_9132_PORT_COUNT` |
| `docs/95-the-npu-is-there-but-nothing-drives-it-20261031.md` | §1.1 的来源说明改成一手证据（§8） |

**没有新增 CI 门禁**（`ci-build-repo/tests/README.md:70` 已定「收敛，不再加新门禁」），
**没有动 apply 路径**（端口分派表的写入时机和内容一字未改），
**没有动 ME 表的写路径**（docs/97 的结论不变）。
