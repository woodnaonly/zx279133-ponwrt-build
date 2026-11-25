# SK-D840N 7.3 线：2.5G 口的 transport VID 63 不在树的范围内（2026-11-25）

## 判决

7.3 线上 2.5G 口出向全死的根因是**树自己的 transport VID 范围停在 62**，而 2.5G 口的
VID 是 **63**：

* **收方向早就是通的** —— 板级层已经把 tagger 的端口范围抬到 8（`kernel-overlays/`
  里那份 `include/net/dsa_zx279133_rtl8372n.h`），2.5G 口的帧带着 VID 63 进来，
  tagger 认得，分发到 DSA 口 8。
* **出方向读的是树自己那份 59..62** —— tagger 发出去的帧带着 VID 63，
  `zx279133_lan_strip_transport_vlan()`（220 补丁）不认这个 VID，于是既不剥标签、
  也不从标签里读出芯片口，帧带着 802.1Q 标签直奔 fallback 口 2（第二个 UNI 口，
  没插线）⇒ PC 一个包都收不到，而板子照样把这一帧计成「已发送」。

⇒ 7.3 树必须把范围放到 **59..63**（6.18 线就是这样，2.5G 口在那边是通的），并给
「逻辑 LAN 口的 MF 扩展表」补上**第五条**表项，外加把表和范围绑在一起的
`static_assert`。

同时**推翻 docs/104 的判决前提**：它把出向死归给「lane 只被编程一遍」，但它当作
「好」的那一轮（1254，17:29）在串口日志里没有任何流量记录；而唯一能定位 PC 位置的
那一轮（1252，14:32）里 PC 插的是 **lan2（1G 口）** —— 2.5G 口在 7.3 线上从来没有
通过过。

## 证据

### 1 板子上现在是什么样子（run #81 的 7.3.4，PC 插在 2.5G 口）

netdev 与 DSA 端口的对应（板上 `ip -d link show` 的 `portname`）：

| netdev | DSA port | 物理口 |
|---|---|---|
| lan0 | p4 | 1G #1 |
| lan2 | p5 | 1G #2 |
| lan3 | p6 | 1G #3 |
| lan4 | p7 | 1G #4 |
| **lan1** | **p8** | **2.5G（PC 插的就是它，UP 且有 carrier）** |

驱动自己打印的（本次启动 2.666344 与 2.676958 秒）：

```
the 9132 is serving: the jacks are the 9132's UNI ports 1..4 (DSA ports 4..7),
the 2.5G jack is the outerphy port 5 (DSA port 8), the CPU port is 9 and the
transport VIDs are 59..63
registered the 9132 as a DSA switch: ports 4..8 carry the transport VIDs 59..63
```

⇒ 驱动**自己知道** 2.5G 口是 p8、VID 63。

### 2 收方向是通的（板上实测）

PC 发 60 个 UDP 广播（192.168.9.255:9999），板上三个计数器同步 +60：

| 接口 | 发之前 | 发之后 |
|---|---|---|
| lan-cpu0 | 1351 | 1411 |
| lan1（p8，2.5G） | 1351 | 1411 |
| br-lan | 1097 | 1157 |
| lan0 / lan2 / lan3 / lan4 | 0 | 0 |

板子的 ARP 表里也有 PC：`192.168.9.100 dev br-lan lladdr 84:47:09:5b:bd:2c`。

⇒ 2.5G 口的帧确实到了 p8，并且被分发到了 lan1。

### 3 两个范围不一样：板级层改了 tagger，树没改驱动

| 地方 | 6.18 线 | 7.3 线 |
|---|---|---|
| `ZX279133_RTL8372N_USER_PORT_MAX`（tagger 的端口范围） | 8 | 8 —— **但是板级层改的** |
| `ZX279133_LAN_TRANSPORT_VID_MAX`（树自己的 VID 范围） | 63 | **62** |
| `zx279133_lan_vport_mf[]`（逻辑 LAN 口的表项数） | 5 | **4** |

7.3 树里那份 `include/net/dsa_zx279133_rtl8372n.h` 是**板级层覆盖进去的**：
`tools/apply-sk-overlay-73.sh` 把 `kernel-overlays/`（排除 6.18 驱动目录的那份）
铺到树上，并且自己就带着一条断言：

```
grep -q 'ZX279133_RTL8372N_USER_PORT_MAX[[:space:]]*8' \
	"$tree/include/net/dsa_zx279133_rtl8372n.h" || {
	echo "the DSA transport-VID range was not widened to port 8" >&2; exit 1; }
```

而 `zx279133.h` 与 `zx279133-np.c` 属于 7.3 驱动，板级层**不覆盖**：
`kernel-overlays/` 的 `drivers/net/ethernet/zte/` 整个被排除，
`kernel-overlays-73/` 里也没有这两个文件 ⇒ 树自己那份 62 与 4 条表项就留了下来。

### 4 出向被挡住的那一行

tagger 的发方向不查范围，直接写 `BASE + dp->index` = 55 + 8 = **63**
（`net/dsa/tag_zx279133_rtl8372n.c`）。驱动这边查的是树自己的范围：

```
/* drivers/net/ethernet/zte/zx279133-netdev.c */
if (vid < ZX279133_LAN_TRANSPORT_VID_MIN ||
    vid > ZX279133_LAN_TRANSPORT_VID_MAX)	/* 62 */
	return 0;
...
*out_port = vid - (ZX279133_LAN_TRANSPORT_VID_MIN -
		   ZX279133_9132_UNI_PPU_FIRST);
```

⇒ VID 63 走不进来：`lan_out_port` 留 0 ⇒ 描述符的输出口用 fallback
`ZX279133_LAN_TX_PORT` = 2，标签也没剥 ⇒ 帧带着标签从 lan2 出去。

### 5 决定性对照：同一版内核上，1G 口通、2.5G 口不通

| 口 | transport VID | 出向 |
|---|---|---|
| 1G 口（lan2，1252 轮 14:32） | 62 | **通**（板上 ping 3 发 3 收） |
| 2.5G 口（lan1，1255/1256 轮 18:12 之后） | 63 | **全死**（板子发、PC 收包增量 0） |

同一份代码、同一个 strip 函数、同一套描述符写法 —— 唯一差别是 VID 有没有落在
59..62 里。

### 6 改了什么

补丁 `kernel-patches/7.3-local/230-sk-lan-transport-vid63.patch`，三处：

1. `zx279133.h`：`ZX279133_LAN_TRANSPORT_VID_MAX` 62 → **63**；
2. `zx279133-np.c`：逻辑口表补第五条
   `{ 0x1c8c2800, 0x31a37e00, 0x00000ad8, 0 }`（VID 63，2.5G 口），
   四个值与 6.18 线**逐字相同**；
3. 同文件：`static_assert(ARRAY_SIZE(zx279133_lan_vport_mf) ==
   ZX279133_LAN_VPORT_LAST - ZX279133_LAN_VPORT_FIRST + 1);`

为什么第五条表项是必须的：范围放到 63 之后 `ZX279133_LAN_VPORT_LAST` 就是 63，
`zx279133_lan_vport_mode_write()` 的循环会索引表的第 5 条；没有它就越界读，把
数组后面的东西写进交换机的表。

### 7 门禁

* `tools/check-sk-lan-vid63.py`：8 条（树的范围、tagger 的范围、VID 63 命名的芯片口
  就是 outerphy、收方向的映射落在同一个 VID、表项数与范围相等、第五条是 6.18 那条、
  static_assert 在、出向读的是常量而不是字面量）。
* `tests/test_sk_lan_vid63.py`：8 条变异 + 1 条原树。每条变异都让**它点名的那一条**
  变红；第一条（范围退回到 62）会让依赖范围的检查一起红 —— 那正是范围该有的性质。

本地在「补丁串 + 板级层」都铺好的树上跑过：门禁 8/8 绿，反控 9/9 绿。

## 未做

* **还没上新内核**：这一版只到「改好、门禁与反控本地绿、补丁能在准备好的树上落地」。
  编译、刷机、在 lan1 上的验收都还没做 ⇒ 「出向通了」这句话目前**没有**证据。
* **`59fbb1fc`（bring-up 后再把 lane 编程一遍）没有回退**：它的前提被本轮证据推翻，
  但它做的事与出向死无关（它只动 lane，不碰 VID 范围），留着无害；留不留等出向通了
  之后再看。
* 第五条表项是**外推值**，不是实测值：板上没有第五个 LAN 口的厂商配置可读，
  四个实测项给的两条等差（word 1 每口 +0x00a00a00，word 2 每口 +0x28）被延续下去。
  6.18 线用同一组值，2.5G 口在那边是通的。
* 2.5G 口的**远端**厂商初始化（`rlt8226b.ko`）仍未搬进 7.3 驱动；本轮不碰它。
