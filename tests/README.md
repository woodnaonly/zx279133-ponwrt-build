# tests/：本地排演套件（**不接进 CI**）

这 104 个文件是 `tools/` 与 `overlays/` 行为的离线回归：一个 runner
（`regress_round_backup.sh`）、39 个套件、25 个被套件调用的助手脚本、
`kernelsrc/`（37 个内核源码副本）和一个夹具校验清单。

每个 `.sh` / `.py` 的文件头都写了同一句话，不是重复，是刻意的——
打开 `test_payload_id.sh` 的人不会先读 README。

## 按交付状态直接跑：rc_all=1，三个套件红

这是实测，不是估计。交付副本里没有那个 4 MiB 的夹具，于是：

| 套件 | 退出 | 它说的最后一句 |
|---|---|---|
| `test_imagelineage.sh` | 1 | `FAIL ./fixtures/stale-set/flash-rootfs.jffs2 missing - this suite cannot prove the refusal` |
| `test_imagelineage2.sh` | 1 | `FAIL ./fixtures/stale-set/flash-rootfs.jffs2 missing - cannot prove the refusal` |
| `check_preflight_9c.sh` | 1 | `FAIL refused without an instruction` |

补上夹具再跑，工作副本上是 **39/39/39、rc_all=0**。

三条里前两条把缺的文件名写出来了，第三条没有——它只说「拒绝时没有给出指令」，
读的人得自己回头看才知道根因是夹具不在。**这是这套件该补的短板，不是可以一笔带过的噪音。**

```sh
sh tests/round36-make-fixture.sh      # 拉那个 4 MiB 的 jffs2 和它的两个同伴
sh tests/regress_round_backup.sh     # 之后：rc_all=0
```

一个不会正确失败的测试，也无法正确地通过——所以这里宁可让它红，也不给它一个
「缺了就跳过」的绿色。

## 它跑不了一个纯 clone 里。这也是实测数字

| 分类 | 数量 |
|---|---|
| 引用真实构建产物 `staging/{rollback,trial,ctl}` | 24 / 39 |
| 硬编码 `cd /mnt/c/ugit/SK-D840N4` | 38 / 39 |
| 两者都有 | 23 |
| **两者都没有（自包含）** | **0 / 39** |

24 个引用产物的套件里，**0 个**写了产物缺失的处理。唯一在沙箱里实测过的
（`test_payload_id.sh`，因为只有它不硬编码路径，能安全地放进一个没有产物的目录树），
产物缺失时是**响亮地红**：exit 1，`FAIL plain run exited 1`。

样本只有 **n=1**，其余 23 个没测——它们会 `cd` 回真实工作树，而其中有套件会写
`staging/ctl/uimage-payload-id.txt`（`payload-id.sh --record` 就是），
在真产物上做实验不安全。这个 n=1 不能推广成「39 个都不会静默变绿」。

## 为什么没有接进 CI

三条，都是结论而不是借口：

1. **在没有产物的地方它只会红。** 接进 CI 等于加一道必然失败的检查，
   而必然失败的检查教人忽略红色。
2. **`build-sk-d840n.yml` 有 `paths` 过滤器**——`overlays/**`、`kernel-overlays/**`、
   `configs/sk-d840n*`、`scripts/configure-sk-d840n.sh`、`templates/**`，
   以及 workflow 文件自身。`tests/**` 不在其中。想让它跑就得改 workflow，那是另一件事。
3. 已经定过：**收敛，不再加新门禁**。

## 它跑起来需要什么

前提是工作区根下有 `staging/{rollback,trial,ctl}` 里的真实产物——
也就是从一次 CI 产出的 flash set 拉下来的那一套。

```sh
sh <repo>/tests/regress_round_backup.sh
```

runner 自己算 `d=$(dirname "$0")` 再定位套件，所以在 `tests/` 里直接跑、
或者从别处调用都可以；它随后 `cd` 到硬编码的工作区根。

## 那个夹具是下载来的，不是生成的

`round36-make-fixture.sh` 从 release `skd840n_flash_21_38243906799` 拉三个文件；
库里的 `sha256sums-release`（85 B）钉住 jffs2 的 sha256。
2026-10-16 实测该 asset 仍然存在（302 → 200）。

该脚本有 direct → `ghproxy.net` → `ghfast.top` 三级回退。能不能走通取决于当时的网络：
本会话里 WSL 直连 `github.com:443` 是被拒绝的（`example.com` 却 200）。

## 为什么 `kernelsrc/` 在库里，而 4 MiB 那个不在

两者都是「本机才有的东西」，处理相反，理由是**可复现性不同**：

* `flash-rootfs.jffs2` 被一个 **sha256** 钉在某个**已发布的 tag** 上，重取能拿到同样的字节。
* `kernelsrc/` 是从 `cnjn/linux-mainline-zte-zxslc-sr1010` 的分支
  `codex/sr1010-mainline` 取的，**没有钉 commit**（见 `fetch_kernel_kconfig.py`、
  `fetch_phy_kconfig.py`、`fetch_kernel_files2.py` 的 `REPO` / `BR`）。
  同一个脚本今天和下个月取到的可能不是同一份。561 KB 文本，换一个不会被时间悄悄改写的依据。

有 4 个套件读 `kernelsrc/`：`test_phy_gate_symbols.sh`、`test_pon_datapath.sh`、
`test_pon_protocol_absence.sh`、`test_release_body_claims.sh`。
其中 `test_phy_gate_symbols.sh` 在文件缺失时会红，并且直接告诉你跑哪个抓取脚本——
这是这套件里处理缺失最规矩的一个，可以当作范本。
