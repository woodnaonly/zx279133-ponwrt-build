# 补百零八：eth0 的帧到底落在哪个 MAC——一次把自己前一步的乐观读法推翻的测量

日期：2026-10-20，本地 21:51–22:12。设备：SK-D840N4（ZX279133 GPON ONU），TTL-USB 在 COM3，
网线插在 br-lan 的某个口（PC = 192.168.9.25），智能插座实测断落点约 :09/:24/:39/:54(+~40s)。

## 0. 结论先说

我上一步把 `network.wan.proto` 改成 `dhcp`（只改配置，可回退），意思是"给 WAN 口标准 OpenWrt 语义，
主人把上级网线插进来就能上网"。**这次测量说明这条 route C 在当前镜像里是空的**：

> CPU 从 eth0 发出的帧，全部被送到 `smac1`（也就是 br-lan 那一侧）；`xmac0 / xmac1 / rgmii / pon0(10G) /
> pon1(1G)` 这几列在同一个窗口里两个方向都恒为 0。eth0 到外挂 PHY（RTL8226）之间没有 MAC 通路。

同一套仪表在 idle 窗口能看见 smac1 上 24 个包（环境流量），eth0 窗口涨到 73（+49），
而 lan 单播对照组（`ping -I br-lan 192.168.9.25`，8 发 8 收）同样只让 smac1 动 —— 所以
"只有 smac1 动"不是仪表坏了，是 eth0 的帧真的没往别的 MAC 去。

## 1. 仪表与其输出结构（这条推翻了我自己的第一次读法）

`/proc/pon/debug/start_statistics` 与 `stop_statistics`：写 `1` 开始/结束一个计数窗口，
**结果整块打印进 dmesg**（不是读文件）。一个窗口约 400 行，里面至少有三套端口表：

| 表头 | 列 |
|---|---|
| `UNI0 … UNI6 LOOPBACK` | 9132 UNI 交换侧 |
| `smac0 … smac6` | `sopc2smac uni sop/eop`、`req2smac uni sop/eop`（TM 投递到 MAC） |
| `smac0 smac1 smac2 smac3 xmac0 xmac1 rgmii pon0(10G) pon1(1G)` | `in_sop/in_eop/success_in_eop/success_out_eop` |

第一次跑（`tools/eth0egress.sh`，22:01）我只取了 `dmesg | tail -190` 再 grep 14 行，窗口被截断，
`paste` 对齐错了一行，于是读成 `sopc2smac = 0 60 31 31 0 0 0`，看起来像"eth0 的帧到了 smac2/smac3"。
全量窗口（`staging/ctl/eth0-egress-wide-2209.txt`，idle 与 eth0 各一整块）出来是：

```
IDLE  in_sop_cnt(16bit)      0 24 0 0 0 0 0 0 0
ETH0  in_sop_cnt(16bit)      0 73 0 0 0 0 0 0 0
IDLE  sopc2smac uni sop      0 19 0 0 0 0 0
ETH0  sopc2smac uni sop      0 59 0 0 0 0 0
```

同一个窗口的其它证据链：`idm vld cnt 19→58`、`in/out idm fwd 19→58`、`qmg2wsch ack 19→58`、
`spa in / out to ppu unicast / ppu in / out to red 42→142`、`sipc to spa 50→176`、
`smac in byte cnt(rc) 1762→5356`，而 **所有 `drop` 类计数两个窗口都是 0**。
也就是说 CPU→IDM→NP→ISU→PPU→SIPC→TM→SMAC 这条链在 4.19 上完全走得通（这正是 mainline 从没做到的部分），
但出口端口被固定成了 smac1。

⇒ 记进规则的教训：截断 + 过滤后的 dmesg 表不能作为归因依据；要归因就得取整窗口，并按 label 而不是按行号配对。

## 2. 顺手量到的三个只读节点（都是第一次读）

| 节点 | 值 | 说明 |
|---|---|---|
| `/proc/zxic/sky_outerphy_state` | `1 9 1` | 字段含义**未证**；`np_133.ko` 里有 `outerphy_get_linkmode`/`outerphy_set_linkmode`/`outerphy_xmac_partner_check`/`outerphy_get_partner_speed` 这组符号，读它的是 bspdriver.ko |
| `/proc/cmac/chip_mac_list` | `mac_list num: 1` + `bridge eth1 84:47:09:5b:bd:2c` | PC 的网卡 MAC 学在 eth1 上，与 `ip neigh` 的 192.168.9.25 一致 |
| `/proc/ethdriver/conf` | `debug mode close / debug mac is 00:00:00:00:00:00` | 这就是之前被判定"不是通路"的 `/dev/ethdriver` 那个驱动的 conf |
| `/proc/rtl_dnstrap/trap_iface` | `br0`（`enable` = 1） | 厂商的 RTL DNS 陷阱仍指向 br0 |

## 3. 本轮同时修掉的三个仪表/工具假阴性

1. `tools/verify_router.sh` 的 arp 通道用 `d0:45-8d-84-09-00` 去匹配 Windows 的
   `d0-45-8d-84-09-00`，且 `grep -c 'ttl='` 对 Windows 的 `TTL=64` 大小写敏感 —— 一台 4/4 通的设备被判 FAIL。
   现在先 `tr ':' '-'`、`grep -aci`，并把 GBK 的 ARP 文本剥到 ASCII 再打印。
2. getty 通道只认 `login:`；这台 root 无密码，BusyBox login 自动登录后控制台直接是 `root@OpenWrt:~#`。
   现在 `login:` 或 shell 提示符都算过。修完后 `verify: ROUTER IS USABLE`（5/5，22:00 冷启动之后）。
3. 这个 rootfs **没有 `pkill` 也没有 `arp` applet**（`pkill: not found`、`arp -s` 退 127），
   所以 21:49 那次 route-B 拆除其实是假的：`/bin/pc`(5495) 与 `gpon_omci`(5778) 一直活到 21:55:10
   才被 `killall -9 pc gpon_omci` 干掉。⇒ 21:11–21:55 那段"路由器已验证可用"的观察窗口里 vendor 进程在跑
   （LAN 计数、ssh、LuCI 这些观测本身是真的，它们不依赖 vendor 进程），但这个事实必须记下来。
   busybox `ping` 也没有 `-b`/`-f`，`-i` 只吃整数秒——`ping -i 0.1` 会 `invalid number '0.1'`，
   一个包都不发，直接造出"计数器全 0"的假结论。

## 4. 现在的设备状态（可回退，都只碰了配置和运行时）

- `/etc/config/network`：`wan` = `eth0` + `proto dhcp`（21:51 提交，22:00:xx 冷启动已生效并验证），
  `lan` 仍 `br-lan`/静态 `192.168.9.1`。回滚参照 `backups/network-pre-wan-20261020-215059.txt`。
- 起来的是标准语义：`udhcpc -i eth0` 在广播 discover，无租约时**不会**冒出假默认路由；
  日志里 `wan6` 正常 up，dnsmasq 在 br-lan 发 192.168.9.100–249。
- 运行时（下次断电即消失）：eth0 上手动 `192.168.9.2/24`。
- 设备时钟仍停在 `Thu Oct 8 11:49 2026`：没有 RTC，NTP 要等 WAN 拿到租约才能同步；这会让所有日志时间戳失真。

## 5. 22:14 冷启动后又补了三个事实，把 route D 的形状改了

| 观测 | 值 | 含义 |
|---|---|---|
| `lsmod \| grep rlt` | `rlt8226b 53248 0` | RTL8226 外挂 PHY 驱动**已经装着**（社区 rootfs 的 `/lib/modules/4.19.136+/rlt8226b.ko` 就在，S99modules 那一层社区脚本已经做了）⇒ "少装一个 PHY 模块"这个便宜的解释被否掉 |
| `/proc/pon/outer_phy` | `xmac0: phy_RTL8226`、`xmac1:`（空） | **eth0 挂在 xmac0** 上，和 §0 里 `xmac0` 列恒 0 正好对上：口认出来了，通路没被喂 |
| vendor `S99modules` 前 60 行 | 先 `insmod rlt8226b.ko`（由 outer_phy 决定装哪个 PHY 驱动），再 `np_133.ko → zx_ponreg_133 → qos_mod → region_init → wan_bridge → iprt → ffe → netdriver → multicast → switch → zte_xgpon → gpondrv_xg` | 厂商顺序里 PHY 驱动在 NP **之前**；本机是社区脚本装载的，顺序/参数不同——这是"测得"，是否是 xmac0 没被喂的原因**未证** |

`/proc/zxic/sky_outerphy_state` 在三个窗口都是 `1 9 1`（idle、eth0 flood、22:14 重启之后），所以它是个
状态量而不是计数器；字段含义仍未证。

⇒ route D 的正确形状不是"装模块"，而是"谁把 CPU 的帧的出口端口从默认的 smac1 改到 xmac0"。
在厂商体系里这件事发生在 app/配置层（`wan_bridge.ko` + `nas_main`/`pc` 下发 VLAN/端口表），
而 route B 已经实测：框架能跑，app 起不来（缺厂商配置库），窗口里 TM 也没有 CLA 规则。
另一条更便宜的可能：U-Boot 的 `xmac_test … 0 2` 能把 xmac0 拉起来（见 [[ref-sk-d840n-uboot-vendor-debug-cli]]），
若真是这样，它得进 bootcmd 才每次生效——那是写 flash，**等主人点头**。

## 6. 已经架好的无人值守仪器

`tools/eth0jackwatch.sh 240`（22:14:47 起，10 s 一次，PC 侧 ssh 采样，落 `staging/ctl/eth0-jackwatch.log`）：
只记 eth0 的 rx/tx 包数、`carrier`、`sky_outerphy_state`。**只要主人把网线换到别的 RJ45**，
那一侧有没有帧进来就会自动留在日志里（`rx>0` 就是 xmac0 收得到，直接回答 §0 剩下的那一半），
不需要谁守在旁边。首批样本：`eth0 … rx=0 tx=27→31 | carrier=1 | 1 9 1`。

## 7. 对"刷好"这个目标意味着什么

`eth0` 想在当前镜像上真正当 WAN 用，缺的是**出口端口映射**（让 NP/TM 把 CPU 的帧投递到 xmac/rgmii 而不是 smac1），
和 mainline 卡住的是同一族问题；区别是 4.19 这套带的是工作固件用过的同一份 `np_133.ko`，
而它的符号里有 `outerphy_set_xmac_mode`、`xmac_mode_set`、`port_attr_*`——也就是厂商自己有一条把
outer PHY/XMAC 配成端口的路，现在只是没被下发。这条路（记作 **D**）比移植到 mainline 近得多，但它仍然未验证。

所以 WAN 真正能通的选择只剩三个，全部要主人表态或动手：
- **A** 整机回厂商固件（`restore_set/` 五件套齐，要写 flash）；
- **C** 把上级网线插进 eth0 对应的那个 RJ45 —— 但按本文件 §0，**插上也拿不到租约**，除非先做完 D；
- **D** 在现系统里把 eth0 的 MAC 通路配起来（我可以只读地把 `xmac_mode_set` 的调用者从 `np_133.ko` 里挖出来再判断）。

物理侧还有两个只有主人能回答的问题：光纤到底插没插（`pon` 的 RX 恒 0），以及四个 RJ45 里哪个是 eth0
（`tools/sweepjack.sh` 就是为这个写的，需要人挪线）。
