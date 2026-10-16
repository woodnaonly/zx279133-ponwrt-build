# tests/：本地排演套件（**不接进 CI**）

这 107 个文件是 `tools/` 与 `overlays/` 行为的离线回归：一个 runner
（`regress_round_backup.sh`）、**42 个套件**、25 个被套件调用的助手脚本、
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

补上夹具再跑，是 **42/42/42、rc_all=0**（这一条也在本机上实跑过，见下）。

三条里前两条把缺的文件名写出来了，第三条没有——它只说「拒绝时没有给出指令」，
读的人得自己回头看才知道根因是夹具不在。**这是这套件该补的短板，不是可以一笔带过的噪音。**

```sh
sh tests/round36-make-fixture.sh      # 拉那个 4 MiB 的 jffs2 和它的两个同伴，并按钉子校验
sh tests/regress_round_backup.sh     # 之后：42/42/42，rc_all=0
```

一个不会正确失败的测试，也无法正确地通过——所以这里宁可让它红，也不给它一个
「缺了就跳过」的绿色。

## 它跑不了一个纯 clone 里。这也是实测数字

| 分类 | 数量 |
|---|---|
| 引用真实构建产物 `staging/{rollback,trial,ctl}` | 25 / 42 |
| 硬编码 `cd /mnt/c/ugit/SK-D840N4` | 38 / 42 |
| 两者都有 | 23 |
| **两者都没有（自包含）** | **2 / 42** |

2026-10-16 重数过一遍（方法：对 runner 循环里的每个脚本分别 grep 模式
`staging/(trial|ctl|rollback)` 和 `cd /mnt/c`）。自包含的那两个是
`test_readme_refs.sh` 和 `rehearse_zrec_gate.sh`——后者是第一个既不引用任何真实产物、
也不硬编码路径的排演。它造的不再是「合成包归档」：`.apk` 是 `ADBd` 容器，runner 上没有
任何工具能读（见那个 gate 步骤自己的注释），所以它造的是**打包器被喂的那棵树**
（`build_dir/target-*/zrec-*/ipkg-*/zrec`）加上打包器写进树里的
`lib/apk/packages/zrec*.list`，十二种形状，含一个用 chmod 的另一个方向造出来的
exec 位缺陷。**所以「0 / 40」当时是对的，现在不是了。**

25 个引用产物的套件里，**0 个**写了产物缺失的处理。唯一在沙箱里实测过的
（`test_payload_id.sh`，因为只有它不硬编码路径，能安全地放进一个没有产物的目录树），
产物缺失时是**响亮地红**：exit 1，`FAIL plain run exited 1`。

样本只有 **n=1**，其余 23 个没测——它们会 `cd` 回真实工作树，而其中有套件会写
`staging/ctl/uimage-payload-id.txt`（`tools/payload-id.sh --record` 就是），
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

**钉子自己不会被重新下载**——它是仓库里跟踪着的那一份，而实测那个 release 在这个
tag 下**根本没有** `sha256sums-release`：三级全失败。更糟的是脚本原本会先把已存在的
文件改名成 `.partial` 再去抓，等于把自己唯一的依据弄没了。现在它在钉子已存在时跳过，
并在最后用 `sha256sum -c` 校验（实测输出 `flash-rootfs.jffs2: OK`），
校验不过就拒绝把这套夹具交给任何测试。

该脚本有 direct → `ghproxy.net` → `ghfast.top` 三级回退。实测本会话里 WSL 直连
`github.com:443` 被拒（`example.com` 却 200），而两个代理都在 2 秒内返回 200——
所以这条路是通的，只是要走代理。

抓下来的文件被 `.gitignore` 挡在版本控制之外（`tests/fixtures/stale-set/*`，
只放行那 85 字节），免得在跑过一次抓取的机器上 `git add tests/` 就把 4 MiB
发布件提交进去。

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

## `test_readme_refs.sh`：这份 README 不能点名不存在的文件

它遍历 README，把每个裸 `*.sh` / `*.py` 名字取出来，要求文件就在同目录；
带目录的（`tools/…`、`scripts/…`）跳过，那些归仓库管。它自己也带反向对照，
用一个确定不存在的名字跑同一段循环，确认那段循环会红。

加它是因为真出过事：这份 README 的**第一条指令**
`sh tests/round36-make-fixture.sh`，那个脚本根本没随套件交付——
它在工作副本里，但不在「从 runner 循环算出的依赖闭包」里，因为**只有 README 引用它，
没有任何套件引用它**。README 是唯一的引用者，于是闭包漏了它。

所以这条检查的原则是：**文档点名的文件必须存在，这件事要有人替它盯着**，
而不是靠「我刚才是照着做的」。
