# 补一百一十：9132 的 DSA 端口、CI 到底编译哪棵树、Uni SerDes 的 1G 档

写于 2026-10-21。设备：SK-D840N（ZX279133，GPON ONU）。读者：接着做 G2/G3 的会话。

本文件是 G2 门（W2：9132 最小驱动）的续篇，补三件在写 DSA 端口之前必须先弄清楚的事，
外加 9132 的 per-port netdev 方案本身。全部结论来自本地取证与离线读源码，**没有在本机编译、没有写 flash**。

---

## 0. 三件事的结论

| 问题 | 结论 |
|---|---|
| CI 编译的是哪棵树？ | **不是** `KERNEL_SHA`（96e5a241）指的那棵，而是它的子模块 `linux-6.18.38` = **bf45d1ed**。两棵树只差 `arch/arm64/boot/dts/zte/zx279133-sr1010.dts` 的 17 行，本轮所有 overlay 都对得上。 |
| Uni SerDes 支持 1G 吗？ | **树里的驱动不支持**（只接 USXGMII/10GBASE-R），**但我们自己的 overlay 早就补上了 1G 档**，而且板上实测 **0/49 词差异**。docs/16 的未决项 #5 由此闭合。 |
| 9132 能做 per-port netdev 吗？ | 能，**用树里已经写好的 transport-VID 标签器**：DSA 口 4..7 ↔ 传输 VID 59..62，和我们写进 9132 UNI 口的 PVID 完全一致。方案见 §3。 |

---

## 1. CI 编译的树：`bf45d1ed`，不是 `96e5a241`

### 1.1 怎么确定的

`build-sk-d840n.yml` 的 "Clone sources" 步骤：

```
git clone --branch "$KERNEL_BRANCH" --recurse-submodules "$KERNEL_URL" linux-mainline-zte-zxslc-sr1010
git -C linux-mainline-zte-zxslc-sr1010 checkout --detach "$KERNEL_SHA"
git -C linux-mainline-zte-zxslc-sr1010 submodule update --init --recursive
test -f linux-mainline-zte-zxslc-sr1010/linux-$KVER/arch/arm64/boot/dts/zte/zx279133.dtsi
KT=linux-mainline-zte-zxslc-sr1010/linux-$KVER
```

`KERNEL_URL` 和 `.gitmodules` 里的 `linux-6.18.38` 是**同一个仓库**：

```
[submodule "linux-6.18.38"]
	path = linux-6.18.38
	url = https://github.com/cnjn/linux-mainline-zte-zxslc-sr1010
	branch = codex/sr1010-mainline
```

本地把 fork 拉下来（blobless）读指针：

```
$ git ls-tree HEAD                       # 分支 build
160000 commit bf45d1ed0b849a72a8c2948096bd70d8164f8418	linux-6.18.38
```

`checkout --detach 96e5a241` 把 superproject 的工作树换成 96e5a241 那个 commit——**而 96e5a241
本身是一棵内核树的根**（43 个顶层目录：arch/drivers/include/…，没有 `linux-6.18.38`）：

```
$ git ls-tree FETCH_HEAD                 # 96e5a241
040000 tree 8d480cd2670cc47bdd7c43e398e3473076226f5	drivers
040000 tree 7a0d12aac3ac8043a3f78b9e9a1cd35fdaaa666	tools
...                                      （内核树，没有 linux-6.18.38）
```

`git checkout` 删不掉 `linux-6.18.38/`（子模块工作树非空，git 只会警告 "unable to rmdir"），
所以 `KT` 仍然是子模块的内容。**结论：CI 编译的是 `bf45d1ed`。**

### 1.2 两棵树差多少

```
$ git diff --stat 96e5a241 bf45d1ed
 arch/arm64/boot/dts/zte/zx279133-sr1010.dts | 17 +++++++++--------
 1 file changed, 9 insertions(+), 8 deletions(-)
```

只差一个 SR1010 的板级 DTS——**和 SK-D840N 无关**。本仓库的 overlay 集合（六个驱动文件 +
`zx279133.h`）逐字节对得上，CI 的"KERNEL_SHA 钉死"注释里说的那件事仍然成立。

### 1.3 由此澄清的一件事：`drivers/phy/` 在 fork 里，不在 `drivers/net/phy/`

`bf45d1ed` 的树里有 **fork 自建的 `drivers/phy/`**：

```
drivers/phy/Makefile
drivers/phy/Kconfig
drivers/phy/zte/Kconfig
drivers/phy/zte/Makefile
drivers/phy/zte/phy-zx279133-pon-serdes.c
drivers/phy/zte/phy-zx279133-uni-serdes.c
```

而上游的 `drivers/net/phy/Makefile` 里**一条 zx279133 都没有**（只有 `zx279051.o`），
`drivers/net/phy/Kconfig` 只 source aquantia/mediatek/qcom/realtek。

> 这就是上一份交接里"Uni SerDes 驱动源码路径至今没找到"的原因：路径是
> `drivers/phy/zte/phy-zx279133-uni-serdes.c`，不是 `drivers/net/phy/zte/...`。
> 用 GitHub tree API（`git/trees/<sha>?recursive=1`）一次就能列全，`drivers/phy/` 就在里面。

---

## 2. Uni SerDes 的 1G 档：树里没有，我们自己有，而且对上了

### 2.1 树里的驱动只接 USXGMII / 10GBASE-R

`drivers/phy/zte/phy-zx279133-uni-serdes.c`（bf45d1ed，7605 字节）：

```c
static int zx279133_uni_serdes_set_mode(struct phy *phy, enum phy_mode mode,
					int submode)
{
	if (mode != PHY_MODE_ETHERNET)
		return -EOPNOTSUPP;

	if (submode != PHY_INTERFACE_MODE_USXGMII &&
	    submode != PHY_INTERFACE_MODE_10GBASER)
		return -EOPNOTSUPP;

	return 0;
}
```

只有一份 profile：`zx279133_uni_10g_profile[]`（厂商 `uni_mode_eth_10gbase_r_cfg()` 的 49 个词）。
SGMII → `-EOPNOTSUPP`。

### 2.2 但我们自己那份 overlay 早就补了 1G 档

`kernel-overlays/drivers/phy/zte/phy-zx279133-uni-serdes.c` 是本仓库自己的文件，它：

* 接受 `PHY_INTERFACE_MODE_SGMII`；
* 带一份**从板子上量出来的** 49 词 1G profile（不是从 `np_133.ko` 里抄的——那份抄来的是
  **PON** SerDes 的镜像，21/49 对得上）；
* SGMII 档**不重置、不写 profile、不写速率寄存器**，只确保 enable 位置上，然后**把现状读回来报告**。

注释里记着这条经验，写得很清楚，值得原样留着：

> writing zx279133_uni_10g_profile (which is the *PON* SerDes' image, 21/49 against the
> board's own file) linked the PHY but returned not one frame; writing the measured file
> after a reset pulse did not even link the PHY.

### 2.3 板上实测：0/49

串口日志里最近一次主线启动（`staging/ctl/serial.log` 第 523539 行起）：

```
[    0.000000] Linux version 6.18.38+ (runner@runnervmk1d70) ...
[    3.622294] zx279133-uni-serdes 16100000.phy: Uni SerDes 1G inherited: mode 0x1 already, image differs in 0/49 word(s), status 0x55000400/0x100019fc
[    3.634460] zx279133-eth 19000000.ethernet eth0: configuring for fixed/sgmii link mode
[    3.686184] zx279133-eth 19000000.ethernet eth0: Link is Up - 1Gbps/Full - flow control off
```

三件事一次说清：

1. 我们那份 1G profile 与板上现状**逐字节相同**（0/49 词不同）——测量的那份就是对的；
2. 板子交给内核时 Uni SerDes 已经在 1G 档（mode 0x1、enable 已置），驱动**继承**它；
3. 同一轮里 NPPT 自己的 eth0（fixed/sgmii）链路是 Up 的，说明 XPCS1/PON 那一侧照旧工作。

> **docs/16 未决项 #5（"XMAC0 的 1G SGMII 档要不要给 uni-serdes 补一档"）由此闭合：早就补了，
> 而且板上已经证明补对了。**

---

## 3. 9132 的 per-port netdev：DSA + 树里现成的 transport-VID 标签器

### 3.1 树里的标签器就是这个方案

`net/dsa/tag_zx279133_rtl8372n.c`（bf45d1ed）：

```c
static struct sk_buff *
zx279133_rtl8372n_xmit(struct sk_buff *skb, struct net_device *netdev)
{
	struct dsa_port *dp = dsa_user_to_port(netdev);
	__be16 tag[VLAN_HLEN / sizeof(__be16)] = {
		htons(ETH_P_8021Q),
		htons(ZX279133_RTL8372N_TRANSPORT_VID_BASE + dp->index),
	};
	...
}
```

`include/net/dsa_zx279133_rtl8372n.h`：

```c
#define ZX279133_RTL8372N_USER_PORT_MIN	4
#define ZX279133_RTL8372N_USER_PORT_MAX	7
#define ZX279133_RTL8372N_TRANSPORT_VID_BASE	55
```

收侧按 `vid - 55` 反查口号，`dsa_conduit_find_user(netdev, 0, port)` 找到对应用户口。

### 3.2 为什么这套正好能用在这块板上

| | SR1010（RTL8372N） | SK-D840N（9132） |
|---|---|---|
| 用户口 | DSA 4..7 | **同样取 4..7** |
| 传输 VID | 55 + 4..7 = **59..62** | **同样 59..62** |
| 口上的 PVID（untag SVID） | RTL8372N 的 service-VLAN 表 | 我们已经写进 9132 的 **PPU 1..4**：PVID = `ZX279133_LAN_TRANSPORT_VID_MIN + (ppu-1)` = 59..62（`zx279133_9132_port_init()`） |

**同一个标签器、同一组 VID、同一套口号。** DSA 口 N+3 就是 9132 的 PPU 口 N。

不需要新写标签器，也不需要改 `include/net/dsa.h`（协议号 30 已经在树里），
只需要在 DTS 里把 9132 描述成 4 个用户口 + 1 个 CPU 口。

### 3.3 这棵树的 DSA 是旧式 API，但 user.c 仍然给用户口建 phylink

`include/net/dsa.h`（bf45d1ed）里是旧式的一对：

```c
	int	(*port_enable)(struct dsa_switch *ds, int port, struct phy_device *phy);
	void	(*port_disable)(struct dsa_switch *ds, int port);
```

（**没有** `port_enable_set` / `port_set_state`。）而 `net/dsa/user.c` 里
`dsa_user_phy_setup()` 会给用户口建 phylink，并且：

```c
	if (ds->ops->phylink_fixed_state) {
		dp->pl_config.get_fixed_state = dsa_user_phylink_fixed_state;
		dp->pl_config.poll_fixed_state = true;
	}
```

链路状态由驱动通过 `phylink_fixed_state()` 回答，phylink 会**轮询**它。

关键前提在 `drivers/net/phy/phylink.c`：

```c
int phylink_fwnode_phy_connect(struct phylink *pl,
			       const struct fwnode_handle *fwnode, u32 flags)
{
	/* Fixed links and 802.3z are handled without needing a PHY */
	if (pl->cfg_link_an_mode == MLO_AN_FIXED || ...)
		return 0;
	...
}
```

`phy-mode = "internal"` → `cfg_link_an_mode = MLO_AN_FIXED` → **不需要 PHY 设备就能建 phylink**，
`phylink_of_phy_connect()` 返回 0，`dsa_user_phy_setup()` 不会失败。

所以本板的做法是：**不把 9132 的内部 PHY 声明成 DT 节点**（避免 phylib 的 `phy_probe()`
去 soft-reset 它们——正是 RTL8226 那次踩过的坑），而是在驱动里用
`mdiobus_read(bus, 0x0a + n, MII_BMSR)` 直接读链路位（BMSR 的链路位是锁存的，要读两次），
再通过 `phylink_fixed_state()` 报给 phylink。

### 3.4 DSA 口与 9132 PPU 口的对应

| DSA 口 | PPU 口 | 9132 内部 PHY（MDIO0） | 传输 VID |
|---|---|---|---|
| 4 | 1 | 0x0a | 59 |
| 5 | 2 | 0x0b | 60 |
| 6 | 3 | 0x0c | 61 |
| 7 | 4 | 0x0d | 62 |

docs/15 §8 的实测说**测试 PC 插在 switch port 1**，即 PPU 2 / PHY 0x0b / VID 60。

### 3.5 本轮实现里刻意不做的事

* **不写 9132 的 VLAN 表。** 端口有的是 PVID；CPU 口发帧时带上传输 VID，标签器收侧再剥掉。
  桥自己的 VLAN 是 Linux 侧的事。
* **不在口关闭时给 PHY 断电。** 厂商是关口就断电，但 mainline 的 DSA 口生命周期不是厂商的，
  被一次误关断电的 PHY 是再也回不来的口。
* **不写 SPA 地址 0 的端口使能位。** docs/15 把它列为差集第一条，但 PPU 内存的地址 0
  同时是 `zx279133_lan_port_bringup()` 写的**入口 PC 寄存器**，在那里写 bit 0 会破坏入口程序。
  本轮只**读回**并打印。

---

## 4. 本轮推的三笔（run87 / run88）

| 提交 | 内容 |
|---|---|
| `4953852` | 只读取证：9132 的端口使能字（PPU 0..5）、BMCR 写后回读、协商 1 s 后的链路复读；试验脚本把 9132/Uni SerDes/XPCS0 那几行一次性导进 kmsg；02_network 改成 lan1..lan4 → lan-cpu0 → eth0 三级回落。 |
| `29717b4` | 9132 的 DSA 驱动：4 个用户口 + CPU 口、端口链路状态、桥 join/leave 交给 NPPT 的 vport；DTS 加 `ports` 子节点；Kconfig `select NET_DSA_TAG_ZX279133_RTL8372N`。 |

DSA 注册**故意放在最后、故意不致命**：lane 和它说的每一句话都已经在日志里了，
DSA 注册不上不会把这一轮的答案一起带走。

---

## 5. 下一轮的判据

拿到 run85/86/87/88 的件之后，U-Boot RAM 起内核，一轮里要拿到这些：

1. `9132 PPU 1..4 attribute word …` / `PVID word 1/2` —— **端口属性字和 PVID 写得进去吗**；
2. `9132 UNI PHY 0a..0d: id … BMCR …->… link …` 与 `link after settle` —— **四个内部 PHY 认不认得出、
   写进去没有、哪一个真的链上了**（0x0b 应当是 1，因为 PC 在那）；
3. `9132 PPU 0..5 enable word …` —— **厂商留下的端口使能字长什么样**（只读）；
4. `XMAC0 datapath enabled; XPCS0 status=… link=… xmac_link=…` —— **XPCS0 到底链没链**；
5. `registered the 9132 as a DSA switch` 与 `lan1..lan4` 是否出现 —— **DSA 能不能注册**；
6. PC 插在 UNI 口时能不能 ping 通 192.168.9.1 —— **这条链路到底通不通**。

第 6 条是 G2 的真正判据；第 1..5 条是它的解释材料。
