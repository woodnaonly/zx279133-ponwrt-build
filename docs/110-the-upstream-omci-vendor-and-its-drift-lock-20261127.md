# 110 — 把上游 OMCI 引擎钉住：锁文件、漂移门禁，以及 CTC 私有 OAM 会话为什么进得来却开不了

> 本文承接 `docs/100-sk-d840n-onu-management-port.md`（ONU 管理口与 OMCI 引擎的来源）。
> 本文记录的是**另一件事**：我们从 `pbs05/openwrt-pon-userspace` 取的那一小块 OMCI
> 引擎，是怎么被钉住的；上游继续演进时，我们怎么知道它漂移了；以及上游新加的
> **CTC 私有 OAM 会话**在这块板子上为什么「进得来、开不了」。

---

## 判决

1. **我们从上游取的只有 9 个文件，而且上游在这块上没动过。** 取的是 OMCI 引擎的 6 个
   文件（钉在 `cce9d756`）和 CTC 私有 OAM 会话的 3 个文件（取自 `a903fcc80f`）。
   逐 blob 核对：上游 main 比钉点多 3 个提交，这 3 个提交只碰 `Makefile` 与
   `src/src/oam/{config,ctc,mod}.rs` —— 我们 vendor 的文件，上游一个字节都没动。
2. **上游真正新增的能力是 CTC 私有 OAM 会话，但它的载体我们没有。** 它跑在
   **IEEE 802.3ah 以太网 OAMPDU**（EtherType `0x8809`、subtype `0x03`）上，上游
   只在 **EPON 模式**启用。我们是 **XG-PON**：走 PLOAM + OMCI，内核侧没有「向上行
   OLT 发以太网帧」的通道（OMCI 网卡 MTU 固定 48，`ndo_start_xmit` 直接丢包）。
   ⇒ 会话**入库但默认关闭**：代码在树里、单测跑着、没有任何东西把它打开。
3. **「8 字节 ONU 序列号」从来不是缺口。** 我们早就通过 PLOAM SERIAL_NUMBER 有了
   （`ZXPON_SN_LEN 8`、UCI `option serial_number`、内核 `ZXPON_A_SN` EXACT_LEN 8）。
   这轮补的是上游那条**取值规则**：显式序列号优先，否则 `vendor_id` + 本端 MAC 后 4 字节。
4. **真正的缺口是「上游改了，而我们不知道」。** 这才是锁文件 + 离线门禁要解决的事。

---

## 1. 我们到底取了什么

| 我们的文件 | 上游路径 | 与上游相同？ |
|---|---|---|
| `omci/src/{mib,protocol,provisioning,schema,security}.rs` | `airoha-pon-daemons/src/src/omci/…` | 4 个带我们自己的改动，`schema.rs` 逐字节相同 |
| `omci/src/wire.rs` | `airoha-pon-daemons/src/src/wire.rs` | 逐字节相同 |
| `omci/src/oam/{ctc,frame,ieee}.rs` | `airoha-pon-daemons/src/src/oam/…` | 三个都逐字节相同 |
| `omci/src/{config,lib}.rs`、`omci/src/oam/{config,mod}.rs` | —— | 我们自己的 |

我们自己的改动，合计约 +432/-140 行：TCONT 预算 16→31、GEM 端口 256→248；
`enhanced_security_enabled` / `reset_security_session` / `loid_auth_status` /
`update_queue_bindings`；GRAPH_CLASSES 10→13；OMCI 档位带 dscp、p-bit、VLAN 选择器；
`aes_cmac` 改成带 key（密钥来自 UCI 的 `omci_psk`）。

上游侧**没有取**的部分和理由，都在锁文件的 `not_taken` 里，每条带一个不会变的 id：

- `oam-runtime`：上游 `oam/mod.rs` 带着它自己的 daemon 运行时（raw socket 收帧、状态
  记录、控制套接字）—— 那是别人的 daemon，本板只取会话本身。
- `daemon-runtime`：`{agent,runtime,transport,control}.rs`，同上。
- `omci-extras`：上游 `omci/{backend,config,control,mod}.rs` 绑定它的 daemon。
- `frontends`：`airoha-ponctl`、`luci-app-pon`、`luci-app-iptv`（本板 LuCI 来自
  LeoooChen/PonWRT-TYFZA1）。

---

## 2. 钉住：`UPSTREAM.lock.json`

锁文件在 `overlays/package/libs/libzxpon-omci/omci/UPSTREAM.lock.json`，记录的是
**一次决定**，不是一次状态：

- `repository` 与 `pinned_commit`：上游仓库与取用时的提交。
- `refreshed_at_commit`：上次与上游比对到的提交。
- `files[]`：每个 vendor 文件的**上游路径**、**上游 blob id**、**本地 blob id**、
  **本地 SHA256**，以及 `local_changes` —— 我们改了什么、为什么。
- `local_sources[]`：我们自己写的文件（同样记 blob id 与 SHA256）。
- `not_taken[]`：上游有而我们没取的部分，每条带 id 与理由。
- `drift[]`：上次比对时发现的上游差异（空 = 没漂移）。

`UPSTREAM` 注记里新增了一段 `Paths:` 块，逐行列出取用的上游路径。锁文件与这段
列表**必须一致** —— 两处各说各话，就是「我们到底取了什么」这个问题有两个答案。

## 3. 漂移：刷新脚本、离线门禁、反控

`tools/refresh-omci-upstream.py`（联网）：读上游 head，逐个文件比 blob id，报告
差异；**只有 `--apply` 才写锁文件**，且只写 `refreshed_at_commit` 与 `drift[]`，
**绝不改 vendor 源码** —— 取新的上游文件是人的决定，不是刷新的动作。

`tools/check-omci-upstream-lock.py`（离线）：门禁作业在两条线上都跑它，它必须
给出**每次一样的答案**，所以它不联网。它拒绝：

- vendor 文件的字节与锁里记的不一样（本地被偷改）；
- 树里有锁没说的文件，或锁里说的文件不在树里（**覆盖关系**）；
- `UPSTREAM` 的路径清单与锁文件不一致；
- `not_taken` 少了该有的 id，或某条没有理由；
- 某处改动在锁里记着，却没写清改了什么；
- 锁里记着与上游的漂移（`drift[]` 非空）。

`tests/test_sk_omci_upstream_lock.py`（反控）：把门禁逐条破坏 10 种，逐一证明
门禁会红。其中两种是元门禁：门禁源码里出现网络调用 ⇒ 反控红；以及把这种
调用注入门禁源码后，检查本身确实抓得住。

这一节的门禁在实施过程中**真的抓到了一次**：给 `lib.rs` 加了一行 `pub mod oam;`
之后没改锁文件，门禁立刻报「vendor 的 `src/lib.rs` 与锁里记的字节不符」。

## 4. 序列号：上游的规则怎么落到两侧

上游的规则（`OamConfig::onu_serial()`）：**有显式序列号就用它；否则
`vendor_id`（4 字节）+ 本端 MAC 的后 4 字节**。

C 侧落在 `overlays/package/utils/zxpond/src/zxpon-config.c`：新增 UCI
`option vendor_id`（4 字节，默认 `OWRT`，与上游默认一致）与 `option mac_address`。
配置里没有序列号、而有 MAC 时，按上面那条规则推导，**PLOAM SERIAL_NUMBER 与将来
的 CTC leaf 用的是同一份值**。两样都没有时，**不给**八个 0 去装作有序列号。

Rust 侧落在 `omci/src/oam/config.rs`：`OamConfig::from_identity()` 把板子带的
序列号作为 `active_serial_number` 交进去，于是 CTC leaf 与 PLOAM 携带同样的字节。

两侧的**同一条规则**用**同一份向量文件**交叉断言：
`tests/fixtures/onu-serial-vectors.txt`（vendor、MAC、期望序列号三列）。C 侧在
`tests/sk_pon_config.c` 读它，Rust 侧在 `oam/config.rs` 的单测里读它。规则要是
在某一侧漂了，同一组字节会给出两个答案。

## 5. CTC 私有 OAM 会话：进得来，开不了

会话本身（`ctc.rs`）逐字节取自上游，`frame.rs`（802.3ah OAMPDU 编解码）与
`ieee.rs`（Information TLV 会话）同样。适配只有两处，都在 `omci/src/oam/` 里：

- `config.rs`：上游那份读的是它自己 daemon 的配置解析器（`Section`），我们没有那个
  解析器；改成用板子自己的身份配置构造。另外上游默认 `operator = "ctc"`，我们改成
  `ieee` —— **CTC 会话默认关闭**，要打开得有理由。
- `mod.rs`：上游的模块入口带着它的 daemon 运行时，我们只挂会话本身。

为什么默认关闭：会话要靠以太网 OAMPDU 收发帧，而这块板子没有这条路。真把它打开，
daemon 只会去绑一个永远收不到帧的套接字。所以**代码在、测试跑、开关关着** ——
将来若有了这条通道，会话是跟着来的，而不是重写一遍。

## 6. 明确不做：内核侧的以太网 OAM 通道

要让会话真正通，得在内核侧新增一条「向上行 OLT 发以太网帧」的通道：一个
MTU≥64 的 netdev，加上 PON MAC 的 GEM/TCONT 上行打包。这一轮**不做**：

- 上游自己只在 EPON 模式用这套，我们是 XG-PON；
- 板子**无光、无 OLT**，端到端无法验证，最多只能证明「帧发出去了」，证不了
  「局端认了」；
- 风险高、收益不可证，而它是**新功能**，不是「同步上游」。

写在这里，是为了让后来的人知道它是被想过的，不是被忘掉的。

## 7. 验证

本地跑得起来的：

- `tools/check-omci-upstream-lock.py --tree <repo>`：绿；
- `tests/test_sk_omci_upstream_lock.py --tree <repo>`：13 项，反向验证逐条红过；
- `tests/test_sk_workflows.py`、`tools/check-sk-workflow-paths.py`：绿（302+ 项）；
- `tests/test_sk_text_hygiene.py`：绿；`tools/prepush.py`：绿；
- Rust 侧 `cargo test --locked`：25 项通过（含上游自带的 CTC 单测、跨实现交叉断言）；
- `cargo check --tests --target aarch64-unknown-linux-musl`：通过；
- 模拟 `Makefile` 的 `Build/Prepare`（`cp -fpR omci/src/*`）后目录结构正确 —— `oam/`
  目录随 `src/*` 一起进包。

本地跑不了的（CI 上成立）：

- `cargo build --release --locked --target aarch64-unknown-linux-musl`（本机 GNU 链接器
  缺 `libgcc`，与代码无关）；
- `tests/test_sk_pon_host.sh` 的完整 POSIX 用例（本机 MinGW 无 `lstat`/`symlink`/
  `fchmod`）；
- 两条线 gates 作业与整条构建。
