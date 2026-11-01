# 刷进板子的那一轮，和它踩出来的一个真缺陷（2026-11-01）

## 0. 一句话

这一轮把 run 300 的三个分区（dtb / kernel / root）刷进了 SK-D840N，冷启动
成功，新加的四个 debugfs 动词（`me` / `mes` / `pr` / `cam`）有三个当场可用 ——
**然后第四个 `pr` 把板子读崩了**，内核死在 `readl()` 上。

崩溃的根因是本轮自己新加的代码漏了一道对齐守卫；顺着这条线审计下去，同一批
动词里还查出 **5 处同类出口**，全都同样能读崩板子。这份文件记的是这三件事：
刷机实录、缺陷本身、以及那次把板子救回来的窗口。

---

## 1. 刷机：三段，全部通过 crc32 回读闸门

U-Boot + TFTP 通道（mainline 的 eth0 数据面双向不通，Linux 侧拿不到载荷也
交不出东西；U-Boot 自带网络栈，TFTP 11.4 MB / 2.02 s）。

| 分区 | download crc | 擦除后头尾回读 | 写入后整段回读 crc | 结果 |
|---|---|---|---|---|
| dtb | `800ef7f6` | `f154670a`（0xFF） | `800ef7f6` | ✅ |
| kernel | `279b2ac9` | `f154670a`（0xFF） | `279b2ac9` | ✅ |
| root | `03b5bcaa` | `f154670a`（0xFF） | `03b5bcaa` | ✅ |

耗时 112 秒（11:16:39 → 11:18:31）。`mtd1`（boot）与 `mtd4`（parameter tags）
**全程未触碰**。

### 1.1 刷机前的备份

root 分区整段读会搞挂板子，所以按 **4 MiB 分块**、每块一次独立 SSH、板上
`md5sum` + `gzip`、PC 端复算：

* `backups/preflash-20261101/mtd5-root.bin.gz`（4735666 B）
* PC 端重组 251658240 B，md5 = `b981f7af8bacc6892fb95e58a033edc4`，
  与板上 `dd if=/dev/mtd5 | md5sum` **完全一致**
* `MANIFEST` 里含 mtd1..mtd5 每行的 dev/local md5 与 gz 大小

配置漂移另存 `backups/drift-20261101/config.tgz`。

### 1.2 回滚三元组

| 想回到 | 用哪个 |
|---|---|
| 刷机前那一刻的板上实况 | `backups/preflash-20261101/mtd5-root.bin.gz`（字节级，md5 与板上一致） |
| run 288 出厂状态 | `staging/trial/flash-rootfs.jffs2.run288` |
| run 284 内核 | `staging/trial/flash-uImage.run284` |

⚠️ run 288 的**镜像**与板上实况**不是同一份字节**（jffs2 挂载后原地写过）：
镜像首 4718596 B md5 `0d752fd9…`，板上实况对应段 `1f2b11be…`，尾部也不是全
0xFF。要回「刷机前那一刻」用 `mtd5-root.bin.gz`，要回「run 288 出厂状态」用
`flash-rootfs.jffs2.run288` —— 两者不是一回事。

---

## 2. 冷启动：成功

11:37:04 上电：

* 新内核起来，jffs2 root 挂载于 device 31:5，init/preinit 正常；
* eth0 Link Up 1Gbps/Full；
* 2.5G 口 `auto-negotiation is complete (status 0x0049)`；
* 端口标签变成 run 300 DTS 的 `lan0..lan4`。

### 2.1 验收结果

| 项 | 结果 |
|---|---|
| `/sys/kernel/debug/dynamic_debug/control` | ✅ 存在（`-rw-r--r-- 0 root root`） |
| `/sys/kernel/debug/zx279133-9132` | ✅ 存在（`-rw-------`） |
| `/sbin/tc`、`/usr/sbin/nft` | ✅ 都在 |
| `dmesg \| grep -c 'switch: lane: far'` | ✅ **0**（周期刷屏归零） |
| `me` | ✅ rc=0，16 个 `ppu me word` + `ppu pkt-type detail` |
| `cam` | ✅ rc=0，`cam: alg crc 0x00000000 bulk 0x00000002 depth0 0x00000012 depth1 0x00000000 smmu1 0x0009e100` |
| `mes 7 3` | ✅ rc=0（写入是否生效另说，见 §4） |
| `pr 90350 4` | ❌ **kernel panic**（见 §3） |

⚠️ **订正（2026-11-01，见 docs/98 §4）**：上面那行命令原先写成 `grep -c 'switch: lane'`，
**0 其实是周期刷屏模式 `switch: lane: far` 的计数**——刷屏行长这样：

```
[    1.608262] zx279133-switch 19000000.ethernet:switch: lane: far an=0x00ad 758d=0x801e 7580=0x9416
```

整个串口日志里这条模式一共 5033 行，分布在大量早期启动里。

`switch: lane` 本身**不是 0**，而是 **15 条一次性诊断行**：4 条 `lane pre-serdes:` +
11 条 `lane after:`，全部落在 t=1.59..2.66 s。它们与刷屏行**共享 `switch: lane` 前缀**，
不同的只是后缀（`pre-serdes:` / `after:` vs `: far`）——所以「15 条是不同字符串」这句
说法也不准确，区分它们要靠后缀，不能靠 `switch: lane` 这个前缀。

⇒ 刷屏归零的正确判据是 `grep -c 'switch: lane: far'` == 0。

---

## 3. 缺陷：`pr` 把板子读崩了

### 3.1 现象

11:41:03，验收的最后一步：

```sh
echo 'pr 90350 4' > /sys/kernel/debug/zx279133-9132
```

板子立刻死了。串口里：

```
Unable to handle kernel paging request at virtual address ffff8000870160ee
  ESR = 0x0000000096000021
  EC  = 0x25: DABT (current EL), IL = 32 bits
  FSC = 0x21: alignment fault
  x1  = 0x00000000000160ee
  x3  = 0x00000000000160ee
  Code: 88dffc00 88dffc00 00000000 00000000 (b9400000)
  pc : readl+0x0/0x20
  lr : zx279133_9132_write+0x1520/0x2280
  Call trace:
   readl+0x0/0x20 (P)
   full_proxy_write+0x60/0x98
   vfs_write+0xac/0x3c4
   ...
  Kernel panic - not syncing: Oops: Fatal exception
  Rebooting in 3 seconds..
```

`Rebooting in 3 seconds..` 之后**没有再起来** —— 串口无任何输出，ARP 表里
192.168.9.1/.2/.3 全是 `Unreachable`/`Incomplete`。板子僵死到插座断电才恢复
（11:57:34 重新上电，新内核正常起来了，固件没事：panic 是运行期 fault）。

> 顺带记一条判据：PowerShell 的 `Test-Connection` 对**没有 ARP 条目**的地址会
> 误报 ALIVE。判断板子死活要看 `Get-NetNeighbor` 的 State。

### 3.2 根因：两个错叠在一起

**(a) `pr` 漏了对齐守卫。**

`regs` / `reg` / `regw` 一直有一道检查（`zx279133-np.c` 第 1385 行），注释里
还记着上一轮的事：

```c
/*
 * The NPPT window is device memory, so a read or a write that is
 * not four-byte aligned is an alignment fault, and it takes the
 * kernel down with it.  An unaligned offset is refused here
 * instead: round 133 read one at +0x40002 and the board panicked
 * in the middle of the round.
 */
if (off & 3)
	return -EINVAL;
```

本轮新加的 `pr` **没有抄这道守卫**。PPS 窗口是设备内存，arm64 上未对齐的
`readl()` 就是 alignment fault，直接带走内核。

**(b) 文档里的示例是十进制。**

`pr` 的偏移走 `kstrtou32(tok, 0, ...)`，base 0 ⇒ **`90350` 是十进制 90350 =
0x160ee**，而 `ZX279133_PPU_CLUSTER_ME_TABLE` 写的是 **`0x90350`**。两者差一个
数量级，而十进制那一个 `0x160ee & 3 = 2` —— **连字对齐都不是**。

所以这一行命令同时犯了两个错，而 (b) 是 (a) 之所以能触发的那个具体原因：
`0x90350` 本来是对齐的。

---

## 4. 审计：`pr` 不是孤例

顺着这条线把同一批动词全查了一遍 —— **凡是「用户给的偏移直接进
`readl()`/`writel()`」的出口**，都得有对齐守卫。查出 6 处缺（`pr` 是本轮
实崩的那一处）：

| 动词 | 出口 | 缺什么 | 触发例 |
|---|---|---|---|
| `pr` | PPS + off | 对齐 | `pr 90350 4`（**本轮实崩**） |
| `nr` / `nd` / `nw` | NPPT + off | 对齐 | `nr 8001` |
| `nwp` | NPPT + at | 对齐 | `nwp 8001 0x1` |
| `soc` / `socw` | 任意绝对地址 | 对齐 | `soc 0x16000001 1` |
| `cam <off> <n>` | se_hash + off | 对齐 | `cam 1 4` |
| `xbasex` | NPPT + base | 对齐 + **边界会溢出** | `xbasex 0xfffff000 2` |

另外两处边界检查本身有整数溢出，与对齐无关，一并修掉：

* `pr` / `cam` 的上界写成 `off > size`，`off == size` 越界一个字 → 改 `>=`；
* `soc` 的白名单比较 `addr + len <= start + size`，`addr` 来自用户、可以是
  0xffffffff，加法绕回小值 → 改成「距离块首还剩多少」；
* `xbasex` 的 `base + 0x7e0004 + 4 > size` 同理绕回 → 改成「最后一个字还在
  窗口内」。

### 4.1 查过但**不需要**加守卫的（逐个查过，不是漏看）

| 一类 | 为什么安全 |
|---|---|
| `m` / `mw`、`m45` / `m45w`、`xpcs` / `xpcsw` | 走 MDIO 控制器，不做裸偏移访问 |
| `v` / `vw`、`er` / `ew`、`fa`、`et`、`es` / `eg` | 走 ERAM 间接通道（`smmu0`） |
| `bppd` / `bppw` | 偏移是 `2*i` / `4*i` 这样的固定倍数 |
| `redqr` | 走 RED 的间接端口（`red_ind_read`） |
| `rxd` / `rxb` | 读的是相干内存里的描述符，不是设备内存 |

---

## 5. 改了什么

* 新增 `zx279133_9132_off_aligned()` 一个守卫，7 个出口统一用它（`regs` 原本的
  `off & 3` 也改用它，语义不变）；
* `pr` / `cam` 的上界 `>` → `>=`；
* `soc` 白名单比较改成不溢出的写法；
* `xbasex` 边界改成不溢出的写法，并补对齐守卫；
* `pr` 的文档示例 `pr 90350 16` → **`pr 0x90350 16`**，并在代码注释里把
  base-0 的语义讲清楚；
* docs/95 里被证伪的两处 `pr 90350 16` 一并订正（§4.2 正文与 §7 复现清单）。

### 5.1 一条留给下轮的经验

**用户态动词的偏移一律写十六进制。** 全仓库 `kstrtou32(tok, 0, ...)` 的
base-0 惯例是对的（`0x90350` 是十六进制、`90350` 是十进制），但**在文档和
示例里，裸数字最容易被读成十进制**。凡是「这个数字长得像寄存器偏移」的，
文档一律带 `0x`。

---

## 6. `mes` 写进去的值没有回读出来（**已定案 → docs/97**）

`mes 7 3` rc=0，但回读 flow 7 的引擎号仍是 0：

```
ppu me word 1 = 0x00000001  flows 4:1 5:0 6:0 7:0
```

两种可能，**本轮没有分清**：

1. **写入未生效** —— 硬件侧有门控。厂商那一批函数（`pps_glb_set_ppu_core_agclk_en()`
   等）都在动 `ZX279133_PPU_AGCLK_CFG`（PPS + 0x1c），本驱动自己的
   `zx279133_smmu0_read/write()` 也会在 `agclk == 1` 时临时清掉 `BIT(0)`。
   ME 表的写是否也必须这样开门，**没有证据**。
2. **位布局本就如此** —— 即 `me` 打印的那 16 个字不是 ME 表本身。

已经排除的：

* **不是软件算错**：厂商 `ppu_cluster_set_me_val_by_flow_num()`（np_133.ko
  @0x829dc，capstone 反汇编）的布局与本实现**逐位一致**（字 = `flow>>2`、
  槽 = `(flow&3)*8`、8 位/槽）。
* **不是表没被驱动写过**：probe 时 `zx279133_ppu_cluster_runtime_prepare()` 写
  的 `0x00000001` 是**生效的** —— `me` 读回 16 个字全是 `0x00000001`，而启动
  日志里的出厂值全是 `0x00000000`。

⇒ 下一轮要做的是：**先 dump 出厂值，再写，再读**，单独一轮，失败即断电复位
（机主已授权读写 ME 表与 PPS 中断相关寄存器）。如果写确实不生效，第一嫌疑是
`AGCLK` 门控，第二嫌疑是厂商的 `ppu_cluster_set_me_select_en()` /
`ppu_cluster_set_thread_swicth_en()` 这两个本轮没做的步骤。

---

### 6.1 结论（2026-11-01 补，见 [docs/97](97-the-me-table-is-read-only-20261101.md)）

上面那轮实验做完了，**上面列的两个可能都不是原因**：

* ME 表那 16 个字（PPS + 0x90350..0x9038C）**不是 CPU 可写的配置寄存器**，
  而是 PPU cluster 自己那张「flow → ME」分派表在 CPU 侧的**只读镜像**。
  写入值在任何一次采样里都没出现过；同一窗口的邻居（0x9045c，pkt-type detail）
  写进去就读得出来，所以「PPS 窗口整体只读」被排除。
* 位布局也不是问题：厂商 `ppu_cluster_set_me_val_by_flow_num()` 的布局与本实现
  逐位一致（字 = `flow>>2`、槽 = `(flow&3)*8`、8 位/槽）。
* 厂商自己那条赋值路径**够不着这张表**：它在寄存器描述符表里用的是 id 66，
  而 id 66 那一行是**整行全 0 的占位行**，访问层要求 `row->id == id`，直接被拒。
* 引擎分派真正的落点在 **PPU 微码 blob 里的 64 条 PORT_FLOW（tag 0xbbbbbbbb）**，
  不在这个寄存器窗口。

⇒ `mes`（可编程）在这组寄存器上做不到；`me`（可观测）成立。要让引擎分派可编程，
得动微码 blob 的 PORT_FLOW 注入，那需要改驱动并重新出 CI。

---

## 7. 复现

```sh
# 板子（只读）
echo fg  > /sys/kernel/debug/zx279133-9132        # flood group 表
echo me  > /sys/kernel/debug/zx279133-9132        # ME 表
echo pr 0x90350 16 > /sys/kernel/debug/zx279133-9132   # PPS 窗口（偏移是十六进制！）
echo cam > /sys/kernel/debug/zx279133-9132        # CAM/算法块
grep -i -e 19000000 -e 18000000 /proc/interrupts
dmesg | grep -c 'switch: lane: far'
```

⚠️ 修复前，**`pr` 的偏移必须带 `0x` 且是 4 字节对齐的**，否则板子会 panic：
`pr 0x90350 16` 安全，`pr 90350 4` 会崩（见 §3）。

ℹ️ **该缺陷已修复**（提交 `fa6f99e`，随 run 302 的镜像上板）：对齐守卫统一到
`zx279133_9132_off_aligned()`，`pr` / `nr` / `nd` / `nw` / `nwp` / `soc` / `socw` /
`cam` / `xbasex` 九个出口共用。现在未对齐的偏移被拒在门口并返回 `-EINVAL`，
板子不会崩——run 302 实测 `pr 0x90351 1` 与 `pr 90350 1` 都是 rc=1（见 docs/98 §3.1）。

---

## 8. 板载教训（这一轮踩出来的）

* **插座节拍要从串口日志的 relay 标记读**，不能用 `watch-state.json`（那个
  文件已经过时）。实测节拍：**上电 20 分 / 断电约 30 秒 / 周期恒定 1230 秒**。
  11:37:04 上电 ⇒ 11:57:04 断电 ⇒ 11:57:34 上电。
* **`board_reset`（继电器 DTR/RTS）对这块板无效**；
  `echo b > /proc/sysrq-trigger` 会让板子停在 U-Boot 之前、完全静默。
* **U-Boot 倒计时只有 1 秒**，`'u'` 中断流必须在**断电之前**布好
  （`tools/armu.sh` 每 0.25 s 写一个 `'u'` 到 `staging/ctl/sercmd.txt`，
  ubflash 见提示符后 `touch u.stop` 自动停流）。断电后才开始灌 `'u'` 抢不到。
* **U-Boot 的 ping/tftp 会顺带初始化 RTL8226**；不经过这一步板子双向都哑。
* **PC 唯一接在 eth0**（外接 RTL8226 2.5G 口）；`lan-cpu0` 不在 br-lan 里，
  `ip link set lan-cpu0 master br-lan` 被驱动拒绝。
* **板上 `wget` 的 `Failed to send request: Operation not permitted` 不等于网络
  不通**；要以 ping/ARP 为准。
* **长任务必须用后台任务托管**，不能靠 `Start-Process` —— Windows 上它起的
  子进程会随那次工具调用结束被回收（现象：文件里有输出但无进程、无进展）。
