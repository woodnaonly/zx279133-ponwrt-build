# SK-D840N ImmortalWrt 固件

简体中文 | [English](README.en.md)

开发者：[MiniMax-AI](https://github.com/MiniMax-AI)

为创维 Skyworth SK-D840N（ZX279133）构建 ImmortalWrt 固件，并提供 RAM 恢复镜像和独立的 Bootweb 恢复程序。本仓库保存板级适配、驱动覆盖、构建脚本和测试；GitHub Actions 拉取固定版本的上游源码后编译，不是可直接运行 `make` 的完整 OpenWrt 源码树。

仓库里有两条构建线：Linux 7.3.4 线是现在的发布线，Linux 6.18.60 线只构建并上传产物。两条线用同一套用户态文件，所以都带面板口当第二条 WAN 与「WAN 接入口」页面。

[下载固件](https://github.com/woodnaonly/zx279133-ponwrt-build/releases) · [构建记录](https://github.com/woodnaonly/zx279133-ponwrt-build/actions/workflows/build-sk-d840n-73.yml)

> 刷写前请备份配置和设备专属数据，确认型号、引导程序及分区布局，并暂停智能插座等定时断电功能。校验失败就停止，不要强制刷入。普通系统升级不会安装 Bootweb，也不负责迁移分区布局。

## 目录

- [项目与支持状态](#status)
- [两条构建线](#lines)
- [下载与校验](#download)
- [首次登录与网络配置](#setup)
- [系统升级](#upgrade)
- [初刷与恢复](#recovery)
- [自行构建](#build)
- [常见问题](#faq)
- [维护资料与来源](#references)

<a id="status"></a>

## 项目与支持状态

适配目标为 ZX279133、双核 Cortex-A53、512 MiB 内存、256 MiB SPI-NAND 的 SK-D840N。不要仅凭 SoC 相同，就刷入 SR1010 或其他 ONU。本项目也不能保证同名设备的所有硬件、运营商固件和 U-Boot 版本都兼容。

当前发布线基于 ImmortalWrt 25.12.1，固定基底提交 `1b50aaf4`；7.3 线的内核是 vanilla linux-7.3.4，叠加 `kernel-patches/7.3/` 里的 ZX279133 驱动补丁系列；6.18 线是 6.18.38 加官方稳定补丁到 Linux **6.18.60**。版本与补丁校验和见 [内核版本配置](configs/sk-d840n-stable.json) 与 [7.3 版本配置](configs/sk-d840n-73.json)，每次发布的具体来源见 `kernel-provenance.json` 与 `kernel-provenance-73.json`。

| 项目 | 当前状态与边界 |
| --- | --- |
| 启动、系统安装与管理页面 | 仓库记录了 2026-11-05 在测试设备上安装 #337、正常冷启动、默认 LAN 与 LuCI 访问的验收；2026-11-25 至 26 又把 7.3.4 线的镜像装到同一台设备，kernel、dtb、rootfs 与当次产物逐位核对，并留下 LAN 出向、transport VID 63、TCP 校验和三条板上实测记录（[docs/103](docs/103-the-lan-egress-verified-on-the-board-20261125.md)、[docs/105](docs/105-the-transport-vid-63-and-the-dead-egress-20261125.md)、[docs/106](docs/106-the-tcp-checksum-is-half-there-20261126.md)）；不代表之后每个构建都已实机验证 |
| 面板口当第二条 WAN | 「WAN 接入口」页面与三条程序在两条线的镜像里都有，2026-11-26 在测试设备上与 #94 产物逐位核对一致。自适应选口与主备切换尚未在设备上跑通，自适应守护默认也不自启，见[面板口当第二条 WAN](#wan2) |
| 分区布局 | 当前镜像使用 `native241`：241 MiB root，无独立 scratch/recovery 分区；root 为 `mtd5`，kernel 分区为 8 MiB |
| 以太网 | 代码适配四个面板 LAN 口；LAN1 是物理 2.5G 口。四个面板口都可以被选作第二条 WAN，但协商速率、2.5G 吞吐、冷启动和长期稳定性不能由端口标称或 CI 成功推断 |
| LuCI 管理 | 包含 Argon、ONU 管理页面和 firewall4；「WAN 接入口」是 ONU 菜单里的独立页面，功能入口存在不等于线路业务已开通 |
| PON / OMCI | 已有驱动、管理接口及 OMCI 引擎接入，但自动光路激活、RX 分流和 OMCI 完整性密钥生命周期仍有缺项；不能当作已完成运营商认证的光猫固件 |
| 硬件加速 | 内核与工具的可用性由构建检查覆盖，真实流量的硬件卸载验收仍未完成；ONU 加速页只读，原生防火墙配置中的卸载选项需自行审慎测试 |
| Bootweb | 特定测试设备已有常驻版实机安装及实体 Reset 重入记录；公开 CI 包仍是离线候选预发布，不是通用可刷 U-Boot |

实机记录见 [#337 验收报告](bootweb/reports/2026-11-05-native241-337-acceptance.json)，PON 边界见 [ONU 管理说明](docs/100-sk-d840n-onu-management-port.md)。CI 通过仅说明该次构建和所运行的检查通过，不代替光纤入网、救砖或性能验收。

<a id="lines"></a>

## 两条构建线

| 线 | 工作流 | 内核 | 发布 | 产物 |
| --- | --- | --- | --- | --- |
| 7.3 线（现在的发布线） | `ZX279133 SK-D840N Build (Linux 7.3 line)`，`build-sk-d840n-73.yml` | vanilla linux-7.3.4 + `kernel-patches/7.3/` | 发 Release，标签 `skd840n_73_flash_<run_number>_<run_id>` 与 `skd840n_73_initramfs_<run_number>_<run_id>` | Actions artifact `sk-d840n-73-images-flash`、`sk-d840n-73-images-initramfs`、失败时的 `sk-d840n-73-logs` |
| 6.18 线 | `ZX279133 SK-D840N Build`，`build-sk-d840n.yml` | 6.18.38 + 官方稳定补丁到 6.18.60 | 不再发布；Releases 页上已有的 6.18 发布是这条线最后一批，作为存档保留 | Actions artifact `sk-d840n-flash`、`sk-d840n-sysupgrade`、`sk-d840n-initramfs`、`sk-d840n-bootweb-native241`、失败时的 `logs-<mode>` |

几条容易踩的地方：

- 两条线各自计 run 号，同一个编号在两条线上是两次不同的构建。标签前缀不同就是为这个：旧的 `skd840n_…` 是 6.18 线，新的 `skd840n_73_…` 是 7.3 线。
- 两条线共用 `overlays/`（用户态，含面板口当第二条 WAN 那套），7.3 线另加 `overlays-73/` 与 `kernel-overlays-73/`。改 `overlays/` 会同时改变两条线的镜像。
- 7.3 线的镜像门禁是 `tools/check-sk-image-drivers.py`：它把板子要用的驱动逐条在刚构建出来的镜像里探一遍，缺一条就停。
- 7.3 线已在测试设备上实装并与产物逐位核对，过程与结论见 [docs/103](docs/103-the-lan-egress-verified-on-the-board-20261125.md)、[docs/105](docs/105-the-transport-vid-63-and-the-dead-egress-20261125.md)、[docs/106](docs/106-the-tcp-checksum-is-half-there-20261126.md)、[docs/107](docs/107-the-adaptive-wan-and-the-second-wan-20261126.md)。
- 6.18 线仍然可构建、可出包，但不再发 Release。要 6.18 的固件就从它的 Actions artifact 取，并记住那批产物早于 7.3 线。

<a id="download"></a>

## 下载与校验

### 选择正确的文件

固件现在有两个去处：Releases 页上是 7.3 线发的发布（两个 mode 各一个标签），Actions 的 Artifacts 里是两条线的构建产物（带 ZIP 外壳，保留期有限）。不要只按列表里最新一项下载，flash、initramfs 与 Bootweb 是三种不同用途的产物。

7.3 线 Release（标签 `skd840n_73_flash_<run_number>_<run_id>`）：

| 文件 | 用途 | 能否直接上传到 LuCI 系统升级页 |
| --- | --- | --- |
| `immortalwrt-sk-d840n-sysupgrade.bin` | 已运行兼容系统时的首选升级文件 | 可以，保持原文件，不解包 |
| `flash-sysupgrade.bin` | 与首选升级文件字节一致的兼容文件名 | 可以，适用条件相同 |
| `flash-uImage`、`flash-dtb.bin`、`flash-rootfs.jffs2` | 分区级维护镜像，须来自同一次构建 | 不可以 |
| `sha256sums` | 上面这些文件的 SHA256 | 不可以 |
| `sysupgrade-report.json` | sysupgrade 包自己的校验报告（fwtool 元数据、CRC、板型匹配、成员大小、JFFS2 magic，以及 uImage 解压后与构建出的 Image 逐字节相同） | 不可以 |

7.3 线 Release（标签 `skd840n_73_initramfs_<run_number>_<run_id>`）：

| 文件 | 用途 | 能否直接上传到 LuCI 系统升级页 |
| --- | --- | --- |
| `immortalwrt-zte-zx279133-skyworth_sk-d840n-initramfs.itb` | RAM 试运行/恢复镜像 | 不可以 |
| `immortalwrt-zte-zx279133-rootfs.cpio.gz` | 同一份 rootfs 的 cpio 形式，供离线查看 | 不可以 |
| `sha256sums` | 上面这些文件的 SHA256 | 不可以 |

Actions artifacts（两条线都有；7.3 线的是 `sk-d840n-73-images-flash` 与 `sk-d840n-73-images-initramfs`，6.18 线的是 `sk-d840n-flash`、`sk-d840n-sysupgrade`、`sk-d840n-initramfs`、`sk-d840n-bootweb-native241`）：

| 内容 | 说明 |
| --- | --- |
| 与 Release 同一批固件文件，另带 `packages/`、`*.manifest`、`profiles.json`、`version.buildinfo` | 7.3 线的 artifact 内容见 [docs/101](docs/101-the-7-3-4-baseline-and-the-migration-surface-20261122.md) 的迁移面说明 |
| `flash-set.tgz`、`sk-d840n-initramfs.itb`、`trial-set.tgz` | 6.18 线的维护包与试运行包 |
| `sk-d840n-bootweb-native241-candidate.tar`、`sk-d840n-bootweb-native241-tools.tgz`、`sha256sums-bootweb` | Bootweb 离线候选包与工具 |

几条规则：

- Release Assets 可直接下载上述文件。Actions 页面的 Artifacts 下载后有 ZIP 外壳，需要先解开 ZIP。
- `.bin` 内部是 sysupgrade tar 格式，这是固件协议的一部分，无需再次解包。
- **7.3 线的 Actions artifact 只保留 14 天**，过期就没了；要留就及时下载，并自己核对包里的 `sha256sums`。
- 6.18 线最后一批 Release 仍在 Releases 页上，作为存档保留；它不是现在的发布线。

### 在 Windows 上校验 SHA256

7.3 线的 Release 带一份 `sha256sums`。把固件文件和它放在同一文件夹，在该文件夹打开 PowerShell：

```powershell
Get-Content .\sha256sums | ForEach-Object {
    $parts = $_ -split '\s+', 2
    $want = $parts[0].Trim().ToLower()
    $name = $parts[1].Trim()
    $got = (Get-FileHash -LiteralPath $name -Algorithm SHA256).Hash.ToLower()
    if ($want -notmatch '^[0-9a-f]{64}$' -or $got -ne $want) { throw "SHA256 mismatch: $name" }
    "ok  $name"
}
```

6.18 线的 `sk-d840n-sysupgrade` artifact 另带一份 `.sha256`，可以单独校验那个文件：

```powershell
$firmware = 'immortalwrt-sk-d840n-sysupgrade.bin'
$expectedHash = ((Get-Content -LiteralPath "$firmware.sha256" -Encoding UTF8 -Raw).Trim() -split '\s+')[0]
$actualHash = (Get-FileHash -LiteralPath $firmware -Algorithm SHA256).Hash
if ($expectedHash -notmatch '^[0-9a-fA-F]{64}$' -or $actualHash -ne $expectedHash) {
    throw 'SHA256 mismatch'
}
$actualHash
```

文件缺失或校验不一致时不要继续。SHA256 用于核对文件完整性，不能证明硬件适配或下载来源可信。

<a id="setup"></a>

## 首次登录与网络配置

以下默认值适用于本项目固件的全新配置。保留配置升级后，地址、密码和端口分配仍以原配置为准。

1. 用网线直连电脑与设备，开始配置时先断开其他上联，避免与现有网络的地址或 DHCP 冲突。
2. 电脑先用自动获取地址。若没有获得租约，可临时设置为 `192.168.9.2/24`，网关和 DNS 暂不填写。
3. 打开 [LuCI](http://192.168.9.1/cgi-bin/luci/)，用户名为 `root`，全新配置初始未设密码。进入"系统 → 管理权"设置 root 密码，再接入其他网络。
4. SSH 地址为 `root@192.168.9.1`。在 Windows PowerShell 连接：

```powershell
ssh root@192.168.9.1
```

也可以在设备 SSH 终端运行 `passwd` 设置密码。固件不内置个人 SSH 公钥或设备身份；不要把未设密码的管理接口暴露到不可信网络。

### 网口与以太网 WAN

| 面板接口 | 系统设备名 | 默认用途 | 可否选作第二条 WAN |
| --- | --- | --- | --- |
| LAN1（物理 2.5G 口） | `lan1` | LAN，加入 `br-lan` | 可以 |
| LAN2 / LAN3 / LAN4 | `lan2` / `lan3` / `lan4` | LAN，加入 `br-lan` | 可以 |
| PON 侧 MAC | `eth0` | 默认 WAN 配置指向该设备；不对应任何面板 RJ45 口 | 不参与：光口恒为 WAN1 |
| 内部接口 | `lan0`、`lan-cpu0` | 芯片内部口与 CPU 通道，不是插孔 | 不可以 |

四个 RJ45 默认全部作 LAN。要把某个网口改成 WAN，**推荐用下面的[面板口当第二条 WAN](#wan2)页面**；它会出桥、提交后回读已提交文件确认落盘、改错了还能自动回滚。手工改法只在你不用页面时才需要。

手工把某个网口改为 WAN（以 LAN4 为例）：

1. 电脑接 LAN2 或 LAN3，保持一个不参与修改的管理口。
2. 在"网络 → 接口 → 设备"编辑 `br-lan`，从桥端口中移除 `lan4`，保留其余 LAN 与内部接口。
3. 编辑 WAN，设备选择 `lan4`，按上联要求选择 DHCP 客户端、PPPoE 或静态地址；核对 WAN 防火墙区域及 LAN 到 WAN 的转发。需要 IPv6 时同步核对 WAN6 的绑定。
4. 保存并应用，确认剩余 LAN 仍能管理设备，再把上联网线接入 LAN4。

此时 LAN4 不再是管理 LAN。不要把同一端口同时留在 `br-lan` 和 WAN 中。ONU 的"上网配置"与"IPTV 与组播"页面只编辑已有 WAN/桥配置，不会自动创建完整网络拓扑。

<a id="wan2"></a>

### 面板口当第二条 WAN

光口（`eth0`，PON 侧）恒是 WAN1；lan1..lan4 才是可以切换的面板口。页面在"管理 → ONU → WAN 接入口"（路由 `admin/onu/wanport`）。

页面能做的：

| 控件 | 含义 |
| --- | --- |
| WAN2 设备 | 选 LAN1–LAN4 之一，或"不使用（只用光口上网）" |
| 协议 | DHCP / IPoE、PPPoE、静态地址 |
| 主备 | 光口（WAN1）优先，或面板口（WAN2）优先 |
| 自适应 | 开着就由板子自己找有上联的面板口 |
| 排除口 | 永远不许被占用的口，例如后面接着自己 DHCP 服务器的机器 |
| 状态卡 | WAN1/WAN2 的设备、协议与连接状态、当前主备、自适应开关与排除口、桥内网口、NPPT 端口类型、管理口 |

几条规则，改之前先知道：

- **换口是一次事务。** 改完提交，板子先回读已提交的文件确认改动真的落盘，然后起一个 90 秒的看门狗；这期间没有人确认，旧状态自己回来。页面里点"确认保留"即取消回滚。
- **端口类型跟着协议走。** PPPoE 的 WAN 口在 NPPT 里翻成 WAN 型（硬件处理 PPPoE 头）；DHCP、静态地址的 WAN 口保持 LAN 型（普通以太网帧）。驱动的 `wan_type` 参数被显式写成 0，端口类型只由用户态改。
- **自适应靠 DHCP 探测。** 口临时出桥发一次 DHCP，谁应答谁当第二条 WAN；没人应答就放回。管理口和你排除的口永远不动。后台会周期性重试，所以插线晚一点也能被发现。
- **两条 WAN 主备切换，不引入 mwan3。** 首选的一条断了，另一条顶上；首选的回来了再切回去（有迟滞，三轮好、三轮坏才动）。
- 配置落在 `/etc/config/sk_wan`（`auto`、`primary`、`exclude` 三项）。`network.wan2` 接口、`firewall.wan2` 区域和 `firewall.lan_wan2` 转发由首次启动脚本建立，`network.wan.metric` 设成 10、`network.wan2.metric` 设成 20，所以两条 WAN 都通时优先走光口。

**自适应模式需要一个在运行的守护进程**，而镜像默认不带它的开机自启链接。第一次用自适应前先把它开起来：

```sh
/etc/init.d/sk-wan-adapt enable
/etc/init.d/sk-wan-adapt start
```

不带自启时页面仍然可用：手动指定一个面板口当 WAN2 一样能工作，只是没有自动选口，也没有主备切换。

<a id="upgrade"></a>

## 系统升级

仅适用于已经运行本项目兼容系统、且现有引导程序与目标布局匹配的设备。原厂系统或其他社区固件不在这套直接升级步骤的保证范围内。

### 升级前检查与备份

- 暂停所有定时断电并确保持续供电，准备与当前设备对应的恢复途径；写入顺序为 root → dtb → kernel，断电或坏内核仍可能导致无法启动。
- root 是 241 MiB 的单向门分区，没有 A/B：写坏了没有回退，只能重刷或靠编程器。升级前把配置备份取回电脑。
- 在 LuCI"系统 → 备份/升级"下载配置备份到电脑。该备份不等于完整 NAND、U-Boot 或设备专属校准/身份备份，重要原始备份应另行安全保存。
- 确认目标型号、构建来源和校验和。当前发布使用 `native241`，不要把旧的 240 MiB 布局直接升级成 241 MiB，也不要反向降级。
- 当前升级脚本会核对分区标签、索引、偏移、容量及擦除大小，布局未知或不匹配时拒绝。旧系统缺少这些校验时，也不能据此认定可升级。不要使用 `sysupgrade -F`、强制选项或手工擦写来绕过拒绝。

设备 SSH 终端中可只读查看当前版本和 root 容量：

```sh
ubus call system board
cat /proc/mtd
cat /sys/class/mtd/mtd5/size
```

root 容量 `252706816` 字节为 241 MiB，`251658240` 字节为 240 MiB；容量值本身不足以证明整套布局匹配。跨布局迁移需另行准备、验证和备份，不属于普通系统升级。

### 使用 LuCI

1. 在"系统 → 备份/升级"上传已校验的 `immortalwrt-sk-d840n-sysupgrade.bin`，不要上传 ZIP、`flash-set.tgz` 或 Bootweb 候选包。
2. 阅读检查结果；出现型号、容量、布局或格式错误就停止。
3. 同系列兼容升级且需要沿用网络/密码时，可选择保留配置。需要干净配置时取消保留；新系统将恢复默认地址和未设密码状态。
4. 确认后等待写入和重启，期间不要拔电、按 Reset 或重复提交。恢复连接后检查版本、LAN/WAN、密码及所需业务。

保留配置只携带系统支持的配置备份内容，不会自动保留所有自行安装的软件或任意文件；跨版本配置是否兼容仍需确认。

### 使用 SSH

先按前述方法在电脑校验固件。若尚未下载配置备份，在设备 SSH 终端运行：

```sh
sysupgrade -b /tmp/sk-d840n-config-backup.tar.gz
```

回到 Windows PowerShell，把备份取回电脑，再上传固件。使用 `scp -O` 兼容没有 SFTP 服务的系统；当前终端目录应包含已校验的固件：

```powershell
scp -O root@192.168.9.1:/tmp/sk-d840n-config-backup.tar.gz .
scp -O .\immortalwrt-sk-d840n-sysupgrade.bin root@192.168.9.1:/tmp/
```

确认备份已保存在电脑。以下命令在设备 SSH 终端执行，校验文件并只测试升级条件，不写入固件：

```sh
cd /tmp
sha256sum immortalwrt-sk-d840n-sysupgrade.bin
sysupgrade -T /tmp/immortalwrt-sk-d840n-sysupgrade.bin
```

输出的 SHA256 必须与下载的校验文件一致，`sysupgrade -T` 必须成功；有任一失败都停止。

下面两条升级命令按需要**只选一条**。保留配置：

```sh
sysupgrade /tmp/immortalwrt-sk-d840n-sysupgrade.bin
```

或清空配置：

```sh
sysupgrade -n /tmp/immortalwrt-sk-d840n-sysupgrade.bin
```

执行后 SSH 断开属于正常现象。重连时使用保留下来的地址/密码，或干净配置的默认值。配置备份若只放在设备 `/tmp`，重启后就会丢失。

<a id="recovery"></a>

## 初刷与恢复

### 原厂系统或未知引导程序

不要直接照搬上面的 sysupgrade 步骤。本仓库不提供覆盖所有原厂版本的免拆初刷保证。先核对板型、U-Boot、NAND/ECC、分区与设备专属数据，并准备完整备份及已验证的救援手段。

[早期初刷记录](docs/05-openwrt-flash-procedure.md) 和 [刷写操作记录](docs/06-flash-howto.md) 是特定设备、旧镜像和当时布局的历史资料，包含破坏性操作，不能当作当前固件的通用教程。U-Boot/FIT 的检查边界见 [兼容性说明](docs/UBOOT-COMPAT.md)。

### RAM 试运行与恢复镜像

initramfs 发布中的 `sk-d840n-initramfs.itb` 用于经验证的 U-Boot RAM 启动；`trial-set.tgz` 用于有相应 kexec 能力的存活系统。它们不会自动替换已安装固件，但启动路径、内存范围、看门狗及回退方式必须事先核实。kexec 可执行程序及依赖不包含在 release 中，不能仅解开 trial 包就假定可运行。

RAM 镜像包含 `zrec`，启动并确认该次 RAM 系统可达后，可打开 `http://192.168.9.1/` 使用恢复页面。RAM 运行本身不写 flash，页面中的刷写操作会写 flash；必须另行核对匹配的维护包与分区。正常 flash 系统不包含 `zrec`。相关入口见 [试运行脚本](templates/trial-kexec.sh) 和 [RAM 诊断脚本](trial-files/etc/rc.d/S15trial)；诊断镜像带有自动重启逻辑，不能作为日常系统使用。

### 已安装常驻 Bootweb 的设备

仅当设备确实已经安装并验证了对应的常驻版本：正常上电且不按 Reset 进入系统；已验收的 native241 版本可在按住 Reset 上电并保持 5 秒后进入 `http://192.168.9.1/`。恢复时电脑可设为 `192.168.9.2/24`，不要依赖页面提供 DHCP。按该版本页面要求上传匹配的系统包，校验通过后再确认写入。

没有安装常驻 Bootweb 的原厂设备不能凭这一步获得恢复页面。进入恢复页面也不保证能修复损坏的 U-Boot；BTL 救援与网页 U-Boot 自刷未获通用验收，自刷入口仍锁定。

公开的 Bootweb CI 候选包仅针对原始 `U-Boot 2021.01-svn308776` 指定 stage2 哈希，不能用于已经装过其他常驻版本的设备，也不能当成完整 boot 分区镜像。候选包现在只经 6.18 线的 Actions artifact（`sk-d840n-bootweb-native241`）分发，不再有单独的 Release；校验成功不等于获得安装授权。具体条件见 [Bootweb 使用说明](bootweb/README.md)、[CI 候选包边界](bootweb/docs/CI-RELEASE.md) 和 [硬件测试与回退](bootweb/docs/HARDWARE-TESTS-AND-ROLLBACK.md)。

<a id="build"></a>

## 自行构建

系统固件使用 GitHub Actions 的 Ubuntu runner 构建；Bootweb 使用独立 Windows job。Windows 本机不需要安装完整 OpenWrt 编译环境。

1. Fork 本仓库，在自己的仓库启用 Actions。
2. 打开 **Actions → ZX279133 SK-D840N Build (Linux 7.3 line) → Run workflow**（7.3 线）或 **Actions → ZX279133 SK-D840N Build → Run workflow**（6.18 线），选择要构建的分支。
3. 选择 `mode`，按需设置下面这些选项，再运行：

7.3 线：

| mode | 输出 |
| --- | --- |
| `initramfs`（默认） | RAM 镜像；发 Release `skd840n_73_initramfs_<run_number>_<run_id>`，同时上传 artifact `sk-d840n-73-images-initramfs` |
| `flash` | 系统升级文件与分区镜像；发 Release `skd840n_73_flash_<run_number>_<run_id>`，同时上传 artifact `sk-d840n-73-images-flash` |

- `clean_cache` 默认 `false`；设为 `true` 忽略 `dl/` 下载缓存。
- `publish_release` 默认 `true`；关闭后不发 Release，仍可从成功 job 下载 Artifacts。RAM 对照测试可关闭发布。
- 7.3 线的 artifact 只保留 14 天。

6.18 线：

| mode | 输出 |
| --- | --- |
| `all`（默认） | initramfs、flash 与独立 Bootweb job |
| `both` | initramfs 与 flash，不构建 Bootweb |
| `initramfs` | RAM 镜像与 trial 包 |
| `flash` | 系统升级文件与维护包 |
| `bootweb` | 仅 Bootweb 离线候选包与工具 |

- `clean_cache` 默认 `false`；设为 `true` 忽略 `dl/` 下载缓存。
- 这条线只构建并上传产物，不发 Release。

共同的注意事项：

- 发布 Release 需要仓库允许工作流写入内容；权限错误请检查 Actions 设置和日志，不要把失败的出包当成固件。
- 构建成功后，从自己 Fork 的 Releases 或该次 Actions Artifacts 下载；失败时只取日志诊断，不使用不完整产物。工作流对部分路径的 push 也会自动触发构建；README 之类的 `.md` 改动会触发 7.3 线的一次完整构建。
- 两条线各自计 run 号，标签前缀不同（`skd840n_73_…` 与旧的 `skd840n_…`），不要把两条线的编号混着看。

固定源码与依赖、应用覆盖、检查和打包流程以 [7.3 线工作流](.github/workflows/build-sk-d840n-73.yml) 与 [主工作流](.github/workflows/build-sk-d840n.yml) 为准。外部内核目录名仍含 `linux-6.18.38`，不代表最终内核停在该版本；以版本配置和发布溯源清单为准。

<a id="faq"></a>

## 常见问题

| 情况 | 处理 |
| --- | --- |
| 打不开 `192.168.9.1` | 确认网线、电脑地址和所接端口；保留配置升级可能沿用旧地址。改过 WAN 的端口不再是管理 LAN，可换 LAN2/LAN3 等未改动端口；检查 VPN 或其他网卡的同网段路由冲突 |
| 升级提示布局或容量不匹配 | 停止；核对当前 240/241 MiB 布局与目标发布，不用强制选项绕过 |
| SCP 报 SFTP 子系统缺失 | 使用上面的 `scp -O`，不要为上传固件而关闭校验 |
| 系统启动但光纤不上网 | 当前 PON 自动认证与完整 OMCI 链路尚未验收；网页能保存认证参数不等于业务可用 |
| 加速页有接口但没有硬件流 | 管理入口或编译成功不代表流量被卸载；核对真实 LAN↔WAN 转发，保持功能未验收的判断 |
| 2.5G 口显示 1G | 核对对端、网线和实际协商；物理 2.5G 标称不代表当前链路速率或已验证吞吐 |
| 系统无法启动 | 只使用此前在该设备验证过的恢复途径；没有可用 Bootweb、串口或编程器时，不要继续盲写分区 |
| 找不到「WAN 接入口」 | 该页面在 ONU 菜单里（`admin/onu/wanport`）；确认镜像是本仓库固件。配置本身可以先看 `uci show sk_wan` |
| 自适应不选口 | 该口没有应答 DHCP、或它在排除列表里、或它就是你正在用的管理口；先用 `/etc/init.d/sk-wan-adapt enable && /etc/init.d/sk-wan-adapt start` 把守护进程开起来 |
| 升级后某个口变回 LAN | root 与配置没有保住；按[系统升级](#upgrade)一节回查备份与分区，别反复试刷 root，它是单向门 |

随固件安装的 `kmod-mtd-rw` 不自动加载。它会解锁包括 boot 和设备参数在内的只读分区，不是普通升级的前置步骤；不要为绕过错误加载它，也不要使用其他内核的模块或强制加载。

<a id="references"></a>

## 维护资料与来源

| 入口 | 内容 |
| --- | --- |
| [ONU 管理说明](docs/100-sk-d840n-onu-management-port.md) | 管理页面、身份迁移、OMCI 和验收边界 |
| [7.3.4 基线与迁移面](docs/101-the-7-3-4-baseline-and-the-migration-surface-20261122.md) | 7.3 线的起点、与 6.18 的差异、迁移要动的地方 |
| [LAN 出向已验证](docs/103-the-lan-egress-verified-on-the-board-20261125.md) | 板上测到的 LAN 出向结论 |
| [transport VID 63](docs/105-the-transport-vid-63-and-the-dead-egress-20261125.md) | 2.5G 口的 transport VID 修正 |
| [TCP 校验和](docs/106-the-tcp-checksum-is-half-there-20261126.md) | 出向 TCP 校验和为什么要在软件里算 |
| [面板口当第二条 WAN](docs/107-the-adaptive-wan-and-the-second-wan-20261126.md) | 自适应选口、第二条 WAN、端口类型与页面 |
| [LuCI 模块必须是 class](docs/108-the-luci-module-has-to-be-a-class-20261126.md) | 页面模块的加载约定 |
| [Bootweb](bootweb/README.md) | 恢复程序、依赖、离线测试和设备流程 |
| [U-Boot 兼容性](docs/UBOOT-COMPAT.md) | FIT 校验、会话绑定及布局限制 |
| [测试说明](tests/README.md) | 早期本地排演套件及夹具要求；有历史路径和过时统计，不是当前 CI 总清单 |
| [7.3 线工作流](.github/workflows/build-sk-d840n-73.yml) | 当前发布线的构建步骤、门禁与发布规则 |
| [主工作流](.github/workflows/build-sk-d840n.yml) | 6.18 线的构建步骤与检查 |
| [历史交接](docs/HANDOFF.md) | 早期交接快照；其中状态、端口结论与旧命令需结合后续记录核对 |

`overlays/` 保存 OpenWrt 包和板级配置（含面板口当第二条 WAN 那套），`kernel-overlays/` 与 `kernel-patches/` 保存内核适配，`overlays-73/` 与 `kernel-overlays-73/` 是 7.3 线专有的那部分，`scripts/` 用于配置构建树，`tools/` 包含检查和实验工具。工具目录中有会改变设备状态甚至擦写 NAND 的脚本，不要批量执行来"自检"。

主要上游与参考：

- [cnjn/ImmortalWrt](https://github.com/cnjn/ImmortalWrt)：ImmortalWrt 基底及 ZX279133 构建目标。
- [cnjn/linux-mainline-zte-zxslc-sr1010](https://github.com/cnjn/linux-mainline-zte-zxslc-sr1010)：ZX279133 外部内核与驱动基底。
- [pbs05/ponwrt](https://github.com/pbs05/ponwrt) 及 [openwrt-pon-userspace](https://github.com/pbs05/openwrt-pon-userspace)：构建模式与 PON 用户态参考；其他平台的驱动不能直接视为 SK-D840N 可用。
- [huxiangjs/SK-D840N-OpenWRT](https://github.com/huxiangjs/SK-D840N-OpenWRT)：社区固件、引导程序和布局参考。

各组件按其随附许可证与来源声明使用；完整 NAND 备份、个人凭据、校准数据和设备身份不作为公共固件分发。
