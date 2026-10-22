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
#include <linux/etherdevice.h>
#include <linux/ethtool.h>
#include <linux/iopoll.h>
#include <linux/mdio.h>
#include <linux/mii.h>
#include <linux/mod_devicetable.h>
#include <linux/module.h>
#include <linux/netdevice.h>
#include <linux/of.h>
#include <linux/of_mdio.h>
#include <linux/of_net.h>
#include <linux/phy.h>
#include <linux/phy/phy.h>
#include <linux/phylink.h>
#include <linux/platform_device.h>
#include <linux/pm_runtime.h>
#include <linux/rtnetlink.h>
#include <linux/slab.h>

#include <net/dsa.h>
#include <net/dsa_zx279133_rtl8372n.h>
#include <net/switchdev.h>

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
/*
 * The vendor's own values for the XMAC0 on this lane.
 *
 * RX_CTRL and FRAME_CFG are xamc_init_conf_by_speed()'s (np_133.ko
 * 0x1a240 and 0x1a258) literals, and the "IPC" bit 0x200 is the one
 * xamc_init_conf_by_speed() puts into MISC_CFG (0x1a288) - not into
 * RX_CTRL, where the tree's RTL8372N driver has it.  That driver drives
 * the same MAC towards a different far side, and its two values were
 * wrong for the 9132's CPU port: RX_CTRL bit 6 is set by the vendor and
 * cleared here, and FRAME_CFG bit 0 is cleared by the vendor and set
 * here.
 */
#define ZX279133_XMAC_VENDOR_RX_CTRL	0x3e8000c6
#define ZX279133_XMAC_VENDOR_FRAME_CFG	0x80000000
#define ZX279133_XMAC_MISC_IPC		BIT(9)
/*
 * xmac_set_speed_sel(): 3 = 1G SGMII, 6 = 2.5G, 4 = 100M, 7 = 10M.
 * The USXGMII image the tree's RTL8372N driver writes leaves the field at
 * 0, which is what that lane runs at, so the two profiles differ here.
 */
#define ZX279133_XMAC_SPEED_1G		3
#define ZX279133_XMAC_SPEED_USXGMII	0
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
 * The SOPC publish array.
 *
 * np_133.ko's xmac_init_by_work_mode() and this board's own U-Boot both
 * publish a MAC only after its own "clear-over-ready" word says the MAC is
 * ready.  U-Boot's net bring-up says so out loud, four times, in this
 * order:
 *
 *	read SOPC_CLR_OVER_READY_SMAC = 0x1, mac = 0x0
 *	write SOPC_SEND_EN_CFG_SMAC,mac = 0x0
 *	... mac = 0x1, mac = 0x2, mac = 0x3
 *
 * and the two words of the pair are nppt+0x34000+((mac+0xa9)<<2) and
 * nppt+0x34000+((mac+0xb0)<<2).  The tree's RTL8372N driver knows only the
 * first MAC of the array (0x342a4 / 0x342c0), and nothing in the tree has
 * ever written the array at all - which is what the run-62 counters said:
 * every descriptor completed and not one frame left the MAC.
 */
#define ZX279133_XMAC_SOPC_READY_BASE	0x342a4
#define ZX279133_XMAC_SOPC_SEND_BASE	0x342c0
#define ZX279133_XMAC_SOPC_CHANNELS	4

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

/*
 * The USXGMII profile, from zx279133_lan_xpcs_configure() in the tree's
 * drivers/net/ethernet/zte/zx279133-rtl8372n.c - the same XPCS0 block, on
 * the same lane, driven for the RTL8372N's 10G CPU port.
 */
#define ZX279133_XPCS_VR_XS_PCS_KR_CTRL 0x8007
#define ZX279133_XPCS_USXG_EN		BIT(9)
#define ZX279133_XPCS_VSMMD1_EN	BIT(13)
#define ZX279133_XPCS_VR_RST		BIT(15)
#define ZX279133_XPCS_USXG_MODE_MASK	GENMASK(12, 10)
#define ZX279133_XPCS_AN_ENABLE		BIT(12)
#define ZX279133_XPCS_USXGMII_AN_BMCR	0x3140

/*
 * The 9132's four UNI ports are the switch ports the copper jacks sit on.
 *
 * They are numbered 1..4 here because that is how the rest of this driver
 * family numbers them: the 9132's PPU ports are 1..4, the NPPT driver says
 * so on the console ("9132 UNI ports 1..4 up (transport VIDs 59..62)"), and
 * DSA port N + 3 is PPU port N.  These two constants used to say 0..3, and
 * the only thing they were used for was this driver's own message - so the
 * two drivers printed two different port ranges for the same four jacks.
 */
#define ZX279133_SWITCH_UNI_PORT_MIN	1
#define ZX279133_SWITCH_UNI_PORT_MAX	4

/*
 * The four UNI ports as DSA ports, and the transport VID that carries them.
 *
 * The tree already ships a tagger for this SoC's LAN conduit -
 * net/dsa/tag_zx279133_rtl8372n.c, built as CONFIG_NET_DSA_TAG_ZX279133_RTL8372N -
 * and it puts the transport VID in an 802.1Q header on the conduit:
 *
 *	VID = ZX279133_RTL8372N_TRANSPORT_VID_BASE + port	(55 + port)
 *
 * The RTL8372N's four user ports are DSA ports 4..7, so the tagger's VIDs
 * are 59..62 - and those are exactly the VIDs the NPPT driver programs as
 * the untagged SVID (the PVID) of the 9132's PPU ports 1..4
 * (zx279133_9132_port_init(): ZX279133_LAN_TRANSPORT_VID_MIN + (ppu - 1)).
 * So the same tagger, the same VIDs and the same DSA port numbers serve
 * this board, and DSA port N + 3 is the 9132's PPU port N.
 *
 * The tagger is not renamed: it is a transport-VID tagger that the tree
 * happens to have named after the switch it was written for, and its
 * protocol number is already in the tree's include/net/dsa.h.
 */
#define ZX279133_SWITCH_DSA_PORT_MIN	4
#define ZX279133_SWITCH_DSA_PORT_MAX	7
#define ZX279133_SWITCH_DSA_NUM_PORTS	9
/*
 * The CPU port: the one that faces the conduit.
 *
 * It is index 8 and not 0 because that is what the tree's own DSA switch
 * does.  The tree's RTL8372N node (arch/arm64/boot/dts/zte/zx279133-sr1010.dts)
 * describes its chassis LAN1..LAN4 as ports 4..7 and its CPU port as
 * port@8, and its driver answers "port == 8" wherever the CPU port is
 * asked about (zx279133_rtl8372n_phylink_fixed_state()).  This tree's DSA
 * fork looks a port up by index (dsa_to_port()) and leaves the numbers
 * between the CPU port and the user ports unused, so the same shape is
 * used here: the four UNI jacks are 4..7 and the CPU port is 8.
 *
 * The tagger does not care which end is which - it puts
 * TRANSPORT_VID_BASE + port on a user port's frames and reads the port
 * back out of the VID - so the four jacks keep the transport VIDs 59..62,
 * which are exactly the PVIDs zx279133_9132_port_init() programs.
 */
#define ZX279133_SWITCH_DSA_CPU_PORT	8
/* The 9132's PPU port behind a DSA port: DSA 4..7 are PPU 1..4. */
#define ZX279133_SWITCH_DSA_PPU(port)	((port) - \
					 (ZX279133_SWITCH_DSA_PORT_MIN - 1))
/* The 9132's internal PHY behind a DSA port: PPU 1..4 are PHY 0x0a..0x0d. */
#define ZX279133_SWITCH_DSA_PHY(port)	(0x0a + \
					 (port) - ZX279133_SWITCH_DSA_PORT_MIN)

struct zx279133_switch {
	/* The device this driver was probed on.  Two of the helpers below
	 * (the lane report and the SOPC publish) print through it and have
	 * no other way to reach it: they are called from the probe and from
	 * the DSA callbacks, and neither of those carries a device of its
	 * own at the point where the messages are printed.
	 */
	struct device *dev;
	struct zx279133_lan_service *service;
	struct phy *serdes;
	struct mdio_device *xpcs_mdiodev;
	struct mii_bus *uni_mdio;
	/*
	 * The bus of the switch's own, which the four user ports' PHYs are
	 * on: DSA port N is at MDIO address N on it, and it translates to
	 * the SoC's controller #0 address 0x0a..0x0d.
	 */
	struct mii_bus *user_mdio;
	struct dsa_switch *ds;
	bool datapath_held;
	bool xpcs_runtime_held;
	bool xpcs_configured;
	bool xmac_configured;
	bool datapath_enabled;
	bool serdes_powered;
	bool usxgmii;
	bool dsa_registered;
	bool datapath_ready;
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

/*
 * The USXGMII profile, from zx279133_lan_xpcs_configure() in the tree's
 * drivers/net/ethernet/zte/zx279133-rtl8372n.c - the same XPCS0 block on
 * the same lane, programmed there for the RTL8372N's 10G CPU port.
 */
static int zx279133_switch_xpcs_usxgmii(struct zx279133_switch *priv)
{
	int val;
	int ret;

	ret = zx279133_switch_xpcs_modify(priv, MDIO_MMD_VEND2,
					   ZX279133_XPCS_VR_MII_AN_CTRL,
					   BIT(3) | BIT(8), 0);
	if (ret)
		return ret;
	ret = zx279133_switch_xpcs_write(priv, MDIO_MMD_PCS, MDIO_CTRL2, 0);
	if (ret)
		return ret;
	ret = zx279133_switch_xpcs_modify(priv, MDIO_MMD_PCS,
					   ZX279133_XPCS_VR_XS_PCS_DIG_CTRL1,
					   ZX279133_XPCS_USXG_EN |
					   ZX279133_XPCS_VSMMD1_EN,
					   ZX279133_XPCS_USXG_EN |
					   ZX279133_XPCS_VSMMD1_EN);
	if (ret)
		return ret;
	ret = zx279133_switch_xpcs_modify(priv, MDIO_MMD_PCS,
					   ZX279133_XPCS_VR_XS_PCS_KR_CTRL,
					   ZX279133_XPCS_USXG_MODE_MASK, 0);
	if (ret)
		return ret;
	ret = zx279133_switch_xpcs_modify(priv, MDIO_MMD_PCS,
					   ZX279133_XPCS_VR_XS_PCS_DIG_CTRL1,
					   ZX279133_XPCS_VR_RST,
					   ZX279133_XPCS_VR_RST);
	if (ret)
		return ret;
	ret = read_poll_timeout(zx279133_switch_xpcs_read, val,
				val >= 0 && !(val & ZX279133_XPCS_VR_RST),
				1000, 400000, false, priv, MDIO_MMD_PCS,
				ZX279133_XPCS_VR_XS_PCS_DIG_CTRL1);
	if (ret)
		return ret;
	ret = zx279133_switch_xpcs_modify(priv, MDIO_MMD_VEND2,
					   ZX279133_XPCS_VR_MII_AN_CTRL,
					   ZX279133_XPCS_AN_INTR_EN,
					   ZX279133_XPCS_AN_INTR_EN);
	if (ret)
		return ret;

	return zx279133_switch_xpcs_write(priv, MDIO_MMD_VEND2, MDIO_CTRL1,
					  ZX279133_XPCS_USXGMII_AN_BMCR);
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
 * The four internal-PHY MACs, the way the vendor programs them.
 *
 * The vendor's nppt_smac_init() (np_133.ko 0xda1c) walks mac 0..3 and, for
 * each one, whose block lives at NPPT + 0x40000 * (mac + 1), writes:
 *
 *	+0x0000  0x00fa2203   the MAC's own control word
 *	+0x0004  0x3fff
 *	+0x0b00 |= 0x200
 *
 * and then publishes that MAC through the SOPC: it waits for the ready word
 * at NPPT + 0x34294 + 4 * mac and writes the send-enable at
 * NPPT + 0x342b0 + 4 * mac.  (The XMAC has its own pair of those words, at
 * 0x342a4 and 0x342c0; the two groups are four words apart and do not
 * overlap.)
 *
 * Two registers of each block are deliberately left alone.  The vendor also
 * writes +0x0008 (the packet filter) and +0x000e0 (the speed word), but on
 * this board those two registers already hold what the vendor's board holds
 * - 0x80000001 and 0x00011000 - straight out of reset, and writing the
 * vendor's values over them *destroys* them: the hardware then reads back
 * 0x80000000 and 0x00010000, with the enable bit and bit 12 gone.  So they
 * are read, not written, here, and only to show what they are.
 *
 * The reset the vendor pulses in between - a bit of the shared soft-reset
 * word at NPPT + 0x2c0004 - is left out on purpose.  On this board it makes
 * no difference to any of these registers, and that word is shared with the
 * XMAC and the PCS, which are not ours to reset from this path.
 *
 * Why this matters: an SMAC is the MAC in front of one of the four internal
 * PHYs, so it is on the path a frame takes out of a LAN port.  With the four
 * blocks left at their reset value (an_ctrl reads 0x00ba2200) a frame that
 * arrives from a LAN port stops at the PHY: it never reaches the 9132, the
 * RED counters stay at zero, the IDM's receive engine is never started and
 * no receive interrupt is ever raised.
 */
static void zx279133_switch_smac_init(struct zx279133_switch *priv)
{
	struct device *dev = priv->dev;
	int mac;

	for (mac = 0; mac < ZX279133_SMAC_COUNT; mac++) {
		u32 base = ZX279133_SMAC_BASE(mac);
		u32 value;
		int tries;

		/* nppt_smac_set_uni_mode(mac, 0). */
		value = zx279133_switch_nppt_read(priv,
						  ZX279133_SMAC_UNI_MODE(mac));
		zx279133_switch_nppt_write(priv, ZX279133_SMAC_UNI_MODE(mac),
					    value & ~ZX279133_SMAC_UNI_MODE_MASK);

		zx279133_switch_nppt_write(priv, base + ZX279133_SMAC_AN_CTRL,
					    ZX279133_SMAC_AN_CTRL_VALUE);
		zx279133_switch_nppt_write(priv, base + ZX279133_SMAC_AN_CTRL2,
					    ZX279133_SMAC_AN_CTRL2_VALUE);

		value = zx279133_switch_nppt_read(priv,
						  base + ZX279133_SMAC_PORT_CTRL);
		zx279133_switch_nppt_write(priv, base + ZX279133_SMAC_PORT_CTRL,
					    value | ZX279133_SMAC_PORT_CTRL_BIT);

		/*
		 * Publish it.  The vendor waits for the ready word, and gives
		 * up after ten tries and publishes anyway; so does this.
		 */
		for (tries = 0; tries < 10; tries++) {
			value = zx279133_switch_nppt_read(priv,
				ZX279133_SMAC_SOPC_READY_BASE + 4 * mac);
			if (value & BIT(0))
				break;
			usleep_range(1000, 1100);
		}
		zx279133_switch_nppt_write(priv,
					   ZX279133_SMAC_SOPC_SEND_BASE + 4 * mac,
					   1);

		dev_info(dev,
			 "SMAC %d programmed: an_ctrl=0x%08x pkt_filter=0x%08x speed=0x%08x ready=0x%08x\n",
			 mac,
			 zx279133_switch_nppt_read(priv,
						   base + ZX279133_SMAC_AN_CTRL),
			 zx279133_switch_nppt_read(priv,
						   base + ZX279133_SMAC_PKT_FILTER),
			 zx279133_switch_nppt_read(priv,
						   base + ZX279133_SMAC_SPEED),
			 value);
	}
}

/*
 * The XMAC0's own registers, written the way the vendor writes them.
 *
 * The vendor's xamc_init_conf_by_speed() (np_133.ko 0x1a200) is six
 * stores and nothing else:
 *
 *	+0x0000 TX_CTRL	 0x00010000, then the speed bits
 *	+0x0010 RX_CTRL	 0x3e8000c6
 *	+0x0020 FRAME_CFG	 0x80000000
 *	+0x0280 MODE_CFG	 2
 *	+0x3400 MISC_CFG	 |= 0x200
 *
 * and xmac_tx_rx_enable() (0x1a114) then sets bit 0 of TX_CTRL and of
 * RX_CTRL, which is the enable.
 *
 * The two values this driver used before came from the tree's RTL8372N
 * driver, which drives the same MAC towards a different far side - an
 * external RTL8372N rather than the 9132's CPU port - and they were not
 * even the same register: the "IPC" bit 0x200 belongs in MISC_CFG, and
 * it was being written to RX_CTRL as well; and RX_CTRL bit 6 was being
 * cleared where the vendor sets it.  Neither of those had ever been
 * exercised on this board, because the function was never called on it
 * (see zx279133_switch_xmac_configure()).
 */
static void zx279133_switch_xmac_program(struct zx279133_switch *priv,
					 u32 speed_code)
{
	u32 xmac = ZX279133_XMAC0_BASE;
	u32 value;

	zx279133_switch_nppt_write(priv, xmac + ZX279133_XMAC_TX_CTRL,
				    0x00010000);
	value = zx279133_switch_nppt_read(priv, xmac + ZX279133_XMAC_TX_CTRL);
	zx279133_switch_nppt_write(priv, xmac + ZX279133_XMAC_TX_CTRL,
				    (value & ~ZX279133_XMAC_SPEED_MASK) |
				    FIELD_PREP(ZX279133_XMAC_SPEED_MASK,
					       speed_code));

	value = zx279133_switch_nppt_read(priv, xmac + ZX279133_XMAC_DUPLEX);
	zx279133_switch_nppt_write(priv, xmac + ZX279133_XMAC_DUPLEX,
				    value & ~ZX279133_XMAC_HALF_DUPLEX);

	zx279133_switch_nppt_write(priv, xmac + ZX279133_XMAC_RX_CTRL,
				    ZX279133_XMAC_VENDOR_RX_CTRL);
	zx279133_switch_nppt_write(priv, xmac + ZX279133_XMAC_FRAME_CFG,
				    ZX279133_XMAC_VENDOR_FRAME_CFG);
	zx279133_switch_nppt_write(priv, xmac + ZX279133_XMAC_MODE_CFG, 2);

	value = zx279133_switch_nppt_read(priv, xmac + ZX279133_XMAC_MISC_CFG);
	zx279133_switch_nppt_write(priv, xmac + ZX279133_XMAC_MISC_CFG,
				    value | ZX279133_XMAC_MISC_IPC);
}

/*
 * The lane's own words, read and printed.
 *
 * Every number here is a number the lane itself reports: the NPPT's two
 * status words, the six XMAC0 registers this driver programs, the SOPC
 * publish array and the XPCS's own C45 registers.  The point is to be able
 * to tell "the PCS says the link is down" from "the PCS says the link is
 * up and the MAC is the part that is broken" - that is the difference
 * between the next fix belonging to the SerDes and the next fix belonging
 * to the XMAC, and nothing else in the log can say it.
 */
static void zx279133_switch_lane_snapshot(struct zx279133_switch *priv,
					   const char *tag)
{
	static const struct {
		int devad;
		int reg;
		const char *name;
	} c45[] = {
		{ MDIO_MMD_VEND2, ZX279133_XPCS_VR_MII_AN_CTRL,
		  "vend2 AN_CTRL" },
		{ MDIO_MMD_VEND2, ZX279133_XPCS_VR_MII_DIG_CTRL1,
		  "vend2 DIG_CTRL1" },
		{ MDIO_MMD_VEND2, MII_BMCR, "vend2 BMCR" },
		{ MDIO_MMD_PCS, MII_BMCR, "pcs BMCR" },
	};
	struct device *dev = priv->dev;
	u32 status, link, tx, rx, frame, mode, duplex, misc;
	int i;

	status = zx279133_switch_nppt_read(priv, ZX279133_NPPT_XPCS0_STATUS);
	link = zx279133_switch_nppt_read(priv, ZX279133_NPPT_XMAC_LINK);
	tx = zx279133_switch_nppt_read(priv,
				       ZX279133_XMAC0_BASE + ZX279133_XMAC_TX_CTRL);
	rx = zx279133_switch_nppt_read(priv,
				       ZX279133_XMAC0_BASE + ZX279133_XMAC_RX_CTRL);
	frame = zx279133_switch_nppt_read(priv,
					  ZX279133_XMAC0_BASE +
					  ZX279133_XMAC_FRAME_CFG);
	mode = zx279133_switch_nppt_read(priv,
					 ZX279133_XMAC0_BASE +
					 ZX279133_XMAC_MODE_CFG);
	duplex = zx279133_switch_nppt_read(priv,
					   ZX279133_XMAC0_BASE +
					   ZX279133_XMAC_DUPLEX);
	misc = zx279133_switch_nppt_read(priv,
					 ZX279133_XMAC0_BASE +
					 ZX279133_XMAC_MISC_CFG);

	dev_info(dev,
		 "lane %s: XPCS0 status=0x%04x link=%s XMAC link=0x%04x\n",
		 tag, status,
		 ((status & 0x3800) == 0x3800) ? "up" : "down", link);
	dev_info(dev,
		 "lane %s: XMAC0 tx=0x%08x rx=0x%08x frame=0x%08x mode=0x%08x duplex=0x%08x misc=0x%08x\n",
		 tag, tx, rx, frame, mode, duplex, misc);

	for (i = 0; i < ZX279133_XMAC_SOPC_CHANNELS; i++) {
		u32 ready = zx279133_switch_nppt_read(priv,
			ZX279133_XMAC_SOPC_READY_BASE + 4 * i);
		u32 send = zx279133_switch_nppt_read(priv,
			ZX279133_XMAC_SOPC_SEND_BASE + 4 * i);

		dev_info(dev, "lane %s: SOPC mac %d ready=0x%08x send=0x%08x\n",
			 tag, i, ready, send);
	}

	if (!priv->xpcs_mdiodev)
		return;

	for (i = 0; i < ARRAY_SIZE(c45); i++) {
		int val = zx279133_switch_xpcs_read(priv, c45[i].devad,
						    c45[i].reg);

		dev_info(dev, "lane %s: XPCS %s = 0x%04x\n", tag, c45[i].name,
			 val < 0 ? 0xffff : val);
	}
}

/*
 * Publish the SOPC send-enable for every MAC, each one after its own
 * ready word says it may be published.
 *
 * The vendor's U-Boot does exactly this, for mac 0..3, and says so in its
 * console output; the tree's RTL8372N driver publishes one word and
 * nothing in the tree has ever written the rest of the array.  A MAC whose
 * send-enable is never published does not move a frame, whatever else is
 * correct.
 */
static void zx279133_switch_sopc_publish(struct zx279133_switch *priv)
{
	struct device *dev = priv->dev;
	int i;

	for (i = 0; i < ZX279133_XMAC_SOPC_CHANNELS; i++) {
		u32 ready = 0;
		int tries;

		for (tries = 0; tries < 10; tries++) {
			ready = zx279133_switch_nppt_read(priv,
				ZX279133_XMAC_SOPC_READY_BASE + 4 * i);
			if (ready & BIT(0))
				break;
			usleep_range(1000, 1100);
		}
		if (!(ready & BIT(0)))
			dev_warn(dev,
				 "SOPC mac %d never became ready (0x%08x); not publishing it\n",
				 i, ready);

		zx279133_switch_nppt_write(priv,
					   ZX279133_XMAC_SOPC_SEND_BASE + 4 * i,
					   1);
	}

	dev_info(dev, "SOPC send-enable published for %d MAC(s)\n",
		 ZX279133_XMAC_SOPC_CHANNELS);
}

/*
 * The XMAC0 bring-up, from zx279133_xmac_sgmii_init(): reset, program,
 * publish the SOPC send enable, then enable the MAC.
 *
 * The reset is optional, and on this board it is not wanted: the soft
 * reset word is shared with the PCS, and the PCS on this board was
 * brought up by the bootloader and must not be disturbed (see
 * zx279133_switch_probe()).  The XMAC is a block of its own and is
 * programmed either way - it is at its reset state already, every one of
 * its control words reading zero, and a MAC that is not configured
 * neither sends nor receives.
 */
static void zx279133_switch_xmac_configure(struct zx279133_switch *priv,
					    bool do_reset)
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

	if (do_reset) {
		/* smac_reset(): preserve the shared reset word and pulse XMAC0 only. */
		zx279133_switch_nppt_write(priv, ZX279133_SYS_SOFT_RESET,
					    priv->saved_xmac_reset &
					    ~ZX279133_XMAC0_RESET);
		usleep_range(ZX279133_XMAC_RESET_US,
			     ZX279133_XMAC_RESET_US + 100);
		zx279133_switch_nppt_write(priv, ZX279133_SYS_SOFT_RESET,
					    priv->saved_xmac_reset |
					    ZX279133_XMAC0_RESET);
	}

	zx279133_switch_xmac_program(priv, priv->usxgmii ?
					     ZX279133_XMAC_SPEED_USXGMII :
					     ZX279133_XMAC_SPEED_1G);

	priv->saved_sopc_duplex =
		zx279133_switch_nppt_read(priv, ZX279133_XMAC_SOPC_DUPLEX);
	priv->saved_sopc_send =
		zx279133_switch_nppt_read(priv, ZX279133_XMAC0_SOPC_SEND);
	value = priv->saved_sopc_duplex & ~ZX279133_XMAC0_SOPC_DUPLEX_MASK;
	zx279133_switch_nppt_write(priv, ZX279133_XMAC_SOPC_DUPLEX, value);
	/* Vendor xmac_init_by_work_mode(): __const_udelay(4295000). */
	usleep_range(4295, 4395);
	/*
	 * Publish every MAC, not one word of the array: the vendor's U-Boot
	 * publishes mac 0..3, each after its own ready word, and a MAC
	 * whose send-enable is never written does not move a frame however
	 * correct everything else is.
	 */
	zx279133_switch_sopc_publish(priv);

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

	/*
	 * The mirror image of the probe: the NPPT must stop handing frames to
	 * the conduit before the conduit stops being openable, and the
	 * datapath must stop being "ready" before it is released.  The order
	 * is the tree's RTL8372N driver's remove path.
	 */
	zx279133_lan_service_set_dsa_active(priv->service, false);
	if (priv->datapath_ready) {
		zx279133_lan_service_datapath_set_ready(priv->service, false);
		priv->datapath_ready = false;
	}

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

/*
 * The 9132's four UNI ports as DSA user ports.
 *
 * The shape of this is the tree's RTL8372N driver: the same conduit, the
 * same transport VID, the same DSA port numbers, the same service calls.
 * Only the register writes differ, and for the 9132 there are almost none,
 * because the 9132 forwards on its own - it learns source MACs by itself,
 * and its port attributes are programmed by the NPPT driver over the PPU's
 * indirect window (zx279133_9132_port_init()).
 */

/*
 * The link state of one UNI port, read from that port's own internal PHY.
 * The four PHYs are the 9132's, on the SoC's MDIO controller #0, at
 * 0x0a..0x0d.  BMSR's link bit is latched, so it has to be read twice.
 */
static int zx279133_switch_uni_link(struct zx279133_switch *priv, int port)
{
	int addr, bmsr;

	if (!priv->uni_mdio)
		return -1;

	addr = ZX279133_SWITCH_DSA_PHY(port);
	mdiobus_read(priv->uni_mdio, addr, MII_BMSR);
	bmsr = mdiobus_read(priv->uni_mdio, addr, MII_BMSR);
	if (bmsr < 0)
		return -1;

	return !!(bmsr & BMSR_LSTATUS);
}

/*
 * phylink asks the driver for the link state of a port it has no PHY to
 * poll.  The 9132's ports do have PHYs - internal ones - so the answer is
 * read there.  DSA sets poll_fixed_state when the driver provides this, so
 * phylink asks again and again, and a cable that arrives after boot is
 * noticed without anything else having to be done.
 */
static void zx279133_switch_phylink_fixed_state(struct dsa_switch *ds, int port,
						 struct phylink_link_state *state)
{
	struct zx279133_switch *priv = ds->priv;
	int link;

	if (port == ZX279133_SWITCH_DSA_CPU_PORT) {
		/* The CPU port's fixed link is in the device tree. */
		state->link = true;
		state->speed = SPEED_1000;
		state->duplex = DUPLEX_FULL;
		return;
	}

	if (port < ZX279133_SWITCH_DSA_PORT_MIN ||
	    port > ZX279133_SWITCH_DSA_PORT_MAX) {
		state->link = false;
		return;
	}

	link = zx279133_switch_uni_link(priv, port);
	state->link = link == 1;
	state->speed = SPEED_1000;
	state->duplex = DUPLEX_FULL;
}

static void zx279133_switch_phylink_get_caps(struct dsa_switch *ds, int port,
					     struct phylink_config *config)
{
	if (port == ZX279133_SWITCH_DSA_CPU_PORT) {
		/* The CPU port is the SoC's own SGMII lane at 1G. */
		__set_bit(PHY_INTERFACE_MODE_SGMII,
			  config->supported_interfaces);
		config->mac_capabilities = MAC_1000FD;
		return;
	}

	if (port < ZX279133_SWITCH_DSA_PORT_MIN ||
	    port > ZX279133_SWITCH_DSA_PORT_MAX)
		return;

	/*
	 * The ports are internal to the 9132: the PHY is on the far side of
	 * the switch's own fabric, not on a link the SoC drives.  The tree's
	 * RTL8372N driver describes its four internal ports the same way.
	 */
	__set_bit(PHY_INTERFACE_MODE_INTERNAL,
		  config->supported_interfaces);
	config->mac_capabilities = MAC_10 | MAC_100 | MAC_1000FD;
	config->lpi_capabilities = MAC_100FD | MAC_1000FD;
}

static enum dsa_tag_protocol
zx279133_switch_get_tag_protocol(struct dsa_switch *ds, int port,
				  enum dsa_tag_protocol conduit_proto)
{
	return DSA_TAG_PROTO_ZX279133_RTL8372N;
}

/*
 * Whether a port's traffic is LAN traffic or WAN traffic is the NPPT's
 * business, and the NPPT keeps one set of tables per transport VID.  This
 * is the tree's rule: a port that has joined a bridge is a LAN port, and a
 * port that has left it is a WAN port.
 *
 * Whether this board wants that rule is a separate question, and the answer
 * is not known from the tree, so it is a parameter rather than a decision.
 *
 * The tree's RTL8372N driver calls the same two functions, but on the
 * SR1010 no DSA port ever leaves a bridge, so only the "join" half of the
 * rule can ever fire there.  On this board one of the four jacks is a WAN,
 * and the tree's np.c says what the WAN type means: a WAN-typed port has a
 * PPPoE header removed on the way in and added on the way out, which is the
 * factory system's "WAN over a LAN jack" and is not what a plain DHCP
 * uplink wants.  So the default here is NOT to touch the NPPT's port type at
 * all: the four ports keep the type the NPPT driver gave them when it
 * brought them up, which is the ordinary-Ethernet one, and Linux's own
 * bridge and routing do the LAN/WAN split.
 *
 * The parameter exists so that the other answer can be measured on the same
 * build instead of being argued about: with wan_type=1 the tree's rule is
 * applied as written, and a port that leaves the bridge becomes a WAN-typed
 * port in the NPPT.  It is writable at runtime, so one RAM image can answer
 * both ways:
 *
 *	echo 1 > /sys/module/zx279133_switch/parameters/wan_type
 */
static bool zx279133_9132_wan_type;
module_param_named(wan_type, zx279133_9132_wan_type, bool, 0644);
MODULE_PARM_DESC(wan_type,
		 "1: a DSA port that leaves the bridge becomes a WAN-typed NPPT port (the tree's rule); 0: leave the NPPT's port type alone (default)");

static int zx279133_switch_port_bridge_join(struct dsa_switch *ds, int port,
					     struct dsa_bridge bridge,
					     bool *tx_fwd_offload,
					     struct netlink_ext_ack *extack)
{
	struct zx279133_switch *priv = ds->priv;

	if (!zx279133_9132_wan_type)
		return 0;

	/* The CPU port is the SoC's own lane, not a jack: it has no PPU
	 * whose type could be changed.
	 */
	if (port == ZX279133_SWITCH_DSA_CPU_PORT)
		return 0;

	return zx279133_lan_service_port_wan_set(priv->service,
						  ZX279133_RTL8372N_TRANSPORT_VID_BASE + port,
						  false);
}

static void zx279133_switch_port_bridge_leave(struct dsa_switch *ds, int port,
					      struct dsa_bridge bridge)
{
	struct zx279133_switch *priv = ds->priv;

	if (!zx279133_9132_wan_type)
		return;

	if (port == ZX279133_SWITCH_DSA_CPU_PORT)
		return;

	zx279133_lan_service_port_wan_set(priv->service,
					  ZX279133_RTL8372N_TRANSPORT_VID_BASE + port,
					  true);
}

/*
 * The port's PHY is the port's switch: the vendor's smac_set_port_enable()
 * is a PHY power-down, and that is what DSA asks for here.  The NPPT
 * driver already powered these four PHYs up when it probed, so this is
 * the same write again, at the moment the netdev is opened.
 */
static int zx279133_switch_port_enable(struct dsa_switch *ds, int port,
					struct phy_device *phy)
{
	struct zx279133_switch *priv = ds->priv;
	int addr, bmcr;

	/*
	 * The CPU port is the SoC's own SGMII lane, not a jack: there is no
	 * PHY behind it to power up.  The core asks for it to be enabled all
	 * the same time (net/dsa/port.c: dsa_port_enable() on a
	 * DSA_PORT_TYPE_CPU port), so a driver that answers "-EINVAL" for it
	 * refuses the whole switch and the registration ends in an error
	 * instead of the four jacks appearing.
	 */
	if (port == ZX279133_SWITCH_DSA_CPU_PORT)
		return 0;

	if (port < ZX279133_SWITCH_DSA_PORT_MIN ||
	    port > ZX279133_SWITCH_DSA_PORT_MAX)
		return -EINVAL;
	if (!priv->uni_mdio)
		return 0;

	addr = ZX279133_SWITCH_DSA_PHY(port);
	bmcr = mdiobus_read(priv->uni_mdio, addr, MII_BMCR);
	if (bmcr < 0)
		return bmcr;

	bmcr &= ~BMCR_PDOWN;
	mdiobus_write(priv->uni_mdio, addr, MII_BMCR, (u16)bmcr);

	return 0;
}

/*
 * Deliberately not a power-down.  The vendor stops the PHY when the port
 * goes down, but mainline's DSA port lifecycle is not the vendor's, and a
 * PHY that a stray close left powered down is a port that never comes
 * back.  The PHY stays up; the port's link state is reported from it.
 */
static void zx279133_switch_port_disable(struct dsa_switch *ds, int port)
{
}

/*
 * The 9132's VLAN tables are not written here.  What the ports do have is
 * a PVID: the NPPT driver programs each UNI port's untagged SVID to that
 * port's transport VID, the CPU port puts that VID on the frames it sends,
 * and the tree's tagger strips it again on the way in.  The bridge's own
 * VLANs are a Linux-side matter and the switch does not have to know them.
 */
static int zx279133_switch_port_vlan_filtering(struct dsa_switch *ds, int port,
						bool vlan_filtering,
						struct netlink_ext_ack *extack)
{
	return 0;
}

static int zx279133_switch_port_vlan_add(struct dsa_switch *ds, int port,
					 const struct switchdev_obj_port_vlan *vlan,
					 struct netlink_ext_ack *extack)
{
	return 0;
}

static int zx279133_switch_port_vlan_del(struct dsa_switch *ds, int port,
					 const struct switchdev_obj_port_vlan *vlan)
{
	return 0;
}

static void zx279133_switch_port_stp_state_set(struct dsa_switch *ds, int port,
						u8 state)
{
}

/*
 * The 9132's four internal PHYs, on a bus of the switch's own.
 *
 * The tree's DSA contract for a user port with no phy-handle is written
 * down in net/dsa/port.c:
 *
 *	User ports with no phy-handle or fixed-link are expected to connect
 *	to an internal PHY located on the ds->user_mii_bus at an MDIO
 *	address equal to the port number.
 *
 * The tree's RTL8372N driver - the same SoC family, the same
 * switch-with-internal-PHYs shape - is that case: a mii_bus of its own,
 * which the MDIO core scans, and a PHY driver of its own, which claims
 * the PHY devices on that bus by the bus's identity and the address
 * rather than by the PHY's id.
 *
 * The 9132's PHYs are not on a bus of the switch's.  They are on the SoC's
 * MDIO controller #0 - the one the NPPT driver already reaches for the PHY
 * enable - at 0x0a..0x0d, while the DSA ports are 4..7.  So the bus below
 * is a translation: the port number goes in, the SoC's MDIO address comes
 * out.  Everything else about it is the tree's own pattern.
 *
 * Why a bus of our own rather than the SoC's own: the core looks the PHY
 * up with mdiobus_get_phy(ds->user_mii_bus, port), and the port number is
 * the address it looks at.  The PHYs are at 0x0a..0x0d, and moving them
 * is not an option.
 */
static int zx279133_switch_mdio_read(struct mii_bus *bus, int addr, int regnum)
{
	struct zx279133_switch *priv = bus->priv;
	int val;

	if (addr < ZX279133_SWITCH_DSA_PORT_MIN ||
	    addr > ZX279133_SWITCH_DSA_PORT_MAX)
		return -EIO;
	if (!priv->uni_mdio)
		return -ENODEV;

	val = mdiobus_read(priv->uni_mdio, ZX279133_SWITCH_DSA_PHY(addr),
			   regnum);
	if (val < 0)
		return val;
	/*
	 * 0xffff is what a bus answers for a device that is not there, and
	 * the core's scan would take it for a PHY whose id is 0xffffffff -
	 * so the id registers are the ones where that answer has to become
	 * an error.
	 */
	if (val == 0xffff &&
	    (regnum == MII_PHYSID1 || regnum == MII_PHYSID2))
		return -ENODEV;

	return val;
}

static int zx279133_switch_mdio_write(struct mii_bus *bus, int addr, int regnum,
				      u16 val)
{
	struct zx279133_switch *priv = bus->priv;

	if (addr < ZX279133_SWITCH_DSA_PORT_MIN ||
	    addr > ZX279133_SWITCH_DSA_PORT_MAX)
		return -EIO;
	if (!priv->uni_mdio)
		return -ENODEV;

	return mdiobus_write(priv->uni_mdio, ZX279133_SWITCH_DSA_PHY(addr),
			      regnum, val);
}

/*
 * The PHY driver for the 9132's internal PHYs.
 *
 * It matches on the bus, not on the id: the chip answers 0x84b9:0x5032 over
 * Clause 22, and no driver in this tree claims that id - the tree's own
 * zx279051.c expects 0x000084b9 and reads it over Clause 45, and the
 * Realtek driver expects 0x001cc9.  Matching the bus instead of the id is
 * what the tree's RTL8372N does, for the same reason.
 */
static int zx279133_switch_phy_match(struct phy_device *phydev,
				      const struct phy_driver *phydrv)
{
	struct mii_bus *bus = phydev->mdio.bus;

	return bus && bus->read == zx279133_switch_mdio_read &&
	       phydev->mdio.addr >= ZX279133_SWITCH_DSA_PORT_MIN &&
	       phydev->mdio.addr <= ZX279133_SWITCH_DSA_PORT_MAX;
}

static int zx279133_switch_phy_probe(struct phy_device *phydev)
{
	phydev->is_internal = true;
	phydev->port = PORT_TP;

	return 0;
}

/*
 * What the four PHYs can do, stated rather than read.
 *
 * The capability registers of a Clause 22 PHY are 0x10 (1000BASE-T), 0x11
 * (100BASE-TX) and 0x12 (10BASE-T), and a driver that reads them learns
 * what the chip says.  This one does not, because the chip's answer to
 * them is not known yet - and a driver that guesses wrong in the other
 * direction is worse than one that says what the hardware is known to do:
 * the four jacks are gigabit, they are copper, and they auto-negotiate.
 * That is the vendor's own configuration too: the NPPT driver reads the
 * PHY's control register, finds auto-negotiation already on, and leaves
 * it that way.
 */
static int zx279133_switch_phy_get_features(struct phy_device *phydev)
{
	linkmode_zero(phydev->supported);
	linkmode_set_bit(ETHTOOL_LINK_MODE_TP_BIT, phydev->supported);
	linkmode_set_bit(ETHTOOL_LINK_MODE_MII_BIT, phydev->supported);
	linkmode_set_bit(ETHTOOL_LINK_MODE_Autoneg_BIT, phydev->supported);
	linkmode_set_bit(ETHTOOL_LINK_MODE_10baseT_Half_BIT,
			 phydev->supported);
	linkmode_set_bit(ETHTOOL_LINK_MODE_10baseT_Full_BIT,
			 phydev->supported);
	linkmode_set_bit(ETHTOOL_LINK_MODE_100baseT_Half_BIT,
			 phydev->supported);
	linkmode_set_bit(ETHTOOL_LINK_MODE_100baseT_Full_BIT,
			 phydev->supported);
	linkmode_set_bit(ETHTOOL_LINK_MODE_1000baseT_Full_BIT,
			 phydev->supported);
	linkmode_set_bit(ETHTOOL_LINK_MODE_Pause_BIT, phydev->supported);
	linkmode_set_bit(ETHTOOL_LINK_MODE_Asym_Pause_BIT, phydev->supported);

	return 0;
}

/*
 * Auto-negotiation, without touching anything but the control register.
 *
 * This is deliberately not genphy_config_aneg().  That one writes the
 * advertisement registers (0x04 and 0x07) and then the control register,
 * and writing registers whose layout is not known to a chip that is
 * already negotiating is how a working link is turned into a silent one -
 * it is exactly what happened to the RTL8226B on jack 4, where
 * phy_init_hw()'s soft reset and the config_init that followed it left
 * the PHY reporting 10baseT and no link (docs/09).
 *
 * So the only write here is the vendor's own enable: read the control
 * register, clear the power-down bit, make sure auto-negotiation is on,
 * and write it back.  Everything else the chip was doing keeps doing.
 */
static int zx279133_switch_phy_config_aneg(struct phy_device *phydev)
{
	int bmcr;

	bmcr = phy_read(phydev, MII_BMCR);
	if (bmcr < 0)
		return bmcr;

	bmcr &= ~BMCR_PDOWN;
	bmcr |= BMCR_ANENABLE;

	return phy_write(phydev, MII_BMCR, (u16)bmcr);
}

static struct phy_driver zx279133_switch_phy_driver = {
	.name = "ZX279133 9132 internal PHY",
	.flags = PHY_IS_INTERNAL,
	.match_phy_device = zx279133_switch_phy_match,
	.probe = zx279133_switch_phy_probe,
	.get_features = zx279133_switch_phy_get_features,
	.config_aneg = zx279133_switch_phy_config_aneg,
	.read_status = genphy_read_status,
};

/*
 * The bus, and the PHY devices on it.
 *
 * mdiobus_register() scans the bus, and the scan is what creates the PHY
 * devices: the core reads the id of every address the bus allows, and
 * makes a phy_device of the ones that answer.  That is why the bus's
 * phy_mask is the inverse of the switch's phys_mii_mask - the four user
 * ports and nothing else - and why the PHY driver has to be registered
 * before the bus is: the device the scan creates is matched against it.
 */
static int zx279133_switch_mdio_setup(struct zx279133_switch *priv,
				      struct dsa_switch *ds)
{
	struct mii_bus *bus;
	int ret;

	if (!priv->uni_mdio) {
		dev_warn(priv->dev,
			 "the 9132's PHY bus is not registered; the four jacks have no PHY\n");
		return -ENODEV;
	}

	ret = phy_drivers_register(&zx279133_switch_phy_driver, 1, NULL);
	if (ret)
		return ret;

	bus = mdiobus_alloc();
	if (!bus) {
		phy_drivers_unregister(&zx279133_switch_phy_driver, 1);
		return -ENOMEM;
	}

	bus->priv = priv;
	bus->name = "9132 internal MDIO";
	snprintf(bus->id, MII_BUS_ID_SIZE, "%s-mii", dev_name(ds->dev));
	bus->read = zx279133_switch_mdio_read;
	bus->write = zx279133_switch_mdio_write;
	bus->parent = ds->dev;
	bus->phy_mask = ~ds->phys_mii_mask;

	priv->user_mdio = bus;
	ds->user_mii_bus = bus;

	ret = mdiobus_register(bus);
	if (ret) {
		ds->user_mii_bus = NULL;
		priv->user_mdio = NULL;
		mdiobus_free(bus);
		phy_drivers_unregister(&zx279133_switch_phy_driver, 1);
		return ret;
	}

	dev_info(ds->dev,
		 "the 9132's four PHYs are on a bus of the switch's own: DSA ports %u..%u are MDIO %02x..%02x on the SoC's controller #0\n",
		 ZX279133_SWITCH_DSA_PORT_MIN, ZX279133_SWITCH_DSA_PORT_MAX,
		 ZX279133_SWITCH_DSA_PHY(ZX279133_SWITCH_DSA_PORT_MIN),
		 ZX279133_SWITCH_DSA_PHY(ZX279133_SWITCH_DSA_PORT_MAX));

	return 0;
}

static void zx279133_switch_mdio_teardown(struct zx279133_switch *priv)
{
	if (!priv->user_mdio)
		return;

	mdiobus_unregister(priv->user_mdio);
	mdiobus_free(priv->user_mdio);
	priv->user_mdio = NULL;
	phy_drivers_unregister(&zx279133_switch_phy_driver, 1);
}

/*
 * The switch's own "get ready" hook, and the one this tree's DSA core does
 * not let a driver skip.
 *
 * net/dsa.c says
 *
 *	err = ds->ops->setup(ds);
 *
 * with no "if (ds->ops->setup)" in front of it, so a driver that leaves the
 * hook out does not fail to build - it fails to boot.  The call goes to
 * address zero and the kernel dies with
 *
 *	Unable to handle kernel execute from non-executable memory at
 *	virtual address 0000000000000000
 *	pc : 0x0
 *	lr : dsa_register_switch+0x904/0xde0
 *
 * Measured 2026-10-21 09:05, run 98: the 9132 came up, the NPPT brought
 * the four internal PHYs up, the lane inherited the bootloader's link, and
 * then the switch registration jumped to zero.  The tree's own RTL8372N
 * driver - the only other switch driver in this tree - has a .setup, which
 * is why the core can call it without looking.
 *
 * The four PHYs come first in it, because everything else here is about a
 * switch that has them: the ports are the ports because their PHYs are
 * there, and the core asks for them by the port number on a bus of the
 * switch's own.  The bus has to exist before the core sets the ports up,
 * and the core sets the ports up after this hook returns.
 */
static int zx279133_switch_setup(struct dsa_switch *ds)
{
	struct zx279133_switch *priv = ds->priv;
	int ret;

	/*
	 * Not fatal: a switch whose four jacks have no PHY is still a switch
	 * with a CPU port and a conduit, and the four ports that cannot be
	 * set up are reported by the core as they fail.  Failing here
	 * instead would take the CPU port down with them.
	 */
	ret = zx279133_switch_mdio_setup(priv, ds);
	if (ret)
		dev_err(ds->dev,
			"the 9132's PHYs could not be brought onto a bus of the switch's own: %pe\n",
			ERR_PTR(ret));

	dev_info(ds->dev,
		 "the 9132 is serving: the jacks are the 9132's UNI ports %u..%u, they are DSA ports %u..%u, the CPU port is %u and the transport VIDs are %u..%u\n",
		 ZX279133_SWITCH_UNI_PORT_MIN, ZX279133_SWITCH_UNI_PORT_MAX,
		 ZX279133_SWITCH_DSA_PORT_MIN, ZX279133_SWITCH_DSA_PORT_MAX,
		 ZX279133_SWITCH_DSA_CPU_PORT,
		 ZX279133_RTL8372N_TRANSPORT_VID_BASE +
		 ZX279133_SWITCH_DSA_PORT_MIN,
		 ZX279133_RTL8372N_TRANSPORT_VID_BASE +
		 ZX279133_SWITCH_DSA_PORT_MAX);

	return 0;
}

static void zx279133_switch_teardown(struct dsa_switch *ds)
{
	struct zx279133_switch *priv = ds->priv;

	zx279133_switch_mdio_teardown(priv);

	dev_info(ds->dev, "the 9132 stops serving\n");
}

static const struct dsa_switch_ops zx279133_switch_dsa_ops = {
	.get_tag_protocol = zx279133_switch_get_tag_protocol,
	.setup = zx279133_switch_setup,
	.teardown = zx279133_switch_teardown,
	.port_bridge_join = zx279133_switch_port_bridge_join,
	.port_bridge_leave = zx279133_switch_port_bridge_leave,
	.port_enable = zx279133_switch_port_enable,
	.port_disable = zx279133_switch_port_disable,
	.port_vlan_filtering = zx279133_switch_port_vlan_filtering,
	.port_vlan_add = zx279133_switch_port_vlan_add,
	.port_vlan_del = zx279133_switch_port_vlan_del,
	.port_stp_state_set = zx279133_switch_port_stp_state_set,
	.phylink_get_caps = zx279133_switch_phylink_get_caps,
	.phylink_fixed_state = zx279133_switch_phylink_fixed_state,
};

/*
 * The conduit is the netdev the CPU port's frames run on, and DSA needs it
 * to exist before it can register: a switch whose CPU port has no conduit
 * is a switch with no way in.  The device tree says which netdev it is -
 * the CPU port node's "ethernet" property - and this is the same lookup
 * DSA itself does.  Doing it here, before anything else is claimed, is what
 * keeps a missing conduit from turning into a deferred probe that would
 * take the shared datapath twice.
 */
static bool zx279133_switch_conduit_present(struct device *dev)
{
	/*
	 * netdevice_tracker, not "struct netdev_tracker": this tree has no
	 * such type.  Its netdev_hold()/netdev_put() take a
	 * netdevice_tracker *, and its own DSA core declares the matching
	 * member that way (include/net/dsa.h: "netdevice_tracker
	 * conduit_tracker;").  A local tracker is therefore declared with
	 * the tree's own typedef.
	 */
	netdevice_tracker tracker;
	struct device_node *ports, *port;
	bool present = false;

	ports = of_get_child_by_name(dev->of_node, "ports");
	if (!ports)
		return false;

	for_each_available_child_of_node(ports, port) {
		struct device_node *ethernet;
		struct net_device *conduit;

		ethernet = of_parse_phandle(port, "ethernet", 0);
		if (!ethernet)
			continue;

		rtnl_lock();
		conduit = of_find_net_device_by_node(ethernet);
		if (conduit) {
			netdev_hold(conduit, &tracker, GFP_KERNEL);
			put_device(&conduit->dev);
		}
		rtnl_unlock();
		of_node_put(ethernet);

		if (conduit) {
			netdev_put(conduit, &tracker);
			present = true;
		}
		break;
	}
	of_node_put(ports);

	return present;
}

static int zx279133_switch_dsa_register(struct device *dev,
					 struct zx279133_switch *priv)
{
	struct dsa_switch *ds;
	int ret;

	ds = devm_kzalloc(dev, sizeof(*ds), GFP_KERNEL);
	if (!ds)
		return -ENOMEM;

	ds->dev = dev;
	ds->num_ports = ZX279133_SWITCH_DSA_NUM_PORTS;
	ds->configure_vlan_while_not_filtering = true;
	ds->max_num_bridges = ZX279133_SWITCH_DSA_PORT_MAX -
			      ZX279133_SWITCH_DSA_PORT_MIN + 1;
	ds->ops = &zx279133_switch_dsa_ops;
	ds->priv = priv;
	priv->ds = ds;

	ret = dsa_register_switch(ds);
	if (ret) {
		priv->ds = NULL;
		return dev_err_probe(dev, ret,
				     "failed to register the 9132 as a DSA switch\n");
	}

	priv->dsa_registered = true;
	dev_info(dev,
		 "registered the 9132 as a DSA switch: ports %u..%u carry the transport VIDs %u..%u\n",
		 ZX279133_SWITCH_DSA_PORT_MIN, ZX279133_SWITCH_DSA_PORT_MAX,
		 ZX279133_RTL8372N_TRANSPORT_VID_BASE +
		 ZX279133_SWITCH_DSA_PORT_MIN,
		 ZX279133_RTL8372N_TRANSPORT_VID_BASE +
		 ZX279133_SWITCH_DSA_PORT_MAX);

	return 0;
}

static int zx279133_switch_probe(struct platform_device *pdev)
{
	struct device *dev = &pdev->dev;
	struct device_node *parent_np;
	struct device_node *pcs_np;
	struct device_node *mdio_np;
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

	priv->dev = dev;

	priv->service = dev_get_drvdata(dev->parent);
	if (!priv->service)
		return dev_err_probe(dev, -EPROBE_DEFER,
				     "NPPT LAN service is not ready\n");
	if (!zx279133_lan_service_valid(priv->service))
		return dev_err_probe(dev, -EINVAL,
				     "incomplete NPPT LAN service\n");

	/*
	 * The 9132's four internal PHYs are on the SoC's MDIO controller #0,
	 * which the parent names.  Their link state is the port's link state,
	 * so this bus is what the DSA ports report from.
	 */
	mdio_np = of_parse_phandle(parent_np, "zx279133,uni-phy-bus", 0);
	if (mdio_np) {
		priv->uni_mdio = of_mdio_find_bus(mdio_np);
		of_node_put(mdio_np);
	}
	if (!priv->uni_mdio)
		dev_warn(dev,
			 "the 9132's PHY bus is not registered; port link state will be unknown\n");

	ret = zx279133_lan_service_datapath_get(priv->service);
	if (ret)
		return dev_err_probe(dev, ret,
				     "failed to acquire shared NPPT/IDM datapath\n");
	priv->datapath_held = true;

	/*
	 * The four internal-PHY MACs first.  They are the front end of every
	 * LAN port on this board, and a frame that arrives from a LAN port
	 * stops at the PHY if they are still at their reset value - which is
	 * what this driver used to leave them at.
	 */
	zx279133_switch_smac_init(priv);

	/*
	 * The lane, as the bootloader left it.  Nothing here has touched
	 * the XMAC0 or the XPCS0 yet, and this board's bootloader did leave
	 * the lane working - its own net bring-up moves eleven megabytes
	 * through it - so the words it left are the reference the rest of
	 * this driver is measured against.
	 */
	zx279133_switch_lane_snapshot(priv, "pre-serdes");

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

	/*
	 * The lane's rate is not a free choice.  This board's vendor
	 * bootloader runs XMAC0 in SGMII ("xmac 0 speed set 2 in sgmii") and
	 * the 9132's CPU port is a 1G port, while the tree's RTL8372N driver
	 * runs this very same XPCS0/Uni-SerDes lane at USXGMII 10G for the
	 * RTL8372N's CPU port.  The Uni SerDes driver only accepts the
	 * interface it implements, so ask for SGMII first and fall back to
	 * USXGMII rather than losing the whole lane to a -EOPNOTSUPP.
	 */
	ret = phy_set_mode_ext(priv->serdes, PHY_MODE_ETHERNET,
			       PHY_INTERFACE_MODE_SGMII);
	if (ret) {
		dev_warn(dev,
			 "Uni SerDes does not take SGMII (%d); trying USXGMII\n",
			 ret);
		ret = phy_set_mode_ext(priv->serdes, PHY_MODE_ETHERNET,
				       PHY_INTERFACE_MODE_USXGMII);
		if (!ret)
			priv->usxgmii = true;
	}
	if (ret) {
		/*
		 * Neither mode was accepted.  Do not give up: the driver
		 * reported at probe that the SerDes is already in the 1G
		 * profile the bootloader left ("Uni SerDes 1G inherited: mode
		 * 0x1 already"), so powering it up untouched may still give
		 * the 1G lane this board's 1G CPU port needs - and the XPCS0
		 * status is the evidence either way.
		 */
		dev_warn(dev,
			 "Uni SerDes takes neither SGMII nor USXGMII (%d); powering it up untouched\n",
			 ret);
	} else {
		dev_info(dev, "Uni SerDes lane set to %s mode\n",
			 priv->usxgmii ? "USXGMII" : "SGMII");
	}

	ret = phy_power_on(priv->serdes);
	if (ret) {
		dev_warn(dev, "Uni SerDes first lock attempt failed: %d; retrying\n",
			 ret);
		msleep(20);
		ret = phy_power_on(priv->serdes);
	}
	if (ret) {
		/*
		 * Keep going.  The SerDes is what carries the lane, so a
		 * failure here is the headline - but the XPCS0 status word is
		 * still readable, and it is the only evidence this round can
		 * produce.  Bailing out here would print nothing.
		 */
		dev_err(dev, "failed to power on Uni SerDes: %d\n", ret);
	} else {
		priv->serdes_powered = true;
	}

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

	/*
	 * The same lane, now with the XPCS0's own registers readable.
	 *
	 * If the PCS still says the link is up, the bootloader brought it up
	 * and this driver has not broken it yet - and reprogramming it is
	 * exactly what breaks it: the 1G SGMII profile below takes the PCS
	 * from the bootloader's 0x380b to 0x280b, one bit of link status
	 * short, and a lane that was carrying traffic stops carrying it.
	 * So in that case the bootloader's configuration is adopted as it
	 * is.
	 *
	 * The XMAC is a different matter, and this is where the board used to
	 * stop.  The XMAC0 is the MAC on the far side of the lane - the one
	 * that faces the 9132's CPU port - and the bootloader leaves it at
	 * zero: every one of its control words reads 0x00000000, so the MAC
	 * was never configured by anybody.  This driver used to skip the
	 * XMAC along with the PCS, and a MAC that is not configured neither
	 * sends nor receives: the frames the stack queued went into the IDM
	 * and stopped there, the XMAC's own transmit counter stayed at zero,
	 * and nothing at all crossed the lane in either direction.
	 *
	 * So the XMAC is programmed in this branch as well.  What is not
	 * repeated is the reset: the soft reset word is shared with the PCS,
	 * and the XMAC is at its reset state already.
	 */
	zx279133_switch_lane_snapshot(priv, "pre-pcs");
	status = zx279133_switch_nppt_read(priv, ZX279133_NPPT_XPCS0_STATUS);
	if ((status & 0x3800) == 0x3800) {
		dev_info(dev,
			 "the bootloader left the PCS link up (0x%04x); inheriting it\n",
			 status);
		zx279133_switch_sopc_publish(priv);
		zx279133_switch_xmac_configure(priv, false);
		goto report;
	}

	ret = priv->usxgmii ? zx279133_switch_xpcs_usxgmii(priv) :
			      zx279133_switch_xpcs_sgmii(priv);
	if (ret) {
		/*
		 * Do not give up here.  The read-back below is the evidence
		 * this round is after: a lane that was programmed wrongly and
		 * a lane that was never programmed look the same from the
		 * outside otherwise, and this is the only place the XPCS0
		 * status word can be read at all.
		 */
		dev_err(dev, "failed to configure XPCS0 for %s mode: %d\n",
			priv->usxgmii ? "USXGMII" : "SGMII", ret);
		goto report;
	}

	ret = zx279133_switch_xpcs_bypass(priv, true);
	if (ret) {
		dev_err(dev, "failed to enable XPCS0 bypass: %d\n", ret);
		goto report;
	}

	zx279133_switch_xmac_configure(priv, true);

	if (!priv->usxgmii) {
		/* Re-apply after the XMAC/SOPC work; the C45 AN bits are lost. */
		ret = zx279133_switch_xpcs_apply_sgmii_vend2(priv, DUPLEX_FULL);
		if (ret)
			dev_err(dev,
				"failed to re-apply the XPCS0 AN state: %d\n",
				ret);
	}
	usleep_range(10000, 11000);

report:
	/*
	 * Report what the lane says about itself.  This is the G2 evidence:
	 * 0x0090 bits 11..13 are the XPCS0 link status the tree's RTL8372N
	 * driver polls for the same block, and 0x0084 is the NPPT's XMAC
	 * link word.
	 *
	 * The link is given a moment to settle before it is read: the tree's
	 * driver polls the same word for up to 400 ms after programming the
	 * PCS, and a status read in the same millisecond as the last write
	 * says nothing about whether the lane came up.
	 */
	{
		int tries;

		for (tries = 0; tries < 40; tries++) {
			status = zx279133_switch_nppt_read(priv,
						ZX279133_NPPT_XPCS0_STATUS);
			if ((status & 0x3800) == 0x3800)
				break;
			usleep_range(25000, 26000);
		}
	}
	dev_info(dev,
		 "XMAC0 datapath enabled; XPCS0 status=0x%04x link=%s xmac_link=0x%04x; 9132 UNI ports %u..%u\n",
		 status,
		 ((status & 0x3800) == 0x3800) ? "up" : "down",
		 zx279133_switch_nppt_read(priv, ZX279133_NPPT_XMAC_LINK),
		 ZX279133_SWITCH_UNI_PORT_MIN, ZX279133_SWITCH_UNI_PORT_MAX);

	/* And every word of the lane, after this driver's own work. */
	zx279133_switch_lane_snapshot(priv, "after");

	/*
	 * The four jacks as DSA ports.  The conduit has to be there first: a
	 * DSA switch whose CPU port has no conduit is a switch with no way
	 * in, and the conduit is the netdev the switch driver on the parent
	 * registers - so it is asked for by name rather than assumed.
	 */
	if (!zx279133_switch_conduit_present(dev)) {
		dev_err(dev,
			"the LAN conduit is not registered; the 9132 stays one flat port\n");
		return 0;
	}

	/*
	 * The LAN datapath has to be declared ready *before* the DSA switch is
	 * registered, and this is the tree's RTL8372N driver's order (its
	 * zx279133_rtl8372n_probe() does the same three steps in this order).
	 *
	 * It matters because two things in the parent are gated on it, and
	 * both of them are the LAN path:
	 *
	 *   - zx279133_lan_netdev_open() (the conduit's ndo_open) answers
	 *     -ENETDOWN unless eth->lan_datapath_ready is set, and DSA's
	 *     dsa_user_open() opens the conduit before it does anything else.
	 *     With the flag unset, every user port fails to open with
	 *     "failed to open conduit lan-cpu0" and the bridge puts the port
	 *     into the disabled state - which is exactly what the run 102
	 *     console showed.
	 *
	 *   - the IDM receive path drops every frame that comes from the LAN
	 *     side unless eth->lan_dsa_active is set: "if (lan_source &&
	 *     !lan_dsa_active) { drop }".  That flag is what tells the NPPT
	 *     that the DSA conduit is live, and it is what makes the
	 *     descriptor's source port be turned back into the transport VID
	 *     the tree's tagger looks for (zx279133_idm_rx_lan_vport(),
	 *     zx279133_idm_rx_preserve_dsa_vlan()).
	 *
	 * Neither call was made here, so the switch registered, the four jacks
	 * appeared, and not one frame ever moved.
	 */
	zx279133_lan_service_datapath_set_ready(priv->service, true);
	priv->datapath_ready = true;

	ret = zx279133_switch_dsa_register(dev, priv);
	if (ret) {
		/*
		 * The lane and everything it says about itself are already in
		 * the log above, so a DSA that would not register does not take
		 * the round's answer with it.
		 */
		dev_err(dev, "the 9132 did not register as a DSA switch: %d\n",
			ret);
		zx279133_lan_service_datapath_set_ready(priv->service, false);
		priv->datapath_ready = false;
		return 0;
	}

	/* The conduit is live; the NPPT may hand its frames to it now. */
	zx279133_lan_service_set_dsa_active(priv->service, true);

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
