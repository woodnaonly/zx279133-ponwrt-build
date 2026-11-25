# SK-D840N 7.3 线：TCP 校验和是半残的，TCP 全被对端静默丢弃（2026-11-26）

## 判决

板子发出的**每一个 TCP 包**的校验和都是错的：出向路径上没有任何人把完整的
checksum 写回 TCP 头。

机制是这样的：

* 驱动声明了 `NETIF_F_HW_CSUM`，于是内核把 TCP checksum **委托**出去，不再自己算
  （`skb->ip_summed` 被置成 `CHECKSUM_PARTIAL`）；
* 而这条芯片的数据通路只会加**伪首部**，不会把最终的 checksum 写进帧的 TCP 头 ——
  驱动只是把 `BIT(15)` 和两个 offset 写进 IDM 描述符，指望芯片补上；
* 于是发出去的是**占位值** ⇒ 对端收到后**静默丢弃**（不回 RST、不报错、没有任何提示）
  ⇒ TCP 连接永远建不起来。

ICMP 不受影响：ICMP 没有 offload 路径，校验和是内核自己算的。所以一直以来的验收口径
（ping）根本看不出这个问题 —— **ping 通 ≠ TCP 通**。

⇒ 修法：把 `zx279133_tx_hw_csum` 置 0 ⇒ 不再声明 `NETIF_F_HW_CSUM` ⇒ 内核用软件
算 checksum。代价是 CPU 多做一点 checksum 运算，这本来就是那半残的硬件 supposed 要省
掉的。

**这也顺带解释了 6.18 线**：6.18 那份驱动声明的是同一个半残的 checksum（`
kernel-overlays/drivers/net/ethernet/zte/` 下的同一段代码），所以 **6.18 线的 TCP 也是
坏的**；只是 6.18 线从来没有量过 TCP —— 量的全是 ICMP。

## 证据

### 1 现象：同一个口上，ping 通而 TCP 不通

刷上 run #82 的内核（head `0b51e52e`，带 230 补丁）之后，**出向**在 lan1 上已经通了：

| 判据 | 结果 |
|---|---|
| PC 侧邻居表 | `192.168.9.1 -> D0-45-8D-84-09-00 state=Reachable` |
| PC 侧以太网收包计数 | 134143 → 134157（+14）；再跑一次 134195 → 134210（+15） |
| 板子侧 ping 192.168.9.100 | 3 发 3 收，0% 丢包，1.443/1.570/1.673 ms |
| 板子侧 ARP 表 | `192.168.9.100 dev br-lan lladdr 84:47:09:5b:bd:2c REACHABLE` |

**但 TCP 完全不通**：PC 连板子的 80 端口超时；板子连 PC 的 8080 端口报
`Failed to send request: Operation not permitted`。

### 2 抓包：SYN 和 SYN-ACK 都在网线上，PC 不回 ACK

PC 侧用 pktmon 抓到的（pcapng 解析）：

```
PC -> 板子: 192.168.9.100.1880 > 192.168.9.1.80    Flags [S]   seq 294831915
板子 -> PC: 192.168.9.1.80 > 192.168.9.100.1880    Flags [S.]  ack 294831916
```

板子确实回了 SYN-ACK（板子侧 `netstat` 里那条 `192.168.9.1:80  192.168.9.100:1773
SYN_RECV` 就是它），**PC 却一个 ACK 都不回**，只是把 SYN 重发了 3 次。

板子主动发起的那一侧也一样：`192.168.9.1.41074 > 192.168.9.100.8080 Flags [S]`
连发 **7 次**，PC 一个回应都没给 —— 连 RST 都没有。

而同一段时间里 ICMP 双向正常：抓包里 `192.168.9.100 > 192.168.9.1: ICMP echo
request` 与它的 reply 一来一回，板子 ping PC 也是 2 发 2 收。

### 3 校验和：算出来是无效的，而且重传时不变

把 pcapng 里的板子那个 SYN 取出来，按伪首部 + TCP 段算一次：

```
192.168.9.1:41074 -> 192.168.9.100:8080 flags=0x02 csum_field=0x93e4 sum=0x85eb valid=False ip_header_ok=True
（7 次重传的 csum_field 都是 0x93e4，sum 各不相同）
```

* **IP 头的校验和是对的**（`ip_header_ok=True`），所以抓包与解析都没问题；
* **TCP 校验和无效**（含原 checksum 求和非 0）；
* 而且 **7 次重传的 checksum 字段是同一个值**，可是同一批帧里的 TCP 时间戳选项每次
  都在变 —— 真正的 checksum 必然跟着变。这正是「占位值」的样子：包里的那个数不是这次
  传输算出来的。

### 4 板子自己的计数与判决一致

```
ethtool -S eth0 | grep -i csum
     tx_hw_csum_packets: 127
     tx_sw_csum_packets: 0
     rx_hw_csum_packets: 0
```

⇒ 127 个包**全部**走了「IDM 硬件 checksum」这条路，软件一个都没算。

### 5 代码：驱动把 checksum 委托给了 IDM

`drivers/net/ethernet/zte/zx279133-main.c`：

```
if (zx279133_tx_hw_csum) {
        /* IDM checksum generation is IPv4/TCP-only. */
        ndev->hw_features |= NETIF_F_HW_CSUM;
        ndev->features |= NETIF_F_HW_CSUM;
        ndev->vlan_features |= NETIF_F_HW_CSUM;
}
```

`drivers/net/ethernet/zte/zx279133-netdev.c` 的 `zx279133_start_xmit_common()`：

```
if (skb->ip_summed == CHECKSUM_PARTIAL) {
        hw_csum = zx279133_tx_hw_csum &&
                skb->len >= ETH_ZLEN &&
                zx279133_tx_csum_supported(skb);
        if (!hw_csum) {
                if (skb_checksum_help(skb))
                        goto drop;
                sw_csum = true;
        }
}
...
if (hw_csum) {
        length_flags |= BIT(15);
        metadata0 |= (skb_network_offset(skb) * 2) & 0x7f;
        metadata0 |= ((skb_transport_offset(skb) * 2) & 0x7f) << 8;
}
```

⇒ 硬件路径一旦选中，**软件兜底就不会再走**；而选中的依据只有那个编译期常量
`#define zx279133_tx_hw_csum 1`（`zx279133.h:83`）。

### 6 旧文档里早有这条线索

`docs/94-the-cpu-load-at-one-gigabit-20261031.md` 的表格里写着：

```
| TX checksum | 半残 | 只有 tx-checksum-ip-generic（仅伪首部），tx-checksum-ipv4/ipv6: off [fixed] |
```

「只有伪首部」= 硬件只会算伪首部部分和，**不会把完整 checksum 写进帧**。

### 7 排除掉的其他嫌疑（都在 PC 侧，都不是原因）

| 嫌疑 | 实测 | 结论 |
|---|---|---|
| xray/v2rayN 的 TUN 劫持 | 把 TUN 适配器停掉，TCP 照样超时；且 TUN 关闭时 ICMP 正常 | 排除 |
| Windows 防火墙 | 三个 profile 本来就全是 `Disabled`；临时全关后再测，仍然超时 | 排除 |
| PC 网卡的 ARP/MAC | 邻居表里是板子的真 MAC `D0-45-8D-84-09-00`；PC 网卡 MAC 与之逐位相同 | 排除 |
| 板子的 nftables | 规则表只有 fw4 默认表 + dnsmasq 表，output 链 policy accept，没有任何 REJECT | 排除 |
| 板子的 uhttpd 本身 | 板子访问自己的 `http://192.168.9.1/` 正常返回 LuCI 页面 | 排除 |

⇒ 责任在**板子发出的 TCP 包本身**，与 PC 无关。

### 8 改了什么

补丁 `kernel-patches/7.3-local/240-sk-lan-tcp-csum.patch`，一处：

* `drivers/net/ethernet/zte/zx279133.h`：`#define zx279133_tx_hw_csum` 1 → **0**，
  并在宏上面写清为什么（半残的伪首部能力、板上实测到的占位 checksum 现象）。

一处改动同时解决两条路：

* `main.c` 的 `if (zx279133_tx_hw_csum)` 不再成立 ⇒ 内核**不委托**，自己在栈里算；
* 万一仍有 `CHECKSUM_PARTIAL` 的包走到 xmit，`hw_csum` 恒为假 ⇒ 驱动的
  `skb_checksum_help()` 兜底把它算完。

### 9 门禁

* `tools/check-sk-lan-tcp-csum.py`：7 条（标志是 0、feature 声明仍在 guard 内、
  软件兜底还在、判定仍读标志、整个驱动里只有一处声明该 feature、宏旁有理由、
  `CHECKSUM_PARTIAL` 仍被处理）。
* `tests/test_sk_lan_tcp_csum.py`：7 条变异 + 1 条原树。每条变异都让**它点名的那条**
  变红（把标志改回 1、把 guard 改成 `if (1)`、删掉软件兜底、把判定改成硬编码
  `true`、在别的文件里声明该 feature、删掉宏旁的理由、把 `CHECKSUM_PARTIAL` 改掉）。

本地在「补丁串 + 板级层」都铺好的树上跑过：门禁 7/7 绿，反控 8/8 绿。

## 未做

* **还没上新内核**：这一版只到「改好、门禁与反控本地绿、补丁能在准备好的树上落地」。
  编译、刷机、在 lan1 上的 TCP 验收都还没做 ⇒ 「TCP 通了」这句话目前**没有**证据。
* **6.18 线没动**（约束如此）：6.18 那份驱动声明的是同一个半残的 checksum，所以
  **6.18 线的 TCP 现在仍然是坏的**；本判决只修 7.3 线。
* 速率仍是 1G 协商（`Link is Up - 1Gbps/Full`），本轮修的是 TCP 能不能通，不是 2.5G
  的速率。
* PC 侧的 v2rayN 为了排查被重启过一次（并把 TUN 临时关掉过），已按备份还原：
  `guiConfigs/guiNConfig.json` 恢复原值、`EnableTun` 回到 true。
