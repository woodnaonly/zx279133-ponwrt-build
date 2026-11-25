# 103 · 出向修复的实机判决：板子↔PC 双向通了，仪器自己也有三条假 FAIL

日期：2026-11-25　　分支：`feat/kernel-7.3`　　CI run：45550983983（#78，head `ae269ad2`）

## 一、判决

1. **出向断路已修复，实机双向验证通过。** 刷入带补丁 220 的 7.3.4 内核后，
   PC 与板子之间两个方向都能过：PC 侧 ARP 表拿到板子的真 MAC（`D0-45-8D-84-09-00`），
   ping 3/3 通（TTL 64，0–1 ms）；板子侧主动 ping PC 也 3/3 通（TTL 128，1.5 ms），
   conduit 的 `rx_packets` 与 `tx_packets` 在同一段窗口里**同时**各 +23。
2. **板子上的内核就是 run 78 那一份，字节可核。** `mtd2` 前 5814271 字节的 md5 =
   `6405f6edb271620e627306ad14f01ffd`，与 artifact 里 `flash-uImage` 的 md5 逐位相同。
3. **之前那三条 FAIL 全是仪器自己的缺陷，板子当时是健康的。** 仪器修好后，
   真机对拍判决是 **5 PASS / 2 N/A / 0 FAIL，退出码 0**（C1 `rx_packets 2043 -> 2285 (+242)`、
   C2 四个 DSA 口都在、C3 `signal detect low, lamp brightness 1`、C4/C5 N/A、C6 四个隔离计数都在、
   C7 四个驱动都在内核符号表里）。
4. **2.5G 插孔（`lan1`）仍然不通**，且这不是出向修复留下的：U-Boot 里能看到厂商的
   `outerphy` 初始化（`phy identifier is [0xc849]`），而 7.3 驱动没有接管它，
   板上 `lan1` 是 `lowerlayerdown`。**这是另一个独立缺陷，见第五节。**

## 二、这一轮刷了什么

| 项 | 值 |
|---|---|
| CI | run 45550983983（#78），gates / initramfs / flash 三个 job 全 success |
| artifact | `sk-d840n-73-images-flash`（id 14995119778，73438266 B），登录态浏览器取件 |
| 三件自校验 | 按 artifact 自带的 `sha256sums` 逐个核对，三件全对 |
| `flash-uImage` | 5814271 B，md5 `6405f6edb271620e627306ad14f01ffd`（**本轮刷它**） |
| `flash-dtb.bin` | md5 `ff6914d007b9b21895e1a6b3f67d5ea5` —— **与板上那份逐位相同 ⇒ 不刷** |
| `flash-rootfs.jffs2` | md5 `e441a1012a7e90d7f1ee74c027df0ed2` —— **不刷** |
| permit | `staging/ctl/flash.permit` 第 30 行：`run=45550983983 stages=kernel`，padded md5 `uimage=7bddf018e1b7cf5f624ac647af0c2fd5` |
| 刷写通道 | U-Boot + TFTP（`tools/ubflash.sh`，ARM=1 装填 → 断电 → `u` 打断 autoboot） |
| 回读校验 | 下载 `crc=d2709c23`、擦除头尾 `crc=f154670a`（0xFF 图案）、写后回读 `crc=d2709c23` |

**为什么只写 kernel 一段**：出向修复全在内核里（补丁 220）；dtb 与板上那份逐位相同，
刷它是白擦一个单向门分区；root 是 241 MiB 的单向门、没有 A/B，而板上那份已经是能起机的
7.3 root（run 45409237203 写的），重写它买不到任何东西，却要吃掉唯一一份回滚余地。
回滚件已固化：`staging/trial/flash-uImage.run45409237203`（板上原 kernel，
刷机前按 `mtd2` 前 5813812 字节逐位核过，md5 `8cc7e09ddbcaaaf28c3b5694cde9d0f6`）。

## 三、出向修复的实机证据

**根因**（docs/102 已详述）：7.3 驱动的 IDM TX 描述符把目的口写死成 5（XMAC0，本板无对端），
既不读也不剥 DSA tagger 压上的 transport VID，第四个字还写着 PON 控制字 `0x08000000`。
补丁 220 照 6.18 世代的形状改成：读 transport VID → 剥标 → 芯片口 = VID − 58、
第四个字用厂商 LAN 路径的 `0x003e0000`、无标签时的 fallback 口 5→2。

**实机双向**（刷完新内核，PC 网线插在 `lan2`）：

```
# PC 侧：清掉陈旧 ARP 再 ping —— ARP 表有没有真 MAC 是唯一诚实的判据
#        （ping 会被本机 TUN 应答，30/30 通也可能是假的）
Get-NetNeighbor -InterfaceIndex 19 -IPAddress 192.168.9.1
  192.168.9.1   D0-45-8D-84-09-00   Reachable        # 板子的真 MAC 出现了
ping -n 3 192.168.9.1   ->   3 sent, 3 received, 0% loss, TTL=64, 0-1ms

# 板子侧：反方向
ping -c 3 -W 2 192.168.9.100
  3 packets transmitted, 3 packets received, 0% packet loss
  round-trip min/avg/max = 1.483/1.559/1.689 ms, ttl=128
ip neigh
  192.168.9.100 dev br-lan lladdr 84:47:09:5b:bd:2c REACHABLE

# conduit 计数：同一段窗口里两个方向同时增长
cat /sys/class/net/lan-cpu0/statistics/{rx_packets,tx_packets}
  before: 140  68
  after : 163  91          # rx +23, tx +23
```

**新内核的启动特征**：`Linux version 7.3.4 ... #0 SMP PREEMPT Mon Nov  9 23:57:33 2026`、
`the 9132 is serving: the jacks are the 9132's UNI ports 1..4 (DSA ports 4..7),
the 2.5G jack is the outerphy port 5 (DSA port 8), the CPU port is 9 and the transport VIDs are 59..63`、
`registered the 9132 as a DSA switch: ports 4..8 carry the transport VIDs 59..63`、
`lan2: Link is Up - 1Gbps/Full`、`eth0: Link is Up - 1Gbps/Full`。

## 四、仪器的三条假 FAIL（这一轮修完的）

之前那轮真机对拍是 4 PASS / 3 FAIL。三条 FAIL 全是仪器自己的缺陷，板子当时是健康的：

| 判据 | 仪器的错法 | 真值 | 修法 |
|---|---|---|---|
| C3 | 把灯的 trigger 文件**整串**与驱动名比 | 内核把生效触发器写在**方括号**里（`… [zx279133-los] …`） | `effective_trigger()`：有方括号取里面的，无方括号取第一个非 `none` 的名字（`ae269ad2`） |
| C4 / C5 | 只在 `$CLASS/<netdev>/device/`（**父设备**目录）里找属性 | 属性建在 `ndev->dev` 上，而 `ndev->dev` **就是网口自己的设备** ⇒ `/sys/class/net/eth0/pon_state` | 新增 `attr_path()`：先读网口自己的目录，再回落到父设备目录（`2bda3c0d`） |
| C3 的 opsd | 按**平铺号 65** 找线，且电平只认 `high/low` | 7.3 的 gpio dump 按**芯片从 0 重新编号**（平铺号 65 根本不存在）、多一列 `|consumer` 名字、电平写 `hi/lo` | 按 driver 请求的名字 `opsd` 找线，平铺号只作老内核回落，电平两种写法都认（`2bda3c0d`） |

**共同的病根**：合成夹具与仪器**错在同一边**，所以本机演练绿、真板红。
夹具已改成镜像真实板子（gpio dump 用 7.3 的形状、属性放网口自己的目录、trigger 文件写方括号形式），
控制项也从两头加：把按名字找线的那一步弄坏 ⇒ 必须读不到；只认 `high/low` ⇒ 7.3 形状上必须读不到；
喂 6.18 的形状 ⇒ 仍能按平铺号读到。本机 `tests/test_trialsurvey_endtoend.py` 27 checks、
`tests/test_trialsurvey_verdict.py` 19 controls 全绿。

## 五、未做

1. **2.5G 插孔（`lan1`）没治本。** 板上 `lan1` 是 `lowerlayerdown`。U-Boot 启动日志里有厂商的
   `enter outerphy info init!` / `--->phy identifier is [0xc849]`，而 7.3 驱动没有把厂商的
   RTL8226 初始化搬进来（材料在 `pon/vendor-modules/rlt8226b.ko` 与 `devtools/rlt8226tab.py`）。
   ⇒ **本轮刷进去的 7.3 固件，1G 的四个口可用，2.5G 口不可用。**
2. **C4 / C5 是 N/A，不是通过。** 板上没有光纤：`opsd` 恒为 `low`、`pon_state` 停在 `O1`、
   `omci_transport` 的 `ready=0 error=-67`。按判据本身，无光时 `pon_state` 不动是正常的、
   通道没 ready 时计数不动是正常的 ⇒ 判 N/A。**要判 PASS 得插上光纤。**
3. **另外三个 LAN 口没插线**，只有 `lan2`（PC 网线所在）做了实口验证；
   `lan3` / `lan4` / `lan0` 只验到「设备在、link 状态正确」。
4. **这一轮不是冷启动回归**：刷完后板子停在 U-Boot 提示符，用 `run bootcmd` 起的。
   **冷启动（断电上电）路径还没验**——而上一轮的 DTR/RTS 软复位是不管用的。
5. **没有做 WAN/PON 侧的任何验证**：`eth0` 无载波（无光），OMCI 未就绪，
   这部分属于第 4 片（协议层），不在出向修复的范围内。