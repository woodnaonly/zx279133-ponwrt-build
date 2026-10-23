# The second prepare resets the NPPT, and the 9132 lives in it

**Date:** 2026-10-23
**Board:** Skyworth SK-D840N (ZX279133 SoC, ZTE 9132 exchange engine)
**Kernels:** run 133 (`3b07706`) is the kernel every measurement below comes from;
run 134 (`2088342`) and run 135 are the two fixes.

---

## 1. What was measured

The board's own diagnostic dump is printed at the end of the driver's probe. It
reads the RED's configuration word (`NPPT + 0x20004`) as the vendor's `0x9e`.

Thirty-four seconds later - long after the network is up, and long after the
PC started pinging - the same word reads `0x7e`. The only writer of `0x7e` in
the whole tree is `zx279133_red_prepare()`, inside `zx279133_tm_prepare()`.

So a **second** `zx279133_tm_prepare()` ran after the probe's own one. The
probe's own prepare is the one that printed the 9132's whole set-up:

```
[    0.539169] zx279133-eth: 9132 PPU 1 attribute word 96040001 (vendor configuration)
[    0.544402] zx279133-eth: 9132 flood groups of PPU 1..4 point at the CPU port 15
[    0.546913] zx279133-eth: 9132 switch ports 1..7 have the protocol action table at ERAM block 19624 (ARP trapped to the CPU)
[    0.546948] zx279133-eth: 9132 outport map: ERAM table 0x27 at block 24071 holds the vendor's 64 entries
[    0.547096] zx279133-eth: 9132 reason map: ERAM table 0x19 at block 19752 holds the vendor's 254 entries
[    0.547107] zx279133-eth: 9132 CPU path: base queue 403, outport 15 -> real port 15 queue 403, reason 0 -> queue 7
[    0.547265] zx279133-eth: 9132 isolation matrix written: the diagonal only, 64 ports
```

**Those lines appear exactly once in the whole boot log.** `zx279133_np_prepare()`
- the function that prints them - did not run a second time. Run 133's
`zx279133_hardware_prepare()` is:

```c
	ret = zx279133_tm_prepare(eth);
	if (ret)
		goto err_clk_disable;          /* <-- np_prepare() is never reached */
	zx279133_bmu_threshold_fixup(eth);
	zx279133_red_vendor_fixup(eth);
	zx279133_tm_vendor_fixup(eth);
	ret = zx279133_np_prepare(eth);
```

so the second prepare **failed inside `zx279133_tm_prepare()`**, and everything
after it - the vendor's RED and QMG words, and the 9132's own configuration -
was skipped.

## 2. Why that kills the LAN

`zx279133_tm_prepare()` starts with `zx279133_np_reset_prepare()`, and that
function resets the whole NPPT:

```c
	value = readl(eth->base + ZX279133_SYS_SOFT_RESET);
	value &= ~ZX279133_SYS_SDET_RESET_N;     /* assert the SDET reset  */
	writel(value, eth->base + ZX279133_SYS_SOFT_RESET);
	...
	value &= ~ZX279133_SYS_PON_RESET_N;      /* assert the PON reset   */
	...
```

The NPPT is the block the 9132's configuration lives in: its protocol action
table, the outport map, the reason map, the isolation matrix, the trap's own
words, and the four port MACs that the switch driver programs. The reset wipes
all of it, and `zx279133_np_reset_restore()` does not put it back - it only
restores two SIPC words.

So the sequence on run 133 is:

1. probe: prepare -> the 9132 is configured, the port MACs are programmed;
2. the datapath is suspended and resumed (the RSS and the XSK paths ask for
   it), which unprepares and prepares the hardware again;
3. the second prepare resets the NPPT - the 9132 is now unconfigured - and then
   fails;
4. nothing ever writes the 9132's tables again.

From that moment the switch is a switch with no configuration: no frame from a
jack is forwarded anywhere, which is exactly what the bench saw (the board's
neighbour table never learned the PC, `lan-cpu0` stayed at 36 received packets,
and no counter in the chip moved).

## 3. Which step failed, and why the log does not say

`zx279133_tm_prepare()`'s order, from the tree's `zx279133-tm.c`:

| step | fails with | prints? |
|---|---|---|
| `zx279133_np_reset_prepare()` | `-ETIMEDOUT` | yes, `dev_err_probe()` |
| `zx279133_qmg_prepare()` | `-ETIMEDOUT` | yes, `dev_err()` |
| `zx279133_red_prepare()` | - | - |
| `zx279133_uopc_prepare()` | `-ETIMEDOUT` | yes, `dev_err_probe()` |
| `zx279133_se_frontend_prepare()` | `-ETIMEDOUT` | yes, `dev_err_probe()` |
| `zx279133_se_hash_prepare()` | `-EINVAL` | **no** |

The whole 73 MB console log of run 133 contains **none** of those messages
("UOPC tcont", "SE parser RAM", "SE SMMU0 RAM", "QMG RAM initialization" and
"initialization timed out" all occur zero times). `zx279133_se_hash_prepare()`
is the only step that can fail without saying so:

```c
	if (eth->se_hash_size < ZX279133_SE_HASH_REQUIRED_SIZE)
		return -EINVAL;
```

but the driver's probe refuses to start when that is true
(`"SE hash reserved memory is too small"`), and the board's device tree
reserves exactly `0x1000000` bytes - the required size - so that check
cannot be the one that fired.

**Conclusion: the failing step was not identified.** Whatever it was, it is
silent in this kernel, and that is itself the defect worth fixing: the tree's
messages go through `dev_err_probe()`, which the board's console never shows
for a probe that is not deferred. Run 135 replaces the tree's
`zx279133-tm.c` with an overlay copy that says which step failed, in plain
`dev_err()`, and gives the silent `-EINVAL` a message of its own.

## 4. The two fixes

### Run 134 (`2088342`) - the 9132 is programmed even when the traffic manager fails

`zx279133_hardware_prepare()` no longer gives up when
`zx279133_tm_prepare()` fails. It warns and carries on, so the vendor's RED
and QMG words and `zx279133_np_prepare()` - the 9132's own configuration -
are written anyway.

### Run 135 - the NPPT is reset once, not twice

The reset is only needed to bring the NPPT up from whatever the bootloader
left in it. Once it is up and the 9132 has been configured in it, a second
reset throws that configuration away and the switch driver - which programs
the four port MACs - does not run a second time.

Run 135 therefore shadows the tree's `zx279133-tm.c` with an overlay copy in
which `zx279133_np_reset_prepare()` skips the reset once
`eth->nppt_ready` is set, and publishes only the two SIPC words it owns. The
driver sets that flag after the first successful `zx279133_np_prepare()`.

## 5. The debugfs console could crash the board

Round 133 read `NPPT + 0x40002` - a two-byte offset - and the board died:

```
[   24.379156] Unable to handle kernel paging request at virtual address ffff800085040002
[   24.379214] FSC = 0x21: alignment fault
[   24.467828] pc : readl+0x0/0x20
[   24.468272] lr : zx279133_9132_write+0x608/0x1490
[   24.623054] Kernel panic - not syncing: Oops: Fatal exception
```

The NPPT window is device memory: a four-byte access at an address that is not
four-byte aligned is an alignment fault, and it takes the kernel with it. A
debugfs command that can panic the running board is not a debugfs command.

Run 135 refuses an unaligned offset, and refuses a dump that would run past
the end of the window.

## 6. What was measured about the port MACs

The switch driver programs the four port MACs of the 9132 at probe:

```
[    1.665911] switch: SMAC 0 programmed: an_ctrl=0x00fa2203 pkt_filter=0x80000001 speed=0x00011000
```

and the header has the reset value next to the wanted one:

```c
#define ZX279133_SMAC_AN_CTRL_VALUE       0x00fa2203
#define ZX279133_SMAC_AN_CTRL_RESET_VALUE  0x00ba2203
```

Reading `NPPT + 0x40000` back at 24 seconds into the boot returns `0x00fa2203`
- the programmed value, not the reset value. Round 134 reads it again after
the second prepare, to settle whether the port MACs survive the reset.

## 7. The files

| file | what |
|---|---|
| `kernel-overlays/drivers/net/ethernet/zte/zx279133-tm.c` | new: the tree's traffic-manager file, with the reset skipped on a re-prepare and every failing step named |
| `kernel-overlays/drivers/net/ethernet/zte/zx279133.h` | `eth->nppt_ready` |
| `kernel-overlays/drivers/net/ethernet/zte/zx279133-main.c` | sets `nppt_ready`; the prepare no longer gives up when the traffic manager fails |
| `kernel-overlays/drivers/net/ethernet/zte/zx279133-np.c` | the debugfs register console refuses an unaligned offset |
