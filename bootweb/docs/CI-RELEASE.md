# native241 Bootweb CI 候选包

`sk-d840n-bootweb-native241-candidate.tar` 只适用于公开的 `U-Boot 2021.01-svn308776` 原始基底。它不是完整 boot 分区备份，也不是 LuCI 系统升级文件。

当前 stage2 必须具有以下 SHA256，否则包校验会拒绝：

```text
5b084490813b540c1a82ddf2e2d4de46080e6cfe47b15b2e6575bdf3fe198fe9
```

已安装其他常驻 Bootweb 的设备不能直接套用。候选包固定为 `CONTROL`、`uboot.bin`、`sha256sums` 三个成员；`uboot.bin` 是 1,441,792 字节的 stage2，仅对应物理 `[0x20000,0x180000)`。不要把它当成 2 MiB boot 镜像写入。

## 校验和安装边界

先核对下载文件的 `sha256sums-bootweb`。配套工具包带有 `toolkit-sha256sums`，可核对解包后的每个文件。Actions artifact 下载有 ZIP 外壳；Release Assets 可直接下载候选 tar 和配套 tgz。

CI 使用公开基底、固定依赖及静态 Reset 配置，完成离线测试、指令审计和同一份生产 C 校验器检查。CI 不接触设备，不读取私人备份，不生成安装器或安装授权；清单中的 `flashable_release`、`hardware_acceptance_complete`、`installation_authorized` 均为 `false`。静态 Reset 配置不是实体按键验收报告。

安装必须另走对应的实机流程：持续供电、新的完整物理 ECC 备份、当前镜像身份核对、精确候选授权和读回检查。既有安装工具的要求不变。本包不能上传到 LuCI；当前网页的 U-Boot 自刷入口仍锁定。

native241 的系统恢复目标为 241 MiB root，无 scratch/recovery 分区；系统按 root → dtb → kernel 写入。每次升级前都要暂停定时断电。CI 不证明本次构建已通过冷启动、BTL 救援、周期断电刷写预算或 2.5G 性能验收。

## 配套文件

工具包包含原生载荷、常驻 bootstrap、stage0 的 ELF/BIN，固定目标布局的 tail，构建清单、离线报告、指令审计、源码指纹和依赖来源。`source/bootweb/` 为该次构建的项目源码，`source/lwip/` 仅包含选用的 BSD 网络栈源码和头文件。完整 boot 镜像、NAND 备份、设备配置及原始串口记录均不在工具包中。

Bootweb 自有代码按 GPL-2.0-only 提供，lwIP 按 BSD-3-Clause 提供。Jitterentropy 的许可见其源码头和随附 README。公开参考镜像来自 `huxiangjs/SK-D840N-OpenWRT`；具体提交和完整哈希见 `source-provenance.json`。这些许可和来源记录不代表项目拥有完整厂商 U-Boot 源码。
