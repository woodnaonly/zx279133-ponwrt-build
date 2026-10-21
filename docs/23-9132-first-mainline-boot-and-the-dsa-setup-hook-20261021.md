# 23 — The 9132's first mainline boot, and the DSA hook that stopped it (2026-10-21)

## What was run

| | |
|---|---|
| Build | run 98, `skd840n_initramfs_98_39517044236` (published 08:52:47) |
| Commit | `d42686c` — `fix(driver): the switch driver had no device to print through` |
| Boot | 2026-10-21 09:04:48, U-Boot RAM boot (`tftpboot` 12,083,972 B, `bootm 0x89000000`) |
| Console | `staging/ctl/serial.log`, offset 24,937,390 |
| Extract | `staging/ctl/run98-boot-evidence.txt` (114 lines) |
| Result | the kernel panicked 1.61 s in, in `dsa_register_switch()` |

This is the first time the mainline kernel has ever booted on this board
with the 9132 driver in it.  Everything the plan's G2 stage was written to
find out was found in the first second — and then the registration of the
switch as a DSA switch killed the kernel.

## What the boot proved

The lane, the PPU and the PHYs all came up, and all of it is on the wire:

```
[    0.480502] zx279133-eth 19000000.ethernet: 9132 PPU 2 word 0 (entry-pc) = 00008015
[    0.480515] zx279133-eth 19000000.ethernet: 9132 PPU 2 word 1 (pvid-pri) = 0000088f
...
[    0.481170] zx279133-eth 19000000.ethernet: 9132 PPU 1 attribute word 96040001 (VID 59)
[    0.481197] zx279133-eth 19000000.ethernet: 9132 PPU 1 PVID word 2 00ec0000
[    0.481218] zx279133-eth 19000000.ethernet: 9132 PPU 2 attribute word 96040001 (VID 60)
[    0.481346] zx279133-eth 19000000.ethernet: 9132 UNI ports 1..4 up (transport VIDs 59..62)
[    0.481866] zx279133-eth 19000000.ethernet: 9132 UNI PHY 0b: id 84b9:5032, BMCR 1140->1140, link 1
[    0.482103] zx279133-eth 19000000.ethernet: 9132 UNI PHY 0c: id 84b9:5032, BMCR 1040->1040, link 0
[    1.539693] zx279133-eth 19000000.ethernet: 9132 PPU 0 enable word 00000463
[    1.575190] zx279133-switch 19000000.ethernet:switch: lane pre-serdes: XPCS0 status=0x380b link=up XMAC link=0x0000
[    1.575490] zx279133-uni-serdes 16100000.phy: Uni SerDes 1G inherited: mode 0x1 already, image differs in 0/49 word(s), status 0x55000400/0x100019fc
[    1.575531] zx279133-switch 19000000.ethernet:switch: lane pre-pcs: XPCS0 status=0x380b link=up XMAC link=0x0000
[    1.575615] zx279133-switch 19000000.ethernet:switch: XPCS vend2 AN_CTRL = 0x0000
[    1.586745] zx279133-switch 19000000.ethernet:switch: SOPC mac 0 never became ready (0x00000000); not publishing it
[    1.608891] zx279133-switch 19000000.ethernet:switch: SOPC mac 2 never became ready (0x00000000); not publishing it
[    1.608906] zx279133-switch 19000000.ethernet:switch: SOPC send-enable published for 4 MAC(s)
[    1.608918] zx279133-switch 19000000.ethernet:switch: XMAC0 datapath enabled; XPCS0 status=0x380b link=up xmac_link=0x0000; 9132 UNI ports 0..3
```

| What the plan asked for | What the board said | Verdict |
|---|---|---|
| G2c: the lane, inherited from the bootloader | `XPCS0 status=0x380b link=up` | **PASS** — run 86 said `0x280b link=down` |
| G2c: the Uni SerDes left alone | `Uni SerDes 1G inherited: mode 0x1 already, image differs in 0/49 word(s)` | **PASS** — nothing was rewritten |
| G2c: the four SOPC MACs published | `SOPC send-enable published for 4 MAC(s)` | **PASS** (see the open item below) |
| G2b: the four internal PHYs, and which jack the PC is in | `UNI PHY 0b: ... link 1`, `0a/0c/0d: link 0` | **PASS** — the PC is in the second UNI port |
| G2a: the PPU's per-port words and PVIDs | `PPU 1..4 attribute word 96040001 (VID 59..62)`, `PVID word 2` | **PASS** — the transport VIDs are in the hardware |
| G2d: the four jacks as separate DSA ports | the kernel panicked in the registration | **NOT REACHED** |

Two things in that output are still open, and both are answers rather than
failures:

* `SOPC mac 0/1/2 never became ready` — the ready bit of three of the four
  SOPC MACs stayed 0, while mac 3's was 1.  The driver publishes the
  send-enable for all four anyway, and the lane's own status word says the
  link is up, so the datapath is enabled; what the ready bit is waiting for
  is not yet known.
* `xmac_link=0x0000` — the NPPT's XMAC link word is 0 while the PCS's is
  0x380b.  The lane was inherited exactly as the bootloader left it, and
  the bootloader moved 11 MB through it that way, so nothing was changed
  here on the strength of a guess.

`9132 UNI ports 0..3` in the last line is the old print; run 99 changed
the range to `1..4` to match the PPU numbering, and run 99 is not the
build that was booted.

## The panic

```
[    1.611796] Unable to handle kernel execute from non-executable memory at virtual address 0000000000000000
[    1.611832] Mem abort info:
[    1.611840]   ESR = 0x0000000086000004
[    1.611848]   EC = 0x21: IABT (current EL), IL = 32 bits
[    1.611911] Internal error: Oops: 0000000086000004 [#1]  SMP
[    2.439892] pc : 0x0
[    2.442078] lr : dsa_register_switch+0x904/0xde0
[    2.521457] Call trace:
[    2.523901]  0x0 (P)
[    2.528...]  of_platform_bus_create+0xfc/0x30c
[    2.531...]  of_platform_populate+0x4c/0xc4
[    2.534...]  devm_of_platform_populate+0x54/0xb4
[    2.538...]  zx279133_eth_probe+0xfac/0x10d0
[    2.656790] Kernel panic - not syncing: Oops: Fatal exception
```

`pc : 0x0` with `lr` inside `dsa_register_switch` is a call through a null
function pointer.  The tree's DSA core is in `staging/tree/net_dsa_dsa.c`,
and this is the line:

```c
static int dsa_switch_setup(struct dsa_switch *ds)
{
	...
	err = ds->ops->setup(ds);            /* net_dsa_dsa.c:644 */
```

There is no `if (ds->ops->setup)` in front of it.  The header says the hook
is optional —

```c
	/* Optional switch-wide initialization and destruction methods */
	int	(*setup)(struct dsa_switch *ds);
	void	(*teardown)(struct dsa_switch *ds);
```

— but the core calls it without looking.  A switch driver that leaves
`.setup` out does not fail to build; it fails to boot, with the call
landing on address zero.

The tree's own RTL8372N driver — the only other switch driver in this tree
— has a `.setup` (`zx279133_rtl8372n_setup`, which writes the RTL8372N's
VLAN table, the port PVIDs and the CPU tag control, and registers the
internal PHY bus).  That is why the core can get away with it.

**This tree is not stock mainline 6.18.**  Its DSA core has the "switch
tree" rework in it (`dsa_switch_tree`, `dsa_tree_setup`, ports on a linked
list rather than an array), and the switch driver's whole relationship with
the core is different from the one in `include/net/dsa.h` of stock
mainline: `struct dsa_switch_ops` here has exactly two PHYLINK hooks
(`phylink_get_caps`, `phylink_fixed_state`) and no `phylink_mac_ops`, the
core creates the phylinks itself (`dsa_port_phylink_create()`), and the
driver's `dsa_switch` is allocated with a plain `devm_kzalloc()` rather
than `dsa_switch_alloc()`.  Reading the tree's own driver, rather than
mainline's, is what makes the difference visible.

## The second defect, which would have failed the round just as surely

`dsa_port_enable()` is called for the CPU port as well as for the user
ports:

```c
	case DSA_PORT_TYPE_CPU:
		...
		err = dsa_port_enable(dp, NULL);
```

and `zx279133_switch_port_enable()` answered `-EINVAL` for anything
outside ports 4..7 — the CPU port is 8.  A switch whose CPU port refuses
to be enabled never registers, so even with `.setup` in place the four
jacks would not have appeared; the round would have failed with an error
instead of a panic, and the difference between the two failures is exactly
what a single window of board time is worth.

The CPU port is the SoC's own SGMII lane and has no PHY behind it to power
up, so it is now accepted and does nothing.  The bridge callbacks are
guarded the same way: with `wan_type=1` they would have asked the NPPT to
change the port type of the CPU port, which has no PPU of its own.

## The fixes (commit `70a5f5b`)

* `zx279133_switch_setup()` — reports what the switch is serving (the UNI
  ports, the DSA ports, the CPU port, the transport VIDs) and returns 0.
  Everything the hardware needed was already done by the NPPT driver and
  by the probe, so there is nothing left to program; the hook says so out
  loud, because a hook that does nothing silently is indistinguishable
  from one that was never called.
* `zx279133_switch_teardown()` — the pair of it.
* `zx279133_switch_port_enable()` — the CPU port is accepted.
* `zx279133_switch_port_bridge_join()` / `_leave()` — the CPU port is
  skipped.

The check that the ops table is not missing anything the core calls
without checking is in `staging/ctl/drivercheck.py`; it reads the tree's
`include/net/dsa.h`, lists every `ops->` the core dereferences, and finds
the ones that are called unguarded.  The unguarded ones are:

| Callback | Guarded? | The 9132 driver |
|---|---|---|
| `setup` | **no** | **missing — the panic** |
| `change_tag_protocol` | no, but only when the tree's protocol differs from the driver's | not needed: both say `DSA_TAG_PROTO_ZX279133_RTL8372N` |
| `port_vlan_filtering` | no, but only on the restore path | present |
| `port_vlan_add` / `port_vlan_del` | no, but the entry points check | present |
| `port_fdb_add` / `port_fdb_del` | the entry points check | absent, and the core returns `-EOPNOTSUPP` for it |
| `port_mdb_add` / `port_mdb_del` | the entry points check | absent, same |
| `port_change_conduit` | no, but only on the runtime CPU-port change | absent, and the tree's own driver does not have it either |
| `lag_fdb_add` / `lag_fdb_del` | the entry points check | absent |

So `.setup` was the only thing missing.

## A tooling defect, found the hard way

The first attempt to boot run 98's image did not get as far as U-Boot, and
the reason was not the board.

The console recorder (`tools/serrelay.ps1`) had been running since 03:07.
At 08:54 it wrote a command and the write failed — and every write after
that failed too:

```
=== send error: 端口已存在...Write... ===
```

The relay is alive, it keeps appending to `serial.log`, and it is logging
nothing but its own complaints.  The board meanwhile rebooted, printed its
whole U-Boot and kernel boot, and **747 bytes of that console output were
sitting unread in the serial port** — the port was writable, and readable,
and the recorder was not reading it.

The cause is the write side of the CH340: the transmit buffer fills up
while the board is off or its console is not being read, the write times
out, and every later write times out the same way.  A wedged recorder
looks exactly like a dead board, and the round sat there waiting for a
U-Boot prompt the board had already printed.

Two things changed:

* `tools/serrelay.ps1` now closes and reopens the port when a write
  fails, which clears the wedge and brings the backlog back with it.  The
  relay keeps running, because a relay that dies is worse than one that is
  slow.
* `staging/ctl/relaycheck.sh` writes a marker through the relay's command
  file and checks that the relay says it sent it; if not, the relay child
  is killed and the loop that owns COM3 starts a fresh one.  `tools/onewindow.sh`
  runs it before every boot, so a wedged recorder costs a few seconds
  instead of a whole window.

## What is still open

* **G2d** — the four jacks as separate DSA ports (`lan1`..`lan4`), and the
  transport VIDs in the frames.  Run 100 is the build that answers it.
* **G3 / M1** — the NAT, with the PC as the upstream and the board's own
  veth client behind the router.  The lab scripts are ready and were
  pushed in `428b2fc`; the watcher they depend on was broken in the
  forced-upstream mode (`/etc/labwan` holds `pc`, which is a name for a
  port and not a port name, so the watcher's `[ -e /sys/class/net/pc ]`
  was never true and the watcher did nothing at all, every minute, for
  ever) and the client behind the router pinged one address, 8.8.8.8,
  which is not reliably reachable from this bench — a ping that fails for
  that reason is indistinguishable from a router that does not forward.
  Both are fixed.
* **The switch's forwarding itself** — whether the 9132's fabric moves
  frames between a jack and the CPU port with only the PPU's per-port
  words and the transport VIDs in place, and no FDB of our own.  The tree
  pushes the FDB to the hardware through `port_fdb_add` and friends; the
  9132 driver does not implement them, so the core keeps the FDB in
  software and the hardware is left to its own VID tables.  Whether that
  is enough is a measurement, not a guess, and it is the next thing after
  the ports appear.

## Reproducing

```sh
# the boot itself
sh tools/ubtrial.sh                      # needs the relay and the TFTP server

# the evidence
sh staging/ctl/bootcut.sh 24937390       # the run-98 boot, cut out of serial.log
sh staging/ctl/panic.sh 24937390         # the panic, in full

# the check that the ops table is complete
python staging/ctl/drivercheck.py \
    ci-build-repo/kernel-overlays/drivers/net/ethernet/zte/zx279133-switch.c \
    staging/tree/include_net_dsa.h
```
