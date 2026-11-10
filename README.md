# SK-D840N ImmortalWrt 固件

简体中文 | [English](README.en.md)

为创维 Skyworth SK-D840N（ZX279133）构建 ImmortalWrt 固件，并提供 RAM 恢复镜像和独立的 Bootweb 恢复程序。本仓库保存板级适配、驱动覆盖、构建脚本和测试；GitHub Actions 拉取固定版本的上游源码后编译，不是可直接运行 `make` 的完整 OpenWrt 源码树。

[下载固件](https://github.com/woodnaonly/zx279133-ponwrt-build/releases) · [构建记录](https://github.com/woodnaonly/zx279133-ponwrt-build/actions/workflows/build-sk-d840n.yml)

> 刷写前请备份配置和设备专属数据，确认型号、引导程序及分区布局，并暂停智能插座等定时断电功能。校验失败就停止，不要强制刷入。普通系统升级不会安装 Bootweb，也不负责迁移分区布局。

## 目录

- [项目与支持状态](#status)
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

当前构建基于 ImmortalWrt 25.12.1，固定基底提交 `1b50aaf4`；ZX279133 驱动基底固定为 `96e5a241`，叠加官方稳定补丁至 Linux **6.18.60**，再应用本仓库覆盖。版本与补丁校验和见 [内核版本配置](configs/sk-d840n-stable.json)，每次发布的具体来源见 `kernel-provenance.json`。

| 项目 | 当前状态与边界 |
| --- | --- |
| 启动、系统安装与管理页面 | 仓库记录了 2026-11-05 在测试设备上安装 #337、正常冷启动、默认 LAN 与 LuCI 访问的验收；不代表之后每个构建都已实机验证 |
| 分区布局 | 当前镜像使用 `native241`：241 MiB root，无独立 scratch/recovery 分区；root 为 `mtd5`，kernel 分区为 8 MiB |
| 以太网 | 代码适配四个面板 LAN 口；LAN1 是物理 2.5G 口。协商速率、2.5G 吞吐、冷启动和长期稳定性不能由端口标称或 CI 成功推断 |
| LuCI 管理 | 包含 Argon、ONU 管理页面和 firewall4；功能入口存在不等于线路业务已开通 |
| PON / OMCI | 已有驱动、管理接口及 OMCI 引擎接入，但自动光路激活、RX 分流和 OMCI 完整性密钥生命周期仍有缺项；不能当作已完成运营商认证的光猫固件 |
| 硬件加速 | 内核与工具的可用性由构建检查覆盖，真实流量的硬件卸载验收仍未完成；ONU 加速页只读，原生防火墙配置中的卸载选项需自行审慎测试 |
| Bootweb | 特定测试设备已有常驻版实机安装及实体 Reset 重入记录；公开 CI 包仍是离线候选预发布，不是通用可刷 U-Boot |

实机记录见 [#337 验收报告](bootweb/reports/2026-11-05-native241-337-acceptance.json)，PON 边界见 [ONU 管理说明](docs/100-sk-d840n-onu-management-port.md)。CI 通过仅说明该次构建和所运行的检查通过，不代替光纤入网、救砖或性能验收。

<a id="download"></a>

## 下载与校验

### 选择正确的文件

进入 [Releases](https://github.com/woodnaonly/zx279133-ponwrt-build/releases)，选择对应构建成功的 **flash** 发布，标签形如 `skd840n_flash_<run_number>_<run_id>`。核对提交和发布说明，并到 Actions 查看对应的 `flash` job 是否成功、产物是否通过校验。不要只按列表中最新一项下载，initramfs 和 Bootweb 是不同用途的发布。

| 文件 | 用途 | 能否直接上传到 LuCI 系统升级页 |
| --- | --- | --- |
| `immortalwrt-sk-d840n-sysupgrade.bin` | 已运行兼容系统时的首选升级文件 | 可以，保持原文件，不解包 |
| `immortalwrt-sk-d840n-sysupgrade.bin.sha256` | 上述固件的 SHA256 | 不可以 |
| `flash-sysupgrade.bin` | 与首选升级文件字节一致的兼容文件名 | 可以，适用条件相同 |
| `flash-set.tgz` | 包含分区镜像、刷写脚本和校验清单的维护/救援包 | 不可以 |
| `flash-uImage`、`flash-dtb.bin`、`flash-rootfs.jffs2` | 分区级维护镜像，须来自同一次构建 | 不可以 |
| `sk-d840n-initramfs.itb` | initramfs 模式的 RAM 试运行/恢复镜像 | 不可以 |
| `trial-set.tgz` | kexec 试运行内核、DTB 和脚本；kexec 工具另备 | 不可以 |
| `sk-d840n-bootweb-native241-candidate.tar` | 独立 Bootweb 离线候选包，仅针对指定原始 U-Boot 基底 | 不可以 |
| `sk-d840n-bootweb-native241-tools.tgz`、`sha256sums-bootweb` | Bootweb 配套工具与两个下载包的校验清单 | 不可以 |

Release Assets 可直接下载上述文件。Actions 页面的 Artifacts 下载后有 ZIP 外壳，需要先解开 ZIP；`sk-d840n-sysupgrade` artifact 内含升级 `.bin` 和校验文件。`.bin` 内部是 sysupgrade tar 格式，这是固件协议的一部分，无需再次解包。

### Windows 校验 SHA256

把固件和同一次发布的 `.sha256` 文件放在同一文件夹，在该文件夹打开 PowerShell：

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
3. 打开 [LuCI](http://192.168.9.1/cgi-bin/luci/)，用户名为 `root`，全新配置初始未设密码。进入“系统 → 管理权”设置 root 密码，再接入其他网络。
4. SSH 地址为 `root@192.168.9.1`。在 Windows PowerShell 连接：

```powershell
ssh root@192.168.9.1
```

也可以在设备 SSH 终端运行 `passwd` 设置密码。固件不内置个人 SSH 公钥或设备身份；不要把未设密码的管理接口暴露到不可信网络。

### 网口与以太网 WAN

| 面板接口 | 系统设备名 | 默认用途 |
| --- | --- | --- |
| LAN1（物理 2.5G 口） | `lan1` | LAN，加入 `br-lan` |
| LAN2 / LAN3 / LAN4 | `lan2` / `lan3` / `lan4` | LAN，加入 `br-lan` |
| PON 侧 MAC | `eth0` | 默认 WAN 配置指向该设备；不对应任何面板 RJ45 口 |

四个 RJ45 默认全部作 LAN，没有预留以太网 WAN。`lan0` 和 `lan-cpu0` 是内部接口，不是额外的面板网口，不要为整理端口列表而随意删除。

需要把某个网口改为 WAN 时，以 LAN4 为例：

1. 电脑接 LAN2 或 LAN3，保持一个不参与修改的管理口。
2. 在“网络 → 接口 → 设备”编辑 `br-lan`，从桥端口中移除 `lan4`，保留其余 LAN 与内部接口。
3. 编辑 WAN，设备选择 `lan4`，按上联要求选择 DHCP 客户端、PPPoE 或静态地址；核对 WAN 防火墙区域及 LAN 到 WAN 的转发。需要 IPv6 时同步核对 WAN6 的绑定。
4. 保存并应用，确认剩余 LAN 仍能管理设备，再把上联网线接入 LAN4。

此时 LAN4 不再是管理 LAN。不要把同一端口同时留在 `br-lan` 和 WAN 中。ONU 的“上网配置”与“IPTV 与组播”页面只编辑已有 WAN/桥配置，不会自动创建完整网络拓扑。

### ONU 管理与加速

LuCI 的 ONU 菜单提供运行状态、硬件身份、线路与认证、上网、IPTV、加速和诊断入口，CPU 状态位于系统菜单。设备身份和协议配置保存在 `/etc/config/pon`，缺失时不会自动套用其他设备的身份。

只填写自己设备和运营商提供的参数。保存 SN、LOID 等字段不代表 OLT 注册成功；以太网链路、软件 O-state 或引擎就绪也不能单独证明入网。不要公开配置备份、SN、REGID、PSK 或 LOID 密码。

硬件卸载选项位于原生防火墙页面。若开展测试，应使用 LAN 与 WAN 两端之间的转发流量，核对 `/sys/kernel/debug/zx279133-flows` 的硬件流计数、conntrack 的 `HW_OFFLOAD` 及双向通信；ping 路由器、加载微码或显示 `hw-tc-offload: on` 都不算硬件转发验收。

<a id="upgrade"></a>

## 系统升级

仅适用于已经运行本项目兼容系统、且现有引导程序与目标布局匹配的设备。原厂系统或其他社区固件不在这套直接升级步骤的保证范围内。

### 升级前检查与备份

- 暂停所有定时断电并确保持续供电，准备与当前设备对应的恢复途径；写入顺序为 root → dtb → kernel，断电或坏内核仍可能导致无法启动。
- 在 LuCI“系统 → 备份/升级”下载配置备份到电脑。该备份不等于完整 NAND、U-Boot 或设备专属校准/身份备份，重要原始备份应另行安全保存。
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

1. 在“系统 → 备份/升级”上传已校验的 `immortalwrt-sk-d840n-sysupgrade.bin`，不要上传 ZIP、`flash-set.tgz` 或 Bootweb 候选包。
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

公开的 Bootweb CI 候选包仅针对原始 `U-Boot 2021.01-svn308776` 指定 stage2 哈希，不能用于已经装过其他常驻版本的设备，也不能当成完整 boot 分区镜像。候选包校验成功不等于获得安装授权。具体条件见 [Bootweb 使用说明](bootweb/README.md)、[CI 候选包边界](bootweb/docs/CI-RELEASE.md) 和 [硬件测试与回退](bootweb/docs/HARDWARE-TESTS-AND-ROLLBACK.md)。

<a id="build"></a>

## 自行构建

系统固件使用 GitHub Actions 的 Ubuntu runner 构建；Bootweb 使用独立 Windows job。Windows 本机不需要安装完整 OpenWrt 编译环境。

1. Fork 本仓库，在自己的仓库启用 Actions。
2. 打开 **Actions → ZX279133 SK-D840N Build → Run workflow**，选择要构建的分支。
3. 选择 `mode`，按需设置下面两个选项，再运行：

| mode | 输出 |
| --- | --- |
| `all`（默认） | initramfs、flash 与独立 Bootweb job |
| `both` | initramfs 与 flash，不构建 Bootweb |
| `initramfs` | RAM 镜像与 trial 包 |
| `flash` | 系统升级文件与维护包 |
| `bootweb` | 仅 Bootweb 离线候选包与工具 |

- `clean_cache` 默认 `false`；设为 `true` 忽略 `dl/` 下载缓存。
- `publish_release` 默认 `true`；关闭后不发布 Release，仍可从成功 job 下载 Artifacts。RAM 对照测试可关闭发布。
- 发布 Release 需要仓库允许工作流写入内容；权限错误请检查 Actions 设置和日志，不要把失败的出包当成固件。
- 构建成功后，从自己 Fork 的 Releases 或该次 Actions Artifacts 下载；失败时只取日志诊断，不使用不完整产物。工作流对部分路径的 push 也会自动触发构建。

固定源码与依赖、应用覆盖、检查和打包流程以 [主工作流](.github/workflows/build-sk-d840n.yml) 为准。外部内核目录名仍含 `linux-6.18.38`，不代表最终内核停在该版本；以版本配置和发布溯源清单为准。

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

随固件安装的 `kmod-mtd-rw` 不自动加载。它会解锁包括 boot 和设备参数在内的只读分区，不是普通升级的前置步骤；不要为绕过错误加载它，也不要使用其他内核的模块或强制加载。

<a id="references"></a>

## 维护资料与来源

| 入口 | 内容 |
| --- | --- |
| [ONU 管理说明](docs/100-sk-d840n-onu-management-port.md) | 管理页面、身份迁移、OMCI 和验收边界 |
| [Bootweb](bootweb/README.md) | 恢复程序、依赖、离线测试和设备流程 |
| [U-Boot 兼容性](docs/UBOOT-COMPAT.md) | FIT 校验、会话绑定及布局限制 |
| [测试说明](tests/README.md) | 早期本地排演套件及夹具要求；有历史路径和过时统计，不是当前 CI 总清单 |
| [主工作流](.github/workflows/build-sk-d840n.yml) | 当前 CI 测试命令、工具依赖、构建参数和发布规则 |
| [历史交接](docs/HANDOFF.md) | 早期交接快照；其中状态、端口结论与旧命令需结合后续记录核对 |

`overlays/` 保存 OpenWrt 包和板级配置，`kernel-overlays/` 与 `kernel-patches/` 保存内核适配，`scripts/` 用于配置构建树，`tools/` 包含检查和实验工具。工具目录中有会改变设备状态甚至擦写 NAND 的脚本，不要批量执行来“自检”。

主要上游与参考：

- [cnjn/ImmortalWrt](https://github.com/cnjn/ImmortalWrt)：ImmortalWrt 基底及 ZX279133 构建目标。
- [cnjn/linux-mainline-zte-zxslc-sr1010](https://github.com/cnjn/linux-mainline-zte-zxslc-sr1010)：ZX279133 外部内核与驱动基底。
- [pbs05/ponwrt](https://github.com/pbs05/ponwrt) 及 [openwrt-pon-userspace](https://github.com/pbs05/openwrt-pon-userspace)：构建模式与 PON 用户态参考；其他平台的驱动不能直接视为 SK-D840N 可用。
- [huxiangjs/SK-D840N-OpenWRT](https://github.com/huxiangjs/SK-D840N-OpenWRT)：社区固件、引导程序和布局参考。

各组件按其随附许可证与来源声明使用；完整 NAND 备份、个人凭据、校准数据和设备身份不作为公共固件分发。
