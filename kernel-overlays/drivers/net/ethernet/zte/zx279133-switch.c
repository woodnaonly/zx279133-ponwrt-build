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
 *   - The 9132 has seven ports: four with internal PHYs (MDIO controller #0,
 *     addresses 0x0a..0x0d), three "extend" ports standing for the SoC's own
 *     MACs, and the CPU port.  The four UNI ports and the CPU port are the
 *     ones that carry the copper traffic.
 *   - The 9132's FDB entry for the plugged PC has vid 0x0000, every port's
 *     VLAN filter mask is 0 and every port's ingress frame mode is 0: the
 *     chip is a flat untagged learning bridge here.
 *
 * So this driver is the 9132's counterpart of the tree's RTL8372N driver: it
 * owns the XMAC0 datapath, the XPCS0 lane and the Uni SerDes that carries
 * them, and it is a child of the NPPT controller, talking to it through the
 * typed service in zx279133-lan.h.
 *
 * The lane runs 10G over USXGMII, which is what the Uni SerDes PHY driver in
 * this tree speaks (phy-zx279133-uni-serdes.c accepts USXGMII and 10GBASE-R
 * and nothing else), and the XPCS programming below is the same USXGMII
 * profile the RTL8372N driver applies to XPCS0 on the SR1010 - the tree's
 * own working code for this exact block, reused rather than reinvented.
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
#include <linux/mdio.h>
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
#define ZX279133_XMAC_RESET_REG		0x2c0004
#define ZX279133_XMAC0_RESET		BIT(10)
#define ZX279133_XMAC_RESET_US		1718

#define ZX279133_XMAC0_SOPC_SEND	0x342c0
#define ZX279133_XMAC_SOPC_DUPLEX	0x343f0
#define ZX279133_XMAC0_SOPC_DUPLEX_MASK BIT(4)

/* NPPT-relative status words, as the RTL8372N driver reads them. */
#define ZX279133_NPPT_XMAC_LINK		0x0084
#define ZX279133_NPPT_XPCS0_STATUS	0x0090

/*
 * The XPCS0 USXGMII profile, taken from the tree's RTL8372N driver, which
 * programs the same block for the SR1010's switch CPU port.
 */
#define ZX279133_XPCS_VR_MII_DIG_CTRL1	0x8000
#define ZX279133_XPCS_VR_MII_AN_CTRL	0x8001
#define ZX279133_XPCS_VR_XS_PCS_DIG_CTRL1 0x8000
#define ZX279133_XPCS_VR_XS_PCS_KR_CTRL	0x8007
#define ZX279133_XPCS_USXG_EN		BIT(9)
#define ZX279133_XPCS_VSMMD1_EN		BIT(13)
#define ZX279133_XPCS_VR_RST		BIT(15)
#define ZX279133_XPCS_USXG_MODE_MASK	GENMASK(12, 10)
#define ZX279133_XPCS_AN_INTR_EN	BIT(0)
#define ZX279133_XPCS_AN_ENABLE		BIT(12)
#define ZX279133_XPCS_USXGMII_AN_BMCR	0x3140
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
	u16 saved_vend2_an_ctrl;
	u16 saved_vend2_bmcr;
	u16 saved_pcs_kr_ctrl;
	u16 saved_pcs_dig1;
	u16 saved_pcs_ctrl2;
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

/* The XPCS is a C45 device on the MDIO bus the XPCS platform device owns. */
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
	int val;

	val = zx279133_switch_xpcs_read(priv, devad, reg);
	if (val < 0)
		return val;

	return zx279133_switch_xpcs_write(priv, devad, reg,
					 (val & ~mask) | (set & mask));
}

static void zx279133_switch_xpcs_restore(struct zx279133_switch *priv)
{
	if (!priv->xpcs_configured)
		return;

	zx279133_switch_xpcs_write(priv, MDIO_MMD_VEND2,
				    ZX279133_XPCS_VR_MII_AN_CTRL,
				    priv->saved_vend2_an_ctrl);
	zx279133_switch_xpcs_write(priv, MDIO_MMD_VEND2, MDIO_CTRL1,
				    priv->saved_vend2_bmcr);
	zx279133_switch_xpcs_write(priv, MDIO_MMD_PCS,
				    ZX279133_XPCS_VR_XS_PCS_KR_CTRL,
				    priv->saved_pcs_kr_ctrl);
	zx279133_switch_xpcs_write(priv, MDIO_MMD_PCS,
				    ZX279133_XPCS_VR_XS_PCS_DIG_CTRL1,
				    priv->saved_pcs_dig1);
	zx279133_switch_xpcs_write(priv, MDIO_MMD_PCS, MDIO_CTRL2,
				    priv->saved_pcs_ctrl2);
	priv->xpcs_configured = false;
}

/*
 * The USXGMII profile, from the tree's zx279133_lan_xpcs_configure() in
 * drivers/net/ethernet/zte/zx279133-rtl8372n.c.  Same block, same
 * sequence, same vendor registers.
 */
static int zx279133_switch_xpcs_configure(struct zx279133_switch *priv)
{
	int val;
	int ret;

	val = zx279133_switch_xpcs_read(priv, MDIO_MMD_PCS, MDIO_CTRL2);
	if (val < 0)
		return val;
	priv->saved_pcs_ctrl2 = val;
	val = zx279133_switch_xpcs_read(priv, MDIO_MMD_PCS,
					ZX279133_XPCS_VR_XS_PCS_DIG_CTRL1);
	if (val < 0)
		return val;
	priv->saved_pcs_dig1 = val;
	val = zx279133_switch_xpcs_read(priv, MDIO_MMD_PCS,
					ZX279133_XPCS_VR_XS_PCS_KR_CTRL);
	if (val < 0)
		return val;
	priv->saved_pcs_kr_ctrl = val;
	val = zx279133_switch_xpcs_read(priv, MDIO_MMD_VEND2, MDIO_CTRL1);
	if (val < 0)
		return val;
	priv->saved_vend2_bmcr = val;
	val = zx279133_switch_xpcs_read(priv, MDIO_MMD_VEND2,
					ZX279133_XPCS_VR_MII_AN_CTRL);
	if (val < 0)
		return val;
	priv->saved_vend2_an_ctrl = val;
	priv->xpcs_configured = true;

	ret = read_poll_timeout(zx279133_switch_xpcs_read, val,
				val >= 0 && !(val & ZX279133_XPCS_VR_RST),
				1000, 400000, false, priv,
				MDIO_MMD_PCS, MDIO_CTRL1);
	if (ret)
		goto err_restore;
	ret = read_poll_timeout(zx279133_switch_xpcs_read, val,
				val >= 0 && !(val & ZX279133_XPCS_VR_RST),
				1000, 400000, false, priv,
				MDIO_MMD_VEND2, MDIO_CTRL1);
	if (ret)
		goto err_restore;

	ret = zx279133_switch_xpcs_modify(priv, MDIO_MMD_VEND2,
					  ZX279133_XPCS_VR_MII_AN_CTRL,
					  BIT(3) | BIT(8), 0);
	if (ret)
		goto err_restore;
	ret = zx279133_switch_xpcs_write(priv, MDIO_MMD_PCS, MDIO_CTRL2, 0);
	if (ret)
		goto err_restore;
	ret = zx279133_switch_xpcs_modify(priv, MDIO_MMD_PCS,
					  ZX279133_XPCS_VR_XS_PCS_DIG_CTRL1,
					  ZX279133_XPCS_USXG_EN |
					  ZX279133_XPCS_VSMMD1_EN,
					  ZX279133_XPCS_USXG_EN |
					  ZX279133_XPCS_VSMMD1_EN);
	if (ret)
		goto err_restore;
	ret = zx279133_switch_xpcs_modify(priv, MDIO_MMD_PCS,
					  ZX279133_XPCS_VR_XS_PCS_KR_CTRL,
					  ZX279133_XPCS_USXG_MODE_MASK, 0);
	if (ret)
		goto err_restore;
	ret = zx279133_switch_xpcs_modify(priv, MDIO_MMD_PCS,
					  ZX279279133_XPCS_VR_XS_PCS_DIG_CTRL1,
					  ZX279133_XPCS_VR_RST,
					  ZX279133_XPCS_VR_RST);
	if (ret)
		goto err_restore;
	ret = read_poll_timeout(zx279133_switch_xpcs_read, val,
				val >= 0 && !(val & ZX279133_XPCS_VR_RST),
				1000, 400000, false, priv,
				MDIO_MMD_PCS, ZX279133_XPCS_VR_XS_PCS_DIG_CTRL1);
	if (ret)
		goto err_restore;
	ret = zx279133_switch_xpcs_modify(priv, MDIO_MMD_VEND2,
					  ZX279133_XPCS_VR_MII_AN_CTRL,
					  ZX279133_XPCS_AN_INTR_EN,
					  ZX279133_XPCS_AN_INTR_EN);
	if (ret)
		goto err_restore;
	ret = zx279133_switch_xpcs_write(priv, MDIO_MMD_VEND2, MDIO_CTRL1,
					  ZX279133_XPCS_USXGMII_AN_BMCR);
	if (ret)
		goto err_restore;

	return 0;

err_restore:
	zx279133_switch_xpcs_restore(priv);
	return ret;
}

/*
 * The XMAC0 register image, from the tree's zx279133_lan_xmac_configure().
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
		zx279133_switch_nppt_read(priv, ZX279133_XMAC_RESET_REG);
	priv->xmac_configured = true;

	/* Reset XMAC0, then release it. */
	zx279133_switch_nppt_write(priv, ZX279133_XMAC_RESET_REG,
				    priv->saved_xmac_reset &
				    ~ZX279133_XMAC0_RESET);
	usleep_range(ZX279133_XMAC_RESET_US, ZX279133_XMAC_RESET_US + 100);
	zx279133_switch_nppt_write(priv, ZX279133_XMAC_RESET_REG,
				    priv->saved_xmac_reset |
				    ZX279133_XMAC0_RESET);

	zx279133_switch_nppt_write(priv, xmac + ZX279133_XMAC_TX_CTRL,
				    0x00010000);
	zx279133_switch_nppt_write(priv, xmac + ZX279133_XMAC_RX_CTRL,
				    0x3e800086 | ZX279133_XMAC_RX_IPC);
	zx279133_switch_nppt_write(priv, xmac + ZX279133_XMAC_FRAME_CFG,
				    0x80000001);
	zx279133_switch_nppt_write(priv, xmac + ZX279133_XMAC_MODE_CFG,
				    0x00000002);
	value = zx279133_switch_nppt_read(priv, xmac + ZX279133_XMAC_DUPLEX);
	zx279133_switch_nppt_write(priv, xmac + ZX279133_XMAC_DUPLEX,
				    value & ~ZX279133_XMAC_HALF_DUPLEX);
	value = zx279133_switch_nppt_read(priv, xmac + ZX279133_XMAC_MISC_CFG);
	zx279133_switch_nppt_write(priv, xmac + ZX279133_XMAC_MISC_CFG,
				    value | BIT(9));
	zx279133_switch_xmac_unlock(priv);
}

/*
 * Publish the XMAC0 datapath.  The SOPC send enable and the duplex word are
 * the XMAC0 ones (0x342c0 / 0x343f0), not the NPPT's XMAC1 ones.
 */
static int zx279133_switch_datapath_enable(struct zx279133_switch *priv)
{
	u32 xmac = ZX279133_XMAC0_BASE;
	int val;
	int ret;
	u32 value;

	/* The vendor's SR1010 tail disables XPCS0 AN after the mode setup. */
	ret = zx279133_switch_xpcs_modify(priv, MDIO_MMD_VEND2,
					   ZX279133_XPCS_VR_MII_AN_CTRL,
					   ZX279133_XPCS_AN_INTR_EN, 0);
	if (ret)
		return ret;
	ret = zx279133_switch_xpcs_modify(priv, MDIO_MMD_VEND2, MDIO_CTRL1,
					   ZX279133_XPCS_AN_ENABLE, 0);
	if (ret)
		return ret;

	val = zx279133_switch_xpcs_read(priv, MDIO_MMD_PCS,
					ZX279133_XPCS_BYPASS_REG);
	if (val < 0)
		return val;
	priv->saved_xpcs_bypass = val;
	ret = zx279133_switch_xpcs_modify(priv, MDIO_MMD_PCS,
					  ZX279133_XPCS_BYPASS_REG,
					  ZX279133_XPCS_BYPASS_EN,
					  ZX279133_XPCS_BYPASS_EN);
	if (ret)
		return ret;

	zx279133_switch_xmac_lock(priv);
	priv->saved_sopc_duplex =
		zx279133_switch_nppt_read(priv, ZX279133_XMAC_SOPC_DUPLEX);
	priv->saved_sopc_send =
		zx279133_switch_nppt_read(priv, ZX279133_XMAC0_SOPC_SEND);
	value = priv->saved_sopc_duplex & ~ZX279133_XMAC0_SOPC_DUPLEX_MASK;
	zx279133_switch_nppt_write(priv, ZX279133_XMAC_SOPC_DUPLEX, value);
	usleep_range(4295, 4395);
	zx279133_switch_nppt_write(priv, ZX279133_XMAC0_SOPC_SEND, 1);
	value = zx279133_switch_nppt_read(priv, xmac + ZX279133_XMAC_RX_CTRL);
	zx279133_switch_nppt_write(priv, xmac + ZX279133_XMAC_RX_CTRL,
				    value | BIT(0));
	value = zx279133_switch_nppt_read(priv, xmac + ZX279133_XMAC_TX_CTRL);
	zx279133_switch_nppt_write(priv, xmac + ZX279133_XMAC_TX_CTRL,
				    value | BIT(0));
	zx279133_switch_xmac_unlock(priv);
	priv->datapath_enabled = true;

	return 0;
}

static void zx279133_switch_datapath_restore(struct zx279133_switch *priv)
{
	u32 xmac = ZX279133_XMAC0_BASE;
	u32 value;

	if (!priv->datapath_enabled)
		return;

	zx279133_switch_xmac_lock(priv);
	value = zx279133_switch_nppt_read(priv, xmac + ZX279133_XMAC_RX_CTRL);
	zx279133_switch_nppt_write(priv, xmac + ZX279133_XMAC_RX_CTRL,
				    value & ~BIT(0));
	value = zx279133_switch_nppt_read(priv, xmac + ZX279133_XMAC_TX_CTRL);
	zx279133_switch_nppt_write(priv, xmac + ZX279133_XMAC_TX_CTRL,
				    value & ~BIT(0));
	zx279133_switch_nppt_write(priv, ZX279133_XMAC0_SOPC_SEND,
				    priv->saved_sopc_send);
	zx279133_switch_nppt_write(priv, ZX279133_XMAC_SOPC_DUPLEX,
				    priv->saved_sopc_duplex);
	zx279133_switch_xmac_unlock(priv);
	zx279133_switch_xpcs_write(priv, MDIO_MMD_PCS,
				    ZX279133_XPCS_BYPASS_REG,
				    priv->saved_xpcs_bypass);
	priv->datapath_enabled = false;
}

static void zx279133_switch_xmac_restore(struct zx279133_switch *priv)
{
	u32 xmac = ZX279133_XMAC0_BASE;

	if (!priv->xmac_configured)
		return;

	zx279133_switch_xmac_lock(priv);
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
	zx279133_switch_nppt_write(priv, ZX279133_XMAC_RESET_REG,
				    priv->saved_xmac_reset);
	zx279133_switch_xmac_unlock(priv);
	priv->xmac_configured = false;
}

static void zx279133_switch_release(void *data)
{
	struct zx279133_switch *priv = data;

	zx279133_switch_datapath_restore(priv);
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
	 * parent's second PHY: phy-names = "serdes", "lan-serdes".
	 */
	priv->serdes = devm_of_phy_get(dev, parent_np, "lan-serdes");
	if (IS_ERR(priv->serdes)) {
		ret = dev_err_probe(dev, PTR_ERR(priv->serdes),
				    "Uni SerDes PHY is unavailable\n");
		goto err_datapath_put;
	}

	ret = phy_set_mode_ext(priv->serdes, PHY_MODE_ETHERNET,
			       PHY_INTERFACE_MODE_USXGMII);
	if (ret) {
		ret = dev_err_probe(dev, ret,
				    "failed to select Uni SerDes USXGMII mode\n");
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

	ret = zx279133_switch_xpcs_configure(priv);
	if (ret) {
		dev_err(dev, "failed to configure XPCS0 USXGMII mode: %d\n", ret);
		return dev_err_probe(dev, ret, "XPCS0 bring-up failed\n");
	}

	zx279133_switch_xmac_configure(priv);

	ret = zx279133_switch_datapath_enable(priv);
	if (ret) {
		dev_err(dev, "failed to enable XMAC0 datapath: %d\n", ret);
		return dev_err_probe(dev, ret, "XMAC0 datapath failed\n");
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
