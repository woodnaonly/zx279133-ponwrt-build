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

---

## 11 B 方案实测：把 `ping` 放回 `bootcmd`（2026-11-01）

§10 的结论是「`ping` 必须保留」，但当时**没有改 flash 里的 env**：
community `boot.bin` 把厂商那套 `bc0`/`bc1`/`bc2` + `bootstate` 整体换成了
`bootcmd=mtd read kernel ...; mtd read dtb ...; bootm ...`，
那条路径里**没有任何网络命令** ⇒ 冷启动时 U-Boot 从不初始化 RTL8226。
这一节把这一步补上，并给出实测。

### 11.1 症状（另一会话的只读取证，结论已复核）

冷启动后 `1e:758d = 0x0010`（bit2=0，不送码），
PCS/AN status 的四个 bit 只有 `0x0029`（缺 0x40），
铜侧协商正常（link up / 1 Gbps）但**帧一个都进不来**：
PC 侧 ping `192.168.9.1` 100% 丢包，板上 `lan1 rx=0`、`lan-cpu0 rx=0`。

### 11.2 动手前必须查清的三件事（都查清了，且都是活板实测）

**① env 落在 NAND 哪里。**
`mtdparts` 只有六项（whole/boot/kernel/dtb/parameter tags/root），没有 env 分区；
内核 `/proc/mtd` 也看不到 env（boot/kernel/dtb/parameter tags/root/scratch）。
这版 U-Boot（2021.01-svn308776）的 `bdinfo` **不打印 env 位置**
（`flashstart=0`、`flashsize=0`），`env` 命令也没有 `location` 子命令。
⇒ 只能板上实测：

```
=> mtd read boot 0x89000000 0x180000 0x20000
Reading 131072 byte(s) (64 page(s)) at offset 0x00180000
=> crc32 0x89000004 0x1fffc
crc32 for 89000004 ... 8901ffff ==> 3c568477
```

`0x180000` 处第一个字就是 `3c568477`（LE），后面紧跟
`baudrate=115200\0bootargs=...\0bootcmd=mtd read kernel 0x88000000; ...\0`。
⇒ **env = mtd1 "boot" 内的 `0x180000..0x1a0000` 这一个 128 KiB 擦除块**，
与 docs/01 §6.5 / docs/03 §5.1 记的 community env 一致（CRC 都是 `3c568477`）。

**② 这个块会不会压到 U-Boot 本体。**
community `boot.bin` 的前 1.5 MiB（`0x0..0x180000`）是 U-Boot 本体，
env 块紧接在它后面，**不重叠**。`saveenv` 的实测擦除日志也印证了这个边界：

```
Saving Environment to NAND... Erasing NAND...
Erasing at 0x180000 --  33% complete.
Erasing at 0x1a0000 --  66% complete.
Erasing at 0x1c0000 -- 100% complete.
Writing to NAND... OK
```

（它按扇区擦除 `0x180000..0x200000`，但 env 数据只落在第一块 `0x180000..0x1a0000`。）

**③ 写坏了能不能救回来。** 能，两条退路：
- env CRC 坏了 ⇒ U-Boot 丢弃 env、改用默认 env（**默认 env 没有分区表**，
  docs/01 §6.4）⇒ 板子起不来 Linux，但 **U-Boot 提示符与固件本体都还在**，
  串口在就能重写 —— 这与「mtd1 整体损坏」那种真砖不是一回事；
- 写之前先把 env 块**逐字节**备份到 PC（见 §11.3）。

### 11.3 备份：逐字节精确

按 docs/01 §6.5 的配方在 PC 上重建整个 128 KiB 块
（`[u32 LE crc32][name=value\0 …][0x00 padding]`，CRC 取 `buf[4:0x20000]`），
内容用板上 `printenv` 的 17 个变量，重算 CRC = `0x3c568477`，
与板上存储值**逐位相同** ⇒ 重建块与板上原块**逐字节一致**。

- 备份：`staging/trial/env-orig-0x180000.bin`（131072 B）
- 回滚：TFTP 拉回 RAM，`mtd erase boot 0x180000 0x20000;
  mtd write boot 0x89000000 0x180000 0x20000`
- 写入前的 permit 行：`staging/ctl/flash.permit`（`run=envsave`）

### 11.4 写进去的三行

```
setenv bootcmd 'ping 192.168.9.25; mtd read kernel 0x88000000; mtd read dtb 0x88800000; bootm 0x88000000 - 0x88800000'
setenv ipaddr 192.168.9.1
setenv serverip 192.168.9.25
saveenv
```

- `bootcmd` 前面加一次 `ping`：**目的只是触发 U-Boot 的 eth bring-up**，不指望它通
  （§10.4 实测失败也只要 0.99 s）；
- `ipaddr`/`serverip` 从厂商遗留的 `192.168.1.x` 改成台面在用的 `192.168.9.x`
  （`tools/ubflash.sh` 每轮本来就在运行时设这两个，改成持久值不改变流程）。

**写完当场对账**：板上再读一遍同一块，再算一次 CRC：

```
=> mtd read boot 0x89000000 0x180000 0x40000
=> crc32 0x89000004 0x1fffc
crc32 for 89000004 ... 8901ffff ==> ff04fcb9
```

`ff04fcb9` **等于** PC 侧按预期内容算出来的 CRC ⇒ 板上那块就是要的那一块。

### 11.5 冷启动实测：U-Boot 这一段现在对了

**插座断电再上电**（19:08:04 那一轮，真正的冷启动）的 U-Boot 段：

```
Loading Environment from NAND... OK
...
enter outerphy info init!
get board_info_id by fdt,the phy_id is 0x5
--->phy identifier is [0xc849]        <- 认到 RTL8226
xmac0 is used.
...
mode_eth_1gbase_x_cfg
xmac 0 speed set 2 in sgmii(xmac speed 3)
mac 4 phy status changed: 1000M full-duplex
host 192.168.9.25 is alive             <- PC 真的应答了 ping
```

⇒ **U-Boot 阶段的 eth bring-up 完整跑通，PC 真的回了 ping** ——
这正是 §10 那个 A/B 里「跑过 outerphy init」那一列的形态。
（18:56:47 那轮用 `reset` 重启，形态完全一样。）

板侧读数的前后对照（本板实测，同一份内核，只差 env）：

| 启动轮次 | `1e:758d` | PCS 四个 bit | 说明 |
|---|---|---|---|
| 17:46:04 冷启动 | **0x0010** | 0x0029 | U-Boot 没初始化 PHY，lane 不送码 |
| 18:06:34 冷启动 | **0x0010** | 0x0029 | 同上 |
| 18:27:04 冷启动 | **0x0010** | 0x0029 | 同上 |
| 18:56:47（改 env 后，`reset`） | **0x8018** | **0x0069**（@2.65 s，全在） | 继承到 U-Boot 的状态，lane 有码 |
| 19:08:04 冷启动（改 env 后） | **0x8018** | **0x0069**（@2.65 s，全在） | 同上 |

### 11.6 但 Linux 里还是不通 —— 根因是**两个**，不是一个

内核起来后 lane 又被打回去（19:08:04 那一轮冷启动）：

| 时刻 | `1e:758d` | 四个 bit | 说明 |
|---|---|---|---|
| 2.65 s | 0x8018 | **0x0069**（全在） | 继承 U-Boot 的状态，lane 有码 |
| 4.71 s | 0x8018 | 0x0029（缺 0x40） | 被打回（紧接着 4.712 s 是 attempt 1/3 的 AN 重启） |
| 41.03 s | 0x8018 | 0x0069 | AN 重启窗口内又全在 |
| 43.11 s | 0x8018 | 0x0029 | 又被打回 |
| 247 s（18:56 轮，活板直读） | **0x8018** | 0x002d | 终态：PCS 缺 0x40 |

PC 侧仍然 100% 丢包。

⇒ **另一会话给的根因（「U-Boot 从不初始化 RTL8226」）是必要条件，不是充分条件。**
U-Boot 现在把 PHY 配好了，可**主线驱动随后用自己的 1G class SerDes 编程
和 AN 重启循环把它覆盖掉**：

```
the 2.5G jack's PHY: auto-negotiation restarted (attempt 2/3, AN control was 0x3000, class 0x9402, lane word 0x8018)
the 2.5G jack's PHY: auto-negotiation is complete (status 0x0049) after 1800 ms
```

每重启一次 AN，PCS 就掉一次 0x40。

另外与「好会话」的 `0x801e` 比，现在内核读到的是 `0x8018`（差 bit1）；
那个好会话的内核打印的是
`the 2.5G jack's MAC (XMAC0) runs at 1G; the loader's TX_CTRL speed field was 3`
与 `lan5:`，**当前驱动打印的却是** `lane pre-serdes:` 与 `lan1:`。
⇒ 那次 A/B 的两侧**不是同一份驱动代码**，「同一内核、同一驱动」这个前提本身不成立
（内核版本串相同，但驱动补丁不同）。

⇒ **下一刀在驱动侧**：内核必须保持 U-Boot 留下的 base-X image
（`0x7580=0x9402`、PCS 四个 bit 全在），而不是自己再写一遍 1G class 的
SerDes option 再做 AN 重启。这正是 §10.3 第 3 条预留的那件事
（「把 vendor 的 `outerphy_phy_init` 完整移植到内核」）。

### 11.7 这一轮的账

**做了**：flash 里的 env 补上 `ping`（一处 128 KiB 块，可回滚）；
U-Boot 阶段从「PHY 根本没被初始化」变成「完整初始化，且 PC 应答 ping」。

**没做**：内核侧那一刀（要改驱动代码，走 CI + 刷机）；
**2.5G 口在 Linux 里仍然不通。**

**代价**：每次冷启动多一次 ping（§10.4 实测 < 1 s）。

**回滚**：`staging/trial/env-orig-0x180000.bin`（逐字节备份），
TFTP 写回 `mtd1+0x180000` 即恢复原 env。
