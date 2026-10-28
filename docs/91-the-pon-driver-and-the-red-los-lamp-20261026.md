# 91 — The PON driver, the PON/ETH mux, and the red LOS lamp

**Date:** 2026-10-26
**Subject:** why the red PON lamp is dark on the mainline image, what the
"PON driver" actually is in 6.18, and the one signal on this board that can
drive the lamp without the vendor's 4.19 stack.

---

## 1. The short version

The red lamp is `skyworth:red:los` on gpio0 pin 9 (mainline gpio-9,
active-low).  It is declared by `gpio-leds` with
`default-state = "keep"`, has no trigger and no userspace consumer, so it
stays at the level U-Boot left it at - dark.

The mainline kernel has no GPON protocol stack.  The only PON-related
driver is the NPPT, and the NPPT is the WAN Ethernet MAC, not the GPON MAC:
the PON SerDes is explicitly muxed to Ethernet mode by
`zx279133_pon_serdes_configure_pll()`.  Its `RX_STATUS` bit 0 therefore says
nothing about the fiber, and the `0x1000706` printed at boot is not
evidence of light.

The fiber-presence signal that does exist on this board is the vendor's
`opsd-gpio` - gpio2 pin 1, mainline global gpio-33.  The vendor kernel reads
it with `gpiod_get_value()` in `optical_signal_rxsd_get()`, and its PON
thread turns the red lamp on when that value is 0.  This document records
that chain and the mainline fix that uses the same signal.

## 2. What the mainline "PON driver" is

The board DTS enables `&nppt`, and `zx279133_eth_probe()` registers a WAN
netdev from it:

- `ci-build-repo/kernel-overlays/drivers/net/ethernet/zte/zx279133-main.c:819`
  (`zx279133_eth_probe`)
- `.../zx279133-main.c:834-839` (`devm_alloc_etherdev_mqs` + `SET_NETDEV_DEV`)
- `.../zx279133.h:1007` (`struct zx279133_eth`)
- `.../zx279133-main.c:1294` (`devm_register_netdev`)

That netdev is driven by XMAC1 and XPCS1, and its PHY is the PON SerDes.
The NP/IDM/TM side is a real packet datapath and is what the LAN work has
been exercising, but it is the packet transport around the MAC, not the
GPON TC layer.

What is absent is the other half of an ONU:

- there is no driver for the `zte,zx279133-pon` node at 0x17000000
  (`ci-build-repo/decompiled/sk-d840n-stock.dts:1864-1870`);
- there is no PLOAM, no GEM, no OMCI and no registration state machine;
- the vendor's `zte_xgpon.ko`, `gpondrv_xg.ko` and `zx_ponreg_133.ko` are
  4.19 modules (vermagic `4.19.136`/`4.19.136+`) and cannot be loaded by
  6.18 (`ci-build-repo/pon/INVENTORY.md:121-136`).

So "the PON driver" on the running image is the NPPT's WAN netdev plus the
PON SerDes PHY.  It can move Ethernet frames over the PON SerDes lane, but
it does not speak GPON.

## 3. Why `RX_STATUS` is not the fiber LOS

`drivers/phy/zte/phy-zx279133-pon-serdes.c` is the only mainline code that
touches the PON SerDes.  The relevant facts are:

- `ZX279133_PON_SERDES_RX_STATUS` is 0xe4, and bit 0 is named
  `ZX279133_PON_SERDES_RX_LOS`
  (`ci-build-repo/tests/kernelsrc/drivers_phy_zte_phy-zx279133-pon-serdes.c:39-41`).
- the only two accepted modes are SGMII and 2.5GBASE-X
  (`...:110-120`);
- `zx279133_pon_serdes_configure_pll()` clears `PON_MODE_MASK` and writes
  `ZX279133_TOPCRM_PON_ETH_SEL` (`...:153-192`);
- `power_on()` reads `RX_STATUS` once, skips the CDR wait when bit 0 is set,
  and prints the value (`...:253-326`).

The boot log shows the result:

```
[    1.557645] zx279133-pon-serdes 16000000.phy: SGMII SerDes ready, status 0xc87cbb83/0x1000706
```

`0x1000706` has bit 0 clear and bit 1 (CDR lock) set
(`staging/ctl/.lastboot.txt:1276`).  That is the Ethernet side of the mux
reporting a locked CDR, not the BOSA reporting a received optical signal.
The earlier U-Boot line `rx los =0 rx data in` was printed in the vendor's
GPON mode (`staging/ctl/boot419-window.txt:58`); it is not the state the
mainline driver puts the SerDes in.

The conclusion matters for the lamp: **do not wire the red lamp to
`RX_STATUS`**.  It would turn the lamp off for the wrong reason.

## 4. The vendor's real LOS chain

The stock board description puts the optical RX signal detect on a GPIO:

```
pon_plat {
    compatible = "zxic,pon-plat";
    opsd-gpio = <0x15 0x01 0x00>;
    ...
};
```

`ci-build-repo/decompiled/sk-d840n-stock.dts:1807-1816`.  Phandle 0x15 is
`gpio@10d10080` (gpio2) at `...:1305`, so the line is **gpio2 pin 1**.  The
mainline gpiochips enumerate as 0/16/32/48/64 (the trial beacon uses
gpio0-10, gpio1-5, gpio2-3, gpio3-15, gpio4-1 as 10, 21, 35, 63, 65), so
this is **global gpio-33**.

The vendor kernel's own reader is built in:

- `optical_signal_rxsd_get` is at `staging/kallsyms-ffffff8008.txt:14717`;
  disassembling `staging/ctl/Image` at file offset 0x2fdeb0 shows it calls
  `gpiod_get_value()` (runtime `0xffffff8008328b54`).
- `pon_plat_probe` is at `...:14753`; its body at file offset 0x2fff50 calls
  `gpiod_get_from_of_node(node, "opsd-gpio", 0, 0, "opsd")` and prints
  `Failed to get optical rxsd signal` on failure (string at file offset
  0x86edf9).  The vendor dmesg contains that exact line at boot
  (`staging/ctl/vendor-dmesg-20261023.txt:322`).

That failure is almost certainly `-EPROBE_DEFER`: the vendor probe does not
retry.  It is not proof that the wire is absent, and it is the main reason
the vendor's own lamp control is unreliable on this unit.

Above the GPIO reader, `gpondrv_xg.ko` has the LOS LED thread.  Its
relocations name the parts: `optical_signal_rxsd_get` at `.text:0x15b0`
and `hw_led_los_ioctl` at `.text:0x15e0/0x1614/0x1648/0x1674`, with
`g_los_led_state` in `.data`.  The disassembly of the thread is
unambiguous about polarity:

- `rxsd == 1` -> branch to the "signal present" path -> `hw_led_los_ioctl(0)`
  and `g_los_led_state = 0` (lamp off);
- `rxsd != 1` -> `hw_led_los_ioctl(1)` and `g_los_led_state = 1`
  (lamp on).

So **1 = light present, 0 = LOS**, matching the active-high `opsd-gpio`
flags, and the red lamp must be on when the value is 0.

The rest of the vendor stack is consistent with that: `bspdriver.ko`
registers `zxic,zx-leds` and exposes `/dev/zxled`; `gpon_omci` calls
`OmciAdapter_Set_LED_State`; the stock `sky_bicolour_led_op` script toggles
`led_pon` and `led_los` brightness once a second as a factory self-test
(`staging/g5root/mtd3_root/bin/sky_bicolour_led_op:13-18`).  None of it is
loadable on 6.18.

## 5. The panel wiring, and why the lamp is dark

The board DTS declares seven GPIO LEDs
(`ci-build-repo/kernel-overlays/arch/arm64/boot/dts/zte/zx279133-sk-d840n.dts:193-262`).
The two that matter here are:

| node | color | GPIO | mainline global | polarity | default-state |
|---|---|---|---|---|---|
| `led-pon` | green | `&gpio3 15` | 63 | active-low | keep |
| `led-los` | red | `&gpio0 9` | 9 | active-low | keep |

`led-los` has no `linux,default-trigger` and the image has no script that
writes its brightness.  The mainline PON path never touches it.  The vendor
image on the same board did have a red lamp path, but that path lived in
the 4.19 `gpondrv_xg`/`bspdriver`/`gpon_omci` stack.

The green lamp is the registration indicator, not the fiber indicator.  It
must stay off until there is a PLOAM/OMCI state machine to justify it.

## 6. The fix in this tree

The fix uses the vendor's own signal and mirrors the vendor's own decision:

- DTS: `led-los` gets a `los_led` label and `default-state = "on"`, so a
  board with no fiber is visibly red from gpio-leds probe even before the
  driver runs.
- DTS: `&nppt` gains `leds = <&los_led>`, `led-names = "los"` and
  `opsd-gpios = <&gpio2 1 GPIO_ACTIVE_HIGH>`.
- `zx279133_eth_probe()` obtains the optional GPIO and LED, and a 200 ms
  delayed work sets the lamp:
  - `gpiod_get_value_cansleep()` returns 0 -> `led_set_brightness(...,
    max_brightness)` (red on);
  - returns 1 -> `LED_OFF` (red off).

This is the same shape as the vendor's `gpondrv_task_thread`: read the
optical RX signal detect, set the LOS lamp.  It does not pretend to know
about registration, and it does not touch the green lamp.

The new `ledtrig-gpio` in 6.18 was considered and rejected: it was rewritten
around the generic `trigger-sources` binding and no longer exposes the old
`gpio`/`invert` sysfs attributes.  The driver-side poll keeps the logic in
the one driver that already owns the PON lane and needs no new kernel
config or userspace helper.

## 7. What is proven and what is not

Proven from the repository and the vendor binaries:

- the red lamp is gpio-9, active-low, and nobody drives it on 6.18;
- the mainline SerDes is in Ethernet mode, so its `RX_STATUS` bit 0 is not
  a fiber LOS;
- the vendor's fiber signal is gpio2 pin 1 (global gpio-33), active-high,
  and its PON thread treats 0 as LOS.

Not proven on hardware yet:

- that gpio-33 actually toggles when a fiber is inserted or removed;
- the exact reason the vendor's `pon_gpio_init` failed (EPROBE_DEFER is the
  likely one);
- whether the mainline pinctrl leaves the pin in a state where the GPIO
  input works.

The first live round must measure gpio-33 with the fiber out and in.  If it
does not move, the fallback is the BOSA I2C RX_LOS path (the vendor's
`optical.ko`/`bob_get_rx_los_status` reads BOSA registers 110/187) or the
PON MAC LOS register behind `fi_Configure_Ponmac_Get_LOS_Status`; both are
open items, and neither is needed to make the no-fiber case visible,
because `default-state = "on"` already fails safe.
