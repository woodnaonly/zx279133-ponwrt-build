# 固定布局、更新格式与安全边界

本文件描述已实现的离线策略，不授予实机刷写许可。地址区间均为左闭右开的物理 NAND 字节偏移。Flash 为 256 MiB，擦除块 128 KiB，页 2048 字节；实际驱动必须再次确认几何参数。

## 当前布局与保护

| 区域 | 偏移区间 | 大小 | bootweb 策略 |
| --- | --- | --- | --- |
| 前级 BTL | `0x00000000–0x00020000` | 128 KiB | 永远不写 |
| U-Boot 主程序窗口 | `0x00020000–0x00180000` | 1408 KiB | 仅 U-Boot 包；另需 BTL 与串口救援门槛 |
| 环境变量 | `0x00180000–0x001A0000` | 128 KiB | 永远不写 |
| boot 尾部 | `0x001A0000–0x00200000` | 384 KiB | 永远不写，包括空白字节 |
| kernel / mtd2 | `0x00200000–0x00A00000` | 8 MiB | 系统包最后写 |
| dtb / mtd3 | `0x00A00000–0x00B00000` | 1 MiB | 系统包第二个写 |
| parameter tags / mtd4 | `0x00B00000–0x00F00000` | 4 MiB | 整区永远不写 |
| root / mtd5 | `0x00F00000–0x0FF00000` | 240 MiB | 系统包首先写 |
| scratch / mtd6 | `0x0FF00000–0x10000000` | 1 MiB | 当前保留，不单独写 |

原生实测成功后才允许移除 scratch 并将 root 扩至 241 MiB；此时 root 终点为 `0x10000000`。后备路线达标后才允许 root 改为 237 MiB，并将 `[0x0FC00000,0x10000000)` 作为 4 MiB recovery。两种未来布局相互排斥，均必须保持 root 的 `mtd5` 别名与 `/dev/mtdblock5`。

后备完整镜像（包括 FIT 封装与 DTB）必须不超过 `0x3C0000`，即 3.75 MiB，另留两个擦除块。`check_recovery_size.py` 只检查体积，不验证它是否真能启动，更不会划分分区。

## 系统更新包

使用现有 `immortalwrt-sk-d840n-sysupgrade.bin` 格式，接受普通 USTAR/GNU regular tar 成员，不接受压缩 tar、PAX、扩展名称、链接和特殊文件。

目录前缀固定为 `sysupgrade-skyworth_sk-d840n/`，包含 `CONTROL`、`kernel`、`dtb`、`root` 四个非空文件；允许一个对应目录成员。`CONTROL` 内容固定为 `BOARD=skyworth_sk-d840n` 加 LF。未知成员、重复成员、路径穿越、非零填充、拼接 archive、截断和无完整终结块均拒绝。

检查 fwtool 元数据及其 CRC；要求板型、target、兼容版本匹配。可识别 fwtool signature chunk 的 CRC，但**没有签名真实性验证**，SHA256/CRC 不等于来源可信。只应使用用户信任的镜像。

kernel 必须是 ARM64/Linux/gzip uImage，load/entry 为 `0x80200000`，长度不超过 8 MiB，校验头 CRC、数据 CRC、声明长度及 gzip 头。当前没有实现 gzip 解压后的内核语义验证。

DTB 检查根板型、唯一 fixed-partitions 表、全部分区偏移和长度、boot/tags 只读属性、mtd 别名、root 编号与当前选择的布局。分区表及祖先节点不能禁用；`/chosen/bootargs` 必须保留 JFFS2 `/dev/mtdblock5`，拒绝重复 root 和 `mtdparts=`/`ubi.mtd=` 覆盖。root 检查 JFFS2 首节点头与 Linux CRC，不声称进行了完整文件系统一致性检查。

所有成员 SHA256 和整包 SHA256 在 RAM 中计算。只能按 `root → dtb → kernel` 固定顺序写入，浏览器和更新包不能指定偏移。

## U-Boot 候选包

固定为普通 tar，恰好包含：

```text
CONTROL
uboot.bin
sha256sums
```

`CONTROL` 恰好五个 LF 结尾的字段，每个字段只能出现一次：

```text
FORMAT=sk-d840n-uboot-v1
BOARD=skyworth_sk-d840n
BASE_SHA256=<当前适配基底 stage2 的 64 位小写十六进制 SHA256>
PAYLOAD_SHA256=<uboot.bin 的 SHA256>
PAYLOAD_BYTES=1441792
```

不允许 `OFFSET`、目标分区、脚本或任何附加字段。`uboot.bin` 必须恰好 `0x160000` 字节，只代表 `[0x20000,0x180000)`；2 MiB 的完整 boot.bin 会被拒绝。`sha256sums` 使用 `hash`、两个空格、成员名和 LF，恰好列出 CONTROL 与 uboot.bin。

包内基底必须匹配固化在适配构建中的基底；擦除前还会通过固定 ECC 读取实际已安装 stage2 并核对。原厂 `svn358069` 不会因为提供了 profile 就变成可写基底。安装新的 stage2 后，要重新审查对应适配基底，不能让上传包自行声明新版本可信。

除普通格式校验外，U-Boot 擦写必须通过目标平台 `verify_boot`，检查实际 BTL 装载长度、容器条件和可执行结构。当前尚无真实回调，所以不能刷 U-Boot。

## 任务与 NAND 行为

1. 一次只接收一个完整包；上传不完整或校验失败不擦除。
2. 明确确认绑定任务编号、启动期随机 token 和整包 SHA256；执行前再次检查缓冲区没有变化。
3. 擦除前校验全部目标好块容量、实际基底与保护区哈希。后一分区容量不足也必须保持零擦除。
4. 只通过已验证的厂商 ECC/坏块接口访问，不使用 raw/OOB 写入或 `run_command("nand ...")`。错误与短读都必须按失败处理。
5. 按物理分区上限跳坏块，逐页写入，并按相同好块顺序读回 SHA256。当前遇到擦写失败即停止，不自动重试或跨界寻找空间。
6. 写入成功或擦写过程中失败，都尝试再次核对 BTL、env、boot 尾部和 tags。失败不得显示成功；保护区读取失败也不视为保护已通过。
7. 确认请求重复提交不能启动第二次写入；确认响应断连仍继续原任务。刷新页面只查已有任务。写入期间仅服务有界的只读 HTTP 请求，拒绝重启、取消或替换任务。

掉电不具备事务原子性。系统包中途失败可能留下混合版本，U-Boot 更新中途掉电可能无法正常启动。写后读回成功只是本次写入完成，必须再经实际重启验证。

## HTTP 边界

只允许 `GET /`、`GET /api/state` 以及带 token 的 `POST /api/upload`、`/api/confirm`、`/api/cancel`、`/api/reboot`。无 shell、任意内存写、Flash 偏移或文件路径接口。

单头部上限 4096 字节，最多 32 个头；拒绝 chunked、Expect、重复 Content-Length、异常换行、折叠头和 GET body。设备端 Host 必须为 `192.168.9.1[:80]`，Origin 如存在也必须匹配；不启用 CORS，返回 no-store 与限制性 CSP。

token 必须来自设备熵源，不可用公开 MAC 或启动时间代替。当前设备熵源也尚未适配。网络端为无 TLS 的隔离直连恢复网段，不面向 WAN。
