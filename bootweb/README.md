# SK-D840N bootweb

这是独立的轻量恢复程序开发版本，不是可刷 U-Boot。

已实现单页前端、无操作系统的 HTTP 服务、lwIP TCP 桥、更新包校验、固定目标 NAND 擦写引擎、单任务状态机和离线工具。普通构建的 `adapter/vendor_unverified.c` 始终拒绝启动。独立 RAM 只读目标已接通本机 `svn308776` 原始网口和 ECC 读取，但不绑定真实擦除、写页或重启接口。

`.build/bootweb-unadapted.bin` 不能当作 `uboot.bin`、串口救援镜像或 Linux 内核使用。早期 RAM 只读试验见 `reports/2026-11-03-ttl-ram-readonly.md`。**2026-11-05 本轮 `native241` 常驻 bootweb 与 #337 实际安装验收完成。**常驻修正版实写和 ECC／保护区检查、216 项离线回归，以及 #337 真实网页实写均通过：root→dtb→kernel 共 2000 块实际擦除，三段 ECC SHA256、完整尾部 FF 和整个 boot／参数区不变审计通过。实体正常冷启动进入 ImmortalWrt 25.12.1 r0-1b50aaf、内核 6.18.60+，241 MiB root、默认 LAN `192.168.9.1/24` 和真实 LuCI 登录页通过；随后再次实体 Reset 冷启动直接进入常驻网页，无 RAM 加载或新增擦写，当前 boot／参数区哈希仍一致。最终记录为 `reports/2026-11-05-native241-337-acceptance.json`。102 条块首页写入采样不等于编程总页数；释放阶段纠错计数未导出，未记为零。已有 JFFS2 扫描告警未修改。BTL 救援、周期刷写预算和 2.5G 性能仍未宣称通过，网页 U-Boot 自刷仍锁定。

正常上电（不按 Reset）进入 #337；按住 Reset 上电并保持 5 秒可进入常驻 bootweb，地址同为 `http://192.168.9.1/`。本轮完成后，用户可恢复日常通电 20 分钟／断电 30 秒循环；今后刷写仍须暂停定时断电并持续供电。系统默认未设 root 密码，观察工具未代为设置或修改配置。

## 已实现与仍缺少的部分

| 部分 | 当前状态 |
| --- | --- |
| 单文件 HTML | 小于 32 KiB，无框架、CDN、外部字体或外部资源 |
| 校验核心、任务状态机 | 离线测试及设备 Linux ARM64 只读测试通过；不含真实擦写 |
| 擦写引擎 | 仅在电脑内存 NAND 模型上验证 |
| 原生 HTTP 服务 | RAM 只读实机完成网页、上传校验、10 次中断重连及确认/重启 HTTP 409；写入期间的状态查询仅为内存模型测试 |
| TCP 网络栈 | 实际 lwIP 经厂商原始以太网收发，实机 PHY 记录为 MAC 4 / 1000M；不算 2.5G 验收 |
| 厂商薄适配层 | 普通构建仍硬停；独立 RAM 只读适配核对重定位、缓存、收帧、ECC/坏块查询及看门狗关闭状态，不开放持久写入 |
| RAM 入口与 Reset 入口 | 临时四参数命令入口、私有栈和返回 U-Boot 已实测；当前安装版实体 Reset 持续按住 3 秒进入常驻网页已验证 |
| U-Boot 更新包生成 | 工具已实现；不等于生成了含 bootweb 的可启动 U-Boot |
| 常驻补丁 / 241 MiB 根分区 | 本轮实际安装验收完成：常驻及 #337 实写／ECC／尾部 FF／保护区、正常冷启动／默认 LAN／LuCI、安装后实体 Reset 网页重入全部通过 |
| 4 MiB Linux 后备 | 仅实现硬体积检查；现有样本超限，没有划恢复区 |

实现按“未知即拒绝”处理。配置文件、上传内容、测试通过结果均不能解锁厂商适配层。

## 在当前 Windows 工作区构建

依赖：Python 3.13、Zig 0.14.1、lwIP 2.2.0、Capstone 5.0.7。构建脚本不下载安装软件，不连接设备。AArch64 构建会检查完整指令流，拒绝 SIMD/FP 与改写 x18 的指令；IDA 仍是可选的静态逆向工具。

当前已验证的路径：

```powershell
Set-Location 'C:\UGit\openwrt\SK-D840N\ci-build-repo'
$env:PYTHONUTF8 = '1'
& 'C:\Python313\python.exe' 'C:\UGit\openwrt\SK-D840N\ci-build-repo\bootweb\tools\verify.py'
```

该命令重新编译生产校验库、独立的合成基底测试库、原生 HTTP 测试库、网络测试库和 AArch64 ELF；运行全部离线测试，并写入 `.build/offline-verification.json`。主机测试库启用 UBSan trap；原生程序保留 `x18`，所有 C 代码启用警告即错误。

Zig 默认路径为 `bootweb\.build\toolchain\zig-x86_64-windows-0.14.1\zig.exe`；lwIP 默认来自相邻 `uboot-an758x\lib\lwip\lwip\src`。也可显式传入 `--zig`、`--lwip-dir`。只选取 BSD 网络栈源码，没有链接该目录中的 Airoha 板级驱动。依赖指纹见 `tools/dependencies.lock.json`。

分开构建和测试：

```powershell
& 'C:\Python313\python.exe' 'C:\UGit\openwrt\SK-D840N\ci-build-repo\bootweb\tools\build.py' --test-core --test-net --test-service --test-readonly --ubsan
if ($LASTEXITCODE -ne 0) { throw '构建失败' }
& 'C:\Python313\python.exe' -m unittest discover -s 'C:\UGit\openwrt\SK-D840N\ci-build-repo\bootweb\tests' -v
```

`verify.py` 将跳过测试视为失败，退出码 0 只表示离线检查通过。报告中的所有硬件验收与可刷标志仍为 `false`。

## 本机页面预览

```powershell
& 'C:\Python313\python.exe' 'C:\UGit\openwrt\SK-D840N\ci-build-repo\bootweb\tools\serve_demo.py' --port 8088
```

打开 `http://127.0.0.1:8088`。默认只能上传和校验，确认写入锁定。此服务器只监听本机，未提供设备访问后端，按 Ctrl+C 退出。`--simulate-writes --base-boot <完整已知基底备份>` 也只修改电脑里的稀疏 NAND 模型，不写备份文件或设备。

设备端目标仍是 `192.168.9.1:80`，本轮电脑保留现有 `192.168.9.25/24`。电脑当前 1 Gbps 链路不算 2.5G 性能验收，本机预览也不能代替网口实测。

## TTL-USB / RAM 只读目标

本轮保留智能开关“通电 20 分钟、断电 30 秒”的循环。不主动重启、不保存环境、不改分区。普通 `bootweb-unadapted` 仍链接硬停适配器；只有独立 `bootweb-ram-readonly` 目标可选择实验性 `svn308776` 适配。

只读属性是编译边界：真实擦除、写页和重启地址均不绑定；即使传入写能力位，HTTP 确认、重启和直接 writer API 仍拒绝。状态为 `native-ram-readonly`，两个操作许可恒为 `false`。会话令牌来自电脑安全随机数。

运行顺序为 `tools/ram_trial.py` 自然上电检查、`tools/ram_probe.py` 最小四参数探针、`tools/ram_web.py` 原生只读服务。串口为单进程独占 `COM3`、115200 8N1、DTR/RTS 关闭；不启动旧 relay。每一步输出到新的私有目录，失联或启动阶段变化后不重放旧指令。

RAM 只读构建命令（不连接设备、不表示硬件验收通过）：

```powershell
& 'C:\Python313\python.exe' 'C:\UGit\openwrt\SK-D840N\ci-build-repo\bootweb\tools\build.py' --ram-readonly --ram-address 0x89000000 --ram-hardware --ram-probe-report 'C:\path\successful-probe\report.json' --test-readonly
```

该地址只适用于本轮已核对的 DRAM/LMB、DMA 和重定位布局，不是通用装载地址。TTL 工具仍会重新检查运行中代码、命令表、缓存和内存重叠，并经 `loadx` / XMODEM-CRC 核对实际长度与内存 CRC 后才临时接入 `?`。`help` 保留；退出恢复别名、缓存和原始 RX 回调。程序和 64 KiB 私有栈位于 2 MiB 镜像预留内，上传缓冲区为独立 96 MiB 区域。

服务会早于第 17 分钟停止，第 18 分钟前完成清理；本轮单次原生服务另限 3 分钟。上传、网络等待、CRC 和 SHA256 均有截止处理。保护区只走固定物理偏移的 ECC 读取；坏块、短读、不可纠正错误都失败，不跳坏块或改用 raw/OOB。证据与未通过项见 `reports/2026-11-03-ttl-ram-readonly.md`。本轮不涉及前级 BTL 救援验证，也不授权任何 NAND 写入。

`tools/ram_acceptance.py` 只读汇总已有证据：至少两份不同自然启动的 inspection/probe/web 报告、同一构建的已审计 ELF/二进制、浏览器截图和按钮锁定记录、完整 HTTP 检查、跨周期保护区哈希及 17/18 分钟截止。还会核对最终离线报告的源码指纹；单周期、缺项或构建不一致均不能通过。汇总报告即使通过，`flashable_release` 和 `native_write_enabled` 仍为 `false`。

同一供电周期内第二次启动的网口复测失败，恢复两个 PHY 模式缓存后仍未通过，不支持以重复运行代替新的自然供电周期。退出读回检查涵盖别名、缓存、RX 回调、已保存的驱动状态及 PHY 模式缓存，不代表已证明所有硬件状态均可重入。看门狗仅只读核对厂商关闭状态，不代表喂狗路径可用。

## SSH 只读实测

设备运行 Linux 时，可以通过 SSH 临时测试同一份 C 校验核心和 HTTP 服务。工具固定连接 `192.168.9.1`，使用 root 空密码及已核对的主机密钥，需要本机安装 PuTTY。未传 `--run-device-test` 时不会连接设备。

```powershell
Set-Location 'C:\UGit\openwrt\SK-D840N\ci-build-repo'
$env:PYTHONUTF8 = '1'
& 'C:\Python313\python.exe' 'C:\UGit\openwrt\SK-D840N\ci-build-repo\bootweb\tools\build_ssh_harness.py'
if ($LASTEXITCODE -ne 0) { throw '构建失败' }
& 'C:\Python313\python.exe' 'C:\UGit\openwrt\SK-D840N\ci-build-repo\bootweb\tools\test_ssh_readonly.py' --run-device-test
```

测试程序只放在独立的 `/tmp/bootweb-ssh-<随机标识>` 目录，HTTP 仅监听设备回环地址 `127.0.0.1:18088`，通过 SSH 转发访问，不占用现有 Web 管理端口。MTD 节点只读打开，擦写回调固定拒绝，写入与重启能力始终禁用；服务最长运行 3 分钟。退出时核对保护区哈希，并停止自己的进程、删除自己的临时文件。

2026-11-02 的 51 项设备检查和 37 项离线测试通过，记录见 `reports/2026-11-02-ssh-readonly.md`。这仍是 Linux 用户态测试：没有验证 U-Boot 网口、ECC/坏块接口、Reset 入口、串口救援、实际更新或重启，也没有改变分区。测试程序的大小不能证明完整 Linux 恢复镜像符合 4 MiB 限制。

## 更新包工具

校验现有系统包，不会刷机：

```powershell
& 'C:\Python313\python.exe' 'C:\UGit\openwrt\SK-D840N\ci-build-repo\bootweb\tools\package.py' inspect 'C:\path\immortalwrt-sk-d840n-sysupgrade.bin'
```

`make-uboot` 接受两个完整 2 MiB boot 镜像，检查当前基底和全部受保护 boot 字节，再生成固定三成员的候选包：

```powershell
# 仅在已有经过审查的候选 boot 镜像时使用，不会自动制作常驻补丁。
& 'C:\Python313\python.exe' 'C:\UGit\openwrt\SK-D840N\ci-build-repo\bootweb\tools\package.py' make-uboot --current-boot 'C:\path\current-boot.bin' --candidate-boot 'C:\path\candidate-boot.bin' --output 'C:\path\uboot-update.candidate.tar'
```

工具拒绝覆盖已有输出，拒绝全量 boot 载荷、未知基底和保护区变化。校验通过只证明格式与哈希一致，**不证明候选程序能启动，也不证明 BTL、ECC 或串口救援兼容**。

三种布局均有离线校验策略，普通目标仍默认 `current240`，其枚举含义没有改变。独立 `native241` 目标明确使用 root `[0x00f00000,0x10000000)`（241 MiB），不存在 scratch/recovery；`recovery237` 不会因此解锁。不能通过网页或 `--layout` 开关改变真实设备布局。

## native241 / #337 持续供电安装阶段

此模式要求先暂停智能开关定时断电，并在备份、升级及启动验收期间保持供电。原生控制器、ECC、擦除和写页超时仍有效；`TIMING_PROVEN=0` 不会被改成周期供电证明。系统只接受严格包结构、板型、分区、CRC/SHA256 校验，没有关闭校验或任意地址写入选项。

完整备份使用独立编译的 `--ram-readonly --ram-backup --ram-hardware --resident-trial --bounded-spi --backup-continuous` 目标，以及 `ram_web.py --full-backup --backup-only --confirm-continuous-power`。它仍然编译禁写，最长 30 分钟；完整保存及保护区复读后，通过认证的 `/api/backup-finish` 返回并还原入口、缓存和原生状态。旧默认的 17/18 分钟边界保持不变。任何部分备份均不能拼接或用于实写。

随后 `backup_audit.py` 重新散列整片文件，`current_config_backup.py` 只在干净串口交接的 120 秒内核对身份并正常启动现有 Linux，将配置导出到 `/tmp` 后下载电脑；不会自动恢复旧配置。正常启动系统可能自行更新 overlay，不代表 Linux 全程零写入。

最终候选必须绑定这份新备份、当前已安装镜像和新的精确操作请求。TTL 安装器仅允许物理 `[0x20000,0x180000)`，不整分区擦除，不写 BTL、env、boot 尾部或参数区。网页 U-Boot 自刷仍锁定。系统按 root → dtb → kernel 写入，首次擦除前拒绝任何目标坏块，完整擦除并校验所有未使用尾部为 FF；完整读回及整个 boot/参数区审计成功后才允许手动重启。浏览器断线不会重试或中止已接受的擦写任务。

网页地址仍为 `http://192.168.9.1/`，电脑保持 `192.168.9.25/24`。`resident_live.py` 可在精确实写报告绑定后记录新的正常冷启动、实体 Reset 入口或串口 `bootweb` 入口；它不加载 RAM 载荷、不发送刷写/重启命令，不能替代网页实装及系统冷启动验收。只有本轮实际写入、ECC 读回、冷启动和常驻网页入口全部通过才算完成。

持续供电修正版的冷启动默认等待 30 分钟，串口 `bootweb [duration-ms]` 支持最长 1800000 毫秒；旧周期供电和只读试验的 180000 毫秒限制不变。确认任务后，在耗时的包复核和 ECC 保护区比较之前锁定任务生命周期，避免准备阶段因空闲期限到达而返回自动启动。此锁定不放宽控制器、ECC、擦除、写页超时，不代表周期供电预算通过，也不启用网页 U-Boot 自刷。未确认的服务仍有等待上限。

## 源码与交付说明

- `src/`：校验、任务、HTTP、擦写及按键入口逻辑。
- `include/`：平台与 NAND/原始以太网接口。
- `adapter/`：开发入口、链接脚本、lwIP 桥和锁定的厂商适配占位。
- `profiles/`：社区适配基底、原厂对照及前级静态分析记录。
- `tools/`：构建、校验、候选包生成、IDA 分析和后备体积检查。
- `tests/`：合成更新包、内存 NAND、原生 HTTP 与真实 lwIP 的内存测试；`tests/device/` 为单独构建的 SSH 只读测试程序。
- `docs/ADAPTATION.md`：版本与 ABI 证据，仍需完成的硬件接入。
- `docs/SAFETY-AND-FORMATS.md`：固定布局、格式和写入保护。
- `docs/HARDWARE-TESTS-AND-ROLLBACK.md`：实机门槛、备份及串口回退约束。
- `reports/2026-11-02-offline.md`：本次真实完成的检查与未完成项。
- `reports/2026-11-02-ssh-readonly.md`：Linux ARM64 只读实测结果、早期失败及清理记录。
- `reports/2026-11-03-ttl-ram-readonly.md`：TTL 装载、原生 RAM 只读试验、保护区哈希及自然周期验收记录。

bootweb 自有代码按 GPL-2.0-only 提供；lwIP 继续遵循其 BSD-3-Clause 许可，见 `LICENSES/lwip.txt`。厂商 dump、IDA 数据库、构建产物、设备配置及原厂参数不纳入源码交付。未修改工作区里原有 DTS、升级脚本、驱动和 CI 的用户改动。
