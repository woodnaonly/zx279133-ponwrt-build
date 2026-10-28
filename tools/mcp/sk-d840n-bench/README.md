# sk-d840n-bench：把台架调试变成一次工具调用

SK-D840N（ZTE ZX279133 XG-PON ONU）的调试台架上，读一个 SoC 寄存器、读一个 PHY
寄存器、看一眼网口状态，原来的做法是写一段 bash 脚本、往 `staging/ctl/sercmd.txt`
里塞命令、等一轮、再从 119 MB 的 `serial.log` 里 grep。这个 MCP server 把这串动作
压成一次调用，并且**返回结构化数据**（地址 + 数值数组），而不是一屏混着串口回显的
文本。

它不改内核、不改驱动、不改 DTS，不碰刷机流程，也不实现 PON 协议层。板子侧的调试
接口已经有了（`zx279133-np.c` 的 debugfs `/sys/kernel/debug/zx279133-9132`，
20 多条命令），这个 server 只做**封装 + 解析 + 状态路由**。

---

## 1. 它怎么够到板子：relay 的客户端，不是串口的主人

**本 server 从不打开 COM3。**

`tools/serrelay.ps1` 持有 COM3，并由 `tools/relayloop.sh` 每 6 小时重启一次。
Windows 上一个 COM 口只能被一个进程打开，所以 MCP 只能做 relay 的**客户端**：

```
MCP 工具调用
   ↓
① 取锁   staging/ctl/.bench.lock   （O_CREAT|O_EXCL，写 pid + 时间戳）
   ↓
② 记 offset = os.path.getsize(staging/ctl/serial.log)
   ↓
③ 投递   写 .sercmd.mcp.<pid>.tmp → os.replace() → staging/ctl/sercmd.txt
          （同卷原子替换；Windows 上等价于 mv -f，失败重试 5 次）
   ↓
④ 确认   轮询 serial.log[offset:] 直到出现
          "=== >>> HH:MM:SS sent: <cmd> ==="
          （relay 自己写的标记 —— 这是"命令确实发出去了"的唯一可靠凭据）
   ↓
⑤ 捕获   轮询 serial.log[offset:] 直到命中 expect 正则，或超时
   ↓
⑥ 解析   parsers.* → 结构化返回
   ↓
⑦ 释放锁
```

第 ④ 步不能省：**写进 `sercmd.txt` 不等于发出去了**。relay 可能正忙、可能没跑、
`sercmd.txt` 可能被别人占着。没有那个标记，任何"成功"都是猜的。

板上会**回显**命令，所以切片里既有回显也有输出；解析前会剔除回显行和 relay 自己
的 `=== ... ===` 行。

---

## 2. 板子有两个状态，工具按状态路由

| 状态 | 判据 | 能用什么 |
|---|---|---|
| `loader` | `uname -a` 回 `Unknown command`（U-Boot 没有 uname） | `uboot_md` |
| `kernel` | `uname -a` 回 `Linux ImmortalWrt ...` | `nppt_*` / `soc_read` / `mdio_read` / `m45_read` / `xpcs_read` / `eram_read` / `net_*` / `eth_stats` / `dmesg_tail` / `snapshot` |
| `unknown` | 探针没答上 | 一律拒绝 |

**两边能读的东西是不重叠的，所以必须先判状态：**

* **loader 侧**唯一能读 **PON SerDes（0x16000000）** 和 **Uni SerDes（0x16100000）**——
  内核的 `/dev/mem` 拒绝设备地址，debugfs 只够得着 NPPT 窗口。
* **kernel 侧**能读 **NPPT 窗口**（0x19000000 起）里的 XMAC、每端口块、ERAM 表，
  以及 MDIO 上的 PHY 寄存器。

状态不匹配时工具**明确拒绝并说明原因**，不猜。判据来自 `uname -a`（10 秒缓存），
`board_state(refresh=True)` 可强制重判。

---

## 3. 工具清单

### A 组 · 只读（默认暴露）

| tool | 作用 | 底层 |
|---|---|---|
| `bench_status()` | root / relay 存活 / 日志大小 / 锁 / 板子状态 / `busy` 标志 | — |
| `board_state(refresh=False)` | `loader` \| `kernel` \| `unknown` + 证据行 | `uname -a` |
| `console_send(cmd, settle, timeout, expect)` | 通用逃生口：发任意命令 + 取回输出 | 任意 |
| `serial_tail(lines=100)` | 日志尾部（直读文件） | 文件读 |
| `uboot_md(addr, words="0x40")` | 任意绝对地址内存 dump（**仅 loader**） | U-Boot `md` |
| `nppt_reg(offset)` | NPPT 单字 | `reg` |
| `nppt_regs(offset, count=64)` | NPPT 窗口批量读（≤256） | `regs` |
| `soc_read(addr, count=1)` | 绝对地址读（白名单块内） | `soc` |
| `spa_read(mem, addr)` | SPA 间接读 | `r` |
| `mdio_read(addr, reg)` | 4 个内部 PHY 的 c22 读 | `m` |
| `m45_read(phy_addr, devad, reg)` | 外置 PHY（RTL8226）Clause-45 读 | `m45` |
| `xpcs_read(lane, devad, reg)` | MAC lane 的 SerDes PCS Clause-45 读 | `xpcs` |
| `eram_read(sdt_id, index, width)` | ERAM 表项读 | `eg` |
| `net_links()` | netdev 与状态 | `ip -o link show` |
| `net_counters()` | 每口收发字节/包 | `cat /proc/net/dev` |
| `eth_stats(iface="eth0")` | 驱动统计 | `ethtool -S` |
| `dmesg_tail(lines=100)` | 内核日志尾部 | `dmesg \| tail -n N` |
| `snapshot()` | **组合**：state + 5 段 NPPT + 4 个内部 PHY + netdevs | 上述组合 |

`snapshot()` 对应 `staging/ctl/win109.sh` 的 step 9–13，一次调用拿全，不用写脚本、
不用等一轮。

### B 组 · 写硬件（`confirm=True` 才动手）

| tool | 底层 | 说明 |
|---|---|---|
| `nppt_reg_write(offset, value)` | `regw` | 写 NPPT 单字并回读 |
| `mdio_write(addr, reg, value)` | `mw` | 写 PHY 寄存器并回读 |
| `m45_write(phy_addr, devad, reg, value)` | `m45w` | 写 Clause-45 并回读 |
| `xpcs_write(lane, devad, reg, value)` | `xpcsw` | 写 SerDes 并回读 |
| `spa_write(mem, addr, value)` | `w` | 写 SPA 并回读 |
| `eram_write(sdt_id, index, width, value)` | `es` | 写 ERAM 表项并回读 |
| `apply_xbasex(base="0x140000", speed=2)` | `xbasex` | 施加厂商 1000BASE-X 序列 |
| `board_reset(hold_ms=900)` | DTR/RTS | 复位板子 |

`confirm` 是**本项目自己的安全门**，不是 MCP 框架强制的：不带 `confirm=True` 调用，
工具只回一句"这次调用被拒绝了"，不碰硬件。`docs/92` 记过多次"写进去就回不来的字"。

`board_reset` **只写 `staging/ctl/serreset` 文件**，让 relay 去拉 DTR/RTS——那个时序
（先拉高、再拉低 hold、再拉高）是踩坑调出来的，不该由这里重写。

---

## 4. 参数名的两处说明

计划里把 `m45` 的第一个参数写成 `bus`、把 `xpcs` 的第一个参数也写成 `bus`。
驱动的真实语义是：

* `m45 <phy_addr> <devad> <reg>` —— 第一个参数是 **PHY 在外置 PHY 总线上的 MDIO 地址**，
  RTL8226 是 **5**（它挂在控制器 #1 = 0x14f02000 上）。所以工具里的参数叫 `phy_addr`。
* `xpcs <lane> <devad> <reg>` —— 第一个参数是 **lane**：0 = eth0 的 MAC（XMAC1），
  1 = LAN lane（XMAC0）。不是总线号，所以叫 `lane`。

除此之外计划里的语义都照搬。

---

## 5. 三个必须记住的坑（已经编码进工具）

1. **偏移必须带 `0x`**。驱动用 `kstrtou32(tok, 0, ...)`，不带前缀就是十进制。
   `np.c:1267-1270` 记过一次因此损失半小时的轮次（`regs 140000 64` 拿到的是
   `nppt + 0x222e0`）。⇒ 本 server **一律**输出 `0x` 前缀。

2. **偏移必须 4 字节对齐**。NPPT 窗口是设备内存，未对齐的读写是 alignment fault，
   **会把内核打挂**（`np.c:1291-1294`：round 133 读 `+0x40002` 导致 panic）。
   ⇒ `nppt_reg` / `nppt_regs` / `nppt_reg_write` 在发命令**之前**就校验对齐。

3. **`m` 走的是 MDIO 控制器 #0（0x14f01000）**，上面只有 4 个内部 PHY
   （addr 0x0a–0x0d）。外置的 RTL8226 在**控制器 #1**（0x14f02000），PHY 地址 5，
   `m` 在它上面回 `0xffff`。⇒ `mdio_read` 读回 `0xffff` 时会带一条 `bus_hint`。

4. **U-Boot 的 `md <addr> <count>`，`count` 是**字数**不是字节数**。实测
   `md 0x16000000 0x40` 出的是 **64 个字**（16 行 × 4），不是 16 个字——`md` 的
   第二个参数是「个数」，默认 `.l`（32 位一项）。所以 `uboot_md` 的参数叫 `words`，
   返回值里同时给 `words_read` 和 `bytes`。

另有一条命令级禁令：`zxboot`（无参会 Synchronous Abort 并复位 CPU）和 `up_mode get`
（读 MTD label `others`）**不会**被 `console_send` 发出去（`tools/ubcmd.sh:13-16`）。

---

## 5.1 动词在不在，取决于**跑着的镜像**

debugfs 那些动词是随内核镜像一起编进去的，所以**镜像比 `kernel-overlays` 里的
`np.c` 旧时，动词就不存在**，板子会回一句
`ash: write error: Invalid argument`（= -EINVAL），而不是没有回答。

⇒ **「某个工具能不能用」取决于板上此刻跑的是哪个镜像，不是 MCP 的问题。**
实测两次，板子中途被换成另一个镜像，同一套工具的结果就变了：

| 动词 | 加入 `np.c` 的提交 | 旧镜像（内核 10-23 构建） | 新镜像（内核 10-26 04:58 构建） |
|---|---|---|---|
| `reg` / `regs` / `r` / `m` / `eg` | 更早 | ✅ | ✅ |
| `m45`（外置 PHY Clause-45） | `7d75ef4`（2026-10-26 22:01） | ❌ 拒绝 | ✅ 读得到 |
| `xpcs`（lane 的 PCS） | `d9cf8e2`（2026-10-27 22:48） | ❌ 拒绝 | ✅ 读得到 |
| `soc`（SoC 侧绝对地址） | `7b36488`（2026-10-28 13:55） | ❌ 拒绝 | ❌ 仍被拒（镜像早于该提交） |
| `xbasex` | `2b3f267`（2026-10-28） | ❌ | ❌ |

⇒ `soc_read` 与 `apply_xbasex` 在**当前镜像上会失败**，`m45_read` / `xpcs_read`
现在可用。要用 `soc`，得换一个含 `7b36488` 的镜像。

这个失败是**快速**的：MCP 会认出 shell 的拒绝（`ash: write error: ...`）并立刻
返回那句话，而不是干等到超时——「拒绝」和「没人回答」必须分得开。

---

## 6. 并发：它挡得住什么，挡不住什么

| 冲突 | 挡得住吗 | 靠什么 |
|---|---|---|
| 两个 MCP 调用互相踩 | ✅ | `.bench.lock`（O_CREAT\|O_EXCL）+ 进程内互斥 |
| 进程被杀留下锁 | ✅ | 锁带时间戳，超过 10 分钟视为 stale 自动接管 |
| 别人的命令在途 | ✅ | 投递前检查 `sercmd.txt` 是否存在（在途 = 忙，拒绝） |
| **别人手跑 `win1xx.sh` 轮次** | ❌ | `win1xx.sh` 不认这把锁——那是别人正在用的脚本，本次不改 |

所以 `bench_status()` 会暴露 `busy` 标志和最近的 relay 标记：**`busy: true` 时不要
发起 MCP 调用**。要彻底解决 R1（与另一会话争用），正确做法是给 `win1xx.sh` 也加同一
把锁——那是改他人正在用的脚本，本次不做。

`serial.log` 已经 119 MB 且还在长。本 server **只读增量**（`seek(offset)` 读切片），
绝不整文件加载；`serial_tail()` 是从文件尾往回读的。

---

## 7. 依赖与运行

* Python 3.10+、`mcp`（FastMCP）、`pytest`（单测用）。
* **不需要** `pyserial` / `paramiko`：attached 模式下不碰串口，板子网络不通时也不做 SSH。

注册（stdio）：

```json
{
  "name": "sk-d840n-bench",
  "transport": "stdio",
  "command": "python.exe",
  "args": ["C:/UGit/openwrt/SK-D840N/ci-build-repo/tools/mcp/sk-d840n-bench/server.py"],
  "env": { "SK_D840N_ROOT": "C:/UGit/openwrt/SK-D840N" },
  "enabled": true
}
```

**root 怎么定的**：先看环境变量 `SK_D840N_ROOT`；没有就用本文件往上 4 级
（`ci-build-repo/tools/mcp/sk-d840n-bench/` → 工作区根）；还是没有就报错退出并列出候选。

单测（离线，不碰板子）：

```sh
python -m pytest ci-build-repo/tools/mcp/sk-d840n-bench/test_bench.py
```

离线模式：`SK_D840N_BENCH_OFFLINE=1` 时，工具只读一份已保存的日志切片
（`SK_D840N_BENCH_OFFLINE_LOG` 指定文件，否则读日志尾部），**不发任何命令**。
单测和 CI 都能在这个模式下跑。

---

## 8. 它不做什么

* 不改内核、驱动、DTS；不动刷机流程（`flash-direct.sh` / `ubflash.sh` 那一类）。
* 不实现 PON 协议层（数据面在主线，协议层在厂商 4.19，见 `docs/HANDOFF.md` §1）。
* 不替代 bench 脚本：`staging/ctl/win1xx.sh` 那些轮次脚本继续存在，MCP 是并行的、
  粒度更细的入口。
* 不做 CI / build 状态查询（那是 `tools/jobstatus.py` 的领域）。
* 不占用 COM3，不重启 relay，不终止 `relayloop.sh` / `serrelay.ps1`。

---

## 9. 已知问题（只记录，不在本次修）

**`tools/serrelay.ps1:168-170` 的清理逻辑反了**：它
`Sort-Object LastWriteTime | Select-Object -Skip 20 | Remove-Item`，也就是
**保留最旧的 20 个、删掉最新的**。实测目录里 20 个 `.sent-*` 全是 2026-10-20 的，
今天的全被删了。

⇒ 所以本 server **不用 `.sent-*` 文件做审计**，而是解析日志里的 `sent:` 标记。
这个 bug 让 `.sent-*` 失去审计价值，但不影响 MCP。那个文件正被 relayloop 使用，
改它有风险，本次不修——可另起一条 issue。
