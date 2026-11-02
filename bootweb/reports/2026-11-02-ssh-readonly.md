# 2026-11-02 SSH 只读测试记录

设备 Linux ARM64 用户态的 51 项检查通过，37 项离线测试通过，无失败或跳过。未执行 Flash 擦写、重启、串口操作或分区变更。原生 U-Boot 适配和常驻恢复仍未完成，没有可刷镜像。

机器记录：

- `device-ssh-2026-11-02.json`：最终设备测试，`pass`、`cleanup_complete` 均为 true。
- `offline-ssh-2026-11-02.json`：本次源码的重新构建与离线测试。
- `device-ssh-2026-11-02-initial-failure.json`：保留早期断连用例失败的记录，不能作为通过证明。

## 环境与测试边界

设备为 `192.168.9.1`，板型 `skyworth,sk-d840n`，运行 ImmortalWrt 25.12.1、Linux `6.18.38+`。当前布局仍为 240 MiB root 和 1 MiB scratch。SSH 使用 root 空密码并固定已核对的主机密钥。

`tests/device/ssh_harness.c` 链接生产 C 核心与 `src/service.c`，不使用合成基底宏。所有 MTD 节点通过 `O_RDONLY | O_NOFOLLOW` 打开，并核对板型、分区几何、字符设备号和基底哈希。擦写回调固定返回失败，capabilities 为 0，reboot 回调为空。没有接入厂商 U-Boot API。

程序位于独立 tmpfs 目录，仅监听设备回环地址 `127.0.0.1:18088`，通过 SSH 隧道访问。服务寿命上限为 180 秒，支持 SIGTERM 退出；清理时先核对 PID 的确切执行路径，只删除本次创建的三个文件及其空目录。

构建使用 Zig 0.14.1，目标为静态 `aarch64-linux-musl`，启用 `-Wall -Wextra -Werror`。测试程序为 1,323,064 字节，SHA256：

```text
0754ceca1790d487f0ee03856caab36f1c8c6b58e151d5198879e29e4937ec9b
```

这个体积不含恢复内核或设备树，不用于判断 4 MiB Linux 后备路线是否达标。

## 通过的设备检查

上传现有旧样本 `staging\trial\run85\flash-sysupgrade.bin`，12,431,637 字节，整包 SHA256：

```text
73beaef1a972dc979bb0ad2b5a4b634af37e6861b1d2bfa047442a175fd5901d
```

设备完整接收和校验耗时约 2.956 秒；结果中的 root、dtb、kernel 长度、容量和哈希与电脑上的生产校验库一致，写入计划顺序为 root → dtb → kernel。这里只验证旧包格式，不建议刷入这份样本，也没有写入其中任何镜像。

检查还覆盖：

- 页面与源码字节一致，6,921 字节；错误 Host、Origin 和 token 被拒绝。
- 连续 10 次在服务端收到部分请求体后断开，状态均变为 failed，不能确认，显式取消后才能继续。
- 完整上传后，确认写入、重复确认和重启请求均被拒绝。
- 错误板型、重复 tar 成员、路径穿越、错误 fwtool CRC、错误 U-Boot 基底和整段 boot 载荷均被拒绝。
- 程序正常退出，擦写回调调用次数为 0；boot、env、tags 等保护区哈希前后一致；临时文件与进程已清理。

## 早期失败与断连用例修正

最初在本机发送部分请求后立即半关闭 SSH 转发连接，偶尔看到服务状态仍为 idle。诊断运行的服务端日志显示该连接 `received_bytes` 为 0：请求尚未交付，不能据此判断“已开始的上传被截断”这一行为。

测试程序现在把首次收到请求体的事件写入私有日志；控制脚本确认事件后才断开连接，不依靠固定延时。最终运行中这一步和后续 failed 状态检查连续通过 10 次。没有为通过测试修改 HTTP 核心或放松断言。

早期清理脚本还使用了设备不支持的 `sleep 0.1`，已改用整数秒等待。后续已核对旧临时目录及测试进程均无遗留，最终记录的 `cleanup_complete` 为 true。

## 保护区复核

下列每个区域的测试前后 SHA256 相同：

| 区域 | SHA256 |
| --- | --- |
| BTL `[0,0x20000)` | `d4d7a4bf90fc26cc61c76000bb272fac3113c14525ab7119c3d10fbd477231f4` |
| stage2 `[0x20000,0x180000)` | `5b084490813b540c1a82ddf2e2d4de46080e6cfe47b15b2e6575bdf3fe198fe9` |
| env `[0x180000,0x1A0000)` | `d6d9522e25c4b984d571d34ba7657674fa371dff06fe82e2a3d34a877168e044` |
| boot 尾部 `[0x1A0000,0x200000)` | `c97fce784428a2e10cd6d4ef97ccc756f2e4c1b146a9b9dc5415d4e3e245b70a` |
| parameter tags | `9f739d37021667828f88ff2438b82b2d4b5806a8f3d44afb628cbd72e2007c59` |

另用设备自带 `sha256sum` 独立核对完整 `/dev/mtd1` 和 `/dev/mtd4`，前后相同。root 的 corrected_bits 计数保持 48；未扫描整个 root，也未修改或清除已有 JFFS2 告警。

## 离线复核与未完成项

重新构建并执行 37 项离线测试，0 失败、0 错误、0 跳过。新增用例保证 SSH 测试必须显式启用，以及断连同步只接受完整的服务端收包事件。源码输入集合 SHA256：

```text
f6dd35195620e6fba618f7dc122a30235cbb3d84aba1ecdd25eba7752595528e
```

原生 freestanding 开发 bin 仍为 98,128 字节，SHA256 与原离线记录相同。所有硬件验收和可刷标记仍为 false。Linux 网络、随机数和 MTD 只读测试不能替代 U-Boot 中的驱动、缓存、看门狗、ECC/坏块、Reset、前级串口救援或完整 RAM 备份验证；实际更新与重启测试尚未进行。
