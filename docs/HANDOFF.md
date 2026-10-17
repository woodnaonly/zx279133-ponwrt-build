# SK-D840N4 交接清单

面向「接手这件事的人」。**详细证据在 `docs/07-sk-d840n-status-20261014.md`（5679 行）**，
这里只写交接需要的东西。

> **这份文件现在在版本控制里**：`ci-build-repo/docs/HANDOFF.md`（2026-10-16 纳入，
> 提交见下）。你手上这个 `C:\ugit\SK-D840N4\HANDOFF.md` 是**工作副本**，
> 改动请改工作副本再同步过去——仓库里的是交付副本。
>
> 纳入之前它一直躺在项目根、不在 `ci-build-repo` 里，所以 clone 下来的人看不到它；
> 那时候这句「不在版本控制里」是实话。现在不是了。

---

## 1. 这是什么

把 `immortalwrt`（上游 OpenWrt 的 ImmortalWrt 分支）改造成能刷进
**天邑 SK-D840N4**（ZTE/中兴 ZX279133 GPON ONU）并正常启动的固件。

**支持代码不进 `immortalwrt` 主树**，全部经 `ci-build-repo/overlays/` 与
`ci-build-repo/kernel-overlays/` 在 CI 注入。`immortalwrt` 至今**一个字节都没改过**。

一句话口径：**这棵树实现的是 PON 数据通路，不是 PON。**
数据面（SerDes/MAC/NPPT/MDIO/Realtek RTL8226B PHY）在主线；
协议层（PLOAM 状态机、GEM/T-CONT 开通表、OMCI）只在厂商 4.19 模块里，
主线内核上一张 PON 表都没编进去。**这是有意为之，不是没做完。**

发布说明里「机制在、四个精确 token 不在」是分开陈述的两件事，别合并成一句。

---

## 2. 现在在哪

| | |
|---|---|
| `immortalwrt` | 干净，HEAD `73fb84b`，**未改** |
| `ci-build-repo` | 已推 `origin/main`，HEAD `0778093`，工作区干净 |
| 离线回归 | **39 个套件，`rc_all=0`，0 失败** |
| 设备 | **从这台机器不可达**（2026-10-16 11:30 实测：只有 `192.168.23.54/24`，`.9.1`/`.9.2`/`192.168.1.1` 全无应答，ARP 表无条目）。最后实测状态见 §4 |
| CI | run 27 = SUCCESS（1h13m57s）；run 31 在飞。**注意没有 concurrency，四个 run 并行跑** |

> **「没有实机运行时证据」这句话是有范围的**（2026-10-16 更正）。
> 准确说法是：**我自己验证过的范围内没有**。
> 仓库里另有他人提交的 2026-10-16 板子实测记录——
> `ea70142` / `eeed421` / `0d30cc7` 描述了 RAM trial 变哑和 panic 后 30 秒重启，
> 那部分**不是从这台机器做的，我无法复现，也不为其背书**。
> 见 `docs/07` 补四十三。

`ci-build-repo` 至今的提交：

* `781495d` — 驱动门禁修好 + 内核版本单一真源 + release body 的 PHY 描述改正
* `27bc7a3` — Unim 门钉到 `$KVER` + `kernel-overlays` 钉 LF
* `3478d5c` — release body 的 PON 断言改写（四个精确 token 只成立一个）
* `7308548` — `tools/`、`docs/`、`pon/` 文本纳入版本控制
* `5f49fe5` — 补三十七记入状态文档
* `cc73856` — 停止指示操作者去钉一个刷完就不存在的地址
* `f96026f` — 按镜像实测改正那句机制描述（上一条我写得不准）
* `fe05f44` — 补三十八记入状态文档
* `f877861` — DTS 内容门加致命前置守卫：blob 缺失不再能静默跳过而 step 仍报成功
* `8e2507b` — 记录「跳闸形状仅此一处」
* `75651fb` — `.gitattributes`：`decompiled/**` 钉 LF，并覆盖 `.gitattributes` 自己
* `0778093` — 补齐最后几个 Windows clone 的 EOL 缺口；`dtb/**` 钉 `-text`

### 工具现在受版本控制了（2026-10-15 变更）

```
C:\ugit\SK-D840N4               -> NOT A REPO
C:\ugit\SK-D840N4\ci-build-repo -> repo（只有它和 immortalwrt 受管）
ci-build-repo/tools/gettrial.sh tracked by git: YES（7308548）
```

`gettrial.sh` 的两个缺陷、`preflight.sh` 的一个缺陷，
2026-10-15 在本机修好后曾**只存在于工作副本**——重新 clone 拿不到带 bug 的版本。
`7308548` 已经把它们交付出去，并用一次真实的 `--depth 1` clone 验收过：
三个修复在 clone 里全部存在。

**仍然不在版本控制里的**，以及为什么：

| 东西 | 原因 |
|---|---|
| `pon/vendor-modules/`（18 个 `.ko`，7.2 MiB） | 中兴/天邑专有目标文件，而仓库是**公开**的。可以共享的是描述它们的笔记，那些已入库。 |
| `staging/`（431.55 MiB） | 构建产物，最大单文件 33.5 MB。**clone 下来也跑不了大部分工具**——那些脚本假定 `staging/` 已填好。 |

`tools/` 的**工作副本留在项目根**：多份脚本开头是 `cd "$(dirname "$0")/.."`，
只在项目根运行时才能正确解析 `staging/` 和 `pon/`。
仓库里的是交付副本。**改动请两边同步**，否则它们会分叉。

---

## 2b. CI 现状（2026-10-16 晚，更新）

| run | id | head | 结论 |
|---|---|---|---|
| **33 SK-D840N Build** | 38862392504 | `63ff208` | `initramfs` **绿**（run 27 之后第一次）；`flash` 在 Compile |
| 32 SK-D840N Build | 38860167205 | `5e773e6` | FAILURE —— 1h14m47s，无 flash 标签 |
| 31 SK-D840N Build | — | `959cae4` | FAILURE |
| 30 SK-D840N Build | — | `2198240` | FAILURE |
| 29 SK-D840N Build | — | `6844c8c` | FAILURE |
| 28 SK-D840N Build | — | `2a26e24` | FAILURE |
| **27 SK-D840N Build** | 38834264406 | `f877861` | SUCCESS（`5f49fe5`…→tag `skd840n_flash_27`） |
| **25 SK-D840N Build** | 38722671878 | `27bc7a3` | SUCCESS（75 分钟） |
| **26 SK-D840N Build** | 38741408817 | `3478d5c` | SUCCESS（1 小时 17 分） |
| 24 SK-D840N Build | 38711101776 | `9b8a7e2` | SUCCESS |
| **23 SK-D840N Build** | 38699492510 | `9d768ef` | **FAILURE** |
| **22 SK-D840N Build** | 38697796513 | `7280b6e` | **FAILURE** |
| 5 SR1010 Build | — | `27bc7a3` | FAILURE（与本板无关，另一条 workflow） |

**22/23 是 rebase 期间那批中间提交，失败属预期内的开发噪音**，不是回归。

`run 25` 里三条门第一次全部拿到真实证据：`Apply SK-D840N overlays`
（DTS 内容门）success、PHY 门 success、**驱动门 success——它此前的全部历史里一次都没执行过**。

`run 26` 由 `3478d5c` 的 push 触发。该提交只改了 release body 文案与对应测试，
**门步骤与 run 25 相同**——所以这是**同样的门第二次真的跑了，还是全过**。

### run 28–32 五连红的注解（第一次读到原文）

日志仍然读不到（见末尾「三条新坑」），但 **annotations 不用登录就读得到**。run 32
`initramfs` job 的四条，全文见 `docs/07` 补五十五。两条 error：

```
error   Assert the kernel really has the PHY driver   Process completed with exit code 1.
error   Assert the kernel really has the PHY driver   CONFIG_DETECT_HUNG_TASK is not built into the kernel
```

第二条正是 `5e773e6` 加的那条 `::error`——**诊断在生产里生效了，它点了违规符号的名**。
第四条 warning `Upload outputs: No files were found with the provided path: flash-uImage …`
解释了 run 32 为什么没有 flash 标签：构建没走到产出那一步。

`63ff208` 把 `DETECT_HUNG_TASK` 移出 required 之后，run 33 的 `initramfs` 就绿了。
**门仍然是 required 的那 9 个符号在把关**，`DETECT_HUNG_TASK` 变成无论在不在都发布状态。

> **`COMPLETED` 不等于成功。** 结论在 `state.conclusion` 里，值是带前缀的枚举
> （`WORKFLOW_RUN_CONCLUSION_SUCCESS`）。我第一版结论脚本拿它和裸的 `SUCCESS` 比，
> 于是在正确答案上方两行的地方报了 UNKNOWN。读 run 状态时别把两者混为一谈。


### 一处曾经让「step 绿」失去意义的洞（`f877861` 已修）

DTS 内容门整段包在 `if [ -f /tmp/zx279133-sk-d840n.dts.dtb ]; then` 里，**没有 else**。
blob 不存在时，六项断言（ethernet 节点、mdio 总线、两处驱动绑定、
以及决定 mtdblock5 的分区顺序）**全部跳过，step 仍 `exit 0`**。

要害不是「少查了几项」，而是**跳过和通过产生的证据一模一样**：
此前对 run 25/26 唯一能说的只是「step 绿了」，而那正是跳过也会给出的东西。
（两次确实真过了门——blob 在场——但工作流里没有任何东西保证这一点。）

blob 缺失只在 harness 连任何板级文件都编译不出来时发生，因为对照组 sr1010
走同一个 harness。原第 182 行已经认出这种情况并发 `::warning::... skipping gate`：
**作为诊断是对的，作为结果是错的**。环境故障不构成给一道从未运行的门发合格证的许可。
现已改为致命，与它上面那个主 compatible 检查一致。

`staging/trial/` 现在装的是 run 25 的 flash + trial 两半，
`tools/check-imagelineage.sh` 三层全绿、`tools/preflight.sh` 离线部分全过
（第 5 步要板子，停在 `ssh: not found`，这是对的）。

**这个 flash set 按工具自己的标准已经可以刷。**

**run 27（id `38834264406`，head `f877861`）= SUCCESS，1h13m57s。**
这是上面那道致命前置守卫的**第一次真实执行**。
关键不是「又绿了」，而是 **step 绿现在等价于门真的跑了**：
blob 缺失不再能整段跳过而 step 仍报成功。守卫没有误触发，也没有把构建弄坏。

（run 22、23 是 FAILURE，是 rebase 期间的中间提交，属开发噪音，不是回归。）

### 查 CI 状态：四条路，三条是死的

1. **`api.github.com` — 不可用。** 403。配额不是本机用完的：本机在共享 NAT 出口
   `36.103.160.13` 后面，60 次/小时的未认证配额属于这个地址背后的**所有人**。
   重试无意义。
2. **commit 页面 — 不成立。** `build-sk-d840n` 出现 11 次，但那是工作流**文件路径**；
   显示名出现 0 次；4 个 `success` 无法归属到该 SHA 的任何检查。
3. **徽章 SVG — 能用，但只答「最新一个**已完成** run 的状态」。**
   新 run 在跑时它继续报旧的绿色 run。**一个 "passing" 徽章在这里不构成任何证据。**
   （`?event=push` / `?event=workflow_dispatch` 可用来判断是否由 push 触发；
   main 上 `workflow_dispatch` 徽章是 `no status`，说明全部是 push 触发。）
4. **Actions 页面的内嵌 JSON — 这条通了。**
   `https://github.com/<owner>/<repo>/actions/workflows/build-sk-d840n.yml`，
   取 `<script type="application/json" data-target="react-app.embeddedData">` 里的
   `payload.workflowRunsRoute.workflowRuns`。

   **键名是 camelCase**：`runNumber`、`id`、`runUrl`、`createdAt.timestamp`、
   `state.status`（形如 `WORKFLOW_RUN_STATUS_IN_PROGRESS`）、`timing.text`、
   `description.linkText` / `description.action`。
   按 snake_case 找 `head_sha` / `run_number` 会一无所获——**我在这上面错了两次。**

现成脚本：`staging/diag-20261014/round38-runlist.py`（已验证：它读出的
run 25 = 38722671878 与本文件记载的锚点一致）。

抓取用 **curl 不用 urllib**：本机上 curl 每次都拿到页面，urllib 两次都死在
传输途中（`IncompleteRead` / `RemoteDisconnected`）。脚本内已加重试。

### 2026-10-16 再补：读**失败原因**的路，全都死了

上面第 4 条只答「绿还是红」。红在哪一步，另找过五条路：

| 路 | 结果 |
|---|---|
| run 详情页 `/actions/runs/<id>` | 200、208 KB，但里面 5 个 `react-partial.embeddedData` 块**全是 UI 脚手架**（最大 7 KB 的 client-env），**零个 step / output / annotation 字段** |
| 同上，用 `-C`/`api.github.com` 取 job | 403（共享 NAT 配额） |
| `/actions/runs/<id>/logs` | **404** |
| commit 的 checks 页 `/commit/<sha>/checks`（**短 sha 会 404，要完整 sha**） | 200、211 KB，5 个 JSON 块**和 run 页一模一样**，同样没有 check-run 的 `output.title` / `summary` / `annotations` |
| 页面里裸搜 `annotation` / `summary` / `conclusion` 字样 | **有**（39 / 10 / 25 次），但都在 React 的 hydration 骨架里，不是数据 |

**别再在这上面花时间。** GitHub 的这些页面一律把数据留给前端去 API 取，
而 API 对我们 403。所以「四连红停在哪一步」这件事，
**只有能读 CI 日志或 API 的人能给**，本会话给不出来，也不该猜。

替代办法已经用上了：把失败时的信息**由 CI 自己打出来**
（见 `补五十二`——那道门现在会打印出错符号在合并 `.config` 里的真实状态，
并用 `::error::` 抛注解）。受益者是那位能读日志的人。

### 2026-10-16 追加：WSL 里连不上 github.com 了（不是 CI 出问题）

查 run 27 时 `round40-newrun.py` 连续五次 `http=000`、页面 0 字节。
**这不是 run 失败，是取数通路断了**——`UNKNOWN` 和 `FAILURE` 是两件事，
不能因为取不到就把结论写成失败。

分开测的结果：

| 目的地 | WSL | Windows |
|---|---|---|
| `github.com:443` | `Connection refused`（21s） | **200，572588 字节** |
| `api.ipify.org:443` | `SSL_ERROR_SYSCALL` | — |
| `example.com:443` | **200** | — |

DNS 正常（解析出 `20.205.243.166`），通用 HTTPS 正常，**只有 github.com 被拒**。
所以现在的工作方式是：**Windows `curl.exe` 取页 → 落盘 → WSL `python3` 解析**。
`staging/diag-20261014/round49-parsefile.py` 就是这个分工，并且带自校准
（把已知为真的 run 26 / id `38741408817` 当锚点，锚点不在列表里就不给结论）。

---

## 3. 已验证 / 未验证

**这一节是交接的重点。请不要把左栏当成右栏。**

### 已经有真实构建证据的

| 结论 | 证据 |
|---|---|
| DTS 内容门能过 | CI run 25 `Apply SK-D840N overlays` = **completed success**（那道门就在这个 step 里） |
| **PHY 门能过** | CI run 25 = **success**（它读合并后的 `.config`，本机没有，只能由 CI 回答——run 25 回答了） |
| **驱动门能过** | CI run 25 = **success**，25 个节点全 ok（此前全部历史里一次都没执行过） |
| 门禁脚本是合法 shell | 5 个 workflow 的 **39 个 `run:` 块**全部 `bash -n` 通过 |
| overlay 能落到内核树 | `Apply SK-D840N overlays` success；本地 `cp -a` 模拟 10516 文件落地 |
| **镜像里装着 dropbear** | run 25 的 `flash-rootfs.jffs2` 解包（jefferson）：`usr/sbin/dropbear` 234,603 B / mode 755 |
| **`/etc/dropbear` 已经是 755 root:root** | 同一次解包实测。补五十唯一那个残留项已关：dropbear 只要求这个目录 root 属主且 group/other 无写位 |
| **镜像里的 `etc/init.d/dropbear` 就是 immortalwrt 那份** | md5 `29f5756b12ef3df641a4a8fc293f9df4`，与源码树**逐字节一致**；所以它开机 `chmod 0600` authorized_keys 的行为就是板子上的行为 |
| **root 的公钥登录不会被禁掉** | `/etc/config/dropbear` 里没有 `RootLogin` 项，init 的 uci 默认是 `RootLogin:bool:1` → 不追加 `-w` |
| dropbear 会开机自启 | `etc/rc.d/S19dropbear` 存在 |

### 离线已验证、但只跑在「上一次发布的产物」上

| 结论 | 怎么验的 | 边界 |
|---|---|---|
| 驱动门会通过 | 用门禁自己的扫描 awk + 判定块（含 `exit "$rc"`）+ 真实 dtb + 真实内核字符串跑，**25 个节点全 ok，退出码 0** | 内核镜像是上次发布那份；`has_driver` 依赖字符串表布局 |
| DTS 内容门会通过 | 同一套办法，五项 `need` 全过，分区顺序 `MATCH` | 同上 |
| 三个非 syscon fallback 都有驱动 | `fixed-layout` / `fixed-partitions` / `arm,primecell`，整行命中 | — |

这些预判（补三十三）**已被 run 25 逐条证实**。

### 2026-10-16 补：换行（CRLF）这件事，现在有结论了

之前 `decompiled/` 的 CRLF 被记成「今天没坏，blob 是对的，故意不处理」。
本轮重新测了，**当时的判断只对了一半**，而且理由是错的。已修：提交 `75651fb` + `0778093`。

**当时的错在哪**：`.gitattributes` 里那句注释写「宽规则加 renormalize 会重写已提交的
`decompiled/*.fdtdump.txt` blob，那是另一个问题」。实测**这句是错的**——
那四个 blob 本来就是 LF，任何规则都不会重写它们。基于一个错误前提把问题挂起了。

**现在的事实**（全部实测，不是推论）：

| 路径 | 工作树 CR | blob CR | lone CR | NUL | 处置 |
|---|---|---|---|---|---|
| `decompiled/` ×4 | 2373 / 2115 / 2433 / 2163 | **0** | 0 | 0 | `text eol=lf` |
| `.gitattributes` | 71 | **0** | — | 0 | `text eol=lf` |
| `docs/03-*.md` | 662 | **0** | 0 | 0 | 已被 `docs/**` 覆盖 |
| `pon/*.txt` ×5 | 468 / 58 / 41 / 67 / 59 | **0** | 0 | 0 | 已被 `pon/**` 覆盖 |
| `overlays/target/linux/zte/{,image/}Makefile` | 31 / 63 | **0** | 0 | 0 | 新增 `overlays/**` |
| `trial-files/**` ×4（含 `S15trial`、`05_trial_alive`、`trial-blink`、`dropbear`） | 79 / 11 / 171 / 10 | **0** | 0 | 0 | 新增 `trial-files/**` |
| `.gitignore` | 20 | **0** | — | 0 | 新增 |
| `dtb/*.dtb` ×2 | 86 | **86** | 86 | **25668** | 钉 `-text`，**不动** |

**为什么 `trial-files/**` 这条最要紧**：`tools/mktrialram.py` 就是把
`ci-build-repo/trial-files` 打进设备 initramfs 的。那三个是 shell 脚本，
CR 在那里不是空白，是**命令的一部分**。和 `02_network` 的续行、`overlays/**/etc/rc.d/*`
是同一个失败模式，只是从一扇没人查过的门进来。

**两个 DTB 没有 bug**：blob 里有 86 个 CR 和两万多 NUL 字节，clone 出来也是 86、
**与 blob 逐字节一致**——autocrlf 没有损坏它们。`-text` 是上锁，不是修。

**全仓库只有 2 个二进制**（NUL 扫描 128 个跟踪文件的结果），
所以 `overlays/**` 和 `trial-files/**` 才敢按目录写而不是逐个文件写。

**验收**：在 `0778093` 上做全新 Windows clone（`core.autocrlf=true`），
**只剩 2 个文件带 CR，就是那两个 DTB，且都与 blob 逐字节一致**。
所有文本路径（`decompiled/*.dts`、`S15trial`、`trial-blink`、`docs/07`、`.gitattributes`）CR 全 0。

**历史没被动过**：两次 `git add --renormalize .` 的暂存结果都只有 `.gitattributes`
一个文件；四个 `decompiled` blob 的 object id 与修改前完全一致（漂移 0）。

### 如果你以后改了 `.gitattributes`，本地工作树不会自动跟上

这条单独拎出来，因为**很容易再踩一次**：

* 加规则**不会**回头改已经 checkout 的文件。
* `git checkout-index -f` **无效**（实测，CR 一个没少）。
* `git checkout-index -f -u` 在**大部分情况下也无效**——我一度以为 `-u` 就是开关，
  那是伪相关：当时的实验脚本自己重写过文件字节，stat 早就失配了。
* **真正起作用的是让索引 stat 与磁盘对不上**，然后再 `-f -u`：

```powershell
# 先碰一下文件（改 mtime），让 stat 失配
[System.IO.File]::SetLastWriteTime($path, (Get-Date))
git checkout-index -f -u -- $path     # 这时才真正跑换行转换
```

判断一个方法有没有用，**必须先造出缺陷再验**。我做过两次废实验：
一次是 clone 出来本来就是 LF（缺陷不存在，四个方法全「通过」），
一次是 `git checkout 8e2507b` 而新旧提交 blob 相同，git 认为「文件没变」压根没重写。
这两次都差点让我把巧合写成结论。

### 接线前必须知道的四件事（2026-10-16 实测，都会当场炸）


**1. 暂存的 `staging/trial/` 已经不可刷，必须重拉。**

镜像里新增了 `etc/dropbear/authorized_keys`（见下一条），所以现产物是旧的。
`tools/check-imagelineage.sh` 现在会**自己**拒绝：

```
REFUSE in-image/ssh access: etc/dropbear/authorized_keys is not in the image at all
```

顺序：推送 → 等 CI（约 75 分钟）→ 重新拉 flash set → 重跑 lineage 门。

**2. 刷完的板子靠公钥进来，钥匙在 WSL 容器里。**

```
私钥：/root/.ssh/id_onu            （容器文件系统，不在 /mnt/c 下）
公钥：已烤进镜像 /etc/dropbear/authorized_keys
指纹：SHA256:2pb66wadj2rDLhc9Z3FCYfu9OVzpG0ley5LBON/iJPM   (ed25519)
```

公钥**随仓库走**，所以任何人 clone 下来刷出来的板子都是认这把钥匙的；
但**私钥不随仓库走**，只有这台机器有。换个机器操作就要先把私钥带过去，
或者用 `KEY=/path/to/key` 覆盖（所有工具都支持这个环境变量）。

无口令是刻意的：所有工具都带 `-o BatchMode=yes`，回答不了口令提示。

**3. 命令在 WSL 里跑，WSL 现在有 ssh 了。**

2026-10-16 之前这个容器里 `ssh/scp/sftp` 一个都没有，26 个工具全是 `not found`；
现已装 `openssh-client 1:8.4p1`。`nc` 没装也不需要——
宿主侧 `.sh` 没有一处调它，那些调用都在设备侧（initrd 的 busybox 自带）。

顺带修掉两个坑：`ow.sh` 和 `zpush.sh` 原先硬编码
`/c/Users/woodn/.ssh/id_onu`（别人机器的路径，还是 Git-Bash 拼写），
是全仓库唯一两个在提交它们的这台机器上跑不通的工具，现已统一成可覆盖写法。

**4. `postflash.sh` 的退出码现在才表示「采到证据了」。**

2026-10-16 之前它是**死代码**：采集失败时的 `if ! … | tee` 分支永远进不去
（管道退出码是 `tee` 的），脚本最后一句是喂给 `cat` 的 heredoc，
所以**在完全没采到任何证据时它也退出 0**。
一个永远打不出的警告比没有警告更糟——操作者会以为刷成功了。

现在三种情况都实测过：

| 情况 | 退出码 |
|---|---|
| 探测答了但整份采集死（rc≠0） | **1**，打出 `ssh capture FAILED (rc=N)` |
| ssh 成功但没有 `uname:` 行 | **1**，打出「capture did not run」 |
| 采到真实证据 | **0** |

所以 `sh tools/postflash.sh && ...` 现在是有意义的；
**在此之前不要相信它的 0**。

### 完全没验证的（需要板子）

* eth0 **carrier 到底有没有起来**、192.168.9.1 是否应答
* 分区编号在板上的实际形态（gate round 的目的）
* PHY 是否真的协商到 2.5G

> 2026-10-17：**「eth0 是不是那个 netdev 的名字」已经不再是未验证项**，
> 它在**出货件层面**已经确定，理由和证据见 §5g。剩下的只有 carrier/应答。

### 一条以前列错位置的（2026-10-16 更正）

这里曾经写着「真实 `dtc` 二进制从未在本机跑过（`fdt2dts.py` 是测试替身）」。
本机没跑过 dtc 是真的，但**把它列为未验证缺口是误导**——权威证据路径走的就是真 dtc：

* `build-sk-d840n.yml` 第 157 行 `dtc --version`，163/170 行用 dtc 把
  `zx279133-sk-d840n.dts` 编译成 dtb（先试 dtc 自带预处理器，再试 cpp + dtc 的内核做法）
* 214/518 行再 `dtc -I dtb -O dts` 解回来跑 DTS 内容门
* `inspect-dtb.yml` 装 dtc 产出仓库里的 `decompiled/`，提交信息里记着当时的 dtc 版本

**所以内容门判定的是真 dtc 编译出来的 blob，不是替身。**
`fdt2dts.py` 只在离线回归里当替身用（`test_dts_content_gate.sh` 会先确认那份文本
确实是 dtc 的反斜杠零形状，否则拒绝运行，免得自己测一个假拼写）。

> 由此也暴露过一个真问题，见 §2b 末尾：内容门原本包在
> `if [ -f <blob> ]; then` 里且没有 else，**blob 缺失时整段门静默跳过而 step 仍报成功**——
> 而「step 绿」正是跳过也会产生的证据。已加致命前置守卫（`f877861`）。

---

## 4. 设备最后实测状态

> 以下是**最后一次实测**的结果，不是当前状态——板子从那之后一直是离线的。
> 重新接上后**第一件事就是重测**，不要拿这张表当现状。

| 分区 | 内容 | 是本项目写的吗 |
|---|---|---|
| mtd1 boot | 厂商 U-Boot | 否 |
| mtd2 kernel | mainline 6.18.38 uImage | 是 |
| mtd3 dtb | mainline dtb | 是 |
| mtd5 root | 社区 4.19 jffs2 | **否** |

**mtd5 从头到尾没被任何工具写过。** 这是整个回滚论断的承重墙：
回滚 = 只把 mtd2/mtd3 写回厂商那一对，root 不用动，mtd5 永不触碰。

> ⚠ **2026-10-18 给这句加了个边界 —— 它只对 gate round 成立。**
> 两个工具的契约不同，别混：
>
> | 工具 | 写 mtd5(root)吗 | 「root 没被写过」 |
> |---|---|---|
> | `tools/flash-direct.sh write`（gate round） | **不写**，只写 mtd2+mtd3 | **为真** |
> | `flash-sk-d840n.sh --write`（完整刷机，跑在 kexec 后的 RAM 系统里） | **写**，而且是第一个写的 | **为假** |
>
> `tools/preflashbackup.sh` 的注释「root is deliberately not dumped: mode `write`
> never touches it」里的 `mode write` 指的也是 `flash-direct.sh`。
> `backups/` 下只有一个**空的** `live-20261012/`，**mtd5 从来没被 dump 过**。
>
> **完整刷机之后要回到刷机前状态，必须把 root 也写回去**，而那份镜像就在本机：
>
> ```
> staging/rootfs.jffs2   11,812,720 B   md5 0fd03a1115374f5335d7fc207b14a849
> ```
>
> （与 `docs/05:110` 的期望一致，本轮实测复核。别用
> `sk-d840n-openwrt/release/rootfs.jffs2`——那份 589,198 B 是**残缺的**。）
> 只回滚 mtd2+mtd3 会得到**厂商 4.19 内核 + 本项目 rootfs** 这个从未测过的组合，
> 而这台机器没有串口。
>
> `templates/flash-sk-d840n.sh` 原来在写完之后打印「root 从没被写过、
> rootfs 回滚不需要也不可能」——**两句在打印的那一刻都是假的**，已改。
> 但 `staging/trial/` 那份（run 39，sha256 `34772dd5…`）**仍是旧文案**：
> 要新文案得重跑 CI 取新 flash set。

因此当前跑的用户空间仍是社区镜像，`02_network` 里的 `192.168.9.1`
**尚未生效**——要等重新构建镜像并写 root 才生效。

---

## 5. 板子接上之后，按这个顺序

前置：CI 成功 → 取新 flash set（见 §6）。

> ⚠ **2026-10-17 撤回这一行**。这里曾经写着
> 「**`staging/trial/` 现在那份是 run 25 的产物，三层全绿，按工具标准可以刷**」——
> **那是错的**，而且错在最要紧的位置（流程第一行）。实测（round494–499、round539–541）：
>
> - `ci-build-repo/staging/trial/` 的 flash 半边是 **run 21**，
>   它的 `flash-dtb.bin` 是 **11,916 B 的 `phy-mode="2500base-x"` 旧件**
>   （工作区根那份才是 12,009 B 的 `sgmii`）；两者**不是「一份更新、一份没跟上」**。
> - 工作区根那棵是 **run 33**，不是 run 25。
> - 而 run 40/41 的 dtb 清单值都是 `088edb435e5e782a…`，即**出货件那份 sgmii dtb**。
>
> **⇒ 要刷就刷 `skd840n_flash_41_38972288244`**（run 41 的 flash 半边，
> 与 `initramfs_41` 同一个 run id `38972288244`，两处模板修复都已出货且逐字节相同）。
> **不要用 `ci-build-repo/staging/trial/` 那半边。**
> 详见「有两棵 `staging/`，别搞混」那节。
>
> **取件：`flash-sk-d840n.sh` 不在 release 的资源列表里**（round559 实测）。
> `skd840n_flash_41_38972288244` 上是 **7 个** asset：`flash-dtb.bin` /
> `flash-rootfs.jffs2` / `flash-set.tgz` / `flash-sysupgrade.bin` / `flash-uImage` /
> `sha256sums` / `sha256sums-release`——**刷写脚本不在其中**。
>
> 这是设计不是丢件：`build-sk-d840n.yml:1052-1063` 的 release `files:` 列表里本来就没有它，
> 它随 `flash-set.tgz` 发布（tarball 内 6 个成员，含该脚本与覆盖全部 5 项的 `sha256sums-release`）。
> **拿件只有两条正路**：`sh tools/gettrial.sh flash 41`
> （`gettrial.sh:108` 先 `tar xzf` 解到暂存区、按清单校验、全过才落位），
> 或自己 `tar xzf flash-set.tgz`。
>
> ⚠ **别拿 release 上单独那份 `sha256sums-release` 去核一个手工拼的目录**：
> 它点名 5 个文件，而 `flash-sk-d840n.sh` 不是独立 asset，`sha256sum -c` 会报
> `flash-sk-d840n.sh: FAILED open or read`。**那不是文件损坏**——
> 清单的作用域是 tarball 的内容。这条在本机实测复现过（round559）。

```sh
cd /mnt/c/ugit/SK-D840N4        # WSL；或在 Git Bash 里用 Windows 路径

# 0. 先把网段对上。
#    刷之前板子答 192.168.9.1；**刷完本项目的镜像后也答 192.168.9.1**，
#    没有任何接口带 192.168.9.2——那是旧社区镜像的拓扑（lan = br-lan over
#    eth1..eth3，mainline 无驱动；eth0 = wan @ .2）。
#
# 0b. ⚠ **这台机器没有 WAN/uplink 网口。** 前面板是 **4 个 LAN 口 + 光纤 PON**。
#     厂商镜像把 eth0 叫 `wan` 只是抄了路由器默认值——它本身就是四个 LAN 口之一，
#     厂商驱动日志说它是 **port 4**（`[eth0] port 4 link up`）。
#     **主线只驱动这一个口**；另外三个 LAN 口挂在 SoC 内部 "9132" 交换机后面，
#     mainline 没有驱动。**所以线插在那三个口上永远不会有任何反应。**
#     ⚠ 厂商镜像里「不知道该插哪个就**挪一根线看 dmesg**：`port N link up`」是
#     **厂商专用**的办法（那个字符串主线不打印）。**主线看板子的 carrier 用 0c 那三行。**
#
#    192.168.9.1 由 **br-lan over eth0** 承载（推导见 §5b；名字的确定性见 §5g）。
#    ⚠ 2026-10-17 撤回一条更正的更正：这里曾被「更正」成「单数版 ucidef_set_interface
#      → 不建桥」。**那次更正是错的**，原文本（br-lan over eth0）才是对的。
#      错在只读了 `02_network` 就去推 `config_generate` 的输出，没读它的函数体；
#      §5b 里那条 `[ "$1" = "lan" -a -z "$ports" ] && ports="$device"`
#      恰恰在「无 ports」时**强制建桥**。单数/复数不是判据。
#    ⚠ 由此有一处很容易自己吓自己的现象：`ip addr show eth0` 是**空的**，
#      地址在 `ip -br addr show br-lan` 上。eth0 上没有地址 ≠ 网卡没起来。
#      （RAM 启动那条路不一样：S15trial 把地址直接打在每个 netdev 上，不经 br-lan。）
#    ⚠ 刷机路径在「netdev 叫什么」这件事上**没有 fallback**：
#      02_network 点名 eth0，config_generate 把桥的唯一端口也钉成 eth0。
#      带「每个 netdev 都拿地址」这个兜底的 S15trial 只存在于 RAM 启动用的
#      initramfs，**不在发布镜像里**。若主线把这个口起成别的名字，
#      RAM 启动仍 ping 得通、刷机后 br-lan 会因为没有真实端口而起不来。
#    （注：名字这一条现已由 §5g 在产物层面确定，不必再当作风险；但那个「没有
#      fallback」的结构事实仍在——它只在名字真的变了时才咬人。）
#    ssh 可能仍然不通：dropbear 被绑在 'lan' 接口上，要靠 reachcheck 解开。
sh tools/backwatch.sh           # 看网卡有没有 carrier（**PC 侧**：网卡状态 + arp + ssh）

# 0c. 板子侧的 carrier —— 比 grep dmesg 稳，round524–530 核实过
cat /sys/class/net/eth0/carrier     # 期望 1
cat /sys/class/net/eth0/operstate   # 期望 up
cat /sys/class/net/eth0/speed       # duplex 同理
#   ⚠ 这三个是 net/core/net-sysfs.c 里的 netdev 无条件属性
#     （carrier_show 在 :307，注册为 DEVICE_ATTR_**RW**(carrier)，所以 grep
#      DEVICE_ATTR_RO 找不到；operstate_show 在 :436）。
#   ⚠ **接口没起时 `carrier` 返回 -EINVAL，不是 0**：
#     `cat` 读出的是一次读错误，**别把「读不出来」当成「没链路」**。
#     拿 `operstate` 交叉判断：非 running 时它会打印 `down`。
#   ⚠ 文档里那句「挪一根线看 dmesg: `port N link up`」是**厂商镜像专用**的
#     （主线不打印那个字符串）。主线就认上面这三行。

# 1. 刷完先看它活着没有。
#    注意：**不要传 HOST=**。传了就把候选锁死成那一个地址，
#    而刷完后 .2 不存在，会把一台健康的板子报成死的。留空让它按 .1 → .2 → .1.1 探测。
sh tools/postflash.sh

# 2. eth0 到不到 192.168.9.1（这是本次改动最核心的一条）
#    reachcheck 会先把 dropbear 的接口绑定解开
APPLY=1 sh tools/reachcheck.sh

# 3. 刷之前的最后一道人工闸
SKIP_BOARD=1 sh tools/preflight.sh

# 2b. 第一次 ssh 成功之后，确认公钥是被**读到**的，而不是碰巧别的路通了。
#     这是从 dropbear 2025.89 源码推出的唯一硬条件（补五十）：
#     **/etc/dropbear 这个目录**必须 root 属主、group/other 不可写。
#     文件本身不用管——/etc/init.d/dropbear 开机会自己 chmod 0600。
#     **这一步只能在板上做**：jffs2 提取出来的权限读不出原权限
#     （解包目标是 wslfs，不保留 mode；jefferson 日志也只有 `writing S_ISDIR path`
#     没有 mode 列）。别试图从 PC 上解镜像得出这个结论。
#     在板子上：
#         stat -c '%U %a' /etc/dropbear
#     必须是 root，且不是 775/777 一类。真出问题的话 dropbear 日志里只有一行：
#         /etc/dropbear must be owned by user or root, and not writable by group or others
#     而现象看上去只是「key 被拒」，很容易误判成公钥不对。

# 4. gate round：只写 mtd2 + mtd3，mtd5 不碰
MASK=1 FLASH=0 WAIT=10 sh tools/runround.sh
```

**`flash-direct.sh` 在代码层面就保证了这一点**：它只暂存 `flash-uImage` 与
`flash-dtb.bin` 两个文件，回读也只认 **2 条 READ_OK**；脚本第 16 行写明
→「改写 `root` 必须走 RAM root，因为 root 挂在 / 上」。
写入顺序是 **dtb 先、kernel 最后**——半途而废会留下一个还能启动的旧组合，
而不是「主线 kernel + 厂商 dtb」那个起不来的组合。

回滚：

```sh
SRC=staging/rollback EXPECT='root=0xf00000,0xf100000' sh tools/flash-direct.sh write
```

**注意 `preflight.sh` 的 `RUN` 默认值指向一次仍然成功的旧构建**（run 21）。
这一点已经处理过了：现在 `RUN` 未显式指定、而暂存集已通过自己的门时，
**它不会去拉取**（`RUN_EXPLICIT` 守卫）。
但**第 4d `check-imagelineage.sh` 仍然是权威，不要因为「第 1 步已经查过」就把 4d 去掉。**

---

## 5a. 走 RAM 启动（trial），不刷机（2026-10-16 补：§5 里原本没有这一步）

§5 讲的是刷机。但**主线能不能跑通这件事，用 RAM 启动就能验，而且不写 flash**——
`phy-mode` 那一行改动就是为 RAM 启动准备的。这一节是现场的操作序列。

```sh
cd /mnt/c/ugit/SK-D840N4

# trial 集与 flash 集是**两个 release**，trial 集来自 initramfs job。
# 要跟 flash 半边出自同一次构建，就用同一个 run 号（本轮是 41）：
sh tools/gettrial.sh initramfs 41         # 或 flash 41

# 复制到板上。注意 trial-kx.tgz（kexec 本体）必须一起过去，
# 那块镜像没有 tftp 客户端，scp 必须用 -O（老协议）。
scp -O -i ~/.ssh/id_onu staging/trial/* root@192.168.9.1:/tmp/trial/

ssh -i ~/.ssh/id_onu root@192.168.9.1 sh /tmp/trial/trial-kexec.sh
```

脚本自己会做的事：**从 `sha256sums-trial` 里排除两条再校验**——
`trial-dtb.bin`（可能被本地打补丁）与 **`flash-sk-d840n.sh`**（它属于 flash 半边，
这份清单是 initramfs job 写的；脚本从不运行它，而刷写脚本在擦任何东西之前
自己会校验 `sha256sums-release`，所以排除它不丢完整性），
并**正向要求清单必须覆盖 `trial-Image.gz`**，否则明确拒绝；
然后 `gunzip trial-Image.gz > Image`、带上 `trial-initramfs.cpio.gz` 作 `--initrd`，

> ⚠ **2026-10-17 更正**：这里原来写的是「校验 `sha256sums-trial`（**dtb 除外**）」——
> 那是**旧出货件**的行为。旧件只排除 dtb 一条，于是「只带够 kexec 所需文件的
> 那一次拷贝」会因为一个用不上的文件缺失而被拒，而 `sha256sum -c` 对缺文件
> 报 `FAILED open or read`，那句 "checksum mismatch" **把原因指向错误的方向**。
> 修复已随 run 41 出货（`trial-kexec.sh` 8,294 B / `b322856dc67d…`，
> 与 `templates/` 那份逐字节相同），第 133 行现在是
> `grep -v -e 'trial-dtb.bin' -e 'flash-sk-d840n.sh' sha256sums-trial`。
> **手上若还是 run 39/40 的那份（7,675 B / `91478d93…`），它照常会执行旧逻辑。**

```
kexec --load --mem-min=0x88000000 --mem-max=0x9b000000 \
      Image --dtb=trial-dtb.bin --initrd=... \
      --command-line="root=/dev/ram0 rw console=ttyAMA0,115200 trialtag=<UTC时间戳>"
```

**`--dtb=trial-dtb.bin` 就是让 `phy-mode="sgmii"` 生效的那一步。** 板上那个
kexec 是 aarch64 静态构建（Alpine 的 kexec-tools 2.0.31），已确认它认识
`--dtb`（选项在它公开的 33 个长选项里）——不认的话会静默退回内核自带的 dtb，
这一行改动就等于没做，而现象是「一切正常但一个帧都不过」。

### 有两棵 `staging/`，别搞混（2026-10-16 实测）

| 谁读 | 读哪一棵 |
|---|---|
| RAM 启动（§5a，`staging/trial/*`） | `/mnt/c/ugit/SK-D840N4/staging/trial` |
| `check-imagelineage.sh` 无参数调用 | 工作区根那棵（`ROOT=${1:-$WS/staging/trial/flash-rootfs.jffs2}`，`WS=dirname(REPO)`） |
| `preflight.sh` → `mkgate.sh`（gate round 的载荷） | `/mnt/c/ugit/SK-D840N4/ci-build-repo/staging/trial` |

后两者读的是**不同副本**。

> ⚠ **2026-10-18：上面那句「今天两份逐字节相同」现在不成立了。**
> 实测（round494–497）两棵树已经**分叉**，而且不是「一份更新、一份没跟上」，
> 是**仓库那棵的 flash 半边被换成了一次很旧的 release**：

| 文件 | `ci-build-repo/staging/trial` | 工作区根 `staging/trial` |
|---|---|---|
| `*.source` 记的 run | flash = **`skd840n_flash_21_38243906799`** / trial = run 39 | **两半都是 run 33** |
| `flash-uImage` | 8,105,331 B | 8,142,017 B |
| `flash-dtb.bin` | 11,916 B | 12,009 B |
| `flash-sk-d840n.sh` | 3,648 B | 5,378 B |
| `sha256sums-release` | 331 B | 415 B |
| `flash-set.tgz` | 24,174,085 B | 24,255,581 B |

**谁干的**：`staging/ciwatch.sh` 是**钉死在某一次 run 上的轮询器**——
`RUN=38243906799`（run **21**），循环里 `sh tools/gettrial.sh flash "$RUN"`，
而 `gettrial.sh` 写的正是**相对 CWD 的 `staging/trial/`**。它 2026-10-17 05:10
把仓库那棵的 flash 半边刷成了 run 21（trial 半边没动，还是 run 39）。
该脚本**现在没有在跑**，所以那是一次性后果，但**只要有人再起它，同样的事会再发生**。

> 这条否定**不是**靠 `ps -eo` 得来的：每次 `wsl -e` 都起一个临时实例，实例里
> 只能看见自己的 2 个 `init`，**看不见 Windows 侧的进程**，用它下「没在跑」是推论。
> 权威判定在 Windows 侧（round499）：
> `Get-CimInstance Win32_Process` 共 167 个进程，其中 `Name` 匹配
> `^(wsl|wslhost|bash|sh)\.exe$` 的有 **0** 个，命令行含 `ciwatch` / `gettrial` 的
> 也有 **0** 个。`Name` 字段在 `CommandLine` 为 null 时仍可读（那 10 个 null 的不算盲区），
> 而 `ciwatch.sh` 要跑就必须有 `wsl.exe` 或 `bash.exe` —— 所以这半边是**实测**的。
> 仪器阳性对照：`Get-Process` 对 `explorer|conhost|powershell` 命中 6 个，
> `Win32_Process` 能列全 167 个；空结果不是因为过滤器坏了。

**由此得到一条操作规则（比这次的具体分叉更重要）**：

> **`staging/` 是 gitignored 的本地树（`.gitignore:9`：「Building that directory is
> the reader's first job, not the clone's」），它不是一个稳定输入。**
> **动它之前先读 `staging/trial/*.source`** —— 那是唯一记着「这一份是哪次 release」
> 的文件，别的都不记。半边换过一次、另一半没换的**混搭状态是可能的**，
> 而且从文件尺寸上未必看得出来。

**结论：接受现状，不要动它。** 上一轮这个混搭被我挂成待裁决项，现已用证据关闭：

1. 混搭是 `gettrial.sh` 双模式同目录（`D=staging/trial`，`initramfs`/`flash` 各取
   一个 tgz）+ 三个脚本共同钉 `RUN=38243906799` 取那张别名钉死的分区表，
   **是有意**的，不是谁覆盖错了；
2. 它**不出货**（`git ls-files staging` 为空、`git status --porcelain staging` 为空）；
3. `mkgate.sh` 要的正是 run 21 那份 dtb 里的分区表（`dtbparts.py … root=0xf00000,0xf000000`），
   **换掉它反而会破坏 gate round**；
4. 本机要自洽的 flash 集，**工作区根那棵（run 33，9/9）已经在**，不必重拉 run 39。

要改的只有上面那条**人的操作规则**。

**这次分叉已经咬出两个可测的后果（round498–499）**

1. **仓库那棵的 `flash-dtb.bin` 是 sgmii 修复**前**的那份**，不是「旧一点但等价」：

   | 文件 | 字节 | `phy-mode` 字符串实测 |
   |---|---|---|
   | `ci-build-repo/staging/trial/flash-dtb.bin` | **11,916** B（md5 `a7dcd188b395`） | offset 8340 = `2500base-x`，`sgmii` **0** 次 |
   | 工作区根 `staging/trial/flash-dtb.bin` | 12,009 B（md5 `cfbfe187a6a5`） | offset 8340 = **`sgmii`**，`2500base-x` 0 次 |
   | `ci-build-repo/staging/trial/trial-dtb.bin` | 12,009 B（md5 `cfbfe187a6a5`） | 同上 |

   字节级扫描，仪器阳性对照 `cpus` 在三个文件里各命中 1 次 ⇒ 0 命中是真的 0。

   > ⚠ **这一条会骗过 §5 那个三秒判据。** 刷了这个 dtb 的板子，
   > `$P/driver` **照样是 realtek**（驱动认得这颗 PHY，
   > §5 的「出现 genphy 就停」那道门**不会响**）。而且按 §5 的推导，
   > `phy-mode="2500base-x"` 会把 `RTL822X_VND1_SERDES_OPTION (0x697a)` 的
   > `GENMASK(5,0)` 写成 **2**（`sgmii` 才是 **0**），
   > **但那个寄存器用户空间读不到**（见 §5 的更正），所以现场只能从 `phy-mode` 判：
   >
   > ```sh
   > # 期望 sgmii
   > tr -d '\0' < /proc/device-tree/soc/ethernet@19000000/phy-mode; echo
   > cat $P/phy_interface
   > ```
   > **两条都该出 `sgmii`。** 出 `2500base-x` ⇒ 你刷的是 sgmii 修复前的 dtb，
   > **不是驱动或硬件坏了**：停手，从**工作区根**那棵树重新 stage，别去查驱动。
   > 第一条读的是 DT 原值（路径由 `zx279133.dtsi:359` 的
   > `mdio1: mdio@14f02000` 与 dts 里的 `ethernet-phy@5` 推出来），
   > 第二条读的是驱动吃进去之后的 `phydev->interface`。

2. **混搭状态在 `sha256sum -c` 上是可见的**，不是只有尺寸能看出来：

   ```
   # ci-build-repo/staging/trial  (flash=run21 / trial=run39)
   $ sha256sum -c sha256sums-trial
   trial-Image.gz: OK    trial-dtb.bin: OK    trial-kexec.sh: OK
   flash-sk-d840n.sh: FAILED          <-- run 39 的清单撞上 run 21 的文件(3,648 B, 10-12 20:52)
   $ sha256sum -c sha256sums-release   # 4/4 OK  <- flash 半边自洽, 只是旧
   # 工作区根 staging/trial  (两半都是 run 33)
   $ sha256sum -c sha256sums-release   # 5/5 OK
   $ sha256sum -c sha256sums-trial     # 4/4 OK
   ```

   两个半边**各自**都自洽（run 21 的 4/4、run 39 的 3/3），坏的只有**跨半边**那条边。
   这也说明 `sha256sums-trial` 里**列着 `flash-sk-d840n.sh`**，而它属于 flash 半边 ——
   正是 §「免串口流程」里 `trial-kexec.sh` 把该文件从 `sha256sum -c` 排除掉的那一条。
   **代价要说清楚**：排除之后，`trial-kexec.sh` 也**看不见**这棵树被混搭过
   （scenario B 实测「缺用不上的文件」rc=0；文件在但版本旧，同样 rc=0）。
   混搭只能由 stage 的人挡住，`trial-kexec.sh` 挡不住。

3. **这次分叉不会随 git 出货。** `staging/` 被 `.gitignore:9` 忽略，
   `git ls-files staging` 为空、`git status --porcelain staging` 为空
   ⇒ 仓库里没有、也不会有这份 dtb，克隆者拿到的 `staging/` 是空的
   （`.gitignore:8` 原话：Building that directory is the reader's first job）。
   所以这纯粹是**本机的现场混淆风险**，不是出货缺陷。

顺带一条仍然成立的好消息：run 33 与 run 39 出的是**同一份 dtb**
（`flash-dtb.bin` 都是 12,009 B / sha256 `088edb435e5e782a…`），
所以 §5g 关于 `eth0` / `sgmii` / `br-lan` 的结论**不受这次分叉影响**。

**「两份相同」这件事本身也曾是假象**：只往 `ci-build-repo/staging/`
重新拉过 flash 集（`preflight` 就是这么做的），而 lineage 门默认看的
工作区根那份没跟着动，那道门依旧会绿。

**这条已经修了（2026-10-16）**：`preflight.sh` 三处调门现在都显式传
`staging/trial/flash-rootfs.jffs2`，第 2 层的 `SETDIR` 也跟着从 `dirname(ROOT)` 走到
repo 那棵。手动跑门时仍然要显式指明：

```sh
sh tools/check-imagelineage.sh staging/trial/flash-rootfs.jffs2   # 从仓库根跑
```

注意 `staging/ctl/`、`staging/rollback/` **只在工作区根**有，仓库那份没有；
所以测试仍需 `cd` 到工作区根，但**被测的 `tools/` 必须指 `ci-build-repo/tools/`**——
工作区根那份 `tools/` 是化石，见 docs/07 补二十六。

### 回滚那两个工具要传 `LOCAL=`（2026-10-16 实测）

`tools/mkrollback.sh` 和 `tools/checkrollback.sh` 开头都是 `cd "$(dirname "$0")/.."`,
然后按**仓库相对路径**去找板子派生的东西：`staging/ctl/mtd2.bin`、`staging/ctl/mtd3.bin`、
`backups/preflash-*/`、`restore_set/`。这些是从板子上 dump 下来的，clone 里没有；
本机仓库又嵌在工作区里，于是它们全在**上一层**，工具按仓库相对路径找就是找了个空。

现象不报错，是**静默降级**：`checkrollback.sh` 第 6 节报「没有 pre-flash 快照」算 FAIL，
第 7 节根本跑不到，读起来像「回滚对不可用」，而回滚对其实好好的。
`mkrollback.sh` 更直接，`FileNotFoundError` 退出。

两个工具现在都有 `LOCAL=${LOCAL:-.}`（默认就是原来的行为，单 clone 布局不受影响）：

```sh
LOCAL=/mnt/c/ugit/SK-D840N4 OUT=/mnt/c/ugit/SK-D840N4/staging/rollback \
  sh ci-build-repo/tools/mkrollback.sh
LOCAL=/mnt/c/ugit/SK-D840N4 sh ci-build-repo/tools/checkrollback.sh
```

**不传 `LOCAL` 就是找不到板子数据**，实测 `FileNotFoundError: './staging/ctl/mtd2.bin'`。

### 出货的 `preflight.sh` 在这台机器上停在**第 4b 步**（2026-10-16 实测）

`SKIP_BOARD=1 sh tools/preflight.sh`（从仓库根跑）实测 `rc=1`，最后一行是：

```
SIM OK - every guard fired when it had to and nothing was erased when it must not be
need staging/rollback/flash-uImage (sh tools/gettrial.sh flash <run>)
```

### 让 `preflight.sh` 在这台机器上跑完整个离线段（2026-10-16 实测，已解决）

先说清楚**问题是什么**：出货的 `preflight.sh` 是按「单一 clone」写的——它假定
`staging/`、`backups/`、`restore_set/` 全在仓库里。woodnaonly 自己的 clone 就是这样；
本机仓库是嵌在工作区里的子目录，本地状态全在上一层，于是最初停在第 4b 步：

```
need staging/rollback/flash-uImage (sh tools/gettrial.sh flash <run>)
```

第 4b 是 `SRC=staging/rollback sh tools/simwrite.sh`（回滚对的排演），
而过不去就意味着 4c/4d/5/6/6b/6c 全部没跑到。

**不要把本机的本地状态整个搬进仓库**。`git check-ignore` 实测：`staging/` 被忽略，
但 `backups/` 和 `restore_set/` **没有**被忽略——后者是 268 MB 中兴专有固件。
按「单一 clone」那条路走，等于把 301 MB 板子 dump 和 268 MB 专有固件放到
一次 `git add -A` 就能推到公开仓库的位置。（这两个目录现已补进 `.gitignore`，
理由和 `pon/vendor-modules/` 那条一样。）

**实际做法是让工具自己生成，而不是拷贝**，因为 `staging/rollback/` 根本不是发布产物，
它是从这台板子的 mtd2/mtd3 dump 推导出来的：

```sh
cd ci-build-repo
# 1. 回滚对：从板子 dump 推导，两份都与 pre-flash 快照 MATCH，与工作区那份逐字节相同
LOCAL=/mnt/c/ugit/SK-D840N4 OUT=$PWD/staging/rollback SKIP_BOARD=1 \
  PERMIT=/tmp/permit.txt sh tools/mkrollback.sh
# 2. 4c 排演用的 initramfs：只拷 3 个输入（1.2 MB），然后跑 preflight 自己打印的那条命令
mkdir -p staging/ctl/rootfs
cp /mnt/c/ugit/SK-D840N4/staging/ctl/rootfs/{busybox,ld-musl-aarch64.so.1,libgcc_s.so.1} staging/ctl/rootfs/
python3 tools/mkctlram.py --mode announce --tag K5
```

**`--mode announce` 是离线的，`--mode flash` 不是**：读过 `mkctlram.py:657-661`，
announce 用 `INIT_TMPL`（RAM 启动 init），flash 用带 `do_write` 的 `FLASH_INIT`。
生成出来的那份实测含 `192.168.9.1`、不含 `ctl-flash` 和 `mtd_debug`，是 RAM 启动那个。

**结果**：`SKIP_BOARD=1 sh tools/preflight.sh` 现在跑到**第 5 步**
（`staging/boot.bin missing`），而第 5 步正是它自己注释里写的离线/上板分界
（第 118 行：「Everything from step 5 on needs the board」）。离线段 0→4d 全过：

* 4b 两对都 `SIM OK`；
* 4c `SIM OK - payload report to flash permit, all on the real /init bytes`；
* **4d 那道门打印 `staging/trial/flash-rootfs.jffs2`（repo 那棵、run 38）并判 OK**——
  这正是今天早上修的那处，修之前它读的是工作区那棵。

第 5 步**不可能离线完成**，所以停在这里是对的：读过 `tools/checkuboot.sh` 的函数体——
它先要 `staging/boot.bin`（板上 bootloader 的 dump），然后 `:28` 直接
`$SSH "dd if=/dev/mtd1 bs=64k"` 读**活的板子**再做 diff，脚本里没有 `SKIP_BOARD` 分支。
把工作区那份 2 MB 的 `boot.bin` 拷进来也只会多走一行，然后卡在 ssh 上，所以没拷。
`backups/` 和 `restore_set/` 一个字节都没动，仍然留在工作区，由 `LOCAL=` 指过去。
（顺带：`checkuboot.sh:21` 会往 `backups/live-*` 写，那条路径现在也在 `.gitignore` 里。）




### ⚠ 这个镜像曾经会自己 reboot，操作窗口很短（2026-10-17 更正：当前镜像不会）

`etc/rc.d/S15trial` 在板上做的事（已逐个核过依赖都在且可执行）：

* 把 `192.168.9.1/24` 打在 **`/sys/class/net/*` 里每一个非 `lo` 的 netdev** 上
  ——所以主线把这个口叫别的名字也没关系；
* 起 **unbound** 的 dropbear；
* 用面板 LED 循环闪（这是不需要端口也不需要 reset 的唯一信号）。

**「在、可执行」这一条对地址不够**——`S15trial` 每一处调用都带 `2>/dev/null`，
少一个能力就是静默失败，现象与「内核没起来」一模一样。所以动词单独查过：
出货的 `/usr/libexec/ip-full` 自己印着

```
Usage: ip address {add|change|replace} IFADDR dev IFNAME [ LIFETIME ]
```

而 `S15trial:38` 是 `ip addr replace $addr dev "$n"` —— **逐字同形**，
二进制里另有独立的 `replace` 动词表条目。**语法面结案**；
剩下的是运行时行为（那时 netdev 在不在），那个只能板上答。

时序是要紧的：

| 时刻 | 发生什么 |
|---|---|
| 0 – 约 120 秒 | 分配地址的循环只跑 **8 × 15 秒** |
| **300 秒** | 除非 `/tmp/trial-noreboot` 存在，它会先 flap 三次再 `reboot -f`（canary 重启） |

**2026-10-17 更正：当前的试镜像里这个 5 分钟窗口根本不会到来。**

`usr/libexec/zrec-bringup:27` 是这个 marker 的**唯一写者**：

```sh
[ -e /tmp/trial-noreboot ] || : >/tmp/trial-noreboot
```

而它挂在 `lib/preinit/85_zrec` 的 `preinit_main` 钩子上，**preinit 阶段就跑完了**，
比 `S15trial` 的 `sleep 300` 早得多。门槛只有 `[ -x /usr/sbin/zrec ]`——
试镜像里 `/usr/sbin/zrec` 是 `-rwxr-xr-x`，**成立**。

两个后果，方向相反：

* **好消息：RAM 启动的镜像不会自己重启，操作时间不受限**，不用抢。
* **坏消息：那三次故意的 link flap 和 canary 重启也不会发生**，也就是
  「重启是否可观测」这条测试**在当前镜像上跑不到**。zrec 的注释说这是**故意的**
  （擦 flash 中途被重启是它唯一不能造成的后果），不是缺陷。

判据随时可能被打破：**哪天真在镜像里跑出一个 `[ -x /usr/sbin/zrec ]` 为假的构建，
5 分钟期限就回来了。** 所以现场仍然可以 `touch /tmp/trial-noreboot`（无害、幂等），
但**不要把「我还有 5 分钟」当作前提**；反过来也别为了试重启去 `rm` 它——
那会同时把擦除保护拆掉。

### kexec 能不能用：已证实，不是假设

试验方法的唯一前提是**正在跑的厂商内核**（不是我们的内核）支持 kexec。
这一点此前没有证据，现在有了：

- `restore_set/kernel_stock.bin` **没有加密**，它是中兴固件容器
  （`ZXIC 133 UNI V2.0.0 286632 CM OSGI` / `U-Boot uImage source file for 133 project`），
  偏移 `0x2d4` 一个 gzip 成员解出 **10,676,232 字节**：
  `Linux version 4.19.136+ (fanqiang@skyworth) (gcc 5.3.1 20160412 / Buildroot 2017.05)
  #4 SMP Mon Dec 1 20:09:27 CST 2025`
- 那个镜像被剥了符号表（`start_kernel`、`init/do_mounts` 都是 0 命中），
  所以**不能**用「找符号」来判断。要看**只存在于 `CONFIG_KEXEC=y` 的字符串**：
  - sysctl 表里有 `kexec_load_disabled` / `kexec_crash_size` /
    `kexec_crash_loaded` / `kexec_loaded`
  - `kexec_core: Starting new kernel`、`Crash kernel`、
    `kexec: Could not allocate control_code_buffer`

⇒ **`CONFIG_KEXEC=y`**，且 `kexec_load_disabled` 默认 0（内核层面没禁掉载入）。
**两个内核槽都成立**（`mtd3_kernel0` 是 2025-12 `fanqiang` 构建、
`mtd6_kernel1` 是 2024-04 `huangdi` 构建，5/5 证据各一次），
所以这是平台属性，不是某个构建的运气。

**厂商侧取证这条路开了**：`backups/` 里的中兴 dump 都是同一套容器格式
（文本头 + 一个 gzip 成员）。`mtd1_boot.bin` 的 gzip@`0xc5330` 里有两个 FDT，
`0x0` 是 704 B 的 u-boot image source，**`0x2c0` 那个 49,307 B 的才是板级设备树**。
厂商树用 `soc/gephy_apb@15400000`（中兴自己的 GEPHY），我们用
`soc/ethernet@19000000`（NPPT），**但两个 MDIO 控制器地址完全相同**
（`mdio@14f01000`、`mdio@14f02000`）—— 我们把 PHY 挂在 `mdio@14f02000` 的地址 5
是有依据的，不是猜的。

还有一条要注意：**厂商树没有 `/reserved-memory`**，保留区只有我们这边有。
所以 kexec 时厂商内核留下的状态没有任何 DT 保护，
`--mem-max=0x9b000000` 到我们保留区最低那块 `0x9d700000` 之间的
**39 MiB 就是全部余量**，不要再去动那个窗口。

**触发方式是操作者手动做的**，脚本和 `trial-files/` 里都没有自动触发：
`trial-files/` 只有 `etc/config/dropbear`、`etc/rc.d/S15trial`、
`lib/preinit/05_trial_alive`、`usr/libexec/trial-blink` 四个文件。

### 判据与读法

**判据是能 ping/ssh，不是 `Link is Up`。** 上一次 carrier 两端都亮着，
`rx_packets` 一直是 0，20 包的突发也没让对端计数动一个字节。

**出货件本身已经逐条核过**：CI 的门查的是构建机上的 `.config`，但那份配置
**嵌在 `flash-uImage` 里**，可以直接读出来（`IKCFG_ST@0xbb3b38` → 159,992 B）。
对着它核过：`DETECT_HUNG_TASK` **is not set**（run 28–32 的根因）、
`PANIC_TIMEOUT=0`、`SERIAL_AMBA_PL011{,_CONSOLE}=y`（`ttyAMA0` 真有驱动）、
`DEVTMPFS_MOUNT=y`（**`/dev/kmsg` 标记会出来**）、`REALTEK_PHY=y`、
`MDIO_ZX279133=y`、`CMDLINE` 逐字等于钉死的那串。

`CONFIG_RD_GZIP` **is not set**，这不是缺陷：profile 是
`INITRAMFS_COMPRESSION_NONE`，`trial-Image.gz` 解开后 `0x010e9448` 处直接就是
裸 newc cpio，而 `gate-Image` 根本不嵌 initramfs。**不要去「修」它。**

`/dev/kmsg` 里有三个标记（没有串口时这是唯一证据）：

```
trial: netdevs=<所有出现的 netdev> addr=192.168.9.1/24
trial: beacon before reboot
trial: canary reboot
```

第一行一次就能把三种不同的「失败」分开：**端口没协商上** / **netdev 名字对不上**
/ **镜像自己 reboot 了**。离线这边已经排掉的：四个驱动（NPPT MAC、ZX279133 MDIO、
dw-xpcs、Realtek RTL8226B PHY）在两个将要启动的内核里都在，`phy-handle` 通到
`ethernet-phy@5`（MDIO 地址 5），S15trial 依赖的每个二进制都在，
**kexec 的载荷窗口 `0x88000000–0x9b000000` 也没有压到 MAC 要的
`bmu`/`se-hash`/`idm` 三块（最低那块在 `0x9d700000`，高出窗口 39 MiB）**——
所以「端口不通」不会是载荷放错位置造成的。

### ssh 起来的是谁：`S19dropbear`，不是 `S15trial` 里那条命令

`S15trial:40` 确实会跑 `/usr/sbin/dropbear -s -B -p 22`，但**它每次都失败，
而且是在绑定 22 之前就退的**。原因已查到源码一级（2025.89，`svr-main.c:149`
`commonsetup()` → `:472` `load_all_hostkeys()` → `svr-runopts.c:729`
`dropbear_exit("No hostkeys available…")`，而 `dropbear_listen()` 在 `:489`，
排在 `commonsetup()` 返回之后）：镜像里没有任何主机密钥，也没有 `-R`。
`-B` 本身**是**合法选项（`svr-runopts.c:87`，含义是「允许空口令登录」）。

**这条不影响任何人**：`S15trial` 的主体包在 `( ... ) &` 里、末尾 `exit 0`，
立刻返回，procd 接着跑 `S19dropbear`。所以**如果串口上看到 dropbear 报 `No hostkeys available`，那是我写的死代码，不是板子坏了** ——
别顺着它查，也**别去给它加 `-R`**：加了它就会抢到 22 端口，随后 `S19dropbear`
的实例因所有端口都绑不上而 `dropbear_exit("No listening ports available.")`，
反而丢掉那条已经证明可用的、由 `dropbearkey` 生成正式密钥的路径。
现在这样每次失败，**等于主动把端口让给 S19dropbear**。

真正起 ssh 的是 `S19dropbear`（`/etc/rc.d/S19dropbear -> ../init.d/dropbear`）：
它自己用 `dropbearkey` 生成 rsa/ecdsa/ed25519 主机密钥，每个都用 `-r` 传给 dropbear，
再带 `-F -P`。试配置里 `PasswordAuth on` / `RootPasswordAuth on`，所以既不加 `-s`
也不加 `-g`。

**公钥路径是 `/etc/dropbear/authorized_keys`，不是 `/root/.ssh/authorized_keys`** ——
`/root` 在两棵树里都是空的，但 OpenWrt 有 `patches/100-pubkey_path.patch`，
uid 为 0 且没传 `-D` 时 `authorized_keys_filepath()` 直接返回 `/etc/dropbear/authorized_keys`。
不要因为看到 `/root` 空就判 ssh 认证不可能成功。

**那把钥匙是 ed25519，而且是唯一一把** —— 已查证这个构建把 ed25519 编进去了，
不要再去怀疑它：`Config.in` 是 `default y if !SMALL_FLASH`，我们的
`Device/skyworth_sk-d840n` 不声明 `small_flash`（全仓库出现 0 次），
`SMALL_FLASH` 因而不被 select，`Makefile:236-241` 把 `#define DROPBEAR_ED25519 1`
写进 `localoptions.h`；二进制里也有 `ssh-ed25519` 和服务端公钥认证的日志标记。

密码这条路是堵死的（`/etc/board.json` 不存在 ⇒ `50-root-passwd` 什么也不做，
root 的 shadow 口令字段为空，`svr-authpasswd.c:101` **无条件**拒绝空口令），
所以**公钥是唯一入口**——这也是它值得单独查一遍的原因。

**`S15trial` 用到的每个外部命令都在镜像里**，已用解析器逐条验过（8/8）：
`ip`（`/sbin/ip -> /usr/libexec/ip-full`，527,483 B）、`sleep`、`pidof`、`echo`、
`/usr/sbin/dropbear`、`/usr/libexec/trial-blink`、`/etc/init.d/network`、`/sbin/reboot`。
镜像里有三个悬空的绝对符号链接 `etc/{localtime,resolv.conf,TZ} -> /tmp/…`，
那是 OpenWrt 的正常做法（`/tmp` 是 tmpfs，开机生成），**不是缺陷**。

**这一条不用上板查了，已经从两份镜像本身结案：**

| 镜像 | `/etc/dropbear` 权限 | 来源 |
|---|---|---|
| 试 initramfs | **0755 uid 0** | cpio 条目头的 mode 字段（newc **存**权限） |
| flash rootfs | 全镜像 334 个 inode **全部 uid 0/gid 0**；100 个目录里 **0 个** group/other 可写 | jffs2 raw inode 节点的 mode 字段 |

`checkfileperm()` 只问两件事：属主是不是该用户或 root、是不是 group/other 可写。
**flash 那份镜像里没有任何一个目录满足后者**，所以 `/etc/dropbear` 不可能是失败的那个。

顺带更正一句我此前写错的话：**jffs2 是存权限的**（`fs/jffs2/fs.c:281`
`inode->i_mode = jemode_to_cpu(latest_node.mode)`）。
**不还原权限的是 `jefferson`** —— 同一份镜像里 `/root` 的真实 mode 是 0750，
jefferson 解出来是 755。别拿 jefferson 的 `stat` 当证据，也别因此以为 jffs2 不存。

想现场复核的话仍是那一条，但它现在只是复核、不是未知：

```sh
stat -c '%U %a' /etc/dropbear
```

**试镜像和刷机后系统的不对称**（别当成不一致）：

| | `/etc/config/dropbear` | 监听范围 |
|---|---|---|
| 试 initramfs | 430 B，overlay **删掉了** `option Interface 'lan'` | 所有地址 |
| flash rootfs | 310 B，与包默认值**逐字节相同** | 只绑 `lan` |

刷机后 `lan` 由 `config_generate` 按 `board.d/02_network` 建出来并带 `192.168.9.1`，
所以绑 `lan` 在那条路径上是对的；试镜像删掉它，是因为地址可能落在别的 netdev 上。

### 一条很便宜的判据：先确认 PHY 绑对了驱动（2026-10-17 加）

`sgmii` 那套推断有一个前提：**`ethernet-phy@5` 真的是一颗 RTL8226B。**
这个前提**便宜地就能在板上验**，而且失败时没有任何报错。

```sh
# 2026-10-18 更正: 原来这里写「期望 0x001ccf」—— 那个值不对, 见下。
P=/sys/class/net/eth0/phydev           # 名字就是字面量 phydev, 见下面的更正
ls -l $P/driver | sed 's/.*-> //'     # 期望 realtek, 不是 genphy
cat $P/phy_id                         # 期望 0x001cc8xx, 且是下面六个之一
cat $P/phy_interface                  # 期望 sgmii
dmesg | grep -i -e rtl8226 -e rtl8221 -e realtek
```

> **round511–517 核实过的三件事**（原文的写法都不准）：
>
> 1. **路径里的名字是字面量 `phydev`，不是 `phy@5`。**
>    `phy_sysfs_create_links()`（`phy_device.c:1441`）建的是
>    `sysfs_create_link_nowarn(&dev->dev.kobj, &phydev->mdio.dev.kobj, "phydev")`
>    —— 网卡 kobj 下**唯一**那个链接名字就叫 `phydev`。
>    所以旧写法 `phy*/driver` **能命中，纯属 `phydev` 恰好以 `phy` 开头的巧合**，
>    不是设计出来的写法。PHY 设备自己的 kobj 名是 `eth0:05`
>    （`PHY_ID_FMT` = `"%s:%02x"`，`phy.h:322`；`phy_device.c:790`）。
>
> 2. **`phy_id` 是 8 位十六进制补零**：`sysfs_emit(buf, "0x%.8lx\n", ...)`
>    （`phy_device.c:569-566`），所以期望值写成 `0x001cc840` 这种 8 位的才对，
>    看到 `0x1cc840`（6 位）不要当成「读错了」。
>
> 3. **`driver` 不是属性文件，`cat` 未必打得开。**
>    `drivers/base/core.c` 里没有 `DEVICE_ATTR(driver)`（阳性对照：该文件共 9 个
>    `DEVICE_ATTR`，所以不是 grep 坏了）；`device_add()` 里也只有
>    `device_set_driver(dev, NULL)`。**我没能定位到创建这个链接的源码位置**
>    （`core.c` / `bus.c` / `dd.c` 的 `driver_bound()` 里都没有 `"driver"` 的
>    `sysfs_create_link`），所以**不下结论说它一定是符号链接**。
>    已知的是它不是属性文件 ⇒ **`ls -l` 看箭头是最稳的写法**，
>    符号链接、不是链接两种形态都读得出驱动名。
>    `readlink` / `basename` / `ls` / `cat` / `tr` / `grep` / `hexdump` / `sed`
>    在 flash rootfs、initramfs、**社区 4.19 rootfs** 三棵用户空间里实测都在
>    （`od` 没有）。

**`driver` 显示 `genphy` 就是没匹配上。** 因为我们给 PHY 节点**故意不写 `compatible`**
（§7 第 4 条，为了不覆盖按 ID 的匹配），ID 认不出来时 phylib 会**静默退回 `genphy`** ——
不报错，现象只是「ping 不通」，和「内核没起来」长得一模一样。
那种情况下 `sgmii` 的前提不成立，要回来重谈 PHY 节点，**不要继续往下调链路**。

> ⚠ **2026-10-18 更正一条写错的期望值。** 原来这里写 `期望 0x001ccf`。
> **这个值不是 Realtek 的 OUI**，驱动里所有 RTL822x 都在 `0x001cc8xx`。
> 照着这条判，`phy_id` 读出 `0x001cc840` 会被当成「不对」——
> 而那恰恰是**正确**的绑定。
>
> 期望值改成下面这张表（逐条读自 `drivers/net/phy/realtek/realtek_main.c`，
> ref `codex/sr1010-mainline`）。**`CONFIG_REALTEK_PHY=y` 已确认**：
> `modules.builtin:302` = `realtek.ko`。
>
> | `phy_id` | 绑到的 `.name` | 额外条件 |
> |---|---|---|
> | `0x001cc800` | `RTL8226 2.5Gbps PHY` | 且 `rtlgen_supports_2_5gbps()` 且 `rtlgen_supports_mmd()`——**按能力探测，不按型号** |
> | `0x001cc840` | `RTL8226B_RTL8221B 2.5Gbps PHY` | 且 `rtlgen_supports_mmd()` |
> | `0x001cc838` | `RTL8226-CG 2.5Gbps PHY` | 精确 |
> | `0x001cc848` | `RTL8226B-CG_RTL8221B-CG 2.5Gbps PHY` | 精确 |
> | `0x001cc849` | `RTL8221B-VB-CG 2.5Gbps PHY (C22/C45)` | 精确（`rtlgen_is_c45_match` 用**相等**不是掩码） |
> | `0x001cc84a` | `RTL8221B-VM-CG 2.5Gbps PHY (C22/C45)` | 精确 |
>
> **这六条全部带 `config_init`，而两个 `config_init` 都调
> `rtl822x_set_serdes_option_mode()`**（`realtek_main.c:1274` 与 `:1279`）。
> 所以**只要绑上这六条里的任何一条，SGMII 的 serdes option 就一定会被编程**——
> 这把 genphy 风险从「可能」压到「ID 落在这六个之外」。
>
> **更省事、也更硬的判据**：读 `phy-mode` 的两个落点。
> `RTL822X_VND1_SERDES_OPTION = 0x697a` 在 MMD30 下，字段是 `GENMASK(5,0)`；
> `phy-mode="sgmii"` 写入 **`0`**，`2500base-x` 写入 **`2`**
> （`realtek_main.c:1238-1244`）。**那颗寄存器本身用户空间读不到**（更正见下），
> 但能读到造成这个差的**那个 `phy-mode` 值**：
>
> ```sh
> # 期望 sgmii（两条都该是 sgmii）
> tr -d '\0' < /proc/device-tree/soc/ethernet@19000000/phy-mode; echo
> cat $P/phy_interface
> ```
>
> 第一条是 DT 原值。⚠ **round555 更正**：原文说这条路径「由 `zx279133.dtsi:359`
> 的 `mdio1: mdio@14f02000` 与 dts 里的 `rtl8226b: ethernet-phy@5` 推出来」——
> **那个推导会得到 `/soc/mdio@14f02000/ethernet-phy@5`，正是 round542 判错的那条**，
> 和它正上方那条命令自相矛盾。正确的是 `phy-mode` 在 **MAC 节点**上：
> `zx279133.dtsi:462` 的 `nppt: ethernet@19000000`，`phy-mode = "sgmii"` 在 dts:286、落在 `&nppt` 块内
> （同一段还有 `memory-region` / `phy-handle = <&rtl8226b>` / `status = "okay"`）；dts:333 的
> `rtl8226b: ethernet-phy@5` 在 `&mdio1` 下，**只有 `reg` 与 `max-speed`，不带 `phy-mode`**——
> 符合 DT binding（`phy-mode` 是 MAC 属性）。这条不是推导出来的，是 round542–544 手工遍历
> **出货 dtb 的 FDT 结构**确认的。
> 第二条是 `phy_device.c:582` 的 `DEVICE_ATTR_RO(phy_interface)`，
> 内容为 `phy_modes(phydev->interface)`。`tr`/`cat` 都在出货 rootfs 里（实测）。
>
> ⚠ **`0x697a` 读不了，这条是 round501–510 更正掉的。** 原先这里写的是
> `cat /sys/bus/mdio_bus/devices/ethernet-phy@5/mmd/30/697a`，**两处都是错的**：
> **设备名不对**——`PHY_ID_FMT` 是 `"%s:%02x"`（`include/linux/phy.h:322`），
> PHY 在 sysfs 里叫 `eth0:05` 这种形式，不是 `ethernet-phy@5`；
> **而且没有 `mmd/<dev>/<reg>` 这样的文件**——`mdio-mmd.c` 在 torvalds/linux
> 与 cnjn 树里**都是 404**（同目录 `Makefile`/`Kconfig` 都 200，
> 所以不是路径走错、也不是限流），旧的
> `/sys/kernel/debug/mdio_bus/<bus>/mmd/<dev>` 那套接口随文件一起没了。
> **寄存器级分析本身仍然成立**（那是从 `realtek_main.c` 推出来的），只是读不出来。
>
> ⚠ **这段证据里有一处误引，round546–548 更正**：
> 我曾写「MDIO core 已合并成单个 `drivers/net/mdio.c`
> （`drivers/net/Makefile:22`），该文件里没有任何 debugfs 注册、任何 sysfs 文件」。
> **`drivers/net/mdio.c` 不是 MDIO 总线核心**——它的文件头写着
> *`mdio.c: Generic support for MDIO-compatible transceivers`*（Solarflare 2006-2009），
> 全部内容是 `mdio45_*` / `mdio_mii_ioctl` 这类**遗留 C45 辅助代码**
> （12,232 B / 426 行）。拿它下那个结论是**拿错了文件**。
> 仍然成立的是枚举证据（`mdio-mmd.c` 两棵树都 404，同目录对照都 200）；
> 但**「用户空间读不到任意 MMD 寄存器」目前是推断，不是已核实的源码事实**——
> MDIO 总线核心本身我在本树里没找到（`drivers/net/mdio/` 35 项里没有
> `mdio_bus.c`/`mdio_device.c`，`drivers/net/` 顶层 33 个文件里也没有）。
>
> **正因为查不到总线名与 class 名，判据才改走 `$P/phy_interface`**
> （`P=/sys/class/net/eth0/phydev`）：那条路径**只需要**
> `phy_sysfs_create_links()`（`phy_device.c:1441-1454`）证明的那个
> 名字就叫 `phydev` 的符号链接，**不依赖总线名、class 名或 PHY 设备名**。

>
> **出 `2500base-x` 时不要去查驱动**（round498 实测
> `ci-build-repo/staging/trial/flash-dtb.bin` 就是那份 11,916 B 的旧 dtb，
> 见「有两棵 `staging/`」那节）：它精确对应 `phy-mode="2500base-x"`，
> 意思是**刷进去的 dtb 是 sgmii 修复之前的那份**。这种情况下 `driver` 仍会显示
> realtek，上面的 genphy 那道门不会响——**只有 `phy-mode` 这条读数能抓住它**。
> 两条都出 `sgmii` 才继续往下走。
>
> **这两条读数各自的核实级别不一样，别当成一样（round533–537）**：
> - `phy_interface` 不会被协商改掉：`phydev->interface` 在 `phy_device.c` 里
>   **只有一个赋值点**（`:1841`，在 `phy_attach_direct()` 里，该函数从 `:1752` 起），
>   其余三处是读；而 `phy_attach_direct()` 在本文件只有**一个**调用点
>   （`:1234`，在 `phy_connect_direct()` 里）——都在连接路径上，不在链路协商路径上
>   （对照：`phy_change_link`/`ch_status` 在同一文件里 6 处）。
>   ⚠ round556 撤回这里原先引的 `phy_modify_interface`：**那个符号在我本地
>   全部源码里一处都不存在**（`modify_interface` 全树 0 命中，
>   对照 `phydev` 708 处），拿它的缺席当证据是空论证。真正的证据是上面
>   那条写入链，详见 docs/07 对应小节。
>   它是「驱动连接时向 DT 要到了什么」的一次性镜像，**来源层面已核实**。
> - `/proc/device-tree/.../phy-mode` 在**产物层面**已核实：
>   出货 dtb 的 offset 8340 就是 `sgmii`（run 41 的 `trial-dtb.bin` 刚又核一遍）。
> - 中间那一步「DT 字符串被解析成那个枚举」是通用 OF 代码，但**我没能在本树里
>   定位到它**（`drivers/of/net/`、`drivers/net/of_net.c`、`drivers/of/base/of_net.c`
>   都是 404；枚举 `drivers/of/` 只剩 `unittest-data`，枚举 `drivers/net/` 顶层
>   29 个 .c 里名字含 `of` 的为 0）。**所以这一环不写成「已核实」。**
>
> **这颗芯片的 IEEE ID 在本项目任何记录里都没有出现过**：厂商侧 dmesg 只有
> `r8226b_phy_probe`、`phy_id:0x5`（那是 **MDIO 地址 5**，不是 IEEE ID）和
> `14f02000.mdio "MDIO id = 1"`。所以**上面那张表是「期望集合」，不是「已知值」**。

**为什么要问这个**：上游自己的 hardware-truth 写着
「`rlt8226b.ko` 被加载并初始化，但现有证据**不能证明板上另有一颗独立 RTL8226B**，
更可能是 SDK/交换芯片 PHY 支持」——**那条是关于 SR1010 的**（那边有 RTL8372N 交换芯片），
我们这块板有自己板子的 dmesg（`r8226b_phy_probe` / `phy_id:0x5`），**不受它直接反驳**。
但它点出了这条链路上的机制性风险，所以值得在板上花三秒看一眼。

### `phy-mode = "sgmii"` 到底改了什么：从 DTS 一行一直推到 PHY 寄存器

上一小节说「上游在 SGMII 上没有硬件历史，只能按第一次跑设期望」。
**2026-10-18 补：那个期望现在有了寄存器级的支撑**，不再只是「应该对」。

读的是 `drivers/net/phy/realtek/realtek_main.c`（ref `codex/sr1010-mainline`，
与 CI 钉的 submodule commit 同一个分支）。函数体逐行：

```c
/* :1214  —— docs/07 引用过但一直没能独立复核的那个符号，就是它 */
static int rtl822x_set_serdes_option_mode(struct phy_device *phydev, bool gen1)
{
	has_2500 = test_bit(PHY_INTERFACE_MODE_2500BASEX, phydev->host_interfaces) ||
		   phydev->interface == PHY_INTERFACE_MODE_2500BASEX;
	has_sgmii = test_bit(PHY_INTERFACE_MODE_SGMII,  phydev->host_interfaces) ||
		    phydev->interface == PHY_INTERFACE_MODE_SGMII;
	...
	if (has_2500 && !has_sgmii) {
		mode = RTL822X_VND1_SERDES_OPTION_MODE_2500BASEX;      /* = 2 */
		phydev->rate_matching = RATE_MATCH_PAUSE;
	} else {
		mode = RTL822X_VND1_SERDES_OPTION_MODE_2500BASEX_SGMII; /* = 0 */
		phydev->rate_matching = RATE_MATCH_NONE;
	}
	...
	phy_modify_mmd_changed(phydev, MDIO_MMD_VEND1,
			       RTL822X_VND1_SERDES_OPTION,          /* 0x697a */
			       RTL822X_VND1_SERDES_OPTION_MODE_MASK, /* GENMASK(5,0) */
			       mode);
}
```

于是 DTS 那一行的后果是确定的：

| `phy-mode` | `has_2500` | `has_sgmii` | 写进 `0x697a` 低 6 位 | `rate_matching` |
|---|---|---|---|---|
| `"sgmii"`（现在） | 0 | 1 | **`0`** | `NONE` |
| `"2500base-x"`（dtsi 默认，被我们翻掉） | 1 | 0 | **`2`** | `PAUSE` |

**这解释了那次实测。** `2500base-x` 时 PHY 的 serdes 跑 2.5G、而线缆那一端是 1G，
于是 phylink 拿到 `Link is Up - 1Gbps/Full`、两端 carrier 都是 1，
**而 board rx_packets 恒为 0、20 包突发在对端计数器上一个字节都不动**
（dts 注释 L267-274 记的 2026-10-16 实测）。改成 `sgmii` 之后写入值变成 `0`，
PHY 的 serdes 与对端同速——**「Up 但一字节不通」这个失效模式在这一层被消掉了。**

⚠ **仍然不是「已验证」**：这一整段是从源码推出来的，`phy_id` 和寄存器实测都还是板上。
但期望值的形状现在有了：**低 6 位必须是 0**，而 `2` 就复现那次死链。

### 期望值：SGMII 上游从来没在硬件上跑过


`phy-mode = "sgmii"` 现在的证据是三份：驱动只接受 `{2500BASEX, SGMII}`（`zx279133-main.c:477-480`）、
驱动从 `plat_132.ko` 逆出来的 `xpcs_1g_mode_conf(xmac, speed=3, duplex=1, pcs_mode=2)`
（`zx279133-mac.c:180`，与厂商 bootloader 打印 `xmac 0 speed set 2 in sgmii(xmac speed 3)` 对上）、
以及上游 `SERDES_DRIVER.md` 的模式映射。

**但上游全部硬件证据都在 2.5GBASE-X 那条路上**
（`NPPT_FORWARDING.md` 报 2.38 Gbit/s 实测上限，端点是 ZX279051）。
在六份上游 hardware-truth / acceptance 文档里，**`sgmii` 只出现一次，
而且是在厂商符号名 `xpcs_sgmii_mode` 里**。

> **按「第一次跑」设期望值，不要按「已验证」设。**
> 成不成，看它能不能 ping/ssh —— 不看 `Link is Up`。

## 5b. 刷完的板子到底答哪个地址（已从镜像实测，不是推论）

这一条被改错过两次，所以写下推导。全部读自
`staging/trial/flash-rootfs.jffs2`（`jefferson` 抽取，`sh staging/diag-20261014/extract_with_jefferson.sh`）：

| 环节 | 实测 |
|---|---|
| `/etc/config/network` | **不存在** → 由 `config_generate` 在首次开机创建 |
| `/etc/board.d/02_network` | 存在，2563 B —— overlay 确实进了产物 |
| `/etc/config/dropbear` | 310 B，带 `option Interface 'lan'`（包默认值，无人覆盖） |
| `/etc/init.d/dropbear` | 12451 B，守护进程确实会启动 |
| `/etc/uci-defaults/` | **没有 `02_network`** |
| `/bin/board_detect` | 才是 `board.d` 的执行者，只在 `/etc/board.json` 为空时跑（首次开机） |
| `/lib/preinit/82_config_generate` | **先 `board_detect`，后 `config_generate`** |

关键在于 `ucidef_set_interface` 写的是 **board JSON**，不是 `/etc/config/network`；
`config_generate` 再用 `json_get_vars device ... ipaddr netmask` 把这些值**读回来**生成
`/etc/config/network`。**所以 overlay 的 `ipaddr "192.168.9.1"` 是 `config_generate` 的输入，
不是被它覆盖的东西**，它的兜底 `192.168.1.1` 根本走不到。

桥的来历：`ucidef_set_interface` 里 `device` 的值不含空格，于是走 `json_add_string`
成为普通 `device` 字段、**不进 ports 数组**；而 `generate_network` 里
`[ "$1" = "lan" -a -z "$ports" ]` **恰恰在这种无 ports 的情况下强制建桥**，
生成名为 `br-lan`、唯一端口 `eth0` 的桥，再把 lan 指过去。

**结论：板子答 192.168.9.1，由 br-lan over eth0 承载；.2 不存在。**

### 链条的另一半：`board_name` 决定上面那句成不成立（2026-10-17，只读函数体）

`02_network:18` 是 `case "$(board_name)" in` 起的头。这个函数**不**定义在
`uci-defaults.sh` 里，全树搜只有一处定义，在 `/lib/functions.sh:532`：

```sh
board_name() {
	[ -e /tmp/sysinfo/board_name ] && cat /tmp/sysinfo/board_name || echo "generic"
}
```

而 `/tmp/sysinfo/board_name` 由 `lib/preinit/02_sysinfo:4-5` 造出来：

```sh
echo "$(strings /proc/device-tree/compatible | head -1)" > /tmp/sysinfo/board_name
```

**根节点 `compatible` 的第一个字符串，就是决定 `192.168.9.1` 会不会被写下去的那个值。**
本板 dtb 根节点是 `"skyworth,sk-d840n\0zte,zx279133"`，第一项 `skyworth,sk-d840n`，
命中 `02_network:22` 那一支。取不到 `/proc/device-tree` 时 `board_name` 返回
`generic`，同样不命中——**静默变成 `*)` 分支，打印 "Unsupported hardware"，
什么接口都不建**。

> ⚠ **这条推导在 PC 上无法实跑**：`/sbin/uci` 的 ELF 头是
> `7f 45 4c 46 02 01 … b7 00`，`e_machine=0xb7`=183=**AArch64 静态可执行**，
> `/usr/bin/jshn` 同理。WSL 是 x86_64，跑不了。**所以下面那段 `/etc/config/network`
> 是从镜像里这几个函数的源码推出来的，不是执行出来的**；dtb 的 `compatible`
> 那半边另有实测（CI 的 dtb 门 + `docs/07` 的 `fdtdump`）。

推导出的 `/etc/config/network`（`config_generate` 的 `generate_network`，
`:94` 起，`:107`–`:129` 强制建桥，`:166` 取 `ipad`）：

> 下面是 `uci` 归一化之后的**形态**，不是逐字节的 uci 输出——`uci` 会重排并按
> section 分组，板上要看就用 `uci show network`。**关键内容就是这几行。**

```
config interface 'loopback'          # generate_static_network()
        option device 'lo'
config globals 'globals'
config device
        option name 'br-lan'
        option type 'bridge'
        list ports 'eth0'
config interface 'lan'
        option device 'br-lan'
        option metric ''
        option proto 'static'
        list ipaddr '192.168.9.1/24'
        option ip6assign '60'
```

`/24` 的来历：`netmask "255.255.255.0"` → `str2ip` 得 `0xffffff00` →
`netmask2prefix`（`lib/functions/ipv4.sh`，`is_contiguous` + `bitcount`）得 `24`。
`ipad=${ipaddr:-"192.168.1.1"}` 里 `ipaddr` 已由 board.d 给定，兜底走不到。
`ip6assign` 那行在 `[ -e /proc/sys/net/ipv6 ]` 成立时写，所以板子还会有一个 ULA。

**桥不需要任何模块**：`lib/modules/6.18.38+/modules.builtin:433` 就是 `bridge.ko`，
且该目录下 **0 个 `.ko`**（441 条全部内建），`/etc/modules.d` 整个不存在。
若 `br-lan` 得靠 `kmod-bridge`，这个镜像起不来桥。

**镜像里根本没有 `wan` 接口**：`02_network` 只建 `lan`；`99-default_network:9` 是
`json_is_a network object && exit 0`，见到 lan 就退出、不补 wan；`config_generate`
只遍历 board.json 里存在的 network 键。后果见 §5c。

## 5c. 上游 SR1010 的残留在本板上照样会跑（2026-10-17 实测，**不是缺陷**）

镜像里带着一整套 SR1010 的东西，本板没有对应硬件，它们**照样会被执行**：

| 文件 | 大小 | 在本板上的实际行为 |
|---|---|---|
| `/etc/init.d/sr1010-netled` | 352 B | `S99sr1010-netled` 已 enable。`start_service` 的闸是 `[ "$(uci -q get sr1010-netled.main.enabled)" = 0 ] && return`；`/etc/config/sr1010-netled` 里 `enabled='1'`，**闸不关**，procd 拉起 `/usr/sbin/sr1010-netled` 并 `respawn` |
| `/usr/sbin/sr1010-netled` | 2,342 B | **是 `#!/bin/sh` 脚本**（首字节 `23 21 2f 62`），不是二进制。它每 5 s 查一次 `ubus call network.interface.wan status`——**本板没有 wan**，ubus 报错被 `2>/dev/null` 吃掉，`online_now` 恒返回 1，于是永远停在 offline |
| 同脚本的 `show()` | — | 写 `/sys/class/leds/sr1010:{white,red}/brightness`。`01_leds:7-8` 的 `case` 只认 `zte,zx279133-sr1010`，本板不命中，所以这两个 LED **不存在**，写失败；脚本没有 `set -e`，继续跑 |
| `/etc/uci-defaults/98-sr1010-defaults` | 1,160 B | 会跑：设 `CST-8` / `Asia/Shanghai`、`luci.main.lang=zh_cn`、`flow_offloading=1`+`flow_offloading_hw=1`、`network.@globals[0].packet_steering=0`，并写 `system.@system[0].sr1010_defaults=1` |
| `/etc/uci-defaults/99-sr1010-netled` | 50 B | `/etc/init.d/sr1010-netled enable` |
| `/etc/sysctl.d/12-sr1010-conntrack.conf` | — | 一组 sysctl |

**代价**：稳态下每 5 s 一次 `ubus` fork，CPU 可忽略；启动时那两次 LED 写失败会在串口
留两行 `No such file or directory`。**它不会挡住 ping 或 ssh。**
现场看到 `sr1010-netled` 进程**不是**异常，别顺手当成故障去 kill。
顺带白拿的好处：界面是中文、时区是北京。

## 5d. 开机顺序：实测 rc.d 全表（2026-10-17）

试 initramfs 的 `/etc/rc.d/` 共 **29** 项（**22** 个 `S*` + 7 个 `K*`），
其中**只有 2 个是普通文件**：

```
S15trial   3106 B  -rwxr-xr-x   ← CI 打进试镜像的普通文件
S21zrec     347 B  -rwxr-xr-x   ← CI 打进试镜像的普通文件
```

其余 27 个全是 `-> ../init.d/*` 符号链接。**flash rootfs 里这些一个都没有**：
`S15trial`、`S21zrec`、`lib/preinit/85_zrec`、`usr/libexec/zrec-bringup`、`usr/sbin/zrec`
在 `/tmp/r320x` 上全部 ABSENT，全树 grep `trial-noreboot` 也是 0 命中。
试镜像里 zrec 有 7 处（`etc/rc.d/S21zrec`、`lib/apk/packages/zrec.list`、
`lib/preinit/85_zrec`、`usr/libexec/{zrec-bringup,zrec-ops}`、`usr/sbin/{zrec,zrec-run}`、`www/zrec`）。

与网口有关的顺序：

| 次序 | 脚本 | 做什么 |
|---|---|---|
| 15 | `S15trial` | 试镜像专属：把 `192.168.9.1/24` 打在**每个**非 `lo` netdev 上（S15trial:29-42），并按需起一个 **unbound** dropbear（`:40`）。分配循环只跑 **8 × 15 秒** |
| 19 | `S19dnsmasq` / `S19dropbear` | 真正的 dropbear 在这里起 |
| 20 | `S20network` → `../init.d/network` | 2,701 B、`START=20`、`USE_PROCD=1`，只 `procd_open_instance` 起 `/sbin/netifd`。**启动路径上不动任何地址**——全脚本里唯一的 `ifdown -a` 在 `stop_service()`（L38）和 `shutdown()`（L141） |
| 21 | `S21zrec` | `zrec-bringup` 的第二个入口；它自己用 `pidof zrec` 保证幂等（第一个入口是 `lib/preinit/85_zrec` 注册的 `preinit_main` 钩子） |
| 99 | `S99sr1010-netled` | 见 §5c |

**`trialtag` 在两棵完整的树上 0 命中**（试镜像 532 个文件、flash rootfs 520 个文件，
二进制一起 grep）。U-Boot CMDLINE 里的 `trialtag=<ts>` **没有任何用户态读者**——
它是给 bootloader/回滚脚本那一侧用的，不要在用户态里找它。

### 5e. preinit 会在早期给 `192.168.1.1` 一下又收走（2026-10-17）

`lib/preinit/00_preinit.conf:7-10` 里：

```sh
pi_ifname=""
pi_ip="192.168.1.1"
pi_netmask="255.255.255.0"
pi_broadcast="192.168.1.255"
```

`10_indicate_preinit` 的 `preinit_ip()` 里 `pi_ifname` 与 `$ifname` 都空，
`/etc/board.d/` 又在，于是走 `preinit_config_board`：它自己再跑一遍
`/bin/board_detect /tmp/board.json`，从 `network.lan` 取出 `eth0`
（`ports` 为空 → `ports="$device"`，和 §5b 同一个补法），最后
`preinit_ip_config eth0` → `ip link set eth0 up` +
**`ip -4 address add 192.168.1.1/24 broadcast 192.168.1.255 dev eth0`**。

所以**正常开机会在 preinit 阶段给 eth0 挂上 `192.168.1.1/24`**，
到 `/sbin/init` 之前由 `99_10_run_init` 的 `preinit_ip_deconfig`
（`ip -4 address flush dev eth0` + `ip link set eth0 down`）清掉。
`ip-full` 两种动词都有：`ip address {save|flush} [ dev IFNAME ] …`
与 `[ broadcast ADDR ] [ anycast ADDR ]` 都在二进制里。

现场用得上两条：

1. **它是「主线把这个口叫 `eth0`」的正向证据，而且比 netifd 早得多。**
   `preinit_ip_config` 开头是 `grep -q "$netdev" /proc/net/dev || return`——
   口不叫 `eth0` 就什么也不做。所以**开机早期某个口上冒出 `192.168.1.1`、
   随后消失**，说明名字对了。**一直不出现不能反推名字错**：也可能是驱动还没
   probe 完，或者已经过去了那个窗口。
2. 那段窗口里，`ping 192.168.1.1` 可能打到**板子**而不是用户自己那台路由
   （两者同在 `192.168.1.0/24`）。`gatewatch.sh` 不用 `192.168.1.1` 当信号的理由
   因此**加强**，不是减弱。

> ⚠ **不要把 failsafe 当成一个会自己出现的状态。** `30_failsafe_wait:97-101` 只给三条路：
> `failsafe=` 在 cmdline 里、**串口 4 秒内按 `f`**、`/tmp/failsafe_button` 存在。
> 本板 cmdline 是写死的（无 `failsafe=`）。第三条有**两条独立的理由**堵死：
> dtb 是**故意不给 `reset-gpios`**（`zx279133-sk-d840n.dts:326`），
> 而 `modules.builtin` 的 441 条里 **`input` / `key` / `button` 一个都没有**
> —— 内核连 `CONFIG_INPUT` 都没编，任何按键 uevent 都不会发生。
> （`/etc/rc.button/failsafe` 本身是在的，但由 procd 分发按键 uevent 才有意义。）
> 那个 0 用同一张表里已知必在的 `bridge.ko`、`dsa_core.ko` 做过阳性对照，各命中 1。
> **推论：`/etc/rc.button/` 下那五个（`failsafe` `power` `reboot` `reset` `rfkill`）
> 在这个镜像里全是死代码。**
> **failsafe 只能靠串口主动进。** 因此「只答 `.1` 不答 `.9`」**不是** failsafe 的签名；
> 无串口时这个现象要按别的线索查。
>
> 顺带记清两条路径各自答哪个地址：串口按 `f` 进去时 deconfig 永不执行
> （`40_run_failsafe_hook` 阻塞在 `lock -w`，排在后面的 `99_10_run_init` 跑不到），
> 所以 **flash rootfs 停在 failsafe 时答 `192.168.1.1`**；
> 而**试镜像**在 failsafe 里是 `85_zrec` 的 `zrec-bringup` 接手，它发的是
> **`192.168.9.1`**。同一个按键，两种镜像答的地址不一样。

### 5f. 镜像里没有防火墙 —— 对 ssh 是好消息，对安全是事实陈述

`/etc/init.d/firewall` **不存在**，`nft` 二进制不存在，`/etc/config/firewall`
不存在，`rc.d` 里也没有任何 `*firewall*`。**没有任何入站过滤。**

* 对本任务：**ssh 不可能被规则挡掉**，§5 第 2 步失败时不必再往防火墙方向想。
* 同样是真的：这块盒子一旦网口可达就没有任何入站过滤。**这是现状陈述，
  不是本轮要修的缺陷**——这轮的目标是让它可维护；要不要加防火墙是另一个决定。
* 副作用：`98-sr1010-defaults` 里 `uci -q set firewall.@defaults[0].flow_offloading='1'`
  写进一个没人读的配置，`-q` 把错误吃掉。无害。

其余开机服务也顺手清了一遍，都没有能挡住 ssh 的：

| 服务 | 状态 |
|---|---|
| `S19dnsmasq` | `/etc/config/dhcp`（1,262 B）：`config dhcp lan` → `option interface 'lan'`（dnsmasq 在 br-lan 上发 DHCP）；`config dhcp wan` → **`option ignore '1'`**，所以**缺 wan 接口不会让 dnsmasq 报错退出** |
| `S96led` | **`/etc/config/leds` 不存在** ⇒ 没有 LED 要点，不会刷错误 |
| `S50cron` | **`/etc/crontabs/` 是空的** ⇒ 没有定时任务碰网络 |
| `S10system` / `S98sysntpd` | `/etc/config/system` 不存在是**正常的**，`config_generate` 的 `generate_static_system()` 首次开机会建 |

### 5g. `eth0` 是**结构上**确定的名字，不是碰运气（2026-10-17，出货件层面）

以前这条列在 §3「完全没验证的」里。它可以拆成两半，**只有一半需要板子**。

**名字这一半：在出货件 `staging/trial/flash-dtb.bin`（12,009 B，
md5 `cfbfe187a6a5a447c41c348848574fa7`，与 `trial-dtb.bin` 同 md5）上确定。**

| 出货件里的事实 | 值 |
|---|---|
| 根 `compatible` | `"skyworth,sk-d840n\0zte,zx279133"` → `board_name` = `skyworth,sk-d840n` |
| `ethernet@19000000` | `status = "okay"`、`phy-mode = "sgmii"`、`pcs-handle = <0xf>`、`phy-handle = <0x18>` |
| `ethernet-pcs@1a000000`（xpcs0） | `status = "okay"` |
| `ethernet-pcs@1b000000`（xpcs1） | `status = "okay"` |
| `ethernet*` 节点总数 | **3** = 1 个 MAC + 2 个 PCS |
| `dsa` / `ethernet-switch` / `mdio-switch` / `lan-conduit` / `rtl8372` / `zx279051` | **各 0 次** |
| `2500base-x` | **0 次**（`sgmii` 1 次 —— 修复确实在产物里） |

搜索有效性用同表里的阳性对照确认过：`phy-handle` ×1、`pcs-handle` ×2、
`lan-pcs-handle` ×1、`mdio` ×4、`snps,dw-xpcs` ×2。

**为什么这就够：** 整个设备树里**只有一个以太网 MAC 节点、零个 DSA 交换机**。
netdev 名字是注册顺序里从 `eth%d` 池取的第一个，所以从 SoC 出来的**只能是 `eth0`**。
`modules.builtin` 里那个 `tag_zx279133_rtl8372n.ko` 虽然**编进了内核**，
但设备树里 `rtl8372` 出现 0 次 ⇒ **没有节点可绑 ⇒ 永远不 probe ⇒ 不占名字**。
上游 dtsi 里 13 个 `status` 只有 1 个是 `okay`（`watchdog0`，而 overlay 又把它关了）；
`nppt`/`xpcs0`/`xpcs1`/`mdio0`/`mdio1`/`pon_serdes`/`uni_serdes` 默认全是 `disabled`，
全部由 `zx279133-sk-d840n.dts` 显式打开——**少一行 `&xpcs1 { status = "okay"; }`
就会让 `pcs-handle` 指向一个不会被创建的设备**（dts:339-345 就是干这个的）。

**carrier 这一半仍然只能板上答**：口上有没有 link、PHY 能不能协商、
`192.168.9.1` 会不会应答。名字对了而 link 不通，是另一个问题，别混。

> ⚠ **本节记录了本项目第五次「拿旧件当活件」的差点。**
> 同一个构建目录树里还有一份 **11,916 B** 的 dtb，它的 `phy-mode` 是 `2500base-x`——
> 那是 **sgmii 修复之前**的旧构建（`docs/07` 记的 run 33 那份也是 11,916 B）。
> 差点据它报一个「出货件 phy-mode 是错的」这种致命缺陷。
> `/tmp` 里还留着两个**故意埋的诱饵** dtb，一个 13 B 内容是 `a patched dtb`，
> 一个 19 B 内容是 `not the shipped dtb`——它们就是为拦这类错误而放的。
> **判断一份 dtb 是不是活件，要先看它在哪、旁边有什么，别只看它反编译出来像什么。**

---

## 6. CI 成功之后

**先确认这个 run 真的产出了 flash set**，再动手取：

```sh
# 0. tag 在不在？产物是什么？（不需要登录，Windows 侧 curl）
#    https://github.com/woodnaonly/zx279133-ponwrt-build/releases/expanded_assets/<tag>
#    initramfs job 只出 trial 集（trial-Image.gz / sk-d840n-initramfs.itb …），
#    **没有 jffs2**；jffs2 归 flash job。看清楚再取。
git ls-remote --tags origin | grep skd840n_flash      # 通道二，最省事

sh tools/gettrial.sh flash     <新的 run id>   # 会在 scratch 目录逐文件验清单后才落地
sh tools/gettrial.sh initramfs <同一个 run id>  # ← 这一行原来漏了，见下
sh tools/check-imagelineage.sh staging/trial/flash-rootfs.jffs2   # 三层全过；**必须显式传**，
                                                             # 无参数会去看工作区根那棵（补二十六）
sh staging/diag-20261014/round167-run-regression.sh   # 见下面那条更正
```

> ⚠ **2026-10-17 更正这一段的两处**：
>
> 1. **跑回归要用 `round167-run-regression.sh`，不是
>    `regress_round_backup.sh`。** 后者在两处都存在，而**版本不同**：
>    `ci-build-repo/tests/regress_round_backup.sh`（9,709 B，决定成败的那个，
>    `round167` 内部调的就是它）与 `staging/diag-20261014/regress_round_backup.sh`
>    （8,908 B，另一份）。引用了后者等于跑了**不是权威的那份**。
> 2. **「41/41」这个数是错的，且它不是任何脚本断言的**——
>    两个脚本里 `41` 与 `42` 各 **0 处**命中（阳性对照：`round167` 里的 `41`
>    只出现在临时文件名 `/tmp/reg41.txt`）。实测当前是 **42 套件**：
>    `suites listed in the loop: 42 / suites actually run: 42 / verdicts recorded: 42`、
>    `rc_all=0`、`FAIL markers: 0`、`regression exit=0`。
>    **套件数是量出来的，不是脚本保证的** —— 以后它变了要重新量，别照抄。
>
> 另外 `sh tools/gettrial.sh` 会写到**当前 CWD 下的 `staging/trial/`**
> （`gettrial.sh:18` 是 `D=staging/trial`，相对路径）。两棵 `tools/gettrial.sh`
> **逐字节相同**（7,142 B / md5 `a5f8ed8397`），所以**内容一样、落地位置不同**：
> §5 让你 `cd /mnt/c/ugit/SK-D840N4`，落的正是**工作区根**那棵（自洽的那棵）。
> 若你改成在 `ci-build-repo/` 里跑，落的就会是**仓库那棵**——
> 它的 flash 半边是 run 21 的旧件（见「有两棵 `staging/`」那节）。

**两个模式都要跑，而且要钉同一个 run。** `flash` 模式只更新 flash 那半，
同目录里那半 trial 文件**原封不动**——2026-10-16 实测踩到过：

| `staging/trial/` | 大小 | `phy-mode` |
|---|---|---|
| `flash-dtb.bin`（刚落地，run 33） | 12,009 B | `sgmii` |
| `trial-dtb.bin`（**没动**，run 27） | 12,013 B | **`2500base-x`** |

于是**照原来那一条命令走完，再从同一个目录做 RAM 启动，会把 `2500base-x` 放回 eth0**，
也就是实测「carrier 亮着一个帧都不过」的那个配置，而一路上没有任何提示。

### **当前这个坑是填上的（2026-10-17 实测，可以放心走）**

run 39 两半到齐之后量了一遍 `staging/` `backups/` `restore_set/` 里每一个裸 FDT
（打开 116 个文件，找到 4 个 dtb）：

| 文件 | 大小 | `phy-mode` |
|---|---|---|
| `staging/ctl/gate-dtb` | 12,009 B | `sgmii` |
| `staging/trial/flash-dtb.bin` | 12,009 B | `sgmii` |
| `staging/trial/trial-dtb.bin` | 12,009 B | `sgmii` |
| `staging/rollback/flash-dtb.bin`（厂商，回滚目标） | 48,463 B | 无此属性 |

三份自有 dtb **md5 全是 `cfbfe187a6a5a447c41c348848574fa7`**、
**sha256 前 16 位全是 `088edb435e5e782a`**——**逐字节相同**。
带 `phy-mode=2500base-x` 的文件：**0 个**。

所以上面那张「12,013 B / `2500base-x`」的表现在只是**历史**。
`trial-dtb.bin` 已经换成了 sgmii 那一份，**RAM 启动不会再把它放回 eth0**。

上面那条警告本身**仍然留着**：下一次只落地 flash 半时，同一件事会重新 armed，
而它 armed 的样子没有任何提示。一句 `sha256sum staging/trial/*.bin` 就够。

两个半都落地之后还有两处变化值得知道：

* 两个 dtb 变成同一个文件，`sha256sums-trial` 覆盖目录里每一个 payload；
* 门多查了一样东西——RAM 启动器那项从「不在本 set 里所以不可能陈旧」
  （一个**空洞的通过**：文件不在，没得比）变成 `trial-kexec.sh matches templates/`。

### `gettrial.sh` 依赖 API，而 API 在这条网络上时开时关

它用 `api.github.com/repos/…/releases` 列表来定位 release。共享 NAT 的 60/hr 配额：

| 时刻 | `core` 配额 | `/releases` |
|---|---|---|
| 2026-10-16 ~16:35 | **0 / 60** | 403 |
| 2026-10-16 ~17:04 | **57 / 60** | 200 |

所以**取件可能失败也可能成功，取决于你几点按**。`WANT_RUN`（第二个参数）能钉住 run，
但解决不了配额——列表请求本身就要花配额。**不依赖 API 的两条路**：

```sh
git -C ci-build-repo ls-remote --tags origin | grep skd840n_flash
curl -sSL https://github.com/woodnaonly/zx279133-ponwrt-build/releases/expanded_assets/<tag>
```

### `staging/trial/` 里有三个内核

`trial-Image`（28,092,424 B，10-12）、`trial-Image.gz`（11,426,876 B，10-16）、
`trial-Image.old13`（29,989,376 B，10-10）。
**当前 `.gz` 解开是 28,092,424 B，而那个散件也是 28,092,424 B——同尺寸、不同内容**
（kernel Image 会填充，大小分不出新旧）。好消息是 `trial-kexec.sh` 只用 `.gz`
（取件后在板子上 `gunzip -c trial-Image.gz > Image`），所以散件是惰性的。
但挑错的信号是零。

`check-imagelineage.sh` 的**无参数调用曾经是坏的**（2026-10-16 修，`e8f6036`）：
三个默认值写成相对路径，而它自己先 `cd` 进了仓库，于是每次都先吐三条
**编造的** REFUSE，把真正的问题埋在下面。`HANDOFF.md` 这一节原来写的
`sh tools/check-imagelineage.sh` 照抄必然中招。现在默认全部从一个绝对 `$REPO` 派生。
判定逻辑一个字没动——**只改它打印的理由是真话**。

`gettrial.sh` 有两处容易踩的地方，都已修：

* 清单必须覆盖的文件**按 mode 区分**——`flash-uImage` / `flash-dtb.bin` 只在
  `flash` 模式要求，`initramfs` 模式无条件要求它们会拒绝每一个 release。
* 两个 release 曾发布同名 `sha256sums-release`（flash 415 B / initramfs 89 B），
  后一次取件覆盖前一次，导致 lineage 门拿错清单、永远过不去。
  现在 flash 保留原名，initramfs 存 `sha256sums-initramfs-release`。

新 release 的 `sha256sums-release` 是 **5 项**（含 `flash-sk-d840n.sh`）。
**这条预判已经兑现**：run 25 的两份都取到了，lineage 门三层全绿——
**那是 2026-10-15 的状态，现在不成立了**，见下。

### 2026-10-16 更正：目前**不存在**可刷的 flash set

上面那句「三层全绿」描述的是 run 25 的产物，它**现在已经过期**，而且过期的理由
正是这道门要抓的东西：

| 产物 | 状态 |
|---|---|
| `staging/trial/`（run 25） | 不可刷：`platform.sh` 2172 B、`02_network` 1080 B、`flash-sk-d840n.sh` 3648 B，清单 4 条漏了 `flash-sk-d840n.sh` |
| `skd840n_flash_27_...`（最后一次绿） | 不可刷：镜像里**没有** `etc/dropbear/authorized_keys`，早于那笔 overlay 提交 |
| run 28–32 | 全红在那道 PHY 门上，**一次 flash set 都没产出** |
| run 33 的 `initramfs` | 绿，但只出 trial 集，没有 jffs2 |

所以在 run 33 的 `flash` job 跑完之前，**不要安排刷机**。刷 run 27 会得到一台
开机后谁也 ssh 不上去的板子，而所有工具都用 `ssh -o BatchMode=yes`——密码交互早就关了。
拿到新 set 之后 `check-imagelineage.sh` 必须**三层全过**才动手。

### 这一条作废了：run 39 两半俱全（2026-10-16）

run 33 / 37 / 38 的 initramfs job 都没出产物，所以「先等 flash job」这个说法已经不成立。
**run 39 是第一个 flash 与 initramfs 同时存在的 run**：

```
skd840n_flash_39_38896098366
skd840n_initramfs_39_38896098366
```

两半钉同一个 run 取回仓库树，rc=0，trial 集清单 4/4 OK，lineage 门**三层全过**
（第 2 层这次是真的在比 `trial-kexec.sh`，不再是「不在本集」）。内核
`6.18.38~5fc46cac3bb56a807e0cc536618a96ea-r1`，`flash-dtb.bin` 仍是
`088edb435e5e782a…`——**dtb 一个字节没变**，`phy-mode="sgmii"` 那条改动仍在。

`trial-Image.gz` 是**内核 + 内嵌 initramfs cpio**，归档 765 个条目。
其中**没有 `.ko`、没有 `modules.alias`、没有 modprobe/kmod**，
但有 `lib/modules/6.18.38+/modules.builtin`（441 条）和 `modules.builtin.modinfo`
（199,230 B）。所以驱动**全部编进内核**，RAM 启动时不需要任何 modprobe——
`zx279133.ko`、`mdio-zx279133.ko`、`zx279133-lan-conduit.ko`、
`zx279133-rtl8372n.ko`、`realtek.ko`、`pcs_xpcs.ko`、`phylink.ko`、`libphy.ko`、
`of_mdio.ko` 都在 `modules.builtin` 里。

**扫描这类镜像时的一条方法论**：initramfs 就嵌在里面，所以「在镜像里命中某个字符串」
必须先归因到内核还是用户态。边界**不能**用 `find(b"070701")` 找——
第一个命中是内核解包器自带的错误串（`incorrect cpio method used`、`no cpio magic`），
用它当边界会把 17 项证据里的 16 项错判成用户态。真边界 0x0115faf8，
用真 `cpio` 列一遍即可。详见 docs/07 补二十七。

**取件时顺手核一下 tag 指向谁**：`skd840n_initramfs_33_38862392504` 指向 `85f7e7a`，
而 run 33 的 head 是 `63ff208`（`85f7e7a` 是它的子提交，只动 `docs/`）。
`Clone sources` 是 clone main，构建跑了 1h08m，期间 main 动了。
**血统核对应以 tag 指向的 commit 为准，不要用 run head。**


---

## 7. 已经定过的事（不要重新讨论，除非有新证据）

1. **PON 协议层不补。** 厂商 ABI 不在树里、没有 OLT、没有光纤，补了既无法验证也无法运行。
2. **`fallback` 没驱动 = 致命**（原先只告警）。实测今天一行都不会拒，是护栏不是修复。
3. **`syscon` 那条分支仍是 note**，因为它问的是另一个问题（syscon core 有没有编进去），
   判据是两个字符串的代理。**2026-10-15 决定保持原样**——把 note 改成致命等于
   新增一条拒绝路径，与「收敛，不再加新门禁」相反。
4. **PHY 节点故意不给 `compatible`**——PHY 节点上的 compatible 会**覆盖**按 ID 的匹配，
   SR1010 的 `ethernet-phy-id0000.84b9` 曾让 ZX279051 驱动绑到 Realtek 芯片上。
5. **回滚只写 mtd2 + mtd3**，mtd5 永不触碰。
6. **内核版本单一真源**：三个 workflow 各自的 `KVER`。`run:` 内用 `$KVER`，
   `run:` 外只能用字面量（GitHub 不在 `name:`/`with:` 里暴露 `env` 上下文）。
7. **本机推 GitHub 走 443**：SSH 22 端口 TCP 通但握手被重置（典型的 SSH 干扰）。

   ```powershell
   $env:GIT_SSH_COMMAND = "ssh -o Hostname=ssh.github.com -p 443 -o StrictHostKeyChecking=accept-new"
   ```

8. **`dtb` 里 `pcs-handle` 不要改成 `xpcs0`**（2026-10-17 读驱动源码后写死的）。

   `xpcs1@0x1b000000` 是 **XMAC1 的 PCS**，而 NPPT MAC 就是 XMAC1（驱动里的
   `ZX279133_XMAC1_SOPC_*` 寄存器名）。`zte,lan-pcs-handle` 指向的
   `xpcs0@0x1a000000` 是 **XMAC0**，它的**唯一**消费者是 RTL8372N 交换驱动
   （`zx279133-rtl8372n.c:7785`），**这块板上没有交换芯片**。
   驱动只读 `pcs-handle`（`zx279133-main.c:434`，缺了直接 `-ENODEV`），
   拿到了就通过 `mac_select_pcs` 亲手交给 phylink
   （`phylink_of_phy_connect(..., 0)`，第三个参数 0 = 不让 phylink 自己找）。

   **改了的坏法是静默的**：没有报错，只是链路起不来。
   和「不要给 `S15trial` 加 `-R`」是同一类——看着像修好，实际掐掉唯一能走的路。

---

## 8. 读证据时的一条警告

这个项目里有 **39 次「检查器自己坏了」**，全部记在文档里。
形状高度一致：**不是逻辑错，是工具的默认行为和我的假设不一致，而错误系统性偏向某一侧。**

最典型的三个：

* `tr -s ' \t'`——单引号里 `\t` 是反斜杠加字母 **t**，把带 `t` 的路径从中间劈开，数出 37 而不是 38
* 一行注释里的**撇号**关掉了 shell 单引号，53 行之后 bash 才报错（而且报错行号比病灶晚 24 行）
* 抽取器切到 `done <…` 就停，**没包含 `exit "$rc"`**——于是致命分支和放行分支在测试里长得一样

**所以：任何一个检查器，在被用来下结论之前，先拿一个已知为真的输入校准它。**
一个会凭空造出失败的检测器比没有检测器更糟——它会把人推向修一个不存在的问题。

反过来的一次更值得记：`test_imagelineage*` 报的失败**是假的**，
工具对、被测输入漂了。**一个绑在实时状态上的断言，报错时先怀疑被测对象。**

### 本项目环境上的坑

* **`grep -a` 在 jffs2 上搜不到不构成「不存在」的证据**（节点是 zlib 压缩的），
  必须用 `jefferson`。
* **不要用 PowerShell 的 `Get-Content` 按行读 `docs/` 下的文件。**
  它按默认编码读 UTF-8，会把字节解码坏并**少报行数**——
  `docs/07-...md` 被它读成 3432 行，实际是 5310 行（现在 5679）。
  用 `wc -l`。
* **提交消息一律用 Write 工具写文件，再 `git commit -F`，不要用
  `Out-File -Encoding utf8`。** PS 5.1 的 `Out-File -Encoding utf8` 会写 BOM，
  git 把它当成消息的第一个字符存进 commit 对象。
  2026-10-16 的 `2a26e24` 就中招了：message 开头是 `ef bb bf`。
  **而且它藏得很深**——`git log --oneline` 显示成一个看不见的字符，
  `git log --format=%s` 读回来首字符又不是 0xFEFF，只有
  `git cat-file commit <h>` 逐字节看才暴露。
  要改就得 amend + force push，而「不 force push」是定死的规矩，所以只能留着。
* **复杂命令一律写脚本文件再 `wsl -d debian -e sh <脚本>`。**
  PowerShell 内联引号会吃掉正则括号、反斜杠、`$(...)`、管道和反引号。

### 2026-10-16 新增的三条通道，和三条新坑

**能读到的（不需要登录）**：

* **step 级**：`<完整 sha>/checks` 页，每步是
  `<details id="check-step-N" data-conclusion="success|failure">`，
  svg 的可访问名就是 `This step passed` / `This step failed`。
  **短 sha 在这条路径上 404，必须给完整 sha。**
* **注解级**：同一页点 job 旁的 `Open job annotations`，Annotations 区会列出
  每条 `::error` / `::warning` 的正文。**面板默认收起，收起时只显示
  「2 errors and 2 warnings」——「看到计数」不等于「读到条目」。**
* **产物级**：`releases/expanded_assets/<tag>` 直接返回产物清单 HTML。

**仍然读不到的**：完整 step 日志。未登录会话拿不到 `data-log-url`，
`/actions/runs/<id>/logs` 是 404。

坑：

* **`api.github.com` 的 403 不是"网络不通"，是共享 NAT 的 60/hr 被人用完。**
  2026-10-16 实测出口 `203.10.99.34`，`core` 恒为 0/60，轮询 10×30 秒没等到窗口。
  **`rate_limit` 自己返回 200，而 `/jobs` 返回 403——能读到配额表不等于还能用。**
  这条路是真的关上了，不要按"再等等"处理。
* **PowerShell 的 `Set-Content -NoNewline` 会把 `git show` 的多行输出压成一行。**
  `& $git show HEAD:file` 返回的是**字符串数组**，`-NoNewline` 把它们无分隔符地接起来，
  写出来的 shell 脚本整个是一行。正确写法：
  `[System.IO.File]::WriteAllText($p, (($lines -join "`n") + "`n"), (New-Object System.Text.UTF8Encoding($false)))`
* **把 `> /tmp/x.txt` 交给 PowerShell 会变成 `C:\tmp\x.txt`。**
  重定向必须在 `sh` 脚本**里面**，否则那条命令根本没按预期跑，而 `$LASTEXITCODE`
  看起来还是 0。复杂命令写脚本文件这条规矩，同样管重定向。

