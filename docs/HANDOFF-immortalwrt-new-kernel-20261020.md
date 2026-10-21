# 交接：新内核 + ImmortalWrt（SK-D840N，GPON 光猫）

写于 2026-10-20 22:57；23:21 复核、23:5x 修订。设备：Skyworth/ZTE **SK-D840N**（SoC **ZX279133**，aarch64，**GPON ONU**）。
读者：**接手执行的新模型/新会话**。本文件是权威起点，不要凭对话记忆行动；**执行顺序见 `PLAN-immortalwrt-new-kernel-20261020.md`**。

---

## 0. 目标口径与硬约束（先看）

**目标**：把这块板子刷成「**新内核 + ImmortalWrt**」。
**基线内核来自 SR1010（路由器）**；本板是**光猫（GPON ONU）**。同一颗 SoC，产品角色与板级不同，**不能把 SR1010 的 WAN/LAN 假设直接搬过来**。

**本板物理角色**：面板是 **4×RJ45 LAN + 光纤 PON**，**没有 WAN 电口**；`eth0`（NPPT+RTL8226）本身就是四个 LAN jack 之一。现有配置把 `eth2` 叫 `wan`、把 `eth0` 叫过 `wan`，都是**实验室把一只 LAN 口当以太网上行**的权宜用法，不是这台光猫的上行。**真正上行是 PON。**（docs/07 补八十一，14:58 订正）

**先选验收口径**：
- **口径 A｜实验室路由器**：以太网上行（eth2 或 eth0）、LAN 口、NAT；**不要求**光纤注册/OMCI。
- **口径 B｜可用光猫**：PON 光纤上行 + 注册 + OMCI + 至少一个 LAN 口 + NAT。
两者差一整块 PON 状态机。不先选口径，最后一定会在“为什么插光纤没反应”上返工。

**新内核侧的主次（这是本文件最重要的一张图）**：

1. **9132 UNI 交换机驱动 —— 必经之路。** 四个 RJ45 里三个挂在 9132 上（eth1/eth2/eth3），mainline 没有驱动；拿不到它，新内核就只剩 eth0 一个 netdev，而 eth0 也不通流（§4.0）。**口径 A 和 B 都绕不过。**
2. **PON 状态机/OMCI —— 口径 B 的必经之路。** 新内核树有 `ZTE_ZX279133_ETH`（PON MAC）、`mcode_intel.bin` 和 PON 数据通路，但 G.984 PLOAM/GEM/T-CONT/OMCI 状态机不在；厂商 4.19 有全套（`zte_xgpon`/`gpondrv_xg`/OMCI），但它的 OMCI TX 门也还没打通（见 §4.0）。**做光猫 = 移植这一大块，工作量大于 9132。**
3. **eth0 / jack 4 —— 可选旁支，不是门槛。** 它只是四个 LAN jack 里的一个。**厂商自己的 4.19 在同一只 jack 上也一个字节都不发**（docs/07 补九十四）；唯一发得出去的是 U-Boot（同一颗 RTL8226/同一根线，11 MB）。所以不要为了“让 mainline 的 eth0 通”阻塞整个计划；它只影响你要不要那一个口。真要碰，先做 §4.0 的 switch.ko 离线差集，不要从 NP 寄存器盲改。

**三条操作硬约束**：

1. **不要用两个内核混写同一个 jffs2 根。** mainline 写过的目录项会让 4.19 解析名字失败（`open: Not a directory` 每轮开机 ~800 行，进而 `/etc/preinit` 起不来）。修法唯一有效的是**重建 inode**（`rm`+`mkdir`，内容经 RAM 暂存），rename 没用——全过程与数据见 `docs/12`。反过来（先 4.19 写、新内核读）同样有风险。**换内核 = 连 rootfs 一起当作一套来出。**
2. **不能从正在运行的系统里刷 `root`**（`root` 就是 `/`）。必须先进 RAM 环境（kexec/initramfs，或 zrec 网页恢复服务），再 `mtd erase root; mtd write <img> root; mtd verify`。
   `/sbin/mtd` 是本机唯一刷写原语（**没有** mtd_debug/nandwrite/mtd-utils）；实测支持 `verify`、支持 `mtdX` 或**带空格的分区名**（`"parameter tags"`）；`/tagparam` 要从 `/dev/mtdblock4` 挂。
3. **不要再开“和 U-Boot 对照寄存器”的第十一轮。** docs/07 补九十五已经把这条方法的边际收益用尽（十条否证）；剩下的差异在 `switch.ko` 的模块初始化写法里，是离线差集工作（§4.0），不是又一个窗口。

---

## 0.5 执行进展（2026-10-21，本轮）

**口径已定：M2（可用光猫），M1 是前置。M1 通过后才把 M2 的 PON 工作并入主线。**

**G1（switch.ko init 离线差集 / task #42）已通过**，完整产出在
**`docs/15-switchko-init-diff-20261020.md`**（原始反汇编 `staging/ctl/switchko-init-20261020.txt`）。
本轮拿到的、下一任不必重做的东西：

1. **9132 的寄存器写实现在 `np_133.ko`，不在 `switch.ko`**（md5 `92b68212542930114769e3c69efe64b6`）。
   `switch.ko` 的 `zxic_api_sw_*` / `tm_*` 全是 UND import。
2. **厂商的间接寄存器编码和 mainline 逐位相同**：
   `cmd = addr | (mem_id << 20) | (op << 27)`。
   ⇒ mainline 现成的 `zx279133_spa_indirect_write/read(eth, mem_id, addr, value)`
   **就是**厂商写 9132 的那把锤子，不需要新驱动框架。
3. **9132 的每端口属性字在 SPA indirect 地址 19（0x013）**，位定义见 docs/15 §5。
   端口使能**不是** SPA 寄存器：厂商走 `smac_set_port_enable()` → 内部 PHY 的 MDIO。
4. **端口号映射**：`sw_port_change_to_chip_port(p) = p+1`（p=0..6），
   `sw_port_change_to_smac_index(p) = p`（p=0..6，59→7）。
   ⇒ **9132 的 4 个 UNI 口 = PPU/mem_id 1..4；外挂 RTL8226（jack 4）= PPU 5；PPU 0 = CPU 口。**
   活板只读取证：switch 口 0..3 = smac0..3，**口 4 = `outerphy_mode=5, phy_RTL8226`**，
   口 5/6 = 宿主侧，口 7 = invalid。
5. **mainline 已经有 4 个逻辑 LAN 口的框架**：`ZX279133_LAN_TRANSPORT_VID_MIN..MAX` = 59..62，
   `zx279133_lan_vport_mf[]` 四条 MF 表，`zx279133_lan_vport_wan_set(eth, vport, wan)`
   可把任一 vport 切成 WAN。**G2/G3 需要的"一只口当 WAN"机制已经在树里了。**
6. **不要碰 `zx279133_lan_port_bringup()`**（mem 5/10/15）——那是 SR1010 外挂 RTL8372N 的路径。

本轮已落地的代码（`kernel-overlays/drivers/net/ethernet/zte/zx279133-np.c`）：
新增 `zx279133_9132_port_init()`，在 `zx279133_np_prepare()` 里调用，
按厂商 `switch.ko` init 的实参写 PPU 1..4 的属性字（isolate/learn/DA-lookup/flood/route-mode/VLAN-check）
与 PVID（transport VID 59..62）。**端口使能（PHY 侧）还没做。**

### 0.6 2026-10-21 白天的四条新结论（动手前先读这一节）

**① CI 连挂四轮的真凶是两个头文件，不是别的东西。**
本树没有 `include/linux/netdev.h`（404）也没有 `include/linux/switchdev.h`
（在 `include/net/switchdev.h`），而我们的 `zx279133-switch.c` include 了这两个 ⇒
编译器在头文件就 fatal，后续错误从未被解析。
**workflow 的失败注解用 `tail -n 12` 只保留 make 链，把编译器原文截掉了** ⇒
run88/89/91/92 四轮都在猜原因。已改为 `<linux/netdevice.h>` + `<net/switchdev.h>`，
并在 CI 加了"驱动 include 必须存在于树里"的门（独立 step），失败注解先吐编译器原文。
**详细**：docs/21 之前的 `docs/19`；CI 侧见 `.github/workflows/build-sk-d840n.yml`。

**② GitHub Actions 的单个 `run:` 块上限是 21000 字符。**
run95 因此被判 `Invalid workflow file ... Exceeded max expression length 21000`，
**整轮白跑**（一秒都没编译）。散文必须放 YAML 注释，不能放 `run:` 脚本里。
现在最长块 18134（余量 2866）；推之前用 `staging/ctl/wflen.py` 查。

**③ "NAT 路由器"这条路原来内核和镜像两边都是空的**（docs/21）：
内核 `CONFIG_NETFILTER` 关着（feed 的 fragment 就写着 `# CONFIG_NETFILTER is not set`），
镜像里没有 nft、没有 iptables、连 busybox 的 iptables applet 都没有；
而 feed 的 nftables 包依赖一个**这个分支里不存在的** kmod 包（`+kmod-nft-core`），
所以它连被选中都不行。
⇒ run97（`4129a20`）把 netfilter 全部**编进内核**，用户态用
`overlays/package/network/utils/nftables/Makefile`（feed 的配方去掉那条依赖）。
**符号名按本树写**：masquerade 是 `CONFIG_NFT_MASQ`（不是 `NFT_MASQUERADE`），
counter 表达式**没有自己的符号**（`nft_counter.o` 在 `nf_tables-objs` 里）。

**④ 板上只有一根网线，M1 的物理前提没有第二根。**
PC↔UNI2（我们的 `lan2`）。厂商自己的 udhcpc 在另一只口上跑了一小时一个租约都没有。
⇒ 两条路并存：
- **真两口**（要机主插第二根线，插哪只都行，板子自己认：`S26nat` 搜 `lan3`→`lan1`→`lan4`，
  60 s 一轮的 watcher 会认出后插的线）；
- **单线**（不用等机主）：PC 当上游（Windows ICS：Wi-Fi 出、有线回，自带 DHCP 和 NAT，
  `tools/labupstream.ps1`），板子的 WAN = PC 所在口（`/etc/labwan = pc`），
  板子的 LAN = 其余三口 + **板子自己造的客户端**（`S27labclient`：veth 对 + 网络命名空间）。
  ⇒ DHCP 服务器、DHCP 客户端、桥、路由、NAT、DNS、上网，全部能验；
  **只有"两口各插一根线"这件事本身仍然要一根线**。

**⑤ 一个镜像级的坑：这版镜像的 `/etc/config/network` 是 JSON。**
`/lib/functions/uci-defaults.sh` 是用 `json_select_object` 写的，生成的文件是 JSON 对象、
LAN 的端口是 `ports` 数组。**用 `uci set` 改它不保证被接受** ⇒ 改配置要走镜像自己的
helper（`board_config_update` + `ucidef_set_interface` + `board_config_flush`）。

### 0.7 2026-10-21 早上这轮：三个"不测就一定会踩"的坑（动手前先读这一节）

完整取证见 **`docs/22-nat-acceptance-baseline-and-busybox-udhcpc-20261021.md`**。

**① 内核 .config 的实物证据拿到了，netfilter 的 pin 路径成立。**
`staging/ctl/ikcfg.sh` 能把内核 Image 里 `IKCFG_ST/IKCFG_ED` 之间的 .config 解出来，
run86 的产物就在 `staging/trial/trial-Image.gz` ⇒ **不用板子、不用 CI 日志**就能读出出货内核的真实配置。
run86 的真实值：`NETFILTER=y`、`NF_TABLES=not set`、`NF_CONNTRACK=not set`、`VETH=not set`、
**`NET_NS=y`**（⇒ `S27labclient` 的 netns 客户端成立）、`BRIDGE=y`。
**整段 netfilter 没有任何一个 `=m`** ⇒ 这个分支里没有任何包声明过这些符号
⇒ 优先级最高的 `.config.override` 里不会有它们 ⇒ 片段里的 `=y` 不会被压掉。
另外 `config/Config-kernel.in` 里**没有** `KERNEL_NF_*`/`KERNEL_VETH`/`KERNEL_NETFILTER`
⇒ DEVMEM 那个"awk 追加压过片段"的坑对 netfilter **不成立**。

**② 这版镜像的 udhcpc 是 busybox 的，它用的是**另一套约定** —— M1 会必然不通过，而且日志上完全看不见。**
镜像已经换成 apk 格式，包清单里**没有 `udhcpc` 包**（也没有 `brctl` 包）⇒ 都是 busybox applet。
**决定性证据就在板子上**：厂商 4.19 的 `/usr/share/udhcpc/default.script` 就是 busybox 自己的脚本，
它写着 `ip addr add $ip/${subnet:-255.255.255.0} ...` —— 所以 busybox 的 udhcpc
**只调脚本一次**（`$1=bound`），**没有 `$2=option` 这个约定**，也**不导出 `mask`**，
`$subnet` 还是**掩码**不是网络地址。
而我们的两个租约脚本写的是 `case "$2" in ip)` ⇒ 在 busybox 下**一个分支都不匹配**，
脚本直接 `exit 0`，**地址从来没被设过**；又因为脚本把 `2>/dev/null` 挂在命令上，
**日志里什么都没有**，看起来跟"上游没接"一模一样。
两个脚本现在两套约定都认，前缀长度由 `pfx_of` 自己算（前缀长度/掩码/网络地址三种都收）。
**教训与工具**：这类"变量可能不存在、约定可能不同"的脚本必须用**真实脚本 + stub** 跑断言，
`staging/ctl/testlease.sh`（21 项，全绿，两套约定都覆盖）——它自己就抓出了三个问题：
`unset ip` 会把 stub **函数**一起删掉（于是测试全绿但什么都没测）、
前缀换算把网络地址算成了 `/31`、以及**第一版测试只测了另一套约定**（测的根本不是这个镜像走的路）。

**③ 单线 M1 的验收脚本原本有个鸡生蛋死锁。**
`tools/labnat-upstream.ps1` 原来靠 `ssh 192.168.9.1` 去查 WAN 租约，
但单线 M1 里**板子的 WAN 口就是 PC 所在那只口** —— 板子把它摘出 br-lan 的那一刻，
192.168.9.1 就离开了 PC 的二层域、从此不应答。脚本一路等到超时然后报 "no lease"，
**而租约一直好好地在板子上**。
现在改成**从 PC 侧发现板子**：先看邻居缓存（PC 自己的 DHCP 交互会把板子的地址和 MAC 放进去），
没有就扫共享网段 `192.168.137.0/24` 的 22 端口 SSH banner；拿到地址后所有证据都用那个地址读。
`S26nat` 同时改成 `nohup ... &` 脱离 ssh —— 原来它是那条 ssh 连接的前台子进程，
而**那条连接正是端口换手时断掉的那条**。

**④ busybox 的 udhcpc 开关语义和独立版不一样。**
`-t N` 是**发 N 个 discover 包**，`-T SEC` 是**租约过期后多少秒再重连**；
独立 udhcpc 的 `-t N -T SEC` 才是"N 次、间隔 SEC 秒"。
原来的 `udhcpc -i $WAN -f -t 0 -T 5` 读起来像"永远重试、每 5 秒一次"，
在 busybox 下却是"发 0 个包"。现在两个开关都不给。

**⑤ 驱动侧复核结论：我们的 DSA 形状和树里那份已验证的驱动一致。**
`ds->num_ports=9`（树里 RTL8372N 也是 9）、用户口 4..7 + CPU 口 8、
`DSA_PPU(port)=port-3`（⇒ PPU 1..4）、`DSA_PHY(port)=0x0a+port-4`（⇒ PHY 0x0a..0x0d）、
tagger 用树里已有的 `DSA_TAG_PROTO_ZX279133_RTL8372N`。
**树里那份驱动也没有 `get_port_phy`** —— 链路状态同样走 `phylink_fixed_state` ⇒ 一致。
只改了一个常量：`ZX279133_SWITCH_UNI_PORT_MIN/MAX` 由 `0..3` 改为 `1..4`
（它们只用在一条打印语句里，而 NPPT 驱动打的是 "9132 UNI ports 1..4"，
之前两个驱动对同一批硬件口报了两套编号）。

**⑥ CI 现在有两个 round 在跑，用途不同。**
run98（`d42686b`，07:45 起）修的是 `priv->dev` 那个编译错；
run99（`f499177`，08:07 起）带的是租约脚本修复和 UNI 口编号。
run98 的 initramfs 镜像里带的是**旧的**租约脚本（run99 才有新的）——
这不阻塞，因为 `tools/labpush.sh` 每次 RAM 试跑前都会把仓库里当前的
`S26nat`/`S27labclient` 推上板并重跑。

---

## 0.8 2026-10-21 上午 09:05：9132 第一次在 mainline 里开机，卡在 DSA 注册（动手前先读这一节）

完整取证见 **`docs/23-9132-first-mainline-boot-and-the-dsa-setup-hook-20261021.md`**，
启动切片 `staging/ctl/run98-boot-evidence.txt`。

**① run98 是这块板子上第一次真的跑起 mainline 内核 + 9132 驱动，1 秒内把 G2 的量测全拿到了：**

| 计划要测的 | 板子说的 | 结论 |
|---|---|---|
| lane 继承自 bootloader | `XPCS0 status=0x380b link=up` | **PASS**（run86 是 `0x280b link=down`） |
| Uni SerDes 不被改写 | `Uni SerDes 1G inherited: mode 0x1 already, image differs in 0/49 word(s)` | **PASS** |
| 四个 SOPC MAC 放行 | `SOPC send-enable published for 4 MAC(s)` | **PASS** |
| 四个内部 PHY / PC 在哪只口 | `UNI PHY 0b: ... link 1`，`0a/0c/0d: link 0` | **PASS**，PC 在第二只 UNI 口 |
| PPU 逐端口字 + PVID | `PPU 1..4 attribute word 96040001 (VID 59..62)` | **PASS** |
| 四个口分成 lan1..lan4 | **没走到**（panic） | 未验 |

**② panic 的根因不是我们的代码写错，是这棵树的 DSA core 有一个"头文件说可选、core 直接调"的钩子。**
`pc : 0x0` + `lr : dsa_register_switch+0x904/0xde0` = 跳到空函数指针。
`staging/tree/net_dsa_dsa.c:644` 是 `err = ds->ops->setup(ds);` —— **前面没有 `if (ds->ops->setup)`**，
而 `include/net/dsa.h` 里写的是 "Optional switch-wide initialization and destruction methods"。
**不写 `.setup` 的驱动不是编不过，是开机就死。** 树里唯一的另一个开关驱动（RTL8372N）有 `.setup`，
所以 core 才敢直接调。

**③ 这棵树不是 stock mainline 6.18 —— 拿 mainline 的知识来改它一定会踩。**
它的 DSA core 里有 "switch tree" 改造：端口是**链表**不是数组、**phylink 由 core 自己建**
（`dsa_port_phylink_create()`，driver 只提供 `phylink_get_caps`/`phylink_fixed_state`）、
driver 的 `dsa_switch` 用 `devm_kzalloc()` 而不是 `dsa_switch_alloc()`。
**读树里那份驱动，别读 mainline 的。**

**④ 同一个注册路径上还有第二个坑，它不 panic、只报错，但一样让四个口出不来。**
`dsa_port_enable()` 对 **CPU 口**也会调一次，而我们的 `zx279133_switch_port_enable()`
对 4..7 以外的端口一律返回 `-EINVAL` —— **CPU 口是 8**。⇒ 修好 `.setup` 之后注册仍然会失败。
已改：CPU 口放行（它就是 SoC 自己的 SGMII lane，背后没有 PHY 要上电）；
`port_bridge_join/leave` 也跳过 CPU 口（它没有自己的 PPU）。

**⑤ 修法已推 `70a5f5b` → run100。** 配套检查工具 `staging/ctl/drivercheck.py`：
读树里的 `include/net/dsa.h`，列出 core **无保护**调用（即可能跳到 0）的所有 `ops->`，
并核对我们的 ops 表。结论：`.setup` 是唯一缺的。

**⑥ 工具坑：串口 relay 的写句柄会卡死，看起来跟板子死了一模一样。**
08:54 那次试跑，relay 一直在报 `send error`，板子其实**已经重启并打完了整个 U-Boot + 内核启动**，
**747 字节的控制台输出卡在串口里没人读**。原因是 CH340 的发送缓冲在板子断电期间被填满，
写超时后每一次写都超时。⇒ `tools/serrelay.ps1` 现在写失败就 close/open 端口（自愈），
新增 `staging/ctl/relaycheck.sh`（写个 marker 看 relay 有没有说"已发送"，
没有就杀掉 relay 子进程让 relayloop 重拉），`tools/onewindow.sh` 每次开机前先跑它。
**卡死的 recorder 和死掉的板子长得一模一样 —— 窗口只有 14 分钟时，这是最贵的一类误会。**

---

## 0.9 2026-10-21 上午 10:30–11:30：run100 成功注册为 DSA switch，四个用户口卡在 PHY（动手前先读这一节）

完整取证见 **`docs/25-the-jacks-phy-and-the-trees-contract-20261021.md`**（树内契约逐条引用）、
**`docs/24-9132-port-table-and-a-runtime-console-20261021.md`**（厂商活板端口表 + 运行时控制台）。

**① run100 是这块板子上第一次真的跑起 mainline 内核 + 9132 驱动，1 秒内把 G2 的量测全拿到了：**
无 panic，内核跑到用户态（`Run /init as init process`、`init: Console is alive`），
**9132 成功注册为 DSA switch**（`registered the 9132 as a DSA switch: ports 4..7 carry the transport VIDs 59..62`），
CPU 口 `lan-cpu0` 起来（`Link is Up - 1Gbps/Full`），conduit 与 WAN netdev 都注册了。

**② 唯一的阻塞是四个用户口的 PHY：**
```
lan1..lan4 (uninitialized): failed to connect to PHY: -ENODEV
error -19 setting up PHY for tree 0, switch 0, port 4..7
```
⇒ **lan1..lan4 根本没生成**，br-lan 无成员，G2d/G3/G4 全被它卡住。

**③ 根因有两层，都已从树内源码读实（不要靠猜）：**
- **第一层**：本驱动没有 `ops->phy_read`，而 DSA core **只为**提供了 `ops->phy_read` 的 switch driver
  建 `ds->user_mii_bus`（`net/dsa/dsa.c:652`）⇒ `ds->user_mii_bus` 是 NULL，
  `dsa_user_phy_setup()` 的 fallback（`if (ret == -ENODEV && ds->user_mii_bus)`）根本没机会跑。
- **第二层**：就算有 bus，core 也只是**查表**（`mdiobus_get_phy()` → `bus->mdio_map[addr]`），
  必须先有 PHY 设备被注册；而本树**没有任何驱动**认领这颗芯片报的 id
  （C22 `0x84b9:0x5032`；树内 `zx279051.c` 期望 `0x000084b9` 且走 C45，Realtek 期望 `0x001cc9`，
  `genphy_driver` 是 `0xffffffff/0xffffffff` 精确匹配）。**必须自写 PHY 驱动。**
- 另外**地址对不上**：9132 内置 PHY 在 **SoC mdio0 的 0x0a..0x0d**，而 DSA 端口号是 **4..7** ⇒ 需要平移。
- 树内的**正确范例**是 `zx279133-rtl8372n.c`（同 SoC 家族、同"switch+内置 PHY"形状）：
  私有 `mii_bus` + 按 **bus 身份+地址**（不是 PHY id）匹配的私有 PHY 驱动；
  而且**只要 bus 有 `read`，core 自己扫描就会把 PHY 设备建出来**（`__mdiobus_register()` → `mdiobus_scan_bus_c22()`）。

**④ run102（`99ad1c7`）先解锁 lan1..lan4：**
四个端口加 `fixed-link { speed=<1000>; full-duplex; }` ——
`phylink_fwnode_phy_connect()` 在 `MLO_AN_FIXED` 下**直接 return 0，根本不找 PHY**。
**代价说清楚**：fixed-link 的口没有自己的 carrier，软件看不到协商出的速率/双工；
但 **PHY 本身没被动过**（NPPT 驱动照样上电+开自协商，0x0b 口 link 1 一直在），9132 自己读 PHY 状态，
所以没插线时照样不通流。**run103 再上真 PHY。**
同一轮还做了两件事：**把四颗 PHY 的 0x00..0x1f 全量寄存器打进 dmesg**（写驱动要对着芯片的真实回答写），
debugfs 控制台加了 `m <addr> <reg>` / `mw <addr> <reg> <val>`（MDIO 读写，addr 是 mdio0 上的 0x0a..0x0d），
**并修好了 NAT 规则集**（`masquerade` 是 nft 的终结语句，后面的 `counter` 让整张表加载失败 ⇒
`oifname "$WAN" counter masquerade`）。

**⑤ 工具坑：串口 root shell 通道已打通。**
往 `staging/ctl/sercmd.txt` 写命令（结尾 `\r`，多命令用 `\r` 分隔），relay 自动发出，
响应进 `staging/ctl/serial.log` —— 厂商 4.19 与 mainline 用户态都实测可用。

**⑥ 别把厂商的"转发语义"记错（docs/15 需要订正）：**
厂商的 "flood num / group id" 写的是**表 125**（字段 0x46/0x47），**不是** word17/18；
隔离矩阵是 **ERAM 表 27**（`se_eram_table_set(0x1b, p | (q<<6), 1, value)`，64×64 bit，1=隔离）；
厂商 UNI 口的 SVID=0 与 tree 的 tagger（55+port ⇒ 59..62）**冲突**，要靠 run101+ 的 debugfs 控制台在活体板上验。

---

## 1. 板子现在的真实状态（2026-10-20 23:21–23:25 实测）

| 项 | 值 |
|---|---|
| flash 三元组 | 社区 `boot.bin`（U-Boot 2021.01-svn308776，**未动过**）+ 社区 **4.19.136+** `uImage`（md5 `a265fe5b…`，pad 后 `f68ceb0f…`）+ 当前 `board.dtb`（48,463 B，pad `881af2d3…`，`model` = `ZTE 133`，`compatible` = `zte,133`）+ OpenWrt **24.10.8** jffs2 `root` |
| 可用性 | `tools/verify_router.sh` **5/5 PASS**（22:14、22:43 两次纯冷启动复现）；23:21 复核 ping 4/4、ssh、LuCI HTTP 200；console/getty 通道依赖 `serial.log`，本轮未重跑 |
| 网络 | `lan` = `br-lan`，**只含 eth1**，静态 `192.168.9.1/24`；`wan` = **eth2** + `proto dhcp`（无租约、无假默认路由）；`eth3` 已摘出；**`wan6` 仍指 eth0（遗留，未清）**；PC = `192.168.9.25` |
| 访问 | `ssh root@192.168.9.1`（公钥 `~/.ssh/id_onu`，**root 无密码**）；LuCI http://192.168.9.1（Argon + 简体中文） |
| 分区（当前 4.19 dtb，`part@128` 表） | `mtd1 boot @0 +2M` · `mtd2 kernel @0x200000 +8M` · `mtd3 dtb @0xa00000 +1M` · `mtd4 "parameter tags" @0xb00000 +4M` · `mtd5 root @0xf00000 +241M`；**`mtd0 whole` 的 reg 只有 128 MiB，但 mtd5 实测 241 MiB、覆盖到 0x10000000（256 MiB）**。所以**不要用 mtd0 的裸偏移写 >128M**，按标签写 `mtd5` 即可；`restore_set/root_stock.bin` 正好 252,706,816 B（241 MiB），证明跨 128M 的读写是通的。 |
| 分区（mainline dtb，route B） | 同一段 246784k 被拆成 **`root @0xf00000 +240M`** 与 **`scratch @0xff00000 +1M`（mtd6）**；U-Boot 的 `root=/dev/mtdblock5` 仍然对。**交接书此前的“241M”只适用于当前 dtb；route B 刷完后会多出 mtd6 scratch，`mtd5` 变 240M。** |
| 备份 | `backups/root-20261020-2045.bin.gz`（mtd5 全量，md5 双向一致）、`backups/config-20261020-2100.tgz`（sysupgrade -b）、`backups/network-pre-wan-20261020-215059.txt`（改 WAN 前原文）、`backups/network-backup-223503.txt`（**注意：这是 22:35 的旧配置**，wan=eth0 static、br-lan=eth1/2/3；当前配置未快照） |
| 整机回退 | `restore_set/{boot,kernel,dtb,param,root}_stock.bin` 五件套齐全（原厂固件，含光猫/注册） |
| 本轮写入 | **零** flash 写入；只有 uci 配置（可回退）与运行时状态 |

**未证/未知**：光纤有没有插、能否注册（`pon` rx 恒 0）；PON 状态机/OMCI 是否能跑；eth2 对应面板哪只 RJ45（`tools/jackmap.sh` 正在记）；9132 是否把 eth2/eth3 划在别的硬件 VLAN（隔离只证到“不泄进 LAN 泛洪域”）；**4.19 的 eth0（xmac0）从未在 Linux 里跑通**——厂商 4.19 在同一只 jack 上也 0 字节（docs/07 补九十四）；U-Boot 曾在同一颗 RTL8226/同一根线上拉过 11 MB，说明 PHY/SerDes/XMAC 硬件通路本身通。

**23:08–23:16 复核（实测）**：`192.168.9.1` 断电前 4/4 通（avg 2 ms）；23:14:51–23:15:43 连续 `NO-REPLY` → 断电；23:16:05 恢复且计数归零（eth1 rx 从 469,848 掉回 16,107）⇒ 计数器每次断电重启，**做增量必须限定在同一次开机内**。
断电前 `jackmap.log` 连续样本里 **eth2 tx 每 22 s 涨约 2.2–2.4 kB（udhcpc 在重试），rx 恒 0**，eth0 rx/tx 几乎冻结，eth1 是唯一有来有回的口。**上行线仍未插入任何一只口**。这一条不解决，口径 A 的“实测出网”永远读不到结果。

---

## 2. 物理与时间约束（只有机主能配合，别绕）

- 板子插在**智能插座**上，**每 ~15.5–16 分钟断电 30 秒**。实测（`jackmap.log`）：`21:56 / 22:12 / 22:28 / 22:43 / 22:59 / 23:14`（22:59:26–22:59:51 `NO-REPLY`，23:00:14 恢复；23:14:51–23:15:43 `NO-REPLY`，23:16:05 恢复）。**22:59 这一刀之前的交接书漏了；docs/13 §0 的 `:09/:24/:39/:54` 也与实际不符，只认 `jackmap.log`。**
  **不是整刻钟，别按 :15/:30/:45 计划。** 锚点会随插座周期漂移，**每轮开始前用 `jackmap.log` 的 `NO-REPLY` 重新对表**。
- **机主是唯一的物理传感器**。要人挪线/看灯/按 RESET，就明确地提出来；不可逆步骤（写 flash、断电时机）默认**先要批准**，但机主确实会批。
- 子代理边界：**不许碰 ONU/串口，不许读 `C:\UGit\SSH`**；而且子代理会**编造板子的实时状态**，任何“设备现在如何”的说法必须自己重读。

---

## 3. 串口/网络仪器（直接用，别重造）

- 串口链路：`tools/serrelay.ps1`（COM3 115200）→ 日志 `staging/ctl/serial.log`，命令经 `staging/ctl/sercmd.txt`（**原子 rename** 投递）；`tools/relayloop.sh 6` 保活；`tools/armu4.sh N` 打满 1 s bootdelay（认 `staging/ctl/u.stop`）；`tools/tftpsrv.py staging/trial 69`。
- 已知坑（都踩过）：串口回显会把输入原样打回；**持续 `u` 流穿过 Linux 启动会强制 procd FAILSAFE**；被杀的 round 会把 streamer 变孤儿；`tail -c +N` 看不到“已经是最后一行”的 prompt（先 `send 'version'` 探一下）；U-Boot 里只有 `md`，没有 `echo`/rdreg。
- PC 侧判据：`ping -S 192.168.9.25 <target>`（**必须绑源**；Windows 是 `-w` 毫秒、`-n` 次数，写成 `-W 1500 4` 会报“值超出范围”而一个包不发）；Windows `arp -a` 输出是 GBK、MAC 全横线、回复行是大写 `TTL=`；`tools/pcrxwatch.ps1 -Seconds N -Every 5` 看 RX 增量。
- `tools/jackmap.sh` 的 `rx=`/`tx=` **都是字节**，不是包：`/proc/net/dev` 行首有空格 → `cut -d" " -f1` 得到空串，于是 `f3=rx_bytes`、`f11=tx_bytes`（不是 rx_packets）。**别用 `awk` 复核**——`awk` 默认会跳过行首空白，它的 `$11` 是 `tx_packets`，跟 `cut -f11` 差一位。所以“tx 一直涨、rx 恒 0”只说明**本端在发、对面没回**，不能读成“链路有流量”。
- 板子侧 applet 现状（**容易做出假阴性**）：busybox **没有** `pkill`/`arp`/`timeout`/`od`/`stat`/`nohup`/`--list`；`killall` 有；`ping` **没有** `-b`/`-f`，`-i SECS` 只吃**整数**（`-i 0.1` 会一帧不发就报错）；`setsid` 有（要分离进程用它）。
- vendor 观测口：`/proc/pon/{debug,tm_debug,switch_debug}` **写有效、输出落 dmesg**；TM 窗口约 400 行、含多套**索引不同**的端口表 → **必须整窗口取、按 counter 标签配对，绝不能按行号**。`sw_port 0..4` 有效、**4 = RTL8226（eth0 的 PHY）**；`in_sop_cnt` 列名是 `smac0 smac1 smac2 smac3 xmac0 xmac1 rgmii pon0(10G) pon1(1G)`；`/proc/tm/shell` 一律 `operation forbid`（ssh 和串口真 tty 都一样，且它 `$?` 恒 0 —— 成败只看 dmesg）。`/proc/gpon_sdk/debug` 读了会挂住。
  **这整套只在 4.19 厂商模块加载时存在；mainline 下没有，别把 docs/13 的 TM 读数当成 mainline 的仪器。**
- 离线反汇编：`tools/ponDis.py`、`staging/kofn.py`、`staging/kocalls.py` 已能解 sections/symtab/`.rela.text`；这是 §4.0 的 `switch.ko` 差集工作的工具。

---

## 4. 路线（按目标排序）

> **别自己发明构建路**：`ci-build-repo/.github/workflows/build-sk-d840n.yml` 已经就是“ImmortalWrt + 新内核”这条线的载体：`base = cnjn/ImmortalWrt`（分支 `sr1010-zx279133`），`kernel = cnjn/linux-mainline-zte-zxslc-sr1010`（`KERNEL_BRANCH=build`，`KERNEL_SHA` 已 pin，`KVER=6.18.38`），`format = huxiangjs/SK-D840N-OpenWRT 证明过的 flash 布局`。`mode=flash` 出 `uImage + dtb.bin + rootfs.jffs2` 三件（对应 mtd2/mtd3/mtd5），`mode=initramfs` 出 RAM FIT（kexec 试跑，不动 flash）。**构建不是瓶颈；产物在板上载不载流才是。**

### 4.0 新内核的出处、板级差异与真正的工作量

**新内核 = ZTE SR1010 那一套 mainline 支持**，参考仓 `https://github.com/huxiangjs/SK-D840N-OpenWRT`（内核侧 `cnjn/linux-mainline-zte-zxslc-sr1010`）。**SR1010 是路由器，SK-D840N 是光猫** —— 同一颗 SoC，板级与产品角色不同，**必须改内核适配**。

| 事项 | SR1010（基线，路由器） | SK-D840N（本板，光猫） | 已存在的适配 |
|---|---|---|---|
| 产品角色 | 路由器（WAN 电口） | **GPON ONU：4×RJ45 LAN + 光纤 PON，无 WAN 电口**；eth0 也是四个 LAN jack 之一 | 无；网络配置必须按“光猫”建模，`eth2` 当 WAN 只是实验室用法 |
| target 树 | `immortalwrt/target/linux/zte/` **只有 SR1010**；`FEATURES` **不含 jffs2**；image/Makefile 没有 `skyworth_sk-d840n` | root 是 **jffs2 的 mtd5**，需要 `skyworth_sk-d840n` 设备定义 | `ci-build-repo/overlays/target/linux/zte/{Makefile,image/Makefile}`（构建时 `cp -a` 注入，**故意不改主树**） |
| 板级 dts | `zx279133-sr1010.dts` / `zx279133-sr1010-openwrt.dts` | 分区表、LED、按键、MAC 来源（`/tagparam` 的 `0..7_mac_addr`，基址 `d0:45:8d:84:08:fc`）、`pon_rsvmem` 保留区都不同 | `kernel-overlays/arch/arm64/boot/dts/zte/zx279133-sk-d840n.dts` + `zx279133.dtsi` |
| 以太网驱动族 | SR1010 的 LAN 走 NPPT 路径 | **eth0 = NPPT + 外挂 RTL8226B（MDIO 地址 5，故意不给 `compatible`）**；四个 LAN 口挂在 SoC 内部 **9132 UNI 交换机**上。厂商由 **`switch.ko` 在模块初始化时直接写端口/VLAN 表**；`/dev/ethdriver` 的 ioctl 是 8 字节桩，**不是配置接口**（docs/07 补九十七）。不用 phylib，也不用 DSA | `kernel-overlays/drivers/net/ethernet/zte/zx279133-{main,mac,np,idm,netdev}.c`、`phy-zx279133-uni-serdes.c`、`unim.c` |
| PHY 芯片 | SR1010 上出过“把 `ZX279051` 驱动绑到 Realtek 芯片”的事故 | 本板是 **RTL8226B**，CI 的门要 `CONFIG_REALTEK_PHY=y`，**不是** `ZX279051_PHY` | `scripts/configure-sk-d840n.sh` 里 pin 符号 |
| PON 侧 | 无 | **GPON/XG-PON 全套（`zte_xgpon`/`gpondrv_xg`/OMCI）主线没有状态机**；新内核树只有 PON 数据通路（`ZTE_ZX279133_ETH` + mcode）。厂商 4.19 的 OMCI TX 门也还没打通（`swport_dev_xmit_fin` 读 `dev+0x8c8`，五个模块里只有读者没有写者；docs/07 补九十七） | 无（口径 B 的最大一块） |

**9132 / switch.ko 起步包（必经之路的具体入口）**：
- 板上 `/lib/modules/4.19.136+/switch.ko`（853,280 B，md5 `993f012407847954805d89efaf51110d`；**本地对应 `pon/vendor-modules/switch.ko`**）。**它的 init 路径写了哪些 `spa_indirect_*`/寄存器 entry，就是 mainline 缺的那张表**；`/dev/ethdriver` 的 ioctl 路线已被否证（桩函数），不要再走。注意 `staging/vendor-full/kmodule/switch.ko` 是另一版（md5 `fdeba014b9c8cddcfc28eaac9700bca0`），别拿错。
- 离线差集（task #42）：用 `tools/ponDis.py`/`staging/kofn.py`/`staging/kocalls.py` 把 `switch.ko` init 里的寄存器写站点抽出来，和 `zx279133-np.c` 的 `zx279133_lan_port_bringup()`（当前在 np.c:1246；mem5/mem10/mem15 的三处 `0x01f4000c` 在 1283/1292/1298）及 `zx279133_wan_port_bringup()`（1317+）做一张**差集表**；**差集非空才值得下一次 build + 窗口**。这是 docs/07 补九十七给出的下一步，不是再开一轮寄存器对照。
- 现成的观察面：`/proc/pon/switch_debug` 的 `sw_port_*`/`sw_vlan_*`/`tm_mactable_fdb_add`（写有效、输出落 dmesg）；`/proc/pon/tm_debug/sw_port_enable_get` 扫 `port=0..15`（port 4 = RTL8226）。
- 硬件事实：四个内部 PHY 0x0A–0x0D（OUI 0x212E54）就是 9132 的四个 LAN 口，CPU 侧是 nppt/XMAC0；U-Boot 的 `enter outerphy info init!`→`phy_id 0x5`→`identifier 0xc849`→`5 serdes option is 0` 是现成的正对照（docs/07 补九十四/九十六）。
- **最小目标**：不做 DSA/switchdev，只做 **CPU 口 + 4 个 UNI 口 + 固定 VLAN** 的小驱动；先把 switch.ko 的 init 写集对上，再谈驱动框架。
- **注意**：`np.c` 的 `lan_port_bringup()` 是给外部 RTL8372N 那种盒子用的路径，不是本板 jack 4 的 mem6；别把它的非零 entry 当本板目标。

**还没解决、需要继续改内核的三条硬账（按优先级）**：

1. **9132 UNI 交换机主线无驱动** ⇒ mainline 下 `/sys/class/net` 只有 `eth0 lo`，`br-lan` 是空桥，**四个 LAN 口全不工作**。不解决，任何“新内核”产物都不是可用路由器/光猫。**这是第一块，也是最大的一块。**
2. **PON 状态机/OMCI（口径 B）**：新内核树没有 G.984 状态机；厂商 4.19 有，但 OMCI TX 门未打通。要做光猫，这是比 9132 更长的移植。口径 A 可以显式排除。
3. **eth0 / jack 4（可选旁支）**：厂商 4.19 在同一只 jack 上也 0 字节；只有 U-Boot 发过 11 MB。`docs/13` 的 `smac1` 读数是在 **4.19** 上测的（原文：“在 4.19 上完全走得通”），**不是 mainline**；mainline 没有 `/proc/pon`，不能把这套读数当成 mainline 的实测。真要做，先做上面的 switch.ko 离线差集；不要从 NP 寄存器盲改，也不要把 eth0 当整个计划的门槛。

**换内核顺带会解决的一条**：mainline 有 nftables → `fw4` 能用 → 当前 4.19 上“内核没有 nf_tables、镜像和 feed 里又没有 iptables”的 NAT 死结自动消失。**路线 A（继续用 4.19）拿不到这个收益。**

**一句话**：`overlays/` + `kernel-overlays/` + `configure-sk-d840n.sh` 是“改内核适配光猫”的现有载体；新执行者要做的是**在这三层上继续加**（9132 最小驱动 + 若做光猫则 PON 状态机；eth0 可选），而不是把 SR1010 的树直接搬。

### 4.A ImmortalWrt userland + 现有 4.19（fallback，不是本目标）
- 内容：只换 `root`（ImmortalWrt 24.10/25.x 的 armsr/armv8 rootfs → 转 jffs2），kernel/dtb **不动**。**这不产出“新内核”。**
- 现状：当前 root 已经是能用的 OpenWrt 24.10.8；CI **没有** “ImmortalWrt userland + 4.19”的 build mode。要做就得自己组装：board.d/network/preinit、`/lib/modules/4.19.136+` 的 **38 个 .ko**（当前加载 34 个）、固件、`/sbin/mtd`/zrecover/zkey/zleds；并清掉现有 root 里 `6.6.144` 的 114 个 .ko，避免拿错树。
- 风险：新 root 起不来时没有用户态可 kexec，恢复要靠 U-Boot/tftp/串口；**这不是“几乎无风险”**。写 root 前先用 RAM initramfs 验证 U-Boot 恢复链，并确认 root 备份可回刷。
- 判据：`verify_router.sh` 5/5 + 至少两次纯冷启动；`uname -a`、`/proc/cmdline`、`lsmod` 记录清楚是哪套在跑。
- NAT：仍没有 nf_tables；要上网得另找 legacy iptables/libxtables（见 §5.5）。
- 光猫：4.19 的 PON/OMCI 用户态在，但 OMCI TX 门未打通（§4.0），**A 也不等于“可用光猫”**。

### 4.B ImmortalWrt + mainline 6.18（主路线）
- **构建侧已通**：`build-sk-d840n.yml`（`mode=both`/`flash`）能出成品；触发方式是 `workflow_dispatch`，或 push 命中 `build-sk-d840n.yml` / `kernel-overlays/**` / `overlays/**` / `configs/sk-d840n*` / `scripts/configure-sk-d840n.sh` / `templates/**`（**改 docs 不会触发**）。产物 release 指针 `skd840n_<mode>_<run_number>_<run_id>`。
- **顺序**：
  1. **先定口径 A/B**（§0）。
  2. **9132 / switch.ko 离线差集**（§4.0，task #42）：先做差集表，不急着写驱动；差集非空再动手。
  3. **9132 最小驱动**（CPU 口 + 4 个 UNI 口 + 固定 VLAN）：拿到 eth1/eth2/eth3，实验室口径就有一只能当 WAN 的口（eth2 的 22 MB 流量已被 4.19 证明）。
  4. **PON 状态机/OMCI**（仅口径 B）：独立工作流，别混在 9132 这一轮里。
  5. **用户态/NAT**：mainline 有 nftables，`fw4` 可用；这是相对于 4.19 的真实收益。
  6. **验收**（§5.1）。
- **IRQ 现状（纠正）**：kit 75→80 的三处寄存器值、`+0x2c8=0x12` 的 RMW、`NAPI_MASK` 加宽都已落到硬件，但 RX 中断计数仍为 0。**这不等于“路由/掩码这一族已被否证”**：docs/11 §4g 明确给出下一个要试的路由值 **`0x07ffffff`**（带流量状态下的实测值），以及最后一个结构性差异 **`+0x04/+0x08=0`**（冷交接时厂商 NP 的 ring base 就是 0）。这两个是最便宜的下一轮；但它们属于 **eth0 旁支**，不阻塞 9132。
- **独立 root**：kexec 的 initramfs 不挂 mtd5；如果非要挂，必须只读。不要用 mainline 写当前 4.19 的 jffs2 根。
- kit 75→80 的逐 kit 判据在 `docs/11`；参考 `docs/09`/`docs/10`/`docs/11` 的 IDM/NP 寄存器对照、U-Boot 的 `idm_tx_debug`/`idm_rx_debug`、`xmac_test … 0 2`。上游仓见记忆 *cnjn ZX279133 upstream repos*、*huxiangjs/SK-D840N-OpenWRT release v2.0*。

### 4.C 退回厂商 4.19 + 仅升级软件包（实验室 NAT 最快）
- 不是“新内核”，但成本最低、能最快把“能上网”落地。**前提**：先把 eth2 当 WAN 的实验室口径写清楚；NAT 用户态见 §5.5。

### 4.D 可选旁支：eth0 / jack 4
- **不是前置，也不是门槛。** 厂商 4.19 在同一只 jack 上 0 字节（docs/07 补九十四）；唯一发过的是 U-Boot。若仍想做，按 §4.0 的 switch.ko 离线差集来；不要在 mainline 的 NP 里盲改寄存器。

---

## 5. 已知待办（按优先级）

1. **先定口径 A/B**（实验室路由器 vs 可用光猫）。这是本计划的第一决策；不选，后面全是返工。
2. **9132 / switch.ko 离线差集**：用 `tools/ponDis.py`/`staging/kofn.py`/`staging/kocalls.py` 抽 `switch.ko` init 的 `spa_indirect_*`/寄存器写站点，和 `np.c` 的 `wan/lan_port_bringup()` 做差集；差集非空才 build。
3. **9132 最小驱动**：CPU 口 + 4 UNI + 固定 VLAN；不做 DSA。先在 4.19 上用 `/proc/pon/switch_debug` 抓端口/VLAN 表作为对照。
4. **PON 状态机/OMCI（口径 B）**：先做可行性评估——厂商 `zte_xgpon`/`gpondrv_xg` 的 ABI、PLOAM/OMCI 状态机、OMCI TX 门；新内核树只有数据通路。没有这一步，别把产物叫“可用光猫”。
5. **NAT 用户态**：当前 4.19 没有 nf_tables，`fw4/nft` 空转；镜像/feed 里没有 iptables 本体；内核有 legacy ip_tables + MASQUERADE。取库通道：离线拆 `restore_set/root_stock.bin` 的 `libxtables.so.12`（jffs2reader/jefferson；本机没工具，可丢 vps1/CI），或直接走 mainline 的 nftables。
6. **root 重刷工具链**：`staging/reflash-root/`（pad 校验过：root `bb6025d9`、uImage `f68ceb0f`、dtb `881af2d3`）+ `tools/ubflash.sh`/`flash-round.sh`/`postflash.sh` + **`ci-build-repo/tools/itbcheck.py`**（不是根 `tools/itbcheck.py`；FIT bring-up 顺序 byte-exact 校验，已在 kit 45/46/50 证过）。
7. **当前网络配置快照**：22:35 的 `network-backup` 是旧配置；再动之前 `uci export network > backups/network-$(date +%Y%m%d-%H%M%S).txt`。`wan6` 仍指 eth0，清掉或写明。
8. **root 无密码**：机主未表态；不要擅自设密码。

---

## 6. 规矩（上一任被纠正过的点，别再犯）

- **编译只在 CI**：本机**绝不**编译。**只有 push 命中以下路径才会触发构建**：`build-sk-d840n.yml`、`kernel-overlays/**`、`overlays/**`、`configs/sk-d840n*`、`scripts/configure-sk-d840n.sh`、`templates/**`。**docs-only push 不会构建**，所以“纯文档提交攒着以免占 release 指针”是错的；真正要攒的是上面这些会触发构建的路径，别把未验证的 overlay 推上去占住 release 指针。
- **正本只有一个**：`ci-build-repo/` 是 canonical（10-20 的 workflow，带 `KERNEL_SHA` pin 和 DEVMEM 修补）；`ci-wanport/` 是 10-16 的旧工作副本（workflow 没有 pin/DEVMEM），**不要 push 它**。
- `KERNEL_SHA` 已 pin（run 72 因为 branch-tip clone 炸出 21 个错）；顶层 `CONFIG_KERNEL_*` 会**覆盖** defconfig（`KERNEL_DEVMEM` 事故，run 63–67 全废），要在 build-root `.config` 里 defconfig **之后**再钉。
- 每个判定门都要有**正例 + 反例**；“0 命中”先怀疑自己的 pattern，而不是怀疑设备。
- 下结论前先报**身份**：`uname -r`、`/proc/cmdline`、`uptime`（分不清是哪套在跑就会得出错误结论，历史上错过）。
- 已经抓到的日志/寄存器**先挖再猜**；不要用替代物建模。
- 脚本一个用途一个文件（机主明确要求「不要写一起」）。
- 需要电源/挪线/按钥 → **停下来问**；但如果已有计划性断电窗口，先把流采样器架好去用那个窗口。
- 不要把运维越权“洗白”成证据：操作员否决门禁时，provenance 里必须写明是谁否决的。

---

## 7. 文件索引（接手时按这个找）

- 文档：`ci-build-repo/docs/01`…`docs/13`（`08` NP 地址空间、`09` U-Boot 已证 IDM 镜像、`10` ring 搬窗口、**`11` 厂商 Linux bring-up 逐字记录**、**`12` jffs2 dirent 损伤与修复 + 刷好的标准**、**`13` eth0 出口归因/upmode 否证/tm shell 否证/eth2 隔离/NAT 缺口**），`HANDOFF.md`（历史收口段）。
- **本轮（2026-10-21）的新文档**：`docs/20`（M2/PON 可行性，冻结为"研究"）、`docs/21`（NAT 路由器那一半 + 单线 M1）、`docs/22`（NAT 验收基线 + busybox udhcpc 的另一套约定）、**`docs/23`（9132 第一次在 mainline 里开机 + DSA 的 `.setup` 钩子 + relay 卡死）**、**`docs/24`（厂商活板的 9132 端口表 + `zx279133-9132` 运行时控制台）**、**`docs/25`（四个用户口的 PHY：树内 DSA/phylink 契约逐条引用 + 为什么 9132 不合那个范例 + fixed-link 的代价）**。
- 工具：`tools/verify_router.sh`（4 通道/5 判定）、`tools/eth0egress.sh`、`tools/jackmap.sh`、`tools/eth0jackwatch.sh`、`tools/boot419score.sh`、`tools/ubflash.sh`、`tools/ubram.sh`、`tools/reflush_root.sh`、`tools/onu2.cjs`、`tools/ponDis.py`、`staging/kofn.py`、`staging/kocalls.py`。
- **新内核这条线的本地落点**：`ci-build-repo/kernel-overlays/`（`arch/arm64/boot/dts/zte/zx279133-sk-d840n.dts` + `drivers/net/ethernet/zte/zx279133-{main,mac,np,idm,netdev}.c` + `.h` + `drivers/phy/zte/phy-zx279133-uni-serdes.c` + `drivers/mtd/nand/spi/unim.c`）、`ci-build-repo/overlays/target/linux/zte/`、`ci-build-repo/scripts/configure-sk-d840n.sh`、`ci-build-repo/tests/kernelsrc/arch_arm64_boot_dts_zte_zx279133-sr1010.dts`、`staging/src/zx279133-sr1010.dts`、`staging/src/fork-SR1010_MTD_WRITE_ACCESS.md`、`.github/workflows/build-sk-d840n.yml`；`configs/sr1010-extra.config` 是 SR1010 基线，**别拿它当本板产物**。
- **9132 / switch.ko 起步包**：板上 `/lib/modules/4.19.136+/switch.ko`（853 KB，md5 `993f0124…`；本地 `pon/vendor-modules/switch.ko`，同 md5；`staging/vendor-full/kmodule/switch.ko` 是另一版 `fdeba014…`，别拿错）；`/proc/pon/switch_debug`；`docs/07` 补七十七（内部 PHY 0x0A–0x0D、OUI 0x212E54）、补八十一（无 WAN 电口）、补九十四/九十五（厂商 4.19 也不发、U-Boot 11 MB）、补九十六（外部状态全一致）、**补九十七（`/dev/ethdriver` ioctl 是桩；差异在 switch.ko init；task #42 离线差集）**；`docs/13 §8`（`/proc/pon/*_debug` 的 setter）。
- **当前 root 的模块树**：`/lib/modules/4.19.136+` 有 39 项/38 个 `.ko`（当前 `lsmod` 34 个）；另有 `6.6.144` 的 114 个 `.ko`。route A 只拷 4.19 树，别把 6.6 树带进去。
- 数据：**`device-dumps/`（原厂全量导出，242 MB；含 `device-dumps.zip` 188,823,324 B，以及 boot/kernel0/mtd2/4/5/12/14/p11 分区 dump）**、`restore_set/`（原厂五件套恢复集）、`backups/`、`staging/vendor-full/`（厂商 initramfs 全量 + `MANIFEST-links.txt`）、`staging/vendor-root/`（arm32 厂商二进制所需最小库）、`staging/ctl/`（串口与采样日志）。
- **工作副本警告**：还存在 `ci-wanport/`（独立 git 仓库，HEAD `8ef6316`，workflow 10-16、没有 `KERNEL_SHA`/DEVMEM）。**canonical = `ci-build-repo/`；`ci-wanport/` 只读参考，不要 push。**
- 提交（截至 2026-10-20 23:16）：`git log origin/main..main` 有 **19 个 docs-only 提交**（净变更 5 个 docs 文件，954 insertions）；交接书此前只列了 10 个。以 `git log` 为准，不要在文档里维护固定清单。

---

## 8. 交接给新模型的 60 秒版

**目标**：SK-D840N（**GPON 光猫**）跑 **新内核 + ImmortalWrt**。新内核基线是 **SR1010 路由器**，不能直接搬；板级差异必须自己补。
**先选口径**：实验室路由器（以太网上行，不要求 PON）还是可用光猫（PON 注册 + OMCI）。**两者不是一个工作量。**
**构建不是瓶颈**：`build-sk-d840n.yml` 已能出 ImmortalWrt + mainline 6.18.38 的 flash 三件套（kit 75–80 已发过），瓶颈是**产物在板上载不载流**。
**新内核的主次**：① **9132 / switch.ko**（必经之路；四个 LAN 口里三个靠它）；② **PON 状态机/OMCI**（仅口径 B；光猫的真正上行）；③ **eth0/jack 4**（可选旁支；连厂商 4.19 都没把它跑通，唯一跑通的是 U-Boot，不要当门槛）。**9132 是真正的第一块骨头。**
**下一步**：先定口径；做 `switch.ko` init 与 `np.c` 的离线差集（task #42），差集非空再写最小 9132 驱动；口径 B 再加 PON 状态机评估。
**无论 A/B**：换内核要连 rootfs 一起出，别和 4.19 共用同一个 jffs2 根；**刷 root 必须先进 RAM 环境**；root 写入按标签 `mtd5`，不要被 mtd0 的 128M 误导（mtd5 实测 241M 到 256M；mainline dtb 则拆成 240M + 1M scratch）。
所有构建走 CI（push 只对指定路径触发；**canonical = ci-build-repo，不是 ci-wanport**），本机不编译。缺什么就明确向机主要：插线、看灯、按 RESET、批准写 flash。
