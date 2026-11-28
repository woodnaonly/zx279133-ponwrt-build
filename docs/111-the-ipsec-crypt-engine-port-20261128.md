# 111 — 把片上加密引擎（ipsec@12000000）搬到 7.3：交接计划

日期：2026-11-28
状态：**调研已完成，代码尚未动手**。本文是交给下一个执行者的全部依据。
上游树：`cnjn/immortalwrt@31e2ac4`（内核 7.3.4），补丁面 `kernel-patches/7.3/`，
DTS 面 `kernel-patches/7.3/430-arm64-dts-zx279133-sr1010.patch` + `kernel-overlays-73/`。

---

## 0. 一句话结论

**能移植，技术路径清楚，但收益低。芯片型号已实测确认落在支持名单内（步骤 0 已于 2026-11-28 完成）。**
厂商自己在 6 个 ZX279133 变体上写了这套驱动，却从未在任何一台 SK-D840N 上启用过它 —— 这是「不值得」的独立证据。

---

## 1. 任务是什么

把这颗 SoC 上一个**当前完全没启用**的硬件加密引擎，接到 7.3 的主线内核上，
让它出现在 `/proc/crypto` 里，成为一个真正的 crypto provider。

它**不在 NPU 里**。这是第一个要纠正的常见误解。

---

## 2. 已确认的事实（都有出处，不要重新推翻）

### 2.1 硬件存在，能力很强

| 项 | 值 | 出处 |
|---|---|---|
| 节点 | `ipsec@12000000` | 厂商 DT `decompiled/sk-d840n-stock.fdtdump.txt:1735` |
| compatible | `zte,zx279133-ipsec` | 同上 |
| reg | `0x12000000/16MB` + `0x13000000/16MB`（两段） | 同上 |
| interrupts | SPI 174–185（12 条） | 同上 |
| memory-region | `ipsec_ddr@90000000`（2 MB，`status="disabled"`） | `sk-d840n-stock.fdtdump.txt:551` |
| 时钟 | `ipsec_wclk` / `ipsec_aclk`，挂在 `top_clk_gate2`（`0x10e10038`，bit 10/11） | 同上 :213-222 |
| 保留区引用 | `ipsec_ddr@90000000` = `zte, zx279132-ipsec-rsv` | 同上 :551-556 |

算法集（cnjn 手册 `NPU_HANDBOOK.md` 硬件参数表 + 厂商模块自测字符串双向印证）：
AES（ECB/CBC/CTR/CCM/GCM）、SM4、DES/3DES、HMAC（MD5/SHA1/SHA224/SHA256/SHA384/SHA512）。
**不支持 ChaCha20 / Poly1305。**

### 2.2 厂商驱动是完整的 crypto API 实现（不是裸寄存器）

本地就有：`pon/vendor-modules/np_133.ko`（5,208,496 B，`=md5 92B68212542930114769E3C69EFE64B6`）。

它带了**一整套标准 Linux crypto API 封装**，所以移植是「照抄重写」而不是「从零逆硬件」：

- API 层：`crypto_register_algs` / `crypto_register_aeads` / `crypto_register_ahash` /
  `crypto_alloc_skcipher` / `crypto_alloc_aead` / `crypto_alloc_ahash` / `crypto_destroy_tfm` /
  `crypto_req_done` / `crypto_unregister_algs`
- 异步模型：`zxic_async_ciper_encrypt` / `zxic_async_ciper_decrypt` /
  `zxic_async_aead_encrypt` / `zxic_async_aead_decrypt` / `zxic_async_decrypt`
- 硬件队列：`pd_driver_encry_{en,de}queue` / `pd_driver_decry_{en,de}queue` /
  `pd_driver_hash_{en,de}queue`
- 分算法：`pd_driver_ccm_aes_{en,de}cry_{en,de}queue` /
  `pd_driver_aead_aes_md5_{en,de}cry_{en,de}queue` /
  `pd_driver_des_{en,de}cry_enqueue` / `pd_driver_des3_{en,de}cry_enqueue` /
  `pd_driver_sm4_{en,de}cry_enqueue`
- 引擎层：`zx_ipsec_top_init` → `ipsec_init` → `se_init` → `ipsec_driver_register` /
  `ipsec_driver_unregister`；中断 `zx_ipsec_irq_base_of_init`、`crypt_irqcb`；
  时钟 `ipsec_clk_reset`、`low_power_api_ipsec_clk_reset`；
  密钥 `ipsec_khb_set` / `ipsec_khb_cipher_auth_set` / `ipsec_key_ic_set` / `ipsec_apd_khb_set` /
  `ipsec_pd_khb_set` / `ipsec_pd_khb_ddr_init`；PD 描述符 `ipsec_pd0..3` / `ipsec_get_next_pd` /
  `ipsec_get_next_apd`；地址约束 `ipsec_addr_constra`；BTLL `ipsec_bttl_base`
- 自测字符串（说明厂商自己跑过）：`====== ecb(aes) test =======:`、`cbc(des3_ede)`、
  `ctr(sm4)`、`ccm(aes)`、`cmac(aes)`、`hmac(sha256)`、`hmac(sha512)` …
- 调试出口：`cat se_stats` / `cat se_hw_error` / `cat se_r_flag` /
  `echo [alg num:0 cipher 1:auth 2:NULL 3:ccm 4:not align] > test_pd`

**逆向工具已经在仓库里**（照 `docs/11` 的做法）：
`tools/ponelf.py`（ELF symtab + rela 交叉引用）、`tools/pondec.py`、
以及 `docs/11` 里用的 `staging/npidmsites.py` / `staging/npiddrbases.py` / `staging/npidmvals.py`
（寄存器访问点跟踪器）。**用它们对着 `ipsec_*` / `pd_driver_*` / `se_*` 函数跑，
能直接得出寄存器序列，不要手写反汇编。**

### 2.3 但有四道门槛

**门槛 1 — 芯片型号白名单 ✅ 已解除（2026-11-28 实测，完整证据见 §2.6）**
厂商模块按 efuse 里的一个 **4 bit 变体字段** 判定 6 个型号：
`ZX279133` / `ZX279133E` / `ZX279133G` / `ZX279133N` / `ZX279133R_A` / `ZX279133R_B`
（字符串 `current chip is ZX279133` / `...ZX279133E` / `...ZX279133N` / `...ZX279133G`）。
字段值 **> 4** ⇒ `get_sub_cputype()` 打印 `wrong chip type, please check` 并返回 6（不支持）。
**本板实测字段值 = 0 ⇒ 落在白名单内，支持。**

**门槛 2 — 7.3 树里没有 IPSEC 时钟**
cnjn 7.3 树 `include/dt-bindings/clock/zte,zx279133-topcrm.h` 只有 **28 个** clock ID
（`ZX279133_TOPCRM_CLK_LSP0_100M` … `ZX279133_TOPCRM_CLK_UNI_SERDES_50M`），
**没有 IPSEC**。厂商 DT 的 `ipsec@12000000` 节点**也没有 `clocks` 属性** ——
厂商是自己写 TOPCRM 寄存器开时钟的（字符串 `after write top crm ipsec wclk is 0x%x,aclk is 0x%x`）。
⇒ 要么给时钟驱动加两个 ID，要么在驱动里直接写 `top_clk_gate2`。

**门槛 3 — 7.3 树里既没有节点也没有保留区**
cnjn 树完整文件清单里**没有任何 ipsec/crypt 驱动**。
而且保留区口径不一致：
- cnjn 手册：加密引擎保留区是 `ipsec_ddr@90000000`（**2 MB**）
- 本仓库 7.3 的 `430-arm64-dts-zx279133-sr1010.patch:72-75`：`se_hash_reserved: buffer@9e100000`
  （**16 MiB**，`no-map`），挂在 `&nppt` 上作 `memory-region-names = "bmu", "se-hash", "idm"`
两派对这块 SoC 的保留内存布局理解不同。**动 ipsec 前必须先把这块对齐，否则 7.3 已经在用
`0x9e100000` 那 16 MiB。**

**门槛 4 — 厂商自己在这块板上从没启用过它（最强的"不值得"证据）**
- 厂商 6.18 rootfs 里**没有任何 ipsec 配置或 proc 调试入口**（grep 整个 `sk-d840n-openwrt/rootfs/etc` = 0 命中）
- 厂商 4.19 启动日志里**没有一行 ipsec 初始化输出**
- 厂商模块自己会打印 `ipsec hardware driver will not init because the devicetree of ipsec is unavailable.`
- cnjn 手册原文（针对同 SoC 的 SR1010）：「这块板的设备树里没有 ipsec 节点，引擎没有初始化
  （`ipsec hardware driver will not init`）」

⇒ **厂商在 6 个变体上写了这套驱动，却从未在任何一台 SK-D840N 上启用它。**

### 2.4 这板子的 CPU 本身没有 AES 扩展

`/proc/cpuinfo` Features = `fp asimd evtstrm crc32 cpuid` — **没有 `aes`，也没有 `sha2`**。
与 cnjn 手册「2 核 Cortex-A53；有 NEON 和 CRC32，**没有** AES／SHA 扩展」一致。

⇒ 板上 `/proc/crypto` 里 AES 的 `driver: aes-lib`（纯软件）是**正确**的，
不是配置缺失。硬件加密只能靠这个外设。

**注意一个易误判点**：内核符号表里存在 `__aes_ce_encrypt` / `__aes_ce_decrypt` /
`__aes_ce_sub` / `__aes_ce_invert`。这是 arm64 crypto extension 的**编译期符号**，
运行时探测判定该核没有 AES 扩展 ⇒ 不注册 `ce` provider。**符号存在 ≠ 硬件支持。**
别把它当成「有硬件 AES」的证据。

### 2.5 现状快照（板子）

- 7.3.4 已刷入 flash 并验收通过（dtb/kernel/root 三段 CRC32 逐一比对一致）
- `uname -a` = `Linux ImmortalWrt 7.3.4 ... aarch64 GNU/Linux`
- `/proc/crypto`：无任何 `ce`/`simd`/`hw`/`npu` 提供者
- 内核 NPU 符号只有 `npu_qos_show` / `npu_qos_store`
- `zx-sec` 符号 0 个；设备树里无 `sec_module` / `ipsec` 节点
- `/dev/mem` 存在但 `CONFIG_STRICT_DEVMEM` 生效，读任何未映射物理地址都返回 `Bad address`
  ⇒ **板上无法直接读 ipsec 寄存器**，验证必须回 U-Boot（`md`）或加内核打印

### 2.6 步骤 0 实测：芯片型号已确认（2026-11-28）

**结论：本板属 ZX279133 家族，efuse 变体字段 = 0，在厂商白名单内 ⇒ 这条路可以继续。**

下面是三条独立证据互相印证，不是推测。

#### (1) 厂商模块里的判定算法（`np_133.ko` 反汇编：`get_sub_cputype`，va `0x128d5c`，size 200）

```
mov  w2, #4                      ; 长度
mov  w1, #0x994                  ; efuse 位偏移
bl   zxic_efuse_read             ; zxic_efuse_read(buf=sp+0x20, 0x994, 4)
ldr  w19, [x29, #0x20]           ; w19 = *(u32*)buf  ← 变体字段
cmp  w19, #4
b.hi <unsupported>               ; >4 ⇒ printk("wrong chip type, please check")，返回 6
ldrb w0, [table, w19]            ; table = .rodata+0x14eb4 = [2, 17, 0, 8, 10]
add  x0, x1, w0, sxtb #2         ; 跳转 = 0x128db4 + sxtb(table[w19]) * 4
br   x0
```

| efuse 字段 | table | 跳转落点 | sub_cputype | 型号 |
|---|---|---|---|---|
| 0 | 2 | `0x128dbc` | `csel w19,w19,#3,ne`（PCIe 链路决定 0 或 3） | ZX279133 / ZX279133N |
| 1 | 17 | `0x128df8` | 4 | ZX279133R_A |
| 2 | 0 | `0x128db4` | 1 | ZX279133E |
| 3 | 8 | `0x128dd4` | 5 | ZX279133R_B |
| 4 | 10 | `0x128ddc` | 2 | ZX279133G |
| ≥5 | — | `0x128de4` | 6 | **不支持**（`wrong chip type, please check`） |

枚举值取自 `.rodata` 里的 `sub_cputype_*` 四个字节常量：
`ZX279133=0`(`+0x14ed0`)、`E=1`(`+0x14ecc`)、`G=2`(`+0x14ec8`)、`N=3`(`+0x14ec4`)、
`R_A=4`(`+0x14ec0`)、`R_B=5`(`+0x14ebc`)。

#### (2) 厂商 U-Boot 读的是同一个字段（离线反汇编 `device-dumps/sk-d840n_b1.bin`，file `0x5bbc4`）

```
mov  x0, #0x280 ; movk x0, #0x10e0, lsl #16   ; x0 = 0x10e00280
ldr  w0, [x0]                                  ; 芯片 ID
...
mov  x1, #0x104c ; movk x1, #0x14f1, lsl #16   ; x1 = 0x14f1104c
ldr  w1, [x1]
ubfx x1, x1, #0x14, #4                        ; 变体字段 = bit 23..20
cmp  w0, #0x4425D ; ccmp w1, #2 ; csel         ; 字段 == 2 ⇒ 返回 0x4425DE（E 型）
```

**`0x14f1104c` 的 bit 20，正好等于 efuse 数组里的位偏移 `0x4c * 32 + 20 = 0x994`** ——
和内核驱动 `zxic_efuse_read(buf, 0x994, 4)` 是同一个字段，两边独立对上。

交叉验证：厂商 U-Boot 规定「字段 == 2 ⇒ ZX279133E」，
内核驱动的表也规定「字段 == 2 ⇒ sub_cputype 1 = ZX279133E」。**同一语义，两套代码。**

#### (3) 板上实测（物理断电回 U-Boot，用 `md` 读，不动 flash）

```
md 0x10e00280 0x8   →  10e00280: 0004425d 00000000 ...   ← 芯片 ID = 0x4425D = 279133
md 0x14f1104c 0x4   →  14f1104c: 00000074 00000000 ...   ← bit 23..20 = 0 ⇒ 变体字段 = 0
md 0x14f11980 0x10  →  14f11980: 00000001，其余到 0x14f119ff 全 0   ← 字节偏移口径下 0x14f11994 也是 0
```

三种口径读出来的都是 0：

| 口径 | 地址 | 实测值 |
|---|---|---|
| efuse 位偏移 `0x994`（内核驱动用的口径） | `0x14f1104c` bit 23..20 | **0** |
| efuse 字节偏移 `0x994` | `0x14f11994` | **0** |
| 厂商 U-Boot 的 `ubfx` 字段 | `0x14f1104c` bit 23..20 | **0** |

⇒ `w19 = 0 ≤ 4` ⇒ **不进「wrong chip type」分支** ⇒ 白名单通过。

厂商 U-Boot 的启动横幅独立印证（`device-dumps-2025/serial-boot-20261110-151637-403184.bin`）：

```
CPU  : ZX279133@A53x2,      ← 不是 ZX279133E（E 型要求字段 == 2）
Model: ZTE 133
```

#### 还没定死的一点点（不影响结论）

字段 0 走 PCIe 链路分支，`sub_cputype` 是 0（ZX279133）还是 3（ZX279133N）
取决于 `check_pcie_link_status()` 的返回值 —— 该函数读 `*(0x15200000 + 0x1100) & 1`
（`np_133.ko` va `0x10ae90`）。**两个值都在白名单内，不影响「支持不支持」**，
但会影响步骤 3 里选哪套寄存器参数。想定死：回 U-Boot `md 0x15201100 0x1` 看 bit0；
若该地址在社区 U-Boot 里没映射（`md` 会 Data Abort），改从有映射的内核态读。
7.3.4 里 `/sys/bus/pci/devices` 不存在（内核没有 PCIe 总线），
`/proc/iomem` 里 `0x15200000` 也没有驱动占着。

#### 顺带记下的坑

- **Linux 侧读不到这个字段**。7.3.4 的 efuse nvmem 设备是 `zx279133-efuse`，
  但它只暴露 `14f11000.efuse:nvmem-layout` 布局目录、没有 `nvmem` 文件
  ⇒ **型号判定只能在 U-Boot 里做**。
- **厂商模块里 `zxic_efuse_read` 是 UND 符号**，实现不在本仓库，
  本仓库也没有厂商 6.18 内核源码 ⇒ 厂商 efuse 驱动的真实语义无法直接读源码确认。
- 判定算法在 `np_133.ko` 里是**间接调用**（函数指针表），没有直接调用者；
  要追调用链用 `tools/ponelf.py xref` + `docs/11` 的寄存器访问点跟踪器，**别手写反汇编**。

---

## 3. 执行计划

### 步骤 0 ✅ 已完成（2026-11-28）— 芯片型号已确认在白名单内

实测过程、读数、判定链全在 §2.6。结论：

- 芯片 ID = `0x4425D`（279133），efuse 变体字段 = **0** ⇒ `w19 ≤ 4` ⇒ **通过白名单**
- 厂商 U-Boot 横幅独立印证：`CPU : ZX279133@A53x2`

**门槛 1 解除，可以往下走步骤 1–5。** 但请先读 §5 的成本收益判断 ——
「技术上能做」不等于「值得做」：剩下的工作量在时钟、保留区、引擎驱动三块，
而厂商自己在 6 个变体上都没启用过它。

### 步骤 1 — 时钟（补丁，挂在 `kernel-patches/7.3/`）

两条路，选一：

- **A（推荐）**：给 `include/dt-bindings/clock/zte,zx279133-topcrm.h` 加
  `ZX279133_TOPCRM_CLK_IPSEC_WCLK` / `_IPSEC_ACLK` 两个 ID，
  在 `drivers/clk/zte/zx279133-topcrm.c` 里实现 gate（参考已有的 `top_clk_gate2` 各位的写法），
  让 DTS 能正常写 `clocks = <&topcrm ...>`。
- **B**：不碰时钟驱动，驱动里直接 `writel` TOPCRM 的 `0x10e10038` 对应 bit。
  厂商就是这么干的（字符串证据见 2.3 门槛 2）。A 更干净，B 更省事但绕过时钟框架。

编号建议：`470-clk-zx279133-ipsec.patch`（接在 460 之后）。

### 步骤 2 — DTS 节点 + 保留区对齐

在 `430-arm64-dts-zx279133-sr1010.patch` 里（或新增 `480-arm64-dts-zx279133-ipsec.patch`）：

1. 新增 `ipsec@12000000`：compatible `zte,zx279133-ipsec`，reg 两段，12 条 interrupts，
   `memory-region` 指向保留区，`status = "okay"`
2. **解决保留区冲突**：现在 `se_hash_reserved` 占 `0x9e100000/16MiB`，
   而厂商加密引擎用的是 `ipsec_ddr@90000000/2MB`。
   两条路：(a) 把加密引擎的 2 MB 单独划出来；(b) 复用 `se_hash_reserved` 那 16 MiB。
   **无论选哪条，都要写清楚依据** —— 手册和 cnjn 的 DTS 对不上，不能默认任一方对。

### 步骤 3 — 引擎驱动（最大的一块）

建议**新起一个模块**（如 `drivers/crypto/zx279133-crypt/`），
而不是塞进现有的 `npu` 模块 —— npu 模块已经有 7 个补丁（320–370）在动它，
混在一起会让 review 和回滚都变难。

工作方法（**不要手写反汇编**）：

1. 用 `tools/ponelf.py` 读 `np_133.ko` 的 symtab + rela，
   拿到 `zx_ipsec_top_init` / `ipsec_init` / `se_init` / `ipsec_driver_register`
   各自引用了哪些字符串和哪些别的函数 ⇒ 得到**初始化调用图**
2. 用 `docs/11` 里那套寄存器访问点跟踪器（`staging/npidmsites.py` 等，
   按 `docs/11` §4b/§4e 的用法）对上面每个函数跑，
   得到**基址 + 偏移 + 写值**的表
3. 逐个寄存器在 `ipsec@12000000` / `0x13000000` 两个窗口里找落点，
   写出 `zx279133-crypt.h` 的寄存器定义
4. crypto API 层按 2.2 列的厂商符号**照着写**（名字、语义、错误路径都能对上）

### 步骤 4 — 门禁与接线（仓库现有机制，别自创）

- 新补丁登记进 `kernel-patches/7.3/MANIFEST.json`（有 `file` / `sha256` / `bytes` 三字段，
  必须重算 sha256）
- 用 `tools/kcheck.py --tree TREE --config FILE --file REL_PATH` 做宿主 clang 语法预检
  （本机没有 aarch64 交叉工具链，这是唯一的快速反馈通道）
- 7.3 的 CI 工作流是 `.github/workflows/build-sk-d840n-73.yml`
- 门禁面检查：新增文件如果不在任何 `paths:` 过滤器里，CI 不会跑 —— 仓库已有教训
  （见 `docs/101` 关于 `paths:` 的记述）

### 步骤 5 — 实机验收

1. **先回 U-Boot 验证寄存器可读**：加 `dev_info` 或调试 print，
   确认 `ipsec@12000000` 窗口能读出非全 0 的值（厂商的 `ipsec_init_test` /
   `crypt_irqcb_test` 是现成的自测参照）
2. **注册 crypto provider**：`/proc/crypto` 里应出现带硬件 driver 名的条目
3. **正确性**：用 `crypto_hash` / `openssl` 或内核自带 `crypto` selftest
   交叉比对硬件与软件结果（厂商的 `====== ecb(aes) test =======:` 就是它的自测，
   我们的实现应当有等价物）
4. **中断**：确认 12 条 SPI 中实际用到的那几条能触发（`/proc/interrupts`）

---

## 4. 必须避开的坑

1. **串口命令必须一条一条发**。上一轮刷机时两条命令粘成一行（换行被吞），
   导致 `0x8a000000` 和 `crc32` 拼成 `0x8a000000crc32`，
   U-Boot 按十六进制解析成 `0x8a000000c` ⇒ 越界访问 ⇒ Synchronous Abort。
   **每次 `console_send` 之后要确认输出尾部回到了 `=>` 提示符再发下一条。**
   （本轮已验证过的做法：`expect` 只用来等结果关键字，发完用 `serial_tail` 轮询提示符。）
2. **7.3 的 `reboot` 不复位板子**。任何要回 U-Boot 的动作必须**物理断电**。
3. **刷机前删 `staging/ctl/u.stop`**。
4. **回滚材料在板外**：`staging/{dtb,kernel,root}_stock.bin`（厂商 6.18 原始内容）。
   板上的 root 是新刷的 7.3.4，旧内容**无法从板上导出**。
5. **不要碰 flash 里的 `boot`（社区 U-Boot + saved env）和 `parameter tags`（GPON 身份）**。
6. **别把 `__aes_ce_*` 符号当成硬件 AES 的证据**（见 2.4）。
7. **别把加密引擎当成 NPU 的一部分**。它在 `ipsec@12000000`，独立于 `nppt@19000000`。
8. `grep`/`ripgrep` 在含控制字节的文件上会静默返回空 ⇒ 扫 `.ko` 用 python 正则，
   而且要过滤噪声（`des` 会命中 `desc`；厂商模块里 "des" 命中 997 条，绝大多数是噪声）。

---

## 5. 成本收益判断（给决策者看，不是给执行者看）

| | 评估 |
|---|---|
| 技术可行性 | ✅ 可行 —— 厂商实现完整可对照，不是裸逆向 |
| 硬件确定性 | ✅ **芯片型号已确认支持**（efuse 变体字段 = 0，ZX279133 家族，见 §2.6） |
| 工作量 | ❌ 大 —— 芯片 ID + 时钟 + DTS + 保留区 + 引擎驱动 + 门禁 + 实机 |
| 收益 | ❌ 低 —— cnjn 手册自己定位「给代理核心（DPI）用时再做」，优先级排在 PON/2.5G/WiFi 之后 |
| 现实用途 | ⚠️ CPU 无 AES 扩展，硬件加密唯一价值场景是 IPsec 隧道 / DPI 加密特征库，**都不是这台光猫的用途** |

**建议**：步骤 0 已做完，答案是「支持」，但**收益依然是低**。
真正的成本在步骤 1–5（时钟 + DTS/保留区 + 引擎驱动 + 门禁 + 实机），
而且厂商自己在 6 个变体上都没启用过它。若真实目标是「让这台光猫更快」，
加密引擎不在这条关键路径上。

**如果真实目标是「让 2.5G 口跑满」，瓶颈不在加密引擎而在 CPU** ——
更该先查 NPU 转发路径的完成度（`docs/95`：8 个 NPU 核一个也没在跑转发，
缺三样：引擎分派表没按 flow 编程、PPS 两条中断没申请、用户态没有 `tc`）。

---

## 6. 相关文档索引

| 文档 | 用途 |
|---|---|
| `docs/95-the-npu-is-there-but-nothing-drives-it-20261031.md` | NPU 现状；`se`/`woe`/`bmu` 只有预留区无驱动 |
| `docs/11-vendor-linux-bringup-transcript-20261020.md` | **逆向方法论**：寄存器访问点跟踪器的用法 |
| `docs/03-stock-uboot-recovery-probe.md` | efuse chip id 读法（0x14f11040 / 0x14f11080） |
| `docs/07-sk-d840n-status-20261014.md` | 厂商 6.18 世代的全量事实；缺失驱动清单 |
| `docs/101-the-7-3-4-baseline-and-the-migration-surface-20261122.md` | 7.3 迁移面与门禁 |
| `pon/INVENTORY.md` | 厂商模块分工（np_133 是 SoC 平台驱动，不是 PON 驱动） |
| `pon/abi-scan.txt` | 各厂商模块的 compatible 与 ioctl 字符串清单 |
| `tools/kcheck.py` | 宿主 clang 语法预检（无交叉工具链时的唯一快速反馈） |
| `tools/ponelf.py` / `tools/pondec.py` | 厂商 .ko 的 ELF 交叉引用与反汇编 |

外部参考：
- cnjn 手册：<https://github.com/cnjn/zx279133_npu>（`NPU_HANDBOOK.md`、`NPU_MCODE.md`、`NPU_ZX133_MAP.md`）
- cnjn 7.3 内核树：<https://github.com/cnjn/linux-mainline-zte-zxslc-sr1010>（分支 `codex/sr1010-mainline`）
- 上游基线：<https://github.com/cnjn/immortalwrt@31e2ac4>

---

## 7. 当前工作区状态（交接时）

- `ci-build-repo`：tip `e3cee924`，**除本文件外工作区干净**（本文件未提交）
- 板子：跑着 7.3.4（步骤 0 期间物理断电回过 U-Boot，已用 `run bootcmd` 正常起回来；
  这套社区 U-Boot **没有 `boot` 命令**，只能用 `run bootcmd`）
- `staging/trial/`：7.3.4 三件套 + 三个 pad 文件 + sha256sums
  + 本轮反汇编脚本：`ubdis.py`（厂商 U-Boot 反汇编）、`subcputype.py`（型号判定函数）、
    `enum.py`（枚举与映射表）、`msgscan.py`（提示字符串）、`pcie.py`（PCIe 链路判定函数）
- `staging/{dtb,kernel,root}_stock.bin`：回滚用
- 本轮**未改仓库代码、未改 CI、未发布 release**，只改了这一个文档
