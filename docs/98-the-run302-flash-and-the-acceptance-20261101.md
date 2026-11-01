# run 302 刷机与冷启动验收实录（2026-11-01）

## 0. 一句话

CI #302（head `f8e708e`，含对齐守卫 `fa6f99e`）绿之后，这一轮把 **kernel + root 两段**
写进了板子的 flash，**crc32 逐段回读全通过**；启动后 `me` / `mes` / `pr` / `cam` 四个动词
可用，**`pr` 的对齐守卫按预期生效**——`pr 0x90350 16` 正常读出 ME 表 16 个字，
`pr 0x90351 1` 与 `pr 90350 1` 都被拒（`-EINVAL`）而**没有 panic**，
`dynamic_debug/control` 在、`tc`/`nft` 可用、周期刷屏 `switch: lane: far` 计数为 **0**。

**dtb 段没有重刷**：板上 `mtd3` 的 md5 与 run 302 的 dtb 分区整块 md5
**逐字节相同**（`2faac7300842bc08c6fca68b6071d639`），重刷它是一次没有收益的擦写。

同时订正了 docs/96 里两处被这轮证伪/说松了的表述（§4）。

---

## 1. 这一轮的输入

### 1.1 CI

| run | id | head | 状态 | 时间（UTC） |
|---|---|---|---|---|
| #302 | 41272065394 | `f8e708e` | **success** | 04:10:52 → 05:27:43（76.9 min） |
| #303 | 41274437132 | `30329eb` | initramfs + flash 两个 job 均 **success** | 04:47:26 起 |

release 标签规则 `skd840n_${matrix.mode}_${run_number}_${run_id}` ⇒ 本轮
`skd840n_flash_302_41272065394`。

### 1.2 产物校验（`sha256sums-release` + 本地补齐算 crc）

| 载荷 | raw | padded | rawcrc | **padcrc** | md5 |
|---|---|---|---|---|---|
| `flash-uImage` | 0x7f27be | 0x7f2800 | e4609253 | **d9642383** | e4d1cfbc1581706560db61bf528bd3c0 |
| `flash-dtb.bin` | 0x3445 | 0x3800 | 456a0cce | **800ef7f6** | ee287b5c1bc47c26c35cdaa2a462176d |
| `flash-rootfs.jffs2` | 0x4a0004 | 0x4a0800 | ead60093 | **87ea7d67** | cee50ec38788643df7b8db15e97ce7bd |

`ff4096 crc = f154670a`（这是「分区被擦干净」的判据：连续 4096 字节全 0xFF 的 crc32）。

### 1.3 dtb 不重刷的判定（这是本轮唯一一次「少刷一段」）

板上实测：

```sh
root@ImmortalWrt:~# md5sum /dev/mtd3
2faac7300842bc08c6fca68b6071d639  /dev/mtd3
```

PC 端算出的 run 302 dtb 分区整块应有的 md5（`ub-flash-dtb.bin.pad` 的 14336 B
再补 0xFF 到 1 MiB）：

```
2faac7300842bc08c6fca68b6071d639
```

**两者逐字节相同** ⇒ 板上的 dtb 已经就是 run 302 的 dtb，写它等于把同样的字节再擦一遍。
`flash-dtb.bin` 的 md5 与 run 300 的 `ee287b5c1bc47c26c35cdaa2a462176d` 也完全一致，
两边对得上。

⇒ 授权段落定为 **kernel + root 两段**。dtb 分区不碰。

### 1.4 许可行

```
ts=2026-11-01T05:33:52Z run=302 proven=operator-waiver stages=kernel root unpinned-by-design=no padded_md5 uimage=7f00bf0f58430256fcef7e45cbe50728 dtb=1b0a5c8cebe9699e8d23bf8b75d155cf root=28fe8973c3410a47682385ebec092efa kit=run302 unproven=net-verdict,root-writable,cold-boot-with-this-kit,root-backup-off-board rollback=vendor:staging/kernel_stock.bin+staging/dtb_stock.bin note=owner指令:提交代码推送远端_等待ci完成_并刷入(2026-11-01T01:20+0800);本轮run=302;dtb不重刷:板上mtd3的md5=2faac7300842bc08c6fca68b6071d639与run302的dtb分区整块逐字节相同;本轮只写kernel+root两段;kernel回滚=staging/kernel_stock.bin+staging/trial/flash-uImage.run300;root回滚=staging/trial/flash-rootfs.jffs2.run300即板上当前root实况镜像+backups/preflash-20261101/mtd5-root.bin.gz
```

回滚路径是实的：`staging/kernel_stock.bin`（8 MiB）与 `staging/dtb_stock.bin`（1 MiB）
都在；root 的回滚镜像是 `staging/trial/flash-rootfs.jffs2.run300`（板上当前 root 就是它）。

---

## 2. 刷机实录

### 2.1 插座节拍

插座周期恒定 **1230 s**（ON 20 分 / OFF 约 30 秒）。本轮实测上电时刻：

```
11:37:04  11:57:34  12:18:04  12:38:34  12:59:04  13:19:34  13:40:04  14:00:34
```

13:40:04 这次是刷机窗口；13:19:34 → 13:40:04 正好 1230 s。

### 2.2 中断流的布置

`ARM=1` 必须在**插座断电之前**就把 'u' 流布好——这块 U-Boot 的 autoboot 倒计时只有 1 秒，
事后再发的流赢不了它。

```
13:34:20  新 relay 起（COM3，3600 s 租期；旧 relay 用 serstop 优雅退出，COM3 才释放得掉）
13:34:47  ubflash 启动：padding → permit ok（22 行，run=302）→ 三个 pad md5 全对
          → 授权 [kernel root]、本轮写 [kernel root] → UDP/69 在 pid 57384
          → armu pid 354375 开流
13:39:34  插座断电（板子最后一个回显正是 13:39:34 的 'u'）
13:40:0x  插座上电，'u' 流把板子停在 U-Boot 提示符；u.stop 升起，armu 以 442 次收工
```

### 2.3 两段的写入与回读

每段四道闸门：下载 crc、擦除后头尾 4 KiB（必须等于 `ff4096` 的 crc）、写入成功标记、
**回读 crc 必须等于本机算出的 padcrc**。

```
13:40:16  tftpboot 0x89000000 ub-flash-uImage.pad
13:40:27  crc32 0x89000000 0x7f2800  ==> d9642383          ← 与 padcrc 一致
13:40:32  mtd erase kernel            （64 eraseblock，0x00000000..0x007fffff）
13:40:36  头 4 KiB crc ==> f154670a   ← 全 0xFF
13:40:42  尾 4 KiB (0x7ff000) crc ==> f154670a
13:40:45  mtd write kernel 8333312 B (4069 page(s))
13:40:51  回读 0x7f2800 B crc ==> d9642383                 ← 写进去的就是本机那份

13:40:56  tftpboot 0x89000000 ub-flash-rootfs.jffs2.pad
13:40:59  crc32 0x89000000 0x4a0800  ==> 87ea7d67
13:41:04  mtd erase root              （1928 eraseblock，0x00000000..0x0f0fffff）
13:41:10  头 4 KiB crc ==> f154670a
13:41:16  尾 4 KiB (0xf0ff000) crc ==> f154670a
13:41:19  mtd write root 4851712 B (2369 page(s))
13:41:24  回读 0x4a0800 B crc ==> 87ea7d67
13:41:29  printenv mk -> DONE-root
```

**全程 13:40:09 → 13:41:29，80 秒**，远在 ON 窗口（13:40:04 → 13:59:34）之内。

`mtd1 boot` 与 `mtd4 parameter tags` **从未被触碰**。

### 2.4 起新装的镜像

用的是 `run bootcmd`（**不是 `boot`**——输入缓冲残留会拼成 `cho`）：

```
13:41:56  run bootcmd
          Reading 8388608 byte(s) (4096 page(s)) at offset 0x00000000
          Reading 1048576 byte(s) (512 page(s)) at offset 0x00000000
          ## Booting kernel from Legacy Image at 88000000 ...
             Image Name:   OpenWrt mainline 6.18.38 sk-d840
             Data Size:    8333182 Bytes = 7.9 MiB
             Verifying Checksum ... OK
13:41:58  内核起来
```

**`Data Size: 8333182` 是判定「板上跑的确实是 run 302」的硬证据**：uImage 头 64 字节，
8333182 + 64 = **8333246** = `flash-uImage.run302` 的精确长度。日志里更早的几次启动
打的是 `Data Size: 8333581`（+64 = 8333645 = run 300 的长度），两者不会混淆。

---

## 3. 验收结果

### 3.1 动词

| 项 | 结果 |
|---|---|
| `me` | ✅ rc=0，16 条 `ppu me word N = 0x00000001  flows a:b ...` + `ppu pkt-type detail = 0x00000001` |
| `mes 7 3` | ✅ rc=0（写不生效是设计如此，见 docs/97） |
| `cam` | ✅ rc=0 |
| `pr 0x90350 16` | ✅ **rc=0**，dmesg 打出 16 行 `pps reg 0x090350 .. 0x09038c = 0x00000001` |
| `pr 0x90351 1` | ✅ **rc=1**（`ash: write error: Invalid argument`），**没有 panic** |
| `pr 90350 1` | ✅ **rc=1**（同上），**没有 panic** |

后两项正是 `fa6f99e` 那个对齐守卫要的效果：run 300 的板子是被 `pr 90350 4` 读崩的
（docs/96 §3），现在同一类输入被拒在门口。

### 3.2 用户态与可观测性

| 项 | 结果 |
|---|---|
| `/sys/kernel/debug/zx279133-9132` | ✅ 存在（`-rw-------`） |
| `/sys/kernel/debug/dynamic_debug/control` | ✅ 存在（`-rw-r--r--`） |
| `dynamic_debug` 里 `switch: lane` 的调用点 | ✅ **0 条** |
| `dmesg \| grep -c 'switch: lane: far'` | ✅ **0**（周期刷屏归零） |
| `dmesg \| grep -c 'switch: lane'` | 15（4 条 `lane pre-serdes:` + 11 条 `lane after:`，全在 t=1.59..2.66 s 的一次性转储） |
| `dmesg` 里 dev_info 级的 switch/组播行 | 80（正常信息量，不是刷屏） |
| `tc` | ✅ `iproute2-6.18.0`；`br-lan` 上 `qdisc mq` + 两条 `fq_codel` |
| `nft` | ✅ `table ip nat`（eth0 上的 masquerade）+ `table inet dnsmasq` |

### 3.3 分区

```
dev:    size   erasesize  name
mtd1: 00200000 00020000 "boot"            ← 未触碰
mtd2: 00800000 00020000 "kernel"          ← 本轮写入，crc 回读通过
mtd3: 00100000 00020000 "dtb"             ← 未触碰（本就与 run 302 相同）
mtd4: 00400000 00020000 "parameter tags"  ← 未触碰
mtd5: 0f000000 00020000 "root"            ← 本轮写入，crc 回读通过
mtd6: 00100000 00020000 "scratch"
```

---

## 4. 对 docs/96 的订正

### 4.1 「`dmesg | grep -c 'switch: lane'` = 0」这行命令写错了

docs/96 §2.1 的表里写的是 `dmesg | grep -c 'switch: lane'` → 0，紧接着又说
「那 15 条是**不同字符串**的一次性诊断行」。这两句不可能同时成立，实测也不成立：

* 周期刷屏行的原文是 `switch: lane: far an=0x00ad 758d=0x801e 7580=0x9416`，
  **整个串口日志里这条模式共 5033 行**；
* 一次性诊断行是 `switch: lane pre-serdes: ...` 与 `switch: lane after: ...`，
  两者**共享 `switch: lane` 前缀**，不同的只是后缀。

所以 **0 属于 `grep -c 'switch: lane: far'`**，而 `grep -c 'switch: lane'` 是 **15**。
本轮板上四个口径同时量过：

```
periodic_lane_far=0     ← 刷屏，真·判据
all_switch_lane=15
pre_serdes=4
after=11
```

⇒ docs/96 的表、那句「不同字符串」的说明、以及 §7 复现命令里的
`grep -c 'switch: lane'` 都已改成 `grep -c 'switch: lane: far'`。

**附带的一条统计陷阱**：在 PC 端拿串口日志统计时，我自己敲的命令回显里含有
`switch: lane` 这个字面量，会被一并数进去——本轮第一次扫出来是 38，
扣掉 23 行命令回显才是真实的 15。

### 4.2 §6「未判定」→ 已定案

docs/96 §6 当初列的两个可能（AGCLK 门控 / 位布局不是那样）**都不是原因**，
结论见 docs/97，并在 §6 补了 §6.1 指过去。

### 4.3 `accept302.sh` 里「裸 `pr` 应写成功」这条预期是错的

驱动里 `pr` 的用法是 `pr <off> <n>`，**两个参数都必须给**（源码注释里写的就是
`"pr 0x90350 16"`）。裸 `pr` 走的是 `kstrtou32(tok, 0, &off)` 失败 ⇒ `-EINVAL`，
这是**设计如此**，不是缺陷。验收脚本第 2 项按「四个动词都应写成功」来写，
只有 `pr` 这一条预期不成立。

### 4.4 base-0 的坑又咬了一次

`pr 90350 1` 里的 `90350` 是**十进制** 90350 = 0x160ee，未对齐，所以被守卫挡下。
但错误信息只有一句 `-EINVAL`，看不出「你少写了 `0x`」。文档与脚本里所有偏移一律带 `0x`。

---

## 5. 冷启动

插座在 13:59:34 断电、14:00:34 上电（周期 1230 s，与 12:59:04 / 13:19:34 / 13:40:04 一致）。
这一轮**没有**再布 'u' 流、也没有 `run bootcmd`：板子自己跑完 U-Boot 那 1 秒倒计时，
从刚写进去的分区启动 —— 这才是「装进去的这份镜像自己能把板子带起来」的证明。

```
14:00:34  relay 记 "autoboot: a boot is starting" —— U-Boot 2021.01-svn308776
          Data Size: 8333182 Bytes = 7.9 MiB          ← 同一份 run 302 内核
14:00:38  内核起来
   9.16  VFS: Mounted root (jffs2 filesystem) readonly on device 31:5   ← 新 root 分区挂上来了
  11.46  switch: the 2.5G jack's PHY: auto-negotiation is complete (status 0x0049)
  13.44  switch lan1: Link is Up - 1Gbps/Full
  19.61  zx279133-eth eth0: Link is Up - 1Gbps/Full
```

### 5.1 冷启动后的完整验收（uptime = 167.83 s）

| 项 | 结果 |
|---|---|
| `uname -r` | `6.18.38+`（`#0 SMP Mon Oct 26 04:58:01 2026 aarch64`） |
| `me` | ✅ rc=0，16 条 `ppu me word` + `ppu pkt-type detail = 0x00000001` |
| `cam` | ✅ rc=0，`cam: alg crc 0x00000000 bulk 0x00000002 depth0 0x00000012 depth1 0x00000000 smmu1 0x0009e100` |
| `mes 7 3` | ✅ rc=0 |
| `pr 0x90350 16` | ✅ rc=0，dmesg **16** 行 `pps reg 0x090350 .. 0x09038c = 0x00000001` |
| `pr 0x90351 1` | ✅ rc=1（`ash: write error: Invalid argument`），**板子照常活着** |
| `pr 90350 1` | ✅ rc=1（同上），**板子照常活着** |
| `/sys/kernel/debug/dynamic_debug/control` | ✅ 存在（`-rw-r--r--`），里面 `switch: lane` 调用点 **0** 条 |
| `dmesg \| grep -c 'switch: lane: far'` | ✅ **0** |
| `dmesg \| grep -c 'switch: lane'` | 15（4 条 pre-serdes + 11 条 after） |
| `tc` | ✅ `iproute2-6.18.0`，`br-lan` 上 `qdisc noqueue 0: root refcnt 2` |
| `nft` | ✅ `table ip nat` → `postrouting` 上 eth0 的 masquerade |

⇒ 验收口径里的每一项都在**冷启动**后的 run 302 系统上过了。

### 5.2 两条台面经验

* **冷启动后的第一条命令会被 shell 登录横幅吞掉**（`BusyBox v1.37.0 ...` + 无 root 密码
  警告 + opkg→apk 对照表）。命令本身执行了，输出被横幅挤掉，看起来像「板子不应答」。
  重发一条就正常 —— 这跟 §2.2 里 `run bootcmd` 之后那次是同一回事。
* **`console_send` 的 `expect` 不要用命令里出现过的字面量**：本轮用 `expect='qdisc'`
  去等 `tc qdisc show` 的结果，结果匹配到的是**命令回显**里的 "qdisc"，脚本提前收尾。
  改用 `echo ACCEPT_""DONE` 这种「回显里不出现字面量」的标记才稳。
  同理，板子回一句 `ash: write error: Invalid argument` 会让 `console_send` 提前返回，
  后面还没跑完的命令的输出就看不到了 —— 分条重发即可。

---

## 6. 还没解决的

* **`mes` 的可编程性**（docs/97 已定案）：ME 表是只读镜像，引擎分派真正的落点是
  PPU 微码 blob 里的 64 条 PORT_FLOW（tag 0xbbbbbbbb）。要让引擎分派可编程，
  得改 blob 的注入 —— 那是改驱动，要重新出 CI。
* **PC ↔ 板子二层不通**仍是台面既有条件（PC 192.168.9.25 / 板 br-lan 192.168.9.1，
  eth0 无 IPv4），所以验收只能走串口。
* **root 段的字节级证据**：回读 crc 是在写入后立刻做的（87ea7d67），但板子起来之后
  jffs2 会往 mtd5 里写（CLEANMARKER 等），所以运行期再 `md5sum /dev/mtd5` 已经不等于
  刚写进去的镜像了；这一段只能靠写入后的回读 crc 认。
