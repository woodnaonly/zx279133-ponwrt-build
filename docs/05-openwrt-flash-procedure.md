# 05 — SK-D840N 刷 OpenWrt 全过程记录（无串口、从存活 Linux 直接写 flash）

> 2026-10-09 执行完成。设备从原厂 Skyworth GPON 固件换成了 `huxiangjs/SK-D840N-OpenWRT` 的
> OpenWrt 24.10.8，**全程没有拆机、没有 USB-TTL**，所有写入都是在原厂 Linux 还跑着的时候
> 用 `mtd_debug` 完成的，写完逐段回读校验才重启。
> 相关文档：`docs/01-flash-offset-map.md`（原厂分区表）、`docs/03-stock-uboot-recovery-probe.md`。

---

## 0. 用到的仓库与下载地址

### 0.1 刷进设备的镜像（社区 OpenWrt）

- 仓库：<https://github.com/huxiangjs/SK-D840N-OpenWRT>（`git clone https://github.com/huxiangjs/SK-D840N-OpenWRT.git`，本地 `sk-d840n-openwrt/`）
- Release：**v2.0**，发布于 2026-09-10T15:54:26Z，资产 `firmware_v2.0.zip` = **16,060,875 B**
  直链：<https://github.com/huxiangjs/SK-D840N-OpenWRT/releases/download/v2.0/firmware_v2.0.zip>
  列表页：<https://github.com/huxiangjs/SK-D840N-OpenWRT/releases>（还有 **v1.0**：`.../releases/download/v1.0/firmware_v1.0.zip`，16,060,821 B，本次未使用）
  资产名/大小/时间是从 API 核过的：<https://api.github.com/repos/huxiangjs/SK-D840N-OpenWRT/releases>
- 演示视频（README 里给的）：<https://www.bilibili.com/video/BV1EMbR6tEZ7>

真正写进 flash 的四件东西，以及它们各自的出处：

| 文件 | 出处 | 大小 | md5 |
| --- | --- | --- | --- |
| `boot.bin` | repo `binary/boot.bin` | 2,097,152 | `daa42186c3b2c5be70a749cbfd9dd69a` |
| `uImage` | repo `binary/uImage` | 4,814,777 | `a265fe5bc6769ef3d083655bc4264504` |
| `board.dtb` | repo `binary/board.dtb` | 48,463 | `5dc270ea1419da8e2b2e589812702eab` |
| `rootfs.jffs2` | **只在 zip 里** | 11,812,720 | `0fd03a1115374f5335d7fc207b14a849` |

三个 `binary/` 文件与 zip 内同名文件 md5 一致（本次复核过），所以只有 `rootfs.jffs2` 需要解包 release。
`paramtag.bin` 不来自任何仓库——见 §3。

> ⚠️ 本地 `sk-d840n-openwrt/release/firmware_v2.0.zip` 只有 **962,560 B**，是直连 GitHub 时被截断的残包
> （`unzip -l` 直接报 `BadZipFile`），`release/rootfs.jffs2`（589,198 B）同样是残缺的。
> 别把这两个当完整素材用；完整的 `rootfs.jffs2` 在 `staging/rootfs.jffs2`。

### 0.2 社区镜像自己的底版（参考，不直接刷）

README 说明 rootfs 模板来自 OpenWrt 官方 **armsr/armv8** 通用构建：

- 目录：<https://downloads.openwrt.org/releases/24.10.8/targets/armsr/armv8/>
- 模板文件：`openwrt-24.10.8-armsr-armv8-generic-ext4-rootfs.img.gz`
- 交叉工具链：`openwrt-toolchain-24.10.8-armsr-armv8_gcc-13.3.0_musl.Linux-x86_64.tar.zst`

（后两个文件名已对该目录的 `sha256sums` 核过，不是猜的；同目录还有 `openwrt-sdk-...` 和 `kmods/6.6.144-1-…/`，
社区 rootfs 里那份用不上的 `lib/modules/6.6.144/` 就是从这条线来的。）

这也是为什么刷完 `system.board` 报 `target: armsr/armv8`、内核却还是 Skyworth 的 4.19.136+：
用户态是 armsr 通用 rootfs，内核是原厂 BSP 打包成的 uImage。

### 0.3 用来判断"能不能刷"的逆向仓库

- <https://github.com/celeron633/ZX279127S-Reverse>（本地两份 clone：`zx279127s-reverse/` 放 README/CLAUDE.md，`zx-repo/` 放 `script/*.py` 与 `uboot-img/*.bin`）
- 取用的是它的跨机型判据：镜像头 `0x80..0x800` 的香农熵（有 RSA ≈7.9 / 无 RSA ≈0.36 明文 `0001ZXIC`）
  和 16 B 容器摘要槽位，据此判定本 SKU 启动链未启用安全启动（§2 第 1 条）。

### 0.4 试过但排除的仓库

- <https://github.com/pbs05/uboot-an758x>（本地 `uboot-an758x/`）：Airoha **AN758x** 平台的 U-Boot，
  与本机的 Sanechips **ZX279133** 不是同一 SoC，不能作为刷机素材，只用于早期确认"没有现成的免刷方案"。

### 0.5 下载通道（这台机器的现实约束）

| 路线 | 结果 |
| --- | --- |
| PC 直连 GitHub | connection reset，拿不到 |
| PC 走 `socks5h://127.0.0.1:10808` | 通，但约 1 KB/s：8 分钟只下来 700 KB，且中途断（`curl` exit 18） |
| **VPS `103.167.150.121`（ssh alias `server-103-167-150-121`）代下** | 16 MB 秒下 → 解出 `rootfs.jffs2` → `python3 -m http.server 8099` → PC 按 1 MiB Range 分片拉回 |

分片拉回用 `tools/getparts.sh`（`staging/parts/p.00 … p.11`，合计 11,812,720 B，md5 复核后再用）；
判成功必须比**精确字节数**，只判断"文件存在"会把截断分片当成功。`scp` 直连超时，走 ProxyCommand 时
命令行引号会被打乱，所以写成 ssh config 文件（`staging/vps.cfg`）。用完记得 `fuser -k 8099/tcp`
（别用 `pkill -f http.server`，会连自己的 ssh 会话一起杀），本次已确认端口关闭。

---

## 1. 结果

| 项 | 刷机前 | 刷机后 |
| --- | --- | --- |
| bootloader | U-Boot 2021.01-svn358069（原厂，A/B 双 slot，`bootcmd=zxboot`） | U-Boot 2021.01-svn308776（社区，5 分区表 + `bootm`） |
| 内核 | `Linux 4.19.136+ #4 ... Wed Apr 24 2024`（原厂） | `Linux 4.19.136+ #4 ... Mon Jun 3 2024`（Skyworth BSP，随社区 uImage） |
| 用户态 | 原厂 csp + OSGi/JVM + VoIP（RAM initrd） | OpenWrt 24.10.8 r29233-443ec4032a，jffs2 rw 持久根 |
| 登录 | `telnet 192.168.1.1` → root | `ssh root@192.168.1.1`（空密码）+ LuCI `http://192.168.1.1` |
| 光纤 WAN | 正常 | **不通**（社区镜像没有 OMCI 用户态，见 §9） |

---

## 2. 为什么可以这么刷（前提判断）

1. **启动链没有签名。** 用 `celeron633/ZX279127S-Reverse` 那套跨机型判据（镜像头 `0x80..0x800`
   的香农熵：有 RSA 的机型 ≈7.9 密文、无 RSA 的 ≈0.36 明文 `0001ZXIC`）对比本机 `boot` 分区，
   再核对 ZTE binheader 的四魔数 / 16B 容器摘要槽位，结论是本 SKU 的 boot 链**未启用安全启动**，
   所以替换 bootloader 与重算 CRC 都可行——这是整个方案能成立的关键，不是"绕过后校验"，而是根本没有后校验。
2. **MTD 是可写的。** 全部 15 个 `/sys/class/mtd/mtd*/flags = 0x400`（= `MTD_WRITEABLE`），
   镜像里带 `/usr/sbin/nandwrite`、`/usr/sbin/nanddump`、`/bin/mtd_debug`、`/usr/sbin/flash_eraseall`，
   所以不需要额外 `.ko`；实际在 `/usr/data` 上 `touch`+`rm` 成功，证明擦除/编程整条链路可用。
3. **`/proc/mtd` 的序号顺序 ≠ 物理顺序**，偏移必须以原厂 U-Boot env 里编译进去的 `mtdparts` 为准
   （在 `boot`+0x180000，crc32 `0xc638125c` 校验通过）。任何"把 /proc/mtd 的 size 累加当偏移"的做法都会写错位置。

---

## 3. 素材（全部留在 `staging/`，md5 已在本地复核）

| 文件 | 大小 | md5 | 来源 |
| --- | --- | --- | --- |
| `boot.bin` | 2 MiB | `daa42186c3b2c5be70a749cbfd9dd69a` | 社区 release `firmware_v2.0.zip`（与 repo `binary/boot.bin` 同 md5） |
| `uImage` | 4,814,777 B | `a265fe5bc6769ef3d083655bc4264504` | 同上 |
| `board.dtb` | 48,463 B | `5dc270ea1419da8e2b2e589812702eab` | 同上，`compatible = "zte,133"` |
| `rootfs.jffs2` | 11,812,720 B | `0fd03a1115374f5335d7fc207b14a849` | 同上 |
| `paramtag.bin` | 4 MiB | `4d92b1d4a45d47cc65ea46f4df8e35d0` | **不是下载的**，是自己备份的 `backups/mtd2_tagparam.bin`（GPON 身份 / MAC / 32-hex secret），搬到新布局的 `parameter tags` |

填充过的版本（`mtd_debug write` 不会自动补页，必须自己填到 2048 B 整数倍，填充满 `0xFF`，
这一步等价于 U-Boot `nand write` 的行为）：

- `uImage.pad` 4,814,848 B → `f68ceb0f7094f65aca036dcb4a8fa0d2`
- `board.dtb.pad` 49,152 B → `881af2d3bd061e13952577cbea8a2835`
- `rootfs.jffs2.pad` 11,812,864 B → `bb6025d93569b031c99447a3844b33bb`

GitHub 直连在这台机器上被 reset / 代理只有 ~1 KB/s，所以 16 MB 的 zip 是先丢到 VPS
（`server-103-167-150-121`）秒下，再按 1 MiB 分片拉回来（`tools/getparts.sh`，带"精确大小重试"，
只判断 `-s file` 会把截断的分片当成成功）。

---

## 4. 先做完整备份（这一步不能省）

`tools/onu2.cjs backup <idx:name:size:...>`：设备端 `nanddump` → `md5sum` → `nc <PC_IP> 9820 < file`
→ PC 端落盘后再算一次 md5，**两个 md5 必须相等**才算数，然后删掉设备端临时文件。
产出在 `backups/`（逐分区，共 256 MiB 全部覆盖，`plugin_data` 因 tmpfs 只有 80 MiB 而分 4 个 24 MiB 片）。

踩到的坑（已在工具里修掉）：
- `fs.createWriteStream` 会"成功"落一个 0 字节文件，而内存里算出的 md5 却是正确的 → 五个分区的备份是假的。
  改成先攒 buffer、`writeFileSync`、再从磁盘重读算 md5 + 比对文件大小。
- 两条会话同时驱动设备时会抢 `/var/.o` 和 `S<n>` 同步标记 → 出现 `shell not responsive`、临时 dump 被删。
  `onu2.cjs` 用自己的 `/var/.o_qd` 和 `QD<n>` 标记，互不干扰。
- busybox `nanddump` 只吃 `[-no] [--bb padbad|skipbad] [-s ADDR] [-l LEN] [-f FILE]`，没有 `--quiet`/`-h`。

---

## 5. 写入前的两次预演（在没有串口的情况下把风险测干净）

1. **空白分区 round-trip**：在 `mtd14 awifi`（全 `0xFF`）上做一次 `mtd_debug erase`+`write`+回读，
   md5 相符 —— 证明"从 Linux 侧写进去的数据、按内核 ECC 布局"是完好的。
2. **B0：原样重写 `others`（mtd7，@0x4200000）再重启一次**。`others` 是文本 profile，记录活动
   kernel slot 和 MAC：
   `DownloadState=0x00000003,LastBooting=0x00000001,ethaddr=D0:45:8D:84:08:FC,UpModeInfo=0x00000040,BootImageNum=0x00000001,...`
   重启后活动槽仍是 `0x2200000`、MAC 仍是 `D0:45:8D:84:08:FC` —— 内容一致的重写 + 重启不死，
   等价于验证了"bootloader 能正确读回被 Linux 编程过的 NAND 页"。
   顺带确认这台机器**活动内核是 kernel1**（`/proc/cmdline` 里 `... 0x2200000 0x1 ...`），
   `kernel0`（@0x200000）才是空槽，所以前三个写入落在空槽里不会立刻变砖。

另外：`reboot` 必须用 `setsid reboot`。`(sleep 3; reboot) &` 会被 SIGHUP 干掉，设备**悄无声息地没重启**，
只能靠 `uptime` 发现——不要假设重启发生了。

---

## 6. 写哪些位置（新社区 U-Boot 的 5 分区表）

```
boot            0x00000000  2 MiB
kernel          0x00200000  8 MiB
dtb             0x00a00000  1 MiB
parameter tags  0x00b00000  4 MiB
root            0x00f00000  241 MiB   (0x0f100000)
```

`staging/flash_a.sh` 的写入顺序（与社区 README 不同，README 假设有串口）：

```
kernel → dtb → param → root → boot        # boot 放最后
```

- **boot 最后写**：数据分区写到一半掉电，还有一个能用的原厂 bootloader；bootloader 一旦写坏，
  没有串口 / 编程器就救不回来，这是唯一真正不可逆的东西。
- 前三段落进原厂 `kernel0`（空槽），所以中途失败时原厂系统还在。
- `root` 段一写，原厂系统就没了——但它是在原厂 Linux 里写的，写完还能立刻回读校验。

擦除长度按分区实际大小给：
`boot:2097152 kernel:8388608 dtb:1048576 param:4194304 root:252706816`。

---

## 7. 为什么用 `mtd_debug` 而不是 `nandwrite`

所有写入都在**任意绝对偏移**上，所以统一走 `/dev/mtd0`（`whole`，原厂下 256 MiB）：

```sh
mtd_debug erase /dev/mtd0 <ofs> <erase_len>
mtd_debug write /dev/mtd0 <ofs> <padded_len> <file.pad>
sync
mtd_debug read  /dev/mtd0 <ofs> <original_len> /var/rb.bin
md5sum /var/rb.bin          # 与原始（未填充）payload 的 md5 比较
```

- ECC/OOB 由内核驱动按原厂布局生成，不需要自己算（预演已证明可用）。
- `nandwrite` 需要 `dev=ofs` 的 OOB 对齐语义、还要 `-p` 之类选项，且我们只有绝对偏移最省事。
- **校验不要用 `nanddump -s <ofs> -l <len>`**：这台 busybox 的 `nanddump` 在带 `-s` 时**忽略 `-l`**，
  会把整个区域倒出来，第一次 `write` 就因为它报了假的 kernel MISMATCH（拿到的是填充后的 md5）。
  `mtd_debug read` 接受精确的 offset+len，才是可靠的回读手段。

---

## 8. 实际执行序列

```sh
# 1) PC 侧起服务（tftpy 会被客户端 reset 打死，所以套一层重启循环）
cd /c/UGit/openwrt/SK-D840N && (while true; do python tools/tftpsrv.py staging 6969 >>staging/tftp.log 2>&1; sleep 1; done)

# 2) 把 payload 推进设备（busybox tftp 的端口是位置参数！-p 表示"监听"）
#    onu2.cjs 走 telnet，密码用环境变量给（不写进仓库）；命令本身就是第一个位置参数，不要写成 `exec "..."`
export ONU_PASS=<原厂 telnet root 密码>
node tools/onu2.cjs "cd /var; for f in boot.bin uImage.pad board.dtb.pad paramtag.bin rootfs.jffs2.pad flash_a.sh; do tftp -g -r \$f -l /var/\$f 192.168.1.2 6969 -b 1468; done; md5sum /var/*.pad /var/boot.bin"
#   多 MB 的传输要 setsid 脱离，否则 shell 超时把 tftp 打断

# 3) 停掉厂商栈并卸载 jffs2（关键！擦除正在挂载的 jffs2 backing store 会 oops 内核）
node tools/onu2.cjs "sh /var/flash_a.sh stop"     # jffs2 mounts remaining: 0

# 4) 写 + 回读校验（五段全绿才允许重启）
node tools/onu2.cjs "sh /var/flash_a.sh write"
node tools/onu2.cjs "sh /var/flash_a.sh verify"
```

`write` 的真实收尾（`staging/write_run2.log`）：

```
[12.%N] == root VERIFIED  md5=0fd03a1115374f5335d7fc207b14a849
[12.%N] == boot: erase 0x0 +0x200000
[13.%N] == boot VERIFIED  md5=daa42186c3b2c5be70a749cbfd9dd69a
[13.%N] ALL STAGES WRITTEN+VERIFIED
```

五段全部回读 md5 == payload md5（kernel `a265fe5b…`、dtb `5dc270ea…`、param `4d92b1d4…`、
root `0fd03a11…`、boot `daa42186…`）。此时原厂 Linux 还在 RAM 里跑，是"最后还能改"的状态；
唯一没做的事就是重启。

```sh
# 5) 重启
node tools/onu2.cjs "setsid reboot < /dev/null > /dev/null 2>&1 & echo REBOOT_ISSUED"
```

约 90 s 后 `22/80` 端口开放、`23` 关闭 —— OpenWrt 一次起来，没有任何补救动作。

---

## 9. 起来之后：能用什么、不能用什么

- `/proc/mtd`：`whole 128M, boot 2M, kernel 8M, dtb 1M, "parameter tags" 4M, root 241M`；
  `/` = `/dev/root` jffs2 rw,noatime（241 MiB 用了 16.7 MiB），`/tagparam` = `/dev/mtdblock4`，
  里面 `0_mac_addr…7_mac_addr`、`paramtag`、`flag_type`、`osgi_set` 都还在 → 身份迁移成功。
- 访问：`ssh root@192.168.1.1`（dropbear 允许**空密码**）；PC 公钥 `~/.ssh/id_onu` 已写入
  `/etc/dropbear/authorized_keys` 和 `/root/.ssh/authorized_keys`；封装脚本 `tools/ow.sh`（从 argv 或 stdin 收命令）。
  PC 地址不再是静态的 `192.168.1.2`，OpenWrt 的 dnsmasq 给它发了 `192.168.1.190`。
- LuCI：`http://192.168.1.1`（OpenWrt 24.10.8 / LuCI 26.205.85413）。**注意 rpcd 没装 `file` 插件**，
  `file.exec`/`file.read` 一律返回 ubus status 6；24.10 也删了 `/rpc/*` 老接口，
  想走 HTTP 只能用 `tools/luci.cjs` 的 `admin/ubus` session 登录。
- **GPON 不通**：镜像是"路由器"版，`wan = dhcp on eth0`（2.5G 口），`lan = br-lan(eth1,eth2,eth3)`。
  内核侧没问题（`zte_xgpon gpondrv_xg np_133 zx_ponreg_133 bspdriver` 全加载，dmesg 有
  `pon serdes init succeed` / `gpon_Init ok` / `vendor name = ZTE_XGPON1ASY` / `rx los =0 rx data in`），
  但根文件系统里**没有任何 OMCI/RMS 用户态程序**（只有 `.ko` 和 `/proc/gpon_sdk`、`/proc/pon`）。
  注册流程建立 TCONT/GEM，所以缺了它 `pon0` 的 `rx_bytes` 恒为 0，`ip link set pon0 up` 之后
  `udhcpc -i pon0` / `-i omci` 都拿不到 lease。结论：这不是 `uci` 能补的配置，而是要移植/重写 OMCI 栈。
- 设备侧两个坑：读 `/proc/gpon_sdk/debug` 用 `cat`/`tail -c` **永不返回**（它会循环把 PON 保留内存
  倒进 kmsg 并吃满一核，load 冲到 6）；这个 busybox **没有 `pkill`**（用 `kill -9 <pid>`），ash 里也没有 `timeout`。

---

## 10. 回滚（恢复原厂）

`restore_set/` 已按**新的 5 分区表**切好，不是原厂偏移：

| 文件 | 大小 | md5 | 内容 |
| --- | --- | --- | --- |
| `boot_stock.bin` | 2 MiB | `9f884388a1e635b93b6e9ed661a41ce9` | 原厂 `mtd1 boot` |
| `kernel_stock.bin` | 8 MiB | `21f15f5b82aee4058d4a584e91f1ad69` | 原厂 `kernel0` 前 8 MiB |
| `dtb_stock.bin` | 1 MiB | `ca1e761a60bc1fab9d0e0bc5b0d97a84` | `kernel0`@0x800000 片 |
| `param_stock.bin` | 4 MiB | `a97600ddd092167b40c96420f2a95bac` | `kernel0`@0x900000 片 |
| `root_stock.bin` | 252,706,816 B | `89a0cd37b5fcfa2da97487296316b6de` | 0xFF 底块，把原厂 mtd6/7/2/5/4/8/9/13/10/12/11(+4片)/14 按真实偏移放进去 |

**关键约束**：社区 dtb 把 `mtd0 whole` 定义成 `0x08000000` = **128 MiB**（不是整片 256 MiB），
所以 >128 MiB 的内容**不能**再走 `/dev/mtd0`。按分区设备、用**相对偏移**写：

```sh
# 先把 restore_set 里的文件传到设备（scp 即可，OpenWrt 有 scp 服务端）
scp -i ~/.ssh/id_onu restore_set/*_stock.bin root@192.168.1.1:/tmp/
mtd_debug erase /dev/mtd1 0 2097152      && mtd_debug write /dev/mtd1 0 2097152      /tmp/boot_stock.bin
mtd_debug erase /dev/mtd2 0 8388608      && mtd_debug write /dev/mtd2 0 8388608      /tmp/kernel_stock.bin
mtd_debug erase /dev/mtd3 0 1048576      && mtd_debug write /dev/mtd3 0 1048576      /tmp/dtb_stock.bin
mtd_debug erase /dev/mtd4 0 4194304      && mtd_debug write /dev/mtd4 0 4194304      /tmp/param_stock.bin
mtd_debug erase /dev/mtd5 0 252706816    && mtd_debug write /dev/mtd5 0 252706816    /tmp/root_stock.bin
```

`root_stock.bin` 里带着 `userconfig`（LOID/宽带账号那套），所以整片回滚是**唯一**能把 GPON 业务原样
恢复的路子——`backups/mtd5_usercfg.bin` 里的配置是压缩的 `db_*_cfg.xml`，grep 不出明文，别指望抄出来重配。
写完逐段 `mtd_debug read` + md5 校验，再 `setsid reboot`。

---

## 11. 时间线（本机文件 mtime 对齐）

| 时间 | 动作 |
| --- | --- |
| 16:1x | telnet 进原厂 root，盘点 CPU/RAM/flash/OS/可写分区 |
| 16:50–17:0x | `backups/` 逐分区备份全部 md5 通过（含修正 0 字节假备份后重拉的五片） |
| 17:1x–18:0x | 社区 zip 经 VPS 取回、拆片、校验；`*.pad` 填充；mtd14 round-trip + B0 预演重启通过 |
| 18:14–18:16 | `flash_a.sh stop` → `write`（第一轮的假 MISMATCH 改成 `mtd_debug read` 后全绿） |
| 18:17 | `restore_set/` 回滚集切好 |
| 19:49 | `setsid reboot` |
| 19:51 | 22/80 开放，确认 OpenWrt 24.10.8；随后装 SSH 公钥、`tools/ow.sh` |
| 20:0x–21:2x | 分区/挂载/身份核对；GPON 侧确认缺 OMCI 用户态，无 WAN |

---

## 12. 复用这套流程要注意的三件事

1. **没有串口就不要相信任何"应该已经重启了"**——每一步都用 `uptime` / 端口探测证实，写完必须回读 md5 再重启。
2. **bootloader 永远最后写**，写之前先确认数据分区已经校验通过；原厂备份要在做 `nand erase` 之前
   就已经是"双端 md5 一致"的状态，否则等于放弃唯一救命稻草。
3. **必须先 `umount` 所有 jffs2 再擦它的 backing store**，否则内核 oops；`stop` 模式里那串 `killall`
   是为了让厂商栈别在擦写过程中往回写。
