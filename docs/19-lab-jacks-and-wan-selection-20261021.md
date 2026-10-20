# 补百十九：实验室口的两端都测过了——板上只有一根线，面板顺序仍未成立

日期：2026-10-21，本地 05:30–06:20（厂商 4.19 活板，PC 侧 ssh 采样）。设备：SK-D840N（ZX279133 GPON ONU）。
本文件是 M1（W3/G3）的物理前提与"哪只口当 WAN"的定稿依据。

---

## 0. 结论先说

1. **M1 的两个 WAN 候选口都不成立**：厂商系统里 `wan`=eth2 跑了 udhcpc 一个多小时，
   `tx` 一直涨、`rx` 恒 0，**一个租约都没有**；`eth0`（外挂 RTL8226，jack 4）同样
   `tx=220 / rx=0`。厂商自己在同一只口上也拿不到租约 ⇒ 这不是 mainline 的问题。
2. **板上现在只有一根网线**：PC（192.168.9.25）↔ `eth1`。`eth3` 是 DOWN（明确没插线），
   `eth1` 有来有回。`eth2` 的 carrier=1 是**假 carrier**（见 §2），所以"插了线但对端不应答"
   与"没插线"这两种情况在厂商侧分不开 —— 这也是为什么必须换到 mainline 的 PHY 链路位来判断。
3. **面板顺序仍未成立**：四个 RJ45 里哪个是 UNI1/UNI2/UNI3/UNI4，除了"PC 在 UNI2"
   之外没有证据。厂商的 `sw_port` 表是逻辑端口号，不是面板位置。
4. ⇒ **因此 M1 的物理前提必须由机主补一根线**，插到**除 PC 那只以外的任一只 RJ45**；
   板子侧不去猜是哪一只：**WAN 口由板子自己认**（`trial-files/etc/rc.d/S26nat` 的搜索逻辑，
   见 §4）。机主不需要知道面板顺序。

---

## 1. 05:30 的活板实测（`tools/boardev.sh` 的等价采样）

```
uptime: 12:05:25 up 10 min        （板子时钟偏了约 7h，PC 侧 05:30 时板子刚起 10 分钟）
eth0  carrier=1  rx=0        tx=220      <- 外挂 RTL8226（jack 4）
eth1  carrier=1  rx=101142   tx=98702    <- PPU 2 = UNI2 = PHY 0x0b = PC 的口，在 br-lan 里
eth2  carrier=1  rx=0        tx=69646    <- PPU 3 = UNI3 = PHY 0x0c；udhcpc 在上面重试
eth3  carrier=  rx=0        tx=0        <- PPU 4 = UNI4 = PHY 0x0d；DOWN，明确没插线
sw    carrier=1  rx=125616   tx=172688   <- 9132 的 CPU 口
br-lan                      rx=99912    tx=101726
/proc/net/arp:  只有 192.168.9.25 → 84:47:09:5b:bd:2c（PC）
brctl show:    br-lan 只含 eth1
```

`ps w` 里 `udhcpc -p /var/run/udhcpc-eth2.pid -s /lib/netifd/dhcp.script -f -t 0 -i eth2 ...`
一直在跑（`-t 0` = 无限重试），这解释了 eth2 的 tx 一直涨。

**eth2 的 tx 增速 ≈ 2.2–2.4 kB / 22 s**（`staging/ctl/jackmap.log` 连续样本），与 DHCP
重试的包长一致；`rx` 恒 0 ⇒ 对端一个字节都没回。

## 2. 厂商的 carrier 在这只板上不可信

`eth0` 与 `eth2` 都报 `carrier=1`，而 `eth3`（明确没插线）报 DOWN。`docs/13` §0 已经记过
同一件事：RTL8226 那一侧 `sky_outerphy_state` 恒 `1 9 1`，没有对端也报 1。

⇒ **`carrier` 不能作为"这只口插了线"的判据。** 判据只能有两条：
  (a) **对端真的会答**（DHCP 有租约 / 有回包），或者
  (b) **PHY 的链路位**（mainline 侧实测：run86 读 PHY 0x0a..0x0d 的 id 是 84b9:5032，
      其中 **0x0b link=1** = PC 的口，其余为 0）。厂商的 PHY 驱动把链路位藏起来了，
      mainline 的 phylink 不会。

## 3. DSA 端口号 ↔ 物理口 的对应（实测，不是推测）

| DSA port | label | PPU | PHY | 厂商叫法 | 本 bench |
|---|---|---|---|---|---|
| 4 | lan1 | 1 | 0x0a | smac_0 | —— |
| 5 | lan2 | 2 | 0x0b | smac_1 = 厂商 `eth1` | **PC 在这里** |
| 6 | lan3 | 3 | 0x0c | smac_2 = 厂商 `eth2` | 候选 WAN（厂商 udhcpc 在这） |
| 7 | lan4 | 4 | 0x0d | smac_3 = 厂商 `eth3` | DOWN（没插线） |

厂商侧佐证：厂商给 PC 那个口打印 `[eth1] port 1 link up:1000M`，它的 switch port 1 = smac_1
= PPU 2；厂商 `eth2` = switch port 2 = smac_2 = PPU 3。厂商 `eth0` 不是 9132 的口
（switch port 4 = 外挂 RTL8226），mainline 里它不在 DSA 端口表内。

**面板位置与这张表的关系：未知。** 本文件不假装知道。

## 4. 板子侧怎么自己认出 WAN（run94 起）

`trial-files/etc/rc.d/S26nat`（S26nat，排在 S20network / S21zrec 之后）：

1. **找出 PC 在哪只口**：桥的转发表里唯一学到的地址就是 PC 的 MAC，
   `brctl showmacs br-lan` 的第三列就是学到它的端口。读不到就用本 bench 的实测值 `lan2`。
   这只口永远不会被当成 WAN。
2. **先试默认 WAN `lan3`**：它本来就不在桥里（`02_network` 把 lan1/lan2/lan4 放进 br-lan，
   lan3 当 `wan`），所以从它发出的 DHCP 请求不会被我们自己的 dnsmasq 抢答。
   镜像自己的 netifd 已经在 lan3 上跑 udhcpc，脚本等它 24 s 看有没有地址。
3. **再试其余空口**（lan1、lan4，最后才是 lan2）：一只一只试，
   **每次把该口拿出桥**再发 DHCP——这同时让 DSA 的 bridge_leave 回调生效，
   把这只口在 NPPT 里变成 WAN 型；没人应答就放回桥里。
   端口从桥里出来的那一刻，**NPPT 的端口类型也会跟着变**（见 §5 的开关）。
4. **应答的那只口成为 WAN**：同时改 `network.wan.device` 和桥的成员表，
   `/etc/init.d/network reload`，之后租约由镜像自己的 netifd 持有。
5. **没人应答时**（本 bench 现在的状态）：默认 WAN 留着，另起一个 watcher 每 60 s 搜一次，
   所以**机主后插的线会被板子自己认出来**，不用碰板子。

## 5. NPPT 的"LAN 型 / WAN 型"是这个设计里唯一没被测量过的东西

`zx279133-np.c` 的注释写明了 WAN 型的含义：**WAN 型口的 PPPoE 头在入向被剥、在出向被加**，
LAN 型口用普通的以太网程序（PC 561）。树里的 RTL8372N 驱动也按"离开桥 = WAN 口"这条规则
调 `port_wan_set()`，但 **SR1010 上没有任何 DSA 口会离开桥**，所以那条规则在树里只有"进桥"
这一半可能被触发。

本板有一只口要当 WAN，而 **实验室上行是普通 DHCP，不是 PPPoE** ⇒ 按树里的规则把该口翻成
WAN 型，反而可能给上行套上 PPPoE 程序。

所以 **run94 起这个行为变成一个运行时开关**（默认关＝不动 NPPT 的端口类型）：

```
/sys/module/zx279133_switch/parameters/wan_type
  0（默认）：不动 NPPT 的端口类型，四个口都保持 NPPT 驱动给它们的普通以太网型
  1：按树的规则，离开桥的口变 WAN 型
```

这样**同一个镜像可以把两种答案都测一遍**，而不是在两个 build 之间争论。

## 6. M1 的物理前提（只有机主能做）

- 板子上**只有一根网线**：PC ↔ UNI2（`eth1`）。
- 要做"一口 WAN 拿 DHCP"，需要**第二根网线**，从上级网络（能发 DHCP 的那台）接到
  **除 PC 那只以外的任一只 RJ45**。插哪只都行，板子自己认（§4）。
- 判据（§4 第 2/3 步）会直接给出答案：哪个口的 PHY 链路位起来了、哪只口拿到了租约。
  这也顺带把**面板顺序**测出来：机主插线前后各看一眼 `/sys/class/net/lanX/carrier`
  （mainline 侧可信，厂商侧不可信）即可。

## 7. CI 记录（这一段为什么重要）

**run92 是第一个真正带上 9132 DSA 驱动的 build。**

- run87 = `4953852`（enable word / BMCR 读回证据），initramfs 的 `.itb` 与 run86 不同 ⇒ 变了。
- run88 = `29717b4`（DSA 四口），**编译失败**：`drivers/net/ethernet/zte/zx279133-switch.o`
  Error 1，原因是本树没有 `struct netdev_tracker`，只有 `netdevice_tracker`。
- run89 = `eb773fd`（9132 每口字转储）：同一处、同一原因。
- run90 = `a85ae80`、run91 = `221a0ac`：同一处、同一原因。
- **run92 = `7973ec6`**：把 tracker 改成树自己的 `netdevice_tracker`，并把 DSA 的 CPU 口
  编号从 0 改成 8、`num_ports` 从 8 改成 9（照抄树里 RTL8372N 的 `port@8` / `num_ports = 9`）。
  **这是第一次编译通过、也第一次会真正出现 lan1..lan4 的 build。**

⇒ 在 run92 出结果之前，任何"四口都有"的结论都是没有的；任何"跑通了"的结论都要问
"哪个 build 的镜像"。
