# 1G 线速下的 CPU 负载：实测（2026-10-31）

## 0. 一句话

**1 Gbps 对这台板子是「刚好够」**：全部收包中断和 NET_RX 软中断都落在 CPU0 上，
1500B 帧跑到 940 Mbps 时 CPU0 已经 95.8% 忙（总 CPU 47.9%），流量一旦真投递给
用户态 socket，总 CPU 立刻到 98.3%。瓶颈是**单核软中断**，不是总算力。

## 1. 测法

- **板子**：Skyworth SK-D840N，ZTE ZX279133，`aarch64` 双核 Cortex-A53（`CPU part
  0xd03`），512 MB RAM，256 MB NAND，无 cpufreq（频率锁死）。内核 #284 + rootfs #288。
- **链路**：PC 的 Realtek PCIe GbE（192.168.9.25/24，链路 1 Gbps）↔ 板子面板 2.5G 口
  `lan5`（DSA 口，跑 1G SGMII 档，`1e:7580=0x9402`）。板子 `ethtool lan5` 报的
  `2500Mb/s` 是 RTL8226 的**能力上限**，实际协商速率是 1 G。
- **没有 iperf3**：板子的 busybox 是 1.37.0 的**精简 nc**（`nc [IPADDR PORT]`，不支持
  `-u/-l/-p`），也没有 socat / openssl / python。所以发压器放在 PC 侧：
  `tools/udpflood.py`（UDP 多线程洪泛）、`tools/tcpsink.py`（TCP 接收端，配板子
  `nc IP PORT` 用）。
- **口径**：板子**自己**把快照写进 `/tmp`（`/proc/stat`、`/proc/net/dev`、
  `/proc/net/softnet_stat`、`/proc/interrupts`、`/proc/uptime`、`date +%s`），
  测量窗口的长度用 `/proc/uptime` 的差值精确定量，不用 PC 侧的往返时间。CPU 占用
  = (总 jiffies − idle) / 总 jiffies，双核算容量。
- **收端的选择**：目的端口 9999（无监听，走「收包→丢」路径）对比目的端口 53
  （dnsmasq 的真实 socket，走 socket 投递路径）。
- 原始快照另存 `staging/ctl/stress-1g-20261031.log`（staging 不进仓库）。

## 2. 五个场景

| 场景 | 板子实收 | 包速率 | CPU0 | CPU1 | 总 CPU | 丢包 |
|---|---|---|---|---|---|---|
| 空闲基线（20.02 s） | – | – | 0.00% | 0.10% | **0.05%** | – |
| UDP→9999，1500B 帧 | 940.5 Mbps | 77.7 kpps | **95.80%** | 0.08% | **47.94%** | **0** |
| UDP→53（dnsmasq），1500B 帧 | 959.6 Mbps | 79.2 kpps | **97.54%** | **99.01%** | **98.26%** | **0** |
| 板→PC，TCP（busybox nc） | 435.1 Mbps（PC 侧收） | 38.7 kpps | **97.79%** | 0.06% | 48.93% | 0 |
| UDP→9999，500B 小包 | 375.5 Mbps | 86.6 kpps | **99.96%** | 0.08% | 50.02% | 约 47% 丢在交换机侧 |

逐条判据：

1. **1 G 收包零丢包，但 CPU0 只剩 4% 余量。** `/proc/net/dev` 的 `lan-cpu0` 收包
   计数在 65.04 s 里涨了 7,646,317,381 B / 5,050,476 包（940.5 Mbps @
   77,655 pps），`drop` 计数 9 → 9 不动，`br-lan` 的 drop 恒 0。CPU 侧
   `/proc/stat` 差分是 user 1 / system 6 / **softirq 6228** / idle 6771 jiffies ——
   忙的 6235 jiffies 里 **99.9% 是软中断**。
2. **softnet 的 backlog 一直在积压。** CPU0 的 `time_squeeze` 从 3,266 涨到 18,097
   （65 s 内 +14,831 次），`dropped` 恒 0。积压是交换机那边的缓冲顶着扛下来的。
3. **流量一旦进用户态 socket，两个核一起满。** 目的端口换成 dnsmasq 的 53 之后，
   CPU0 仍在 97.5%，CPU1 被 dnsmasq 收包打到 **99.0%**，总 CPU 98.26%，
   `load average` 1.28。此时每包 CPU 从 12.35 µs 涨到 24.88 µs。
4. **500B 小包撞上了包速率天花板。** PC 供 163,130 pps（652.5 Mbps），板子只收下
   **86,604 pps**（375.5 Mbps），其余约 47% **丢在交换机 CPU 端口的硬件队列** ——
   板子侧 `drop` 计数不涨（丢在 switch 内部，DSA 驱动不计数），而 CPU0 已经
   99.96%。每包 CPU 11.55 µs。
5. **因此单核的包速率上限 ≈ 86,600 pps**（1 / 11.55 µs），1500B 帧时恰好等于 1G
   线速（81.3 kpps 的 95–99%）。

## 3. 硬件加速 / 卸载的现状（实测，不是推断）

| 项 | 状态 | 证据 |
|---|---|---|
| 交换机硬件转发 | **开** | LAN↔LAN 二层转发在 switch 芯片里完成，不进 CPU |
| rx-checksumming | on | `ethtool -k eth0/lan-cpu0/lan5` |
| GRO | on | `generic-receive-offload: on` |
| **TSO / GSO** | **无** | `tcp-segmentation-offload: off`、`generic-segmentation-offload: off [requested on]` |
| **scatter-gather** | 无 | `off [fixed]` |
| VLAN offload / receive-hashing | 无 | 均 `off [fixed]` |
| TX checksum | 半残 | 只有 `tx-checksum-ip-generic`（仅伪首部），`tx-checksum-ipv4/ipv6: off [fixed]` |
| **nft flowtable（软件流卸载）** | **无** | `nft list ruleset` 只有 `table ip nat { postrouting: oifname "eth0" masquerade }` 和一个空的 `table inet dnsmasq`；`/proc/net/flow_offload` 不存在；**`/etc/config/firewall` 根本不存在**（构建 yml 明写「This build has no feed packages，firewall4 is not in the tree」） |
| 硬件 RX 队列 | 有但没用上 | `lan-cpu0`/`eth0` 都是 RX 2 + TX 2，但整机只有**一个**网口中断 `22 (19000000.ethernet)`，100% 在 CPU0 |
| **RPS / RFS** | 默认全关 | `rps_cpus=0`（四处）、`rps_sock_flow_entries=0`、`rps_default_mask=0` |
| **巨型帧** | **被驱动挡死** | `ip link set lan5 mtu 9000` → `RTNETLINK answers: Invalid argument`；`lan-cpu0` 设 9004 同样被拒；`zx279133-switch.c` 里没有 change_mtu / max_frame |
| irqbalance | 没装 | `which irqbalance` 空 |
| netdev_budget | 300 / 20000 µs | 已经是厂商调过的值 |

**驱动里已经有硬件流表卸载**：`zx279133-offload.c`（NPPT IPv4/IPv6 flow-table
offload）在构建列表里（Makefile 的 `zx279133-offload.o`），板上
`/sys/kernel/debug/zx279133-flows` 在、`/sys/class/net/eth0/device/wan_ports` 属性
也在，PPU 微码已按**本板**的 port-flow 表正确加载（`PPU microcode version
0x10108.0x31ba6a03`，`9132 PPU 0 word 0 (entry-pc) = 0000a6a5`）。它没被用起来的
原因是用户态那一半不存在：**板上没装 `tc`**（接口是 TC flower，
`ndo_setup_tc` 处理 `TC_SETUP_BLOCK`/`TC_SETUP_FT`），而这个构建没有 feed 包，
`iproute2` 的 `tc` 得像 nftables 那样自己 vendor 进 overlay；也没有 fw4 去下发规则。

## 4. RPS 的 A/B（唯一不动驱动就能动的旋钮）

| 场景 | RPS 位置 | 板子实收 | CPU0 | CPU1 | 总 CPU |
|---|---|---|---|---|---|
| 1500B | 关（原始） | 940.5 Mbps @ 77.7 kpps | 95.80% | 0.08% | 47.94% |
| 1500B | 只在 `lan-cpu0` | 971.6 Mbps @ 80.2 kpps | 98.68% | 1.89% | 50.28% |
| 1500B | `lan-cpu0`+`eth0`+**`br-lan`** | 950.8 Mbps @ 78.5 kpps | 95.34% | **23.23%** | 59.28% |
| 500B | 关（原始） | 375.5 Mbps @ 86.6 kpps | 99.96% | 0.08% | 50.02% |
| 500B | `lan-cpu0`+`eth0`+**`br-lan`** | **607.0 Mbps @ 140.0 kpps** | 99.62% | **69.30%** | 84.45% |

**关键发现：RPS 设在 `lan-cpu0` 或 `eth0` 上完全无效。** `/proc/net/softnet_stat` 的
`received_rps` 恒为 0，CPU1 的 `processed` 计数纹丝不动（1 → 1）。**必须设在
`br-lan`（桥）上**才真正分核 —— 设上之后 CPU1 的 `received_rps` 从 0 涨到
1,213,744，CPU1 分走 23%（1500B）到 72%（500B）的软中断。

结论：**包速率上限 86.6 → 140.0 kpps（+62%），500B 小包吞吐 375.5 → 607.0 Mbps
（+62%）**，代价是同等流量下总 CPU 多付 34 个点（大包场景多付 11 个点）。大包
1G 场景链路本来就吃满，RPS 不涨吞吐只多花 CPU；小包/高 pps 场景 RPS 是必需的。

```sh
echo 3 > /sys/class/net/br-lan/queues/rx-0/rps_cpus      # 打开
echo 0 > /sys/class/net/br-lan/queues/rx-0/rps_cpus      # 恢复
```

该设置不持久（断电回 0），要常开得写进 hotplug 或启动脚本。

## 5. 由此得到的优化清单

**不用改驱动（可立刻做）**

1. RPS 设在 `br-lan`（§4）：小包处理能力 +62%。注意设在 `lan-cpu0`/`eth0` 上无效。
2. 认清 1G 大包场景 CPU 已经是 95.8%，别把它当成「还有一半空闲」。

**要改驱动（需机主点头，且要重新刷 kernel）**

3. **巨型帧**（收益最大、改动最小）：1G 下 CPU 是被**包速率**卡死的
   （≈12 µs/包）。1500B 帧 81 kpps 就打满 CPU0，换 9000B 帧后 pps 需求降到
   ~14 kpps，**CPU 负载理论降 5–6 倍**。要做的是在 `zx279133-switch.c` 里实现
   `change_mtu` 并把交换机的端口最大帧长寄存器一起改掉。PC 侧 Realtek 网卡有
   「巨型帧」高级属性（当前关闭），可直接开 9014。
4. **TC flower 硬件流卸载接通**：驱动和 PPU 都在位，缺 `tc`（要 vendor 进 overlay）
   和下发脚本。这是唯一能让 NAT/转发**完全不上 A53** 的路径，直接绕开 87 kpps
   上限；验收还需要把 WAN 侧（`eth0`）配出 IPv4 和默认路由。
5. **9132 二层硬件转发**：`docs/PLAN-lan1-2p5g-20261030.md` §9 已把它列为挂起项
   （不是取消）：`zx279133-switch.c:2556` 的 `port_bridge_join` 不设
   `*tx_fwd_offload`，`:3002-3016` 的 `dsa_switch_ops` 没有 `port_fdb_add/del`，
   CPU 侧 FDB 不同步 → 交换机学不到 → 泛洪给 CPU。已知单播 UNI↔UNI 从来没被
   干净测过，验收需要 LAN1 通 + 至少两只口插线。
6. **2.5G 档位**：`docs/PLAN-lan1-2p5g-20261030.md` §8 明确推迟（`1e:7580` 低 6 位
   要从 `0x02` 变成 `0x16`）。

**硬件天花板（软件改不动）**

7. CPU 是双核 A53 @1 GHz，无 cpufreq，超不了频。
8. 8 核 NPU 在 OpenWrt 里没有网络转发用途，不是通用加速器。

## 6. 没测到的，和为什么

- **LAN→WAN 转发 + SNAT**：板子的 WAN 口 `eth0` 没有 IPv4、也没有默认路由（只有
  `192.168.9.0/24 dev br-lan`），PC 也只有一根有线网卡，凑不出「两头主机」，
  构造不出真正的路由转发压测。
- **发送方向的 1 G**：板子只有精简 busybox nc（4 KB 写、单线程），只压到
  435 Mbps。那 435 Mbps 的 CPU 里 system 占 43 s，**大头是 busybox nc 自己的
  read/write syscall**，不是内核转发开销，所以不能拿它当「1 G 发送的内核成本」。

## 7. 复现

```sh
# PC 侧发压（板子收）
python tools/udpflood.py --host 192.168.9.1 --port 9999 --threads 6 --size 1472 --seconds 75

# 板子侧取窗口快照（板子自己存，窗口长度用 /proc/uptime 的差值）
sleep 3; cat /proc/uptime > /tmp/t0; head -3 /proc/stat > /tmp/s0; \
cat /proc/net/dev > /tmp/d0; cat /proc/net/softnet_stat > /tmp/n0; sleep 65; \
cat /proc/uptime > /tmp/t1; head -3 /proc/stat > /tmp/s1; \
cat /proc/net/dev > /tmp/d1; cat /proc/net/softnet_stat > /tmp/n1
```
