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

* eth0 是否真的 link、插哪个口能通、192.168.9.1 是否应答
* 分区编号在板上的实际形态（gate round 的目的）
* PHY 是否真的协商到 2.5G

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

因此当前跑的用户空间仍是社区镜像，`02_network` 里的 `192.168.9.1`
**尚未生效**——要等重新构建镜像并写 root 才生效。

---

## 5. 板子接上之后，按这个顺序

前置：CI 成功 → 取新 flash set（见 §6）。
**`staging/trial/` 现在那份是 run 25 的产物，三层全绿，按工具标准可以刷。**

```sh
cd /mnt/c/ugit/SK-D840N4        # WSL；或在 Git Bash 里用 Windows 路径

# 0. 先把网段对上。
#    刷之前板子答 192.168.9.1；**刷完本项目的镜像后也答 192.168.9.1**，
#    由只含 eth0 一个端口的 br-lan 承载。没有任何接口带 192.168.9.2——
#    那是旧社区镜像的拓扑（lan = br-lan over eth1..eth3，mainline 无驱动；
#    eth0 = wan @ .2）。
#    ssh 可能仍然不通：dropbear 被绑在 'lan' 接口上，要靠 reachcheck 解开。
sh tools/backwatch.sh           # 看网卡有没有 carrier

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
sh tools/check-imagelineage.sh               # 应当三层全过；无参数即可
sh staging/diag-20261014/regress_round_backup.sh   # 应当 41/41 rc_all=0
```

**两个模式都要跑，而且要钉同一个 run。** `flash` 模式只更新 flash 那半，
同目录里那半 trial 文件**原封不动**——2026-10-16 实测踩到过：

| `staging/trial/` | 大小 | `phy-mode` |
|---|---|---|
| `flash-dtb.bin`（刚落地，run 33） | 12,009 B | `sgmii` |
| `trial-dtb.bin`（**没动**，run 27） | 12,013 B | **`2500base-x`** |

于是**照原来那一条命令走完，再从同一个目录做 RAM 启动，会把 `2500base-x` 放回 eth0**，
也就是实测「carrier 亮着一个帧都不过」的那个配置，而一路上没有任何提示。

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

