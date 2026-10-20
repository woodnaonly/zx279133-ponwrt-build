# 11 - The vendor 4.19 Linux bring-up transcript was already on disk, and it refutes docs/10

Date: 2026-10-20, found while CI run 75 was still compiling.

## 0. What happened

Since task #26 (RAM-boot of `staging/v419-uImage` + `staging/v419-dtb.bin`), `staging/ctl/serial.log`
has contained the **vendor 4.19 kernel's own np_133.ko bring-up, printed on this board** - including
every `idm_*` value the working Linux datapath uses. Nobody had read it: `grep -rl "idm net init ok"
docs/ ci-build-repo/docs/` returns nothing, and docs/09 + docs/10 therefore built the entire reference
spec out of the **U-Boot** image instead. Identification of the transcript (not assumed): `Linux
version 4.19` appears at file offset 0x372d5, 0x8f00 bytes before the quoted lines; the module load
line directly above reads `init_module: umod=..., len=5208496` = np_133.ko's exact size, and its
vermagic complaint `magic '4.19.136 SMP mod_unload aarch64' should be '4.19.136+ ...'` is this
board's 4.19.136+. It is followed at [27.84 s] by `ethdrv_dev_ioctl, brdev_set.port_id 0,
brdev_set.name eth0, brdev_set.flag 0`.

## 1. The two lines that close task #43

```
[   15.993777] zx_pon_irq_base_of_init: idm_ddr_phy_start 0x80b23000, idm_ddr_size 0xee5000
[   18.639872] 1 idm_ddr_base is 000000008be7859e, phy addr is 0x80b23000
[   18.646611] ddr size(0xee5000), idm used(0xee5000)
```

The working Linux NP driver hands the NP a **plain CPU DRAM** address, `0x80b23000`, 0xee5000 =
15.9 MiB, and uses all of it. U-Boot's `idm_ddr_base is 0x00000000b0000000` is the *same variable* in
the bootloader, where the pool happens to sit in the 0xb0000000 region.

**Verdict on #43: the NP can and does master into CPU DRAM.** docs/10's thesis - "rings and RX buffers
must be allocated inside 0xb0000000 because that is the only memory the NP can reach" - is refuted, so
task #39 must NOT be implemented as written there. What `tools/npspace.sh` proved (0xb0000000 is not a
`+0x30000000` alias of the CPU's DRAM) still stands; what was wrong is the inference drawn from it,
namely that the NP therefore cannot reach DRAM. The vendor path also explains why run 68's
`idm+0xb0` completion word advanced while the ring bases held ordinary `dma_addrs`: that was not an
anomaly to be explained away, it was the vendor's own arrangement.

## 2. Second, independent proof from the binary (offline, reproducible)

Two scripts written this session, both with a positive control because "0 hits" is the most
dangerous answer this project produces:

- `python staging/npidmsites.py pon/vendor-modules/np_133.ko` ->
  170 `add Xd, Xn, #0x280, lsl #12` (= nppt + 0x280000 = the IDM block) markers, **196 IDM-relative
  accesses, 99 distinct offsets**, control store at 0x11dd8 = `str w5, [x0, #0xa4]` present.
- `python staging/npiddrbases.py pon/vendor-modules/np_133.ko` -> throws the register tracker away:
  **29 functions** touch the IDM block (`idm_init`, `idm_rx_bp_init`, `idm_rev_bp_init`,
  `idm_tx_desc_init`, `idm_rx_desc_init`, `idm_net_lan_tx_direct`, ...); among all of them, stores to
  `[that base, #4]` or `[that base, #8]`, including the `stp w,w,[x,#4]` pair form: **0**. Control:
  `idm_init` is in the function list.

So **the vendor Linux driver never programs `idm+0x04`/`idm+0x08`, the TX and RX ring bases.** It
either inherits whatever the bootloader left or sets them through a path that is not an MMIO store in
this module. Mainline writes both from `dma_alloc_coherent`, so mainline is doing something the
traffic-proven Linux driver never does - that is a legitimate thing to stop doing, and it is the
cheapest remaining structural difference.

## 3. New first-ranked suspect: the four RX interrupt mask words

The transcript prints the values, and `kofn.py` on `idm_int_mask_configure_init` gives the registers:
its tail loads four consecutive words from a static array (`ldr w2,[x19,#0x758/0x75c/0x760/0x764]`,
the dump loop walks `x21 = 0..3` with `x22 = x19 + 0x758`) and stores them to

| reg | vendor 4.19, traffic-proven | mainline (`zx279133-idm.c:1423-1428`) |
|---|---|---|
| `idm+0x44` | `0x071ffbfb` | `ZX279133_IDM_DIRECT_RX_MASK` = `0xf00f` |
| `idm+0x48` | `0x00200000` | `0x00ff0000` |
| `idm+0x4c` | `0x00400000` | `0` (comment: "no vendor buffer-release callback") |
| `idm+0x50` | `0x00800404` | `ZX279133_IDM_LOCAL_MASK` = `0x0f00` |

`[x19+0x758]` -> `+0x44`, `+0x75c` -> `+0x48`, `+0x760` -> `+0x4c`, `+0x764` -> `+0x50`, and `+0x40` is
written by `idm_net_init`/`idm_int_enable`/`idm_int_disable` instead (mainline writes `0x07ffffff`).
The four irq lines are the ones the driver requested: `idm-intr0 is 40`, `intr1 41`, `intr2 42`,
`intr3 43`.

Mainline's model of these registers is "16 low bits split into nibble groups"; the working image has
bits set at 26, 23, 22, 21, 10 and 2, i.e. a 27-bit field where the vendor spreads sources across all
four lines. `idm+0x4c = 0` in mainline while the working image holds `bit22`, and `+0x44 = 0xf00f`
against `0x071ffbfb`, are exactly the kind of divergence that produces **TX completes, RX never
interrupts, `rx_packets` 0 forever**. Caveat to keep honest: the four values are computed at runtime,
not a static table (`python` packed-byte search for the 4-word sequence: NOT FOUND), so they are
*this board's* mask configuration, correct as a measured image to reproduce and not derivable from
the .ko.

### 3b. The line -> SPI map that turns this from a diff into a mechanism

Two DTs, read together:

- vendor 4.19 (`decompiled/sk-d840n-community-4.19.dts:1931`) gives `idm@19280000`
  `interrupts = <0 1 4>, <0 2 4>, <0 3 4>, <0 4 4>` = **SPI 1,2,3,4**, and its driver prints them back
  as `idm-intr0 is 40` ... `idm-intr3 is 43`.
- mainline (`upstream-read/zx279133.dtsi:467-473`) gives the nppt node
  `interrupts = <GIC_SPI 5>, <GIC_SPI 1>, <GIC_SPI 2>, <GIC_SPI 3>, <GIC_SPI 4>` with
  `interrupt-names = "nppt", "rx", "idm", "buffer-release", "local-test"`, and `zx279133-main.c:634`
  requests **`irqs[1]` = SPI 1** for `zx279133_idm_rx_irq`.

So `+0x44` is the routing word for **the very line mainline's RX handler sits on**. The comparison
below was computed, not read off by eye (`python`: route each of bits 0..26 to the line whose word
holds it, vendor `0x071ffbfb/0x00200000/0x00400000/0x00800404` vs mainline
`0xf00f/0x00ff0000/0/0x0f00`):

| vendor RX-line (line 0) sources | bits |
|---|---|
| working image delivers to the RX line | 0,1,3,4,5,6,7,8,9,11,12,13,14,15,16,17,18,19,20,24,25,26 (22) |
| mainline delivers to the RX line | 0,1,2,3,12,13,14,15 (8) |
| **routed to no line at all by mainline** | **4,5,6,7,24,25,26** (7) |
| moved to line 3 `"local-test"` | 8,9,11 |
| moved to line 1 `"idm"` - **an IRQ this driver never requests** | 16,17,18,19,20 |
| pulled onto the RX line that the vendor keeps off it | 2 |
| `+0x4c`: vendor routes bit 22 to line 2 | mainline writes **0** there |

Fourteen of the twenty-two completion sources the working image delivers to the RX interrupt were
either stranded on an unrouted bit or sent to a line no handler owns - and five of them (16..20) to an
IRQ mainline never even requests. That is the first mechanism in this whole investigation that explains
`rx_packets` 0 *and* leaves TX completions moving, and it does not need the address-space theory at all.

**Shipped:** `zx279133-idm.c` now writes the four measured words verbatim (`0x071ffbfb`, `0x00200000`,
`0x00400000`, `0x00800404`) and keeps mainline's `+0x40 = 0x07ffffff` enable-all behaviour, so the
routing and the enable word cannot disagree about which bits exist. SPI2/SPI3 are still not requested
by this driver, and an unrequested SPI stays masked at the GIC distributor, so reproducing the vendor's
words for those two lines is faithful without being able to storm the console.

## 4. Warning about the three values shipped in run 75

`idm+0x10c` is a **bitfield**, not a count. `idm_rx_bp_init` does a read-modify-write on the live
register:

```
18f3c  ldr  w4, [x3, #0x10c]
18f40  lsr  w4, w1, #0xc          ; w1 = a size in bytes
18f44  sub  w4, w4, #1
18f48  lsl  w4, w4, #0x15         ; << 21
18f4c  orr  w4, w4, #0x100
18f50  str  w4, [x3, #0x10c]
```

For 4096 bytes that yields `0x00600100`. Run 75 writes `ZX279133_IDM_BP_RING_SIZE` = `0x1000` there
(and at `+0x110`), because `0x1000` is what the *U-Boot* image holds. Both cannot be the right encoding
for Linux, and the one that came from the Linux transcript is the one with a formula behind it. So if
run 75 boots and stays silent, do not conclude the mask theory is wrong: `+0x10c/+0x110 = 0x1000` may
itself be a regression inherited from the bootloader's image.

## 4b. The vendor Linux bring-up register image, extracted from the .ko

`python staging/npidmvals.py pon/vendor-modules/np_133.ko idm_init idm_net_init idm_tx_desc_init
idm_rx_desc_init` tracks `mov wN,#imm` / `movk` / `orr` per register and reports what each store into
the IDM block carries. Result, with mainline's value beside it:

| register | vendor Linux (literal) | mainline | U-Boot (docs/09) |
|---|---|---|---|
| `+0x054` | `0x06060606` | `0x06060606` | - |
| `+0x058` | `0x00060606` | `0x00060606` | - |
| `+0x05c` | `0x07070707` | `0x07070707` | - |
| `+0x060` | `0x07070707` | `0x07070707` | - |
| `+0x3fc` | `0x00000f49` | `0x00000f49` | - |
| `+0x040` | `0x07ffffff` (`idm_net_init`) | `0x07ffffff` | - |
| **`+0x124`** | **`0x00000080`** | `0x00000040` (since run 75) | `0x00000040` |
| `+0x000` | RUNTIME (three stores in `idm_init`, one in `idm_rx_desc_init`) | - | `0x01ff0007` |
| `+0x00c` | RUNTIME (`idm_tx_desc_init`) | `0x04000000` | `0x04000000` |
| `+0x010`, `+0x0c0`, `+0x2c8`, `+0x5c0`, `+0x070` | RUNTIME - loaded from globals or computed | 128 / `IDM_DESC_MODE` / - / 7 / depth-1 | `0x00000020` / `0` / - / - / - |

Six words the vendor writes as literals are **already exactly what mainline writes**, which is
independent confirmation that mainline's bring-up was ported faithfully wherever the value is a
constant. The one constant that disagrees is `+0x124`: the bootloader holds `0x40`, the Linux bring-up
writes `0x80` (`mov w1, #0x80` at 0x19664 immediately followed by `str w1, [x0, #0x124]` at 0x19668 -
read in the raw listing, not just inferred from the tracker). Since we are writing a Linux driver, the
Linux value is the one that ships: **run 77 changes `+0x124` from `0x40` to `0x80`.**

The remaining RUNTIME rows are the honest limit of this method - `+0x00c` and `+0x010` in particular
are computed from variables in `.data`, so their Linux values are only known from what the board prints
or from a register read, and §5 established that no register read under the vendor kernel is obtainable.

**Self-check that mattered:** the first version of `npidmvals.py` excluded `ldr` from the
register-kill list, so `ldr w1, [x1]` at 0x19688 left a stale `w1 = 0x80` live and the script reported
`+0x010` and `+0x0c0` as literals `0x80` - two invented register values, both plausible-looking, both
wrong. The corrected run reports them RUNTIME and keeps only `+0x124 = 0x80`, which the raw disassembly
confirms. Same rule as everywhere else here: an extractor's output is only usable when its control
lines up with the listing.

## 5. Next round, in priority order

**Update after the 14:28 re-run: §5-1 as written is not achievable, and the reason is worth keeping.**
`tools/vendor419.sh` RAM-booted the vendor pair again at 14:28:49 and the bring-up transcript repeated
*verbatim* - `1 idm_ddr_base is 000000004031c7e0, phy addr is 0x80b23000`,
`ddr size(0xee5000), idm used(0xee5000)`, `irq[0] sg_idm_irq_mask_conf(0x71ffbfb)`,
`idm net init ok`, `nppt init end. ret = 0` - now at [ 60.4 s ] instead of [ 18.6 s ], so it is a genuine
second boot and those runtime-computed values are stable across boots, not per-boot noise.

What did **not** happen is a shell, and that is structural rather than a timing problem: the vendor
4.19 kernel mounts the root that is actually flashed - `VFS: Mounted root (jffs2 filesystem) readonly on
device 31:5.` then `Run /sbin/init as init process` - and that root is OpenWrt/procd built for mainline.
The console filled with `open: Not a directory` from services that cannot run on 4.19, and 18 probes of
`echo V419SHELL-$((6*7))` never answered. `netdriver.ko` never loaded either (0 lines for `ethdrv_dev_
ioctl` / `brdev_set`, which the 10-19 boot had printed at [27.8 s]), because that is started by the
stock init scripts this root does not have. **So: no devmem reads under the vendor kernel, ever, on this
flash layout - the module's own dmesg is the only vendor-side evidence obtainable, and it is now
captured twice.** Anything else the vendor path does must come from the .ko (offline) or from the
transcript.

That leaves one question the transcript cannot answer, and it is the one docs/10 was written for: does
the NP **latch** the ring-base registers when its firmware loads, so that a Linux driver writing new
ring bases after `np init ok` is simply ignored? If it does, the vendor image makes sense - U-Boot's
`0xb0008000 / 0xb0028000` stay authoritative, np_133.ko never touches them (§2), and the DRAM pool at
`0x80b23000` is handed over through the buffer-table registers it *does* write (`+0x104/0x108/0x10c/
0x110`, `idm_rx_bp_init`) - and it also explains run 68's `idm+0xb0` completion word advancing while
mainline's DRAM ring bases were never consumed. This is now the top remaining suspect, and unlike the
reachability theory it is testable in one mainline boot: a kit that **does not write `+0x04/+0x08`** and
instead drives the rings U-Boot left in the np-pool window (mainline already ioremaps that window with
`devm_ioremap_wc` + `memcpy_toio` for TX payload, so the machinery exists).

Order from here:

1. Boot the run-76 kit (`tools/queuekit.sh 76`, now also reading `+0x44..0x4c` and
   `/proc/interrupts` before and after the traffic) - this tests the routing fix, and the IRQ delta
   separates "line never asserts" from "handler runs, ring yields nothing".
2. If the IRQ counter moves but `rx_packets` stays 0, the fault is downstream of routing and the
   ring-latch kit is the next one.
3. `brdev_set.port_id 0` for eth0 in the 10-19 vendor boot is still the only measured answer for the
   Linux-side egress binding; #37's outport sweep was done against the U-Boot model.

## 6. Instrument notes (so these numbers can be re-taken)

- `staging/npidmsites.py`'s first two attempts reported **0 sites** and its own control said ABSENT;
  the bug was that the store pattern was anchored on the mnemonic while `ins.op_str` carries only the
  operands. A negative from a scanner whose positive control fails is not a finding - this is the same
  class of error as `tools/portattr.sh`'s `data =0` reads.
- `staging/koxref.py` (RELA-based global xref) reports `RELA sites in .rela.text: 0` for
  `idm_ddr_base`, `idm_ddr_phy_start` and `idm_ddr_size` even though all three exist as `.bss`
  globals (0x77e0 / 0xd8 / 0xd0, 8 bytes each). Reason: references to file-scope statics are emitted
  against the **section symbol**, with the variable's offset carried in the addend, so matching on the
  variable's own symbol index finds nothing. **Its "0 sites" must never be read as "unused".**
