// SPDX-License-Identifier: GPL-2.0-only
/*
 * ZTE ZX279133 on-board LAN switch (9132) driver, for the SK-D840N.
 *
 * The 9132 is this board's LAN switch and it sits exactly where the RTL8372N
 * sits on the SR1010: on the SoC's XMAC0 LAN lane, with its CPU port facing
 * the SoC.  The evidence is in
 * docs/16-9132-topology-and-driver-design-20261021.md and
 * staging/ctl/g2c-vendor-answers.md:
 *
 *   - The vendor prints "xmac0_pcs_base is 0x140000" and "xmac0 is used"
 *     before it declares its MII devices, and the first of those is "sw",
 *     which is the 9132's CPU port.  0x140000 is XMAC0_BASE, the window
 *     mainline's header calls "XMAC0 carries the LAN".
 *   - mainline's own NPPT driver already programs the 9132's port words
 *     (zx279133_9132_port_init()) and its LAN path says, in
 *     zx279133_lan_port_bringup(): "LAN frames reach XMAC0 (SPA memory 5)".
 *     The PPU port 5 that carries the LAN is XMAC0, and XMAC0 is a window
 *     nothing in this tree programs on this board - which is why no LAN
 *     frame has ever moved.
 *   - The lane is 1G.  The bootloader left the Uni SerDes in the 1G state
 *     that moved 11 MB through it, and this kernel's Uni SerDes driver
 *     reports that state as already correct ("Uni SerDes 1G inherited:
 *     mode 0x1 already, image differs in 0/49 word(s)").
 *
 * So this driver is the 9132's counterpart of the tree's RTL8372N driver:
 * it owns the XMAC0 datapath, the XPCS0 lane and the Uni SerDes that
 * carries them, and it is a child of the NPPT controller, talking to it
 * through the typed service in zx279133-lan.h.
 *
 * The XPCS programming below is the tree's own 1G SGMII profile, taken
 * from zx279133_xmac_sgmii_init() and the zx279133_xpcs_* helpers in
 * drivers/net/ethernet/zte/zx279133-mac.c - the same vendor sequence the
 * NPPT's own MAC runs - pointed at XPCS0 instead of XPCS1 and at XMAC0
 * instead of XMAC1.  The XMAC register image and the reset word are the
 * RTL8372N driver's, which are the same block seen from the other side.
 *
 * What it does NOT do yet, and why:
 *
 *   - Per-port netdevs.  The plan's G2 gate asks for eth1/eth2/eth3, one per
 *     UNI port.  The parent service owns exactly one conduit netdev
 *     (zx279133_lan_netdev_setup() stores a single eth->lan_ndev), and the
 *     four per-port netdevs on the SR1010 are DSA's, with the transport VID
 *     tagger on the conduit.  Until the 9132 side can steer a tagged frame
 *     to one chosen UNI port - which needs the VLAN tables, not just the
 *     port attributes - there is nothing to tag.  One conduit netdev over
 *     the whole flat bridge is what works today, and it is enough to prove
 *     the path.
 *   - The 9132's port attributes and its internal PHY enable.  Those belong
 *     to the NPPT driver, which reaches the 9132 over the PPU's indirect
 *     register window: zx279133_9132_port_init() writes the port words and
 *     zx279133_9132_phy_enable() powers the four internal PHYs over MDIO
 *     controller #0.  This driver only owns the SoC side of the CPU port.
 */

#include <linux/bitfield.h>
#include <linux/delay.h>
#include <linux/device.h>
#include <linux/ethtool.h>
#include <linux/mdio.h>
#include <linux/mii.h>
#include <linux/mod_devicetable.h>
#include <linux/module.h>
#include <linux/of.h>
#include <linux/of_mdio.h>
#include <linux/phy.h>
#include <linux/phy/phy.h>
#include <linux/platform_device.h>
#include <linux/pm_runtime.h>
#include <linux/slab.h>

#include "zx279133-lan.h"

/* XMAC0, the LAN window.  Same offsets the RTL8372N driver uses. */
#define ZX279133_XMAC0_BASE		0x140000
#define ZX279133_XMAC_TX_CTRL		0x0000
#define ZX279133_XMAC_RX_CTRL		0x0010
#define ZX279133_XMAC_RX_IPC		BIT(9)
#define ZX279133_XMAC_FRAME_CFG		0x0020
#define ZX279133_XMAC_MODE_CFG		0x0280
#define ZX279133_XMAC_DUPLEX		0x0500
#define ZX279133_XMAC_MISC_CFG		0x3400
#define ZX279133_XMAC_HALF_DUPLEX	BIT(24)
#define ZX279133_XMAC_ENABLE		BIT(0)
#define ZX279133_XMAC_SPEED_MASK	GENMASK(31, 29)
/* xmac_set_speed_sel(): 3 = 1G. */
#define ZX279133_XMAC_SPEED_1G		3
#define ZX279133_SYS_SOFT_RESET		0x2c0004
#define ZX279133_XMAC0_RESET		BIT(10)
#define ZX279133_XMAC_RESET_US		1718

#define ZX279133_XMAC0_SOPC_SEND	0x342c0
#define ZX279133_XMAC_SOPC_DUPLEX	0x343f0
#define ZX279133_XMAC0_SOPC_DUPLEX_MASK BIT(4)

/* NPPT-relative status words, as the RTL8372N driver reads them. */
#define ZX279133_NPPT_XMAC_LINK		0x0084
#define ZX279133_NPPT_XPCS0_STATUS	0x0090

/*
 * The XPCS 1G SGMII profile, recovered from the vendor's
 * xpcs_1g_mode_conf() and xpcs_auto_negotiation_conf_in_sgmii_mode() and
 * carried in zx279133-mac.c for the NPPT's own PCS.
 */
#define ZX279133_XPCS_VR_MII_DIG_CTRL1	0x8000
#define ZX279133_XPCS_VR_MII_AN_CTRL	0x8001
#define ZX279133_XPCS_VR_XS_PCS_DIG_CTRL1 0x8000
#define ZX279133_XPCS_VR_XS_PCS_XAUI_CTRL 0x8004
#define ZX279133_XPCS_VR_XS_PCS_DIG_STS	0x8010
#define ZX279133_XPCS_MAC_AUTO_SW	BIT(9)
#define ZX279133_XPCS_2G5_EN		BIT(2)
#define ZX279133_XPCS_AN_INTR_EN	BIT(0)
#define ZX279133_XPCS_SGMII_LINK_STS	BIT(4)
#define ZX279133_XPCS_PCS_MODE_MASK	GENMASK(2, 1)
#define ZX279133_XPCS_LOW_POWER		BIT(11)
#define ZX279133_XPCS_SPEED_SEL		BIT(13)
#define ZX279133_XPCS_SR_MII_SPEED_MASK (BIT(13) | BIT(6) | BIT(5))
#define ZX279133_XPCS_DUPLEX		BIT(8)
#define ZX279133_XPCS_PSEQ_MASK		GENMASK(4, 2)
#define ZX279133_XPCS_PSEQ_IDLE		0x4
#define ZX279133_XPCS_BYPASS_REG	0x8005
#define ZX279133_XPCS_BYPASS_EN		BIT(4)

/* The 9132's four UNI ports are the switch ports the copper jacks sit on. */
#define ZX279133_SWITCH_UNI_PORT_MIN	0
#define ZX279133_SWITCH_UNI_PORT_MAX	3

struct zx279133_switch {
	struct zx279133_lan_service *service;
	struct phy *serdes;
	struct mdio_device *xpcs_mdiodev;
	bool datapath_held;
	bool xpcs_runtime_held;
	bool xpcs_configured;
	bool xmac_configured;
	bool datapath_enabled;
	bool serdes_powered;
	u16 saved_vend2_bmcr;
	u16 saved_vend2_an_ctrl;
	u16 saved_xpcs_bypass;
	u32 saved_xmac_tx;
	u32 saved_xmac_rx;
	u32 saved_xmac_frame;
	u32 saved_xmac_mode;
	u32 saved_xmac_duplex;
	u32 saved_xmac_misc;
	u32 saved_xmac_reset;
	u32 saved_sopc_send;
	u32 saved_sopc_duplex;
};

static u32 zx279133_switch_nppt_read(struct zx279133_switch *priv, u32 offset)
{
	return zx279133_lan_service_nppt_read(priv->service, offset);
}

static void zx279133_switch_nppt_write(struct zx279133_switch *priv, u32 offset,
				       u32 value)
{
	zx279133_lan_service_nppt_write(priv->service, offset, value);
}

static void zx279133_switch_xmac_lock(struct zx279133_switch *priv)
{
	zx279133_lan_service_xmac_lock(priv->service);
}

static void zx279133_switch_xmac_unlock(struct zx279133_switch *priv)
{
	zx279133_lan_service_xmac_unlock(priv->service);
}

/*
 * The XPCS is a C45 device on the MDIO bus the XPCS platform device owns.
 * That bus is a direct MMIO window onto the PCS registers, so these writes
 * reach the 0x1a000000 block; the external ZX279133 MDIO controller would
 * not.
 */
static int zx279133_switch_xpcs_read(struct zx279133_switch *priv, int devad,
				     int reg)
{
	return mdiodev_c45_read(priv->xpcs_mdiodev, devad, reg);
}

static int zx279133_switch_xpcs_write(struct zx279133_switch *priv, int devad,
				      int reg, u16 val)
{
	return mdiodev_c45_write(priv->xpcs_mdiodev, devad, reg, val);
}

static int zx279133_switch_xpcs_modify(struct zx279133_switch *priv, int devad,
					int reg, u16 mask, u16 set)
{
	return mdiodev_c45_modify(priv->xpcs_mdiodev, devad, reg, mask, set);
}

static int zx279133_switch_xpcs_init(struct zx279133_switch *priv)
{
	int val;
	int ret;

	ret = read_poll_timeout(zx279133_switch_xpcs_read, val,
				val < 0 || !(val & BIT(15)), 859, 859 * 400,
				false, priv, MDIO_MMD_PCS, MII_BMCR);
	if (ret)
		return ret;
	if (val < 0)
		return val;

	ret = read_poll_timeout(zx279133_switch_xpcs_read, val,
				val < 0 || !(val & BIT(15)), 859, 859 * 400,
				false, priv, MDIO_MMD_VEND2, MII_BMCR);
	if (ret)
		return ret;
	if (val < 0)
		return val;

	return zx279133_switch_xpcs_modify(priv, MDIO_MMD_VEND2,
					   ZX279133_XPCS_VR_MII_AN_CTRL,
					   BIT(3) | BIT(8), 0);
}

/*
 * The vendor's AN-disabled 1G path: PCS mode 2 + SGMII_LINK_STS, no
 * MAC_AUTO_SW.  Re-applied after the XMAC has been programmed.
 */
static int zx279133_switch_xpcs_apply_sgmii_vend2(struct zx279133_switch *priv,
						   int duplex)
{
	u32 bmcr = BMCR_ANENABLE | BMCR_SPEED1000;
	int ret;

	if (duplex != DUPLEX_HALF)
		bmcr |= BMCR_FULLDPLX;

	ret = zx279133_switch_xpcs_write(priv, MDIO_MMD_VEND2,
					  ZX279133_XPCS_VR_MII_AN_CTRL,
					  ZX279133_XPCS_SGMII_LINK_STS |
					  ZX279133_XPCS_AN_INTR_EN |
					  FIELD_PREP(ZX279133_XPCS_PCS_MODE_MASK, 2));
	if (ret)
		return ret;

	ret = zx279133_switch_xpcs_write(priv, MDIO_MMD_VEND2,
					  ZX279133_XPCS_VR_MII_DIG_CTRL1,
					  0x2000 | ZX279133_XPCS_MAC_AUTO_SW);
	if (ret)
		return ret;

	return zx279133_switch_xpcs_write(priv, MDIO_MMD_VEND2, MII_BMCR, bmcr);
}

/*
 * The 1G SGMII profile, from zx279133_xpcs_sgmii_quirk() in
 * drivers/net/ethernet/zte/zx279133-mac.c.
 */
static int zx279133_switch_xpcs_sgmii(struct zx279133_switch *priv)
{
	int val;
	int ret;

	ret = zx279133_switch_xpcs_init(priv);
	if (ret)
		return ret;

	val = zx279133_switch_xpcs_read(priv, MDIO_MMD_VEND2, MII_BMCR);
	if (val < 0)
		return val;
	priv->saved_vend2_bmcr = val;
	val = zx279133_switch_xpcs_read(priv, MDIO_MMD_VEND2,
					ZX279133_XPCS_VR_MII_AN_CTRL);
	if (val < 0)
		return val;
	priv->saved_vend2_an_ctrl = val;
	priv->xpcs_configured = true;

	/* xpcs_exit_sgmii_mode(): clear any previous mode state. */
	ret = zx279133_switch_xpcs_modify(priv, MDIO_MMD_VEND2, MII_BMCR,
					   BMCR_ANENABLE, 0);
	if (ret)
		return ret;
	ret = zx279133_switch_xpcs_modify(priv, MDIO_MMD_VEND2,
					   ZX279133_XPCS_VR_MII_AN_CTRL,
					   ZX279133_XPCS_AN_INTR_EN |
					   ZX279133_XPCS_SGMII_LINK_STS, 0);
	if (ret)
		return ret;
	ret = zx279133_switch_xpcs_modify(priv, MDIO_MMD_VEND2,
					   ZX279133_XPCS_VR_MII_DIG_CTRL1,
					   ZX279133_XPCS_MAC_AUTO_SW |
					   ZX279133_XPCS_2G5_EN, 0);
	if (ret)
		return ret;
	ret = zx279133_switch_xpcs_modify(priv, MDIO_MMD_PCS,
					   ZX279133_XPCS_VR_XS_PCS_DIG_CTRL1,
					   ZX279133_XPCS_2G5_EN, 0);
	if (ret)
		return ret;

	/* xpcs_1g_mode_conf(xmac, speed=3, duplex=1, pcs_mode=2). */
	ret = zx279133_switch_xpcs_write(priv, MDIO_MMD_PCS, 7, 1);
	if (ret)
		return ret;
	ret = zx279133_switch_xpcs_modify(priv, MDIO_MMD_PMAPMD, MII_BMCR,
					   ZX279133_XPCS_SPEED_SEL, 0);
	if (ret)
		return ret;
	ret = zx279133_switch_xpcs_modify(priv, MDIO_MMD_PCS,
					   ZX279133_XPCS_VR_XS_PCS_XAUI_CTRL,
					   BIT(0), 0);
	if (ret)
		return ret;
	ret = zx279133_switch_xpcs_modify(priv, MDIO_MMD_PCS, MII_BMCR,
					   ZX279133_XPCS_SPEED_SEL, 0);
	if (ret)
		return ret;
	/*
	 * SGMII SerDes stays 1.25G; 10/100/1000 are in-band.  Vendor
	 * xpcs_sgmii_mode_conf() always programs SR-MII speed 3 / full.
	 */
	ret = zx279133_switch_xpcs_modify(priv, MDIO_MMD_VEND2, MII_BMCR,
					   ZX279133_XPCS_SR_MII_SPEED_MASK, BIT(6));
	if (ret)
		return ret;
	ret = zx279133_switch_xpcs_modify(priv, MDIO_MMD_VEND2, MII_BMCR,
					   ZX279133_XPCS_DUPLEX,
					   ZX279133_XPCS_DUPLEX);
	if (ret)
		return ret;
	ret = zx279133_switch_xpcs_modify(priv, MDIO_MMD_PCS, MII_BMCR,
					   ZX279133_XPCS_LOW_POWER,
					   ZX279133_XPCS_LOW_POWER);
	if (ret)
		return ret;

	ret = read_poll_timeout(zx279133_switch_xpcs_read, val,
				val < 0 ||
				FIELD_GET(ZX279133_XPCS_PSEQ_MASK, val) !=
				ZX279133_XPCS_PSEQ_IDLE,
				859, 859 * 400, false, priv, MDIO_MMD_PCS,
				ZX279133_XPCS_VR_XS_PCS_DIG_STS);
	if (ret)
		return ret;
	if (val < 0)
		return val;

	ret = zx279133_switch_xpcs_modify(priv, MDIO_MMD_PCS, MII_BMCR,
					   ZX279133_XPCS_LOW_POWER, 0);
	if (ret)
		return ret;
	ret = zx279133_switch_xpcs_modify(priv, MDIO_MMD_VEND2,
					   ZX279133_XPCS_VR_MII_AN_CTRL,
					   ZX279133_XPCS_PCS_MODE_MASK,
					   FIELD_PREP(ZX279133_XPCS_PCS_MODE_MASK, 2));
	if (ret)
		return ret;

	/*
	 * xmac_init_by_work_mode(3) calls xmac_sgmii_conf(..., auto=0):
	 * PCS mode 2 + SGMII_LINK_STS, no MAC_AUTO_SW.
	 */
	ret = zx279133_switch_xpcs_write(priv, MDIO_MMD_VEND2,
					  ZX279133_XPCS_VR_MII_AN_CTRL,
					  ZX279133_XPCS_SGMII_LINK_STS |
					  FIELD_PREP(ZX279133_XPCS_PCS_MODE_MASK, 2));
	if (ret)
		return ret;
	ret = zx279133_switch_xpcs_write(priv, MDIO_MMD_VEND2,
					  ZX279133_XPCS_VR_MII_DIG_CTRL1, 0x2000);
	if (ret)
		return ret;

	return zx279133_switch_xpcs_write(priv, MDIO_MMD_VEND2, MII_BMCR,
					 BMCR_SPEED1000 | BMCR_FULLDPLX);
}

static int zx279133_switch_xpcs_bypass(struct zx279133_switch *priv, bool enable)
{
	int val;

	val = zx279133_switch_xpcs_read(priv, MDIO_MMD_PCS,
					 ZX279133_XPCS_BYPASS_REG);
	if (val < 0)
		return val;

	if (enable) {
		priv->saved_xpcs_bypass = val;
		return zx279133_switch_xpcs_modify(priv, MDIO_MMD_PCS,
						    ZX279133_XPCS_BYPASS_REG,
						    ZX279133_XPCS_BYPASS_EN,
						    ZX279133_XPCS_BYPASS_EN);
	}

	return zx279133_switch_xpcs_write(priv, MDIO_MMD_PCS,
					   ZX279133_XPCS_BYPASS_REG,
					   priv->saved_xpcs_bypass);
}

static void zx279133_switch_xpcs_restore(struct zx279133_switch *priv)
{
	if (!priv->xpcs_configured)
		return;

	zx279133_switch_xpcs_write(priv, MDIO_MMD_VEND2,
				    ZX279133_XPCS_VR_MII_AN_CTRL,
				    priv->saved_vend2_an_ctrl);
	zx279133_switch_xpcs_write(priv, MDIO_MMD_VEND2, MII_BMCR,
				    priv->saved_vend2_bmcr);
	priv->xpcs_configured = false;
}

/*
 * The XMAC0 register image, from zx279133_xmac_program() in
 * drivers/net/ethernet/zte/zx279133-mac.c with the 1G speed code.
 */
static void zx279133_switch_xmac_program(struct zx279133_switch *priv)
{
	u32 xmac = ZX279133_XMAC0_BASE;
	u32 value;

	zx279133_switch_nppt_write(priv, xmac + ZX279133_XMAC_TX_CTRL,
				    0x00010000);
	value = zx279133_switch_nppt_read(priv, xmac + ZX279133_XMAC_TX_CTRL);
	zx279133_switch_nppt_write(priv, xmac + ZX279133_XMAC_TX_CTRL,
				    (value & ~ZX279133_XMAC_SPEED_MASK) |
				    FIELD_PREP(ZX279133_XMAC_SPEED_MASK,
					       ZX279133_XMAC_SPEED_1G));

	value = zx279133_switch_nppt_read(priv, xmac + ZX279133_XMAC_DUPLEX);
	zx279133_switch_nppt_write(priv, xmac + ZX279133_XMAC_DUPLEX,
				    value & ~ZX279133_XMAC_HALF_DUPLEX);

	zx279133_switch_nppt_write(priv, xmac + ZX279133_XMAC_RX_CTRL,
				    0x3e800086 | ZX279133_XMAC_RX_IPC);
	zx279133_switch_nppt_write(priv, xmac + ZX279133_XMAC_FRAME_CFG,
				    0x80000001);
	zx279133_switch_nppt_write(priv, xmac + ZX279133_XMAC_MODE_CFG, 2);

	value = zx279133_switch_nppt_read(priv, xmac + ZX279133_XMAC_MISC_CFG);
	zx279133_switch_nppt_write(priv, xmac + ZX279133_XMAC_MISC_CFG,
				    value | BIT(9));
}

/*
 * The XMAC0 bring-up, from zx279133_xmac_sgmii_init(): reset, program,
 * publish the SOPC send enable, then enable the MAC.
 */
static void zx279133_switch_xmac_configure(struct zx279133_switch *priv)
{
	u32 xmac = ZX279133_XMAC0_BASE;
	u32 value;

	zx279133_switch_xmac_lock(priv);
	priv->saved_xmac_tx =
		zx279133_switch_nppt_read(priv, xmac + ZX279133_XMAC_TX_CTRL);
	priv->saved_xmac_rx =
		zx279133_switch_nppt_read(priv, xmac + ZX279133_XMAC_RX_CTRL);
	priv->saved_xmac_frame =
		zx279133_switch_nppt_read(priv, xmac + ZX279133_XMAC_FRAME_CFG);
	priv->saved_xmac_mode =
		zx279133_switch_nppt_read(priv, xmac + ZX279133_XMAC_MODE_CFG);
	priv->saved_xmac_duplex =
		zx279133_switch_nppt_read(priv, xmac + ZX279133_XMAC_DUPLEX);
	priv->saved_xmac_misc =
		zx279133_switch_nppt_read(priv, xmac + ZX279133_XMAC_MISC_CFG);
	priv->saved_xmac_reset =
		zx279133_switch_nppt_read(priv, ZX279133_SYS_SOFT_RESET);
	priv->xmac_configured = true;

	/* Disable XMAC0 and drop the SOPC send enable before the reset. */
	value = zx279133_switch_nppt_read(priv, xmac + ZX279133_XMAC_RX_CTRL);
	zx279133_switch_nppt_write(priv, xmac + ZX279133_XMAC_RX_CTRL,
				    value & ~ZX279133_XMAC_ENABLE);
	value = zx279133_switch_nppt_read(priv, xmac + ZX279133_XMAC_TX_CTRL);
	zx279133_switch_nppt_write(priv, xmac + ZX279133_XMAC_TX_CTRL,
				    value & ~ZX279133_XMAC_ENABLE);
	zx279133_switch_nppt_write(priv, ZX279133_XMAC0_SOPC_SEND, 0);

	/* smac_reset(): preserve the shared reset word and pulse XMAC0 only. */
	zx279133_switch_nppt_write(priv, ZX279133_SYS_SOFT_RESET,
				    priv->saved_xmac_reset &
				    ~ZX279133_XMAC0_RESET);
	usleep_range(ZX279133_XMAC_RESET_US, ZX279133_XMAC_RESET_US + 100);
	zx279133_switch_nppt_write(priv, ZX279133_SYS_SOFT_RESET,
				    priv->saved_xmac_reset |
				    ZX279133_XMAC0_RESET);

	zx279133_switch_xmac_program(priv);

	priv->saved_sopc_duplex =
		zx279133_switch_nppt_read(priv, ZX279133_XMAC_SOPC_DUPLEX);
	priv->saved_sopc_send =
		zx279133_switch_nppt_read(priv, ZX279133_XMAC0_SOPC_SEND);
	value = priv->saved_sopc_duplex & ~ZX279133_XMAC0_SOPC_DUPLEX_MASK;
	zx279133_switch_nppt_write(priv, ZX279133_XMAC_SOPC_DUPLEX, value);
	/* Vendor xmac_init_by_work_mode(): __const_udelay(4295000). */
	usleep_range(4295, 4395);
	zx279133_switch_nppt_write(priv, ZX279133_XMAC0_SOPC_SEND, 1);

	value = zx279133_switch_nppt_read(priv, xmac + ZX279133_XMAC_RX_CTRL);
	zx279133_switch_nppt_write(priv, xmac + ZX279133_XMAC_RX_CTRL,
				    value | ZX279133_XMAC_ENABLE);
	value = zx279133_switch_nppt_read(priv, xmac + ZX279133_XMAC_TX_CTRL);
	zx279133_switch_nppt_write(priv, xmac + ZX279133_XMAC_TX_CTRL,
				    value | ZX279133_XMAC_ENABLE);
	zx279133_switch_xmac_unlock(priv);
	priv->datapath_enabled = true;
}

static void zx279133_switch_xmac_restore(struct zx279133_switch *priv)
{
	u32 xmac = ZX279133_XMAC0_BASE;
	u32 value;

	if (!priv->xmac_configured)
		return;

	zx279133_switch_xmac_lock(priv);
	value = zx279133_switch_nppt_read(priv, xmac + ZX279133_XMAC_RX_CTRL);
	zx279133_switch_nppt_write(priv, xmac + ZX279133_XMAC_RX_CTRL,
				    value & ~ZX279133_XMAC_ENABLE);
	value = zx279133_switch_nppt_read(priv, xmac + ZX279133_XMAC_TX_CTRL);
	zx279133_switch_nppt_write(priv, xmac + ZX279133_XMAC_TX_CTRL,
				    value & ~ZX279133_XMAC_ENABLE);
	zx279133_switch_nppt_write(priv, ZX279133_XMAC0_SOPC_SEND,
				    priv->saved_sopc_send);
	zx279133_switch_nppt_write(priv, ZX279133_XMAC_SOPC_DUPLEX,
				    priv->saved_sopc_duplex);
	zx279133_switch_nppt_write(priv, xmac + ZX279133_XMAC_TX_CTRL,
				    priv->saved_xmac_tx);
	zx279133_switch_nppt_write(priv, xmac + ZX279133_XMAC_RX_CTRL,
				    priv->saved_xmac_rx);
	zx279133_switch_nppt_write(priv, xmac + ZX279133_XMAC_FRAME_CFG,
				    priv->saved_xmac_frame);
	zx279133_switch_nppt_write(priv, xmac + ZX279133_XMAC_MODE_CFG,
				    priv->saved_xmac_mode);
	zx279133_switch_nppt_write(priv, xmac + ZX279133_XMAC_DUPLEX,
				    priv->saved_xmac_duplex);
	zx279133_switch_nppt_write(priv, xmac + ZX279133_XMAC_MISC_CFG,
				    priv->saved_xmac_misc);
	zx279133_switch_nppt_write(priv, ZX279133_SYS_SOFT_RESET,
				    priv->saved_xmac_reset);
	zx279133_switch_xmac_unlock(priv);
	priv->xmac_configured = false;
	priv->datapath_enabled = false;
}

static void zx279133_switch_release(void *data)
{
	struct zx279133_switch *priv = data;

	zx279133_switch_xmac_restore(priv);
	zx279133_switch_xpcs_restore(priv);
	if (priv->serdes_powered) {
		phy_power_off(priv->serdes);
		priv->serdes_powered = false;
	}
	if (priv->xpcs_runtime_held) {
		pm_runtime_put_autosuspend(priv->xpcs_mdiodev->bus->parent);
		priv->xpcs_runtime_held = false;
	}
	if (priv->xpcs_mdiodev) {
		mdio_device_put(priv->xpcs_mdiodev);
		priv->xpcs_mdiodev = NULL;
	}
	if (priv->datapath_held) {
		zx279133_lan_service_datapath_put(priv->service);
		priv->datapath_held = false;
	}
}

static int zx279133_switch_probe(struct platform_device *pdev)
{
	struct device *dev = &pdev->dev;
	struct device_node *parent_np;
	struct device_node *pcs_np;
	struct zx279133_switch *priv;
	u32 status;
	int ret;

	if (!dev->parent || !dev->parent->of_node)
		return dev_err_probe(dev, -ENODEV,
				     "the switch must be a child of the NPPT controller\n");
	parent_np = dev->parent->of_node;

	priv = devm_kzalloc(dev, sizeof(*priv), GFP_KERNEL);
	if (!priv)
		return -ENOMEM;

	priv->service = dev_get_drvdata(dev->parent);
	if (!priv->service)
		return dev_err_probe(dev, -EPROBE_DEFER,
				     "NPPT LAN service is not ready\n");
	if (!zx279133_lan_service_valid(priv->service))
		return dev_err_probe(dev, -EINVAL,
				     "incomplete NPPT LAN service\n");

	ret = zx279133_lan_service_datapath_get(priv->service);
	if (ret)
		return dev_err_probe(dev, ret,
				     "failed to acquire shared NPPT/IDM datapath\n");
	priv->datapath_held = true;

	/*
	 * The Uni SerDes carries the lane to the 9132's CPU port.  It is the
	 * parent's second PHY: phy-names = "serdes", "lan-serdes".  The lane
	 * runs 1G, and the bootloader left the SerDes in that state, so the
	 * driver's 1G profile inherits it rather than rewriting it.
	 */
	priv->serdes = devm_of_phy_get(dev, parent_np, "lan-serdes");
	if (IS_ERR(priv->serdes)) {
		ret = dev_err_probe(dev, PTR_ERR(priv->serdes),
				    "Uni SerDes PHY is unavailable\n");
		goto err_datapath_put;
	}

	ret = phy_set_mode_ext(priv->serdes, PHY_MODE_ETHERNET,
			       PHY_INTERFACE_MODE_SGMII);
	if (ret) {
		ret = dev_err_probe(dev, ret,
				    "failed to select Uni SerDes 1G mode\n");
		goto err_datapath_put;
	}

	ret = phy_power_on(priv->serdes);
	if (ret) {
		dev_warn(dev, "Uni SerDes first lock attempt failed: %d; retrying\n",
			 ret);
		msleep(20);
		ret = phy_power_on(priv->serdes);
	}
	if (ret) {
		ret = dev_err_probe(dev, ret, "failed to power on Uni SerDes\n");
		goto err_datapath_put;
	}
	priv->serdes_powered = true;

	pcs_np = of_parse_phandle(parent_np, "zte,lan-pcs-handle", 0);
	if (!pcs_np) {
		ret = -ENODEV;
		dev_err(dev, "LAN PCS phandle is missing\n");
		goto err_power_off;
	}

	priv->xpcs_mdiodev = fwnode_mdio_find_device(of_fwnode_handle(pcs_np));
	of_node_put(pcs_np);
	if (!priv->xpcs_mdiodev) {
		ret = -EPROBE_DEFER;
		dev_err(dev, "XPCS0 MDIO device is unavailable\n");
		goto err_power_off;
	}

	ret = pm_runtime_resume_and_get(priv->xpcs_mdiodev->bus->parent);
	if (ret < 0) {
		mdio_device_put(priv->xpcs_mdiodev);
		priv->xpcs_mdiodev = NULL;
		dev_err(dev, "failed to enable XPCS0 CSR clock: %d\n", ret);
		goto err_power_off;
	}
	priv->xpcs_runtime_held = true;

	ret = devm_add_action_or_reset(dev, zx279133_switch_release, priv);
	if (ret)
		return ret;

	ret = zx279133_switch_xpcs_sgmii(priv);
	if (ret) {
		dev_err(dev, "failed to configure XPCS0 for 1G SGMII: %d\n", ret);
		return dev_err_probe(dev, ret, "XPCS0 bring-up failed\n");
	}

	ret = zx279133_switch_xpcs_bypass(priv, true);
	if (ret) {
		dev_err(dev, "failed to enable XPCS0 bypass: %d\n", ret);
		return dev_err_probe(dev, ret, "XPCS0 bypass failed\n");
	}

	zx279133_switch_xmac_configure(priv);

	/* Re-apply after the XMAC/SOPC work; the C45 AN bits are lost. */
	ret = zx279133_switch_xpcs_apply_sgmii_vend2(priv, DUPLEX_FULL);
	if (ret) {
		dev_err(dev, "failed to re-apply the XPCS0 AN state: %d\n", ret);
		return dev_err_probe(dev, ret, "XPCS0 re-apply failed\n");
	}
	usleep_range(10000, 11000);

	/*
	 * Report what the lane says about itself.  This is the G2 evidence:
	 * 0x0090 bits 11..13 are the XPCS0 link status the tree's RTL8372N
	 * driver polls for the same block, and 0x0084 is the NPPT's XMAC
	 * link word.
	 */
	status = zx279133_switch_nppt_read(priv, ZX279133_NPPT_XPCS0_STATUS);
	dev_info(dev,
		 "XMAC0 datapath enabled; XPCS0 status=0x%04x link=%s xmac_link=0x%04x; 9132 UNI ports %u..%u\n",
		 status,
		 ((status & 0x3800) == 0x3800) ? "up" : "down",
		 zx279133_switch_nppt_read(priv, ZX279133_NPPT_XMAC_LINK),
		 ZX279133_SWITCH_UNI_PORT_MIN, ZX279133_SWITCH_UNI_PORT_MAX);

	return 0;

err_power_off:
	if (priv->serdes_powered) {
		phy_power_off(priv->serdes);
		priv->serdes_powered = false;
	}
err_datapath_put:
	if (priv->datapath_held) {
		zx279133_lan_service_datapath_put(priv->service);
		priv->datapath_held = false;
	}
	return ret;
}

static const struct of_device_id zx279133_switch_of_match[] = {
	{ .compatible = "zte,zx279133-switch" },
	{ }
};
MODULE_DEVICE_TABLE(of, zx279133_switch_of_match);

static struct platform_driver zx279133_switch_driver = {
	.probe = zx279133_switch_probe,
	.driver = {
		.name = "zx279133-switch",
		.of_match_table = zx279133_switch_of_match,
	},
};
module_platform_driver(zx279133_switch_driver);

MODULE_DESCRIPTION("ZTE ZX279133 on-board LAN switch (9132)");
MODULE_LICENSE("GPL");
