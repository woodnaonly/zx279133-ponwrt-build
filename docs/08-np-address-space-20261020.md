# 08 - NP 地址空间与 IDM 环（2026-10-20 实测，未实现）

状态：这是一份**测量结论 + 待实现规格**。代码未改、未推、未上板。工作区干净（`git status` 空，
HEAD = `4a0ef55` 补九十七）。下一位接手请从 §3 开始，不要重复 §4 里已经否掉的实验。

## 1. 仪器：U-Boot 会打印自己的描述符

`backups/mtd1_boot.bin` 偏移 `0x9ff3e-0xa06d0` 里有三条从未用过的命令和它们的格式串：

- `idm_tx_debug [en]` / `idm_rx_debug [en]` — 打开后 U-Boot 逐字段打印它自己 post 的描述符
  （`tx desc0x%p:` / `rx desc address 0x%p:` / `buff address : 0x%p, len : %u` /
  `idm_ddr_base is 0x%p`）；不带参数则打印当前值。
- `idm_tx_outport [outport]`。
- `spa_port_attr_op` 裸跑答 `error`，`spa_port_attr_op 0` 打印 MEMID 与 port_attr_id 映射表。

`tools/idmdesc.sh` 在 `ping` 前后打开它，于 08:28:44 拿到**这块板子上第一个「成功的 RX 描述符」**
（RX 侧是所有 mainline run 里唯一从来没通过的一半）。**这个方法比反汇编 `np_133.ko` 优先**：它直接给出
工作实现的答案，不需要符号也不需要猜。

坑：这个 U-Boot **没有编译 `echo`**（`Unknown command 'echo'`），所以所有 `echo TAG-BEGIN` 标记都不存在，
切分日志只能用中继打的 `=== >>> HH:MM:SS sent:` 行。

## 2. 实测的工作镜像

冷启 U-Boot，`ping 192.168.9.25` 回 `host 192.168.9.25 is alive`，同一 boot 内：

```
0x19280000: 01ff0007 b0008000 b0028000 04000000
            00000020 00010001 x11        00001000  00000001(有流量后)
                 |          |
                 |          +-- RX ring base      +-- TX ring base
```

TX 描述符（0x20 字节一项）：

```
b00080c0: b0328000 0f800054 00460000 0 0 0 0 0
address 0xb0328000, addr_type 0, ptk_len 42, offset 128, checksum_en 0, inport 15,
ipv4_ipv6_flag 0, tcp_udp_flag 0, omci_flag 0, l3_offset 0, l4_offset 0,
output_port_valid 1, output_port 6 ;  buff 0xb0328080 len 42
```

RX 描述符（0x20 字节一项，walk 过 0xb0028000..0xb0028180 连续 13 项）：

```
b0028000: b00e8000 078200b3 c0a80919 effffffa 0 00020c00 0 0   ptk_len 179
b0028020: b00e8900 0782003c 00000000 00000000 0 00000c00 0 0   ptk_len 60
b00280c0: b00eb600 0782005a 4560e537 ced5bb7d 0 00010c14 0 0   ptk_len 90
```

解码出的布局（可直接照抄）：

| 项 | 值 |
| --- | --- |
| RX buffer 起点 / stride / payload 偏移 | `base+0xe8000` / **0x900** / **buffer+0x40** |
| TX payload 起点 / payload 偏移 | `base+0x328000` / **buffer+0x80** |
| TX word1 | 低字节 = `len<<1`（42→0x54），bits 27:24 = inport（15），bit23 置位 |
| **RX word1** | 低字节 = **len 原值**（60→0x3c，90→0x5a，179→0xb3），非左移 |
| RX word2/3 | 该帧的 IP src / dst |
| RX word5 | `0x0c00` / `0x20c00` / `0x10c14`，随帧类型变（queue/reason 字段） |
| TX word2 | `BIT(22) | output_port<<16`（0x00460000 = port 6） |

对照 mainline：**描述符格式本来就对**（`zx279133-netdev.c:384-390` 已经是 `skb->len << 1` +
`zx279133_tx_selector << 24` + `BIT(23)`；`ZX279133_NP_POOL_TX_OFF = 0x328000` 正是 U-Boot 打印的
`0xb0328000` 那一格）。唯一分歧是**地址空间**。

顺带记到 U-Boot 的 bring-up 打印里，mainline 没有对应物：它轮询 `reg = 0x192c0004`
（`val =0xffffffff` → `reset val =0xfffffffe/fd/fb/f7`）并对 **mac = 0,1,2,3** 依次写
`SOPC_SEND_EN_CFG_SMAC`；随后 `idm_ddr_base is 0x00000000b0000000`、`np init ok`。

## 3. 判定：`0xb0000000` 不是 CPU DRAM 的 +0x30000000 别名（`tools/npspace.sh`，08:47:35-08:48:14）

同一个 boot、NP 正在写 RX 环的时候，四路互证：

1. `md 0xb0028000` = 活的 NP 描述符；`md 0x80028000` = `ffffffff`，连读三次一致。
2. `mw 0x800280c0 0xa5a50101` → `md 0x800280c0` 读得到，`md 0xb00280c0` 仍是 `b00eb600 0782005a`。
3. `mw 0xb00280c4 0xb4b40202` → 在 `0xb00280c4` 出现，在 `0x800280c4` **永远不出现**。
4. 远端一对排除局部镜像：`mw 0x803f0000 0xcafe0303` 自己可见，`md 0xb03f0000` =
   `ffffffff ffffff7f`。

**所以「给填进去的地址 +0x30000000」这种改法被否掉。** NP 必须被交给窗口**内部**的内存。

## 4. 待实现（唯一剩下的 RX 修复方向）

NP 能触碰的一切——两个 ring base 寄存器 **和每一个 RX buffer**——都要分配在 `0xb0000000` 窗口里，
并且按 TX payload 已经在用的方式驱动：`devm_ioremap_wc`（`zx279133-main.c:384`）+
`memcpy_toio` / `readl`（`zx279133-netdev.c:377-384`）。

现在 mainline 的做法是 `dmam_alloc_coherent` 出两个环（`main.c:435/454`，run 69 量到
`0x9a080000` / `0x9a100000`），RX buffer 填 `page_pool` 的 dma_addr（`netdev.c:499/564/633`）——
这些格子 NP 看不见。这也是本项目里第一个能同时解释「doorbell 响了但环没被消费」和「`rx_packets` 恒 0」
的机制。

规模提示（决定要不要缩环），已核对过仓库与 DTS：

- 窗口尺寸是**我们自己声明的**：`zx279133-sk-d840n.dts:339-340` =
  `zte,np-pool-base = <0xb0000000>; zte,np-pool-size = <0x400000>;` → **4 MiB**，不是硬件上限。
- RX 侧若要进窗口，按现有常量要吃 **1.5 MiB 环**（`IDM_RX_QUEUES 24` × `IDM_RX_RING_SIZE 2048` ×
  描述符 0x20）**+ 4.6 MiB buffer**（`IDM_RX_BUFFER_COUNT 2048` × `IDM_RX_BUFFER_STRIDE 0x940`）
  = **6.1 MiB**，4 MiB 装不下（本文早先写的 1.9 MiB 是算错的，`0x940 × 2048 = 0x4A0000`）。
- 由 probe 成功这一事实反推 TX 侧预算：`main.c` 要求 `size >= NP_POOL_TX_OFF + NP_POOL_TX_SIZE`，
  而 kit 能起来、eth0 存在，说明 `0x328000 + CPU_TX_QUEUES × 64 × 0x940 <= 0x400000`，
  即 `CPU_TX_QUEUES <= 78`。（注意：本文件此前那句「TX 侧已需要约 12 MiB」作废，那是用错常量算的。）
- U-Boot 只用**一条** RX 环、buffer stride **0x900**、buffer 区仅约 184 KiB（`0xb00e8000..0xb0116000`）。
  所以实现要么照厂商缩（单队列 + 小 ring），要么抬高 `np-pool-size`。
- **抬多大是硬件问题，必须先量**：窗口真实边界从未测过。做法（与 kit 无关，可在任意 U-Boot 窗口做）：
  沿 `0xb0000000 + n·0x100000` 递增 `mw` 一个标记值再 `md` 读回，并确认 `0xb0400000` 之后是否是同一批
  格子；同时用 `md 0xb0328000` 这类「U-Boot 已知在用」的格子做正对照，避免把「读不到」误判成「不存在」。


## 5. 已经否掉、不要重做的实验

- `idm_tx_outport`：不写任何 MMIO（`0x19008000/0x19000000/0x19002000` 在设 6 或 1 后逐字不变），
  **也不是**出口闸门——`idm_tx_outport 0` 之后 `ping` 照样 `is alive`（双侧对照，08:26）。
- `spa_port_attr_op get_port_attr` 冷启全读 0：那是**状态**不是**规格**，U-Boot 从不跑 PPU switch 初始化，
  所以这些 0 不能当成 Linux 该填的值（这条同时削弱了「mem6 addr 1/13/17/18/19 写实测 0」这个既有改动的依据）。
- `switch.ko` 本身**零硬件写**：`sw_init_switch` 的 20 个叶子全调用只在 `np_133.ko` 里定义的
  `zxic_api_sw_*`/`tm_*`。它的 init 确实写了 mainline 没写的表（ERAM **0x22/0x23/0x24**、
  `tm_port_route_mode_set`、`tm_vlan_check_ena_set(port 5,6)`、`reason_map_cpu_qid(0,0,7)`、port isolate、
  `glb_lan_up_en/port` + `np_onu_reg_write` 0x1a）——**但** U-Boot 证明过的路径很可能绕过这些表，
  所以只在 §3 的地址空间修复之后仍然 `rx_packets` 不动时才轮到它。复扫工具：`staging/swspa.py`。

## 6. 本轮的板子状态（供下一位对照）

- 08:38:39 从 **flash 冷启**的 mainline：`eth0 RX packets:0 / TX packets:17`，
  `ping -c 4 -I eth0 192.168.9.25` = `4 packets transmitted, 0 packets received, 100% packet loss`。
- 已刷进去的 root **没有** `rdreg` / `wrreg` / `ethtool`（root 是故意没动的，只刷了 dtb+kernel），
  所以 Linux 侧读寄存器只有两条路：RAM-boot 带 applet 的 kit，或把要看的值直接打进驱动日志（推荐后者，
  一次上板同时拿到日志和结论）。
- U-Boot 窗口在本轮里可复现：`:15/:30/:45` 断电后 `Hit any key to stop autoboot: 1 0` 之前必须已经
  在灌 `u` 流；08:45 那次到 prompt 用了 62 次探测（约 8 分钟），所以 `UBWAIT` 别设小于 1200。
