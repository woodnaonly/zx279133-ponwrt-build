# 25 — The four jacks' PHY: the tree's contract, and how the 9132 does not fit it (2026-10-21)

## 0. What this document settles

Run 100 was the first boot in which the 9132 registered as a DSA switch, and
it left exactly one thing broken:

```
lan1..lan4 (uninitialized): failed to connect to PHY: -ENODEV
error -19 setting up PHY for tree 0, switch 0, port 4..7
```

The CPU port came up, the conduit came up, the tagger registered with the
transport VIDs 59..62 — and the four jacks did not exist, because a DSA user
port is not a netdev until its PHY is attached.

This document is the reading of the tree that turns that error into a plan.
It answers four questions, all of them answered by the tree's own source at
the pinned SHA (`96e5a2413524742e177e383fe523ef21de5ed238`) and not by
inference:

1. **What does DSA expect a user port's PHY to be?**  A device on
   `ds->user_mii_bus`, at an MDIO address equal to the port number.
2. **Why is there no such device?**  Because the bus does not exist: nothing
   in this driver gives the switch one, and the core only makes one when the
   switch driver offers `ops->phy_read`.
3. **Why would making one not be enough?**  Because the core only *looks* the
   PHY up (`mdiobus_get_phy` is a table lookup, it creates nothing), and no
   driver in this tree claims the id this chip answers.
4. **What is the smallest change that gets the four jacks on the air?**  A
   `fixed-link` on the four port nodes — the one thing phylink needs no PHY
   for — and, to make that a temporary answer rather than the final one, the
   PHY's own register file in the log.

## 1. The tree's contract, quoted

`net/dsa/port.c`, in the comment above the shared-port workarounds:

```
 * User ports with no phy-handle or fixed-link are expected to connect to an
 * internal PHY located on the ds->user_mii_bus at an MDIO address equal to
 * the port number. This description is still actively supported.
```

`net/dsa/user.c`, the port's PHY setup:

```c
static int dsa_user_phy_setup(struct net_device *user_dev)
{
	...
	ret = phylink_of_phy_connect(dp->pl, port_dn, phy_flags);
	if (ret == -ENODEV && ds->user_mii_bus) {
		/* We could not connect to a designated PHY or SFP, so try to
		 * use the switch internal MDIO bus instead
		 */
		ret = dsa_user_phy_connect(user_dev, dp->index, phy_flags);
	}
	...
}

static int dsa_user_phy_connect(struct net_device *user_dev, int addr, u32 flags)
{
	...
	user_dev->phydev = mdiobus_get_phy(ds->user_mii_bus, addr);
	if (!user_dev->phydev) {
		netdev_err(user_dev, "no phy at %d\n", addr);
		return -ENODEV;
	}
	...
}
```

Two things follow from those two functions, and they are the whole story:

* the port number **is** the MDIO address on that bus, and
* `mdiobus_get_phy()` only looks: `mdiobus_find_device()` returns
  `bus->mdio_map[addr]`, and the map is filled by `mdiobus_register_device()`.
  A PHY that nobody registered is not found, whatever it answers.

## 2. The bus the core would make, and why there is none

`net/dsa/dsa.c`, `dsa_switch_setup()`:

```c
	/* Initialize ds->phys_mii_mask before registering the user MDIO bus
	 * driver and before ops->setup() has run ...
	 */
	ds->phys_mii_mask |= dsa_user_ports(ds);
	...
	err = ds->ops->setup(ds);
	...
	if (!ds->user_mii_bus && ds->ops->phy_read) {
		ds->user_mii_bus = mdiobus_alloc();
		...
		dsa_user_mii_bus_init(ds);
		err = mdiobus_register(ds->user_mii_bus);
```

So the core makes a user bus only for a switch driver that offers
`ops->phy_read`, and the bus it makes is a *translator*:
`dsa_user_phy_read()` forwards to `ds->ops->phy_read(ds, port, reg)` for the
addresses in `ds->phys_mii_mask` — the user ports — and refuses the rest.

**This driver has no `ops->phy_read`, so no bus was made, so
`ds->user_mii_bus` was NULL, so the `if (ret == -ENODEV && ds->user_mii_bus)`
fallback never ran.** The error the log shows is the *first* one —
`phylink_of_phy_connect()` found no `phy-handle` and no `fixed-link` on the
port node and answered `-ENODEV` — and it was fatal because there was no
second chance.

### 2.1 The tree's own example of the right way

The tree has exactly one other switch driver, and it is the same SoC family
with the same shape — a switch with internal PHYs behind the jacks:
`drivers/net/ethernet/zte/zx279133-rtl8372n.c`.

```c
static int rtl8372n_mdio_setup(struct dsa_switch *ds)
{
	...
	bus = mdiobus_alloc();
	...
	bus->priv = priv;
	bus->name = "RTL8372N internal MDIO";
	snprintf(bus->id, MII_BUS_ID_SIZE, "%s-mii", dev_name(ds->dev));
	bus->read_c45 = rtl8372n_mdio_read_c45;
	bus->write_c45 = rtl8372n_mdio_write_c45;
	bus->parent = ds->dev;
	bus->phy_mask = ~ds->phys_mii_mask;
	ds->user_mii_bus = bus;

	ret = mdiobus_register(bus);
	...
}
```

and a PHY driver that claims the devices on that bus **by the bus's identity
and the address, not by the PHY's id**:

```c
static int rtl8372n_phy_match(struct phy_device *phydev,
			      const struct phy_driver *phydrv)
{
	struct mii_bus *bus = phydev->mdio.bus;

	return bus && bus->read_c45 == rtl8372n_mdio_read_c45 &&
	       phydev->mdio.addr >= RTL8372N_USER_PORT_MIN &&
	       phydev->mdio.addr <= RTL8372N_USER_PORT_MAX;
}
```

**That the RTL8372N does not have to create its PHY devices by hand is the
part that matters.** `drivers/net/phy/mdio_bus_provider.c`,
`__mdiobus_register()`:

```c
	if (bus->read) {
		err = mdiobus_scan_bus_c22(bus);
		...
	}
```

and `mdiobus_scan_bus_c22()` calls `mdiobus_scan_c22()` for every address
the bus's `phy_mask` allows, which calls `get_phy_device()` — and that reads
the id of the address and makes a `phy_device` of it, registers it, and lets
the driver match it.  So **a bus with a `read` that answers the PHY's id is
enough**: the core creates the devices.

## 3. Why the 9132 does not fit that example, in three places

| | RTL8372N (SR1010) | 9132 (SK-D840N) |
|---|---|---|
| where the PHYs are | on the switch's own MDIO controller | on the **SoC's** MDIO controller #0 (`zx279133,uni-phy-bus = <&mdio0>`) |
| at which addresses | 4..7, the DSA port numbers | **0x0a..0x0d** |
| how they answer | Clause 45 | **Clause 22** |

The addresses are not a guess: the vendor's own device tree carries a
`uni-phy` property on both MDIO controllers, and it is the array
`{ 0x0a, 0x0b, 0x0c, 0x0d, 0x05, 0x1c, 0x02 }`, where 0x0a..0x0d are the
four internal UNI PHYs.  The NPPT driver already reads and writes them there
(`zx279133_9132_phy_enable()`), and run 100 printed what they answer:

```
9132 UNI PHY 0a: id 84b9:5032, BMCR 1040->1040, link 0
9132 UNI PHY 0b: id 84b9:5032, BMCR 1040->1040, link 1
9132 UNI PHY 0c: id 84b9:5032, BMCR 1040->1040, link 0
9132 UNI PHY 0d: id 84b9:5032, BMCR 1040->1040, link 0
```

0x0b is the jack the test PC is in, and it is the only one with a link.

### 3.1 The id nobody claims

`0x84b9:0x5032` over Clause 22 is the 32-bit id `0x84b95032`.  No driver in
this tree matches it:

* `drivers/net/phy/zx279051.c` is the vendor's own PHY driver, and it uses
  `PHY_ID_MATCH_EXACT(0x000084b9)` — a *different* id (the ZX279051 reports
  `0x84b9:0x0000`) — and it reads it over Clause 45, twice, out of the AN
  MMD's DEVID1.  These PHYs answer in Clause 22.
* `drivers/net/phy/realtek/realtek_main.c` matches the Realtek vendor id
  `0x001cc9`.
* `drivers/net/phy/phy_device.c`'s `genphy_driver` is
  `phy_id = 0xffffffff, phy_id_mask = 0xffffffff`, and this tree's
  `phy_id_compare()` is a plain masked compare — `!((id1 ^ id2) & mask)` —
  with no "0xffffffff means anything" escape, so it matches nothing but
  itself.

Hence: **the PHY driver has to be ours**, and it has to claim the devices by
the bus, the way the RTL8372N's does.

## 4. What run 102 does, and what it costs

`phylink_fwnode_phy_connect()` (drivers/net/phy/phylink.c) is the function
that answered `-ENODEV`:

```c
	/* Fixed links and 802.3z are handled without needing a PHY */
	if (pl->cfg_link_an_mode == MLO_AN_FIXED || ...)
		return 0;

	phy_fwnode = fwnode_get_phy_node(fwnode);
	if (IS_ERR(phy_fwnode)) {
		if (pl->cfg_link_an_mode == MLO_AN_PHY)
			return -ENODEV;
		return 0;
	}
```

A `fixed-link` child on the port node sets `MLO_AN_FIXED`
(`phylink_parse_mode()`), and the function then returns **before it looks for
a PHY at all**.  So:

```dts
			port@4 {
				reg = <4>;
				label = "lan1";
				phy-mode = "internal";

				fixed-link {
					speed = <1000>;
					full-duplex;
				};
			};
```

…is all it takes for `lan1`..`lan4` to exist, and for the switch
registration to complete.

**What that costs, stated plainly:** a port in fixed-link mode has no carrier
of its own.  It reports 1G/full whether or not a cable is in it, and the
software never learns the negotiated speed or duplex.

What it does *not* cost: the PHYs themselves are untouched.  The NPPT driver
still powers them and turns auto-negotiation on, and the 9132 reads their
link state itself, so traffic only moves when a cable is really there.  The
vendor's own system has exactly this arrangement — the BMCR read at probe is
`0x1040`, auto-negotiation on — and it is what U-Boot left behind.

This is the same trade the board's DTS already makes on the NPPT's own port
(the RTL8226B on jack 4): a fixed link there too, for the same reason —
`phy_init_hw()`'s soft reset and the config_init that follows it left that
PHY reporting `10baseT` and no link, after U-Boot had moved 11 MB through
that very jack.

## 5. The register dump, and why it is in this round

A PHY driver for this chip cannot be written without knowing what the chip
answers.  The register file is therefore dumped once, when the NPPT driver
finds the PHYs:

```
9132 UNI PHY 0a regs 00-1f: 1040 0000 84b9 5032 0000 0000 ...
```

and the 9132 console grew two commands that reach the PHYs over MDIO, so
the same question can be asked of a live board without a rebuild:

```
	echo "m 0b 04" > .../zx279133-9132	# read one PHY register
	echo "mw 0b 00 1040" > .../zx279133-9132	# write one, read it back
```

`cat /sys/kernel/debug/zx279133-9132` prints the switch's port words *and*
the four PHY register files.

## 6. What run 103 does: the real PHY

With the register file in hand, the driver can be written properly, and it
is the tree's own pattern, at `zx279133-switch.c`:

1. a mii_bus of the switch's own, whose `read`/`write` translate the DSA
   port number into the SoC's MDIO address:

   ```c
   static int zx279133_switch_mdio_read(struct mii_bus *bus, int addr, int regnum)
   {
   	...
   	val = mdiobus_read(priv->uni_mdio, ZX279133_SWITCH_DSA_PHY(addr), regnum);
   	...
   }
   ```

   with `bus->phy_mask = ~ds->phys_mii_mask`, so the core's scan looks at
   the four user ports and nothing else;

2. a PHY driver that claims the devices on that bus by the bus's identity
   and the address, not by the id — the RTL8372N's rule, for the RTL8372N's
   reason;

3. `bus->read`/`bus->write` set (not `read_c45`), because these PHYs are
   Clause 22 — which is also what makes the core's own scan create the
   devices.

Two decisions in that driver are already made, and both are about *not*
touching the chip:

* **no `soft_reset`**, so `phy_init_hw()` leaves the PHY as the vendor and
  U-Boot left it;
* **a `config_aneg` that writes nothing but the control register** — read
  BMCR, clear the power-down bit, set auto-negotiation, write it back —
  instead of `genphy_config_aneg()`, which would also write the
  advertisement registers 0x04 and 0x07, whose layout in this chip is not
  known.  Writing registers whose layout is unknown to a chip that is
  already negotiating is how a working link is turned into a silent one.

The capabilities are stated rather than read, for the same reason: the
Clause 22 capability registers are 0x10, 0x11 and 0x12, and until the dump
says what they hold, saying what the hardware is known to do — gigabit,
copper, auto-negotiation — is the honest answer.

## 7. Also in this round: the NAT ruleset that never loaded

The trial's own NAT was never in place.  The ruleset was:

```
		oifname "$WAN" masquerade counter
```

and `masquerade` is a **terminal** statement in nft, so the `counter` behind
it was rejected — `Statement after terminal statement has no effect` — and
the whole table never loaded.  The counter goes in front:

```
		oifname "$WAN" counter masquerade
```

## 8. What is still open

* **The PHY's register file** — this round's dump answers it.
* **The 9132's forwarding**: the vendor's "flood num / group id" is written
  to **table 125**, not to word 17/18 (docs/15 D12 is wrong about that),
  and the isolation matrix is **ERAM table 27** (docs/15 D13).  Neither is
  written by mainline yet.
* **The service VID**: the vendor's UNI ports have SVID 0, while the tree's
  tagger puts 55+port on the conduit.  The 9132's PVID of 59..62 and the
  tagger's VIDs have to be checked against each other on the live board,
  which the console now makes possible in one boot.
