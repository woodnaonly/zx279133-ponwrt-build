# 92 · 2.5G 那个丝印口是交换机的口，不是 eth0 的口（2026-10-26 晚）

目标（owner 原话）：「板子插在智能开关上面 设定20分钟通电 ，20秒钟断电循环，要求你持续跟进 ，
网线插在lan1 2.5g 口 并调通」。这一篇记录今晚为 LAN1 取得的全部测量，以及它们把哪几条假设否掉了。
智能开关的节奏是 20 min 通电 / 20 s 断电，每一个 U-Boot 窗口都是这么来的。

标注约定：**量到的** = 有原始行；**推出的** = 由量到的推出来；**没定的** = 还缺哪一行。

**读前先知道：本篇标题在 §11 被自己的实验否证了。**「把 2.5G 口声明成交换机的口」不是这条通路的开关，
今晚运行期把 mem 5 配成一个真能用的口的完整配方、也把 U-Boot 留在 mem 6 的 extend 配置利用起来之后，
PC 的 20 帧仍然一个都没到 CPU。剩下唯一活着的嫌疑在 PHY 面向 SoC 那一侧的 SerDes，见 §11。

## 1. 量到的：这条通路在原厂引导程序里今天仍然是双向通的

四次独立窗口，线一直在面板 LAN1：

- 18:01:34（`tools/ublan1.sh`）`ping 192.168.9.25` → `host 192.168.9.25 is alive`，
  `ethact=eth0`，`current idm tx outport is 4`，并且它自己的 RX 环 `0xb0028000` 里全是收进来的帧
  （len 60/74/82/102/130）。
- 19:03:04（`tools/ubdesc.sh`）同一句 `is alive`，然后逐字读出环：
  ```
  b0008000: b0328000 0f800054 00440000 00000000    <- TX，len 0x54>>1 = 42 = 那条 ARP
  b0028000: b00e8000 0785004a c0a80919 c0a80901    <- RX，c0a80919=.25  c0a80901=.1
  ```
- 19:24（`tools/uboutport.sh`）见 §2。
- 20:04:47（`tools/txportsweep.sh` 的前半）引导 RAM kit 之前，U-Boot 的 `tftpboot` 依旧把 12 MB 拉了下来。

物理层也量到了：这台 PC 的有线网卡（ifIndex 19，192.168.9.25）在线插在 LAN1 时报
`LinkSpeed 1 Gbps / MediaConnectionState Connected` —— RTL8226 的铜侧和 PC 协商成功。
**所以坏的不是线、不是 PHY、不是这个口。**

## 2. 量到的：出口端口字段不控制 U-Boot 的出帧

`tools/uboutport.sh` 把 `idm_tx_outport` 依次设成 4、5、6、3、0，每次都 `ping`：

```
port 4 ALIVE (two-way)   PC ReceivedBytes 20168519 -> 20168713   (+194)
port 5 ALIVE (two-way)   20168713 -> 20168907                    (+194)
port 6 ALIVE (two-way)   20168907 -> 20169101                    (+194)
port 3 ALIVE (two-way)   20169101 -> 20169369
port 0 ALIVE (two-way)
```

**五个值全都通，PC 计数在每个值的窗口里都动。** 于是：

- §1 里那条 `0x00440000`（bit22 端口有效 + bits16..21 = 4）不能再读成「4 是这条口的出口编号」——
  把它改成 6 都不影响出帧。原厂引导程序是把这条 lane 当 extend/outerphy 口直接驱动（`xmac0 is used`、
  outerphy init 里 `the phy_id is 0x5`），那个字段多半根本不参与它的转发。
- 顺带证明 PC 这块网卡和这根线**能**计数、也**能**收（+194/次），这是 docs/07 补八十九 要的正对照。

## 3. 量到的：mainline 在这一口上收发都看不见

RAM kit（run 188，`sk-d840n-initramfs.itb`，sha256 `dbbffc6b…`，按 release manifest 核过）20:06:59 起来后：

- `echo 5 > /sys/module/zx279133/parameters/zx279133_tx_port; cat $P` → `5`。**参数是活的**
  （`/sys/module/zx279133/parameters/` 在本板存在，里面本来就有 rx_hash）。
- `ping -c 8 -I eth0 192.168.9.25` → `8 packets transmitted, 0 packets received, 100% packet loss`，
  `eth0/statistics/tx_packets 136`，`rx_packets 0`。
- `grep . /sys/class/net/lan*/statistics/rx_packets` → **lan1..lan4 和 lan-cpu0 全是 0**。
  收方向的账在 `lan-cpu0`（docs/90 之后量到的规矩），它也是 0，说明**从这根线上没有任何帧到达 CPU**。
- PC 侧在板子安静时仍以约 +352 字节/3 s 稳定增长。**这条没有定**：它要么是板子经 9132 泛洪出来的
  （那样的话出方向其实是通的），要么是网段上另一台机器——板子的 ARP 表里有一条
  `192.168.9.174 = 52:18:69:52:58:78`（本地管理位已置，像虚拟网卡），而 PC 的 IPv6 邻居表在
  ifIndex 19 上没有 `d0-45-8d-84-09-00`。下一轮要用 `tcpdump`/镜像口把它分清。

## 4. 一次差点被我当成突破的假阳性，记下来

20:13 PC `ping -S 192.168.9.25 -n 6 192.168.9.1` 报「已接收 = 3」，20:14 `-n 10` 报「已接收 = 5」。
看着像 LAN1 通了。**它不是**：同一时刻 PC 的 IPv4 邻居表里
`192.168.9.1 → 00-00-00-00-00-00 / Incomplete`，也就是这台 PC 从来没解析到板子的 MAC，
因此它没有向板子发过单播 echo request，那 3/5 个「收到」是本机生成的差错回复。
判据要写成**两条**：收到回包 **且** 邻居表里有那块 MAC。只看 ping 的计数会把死口报成活口。

## 5. 否证掉的两条，别再走

- **出口端口号**（§2）：U-Boot 无视它；mainline 改它也没让 PC 收到（§3，且见 §6 的那次无效扫描）。
- **四个 XMAC 端口块没人写**（`tools/txportsweep.sh` 的 `ports()` 想回答的问题）：RAM kit 里读回来是
  `0x19040000: 0x00fa2203 / 0x19040004: 0x00003fff`，`0x190c0000` 同值 —— 和原厂 init loop 的
  `+0x04=0x3fff` 一致。bootm 没把它们清掉，这条不是缺陷。
  另外 `0x1900c0f0`（SMCT_DONE）在 ping 前后是 `0xfc → 0x65`，**它在往下走**，所以它是个指针/水位，
  不是帧总数；docs/07 补八十九 已经修正过一次它的含义，这里再钉一次。

## 6. 我的仪表犯的两个错，都已改

1. `tools/txportsweep.sh` 的 `ping -c 4 192.168.9.25` 没有 `-I eth0`。RAM 镜像把
   `192.168.9.1/25` 给了 br-lan，而脚本给 eth0 加的是 /24 —— **长前缀优先，八个值全都只是在 ping 桥**，
   被测的那个出口压根没被走到。已改成 `ping -c 4 -I eth0 192.168.9.25`。
2. 同一脚本读「编译期默认值」时 `grep` 了标记本身，命中的是中继回显的那行 `echo DEFIS-$DEF`，
   于是打印出 `-DEF-$DEF`。已改成 `grep -oE 'DEFIS-[0-9]+'`。
3. `tools/ubdesc.sh` 在 18:42 那次读了三个环，全 `ffffffff`：**断电之后 DRAM 是空的**，
   描述符必须在同一次开机里先制造流量再读。已在 ping 之后才 dump。
4. `tools/ubdesc.sh` 用 `0xb0008000:` 匹配回复，而 arm64 的 `md` 打的是补零地址、且不带 `0x`，
   18:23 那轮三条都 ABSENT。已按数字前缀匹配，并把 `dump()` 里自作主张加上的冒号去掉。
5. `tools/uboutport.sh` 的 `waitfor()` 给全局 `p` 赋值，把扫描循环的 `p` 冲掉了，
   五行判定全打印成 `port is alive`；发的值是错的（对的），标签不可信。已改成 `wp`。

## 7. 下一步（只有一条，因为它是剩下唯一能定死编号的读法）

`tools/ubxmac.sh`（已修，等 20:2x 的窗口）在 U-Boot 刚 ping 通之后读：
`spa_port_attr_op 0`（芯片自己的 port↔MEMID 表）、四个端口块、`0x19140000`/`0x19180000` 的
TX/RX 控制、`0x161000e4`（Uni SerDes 状态：今晚是 `0x01000706`，bit0 RX LOS = 0，**物理层认为自己有
rx data**）、以及刚用过的那条 TX 描述符。

要定死的问题是：**RTL8226 在 9132 的 SPA 里是几号口**。三条旁证都指向「它是交换机的一个口」——
原厂 `uni-phy` 数组第 5 项是 `0x05`、docs/13 的 `port=4 outerphy_mode=5,phy_RTL8226`、
docs/15 的「RTL8226 = switch 口 4 = 芯片口 5」——而我们的 DSA 节点只声明了 port@4..7（lan1..lan4）
加 port@8（CPU）。**如果它是一个交换机口，那 mainline 的缺陷不是 eth0 的端口号，而是这个口没有被声明**：
没有 port 节点 ⇒ 没有 PVID/转发 ⇒ 芯片不会把它和 CPU 通道互通，于是 Linux 下这个丝印口两头都是黑的，
而 U-Boot 拿它当 extend 口直接驱动，所以能用。

推论（还没做，等 §7 的表落定）：在 `&nppt`/switch 的 `ports` 里给它补一个用户口，按 docs/90 已验证的
transport-VID/PVID 方案同构处理，然后让它进 `br-lan`。真要这么做之前，面板命名要先问 owner：
`lan1` 这个标签在本 SKU 上没有丝印口（docs/90 §6），新增的这个口对应的丝印是 LAN1。

## 8. 20:45 窗口：修好 `-I eth0` 之后重扫 0..7，端口号这条路正式关门

`sk-d840n-initramfs.itb`（run 188，U-Boot 20:45:46 抓到，`bootm` 20:46:08，shell 20:48:02）。
`compiled-default DEFIS-4` 确认这次跑的确实是改了参数的内核，`cat $P` 能读回写入值。

```
port 1  20:48:41  4 packets transmitted, 0 packets received
port 2  20:48:53  4 packets transmitted, 0 packets received
port 3  20:49:05  4 packets transmitted, 0 packets received
port 4  20:49:18  4 packets transmitted, 0 packets received
port 5  20:49:30  4 packets transmitted, 0 packets received
port 6  20:49:42  4 packets transmitted, 0 packets received
port 7  20:49:54  4 packets transmitted, 0 packets received
```

PC 那一半同轮取样（`tools/pcrxwatch.ps1`，2 s 一次，662 个样本）：**rx_delta 中位数 0**。整晚唯一的大突发是
t=+1088..+1099（≈20:45:58-20:46:10）几百 KB —— 那正是 U-Boot 自己 `tftpboot` 这个 kit 的窗口。
把这两件事放在一起：同一块网卡、同一根线，几分钟内先收下了引导程序的下载和 5 次 ping 的 +194，
又在 mainline 的 8 个端口值上一个字节都没收到。

**所以 §2 的否证在 mainline 这边也成立：descriptor 的出口端口不是这个口的病因。** §7 那句「补一个
用户口」因此不是推论而是剩下的唯一形状 —— 只是要先搞清 mainline 到底把这条 lane 的 MAC 配成了什么。

## 9. 新证据：两次开机在 MAC 窗口上是**不一样**的（今晚最关键的一行）

同一块板、同一个丝印口，U-Boot 的 ping 通了之后读（`tools/ubxmac.sh`，20:25 窗口）对 RAM kit 基线（20:48）：

| 窗口 | U-Boot（流是通的） | mainline RAM kit |
|---|---|---|
| `0x19040000` / `0x19080000` | `00ba2200 00003fff 80000001` | `00fa2203 00003fff 80000001` |
| `0x190c0000` / `0x19100000` | 同上 `00ba2200 …` | 同上 `00fa2203 …` |
| `0x190400e0` / `0x190800e0` | `00011000` | **`00011200`** |
| `0x190c00e0` / `0x191000e0` | `00011000` | `00011000` |
| `0x19140000`..`+0x0c` | **`60010000` ×4** | **`60010001` ×4** |
| `0x19180000`..`+0x0c` | **`00000000` ×4** | **`60010001` ×4** |
| `0x1900c0f0`（SMCT 指针） | `0xfc→0x65`（会倒退，是指针） | 基线 `0x4f`，扫到 port1 时 `0x7e` |
| `0x161000e4`（Uni SerDes 状态） | `0x01000706`（bit0 RX LOS = 0） | `0x01000706` 一样 |

三条读起来最有分量的：

1. **`0x19180000` 在 U-Boot 里是全 0，在 mainline 里被写成 `60010001`（bit0 使能）。**
   按 `zx279133.h:834` 的命名，`0x180000` 正是 XMAC1，而 `zx279133.h:28` 的默认端口 6 = XMAC1 ——
   mainline 把 U-Boot 从来不开的那个 MAC 开了，并把 eth0 的帧全送给它。
2. **`0x19140000`（XMAC0）在 U-Boot 是 `60010000`（bit0 = 0），mainline 是 `60010001`。**
   也就是说两份状态里唯一的 bit0 差别，方向是相反的：通流的那份**没**使能，不通的那份使能了两位。
   `zx279133-mac.c:31,413` 只用 `ZX279133_XMAC1_BASE`，所以这两个窗口的差值不是它写的 —— 谁写的还没定。
3. **四个 `+0x40000` 步进的块从 `00ba2200` 变成 `00fa2203`，两块 `+0xe0` 从 `0x11000` 变成 `0x11200`。**
   §5 那句「mainline 没人写它们」因此要撤回：Linux 侧确实改了它们。改它们的是谁，正在查
   （nppt 基址 +0x40000/0x80000/0xc0000/0x100000 这四个窗口）。

**下一轮要读的东西已经明确**：把 `0x19140000`、`0x19180000` 和四个块的写者找出来，然后判断
「eth0 该走的 MAC 窗口到底是哪一个」——现在所有证据都说 eth0 在驱动一条 U-Boot 根本不用、
而 RTL8226 也不在上面的 lane。

## 10. 20:54 量到的：帧**确实进了 SoC**，只是没到任何一条到 CPU 的队列

RAM kit（同一个 boot），先读 SOPC 计数器，然后从 PC 往线上打 12 个 ping（打不响也没关系，
ARP who-has 已经是真帧），再读一次：

```
                 BEFORE          AFTER(+12 ping)
0x19034100:  0x00650065   ->   0x00860086      +33
0x19034104:  0x00120012   ->   0x00120012      0
0x19034108:  0x000f000f   ->   0x000f000f      0    <- sopc_to_smac5
0x1903410c:  0x0003000f   ->   0x0003000f      0
0x19034130:  0x00080008   ->   0x00080008      0
0x19034134:  0x00070007   ->   0x00070007      0    <- sopc_req_smac5
```

同一个 boot 里 `ethtool -S eth0` 的收方向：**`rx_irq_count 0`、`rx_napi_polls 0`、
`rx_desc_not_ready 0`** —— 描述符环一次都没被弄就绪过。

把两件事放在一起，故障点被夹住了：

- PC 的帧进了芯片（`0x19034100` +33），
- 但**没有任何一条到 CPU 通道的 smac 计数动过**（`sopc_to_smac5` / `sopc_req_smac5` 都是 0 增量），
- 于是 CPU 侧的环、中断、NAPI 全空。

这就是「这个口在芯片里没有被接到 CPU 通道」的形状：帧进得来，转发不到 CPU。
和 §5/§8（出口端口字段两头都不是病因）、§9（mainline 打开了 U-Boot 从来不开的 `0x19180000`）一致。

**所以 #29 是剩下唯一的一条实现路径**：把 RTL8226 作为交换机的一个口声明出来，并按 docs/90 已验证的
transport-VID/PVID 方案接到 CPU 通道上。要做的第一件事实是芯片给它的是第几号口 —— U-Boot 的 CLI 读不到
（§7），就改用原厂二进制里已经解出来的两条：`uni-phy` 数组第 5 项 `0x05`、
docs/13 的 `port=4 outerphy_mode=5 phy_RTL8226`，配合 RX 描述符里那个源端口 5。

## 11. 21:36–21:53 窗口：**「把口声明出来」这条被当场否证**，本篇标题不再成立

上面 §10 的结论（「帧进得来、转发不到 CPU，所以 #29 是把 RTL8226 声明成交换机的口」）在今晚最后
20 分钟里被自己的实验否证了。做法是不出包、只在运行期把芯片配到位，然后用 PC 的流量判决。

量到的（全部在 21:26:33 通电后那一次开机里，`uname -r` = `6.18.38+`，`/proc/uptime` 从 461 s 起）：

| 时间 | 写入 | 读回 | PC 侧结果 | 板侧结果 |
|---|---|---|---|---|
| 21:15 | `fa 5 96040001` | `mf eram mem 5 = 05b97441 …` | — | **写进去的是十进制** 96040001 = 0x05b97441，仪表错，见 §6 同类 |
| 21:18 | `w 5 19 0x96040001`、`fa 5 0x960C0001`、`trapall 5 2` | `spa: mem 5 addr 19 = 0x96040001` / `mf eram mem 5 = 96040001 00000000 00000000 00000000` | 8 ping → 0 回复，邻居表 `00-00-00-00-00-00 Unreachable` | `eth0 rx_packets 0` |
| 21:36 | `w 5 19 0x960C0001`（bit19 = extend 标志，docs/36:178）、`trapall 5 2` | `spa: mem 5 addr 19 = 0x960c0001` | 12 ping → 0 回复 | `lan1 rx 0`、`eth0 rx 0`、`br-lan rx 0`、`nppt +0x34100 = 0` |
| 21:40 | `regw 0x140020 0x80000001`（补上原厂 XMAC0 的 bit0） | `soc reg 0x19140020 = 0x80000001` | 12 ping → 0 回复 | 全 0 |
| 21:50 | 把**一个真能用的口（mem 1）整套字**复制给 mem 5：`w 5 0 0x0000a6a5`、`w 5 10 0x0fa0000e`、`w 5 13 0x00008008`、`w 5 14 0x0`、`w 5 17 0x05400410`、`w 5 18 0x01400940`、`w 5 19 0x960c0001`、`ew 24742 0x960C0001 0x01400940 0x05400410 0`、`trapall 5 2` | 每一个 `w` 都读回写入值；`er 24742` → `960c0001 01400940 05400410 00000000` | 14 + 20 ping → **0 回复**，`PC-TX` 计到 +2312 B 帧确实上了线 | `lan1 rx 0`、`eth0 rx 0`、`br-lan rx 0`、`+0x34100 = 0` |
| 21:53 | 以 TX 计数 `+0x142070` 为锚扫 XMAC0 统计块 `regs 0x142000 64` | 空载与 20 帧之后**逐字相同**；整块只有 `+0x142000..0x14200c = 0x00000020`，`+0x142070`（TX frames）也是 0 | — | 没有任何一个寄存器在数这根线上的帧 |

参照值（同一个 boot 里读到的，用来判断「差在哪」）：

```
mem 1 (能用):  reg0 a6a5   reg1 088f   reg10 0fa0000e  reg13 00008008  reg14 0  reg17 04400410  reg18 00400840  reg19 96040001
mem 5 (mainline 写的): reg0 8015   reg1 088f   reg10 01f4000c  reg13 00650650  reg14 00650650  reg17 0        reg18 0        reg19 00042000
mem 6 (U-Boot 留的):   reg0 0463   reg19 148e0000   MF 镜像 = 960c0001 0e014400 000001f1 148e0000
```

`mem 6` 那一行是今晚最有用的一行：**原厂引导程序早就把 6 号口按 extend 口配好了**（bit19=1，MF 镜像的
flood 组字都填着），mainline 一次都没碰过它 —— 而 `eth0` 的 MAC 恰恰就是 `0x19180000`（XMAC1）。
也就是说：芯片侧该配的一直在那儿，帧还是没有到 CPU。`trapall 6 2` 之后同样 0 回复。

**推出的结论**：故障点不在 9132 的端口声明里。§10 的「帧进了 SoC（`0x19034100` +33）」在今晚每一次采样里
都是 0，那一行是**没定的**——它到底数的是什么，需要另一次开机才能重问；本篇标题
「2.5G 那个丝印口是交换机的口」按 §11 应读作**已被否证**。

剩下的唯一嫌疑（也是今晚全部排除法的落点）：**RTL8226 面向 SoC 那一侧的 SerDes**。量到的支持它：

- 铜口侧是活的：PC 网卡 `Status=Up SPEED=1 Gbps`，`RXBYTES` 在两小时内一次都没涨（板子什么都没发出来）。
- PHY 本体是活的、而且**被 mainline 写过**：`/sys/bus/mdio_bus/devices/14f02000.mdio:05/phy_id = 0x001cc849`，
  `readlink driver` = `RTL8221B-VB-CG 2.5Gbps PHY (C22)`，它自己的 mdio 统计 `reads 9 / writes 15 / transfers 24`。
- 但 `m 5 0` / `m 5 1`（走控制器 #0）= `0xffff` —— **PHY 不在 #0 上**：原厂写 `mdio_8226_id = <0x01>`，
  控制器 #1 的 `reg = <0x00 0x14f02000 0x00 0x1000>`。今晚两次把 `0xffff` 读成「PHY 死了」都是走错了总线。
- generic realtek 驱动**不会**跑原厂的那套 outerphy 序列：`Rtl8226_serdes_rst` 拨 `1e.7581` 的 BIT(4)、
  `Rtl8226_serdes_autoNego_set` 写 `1e.7588` 再写 `1e.7589 = 0x70d0/0x71d0`、`Rtl8226_serdes_link_get`
  读 `1e.758b/1e.758d`、`Rtl8226b_phy_init` 经 `1f.0xa436/0xa438` 下 DSP 补丁（rlt8226b.ko，docs/07:14726、
  devtools/phydiff.py:30）。这些全是 Clause-45，而现在没有任何一条路能在运行期读到它们。

**所以下一步不是猜，是先有一双眼睛**：本次提交给控制台加 `m45` / `m45w`（`zx279133-np.c` 的
`zx279133_9132_write()`），经 `zx279133,outer-phy-bus = <&mdio1>` 这块新属性走
`mdiobus_get_phy()` + `phy_read_mmd()/phy_write_mmd()`，于是 Linux 里能读也能**重放**原厂那几条写。

```
echo 'm45 5 0x1e 0x758d' > /sys/kernel/debug/zx279133-9132
echo 'm45w 5 0x1e 0x7581 0x10' > /sys/kernel/debug/zx279133-9132
```

配对的参考值由 `tools/ubattr.sh` 的第二阶段在**同一个还能ping通原厂 U-Boot 的开机**里读
`mii 45read 5 1e 7581/7588/7589/758b/758d`、`1f a436/a438/d044`、`0 0000/0001`
（`mii 45read` 的 devad/reg 按十六进制解析，docs/07:13839）。两边一比就知道是 generic 驱动改掉了哪几个、
还是原厂那几个从来没写过。

运行期写入随每一次 20 s 断电丢失，所以以上所有配置都不是修复，只是仪表。
