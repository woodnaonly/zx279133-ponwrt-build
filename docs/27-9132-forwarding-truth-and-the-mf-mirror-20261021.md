# 27 — 9132 的转发真值：厂商活板对照、整块寄存器导出，以及"属性字镜像"这个真正的堵点

写于 2026-10-21。承接 `docs/26`（LAN 数据面与 9132 真值）。本文件记录 run105 之前这一轮拿到的
三组新证据，以及由此定案的一个具体缺陷。**没有改 flash，没有改板子配置，所有厂商侧操作都是只读的。**

---

## 0. 结论先说

1. **9132 确实是硬件 MAC 学习桥**，架构（DSA：conduit = PPU 5，四个 UNI 口 = PPU 1..4）没有错。
2. **厂商侧的 SPA 寄存器块可以整块导出**（`/proc/pon/tm_debug/module_reg -r spa`），已经存成
   `staging/ctl/vendor-spa-dump.txt`（1024 字 + smct 256 字），可与 mainline 逐字对照。
3. **真正的堵点：每口的"属性字"有两份，而芯片读的是 ERAM 那一份，不是 SPA 内存那一份。**
   厂商的 `port_attr_def_*_set()` 每改一位就把整个属性字镜像进 ERAM 表 0x7d（每口一项）。
   我们只写了 SPA 内存那份。**CPU 口（PPU 5）在 ERAM 里的那份还是 RTL8372N 路径留下的
   0x00042000** —— 没有学习使能、没有 DA 查表、没有泛洪位。在芯片真正读的那张表里，
   CPU 口是个死口，于是双向一帧都不通。
4. run107（commit `c1da3fa`）把属性字镜像回 ERAM，并给调试控制台加了 `fa <mem> <attr>`。

---

## 1. 决定性对照实验：厂商板上，"PC 插的那只口" 的硬件计数在 ping 时同增

厂商 4.19 活板在一次断电重启后被拉起来（`root@192.168.9.1`），于是可以在**厂商自己的系统里**
做一次带硬件计数的对照。

`/proc/pon/tm_debug/sw_port_statistics1_get` 的参数写法是 `key=value`，键名是 `sw_port`（不是
`port`——用 `port=` 的话它会静默按 0 处理）。读端口 0..4 的计数，然后从 PC ping 192.168.9.1，
再读一次：

| 厂商 sw_port | 身份 | ping 前 inpkts | ping 后 inpkts | ping 前 outpkts | ping 后 outpkts |
|---|---|---|---|---|---|
| 0 | smac0 = UNI1 | 0 | 0 | 2 | 2 |
| **1** | **smac1 = UNI2（PC 插的就是这只口）** | **1212** | **1352** | **921** | **1031** |
| 2 | smac2 = UNI3 | 0 | 0 | 101 | 105 |
| 3 | smac3 = UNI4 | 0 | 0 | 0 | 0 |
| 4 | 外挂 RTL8226 | 0 | 0 | 0 | 0 |

⇒ **PC 的帧在硬件上从 9132 的 smac1 口进（inpkts 涨），回复也从同一只口出（outpkts 涨）。**
这是 9132 自己做转发的证据，不是 CPU 收上来再软转发的证据。

⇒ 厂商的 FDB 里只有一条 `84:47:09:5b:bd:2c → port_id 2`（`port_id` 是**芯片口号**，
`sw_port 1` 对应芯片口 2，因为 `sw_port_change_to_chip_port(p) = p+1`），这正是
"学到了 PC 的 MAC，于是 CPU 发的帧按 DA 查表定向到 UNI2"。

**架构判定：9132 = 硬件学习桥；CPU 口 = xmac_0 = PPU 5；四个 UNI 口 = smac_0..3 = PPU 1..4。
DSA 的 conduit 取 PPU 5 是对的，run104 的做法不用推翻。**

---

## 2. 厂商的 SPA 寄存器块可以整块导出

厂商内核的 `/proc/pon/tm_debug/module_reg` 用法（在 `help` 里）：

```
echo [module_name]              > .../module_reg       # 打印该模块的信息
echo -r [mod] reg=0xNN cnt=0xN > .../module_reg       # 读 N 个寄存器
echo -w [mod] reg=0xNN val=0xN mask=0xN > .../module_reg
echo -all / -all -ind_ram       > .../module_reg
```

输出格式 `<mod> reg=0x<idx> val=0x<val>`，**idx 是字下标**。

模块表（`echo help > .../module_reg`）：

```
sys sub_sys module flag base reg_cnt
nppt nppt  nppt_greg  2 0x0     0x100
nppt nppu  sdet       2 0x800   0x180
nppt nppu  sipc       2 0x1000  0x100
nppt nppu  spa        2 0x2000  0x400
nppt nppu  smct       2 0x3000  0x100
nppt nppu  isu        2 0x4000  0x100
nppt nppu  odma       2 0x5000  0x100
nppt nppu  reorder    2 0x6000  0x100
nppt nppu  soam       2 0x7000  0x100
nppt nppu  woe        2 0x81000 0x800
nppt nppu  idm        2 0xa0000 0x200
nppt nppu  crm        2 0xb0000 0x10
nppt tm    red/qmg/usch/ssch/wsch/woe_sch/uopc
```

**地址换算（实测钉死）**：`spa` 模块的 `reg=N` 就是 **NPPT 基址 + 0x8000 + 4N**。
证据：`reg=0x16` 读出 `0x1`（= `ZX279133_SPA_IND_STATUS` 的 done 位，地址 0x8058），
`reg=0x17` 读出 `0x96040001`（= 刚刚读回来的 PPU 1 的属性字，地址 0x805c = `SPA_IND_DATA`）。
mainline 驱动里 `ZX279133_SPA_IND_CMD/STATUS/DATA = 0x8054/0x8058/0x805c`，
与 `spa` 模块的 0x2000..0x23ff 相差 0x6000，即厂商工具用的是合成基址。

**导出结果**：`staging/ctl/vendor-spa-dump.txt`（spa 1024 字 + smct 256 字）。
非零项 250 个，值得记的有：

| reg | 值 | 备注 |
|---|---|---|
| 0x0000 | 0xe8000000 | |
| 0x0005 | 0x06111113 | |
| 0x0006 | 0x01111111 | |
| 0x0016 | 0x00000001 | = SPA_IND_STATUS，done 位 |
| 0x00a1..0x00a4 | 0x88a88100 / 0x94009100 / 0x81009200 / 0x07020703 | ONU MAC / trap 相关 |
| 0x00c1..0x00ed | 0xffffffff 与 0x00007fff 交替 | 16 组 MAC 过滤槽 |
| 0x0280..0x02af | 0xffffffff 与 0x00ffffff 交替 | |
| 0x0300..0x0302 | 0x01050105 / 0x01050105 / 0x0000009c | |
| 0x0358..0x0377 | 若干 | |

导出脚本：`staging/ctl/vdumpregs3.sh`（**注意 busybox 的 `[` 用十进制比较，块数必须写十进制**，
写成 `0x400` 会被当成 0，循环一次都不会跑 —— 这是第一版导出全是 0 行的原因）。

**用法**：run106 之后，mainline 板上有 `nd 8000 1024` 把自己的同一块导出，
`staging/ctl/spa_diff.py` 做逐字对照并生成可回放的 `nwp` 命令行。

---

## 3. 厂商的端口属性字有两份，芯片读的是 ERAM 那份

离线 RE（`staging/ctl/re-fdb-api.md`，子代理产出）把 `np_133.ko` 的
`port_attr_def_sa_lookup_learn_en_set`（`.text+0xb9f4c`）解开了：

```asm
mov  w2, #0x47                    ; field id 0x47 -> SPA addr 0x013（整个属性字）
bl   spa_port_attr_tbl_get
ldrb w1, [x29, #0x47]
bfi  w1, w20, #7, #1              ; 学习使能 = 属性字的 bit 7
strb w1, [x29, #0x47]
bl   spa_port_attr_tbl_set        ; 写回 SPA 内存
mov  w0, #0x7d
bl   se_eram_table_get            ; 并且镜像进 ERAM 表 0x7d
ldrb w0, [x29, #0x4b]
bfi  w0, w20, #7, #1              ; 同一个 bit，镜像的那份
mov  w0, #0x7d
bl   se_eram_table_set
```

⇒ **每改一位属性字，都要同时写 SPA 内存和 ERAM 表 0x7d（每口一项，64 项）。**
同一族（`da_lookup_en` / `isolate_en` / `input_vlan_check_en` / `mac_limit_full_flag` /
`src_is_extend_port_flag`）全都镜像进 0x7d。

mainline 驱动里的对应物是 **MF extension table**（`ZX279133_MF_ERAM_BASE_BLOCK 17106`，
64 项、每项 128 位、word 3 就是属性字）—— 注释里写着
"the SPA multi-forward engine consults to accept CPU-originated frames for a port"，
说的正是同一件事。

### 3.1 缺陷：两处写入的顺序错了

驱动的硬件准备顺序是：

```
zx279133_mf_port_ppuinit()        -> 逐口：SPA word19 |= BIT(18)，并把属性字写进 MF ERAM
zx279133_lan_port_bringup()       -> RTL8372N 路径：mem5 word19 = 0x00042000，
                                     且 zx279133_mf_eram_attr_set(5, 0x00042000)
zx279133_9132_port_init()         -> 9132 路径：mem1..5 word19 = 0x96040001（厂商原值）
```

最后一步把 SPA 内存改成了厂商的值，**却没有把 ERAM 那份一起改**。
于是：

| PPU | 身份 | SPA word19 | MF ERAM word3 | 一致？ |
|---|---|---|---|---|
| 1..4 | 9132 的四个 UNI 口 | 0x96040001 | 0x96040001（ppuinit 时旧值已相同） | 是 |
| **5** | **9132 的 CPU 口（conduit）** | **0x96040001** | **0x00042000** | **否** |

`0x00042000` = bit 17 + bit 18：既没有 SA_LEARN(31)，也没有 DA_LOOKUP(28)，
也没有 BRD_FLOOD(25)/MUL_FLOOD(26)。**在芯片真正读的那张表里，CPU 口是一个死口。**

这与实测完全吻合：PHY 有链路、端口内存是厂商原值、DSA 侧数据面已声明就绪，
但**双向一帧都不动**。

### 3.2 修法

`zx279133_9132_port_init()` 在写完 SPA 内存之后，把同一个字镜像进 MF ERAM
（run107，commit `c1da3fa`）：

```c
ret = zx279133_mf_eram_attr_set(eth, ppu, ZX279133_9132_VENDOR_ATTR_WORD);
```

调试控制台也长出一条手工命令（run106/107）：

```
fa 5 96040001        写 CPU 口那一份并读回
```

---

## 4. 根因之二：协议动作表（reason → action），以及 reason 0/1 的补丁

### 4.1 帧到 CPU 只有一条路：陷阱

厂商 `switch.ko` 的 `sw_init_flood_ctrl`（反汇编见 `staging/ctl/switchko-init-20261020.txt`）：

```
zxic_api_sw_port_{mul,unk_da,brd}_flood_dest_port(wan, 0, &mask)   /* WAN 口：目的为空 */
zxic_api_sw_port_{mul,unk_da,brd}_flood_en(wan, 0)
for p in 0..7, p != wan:
    zxic_api_sw_port_{mul,unk_da,brd}_flood_dest_port(p, 1, &mask)
    zxic_api_sw_port_{mul,unk_da,brd}_flood_en(p, 1)
```

`mask` 是调用者放在栈上的一块，`x21` 指向它；里面第一个字节被写成 `g_wan_port_id`，
其余是垃圾。所以**每个 LAN 口的泛洪组里只有 WAN 口一个成员**。

⇒ **目的地址不在 MAC 表里的帧，永远不会被泛洪到 CPU 口。**
⇒ 帧要上 CPU，只能靠**协议动作陷阱**。

厂商自己的计数印证了这一点（`staging/ctl/vendor-tm-stats-20261020.txt`）：

```
protocol action trap cnt(32bit)   7
in idm fwd cnt(32bit)              7
out idm fwd cnt(32bit)             7
port id 15 output uni cnt(8bit)    7
```

那 7 个正是测试 PC 那次 ping 的一来一回。**PC 的帧就是靠陷阱进 CPU 的。**

### 4.2 陷阱规则在哪：每口的 reason → action 表

`switch.ko` 的 `sw_init_port_protocol_action`（0x2370）：

```
set_protocol_action(g_wan_port_id)          /* 0x21dc：77 条 (协议, 动作) */
for p in 1..7:   set_port_action(p)          /* 0x22a8：77 条 (协议, 动作) */
for p in 0x10..0x30: set_port_action(p)
```

两个函数都是把 **0x268 字节（77 对 u32）从自己的 .rodata 拷到栈上**，然后逐对调用
`tm_port_protocol_pktdeal_set(port, action, protocol)`。

`np_133.ko` 侧（0x112498）：

```c
int tm_port_protocol_pktdeal_set(u32 port, u32 protocol, u32 action)
{
    if (port > 0x3f) return -1;
    byte = (byte & ~3) | ((action - 2) & 3);      /* 动作占 2 bit */
    return port_reason_action_table_set(protocol, port, &byte);
}
```

`port_reason_action_table_set`（0xb1074）：

```c
offset = port | (reason << 7);
se_eram_table_set(0x18, offset, 1, &byte);       /* ERAM 表 0x18 */
```

⇒ **ERAM 表 0x18，每口 128 个 reason，每个 entry 16 字节（4 word），动作在 entry 的低 2 bit。**
⇒ 动作 2 = 陷阱到 CPU（厂商的编码是 `(action-2)&3`，所以动作 2 落在 0，动作 0 落在 2）。

### 4.3 关键：np.ko 的表和 switch.ko 的表只差 reason 0 和 1

驱动里已有的 `zx279133_lan_proto_table`（`zx279133-np.c`）就是 np.ko 给一个 LAN 口的
**128 项 × 16 bit** 的表。把它按 entry 解出来，再和 switch.ko 的 .rodata 里的 77 对
逐项比（`staging/ctl/prodec.py`）：

```
reason  driver-field  vendorB-action  vendorB-field
    0       2            2              0   <-- differs
    1       2            2              0   <-- differs
    8       0            2              0
    9       0            2              0
   ...     （其余 74 项全部一致）
```

⇒ **驱动里的表和厂商 `switch.ko` 的表逐项相同，只有 reason 0 和 1 不同：**
np.ko 留成 action 0（普通转发），switch.ko 改成 action 2（陷阱）。

**reason 0 就是 ARP。** 而 ARP 是客户端发的第一个帧。

⇒ mainline 写的是一张"ARP 走普通转发"的表，而且**根本没有给 9132 的四个 UNI 口写过这张表**。
⇒ 9132 把客户端的每一个帧都丢了：既不能转发给 CPU，也不允许泛洪给 CPU。

**修法（run108，commit `2439661`）**：给 PPU 1..4（四个 UNI 口）和 PPU 5（CPU 口）
写 `zx279133_9132_proto_table` = 驱动的 LAN 表，但把 reason 0、1 的低 2 bit 清零
（动作 2 落在 0）。**只有第一个 word 变**：`0x40024002` → `0x40004000`。

### 4.4 厂商另外两张还没写的表

| 厂商调用 | 表 | 我们的状态 |
|---|---|---|
| `sw_init_flood_ctrl` | 泛洪目的表（group id → 口列表） | 未写（LAN↔LAN 泛洪用；本路径靠陷阱，不拦 M1） |
| `sw_init_omci_trap_qid_init` | ERAM 表 0x19，reason 0 → qid 7 | 未写（qid 0 也在 `queue < 8` 内，驱动照样当 LAN 源收） |

---

## 5. 还没有定的

| # | 未决 | 怎么定 |
|---|---|---|
| 1 | ERAM 表 0x7d 的物理地址与项布局是否就是 MF extension table（block 17106） | 子代理 `re-eram-7d.md`；run106 的 `er` 命令可实机读 block 17106+n 对照 |
| 2 | 厂商 `sw_init_flood_ctrl` 写的泛洪目的表（"table 125"）与 `sw_init_port_isolate` 的隔离矩阵（"ERAM table 27"） | 同上；文档 15 的 D12/D13 把位置记错了，要按 0x7d/0x1b 重新定位 |
| 3 | 厂商 `sw_init_port_protocol_action` 给芯片口 2..8 装的协议动作表 | 同上；我们只给 SoC 逻辑口 0/5/10/59..62 装了 PRO 表 |
| 4 | 9132 的 FDB 落在 ZCAM 还是 DDR | 子代理报告 §1.2：按 `g_se_hash_table_support_ddr` 运行时决定；**不需要写**，SA 学习开着就会自己学 |
| 5 | SoC 侧（XMAC0 lane）到底有没有把帧放上线 | run105 加的 lane 计数器（0x140000+0x2070 / +0x20b0） |

---

## 5. 复现命令

```bash
# 厂商侧：整块 SPA 寄存器（只读）
sh staging/ctl/vdumpregs3.sh | ssh -i ~/.ssh/id_onu root@192.168.9.1 sh -s \
    > staging/ctl/vendor-spa-dump.txt

# 厂商侧：哪只口在动（参数是 sw_port=N，不是 port=N）
sh staging/ctl/vexp4.sh

# mainline 侧（run106 之后）：同一块
sh staging/ctl/ourspa.sh | ssh -i ~/.ssh/id_onu root@192.168.1.1 sh -s

# 对照并生成回放命令
python staging/ctl/spa_diff.py staging/ctl/vendor-spa-dump.txt our-spa.txt
```
