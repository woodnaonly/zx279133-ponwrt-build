# ZX279133 IPsec port: bring-up notes

Status: `ecb-aes-zx279133` works on the SK-D840N 7.3.4 live kernel through
the opt-in RAM module. AES-128/192/256, SPI 174 and the controlled reset
experiment were measured on hardware. The built-in path remains opt-in;
normal images keep the driver, device and DDR reservation disabled.

## Source of the wiring

- The factory DT's `top_clk_gate2` uses parallel arrays. Aligning
  `clock-output-names` with `zx-clock,bit-shift` identifies `ipsec_wclk`
  as bit 17 (parent `cci_aclk`) and `ipsec_aclk` as bit 19 (parent
  `sys_aclk`). The bits are at TOPCRM offset `0x38`, not bits 10/11.
  `low_power_api_ipsec_clk_reset` independently reads and clears exactly
  those two bits. It also clears TOPCRM `0x60` bit 13 in a low-power
  routine; that by itself is not evidence for a safe cold-start reset
  sequence. `zx_ipsec_reset` in this binary is a no-op.
- The module platform driver object is `.data+0x230`; its probe is
  `.text+0x48c`. The stores in `.text+0x518`, `+0x548` and `+0x554` and
  their destination object symbols identify the base addresses below.
- The stock DT phandle identifies `0x90000000..0x901fffff` as a separate
  2 MiB IPsec area. It lies inside SK-D840N DRAM and does not overlap any
  reservation in the SK-D840N DTS. The NPPT `se-hash` phandle still names
  the original `0x9e100000..0x9f0fffff` region. The new reservation is
  disabled along with the device, so it does not reserve RAM in the
  normal image. Use neither region for IPsec DMA until the design is
  checked on hardware.
- The new patches belong to `kernel-patches/7.3-local/`: the build lock
  deliberately keeps `kernel-patches/7.3/` byte-for-byte upstream.

| Vendor symbol | Meaning |
| --- | --- |
| `ipsec_bttl_base` | Resource 0, `0x12000000` |
| `ipsec_base` | Resource 0 + `0x400000`, `0x12400000` |
| `trng_base` | Resource 1, `0x13000000`; not a second descriptor queue |
| `g_ipsec_irq` | Ten entries; `register_ipsec_int` requests indices `0..9` and passes the index to the common handler at `.text+0x130818` |

The stock DT has **11** entries, SPI 174..184 (`0xae..0xb8`). The probe
calls `of_irq_get(node, i)` for `i = 0..9` in order (`.text+0x588`), with
no remapping. The table below describes the factory handler's software
dispatch, not confirmed wiring on the board. The diagnostic does not
request any IRQs.

| DT index / SPI | Observed vendor handler at `.text+0x130818` |
| --- | --- |
| 0..3 / 174..177 | Read QC `0x180 + 4*i`, clear `0xf << (6*i)` in QC `0x10`; schedule completion work for queues 0 and 1 only |
| 4..7 / 178..181 | Read RAM status at QC `0x154 + 12*(i-4)` and then read `0x14c + 12*(i-4)` |
| 8 / 182 | Read FIFO status at QC `0x148` and then read QC `0x140` |
| 9 / 183 | Read BTLL `0x20` and dispatch QI, JS and the two EUCs |
| 10 / 184 | Not requested by this binary |

`crypt_irqcb` confirms the per-queue stride is 4 bytes for the encrypt
and decrypt count, completion FIFO and completion credit registers.
The factory IRQ names are not reliable by themselves: cross-check the
index, register access and observed IRQ on hardware. The KAT masks all
three QC enables (`0x08`, `0x0c`, `0x10`); this is not a claim that
separate BTLL error outputs are disabled.

### Watchdog recovery candidate

The handler for BTLL global error bits 2 and 3 calls `.text+0x12e768`
with EUC 1 and 2. It reads EUC status and enable at BTLL
`0x3020/0x3024` or `0x4020/0x4024`. When status bit 0 reports watchdog
overflow, it writes `1` to the corresponding `0x3010/0x4010` register
and polls it until zero with no deadline. It then writes back the status
and enable. This is an EUC-local recovery candidate, **not** proof that
pending AXI transactions, QC FIFOs or the other EUC have stopped. Do not
issue that sequence or reclaim a timed-out descriptor on the basis of
the watchdog reset alone. On KAT failure the diagnostic only reads the
BTLL global and two EUC status registers to preserve the initial evidence.

## Initialization trace

`np_init_module` calls `ipsec_init`, which calls `zx_ipsec_init_for_rest`,
then `register_ipsec_int`, then `zxic_crypt_async_init`. `se_init` is not
part of that chain: it initializes the NPPT parser, SMMU, hash tables and
statistics.

The calls below are in order within `zx_ipsec_init_for_rest`. These are
observed writes, not a recommended bring-up script. Register field names
are deliberately omitted where only the numeric operation is known.

| Function | Observed operation |
| --- | --- |
| `ipsec_pd_khb_ddr_init` | Populates software queue/context pointers from `ipsec_ddr_phy_start` with direct-map arithmetic |
| `zx_ipsec_top_init` | BTLL `+0x18 = 0x7e9b`; BTLL `+0x04` OR `0x08010000`, then read back |
| `zx_ipsec_int_init` | Engine `+0x100028/+0x10002c = 0x80`, `+0x100030/+0x100034 = 0x2710`, `+0x100008 = 0xf`, `+0x10000c = 0xffff`, `+0x100010 = 0x003cf3cf` |
| `zx_ipsec_qi_init` | BTLL `+0x1080..+0x109c = 0x44444444` at 4-byte intervals, then `+0x1040 = 0xf` |
| `zx_ipsec_wd_init` | Enables two watchdogs with count `0x3fffff`; sets BTLL `+0x3024/+0x4024 = 1` |
| `zx_ipsec_set_cpu_num` | Selects one of several queue configurations and RAM gaps from a module variable |
| `ipsec_addr_constra` | Programs four windows relative to the IPsec DDR base, spaced by `0x20000` and encoded at `>> 16` granularity |

To reproduce the static trace from the workspace root:

```powershell
python tools/ponelf.py pon/vendor-modules/np_133.ko xref '^(ipsec_init|zx_ipsec_init_for_rest|se_init)$'
llvm-objdump -dr -j .text --start-address=0x48c --stop-address=0x6a8 pon/vendor-modules/np_133.ko
llvm-objdump -dr --disassemble-symbols=low_power_api_ipsec_clk_reset,zx_ipsec_top_init,zx_ipsec_int_init,zx_ipsec_qi_init,zx_ipsec_wd_init,zx_ipsec_set_cpu_num,ipsec_addr_constra,register_ipsec_int pon/vendor-modules/np_133.ko
```

## Before enabling the device

`tools/trace-ipsec-kat.py` runs the pinned factory module's two synchronous
paths in Unicorn. Its completion is fabricated on the FIFO write: it
checks what the CPU emits, not what the accelerator does. From the
`ci-build-repo` directory, with Unicorn 2.1.4 available:

```powershell
python tools/trace-ipsec-kat.py ..\pon\vendor-modules\np_133.ko
```

| Operation | Encrypt queue 0 | Decrypt queue 0 |
| --- | --- | --- |
| Completion count | `0xe0` | `0xf0` |
| Request credit | `0x80` | `0x90` |
| Submission FIFO | `0x80000` | `0x80010` |
| Completion FIFO | `0x80020` | `0x80030` |
| Completion credit | `0xa0` | `0xb0` |

These offsets are relative to QC at `0x12500000`. The fixed recipe is
AES-128 ECB (`0x13`), with PD/KHB at DDR offsets `0`/`0x40`, key/dynamic
context at `0x80000`/`0x120000`, and source/destination bounce buffers at
`0x1f0000`/`0x1f0040`. It encodes the observed physical addresses directly
for the dedicated board reservation; the SoC's DMA translation and
completion-to-output ordering still require measurement. The on-board
KAT still submits only the two AES-128 ECB requests.

`trace-ipsec-kat.py --matrix` also exercises factory preparation for AES
ECB (`0x13`), CBC (`0x12`), and CTR (`0x11`), with 16/24/32-byte keys, two
sample lengths per mode (including 31 bytes for CTR), each direction,
and synchronous plus asynchronous queue 0/1 paths. This starts at the
factory packing functions, not its Linux scatterlist wrappers. For
these samples the KHB contains the byte key length at `+0x05` and the
actual payload length at both `+0x10` and `+0x16` (big endian); CBC and
CTR copy their 16-byte IV to the start of the dynamic context. Async
submission reads the input count at QC `0xc0`/`0xd0` (encrypt/decrypt),
then writes request credit and the FIFO. The queue stride is 4 bytes.
The factory async path checks for capacity, it does not wait for the
completion. This trace stops at submission for that path.

With the factory's two-queue setting, the first asynchronous queue 1
slot is PD `DDR+0x40000`, KHB `DDR+0x40040`, key `DDR+0x140000`, dynamic
context `DDR+0x1e0000`. `ipsec_get_next_{phy_,}apd` uses strides of 128,
320 and 64 bytes for the PD/KHB pair, key and dynamic context,
respectively; `zx_ipsec_get_depth` returns a hardware queue depth of
1024 with this setting. These are layout observations, not ownership or
recovery rules for an in-flight descriptor.

Clang's ARM64 syntax check passed against the prepared Linux 7.3.4 tree.
All 57 patches applied to pinned vanilla, and the compiled SK-D840N DTB
contains exactly the stock IRQ cells and a disabled IPsec node.

After running `kcheck` with the KAT file, compile the real ARM64 routine
and exercise its queue logic with a synthetic MMIO device:

```powershell
python tools/check-ipsec-kat.py --tree $KernelTree --gen $KcheckHeaders ..\pon\vendor-modules\np_133.ko
```

The compiled Linux encoder matched all 108 factory PD/KHB pairs, and
the trace checks the raw key and initial IV placement for each sample.
The fixed ECB diagnostic also matched its factory contexts. In each
direction the model tested normal completion, a pending input on queue 1,
stale completion, timeout, invalid count, wrong returned address and wrong
output, including whether the driver reads the completion FIFO and
returns a credit. The model supplies a known output or fault; it does not
verify that the hardware encrypts, transfers DMA or delivers interrupts.
Before initializing queues and before each descriptor write, the KAT
rejects nonzero QC input/output counts for both queues and directions.
Zero counters are a preflight check, not proof that AXI DMA is idle.

### Controlled RAM-only test

1. Manually dispatch the 7.3 workflow on the source ref with
   `mode=initramfs`, `ipsec_kat=true`, `publish_release=false`. This mode
   cannot publish or build a flash job. Its separate Actions artifact,
   `sk-d840n-73-ipsec-kat-ram-only`, contains the gated FIT, its SHA256,
   and `boot-manifest.json` with the full-transfer CRC32 and the four
   recomputed U-Boot subimage hashes. The generated build enables the diagnostic and its no-map
   reservation, forces `rdinit=/bin/sh` and `zx279133_ipsec_kat.enable=1`,
   turns off kexec, suspend and hibernation, and disables SPI NAND in the DT.
2. Verify the FIT checksum. Start from a physical cold power-on and boot
   the FIT from RAM through the U-Boot console at `0x89000000`. The
   artifact gate checks this transfer range and the decompressed kernel
   range against the IPsec reservation. Follow `docs/UBOOT-COMPAT.md`
   for non-executing `bootm start` and transfer verification, using the
   expected values from `boot-manifest.json`; send one
   command at a time and do not save the environment or write flash.
   Capture the full serial log, including `reserved mem`, `cold-boot KAT`
   and the KAT result. Do not use kexec to enter the diagnostic.
3. A pass requires matching outputs for hardware AES-128 encrypt and
   decrypt. A timeout, stale completion or wrong slot stops submission;
   do not retry on the live system. Both clocks remain enabled and the
   reserved DDR stays `no-map`, even after a successful diagnostic. Use a
   physical power cycle before running any other kernel.
4. Before registering the provider, verify interrupt routing and errors,
   and prove a method to quiesce pending DMA before reusing slots on
   timeout, reset or removal. Then cover other key lengths, modes, Crypto
   API selftests and `/proc/crypto`. The TRNG is not part of this test.

### First board run: 2026-11-28

After physical OFF/ON, the board verified the FIT kernel and DTB hashes in
both staged and full boot, as well as the RAM transfer CRC32 `802808e1`.
Linux 7.3.4 reached the diagnostic probe. It printed `BTLL[4]=0x0 QC[0]=0x0`
before initialization, then timed out on the encrypt completion with all
three reported BTLL error status values zero. It quarantined the IPsec DDR
and did not submit the decrypt request. This is not evidence that DMA was
idle or that either clock, reset or DMA address path was correct.

The serial-only kernel also passed the unrecognized bare `pd_ignore_unused`
token as an argument to `/bin/sh`, causing PID 1 to exit and panic. Use the
assignment form so kernels lacking generic power-domain support pass it as
an environment variable instead. This does not fix the hardware timeout.

### Second image and U-Boot observations: 2026-11-28

The next U-Boot session read `0x33fbf77f` at TOPCRM `0x10e10038` and
`0x00002fff` at `0x10e10060`. Both identified IPsec gate bits were set.
The latter address is the stock DT's `zte,zx296718-reset` controller;
Linux `reset-simple` models it as active-low, so bit 13 read back
deasserted. This measures the U-Boot state only, not the registers after
Linux's clock and power setup. It does not justify writing the reset bit.

The second RAM FIT (transfer CRC32 `e275c7d7`) passed staged image hash
verification. Its driver checks the already-read BTLL `0x04` bits before
configuring the queues, reports QC mode and the first address window, and
reports input and output counts on a timeout. An unlatching BTLL value
stops the diagnostic before any descriptor is submitted. A matching
readback proves only that the checked registers retained their values;
it does not prove that DMA, key fetch or AES execution took place.

The operator confirmed that this U-Boot session followed physical OFF/ON.
The full boot also verified both subimages. Linux again read zero from
BTLL `0x04` and QC `0x00`. After the BTLL control write, its readback
was still zero; the driver returned `-EIO` from initialization without
programming queue mode or writing a submission doorbell. The three
BTLL error reads were zero. This shifts the immediate investigation to
MMIO access, clock, reset or bus state before descriptor DMA. The shell
init command ran without the bare argument that caused the first panic.

### Third board run: 2026-11-28

The operator performed another physical OFF/ON. U-Boot read gate2
`0x33fbf77f`, bus divider `0x11` and reset `0x2fff`. After verified RAM
boot (transfer CRC32 `d5512a3c`), Linux read the same values before and
after obtaining both clock enables. The clock framework reported wclk
500 MHz and aclk 250 MHz; these are calculated rates, not physical clock
measurements. BTLL `0x04` and QC `0x00` still read zero. The BTLL control
write was again read back as zero, so the diagnostic stopped before
queue programming or any DMA submission. All three error reads were
zero. Linux removing the two gate enables is not consistent with these
observations. Look for independent evidence of address decoding or an
additional access prerequisite before changing reset or repeating the KAT.

### R4: reference initialization and offline gate

The original SG350H 7.3.4 FIT (SHA256
`c6e5010d4b59dbb28d94b6ead8c44d94024c912d761b93fd5186b2d1855fc046`) has
the IPsec implementation built into its gzip-compressed ARM64 Image.
Its separate initrd reaches the newc trailer and has no IPsec hardware
module. The enabled node and Crypto API registration strings do not show
whether the SG350H hardware ever passed its KAT.

Offsets below are file offsets in that decompressed Image:

| Offset | Reference behavior used for R4 |
| --- | --- |
| `0x7edb80..0x7edc14` | Gets/enables the four named clocks, then obtains the named IPsec reset. |
| `0x7e6340..0x7e6358` and `0x7e5d84..0x7e5d94` | Asserts the reset for 1,000-1,500 us, then deasserts it and waits 300-400 us. |
| `0x7edab0..0x7edabc` and `0x7e5d98..0x7e5dbc` | Separates SEC (+0x400000) from QC (+0x500000); writes 0x05000000 at SEC +0x450 and reads it back. |
| `0x7e5dcc..0x7e5e34` | Writes BTLL +0x18, then sets the BTLL +0x04 control bits. |

The factory DT corroborates TOPCRM gate2 bits 28/29 and SEC WCLK mux
bits 13:12 at TOPCRM +0x08 (25/125/172/200 MHz). R4 reads the bootloader's
selector and does not reparent or retune the clock. The reset is owned by
the existing active-low reset controller; the diagnostic samples both
states of bit 13 before proceeding. Masking and programming QC only begins
after the BTLL control bits read back. The diagnostic logs pre-submission
failures separately from failures after a doorbell.

R4 retains the factory-derived descriptor layout used by R3. The SG350H
driver programs a different QC window size, so do not mix its queue
configuration with the existing factory descriptor layout without tracing
the two together. Its cold-start reset sequence is not evidence that a
timed-out DMA transfer is safe to abandon or retry.

R4 passed the ARM64 GCC and DTB builds, 42 FIT/parser checks, the
synthetic BTLL/queue checks and 108 factory descriptor comparisons. The
gated RAM-only FIT has transfer CRC32 `08adc5e4` and SHA256
`a01fc02847be7006be5297c308c49d3535c5718b4a66df9bee2cccf3cf37b3a5`.
It has not booted on SK-D840N. Collect mux/rates, both reset values,
SEC/BTLL readback, QC configuration and, only if initialization passes,
AES outcomes in one board window. Do not repeat an unchanged image to
collect the same evidence. No provider, interrupt delivery or DMA
recovery acceptance is implied by the offline results.

### Live pre-DMA measurements and R5

The factory module confirms the auxiliary write independently of the
SG350H binary: `ipsec_init` writes 0x05000000 at `ipsec_base+0x450`
unconditionally at .text+0x130e50. The separate conditional write in
`zx_ipsec_init_for_rest` depends on `reset_flag`; it is not the cold
entry point.

A temporary module in the flashed kernel's /tmp measured mux 0x3711
(SEC selector 3, 200 MHz nominal), gate2 0x33fbf77f and reset 0x2fff.
BTLL +0x04/+0x18 and SEC +0x450 initially read zero, but QC +0x04 read
0x1f000000; all input/output counts were zero. The live init-only check
obtained the existing reset controller's line 13 exclusively through
the reset framework. Assertion/deassertion status was 1/0. The SEC
write read back as 0x5 and BTLL +0x04 **latched 0x08010000**. A second
reset cleared both values. The module unloaded and uptime remained
continuous. This established the BTLL initialization path without a
power cycle, but it did not submit DMA.

The first live QC-only build allocated a 2 MiB coherent arena at
0x9a300000, then faulted before touching QC: it derived SEC by adding
a physical offset to the separately mapped BTLL virtual address.
Linux panicked and its automatic restart and the serial reset signal
did not bring back U-Boot. **No descriptor was submitted.** R5 passes
SEC as a separate mapping and sizes the BTLL/QC mappings for the
highest offsets used by the selected mode. The model keeps SEC at an
unrelated virtual address and covers relocated coherent descriptors
and both doorbells, in addition to the fixed-reservation path.

The rebuilt module defaults to read-only. The opt-in `init_check=1
queue_check=1` path validates mode and all address windows without a
doorbell and verifies the reset state before returning the arena.
R5 passed this pre-DMA test. With `aes_check=1` its encryption matched,
but decryption returned `6be7331965d2ee7712ea43cf0108dfb6` after a valid
completion. The lower-level factory descriptor trace had bypassed
`ciper_set_key`, which builds the decryption seed. Treating the original
AES-128 key as round key 10 reproduces that exact erroneous plaintext
with software AES. Supplying the actual round key
`13111d7fe3944a17f307a78b4d2b30c5` fixed R6. R5's two completions were
drained; a separate reset-only run verified all counts zero and reset
the engine, retaining R5's original DMA allocation. R6 passed both
directions with a fresh arena, without rebooting.

### R7-R9 live acceptance

- R7 required a real queue-0 interrupt for each completion. GIC SPI 174
  reported QC status `0x2` for encrypt and `0x1` for decrypt; the
  corresponding output count was one each time. Polling cannot pass
  this check by itself.
- R8 injected cancellation immediately after the doorbell, before the
  completion was drained. The sampled input/output counts were `0/0`,
  so this does not establish the exact AXI in-flight state at reset.
  After the pulse the queues were empty and the entire DMA arena's CRC
  stayed unchanged over 50 ms. The arena at `0x9a500000` stayed pinned.
  A separate R7 run at `0x9a700000` passed the IRQ KAT again. This is not
  proof of safe reclamation after every timeout; the provider still
  stops on ambiguous completion and retains the arena.
- R9 registered `ecb-aes-zx279133` as an asynchronous `ecb(aes)` skcipher
  on Linux 7.3.4. The independent smoke module selected it by exact
  driver name and compared 96-byte fragmented encrypt and in-place
  decrypt with software AES for 128-, 192- and 256-bit keys. All passed.
  The hardware IRQ count increased from 6 to 18 during those requests.
  No reboots or flash writes were needed for R6 through R9.

The built-in path requires both `zx279133_ipsec_kat.enable=1` and
`zx279133_ipsec_kat.provider=1`, the enabled diagnostic DT, and an IRQ
KAT before provider registration. Build and gate it using `--provider`
on both `prepare` and `gate` in `tools/sk-ipsec-kat-image.py`. The final
shared-header R10 module repeated all three independent comparisons;
the interrupt count increased from 20 to 32. The live kernel did not
enable `CONFIG_CRYPTO_SELFTESTS`, so its `/proc/crypto` `selftest: passed`
field is not additional test evidence. R10 remains loaded from `/tmp`.

The built-in RAM-only FIT compiled and passed the gate, with CRC32
`2192fdca` and SHA256
`f9ae388f94ac4f7eac2fca856a9950240d43871f4b0e731f5f57b080eced8599`.
It has not been cold-booted on the board. Other modes
such as CBC, CTR, GCM, and actual ESP traffic are not implemented or
claimed as accelerated. ECB support alone does not offload IPsec ESP.
