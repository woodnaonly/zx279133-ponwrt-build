# 107 — 面板口当 WAN：LAN/WAN 自适应、第二条 WAN 与「WAN 接入口」页面

> 本文承接 `docs/106-the-tcp-checksum-is-half-there-20261126.md`。
> 106 号记录的是 7.3 固件上 TCP 校验和只对了一半；本文记录的是**另一件事**：
> 这块光猫的面板网口（lan1..lan4）能不能当 WAN 用，怎么当，以及页面怎么配。

---

## 一、判决

1. **面板上的任何一个网口都可以当第二条 WAN**，不限于光口。
   光口（`eth0`，PON 侧）恒是 WAN1，不参与自动降级；lan1..lan4 才是可切换的面板口。
2. **换口是一次事务，不是一次改配置。**
   改之前先把旧状态写进 `/run`，改完提交并回读**已提交的文件**确认改动真的落盘，
   然后起一个 90 秒的看门狗；90 秒内没人确认，旧状态自己回来。
   页面里点「确认保留」即取消回滚。
3. **NPPT 的端口类型跟着协议走，不跟着防火墙分区名走。**
   PPPoE 的 WAN 口翻成 WAN 型（硬件剥/加 PPPoE 头）；DHCP、静态地址的 WAN 口保持 LAN 型
   （普通以太网帧）。驱动的 `wan_type` 参数被显式写成 0，端口类型只由用户态改。
4. **自适应靠 DHCP 探测**：谁应答谁当第二条 WAN。
   口临时出桥发一次 DHCP；应答就采纳，不应答就放回；管理口和用户排除的口永远不动。
5. **两条 WAN 主备切换，不引入 mwan3。**
   首选的一条断了，另一条顶上；首选的回来了，切回去（有迟滞，三轮好/三轮坏才动）。
6. **Web 界面有独立页面「WAN 接入口」**（`admin/onu/wanport`），不塞进「上网配置」。
7. **上游 `zx279133-wan-ports` 在本板是空操作，已被本板正确版覆盖。**

---

## 二、硬件与现状（实测）

### 2.1 板子的口

| 名字 | 是什么 | 说明 |
|---|---|---|
| `eth0` | NPPT 自己的网口 | PON 侧，恒为 WAN1 |
| `lan-cpu0` | DSA conduit | CPU 自己的通道，**不是插孔**，也永远不进桥 |
| `lan0` | 9132 的无插孔口 | transport VID 59 |
| `lan1` | 2.5G 插孔 | DSA p8，VID 63 |
| `lan2`/`lan3`/`lan4` | 千兆插孔 | DSA p5/p6/p7，VID 60/61/62 |

实测（`/sys/class/net`）：

```
lan1  lower_lan-cpu0 -> .../ethernet-lan-cpu/net/lan-cpu0   master br-lan
lan-cpu0  upper_lan0..upper_lan4   /sys/class/net/lan-cpu0/dsa 存在
br-lan  lower_lan1 lower_lan2 lower_lan3 lower_lan4
```

⇒ **面板口是 DSA 用户口，lower 是 conduit**，所以「这个口在不在桥里」
就是「它是 LAN 口还是 WAN 口」。

### 2.2 上游 `zx279133-wan-ports` 为什么在这块板上是空操作

上游脚本用「端口的 lower 是不是 DSA conduit」来认端口，并且**在桥里的口直接跳过**。
这块板上：

* `eth0` 没有 lower → 认不出；
* `lan1..lan4` 在桥里 → 也被跳过；
* WAN 分区里只有 `eth0`（光口）→ `wan_ports` 永远是空。

⇒ 上游脚本在本板**什么都没做**，`wan_ports` 属性一直是空的（实测）。

### 2.3 `wan_ports` 属性的语义（读 7.3 补丁 `370-net-zx279133-npu-rule-api.patch`）

* 路径：`/sys/class/net/eth0/device/wan_ports`（本板实测存在，权限 `-rw-r--r--`）；
* 写 `<port> 1` → 该口翻成 WAN 型；写 `<port> 0` → 翻回 LAN 型；
* **口还在桥里时写 1 会被驱动拒绝（`-EBUSY`）**：在桥里的口是 LAN 口；
* 读回来是当前处于 WAN 型的口名列表。

### 2.4 驱动的 `wan_type` 参数

`kernel-overlays/.../zx279133-switch.c`：`wan_type=1` 时，驱动在 DSA 口**离开桥**时
自动把它翻成 WAN 型；`wan_type=0`（默认）时驱动不管端口类型。
本板实测 `/sys/module/zx279133_switch/parameters/wan_type` = `N`。

⇒ 上游那条「树里的规则」在本板会把**每一个离开桥的口**都变成 WAN 型，
不管它当什么用。本需求要自己管端口类型，所以这个参数被显式写成 0。

---

## 三、做了什么

### 3.1 文件清单

| 文件 | 作用 |
|---|---|
| `overlays/target/linux/zte/zx279133/base-files/usr/libexec/sk-wan-switch` | 特权助手：改口、回滚、看门狗、状态、确认 |
| `overlays/target/linux/zte/zx279133/base-files/usr/libexec/sk-wan-adapt` | 自适应守护：DHCP 探测 + 主备切换 |
| `overlays/target/linux/zte/zx279133/base-files/etc/init.d/sk-wan-adapt` | 自适应守护的 init 脚本（START=90） |
| `overlays/target/linux/zte/zx279133/base-files/etc/config/sk_wan` | 策略文件：auto / primary / exclude |
| `overlays/target/linux/zte/zx279133/base-files/etc/uci-defaults/99-sk-wan2` | 首次开机建 `network.wan2`、防火墙分区、转发 |
| `overlays/target/linux/zte/zx279133/base-files/usr/libexec/zx279133-wan-ports` | **覆盖上游**：端口类型按协议决定 |
| `overlays/target/linux/zte/zx279133/base-files/etc/init.d/zx279133-wan-ports` | **覆盖上游**：服务的注释与触发 |
| `overlays/package/luci-app-sk-d840n-pon/htdocs/luci-static/resources/skd840n/wanport.js` | 页面助手（走 `file exec`） |
| `overlays/package/luci-app-sk-d840n-pon/htdocs/luci-static/resources/view/skd840n/wanport.js` | 「WAN 接入口」视图 |
| `tests/sk_wan_switch_fixture.py` + `tests/fixtures/sk-wan/*` | 沙箱板子：假 uci / ip / brctl / ubus / udhcpc |
| `tests/test_sk_wan_switch.py` | 事务与回滚的门禁 |
| `tests/test_sk_wan_adapt.py` | 自适应与主备的门禁 |
| `tests/test_sk_wan_ports.py` | 端口类型的门禁 |

### 3.2 为什么特权助手是独立程序，而不是 LuCI 的 uci 通道

桥的端口表是 **list 选项**：

```
config device
        option name 'br-lan'
        list ports 'lan1'
```

LuCI 的 uci 通道**没有 `add_list`**：写 list 只能先删掉再整条写，
写成 `uci set network.@device[0].ports='lan1 lan3'` 的话，
内核拿到的就是一个叫「lan1 lan3」的端口，桥里什么都没有。
所以端口表由板子上的程序写（和 `tools/portmove.sh` 同一个路子）。

**回滚也因此自己扛。** LuCI 的 `uci apply(rollback=true)` 只回滚走 LuCI 通道的东西；
端口表不走那条路，于是「LuCI 回滚」会把 `wan2` 设备撤了、却把口留在桥里 ——
两头不靠的中间状态，看起来就像一个死了的插孔。
所以回滚在助手自己这边：旧状态写在 `/run`（tmpfs，断电即忘），
90 秒内没人确认就还原。**这与计划里的偏离已记录在案。**

### 3.3 事务的形状

```
apply-wan2 --device <lan1..lan4|none> [--proto P] [--username U] [--password P]
           [--ipaddr I] [--netmask N] [--gateway G] [--primary wan|wan2]
reset        取消第二条 WAN，所有面板口回桥，光口回到唯一 WAN
confirm      确认保留（取消 90 秒回滚）
rollback     立刻还原
status       打印一个 JSON 对象
watchdog S T 看门狗：S 秒内没有确认就还原
mgmt-port A  管理客户端在哪个插孔上
```

顺序是：

1. `flock` 互斥；
2. 读现状（桥里有哪些口、旧的 wan2 是什么）；
3. 写 `/run/sk-wan-switch.state`（**每个值都单引号引起来**，见 §5.1）；
4. 删掉端口表，再一条一条 `add_list` 写回去（**新口 − 旧口 + 旧口回桥**，最小改动）；
5. 写 `network.wan2`、防火墙分区与转发，提交；
6. **回读已提交的文件**确认桥里确实有新口、wan2 段确实带着那个设备；
7. 不通过就还原并报错；
8. 起 90 秒看门狗；
9. 重载网络，再重载防火墙（防火墙在后，wan2 界面才存在）。

### 3.4 端口类型

本板正确版的 `zx279133-wan-ports`：

* 驱动的 `wan_type` **显式写 0**；
* 端口类型**按 `network.<wan>.proto` 决定**：
  * `pppoe` → 该口翻成 WAN 型（硬件剥/加 PPPoE 头）；
  * `dhcp` / `static` → 该口保持 LAN 型；
* 只读**当前处于 WAN 型的口**（属性读回来就是），
  把不该是 WAN 型的写回 0、把该是的写成 1 —— 最小改动，不来回翻；
* 写 1 被驱动拒（`-EBUSY`）时**重试**（口刚出桥、内核还没处理完是常态）；
* 实在写不进去就**报出来**，不闷声留着错的类型。

### 3.5 自适应

* 候选 = 面板口 − 管理口 − 用户排除的口 − 现在就是 wan2 的口 − 自己有地址的口；
* 探测：口临时 `nomaster`（桥成员不能持有地址），
  `udhcpc -n -q -t 2` 只**记录 offer 不配置**，应答即候选；
* 不应答 → 放回桥里；应答 → 交给网络服务（由它持有租约），
  起一个宽限期看着 wan2 是否真的拿到地址；拿不到就还原；
* **管理口永远不动**：管理客户端的 MAC 从桥的转发表里查出来，
  再用 `/sys/class/net/br-lan/brif/<口>/port_no` 把端口号对回口名；
  **查不到就一个口都不抢**（抢错口就是抢别人的口）。

### 3.6 页面

`admin/onu/wanport`，标题「WAN 接入口」：

* 当前状态：两条 WAN 的设备/协议/连接情况、桥里有哪些口、NPPT 的端口类型、管理口；
* 表单：接入口（lan1..lan4 / 不使用）、上网方式（DHCP/PPPoE/静态）、主备、
  自适应开关、排除的网口；
* 应用 → 走 `file exec` 调 `sk-wan-switch apply-wan2`；
* **选的正好是管理口时，页面明确警告「管理连接会断，90 秒内没有确认就自动还原」**，
  并在提交前再确认一次；
* 提交后页面每 3 秒探一次板子：**连续 6 次（18 秒）都通**，才自动「确认保留」；
  链路断了就一直不通 → 看门狗在 90 秒后还原。

权限：ACL 里给 `luci-app-sk-d840n-pon` 加了 `file:["exec"]`（读、写两处）与 `sk_wan` 的 uci 读写。
`file exec` 只跑一个固定路径的程序（`/usr/libexec/sk-wan-switch`），页面不能自己指定命令。

## 四、验证

### 4.1 仓库内门禁

| 门禁 | 内容 | 结果 |
|---|---|---|
| `tests/test_sk_wan_switch.py` | 15 个用例：目标校验、桥表计算、幂等、回读校验失败即复原、confirm/rollback、看门狗、状态文件格式 | 通过 |
| `tests/test_sk_wan_adapt.py` | 7 个用例：候选集（排除管理口/排除列表）、逐口探测与放回、应答口被采纳、管理口映射失败时不抢口、主备 metric 对调与迟滞 | 通过 |
| `tests/test_sk_wan_ports.py` | 8 个用例：proto=pppoe ⇒ `"<port> 1"`；dhcp/static ⇒ `"<port> 0"`；无 WAN2 ⇒ 全部 `0`；写 1 遇 EBUSY 重试 | 通过 |
| `tests/test_sk_pon_ui.js` | 视图数 8→9；新视图在正常/RPC 失败/只读三种场景下渲染，无 `NaN`/`undefined`；警告文案存在 | `PASS: 36 view renders` |
| `tools/check-sk-workflow-paths.py` | workflow 跑的每个文件都要被 `paths:` 覆盖 | `OK: 286 check(s)` |
| `tools/prepush.py --overlay-modes` | 镜像要**运行**的文件在 git 里必须是 100755（执行位，见 §5.2（三）） | `0 problems` |

三个新测试共 **30 个用例**（15 + 8 + 7），全部通过；新增的 fixture 也进了
两个 workflow 的 `paths:`，并进 `check-gates`（73 系）与 `check-led-gates`（主线）步骤。
执行位检查（§5.2（三））也进了这两个门禁步骤：它以前只在 build job 里跑，
比门禁晚半小时，而门禁先炸的时候它连说话的机会都没有。

### 4.2 实机验收（SSH，2026-11-26）

实机验收走 SSH（`root@192.168.9.1`，空密码，PuTTY plink + 固定主机密钥），
不用串口 bench。已做并通过：

| # | 验收项 | 观察到的结果 |
|---|---|---|
| 1 | `zx279133-wan-ports` 能跑 | rc=0；`/sys/module/zx279133_switch/parameters/wan_type` 保持 `N`（即 0）；`wan_ports` 为空 |
| 2 | `sk-wan-switch status` 是合法 JSON | 解析成功；桥 = `lan1 lan2 lan3 lan4`；`primary=wan`；`adaptive.auto=1` |
| 3 | 管理口识别 | `sk-wan-switch mgmt-port 192.168.9.25` ⇒ `lan1`（与 `brctl` 转发表一致） |
| 4 | 手动指派 WAN2 | `apply-wan2 --device lan2 --proto dhcp` ⇒ 桥里少了 lan2；`network.wan2.device=lan2`；防火墙 `wan2` 分区与 `lan→wan2` 转发就位；**LAN 未断** |
| 5 | 显式回滚 | `sk-wan-switch rollback` ⇒ 桥恢复 `lan1 lan2 lan3 lan4`；`network.wan2` 置 `disabled=1`；**LAN 未断** |
| 6 | 90 秒看门狗自动还原 | 指派后不确认 ⇒ 到点自动还原：桥恢复、wan2 disabled、`pending=false` |
| 7 | 状态文件干净 | 板端 `busybox hexdump -C` 看 `/run/sk-wan-switch.state`：无 CR/LF 混入，JSON 可解析 |
| 8 | 板上的文件与仓库一致 | 三份脚本 + 两个 init.d + `sk_wan` + 两个 LuCI js + menu/acl，共 10 个文件，板端 md5 与仓库逐个相同；三份脚本 `sh -n` 通过 |


### 4.3 还没在实机上验的

| # | 项 | 为什么还没验 |
|---|---|---|
| 1 | 自适应探测（DHCP 判据） | 需要在某个面板口外挂一台会应答 DHCP 的设备；本轮板子没接 |
| 2 | 主备 metric 对调 | 需要拔掉光口信号或让光口租约掉线 |
| 3 | PPPoE 端口类型翻转 | 需要一条真的 PPPoE 线路；`wan_ports` 写 `"<port> 1"` 的路径只在脚本级验过 |
| 4 | LuCI 页面真机点击 | 本轮只跑了 node 的渲染测试，没在真机的 LuCI 上点过 |
| 5 | 刷机后 `99-sk-wan2` 首次开机建段 | 需要 CI 出件后刷机才会走到 |
| 6 | 跨重启形态保留 | 看门狗在 tmpfs，但已 commit 的形态应保留 —— 同样要刷机后才验得到 |

---

## 五、偏离与事故

### 5.1 相对计划的偏离

| # | 计划里写的 | 实际做的 | 为什么 |
|---|---|---|---|
| 1 | 回滚用 LuCI 的 `uci apply(rollback=true)` | **助手自带 90 秒看门狗** | rpcd 的 uci 没有 `add_list`，桥的端口表是 list 选项，LuCI 通道既写不了也不回滚。对外表现一致（不确认即自动还原），覆盖范围更大 |
| 2 | 策略路径 `sk_wan.primary` / `sk_wan.auto` / `sk_wan.exclude` | `sk_wan.wan.primary` / `sk_wan.wan.auto` / `sk_wan.wan.exclude` | 段头是 `config settings 'wan'`，uci 的段路径带段名 |
| 3 | 取消 WAN2 时删掉 `network.wan2` 段 | **保留段并置 `disabled=1`** | 防火墙的 `wan2` 分区引用 network `wan2`，段消失 fw4 会告警 |
| 4 | 防火墙段名未指定 | 命名段 `firewall.wan2` / `firewall.lan_wan2` | 方便脚本按名字定位，不依赖匿名序号 |
| 5 | 「读现状、最小改动」的桥表算法 | 照做：读现状 → 新口 − 旧口 + 旧口回桥；`lan-cpu0` 绝不从桥里移除 | 计划 §6 R1 的处理原则，不写死端口表 |
| 6 | `sk_wan.auto` 默认未指定 | 默认 **1**（开） | 「插线即认」是本需求的核心，默认关等于功能没上线 |
| 7 | 管理口用 dmesg 的 `port N(<name>)` 映射 | 改用 `brctl showmacs` + `/sys/class/net/br-lan/brif/<口>/port_no` | 内核重建桥会重新编号，dmesg 里的旧行会过期；端口号映射是活的。**映射失败就一个口都不抢** |
| 8 | network reload 后跑 `zx279133-wan-ports` | `network reload` → sleep 5 → `firewall reload` → `zx279133-wan-ports` | 防火墙在后，wan2 的界面才存在；端口类型必须在网桥生效之后才写 |
| 9 | 端口类型按协议决定 | 照做，并把 `wan_type` **显式写 0** | 类型只由用户态改，不与驱动的 `bridge_leave` 回调抢同一个寄存器 |
| 10 | `PKG_RELEASE` 4→5 | 3→4 | 仓库里的实际当前值是 3，按「+1」的意图走 |
| 11 | 并发锁用 `mkdir` 原子锁 | 改成 `flock`（`/run/sk-wan-switch.lock`） | `mkdir` 锁在异常退出后会留残目录，`flock` 随 fd 自动释放 |
| 12 | 助手第一次动网络时补 `network.wan6` | **不补** | 计划的前提是「fw4 会因缺 wan6 告警」，实测 `fw4 check` 直接通过、不告警；而补一个 `device=eth0, proto=dhcp` 的 wan6 会在光口上**多起一个 DHCP 客户端**与 `wan` 抢租约，风险大于收益。上游 zx279133 也没有 wan6 |

### 5.2 测试和 CI 抓到的三个真问题

**（一）主备对调只改了一半。**
`sk-wan-adapt` 的 `set_metrics` 直接写 `network.wan.metric` 和 `network.wan2.metric`。
`network.wan2` 段不存在时，第二条**写不进去**（uci 返回 1），而脚本照样往下走：
一个 metric 改了、另一个没改 —— 两条 WAN 的优先级变成一样，流量没有偏好，
**而且没有任何人会说这件事**。
修法：先确保段在，再写；两条都写成功才 commit，否则**报出来、放弃这次对调**。
（这是同一个形状的毛病：上一次是回滚把桥写空，这一次是对调只做一半。）

**（二）沙箱板不像实机板。**
夹具的 `default_network()` 里没有 `network.wan2` 段，而实机板**从第一次开机起就有**
（`99-sk-wan2` 建的：proto dhcp、metric 20、disabled 1、没有 device）。
所以主备切换的两个用例是在一块「根本没有第二条 WAN」的板上测的 ——
那块板上本来就不该有备，两个用例理应失败，是夹具骗了它们。
修法：夹具的默认网络改成和实机板一样带着 `wan2` 段，
两个自己拼段的测试改成用夹具，不再各拼各的。

**（三）文件模式差一位：本地看不见，CI 上炸，而板子上会静默地坏。**

`sk-wan-adapt` 是直接执行助手的：`"$SWITCH" mgmt-port "$1"`。
而仓库里 `sk-wan-switch` 的 git 模式是 **100644** —— 没有执行位。

* **Windows 上不复现**：MSYS 的 shell 不看执行位，`sh <脚本>` 一律能跑；
* **Linux 上必炸**：CI 的 runner 上执行一个 644 的脚本是 `Permission denied`。
  管理口因此查不到，守护按「认不出管理口就不抢任何口」的规矩**一个口都不认领**，
  `network.wan2.device` 留空 —— 用例断言 `lan3` 失败。

CI 的公开注解把话说全了（run 45759412243）：

````
the gate that failed: python3 tests/test_sk_wan_adapt.py
AssertionError: '' != 'lan3'
sk-wan-adapt: the management port is not known; no port is taken this round
sk-wan-switch: Permission denied
````

**这不只是 CI 的事。** overlay 是用 `cp -fpR` 拷进镜像的 ——
**git 的模式就是镜像里的模式**（板上的 `/etc/init.d/sk-bridge` 就是 755，
因为它在 git 里是 100755）。644 的后果在板子上是：

| 文件 | 644 的后果 |
|---|---|
| `/usr/libexec/sk-wan-switch` | 不可执行 ⇒ 页面的 `file exec` 直接 EACCES，**「WAN 接入口」页面的每一个按钮都不动** |
| `/etc/init.d/sk-wan-adapt` | 不可执行 ⇒ init 系统不跑它，自适应永远不开始 |
| `/etc/init.d/zx279133-wan-ports` | 不可执行 ⇒ init 系统不跑它，NPPT 端口类型不跟着协议走 |
| `/etc/uci-defaults/99-sk-wan2` | 它是被 source 的，模式不影响（`docs/89` 记的就是这件事） |

⇒ **这六个文件在 git 里必须是 100755**（`git update-index --chmod=+x`）。

仓库里早就有这条检查：`tools/prepush.py` 的 `overlay_modes_check()`，
`docs/89` 记的就是同一类事故（NAT 的 init 脚本 0644，镜像里不跑）。
但它跑在 **build job** 里，比门禁晚半小时；门禁先炸的时候它连说话的机会都没有。
本地的 prepush hook 又没有装（`.git/hooks` 是空的）。
⇒ 检查挪进两个门禁步骤，并给它一个单跑模式（`--overlay-modes`），
失败时打 `::error::` 注解 —— 免 token 的机器只读得到注解。

⇒ 另外，守护与看门狗改为**经 `sh` 调用**助手（`sh "$SWITCH" ...`、
`setsid /bin/sh "$SELF" watchdog`）：执行位掉了，守护还能工作，
而不是变成一个「找不到管理口、于是什么都不做」的哑巴。

### 5.3 实机事故：第一次验收把 LAN 全断了

**现象**：第一次实机验收时，提交换口后 90 秒，板子的 LAN 整个不可达，
SSH 连不上，桥表里一个口都没有。

**根因**：`save_state` 写出的状态文件是

```
BRIDGE_PORTS=lan1 lan2 lan3 lan4
WAN2_DEVICE=lan2
...
```

端口表是**带空格的三四个词**，而回滚用 `. "$STATE"` 把状态文件当 shell 脚本 source。
于是 `BRIDGE_PORTS=lan1 lan2 lan3 lan4` 被当成一条命令执行 ——
变量 `BRIDGE_PORTS` 根本没被赋值（只赋了 `lan1`）。
回滚逻辑拿到空的端口表，**把桥的端口表删干净了**。

同一个坑还有第二个触发点：本地测试用的假 uci 在 Windows 上写文件默认 CRLF，
尾部的一个 `\r` 会粘到最后一个端口名上（`lan4\r`），JSON 也随之非法。

**修复**（三处一起改，缺一不可）：

1. `shell_quote()`：状态文件里**每个值都单引号引起来**，密码这种什么都可能有的值也安全；
2. `restore` 遇到**空的端口列表直接拒绝还原**并退出非零 —— 不允许把桥写空；
3. 假 uci 加 `sys.stdout.reconfigure(newline="\n")`，只写 LF。

另外，`status` 打印的 JSON 也做了加固：`json_escape()` 去掉 CR/LF，
`bridge_ports()` 读回来的端口表也 `tr -d '\r'`。

**恢复过程**：板子不可达时唯一的入口是串口，但串口的 relay 进程**早就死了**
（`sercmd.txt` 里排队的命令从未执行，`serial.log` 停在 09:32:36），
所以连「在串口上敲命令救板子」这条路都是断的。做法是：

1. 重启 relay（`staging/ctl/relaystart.sh 12`），确认 COM3 可打开；
2. relay 起来后先把排队的那条命令发出去；
3. 用 MCP `console_send` 从 `/root/backup-wan/*.bak` 恢复 `network` 与 `firewall`，
   再 reload，板子恢复。

⇒ **教训**：串口 relay 是这条链路上唯一的「第二条命」，它死了没人知道，
排障时先确认它活着，再动网络。

### 5.4 板子是共用的：一次传到一半的文件

验收期间板子突然连不上了。查串口 bench：板子停在 **U-Boot 提示符**（不是内核），
日志里另有人在跑 `mtd read kernel` / `crc32` / `Erasing ... (64 eraseblock(s))` ——
**有人在刷 kernel**。SSH 连不上是因为板子压根没在跑内核，与本轮的改动无关。

板子回来后又发现 `/usr/libexec/sk-wan-adapt` 变成了 **4096 字节、语法报错**的文件：
头部与仓库里那份**逐字相同**，只是被截断在 `candidates()` 函数中间。
⇒ 是另一个人（或另一个会话）在用串口**分块上传同一份文件**，传完第一个 4096 字节块就停了。
那份文件留在板子上是**跑不起来的**（`sh -n` 直接报 unexpected end of file）。

处理：串口是别人的，不碰；用 SSH（自己的通道）把完整版放回去，
md5 与仓库一致、`sh -n` 通过。板子上现在三份脚本的 md5 都与仓库一致。

⇒ **教训**：板子和串口都不是自己独占的。别人传了一半的文件会让板子处于
「看起来部署过了、其实根本跑不起来」的状态 —— 部署完必须核对 md5 和语法，
不能只看文件在不在。

### 5.5 这一轮的边界

* 板子上部署的是**加固后**的 `sk-wan-switch`（含 `shell_quote`、`restore` 空表保护、
  `json_escape`、`bridge_ports` 去 CR）；
* 改配置的路径**全部**走 SSH 脚本，不经 LuCI 页面；页面的 `file exec` 通道**本轮没在真机上点过**；
* `sk-wan-adapt` 服务在板子上是 **inactive**（本轮只落文件，没有 enable）：
  没有上行口时它会周期性地把面板口拿出桥发 DHCP —— 正是计划 §3 里 S26nat 踩过的那个坑；
  刷机后它随 `START=90` 开机自启，届时 `sk_wan.auto` 默认 1；
* **没有**刷机：CI 出件、刷机、刷机后的首次开机流程都还没跑；
* 本机没有 arm64 交叉工具链，构建只能等 CI；
* 板子是**共用的**：验收期间另一个会话在串口上刷 kernel、也往板子上传同一批文件
  （见 §5.4）。串口没有占用，但板子随时可能被重刷 —— 板上的东西要能重来。



