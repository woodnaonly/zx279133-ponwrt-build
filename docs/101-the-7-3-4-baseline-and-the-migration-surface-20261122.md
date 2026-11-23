# 101 — 把 SK-D840N 挪到 Linux 7.3.4：逆向结果、基线验证与升级通道

> 本文承接 `docs/100-the-other-build-npu-driver-and-the-7-3-upgrade-20261122.md`。
> 100 号记录的是「别的人固件」的拆解与驱动定位，本文记录的是**据此动手改仓库**的结果：
> 7.3.4 基线是否成立、我们自己的驱动增量到底有多大、升级通道怎么接、以及哪些东西
> 这一轮**没有**搬过去。

---

## 一、判决

1. **7.3.4 这条基线是成立的，而且已经在本机验证过。** 原生 `linux-7.3.4.tar.xz`
   的 sha256 与 kernel.org 官方清单逐字一致；cnjn 的 39 个 `patches-7.3` 补丁
   在这棵 vanilla 树上 **39/39 全部干净 apply**，无一需要 fuzz 或 reject。
   证据见第三节。
2. **升级不是「换个内核版本号」，是换一整套驱动架构。** 6.18 世代是拆分成
   `np/mac/netdev/main/tm/idm` 的老结构；7.3 世代被重写成**一个组合模块
   `npu.o`**（`main+tm+np+idm+mac+netdev+offload-api+ptp+host+native+meter+qos`）。
   目标固件里的 `npu.ko` 正是这个结构。
3. **我们自己的增量有 171 个函数、约 7900 行，是这次升级的主要成本。**
   按文件分：**9132 相关的 73 个**（`zx279133-np.c` 30、`zx279133-switch.c` 39、
   `zx279133-lan1-led.h` 4）、**PON/OMCI 栈 56 个**（`zxpon.c` 37、
   `zx279133-pon.c` 17、`zx279133-omci.c` 2）、**RGMII 调试 8 个**、
   **其余健壮性修正 34 个**（netdev 12、main 10、idm 6、mac 6）。
   7.3 公版里**一个都没有对应物**——因为 cnjn 公开的 7.3 树**没有 PON 模块**。
   这 171 个函数**逐条都有归属**，账本见 3.5 与八之七。
4. **CI 走「外部树」模式，与已验证的 6.18 线同构。** cnjn 自己的 zte target 在
   master 上明确禁止 `CONFIG_EXTERNAL_KERNEL_TREE`（它要 OpenWrt 自己下载底包并打
   `patches-7.3`）；我们反过来：自己准备「vanilla 7.3.4 + 39 补丁」，再把树交给
   OpenWrt。好处是内核字节与补丁字节都被本仓库钉死、可离线复核。
5. **驱动增量已按片重打五片，第 6 片是实机对拍。** 顺序是
   IDM（1~2 片）→ 9132 交换表驱动（3）→ PON/OMCI 栈（4）→ **9132 芯片本身的编程（5）**。
   每片一个补丁、每片一扇编译前的门禁、每片一次 CI。
   **这五片的代码在本机已全部通过 clang 的语法与符号检查**
   （现在是仓库的工具 `tools/kcheck.py`，见 7.17），但**还没有被真正编译过**
   （本机无 arm64 交叉工具链），所以下一步是
   CI 结论与实机对拍。
   剩下的两项（RGMII 调试、TX 故障隔离/恢复）已各自判明，见八之五.9。
6. **7.3 线 run #1~#13 的 Compile 失败与内核无关**：失败的是我们自己的 Rust 包
   `libzxpon-omci`，因为这条线少了装 Rust 工具链的步骤（6.18 线有）。已修，见 7.11。
7. **7.3 线即使编译过了，镜像里也不会有 NPU**：cnjn 的 `config-7.3` 把驱动定成
   模块，而本仓库没有加载它们的机制；`pin_y` 也救不了，因为 kconfig 会把
   「依赖是模块」的 tristate 夹成 `=m`。已修（四个符号钉成 `=y`），见 7.12。
8. **7.3 线此前只查了 4 个驱动符号，没有 6.18 线那道「读合并配置、断言板子全部
   要求」的门禁**：一份丢了 Realtek PHY、丢了整族 netfilter、或者带着 SR1010 的
   `mtdblock7` 内核命令行的配置，照样能编出一个「构建成功但不好用」的镜像，
   而且每一种失败在板子上都长得像「内核起不来」。已补上，并先在本机用
   kconfig 自己的三值依赖逻辑证明了那 39 个符号在这棵 7.3 树上**不会被夹成
   `=m`**。见 7.14。
9. **7.3 线此前也没有「dtb 的每个 compatible 都有驱动」这道门禁**：一个没有驱动
   的 compatible 会被内核**静默跳过**，板子照常起来，只是没有那个口。
   这道门禁不能照抄 6.18 的版本（会因 RGMII 那个节点变红），
   所以重写成了 `tools/check-sk-dtb-compat.py`，而且问的是更强的问题：
   **compatible 字符串在不在已编译的内核镜像里**——所以它同时是
   「驱动有没有退回成模块」的探测器。见 7.15。
10. **7.3 线卡在 libbpf 上的原因查到了，而且只在 7.3 线成立。**
    7.3.4 的 arm64 `asm/ptrace.h` 用 `__u128`，而 `__u128` 只定义在
    **被 libbpf 自带那份 `include/linux/types.h` 遮蔽掉**的 uapi `linux/types.h` 里；
    6.18.56 的同一批行（96、261-264、268）写的是 `__uint128_t`（编译器内建名），
    **行号一样，只有拼写不同**。修法是一个只落 `overlays-73/` 的补丁，
    本机两道门禁（最小 + 整包 22 个 `.c`）全绿，9 处突变的反向验证全绿。
    **但这一轮 CI 的结果还没出**，见 7.21。
11. **7.3 线的诊断 pass 够不到内核。** 它只认
    `ERROR: package/<path> failed to build.`，而内核编不过时报的是
    `ERROR: target/linux failed to build.` —— 内核不是包，它由
    `target/linux/compile` 编。⇒ 内核失败时诊断 pass 直接落到整树兜底，
    而整树兜底**到不了内核**（6.18 线 run 88/89/91/92 的失败）。
    已加内核分支（内核优先于包），三条断言 + 三处突变反向验证。
    **五片驱动补丁从未被真正的编译器编过，内核是最可能的下一个失败面**，
    见 7.22。
12. **7.3 线导出 dtb 时不查分区表。** 6.18 线有这道门禁，7.3 线没有，
    而 7.3 线的镜像正是接下来要刷进板子的那一件。分区表决定 root 在哪，
    表差一个擦除块就是「panic 且串口没输出」——
    本机 `staging/trial/` 里那个 dtb 恰好就是旧布局（`current240`），
    刷错的后果不用猜。已补两道（编出来的 blob + 要刷进去的件），
    两条断言 + 两处突变反向验证。见 7.23。
13. **7.21 里关于 `__attribute_const__` 的结论被推翻了。**
    初稿说「遮蔽还让 `__attribute_const__` 没了，不补它报错只是往后挪一处」——
    **错在拿「导出前」的头推「导出后」的编译**。内核导出每个 uapi 头时，
    `scripts/headers_install.sh:34` 的 sed **就把这个宏剥掉了**，
    所以 libbpf 真正编译的那批头里**本来就没有它**；
    CI 的注解是旁证：**6 条 `__u128`，0 条 `__attribute_const__`**。
    ⇒ 补丁里那一行降级为**防御性定义**（`#ifndef` 守卫），**不补它不会失败**；
    顺带解开了 6.18 侧「为什么没有这一类错」的悬案：**同一个机制**，
    6.18 线**整份补丁都不需要**。见 7.24。
14. **实机刷机路径上的分区表门禁，工装区落后于仓库。**
    工作区根目录的 `tools/` 才是实机真正会跑的那套脚本，而它的
    `dtbparts.py` 是旧版（没有 `layout=`）、`sk_layout.py` 不存在；
    `flash-round.sh`/`preflight.sh`/`lan1roundcheck.sh`/`gettrial.sh`
    问的是 **240 MiB 的旧布局**，`mkrollback.sh` 问的却是 **241 MiB** ——
    同一块板子两套门禁，一套放行旧件、一套拒绝旧件。
    ⇒ 7.23 在 CI 里补的那道门禁**管不到实机**；而 7.3 的新 dtb 又是
    native241，工装区的旧参数会**拒绝正确的件**并报「大小不对」。
    已**定向**同步（不整包覆盖：`gettrial.sh` 工装区有 81 行独有）。
    见 7.25。
15. **7.3 线的面板 LED 拓扑没有门禁，而 LOS 灯正是实机对拍要看的那盏。**
    6.18 发布线有（`build-sk-d840n.yml:663`），7.3 线编出了板级 dtb 却只查了
    分区表。dtb 里 GPIO 号 / 极性 / 触发器 / 占用关系改错了，**7.3 线不会拦** ——
    而灯不亮或灯亮错**看起来像板子没光**，板子其实有光。
    已补三样：门禁本体、门禁自己的负控、真合并配置里的 7 个 LED 符号。
    这不是配置问题（那些符号早就被 pin 成 =y），是**没人查编出来的 dtb**。
    见 7.26。
16. **「conduit 网口叫 `lan-cpu%d`」这个结论是错的，已经推翻。**
    上一轮的推断是：`strscpy` 是有界拷贝不是格式化器 ⇒ 网口在运行期就叫
    字面量 `lan-cpu%d` ⇒ 实机对拍读 `lan-cpu0` 会读不到。
    **错在只看了拷贝那一半，没看注册那一半**：`register_netdevice()` 会
    调用 `dev_get_valid_name()`，它看到名字里有 `%` 就交给
    `__dev_alloc_name()`，那里把 `%d` 解析成第一个空闲编号
    ⇒ **发布名就是 `lan-cpu0`**。
    实机已经证明：6.18 线跑着的固件用的是**同一行**代码，板子上就是
    `lan-cpu0`。⇒ **不需要改内核**（`kernel-patches/7.3-local/` 一行不加），
    但**需要一道门禁**：格式串一改，发布名就会变，或者驱动直接注册失败、
    板子上根本没有 conduit，而对拍读的是**固定名字**，读不到会**静默地**读出空值。
    见 7.27。
17. **7.3 线第一次真的把镜像编出来了。**
    API 全表：这条线 39 轮里 **#1~#35 无一成功**（26 轮 failure、9 轮 cancelled），
    **#36 是第一次全绿**（三个 job 全 success，1 h 21 m 51 s），
    #37 同样全绿。⇒ 7.21 的 libbpf 修复是对的，它就是那最后一道门。
    产物在本机**自证一致**：`sha256sums` 六项全对、dtb 是 **native241**
    （root 在 mtd5、别名钉死）、inflate 出来的 Image 是 14,051,336 B，
    与 `sysupgrade-report.json` 的 `raw_bytes` 逐字节吻合，uImage 名字就是
    `ARM64 ImmortalWrt Linux-7.3.4`。
    ⇒ **但板子还没跑过 7.3**：`staging/ctl/flash.permit` 里没有这对字节的凭证，
    `tools/gatecheck.sh` 不放行；而且它对 `gate-*` 凭证只认 `kver=6.*`，
    **7.3 得先跑一轮 RAM 门禁（不写 flash）**，闸门还得为 7.3 放开一档。
    见 7.28。
18. **刷机闸门的版本判据改成了「从待刷镜像自己读」。**
    旧写法只认 `kver=6.*` ⇒ 7.3 的门禁凭证（`kver=7.3.4`）**永远过不了**，
    而那条判据的本意是「必须是**正在刷的这一个内核**」，不是「必须是 6.x」。
    现在期望版本从**待刷那个 uImage 的 banner** 里读出来，再与凭证**逐字符**比对：
    6.18 配 6.18 放行、7.3 配 7.3 放行、**6.18 配 7.3 拒绝**、
    厂商 4.19 一律拒绝、**读不出版本就拒绝**（fail closed）。
    ⇒ 比写死系列**更严**，两条线都不用改代码。
    顺带修掉一个真缺陷：GNU `md5sum` 遇到含反斜杠的文件名会输出转义形式，
    于是许可行永远匹配不上、闸门对**任何**字节对都拒绝。
    见 7.29。
19. **门禁轮的 payload 里也烤死了 `6.*`，7.3 那轮会一声不吭。**
    `tools/mkctlram.py` 生成的 announce payload 写着
    `case "$(uname -r)" in 6.*) PAYLOAD=1` —— 只有 6.x 内核才武装 RAM mailbox。
    而按 payload 自己的注释，**RAM mailbox 是 mainline 唯一的报告通道**
    （mainline 没有 lan bridge，TCP 推不出去；pstore 那条只带注册过的 pstore 写过什么）。
    ⇒ **mailbox 不武装 ⇒ 那一轮什么也收不到 ⇒ 没有凭证 ⇒ 7.3 刷不进去。**
    现在 payload 的版本从**待刷镜像**推导（`--kver` 默认读
    `staging/trial/flash-uImage` 的 banner），烤成 `7.3.4*) PAYLOAD=1`；
    厂商 4.19 仍然不匹配，真正的安全网（保留窗口必须在 `/proc/iomem` 里）一个字没动。
    见 7.30。
20. **第 3/4/5 片的代码已经在 CI 产出的 7.3 镜像里了，而且现在有门禁管着。**
    第九节原来写这三片「未编译验证——等 CI 的 Compile」，那是 run #36 之前的话；
    现在是**实测结论**：run #39 的 `Compile` 用了 **46 分钟**并成功，
    紧接着四道门禁全绿（内建、合并配置、dtb compatible、面板 LED）。
    更硬的一层是**字节级**：把 7.3 那个 uImage inflate 之后，
    三片各自的特征串**都**在里面 —— switch 驱动的 of_match 名、
    9132 内部 PHY 的注册名、面板 LED 的 cdev 名、PON netdev 的名字、
    OMCI 传输计数的 sysfs 属性名、9132 芯片编程的每口 PHY 打印串、
    PPU 字名表、conduit 的格式串。
    但**「在树里」「能编」「配成内建」「在要刷的镜像里」是四句话**，
    前三句都有门禁管，**第四句没有** ⇒ 新增一道门禁，**直接问产物**：
    每个探针同时要求「这个串还在它声称的源文件里」与「这个串在 inflate 后的内核里」，
    两半分开判，少一半都转红。
    顺带查明：**bench 的硬件链路早就断了**（CH340 不在枚举里、网口无载波、
    本机有个 TUN 代理在应答 `192.168.9.0/24` 的 ARP），所以第 6 片推不动
    **不是代码问题**。见 7.31。
21. **「把内核版本写进判断」这个类别，在整条 PC 侧链上还有八处。**
    7.29 与 7.30 修掉了两处（闸门与 payload），本轮把它**整个类别**查完并堵上：
    新写的 lint（`tools/check-baked-version.py`）扫出**八处**，
    其中**三处会让 7.3 直接失败**：
    `tools/preflash.py`（刷机前门禁，硬编码 `6.18.38`）、
    live 的 `tools/flashverify.sh`（刷后验收，硬编码 `Linux version 6\.18\.38`）、
    live 的 `tools/round.sh` 与 `tools/onewindow.sh`
    （RAM 轮判据「是不是 mainline 内核」写死 `6.18*` ⇒
    板子跑着 7.3 会被判成「不是 mainline 内核」）。
    三处都改成**从待刷镜像自己读**（或从**这份出货报告自己说**——
    出货报告现在带 `kernel.version` 了）。
    剩下的五处全是**厂商内核 4.19**：那是**板子上的固定事实**，
    烧死在厂商 flash 里，我们改不动，所以进允许清单并写明理由。
    见 7.32。
22. **发布线上「版本从待刷镜像读」这条链一直是空的；而上一轮改它的时候把发布线弄红了。**
    6.18 的 #394 在「Prepare firmware tools and upgrade regression tests」这一步就红了，
    真因是**上一轮（`ecefbd0f`）自己加的**：`tools/check_sysupgrade.py` 的
    `inspect_kernel()` 改成「从 banner 读版本」之后，把「读不到 banner」变成了**硬失败**，
    而测试夹具造的合成 kernel **本来就没有 banner**。
    顺带查出**更严重**的一件事：**出货报告从来没有落过盘** ——
    6.18 工作流只把 JSON 打进 CI 日志，release 资产里**没有** `sysupgrade-report.json`，
    ⇒ `tools/preflash.py` 的 `release_version()` 永远返回 `None`，
    ⇒ 上一轮改的「期望版本 = 报告里的版本」在发布线上**是死代码**。
    现在：报告落盘并**进 release 资产**；新增一道**独立对照**的门禁
    （报告与镜像各读一遍再比对版本 —— 写报告的工具替自己作证不算证据）；
    `preflash.py` 改成三态（新报告说这镜像没有 banner ⇒ **红灯**；
    旧报告没有这个字段 ⇒ 降级为只做厂商检查并说明）。
    见 7.33。
23. **第 7 片（TX 故障隔离与恢复）落地了；顺带查出上一轮自己加的门禁有一处缺陷。**
    7.3 树的驱动**相信** TX 完成计数器：计数器报的完成数超过环里待发帧数时，
    它把增量**钳位**成待发数然后继续 —— 释放 slot、**卸载 DMA 映射**、
    并把 `done` 设成硬件那个不可信的原始值。
    **不可信的计数器不能证明 DMA 已经交还了缓冲区**；
    释放一个 DMA 可能还持有的缓冲区是 use-after-free。
    6.18 世代不信这个计数器：隔离该环、停掉两个逻辑 TX 设备、
    交给恢复工作（flush + reclaim，必要时**至多一次**重启共享 datapath）。
    `160-sk-tx-fault-isolation.patch` 把这半搬进 7.3 树
    （**旧结论「7.3 树没有这个模型」已被推翻**：树里本来就有
    `datapath_suspend`/`resume` 与 `lan_datapath_get`/`put`，
    缺的只是故障隔离那半）。
    新门禁**按位置**判（36 条）：故障分支里不能有任何释放语句，
    且函数里所有释放都必须在该分支之后 —— 比「某个串在不在」强得多。
    本机用仓库自带的「编译预言机」过了 9 个文件。
    顺带查出：**7.3 #43 的 initramfs job 红了，是我上一轮加的产物门禁** ——
    initramfs 模式**根本不产出内核镜像**（run 注解原话：
    "no kernel image ... the image profile did not make one"），
    而门禁硬要去问那个不存在的镜像（同一个 run 的 flash job 是绿的）。
    已限定在 flash 模式，并给这个限定加了门禁。
    见 7.34。
24. **第 8 片（IDM 的 RX 侧）落地了；同时把 #46 的失败查到了底。**
    两代 IDM 的 RX 侧逐函数对照：ours 独有 10 个、theirs 独有 6 个、
    REWRITE 4 个、similar 11 个。**只搬 6 项**（ethertype 守卫、三处越界读、
    XDP PASS 合法性、`CHECKSUM_NONE`、归还前的整窗设备侧同步）；
    **不搬 4 项**（RX 调试转储、jack 计数、`cpu_dp->rcv()` 直调 tagger、
    `bp_ring_cfg`），其中 jack 计数与直调 tagger **留给第 9 片** ——
    它们要 DSA 拓扑与实机对拍才有意义。
    **一处布局差异不能搬**：两代的 page pool `.offset` 各不相同
    （ours = `PAYLOAD_OFFSET`，theirs = `PAYLOAD_OFFSET + rx_frame_offset`），
    两边 CPU 侧同步**各自自洽**，搬过去会双重偏移。
    `170-sk-rx-delivery-safety.patch`（12173 B，106 插入 / 27 删除）
    + 39 条门禁 + 24 项负控 + 17 处反向验证。
    **顺带把 #46 的失败查到了底**：门禁在 CI 上红了 19 条、说树里没有 160 的符号，
    而 prepare 同时报「160 已应用」—— **两条日志互相矛盾**。
    本机逐条排除九个假设（补丁字节、MANIFEST 登记、两个脚本、板层覆盖、
    日志里有没有 checkout、门禁是不是另一棵树、`git apply` 在仓库内外是否不同、
    Python 版本、解析器版本），最后用**同一个 commit 版门禁**跑「160 之前」的树，
    得到**恰好 25 条检查、19 条红**，与 CI **逐条一致**
    ⇒ **CI 的树就是「160 之前」的状态**：`git apply` 报成功，
    而补丁的内容**没有落到门禁读的那份文件里**。
    **这类事在没有内容级校验时看不见** ⇒ prepare 从此在每个补丁 apply 之后，
    查这个补丁**加进去的那些行**是否真的在它声称写的那个文件里；
    provenance 带**树指纹**；两道门禁把它们读到的文件 sha256 写进
    `GITHUB_STEP_SUMMARY`（job log 匿名下载不到，step summary 谁都能读）。
    见八之八与 7.37。
25. **#46 / #47 / #48 的红查到了底：补丁 160 与 170 从未进入 CI 的树。**
    `git apply` 在**一个仓库里、从子目录**运行时，对**带 `diff --git` 头**的
    补丁按**仓库根**解析路径；内核树是 checkout 里的一个目录，
    这样的路径落在当前子目录之外，git 就把整个补丁文件**默默丢掉** ——
    退出码 0、一个字节都不写、默认什么都不说。
    **带纯 unified diff 头的补丁不受影响**，于是同一批 47 个补丁里
    vendored 的 39 个和 local 的 100~150 落地了，
    只有带 git 头的 `160`（TX 隔离）与 `170`（RX 交付修复）没有。
    git 加 `--verbose` 会自己说出来：`Skipped patch '...'.`
    ⇒ **7.3 线此前编译过的每一个镜像，编的都是少了这两片的驱动**；
    36 条 TX 门禁与 39 条 RX 门禁在 CI 上一次都没绿过。
    修法：apply 放进 git 找不到 checkout 的上下文
    （`GIT_CEILING_DIRECTORIES` = 树的上级目录）、补丁路径相对树给出、
    工作目录就是树；再加一道守卫，git 说 `Skipped patch` 就直接拒绝。
    门禁：`tests/test_sk_prepare_apply_context.py`（12 项）+
    反向验证 5 项；本机用**真的**补丁在**真的** checkout 布局里跑通，
    四个驱动文件的 sha256 与「160 已应用」逐个相同。
    顺带：本文里当时藏着一个 **NUL 字节**，让所有针对本文的 grep 静默返回空
    ⇒ 文本卫生门禁扩到「U+FFFD + NUL + 其它控制字符」。
    **#49 的结果**：prepare 绿、36 条 TX 门禁与 39 条 RX 门禁**第一次同时绿**
    ⇒ 7.3 线此前产出的每一个镜像，编的都是少了这两片的驱动。
    见 7.39 与 7.40。
26. **第 10 片：TX 隔离的四个计数器接到了 `ethtool -S` 上；这是最后一块软件侧的活。**
    第 7 片（补丁 160）在 `struct zx279133_eth` 里留了四个计数器
    （`tx_invalid_completions` / `tx_recovery_requests` /
    `tx_recovery_restarts` / `tx_recovery_failures`），
    递增点在 `zx279133-netdev.c`（`:101`、`:131`、`:1007`、`:1009`），
    **树里没有任何地方读它们**
    ⇒ 一个被隔离过的环和一个从没出过问题的环，在板子上长得一模一样 ——
    而这正是隔离模型要回答的那个问题。
    7.3 驱动**本来就有** `ethtool` 统计表（三张位置表：
    `enum zx279133_ethtool_stat`、`zx279133_gstrings_stats`、
    `zx279133_get_ethtool_stats()` 里的赋值），
    补丁 180 把四个计数器接上去（2995 B，+19 -0，只改 `zx279133-netdev.c`）：
    **加在末尾**，所以已有统计项的索引一个都不动；
    名字与枚举项**同序**，并且**在同一把锁里读**。
    新门禁 `tools/check-sk-tx-isolation-stats.py`（33 条）**按位置**判：
    两张表同长、四个计数器在两张表里是**同一个索引**、赋值在锁内、
    读的是它自己那个计数器、那个计数器确实在被累加。
    负控 `tests/test_sk_tx_isolation_stats.py`（11 项）
    **每一项都必须转红在指定的那条检查上**。
    端到端（`scratch/verify_180_in_checkout.py`，16 项）在**CI 的布局**里验过：
    48 个补丁全落地、四个驱动文件逐个同哈希、拿掉 180 门禁就红。
    **产物级**门禁的探针清单也扩了：加两条问「**要刷进板子的那个像**里
    有没有第 7 片与第 10 片的东西」（`shared TX recovery failed`、
    `tx_invalid_completions`）—— 旧的 7.3 镜像**恰好红在这两条上**，
    其余七条两边都有 ⇒ 这道门禁对新内容有分辨力（见 7.41）。
    见八之十。
27. **第 11 片：失败的数据面**怎么回来** —— 我们自己的 6.18 世代早就有回路，
    7.3 公版没有。**两代都有三个恢复入口，7.3 只剩两个**：
    WAN 的 open 与 LAN 服务的 get 都要求「**已经有第二个使用者**」，
    而**第三个 —— conduit 的 open —— ours 是无条件的，7.3 树里没有**。
    后果是具体的：`datapath_failed` 一旦置上，
    **恢复工作遇到它是直接放弃的**（`:963`），
    `zx279133_xsk_pool_cycle()` 的恢复失败也把它留在那里，
    于是**只开了一个口的板子回不来**。
    补丁 190 把 ours 那一段搬进 7.3 树（3038 B，+25 -2）。
    **顺序是关键，不是照抄**：`datapath_failed` ⇒ 硬件没准备好，
    所以树里那道 `hardware_prepared` 检查排在恢复**前面**的话，
    open 会在任何恢复还没跑到之前就被拒掉 ——
    ⇒ **恢复放前面，硬件检查放后面**。
    而恢复在健康的数据面上是**空操作**（第一句就 `return 0`），
    ⇒ **能跑的板子一点行为都不变**。
    新门禁 `tools/check-sk-datapath-restore.py`（18 条）**按位置**判，
    其中「DSA 在用户口 up 时打开 conduit」查的是 `dsa_user_open()` **函数体内**
    —— 树里不止一处打开 conduit，查整个文件会放过错误的那一处。
    负控 `tests/test_sk_datapath_restore.py`（9 项）。
    **⇒ 软件侧到此为止**：剩下的全是**实机对拍**。见八之十一。
28. **两条线的 `paths:` 都有漏洞，而且不只漏了门禁。**
    `paths:` 说的是「哪些改动会起一轮构建」。**工作流会跑、但没被列进去的文件**，
    改了就**不会**起构建 ⇒ **读它的门禁从那一刻起就不再被验证**，
    而仓库看上去一切正常。
    这不是新问题：`tests/test_sk_workflows.py`（门禁中的门禁）此前就不在 7.3 线的
    `paths:` 里，已经修过一次；本轮把它**查到底**，结果是
    **7.3 线缺 10 个、6.18 发布线缺 2 个**，包括
    **面板 LED 门禁**、**dtb 兼容门禁**、**分区表工具**、**四个仓库自带的控制**，
    以及 6.18 线上的**门禁中的门禁自己**。
    再往下一层还有**间接**的：`tools/mkgzip.py`（**镜像的压缩脚本**）、
    `overlays/`（**被 `cp -a` 进 OpenWrt 树的那一份**）、
    `configs/sk-d840n-offload.config`（共享配置脚本读的那份 offload 片段）——
    7.3 线用着它们，却都不在 `paths:` 里
    ⇒ **改了压缩脚本，7.3 线不会重建，镜像仍是旧脚本压的**。
    还有一处是**反向**的：7.3 线的 `paths:` 只列了 `docs/101-*`，
    而**文本卫生门禁读的是全仓 131 个 .md**，且**只有 7.3 线在跑它**
    （6.18 线根本没有这道门禁）⇒ `docs/` 之外那份文档里长出一个替换字符，
    **没有任何一轮构建会发现**。
    已补齐，并加一道**机器查**的门禁 `tools/check-sk-workflow-paths.py`
    （两条线加起来 206 条检查），负控 `tests/test_sk_workflow_paths.py`（16 项）。
    见八之十二。

---

## 二、这条线与 6.18 线的关系

| | 6.18 线（现行发布线） | 7.3 线（本文） |
|---|---|---|
| 内核来源 | `cnjn/linux-mainline-zte-zxslc-sr1010` 的 `linux-6.18.38` 子模块 | vanilla `linux-7.3.4.tar.xz`（sha256 钉死） |
| 驱动来源 | 直接在 fork 的核心里（内置式 `zx279133.o`） | `kernel-patches/7.3/` 的 39 个补丁（cnjn `patches-7.3`） |
| 驱动形态 | 拆分模块 np/mac/netdev/main/tm/idm | 组合模块 `npu.o` + `fnoffload.o` + `lan-conduit` + `rtl8372n` |
| 挂接方式 | `CONFIG_EXTERNAL_KERNEL_TREE` | `CONFIG_EXTERNAL_KERNEL_TREE`（同构） |
| OpenWrt 树 | `cnjn/ImmortalWrt` `sr1010-zx279133` @ `1b50aaf4` | `cnjn/ImmortalWrt` `master` @ `31e2ac4` |
| target 配置片段 | `config-6.18` | `config-7.3` |
| 发布 | 发布 release | **不发布**，只出镜像看 |

两条线共用同一个仓库、同一套 `overlays/`（OpenWrt 侧）、同一批 `tests/`。
只有三处按内核代际分叉，本轮全部落地：

* `kernel-overlays/`（内核树覆盖层）——**6.18 专用**，见第五节；
* `overlays-73/target/linux/zte/Makefile`——7.3 线的 zte target；
* `configs/sk-d840n-73.json` + `tools/prepare-sk-kernel-73.py`——7.3 线的锁与准备工具。

---

## 三、证据

### 3.1 目标固件

* `别的人固件/immortalwrt-zte-zx279133-star_sg350h-initramfs.itb`
  sha256 `c6e5010d4b59dbb28d94b6ead8c44d94024c912d761b93fd5186b2d1855fc046`
* initrd 里 `lib/modules/7.3.4/`，`npu.ko` / `npu-pon.ko` / `fnoffload.ko` /
  `zx279133-ge.ko` 四个模块，`npu.ko` 388,632 B，vermagic 7.3.4。

详见 100 号文档第一节，此处不重复。

### 3.2 驱动源码定位

* `cnjn/immortalwrt` master `31e2ac4ded9c8896c7aefd3dcb9a46ef77440fbd`
  （2026-11-10）的 `target/linux/zte/patches-7.3/`，39 个补丁。
* 逆向出的 99 个 `npu.ko` 导出与源码的 84 个 `EXPORT_SYMBOL*` 对照：
  **69 个同名命中**（含逐字一致的 `zte_nppt_ppu_me_diagnostics_get`）；
  **30 个只在逆向里**（PON 钩子：`zx279133_pon_rx_fixup_hook`、
  `zx279133_omcc_gem_id`、`zx279133_tx_pon_control`、`zx279133_pon_flow_gem_hook` …）
  ⇒ **目标固件比公开 master 新**；15 个只在源码里（`zte_nppt_is_wan`、
  `zte_nppt_native_red_usage_get`、`zte_nppt_ppu_me_stats_get`、`zte_nppt_shader_clear` …）。

### 3.3 7.3.4 基线可用性（本机实测）

```
tarball sha256 = b11b6df8295e8e991ff040b71aaa4994a8b55caa23b6b092907f195b5840fd1f
官方   sha256 = b11b6df8295e8e991ff040b71aaa4994a8b55caa23b6b092907f195b5840fd1f
校验: 一致
大小: 162439264 bytes
```

* 官方清单来自 `mirror.nju.edu.cn/kernel.org/linux/kernel/v7.x/sha256sums.asc`
  （即 kernel.org 的镜像，内容与 `cdn.kernel.org` 同源）。
* 下载走南京大学镜像：**3.1 秒 / 51,581 KB/s**。`cdn.kernel.org` 在本机只有
  30–50 KB/s，这是本轮最大的单点提速。
* 补丁 apply：`git apply --check` 后再 `git apply`，**39 个全部干净 apply，0 失败**。
  逐条输出见 `tools/prepare-sk-kernel-73.py` 的实跑日志。
* apply 之后正向校验（不是「git 说 OK」就算数）：树里必须出现
  `zx279133.h`、`zx279133-host.c`、`zx279133-native.c`、`zx279133-offload.c`、
  `arch/arm64/boot/dts/zte/zx279133.dtsi`、`net/dsa/tag_zx279133_rtl8372n.c`，
  少一个就报错退出。

### 3.4 两代驱动集的结构差异

7.3 的 `drivers/net/ethernet/zte/Makefile`（补丁 410 产出）：

```make
obj-$(CONFIG_ZTE_ZX279133_ETH) += npu.o
npu-y := zx279133-main.o zx279133-tm.o zx279133-np.o zx279133-idm.o \
	       zx279133-mac.o zx279133-netdev.o zx279133-offload-api.o \
	       zx279133-ptp.o
npu-y += zx279133-host.o zx279133-native.o zx279133-meter.o zx279133-qos.o
npu-$(CONFIG_ZTE_ZX279133_NATIVE_DEBUG) += zx279133-native-debug.o
obj-$(CONFIG_ZTE_ZX279133_LAN_CONDUIT) += zx279133-lan-conduit.o
obj-$(CONFIG_ZTE_ZX279133_RTL8372N) += zx279133-rtl8372n.o
obj-$(CONFIG_ZTE_ZX279133_FNOFFLOAD) += fnoffload.o
fnoffload-y := zx279133-offload.o
```

与目标固件的 `npu.ko` + `fnoffload.ko` 结构一一对应。

函数级对照（我方 overlay 对 7.3 抽取树，脚本 `tools/func-surface-diff.py`）：

```
ours: ci-build-repo/kernel-overlays/drivers/net/ethernet/zte
theirs: linux-7.3.4/drivers/net/ethernet/zte
functions: ours=327 theirs=367 common=156 only_ours=171 only_theirs=211
```

### 3.5 我们自己的增量清单（171 个函数）

这张表是**实测**出来的，不是手写的：口径是「6.18 世代的
`kernel-overlays/drivers/net/ethernet/zte` 对 vanilla `linux-7.3.4`
里没有的函数」，工具是仓库自己的 `tools/func-surface-diff.py`，
复跑命令见第十节。

| 文件 | 函数数 | 这一块是什么 |
|---|---|---|
| `zx279133-switch.c` | 39 | 9132 交换芯片、LED、MDIO、XPCS |
| `zxpon.c` | 37 | PON 协议核心：PLOAM MIC、T-CONT、GEM、OMCI 队列、AES |
| `zx279133-np.c` | 30 | 9132 交换表 / 端口 / 洪泛 / MF 镜像，**加 ERAM 与 PPU 的基础设施** |
| `zx279133-pon.c` | 17 | PON 栈与网设备的接线 |
| `zx279133-netdev.c` | 12 | TX 恢复 / phylink 生命周期 / LAN 控制 |
| `zx279133-main.c` | 10 | BMU 阈值 / RED 网关 / vendor 重配 / 诊断转储 |
| `zx279133-rgmii.c` | 8 | RGMII 调试 |
| `zx279133-idm.c` | 6 | IDM 实测修正：BP 环配置、RX 头定位、广播查找 |
| `zx279133-mac.c` | 6 | SOPC 就绪 / XPCS 设备 / XMAC 复位掩码 |
| `zx279133-lan1-led.h` | 4 | LAN1 那盏灯的 claim / read / set / restore |
| `zx279133-omci.c` | 2 | OMCI 队列的准备与回收 |
| **合计** | **171** | |

**上一版这张表是错的**：只有 8 行、相加 161，比总数少 10 个——
`zx279133-np.c` 的 6 个（ERAM/PPU 基础与 debugfs 的拆除），
以及整个 `zx279133-lan1-led.h`（4 个）没有被记账。
**总数 171 是对的，分解是错的**，而分解正是「要不要搬」的清单，
所以这个错不是排版问题。

7.3 公版**没有 PON 模块**（`npu-pon.ko` 在目标固件里有、公开树里没有），
这意味着：我们自研的 PON/OMCI 栈不是「被 7.3 取代的东西」，而是**站在公开树
前面**的东西。

### 3.6 板级层重基结论

* **DTS 的 27 个 label 引用全部可解析**：14 个来自 7.3 的 `zx279133.dtsi`
  （`gpio0-4`、`mdio0/1`、`nppt`、`pon_serdes`、`pvt`、`spifc`、`uart0`、
  `uni_serdes`、`usb3`、`watchdog0/1`、`xpcs0/1`），其余 13 个在我们自己的板级
  DTS 里定义。7.3 的 `zx279133.dtsi` 相对 6.18 改了 CPU 策略
  （固定 2.0 GHz PLL → OPP 表 + PWM 调压，最高 1.4 GHz）、topcrm 增加了
  `cpu-pll` 寄存器窗口、PWM 由 `disabled` 改 `okay`——**这些改动不影响我们的板级文件**。
* **DSA tagger 两代逐字节一致**：`net/dsa/tag_zx279133_rtl8372n.c` 在 6.18 树
  与 7.3 树里都是 2,774 B。我们把 `include/net/dsa_zx279133_rtl8372n.h` 的
  `USER_PORT_MAX` 从 7 提到 8（9132 的第五个 UNI 口要 transport VID 63）
  这个改动在 7.3 上照样成立。
* **Unim NAND 接线前提成立**：7.3 树里 `drivers/mtd/nand/spi/{core.c,Makefile}`
  与 `include/linux/mtd/spinand.h` 都在，而 `scripts/configure-sk-d840n.sh`
  对 `unim.o` 的接线是**在构建时改这三个文件**，与内核代际无关。
* **NPU 微码要放进内核树**：`config-7.3` 里
  `CONFIG_EXTRA_FIRMWARE="zte/zx279133/mcode_intel.bin"` +
  `CONFIG_EXTRA_FIRMWARE_DIR="firmware"`，内核按自己的源码树解析，所以
  `mcode_intel.bin` 必须落在准备好的树里（cnjn 是在他的 `Kernel/Prepare` 里拷的，
  我们在自己的脚本里拷）。

---

## 四、CI 通道怎么接

### 4.1 为什么不是 OpenWrt 原生模式

cnjn master 的 `target/linux/zte/Makefile` 写着：

```make
ifneq ($(DUMP),1)
  ifneq ($(call qstrip,$(CONFIG_EXTERNAL_KERNEL_TREE))$(call qstrip,$(CONFIG_KERNEL_GIT_CLONE_URI)),)
    $(error The ZTE target uses its Linux 7.3.1 patch series. Clear CONFIG_EXTERNAL_KERNEL_TREE and CONFIG_KERNEL_GIT_CLONE_URI)
  endif
endif
```

原生模式下 OpenWrt 自己下载 7.3.1、自己打 `patches-7.3`。我们不走这条路，
理由有三条：

1. **可离线复核。** 底包字节、补丁字节、补丁顺序、OpenWrt 元数据，全部被本仓库
   的锁文件钉死；本机不装 arm64 工具链也能把整棵树准备出来（已实测）。
2. **不依赖别人的 OpenWrt 语义。** `include/kernel-defaults.mk` 里
   `CONFIG_EXTERNAL_KERNEL_TREE` 分支会 `ln -s` 外部树并**跳过 target 补丁**，
   于是「谁打补丁」是我们说了算，不会出现两边都打或者都没打。
3. **版本口径干净。** cnjn 在 `include/kernel.mk` 里专门为
   `BOARD:zte KERNEL_PATCHVER:7.3` 留了
   `LINUX_UNAME_VERSION:=$(LINUX_VERSION)`——外部树模式下
   `$(LINUX_DIR)/include/config/kernel.release` 此刻还不存在，这条分支正是为此
   而写。我们的 target Makefile 保持 `KERNEL_PATCHVER:=7.3`，正好落进这个分支。

### 4.2 一次构建的完整链路

```
configs/sk-d840n-73.json          锁：7.3.4 / tarball sha256 / 镜像地址 / 上游 commit
        │
tools/prepare-sk-kernel-73.py     下载 → 校验 sha256 → 解包 → 逐个 apply 39 补丁
        │                         → 正向校验产物 → 改写 openwrt 的 kernel-7.3 元数据
        │                         → 写 kernel-provenance-73.json
        ▼
linux-7.3.4/                      vanilla + cnjn patches-7.3
        │
tools/apply-sk-overlay-73.sh      板级层：板级 DTS + Unim NAND + DSA VID 范围
        │                         + mcode_intel.bin；并断言 6.18 驱动文件没有混进来
        ▼
overlays/ + overlays-73/           OpenWrt 侧（target Makefile 换 7.3 版）
        │
scripts/configure-sk-d840n.sh     复用现有配置脚本（只把一处硬编码的 config-6.18
                                  改成版本无关的 $KCFG）
        ▼
CONFIG_EXTERNAL_KERNEL_TREE=<树>   OpenWrt 把这棵树当自己的内核目录
```

### 4.3 幂等与「树被别的东西改过」

工具用**标记文件** `.sk-series-applied.json` 判定幂等，**不用**「反向 apply 是否成功」
来判——因为系列里 450 与 460 两个补丁都改 `net/netfilter/nf_flow_table_offload.c`，
后者会让前者反向失败，用反向判据会把一棵完整的树误判成半成品。
若树里已经有驱动却没有标记文件，工具**拒绝**再打一遍并说明原因。

---

## 五、本轮落地的文件

| 文件 | 作用 |
|---|---|
| `configs/sk-d840n-73.json` | 7.3 线的锁：版本、tarball sha256/大小、镜像、上游 commit、OpenWrt 元数据改写口径 |
| `tools/prepare-sk-kernel-73.py` | 准备 7.3.4 树：下载校验 → 解包 → 逐补丁 apply → 正向校验 → 改写 OpenWrt 元数据 → 写 provenance |
| `tools/apply-sk-overlay-73.sh` | 把板级层拷进 7.3 树，并**显式排除**6.18 世代驱动文件；拷完逐条断言正反两面 |
| `overlays-73/target/linux/zte/Makefile` | 7.3 线的 zte target（`KERNEL_PATCHVER:=7.3` + 外部树要求） |
| `.github/workflows/build-sk-d840n-73.yml` | 7.3 通道：手动触发 + 7.3 相关路径推送触发；只出镜像，不发布 |
| `scripts/configure-sk-d840n.sh` | 一处改动：把硬编码的 `target/linux/zte/zx279133/config-6.18` 换成版本无关的 `$KCFG` |
| `kernel-patches/7.3/` | cnjn 的 39 个补丁的 vendored 副本 + `MANIFEST.json`（上游 repo/branch/commit/date + 每补丁 sha256/bytes） |
| `kernel-patches/7.3-local/` | **我们自己的**补丁，applied 在 vendored 系列之后；当前是 IDM 实测修正三片（`100` 寄存器值、`110` RX 帧落点、`120` BP 投递协议）**加第 3 片的 `130` LAN 控制事务**、**第 4 片的 `140` PON/OMCI 接线**、**第 5 片的 `150` 9132 芯片编程**、**第 7 片的 `160` TX 故障隔离**、**第 8 片的 `170` RX 交付安全**与**第 10 片的 `180` TX 隔离计数器发布**（共 9 个） |
| `kernel-overlays-73/drivers/net/ethernet/zte/` | **7.3 线专有的驱动树**：9132 交换表驱动本体 + `zx279133-9132.h`（9132 自己的寄存器图**与 4 个 PPU 号**）+ `zx279133-lan1-led.h`，**加第 4 片的 PON/OMCI 五个文件**（`zxpon.c`/`zxpon.h`/`zx279133-pon.c`/`zx279133-omci.c`/`zx279133-omci-wire.h`）。与 6.18 世代的 `kernel-overlays/` 并存，互不覆盖（见八之三、八之四、八之五） |
| `tools/check-sk-switch-api.py` | 第 3 片的编译前门禁：驱动用到的名字、内核 API、**LAN 服务契约**、构建接线，四项逐条核对（见八之三.3） |
| `tools/check-sk-pon-api.py` | 第 4 片的编译前门禁：协议核心闭合、**内核名字在包含闭包里可见**、树侧契约、四个钩子、构建接线、LED 依赖（见八之四.5） |
| `tools/check-sk-9132-api.py` | 第 5 片的编译前门禁：编程在树里、**ERAM 描述符在分配处被记录**、名字闭合、内核 API 在、**没有重定义**、调用顺序、板级 DTS 的 PHY 总线（见八之五.7） |
| `tests/test_sk_workflows.py` | 工作流自身的门禁：每个工作流文件可解析、7.3 线依赖的步骤都在、门禁点名的测试文件都存在、7.3 线的路径过滤覆盖这条线的输入（见 7.9） |
| `tools/check-conduit-netdev-name.py` | **conduit 网口发布名**的门禁：不 grep 格式串，而是按内核的规则（`dev_valid_name()` → `__dev_alloc_name()`）把发布名**算**出来再比对，必须是 `lan-cpu0`（见 7.27） |
| `tests/test_conduit_netdev_name.py` | 上面那道门禁的**负控**：格式串改了、说明符不对、名字被占用、驱动不注册、驱动命名两次 —— 每一种都必须转红 |
| `tools/gatecheck.sh` | **刷机闸门**：只放行「这块板上真跑起来过」的那对字节。版本判据改成**从待刷 uImage 的 banner 读出期望版本**再与凭证逐字符比对（`6.*` 那种写死系列的老写法已删），并去掉 `md5sum` 在 Windows 路径下的转义（见 7.29） |
| `tools/uimage-kver.py` | 从 uImage 的 banner 读出内核版本（独立的第二套实现，与闸门里的 shell 读法交叉对拍） |
| `tests/test_gatecheck.py` | 闸门的**负控**：6.18 凭证配 7.3 镜像、厂商 4.19 凭证、读不出版本的镜像、缺 `root_read`/`root_index`/`jffs2_ro` 的凭证、空标签 —— 每一种都必须转红（见 7.29） |
| `tools/mkctlram.py` | 门禁轮 payload 的生成器：**新增 `--kver`**（默认从 `staging/trial/flash-uImage` 的 banner 推导），payload 只为「它被造出来要骑的那个内核」写 RAM mailbox —— 厂商 4.19 控制轮照旧一个字不写（见 7.30） |
| `tests/test_ctl_payload.py` | 上面那条的**负控**：版本必须来自镜像、显式指定优先、模板里不能再有 `6.*`、厂商内核仍不匹配 |
| `tools/check-sk-image-drivers.py` | **产物级门禁**：问**要刷进板子的那个 uImage** 里有没有我们自己的驱动。每个探针两半分开判 —— 串还在它声称的源文件里（不是过期探针）**且**串在 inflate 后的内核里（代码真被编进去了）。「在树里 / 能编 / 配成内建 / 在镜像里」是四句话，前三句有门禁管，第四句原来没有（见 7.31） |
| `configs/sk-d840n-73-image-drivers.json` | 上面那道门禁的**探针清单**（**9 条**，覆盖第 3/4/5/**7/10** 片与 conduit）。清单**不在工具里**：发布线的内核里是另一批驱动，得有另一份；清单本身也进了 path filter，改了它就要重跑构建。**第 8 片没有产物级探针**——补丁 170 没引入任何字符串字面量，字节层面无从问起（见 7.41） |
| `tests/test_sk_image_drivers.py` | 上面那道门禁的**负控**：针不在像里、针不在源里（=过期探针）、源文件不存在、不是 uImage、载荷不是 gzip、清单没有探针、**针在第二个 gzip 成员里** —— 每一种都必须转红（见 7.31） |
| `tools/check-baked-version.py` | **lint**：找出**把内核版本写进判断**的地方。判据是**位置**（case 分支、比较的一边、grep 的模式参数、指名版本行的模式串），不是文本 —— 消息、docstring、IP、元数据版本都不算（见 7.32） |
| `configs/baked-version-allow.json` | 上面那道 lint 的**允许清单**：每一条都写明「为什么这个版本可以留在这里」。现在只有**厂商内核 4.19** 那一类（板子上的固定事实），**失效的允许项会被报出来** —— 那是洞 |
| `tests/test_sk_baked_version.py` | 上面那道 lint 的**负控**：该判的四种形状都判、不该判的五种形状都不判、允许项的三种边界都验（15 项） || `overlays-73/target/linux/zte/zx279133/target.mk` | 7.3 线的 subtarget 默认包：**清空 OpenWrt 的 `DEFAULT_PACKAGES.tweak`**，否则其中的 `luci` 元包会拉进 `luci-theme-bootstrap`（见 7.8） |
| `tools/check-release-kernel-version.py` | **独立对照门禁**：读一遍出货报告、再读一遍镜像，比对两边的内核版本。写报告的那个工具**不替自己作证** —— 它刚把 banner 读错过一次（见 7.33） |
| `tests/test_release_kernel_version.py` | 上面那道门禁的**负控**（25 项）：不只验「拒绝」，还验**拒绝的理由**；也验旧格式报告仍然被接受 |
| `tools/check_sysupgrade.py` | 改了：读不到 banner **不再硬失败**，版本记 `null` 并加 `version_known` 标志 —— 「旧报告没这个字段」与「新报告说这镜像没有 banner」要能分开（见 7.33） |
| `tools/preflash.py` | 改了：报告的版本读成**三态**（`version` / `no-banner` / `absent`）。新报告说没有 banner ⇒ 红灯；旧报告没有这个字段 ⇒ 降级为只做厂商检查并说明（见 7.33） |
| `kernel-patches/7.3-local/160-sk-tx-fault-isolation.patch` | **第 7 片**：把 6.18 世代的 TX 故障隔离与恢复模型搬进 7.3 树（四个文件、405 行新增）。不可信的完成计数器**不再被钳位后照单释放**：环被隔离、两个逻辑 TX 设备被停、恢复工作 flush + reclaim 并**至多一次**重启共享 datapath（见 7.34） |
| `tools/check-sk-tx-isolation.py` | **第 7 片的门禁**：按**位置**判 36 条不变量 —— 故障分支里不能有任何释放语句、函数里所有释放都必须在该分支之后、隔离守卫在读计数器之前、两个逻辑设备都被停、恢复不等 RTNL、重启由真实完成增量重新武装（见 7.34） |
| `tests/test_sk_tx_isolation.py` | 上面那道门禁的**负控**（14 项）：突变跑在**真树**的副本上，不在合成树上 —— 合成树只能证明门禁读得懂合成树（见 7.34） |
| `kernel-patches/7.3-local/170-sk-rx-delivery-safety.patch` | **第 8 片**：把 6.18 世代 RX 交付路径上的 6 处纯软件正确性修复搬进 7.3 树（ethertype 守卫、三处越界读、XDP PASS 合法性、`CHECKSUM_NONE`、归还前的整窗设备侧同步）。**只搬有实机/文档证据的那些**；jack 计数与 `cpu_dp->rcv()` 直调 tagger 留给第 9 片（见八之八） |
| `tools/check-sk-rx-delivery.py` | **第 8 片的门禁**：按**位置**判 39 条不变量 —— 参照点全是**执行点**（调用点、条件、使用点），不是声明点（见八之八.3） |
| `tests/test_sk_rx_delivery.py` | 上面那道门禁的**负控**（24 项）：突变跑在**真树副本**上，不在合成树上 |
| `tests/test_sk_prepare_content.py` | **prepare 步内容校验的负控**（16 项）：一个补丁加了行而树里没有，就必须被拒；空行不算缺失；自包含，不需要内核树 |
| `tests/test_sk_prepare_apply_context.py` | **apply 上下文的负控**（12 项）：自建一个 checkout 加一个树在里面，断言**带 `diff --git` 头的补丁也落地**、**纯 diff 的补丁落地**、**context 挡住了 checkout**；反向三项：**git 确实会默默丢掉补丁**（退出 0、零写入、stderr 里有 `Skipped patch`）、**被丢掉的补丁被拒绝**、**被拒绝的树没有那行内容**（见 7.39） |
| `tests/test_sk_text_hygiene.py` | 文档的**文本卫生**门禁：查 **U+FFFD、NUL 与其它控制字符**。一个 NUL 就足以让 ripgrep 把整个文件当成二进制，**所有针对该文档的搜索都静默返回空**（见 7.39） |
| `kernel-patches/7.3-local/180-sk-tx-isolation-stats.patch` | **第 10 片**：把 TX 故障隔离的四个计数器接到驱动**本来就有**的 `ethtool` 统计表上（+19 -0，只改 `zx279133-netdev.c`）。**加在枚举与名字表的末尾**，已有统计项的索引一个都不动；四个计数器**同序**、**在同一把锁里读**（见八之十） |
| `tools/check-sk-tx-isolation-stats.py` | **第 10 片的门禁**（33 条）：按**位置**判 —— 两张表同长、四个计数器在两张表里是**同一个索引**、赋值在 TX 锁内、读的是它自己那个计数器、那个计数器真的在被累加。**编译器抓不到「名字在错位」**，所以门禁必须自己抓 |
| `tests/test_sk_tx_isolation_stats.py` | 上面那道门禁的**负控**（11 项）：每一项改坏树的一个地方，门禁必须**转红在指定的那条检查上**，而不只是在某处变红 |
| `kernel-patches/7.3-local/190-sk-datapath-restore-on-conduit-open.patch` | **第 11 片**：把 ours 6.18 世代「conduit 打开时恢复失败的数据面」那一段搬进 7.3 树（+25 -2，只改 `zx279133-netdev.c`）。**顺序是关键**：恢复排在硬件检查**前面**，因为数据面失败时硬件是 unprepare 的，检查排在前面的话 open 会在恢复跑到之前就被拒掉。健康的数据面上恢复是空操作（见八之十一） |
| `tools/check-sk-datapath-restore.py` | **第 11 片的门禁**（18 条）：按**位置**判 —— 恢复被调用、在 `datapath_lock` 里、**排在硬件检查之前**、失败不让 open 继续、健康时立刻返回；以及「DSA 在用户口 up 时打开 conduit」查的是 `dsa_user_open()` **函数体内**（树里不止一处打开 conduit） |
| `tests/test_sk_datapath_restore.py` | 上面那道门禁的**负控**（9 项）：恢复没了、顺序反了、错误被吞、锁没了、助手不再早退、服务的对调了、conduit 不走服务了、DSA 不开 conduit —— 每一项都转红在指定的那条检查上 |
| `tools/check-sk-workflow-paths.py` | **两条线共用的门禁**（加起来 206 条）：工作流**跑**的、**提到**的每个仓库文件都必须被 `paths:` 覆盖，`paths:` 里逐个点名的文件必须真的存在。改了一个门禁却忘了登记 ⇒ **不会重建 ⇒ 那道门禁从此不再被验证**（见八之十二） |
| `tests/test_sk_workflow_paths.py` | 上面那道门禁的**负控**（16 项）：拿掉条目、改坏 glob、删掉整个 `paths:` 块、指向不存在的文件、拿掉只被提到的文件 —— 每一项都转红在指定的那条检查上；另有一项**反过来**：拿掉被运行的那些行，门禁必须**不再问**它；再有一项：**控制不许往 run 页面发判决**（见八之十二.5） |
| `tools/func-surface-diff.py` | 两代驱动的函数面对照，输出「只在我们这边」与「只在 7.3 那边」两张工作清单 |
| `tools/func-port-coverage.py` | **按能力对账**：171 个函数逐条必须有归属（同名 / 账本写明搬到哪 / 有处置记录），未记账即非零退出；账本指着的目标函数必须真在 7.3 树里（见八之七） |
| `tools/kcheck.py` + `tools/kshadow.py` | **编译预言机**：用宿主 clang 对 7.3 树做 `-fsyntax-only`，生成树自己的头文件与影子头文件；7.3 线在 Compile 之前先跑它（见 7.17） |
| `tests/test_sk_kcheck.py` | 上面那道检查的**判决规则**的单元测试：哪些错误算我们的、哪些算编译器读不动树 |
| `tools/idm-register-diff.py` | 以 `writel(...)` 语句为单位逐条对比两代的寄存器写入 |
| `overlays-73/package/libs/libbpf/patches/110-arm64-u128-types.patch` | **7.3 线 libbpf 的唯一一处补丁**：给 libbpf 自己那份 `include/linux/types.h` 补 `__u128`/`__s128`（7.3.4 的 `asm/ptrace.h` 用 `__u128`，而 uapi 那份定义被 libbpf 自带的头遮蔽了），外加一条带 `#ifndef` 守卫的 `__attribute_const__`（**防御性定义，真实构建用不到** —— 装头时就被内核剥掉了）。**只落 7.3 线**，`overlays/` 一行未改（见 7.21） |
| `pon/npu-reverse/` | 逆向产物留档：99 个 API、rodata、函数表、initrd 清单、目标 DTS |

**没有动的东西**：`kernel-overlays/`（6.18 世代驱动，一行未改）、`overlays/`（OpenWrt 侧，
一行未改）、现行 `build-sk-d840n.yml`（发布线一行未改）。

---

## 六、未做 / 风险

1. **我们自己的 171 个函数逐条都有归属，但「搬了」不等于「同名」。**
   7.3 通道目前是「cnjn 的 7.3 驱动 + 我们的板级层 + IDM 三片 + 9132 交换表驱动
   + PON/OMCI 栈 + **9132 芯片编程**」。
   实测对账（八之七，`tools/func-port-coverage.py`）：
   **125 个同名对上**、**27 个由 7.3 树以别的名字、别的形状实现**
   （vendor 兼容重配、BMU 阈值、RED 网关、SOPC、数据面模型，7.3 各换了机制）、
   **19 个有明确处置**：RGMII 的 8 个（**取舍不是缺口**，见八之五.9）、
   TX 故障隔离/恢复的 8 个（**不整体移植**，见八之五.9）、
   9132 debugfs 控制台的 4 个（7.3 树有自己的 `native_*_io` 端点），
   外加 1 个只做调试打印的函数。**未记账 0 个。**
   ⇒ 「剩下的是 RGMII 与 TX 恢复」这个说法**按函数名看是对的、按能力看是错的**：
   那些函数名之所以不在 7.3 树里，是因为 7.3 树用另一套机制做同一件事。
   **但这五片还没有一次成功的编译记录**——本机无交叉工具链，编译只能走 CI；
   7.3 线此前每一轮都死在下载阶段（见 7.16），所以第一轮完整编译的结果还不知道。
   实机对拍是它之后的下一道。
   **PON/OMCI 的上行没有接**——但 6.18 侧同样没有接（见八之四.7），
   所以这不是 7.3 线独有的缺口。
   **PON MAC 窗口一个寄存器都没碰**（厂商模块没有
   `mode_xgpon_nsyn_cfg_133`），所以第 4 片只在没有光纤时也成立：
   LOS 灯会跟着 `opsd` 翻，carrier 停在 O1，OMCI 队列空转。
2. **没有编译验证。** 本机无 arm64 交叉工具链，编译只能走 CI；`build-sk-d840n-73.yml`
   是**第一次**运行，结果未知。
3. ~~**DTS 没有在本地编译过。**~~ **本轮补上了**：本机现在有与 CI 同版本的
   dtc 1.6.1（见八之十三），DTS 门禁在本机**真的跑过**（`tests/test_sk_led_dts_gate.py` 绿），
   不再只是「label 可解析 + 属性存在」的静态核对。
4. ~~**驱动是模块而非内置。**~~ **已在本轮判明并修掉**，见 7.12：
   7.3 的 `config-7.3` 里 `CONFIG_ZTE_ZX279133_ETH=m`，而 6.18 线是内置；
   模块化不只是「要重新确认 kmod 依赖链」，而是**镜像里根本不会有驱动**——
   本仓库没有加载它们的机制，`pin_y` 也会被 kconfig 夹掉。7.3 线现在把
   `ZTE_ZX279133_ETH`/`_LAN_CONDUIT`/`_SWITCH` 钉成 `=y`，与 6.18 线一致。
   **剩下的**：`ZTE_ZX279133_RGMII` 在 7.3 树里**没有 Kconfig**，
   配置脚本钉的符号会被 defconfig 静默丢掉（6.18 线上的 RGMII 只读探针
   在 7.3 上不存在）。
   **但 MAC 本身在内建驱动里**（本轮实测，基线树
   `drivers/net/ethernet/zte/Makefile:11-13`）：7.3 的 MAC 是
   `zx279133-mac.o`，**在 `npu.o` 里面**，随 `ZTE_ZX279133_ETH=y` 一起进内核；
   7.3 原版 Kconfig 里也没有 `ZTE_ZX279133_SWITCH`，
   它是 vendored 补丁加进来的（基线树 Kconfig 第 79 行），
   而 `kernel-overlays-73/` 只提供 `zx279133-switch.c` 的源码、**不带 Kconfig**，
   所以树里那个 Kconfig 不会被覆盖掉。
   ⇒ 缺的只是「一个独立符号的探针」，不是「MAC」；
   四个符号（`_ETH`/`_LAN_CONDUIT`/`_SWITCH`/`_FNOFFLOAD`）在**我们真编的那棵树**里都在，
   合并配置门禁要求它们 `=y`，这一步会在 CI 里判。
5. **目标固件的 PON 模块仍未公开。** 我们逆向出的 30 个 PON 钩子导出只在二进制里；
   等 cnjn 公开 `npu-pon.ko` 源码，我们自研的 PON/OMCI 栈要么对齐、要么让位——
   这个决策应该在 7.3 的 NPU 驱动跑通之后再做。
6. ~~**7.3 线现在只编译，不产出镜像。**~~ **本轮补上了**：
   7.3 线现在会收集 `bin/targets/zte/zx279133`、用 6.18 线同一个工具
   （`tools/check_sysupgrade.py`）校验 sysupgrade 镜像并导出三个分区文件
   （`flash-uImage` / `flash-dtb.bin` / `flash-rootfs.jffs2`），
   再把镜像与分区文件一起作为 artifact 上传。
   **仍然没有的**：6.18 线那套**发版**流程（Release、免串口直刷下载、
   `flash-set.tgz` 维护包、initramfs 的 `.itb`）——7.3 线只出镜像不发布，
   这是有意的。`matrix.mode` 目前只影响 `configure-sk-d840n.sh` 的第三个参数，
   initramfs/flash 两路走的是同一条编译链。
   **要注意的**：NPU（加上 flow offload）现在在 `vmlinux` 里，
   要塞进固定 8 MiB 的 `kernel` 分区；镜像 profile 的 `KERNEL_SIZE := 8192k`
   会让 OpenWrt 自己拒绝超尺寸的镜像，但「Collect the images」步会把字节数
   打进 annotation，超了就一眼看得见。
7. **6.18 线那道「管理面进了镜像」的门禁只搬了一半**：
   `Gate: ONU UI, protocol service and firewall4 reached the image`
   跑的是 `scripts/check-sk-d840n-management.sh`，它要连带 8 个工具与测试
   （staged root 的文件清单、主机侧的 ucode 解释器、firewall4 自己的规则生成器
   测试套件）。那一整套问的是**发布线的产品面**，不是这次迁移；
   而它的另一半——「包是不是真的装进了镜像，而不只是被选中」——
   已经搬成 7.3 线的一道轻量门禁（`Gate: the image carries the packages
   the board needs`，读 `bin/targets/**.manifest`）。
   另：「7.3 线要不要把整套 netfilter 钉成内置」——**不需要，它本来就是内置的**，
   见 7.14.2。

---

## 七、7.3 通道的 CI 运行记录

### 7.1 run #1（2026-11-22 09:50Z）

| 阶段 | 结果 |
|---|---|
| `repository gates` | ✅ 通过 |
| `initramfs` / `flash` 的 `Configure` | ❌ 失败：`Missing SK-D840N target kernel config` |
| 本机复现 | ✅ 复现成功（用准备好的 7.3 树 + IW master 的稀疏检出 + 我们的 overlays 跑同一个脚本） |
| 根因 | `scripts/sk-d840n-offload.sh` 把三个配置片段名写死成 `config-6.18` |
| 修法 | 系列号从**内核树自己**的 `VERSION`/`PATCHLEVEL` 读出来；两个系列都验证过（7.3 树 → 7.3，6.18 fork → 6.18，发布线行为不变） |

### 7.2 run #2（2026-11-22 09:59Z，head `3dbf3003`）

| 阶段 | 结果 |
|---|---|
| `repository gates` | ✅ 通过 |
| `Prepare the Linux 7.3.4 tree` | ✅ 通过（39 个 vendored 补丁 + 1 个本地补丁，CI 上干净 apply） |
| `Apply the board layer` | ✅ 通过（板级 DTS / Unim / DSA 头 / 微码全部落地） |
| `Apply the OpenWrt-side overlays` | ✅ 通过 |
| `Enable /dev/mem in the 7.3 fragments` | ✅ 通过 |
| `Check the overlay for what a compiler would have caught` | ✅ 通过 |
| `initramfs` / `flash` 的 `Configure` | ❌ 失败：`Argon is not built into the resolved SK-D840N config` |

**判决**：run #2 把失败点从「配置片段名写死」推进到了「defconfig 之后的
主题断言」。前六步全绿说明**树准备与板级层重基在 CI 上是成立的**；
剩下的失败与 7.3 内核无关，是 OpenWrt 侧少了一个步骤。

**根因**：`jerrykuku/luci-theme-argon` 的 Makefile 最后一行是

```
include $(TOPDIR)/feeds/luci/luci.mk      # Makefile:54
```

7.3 工作流**没有「Install feeds」这一步**（6.18 发布线有，
`build-sk-d840n.yml:782`），于是 `feeds/luci/luci.mk` 不存在 ⇒ Argon 的
配方无法解析 ⇒ 这个包不进包数据库 ⇒ `make defconfig` 把
`CONFIG_PACKAGE_luci-theme-argon=y` **静默丢弃**（和「符号不存在」的
处理方式一样）⇒ 主题断言报错。

CI 警告里还留着同一件事的另一个侧面：

```
WARNING: Makefile 'package/emortal/default-settings/Makefile' has a dependency on 'luci', which does not exist
```

`luci` 不存在 = luci feed 没装 = Argon 依赖的 `luci-base` 也不存在，
即使 `luci.mk` 在也选不上。

**修法**：在 7.3 工作流里补上与 6.18 线同构的 `Install feeds` 步骤
（`feeds update -a` + `feeds install -a`），位置放在门禁之后、Configure
之前，并对 `feeds/luci/luci.mk` 与 `package/feeds/luci/luci-base/Makefile`
做存在性断言。

### 7.3 这一轮暴露的方法论错误：本机复现的边界被高估了

run #1 的「本机复现成功」是**假通过**。本机复现环境
`scratch/ow73run` 里没有 `feeds/`、也没有 `package/luci-theme-argon`，
但它的 `.config` 里**仍然留着** `CONFIG_PACKAGE_luci-theme-argon=y`——
因为本机没有 `make`，`make defconfig` 根本没跑，那行配置也就从没被
丢弃过。本机复现只走到了 `defconfig` **之前**。

所以边界要写清楚：

| 本机可复现 | 本机不可复现 |
|---|---|
| `Prepare` / `Apply board layer` / `Apply overlays` / `Enable /dev/mem` / `Check the overlay` | `make defconfig` 及其之后的**全部**断言 |
| 任何「改配置片段」的脚本逻辑 | **符号不存在 → 被静默丢弃**这一类行为 |

`defconfig` 丢弃无效符号时不出一声，所以「本地 .config 里还有那行」
和「CI 上那行不见了」之间的差别，本机原理上看不见——除非先装上 feeds
再跑 defconfig，而 defconfig 需要 make。

这条边界就是「失败点由断言精确指出，而不是让构建跑到一半才炸」的价值
所在：run #2 的报错落在 `skd840n_check_theme_config` 这一句断言上，
而不是七十多分钟后的某个编译错误。

### 7.4 拿日志的手段（无 token 的情况）

本机没有 GitHub token，实测：

* `GET /actions/jobs/<id>/logs` → **403**；
* `GET /actions/artifacts/<id>/zip` → **401 Requires authentication**；
* `GET /commits/<sha>/check-runs` → **可读**（偶发 504，重试即可）；
* `GET /check-runs/<id>/annotations` → **可读**，工作流里
  `echo "::error::$message"` 的正文就在这里。

所以失败诊断走「check-run → annotations」，不是日志下载。

---

### 7.5 run #3：feeds 修复生效，失败点前进了

**判决**：`Install feeds` 那一补**是对的**。run #3 的 Configure 仍然失败，
但**报错换了**，而且是从「包根本没进来」变成「包进来了但多了一个」：

| | run #2 | run #3 |
|---|---|---|
| Configure 的最后一句 | `Argon is not built into the resolved SK-D840N config` | `Only Argon may be selected in the SK-D840N config` |
| 含义 | Argon 的配方**没被解析**，`feeds/luci/luci.mk` 根本不存在 | Argon **已经**在 `.config` 里了，但 **bootstrap 主题也在** |

也就是说 run #3 越过了「Argon 不存在」这一关，撞上了下一关：
`.config` 里同时有 `CONFIG_PACKAGE_luci-theme-argon=y` 和
`CONFIG_PACKAGE_luci-theme-bootstrap=y`，而断言要求**只有** Argon。

**已经排除的路径**（都查过，都不成立）：

| 怀疑 | 查证 | 结论 |
|---|---|---|
| master 的 `DEVICE_PACKAGES` 注入了 bootstrap | `target/linux/zte/image/Makefile` 里确实有 `DEVICE_PACKAGES := ... luci-theme-bootstrap ...`，但我们的 overlay 用自己的 image/Makefile **覆盖**了它——CI 的断言 `grep -q 'Device/skyworth_sk-d840n' openwrt/target/linux/zte/image/Makefile` 就是覆盖生效的证据 | 排除 |
| `skd840n_select_theme` 的 sed 没生效 | 它的第二行 `s/^(CONFIG_PACKAGE_luci-theme-[^=[:space:]]+)=[ym]$/...` 对 `CONFIG_PACKAGE_luci-theme-bootstrap=y` 是匹配的，第三行还额外追加了 `# CONFIG_PACKAGE_luci-theme-bootstrap is not set` | 排除 |
| 两线的初始 `.config` 不同 | master 与 sr1010-zx279133 分支的 `configs/zte_zx279133_sr1010_initramfs.config` **都**有 `CONFIG_PACKAGE_luci-theme-bootstrap=y`（master 在第 25 行） | 排除 |
| `skd840n_select_theme` 第一行 sed 替换的主题名来源不同 | **这才是两线的差别**：6.18 线分支的 `target/linux/zte/zx279133/target.mk` 里有 `DEFAULT_PACKAGES += ... luci-theme-bootstrap`，那一行 sed 把它改成 argon，于是 defconfig 选 argon；**master 的 target.mk 里没有 `DEFAULT_PACKAGES`**，那条 sed 无事可做 | 部分成立，但这只能解释「argon 没被自动选上」，解释不了「bootstrap 又变回 y」 |
| 某个被选中的包 select 了 bootstrap | **已排除**：把两个 luci 树（`immortalwrt/luci@ed7692cb` 与 `fxfall/luci@d438535`，各 243 个构建文件）完整扫了一遍，提到 `luci-theme-bootstrap` 的**只有四处**，而且**两个树完全一样**：`collections/luci-layer2`、`collections/luci-light`、`collections/luci-nginx` 依赖它，加上它自己的定义。依赖链是 `Package/luci`（元包，标题带 "(default)"）→ `+luci-light` → `+luci-theme-bootstrap` | 链存在，但**两线一样**，所以它不是两线的差异源 |

**还缺的一环**：defconfig 之后 `.config` 里**到底哪些 luci 包被选中**。
这正是断言报错时看不见的东西——工作流只取日志尾部 65 行，看不到全貌。

**修法（诊断先行）**：把 `skd840n_check_theme_config` 的报错补成打印**全部**
`CONFIG_PACKAGE_luci*` 行。这两行 grep 能一次分开两种情况：

* 打印出来**只有** bootstrap ⇒ 它没被 unset 掉，是 sed 没作用到它；
* 打印出来**还有** `luci` / `luci-light` ⇒ 是依赖链在 defconfig 期间把它拉了回来，
  要从「谁选了 luci 元包」入手。

修法确定之后，下一轮 CI 就能带上决定性的那一行输出。

---

### 7.6 板级层的编译前提：本机预检（在 Compile 之前）

**判决**：板级层在 7.3 树上的内核 API 前提**全部满足**，本机可查的范围内
没有编译风险；但**真正的编译只能在 CI 里发生**。

**为什么要单独做这一步**：本机没有 arm64 交叉工具链，`Compile` 一失败就是
一轮七十分钟。而板级层（板级 DTS / Unim NAND / DSA 头）是这次唯一**由我们
新增**、且**在 7.3 公版里不存在**的代码——公版驱动集编译得再干净，也不能
替我们保证这几个文件。

**工具**：`tools/check-sk-board-api.py`，两个阶段。

| 阶段 | 跑在什么时候 | 查什么 |
|---|---|---|
| `pre` | 树备好、overlay 还没拷进去 | 板级层要用的每个宏/类型/访问器在树里存在且形态正确；记下 cnjn 公版 DSA 头的宏值 |
| `post` | overlay 拷完之后 | 板级文件已落位；DTS 的 include 与 label 全可解析；tagger 用到的宏我们的头里都有；宏值与公版逐个对照 |

**本机结果**：两阶段 exit=0。

**证据（关键的几条，都是会咬人的）**：

* **7.3 把 `include/linux/nand_dev.h` 删了**，它的内容并进了
  `include/linux/mtd/nand.h`。`unim.c` 调用的 `nanddev_get_ecc_conf()`
  现在在 `mtd/nand.h:634`，芯片条目用的 `NAND_MEMORG` / `NAND_ECCREQ`
  也在 `mtd/nand.h:41` / `:199`。它们可见**只因为**
  `include/linux/mtd/spinand.h:5` 包含了 `mtd/nand.h`——这条包含链是契约，
  所以工具查它，而不是假定它。
* `struct spinand_manufacturer` 在 **spinand.h**（不是 nand.h），
  字段 `id / name / chips / nchips / ops` 齐全，我们 overlay 的 `.id/.name/
  .chips/.nchips/.ops` 五个赋值都有对应。
* **7.x 把 DSA tagger 放在 `net/dsa/`**，不是 6.x 的 `drivers/net/dsa/`。
  `net/dsa/tag_zx279133_rtl8372n.c` 与 6.18 树**逐字节一致**
  （2774 B，sha256 前 16 位 `CEF5DB6538A1C789`）⇒ tagger 本身不用移植。
* **cnjn 公版已经带 `include/net/dsa_zx279133_rtl8372n.h`**（303 B，
  sha256 前 16 位 `1E5AFBCD378A1E88`，与 6.18 树那份逐字节一致），
  我们的 overlay 会**覆盖**它。宏名相同 ⇒ 不会编译不过；宏值有一处**有意**
  不同：

  | 宏 | 公版 | 我们 | 出处 |
  |---|---|---|---|
  | `ZX279133_RTL8372N_USER_PORT_MIN` | 4 | 4 | 相同 |
  | `ZX279133_RTL8372N_USER_PORT_MAX` | **7** | **8** | docs/92 §90：2.5G jack 是 9132 的第 5 个 UNI 口（DSA port 8），transport VID 55+8=**63**，超出公版的 55+7=62，公版会把这帧丢掉 |
  | `ZX279133_RTL8372N_TRANSPORT_VID_BASE` | 55 | 55 | 相同 |

  这正是 `post` 阶段要把两边逐个对照的原因：改这个值会改 tagger 在运行期
  接受哪些 VID，**不能靠编译通过来判定它对**。
* 板级 DTS 引用 13 个 label，在 7.3 的 `zx279133.dtsi` 里**全部可解析**；
  三个 `#include` 的 dt-bindings 头都存在；dtb 进了
  `arch/arm64/boot/dts/zte/Makefile`。

**未做**：

* 没有真正编译——本机没有 arm64 工具链，`Compile` 只能在 CI 里发生。
* `unim_spinand_manufacturer` 往 `spinand_manufacturers[]` 的登记**不在
  这个工具的检查范围**：那是 `scripts/configure-sk-d840n.sh` 在树备好之后
  做的三处编辑（`spinand.h` 的 extern 声明、`core.c` 的表项、Makefile 的
  `spinand-objs += unim.o`），脚本自己断言了这三处，所以在它改的地方查
  它就够了。

---

**已落地：第 1 片（IDM 实测修正）的寄存器部分**，见
`kernel-patches/7.3-local/100-sk-idm-measured-fixes.patch`：

* 控制字：bit 24 打开（`zx279133_idm_cfg_bit24` 0 → 1）、bit 13 去掉；
* `+0x0010` = 0x40（`g_idm_rx_bp_retev_num_th`，来自原厂 DT）；
* `+0x0014..+0x0034` 路由字 = 0x00010001；
* `+0x0038` = 0x00001000；`+0x0070` = 深度 − 0x18（24 个队列头）、
  `+0x0074/+0x0090/+0x0094` = 0；
* 中断掩码 `+0x0040` = 0，IRQ 路由 `+0x44/+0x48/+0x4c/+0x50` 照抄厂商
  `sg_idm_irq_mask_conf(0x71ffbfb 0x200000 0x400000 0x800404)`，
  `+0x006c` = 0x800；
* `+0x0490` = 0、`+0x2c8` 字段 = 0x12、`+0x0124` = 0x80、`+0x00bc` = 0；
* 头文件 `ZX279133_IDM_TX_CFG_DEPTH` 0 → 0x400。

打上之后，本机逐条比对确认：**两代之间剩下的写入差异只剩 BP 池布局**
（`+0x0108` 的 jumbo 池基址、`+0x010c/+0x0110` 的按深度环大小）。

### 7.7 一次改动打红了 6.18 发布线（已修）

**判决**：6e6992b2（系列号改为从内核树推导）**打红了 6.18 发布线**，
run 45010155031（head `3dbf3003`）在「Rust 1.96.1 and offline ONU
regression tests」这一步就红了，`Configure` 之后的步骤全被跳过。
原因已定位并修好。

**现象**：

```
FAIL: test_real_fragment_and_build_root_merge_is_idempotent (__main__.OffloadTests)
AssertionError: 1 != 0 : Missing SK-D840N target kernel config
```

**根因**：那个提交把 `skd840n_pin_offload_fragments` 的系列号来源从
写死的名字改成**内核树**，并把内核树设成了必填的第三个参数：

```sh
sk_off_series=$(skd840n_target_config_series "$sk_off_tree" "$sk_off_kernel") || {
	echo 'Missing SK-D840N target kernel config' >&2; return 1; }
```

而仓库自己的门禁测试是这么调它的：

```sh
skd840n_pin_offload_fragments "$2" "$3"     # 只有两个参数
```

**没有内核树** ⇒ 推导失败 ⇒ 报「缺 SK-D840N 内核配置」。
真实构建路径一直带着内核树，所以构建本身没坏，坏的是门禁测试——
而门禁测试恰恰是发布线上的**第一道**关卡。

**修法**：内核树仍然是首选来源，但不再是唯一来源。新增回退
`skd840n_target_config_series_from_fragments()`：没有内核树时，从
**subtarget 片段**（`target/linux/*/*/config-<series>`）取系列号。

回退**不能用 `target/linux/generic`**：cnjn master 的 generic 目录里
`config-6.12`、`config-6.18`、`config-7.3` **三个系列并存**，从那里取
是三选一的猜测。subtarget 目录只有一个（master 的 `zx279133/` 里
只有 `config-7.3`），万一出现多于一个，回退**拒绝而不是猜**。

**证据**（本机四条路径全部验证）：

| 场景 | 结果 |
|---|---|
| 内核树 = 7.3.4 | `7.3` |
| 内核树 = 6.18 fork | `6.18` |
| 无内核树，subtarget 一个片段 | `6.18` |
| 无内核树，subtarget 两个候选 | **拒绝**（不猜） |

`tests/test_sk_offload.py` 回到 10/10 通过（本地与 CI 同一份测试）。

**教训**：改一个**共享**脚本时，它的调用方不止构建路径——**仓库自己的
门禁测试也是调用方**。而门禁测试跑在发布线的最前面，所以它在发布线
上的优先级高于任何构建步骤。

---

### 7.8 run #4：主题残留的根因，和一次被推翻的「排除」

run #4（45012472909，head `1581854a`）在 Configure 失败，但这次的
**诊断输出是决定性的**：

```
.config:30:warning: override: TARGET_zte_zx279133_DEVICE_skyworth_sk-d840n changes choice state
#
# configuration written to .config
#
CONFIG_PACKAGE_luci-theme-bootstrap=y
Only Argon may be selected in the SK-D840N config
--- the themes in the resolved config ---
CONFIG_PACKAGE_luci-theme-argon=y
CONFIG_PACKAGE_luci-theme-bootstrap=y
--- the LuCI packages in the resolved config ---
CONFIG_PACKAGE_luci=y
CONFIG_PACKAGE_luci-light=y
...
```

**判决**：resolved config 里同时有 argon 和 bootstrap，**而且有 `luci`
元包和 `luci-light`**。这两条是 bootstrap 的来源。

**根因（这次查到底了）**：

```
include/target.mk（OpenWrt 自己，两个分支都有）：
  # For easy usage
  DEFAULT_PACKAGES.tweak:=
  	block-mount \
  	default-settings-chn \
  	kmod-nf-nathelper \
  	luci              ← LuCI 元包
```

而 luci 树里：

```
collections/luci        LUCI_DEPENDS:= +luci-light +luci-app-package-manager
collections/luci-light  LUCI_DEPENDS:= ... +luci-theme-bootstrap ...
```

**⇒ `luci`（默认包）→ `luci-light` → `luci-theme-bootstrap`。**

**为什么 6.18 线没有这个问题**：cnjn 的 `sr1010-zx279133` 分支在
`target/linux/zte/zx279133/target.mk` 里**清空**了 tweak 列表并给了自己
的清单：

```make
DEFAULT_PACKAGES.tweak:=
DEFAULT_PACKAGES += \
	dnsmasq ethtool ip-full \
	luci-base luci-mod-admin-full luci-theme-bootstrap \
	uhttpd uhttpd-mod-ubus \
	openssh-sftp-server
```

里面的 `luci-theme-bootstrap` 被 `scripts/sk-d840n-theme.sh` 的 sed 改成
argon，所以 6.18 线的 resolved config 里只有 argon。

**而 7.3 线构建自 cnjn 的 master，它的 `zx279133/target.mk` 根本没有
tweak 清单** ⇒ 上游默认的 `luci` 留下 ⇒ 主题检查失败。

**⇒ 修法**：7.3 线补一个 `overlays-73/target/linux/zte/zx279133/target.mk`，
照 6.18 的做法清空 tweak 并给出自己的清单（主题直接写 argon，不依赖 sed）。
CI 里加两条断言：overlay 文件里必须有 `DEFAULT_PACKAGES.tweak:=`；
resolved config 里**不得**有 `CONFIG_PACKAGE_luci=y`（否则主题检查可能是
因为错误的原因才通过的，镜像里会同时装两个主题）。

**这一轮被推翻的「排除」**：上一轮记过「已排除 luci feed 依赖差异
（两个 luci 树各 243 个构建文件，提到 bootstrap 的只有 4 处且两树完全
一样）」。**这个排除是错的**：

* 243 这个数字来自 **GitHub trees API 的截断**（recursive 查询有截断，
  脚本没有检查 `truncated` 标志）。用 `git clone` 取完整树后是
  **260 个构建文件**，多出来的那几个正是 cnjn 自己加进 fxfall/luci 的
  包（`luci-app-passwall`、`luci-app-rclone` 等）。
* 而且「两树完全一样」这个结论本身也不成立：两棵树的 `collections/luci`、
  `collections/luci-light`、`themes/luci-theme-bootstrap` 和 `luci.mk`
  **确实逐字相同**，但那不重要——**问题根本不在 luci 树里**，而在
  OpenWrt 树的 `DEFAULT_PACKAGES.tweak`。

**⇒ 方法论**：用「树 API 拉文件列表 + 逐个 raw 抓内容」做全量对比时，
**必须先确认列表没有被截断**，否则「两树一样」只是「两边都被截成了
一样的前 243 个文件」。

### 7.9 run #5 与 run #6：工作流自己解析失败，以及 Configure 通过

**run #5（2026-11-22，head `94335616`）报失败，但一个作业都没有。**
GitHub 在**解析工作流文件**失败时的行为就是：建一个 run、判它失败、不建任何
作业。所以唯一的证据只有 run 本身的结论。

根因是那一步的**未加引号的 YAML 标量里带了冒号和空格**：

```yaml
      - name: Gate: the resolved config carries exactly one theme
```

整个文件因此不可解析，那一轮**根本没走到 Configure**——本来是要验证
tweak 包修复的那一轮，什么也没验证。

修法是给标量加引号，外加一个门禁把这件事挡住：`tests/test_sk_workflows.py`
在**推仓库的这台机器上**检查两条线的形状——每个工作流文件都能解析、7.3 线
依赖的步骤都还在、门禁作业点名的测试文件都存在、7.3 线的路径过滤覆盖这条线
的输入（**补丁落在过滤之外就什么都不会构建，而那一轮绿灯什么都不能说明**）。
它接在两条线的门禁作业里，在任何构建之前跑。

> 仓库里早就有 `tests/check_workflow_yaml.py`，但它自己的注释写着「**没有**
> 接进 CI」，路径写死成某一台机器的 `/mnt/c/ugit/SK-D840N4/...`，而且只看
> 发布线那一条。它防不住这次的失败。

**run #6（head `edc3db7b`）把 Configure 走通了。** 顺序是：
`repository gates` 绿 → 准备 7.3.4 树 → 板级层预检 → 板级层拷入 → 板级层
复检 → OpenWrt 侧 overlay → `/dev/mem` 片段 → feeds → **Configure 通过**
→ 主题门禁通过 → Download → Compile。

**⇒ 7.8 的根因判定成立**：`DEFAULT_PACKAGES.tweak` 清空之后，resolved config
里不再有 `CONFIG_PACKAGE_luci=y`，`luci-theme-bootstrap` 也不再出现，主题
只剩 argon。**7.3 线第一次拿到可编译的源码树。**

### 7.10 run #6 的 Compile 失败，和一个诊断盲区

**run #6（head `edc3db7b`）在 Compile 步失败，退出码 2。**
`initramfs` 作业跑了 50 分 14 秒，`flash` 作业同样。前面 16 个步骤全绿
（含 Configure、主题门禁、Download packages），失败点在 `make` 内部。

**但诊断是空的**：annotations 只有 GitHub 自己那句
「Process completed with exit code 2」。原因是

- 作业日志 `/actions/jobs/<id>/logs` 返回 **403**；
- 构建日志 artifact `/actions/artifacts/<id>/zip` 返回 **401**
  （列表公开，下载要鉴权）；
- 浏览器打开作业页显示「**Sign in to view logs**」。

**⇒ 盲区不在编译上，在诊断管线上。** 6.18 发布线早就有解法：Compile 步把
编译器自己那几行**逐条重发成 `::error::cc:` annotation**，
并把同样的内容写进 job summary。**annotation 是公开的，作业日志不是**——
这一条差别决定了「能不能在没登录的机器上查出一个失败的原因」。
7.3 线那一步是照搬 6.18 写的，但没有带这段诊断。

修法：把 6.18 的做法搬进 7.3 的 Compile 步——

```bash
{
  grep -naE "fatal error:|\berror:|Error [0-9]+|implicit declaration|undefined reference|undefined symbol|No such file|not defined" \
    ../compile.log 2>/dev/null | head -n 12 || true
} | while IFS= read -r line; do
  echo "::error::cc: $(printf '%s' "$line" | sed -e 's/%/%25/g' -e 's/#/%23/g' | tr -d '\r')"
done
```

**逐条发而不是发一个转义后的大块**：GitHub 对一个步骤只保留前 10 条 annotation，
而一个 3500 字符的转义块既是**一条**（十行的上限对它不适用），读者也没法读。
同样的内容再写进 `$GITHUB_STEP_SUMMARY`——那里没有条数上限。

本机用一份假的编译日志验证过这段 shell：它确实逐条吐出带行号的错误行，
`%` 与 `#` 的转义也对。

**⇒ 第一次修完还是不够，run #12 给出了第二层。** run #12（head `dabf958`，
第一次带诊断的那一轮）的 annotation 里有内容了，但只有两行：

```
cc: 114:make: *** [.../openwrt/include/toplevel.mk:233: world] Error 1
```

**仍然不是编译器的原话。** 原因有两个，都与 OpenWrt 的日志布局有关：

1. **顶层日志里没有编译器的输出。** OpenWrt 把每个包自己的编译输出放在
   `logs/` 下，顶层日志只留摘要——7.3 那轮的 `compile.log` 一共**只有 114 行**，
   而它跑了 50 分钟。**编译器的原话在 `openwrt/logs/` 里，不在 `compile.log` 里。**
2. **grep 是大小写敏感的。** OpenWrt 自己的失败行是
   `ERROR: failed to build package/linux-7.3.4 (...)`——**大写的 ERROR**，
   而模式里写的是小写 `\berror:`，于是连「是哪个包失败」这一句也没匹配上。

第二版把两处都补上：`-i` 让搜索不区分大小写、并且**先读 `logs/` 下的包日志**
（编译器的话在那里），再读顶层日志（哪个包失败）。本机用一份假的包日志树
验过：annotation 的第一条就是编译器的原话，第二条是失败的包名。

> 这一层的教训和上一层一样：**诊断要按失败信息实际所在的位置去找**。
> 6.18 线只读顶层日志就够了，是因为它那条线的失败消息本来就落在顶层日志里；
> 7.3 线照搬过去就会漏。两条线长得像，失败的位置不一样。

### 7.11 run #13：编译失败的真因不在内核，在 Rust 包的工具链

run #13（head `3f90c50`）第一次让诊断说出了**是哪个包**失败：

```
cc: 110:    ERROR: package/libs/libzxpon-omci failed to build.
cc: 112:make: *** [.../openwrt/include/toplevel.mk:233: world] Error 1
```

**判决：失败的不是内核，是我们自己的用户态包，而且失败与 7.3 无关。**

证据链：

1. **失败的是 `package/libs/libzxpon-omci`**，它是本仓库
   `overlays/package/libs/libzxpon-omci/` 里的一个 **Rust** 包（`omci/Cargo.toml`、
   `Cargo.lock`，16 个 crate，直接依赖 `aes` 与 `cmac`）。
2. 它的 `Build/Compile` 是这样的：
   ```make
   ZX_CARGO?=$(shell command -v cargo)
   define Build/Compile
   	cd $(PKG_BUILD_DIR) && RUSTC="$(dir $(ZX_CARGO))rustc" \
   		RUSTFLAGS="-C target-cpu=cortex-a53" $(ZX_CARGO) build --release --locked \
   		--target aarch64-unknown-linux-musl
   endef
   ```
   工具链**只从环境变量 `ZX_CARGO` 来**，机器上找不到 `cargo` 就没有第二来源。
3. **6.18 发布线有装工具链的步骤，7.3 线没有。**
   `build-sk-d840n.yml` 的 build 作业里有「Rust 1.96.1 and offline ONU regression tests」：
   ```sh
   rustup toolchain install 1.96.1 --profile minimal
   rustup target add --toolchain 1.96.1 aarch64-unknown-linux-musl
   export ZX_CARGO="$(rustup which --toolchain 1.96.1 cargo)"
   echo "ZX_CARGO=$ZX_CARGO" >> "$GITHUB_ENV"
   ```
   `build-sk-d840n-73.yml` 里**一个 rust 字都没有**（逐行比过两个工作流的步骤表）。
   ⇒ 7.3 线上 `ZX_CARGO` 为空 ⇒ `cargo build` 没有 cargo 可执行 ⇒ 包构建失败 ⇒
   顶层 `make` 以 `world Error 1` 收场。
4. **这解释了 7.3 线从来没编成功过**：run #1 到 #13 的 Compile 失败是同一件事，
   与我们搬的五个片无关。run #6（只有 IDM 三片）与 run #9（加第 3/4 片）失败在
   同一处，也正是因为那时就已经缺工具链了。

**⇒ 修复**：7.3 线补上同样的步骤，并**带断言**（cargo 存在、rustc 在旁边、
musl target 已装），缺一件就地失败，而不是把这件事留到 50 分钟之后、包日志里。

**⇒ 顺带把诊断的 pattern 补宽**：原来的 pattern 里没有 `command not found`，
也没有 `error[`——cargo 的错误长成 `error[E0308]: ...`，两样都匹配不上，
所以包日志里明明有原因，annotation 里却只有「哪个包失败」。现在 pattern 里有
`error\[`、`command not found`、`not found`、`failed to resolve`、`cannot find`。

**⇒ 门禁**：`tests/test_sk_workflows.py` 新增一节「the OMCI engine's toolchain is
installed on both lines」，两条线都要有 `rustup toolchain install`、要有
`aarch64-unknown-linux-musl`、要有 `ZX_CARGO=`。反向验证过：把 7.3 的
`rustup toolchain install` 换掉，门禁立刻转红。

**⇒ 本机这一轮的收获**：把 7.3 树用宿主 clang 做 `-fsyntax-only` 的检查器
（`scratch/kcheck.py`）打通之后，**我们补丁碰过的 12 个驱动文件全部通过**
（`np.c`/`idm.c`/`switch.c`/`pon.c`/`zxpon.c`/`omci.c`/`netdev.c`/`main.c`/`mac.c`/
`offload.c`/`host.c`/`lan-conduit.c`），cnjn 那 39 个补丁碰过的 40 多个 `.c`
也全部通过，DTS 预处理通过。**⇒ 内核这一侧本来就没有编译错误**，
这与「失败的是 `libzxpon-omci`」互相印证。检查器还能编出目标码并按 modpost
的规则查符号闭合（11 条疑似缺符号全是宏生成导出造成的误报，逐条核实过）。

---

### 7.12 7.3 线的驱动是模块：镜像里不会有 NPU，而且 `pin_y` 会被 kconfig 夹掉

**判决**：7.3 线**即使编译过了，出来的镜像里也没有 NPU 驱动**。
这不是「kmod 依赖链要重新确认」这种软问题，是硬缺口；而 `configure-sk-d840n.sh`
里那句 `pin_y ZTE_ZX279133_SWITCH` **在 7.3 线上不生效**——它写的 `=y` 会被
kconfig 静默夹成 `=m`。已修：7.3 线把三个符号钉成 `=y`，与 6.18 发布线一致。

证据链：

1. **cnjn 的 `config-7.3` 把驱动定成模块。**
   `target/linux/zte/zx279133/config-7.3:2513` 是 `CONFIG_ZTE_ZX279133_ETH=m`，
   `:2517` 是 `CONFIG_ZTE_ZX279133_RTL8372N=m`。
   合并后的三个片段里，`ZTE_ZX279133_SWITCH` **一个字都没有**（既不是 `=m`
   也不是「is not set」），它是 `scripts/configure-sk-d840n.sh` 的 `pin_y` 加进去的。
2. **本仓库没有任何机制加载这些模块。**
   - `overlays/`、`overlays-73/` 下没有 `kmod-*` 包（只有 `mtd-rw` 一个自研包）；
   - `overlays/target/linux/zte/zx279133/base-files/` 下**没有** `etc/modules.d/`；
   - OpenWrt **不会**把树内模块放进 rootfs——只有被 `kmod-*` 包声明过的模块才会
     被装进 `/lib/modules/<版本>/`，而没有 `/etc/modules.d/` 条目就没人 `modprobe`。
   ⇒ `=m` 的后果不是「模块没自动加载」，而是**镜像里根本没有 `npu.ko`**。
3. **6.18 发布线不是这样，而且有实机读数。**
   `docs/07-sk-d840n-status-20261014.md:335` 记着从这台板子的 `/proc/config.gz` 里读到的
   `CONFIG_ZTE_ZX279133_ETH=y  <-- 网卡驱动内建`；同一份记录里
   `/lib/modules/6.18.38+/` 下 **0 个 `.ko`**、`/etc/modules.d` **整个不存在**
   （`docs/07:11358-11360`）——所有需要的驱动都是内建的，镜像里一个可加载模块都没有。
   ⇒ 6.18 线能在这块板子上跑起来，靠的就是「内建」，不是「打包 + 加载」。
4. **厂商固件靠的是另一套东西，我们没有。**
   从目标固件的 initrd（`fit/initrd-1.bin`，gzip + newc cpio）里挖出来的
   `etc/modules.d/30-zte-npu` 内容是 `npu vlan_offload=1`，
   `etc/modules-boot.d/30-zte-npu` 是指向它的符号链接；另有
   `etc/modules.d/32-zte-npu-pon`（`npu-pon`）与
   `etc/modules.d/42-zte-fnoffload`（`fnoffload vlan_offload=1`）。
   那是**厂商自己的 rootfs** 里的 OpenWrt 模块装载机制，
   而我们的 7.3 线是从 OpenWrt 现装的包树里长出来的，没有这一步。
5. **`pin_y` 为什么救不了。**
   `ZTE_ZX279133_SWITCH` 是 tristate 且 `depends on ZTE_ZX279133_ETH`。
   kconfig 会把「依赖是模块」的 tristate 夹成 `=m`：
   `scripts/kconfig/symbol.c:471-474` 取的是
   `EXPR_AND(用户给的值, 符号的可见性)`，而依赖为模块时该符号的可见性是 `mod`
   而不是 `yes`，所以 `EXPR_AND(yes, mod) = mod`。
   ⇒ `=y` 被夹成 `=m`，**全程不报任何错**。
6. **板级 DTS 确实点了这个交换芯片。**
   `arch/arm64/boot/dts/zte/zx279133-sk-d840n.dts` 里有
   `switch { compatible = "zte,zx279133-switch"; ... }`，
   而 `zx279133-switch.c` 的 `of_match_table` 正是这个 compatible。
   模块不会被自动加载 ⇒ 这个节点**永远不会 bind** ⇒ 四个 DSA 用户口不会出现。

**⇒ 修复**：在 7.3 工作流里加一步「Build the zte drivers into the kernel」，
把 `ZTE_ZX279133_ETH`、`ZTE_ZX279133_LAN_CONDUIT`、`ZTE_ZX279133_SWITCH`
和 `ZTE_ZX279133_FNOFFLOAD` 写进**最后合并的那个片段**
（`target/linux/zte/zx279133/config-7.3`），每写一个就断言一次；
再在 Compile 之后加一道门禁，对着**合并后的内核 `.config`**（不是被改的片段）
检查这四个符号是 `=y`。

**⇒ 第四个符号（flow offload）为什么也钉**：
cnjn 的 7.3 树把 offload 从 NPU 驱动里拆成独立的 `fnoffload.o`
（`obj-$(CONFIG_ZTE_ZX279133_FNOFFLOAD) += fnoffload.o`），
而 6.18 树把它放在复合目标 `zx279133-y` 里面（`kernel-overlays/.../Makefile:12`）
⇒ 6.18 线上它是**内建**的，7.3 线上它是模块 ⇒ 不在镜像里。
它自己的依赖 `NF_FLOW_TABLE` 由 `configs/sk-d840n-offload.config`
（`CONFIG_NF_FLOW_TABLE=y`）在 Configure 步钉成内置
（`scripts/sk-d840n-offload.sh` 的 `skd840n_pin_offload_fragments`
把整份档子写进三个片段），所以 `=y` 是够得着的。
不钉它，**整份 netfilter 档子就白进内核了**——那几十个符号是给 flow offload 用的。

**⇒ 四个驱动并进同一个内核会不会符号冲突**：本机用编译预言机编出的目标码
直接查过——`zx279133-offload.c.o`、`zx279133-switch.c.o`、
`zx279133-lan-conduit.c.o` **一个全局符号都不定义**（里面的函数全是 `static`，
`nm` 出来全是小写 `t`/`d`/`b`），全局名字只来自 NPU 驱动自己的那些目标码
（`np.c.o` 有 23 个 `T`）。⇒ 四个链接单元合成一个镜像不会撞名。
（`scratch/dup_syms.py` 就是干这个的。注意：**函数的 `static` 常常写在上一行**，
用行级正则找「非 static 定义」会看漏——目标的 `nm` 结果才是准的。）

**⇒ 为什么改在 7.3 工作流里而不是 `scripts/configure-sk-d840n.sh`**：
那个脚本两条线共用，`KCFG` 在 7.3 线上是 `config-7.3`、在 6.18 线上是 `config-6.18`；
在脚本里加 `pin_y ZTE_ZX279133_ETH` 会**同时改动 6.18 发布线的配置片段**。
6.18 线本来就是 `=y`（上面第 3 条有读数），改了也是空操作，但发布线不该由
一次 7.3 线的排查去碰。放在 7.3 工作流里，6.18 发布线一个字节都不动。

**⇒ 门禁**：
- `tests/test_sk_workflows.py` 新增一节「the 7.3 line builds the zte drivers into
  the kernel」：钉的三个符号一个都不能少、必须写进最后合并的片段、
  门禁必须读**合并后的 `.config`**（不许读片段）、必须断言 `=y`（不许只断言「存在」）。
- **反向验证做过**：五处变异逐个转红且转红在正确的检查上——
  ① 不钉 `LAN_CONDUIT`；② 钉到 `generic/config-7.3` 而不是 subtarget 的；
  ③ 门禁改读片段；④ 门禁改成「存在即可」；⑤ 门禁不查 `SWITCH`。
  （第一次跑时第 ② 项**没有转红**，因为 `/dev/mem` 那一步里也有同一个片段名，
  变异打错了地方；把 pin 步的变量改名成 `DRVCFG` 之后才真正打到点上。
  **这正是「门禁必须做反向验证」的意义：自己写的门禁自己也可能测不到点上。**）

**⇒ 本机这一轮的收获**（四个 scratch 工装，都是为了不花 50 分钟去问 CI）：
- `scratch/kobjmap73.py`：把 Kbuild 的 `obj-$(CONFIG_X)`、复合目标（`npu-y`）
  和条件成员（`npu-$(CONFIG_X)`）解出来，回答「**哪些 `.c` 真会被编进内核**」。
  写这个是因为「`obj-$(CONFIG_X)` 对应的 CONFIG 没开 ⇒ 那个文件被静默跳过」
  这件事编译器不会告诉你，clang 检查器也不会——它只查被点名的文件。
  本机跑出来的结论：7.3 树里 `zx279133-switch.c`、四个 `npu-y` 成员
  **本来都会被编**（前提是 CONFIG 打开），`dinghai/en_pf.c` 与
  `zx279133-native-debug.c` 不会（对应 CONFIG 没开）。
- `scratch/cpio_pick.py`：按 magic 自动选解压器（gzip/xz/zstd/lz4）后读 newc cpio，
  从目标固件的 initrd 里把 `/etc/modules.d/*` 挖出来——厂商怎么加载模块就是这么看见的。
- `scratch/pin_step_check.py`：把工作流里那一步的 shell **从 YAML 里原样取出来**
  （不复制，避免两边漂移），在 cnjn 的真实 `config-7.3` 副本上跑一遍，
  验证「三种写法（有值 / is not set / 根本没有）」都能被改成 `=y`。
  第一次跑就抓到我校验脚本自己写反了（查的是改**前**的行）——**工装也会骗人**。
- `scratch/reverse_check_workflow.py`：门禁的反向验证器，五处变异逐个跑。

**未做 / 遗留**：
1. **`ZTE_ZX279133_RGMII` 在 7.3 树里没有 Kconfig。**
   `scripts/configure-sk-d840n.sh` 的 `pin_y ZTE_ZX279133_RGMII` 会往
   `config-7.3` 里写一个**不存在的符号**，defconfig 静默丢掉。
   ⇒ 6.18 线上那个「RGMII MAC 寄存器块只读探针」在 7.3 上不存在。
   这是取舍还是缺口，等对拍时看能不能读寄存器再判。
2. ~~**内核体积。**~~ **已查实：7.3 装得下，而且余量比 6.18 线大得多。**
   分区是固定的 8 MiB（8,388,608 B），装的是**压缩后**的内核
   （uImage 头 64 B 之后那段 gzip 流，zopfli 压的）。
   本机实测（`scratch/kernel_fit.py`：**逐个 gzip 成员量**，
   不拿 uImage 头的 size 字段当压缩后的大小）：

   | 镜像 | 压缩载荷 | 8 MiB 余量 | 解压后的 Image |
   |---|---|---|---|
   | **7.3**（`staging/trial73/flash/`，run #36/#37 那一批的） | 5,811,931 B | **+2,576,677 B（2.46 MiB）** | 14,051,336 B |
   | 6.18 发布线（live `staging/trial/`） | 8,333,285 B | +55,323 B（54 KiB） | 21,256,200 B |
   | 6.18 回滚件（厂商 4.19） | 4,814,713 B | +3,573,895 B | 10,676,232 B |

   ⇒ 初稿那句「7.3 的底子比 6.18 大 ⇒ **flash 那一路可能撑不下**」
   **不成立**：**7.3 的内核比 6.18 的小 2.5 MB（压缩后）**。
   本轮后加的四片补丁（160/170/180/190，加起来几万字节源码）
   编译后只是几 KB 量级 ⇒ 离 8 MiB 还差得远。
   **仍然是 CI 说了算**：7.3 线的「Collect the images」步会把内核镜像的
   字节数打进 annotation，而装不下时是 `Image too big` 报错，不是静默失败。
3. **`ZTE_ZX279133_RTL8372N` 仍是 `=m`。** 它是 SR1010 那块外挂 RTL8372N 的驱动，
   这块板子上没有对应节点，模块不加载即可，没有副作用。

---

### 7.13 7.3 线的镜像阶段缺 zopfli：内核编完了也会死在打包上

**判决**：7.3 线**即使内核编译成功**，镜像阶段也会失败，
因为压缩内核的脚本要 `import zopfli.gzip`，而这条线既没装 zopfli，
也没告诉镜像 profile 该用哪个 python。

证据链：

1. **镜像的内核是用 zopfli 压的，不是 gzip。**
   `overlays/target/linux/zte/image/Makefile:15-18` 的 `Build/skd840n-gzip` 是
   ```make
   define Build/skd840n-gzip
   	$(SKD840N_HOST_PYTHON) -u $(TOPDIR)/scripts/skd840n-mkgzip.py $@ $@.new 10
   	mv $@.new $@
   endef
   ```
   而 `SKD840N_HOST_PYTHON ?= python3`——**默认是系统的 python3**。
2. **`scripts/skd840n-mkgzip.py` 顶层就是 `import zopfli.gzip`，没有兜底。**
   为什么非要 zopfli：这块板的 `kernel` 分区是固定 8 MiB（8,388,608 字节），
   脚本自己的注释记着——`gzip -9` 最好的一次是 8,397,374 字节，**还差 8,766 字节**
   （zlib 其它策略更差：Z_RLE 13.4 MB、Z_FIXED 9.4 MB、Z_HUFFMAN_ONLY 13.8 MB、
   Z_FILTERED 9.3 MB），而 zopfli 十次迭代加分块是 8,069,537 字节。
   `mtd write` 会**无声地截断**超长的镜像，只有 `mtd verify` 才看得出来。
   ⇒ gzip 不是「更好的选择」，是**装不下**。
3. **6.18 发布线有这一步，7.3 线没有。**
   `build-sk-d840n.yml:180-190` 有「Prepare firmware tools and upgrade regression tests」：
   建 venv、`pip install 'zopfli==0.4.3' 'PyYAML==6.0.3'`、
   `echo "SKD840N_HOST_PYTHON=$PY" >> "$GITHUB_ENV"`。
   `build-sk-d840n-73.yml` 里 **zopfli 一个字都没有**，
   `SKD840N_HOST_PYTHON` 也没有 ⇒ 镜像阶段必然
   `ModuleNotFoundError: No module named 'zopfli'`。
4. **这个失败点很贵。** 它在 `make` 的**最后**（`target/install` 出镜像），
   也就是说内核已经编译完、用户态包也装完了，才在最后一步炸。
   前 13 轮之所以没报这个错，是因为它们更早就死在 `libzxpon-omci` 上。

**⇒ 修复**：7.3 线补上同样的一步（「The firmware tools」），
并带**两条断言**：zopfli 的绑定能被 import；
`tools/mkgzip.py` 自己能被 import（它的 import 在顶层，所以「模块能加载」就是那条断言）。
还断言 `SKD840N_HOST_PYTHON` 真的进了 `$GITHUB_ENV`——不然后面那两条都白做。

**⇒ 门禁**：`tests/test_sk_workflows.py` 新增一节「the 7.3 line has the tools the
image stage needs」：必须钉 `zopfli==0.4.3`、必须把 `SKD840N_HOST_PYTHON=$PY`
写进 `GITHUB_ENV`、必须检查镜像自己的脚本能被加载。
反向验证里对应三处变异，逐一转红在对应的检查上。

**⇒ 本机这一轮的收获**：`scratch/mkgzip_import_check.py` 在本机跑那条断言
（本机没有 zopfli，用一个桩替身顶上——要验的是**模块加载那段代码**，不是编码器），
并且**同时验了反面**：一个顶层 import 会失败的脚本**必须**加载失败，
否则这条断言就是空的。

---

### 7.14 7.3 线没有 6.18 那道「合并配置」门禁：镜像会编出来，但不一定能用

**判决**：7.3 线此前只有一道「四个 zte 驱动符号是不是 `=y`」的门禁，
而 6.18 发布线有一道更宽的门禁：它读**实际出货那颗内核**的合并 `.config`，
断言 **39 个符号**是 `=y`，另外断言优化级别、`PANIC_TIMEOUT` 和内建命令行。
⇒ 一份丢了 Realtek PHY、丢了整族 netfilter、或者带着 SR1010 的 `mtdblock7`
内核命令行的配置，**照样能编出一个「构建成功」的镜像**，
而这几种失败在板子上全都长得像「内核起不来」。
已补上这道门禁；补之前先在本机证明了它在这棵 7.3 树上**不会永远变红**。

#### 7.14.1 证据：两条线的步骤差在哪

`scratch/stepdiff.py`（新增）把两条工作流的步骤清单逐条对照：
6.18 线 44 步、7.3 线 30 步。6.18 独有的步骤里，有三道问的是
「镜像到底好不好用」，而不是「镜像有没有编出来」：

| 6.18 独有步骤 | 它问什么 |
|---|---|
| `Assert the kernel really has the PHY driver`（`build-sk-d840n.yml:1202-1342`） | 合并 `.config` 里那 39 个符号是不是 `=y`；`CC_OPTIMIZE_FOR_PERFORMANCE` 是不是没开；`PANIC_TIMEOUT` 是不是 0；`CONFIG_CMDLINE` 是不是那条钉死的命令行；有没有 `mtdblock7` |
| `Gate: ONU UI, protocol service and firewall4 reached the image` | 根文件系统里有没有 LuCI、协议服务、firewall4 |
| `Gate: every dtb compatible has a driver in this kernel` | 板级 DTS 里每个 `compatible` 在这颗内核里是不是真有驱动 |

7.3 线当时**一道都没有**。第一道是本节补的；第二、三道见第六节未做项。

#### 7.14.2 一个曾被挂着的未决项，现在有答案

「7.3 线要不要把整套 netfilter 也钉成内置（6.18 的做法）？」——**答案是：已经是了。**

- `configs/sk-d840n-offload.config` 里有 **49 个** `=y` 符号，整族 netfilter 都在里面
  （`NETFILTER` / `NF_CONNTRACK` / `NF_NAT` / `NF_TABLES` / `NFT_*` / `NF_FLOW_TABLE` /
  `NFT_FLOW_OFFLOAD` / `VETH` / `NET_SCH_*`）。
- `scripts/sk-d840n-offload.sh` 的 `skd840n_pin_offload_fragments` 把它**写进三个
  `config-7.3` 片段**（`target/linux/generic`、`zte`、`zte/zx279133`），
  每个片段里都先删掉旧行再追加 `=y`。
- 所以「裸片段里 netfilter 是 `=m`」这件事只描述 **Configure 之前**的状态；
  Configure 之后（也就是编译时看到的那份配置）它们全是 `=y`。
  本机预言机重放整条序列后实测：39 个必需符号 + 4 个 zte 符号，**全部 `=y`**。

⇒ 换句话说，7.3 线在 netfilter 这件事上**从来没有落后过 6.18 线**；
落后的只是「有没有一道门禁去确认它」。

#### 7.14.3 「写了 `=y`」不等于「会是 `=y`」

kconfig 把请求值和符号的**可见性**取 AND（`scripts/kconfig/symbol.c`）：
依赖是模块，tristate 就被夹成 `=m`，bool 就被夹成 `=n`——**全程无警告无报错**。
所以要证明某个符号真的是 `=y`，必须对着**整条依赖链**证明，不能对着被改的片段证明。

本机为此做了两件工具（都在 `scratch/`，不进仓库）：

- `scratch/cfg73_sim.py`：**重放整条配置序列**并产出合并后的 `.config`。
  它的每一个钉子都是从仓库自己的文件里**读出来**的，不是抄在这里的：
  三个 `config-7.3` 片段来自 cnjn 的克隆，工作流的 `/dev/mem` 编辑、
  四个驱动钉子来自 7.3 工作流，`pin_y` 名单、`CC_OPTIMIZE_FOR_SIZE`、
  `PANIC_TIMEOUT=0`、`CONFIG_CMDLINE` 来自 `scripts/configure-sk-d840n.sh`，
  49 个 netfilter 符号来自 `configs/sk-d840n-offload.config`。
  合并规则是 kconfig 的规则：**后写的赢**。
- `scratch/kdeps73.py`：拿**树自己的 Kconfig 文件**走依赖闭包，
  按 kconfig 的三值逻辑求值（`y`=真、`m`=模、`n`/未定义=假；
  `!` 翻转真假但不动模；`&&`/`||` 按 kconfig 的规则归约）。

**结论：47 个符号（39 必需 + 4 zte + 4 抽查）全部 `value=y` 且 `visibility=yes`，
没有任何一个符号的依赖链上是 `=m`** ⇒ 它们在 7.3 线上**不会被夹**，
这道门禁**不会永远变红**。

> 预言机自己也错了四次，每一个都记在这里，因为它们全都让结论**看起来比实际更绿**：
> ① `fragments[2] = subtarget` 把后来的 offload 钉子覆盖回去了；
> ② `pin()` 会再加一次 `CONFIG_` 前缀，于是 `CONFIG_CMDLINE` 变成了
> `CONFIG_CONFIG_CMDLINE`；
> ③ `arch/um/Kconfig`（User Mode Linux，不参与 arm64 构建）也定义了
> `MAGIC_SYSRQ`，而且 `depends on MCONSOLE`，解析器先抢到了它；
> ④ 未在本树定义的符号被当成「真」，而 kconfig 里它是 `n`——
> 于是 `depends on !UML` 被算成不成立。
> 前两条让 22 个符号看起来是红的，后两条让 `MAGIC_SYSRQ` 看起来是红的。
> **教训**：预言机的结论要自己挑错，数字对不上时先怀疑工具。

#### 7.14.4 ⇒ 修复

7.3 线新增一步 **`Gate: the merged kernel config carries the board requirements`**：
读合并后的内核 `.config`，断言

- 那 **39 个符号**是 `=y`（清单**与 6.18 线逐字相同**）；
- `# CONFIG_CC_OPTIMIZE_FOR_PERFORMANCE is not set`；
- `CONFIG_PANIC_TIMEOUT=0`；
- `CONFIG_CMDLINE="console=ttyAMA0,115200n8 root=/dev/mtdblock5 rootfstype=jffs2 ro rootwait clk_ignore_unused pd_ignore_unused"`；
- 配置里**不得出现** `mtdblock7`；

失败时用 `::error::` 逐条报出**那个符号的实际状态**（`=y` / `=m` / `is not set` /
`.config` 里根本没有），并且把整张清单的状态一起打出来——
这四种情况从外面看一模一样，而它们是四个不同的原因。
仪器类符号（`DEVMEM` / `DETECT_HUNG_TASK` / `IKCONFIG_PROC` / `VETH` …）
照 6.18 的做法**只警告不拦**。

#### 7.14.5 ⇒ 门禁与验证

- `tests/test_sk_workflows.py` 新增一节「the 7.3 line asserts the release line's
  kernel requirements」。它**不是**把 39 个名字抄一份——抄的会漂。
  它把两条工作流里的 `required="..."` 各自读出来**逐字比较**，
  所以任何一边改了清单而另一边没改，门禁立刻转红。
  另外还断言：门禁读的是合并 `.config`（不是被钉的片段）、
  断言的是 `=y`（不是「出现过」）、并且 `-O2` / `PANIC_TIMEOUT` /
  内建命令行 / `mtdblock7` 四条都在。
- **反向验证**：新增 7 处变异（少一个符号、把 `=y` 放宽成「出现过」、
  改去读片段、去掉 `-O2` 拒绝、去掉 panic 拒绝、改掉内建命令行、
  整步删掉），逐个转红在**指定**的那条检查上。连同原有的 15 处，共 **22 处**全绿。
- **行为级演练**（新增 `scratch/boardgate_check.py`）：把工作流里那一步的 shell
  原样取出来，喂给它**预言机产出的那份真实合并配置**——
  ①必须通过（这证明门禁接受这条线真要编出来的那份配置）；
  然后逐个破坏：Realtek PHY 变模块 / netfilter 变模块 / 优化级别变 `-O2` /
  `PANIC_TIMEOUT` 变 1 / 命令行改成 `mtdblock7` / 符号在 `.config` 里根本不存在 /
  压根没有 `.config`——七种都必须转红，**并且红的原因要说对**。
  本机七关全绿。

#### 7.14.6 未做

6.18 那道 `Gate: ONU UI, protocol service and firewall4 reached the image`
**只搬了一半**：搬过来的是「包是不是真的装进了镜像」这一半
（7.3 线的新步 `Gate: the image carries the packages the board needs`，
读 `bin/targets/**.manifest`，要求 `zxpond` / `libzxpon-omci` /
`luci-app-sk-d840n-pon` / `luci-mod-system` / `firewall4` / `nftables` /
`tc-tiny` / `luci-app-firewall` / `rpcd-mod-ucode` / `ucode-mod-fs` 都在里面）；
**没搬的是** staged root 的文件清单、主机侧 ucode 解释器、
firewall4 自己的规则生成器测试套件——那三项问的是**发布线的产品面**，
不是这次迁移，而它们失败的话 7.3 线连镜像都出不来。

6.18 的另一道（`Gate: every dtb compatible has a driver in this kernel`）
**已经搬了，但不是照抄**：见 7.15。

### 7.15 dtb compatible 有驱动：这道门禁不能照抄，所以重写了一遍

**判决**：6.18 线有一道门禁问「板级 DTS 里每个 `compatible` 在这颗内核里是不是
真有驱动」。它**不能照抄**到 7.3 线上——照抄会因为一个节点变红。
但它问的问题恰恰是 7.3 线最该问的，所以**重写了一个本仓库自己的工具**。

#### 7.15.1 为什么不能照抄

6.18 那道门禁扫的是 **编译出来的 dtb**（`dtc -I dtb -O dts`），并且对
**每一个** `zte,zx279133-*` 的 compatible 做**整行匹配**
（`grep -Fxq`，不是子串——因为子串搜索会把 `zte,zx279133-pon` 判成"存在"，
只因为 `zte,zx279133-pon-serdes` 存在）。

本机把这道门禁的**内容**先查了一遍（`scratch/dtb_compat73.py`，读板级 DTS 源码，
因为本机没有 dtc）：7.3 板级 DTS 一共 **8 个 compatible**，
其中 7 个在这棵 7.3 树里都有驱动，**只有 `zte,zx279133-rgmii` 没有**——
而那正是八之五.9 判明「取舍不是缺口」的那一项（6.18 有只读探针，7.3 没有）。

⇒ 照抄会在这一个节点上变红。所以例外清单必须**明写理由**，
而不是把检查放宽。

#### 7.15.2 新工具问的是更强的问题

`tools/check-sk-dtb-compat.py` 问的不是「驱动在不在树里」，而是
**「compatible 字符串在不在已编译的内核镜像里」**：

- 驱动是**内建**时，它的 `of_match_table` 字符串在 `vmlinux` 里；
- 驱动是**模块**时，字符串在 `.ko` 里，不在 `vmlinux` 里。

⇒ 所以这道门禁同时是「驱动有没有退回成模块」的探测器，
而那正是 7.3 线**已经犯过一次**的错（NPU 驱动 `=m` ⇒ 镜像里根本没有 NPU，
也没有任何东西会加载它）。

#### 7.15.3 写这个工具时抓到的四个坑（都在设备树解析上）

1. **节点可以带标签**：`nppt: ethernet@19000000 {`。正则不认标签，
   这个节点就**根本没被打开**，它的 compatible 记到了**父节点**头上——
   而父节点是 `soc`。
2. **板级树用 `&label { status = "okay"; }` 打开节点**。这个覆盖必须落到
   它命名的那个节点上；不落的话，nppt 节点会保留 dtsi 里的 `"disabled"`，
   于是门禁**跳过它**——而那恰恰是整道门禁最该看的一个节点。
   这是**假放过**，比假报错更糟。
3. **compatible 列表可以跨行**：`compatible = "zte,zx279133-uart", "arm,pl011",`
   后面还有几行。只看单行会漏掉整个 uart 节点。
4. **注释里也会出现 compatible**：板级树把厂商树的那一段**引用**在注释块里，
   不剥注释就会凭空多出一个节点。

另外两条是**规则**而不是 bug：
5. **只查 `zte,zx279133-*` 这一族**。设备树里有大量**不是**设备 compatible 的
   字符串——`arm,cortex-a53` 是 CPU 类型，cpu 驱动用 `cpu_type` 匹配，
   根本没有 `of_match_table`；要求它有驱动字符串是在要求不可能的事。
   6.18 那道门禁画的是同一条线，而这一族正是风险所在（每一个都是我们手搬的，
   或者我们依赖的）。
6. **有 fallback 的节点由 fallback 服务**：primary 没有驱动但 fallback 有，
   不是错误（内核会绑到 fallback）；fallback 也没有才是错误。

#### 7.15.4 ⇒ 门禁与验证

- 7.3 线新增步 **`Gate: every compatible the device tree names has a driver`**，
  跑 `python3 tools/check-sk-dtb-compat.py --dts <板级 DTS> --image <编出来的 Image>`。
- 例外清单只有一条（`zte,zx279133-rgmii`），**带理由**；
  契约测试**直接 import 工具**读那个清单（不在测试里抄第二份，抄的会漂），
  并断言：清单**恰好**是这一条、每条理由不短于 80 字符。
- **反向验证**：新增 6 处变异（工作流 4 处 + 工具 2 处），
  其中「例外清单多一条」与「例外不写理由」都要转红。
  连同之前的 22 处，共 **29 处**全绿。
- **本机行为级演练**（`scratch/dtbcompat_check.py`）：
  真实板级树必须过（且 RGMII 的例外要被**打印出来**）；
  去掉 `zte,zx279133-nppt` / `zte,zx279133-switch` 的驱动字符串 ⇒ 转红并点名；
  去掉 primary 但留着 fallback ⇒ 仍然绿并说明「falls back to 'arm,pl011'」；
  去掉 fallback ⇒ 转红并点名；注释里的那个 RGMII 副本**不得**被算成第二个节点。

---

---

### 7.16 run #14：失败点前移到 `libbpf`，而 6.18 线同一个 head 全部成功

**判决**：run #14（head `0af2270`）**不再**报 Rust 包 `libzxpon-omci`——
7.11 那个工具链问题过去了。失败点**前移**到 `package/libs/libbpf`。
而这**不是内核的问题，也不是这个提交的问题**：6.18 发布线
**同一个 head `0af2270`** 的 run #364 **所有作业全部成功**。

#### 7.16.1 证据

1. run #14 的两个作业（flash / initramfs）都死在 **Compile** 步，
   而 annotation 只有四条，实质信息只有一行：
   `ERROR: package/libs/libbpf failed to build.`
2. **对照实验**：6.18 线 run #364（head `0af2270`，**同一个提交**）
   的四个作业（LED CI regression / native241 bootweb / initramfs / flash）
   **全部 success**。⇒ 仓库内容、包集、overlay 全都一样
   ⇒ **libbpf 在这个项目里是能编出来的**。
3. **libbpf 为什么会进镜像**：本仓库自己的 overlay
   `overlays/package/network/utils/iproute2/Makefile`：
   `Package/ip` 是 `DEPENDS:=+libnl-tiny +libbpf ...`，
   `tc-tiny` / `tc-bpf` / `tc-full` 也都带 `+libbpf`。
   而这个 overlay **两条线共用**。
4. **它的源码是从 github 克隆的**（cnjn master `31e2ac4` 的
   `package/libs/libbpf/Makefile`）：
   ```
   PKG_VERSION:=1.6.2
   PKG_SOURCE_PROTO:=git
   PKG_SOURCE_URL:=https://github.com/libbpf/libbpf
   ```
5. **两条线克隆的是同一个 OpenWrt 树**（同样的 `IW_URL` / `IW_SHA`）
   ⇒ 需要的包完全相同。
   —— **本条已被 7.18.5 更正**：两条线克隆的是**同一个仓库的不同 commit**
   （6.18 线 `sr1010-zx279133` @ `1b50aaf4`，7.3 线 `master` @ `31e2ac4d`），
   并不是同一棵树。需要的包**大体**相同，但这句话本身当时是错的。
6. **6.18 线有 `dl/` 下载缓存，7.3 线没有。**
   6.18 线的缓存里早就躺着 libbpf，每轮直接复用；
   7.3 线每轮都重新克隆，而且**没有缓存兜底**。
7. **为什么「包日志里什么都没有」**：一个**从没被编译过**的包**没有自己的日志**；
   而下载/克隆失败的措辞是 "could not resolve" / "unable to access" / "fatal:"，
   **没有一个**落在旧的诊断模式（"fatal error:"、"error:"、"command not found"…）里
   ⇒ **诊断自己把原因漏掉了**，留下的只有包名。

#### 7.16.2 ⇒ 修复一：让构建不再依赖那一次克隆

7.3 线补上与发布线**同一把钥匙**的 `dl/` 缓存：

```yaml
      - name: Cache dl/
        if: inputs.clean_cache != 'true'
        uses: actions/cache@v4
        with:
          path: openwrt/dl
          key: skd840n-dl-${{ matrix.mode }}-${{ hashFiles('configs/*.config', 'overlays/**') }}
          restore-keys: skd840n-dl-
```

钥匙由**构建模式**与 `configs/` + `overlays/` 的哈希决定，而这两处在两条线里
是同一批文件 ⇒ **两条线共用同一份缓存**，7.3 线会直接拿到发布线已经下好的 libbpf。
（顺带：7.3 线每轮都在重新下载整个 feed，加了缓存之后到 Compile 的等待也短得多。）

#### 7.16.3 ⇒ 修复二：让下一次失败能说出原因

> **本节已被 7.18.7 取代**：这里描述的取证顺序（读包自己的日志、
> 搜下载失败措辞、退回编译错误模式）**结构上取不到原因**——
> `logs/<pkg>.txt` 只在 `CONFIG_BUILD_LOG=y` 时才写（7.18.3），
> 而非 verbose 模式下编译器的话被 OpenWrt 丢进 `/dev/null`（7.18.2）。
> 现在的做法是：**失败后再串行跑一遍 `make -j1 V=s`**。

Compile 步现在按这个顺序取证：

1. 从顶层日志里读出**失败的是哪个包**；
2. 读那个包**自己的日志**的最后几行（**如果它有**）；
3. **专门**搜下载/克隆失败的措辞（"could not resolve"、"unable to access"、
   "RPC failed"、"fatal:"……）；
4. 再退回到编译错误的模式。

失败时用 `::error::cc:` 逐条发出（每步只留前 10 条，所以顺序有讲究：
先包自己的日志，再下载失败，最后才是编译错误）。

#### 7.16.4 本机验证

- 提取包名的 `sed` 在**真实的日志行**上跑通：
  `ERROR: package/libs/libbpf failed to build.` → `libs/libbpf`
  → `basename` → `logs/libbpf.txt`。
- 工作流里每一个 `run:` 块仍然都能 `bash -n`（`scratch/wf_shell_parse.py`）。
- 反向验证新增 4 处（缓存步没了、缓存钥匙与发布线不同、
  Compile 步不读包日志、Compile 步不搜下载失败），
  逐个转红在**指定**的那条检查上。
  其中「Compile 步不读包日志」**第一次没有转红**：
  契约测试查的是 `logs/$(basename`，而变异只改掉了两处出现中的一处
  （另一处没有 `PKGLOG=` 前缀）⇒ **又一次是「门禁比的是字符串而不是断言」**。

### 7.17 把编译预言机收进仓库，并接进 CI

**判决**：本机那套「用宿主 clang 检查 7.3 树」的检查器
（原来只活在 `scratch/kcheck.py` 里）现在**是仓库的工具**，
而且 **7.3 线在真正编译之前会先跑它**。

**为什么要收进仓库**：`scratch/` 是跨会话累积的工装区，
**换一台机器它就不在**，下一个会话也不会知道它存在过。
收进 `tools/` 之后它有版本、有门禁、有复跑命令。

**现在长什么样**：

| 文件 | 是什么 |
|---|---|
| `tools/kcheck.py` | 检查器本体：用宿主 clang 对 7.3 树做 `-fsyntax-only`，并按 modpost 的规则查符号闭合（`--objdir`） |
| `tools/kshadow.py` | 它依赖的「影子头文件」生成器：clang 不接受内核头里那种匿名成员，得给它一个等价形状，否则布局断言全挂 |
| `tests/test_sk_kcheck.py` | 判决规则的单元测试（不需要内核树、不需要编译器、不需要板子） |

**CI 里怎么用**：7.3 线的 build 作业里，在四个符号钉成 `=y` 之后、
**Compile 之前**多了一步「Check the drivers with the host compiler」。

**为什么这一步放在 build 作业、而不是 check-gates 作业**：
check-gates 作业**只有本仓库**，手里没有内核树；
要让它检查驱动，就得在那里重新下载 7.3.4 的 tarball、克隆 OpenWrt、
再打那 39 个补丁——而这**正是 build 作业开头已经做过的事**，
check-gates 的超时却只有 10 分钟。
⇒ 放在**树已经备好、Compile 还没开始**的时刻：
既不重复准备，又把「语法错误」从「五十分钟后」提前到「一分钟后」。
契约测试保证这一步**不会消失**，也保证它**排在 Compile 之前**。

**检查的是哪些文件**：不是手写的清单，而是
**本仓库的补丁系列碰过的每一个 `.c`，加上板级层新增的每一个 `.c`**。
契约测试从 `kernel-patches/7.3-local/*.patch` 与 `kernel-overlays-73/`
**算出来**这个集合，再和工作流里的清单比对
⇒ 新补丁碰了别的驱动，契约测试立刻转红，工作流里的清单不会漏。

**它也是这条线的输入**：7.3 线是靠路径过滤决定要不要起构建的，
而检查器与它的影子头文件生成器**就是构建要执行的东西**
⇒ `tools/kcheck.py`、`tools/kshadow.py`、`tests/test_sk_kcheck.py`
都在过滤里，契约测试也逐个断言。
（漏掉的话，改了检查器也不会有构建去跑它。）

**判决规则（关键）**：只看**我们自己的文件**里的错误。
编译器报在**内核自己的头文件**里的错误，说明**编译器读不动这棵树**
（CI runner 的 clang 比它读的 7.3.4 老），
这时我们文件里的错误可能是它的**后果**而不是我们的错 ⇒
工具把这类文件标成 `WARN` 并**不据此判红**。
这条规则是必须的，否则这道门禁会在一个**与我们的代码无关**的地方变红。

**还有一条更靠前的规则**：**环境不完整就不下判决**。
生成头文件这一步本身若缺了东西（arch 自己的三个头、
syscall 头、`kernel/bounds.c`、影子头），
那么解析器读的就**不是构建要读的那棵内核**
⇒ 工具把文件标成 `WARN`、**连「解析通过」都不算证据**
（缺一个头也可能让文件里的一段代码整个消失）。
⇒ 这道门禁**只会在环境完整、且错误确实在我们文件里的时候**判红。

**本机验证**：

- **正向**：9 个文件（补丁碰过的 5 个 + 板级层新增的 4 个）全部 `OK`，
  退出码 0；
- **反向**：在树里复制一份驱动目录并注入一处语法错误 ⇒
  工具报 `FAIL`、退出码 1；
- **契约测试反向验证两处**：
  少列一个文件 ⇒ 转红在
  「the check names exactly the drivers this repository touches」；
  把这一步挪到 Compile 之后 ⇒ 转红在
  「the compiler check runs before the Compile step」。
  两条都是**只红在指定的那一条**。

**未做**：

- CI 里这一道的编译器是 runner 自带的（Ubuntu 22.04 的 `clang` 包），
  **比本机的 clang 23 老**。真出现「编译器读不动树」时，
  这一步会标 `WARN` 而不是判红 ⇒ **它可能放过一类错误**，
  但**绝不会因为编译器太老而误伤构建**。
- 本机那一版还带 `--objdir`（编出目标码、按 modpost 的规则查符号闭合），
  **CI 里没有开**：那 11 条疑似缺符号全是宏生成导出造成的误报，
  在逐条核实之前不适合当门禁。

---

### 7.18 run #25：下载不再是原因，而**失败的原因我们一直看不见**

**判决**：run #25（head `cb0f627`，run id `45040260008`）**第一次带着 `dl/` 缓存**
跑完整条链路：**22 个编译前步全部 success**（含 `Download packages`），
两个作业仍然死在 **Compile**，仍然是 `package/libs/libbpf`。
⇒ **7.16 的修复一（加缓存）是对的，但 7.16 的结论「libbpf 能编出来、
只是下载失败」是错的**：下载成功了，libbpf 照样编不出来。

**而且这一轮暴露了一件更基础的事**：**我们从头到尾就没有看见过失败的原因。**
不是「这次没取到」，是**结构上取不到**。

#### 7.18.1 证据一：run #25 的 annotation 原文

两个作业（`initramfs` / `flash`）的失败 annotation 只有这些：

```
[warning] Node.js 20 is deprecated. ...
[failure] Process completed with exit code 2.
[failure] cc: 146:make: *** [/home/runner/work/.../openwrt/include/toplevel.mk:233: world] Error 1
[failure] cc: 144:    ERROR: package/libs/libbpf failed to build.
[failure] cc: --- libs/libbpf has no log of its own: it never got as far as being compiled ---
```

注意两件事：

- `144:` / `146:` 是行号 ⇒ **整个 `compile.log` 只有 146 行**。
  一次完整构建不可能只有 146 行输出 ⇒ **绝大多数输出根本没写下来**。
- 最后那句「包没有自己的日志，它根本没开始编译」是**我们自己写的话**
  （旧诊断里的 `--- $FAILED has no log of its own: ... ---`），
  **不是 OpenWrt 说的**，而且**是错的**。

#### 7.18.2 证据二：OpenWrt 把子 make 的输出丢掉了

cnjn master `31e2ac4` 的 `include/verbose.mk`：

```make
  SILENT:=>/dev/null $(if $(findstring w,$(OPENWRT_VERBOSE)),,2>&1)
  SUBMAKE=cmd() { $(SILENT) $(MAKE) -s "$$@" < /dev/null || { echo "make $$*: build failed. Please re-run make with -j1 V=s or V=sc for a higher verbosity level to see what's going on"; false; } } 8>&1 9>&2; cmd
```

**非 verbose 模式下，每个子 make 的 stdout 与 stderr 全部进 `/dev/null`。**
所以 `compile.log` 里只有 make 调用链，**编译器的话一个字都没有**。
这条规则 6.18 线早就踩过（run 88/89/91/92 只回来一句
`make[11]: *** [.../zx279133-switch.o] Error 1`），
**那边的解法是：失败之后再串行跑一遍 `make -j1 V=s`**。
**7.3 线没有抄这一步**，所以它一直在对着空气做诊断。

#### 7.18.3 证据三：`logs/` 里本来也不会有包日志

`include/rules.mk`：

```make
ifeq ($(CONFIG_BUILD_LOG),y)
  BUILD_LOG:=1
endif
```

`include/subdir.mk` 的 `log_make` 只有在 `$(BUILD_LOG)` 非空时才会
`tee` 到 `$(BUILD_LOG_DIR)/...txt`。
⇒ **`logs/<pkg>.txt` 只在 `CONFIG_BUILD_LOG=y` 时才存在**，
而本仓库**从来没有设过这个开关**。
⇒ 旧诊断去读 `logs/libbpf.txt`，读不到是**必然**的，
它说明不了关于 libbpf 的任何事。

#### 7.18.4 证据四：旧诊断连 OpenWrt 留下的那一句都没抓住

OpenWrt 在静默模式下**只留一句**：`make <target>: build failed. Please re-run make with -j1 V=s ...`。
旧诊断的模式是 `failed to build`（**语序相反**），
所以这句唯一的线索也没进 annotation。
⇒ 旧诊断的模式是 `\*\*\*|failed to build|No rule to make|\berror:`，
**四个模式一个都不匹配那句**。

#### 7.18.5 证据五：两条线其实**不是**同一棵 OpenWrt 树

7.16.1 第 5 条写的是「两条线克隆的是同一个 OpenWrt 树（同样的 `IW_URL` / `IW_SHA`）」——
**这句话是错的**，本轮更正：

| 线 | 仓库 | 分支 | commit |
| --- | --- | --- | --- |
| 6.18 发布线 | `cnjn/ImmortalWrt` | `sr1010-zx279133` | `1b50aaf4ed9cf042df6207564555b244d6ced517` |
| 7.3 线 | `cnjn/ImmortalWrt` | `master` | `31e2ac4ded9c8896c7aefd3dcb9a46ef77440fbd` |

用 GitHub 的 tree API 把两棵树各取一次递归清单（blob SHA 是内容寻址的，
所以可以直接比）：**10696 vs 10580 个 blob**，
差 **140 个只在 master、24 个只在 sr1010、32 个内容不同**，合计 **196 个文件**。

**`package/libs/libbpf/Makefile` 在两棵树里逐字节相同**（1.6.2、同一个
`PKG_MIRROR_HASH`）⇒ **共享的 `dl/` 缓存对 libbpf 是对的**。
两棵树真正不同的、与本问题相关的文件里，有
`include/kernel.mk`、`include/kernel-defaults.mk`、`include/image.mk`、
`include/bpf.mk` 与 `package/kernel/linux/modules/*.mk`。

**推论（只到这一步）**：共享缓存的前提是「两棵树需要同样的包」，
这个前提现在**被核对过**了，而不是像 7.16 那样靠一句错的注释撑着。

#### 7.18.6 证据六：libbpf 是被谁拉进来的（两条线一样）

- 7.3 线：`overlays-73/target/linux/zte/zx279133/target.mk` 的
  `DEFAULT_PACKAGES` 里有 **`ip-full`**；
- 6.18 线：cnjn `sr1010-zx279133` 的 `target.mk` 里**也有 `ip-full`**；
- `Package/ip-full` 的 `DEPENDS:=+libnl-tiny +libbpf ...`
  （本仓库自己的 `overlays/package/network/utils/iproute2/Makefile` 同向）。

⇒ **两条线都会编 libbpf**，6.18 线编得过、7.3 线编不过，
**差别只能在内核侧或工具链侧**。

一条**线索**（不是判决）：libbpf 1.6.2 把大部分内核 uapi 头**vendored 进了自己的仓库**
（`include/uapi/linux/{bpf,bpf_common,btf,if_link,if_xdp,netdev,netlink,perf_event,pkt_cls,pkt_sched}.h`
与 `include/linux/{compiler,err,filter,kernel,list,overflow,ring_buffer,types}.h`），
所以它**大体上不依赖目标内核的头**；但它仍然无条件 include 了两个**没有 vendored** 的头：
`src/libbpf.c` 的 `<linux/bpf_perf_event.h>` 与 `src/libbpf_probes.c` 的 `<linux/version.h>`
——后者在 Linux 里是**构建期生成**的头（`include/generated/uapi/linux/version.h`）。
**这两个头从哪来、够不够，只有真编译一次才知道** ⇒ 这正是下面那道诊断要回答的。

（顺带排除一个假设：libbpf 的 `CFLAGS ?= -g -O2 -Werror -Wall -std=gnu89` 里那个
`-Werror` **在 OpenWrt 的构建里不生效**——OpenWrt 用
`+$(MAKE_VARS) $(MAKE) ...` 把 `CFLAGS="..."` 作为**命令行变量**传进去，
命令行变量压过 `?=`。⇒ 「新工具链多一条警告就编不过」这条路也不成立。）

#### 7.18.7 ⇒ 修复：Compile 步加一道**串行 verbose** 的诊断 pass

照 6.18 线 run 88 以来的做法（**不是新发明，是把发布线的做法搬过来**）：

1. 并行构建照旧（`make -j$(nproc)`，输出进 `compile.log`）；
2. 失败时先从顶层日志里读出**失败的是哪个包**
   （`ERROR: package/<cat>/<pkg> failed to build.` 这行是顶层 make 打的，
   **静默模式下也在**）；
3. 对着**那一个目标**再跑一遍**串行 + verbose**：
   `make -j1 V=s package/libs/libbpf/compile`（20 分钟上限）；
4. 这一遍里子 make 的输出**不再进 `/dev/null`** ⇒ 编译器的话被 `tee` 下来；
5. 把错误行发成 `::error::diag:`，并把诊断日志写进 **step summary**；
6. 若这一遍**没有说出任何错误**，再退回整棵树跑一遍 `make -j1 V=s`（20 分钟上限）。

错误模式同时补齐三种措辞：
编译器说 `error:`、make 说 `Error 1`、OpenWrt 说 `build failed`
——**旧的模式只认得第一种**。

同时**删掉**那句错误断言（「包没有日志 ⇒ 它根本没开始编译」），
以及去读 `logs/<pkg>.txt` 的两行（那个文件从来不会被写）。

#### 7.18.8 本机验证

- 契约测试（`tests/test_sk_workflows.py`）新增 13 条断言，全绿；
- 反向验证新增 9 处（`scratch/reverse_check_workflow.py`），
  逐个转红在**指定**的那条检查上：
  - 去掉串行 verbose pass ⇒ 红在「the Compile step runs a serial verbose pass」；
  - verbose pass 不再指向失败的那个包 ⇒ 红在「the verbose pass is aimed at the package that failed」；
  - 模式里去掉 `Error [0-9]+` ⇒ 红在「the error pattern knows make's own wording」；
  - 模式里去掉 `build failed` ⇒ 红在「the error pattern knows OpenWrt's own wording」；
  - 把那句错误断言加回去 ⇒ 红在「the step does not claim a package was never compiled」；
  - 把读 `logs/$(basename ...)` 加回去 ⇒ 红在「the Compile step does not read a log that is never written」；
  - 把 7.3 线的 `IW_SHA` 改成 6.18 线的 ⇒ 红在「the two lines build different trees」；
  - 把 7.3 线的 `IW_URL` 改掉 ⇒ 红在「both lines clone the same repository」；
  - 把那句错的注释写回去 ⇒ 红在「the 7.3 workflow does not claim the two lines share a commit」。
- 那 9 条里有一条**第一次没有转红**：变异只改掉了
  `make -j1 V=s` 的**一处**出现，而另一处（整棵树的兜底 pass）还在
  ⇒ 检查只问「有没有 verbose 的串行 pass」，所以它本来就应该还是绿的
  ⇒ **把变异改成两处一起改**才符合这条断言的语义。

#### 7.18.9 未做

- **libbpf 到底为什么编不过，仍然不知道**。这一轮只把「看不见」变成「看得见」，
  下一轮才会拿到编译器的那句话。**在拿到那句话之前不动 libbpf**：
  改包定义、换版本、砍掉 `ip-full` 都是猜。
- 没有动 6.18 发布线（它编得过 libbpf，本轮的诊断改动也不需要它）。
- 没有改 `CONFIG_BUILD_LOG`：串行 verbose pass 已经够，
  再开它会让每个子 make 都额外写一份日志，**收益不抵磁盘与时间**。

---

### 7.19 run #33 / #34：诊断步自己问错了目标，「看不见」变成了「看得见一句废话」

**判决**

1. run #33 与 #34 的诊断 pass **从来没有编到那个包**。它问的目标是
   `libs/libbpf/compile`，顶层没有这条规则，于是落进 `include/toplevel.mk:233`
   的兜底规则（`%::`），而兜底规则报出来的是「没有这条规则」，
   不是编译器的话。**上一轮以为「已经能看见失败原因了」，其实只学会了
   看见自己问错问题的回声。**
2. 兜底条件还有第二个洞：make 每次失败都会打 `Error 2`，而 `Error 2`
   命中了「已经找到错误」的条件，**于是全树重建那一条兜底从来没跑过**。
3. libbpf 从开始到失败只用了 **4 秒**（`make[3] -C package/libs/libbpf compile`
   @03:26:32 → `ERROR: package/libs/libbpf failed to build.` @03:26:36）。
   22 个 C 文件编不出这种时间 ⇒ **不是编译错误**，更可能是
   「第一个编译单元就缺头」或者挂在 Prepare 阶段。
4. 7.3 线 **34 轮 0 成功**（API 读 `build-sk-d840n-73.yml` 的全部 run：
   #1~#32 全 failure/cancelled，#33/#34 failure），而同一个 head 上 6.18 线
   全部成功 ⇒ **不是回归，是这条线从第一天就没过 libbpf 这关**。
   排查方向因此不是「找回归点」，而是「找这条线独有的失败面」。

**证据**

- 证据一（run #34 的 annotation 原文，#33 同一句）：

  ```
  diag: 8:make[1]: *** No rule to make target 'libs/libbpf/compile'.  Stop.
  diag: 10:make: *** [.../include/toplevel.mk:233: libs/libbpf/compile] Error 2
  ```

  #33 的 flash 作业（job `137447283519`）的 job 日志里同一句也在，
  时间戳是 `03:26:32 GMT` → `03:26:36 GMT`。

- 证据二（顶层没有这条规则）：`include/toplevel.mk:233` 是 `%::` 兜底规则，
  顶层**没有** `libs/libbpf/compile`；正确目标是 **`package/libs/libbpf/compile`**。
- 证据三（两棵 OpenWrt 树的差异面，`scratch/iw_tree_diff.py`）：
  master（7.3 线）10696 blobs vs sr1010（6.18 线）10580 blobs，
  **只有 32 个文件内容不同**；`toolchain/` **零差异**；
  `include/` 只有 4 个 `.mk` 不同（`bpf.mk` / `image.mk` / `kernel-defaults.mk` /
  `kernel.mk`）且都与 libbpf 无关；
  **`package/libs/libbpf/Makefile` 与 `patches/100-bpf_tc_classid.patch`
  两树逐字节相同**；feeds 只差 luci，packages feed 两树同为 `e93a938c…`。
- 证据四（头依赖面排除，`scratch/libbpf_sys_hdr_73_20261123.py`）：
  libbpf 1.6.2 真正需要的系统 uapi 头（`linux/limits.h`、`bpf_perf_event.h`、
  `rtnetlink.h`、`if_ether.h`、`genetlink.h`、`ptrace.h`、`version.h`、
  `asm/unistd.h`、`asm/byteorder.h`）在两树**实质相同**；
  `rtnetlink.h` 与 `if_ether.h` 的差异**只有新增**。
  两棵内核树的 `headers_install` / `headers:` 目标与 `no-export-headers`
  列表**逐行相同** ⇒ 7.16 里「两棵树导出头的机制不同」这个判断**已被推翻**。
- 证据五（`-Werror` 不是原因）：libbpf 的 `src/Makefile` 写的是
  `CFLAGS ?= -g -O2 -Werror -Wall -std=gnu89`，而
  `include/package-defaults.mk:130` 在 `MAKE_ENV` 里已经传了 `CFLAGS=…`
  ⇒ `?=` 不生效 ⇒ **`-Werror` 与 `-std=gnu89` 都没有生效**。
- 证据六（补丁不是原因）：`git apply --check` 对
  `100-bpf_tc_classid.patch` 退出码 0 ⇒ 排除「补丁打不上」。
- 证据七（`-Werror` 之外也没有版本差）：两树 `toolchain/gcc/Config.version`
  逐字节相同（默认回落 **14.3.0**），两树 musl 均 1.2.5；
  7.3 的 `config-7.3` 里 `CONFIG_CC_VERSION_TEXT=… 14.3.0`。

**⇒ 修复：修的是诊断步自己（三处）**

1. 目标改成 `package/${FAILED}/compile`。**`package/` 不是可选的**。
2. 兜底条件加一条 `"No rule to make target"`：pass 根本没编到包，
   也就是「没有找到原因」，应当去看全树。
3. 兜底条件里的 `"Error [0-9]+"` 删掉：make 每次失败都会打 `Error 2`，
   它命中「已经找到错误」，于是全树重建从来没跑过。

**⇒ 门禁：诊断步也要被门禁盯着，而且门禁要比断言本身**

- 断言从命令里**解析出目标**再比较（`targets == ["package/${FAILED}"]`），
  并且**先剔除注释行** —— 注释里写一遍目标不算数，
  上一轮的错误正是「以为改了、其实问的是另一个目标」。
- 兜底条件里的 pattern 单独读出来，断言它**不含** `Error [0-9]+`；
  「整条 body 的 pattern」与「兜底条件里的 pattern」**不是同一个列表**，
  混在一起判就会出现「改了 A、通过 B」的假绿。
- 反向验证新增三处突变（去掉 `package/`、把 `Error [0-9]+` 放回兜底条件、
  删掉 `|| grep "No rule to make target"`），每一处必须转红在
  **指定的那条检查上**；「整步删掉」类突变天然会红多条，那一类不做此要求。

**未做**

- **libbpf 的真因仍未拿到**。这一轮修的是「我们看不见」这件事本身，
  下一轮才会拿到编译器的那句话。**在那之前不动 libbpf**：
  改包定义、换版本、砍掉 `ip-full` 都是猜。
- 没有在本机复现：本机只有 clang 23，CI 用的是 GCC 14.3.0，
  头文件工具链也不同（elfutils 0.192 拉不到），复现出来的结论不能当证据。
- 没有动 6.18 发布线（它编得过 libbpf，本轮的改动也不需要它）。

---

### 7.20 本机复现：**本节的结论已被 7.21 推翻**，留档说明它错在哪

> **更正（7.21）**：本节说「libbpf 对 7.3.4 的 uapi 头是编得过的，内核头不是原因」。
> **这句话是错的。** 7.3 线 libbpf 失败的真正原因就是内核导出的 uapi 头：
> `asm/ptrace.h` 用 `__u128`，而这个类型只定义在被 libbpf 自带那份
> `include/linux/types.h` 遮蔽掉的 uapi `linux/types.h` 里。
>
> 而且**本节的方法本身也不成立**，有两个独立的错误：
>
> 1. **include 顺序错了**：它把 libbpf 自带的 `include/` 放在了系统目录之前，
>    这本身是对的（CI 里也是这个顺序），但它**没有把内核构建期生成的头**
>    （`asm/unistd_64.h`）补进 include 集，于是编译器在缺头处就停了，
>    根本走不到 `__u128` 那一行。
> 2. **结论是误读日志**：重跑同一个脚本（`scratch/repro_libbpf_73_20261123.py`），
>    现在的输出是 **20 个文件里 19 个失败**，第一个拦路的是
>    `fatal error: 'asm/unistd_64.h' file not found`，不是「只有需要
>    `libelf.h` 的那几个编不过」。
>
> 正确的复现（带上生成的头、带上 libbpf 自带的 include/、并且先复现再打补丁）
> 见 7.21。

**判决（原文，已作废）**

1. ~~libbpf 1.6.2 的源码在「musl 1.2.5 的头 + 7.3.4 内核导出的 uapi 头」这套装配下
   **编得过**：本机用 clang 23 把 22 个 `.c` 全部过了一遍，只有需要 elfutils
   `libelf.h` 的那几个编不过（elfutils 0.192 本机拉不到）。
   ⇒ ~~**内核头这一面排除**：7.3 线的 libbpf 失败**不是**因为缺 uapi 头。~~
   —— **7.21 已推翻：内核头这一面正是原因。**
2. 复现的过程纠正了**复现自己**的两个错误（都不是树的问题，但它们一度把人
   指向「头缺失」这个错误方向）：
   - musl 的 `include/bits` 是 **`arch/generic/bits` + `arch/aarch64/bits` 两半的
     并集**（musl 的 Makefile 里 `ALL_INCLUDES` 同时收这两半）。只拷 aarch64
     那一半，musl 自己的头就坏掉：`bits/errno.h` 找不到、`int8_t` 未知、
     `locale_t` 未知。
   - **`asm/types.h` 不在 arm64 的 uapi 目录里**。它来自内核**构建期**生成的
     `arch/<arch>/include/generated/uapi/asm/`（顶层 Makefile 的
     `uapi-asm-generic` 规则把 `include/uapi/asm-generic/` 生成过去），
     而 `scripts/Makefile.headersinst` 明写「`arch/<arch>/include/generated/uapi`
     里的头也要导出」。少了这一层，`<asm/types.h>` 就找不到 —— 而 libbpf 自带的
     `include/linux/types.h` 无条件 include 它。

**证据**

- 复现脚本 `scratch/repro_libbpf_73_20261123.py`（本机，clang 23，
  `-target aarch64-linux-musl -fsyntax-only`）：补齐上面两层之后，
  22 个 `.c` 里**只有需要 `libelf.h` 的那几个编不过**，报错全部是
  `fatal error: 'libelf.h' file not found`；其余输出只剩 musl `byteswap.h`
  的一条 `-Wshift-op-parentheses` 警告。
- libbpf 1.6.2 需要**系统**提供而自己不带的头，完整清单
  （本机把 `src/` 下全部 `.c`/`.h` 的 include 逐条 grep 出来）：
  `asm/unistd.h`、`asm/byteorder.h`、`linux/limits.h`、`linux/bpf_perf_event.h`、
  `linux/genetlink.h`、`linux/ptrace.h`、`linux/if_ether.h`、`linux/rtnetlink.h`、
  `linux/errno.h`、`linux/version.h`（构建期生成）。
  `linux/bpf_verifier.h`、`linux/bsearch.h`、`linux/fdtable.h`、`linux/mm.h`、
  `linux/mman.h`、`linux/slab.h`、`linux/sort.h`、`linux/string.h` 都在
  `#ifdef __KERNEL__` 里，用户态用不到 —— 之前的清单把它们算成了依赖，是错的。
- 这 10 个头在两棵内核树里**都在**，内容**逐字相同或只有新增**：
  `linux/if_ether.h` 多 `ETH_P_*`、`linux/rtnetlink.h` 多 `RTA_DEL_REASON`；
  `asm/ptrace.h` 只把 `__ASSEMBLY__` 改成 `__ASSEMBLER__`、
  `__uint128_t` 改成 `__u128`。`asm/unistd.h`、`asm/byteorder.h`、
  `linux/limits.h`、`linux/bpf_perf_event.h`、`linux/genetlink.h`、
  `linux/ptrace.h`、`linux/errno.h`、`linux/netlink.h`、`linux/types.h`
  **逐字节相同**。

**未做**

- 本机没有 GCC 14（CI 用的是它），也没有 arm64 交叉工具链，所以复现只能证明
  「头这一面不是原因」，不能证明「编译一定过」。
- elfutils 0.192 本机拿不到（sourceware 404、buildroot 极慢、Debian sources
  返回 HTML 假页），`elf.c` / `usdt.c` 这几个需要 `libelf.h` 的没有复现。
- **libbpf 真正的失败原因仍未拿到**：下一轮 CI 的诊断 pass 会说。
  —— **7.21 已解决**：run #35 的诊断 pass 把编译器原话带回来了，
  真因就是 `__u128`。

---

### 7.21 run #35：libbpf 失败的真正原因，和它为什么**只**在 7.3 线出现

**判决**

1. **真因拿到了**：7.3 线 libbpf 编译失败的原因是
   **7.3.4 的 arm64 `asm/ptrace.h` 用了 `__u128`，而 `__u128` 只定义在被
   libbpf 自带那份 `include/linux/types.h` 遮蔽掉的 uapi `linux/types.h` 里。**
2. **为什么只在 7.3 线**：6.18.56 的同一批行（96、261-264、268）写的是
   `__uint128_t` —— 那是**编译器内建名**，用户态看得见，所以 6.18 线编得过。
   **行号一模一样，只有拼写不同。**
3. **修法**：给 libbpf 自己那份 `include/linux/types.h` 补上 `__u128`/`__s128`
   （逐字取自内核 uapi `linux/types.h`）。补丁**只**落 `overlays-73/`，
   6.18 发布线一行不碰。
4. **`__attribute_const__` 不是真实构建的缺口** —— **本节初稿的这条写错了，
   现更正**（见「证据四」）：内核导出 uapi 头时，`scripts/headers_install.sh:34`
   的 sed 就把这个宏**剥掉了**，所以 libbpf 真正编译的那批头里**本来就没有它**，
   CI 的诊断 pass **一条 `__attribute_const__` 错都没报**。
   补丁里仍然带着它，但只是**带 `#ifndef` 守卫的防御性定义**：遮蔽是 libbpf
   自己的事，不该依赖内核继续剥掉这个宏。**不补它不会导致 CI 失败。**
5. **6.18 侧为什么没有 `__attribute_const__` 这一类错，已经查实**：
   与 7.3 线**同一个机制** —— 装头时就被剥掉了，压根没进过编译。
   ⇒ **6.18 线连 `__u128` 都不需要，这个补丁整份都不需要。**

**证据**

证据一：run #35 的公开注解（11 条，job `137472302996`，run id `45107547379`）

诊断 pass 把编译器原话写进了 annotation，六处 `__u128` 报错一字不差：

```
[.github:164] diag: .../staging_dir/toolchain-aarch64_cortex-a53_gcc-14.3.0_musl/include/asm/ptrace.h:96:9:
                error: unknown type name '__u128'; did you mean '__u32'?
[.github:168] ...  asm/ptrace.h:261:9: error: unknown type name '__u128'; did you mean '__u32'?
[.github:172] ...  asm/ptrace.h:262:9: ...
[.github:176] ...  asm/ptrace.h:263:9: ...
[.github:180] ...  asm/ptrace.h:264:9: ...
[.github:184] ...  asm/ptrace.h:268:9: ...
```

失败链（也是 annotation）：

```
diag: 189:make[3]: *** [Makefile:137: staticobjs/libbpf.o] Error 1
diag: 191:make[2]: *** [Makefile:75: .../libbpf-1.6.2/.built] Error 2
diag: 195:make[1]: *** [package/Makefile:188: package/libs/libbpf/compile] Error 1
diag: 197:make: *** [.../include/toplevel.mk:233: package/libs/libbpf/compile] Error 2
```

这 6 个行号与本机 `scratch/linux-7.3.4/arch/arm64/include/uapi/asm/ptrace.h`
里 `__u128` 出现的行**逐行吻合**（96 `vregs[32]`；261-264 是 PAC 的四个 key；
268 `apgakey`）—— 报错和树是对得上的，不是猜的。

证据二：遮蔽发生在哪里

`libbpf-1.6.2/src/Makefile:33`：

```make
INCLUDES := -I. -I$(TOPDIR)/include -I$(TOPDIR)/include/uapi
```

libbpf **自带**一份 `include/linux/types.h`（guard `__LINUX_TYPES_H`），而这个
`-I$(TOPDIR)/include` 排在所有系统目录之前，那份头**没有 `__u128`**
（本机 `scratch/libbpf162/full/libbpf-1.6.2/include/linux/types.h`，全文无
`__u128`）。

导出 uapi 头里的两个 `#include <linux/types.h>`（**尖括号**）因此都解析到
libbpf 自己那份：

| 头 | 行 | 内容 |
|---|---|---|
| `linux/ptrace.h` | 9 | `#include <linux/types.h>` |
| `asm/ptrace.h` | 23 | `#include <linux/types.h>` |

而 `__u128` 的**唯一定义处**在 uapi `linux/types.h:16-18`：

```c
#ifdef __SIZEOF_INT128__
typedef __signed__ __int128 __s128 __attribute__((aligned(16)));
typedef unsigned __int128 __u128 __attribute__((aligned(16)));
#endif
```

—— 正好是**被遮蔽的那一份**。这就是全部机制。

证据三：7.3.4 与 6.18.56 的 `asm/ptrace.h`：行号相同，拼写不同

| 行 | 7.3.4 | 6.18.56 |
|---|---|---|
| 96 | `__u128 vregs[32];` | `__uint128_t vregs[32];` |
| 261 | `__u128 apiakey;` | `__uint128_t apiakey;` |
| 262 | `__u128 apibkey;` | `__uint128_t apibkey;` |
| 263 | `__u128 apdakey;` | `__uint128_t apdakey;` |
| 264 | `__u128 apdbkey;` | `__uint128_t apdbkey;` |
| 268 | `__u128 apgakey;` | `__uint128_t apgakey;` |

`__uint128_t` 是 clang/GCC 的**内建类型名**（本机实测：`typedef __uint128_t U;`
用 `-target aarch64-linux-musl` 编译 rc=0），所以 6.18 那条路不需要任何
uapi 定义就看得见它。

两棵树的 uapi `linux/types.h`（2035 B）与 `linux/swab.h`（7010 B）
**逐字相同**，arm64 的 `asm/byteorder.h`（887 B）也逐字相同 ——
**差别只有 `asm/ptrace.h` 和 `asm/sigcontext.h` 这两处的拼写**。

证据四：`__attribute_const__` —— **初稿这条结论错了，此处更正**

初稿说：「遮蔽还让 `__attribute_const__` 没了，不补它报错只是从
`asm/ptrace.h:96` 挪到 `linux/swab.h:48`」。**错在把「导出前」的头当成了
「导出后」的头。**

**导出前**，uapi `linux/swab.h` 里确实用了这个宏（5 处）：

```
include/uapi/linux/swab.h:48   static inline __attribute_const__ __u16 __fswab16(__u16 val)
include/uapi/linux/swab.h:57   static inline __attribute_const__ __u32 __fswab32(__u32 val)
include/uapi/linux/swab.h:66   static inline __attribute_const__ __u64 __fswab64(__u64 val)
include/uapi/linux/swab.h:79   static inline __attribute_const__ __u32 __fswahw32(__u32 val)
include/uapi/linux/swab.h:88   static inline __attribute_const__ __u32 __fswahb32(__u32 val)
```

包含链（本机逐个打开确认）：

```
libbpf 自带 include/uapi/linux/perf_event.h:20   #include <asm/byteorder.h>
  arm64 的 asm/byteorder.h:23                    #include <linux/byteorder/little_endian.h>
    linux/byteorder/little_endian.h:14          #include <linux/swab.h>
```

内核把这个宏定义在 `include/linux/compiler_attributes.h:83`：

```c
#define __attribute_const__             __attribute__((__const__))
```

而 `scripts/Makefile.headersinst` 第 5-6 行写死了只导出
`include/uapi`、`include/generated/uapi`、`arch/<arch>/include/uapi`、
`arch/<arch>/include/generated/uapi` —— **`include/linux/` 里的头一个都不导出**。

**但这只是导出前的状态。** 内核导出**每一个** uapi 头时，都要过一遍
`scripts/headers_install.sh`（`scripts/Makefile.headersinst:60,62,65`：

```
60:  cmd_install = $(CONFIG_SHELL) $(srctree)/scripts/headers_install.sh $< $@
62:  $(src-headers): $(dst)/%.h: $(src)/%.h $(srctree)/scripts/headers_install.sh FORCE
65:  $(gen-headers): $(dst)/%.h: $(gen)/%.h $(srctree)/scripts/headers_install.sh FORCE
```

），而那个脚本的 sed 里有一条：

```
scripts/headers_install.sh:33   s/([[:space:](])(__user|__force|__iomem)[[:space:]]/\1/g
scripts/headers_install.sh:34   s/__attribute_const__([[:space:]]|$)/\1/g
```

—— **`__attribute_const__` 在导出时就被剥掉了**。
（同一条 sed 的第 37 行还把 `inline`/`asm`/`volatile` 改成 `__inline__` 之类；
第 42 行的 `scripts/unifdef -U__KERNEL__ -D__EXPORTED_HEADERS__` 负责砍掉
`#ifdef __KERNEL__` 块。`__u128` 不受影响 —— 它不在 `__KERNEL__` 块里，
sed 也不碰它。）

**OpenWrt 侧装的就是这批「剥过的头」**（本机 `scratch/iwmaster/immortalwrt-31e2ac4.../`）：

```
toolchain/kernel-headers/Makefile:64-65   INSTALL_HDR_PATH="$(BUILD_DIR_TOOLCHAIN)/linux-dev/" headers_install
toolchain/kernel-headers/Makefile:106     $(CP) $(BUILD_DIR_TOOLCHAIN)/linux-dev/* $(TOOLCHAIN_DIR)/
```

⇒ CI 报错路径里的
`staging_dir/toolchain-aarch64_cortex-a53_gcc-14.3.0_musl/include/asm/ptrace.h`
就是这个目录里的头，**是剥过的**。

**决定性旁证**：run #35 的诊断 pass 单独重编 libbpf，**报了 6 条 `__u128`，
一条 `__attribute_const__` 都没有**（证据一的 11 条注解里没有任何一条提到它）。
若那真是缺口，编译器必然会报 —— 它在 `linux/swab.h:48` 用着。

⇒ **判决：`__u128` 缺失是真错；`__attribute_const__` 缺失不是错。**
初稿「不补它报错就挪到 `linux/swab.h:48`」是**照着导出前的头推的**，
而 CI 照着的是**导出后的头**。

（顺带解开了 6.18 侧「为什么没有这一类错」的悬案：**不是没走到 `swab.h`，
是那个宏在装头时就没了**，跟 7.3 线完全一样。）

证据五：两棵 OpenWrt 树**不是**原因（blob 级）

7.3 线编 `master`（`31e2ac4`），6.18 发布线编 `sr1010-zx279133`（`1b50aaf4`）。
逐 blob 比对本机 `scratch/cnjn-iw`：

| 文件 | 结果 |
|---|---|
| `package/libs/libbpf/Makefile` | **逐字相同** |
| `package/libs/elfutils/Makefile` | **逐字相同** |
| `package/libs/zlib/Makefile` | **逐字相同** |
| `package/network/utils/iproute2/Makefile` | **逐字相同** |
| `tools/pkgconf/Makefile` | **逐字相同** |
| `include/package.mk` | **逐字相同** |
| `include/package-defaults.mk` | **逐字相同** |
| `rules.mk` | **逐字相同** |
| `package/kernel/linux/modules/lib.mk` | 只差 1 行：`K618:=$(filter 6.18.%,...)` vs `$(call kernel_patchver_ge,6.18)` |
| `include/kernel.mk` | 只差 3 处：`LINUX_UNAME_VERSION`、7.3 不传 `cmd_syscalls=`、`KERNELRELEASE`/模块 release 钩子 |

`Package/ip-full` 在**两棵树**里都依赖 libbpf（同一个文件、同一行）：
`package/network/utils/iproute2/Makefile:52`
`DEPENDS:=+libnl-tiny +libbpf +(PACKAGE_devlink||PACKAGE_rdma||PACKAGE_dcb):libmnl`。

⇒ 两条线拉 libbpf 的方式一样、包定义一样、构建环境一样，**唯一不同的就是
内核导出的那批 uapi 头**。

证据六：6.18 线**确实**编 libbpf（所以不能拿「6.18 也编」当借口）

6.18 发布镜像的 initramfs cpio 清单（本机 `scratch/image_cpio_list.py` 解
`scratch/trial-Image.gz`，共 1062 条）里有：

```
usr/lib/libbpf.so.1
usr/lib/libbpf.so.1.6.2
usr/lib/libelf.so.1
usr/lib/libelf-0.192.so
```

⇒ 6.18 线在 `__uint128_t` 那条路上走通了，**这条线不需要这个补丁**。

**修法**

新增一个补丁，只落 7.3 线：

```
overlays-73/package/libs/libbpf/patches/110-arm64-u128-types.patch
```

它改的是 **libbpf 自己那份** `include/linux/types.h`（`--- a/include/linux/types.h`
/ `+++ b/include/linux/types.h`），在 `#include <asm/posix_types.h>` 之后加：

```c
#ifdef __SIZEOF_INT128__
typedef __signed__ __int128 __s128 __attribute__((aligned(16)));
typedef unsigned __int128 __u128 __attribute__((aligned(16)));
#endif

#ifndef __attribute_const__
#define __attribute_const__ __attribute__((__const__))
#endif
```

- `__u128`/`__s128` 两行**逐字取自**内核 uapi `linux/types.h:17-18`；
- `__attribute_const__` 用**内核自己的拼法**（`include/linux/compiler_attributes.h:83`），
  带 `#ifndef` 守卫 —— **它是防御性定义，不是真实构建需要的**（证据四）：
  遮蔽是 libbpf 自己的事，不该依赖内核继续在装头时剥掉这个宏；
- **只加不删**（测试里有一条断言专门盯 `-` 行）；
- 不改 `package/libs/libbpf/Makefile`：OpenWrt 自己会应用 `patches/*.patch`。

`overlays/`（6.18 发布线）**保持零 libbpf 补丁** —— 测试里也有一条断言盯这个。

**门禁**

1. **本机最小门禁** `scratch/libbpf_u128_gate.py`：拿上游 libbpf 配 7.3.4 的头，
   先复现 `error: unknown type name '__u128'`，再打**仓库里那个补丁**，确认编过。
   ⇒ `GATE OK`。
2. **本机整包门禁** `scratch/libbpf_pkg_gate.py`（22 个 `.c`）：门禁直接
   `git apply` **仓库里那个补丁**（不在脚本里另抄一份修法文本，避免门禁与出货物漂移）。
   门禁**复刻内核装头的预处理**（`headers_install.sh` 的两条 sed），并跑**两轮**：
   - **主门禁用「装头后」的头**（真实构建用的就是这批）：修前**只有** `__u128`、
     **没有** `__attribute_const__`、因别的原因失败的文件**为空**
     ⇒ **精确复现 CI 报告的错误集**；修后编过。
   - 再跑一轮**原始 uapi 头**（更严）：修前多一条 `__attribute_const__`
     （`libbpf.c`），修后也编过。
   ⇒ 7.3.4 两轮 `GATE OK`。
   **同跑 6.18.56**（`python scratch/libbpf_pkg_gate.py 618`）：装头那轮
   **一个错都没有**（无 `__u128`、无 `__attribute_const__`、无别的失败）
   ⇒ **6.18 线整份补丁都不需要**；原始头那轮只剩 `__attribute_const__`
   这一条 —— 正好说明原始头轮次不是空转。
3. **仓库静态断言** `tests/test_sk_workflows.py` 新增一段（11 条）：补丁在
   `overlays-73/`、目标文件精确为 `+++ b/include/linux/types.h`、含 `__u128`
   typedef、含 `#ifdef __SIZEOF_INT128__`/`#endif` 守卫、含 `__attribute_const__`、
   **只加不删**、`overlays/` 里**没有**任何 libbpf 补丁、工作流 overlay 步存在、
   `LIBBPF_PATCH` 路径精确、`test -f` 存在、3 个 `grep -qx` 的 pattern 列表**精确相等**。
4. **工作流断言**（`.github/workflows/build-sk-d840n-73.yml` 的
   “Apply the OpenWrt-side overlays” 步）：4 条 `grep -qx` / `test -f`，
   **读的是补丁本身**，不是关于补丁的一句话。
5. **反向验证** `scratch/reverse_check_libbpf.py`：**9 处突变全部只转红在
   指定的那条检查上**（删 typedef / 删 guard / 删 `__attribute_const__` /
   改目标文件 / 加 `-` 行 / 把补丁放进 `overlays/` / 改工作流路径 /
   把 `grep -qx` 换成短语 / 去掉 `test -f`）。
   ⇒ `reverse check: OK (9 mutations, each red on its own check)`。

**未做**

- ~~**6.18 侧为什么没有 `__attribute_const__` 这一类错，本机没查实。**~~
  **已查实**（见证据四）：内核导出 uapi 头时 `scripts/headers_install.sh:34`
  就把这个宏剥掉了，**两条线是同一个机制**；6.18 的真实构建里那个宏
  **压根没进过编译**。
  保留原来那串排查的记录，是因为它正是本节初稿走偏的地方 ——
  **那一条查的都是「导出前」的头**：
  - 两棵树的 uapi `linux/types.h`（2035 B）、`asm/byteorder.h`（887 B）、
    `linux/swab.h`（7010 B，含 `__attribute_const__`）**逐字相同**；
  - musl 1.2.5 tarball 里 `__uint128_t`/`__attribute_const__`
    **只有使用、没有定义**；
  - 整棵 7.3.4 内核树里 `__attribute_const__` 的**定义只有两处**：
    `include/linux/compiler_attributes.h:83` 与 `tools/include/linux/compiler.h:121`
    —— **两个都不导出**（`scripts/Makefile.headersinst` 只导出 `include/uapi/`、
    `include/generated/uapi/`、`arch/<arch>/include/uapi/`、
    `arch/<arch>/include/generated/uapi/`）；
  - 整棵 OpenWrt 树里 `__attribute_const__` 只有两处命中：
    `package/devel/binutils/patches/001-replace-attribute_const.patch`（改的是
    binutils 自己的 gprofng 源码，**不是**用户态头）和
    `package/boot/uboot-lantiq/patches/0028-gcc-compat.patch`（u-boot 自己）；
  - 唯一在用户态提供这个宏的是 **glibc 的 `sys/cdefs.h`**，而 OpenWrt 用的是 musl。
  ⇒ 这些全都指向「用户态没人定义它」，但**没有一条回答「编译时它还在不在」** ——
  而答案在装头那一步：`headers_install.sh` 的 sed 早就把它剥了。
- **补丁里 `__attribute_const__` 那一行是防御性的，真实构建用不到**
  ⇒ 它的正确性只由「与内核自己的拼法逐字一致 + `#ifndef` 守卫」保证，
  **没有 CI 证据**（CI 不报这个错，也就不会因为它而失败）。
  本机门禁能证明的只有：加了它不影响别的头，删了它在**原始头**那轮会多一条错。
- elfutils 0.192 的 `libelf.h`/`gelf.h` 本机拿不到（sourceware 404、buildroot 极慢、
  sources.debian.org 返回 HTML 假页）⇒ 整包门禁里 20/22 个文件**在本机判不了**，
  只能由 CI 判。
- 本机没有 GCC 14（CI 用的是它），复现门禁用的是 clang 23
  （`-target aarch64-linux-musl -fsyntax-only`）⇒ 能证明「报不报错」，
  不能证明「CI 里一定编得过」。
- 这一轮 CI 的结果还没出：**补丁在真实 CI 里能不能一次编过，尚未验证。**

---

### 7.22 诊断 pass 够不到内核：下一块失败面，正好是它看不见的那一面

**判决**

1. **7.3 线的诊断 pass 只认包，不认内核。** 它从 `compile.log` 里找的是
   `ERROR: package/<path> failed to build.`，而内核编不过时顶层报的是
   **`ERROR: target/linux failed to build.`** —— 模式对不上，`FAILED` 是空的。
2. **内核不是包。** 它由 `target/linux/compile` 编：
   `include/target.mk:398-401` 在 `TARGET_BUILD=1` 时拉进 `kernel-build.mk`，
   而 `include/kernel-build.mk:156-157` 的 `compile:` 目标才是真正跑内核
   make 的地方。`package/kernel/linux/Makefile:44-45` 的 `Build/Compile` 是
   **空的**，所以 `package/kernel/linux/compile` 什么也编不出来。
3. ⇒ 内核失败时，诊断 pass **直接落到整树兜底**；而整树兜底是从头再走一遍
   包顺序，20 分钟的上限**到不了内核** —— 这正是 6.18 线 run 88/89/91/92
   的失败（`build-sk-d840n.yml:1106-1109` 写得很清楚）。
4. **修法**：诊断 pass 增加一条内核分支，**先**问 `target/linux/compile`
   （6.18 线早就在用这个目标），内核优先于包。
5. **为什么现在做**：7.3 线的五片驱动补丁**从未被真正的编译器编过**
   （本机没有 arm64 交叉工具链），**内核是最可能的下一个失败面**，
   而诊断 pass 恰好在这一面是瞎的。

**证据**

证据一：内核的失败行长什么样

- `include/subdir.mk:79`：
  `|| $(call ERROR,$(1),   ERROR: $(1)/$(bd) failed to build...)`
- `include/subdir.mk:25-27` 的 `ERROR` 宏把这条消息**缩进三格**后打印：
  `($(call MESSAGE, $(2)); ... exit 1;)`
- `target/Makefile:10`：`$(curdir)/builddirs:=linux sdk imagebuilder toolchain llvm-bpf`
  ⇒ `$(1)` = `target`、`$(bd)` = `linux`
  ⇒ 那行就是 **`   ERROR: target/linux failed to build.`**

⇒ 所以工作流里用的是 **固定字符串**匹配（`grep -qaF`）：缩进几格不影响，
固定字符串也不受正则元字符干扰。

证据二：6.18 线早就在用 `target/linux/compile`

`build-sk-d840n.yml:1115`：

```sh
timeout --kill-after=60s 25m bash -c 'echo $$ > ../compile-make.pid; exec make -j1 V=s target/linux/compile' 2>&1 | tee ../compile-v1.log || DIAG_RC=$?
```

6.18 线能看见内核的编译错误，用的就是这个目标。7.3 线没有。

**修法**

7.3 线的 Compile 步现在多一段：

```bash
KERNEL_FAILED=no
if grep -qaF 'ERROR: target/linux failed to build' ../compile.log; then
  KERNEL_FAILED=yes
fi
...
if [ "$KERNEL_FAILED" = yes ]; then
  timeout --kill-after=60s 25m bash -c "make -j1 V=s target/linux/compile" 2>&1 | tee "$DIAG" || true
elif [ -n "$FAILED" ]; then
  timeout --kill-after=60s 20m bash -c "make -j1 V=s package/${FAILED}/compile" 2>&1 | tee "$DIAG" || true
fi
```

- **内核优先**：内核坏掉时，报错的那个包只是它的后果，
  拿包去问会把 20 分钟的上限花在后果上。
- 时间预算：并行编译 280m + 内核诊断 25m + 整树兜底 20m = 325m，
  作业上限 400m，装得下。

**门禁**

`tests/test_sk_workflows.py` 多三条断言：

1. **verbose pass 的目标清单精确等于** `package/${FAILED}` 与 `target/linux`
   两个（排序后比列表，所以多出第三个目标也会转红）；
2. **工作流用的固定字符串真的能匹配 OpenWrt 打印的那一行** ——
   把模式抽出来，拿 `"   ERROR: target/linux failed to build."` 去跑；
   只比对「模式里出现了 target/linux」是不够的，那样的模式一行都不会 fire；
3. **内核分支在包分支之前**（两个分支都在时）。

`scratch/reverse_check_workflow.py` 多三处突变，
每一处都只转红在**指定**的那条检查上：

| 突变 | 转红在 |
|---|---|
| 把内核分支整段删掉 | 「verbose pass 的目标是失败包与内核」 |
| 把固定字符串改成一个匹配不上的 | 「工作流认得内核自己的失败行」 |
| 把包分支挪到内核分支前面 | 「内核先于包被诊断」 |

**未做**

- 这一条只修了「诊断 pass 够不到内核」。**内核第一次被真正的编译器编，
  会不会过，仍然不知道** —— 本机没有交叉工具链，编译只能走 CI。
- 内核分支的 25 分钟上限是照抄 6.18 线的；7.3 线的内核更大，
  如果一次重编到不了出错的那个文件，可能还要再调。
- 整树兜底那条路（`make -j1 V=s`）**仍然是到不了内核的**，
  这次只是给它前面加了一条更直接的路；兜底本身没有改。

---

### 7.23 导出 dtb 时不查分区表：正对着实机对拍的那一步

**判决**

1. **7.3 线导出 `flash-dtb.bin` 时没有查分区表**；6.18 线有
   （`build-sk-d840n.yml:661`）。
2. **分区表决定 root 在哪**：U-Boot 的 bootargs 写死 `root=/dev/mtdblock5`，
   板子的表是 root@`0xf00000`、241 MiB，**由 `mtd5` 别名钉死**。
   表差一个擦除块 ⇒ jffs2 写到新内核不会去看的地方 ⇒
   **panic，而且串口没有输出可读** —— 这是刷机之后最难查的一种失败。
3. **板级 dts 里写死的就是这张表**，两条线共用同一份
   （`kernel-overlays/arch/arm64/boot/dts/zte/zx279133-sk-d840n.dts:940-965`）
   ⇒ 7.3 线的 dtb 也是 native241 ⇒ **这道门禁不会误伤**。
4. 补法：dtb 编译步查一次编出来的 blob，导出步**再查一次那个要刷进去的件**。

**证据**

证据一：板级 dts 里的分区表（逐条）

| 分区 | 偏移 | 大小 |
|---|---|---|
| boot | `0x000000` | `0x200000` |
| kernel | `0x200000` | `0x800000` |
| dtb | `0xa00000` | `0x100000` |
| parameter tags | `0xb00000` | `0x400000` |
| **root** | **`0xf00000`** | **`0xf100000`**（241 MiB） |

root 由 **`mtd5` 别名**钉死（dts:71-75，`mtd1`..`mtd5` 一一对应这五个分区），
**不是按节点顺序数出来的** ⇒ `/dev/mtdblock5` ⇒ 与 U-Boot 的 bootargs 对得上。
（`mtd0` 是不是 SPI-NAND master 取决于 `CONFIG_MTD_PARTITIONED_MASTER`，
但别名让 root 的号与那个配置无关 —— 这正是 dts:61-76 注释里写的用意。
旧布局多一个 `scratch`，它拿 `mtd6`，**root 仍然是 `mtd5`**。）

**本机核实（不是假设）**：门禁要的每一个条件，都对着板级 dts 的源码查过
（`python3 scratch/check_board_dts_layout.py`）：

```
the dts declares 5 partitions:
  bootpart     @       0 reg=<0x000000 0x200000> label=boot read-only
  kernelpart   @  200000 reg=<0x200000 0x800000> label=kernel
  dtbpart      @  a00000 reg=<0xa00000 0x100000> label=dtb
  tagspart     @  b00000 reg=<0xb00000 0x400000> label=parameter tags read-only
  rootpart     @  f00000 reg=<0xf00000 0xf100000> label=root
the dts pins 5 mtd aliases: mtd1..mtd5
compatible: ('skyworth,sk-d840n', 'zte,zx279133')
bootargs: console=ttyAMA0,115200n8 root=/dev/mtdblock5 rootfstype=jffs2 ro rootwait

the board dts satisfies what the native241 gate will ask of the compiled dtb
```

⇒ `tools/sk_layout.py` 的严格解析器要问的每一项（compatible、bootargs、
分区个数、root 容量、mtd 别名、标签与偏移、read-only 标志）**都对得上**
⇒ **7.3 线编出来的 dtb 一定是 native241，这道门禁不会误伤**。
（7.3 线用的就是这份 dts：`tools/apply-sk-overlay-73.sh:35-40` 把
`kernel-overlays/` 整体拷进 7.3 树，只排除 6.18 世代的驱动与 PHY。）

证据二：6.18 线本来就有这道门禁

`build-sk-d840n.yml:661`：

```sh
python3 tools/dtbparts.py /tmp/zx279133-sk-d840n.dts.dtb layout=native241 root=0xf00000,0xf100000
```

7.3 线没有。

证据三：**本机暂存的那个 dtb 是旧布局** —— 这道门禁不是假想

```sh
python tools/dtbparts.py staging/trial/flash-dtb.bin layout=native241 root=0xf00000,0xf100000
# TABLE WRONG - unexpected layout: current240     （退出码 1）
```

`staging/trial/flash-dtb.bin` 是 **#337 之前**的那件（板子那时还是
`current240`；#337 之后板子是 `native241`，241 MiB root）。
⇒ **没有这道门禁，一件旧布局的 dtb 会被当成新件刷进板子**，
而刷完的板子什么日志都不给你。
⇒ 同时也说明：**实机对拍必须用 CI 导出的那三个件，
不能拿 `staging/trial/` 里现成的旧件**。

**修法**

7.3 线补两处，都在问同一个问题：

```sh
# “Gate: the board device tree compiles against the 7.3 tree” 步：问编出来的 blob
python3 tools/dtbparts.py /tmp/sk-d840n.dtb layout=native241 root=0xf00000,0xf100000

# “Validate and export the partitions” 步：问要刷进去的那一件
python3 tools/dtbparts.py flash-dtb.bin layout=native241 root=0xf00000,0xf100000
```

**门禁**

`tests/test_sk_workflows.py` 多两条断言：把 `tools/dtbparts.py` 的调用
**连同它的参数**抽出来精确比较 —— 查的是哪个 blob、`layout=` 是什么、
`root=` 是什么，三样都要对。

`scratch/reverse_check_workflow.py` 多两处突变：
把 dtb 步的那行换成 `echo`、把导出步的那行换成 `echo`，
各自只转红在**指定**的那条检查上。
⇒ 连同之前各节的突变，反向验证累计 **55 处**（工作流 53 + 工具 2），
每处只红在指定的那条检查上；脚本自己的结语行会把计数打出来。

**未做**

- 本机没有 dtc，**没法在本地编出 7.3 线的 dtb 来试这道门禁**；
  它在 CI 里第一次真正执行。
- 这道门禁只管**布局**；容量、别名、各段偏移都由 `tools/dtbparts.py` 自己查。
- 暂存的 `staging/trial/` 那三件是 **6.18 旧布局**，
  实机对拍前必须换成 CI 导出的那三件，并先 `sha256sum -c sha256sums`。

---

### 7.24 更正 7.21 的 `__attribute_const__` 结论：**它不是真实构建的缺口**

**判决**

1. **7.21 初稿的判决 4 是错的，本节更正。**
   初稿说「同一个遮蔽还让 `__attribute_const__` 没了，不补它报错只是从
   `asm/ptrace.h:96` 挪到 `linux/swab.h:48`」——
   **错在把「导出前」的头当成了「导出后」的头。**
2. **`__attribute_const__` 缺失不是错。** 内核导出每一个 uapi 头时都过一遍
   `scripts/headers_install.sh`，而它的 sed **第 34 行就把这个宏剥掉了**。
   ⇒ **libbpf 真正编译的那批头里本来就没有它。**
3. **CI 的注解是决定性旁证**：run #35 的诊断 pass 单独重编 libbpf，
   **报了 6 条 `__u128`，一条 `__attribute_const__` 都没有**。
   若那真是缺口，编译器必然会报 —— 它在 `linux/swab.h:48` 用着。
4. **顺带解开了 6.18 侧的悬案**：「为什么 6.18 没有这一类错」的答案是
   **同一个机制** —— 装头时就被剥掉了，压根没进过编译。
   ⇒ **6.18 线连 `__u128` 都不需要，这个补丁整份都不需要。**
5. **补丁里那一行保留，但降级为防御性定义**：`#ifndef` 守卫 + 内核自己的拼法。
   理由是**遮蔽是 libbpf 自己的事**，不该依赖内核继续剥掉这个宏。
   **不补它不会导致 CI 失败。**

**证据**

```
# 内核侧：每个 uapi 头都过一遍 headers_install.sh
scripts/Makefile.headersinst:60   cmd_install = $(CONFIG_SHELL) $(srctree)/scripts/headers_install.sh $< $@
scripts/Makefile.headersinst:62   $(src-headers): $(dst)/%.h: $(src)/%.h $(srctree)/scripts/headers_install.sh FORCE
scripts/Makefile.headersinst:65   $(gen-headers): $(dst)/%.h: $(gen)/%.h $(srctree)/scripts/headers_install.sh FORCE

# 剥掉它的那一条 sed
scripts/headers_install.sh:33   s/([[:space:](])(__user|__force|__iomem)[[:space:]]/\1/g
scripts/headers_install.sh:34   s/__attribute_const__([[:space:]]|$)/\1/g

# OpenWrt 侧装进 toolchain 的就是这批剥过的头
toolchain/kernel-headers/Makefile:64-65   INSTALL_HDR_PATH="$(BUILD_DIR_TOOLCHAIN)/linux-dev/" headers_install
toolchain/kernel-headers/Makefile:106     $(CP) $(BUILD_DIR_TOOLCHAIN)/linux-dev/* $(TOOLCHAIN_DIR)/
```

⇒ CI 报错路径里的
`staging_dir/toolchain-aarch64_cortex-a53_gcc-14.3.0_musl/include/asm/ptrace.h`
就是这批头。复现命令：`python3 scratch/headers_install_chain.py` 与
`python3 scratch/openwrt_headers_chain.py`。

**门禁也跟着改了**

整包门禁 `scratch/libbpf_pkg_gate.py` 现在**复刻内核装头的预处理**
（`headers_install.sh` 的两条 sed），并跑两轮：

| 树 | 头 | 修前 | 修后 |
|---|---|---|---|
| 7.3.4 | 装头后 | **只有** `__u128`（6 条），**无** `__attribute_const__` | 编过 |
| 7.3.4 | 原始头 | `__u128` + `__attribute_const__`（`libbpf.c`） | 编过 |
| 6.18.56 | 装头后 | **一个错都没有** | — |
| 6.18.56 | 原始头 | 只有 `__attribute_const__` | — |

⇒ 7.3.4 的「装头后」那轮**精确复现 CI 报告的错误集**；
6.18.56 的「装头后」那轮**完全干净** ⇒ 6.18 线不需要这个补丁；
两棵树的「原始头」那轮都只剩 `__attribute_const__` ⇒ 原始头轮次**不是空转**。

**未做**

- 补丁里 `__attribute_const__` 那一行**没有 CI 证据**（CI 不报这个错，
  也就不会因为它而失败）。本机门禁能证明的只有两件事：
  加了它不影响别的头；删了它在**原始头**那轮会多一条错。
- 本节只更正了**结论**；`overlays-73/` 里那个补丁**正文一个字没改**
  （只改了它的说明文字），所以 7.3 线的构建面不变。

---

### 7.25 实机刷机路径上的分区表门禁：**工装区落后于仓库**

**判决**

1. **7.3 线的 CI 已经查了分区表（7.23），但真正下手的工装区没跟上。**
   工作区根目录的 `tools/` 是**实机那一侧真正会跑的脚本**，
   而它的 `dtbparts.py` 是**旧版**（没有 `layout=` 这个参数），
   `sk_layout.py` **根本不存在**。
2. **更糟的是参数不一致，而且方向相反**：
   工装区里 `flash-round.sh` / `preflight.sh` / `lan1roundcheck.sh` /
   `gettrial.sh` 问的是 **`root=0xf00000,0xf000000`（240 MiB）**，
   而 `mkrollback.sh` 问的是 **`root=0xf00000,0xf100000`（241 MiB）**。
   仓库 `tools/` 里**全部**已经是 241 MiB。
   ⇒ 同一块板子上的两套门禁，一套放行旧布局、一套拒绝旧布局。
3. **后果有两个方向**：
   - **旧载荷能过工装区的门禁**：`staging/trial/` 里那三件正好就是
     **current240**，工装区的旧参数**放行**它 ⇒ 7.23 在 CI 里补的那道门禁
     **管不到实机**。
   - **新载荷反而会被工装区拒绝**：7.3 的新 dtb 是 **native241**，
     工装区的旧参数会报 `MISMATCH root` ⇒ 正确的件被拒，
     而报错说的是「大小不对」，会让人去查一个不存在的问题。
4. **工装区的脚本不能整包覆盖**：两边已经**双向分叉**了 ——
   76 个共有文件里 58 个相同、18 个不同，而方向不一：
   `gettrial.sh` 工装区有 **81 行独有**（`RELEASE=` 精确指定 tag、
   绕开 GitHub API 60 次/小时的额度），仓库那份反而旧。
   ⇒ **只做定向同步**，不整包搬。

**证据**

```
# 工装区（旧）：问 240 MiB
tools/flash-round.sh:45      python tools/dtbparts.py staging/trial/flash-dtb.bin root=0xf00000,0xf000000
tools/preflight.sh:74        python tools/dtbparts.py staging/trial/flash-dtb.bin root=0xf00000,0xf000000
tools/lan1roundcheck.sh:135  python tools/dtbparts.py "$D/flash-dtb.bin" root=0xf00000,0xf000000
tools/gettrial.sh:198        ... root=0xf00000,0xf000000 ...
tools/mkrollback.sh:52       python tools/dtbparts.py "$OUT/flash-dtb.bin" root=0xf00000,0xf100000   # 已经是新的

# 仓库（对）：全部 native241
tools/flash-round.sh:45      ... layout=native241 root=0xf00000,0xf100000
tools/preflight.sh:98        ... layout=native241 root=0xf00000,0xf100000
tools/gettrial.sh:157        ... layout=native241 root=0xf00000,0xf100000
tools/dtbparts.py            import sk_layout; layout= 由 inspect_layout() 判

# 两份 dtbparts.py 的差异是纯增量（工装区独有行 = 0，仓库独有 16 行）
```

**本机实测（暂存的旧 dtb）**

```
$ python tools/dtbparts.py staging/trial/flash-dtb.bin layout=native241 root=0xf00000,0xf100000
  ... root @0x00f00000 len=0x0f000000 (240 MiB) ...
TABLE WRONG - unexpected layout: current240
rc=1

$ python tools/dtbparts.py staging/trial/flash-dtb.bin root=0xf00000,0xf000000
TABLE OK - all 1 expectations matched
rc=0
```

⇒ **新门禁能分清两种布局**，而旧参数**分不清** ——
只比 root 的偏移和大小，不看分区有几个。

**为什么两种布局都要认**

板级 dts（`kernel-overlays/arch/arm64/boot/dts/zte/zx279133-sk-d840n.dts:940-965`）
声明的是 **5 个**分区：boot / kernel / dtb / parameter tags / **root 0xf00000/0xf100000**，
**没有 scratch** ⇒ `native241`。而旧 dtb 是 6 个分区，最后多一个
`scratch @0xff00000/0x100000`，root 只有 240 MiB ⇒ `current240`。
两者的 **root 都是 `/dev/mtdblock5`** —— 不是数出来的，是**别名钉死的**
（新布局 `mtd1..mtd5`，旧布局 `mtd1..mtd6`，root 在两边都是 `mtd5`）——
差别只在 root 吃掉了多少闪存、以及有没有 scratch。
`tools/sk_layout.py:113` 把这两种组合都列为合法，
`tools/sk_layout.py:131` 用「分区个数」来命名它们。

**修了什么（工装区，不进仓库）**

- 装入 `tools/sk_layout.py`（自包含，只依赖 `struct`）。
- 换掉 `tools/dtbparts.py`（仓库那份是**严格超集**：工装区独有行 = 0）。
- 换掉 `tools/flash-round.sh`（仓库那份也是**严格超集**：工装区独有行 = 1，
  就是那行旧门禁；它还多一道 `write-root` 的新鲜度门禁）。
- **定向**改四处参数（不动别的内容，因为工装区那些文件里有独有内容）：
  `flash-direct.sh`（`EXPECT` 默认值 + 注释）、`preflight.sh`、
  `lan1roundcheck.sh`、`gettrial.sh` ⇒ 全部 `layout=native241 root=0xf00000,0xf100000`。
- 顺带带上仓库那份 `flash-direct.sh` 里的一个**真 bug 修复**：
  `ok=$(... | grep -c "READ_OK")` 在 `set -eu` 下**找不到时 `grep` 退出 1，
  会把脚本在这里杀掉** —— 正好是在「零条 READ_OK」这种**最该说话**
  的情形（ABORT / TIMEOUT）之前。改成 `grep -c ... || true`。

**验证**

- 五个改过的脚本 `sh -n` 全过（`scratch/live_syntax_check.sh`）。
- 新门禁对暂存的旧 dtb **明确拒绝**（`current240`，rc=1），
  不带 `layout=` 时**仍可只读检查**（rc=0）——
  历史的 vendor 载荷要能查，只是不能被当成新件刷进去。

**未做**

- 工装区里另外 14 个与仓库不同的文件**没有动**（`postflash.sh`、
  `preflashbackup.sh`、`mkctlram.py` 等各有用途上的分叉）。
- 工装区**不在版本库里**（工作区根不是 git 仓库），
  所以这次同步**没有可推送的产物**；仓库 `tools/` 本来就是对的。
- 实机那一步仍要等 7.3 的镜像，且**必须用 CI 导出的那三件**。

---

### 7.26 面板 LED 拓扑：6.18 线有门禁，7.3 线没有

**判决**

1. **6.18 发布线有这道门禁，7.3 线没有。**
   6.18 线（`build-sk-d840n.yml:663`）：

   ```sh
   python3 tools/check-sk-lan1-led.py --dtb /tmp/zx279133-sk-d840n.dts.dtb &&
   python3 tests/test_sk_lan1_led.py --dtb /tmp/zx279133-sk-d840n.dts.dtb
   ```

   7.3 线编出了板级 dtb（`/tmp/sk-d840n.dtb`），却**只查了分区表**。
2. **后果**：dtb 里 GPIO 号、极性、触发器或占用关系改错了，7.3 线**不会拦**。
   而那盏灯正是实机对拍的六项读数之一（**LOS 灯随 `opsd` 翻转，有光⇒灯灭**）——
   灯不亮、或者灯亮反了，**看起来像板子没光**，而板子其实有光。
3. **这不是配置问题，是 dtb 的问题。**
   面板灯需要的内核符号 —— `LEDS_CLASS`、`LEDS_TRIGGERS`、
   `LEDS_TRIGGER_NETDEV`、`LEDS_TRIGGER_DEFAULT_ON`、`USB_LEDS_TRIGGER_USBPORT` ——
   **早就被 pin 成 =y**（`scripts/configure-sk-d840n.sh:126-135`；
   `pin_y` 按 glob 写进**这条线自己的** `config-7.3` 片段，
   见该脚本 `:473-475`），而 7.3 的两个自定义触发器
   （`zx279133-pon` 在 `kernel-overlays-73/.../zx279133-pon.c:346`，
   `zx279133-los` 在 `kernel-patches/7.3-local/140-sk-pon-omci-transport.patch:166`）
   也都在，`tools/check-sk-pon-api.py:489-516` 逐条查过
   `LEDS_CLASS` 与 `LEDS_TRIGGERS`。
   ⇒ **缺的不是符号，是没人查编出来的那个 dtb。**
4. **修法**：给 7.3 线加一道门禁，跑三样东西：
   - `tools/check-sk-lan1-led.py --dtb /tmp/sk-d840n.dtb` —— 门禁本体；
   - `tests/test_sk_lan1_led.py --dtb /tmp/sk-d840n.dtb` ——
     **门禁自己的负控**（在内存里改同一棵树，要求每一处改动都被拒），
     所以一个「什么形状都放行」的门禁过不去；
   - `tools/check-sk-lan1-led.py --kernel-config "$KC"` ——
     真合并配置里那 7 个 LED 符号（kconfig 把 tristate 夹成 `=m` 的那条路）。

**证据**

门禁查什么（`tools/check-sk-lan1-led.py:37-112`）：九个 GPIO 面板灯 +
**恰好一个** PHY LAN1 灯；标签、颜色、功能、初态、触发器**逐条**；
GPIO 占用唯一（谁都不能抢同一根针）；pinctrl 映射；
四个 DSA 用户口（port@8=lan1 / @5=lan2 / @6=lan3 / @7=lan4）；
NPPT 的 `opsd-gpios` 接线。

负控是什么（`tests/test_sk_lan1_led.py:287-343`）：对每个面板灯改
`gpios` / 极性 / `default-state` / `color` / `function` / 触发器 / `label`，
删灯、复制灯、加第二个占用者；对 LAN1 灯、对 GPIO4_2 的旧占用者、
对 DSA 口标签、对 `opsd` 接线 —— **每一处都必须被拒**。

本机跑（不带 dtb，走门禁自己的合成树）：

```
$ python3 tests/test_sk_lan1_led.py
PASS: all panel board defaults; SR1010 and unrelated boards unchanged
PASS: missing-only migration, custom/disabled/duplicate LEDs, exact WPS cleanup, idempotence and failure guards
PASS: RAM beacon yields before start and between pulses when the system LED service takes over
PASS: nine GPIO LEDs plus unique port-8 PHY LAN1, ownership, triggers and DSA mapping; real lamps require observation
PASS: LED declaration negative controls reject changed GPIO/polarity/color/function/state/trigger, WPS, duplicates and conflicts; not physical verification
PASS: shipped kernel has built-in GPIO LEDs, netdev/default-on and USB port triggers
PASS: missing or modular panel GPIO/netdev/default-on/USB support rejected
exit=0
```

**这道门禁有牙齿**（拿暂存的旧 dtb 跑，**转红**）：

```
$ python3 tools/check-sk-lan1-led.py --dtb staging/trial/flash-dtb.bin
AssertionError: wrong panel LED count (no WPS/WLAN on this SKU)
rc=1
```

那个 dtb 是更早一代的板级 dtb（LED 拓扑已经不同）——
门禁不是「什么形状都放行」。

**门禁**

- `tests/test_sk_workflows.py` 多 **5 条**断言，比的是**调用与参数**、
  不是字符串出现：门禁步存在、读的是本线编出来的 blob、
  负控读**同一个** blob、问的是合并配置、读的是**本线内核**的合并配置。
- `scratch/reverse_check_workflow.py` 多 **5 处**突变
  （4 处严格只红一条 + 1 处整步改名）：换 blob、换负控的 blob、
  把 `--kernel-config` 的参数改掉、读另一个内核的配置、整步改名。
  ⇒ `reverse check: OK (55 mutations, each red on its own check)`。

**未做**

- 本机**没有 dtc**，所以这道门禁在 7.3 线编出来的 dtb 上
  **第一次真正执行是在 CI 里**。
- 门禁查的是 **dtb 里的拓扑**，**不是实物灯**。
  板上的灯到底亮不亮，仍然只能靠实机观测
  （docs/96 §96.31 记着哪些是厂商声明、哪些实测过）。
- 7.3 线**没有** `zrec`（恢复服务）：那是 6.18 发布线的产品面，
  这条线只出镜像，刻意不含。

---

### 7.27 conduit 网口的名字：`"lan-cpu%d"` **不是**缺陷，是内核在注册时解析掉的

**判决**

1. **上一轮那条结论是错的，本节把它推翻。** 当时的推断是：
   驱动写的是 `strscpy(ndev->name, "lan-cpu%d", IFNAMSIZ)`，
   `strscpy` 是有界拷贝不是格式化器 ⇒ 网口在运行期就叫字面量 `lan-cpu%d`
   ⇒ 实机对拍读 `lan-cpu0` 会读不到。
   **错在只看了拷贝那一半，没看注册那一半。**
2. **内核在 `register_netdevice()` 时把 `%d` 解析掉**，发布名就是 `lan-cpu0`：
   - `net/core/dev.c:11441` `register_netdevice()`
     → `:11468` `dev_get_valid_name(net, dev, dev->name)`；
   - `:1475` `dev_get_valid_name()` → `:1438` `dev_prep_valid_name()`；
   - `:1445-1446` `dev_prep_valid_name()`：
     `if (strchr(want_name, '%')) return __dev_alloc_name(net, want_name, out_name);`
   - `:1380` `__dev_alloc_name()`：先校验**恰好一个 `%d`**（`:1392-1394`，
     否则 `-EINVAL`），再扫已注册的网口占用的编号，取**第一个空位**，
     最后 `:1432-1433` `strscpy(buf, name, IFNAMSIZ); snprintf(res, IFNAMSIZ, buf, i);`
     ⇒ **`lan-cpu0`**。
   ⇒ `strscpy` 只是把格式串**播种**进 `ndev->name`；真正决定发布名的是注册路径。
3. **两代树里是同一行**，所以这不是 cnjn 的 bug、也不是上游的 bug：
   - 7.3：`kernel-patches/7.3/390-net-zx279133-lan-conduit.patch:181`
     （这个补丁是**从 /dev/null 新建**这个文件的）；
   - 6.18：cnjn 那棵 `linux-6.18.38` 的
     `drivers/net/ethernet/zte/zx279133-lan-conduit.c:176`。
4. **实机已经证明了这一点**：6.18 线跑着的固件用的就是**上面那同一行**，
   而板子上是 `lan-cpu0` —— `ip -o link` 里的 `lan1@lan-cpu0:`、
   `/proc/net/dev` 里的 `lan-cpu0:`（`tools/mcp/sk-d840n-bench/test_bench.py:112,125`）。
5. **⇒ 不需要改内核**：`kernel-patches/7.3-local/` 一行都不加。
   但**需要一道门禁**：这条线一旦把格式串改了，发布名就会变；
   改成内核不认的说明符，驱动**直接注册失败**，板子上根本没有 conduit。
   而对拍读的是一个**固定名字**（`/sys/class/net/lan-cpu0/...`），
   读不到就会**静默地**读出空值 —— 看起来像「板子没流量」。

**证据**

本机实证：把内核自己的四个函数（`dev_valid_name()`、`__dev_alloc_name()`、
`dev_prep_valid_name()`、`dev_get_valid_name()`）从 `net/core/dev.c`
**原样抬出来**编成一个宿主程序，让它走驱动自己的调用序列
（`strscpy` 播种 + `dev_get_valid_name()` 解析），只有网口链表、
位图和 `strscpy` 是替身：

```
$ python scratch/netdev_name_resolve.py
the kernel calls dev_get_valid_name() at:
   err = dev_get_valid_name(net, dev, newname);
   ret = dev_get_valid_name(net, dev, dev->name);

case 1: the driver, nothing else holds the name
  seeded name   : "lan-cpu%d"
  register rc   : 0
  published name: "lan-cpu0"
  expected      : "lan-cpu0"  -> MATCH

case 2: lan-cpu0 is already taken
  published name: "lan-cpu1"

case 3: a format string with no %d (control)
  published name: "lan-cpu"

case 4: two format specifiers (control)
  register rc   : -22  (registration would fail)
  published name: (none)

== what the copy alone would have produced ==
  strscpy(ndev->name, "lan-cpu%d", IFNAMSIZ) on its own gives "lan-cpu%d"
  and that is NOT what register_netdevice() publishes.
```

⇒ **上一轮那个「重命名缺陷」脚本（`scratch/strscpy_name_check.py`）已删除**：
它只抬了 `strscpy` 的拷贝尾段、没抬注册路径，结论是错的；
留着它就是给下一个会话留一个陷阱。

**⇒ 修法**：不加内核补丁，加一道门禁
`tools/check-conduit-netdev-name.py`。它**不 grep 格式串**，而是按内核的规则
**把发布名算出来**再比对：

- `dev_valid_name()`：名字本身必须合法（否则内核直接拒）；
- 必须**恰好一个 `%d`**（两个、或别的说明符 ⇒ 内核 `-EINVAL`，驱动注册失败）；
- 取**第一个空闲编号** ⇒ `lan-cpu0`；
- 名字已被占用 ⇒ 顺延到下一个（`lan-cpu1`）；
- 源里必须有 `devm_register_netdev()` / `register_netdevice()`
  （不注册就没有名字可言）；
- 源里必须**只有一处**给网口命名。

两条线都接上了：

- **7.3 线**：新增一步 `Check the conduit netdev name against the 7.3 tree`，
  跑的是**这一轮要编的那棵树里**的驱动文件
  （`$KERNEL_TREE/drivers/net/ethernet/zte/zx279133-lan-conduit.c`）；
  文件不在就**报错退出**，不跳过。
- **6.18 发布线**：在 `Apply SK-D840N overlays` 步里，跑的是 cnjn 那棵树里的
  同一个文件（`$KT/drivers/net/ethernet/zte/zx279133-lan-conduit.c`）。
- 两条线的门禁作业都跑 `tests/test_conduit_netdev_name.py`，
  也就是**门禁自己的负控**：格式串改了、说明符不对、名字被占用、
  驱动不注册、驱动命名两次、空文件 —— 每一种都必须转红。

**反向验证**：`scratch/reverse_check_workflow.py` 现在是 **69 处变异**
（工作流 53 + 工具 2 + 本次新增 14：7.3 线 4 处、6.18 线 4 处、
门禁本体 6 处），每一处都**只转红在指定的那条检查上**。

**未做**

1. **没有在实机上读 7.3 镜像的网口名** —— 镜像还没出来。
2. 门禁是**静态**的：它读源码、按内核的规则算名字，**没有真的注册一个网口**。
   本机的实证程序是把内核那四个函数**原样**抬出来编的，
   `register_netdevice()` 本身（依赖太多）**没有**被抬出来跑；
   它的调用点是从树里**引用**的（`:11468`）。
3. 「`lan-cpu0` 一定拿到 0 号」的前提是**当时没有别的网口占用这个名字**。
   门禁把这条当成前提（`--taken` 可以显式给出占用者），
   但两棵树里都只有 `zx279133-lan-conduit.c` 一处用 `lan-cpu%d`。

### 7.28 7.3 线**第一次**产出镜像（run #36 / #37），以及产物的自证

**判决**

1. **run #36（head `ac50502`）是 7.3 线的第一次成功。**
   API 全表列出这条线 39 轮（#5 的工作流自己解析失败、连 job 都没有，
   所以不在表里）：**#1~#35 无一成功**（26 轮 failure、9 轮 cancelled），
   #36 的三个 job（repository gates / flash / initramfs）**全部 success**，
   用时 1 h 21 m 51 s。
   ⇒ **7.21 的 libbpf 修复是对的**，而且它就是那最后一道门。
2. **run #37（head `9e5f56d`）同样全绿** ⇒ 不是偶然。
3. **产物在本机自证一致**（不是转述 CI 的说法）：
   - `sha256sums` 里 6 个文件**全部对上**；
   - `flash-dtb.bin` 过 `tools/dtbparts.py layout=native241 root=0xf00000,0xf100000`
     ⇒ **native241**、root 在 **mtd5**、5 个分区、每个分区的序号都被别名钉死；
   - `flash-uImage` inflate 出来的 Image 是 **14,051,336 B**，与
     `sysupgrade-report.json` 里的 `kernel.raw_bytes` **逐字节吻合**；
   - uImage 的名字就是 `ARM64 ImmortalWrt Linux-7.3.4`；
   - 内核包叫 `kernel-7.3.4~074c9ff4fc2d9852f38c6476458c9e5e-r1.apk`。
4. ⇒ **7.3 的三件套可以进板子**——但还差一道闸（见「未做」第 1 条）。

**证据**

```sh
# 产物从哪来：artifact 的列表是公开的，内容要登录态（本机走内置 Browser）
$ python scratch/ci_artifacts.py 45116803192
run 45116803192: 2 artifact(s)
  id=14795466830  sk-d840n-73-images-initramfs   15878929 bytes
  id=14792934429  sk-d840n-73-images-flash        73408482 bytes

# 自证：三件套的和、分区表、内核版本
$ python scratch/verify_artifact.py staging/trial73/flash
ok   flash-uImage  d62cac18e355fa90352fa2e6a73716b455ba634323bedc540b9c65183a734efc
ok   flash-dtb.bin  1c80886a5df7a0ae44a27ea07f7b2993cec7ba1e4f68d8988d3e7f3cf0d37a0e
ok   flash-rootfs.jffs2  2d781630be436fecd7e2a19d8d999165d3e2b43ac94c740c425b3e2f222111d4
ok   flash-sysupgrade.bin  9fe87cbcc581b85d8b350987c4224bcd859eb168cc609dcf5b9768aaa6ef19b2
ok   immortalwrt-sk-d840n-sysupgrade.bin  9fe87cbcc581b85d8b350987c4224bcd859eb168cc609dcf5b9768aaa6ef19b2
ok   sysupgrade-report.json  3c1dc2fe4f04564add1d11e7e0ad093cdf29dddd6f714f31d0ea4e6bd1a39bb6
validated layout: native241
...
index order: 1:boot 2:kernel 3:dtb 4:parameter tags 5:root
root is /dev/mtdblock5 - U-Boot's bootargs hard-code 5, so OK
OK: the artifact is self-consistent

# 门禁载荷：由**要刷的那对字节**推出来（仓库自己的 tools/mkgate.sh）
$ sh tools/mkgate.sh
uImage      5811995 B  name='ARM64 ImmortalWrt Linux-7.3.4' load=0x80200000 ep=0x80200000 os/arch/type/comp=5/22/2/1
Image      14051336 B  text_offset=0x0 image_size=0xdd0000  md5=bd3e45a2bd547350743569fa6be2227e
dtb           14958 B  md5=ff6914d007b9b21895e1a6b3f67d5ea5
the gate kernel IS the flash kernel: inflated flash-uImage md5 == gate-Image md5 (bd3e45a2bd547350743569fa6be2227e)
```

**未做 / 还欠着**

1. **板子还没跑过 7.3。** 7.3 的那对字节在 `staging/ctl/flash.permit` 里
   **没有凭证**，而 `tools/gatecheck.sh` 只放行有凭证的字节对 ——
   而且它对 `gate-*` 凭证还要求 `kver=6.*`（那是 6.18 那次门禁跑出来的）。
   ⇒ **7.3 必须先跑一轮 RAM 门禁**（kexec 进 RAM，**不写 flash**），
   拿到 `kver=7.3.4` 的报告之后，那道闸还得为 7.3 放开一档。
   （**那道闸的版本判据已经在 7.29 改掉了**：期望版本改成从待刷镜像自己读。）
   这是**设计如此**，不是缺陷：写进去的字节如果没有在这块板上起来过，
   板子上就没有任何东西能跑（无串口、无 U-Boot 控制台、无 A/B）。
2. **本轮没做实机对拍。** bench 的 inbox 被另一个会话占着；
   板子此刻停在 **U-Boot 提示符**（`192.168.9.1` 是 U-Boot 自己的 IP：
   应答 ICMP，但没有 sshd，所以 `ssh` 卡在 banner 交换上）。
   ⇒ 既不能 SSH 进正在跑的系统，也不能发继电器命令，两条路都走不了。
3. **#38~#40 还在跑**，它们会顺带回答「本轮新加的三道门禁
   （dtb 分区表 / 面板 LED / conduit 名字）在真产物上过不过」。
4. 6.18 发布线的 gate round 凭证只认 `kver=6.*`，**这是本轮第一次撞上的
   设计边界**，下一节记它。

---

### 7.29 刷机闸门 `gatecheck.sh`：版本判据从「写死 6.x」改成「**从待刷镜像自己读**」

**判决**

1. **7.3 过不去的原因查实了**：闸门对 `gate-*` 凭证的判据是
   `case "$KV" in 6.*)`，而 7.3 的门禁轮报的是 `kver=7.3.4` ⇒ **永远拒绝**。
   这条判据的**本意**不是「必须是 6.x」——原注释写的是
   「the flash target is the 6.18.38 build this dtb was made for」，
   意思明明是「必须是**正在刷的那一个内核**」；写成 `6.*` 把「同一个内核」
   放宽成了「同一个大版本」。
2. **改成精确比对**：期望版本从**待刷那个 uImage 自己**读出来（内核的 banner），
   再与凭证里的 `kver` **逐字符相等**地比：

   | 凭证的 kver | 待刷镜像 | 结果 |
   |---|---|---|
   | `6.18.38+` | 6.18 的 uImage | 放行（旧写法也放行） |
   | `7.3.4` | 7.3 的 uImage | **放行**（旧写法拒绝） |
   | `6.18.38+` | 7.3 的 uImage | **拒绝**（旧写法**放行**） |
   | `4.19.136+` | 任意 | 拒绝（厂商内核不证明 mainline，保留） |
   | 任意 | 读不出版本的镜像 | 拒绝（fail closed） |

   ⇒ 比写死某个系列**更严**，而且**两条线都不用改代码**。
3. **顺带修掉一个真缺陷**：GNU `md5sum` 在**文件名含反斜杠或换行**时输出转义形式
   （hash 前面多一个 `\`），于是许可行永远匹配不上 —— 闸门对**任何**字节对都拒绝。
   Windows 上的绝对路径正是这种文件名。⇒ 现在把那个转义去掉。
4. **`tools/siminit.sh` 的伪造 `uname` 跟着改**：演练里那个假 `uname` 原来写死
   `6.18.38+`。新规则下凭证版本必须等于镜像声明的版本 ⇒ 演练改为
   **从暂存镜像里读**，否则 7.3 那轮会误报失败。
5. **闸门有了自己的负控测试** `tests/test_gatecheck.py`（30 项），
   并做了 **10 处突变的反向验证**：每改坏一处，控制必须**转红在指定的那条检查上**。
6. **banner 的位置不是固定的** —— 这是动手前必须先量的一件事：
   内核的 banner 串在 `.rodata` 里，链接器把它放哪儿它就在哪儿
   （7.3 那份在 inflate 后的 `0x84009a`，6.18 那份在 `0xc400b8`），
   **不是** arm64 头后第 64 字节（那里是全零）。
   ⇒ 只能**搜文本**，不能切固定偏移 —— 仓库里 `tools/checkrollback.sh:71` 与
   `tools/mkrollback.sh:44` 早就是这么做的（后者注释写得很清楚：
   「The banner is not at a fixed offset inside the Image, so search the text rather
   than slice it」）。

**证据**

```sh
# 1) banner 在哪：量出来的事实（不是假设）
$ python scratch/kver_members.py staging/trial73/flash/flash-uImage
uImage size=5811931 body=5811931 bytes
member 1: offset=0 compressed=5811931 inflated=14051336
  first 16: 1f 20 03 d5 27 40 2d 14 00 00 00 00 00 00 00 00
  'ARM\x64' magic at 56
  'Linux version' at [8650906, 9820220]
    8650906: b'Linux version 7.3.4 (runner@runnervmxfy6o) (aarch64-openwrt-linux-musl...'
    bytes before 8650906: 00 69 6e 69 74 63 61 6c 6c 5f 64 65 62 75 67 00
#    8650906 = 0x84009a；6.18 那份是 12845240 = 0xc400b8；两处的前一个串都是
#    .rodata 里的 "initcall_debug"

# 2) 两个暂存镜像各自声明的版本（闸门要的就是这个）
$ sh scratch/check_gate_scripts.sh
syntax OK  ci-build-repo/tools/gatecheck.sh
syntax OK  tools/gatecheck.sh
syntax OK  ci-build-repo/tools/siminit.sh
syntax OK  tools/siminit.sh
the version each staged image declares:
  staging/trial/flash-uImage -> [6.18.38+]
  staging/trial73/flash/flash-uImage -> [7.3.4]

# 3) 闸门的负控（30 项）
$ python ci-build-repo/tests/test_gatecheck.py
PASS: a record about this kernel allows the flash
PASS: the gate says what it allowed
PASS: a 6.18 record allows a 6.18 image
PASS: a 6.18 record does NOT allow a 7.3 image
PASS: the refusal names both versions
PASS: a 7.3 record does NOT allow a 6.18 image
PASS: a vendor-kernel record does NOT allow a mainline image
PASS: a vendor-kernel record does NOT allow the vendor image
PASS: an image without a banner is refused
PASS: an image that is not gzip is refused
...
PASS: the flash gate has teeth

# 4) 反向验证：10 处突变逐个转红，且只红在指定的那条检查上
$ python scratch/reverse_check_gate.py
PASS: accept any 6.x record again turns the control red (a 6.18 record does NOT allow a 7.3 image)
PASS: trust the record instead of the image turns the control red (an image without a banner is refused)
PASS: stop reading the version at all turns the control red (a record about this kernel allows the flash)
PASS: let the vendor kernel count as a proof turns the control red (a vendor-kernel record does NOT allow the vendor image)
PASS: accept an empty gate tag turns the control red (proven=gate- is refused)
PASS: stop asking for the ECC read to have matched turns the control red (a record with root_read=DIFFERENT is refused)
PASS: stop asking for the root to have mounted turns the control red (a record with jffs2_ro=FAIL is refused)
PASS: stop asking which root partition was read turns the control red (a record with root_index=4 is refused)
PASS: let any proven= value through turns the control red (proven=guessed is refused)
PASS: keep md5sum's escape in front of the hash turns the control red (a record about this kernel allows the flash)
PASS: every mutation of the gate turns its own control red
```

**未做 / 还欠着**

1. **闸门放开 ≠ 凭证有了**：7.3 那对字节仍然**没有**门禁凭证
   （`staging/ctl/flash.permit` 里没有它们的记录）⇒ 闸门照样拒绝。
   要真的刷进去，仍须先跑一轮 **RAM 门禁**（kexec 进 RAM，**不写 flash**），
   拿到 `kver=7.3.4` 的报告；**那一轮就会把凭证写进去**，闸门随即放行。
2. **本轮没有跑那轮 RAM 门禁**：bench 的 inbox 被别的会话占着，板子停在 U-Boot。
3. **闸门自身没在真板子上验过**：负控是 PC 侧跑的（合成 uImage）；
   7.3 的真 uImage 只验过「读得出版本」这一件事。
   ⇒ **真凭证那一轮**才会第一次把真字节喂给闸门。

---

### 7.30 门禁轮的 payload：版本也从**待刷镜像**推导，7.3 那轮才收得到报告

**判决**

1. **7.3 的门禁轮会没有报告。** `tools/mkctlram.py` 生成的 announce payload 里写着
   `case "$(uname -r)" in 6.*) PAYLOAD=1`：只有 6.x 内核才武装 RAM mailbox，
   在 7.3 上它只会说 `mbox_write=off-kernel-is-not-the-payload`。
2. **而 RAM mailbox 是 mainline payload 唯一的报告通道** —— payload 自己的注释写着：
   「mainline 没有 lan bridge，它只有 eth0，而交换驱动不在这棵内核里，
   所以这个项目推过的每一份 TCP 报告都走进了它发不出去的网口」，
   pstore 那条通道只带「注册过的 pstore 写过什么」。
   ⇒ **mailbox 不武装 ⇒ 这一轮什么也收不到 ⇒ 没有凭证 ⇒ 刷不进去。**
   这不是「少一条冗余通道」，是**主通道**。
3. **改成从待刷镜像推导**：`--kver` 默认读 `staging/trial/flash-uImage` 的 banner，
   烤成 `7.3.4*) PAYLOAD=1`。厂商 4.19 仍然不匹配（控制轮照样一个字都不写），
   真正的安全网 —— 保留窗口必须在 `/proc/iomem` 里 —— **一个字没动**。
4. **演练多了一道检查**：「payload 骑的必须是待刷的那个内核」——
   把 payload 的 `/init` 里那条模式抠出来，和暂存镜像的版本对。
   这正是今天才发现这个坑的那道检查（它当场转红）。
5. **沙箱里把整条链跑通了**（`scratch/sim73/`，**不碰 live 工装区**）：
   7.3 那对字节 → payload 报告 `kver=7.3.4` → mailbox 武装 →
   `gatewatch` 写凭证 → **闸门放行** → 厂商轮与「根挂不上」的轮**仍然被拒**。
   ⇒ **SIM OK**。

**证据**

```sh
# 1) payload 的版本从待刷镜像推导出来（不是写死的）
$ python tools/mkctlram.py --mode announce --tag K73 \
        --staged scratch/sim73/staging/trial/flash-uImage \
        --out scratch/sim73/staging/ctl/ctl-initramfs.cpio.gz
the payload rides the kernel: 7.3.4*
...
$ python scratch/payload_flavour.py scratch/sim73/staging/ctl/ctl-initramfs.cpio.gz
init: 16338 B
case "$(uname -r)" in    yes    the payload's kernel gate
   'case "$(uname -r)" in'
   '7.3.4*) PAYLOAD=1'          <- 不再是 '6.*) PAYLOAD=1'

# 2) 整条链的演练（沙箱，不碰 live 工装区）
$ sh scratch/sim73_rehearsal.sh
...
  | kver=7.3.4
  | mbox_write=off-window-not-reserved      <- 不再是 off-kernel-is-not-the-payload
  ok   reports its kernel version
  ok   the payload rides the kernel that is staged
  ok   report states the mailbox verdict
...
=== permit written by that verdict ===
  | ts=2026-11-23T15:30:01 uimage_md5=e041084ac56287d46d1f0144beab659f \
        image_md5=bd3e45a2bd547350743569fa6be2227e \
        dtb_md5=ff6914d007b9b21895e1a6b3f67d5ea5 \
        kver=7.3.4 root_index=5 root_read=IDENTICAL jffs2_ro=OK proven=gate-K73
gatecheck: ALLOWED (gate-K73)
  ok   a flash of exactly these bytes would be permitted
  ok   a vendor-kernel round is still recorded as it reported
  ok   a gate round that ran the vendor kernel does NOT permit the flash
  ok   a round that cannot mount root is recorded as it reported
  ok   a root mainline cannot mount does NOT permit the flash
  ok   early marker is in the window before any flash read
  ok   the finished report is four blocks past the early marker
  ok   and it says the window was armed
  ok   the readback proves /dev/mem took the write
  ok   the extras get the finished report too
  ok   the recovered record reaches the log
SIM OK - payload report to flash permit, all on the real /init bytes

# 3) payload 门禁自己的负控
$ python ci-build-repo/tests/test_ctl_payload.py
PASS: the 7.3 image declares 7.3.4
PASS: the 6.18 image declares 6.18.38+
PASS: a file that is not a uImage declares nothing
PASS: the payload rides the staged 7.3 image
PASS: the payload rides the staged 6.18 image
PASS: an explicit request wins over the image
PASS: the payload gates on the resolved version
PASS: the payload no longer gates on a series
PASS: the vendor kernel still does not match
PASS: the gate payload rides the kernel it was built for
```

**未做 / 还欠着**

1. **还没在真板子上跑这一轮**：bench 的 inbox 被别的会话占着，板子停在 U-Boot。
2. **live 工装区的 `tools/mkctlram.py` 比仓库那份旧**（少 sysfs/netdev 那几个报告字段，
   `tools/pstore2report.py` 的字段白名单里也没有它们）。
   本轮**只把 `--kver` 这一处改过去，没有整份覆盖** ——
   整份覆盖要连带白名单一起改，是另一件事，不该混在「让 7.3 轮能跑」里做。
3. **7.3 的 announce payload 已经在沙箱里生成好了**
   （`scratch/sim73/staging/ctl/ctl-initramfs.cpio.gz`），
   但**没有**写进 live 的 `staging/ctl/` —— 那要等 bench 空闲、真跑那一轮的时候。

---

### 7.31 三片（第 3/4/5）的代码确实进了那个 7.3 镜像；以及 bench 的硬件链路早就断了

#### 7.31.1 判决

1. **第 3/4/5 片的代码在 CI 产出的 7.3 镜像里**——不是「应该在里面」，是**在**，
   而且是**按字节**核对的。
2. **这件事原来没有门禁管**，现在有了：新增 `tools/check-sk-image-drivers.py`
   与 `configs/sk-d840n-73-image-drivers.json`，直接问**产物**。
3. **第 6 片（实机对拍）推不动的原因是硬件，不是代码**：板子的两条链路
   （串口与网口）**从 2026-10-28 起就没有载波 / 没有枚举**，
   而本机还有个 TUN 代理**在应答 `192.168.9.0/24` 的 ARP**，
   所以「ping 得通」这件事**一直是假阳性**。

#### 7.31.2 证据一：run #39 的逐步结论（不是「最近几轮是绿的」）

```
$ python scratch/ci_runs_73.py 8ad7484
run #39 id=45123469622 status=completed conclusion=success head=8ad74843
    job 137522341139  repository gates  completed/success
    job 137522497484  initramfs  completed/success
    job 137522497513  flash  completed/success

$ python scratch/job_steps.py 137522497513
job 137522497513  flash  completed/success
   12  completed/success  Check the 9132 switch driver against the 7.3 tree
   13  completed/success  Check the PON / OMCI stack against the 7.3 tree
   14  completed/success  Check the 9132 switch programming against the 7.3 tree
   25  completed/success  Compile  [2026-11-23T06:28:30Z -> 2026-11-23T07:14:49Z]
   26  completed/success  Gate: the zte drivers are built into the kernel
   27  completed/success  Gate: the merged kernel config carries the board requirements
   28  completed/success  Gate: every compatible the device tree names has a driver
   31  completed/success  Validate and export the partitions
```

`Compile` 用了 **46 分 19 秒**并成功 —— 这意味着三片的 `.c` **被编过**：
Kbuild 里少一个目标文件，整棵树就编不出来。

**但「编过」还不是「在镜像里」**，所以有第二层证据。

#### 7.31.3 证据二：字节级 —— 三片的特征串都在那个 uImage 里

```
$ python scratch/image_has_drivers.py staging/trial73/flash/flash-uImage
=== staging/trial73/flash/flash-uImage (5811995 bytes on disk) ===
    inflated payload: 14051336 bytes
    FOUND  slice 3: switch driver       x3   (zx279133-switch.c 的 of_match 名)
    FOUND  slice 3: 9132 internal PHY   x1   (9132 内部 PHY 的注册名)
    FOUND  slice 3: lan1 LED cdev       x1   (面板 LED 拓扑)
    FOUND  slice 4: pon device          x7   (PON netdev)
    FOUND  slice 4: omci counters       x2   (OMCI 传输计数)
    FOUND  slice 5: np.c                x5   (9132 芯片编程)
    FOUND  slice 5: eram tables         x1   (PPU 字名表)
    FOUND  conduit                      x1   (conduit 格式串)
```

**一个坑，记在这里**：uImage 的头部是 **64 字节**，载荷在 `0x40`；
第一版探针脚本按 raw Image 的 `ARMd` 魔数（`0x38`）去判，于是**根本没解压**，
八个探针全部 MISS —— 看起来像「三片都没进镜像」。
**这是「测的是自己的假设」的又一例**：判据错了，结论就会整片反过来。

#### 7.31.4 证据三：负控 —— 用 7.3 的清单去查 6.18 的镜像

同一份探针清单查 6.18 那个 uImage：

```
$ python tools/check-sk-image-drivers.py --image staging/trial/flash-uImage \
        --manifest configs/sk-d840n-73-image-drivers.json --board-layer kernel-overlays-73
  inflated: 21256200 bytes, kernel 6.18.38+
  slice 3: the panel LED topology: the image does not carry 'skyworth:green:lan1'
  slice 4: the OMCI transport counter: the image does not carry 'omci_transport'
FAIL: 2 of 7 probe(s) failed - the image is not this board's kernel
exit=1
```

**恰好红在那两条不一样的探针上**，其余五条两边都有 ⇒ 这道门禁**有分辨力**，
不是恒绿。

#### 7.31.5 ⇒ 新增的门禁

`tools/check-sk-image-drivers.py` 的判据是**两半分开**：

| 半 | 判据 | 转红意味着 |
|---|---|---|
| 源 | 探针的串**还在它声称的源文件里** | 源被改名或改写 ⇒ 探针过期，**不是**放行 |
| 像 | 探针的串**在 inflate 后的内核里** | 这段代码**没有被编进**要刷的镜像 |

探针清单**不在工具里**，在 `configs/sk-d840n-73-image-drivers.json` ——
发布线的内核里是**另一批驱动**，清单必须分开；清单本身也进了 path filter，
改了它就要重跑构建。

工作流里新增一步（`Gate: the image carries the board drivers`），
位置在 **`Validate and export the partitions` 之后** —— 那一步才产出
`flash-uImage`，也就是**真正要刷进板子的那对字节**。

#### 7.31.6 门禁自己的负控与反向验证

* `tests/test_sk_image_drivers.py`：**9 项负控**（针在像里/不在像里、
  针不在源里=过期、源文件不存在、不是 uImage、载荷不是 gzip、
  清单没有探针、**针在第二个 gzip 成员里**、两个探针错一个）。
  「第二个 gzip 成员」那项是故意的：`gzip.decompress()` 只读第一个成员，
  读法一变就少半个内核，而那**不会报错**。
* `scratch/reverse_check_image_gate.py`：**16 处突变**，
  每一处都必须**转红在指定的那条检查上**（工具 7 处、工作流步 4 处、
  path filter 2 处、清单 3 处）。实测 16/16。

#### 7.31.7 实机那一侧：bench 的两条链路都断了（且「ping 通」是假象）

**证据（本机实测，2026-11-23）**：

```
$ [System.IO.Ports.SerialPort]::GetPortNames()
（空）

$ Get-PnpDevice -PresentOnly | Where-Object { $_.InstanceId -like 'USB\VID_1A86*' }
（空）          # CH340 连「在不在」都查不到，只剩一个 Status=Unknown 的幽灵 COM3

$ Get-NetAdapter -Name '以太网'
Status=Disconnected  LinkSpeed=0 bps   ifIndex=19      # 连续 24 秒不跳变

$ Get-NetRoute -AddressFamily IPv4 | ? { $_.DestinationPrefix -eq '0.0.0.0/0' }
ifIndex 13  0.0.0.0/0  0.0.0.0  RouteMetric 0   # xray_tun（Wintun）
ifIndex  8  0.0.0.0/0  192.168.10.1  RouteMetric 10

$ Get-NetNeighbor -AddressFamily IPv4 | ? { $_.IPAddress -like '192.168.9.*' }
ifIndex 13  192.168.9.25  Reachable
ifIndex 13  192.168.9.2   Stale
ifIndex 13  192.168.9.1   Probe
```

**这四条合起来是一句话**：`192.168.9.0/24` 的邻居解析**全落在 TUN 适配器上**
⇒ **是 `xray_tun` 在应答 ARP**，不是板子。旁证：连**本机自己的** `192.168.9.25`
都能「连上」22/23/80/443/8080/2323/5555/9 **全部端口**却**一个 banner 都读不到**
（`Connection timed out during banner exchange`）—— 真机不会这样。

⇒ **「ping 得通」是假阳性**，这一轮之前的任何一次「板子可达」结论都要重新看。

另外两条证据说明 bench 的守护进程早就没了：
`relayloop.log` 最后一行是 `2026-11-02 10:32 instance 4 starting`，
`bench-keep.log` 最后一行是 `2026-11-01 09:06 relay=1 watch=1`，
`plugphase.log` 最后一条是 `2026-10-28 00:35`（相位监视器停了 26 天）。

#### 7.31.8 未做

1. **第 6 片（实机对拍）没有推进**：板子要重新插上 CH340、插上网线、
   确认智能插座供电，然后 `tools/bench-keep.ps1` 把继电器与冷启动看门狗拉起来。
   **本机做不了这三件事**，需要人在现场。
2. **TUN 代理的绕行没做**：板子回来以后，`192.168.9.0/24` 要么走以太网
   （更具体的路由会赢过 `0.0.0.0/0`），要么把这一段从 `xray` 的路由里排除。
   本轮**没有动代理的配置**（不在仓库里，也不在授权范围内）。
3. **发布线没有接这道门禁**：它的内核里是另一批驱动，
   要接得先量它自己的特征串 —— 那是另一件事，不该混在 7.3 线里做。

---

### 7.32 「把内核版本写进判断」这个类别：查完、改掉、加 lint

#### 7.32.1 判决

1. **整条 PC 侧链上还有八处「拿内核版本字面量做判断」**，
   其中**三处会让 7.3 线直接失败**（不是误报，是**真会拒绝好东西**）。
2. 三处都改成**从待刷镜像自己读**，或者从**这份出货报告自己说**。
3. 剩下五处全是**厂商内核 4.19** —— 那是**板子上的固定事实**
   （烧死在厂商 flash 里，我们改不动），所以进**允许清单**并写明理由。
4. 新增一道 **lint**（`tools/check-baked-version.py` + `configs/baked-version-allow.json`）
   把这个类别钉死，接进 7.3 线的门禁作业。

#### 7.32.2 证据一：改之前，三处会让 7.3 直接失败

| 位置 | 原来写死的东西 | 7.3 上会发生什么 |
|---|---|---|
| `ci-build-repo/tools/preflash.py:162` | `note("6.18.38" in text, "the kernel announces 6.18.38", ...)` | 刷机前门禁**报问题**、退出码非零 ⇒ **刷不下去** |
| live `tools/flashverify.sh:97` | `check "kernel from flash starts" 'Linux version 6\.18\.38' "$WAIT"` | 刷完之后的验收**判 FAIL**（板子可能好得很） |
| live `tools/round.sh:97` / `onewindow.sh:111,117` | `case "$ver" in 6.18*)` | RAM 轮会说「板子回了 '7.3.4' —— 不是 mainline 内核」⇒ **一轮白跑** |

**这三处的共同点**：它们问的是「这个内核是不是我以为的那个」，
而答案被写死成**某一个系列**。7.29 与 7.30 已经把同样的毛病从
**闸门**与**payload** 里拿掉了，这一轮把它从**其余全部**地方拿掉。

#### 7.32.3 ⇒ 改法：版本从待刷镜像自己读

| 地方 | 改成 |
|---|---|
| `tools/check_sysupgrade.py` | 出货报告的 `kernel` 段**多一个 `version`**（从内核自己的 banner 读），**两条线共用这个工具** ⇒ 两边的出货报告都带版本 |
| `tools/preflash.py` | 期望版本 = **这份出货报告自己说的**（`sysupgrade-report.json` 的 `kernel.version`）；另问一句「它是不是厂商内核」（4.19 ⇒ 拒）。**报告没写版本**时只做厂商检查，并说明为什么 |
| `tools/checkrollback.sh` | 「是不是厂商内核」这件事**对任何 mainline 都是错的**（回滚包必须装厂商内核，否则回滚不回 PON）⇒ 判据从「`6.18*` 是 mainline」改成「**不是 4.19 就是 mainline**」，6.18 与 7.3 一视同仁 |
| live `tools/flashverify.sh` | 期望版本 = `python tools/uimage-kver.py "$DIR/flash-uImage"`（**仓库那份已定向同步到工装区**）；`KVER=` 留给离线控制 |
| live `tools/round.sh`、`onewindow.sh` | 判据 = **「不是厂商内核」**（`''|4.19*` 是「不是」，其余都是）；严格版本判据在**闸门**与**刷机前门禁**里 |

#### 7.32.4 证据二：新写的 lint 扫出来的**全量命中**

`tools/check-baked-version.py` 的判据是**位置**而不是文本：
版本字面量必须落在**决策位**上（shell 的 case 分支、比较的一边、
grep 的模式参数、指名内核版本行的模式串），
而**不在**消息、docstring、IP 地址、fwtool 元数据版本里。

```
$ python tools/check-baked-version.py --root tools
allowed tools/checkrollback.sh:73  4.19*   (the vendor kernel is 4.19 ...)
allowed tools/gatecheck.sh:97      4.19*   (the vendor kernel is 4.19 ...)
allowed tools/preflash.py:206      4.19    (the vendor kernel is 4.19 ...)
allowed tools/pstorecatch.sh:36    4.19*   (the vendor kernel is 4.19 ...)
allowed tools/runround.sh:134      4.19*   (the installed system has to be the vendor one ...)
allowed tools/runround.sh:140      4.19*   (the installed system has to be the vendor one ...)
allowed tools/siminit.sh:232       4.19.136 (the rehearsal fabricates a vendor round ...)
allowed tools/siminit.sh:267       4.19.136 (the rehearsal fabricates a vendor round ...)

0 version literal(s) in a decision, 8 allowed
```

**为什么 4.19 可以留在判断里，而 6.18/7.3 不行**：
厂商内核是**板子上的固定事实** —— 它烧死在厂商 flash 里，
这个项目里**没有任何一条线能改它**；
而 6.18 与 7.3 是**我们自己的两个构建**，写进判断里就必然有一边是错的。
允许清单里每一条都必须写明这个理由，**否则 lint 变红**。

**允许清单不许失效**：一条允许项在扫描的树里**匹配不到任何行**时，
lint 会报 `stale allowance` —— 那是**洞**：它原来守的地方没了，
下一个长得像的地方也会被放行。

#### 7.32.5 live 工装区也扫了（它不在仓库里 ⇒ lint 覆盖不到）

```
$ python tools/check-baked-version.py --root C:/UGit/openwrt/SK-D840N/tools ...
0 version literal(s) in a decision, 6 allowed
```

改之前同一棵树上是 **11 处**（多出来的三处就是 7.32.2 那三处）。

#### 7.32.6 门禁自己的负控与反向验证

* `tests/test_sk_baked_version.py`：**15 项负控** ——
  该判的四种形状（case 分支 / 比较 / grep 模式 / 指名版本行）都判，
  不该判的五种形状（消息、docstring、IP、元数据版本、只被赋值的变量）都不判，
  允许项的三种边界（命中、不命中、失效）都验。
* `scratch/reverse_check_baked_version.py`：**7 处突变**，
  每一处都**转红在指定的那条控制上**（实测 7/7）。

**这轮 lint 自己抓到了它自己的两个漏洞**：第一版的判据只认
「字符串后面跟比较符」的形状，认不出 `x == "6.18.38"`；
也认不出 `grep -q 'kver=4.19.136+'`（grep 和模式之间隔着选项）。
两条都是**负控先红了**才改的 —— 门禁自己也是靠负控长的。

#### 7.32.7 未做

1. **发布线没有跑这道 lint**：仓库的 `tools/` 是两条线共用的，
   7.3 线的门禁作业已经会扫到它；给发布线也加一道是另一件事，
   本轮没做（发布线一动就要发版）。
2. **live 工装区不在仓库里** ⇒ CI 的 lint 覆盖不到它；
   本机扫过了（7.32.5），但**下一次有人改 live 的脚本，CI 不会拦**。
   这些脚本（`flashverify.sh`、`round.sh`、`onewindow.sh`、`flashverify-test.sh`）
   **仓库里一份都没有** —— 这是个记着的缺口。
3. **`round.sh` / `onewindow.sh` 的判据是「不是厂商内核」而不是精确版本**：
   严格版本判据在**闸门**（`tools/gatecheck.sh`，7.29）与
   **刷机前门禁**（`tools/preflash.py`，本节）里；那两处是精确比对。

---

### 7.33 6.18 的 #394 红了：是我上一轮加的；而「版本从待刷镜像读」那条链一直没人走过

#### 7.33.1 怎么发现的

6.18 的 #394（head `ecefbd0f`）在**第 6 步**
「Prepare firmware tools and upgrade regression tests」就红了，
后面 28 个步骤全是 skipped。job 日志匿名下载不可用（403），
所以走 `check-runs → annotations`：
注解只有两条，第一条是 Node.js 20 的弃用警告，第二条是
`Process completed with exit code 1`（`.github:187`）—— **没有说哪一行**。

那一步跑的是：

```sh
"$PY" tests/test_sysupgrade.py
"$PY" tests/test_mtd_rw.py
sh -n overlays/.../platform.sh
```

**本机复现**（`python tests/test_sysupgrade.py`）：

```
ValueError: the kernel carries no version banner
...
FAIL: test_cli_selects_exact_board_and_refuses_ambiguity
Ran 19 tests ... FAILED (failures=1, errors=10)
```

#### 7.33.2 真因：上一轮自己加的硬失败

`ecefbd0f`（判决 21 那一轮）把 `tools/check_sysupgrade.py` 的 `inspect_kernel()`
改成「从内核 banner 读出版本，写进出货报告」，而**读不到就 `require()` 掉了**：

```python
match = BANNER.search(raw)
require(match is not None, "the kernel carries no version banner")   # ← 这一行
```

而 `tests/test_sysupgrade.py` 的夹具（`uimage()`，第 62 行）造的合成 kernel 是

```python
raw = b"self-contained ARM64 kernel" * 32      # ← 里面没有 banner
```

⇒ 合成镜像**本来就没有 banner**，被当成损坏处理了。
**这条错误是我自己写进去的**，而且它拦的是**所有**用到 kernel 成员的测试（10 个 error 是它的级联）。

修法：**读不到 banner 不是损坏，是「版本未知」。**
报告改成写 `"version": null` 并加一个 `"version_known"` 标志 ——
这样「报告没这个字段」（旧报告）和「报告说这镜像没有 banner」（新报告，是构建出了问题）
**能被分开**。

#### 7.33.3 更严重的那件事：出货报告从来没有落过盘

顺着这条线查下去，发现上一轮改的「期望版本 = 报告里的版本」**在发布线上是死代码**：

| | 7.3 线 | 6.18 发布线 |
|---|---|---|
| `check_sysupgrade.py` 的输出去哪了 | `> sysupgrade-report.json`，并 `cp -a` 进 `images/` | **只打进 CI 日志** |
| release 资产里有 `sysupgrade-report.json` 吗 | 不发 release | **没有** |
| `tools/preflash.py` 的 `release_version()` | （同上） | **永远返回 `None`** |

⇒ `preflash.py` 里那句「期望版本 = 报告里的 `kernel.version`」
**从来没有拿到过值**，一直走的是「报告没版本 ⇒ 只做厂商检查」那条降级分支。
**判决 21 修掉的三处「把版本写进判断」是实的，但换上去的那一处当时并没有真的接上。**

这才是本轮真正要补的：**报告得落盘，而且得进 release 资产**
（`preflash.py` 读的是 `staging/trial/<tag>/sysupgrade-report.json`，
那个目录的内容来自 release 资产）。

#### 7.33.4 改法

1. **`tools/check_sysupgrade.py`**：banner 读不到**不再硬失败**；
   `version` 记 `null`，另加 `version_known` 标志。
2. **新增 `tools/check-release-kernel-version.py`**：**独立**读一遍报告、
   **独立**读一遍镜像，再比对两个版本。
   为什么不复用 `check_sysupgrade.py` 写报告时读的那份？
   **写报告的工具替自己作证不算证据** —— 它刚把 banner 读错过一次。
   这道门禁要求：报告必须说出版本；镜像必须带 banner；**两者必须相等**。
3. **6.18 发布线**：`check_sysupgrade.py` 的 stdout 落盘成 `sysupgrade-report.json`，
   进 artifact、**进 release 资产**；导出之后立刻跑上面那道对照门禁。
4. **7.3 线**：本来就有落盘（`images/`），补上同一道对照门禁。
5. **`tools/preflash.py`**：改成**三态**。
   - `version`（新报告说得出版本）⇒ **精确比对**，版本不同即失败；
   - `no-banner`（新报告说这镜像没有 banner）⇒ **红灯**：
     没有可比的东西，一道不能被检查的刷机不是这道门禁放行的刷机；
   - `absent`（旧报告没有这个字段）⇒ 降级为只做厂商检查，**并说明**。
   旧报告仍然能刷：这条降级是给已经发出去的 release 留的路。

#### 7.33.5 负控与反向验证

* `tests/test_release_kernel_version.py`：**25 项控制**。
  不只验「拒绝」，还验**拒绝的理由** —— 「报告说这镜像没有 banner」与
  「报告说有 banner 却不说版本」是两种不同的毛病，
  只知道「被拒了」的门禁分不出它们。
  也验**旧格式报告仍然被接受**（向后兼容）。
* `scratch/reverse_check_release_kver.py`：**12 处突变**，
  每一处都必须**转红在指定的那条控制上**（实测 12/12）：
  门禁 4 处、`preflash.py` 3 处、`check_sysupgrade.py` 1 处、两个工作流 4 处。
  其中「把 `check_sysupgrade.py` 改回硬失败」那一处，
  红的是 `tests/test_sysupgrade.py` 自己的
  `test_cli_selects_exact_board_and_refuses_ambiguity` —— **正好是 #394 的那个失败**。
* `tests/test_sk_workflows.py` 新增 6 条：两个工作流都必须有这道对照门禁、
  且必须**排在导出之后**；发布线必须**写出**报告、必须**把报告放进 release 资产**；
  两条线都必须跑这道负控。

#### 7.33.6 未做

1. **发布线的 release 已经有历史资产了**：新的 release 会带报告，
   **旧的 release 不会** ⇒ 刷旧 release 走的是 `absent` 降级分支。
   这是有意的（见上），但它意味着**旧 release 的刷机没有版本这一层检查**。
2. **initramfs 模式的 release 没有报告**：那一步只跑 flash 模式，
   而 initramfs 镜像本来就不是刷进 flash 的东西。
3. ~~**本机 `tests/test_sysupgrade.py` 仍有 1 个 error**：本机缺 `libfdt`
   （`cc -lfdt` 找不到库），CI 上有。这是**环境**，不是代码。~~
   ⇒ **已补齐**：本机现在有与 CI 同版本的 libfdt 1.6.1，
   这条 error 没有了（见八之十三）。
4. **这轮只修了报告链，没有碰 live 工装区**：live 的四个脚本不在仓库里（7.32.7），
   `preflash.py` 是仓库里的那一份，live 用的可能不是它 —— **这一条没查**。

---

### 7.34 第 7 片：TX 故障隔离与恢复模型，搬进 7.3 树

#### 7.34.1 6.18 世代的模型是什么

6.18 世代（`kernel-overlays/drivers/net/ethernet/zte/`）的隔离机制是
**13 个函数 + 14 个结构体字段**：

| 角色 | 函数 |
|---|---|
| 生命周期 | `zx279133_tx_recovery_init()`、`zx279133_tx_recovery_cancel()` |
| 请求 | `zx279133_tx_recovery_queue_locked()`、`zx279133_tx_recovery_request_locked()` |
| 判据 | `zx279133_tx_available_locked()`、`zx279133_datapath_attach()` |
| 失败态 | `zx279133_datapath_suspend()`、`zx279133_datapath_resume()`、`zx279133_datapath_restore_failed_locked()` |
| 工作 | `zx279133_tx_recovery_work()` |
| LAN 侧 | `zx279133_lan_datapath_get()`、`zx279133_lan_datapath_set_ready()`、`zx279133_lan_datapath_quiesce()`、`zx279133_lan_datapath_put()` |

字段：`tx_fault_queues`、`tx_recovery_queues/epoch/done[]/baseline[]/disabled/active/attempted/requests/restarts/failures`、
`datapath_users`、`datapath_failed`、`lan_datapath_ready`，外加一个 mutex 与一个 delayed_work。

#### 7.34.2 7.3 树原来是什么：**相信**完成计数器

7.3 树的 `zx279133_idm_tx_reclaim_queue_locked()`（打补丁前）：

```c
	eth->tx_reclaim_polls++;
	if (completed > tx->pending) {
		if (net_ratelimit())
			netdev_warn(...);
		completed = tx->pending;          /* ← 钳位 */
	}
	tx->done = done;                        /* ← 取硬件的原始值 */
	reclaimed = completed;
	... dma_unmap_single() / dev_consume_skb_any() / tx->pending-- ...
```

⇒ **不可信的计数器被当成真的**：增量被钳位成待发数，slot 被释放、**DMA 映射被卸载**，
而 `done` 被设成硬件那个**不可信的**值（等于把唯一的「还有什么在外头」的基准扔掉）。

**不可信的计数器不能证明 DMA 已经交还了缓冲区**；
释放一个 DMA 可能还持有的缓冲区是 use-after-free。

#### 7.34.3 树里已经有的骨架（这一条推翻了旧结论）

旧结论说「7.3 树没有这个模型」。**那是错的**。7.3 树里本来就有：

* `zx279133_datapath_suspend()` / `zx279133_datapath_resume()`（netdev.c）；
* `zx279133_lan_datapath_get()` / `zx279133_lan_datapath_set_ready()` /
  `zx279133_lan_datapath_quiesce()` / `zx279133_lan_datapath_put()`；
* `datapath_users`、`datapath_lock`、`lan_datapath_ready`。

**缺的只是故障隔离那半**：`tx_fault_queues` 的隔离、`datapath_failed`、
以及恢复工作。⇒ 这一片是「在一套几乎同构的骨架上加一层」，不是从零造。

#### 7.34.4 补丁做了什么（`160-sk-tx-fault-isolation.patch`）

四个文件，**405 行新增**：

* `zx279133.h`：**14 个字段**（隔离位、恢复状态、四个计数器）+ `datapath_failed` +
  `struct delayed_work tx_recovery_work` + 三个函数声明；
* `zx279133-idm.c`：`zx279133_idm_tx_reclaim_queue_locked()` 改成
  **入口先排除被隔离的环**，遇到不可信增量时**不再钳位**，
  而是 `zx279133_tx_recovery_request_locked(eth, queue, true)` 并 `return 0`
  —— 映射与旧基准都留着，直到硬件被停；
* `zx279133-netdev.c`：恢复机制本体（隔离、判据、attach、失败态、恢复工作），
  以及 `open` / `lan_datapath_get` 的「先把失败的 datapath 拉回来」、
  `lan_datapath_quiesce` 的「不 quiesce 时把暂停的共享 datapath 交给恢复工作」；
* `zx279133-main.c`：probe 里 `zx279133_tx_recovery_init()`、
  devm action `zx279133_tx_recovery_cancel()`、shutdown 里再取消一次。

**有节制的恢复**：恢复工作用 `rtnl_trylock()`（不与 teardown 死锁），
重启共享 datapath **至多一次** —— 只有「上一轮重启之后真有 TX 完成过」才允许再重启，
否则 watchdog 一次次触发就变成复位循环。

#### 7.34.5 门禁：**按位置**判的不变量（36 条）

`tools/check-sk-tx-isolation.py` 问的是**位置**，不是「某个串在不在」：

* 故障分支（`if (completed > tx->pending)`）**里没有任何释放语句**
  （`dma_unmap_single`、`dev_consume_skb_any`、`xdp_return_frame`、`xsk_buff_free`、
  `xsk_tx_completed`、`netdev_tx_completed_queue`、`tx->done =`、`tx->completed +=`、
  `tx->pending--`、`tx->consumer =`），**并且函数里所有释放都发生在这个分支之后**；
* 故障分支把环交出去、并说明「这个完成是无效的」；
* 被隔离的环在**读计数器之前**就被排除；
* 隔离动作停掉 **WAN 与 LAN 两个逻辑设备**（它们共用一个物理环）；
* 恢复由 delayed work 驱动，且**不等 RTNL**；
* 恢复的重启由「真实完成增量」重新武装；
* 拉不回来的 datapath 被标记 `datapath_failed`，下一个用户**先恢复再使用**；
* 四个计数器都在。

**它自己的一个漏洞是被负控逼出来的**：第一版的「隔离守卫在读计数器之前」
拿 `done_reg` 的**声明**当参照点，而 `done_reg` 是在函数开头声明的 ——
于是这条检查恒红。改成「读计数器的那一行」之后才对。

#### 7.34.6 负控与反向验证

* `tests/test_sk_tx_isolation.py`：**14 项**（1 正向 + 13 突变）。
  突变跑在**真树**的副本上，不在合成树上 ——
  合成树只能证明门禁读得懂合成树。
* `scratch/reverse_check_tx_isolation.py`：**11 处突变**，全部**转红在指定的那条检查上**（实测 11/11）。
  它问的是**两个方向**：树变坏 ⇒ 门禁在指定检查上红；
  **门禁变弱 ⇒ 负控自己发现「该红的检查不红了」并指名它**。
  后者是更重要的方向：门禁被悄悄削弱是正向测试看不见的。

#### 7.34.7 顺带修掉的：产物门禁在 initramfs 模式

7.3 的 **#43**（head `ace0eb7`）的 **initramfs** job 红了，红在
「Gate: the image carries the board drivers」——**上一轮（`ace0eb79`）我自己加的门禁**。

run 的注解把原因说得很清楚：

```
no sysupgrade image under openwrt/bin/targets/zte/zx279133 - nothing to validate or export
no kernel image under openwrt/bin/targets/zte/zx279133 - the image profile did not make one
```

⇒ **initramfs 模式根本不产出内核镜像**，而门禁硬要去问那个不存在的镜像。
（同一个 run 的 **flash** job 是 **success** —— 同一道门禁在有镜像的模式下是绿的。）

⇒ 门禁限定在 `matrix.mode == 'flash'`，并在 `tests/test_sk_workflows.py` 里
加了一条「这道门禁只能在有产物的那个模式里问」的门禁。

#### 7.34.8 未做

1. **本机不能编译**：仓库自带的「编译预言机」（宿主 clang `-fsyntax-only`，
   `tools/kcheck.py`）在改后的树上过了 **9 个文件、0 错**，
   但那不是编译 —— 真编译只能由 CI 证明。
2. **没有实机证据**：隔离、恢复、复位循环这三件事都要真的撞上不可信计数器才看得到。
   板子回来之前，第 7 片只有「树里的形状对」这一层证据。
3. **`tx_invalid_completions` 之外没有暴露到 sysfs**：计数器在 `struct zx279133_eth` 里，
   还没有各自的 sysfs 入口 ⇒ 实机上只能靠 `dmesg` 看。
4. **7.3 树里 `datapath_failed` 只在两个用户入口复位**：XDP/AF_XSK 的池切换
   （`zx279133_xsk_pool_cycle()`）走的是另一条 suspend/resume 路径，
   它失败时也会置 `datapath_failed`，但**没有**对应的恢复入口 ——
   那一处是已知的缺口，本轮没补。

#### 7.36 第 8 片：RX 交付路径（`170-sk-rx-delivery-safety.patch`）

补丁、39 条门禁、24 项负控、17 处反向验证的判决与证据在**八之八**。
本轮它在 7.3 工作流里的位置：

* 门禁步 `Gate: the RX delivery path proves what it hands over`
  跑在 `Gate: a TX ring with an untrusted counter is quarantined` 之后、
  `Check the overlay for what a compiler would have caught` 之前，
  两条都在 `--tree "$KERNEL_TREE"`（**要编译的那棵树**）上跑；
* `paths:` 清单加了 `tools/check-sk-rx-delivery.py` 与
  `tests/test_sk_rx_delivery.py` ⇒ 改门禁或改负控都会起一轮构建。

#### 7.37 **#46 的失败：门禁红了 19 条，而树里没有那批符号**

7.3 的 **#46**（head `1eb07e3b`）的 `initramfs` 与 `flash` **两个** job 都红在
step 20「Gate: a TX ring with an untrusted counter is quarantined」：
门禁只跑了 **25 条**检查、**19 条**红，红的内容**全部**是
「160 引入的符号/字段不存在」。

**本机逐条排除（每一条都有做法，不是推断）：**

| 假设 | 怎么排除的 | 结果 |
|---|---|---|
| CI checkout 的是另一份 160 补丁 | raw.githubusercontent 逐字节比对（8 个补丁 + MANIFEST） | **一致**（24508 B，sha256 相同） |
| MANIFEST 漏登记 160 | 比对 `1eb07e3b` 的 `MANIFEST.json` | 7 条，**含 160** |
| prepare / 门禁脚本被改 | 逐字节比对 | **一致**（16006 B / 13347 B） |
| 板层覆盖了那 4 个文件 | `kernel-overlays-73/` 只有 8 个文件，不含它们 | **排除** |
| prepare 之后有 `git checkout/reset` | 逐行扫完整 job 日志 | **没有** |
| 门禁的 C 解析器版本不同 | 用 **commit 版**门禁跑本机三棵树 | 全绿 36/36 |
| Python 版本不同 | 用 **Python 3.12.14**（CI 的版本）跑 | 全绿 36/36 |
| `git apply` 在仓库内外行为不同 | 小实验：仓库内/外各 apply 一次 | **一致** |
| 门禁跑在另一棵树上 | 日志：同一 `--tree linux-7.3.4`，prepare 的 `source_directory` 也是它 | **排除** |

**剩下的事实链（三条，都是日志里的原文）：**

1. CI 的 prepare **确实应用了 160**：
   `Applied local/160-sk-tx-fault-isolation.patch`、
   `Prepared Linux 7.3.4 with 46 patches`。
2. 门禁在**同一棵树**上、**36 毫秒之后**运行，报告
   「the eth carries the recovery work」红 —— 而这条检查是**纯字符串查找**
   （`"struct delayed_work tx_recovery_work;" in header`），
   **不是**函数解析 ⇒ **CI 树里的 `zx279133.h` 确实没有 160 的内容。**
3. 本机完整复刻（vanilla + 39 vendored + 100~150 → apply 160 → 门禁）
   **36/36 全绿**；而把「160 之前」的树喂给同一个门禁，得到
   **恰好 25 条检查、19 条红**，**与 CI 逐条一致**
   ⇒ **CI 的树就是「160 之前」的状态。**

⇒ 判决：**CI 上 `git apply` 报成功，但补丁的内容没有落到门禁读的那份文件里。**
这个区别在没有内容级校验时**看不见**：门禁只能红给一个「符号不存在」，
而 prepare 只能报一个「应用成功」—— 两句话都不指向真相。

**本轮补的三件事：**

1. `tools/prepare-sk-kernel-73.py` 在**每个补丁 apply 之后**，
   查这个补丁**加进去的那些行**是否真的在它声称写的那个文件里；
   不在就抛错，并**点名那个文件与那一行**。
   ⇒ 「apply 成功但内容没落盘」从此不能静默发生。
   负控 `tests/test_sk_prepare_content.py`（16 项）证明它有牙，
   `scratch/reverse_check_prepare_content.py`（4 项）证明它被削弱时负控会指名。
2. provenance（`kernel-provenance-73.json`）现在带**树指纹**：
   5 个驱动文件的 sha256 / 字节数 / 行数。
3. 两道门禁把**它们读到的那几个文件的 sha256** 写进 `GITHUB_STEP_SUMMARY`。
   ⇒ run 页面直接可读，**不必**下载 job log。

**最后这条是从 #46 的诊断里学到的**：那一轮门禁红了 19 条，
但 job log **匿名下载不到**（403），GitHub 日志搜索框对被截断的部分返回 `0/0`
⇒ 只能靠登录态浏览器一页页翻。**step summary 是 run 自己的页面，谁都能读。**

#### 7.38 **#47 的结果：prepare 步自己把话说清楚了**

补上取证装置后的第一轮（**#47**，head `c9146bc`）**红在第 8 步
「Prepare the Linux 7.3.4 tree」**，而不是门禁步 —— 新加的内容校验先一步抓住了：

```
ValueError: local/160-sk-tx-fault-isolation.patch applied but the tree does not carry it:
  ... adds to .../zx279133-idm.c   but the tree does not carry '\tu16 done;'
  ... adds to .../zx279133-main.c   but the tree does not carry '\tzx279133_tx_recovery_init(eth);'
  ... adds to .../zx279133-netdev.c but the tree does not carry 'static void zx279133_tx_recovery_work(...);'
  ... adds to .../zx279133.h        but the tree does not carry '\t * Recovery state and counters are protected by tx_lock.'
```

⇒ **日志里没有 `Applied local/160-...` 那一行**（100~150 都有）。
⇒ `git apply` **返回了 0**，而那四个文件里**没有它的任何一行**。

**这是本机 100% 复现不出来的现象**：同一份补丁（逐字节相同）、
同一份脚本、同一棵 vanilla + 39 vendored + 100~150 的树，
本机上 apply 之后内容确实落进去了（门禁 36/36 全绿）。

**所以现在的判决是**：这不是补丁写错了，也不是清单写错了，
而是**在 CI 那个环境里，`git apply` 报成功而内容没有落到它声称写的文件里**。
「apply 说成功」与「树里有这个改动」在那一刻是**两件事**，
而过去整条流水线里**只有后者会被门禁发现**。

**下一步（#48）回答「apply 到底做了什么」**：prepare 现在在 apply **前后**
各记下那几个文件的哈希，并在内容对不上时把这三件事分开说：

* 文件**一个字节都没变**（apply 什么也没做）；
* 文件变了但内容不含新增行（apply 做了别的事）；
* 那行字**落到了别的文件里**（apply 落错了地方）。

**这一轮的设备在两条线上都还在跑**（7.3 的 #41~#47、6.18 的 #391~#397），
按约定**一个都不取消**。

#### 7.39 **#48 的结果：根因是 git 把补丁默默丢掉了，而它说的是「成功」**

**判决**：`git apply` 在**一个仓库里、从子目录**运行时，对**带 `diff --git` 头**
的补丁是按**仓库根**解析路径的。内核树是 checkout 里的一个目录，
这样的路径落在当前子目录之外，git 就把整个补丁文件**默默丢掉**：

* 退出码 **0**；
* **一个字节都不写**；
* **默认什么都不说**。

带**纯 unified diff 头**（只有 `---`/`+++`）的补丁不受影响 ——
它按当前目录解析。于是同一批 47 个补丁里，
vendored 的 39 个和 local 的 100~150 **都落地了**，
只有带 git 头的 `160-sk-tx-fault-isolation.patch` 和
`170-sk-rx-delivery-safety.patch` **没有**。

**证据一：#48 的 prepare 步自己把话说清楚了**（`00c84ca8`）：

```
ValueError: local/160-sk-tx-fault-isolation.patch applied but the tree does not carry it:
  ... adds to .../zx279133-idm.c   but the tree does not carry '\tu16 done;'
  ...
what the files looked like:
  drivers/net/ethernet/zte/zx279133-idm.c: did not change at all
    before 7c461f9a19635214af07ed2948b77fce15aa3400bd5ddf26d54596a15632460e
    after  7c461f9a19635214af07ed2948b77fce15aa3400bd5ddf26d54596a15632460e
```

⇒ 四个文件的 **before 与 after 逐字节相同** —— 不是「落错了地方」，
是**什么都没发生**。

**证据二：那几个哈希就是「160 之前」的状态**。本机
`scratch/linux-73-base` 的 `8c4d767`（= 160 之前）四个文件的 sha256
**逐个等于** CI 报出来的那四个：

| 文件 | CI 报的 before/after | 本机 `8c4d767` | 本机 `5e63e46`（160 已应用） |
|---|---|---|---|
| `zx279133-idm.c` | `7c461f9a…` | `7c461f9a…` ✓ | `a1921333…` |
| `zx279133-main.c` | `84f393eb…` | `84f393eb…` ✓ | `4713f0bd…` |
| `zx279133-netdev.c` | `e29cfdbe…` | `e29cfdbe…` ✓ | `b72390f7…` |
| `zx279133.h` | `6cdde38e…` | `6cdde38e…` ✓ | `ca990f56…` |

⇒ **CI 的树就是「160 之前」**，而本机树的状态是同一套补丁**真的落进去**之后的样子。

**证据三：补丁的头不一样**。这就是全部差别：

| 补丁 | `diff --git` 头 | `index` 行 | 在 CI 上 |
|---|---|---|---|
| vendored 39 个 | 无 | 无 | **落地** |
| local 100~150 | 无 | 无 | **落地** |
| local `160`/`170` | **有** | **有** | **被丢掉** |

**证据四：git 自己会说出来，只是要加 `--verbose`**。
本机用**同一个补丁字节**、**同一个脚本**、**同一个 checkout 布局**逐字复现：

```
$ git apply --verbose ...   # 树在 checkout 里面
Skipped patch 'drivers/net/ethernet/zte/zx279133-idm.c'.
Skipped patch 'drivers/net/ethernet/zte/zx279133-main.c'.
Skipped patch 'drivers/net/ethernet/zte/zx279133-netdev.c'.
Skipped patch 'drivers/net/ethernet/zte/zx279133.h'.
```

退出码 0，四个文件**零字节变化**。把树换成「自己的仓库」或「不在任何仓库里」，
同一个补丁立刻正常落地（四个文件都变了）⇒ **变量就是「树是不是某个仓库的子目录」**。

**证据五：为什么本机之前一直复现不出来**。
本机的树 `scratch/linux-73-verify*` **不在任何仓库里**
（它们是 prepare 跑完之后才 `git init` 做了快照的），
所以 apply 走的是「无仓库」那条路，从来没踩到过这个坑。

**⇒ 结论（这条要记住）**：
**7.3 线此前编译过的每一个镜像，编的都是少了 TX 隔离与 RX 交付修复两片的驱动。**
而 36 条 TX 门禁与 39 条 RX 门禁**在 CI 上一次都没绿过** ——
它们每次红，都是因为树里没有那两片。

**⇒ 修法**（`tools/prepare-sk-kernel-73.py`）：

1. apply 放进一个 **git 找不到 checkout** 的上下文：
   `GIT_CEILING_DIRECTORIES` = 树的上级目录；
2. 补丁路径**相对树**给出，apply 的**工作目录就是树**；
3. 加一道守卫：apply 带 `--verbose`，输出里出现 `Skipped patch`
   就**直接拒绝**，不许当已应用。

**⇒ 门禁与验证**：

* `tests/test_sk_prepare_apply_context.py`（**12 项**）：自建一个 checkout
  加一个树在里面，断言**带 git 头的补丁落地**、**纯 diff 的补丁落地**、
  **context 挡住了 checkout**，以及三项反向：
  **git 确实会默默丢掉**（退出 0、零写入、stderr 里有 `Skipped patch`）、
  **被丢掉的补丁会被拒绝**、**被拒绝的树确实没有那行内容**；
* `scratch/reverse_check_prepare_apply_context.py`（**5 项**）：
  抽掉 ceiling ⇒ 门禁**在指定的那条检查上**转红；
  抽掉 `Skipped patch` 守卫 ⇒ 门禁**在指定的那条检查上**转红；
* `scratch/prove_apply160_in_checkout.py`：拿**真的** `160` 补丁、
  **真的**「160 之前」的树、**真的** checkout 布局，
  apply 之后四个文件的 sha256 **逐个等于**本机「160 已应用」的状态；
* `scratch/run_prepare_in_checkout.py`：**完整 47 个补丁**在 checkout 布局里
  跑一遍，**每一个都落地**（`Applied local/170-...` 在列），
  四个驱动文件的 sha256 与 `scratch/linux-73-verify4` **逐个相同**。

**⇒ 顺带查出来的第二件事**：这份文档里当时藏着一个 **NUL 字节**
（判决 24 的正文里），它让 ripgrep 把整个文件当成二进制 ——
**所有针对本文的 grep 都静默返回空**（本轮因此又误判了一轮）。
已清掉，并把 `tests/test_sk_text_hygiene.py` 从「只查 U+FFFD」
扩到「**U+FFFD + NUL + 其它控制字符**」（131 个文件、0 问题）。

#### 7.40 **#49 的结果：补丁真的进树了，两道门禁第一次同时变绿**

**判决**：修复生效。head `9e97ff2a` 的这一轮，
**7.3 线第一次编译的是带 TX 隔离与 RX 交付修复的驱动**。

| 步骤 | 结果 | 说明 |
|---|---|---|
| `repository gates`（作业 137632470854） | ✅ success | 新的 apply 上下文负控在 CI 的 git **2.56.1** 上也绿 ⇒ ceiling 机制与 git 版本无关 |
| 8 `Prepare the Linux 7.3.4 tree` | ✅ success | 38 秒；**47 个补丁全部落地**，160 与 170 也在内 |
| 19 `Check the drivers with the host compiler` | ✅ success | 编译预言机在带 160/170 的树上过 |
| 20 `Gate: a TX ring with an untrusted counter is quarantined` | ✅ success | **36 条 TX 门禁第一次绿** |
| 21 `Gate: the RX delivery path proves what it hands over` | ✅ success | **39 条 RX 门禁第一次绿** |

（initramfs 作业 137632800488。）

**⇒ 这两道门禁在 CI 上从来没有绿过** ——
不是因为它们太严，是因为**树里没有它们要问的那两片东西**：
每次红，都是在问「160 的符号呢？」而树停在 160 之前。
门禁是对的，**树是错的**；现在树对了，门禁自己就绿了。

**⇒ 由此可知一件必须记住的事**：
**7.3 线此前产出过的每一个镜像，编的都是少了 TX 隔离与 RX 交付修复的驱动。**
那些镜像不能拿来当「7.3 能跑」的证据。

#### 7.41 产物级门禁扩到第 7 片与第 10 片：**像**里也得有那两片

**判决**：7.31 那道产物门禁（问**要刷进板子的那个 uImage** 里有没有我们自己的驱动）
原来只有 7 条探针，覆盖第 3/4/5 片与 conduit。
第 7 片（TX 隔离）与第 10 片（TX 计数器）搬进的是 **cnjn 自己的文件**
（`zx279133-netdev.c`），不是板级层的文件 ——
「这个文件有没有被编进去」对它们**不构成风险**，
真正要问的是「**补丁有没有落到编进镜像的那棵树里**」。
而这恰恰是 #46~#49 那三轮的真实问题。

⇒ 探针清单加两条，**都问那个补丁自己引入的字符串**：

| 探针 | 串 | 来自 |
|---|---|---|
| `slice 7: the TX fault isolation` | `shared TX recovery failed` | 补丁 160 的 `netif_warn()` |
| `slice 10: the TX isolation counters` | `tx_invalid_completions` | 补丁 180 加进 `gstrings_stats` 的名字 |

**这两条是真有分辨力的**（本机实测，用**旧的**那个 7.3 镜像 ——
它编的是 160/170/180 **之前**的驱动）：

```
$ python tools/check-sk-image-drivers.py --image staging/trial73/flash/flash-uImage \
        --manifest configs/sk-d840n-73-image-drivers.json --board-layer kernel-overlays-73
  slice 3: the 9132 switch driver: HIT 'ZX279133 9132 internal PHY' x1
  ...（七条旧探针全部 HIT）
  slice 7: the TX fault isolation: the image does not carry 'shared TX recovery failed'
  slice 10: the TX isolation counters: the image does not carry 'tx_invalid_completions'
FAIL: 2 of 9 probe(s) failed - the image is not this board's kernel
```

⇒ **恰好红在新加的两条上**，其余七条两边都有 ⇒ 这道门禁对新内容**有分辨力**，不是恒绿。

**一条限制，写清楚**：**第 8 片（RX 交付修复）在产物层没有探针** ——
补丁 170 **没有引入任何字符串字面量**（它改的是判断，不是消息），
所以「像里有没有它」这件事**在字节层面无从问起**；
函数名也指望不上：kallsyms 的名字表是压缩的，本机实测
`zx279133_idm_rx_has_ethertype` 在 6.18 与 7.3 两个像里**都搜不到**。
⇒ 第 8 片由**树级**的 39 条门禁与 prepare 的内容级校验管住，
这是它**唯一**能被管住的地方，本节不假装它有产物级证据。

**顺带修掉的一个真 bug**：`tests/test_sk_workflows.py` 里那条
「探针清单覆盖了哪几片」用的正则是 `slice (\d)` ——
它把 `slice 10` 只截成 `1` ⇒ 清单里加了第 10 片之后，那条检查**恒红**。
改成 `slice (\d+)`。

**反向验证**：`scratch/reverse_check_image_gate.py` 由 16 处突变增加到 **18 处**
（新增「清单少了 TX 隔离那条探针」「清单少了 TX 计数器那条探针」），
每一处仍然**转红在指定的那条检查上**；实测 18/18。

**顺带补上的一处 path filter 漏洞**：`tests/test_sk_workflows.py`
（**门禁中的门禁**：它决定这条线有哪些步骤、哪些路径会起构建）
本来**不在** 7.3 线的 `paths:` 里 ⇒ 改它**不会**重跑构建，
而它改的正是「构建检查什么」。清单里其它每一道门禁的负控都在，
只有它自己漏了。现在在了。

---

## 八、BP 池：逐槽位对照（第 1 片剩余部分的结论）

**判决**：**BP 池布局在 7.3 上不需要移植。** 我们 6.18 世代那套写法是
**为 6.18 的 free ring 内存布局写的**，把它搬到 7.3 会指向错误的内存。
第 1 片的「BP 池布局」这一项可以关掉。

**两代的 free ring 内存布局本来就不同**（这是全部矛盾的根源）：

| | 6.18 世代（我们 overlay） | 7.3 公版 |
|---|---|---|
| `FREE_RING_SIZE` | `0x40000`（五个环**每个**都这么长） | `0x4000`（RX 两个）/ `0x40000`（TX 三个） |
| `FREE_RING0` 起 | 0x1a0000，每环 +0x40000 | 0x1a0000，RX 两环各 +0x4000，TX 三环各 +0x40000 |
| 环的语义 | FR0…FR4 | FR0 = RX normal，FR1 = RX jumbo，FR2/FR3/FR4 = TX normal / TX jumbo / CPU133 |

**五个硬件槽位逐个对照**：

| 槽位 | 7.3 公版 | 我们 6.18 世代 | 判定 |
|---|---|---|---|
| `+0x104` | `rx_normal_bp_dma`（独立分配） | `rx_normal_bp_dma`（独立分配） | 相同 |
| `+0x108` | `FREE_RING1`（RX jumbo 位，**软件不用它**） | `rx_jumbo_bp_dma`（独立分配） | 语义不同 |
| `+0x118` | `FREE_RING2` | `FREE_RING1` | **错位一位** |
| `+0x11c` | `FREE_RING3` | `FREE_RING2` | **错位一位** |
| `+0x408` | `FREE_RING4` | `FREE_RING3` | **错位一位** |

**为什么我们 6.18 世代那套不能搬**：在 6.18 的布局里 `FREE_RING1` 是
`FR0 + 0x40000`，在 7.3 的布局里 `FREE_RING1` 是 `FR0 + 0x4000`——
**同一个名字在两代指向的内存差 0x3C000 字节**。照搬过去，三个 TX
retrieval 环会指向 RX jumbo 环和 TX normal 环的位置。
而且 7.3 公版的映射是**按厂商布局一一对应**的（FR1↔`+0x108`、
FR2↔`+0x118`、FR3↔`+0x11c`、FR4↔`+0x408`），**公版是对的**，
我们 6.18 世代那套才是错位的一方。

**池大小寄存器也不需要改**：按我们自己的实测编码规则
`(((depth >> 12) - 1) << 21) | (((free_depth >> 12) - 1) << 10) | BIT(8)`，
7.3 的 `BP_RING_SIZE` = 4096 ⇒ `((1 - 1) << 21) | 0x3C00 | 0x100` =
**`0x3d00`**——**正是公版写死的那个值**。公版在这里和我们的规则一致，
因为 normal 环深度从 8192 变成 4096 时 `(depth >> 12) - 1` 正好是 0。

**内存占用**：我们 `(8192 + 4096) × 4` = **48 KiB**（两块池），
公版 `0x4000` = **16 KiB**（一块，4096 项）。

**为什么跟随公版是对的**：**目标固件（cnjn 自己发布的 7.3 固件）用的就是
公版的单池 4096 布局**——那份 `npu.ko` 就是 7.3 公版驱动集。跟随公版
等于跟随目标固件，而不是跟随我们 6.18 世代那次改动。

**未做 / 风险**：

* 我们 6.18 世代的 **8192 深度**没有带到 7.3。cnjn 自己的固件用 4096 跑得通，
  但那是公版的深度，不是这块板在 2.5G 满载下的实测深度。
  **判据**：实机跑起来后看 `ethtool -S` 的 RX 欠载/丢包计数；
  若出现欠载，再把 normal 池加深到 8192（那时 `+0x010c` 要改成 `0x203d00`，
  `+0x108` 指向独立分配的 jumbo 池，且 TX 三个环必须保持公版的 FR2/FR3/FR4）。
* **「布局」和「投递协议」是两件事，这里只判了布局。** 7.3 **有**
  `zx279133_idm_rx_flush_refill`，只是它写的是 `writel(count, BP_REFILL)`，
  而厂商的 `idm_new_bp_update()` 写的是「每 1024 个缓冲一次 0x400 脉冲，
  再写余数（normal 在 bits 15:0、jumbo 在 bits 31:16）」。
  **单池时 `writel(count)` 是否等价，没有实测依据**——厂商之所以按 1024
  记账，很可能是因为硬件内部以 1024 为单位。
  **⇒ 这一条要单独判**，判据是实机 RX 是否真的收得到帧
  （`/proc/net/dev` 的 RX 计数在涨、`ethtool -S` 无欠载）。
  我们的 6.18 实现在这里已经不是「布局」，而是**实测修正**，
  与第 1 片同源，应当随第 1 片一起在 7.3 上验证。

---

## 八之二、第 2 片：RX 帧落点与 BP 投递协议（两个补丁）

**判决**：两件事都**要移植**，各一个补丁。

### 八之二.1 `110-sk-idm-rx-frame-offset.patch`：帧不在缓冲区的头

**证据（本板自己的字节，run 160）**：驱动交给 IDM 的是「页 + 256 字节」，
回来的描述符指的就是这个地址（驱动自己的检查坚持这一点，每一帧都通过），
**而帧在这个地址再往后 0x80**。run 160 把整页打了出来：

```
page +0180: ffffffffffffff8447095bbd2c08060001
page +0190: 0800060400018447095bbd2cc0a80919
page +01a0: 000000000000c0a80901000000000000
```

完整读出来就是测试 PC 发来的 ARP request。**帧前面那 128 字节是页原来
留下的内容**（一次运行是 0，另一次是 0xff）——驱动读的是一个从未被写
过的页，交上去的每一帧都是那个页的残留（docs/77）。

**为什么这条能直接搬到 7.3**：第 1 片已经把 IDM 寄存器对齐到本板的实测
值，而 run 160 的测量**是在那次对齐之后**做的。所以它描述的是同一套
硬件，而 7.3 线上的寄存器就是同一套。

**四处必须一起动**（同一个地址的四种写法）：

| 位置 | 改动 |
|---|---|
| page pool 的 `.offset` | 加帧偏移。缓存失效（`page_pool_dma_sync_for_cpu()` 会自己加 pool 的 offset）与设备侧同步（`page_pool_put_full_page()`）都从它取 |
| 帧的 `data` 指针 | 加帧偏移 |
| XDP 缓冲 | 加帧偏移 |
| AF_XDP 池的尺寸检查 | 加上偏移后仍要放得下 |

**做成只读模块参数**：`zx279133.rx_frame_offset`，默认 `0x80`。
只读是故意的——一个池里所有缓冲的偏移必须一致，而已经交给硬件的池
不能挪。**一次 `zx279133.rx_frame_offset=0` 的启动就能在板子上把两种
形状分开**，不用在纸上争。

### 八之二.2 `120-sk-idm-bp-refill-protocol.patch`：门铃要写编码，不写计数

**判决**：上一轮判「单池时是否等价没有实测依据」，这一轮判掉了。

`IDM+0x100` 不收缓冲计数。厂商的 `idm_new_bp_update()` 写的是
**每 1024 个缓冲一次 `0x400` 脉冲，再写余数**（normal 在 bits 15:0、
jumbo 在 bits 31:16），硬件从这些字段里读出计数。**写计数本身等于没写
缓冲；计数是 1024 的倍数时等于写了零个缓冲**（docs/45 §2.3）。

**而 7.3 在启动时正好投 2048 个缓冲**（`ZX279133_IDM_RX_BUFFER_COUNT`），
**2048 正是 1024 的倍数** ⇒ 公版那一次写等于告诉硬件「池是空的」。

**这不影响 cnjn 的目标固件**（它投的也是 2048，但用的是厂商自己的
`npu.ko`），**也不影响公版在其他板上的表现**——它只说明**我们这条线**
的 7.3 镜像不能照抄公版的那一次写。

**7.3 只有单池**，所以只写厂商序列的 normal 字段。

---

## 八之三、第 3 片：9132 交换表驱动（`switch.c` 整体移植）

**判决**：**可以整体搬过去，搬法是「照抄 + 换名字」，不是重写。**
这一片是 76 个函数、4150 行，和第 1、2 片（寄存器级的小改）不是一个量级，
所以先量化，结论和预期相反。

**先纠一个比错的对象**：7.3 树里**没有** `zx279133-switch.c`，只有
`zx279133-rtl8372n.c`（243299 B）。乍看像「整体新写」，但
`zx279133-rtl8372n.c` 是给**另一颗交换芯片**用的——Realtek RTL8372N 是
SR1010 的 LAN 交换芯片；我们板上的 LAN 交换芯片是 Marvell 9132。7.3 树里
`zx279133-rtl8372n.c` 的文件头自己就写着「the RTL8372N sits on the SR1010」。

真正的对照对象是**我们自己**的 `kernel-overlays/.../zx279133-switch.c`
（159177 B，76 个函数）。逐项量化：

| 检查面 | 结果 |
|---|---|
| switch.c 用到的 `ZX279133_*`/`zx279133_*` 名字 | 201 个共用，**22 个只有我们这边有** |
| switch.c 调到的**内核 API** | 46 个，**3 个 7.3 树里没有**（全是我们自己的 LED 辅助函数） |
| DSA 操作表成员 | 13 个，**7.3 的 `dsa_switch_ops` 全部有，签名一致** |
| LAN 服务算子 | 11 个里只有 `control_begin`/`control_end` 两代不同 |

**为什么 DSA 那一项是「全部有」**：我们这份 `switch.c` 用的**已经是新代
DSA API**——`port_bridge_join` 的第二个参数是 `struct dsa_bridge`（按值
传）、带 `tx_fwd_offload` 与 `extack` 出参——而 7.3 树
`include/net/dsa.h` 的 `dsa_switch_ops` 正是这一代。注册入口也一样：两边
都是 `devm_kzalloc()` + `dsa_register_switch()`，树里的
`zx279133-rtl8372n.c` 就是这么注册的。

**22 个名字的来龙去脉**：其中 21 个是**9132 自己的寄存器图**
（`ZX279133_SMAC_*` 那组内部 PHY MAC 块、`ZX279133_9132_OUTERPHY_PPU`、
`ZX279133_XMAC0_SOPC_SEND_ENABLE`），它们在我们 6.18 世代的 `zx279133.h`
里；7.3 的 `zx279133.h` 是**SoC 的**头文件，里面没有这些。⇒ 新开
`zx279133-9132.h` 装这 21 个，值与出处逐字照抄。

剩下 1 个是 `zx279133_lan_service_control_begin`，见下。

### 8之三.1 `control_begin`/`control_end`：我们自己加的服务能力，要一起搬

**它是什么**：一个**跨 XMAC 窗口与 PCS 窗口**的控制事务。4 处用到：lane
监视器读远端、release 时的「恢复 XMAC 镜像 + 恢复 XPCS 镜像」、probe 里的
两段。

**为什么不能用 XMAC 锁代替**：控制序列里**恢复 XPCS 镜像那一步自己会取
XMAC 锁**（`zx279133_switch_xmac_restore()` 内部 `xmac_lock`/`unlock`）。
拿 `xmac_lock` 当事务锁会**自锁死**。

**7.3 树里没有**：7.3 的 `zx279133-lan.h` 的服务算子表里没有这两个；我们
6.18 世代的树里有（`zx279133-netdev.c` 的 `zx279133_lan_control_begin()`
拿 `eth->lan_control_lock`）。

⇒ 随第 3 片一起搬：`kernel-patches/7.3-local/130-sk-lan-control-transaction.patch`
给 7.3 的树加：

* `struct zx279133_eth` 里一把 `lan_control_lock`（紧挨 `xmac_lock`）；
* 服务算子 `control_begin`/`control_end` 两个槽位 + 两个内联包装；
* `zx279133_lan_service_valid()` 改为**要求**这两个算子——子驱动要了一个
  父驱动没有的事务，要在 probe 时就被拒，而不是等到第一次恢复；
* 父驱动侧的实现（加锁 / 解锁）。

**树自己的 RTL8372N 驱动不需要这个事务**（同族芯片，序列自己取 XMAC 锁），
所以**树的行为一点没变**，服务只是长出了两个算子。

**Kconfig 与 Makefile 也在这一个补丁里**，因为服务长出来的原因就是 9132
驱动：`CONFIG_ZTE_ZX279133_SWITCH` 与
`obj-$(CONFIG_ZTE_ZX279133_SWITCH) += zx279133-switch.o`。

> `scripts/configure-sk-d840n.sh` 早就 `pin_y ZTE_ZX279133_SWITCH` 了，
> 在这条线上**一直是空转**——defconfig 会把不认识的符号**静默丢掉**。
> 符号存在之后，这个 pin 才开始生效。

### 8之三.2 落地位置：新的 `kernel-overlays-73/`

**不覆盖 `kernel-overlays/`**：那是 6.18 世代的驱动集，发布线在用，一行都不能动。
7.3 线的自有驱动树另开：

| 文件 | 说明 |
|---|---|
| `kernel-overlays-73/drivers/net/ethernet/zte/zx279133-switch.c` | 9132 驱动本体（159 KB），文件头改成 7.3 的语境 |
| `kernel-overlays-73/drivers/net/ethernet/zte/zx279133-9132.h` | 9132 的寄存器图（21 个宏），私有 |
| `kernel-overlays-73/drivers/net/ethernet/zte/zx279133-lan1-led.h` | LAN1 LED 辅助（自包含，照抄） |

`tools/apply-sk-overlay-73.sh` 改成**拷两份**：`kernel-overlays/`（去掉 6.18
驱动目录与 PHY 目录）**和** `kernel-overlays-73/`；正反两面的断言清单同步
更新——`zx279133-switch.c` 与 `zx279133-lan1-led.h` 从「不得出现」挪到
「必须出现」。

`.gitattributes` 加 `kernel-overlays-73/** text eol=lf`：**同一个理由**——
`core.autocrlf=true` 的 Windows 检出会把驱动源码变成 CRLF，而编译器读到的
文件就不是门禁读到的那个文件。

### 8之三.3 编译前的门禁：`tools/check-sk-switch-api.py`

本机没有 arm64 交叉工具链，编译只能走 CI，**而 Compile 在作业的第 70
分钟**。所以把编译器会说的话先问一遍：

1. 驱动带的三个文件都在树里；
2. 驱动用到的每个 `ZX279133_*`/`zx279133_*` 名字都有定义（驱动自己 / 树的头文件 / 私有头）；
3. 驱动调到的每个**内核函数**在树里存在；
4. **服务契约**：驱动调用的每个 `zx279133_lan_service_*` 包装在树里、
   它包的那个算子在算子表里、**父驱动真的提供了它**——最后这一条是链接
   器七十分钟后才会说的话；
5. 构建接线：Kconfig 符号、Makefile 对象、板级 DTS 节点、`configure` 脚本
   的 pin，四者对得上。

**本机实测**：在 `scratch/test73c`（vanilla + 39 补丁 + 100/110/120 + 板级层
+ 130 + `kernel-overlays-73/`）上**全部通过**。

### 8之三.4 验证

* 补丁 130 在「vanilla + 39 + 100 + 110 + 120」的基准树上**干净 apply**，
  且 apply 后的结果与手工编辑**逐字节一致**（`git diff` 为空）。
* `tools/apply-sk-overlay-73.sh` 在真树上跑通，文件清单断言全过。
* 符号闭合检查：22 → **0**，内核 API 3 → **0**。
* **未做**：本机没有编译器，**这份 `switch.c` 还没有被真正编译过**；
  CI 的 Compile 是第一次编译。

---

### 八之三.5 第 4 片的预先量化：PON / OMCI 栈（74 个函数）

第 4 片的代码还没写，但**面已经量清楚了**，写之前先把账算出来：

| 文件 | 函数 | 字节 | 性质 |
|---|---|---|---|
| `zxpon.c` / `zxpon.h` | 48 | 18078 | **协议核心**（AES-128/CMAC、PLOAM、OMCI 状态机） |
| `zx279133-pon.c` | 23 | 10796 | 内核胶水：netlink、`omci` netdev、carrier、sysfs、LED 触发器 |
| `zx279133-omci.c` | 3 | 3131 | 把 OMCI 帧投进 IDM 的**队列 0**（管理队列） |
| `zx279133-omci-wire.h` | — | 635 | 三个描述符字（长度/控制/GEM），照抄 |

**内核 API 缺口：0。** 三个文件调到的内核函数，7.3 树里全有；`zxpon_state` 与
`zxpon_state_name` 是**我们自己**的 `zxpon.h` 里的（不是内核符号）。

`zx279133-omci.c` 用到的 `struct zx279133_eth` 字段，7.3 树里也全有：
`dev`、`base`、`datapath_lock`、`tx_descs`、`tx_prepared`、`tx_stopping`，
以及 `ZX279133_IDM_BASE`、`ZX279133_IDM_TX_DEPTH`、
`zx279133_idm_tx_done_reg()`、`zx279133_idm_tx_doorbell_reg()`、
`struct zx279133_idm_desc`。**只差 8 个 `omci_*` 字段**（`omci_buffer`、
`omci_dma`、`omci_done`、`omci_producer`、`omci_pending`、
`omci_submitted`、`omci_completed`、`omci_error`）。

**真正的移植工作不在代码，在四个钩子的落点**：

| 钩子 | 6.18 世代的调用点 | 7.3 树的落点 | 难度 |
|---|---|---|---|
| `zx279133_pon_attach(ndev)` | `main.c:1336` | `main.c` 里 `devm_register_netdev()` 之后 | 直接 |
| `zx279133_pon_carrier_kick(eth->ndev)` | `mac.c:730` | 7.3 用 **phylink**，`mac.c` 的 `link_up`/`link_down` | 要找准回调 |
| `zx279133_pon_note_rxsd(light)` | `main.c:824` | **7.3 没有对应物** | 见下 |
| `zx279133_pon_stop()` | `main.c:808`（清理动作） | `main.c` 的 `devm_add_action_or_reset()` | 直接 |

**`rxsd` 是第 4 片唯一的真问题。** 它是**光模块的接收信号检测**，在我们侧
不是寄存器而是一个 **GPIO**：`eth->opsd_gpio` 读 `opsd-gpios = <&gpio2 1
GPIO_ACTIVE_HIGH>`（板级 DTS 第 396 行，**板级层已经在 7.3 树里了**）。
7.3 的 `zx279133-main.c` 里**一个 GPIO 都没有**（实测：`gpio`/`gpiod` 零命中），
所以这个轮询得随第 4 片一起写进去：不写，`zxpon_set_los()` 永远不会被调用，
PON 的 carrier 永远不起来，OMCI 整条路径是死的。

**另外两件要一起做的事**：LOS 灯的 LED 触发器（板级 DTS 用
`linux,default-trigger = "zx279133-los"`，触发器名字由驱动注册），
以及 `npu.ko` 的 Makefile 挂法（6.18 是 `zx279133-y`，7.3 是 `npu-y`，
三个文件都没有自己的 `module_init`，所以进同一个模块即可）。

**用户态那半边已经在 7.3 线上了**：`overlays/package/utils/zxpond/` 与
`overlays/package/luci-app-sk-d840n-pon/` 由工作流的 `cp -a overlays/. openwrt/`
带进去，`zxpond` 用的 `zxpon.c` 与内核侧是**同一份代码**（`#ifdef __KERNEL__`
双栖）。

**⇒ 第 4 片的判决**：**可以搬**，而且大部分是照抄；要新写的是
`opsd` GPIO 的轮询与 LOS 触发器，以及 7.3 树侧的四个钩子落点。

---

## 八之四、第 4 片：PON / OMCI 栈（协议核心照抄 + 树侧四个钩子 + 一次 GPIO 轮询）

八之三.5 把面量清楚了。这一节是把它做掉之后的记录。

### 8之四.1 判决

1. **协议核心照抄。** `zxpon.c`（48 个函数，18078 B）、`zxpon.h`（3778 B）
   **逐字节**搬进 `kernel-overlays-73/`：文件里只有 `memcpy`/`memset`
   和 AES/CMAC/PLOAM/OMCI 的算术，`#ifdef __KERNEL__` 那一半只多包了
   `<linux/string.h>`。
2. **两个内核文件照抄。** `zx279133-pon.c`（23 个函数，10796 B）、
   `zx279133-omci.c`（3 个函数，3131 B）、
   `zx279133-omci-wire.h`（635 B）**逐字节**搬进 7.3 专有驱动树。
3. **树侧只差四个钩子和一次轮询**，写成补丁
   `140-sk-pon-omci-transport.patch`（9129 B）。
4. **「队列 0 是管理队列」这个前提在 7.3 里同样成立**——这是第 4 片唯一
   可能整体翻车的假设，所以先查了它（见 8之四.2）。

### 8之四.2 证据：描述符布局两代相同

OMCI 把帧写进 `eth->tx_descs + eth->omci_producer`，`omci_producer`
是 `0..TX_DEPTH-1`。如果 7.3 的数据面也用 0 号环，这一帧就会和数据帧
抢描述符。实测两边都是：

| | 数据面描述符下标 | 管理队列 |
|---|---|---|
| 6.18 | `tx_descs + (ZX279133_IDM_CPU_TX_FIRST + queue) * ZX279133_IDM_TX_DEPTH + producer`（`zx279133-netdev.c:650`） | 0 号环 |
| 7.3 | `tx_descs + (ZX279133_IDM_CPU_TX_FIRST + queue) * ZX279133_IDM_TX_DEPTH + producer`（`zx279133-netdev.c:389`） | 0 号环 |

`ZX279133_IDM_CPU_TX_FIRST` 两边都是 **1**（6.18 的 `zx279133.h:28`、
7.3 的 `zx279133.h:26`），`ZX279133_IDM_TX_DEPTH` 两边都是 1024。
⇒ 0 号环在两代里都是留给管理队列的，**OMCI 的下标不用改**。

### 8之四.3 证据：内核 API 在 7.3 树里都还在

本机没有 arm64 交叉工具链，编译只能走 CI（Compile 在作业第 70 分钟）。
所以先用**包含图可达性**把编译器会问的问题问掉：对 7.3.4 树做
`#include` 图的 BFS，看每个文件自己能不能看见它调用的每个名字。

| 文件 | 用到的内核名字 | 结论 |
|---|---|---|
| `zxpon.c` | `memcpy`/`memset`/`uint8_t`/`uint16_t`/`uint32_t` | 全部可见（`<linux/string.h>` → `<linux/types.h>`） |
| `zx279133-omci.c` | `readl`/`writel`/`lower_32_bits`/`upper_32_bits`/`cpu_to_le32`/`dmam_alloc_coherent`/`readl_poll_timeout`/`dma_rmb`/`wmb`/`mutex_*` | 全部可见（`readl`/`writel` 走 `<linux/iopoll.h>` → `<linux/io.h>`；`lower_32_bits` 在 `<linux/wordpart.h>`） |
| `zx279133-pon.c` | 43 个（netlink、netdev、LED、sysfs、workqueue…） | 全部可见 |

三个**在 7.3 里变过形状**的地方，单独查过：

- **通用 netlink**：7.3 的 `net/netlink/Kconfig` 里**已经没有**
  `CONFIG_GENERIC_NETLINK` 这个符号了——通用 netlink 变成无条件编译
  （`net/netlink/Makefile` 的 `obj-y := af_netlink.o genetlink.o policy.o`），
  `genl_register_family` 仍然以 `EXPORT_SYMBOL` 导出
  （`net/netlink/genetlink.c:840`）。⇒ **7.3 线不需要任何 netlink 的
  Kconfig 开关**，6.18 侧那句 `CONFIG_GENERIC_NETLINK` 的依赖在 7.3 里
  是多余的。
- **LED 触发器**：`led_trigger_register_simple` 在
  `include/linux/leds.h:479` 被 `#ifdef CONFIG_LEDS_TRIGGERS` 包着，
  而 `LEDS_TRIGGERS` 是 `menuconfig`、默认 n。cnjn 的
  `target/linux/zte/zx279133/config-7.3` 里有 `CONFIG_LEDS_CLASS=y`
  与 `CONFIG_LEDS_TRIGGERS=y`，**但这是第 4 片新引入的依赖**，
  所以门禁里逐条查这两行。
- **`dev->stats`**：`include/linux/netdevice.h:2292` 还在
  （注释写着「not used by modern drivers」），`zxpon_omci_xmit()` 的
  `dev->stats.tx_dropped++` 因此不用改。

### 8之四.4 补丁 140：树侧到底改了什么

`kernel-patches/7.3-local/140-sk-pon-omci-transport.patch`
（sha256 `99a75ffe459bf519f8ddaa3adcbe112d525309b5b798085629d26942fb857b28`），
改 5 个文件、119 行：

| 文件 | 改动 | 为什么 |
|---|---|---|
| `zx279133.h` | 加 8 个 `omci_*` 字段 + `pon_led_work`/`opsd_gpio`/`los_trigger`；加 7 个函数声明 | 两个内核文件要用的字段和入口 |
| `zx279133-main.c` | 加 `zx279133_pon_led_work`（200 ms 轮询 GPIO）、`zx279133_pon_cleanup`、`zx279133_pon_led_cancel`；probe 里取 GPIO、注册 LOS 触发器；`devm_register_netdev()` 之后 `zx279133_pon_attach()` | **7.3 的 main.c 里一个 GPIO 都没有**，不写这轮询，`zxpon_set_los()` 永远不会被调用，PON 的 carrier 永远不起来 |
| `zx279133-mac.c` | `mac_link_up()` 末尾 `zx279133_pon_carrier_kick(eth->ndev)` | carrier 跟着 PON 状态走，链路起来时要重算一次 |
| `zx279133-idm.c` | `zx279133_idm_tx_prepare()` 里先 `zx279133_omci_prepare()` 再 `memset` | 管理队列要和描述符数组的清零对齐，和 6.18 侧同序 |
| `Makefile` | `npu-y += zx279133-pon.o zx279133-omci.o zxpon.o` | 三个文件没有自己的 `module_init`，进同一个模块 |

**`mac_link_down()` 没有加 kick**，和 6.18 侧一致：6.18 的
`zx279133_mac_link_down()` 里也没有。原因是 carrier 跟的是 PON 状态机，
状态机自己每 100 ms 醒一次重算 carrier，link_down 的 kick 是冗余的。

**GPIO 的取法也照抄 6.18**：`devm_gpiod_get_optional(dev, "opsd", GPIOD_IN)`，
`-EPROBE_DEFER` 就返回（等 gpio2 注册），其它错误就当没有这根线。
板级 DTS 第 396 行有 `opsd-gpios = <&gpio2 1 GPIO_ACTIVE_HIGH>`，
7.3 树的 `arch/arm64/include/asm/…`（`zx279133.dtsi` 第 274 行）
`gpio2: gpio@10d10080` 没有 `status` 属性（默认 okay），所以这根线拿得到。

### 8之四.5 编译前的门禁：`tools/check-sk-pon-api.py`

和第 3 片同一个路子，把编译器会问的问题在 CI 编译之前问掉：

1. 五个文件在树里，且**与仓库带的那份逐字节相同**（sha256 比对）。
2. **协议核心闭合**：`zxpon.h` 声明的每个函数在 `zxpon.c` 里有定义，
   反之亦然。（`zxpon.c` 同时被用户态的 `zxpond` 编译，单边缺名字是那边的
   链接错误。）
3. **内核名字可见**：对每个文件算包含闭包，检查它调用的每个内核名字在
   闭包里**有定义**——不是「某个头文件里有」，而是「这个文件看得见」。
4. **内核侧契约**：树的 `zx279133.h` 声明那 7 个函数，
   `struct zx279133_eth` 带那 11 个字段。
5. **四个钩子在位**：`main.c` 的 attach/stop、`mac.c` 的 carrier kick、
   `idm.c` 的 `zx279133_omci_prepare()`。
6. **构建接线**：三个对象在 `npu-y` 里。
7. **新引入的依赖**：板级 DTS 有 `opsd-gpios` 与
   `linux,default-trigger = "zx279133-los"`；7.3 配置片段有
   `CONFIG_LEDS_CLASS=y` 与 `CONFIG_LEDS_TRIGGERS=y`。

### 8之四.6 验证（本机）

- **补丁 140 在「vanilla+39+100+110+120+130」基准树上 `git apply --check` 通过**，
  apply 之后的五个文件与手工编辑**逐字节一致**。
- `tools/apply-sk-overlay-73.sh` 在测试树 `scratch/test73c` 上跑通：
  「board layer applied …; the 7.3 driver set is intact」。脚本现在**多断言**
  八个由 7.3 线带进树的文件与仓库那份逐字节相同（`cmp -s`）——
  因为 PON/OMCI 的文件名与 6.18 世代同名，光靠「文件在不在」分不出是哪一代。
- `tools/check-sk-pon-api.py` 全绿（含内核名字可见性、协议核心闭合、
  四个钩子、构建接线、LED 依赖）。
- 本机四条门禁测试全绿：`test_sk_workflows.py` OK、
  `test_sk_kernel.py` 10/10、`test_sk_uboot.py` 21/21、
  `test_sk_offload.py` 10/10（skipped=1）、`tools/prepush.py` 0 problems。
- MANIFEST.json 里四个补丁的 sha256/bytes 全部校验 OK。

### 8之四.7 未做

1. **没有编译。** 这四个文件是 CI 第一次编译它们。
2. **没有实机验证。** bench 被别的会话占着，本轮没做任何实机操作。
   板上有光纤时才能验：LOS 灯随 `opsd` 翻转、`pon_state` 从 O1 往 O5 走、
   `omci_transport` 的 `submitted`/`completed` 在涨。
3. **上行（RX 方向的 PLOAM/OMCI 帧）没有接**——这不是第 4 片的缺口：
   `zxpon_rx_ploam()`/`zxpon_rx_omci()` 在 **6.18 侧同样没有任何调用点**
   （`kernel-overlays/` 全目录零命中）。OMCI 的上行是用户态的 `zxpond`
   走 netlink 的 `OMCI_TX` 命令下去的。第 4 片忠实复制了 6.18 的状态。
4. **PON MAC 窗口仍然没有编程。** `zx279133-pon.c` 的头注释写得很清楚：
   厂商模块只有 `mode_xgpon_nsyn_cfg`、没有 `mode_xgpon_nsyn_cfg_133`，
   所以那 49 字的 `xgpon_nsyn` 镜像不是这块板子该写的，`0x17000000`
   的 PON MAC 窗口一个寄存器都没碰。

---

## 八之五、第 5 片：9132 芯片本身的编程（`150-sk-9132-switch-programming.patch`）

### 8之五.1 判决：第 3 片搬的是 DSA 前端，**芯片的编程没有搬**

**判决：第 5 片的全部内容就是「把 9132 芯片本身的编程搬进 7.3 树」。**

第 3 片把 `zx279133-switch.c`（DSA 前端）和 `zx279133-9132.h`（9132 自己的寄存器图）
搬进了 7.3 线。但**芯片的编程不在 `switch.c` 里，在 6.18 侧的 `zx279133-np.c` 里**：

- 6.18 的 `switch.c` 里 `zx279133_eram_table_set_bits` / `zx279133_eram_table_get_bits`
  **零命中** ⇒ `switch.c` 不写 9132 的 ERAM 表；
- 6.18 的 `zx279133-np.c` 里，9132 的编程是一整段：每端口属性字、CPU 通路、
  flood 组、outport map、隔离矩阵、协议动作表、PHY 使能。

所以 7.3 线上的 9132 会注册出 DSA 口，但**芯片不会被配置**：
`zx279133_spa_indirect_read(eth, ppu, ZX279133_PPU_ATTR_REG, &attr)` 一次都没跑过，
没有镜像、没有 CPU 路径、没有 flood 组指向 CPU 口 ⇒ 四个 GE 口与 2.5G 口不通。

这一片把这段编程**逐字**搬过去（它是在这块板子上实测出来的），外加两处小改动。

### 8之五.2 证据一：两代 `np.c` 的函数面对照

| | 6.18 侧 | 7.3 侧 |
|---|---|---|
| `zx279133-np.c` 行数 | 6448 | 3108 |
| 函数总数 | 114 | 90 |
| 两边同名 | 74 | 74 |
| 只在 6.18 侧 | **40** | — |
| 只在 7.3 侧 | — | 16 |

只在 6.18 侧的 40 个函数里：

- **26 个**是本片要搬的 9132 编程（22 个 `zx279133_9132_*` + 4 个 `zx279133_eram_*`）；
- 4 个是 PPU 的 ME 助手（`zx279133_ppu_me_word` 等）——**本片不搬**，被搬的代码不用它们；
- 其余是 6.18 世代的其他增量。

只在 7.3 侧的 16 个里有 `zx279133_native_eram_io` / `zx279133_native_spa_io`
等——它们是 7.3 树自己的 debugfs 端点，替代 6.18 的 9132 控制台，
**所以 6.18 的 `zx279133_9132_show`/`_write`/`_next_token`/`_xbasex_word`/
`_off_aligned`/`_open`/`zx279133_9132_debugfs_init` 这一整套控制台不搬**。

### 8之五.3 证据二：被搬的代码在 7.3 树里的依赖

对 10 个入口函数做**调用闭包**（`scratch/closure9132.py`）：

- 闭包共 **32 个函数**：26 个要搬 + 6 个 7.3 树里已经有了
  （`zx279133_spa_indirect_read/write`、`zx279133_smmu0_read/write`、
  `zx279133_mf_eram_attr_set`、`zx279133_mf_eram_entry_write`）；
- 这 6 个助手的**签名在两代完全相同**（逐字比对，见 `scratch/check_port_dups.py`），
  所以搬过来的代码一个字都不用改；
- 用到的 `struct zx279133_eth` 字段只有两个：`dev`、`np_ind_lock`——**两个都在**；
- 用到的内核 API 只有 7 个：`mdiobus_read`、`mdiobus_write`、`of_mdio_find_bus`、
  `of_parse_phandle`、`of_node_put`，加两个宏 `MII_BMSR`、`BMSR_LSTATUS`
  （后两个在 `include/uapi/linux/mii.h`，7.3 树里都在）；
- 用到的 `ZX279133_*` 常量 35 个：3 个（`ZX279133_SMMU0_CMD_READ/WRITE`、
  `ZX279133_SMMU0_WORDS`）7.3 树里已有，4 个是 9132 自己的 PPU 号（放进我们自己的
  `zx279133-9132.h`），**其余 28 个由补丁带进 `np.c`**。

### 8之五.4 补丁 150：逐文件说明

`kernel-patches/7.3-local/150-sk-9132-switch-programming.patch`
（71010 字节，sha256 `d7d6cc44d1f265bfaae9d842e17c7c1af98a2e5f53c13eb12634ac2fee8a206e`），
改 2 个文件、1686 行新增 / 65 行删除：

| 文件 | 改了什么 |
|---|---|
| `drivers/net/ethernet/zte/zx279133-np.c` | 加 4 个头（`<linux/mdio.h>`、`<linux/mii.h>`、`<linux/of.h>`、`<linux/of_mdio.h>`）与 `#include "zx279133-9132.h"`；加 ERAM 表描述符与 4 个位操作助手；加 22 个 9132 编程函数与 6 张数据表；`zx279133_se_sdt_create()` 里记录 ERAM 描述符（4 行）；`zx279133_np_prepare()` 里按 6.18 的顺序调用这 10 步 |
| `drivers/net/ethernet/zte/zx279133-idm.c` | `zx279133_idm_rx_lan_vport()` 加 PPU→transport VID 映射（7 行）；`zx279133_idm_tx_drain()` 回收 OMCI 管理队列并等 `omci_pending`（2 行） |

**调用顺序必须与 6.18 一致**，其中一处容易放错：
「端口字基线读取」（`zx279133_9132_port_words_read()`）在 6.18 里是
**`zx279133_spa_prepare()` 之后、`zx279133_nppu_early_prepare()` 之前**——
它的用途是读出 bootloader 留下的每端口字，放晚了读到的就是驱动自己写的值。
门禁里有一条专门盯这个位置（反向验证已实测：挪到 SMCT 之后门禁转红）。

搬运是**逐字**的：从 6.18 的 `np.c` 里按 44 个区块（1419 行，含注释）原样取出，
按依赖顺序拼进 7.3 的 `np.c`。脚本 `scratch/port_9132.py` 会在写盘前拒绝两件事：

1. **重定义**——补丁带来的 30 个 `ZX279133_*` 宏若与树里已有的撞名就中止；
2. **前向引用**——拼装后若有函数先于它调用的函数出现就中止。

### 8之五.5 ERAM 表描述符：为什么必须一起搬

9132 的表在 ERAM 里，而 **ERAM 表的基址不是常数**：`zx279133_se_sdt_create()` 用一个
顺序分配器把块发出去，从块 12288 开始，每张表拿走剩下的块、下一张接在后面。
硬件按 `(base << 7) + (width * index & ~0x7f)` 找表项——

- 基址写错 ⇒ 硬件根本找不到那张表；
- 表项宽度写错 ⇒ 硬件在错的位置读表项。

6.18 的做法是：**在分配的那一刻把描述符记下来**（`zx279133_eram[sdt_no].base` /
`.bit_len`），之后按表 id 查。7.3 的 `se_sdt_create()` 与 6.18 的**逐字相同**，
只差那 4 行记录——所以补丁 150 在同一个位置加上它们，
而不是在 `np.c` 里另抄一份分配逻辑。

### 8之五.6 两个小改动

**PPU → transport VID。** IDM 把帧的源端口放在描述符的 word 1 里，而在 9132 这条路上
那个数是**芯片自己的 PPU 号**（四个 UNI 口是 PPU 1..4，2.5G 口的 lane 是 PPU 5），
不是 transport VID。7.3 树的 `zx279133_idm_rx_lan_vport()` 只认 transport VID，
于是从网口进来的帧**打不上任何标签**。补丁把 PPU 1..5 映射到 transport VID 59..63
（`ZX279133_LAN_TRANSPORT_VID_MIN` + PPU − `ZX279133_9132_UNI_PPU_FIRST`）。

**OMCI 管理队列。** OMCI 走的是 IDM TX 环的 **0 号队列**（管理队列，在数据面队列之下）。
7.3 的 `zx279133_idm_tx_drain()` 只回收数据面队列，管理队列手里捏着描述符不放。
补丁加了 `zx279133_omci_reclaim(eth)`，并把 `!eth->omci_pending` 一起等上。

### 8之五.7 编译前的门禁：`tools/check-sk-9132-api.py`

新增一扇门（与第 3、4 片同构），在 CI 里紧跟在 PON/OMCI 那扇之后跑，核对：

- 编程的 26 个函数在树里；ERAM 表描述符与数组在；
- **`se_sdt_create()` 里记录了基址与宽度**；
- 块里用到的每个 `ZX279133_`/`zx279133_` 名字都有定义；
- 私有头里有 4 个 PPU 号，且 `np.c` 与 `idm.c` 都 include 了它；
- 树自己的 6 个助手在；
- 7 个内核 API 在树里；
- **`np.c` 与 `idm.c` 里没有名字被定义两次**（前置声明不算，`u64_stats_inc()`
  这种宏调用不算）；
- **`np_prepare()` 按 6.18 的顺序调用这 10 步**；
- 板级 DTS 里有 `zx279133,uni-phy-bus`（PHY 使能要靠它找总线）；
- 树里的私有头与仓库携带的那份**逐字节相同**。

**双向验证**：对着打过补丁的树全绿；对着未打补丁的 vanilla 树**必须红**（已实测）。

### 8之五.8 验证（本机）

| 检查 | 结果 |
|---|---|
| 补丁 150 在 +140 基准树上 `git apply --check` | 通过 |
| 整链（+120 → 130 → 140 → 150）apply | 通过，2 个文件 1685+/65− |
| `check-sk-9132-api.py`（+150 + overlay 树） | 全绿 |
| `check-sk-9132-api.py`（vanilla 树） | 按预期红 |
| `check-sk-switch-api.py` / `check-sk-pon-api.py` | 未受影响，仍全绿 |
| `tests/test_sk_workflows.py` | OK（新步骤与新路径过滤都在） |
| `tests/test_sk_kernel.py` / `test_sk_uboot.py` | 10/10、21/21 |
| `tools/prepush.py` | 0 problems |
| 补丁文件换行 | 纯 LF（CR = 0） |

### 8之五.9 未做

1. **没有编译。** 这是 CI 第一次编译这段代码。
2. **没有实机验证。** bench 被别的会话占着。
   本片上板后应看到：四个 DSA 用户口的 link 状态正确、
   conduit（XMAC0）的 RX 计数在涨、2.5G 口能收发。
3. **TX 故障隔离与恢复机制不整体移植。** 它建立在 6.18 世代的
   `zx279133_datapath_attach`/`datapath_failed` 模型上，**7.3 树没有这个模型**
   （7.3 用 LAN service 的 `datapath_get`/`set_ready`/`put`），
   而且 7.3 的 `tx_reclaim_queue_locked()` 对非法完成增量是**钳位**
   （`completed = tx->pending`）而不是隔离。要搬就得连模型一起搬，是独立的一片。
4. **RGMII（`zx279133-rgmii.c`）不搬。** 它只是 `0x15900000` 窗口的 debugfs
   读写端点，而该块在本机全零、厂商 Linux 从不 bring up、2.5G 口其实是 9132 的铜口
   ⇒ 它的价值只是「可复现地看到这块是死的」。这是一个**取舍**，不是缺口。
5. **9132 的 debugfs 控制台不搬**（见 8之五.2），7.3 树有自己的 `native_*_io` 端点。

### 八之六、对拍的仪器：`tools/trialsurvey.sh`

对拍要回答的是五个问题，**每一个都有确定的读数位置**，这个脚本就是去读它们：

| 问题 | 读数位置 | 出处 |
|---|---|---|
| conduit 的 RX 计数在涨？ | `/sys/class/net/lan-cpu0/statistics/rx_{packets,bytes}` | `zx279133-lan-conduit.c:176` 把网设备名**播种**成 `"lan-cpu%d"`，内核在 `register_netdevice()` 时把它解析成 `lan-cpu0`（见 7.27） |
| 帧是被交出去了还是被扔了？ | `/sys/class/net/*/statistics/rx_dropped` | `zx279133-idm.c:948-953`：DSA 路径没起来时丢的帧计在 conduit 的 `rx_dropped` 上。**`rx_packets` 涨而 `rx_dropped` 也涨**，与**`rx_packets` 涨而 `rx_dropped` 不动**，是两回事（见八之九.2） |
| 四个 DSA 用户口 link 状态正确？ | `/sys/class/net/lan{0,1,2,3,4}/{operstate,carrier,speed}` | 板级 DTS 的 `switch` 节点声明 `port@4..port@8`，label 是 `lan0`/`lan2`/`lan3`/`lan4`/`lan1`；**2.5G 插孔是 port@8 ⇒ `lan1`**（见八之九.2） |
| LOS 灯随 `opsd` 翻转？ | `/sys/class/leds/*/{brightness,trigger}` + `/sys/kernel/debug/gpio` 里 **gpio 65** 的电平 | `zx279133-main.c:297-299` 用 `gpiod_get_value_cansleep()` 读 opsd，再用 `led_trigger_event()` 点灯；opsd 在 DTS 里是 `gpio2 pin 1`（`GPIO_ACTIVE_HIGH`），平铺编号 65 |
| `pon_state` 从 O1 往 O5 走？ | `/sys/class/net/<wan>/device/pon_state` | `zx279133-pon.c:351` 在 `ndev->dev` 上建这个属性 |
| `omci_transport` 的 submitted/completed 在涨？ | `/sys/class/net/<wan>/device/omci_transport` | 同上，`zx279133-pon.c:353` |

脚本还做三件对拍必须的事：

1. **取两次样本**（默认隔 10 秒），这样「没动」是一个结论而不是一次读数。
2. **把 opsd 的电平与灯的亮度并排打出来，而且打两次**——
   「灯随光信号翻」这句话只有在两者同时可见、且前后两次都可见时才成立；
   只读一次，分不清「灯在工作」和「板上根本没有光信号」。
3. **PON 属性带上它属于哪个 netdev**（`eth0/pon_state: O5` 而不是 `device: O5`），
   因为 WAN 口和 conduit 口都在同一个 sysfs 根下。

**本机已演练**（`scratch/trialsurvey_check.py`）：合成一棵 `/sys/class/net` 树，
脚本要在完整树上把五个问题都答出来，且在四种坏树上**不能死**——
① 没有 PON 属性的树（驱动没挂上时的样子）；
② 某个网设备没有 `statistics` 目录；
③ 灯的 trigger 被用户换成了别的（脚本要如实说「驱动没在管这盏灯」）；
④ gpio dump 里没有 opsd 那一行（脚本要说的是「不在 dump 里」，不是「低」）。
并且演练**反过来也要成立**：把脚本改坏四处
（去掉 opsd 读数、去掉第二次采样、去掉 trigger 判据、去掉 netdev 名），
检查必须转红。
脚本的路径是变量（`SK_CLASS_NET`/`SK_DEBUGFS`/`SK_LEDS`/`SK_LOS_LED`/`SK_OPSD_GPIO`），
就是为了能在本机用合成目录跑。

#### 八之六.1 写这个仪器时抓到的三个坑（都是「测的是自己的假设」）

**坑一：opsd 的读数位置写错了，而旧的演练把它掩盖了。**
第一版的脚本是这样找 opsd 的：

```sh
sed -n 's/^\([0-9]*\).*opsd.*/    gpio \1: opsd/p' "$DEBUG/gpio"
grep -o 'opsd[^|]*' "$DEBUG/gpio"
```

**内核的 debugfs gpio dump 里没有 "opsd" 这个字样**——
它写的是 gpio chip 和 pin：

```
GPIOs 64-95 (gpiochip2):
 [65] 65-66 (gpio 65)  1c280000.digital  in  high  IRQ ACTIVE HIGH
```

consumer 的名字（`opsd-gpios`）只存在于设备树里，dump 里不会有。
⇒ 这两行**永远匹配不到**，是死代码。

为什么没被发现？因为**旧的演练喂给脚本的 gpio 文件里写着 "opsd"**：

```
gpio-0  (opsd) in hi ACTIVE HIGH
```

演练和脚本互相印证了一个谁也没去核实的假设。
现在演练改成喂内核真正的形状，断言改成「opsd (gpio 65): high」，
并加了一条反向验证：把脚本里的 opsd 读数删掉，检查必须转红。

**坑二：灯只采了一次，「翻转」这个动作根本没被观测到。**
原来第 3 节打印灯和电平，第 6 节（第二次采样）只打印计数器。
⇒ 「灯随 opsd 翻转」这个命题一次都没被验证过。
现在第二次采样也打印灯与电平，演练断言 `opsd` 至少出现两次。

**坑三：PON 属性打的是 `device: O5`，看不出是哪个口。**
`$CLASS/*/device/pon_state` 的 `dirname` 是 `.../eth0/device`，
`basename` 出来是 `device`。现在取两级，打成 `eth0/pon_state: O5`。

#### 八之六.2 对拍时必须一起看的一对读数：LOS 与 `pon_state`

`zx279133_pon_note_rxsd()`（`zx279133-pon.c:321-329`）把**同一个** opsd 读数
写进 PON 状态机：

```c
void zx279133_pon_note_rxsd(int light)
{
	...
	zxpon_set_los(&zx_state, light ? 0 : 1);
	...
	zxpon_apply_carrier();
}
```

而 `zx279133-main.c:299` 用它点灯：`rxsd ? LED_OFF : LED_FULL`
⇒ **有光时 LOS 灯灭，无光时 LOS 灯亮**（LOS = Loss of Signal，红灯亮就是没光）。

⇒ 所以：**板上没有光信号时，`pon_state` 本来就不会往 O5 走。**
判读必须是成对的：

| opsd | LOS 灯 | `pon_state` 不动 | 结论 |
|---|---|---|---|
| low（无光） | 亮 | 是 | **正常**：无光时状态机不会前进，不是驱动故障 |
| high（有光） | 灭 | 是 | 驱动/PON 栈的问题 |
| high（有光） | 灭 | 否，O1→O5 在走 | 通过 |

只看 `pon_state` 不动就判「移植失败」，会把「这根光纤没插」误判成故障。

**未做**：没有在真机上跑过这个脚本（本机没有板子，也没有 7.3 镜像）；
演练用的是合成树，**不能替代实机读数**。

### 八之七、按能力对账，而不是按函数名

**判决：171 这个数字是对的，但「按函数名核对」这条办法是错的，
所以本文此前的「剩下的是 RGMII 与 TX 恢复」这句话也是错的。**

**为什么按名字对不行。** 第 1~2 片的补丁改的是 7.3 树里**已经存在**的函数：
`100` 改 `zx279133_idm_tx_configure()` 的寄存器值，
`110` 改 `zx279133_idm_rx_page_slot()` 用的帧落点，
`120` 改 `zx279133_idm_rx_flush_refill()`。
6.18 世代那三个对应的函数叫 `zx279133_idm_bp_ring_cfg()`、
`zx279133_idm_rx_find_header()`、`zx279133_idm_find_broadcast()`——
**一个名字都不在 7.3 树里**。
⇒ 按名字核对，这三个会被判成「没搬」；实际上第 1~2 片搬了，
而且 7.3 树**根本不需要**「找头」这个函数，因为帧落点是固定的 0x80。

**那 7.3 树自己怎么做的。** 7.3 树不是缺这些能力，是用另一套机制做：

| 6.18 世代的函数 | 7.3 树里的对应物 |
|---|---|
| `zx279133_bmu_threshold_fixup()` | `zx279133_bmu_prepare()` / `zx279133_bmu_restore()`（`zx279133-tm.c`） |
| `zx279133_red_gate_clear()` / `_restore()` | `zx279133_red_prepare()` / `zx279133_red_restore()`（`zx279133-tm.c`） |
| `zx279133_red_vendor_fixup()` | `zx279133_red_np1_prepare()` / `_restore()`（`zx279133-tm.c`） |
| `zx279133_tm_vendor_fixup()` | `zx279133_tm_prepare()` / `zx279133_tm_restore()`（`zx279133-tm.c`） |
| `zx279133_sopc_duplex_mask()` / `_ready()` / `_send_enable()` | `zx279133_sopc_prepare()` / `zx279133_sopc_restore()`（`zx279133-tm.c`） |
| `zx279133_xmac_base()` / `zx279133_xmac_reset_mask()` | `zx279133_xmac_reset()`（`zx279133-mac.c`） |
| `zx279133_ppu_get_me_val()` / `zx279133_ppu_me_word()` | `zx279133_ppu_me_stats_read()` / `zx279133_ppu_me_diagnostics_read()`（`zx279133-np.c`） |
| `zx279133_datapath_attach()` | `zx279133_lan_datapath_get()` / `zx279133_lan_datapath_put()`（`zx279133-netdev.c`） |
| `zx279133_lan_mmio_set()` | `zx279133_lan_nppt_write()` / `_read()`（第 3 片的 LAN 控制事务） |
| `zx279133_lan_strip_transport_vlan()` | 第 5 片的 PPU → transport VID 映射（`zx279133_idm_rx_lan_vport()`） |
| `zx279133_wan_phylink_start()` / `_stop()` | `zx279133-netdev.c` 里直接调 `phylink_start()` / `phylink_stop()` |
| `zx279133_idm_rx_has_ethertype()` | `eth_type_trans()`（`zx279133-idm.c`） |
| `zx279133_idm_xdp_pass_valid()` | `zx279133-netdev.c` 的 XDP 路径里的 `xdp_buff_has_frags()` 与长度检查 |

**账本。** 新工具 `tools/func-port-coverage.py` 把这件事变成可复跑的检查：
它拿 6.18 世代的驱动目录与 7.3 树对照，对**只有 6.18 有的每一个函数**，
要求它落进三个桶之一：

1. **同名在 7.3 树里** —— 125 个；
2. **账本里写着它搬到了哪里**（`CARRIED` 表，27 个）——
   **表里写的目标函数必须真的在 7.3 树里**，否则工具报「账本指着不存在的东西」；
3. **有处置记录**（`DISPOSITIONS` 表，19 个）——
   每条处置都必须说清 7.3 树用的是什么，不是「我们没搬」。

三个桶都不在 ⇒ **缺口**，工具非零退出。

**反向验证**（`scratch/reverse_check_port_coverage.py`，本机已跑通）：
删掉一条账本条目、删掉一条处置、把账本里的目标函数改成树里没有的名字，
工具**都必须转红**，且必须点名它丢了哪一条。

**为什么相似度不能当判据。** 工具本来还有一个「函数体相似度 ≥ 60% 就算搬过」的兜底，
实测发现它会**掩盖账本的缺失**：`zx279133_bmu_threshold_fixup()` 与某个
不相干的短函数恰好有 60% 的行重合，删掉账本条目后工具照样绿。
⇒ 已删掉这个兜底：**只有账本算数**，相似度只作为「最接近的是谁」的线索打印。

**未做**：
- 账本目前是**人工核实**的结论（本机逐个读过 6.18 侧的实现、在 7.3 树里找到出处）；
  没有进 CI（它需要 vanilla 7.3 树，而 CI 只准备打过补丁的树）。
- 27 个「换名搬过去」的对应物，是**能力层面**的对应，不是逐行等价；
  它们能不能在板子上真的工作，要等实机对拍。
- 「125 个同名对上」只说明名字在，**不说明实现没变**；
  同名的那些函数里有多少是逐字相同、多少是重写，需要另用
  `tools/func-body-diff.py` 逐个文件比（IDM 那一个文件已经比过，见第九节）。

### 八之八、第 6 片（实机对拍）：机械清单

**判决**：前五片落成补丁，第 6 片是**板子上的对拍**。
这一节是**操作顺序**：每一步该看什么、看到什么算过、不过怎么办。

#### 八之八.1 前置：镜像与三件套

CI 的「Validate and export the partitions」步会校验 sysupgrade 镜像
（fwtool 元数据、CRC、板型兼容、各成员大小、JFFS2 魔数、
以及 uImage 解压后**正好等于**编出来的那个 Image）并**原样导出**三个分区文件：

| 文件 | 去处 | 多大 |
|---|---|---|
| `flash-uImage` | 内核分区 | 8 MiB |
| `flash-dtb.bin` | DTB 分区 | 约 13 KiB |
| `flash-rootfs.jffs2` | rootfs 分区 | 约 4.7 MiB |

`staging/trial/flash-set.tgz` 是这三件套（6.18 线那一版的打包）。

**怎么拿到手**：CI 把它们作为 artifact 上传（`sk-d840n-73-images-flash`）。
artifact 的**列表**是公开的，**内容要登录态**
⇒ 用带登录态的浏览器下载那个 zip，解开即得六个文件：
三个分区文件、sysupgrade 镜像、校验报告，以及**一份 `sha256sums` 清单**。

**先校验再刷**：解开后先 `sha256sum -c sha256sums`；
清单对不上就不要碰板子。
这份清单是 CI 那边跟文件一起生成的，**而且它自己也在 CI 里被校验过一遍**
（导出步末尾的 `sha256sum -c`）——
手取下来的东西必须能自证没坏。

**不要拿 `staging/trial/` 里现成的那三件**：那是 6.18 旧布局
（`current240`），板子现在是 `native241`（见 7.23），
拿旧件去刷等于把 jffs2 写到新内核不会去看的地方。

#### 八之八.2 刷机（两条路）

| 路径 | 命令 | 什么时候用 |
|---|---|---|
| 只换内核与 DTB | `sh tools/flash-round.sh write` | 板子上装着的 jffs2 已经是 7.3 线的那套 |
| 全量 | `sh tools/flash-round.sh write-root` | 第一次上 7.3 线，rootfs 也要换 |
| 从正在运行的系统直接写 | `sh tools/flash-direct.sh write` | 已经证明能进 userspace，想少动一次 flash |

设备是 `root@192.168.9.1`，密钥 `~/.ssh/id_onu`。
`flash-round.sh` 的**第一次必须先跑 `dry`**：它只勘察 flash 并把载荷挂在
`http://192.168.9.1/` 上，**一个字节都不写**。

#### 八之八.3 第一次启动：三个观察点

1. **串口**：内核 banner 的版本必须是 `7.3.4`（门禁已保证产物是它，
   但 banner 是板子上真正跑的那一个）；
2. **LOS 灯**：**有光 ⇒ 灯灭，无光 ⇒ 灯亮**（驱动是 `rxsd ? LED_OFF : LED_FULL`）；
3. **网络口**：四个 DSA 用户口都要出现（`lan0 lan1 lan2 lan3 lan4`）。

#### 八之八.4 对拍仪器

```sh
sh tools/trialsurvey.sh 10
```

它一次回答五个问题（见工具头部），每个计数**采两次**，
灯与 `opsd` **也采两次**。判读表：

| 观察 | 期望 | 不符时先查 |
|---|---|---|
| `lan-cpu0` 的 `rx_packets` 在涨 | 涨 | BP 池投递（补丁 120）、帧落点 0x80（补丁 110） |
| `lan0..lan4` 都在、`carrier` 正确 | 都在 | 9132 驱动没注册（`dmesg \| grep 9132`） |
| LOS 灯与 `opsd` **成对**读 | 有光⇒灭，无光⇒亮 | 只读灯不说事：必须同时有 `opsd` 的读数 |
| `pon_state` 从 O1 往 O5 走 | 有光时走 | **无光时本就不该走** ⇒ 必须与 LOS 成对判读 |
| `omci_transport` 的 `submitted`/`completed` 在涨 | 涨 | OMCI 队列（补丁 140） |

**注意第三条与第四条是耦合的**：`zx279133_pon_note_rxsd()` 用的是**同一个**读数，
所以「无光 ⇒ pon_state 不动」**不是故障**。

#### 八之八.5 三片的专项判据

| 片 | 判据 | 怎么读 |
|---|---|---|
| 第 3 片（9132 交换表驱动） | 交换表被写成厂商那张表 | bench 的 ERAM 读（`eram_read`）与文档 30/31/32 的表对 |
| 第 4 片（PON/OMCI 栈） | `omci_transport` 两个计数在涨 | `trialsurvey.sh` 第 5 节 |
| 第 5 片（9132 芯片编程） | 帧按 PPU→VID 映射落到 lan-cpu0 | `lan-cpu0` 的 RX 计数在涨，**且**收到的帧的 VLAN 与补丁 150 里那张 PPU→VID 映射一致 |

#### 八之八.6 不符时的取证顺序

1. `dmesg | grep -iE 'zx279133|9132|lan conduit|nppu'`（仪器第 7 节已经带这一条）；
2. `/proc/net/dev` 与 `ethtool -S lan-cpu0`（看有没有 RX 欠载）；
3. `cat /sys/kernel/debug/gpio`（`opsd` 的原始读数）；
4. bench 的寄存器读（`nppt_reg` / `soc_read` / `eram_read`）。

**不要**因为对拍不过就去整体移植 TX 故障隔离/恢复（**判为不整体移植**，见八之五.9），
也不要顺手去动 RGMII（**取舍不是缺口**）。

#### 八之八.7 现场那套工装：清单里的仪器**不在清单所在的目录里**

**判决**：照着这份清单在现场做，**第一条命令就会失败**。

`sh tools/trialsurvey.sh 10` 是在**工作区根目录**跑的（现场站的地方），
那里的 `tools/` 是**现场工装区**，不是仓库的 `ci-build-repo/tools/` ——
而 `trialsurvey.sh`（含第 10 片加的 `rx_dropped`）**只在仓库里**，
现场那份**没有** ⇒ 「文件不存在」。

**已定向同步**（不整包覆盖）：仓库那份**逐字节**复制到现场
（sha256 `33fb8a1d…`，`sh -n` 过）。

**顺带体检了整条刷机链**（`scratch/live_flash_tools_check.py`）：
`gatecheck.sh` / `flash-round.sh` / `flash-direct.sh` / `preflight.sh` /
`flashverify.sh` / `flashverify-test.sh` / `lan1roundcheck.sh` /
`gettrial.sh` / `round.sh` / `onewindow.sh` / `mkctlram.py` /
`dtbparts.py` / `uimage-kver.py`
—— **十三个全部在、全部能解析**；
`bench-keep.ps1` 在现场与仓库**逐字节相同**，里面**没有**写死的
内核版本或分区大小。布局方面，`flash-round.sh`、`flash-direct.sh`、
`preflight.sh`、`lan1roundcheck.sh`、`gettrial.sh`
问的都是 **native241 + root=0xf00000,0xf100000**，不是旧的 240 MiB。

**未做**：现场工装区里有 **189 个文件**在仓库里没有对应物
（`e1xxx.sh` 那些一次性实验脚本、`accept*.sh`、`arm*.sh` 等等），
**CI 看不见它们**（不在仓库里 ⇒ 不在 `paths:` 里 ⇒ 没有 lint 覆盖）。
本轮只查了上面这十三个**刷机链上**的脚本能解析，
**没有把它们收进版本控制** —— 收进来的取舍见「下一步」。

**未做**：这一节是**清单**，不是结论——它一行都还没有在**板子**上跑过：
镜像早出来了（run #36/#37，见 7.28），但**板子还没跑过 7.3**
（`staging/ctl/flash.permit` 里没有 7.3 那对字节的凭证，见 7.28）。

---

## 八之八、第 8 片：IDM 的 RX 侧（交付路径的安全修复）

### 8之八.1 判决

**搬 6 项**（每项都有实机或文档证据，不是「看起来更严谨」）：

1. **`!rx_has_ethertype()` 守卫**：L2 重写只对「不带类型字段」的帧做。
   依据：docs/42:164 与 docs/77 的实机验证 —— 帧自带 ethertype 时无条件重建 L2
   会把 IP 头**右移 4 字节**、总长被读成 `0x0800`，IPv4 与 IPv6 全部丢失。
2. **`rx_restore_lan_l2()` 的 ARP 分支加 `skb->len >= 2*ETH_ALEN+4`**：越界读。
3. **交付段加 `pskb_may_pull(skb, ETH_HLEN)`**：越界读。
4. **`zx279133_idm_xdp_pass_valid()` + 两个 XDP_PASS 分支的校验**：
   XDP 程序可能把栈留在缓冲区之外。
5. **`skb->ip_summed = CHECKSUM_NONE`**：7.3 世代用 PPU 的「parsed」标志设
   `CHECKSUM_UNNECESSARY` —— 那把「头被解析过」当成了「校验和正确」。
6. **`__zx279133_idm_rx_post_page()` 补设备侧同步**，同步整个硬件写窗口：
   归还给硬件的缓冲区必须先同步到设备。

**不搬 4 项**：

* RX 调试转储（`rx_dump` / `dump_frame` / `dump_nonzero` / `rx_find_header` /
  `find_broadcast`）—— 诊断用，不属于交付路径；
* `rx_is_jack` 与 `rx_jack_frames` / `rx_jack_dropped` —— **划入第 9 片**：
  它依赖 DSA 拓扑，要实机对拍才有意义；
* `cpu_dp->rcv()` 直调 tagger —— **划入第 9 片**，同上；
* `bp_ring_cfg`。

**一处布局差异，不是缺陷，不能搬**：

| | page pool 的 `.offset` |
|---|---|
| 6.18 世代（ours） | `PAYLOAD_OFFSET` |
| 7.3 世代（theirs） | `PAYLOAD_OFFSET + rx_frame_offset` |

两边的 **CPU 侧同步各自自洽**（逐条核过 `page_pool_dma_sync_for_cpu` 的偏移）。
把一边搬到另一边会**双重偏移** ⇒ 这不是「哪个更好」的问题，是**两边各自对**。

### 8之八.2 证据：两代函数逐个对照

`tools/func-body-diff.py` 对照 `kernel-overlays/.../zx279133-idm.c`（ours = 6.18 世代）
与 7.3 世代 cnjn 实现：

* **ours 独有 10 个**：`zx279133_idm_bp_ring_cfg`、`zx279133_idm_dump_frame`、
  `zx279133_idm_dump_nonzero`、`zx279133_idm_find_broadcast`、
  `zx279133_idm_give_frame_back`、`zx279133_idm_rx_find_header`、
  `zx279133_idm_rx_has_ethertype`、`zx279133_idm_rx_is_jack`、
  `zx279133_idm_rx_sync_xsk_for_cpu`、`zx279133_idm_xdp_pass_valid`；
* **theirs 独有 6 个**：`module_param` / `MODULE_PARM_DESC`
  （`zx279133_idm_rx_frame_offset` 可调）、`zx279133_idm_rx_count`、
  `zx279133_idm_rx_parsed`、`zx279133_idm_rx_prepare_dsa`、
  `zx279133_idm_rx_sync_xsk_for_device`；
* **REWRITE 4 个**：`rx_flush_refill`(27/32)、`rx_preserve_dsa_vlan`(21/21)、
  `rx_sync_for_device`(15/9)、`tx_configure`(129/68，第 1 片已解决)；
* **similar 11 个**（RX 侧 7 个）；
* 主体函数 `zx279133_idm_rx_process_queue`：ours 653 行 vs theirs 424 行。

### 8之八.3 补丁 170 与它的两道门

`kernel-patches/7.3-local/170-sk-rx-delivery-safety.patch`：改
`drivers/net/ethernet/zte/zx279133-idm.c` 一个文件，**106 插入 / 27 删除**，
12173 字节，sha256 `58772d4cd557c7d9697e20f4a38d2f3580f52b79156193cd1784795b0ec0a35e`。

* `tools/check-sk-rx-delivery.py`：**39 条**，全部**按位置**判，参照点都是**执行点**：
  `rx_restore_lan_l2(` 调用点往上最近的 `if (` 条件里必须有
  `zx279133_idm_rx_has_ethertype`；`pskb_may_pull` 必须早于它的「首次出现」与
  「rewrite 条件里的使用点」两处；XDP 校验必须早于同一 `case` 块里的 `data = xdp`；
  设备侧同步必须早于 `WRITE_ONCE(eth->rx_normal_bp[`，且全文件**恰好一处**。
* `tests/test_sk_rx_delivery.py`：**24 项**负控，突变跑在**真树副本**上。
* `scratch/reverse_check_rx_delivery.py`：**17 处**（14 处削弱门禁判据 + 3 处破坏工作流），
  全部**转红在指定的那条检查上**。

**两个踩过的坑（写在这里，别人会踩）**：

1. **补丁必须相对「上一个补丁已应用」的基线生成。** 170 的第一版把 160 的改动
   也裹了进去（`git diff` 取的是旧基线），CI 路径 apply 直接失败：
   `error: patch failed: drivers/net/ethernet/zte/zx279133-idm.c:1281`。
   修法是把本机 git 基线推进到「160 已应用」再生成。
2. **C 解析器必须区分「函数定义」与「调用点」。** `function_body()` 原来把
   「调用点后面跟着 `{`」（`if (unlikely(!zx279133_idm_xdp_pass_valid(xdp, true))) {`）
   当成了函数定义。判据是**名字前面有没有类型词**。同一个修复顺带应用到了
   第 7 片的 `tools/check-sk-tx-isolation.py`。

### 8之八.4 未做

1. **没有实机证据**：ethertype 守卫与三处越界读在 6.18 世代上有实机记录，
   但**在 7.3 树上还没有**。板子回来之前，第 8 片只有「树里的形状对」这一层证据。
2. **jack 计数与 `cpu_dp->rcv()` 直调 tagger 留给第 9 片** —— 它们要 DSA 拓扑与实机。
3. `zx279133_idm_rx_parsed()` 与 `CHECKSUM_UNNECESSARY` 被**删掉**了：
   那个字段在 7.3 树里恒为 0（idm.c 从不给它加数），厂商 `npu.ko.rodata.txt` 里
   也有这个名字 ⇒ 保留字段名是对的，但不再加数。

---

## 八之九、第 9 片：jack 帧的计数，与 CPU 侧 tagger 的交接方式

**判决**：第 8 片留下的三件事，查清之后是**一件不用搬、两件不能搬**：

| ours（6.18 世代） | 7.3 树 | 判决 |
|---|---|---|
| `zx279133_idm_rx_is_jack()`（`zx279133-idm.c:410`） | 判据用的 `ZX279133_9132_OUTERPHY_PPU`（= 5）在 7.3 板层 `zx279133-9132.h:131` **已有**，且 `zx279133-idm.c:24` 已经 include 了那个头 | 判据可用，但**用不上**（见下） |
| `rx_jack_frames`（`:1212`）/ `rx_jack_dropped`（`:1216`） | 树里没有这两个计数器，**也没有能读它们的地方** | **不搬** |
| `cpu_dp->rcv(skb, eth->lan_ndev)`（`:1290`） | 树里**没有 `cpu_dp`**，而且**也不需要** | **不搬** |

**⇒ 第 9 片没有补丁可写。** 这不是一个还没查清的待办，是查清之后的**否定结论**。

### 8之九.1 为什么 `cpu_dp->rcv()` 直调不能搬

**7.3 的 RX 交付路径**（`zx279133-idm.c` 的 `deliver_skb`）：

```
955:  if (lan_dsa_active && lan_source && eth->lan_ndev) {
956:          rx_ndev = eth->lan_ndev;
957:          zx279133_idm_rx_prepare_dsa(skb, word1);
958:  }
...
971:  skb->protocol = eth_type_trans(skb, rx_ndev);
972:  napi_gro_receive(napi, skb);
```

⇒ 帧被交给 **conduit netdev**（`eth->lan_ndev`，`zx279133-netdev.c:2081` 赋值），
再由 **DSA 核心**在 conduit 的接收路径上调用 tagger。
7.3 树里带的那个 tagger 是 `net/dsa/tag_zx279133_rtl8372n.c`：

```
33:  static struct sk_buff *
34:  zx279133_rtl8372n_rcv(struct sk_buff *skb, struct net_device *netdev)
...
65:          skb->dev = dsa_conduit_find_user(netdev, 0, port);
```

它读 transport VID、把它映射到 DSA 用户口。

**⇒ 7.3 的等价路径已经存在，而且就是标准 DSA 模型**：
IDM → conduit netdev → DSA 核心 → tagger → 用户口。
ours 那一代把 CPU 口做成**另一个 netdev**（`cpu_dp`），
所以驱动必须**亲手**调 `cpu_dp->rcv()`；7.3 不用。

**⇒ 搬过去会做什么**：树里根本没有 `cpu_dp` 这个符号，搬不了；
就算硬搬，也是**在 DSA 核心已经会做一遍的地方再做一遍** ——
帧会被 tagger 处理两次。

**⇒ 这一项不是「还没查清」，是「查清了，不搬」。**

### 8之九.2 jack 判定与计数器：**不搬**

ours 那两个计数器回答的是两个问题：

* `rx_jack_frames`：**有多少帧是从 2.5G 插孔回来的**；
* `rx_jack_dropped`：其中**多少被丢掉了** ——
  ours 的注释说得很清楚（`zx279133.h:1446-1449`）：
  「`rx_jack_dropped` 只在 DSA 路径**不在**的时候动，
  所以它是那个说明 RX 路径到底把帧交出去了还是扔掉了的计数器」。

**在 7.3 上，这两个问题都已经被回答了，而且是回答得更准**：

1. **插孔已经是 DSA 用户口。**
   板层 `kernel-overlays-73/.../zx279133-switch.c:378-387`：
   `ZX279133_SWITCH_DSA_PORT_MIN = 4`，
   「四个带内部 PHY 的插孔，**然后是 2.5G 插孔**；
   2.5G 插孔是 9132 的第五个用户口 —— 厂商叫它 outerphy」，
   「**它和另外四个一样是用户口**」。
   ⇒ 插孔进来的帧由**那个用户口自己的 netdev 计数器**统计
   （设备树给 port@4..port@8 的标号是 lan0、lan2、lan3、lan4、**lan1**，
   插孔是 port@8 ⇒ `lan1`）。
   `tools/trialsurvey.sh` 本来就把**每个网口**的 `rx_packets` 读出来 ⇒ 现成的。

2. **丢帧已经被 7.3 自己计了。**
   `zx279133-idm.c:948-953`：
   ```
   if (lan_source && !lan_dsa_active) {
           if (eth->lan_ndev)
                   zx279133_stats_rx_dropped(eth, eth->lan_ndev);
           dev_kfree_skb_any(skb);
   ```
   ⇒ DSA 路径没起来时丢的帧，**已经**计在 conduit 的丢帧计数器上。
   ours 是**在这之上**又细分出「其中来自插孔的那部分」。

3. **7.3 树里没有能读这两个计数器的地方。**
   ours 读它们的地方是 SPA 调试控制台（`zx279133-np.c:1182`）。
   7.3 的 np.c 只有一处 `seq_printf`（用户表转储，`:986`），
   树里的 `zx279133-native-debug.c` 是 NPPT 芯片诊断控制台，不是 RX 统计。

**⇒ 搬过去会得到两个只有写、没有读的计数器** —— 死代码。
**⇒ 真正的计数器是 netdev 的那两个**，而仪器已经在读它们了。

**顺带**：仪器现在**多读一个 `rx_dropped`**
（每个网口、每个 DSA 用户口、以及第二次采样），
因为「`rx_packets` 在涨而 `rx_dropped` 也在涨」与
「`rx_packets` 在涨而 `rx_dropped` 不动」是两回事：
前者是帧被丢着，后者才是 RX 路径把它们交出去了。

### 8之九.3 未做

1. **没有补丁，也没有门禁** —— 本节的结论是「**没有东西要搬**」，
   所以这一片不需要新的补丁、新的门禁或新的负控。
   要有的话，反而是一个「别再搬 jack 计数器」的负控；等有人再提这件事时再加。
2. **没有实机证据**：上面每一条都是**读树**读出来的，
   不是在跑着的板子上量出来的。
   板子回来之后，插拔 2.5G 网线，看 **`lan1` 的 `rx_packets`** 涨不涨、
   **`lan-cpu0` 的 `rx_dropped`** 动不动 —— 那才是这一步的实机证据。
3. **DSA 拓扑**（四个用户口的 PHY、VID 与 conduit 的对应关系）
   要在实机对拍里一起验；本节只验了**交付路径的形状**。

---

## 八之十、第 10 片：把 TX 隔离的四个计数器接到 `ethtool -S` 上

**判决**：第 7 片（补丁 160）搬进来的隔离模型带四个计数器，
在 7.3 树里**只写不读**。补丁 180 把它们接到驱动**本来就有**的统计表上，
`ethtool -S` 就能读到。
这是第 10 片，也是**软件侧最后一块活**：其余的都要等实机对拍。

### 8之十.1 树里的四个计数器：只写不读

补丁 160 在 `struct zx279133_eth` 里留了四个计数器：

| 计数器 | 声明 | 递增点 | 含义 |
|---|---|---|---|
| `tx_recovery_requests` | `zx279133.h:783` | `zx279133-netdev.c:101` | 第一次请求恢复 |
| `tx_recovery_restarts` | `zx279133.h:784` | `zx279133-netdev.c:1009` | 共享 datapath 被重启 |
| `tx_recovery_failures` | `zx279133.h:785` | `zx279133-netdev.c:1007` | 恢复失败 |
| `tx_invalid_completions` | `zx279133.h:786` | `zx279133-netdev.c:131` | 完成计数器不可信 |

**证据（实测）**：整个 `drivers/net/ethernet/zte/` 里这四个名字**只出现在这 8 行** ——
4 个声明 + 4 个递增点。**没有任何一处读它们。**

⇒ 在板子上，一个**被隔离过的环**和一个**从来没出过问题的环**长得一模一样。
而「这个环到底有没有出过事」正是隔离模型存在的理由。

### 8之十.2 7.3 驱动本来就有统计表

7.3 的驱动已经有 `ethtool` 统计表，它是**三张必须同长同序的位置表**
（行号是打补丁**前**的）：

1. `enum zx279133_ethtool_stat`（`zx279133-netdev.c:1419`，哨兵 `ZX279133_STAT_COUNT` 在 `:1487`）；
2. `static const char zx279133_gstrings_stats[ZX279133_STAT_COUNT][ETH_GSTRING_LEN]`（`:1490`）；
3. `zx279133_get_ethtool_stats()`（`:1597`）里的 `data[...] = ...`。

`ethtool -S` 打印的就是第 2 张表里的名字；
`zx279133_get_sset_count()`（`:1567`）返回
`ZX279133_STAT_COUNT + ZX279133_IDM_CPU_RX_QUEUES + 16`。

⇒ **不用新建任何机制**，把四个计数器加进这三张表就行。

### 8之十.3 补丁 180 做了什么

`180-sk-tx-isolation-stats.patch`：2995 B，**+19 -0**，**只改**
`drivers/net/ethernet/zte/zx279133-netdev.c` 一个文件。三处：

1. **枚举末尾**（在哨兵 `ZX279133_STAT_COUNT` **之前**）追加四项；
2. **名字表末尾**追加四个**同序**标签；
3. 在 `zx279133_get_ethtool_stats()` 的
   `spin_lock_bh(&eth->tx_lock)`（`:1637`）与 `spin_unlock_bh(&eth->tx_lock)`（`:1660`）
   **之间**填四个 `data[...]`。

**为什么加在末尾**：`data[]` 的下标由 `ZX279133_STAT_*` 决定，
插在中间会把**后面所有统计项的下标**挪一位 ——
那些下标被 `memset(data + ZX279133_STAT_RX_NAPI_POLLS, 0, ...)` 这类代码用着。

**为什么必须在锁内读**：这四个计数器是在 `tx_lock` 下递增的
（`zx279133_tx_recovery_request_locked()` 的注释就写着「Caller holds tx_lock.」），
锁外读是一次撕裂读。

### 8之十.4 门禁：33 条，**按位置**判

`tools/check-sk-tx-isolation-stats.py`。

门禁问的是**位置**，不是「某个串在不在」，因为
**编译器抓不到「名字在错位」**：数组长度错了是编译错误，名字放错了不是。
它问的是：

* 枚举有哨兵、名字表与枚举**同长**；
* 四个计数器是枚举的**最后几项**、也是名字表的**最后几个**；
* 四个计数器在两张表里是**同一个索引**（这三条才是「位置」）；
* 每个计数器在 `zx279133_get_ethtool_stats()` 里**有值**；
* 那个赋值**在 TX 锁内**（参照点是**加解锁那一对**，不是某个变量的声明处）；
* 那个赋值**读的是它自己那个计数器**；
* 那个计数器**在树里存在**，并且**真的在被累加**
  （否则发出去的计数器永远是 0）。

门禁把自己读到的两个文件的 sha256 写进 `GITHUB_STEP_SUMMARY`，
和另两道树门禁一个做法：**门禁读的是哪棵树，要能在 job 页上看得到**。

### 8之十.5 负控：11 项，每一项都必须**转红在指定的那条检查上**

`tests/test_sk_tx_isolation_stats.py`。

| 改坏的地方 | 门禁必须转红的检查 |
|---|---|
| 枚举里两个计数器换位 | 「四个计数器是枚举的最后几项」+「同一个索引」 |
| 名字表里两个计数器换位 | 「四个计数器是名字表的最后几个」+「同一个索引」 |
| 名字表多一个名字 | 「枚举与名字表同长」 |
| 枚举多一项 | 「枚举与名字表同长」 |
| 四个赋值挪到解锁之后 | 「在 TX 锁内读」 |
| 读成别人的计数器 | 「读的是它自己那个计数器」 |
| 删掉一处递增 | 「那个计数器真的在被累加」 |
| 删掉头文件里的字段 | 「树里有那个计数器」 |
| 去掉统计函数里的锁 | 「统计函数拿了 TX 锁」 |
| 删掉一个赋值 | 「那个计数器在统计函数里有值」 |

另外还有第一条：**树原样必须通过**（否则门禁就是在测自己）。

### 8之十.6 端到端：180 在 **CI 的布局**里真的落地

`scratch/verify_180_in_checkout.py`（16 项，全绿）。

这一片特意**不在抽象的目录里**验补丁，因为第 7.39 节那个坑就是这么来的：
`git apply` 在仓库子目录里会默默丢掉带 `diff --git` 头的补丁。
所以这个脚本：

1. 在一个 **checkout 里**建树（CI 的形状）跑**真的** prepare；
2. 报告里 **48 个补丁全部 applied**（39 个 vendored + 9 个 local，含 180）；
3. 四个驱动文件的 sha256 与本地树**逐个相同**；
4. 门禁 33/33 绿、负控 11/11 绿；
5. **反向**：把 180 从树里拿掉 ⇒ 门禁转红，
   且**转红在**「四个计数器是枚举的最后几项」与「`tx_invalid_completions` 有值」
   这两条**指定的**检查上。

### 8之十.7 未做

1. **没有实机证据**：本节每一句都是**读树**读出来的。
   板子回来之后，`ethtool -S` 的输出里应当出现这四个名字，
   隔离真发生过的时候 `tx_invalid_completions` / `tx_recovery_requests` 应当**大于 0**。
2. **`ethtool` 包本身没有在本机验过它在 7.3 镜像里**：
   `overlays-73/target/linux/zte/zx279133/target.mk` 的 `DEFAULT_PACKAGES` 里有 `ethtool`
   （与 6.18 线同一份清单），但**镜像里到底有没有那个二进制**，要等实机。
3. **第 7 片留下的缺口本片没有动**：
   `zx279133_xsk_pool_cycle()` 里数据面挂起/恢复失败会把 `datapath_failed` 置上
   （`zx279133_datapath_suspend()` `:864`、`zx279133_datapath_resume()` `:879`），
   而把失败的数据面拉回来的入口只有 **WAN/LAN 的 open 路径**
   （`:303`、`:2258`），且**只在已经有第二个使用者**时才会走；
   恢复工作自己遇到 `datapath_failed` 是**直接放弃**的（`:963`）。
   这一条需要一个单独的补丁，不在第 10 片里。
4. **没有改 6.18 线一个字**：`kernel-overlays/` 一行没动，
   6.18 线自己的 `staging/trial/flash-uImage` md5 仍是 `35685e15e79c27bb30ca6fdb3b0af1d5`。

---

## 八之十一、第 11 片：失败的数据面**怎么回来**

**判决**：八之十.7 的第 3 条那个缺口查清了，是一个**真缺口**，
而且是**我们自己的 6.18 世代早已修好、7.3 公版没有**的。
补丁 190 把 6.18 的那一段搬进 7.3 树。
**这是软件侧的最后一片。**

### 8之十一.1 两代的差别就在一个函数里

ours（6.18 世代，`kernel-overlays/.../zx279133-netdev.c`）的
`zx279133_lan_netdev_open()`（`:2161`）：

```c
	mutex_lock(&eth->datapath_lock);
	if (!(eth->datapath_users & ZX279133_DATAPATH_USER_LAN) ||
	    !eth->lan_datapath_ready) {
		ret = -ENETDOWN;
		goto out_unlock;
	}

	ret = zx279133_datapath_restore_failed_locked(eth);   /* ← 就是这一段 */
	if (ret)
		goto out_unlock;
```

7.3 树（打补丁前）的同一个函数（`:2132`）：

```c
	mutex_lock(&eth->datapath_lock);
	if (!(eth->datapath_users & ZX279133_DATAPATH_USER_LAN) ||
	    !eth->lan_datapath_ready ||
	    !READ_ONCE(eth->hardware_prepared)) {            /* ← 多出来的一道 */
		ret = -ENETDOWN;
		goto out_unlock;
	}

	netif_carrier_on(ndev);
```

**两代都有三个恢复入口，7.3 只剩两个**：

| 恢复入口 | ours（6.18） | 7.3 树 |
|---|---|---|
| WAN 的 open（`zx279133_open()`） | `:352`，**只在已有第二个使用者时** | `:303`，同上 |
| LAN 服务的 get（`zx279133_lan_datapath_get()`） | `:2364`，同上 | `:2258`，同上 |
| **conduit 的 open**（`zx279133_lan_netdev_open()`） | **`:2173`，无条件** | **没有** |

**⇒ 差的就是第三行，而且差在「无条件」三个字上。**

### 8之十一.2 为什么这一段要紧：数据面失败之后没有回路

7.3 树里 `datapath_failed` 的两个来源：

1. `zx279133_datapath_suspend()`（`:864`）—— 恢复工作重启数据面时先挂起；
2. `zx279133_datapath_resume()` 失败（`:879`）。

而**恢复工作自己遇到 `datapath_failed` 是直接放弃的**
（`:963`：`... || eth->datapath_failed) { eth->tx_recovery_queues = 0; goto out_tx_unlock; }`）
⇒ **失败的数据面不会被恢复工作再试一次。**

另一条路：`zx279133_xsk_pool_cycle()`（`:1030`）挂起再恢复，
恢复失败时它再试一次（`:1068`），还是不成就把 `datapath_failed` 留在那里。

⇒ 7.3 的树里，**数据面一旦失败，板子就没有回路**：
WAN 与 LAN 服务的恢复入口都要求「**已经有第二个使用者**」，
所以**只开了一个口的板子**（只有 WAN，或只有 LAN）**回不来**。

### 8之十一.3 为什么必须是 conduit 的 open

* 在 7.3 里，`zx279133_lan_netdev_open()` 是 **conduit 的 ndo_open**：
  `zx279133-lan-conduit.c:25` → `zx279133_lan_service_netdev_open()`。
  6.18 的同一个函数**也是** conduit 的 ndo_open ——
  **我们自己 6.18 的 `zx279133-switch.c:4164` 那条注释就是这么写的**。
* DSA 在**用户口 up** 的时候打开 conduit：`net/dsa/user.c:406`
  `err = dev_open(conduit, NULL);`（在 `dsa_user_open()` 里）。
  ⇒ 操作者的 `ip link set lan1 down && up` 就是回路。
* 6.18 的 `zx279133_datapath_attach()`（`:325`）与 7.3 的
  **逐字同形**（都 attach `eth->ndev` 与 `eth->lan_ndev`），
  所以「在 conduit 自己的 open 里把它 attach 回去」是**出货驱动里已验证的写法**。

### 8之十一.4 补丁 190：顺序是关键，不是照抄

`190-sk-datapath-restore-on-conduit-open.patch`（3038 B，+25 -2，
只改 `drivers/net/ethernet/zte/zx279133-netdev.c`）

**顺序**：`datapath_failed` ⇒ 硬件是**没准备好**的
（挂起会 unprepare，恢复失败会再 unprepare 一次），
所以 7.3 那道 `hardware_prepared` 检查**排在恢复前面**的话，
open 会在**任何恢复还没跑到**之前就被拒掉。
⇒ **恢复放在前面，硬件检查放在后面**（恢复成功会把硬件重新准备好）。

**恢复在健康的数据面上是个空操作**：
`zx279133_datapath_restore_failed_locked()` 第一句就是
`if (!eth->datapath_failed || !eth->datapath_users) return 0;`
⇒ **能跑的板子，一点行为都不变。**

### 8之十一.5 门禁：18 条，**按位置**判

`tools/check-sk-datapath-restore.py` —— 它问的是**顺序**，不是「有没有」：

* 恢复被调用了；在 `datapath_lock` 里调用；**排在硬件检查之前**；
* 恢复失败**不能让 open 继续**；
* 恢复助手**在数据面健康时立刻返回**（否则健康的板子也要付代价）；
* 这个函数**是** LAN 服务的 `.netdev_open`；
* conduit 的 open **真的**走到服务；
* DSA **真的**在用户口 up 的时候打开 conduit ——
  这条查的是 `dsa_user_open()` **函数体内**，
  因为树里**不止一处**打开 conduit（`user.c:2941` 也开），
  查整个文件会放过错误的那一处
  （这是负控逼出来的：那一项一开始就是红的，因为门禁查得太松）。

负控 `tests/test_sk_datapath_restore.py`（**9 项**）：
每一项改坏一处，门禁必须**转红在指定的那条检查上**。

### 8之十一.6 未做

1. **没有实机证据**：本节每一句都是**读树**读出来的。
   板子回来之后，要真的把数据面弄失败一次（拔插网线、
   让恢复工作重启失败）才知道这个回路管不管用。
2. **第 11 片也没有产物级探针**：补丁 190 **没有引入任何字符串字面量**
   （它加的是判断与注释），所以产物层问不了它 —— 与第 8 片同因（见 7.41）。
3. **两代还有一处差别没有搬**：6.18 的 conduit open 还要看
   `zx279133_tx_available_locked()`，为假就不给 carrier（`:2178-2183`）；
   7.3 的没有这道判断。**本片没有搬它**：它管的是
   「环被隔离时别把口点亮」，与「数据面失败后怎么回来」是两件事，
   而 7.3 的 `zx279133_datapath_attach()`（`:895`）已经在 attach 时做了同样的判断。

---

## 八之十二、`paths:` 的漏洞：改了门禁，却不会重建

### 8之十二.1 判决

1. **7.3 线缺 10 个、6.18 发布线缺 2 个**「工作流会跑、`paths:` 没收」的文件。
2. **另有三处是间接的**：`tools/mkgzip.py`、`overlays/`、
   `configs/sk-d840n-offload.config` —— 7.3 线用着它们，`paths:` 里却没有。
3. 缺口的形状是**静默**的：改了那个文件**不会**起一轮构建，
   于是**读它的门禁从那一刻起就不再被验证**，而仓库看上去一切正常。
4. 已补齐，并加一道**机器查**的门禁，让这件事不再靠人记得。
5. **还有一处是反向的**：7.3 线的 `paths:` 只列了 `docs/101-*`，
   而**文本卫生门禁读的是全仓所有 .md**，且**只有 7.3 线在跑它** ——
   6.18 线根本没有这道门禁（grep 全文无 `text_hygiene`）。
   ⇒ `docs/` 之外那份文档里长出一个替换字符或控制字符，
   **没有任何一轮构建会发现**。已把 7.3 线的条目换成 `**.md`。

### 8之十二.2 证据：缺的是哪些

7.3 线（`build-sk-d840n-73.yml`）：

| 文件 | 在哪一步被用 | 不在 `paths:` 的后果 |
|---|---|---|
| `tests/test_sk_kernel.py` | repository gates | 改了内核门禁不重建 |
| `tests/test_sk_led_dts_gate.py` | repository gates | 同上 |
| `tests/test_sk_layout.py` | repository gates | 同上 |
| `tests/test_sk_uboot.py` | repository gates | 同上 |
| `tools/check-sk-lan1-led.py` | 面板 LED 拓扑门禁 | **改了灯的门禁不重建 ⇒ 实机那盏 LOS 灯没人验** |
| `tests/test_sk_lan1_led.py` | 面板 LED 拓扑门禁 | 同上 |
| `tools/check-sk-dtb-compat.py` | 「每个 compatible 都有驱动」门禁 | **改了它不重建 ⇒ dtb 的 compatible 没人验** |
| `tools/check_sysupgrade.py` | 分区导出 | 改了升级检查不重建 |
| `tools/dtbparts.py` | 分区导出 + dtb 编译门禁 | **改了分区表检查不重建 ⇒ 刷错分区没人拦** |
| `scripts/prepare-sk-d840n-ui.sh` | Configure | 改了 UI 的准备不重建 |

6.18 线（`build-sk-d840n.yml`）：

| 文件 | 在哪一步被用 | 后果 |
|---|---|---|
| `tests/test_sk_workflows.py` | LED CI regression | **门禁中的门禁自己改了不重建** |
| `tests/test_kernel_version_single_source.py` | LED CI regression | 改了内核版本单源的控制不重建 |

**再往下一层**（不是工作流直接引用的，是它调用的脚本引用的）：

| 文件 | 谁用它 | 后果 |
|---|---|---|
| `tools/mkgzip.py` | `scripts/configure-sk-d840n.sh:26` 把它装成 OpenWrt 树里的 `scripts/skd840n-mkgzip.py`；`overlays/target/linux/zte/image/Makefile:16` 用它压内核 | **改了压缩脚本，7.3 线不重建 ⇒ 镜像仍是旧脚本压的** |
| `overlays/` | 7.3 线 `cp -a overlays/. openwrt/` | 改了共享的 OpenWrt 侧覆盖层，7.3 线不重建 |
| `configs/sk-d840n-offload.config` | `scripts/configure-sk-d840n.sh:12` 的 `OFFLOAD_PROFILE` | 改了 offload 片段，7.3 线不重建 |

### 8之十二.3 门禁：206 条

`tools/check-sk-workflow-paths.py` 读工作流，问两件事：

* **它跑的、它提到的，每个仓库文件都得被 `paths:` 覆盖** ——
  「提到」这一半不是多余的：7.3 线用 `"$PY" -c '... "tools/mkgzip.py" ...'`
  加载压缩脚本、用 `cp -a overlays/.` 复制目录，
  这两种**都不是「解释器 + 路径」的形状**，只查调用形态就看不见；
* **`paths:` 里逐个点名的文件必须真的存在** —— 一个不存在的条目永远匹配不上，
  看着像在管事，其实在骗人。

检查项数是**两条线加起来 206 条**（每条线各自覆盖自己那份工作流）。
它把「工作流用了什么」与「`paths:` 收了什么」摆在一起比，
所以新增一个门禁而忘了登记，**立刻变红**。

**写这道门禁时踩到的两个坑（写在这里，别人会踩）**：

1. **YAML 里 `*` 是别名的开头。** `- **.md` 不是「一个 glob」，
   它是**语法错误**（YAML 报 `found undefined alias`）⇒ 写成 `- '**.md'`。
   门禁的解析器也得把引号**剥掉** —— 引号是 YAML 的语法，不是路径的一部分。
2. **`paths:` 块本身不算「用到」。** 列在里面的文件如果也算「被提到」，
   门禁就退化成**拿 `paths:` 跟自己比**，什么也证明不了。
   ⇒ 扫「被提到的文件」时**必须把 `paths:` 块排除在外**。
   （负控里那一条「拿掉被运行的行，门禁必须不再问它」就是靠这一条才成立的。）

### 8之十二.4 负控：16 项

`tests/test_sk_workflow_paths.py`：每一个突变都要**转红在指定的那条检查上**，
而不是「在某处变红」：

| 突变 | 必须转红的那条检查 |
|---|---|
| 拿掉一个「工作流用到的文件」条目 | `… : <文件> is covered by paths` |
| 把一个覆盖了文件的 glob 改坏 | 同上（该文件那条） |
| 整个 `paths:` 块删掉 | `… : the workflow has a paths: block` |
| 把一个条目指向不存在的文件 | `… : <条目> exists` |
| 拿掉一个**只被提到**的文件的条目 | `… : <文件> is covered by paths` |
| **拿掉一个文件被运行的那些行** | **门禁必须不再问它**（反过来的一条：证明门禁真的在读调用行，而不是拿 `paths:` 跟自己比） |

负控**直接 import 门禁本体**，不另抄一份解析逻辑 ——
抄一份的话，规则改了负控还会照旧通过，等于什么都没验。

### 8之十二.5 负控把**绿着的 job**显示成红的

这道门禁第一次在 CI 上真跑（7.3 run #54，`aa3dd95b`）就撞上了一个
**只有 run 页面才看得见**的问题：

- 「repository gates」这个 job 是**绿的**；
- 但 run 页面的 **step summary** 写着 **「106 check(s), 1 failed」**，
  还带着一行 `FAIL build-sk-d840n-73.yml: tools/mkgzip.py is covered by paths`。

**先排除「门禁真的红了」**：真判决是**两条线加起来 206 条**，
summary 里只有 **106 条**（一条线），而且 summary 底部写着它读的那个文件是
`/tmp/sk-wf-paths-*/dropnamed/build-sk-d840n-73.yml` ——
**一份被负控改坏的副本**，不是工作流本身。

⇒ 机制：**负控自己也调用门禁**，而门禁把判决写进 `GITHUB_STEP_SUMMARY`，
用的是 `write_text`（**覆盖**，不是追加）⇒ 负控那一跑（改坏的副本）
**把真判决覆盖掉了**。

⇒ 所以 run 页面显示的是**负控的预期失败**，不是门禁的红。
门禁本身的**退出码是对的**（有失败就 `return 1`），
job 绿是因为真判决确实全过。

**这仍然是一个缺陷**，而且是这一类里最难发现的一种：
它不改变任何判定，只改变**人看到的那个页面** ——
一个绿着的 job 看上去像红的。人会以为门禁坏了，
或者更糟：以为门禁坏了就把它删了。

⇒ **修复**：负控跑门禁时**摘掉 `GITHUB_STEP_SUMMARY`**（控制不许发判决）；
并给这个性质本身配一条负控：
「把摘要文件放在那里，跑一次控制用的门禁，要求那个文件**根本没有被写出来**」。

**反向验证**（把修复撤掉）：负控从 14 项变 16 项，
**转红的正是那一条** —— `a control does not publish a verdict to the run page`
（两条线各一次，16 项里 2 项红，其余 14 项照旧绿）。

⇒ **教训（可推广）**：**一个会往 run 页面写判决的工具，被别人调用时必须自己管住那个出口**。
门禁写 summary 是对的（那正是它该做的），但**负控调用门禁**这件事，
不能顺手把「判决发给谁」也继承过去。
凡是有「写到一个共享出口」的工具，都要问一句：
**别人调我的时候，会不会把我该说的话说给不该听的人？**

### 8之十二.6 未做

1. **门禁不追进脚本里**。`configs/sk-d840n-offload.config` 这类
   「脚本读的文件」是**手工**补进 `paths:` 的；门禁只能看见工作流自己提到的东西。
   ⇒ **再往下一层还得有人记着**，这一层没有机器查。
2. **门禁不追进 OpenWrt 树里**。`overlays/` 里的文件被 `cp -a` 进树之后，
   树里再引用它们的方式，本门禁看不见。
3. ~~**这道门禁在 CI 里第一次执行是在这次推送之后**~~ **已经在 CI 上跑过了**：
   7.3 run #54（`aa3dd95b`）的「repository gates」job **绿**（28 秒），
   206 条检查全过；**而且它当场暴露了一个只有 run 页面才看得见的缺陷**
   ——负控把真判决覆盖成了自己的预期失败（见 8之十二.5，已修）。
   ⇒ 「本机验过」这件事，现在有 CI 证据了。
4. **6.18 发布线只加了三行**，没有改它的任何步骤、任何门禁的判据 ——
   发布线的不动是硬约束。

---

## 八之十三、本机补齐 host 工具链：本地门禁从 16/18 到 18/18

### 8之十三.1 判决

CI 的「repository gates」那一步里有两道门禁**在本机跑不了**：

| 门禁 | 缺什么 | 症状 |
|---|---|---|
| `tests/test_sk_led_dts_gate.py` | `dtc` | `dtc: command not found` |
| `tests/test_sk_layout.py` | `cc -lfdt` | `fatal error: 'libfdt.h' file not found` |

⇒ 本机那份命令清单（`scratch/run_ci_gates_local.py`）**16/18**，
缺的两项是**环境**缺口，不是代码缺口。

现在三件 host 工具在本机补齐了 ⇒ **18/18 全绿**。

### 8之十三.2 为什么值得补

CI 的 gates job 几分钟就出结果，但**推送会连带起一轮 90 分钟的构建**
（两条线各一轮）。本机能在推送前把门禁本身跑完，
等于把「**门禁写错了**」与「**树写错了**」这两件事分开 ——
这正是「门禁红时先问树对不对、再问门禁对不对」那条规矩的前提（见 7.19、8之十二）。

### 8之十三.3 版本必须对齐 CI，不能拿「手边有的」凑

CI 的 gates job 装的是：

```
sudo apt-get -qq install --no-install-recommends device-tree-compiler libfdt-dev python3-yaml
```

Ubuntu 22.04 上这两样**都是 1.6.1**。⇒ 本机也编 1.6.1。

两个具体的坑：

1. **内核树里的 `scripts/dtc/libfdt` 是上游 libfdt 的裁剪版**：
   它**没有 `fdt_check.c`**，因而**没有 `fdt_check_full`** ——
   `sk-layout-check.c` 正好调用它 ⇒ **链不上**。
   这不是本机环境的问题，是那份裁剪的真实内容（先拿内核树的 libfdt 编，结果是
   `undefined symbol: fdt_check_full`）。
2. **dtc 的发行 tarball 与内核 tarball 都不带生成好的 lexer/parser**
   ⇒ 必须有 flex 与 bison。本机没有，于是用 **winflexbison**（免安装包，
   `win_flex.exe` / `win_bison.exe`）现生成。
   ⇒ 顺带一个 win_flex 的怪癖：**它不认 `-oFILE`，只认 `-o FILE`**。

### 8之十三.4 三件东西的来历

| 工具 | 版本 | 来源 | 备注 |
|---|---|---|---|
| `dtc` | 1.6.1 | `mirrors.edge.kernel.org/pub/software/utils/dtc/dtc-1.6.1.tar.gz` | win_bison 生成 parser、win_flex 生成 lexer、clang 编译 |
| `libfdt` | 1.6.1 | 同一份 tarball 的 `libfdt/` | 编成 `libfdt.a` + 三个头（`libfdt.h`、`libfdt_env.h`、`fdt.h`），与 Ubuntu `libfdt-dev` 装的一致 |
| `cpp` | 垫片 | 本机写的 30 行 C | 见 8之十三.5 |

### 8之十三.5 垫片要补的那一个差别（不是「随便找个 cpp」）

GNU cpp 在 Linux 目标上**把 `linux` 预定义成 1**；clang 不预定义它。

门禁里有一条**对照分支**正是靠这个内建宏才能复现原始故障：
去掉 `-undef` 之后，`linux,default-trigger` 会被改名成 `1,default-trigger`，
而 dtc **照单全收**（这正是当初那类故障的形状）。
⇒ 垫片转发给 `clang -E`，并且**只在调用方没有给 `-undef` 时**加 `-Dlinux=1` ——
而 `-undef` 恰恰就是用来消掉那个宏的，所以这两条规则合起来
**与 GNU cpp 的行为等价**。

实测（`cpp -dM`）：

```
$ cpp -dM -x assembler-with-cpp -        # 没有 -undef
#define linux 1
$ cpp -dM -x assembler-with-cpp -undef - # 有 -undef
（没有 #define linux）
```

⇒ 本机跑这条门禁时，那条对照分支**真的跑了**，不是被跳过。

### 8之十三.6 边界（未做 / 不成立）

1. **本机 dtc 没有 YAML 输入**（按上游 Makefile 的 `NO_YAML=1` 去掉 `yamltree.c`）。
   门禁只喂 `.dts`，所以不影响门禁结论；**但 `dtc -I yaml` 在本机不可用**。
2. **本机 `cpp` 是 clang 的，不是 GNU cpp**。除了 8之十三.5 那一条内建宏，
   其余行为一致；但**不能**用它证明「GNU cpp 会怎么报」。
3. **这些工具在 `scratch/toolchain/`，不在仓库里** ——
   它们是本机的工装，不是出货物。⇒ CI 不会因为它们变红，也不需要它们。
4. **版本对齐是「当前」事实**：CI 的 apt 版本一换，本机就得跟着换
   （换 `device-tree-compiler` 的版本要重编 dtc，换 `libfdt-dev` 要重编 libfdt）。
5. **门禁本身没动**：本轮**一行仓库门禁代码都没改**，
   改的只是本机环境。⇒ 这也顺带证明那两道门禁在 CI 上是真的绿的
   （CI 从头到尾都是绿的，本机是第一次能自己跑它们）。

---

## 八之十四、产物到手：新镜像 9/9 全绿，以及第 11 片的**受控差分**证据

### 8之十四.1 判决

7.3 线的 run **#51**（`03109d6b`，第 10 片）**整轮绿**（1 小时 12 分 21 秒），
它的 flash 产物已取回本地（`staging/trial73b/flash/`）：

| 检查 | 旧产物（run #36/#37） | 新产物（run #51） |
|---|---|---|
| 自证（sha256sums 六项 / dtb native241 / banner 7.3.4） | OK | OK |
| **9 条产物探针** | **7/9** | **9/9** |
| 压缩载荷 vs 8 MiB 分区 | 5,811,931 B（余 2,576,677 B） | 5,813,416 B（余 **2,575,192 B**） |

⇒ 第 7 片（`shared TX recovery failed`）与第 10 片（`tx_invalid_completions`）
的串**在要刷进板子的那个镜像里**，字节级可见。
（对照：旧产物在同一道门禁上**恰好红在这两条**上，其余七条两边都绿 ——
所以这道门禁对这两个补丁**有分辨力**，不是恒绿。）

一条命令做完这三件事：`python3 scratch/verify_73_artifact.py staging/trial73b/flash`。

### 8之十四.2 取件链：产物早就下来了，只是落在 Downloads 里

GitHub 的产物 zip 要登录态，而 `fetch()` 读不了跨域跳转的响应
（`opaqueredirect`）⇒ 改成让浏览器**原生下载**：

1. 导航到 `<repo>/actions/runs/<run_id>/artifacts/<artifact_id>`
   —— 页面上的「Download … (opens in a new tab)」指向的就是它；
2. 浏览器把文件落成 `%USERPROFILE%\Downloads\<uuid>.tmp`；
3. **判完整性看尾部 22 字节有没有 `PK\x05\x06`**（zip 的 EOCD），
   **不要靠扩展名，也不要靠「下载完没有」**。

⇒ `ERR_ABORTED` 是**正常**的：那次导航本身就是一次下载。

（产物 id 与大小是公开的：`GET /repos/<owner>/<repo>/actions/runs/<run_id>/artifacts`
匿名可读；只有 zip 本身要登录态。）

### 8之十四.3 第 11 片（190）没有字符串探针 —— 但可以**受控差分**

第 11 片的补丁只改了 `zx279133-netdev.c`（+25 -2），**没有引入新字符串** ⇒
按第 8 片的结论（函数名在像里找不到：kallsyms 的名字表是压缩的），
它**没有直接的产物探针**。

但「没有直接探针」**不等于**「只能靠推断」。这里有一条受控差分：

- #51（`03109d6b`）与 #52（`b1d79b17`）之间**只改了 CI 侧的文件**（产物门禁本身），
  镜像代码没动；
- 实测：两个 run 的 `flash-uImage` **逐字节相同**
  （5,813,480 B，sha256 `8a4c32e804de88ead241b122734d9d469410386ec736545cf1e295a820bf8975`）；
- ⇒ 「只改 CI 文件不会改镜像」这件事**已经被实测证明**，不是假设；
- 于是 #53（`bb155075`，与 #52 的唯一代码差异就是 190 补丁）
  如果产出**不同的** `flash-uImage`，这个差异**就只能**来自 190。

⇒ 这比字符串探针更强：它**不依赖任何字符串**，
只依赖「两轮之间只差一个补丁」这个事实 + 一次逐字节比较。
（实测结论见 8之十四.4。）

### 8之十四.4 实测

（#53 的 flash job 跑完之后补。）

---

## 九、下一步（按片重打，每片一次 CI）

**没搬的，按片来：**

1. **IDM 的 RX 缓冲池**：**布局已结论——不移植；投递协议已判——移植**。
   * *布局*（五个槽位指向谁、池大小寄存器）：跟随 7.3 公版。逐槽位对照见
     第八节——公版的映射按厂商布局一一对应，我们 6.18 世代那套是**为 6.18
     的 free ring 内存布局写的**，搬过来会指向错误的内存。
   * *投递协议*（refill 怎么通知硬件）：**已判，已落成第 2 片的第二个补丁**
     `120-sk-idm-bp-refill-protocol.patch`。7.3 启动时投 2048 个缓冲，
     正好是 1024 的倍数，公版的 `writel(count)` 在厂商的解码下等于
     「零个缓冲」。
   * **RX 帧落点 0x80**（本板实测，run 160）是第 2 片的第一个补丁
     `110-sk-idm-rx-frame-offset.patch`，见第八之二节。它不是「池布局」，
     是**帧在缓冲区里的位置**，两件事分开判才对。
   * 第 1 片的寄存器修正已落地，打完之后两代 `zx279133_idm_tx_configure` 的
     33 条 `writel` 里**只有 6 条不同，且全是 BP 池那几条**（见下）。
   * **观察项**：我们 6.18 世代的 8192 深度没有带过来；若实机 `ethtool -S`
     出现 RX 欠载，再加深（做法见第八节末）。
   * **判据（实机）**：`/proc/net/dev` 的 RX 计数在涨、`ethtool -S` 无欠载；
     若 RX 全 0，用 `zx279133.rx_frame_offset=0` 再启一次，把两种形状
     在板子上分开。
2. **IDM 的其余部分**。这一条原来是「约 78 个 hunk 的重写」，那是按 diff
   规模估的，**不精确**。按函数体逐个对照之后（`tools/func-body-diff.py`，
   两代 `zx279133-idm.c`），实际工作量小得多，也清楚得多：

   | 判定 | 个数 | 含义 |
   |---|---|---|
   | IDENTICAL | 27 | 去掉注释与空行后**逐字相同**，不用动 |
   | similar（60–92%） | 9 | 大部分行相同，看一眼差异即可 |
   | REWRITE（25–52%） | 6 | 真正要重写 |
   | 只在 6.18 世代 | 10 | 7.3 里没有，要作为新代码写进去 |
   | 只在 7.3 世代 | 4 | 7.3 新增，其中一个是我们改过名的 |

   要重写的那些（我们行数 / 7.3 行数，相同行占比）——**这张表写在第 8 片之前，
   已经过时**：第 8 片（八之八）用 `tools/func-body-diff.py` 逐个函数体重跑过一遍，
   判定以**那一节**的表为准：

   | 函数 | 我们 | 7.3 | 占比 | 现在的处置 |
   |---|---|---|---|---|
   | `zx279133_idm_tx_configure` | **129** | 68 | 25% | **第 1 片已解决**，见下 |
   | `zx279133_idm_rx_sync_for_device` | 15 | 9 | 27% | **第 8 片已解决**（设备侧同步整个硬件写窗口） |
   | `zx279133_idm_rx_flush_refill` | 27 | 32 | similar | 第 8 片：similar，**不用动** |
   | `zx279133_idm_rx_preserve_dsa_vlan` | 21 | 21 | similar | 第 8 片：similar，**不用动** |
   | `zx279133_idm_rx_lan_vport` | 12 | 6 | similar | 第 8 片：similar，**不用动** |
   | `zx279133_idm_tx_drain` | 25 | 16 | similar | 第 8 片：similar，**不用动** |

   **`tx_configure` 划掉的理由**：函数体差异大，是因为**注释和 BP 池那几行**，
   不是寄存器值。按语句逐条比（`tools/idm-register-diff.py`，把
   `writel(...)` 当比较单位）：

   ```
   zx279133_idm_tx_configure: ours 33 writes, theirs 33 writes
   6 of 33 statements differ
   ```

   **那 6 条全部是 BP 池**：`+0x108`（jumbo 池 vs `FREE_RING1`）、
   `+0x118/+0x11c/+0x408`（三个 retrieval 环错位一位）、
   `+0x010c/+0x0110`（按深度编码 vs 写死 0x3d00）。
   **其余 27 条——控制字、`+0x0010`、路由字、`+0x0038`、`+0x0070/+0x74/+0x90/+0x94`、
   `+0x00bc`、`+0x0124`、`+0x02c8`、`+0x0490`、中断掩码与 IRQ 路由——两代逐条相同**，
   因为第 1 片已经把它们改成了实测值。

   **⇒ 第 1 片之后的 IDM 寄存器初始化已经拉齐，只剩 BP 池，而 BP 池跟随公版。**


   只在 6.18 世代的 10 个，**第 8 片逐个判过了**（八之八.1）：

   * **搬了 2 个**：`zx279133_idm_rx_has_ethertype`（L2 重写只对不带类型字段的帧做）、
     `zx279133_idm_xdp_pass_valid`（XDP 程序可能把栈留在缓冲区之外）；
   * **不搬 8 个**：RX 调试转储（`dump_frame` / `dump_nonzero` / `rx_find_header` /
     `find_broadcast` / `rx_dump`）是诊断用，不在交付路径上；
     `rx_is_jack` 与两个 jack 计数器、**`cpu_dp->rcv()` 直调 tagger** 划入**第 9 片**
     （它们要 DSA 拓扑与实机才有意义）；`bp_ring_cfg` 是第 1 片剩下的 BP 池布局，
     而 **BP 池跟随公版**；
   * `zx279133_idm_rx_sync_xsk_for_cpu` —— 7.3 里叫
     `zx279133_idm_rx_sync_xsk_for_device`，**是改名不是新增**。

   只在 7.3 侧的 4 个：`zx279133_idm_rx_count`、`zx279133_idm_rx_parsed`、
   `zx279133_idm_rx_prepare_dsa`、`zx279133_idm_rx_sync_xsk_for_device`。

   **⇒ 这一条已经做完**：第 8 片（八之八）把 6 项搬进 7.3 树（补丁 170），
   判了 4 项不搬并写下理由；原先估的「先 6 个 REWRITE、再 10 个新增」
   在逐个函数体重跑之后**不成立** —— REWRITE 里三个是 similar，
   两个已在前面的片里解决。
3. **9132 交换表 / switch.c**（76 个函数，4150 行）。这是 SK-D840N 四个 GE 口能不能活的问题。
   **第 3 片已落地**（见八之三）：`kernel-overlays-73/` 带驱动本体与两个私有头，
   `kernel-patches/7.3-local/130` 给 7.3 的 LAN 服务补上控制事务并接好
   Kconfig/Makefile。**已编译验证**——run #36~#39 连续四轮全绿，
   `Compile` 用了 46 分钟并成功；而且**按字节核对过**：那个 7.3 镜像里
   有 switch 驱动的 of_match 名、9132 内部 PHY 的注册名、面板 LED 的 cdev 名
   （见 7.31）。这一片剩下的是**实机对拍**：驱动能在 7.3 上编译不等于 LAN 口能通，
   判据是 conduit netdev 的 RX 计数在涨、四个 DSA 用户口的 link 状态正确。
4. **PON / OMCI**（约 56 个函数）。**第 4 片已落地**（见八之四）：协议核心与两个
   内核文件逐字节搬进 `kernel-overlays-73/`，
   `kernel-patches/7.3-local/140` 给 7.3 的树补上四个钩子、一次 `opsd` GPIO 轮询
   与 LOS 触发器。**已编译验证**——run #36~#39 连续四轮全绿；
   按字节核对过：那个 7.3 镜像里有 PON netdev 的名字与
   `omci_transport` 这个 sysfs 属性名（见 7.31）。
   这一片剩下的还是**实机对拍**：LOS 灯随 `opsd` 翻、`pon_state` 走 O1→O5、
   `omci_transport` 的 `submitted`/`completed` 在涨。
   和第 5 条「cnjn 会不会公开 npu-pon.ko」的强相关仍然存在：我们的栈只做
   状态机与下行，上行是用户态 `zxpond` 走 netlink 下去的。
5. **9132 芯片本身的编程**（26 个函数 + 6 张表）。**第 5 片已落地**（见八之五）：
   从 6.18 的 `np.c` 逐字搬进 7.3 的 `np.c`，并把 ERAM 表描述符的记录点补上；
   `kernel-patches/7.3-local/150` 一个补丁改 2 个文件。
   **已编译验证**——run #36~#39 连续四轮全绿；按字节核对过：
   那个 7.3 镜像里有 9132 芯片编程的每口 PHY 打印串与 PPU 字名表（见 7.31）。
   这一片上板后的判据是
   **四个 DSA 用户口的 link 状态正确、conduit 的 RX 计数在涨**。
6. **RGMII 调试**（8 个函数）：**判为取舍，不搬**——它只是 `0x15900000` 窗口的
   debugfs 端点，该块在本机全零、厂商 Linux 从不 bring up（见八之五.9）。
7. **netdev/main/mac 的健壮性修正**（约 28 个函数）：核心是 **TX 故障隔离与恢复
   机制**，**第 7 片已落地**（见 7.34）。这一条原来是「判为不整体移植」，
   理由写的是「7.3 树没有这个模型」——**那条结论已被推翻**：
   7.3 树**有** `zx279133_datapath_suspend()`/`zx279133_datapath_resume()`
   与 `zx279133_lan_datapath_get()`/`put()`，缺的只是**故障隔离那半**
   （`tx_fault_queues` 隔离 + `datapath_failed` + 恢复工作）。
   `160-sk-tx-fault-isolation.patch` 把那半搬了过去，
   并配了一道**按位置判**的门禁（36 条）。
8. **第 8/9/10 片**（软件侧最后三片）：
   * **第 8 片已落地**（见八之八）：`170-sk-rx-delivery-safety.patch`
     （6 处纯软件正确性修复）+ 39 条门禁 + 24 项负控。
     **run #49 是它第一次真的编进镜像** —— 此前三轮它从未进过树（见 7.39）。
   * **第 9 片是否定结论**（见八之九）：`cpu_dp->rcv()` 直调**不搬**
     （7.3 走标准 DSA 路径，硬搬会被 tagger 处理两次）、
     jack 两个计数器**不搬**（7.3 已由 DSA 用户口 `lan1` 的 `rx_packets`
     与 conduit 的 `rx_dropped` 回答得更准）。
   * **第 10 片已落地**（见八之十）：`180-sk-tx-isolation-stats.patch`
     把 TX 隔离的四个计数器接到 `ethtool -S` 上（+19 -0）
     + 33 条门禁 + 11 项负控 + 16 项端到端。
   * **第 11 片已落地**（见八之十一）：`190-sk-datapath-restore-on-conduit-open.patch`
     把 ours 6.18 世代「conduit 打开时恢复失败的数据面」搬进 7.3 树（+25 -2）
     + 18 条门禁 + 9 项负控。
   ⇒ **软件侧到此为止**：剩下的全是**实机对拍**。

验收口径与 6.18 线一致：**CI 绿 + 门禁脚本全过 + 实机启动后
`/proc/net/dev` 有预期网口、`ethtool -S` 计数在涨**。

---

## 十、复现本文结论的命令

```sh
# 1) 7.3.4 底包 + 39 补丁（本机已跑通，约 3 分钟）
python3 ci-build-repo/tools/prepare-sk-kernel-73.py \
        --tree linux-7.3.4 --version 7.3.4 \
        --openwrt <openwrt-checkout> --report kernel-provenance-73.json

# 2) 板级层拷进 7.3 树
sh ci-build-repo/tools/apply-sk-overlay-73.sh linux-7.3.4

# 3) 函数级迁移面对照
python3 ci-build-repo/tools/func-surface-diff.py \
        ci-build-repo/kernel-overlays/drivers/net/ethernet/zte \
        linux-7.3.4/drivers/net/ethernet/zte

# 4) 板级层的编译前提预检（overlay 拷入前 / 拷入后各一次）
python3 ci-build-repo/tools/check-sk-board-api.py pre \
        --tree linux-7.3.4 --report board-api.json
sh ci-build-repo/tools/apply-sk-overlay-73.sh linux-7.3.4
python3 ci-build-repo/tools/check-sk-board-api.py post \
        --tree linux-7.3.4 --report board-api.json

# 5) IDM 的移植工作单：同名函数逐个比函数体
python3 ci-build-repo/tools/func-body-diff.py \
        ci-build-repo/kernel-overlays/drivers/net/ethernet/zte/zx279133-idm.c \
        linux-7.3.4/drivers/net/ethernet/zte/zx279133-idm.c

# 6) IDM 的寄存器初始化：按 writel 语句逐条比（函数体差异的真实来源）
python3 ci-build-repo/tools/idm-register-diff.py \
        ci-build-repo/kernel-overlays/drivers/net/ethernet/zte/zx279133-idm.c \
        linux-7.3.4/drivers/net/ethernet/zte/zx279133-idm.c \
        zx279133_idm_tx_configure

# 7) 第 3 片：9132 交换表驱动的编译前门禁（overlay 拷入之后）
python3 ci-build-repo/tools/check-sk-switch-api.py \
        --tree linux-7.3.4 --repo ci-build-repo

# 8) 第 4 片：PON / OMCI 栈的编译前门禁（overlay 拷入之后）
python3 ci-build-repo/tools/check-sk-pon-api.py \
        --tree linux-7.3.4 --repo ci-build-repo --openwrt <openwrt-checkout>

# 9) 第 5 片：9132 芯片编程的编译前门禁（overlay 拷入之后）
python3 ci-build-repo/tools/check-sk-9132-api.py \
        --tree linux-7.3.4 --repo ci-build-repo

# 10) 工作流自身的门禁（两条线都在推之前跑）
python3 ci-build-repo/tests/test_sk_workflows.py

# 11) 本机的编译预言机：clang 对 7.3 树做语法检查（秒级，代替 50 分钟的 CI）
#     仓库自己的工具，7.3 线在 Compile 之前跑的是同一个（见 7.17）。
#     配置片段直接给三个（工具自己合并，缺的那个会跳过）——
#     CI 里读的是 OpenWrt 克隆里的同一批文件。
python3 ci-build-repo/tools/kcheck.py --tree scratch/test73c \
        --config scratch/cnjn-iw/target/linux/generic/config-7.3 \
        --config scratch/cnjn-iw/target/linux/zte/config-7.3 \
        --config scratch/cnjn-iw/target/linux/zte/zx279133/config-7.3 \
        --file drivers/net/ethernet/zte/zx279133-np.c \
        --file drivers/net/ethernet/zte/zx279133-idm.c \
        --gen scratch/kgen73
# 加 --objdir scratch/kobj 会编出目标码并按 modpost 的规则查符号闭合；
# --raw 打印编译器的原话。
python3 ci-build-repo/tests/test_sk_kcheck.py   # 判决规则的单元测试

# 12) 哪些 .c 真会被编进内核（Kbuild 的 obj-$(CONFIG_X) 解析）
python3 scratch/kobjmap73.py --tree scratch/test73c \
        --config scratch/cfg73/config-7.3.merged

# 13) 驱动在 7.3 里是内建还是模块（决定镜像里有没有它）
python3 scratch/kcfg73.py

# 14) 厂商固件怎么加载模块：从目标固件的 initrd cpio 里挖出来
python3 scratch/cpio_pick.py scratch/re_other_fw/fit/initrd-1.bin \
        etc/modules.d/30-zte-npu etc/modules-boot.d/30-zte-npu

# 15) 在本机跑工作流里那一步的 shell（从 YAML 原样取出，不复制）
python3 scratch/pin_step_check.py

# 16) 门禁的反向验证：六十九处变异逐个转红
python3 scratch/reverse_check_workflow.py

# 17) 四个驱动并进同一个内核会不会撞名（用本机编出的目标码）
python3 scratch/dup_syms.py --objdir scratch/kobj \
        --nm <llvm-nm.exe>

# 18) 两条工作流的步骤清单对照（谁有谁没有）
python3 scratch/stepdiff.py --repo ci-build-repo

# 19) 7.3 线的合并配置预言机：重放整条配置序列，产出合并后的 .config
python3 scratch/cfg73_sim.py --repo ci-build-repo \
        --openwrt scratch/cnjn-iw --tree scratch/test73c

# 20) kconfig 的三值依赖闭包：证明那 39+4 个符号在这棵树上不会被夹成 =m
python3 scratch/kdeps73.py ZTE_ZX279133_ETH ZTE_ZX279133_SWITCH \
        ZTE_ZX279133_FNOFFLOAD ZTE_ZX279133_LAN_CONDUIT REALTEK_PHY \
        --tree scratch/test73c --config scratch/cfg73/config-7.3.sim --quiet

# 21) 合并配置门禁的行为级演练：喂真配置必须过，七种破坏必须红
python3 scratch/boardgate_check.py

# 22) 工作流里每一个 run: 块都必须能当 shell 解析
python3 scratch/wf_shell_parse.py \
        --workflow ci-build-repo/.github/workflows/build-sk-d840n-73.yml

# 23) dtb compatible 门禁的演练：真板级树必须过，缺驱动必须红
python3 scratch/dtbcompat_check.py

# 24) 板级 DTS 的 compatible 与 7.3 树的驱动对照
python3 scratch/dtb_compat73.py --tree scratch/test73c

# 25) 171 个函数逐条对账：每个都要有归属（同名 / 账本写明搬到哪 / 有处置），
#     未记账即非零退出；账本指着的目标函数必须真在 7.3 树里
python3 ci-build-repo/tools/func-port-coverage.py \
        ci-build-repo/kernel-overlays/drivers/net/ethernet/zte \
        scratch/test73c/drivers/net/ethernet/zte

# 26) 这道对账的反向验证：改坏四处必须转红
python3 scratch/reverse_check_port_coverage.py

# 27) 两棵 OpenWrt 树的 blob 级差异：只有一个文件变了才能叫「差异面」
python3 scratch/iw_tree_diff.py          # 落盘 scratch/iw_tree_diff.txt

# 28) libbpf 依赖的系统 uapi 头：两棵树逐字对比
python3 scratch/libbpf_sys_hdr_73_20261123.py

# 29) libbpf 的最小门禁：先复现 __u128 报错，再打仓库里那个补丁
python3 scratch/libbpf_u128_gate.py

# 30) libbpf 的整包门禁：22 个 .c，门禁自己 git apply 仓库里的补丁
python3 scratch/libbpf_pkg_gate.py          # 7.3.4 的头
python3 scratch/libbpf_pkg_gate.py 618      # 同一个门禁跑在 6.18.56 的头上

# 31) 上面那道门禁的反向验证：9 处突变逐个转红，且只红在指定的那条检查上
python3 scratch/reverse_check_libbpf.py

# 32) 两棵 OpenWrt 树的 blob 级对比（7.3 线编 master，6.18 线编 sr1010）
python3 scratch/libbpf_blob_cmp.py

# 33) 装头链：内核导出 uapi 头时把 __attribute_const__ 剥掉了（7.21 的更正依据）
python3 scratch/headers_install_chain.py    # 内核侧：Makefile.headersinst + headers_install.sh
python3 scratch/openwrt_headers_chain.py     # OpenWrt 侧：toolchain/kernel-headers/Makefile

# 34) 重新生成 libbpf 补丁，并校验它能干净 apply 到上游 libbpf 1.6.2
python3 scratch/make_libbpf_patch.py

# 35) live 工装区与仓库 tools/ 的差异（谁有谁没有、谁更旧）
python3 scratch/cmp_live_tools.py

# 36) 工装区独有的内容（盲拷贝会丢掉的行）
python3 scratch/live_only_lines.py

# 37) live 工装区改过的五个脚本的语法检查
sh scratch/live_syntax_check.sh

# 38) 板级 dts 满足 native241 门禁的每一个条件（不是假设，是逐条查源码）
python3 scratch/check_board_dts_layout.py

# 39) conduit 网口的发布名：把内核自己的名字解析代码抬出来编，跑驱动的调用序列
python3 scratch/netdev_name_resolve.py

# 40) conduit 名字门禁的负控（格式串改了 / 说明符不对 / 名字被占用 / 不注册 /
#     命名两次 / 空文件，每一种都必须转红）
python3 ci-build-repo/tests/test_conduit_netdev_name.py

# 41) 7.3 线的全表：第一次成功是哪一轮（不是靠最近几轮推断）
python3 scratch/ci_first_success.py

# 42) 产物的自证：sha256sums + dtb 分区表 + 内核版本 banner
python3 scratch/verify_artifact.py staging/trial73/flash

# 43) uImage 的 banner 在哪：inflate 后按 gzip 成员拆开量（不是假设偏移）
python3 scratch/kver_members.py staging/trial73/flash/flash-uImage

# 44) 闸门的负控：版本相符放行、6.18 凭证配 7.3 镜像拒绝、厂商内核拒绝、
#     读不出版本拒绝、缺证据字段拒绝，每一种都必须转红
python3 ci-build-repo/tests/test_gatecheck.py

# 45) 上面那道负控的反向验证：10 处突变逐个转红，且只红在指定的那条检查上
python3 scratch/reverse_check_gate.py

# 46) 门禁轮 payload 的版本从待刷镜像推导（而不是写死 6.*）
python3 ci-build-repo/tools/mkctlram.py --mode announce --tag K73 \
        --staged scratch/sim73/staging/trial/flash-uImage \
        --out scratch/sim73/staging/ctl/ctl-initramfs.cpio.gz
python3 scratch/payload_flavour.py scratch/sim73/staging/ctl/ctl-initramfs.cpio.gz

# 47) 7.3 那对字节的**整条链演练**（沙箱里，不碰 live 工装区）：
#     payload → 报告 → 凭证 → 闸门 → 两个必须拒绝的负例
sh scratch/sim73_rehearsal.sh

# 48) payload 门禁自己的负控
python3 ci-build-repo/tests/test_ctl_payload.py

# 49) 第 3/4/5 片的代码到底在不在 7.3 镜像里（按字节，不是按编译日志）
python3 scratch/image_has_drivers.py staging/trial73/flash/flash-uImage

# 50) 产物级门禁：7.3 的清单查 7.3 的像（应过）；
#     同一份清单查 6.18 的像（必须红，且恰好红在那两条不一样的探针上）
python3 ci-build-repo/tools/check-sk-image-drivers.py \
        --image staging/trial73/flash/flash-uImage \
        --manifest ci-build-repo/configs/sk-d840n-73-image-drivers.json \
        --board-layer ci-build-repo/kernel-overlays-73
python3 ci-build-repo/tools/check-sk-image-drivers.py \
        --image staging/trial/flash-uImage \
        --manifest ci-build-repo/configs/sk-d840n-73-image-drivers.json \
        --board-layer ci-build-repo/kernel-overlays-73

# 51) 上面那道门禁自己的负控与反向验证
python3 ci-build-repo/tests/test_sk_image_drivers.py
python3 scratch/reverse_check_image_gate.py

# 52) 「把内核版本写进判断」的全量排查：仓库与 live 工装区各扫一次
python3 ci-build-repo/tools/check-baked-version.py --root ci-build-repo/tools
python3 ci-build-repo/tools/check-baked-version.py \
        --root tools --allow ci-build-repo/configs/baked-version-allow.json

# 53) 上面那道 lint 自己的负控与反向验证
python3 ci-build-repo/tests/test_sk_baked_version.py
python3 scratch/reverse_check_baked_version.py

# 54) 出货报告与镜像的版本对照：门禁本身、它自己的负控
python3 ci-build-repo/tests/test_release_kernel_version.py

# 55) 上面那道门禁的反向验证：12 处突变，每一处都转红在指定的那条控制上
python3 scratch/reverse_check_release_kver.py

# 56) #394 的失败在本机复现（那一步跑的就是它）
python3 ci-build-repo/tests/test_sysupgrade.py

# 57) 第 7 片：隔离门禁、它的负控、以及反向验证
python3 ci-build-repo/tools/check-sk-tx-isolation.py --tree scratch/linux-73-base
python3 ci-build-repo/tests/test_sk_tx_isolation.py --tree scratch/linux-73-base
python3 scratch/reverse_check_tx_isolation.py

# 58) 编译预言机：宿主 clang 对改后的 7.3 树做 -fsyntax-only（9 个文件）
python3 scratch/run_kcheck_73.py

# 59) 补丁本身：生成、登记、再验一次能干净应用
python3 scratch/make_txiso_patch.py
python3 scratch/add_txiso_manifest.py

# 60) 第 8 片：RX 交付路径的门禁、负控与反向验证
python3 ci-build-repo/tools/check-sk-rx-delivery.py --tree scratch/linux-73-base
python3 ci-build-repo/tests/test_sk_rx_delivery.py --tree scratch/linux-73-base
python3 scratch/reverse_check_rx_delivery.py

# 61) prepare 步的内容校验：它的负控与反向验证（都不需要内核树）
python3 ci-build-repo/tests/test_sk_prepare_content.py
python3 scratch/reverse_check_prepare_content.py

# 62) 整链 prepare（39 vendored + 8 local，含 170），以及它带出的树指纹
python3 ci-build-repo/tools/prepare-sk-kernel-73.py \
        --tree scratch/linux-73-verify4 --report scratch/prepare-73-170.json

# 63) #46 的失败在本机复现：把「160 之前」的树喂给同一个 commit 版的门禁
#     （得到恰好 25 条检查、19 条红，与 CI 逐条一致）
python3 scratch/exp_pre160_gate.py

# 64) CI 上的门禁脚本与本机的是不是同一份（逐字节），以及补丁与 MANIFEST 同样
python3 scratch/cmp_gate_script_remote.py
python3 scratch/check_manifest_remote.py
python3 scratch/hash_local_patches.py

# 65) 文档里的替换字符：一个 U+FFFD 会让 ripgrep 把整篇文档当成二进制，
#     于是对这个文档的每一次 grep 都静默地返回「没有匹配」
python3 scratch/fix_doc_replacement_chars.py

# 66) apply 上下文：一个 checkout 里的树，带 diff --git 头的补丁落地了吗
#     （本机复现 git 的 "Skipped patch"：退出 0、零字节变化）
python3 scratch/exp_ci_apply_layout.py
python3 scratch/exp_ci_apply_fix.py

# 67) apply 上下文的负控与反向验证（12 项 + 5 项）
python3 ci-build-repo/tests/test_sk_prepare_apply_context.py
python3 scratch/reverse_check_prepare_apply_context.py

# 68) 真的 160 补丁，在真的 checkout 布局里，落到「160 已应用」的状态
python3 scratch/prove_apply160_in_checkout.py

# 69) 整链 47 个补丁在 checkout 布局里跑一遍，每一个都落地
python3 scratch/run_prepare_in_checkout.py

# 70) 文档里的控制字符（NUL 与其它控制字符也会让 ripgrep 把文件当二进制）
python3 scratch/fix_control_chars.py ci-build-repo/docs/101-*.md

# 71) 第 10 片：TX 隔离计数器的门禁与它的负控（11 项负控每一项都转红在指定的检查上）
python3 ci-build-repo/tools/check-sk-tx-isolation-stats.py --tree scratch/linux-73-base
python3 ci-build-repo/tests/test_sk_tx_isolation_stats.py --tree scratch/linux-73-base

# 72) 补丁 180 本身：生成、登记、再验一次能干净应用
python3 scratch/make_patch180.py
python3 scratch/check_patch180_applies.py

# 73) 端到端：48 个补丁在 CI 的布局（checkout 里的树）里全落地，四个驱动文件
#     逐个同哈希；拿掉 180 门禁就红，且红在指定的那两条检查上
python3 scratch/verify_180_in_checkout.py

# 74) 产物级门禁：新加的两条探针在**旧的** 7.3 镜像上必须红，其余七条必须绿
python3 ci-build-repo/tools/check-sk-image-drivers.py \
        --image staging/trial73/flash/flash-uImage \
        --manifest ci-build-repo/configs/sk-d840n-73-image-drivers.json \
        --board-layer ci-build-repo/kernel-overlays-73

# 75) 像里到底有没有某个串（函数名指望不上：kallsyms 的名字表是压缩的）
python3 scratch/needle_probe.py staging/trial73/flash/flash-uImage \
        "shared TX recovery failed" "tx_invalid_completions" "lan-cpu%d"

# 76) 产物门禁自己的反向验证：18 处突变逐个转红在指定的那条检查上
python3 scratch/reverse_check_image_gate.py

# 77) 第 11 片：失败的数据面怎么回来 —— 门禁与负控
python3 ci-build-repo/tools/check-sk-datapath-restore.py --tree scratch/linux-73-base
python3 ci-build-repo/tests/test_sk_datapath_restore.py --tree scratch/linux-73-base

# 78) 第 11 片：补丁 190 的生成与端到端（49 个补丁在 CI 的布局里全落地；
#     拿掉 190 门禁就红）
python3 scratch/make_patch190.py
python3 scratch/verify_slice_in_checkout.py \
        190-sk-datapath-restore-on-conduit-open.patch \
        190-sk-datapath-restore-on-conduit-open.patch \
        tools/check-sk-datapath-restore.py \
        tests/test_sk_datapath_restore.py

# 79) `paths:` 的门禁：工作流跑的和提到的每个文件都得被覆盖
python3 ci-build-repo/tools/check-sk-workflow-paths.py \
        --workflow .github/workflows/build-sk-d840n-73.yml \
        --workflow .github/workflows/build-sk-d840n.yml

# 80) 上面那道门禁的负控（16 项，每一项都转红在指定的那条检查上；
#     有一项反过来：拿掉被运行的行，门禁必须不再问它；
#     还有一项：控制不许往 run 页面发判决）
python3 ci-build-repo/tests/test_sk_workflow_paths.py

# 81) 本机跑 CI「repository gates」那一步的命令清单（含上面这两道）
python3 scratch/run_ci_gates_local.py

# 82) 内核塞不塞得进 8 MiB 分区：逐个 gzip 成员量压缩载荷
python3 scratch/kernel_fit.py staging/trial73/flash/flash-uImage \
        staging/trial/flash-uImage staging/rollback/flash-uImage

# 83) 现场刷机链的体检：十三个脚本在不在、能不能解析、问的是哪个布局；
#     以及现场有多少文件在仓库里没有对应物（CI 看不见的那些）
python3 scratch/live_flash_tools_check.py

# 84) 造本机那三件 host 工具（dtc 1.6.1、libfdt 1.6.1、cpp 垫片）
#     需要先备好：dtc-1.6.1.tar.gz 与 win_flex_bison-2.5.25.zip
pwsh -NoProfile -File scratch/toolchain/build-host-tools.ps1

# 85) 本机跑 CI「repository gates」那一步的命令清单 —— 补齐工具后 18/18 全绿
python3 scratch/run_ci_gates_local.py

# 86) 查这三件工具的版本与那个内建宏的行为（`linux` 该在的时候在、不该在的时候不在）
scratch/toolchain/bin/dtc.exe --version
scratch/toolchain/bin/cpp.exe -dM -x assembler-with-cpp - | grep '^#define linux '
scratch/toolchain/bin/cpp.exe -dM -x assembler-with-cpp -undef - | grep -c '^#define linux '

# 87) 产物到手之后的一条命令：自证 + 9 条产物探针 + 8 MiB 体积余量
python3 scratch/verify_73_artifact.py staging/trial73b/flash

# 88) 负控不许往 run 页面发判决：跑一次控制用的门禁，摘要文件必须没被写出来
python3 ci-build-repo/tests/test_sk_workflow_paths.py
```

第十节的 18~24 号命令里，`cfg73_sim.py` 与 `kdeps73.py` 是**一对**：
前者产出「这条线真会编出来的合并配置」，后者证明那 39+4 个符号在这棵树上
不会被 kconfig 夹成 `=m`。`boardgate_check.py` 吃的正是前者的产物，
所以这两条命令合起来回答的是同一个问题：
**「7.3 线新增的那道合并配置门禁，会不会永远变红」**——答案是不会。

25 与 26 号命令是**另一对**：25 号回答「171 个函数有没有落到地上」，
26 号证明这个回答不是靠「没查所以没报」蒙混过去的。

29~31 号命令是**第三对**，而且是本轮真正救命的一组：29 号回答
「`__u128` 那个错能不能在本机复现、补丁能不能修好」，30 号回答
「整包 22 个 `.c` 里有没有第二个同源缺口」，31 号证明
「这个回答不是靠一条字符串比较蒙混过去的」。
**30 与 31 号之间还夹着一个事实**：门禁 `git apply` 的是**仓库里那个补丁本身**，
不在脚本里另抄一份修法文本 —— 否则门禁绿了而出货的补丁是坏的，两边会漂移。
32 号是排除用的：证明两棵 OpenWrt 树在「libbpf 怎么被编」这件事上
**逐字相同**，把嫌疑指回内核导出的那批 uapi 头。

**33 号是本节的一处更正**：初稿说「遮蔽还让 `__attribute_const__` 没了，
不补它报错只是往后挪一处」，那是**照着导出前的头**推的。
33 号把导出链原样打出来 —— `scripts/Makefile.headersinst:60,62,65` 让每个
uapi 头都过一遍 `scripts/headers_install.sh`，而它的 sed（第 34 行）把
`__attribute_const__` 剥掉了；OpenWrt 侧 `toolchain/kernel-headers/Makefile:64-65,106`
装进 toolchain 的正是这批**剥过的**头。
⇒ CI 报的 6 条 `__u128` 是真错，`__attribute_const__` 不是；
补丁里那条 `__attribute_const__` 降级为**防御性定义**。
34 号保证补丁是**生成**出来的、且能干净 apply —— 改说明文字不会把手改坏。

39 与 40 号命令是**第四对**：39 号回答「`lan-cpu%d` 到底会被发布成什么名字」，
40 号证明「这个回答不是靠一条字符串比较蒙混过去的」。
39 号的做法是把内核 `net/core/dev.c` 里那四个决定名字的函数
（`dev_valid_name()`、`__dev_alloc_name()`、`dev_prep_valid_name()`、
`dev_get_valid_name()`）**原样**抬出来编成宿主程序，让它走驱动自己的调用序列 ——
不是复述内核的行为，是**跑一遍内核的代码**。
⇒ 上一轮「网口叫 `lan-cpu%d`」那个结论就是被这一步推翻的（见 7.27）。

43~48 号命令是**第六组**，也是本轮**唯一一处改「能不能刷」而不是改「能不能编」**的地方：
43 号回答「内核的版本串到底在 uImage 的哪个位置」——
量出来的答案是**不在固定偏移**（7.3 那份在 inflate 后的 `0x84009a`，6.18 那份在 `0xc400b8`），
所以只能搜文本；44 号是闸门自己的负控；45 号证明 44 号不是靠「没查所以没报」蒙混过去的；
46~47 号把同一条推理用到**载荷**上：payload 骑的是哪个内核，也不能写死一个系列；
48 号是那条推理的负控。
⇒ 43 号那个「量」直接决定了闸门怎么写：**按 arm64 头后第 64 字节去切，
会切出全零**，那扇闸就会永远拒绝 —— 它会「安全地」拒绝一切，而这比放行错误更费时间。
⇒ 47 号那句 `SIM OK` 是本轮对 7.3 刷机路径**唯一**的正面证据：
从 payload 到许可到闸门，全在真的字节上跑过一遍。

71~73 号命令是**第五对**，而且它回答的是第 25 号那个结论**直接逼出来**的问题：
71 号回答「TX 隔离的四个计数器是不是真的发出来了」，
72 号保证这个补丁是**生成**出来的（改说明文字不会把手改坏），
73 号证明它**在 CI 的布局里**真的落地。
⇒ 「补丁能 apply」这件事**必须**在 CI 的形状里验一次：
`git apply` 对带 `diff --git` 头的补丁按**仓库根**解析路径，
树是 checkout 里的一个目录，于是 git 把整个补丁**默默丢掉** ——
退出码 0、一个字节都不写、默认什么都不说（见 7.39）。
