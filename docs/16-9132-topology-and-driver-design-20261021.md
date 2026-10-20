# 16 — SK-D840N 的 9132 拓扑与驱动设计（2026-10-21）

本文件是 G2 门的设计输入。G1（`docs/15-switchko-init-diff-20261020.md`）已经把厂商
`switch.ko` 的 init 写集和 mainline 的差集列出来了，但那 14 条差集**不足以**解释
"9132 的端口没有 bring-up"，原因是差集的前提搞错了：本轮在活板上量到的事实表明，
**9132 根本不是"差集里少了几笔"的问题，而是 mainline 侧从来没有一条能到 9132 的
数据通路**。本文件记录量到的事实、由此推出的架构，以及据此写出的驱动设计。

所有活板数据都是只读取到的（`/proc/pon/*`、`/proc/device-tree/*`、`/sys/class/net/*`、
`dmesg`）；没有写 flash，没有动模块，没有改板子配置。

---

## 1. 一句话结论

**SK-D840N 上的 9132，就是 SR1010 上 RTL8372N 的同一个角色：一颗挂在 SoC 的 XMAC0
LAN 通道上的外部交换芯片。** mainline 树里已经有这个角色的完整实现
（`zx279133-rtl8372n.c` + `zx279133-lan-conduit.c` + `zx279133-lan.h` 的父子服务
接口），只是它写死了 RTL8372N。计划里 W2 要新建的 `zx279133-switch.c`，就是把这套
机制照搬给 9132。

而现在 `kernel-overlays/.../zx279133-sk-d840n.dts` 把 `&nppt` 指向
`phys = <&uni_serdes>` / `pcs-handle = <&xpcs0>`，**而 mainline 的 `zx279133-mac.c`
写的是 `ZX279133_XMAC1_BASE`（0x180000）** —— 寄存器基址和 PCS 属于两个不同的 MAC。
这条错配就是"lane 上一个字节都不动"的直接原因（详见 §4.3）。

---

## 2. 活板量到的事实

### 2.1 端口与网口

| 厂商 switch port | 含义 | enable | link | 活板取证 |
|---|---|---|---|---|
| sw0 | UNI 口 1 | 1 | 0 | `sw_port_enable_get` / `sw_port_linkstate_get` |
| sw1 | UNI 口 2 | 1 | **1** | **PC 插的就是这个口** |
| sw2 | UNI 口 3 | 1 | 0 | |
| sw3 | UNI 口 4 | 1 | 0 | |
| sw4 | outerphy | 1 | 0 | `outerphy_mode=5,outerphy_name=phy_RTL8226` |
| sw5 / sw6 | 非 smac（xmac0/1_to_extend） | — | 1, speed 6 | `smac_get_port_enable` 报 `error port=5/6` |
| sw7 | invalid | — | — | `sw_port(7) is not unvalid smac` |

每口的隔离使能、VLAN 过滤掩码、ingress 帧模式**全部为 0**
（`sw_port_isolate_get` → `enable:0`，`tm_port_vlan_filter_get` → `pp_vlTagMsk:0`，
`sw_vlan_port_inframemode_get` → `mode:0`，端口 0..7 全部如此）。

### 2.2 9132 是一颗扁平的无标签学习桥

厂商 FDB（`echo show > /proc/pon/tm_debug/tm_mactable_fdb_show`）在活板上只有一条：

```
mac_addr = 84:47:09:5b:bd:2c      <- 本机 PC 的 MAC
vid      = 0x0000
port_id = 2
```

`vid = 0`、每个口的 VLAN 过滤掩码为 0、ingress 帧模式为 0 —— 三条独立证据一致地
说明：**这台机器上的 9132 不做 VID→口的映射，它是一颗按 MAC 学习转发的透明桥。**

这条事实直接改写了 G1 差集里 D9/D10/D11/D12（默认 VLAN 优先级 / untag SVID /
vlancfg / flood 目的口）的意义：厂商自己在跑的系统里根本没用这些字段，往里写
transport VID **不像是"补齐缺失"，更像是"给一颗不需要 VLAN 的芯片强加 VLAN 语义"**。
所以这一轮的 G2a 改动里，PVID 写入仍然保留（它同时是"厂商 init 到底写了什么"的
对照实验），但**不再把它当作让端口工作的必要条件**；真正必要的是 §2.3 和 §2.4。

### 2.3 四个 UNI 口的内部 PHY 在 MDIO 0x0A–0x0D

活板设备树里，**两个 MDIO 控制器（`soc/mdio@14f01000` = mdio0，
`soc/mdio@14f02000` = mdio1）都带一个 `uni-phy` 属性**，内容是同一个 u32 数组：

```
00000000  00 00 00 0a  00 00 00 0b  00 00 00 0c  00 00 00 0d
00000010  00 00 00 05  00 00 00 1c  00 00 00 02
```

即 `{ 0x0a, 0x0b, 0x0c, 0x0d, 0x05, 0x1c, 0x02 }`。

- `0x0a..0x0d` —— 四个内部 UNI PHY，**计划原文写的"四个 PHY 0x0A–0x0D"完全正确**；
- `0x05` —— jack 4 的 RTL8226，mainline 的板级 DTS 已经把它声明在 `&mdio1` 的地址 5；
- `0x1c` / `0x02` —— 未解释（见 §6 未决项）。

厂商的"端口使能"就是走这条 MDIO：`np_133.ko` 的 `smac_set_port_enable()` →
`zte_set_gephy_enable(phy, en)` → 读 BMCR、清 bit 11（POWERDOWN）、写回，
底层是 `zx_mdio_0_read/write`，**即 MDIO 控制器 #0**。
→ G2b 的实现路径因此是确定的：`mdiobus_read/write(bus, 0x0a..0x0d, MII_BMCR)`。

### 2.4 SoC 的 MAC 结构

活板厂商设备树里有 **mainline dtsi 里根本没有的节点**：

```
soc/rgmii@15900000   compatible = "zte,zx279133-rgmii"   (status = "disabled")
```

`/proc/pon/outer_phy` 说 `xmac0: phy_RTL8226` —— 厂商的 xmac0 挂的是那颗独立 RGMII
MAC，也就是 jack 4。mainline 树里既没有这个节点也没有它的驱动，所以 **jack 4 在
mainline 下不可能通**，这与计划 §"非目标"一致（jack 4 不是门槛）。

厂商 dmesg 里与 MAC 相关的行：

```
[16.070651] xmac0_pcs_base is 0xffffff8014000000
[16.35-16.52] smac_init: smac_pkt_filter = 0x80000001, mac = 0x0 / 0x1 / 0x2 / 0x3
[18.673472] xmac0 is used
[18.697611] mac 1 phy status changed: 1000M full-duplex
[19.249948] g_mii_dev_name[0]  sw
[19.258858] g_mii_dev_name[1]  pon
[19.552293] Init switch module
[19.672259] zy switch all port count:7
[19.676005] switch config 9132 GPON  mode!
```

- `smac_init` 跑了 **4 次，mac = 0,1,2,3** —— NP 里有 4 个 SOPC MAC 通道，对应 9132 的
  四个 UNI 口（厂商的 `sw_port_change_to_smac_index(p) = p`）；
- 厂商 MII 设备表只有两项：`sw`（9132 的 CPU 口）和 `pon`（PON 侧）；
- `xmac0 is used` 出现在建网口之前，而 `g_mii_dev_name[0] = sw` —— **9132 的 CPU 口
  跑在厂商的 xmac0 上**。

### 2.5 活板网口与 MAC

```
sw    d0:45:8d:84:08:fc     9132 的 CPU 口
pon   00:d0:d0:61:62:00     PON 侧
idm0  d0:45:8d:84:08:fe
idm1  d0:45:8d:84:08:fd
eth0  d0:45:8d:84:08:ff     = RGMII MAC + RTL8226 = jack 4
eth1  d0:45:8d:84:09:00     在 br-lan，承载着 PC 的全部流量 = 9132 的 UNI 口
eth2  d0:45:8d:84:09:01     rx=0
eth3  d0:45:8d:84:09:02     down
```

厂商靠 `netdriver.ko` 给每个 UNI 口一个**虚拟 MAC**（基址递增），并在 9132 的 FDB 里
把这些虚拟 MAC 绑到对应口，从而"按目的 MAC 选出口、按入口 port id 分发 RX"。
br-lan 里只有 `eth1` —— 即本机 PC 所在的那一口。

---

## 3. mainline 树里已有的东西

从 `cnjn/linux-mainline-zte-zxslc-sr1010`（SHA `96e5a241…`，CI 用的同一个树）读出：

### 3.1 两个 MAC 窗口

`drivers/net/ethernet/zte/zx279133.h`：

```c
/* XMAC0 carries the LAN; the RTL8372N driver owns it. */
#define ZX279133_XMAC0_BASE	0x140000
#define ZX279133_XMAC1_BASE	0x180000
```

`drivers/net/ethernet/zte/zx279133-mac.c` 只用 `ZX279133_XMAC1_BASE`，即 **NPPT 自己
的 MAC**。

### 3.2 LAN conduit

`drivers/net/ethernet/zte/zx279133-lan-conduit.c`（Kconfig 里的
`ZTE_ZX279133_LAN_CONDUIT`，`bool ... default y`，编进内核）：

> Build the XMAC0 LAN conduit netdev as a **child of the ZX279133 NPPT controller**.
> The conduit is used as a DSA CPU-facing netdev. Shared DMA and datapath ownership
> remains in the parent driver.

它在 `&nppt` 节点下注册一个名为 `lan-cpu%d` 的网口，所有收发动作都转发给父驱动的
服务接口。

### 3.3 父子服务接口

`drivers/net/ethernet/zte/zx279133-lan.h` 定义了 `zx279133_lan_service`，父驱动
（NPPT）实现、子驱动（交换机）调用：

```
nppt_read / nppt_write        父驱动独占的 MMIO 映射
xmac_lock / xmac_unlock
datapath_get / set_ready / quiesce / put
set_dsa_active
netdev_setup / teardown / open / stop / xmit / tx_timeout
netdev_get_stats64 / change_mtu / setup_tc
port_wan_set(vid, wan)        把某个 transport VID 的 LAN 口切成 WAN 型
```

### 3.4 RTL8372N 驱动就是模板

`drivers/net/ethernet/zte/zx279133-rtl8372n.c`（229 KB，Kconfig 里的
`ZTE_ZX279133_RTL8372N`）的 probe 顺序：

1. `dev_get_drvdata(dev->parent)` 取父服务，`zx279133_lan_service_valid()` 校验；
2. `zx279133_lan_service_datapath_get()` 抢占共享数据面；
3. `zte,mdio-handle` 取交换机自己的 MDIO 器件；
4. `devm_of_phy_get(dev, parent_np, "lan-serdes")` 从**父节点**取 Uni-SerDes PHY，
   `phy_set_mode_ext(..., PHY_INTERFACE_MODE_USXGMII)` + `phy_power_on()`；
5. `of_parse_phandle(parent_np, "zte,lan-pcs-handle", 0)` 取 XPCS0，`pm_runtime_resume_and_get()`；
6. `zx279133_lan_xpcs_configure()` → `zx279133_lan_xmac_configure()` →
   `zx279133_lan_datapath_enable()`；
7. 配置交换机（VLAN/PVID/隔离），注册 DSA。

**它占用的正是 XMAC0 + XPCS0 + uni_serdes。** SR1010 上这是 RTL8372N 的 CPU 口；
SK-D840N 上这就是 9132 的 CPU 口。

---

## 4. 由此得出的差距

### 4.1 差距一：mainline 没有一条到 9132 的数据通路

`&nppt` 现在被 overlay 指向 `phys = <&uni_serdes>` / `pcs-handle = <&xpcs0>`，
但 `zx279133-mac.c` 写的是 **XMAC1** 的寄存器。XMAC0（lane 真正那一侧的 MAC）
从来没有被 mainline 编程过 —— 这就是历次试跑"lane 上一字节都不动"的原因，
而且和 PHY 有没有接上无关。

厂商侧对照：U-Boot 在这个 jack 上跑的是 `mode_eth_1gbase_x_cfg` +
`uni serdes init succeed` + `xmac 0 speed set 2 in sgmii`，**用的是 xmac0**。

### 4.2 差距二：9132 的 UNI 口没有 MAC 通道

NP 里的 4 个 SOPC MAC 通道（厂商 `smac_init` 的 mac=0..3）mainline 一个都没用。
mainline 的 NPPT 只有一个网口，挂在 XMAC1 上。

### 4.3 差距三：9132 芯片本身没有被配置

G1 的 14 条差集说的就是这件事。本轮已按计划实现其中的"端口属性字 + PVID"（G2a），
并补上了厂商真正用来"使能端口"的那一笔（PHY 的 BMCR，G2b）。

### 4.4 差距四（计划与树的冲突）：per-port netdev

计划 G2 判据要求 `/sys/class/net` 出现 **eth1/eth2/eth3**，即每个 UNI 口一个网口。

但 mainline 的父服务接口只支持**一个** conduit 网口：`zx279133_lan_netdev_setup()`
把 `eth->lan_ndev = ndev`（单个）。SR1010 上多出来的 4 个用户口是 **DSA** 建的
（`DSA_TAG_PROTO_ZX279133_RTL8372N` 标签器负责在 conduit 上插/拔 transport VID）。

所以在"不做 DSA"的前提下，要得到 per-port 网口，**必须给父驱动加一个 per-port 的
服务接口**（父驱动按 vport 分发 RX，每个 vport 一个网口）。这不是小改动。

**建议（自主决断）：先按 DSA 路线做。** 理由：

- 树里已有可复用的标签器与 conduit，per-port 网口、per-port 隔离、per-port VLAN
  全部是现成机制；
- 9132 侧只需要把"transport VID → 口"这件事做对，这与厂商的 FDB 虚拟 MAC 方案
  是两套机制，而 VID 方案能直接复用 mainline 已经写好的 PPU 侧逻辑
  （`zx279133_lan_vport_mf[]`、`zx279133_lan_vport_mode_write()`、
  `zx279133_lan_vport_wan_set()`，VID 59..62）；
- 不做 DSA 就要改父驱动的服务接口，改动面更大且没有现成参照。

计划里"不做 DSA/switchdev"这条约束的由来，是在还不知道树里已有 LAN conduit 与
lan_service 的前提下写的；本文件把它改判为"用 DSA，但不新写 tagger"。

---

## 5. 驱动设计（W2 落地形态）

### 5.1 文件

| 文件 | 作用 | 状态 |
|---|---|---|
| `kernel-overlays/drivers/net/ethernet/zte/zx279133-switch.c` | 9132 交换机子驱动 | 待写 |
| `kernel-overlays/drivers/net/ethernet/zte/Makefile` | 树侧 Makefile **整份**覆盖 + 新文件 | 待写 |
| `kernel-overlays/drivers/net/ethernet/zte/Kconfig` | 树侧 Kconfig 整份覆盖 + 新条目 | 待写 |
| `kernel-overlays/arch/arm64/boot/dts/zte/zx279133-sk-d840n.dts` | 9132 节点 + conduit 子节点 | 待写 |

新增 `.c` 必须自带 Makefile overlay：overlay 是 `cp -a kernel-overlays/. $KT/`，
树侧 `drivers/net/ethernet/zte/Makefile` 会被整份覆盖，所以覆盖件必须包含树侧全部
内容（内容已存在 `upstream-read/kernel/drivers_net_ethernet_zte_Makefile`）。

### 5.2 设备树

```dts
&nppt {
	/* 保留 NPPT 自己的 MAC 走 PON 侧：pcs-handle = <&xpcs1>、phys = <&pon_serdes> */
	lan_cpu: ethernet-lan-cpu {
		compatible = "zte,zx279133-lan-conduit";
	};

	switch {
		compatible = "zte,zx279133-9132";
		zte,mdio-handle = <&mdio0>;   /* 9132 内部 PHY 在这条总线上 */

		ports {
			#address-cells = <1>;
			#size-cells = <0>;

			port@0 {
				reg = <0>;
				ethernet = <&lan_cpu>;
				phy-mode = "sgmii";
			};
			port@1 { reg = <1>; label = "lan1"; phy-mode = "internal"; };
			port@2 { reg = <2>; label = "lan2"; phy-mode = "internal"; };
			port@3 { reg = <3>; label = "lan3"; phy-mode = "internal"; };
			port@4 { reg = <4>; label = "lan4"; phy-mode = "internal"; };
		};
	};
};
```

注意 `&nppt` 的 `pcs-handle` 必须从 xpcs0 交还给 xpcs1：xpcs0 是 XMAC0（LAN 通道）
的 PCS，xpcs1 才是 NPPT 自己（XMAC1）的 PCS。SR1010 的 dtsi 就是这么分的
（`pcs-handle = <&xpcs1>` / `zte,lan-pcs-handle = <&xpcs0>`）。

### 5.3 驱动要做的四件事

1. **拿到资源**：父服务、`lan-serdes` PHY、`zte,lan-pcs-handle`（XPCS0）、
   `zte,mdio-handle`（mdio0）。
2. **把 XMAC0 拉起来**：照 `zx279133_lan_xmac_configure()` +
   `zx279133_lan_datapath_enable()` 移植，但换成 1G SGMII/1000BASE-X
   （厂商：`xmac 0 speed set 2 in sgmii(xmac speed 3)`；RTL8372N 那套是 10G USXGMII）。
   mainline 侧现成的 SGMII 代码在 `zx279133-mac.c::zx279133_xmac_sgmii_init()`，
   差别只是 XMAC 基址换成 `ZX279133_XMAC0_BASE`。
   **未决**：mainline 的 `phy-zx279133-uni-serdes.c` 只接受 USXGMII/10GBASE-R，
   对 SGMII 返回 `-EOPNOTSUPP`（overlay 的 DTS 注释已记下这条），
   所以 1000BASE-X 档要么给这个 PHY 驱动补一档，要么像厂商那样完全绕开 phylink、
   自己把 PCS 写成固定 1G。后者更接近厂商实际做法。
3. **把 9132 芯片配好**：G2a 的属性字 + PVID（已在 np.c），G2b 的 PHY 使能（已在 np.c），
   再加上 per-port 的隔离矩阵与 VID→口映射。
4. **建 per-port 网口**：DSA 路线下由 tagger 承担。

### 5.4 9132 侧的 VID→口映射（未决，需要逆向）

这是 G2d 的关键未知量。厂商侧可查的东西：

- `tm_vlan_check_ena_set(port, mode, enable)` 的真实实现与 mode 0/1 的含义；
- `tm_port_vlan_filter_set()` 里 `pp_vlTagMsk` 的编码方式；
- `port_pvid_table_set()` 落到 ERAM 表 `0x25`；
- `output_vlan_check_en_table_set()` 落到 ERAM 表 `0x23`。

活板侧已知：厂商 init 对 p=0..7 都调了 `tm_vlan_check_ena_set(p,0,1)` 与
`(p,1,1)`，而活板读回来的 ingress 帧模式却是 0 —— 所以"厂商开了 VLAN 检查"和
"活板跑成扁平桥"并不矛盾，VLAN 检查的语义需要从代码里解出来。

---

## 6. 本轮（run 39460558858）实际要测的东西

这一轮不写新驱动，只把两个可测量的写加到 `zx279133-np.c`，用一次构建把两个未知量变成
已知量：

1. **SPA 通路是否真的通到芯片** —— 写完属性字与两个 PVID 字之后立刻读回来并打印。
   读回值与写入值一致 ⇒ 通路正常；读回 0 或全 1 ⇒ 写根本没到芯片，后面所有端口配置
   都不用再试。
2. **mainline 的 MDIO 能不能摸到 9132 的内部 PHY** —— 对 mdio0 的 0x0a..0x0d 读
   PHYSID1/2，清 BMCR 的 POWERDOWN，置 ANENABLE|ENABLE，再读 LMSR 的链路位。
   打印每口的 PHY ID、BMCR、link。链路位为 1 ⇒ 哪个物理口插着 PC 一眼可见
   （厂商侧对应关系：PC 插的是 sw1 = chip port 2）。

预期 dmesg 形态：

```
zx279133-np 19000000.nppt: 9132 PPU 1 attribute word xxxxxxxx (VID 59)
zx279133-np 19000000.nppt: 9132 PPU 1 PVID word 1 xxxxxxxx
zx279133-np 19000000.nppt: 9132 PPU 1 PVID word 2 xxxxxxxx
... 端口 2..4 同 ...
zx279133-np 19000000.nppt: 9132 UNI ports 1..4 up (transport VIDs 59..62)
zx279133-np 19000000.nppt: 9132 UNI PHY 0a: id xxxx:xxxx, BMCR xxxx, link 1
```

## 7. 未决项

| # | 问题 | 需要什么 |
|---|---|---|
| 1 | 9132 能不能按 VID 把 CPU 口来的帧送到指定 UNI 口并在出口脱标 | 逆向 `tm_vlan_check_ena_set` / `tm_port_vlan_filter_set` / ERAM 表 0x23、0x25 |
| 2 | 9132 的 per-port 隔离矩阵怎么写（D13） | 逆向 `port_set_port_isolate(p,q,x)` |
| 3 | 厂商给每个 UNI 口用的虚拟 MAC 是怎么写进 FDB 的 | 逆向 `netdriver.ko` 的 FDB 写路径（VID 方案的替代品） |
| 4 | `uni-phy` 里的 0x1c、0x02 是什么 | 活板继续查；或读厂商 `uni_phy` 符号 |
| 5 | XMAC0 的 1G SGMII 档要不要给 `phy-zx279133-uni-serdes.c` 补一档 | 照厂商 U-Boot 的 `mode_eth_1gbase_x_cfg` 寄存器组做 |
| 6 | 厂商的 `xmac0`（0x140000）与 RGMII MAC（0x15900000）的关系 | 本文件只用到"9132 的 CPU 口 = 0x140000 那一侧"，已被 `g_mii_dev_name[0] = sw` + `xmac0 is used` 交叉支持 |
