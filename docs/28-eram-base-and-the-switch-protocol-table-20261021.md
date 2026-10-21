# 28 — ERAM 表写在硬件不知道的地方（2026-10-21）

> 本文是 G2 数据面（9132 一帧不通）调查的第三个根因。前两个见
> `docs/27-9132-forwarding-truth-and-the-mf-mirror-20261021.md`：
> 根因之一是端口属性字只写进了 SPA 内存、没有镜像回 MF 扩展表；
> 根因之二是协议动作表没给 9132 的口写。
>
> 这一个比前两个更靠底：**三张表写的位置，硬件根本不知道。**

## 1. 症状回顾

run104 起，9132 双向零帧。厂商活板对照证明：**架构是对的**（9132 在硬件上
自己做转发，厂商板上 PC 插着的那个口 in/out 计数同增），端口属性、PHY、
conduit、entry program 都已经逐字照抄厂商。

于是剩下的怀疑对象就是"写进芯片的那些表"。

## 2. 厂商的 ERAM 寻址模型（离线逆向，np_133.ko）

厂商把 SE 的表放在一个 ERAM 池里，`se_sdt_get_eram(id)` 返回
`.bss+0xb6e0 + id*8` 里那份 8 字节的运行期描述符。

**base 不是编译期常量。** `se_sdt_creat_eram_sdt()` 建表时调用
`se_smmu0_get_available_space_for_eram(depth, &base)`，从一个游标
（`.data+0xd138+0x120`，初值 0x1000 = 12288）**顺序取号**：

```
addr_ind = (base << 7) + index * 128        # 128 字节为一个 block
```

也就是说：**一张表在哪，是它前面所有表加起来决定的**；表一改，后面全部平移。

## 3. 本仓库驱动里的三处硬编码 base

`zx279133-np.c` 原来写着三个常量：

| 常量 | 值 | 说的是哪张表 |
|---|---|---|
| `ZX279133_MF_ERAM_BASE_BLOCK` | 17106 | MF 扩展表（端口属性字的镜像） |
| `ZX279133_PRO_ERAM_BASE_BLOCK` | 21266 | 协议表 |
| `ZX279133_PORT_TYPE_ERAM_BASE_BLOCK` | 23026 | 端口类型表（SDT 6） |
| `ZX279133_VLAN_ERAM_BASE_BLOCK` | 12288 | VLAN 动作表（SDT 0） |

**这三个值没有一个是驱动自己算出来的那个。** 它们来自另一份 SDT 表
（通用 np.ko 的 `g_core_sdt_info_133`，那份表有 0x7d，本驱动的
`zx279133_core_sdt_info[]` 没有）。

把本驱动的表按它自己的分配器（`zx279133_se_sdt_create()`，`eram_base`
从 12288 起顺序分配）算一遍：

```
0x00  12288   VLAN 动作表        <- 驱动里写死的 12288，对的
0x02  16386   per-wanid L3 MTU   <- 驱动里写死的 16386，对的
0x04  20754   NPPT 自己的协议表
0x06  22514   端口类型表          <- 驱动写的是 23026
0x18  22322   交换机协议动作表
0x7d  27532   MF 扩展表          <- 驱动写的是 17106，而且这张表
                                     根本不在驱动的表里，硬件压根
                                     不知道有它
```

（复算工具：`staging/ctl/sdtbase.py`、`sdtbase2.py`、`sdtbase3.py`。）

**所以：MF 扩展表、协议表、端口类型表，三张表全部写在了硬件不会去读的地方。**
厂商 np.ko 的 MF 扩展表 id 是 **0x7d**（64 项 × 128 bit，按 SPA memory id
索引），端口属性字在 entry 的 word0，bit 31 是 SA 学习使能。

## 4. 交换机协议动作表的格式也对不上

厂商 `switch.ko` 的 `.rodata` 里有两张 77 对 (协议, 动作) 表：
表 A 在 `+0x210`（WAN 口用），表 B 在 `+0x498`（其它口用）——**两张只差一个
条目（协议 75）**。

厂商的写法（`tm_port_protocol_pktdeal_set` → `port_reason_action_table_set`）：

```asm
; tm_port_protocol_pktdeal_set(port, action, proto)
bfxil  w4, w1, #0, #2     ; 动作的低 2 bit，原值写入
strb   w4, [x29, #0x20]
; port_reason_action_table_set(reason=proto, port=port, &byte)
orr   w19, w0, w21, lsl #7 ; index = reason | (port << 7)
mov   w2, #1              ; 写 1 个 word
mov   w0, #0x18            ; SE ERAM 表 0x18
```

⇒ **表 0x18 的 entry 是"每 (口, 协议) 一个 word"**，动作在 word 的低 2 bit。

本驱动原来写的是**NPPT 自己的 16 bit 协议表**（一张 block 里塞 8 个 reason），
写到 9132 的口上——格式和地址两处都不对。

## 5. 改法

1. ERAM base 改成运行时从**驱动自己那份分配**里取（就是它告诉硬件的那一份），
   按表 id 查；表 0x7d 加进 `zx279133_core_sdt_info[]`，让 MF 扩展表真正存在。
2. 交换机协议动作表按厂商格式写：表 0x18，entry `(port << 7) | protocol`，
   一个 word，动作是厂商自己的值（2 = 交给 CPU）。低 8 个 switch port 都写，
   这样 4 个 UNI 口和 CPU 口无论按哪套编号都对得上。
3. debugfs 控制台打印驱动创建的每一张 ERAM 表的 base，便于和芯片对账。

## 6. 这条结论怎么复核

`staging/ctl/sdtbase.py` 复算分配；`staging/ctl/protoc.py` 从 switch.ko 的
.rodata 里把两张表解成 C 数组；`staging/ctl/re-eram-7d.md` 是 ERAM 表 0x7d 的
离线逆向报告（子代理产出，注意其中"属性字在 word0"一条与本驱动实测的
"word3"不符，以驱动实测为准）。
