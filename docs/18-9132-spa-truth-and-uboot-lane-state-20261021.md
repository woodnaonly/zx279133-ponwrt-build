# 18 — 9132 的每口真值、U-Boot 的 lane 工作态，与 run86 的实测（2026-10-21）

本文件接着 `docs/16-9132-topology-and-driver-design-20261021.md` 与
`docs/17-9132-dsa-ports-and-ci-tree-identity-20261021.md`，记录本轮拿到的三组新证据，
以及由此改掉的两个判断。

---

## 1. 厂商 SPA 每口属性表 —— 直接读出来的活板真值

厂商内核的 `/proc/pon/tm_debug/module_ind_ram` 可以把模块 RAM 整块倒出来。
`spa` 模块下有 41 块 20 字 × 32 位的"每口属性表"，端口名就是厂商自己的命名：

```
spa_port_attr_pon_0 / pon_1
spa_port_attr_smac_0 .. smac_3
spa_port_attr_xmac_0 / xmac_1
spa_port_attr_rgmii
spa_port_attr_trap(cpu) / loop / vir_err
spa_port_attr_wifi_ssid_0 .. wifi_ssid_23 / wifi_woe
spa_port_attr_pkttype_mem
```

活板（厂商 4.19 正在跑、`isolate=0 / enable=1` 的工作配置）读出来的值：

| 口 | word0 | word1 | word2 | word9 | word13 | **word19** |
|---|---|---|---|---|---|---|
| smac_0 | 0x0000a6a5 | 0x0000088f | 0x00000000 | 0 | 0x00008008 | **0x96040001** |
| smac_1 | 0x0000a6a5 | 0x0000088f | 0x00000000 | 0 | 0x00008008 | **0x96040001** |
| smac_2 | 0x0000a6a5 | 0x0000088f | 0x00000000 | 0 | 0x00008008 | **0x96040001** |
| smac_3 | 0x0000a6a5 | 0x0000088f | 0x00000000 | 0 | 0x00008008 | **0x96040001** |
| xmac_0 | 0x0000a6a5 | 0x0000088f | 0 | 0 | 0x00008008 | 0x96040001 |
| xmac_1 | 0x0000a6a5 | 0x0000088f | 0 | 0 | 0x00008008 | 0x96040001 |
| rgmii | 0x0000a6a5 | 0x0000088f | 0 | 0 | 0x00008008 | 0x96040001 |
| pon_0 | 0x0000a6a5 | 0x0000088f | 0 | 0 | 0x00008008 | 0x90040000 |
| trap(cpu) | 0x0000a767 | 0x0000088f | 0 | 0 | 0x00008008 | 0x00000000 |

三条结论：

1. **厂商的 untag SVID（word2）与 VID 过滤掩码（word9）在每个口上都是 0。**
   厂商的工作配置里根本没有 VID 语义 —— 这与 `tm_port_vlan_filter_get` 读出 0、
   `sw_vlan_port_inframemode_get` 读出 mode 0 两条独立证据一致，也与
   `docs/16 §2.2` 的结论一致。
2. **word19 = 0x96040001 是"带流量的口"的字**（smac/xmac/rgmii），
   0x90040000 是 PON 与 WiFi 口，0 是 trap/loop/vir_err。
   具名的 bit（31 SA_LEARN / 28 DA_LOOKUP / 26 MUL_FLOOD / 25 BRD_FLOOD /
   18 IN_VLAN_CHK）加起来是 **0x96040000**，**bit 0 是带流量的口才有的那一位**。
   于是驱动改为照抄厂商的整字（`0x96040001`），而不是用具名 bit 拼一个差一位的值。
3. **word1 = 0x88f 在每个口上都一样**，且我们的写入读回后仍然是 0x88f ——
   这是"PPU 1..4 就是厂商的 smac_0..3"的逐位印证（见 §2）。

证据文件：`staging/ctl/probe3-out.txt`（厂商 help 全文 + 每口 getter 的应答）、
`staging/ctl/serial.log`（`module_ind_ram -all` 的完整转储）。

---

## 2. run86 实测：SPA 通路与 MDIO 通路都是通的

run86（commit `fe64f6f`）的 initramfs 用 U-Boot 从 RAM 起内核，拿到的 G2 证据：

```
zx279133-lan-conduit 19000000.ethernet:ethernet-lan-cpu: LAN conduit netdev registered
zx279133-eth 19000000.ethernet: 9132 PPU 1 attribute word 96040000 (VID 59)
zx279133-eth 19000000.ethernet: 9132 PPU 1 PVID word 1 0000088f
zx279133-eth 19000000.ethernet: 9132 PPU 1 PVID word 2 00ec0000
...                                    PPU 2/3/4 同
zx279133-eth 19000000.ethernet: 9132 UNI ports 1..4 up (transport VIDs 59..62)
zx279133-eth 19000000.ethernet: 9132 UNI PHY 0a: id 84b9:5032, BMCR 1040, link 0
zx279133-eth 19000000.ethernet: 9132 UNI PHY 0b: id 84b9:5032, BMCR 1140, link 1
zx279133-eth 19000000.ethernet: 9132 UNI PHY 0c: id 84b9:5032, BMCR 1040, link 0
zx279133-eth 19000000.ethernet: 9132 UNI PHY 0d: id 84b9:5032, BMCR 1040, link 0
zx279133-switch 19000000.ethernet:switch: XMAC0 datapath enabled; XPCS0 status=0x280b link=down xmac_link=0x0000
```

- **G2a PASSED**：属性字与两个 PVID 字写进去之后读回来逐位相同
  （word2 的 0x00ec0000 → VID 59，0x00f00000 → 60，0x00f40000 → 61，0x00f80000 → 62），
  **SPA 间接通路是通的**。而且读回的 word1 = 0x88f 与厂商 smac 口逐位相同，
  **PPU 1..4 = 厂商的 smac_0..smac_3** 这件事从此不再是推断。
- **G2b PASSED**：mdio0 上 0x0a..0x0d 四个 PHY 都在，ID 全是 `84b9:5032`，
  **0x0b 有链路**（= PC 插的那个口，与 `docs/15 §8` 一致），其余无链路。
  BMCR 0x1140 与 0x1040 的差是协商结果被锁回寄存器。
- **G2c 未过**：XPCS0 status = 0x280b，link down。

---

## 3. U-Boot 的 lane 工作态 —— 差异只有两处

U-Boot 的 autoboot 里带一次 netboot，所以**在 U-Boot 提示符下读到的就是它刚跑完
11.5 MB tftp 的工作态**。用 `tools/ubregs.sh` 读到的：

```
19000090: 0000380b        <- XPCS0 status，bits 11,12,13 全置 = link up
19000084: 00000000        <- NPPT XMAC link
19140500: 00000100        <- XMAC0 DUPLEX
161000d0: 55000400        <- Uni SerDes PLL status（与 mainline 读到的完全相同）
161000e4: 100019fc        <- Uni SerDes RX status（与 mainline 读到的完全相同）
19140000..1914001c: 全 0  <- 树里 XMAC0 的基址 0x140000，读出来是 0
191434a0..191434dc: 全 0  <- SOPC ready/send 数组
```

U-Boot 自己的 net bring-up 把顺序说得很清楚：

```
read SOPC_CLR_OVER_READY_SMAC = 0x1, mac = 0x0
write SOPC_SEND_EN_CFG_SMAC,mac = 0x0
... mac = 0x1 ... mac = 0x2 ... mac = 0x3
enter outerphy info init! ... No outerphy on this port<6> / <5>
--->phy identifier is [0xc849]
5 serdes option is 0
xmac0 is used.
idm_ddr_base is 0x00000000b0000000
mode_xgpon_nsyn_cfg_133 / pon serdes init succeed / np init ok
mac 0 link down
mac 1 phy status changed: 1000M full-duplex
mac 2 link down / mac 3 link down / mac 4 link down
Using eth0 device
TFTP from server 192.168.9.25 ... Bytes transferred = 11542708
```

由此得到与 mainline 的**两处差异**（也是本轮改动的全部内容）：

### 差异一：PCS

- U-Boot 留下 **0x380b**（link up）；
- 我们的 1G SGMII profile 把它变成 **0x280b**，**恰好少 bit 12**。

所以 `zx279133-switch.c` 现在先读 lane 的状态，**PCS 还在说 link up 就不编程，
直接继承 bootloader 的配置**；只有 link 已经是 down 时才走原来的编程路径。

### 差异二：SOPC publish

- U-Boot **对 mac 0..3 逐个**先读自己的 ready 字（`nppt+0x34000+((mac+0xa9)<<2)`）、
  再写 send-enable 字（`nppt+0x34000+((mac+0xb0)<<2)`）；
- 树里的 RTL8372N 驱动只知道数组里的**第一个** mac（0x342a4 / 0x342c0），
  **树里从来没有任何代码写过这个数组** —— run62 的计数器就是这么说的：
  `smct_done 24`、队列全清、`xmac_tx_frames 0`：描述符全部完成，但一个帧都没出 MAC。

所以驱动现在**逐个 ready 轮询后把四个 mac 全 publish**。

### 附带确认

- 厂商 U-Boot 打印 `xmac 0 speed set 2 in sgmii(xmac speed 3)`：
  **1G SGMII 的寄存器速度码就是 3**，驱动原来写的 3 是对的。
- `mac 1 phy status changed: 1000M full-duplex` 与我们的 PHY 0x0b 是同一个口
  （厂商的 `sw_port_change_to_smac_index(p) = p`），互为印证。

---

## 4. 拓扑定案

把 §1 的 SPA 名字、§3 的 U-Boot 序列和 `docs/16 §2` 的网口表放在一起：

```
        ┌──────────── 9132（交换机芯片）────────────┐
        │  CPU 口                                     │
   Uni1 │  PHY 0x0a   ←  smac_0  ←  SoC 的 NPPT 侧   │
   Uni2 │  PHY 0x0b   ←  smac_1  ←  （厂商 eth1）    │
   Uni3 │  PHY 0x0c   ←  smac_2  ←  （厂商 eth2）    │
   Uni4 │  PHY 0x0d   ←  smac_3  ←  （厂商 eth3）    │
        │      ▲                                    │
        │      └──────── XMAC0 / XPCS0 / Uni-SerDes ───┘  1G SGMII，厂商 xmac0
        └───────────────────────────────────────────┘
```

- **LAN 侧** = SoC 的 XMAC0（SerDes lane）→ 9132 的 CPU 口 → 4 个 UNI 口，
  4 个 UNI 口在硬件上是**一个桥域**；
- **per-port 分离**在 SoC 侧由 NPPT 的 MF 表按 MAC 完成（厂商 FDB 里那条
  `84:47:09:5b:bd:2c → port_id 2` 就是它），厂商再给每个 UNI 口一个虚拟 MAC；
- 所以 mainline 侧 **per-port netdev 就是 DSA**：conduit = 厂商 eth0 那个 MAC
  （树里 `lan-cpu0`），用户口 = 4 个 UNI 口。`docs/16 §4.4` 的判断不变。

---

## 5. 这一轮之后

| # | 未决 | 怎么定 |
|---|---|---|
| 1 | 9132 能不能按 VID 把 CPU 口来的帧送到指定 UNI 口并在出口脱标 | run90 之后决定：若 lane 起来后 DSA 用户口不通，就改走厂商的虚拟 MAC 方案（§4） |
| 2 | 9132 的 per-port 隔离矩阵怎么写（D13） | 不在 M1 路径上；DSA 的 `port_bridge_join/leave` 足够 |
| 3 | 厂商的虚拟 MAC 怎么写进 FDB | 只在 #1 为否时才需要 |
| 4 | `uni-phy` 里的 0x1c、0x02 是什么 | 仍未知；不影响 M1 |
| 5 | XMAC0 的 1G SGMII 档要不要给 `phy-zx279133-uni-serdes.c` 补一档 | **已闭合**：`mode 0x1` + 49 词实测档，板上 0/49 差异 |
| 6 | 厂商的 `xmac0` 与 RGMII MAC（0x15900000）的关系 | §4 已定：xmac0 = 9132 的 CPU 口 lane；RGMII MAC + RTL8226 是另一个独立口（jack 4） |

下一轮的判据在 `docs/17` 的基础上加一条：**lane 的 XPCS0 status 必须是 0x3800
（link up），并且 `XMAC link` 不再是 0x0000**。
