# 26 — 9132 的 LAN 数据面：run102 的实机证据与 run104 的修复（2026-10-21）

写于 2026-10-21。本文件记录三件事：run102 在实机上第一次把 lan1..lan4 跑出来、
厂商活板上量到的 9132 真值、以及“帧一个都不过去”的根因（LAN 数据面没被声明就绪）。

---

## 1. run102 的实机结果（run 39537842961，12:17 的套件）

RAM 引导（U-Boot tftpboot `sk-d840n-initramfs.itb`），串口全程可读。**这是第一次
四个网口同时出现。**

| 观测 | 值 | 意义 |
|---|---|---|
| `registered the 9132 as a DSA switch: ports 4..7 carry the transport VIDs 59..62` | — | DSA 注册成功 |
| `DSA: tree 0 setup` | — | DSA 树建立 |
| `lan1..lan4` | 四个 netdev 都在 | **fixed-link 生效**（run100/101 的 PHY -ENODEV 阻塞解除） |
| `br-lan: port 1(lan1) entered blocking state` / `disabled state` | 四个口都 disabled | **桥没把口打开** |
| `lan1: failed to open conduit lan-cpu0` | 每个用户口一次 | **根因** |
| `9132 UNI PHY 0b: id 84b9:5032, BMCR 1140->1140, link 1` | 只有 0b 有链路 | PC 插在 PHY 0x0b = PPU 2 = DSA 口 5 = lan2 ✓ |
| `9132 PPU 1 PVID word 2 00ec0000` | 59<<18 | PVID 写对了 |
| `9132 PPU 2 PVID word 2 00f00000` | 60<<18 | 同上 |
| `9132 PPU 3 PVID word 2 00f40000` | 61<<18 | 同上 |
| `9132 PPU 4 PVID word 2 00f80000` | 62<<18 | 同上 |
| `9132 PPU 5 (the DSA conduit) attribute word 96000001` | SVID 清零 | conduit 是 trunk ✓ |

PC 侧 `ping -S 192.168.9.25 192.168.9.1` = 100% 丢包。

### 1.1 PHY 寄存器（run102 新加的 dump，第一次拿到）

```
0a: 1040 7949 84b9 5032 11e1 0000 0064 2001 0000 0700 ...   link 0
0b: 1140 796d 84b9 5032 11e1 cde1 006f 2001 4006 0700 7800 ...   link 1
0c: 1040 7949 84b9 5032 11e1 0000 0064 2001 0000 0700 ...   link 0
0d: 1040 7949 84b9 5032 11e1 0000 0064 2001 0000 0700 ...   link 0
```

- 四颗 PHY 都是 `84b9:5032`（C22）。
- 0x10/0x11/0x12 存在（0b 的 0x10=0x006f、0x11=0x2001、0x12=0x4006），说明能力寄存器可读。
- 只有 0x0b 有链路（PC 插的那只口），BMCR 0x1140 = bit12(restart)+bit8(1000 full)+bit6(1000 half)。

---

## 2. 厂商活板上的 9132 真值（`/proc/pon/tm_debug`）

厂商 4.19 此刻还在跑，它的 `tm_debug` 目录是这台芯片唯一的一手接口。测出来的：

### 2.1 端口编号不与 ram_id 对齐

`sw_vlan_port_pvid_set port=1 vid=60` 之后，**写到了 `spa_port_attr_smac_1`（ram_id 2）**，
word2 = `0x00f00000` = 60<<18。

⇒ **厂商的 “switch port 1” = smac_1 = ram_id 2 = PPU 2 = PHY 0x0b = DSA 口 5 = lan2**，
和 PC 实际插的位置一致（厂商日志 `[eth1] port 1 link up:1000M`）。

### 2.2 SVID 的位段是 word2 的 bits 18..29

厂商自己的 `tm_port_pvid_set()`（np_133.ko:0x115784）做三件事：
`spa_set_pt_dft_vlan_pri()`、`spa_set_pt_upstream_untag_svid()`（位域号 0x10 = 16）、
`port_pvid_table_set()`（ERAM 表 0x25）。**位域号 16 就是 word2 bits 18..29**，
我们 np.c 里 `ZX279133_PPU_PVID_VID_REG 2` + `SHIFT 18` 与它一致 —— 这条不用改。

### 2.3 厂商把 802.1Q 整个关着

| 入口 | 参数 | 结果 |
|---|---|---|
| `sw_port_802dot1_en_get` | `sw_port=N` | **enable:0**（0..9 全部） |
| `tm_port_vlan_filter_get` | `p_port=N` | **pp_vlTagMsk:0**（0..9 全部） |
| `sw_vlan_port_inframemode_get` | `port=N` | mode:0（0..9 全部） |
| `sw_port_linkstate_get` | `port=N` | port 1 = **link:1 speed:2 duplex:1**（PC 那只口）；port 5、6 也 link:1（CPU 口与 PON 侧） |
| `sw_port_isolate_get` | `port=N` | enable:0 |
| `tm_mactable_fdb_show` | — | 表里只有一条：PC 的 MAC `84:47:09:5b:bd:2c` → **port_id 2** |

⇒ 厂商把 9132 跑成**纯扁平桥**：没有 VLAN 过滤、没有 802.1Q、靠 MAC 学习转发。
厂商的 4 个 per-port 网口（eth0..eth3，MAC 依次 …:08:ff / …:09:00 / …:09:01 / …:09:02）
是 CPU 侧的“扩展口”，源端口由**硬件元数据**给出，不是 VLAN 标签。

串口转储（厂商驱动的收包调试）直接印证：

```
d0 45 8d 84 09 00  84 47 09 5b bd 2c  08 00 45 00 ...   <- 无 8100，无标签
skb->dev->name = sw
skbinfo: port 1
```

### 2.4 所以“按 VID 转发”这条路不是厂商的路，但它是 DSA 的路

树里的标签器 `net/dsa/net_dsa_tag_zx279133_rtl8372n.c`：

- 收侧：帧里没有 VID 59..62 的标签就 **直接丢帧**（`if (!eth_type_vlan(...)) return NULL;`）。
- 发侧：给用户口的帧加 VID `55 + dp->index`。

而 mainline 的 IDM 收包路径**已经把厂商的硬件元数据翻译成了 DSA 需要的标签**
（cnjn 的 idm.c 里就有）：

```c
/* The tag is gone: the port comes from word 1. */
u16 transport_vid = zx279133_idm_rx_lan_vport(word1);
...
zx279133_idm_rx_preserve_dsa_vlan(...)   /* 或 zx279133_idm_rx_put_vlan() */
```

word1 的 bits 16..21 是 PPU 报上来的**传输 VID**（= 我们写进 PPU 1..4 的 SVID 59..62），
PPU 自己把标签摘掉了，驱动再把 802.1Q 头补回去给标签器。

⇒ **收侧不需要 9132 会打标签，只需要 PPU 会报源端口。发侧需要 9132 认得 VID。**

---

## 3. 根因：LAN 数据面没有被声明就绪

`zx279133_lan_netdev_open()`（conduit 的 ndo_open）：

```c
if (!(eth->datapath_users & ZX279133_DATAPATH_USER_LAN) ||
    !eth->lan_datapath_ready || !READ_ONCE(eth->hardware_prepared)) {
        ret = -ENETDOWN;
        goto out_unlock;
}
```

而 `zx279133_lan_netdev_open()` 是 DSA 的 `dsa_user_open()` 的第一件事：

```c
err = dev_open(conduit, NULL);
if (err < 0) {
        netdev_err(dev, "failed to open conduit %s\n", conduit->name);
```

⇒ `lan_datapath_ready` 一直是 false ⇒ 每个用户口都打不开 conduit ⇒ 桥把口置为 disabled。

第二处：IDM 收包路径

```c
if (lan_source && !lan_dsa_active) {   /* 丢帧 */
```

`lan_dsa_active` 只由 `set_dsa_active(true)` 置位，**我们从没调用过**。
所以就算口打开了，LAN 侧的帧也会被丢。

**两处都指向同一件事：这个驱动从来没有按 service 协议把 LAN 数据面声明出去。**

树内 RTL8372N 驱动（`drivers/net/ethernet/zte/zx279133-rtl8372n.c`，probe 里）是这么做的：

```c
zx279133_lan_service_datapath_set_ready(priv->service, true);
priv->parent_datapath_ready = true;
ret = zx2791372n_dsa_register(dev, priv);        /* dsa_register_switch() */
if (ret) goto err_datapath_restore;
zx279133_lan_service_set_dsa_active(priv->service, true);
```

### run104 的改动（commit 9d9805a）

`zx279133-switch.c` 的 probe 里补上同样三步、同样顺序，release 里按镜像顺序回退：

```c
zx279133_lan_service_datapath_set_ready(priv->service, true);
priv->datapath_ready = true;

ret = zx279133_switch_dsa_register(dev, priv);
if (ret) { ...set_ready(false)...; return 0; }

zx279133_lan_service_set_dsa_active(priv->service, true);
```

另外把试跑镜像的 `S26nat` 改成在**串口**上打印桥与各口的状态
（operstate / carrier / flags / rx / tx / brif / brport / 桥口 state / 地址 / 路由）。
理由：这条链上所有取证都走 ssh，而 ssh 要 LAN 通；LAN 不通时，串口是唯一的证人。

---

## 4. 这一轮没有改、但已经知道的事

- **厂商 4.19 的 eth0..eth3 不是网桥成员**（`br-lan` 只有 eth1），其余口是独立的；
  厂商的 per-port 分离靠的是“每个口一个 MAC + 硬件按 MAC 学习转发”，不是 VLAN。
- **DSA 的主口（lan-cpu0）不在 br-lan 里是对的**：DSA 的收侧由标签器把帧分发到用户口，
  主口只承担发出去。树内 SR1010（DSA 板）的 `02_network` 也是
  `ucidef_set_interfaces_lan_wan "lan1 lan2 lan3 lan4" "eth0"`，主口不在列表里。
- **厂商的 PPU 1..4 与 DSA 口 4..7 的对应**已由三处独立证据确认：
  厂商日志的 port 号、厂商 `sw_vlan_port_pvid_set` 的落点、以及 PHY 0x0b 的链路。

## 5. 下一步（run105 起）

1. run104 起来后先看 `lan1: failed to open conduit` 还在不在、`trial: link:` 那一串状态。
2. 若 PC 能 ping 通 192.168.9.1 ⇒ 直接进 G3（NAT 与 M1 单线验收）。
3. 若收得到帧但回不去 ⇒ 发侧问题：树内标签器给帧加了 VID 60，
   而 9132 的 ERAM VLAN 表项（word0 bits22..31 = 脱标掩码 0x3ff、word1 bits0..9 =
   成员掩码 0x3ff，即“全端口成员、全端口脱标”）得由活板验证；
   备选是自写一个 DSA 标签协议（发侧不加 VID，靠 MAC 学习定向 —— 厂商那条路）。
