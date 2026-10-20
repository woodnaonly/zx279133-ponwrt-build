# 补百二十一：NAT 路由器这条路，原来内核和镜像两边都是空的

日期：2026-10-21，本地 06:45–07:20。设备：SK-D840N（ZX279133 GPON ONU）。
本文件是 G3（M1 的 NAT 那一半）的定稿依据，以及在没有第二根网线时 M1 能做到哪一步的说明。

---

## 0. 结论先说

1. **计划里的 "NAT 路由器" 之前根本没有落脚点**，而且缺的不是配置，是两样东西本身：
   - **内核里没有 netfilter**。feed 自己的片段就写着
     `target/linux/generic/config-6.18:4003  # CONFIG_NETFILTER is not set`，
     run 51 合并后的内核配置也写着 `# CONFIG_NF_TABLES is not set`、
     `# CONFIG_IP_NF_IPTABLES is not set`。netfilter 的所有符号都在
     `if NETFILTER`（`net/Kconfig:165`）底下，这一关关了，
     conntrack、NAT、nft 就一个都不存在，用户态喊什么都没用。
   - **镜像里没有 NAT 用户态**。run 86 的 rootfs 里有 `ip-full`、`dnsmasq`、
     `dropbear`、`ethtool`、`brctl`、`udhcpc`，**没有 nft，也没有 iptables**；
     busybox 在这个 feed 里连 iptables applet 都没有
     （`package/utils/busybox/config/Config.in` 里搜不到 `IPTABLES`）。
2. **feed 的 nftables 包在��个分支上根本选不出来**：它 `DEPENDS:=+kmod-nft-core`，
   而这个分支里 `package/kernel/modules/` 下的 netfilter kmod 全部 404
   （`nf_tables.ko`、`nft_masq.ko`、`nft_nat.ko`、`nf_conntrack.ko`、`nf_nat.ko` 都试过）。
3. ⇒ **run97（`4129a20`）把两半都补上**：内核里把 netfilter 全部 **编进内核**
   （不是编成模块——编成模块的话 kmod 包里就没有东西可以打包），
   用户态用 `overlays/package/network/utils/nftables/Makefile`：
   feed 自己的配方，**只去掉 `+kmod-nft-core` 这一条依赖**。
4. 顺带查清了三件"名字不对就白等一轮"的事，记在这里：
   - 本树的 masquerade 表达式叫 **`CONFIG_NFT_MASQ`**，不叫 `CONFIG_NFT_MASQUERADE`
     （`net/netfilter/Kconfig:543`）；
   - **counter 表达式在这个树里没有自己的符号**——`nft_counter.o` 是
     `nf_tables-objs` 的一部分（`net/netfilter/Makefile:91`），
     所以只要 `nf_tables` 在，规则里的 `counter` 就能用，计数器是有的；
   - `CONFIG_NF_TABLES_IPV4` 在 **`net/ipv4/netfilter/Kconfig:35`**，
     不在 `net/netfilter/Kconfig`；`"table ip"` 要的就是它。
5. **板上只有一根网线（PC↔UNI2）这件事，现在有了不用等机主的走法**：
   `S27labclient` 在板子自己身上造一个 LAN 客户端（veth 对 + 网络命名空间），
   `S26nat` 新增 `/etc/labwan` 强制上行口（写 `pc` = "PC 所在的那只口"）。
   两者合起来，**PC 当上游（ICS：Wi-Fi 出、有线回，自带 DHCP 和 NAT）**，
   板子的 WAN = PC 所在口，板子的 LAN = 其余三口 + 那个 veth 客户端，
   客户端拿租约、出网、走板子的 NAT —— 计划里 M1 的功能项全部能验，
   唯独**"两口分别插两根线"这件事本身**仍然要一根网线。

---

## 1. 证据

### 1.1 镜像里有什么、没有什么

run 86 的 initramfs rootfs 就是 `staging/trial/trial-initramfs.cpio.gz`，
用 `staging/ctl/cpiols2.py`（这次新写的真 cpio 解析器，旧的
`cpiolist.py` 是正则扫字节，只能看名字）解出：

```
/sbin/ip     -> /usr/libexec/ip-full      (iproute2 全量，netns、veth 都能用)
/sbin/udhcpc -> ../bin/busybox
/usr/sbin/brctl -> ../../bin/busybox
/usr/sbin/dnsmasq, dropbear, ethtool, uhttpd
lib/apk/packages/ip-full.list               (在)
/usr/sbin/iptables                          (不在)
/usr/sbin/nft                               (不在)
lib/apk/packages/nftables.list             (不在)
lib/apk/packages/kmod-*.list               (一个都没有)
```

`lib/apk/packages/` 下 105 个条目里没有任何 `kmod-*` 包，
也没有 `nftables` / `iptables` / `firewall4`。

### 1.2 内核里有什么、没有什么

`staging/ctl/run51-kernel.config`（run 51 合并后的内核配置）：

```
# CONFIG_NETFILTER is not set
# CONFIG_NETFILTER_ADVANCED is not set
# CONFIG_NF_TABLES is not set
# CONFIG_IP_NF_IPTABLES is not set
# CONFIG_VETH is not set
CONFIG_BRIDGE=y
CONFIG_BRIDGE_VLAN_FILTERING=y
```

桥有、netfilter 没有。**桥有就够了 NAT 这条路走不通一半**：
桥决定包到不到 LAN，netfilter 决定出不出得去。

### 1.3 feed 那边是什么样

- `cnjn/ImmortalWrt` 分支 `sr1010-zx279133`：
  - `package/network/utils/nftables/Makefile`：`DEPENDS:=+kmod-nft-core +libnftnl`；
  - `package/libs/libnftnl/Makefile`：`DEPENDS:=+libmnl`（纯用户态 ✓）；
  - `package/kernel/modules/` 下的 netfilter kmod **全部 404**。
- 所以 feed 的 nftables 包**依赖一个不存在的包**：`make defconfig` 阶段就会被丢掉，
  镜像里也就不会有 nft。

### 1.4 树里的符号名（6.18.38，pinned `96e5a241…`）

| 用途 | 符号 | 出处 |
|---|---|---|
| netfilter 总开关 | `NETFILTER`（menuconfig） | `net/Kconfig:165` |
| 高级开关 | `NETFILTER_ADVANCED` | `net/Kconfig:220` |
| 连接跟踪 | `NF_CONNTRACK` | `net/netfilter/Kconfig:82` |
| NAT 核心 | `NF_NAT` | `net/netfilter/Kconfig:418` |
| nft 框架 | `NF_TABLES` | `net/netfilter/Kconfig:466` |
| nft 的 IPv4 族 | `NF_TABLES_IPV4` | `net/ipv4/netfilter/Kconfig:35` |
| nft 的 nat 链类型 | `NFT_NAT` | `net/netfilter/Kconfig:561` |
| nft 的 masquerade | **`NFT_MASQ`** | `net/netfilter/Kconfig:543` |
| nft 的 counter | *（没有符号）* | `net/netfilter/Makefile:91` |
| 虚拟以太网对 | `VETH` | `drivers/net/Kconfig:440` |

`NFT_MASQ` 这条最容易踩：按 `CONFIG_NFT_MASQUERADE` 去 pin，
defconfig 会**不声不响地丢掉**（符号不存在），pin_y 自己的 grep 只查种子文件，
所以本地断言会绿，构建出来的内核却没有 masquerade。

### 1.5 pin 的写法：三个片段都要改

OpenWrt 把 `target/linux/generic/config-6.18` → `target/linux/zte/config-6.18` →
`target/linux/zte/zx279133/config-6.18` 依次拼起来，kconfig **保留最后一条**。
DEVMEM 那三轮就是输在"只改了一个片段"上（见 workflow 里那段散文）。
所以 `scripts/configure-sk-d840n.sh` 现在的做法是：

```sh
for f in target/linux/generic/config-6.18 \
	 target/linux/zte/config-6.18 \
	 target/linux/zte/zx279133/config-6.18; do
	for s in $NF_SYMS; do
		sed -i -e "/^CONFIG_$s=/d" -e "/^# CONFIG_$s is not set\$/d" "$f"
		printf 'CONFIG_%s=y\n' "$s" >>"$f"
	done
done
```

每个片段里旧的两种写法都删掉，新的 `=y` 追加到**文件末尾**——
这样无论拼接顺序如何，最后一条都是 `=y`。

workflow 的 "Assert the kernel really has the PHY driver" 步骤把这 9 个新符号
放进 **reported**（不是 required）：状态照打不误，但符号没进去也不会**扣住**
一个其实能用的 kit（DEVMEM 那一轮的教训）。

---

## 2. run97 改了什么

| 文件 | 改动 |
|---|---|
| `scripts/configure-sk-d840n.sh` | 9 个内核符号 pin 进三个片段；`CONFIG_PACKAGE_nftables=y`；defconfig 后断言 |
| `overlays/package/network/utils/nftables/Makefile` | feed 的 nftables 1.1.6 配方，**去掉 `+kmod-nft-core`** |
| `trial-files/etc/rc.d/S26nat` | `/etc/labwan` 强制上行口；nft 规则加 `counter`；证据里加 conntrack 与 WAN 口字节计数 |
| `trial-files/etc/rc.d/S27labclient` | 新增：板子自己身上的 LAN 客户端 |
| `.github/workflows/build-sk-d840n.yml` | 新符号进 reported 列表 |

镜像里因此会有：`/usr/sbin/nft`（+ libnftnl/libmnl），
内核里会有：conntrack、NAT、nft 的 ip 族、masquerade、veth。
**不需要任何 modprobe**：全是编进内核的。

---

## 3. 没有第二根网线时，M1 怎么验

板子四只 RJ45 里，主线能驱动的是 9132 的四口（`lan1..lan4`，
其中 `lan1` = PPU1 在本 SKU 上没有插线，真实可用的是 `lan2/lan3/lan4`），
第四只（RTL8226，`eth0`）在厂商系统和主线里都是死的。
现在只有一根线在 `lan2`。

### 3.1 单线模式：PC 当上游

```
PC(Wi-Fi 192.168.10.227 ── 互联网)  ≡ICS≡  PC(有线 192.168.137.1)
                                            │  一根线
                                     板子 lan2  ← 板子的 WAN
                                     板子 br-lan = lan1 lan3 lan4 + labhost0(veth)
                                     板子 labcli0(netns) ← 板子的 LAN 客户端
```

- PC 侧：`tools/labupstream.ps1 -Mode on`（HNetCfg 的 ICS COM 接口：
  Wi-Fi 共享为 public，有线共享为 private → 有线口变 192.168.137.1，
  并自带一个 DHCP 服务器）。
- 板子侧：`echo pc > /etc/labwan`，重跑 `S26nat`。
  板子把 `lan2` 拿出桥、交给 netifd 当 WAN，DHCP 从 PC 的 ICS 服务器拿租约；
  dnsmasq 继续在 br-lan 上发租约。
- 客户端：`S27labclient` 造 veth 对，`labhost0` 进 br-lan，
  `labcli0` 进 netns（`CONFIG_NET_NS` 已开），在里面跑 udhcpc，
  然后每 30 s ping 8.8.8.8，把租约、ping 结果和 conntrack 条目写进
  `/tmp/labclient.status`。

这一条链路上依次能被证明的东西：DHCP 服务器（板子给客户端发租约）、
DHCP 客户端（板子从上游拿租约）、桥（包从客户端到 WAN 口）、
路由、**NAT**（conntrack 里那条 192.168.9.x 的记录 + WAN 口字节计数在涨）、
DNS、以及"客户端能上网"。

**证明不了的那一条**：两个 RJ45 上各插一根线的物理形态。
那需要机主插第二根线，插哪只都行——板子自己认（`S26nat` 的搜索 + watcher，
`docs/19` §4）。

### 3.2 双线模式：机主插了线以后

`S26nat` 的搜索顺序是 `lan3` → `lan1` → `lan4`，PC 所在的那只口永不当 WAN；
插上线的瞬间，watcher（60 s 一轮）会认出来，改 `network.wan.device`、
重建桥、交给 netifd 拿租约。**机主不需要知道面板顺序，也不需要碰板子。**

---

## 4. 这一轮还没解决的

- `lan1`（PPU1 / PHY 0x0a）在本 SKU 上没有插线，DSA 里仍然有它；
  判据是 PHY 链路位，不是 `carrier`（厂商的 carrier 不可信，见 `docs/19` §2）。
- NPPT 的"LAN 型 / WAN 型"口型语义仍未实测；run94 起它是运行时开关
  （`/sys/module/zx279133_switch/parameters/wan_type`），等 run96/run97 的产物。
- 刷机（G4）必须等 RAM 试跑先证明 DSA 四口真的在。
