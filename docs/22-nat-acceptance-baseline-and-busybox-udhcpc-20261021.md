# 22 — 路由器验收的真实前提：内核 .config 的实物证据、busybox 的 udhcpc、单线 M1 的鸡生蛋死锁

写于 2026-10-21 08:1x。这一轮没有新的板子测量，**全部是取证与代码修正**；
板子上的实测要等 run98/run99 的产物。所有结论都能在本文指出的文件里复核。

---

## 1. 最重要的一步：把 run86 内核里的真实 .config 挖出来

### 1.1 为什么必须挖

`scripts/configure-sk-d840n.sh` 把 netfilter 的九个符号**写进三个 config 片段**
（generic / zte / zx279133），理由是"kconfig 对同一个符号取最后一条语句"。
这个理由在 DEVMEM 上**已经错过三轮**（docs/HANDOFF §0 注释里记着完整过程）：
`include/kernel-defaults.mk` 的 `Kernel/Configure/Default` 会在片段合并**之后**
再追加一段 `CONFIG_KERNEL_*`（前缀剥掉后的）行，于是
`# CONFIG_DEVMEM is not set` 会压过片段里的 `CONFIG_DEVMEM=y`。

同一套机制会不会也压过我们的 `CONFIG_NF_TABLES=y`？**光看片段看不出来**，
因为合并结果只在构建时存在，而构建结果就是我们要验证的东西。

### 1.2 怎么挖

内核的 `.config` 被编进了 Image：`IKCFG_ST` 与 `IKCFG_ED` 之间是一段 gzip。
run86 的产物就在 `staging/trial/trial-Image.gz`（10月21日 04:16 的那一份），
所以这份证据**不需要板子、不需要 CI 日志、也不花 api.github.com 配额**。

工具：`staging/ctl/ikcfg.sh`（新写）

```sh
sh staging/ctl/ikcfg.sh staging/trial/trial-Image.gz NET_NS NETFILTER NF_TABLES VETH DEVMEM
```

实测输出（2026-10-21 07:5x）：

```
    CONFIG_NET_NS                       =y
    CONFIG_NETFILTER                    =y
    # CONFIG_NF_TABLES is not set
    # CONFIG_VETH is not set
    CONFIG_DEVMEM                       =y
```

### 1.3 这份基线读出来的六件事

| 事实 | 出处 | 对本计划的意义 |
|---|---|---|
| `CONFIG_NETFILTER=y` | run86 Image | 内核侧 netfilter 的框架本来就是开的 |
| `# CONFIG_NF_TABLES is not set` | run86 Image | nft 表引擎确实没编进去 |
| `# CONFIG_NF_CONNTRACK is not set` | run86 Image | 连接跟踪也没编 |
| `# CONFIG_VETH is not set` | run86 Image | veth（板子自造客户端）也没有 |
| `CONFIG_NET_NS=y` | run86 Image | **网络命名空间可用** —— `S27labclient` 的 netns 客户端成立 |
| `CONFIG_BRIDGE=y` | run86 Image | 桥可用，LAN 侧成立 |

**整段 netfilter 在 run86 的真实 .config 里没有一个 `=m`。**
这是关键：`.config.override`（优先级最高的一份，由 `package-metadata.pl kconfig`
从各包的 `Kernel-Config:` 生成）只会给**被选中的包**写 `=m`，
给**没被选中的包**写 `# ... is not set`。
既然 run86 的 netfilter 全段没有 `=m`，就说明这个分支里**没有任何包**声明过
这些 netfilter 符号 ⇒ `.config.override` 里不会有它们 ⇒ 我们的片段 pin 不会被它压掉。

### 1.4 片段 pin 到底会不会赢：三条独立证据

1. **`config/Config-kernel.in` 里没有 `KERNEL_NF_*` / `KERNEL_VETH` / `KERNEL_NETFILTER`。**
   整份文件读过（`https://raw.githubusercontent.com/cnjn/ImmortalWrt/sr1010-zx279133/config/Config-kernel.in`）。
   那条 awk 只认 `^(#[[:space:]]+)?CONFIG_KERNEL`，
   没有对应项 ⇒ 追加阶段不会产生 `# CONFIG_NF_TABLES is not set`。
   （唯一沾边的是 `KERNEL_NF_CONNTRACK_TIMEOUT`，那是另一个符号。）
   ⇒ **DEVMEM 那个坑对 netfilter 不成立。**

2. **subtarget 片段里就是 `CONFIG_NETFILTER=y`**，而 run86 的出货内核里
   `CONFIG_NETFILTER=y` —— 片段说的被采纳了。

3. **我们把 `=y` 追加到三个片段的末尾，并把三个片段里原有的
   `CONFIG_X=` 与 `# CONFIG_X is not set` 全删掉。**
   于是不管三个片段的合并顺序是什么，最后一条一定是 `=y`。

⇒ **结论：run98/run99 的内核应该有 `CONFIG_NF_TABLES=y`、`CONFIG_NF_CONNTRACK=y`、
`CONFIG_NF_NAT=y`、`CONFIG_NFT_NAT=y`、`CONFIG_NFT_MASQ=y`、`CONFIG_VETH=y`。**
这六条已经在 workflow 的 `reported` 列表里，构建后会在注解里逐条打出状态
（`=y` / `=m` / `# ... is not set` / `<not in .config at all>`）。

### 1.5 顺带确认的 Kconfig 依赖

本树的符号与依赖（`staging/tree/net_netfilter_Kconfig`、`net_ipv4_netfilter_Kconfig`）：

- `NF_CONNTRACK` 是 **tristate**，`default m if NETFILTER_ADVANCED=n` ⇒ 必须同时钉
  `CONFIG_NETFILTER_ADVANCED=y`，否则它会变 `=m`（模块），而这个分支没有 netfilter 的
  kmod 包（404）。
- `NF_NAT` 是 tristate，`depends on NF_CONNTRACK` ⇒ 两个都要 `=y`。
- `NF_TABLES` 是 tristate，`select NETFILTER_NETLINK`、`select NET_CRC32C` ⇒ 无外部依赖。
- `NFT_NAT` 是 tristate，`depends on NF_CONNTRACK`，`select NF_NAT`，
  `depends on NF_TABLES_IPV4 || NF_TABLES_IPV6` ⇒ `CONFIG_NF_TABLES_IPV4=y` 必须钉。
- `NFT_MASQ` 是 tristate，`depends on NF_CONNTRACK && NF_NAT`，
  `select NF_NAT_MASQUERADE` ⇒ 钉 `NFT_MASQ=y` 即带出 masquerade。
- `NF_TABLES_IPV4` 是 **bool**（不是 tristate），且在 `if NF_TABLES` 里 ⇒ 只能 `=y`。
- `NF_TABLES` **不在** `if NETFILTER_ADVANCED` 块里 ⇒ 不依赖那个开关也能开。

generic 片段里本来就有 `CONFIG_NF_TABLES_ARP/BRIDGE/INET/IPV4/IPV6/NETDEV=y`
（4428–4433 行）—— 它们在 `if NF_TABLES` 里，NF_TABLES 一开就全部生效，
**`NFT_INET` 也就有了**，这是 `table ip` 能工作的前提。

---

## 2. M1 会直接卡死的一个真 bug：busybox 的 udhcpc 不给 `mask`

### 2.1 事实

run86 的 rootfs 是 **apk 格式**的（`lib/apk/packages/*.list`），
整个包清单里**没有 `udhcpc` 包，也没有 `brctl` 包** ⇒
`udhcpc` 和 `brctl` 都是 **busybox 的 applet**。

busybox 的 udhcpc 调租约脚本时给的环境变量是
`ip / siaddr / sname / boot_file / subnet / router / dns / lease / interface`，
**没有 `mask`**；而且它的 `subnet` 是**掩码**（255.255.255.0），不是网络地址。

### 2.2 我们写的两个租约脚本都用了 `"$ip/$mask"`

`S26nat` 的 `/tmp/udhcpc-trial.sh` 与 `S27labclient` 的 `/tmp/labclient-dhcp.sh`：

```sh
ip)	ip addr replace "$ip/$mask" dev "$interface" 2>/dev/null ;;
```

⇒ 地址变成 `192.168.9.100/`，被 `ip` 拒绝；
而脚本把 `2>/dev/null` 挂在命令上 ⇒ **日志里什么都没有**，
看起来跟"上游没接"一模一样。

⇒ **WAN 拿不到地址，板子自造的 LAN 客户端也拿不到地址，M1 必然不通过。**
这是本计划里最贵的一个 bug，因为它在日志上完全隐形。

### 2.3 修法

两个脚本现在自己算前缀长度，**掩码形式、纯数字前缀长度、网络地址三种都收**：

```sh
pfx_of() {
	case "$1" in
	'') echo 24 ;;
	*[!0-9.]*) echo 24 ;;
	*.*)
		case "$1" in
		255.255.255.*) echo 24 ;;
		255.255.*.*) echo 16 ;;
		255.*.*.*) echo 8 ;;
		*) echo "$1" | awk -F. '{n=0; for (i=4;i>=1;i--) { if ($i=="0") n++; else break } p=8*(4-n); if (p<=0) p=24; print p}' ;;
		esac ;;
	*) echo "$1" ;;
	esac
}
```

`subnet)` 那一支**改成什么都不做**：地址一带前缀，连接路由内核自己就加；
而 busybox 给的 `subnet` 是掩码，拿它去 `ip route replace` 是一条无效路由。

### 2.4 这个 bug 是怎么被发现的（方法也值得记住）

不是读代码读出来的，是**把脚本抽出来、用 stub 的 `ip` 跑一遍**：

`staging/ctl/testlease.sh`（新写）把两个脚本从 rc.d 里按 heredoc 原样抽出来，
用假的环境变量（busybox 风格 / 有 `mask` 风格 / 网络地址风格）跑，
比对它**会**敲出的 `ip` 命令。18 项断言，全绿。

测试本身也踩了两个坑，都写在了脚本里：

- `unset ip` 会把**函数** `ip()` 一起删掉（POSIX 的 `unset` 对函数名一样生效），
  于是 stub 没了，脚本自己的 `2>/dev/null` 又把 "command not found" 吞了 ⇒ **测试全绿但什么都没测**。
- 第一次的 awk 把网络地址 `192.168.9.0` 算成 `/31`（写成 `32-n` 而不是 `8*(4-n)`）。
  是这个断言抓出来的。

⇒ **教训：这种"变量可能不存在"的脚本，必须用真实脚本 + stub 跑断言，不能靠读。**

### 2.5 busybox 的 udhcpc 开关语义

原来的 WAN 常驻客户端是 `udhcpc -i "$WAN" -f -t 0 -T 5`，
读起来像"永远重试、每 5 秒一次"。busybox 的 udhcpc 里：

- `-t N` = **发 N 个 discover 包**（不是超时次数）
- `-T SEC` = **租约过期后多少秒再重连**（不是每包间隔）

独立 udhcpc 的 `-t N -T SEC` 才是"N 次、间隔 SEC 秒"。
⇒ 原来那条命令在 busybox 下是"发 0 个包 + 5 秒后重连"，语义完全不是想的那样。
现在改成 `udhcpc -i "$WAN" -f -s /tmp/udhcpc-trial.sh`：
两个开关都不给，busybox 的客户端就持续 discover，这才是 WAN 需要的行为。

---

## 3. 单线 M1 的验收脚本里有一个鸡生蛋死锁

### 3.1 死锁在哪

`tools/labnat-upstream.ps1` 原来的第 3 步是
**"ssh 到 192.168.9.1，在那边看有没有 192.168.137.x 的地址"**。

但单线 M1 的结构是：**板子的 WAN 口 = PC 所在的那只口**。
板子把这个口从 br-lan 里摘出去的那一刻，
板子的 LAN 地址（192.168.9.1）就**离开了 PC 所在的二层域**，
192.168.9.1 从此不应答 —— 而那正是脚本用来查租约的地址。

⇒ 脚本一路等到超时，然后报 "no lease"，
**而租约一直好好地待在板子上**。

### 3.2 改成"从 PC 这一侧发现板子"

路由器在自己所在的网段里找主机，用的就是这两样：

1. **邻居缓存**：PC 自己的 DHCP 服务端和板子之间的那次 DHCP 交互，
   会把板子的地址和 MAC 放进 PC 的邻居缓存（`Get-NetNeighbor`）。
2. **直接探测**：共享网段 `192.168.137.0/24` 上扫一遍 22 端口的 SSH banner。

拿到地址之后，所有证据都用**那个地址**去读（`SshTo`，不再绑 192.168.9.25）。

顺带修的：`S26nat` 现在是 `nohup ... &` 起的后台进程。
原来它是 ssh 命令的前台子进程，而**那条 ssh 连接正是端口换手时断掉的那条** ⇒
脚本会跟着连接一起死。

---

## 4. 驱动侧的复核（读代码 + 对照树里那份已验证的驱动）

| 项 | 结论 |
|---|---|
| `ds->num_ports` | 我们 `9`，树里 RTL8372N 也是 `9`（`staging/tree/drivers_net_ethernet_zte_zx279133-rtl8372n.c:7680`）|
| DSA 口编号 | 我们 4..7 + CPU 8；SR1010 的 DTS 也是 4..7 + 8 ⇒ 形状一致 |
| `DSA_PPU(port)` | `port - 3` ⇒ DSA 4..7 ↔ PPU 1..4 ✓ |
| `DSA_PHY(port)` | `0x0a + port - 4` ⇒ DSA 4..7 ↔ PHY 0x0a..0x0d ✓ |
| tagger | `DSA_TAG_PROTO_ZX279133_RTL8372N`，树里已有（`net/dsa/tag_zx279133_rtl8372n.c`）|
| `dsa_switch_ops` | 我们是树里那份的子集；**树里也没有 `get_port_phy`**，链路状态同样走 `phylink_fixed_state` ⇒ 一致 |
| 用户口的 PHY | 9132 的内部 PHY 在 MDIO 0x0a..0x0d，`phylink_fixed_state` 直接读 BMSR ⇒ 插线能被感知 |
| conduit 探测顺序 | run86 的串口日志已经证明：conduit 在 0.4915s 注册，switch 在 0.5528s 探测 ⇒ **顺序没问题** |

**唯一改动的常量**：`ZX279133_SWITCH_UNI_PORT_MIN/MAX` 由 `0..3` 改为 `1..4`。
这两个常量**只用在一条打印语句里**（`"9132 UNI ports %u..%u"`），
而 NPPT 驱动打的是 "9132 UNI ports 1..4" ⇒ 之前两个驱动对同一批硬件口报了两套编号。

---

## 5. CI 状态与两个 round 的分工

| round | commit | 内容 | 状态 |
|---|---|---|---|
| 96 | `b32b5f8` | 头文件修复 + 工作流超长 | 编译错（`priv->dev`），双 job 失败 |
| 97 | `4129a20` | netfilter 符号 + nft 包 + S27labclient | 同一个编译错，initramfs job 也挂在同一点 |
| **98** | `d42686b` | **修 `priv->dev`**（并顺手修 `&priv->dev` 的取址误用）| 2026-10-21 07:45 起跑 |
| **99** | `f499177` | **租约脚本 `$mask` 修复 + busybox udhcpc 语义 + UNI 口编号 1..4** | 2026-10-21 08:07 起跑 |

run98 的 initramfs 镜像里带的是**旧的**租约脚本（run99 才有新的）。
这不阻塞：`tools/labpush.sh` 会在每次 RAM 试跑前把仓库里当前的
`S26nat` / `S27labclient` 推上板并重跑。

---

## 6. 这一轮真正改了什么（清单）

**仓库内（`ci-build-repo`，会进镜像）**

- `kernel-overlays/drivers/net/ethernet/zte/zx279133-switch.c`
  - `struct zx279133_switch` 增加 `struct device *dev;`，probe 里 `priv->dev = dev;`
    （run96/97 的编译错）。
  - 两处 `&priv->dev` 改为 `priv->dev` —— 成员不存在时编译器只报"没有这个成员"，
    成员补上之后这才会变成下一条错（`struct device **` 赋给 `struct device *`）。
  - `ZX279133_SWITCH_UNI_PORT_MIN/MAX`：`0..3` → `1..4`。
- `trial-files/etc/rc.d/S26nat`、`trial-files/etc/rc.d/S27labclient`
  - 租约脚本自己算前缀长度；`subnet` 支路不再造路由。
  - WAN 常驻 udhcpc 去掉 `-t 0 -T 5`。

**仓库外（本机工具，不进镜像）**

- `tools/labnat-upstream.ps1`：改成从 PC 侧发现板子；S26nat 用 nohup 脱离。
- `tools/labpush.sh`：S26nat 用 nohup 脱离；推完回读 `/tmp/s26nat.log`。
- `staging/ctl/ikcfg.sh`（新）：从 Image 里挖出内核真实 .config。
- `staging/ctl/testlease.sh`（新）：租约脚本的 18 项断言。
- `staging/ctl/awaitrel2.sh`（新）：按 **run id** 匹配发布 tag（不猜 run_number）。
- `staging/ctl/membercheck.sh`：`priv->` 成员交叉核对（正则补上数字，漏过 `saved_vend2_bmcr`）。
