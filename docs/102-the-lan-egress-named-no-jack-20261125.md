# 102 — 板子发出去的帧没有出口：7.3 驱动的出向目的口写死了 5

> 本文承接 `docs/101-the-7-3-4-baseline-and-the-migration-surface-20261122.md`。
> 101 号记录的是把 SK-D840N 挪到 Linux 7.3.4 的通道与增量清单；本文记录的是
> **7.3 固件刷上板之后、对拍判出来的第一个功能缺口**：板子收得到、发不出去。

---

## 一、判决

1. **板子的「发不出去」是驱动的出向描述符写错了，不是 9132 的表项不对。**
   7.3 树把 IDM TX 描述符的**目的口字段**写死成 **5**（`ZX279133_LAN_TX_PORT`），
   而 5 是 **XMAC0**——一个 10G SerDes MAC，本板没有对端，厂商也从不编程它。
   于是 CPU 交给网卡的每一帧都被瞄准一个「没有对端的 MAC」：
   帧离开 CPU（桥与 conduit 的 TX 计数都在涨）、进入 9132、**从不任何一个网口出来**。
2. **目的地本来就在帧上。** DSA tagger（`net/dsa/tag_zx279133_rtl8372n.c`）
   在**每一帧出网**时压进一个 802.1Q 头，VID = 55 + DSA 端口号；
   板上 dmesg 自己印着「ports 4..8 carry the transport VIDs 59..63」，
   即 DSA 端口 4..8 = PPU 1..5 = transport VID 59..63。
   ⇒ **芯片口 = VID − 58**，与收方向的 `zx279133_idm_rx_lan_vport()` 正好互逆。
   6.18 世代就是读这个 VID、把它算成芯片口写进描述符的；7.3 树**既不读也不剥**。
3. **LAN 帧的第四个描述符字也不对。** 厂商自己的 `dump_desc_tx()` 叫它
   「GEM port 的 LLID id 加 queue id」，**LAN 路径写 `0x003e0000`**、PON 路径写
   `0x1f568623`；7.3 树两条路都写 PON 控制字 `zx279133_tx_pon_control`（`0x08000000`）。
4. **修法就是照 6.18 世代的形状搬过来**，不学新东西：
   读出 transport VID → 把它算成芯片口 → **同时把标签从帧上摘掉**
   （带着标签出线的帧，客户端用不了）→ LAN 路径的第四个字改成厂商的值；
   没有 transport VID 的帧走 fallback，fallback 从 5 改成 **2**
   （第二个 UNI 口，本板实测值，docs/59 §3.2）。
5. **这次改动带了门禁和反向控制**（`tools/check-sk-lan-egress.py` +
   `tests/test_sk_lan_egress.py`）：6 条判定，6 条变异，**每条变异只让被它点名的那条判定变红**；
   门禁直接读**将要被编译的那棵树**，不是读一份合成样本。
6. **补丁尚未编译、尚未上板。** 本机没有 arm64 交叉工具链，编译只能在 CI 里发生；
   门禁只能证明**形状**对不对，证明不了它通了。真正的判决要等 CI 出件、刷机、
   实机双向复测。

---

## 二、现象与测量

### 2.1 方向性实测（刷完 7.3 固件的第一次对拍）

| 方向 | 板子侧 | PC 侧 | 判决 |
|---|---|---|---|
| PC → 板子（UDP 广播洪流 10 s） | `lan-cpu0`/`lan2` RX **+19940 包** | — | **通** |
| 板子 → PC（`ping -c 20`） | `br-lan` TX 3998 B/69pk → 5510 B/93pk；`lan-cpu0` TX 6994 B/85pk → 8872 B/109pk（**+24 包**） | 以太网 RX 恒为 **107188190，零增长** | **不通** |

⇒ 帧**确实离开了 CPU**（桥与 conduit 的 TX 计数都在涨），**却没有到 PC**。
丢掉的地方在 9132 里面，或者在 9132 出去的那一段。

### 2.2 板上的网口与端口号（实测，不是推的）

```
5: lan0@lan-cpu0: state DOWN                     （不在 br-lan）
6: lan2@lan-cpu0: state UP, master br-lan        ← PC 的网线在这里
7: lan3@lan-cpu0: LOWERLAYERDOWN, master br-lan
8: lan4@lan-cpu0: LOWERLAYERDOWN, master br-lan
9: lan1@lan-cpu0: LOWERLAYERDOWN, master br-lan
4: lan-cpu0: state UP        （DSA conduit，tag_protocol = tagging）
```

驱动自己印的端口表（板上 dmesg）：

```
the 9132 is serving: the jacks are the 9132's UNI ports 1..4 (DSA ports 4..7),
the 2.5G jack is the outerphy port 5 (DSA port 8), the CPU port is 9 and the
transport VIDs are 59..63
registered the 9132 as a DSA switch: ports 4..8 carry the transport VIDs 59..63
```

⇒ **DSA 端口 N+3 = PPU 端口 N**，**transport VID = 55 + DSA 端口 = 58 + PPU**。
⇒ 收方向的 `zx279133_idm_rx_lan_vport()` 用的就是这个表的逆（VID − 59 + 1）。

### 2.3 谁把标签压上去的

7.3 树的 DSA 核心里，用户口的发送函数**就是 tagger 自己的**：

```
net/dsa/user.c:2719   p->xmit = cpu_dp->tag_ops->xmit;
net/dsa/tag_zx279133_rtl8372n.c:14-18
        __be16 tag[VLAN_HLEN / sizeof(__be16)] = {
                htons(ETH_P_8021Q),
                htons(ZX279133_RTL8372N_TRANSPORT_VID_BASE + dp->index),
        };
```

⇒ 主机发往某个网口的每一帧，在进 TX 描述符之前，**头上都带着那个网口的 transport VID**。
6.18 世代的驱动就是在这里把它读下来、算成芯片口、**并把标签摘掉**的。

### 2.4 6.18 世代被证明能用的形状

| | 6.18 世代（`kernel-overlays/`，本板实测通） | 7.3 树（本文的对象） |
|---|---|---|
| 目的口 | `tx_port = lan_out_port ? lan_out_port : eth->lan_tx_port;`（netdev.c:626） | `tx_port = ndev != hw_ndev ? ZX279133_LAN_TX_PORT : zx279133_tx_port;`（netdev.c:508），**5** |
| 标签 | `zx279133_lan_strip_transport_vlan()` 读 VID 并摘标签（netdev.c:589/2256） | **没有** |
| 第四个字 | `eth->lan_tx_word4` = `0x003e0000`（netdev.c:670，厂商 LAN 路径的值） | `zx279133_tx_pon_control` = `0x08000000`（netdev.c:549，**PON** 控制字） |
| fallback | `ZX279133_LAN_TX_PORT` = 2（docs/59 §3.2 实测） | 5 |

6.18 世代的 LAN 能通，是在**同一块板子**上测出来的：docs/90 §5 里 LAN2/LAN3/LAN4
各 4/4 双向、`txp` 写入 0 次——目的口是每帧自己带的，不是旋钮拧出来的。

---

## 三、修复

补丁：`kernel-patches/7.3-local/220-sk-lan-egress-port.patch`
（SHA256 `8c0a43dd72a6c967a605dab4e099e7bc5bc9141c9a7616aabb5ad783d5d9e64a`，8535 B）。

改动只有两个文件：

**`drivers/net/ethernet/zte/zx279133.h`**

* `ZX279133_LAN_TX_PORT`：5 → **2**（fallback 用；带标签的帧不再走它）。
* 新增 `ZX279133_LAN_TX_WORD4` = **`0x003e0000`**（厂商 LAN 路径的第四个字）。

**`drivers/net/ethernet/zte/zx279133-netdev.c`**

* 新增 `zx279133_lan_strip_transport_vlan()`：读帧头的 802.1Q VID，
  落在 `ZX279133_LAN_TRANSPORT_VID_MIN..MAX`（59..62）内才算数，
  然后 `__skb_vlan_pop()` **把标签摘掉**，并算出芯片口
  `VID − (59 − ZX279133_9132_UNI_PPU_FIRST)` = **VID − 58**。
* `zx279133_start_xmit_common()` 多带一个 `lan_out_port` 参数；
  **目的口**改成「每帧自己带的那个，没有才用 fallback」；
  **第四个字**在 LAN 路径上改成 `ZX279133_LAN_TX_WORD4`，PON 路径不动。
* 摘标签的位置**在环容量检查之后**：`NETDEV_TX_BUSY` 返回时标签必须还在，
  因为标签就是重试时的目的口。

---

## 四、门禁

`tools/check-sk-lan-egress.py` 读**将要被编译的那棵树**（`--tree`），问 6 件事：

1. 出向命名的是 transport VID 带的那个芯片口（摘标签的函数在、被调用、结果真的进了描述符）；
2. 芯片口的换算与收方向用的是同一套 VID 范围与同一个首端口常量；
3. 标签在描述符填好之前就被摘掉；
4. LAN 路径的第四个字是厂商的 `0x003e0000`，不是 PON 控制字；
5. 没有 transport VID 的帧，fallback 落在 PPU 1..4 里（不是 5）；
6. 摘标签发生在环容量检查之后。

`tests/test_sk_lan_egress.py` 是它的**反向控制**：6 条变异分别破坏上面 6 条，
每条变异都要求门禁**恰好在它点名的那条判定上变红**——不是「哪儿红了算哪儿」。
本机实测：门禁在打过补丁的树上 **6/6 绿**，在没打补丁的树上 **6/6 红**，
反向控制 **7/7 绿**。

门禁与反向控制都进了 7.3 workflow 的 `paths:`（改了它们要起一次构建），
并作为一步跑在**编译之前**、对着**同一棵树**。

---

## 五、未做与未验证

1. **补丁没有被编译过。** 本机没有 arm64 交叉工具链。CI 的 `kcheck.py` 步骤会用宿主
   clang 解析这些文件，但那是语法与符号，不是编译。
2. **补丁没有上过板。** 出向是否真的通了，本文的判决只到「形状与 6.18 世代一致」为止；
   真判决要等 CI 出件 → 刷机 → 板子 ping PC + PC 收板子的 ARP/ping。
3. **第四个字这条改动带了一点「与厂商对齐」的成分。** 6.18 世代在 docs/90 测量时，
   LAN 路径的第四个字是否已经是 `0x003e0000`，那一轮没有记录；
   本次按厂商 dump 出来的 LAN 路径的值一并改，与 6.18 世代今天的形状一致。
4. **2.5G 口（PPU 5 / DSA 端口 8 / VID 63）不在本树的处理范围内**：
   7.3 树的 LAN transport VID 范围是 59..62，且该口的厂商 RTL8226 初始化
   仍未搬进 7.3 驱动（另一个独立缺陷）。
5. **C4/C5（PON 栈没有把 `pon_state`/`omci_transport` 挂到网口上）没有查。**
6. **仪器 C3 的假 FAIL（内核把生效触发器写在方括号里，仪器按整串比较）**
   与本补丁无关，是仪器自己的缺陷，另行修。
