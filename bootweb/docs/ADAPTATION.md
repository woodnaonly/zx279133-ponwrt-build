# 版本适配与静态逆向记录

## 版本身份

当前适配对象是社区版 `svn308776`，原厂 `svn358069` 仅用于对照。旧脚本注释把 `backups/mtd1_boot.bin` 叫作社区版，这是错误的；应以二进制哈希为准。

| 项目 | 社区版 | 原厂对照 |
| --- | --- | --- |
| banner | U-Boot 2021.01-svn308776，2025-04-17 16:09:35 +0800 | U-Boot 2021.01-svn358069 |
| 完整 2 MiB boot SHA256 | `0f8efd0c8cb1083e813bcf6a3c6a138670fad5f3d6ec79330b410c15afe1f941` | `009929cd17142567a1537e773758809bb75aa79a51af6597f2140c82e318f808` |
| `[0x20000,0x180000)` SHA256 | `5b084490813b540c1a82ddf2e2d4de46080e6cfe47b15b2e6575bdf3fe198fe9` | `250f8537cfd436a67196a263f4a39aeac36d894c27e5cf8d611c2ff856defd98` |
| 原生运行 / 写入 | RAM 只读入口及 HTTP 已实测，完整验收见试验报告 / 锁定 | 不支持 / 锁定 |

两版前 128 KiB 相同，SHA256 为 `d4d7a4bf90fc26cc61c76000bb272fac3113c14525ab7119c3d10fbd477231f4`。boot 尾部 `[0x1A0000,0x200000)` 同为 `c97fce784428a2e10cd6d4ef97ccc756f2e4c1b146a9b9dc5415d4e3e245b70a`。env 不是跨设备可复制的固件代码，不能用另一份 dump 的 env 覆盖当前配置。

`audit_boot.py` 读取完整 2 MiB dump，输出区域哈希、命令表候选及每个命令实现前 64 字节的哈希。它从不执行输入，不把 profile 的布尔字段当作授权。

## 地址与命令 ABI

stage2 从文件偏移 `0x20000` 开始，链接地址为 `0x88000000`。本版静态换算为 `link = file_offset + 0x87FE0000`。**链接地址不是 U-Boot 运行时地址，不能直接强转成函数指针调用。**

实测扫描到两版各 62 条命令。社区版表范围 `[0xB62E8,0xB7078)`，每条 56 字节，对应 ARM64 `cmd_rep` 形式：

```text
name[8], maxargs[4], padding[4], cmd_rep[8], cmd[8], usage[8], help[8], complete[8]
```

文件 `0x32C98` 的分发路径从记录 `+0x10` 调用 repeat wrapper；wrapper 从 `+0x18` 取实际实现。它不是旧版整数 `repeatable` 结构。恢复程序入口 `bootweb_command(cmd, flags, argc, argv)` 已采用四参数命令 ABI。

当前命令表没有 `go` 或 `bootelf`。本机 RAM 只读入口通过临时替换 `?` 记录的 `+0x18` 字段实测，`help` 保留，返回后读回核对恢复。不能给出一条实际不存在的 `go 0x89000000` 命令。`0x89000000` 仅适用于本轮经 `bdinfo`、LMB、运行时代码和页表核对的固定布局，不是通用安全地址。

反编译的 `bdinfo` 路径显示 `gd` 在 `x18`，字段为 relocaddr `gd+112`、reloc_off `gd+152`、fdt_blob `gd+200`。本轮实测 gd/x18 为 `0x9f6fbde0`，重定位地址 `0x9ff3c000`、偏移 `0x17f3c000`。最小四参数探针已返回 U-Boot 并恢复入口与缓存；完整服务另用 64 KiB 私有栈，机器码审计禁止改写 x18 及 FP/SIMD 指令。

## 社区版函数候选

以下仅是 IDA/Capstone 与 U-Boot 2021.01 通用代码的静态比对结果。没有一项因此获得实机写入权限；不得用于原厂版或其他编译版本。

| 链接地址 | 候选用途 | 尚需确认 |
| --- | --- | --- |
| `0x88008218` | netboot_common | 启动、清理和返回约定 |
| `0x88068FBC` | net_loop | 协议状态和看门狗交互 |
| `0x88068318` | net_init | 网络全局变量与内存布局 |
| `0x88067E00` / `0x88067E84` | eth_init / eth_halt | 2.5G 物理口与状态切换 |
| `0x88067EE4` | eth_rx | 收帧最终进入旧 IP 栈，不能直接作为 lwIP 的 raw RX |
| `0x88068140` | eth_set_current | 当前 legacy 设备选择 |
| `0x88065630` / `0x880656DC` | get_timer / udelay | 单位、溢出与运行异常级别 |
| `0x8806677C` / `0x88008D88` | printf / env_get | 重定位、变参及生命周期 |
| `0x8801CE94` / `0x8801CC68` | nand_read_skip_bad / nand_write_skip_bad | 参数、长度返回、边界与 ECC |
| `0x880192C0` | mtd_block_isbad | mtd_info 来源、BBT 和错误返回 |
| `0x8801C7FC` | nand_erase_opts | opts 结构与擦除精度，不猜结构 |
| `0x88018EB4` / `0x88018F5C` | mtd_read_oob / mtd_write_oob | 仅供核对调用图，禁止拿 raw/OOB 接口绕开 ECC |

这是 **legacy eth_device**，不是 eth-uclass：静态全局 `eth_current` 候选为 `0x880B1938`，state 偏移 40，init/send/recv/halt 指针分别为 48/56/64/72。需要在 RAM 中核对实际结构、设备名称、MAC、收帧截头与缓冲区复用。驱动 recv 进入旧 U-Boot IP 栈的路径必须与 lwIP 隔离，任何临时 RAM hook 都必须保留原指令并处理指令/数据缓存。

真实 NAND 适配必须保证 ECC 全长度读、纠错状态、坏块查询、页对齐和擦除块边界。`-EUCLEAN` 只有在驱动确实完成全部请求数据且确认其语义时才能归一为成功；短读、不可纠正 ECC 和负的坏块查询都必须失败。不能将命令表里的 `nand`/`mtd` shell handler 当作低层 NAND API。

看门狗、设备熵源、重启和 Reset GPIO 尚未完成适配；不从 Linux GPIO 编号推断前级寄存器。

## 本机 RAM 只读适配

`vendor_ram_readonly.c` 仅由独立只读目标链接，普通 `vendor_unverified.c` 仍硬停。每次 TTL 装载前后核对当前实例指纹；实际使用 `reloc+0x18d34` 的固定物理偏移 ECC 读取和 `reloc+0x192c0` 坏块查询，不使用 skip-bad、raw/OOB 或 NAND shell handler。只读范围限定为验收所需分区，擦除、写页、保存环境和重启函数均不绑定。

原始网帧使用已核对的 legacy eth_device 发送回调和驱动 RX 回调槽，协议栈为 lwIP，不借用 Linux 网络。退出时 halt、恢复 RX 回调、设备状态、已保存的 NP 驱动上下文及两个 PHY 模式缓存，并读回检查。PHY 缓存恢复后同周期网络重入仍失败；只能把每次新的自然上电作为独立验证起点，不能承诺可重复初始化。

看门狗只读检查厂商已关闭状态，会话令牌来自电脑安全随机数。具体 RAM 区间、构建哈希、HTTP、ECC 和两周期状态见 `../reports/2026-11-03-ttl-ram-readonly.md`；这些证据不开放任何写入能力。

## 前级 BTL 的两种路径

共享前级的静态区域指纹另存于 `profiles/btl-shared-static.json`。文件偏移/静态地址 `0x1858` 通过硬件启动选择进入 NAND、NOR 或串口路径；NAND 启动候选 `0x242C` 向装载路径传入 Flash `0x20000` 和长度 `0x180000`。

`0x4BB0` 的安全选择候选会等待硬件状态后读取低位。非安全分支使用 `0x88000000` 并直接执行；安全分支先放到 `0x88800000`，随后经 `0x4C90` 的板型与安全容器校验再执行。`0x47AC` 的容器校验涉及 2048 字节头、多个 HASH/RSA/AES 硬件步骤与可选加密。

因此不能一概说 raw stage2 一定不能串口启动，也不能认定当前机器一定走可直接执行的分支。BTL 的串口控制台静态上存在 XMODEM/执行逻辑，**不是已完成的已知良好串口救援链**。没有实测前，不提供据此生成的救援容器或自动 RAM 写入脚本。

候选 NAND 装载长度会覆盖到文件 `0x1A0000`，即包含 env；这不改变 bootweb 允许写入的上限 `0x180000`。仍需核对实际 NAND 启动、坏块映射与完整载荷边界，不能因某段是 FF 就直接塞入持久化代码。

## 复现 IDA 分析

`idat.exe` 必须强制 `-TBinary file -pARM`；默认自动识别曾把 raw boot.bin 误当 COFF。脚本按 64 位映射代码及可写数据；将整个混合区域设为只读 CODE 会导致 Hex-Rays 把可写全局错误折叠成常量。

使用新数据库，数据库和输出放在被忽略的 `.build`，不要写入 dump 目录：

```powershell
& 'C:\Dev\Tools\IDA Professional 9.2\idat.exe' -A -c -pARM '-TBinary file' '-LC:\UGit\openwrt\SK-D840N\ci-build-repo\bootweb\.build\ida-local.log' '-oC:\UGit\openwrt\SK-D840N\ci-build-repo\bootweb\.build\community-local.i64' '-SC:\UGit\openwrt\SK-D840N\ci-build-repo\bootweb\tools\ida_audit.py C:\UGit\openwrt\SK-D840N\ci-build-repo\bootweb\.build\ida-local.json' 'C:\UGit\openwrt\SK-D840N\sk-d840n-openwrt\binary\boot.bin'
```

分析前级时在脚本参数最后加 `stage1`，并另选数据库与输出名。报告包含伪代码、静态调用点、函数范围及区域哈希，仍全部标记为未运行验证。

RAM 只读目标的实测不解除普通适配器的硬停。完整备份、前级救援、可写适配、Reset 入口及升级读回/重启仍需单独验收；不得通过猜地址或设置 capability 位跳过。
