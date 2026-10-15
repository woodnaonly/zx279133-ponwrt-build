# PON stack inventory — SK-D840N (Sanechips/ZTE ZX279133)

Collected 2026-10-10 from three sources: reading the running unit with read-only commands
(`tools/ponsurvey.sh`, `tools/ponprobe.sh`, output in `staging/ponprobe.log`), disassembling
the vendor modules copied off it (`tools/ponelf.py`, `tools/pondec.py`), and reading the
upstream PON feeds (`pon/upstream-ponwrt/`). Nothing here was guessed; where something is
not known it says so.

**Do not publish these files.** `pon/vendor-modules/*.ko` are ZTE/Skyworth BSP
binaries. The build repo `woodnaonly/zx279133-ponwrt-build` is public, so this
directory stays outside it. (The same account that hosts the Airoha PON feeds also
hosts a `dmca` repo of takedown notices - vendor ONT code gets removed, and
redistributing it is a separate problem from understanding it.)

## 1. Conclusion first

- The vendor PON stack **is present and loaded on the currently flashed 4.19 system**,
  bound to the device tree, with its kernel threads running (`gpondrv_scan_th`,
  `gpondrv_task`), its `/proc` command surfaces live, **and PON interfaces registered as
  netdevs**: `pon`, `pon0`, `omci` (virtual, administratively down, `carrier_changes=0`)
  plus the hardware-switch glue `sw`, `idm0`, `idm1` (up, `sw` carrying real traffic).
  The boot trace proves the SDK came up: `pon mode is xg`, `gpon_Init ok`,
  `vendor name = ZTE_XGPON1ASY`, `switch config 9132 GPON mode!`, `insmod gponsdk_dev`,
  `pon0 create success`.
- **`zte_xgpon` runs the ITU state machines in kernel space.** Its binary carries the
  G.987.1 power state machine (`power_p2_lsi:power P2--->P3`,
  `power_p5_taware_expire:power P5--->P6 Watch/Listen`, …) and the ONU operation-state
  machine (`Op State Machine State %s Event %s`, `o1-o2-3`, `o2_3-o7`,
  `O4 p_o4_assigned_onu_id_msg`, `O7 p_o7_enable_sn_msg`, `O4 O4 Receive rangetime
  grant`), plus PLOAM send/receive (`fi_Configure_Interrupt_Read_Ploam_Msg`,
  `fi_sendPloamMsg`) and the GPON AES layer (`128 BIT AES CODE`, `omci aes key`,
  `onu aes key`). **So PON registration does not need the missing vendor userspace** -
  which is what changes the cost estimate for the fibre uplink: what is missing is
  visibility and configuration, not the protocol.
- What is genuinely absent on this image: **no `/dev` nodes** for the char devices the
  stack registers, and **no OMCI/management userspace anywhere**. The stack is not
  inert - it is headless.
- The headless part is smaller than it looks, **but only on the receive side**.
  `np_133`'s `idm_net_init()` registers **seven virtual netdevs** - `omci`, `oam`, `sw`,
  `pon`, `idm0`, `idm1`, `olt` - and this image shows four of them. An earlier draft of
  this file read that as "the OMCI frame path is reachable as a network interface"; that
  is **wrong and now measured** (§4h): every PON netdev transmit ends in an error branch,
  because the `netdev_priv + 0x8c8` back-pointer that `swport_dev_xmit_fin` requires is
  never written by any module in the image.
  **The OMCI MIB/ME engine is not in any of these modules** (`np_133` has only hardware
  hooks: `register_omci_oam_handle`, `register_omci_mic_check_handle`,
  `red_set_omci_drop_th`; `zte_xgpon` has only the channel plumbing:
  `fi_Configure_Ponmac_Create_Omci_Channel` builds the OMCC T-CONT + GEM pair, and
  `fi_Configure_Ponmac_Send_Omci_Msg` is a 2-instruction `return 0` stub). So an OMCI
  daemon is still required for *service* configuration; it is not required for
  *registration*.
- **OMCI can be received but not answered, with no door to open.** RX is proven: frames
  reach `omci_recv` (registered into the MAC by `register_omci_oam_handle`) and are
  forwarded onto Monitor netlink protocol 26. TX exists nowhere: no ioctl macro sends a
  PDU, netdriver has `omci_recv` but no `omci_send`, the netdev xmits all fail, and the
  stack's own send function is the stub above. Route A therefore needs **one small kernel
  patch to `zte_xgpon`**, not just userspace - see §4h and §7.
- **The kernel->userspace channel exists and works; it was just undocumented.** The 4.19
  image has a built-in Monitor subsystem that creates netlink protocols 25-28 at boot, and
  binding protocol 25 with `nl_pid = 0x48a3` on the live unit delivered eleven cached
  device-state messages that had been queued since boot (§4f). So the missing vendor
  daemon is not a missing *mechanism* - only the OMCI service logic is genuinely absent.
- Both command surfaces are now decoded from the binaries, in `pon/zte_xgpon-proc-cli.txt`
  and `pon/gpondrv-ioctl.txt`, by `tools/pondec.py`, and a client exists for them:
  `ci-build-repo/tools/ponctl.c` (built statically for aarch64 by the `build-ponctl.yml`
  workflow, fetched with `sh tools/getponctl.sh`). Practical consequence: the first round
  of live experiments needed **no compiled client at all** - `/proc/gpon_sdk/debug`
  answers every read-only question - and the client now confirms the same answers through
  a second, unrelated interface.
- **One finding can invalidate route A by itself**: password authentication is not
  configurable on this build. `zxic_api_gp_set_pwd` is exported but called by nothing, and
  the 1G registration adapter (ioctl macro 13) is a `not support now!` stub - see §4e. If
  the OLT at the other end of the fibre authenticates by password rather than by serial
  number, that has to come from an OMCI daemon, because no in-tree path can set it.
- It is **4.19-BSP-only**: `np_133.ko` alone is 5.2 MB and is the platform driver for
  nearly the whole SoC network block. Nothing about it works on a mainline kernel.
- **Corrected 2026-10-15.** The next bullet used to read: "So 'get GPON working' and
  'get mainline nftables/WireGuard' are mutually exclusive on this hardware with the code
  that exists today." That was derived from §5's claim that mainline has no PON MAC
  driver. §5 was wrong: the prepared tree's NPPT driver **is** the PON MAC, it hard-depends
  on the PON SerDes PHY, it loads ZTE's own PPU microcode, and it is all compiled into the
  kernel being flashed. So the exclusivity is no longer a property of the code - it is an
  unmeasured question. What is still unmeasured is whether the G.984.1 protocol layer is in
  that microcode, and whether any of it comes up at runtime. See §5 and §7B.

- The `ioctl` ABI is decoded, not guessed (`pon/gpondrv-ioctl.txt`): `gpondrv_devIoctl()`
  copies a fixed **136-byte** struct out of userspace, reads the command out of the
  struct (`ioctl_data->m_gponFuncMacro`) rather than out of the `cmd` argument - which it
  ignores entirely - looks the id up in a linked table built at init, calls the handler
  with `buf + 8`, and copies all 136 bytes back. 24 public ids (1-18, 20-25) and 20
  private ones (1001-1020), each bound to a named `gpondrv*Adapter`. Two ids are
  registered with a NULL handler, and four adapters (`SetPower`, `GetPower`,
  `CfgMulGemport`, `CfgBroadcastKey`) are compiled in but reachable from nothing - this
  `.ko` is a trimmed, product-specific build.

## 2. Module graph on the running unit

`lsmod` (name, resident size, users) for the parts that matter:

```
np_133        2654208  5 GN28L95,zte_xgpon,switch,netdriver,zx_ponreg_133
zte_xgpon      188416  1 gpondrv_xg
gpondrv_xg      36864  0
zx_ponreg_133   16384  2 zte_xgpon,switch
switch         344064  0
ffe            131072  1 switch
wan_bridge      45056  3 upload_optimize,download,netdriver
netdriver       28672  2 upload_optimize,download
multicast      163840  1 switch,[permanent]
optical         28672  0        (depends on bspdriver)
GN28L95         24576  0        (depends on bspdriver, np_133)
bspdriver      147456  2 GN28L95,optical
qos_mod         53248  3
region_init     16384  2 multicast,wan_bridge
```

Copies in `pon/vendor-modules/`, sizes as flashed:

| module | bytes | modinfo description / author | vermagic |
| --- | --- | --- | --- |
| `np_133` | 5 208 496 | (no description) | `4.19.136 SMP mod_unload aarch64` |
| `zte_xgpon` | 308 856 | `gponsdk device`, mengxiangjuan, depends `np_133,zx_ponreg_133` | `4.19.136` |
| `switch` | 853 280 | `switch devices`, guxiaodong, depends `np_133,ffe,zx_ponreg_133,multicast` | `4.19.136+` |
| `multicast` | 284 928 | | |
| `bspdriver` | 260 664 | | |
| `gpondrv_xg` | 66 504 | `gpondrv device`, ZXIC, depends `zte_xgpon` | `4.19.136+` |
| `rlt8226b` | 67 816 | `realtek 8226b Driver Module`, ZTE.wys | `4.19.136+` |
| `wan_bridge` | 84 928 | `MODULE WAN_BRIDGE`, ZXIC | `4.19.136+` |
| `ffe` | 169 224 | `FFE`, ZXIC | `4.19.136+` |
| `zx_ponreg_133` | 8 792 | `PON Driver`, ZTE, depends `np_133` | `4.19.136` |
| `netdriver` | 48 552 | `Eth Driver` | `4.19.136+` |
| `optical` | 47 496 | | |
| `GN28L95` / `GN28L96` | 27 960 / 15 816 | (BOSA/optics front end) | |
| `qos_mod`,`region_init`,`download`,`upload_optimize` | 92 352 / 5 736 / 33 528 / 38 288 | traffic shaper + per-direction accel | |

**The vermagic column is a finding by itself.** The stock BSP built `np_133`,
`zte_xgpon` and `zx_ponreg_133` for `4.19.136`, while this unit runs a Skyworth build
called `4.19.136+`; dmesg says so out loud:

```
np_133: magic '4.19.136 SMP mod_unload aarch64' should be '4.19.136+ SMP mod_unload aarch64'
```

so the community image loads them with forced vermagic. That works today and is the
reason any PON code path exists on the box at all.

## 3. What each layer is (from the binaries, not from names)

Full per-module string analysis is in `pon/abi-scan.txt`. Highlights:

- **`np_133` is the SoC platform driver, not a "PON driver".** The compatibles it
  carries are `zte,zx279133-{pon, nppt, idm-intr, bmu-ddr, se-ddr, woe-ddr,
  gephy-apb, xmac0-pcs, rgmii, pcu, pps, ipsec}` plus `zxic,apb-timer0/2`. In other
  words the PON MAC, the NPPT/IDM ethernet path, the buffer manager, the accelerators
  and the switch glue all live in this one 5.2 MB module. Its debug output is a
  `/proc`-style command language (`echo [me_id] > me_stats`,
  `echo [gemport_id] > supgem`, `gemport_vlan_map_table_set`, `sdmac`, `squeagemode`),
  and it prints PON-state histograms (`omci_flag(42:42)`, `gemport/llid(58:43)`,
  `dn omci cnt`).
- **`zte_xgpon` ("gponsdk") is the GPON/XG-PON protocol stack in kernel space**: GEM
  port and T-CONT tables (`DGEMT`/`UGEMT` counter blocks), AES (`DGEMT AES Drop …`,
  `omci aes key`), ITU power state machine (`power_p2_fwi`, `power_p3_fwi`,
  `power_p5_fwi`, `power_p6_fwi`), OMCI plumbing (`ASSIGN_GEM_PORT_MESSAGE`,
  `STACK OMCI DATA`, `OMCI Monitor`, `XGMAC SDK OMCI Mic Check failed`), and an
  `ioctl` file operation set: `gponsdk_omci_devReg`, `gponsdk_omci_devFops`,
  `gponsdk_omci_devIoctl`. Read §4c before trusting that last sentence: the ioctl and
  read handlers in it are `return 0` stubs.
- **`gpondrv_xg` is the ONU state/adapter layer** above it: `fi_onuGponSetOpState`,
  `gpondrvCfgO7StateAdapter`, `gpondrvCfg1GRegInfoAdapter`, and it owns the two kernel
  threads.
- **`optical` + `GN28L95`/`GN28L96` on top of `bspdriver`** are the BOSA (laser/receiver)
  control; `bspdriver` is the I2C/syscon glue (`optical,[permanent]` and `GN28L95` both
  depend on it).

## 4. Interfaces the stack exposes on this box

Char devices (`/proc/devices`), **with no `/dev` nodes created**:

```
102 gpondrv_dev
109 optical_dev
111 gponsdk_dev
220 dsp_dev        (voice, unrelated)
104 switch_dev
```

Live `/proc` handles, with what they actually report right now (no fibre in the unit):

| path | value / entries |
| --- | --- |
| `/proc/pon/upmode` | `upmode: 64` |
| `/proc/pon/up_mode_info` | `0x40; g_up_mode=0x40, g_is_auto_ponmode=0, g_epon_open_high=1, g_wan_port=0, g_lanup_port=0, g_wlanup_port=0, g_switch_ponmode=0x40, g_kernel_switch_en=0x0, g_multi_bosa_en=0x0, g_bosa_type=0x2, g_switch_time=60` |
| `/proc/pon/outer_phy` | `xmac0: phy_RTL8226 \| xmac1:` |
| `/proc/pon/{is_auto_ponmode, sky_auto_ponmode, switch_chip_type, switch_debug, tm_debug, ctrl_tm_shell, forbid_telnet, upmode}` | knobs, mostly read/write flags |
| `/proc/pon/pon_rsv_mem` | buffer-pool layout: `g_zx_net_max_mtu = 0x800`, `g_bppe_size = 0x20000`, `g_bp_size = 0x900`, `g_bppe_pool_size = 0x3400`, jumbo pool sizes |
| `/proc/gpon_sdk/debug` | present, currently empty |

Two things worth pinning:

- `g_wan_port=0` plus `/dev/ethdriver` naming `eth0..eth3` shows the vendor treats the
  PON path and the LAN ports as one hardware-switched domain (the 9132 "UNI switch"),
  which is why `brctl showmacs br-lan` is empty and why `eth0` is not a software port.
- `/proc/pon/outer_phy` = **`xmac0: phy_RTL8226`**. This is independent confirmation of
  the mapping our mainline board DTS uses (`&xpcs0` = XMAC0's PCS, PHY on MDIO bus 1 at
  address 5) - see commit `be57de8`, which also removed SR1010's wrong PHY compatible.

Device tree the vendor drivers bind to (read from `/sys/firmware/devicetree/base`, i.e.
the 4.19 DTB we are actually booting):

| node | properties that matter |
| --- | --- |
| `soc/pon@17000000` | `compatible = "zte,zx279133-pon"`, `interrupts = <0 6 4>`, `reg` = 0x17000000/0x1000000 plus 0x10e00000/0x1000 and 0x10e10000 (CRM), **`status = "okay"`** |
| `soc/pon_plat` | `compatible = "zxic,pon-plat"`, `mdio_8226_id = <1>`, `bosa_and_switch_ponmode_info = <0x05200040>`, `hff_chip_cap = <0x7ff>`, `kernel_default_up_mode_info = <0x40>`, `uboot_default_up_mode_info = <0x40>`, `opsd-gpio`, pinctrl function `pon_ben` on pin `p2-2` |
| `soc/pon_plat_rsv_mem_cfg` | `zxic,resv-memory-config`: the whole IDM ring/queue and RED threshold table (`idm_rx_queue_num = 0x18`, `config_zx_net_max_mtu = 0x800`, `se_hash_ddr_size = 0x400000`, …) |
| `reserved-memory/pon_rsvmem` | `zxic,resv-memory`, `size = <0x03245000>` (**~52 MiB**) |
| `soc/pon_serdes@16000000`, `pon_serdes_pll@16010000`, `uni_serdes@16100000` | `status = "disabled"` in the DT - `np_133` drives these blocks regardless |
| `leds*/led_pon` | `gpios = <&gpio? 15 0>`, `led-id = <7>`, default off |

The ~52 MiB reservation and the queue/RED table are a real constraint on any mainline
reimplementation: the vendor datapath is shaped around fixed buffer pools, and this
board has 512 MiB of DDR total.

## 4b. The interfaces the stack actually publishes

Netdevs (`/sys/class/net`, read 2026-10-10):

| iface | flags | state | notes |
| --- | --- | --- | --- |
| `pon` | `0x1002` (down) | `carrier_changes=0`, all statistics 0, `/sys/devices/virtual/net/pon` | the PON service interface, never brought up by this image |
| `pon0` | `0x1002` (down) | same | created by `np_133` (`qupengchao : pon0 create success`) |
| `omci` | `0x1002` (down) | same, MAC `00:d0:d0:61:62:02` | an OMCI netdev *exists*, which is half a transport: receive works (netlink 26, §4f) but its xmit always takes the error branch and `send_omci_cnt` never moves, so this is not a usable client door (§4h) |
| `sw` | `0x1003` (up) | rx 24932 / tx 33545 | the 9132 hardware-switch glue |
| `idm0`, `idm1` | `0x1003` (up) | 0 rx / 2 tx | IDM (the SoC's interrupt-driven DMA channel netdevs) |
| `eth0..eth3` | up, carrier 1 | MACs `d0:45:8d:84:08:ff`, `09:00..09:02` | eth0 = RTL8226 (XMAC0), eth1..3 = 9132 UNI ports |

`/proc` command surface (this is the vendor's own debug CLI; `np_133` carries the usage
text, e.g. `Usage: echo [OPTIONS] > /proc/tm/shell`):

- `/proc/pon/upmode`, `/proc/pon/switch_time` (`switch time: 60`),
  `/proc/pon/is_auto_ponmode`, `/proc/pon/sky_auto_ponmode`, `/proc/pon/outer_phy`,
  `/proc/pon/switch_chip_type`, `/proc/pon/pon_rsv_mem`, `/proc/pon/up_mode_info`
- `/proc/pon/debug/` - `help`, `tm_dbg`, `start_statistics`, `stop_statistics`,
  `cla_show_fast_rule`, `multicast_table`, `multicast_v4table`, `multicast_v6table`,
  `multicast_groupid_table`, `multicast_multtrans_table`, `multicast_src_action_table`
- `/proc/pon/tm_debug/` - ~60 nodes, and the useful-looking ones for us:
  `tm_onu_mac_addr_get` / `_set`, `sw_port_linkstate_get`, `sw_port_statistics1..5_get`,
  `tm_mactable_fdb_show`, `sw_upmode_set`, `port_proation_get/set`, `module_reg`,
  `module_ind_ram`
- `/proc/pon/switch_debug/` - ACL/MAC-table/protection commands (`sw_mac_get_mactable`,
  `sw_mac_show_macaddress`, `fast_rule_add`, `sw_debug_set_debuglevel`, …)
- `/proc/tm/shell` - the traffic-manager shell (`echo mtu=1000 > …`, `up_en`, etc.)
- `/proc/gpon_sdk/debug` - reads as NUL bytes; a write-side command node whose
  vocabulary is not in the string table.

Two gotchas worth recording, both hit while collecting this:

1. **Reading `/proc/pon/debug/multicast_*` dumps large tables into the kernel log**, and
   the 4.19 ring buffer is small: doing so evicted the boot trace that contained the
   PON init lines. Capture `dmesg` before poking at those nodes.
2. The LEDs are *not* currently driven by the LOS logic on this image:
   `/sys/class/leds/{led_los,led_pon,led_wan,led_power}` all exist with
   `brightness=0`, `trigger=[none]`, and the vendor `zxled` class is registered
   separately - so no optical state can be inferred from the panel here.

And a negative result: the stock Linux rootfs is **not recoverable from our flash dumps**
in any obvious way. `tools/flashscan.py` finds no jffs2/squashfs/UBI/ELF magic in any of
the 18 dumps; `mtd3_kernel0.bin` decompresses to a 10.7 MB arm64 `Image` at offset 724
and the remaining ~7 MB to 19 MB is uniformly high-entropy (H≈8.0) with no structure,
i.e. compressed-or-encrypted firmware. `mtd10/mtd12_osgi*` are the Skyworth Java OSGi
middleware (1332 zip members), not the Linux userspace. So the vendor *userspace* ABI has
to come from the modules themselves, not from the stock rootfs.

## 4c. `zte_xgpon` dissected

Done after the pass above, with two tools written for it. `tools/ponelf.py` reads a `.ko`
as ELF - `.symtab` plus every `.rela.*`, which in an unlinked module is a complete
cross-reference (which function touches which string, which data slot, which callee) - and
`tools/pondec.py` then walks the tables the way the driver itself does. Nothing below was
inferred from names; these binaries are fully symbolized (344 functions in `zte_xgpon`,
~5 600 symbols in `np_133`), so this is table-reading rather than guesswork.

**Layering, read off the import graph** (`pon/zte_xgpon-api.txt`):

- `zte_xgpon` asks the platform layer for six kinds of thing only: register access
  (`fpga_read_reg`/`fpga_write_reg` → its own `Hal_ReadPonGreg`/`Hal_ReadXmacReg`),
  interrupts (`register_lp_int`, `register_xgmac_int`), integrity hooks
  (`register_omci_mic_check_handle`, plus `regisetr_omci_mic_add_handle` - the vendor typo
  is part of the ABI), laser/power (`hw_power_optx_set`), pin control
  (`pon_plat_pinctrl_set`) and the traffic manager (`tm_get_statistic`,
  `tm_tcont_que_sch_*`).
- It hands back **63 exported symbols**, and they are the whole provisioning vocabulary:
  `zxic_api_gp_set_sn` / `set_pwd` / `set_regid`, `set_tcont` / `set_tcont_queue` /
  `add_gemport` / `del_gemport` / `clr_tcont`, `set_gemport_encrypt` /
  `set_multicast_encrypt_key`, `set_o7state` / `get_opstate`, `set_longlaser` /
  `set_ponsend` / `set_ponrcv`, `get_equalization_delay`, `get_fec_state` /
  `get_aes_state` / `get_lp_para` (rx/tx power), `get_onuid`, `zxic_ploamMsgLaserControl`,
  four `*_notify_register` upcalls and the per-direction PM counters
  (`zxic_api_xgpon_interface_{up,dn}_pm_get`).
- `gpondrv_xg` is its only consumer, and the mapping is 1:1: every exported call has a
  named `*Adapter`, and every adapter is registered under a small integer macro id.

**The state machines are dispatch matrices sitting in `.rodata`**, which is easier to read
than code:

| table | shape | what it proves |
| --- | --- | --- |
| operation state names | `O0 O1 O2 O2-3 O4 O5 O6 O7` at `.rodata+0x23b8` | G.984/G.987 O-states |
| O-state × PLOAM event | 44 slots per state, stride 8, from `.rodata+0x2800` | `p_o2_3_sn_request_event`, `p_o2_3_assigned_onu_id_msg`, `p_o4_ranging_request_event`, `p_o5_assign_alloc_id_msg`, `p_o5_request_aes_key_msg`, `p_o5_request_registration_msg`, `p_o7_enable_sn_msg` |
| PLOAM message names | 30 strings at `.rodata+0x2f40` | `SERIAL_NUMBER_REQUEST`, `ASSIGN_ONU_ID_MESSAGE`, `RANGING_REQUEST`, `ASSIGN_ALLOC_ID_MESSAGE`, `REQUEST_KEY_MESSAGE`, `KEY_SWITCHING_TIME_MESSAGE`, `PST_MESSAGE`, `SERIAL_NUMBER_MASK_MESSAGE`, `DISABLE_SERIAL_NUMBER_{ENABLE,DISABLE}_MESSAGE`, `ENCRYPT_PORT_ID_MESSAGE`, `CONFIGURE_PORT_ID_MESSAGE`, `BER_INTERVAL_MESSAGE` |
| power state × event | `power_p2_{fwi,sa_off,ldi,lsi,lpi}`, then P3-P6 at `.rodata+0x3088` | G.987.1 P1-P6 sleep/listen/disconnect |
| upstream key exchange | `p_key_exchange_kn{0,2,4}_*`, `tk4`/`tk5` at `.rodata+0x3378` | GPON encryption key lifecycle |

Most slots are `p_donothing`, and that is itself information: the events this build reacts
to are the registration path and little else. With the AES-128 + CMAC code compiled into
the same module (`AES_set_encrypt_key`, `AES_128`, `AES_CMAC`, `Drv_Ploam{u,d}Mic_*`,
`Drv_Omci{u,d}Mic_*`), **SN/password/RegID matching, ranging, alloc-id assignment and key
exchange all happen in kernel space**, driven by `gpondrv_task` / `gpondrv_scan_th`.
`gpondrv_xg` confirms the endpoint: `hw_power_optx_set` is called from
`p_o7_enable_sn_msg` and from `zxic_api_gp_set_o7state`, i.e. reaching O7 is what switches
the upstream transmitter on.

**Both char devices, and what each is actually worth** (`file_operations` read out of
`.data` slot by slot):

- major **111** `gponsdk_dev`: `.read` is `mov x0, #0; ret`, and `.unlocked_ioctl` /
  `.compat_ioctl` both point at `gponsdk_omci_devIoctl`, which is also `return 0`; there is
  no `.write`. Registered as `__register_chrdev(0x6f, 0, 256, "gponsdk_dev", fops)`.
  **Creating a `/dev` node for this one buys nothing** - it is inert by construction, not
  by omission. (This corrects the guess in §4 above.)
- major **102** `gpondrv_dev`: the working door, in `pon/gpondrv-ioctl.txt`.
  `__register_chrdev(0x66, 0, 256, "gpondrv_dev", fops)`; `ioctl(fd, anything, &buf)` over a
  **136-byte** struct - `cmd` is ignored, the command lives at `+4`, the handler is called
  with `buf + 8`, and all 136 bytes are copied back. Macro **15** =
  `gpondrvGetOpStateAdapter`, **1001** = `gpondrvGetPonStatAdapter`, so "what state is the
  ONU in" is one ioctl away once a node exists. 24 public ids (1-18, 20-25 - 19 is a real
  hole) and 20 private ones (1001-1020, two of them registered with a NULL handler). Four
  adapters (`SetPower`, `GetPower`, `CfgMulGemport`, `CfgBroadcastKey`) are compiled in but
  referenced by nothing, so this `.ko` is a trimmed, product-specific build.

**The `/proc/gpon_sdk/debug` CLI is the practical interface** - 44 commands recovered from
the record array the write handler walks (stride `0x130`), in
`pon/zte_xgpon-proc-cli.txt`. Syntax `echo "<name> [args]" > /proc/gpon_sdk/debug`; the
handler is `fn(int...)` after `sscanf(rest, fmt)`, and the reply goes to dmesg as
`func <name> return success`. Three things fall out of reading the dispatcher itself:

1. `p*` commands are read-only prints - `pstate`, `pomci`, `pploam`, `ppower`, `pmic`,
   `ptimer`, `pirq`, `pregs <addr> <n>`, `pdn*cnt`, `pup*cnt`. That is a complete status
   surface for a headless stack with no compiled client involved.
2. An **unknown token does not fail**: the loop reaches index `0x2c` and then prints the
   whole table (name + usage) to the kernel log. So `echo what > /proc/gpon_sdk/debug`
   recovers the authoritative vocabulary from the running unit - which is how the table
   above gets checked against reality.
3. One command is matched before the loop and is therefore absent from the table:
   **`ssn <12 chars>`** → `test_Configure_Ponmac_Set_Sn()`. The ONU serial number can be
   programmed from a shell. The same table holds `sponmode <0x3|0x7|0xB|0xF>`
   (`xgpon1 / xgpon2 / ngpon2.5G / ngpon10G`) and `sponen <0|1>`.

The hazard is just as concrete: `r*`/`c*` (`rtcont`, `rgemp`, `rrawtcont`, `crawtcont`)
**delete live T-CONT and GEM-port entries**, and `s*` writes real configuration. Those are
not exploratory commands. `fi_Configure_Debug_Init` also creates a second node,
`special_debug`, backed by `seq_file`.

**What this changes, and what it does not.** Registration (SN/password → O2-3 → ranging →
O5 → O7 → laser on) exists in the kernel and is addressable from a shell, which turns "GPON
up on the 4.19 BSP" into a configuration problem instead of a driver-writing project - *as
far as the binaries show*. None of it has been exercised on the unit, and there was no fibre
connected while these findings were collected, so they are ABI results, not end-to-end ones.
What stays missing after O7 is OMCI ME/MIB service configuration, which no module here
implements, and §4h shows its transmit transport does **not** exist - receive is a working
netlink path, respond is a kernel patch.

## 4d. What the stack reports about itself right now

`sh tools/ponprobe.sh` piped through `sh tools/ow.sh`, 2026-10-10, no fibre connected,
output in `staging/ponprobe.log`. This is the first time the vendor stack has been asked a
question in its own language, and it answers:

```
=======================MAC STATE=====================
ID            1023,          <- 0x3FF = the "no ONU-ID assigned yet" value
SN            00000808       <- a vendor default, not a real ZTEG serial
STATE         O1             <- G.984 O1: PON-ID unassigned, serial number not matched
REGID                        <- empty
SDK VSERSION  V1.1
```

| probe | what came back | reading |
| --- | --- | --- |
| `pploam` | every counter 0 (`whole receive ploamd`, `recv_assign_onuid`, `send_sn`, `send_reg`…) | nothing has ever been received on the downstream, so the PLOAM machine has not been triggered |
| `pomci` | `recv_omci_cnt = 0`, `send_omci_cnt = 0`, `recv_omci_mic_err_cnt = 0` | no OMCI traffic at all, and no MIC failures - not even attempted |
| `pirq` | `mg_sn_req_irq_cnt = 0`, `mg_fwi_irq_cnt = 0`, `pwn2mg_* = 0` | the hardware never raised the "OLT asked for my serial number" interrupt |
| `ppower` | `Software_powerState = 1`, all of `ihold/iaware/isleep/itransinit` 0 | P1 = fully awake, no power-state transitions ever taken |
| `pmic` | `ploam_mic_enable = 1`, `newKeyEncryptEnable = 1`, `onuKeyComfirmEnable = 1`, `fake_enable = 0`, `laser_status = 0` | PLOAM integrity and upstream encryption are **on by default**; the laser is off |
| `ptimer` | `TO1 delay = 10000`, `TO2 = 100`, `TK4 = 100`, `TK5 = 20`, all `NOT_ACTIVE` | G.984 timers configured, none armed |
| `pgemport` / `ptcont` | both empty, in the database *and* in the hardware | the service path has never been provisioned |
| `ps` | `[gpondrv_scan_th]`, `[gpondrv_task]`, state `SW` | both threads alive and idle, waiting on the PLOAM queue |

`/proc/gpon_sdk/debug` does not exist as a directory listing entry beyond `debug`
(`special_debug`, the second node `fi_Configure_Debug_Init` creates, was not created on
this boot), and the four char devices are still registered with no `/dev` nodes:
`102 gpondrv_dev`, `104 switch_dev`, `109 optical_dev`, `111 gponsdk_dev`.

**The recovered command table checks out against the driver itself.** Sending an unmatched
token makes the write handler print its whole table, and the live dump - `pstate, pdebug,
ptcont, pgemport, pstatictcont, pstaticgemport: gemportId[%d], pstaticfec, pmic, pkey,
pploam, pomci, pirq, ptimer, ppower, sbrdgem …` - is in the same order with the same usage
strings as `pon/zte_xgpon-proc-cli.txt`. That is the validation this kind of static
reconstruction usually cannot get, and it is why §4c's conclusions can be trusted.

Reading the state honestly: **O1 with every counter at zero is "idle", not "broken"**. It
also tells us the failure mode to expect when the fibre goes in: if the OLT asks and the
stack answers, `send_sn` and `mg_sn_req_irq_cnt` move before any userspace is involved, and
the state walks `O1 -> O2-3 -> O4 -> O5 -> O7`. If it does not move, `pirq`/`pploam` say so
immediately, and the first knob to try is `ssn` (the SN on this unit is a default, and an
OLT that authenticates by serial number will ignore `00000808`).

## 4e. The provisioning path, end to end

What an ONT actually has to tell an OLT, and what this stack can be told, traced through
the code rather than the strings:

| need | reachable how | status |
| --- | --- | --- |
| serial number | `/proc/gpon_sdk/debug` ← `ssn <12 chars>`, **or** `gpondrv_dev` ioctl macro **14** | works with no client at all |
| LOID / RegID | ioctl macro **14** only | needs a ~30-line client, struct known (below) |
| password | nothing | **not configurable on this image** - see below |
| PON mode (xgpon1 / xgpon2 / ngpon) | `/proc/gpon_sdk/debug` ← `sponmode 0x3\|0x7\|0xB\|0xF`, plus `/proc/pon/upmode` | present, untested |
| upstream laser enable | `sponen 0\|1`, and `hw_power_optx_set` fires automatically on O7 | present |
| read state / counters | `/proc/gpon_sdk/debug` `p*` commands (dmesg) | proven live, §4d |

`gpondrvCfgXGRegInfoAdapter()` (macro 14) is the only writer of identity, and its payload
layout falls straight out of the disassembly - it copies 46 bytes of the ioctl payload into
a stack frame, then:

```
+0x00  u8 sn[8]        memcmp'd against 8 zero bytes; if non-zero -> zxic_api_gp_set_sn()
+0x08  u8              copied but never read by this adapter (meaning unknown)
+0x09  u8 regid[36]    memcmp'd against 36 zero bytes; if non-zero -> zxic_api_gp_set_regid()
```

so the client is `struct { u32 zero; u32 macro /*14*/; u8 sn[8]; u8 pad; u8 regid[36]; u8
rest[76]; }` over `ioctl(fd, 0, &s)`, and either field may be left zeroed to skip it. The
debug print in the same function shows the SN convention the vendor expects: four ASCII
vendor characters then four hex bytes (`sn is:%c%c%c%c-%02x%02x%02x%02x`), which is why the
live `pstate` reading `SN 00000808` is clearly a placeholder rather than a real `ZTEG…`
serial.

**The password gap is real and specific.** `zxic_api_gp_set_pwd` is exported by
`zte_xgpon`, and *nothing in any loaded module calls it* - not `gpondrv_xg`, not
`np_133`, not `wan_bridge` or `netdriver`. Same for `zxic_api_gp_set_sfsd` (vendor/sub
vendor id). Macro **13** (`gpondrvCfg1GRegInfoAdapter`, the GPON-1G sibling of 14) is not
even implemented: it prints `"%s not support now!"` and returns 0. So on this build a
password-authenticating OLT cannot be satisfied through any in-tree path - it would have to
come from the missing OMCI daemon (ME 14 / `ntl` password), or the OLT has to accept SN
auth. That is a concrete thing to check against the local OLT before writing any code, and
it is the one finding here that can invalidate route A on its own.

## 4f. The kernel's Monitor netlink channel - the door that was missing

`MontorSendMsg` is **undefined in every module we copied**, and it is not exported by any
of them. It is in `/proc/kallsyms` as kernel text: the vendor 4.19 image has a whole
**Monitor** subsystem built in, and that is the kernel -> userspace channel the missing
vendor daemon used. There was no source and no `.ko` for it, so it was read out of
`/proc/kcore` on the running unit with `tools/kcore.py` (the program headers give an exact
`VA -> file offset` map, so this is a few `dd` bytes over ssh, not a kernel rebuild).

The subsystem, as the running kernel reports it:

```
$ grep -iE "montor|monitor" /proc/kallsyms
ffffff80085c4cb8 t MontorSendMsgInner
ffffff80085c4f3c t monitor_timer_handler
ffffff80085c5070 t MonitorCacheMsg
ffffff80085c5200 T OmciMontorSendMsgBroad      <- __ksymtab
ffffff80085c55c8 T MonitorInit                 <- __initcall_MonitorInit6
ffffff80085c5750 T MontorSendMsg               <- __ksymtab
ffffff80085c57d0 T MontorSendPriorMsg          <- __ksymtab
ffffff80085c5874 T MontorSendAnyMsg            <- __ksymtab
ffffff80085c5914 T OtherMontorSendMsg          <- __ksymtab
ffffff8008b0fea8 b GOmciMsgSocket
ffffff8008b0feb8 b g_monitor_dir_entry
```

`MonitorInit` (a `device_initcall`) does four things, all now visible in the running
kernel's memory:

| what | value | evidence |
| --- | --- | --- |
| `__netlink_kernel_create(net, 25, cfg)` | `GDeviceStateSocket` | protocol 25 present in `/proc/net/netlink` with pid 0 (kernel-owned) |
| `__netlink_kernel_create(net, 26, cfg)` | `GOmciMsgSocket` | same, protocol 26 |
| `__netlink_kernel_create(net, 27, cfg)` / `(net, 28, cfg)` | `GOmciMsgSocket1`, a fourth | protocols 27, 28 present |
| `proc_mkdir("monitor")` + `proc_create("mstats"/"msgtrack"/"msgbuff")` | `g_monitor_dir_entry` is non-NULL in `.bss` | but **`/proc/monitor` is not in the filesystem** - so those three diagnostic nodes are unreachable on this image, while the sockets are alive |

The message framing, from `MontorSendMsgInner`:

```
struct nlmsghdr            nlmsg_type = 25 (the protocol number), nlmsg_pid = 1
  + u16 source             who sent it (0 from MontorSendMsg, 26 from the switch path)
  + u16 event              the event id - an enum that exists only as call-site immediates
  + u16 prio
  + u16 payload_len
  + payload                <= 0x800 bytes, copied from the caller
```

and the delivery differs per channel:

- **OMCI** (`OmciMontorSendMsgBroad`) uses `netlink_broadcast(GOmciMsgSocket, skb, 0,
  group=1, gfp)` -> a listener binds protocol 26 with `nl_groups = 1`.
- **Device state** (`MontorSendMsg`) uses `netlink_unicast(GDeviceStateSocket, skb,
  portid = 0x48a3, 1)` -> a listener must bind protocol 25 **with `nl_pid = 0x48a3`
  (18595)**, because that is the hardcoded destination portid, not something negotiated.
  `MontorSendMsg` also checks the low word of `GOmciMsgSocket` and, when it is non-zero,
  takes the `MonitorCacheMsg` path instead of sending - so on this build the device-state
  events are queued for later delivery rather than pushed live.

**Proven on the unit** with `ponctl listen` (`ci-build-repo/tools/ponctl.c`, built by the
`build-ponctl.yml` workflow, asserted statically linked and smoke-tested under qemu in
CI). Binding protocol 25 with `nl_pid = 0x48a3` immediately delivered **eleven cached
messages** - the backlog the kernel had been holding since boot:

```
monitor hdr: 0  42246 0 24    "eth2" ...                        (port event)
monitor hdr: 26 42048 1 196   MAC d0:45:8d:84:09:00 "eth1"      (per-LAN-port record)
monitor hdr: 26 42048 1 196   MAC d0:45:8d:84:09:01 "eth2"
monitor hdr: 26 42048 1 196   MAC d0:45:8d:84:09:02 "eth3"
monitor hdr: 26 42048 1 196   MAC 84:47:09:5b:bd:2c "eth2"      (learned address)
monitor hdr: 0  42052 0 56    "br-lan" + fdb4:3e24:4072::1      (interface address)
monitor hdr: 26 42048 1 196   MAC d0:45:8d:84:08:ff "eth0"      (the RTL8226 port)
```

Two behaviours matter for any daemon built on this, and both were measured rather than
assumed:

1. **The cache flushes once.** Re-binding the same protocol/portid minutes later delivered
   zero messages. So a listener has to be up before the events it cares about, and cannot
   treat the kernel as a replayable log.
2. **`/dev/gpondrv_dev` and `/proc/gpon_sdk/debug` agree.** `ponctl status` (ioctl macros
   15/16/1007) reports `opstate 1`, `onu_id 1023 (unassigned)`, and the `/proc` CLI's
   `pstate` independently reports `STATE O1`, `ID 1023` - two unrelated interfaces, same
   answer, which is the check that the ioctl table was decoded correctly.

Two caveats worth keeping in the same place: the event ids are only known as numbers
(`42048`, `42052`, `42246` observed live; `tools/ponevents.py` finds 11 send sites in
`gpondrv_xg.ko` but the ids are computed there, not immediates, so the enum's names are
still unmapped), and **`dmesg -c` was used once while probing and it cleared the ring
buffer** - the boot trace that contained `pon mode is xg` / `gpon_Init ok` is gone from the
device until it reboots. It is preserved here and in `staging/ponprobe.log`, and
`tools/ponprobe.sh` deliberately does not use `-c`.

Note also: forcing O1 (`ponctl raw 22`, the state the unit is already in and the direction
that turns the upstream laser *off*) produced no event and changed nothing, so the PON
state-change events can only be exercised with light on the receiver. `los` reads 0 while
disconnected, which is another sign that flag is set by an interrupt path rather than
polled - do not read it as "fibre is fine".

## 4g. ponctl: the client, built in CI, run on the unit

`ci-build-repo/tools/ponctl.c` + `.github/workflows/build-ponctl.yml` (latest tag
`ponctl_5_38068805959`, fetched with `sh tools/getponctl.sh`, pushed with
`sh tools/zpush.sh staging/ponctl/ponctl /tmp/ponctl`). Static aarch64 - the target runs
musl and has no compiler - asserted `statically linked` in CI and smoke-tested under
qemu before it was published, because "does not exec" is expensive to discover over ssh.
Run #3 failed for a reason worth keeping: a scripted edit put raw newlines inside the
usage string literals, so it broke at compile time rather than on the device.

`ponctl` mknods `/dev/gpondrv_dev` itself, and its read-only path agrees with the `/proc`
CLI, which is the cross-check that the decoded ioctl table is right:

```
$ /tmp/ponctl status
opstate          1                <- O1;  /proc/gpon_sdk/debug pstate says STATE O1
onu_id           1023  (unassigned)  <- same value pstate prints as "ID 1023"
los              0
fec_state        raw (layout not decoded)   [0]=1
response_time    raw                        0x2a86 = 10886
eq_delay         raw                        all zero
pon_stats        raw                        all zero

$ /tmp/ponctl listen 25 0x48a3 0   # the cached backlog, §4f
$ /tmp/ponctl listen 26 0 1        # OMCI multicast; silent with no fibre
$ /tmp/ponctl sn ZTEG01234567      # macro 14: identity, the only writer in this build
$ /tmp/ponctl tx omci "ff ff ... "  # §4h: bytes must be space/colon separated
```

`ponctl raw 22` (force O1 - the state it was already in, and the direction that turns the
upstream laser **off**) returned 0 and changed nothing observable, so no event was emitted
by it. That is the honest limit of what can be proven without light: the *channel* is
demonstrated, the *PON* state transitions are not.

## 4h. OMCI has a receive door, and no transmit door

§4f and §7 left one question open: if an OMCI daemon existed, how would a response get
back to the OLT? That question is now answered, and it was answered **without light on the
receiver** - the evidence is counters and code, not a live OLT.

**The netdevs cannot transmit, as built.** `ponctl tx <if> <hex>` (AF_PACKET/SOCK_RAW,
ifindex read from `/sys/class/net`) was pointed at every PON netdev with the same frame:
an Ethernet header plus an OMCI frame (version 1, message id 1 "up", one section, one
message entry). `sendto()` always succeeded; the driver always failed:

| netdev | `tx_packets` col | `tx_errors` col | stack `send_omci_cnt` |
| --- | --- | --- | --- |
| `omci` (ifindex 4) | 0 -> 0 | 0 -> 3 -> 4 -> 5 -> 6 -> 7 | 0 -> 0 |
| `pon` (6) | 0 -> 0 | 0 -> 3 | 0 |
| `pon0` (9) | 220 -> 299 | 2 -> 3 | 0 |
| `idm0` (7) | 220 -> 299 | 2 -> 3 | 0 |
| `idm1` (8) | 220 -> 299 | 2 -> 3 | 0 |

What is reliable here: **`tx_errors` moves by exactly one per frame on every one of them,
and `send_omci_cnt` - the stack's own counter for "we put an OMCI frame on the wire" -
never moves.** The frame used for the sweep was 79 bytes because its message entry came out
49 bytes instead of G.984's 48, so the experiment was repeated twice on `omci` with a
byte-exact frame (14 Ethernet + 8 OMCI header + 8 section + a 48-byte message entry = 78
bytes, `staging/frame78.txt`): one more `tx_error`, `send_omci_cnt` still 0
(`staging/omcitx-spec78.log`). Malformed-frame rejection is not the explanation. What is
*not* reliable: the `+79` that lands in the `tx_packets` column of `pon0`/`idm0`/`idm1`
while the bytes column stays 0 - that matches neither branch of the vendor's own
accounting, so read these netdevs' stats columns as vendor bookkeeping, not as Linux
`net_device_stats` semantics.

Why the failure branch is unconditional: `swport_dev_xmit_fin` (netdriver.ko, `.text+0x2350`)

```
x2 = [dev + 0x8c8]            ; the "real device behind this port" back-pointer
if (x2 == 0)  { [dev + 0x8d0 + 0x38]++;  kfree_skb(skb);  return -1; }
```

and a struct-offset scan over the modules that could plausibly own that field finds exactly
one reader and **no writer anywhere among them**:

```
$ python tools/ponDis.py scan pon/vendor-modules/{netdriver,switch,np_133,gpondrv_xg,zte_xgpon}.ko 0x8c8
netdriver.ko   swport_dev_xmit_fin        0000236c  ldr  x2, [x19, #0x8c8]
# netdriver.ko: 1 accesses to +0x8c8
# switch.ko: 0 accesses to +0x8c8
# np_133.ko: 0 accesses to +0x8c8
# gpondrv_xg.ko: 0 accesses to +0x8c8
# zte_xgpon.ko: 0 accesses to +0x8c8
```

So the netdev TX path is struct dead in this build, not merely unconfigured.

**No other surface offers OMCI transmit either.** Three independent reads agree:

- `pon/gpondrv-ioctl.txt`: 45 macros, none sends an OMCI PDU. The only OMCI entries are
  1016 `gpondrvSetOmciMskAdapter` and 1017 `gpondrvSetOmciEnSecurityAdapter`.
- `zte_xgpon`'s `fi_Configure_Ponmac_Send_Omci_Msg` is a `return 0` stub (§4c).
- netdriver's own symbol set has **`omci_recv` and `oam_recv`, but no `omci_send`**. The
  string `"send OMCI msg  failed %d"` sits *inside* `omci_recv` - it is the failure print
  of forwarding a *received* frame to the Monitor netlink (`OtherMontorSendMsg`), not a
  transmit path. The only internal caller of `pdt_ethdrv_send` is `oam_recv` (OAM loopback).

`pdt_ethdrv_send`/`switch_skb_recv` are installed as callbacks by `pdt_ethdriver_init` into
a table at `+0x758/+0x760/+0x768`, i.e. the PON MAC calls *them*; they are not reachable
from userspace. `netdriver` also registers `/dev/ethdriver` (major **83**, mode 644, and
unlike `gpondrv_dev` the node **does exist** at boot) - but its `ethdrv_dev_read`,
`ethdrv_dev_write` and `ethdrv_dev_ioctl` are 8-byte stubs, and `/proc/ethdriver/conf` only
exposes a debug MAC:

```
$ cat /proc/ethdriver/conf
debug mode close
debug mac is 00:00:00:00:00:00
```

**Receive is partly broken too, in a way that only shows up now.** During the TX sweep the
kernel printed, twice:

```
pdt_ethdrv_recv meet error pdev==NULL || pdev->reg_state== 0,skb->pdt_info.port=15.
```

`pdt_ethdrv_recv` classifies an incoming skb onto a registered netdev by `skb->pdt_info.port`,
and port 15 has none. That is the same half-initialized vendor datapath the missing daemon
used to complete, and it means a received OMCI/PLOAM-bearing frame cannot be assumed to
reach a socket on this image either - `omci_recv` is wired to the MAC directly (via
`register_omci_oam_handle`, §2), so *it* will run, but the general classification path is
not.

**Consequence for route A, stated plainly:** with the code that exists, this ONU can do
PLOAM-level registration (identity, O-states, key exchange - all in `zte_xgpon` +
`gpondrv_xg`, and readable through the surfaces already proven) and **cannot** answer an
OLT's OMCI requests, because there is no transmit door to open. An OMCI daemon therefore
needs a kernel change, and the change is small and named: implement
`fi_Configure_Ponmac_Send_Omci_Msg` in `zte_xgpon` so it queues to the MAC, and publish one
ioctl macro or netlink op that hands a frame to it. That is a patch to a 4.19 module, not a
new driver - but it is no longer "just userspace", which is what route A assumed.

Scripts and raw output: `staging/omcitx.sh` / `staging/omcitx.log`,
`staging/txsweep.sh` / `staging/txsweep.log`, `staging/frame78.txt` +
`staging/omcitx-spec78.log` (the byte-exact rerun), `staging/offset-8c8.txt` (the field
scan). `ip link set {pon,pon0,idm0,idm1,omci} up` is harmless (all five have no carrier and
no queue activity), and none of this touched the fibre.

## 4i. The missing daemon is missing from the stock firmware too

If the vendor ever shipped a PON client, it would be inside the stock partitions we dumped,
and it would be worth more than any of our reverse-engineering: real payload layouts, real
event ids, real OMCI framing. So the dumps were searched two ways - raw bytes, and every
decompressible stream inside them (`tools/stocksearch.py`, gzip/xz/bzip2/raw-LZMA starts,
each inflated and grepped for `gpondrv_dev`, `gpon_sdk`, `omci`, `ploam`, `zxic`):

| searched | raw grep | decompressed streams | PON userspace |
| --- | --- | --- | --- |
| `backups/mtd4_middleware.bin` | yes | 1 | none |
| `backups/mtd7_others.bin`, `mtd9/mtd13_phoneapp*`, `mtd14_awifi` | yes | 0 (empty) | none |
| `backups/mtd10_osgi0.bin`, `mtd12_osgi1.bin` | yes | 31 each | none |
| `device-dumps/p11_v2_chunk0.bin` | yes | 0 | none |
| `restore_set/root_stock.bin` | **32 `zxic` hits, 0 `omci`/`gpon_sdk`/`gpondrv_dev`** | not parsed | none |

Honest caveat: `root_stock.bin` (241 MiB) has no recognizable filesystem superblock - its
printable content is a **flattened device tree** (`zxic,pon-plat`, `zxic,pon-plat-queue-cpuid-map`,
`zxic,pon-plat-rsv-mem-cfg`, `zte,zx279133-pon`, `zxic,msi-1.0-zxic-133`), so it is a
kernel/dtb region rather than a rootfs, and a compressed filesystem inside it would not have
been found. Absence there is not proven; absence everywhere else is.

One find to **discount on purpose**: `device-dumps/sk-d840n_fw_flashing.bin` contains
`omciUpgrade` and `==omci/oam g_iFwMediaPid`, which looks like the missing OMCI client at a
glance. It is an **EM_MIPS, 32-bit little-endian ELF** (`e_machine = 8`), and this SoC is
aarch64 - it cannot have run here, and it belongs to the other ONU in `C:\UGit\SSH`, not to
this board. Also checked and ruled out: `download.ko` is a TCP **SACK** offload module
(`sack_init`, `sack_update`, `release_sack_list`), not an OMCI firmware downloader, and
`zte_xgpon`'s OMCI chardev is confirmed inert - `gponsdk_omci_devFops` slot `.read` is
literally `mov x0, #0; ret`, so `/dev/gponsdk_dev` offers neither direction.

**Useful by-product:** those same compatible strings appear with identical counts in
`staging/board.dtb`, the tree our OpenWrt actually boots. So the device tree is *already*
the vendor's PON description - `np_133` binds today because of it, and a patched
`zte_xgpon` would bind the same way. The DTB is not a risk item for route A or for task #7.

## 5. What the prepared tree has, for contrast

**This section was wrong until 2026-10-15 and has been rewritten. The original text
said "there is no PON MAC driver", reached by grepping `drivers/net/ethernet/zte` and
finding only an NPPT "WAN path" driver. Both the search and the conclusion were
faulty. Do not use the old wording.**

`drivers/net/ethernet/zte` in `cnjn/linux-mainline-zte-zxslc-sr1010` (6.18.38) really
does contain exactly `ZTE_ZX279133_ETH`, `ZTE_ZX279133_LAN_CONDUIT` and
`ZTE_ZX279133_RTL8372N`, and no file is named after PON. The name is the whole
misleading part:

```
config ZTE_ZX279133_ETH
	tristate "ZTE ZX279133 NPPT Ethernet support"
	depends on PHY_ZX279133_PON_SERDES      <-- the PON SerDes PHY, not optional
	depends on PCS_XPCS
	  "phylink MAC/PCS integration, TX completion, page-pool RX, and the SR1010 WAN path"
```

The NPPT driver **is** the PON MAC. Its device-tree node is
`zte,zx279133-nppt` at `ethernet@19000000`, wired to `pon_serdes` (the PON SerDes PHY)
and `uni_serdes` with `phy-mode = "2500base-x"`, carrying the `ZX279133_TOPCRM_CLK_PON_MAC_WCLK`
and `PON_WOE1_WCLK` clocks, `pcs-handle = <&xpcs1>` and `zte,lan-pcs-handle = <&xpcs0>`.
The vendor DTB's `zte,zx279133-pon` node is not what the mainline path uses, which is
probably what made the original grep look conclusive.

What that driver does *not* look like is a hand-written protocol stack. It loads ZTE's
own packet-engine program and programs the hardware around it:

```
zx279133-np.c:1707   request_firmware(&fw, "zte/zx279133/mcode_intel.bin", eth->dev);
zx279133-np.c:1710        "failed to load PPU microcode\n"
```

`mcode_intel.bin` is a tagged instruction stream for the PPU (`np_downlaod_mcode stores
each file pair high, low`; tag words `0xaaaaaaaa` version, `0xbbbbbbbb` port flow,
`0xcccccccc` dup, `0xdddddddd` skip). The driver gates the accelerated path on the
blob's own identity:

```
hash = rx_hash && fw->size == 103456 &&
       (crc32_le(~0U, fw->data, fw->size) ^ ~0U) == 0xef1d7647;
```

and the blob in `immortalwrt/target/linux/zte/zx279133/firmware/zte/zx279133/` is exactly
103456 bytes with exactly that CRC32, magic `0xffffaaaa` at offset 0 and `inst_num`
12288 at offset 8. (The vendor 4.19 rootfs carries a *different* build of the same
program at `/etc/mcode/mcode_133.bin` - same size and header, CRC32 `0xc839b946` - so
the two are variants of one format, not the same file.)

Also present, and missed by the original search entirely:
`drivers/phy/zte/phy-zx279133-pon-serdes.c`, `drivers/phy/zte/phy-zx279133-uni-serdes.c`,
`drivers/soc/zte/zx279133-xpcs-nppt-audit.c`, and DT bindings for `zte,zx279133-nppt`,
`zte,zx279133-pon-serdes`, `zte,zx279133-uni-serdes`, `zte,zx279133-pon-route` and
`zte,zx279133-idm-cci`.

### Where the protocol layer lives, measured 2026-10-15

The first draft of this section left it as an even either/or: the G.984.1 state machines
are "either inside `mcode_intel.bin` or absent". That was too generous to the tree, and it
is settleable from what is on disk.

The vendor stack runs the ITU state machines **on the CPU, in a loadable module**.
`zte_xgpon.ko` carries them verbatim - verified, not quoted from an earlier draft:

```
power P2--->P3 / power P5--->P6        G.987.1 power machine
Op State Machine, o2_3-o7               G.984.1 ONU machine
O4 p_o4_assigned_onu_id_msg, O7 p_o7_enable_sn_msg
fi_sendPloamMsg, fi_Configure_Ponmac_Send_Omci_Msg
```

`np_133.ko` is the vendor's microcode loader (`/etc/mcode/mcode_133.bin`,
`np_downlaod_mcode`). The mainline driver cites that same vendor symbol -
`zx279133-np.c:1737`, "np_downlaod_mcode stores each file pair high, low", misspelling
included - so the mainline PPU loader was written by reading `np_133.ko`, and it
reimplemented the **packet-engine half only**.

In the kernel that actually gets flashed:

| token | whole-line | substring |
|---|---|---|
| `PLOAM` / `ploam` | 0 | 0 |
| `Op State Machine` | 0 | 0 |
| `p_o7_enable_sn_msg` | 0 | 0 |
| `p_o4_assigned_onu_id_msg` | 0 | 0 |
| `fi_sendPloamMsg` | 0 | 0 |
| `Serena` | 0 | 0 |
| `fi_Configure_Ponmac_Send_Omci_Msg` | 0 | 0 |

while the same tokens appear 76 times (`ploam`), 25 times (`omci`) and once
(`Op State Machine`) in `zte_xgpon.ko`. The detector is calibrated, so the zeros mean
absence: **there is no PON MAC protocol layer in this kernel.**

Two cautions on how that is phrased. A broad vocabulary search is worthless here - `gem`
matches the Marvell PHY driver's Ethernet GEM - so the tokens above are the ones only a
G.984 implementation can produce. And it does **not** follow that the microcode is an
Ethernet-only program.

### The vendor's split is finer than "protocol in a module, rest in the microcode"

`np_133.ko` is not only the loader. It binds `zte,zx279133-pon` **and**
`zte,zx279133-nppt` - the same node the mainline driver binds - and alongside
`np_downlaod_mcode` -> `ppu_set_inst` it exports the PON **hardware table programming**:

```
spa_pon_static_llid_set, epon_get_llid_state, xepon_get_llid_state
pon_sta_llid_{rx,tx,err}_stats_get, se_llid_stat_table_data_get
uopc_set_tcont_mode, uopc_set_init_tcont_req, uopc_set_tcont_sch_active_ena
red_set_tcont_{in,out}_share_max
gemport_vlan_map_table_{get,update,del_by_node_info}
ppu_cluster_set_pkt_type_detail     "set ok. pkt_type_num = %d"
spa_set_pkt_type_table, ppu_set_dup_start_num, ppu_debug_hash_set_tbl
tm_tcont_*                            352 functions: queue scheduling, sharp, secondary
```

So the vendor runs PON as three layers: ITU state machines on the CPU
(`zte_xgpon.ko`), table programming on the CPU (`np_133.ko`), and packet execution in
the PPU microcode against those tables.

Counted with word-boundary-anchored patterns, calibrated on the one mainline file that
must mention the PPU (129 hits), the prepared tree programs **two** of those tables:

| | mainline |
|---|---|
| packet-type table | present |
| dup table | present |
| LLID / T-CONT / GEM port / alloc-id / LPM | **absent** |
| PLOAM / OpState | **absent** |

Two false positives had to be removed to get that table right: an unanchored `t_cont`
matched `reset_conT_CONTrol`, and `SMCT_COS_TH0` is the select-block priority threshold -
Ethernet QoS, not a PON T-CONT.

### What is still unknown, and cannot be answered from the tree

Whether the PPU microcode executes those tables as GEM/T-CONT demux. The symbols show the
tables exist and that the CPU programs them; they do not show the PPU interpreting them as
PON. Proving that needs a disassembly of the program, and capstone is not installed here.

**But that question no longer decides anything.** Mainline programs no LLID, no T-CONT and
no GEM port table, so there is nothing for a microcode to act on whether or not it could.
The missing piece is code, and its absence is a fact about the tree rather than a
measurement - which means the pending PON decision is not waiting on a board window.

And none of this is runtime evidence. No netdev has been observed, no link, no PON
state. The prepared tree contains a coherent PON **data path** - and, measured now, an
Ethernet-flavoured use of the packet engine: packet-type and dup tables only.

### A second correction, about "syscon"

An earlier draft of this project's own analysis - repeated in a comment shipped in the
release workflow - said `"syscon"` is not in this kernel, so `zte,zx279133-pon-route`,
`-idm-cci` and `-wdt-reset` would bind to nothing. That is wrong too, for a reason worth
recording. This tree's `drivers/mfd/syscon.c` has **no `of_device_id` table and no
platform driver at all**; it registers on demand:

```c
struct regmap *syscon_node_to_regmap(struct device_node *np)
{
	return device_node_get_regmap(np, of_device_is_compatible(np, "syscon"), true);
}
```

Every one of those nodes carries `"syscon"` as its fallback compatible, so the regmap is
created at lookup time with no separate driver ever matching the node. A node-to-driver
table cannot see that - which is exactly how a clean, confident, wrong answer gets
produced. `CONFIG_MFD_SYSCON` is on: the built kernel carries `__regmap_init_mmio_clk`,
`altr_sysmgr_regmap_lookup_by_phandle`, and `drivers/mfd/syscon.c`'s own
`pr_err("regmap init failed")`.

`zte,pon-route` and `zte,idm-cci` are nonetheless **mandatory** for the NPPT driver -
`zx279133-main.c` returns `dev_err_probe()` on either `IS_ERR`, and the binding lists
both under `required:`. So they are a hard dependency that currently resolves.


## 6. The upstream PON work we can actually learn from (`pbs05`)

`pbs05/openwrt-pon-drivers` and `pbs05/openwrt-pon-userspace` (default branch `main`,
fetched into `pon/upstream-ponwrt/`) are **open source, for Airoha AN7581/AN7583**.
Their own status table: XG-PON ✅ tested, 10G-EPON 10G/1G ✅, GPON ❌, XGS-PON ❓.

Structure, which is the transferable part:

```
openwrt-pon-drivers/
  airoha-xpon/src/         airoha-xpon-{main,regs.h,netlink,omci,ploam,xgpon,epon,
                           oam,crypto,identity,mode,diagnostics,leds,control}.c
  airoha-pon-frontends/src/ airoha-en7572.c, airoha-paged-bosa-{main,gn28l95,ux3363}.c
openwrt-pon-userspace/
  airoha-pon-daemons/src/src/  Rust: omci/{protocol,mib,provisioning,security,schema,
                               backend,config,control}, oam/{ieee,ctc,frame,config},
                               transport, control, agent, runtime, wire
  airoha-ponctl/src/           Rust CLI + netlink client
  luci-app-pon/, luci-app-iptv/  LuCI views + uci config + menu/acl json
```

Notes that matter for a ZTE port:

- They put the **protocol logic in the kernel driver** (PLOAM, OMCI framing, GEM, AES,
  serial/identity) and publish it over a **netlink family** (`airoha-xpon-netlink.h`),
  then do M.E./provisioning policy in Rust userspace. Our vendor stack is the same
  shape but with the control ABI being `ioctl` on `/dev/gponsdk_dev` (major 111) plus a
  `/proc` command language, and no userspace at all.
- **`airoha-paged-bosa-gn28l95.c` drives a GN28L95** - the same optics part family this
  board loads (`GN28L95.ko`/`GN28L96.ko`, `optical.ko`). Their page-based BOSA register
  model is readable reference for the optical layer, and the optical layer on our side
  is I2C (`zx_i2c` driver is registered, `i2c-dev`/`i2c-adapter` classes exist), so it is
  the one piece of the PON path that is *not* tied to the ZTE PON MAC.
- Licences: `LICENSES/GPL-2.0` and `LICENSES/Apache-2.0` in the userspace feed; package
  names, uci schema and the LuCI menu/acl layout can be followed without copying code.

None of their code loads on `zte/zx279133` - it is Airoha register-level code.

## 7. Routes, with what each one needs as evidence

**A. Keep 4.19, add the missing userspace.** Cheaper than it looked when this file was
first written, and now partly done. `pon/gpondrv-ioctl.txt` gives the macro ids and the
136-byte struct, `pon/zte_xgpon-proc-cli.txt` gives 44 commands that need no client at
all, and `ci-build-repo/tools/ponctl.c` + `build-ponctl.yml` produce a static aarch64
binary that has already been run on the unit: `ponctl status` reads the ONU state through
`/dev/gpondrv_dev` (auto-`mknod`) and agrees with `/proc/gpon_sdk/debug` (§4f). The
kernel->userspace channel is the built-in Monitor **netlink** (protocols 25-26-27/28), not
an ioctl we still have to guess. The `gponsdk_dev` (major 111) half of this route should
be **dropped** - its read and ioctl handlers are `return 0` stubs.

What is actually left for A: an OMCI ME/MIB daemon - and §4h shows its transport **does not
exist yet in either direction on the data path**. Receive is proven (OMCI frames reach
`omci_recv`, which forwards onto netlink protocol 26 as a Monitor multicast, and `ponctl
listen 26 0 1` is the listener). Transmit has no door at all: no ioctl macro, no working
netdev xmit, and `fi_Configure_Ponmac_Send_Omci_Msg` is a stub. So A is no longer
"4.19 + a userspace package"; it is "4.19 + one small kernel patch to `zte_xgpon` + a
userspace daemon". The patch is a queue-to-MAC implementation plus one exported entry
point, which is far less than route B and far more than a config change.
Whether the OLT you will actually meet needs OMCI at all is the question that decides if
this matters; with no light on the receiver that is still unproven (task: §5 of the task
list, and it cannot be answered from here).
Everything else about A is measured.
Consequence unchanged: no nftables, no WireGuard, no `sch_*`, no kmods from any current
OpenWrt release - exactly the limits that started the mainline work.

**B. Write a mainline PON MAC driver.** ~~Needs the register map of
`zte,zx279133-pon@17000000` (16 MiB window), the PON serdes + PLL bring-up, the IDM/BMU
buffer model (~52 MiB reserved, fixed queue/RED table), PLOAM/G.984-1 discovery, then
GEM/T-CONT and OMCI. That is the size of the vendor's `np_133` + `zte_xgpon` combined,
done from scratch against a chip with no public manual. The realistic first milestone is
"PON serdes locked and PLOAM received", which is already a reverse-engineering project.~~

**Rewritten 2026-10-15: the premise of this route was wrong, so its cost estimate is
void.** §5 was rewritten after reading the prepared tree's actual sources. The register
map, the SerDes + PLL bring-up, the IDM/BMU buffer model and the queue/RED programming
are **already implemented** in `cnjn/linux-mainline-zte-zxslc-sr1010` - the NPPT driver,
`phy-zx279133-pon-serdes.c`, `zx279133-idm.c`, the two XPCS nodes, and ZTE's own PPU
microcode blob, all wired and enabled in the compiled dtb. Nobody has to reverse-engineer
the packet engine from scratch; someone already did, and shipped the program as
`mcode_intel.bin`.

What is genuinely still open is narrower and cheaper than route B as written:

1. **Does the PPU microcode carry G.984.1 framing?** Narrowed 2026-10-15: the state
   machines are **not** in the kernel - zero occurrences of `PLOAM`, `Op State Machine`,
   `p_o7_enable_sn_msg`, `fi_sendPloamMsg`, `Serena` in the binary, against 76/1/25
   occurrences in `zte_xgpon.ko`. The vendor ran the protocol on the CPU in that module,
   and the mainline tree reimplemented only the packet engine. What remains open is
   narrower: whether the microcode's packet-type and port-flow tables do GEM/T-CONT
   framing on their own. A boot log answers it; the source does not.
2. **Does it come up?** No runtime evidence exists: no netdev, no link, no PON state.
   And note the ceiling: even a fully working data path is not a working PON, because
   nothing implements the protocol above it.
3. So the from-scratch work is narrower than the original route B but is real: the
   datapath is done, and the remaining piece is the G.984.1 MAC - which on the vendor
   side is what `zte_xgpon.ko` (309 KB) contains. That is a protocol-layer job against a
   register block whose programming sequence can be read out of that module, not a chip
   bring-up from nothing.

Route B is therefore not "a separate, long project" any more. It is a measurement
followed, possibly, by a much smaller piece of work. The order suggested below
(C, then A) is unchanged; the reason to prefer A over B is weaker than §7 originally
claimed, and the honest statement is that the choice now depends on answer (1).


**C. Reuse the optics layer.** Independent of B and useful now: the BOSA is
I2C-attached and the Airoha project documents a paged GN28L95 protocol. This buys laser
bias/temperature/loss-of-signal diagnostics, not connectivity.

Suggested order (revised 2026-10-15): **C is cheap and honest diagnostics. A is the only
route to a working fibre uplink that is *demonstrated* on this hardware today. B is no
longer "a separate, long project" - see the rewrite above.** The sentence that used to
read "A is the only route to a working fibre uplink on this hardware today" was true of
what had been shown to work; it is no longer a claim that mainline cannot get there, only
that nobody has demonstrated it. The decision that matters is still A vs. mainline, but
it should now be made after measuring (1) above - which needs the board, not more
reading - rather than by drift.

### One measurement retires most of this choice

Everything in question (1) is answered by booting the prepared kernel and watching
`dmesg` for the PPU microcode banner and the netdev, then - if a netdev appears - reading
whether PON state moves. `/proc/gpon_sdk/debug` is not needed for that; the mainline
path has its own surface. So the A-vs-B decision is blocked on ~20 minutes with the board
reachable, not on a research project.


## 8. Reproducing this inventory

```
sh tools/ponsurvey.sh                       # run over ssh on the board; sections 1-7
sh tools/ponprobe.sh                        # read-only /proc/gpon_sdk/debug questions
python tools/ponabi.py > pon/abi-scan.txt   # string-level ABI of the .ko copies
python tools/ponelf.py pon/vendor-modules/zte_xgpon.ko
       sections|symbols|modinfo|ksymtab|imports|funcs|xref|strings [regex]
python tools/pondec.py cli   > pon/zte_xgpon-proc-cli.txt   # /proc/gpon_sdk/debug table
python tools/pondec.py ioctl > pon/gpondrv-ioctl.txt        # /dev/gpondrv_dev macros
python tools/ponevents.py pon/vendor-modules/<m>.ko         # Monitor event ids at call sites
python tools/ponDis.py pon/vendor-modules/netdriver.ko swport_dev_xmit_fin
       # one function, disassembled, with every reloc resolved to a name
python tools/ponDis.py scan pon/vendor-modules/*.ko 0x8c8
       # every access to a struct member, across modules -> staging/offset-8c8.txt
python tools/kcore.py dis|ptrs|dump <hex VA> <len>          # live kernel text/data, kcore
sh tools/getponctl.sh                                     # CI-built aarch64 ponctl + sha256
sh tools/zpush.sh staging/ponctl/ponctl /tmp/ponctl       # then: /tmp/ponctl status
sh tools/ow.sh < staging/omcitx.sh                        # the OMCI transmit question, §4h
sh tools/ow.sh < staging/txsweep.sh                       # every PON netdev, same probe
python tools/stocksearch.py backups/mtd10_osgi0.bin       # inflate every stream in a stock
       # dump and grep the decoded bytes (section 4i)
```

`kcore.py` hardcodes the `VA -> offset` pair of the kcore `PT_LOAD` that covers kernel text
(`KBASE 0xffffff8008080000` = `_text`, `KOFF 0x8082000`); re-read the program headers if
the kernel image changes.

Module copies: `pon/vendor-modules/*.ko` (7.4 MB, 18 files, md5 as on the device can be
re-taken with `md5sum`). pbs05 files: `pon/upstream-ponwrt/` (flat, `/` replaced by
`_` in names).

`ponelf.py`/`pondec.py` need a CPython with capstone; on this PC that is
`C:/Python313/python.exe`, not the `python3` stub on PATH.
