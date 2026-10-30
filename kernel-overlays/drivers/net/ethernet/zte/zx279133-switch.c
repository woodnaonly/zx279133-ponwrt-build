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
 * The XPCS programming below is the tree's own 1G profile, taken
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
#include <linux/workqueue.h>

#include <net/dsa.h>
#include <net/dsa_zx279133_rtl8372n.h>
#include <net/switchdev.h>

#include "zx279133.h"
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
 * The XMAC0's own words on this lane.
 *
 * RX_CTRL is xamc_init_conf_by_speed()'s (np_133.ko 0x1a240) literal, and the
 * "IPC" bit 0x200 is the one xamc_init_conf_by_speed() puts into MISC_CFG
 * (0x1a288) - not into RX_CTRL, where the tree's RTL8372N driver has it.
 *
 * FRAME_CFG and MISC_CFG are the values the *board's own* U-Boot leaves behind
 * when it moves traffic through this jack, and they are NOT the vendor Linux's
 * (docs/92 §79).  Measured 2026-10-28 08:22, from run210's "lane pre-serdes"
 * snapshot - that snapshot is the state U-Boot's net bring-up left, because
 * mainline had not touched the MAC yet at that point:
 *
 *	tx_ctrl = 0x60010000   rx_ctrl = 0x3e8000c6
 *	frame_cfg = 0x80000001   mode = 0x00000000
 *	duplex = 0x00000100   misc_cfg = 0x00002000
 *
 * The vendor Linux's xamc_init_conf_by_speed() differs in exactly two of those
 * words: FRAME_CFG 0x80000000 (bit 0 cleared) and MISC_CFG with the IPC bit
 * 0x200 set.  Both are the values this driver used until now.
 *
 * Why U-Boot's are the ones to copy: **U-Boot moved 11 471 728 bytes through
 * this jack** (2026-10-28 03:45:57, "tftpboot ... Bytes transferred =
 * 12162040"), and **the vendor's own Linux moved none** - its 2.5G port never
 * received a byte (docs/07 补九十四).  Its XMAC0 configuration therefore comes
 * with a known-broken history, while U-Boot's comes with a known-working one.
 *
 * FRAME_CFG bit 0 is the one the tree's RTL8372N driver also sets (its
 * XMAC0 image is 0x80000001), so this is not U-Boot against the tree: it is
 * U-Boot and the tree against the vendor Linux.
 *
 * MISC_CFG's IPC bit is left alone for the same reason: on this lane the far
 * side is a SerDes, not an internal copper PHY, and U-Boot left it clear.
 */
#define ZX279133_XMAC_VENDOR_RX_CTRL	0x3e8000c6
#define ZX279133_XMAC_UBOOT_FRAME_CFG	0x80000001
/*
 * xmac_set_speed_sel(): 3 = 1G SGMII, 6 = 2.5G, 4 = 100M, 7 = 10M.
 * The USXGMII image the tree's RTL8372N driver writes leaves the field at
 * 0, which is what that lane runs at, so the two profiles differ here.
 *
 * 6 is the rate this port needs when the peer on the jack negotiates 2.5G;
 * see zx279133_switch_xmac0_speed() for the measurement.
 */
#define ZX279133_XMAC_SPEED_1G		3
#define ZX279133_XMAC_SPEED_2P5G	6
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
 *
 * How many channels there are was the wrong number for a long time.  The
 * array is not a run of XMACs: np_133.ko's SMAC side uses the offsets
 * 0x34294 + 4 * mac and 0x342b0 + 4 * mac for the four internal-PHY MACs,
 * so the words this driver was reading for "channel 0" are SMAC0's READY
 * word and SMAC0's SEND word, and the words for "channel 3" are SMAC3's
 * READY word and a word past the end of the SEND array.
 *
 * The XMAC channels are the two the vendor's own XMAC function reaches:
 * 0x342a4 / 0x342c0 for XMAC0 and 0x342a8 / 0x342c4 for XMAC1.  A third
 * pair follows at 0x342ac / 0x342c8 for the SMAC on the RGMII port, and
 * after that the words are not a channel list any more.
 *
 * So the count is two, and the "channel 3" this driver used to publish was
 * a write to 0x342cc - a word outside the array, in the middle of the
 * SOPC's own register block.  The live board reads 0x342a4 = 0 (XMAC0's
 * ready bit is not set), 0x342a8 = 1 (XMAC1's is), 0x342b0..0x342bc = 1
 * (the four SMAC send-enables) and 0x342c0..0x342c8 = 1, which is what the
 * count of two explains and what the count of four does not.
 */
#define ZX279133_XMAC_SOPC_READY_BASE	0x342a4
#define ZX279133_XMAC_SOPC_SEND_BASE	0x342c0
#define ZX279133_XMAC_SOPC_CHANNELS	2

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
/*
 * The PCS mode field of VR_MII_AN_CTRL (bits 2:1).
 *
 * The vendor's two 1G profiles differ in exactly one value here.  Both
 * xpcs_sgmii_mode_conf() and xpcs_1000base_x_conf() in np_133.ko have the
 * same shape - prepare_for_switch_mode(), one shared helper, then the work
 * mode is stored - and the helper is called with the PCS mode as its fourth
 * argument: 2 for SGMII (np_133.ko 0x1c8c8) and 0 for 1000BASE-X
 * (np_133.ko 0x1c65c).  Disassembled 2026-10-28 with staging/ctl/adis.py.
 *
 * This lane has to be 1000BASE-X and not SGMII: the device on the far end
 * of it is a 1000BASE-X device.  The RTL8226 on the 2.5G jack answers
 * Clause 45 with PCS BMCR 0x3000 (speed 1000BASE-X, AN enabled) and PMD
 * status 0x0002 ("1000BASE-X capable"), both read out of the board's own
 * U-Boot on 2026-10-28 05:28 with its PCS status at 0x0069 - link up,
 * block lock and block sync all set.
 */
#define ZX279133_XPCS_PCS_MODE_1000BASE_X	0
#define ZX279133_XPCS_PCS_MODE_SGMII		2
#define ZX279133_XPCS_LOW_POWER		BIT(11)
#define ZX279133_XPCS_SPEED_SEL		BIT(13)
#define ZX279133_XPCS_SR_MII_SPEED_MASK (BIT(13) | BIT(12) | BIT(6) | BIT(5) | BIT(0))
/*
 * The SR-MII speed word, as the vendor's xpcs_set_sr_mii_ctrl_speed()
 * (np_133.ko 0x1b910) spells it out.  That function reads a byte out of a
 * table and jumps through a second table, so the speed number and the value
 * it turns into are not in the order the cases appear in the source:
 *
 *	speed 0 -> 0x0000	 speed 3 -> 0x0020
 *	speed 1 -> 0x2000	 speed 4 -> 0x2020
 *	speed 2 -> 0x0040	 speed 5 -> 0x2040
 *
 * The byte table is np_133.ko's .rodata+0x187c (0a 00 02 04 06 08) and the
 * cases are at 0x1b948 (0x2000), 0x1b950 (0x40), 0x1b958 (0x20), 0x1b960
 * (0x2020), 0x1b968 (0x2040) and 0x1b970 (0).
 *
 * The 1000BASE-X path passes speed 2: xmac_1gbase_x_conf() (0x1a6ec) calls
 * xpcs_1000base_x_conf() with speed 2 and duplex 1, and that reaches the
 * speed function unchanged.  So the value for this lane is 0x40 = BIT(6),
 * and the board agrees: with XMAC0 set to "work in 1000BASE-X mode" the
 * SR_MII_CTRL word at 0x1a7c0000 reads 0x0140, which is exactly the duplex
 * bit (BIT(8)) plus 0x40.
 *
 * The mask is the vendor's own, 0x2061.  It clears bit 12 and bit 0 as
 * well, and that matters: the word the SGMII mode leaves behind is 0x1140,
 * and a modify with the narrower mask would leave bit 12 set and produce
 * 0x1140 instead of the 0x0140 the base-X state has to be.
 */
#define ZX279133_XPCS_SR_MII_SPEED_1000BASE_X	BIT(6)
/*
 * The lane's four PCS registers as the board's own U-Boot leaves them.
 *
 * Read out of the loader's prompt on 2026-10-28 at 0x1a7c0000 and
 * 0x1a7e0000, in a power cycle whose net bring-up had already configured the
 * outerphy port:
 *
 *	0x1a7c0000	0x1140 0x0139 0x7996 0xced0 0x0020 0xd000 0x0002 0x0000
 *	0x1a7e0000	0x2200 0x0005 0x001a 0x0000 0x0000 0x0000 0x0000 0x0000
 *
 * These are the 1G branch's words.  The 2.5G branch leaves BMCR 0x2140,
 * DIG_CTRL1 0x2000 and AN_CTRL 0x0000 instead (docs/92 §96.15).
 *
 * The window is four bytes per register, so:
 *	0x1a7c0000		VEND2 0x0000, the BMCR
 *	0x1a7c0010		VEND2 0x0004, the SR-MII speed word
 *	0x1a7e0000		VEND2 0x8000, the DIG_CTRL1
 *	0x1a7e0004		VEND2 0x8001, the AN_CTRL
 *
 * The AN_CTRL's mode field (bits 2:1) is 2, i.e. SGMII.
 *
 * One thing this comment used to say and must not say again: that the
 * loader's net bring-up "carried 11,471,728 bytes over" this lane.  It did
 * not.  The loader's own log says "Using eth0 device" before it starts the
 * TFTP (docs/92 §96.18): the bytes went over eth0, that is the SoC's MAC
 * through the PON SerDes to the 9132's CPU port and out of the LAN2 jack.
 * The loader configured this lane, and the far end says it was up
 * (1e.0x758d = 0x801e, 7.0x01 = 0x00e9, docs/92 §96.16), but it never
 * moved a byte over it.
 */
#define ZX279133_XPCS_VEND2_SR_MII_SPEED	0x0004
#define ZX279133_XPCS_UBOOT_BMCR		0x1140
#define ZX279133_XPCS_UBOOT_SR_MII_SPEED	0x0020
#define ZX279133_XPCS_UBOOT_DIG_CTRL1		0x2200
#define ZX279133_XPCS_UBOOT_AN_CTRL		0x0005
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
/*
 * The four jacks with an internal PHY, and then the 2.5G jack.
 *
 * The 2.5G jack is the 9132's fifth user port - the one the vendor calls
 * the outerphy - and its PHY is the external RTL8226, so it is not on the
 * 9132's own MDIO bus and has no PHY address in the 0x0a..0x0d run.
 *
 * It is a user port like the other four, and the shape is kept: the user
 * ports are a run and the CPU port is the one after them.
 */
#define ZX279133_SWITCH_DSA_JACK_MAX	7
#define ZX279133_SWITCH_DSA_OUTER_PORT	8
#define ZX279133_SWITCH_DSA_PORT_MAX	8
#define ZX279133_SWITCH_DSA_NUM_PORTS	10
/*
 * The CPU port: the one that faces the conduit.
 *
 * It is index 9 and not 0 because that is what the tree's own DSA switch
 * does.  The tree's RTL8372N node (arch/arm64/boot/dts/zte/zx279133-sr1010.dts)
 * describes its chassis LAN1..LAN4 as ports 4..7 and its CPU port as
 * port@8, and its driver answers "port == 8" wherever the CPU port is
 * asked about (zx279133_rtl8372n_phylink_fixed_state()).  This tree's DSA
 * fork looks a port up by index (dsa_to_port()) and leaves the numbers
 * between the CPU port and the user ports unused, so the same shape is
 * used here: the user ports are 4..8 and the CPU port is 9.
 *
 * The tagger does not care which end is which - it puts
 * TRANSPORT_VID_BASE + port on a user port's frames and reads the port
 * back out of the VID - so the five user ports keep the transport VIDs
 * 59..63, which are exactly the PVIDs zx279133_9132_port_init() programs
 * for the 9132's PPU ports 1..5.
 */
#define ZX279133_SWITCH_DSA_CPU_PORT	9
/* The 9132's PPU port behind a DSA port: DSA 4..8 are PPU 1..5. */
#define ZX279133_SWITCH_DSA_PPU(port)	((port) - \
					 (ZX279133_SWITCH_DSA_PORT_MIN - 1))
/* The 9132's internal PHY behind a DSA port: PPU 1..4 are PHY 0x0a..0x0d. */
#define ZX279133_SWITCH_DSA_PHY(port)	(0x0a + \
					 (port) - ZX279133_SWITCH_DSA_PORT_MIN)
/*
 * The external PHY: the RTL8226 on the 2.5G jack, at address 5 on the SoC's
 * MDIO controller #1.  It is not on the 9132's own bus, so it has no address
 * in the 0x0a..0x0d run above.
 */
#define ZX279133_SWITCH_OUTERPHY_ADDR	5
/* The auto-negotiation block of a Clause 45 PHY, and its status register. */
#define ZX279133_SWITCH_OUTERPHY_AN_DEVAD	7
#define ZX279133_SWITCH_OUTERPHY_AN_STATUS	1
/* The auto-negotiation's "link partner detect" bit. */
#define ZX279133_SWITCH_OUTERPHY_AN_PARTNER	BIT(2)

/*
 * The outer PHY's SerDes option registers, and the two words that say which
 * SerDes class the chip is in.
 *
 * The RTL8226 answers a clause 45 read of its vendor bank (devad 0x1e) with
 * these words:
 *
 *	0x1e.0x75f3	0x030d	out of the box	0x0002	the 1G class
 *	0x1e.0x697a	0x0003	out of the box	0x0000	the 1G class
 *	0x1e.0x6a04	0x0503	out of the box	0x0403	the 1G class
 *	0x1e.0x6f10	0x084a	out of the box	untouched	the loader leaves it
 *	0x1e.0x6f11	0x0021	out of the box	untouched	the loader leaves it
 *
 * The "1G class" column is what this board's own U-Boot leaves behind after
 * its net bring-up has configured this PHY - measured at the loader's prompt
 * on 2026-10-30 12:26, right after "mac 4 phy status changed: 1000M
 * full-duplex" - and the "out of the box" column is what the same chip reads
 * when nothing has configured it.  That is the state a board that boots from
 * flash is in, because the loader's net bring-up only runs when the loader is
 * given a network to talk to.
 *
 * The difference between the two states is the whole of this lane's problem
 * (docs/92 §96.46): with the chip in the 1G class the lane carries code and
 * the board answers pings on it; with the chip in its out-of-the-box class the
 * SoC side - which mainline does put in the 1G class, the Uni SerDes prints
 * "1G profile written: mode 0x101" - and the far end disagree about the rate,
 * the base-X lane never locks, and every word the driver reads still looks the
 * way it looked when it worked.
 *
 * Two more words say whether the lane carries code:
 *
 *	0x1e.0x7580	the SerDes mode the chip is in.  Its low six bits are the
 *			class: 0x02 is 1000BASE-X, which is what this lane needs.
 *	0x1e.0x758d	the lane's own word.  Bit 2 is the one that says the lane
 *			carries code: it reads 0x801e when the lane works and
 *			0x8018 when it does not.
 *
 * Both measured on the running board on 2026-10-30 12:29-12:32, by writing
 * the three option registers by hand and watching the board stop answering
 * pings (0x8018) and start again (0x801e).
 */
#define ZX279133_SWITCH_OUTERPHY_SERDES_DEVAD	0x1e
#define ZX279133_SWITCH_OUTERPHY_SERDES_OPT_1	0x75f3
#define ZX279133_SWITCH_OUTERPHY_SERDES_OPT_2	0x697a
#define ZX279133_SWITCH_OUTERPHY_SERDES_OPT_3	0x6a04
#define ZX279133_SWITCH_OUTERPHY_SERDES_MODE	0x7580
#define ZX279133_SWITCH_OUTERPHY_SERDES_LINK	0x758d
/* The mode word's low six bits, and the class this lane needs. */
#define ZX279133_SWITCH_OUTERPHY_SERDES_MODE_MASK	0x3f
#define ZX279133_SWITCH_OUTERPHY_SERDES_MODE_1G	0x02
/* The bit of the lane's word that says the lane carries code. */
#define ZX279133_SWITCH_OUTERPHY_SERDES_LINK_BIT	BIT(2)

/*
 * The 1G class, word for word: the three registers and the values this
 * board's own U-Boot leaves in them.
 */
static const u16 zx279133_switch_outerphy_serdes_1g[][2] = {
	{ ZX279133_SWITCH_OUTERPHY_SERDES_OPT_1, 0x0002 },
	{ ZX279133_SWITCH_OUTERPHY_SERDES_OPT_2, 0x0000 },
	{ ZX279133_SWITCH_OUTERPHY_SERDES_OPT_3, 0x0403 },
};

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
	 * The bus of the external PHY - the RTL8226 on the 2.5G jack - which
	 * is the SoC's MDIO controller #1.  It is a different bus from the
	 * 9132's own four internal PHYs, and the 2.5G jack's link state has
	 * to be read there.
	 */
	struct mii_bus *outer_mdio;
	/*
	 * The last raw value of the outerphy's auto-negotiation status, so the
	 * driver can say when the lane's state changed and what it changed from.
	 * The word is read with mdiobus_c45_read(), i.e. a real Clause-45
	 * transaction - the same one the vendor's U-Boot makes with
	 * "mii 45read 5 7 0x01" - and it is the only register that says whether
	 * the base-X lane still delivers frames at all.
	 */
	u32 outerphy_an_status;
	/*
	 * The far end's SerDes class and the two words that say whether the
	 * lane carries code, so that the lane's own report is printed when it
	 * changes and not every time the lane is looked at.
	 */
	int outerphy_serdes_link;
	int outerphy_serdes_mode;
	/*
	 * Whether the outer PHY's own configuration has been printed yet.
	 *
	 * The print happens on the first read of the PHY, because the MDIO
	 * controller it sits on is a separate device and the two are not
	 * probed in a fixed order.
	 */
	bool outerphy_dumped;
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
	/*
	 * The lane's PCS, watched.
	 *
	 * The lane's configuration lives in the SerDes domain, and the NPPT's
	 * prepare - which runs when eth0 is opened, thirteen seconds into the
	 * boot - resets that domain: the switch driver has programmed the lane
	 * once, at probe, and nothing has programmed it since.  Measured on
	 * 2026-10-28: the PCS's registers read 0x0000 from the nineteenth
	 * second on, and a write into them does not stick.
	 *
	 * So the lane is watched, and put back when it is found empty.
	 */
	struct delayed_work lane_work;
	bool lane_lost;
	u32 lane_tries;
	/*
	 * When the watch was armed, and how many times it has run.
	 *
	 * The first thirty seconds after the probe are sampled twenty
	 * milliseconds apart.  The question this watch exists for is *when*
	 * the far end loses the lane, and the candidates are fifty
	 * milliseconds apart: eth0's link-up and the outer port's own
	 * phylink setup happen in that window, and a two-second period
	 * cannot tell them apart, while twenty milliseconds can.
	 */
	unsigned long lane_start;
	u32 lane_samples;
	/*
	 * The loader's own four words for this lane's PCS, saved when the
	 * loader is the one that configured it (judgment TT).
	 *
	 * The watch puts the lane back when the SerDes domain's reset has
	 * emptied the block, and what it puts back has to be the loader's
	 * configuration and not this driver's own: this driver's image is a
	 * 1G one, and the lane may be a 2.5G one.
	 */
	bool lane_pcs_inherited;
	u32 lane_pcs_words[4];
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
 * The lane's PCS, as the board's own U-Boot leaves it.
 *
 * Read out of the loader's prompt on 2026-10-28, in the same power cycle
 * whose net bring-up had already brought the 2.5G jack up and carried
 * 11,471,728 bytes over it:
 *
 *	0x1a7c0000	0x1140 0x0139 0x7996 0xced0 0x0020 0xd000 0x0002 0x0000
 *	0x1a7e0000	0x2200 0x0005 0x001a 0x0000 0x0000 0x0000 0x0000 0x0000
 *
 * The four registers that matter, with their VEND2 addresses (the window
 * is four bytes per register):
 *
 *	VEND2 0x0000	0x1140	the BMCR
 *	VEND2 0x0004	0x0020	the SR-MII speed word
 *	VEND2 0x8000	0x2200	the DIG_CTRL1: the MAC auto-switch bit and bit 13
 *	VEND2 0x8001	0x0005	the AN_CTRL: the PCS mode field (bits 2:1) is 2,
 *				i.e. SGMII, and the AN interrupt enable is on
 *
 * Two of these are what the 1000BASE-X profile this driver used to program
 * got wrong, and both of them cost the lane:
 *
 *   - the PCS mode: 1000BASE-X puts 0 in the mode field, U-Boot has 2
 *     (SGMII).  The far end of this lane is the RTL8226 on the 2.5G jack
 *     (phy-zx279133-uni-serdes.c says so), and it answers Clause 45 with a
 *     1000BASE-X PCS BMCR of 0x3000 and a PCS status of 0x0069 - link up,
 *     block lock and block sync all set - while U-Boot has the lane up.
 *     In the kernel the same register reads 0x002d: the link is down.
 *   - the BMCR: the profile's modify(VEND2 BMCR, 0x2061, BIT(6)) takes
 *     U-Boot's 0x1140 to 0x0140, i.e. it clears bit 12; and the SR-MII speed
 *     word, which U-Boot has at 0x0020, is not written at all - the profile
 *     puts the speed bits into the BMCR instead.
 *
 * So the four registers are written whole, and the read-back is printed.
 */
/*
 * The lane's configuration, written from scratch.
 *
 * The four words below are the ones the board's own U-Boot leaves in the
 * lane's PCS, and they are the configuration this lane ran in while the
 * loader moved 11,471,728 bytes across it:
 *
 *	BMCR		0x1140	the SR-MII control word
 *	SR-MII speed	0x0020	the speed word
 *	DIG_CTRL1	0x2200	the MAC auto-switch bit and bit 13
 *	AN_CTRL		0x0005	the AN interrupt enable and the PCS mode field
 *
 * They are written whole, and the read-back is printed, because the whole
 * point is what the block holds.
 */
static int zx279133_switch_xpcs_write_words(struct zx279133_switch *priv,
					     u32 bmcr, u32 speed,
					     u32 dig_ctrl1, u32 an_ctrl)
{
	int ret;

	ret = zx279133_switch_xpcs_write(priv, MDIO_MMD_VEND2, MII_BMCR,
					  bmcr);
	if (ret)
		return ret;

	ret = zx279133_switch_xpcs_write(priv, MDIO_MMD_VEND2,
					  ZX279133_XPCS_VEND2_SR_MII_SPEED,
					  speed);
	if (ret)
		return ret;

	ret = zx279133_switch_xpcs_write(priv, MDIO_MMD_VEND2,
					  ZX279133_XPCS_VR_MII_DIG_CTRL1,
					  dig_ctrl1);
	if (ret)
		return ret;

	return zx279133_switch_xpcs_write(priv, MDIO_MMD_VEND2,
					  ZX279133_XPCS_VR_MII_AN_CTRL,
					  an_ctrl);
}

static int zx279133_switch_xpcs_program_lane(struct zx279133_switch *priv)
{
	int val;
	int ret;

	ret = zx279133_switch_xpcs_write_words(priv,
					ZX279133_XPCS_UBOOT_BMCR,
					ZX279133_XPCS_UBOOT_SR_MII_SPEED,
					ZX279133_XPCS_UBOOT_DIG_CTRL1,
					ZX279133_XPCS_UBOOT_AN_CTRL);
	if (ret)
		return ret;

	/* Read them back: the whole point is what the block holds. */
	val = zx279133_switch_xpcs_read(priv, MDIO_MMD_VEND2,
					ZX279133_XPCS_VR_MII_AN_CTRL);
	dev_dbg(priv->dev,
		"XPCS0 AN_CTRL = %d, DIG_CTRL1 = %d, BMCR = %d, SR-MII = %d\n",
		val,
		zx279133_switch_xpcs_read(priv, MDIO_MMD_VEND2,
					   ZX279133_XPCS_VR_MII_DIG_CTRL1),
		zx279133_switch_xpcs_read(priv, MDIO_MMD_VEND2,
					   MII_BMCR),
		zx279133_switch_xpcs_read(priv, MDIO_MMD_VEND2,
					   ZX279133_XPCS_VEND2_SR_MII_SPEED));

	return 0;
}

/*
 * The lane, watched.
 *
 * The switch driver programs the lane once, at probe.  The NPPT's prepare
 * then runs - it is what opens eth0, and it is thirteen seconds into the
 * boot - and it resets the SerDes domain the lane's PCS lives in: measured
 * on 2026-10-28, the PCS's AN_CTRL reads 0x0005 two and three quarters
 * seconds into the boot and 0x0000 from the nineteenth second on, and a
 * write into the block does not stick.
 *
 * So the lane is read every two seconds, and put back when it is found
 * empty.  What this buys, whichever way the next round goes:
 *
 *   - if the block takes the writes again, the lane comes up by itself and
 *     the 2.5G jack gets its frames;
 *   - if it does not, the log says so, once per loss, and says whether the
 *     re-apply took.
 *
 * What it must not do is police the lane's configuration.  It used to
 * compare VR_MII AN_CTRL with the loader's value and rewrite the loader's
 * four words whenever the two differed, which meant that any other
 * configuration of this lane was overwritten two seconds after it was
 * written - the vendor's own base-X recipe included.  Measured on
 * 2026-10-29 (win125): the log said
 *
 *	the lane's PCS lost its configuration (AN_CTRL = 0x0004);
 *	the loader's values are written again
 *	the lane's PCS took the loader's values again (AN_CTRL = 0x0005)
 *
 * right after a runtime write of 0x0004 into that word, and every sample
 * of the far end taken afterwards was worthless, because the word it was
 * testing had been put back before the sample was taken.
 *
 * So the watch looks for one thing only: an empty block.  That is what the
 * SerDes domain's reset leaves behind, and it is the one case where
 * writing the lane's configuration again is the right thing to do.
 */
static void zx279133_switch_lane_watch(struct work_struct *work);
static void zx279133_switch_lane_work_cleanup(void *data);

/*
 * The far end's own view of this lane: forward declaration.
 *
 * The function itself is further down, with the rest of the outer PHY's code.
 * It reads the RTL8226's AN status word (devad 7 reg 1), prints it whenever it
 * changes, and prints the PHY's own configuration once per boot.
 */
static int zx279133_switch_outerphy_link(struct zx279133_switch *priv);

/*
 * The lane's whole state, in one line: forward declaration.
 *
 * The function itself is further down, with the rest of the outer PHY's code.
 */
static void zx279133_switch_lane_state(struct zx279133_switch *priv);

static void zx279133_switch_lane_watch(struct work_struct *work)
{
	struct zx279133_switch *priv =
		container_of(to_delayed_work(work), struct zx279133_switch,
			     lane_work);
	int an_ctrl;
	int bmcr;
	int ret;
	unsigned long period = 2000;

	priv->lane_samples++;

	/*
	 * The first thirty seconds are sampled twenty milliseconds apart.
	 *
	 * The far end's word changes once, and the question is when.  The
	 * events around that moment are fifty milliseconds apart - eth0's
	 * link-up and the outer port's own phylink setup - so a two-second
	 * period cannot tell them apart, and a twenty-millisecond one can.
	 *
	 * The fast window is over after thirty seconds, and the watch goes
	 * back to two seconds: the change, if it is going to happen, has
	 * happened by then.
	 */
	if (priv->lane_start &&
	    jiffies - priv->lane_start < msecs_to_jiffies(30000))
		period = 20;

	/*
	 * The far end's own word, every two seconds.
	 *
	 * Why this is in the watch and not only in the port's link state:
	 * the word is 0x00e9 when the loader's configuration is in place and
	 * 0x00ad under mainline, and the difference is one bit - the far end
	 * is no longer receiving valid code (docs/92 §78.3, §94.3).  When this
	 * driver inherits the lane's own words, the near side is the loader's,
	 * word for word, and the far end still loses that bit - so nothing this
	 * driver writes explains it, and the question is *when* it happens.
	 *
	 * The port's link state only asks once (the link is a fixed one, so
	 * phylink never polls it again), which is why the boot log only ever
	 * showed one sample, taken after the damage was done.  Reading it here
	 * puts every change in the log with its timestamp, and the word is
	 * printed only when it changes, so a boot where it never changes says
	 * nothing at all.
	 */
	zx279133_switch_outerphy_link(priv);

	/*
	 * The far end's SerDes class is the one thing on this lane that the
	 * board itself can take away, and none of the words read above tells
	 * that apart from a lane that works: the far end's auto-negotiation
	 * word reads the same either way (0x002d), and its mode word only
	 * changes when the chip re-negotiates.  So the class is checked here
	 * and put back when it is wrong.
	 *
	 * Every half second while the fast window lasts, and every two
	 * seconds after it: the check is a handful of MDIO reads, and the fast
	 * window is there to time the far end's own word, not this.
	 */
	if (period > 20 || !(priv->lane_samples % 25)) {
		if (!zx279133_switch_outerphy_serdes_check(priv))
			zx279133_switch_outerphy_serdes_set(priv, true);
	}

	an_ctrl = zx279133_switch_xpcs_read(priv, MDIO_MMD_VEND2,
					     ZX279133_XPCS_VR_MII_AN_CTRL);
	bmcr = zx279133_switch_xpcs_read(priv, MDIO_MMD_VEND2, MII_BMCR);

	/*
	 * An empty block is the SerDes domain's reset: both words read
	 * zero.  Anything else is a configuration, and the watch's business
	 * ends there.
	 */
	if (an_ctrl != 0 || bmcr != 0) {
		if (priv->lane_lost) {
			dev_info(priv->dev,
				 "the lane's PCS is configured again (AN_CTRL = 0x%04x, BMCR = 0x%04x)\n",
				 an_ctrl < 0 ? 0xffff : (an_ctrl & 0xffff),
				 bmcr < 0 ? 0xffff : (bmcr & 0xffff));
			priv->lane_lost = false;
		}
		goto rearm;
	}

	if (!priv->lane_lost) {
		if (an_ctrl < 0)
			dev_warn(priv->dev,
				 "the lane's PCS cannot be read (%d); the lane is programmed again\n",
				 an_ctrl);
		else
			dev_warn(priv->dev,
				 "the lane's PCS was reset (AN_CTRL = 0x%04x, BMCR = 0x%04x); the lane is programmed again\n",
				 an_ctrl & 0xffff, bmcr & 0xffff);
		priv->lane_lost = true;
		priv->lane_tries = 0;
	}

	if (priv->lane_pcs_inherited) {
		/*
		 * The loader configured this lane, so the loader's words
		 * are the ones that belong in this block - not this
		 * driver's own image, which is a 1G one and this lane may
		 * be a 2.5G one.
		 */
		ret = zx279133_switch_xpcs_write_words(priv,
				priv->lane_pcs_words[0], priv->lane_pcs_words[1],
				priv->lane_pcs_words[2], priv->lane_pcs_words[3]);
	} else {
		ret = zx279133_switch_xpcs_program_lane(priv);
	}
	priv->lane_tries++;

	/*
	 * Did the writes take?
	 *
	 * This is the question the next round is about, so it is answered in
	 * the log and not left to be inferred: the block may be gone (the
	 * SerDes domain was reset under it) or it may be there and simply
	 * refuse the writes.
	 */
	an_ctrl = zx279133_switch_xpcs_read(priv, MDIO_MMD_VEND2,
					     ZX279133_XPCS_VR_MII_AN_CTRL);
	if (an_ctrl == (int)(priv->lane_pcs_inherited ?
			     priv->lane_pcs_words[3] :
			     ZX279133_XPCS_UBOOT_AN_CTRL)) {
		dev_info(priv->dev,
			 "the lane's PCS took the values again (AN_CTRL = 0x%04x)\n",
			 an_ctrl & 0xffff);
	} else if (ret || !((priv->lane_tries - 1) % 5)) {
		dev_warn(priv->dev,
			 "the lane's PCS does not take the values (AN_CTRL = 0x%04x, %d)\n",
			 an_ctrl < 0 ? 0xffff : (an_ctrl & 0xffff), ret);
	}

rearm:
	/*
	 * And a line of the lane's state every half second while the fast
	 * window lasts, so the timeline is dense enough to show what the
	 * lane was doing before the far end's word changed.
	 */
	if (period == 20 && !(priv->lane_samples % 25))
		zx279133_switch_lane_state(priv);

	schedule_delayed_work(&priv->lane_work, msecs_to_jiffies(period));
}

/*
 * The lane's PCS, programmed as SGMII.
 *
 * The steps below are the tree's own 1G profile (from
 * zx279133_xpcs_sgmii_quirk() in drivers/net/ethernet/zte/zx279133-mac.c),
 * which programs this block as SGMII, i.e. PCS mode 2.  What this driver used
 * to do instead was the 1000BASE-X variant of it, with the mode field and the
 * BMCR changed.
 *
 * What U-Boot leaves in this block is SGMII - AN_CTRL = 0x0005, whose mode
 * field (bits 2:1) is 2 - so the four registers are written with the values
 * U-Boot leaves, whole; see zx279133_switch_xpcs_program_lane().
 *
 * WHY THIS IS NOT THE 2.5G JACK'S PROBLEM (win102, 2026-10-28 12:01, one
 * boot, both sides dumped word by word):
 *
 * This driver used to say that the base-X profile "cost the 2.5G jack its
 * frames", and that the lane's far end is the RTL8226 on that jack.  The
 * second half of that is the wrong way round: the lane is the 9132's CPU
 * port, and the 1G jacks work through it (docs/92 §82).  The first half is
 * not supported by the registers either: in the one boot of win102, the
 * loader's working state and the kernel's state are the same word for word
 * in every block that belongs to the 9132 - PPU 5's own attribute block
 * (nppt+0x8500) has ZERO differences, and XMAC0 (nppt+0x140000), which the
 * vendor's own port list calls the outerphy port's MAC, differs in twelve
 * single bits.  A lane that is programmed identically in the configuration
 * that moves bytes and in the configuration that does not cannot be the
 * difference between them.
 *
 * What is left is the SoC side of this lane: the loader leaves BMCR 0x1140,
 * DIG_CTRL1 0x2200 and AN_CTRL 0x0005 in the PCS, and this driver overwrites
 * them with its own base-X image (0x0140 / 0x2000 / 0x0000).  Those four
 * words are what win103 tests on the board.
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
	 * The BMCR and the SR-MII speed word are written whole, with U-Boot's
	 * values, after this sequence - so the two modifies that used to be
	 * here (the SR-MII speed into the BMCR, and the duplex bit into the
	 * BMCR) are gone: the first of them put the speed bits in the wrong
	 * register, and the second took U-Boot's BMCR from 0x1140 to 0x0140.
	 */
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

	/*
	 * And now U-Boot's four registers, whole.
	 */
	ret = zx279133_switch_xpcs_program_lane(priv);
	if (ret)
		return ret;

	return 0;
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
 * Two registers of each block were left alone until now.  The vendor writes
 * +0x0008 (the packet filter) twice and +0x000e0 (the speed word) once,
 * after the reset pulse, and this driver used to skip both of them because
 * the hardware seemed to "destroy" them.  It does not: the two bits that
 * disappear are write-one-to-clear, and the vendor's own board ends up with
 * the same values.  The writes are made here now, with the vendor's values.
 *
 * The reset the vendor pulses in between - a bit of the shared soft-reset
 * word at NPPT + 0x2c0004 - is done here too, in the vendor's order:
 * an_ctrl goes back to 0x00ba2203, the MAC's bit in that word is cleared for
 * a moment and set again, and only then does an_ctrl become 0x00fa2203.
 * The pulse is what clears the MAC's own receive state; without it the block
 * keeps whatever the power-on reset left in it, and a frame that arrives at
 * such a MAC is handed on without a length - which is what the switch's
 * "pktlen0 drop" counter counts.
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

		/*
		 * The vendor's nppt_smac_init() puts an_ctrl back to
		 * 0x00ba2203, pulses this MAC's bit in the shared
		 * soft-reset word at NPPT + 0x2c0004, and only then writes
		 * the 0x00fa2203 that says "running".
		 *
		 * The pulse is the part that matters.  It clears the MAC's
		 * own receive state - the buffer pointers, the descriptor
		 * watermarks, the length bookkeeping - and a MAC that was
		 * never reset keeps whatever the power-on reset left in it.
		 * A frame that arrives at such a MAC is handed on without
		 * a length, and the switch drops it: that is what the RED's
		 * "pktlen0 drop" counter counts, and it is why the
		 * ingress counter used to move while nothing ever came
		 * out.
		 *
		 * The vendor's smac_reset() reads the word, writes it with
		 * this MAC's bit cleared, and writes it back with the bit
		 * set; it touches no other bit, so the XMAC and the PCS
		 * are not reset from here.
		 */
		zx279133_switch_nppt_write(priv, base + ZX279133_SMAC_AN_CTRL,
					    ZX279133_SMAC_AN_CTRL_RESET_VALUE);

		value = zx279133_switch_nppt_read(priv,
						  ZX279133_SMAC_RESET_WORD);
		zx279133_switch_nppt_write(priv, ZX279133_SMAC_RESET_WORD,
					    value & ~(1u << mac));
		/*
		 * The vendor's smac_reset() waits __const_udelay(1718000)
		 * between the two writes - about 1.7 ms - and the MAC's own
		 * reset takes that long.  A tenth of a millisecond is not
		 * enough: the MAC is programmed again while its reset is
		 * still running, and its "clear-over-ready" word never comes
		 * up.  On the live board the same pulse, done by hand with
		 * seconds in between, did bring the word up.
		 */
		usleep_range(1718, 1818);
		zx279133_switch_nppt_write(priv, ZX279133_SMAC_RESET_WORD,
					    value | (1u << mac));

		zx279133_switch_nppt_write(priv, base + ZX279133_SMAC_AN_CTRL,
					    ZX279133_SMAC_AN_CTRL_VALUE);
		zx279133_switch_nppt_write(priv, base + ZX279133_SMAC_AN_CTRL2,
					    ZX279133_SMAC_AN_CTRL2_VALUE);
		/*
		 * The packet filter (+0x08) and the speed word (+0xe0), with the
		 * vendor's values, in the vendor's order: the filter first, then
		 * the port control word, then the speed word, all of them after
		 * the reset pulse.
		 *
		 * np_133.ko writes the filter twice.  The first write goes into
		 * a MAC that has only just come out of its reset, so it does not
		 * stick; the second one does.
		 *
		 * The words look as if they were being "destroyed" before: the
		 * filter read back 0x80000000 instead of 0x80000001 and the
		 * speed word read back 0x00010000 instead of 0x00011000.  Both
		 * are write-one-to-clear bits, and the vendor's own board ends
		 * up with the same values, because the vendor's dump of the
		 * block was taken before its Linux driver wrote it.  So the
		 * writes are made here too, and the read-back is printed.
		 */
		zx279133_switch_nppt_write(priv, base + ZX279133_SMAC_PKT_FILTER,
					    ZX279133_SMAC_PKT_FILTER_VALUE);
		zx279133_switch_nppt_write(priv, base + ZX279133_SMAC_PKT_FILTER,
					    ZX279133_SMAC_PKT_FILTER_VALUE);

		value = zx279133_switch_nppt_read(priv,
						  base + ZX279133_SMAC_PORT_CTRL);
		zx279133_switch_nppt_write(priv, base + ZX279133_SMAC_PORT_CTRL,
					    value | ZX279133_SMAC_PORT_CTRL_BIT);

		zx279133_switch_nppt_write(priv, base + ZX279133_SMAC_SPEED,
					    ZX279133_SMAC_SPEED_VALUE);

		/*
		 * The word at NPPT + 0x343f0 is a bitmap of the MACs that run
		 * in half duplex: bit(mac) is the four SMACs', bit(mac+4) the
		 * XMACs'.  The vendor's nppt_smac_init() clears this MAC's bit
		 * once and leaves it cleared, so the MAC runs full duplex.
		 * This driver used to set the bit and set it back, which left
		 * the MAC exactly where the vendor leaves it - in half duplex.
		 */
		value = zx279133_switch_nppt_read(priv,
						  ZX279133_SMAC_SOPC_PULSE);
		zx279133_switch_nppt_write(priv, ZX279133_SMAC_SOPC_PULSE,
					    value & ~(1u << mac));

		/*
		 * Publish it.  The vendor waits for the ready word - ten tries,
		 * 4.29 ms apart - and gives up after ten tries and publishes
		 * anyway; so does this.
		 *
		 * When the ready word does come up, the vendor waits another
		 * 4.29 ms before it writes the send-enable, and writes the
		 * send-enable twice.  Both are done here.
		 */
		for (tries = 0; tries < 10; tries++) {
			value = zx279133_switch_nppt_read(priv,
				ZX279133_SMAC_SOPC_READY_BASE + 4 * mac);
			if (value & BIT(0))
				break;
			usleep_range(4200, 4400);
		}
		if (!(value & BIT(0)))
			dev_warn(dev,
				 "SMAC %d never became ready (0x%08x) after the reset pulse at nppt+0x%06x\n",
				 mac, value, ZX279133_SMAC_RESET_WORD);
		else
			usleep_range(4200, 4400);
		zx279133_switch_nppt_write(priv,
					   ZX279133_SMAC_SOPC_SEND_BASE + 4 * mac,
					   1);
		zx279133_switch_nppt_write(priv,
					   ZX279133_SMAC_SOPC_SEND_BASE + 4 * mac,
					   1);

		/*
		 * The vendor's last write to this MAC is an_ctrl |= 2, after
		 * the send-enable.  The MAC has to be told again that it is
		 * running, and this is where the vendor says so.
		 */
		value = zx279133_switch_nppt_read(priv,
						  base + ZX279133_SMAC_AN_CTRL);
		zx279133_switch_nppt_write(priv, base + ZX279133_SMAC_AN_CTRL,
					    value | 2);

		dev_info(dev,
			 "SMAC %d programmed: an_ctrl=0x%08x pkt_filter=0x%08x speed=0x%08x ready=0x%08x send=0x%08x\n",
			 mac,
			 zx279133_switch_nppt_read(priv,
						   base + ZX279133_SMAC_AN_CTRL),
			 zx279133_switch_nppt_read(priv,
						   base + ZX279133_SMAC_PKT_FILTER),
			 zx279133_switch_nppt_read(priv,
						   base + ZX279133_SMAC_SPEED),
			 value,
			 zx279133_switch_nppt_read(priv,
						   ZX279133_SMAC_SOPC_SEND_BASE +
						   4 * mac));
	}

	/*
	 * The SOPC register window, in one line.
	 *
	 * Two sources disagree about where the "clear-over-ready" word of an
	 * internal-PHY MAC lives: np_133.ko's SMAC path reads it at
	 * nppt+0x34294+4*mac, while the vendor's U-Boot prints
	 * nppt+0x34000+((mac+0xa9)<<2), which is nppt+0x342a4+4*mac.  The
	 * window is printed here whole, so the board answers the question
	 * instead of another argument about it.
	 */
	{
		u32 w[12];
		u32 i;

		for (i = 0; i < ARRAY_SIZE(w); i++)
			w[i] = zx279133_switch_nppt_read(priv,
					ZX279133_SMAC_SOPC_READY_BASE + 4 * i);
		dev_info(dev,
			 "SOPC window nppt+0x%06x: 0x%08x 0x%08x 0x%08x 0x%08x 0x%08x 0x%08x 0x%08x 0x%08x 0x%08x 0x%08x 0x%08x 0x%08x\n",
			 ZX279133_SMAC_SOPC_READY_BASE,
			 w[0], w[1], w[2], w[3], w[4], w[5],
			 w[6], w[7], w[8], w[9], w[10], w[11]);
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
				    ZX279133_XMAC_UBOOT_FRAME_CFG);
	zx279133_switch_nppt_write(priv, xmac + ZX279133_XMAC_MODE_CFG, 2);

	/*
	 * MISC_CFG is deliberately not written at all.  The vendor's
	 * xamc_init_conf_by_speed() ORs the "IPC" bit 0x200 into it; the board's
	 * own U-Boot does not (it leaves 0x00002000), and U-Boot is the
	 * configuration the far end of this lane agrees with (docs/92 §96.16).
	 */
}

/*
 * The rate XMAC0 has to run at.
 *
 * XMAC0 is the 9132's outerphy port - chip port 5, the one the vendor's own
 * port table calls "5 = XMAC0" (docs/92 §83.4) - and it faces the RTL8226 on
 * the panel's 2.5G jack over base-X.  The two ends of that lane have to agree
 * on the rate, and the loader is the one that knows it: it negotiates with
 * the peer on the copper side and then programs this MAC to the rate the peer
 * gave it.
 *
 * Measured on this board, two power cycles, the loader's own console and its
 * TX_CTRL in the same boot (staging/ctl/win119.log, docs/92 §96):
 *
 *   peer at 1G    the loader prints "mode_eth_1gbase_x_cfg" and
 *		  "mac 4 phy status changed: 1000M full-duplex", and leaves
 *		  TX_CTRL (nppt+0x140000) = 0x60010000
 *		  -> speed field 31:29 = 3 = 1G
 *   peer at 2.5G  the loader prints "mode_eth_2p5gbase_x_cfg" and
 *		  "mac 4 phy status changed: 2.5G full-duplex", and leaves
 *		  TX_CTRL = 0xc0010000
 *		  -> speed field 31:29 = 6 = 2.5G
 *
 * and the PHY, which mainline never writes, publishes the mode it ended up
 * in: devad 0x1e reg 0x7580 = 0x9416, whose mode field (bits 5:0) is 0x16 =
 * 2500BASE-X.
 *
 * This driver used to write 1G here whatever the peer had negotiated, so the
 * 9132 ran a 1G MAC against a 2.5G SerDes.  The PHY's auto-negotiation then
 * sat in "restart in progress" forever - devad 7 reg 1 = 0x00ad, with the
 * 0x40 "block lock" bit clear, against the loader's 0x00e9 - and the lane
 * carried no frame at all (docs/92 §94.6's N3, §95).
 *
 * So the rate is taken from the loader's own TX_CTRL: it negotiated it, and
 * it is the only thing here that knows the peer's rate.  The driver's own
 * value is used only when the loader left nothing usable there.
 */
static u32 zx279133_switch_xmac0_speed(struct zx279133_switch *priv)
{
	u32 speed = (priv->saved_xmac_tx & ZX279133_XMAC_SPEED_MASK) >> 29;

	switch (speed) {
	case ZX279133_XMAC_SPEED_1G:
	case ZX279133_XMAC_SPEED_2P5G:
		break;
	default:
		/*
		 * Nothing usable in the loader's word: 0 is the USXGMII value,
		 * which belongs to the other lane, and the rest are rates this
		 * port cannot run at.  1G is what this port ran at before, and
		 * it is the rate the loader itself used when the peer offered
		 * 1G.
		 */
		speed = ZX279133_XMAC_SPEED_1G;
		break;
	}

	dev_info(priv->dev,
		 "the 2.5G jack's MAC (XMAC0) runs at %s; the loader's TX_CTRL speed field was %u\n",
		 speed == ZX279133_XMAC_SPEED_2P5G ? "2.5G" : "1G",
		 (priv->saved_xmac_tx & ZX279133_XMAC_SPEED_MASK) >> 29);

	return speed;
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
		/*
		 * The same four registers on the other MMD.
		 *
		 * Which MMD carries this lane's four registers is not a
		 * question this tree has answered, and the two readings
		 * disagree: on 2026-10-29 (docs/92 §96.13) the raw words
		 * at 0x1a7c0000 / 0x1a7c0010 / 0x1a7e0000 / 0x1a7e0004
		 * were 0x1140 / 0x0020 / 0x2200 / 0x0005 - the loader's
		 * own values - while a Clause-45 read of MMD 0x1f gave
		 * exactly those four numbers for the same four register
		 * numbers, and MMD 0x1e gave 0x0000 for three of them.
		 *
		 * So both are printed, from now on, with the register
		 * numbers: the lane's own numbers are then settled by the
		 * log of whichever round is next, and not by an argument.
		 */
		{ MDIO_MMD_PCS, ZX279133_XPCS_VR_MII_AN_CTRL,
		  "pcs AN_CTRL" },
		{ MDIO_MMD_PCS, ZX279133_XPCS_VR_MII_DIG_CTRL1,
		  "pcs DIG_CTRL1" },
		{ MDIO_MMD_PCS, ZX279133_XPCS_VEND2_SR_MII_SPEED,
		  "pcs SR-MII speed" },
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

	/*
	 * The raw word and its bits, and no "up" or "down" next to it.
	 *
	 * This driver used to print link=up when bits 11..13 of this word were
	 * all set and link=down otherwise, and that label was wrong: on
	 * 2026-10-29 the lane was read 0x280b (bit 12 clear, so "down") while
	 * the lane was carrying frames - lan-cpu0 had 795 bytes and 9 packets
	 * received and 4460 bytes and 36 packets sent, and the bench PC's ping
	 * to 192.168.9.1 was answered in 1 ms (docs/92 §95.1).  The word is
	 * printed as it is, and the bits are printed with it, so that the next
	 * reader does not take a bit of it for a link indicator.
	 */
	dev_info(dev,
		 "lane %s: XPCS0 status=0x%04x (bits 11..13 = 0x%03x) XMAC link=0x%04x\n",
		 tag, status, (status >> 11) & 0x7, link);
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
					     zx279133_switch_xmac0_speed(priv));

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

static void zx279133_switch_outerphy_dump(struct zx279133_switch *priv);

/*
 * The link state of the 2.5G jack, read from its own PHY.
 *
 * The jack is the 9132's fifth user port - the one the vendor calls the
 * outerphy - and the PHY behind it is the external RTL8226, at address 5 on
 * the SoC's MDIO controller #1.  That PHY does not answer the registers the
 * other four are read through: its MMD 0 (the basic block) reads zero for
 * BMCR and BMSR - measured on the running board on 2026-10-28, where
 * 00:0x00 and 00:0x01 were both 0x0000 while 07:0x01 read 0x00e9 - so the
 * standard "the link bit is in BMSR" does not exist on this chip.
 *
 * MMD 7 is the auto-negotiation block and register 1 is its status.  Bits
 * 2..5 are the negotiation status: 00 is no link, 01 a link without
 * autonegotiation, 10 a link with it complete and 11 a link with a restart
 * in progress.  Bits 2 and 3 alone tell "no link" from "a link".
 *
 * Measured with the vendor's own loader on the same board and the same
 * cable: the loader's port scan says "mac 4 phy status changed: 2.5G
 * full-duplex", and the kernel side reads 0x00e9 - bits 2..5 = 10, the
 * autonegotiation complete with a link.  The two agree, and the link is up
 * in mainline too, so this port's problem has never been the cable.
 *
 * The bus is looked up when it is needed rather than at the probe, because
 * the MDIO controller is a separate device and the two are not probed in a
 * fixed order.
 *
 * The register is read with mdiobus_c45_read() and not through phylib: the
 * read is a plain Clause 45 transaction to a device address, and phylib is
 * not needed for one.  It is also the safer way round: this PHY is not one
 * of the switch's own, nothing is supposed to configure it (see
 * port_enable() below), and asking phylib for it would mean asking for a
 * phy_device that this board has no reason to have - the device tree's
 * ethernet-phy node is a description, not a promise that phylib made a
 * device out of it.
 */
/*
 * The MDIO bus the outer PHY hangs off.
 *
 * The bus is looked up when it is needed rather than at the probe, because
 * the MDIO controller is a separate device and the two are not probed in a
 * fixed order.
 */
static struct mii_bus *zx279133_switch_outer_mdio(struct zx279133_switch *priv)
{
	struct device_node *np;
	struct mii_bus *bus;

	if (priv->outer_mdio)
		return priv->outer_mdio;

	np = of_parse_phandle(priv->dev->parent->of_node,
			       "zx279133,outer-phy-bus", 0);
	if (!np)
		return NULL;
	bus = of_mdio_find_bus(np);
	of_node_put(np);
	if (!bus)
		return NULL;

	priv->outer_mdio = bus;
	return bus;
}

/*
 * The whole lane, in one line: both ends, the MAC, and the NPPT's words.
 *
 * What this is for: the far end's auto-negotiation status is the only word
 * that says whether this lane carries code, and it changed from 0x00e9 (the
 * loader's value, "negotiation complete with a link") to 0x00ad ("a
 * negotiation restart in progress") some time between the loader's handover
 * and the kernel's first traffic.  Knowing *when* is what the next step
 * needs; knowing *what* changed on the near side at that moment is what
 * says why.
 *
 * So this line carries, in one place:
 *
 *	- the far end's AN status and its two SerDes words (0x758d, 0x7580)
 *	- the near end's three PCS words (VEND2 BMCR, AN_CTRL, DIG_CTRL1)
 *	- the NPPT's four words around the lane's own two status words
 *	  (0x0084 = the XMAC link, 0x0090 = the PCS status, and 0x0088 and
 *	  0x008c, whose loader values are 0x00000c78 and 0x00000071 - two
 *	  words nothing has ever been able to explain, and the first ones
 *	  that would show a change)
 *	- XMAC0's six control words and its SOPC send enable
 *
 * The loader's values for every one of them are in docs/92 §96.21, measured
 * at the loader's own prompt on 2026-10-29 07:18 in one boot, after its
 * net init: XMAC0 tx = 0xc0010000, rx = 0x3e8000c6, NPPT 0x84 = 1,
 * 0x88 = 0x00000c78, 0x8c = 0x00000071, 0x90 = 0x0000380b, and the far end
 * at 0x00e9.
 */
static void zx279133_switch_lane_state(struct zx279133_switch *priv)
{
	struct mii_bus *bus = zx279133_switch_outer_mdio(priv);
	u32 xmac = ZX279133_XMAC0_BASE;
	int an = -1, f758d = -1, f7580 = -1;
	int bmcr, anctrl, dig;

	if (bus) {
		an = mdiobus_c45_read(bus, ZX279133_SWITCH_OUTERPHY_ADDR,
				       ZX279133_SWITCH_OUTERPHY_AN_DEVAD,
				       ZX279133_SWITCH_OUTERPHY_AN_STATUS);
		f758d = mdiobus_c45_read(bus, ZX279133_SWITCH_OUTERPHY_ADDR,
					  0x1e, 0x758d);
		f7580 = mdiobus_c45_read(bus, ZX279133_SWITCH_OUTERPHY_ADDR,
					  0x1e, 0x7580);
	}

	bmcr = zx279133_switch_xpcs_read(priv, MDIO_MMD_VEND2, MII_BMCR);
	anctrl = zx279133_switch_xpcs_read(priv, MDIO_MMD_VEND2,
					   ZX279133_XPCS_VR_MII_AN_CTRL);
	dig = zx279133_switch_xpcs_read(priv, MDIO_MMD_VEND2,
					ZX279133_XPCS_VR_MII_DIG_CTRL1);

	dev_info(priv->dev,
		 "lane: far an=0x%04x 758d=0x%04x 7580=0x%04x | near bmcr=0x%04x anctrl=0x%04x dig=0x%04x | nppt 84=0x%08x 88=0x%08x 8c=0x%08x 90=0x%08x | xmac0 tx=0x%08x rx=0x%08x frame=0x%08x mode=0x%08x duplex=0x%08x misc=0x%08x send=0x%08x\n",
		 an < 0 ? 0xffff : an,
		 f758d < 0 ? 0xffff : f758d,
		 f7580 < 0 ? 0xffff : f7580,
		 bmcr < 0 ? 0xffff : (bmcr & 0xffff),
		 anctrl < 0 ? 0xffff : (anctrl & 0xffff),
		 dig < 0 ? 0xffff : (dig & 0xffff),
		 zx279133_switch_nppt_read(priv, ZX279133_NPPT_XMAC_LINK),
		 zx279133_switch_nppt_read(priv, 0x0088),
		 zx279133_switch_nppt_read(priv, 0x008c),
		 zx279133_switch_nppt_read(priv, ZX279133_NPPT_XPCS0_STATUS),
		 zx279133_switch_nppt_read(priv, xmac + ZX279133_XMAC_TX_CTRL),
		 zx279133_switch_nppt_read(priv, xmac + ZX279133_XMAC_RX_CTRL),
		 zx279133_switch_nppt_read(priv, xmac + ZX279133_XMAC_FRAME_CFG),
		 zx279133_switch_nppt_read(priv, xmac + ZX279133_XMAC_MODE_CFG),
		 zx279133_switch_nppt_read(priv, xmac + ZX279133_XMAC_DUPLEX),
		 zx279133_switch_nppt_read(priv, xmac + ZX279133_XMAC_MISC_CFG),
		 zx279133_switch_nppt_read(priv,
						  ZX279133_XMAC0_SOPC_SEND_ENABLE));
}

/*
 * The outer PHY's SerDes option registers, read.
 */
static int zx279133_switch_outerphy_serdes_read(struct zx279133_switch *priv,
						u32 *vals)
{
	struct mii_bus *bus = zx279133_switch_outer_mdio(priv);
	unsigned int i;

	if (!bus)
		return -ENODEV;

	for (i = 0; i < ARRAY_SIZE(zx279133_switch_outerphy_serdes_1g); i++) {
		int val = mdiobus_c45_read(bus, ZX279133_SWITCH_OUTERPHY_ADDR,
					   ZX279133_SWITCH_OUTERPHY_SERDES_DEVAD,
					   zx279133_switch_outerphy_serdes_1g[i][0]);
		if (val < 0)
			return val;
		vals[i] = (u32)val;
	}

	return 0;
}

/*
 * Put the outer PHY into the 1G SerDes class.
 *
 * The chip is read first and written only when it is not already in that
 * class: a PHY that is already right is not disturbed.  The write is a real
 * clause 45 transaction on the same bus the reads use (mdiobus_c45_write()),
 * and not phylib's, because this chip answers devad 0x1e only through a true
 * clause 45 access (docs/92 §94.4) - the same reason the debugfs console
 * reaches it the way it does.
 *
 * After the write the lane needs a moment to lock again, so the word that
 * says whether it carries code is polled for half a second, and what the two
 * words say is printed whether it locked or not.
 */
static int zx279133_switch_outerphy_serdes_set(struct zx279133_switch *priv,
						bool force)
{
	struct mii_bus *bus = zx279133_switch_outer_mdio(priv);
	u32 before[3], after[3];
	int link = -1;
	int mode = -1;
	unsigned int i;
	int ret = 0;
	bool changed = false;

	if (!bus)
		return -ENODEV;

	for (i = 0; i < ARRAY_SIZE(zx279133_switch_outerphy_serdes_1g); i++) {
		int val = mdiobus_c45_read(bus, ZX279133_SWITCH_OUTERPHY_ADDR,
					   ZX279133_SWITCH_OUTERPHY_SERDES_DEVAD,
					   zx279133_switch_outerphy_serdes_1g[i][0]);
		before[i] = val < 0 ? 0xffffffff : (u32)val;
		if (before[i] != zx279133_switch_outerphy_serdes_1g[i][1])
			changed = true;
	}

	if (!force && !changed)
		return 0;

	for (i = 0; i < ARRAY_SIZE(zx279133_switch_outerphy_serdes_1g); i++) {
		int val = mdiobus_c45_write(bus, ZX279133_SWITCH_OUTERPHY_ADDR,
					    ZX279133_SWITCH_OUTERPHY_SERDES_DEVAD,
					    zx279133_switch_outerphy_serdes_1g[i][0],
					    zx279133_switch_outerphy_serdes_1g[i][1]);
		after[i] = val < 0 ? 0xffffffff : (u32)val;
		if (val < 0)
			ret = -EIO;
	}

	if (changed)
		dev_info(priv->dev,
			 "the 2.5G jack's PHY: the SerDes option is set to the 1G class (75f3 %04x->%04x, 697a %04x->%04x, 6a04 %04x->%04x)\n",
			 before[0], after[0], before[1], after[1], before[2], after[2]);

	/* The lane needs a moment to lock again. */
	for (i = 0; i < 25; i++) {
		int val = mdiobus_c45_read(bus, ZX279133_SWITCH_OUTERPHY_ADDR,
					   ZX279133_SWITCH_OUTERPHY_SERDES_DEVAD,
					   ZX279133_SWITCH_OUTERPHY_SERDES_LINK);
		if (val >= 0) {
			link = val;
			if (val & ZX279133_SWITCH_OUTERPHY_SERDES_LINK_BIT)
				break;
		}
		msleep(20);
	}

	mode = mdiobus_c45_read(bus, ZX279133_SWITCH_OUTERPHY_ADDR,
				ZX279133_SWITCH_OUTERPHY_SERDES_DEVAD,
				ZX279133_SWITCH_OUTERPHY_SERDES_MODE);

	if (link != priv->outerphy_serdes_link ||
	    mode != priv->outerphy_serdes_mode) {
		priv->outerphy_serdes_link = link;
		priv->outerphy_serdes_mode = mode;

		dev_info(priv->dev,
			 "the 2.5G jack's PHY: the lane's word is 0x%04x and its mode word is 0x%04x%s\n",
			 link < 0 ? 0xffff : link,
			 mode < 0 ? 0xffff : mode,
			 (link >= 0 && (link & ZX279133_SWITCH_OUTERPHY_SERDES_LINK_BIT)) ?
			 " - the lane carries code" : " - the lane carries no code");
	}

	return ret;
}

/*
 * Whether the far end's SerDes class has to be put back.
 *
 * Two things can be wrong, and they are different:
 *
 *	- the chip's configuration is not the 1G class.  That is the state a
 *	  board that boots from flash is in, because the loader's net bring-up
 *	  is what configures this PHY and it only runs when the loader is given
 *	  a network to talk to.
 *
 *	- the chip is in the 1G class and the lane still carries nothing.
 *	  That is what the board did to itself on 2026-10-30 11:25, 128 s into
 *	  a boot that had started with the loader's class: the mode word went
 *	  from 0x9402 to 0x9412 and the lane stopped.
 *
 * A lane with no cable in it is neither of those: the far end sees no
 * partner then, and there is nothing to put back.
 */
static bool zx279133_switch_outerphy_serdes_check(struct zx279133_switch *priv)
{
	struct mii_bus *bus = zx279133_switch_outer_mdio(priv);
	u32 vals[3];
	int mode, link, an;
	unsigned int i;

	if (!bus)
		return true;

	if (zx279133_switch_outerphy_serdes_read(priv, vals))
		return true;

	/* The chip's own configuration first: that one is wrong even with no
	 * cable in the jack.
	 */
	for (i = 0; i < ARRAY_SIZE(zx279133_switch_outerphy_serdes_1g); i++)
		if (vals[i] != zx279133_switch_outerphy_serdes_1g[i][1])
			return false;

	/* Is there a partner at the far end?  Without one there is nothing
	 * to put back and nothing to complain about.
	 */
	an = mdiobus_c45_read(bus, ZX279133_SWITCH_OUTERPHY_ADDR,
			       ZX279133_SWITCH_OUTERPHY_AN_DEVAD,
			       ZX279133_SWITCH_OUTERPHY_AN_STATUS);
	if (an < 0 || !(an & ZX279133_SWITCH_OUTERPHY_AN_PARTNER))
		return true;

	/* The class the chip ended up in. */
	mode = mdiobus_c45_read(bus, ZX279133_SWITCH_OUTERPHY_ADDR,
				 ZX279133_SWITCH_OUTERPHY_SERDES_DEVAD,
				 ZX279133_SWITCH_OUTERPHY_SERDES_MODE);
	if (mode >= 0 &&
	    (mode & ZX279133_SWITCH_OUTERPHY_SERDES_MODE_MASK) !=
	    ZX279133_SWITCH_OUTERPHY_SERDES_MODE_1G)
		return false;

	/* And the lane's own word. */
	link = mdiobus_c45_read(bus, ZX279133_SWITCH_OUTERPHY_ADDR,
				 ZX279133_SWITCH_OUTERPHY_SERDES_DEVAD,
				 ZX279133_SWITCH_OUTERPHY_SERDES_LINK);
	if (link < 0 || (link & ZX279133_SWITCH_OUTERPHY_SERDES_LINK_BIT))
		return true;

	return false;
}

static int zx279133_switch_outerphy_link(struct zx279133_switch *priv)
{
	struct mii_bus *bus;
	int val;

	bus = zx279133_switch_outer_mdio(priv);
	if (!bus)
		return -1;

	val = mdiobus_c45_read(bus,
			       ZX279133_SWITCH_OUTERPHY_ADDR,
			       ZX279133_SWITCH_OUTERPHY_AN_DEVAD,
			       ZX279133_SWITCH_OUTERPHY_AN_STATUS);
	if (val < 0)
		return -1;

	/*
	 * The raw word, and the four bits that say the lane carries code.
	 *
	 * Why this is worth a line in the log: the word reads 0x00e9 under the
	 * vendor's U-Boot and 0x00ad under mainline, and the difference is
	 * exactly one of the four bits (docs/92 §78.3, §94.3).  The driver only
	 * asks for `val & 0xc`, so the difference is invisible in the carrier:
	 * the port reports "up" either way, which is why this lane could look
	 * healthy while it carried nothing.  Printing the raw word whenever it
	 * changes puts that difference in every boot's log, and the change is
	 * printed because the value is otherwise stable.
	 *
	 * Which of the four bits is which has never been decoded, so the
	 * message does not name them: it prints the mask (0x01 0x08 0x20 0x40)
	 * and says how many of them are set.
	 */
	if (val != (int)priv->outerphy_an_status) {
		priv->outerphy_an_status = val;

		dev_info(priv->dev,
			 "the 2.5G jack's PHY: AN status 0x%04x, the lane's four bits (0x01 0x08 0x20 0x40) are 0x%04x%s\n",
			 val, val & 0x69,
			 (val & 0x69) == 0x69 ? "" :
			 " - at least one of them is missing, the lane carries no code");

		/*
		 * What the lane looks like at the moment this word changed.
		 *
		 * The word itself says that the far end is no longer
		 * negotiating; it does not say what changed on this side of
		 * the lane, and that is the question.  So the whole lane goes
		 * into the log next to it: the far end's SerDes words, the
		 * near end's PCS words, the NPPT's four status words and
		 * XMAC0's six control words.
		 */
		zx279133_switch_lane_state(priv);
	}

	zx279133_switch_outerphy_dump(priv);

	return !!(val & 0xc);
}

/*
 * The outer PHY's own configuration, printed once per boot.
 *
 * This is the chip on the far end of the 2.5G jack.  Two things about it were
 * said here that the measurements of 2026-10-29 06:30-07:00 took back:
 *
 *  - That "the tree's generic Realtek driver rewrites the SerDes option and
 *    the DSP patch window when phylib attaches to it" (docs/92 §94.3).  It
 *    does not: phylib has no device for this chip.  There is no phy_device at
 *    address 5 on any bus (the four internal PHYs and the two dwxpcs are all
 *    that /sys/bus/mdio_bus/devices has), the tree's realtek driver prints
 *    nothing in the boot log, and the SerDes option word (1e.0x697a) still
 *    holds the loader's own value (0x0000, written by the loader's net init).
 *  - That the DSP patch window reads 0x0038 under mainline against the
 *    loader's 0x0002.  That window is a *streaming* one: every read advances
 *    it, so the second read of a value is the next one in the stream.  Writing
 *    0x0002 into it and reading it twice gave 0x0002 and then 0x0100 (measured
 *    2026-10-29 06:57), which is what a stream does.  So the 0x0038 was an
 *    artifact of reading it twice, and nothing had written it.
 *
 * What the far end does say is in the AN status word (devad 7 reg 1): 0x00e9
 * under the loader and 0x00ad under mainline, and the difference is one bit -
 * the far end is no longer receiving valid code (§94.6's N3).
 *
 * The words printed here are the ones that decide which SerDes mode the chip
 * is in, and the vendor's own values for them, read out of the board's U-Boot
 * on 2026-10-29 00:47, plus what the same boot measured at the loader's
 * prompt after its net init on 2026-10-29 06:52:
 *
 *	1e.0x697a	0x0003	the SerDes option before the loader's net init,
 *				0x0000 after it (the loader's own value)
 *	1e.0x7580	-	the SerDes mode (the tree reads 0x02 as SGMII and
 *				0x16 as 2500BASE-X; the vendor's value was never
 *				measured)
 *	1e.0x758d	0x0010	the SerDes control word before the loader's net
 *				init, 0x801e after it; bit 15 of it is set by
 *				the chip itself (writing 0x0010 leaves it set,
 *				measured 2026-10-29 01:31)
 *	1f.0xa438	0x0002	the DSP patch window - a streaming one, see above
 *
 * The auto-negotiation block is printed with them, because the interesting
 * pair is there: the partner's advertisement (7.0x20) was 0x00a1 under the
 * loader and is 0x00a1 in mainline too - the peer on the jack is talking -
 * while the status word (7.0x01) says the negotiation keeps restarting.
 */
static void zx279133_switch_outerphy_dump(struct zx279133_switch *priv)
{
	static const struct {
		u32 devad;
		u32 reg;
		const char *name;
	} regs[] = {
		/*
		 * The AN block's BMCR (7.0x00) is NOT read here, and it must not
		 * be: reading it makes this chip restart its auto-negotiation,
		 * and the lane loses its block lock when it does (docs/92
		 * §96.22, judgment N99).
		 *
		 * Measured on the vendor's own U-Boot, at its prompt, in one
		 * boot, with nothing else touched:
		 *
		 *	mii 45read 5 0x07 0x01   -> 0x00e9   (locked)
		 *	mii 45read 5 0x07 0x00   -> 0x3000
		 *	mii 45read 5 0x07 0x01   -> 0x00ad   (restarting, unlocked)
		 *
		 * One read of one register, and the lane is dead - which is
		 * exactly what this driver used to do at every boot, in the
		 * middle of the dump below.  The word itself can still be
		 * written (the next round works out what to write), but it
		 * must not be read.
		 *
		 * 2026-10-29 (docs/92 §96.24): this is still a hard rule, but
		 * it is NOT what kills the lane.  A control round measured the
		 * lane dying on its own: 78 seconds after the loader's net
		 * init, with nothing sent to the board at all, the far end's
		 * status word went 0x00e9 -> 0x00ad.  So this table's other
		 * reads are harmless, and the lane's fate does not depend on
		 * them either way.
		 */
		{ 7, 0x01, "AN status" },
		/*
		 * The two halves of the negotiation's capability exchange
		 * (Clause 73 registers 2 and 3).  Measured on the board on
		 * 2026-10-29, in the lane's good state and in its dead one,
		 * they read 0x001c and 0xc849 in both - the far end's own
		 * advertisement, and the link partner ability it has
		 * collected.  They are worth having in the log because they
		 * are the only place where what the far end believes its
		 * partner offered is visible (docs/92 §96.24.2).
		 */
		{ 7, 0x02, "AN advertisement" },
		{ 7, 0x03, "AN partner ability" },
		{ 7, 0x10, "AN capabilities" },
		{ 7, 0x13, "AN capabilities 2" },
		{ 7, 0x20, "AN partner" },
		{ 0x1e, 0x697a, "SerDes option" },
		{ 0x1e, 0x7580, "SerDes mode" },
		{ 0x1e, 0x758d, "SerDes ctrl" },
		{ 0x1f, 0xa434, "PHY status" },
		{ 0x1f, 0xa438, "DSP patch" },
		{ 0x1f, 0xa616, "PMA speed" },
		/*
		 * The 10GBASE-T AN's own registers and the EEE half of the
		 * same exchange, measured on the board on 2026-10-29 at
		 * 12:49 in the lane's good state (right after the loader's
		 * net init) and in its dead one, twenty seconds later:
		 *
		 *	1f.0xa5d0  AN EEE advertisement      0x0006  0x0006
		 *	1f.0xa5d2  AN EEE partner ability    0x0006  0x0006
		 *	1f.0xa5d4  AN 10GBT control          0x00a1  0x00a1
		 *	1f.0xa5d6  AN 10GBT status           0x3428  0x3428
		 *
		 * They are identical in both states, so neither the EEE
		 * exchange nor the partner's 10GBASE-T advertisement is what
		 * breaks this lane (docs/92 §96.27).  They are in the log
		 * because the partner's status word is the only place where
		 * what the far end believes its copper partner said is
		 * visible: 0x3428 decodes as "the partner is 2.5GBASE-T
		 * capable, it is not 5G or 10G capable, it says Remote OK
		 * and Local OK, and there is no master/slave fault".
		 *
		 * The BMSR is the standard link status, and it latches: two
		 * reads in a row gave 0x0002 and then 0x0006 (the auto-
		 * negotiation ability bit appearing), while the lane was
		 * already dead.
		 */
		{ 0x01, 0x01, "BMSR" },
		{ 0x1f, 0xa5d0, "AN EEE advertisement" },
		{ 0x1f, 0xa5d2, "AN EEE partner ability" },
		{ 0x1f, 0xa5d4, "AN 10GBT control" },
		{ 0x1f, 0xa5d6, "AN 10GBT status" },
	};
	int i;

	if (priv->outerphy_dumped)
		return;
	priv->outerphy_dumped = true;

	for (i = 0; i < ARRAY_SIZE(regs); i++) {
		int val = mdiobus_c45_read(priv->outer_mdio,
					   ZX279133_SWITCH_OUTERPHY_ADDR,
					   regs[i].devad, regs[i].reg);

		dev_info(priv->dev, "the 2.5G jack's PHY: %s (devad %#02x reg %#04x) = 0x%04x\n",
			 regs[i].name, regs[i].devad, regs[i].reg,
			 val < 0 ? 0xffff : val);
	}
}

/*
 * phylink asks the driver for the link state of a port it has no PHY to
 * poll.  The 9132's ports do have PHYs - internal ones - so the answer is
 * read there.  DSA sets poll_fixed_state when the driver provides this, so
 * phylink asks again and again, and a cable that arrives after boot is
 * noticed without anything else having to be done.
 */
/*
 * The link state of a port the switch owns but has no PHY for.
 *
 * NOTE (docs/92 §96.31.6): for a switch's *own* user ports this callback
 * is not consulted.  phylink_get_fixed_state() takes the device tree's
 * fixed-link first and only then lets config->get_fixed_state override it,
 * and the DSA core installs that callback for user devices only
 * (net_dsa_user.c: dsa_user_phylink_setup()); dsa_port_phylink_create()
 * does not install it.  So for lan5 the fixed-link in the device tree is
 * what phylink uses - it always claims a carrier, and the rate it reports
 * is the one written there.  The values below are the ones that would be
 * right if the callback did run.
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

	if (port == ZX279133_SWITCH_DSA_OUTER_PORT) {
		/* The 2.5G jack: the external RTL8226's own link state. */
		link = zx279133_switch_outerphy_link(priv);
		state->link = link == 1;
		state->speed = SPEED_2500;
		state->duplex = DUPLEX_FULL;
		return;
	}

	if (port < ZX279133_SWITCH_DSA_PORT_MIN ||
	    port > ZX279133_SWITCH_DSA_JACK_MAX) {
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

	if (port == ZX279133_SWITCH_DSA_OUTER_PORT) {
		/*
		 * The 2.5G jack: the 9132's port runs base-X to the external
		 * RTL8226 (the vendor's mode_eth_2p5gbase_x_cfg), and the lane
		 * is measured running 2500BASE-X - the far end's SerDes mode
		 * word (devad 0x1e reg 0x7580) reads 0x9416, whose mode field
		 * (bits 5:0) is 0x16 = 2500BASE-X, and a host behind this jack
		 * is reached with no loss at all (docs/92 §96.31).
		 *
		 * Both rates are advertised because the device tree names the
		 * mode the port is in ("phy-mode = \"2500base-x\"") and phylink
		 * refuses a port whose mode is not in this set.
		 */
		__set_bit(PHY_INTERFACE_MODE_1000BASEX,
			  config->supported_interfaces);
		__set_bit(PHY_INTERFACE_MODE_2500BASEX,
			  config->supported_interfaces);
		config->mac_capabilities = MAC_1000FD | MAC_2500FD;
		return;
	}

	if (port < ZX279133_SWITCH_DSA_PORT_MIN ||
	    port > ZX279133_SWITCH_DSA_JACK_MAX)
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

	if (port == ZX279133_SWITCH_DSA_OUTER_PORT)
		/*
		 * The external RTL8226 is not a PHY this port owns: the port
		 * is a fixed-link one, and the RTL8226 carries the
		 * configuration the vendor's own sequence gives it - the
		 * loader's net bring-up writes it, and the NPPT driver has
		 * already put the port's own words in place.  Powering it down
		 * and letting phylib configure it from scratch would throw
		 * away the very state the link is working with: the
		 * autonegotiation status reads 0x00e9 in mainline, which is
		 * the same "link up, autonegotiation complete" the loader
		 * leaves it in.
		 */
		return 0;

	if (port < ZX279133_SWITCH_DSA_PORT_MIN ||
	    port > ZX279133_SWITCH_DSA_JACK_MAX)
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
	    addr > ZX279133_SWITCH_DSA_JACK_MAX)
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
	    addr > ZX279133_SWITCH_DSA_JACK_MAX)
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
	       phydev->mdio.addr <= ZX279133_SWITCH_DSA_JACK_MAX;
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
		 ZX279133_SWITCH_DSA_PORT_MIN, ZX279133_SWITCH_DSA_JACK_MAX,
		 ZX279133_SWITCH_DSA_PHY(ZX279133_SWITCH_DSA_PORT_MIN),
		 ZX279133_SWITCH_DSA_PHY(ZX279133_SWITCH_DSA_JACK_MAX));

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
		 "the 9132 is serving: the jacks are the 9132's UNI ports %u..%u (DSA ports %u..%u), the 2.5G jack is the outerphy port %u (DSA port %u), the CPU port is %u and the transport VIDs are %u..%u\n",
		 ZX279133_SWITCH_UNI_PORT_MIN, ZX279133_SWITCH_UNI_PORT_MAX,
		 ZX279133_SWITCH_DSA_PORT_MIN, ZX279133_SWITCH_DSA_JACK_MAX,
		 ZX279133_9132_OUTERPHY_PPU, ZX279133_SWITCH_DSA_OUTER_PORT,
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
	u32 link;
	int an_ctrl;
	int ret;

	if (!dev->parent || !dev->parent->of_node)
		return dev_err_probe(dev, -ENODEV,
				     "the switch must be a child of the NPPT controller\n");
	parent_np = dev->parent->of_node;

	priv = devm_kzalloc(dev, sizeof(*priv), GFP_KERNEL);
	if (!priv)
		return -ENOMEM;

	priv->dev = dev;
	dev_set_drvdata(dev, priv);
	INIT_DELAYED_WORK(&priv->lane_work, zx279133_switch_lane_watch);
	/*
	 * The watcher reads the lane's PCS, so it has to be stopped before
	 * the driver goes away.  A device action does that without depending
	 * on the platform driver's remove callback.
	 */
	if (devm_add_action_or_reset(dev, zx279133_switch_lane_work_cleanup,
				      priv))
		return dev_err_probe(dev, -ENOMEM,
				     "the lane watcher could not be armed\n");

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
	 * The far end of this lane is the external RTL8226, and its SerDes
	 * class is the one thing on this lane that nobody else sets.
	 *
	 * The vendor's own U-Boot configures it in its net bring-up, which
	 * only runs when the loader is given a network to talk to - so a board
	 * that boots from flash never gets it, and the far end sits in the
	 * class it came out of the box in while the SoC side (which mainline
	 * does put in the 1G class) waits for a 1000BASE-X partner that never
	 * comes.  The write below is the same one the loader makes, and it is
	 * made here, before the near end is programmed, in the order the
	 * loader makes it: the far end first.
	 *
	 * It is made only when the chip is not already in that class, so a
	 * board whose loader did configure it (a warm boot, or a boot after a
	 * TFTP) is left alone.
	 */
	zx279133_switch_outerphy_serdes_set(priv, false);

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
	 * exactly what breaks it: the 1G profile below takes the PCS
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
	 * So the XMAC is programmed in this branch as well.  What used to be
	 * left out here was the reset, on the grounds that "the soft reset
	 * word is shared with the PCS, and the XMAC is at its reset state
	 * already".  The word is shared, but the bit is not: XMAC0's reset
	 * is bit 10 of it and pulsing that bit touches the XMAC alone, which
	 * is why the non-inheriting branch below can pulse it without
	 * touching the PCS.
	 *
	 * The reset is what the board was missing.  A MAC that has never
	 * been reset since the bootloader handed it over has not attached
	 * its output queue, and the vendor's own code says so: the MAC is
	 * published only after its own "clear-over-ready" word says it is
	 * ready, and on the live board that word for XMAC0 (NPPT + 0x342a4)
	 * reads 0 while XMAC1's (0x342a8) reads 1.  A frame that the stack
	 * queued therefore sat in the IDM's descriptor ring with nobody to
	 * take it: the board's own counter said fifty-one packets sent,
	 * the XMAC's transmit counter said zero, and nothing crossed the
	 * lane in either direction.  The vendor's bootloader does reset
	 * XMAC0 before it configures it ("xmac 0 speed set 2 in sgmii"), and
	 * so does the vendor's own Linux, whose XMAC0 control words read
	 * zero because they were reset and never programmed again.
	 */
	zx279133_switch_lane_snapshot(priv, "pre-pcs");
	status = zx279133_switch_nppt_read(priv, ZX279133_NPPT_XPCS0_STATUS);
	link = zx279133_switch_nppt_read(priv, ZX279133_NPPT_XMAC_LINK);
	an_ctrl = zx279133_switch_xpcs_read(priv, MDIO_MMD_VEND2,
					    ZX279133_XPCS_VR_MII_AN_CTRL);
	/*
	 * This status word is not a link indicator, and it is not a mode
	 * indicator either - it is the signature of the work mode the PCS
	 * was left in, and both of the modes this lane ever runs in have
	 * their own value: 0x2c03 in the boots where the loader brought the
	 * lane up at 1G, 0x380b in the boots where it brought it up at
	 * 2.5G, and 0x280b in the boots where this driver had done its own
	 * work on the PCS (docs/92 §96.14, judgment VV).
	 *
	 * So it is not used to decide anything here.  The two words that do
	 * decide are read above.
	 */
	/*
	 * Did the loader bring this lane up?
	 *
	 * One word says so, and it is the only one that does: the XMAC
	 * link word (NPPT + 0x0084), which the loader's own net bring-up
	 * sets when it configures the outerphy port.  Counted over the
	 * 1163 boots in the serial log (docs/92 §96.14): in the 68 boots
	 * where the loader took that port it reads 1, and in the 373 boots
	 * where it did not it reads 0.  Measured again by hand on
	 * 2026-10-29 05:11 at the loader's own prompt, before and after
	 * its net init in one boot:
	 *
	 *	before:  0x19000084 = 0x00000000
	 *	after:   0x19000084 = 0x00000001
	 *
	 * and the PCS's own status word (NPPT + 0x0090) reads 0x380b in
	 * both - it says which work mode the lane is in, not whether it is
	 * up (docs/92 §96.14, judgment VV).
	 *
	 * The PCS's AN_CTRL is NOT the criterion.  The loader's 2.5G
	 * recipe leaves it at 0x0000 - that branch runs the lane at a
	 * fixed rate with the auto-negotiation off - while its 1G recipe
	 * leaves it at 0x0005 (docs/92 §96.15).
	 *
	 * When the loader left the lane up, this driver leaves the PCS
	 * alone.  That is the whole of judgment TT.  The loader's
	 * configuration is a working one - the far end of this lane says the
	 * link was up when the loader left it (1e.0x758d = 0x801e,
	 * 7.0x01 = 0x00e9, docs/92 §96.16) - and programming the PCS is what
	 * destroys it.  (The loader's own image download did not go over this
	 * lane: its log says "Using eth0 device", docs/92 §96.18.)
	 * This driver's own image
	 * is a 1G one, and the far end of this lane is in 2500BASE-X
	 * (1e.0x7580 = 0x9416, the mode field 0x16), so a 1G near end
	 * against a 2.5G far end negotiates nothing: the far end's status
	 * word was read at 0x00e9 (locked) nineteen milliseconds after this
	 * driver programmed the near end, and at 0x00ad (restarting) the
	 * next time it was read.
	 */
	if (link) {
		static const struct {
			int reg;
			const char *name;
		} words[] = {
			{ MII_BMCR, "BMCR" },
			{ ZX279133_XPCS_VEND2_SR_MII_SPEED, "SR-MII speed" },
			{ ZX279133_XPCS_VR_MII_DIG_CTRL1, "DIG_CTRL1" },
			{ ZX279133_XPCS_VR_MII_AN_CTRL, "AN_CTRL" },
		};
		unsigned int i;

		dev_info(dev,
			 "the loader brought this lane up (XMAC link = 0x%04x, the PCS's AN_CTRL = 0x%04x); the PCS is left as it is\n",
			 link, an_ctrl & 0xffff);

		/*
		 * The loader's four words for this block, saved word for
		 * word.  The watch puts the lane back if the SerDes domain's
		 * reset empties the block later on, and what it puts back
		 * has to be the loader's configuration: this driver's own
		 * image is a 1G one and this lane may be a 2.5G one.
		 */
		for (i = 0; i < ARRAY_SIZE(words); i++) {
			int val = zx279133_switch_xpcs_read(priv, MDIO_MMD_VEND2,
							    words[i].reg);

			priv->lane_pcs_words[i] = val < 0 ? 0 : (u32)val;
			dev_info(dev, "the loader's %s = 0x%04x\n",
				 words[i].name, priv->lane_pcs_words[i] & 0xffff);
		}
		priv->lane_pcs_inherited = true;

		zx279133_switch_xmac_configure(priv, false);
		goto report;
	}

	dev_info(dev,
		 "the NPPT PCS status word is 0x%04x; the lane is programmed here, not inherited\n",
		 status);

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

	/*
	 * No reset.
	 *
	 * zx279133_switch_xmac_configure()'s own comment says the reset is not
	 * wanted on this board: the soft reset word is shared with the PCS, and
	 * the PCS on this board was brought up by the bootloader and must not be
	 * disturbed.  That was written when the function was written; the call
	 * said otherwise, and the call is what runs.
	 *
	 * What the reset costs is measured.  docs/92 §15 wrote down a boot in
	 * which this lane really carried frames - the loader had brought it up
	 * and the kernel inherited it - and the near end's own PCS status word
	 * (NPPT + 0x0090) read 0x2c03 in that boot and in every one of the
	 * twelve such boots in the log, while the cold boots read 0x380b and the
	 * boots in which the driver had done its own work read 0x280b:
	 *
	 *	lane pre-serdes: XPCS0 status=0x380b   (the loader's hand-over)
	 *	lane after:      XPCS0 status=0x280b   (after the XMAC0 work)
	 *
	 * The XMAC0 reset is in the middle of that work, and the reset word is
	 * the PCS's, so the lane's link state goes with it.  Writing the
	 * loader's words back afterwards does not bring it back: the four PCS
	 * registers read 0x1140 / 0x0020 / 0x2200 / 0x0005 either way, and the
	 * status word stays 0x280b (measured on 2026-10-29, staging/ctl/
	 * win131.log).
	 *
	 * The XMAC0 is programmed either way; it is at its reset state already,
	 * every one of its control words reading zero.
	 */
	zx279133_switch_xmac_configure(priv, false);

	if (!priv->usxgmii) {
		/* Re-apply after the XMAC/SOPC work; the C45 AN bits are lost. */
		ret = zx279133_switch_xpcs_program_lane(priv);
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
		 "XMAC0 datapath enabled; XPCS0 status=0x%04x (bits 11..13 = 0x%03x) xmac_link=0x%04x; 9132 UNI ports %u..%u\n",
		 status,
		 (status >> 11) & 0x7,
		 zx279133_switch_nppt_read(priv, ZX279133_NPPT_XMAC_LINK),
		 ZX279133_SWITCH_UNI_PORT_MIN, ZX279133_SWITCH_UNI_PORT_MAX);

	/* And every word of the lane, after this driver's own work. */
	zx279133_switch_lane_snapshot(priv, "after");

	/*
	 * The far end's own word, once, here: this is the first sample of the
	 * series the lane watch keeps going (see the comment in the watch).
	 * Under the loader it reads 0x00e9, so this line says whether the far
	 * end was still carrying code at the moment this driver was done with
	 * the lane - which is the question the next round asks.
	 */
	zx279133_switch_outerphy_link(priv);

	/*
	 * Watch the lane from now on.
	 *
	 * The NPPT's prepare - which runs when eth0 is opened, thirteen
	 * seconds into the boot - resets the SerDes domain this lane lives in,
	 * and nothing has programmed the lane since this probe.  The watcher
	 * puts the loader's values back when it finds the block empty.
	 */
	if (!priv->usxgmii) {
		/*
		 * The watch's fast window starts here, so that the thirty
		 * seconds it covers include the moment this lane is handed
		 * over - the eth0 link-up happens about ten seconds into the
		 * boot.
		 */
		priv->lane_start = jiffies;
		schedule_delayed_work(&priv->lane_work, msecs_to_jiffies(2000));
	}

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

static void zx279133_switch_lane_work_cleanup(void *data)
{
	struct zx279133_switch *priv = data;

	/* The watcher reads the lane's PCS; it must not outlive the driver. */
	cancel_delayed_work_sync(&priv->lane_work);
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
