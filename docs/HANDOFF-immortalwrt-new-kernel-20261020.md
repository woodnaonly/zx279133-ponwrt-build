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

## 0.10 2026-10-21 中午 12:17–13:00：run102 让 lan1..lan4 出来了，帧一个都不过去（动手前先读这一节）

完整取证见 **`docs/26-lan-datapath-and-the-9132-truth-20261021.md`**。

**① run102（`99ad1c7`）是第一次四个口同时存在：**
DSA 注册成功，`lan1..lan4` 四个 netdev 都在（fixed-link 解开了 run100/101 的 PHY -ENODEV），
四颗 PHY 的寄存器第一次读全（`84b9:5032`，只有 0x0b 有链路 = PC 那只口），
PPU 1..4 的 SVID 写进去读回来是 `00ec0000 / 00f00000 / 00f40000 / 00f80000`（59..62 << 18，**对**）。
**但 PC ping 192.168.9.1 = 100% 丢包**，串口里每个用户口一次
`lanN: failed to open conduit lan-cpu0`，桥把四个口都置成 disabled。

**② 根因是 service 协议的两步没做，不是 9132：**
- `zx279133_lan_netdev_open()`（conduit 的 ndo_open）在 `eth->lan_datapath_ready` 为假时
  返回 **-ENETDOWN**；而 DSA 的 `dsa_user_open()` 第一件事就是 `dev_open(conduit)`
  ⇒ 每个用户口都开不起来。**`datapath_set_ready()` 从来没被调用过。**
- IDM 收包路径 `if (lan_source && !lan_dsa_active) { 丢帧 }` ——
  **`set_dsa_active()` 也从来没被调用过**，所以 LAN 侧的帧本来也会被丢。
- 树内 RTL8372N 驱动的顺序是：`datapath_set_ready(true)` → `dsa_register_switch()` →
  `set_dsa_active(true)`。**run104（`9d9805a`）照抄这个顺序。**

**③ 厂商活板量到的 9132 真值（`/proc/pon/tm_debug`，参数写法是 `key=value`）：**
- 厂商 **switch port 1 = smac_1（ram_id 2）= PPU 2 = PHY 0x0b = DSA 口 5 = lan2**
  （`sw_vlan_port_pvid_set port=1 vid=60` 落到了 `spa_port_attr_smac_1` 的 word2 = `0x00f00000`）。
- **SVID 的位段 = word2 bits 18..29**（= 厂商 `tm_port_pvid_set()` 的位域号 0x10），
  和我们 np.c 的 `PPU_PVID_VID_REG 2 / SHIFT 18` 一致，**这条不用改**。
- 厂商把 9132 跑成**纯扁平桥**：全口 `802dot1_en=0`、`pp_vlTagMsk=0`、`inframode=0`、
  隔离全 0；FDB 里只有 PC 的 MAC（`84:47:09:5b:bd:2c` → port_id 2）。
  厂商的 4 个 per-port 网口靠**硬件元数据**分源端口（`skbinfo: port 1`），**帧里没有 VLAN 标签**。
- ⇒ **厂商那条路（靠 MAC 学习定向）与 DSA 的路（靠 VID）不是同一条。**
  我们的收侧靠 IDM 把描述符里的源端口翻译回传输 VID
  （`zx279133_idm_rx_lan_vport()` / `zx279133_idm_rx_preserve_dsa_vlan()`，cnjn 的 idm.c 里已有），
  **发侧则要 9132 认 VID** —— 这是 run104 之后要验的下一件事。

**④ 工具坑：`tools/onewindow.sh` 的等窗口逻辑是坏的。**
它用 powermon.log **最后一行**（每 17 秒一行）当“上次上电”，于是“距上电 60~600 秒”这个条件
永远不成立，脚本会一直等。**改成找最后一次状态跳回 LINUX 的那一次**（`staging/ctl/wincheck.sh`
可以单测这个判定）。

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

- **编译只在 CI**：本机**绝不**编译。**只有 push 命中以下路径才会触发构建**：`build-sk-d840n.yml`、`kernel-overlays/**`、`overlays/**`、`configs/sk-d840n*`、`scripts/configure-sk-d840n.sh`、`templates/**`、`trial-files/**`、`tools/**`、`docs/**`（`.github/workflows/build-sk-d840n.yml:27-46`，逐条核对于 2026-10-26 00:52）。**本节此前写的「改 docs 不会触发」是错的**——`docs/**` 在第 46 行里，而且这句话还是 run126 那次「修了门禁却没起构建」之后补进来的。所以**等 release 的时候什么都别推**：一次文档 push 会起一个和你正在等的那次竞争的构建，抢同一批 runner。真正要攒的是「别在别人的 release 落地之前推会触发的路径」。
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

---

## 9. 2026-10-24：G2 的三个根因，和它们是怎么被测出来的

这一节是 2026-10-24 那一轮的结果，接在第 8 节的"60 秒版"后面。前面几轮
的判断都建立在"计数器好看 = 数据面是好的"这个假设上，**这个假设是错的**。

### 9.1 四个独立的故障，缺一不可

| # | 故障 | 症状 | 修在哪 |
|---|---|---|---|
| 1 | PPU 跑的是 **SR1010 的微码**（树里那份），不是本板厂商 blob | 帧停在 PPU 里：ISU 收 3111、进 PPU 427、ODMA 0 | `51ac6c6`：CI overlay 换成本板 `mcode_133.bin`（CRC `0xc839b946`）+ 窄 `.gitattributes -text -diff` + 驱动开机校验 CRC |
| 2 | RX 帧**没经 DSA tagger 派发**，直接丢给 conduit `lan-cpu0` | 帧到了 CPU 口就没人管；栈按 ETH_P_XDSA 丢掉 | `51ac6c6`：IDM 接收路径调 `cpu_dp->rcv()`，协议在 tagger 之后、在**端口 netdev** 上取 |
| 3 | **RX：帧不在缓冲区的头** —— 描述符指的是"缓冲区"，帧在它后面 0x80 | 帧到了、长度对、元数据对、计数器全对，但栈拿到的是页的**旧内容** | `1ddcc4b`/`ecfe452`：新增 `ZX279133_IDM_RX_FRAME_OFFSET`，data / prefetch / XDP / cache 失效 / 设备侧失效 五处一起挪 |
| 4 | **TX：剥 transport VLAN 的偏移错了 2 字节** | 板子**确实回了包**（桥学到 PC 的真实 MAC、ARP 表里有 PC），但线上那 56 字节的 ARP reply **被剪掉了以太类型和 htype**，PC 的 IP 栈丢掉 | `8c5cc82`：`memmove` 用 `2 * ETH_ALEN` 而不是 `ETH_HLEN` |

第 3 个故障的证据链（全部来自本板自己的字节）：

- run153 的描述符转储：`dma 84265100 len 92 word1 0782005c meta1 c0a801ff`。
  **描述符是对的**：地址就是驱动交给硬件的那块页 +0x100，长度 92，
  `meta1` 里 `c0 a8` 就是 192.168。
- run160 的诊断把整页打了出来，**帧就在里面**：

      page +0180: ffffffffffffff8447095bbd2c08060001
      page +0190: 0800060400018447095bbd2cc0a80919
      page +01a0: 000000000000c0a80901000000000000

  完整读出来就是测试 PC 发来的 ARP request：目的 ff:ff:ff:ff:ff:ff、
  源 84:47:09:5b:bd:2c、0806、htype 1、ptype 0800、hlen 6、plen 4、
  oper 1、sha=PC、spa=c0a80919、tha=0、tpa=c0a80901。
- 另一帧（IP/UDP，里面是 HTTP GET，179 字节）**同样在 +0x180**：
  `01:00:5e:7f:ff:fa`（组播） / `84:47:09:5b:bd:2c` / `0800` / `4500` /
  TTL 64 / proto 17 / `c0a80119` → `efffffa`。

**=> 硬件把帧写在了描述符所指地址的后面 0x80 处；驱动一直读缓冲区的头，读到的是
那一页上一次的残留（一次是 initramfs 的 ASCII 文本，一次是 0xff，一次是零）。
计数器全程正常，因为帧确实到了。**

### 9.2 同一处的另一个真 bug：cache 失效区间
`page_pool_dma_sync_for_cpu(pool, page, offset, len)` **会自己加 pool 的
`.offset`**（这个 pool 建的时候 `.offset = PAYLOAD_OFFSET`），所以它实际失效的是
`[dma + offset + pool->p.offset, +len)`。要失效帧所在的那一段，参数必须是 **0**；
`2d11bbd` 把它改成了 `PAYLOAD_OFFSET`，区间就落到帧后面 256 字节。

**但它不是病因**：run160 的诊断把帧打了两遍（数据面看到的那一遍，和整页强制失效
之后的那一遍），**两遍完全一样**，页转储也和它们一致 —— 缓存并没有陈旧，错的只是
**读的位置**。陈旧缓存和读错位置在栈那一侧长得一模一样，这就是前半个回合一直在
追 cache 的原因。

这个区间仍然必须修：帧挪到 +0x80 之后，要失效的是
`[dma + 0x100 + 0x80, +len)`。现在用的是底层调用
（`__dma_sync_single_for_cpu()`），**不再问设备"要不要失效"**（那个标志由设备树
决定，这块芯片无论如何都不与 CPU 缓存一致）。

### 9.3 第四个故障：板子发出去的帧被剪掉了 8 个字节

RX 修好之后，板子**确实在回包**了：桥学到的是 PC 的**真实 MAC**（`84:47:09:5b:bd:2c`，
端口 2），板子的 ARP 表里也有 `192.168.9.25 → 84:47:09:5b:bd:2c`，`lan-cpu0 tx` 和
`lan2 tx` 都在涨 —— 但 PC 一个回复也收不到。

用 Windows 的 `pktmon` 在 PC 自己的网卡上抓到了线上那一帧：

```
D0-45-8D-84-09-00 > 84-47-09-5B-BD-2C, ethertype IPv4 (0x0800), length 56: IP0 (invalid)

  +000  84 47 09 5b bd 2c | d0 45 8d 84 09 00 | 08 00 | 06 04
  +010  00 02 | d0 45 8d 84 09 00 | c0 a8 09 01 | 84 47 09 5b
  +020  bd 2c | c0 a8 09 19 | 00 00 00 00 00 00 | 00 00 00 00
```

按 ARP reply 读，这是**回包的尾巴**：hlen 06、plen 04、oper 0002、
sha=板子、spa=192.168.9.1、tha=PC、tpa=192.168.9.25 —— 板子在正确地回答 PC，
但 ARP 载荷的**前 4 个字节（htype 0001、ptype 0800）不见了**，以太类型的位置上是
**0800**（那正是 ptype）。PC 的 IP 栈把它丢了，网卡却把它算成收到的单播包 ——
所以它是"收到但不认"。

**两处各剪掉 4 字节：**

1. **驱动的 `zx279133_lan_strip_transport_vlan()` 偏移错了 2 字节。** 标签在
   `2 * ETH_ALEN` 处，是 4 字节：TPID、TCI、然后是帧自己的以太类型。摘掉标签要
   把它后面那段**前移 4 字节**，也就是从 `2 * ETH_ALEN + VLAN_HLEN` 拷到
   `2 * ETH_ALEN`。代码用的是 `ETH_HLEN`（14）—— **偏了两字节**：拷贝从以太类型
   开始而不是从它后面的载荷开始，于是 TPID 留在以太类型的位置上、以太类型被丢掉。
2. **9132 的 CPU 口随后又剪了 4 字节**：它在偏移 12 看到一个 TPID，就把它当成
   标签又摘了一次。

RX 侧的 `zx279133_idm_rx_restore_lan_l2()` 用的是 `2 * ETH_ALEN`，**一直是对的**；
错的只有发送侧。

### 9.4 G2 的结果（run165）

8c5cc82 之后，run165 第一次把 G2 走通：

    23:15:34   sent=4 received=4 lost=0 answers from the board=4   (洪水前)
    23:15:39   flooding 300 pps for 10 s, 3000 frames
    23:15:56   sent=4 received=4 lost=0 answers from the board=4   (洪水后)

    br-lan      rx 3002 pkts   tx 2
    lan-cpu0    rx 3002 pkts   tx 2
    lan2        rx 0           tx 2

桥学到的是 PC 的**真实 MAC**（不再是 ASCII 文本），板子的 ARP 表里有 PC，
两个方向的帧都对得上。

### 9.5 顺带修掉的编译错误
`dma_sync_single_range_for_cpu()` 在 6.18 是**五个**参数
`(dev, addr, offset, size, dir)`，诊断代码给了四个 → run156/run157 根本没编译过。
CI 的 Actions 日志匿名访问 404 又看不到编译器原文，只能去翻那个内核 SHA 的
`include/linux/dma-mapping.h` 才确认。

### 9.6 这一轮逐项排除掉的东西

- **帧确实来自 PC 那个物理口**：`echo "trapall 2 0" >/d` 之后驱动一帧都收不到。
- **协议陷阱表没问题**：`trapall 2 2`（全 trap）送到的帧数和默认一样多。
- **端口属性字与厂商逐字一致**：`a0=0000a6a5 a1=0000088f a10=0fa0000e
  a13=00008008 a19=96040001`（docs/15 的厂商清单）。
- **IDM 自己的配置也是厂商的**：控制字 0x01ff1007、descriptor mode 0、深度、
  buffer 池指针，逐字相同。
- **BMU 的 BPPE 表由硬件维护**：控制台用四种格式写进去，读回来都一样。
- **MAC 宽度的 ERAM 表（0x1e/0x21/0x2b）和 128 位表全空**。
- **SA-lookup 的位（bit 28 / bit 31）改与不改结果一样**。
- **"帧在 BMU 自己的缓冲池里"是误判**：帧就在描述符指的那块页里，只是往后 0x80。
  池扫描只找到 0xff 填充。
- **板子 loader 里的 0x40 不是红鲱鱼**：同一种现象、不同的数（loader 的网络栈是
  **PON** 的，PON 帧在缓冲 +0x40）。错的是由此得出的"帧在别处"的结论，不是观察。
- **厂商模块也不信 CPU 缓存**：`np_133.ko` 自带 `zxOsCacheInv` / `zxOsCacheFlush`，
  只在 `se_ddr_data_read()` / `se_ddr_data_write()`（读写交换引擎自己的 DDR 窗口）
  里调用。那是硬件窗口而不是主机接收缓冲，不能直接当结论，但说明厂商对这个芯片的
  缓存一致性是当真的。

### 9.7 实验台的坑（这一轮踩的）

- **插座断电会毁掉一轮验收**：智能插座每 ~15–19 分钟断电 30 秒。验收中途被
  断，量到的是"板子没了"。验收前先问板子 `uname -r`，并支持多次重试。
- **串口 relay 的 DTR/RTS 复位会把 CH340 的接收路径卡死**：复位后板子完全
  不出声，但板子**有电**（网口 1 Gbps Up）。`tools/relayctl.ps1 stop` +
  `start` 重开串口即恢复。**板子没声音时，先重启 relay，再怀疑板子。**
- **relay 死了和板子没电是两回事**：relay 挂掉时串口一个字节都没有，但 PC 的
  网口**仍然是 Up**；板子没电时网口是 **Disconnected**。先看网口状态再决定
  动哪一边。
- **trial 内核的 `reboot` 会把板子卡死**（不回 loader，网口还 Up，但什么都不
  响应）。`tools/hardreset.ps1`（按住复位线 4–5 秒）也不行的时候，只能等插座
  真的断电。**跑 G2/G3/G4 时不要对 trial 内核发 `reboot`。**
- **取消后台任务要连子进程一起杀**：否则被取消的轮次还在往串口发命令。
- **验收必须短**：`accept3.py` 要读 7 段 NPPT 计数器 + 14 个 SPA 端口属性字
  = 160 秒串口流量，断电窗口里很容易被打断。新的 `accept4.py` 只问 G2 真正
  要问的东西，约 1 分钟。
- **Windows 的 ping 会把本机 ICMP 不可达算作 "Received"**：板子完全不应答时
  PC 上打出来是 "Received = 2"。判定必须数**指名板子地址的回复**
  （`Reply from 192.168.9.1:`），不能数包。
- **PC 的静态 ARP 必须指向 trial 内核当次的 MAC**：trial 内核没有工厂 MAC
  （`eth_hw_addr_random()`），**每次启动都不一样**；而 PC 上那个
  `3e-8e:98:91-64-2a` 是**厂商 4.19 系统的**地址。地址对不上时单播被桥泛洪，
  板子一帧都收不到。`accept4.py` 现在先从串口读板子当次的地址再装条目。
- **改完运行时状态要复原**：`trapall` 的实验把 trap 关掉后，后面几次实验
  实际是在"一帧都不来"的状态下做的，结论全部作废。
- **后台任务有 1 小时上限**：CI 一次要 1.5–2 小时，所以"等 CI"和"取件+引导+
  验收"必须拆成两个任务。
- **prepush 的宏门禁能挡住编译不过的改动**：撤回一个常量时漏了一处引用，是
  它抓出来的（`prepush: 14 files, 1 problems`）。**别跳过 prepush。**

### 9.8 本轮新增的工具

- `staging/ctl/accept4.py` —— 短的 G2 验收（ping / 各口计数 / 桥转发表 / 板子 ARP 表），
  **会先按板子当次的 MAC 装 PC 的静态 ARP**。
- `staging/ctl/setmac.py` —— 只做那一步（板子重启后 MAC 变了时用）。
- `staging/ctl/rxuprobe.py` —— 让板子打印它收到的下一帧（`rxb` 命令）。
- `staging/ctl/pageimg.py` —— 从**原始串口日志**把整页重建出来（控制台的渲染会
  吞/串字符，页转储必须从原始字节看），并找出帧头的位置。
- `staging/ctl/q.py` —— 串口上问板子一句话，打印回答。
- `staging/ctl/bytecount.py` —— 芯片自己的字节计数器：PC 发 N 帧，看芯片数到多少。
- `staging/ctl/stacksize.py` —— 栈看到的每帧多少字节。
- `staging/ctl/bppemode.py` / `trapexp.py` / `mfbit.py` / `eramdump.py` ——
  运行时改 9132 的表并看结果（不必等一次 CI）。
- `staging/ctl/g2run158.sh` / `g2run4b.sh` / `g2run4c.sh` / `g2second4.sh` ——
  无人值守 G2 轮次，带断电重试。
- `staging/ctl/fetchit.sh` —— 只等某一个 release 出现就取那一份（`fetchrun.sh`
  会等两个 release 都到齐，flash 打包失败会把 initramfs 也拖住）。
- `staging/ctl/waithenrun.sh` —— 等板子回到链路上，立刻开始这一轮。
- `staging/ctl/cbrace.py` / `ko_calls.py` —— 括号配平自检；查厂商 .ko 里某个
  符号的调用点（厂商模块自带 `zxOsCacheInv` / `zxOsCacheFlush` 就是这么找到的）。
- `staging/ctl/pingboth.py` —— ping 的同时读**板子两侧**的收发计数：
  板子发出几帧、PC 收到几帧，两边对不上就是帧在路上丢了。
- `staging/ctl/sniffpcap.py` / `sniffround.sh` —— **用 Windows 的 `pktmon` 在 PC
  自己的网卡上抓线上帧的原始字节**。`pktmon etl2txt` 只给解码不给字节，要
  `pktmon etl2pcap` 导成 pcapng 再自己解（pcapng 的 block 链，EPB 的偏移 28）。
  `--pkt-size 0` 只抓头，要 `--pkt-size 256`。**板子发出的帧只有这一种办法看得到**。
- `tools/hardreset.ps1` —— 真正按住复位线 4–5 秒。

### 9.9 文档

- `docs/75` —— 本板微码 + DSA 派发（`51ac6c6`）
- `docs/76` —— blob 的 PORT_FLOW tag 说了什么；对 docs/74 的更正
- `docs/77` —— **帧不是帧**：第三个根因的完整证据链、"帧在 BMU 池里"为什么是
  误判、loader 的 0x40 为什么不是红鲱鱼，以及那一处 cache 失效区间
- `docs/78` —— **kernel 分区只有 8 MiB**：`mtd write` 静默截断、U-Boot 只认 gzip、
  zopfli 是唯一出路，以及 CI 的尺寸门禁
- **索引补齐（2026-10-26 核对：`docs/74`…`docs/89` 磁盘上全在，无悬空引用）**：
  `docs/74-the-board-s-port-flow-table`（端口流表，被 76 更正）、
  `docs/79-the-flashed-image-had-no-firewall`（刷进去的镜像没有防火墙，所以不转发）、
  `docs/80-the-ci-failed-on-the-compiler-s-command-line`（zopfli 的三个不存在的选项）、
  `docs/81-the-console-can-change-the-network-and-it-was-a-glob`（「串口 uci parse error」实为 shell glob）、
  `docs/82-the-build-hung-twice-and-the-ceiling-was-in-the-wrong-place`（上限该在步骤里面，不是步骤上）、
  `docs/83-the-build-publishes-its-own-progress`（`ci-progress/<run>-<mode>` 分支，git 不耗 API 配额）、
  `docs/84-the-watchdog-must-see-a-copy-that-kept-its-modification-time`（`cp -a` 保 mtime ⇒ 该问 ctime）、
  `docs/85-a-background-process-in-the-build-is-what-stopped-the-build`（uci-defaults 拉起的永不退出的进程占着 make 的 jobserver socket）、
  `docs/86-the-ci-release-flashed-and-booted-twice`（run181：CI 自己的 release 刷进 flash、两次冷启动 14/14+5/5；**当前留的是模板，等 run182 的证据**）、
  `docs/87-the-gate-was-asking-the-wrong-question-about-the-nat-rule`（问名字，不问字节）、
  `docs/88-the-gate-refused-a-good-image`（`strings | grep -x` 会拒掉所有镜像）、
  `docs/89-the-init-script-that-was-never-executable`（**一个模式位**：镜像里的 init 脚本从来没被执行）。

### 9.10 G3（NAT / M1）：结果与这一轮修掉的五个 bug

**2026-10-25 01:18–01:22，run165 的 trial 内核，G3 全项 PASS。** 证据在
`staging/ctl/g3nat6.txt`。

板子的路由器功能**自始至终是对的**——五个 bug 里三个在 PC 侧的 bench 脚本，两个在
板子的 `S27labclient`。下面是这一轮的实际做法和证据：

| 步骤 | 证据 |
|---|---|
| 串口告诉板子 `/etc/labwan=pc` | 板子把 lan2 从 br-lan 里移出来当 WAN |
| 板子 WAN 拿 DHCP 租约 | **192.168.137.193**（PC 侧 ICS 的租约） |
| 板子自造 veth + netns 客户端 `labcli0` | 拿到 LAN 租约 **192.168.9.179/24** |
| 客户端 ping 公网 | `223.5.5.5 -> RC=0 ... 1 packets received` |
| conntrack | `src=192.168.9.179 dst=223.5.5.5 ... src=223.5.5.5 dst=192.168.137.25` → masquerade 成立 |
| nft masquerade 计数 | 7 包 |
| lan2 的 tx 计数 | 50548 字节 |
| `ip_forward` | 1 |

结束后板子被脚本恢复成 LAN 模式（`killall udhcpc; ip link set lan2 master br-lan;
ip addr flush dev lan2; ip route del default; rm -f /etc/labwan /tmp/labwan.port`），
板子回到 192.168.9.1。

**这五个 bug（下划线的是板侧的）：**

1. **`nohup` 不在这个镜像里**（busybox 没有 nohup applet）→ `ash: nohup: not found`，
   于是 `S26nat` **从来没运行过**。改用 `setsid`。
2. **ssh 的 stderr 被 `2>$null` 吞掉** → 板子不可达的时候脚本**静默无输出**，
   看起来像"没反应"。现在保留 rc 并打印出来。
3. **ICS 一开就抹掉网卡上的 `192.168.9.25`**（ICS 会重配网卡）→ 每次 ssh 前
   `Ensure-BenchIp` 补回并探测。
4. **`S27labclient` 把地址表里的空格也删了**（`tr -d '\r\n '`）→ 变成一串非法地址。
   改成 `tr -d '\r\n'`。
5. **`S27labclient` 用 `1 received` / `bytes from` 判成功**，但 busybox ping 的末两行
   是 `1 packets transmitted, 1 packets received`。改用 **ping 的退出码**，并保留完整输出。

**顺序必须是「先串口下令，再开 ICS」。** ICS 开着的时候到 192.168.9.1 的 ssh 连试 8 次
全败，ICS 一关立刻就通——所以板侧下令走**串口**（不依赖网络），顺序也不能反。

### 9.11 G4（刷 flash）：8 MiB 分区、zopfli，和这一轮踩的坑

G4 的拦路虎**和驱动、NAT、内核都没关系**：CI 产出的 flash kernel 镜像
（8,395,923 字节）**比 kernel 分区（8 MiB = 8,388,608 字节）大 7,315 字节**。

- **`mtd write` 会静默截断**，写完还报 success。**只有 `mtd verify` 抓得到**——第一次
  刷机三个分区全部"成功"，板子却起不来，最后是 kernel 分区的哈希和镜像的哈希不一样
  才暴露出来。**任何刷完都必须 `mtd verify`。**
- 分区表和 `bootargs` 都在 NAND 的 `boot` 分区里（刷写器不碰 `boot`），所以**分区改不了**，
  镜像必须缩。`root=/dev/mtdblock5`，主 line dtb 的分区编号（boot=1/kernel=2/dtb=3/
  parameter=4/root=5/scratch=6）和 env 的 `mtdparts` 完全一致，**dtb 的 root 240M + scratch 1M
  正好等于 env 的 root 246784k**。
- **U-Boot 只支持 gzip。** 用探针 uImage 逐类型 tftpboot + bootm 实测：bzip2 / lzma /
  lzo / lz4 / zstd 全是 `Unimplemented compression type N`。U-Boot 是 stripped，没有符号可查。
- **zlib/gzip 任何参数都压不动**（level9 memLevel9 = 8,397,374，Z_FILTERED = 9,271,933）。
- **zopfli 是唯一出路**：产出的是**标准 gzip 流**（板子的 gunzip 直接认），而且
  **内核本体位不变**（刷进去的就是试车用的那个内核，验收证据不打折）。
  8,395,923 → **8,069,601 字节，余 319,007**。不加 `--splitting` 是 8,199,167（还差 189 KB）。
- CI 侧已加：`zopfli -q --i5 --splitting --gzip` + `gzip -dc | cmp -` 自检 +
  **8 MiB 尺寸门禁**（超了直接 `::error::` 失败，绝不再发布会被静默截断的镜像）。
  板侧的 `templates/flash-sk-d840n.sh` 本来就有尺寸检查，但那是在板上、发布之后。

**这一轮踩的坑（PowerShell / 串口 / 插座）：**

- **`mtd` 的参数顺序是 `<imagefile> <device>`**，不是反过来。`mtd write kernel /tmp/img`
  只会打印 usage，什么都不写，**而且在只检查整条链退出码的脚本里还会当成成功**。
- **PC 的 WLAN 网卡上有一条 `192.168.9.1` 的静态 ARP**（早期实验留下的），
  它让**所有没有绑定源地址**的去板子的包都从无线出去——板子明明在回 ping，
  `ssh` 却报 `connect to host 192.168.9.1 port 22: Connection timed out`。
  现在所有工具都绑源地址：ssh `-b`、ping `-S`、curl `--interface`。
- **`/dev/kmsg` 是流设备**，`grep ... /dev/kmsg | tail` 会**永久阻塞**。用 `dmesg`。
- **PowerShell 里 `& ssh` 会解析到同名函数** → 必须写 `ssh.exe`；函数里不能用 `$args`。
- **智能插座每 15.5 分钟断电 30–36 秒**。跨越窗口的操作会被打断：
  `mtd write` 被打断不会砖（mtd2 可重写），但**引导 trial 内核 + 刷 flash 必须挤在
  同一个窗口里**，否则白干一轮。
- **绝不要给 trial 内核发 `reboot`**（会把板子卡死，不回 loader）。冷启动用
  `tools/hardreset.ps1`，或干脆不干预等插座断电。
- **relay 的 DTR/RTS 复位会把 CH340 接收路径卡死**：复位后板子完全不出声但有电
  （网口 1 Gbps Up）→ `tools/relayctl.ps1 stop` + `start 21600` 重开串口即恢复。
  **板子没声音时先重启 relay，再怀疑板子。**
- **relay 见到 U-Boot banner 发空格会打断 countdown**，板子就永远停在 `=>`、从不自己
  启动 flash 里的镜像 → `staging/ctl/ser-nostop` 开关文件（保持存在）。

### 9.12 G4 的结果（2026-10-25 01:35–03:05）

**G4 通过：板子自己从 flash 起来两次，`verify_router.sh` 5/5。**

刷的是 run165 的三件套（root/dtb/kernel），kernel 是本机 zopfli 重压过的
`staging/trial/flash-uImage.gzipfit`（8,069,601 字节，余 319,007）。写入顺序
root → dtb → kernel，`mtd verify` Success，另外**独立读回分区**再算一遍 md5
（`329f1bbc1b9b5af2d6d5ba634c2d3bdf`）也对上——「写进去了」和「它能启动」是两件事。

两次冷启动都由智能插座完成（02:19:04 断 → 02:19:40 上电；02:35:10 断 →
02:35:10 上电），**串口全程没有对板子发任何东西**（`staging/ctl/ser-nostop`
存在，relay 不打断 countdown）。第二次冷启动后 **14 秒**板子就能 ssh 答话。

| 项 | 冷启动 1 | 冷启动 2 |
|---|---|---|
| ping | 4/4 | 4/4 |
| `uname -r` | 6.18.38+ | 6.18.38+ |
| `/proc/cmdline` | `root=/dev/mtdblock5` | 同左 |
| `/proc/mtd` | mtd2=kernel(8 MiB)、mtd5=root | 同左 |
| 根文件系统 | `/dev/root on / type jffs2 (rw,noatime)`，可写 | 同左 |
| 分区读回 md5 | kernel/dtb 都对上 | 同左 |
| LuCI | HTTP 200 | HTTP 200 |
| 串口 flood | 0 行 | 0 行 |

`tools/verify_router.sh`（原版）：**5 pass / 0 fail — ROUTER IS USABLE**。
证据：`staging/ctl/accept-g4-coldboot1.txt`、`accept-g4-coldboot2.txt`、
`accept-g4-verify_router.txt`。

**回退路径可用**：`staging/rollback/flash-uImage`（厂商 4.19，4,814,777）+
`staging/rollback/flash-dtb.bin`（48,463）+ `staging/rootfs.jffs2`
（11,812,720，md5 `0fd03a1115374f5335d7fc207b14a849`，与刷写器里记的一致）——
三件都在 PC 上，回退就是把 mtd2/mtd3/mtd5 写回去。

**但刷机后的镜像还不是路由器**：它**没有 firewall4**（这个构建没有 feed 包），
所以没有 masquerade 规则 → 能开机、能起 LuCI、能发 DHCP 租约，**但转发不出去**。
已经补上：`overlays/.../base-files/etc/init.d/sk-nat`（装 nft 规则）+ 
`etc/uci-defaults/99-sk-nat`（把它链进 `/etc/rc.d`），CI 里有断言。见 **docs/79**。

**这一轮新踩的坑：**

- **PC 的 WLAN 网卡上有一条 `192.168.9.1` 的静态 ARP**（早期实验留下的）。
  它让**没有绑定源地址**的去板子的包都从无线出去——板子明明在回 ping，
  `ssh` 却报 `Connection timed out`，`curl` 也超时。**所有工具都要绑源地址**
  （ssh `-b`、ping `-S`、curl `--noproxy`），而且**脚本里的 ssh 也要绑**
  （漏了一处就整轮白跑）。
- **本机有 `HTTP_PROXY`/`HTTPS_PROXY`/`ALL_PROXY`**，而且 `NO_PROXY` 里的
  `192.168.*` 对 curl 不生效 → curl 把去板子的请求发给了代理 → 超时。
  **`curl` 必须加 `--noproxy '*'`**；git bash 里的 `curl` 是 mingw 版
  （`/mingw64/bin/curl`），Windows 自带的是 `curl.exe`，两者行为不同。
- **git bash 里的 `sh` 不可靠**：`sh tools/verify_router.sh` 一条都不输出、
  退出码 0；换成 `bash` 或 `dash` 就正常。**用 `bash`。**
- **`/etc/config/network` 的改动会被 `uci commit` 落进 flash**。手工改（没 commit）
  也一样会被 netifd 的 reload 应用掉 —— 改完板子的 br-lan 立刻掉成
  NO-CARRIER，板子从网上消失，**只有串口救得回来**（`staging/ctl/q.py`）。
  改板子的网络配置前先想好怎么用串口改回来。

### 9.13 run167 / run168 的两个病因，和 CI 的防挂死改造（2026-10-25）

**run167 和 run168 不是同一个失败。** run167 停在 `Package flash images`
（`::error::zopfli produced nothing`），run168 停在 **`Compile` 步骤**，
19:04 开始、00:57 被 job 自己的 6h 上限砍掉，**5h53m**。两个 job 一样。
所以 run168 根本没走到压缩那一步。

对比同一仓库前两次 run 的 `Compile` 耗时：**166 是 62:20 / 69:05，167 是
51:14 / 32:55**（分钟）。33–69 分钟是正常，5h53m 不是「慢」，是**卡住**。

**病因一：zopfli 的命令行有三个不存在的选项。** build 里写的是

```sh
"$ZOPFLI" -q --i5 --splitting --gzip -o "$IMG" "$RAW"
```

Ubuntu 仓库里的 zopfli 是 1.0.3，它的命令行只有
`-h -c -v --i# --gzip --zlib --deflate --splitlast`——**没有 `-q`、没有 `-o`、
没有 `--splitting`**。命令自己拒绝了自己的参数，什么都没产出，下一行只能
报「文件不在」。**报错指向 zopfli，过错在调用方式。**

**改法：改用 zopfli 的 Python 绑定**（`ci-build-repo/tools/mkgzip.py`）：

```sh
python3 tools/mkgzip.py "$RAW" "$IMG" 10
```

- 绑定是**按名字**给参数的（`numiterations` / `blocksplitting`），
  `blocksplitting=True` 正好是值 1.5% 的那个选项（8,199,103 → 8,069,537）。
- **CI 免编译**：PyPI 上 zopfli 0.4.3 有 `cp310-abi3-manylinux2014_x86_64`
  wheel，ubuntu-22.04 的 python3.10 直接装。
- **和已验收的镜像逐字节一致**（本机实测）：同一内核跑 `tools/mkgzip.py`
  得 8,069,537 字节 payload，与**板子上那个已通过验收的镜像的 payload
  完全相同**（`staging/ctl/checkmkgzip.py` 的 MATCH 输出）。**CI 和本机
  现在产出的是同一个镜像**，板子上挣来的验收对 CI 出的片仍然成立。
- 内核本体一个字节没动，变的只是外面的容器。

**病因二：编译卡死。** OpenWrt 的 build 要联网取东西，**git 没有默认超时**
——连接建好之后不送数据，fetch 就无限期挂着。这正是「六小时没输出的 build」
的形状。

三处改造（都不改变构建出来的东西）：

- `Compile` 步骤加 `env: GIT_HTTP_LOW_SPEED_LIMIT=1000` +
  `GIT_HTTP_LOW_SPEED_TIME=60` + `GIT_TERMINAL_PROMPT=0`：
  **传输低于 1 KB/s 持续 1 分钟就放弃**，卡住的 fetch 变成会失败的 fetch。
- `Compile` 步骤加 `timeout-minutes: 200`（job 的 6h 留作外层兜底）。
  200 是最差正常 run（69 分钟）的三倍，**只在已经出问题时才触发**，而且
  触发时点名是哪个步骤停了，不会再把整个 job 报成 "cancelled"（那个词
  什么也没说：慢、抓包、还是内存不够）。
- `Compile` 在慢速诊断 pass 之前**先重试一次并行 build**。`make` 是增量的，
  第二次接着第一次断的地方走；失败常常只是一次卡住的 fetch。

**这一轮最该记住的：** 一个**步骤**能报出自己的超时、一个 fetch 被允许失败，
才能把「构建停了六小时什么都不说」变成「构建停了，停在这里」。
详见 **docs/80**。

**新文件**：`ci-build-repo/tools/mkgzip.py`（CI 与本机共用的压缩脚本）。
**新坑**：CI 里 `python3 -m pip install` 要加 `--user`（runner 的 python 不归
runner 改），加 `--disable-pip-version-check` 免得 pip 自己先升级自己。

### 9.14 刷机镜像的 NAT 复验通过（2026-10-25 10:20–10:50）

**刷机镜像现在是路由器了：`sk-nat` 在真板上装上了规则，客户端从板子拿到租约，
ping 通外网，conntrack 里能看到源地址被改写。**

这一轮先证了一件事：**镜像里的 first-boot 机制在真板上成立**。当前板上刷的是
run165 的旧 rootfs（NAT 修复在 2e9b4e6，CI 一直没出片），所以我把仓库里的
`overlays/.../etc/init.d/sk-nat` 和 `etc/uci-defaults/99-sk-nat` 拷到板上，
**按 boot 的方式**（`cd /etc/uci-defaults` → `. ./file` → `uci commit` →
删掉已应用的）跑了一遍：

- `/etc/init.d/boot` **自带 `uci_apply_defaults()` 实现并在第 53 行调用** ——
  机制在，不是我以为的「靠某个包提供」；
- `99-sk-nat` 被执行，`/etc/rc.d/S96sk-nat -> ../init.d/sk-nat` 建好；
- 脚本里的 `setsid` 把 sk-nat 拉起来，15 秒后规则装上：

```
table ip nat { chain postrouting {
    type nat hook postrouting priority srcnat; policy accept;
    oifname "lan3" counter packets 0 bytes 0 masquerade } }
dmesg: sk-nat: masquerade installed on lan3
```

**这一轮最重要的发现：HANDOFF 里「串口 `uci` parse error」是错的诊断。**
不是 uci 的限制，是 **shell glob**：`@device[0]` 是 glob 模式，串口的 shell
把它按当前目录展开，uci 收到的东西不是敲进去的东西，于是报了一个指向语法
的 parse error。**加上引号就好了**：

```sh
uci -q get "network.@device[0].ports"   ->   lan1 lan2 lan4 lan-cpu0
```

这解锁了整轮：**换 WAN 口可以在串口做**，而串口不依赖网络。

**为什么必须走串口：插座每 ~15.5 分钟断电 15 秒。** 换口之后板子的
192.168.9.1 就离开了本 PC 的网段（lan2 出了桥），后面还要等租约——这段
时间板子随时可能重启。实测：10:25 在 ssh 上换的口，板子在读到租约前重启，
回来还是 `wan=lan3`，**改动随电走了**。改走串口 + `uci commit`（commit 写
jffs2，能扛住断电）之后：板子被插座切断后 57 秒回来，报告的仍是
`wan=lan2` / `ports=lan1 lan4 lan-cpu0` —— **改动扛过了断电**。

**第二个发现：`dropbear` 只监听 lan。** 换口后板子在共享网段上是
192.168.137.215，但 `dropbear.main.Interface='lan'`，ssh 只听 192.168.9.1:22，
所以 ssh 不通。改成 `lan wan` + 重启 dropbear 即可（验完要改回 `lan`）。

**最终证据**（从板子的 WAN 地址读，客户端是板子自己造的 veth+netns）：

| 项 | 结果 |
|---|---|
| masquerade 规则 | `oifname "lan2" counter packets 47 bytes 2572 masquerade` |
| 客户端租约 | `192.168.9.226/24`（来自板子自己的 DHCP） |
| 客户端默认路由 | `default via 192.168.9.1 dev labcli0` |
| 外网 ping | `223.5.5.5` **2/2 通，0% 丢包** |
| conntrack 改写 | `src=192.168.9.226 dst=223.5.5.5` → 回程 `dst=192.168.137.215` |
| WAN tx | 73814 字节 |

conntrack 那一行就是 NAT 本身：源地址从客户端的 `192.168.9.226` 变成板子 WAN
的 `192.168.137.215`。证据：`staging/ctl/g4nat-evidence.txt`。

**新增仓库工具**（`ci-build-repo/tools/`，都自洽可用）：

| 文件 | 作用 |
|---|---|
| `bssh2.sh` | 一次 ssh，命令从文件读（避免三层引号转义地狱） |
| `cmd-portstate.txt` | 读端口/wan/forward 的命令文件 |
| `portmove.sh` | 串口换 WAN 口（move/back），带 commit |
| `clientrun.sh` | 装客户端、等它拿到租约 |
| `natcounters.sh` | 规则 + 计数 + conntrack + 客户端视角 |
| `natevidence.sh` | 从板子 WAN 地址读全部证据 |
| `restore.sh` | 拆客户端、端口还原、dropbear 还原 |
| `q.py` | 串口问板子（路径改成相对脚本位置，`SKD_CTL` 可覆盖） |

**这一轮踩的坑：**

- **`uci delete network.@device[0].ports` 在串口「parse error」是 glob 不是
  uci**（上面已详述）。**凡是带 `[]` 的 uci 参数都要加引号。**
- **`dropbear.main.Interface='lan'` 意味着换口后 ssh 就断了**；要改成
  `lan wan` 才读得到证据，**验完改回 `lan`**。
- **Git bash 会把发给板子的 `/etc/...` 改成 `C:/Program Files/Git/etc/...`**，
  板子回 `-ash: C:/Program: not found`。调用 `python q.py` 时要
  `export MSYS_NO_PATHCONV=1`。
- **插座断电会把板子留在 U-Boot 提示符**（这次是 `network restart` 撞上
  断电，自启没跑成）。串口 `=>` 下 `bootm` 会说 "Wrong Image Format"——
  因为 `mtd read` 还没执行。完整执行
  `mtd read kernel 0x88000000; mtd read dtb 0x88800000; bootm 0x88000000 - 0x88800000`
  就能正常从 flash 起来（这次 20 秒内就 ping 通了）。
- **`labnat-flash.ps1` 要用 `pwsh` 跑，不能用 `powershell`**：前者是 PS7，
  后者是 PS5.1，而脚本里的三元运算符 `$x ? 0 : 1` 只有 PS7 认。
  （已把 `labnat-flash.ps1` 的三元改成 if/else，两边都能跑。）
- **`scp` 没有 `-b`**，要绑源地址得用 `-o BindAddress=192.168.137.1`。

详见 **docs/81**。

### 9.15  CI 连挂两次：Compile 步骤的超时放错了地方（2026-10-25）

**现象**：run168 和 run169 连续两次卡在 `Compile` 步骤出不来。

| run | commit | Compile 时长 | 怎么结束的 |
|---|---|---|---|
| 167 | `bc923d6` | 51.2 min | 成功 |
| 168 | `2e9b4e6` | 353.4 min | 被 job 自己的 6h 上限杀掉（报 `cancelled`） |
| 169 | `bf0e503` | >116 min（读数时） | 被步骤的 200 min 上限杀掉 |

好 run 的同一段是 33-69 分钟（runs 160-167），116 分钟已经是最差好 run 的
两倍。

**不是什么原因**：

- 不是 run168 新加的 `sk-nat` 断言。它们在 `Compile` 前后的两个步骤里，
  run169 里前一个步骤用时 0.0 min 就过了。
- 不是 `dl/` 缓存。`Download packages`（把整个 `dl/` 拉齐的那步）
  在 run169 只用 1.8 分钟就完成，编译开始时源码都在。
- 不是 run169 新加的 git 超时环境变量——run168 没有它们，也一样挂。

**查不到根因（本机没有读日志的通道）**：

- `GET /repos/{o}/{r}/actions/jobs/{id}/logs` 匿名 **403**（要 `actions:read`）；
- `.../runs/{id}/job/{id}/logs` 匿名 **404**；
- 浏览器打开 job 页（**公开仓库**）显示 **"Sign in to view logs"**；
- 本机没有 `gh`、环境里没有 token。

能匿名读到的只有 run/job 的元数据（走代理，代理有自己的地址和配额）和
check-run 注解，而这两个 run 的注解是空的——唯一能说出"哪个文件坏了"的
那段没跑到。所以这次挂起**归因不了**，如实记为未解释。

**真正错的地方**：200 分钟的上限原本挂在**步骤**上，而「失败后重试一次」
在**同一个步骤里**。于是上限不是让步骤失败、让下一行继续，而是把整个步骤
连同重试一起杀掉。run168 在 6h job 上限下是同一个形状：353 分钟卡在
Compile，重试一次的机会从来没出现过。

**改法（`bf0e503` 之后的这次）**：上限挪进步骤内部，步骤自己的上限是
三段之和加余量。

```yaml
timeout-minutes: 340
timeout --kill-after=60s 250m make -j"$NCPU" 2>&1 | tee ../compile-parallel.log || rc=$?
timeout --kill-after=60s  70m make -j"$NCPU" 2>&1 | tee -a ../compile-parallel.log || rc=$?
make -j1 V=s 2>&1 | tee ../compile-v1.log || rc=$?
```

（上面 `$NCPU` 就是工作流里写的 `$(nproc)`，这里只是不想让它被 shell 吃掉。）

- **第一遍 250 分钟**：好 run 是 33-69 分钟，250 接近最差好 run 的四倍，
  够扛一个比好 run 慢三倍的 runner；真卡住就在 250 分钟失败，而不是
  吃掉整个 job。
- **重试 70 分钟，而且真的跑得到**：`make` 是增量的，第二遍只需要补完
  第一遍剩下的部分。
- **并行编译的输出进文件**：`compile-parallel.log` 现在和
  `compile-v1.log`、`openwrt/logs` 一起进失败产物。run168/169 两次都是
  「Compile 几小时没说话，事后也没东西可读」，下一次会留下遗言。
- **`timeout` 的 124 被单独点名**：它既不是 0 也不是编译器的返回码。

**这个改法的代价（说清楚）**：杀 `make` 可能留下写了一半的 `.o`——编译器
正在写的时候被信号打断，文件比源文件新，下一次 `make` 不会重编它。但这个
失败是**响的**：截断的 `.o` 会在链接处报错，链接错误会点名文件，而专门
用来点名的串行那一遍还在。**不会**出现一个坏 `.o` 悄悄进了镜像。

详见 **docs/82**。

### 9.15 续：输入侧全部排除，构建开始自报位置（2026-10-25 14:45–15:05）

**先把「是不是输入变了」这件事查死。** run167（51 分钟编译完）和 run168
（Compile 卡到 6h job 上限）之间，喂给构建的东西逐个核对：

- **基础树**：`git clone --depth 1 -b sr1010-zx279133` 取的是分支 tip，
  分支一动就等于每次 run 换一棵树。实测 `cnjn/ImmortalWrt` 的 tip 是
  `5996412`，提交时间 2026-10-23 22:18 UTC——**比第一个好 run（run165）
  还早**，到现在也没动。165 之后所有 run 的基础树是同一棵。
- **内核**：`KERNEL_SHA` 是 pin 死的
  `96e5a2413524742e177e383fe523ef21de5ed238`，clone 之后手工 checkout，
  submodule 也跟这个 commit 走。
- **feeds**：五条里四条 pin 了 commit（`packages` `e93a938`、`luci`
  `ed7692cb`、`routing` `76c93390`、`telephony` `2618106d`），唯一浮动的
  是 `zte_mesh`，而它最新一个提交是 2026-10-19——比第一个好 run 还早六天。

**结论：编译成功的 run 和编译挂掉的 run，输入完全一样。** 剩下的差异只有
仓库自己——run168 是 `2e9b4e6` 的第一个 run，那个提交加了两个 base-files
脚本和几条断言。两个 rootfs 里的 shell 脚本，加上一步 0.0 分钟就过的 grep，
挂不住一次编译；runner 变慢也解释不了「两个 matrix job 同时卡住」。

**那还剩什么？卡住的 fetch。** 一个卡在包镜像上的构建，从外面看和「构建慢」
一模一样，而这两者只能靠「构建此刻在干什么」来区分——而此前没有任何东西
在报这件事。

**改法（`1978d4d`）**：给 Compile 加一个看门狗。

- 每 2 分钟把进程表、load、内存快照写进 `compile-watchdog.log`；
- 每 6 分钟用 `::notice::` 注解说一行：跑得最久的几个进程（带完整命令行，
  卡住的 fetch 会把 URL 带上）、构建最近碰过的包目录、还剩多少内存；
- 看门狗日志也进失败产物；「构建停下时它在干什么」那一步现在也把它打出来。

**但注解不是实时通道**（2026-10-25 15:10 实测）：**注解是在 job 结束时才
发布的**。run170 的 Compile 已经跑了 2 小时、那句 `::notice::the build starts
with 97G free` 早就打在日志里了，API 返回的注解却是 **0 条**。所以心跳
要等 job 结束才看得见——正好是最不需要它的时候。

**于是改成：构建自己停下来**（`tools/watchdog-dryrun.sh` 演练过）。

- **两种停法，各有各的判据**：
  - **沉默的**：`make` 每编一个目标都打一行，所以「日志 24 分钟没长」
    等于「构建没在干活」。
  - **会说话的**：会干活的构建会写文件，所以「`build_dir` 下半小时没写过
    任何文件」等于「没在干活」——**不管它在打印什么**。卡在重试循环里的
    构建就是这种：每隔几秒打一行、什么都不写；只看日志的看门狗会听它
    说到天亮。
- 判定成立就看门狗**杀掉 make 自己**（构建启动时把 make 的 pid 写进
  `compile-make.pid`，`exec` 保证那个 pid 就是 make）。
- 步骤失败 → job 结束 → 注解发布 → 里面就是证据：并行日志的尾巴 +
  看门狗记录下来的那一刻的进程表。
- **演练结果**：一直打印且一直写文件的构建**不会被杀**（still alive）；
  安静下来的**被停掉**（stopped）；只打印不写文件的**也被停掉**。
  这就是全部契约：慢但健康的构建一直在写文件，永远不会被碰。

**同时给下载加上限速**：`scripts/download.pl` 只给了 curl/wget **连接**
超时（5 秒），**传输本身没有上限**——镜像接受了连接然后不再发数据，构建就
永远等下去，而从外面看「永远等」和「很慢」一模一样。它本来就读的
`CURL_OPTIONS` / `WGET_OPTIONS` 现在在 Compile 步骤里给了：低于 100 B/s
持续 2 分钟就放弃，并且报出是哪个 URL。

**上限也降下来了**：第一遍 250 → 90 分钟，重试 70 → 40 分钟，串行那一遍
从无上限 → 10 分钟。好 run 是 33–69 分钟，而看门狗在 90 分钟之前就
判定卡死了——那个长上限买到的只是「知道它卡住」所花的时间。

详见 **docs/82 §7–§9**。

### 9.15 再补：刷机前门禁（2026-10-25 15:25）

flash 是这个项目里唯一不好撤销的动作，所以进 flash 之前先把 release 看一遍。
新增 `tools/preflash.py`，逐条检查**只有上板才会暴露**的问题：

- release 自带的 `sha256sums-release` 是否描述了旁边的文件（刷机脚本在板上
  就是拿它校验的，对不上照样写，写完才发现）；
- uImage 是否放得进 8 MiB 的 kernel 分区（`mtd write` 静默截断，板子起来
  只有一句 "wrong image header"）；
- payload 是不是 gzip（loader 别的都不认）；
- 里面的内核是不是 6.18.38；
- **rootfs 里有没有 `sk-nat` 和把它挂进 `/etc/rc.d` 的 uci-defaults**
  （没有的话，镜像起来是个能 ssh 但不路由的盒子）；
- dtb 的 magic 对不对。

**用 run166 的 release 做过反测**：三条老问题（超尺寸 7315 字节、无 sk-nat、
无 uci-defaults）全部被抓住，其余（manifest、magic、gzip、内核版本、dtb）
全部放行。门禁本身是有效的，不是一次都没响过的检查。

### 9.16：构建自己把进度写在能读到的地方（2026-10-25 17:20）

**先说查实的事**（`docs/83`）：

- **最后一个 Compile 段正常跑完的 run 是 167**（initramfs 51.2 分钟、flash
  32.9 分钟，跑完于 `2026-10-24T19:12Z`）。**run168 的 Compile 从
  `19:04Z` 开始就再没结束过**（353 分钟被 job 上限砍掉）。中间只隔 48 分钟。
- **边界这一侧我们自己的改动没碰 Compile 段**：`bc923d6` 只改了打包段
  （zopfli + 8 MiB 门禁）和两份文档。run167 和 run175 的 Compile 段是同一份
  代码。
- **输入侧全部没动**：base tree tip 仍是 `5996412`（2026-10-23 22:18:27Z）、
  四条 feed 仍 pin 在 `e93a938`/`ed7692cb`/`76c93390`/`2618106d`、唯一浮动的
  `zte_mesh` tip 仍是 `327101a`（2026-10-19，run167 用的就是它）、内核 SHA
  没变。`zte-mesh` 包的 `Build/Prepare` 只解一个**随仓库提交**的 tarball，
  没有下载，不存在「等网络」的可能。
- **⇒ 挂点在环境侧，不在我们的改动里。**

**再说明白一件事**：能匿名读的通道，一个都不管用。

| 通道 | 实测 |
|---|---|
| job 日志 API | **403**（无 token） |
| job 页 HTML | 取不到（请求超时） |
| run 页 HTML | 能读，但 job 列表是脚本加载的，没有步骤数据、没有实时日志 |
| artifact **列表** API | **200**，匿名可读 |
| artifact **内容** | **401**，要 session |
| **运行中 job 的 check-run 注解** | **空** |

最后一条测了两次：run170 在飞时 API 返回 0 条；**run174 的首轮 16:55 被上限
砍掉后，Compile 步骤已经把 `::error::evidence:`（并行日志尾巴 + 看门狗记录）
打出来了，17:04 再查，两个 job 仍然是 0 条**。**注解是 job 结束时才发布的，
而卡死的构建恰恰是永远不会结束的 job。**

**于是让构建自己写下来**：

- 新增 `tools/publish-progress.sh`：构建每 6 分钟把一份进度推到
  `ci-progress/<run>-<mode>` 分支的 `progress/<run>-<mode>.txt`。
- 进度里有三样能回答「它在哪」的东西：**构建日志的尾巴**（它在编译什么）、
  **运行时间最长的进程**（它是在编译还是在等，等的是谁）、**最近碰过的目录**
  （它到了哪个阶段）；另加日志大小、多久没说话、有没有写过文件、内存和负载。
- **为什么是分支**：git 协议不是 REST API，`git fetch` **不耗 API 配额、不需要
  session**，而且在 job 还在跑的时候就能读。分支名带 run 号，两个 run 不会抢
  同一个 ref；文件放在 `progress/` 下，**不在工作流 `push:` 的路径过滤里**，
  所以发进度不会触发新构建。
- `tools/publish-rehearse.sh` 用一次性裸库演练过：发布 → 从远端读回 → 被下一
  次覆盖；源文件缺失、目录不是仓库这两种情况都只返回 0，不误伤构建。
- 读法：`python staging/ctl/progress.py <run> <mode>`。

**看门狗的判据也改了，而且判据和演练现在是同一份代码**。原来两条都是
「不说话了」，不够：编译一个大文件时构建同样不说话，下载时构建照样说话。
**现在两条必须同时成立**才算卡死——

- 日志 12 分钟没长 **且** `build_dir`/`bin`/`staging_dir`/`dl`/`tmp` 20 分钟
  没写过文件；
- **或者** 40 分钟什么都没写（不管它在打印什么）——这一条抓的是「会说话的
  卡死」：卡在重试循环里的构建每隔几秒打一行、什么都不写。

第一版里还有第三条「日志 30 分钟没长（不管文件）」，**被演练抓出来是错的**
并删掉了：它会把「安静但仍在写文件」的构建也杀掉，而那正是编译一个大文件
的构建。现在的四个用例是：打印+写文件 → 不动；**安静但仍写文件 → 不动**；
安静且不写 → 停；只打印不写 → 停。

看门狗本身被抽成了 `tools/compile-watchdog.sh`，**工作流和演练跑的是同一个
文件**。以前是两份拷贝，而且**已经漂移过一次**：上线跑的规则和演练的规则
不是同一条——这正是演练最不能有的毛病。

**上限调回 240 分钟，而且它是保险不是判官**：90 分钟那个上限是猜的，好 run 是
33–69 分钟，而每次挂的 run 都是在**还在干活**的时候被这个上限砍掉的。判官是
看门狗——它盯的是「构建有没有在干活」，不是钟。**又忙又不停**的构建交给上限
去管，那本来就是上限该管的事。

**被停掉的构建不再重来**：*失败*的构建不一样，make 是增量的，同一棵树往往就差
一个目标，值得再试一遍（重试保留）。但*被停掉*的构建在同一处只会再停一次，
而重试 + 串行那 50 分钟，正好是注解发布前要等的 50 分钟——所以步骤当场结束，
证据立刻出去。

**下载限速也给到了所有会下载的地方**：`Download packages` 步骤现在也有
`CURL_OPTIONS`/`WGET_OPTIONS`，`Install feeds` 步骤加了 git 自己的低速限——
半路停住的 feed clone 和半路停住的镜像下载，形状是一样的。

详见 **docs/83**。
### 9.17：看门狗把一个健康的构建当成了卡死（2026-10-25 19:10）

**run177 是我们自己杀的。** 这一节记的是怎么查出来的、错在哪、以及改成了
什么。

**证据在进度分支里**（`ci-progress/177-flash`，每个 beat 一次提交）：

| beat | 时刻(UTC) | 日志字节 | 多久没说话 | 有没有写 | 日志最后一行 |
|-----:|-----------|---------:|-----------:|----------:|--------------|
| 18 | 10:21:32 | 7 392 | 0 | yes | `make[2] package/install` |
| 24 | 10:33:36 | 7 392 | 6 | yes | 同上 |
| 27 | 10:39:38 | 7 392 | 9 | yes | 同上 |
| 30 | 10:45:45 | 7 392 | 0 | **no** | 同上 |
| 33 | 10:51:47 | **7 516** | 2 | no | `make: *** […/toplevel.mk:233: world] Terminated` |

两个数字就是全部：**7 392 → 7 516，正好 124 字节**，多出来的那行是 make 被
`SIGTERM` 时打印的话。而 Compile 步骤里唯一会给 make 发 `SIGTERM` 的，就是
我们自己的看门狗——beat 30 时它的第二条规则命中了（「40 分钟什么都没写，不管
它在打印什么」），日志里同时打出了 `::error::the build has written nothing
for 40 minutes…`。

**错在哪**：那条规则问的是「有没有文件**比标记新**」——
`find $DIRS -type f -newer <marker>`，比的是**修改时间**。而 OpenWrt 的
install 阶段用 `cp -a` 把 rootfs 铺开，**`cp -a` 保留每个被拷贝文件的修改
时间**：拷贝出来的文件和原件一样老。于是构建写得最快的那些文件，恰好是看门狗
看不见的那些文件。规则问对了问题、问错了问题。

**该问的是「这个文件有没有**变过**」**：inode 的 change time（`ctime`）。它由
内核设定，程序改不了——`cp -a` 能保留 mtime、属主和权限，但拷贝件的 ctime 就是
拷贝那一刻。`find -cnewer` 就是这个问法（拿文件的 ctime 比标记的 mtime）：

```sh
find build_dir bin staging_dir dl tmp -cnewer ../stale-marker
```

本机实测（5 个 `cp -a` 拷贝，原件 mtime 是 1970）：

```
-newer  -> 0 个文件
-cnewer -> 5 个文件
```

**判据从两条变三条**：日志 20 分钟没长 **且** 文件 30 分钟没变 **且** 15 分钟
没启动过新进程（构建自己的进程里最年轻的那个有多老）。编译大文件的构建安静
但在写、下载的构建说话但写在 `dl/`、安装 rootfs 的构建长时间不说话但在写——
三条同时成立才是真的停了。「会说话的卡死」那条独立规则（90 分钟什么都没写）
保留。

**还有一件事：被杀死的构建不能把步骤一起拖住。** 击杀之后 run177 的 Compile
**又 in_progress 了七分钟**——构建已经死了，job 还活着。因为步骤是
`timeout … | tee ../compile-parallel.log`，而看门狗只杀了 **make**：make 的子
进程还攥着管道的写端，`tee` 收不到 EOF，管道不结束，步骤就不结束，runner 就
在死构建上白占四个小时。`timeout` 会把构建放进**自己的进程组**，所以杀组就是
杀构建和构建起的一切；看门狗现在杀进程组，5 秒后再把残留的清掉，构建不在了
它就停止观察。

**演练加了第五个用例**，而且这个用例的证据就是这次的病根：假构建只写
`cp -a` 的拷贝（原件 mtime 是 2020-01-01），用例末尾同时打两个问句的答案：

```
  the modification-time question: 0 files
  the change-time question:     19 files
```

改完的规则在演练里 5/5 通过（打印+写 → 不动；安静但仍写 → 不动；**只写保留
mtime 的拷贝 → 不动**；安静且不写 → 停；只打印不写 → 停）。上线的是
`tools/compile-watchdog.sh` 这一个文件，演练跑的就是它。

旧的 8 个 run（171–178）全部取消，腾出 runner 给 **run179**（`56b75ee`）。

详见 **docs/84**。
### 9.18：真正卡住构建的是一个后台进程（2026-10-25 20:30）

**run168–179 全部没有跑完，而且不是构建慢**：活干完了、机器空闲、日志停在
`make[2] package/install` 之后、rootfs 停在 622 个文件不动。run179 的实时
通道把这一段完整记下来了：

| beat | 时刻(UTC) | 日志 | 多久没说话 | 有没有写 | rootfs |
|-----:|-----------|-----:|-----------:|----------:|-------:|
| 30 | 12:10:19 | 7 392 | 0 | yes | 622 files, 12M |
| 33 | 12:16:22 | 7 392 | 6 min | yes | 622 files, 12M |
| 36 | 12:22:24 | 7 392 | 12 min | yes | 622 files, 12M |

同一份快照里的进程表更说明问题：两个包管理器的 post-install 循环还活着在
等（`make --no-print-directory -j1 package/install` + 两个 `bash -c ( cd
…/root-zte; …`），而机器的负载是 `0.08`。

**相关性只有一个变量宽**：run167 是最后一个跑完的构建（2026-10-24T19:12Z），
而 overlay 的提交历史短到能一眼看完：

```
2e9b4e6 2026-10-25 02:56:52 +0800 flash(nat): the flashed image had no firewall, so it could not route
51ac6c6 2026-10-24 03:27:41 +0800 fix(9132): this board's own microcode, and the frames that never reached the DSA ports
```

`2e9b4e6` 推送于 2026-10-24 18:56:52Z，run167 在 19:12Z 结束——**run167 是
不带它的最后一个构建，run168 是带它的第一个**，此后每一个构建都挂在同一个地
方。其余输入都没动（base tree、四个 feed、内核 SHA、Compile 段本身，见
docs/82 §2）。

**2e9b4e6 加的东西就是元凶**：它给镜像加了 NAT 规则，也加了
`/etc/uci-defaults/99-sk-nat`，那个文件最后一行是

```sh
setsid sh /etc/init.d/sk-nat >/tmp/sk-nat.log 2>&1 &
```

而 `/etc/init.d/sk-nat` 是一个**永不结束的循环**（`while true; do …; sleep 15;
done`）。**构建会在包管理器的 chroot 里执行 uci-defaults**，所以构建里被拉起
了一个永不退出的进程。

**机制**：GNU make 把自己的 jobserver（一个 socket）交给每个 recipe，**make
在结束之前要等这个 socket 关闭**；recipe 起的后台进程继承了这个 socket 并一直
持有它。构建的活干完了，make 却在等一个**自己起的进程**持有的 socket——这就是
为什么日志停在 install、什么都不写、什么都不算、怎么等都没用。

**证据是相关性本身，不是镜像里的时间戳**：

- run167 是不带 `2e9b4e6` 的最后一个构建，run168 是带它的第一个，**此后每一个
  构建都挂在同一个地方**，而其余输入一个都没动。overlay 里的文件只可能通过
  「构建去执行它」影响构建——构建不执行 uci-defaults 的话，加这个文件对构建
  毫无影响，run168 不会挂。**所以构建确实会执行 uci-defaults**，而执行它就会
  拉起那个永不退出的进程。
- ⚠️ 2026-10-25 21:10 更正：这一节原先写的第二条证据（「板上
  `/etc/rc.d/S96sk-nat` 的时间戳比其它链接晚 27 分钟，所以 uci-defaults 是在
  构建里跑的」）**是错的**。板上那套 sk-nat 文件是 2026-10-25 早上手工拷上去
  的（见 §9.14：当时板上刷的还是 run165 的旧 rootfs，NAT 修复在 CI 里还没出片，
  所以把仓库里的两个文件拷到板上、并按 boot 的方式手工执行了一遍）——它和那条
  链接的时间戳都是**手工安装**留下的，和构建无关。镜像里的文件时间戳一律是
  `2026-10-24 06:18:27 +0800`（= base tree 提交时刻 `5996412`，2026-10-23
  22:18:27 UTC），而手工装上去的那两个文件是 `06:45:34`——这正是它们不属于
  镜像的证据。
- 那条「板上 `/etc/uci-defaults/` 是空的」也不能作数：板的 uci-defaults 是
  **首次开机时**跑的（`/etc/init.d/boot` 自带 `uci_apply_defaults()`，第 53 行
  调用），跑过就删掉，所以板上永远是空的，与构建无关。

**「CI 的镜像里到底有没有那条链接」只能去问镜像本身**，问法见 **docs/87**：
jffs2 压缩文件内容但**不压缩文件名**，所以 `tools/jffs2names.py` 能把镜像里的
文件名全部读出来，`tools/preflash.py` 于是问的是「名字在不在」，而不是「某个
字节在不在」。

**修法**：`99-sk-nat` 不再拉起任何后台进程，只留那条链接——**链接才是让 init
脚本跑起来的东西**，而链接本来就进了镜像，所以板上行为一点没变（规则仍然
由 init 脚本在开机时装上，板上的 flashed image 从出厂起就是这样）。init 脚本
另加一道护栏：构建环境（`IPKG_INSTROOT`/`PKG_ROOT`/`CI` 任一有值）里只走一轮
就退出，绝不循环——反正构建的 chroot 里没有 uci、也没有本架构的 nft，本来就
装不上规则。

**两个故障是独立的，两个都得修**：看门狗的错（用 mtime 判断「有没有写」，看不
见 `cp -a` 保留 mtime 的拷贝）会让构建**因为错误的理由被杀**；这个后台进程会让
构建**根本跑不完**。

详见 **docs/85**。
### 9.19：run180 的构建跑完了，是门禁把 release 拦下了（2026-10-25 21:25）

**先说好消息：构建不再挂。** run180 的 Compile 从 12:34:40Z 到 13:12:30Z，
**37.8 分钟，completed**——这是 run167 之后第一个跑完的构建，docs/85 那个修
法（把 uci-defaults 里的后台进程去掉）是对的。initramfs job 也成功并发布了
release。

**坏消息：flash job 被自己的门禁拒了。**

```
::error::the built rootfs does not contain sk-nat - the flashed image would not masquerade
```

门禁是这样问的：

```sh
strings "$RS" | grep -qx 'sk-nat' || { … exit 1; }
strings "$RS" | grep -q 'sk-nat.log' || { … exit 1; }
```

**两条都是错问法**，第一条跟 jffs2 的压缩没关系：

**`strings` 不会把文件名单独放一行。** `strings` 打印的是文件里可打印字符的
连续段，而 jffs2 的目录项（承载文件名的那个节点）在名字两边都是二进制字节，
所以名字总是搭在更长的段里。拿 run166 那个真实 rootfs（767 个文件）实测：

```
network      exact-line=4    substring=25
dnsmasq      exact-line=0    substring=22
init.d       exact-line=0    substring=33
rc.d         exact-line=1    substring=1
```

`init.d` 就在那个镜像里（它是根目录下的一个目录），而
`strings | grep -cx 'init.d'` 是 **0**——`strings` 印出来的是
`']init.d'`、`'../init.d/gpio_switch'` 这种。**所以这条门禁问的其实是「镜像
的可打印段里有没有一行正好是 `sk-nat`」，跟构建出来的是什么镜像无关——它会
拒掉所有镜像，好的坏的都拒。**

**第二条问的是一个注释里的词。** `sk-nat.log` 是旧 uci-defaults 把 init 脚本
输出重定向到的文件名，那行已经没了（docs/85：就是它占着 make 的 jobserver 把
构建卡住），剩下的只是注释里的一句话——而 jffs2 压缩的正是**文件内容**，文件
里的词是找不到的：

```
$ python -c "d=open('flash-rootfs.jffs2','rb').read(); print(d.count(b'sk-nat'), d.count(b'sk-nat.log'))"
0 0
```

**修法**：jffs2 压缩文件内容但**不压缩文件名**（目录项原样存），所以
`tools/jffs2names.py` 能把镜像里的文件名全部读出来，门禁改成问**名字**：
`sk-nat`（init 脚本本身）、`S96sk-nat`（rc.d 链接，构建跑了 uci-defaults
时在镜像里）、`99-sk-nat`（uci-defaults，构建没跑时在镜像里）——后两个至少要
有一个在，否则脚本永远不会被启动。门禁还会把它看到的名字打出来，下次失败就
能看出镜像里到底有什么。

**同时给 uci-defaults 加了一道保险**：它现在除了建链接，还直接把规则装上。
镜像里是哪一个，文件自己没法知道——构建跑了 uci-defaults，链接就在镜像里、
第一次开机就跑；构建没跑，链接要等第一次开机才建，而 `/etc/rc.d` 的链接是在
**运行**的时候读的，那时候 uci-defaults 还没跑，所以规则会晚一次开机。现在
第一次开机两种情况都有 NAT。构建环境里这一整块是跳过的（`IPKG_INSTROOT` /
`PKG_ROOT` / `CI` 任一有值），也不拉起任何进程。

**台架上那道门禁犯的是同一个错**，已按同一条思路修掉（docs/87）；两道门禁是
同一个问题问在两个地方，现在答案也一样。

详见 **docs/88**。
### 9.21：G4' 第一轮 —— CI 自己的 release 刷进 flash，两次冷启动全过（2026-10-25 23:00）

**run181 是这个项目第一个 flash release**，CI 的结果是 `completed successfully`；
门禁改问文件名之后，它一次就过了。

**写入成功，22:53:32。** 三个分区读回来和写进去的字节一致：

```
kernel: OK b2964491dce550c9ccfe17e20aaf76be
dtb: OK 8c460c9794558ac7e18bcb18d903a228
root: OK a491ee2bb765ecaa7da1ba443d74f0e5
```

**冷启动 1（22:59:41）**：插座自己断电、自己回电，板子**自己**从 flash 起来，
`g4verify` **14/14**，`verify_router.sh` **5/5**。
**冷启动 2（23:15:11）**：同样 **14/14 + 5/5**。

**板子上的 NAT 规则是镜像自己装的**，这正是 G4' 要证明的那一件事：镜像里**没有**
`S96sk-nat` 链接，只有 `99-sk-nat`（说明**构建没有跑** uci-defaults，它留在镜像
里等第一次开机），第一次开机时它建了链接**并且直接装了规则**（这是 222e1dc 加的
保险）：

```
[   10.834216] sk-nat: the masquerade is on lan3
```

板上 `/etc/uci-defaults/` 跑完就被 boot 脚本删掉了。

**台架上栽了四个跟头**，都写进 docs/86 第 5 节：

1. **复位把 U-Boot 的横幅弄丢了。** 进 loader 才能起 RAM 镜像，RAM 镜像才能写
   flash。复位原来由 `tools/hardreset.ps1` 做——它要独占 COM3（一个串口只能被一个
   进程持有），而 loader 的横幅是在复位线放开后**大约一秒**才打印的，正好落在独占
   窗口里。于是横幅没被看见，轮次报 `the loader never showed up`，板子继续跑它本来
   就有的 flash。
   改成让 relay 自己复位（它不放开端口，看得见横幅），并且**空格连发到 loader 停在
   `=>` 为止**——横幅打在倒计时开始之前很久，单个空格会被白费，倒计时跑完板子就自
   己 boot 了 flash。
   顺带测出两件事：**relay 的 DTR/RTS 翻转对这个板子根本不产生复位**（板子一直在
   ping，中间一个字都没打印）；真正好用的是 loader 自己的 `reset` 命令。
2. **断在半路的管道，看起来跟成功的管道一模一样。** 镜像用
   `cat file | ssh "cat > file"` 送上板，板上的 `flash-sysupgrade.bin` 短了
   **133120 字节**（13050077 / 13138197），脚本却打印 `done`。幸好 flasher 自己的
   manifest 检查拦住了（`sha256sum -c` 不通过就拒绝写 flash）——那正是这道检查存在
   的意义。
3. **那个短文件还是上一轮遗留的旧文件。** g4flash 第 2 步只把 uImage、dtb、
   rootfs 和 manifest 从 release 复制过来，**没有复制 sysupgrade 镜像**，于是送上去
   的是 run165 那轮的旧文件。两个错误互相遮住了：一个又是旧、又是短的过时文件，看
   起来就像一次传输中断。
4. **`cmd | tee file` 的退出码是 tee 的**，永远是 0——所以门禁拒了 release 之后，轮
   次照样往下跑去了刷机。现在改成写文件再 `cat`，退出码是它自己的。

### 9.22：镜像里的 init 脚本从来没被执行过（执行位），run182 正在重建（2026-10-26 00:05）

**NAT 轮失败了，失败在最要命的地方**：板子有租约、客户端有地址，而 masquerade 表
根本不存在：

```
=== the masquerade rule and its counter ===
Error: No such file or directory
list table ip nat
```

**根因是 git 里的一个模式位。** 镜像用两个地方装这条规则：

- **`/etc/uci-defaults/99-sk-nat`**：直接装规则，第一次开机跑。OpenWrt 的 boot 脚
  本是 **source** uci-defaults 的，所以这个文件的模式**没关系**——规则因此在第一次开
  机后还在（`sk-nat: the masquerade is on lan3`）。
- **`/etc/init.d/sk-nat`**：通过 `/etc/rc.d/S96sk-nat` 在**之后每一次**开机装规
  则。**而它从来没有被执行过，因为它不可执行**：

```
root@ImmortalWrt:~# ls -l /etc/init.d/sk-nat
-rw-r--r--    1 root     root          3822 Oct 24 06:18 /etc/init.d/sk-nat
```

而仓库里它就是 `100644`：

```
$ git ls-files -s overlays/target/linux/zte/zx279133/base-files/etc/init.d/sk-nat
100644 d73fb7261a1b4cc3fc5b8d8bb888626869037856 0	overlays/.../etc/init.d/sk-nat
```

`100644` 就是「不可执行」。构建把这个模式原样带进镜像，**不可执行的脚本不会被运行
——而且是静默的，因为镜像照常启动，别的功能照常工作**。这个仓库里唯一本来就对
的是 `etc/board.d/02_network`（100755），所以板子的 LAN 是通的、NAT 是不通的。

**板上证明「模式位就是全部原因」**（同一块板、同一个镜像，只改了一个位）：

```
[  503.168825] sk-nat: masquerade installed on lan3
table ip nat { chain postrouting { oifname "lan3" counter packets 0 bytes 0 masquerade } }
```

**同类文件一共 8 个**，全部改成 `100755`：sk-nat、99-sk-nat、platform.sh，以及 zrec
那 5 个（`S21zrec`、`85_zrec`、`zrec-bringup`、`zrec-ops`、`zrec-run`——那是 RAM 镜
像的恢复服务）。

**CI 侧这次一起改的**：

- **release 现在发布 `flash-sk-d840n.sh` 了。** manifest 是对着 staging 目录生成
  的，所以它列了 flasher；release 的文件清单是另一份手写的清单，两份清单会漂移——
  run181 的 release 只发布了 manifest 里的 5 个文件中的 4 个，于是「相信 manifest
  的 fetch」在一个 release 根本不会带的文件上失败。
- **`prepush.py` 加了两条门禁**：① release 发布的文件清单必须覆盖它自己 manifest 列
  出的每一个文件；② **镜像要跑的文件（`/etc/init.d/`、`/etc/rc.d/`、
  `/etc/uci-defaults/`、`/lib/preinit/`、`/usr/libexec/`、`/usr/sbin/`、
  `/lib/upgrade/`）必须是 100755**。
- **`tools/getrel.py` 的兜底**：manifest 提到、release 没发布的文件改从仓库的
  `templates/` 取；清单校验抽成 `check_manifest()` 函数，`tools/test-getrel.py` 是
  它的回归测试，`prepush.py` 现在会把仓库自己的两个测试一起跑。

**已推送 `cca0ba6`（23:56:52），run182 正在构建。**

**这一轮暴露的东西值得记住**：这是本项目第四次栽在「板子说的」和「构建做的」之间
的差别上，而这一次差的是**一个模式位**。执行位、符号链接、换行、编码——都是构建系
统里「拷贝过去就以为是对的」的东西。完整取证与八个文件的清单见 **docs/89**。

---

### 9.23 无人值守轮次开跑前的体检：十七处修、两处否证（2026-10-26 00:41–01:26）

run182 还在编译，出片后 `g4prime-auto.sh` 会**自己**跑完整一轮（门禁→窗口内刷机→两次冷启动→NAT）。
趁这段时间做了三份只读审计（轮次脚本 / 文档与证据 / 执行位影响），**每条结论都自己重读源码复核过**，
然后修了十六处（①–⑨ 刷机与判据；⑩–⑬ 冷启动验收；⑭–⑮ NAT 腿的收尾；⑯ 门禁问对了问题没有）。
下面每一条都是「不修就一定白跑一轮、
或者会把好的判成坏的」的东西，不是改进。

**① 自动轮会在第一步就退出，因为它根本没有取包这一步。**
`g4prime.sh` 的第 1 步跑 `preflash.py`，而 `preflash.py:100-104` 只看 `staging/trial/<tag>` 在不在，
**不 fetch**（`grep fetch` 零命中）；`flashonpower.sh:21-24` 同样只检查目录。
真正取包的是 `g4flash.sh:59`——**在窗口里面**。
⇒ tag 一出现，轮次打印「there is no such directory」，`g4prime.rc=1`，一个字节都不会写。
run181 那一轮之所以能跑，是因为取包是手工做的。
**已修**：`g4prime.sh` 加了第 0 步，用 `ci-build-repo/tools/getrel.py` 在窗口外把 release 取下来，
失败就退出——顺带把「下载坏了」这种失败也从窗口里挪到了窗口外。

**② 插座监视器比 release 先死。**
`tools/plugwatch.ps1` 是 `-Seconds` 到点自己退的（`:18` 的 deadline、`:35` 的循环），
上一个实例 19:15:27 起、跑 21600 秒 ⇒ **01:15:27 停**。它一停，`staging/ctl/linkup` 不再被盖，
而 `flashonpower.sh:30` 等的就是 `linkup -nt .flashwant` ⇒ 空等 100 分钟（`:37`）后 exit 1。
**已修**：00:48:50 换成一个 28800 秒的实例（到 ~08:49）。新实例的第一行是 `00:48:58 link  -> Up`，
**这一行故意不盖 linkup**（`:26-31`：开机时已经在 Up 不是跳变），下一次真实跳变在 ~01:01。

**③ 冷启动失败不进退出码。**
`g4accept.sh:30-37` 把两次冷启动的 rc 只 `echo` 出来，末尾 `exit $rc` 用的是 NAT 轮的。
⇒ 板子根本起不来，轮次照样报 0。**已修**：改成 `worst`，任何一次冷启动失败都让轮次非 0。

**④ 「NAT 轮的绿」不是一条判据，所以新增了一条。**
`natcounters.sh`、`natevidence.sh` 只打印不 assert，`natround.sh` 是 `set -e` 但没有 pipefail
且六处 `... | tail`（管道退出码是 tail 的 0）。⇒ §9.22 那种「镜像里没有规则」的轮次**返回 0**。
新增 `staging/ctl/natverdict.py`：从**轮次捕获的字节**里要三样东西——
masquerade 计数 > 0、客户端的 ping 一个不丢地回来了、conntrack 里有客户端的流。
`natrun.py` 现在在轮次结束后跑它，不过就返回 1（**只能把绿改成红，不能反过来**）。
判据的控制组全是盘上真件（`staging/ctl/test-natverdict.py`，5/5）：
正例 `g3nat6.txt`（01:19 那次真 PASS 的 G3 轮）；
反例 `g4nat-round.txt`（run181 断在第 2 步）、`g4nat-evidence.txt`（无规则那次）、
以及串口日志里板子自己打的 `oifname "lan3" counter packets 0 bytes 0 masquerade`
——「装了但一个包都没数」，正是骗过台架的那张脸。

**⑤ `getrel.py` 的「无参数列出 release」从来没跑通过。**
`tools/getrel.py:143` 写的是 `if len(sys.argv) < 1` ——argv 永远 ≥1，所以直接
`IndexError`。改成 `< 2`，实测能列出 release 与每个 release 的资产清单
（**这也是「取包通道今晚可用」的正例**：API 走代理、匿名可读）。

**⑥ 门禁有两个副本，跑的是影子那份。**
`g4prime.sh` 调 `staging/ctl/preflash.py`，正本在 `ci-build-repo/tools/preflash.py`。
逐行 diff：只差两行注释里的路径，行为一致——**今晚没吃亏，但下次改正本就会吃亏**。
已把 `g4prime.sh` 指向正本，并用两个真实 release 给门禁做了正例和反例：
run181 **放行**（11 项 OK，含「rootfs 里有 `sk-nat`」「uci-defaults 在镜像里」），
run166 **拒 3 项**（超 8 MiB 达 7315 字节 / 无 `sk-nat` / 既无 rc.d 链接也无 uci-defaults）——
和 §9.11、§9.19 记录的那三条一字不差。门禁不是那种一次都没响过的检查。

**⑦ 分区的读回比对过去只打印、不进退出码。**
`g4flash.sh` 的第 6 步是 `$SSH 'sh -s' <<EOF`，退出码是 ssh 的 0——板子答什么都一样。
⇒ 读回 MISMATCH 也照样往下冷启动。现在写到 `staging/ctl/g4flash-readback.txt`、`cat` 出来、
`grep -qE 'MISMATCH|no partition'` 就 exit 1。
控制组：真件 `staging/ctl/flashbyhand.txt`（run181 的三行 `kernel/dtb/root: OK <md5>`）
**不匹配**（grep rc=1），而 MISMATCH 文本匹配（rc=0）。
这条是 §9.11「任何刷完都必须 mtd verify」在轮次脚本里的落点——`mtd write` 静默截断还报 success。

**⑧ 轮次中途失败会把 `ser-nostop` 留在 off，把整条验收腿一起废掉。**
第 3 步把标志文件改名成 `ser-nostop.off`（让 relay 送空格、把板子停在 U-Boot 提示符好起 RAM 镜像），
第 7 步才放回；而第 3 到第 7 步之间任何一条早退（加载器没起来、推包失败、现在多了的读回失败）
都把它留在 off。此后每一次冷启动都会停在 `=>` 而不是自己从 flash 起——
**验收会全灭，而且原因和镜像无关**。原来的写法是在这些地方**给人打一条 `mv` 建议**
（`:154`、`:177`），可这一轮是 02:00 没人看。
**已修**：`trap restore_flag EXIT`（函数体是 `if` 判断，`set -u` 下安全）。
控制组：把 `g4flash.sh` 里那个函数**原样抽出来**跑，标志文件回到原位、`rc` 仍是 1
（失败照旧往外传，不会被掩盖）。

**⑨ 证据文件名不带 run 号 ⇒ 半路死的轮次会把上一轮的证据当成自己的。**
`g4prime.sh` 收尾时 `cat g4cold1.txt` / `cp g4nat-round.txt`，而这些文件的名字里没有 run；
run181 就是被同一形状的坑咬过（第 2 步没复制 sysupgrade，于是送上去的是上一轮的旧文件，§9.21 坑 3）。
**已修**：轮次开头落一个 `staging/ctl/.round-start` 标记，打包证据的 `show()` 只引用
**比标记新**的文件，否则写 `(stale: ... this round did not write it)`。
控制组（用抽出来的真函数跑）：真的 run181 文件 ⇒ **stale**；本轮写的文件 ⇒ 打内容；文件不存在 ⇒ `(no file)`。
`fill86.py` 那条「按 run 号取、不回退」是同一件事在收证据端的对应（§9.23③-收证据）。

**⑩–⑬ 验收这条腿（第二次只读审计，01:07–01:10）**

**⑩ 「原门禁」的退出码恒 0，所以它一直是装饰。**
`tools/verify_router.sh:87` 是文件最后一行 `[ "$fail" -eq 0 ] && echo 'USABLE' || echo 'not usable yet'`
—— **两个分支都是 echo，状态永远是 0**。`coldboot.sh:89 vrc=$?` 于是白拿，
`coldboot.sh:97 rc=1; [ $g4rc -eq 0 ] && [ $vrc -eq 0 ] && rc=0` 里那条"五个通道的原门禁"
从来没有参与判定。**已修**：末尾加一行 `[ "$fail" -eq 0 ]`。
控制组：真板子 `5 pass / 0 fail — ROUTER IS USABLE` **rc=0**（这个 0 现在是挣来的）；
反例 `BOARD=192.168.9.99 MAC=de-ad-…` ⇒ arp/http/ssh 三项 FAIL、**rc=1**。
（历史上文档里写的「5/5 PASS」读的是**打印文本**，那些结论不因此作废。）

**⑪ getty 通道会在"录音机中途重开"时假阴。**
它只认 `login:` / `root@OpenWrt:~#` / `Please press Enter to activate`，
而 procd 那行在内核时间 **9.6 s**。01:02 那次我把 relay 换给保活环，环的实例 01:02:17 才开端口，
板子 01:02:10 就上电了 ⇒ 那一行正好落在缝里，一块 arp 4/4 + http 200 + ssh 6.18.38+ 的板子
**自己的 console 通道判了 FAIL**。
**已修**：模式表加上 `procd:`、`kmodloader:`（这条通道的作用域按它自己的注释就是
"分开 init 没跑 与 用户态起来了"，这两行正是那一线）。
拒绝控制：日志里那一刀 panic（banner 在 392936 行、`Requested init /bin/busybox failed (error -20)`
在 395203 行）**五个模式 0 命中** —— 放宽没有把门放宽掉。

**⑫ `coldboot.sh` 判"这一次冷启动发生了"没有身份判据。**
它的判据是 `:30` 取 serial.log 现长 + 新段里出现 `Linux version`（`:37`），
ssh 那边 `:52` 只要 `uname -r` 有值就算板子回来了 ——
**板子没重启也会算"这次冷启动"**（RAM 镜像自复位、panic 重启、relay 重开吐旧积压都可能被认账）。
**已修**：ssh 那一步同时取 `uname -r` 与 `/proc/uptime` 的第一段，
要求 `uptime < 600` 才认；并把两种失败分开报：「答了但从未重启（插座没断 / 卡在旧系统）」
和「完全不应答」。阈值 600 不是拍的：ssh 在上电后 ~15 s 就答，窗口全长 930 s，
所以 600 仍然否得掉"上一窗口那次启动"，又容得下"对这一次看得晚了一点"。
实测真板子：`kern=[6.18.38+] up=[513]` ⇒ 接受；同一块板在下一个窗口之前会被拒。

**⑬ 验收没有一处断言 `ser-nostop` 在场。**
标志文件在 `:off` 状态时 relay 会打断倒计时 ⇒ 板子停在 `=>`，
于是这条腿会去判**一个从未启动的镜像**，结论会写成"镜像起不来"。
**已修**：`g4accept.sh` 入口先断言标志文件在，不在就 exit 1（只有 `.off` 时额外提示怎么恢复）。
`g4flash.sh` 的 trap（⑧）负责还回来；这是从"会为此买单的那一侧"再看一遍。
控制组：把守卫原样抽出来跑——无文件 rc=1、只有 `.off` rc=1+提示、文件在 rc=0。

**⑭–⑮ NAT 腿的收尾（第三次只读审计，01:12–01:14）——这两条是今晚最贵的**

**⑭ 收尾的那条 ssh 在 ICS 开着的时候必然连不上，而它的失败被 `| tail` 掩掉。**
`tools/restore.sh` 调 `portmove.sh back`，只传了 `SKD_HOST=$WAN`，没传 `SKD_BIND`
⇒ 沿用默认 `-b 192.168.9.25`；而**第 7 步是在 ICS 还开着的时候跑的**，
共享一开就把有线网卡重配成只有 `192.168.137.1`（`clientrun.sh:35` 正是用 137.1 做 BindAddress）
⇒ ssh 在 bind 阶段就 255 退出 ⇒ 又被 `2>&1 | tail -20` 变成 0。
后果不是"少一条日志"：**口从没放回**，`wan` 与它的 `uci commit` 留在 flash 里，
`natround.sh:175` 照样打印 "the round is done"；下一次插座断电之后 `192.168.9.1` 不在 PC 这一段，
**此后所有 ssh 工具与冷启动验收一起废**，而且看起来像镜像坏了。
**已修**：bind 按"这块网卡现在到底有没有 137.1"来选（实测当前 ICS 关 ⇒ 0 命中 ⇒ 仍走 9.25；
合成文本 ⇒ 命中），并**去掉那条 `| tail`**——`portmove.sh back` 自己会从**串口**回读桥的端口列表、
不对就 exit 1，那个判定正是"口放回没放回"的全部意义，它必须传到脚本的退出码上。

**⑮ `natround.sh` 只在走到末尾时才复原。**
它 `set -e`，而复原排在最后一步；`grep` 六个脚本**没有一条 trap** ⇒
任何一步失败（最可能的是 `:146 sh natcounters.sh "$WAN"`——唯一不带 `| tail` 的那条，
板子中途失联就整轮退出）就把 ICS、被摘出去的口、`lan wan` 的 dropbear **全留下**。
**已修**：`trap natround_cleanup EXIT` + 两面旗（`RESTORED`、`ICS_ON`）保证不重复复原；
顺带给整个脚本加上 `set -o pipefail` —— `portmove.sh` 自己那句
"the round stops here rather than going on with a bridge that is not the one that was asked for"
**从来就停不住**，因为它的状态被管道里的 `tail` 换了。
控制组（把 `restore.sh`/`pwsh` 换成桩跑抽取出来的真函数）：
`RESTORED=0 ICS_ON=1` ⇒ 复原与关共享都执行；两者都已完成 ⇒ 什么都不做；
内层退出码 7 与 3 ⇒ **原样传出**。
**测试自己也会错，记下来省得再错**：第一版把 `sh` 也做成了桩，于是连测试自己的
`sh -c 'exit 7'` 都被拦下来，读成 rc=0，差点得出"trap 吞状态码"的错误结论。
控制组必须先证明它测的是被测试的那个东西——造失败码要用 `bash -c`，别用被桩接管的名字。

**⑯ 冷启动门禁原来一个问题都不问 run182 要答的那件事。**
`staging/ctl/g4verify.sh` 的 14 项里**没有任何一项**与 NAT 有关（`grep -niE "masquer|nft|nat"` 零命中），
而 run182 之所以存在，就是一个 git 模式位：`/etc/init.d/sk-nat` 是 `100644`，镜像里那个 init 脚本
从来没被执行——规则只在**第一次**开机后还在（uci-defaults 是被 source 的，模式无关）。
也就是说：**两次干净的冷启动并不能证明这次出片修好了它要修的东西**。
已加两项（读板子本身，两个失败原因不同）：`ls -l /etc/init.d/sk-nat` 必须 `-rwx`，
`nft list table ip nat` 必须有 masquerade 行。
控制组用本项目自己抓过的两行真字符串：`-rw-r--r-- …  sk-nat`（§9.22 那块坏镜像）走 FAIL 分支，
`-rwxr-xr-x …  sk-nat`（今天手工 chmod 后读回的）走 PASS 分支，文件不在也走 FAIL。

**⑰ 轮次要在上电沿之前把 `ser-nostop` 移走，不是沿之后。**
run182 的第一次自动尝试（01:15:10 点火）就是这么死的：`flashonpower.sh` 在沿上开工以后，
`g4flash.sh` 的第 1–2 步还要取包+复制四件镜像——实测 **01:17:40 上电 → 01:20:56 才走到第 3 步**
（移走标志）；而在这三分钟里标志一直在场 ⇒ relay 不动倒计时 ⇒ 板子在 01:17:41 就启进了 Linux。
于是第 4 步找不到加载器，relay 的 DTR/RTS 复位又对这个板子无效（第二次实测），
`reset` 打在 Linux 控制台上也不是复位 ⇒ 01:24:31 `the loader never showed up`，
**flash 一个字节都没写**（安全失败）。**已修**：`flashonpower.sh` 在等沿之前就把标志移走，
自己再加一道 EXIT trap（沿没等到也要放回）。这样沿一上来 relay 就把板子停在 `=>`，
那三分钟板子在 prompt 上等我们。窗口内取包不是问题：`getrel.py:190` 对已在盘上的文件
打印 `(already here: N bytes)` 直接跳过。
**顺带两条实测的正面证据**：① ⑧ 那道 trap 在**真实失败路径**上响了——日志先打
`g4flash: the flag file is still away - put it back with:`（旧写法：给人一条建议），
紧接着打 `g4flash: the flag file is back on the way out`，`ser-nostop` 回到原位、`.off` 消失；
② ⑨ 之后 `g4prime.rc` 仍是 22:50 的旧文件（轮次失败时不写它），**读退出码要看 mtime**。

**否证一：`trial-files/` 那 5 个 `100644` 不是 bug。**（子代理报的是"同类缺口"，我不接受，去查了构建路径）
`workflow:305` 的 `cp -a` 后面紧跟 `:306 chmod 755 openwrt/files/etc/rc.d/* openwrt/files/usr/libexec/*`
（`git log -S` 显示它自 `6d076d4`（2026-10-10）就在），所以 S15trial/S26nat/S27labclient/trial-blink
**从来没在镜像里是 0644**——docs/07:5889 记的出货件就是 `-rwxr-xr-x`。
`lib/preinit/05_trial_alive` 更根本：preinit 在 OpenWrt 里是被 **source** 的
（本仓 zrec 的 `85_zrec:2-4` 自陈「SOURCED，只能定义函数」，`Makefile:43` 还用 `INSTALL_DATA`(0644) 装它，
而它的 `preinit_main` 在板上确实跑过）。⇒ 它不该算 bug，是 `prepush.py:49-50` 的 MUST_RUN 把
`/lib/preinit/`、`/etc/uci-defaults/` 也列进去了才看着像。
我还是做了两件无害的事：git 位对齐成 `100755`（exec 与 source 都走得通），
并把 `overlay_modes_check` 从只扫 `overlays` 扩到 **`overlays` + `trial-files`**
（正例 `prepush: 14 files, 0 problems`；反例把 S15trial 调回 100644，门禁点名它并给 1 problem）。
RAM 镜像的开机行为**逐字节等价**，今晚那一轮不受影响。

**否证二：没有加「剩余窗口不足就不开工」。**
`linkup` 的章正是在 **Up 沿**盖的（`plugwatch.ps1:29-31`），`flashonpower.sh` 每 5 秒看一次，
所以开工时刻就是窗口起点，15.5 分钟全在前面。run181 那次被砍（22:28:41 起、22:43:35 砍）
是因为**取包和推 sysupgrade 都在窗口里**做——那正是①已经挪出去的东西。
加一条窗口算术只会引入新bug，买不到东西。

**已知、未修、别当成已修**（今晚的轮次会带着它们跑，`natverdict` 兜住了最关键的一条）：
`natround.sh` 无 pipefail + `| tail` 吞步骤失败；`g4watch.sh:21-34`、`g4natwatch.sh:21-28`
循环超时后照样打「the power cycle happened」；`g4verify.sh:114` 有个 `|| true`；它的 #13/#14 读的是「上一段 `Linux version` 之后」的日志，板子中途死了那两项照过（⑫ 补的是 ssh 侧的身份，补不住这两项）；验收全程不读 `plugphase.log` 来避让断电，跨 15:30 是常态、只重试。
NAT 腿里还有几处**只打印不判**的（今晚由 ④ 的 `natverdict` 在末尾兜住，单条没修）：
`tools/labupstream.ps1:90/92` 把异常写成 `already shared`，且不断言 `137.1` 真到位；
`clientrun.sh:46-57` 轮询没有 else、`:57 || true`，没租约也返回 0，`:51` 只看 `lease + 192.168.9.`，
不看 ping、不验 netns（`S27labclient:18-21` 允许回落根空间）；`portmove.sh:112` 的 `tail -1` 会被
netifd 的横幅占；`flashpush.sh:85` 的 `sha256sum -c sha256sums-release || sha256sum …`：这文件是有 `set -eu`（:22）的，所以不是「只打印」——真正的洞是 `||` 把**真的 FAILED**（某个镜像哈希不对）和「清单读不到」混成同一种失败，然后后备的裸 `sha256sum` 必然成功、`set -e` 就没得可杀。清单点名的 5 个文件确实全在板上（:49 推 4 个 + 清单，`flash-sk-d840n.sh` 在 :76 从 templates 单独推），所以改成严格判定不会误杀：把输出收进变量，出现 `FAILED|WARNING|can't open` 就在 `--write` 之前退出。
`natverdict` 接受 `-> RC=0` 这种形状，而它可能来自 `S27labclient` 写进 status、被
`natevidence.sh:34` cat 出来的文字——那不构成「netns 里那次 ping 真通过」，所以判绿要看
`0% packet loss` 那一行，不是 RC=0。
`g4flash.sh:63` 取 RAM 镜像用 `|| true` **是有意的**（`:88-96`：release 自带 itb 就用它，
没有就留在盘上那一个，好让「RAM 镜像起不来的 release」也还有一条活路），不算缺陷。
⑦⑧ 两条已经在本节里修掉并给了控制组，不要重复劳动。

**现在的等待链**：`g4prime-auto.sh`（pid 45168，约 **01:44** 到点，270×(20s+一次 ls-remote)）
→ 若它放弃而 tag 才出现，`staging/ctl/g4prime-baton.sh`（00:45 起，240 分钟，写 `g4prime-baton.txt`）
**只在** auto 的错误文件里出现「did not arrive」且 tag 已存在时接管；auto 已经开跑就自己站下。
两者不会同时刷机。

---

### 9.24 run182 的自动整轮：flash 段已经过了（2026-10-26 01:14–01:35）

①–⑰ 那批修正是为这一轮写的；这一节是它自己跑出来的结果。**flash 段成功，
写进去的字节被逐字节读回**；冷启动两次与 NAT 轮在后面（本节留了位置）。

| 时刻 | 事件 | 证据 |
|---|---|---|
| 01:14:35 | tag `skd840n_flash_182_40209332232` 出现，baton 看见第一轮已点火就自己站下 | `g4prime-baton.txt` |
| 01:15:10 | 自动轮点火，**第 0 步取包第一次在真实轮次里跑**（13 MB） | `g4prime-auto.txt`、`g4prime-getrel.txt` |
| 01:16–01:17 | 门禁 11 项全 OK：`the release is fit to be flashed` | `g4prime-release.txt` |
| 01:17:40 → 01:24:31 | 第一次尝试死于 `the loader never showed up`（⑰ 那条时序坑），**一个字节都没写**；⑧ 的 EXIT trap 在真实失败路径上把标志放回 | `g4prime-flash.txt` 两行：`the flag file is still away - put it back with:` 然后 `the flag file is back on the way out` |
| 01:26:08 | 重挂，标志在**等沿之前**就移走 | `g4prime-flash.txt` |
| 01:32:35 / 01:33:10 | 插座断电 / 上电 | `plugphase.log` |
| **01:33:19** | **`the loader is up`** —— relay 在沿上把板子停在 `=>`，⑰ 的修法被真实验证 | `g4prime-flash.txt` |
| 01:34:02 | RAM 镜像里读到身份：`uname -r 6.18.38+`、`root=/dev/mtdblock5` | `g4flash-env.txt` |
| 01:34:44 | `g4flash said 0`，第 7 步把标志放回 | 同上 |

**flasher 自己的校验**（`g4flash-write.txt`）：`sha256sums-release` 五项全 OK
（uImage / dtb / rootfs / sysupgrade / flash-sk-d840n.sh），三个分区都跑了 `mtd verify`。

**⑦ 那道新读回的结果**（`g4flash-readback.txt`，从 RAM 环境里把分区dd回来算 md5）：

```
kernel: OK 170592a71ed0d83b9aea4b77fd2aa628
dtb:    OK 8c460c9794558ac7e18bcb18d903a228
root:   OK 2af47bc59aeb090982e69f1e658487d4
```

这三个 md5 与 PC 上 `staging/trial/flash-{uImage,dtb.bin,rootfs.jffs2}` 的 md5 **逐字节相同**
——「flash 里就是写进去的东西」这一条不再是推断。

两点顺带记下：
- **dtb 的 md5 与 run181 完全相同**（同一件 dtb），变的是 kernel 与 root——run182 要修的就是
  rootfs 里那几个执行位，所以 dtb 相同反而是好消息：**分区表没变，`root=/dev/mtdblock5` 仍然对**。
**一条容易骗人的身份证据，实测过（01:45:01，板子还停在 RAM 镜像里等下一次断电）**：
`/proc/cmdline` 里的 `root=/dev/mtdblock5` **不是**"从 flash 起来了"的证据——RAM 镜像同样带着这条
U-Boot bootargs。真正的判别只有两条，而且都已在门禁里：
`grep ' / ' /proc/mounts` 的根必须是 `type jffs2`（`g4verify.sh:74`；此刻 RAM 镜像里读回来是
`tmpfs / tmpfs rw,relatime 0 0` ⇒ 这就是那条检查的**真实反例**），
以及 ⑯ 新加的 `/etc/init.d/sk-nat` 在不在（RAM 镜像里没有这个文件，实测 `No such file or directory`）。
所以"验收时板子在哪个系统里"这个问题，今晚有了两个互相独立的回答。

- `g4verify.sh:23` 的 `KIMG` 默认还指着 run165 留下的 `flash-uImage.gzipfit`；本轮没踩，
  因为 `coldboot.sh:94` 把它覆盖成 `staging/trial/flash-uImage`。**那个默认值是旧物**，
  下一次谁直接手跑 `g4verify.sh` 就会拿一轮前的镜像去比哈希，判出一个假 FAIL。

### 9.24 续：冷启动 1 过闸（2026-10-26 01:48:40–01:49:37）

断电 01:48:05 → 通电 01:48:40，`coldboot.sh` 全部按新写的样子工作：

| 时刻 | 事件 | 实测 |
|---|---|---|
| 01:48:40 | 内核 banner | `coldboot: the kernel banner appeared at 01:48:40` |
| 01:48:58 | ssh 应答，**身份判定（修⑫）** | `kernel 6.18.38+, uptime 19s` —— 19 < 600，是本次上电的启动 |
| 01:49:23 | `g4verify.sh` 十六项 | `g4: summary 16 pass / 0 fail` |
| 01:49:37 | `verify_router.sh` 五项 + 状态码传递（修⑩⑪） | `verify: summary 5 pass / 0 fail`、`coldboot: verify_router says 0` |

两条新检查就是 run182 执行位问题的现场答案：

- `g4: sknat PASS - -rwxr-xr-x 1 root root 3822 Oct 24 06:18 /etc/init.d/sk-nat`
  —— 板子上是 `-rwx`，不是仓库里曾经的 `-rw-`；
- `g4: masq PASS - the image installed its own masquerade rule (1 line)`
  —— 跑着的 `ip nat` 表里确实有 masquerade 规则。

配套的两条否证（省得把设计当 bug 修）：

1. **「WAN 搬到 lan2 后规则会留在 lan3」——不成立。** `sk-nat` 不是开机跑一次的
   脚本，是 `while true` + `sleep 15` 的循环（`sk-nat:69-95`），每轮重读
   `uci get network.wan.device`，只在设备变了时才 `nft delete table ip nat` 重装
   （`[ "$wan" != "$cur" ]`）。所以轮次第 1 步搬完口，规则最迟 15 秒后自己跟到
   `lan2`；轮次第 3 步本来就要轮询到 120 秒，读证据时规则早就在新口上了。
2. **「`natcounters.sh:24` 读 `lan2` 是上一轮的残留」——不成立。** `portmove.sh:148`
   就是 `set_wan lan2`，`restore.sh` 再 `set_wan lan3` 放回去（`portmove.sh:166`），
   计数读的口和轮次自己定的拓扑一致。

一处子代理的引用错误，记下来免得照它改：它说 `natround.sh:173` 给
`natcounters.sh` 又套了一层 `| tail -20`；实际第 171 行
`sh natcounters.sh "$WAN" 2>&1` 没有管道，那个 `| tail -20` 属于第 173 行的
`clientrun.sh`。按它改会改错文件。


### 9.25 收证据这一环自己会撒谎：fill86 的三处修 + 两个等待者的否证落空（2026-10-26 01:56–02:02）

轮次还在跑，这一段修的是**轮次之后**要用的东西。核心问题不是 `fill86.py` 会不会崩，
而是它会不会把上一轮的数当成这一轮的数写进一份永久文档。

**① 真的漏（子代理逐字抓到的串）**：`EVIDENCE` 里 `flashbyhand.txt` 是**不带 run 号**的
文件，昨天 22:53 落盘、属于 run 181，里面就有 `kernel: OK b2964491dce550c9ccfe17e20aaf76be`
与 `root: OK a491ee2bb765ecaa7da1ba443d74f0e5` —— 而 `<<FLASH>>`/`<<WRONGWAY>>` 两节的
取数函数都无条件读它。修法：新增 `release_dir_mtime(run)` + `stale(name, when)`，
**不带 run 号的文件用时间钉**（早于本轮 release 目录 mtime 01:17:05 的一律不引用），
`flash_write_evidence(run)` 命中时返回「本轮没有手工写盘，磁盘上那个文件早于本轮
release，不引用」而不是返回它的内容。

**② 我自己那条守卫原来是空转的**：上一夜加的「按 `.fill86.state.json` 比对 mtime」
在**第一次**填充时状态文件不存在，整段 `if state and ...` 直接跳过（`load_state()` 返回
None）—— 所以它对今晚这个泄漏一点作用都没有。这就是「我自己造的正例算不算对照」的
反面教材：一条规则在真实首跑路径上根本不执行。现在改成不依赖状态文件。

**③ 「文件在、而且新」不等于「那次启动被验过」**：`g4cold2.txt` 现在只有 69 字节、
内容只有 `coldboot: … waiting for a boot`，而 `section_cold()` 会把这种半截文件照样填进
文档。新增门禁：冷启动文件必须同时含 `g4: summary` 与 `coldboot: done`，否则具名拒绝。

**④ 两处硬写的假事实**（不是正则问题，是 prose 自己编的）：
`section_release` 原文写「it is the first *flash* release this project ever published」——
`staging/trial` 里就摆着 `skd840n_flash_166_*`、`skd840n_flash_181_*`，而且 §9.21 记着 181
已经刷进 flash 并两次冷启动全过；`section_bytes` 里那张表把 2026-10-25 01:35 **手工构建**
的镜像（`329f1bbc…`/`55ee805a…`）列在讲 CI release 的小节里，和 182 的真实哈希
（`170592a7…`/`2af47bc5…`）并排。两句都改成就事论事：182 不是「第一个 flash release」，
它是第一个**镜像自带的 `/etc/init.d/sk-nat` 以可执行位到达板上、自己装上
masquerade 规则**的 release，而证据是下面两次冷启动的实测行，不是断言。

对照（全部用磁盘上真实的东西，没有一条是我造的）：

| 对照 | 输入 | 结果 |
|---|---|---|
| 拒绝 | `fill86.py 182 --reset`（NAT 还没跑） | `rc=1`，四条具名理由；docs/86 md5 仍 `e2de4030…`＝模板 |
| stale 真值 | `flashbyhand.txt`(昨 22:53)、`g4nat-round.txt`(昨 23:33) | `stale=True` |
| stale 假值 | `g4cold1.txt`(今 01:49，本轮真实新文件) | `stale=False` |
| 漏点关闭 | `section_flash(182)` 输出 | 走「本轮自己写的」分支，且 `a491ee2b`/`b2964491` **都不在** |

**⑤ 两个等待者的「超时也说成成功」**：`g4watch.sh`/`g4natwatch.sh` 的 240 次轮询只用
`break` 区分成功，`done` 之后无条件 `echo "watch: the power cycle happened"`；而
`grep … || true` 会把「日志读不到」也当成 0 次，于是**十二分钟没有任何证据**会被宣布成
一次完整的断电，`g4natwatch` 还紧接着去跑 NAT 轮次（`pwsh … labnat-flash.ps1`），并且
它等 ssh 那个循环也没有判定、脚本也没有退出码。修法：`CYC` 标志 + 不成立就 `exit 1`
（并把那一段切片打到 stderr），`g4natwatch` 的 ssh 等待加 `VER` 判定、轮次的 `rc` 传出。
`LOG=${LOG:-…}`、`sleep ${WAIT:-3}` 是为了能用真实抓取件跑对照。

| 对照 | 输入 | 结果 |
|---|---|---|
| 正例 | `g4prime-accept.txt` 里真实的 `01:17:05 Up -> Disconnected` + `01:17:40 Disconnected -> Up` | 宣布，`rc=0` |
| 否例 | `.netround.slice`（真实控制台抓取，0 条断电、0 条上电） | 不宣布，`rc=1`，`cut=0 up=0` |
| 否例 | 空日志（文件在，什么都没有） | 不宣布，`rc=1` |

诚实标注：**没有**磁盘上真实存在的「只断电、没上电」半证据件（`plugphase.log` 286 条
cut 都对 287 条 up，总是成对），所以那一条**未测**，不算已验证。

**⑥ 仍然没修，只把形状写清楚**：`tools/postflash.sh` 的分区回读比报告说的更糟——
(a) 第 232 行 `|| true`；(b) `2>&1 | tee` 让状态本来就是 tee 的；(c) **根本没有比较**：
设备哈希和期望哈希只是打印给人看，heredoc 是文件最后一个可执行块，所以脚本退出码
恒为 0、那段「Expected, md5 of …」永远像回读成功一样打印；另外 `ci-build-repo/` 里没有
`staging/`，从其仓库根跑时这段是够不着的。不改的理由是要测它只能 stub `ssh`，那是我自
造的正例；而今晚的 flash 路径已经有独立且更强的闸门（`g4flash.sh` 三分区 md5 回读，
实测与 PC 侧三个镜像逐字节相同）。

### 9.26 run182 整轮结果：两次冷启动 + NAT 真转发，全绿（2026-10-26 01:33–02:07）

无人值守跑完，没有任何一步是人手工补的。

| 时刻 | 段 | 实测 |
|---|---|---|
| 01:33:10 / 01:33:19 | 上电 → RAM 镜像 loader | `the loader is up at 01:33:19` |
| 01:34:44 | flash 段 | `g4flash said 0`；回读 `kernel 170592a7…`、`dtb 8c460c97…`、`root 2af47bc5…` 与 PC 侧三个镜像逐字节相同 |
| 01:48:05 / 01:48:40 | 冷启动 1 | ssh 01:48:58 `kernel 6.18.38+, uptime 19s`；`16 pass / 0 fail` + `5 pass / 0 fail`；`cold boot 1: 0` |
| 02:03:35 / 02:04:11 | 冷启动 2 | ssh 02:04:25 `kernel 6.18.38+, uptime 16s`；同样 `16 pass / 0 fail` + `5 pass / 0 fail` |
| 02:04–02:06 | NAT 轮次 | `the round is done; the board's own address is 192.168.137.170 on the shared subnet` |
| 02:06:48 | 整轮 | `g4accept: done (0)`、`g4prime.rc` = 0、`g4prime-auto: the round said 0` |

NAT 的证据不是「装了规则」，是**流量过了规则**：

- `root@ImmortalWrt:~# [   59.690634] sk-nat: masquerade installed on lan2` —— 内核时间 59.69 s，
  也就是 `portmove.sh move` 把 WAN 搬到 `lan2` 之后，`sk-nat` 那个 15 秒循环**自己**把规则
  重装到新口上（§9.24 那条否证的现场证实，不是我推测的）；
- `oifname "lan2" counter packets 1 bytes 84 masquerade`；
- 客户端从板上取到租约 `lease: 192.168.9.240/24`，`ping 223.5.5.5` → `1 packets transmitted,
  1 packets received, 0% packet loss`，回程 `ttl=50 time=18.589 ms`；
- conntrack 里是完整的一对：`src=192.168.9.240 dst=223.5.5.5 … src=223.5.5.5 dst=192.168.137.170`；
- `natverdict: PASS (g4nat-round.txt, 9257 bytes)`：规则计数 > 0、0% 丢包、22 处客户端流。
  判绿走的是证据文本，不是轮次自己的退出码（§9.23④）。
- **复位还是没复位，dmesg 自己把三步写出来了**（02:13 实测，这是全部证据里最硬的一条，因为它不是脚本声称的）：

```
[   14.622025] sk-nat: masquerade installed on lan3      # 冷启动 2 开机，默认拓扑
[   59.690634] sk-nat: masquerade installed on lan2      # NAT 轮次第 1 步把 WAN 搬到 lan2
[  104.771614] sk-nat: masquerade installed on lan3      # 第 7 步复位，规则跟回来
```

现在板上是 `oifname "lan3" counter packets 0 bytes 0 masquerade`——**这个 0 是正确的**：重装前 `install_rules` 先 `nft delete table ip nat`，计数器随旧表一起没了；轮次读到的计数在 `lan2` 那张表上。别把它读成「规则不工作」。配置侧同时读到 `network.wan.device=lan3`、`br-lan` 端口 `lan1 lan2 lan4 lan-cpu0`（`lan2` 已还桥）、`br-lan` 上 `192.168.9.1`、绑定源地址 ping 全应答。

- 复位段其余可核：dropbear 回 `lan`、`network restart`、`ensurebenchip: OK`（PC 回到
  `192.168.9.25/24` + `192.168.1.25/24`），ICS 关掉之后板子的 LAN 侧仍可达。

`fill86.py 182 --reset` 现在 `rc=0`、`placeholders left: (none)`，这是 ①–④ 那条**真实正向**
（不是我造的），审计结果：`170592a7…`/`8c460c97…`/`2af47bc5…` 在文档里，run 181 的
`b2964491…`、`a491ee2b…` **0 命中**。

**这一轮真正证明了什么（也说清没证明什么）**：证明的是关键路径第 1 项——9132 内部交换在
mainline 下带着真流量跑通（`br-lan` 收 `lan1/lan2/lan4 + lan-cpu0`，控制台里
`zx279133-switch … lan4: left promiscuous mode` 这类 DSA 端口事件是活的；PC 那根网线在
搬口前后都能通），ImmortalWrt 用户态 + `6.18.38+` 内核能从闪存自举两次并且做 NAT。
**没证明**的：G5（PON 状态机 / OMCI / ONU 角色）照旧冻结，这块板子现在是**实验室路由器**，
不是可用光猫；eth0 / 面板第 4 个 RJ45 在两个 Linux 下都从未发过字节，仍不是门槛。

## 10. 现在的状态（2026-10-26 00:07）

### 10.1 正在跑的东西

| 什么 | 在哪 | pid / 怎么查 |
|---|---|---|
| **run182 的自动整轮** | `sh staging/ctl/g4prime-auto.sh 182 90` | pid 45168，输出 `staging/ctl/g4prime-auto.txt`；**到点约 01:44**（270×(20 s + 一次 ls-remote)，不是正好 90 分钟） |
| **接棒的等待者**（00:45 新加，见 §9.23） | `sh staging/ctl/g4prime-baton.sh 182 240` | 输出 `staging/ctl/g4prime-baton.txt`；只在 auto 的错误文件里出现「did not arrive」且 tag 已存在时才接管，auto 已经开跑就自己站下 |
| 串口 relay | `tools/relayctl.ps1 status` | **01:02 起改由 `sh tools/relayloop.sh 6` 保活**：裸 relay 那个实例 04:49 会到点自杀，而保活环没有钟（实例退出 4 秒后自己重开），单实例由「COM3 真能开」+「数 sh.exe 根进程」两道拒绝保证。换过来以后**实测**抓下 01:01:35 断电 → 01:02:10 上电的整次启动（`sk-nat: masquerade installed on lan3` 在 14.48 s） |
| 插座监视 | `tools/plugwatch.ps1` | **旧实例 32244 已在 00:48 被我杀掉**（它本来会在 01:15:27 自己退，见 §9.23②）；新实例 00:48:50 起、`-Seconds 28800` ⇒ 到 ~08:49，写 `staging/ctl/plugphase.log` + `linkup` |
| TFTP | `python tools/tftpsrv.py staging/trial 69` | pid 35284，`netstat -ano -p udp` 里 UDP 0.0.0.0:69 在 |

CI 侧：run182（`cca0ba6`）16:39Z 还在 `toolchain/gcc/final` + `target/linux`，看门狗判据是 `wrote=yes`
（`python staging/ctl/beat.py 182 flash` 读进度分支，不耗 API 配额）。

`g4prime-auto.sh` 会等 run182 的 flash release 出现（用 `git ls-remote --tags`，不耗
API 配额），一出现就自动跑整轮：**门禁 → 下一个上电窗口刷机 → 两次冷启动 → NAT
轮**。

### 10.2 插座的节拍

断电每 **15 分 30 秒**一次，断 35 秒。断电时刻：23:30:06、23:45:35、**00:01:05**、
00:16:35、00:32:05、00:47:35、01:03:05、01:18:35、01:33:05……
（`staging/ctl/plugphase.log` 是权威，上电时刻 = 断电时刻 + 35 秒）

**刷机必须整个落在两个断电之间**（从 RAM 镜像写三个分区，约 5 分钟）。run181 那一
轮就吃了这个亏：22:28:41 上电时写的，正好切在 22:43:35。

### 10.3 板子现在的样子

- 跑的是 **run181 的镜像**（CI 自己出的片），`uname -r` = 6.18.38+
- `/etc/init.d/sk-nat` 的模式是 `-rwxr-xr-x`——**这是我手工 chmod 的**，为了证明模式
  位就是原因；下一次刷机会被新镜像覆盖成正确的 0755
- 桥的端口 `lan1 lan2 lan4 lan-cpu0`、wan = `lan3`、masquerade 规则在 `lan3`（23:59
  复原过，见 10.5）
- `/etc/uci-defaults/` 是空的（跑过就被删）
- SSH：`root@192.168.9.1`，密钥 `~/.ssh/id_onu`，**必须绑源地址** `-b 192.168.9.25`

### 10.4 PC 现在的样子

- 有线网卡 `以太网`：192.168.9.25/24 + 192.168.1.25/24，**ICS 是关的**
- Wi-Fi 是公网侧（ICS 打开时它是 public，有线是 private）

### 10.5 刚才动过板子的地方（已经复原）

00:03 为了定位「控制台的 uci delete 为什么无效」，在板上把桥的端口列表**删空**做对
照实验——结果那次 delete **成功了**（列表变空），而补回的脚本 `ucideltest.sh` 里
`bssh2.sh` 少写了 `tools/` 目录，**没执行**，板子就那样留了几分钟。
00:05 用 `staging/ctl/uciback.sh` 补回了四个端口（`lan1 lan2 lan4 lan-cpu0`），
wan 仍是 lan3，规则仍在 lan3，SSH 已验证可达。

**结论**：控制台的 `uci delete` 本身是好的，23:16 那次失败另有原因（还没定论）。
端口搬运现在走 ssh（`tools/portmove.sh`），只有网络重启走控制台。

### 10.6 下一步（按顺序）

1. **等 run182 出片**：`python staging/ctl/beat.py 182 flash` 看编译进度；
   `python devtools/runstatus.py --recent 3` 看 run 状态。出片后 `g4prime-auto.sh` 自己接上，
   **现在它会先自己取包、再跑门禁**（§9.23①），过了 01:44 由 `g4prime-baton.sh` 接管。
2. **盯整轮**：`Get-Content staging/ctl/g4prime-flash.txt -Wait`、`g4cold1.txt`、
   `g4cold2.txt`、`g4nat-round.txt`、`g4prime.rc`。
   **判绿用 `python staging/ctl/natverdict.py staging/ctl/g4nat-round.txt`**（§9.23④），
   不要只看轮次自己的退出码；`g4prime.rc` 现在等于「两次冷启动 + NAT 判据」的最坏值（§9.23③）。
   判据自己的控制组：`python staging/ctl/test-natverdict.py`（5/5，正例是真跑过的 G3 轮）。
3. **收证据**：`python staging/ctl/fill86.py 182 --reset` —— run 号**必须显式给**：
   它按 run 号取样，拿不到就拒绝、绝不回退到别的 run 的证据（实测对 run182 报四条理由、
   `rc=1`、docs/86 的 md5 不动）。占位是 **7 个**不是 5 个。
   `python staging/ctl/addh921.py` 只是核对（§9.21/§9.22/§10 已在仓库文件里，跑它是 no-op）。
4. **最后 push**：`python tools/prepush.py` 必须过（末行 `prepush: 14 files, 0
   problems`），然后 `git push`。**`docs/**`、`tools/**`、`trial-files/**` 都在 CI 的触发路径里**
   （§6 已按 workflow 原文改正），所以**在 run182 的 release 落地之前什么都别推**——
   一次 push 就是一会和 run182 抢同一批 runner 的一次构建。
   本地攒着待推：`origin/main..main` 的 2 个提交（`23ec6f6`、`b81cca4`）
   ＋ §9.23 这批（trial-files 的 git 位对齐、prepush 门禁扩到 trial-files、
   `getrel.py` 的 argv off-by-one、本节与 §6 的文档）。
5. **G5（PON 状态机）保持冻结**：按计划第 7 节「PON 状态机工作量过大 → 冻结 M2，保
   留 M1 实验室路由器」。未通过前不要把产物叫「可用光猫」。

### 10.7 这一夜里踩过的坑（省得再踩一遍）

- **`cmd | tee file` 的退出码是 tee 的**，永远是 0。轮次脚本里凡是这么写的，失败都
  会被吞掉。改成写文件再 `cat`。
- **同一条坑在量出来的时候也会踩**：`python x.py 182 | tail -8; echo "rc=$?"` 打出来的是
  `tail` 的 0，不是 x.py 的退出码——2026-10-26 00:55 我就这样把一条**正确拒绝**的检查读成了
  「它不拒绝」。要读退出码就别接管道：先 `> file 2>&1`，`echo $?`，再 `tail file`。
- **`cat file | ssh "cat > file"` 会断在半路**，而且脚本会说 done。现在
  `tools/flashpush.sh` 会比大小、比不上就重传。
- **Windows PowerShell 5.1 按 ANSI 读 .ps1**，脚本里的中文会让它报解析错误——用
  `pwsh`（7），并且给 pwsh 传**Windows 路径**（`cygpath -w`）。
- **python 写重定向的控制台是 GBK**，板子上一个字符就能让整轮死在 UnicodeEncodeErr
  上（`staging/ctl/natrun.py` 已改成 `sys.stdout.reconfigure(encoding="utf-8")`）。
- **git bash 的 MSYS 会把 `/etc/...` 改写成 Windows 路径** → 给板子的命令要带
  `MSYS_NO_PATHCONV=1`。
- **`$HOME` 在 PowerShell 里传给 bash 会被吃掉反斜杠**（`C:Userswoodn/.ssh/...`）→
  含 `$`/引号/`|` 的命令一律写成脚本文件再执行。
- **插座监视器的 `linkup` 只在 Disconnected→Up 时写**，所以「上电」不会误判成断电。
- **relay 会自己死掉**：`tools/relayctl.ps1 status` + 看 `serial.log` 是否还在长；
  复活用 `tools/relayctl.ps1 start 21600`。**冷启动验收期间不能让它死**。
- **`staging/ctl/ser-nostop` 在 = 插座不给 U-Boot 倒计时送空格 = 板子自己从 flash 起
  来**。冷启动验收期间必须保持在场；刷机时它被移走（`ser-nostop.off`），刷完必须
  移回来。
- **trial/mainline 内核的 `reboot` 会把板子卡死**；要复位用 loader 的 `reset` 命令
  或 `tools/hardreset.ps1`（后者会丢横幅，只在没有别的办法时用）。

### 9.27 第三次冷启动：NAT 轮次写进闪存的配置抗住了断电（2026-10-26 02:19:05–02:20:36）

轮次本身的结束条件是「复位成功」，但复位是靠 `uci commit` 落在闪存里的，所以「读回来对」
还不等于「下次上电还对」。接了一次正常的定时断电来验它：

| 时刻 | 事件 | 实测 |
|---|---|---|
| 02:19:05 / 02:19:41 | 断电 / 送电 | `plugphase.log` |
| 02:19:56 | ssh 应答 | `kernel 6.18.38+, uptime 17s` |
| 02:20:36 | 验证完成 | `g4: summary 16 pass / 0 fail`、`verify: summary 5 pass / 0 fail`、`THE BOARD BOOTS OUT OF FLASH AND IS A ROUTER` |

配置字段（同一次启动内，`uptime 90` 时读）：`network.wan.device=lan3`、`br-lan` 端口
`lan1 lan2 lan4 lan-cpu0`、运行中的规则 `oifname "lan3" counter … masquerade`，而这次启动的 dmesg
里 **只有一行** `[   16.883592] sk-nat: masquerade installed on lan3`——`lan2` 在新启动中完全不存在。

所以第 10 项里唯一还没被断电级证据覆盖的一条也补上了：**这块板子现在的状态是可以随时拔电的**，
不需要保全关机。轮次的全部验收（刷写→两次冷启动→NAT 转发→复位→再次冷启动）现在全部闭环。

补一条后续的实测，并把它与上面的闸门区开：02:50:05/02:50:40 与 03:05:35/03:06:10 又断了两次，**这两次没有跑 16 项闸门**，只是在 03:06:19 直接读板：`uname -r = 6.18.38+`、`uptime 15`、`oifname "lan3"` 的 masquerade、绑定源地址 ping `rc=0`。所以准确的说法是：**用闸门验过的冷启动 = 3 次**，**非闸门观察到的自行恢复 = 至少 5 次**。

### 10.8 轮次之后的交接状态（2026-10-26 02:07）—— §10.1、§10.6 里前瞻的部分到此作废

- **§10.6 的 1–4 步全部做完**：run182 出片→整轮无人值守跑完→`fill86.py 182` 已填
  docs/86（`rc=0`，占位符清零）→ `prepush` 过 → 已 push。**第 5 步（G5 冻结）继续有效**：
  未通过 PON 状态机之前，产物一律不叫「可用光猫」。
- **板上现在是什么**：`6.18.38+` + ImmortalWrt（`DISTRIB_ID='ImmortalWrt'`），根是
  `/dev/mtdblock5` 上的 jffs2，三分区回读 md5 与 release 里的三个文件相同；`br-lan` 走
  9132 DSA 端口；`/etc/init.d/sk-nat` 以可执行位到达板上并自己装 masquerade。
  断电窗口照常：每 ~15 分钟断 ~35 s，`staging/ctl/plugphase.log` 是唯一权威。
- **还活着的进程**：`tools/relayloop.sh 6`（常驻，取代定长 relay）、`tools/plugwatch.ps1`
  （`-Seconds 28800`，到 ~08:49）、`tftpsrv`。`g4prime-auto.sh`/`g4prime.sh`/`g4accept.sh`/
  `coldboot.sh` 都已随 `g4prime.rc = 0` 退出，没有孤儿流处理器。
- **下次开工的三件小事**（都不紧急）：① `tools/postflash.sh` 的分区回读**没有比较**，
  三重吞码，见 §9.25⑥；② `portmove.sh:112` 的 `tail -1`
- **审计后澄清的两条（别再当 bug 修）**：`g4verify.sh:118` 取的是 `serial.log` 最后 300 KB，看着像「混用多次启动」，但紧接着的 `awk '/Linux version/{n=NR}…END{for(i=n;i<=NR;i++)…}'` 把窗口**起点重定到窗口里最后一条 banner**，而一次启动只产 ~57 KB（01:48 那次：103382771→103439624），所以 `flood lines this boot` 的「this boot」是**准确的**；第 14 项（banner 在日志里）则是**装饰**——它靠的正是那条用来重定起点的 banner，永远真。真正的「本次启动」身份证据是 `coldboot.sh` 的字节偏移（它先记录 `the console log is N bytes now`，再等 N 之后出现 banner）加 `uname -r` 加 `uptime < 600`，不是这一项。
  可能吃到 netifd 横幅、`g4verify.sh:114` 的 `|| true`、验收不看 `plugphase.log` 避开断电；
  ③ 关键路径第 2 项（PON 状态机）才决定它能不能当光猫用。
