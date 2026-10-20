# 补百零七：刷好的标准是「冷启动后网络双向能通」——root 的 jffs2 dirent 损伤与本次修复

日期：2026-10-20，本地 19:41–21:00。设备：SK-D840N4（ZX279133 GPON ONU），TTL-USB 在 COM3，
网线插在 lan 口，智能插座每约 15 分钟断电 30 秒（实测落点在 :09/:24/:39/:54，不是整刻钟）。

## 0. 结论先说

flash 里现在这套（社区 `boot.bin` U-Boot 未动 + 4.19.136+ `uImage`/`board.dtb` + OpenWrt 24.10.8 jffs2 root）
**连续两次纯冷启动都干净，并且双向带流量**：

| 观测 | 20:39 那次 | 20:54 那次 |
|---|---|---|
| `Linux version 4.19.136+` / `VFS: Mounted root (jffs2 filesystem) readonly on device 31:5` | 是 | 是 |
| dmesg 里 `Not a directory` / `nonexistent directory` 行数 | **0 / 0** | **0 / 0**（ssh 进去再数还是 0） |
| procd、`Please press Enter to activate this console`、`br-lan` eth1/eth2/eth3 forwarding、`link becomes ready` | 是（73 s） | 是（32 s） |
| PC `ping -S 192.168.9.25 192.168.9.1` | 4 发 4 收 0% 丢 TTL=64 | 4 发 4 收 0% 丢 |
| 板内 `ping 192.168.9.25` | 2/2，1.5–2 ms | — |
| PC 侧 `pcrxwatch` rx_delta | 每 5 s 370–430 B（此前一整周恒为 0） | — |
| TCP 22/80/443 | 开 | — |
| `ssh root@192.168.9.1`（密钥在重建目录时保住了） | `Linux OpenWrt 4.19.136+ … aarch64`，`br-lan 192.168.9.1/24` | 同 |
| LuCI `GET /` | 200 | 200 |

**没有擦 root。** 也就是说：这不是"回滚到出厂"，是把被我们自己写坏的那套东西修回去了。

## 1. 损伤是什么（机制，不是形容词）

`root`（mtd5，241 MiB，jffs2）被两套 jffs2 实现写过：厂商/社区 4.19 装的，和 10-13～10-19 期间
mainline 6.18 反复写的。**4.19 内核会把 mainline 写过的那些名字解析成"不是目录"**，于是
`open: Not a directory` 每次启动刷约 800 行；把 `/bin` 修好之后，它一次死在下一层：

```
/etc/preinit: line 16: can't create /dev/null: nonexistent directory
/etc/preinit: line 0: can't open '/dev/null': Not a directory
```

同一块分区在 mainline 下读得完全正常（`mount -t jffs2` → `/bin` 59 项，`/mnt/r/bin/sh` 能以 uid 0 执行），
mainline 自己只在扫描时报一条 `Node CRC failed on node at 0x0e4c17f4`。**字节没问题，是两套实现对同一个
名字留下两份 dirent 后各自挑了不同版本。**

## 2. 修复：新 dirent 没用，必须新 inode

这条是本次最值钱的测量，因为它否掉了一个当时最讲得通的解释：

* **写新 dirent = 无效。** 19:54 的 `tools/binfix.sh`：对 14 个顶层目录做 `mv dev ._dev; mv ._dev dev`，
  28 步全部返回 0，改名后内容清点无误（bin 59 / etc 64 / lib 24）。下一次 4.19 冷启动**照旧**卡在 `/dev/null`。
* **新 inode = 有效。** 先是对 `/bin` 做删除+重建（刷量从 ~800 行降到 1 行，`/etc/preinit` 开始跑）；
  20:31–20:33 用 `tools/reflush_root.sh` 对全部 14 个顶层目录做同一件事，内容先经 RAM 暂存再搬回去：
  `FND-<dir>-1-preN-postN`，前后条目数逐个相同——`etc 64/64`、`sbin 58/58`、`lib 24/24`、`root 5/5`、`dev 1/1`。

所以判定式很简单：**同一个 name 只要还是老 inode，4.19 就继续挑错的那份；把它换成新 inode 才修好。**

## 3. 顺手补上了这个项目一直缺的保险

网络一通，第一件事是把 `root` 完整导出——它一直是"从没 dump 过、所以擦了就真没了"的那块：

* `dd if=/dev/mtd5 bs=65536 | gzip -1` = 11,347,306 B，md5 `6954ef8a026697ee1903bc1431e6353a`，
  板端与 PC 端一致 → `backups/root-20261020-2045.bin.gz`
* `sysupgrade -b` 配置包 7,366 B，md5 `2b63f7984bb5fd7d2c6c52b4d23b0a31` → `backups/config-20261020-2100.tgz`
* 干净镜像回退包仍在 `staging/reflash-root/`：root 填充 md5 `bb6025d9` == 10-09 安装时那份 `.pad`；
  `uImage`/`dtb` 的填充 md5（`f68ceb0f`/`881af2d3`）与 flash 现存字节一致。
  `DIR=staging/reflash-root STAGES=root tools/ubflash.sh` 随时可执行——但现在**不需要**，且它已不再不可逆。

## 4. WAN：这次没做，而且原因很具体

`ip route` 只有两条 /24，没有默认路由。PON 侧测到的是：

* 光**有**：dmesg `rx los =0 rx data in`；`pon mode is xg`、`gpon_Init ok`、光学芯片 UX3360/UX3361 读表成功；
  `gpondrv_task`/`gpondrv_scan_th` 两个内核线程在跑；`/proc/pon/upmode: 64`、`up_mode_info: 0x40`。
* 但 `pon`/`omci` 两个 netdev `operstate=down`、RX/TX 计数 0；`/etc/config/network` 里**没有任何 interface 引用 pon**
  （`wan` 是 `eth0` 上的静态 192.168.9.2）。
* 全盘搜 `*omci*` / `*pon*`：只有 `/etc/modules.d/` 里那三个模块声明，**一个 OMCI/注册用户态程序都没有**，
  `/usr/data` 是空的。

结论：这套社区 root 带的是 PON **内核模块**，不带 PON **用户态**（PLOAM/OMCI 注册流程）。

**21:34 更正（本文件上一版说错了一句，那句是错的）**：厂商那份用户态**在盘上**。
`restore_set/root_stock.bin`（252,706,816 B = 社区 `root` 分区 241 MiB，10-09 装社区 root 之前 dump 的）
在文件偏移 `0x1300000`（绝对 `0x2200000`，正好是 stock `kernel1` 槽起点）处保留了**完整的 stock kernel1**：
ZTE 容器 magic `99999999 44444444` + FIT（totalsize `0x126c84b`）+ 三个子镜像
`kernel@133`(gzip 4,814,954 B)、`fdt@133`(49,307 B)、**`initramfs@133`(LZMA 14,452,253 B → newc cpio 54,466,560 B，
1849 条记录、1012 个文件)**，里面有 `bin/gpon_omci`(889,128 B)、`bin/sigpon`、`bin/nas_main`、`bin/cspd`、
`bin/osgid`、91 个 arm32 库（glibc 2.26 + `liboss/libdb/libdbview/libdevinfo/libenv…`）和 `etc/GPON_GN28L95_datas/*`
光学 LUT。已切出来放在 `staging/stock-restore/`（md5：Image.gz `b456a47a…`、Image `b61edb4c…`、
board.dtb `1a3d6a53…`、initramfs.lzma `6ed79f88…`）。

**而且它能在这台机器上跑起来。** 厂商二进制是 **32 位 ARM**，现用内核是 aarch64，但
`/proc/kallsyms` 里有 `el0_sync_compat`/`el0_fiq_invalid_compat` ⇒ CONFIG_COMPAT 是开的。实测（21:33:12，
全部在 `/tmp` 里，没写 flash）：把 vendor 的 lib 目录 + soname 软链接配好之后，
`ld-2.26.so --library-path … gpon_omci` **成功执行并打印了它自己的横幅**：

```
====================gpon_omci start======================
[ut_main]$$$ omci OSSStart error:-1!
```

缺的设备节点也都在现内核里注册着：`gpondrv_dev=102`、`comdev=158`、`zxled=97`、`gponsdk_dev=111`、
`switch_dev=104`（mknod 用的是活_MAJOR_，不是 stock 的编号）。`OSSStart` 返回 -1 是因为它在等厂商的
应用框架（`nas_main`/OSS 那一层），也就是说**注册不是一个 daemon 的事，而是要把 vendor 的 app server 起起来**。
期间 `omci pkt cnt`、`txd frm cnt`、`pon` 的 RX/TX 仍是 0。

所以 WAN 现在有两条**都建立在盘上实物**的路线，而不是"只能自己从零写 OMCI"：

1. **整机回到厂商固件**：`restore_set/` 已经是完整一套（boot/kernel/dtb/param/root 五件），且我们还能从
   root_stock 里切出 stock kernel1 的 FIT——这条路会把 GPON 注册、光猫功能一起带回来，代价是现代 OpenWrt 没了。
2. **在现系统上把 vendor 的 app server 起来**：compat、库、设备节点都已验证可用，剩下的是
   `nas_main` 及其配置数据库（`libdb`/`libdbview`/XML）与 OpenWrt 的 netifd/firewall 共存问题。

无论哪条，都要动 flash 或动系统配置，属于不可逆/半可逆操作，**等机主点头再做**；`root` 已 dump、
`config` 已备份，两向都能回。

## 5. 本轮踩到的工具/脚本坑（下次别再踩）

* 这块 RAM root **没有 `stat` applet**：`stat -c %a` 回 `-ash: stat: not found`（20:30:52）。用 `wc -c < f`、
  `ls -ld d | cut -c1-11`。而且当时那句带了 `set -e`，所以整段在 `rm` **之前**就退出了——否则就是误删目录。
* bash 里 `local n=${2:-60} end=$(( $(date +%s) + n ))` 在 `set -u` 下报 `n: unbound variable`：算术式在 local
  赋值生效前就展开了。20:25:36 那轮就是这么死掉的，当时串口另一头的 shell 是活的。声明必须拆成两句。
* `Get-CimInstance … CommandLine -match '<模式>'` 会把**发起这条命令的 shell 自己**也匹配上；用它 pattern-kill
  会把自己杀掉（20:26:11 返回 exit -1，顺手杀掉了刚武装好的那轮）。要先按 `Name -eq 'sh.exe'` 过滤、打印命令行、
  再按明确 PID 杀。
* 控制台只有约 11.5 KB/s：`tar` 里塞 `etc root lib/modules probe bin www` 等于整个 11.8 MB root，base64 要 17 分钟，
  一个窗口必爆。只有 `etc root probe` 是这台 PC 上没有的（其余都在 `staging/rootfs.jffs2` 里）。
  `tools/rootsave.sh` 现在先量大小、超预算就明确拒绝，并且改成 `mount -t jffs2 -o ro`。
* 断电落点是 **:09/:24/:39/:54**（由 20:08:55、20:24:48、20:39:55 三个启动时间反推），不是之前记录的整刻钟。
  武装 `armu4.sh` 要跨过预测秒，别按"15 分/30 分"等待。

## 6. 判据（写给下一次接手的人）

1. 先读串口再下结论：`tools/boot419score.sh` 会把 `Not a directory` / `nonexistent directory` / `Mounted root` /
   `panic` 逐条计数——**计数才是测量，最后一行不是**。
2. 看到 `Not a directory` 刷屏，先 `tools/ubram.sh`（RAM 起 mainline）再 `tools/reflush_root.sh`（新 inode 重建），
   然后让**下一次自动断电**去验证；不要一上来就擦分区。
3. 只要有过 mainline 以读写方式挂载这块 root，就必须用一次 4.19 冷启动来验收，再相信 LAN。
4. 网络一旦通，别只用串口：ssh/scp 到 192.168.9.1 是快通道，串口留给启动期证据。
