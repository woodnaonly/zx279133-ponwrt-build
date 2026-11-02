# SK-D840N bootweb

这是独立的轻量恢复程序开发版本，不是可刷 U-Boot。

已实现单页前端、无操作系统的 HTTP 服务、lwIP TCP 桥、更新包校验、固定目标 NAND 擦写引擎、单任务状态机和离线工具。厂商接口仍未完成实机适配：`adapter/vendor_unverified.c` 始终拒绝启动，不包含可用的 Flash 驱动地址。

**尚未完成原生常驻恢复。没有生成持久化补丁、修改设备分区、擦写设备或重启设备。** `.build/bootweb-unadapted.bin` 不能当作 `uboot.bin`、串口救援镜像或 Linux 内核使用。

## 已实现与仍缺少的部分

| 部分 | 当前状态 |
| --- | --- |
| 单文件 HTML | 小于 32 KiB，无框架、CDN、外部字体或外部资源 |
| 校验核心、任务状态机 | 离线测试及设备 Linux ARM64 只读测试通过；不含真实擦写 |
| 擦写引擎 | 仅在电脑内存 NAND 模型上验证 |
| 原生 HTTP 服务 | 已用同一份 C 代码测试上传、确认、断连与写入期间的状态查询 |
| TCP 网络栈 | 实际 lwIP 代码通过内存以太网测试；没有测试设备 DMA/网口 |
| 厂商薄适配层 | 尚未接通；重定位、缓存、原始收帧、ECC/BBT、看门狗都待验证 |
| RAM 入口与 Reset 入口 | 四参数命令入口与连续按住 3 秒逻辑已实现；地址、按键 GPIO 和电平未实测 |
| U-Boot 更新包生成 | 工具已实现；不等于生成了含 bootweb 的可启动 U-Boot |
| 常驻补丁 / 241 MiB 根分区 | 未实施，必须先通过 RAM 实机验证 |
| 4 MiB Linux 后备 | 仅实现硬体积检查；现有样本超限，没有划恢复区 |

实现按“未知即拒绝”处理。配置文件、上传内容、测试通过结果均不能解锁厂商适配层。

## 在当前 Windows 工作区构建

依赖：Python 3.13、Zig 0.14.1、lwIP 2.2.0。构建脚本不下载安装软件，不连接设备。Capstone 和 IDA 仅用于可选的静态逆向，不是编译恢复程序的依赖。

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
& 'C:\Python313\python.exe' 'C:\UGit\openwrt\SK-D840N\ci-build-repo\bootweb\tools\build.py' --test-core --test-net --test-service --ubsan
if ($LASTEXITCODE -ne 0) { throw '构建失败' }
& 'C:\Python313\python.exe' -m unittest discover -s 'C:\UGit\openwrt\SK-D840N\ci-build-repo\bootweb\tests' -v
```

`verify.py` 将跳过测试视为失败，退出码 0 只表示离线检查通过。报告中的所有硬件验收与可刷标志仍为 `false`。

## 本机页面预览

```powershell
& 'C:\Python313\python.exe' 'C:\UGit\openwrt\SK-D840N\ci-build-repo\bootweb\tools\serve_demo.py' --port 8088
```

打开 `http://127.0.0.1:8088`。默认只能上传和校验，确认写入锁定。此服务器只监听本机，未提供设备访问后端，按 Ctrl+C 退出。`--simulate-writes --base-boot <完整已知基底备份>` 也只修改电脑里的稀疏 NAND 模型，不写备份文件或设备。

设备端目标仍是 `192.168.9.1:80`，电脑静态地址 `192.168.9.2/24`。设备 2.5G 口尚未完成 bootweb 实测，不能用本机预览代替这一验收。

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

三种布局均有离线校验策略，但设备当前只保留 `current240`。`native241` 与 `recovery237` 的写入能力分别有独立门槛，不能通过网页或 `--layout` 开关改变真实设备布局。

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

bootweb 自有代码按 GPL-2.0-only 提供；lwIP 继续遵循其 BSD-3-Clause 许可，见 `LICENSES/lwip.txt`。厂商 dump、IDA 数据库、构建产物、设备配置及原厂参数不纳入源码交付。未修改工作区里原有 DTS、升级脚本、驱动和 CI 的用户改动。
