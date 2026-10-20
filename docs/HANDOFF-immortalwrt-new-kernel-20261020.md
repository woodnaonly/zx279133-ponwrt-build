# 交接：目标是把这块板子刷成「带新内核的 ImmortalWrt」

写于 2026-10-20 22:57（本地时间）。设备：Skyworth/ZTE **SK-D840N**（SoC **ZX279133**，aarch64，GPON ONU）。
读者：**接手执行的新模型/新会话**。本文件是权威起点，不要凭对话记忆行动。

---

## 0. 先说最要紧的三条（不看就会浪费几小时）

1. **「新内核」在这块板子上不是换 kernel 就完事，而是两堵墙**：
   - **9132 UNI 交换机没有 mainline 驱动** → mainline 下 `/sys/class/net` 只有 `eth0 lo`，
     **四个 LAN 口里三个全死**（`br-lan` 是 netifd 无条件建的**空桥**，不是真桥）。这条已被两次实测确认。
   - **mainline 的 eth0 数据通路不通**（`rx=0`），NP/IDM 那一族假设（CLA 规则、ring 基址搬进 NP 窗口、
     中断路由字…）**都已逐个否证**。详见 `docs/08`–`docs/11`、`docs/13`。
   ⇒ 4.19 能用，是因为厂商 BSP kmods 全装载并工作（`np_133`/`netdriver`/`switch`/`ffe`/`qos_mod`/
   `wan_bridge`/`rlt8226b`/`iprt`…，`lsmod` 实测 39 个）。**任何"新内核"方案都必须回答这两条怎么解决。**
2. **不要用两个内核混写同一个 jffs2 根**。mainline 写过的目录项会让 4.19 解析名字失败
   （`open: Not a directory` 每轮开机 ~800 行，进而 `/etc/preinit` 起不来）。修法唯一有效的是
   **重建 inode**（`rm`+`mkdir`，内容经 RAM 暂存），rename 没用 —— 全过程与数据见 `docs/12`。
   反过来（先 4.19 写、新内核读）同样有风险。**换内核 = 连 rootfs 一起当作一套来出。**
3. **不能从正在运行的系统里刷 `root`**（`root` 就是 `/`）。必须先进 RAM 环境（kexec/initramfs，
   或 zrec 网页恢复服务），再 `mtd erase root; mtd write <img> root; mtd verify`。
   `/sbin/mtd` 是本机唯一刷写原语（**没有** mtd_debug/nandwrite/mtd-utils），参数接受 `mtdX` 或**带空格的分区名**
   （`"parameter tags"`）；`/tagparam` 要从 `/dev/mtdblock4` 挂。

---

## 1. 板子现在的真实状态（全部实测，非记忆）

| 项 | 值 |
|---|---|
| flash 三元组 | 社区 `boot.bin`（U-Boot 2021.01-svn308776，**未动过**）+ 社区 **4.19.136+** `uImage`（md5 `a265fe5b…`，pad 后 `f68ceb0f…`）+ `board.dtb`（48,463 B，pad `881af2d3…`）+ OpenWrt **24.10.8** jffs2 `root` |
| 可用性 | `tools/verify_router.sh` **5/5 PASS / ROUTER IS USABLE**，且在 22:14、22:43 两次**纯冷启动**后复现 |
| 网络 | `lan` = `br-lan`，**当前只含 eth1**，静态 `192.168.9.1/24`；`wan` = **eth2** + `proto dhcp`（无租约、无假默认路由）；`eth3` 也已从桥里摘出；PC = `192.168.9.25` |
| 访问 | `ssh root@192.168.9.1`（公钥 `~/.ssh/id_onu`，**root 无密码**）；LuCI http://192.168.9.1（Argon 主题 + 简体中文） |
| 分区 | `mtd1 boot @0 +2M` · `mtd2 kernel @0x200000 +8M` · `mtd3 dtb @0xa00000 +1M` · `mtd4 "parameter tags" @0xb00000 +4M` · `mtd5 root @0xf00000 +241M`；**`mtd0 whole` 只有 128 MiB**（社区 dtb 限死 `0x08000000`，别写超过 128M） |
| 备份 | `backups/root-20261020-2045.bin.gz`（mtd5 全量，md5 双向一致）、`backups/config-20261020-2100.tgz`（sysupgrade -b）、`backups/network-pre-wan-20261020-215059.txt`（改 WAN 前原文）、`backups/network-backup-*.txt` |
| 整机回退 | `restore_set/{boot,kernel,dtb,param,root}_stock.bin` 五件套齐全（原厂固件，含光猫/注册） |
| 本轮写入 | **零** flash 写入；只有 uci 配置（可回退）与运行时状态 |

**未证/未知**：光纤有没有插、能否注册（`pon` rx 恒 0）；eth2 对应面板哪只 RJ45（`tools/jackmap.sh` 正在记）；
9132 是否把 eth2/eth3 划在别的硬件 VLAN（隔离只证到"不泄进 LAN 泛洪域"）。

**23:08–23:16 复核（写这份文档之后又跑了一次）**：`192.168.9.1` 断电前仍 4/4 通（avg 2 ms）；
23:14:51 起连续 `NO-REPLY` → 又一次断电，23:16:05 恢复且**计数已归零**（eth1 rx 从 40 万掉回 1.6 万）
⇒ 计数器每次断电重启，**做增量必须限定在同一次开机内**。
断电前 `jackmap.log` 连续样本里 **eth2 tx 每 22 s 涨约 2.4 kB（udhcpc 在重试），rx 恒 0**，eth0 rx/tx 全冻结，
eth1 是唯一有来有回的口。断电后直接实测过：eth2 `rx_bytes=0`、`tx_bytes` 5692→7060（22 包）——
**上行线仍未插入任何一只口**。这一条不解决，路线 D 的"实测出网"永远读不到结果。

---

## 2. 物理与时间约束（只有机主能配合，别绕）

- 板子插在**智能插座**上，**每 ~15.5–16 分钟断电 30 秒**。实测落点：`21:56 / 22:12 / 22:28 / 22:43 / 23:14`
  （`jackmap.log` 从 23:14:51 起连续 `NO-REPLY`，ssh 同时 `Connection timed out`），
  **不是整刻钟**，别按 :15/:30/:45 计划。进 U-Boot 只能靠这个断电窗口。
  锚点会随插座自己的周期漂移，**每轮开始前用 `jackmap.log` 的 `NO-REPLY` 重新对一次表**。
- **机主是唯一的物理传感器**。要人挪线/看灯/按 RESET，就明确地提出来；不可逆步骤（写 flash、断电时机）
  默认**先要批准**，但机主确实会批。
- 子代理边界：**不许碰 ONU/串口，不许读 `C:\UGit\SSH`**；而且子代理会**编造板子的实时状态**，
  任何"设备现在如何"的说法必须自己重读。

---

## 3. 串口/网络仪器（直接用，别重造）

- 串口链路：`tools/serrelay.ps1`（COM3 115200）→ 日志 `staging/ctl/serial.log`，命令经
  `staging/ctl/sercmd.txt`（**原子 rename** 投递）；`tools/relayloop.sh 6` 保活；`tools/armu4.sh N` 打满 1 s
  bootdelay（认 `staging/ctl/u.stop`）；`tools/tftpsrv.py staging/trial 69`。
- 已知坑（都踩过）：串口回显会把输入原样打回；**持续 `u` 流穿过 Linux 启动会强制 procd FAILSAFE**；
  被杀的 round 会把 streamer 变孤儿；`tail -c +N` 看不到"已经是最后一行"的 prompt（先 `send 'version'` 探一下）；
  U-Boot 里只有 `md`，没有 `echo`/rdreg。
- PC 侧判据：`ping -S 192.168.9.25 <target>`（**必须绑源**；Windows 是 `-w` 毫秒、`-n` 次数，写成 `-W 1500 4` 会报"
  值超出范围"而一个包不发）；Windows `arp -a` 输出是 GBK、MAC 全横线、回复行是大写 `TTL=`（大小写/分隔符踩过两次）；
  `tools/pcrxwatch.ps1 -Seconds N -Every 5` 看 RX 增量。
- `tools/jackmap.sh` 的 `rx=`/`tx=` **都是字节**，不是包：`/proc/net/dev` 行首有空格 → `cut -d" " -f1` 得到空串，
  于是 `f3=rx_bytes`、`f11=tx_bytes`（不是 rx_packets）。**注意别用 `awk` 复核**——`awk` 默认会跳过行首空白，
  它的 `$11` 是 `tx_packets`，跟 `cut -f11` 差一位（23:16 就在这儿差点把自己骗过去，改回 `cut` 才对齐）。
  所以"tx 一直涨、rx 恒 0"只说明**本端在发、对面没回**，不能读成"链路有流量"。
- 板子侧 applet 现状（**很重要，容易做出假阴性**）：busybox **没有** `pkill`/`arp`/`timeout`/`od`/`stat`/`nohup`/
  `--list`；`killall` 有；`ping` **没有** `-b`/`-f`，`-i SECS` 只吃**整数**（`-i 0.1` 会一帧不发就报错）；
  `setsid` 有（要分离进程用它）。
- vendor 观测口：`/proc/pon/{debug,tm_debug,switch_debug}` **写有效、输出落 dmesg**；TM 窗口约 400 行、
  含多套**索引不同**的端口表 → **必须整窗口取、按 counter 标签配对，绝不能按行号**。
  `sw_port 0..4` 有效、**4 = RTL8226（eth0 的 PHY）**；`in_sop_cnt` 列名是
  `smac0 smac1 smac2 smac3 xmac0 xmac1 rgmii pon0(10G) pon1(1G)`；`/proc/tm/shell` 一律 `operation forbid`
  （ssh 和串口真 tty 都一样，且它 `$?` 恒 0 —— 成败只看 dmesg）。`/proc/gpon_sdk/debug` 读了会挂住。

---

## 4. 「新内核 ImmortalWrt」的三条现实路线（附判据）

> **别自己发明构建路**：`ci-build-repo/.github/workflows/build-sk-d840n.yml` 已经就是"ImmortalWrt + 新内核"
> 这条线的载体，头部注释写得很清楚：`base = cnjn/ImmortalWrt`（分支 `sr1010-zx279133`——`target/linux/zte/zx279133`
> 是**这条分支**带来的，不是上游 ImmortalWrt master；本机没有 openwrt/immortalwrt 的检出，所以这句只能算对 workflow
> env 的直读，别当成已核实过上游）、`kernel = cnjn/linux-mainline-zte-zxslc-sr1010`
> `6.18.38`（`KERNEL_SHA` 已 pin）、`format = huxiangjs/SK-D840N-OpenWRT 证明过的 flash 布局`。
> `mode=flash` 出的就是 `uImage + dtb.bin + rootfs.jffs2` 三件，正好对应 `mtd2/mtd3/mtd5`；
> `mode=initramfs` 出 RAM FIT，用来 kexec 试跑、不动 flash。
> **它构建的是下面路线 B 的那个东西**，而且已经在出 kit（75/76/77/78/79/80，逐 kit 的寄存器判据在 `docs/11`）。
> 所以 §4 的 A/B 差别不在"能不能构建"，而在**产物在板上载不载流**。

### 4.0 新内核的出处，以及"为什么不能简单移植"（机主 2026-10-20 23:05 明确）

**新内核 = ZTE SR1010 那一套 mainline 支持**，参考仓 `https://github.com/huxiangjs/SK-D840N-OpenWRT`
（该项目的目录里就有；内核侧对应 `cnjn/linux-mainline-zte-zxslc-sr1010`，分支 `codex/sr1010-mainline`）。
**SR1010 是路由器，当前板子 SK-D840N 是光猫（GPON ONU）** —— 同一颗 SoC（ZX279133），**板级不同**，
所以**不能简单移植，必须改内核适配**。这不是推测，架构与差异都已在 `docs/07` 落过：

| 事项 | SR1010（基线，路由器） | SK-D840N（本板，光猫） | 已存在的适配 |
|---|---|---|---|
| target 树 | `immortalwrt/target/linux/zte/` **只有 SR1010**；`FEATURES` **不含 jffs2**；image/Makefile 没有 `skyworth_sk-d840n` | root 是 **jffs2 的 241 MiB `mtd5`**，需要 `skyworth_sk-d840n` 设备定义 | `ci-build-repo/overlays/target/linux/zte/{Makefile,image/Makefile}`（构建时 `cp -a` 注入，**故意不改主树**，否则破坏 SR1010 可构建性） |
| 板级 dts | `zx279133-sr1010.dts` / `zx279133-sr1010-openwrt.dts`（本地已有：`tests/kernelsrc/arch_arm64_boot_dts_zte_zx279133-sr1010.dts`、`staging/src/zx279133-sr1010.dts`） | 分区表、LED、按键、MAC 来源（`/tagparam` 的 `0..7_mac_addr`，基址 `d0:45:8d:84:08:fc`）、`pon_rsvmem` 保留区都不同 | `kernel-overlays/arch/arm64/boot/dts/zx279133-sk-d840n.dts` + 那份 `zx279133.dtsi` |
| 以太网驱动族 | SR1010 的 LAN 走 NPPT 路径 | **eth0 = NPPT + 外挂 RTL8226B（MDIO 地址 5，`rtl8226b: ethernet-phy@5`，故意不给 `compatible`）**；四个 LAN 口挂在 SoC 内部 **9132 UNI 交换机**上，厂商用 `/dev/ethdriver` ioctl 驱动，**不用 phylib 也不用 DSA** | `kernel-overlays/drivers/net/ethernet/zte/zx279133-{main,mac,np,idm,netdev}.c`、`drivers/phy/zte/phy-zx279133-uni-serdes.c`、`drivers/mtd/nand/spi/unim.c` |
| PHY 芯片 | SR1010 上出过"把 `ZX279051` 驱动绑到 Realtek 芯片"的事故（PHY 节点的 `compatible` 会覆盖 ID 匹配） | 本板是 **RTL8226B**，CI 的门要 `CONFIG_REALTEK_PHY=y`，**不是** `ZX279051_PHY`（该符号在 Kconfig 里真实存在，所以这个错极难发现；`docs/07` 已把发布说明里的错字改掉） | `scripts/configure-sk-d840n.sh` 里 pin 符号 |
| PON 侧 | 无 | **GPON/XG-PON 全套（`zte_xgpon`/`gpondrv_xg`/OMCI）主线没有驱动**，光猫功能回不来 | 无（这是本板独缺的一大块） |

**还没解决、需要继续改内核的两条硬账（本轮实测）**：

1. **9132 UNI 交换机主线无驱动** ⇒ mainline 下 `/sys/class/net` 只有 `eth0 lo`，
   `br-lan` 是 netifd 建的空桥，**四个 LAN 口全不工作**。这条不解决，任何"新内核"产物都不是可用路由器。
   （`tag_zx279133_rtl8372n.ko` 永不 probe；RTL8372N DSA 节点与 `lan-conduit` 子节点是被**故意去掉**的。）
2. **eth0 的 NP/TM 出口端口映射不对**：CPU 发的帧全部落在 TM 列 `smac1`，而 eth0 的 PHY 在厂商 CLI 里是
   **`sw_port 4`**（`enable:1`），`in_sop_cnt` 的 `xmac0/xmac1/rgmii/pon0/pon1` 列在所有窗口恒 0（`docs/13` §0/§8/§16）。
   即主线驱动"发了包"，但没有把包交给它自己的 MAC。`upModeInfo`、`/proc/tm/shell`、loopback API 三条干预路都被否证。
   要改的地方在 `zx279133-np.c`/`zx279133-idm.c`/`zx279133-mac.c` 这一族的端口选择/授权路径，
   参照物是 `docs/09`/`docs/11` 的 U-Boot 与厂商 Linux 寄存器镜像，以及 U-Boot 的 `idm_tx_debug`/`idm_rx_debug`。

**换内核顺带会解决的一条**：mainline 有 nftables → `fw4` 能用 → 当前 4.19 上"内核没有 nf_tables、镜像和 feed
里又没有 iptables"的 NAT 死结自动消失（见 §5.2）。这是走新内核路线的一个真实收益。

**一句话**：`overlays/` + `kernel-overlays/` + `configure-sk-d840n.sh` 就是"改内核适配光猫"的现有载体；
新执行者要做的是**在这三层上继续加**（9132 驱动 + eth0 出口端口 + 若需要则 GPON），而不是把 SR1010 的树直接搬。

**A. ImmortalWrt userland + 现有厂商 4.19 内核（推荐先做，风险最低）**
- 内容：只换 `root`（ImmortalWrt 24.10/25.x 的 armsr/armv8 rootfs → 转 jffs2，241 MiB 分区），kernel/dtb **不动**。
- 优点：LAN 通路、`br-lan`、厂商 kmods 全部现成可用；已证明这套 kernel 能带流量。
- 要做：确认 ImmortalWrt 的 `LIBC`/架构与现有 rootfs 一致（musl aarch64），把厂商 `/lib/modules/4.19.136+/`
  那 39 个 `.ko`（vermagic `4.19.136+`）一起塞进新 rootfs，并保留 `/sbin/mtd`、`zrecover`/`zkey`/`zleds` 等本机工具。
- 判据：`verify_router.sh` 5/5 **且**至少两次纯冷启动复现；`uname -a`、`/proc/cmdline`、`lsmod` 记录清楚是哪套在跑。

**B. ImmortalWrt + mainline 6.18.x（研究性，会撞墙）**
- **构建侧其实已经通了**：`build-sk-d840n.yml`（`mode=both`/`flash`）就能出成品，触发方式是 `workflow_dispatch`，
  或 push 命中 `build-sk-d840n.yml` / `kernel-overlays/**` / `overlays/**` / `configs/sk-d840n*` /
  `scripts/configure-sk-d840n.sh` / `templates/**`（改文档不会触发）。产物自带 release 指针
  `skd840n_<mode>_<run_number>_<run_id>`。
- 已知硬缺口：9132 UNI 驱动（无）+ eth0 数据通路（未通）。**不解决这两条，刷进去就是"只有 eth0、且不带流量"**。
- kit 75→80 的实际推进记录在 `docs/11`：三处寄存器值、RX 中断路由（`+0x2c8`）与 `NAPI_MASK` 加宽都**已在硬件里读到落地**，
  但 RX 中断计数仍为 0 ⇒ 路由/掩码这一族已被否证。**把它当成"下一条假设"而不是"配置没配对"**。
- 若仍要做：先在**当前** mainline 分支上把 `docs/13` 的 TM 归因做完（CPU 出口列 ≠ 期望列 → 找 NP 端口映射），
  并且**用独立的 root**（不要和 4.19 共用一个 jffs2，见 §0.2）。
- **顺带说明与路线 A 的拼接点**：路线 B 的 `rootfs.jffs2` 里装的是 ImmortalWrt 的 userland + 6.18 的 modules。
  如果只想借用它的 rootfs 而保留能载流的 4.19 内核（= 路线 A），**必须**同时带上
  `/lib/modules/4.19.136+/` 那 39 个厂商 `.ko`，否则 LAN 桥建不起来。
- 参考来源：`docs/09`/`docs/10`/`docs/11` 的 IDM/NP 寄存器对照、U-Boot 的 `idm_tx_debug`/`idm_rx_debug`
  与 `xmac_test … 0 2`（见记忆条目 *U-Boot vendor debug CLI*）；上游仓见记忆 *cnjn ZX279133 upstream repos*、
  *huxiangjs/SK-D840N-OpenWRT release v2.0*。

**C. 退回厂商 4.19 + 仅升级软件包**：不是"新内核"，但成本最低、能最快把"能上网"落地（配合 §5 的 NAT 缺口）。

---

## 5. 已知待办（按优先级）

1. **上行**：eth2 那只 RJ45 插线。`staging/ctl/jackmap.log`（每 20 s 记 eth0..eth3 的 rx/tx/carrier）会指出
   哪只 netdev 开始收；然后验 `udhcpc` 是否拿到租约、默认路由是否合理。
2. **NAT 用户态**（新内核顺带解决）：当前 4.19 **内核没有 nf_tables**，`fw4/nft` 完全空转
   （`nft list ruleset` → `cache initialization failed`），而镜像里、24.10.8 armsr feed 里都**没有** iptables
   （`iptables`/`iptables-legacy`/`libxtables*` 0 命中），厂商 `/bin/iptables` 缺 `libxtables.so.12`。
   **但内核有 legacy ip_tables + `MASQUERADE`**（`/proc/net/ip_tables_targets` 实测在）→ 只要一个程序就能用。
   取库通道：离线用 `jffs2reader`/`jefferson` 解 `restore_set/root_stock.bin`（本机没工具、`python3` 是 stub）。
   若走路线 B（mainline），nftables 有 → **换内核会自动解决这一条**。
3. **root 重刷工具链就绪校验**：`staging/reflash-root/`（`ub-*.pad` 校验过：root pad md5 `bb6025d9`、
   uImage `f68ceb0f`、dtb `881af2d3`）+ `tools/ubflash.sh`/`flash-round.sh`/`postflash.sh` +
   `tools/itbcheck.py`（FIT bring-up 顺序 byte-exact 校验，已在 kit 45/46/50 证过）。
4. **root 无密码**：机主未表态；不要擅自设密码。

---

## 6. 规矩（上一任被纠正过的点，别再犯）

- **编译只在 CI**：本机**绝不**编译；**push main 就是触发构建**。纯文档提交请**攒着一起提**，别让未验证的 kit
  占住 release 指针。CI 细节：`KERNEL_SHA` 已 pin（run 72 因为 branch-tip clone 炸出 21 个错）；
  顶层 `CONFIG_KERNEL_*` 会**覆盖** defconfig（`KERNEL_DEVMEM` 事故，run 63–67 全废），要在 build-root
  `.config` 里 defconfig **之后**再钉。
- 每个判定门都要有**正例 + 反例**；"0 命中"先怀疑自己的 pattern，而不是怀疑设备。
- 下结论前先报**身份**：`uname -r`、`/proc/cmdline`、`uptime`（分不清是哪套在跑就会得出错误结论，历史上错过）。
- 已经抓到的日志/寄存器**先挖再猜**；不要用替代物建模。
- 脚本一个用途一个文件（机主明确要求「不要写一起」）。
- 需要电源/挪线/按钥 → **停下来问**；但如果已有计划性断电窗口，先把流采样器架好去用那个窗口。
- 不要把运维越权"洗白"成证据：操作员否决门禁时，provenance 里必须写明是谁否决的。

## 7. 文件索引（接手时按这个找）

- 文档：`ci-build-repo/docs/01`…`docs/13`（`08` NP 地址空间、`09` U-Boot 已证 IDM 镜像、`10` ring 搬窗口、
  **`11` 厂商 Linux bring-up 逐字记录**、**`12` jffs2 dirent 损伤与修复 + 刷好的标准**、**`13` eth0 出口归因/
  upmode 否证/tm shell 否证/eth2 隔离/NAT 缺口**），`HANDOFF.md`（22:53 收口段 + 本文件之前的历史）。
- 工具：`tools/verify_router.sh`（4 通道判定）、`tools/eth0egress.sh`（TM 归因，含 idle 基线）、
  `tools/jackmap.sh`、`tools/eth0jackwatch.sh`、`tools/boot419score.sh`（开机打分）、`tools/ubflash.sh`、
  `tools/ubram.sh`、`tools/reflush_root.sh`（新 inode 重建，救命用）、`tools/onu2.cjs`（大文件传输）。
- **新内核这条线的本地落点**（不用重新下载）：
  `ci-build-repo/kernel-overlays/`（`zx279133-sk-d840n.dts` + `drivers/net/ethernet/zte/zx279133-{main,mac,np,idm,netdev}.c`
  + `drivers/phy/zte/phy-zx279133-uni-serdes.c` + `drivers/mtd/nand/spi/unim.c`）、
  `ci-build-repo/overlays/target/linux/zte/`（含 jffs2 的 target/image Makefile）、
  `ci-build-repo/scripts/configure-sk-d840n.sh`（符号 pin）、
  `ci-build-repo/tests/kernelsrc/arch_arm64_boot_dts_zte_zx279133-sr1010.dts`（SR1010 基线 dts，另有
  `staging/src/zx279133-sr1010.dts`）、`staging/src/fork-SR1010_MTD_WRITE_ACCESS.md`（MTD 写权限说明）、
  `.github/workflows/build-sk-d840n.yml`（**本板这条线的主管线**：ImmortalWrt base + 6.18.38 内核 + overlay，
  `mode=initramfs|flash|both`）、`.github/workflows/build-sr1010.yml` 与 `configs/sr1010-extra.config`（SR1010 基线，
  别拿它当本板的产物）。
  注意还存在一份工作副本 **`ci-wanport/`**（含同名 `build-sr1010.yml`/`configs`/`tests`），接手前先确认改的是哪一个。
- 数据：`device-dumps/`（**全分区导出，242 MB**，用户原始要求里就点名要它）、`restore_set/`（原厂五件套）、
  `backups/`、`staging/vendor-full/`（厂商 initramfs 全量 + `MANIFEST-links.txt`）、
  `staging/vendor-root/`（跑 arm32 厂商二进制所需的最小库集，route B 用过）、`staging/ctl/`（串口日志与各采样日志）。
- 提交（docs-only，**未 push**）：`d3f4671 5984933 0fab4c3 0596d03 b69dc57 17e73ea 9bb0887 055a788 fa75a0e 2c71f1c`。

## 8. 交接给新模型的 60 秒版

板子已刷好并可作为 LAN 路由器使用（4.19 厂商内核 + OpenWrt 24.10.8 root，5/5 验证、冷启动可复现）。
新目标是「ImmortalWrt + 新内核」：**先选路线**（§4 的 A 或 B）。A 只换 root、几乎无风险；
B 必须先解决 9132 UNI 驱动与 eth0 的 NP 数据通路（**这两条已被多轮实测确认不通**，别当成配置问题）。
**构建不是瓶颈**：`build-sk-d840n.yml` 已经能出 ImmortalWrt + mainline 6.18.38 的 flash 三件套（kit 75–80 已发过），
瓶颈是**产物在板上不载流**——所以先决定要不要接受"刷进去只有一张嘴能看见的 eth0"。
无论 A/B：**换内核要连 rootfs 一起出**，别和 4.19 共用同一个 jffs2 根；**刷 root 必须先进 RAM 环境**。
所有构建走 CI（push main = 触发），本机不编译。缺什么就明确向机主要：插线、看灯、按 RESET、批准写 flash。
