# 补一百零九：switch.ko init 离线差集（task #42 / G1 门）

写于 2026-10-21。设备：SK-D840N（ZX279133，GPON ONU）。读者：接着做 G2（9132 最小驱动）的会话。

本文件是 `PLAN-immortalwrt-new-kernel-20261020.md` §4.1（W1）的产出：**把"mainline 少了什么"从猜测变成一张可以照着写的表**。
全部结论来自离线反汇编 + 一次只读活板取证，**没有 build、没有 push、没有写 flash、没有改板子配置**。

---

## 0. 门判据与结论

| G1 硬门 | 结果 |
|---|---|
| 正例控制通过（`np_133.ko idm_net_lan_tx_direct` 看到 `0x11dd8` store） | **PASS**（`00011dd8  str w5, [x0, #0xa4]`） |
| 差集表非空 | **PASS**（14 类寄存器写，mainline 一条都没有） |
| 至少一条差异能解释"9132 的端口没有 bring-up" | **PASS**（端口使能寄存器 + 整个 0x013 端口属性字，mainline 从未写过） |
| API 实现在别的模块时，差集必须包含那个模块并标明是哪一版 | **PASS**（`np_133.ko`，md5 `92b68212542930114769e3c69efe64b6`） |

**G1 通过。可以进 G2。**

---

## 1. 参与分析的模块身份

| 模块 | md5 | 说明 |
|---|---|---|
| `pon/vendor-modules/switch.ko` | `993f012407847954805d89efaf51110d` | **与板上一致**（板上 `/lib/modules/4.19.136+/switch.ko` 实测同 md5） |
| `pon/vendor-modules/np_133.ko` | `92b68212542930114769e3c69efe64b6` | **本轮新增的关键模块**：9132 寄存器写实现层在这里 |
| `pon/vendor-modules/netdriver.ko` | `cfcf64947d4daba25a1425fb56dc9753` | 厂商 netdev 层（`sw`/`pon`/`eth0..3`） |
| `staging/vendor-full/kmodule/switch.ko` | `fdeba014b9c8cddcfc28eaac9700bca0` | **另一版，本轮不使用**（拿它做差集会得到错的表） |

工具：`tools/ponelf.py`、`tools/ponDis.py`、`staging/kofn.py`、`staging/kocalls.py`、`staging/swspa.py`；
本轮新增 `scratch/finddef.py`、`scratch/secstr.py`、`scratch/dumptable.py`、`scratch/fieldids.py`、`scratch/attrbits.py`。
原始输出：`staging/ctl/switchko-init-20261020.txt`（`tools/dump-switchko-init.ps1` 生成）。

---

## 2. 初始化入口在哪：三层调用链

`switch.ko` 的 `init_module`（0x84c，144 字节）**是 0 调用的桩**，不要从它展开。
真正的初始化入口是 **`sw_init_switch`（0x2e74）**，它的调用表（`staging/kocalls.py` 解 `.rela.text`）：

```
sw_reg_set_onu_swcap / tm_acl_l2_fast_init / l3_hardfastReg / sw_alarm_init / sw_qos_init
→ sw_init_port_protocol_action
→ sw_set_default_vlan_check_ctrl
→ sw_init_port_isolate
→ sw_init_mac_lookup_ctrl
→ sw_init_flood_ctrl
→ sw_init_omci_trap_qid_init
→ sw_init_port_route_mode
→ sw_reg_set_onu_swcap
→ sw_mac_table_op_init
→ sw_multi_config_init
```

这 10 个 `sw_init_*` 里的 API **全部是 UND（import）**，`switch.ko` 自己不实现它们：

- `zxic_api_sw_*` / `tm_*` → **定义在 `np_133.ko`**
- 端口号映射 `sw_port_change_to_chip_port`（0x1148ac）也在 `np_133.ko`

**结论：9132 的寄存器写实现在 `np_133.ko` 里，和 mainline 的 `zx279133-np.c` 是同一份代码的两个版本。**
这正是"差集"能成立的前提——两边用的是同一个寄存器窗口。

---

## 3. 关键使能发现：间接寄存器编码两边完全一致

厂商 `np_133.ko` 的 `spa_set_indirect_rw_cmd(op, mem_id, addr)`（0x22328）：

```asm
cmp  w20, #0x40        ; mem_id <= 64
mov  w0, #0x1ff
ccmp w22, w0, #2, ls   ; addr   <= 0x1ff
ccmp w21, #1, #2, ls   ; op     <= 1
add  w1, w22, w20, lsl #20   ; addr | (mem_id << 20)
add  w1, w1, w21, lsl #27   ; | (op << 27)
```

mainline `zx279133-np.c` 的 `__zx279133_spa_indirect_write()`：

```c
writel((readl(eth->base + ZX279133_SPA_IND_CMD) & ~ZX279133_SPA_IND_CMD_MASK) |
       mem_id << 20 | addr, eth->base + ZX279133_SPA_IND_CMD);
writel(value, eth->base + ZX279133_SPA_IND_DATA);
```

**逐位相同**（`op` 在 mainline 是 `ZX279133_SPA_IND_READ` 常量）。
也就是说：**mainline 现有的 `zx279133_spa_indirect_write/read(eth, mem_id, addr, value)` 就是厂商写 9132 的那把锤子**，
不需要新驱动、不需要 DSA、不需要 phylib。

**`mem_id` 就是端口号**：`do_spa_port_attr_tbl_get/set` 的参数是 `(port, addr)`，
`spa_set_indirect_rw_cmd(op, port, attr_addr)`。所以"给某个 9132 端口写某个属性"在 mainline 里就是
`zx279133_spa_indirect_write(eth, port, attr_addr, value)`。

---

## 4. 9132 的每端口属性寄存器

`np_133.ko` 的 `spa_port_attr_table`（.data @0x1978，0x4a0 字节 = **74 项 × 16 字节**）：
每项 = `[+0 名称 | +4 mask | +8 shift | +12 addr]`。
`spa_port_attr_tbl_set(value, port, field_id)` 的语义：

```
new = (old & ~(mask << shift)) | ((value & mask) << shift)
旧值/新值都走 SPA indirect: mem_id = port, addr = DESC[field_id].addr
```

| field | mask | shift | addr | 内容 |
|---|---|---|---|---|
| 0 | 0x1 | 0 | 0x000 | 端口使能（8bit 寄存器的 bit0） |
| 1 | 0x3fff | 1 | 0x000 | |
| 2 | 0x1 | 30 | 0x000 | |
| 3 | 0x3f | 15 | 0x000 | |
| 4 | 0x7 | 21 | 0x000 | |
| 5 | 0x3f | 24 | 0x000 | |
| 8 | 0x7 | 0 | 0x001 | |
| 9 | 0x1ff | 3 | 0x001 | |
| 10 | 0x3 | 12 | 0x001 | pkt filter |
| 11 | 0x1ff | 14 | 0x001 | |
| 12 | 0x1 | 23 | 0x001 | |
| 13 | 0x1 | 24 | 0x001 | |
| **14** | **0x7** | **25** | **0x001** | **默认 VLAN 优先级（PVID pri）** |
| **16** | **0xfff** | **18** | **0x002** | **上游 untag SVID（PVID vid）** |
| 17 | 0x3 | 30 | 0x002 | |
| 19 | 0xffffff | 0 | 0x004 | |
| 20–23 | 0xffffffff | 0 | 0x005–0x008 | |
| 25–41 | 各种 | | 0x009 | VLAN filter / flood group id 等 |
| **42** | **0x0fffffff** | **0** | **0x00a** | **mainline 已写**（`0x0a00000c`） |
| 43 | 0x7 | 28 | 0x00a | |
| 44–47 | | | 0x00b–0x00c | |
| 48–55 | 0x7 ×3bit | 0,3,…,21 | 0x00d | TPID/priority 选择（mainline 已写 0x8008） |
| 56–63 | 0x7 ×3bit | 0,3,…,21 | 0x00e | |
| 64–67,72 | 0x7 ×3bit | | 0x00f | |
| 68 | 0xffffffff | 0 | 0x010 | |
| 69 | 0xffffffff | 0 | 0x011 | brd flood num / brd flood group id |
| 70 | 0xffffffff | 0 | 0x012 | mul / unk flood group id |
| **71 (0x47)** | **0xffffffff** | **0** | **0x013** | **端口属性字（见 §5）** |
| 73 | 0x1 | 28 | 0x001 | |

### §5 端口属性字（addr 0x013）的位定义

厂商的 `port_attr_def_*_set()` 全部用 field 0x47（整字读写），各自在 4 字节缓冲
`[x29+0x44..0x47]` 里改一个 bit（`scratch/attrbits.py` 提取）：

| 属性 | 字节 | 位 | 绝对 bit |
|---|---|---|---|
| `unk_da_flood_num` | 0 | 0–5 | 0–5 |
| `route_mode_en` | 1 | 5 | **13** |
| `flag_success_sfu_prcecss` | 1 | 6 | 14 |
| `flag_success_8021x` | 1 | 7 | 15 |
| `en_8021x` | 2 | 0 | 16 |
| `mirror_en` | 2 | 1 | 17 |
| `input_vlan_check_en` | 2 | 2 | **18** |
| `src_is_extend_port_flag` | 2 | 3 | 19 |
| `unk_unida_ratelimit_en` | 2 | 4 | 20 |
| `unk_sa_ratelimit_en` | 2 | 5 | 21 |
| `brd_ratelimit_en` | 2 | 6 | 22 |
| `mul_ratelimit_en` | 2 | 7 | 23 |
| `ratelimit_en` | 3 | 0 | 24 |
| `brd_flood_en` | 3 | 1 | **25** |
| `mul_flood_en` | 3 | 2 | **26** |
| `isolate_en` | 3 | 3 | **27** |
| `da_lookup_en` | 3 | 4 | **28** |
| `mac_limit_full_flag` | 3 | 5 | **29** |
| `sa_lookfail_drop_en` | 3 | 6 | 30 |
| `sa_lookup_learn_en` | 3 | 7 | **31** |

---

## 6. 厂商 `switch.ko` init 的完整写集

下面每一行都是 `staging/ctl/switchko-init-20261020.txt` 里反汇编出来的**实参**，
不是推测。`p` = switch 端口号（0..3 = 9132 UNI 口，4 = 外挂 RTL8226，0x10..0x30 = 扩展口）。

| init 函数 | 调用（实参） | 落到 9132 的哪一笔 |
|---|---|---|
| `sw_set_default_vlan_check_ctrl` | `tm_vlan_check_ena_set(p,0,1)`、`tm_vlan_check_ena_set(p,1,1)`（p=0..7, 0x10..0x30） | addr 0x013 bit 18（input_vlan_check_en）+ 同族字段 |
| | `zxic_api_sw_vlan_set_port_vlancfg(p, 0, 0xfff, 0)` | addr 0x002 field 16/17 |
| | `tm_vlan_check_ena_set(5,1,0)`（xmac0_to_extend）、`(6,1,0)`（xmac1_to_extend） | 同上 |
| `sw_init_port_isolate` | `zxic_api_sw_set_port_isolate_enable(p,0)`，p=0..0x3f | addr 0x013 bit 27 = 0 |
| | `zxic_api_sw_port_set_port_isolate(p,q,0)`（p≠q）/ `(p,p,1)`，p,q=0..0x3f | 隔离矩阵 |
| `sw_init_mac_lookup_ctrl` | `zxic_api_sw_set_sa_lookup_learn_en(p,1)`（p=0..7, 0x10..0x30） | addr 0x013 bit 31 = 1 |
| | `zxic_api_sw_set_mac_limit_full_flag(p,0)` | addr 0x013 bit 29 = 0 |
| | `zxic_api_sw_set_da_lookup_en(p,1)`（p=0..7, 0x10..0x30） | addr 0x013 bit 28 = 1 |
| | `zxic_api_sw_set_da_lookup_en(7,0)`（rgmii_to_extend） | addr 0x013 bit 28 = 0（端口 7） |
| `sw_init_flood_ctrl` | `zxic_api_sw_port_{mul,unk_da,brd}_flood_dest_port(wan,0,&mask)`；非 WAN 口 `{...}_flood_en(p,1)` | addr 0x013 bit 25/26 + addr 0x011/0x012 |
| `sw_init_omci_trap_qid_init` | `zxic_api_sw_reason_map_cpu_qid_set(0,0,7)` | reason 0 → CPU qid 7 |
| `sw_init_port_route_mode` | `tm_port_route_mode_set(p,0)`，p=0..0x40 | addr 0x013 bit 13 = 0 |
| `sw_reg_set_onu_swcap` | 只写 `.bss`（ONU swcap 结构），**无硬件写** | — |
| `sw_mac_table_op_init` | 只注册 mac ops | — |
| `sw_multi_config_init` | `zxic_init_igmp_list` + 注册组播回调 | — |
| `sw_init_port_protocol_action` | p=1..7 与 0x10..0x30 的协议动作 | — |

**端口使能（`zxic_api_sw_port_set_port_enable`）不在 `switch.ko` 的 init 里**，
它在 `switch.ko` 内部只被 `sw_port_set_port_enable` 引用，调用方在 netdev 打开路径
（厂商 `netdriver.ko`）——即**端口使能是"网口 up 时才写"的，不是开机写的**。
这解释了为什么它不在上表里：G2 要在 netdev `open` 时写它。

---

## 7. 差集表（mainline 缺什么）

mainline 现状：`zx279133-np.c` 的 `zx279133_spa_port_attr_init()`（2179 行）对 **mem_id 0..63** 只写三笔：

```
(mem_id, 10) ← (old & ~0x0fffffff) | 0x0a00000c      /* field 42 */
(mem_id,  1) ← (old & ~0x00000fff) | attr8 | 0x111<<3 /* field 8,9 */
(mem_id, 13) ← (old & ~0x00ffffff) | 0x8008           /* field 48..55 */
(15,       13) ← 0x8008
(mem_id 49..54, 0) ← (old & ~0x7<<21) | 7<<21         /* field 4 */
```

这是 `np.ko spa_init()` 的忠实移植（mainline 注释已写明）。**它没有覆盖 9132 端口属性的其余部分。**

| # | 9132 寄存器 | 厂商 init 写的值 | mainline | 差集？ | 解释的症状 |
|---|---|---|---|---|---|
| D1 | **端口使能** addr 0x000 bit0（8bit 寄存器） | 1（网口 up 时写） | **无** | **差** | 端口在 mainline 下不出现/不通 |
| D2 | **0x013 bit 27** isolate_en | 0（全部 64 口） | 无 | **差** | 隔离行为不对 |
| D3 | **0x013 bit 31** sa_lookup_learn_en | 1（口 0..7, 0x10..0x30） | 无 | **差** | 学不到 MAC |
| D4 | **0x013 bit 28** da_lookup_en | 1（口 7 在 rgmii_to_extend 下为 0） | 无 | **差** | 目的 MAC 查表关着 ⇒ 单播不通 |
| D5 | **0x013 bit 29** mac_limit_full_flag | 0 | 无 | **差** | |
| D6 | **0x013 bit 25/26** brd/mul flood_en | WAN 口 0，其余 1 | 无 | **差** | 广播/组播不通 |
| D7 | **0x013 bit 13** route_mode_en | 0（口 0..0x40） | 无 | **差** | 路由模式 |
| D8 | **0x013 bit 18** input_vlan_check_en | 1 | 无 | **差** | VLAN 检查关着 |
| D9 | **addr 0x001 field 14（bit 25..27）** PVID pri | `tm_port_pvid_set` | 无 | **差** | 没有 PVID ⇒ 端口不收标签帧 |
| D10 | **addr 0x002 field 16（bit 18..29）** 上游 untag SVID | `tm_port_pvid_set` | 无 | **差** | 同上 |
| D11 | **addr 0x002 field 17** vlancfg | 0xfff / 0 | 无 | **差** | |
| D12 | **addr 0x011 / 0x012** flood num / group id | 每口一组 | 无 | **差** | 泛洪目标端口表 |
| D13 | 隔离矩阵（p↔q 64×64） | 仅自隔离 | 无 | **差** | |
| D14 | reason→CPU qid 映射 | reason 0 → qid 7 | 无 | **差** | 上送队列号 |

**差集非空（14 条），且 D1/D2/D3/D4 全部落在"端口不 bring-up"这一类上。G1 的解释要求满足。**

---

## 8. 活板取证（只读，2026-10-20 23:5x–2026-10-21 00:0x）

只用了 `*_get` 读接口（`sw_port_enable_get` / `sw_port_linkstate_get` / `sw_port_linkmode_get` /
`sw_port_isolate_get` / `sw_vlan_port_pvid_get`），输出落 dmesg。**没有写任何 setter，没有挪线，没有断电。**

`/proc/pon/tm_debug/` 逐口读回来的厂商 9132 端口表：

| sw_port | enable | link | speed | duplex | 厂商认领 |
|---|---|---|---|---|---|
| 0 | 1 | 0 | 0 | 0 | smac0 |
| 1 | 1 | **1** | 2 | 1 | smac1 —— **本机 PC 插的就是这个口** |
| 2 | 1 | 0 | 0 | 0 | smac2 —— eth2（udhcpc 在重试，rx 恒 0） |
| 3 | 1 | 0 | 0 | 0 | smac3 —— eth3（DOWN） |
| 4 | 1 | 0 | 0 | 0 | `outerphy_mode=5, outerphy_name=phy_RTL8226` —— **eth0 / jack 4** |
| 5 | error(smac) | 1 | 6 | 1 | 宿主侧口（speed 6） |
| 6 | error(smac) | 1 | 6 | 1 | 宿主侧口 |
| 7 | error | — | — | — | `sw_port(7) is not unvalid smac` |

所有口 `isolate enable = 0`（与 `sw_init_port_isolate` 写 0 一致）。

厂商板上的 netdev 拓扑（`/sys/class/net/*`）：

| netdev | ifindex | iflink | MAC | flags | 角色 |
|---|---|---|---|---|---|
| `sw` | 5 | 5 | d0:45:8d:84:08:fc | 0x1003 | **9132 CPU 口**，全板流量走它（rx 259636 / tx 230458） |
| `pon` | 6 | 6 | 00:d0:d0:61:62:00 | 0x1002 | PON MAC（DOWN，未注册） |
| `idm0` | 7 | 7 | d0:45:8d:84:08:fe | 0x1003 | SoC IDM/XMAC |
| `idm1` | 8 | 8 | d0:45:8d:84:08:fd | 0x1003 | SoC IDM/XMAC |
| `eth0` | 10 | 10 | d0:45:8d:84:08:ff | 0x1003 | rgmii / RTL8226 = **jack 4** |
| `eth1` | 11 | 11 | d0:45:8d:84:09:00 | 0x1303 | **9132 smac1**，在 br-lan，rx 229614 / tx 186544 |
| `eth2` | 12 | 12 | d0:45:8d:84:09:01 | 0x1003 | 9132 smac2，rx 0 / tx 41260 |
| `eth3` | 13 | 13 | d0:45:8d:84:09:02 | 0x1002 | 9132 smac3，DOWN |

`netdriver.ko` 的两个母设备名（`.rodata.str1.1` @0x508 / @0x50b）：**`"sw"`** 与 **`"pon"`**。
`ethdrv_add_br_device(port, flags, name)`：flags bit0 置位 → 母设备 `"pon"`（WAN）；否则 `"sw"`。

**端口号映射**：`sw_port_change_to_chip_port(p)`（`np_133.ko` 0x1148ac）= **p+1（p=0..6）**，
即 switch 口 0..6 → chip/PPU 口 1..7。这和 mainline `zx279133-np.c` 注释里的
"`getPort(5) = 6`"一致：mainline 的 **PPU 6 = switch 口 5**。

**结论：9132 的 UNI 口 = switch 口 0..3 = PPU/SPA mem_id 1..4**（芯片口 1..4），
外接 RTL8226 = switch 口 4 = 芯片口 5，宿主侧 = 芯片口 6..7。

---

## 9. 对 G2 的直接结论

1. **不用写新驱动框架。** mainline 的 `zx279133_spa_indirect_write/read(eth, mem_id, addr, value)`
   就是厂商写 9132 的原语，编码逐位相同。G2 只需要**补一批具体的 (mem_id, addr, value) 写**。

2. **需要写的三组**：
   - 开机一次：0x013 端口属性字（isolate/learn/flood/route-mode）、0x001/0x002 的 PVID 与 VLAN 检查、
     0x011/0x012 泛洪目标表、reason→qid 映射（对应 D2..D14）。
   - netdev `open` 时：**端口使能 addr 0x000 bit0 = 1**（D1）。
   - 关机/`stop` 时：端口使能清 0。

3. **mainline 已经有 4 个逻辑 LAN 口（vport）的框架**：
   `ZX279133_LAN_TRANSPORT_VID_MIN..MAX` = 59..62，`zx279133_lan_vport_mf[]` 四条 MF 表，
   `zx279133_lan_vport_wan_set(eth, vport, wan)` 可把任一 vport 切成 WAN。
   **G2 的 4 个 9132 UNI 口应当映射到这 4 个 vport（59..62）**，而不是新造一套 netdev 体系。
   这是本轮最有价值的一条：G3（一只口当 WAN）需要的机制，mainline 里已经写好了。

4. **不要碰 `zx279133_lan_port_bringup()`（mem 5/10/15）**——那是 SR1010 外挂 RTL8372N 的路径；
   本板对应的是 9132 内部 UNI（mem 1..4）。交接书 §4.0 已经写明这一点，本轮独立复核确认。

---

## 10. 复现命令

```bash
# 正例控制
python staging/kocalls.py pon/vendor-modules/np_133.ko idm_net_lan_tx_direct | grep 11dd8

# init 入口与调用表
python tools/ponelf.py pon/vendor-modules/switch.ko funcs 'init|probe|sw_init'
python staging/kocalls.py pon/vendor-modules/switch.ko sw_init_switch

# API 实现在哪个模块
python scratch/finddef.py '^zxic_api_sw_|^tm_port_route_mode_set$'

# 间接寄存器编码
python staging/swspa.py pon/vendor-modules/np_133.ko dis spa_set_indirect_rw_cmd

# 描述符表 / 位定义
python scratch/dumptable.py pon/vendor-modules/np_133.ko .data spa_port_attr_table 74
python scratch/attrbits.py port_attr_def_isolate_en_set port_attr_def_da_lookup_en_set ...

# 原始反汇编
pwsh tools/dump-switchko-init.ps1     # -> staging/ctl/switchko-init-20261020.txt
```

---

## 11. 下一门（G2）的判据建议

- G2 的成功标志不是"eth1/2/3 出现在 `/sys/class/net`"这么弱——**厂商的 `sw` 才是 CPU 口**，
  mainline 的单网口是 PPU 5（= switch 口 4 = RTL8226）。
  **G2 的正确判据是：4 个 vport（59..62）各自的 MF/PRO 表就位 + 9132 的 UNI 口（switch 0..3）
  端口使能与属性字写对 + 至少一只 9132 UNI 口双向收发。**
- 建议的实现落点：`ci-build-repo/kernel-overlays/drivers/net/ethernet/zte/zx279133-9132.c`
  （新文件），或直接在 `zx279133-np.c` 里加一个 `zx279133_9132_port_init()`。
  **不要动 `zx279133_lan_port_bringup()` 的 RTL8372N 路径。**
