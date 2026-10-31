# SK-D840N 面板 2.5G 口（LAN1）双向换档：问题、根因与修复（2026-10-31）

本文是给上游作者的独立参考材料，只描述与 2.5G 口 lane 直接相关的硬件行为、
寄存器证据和驱动改动，不含本项目其他调试内容。

平台：ZTE ZX279133（SK-D840N），Linux 6.18，OpenWrt 25.12.1。
面板 LAN1 = RTL8226B → 9132 PPU5 → SoC Uni SerDes → XPCS0/XMAC0。

---

## 1 结论

1. **Linux 下 2.5G 口不通，不是 PHY 配错，而是 SoC 侧没有 2500BASE-X 的
   PCS/XMAC 编程路径**：XPCS 被显式清掉 2G5 能力，XMAC 固定写 1G SGMII（速度码 3）。
2. **能通 2.5G 但降不回 1G，根因是驱动内 1G 49 词镜像数组写错 + 换档时序与
   vendor 不一致**：复位+恢复快照会让 lane 停在“PLL 锁了但不出码”（`1e:758d=0x8018`）。
3. 修复后，冷启动 1G/2.5G 都正常，**不断电 2.5G→1G→2.5G 换线往返全部 0% 丢包**，
   换档到 Link Up 约 3.1 s。
4. 关键判据只认三个读数：`1e:7580`（系统侧档位）、`1e:758d` bit2（lane 是否送码）、
   `1f:a434`（铜侧速率码）。镜像对错最终都体现在这三个值上。

---

## 2 拓扑与角色（容易搞错的地方）

```text
面板 LAN1 ── RTL8226B 铜侧
              └─(2500BASE-X / SGMII)─ 9132 PPU5（第 5 个 UNI 口）
                     └─ 9132 CPU 口 ── Uni SerDes(0x16100000) ── XPCS0 ── XMAC0
                            └─ NPPT ── CPU
```

- LAN1 是 9132 的 **PPU5**，在 DSA 里是 **port 8**；transport VID = 55 + 8 = 63。
  旧代码把它当 port 9 / VID 63 特例处理，其实 55+9=64 在硬件上不存在。
- RTL8226 挂在 **SoC MDIO #1**，C45 devad `0x1e`（SerDes/option）、`0x1f`（状态）。
  本板 DTS 故意不挂 PHY 子节点，避免 realtek phylib 改写它的 SerDes option。
- 2.5G 档位由 **PHY 自己**根据铜侧协商结果切换（`1e:7580` 低 6 位：
  `0x02`=SGMII，`0x16`=2500BASE-X），SoC 侧必须跟着切。

---

## 3 症状

| 编号 | 现象 | 现场读数 |
|---|---|---|
| S1 | U-Boot 下接 2.5G 交换机可通，进 Linux 不通 | `1e:7580=0x9416`（PHY 已 2500BASE-X），XPCS0 link down |
| S2 | 同一根线下强制 1G 也不通 | XMAC 速度位=3、`2G5_EN=0`，与 PHY 的 2500BASE-X 互斥 |
| S3 | 2.5G 修好后，运行中 2.5G→1G 回落失败 | `1e:758d=0x8018`（bit2=0，lane 不送码），carrier=0，ping 100% 丢 |

---

## 4 根因

### 4.1 SoC 侧从来没有 2.5G lane 编程（S1/S2）

- `xpcs_sgmii()` 显式清 `XPCS_2G5_EN`（vend2 DIG_CTRL1 与 PCS
  `VR_XS_PCS_DIG_CTRL1` bit2）；
- `xmac_program()` 只写 1G（速度码 3）或 USXGMII（0），从不写 2.5G 的 **6**；
- 全树没有 2500BASE-X 的 PCS/XMAC profile。
- PHY 铜侧协商到 2.5G 后系统侧自动切 2500BASE-X，于是两端模式互斥，
  链路必然不通；降 1G 也救不回来，因为 PHY 的系统侧不会自己退回。

### 4.2 驱动内 1G 镜像数组是错的（S3 的核心）

板上真实 1G 首帧（vendor CPU-133 `uni_mode_eth_1gbase_x_cfg`）与驱动里
旧数组有 **28/49 词不同**，关键位置如下（板上逐字读回）：

| offset | 正确值（vendor CPU-133） |
|---|---|
| `0x00` | `0xe0000004` |
| `0x40` | `0xc5a85100` |
| `0x48` | `0x01045b2a` |
| `0x4c` | `0x66002200` |
| `0x50` | `0x34000003` |
| `0x54` | `0x00000100`（使能后 `0x00000101`） |
| `0x60` | `0x200554a8` |
| `0x64` | `0x00648091` |
| `0x74` | `0x10640002` |
| `0xac` | `0x40002000` |

写错的镜像 PLL 能锁，但 lane 不出码，正好对应 `1e:758d=0x8018`。

### 4.3 换档时序与 vendor 不一致（S3）

- 旧实现：`reset pulse` → 写 1G 镜像 → 清 `1G_RATE` 再置位 → 恢复 128 词快照。
- vendor CPU-133：`uni_mode_eth_1gbase_x_cfg` **原地**写 49 词；随后
  `uni_serdes_init` 里对 `+0x54` 做 `|= ENABLE | 1G_RATE`，一次使能，再等 PLL。
- 区别：块复位和快照恢复会把刚写进去的镜像破坏/覆盖；2.5G↔1G 不需要块复位。
  现场实验证明：在活板上**不复位、不关时钟**，原地写 49 词 + `0x54=0x101`，
  立刻得到 `758d=0x801e`、carrier=1、1000 Mbps、ping 通。

---

## 5 修复（两处）

### 5.1 `zx279133-switch.c`：让 SoC 侧跟着铜侧走

1. probe 顺序：先取 serdes，读 `1e:7580` 决定初始 mode，再 `phy_power_on()`，
   watchdog 最后启动（消除“日志说切了、寄存器没切”的竞态）。
2. 新增 `outerphy_apply_speed(speed)`：显式 `power_off()` → 换档 → `power_on()`，
   再写 XPCS profile、XMAC 速度（2.5G=6，1G=3）、发布 SOPC；失败自动回落 1G。
3. 新增 XPCS 2500BASE-X profile（`pcs_type=0xe`、PMA speed_sel、low_power 脉冲），
   沿用 vendor 的“不置 2G5_EN”写法。
4. `phylink_fixed_state()` 上报真实速率，不再恒 `2500`。
5. DSA：LAN1 改回 port 8，tagger 用 `BASE + port` 的通用规则，
   删除 port 9 / VID 63 特例（`include/net/dsa_zx279133_rtl8372n.h`、
   `net/dsa/tag_zx279133_rtl8372n.c`）。

### 5.2 `phy-zx279133-uni-serdes.c`：换镜像只做“原地写 + 一次使能”

1. `zx279133_uni_1g_profile` 换成 vendor CPU-133 1GBASE-X 49 词；
2. 删除 `saved_image` / `image_saved` / `reset_done`，1G 分支不再复位、不再恢复快照；
3. 1G 分支：
   - 若 `!(0x54 & BIT(8))` 且 `0x50 == 0x34000003`（lane 里还是 2.5G 镜像），
     原地写 49 词；
   - 然后 `0x54 |= ENABLE | 1G_RATE`（即 `0x101`），轮询 `0xd0` bit0；
4. 2.5G 分支：写 49 词 → `0x54=0x409` → 轮询 `0xcc & 7 == 7`（CPU-133 的
   SC PLL），再轮询 `0xe4` bit1（CDR lock）。

---

## 6 实测（kernel a25g，U-Boot TFTP 刷写）

| 阶段 | 物理动作 | 关键读数 | 结果 |
|---|---|---|---|
| 冷启动（线在 1G 口） | 上电 | `7580=9402`、`758d=801e`、`a434=182e`、mode `0x101` | 1000 Mbps，ping 5/5 |
| 升 2.5G | 换到 2.5G 口 | mode `0x409`、`7580=9416`、`758d=801e`、`a434=121e` | Link Up 2.5Gbps，ping 10/10 |
| 降 1G（历史失败项） | 换到 1G 口 | 日志 `1G: 2.5G image replaced in place`、mode `0x101`、`7580=9402`、`a434=102e` | ~3.1 s Link Up 1Gbps，ping 20/20 |
| 再升 2.5G | 换回 2.5G 口 | mode `0x409`、`7580=9416`、`a434=121e` | Link Up 2.5Gbps，ping 30/30 |
| 冷启动（线在 2.5G 口） | 上电 | mode `0x409`、`7580=9416`、`a434=121c` | Link Up 2.5Gbps，ping 20/20 |

判读规则：

- `1e:758d` bit2：`0x801e` = lane 在送码，`0x8018` = 不送码；
- `1f:a434`：`sig=(v>>9)&3`、`spd=(v>>4)&3`；`sig=1/spd=1` ⇒ 2.5G，
  `sig=0/spd=2` ⇒ 1G；
- `16100000+0x54`：`0x409` = 2.5G 镜像 + ENABLE，`0x101` = 1G 镜像 + ENABLE + 1G_RATE。

---

## 7 复现与调试命令

```sh
# RTL8226（注意必须带 0x 前缀，kstrtouint(...,0)）
echo 0x1e:0x7580 > /sys/module/zx279133_switch/parameters/phydbg
echo 0x1e:0x758d > /sys/module/zx279133_switch/parameters/phydbg
echo 0x1f:0xa434 > /sys/module/zx279133_switch/parameters/phydbg

# Uni SerDes（offset[:value]，逗号分隔可批量写）
echo 0x50 > /sys/module/zx279133_uni_serdes/parameters/serdesdbg
echo 0x54 > /sys/module/zx279133_uni_serdes/parameters/serdesdbg
echo 0xcc > /sys/module/zx279133_uni_serdes/parameters/serdesdbg
echo 0xe4 > /sys/module/zx279133_uni_serdes/parameters/serdesdbg
```

现场排障捷径：在活板上直接写 vendor 1G 49 词 + `0x54=0x101`（不关时钟、不复位），
如果 `758d` 立刻从 `0x8018` 变 `0x801e` 且 ping 通，就说明问题在镜像/时序，
而不是 PHY 或 9132 端口配置。

---

## 8 仍未覆盖的边界

1. **内核不能替代 U-Boot 对 RTL8226 的初始化**（DTS 不挂 PHY 节点，内核
   只在 1G 档写一次 serdes option）。已做 A/B 冷启动验证：去掉 `bootcmd` 里的
   `ping` 后，2.5G 口起不来（详见 §10）。因此该板的 `bc0`/`bc1`/`bc2`
   仍保留 `ping` 以触发 U-Boot 的 eth init。
2. DSA `fixed-link` 仍声明 2500，实际速率由 `fixed_state` 上报；上层若直接读
   fixed-link 会看到不真实的 2500。
3. lan3/lan4 当前无电缆，只确认了 `NO-CARRIER` 状态未变，未做插线回归。

---

## 9 涉及文件

| 文件 | 作用 |
|---|---|
| `drivers/net/ethernet/zte/zx279133-switch.c` | probe 顺序、`outerphy_apply_speed()`、XPCS 2.5G profile、fixed_state、watchdog |
| `drivers/phy/zte/phy-zx279133-uni-serdes.c` | 1G/2.5G 49 词镜像、原地换档、PLL/CDR 等待 |
| `arch/arm64/boot/dts/zte/zx279133-sk-d840n.dts` | LAN1 = port 8、phy-mode `2500base-x` |
| `include/net/dsa_zx279133_rtl8372n.h` | 用户口范围 4..8 |
| `net/dsa/tag_zx279133_rtl8372n.c` | 去掉 port 9/VID 63 特例 |

---

## 10 U-Boot 依赖：断电 A/B 实测与 ping 耗时

### 10.1 测试方法

两条路径都在**断电冷启动**后执行，线固定在 2.5G 口，对比"是否经过 U-Boot 的
eth init"：

- **A（现状，`bootcmd` 含 `ping`）**：`bootcmd=mw.w 0x10d10080 0x0108;
  mw.w 0x10d1009c 0x0100; run ${bootstate}`，`bc0` 内含 `ping 192.168.5.112`。
  `ping` 的目的不是联网，而是**触发 U-Boot 的 eth bring-up**（`outerphy info init`
  → `xmac0 is used` → `mode_eth_1gbase_x_cfg` → 2500BASE-X 切换）。
- **B（去掉 eth init）**：在 `=>` 下直接

  ```text
  mw.w 0x10d10098 0x0100
  mtd read kernel 0x88000000
  mtd read dtb    0x88800000
  bootm 0x88000000 - 0x88800000
  ```

  完全不碰 U-Boot 网络栈，直接引导同一个内核。

### 10.2 结果

| 路径 | `1e:7580` | `1e:758d` | `1f:a434` | `lan1` carrier / speed | ping |
|---|---|---|---|---|---|
| A（含 `ping`） | `0x9416` | `0x801e`（送码） | `0x1a1c`（2.5G） | `1` / `2500` | 20/20 |
| B（直接 `bootm`） | `0x9412` | `0x8010`（bit2=0，**不送码**） | `0x1a1c` | `0` / `lowerlayerdown` | 不通 |

B 的内核日志本身是"成功"的：

```text
the 2.5G jack's PHY: system side at 2500 Mbps; programming the lane to match
2.5G profile written: mode 0x409
the 2.5G jack: lane programmed for 2500BASE-X (speed 6)
```

也就是说 **SoC 侧（Uni SerDes / XPCS / XMAC）已经按 2.5G 编好了**，
但 RTL8226 的系统侧仍停在 `0x9412`（`mode=0x12`，既不是 SGMII `0x02`
也不是 2500BASE-X `0x16`），且 `758d` bit2=0 表示 PHY **没有向 lane 送码**，
于是 PCS 收不到码流，carrier 起不来。

### 10.3 结论

1. 该板的 RTL8226B 在**上电后必须由 U-Boot 做一次完整初始化**
   （vendor `outerphy_phy_init` 序列，包括系统侧 mode 选择与送码使能）。
   内核当前对这颗 PHY 只做了 1G 档的一次 serdes option 写入（DTS 故意不挂
   PHY 子节点，避免 realtek phylib 覆盖 option），**无法替代** U-Boot 的初始化。
2. 因此 `bc0`/`bc1`/`bc2` 里的 `ping` **必须保留**，不能为了"少一次网络初始化"
   去掉。这不是遗留 hack，而是该板 RTL8226 bring-up 的必要步骤。
3. 若未来要让内核自足，需要把 vendor 的 `outerphy_phy_init`（系统侧 mode 选择 +
   送码 + 铜侧 AN 广告）完整移植到内核，并在 probe 里跑一次。本项目暂不做。

### 10.4 ping 耗时重测（推翻历史"34 s"）

历史记录里"ping 目标不可达会拖慢启动 34 s"的测量**不成立**，重新在 U-Boot 下实测：

| 目标 | 结果 | 实测耗时（`time` 计时） |
|---|---|---|
| `ping 192.168.5.112`（可达） | 正常应答 | **0.54 s** |
| `ping 192.168.5.99`（不可达） | `ping failed; host 192.168.5.99 is not alive` | **0.99 s** |

U-Boot 的 `ping` 只发一轮（默认 5 个包、超时 1 s 量级），一次失败即返回，
不存在 34 s 级别的等待。之前那次数值应为测量方式（包含人工操作 / 串口回放 /
多次重试）导致的偏差，与 `ping` 本身无关。

因此保留 `ping` 对启动时间的代价可以忽略（< 1 s），A/B 结论与耗时结论不冲突：
**保留 `ping` 是对的**。
