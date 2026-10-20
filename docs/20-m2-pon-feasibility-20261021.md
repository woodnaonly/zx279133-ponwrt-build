# 补百二十：M2（PON/OMCI）可行性——厂商状态机在，但 OMCI 的发送门是厂商自己留的空桩

日期：2026-10-21，离线反汇编（`pon/vendor-modules/*.ko`，18 个模块，全部 AARCH64）。
工具：`tools/ponDis.py`、`tools/ponelf.py`、`staging/kocalls.py`（capstone 5.0.7）。
本文件是计划里 G5 的交付物：**可行性报告 + 决策**。结论先说，再给证据。

---

## 0. 结论与决策

**M2（可用光猫）在本轮不可交付，冻结为"研究"；M1（实验室路由器）继续做完。**

理由不是"难"，是三件具体的事：

1. **厂商的 G.984 PLOAM 状态机是完整的**，就在 `zte_xgpon.ko` 里（O0–O7 八个状态、
   一整套 ITU-T 消息名与处理函数）。所以"移植状态机"这条路**技术上存在**。
2. **但厂商自己的 OMCI 发送门是空桩**：`zte_xgpon.ko` 的
   `fi_Configure_Ponmac_Send_Omci_Msg` 的全部内容是
   `mov w0, #0 ; ret`（`.text+0x5e4c`，8 字节）。
   厂商的固件**从来没有把 OMCI 发出去过**——它把 OMCI 的发送做成了"永远成功但什么都不做"。
   ⇒ **没有一条"能通的 OMCI 通路"可以借鉴**，得从零写。
3. **mainline 侧连 PON MAC 都没有**：树里没有 `zte,zx279133-pon` 节点、没有 0x17000000 的
   寄存器窗口、没有 PLOAM、没有 GEM/T-CONT、没有 OMCI。树里只有 PON *数据通路*（PPU mcode、
   ONU MAC 写、SPA/MF/SMMU 表）。

⇒ **决策**：G5 的交付物 = 本报告（M2 冻结为"研究"）。M2 若要继续，只能是
**"在 mainline 从零写一个 G.984 GPON ONU"**（PON MAC 驱动 + PLOAM 状态机 + GEM/T-CONT +
最小 OMCI），这是一个独立的多月工程，**不并入本轮主线**。理由与计划 §5 的回退条款一致：
"PON 状态机工作量过大 → M2 可行性报告"。

**不要把产物叫"可用光猫"。**（计划 §5 明写。）

---

## 1. 厂商 PON 栈的分工（实测）

| 活 | 谁干 | 证据 |
|---|---|---|
| PON 网口 `pon/omci/oam/sw/idm0/idm1/olt` | **np_133** | `idm_net_init`（np_133.ko `.text+0x18900`）；`.text` 里 `+0x18940/48`→`"omci"`、`+0x18968/70`→`"oam"`、`+0x1899c/a0`→`"sw"`、`+0x189c4/cc`→`"pon"`、`+0x189e8/f0`→`"idm0"`、`+0x18a0c/14`→`"idm1"`、`+0x18a40/48`→`"olt"`，每条后面跟着 `"failed to register <X> net"` |
| PON MAC / SoC 平台 | np_133 | 字符串 `zte,zx279133-pon`、`-nppt`、`-idm-intr`、`-xmac0-pcs`、`-pon_serdes`、`-uni_serdes`、`-gephy-apb`、`-rgmii`、`-pcu`、`-pps`、`-ipsec`、`-bmu-ddr`、`-se-ddr`、`-woe-ddr`、`zxic,apb-timer0/2` |
| **PLOAM / O 状态机 / 上下电状态机** | **zte_xgpon** | §2 |
| ONU 适配层、ioctl 适配、两个线程 | gpondrv_xg | `gpondrv_task_thread`（`.text+0xca8`）、线程名 `"gpondrv_task"`/`"gpondrv_scan_thread"`；`gpondrvCfgXGRegInfoAdapter`（`0x2858`）、`gpondrvCfgO7StateAdapter`（`0x2b58`） |
| **OMCI 报文层 / MIB / TX 队列** | **没有实现** | §3 |
| 以太网/桥胶水、光路 | netdriver / switch / wan_bridge / optical+GN28L9x+bspdriver | `pdt_ethdriver_init`（`0xda4`）、`ethdrv_add_br_device`（`0xfff`） |

`zte_xgpon` **不 import 任何网口注册符号** ⇒ 它自己一个网口都不注册。

## 2. PLOAM 状态机：前提被推翻，但状态机是真的

计划/交接里写的 "LOS/HLOAM/SER/STATE/RANGING/OPERATIONAL" 这套名字**不存在**：
18 个模块的字符串穷举里没有 `HLOAM`、没有 `OPERATIONAL`，`LOS` 只出现在 switch.ko 的告警串
（`"alarmtype(1-AUTONEG FAIL 2-PORT LOS ...)"`）和 zte_xgpon 的 `"O1 Send LOS OFF Msg Failed ret=%d"`。

**真实的名字是 ITU-T G.984 的 O 状态**，全在 `zte_xgpon.ko` 的 `.rodata.str1.1`：

- O 状态：`'O0' 'O1' 'O2' 'O2-3' 'O4' 'O5' 'O6' 'O7'`（`+0x24c2..+0x24d9`），
  小写 `'o0'..'o7'`（`+0x4059..+0x4070`）。
- PLOAM 消息/事件名（`+0x4073..+0x4311`）：
  `SERIAL_NUMBER_REQUEST`、`ASSIGN_ONU_ID_MESSAGE`、`RANGING_REQUEST`、`RANGING_TIME_MESSAGE`、
  `TIMER_TO1_EXPIRE`、`TIMER_TO2_EXPIRE`、`DEACTIVATE_ONU_ID_MESSAGE`、
  `DETECT_DOWNSTREAM_LOS_OR_LOF`、`CLEAR_DOWNSTREAM_LOS_OR_LOF`、
  `DISABLE_SERIAL_NUMBER_{DISABLE,ENABLE}_MESSAGE`、`EXTENDED_BURST_LENGTH_MESSAGE`、
  `ENCRYPT_PORT_ID_MESSAGE`、`ASSIGN_ALLOC_ID_MESSAGE`、`CONFIGURE_PORT_ID_MESSAGE`、
  `KEY_SWITCHING_TIME_MESSAGE`、`REQUEST_KEY_MESSAGE`、`REQUEST_PASSWORD`、
  `PHYSICAL_EQUIPMENT_ERROR_MESSAGE`、`VENDOR_SPECIFIC_MESSAGE`、`BER_INTERVAL_MESSAGE`、
  `BURST_PROFILE_MESSAGE`、`BROADCAST/DIRECTED_POPUP_MESSAGE`、`CHANGE_POWER_LEVEL_MESSAGE`、
  `PST_MESSAGE`、`SERIAL_NUMBER_MASK_MESSAGE`、`ASSIGN_GEM_PORT_MESSAGE`、`UNKNOWN_MESSAGE`。
- 处理函数（节选）：`p_o2_3_sn_request_event`（`0xd0d4`）、`p_o4_ranging_request_event`（`0xd108`）、
  `p_o4_assigned_onu_id_msg`（`0xe9a4`）、`p_o4_ranging_time_msg`（`0x107e4`）、
  `p_o5_assign_alloc_id_msg`（`0xef30`）、`p_o5_request_registration_msg`（`0xf144`）、
  `p_o7_enable_sn_msg`（`0xeb20`）、`p_power_state_machine`（`0x112b0`）。
- PLOAM 收发：`fi_Configure_Ponmac_Get_Ploam_Msg`（`0x5d68`）、
  `fi_Configure_Ponmac_Send_Sr_Ploam_Msg`（`0x5af8`）、
  `fi_Configure_Interrupt_Read_Ploam_Msg`（`0xc6b0`）、
  `Drv_PloamuMic_Generate_Activation`（`0x426c`）、`Drv_PloamdMic_fake`（`0x44c4`）。
- 注册路径：O2-3 SN-Request → O4 ASSIGN_ONU_ID → RANGING_REQUEST → RANGING_TIME（TO1）→
  O5 ASSIGN_ALLOC_ID → REQUEST_REGISTRATION → 密钥交换（`fi_set_key_exchange_state` `0x11f10`）
  → O7 ENABLE_SERIAL_NUMBER。**里面没有 GEM/T-CONT/OMCI。**

⇒ **PLOAM 这一层厂商是做完的**，这是本报告里唯一对 M2 有利的硬事实。

## 3. OMCI 的发送门：项目笔记里的 `+0x8c8` 说法是错的

### 3.1 读者确实存在

`netdriver.ko` 的 `swport_dev_xmit_fin`（`.text+0x2350`，156 字节）：

```
236c  ldr   x2, [x19, #0x8c8]     ; 读 netdev+0x8c8
2374  cbnz  x2, #0x2398           ; 非零 -> 走发送
2378  ldr   x0, [x1, #0x38]       ; 统计块 A(+0x8d0) 的错误计数
2380  str   x0, [x1, #0x38]       ; ++err
238c  bl    __dev_kfree_skb_any
2390  mov   w0, #-1               ; 返回 -NETDEV_TX_BUSY
```

私有块在 `netdev+0x8c0`（`ethdrv_add_br_device` 里 `mov x2,#0x188; … memset`），
所以 **`+0x8c8` = priv+0x08**，一个 8 字节指针位。

### 3.2 但它没有读者以外的东西——而且它根本没被装上

- **写者存在**（笔记说"无写者"是错的，只是字面偏移扫不到）：
  `netdriver.ko` `ethdrv_add_br_device` `.text+0x1158`：`str x21,[x20,#8]`
  （`x20 = netdev+0x8c0`）⇒ 写进 `netdev+0x8c8` 的是 `dev_get_by_name()` 得到的
  `struct net_device *`；
  `np_133.ko` 的建网口辅助函数 `.text+0xf334`：`str x19,[x19,#0x8c0]`、
  `str w20,[x19,#0x8c8]` ⇒ 同一位置写的是 **u32 网口 id（0..6，非零）**。
  两者对同一字段的**语义不同**。
- **`swport_dev_xmit_fin` 是死代码**：它**没有被导出**
  （netdriver 的 `__ksymtab_strings` 只有 `g_update_seconds, set_devnode_mode,
  update_statistics_2eth, pdt_ethdrv_recv, up_ethdrv_handle_recv_packet,
  dl_ethdrv_handle_recv_packet`），并且**18 个模块里没有任何一处重定位引用它**
  ⇒ 它不是任何网口的 `ndo_start_xmit`。

### 3.3 真正的门在别处，而且是厂商自己留的空桩

1. **`zte_xgpon.ko` 的 `fi_Configure_Ponmac_Send_Omci_Msg`**：
   `.text+0x5e4c`，**size = 8 字节**，内容就是
   ```
   00005e4c  mov  w0, #0
   00005e50  ret
   ```
   ⇒ **厂商的 OMCI 发送入口返回成功，但一个字节都不发。**
2. **没有任何 ioctl 能发 OMCI PDU**：`pon/gpondrv-ioctl.txt` 里 OMCI 相关的只有
   1016 `gpondrvSetOmciMskAdapter`、1017 `gpondrvSetOmciEnSecurityAdapter`，
   macro 15 是 `gpondrvGetOpStateAdapter`（读）。
3. **netdriver 里有 `omci_recv`（`0x114`）、没有 `omci_send`**；
   `pdt_ethdrv_send`（`0x29a8`）在模块内**没有调用者**，只是被
   `pdt_ethdriver_init`（`0xda4`）填进 MAC 回调表。
   `omci_recv` 只是把**收到的**帧转给 Monitor netlink（`OtherMontorSendMsg`），
   `"send OMCI msg  failed %d"` 是那次转发的失败打印。

⇒ **结论**：厂商固件的 OMCI 上行从来就没通，**不是"某个字段没人写"这种可修的 bug，
而是发送函数被做成了空桩**。因此没有"把厂商缺的那一环补上"这条路。

## 4. mainline 侧：连 PON MAC 都没有

- `staging/tree/drivers_net_ethernet_zte_Kconfig`：`ZTE_ZX279133_ETH`（NPPT + IDM，
  **SR1010 的 WAN 路径**）、`ZTE_ZX279133_LAN_CONDUIT`、`ZTE_ZX279133_RTL8372N`。
  **没有 PON MAC，没有 PLOAM，没有 GEM/T-CONT，没有 OMCI。**
- 树里与 PON 相关的东西只有数据通路一侧：
  `zx279133-np.c:37` `regmap_write(eth->pon_route, 0, …)`、
  PPU mcode 装载器 `zx279133_ppu_mcode_prepare()`（`:1989`，固件
  `"zte/zx279133/mcode_intel.bin"`，`ZX279133_MCODE_MAGIC`/`_INST_NUM` 校验、
  逐指令 tag `ZX279133_MCODE_TAG_{PKT_TYPE,DUP,SKIP,VERSION,PORT_FLOW}`）、
  ONU MAC 写 `zx279133_spa_onu_mac_write()`（`:445`）、
  `zx279133_program_spa_cpu_mac()`（`:459`）、
  `zx279133_program_wanid_cpu_mac()`（`:542`）。
- **SerDes 不是 PON**：`&pon_serdes`/`&uni_serdes` 只是 NPPT WAN 口的**以太网 PHY**
  （`phys = <&pon_serdes>, <&uni_serdes>`、`pcs-handle = <&xpcs1>`）。
- **我们自己的 DTS 里没有 PON 节点**：`zx279133-sk-d840n.dts` 有 `&pon_serdes`(:264)、
  `&uni_serdes`(:268)、`&nppt`(:272)，**没有 `zte,zx279133-pon` 节点、没有 0x17000000 的
  reg、没有 `pon_plat`**（只有一个 `reserved-memory` 块 :96）。
  ⇒ **mainline 在这块板上连 PON 网口都不会创建。**

## 5. 用户态那一半：厂商的 OMCI 应用**在**（推翻 `pon/INVENTORY.md` §4i）

`staging/vendor-full.tgz`（21,100,715 B）与 `staging/vendor-root.tgz`（7,558,792 B）里：

- **`./bin/gpon_omci`（889,128 B）**：1981 个 OMCI 相关符号
  （`OMCI_Execute_*`、`OMCI_me_*`、`zxic_omci_msg_sendto_drv`、
  `zxic_omci_sendmsg_to_omci`、`OmciAdapter_*`、`OnuAuthenticateOlt`、
  `OmciBasedKey`、`pm_pon_set_tcont/gemport`、`pm_pon_get_onu_info`、`g_onuPwMode`）。
  传输方式：**`socket(AF_PACKET, SOCK_RAW, htons(0x%x))`** ⇒ 裸以太网包，发到 `omci` 网口。
  配置来自厂商 OSS 层（`liboss.so`、`OSSStart`、`tOMCI_OSSCFG`）。
- `./bin/sigpon`（18,808 B）：gpondrv 的 ioctl 客户端，字符串 `/dev/gpondrv_dev`、`/dev/comdev`。
- `./bin/pc`（39,228 B，厂商进程管理）、`./bin/nas_main`（70,928 B）、`./bin/cspd`、
  `./bin/osgid`、`./bin/opticaltst`（`/dev/optical_dev`）、`./bin/netdebug`（`/dev/netdebug_dev`）。
- `./etc/gponcfg/db_default_*.xml`：**19 个按省的 OMCI MIB 默认库**；
  `./etc/GPON_GN28L9{5,6,7}_datas*`：BOSA 查找表。
- **来源**：`restore_set/root_stock.bin` 偏移 `0x1300000` 切出的原厂 `kernel1` FIT
  （`initramfs@133`，LZMA 14,452,253 B → newc cpio 54,466,560 B，1849 条记录 / 1012 个文件）。
- **全部是 32 位 ARM**（ELFCLASS32, e_machine=40，含 busybox 与 `ld-2.26.so`），
  而板上模块是 AARCH64 ⇒ 靠内核的 `CONFIG_COMPAT` 跑（`el0_sync_compat`/`el0_fiq_invalid_compat`）。
- **板上已经跑过**（docs/12 §4，21:33:12）：
  `ld-2.26.so --library-path … gpon_omci` 打出
  `====================gpon_omci start======================`，
  然后 `[ut_main]$$$ omci OSSStart error:-1!` ⇒ **它起得来，卡在厂商应用服务器**。
- **整根 rootfs chroot 试过**（docs/12，21:47–21:49）：`/bin/pc` 起来了
  （`pc`/`osgid`/`ifs_mgr` 活着），但 `voip/httpd/raserver/dhcps/dhcp6s` 立刻死
  （缺 `/usr/data`、`/userconfig`、XML 库）；**`gpon_omci` 始终没起来**，
  `omci pkt cnt` / `txd frm cnt` / PON 的 RX-TX 全 0。
- 活着的设备节点：`gpondrv_dev=102, comdev=158, zxled=97, gponsdk_dev=111, switch_dev=104`。

## 6. 上游选项：`pon/upstream-ponwrt/` **不支持 GPON**

- `README.md`：**"WIP Airoha PON kernel drivers — PON MAC and optical frontend drivers for
  Airoha AN7581/AN7583"**，状态表 **GPON ❌、XG-PON ✅、XGS-PON ❓、EPON 1G/1G ❌、
  10G-EPON 10G/1G ✅**。⇒ **本板是 GPON，用不上它的 MAC**。
- `airoha-pon-frontends_README.md`：EN7572（512B BOB NVMEM）、**GN28L95/UX3363（APONCAL NVMEM）**
  —— 这块板正好用 GN28L95/GN28L96 ⇒ **BOSA/光前端这块代码可复用**。
- `airoha-xpon-netlink.h`：通用 netlink family `"xpon"` v1，
  `GET_LINE/SET_LINE/GET_STATUS`，属性 `IFINDEX/MODE/ACTIVE_MODE/LINE/FRONTEND/
  REGISTRATION/DATAPATH/COUNTERS`，line 属性含 `OPTICAL_SIGNAL/RX_ACTIVE/PHY_READY/
  XGTC_SYNC/PCS_SYNC/PCS_PROFILE` ⇒ **"用一条 netlink 通道把 PON 状态机搬出内核"这个思路
  是现成的范式**。
- `airoha-paged-bosa-gn28l95.c`（GPL-2.0-only）：分页 BOSA 访问
  （`select_page 0xff/0/2`、id `0x80`==0xa1、ready `0xc7`）。
- **Rust OMCI 守护进程 / `airoha-ponctl` 不在 WS 里** ⇒ 上游那套 OMCI 是
  **内核模块 + 通用 netlink**，不是 C 库。

## 7. 未证 / 需要活板或厂商源码才能闭合的

1. `omci`/`pon` 网口**实际装的是哪个 xmit**，以及它为什么返回错误 ⇒ 真正的 OMCI TX 门
   （`swport_dev_xmit_fin` 已排除）。
2. 跑的 `4.19.136+` 内核的 `struct net_device` 布局（np_133 与 netdriver 的偏移来自不同版本）。
3. OLT 用 SN 还是密码认证（`zxic_api_gp_set_pwd` 已导出，但没找到调用者）——需要 OLT。
4. 让 `gpon_omci` 走到 `OSSStart` 成功所需的厂商 `/usr/data`、`/userconfig` 与 XML 库
   （不在盘上）。
5. 若走"从零写"路线：Airoha 那套栈能不能补 GPON（现在 ❌）。

## 8. 与计划的对应

| 计划条款 | 本报告的答案 |
|---|---|
| G5 判据：PON 注册 + OMCI + 数据 + 两次冷启动 | **不可达**：OMCI 上行没有任何可借鉴的通路，mainline 无 PON MAC |
| 回退："PON 状态机工作量过大 → M2 可行性报告" | **本文件即该报告；M2 冻结为"研究"** |
| "不要在没有 PON 状态机的情况下把产物叫'可用光猫'" | 遵守：M1 产物叫"实验室路由器" |
