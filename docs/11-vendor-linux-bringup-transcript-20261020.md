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

## 5. Next round, in priority order

1. **`tools/v419spec.sh`** - RAM-boot `staging/v419-uImage` + `v419-dtb.bin` (proven procedure from
   task #26) and dump `md 0x19280000 0x20` plus the `+0x40..+0x50` band, the `+0x100..+0x124` band and
   the two ring bases *under the vendor kernel while it is carrying traffic*, with `tools/pcrxwatch.ps1`
   as the traffic instrument. This replaces the U-Boot-derived spec with the Linux-derived one; every
   register value mainline copies should come from that table.
2. Read run 75 as a *mechanical* verdict only: did `+0x0c/+0x10c/+0x110/+0x124` reach the hardware.
   Its traffic result cannot settle the mask question, because it does not touch those four words.
3. `brdev_set.port_id 0` for eth0 in the same vendor boot is the first measured answer for the egress
   binding on the Linux side; #37's outport sweep was done against the U-Boot model.

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
