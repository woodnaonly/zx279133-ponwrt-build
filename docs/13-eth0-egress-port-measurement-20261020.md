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

## 8. 22:17–22:21 追加：找到并试过厂商的 uplink-mode 开关，route C 仍然不通

`/proc/pon/` 里除了 `debug/` 还有 `tm_debug/`、`switch_debug/` 两整棵 CLI 树（`ls -l` 显示 -r-- 但**写入有效**，
和 `debug/start_statistics` 同一族），里面赫然有两个 uplink 模式开关：`tm_debug/sw_upmode_set`、
`switch_debug/sw_port_set_upmode`。用法直接写在 np_133.ko 的字符串里：

```
echo upModeInfo=xx > /proc/pon/tm_debug/sw_upmode_set
[%s]input:upModeInfo is 0x%x,new_upmode is 0x%x,new_lanup_port is %d new_wlanup_port is %d
```

读 `/proc/pon/up_mode_info`（重启后第一次读，全部为"测得"）：

```
g_up_mode=0x40   g_is_auto_ponmode=0   g_epon_open_high=1
g_wan_port=0     g_lanup_port=0        g_wlanup_port=0
g_switch_ponmode=0x40  g_kernel_switch_en=0x0  g_bosa_type=0x2  g_switch_time=60
```

试过 `upModeInfo=1`：写入 `set success`，`g_up_mode` 真的从 0x40 变成 0x1，ssh/br-lan/ping 全不受影响。
然后用**同一套三口径窗口**（idle / eth0 flood / lan 单播对照）复测，`staging/ctl/upmode1-egress-2218.txt`、
`staging/ctl/upmode-ab-2219.txt`：

| 窗口 | `sopc2smac uni sop`（smac0…smac6） | `in_sop_cnt`（smac0…smac3, xmac0, xmac1, rgmii, pon0, pon1） |
|---|---|---|
| idle（mode 0x1） | `0 38 0 0 0 0 0` | `0 38 0 0 0 0 0 0 0` |
| eth0 flood（mode 0x1） | `0 115 31 31 0 0 0` | `0 154 0 0 0 0 0 0 0` |
| eth0 flood（mode 0x1，第二次 A/B） | `0 116 0 0 0 0 0` / `0 118 0 0 0 0 0` | `0 118 …0` / `0 120 …0` |
| lan 单播对照（mode 0x1） | `0 47 0 0 0 0 0` | `0 46 0 0 0 0 0 0 0` |

结论三条，都是测得：

1. **A/B 否掉了 upmode 假设**：mode 0x1 与"改之前"的 eth0 窗口在 `in_sop_cnt` 上一样，xmac0/xmac1/rgmii/pon0/pon1
   六个口在**所有**窗口都是 0。`upModeInfo=1` 只是换了 `g_up_mode` 这个变量，没让任何一帧走到 eth0 的 MAC。
2. **`31 31` 不是 upmode 造成的**，它时有时无（同样的 mode 0x1，两次 A/B 窗口里都没有）。第一次我把它当成
   "eth0 到 smac2/smac3"的证据，第二次又把它当成截断假象 —— 两个都不对：它是**广播泛洪的偶发命中**
   （udhcpc 在 eth0 上不停 broadcast discover），要归因必须在同一个窗口里区分单播/广播，这条留给下一次。
3. **写不回去**：`upModeInfo=64` 报 `set success` 但 `g_up_mode` 仍是 0x1 —— 这个 handler 对入参有自己的映射，
   没测清之前别把它当配置接口用。当前这块偏差是纯运行时状态，下一次断电（约 22:24）自动回到 0x40。

⇒ route C（eth0 当 WAN）到这里可以判定：**在这套镜像 + 这套装载顺序下，eth0 的帧到不了 xmac0**，
而 `/proc/pon/*_debug/` 这一大套写接口里有 `sw_port_linkmode_set`、`sw_vlan_set_port_pvid`、`sw_mac_onu_proaction_set`、
`tm_mactable_fdb_add` 这些正是"把端口/VLAN 接起来"的旋钮 —— 厂商 app 层（`cspd` 里有 `si_eth_upmode_set`、
`g_reg_upmode`）就是靠它们把口接上的。要沿着这条走，下一步该做的是**读 `cspd`/`pc` 下发顺序**（离线，零风险），
而不是继续往 NP 里乱写。

## 9. 22:23 追加：`/proc/tm/shell` 是 NP 调试变量口子，不是配置 API

cspd / sbin/httpd 里到处是 `echo g_wfwdlatency_flag=1 > /proc/tm/shell`、`echo tx_div_flag=1 > /proc/tm/shell`、
`echo g_mode_detect_flag=%d > /proc/tm/shell` 这类命令，看起来像"厂商用一条 shell 把 NP 配起来"，
`np_133.ko` 里也只有孤零零一句 `Usage: echo [OPTIONS] > /proc/tm/shell`（后面没有选项表）。实测：

- 节点存在：`/proc/tm/shell`（`-r--r--r--`，但写有效）。
- 写 `help` → dmesg 只有 `operation forbid`，**没有**选项表；
- 闸门是 `/proc/pon/forbid_telnet_ctrl_tm_shell`：`echo 0 >` 之后 dmesg 明确回
  `set proc-tm-shell for telnet enabled(0).`，但紧接着的 `echo help > /proc/tm/shell` **仍然** `operation forbid`；
- 换成 cspd 用过的真实变量写法 `echo g_mode_detect_flag=1 > /proc/tm/shell`，**同样** `operation forbid`。

⇒ 更正：我先前一句"forbid 来自内容不合法"是**错的**——合法形式也被拒。现在的测得只是：
即使打开了 telnet 闸门，从 ssh 会话写 `/proc/tm/shell` 一律 `operation forbid`，原因未定
（可能还检查控制终端/调用上下文，或另有第二道闸门）。写完已把闸门恢复成 `disabled(1)`。
这条只能留到"用串口控制台写一次"再判（串口就是真 tty），本轮不追。

⇒ 结论：**route D 的可用旋钮只有 `/proc/pon/{tm,switch}_debug/*` 那一族**（`sw_upmode_set` 实测能写并回显
`input:upModeInfo is 0x40,new_upmode is 0x40,new_lanup_port is 0 new_wlanup_port is 0`），
以及 `switch.ko` 的 ioctl 面（`cspd` 用 `si_sw_do_ioctl`/`si_eth_upmode_set` 走 `/dev/switch_dev`——
本机 `/dev` 里根本没有那些节点，社区镜像没建，route B 时是我 mknod 出来的）。
`/proc/tm/shell` 这条路可以直接排除，省下以后再来试的时间。
git -C ci-build-repo add docs/13-eth0-egress-port-measurement-20261020.md && git -C ci-build-repo commit -q -m "docs(13): the tm shell refuses even a real serial tty - route D cannot use /proc/tm/shell" && git -C ci-build-repo log --oneline -1

## 10. 22:32 追加：串口真 tty 也照样 `operation forbid`

为判"是不是只许本机 tty"，用 TTL 控制台（`root@OpenWrt:~#`，真 tty，先确认 `forbid_telnet_ctrl_tm_shell`
= enabled(0)）写同一个口子：`echo help > /proc/tm/shell` 和 `echo g_mode_detect_flag=1 > /proc/tm/shell`
—— 两条都在 dmesg 回 `operation forbid`，而 shell 自己退出码仍是 0（`HELP-RC=0`/`VAR-RC=0`），
所以**不能拿 `$?` 当写成功的判据**，要看 dmesg。写完把闸门恢复成 disabled(1)。

⇒ `/proc/tm/shell` 从 ssh 与从串口都进不去，里面应该还有一个没点亮的内部开关
（同族嫌疑名字：`low_power_debug_set_int_mask`、`g_netdebug_mod_tab`、`netdebug`，**未证**）。
route D 到此只剩两条：`/proc/pon/{tm,switch}_debug/*` 那族单项 setter（`sw_upmode_set` 实测可写可回显），
和 `switch.ko` 的 `/dev/switch_dev` ioctl（需要 mknod，route B 已验证建节点可行）。

## 11. 22:33–22:37 追加：把 WAN 从 eth0 挪到 eth2 —— 绕开 xmac0 的那条便宜路

先补一条端口归因，因为它改变了形状。用 `tm_debug` 的 getter（`echo port=N > /proc/pon/tm_debug/sw_port_enable_get`，
写法是从 np_133.ko 的 usage 字符串里拿的，答案落 dmesg）扫 port 0..15：

```
port=0..3  [outerphy?]            enable:1
port=4     outerphy_mode=5,outerphy_name=phy_RTL8226.   enable:1
port=5,6   [smac_get_port_enable] error
port=7..14 [sw_port_change_to_smac_index] sw_port(N) is not unvalid smac
```

⇒ **RTL8226（eth0 的 PHY）在厂商 CLI 里是 sw_port 4，而且是 enable=1**；而 eth0 的 CPU 出口帧
始终落在 `sopc2smac` 的第 2 列（index 1），第 5 列（index 4）一直是 0。
试着给它加内部回环 `echo port=4 direct=1 enable=1 > tm_debug/sw_port_loopback_set`
→ `[zxic_api_sw_port_set_port_loopback] input invalid port`（这个 API 不吃 port 4），
所以"无光环境下自证 eth0 收发"这条路也堵了。

于是换思路：**eth2 / eth3 是 9132 UNI 交换机上的口，和已经证明能带 22 MB 流量的 eth1 是同一条通路**，
把它们从 br-lan 里摘出来当 WAN，就完全不需要碰 xmac0/NP 那层。做了，纯配置：

```
uci -q del_list network.@device[0].ports='eth2'   # eth3 同样摘掉
uci set network.wan.device='eth2'; uci set network.wan.proto='dhcp'; uci commit network
/etc/init.d/network reload
```

测得（22:36，reload 之后立刻）：`br-lan` 只剩 `eth1`，`lan` 仍 `up` 且 `192.168.9.1/24`，
ssh/LuCI/ping 全没断，`tools/verify_router.sh` **5/5 PASS**；`eth2` `state UNKNOWN LOWER_UP`、
`ifstatus wan` = `proto dhcp` + `up:false`（无租约，不冒假路由）。
运行时残留也清了：`ifconfig eth0 0.0.0.0` 之后路由表只剩 `192.168.9.0/24 dev br-lan` 一条。

四个口的当前计数（`tools/jackmap.sh` 第一条样本）：
`eth0 rx=0 tx=40180 | eth1 rx=213917 tx=175598 | eth2 rx=0 tx=22025 | eth3 rx=0 tx=13175`
——只有 eth1 有 rx，即只有 PC 那根线在收；eth2/eth3 的 tx 是桥时期泛洪留下的，说明**驱动一直在往这两个口发**。

还没证的有两件，都必须等一根真实的线：

1. **哪个 RJ45 是 eth2**：`tools/jackmap.sh`（20 s 一次，落 `staging/ctl/jackmap.log`）把 eth0..eth3 的
   rx/tx/carrier 一起记；线一挪，rx 突然开始涨的那个 netdev 就是那只口。注意 carrier 不可靠——
   eth0 在没有对端时也报 `c=1`（`sky_outerphy_state` 恒 `1 9 1`）。
2. **9132 在硬件里是否还把 eth2 和 eth1/eth3 划在同一个 VLAN**：netifd 只把它从 Linux bridge 摘了出来，
   硬件 VLAN 表不一定跟着变（`sw_vlan_port_pvid_get` 只回 `cmd success!`，没吐数值，读法还没破解）。
   如果确实同 VLAN，WAN 与 LAN 会在 L2 上共享一段——**能拿到租约、NAT 也能跑，但隔离是假的**，
   届时要改成给 eth2 建独立 VLAN 或直接换 port。这条留在线插上之后判（一眼就能看出来：
   如果 PC 侧能直接看到上游路由器的 DHCP OFFER，就是同 VLAN）。

## 12. 22:38 追加：真正卡住"当路由器用"的下一个环是 NAT，而它缺的只有一个用户态程序

把 WAN 挪到 eth2 之后（§11）顺手查了"NAT 到底能不能做"，答案是**内核能做，用户态一个都没有**：

| 检查 | 结果 |
|---|---|
| `which iptables iptables-legacy iptables-nft` | **全部 none** |
| `nft list ruleset` | `netlink: cache initialization failed: Invalid argument`（这内核没有 nf_tables，`fw4` 因此永远是 "active with no instances"） |
| `/proc/net/ip_tables_targets` | **存在**，内容含 `MASQUERADE SNAT DNAT REDIRECT NETMAP TCPMSS REJECT LOG MARK CONNMARK DSCP` + 厂商自注册项（`FFESKIP NAT44TRANS URLREDIRECT URLACCEPT MAC_DNAT`…） |
| `/proc/net/ip_tables_matches` | `state tcp udp connmark physdev mac multiport iprange set …` |
| `/proc/kallsyms` | `ipt_do_table`、`nf_nat_masquerade_ipv4`、`br_netfilter`、`nf_defrag_ipv4` 全在（builtin） |
| `/proc/sys/net/ipv4/ip_forward` | `1` |

⇒ **legacy ip_tables + NAT 是编译进内核的**，装一条 `MASQUERADE` 只需要一个用户态程序；`opkg` 又因为没有上行而用不了。
试过两条取法，都还没成：
- 24.10.8 官方 feed（`packages/aarch64_generic/{base,packages}`，index 各 347 KB / 2.5 MB 都取到了）里
  **只有 `iptables-mod-*`**，没有 `iptables` / `libxtables*` 本体 —— 24.10 的 iptables 是 nft 后端，而这内核没有 nf_tables。
- 厂商 initramfs 里有 `/bin/iptables`（423,712 B，EM_ARM=0x28 的 32 位 ELF，可被 CONFIG_COMPAT 跑），
  但它依赖 `libxtables.so.12`，而整个 initramfs 里**没有这个库** → 这条也走不通（同族的 `libcommfun.so`、
  `ld-2.26.so`、`libc-2.26.so` 都在，只缺 xtables）。
- `releases/22.03.7/packages/aarch64_generic/base/Packages` 是 404（153 B），路径待换 `targets/armsr/armv8/packages`。

**下一步（有网之后第一件事）**：找一个 aarch64 的 legacy `iptables`（1.8.x + `libxtables.so.12`，
musl 或静态均可），`ssh root@192.168.9.1 "cat > /tmp/ipt.ipk" < f.ipk` 推进去 `opkg install --force-depends`，
然后 `iptables -t nat -A POSTROUTING -o eth2 -j MASQUERADE`。判据是 `-t nat -L -v` 能列出规则且 pkts 计数会动；
内核侧已经没有借口了。

## 13. 四个口的实测计数（22:36，`tools/jackmap.sh` 第一条）

`eth0 rx=0 tx=40180 | eth1 rx=213917 tx=175598 | eth2 rx=0 tx=22025 | eth3 rx=0 tx=13175`，
carrier 分别 `1 / 1 / 1 / (空)`。只有 eth1 有 rx —— 也就是**全板只有 PC 那一根线在收**；
eth2/eth3 的 tx 是它们还在桥里时被泛洪出来的计数，说明驱动层面一直在往这两个口发。

## 14. NAT 用户态这一环：三条取法都试到底了，只剩离线拆包

按 §12 的结论去取一个 legacy iptables，三条路的实测结果：

1. **24.10.8 官方 feed（armsr aarch64_generic，index 347 KB + 2.5 MB 都完整取到）**：
   `iptables` / `iptables-legacy` / `iptables-nft` / `libxtables*` **一个都没有**（0 命中）。
   这不是解析失败——同一个 index 里 `iptables-mod-account/-asn/-chaos/…`、`collectd-mod-iptables`、
   `geoip-shell-iptables`、`miniupnpd-iptables` 都列得出来，所以核心包是真的不在。
   ⇒ 没有上行、也没有这些包的现在，`opkg` 这条路是死的。
2. **厂商 initramfs**：有 `/bin/iptables`(423,712 B, EM_ARM) 和 `/bin/ip6tables`，但整个 initramfs 里没有
   `libxtables.so.12`（同目录的 `libcommfun.so`、`ld-2.26.so`、`libc-2.26.so` 都在），所以它跑不起来。
3. **本地所有 flash 导出**（`device-dumps/` 242 MB、`restore_set/root_stock.bin`、`backups/`）里
   grep `xtables` **零命中**。注意这条**不能当否证**：jffs2 节点是压缩存的，字符串本来就看不见；
   而 `staging/stock-initramfs.cpio` 命中 2 次，那正是 iptables 二进制自己引用的 SONAME。

⇒ 唯一还没走的技术通道：**离线把 `root_stock.bin`（厂商 jffs2 根）里的 `usr/lib/libxtables.so.12` 抽出来**
（`jffs2reader` / `jefferson`，本机没有、python3 是 stub，可行的是丢到 vps1 上解），
拿到之后和厂商 `bin/iptables` 一起推进 `/tmp`，用
`/tmp/vroot/lib/ld-2.26.so --library-path /tmp/vroot/lib /tmp/vroot/bin/iptables -t nat -A POSTROUTING -o eth2 -j MASQUERADE`
——CONFIG_COMPAT 能跑 arm32 这点 route B 已经证过。内核侧没有障碍（`/proc/net/ip_tables_targets`
里 `MASQUERADE` 就在，`ipt_do_table`/`nf_nat_masquerade_ipv4` 都是 builtin）。

判据（下次有 WAN 时一次跑完）：`iptables -t nat -L -v` 列出规则，且 LAN 客户端经过板子时那条规则的 pkts 计数会涨。

## 15. 22:52 实测：eth2 摘出桥之后，硬件层是真的隔离了（这一半可以放心）

无光线也能判的一问：netifd 只是把 eth2 从 Linux bridge 里摘出来，9132 的**硬件**转发域跟不跟着变？
做法：给 eth2 临时配 `192.168.9.3/24`（运行时，随后清掉），从板子分别朝 PC 打两次 ICMP：

| 出口 | 结果 | 邻居表 |
|---|---|---|
| `ping -I br-lan 192.168.9.25` | **4/4 回复** | `192.168.9.25 dev br-lan lladdr 84:47:09:5b:bd:2c REACHABLE` |
| `ping -I eth2 192.168.9.25` | **0/6 回复** | `192.168.9.25 dev eth2 probes 5 INCOMPLETE` |

PC 就在同一个 IP 网段的 eth1 段上、并且对 br-lan 的 ARP 应答正常，
所以 eth2 发出的 ARP who-has **没有泄进 LAN 泛洪域** —— 隔离成立（若还在同一硬件 VLAN，PC 必然回）。
注意这一条**只证明隔离**，不证明 eth2 那只口能收：它现在没插线，0/6 同时也是"没对端"的预期结果。
还剩两问：eth2 的 jack 收到帧（插上就有答案，`tools/jackmap.sh` 会记）、以及 NAT 用户态（#56）。
