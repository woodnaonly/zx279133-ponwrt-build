# SK-D840N 7.3 线：lane 只被编程一次时，2.5G 口的出向全死（2026-11-25）

## 判决

同一个内核、同一个驱动、同一个板子，两次启动的差别不是 lane 的配置，而是
**lane 被编程了几次**：

| 启动 | U-Boot 阶段 | lane 被编程次数 | 出向 |
|---|---|---|---|
| 1254（17:29:19） | 没有跑 net bring-up（`ubinit=0 tftp=0`） | **两次**（1.6 s 一次、8.99 s 一次） | 通（PC ping 0% 丢包） |
| 1255（18:12:20） | 跑了 net bring-up + TFTP（`mode_eth_1gbase_x_cfg` / `mac 4 phy status changed: 1000M full-duplex`） | **一次**（1.6 s） | **全死**（板子收得到 PC 的帧，回包一个也到不了 PC） |

⇒ **7.3 线必须在 bring-up 之后再把这条 lane 编程一遍**，而且这一遍**不能**
顺带重启远端的 auto-negotiation（那是驱动自伤，见 docs/92 `12.3）。

## 证据

### 1 两次启动的 lane 状态，在各自做完自己的活之后是逐字相同的

| 量 | 1254（好） | 1255（坏） |
|---|---|---|
| 远端 SerDes class（`1e:0x7580`） | 0x9402 | 0x9402 |
| lane 自己的字（`1e:0x758d`） | 0x801e | 0x801e |
| 远端 AN status（`7:0x01`） | 0x0069 | 0x0069 |
| 近端 PCS VEND2 BMCR | 0x1140 | 0x1140 |
| 近端 PCS VEND2 AN_CTRL / DIG_CTRL1 | 0x0005 / 0x2200 | 0x0005 / 0x2200 |
| XMAC0 tx/rx/frame/mode/duplex/misc | 0x60010001 / 0x3e8000c7 / 0x80000001 / 0 / 0x100 / 0x2000 | 同左 |
| 近端 Uni SerDes 的 49 字画像 | 驱动自己写的（`diff1g=0`） | **U-Boot 留下的**（`diff1g=1`） |

⇒ 配置不是差别。

### 2 近端那 1 个字不是病根，6.18 线早就判过

`diff1g=1` 的那一个字是 word 6（偏移 0x18）：U-Boot 留 `0x00ff8000`，驱动的
1G 画像是 `0x0b016100`。docs/92 `79.2 把这一条量过并判了：

> 厂商 Linux 的 image 与 U-Boot 的 image 不是一回事（差 4 个字，0x18 和 0xa0
> 是速率/均衡一类的设置）。厂商 Linux 的 2.5G 口一个字节都收不到，U-Boot 的
> 搬了 11 MB ⇒ 所以留 U-Boot 那份是对的，驱动里的参考文件不改。

⇒ **近端画像不该动**，本轮也没动。

### 3 差别在「第二遍编程」

两次启动的完整日志逐行 diff（去掉时间戳）后，实质差别只有几处；其中这五行是
1254 独有的：

```
  1.596749  the 2.5G jack's PHY: auto-negotiation restarted (attempt 1/3, ...)
  5.885778  1G profile written in place            <- 近端画像被重写
  8.973497  LAN1_RATE_DIAG attempt=2 stage=before   <- 近端被重新上电
  8.989178  SOPC mac 0 never became ready           <- XMAC0 被重新编程
  9.000508  the lane's BMCR was 0x0140 and is 0x1140 <- PCS 的字被重写
```

1255 一次都没有。那两行 1254 有、1255 没有的 —— `SOPC mac 0 never became
ready` 与 BMCR 被重写 —— 只出现在
`zx279133_switch_outerphy_apply_speed()` 里，也就是「这一遍 lane 被编程」。
⇒ 1254 的 lane 被编程了两次，1255 只有一次。

### 4 那第二遍是被什么触发的，1255 里为什么不会有

`lane_watch()` 的 `rearm:` 分支里有一条：远端的 class 变了就把 lane 放回去。

* 1254：驱动自己重启了远端的 AN，远端的 class 从 0x9412 变成 0x9402 ⇒
  `lane_seen` 过期 ⇒ **watch 的第一遍**（probe 结束 + 2 s）就把 lane 重新编程了一遍。
* 1255：class 是 U-Boot 留下的 0x9402，`lane_seen` 记的就是它 ⇒ **永远不会变**
  ⇒ **永远不会重新编程**。

⇒ 第二遍编程不是「本来该有的」而是「碰巧有的」：它只在驱动自己动过远端时
才发生，而继承 U-Boot 状态的启动恰恰是驱动没动远端的那种。

### 5 出向是单向死的

1255 里板子学到了 PC 的真 MAC（`192.168.9.100 dev br-lan lladdr
84:47:09:5b:bd:2c`），PC 侧对 `192.168.9.1` 的 ARP 却是 `Incomplete`、
`00-00-00-00-00-00`；板子 `ping -c 3 -W 1 192.168.9.100` 三个发出去零个回。
⇒ PC→板 通、板→PC 不通，缺口在 lane 的出向。

（板子 ping 192.168.9.1 是 xray_tun 的假应答，不算证据；判据是 PC 的 ARP 表
与 PC 网卡的收包计数。）

## 改了什么

### 驱动（`kernel-overlays-73/drivers/net/ethernet/zte/zx279133-switch.c`，仅 7.3 线）

1. 新增 `zx279133_switch_lane_reapply()`：把 lane 再编程一遍
   （近端重新上电、XMAC0 重新编程、SOPC send-enable 重新发布、PCS 的字重写），
   **不**重启远端的 AN。上限 3 次/boot，两次之间至少隔 5 s。
2. `lane_watch()` 在 bring-up 之后的第一遍无条件调它一次
   （`if (!priv->lane_reapplies)`）。
3. 远端 class 变化那条路径改为走同一个函数，两条路径因此不会连着跑两遍。

6.18 发布线的 `kernel-overlays/` 一行没动；`kernel-overlays-73/` 是 7.3 线自己的。

### 门禁（`tools/check-sk-lane-reapply.py`）

问的是「树里还有没有这一遍」，以及这一遍的四条性质：它真的编程 lane（不是只
打一行日志）、它有上限与冷却、它不重启远端的 AN、远端 class 那条路走的是同
一个函数。本机没有 aarch64 工具链，编译器会说的话由这个门禁代说。

### 反向控制（`tests/test_sk_lane_reapply.py`）

五条，每条把门禁的一条判据拿掉，门禁必须**恰好**在对应那条判据上变红：

| 拿掉什么 | 必须变红的判据 |
|---|---|
| watch 里那一遍 | the watch programs the lane again after the bring-up |
| 只打日志不编程 | the re-apply pass programs the lane |
| 上限与冷却 | the re-apply pass is bounded |
| 加回重启远端 AN | the re-apply pass does not restart the far end's auto-negotiation |
| class 变化不再走这一遍 | the far end's class change goes through the same pass |

本地演练：门禁 7 项全绿，5 条控制各自把对应判据打红。

## 未做

* **还没上板验**：这一版只改了驱动与门禁，CI、刷 kernel、实机判出向都还没做。
* **寄存器级在线实验做不了**：7.3 镜像没有 6.18 那套 debugfs 控制台
  （`m45_read` / `apply_xbasex` 都超时失败），所以「第二遍编程到底修了
  哪一处」没有直接读数；本轮认的是两次启动之间的结构差别。
* **>1G 吞吐本机验不了**：PC 网卡只有 1G。
* **冷启动 hang 的根因仍然悬着**（n=2：16:15 坏 / 17:29 好 / 18:12 好），本轮
  没有碰它。
