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

### 8 conduit 的 features 是从 eth0 抄的 —— 补丁在源头就够

板上 `ethtool -K eth0 tx off` **单独做不够**，必须 `ethtool -K lan-cpu0 tx off` 才通，
一开始看着像「conduit 另有独立声明，补丁漏了它」。查了源码，不是那样：

`drivers/net/ethernet/zte/zx279133-netdev.c` 的 `zx279133_lan_netdev_setup()`（conduit 的
`netdev_setup` 回调）：

```
ndev->features = eth->ndev->features & ~NETIF_F_HW_VLAN_CTAG_FILTER;
ndev->hw_features = eth->ndev->hw_features & ~NETIF_F_HW_VLAN_CTAG_FILTER;
ndev->vlan_features = eth->ndev->vlan_features;
```

⇒ **conduit 的 features 是从父设备 eth0 复制来的**，它自己没有独立的 feature 声明；
而整个驱动里 `NETIF_F_HW_CSUM` 只有 `zx279133-main.c` 的 guard 内一处声明（`main.c:642-644`）。

⇒ 240 补丁把那个 guard 关掉 ⇒ eth0 不再有 `NETIF_F_HW_CSUM` ⇒ conduit 复制过去的也就没有 ⇒
**LAN 帧真正走的那条路上也不会再委托 checksum**。补丁是够的，不需要追加改动。

（`ethtool -K eth0 tx off` 单独不够，是因为 eth0 的 feature 改变发生在 conduit 已经复制完之后；
conduit 是独立 netdev，得在它自己身上关。）

### 9 改了什么

补丁 `kernel-patches/7.3-local/240-sk-lan-tcp-csum.patch`，一处：

* `drivers/net/ethernet/zte/zx279133.h`：`#define zx279133_tx_hw_csum` 1 → **0**，
  并在宏上面写清为什么（半残的伪首部能力、板上实测到的占位 checksum 现象）。

一处改动同时解决两条路：

* `main.c` 的 `if (zx279133_tx_hw_csum)` 不再成立 ⇒ 内核**不委托**，自己在栈里算；
* 万一仍有 `CHECKSUM_PARTIAL` 的包走到 xmit，`hw_csum` 恒为假 ⇒ 驱动的
  `skb_checksum_help()` 兜底把它算完。

### 10 门禁

* `tools/check-sk-lan-tcp-csum.py`：8 条（标志是 0、feature 声明仍在 guard 内、
  软件兜底还在、判定仍读标志、整个驱动里只有一处声明该 feature、宏旁有理由、
  **conduit 从父设备继承 features**、`CHECKSUM_PARTIAL` 仍被处理）。
* `tests/test_sk_lan_tcp_csum.py`：8 条变异 + 1 条原树。每条变异都让**它点名的那条**
  变红（把标志改回 1、把 guard 改成 `if (1)`、删掉软件兜底、把判定改成硬编码
  `true`、在别的文件里声明该 feature、删掉宏旁的理由、把 `CHECKSUM_PARTIAL` 改掉、
  **让 conduit 自己声明 `NETIF_F_HW_CSUM`**）。

本地在「补丁串 + 板级层」都铺好的树上跑过：门禁 8/8 绿，反控 9/9 绿。

### 11 第一次上板被 CI 挡住：paths 条目粘了行尾注释

8e6319de 推上去之后，**两条线一起红了**（7.3 线 run 84 的 repository gates、6.18 线 run 434 的
LED CI regression），红在同一句：

```
FAILED: 1 of 271 check(s)
  - build-sk-d840n-73.yml: tests/test_sk_lan_tcp_csum.py is covered by paths
```

原因是这一行（我把新加的路径条目和下面那条门禁的注释写在了同一行）：

```yaml
      - tests/test_sk_lan_tcp_csum.py      # The gate that says the lane is programmed again after the bring-up,
```

`tools/check-sk-workflow-paths.py` 的条目正则是 `^\s*-\s+(\S+)\s*$` —— **整行**必须就是
一个路径 ⇒ 这一行没被算进 paths ⇒ 「改了它不会触发构建」这条性质被破坏。6.18 线的 LED
作业也跑这条门禁（它一次检查两个 workflow），所以同一个错误把两条线一起打红。

⇒ 修法：路径条目自己一行，注释归位（`0ad2cdf0`）。本地跑那条门禁只要一秒：
`python3 tools/check-sk-workflow-paths.py --workflow A --workflow B`。

### 12 上板验收：新内核，TCP 通了

CI 7.3 线 run 85（head `0ad2cdf0`）全绿，产物取件后自校验 6/6（sha256sums、镜像门禁 9/9、
内核装进 8 MiB 分区还余 2.5 MB），只刷 kernel 一段：

| 步骤 | 结果 |
|---|---|
| 下载 | `kernel download verified crc=d0617529` |
| 擦除头 | `kernel erase head verified crc=f154670a`（全 0xFF） |
| 擦除尾 | `kernel erase tail verified crc=f154670a`（全 0xFF） |
| 写入后回读 | `kernel read-back verified crc=d0617529` |

起机后 banner `Linux version 7.3.4`，驱动自打印 `the transport VIDs are 59..63`（230 补丁仍在）。

**关键判据：不用任何运行时绕行，`tx-checksumming` 默认就是 off。**

```
ethtool -k eth0      | grep tx-checksumming   =>  tx-checksumming: off
ethtool -k lan-cpu0  | grep tx-checksumming   =>  tx-checksumming: off
ethtool -S eth0      | grep -i csum           =>  tx_hw_csum_packets: 0
```

⇒ 240 补丁在**源头**关掉了这个 feature，eth0 与 conduit（从 eth0 复制 features）**两个口**都
不再委托 checksum ⇒ 与第 8 节的读码结论一致。

在 lan1（2.5G 口）上的实测：

| 判据 | 结果 |
|---|---|
| PC 侧邻居表 | `192.168.9.1 -> D0-45-8D-84-09-00 state=Reachable` |
| PC 侧以太网收包计数 | 174219 → 174227 → 174241（连续递增） |
| PC → 板子 HTTP | `GET /` → `HTTP/1.0 200 OK`，664 字节的 LuCI 入口页 |
| PC → 板子 LuCI | `GET /cgi-bin/luci/` → `403` + `x-luci-login-required: yes`（LuCI 未登录的正常应答） |
| 板子 → PC ICMP | 3 发 3 收，0% 丢包，1.516/1.599/1.671 ms |
| 板子 → PC TCP | `wget http://192.168.9.100:8080/` → `Download completed`；PC 侧监听端收到 `connection from ('192.168.9.1', 41802)` 与完整的 `GET / HTTP/1.1` |

⇒ 板子**自己发起**的 TCP 也通了（修之前是 `Failed to send request: Operation not permitted`），
不再只是「PC 能连板子」。⇒ **「要处理到能进入路由系统」在 lan1 上达成**：浏览器能打开 LuCI。

## 未做

* **板上还有运行时绕行的残留**：早前那轮为了验证方向，手工执行过
  `ethtool -K eth0 tx off` 与 `ethtool -K lan-cpu0 tx off`。新内核刷进去后这两个开关都是
  默认 off（见第 11 节），绕行已经不必要；但本轮**没有**去查 rootfs 里是否有开机脚本会
  再把它们打开 —— `scripts/sk-d840n-offload.sh` 里没有 ethtool 调用，全仓 grep 也没有第二处。
* **本轮只在 lan1 上验收**：PC 的网线一直插在 2.5G 的 LAN1 口上，其他三个 1G 口没有测过。
* **6.18 线没动**（约束如此）：6.18 那份驱动声明的是同一个半残的 checksum，所以
  **6.18 线的 TCP 现在仍然是坏的**；本判决只修 7.3 线。
* 速率仍是 1G 协商（`Link is Up - 1Gbps/Full`），本轮修的是 TCP 能不能通，不是 2.5G
  的速率。
* PC 侧的 v2rayN 为了排查被重启过一次（并把 TUN 临时关掉过），已按备份还原：
  `guiConfigs/guiNConfig.json` 恢复原值、`EnableTun` 回到 true。
