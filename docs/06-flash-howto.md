# 06 — SK-D840N 刷 OpenWrt：只用方法 B（无串口，从原厂 Linux 里写 flash）

> 目标设备：Skyworth **SK-D840N**（Sanechips **ZX279133**，2×Cortex-A53，512 MiB DDR，256 MiB SPI-NAND）。
> 全程只需要一根网线 + telnet，**不拆机、不需要 USB-TTL**。2026-10-09 实机走通。
> 原理、取证、踩坑细节见 `docs/05-openwrt-flash-procedure.md`。

---

## 1. 用哪个仓库、下载哪个文件

| | 地址 |
| --- | --- |
| 仓库 | <https://github.com/huxiangjs/SK-D840N-OpenWRT> |
| Release 页 | <https://github.com/huxiangjs/SK-D840N-OpenWRT/releases> |
| **下载这个包**（tag `v2.0`，16,060,875 B） | <https://github.com/huxiangjs/SK-D840N-OpenWRT/releases/download/v2.0/firmware_v2.0.zip> |

GitHub 直连在这台 PC 上被 reset、走 `socks5h://127.0.0.1:10808` 只有 ~1 KB/s 且会断。
可用做法：VPS（本次用 `server-103-167-150-121`）下载并解包 → VPS 起 `python3 -m http.server 8099`
→ PC 用 `tools/getparts.sh` 按 1 MiB 分片拉回，**逐片比对精确字节数**（只判断文件存在会把截断片当成功）。
用完 `fuser -k 8099/tcp` 关服务（不要用 `pkill -f http.server`，会连自己的 ssh 会话一起杀掉）。

## 2. 刷入的 5 个文件（大小 + md5 都是本次实际值）

| 文件 | 出处 | 大小 | md5 |
| --- | --- | --- | --- |
| `boot.bin` | zip 内，等同 repo `binary/boot.bin` | 2,097,152 | `daa42186c3b2c5be70a749cbfd9dd69a` |
| `uImage` | zip 内，等同 repo `binary/uImage` | 4,814,777 | `a265fe5bc6769ef3d083655bc4264504` |
| `board.dtb` | zip 内，等同 repo `binary/board.dtb` | 48,463 | `5dc270ea1419da8e2b2e589812702eab` |
| `rootfs.jffs2` | **只有 zip 里有** | 11,812,720 | `0fd03a1115374f5335d7fc207b14a849` |
| `paramtag.bin` | **不下载**：第 3 步自己备份出来的原厂 mtd2 | 4,194,304 | `4d92b1d4a45d47cc65ea46f4df8e35d0` |

`paramtag.bin` 里是 GPON 身份（SN / 各口 MAC / 32-hex secret）。不搬它，注册信息就没了。

---

## 3. 第 0 步：先把整片备份下来（唯一救命稻草，别跳）

```sh
cd /c/UGit/openwrt/SK-D840N && export ONU_PASS=<原厂 telnet root 密码>
node tools/onu2.cjs backup \
  1:mtd1_boot:2097152 2:mtd2_tagparam:4194304 3:mtd3_kernel0:33554432 \
  4:mtd4_middleware:4194304 5:mtd5_usercfg:8388608 6:mtd6_kernel1:33554432 \
  7:mtd7_others:6291456 8:mtd8_wlan:4194304 9:mtd9_phoneapp0:2097152 \
  10:mtd10_osgi0:31457280 12:mtd12_osgi1:31457280 14:mtd14_awifi:4194304
# mtd11 plugin_data 有 96 MiB，设备 tmpfs 只有 80 MiB，必须分片：
node tools/onu2.cjs backup \
  11:mtd11_plugin_data:100663296:0:25165824 11:mtd11_plugin_data:100663296:25165824:25165824 \
  11:mtd11_plugin_data:100663296:50331648:25165824 11:mtd11_plugin_data:100663296:75497472:25165824
```

`onu2.cjs backup` 每条会打印设备端 md5 和 PC 端 md5，**两个必须相同**并显示 `VERIFIED`。
成品在 `backups/`，清单 `backups/MANIFEST.md5`。注意 `mtd4/mtd5/mtd11` 是活的 jffs2，两次读会漂移（系统在写），
不是传输错。`mtd11` 前 72 MiB 全 `0xFF`，所以三片 md5 相同是正常的。

## 4. 第 1 步：本地准备（paramtag + 页填充）

```sh
cd /c/UGit/openwrt/SK-D840N
cp backups/mtd2_tagparam.bin staging/paramtag.bin
cd staging && python -c "
for f in ('uImage','board.dtb','rootfs.jffs2'):
    d = open(f,'rb').read(); pad = (2048 - len(d) % 2048) % 2048
    open(f+'.pad','wb').write(d + b'\xff'*pad); print(f,'->',len(d),'+',pad,'=',len(d)+pad)
"
```

为什么要有 `.pad`：**`mtd_debug write` 不会补页**，而 NAND 只能按 2048 B 页写。补 `0xFF` 到整页
正好等价于 U-Boot `nand write` 的行为。结果：

```
uImage        4,814,777 + 71  = 4,814,848   md5 f68ceb0f7094f65aca036dcb4a8fa0d2
board.dtb        48,463 + 689 =    49,152   md5 881af2d3bd061e13952577cbea8a2835
rootfs.jffs2 11,812,720 + 144 = 11,812,864  md5 bb6025d93569b031c99447a3844b33bb
```

`boot.bin`(2 MiB) 和 `paramtag.bin`(4 MiB) 本来就整页对齐，不用填。

## 5. 第 2 步：PC 起服务、把文件推进设备

PC 网卡先设**静态 `192.168.1.2`**（原厂 LAN 是 192.168.1.1/24）。

```sh
cd /c/UGit/openwrt/SK-D840N
(while true; do python tools/tftpsrv.py staging 6969 >>staging/tftp.log 2>&1; sleep 1; done)   # 后台常驻
```

```sh
ONU_PASS=<…> node tools/onu2.cjs "cd /var; for f in boot.bin uImage.pad board.dtb.pad paramtag.bin rootfs.jffs2.pad flash_a.sh; do tftp -g -r \$f -l /var/\$f 192.168.1.2 6969 -b 1468; done; md5sum /var/*.pad /var/boot.bin"
```

三个易错点：
- busybox `tftp` 的端口是**位置参数**（`... 192.168.1.2 6969`），写成 `-p 6969` 是"监听"不是"上传/下载"。
- `-b 1468`（块大小）+ 大文件要 `setsid` 脱离，否则 telnet shell 超时会把传输打断。
- tftpy 遇到客户端 reset 会直接死掉，所以外面套 `while true` 重启循环。

`md5sum` 输出必须和第 2、4 节的表逐字对上，对不上就重传，别往下走。

## 6. 第 3 步：写入位置（社区 U-Boot 的 5 分区表）

```
boot            0x00000000   2 MiB      ← boot.bin
kernel          0x00200000   8 MiB      ← uImage.pad
dtb             0x00a00000   1 MiB      ← board.dtb.pad
parameter tags  0x00b00000   4 MiB      ← paramtag.bin（原厂这里在 0x04800000，要搬家）
root            0x00f00000   241 MiB    ← rootfs.jffs2.pad（擦除长度 0x0f100000 = 252,706,816）
```

写入顺序固定 **kernel → dtb → param → root → boot，boot 放最后**：
前三段落进原厂 `kernel0`（本机活动槽是 **kernel1** `0x2200000`，所以 kernel0 是空槽），
中途失败原厂系统还在；bootloader 是唯一没有串口/编程器就救不回来的东西，掉电风险留到最后。

`staging/flash_a.sh` 的 `ENTRIES` 表就把上面这套偏移和期望 md5 硬编码在里面
（换文件/换版本要同步改这张表）。设备端用法：`sh /var/flash_a.sh stop|check|one <name>|write|verify`。

## 7. 第 4 步：停厂商栈、卸载 jffs2

```sh
ONU_PASS=<…> node tools/onu2.cjs "sh /var/flash_a.sh stop"
```

- `killall -9 java osgid cspd pc dmm httpd voip phoneapp simulation ctsgw_proxyd dhcps dhcp6s raserver radvd dnsmasq portmap openl2tpd openl2tpd6 cpulimit log_usb`
- `umount -l` 掉 `/usr/local/osgi /usr/local/upgrade_module /usr/data /wlan /userconfig /important_bak /tagparam`

必须看到 **`jffs2 mounts remaining: 0`**。擦除正在挂载的 jffs2 backing store 会直接 oops 内核。
这一步做完原厂 Web/OSGi 就没了（预期行为），设备只剩 telnet 和 flash 读写。

## 8. 第 5 步：写入并逐段回读校验

```sh
ONU_PASS=<…> node tools/onu2.cjs "sh /var/flash_a.sh write"
ONU_PASS=<…> node tools/onu2.cjs "sh /var/flash_a.sh verify"
```

每段就三条命令（`/dev/mtd0` = 原厂 `whole`，256 MiB，按绝对偏移操作）：

```sh
mtd_debug erase /dev/mtd0 <ofs> <分区大小>
mtd_debug write /dev/mtd0 <ofs> <填充后长度> /var/<file>.pad     # 长度用 .pad 的
sync
mtd_debug read  /dev/mtd0 <ofs> <原始长度> /var/rb.bin           # 长度用原始文件的，不含填充
md5sum /var/rb.bin                                              # 必须等于第 2 节的 md5
```

- **校验不要用 `nanddump -s <ofs> -l <len>`**：这台 busybox 带 `-s` 时会忽略 `-l`，把整片倒出来，
  md5 必然不匹配（本次第一轮的"假 MISMATCH"就是这么来的）。`mtd_debug read` 才接受精确 offset+len。
- ECC/OOB 由内核驱动按原厂布局生成，不需要自己算（事前在空白分区 `mtd14 awifi` 上做过 round-trip 验证）。
- 任何一段 `*** MISMATCH ***` 就停下重擦重写那一段，**五段全绿之前不許重启**。

## 9. 第 6 步：重启

```sh
ONU_PASS=<…> node tools/onu2.cjs "setsid reboot < /dev/null > /dev/null 2>&1 & echo REBOOT_ISSUED"
```

**必须 `setsid`**：`(sleep 3; reboot) &` 会被 SIGHUP 静默杀掉，设备根本不会重启，只能靠 `uptime` 才发现。

判据（本次约 90 s）：

```sh
# 22 开放 + 80 开放 + 23 关闭 = OpenWrt 起来了
node -e "const n=require('net');for(const p of [22,23,80]){const s=n.connect({host:'192.168.1.1',port:p,timeout:2000});s.on('connect',()=>{console.log(p,'OPEN');s.destroy()});s.on('error',e=>console.log(p,e.code||'closed'))}"
```

---

## 10. 刷完的状态

```sh
ssh root@192.168.1.1        # dropbear，root 空密码（原厂 telnet 密码失效）
http://192.168.1.1          # LuCI，OpenWrt 24.10.8 r29233-443ec4032a
```

- 分区表变成 5 分区：`whole 128M, boot 2M, kernel 8M, dtb 1M, "parameter tags" 4M, root 241M`。
  **注意 `mtd0 whole` 只有 128 MiB**（社区 dtb 写小了），超过 128 MiB 的读写必须走分区设备。
- `/` = jffs2 读写持久（241 MiB 用了 16.7 MiB）；`/tagparam` = `mtdblock4`，MAC/SN 都还在。
- 网口：`eth0` = WAN（dhcp，2.5G 口），`eth1/eth2/eth3` → `br-lan` 192.168.1.1。
  电脑接 LAN 口会被 DHCP 分走地址（本次成了 `192.168.1.190`），静态 `192.168.1.2` 的假设作废。
- **光纤 WAN 不通**：这个镜像是路由器版，根文件系统里**没有任何 OMCI 用户态**（只有 `.ko`：
  `zte_xgpon gpondrv_xg np_133 zx_ponreg_133 bspdriver`）。PON 物理层是好的（`rx los =0 rx data in`），
  但没有注册就没有 TCONT/GEM，`pon0` 恒为 `rx_bytes 0`，`ip link set pon0 up` 后 `udhcpc -i pon0` 也拿不到 lease。
  ⇒ 这台现在**不能当光猫上网**，要上网就走第 11 节回原厂。
- 两个别踩的设备端坑：`/proc/gpon_sdk/debug` 用 `cat`/`tail -c` 读**永不返回**（它循环把 PON 保留内存
  灌进 kmsg 并吃满一核）；这个 busybox **没有 `pkill`**（用 `kill -9 <pid>`），ash 里也没有 `timeout`。
- 建议现在就把 root 密码设上（LuCI 或直接 `passwd`），本次没替你改。

## 11. 要回原厂（反悔）

`restore_set/` 已按**新分区表**切好 5 个文件（`root_stock.bin` 252,706,816 B，
md5 `89a0cd37b5fcfa2da97487296316b6de`，内含 usercfg 的 LOID/宽带账号）。
按**分区设备 + 相对偏移**写，不要走 `/dev/mtd0`：

```sh
scp -i ~/.ssh/id_onu restore_set/*_stock.bin root@192.168.1.1:/tmp/
mtd_debug erase /dev/mtd1 0 2097152   && mtd_debug write /dev/mtd1 0 2097152   /tmp/boot_stock.bin
mtd_debug erase /dev/mtd2 0 8388608   && mtd_debug write /dev/mtd2 0 8388608   /tmp/kernel_stock.bin
mtd_debug erase /dev/mtd3 0 1048576   && mtd_debug write /dev/mtd3 0 1048576   /tmp/dtb_stock.bin
mtd_debug erase /dev/mtd4 0 4194304   && mtd_debug write /dev/mtd4 0 4194304   /tmp/param_stock.bin
mtd_debug erase /dev/mtd5 0 252706816 && mtd_debug write /dev/mtd5 0 252706816 /tmp/root_stock.bin
setsid reboot
```

`mtd5` 一次写 241 MiB 会跑很久（本次实测 ~7.8 MB/s，约 30 s，但要留余量），写完同样逐段 `mtd_debug read` + md5 校验再重启。
