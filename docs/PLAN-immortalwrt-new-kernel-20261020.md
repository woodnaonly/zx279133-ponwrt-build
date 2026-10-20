# 执行计划：SK-D840N 新内核 + ImmortalWrt（GPON 光猫）

写于 2026-10-20 23:5x。读者：执行本计划的会话/模型。**先读** `HANDOFF-immortalwrt-new-kernel-20261020.md`（目标口径、板子状态、仪器坑、规则），本文件只负责“按什么顺序做、每步的判据是什么”。

---

## 0. 目标、范围、非目标

**目标**：在 SK-D840N（ZX279133，**GPON ONU**）上运行 **ImmortalWrt + mainline 6.18.38**。基线内核来自 SR1010（路由器），板级适配必须自己做。

**两个里程碑，不要混在一起**：

| 里程碑 | 内容 | 验收口径 |
|---|---|---|
| **M1｜mainline 实验室路由器** | 9132 驱动 + ImmortalWrt + 以太网上行 + NAT | 至少两个 9132 LAN 口；一只口当 WAN 能拿租约；LAN 客户端能上网；两次冷启动 |
| **M2｜可用光猫** | M1 + PON 状态机/OMCI | 光纤注册、OMCI、T-CONT/GEM、LAN 客户端经 PON 上网；两次冷启动 |

**默认按 M2 规划，但 M1 是 M2 的前置**：PON 状态机是独立大工程，不要阻塞 9132。**只有 M1 通过后，才把 M2 的 PON 工作并入主线。**

**非目标**：
- 不把 eth0/jack 4 当门槛。厂商 4.19 在同一只 jack 上也 0 字节；只有 U-Boot 发过 11 MB。它是可选旁支。
- 不把“继续用 4.19”当最终目标。路线 A 只是 fallback，不产出新内核、不解决 NAT。
- 不用 `/dev/ethdriver` 的 ioctl 做配置。它是 8 字节桩（docs/07 补九十七），真正的端口/VLAN 表是 `switch.ko` 在 init 时写的。
- 不再做“和 U-Boot 逐寄存器对照”的第十一轮。docs/07 补九十五已经把这条方法的边际收益用尽。

---

## 1. 起点事实（2026-10-20 23:21–23:25 实测）

- 板子在线：ping 4/4、ssh、LuCI HTTP 200；`uname -r` = `4.19.136+`；cmdline = `root=/dev/mtdblock5 rootfstype=jffs2`；uptime 6 min（23:16 恢复）。
- `/proc/mtd`：whole 128M、boot 2M、kernel 8M、dtb 1M、param 4M、**root 241M**（无 scratch）。
- `br-lan` 只含 eth1；`wan` = eth2 + dhcp；eth3 已摘出；`wan6` 仍指 eth0。
- `/lib/modules/4.19.136+`：39 项/**38 个 .ko**，当前 `lsmod` **34**；另有 `6.6.144` 的 114 个 .ko（死重量）。
- `nft` 失败；`MASQUERADE` builtin；`ip_forward=1`；`/sbin/mtd` 支持 `verify` 和按标签写。
- **`switch.ko` 身份**：板上 `/lib/modules/4.19.136+/switch.ko` = **md5 `993f012407847954805d89efaf51110d`**，本地对应 **`pon/vendor-modules/switch.ko`**（同 md5）。`staging/vendor-full/kmodule/switch.ko` 是另一版（`fdeba014b9c8cddcfc28eaac9700bca0`），**不要拿它做差集**。
- 断电节奏：约每 15.7 分钟，实测 `21:56 / 22:12 / 22:28 / 22:43 / 22:59 / 23:14`；每轮开始前用 `staging/ctl/jackmap.log` 的 `NO-REPLY` 重新对表。
- 本机 Python：`python` = 3.13.5（可用）；`python3` 是 WindowsApps 桩，别用。CI 仍是唯一编译通道。
- **原厂全量导出在 `device-dumps/`**（242 MB；`device-dumps.zip` 188,823,324 B，解压后与目录内容一致）：`sk-d840n_b1.bin`（boot）、`sk-d840n_k0.bin`（kernel0）、`sk-d840n_mtd2.bin`、`sk-d840n_mtd4_retry.bin`、`sk-d840n_mtd5_retry.bin`、`sk-d840n_mtd12_osgi1.bin`、`sk-d840n_mtd14_awifi.bin`、`p11_v2_chunk0..3.bin`、`nf_nat_ftp.ko`、`sk-d840n_fw_flashing.bin`。`restore_set/` 是五件套**恢复**集；`staging/vendor-full/` 是厂商 **initramfs 全量**；需要原厂二进制/配置时先在这里找，不要重新从板上拉。

---

## 2. 关键路径（一句话）

**switch.ko 离线差集（G1）→ 9132 最小驱动（G2）→ mainline ImmortalWrt + NAT（G3）→ 刷机/回退（G4）→ PON 状态机（G5，M2）。**

G1 之前**不 build、不 push、不动板子**。G1 若差集为空，停下来重新归因，不要“先写个驱动试试”。

---

## 3. M0 — 基线与决策（约 1 个会话，无板子操作）

- [ ] 复核身份：`uname -a`、`cat /proc/cmdline`、`cat /proc/mtd`、`uci show network`、`lsmod | wc -l`。
- [ ] **快照当前网络配置**：`ssh root@192.168.9.1 'uci export network' > backups/network-$(date +%Y%m%d-%H%M%S).txt`（22:35 的 `network-backup` 是旧配置，不能当 rollback）。
- [ ] 确认口径：M1 only 还是 M1+M2。写进本轮的执行记录，不要口头默认。
- [ ] 确认物理前提：M1 需要一根上游网线插到 eth2 对应的 RJ45、PC 插在 eth1 对应的 RJ45；M2 需要光纤和机主配合。
- [ ] 确认 canonical 仓库：`ci-build-repo/`（`ci-wanport/` 是 10-16 旧副本，**不要 push**）。
- [ ] 把当前未提交的文档/DTS 改动看清楚：DTS 在 `kernel-overlays/**` 触发路径里，push 会拉起构建；docs-only 不会。

**G0 判据**：配置已快照；口径已写；板子身份与 §1 一致；canonical 仓库确认。  
**回退**：无（只读 + 配置快照）。

---

## 4. M1 — 9132 / mainline 实验室路由器

### 4.1 W1：switch.ko init 离线差集（task #42，无板子）

**目的**：把“mainline 少写了什么”从猜测变成一张表。**这一步不需要板子、不需要 CI。**

**输入**：
- `pon/vendor-modules/switch.ko`（md5 `993f0124…`，与板上一致）。
- `ci-build-repo/kernel-overlays/drivers/net/ethernet/zte/zx279133-np.c`（`zx279133_lan_port_bringup()` 在 1246 行；mem5/10/15 的 `0x01f4000c` 在 1283/1292/1298；`zx279133_wan_port_bringup()` 在 1317+）。
- 工具：`tools/ponelf.py`、`tools/ponDis.py`、`staging/kofn.py`、`staging/kocalls.py`（Capstone 已装；本机用 `python`）。
- **原厂全量导出 `device-dumps/`**（242 MB；boot/kernel0/mtd2/4/5/12/14/p11 分区 dump + `device-dumps.zip`）：需要交叉核对其它模块版本、厂商配置或 rootfs 内容时先在这里找；`restore_set/root_stock.bin` 还含 stock kernel1 + 厂商 OMCI 用户态。

**步骤**：

1. **正例控制**（防止工具/重定位数学错）：
   ```bash
   python staging/kocalls.py pon/vendor-modules/np_133.ko idm_net_lan_tx_direct
   ```
   必须看到 docstring 里那个已知的 doorbell store（`0x11dd8`）和至少一个已解析调用。看不到就停下来修工具，不要继续。
2. **列 init 符号**：
   ```bash
   python tools/ponelf.py pon/vendor-modules/switch.ko symbols 'init|probe|sw_init'
   ```
   **实测（2026-10-20 23:35）：`init_module` 是 0x84c/0x90 字节的桩，`kocalls.py` 解析出 0 个调用**——不要从它展开。真正的初始化入口是 **`sw_init_switch`（0x2e74）**，它调用 `sw_init_port_protocol_action`、`sw_set_default_vlan_check_ctrl`、`sw_init_port_isolate`、`sw_init_mac_lookup_ctrl`、`sw_init_flood_ctrl`、`sw_init_omci_trap_qid_init`、`sw_init_port_route_mode`、`sw_reg_set_onu_swcap`、`sw_mac_table_op_init`、`sw_multi_config_init` 等。
   这些函数大多只是调用 `zxic_api_sw_*`/`tm_*`/`sw_reg_*` 的 API；**真正的寄存器/表写在 API 实现层**。先判断符号在哪：
   ```bash
   python tools/ponelf.py pon/vendor-modules/switch.ko symbols 'zxic_api_sw_|tm_port_route|sw_reg_'
   python tools/ponelf.py pon/vendor-modules/switch.ko imports | grep -E 'zxic_api_sw_|tm_|sw_reg_'
   ```
   若在 `switch.ko` 里定义，就继续 `kocalls.py`/`ponDis.py`；若 UND，去 `np_133.ko`/`ffe.ko`/其他厂商模块里找定义，再展开。**只到 API 实现层，才能看到真正的写集。**
   已测到的调用样例：`sw_init_port_route_mode → tm_port_route_mode_set`；`sw_init_port_isolate → zxic_api_sw_set_port_isolate_enable / zxic_api_sw_port_set_port_isolate`；`sw_init_flood_ctrl → zxic_api_sw_port_{mul,unk_da,brd}_flood_{dest_port,en}`。
3. **逐个函数取调用表 + 写站点**：
   ```bash
   python staging/kocalls.py pon/vendor-modules/switch.ko sw_init_switch
   python tools/ponDis.py  pon/vendor-modules/switch.ko sw_init_switch
   python staging/kocalls.py pon/vendor-modules/switch.ko sw_init_port_route_mode
   python staging/kocalls.py pon/vendor-modules/switch.ko sw_init_port_isolate
   # 其余 sw_init_* 同上，按 sw_init_switch 的调用表逐个展开；再对 API 实现层重复
   ```
   `kocalls.py` 同时打印 call list 和 store list；`ponDis.py` 给带 reloc 注解的反汇编；`kofn.py` 给线性反汇编。
4. **建表**（落 `staging/ctl/switchko-init-<date>.txt`，再整理成 Markdown）：
   | ko symbol | addr | width | value / source | context（port/VLAN/PHY/表项） | np.c 对应写 | 差集？ |
   |---|---|---|---|---|---|---|
   记录每个 store 的地址、位宽、值是立即数还是来自寄存器/表；把 `spa_indirect_*` 的参数（mem/entry/offset）单独列一列。
5. **对照 np.c**：
   ```bash
   grep -n 'zx279133_lan_port_bringup\|0x01f4000c\|zx279133_wan_port_bringup' \
     ci-build-repo/kernel-overlays/drivers/net/ethernet/zte/zx279133-np.c
   ```
   只比较**同一目标**（mem/entry/offset），不要按行号比较。重点：switch.ko 的端口使能、VLAN/PVID、PHY 地址 0x0A–0x0D、CPU 口、MAC 表初始化。
6. **产出**：
   - `staging/ctl/switchko-init-<date>.txt`（原始输出）
   - `ci-build-repo/docs/15-switchko-init-diff-<date>.md`（表 + 结论 + 下一步）

**G1 判据（硬门）**：
- 正例控制通过（`np_133.ko idm_net_lan_tx_direct` 的 `0x11dd8` store 可见；实测 2026-10-20 23:35 可见）。
- 差集表非空，且至少一条差异能解释“9132 的端口没有 bring-up”。若 API 实现位于其它厂商模块，差集必须包含那个模块（并用 md5 标明是哪一版）。
- 若差集为空：**停止，不 build**。说明 switch.ko init 不是缺失点，重新归因（PPU 微码/CLA、U-Boot outerphy 初始化、交换芯片的隐藏状态），不要“先写个驱动试试”。

**回退**：无（纯离线）。

**预计**：1–2 个会话，无窗口、无板子。

---

### 4.2 W2：9132 最小驱动（kernel-overlays）

**前置**：G1 通过，且差集表里至少有“端口使能 + VLAN/PVID + CPU 口”三类写。

**设计约束**：
- **不做 DSA/switchdev**；不做 phylib；不用 `/dev/ethdriver`。
- 只做：CPU 口 + 4 个 UNI 口 + 固定 VLAN；先把 eth1/eth2/eth3 变成可收发的 netdev。
- 参考 `switch.ko` init 的写集，不参考 `np.c` 里给外部 RTL8372N 的 `lan_port_bringup()` 非零 entry（它对应另一个盒子，不是本板 jack 4 的 mem6）。
- netdev 的创建方式以 W1 的调用表为准（`switch.ko` 怎么建 eth1/2/3，就照什么顺序）；在没读到之前不要假设 DSA 或 `lan-conduit`。

**落点**：
- 新文件 `ci-build-repo/kernel-overlays/drivers/net/ethernet/zte/zx279133-switch.c`（名字可调），加 Kconfig/Makefile overlay。
- DTS：在 `kernel-overlays/arch/arm64/boot/dts/zte/zx279133-sk-d840n.dts` 里加 9132 节点（寄存器范围、MDIO、四个 PHY 0x0A–0x0D、中断如需要）；保留现有 `nppt` 节点不动。
- 如果最小驱动必须复用 `np.c` 的 SPA/IDM 寄存器路径，优先加 **新的差集写**，不要改现有 `lan_port_bringup()` 的外部交换机路径。

**构建/试跑**：
- 只走 CI：`mode=initramfs`，`KERNEL_SHA` 保持 pin；**本机不编译**。
- 触发：push 命中 `kernel-overlays/**` 即构建；push 前确认 diff 表已产出、改动只包含本轮需要的文件。
- 试跑：`tools/getkexec.sh` 组 kexec 工具 → `trial-kexec.sh` 起 RAM 镜像（不动 flash）。身份先看 `uname -r` = 6.18.38、`/proc/cmdline` 是 trial cmdline。

**G2 判据**：
- `/sys/class/net` 出现 eth1/eth2/eth3（至少 eth1/eth2）；`ip -br link` 正常。
- PC 插在对应 LAN 口，`ping -S 192.168.9.25 <板子地址>` 通；至少一只 9132 口双向收发。
- `dmesg` 无 oops、无 “no driver” 的 9132 节点；两次冷启动（kexec 重跑）结果一致。
- 没有写 flash（initramfs trial 不挂 mtd5；若挂，只读）。

**回退**：断电回 4.19；flash 未动。

**预计**：2–4 个 CI kit + 2–4 个断电/kexec 窗口（每个 kit 构建约 1–1.5 h，窗口受 15.7 分钟周期限制）。

---

### 4.3 W3：mainline ImmortalWrt 路由器（NAT / 用户态）

**前置**：G2 通过。

- 用 mainline 的 ImmortalWrt rootfs（CI 的 `mode=initramfs` 里已带）配置：`eth2` 当 WAN（实验室以太网上行），`eth1` 当 LAN；`br-lan` 只含 eth1。
- `nft list ruleset` 必须能工作（mainline 有 nf_tables）；`fw4` 正常实例化。
- 上游网线由机主插到 eth2 对应的 RJ45；PC 插 eth1。
- 判据：PC 拿到 DHCP；能 ping 上游/公网；`nft` 计数器增长；`ip route` 默认路由正确；两次冷启动。
- 注意：这是**实验室口径**，不是光猫口径；PON 不在本轮验收内。

**G3 判据**：DHCP + DNS + NAT + 两次冷启动；`uname -r` 仍是 6.18.38；无 9132 口掉线。

**回退**：RAM 镜像重启即回 4.19；flash 未动。

**2026-10-21 的两处修正（执行前必读）**：

1. **"mainline 有 nf_tables" 这句话当时是错的**：这棵树/这个 feed 的
   `CONFIG_NETFILTER` 关着，镜像里也没有 nft、没有 iptables（连 busybox 的
   iptables applet 都没有），feed 的 nftables 包还依赖一个这个分支里不存在的
   kmod 包。⇒ NAT 的内核半边和用户态半边都要补：run97 把 netfilter 全部编进
   内核，用户态用 `overlays/package/network/utils/nftables/Makefile`。
   **符号名按本树写**：masquerade 是 `CONFIG_NFT_MASQ`；counter 表达式没有
   自己的符号（`nft_counter.o` 在 `nf_tables-objs` 里）。详见 **docs/21**。
2. **"上游网线由机主插到 eth2 对应的 RJ45" 这一句现在没有第二根线可插**：
   板上只有 PC↔UNI2（我们的 `lan2`）一根。⇒ 两条路并存：
   - **真两口**：机主插第二根线（插哪只都行，板子自己认：`S26nat` 搜
     `lan3`→`lan1`→`lan4`，60 s 一轮的 watcher 认出后插的线）；
   - **单线（本轮走这条）**：PC 当上游（Windows ICS，Wi-Fi 出、有线回，自带
     DHCP 和 NAT），板子的 WAN = PC 所在口（`/etc/labwan = pc`），板子的 LAN =
     其余三口 + 板子自己造的客户端（`S27labclient`：veth 对 + 网络命名空间）。
     DHCP 服务器 / DHCP 客户端 / 桥 / 路由 / NAT / DNS / 上网全部可验，
     **只有"两口各插一根线"这个物理形态仍然要一根线**。
   另外：这版镜像的 `/etc/config/network` 是 **JSON**，改配置要走镜像自己的
   helper（`board_config_update` + `ucidef_set_interface`），`uci set` 不保证被接受。

---

### 4.4 W4：刷机 / 回退（只在 G2+G3 都通过后）

**前置**：G2、G3 通过；机主批准写 flash；RAM 环境已就绪。

- 用 CI 的 `mode=flash` 产物：`flash-uImage`、`flash-dtb.bin`、`flash-rootfs.jffs2`（以及 `flash-set.tgz`）。
- **必须从 RAM 环境刷**（kexec initramfs 或 zrec 网页恢复），不能从当前 4.19 的 `/` 里刷 root。
- 按 `templates/flash-sk-d840n.sh` 的顺序与标签写：root → dtb → kernel（以模板为准；U-Boot 的 `root=/dev/mtdblock5` 依赖分区顺序）。
- 写前确认 `mtd verify` 可用；写后 `mtd verify`。
- 判据：`uname -r` = 6.18.38；`/proc/cmdline` 是 pinned cmdline；`/proc/mtd` 是 mainline 布局（root 240M + scratch 1M）；`verify_router.sh` 5/5（或 mainline 适配版）；两次冷启动；jffs2 无 dirent 损伤。
- 回退：`restore_set/` 五件套 + `backups/root-20261020-2045.bin.gz`；U-Boot/tftp 串口链已备。
- **不要在 4.19 与新内核之间共用同一个 jffs2 根**；换内核 = kernel+dtb+root 一套。

**G4 判据**：两次冷启动 + `verify_router.sh` + jffs2 干净 + 回退路径可用。

---

## 5. M2 — PON / 可用光猫（独立工作流）

**前置**：M1 的 G2 通过（LAN 侧可用）；M2 可并行做可行性，但不要阻塞 M1。

- **可行性**：把厂商 `zte_xgpon`/`gpondrv_xg`/OMCI 的 ABI 与 mainline 的 PON 数据通路对齐。材料：`pon/INVENTORY.md`、`pon/abi-scan.txt`、`pon/zte_xgpon-api.txt`、`pon/gpondrv-ioctl.txt`、`docs/07` 的 PON 段；厂商二进制/用户态从 **`device-dumps/`**（原厂全量导出、242 MB）和 `restore_set/root_stock.bin`（stock kernel1 + 厂商 OMCI 用户态）里取，`staging/vendor-full/` 是 initramfs 全量。
- **已知硬点**：mainline 树有 PON MAC + mcode + 数据通路，但**没有** G.984 PLOAM/GEM/T-CONT/OMCI 状态机；厂商 4.19 有整套，但 OMCI TX 门未打通（`swport_dev_xmit_fin` 读 `dev+0x8c8`，无写者）。所以 M2 不是“补一个模块”，是移植/重写状态机。
- **决策点**：port 厂商状态机 vs 用上游 OMCI 实现 vs 先只做 PON 链路/PLOAM 最小闭环。写一份可行性报告，再决定。
- **测试**：光纤由机主插；判据是 `pon` link、PLOAM/注册、OMCI MIB、T-CONT/GEM、LAN 客户端经 PON 上网；两次冷启动。
- **不要**在没有 PON 状态机的情况下把产物叫“可用光猫”。

**G5 判据**：PON 注册 + OMCI + 数据 + 两次冷启动。未通过前，M2 只能叫“研究”。

---

## 6. 验收矩阵

| 项 | M1 | M2 |
|---|---|---|
| 内核 | `uname -r` = 6.18.38；cmdline pinned | 同 M1 |
| LAN | eth1/eth2/eth3 存在；至少两只双向 | 同 M1 |
| WAN | eth2 拿 DHCP 租约；默认路由 | PON 注册 + T-CONT/GEM |
| NAT | `nft` 规则 + 计数器 + LAN 客户端出网 | 同 M1 |
| 稳定性 | 两次冷启动；jffs2 无损伤 | 两次冷启动 |
| 回退 | 4.19 备份/RAM 恢复链 | 同 M1 |
| 光猫 | 不要求 | PON + OMCI + 数据 |

---

## 7. 风险与停止条件

| 风险 | 触发信号 | 停止/转向 |
|---|---|---|
| switch.ko 差集为空 | G1 表无差异 | 停止；重新归因（PPU/CLA、U-Boot outerphy、隐藏状态），不要盲写驱动 |
| 9132 寄存器写不进去/读不回 | G2 端口不出现在 `/sys/class/net` | 回到 W1，检查寄存器窗口/时钟/复位；不要连续烧 kit |
| 9132 netdev 建了但不通 | G2 有 eth1/2/3 但 ping 不通 | 查 VLAN/PVID/PHY、CPU 口、MAC 表；一次只改一类 |
| PON 状态机工作量过大 | M2 可行性报告 | 冻结 M2，保留 M1 实验室路由器 |
| jffs2 dirent 损伤 | `open: Not a directory` | 立即停；按 docs/12 重建 inode；不要 rename |
| 断电窗口错过 | jackmap `NO-REPLY` 与预期不符 | 重新对表；不要按整刻钟计划 |
| CI 构建被未验证 overlay 占住 release | push 后出现新 kit | 只推差集已确认的改动；docs-only 不会触发 |
| 拿错 switch.ko | md5 不是 `993f0124…` | 用板上副本；`staging/vendor-full` 那份是另一版 |

---

## 8. 纪律（每轮开头读一遍）

- **本机不编译**；构建只走 CI。push 只对 `kernel-overlays/**`、`overlays/**`、`templates/**`、`configs/sk-d840n*`、`scripts/configure-sk-d840n.sh`、`build-sk-d840n.yml` 触发；docs 不触发。
- **canonical = `ci-build-repo/`**；`ci-wanport/` 是旧副本，不要 push。
- **身份第一**：`uname -r`、`/proc/cmdline`、`uptime`；分不清哪套在跑就不下结论。
- **正例 + 反例**：每个 gate 都要有；0 命中先怀疑 pattern/工具。
- **先挖日志再猜**；不用截断的 dmesg 表归因；TM 窗口按标签配对，不按行号。
- **脚本一个用途一个文件**。
- **需要电源/挪线/按 RESET/写 flash → 停下来问**；已有计划性断电窗口时，先架好采样器再用。
- **子代理不许碰板子/串口**；任何“设备现在如何”自己重读。
- **不要 mix 两个内核写同一个 jffs2 根**；root 只能从 RAM 环境刷。

---

## 9. 执行者第一小时清单（从这里开始）

1. `ping -S 192.168.9.25 -n 4 192.168.9.1`；`ssh -i ~/.ssh/id_onu root@192.168.9.1 'uname -a; cat /proc/cmdline; cat /proc/mtd'`；确认 §1。
2. `ssh ... 'uci export network' > backups/network-$(date +%Y%m%d-%H%M%S).txt`；确认 `br-lan=eth1`、`wan=eth2 dhcp`。
3. `ssh ... 'md5sum /lib/modules/4.19.136+/switch.ko'`，必须是 `993f0124…`；否则先把板上副本拉到 `staging/`。
4. 正例控制：`python staging/kocalls.py pon/vendor-modules/np_133.ko idm_net_lan_tx_direct`，确认 `0x11dd8`。
5. `python tools/ponelf.py pon/vendor-modules/switch.ko symbols 'init|probe|sw_init'`；**从 `sw_init_switch` 开始**（`init_module` 是 0 调用的桩），再到 `zxic_api_sw_*`/`tm_*`/`sw_reg_*` 的 API 实现层。
6. 产出 `staging/ctl/switchko-init-<date>.txt` 和 `docs/15-...md`；**G1 通过前不 build、不 push、不动板子**。
7. G1 通过后，才进入 W2；否则停下来重写归因。

---

## 10. 关联文件

- 交接书：`ci-build-repo/docs/HANDOFF-immortalwrt-new-kernel-20261020.md`
- 归因：`docs/07`（补七十七/八十一/九十四/九十五/九十六/九十七）、`docs/13`（eth0/TM/NAT）、`docs/11`（kit 75–80）
- 工具：`tools/ponelf.py`、`tools/ponDis.py`、`staging/kofn.py`、`staging/kocalls.py`、`tools/jackmap.sh`、`tools/getkexec.sh`、`tools/ubflash.sh`、`tools/itbcheck.py`（在 `ci-build-repo/tools/`）
- 构建：`ci-build-repo/.github/workflows/build-sk-d840n.yml`；`scripts/configure-sk-d840n.sh`；`kernel-overlays/`
- 数据：**`device-dumps/`（原厂全量导出，242 MB；含 `device-dumps.zip` 188,823,324 B）**、`pon/vendor-modules/switch.ko`（live md5 `993f0124…`）、`staging/vendor-full/kmodule/switch.ko`（另一版 `fdeba014…`）、`restore_set/`（五件套）、`backups/`
