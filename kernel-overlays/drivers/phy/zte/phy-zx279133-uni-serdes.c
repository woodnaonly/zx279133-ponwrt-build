// SPDX-License-Identifier: GPL-2.0-only
/*
 * ZTE ZX279133 internal Uni SerDes PHY.
 *
 * The SR1010 LAN CPU uplink uses XMAC0 work mode 5: Uni SerDes mode 1
 * (the vendor 10G Ethernet profile) followed by XPCS0 USXGMII mode 0.
 * SK-D840N's panel jack 4 uses the same block as 1000BASE-X/SGMII, with the
 * image measured out of that lane while it was carrying gigabit traffic.
 * This provider owns only the internal Uni SerDes block; XMAC0/XPCS0 and
 * the RTL8372N switch are separate consumers.
 */

#include <linux/clk.h>
#include <linux/io.h>
#include <linux/iopoll.h>
#include <linux/module.h>
#include <linux/mutex.h>
#include <linux/of.h>
#include <linux/phy.h>
#include <linux/phy/phy.h>
#include <linux/platform_device.h>
#include <linux/reset.h>

#define ZX279133_UNI_SERDES_PROFILE_WORDS	49
#define ZX279133_UNI_SERDES_MODE_CTRL		0x54
#define ZX279133_UNI_SERDES_ENABLE		BIT(0)
#define ZX279133_UNI_SERDES_PLL_STATUS		0xd0
#define ZX279133_UNI_SERDES_PLL_LOCK		BIT(0)
#define ZX279133_UNI_SERDES_RX_STATUS		0xe4
#define ZX279133_UNI_SERDES_RX_LOS		BIT(0)
#define ZX279133_UNI_SERDES_CDR_LOCK		BIT(1)
#define ZX279133_UNI_SERDES_POLL_US		2000
#define ZX279133_UNI_SERDES_TIMEOUT_US		2000000
#define ZX279133_UNI_RESET_PHASE_US		4300

struct zx279133_uni_serdes {
	struct device *dev;
	void __iomem *serdes;
	struct clk_bulk_data clocks[2];
	struct reset_control_bulk_data resets[2];
	struct phy *phy;
	/* Protects the powered state and clock/reset lifecycle. */
	struct mutex lock;
	bool powered;
	int submode;
};

/* Vendor uni_mode_eth_10gbase_r_cfg(), used for Uni mode 1. */
static const u32 zx279133_uni_10g_profile[] = {
	0xe0000004, 0x50a840a3, 0x013e8687, 0x0210c073,
	0x00000048, 0x00000000, 0x00ff8000, 0x00238020,
	0x80000400, 0x00020000, 0x0c633830, 0x00000e46,
	0x00000020, 0x00002000, 0x00000000, 0x00ff0000,
	0x05a85100, 0x00000000, 0x4f042b2a, 0x60002200,
	0x34000003, 0x00000100, 0x00000080, 0x00000000,
	0x200554a8, 0x67748091, 0x01000000, 0x40003b00,
	0xa9004000, 0x10670002, 0x000000da, 0xf20000c8,
	0x10371037, 0x01000200, 0x00020001, 0x10000000,
	0x00084008, 0x802dc000, 0x000000ff, 0x55555500,
	0x55555555, 0x00555555, 0x30000818, 0x40002000,
	0x0000000c, 0x01000000, 0x00000080, 0x00010000,
	0x00000000,
};

static_assert(ARRAY_SIZE(zx279133_uni_10g_profile) ==
	      ZX279133_UNI_SERDES_PROFILE_WORDS);

/*
 * The image this lane actually lives in, read out of the board rather than
 * transcribed.
 *
 * Provenance: 2026-10-28 05:52, the board's own U-Boot, `md 0x16100000 0x31`
 * - 49 words - taken right after `xmac_test xmac_work_mode_set 0 2`, which
 * printed "mode_eth_1gbase_x_cfg" and "work in 1000BASE-X mode. ret = 0".
 *
 * This file used to hold the other image: the 49 words U-Boot leaves behind
 * when it has *not* configured this lane for base-X.  The two differ in
 * exactly 28 of the 49 words, and 28 is the number this driver has been
 * reporting since it started looking ("Uni SerDes 1G inherited: ... image
 * differs in 28/49 word(s)").  The word at 0x16100054 - the mode register -
 * reads 0x101 here and 0x01 in the other image, and the log has been saying
 * "mode 0x101 already" all along: the board was holding the base-X image the
 * whole time and the reference file in this driver was the wrong one.
 *
 * What the two images mean: the base-X one has the mode word at 0x101 and
 * the 1.25G lane state in the registers around it; the other one (0x01, and
 * the words this file used to carry) is what the same bootloader leaves
 * behind when the lane is left in SGMII.  U-Boot's net init prints
 * "mode_eth_1gbase_x_cfg" on the way in, so the bootloader configures this
 * lane for base-X before Linux ever sees it.
 *
 * np_133.ko's uni_mode_eth_10gbase_r_cfg() list (zx279133_uni_10g_profile
 * below) matches this image on 33 of the 49 words, and the older 1G list
 * matches it on 21 - the same 21 the comment above used to quote against
 * the wrong file.
 */
static const u32 zx279133_uni_1g_profile[] = {
	0xe0001004, 0x4fa840a3, 0x013e860f, 0x0210c073,
	0x00000048, 0x00000000, 0x00ff8000, 0x00238620,
	0x80000000, 0x00050000, 0x0c633830, 0x00000007,
	0x00000020, 0x00002000, 0x00000000, 0x00ff0000,
	0xc5a85100, 0x00000000, 0x01045b2a, 0x66002200,
	0x34000003, 0x00000101, 0x00000080, 0x00000000,
	0x200554a8, 0x00648091, 0x01000000, 0x00003700,
	0xa9004000, 0x10640002, 0x000000da, 0xf20000c8,
	0x10371037, 0x01000200, 0x00020001, 0x10000000,
	0x0000001c, 0x8000c000, 0x000000ff, 0x55555500,
	0x55555555, 0x00555555, 0x30000818, 0x40002000,
	0x0000000c, 0x01000000, 0x00000080, 0x00010000,
	0x00000000,
};

static_assert(ARRAY_SIZE(zx279133_uni_1g_profile) ==
	      ZX279133_UNI_SERDES_PROFILE_WORDS);

static void zx279133_uni_serdes_write_profile(struct zx279133_uni_serdes *priv)
{
	const u32 *profile = priv->submode == PHY_INTERFACE_MODE_SGMII ?
		zx279133_uni_1g_profile : zx279133_uni_10g_profile;
	unsigned int i;

	for (i = 0; i < ZX279133_UNI_SERDES_PROFILE_WORDS; i++)
		writel(profile[i], priv->serdes + i * 4);
}

static int zx279133_uni_serdes_set_mode(struct phy *phy, enum phy_mode mode,
					int submode)
{
	struct zx279133_uni_serdes *priv = phy_get_drvdata(phy);

	if (mode != PHY_MODE_ETHERNET)
		return -EOPNOTSUPP;

	if (submode != PHY_INTERFACE_MODE_USXGMII &&
	    submode != PHY_INTERFACE_MODE_10GBASER &&
	    submode != PHY_INTERFACE_MODE_SGMII)
		return -EOPNOTSUPP;

	/* nppt powers the SerDes off, sets the mode and powers it back on again, so
	 * storing it here is enough to make the next power_on() write this lane's image.
	 */
	priv->submode = submode;

	return 0;
}

static int zx279133_uni_serdes_reset_pulse(struct zx279133_uni_serdes *priv)
{
	int ret;

	ret = reset_control_bulk_assert(ARRAY_SIZE(priv->resets), priv->resets);
	if (ret)
		return ret;
	usleep_range(ZX279133_UNI_RESET_PHASE_US,
		     ZX279133_UNI_RESET_PHASE_US + 500);

	ret = reset_control_bulk_deassert(ARRAY_SIZE(priv->resets), priv->resets);
	if (ret)
		return ret;
	usleep_range(ZX279133_UNI_RESET_PHASE_US,
		     ZX279133_UNI_RESET_PHASE_US + 500);

	return 0;
}

static int zx279133_uni_serdes_power_on(struct phy *phy)
{
	struct zx279133_uni_serdes *priv = phy_get_drvdata(phy);
	unsigned int i, mismatch = 0;
	const char *how;
	u32 status;
	int ret;

	mutex_lock(&priv->lock);
	if (priv->powered) {
		ret = 0;
		goto out_unlock;
	}

	/*
	 * Do not read this block before the clocks are on.  The registers live
	 * behind the UNI SerDes CSR clock, and a readl() of them while that
	 * clock is still gated is a bus abort, not a zero: run207 stopped the
	 * board 0.38 s into the boot, right where this read used to be, with
	 * no panic and no oops on the console.  The count further down runs
	 * after clk_bulk_prepare_enable() and is safe.
	 */
	ret = clk_bulk_prepare_enable(ARRAY_SIZE(priv->clocks), priv->clocks);
	if (ret)
		goto out_unlock;

	if (priv->submode == PHY_INTERFACE_MODE_SGMII) {
		/* Inherit: no reset pulse, no image write, no rate registers.
		 *
		 * The bootloader configures this lane for base-X on its way in -
		 * its net init prints "mode_eth_1gbase_x_cfg" - and the 49 words
		 * it leaves behind are the ones in zx279133_uni_1g_profile[].
		 * So the image this driver must not touch is already the right
		 * one, and the count below has to come out as 0/49.  It used to
		 * come out as 28/49, because the reference file in this driver
		 * held the other image (the one U-Boot leaves behind when it has
		 * not configured the lane), and 28 is exactly the difference
		 * between the two.
		 *
		 * Every mainline bring-up that does rewrite it has lost the lane:
		 * writing zx279133_uni_10g_profile (which is the *PON* SerDes'
		 * image, 21/49 against this one) linked the PHY but returned not
		 * one frame (rx_packets 0, xmac_tx_good_frames 12); writing the
		 * measured file after a reset pulse (run 51, 20:14) did not even
		 * link the PHY: "Link detected: no", supported 10baseT only,
		 * tx_packets 0, ARP FAILED, while U-Boot had carried 11 MB
		 * minutes earlier in the same cycle.  The bring-up that can be
		 * right is the one that leaves this block alone - so the only
		 * thing done here is to make sure the enable bit is set, and
		 * to report what the block says about itself.
		 */
		status = readl(priv->serdes + ZX279133_UNI_SERDES_MODE_CTRL);
		how = status & ZX279133_UNI_SERDES_ENABLE ? "already" : "set";
		if (!(status & ZX279133_UNI_SERDES_ENABLE))
			writel(status | ZX279133_UNI_SERDES_ENABLE,
			       priv->serdes + ZX279133_UNI_SERDES_MODE_CTRL);
		/* Count, do not correct: whether the block came up in the image the working lane
		 * was measured in is the single question this round can answer, and the answer has
		 * to be in the log because nothing else on this board can read it back later.
		 */
		for (i = 0; i < ZX279133_UNI_SERDES_PROFILE_WORDS; i++)
			if (readl(priv->serdes + i * 4) != zx279133_uni_1g_profile[i])
				mismatch++;

		dev_info(priv->dev,
			 "Uni SerDes 1G inherited: mode %#x %s, image differs in %u/%d word(s), status %#x/%#x\n",
			 readl(priv->serdes + ZX279133_UNI_SERDES_MODE_CTRL), how, mismatch,
			 ZX279133_UNI_SERDES_PROFILE_WORDS,
			 readl(priv->serdes + ZX279133_UNI_SERDES_PLL_STATUS),
			 readl(priv->serdes + ZX279133_UNI_SERDES_RX_STATUS));
		priv->powered = true;
		ret = 0;
		goto out_unlock;
	}

	ret = zx279133_uni_serdes_reset_pulse(priv);
	if (ret)
		goto err_disable_clocks;

	zx279133_uni_serdes_write_profile(priv);
	status = readl(priv->serdes + ZX279133_UNI_SERDES_MODE_CTRL);
	writel(status | ZX279133_UNI_SERDES_ENABLE,
	       priv->serdes + ZX279133_UNI_SERDES_MODE_CTRL);

	ret = readl_poll_timeout(priv->serdes + ZX279133_UNI_SERDES_PLL_STATUS,
				 status, status & ZX279133_UNI_SERDES_PLL_LOCK,
				 ZX279133_UNI_SERDES_POLL_US,
				 ZX279133_UNI_SERDES_TIMEOUT_US);
	if (ret)
		goto err_disable_clocks;

	status = readl(priv->serdes + ZX279133_UNI_SERDES_RX_STATUS);
	if (!(status & ZX279133_UNI_SERDES_RX_LOS)) {
		ret = readl_poll_timeout(priv->serdes + ZX279133_UNI_SERDES_RX_STATUS,
					 status,
					 status & ZX279133_UNI_SERDES_CDR_LOCK,
					 ZX279133_UNI_SERDES_POLL_US,
					 ZX279133_UNI_SERDES_TIMEOUT_US);
		if (ret)
			goto err_disable_clocks;
	}

	dev_info(priv->dev, "Uni SerDes 10G profile written: mode %#x, status %#x/%#x\n",
		 readl(priv->serdes + ZX279133_UNI_SERDES_MODE_CTRL),
		 readl(priv->serdes + ZX279133_UNI_SERDES_PLL_STATUS),
		 readl(priv->serdes + ZX279133_UNI_SERDES_RX_STATUS));
	priv->powered = true;
	ret = 0;
	goto out_unlock;

err_disable_clocks:
	writel(readl(priv->serdes + ZX279133_UNI_SERDES_MODE_CTRL) &
	       ~ZX279133_UNI_SERDES_ENABLE,
	       priv->serdes + ZX279133_UNI_SERDES_MODE_CTRL);
	clk_bulk_disable_unprepare(ARRAY_SIZE(priv->clocks), priv->clocks);
out_unlock:
	mutex_unlock(&priv->lock);
	return ret;
}

static int zx279133_uni_serdes_power_off(struct phy *phy)
{
	struct zx279133_uni_serdes *priv = phy_get_drvdata(phy);

	mutex_lock(&priv->lock);
	if (priv->submode == PHY_INTERFACE_MODE_SGMII) {
		/* An ifdown must not tear down a lane the bootloader just carried 11 MB through:
		 * clearing the enable bit and gating the clock here is how an inherited SerDes
		 * gets lost between one command and the next.
		 */
		mutex_unlock(&priv->lock);
		return 0;
	}
	if (priv->powered) {
		writel(readl(priv->serdes + ZX279133_UNI_SERDES_MODE_CTRL) &
		       ~ZX279133_UNI_SERDES_ENABLE,
		       priv->serdes + ZX279133_UNI_SERDES_MODE_CTRL);
		clk_bulk_disable_unprepare(ARRAY_SIZE(priv->clocks), priv->clocks);
		priv->powered = false;
	}
	mutex_unlock(&priv->lock);

	return 0;
}

static const struct phy_ops zx279133_uni_serdes_ops = {
	.set_mode = zx279133_uni_serdes_set_mode,
	.power_on = zx279133_uni_serdes_power_on,
	.power_off = zx279133_uni_serdes_power_off,
	.owner = THIS_MODULE,
};

static void zx279133_uni_serdes_cleanup(void *data)
{
	struct zx279133_uni_serdes *priv = data;

	if (priv->phy)
		zx279133_uni_serdes_power_off(priv->phy);
}

static int zx279133_uni_serdes_probe(struct platform_device *pdev)
{
	struct device *dev = &pdev->dev;
	struct zx279133_uni_serdes *priv;
	struct phy_provider *provider;
	struct phy *phy;
	int ret;

	priv = devm_kzalloc(dev, sizeof(*priv), GFP_KERNEL);
	if (!priv)
		return -ENOMEM;

	priv->dev = dev;
	mutex_init(&priv->lock);
	priv->serdes = devm_platform_ioremap_resource(pdev, 0);
	if (IS_ERR(priv->serdes))
		return PTR_ERR(priv->serdes);

	priv->clocks[0].id = "pclk";
	priv->clocks[1].id = "ref50m";
	ret = devm_clk_bulk_get(dev, ARRAY_SIZE(priv->clocks), priv->clocks);
	if (ret)
		return dev_err_probe(dev, ret, "failed to get Uni SerDes clocks\n");

	priv->resets[0].id = "rx";
	priv->resets[1].id = "tx";
	ret = devm_reset_control_bulk_get_exclusive(dev,
						    ARRAY_SIZE(priv->resets),
						    priv->resets);
	if (ret)
		return dev_err_probe(dev, ret, "failed to get Uni SerDes resets\n");

	phy = devm_phy_create(dev, NULL, &zx279133_uni_serdes_ops);
	if (IS_ERR(phy))
		return dev_err_probe(dev, PTR_ERR(phy), "failed to create PHY\n");

	phy_set_drvdata(phy, priv);
	priv->phy = phy;
	platform_set_drvdata(pdev, priv);

	ret = devm_add_action_or_reset(dev, zx279133_uni_serdes_cleanup, priv);
	if (ret)
		return ret;

	provider = devm_of_phy_provider_register(dev, of_phy_simple_xlate);
	return PTR_ERR_OR_ZERO(provider);
}

static const struct of_device_id zx279133_uni_serdes_of_match[] = {
	{ .compatible = "zte,zx279133-uni-serdes" },
	{ }
};
MODULE_DEVICE_TABLE(of, zx279133_uni_serdes_of_match);

static struct platform_driver zx279133_uni_serdes_driver = {
	.probe = zx279133_uni_serdes_probe,
	.driver = {
		.name = "zx279133-uni-serdes",
		.of_match_table = zx279133_uni_serdes_of_match,
	},
};
module_platform_driver(zx279133_uni_serdes_driver);

MODULE_DESCRIPTION("ZTE ZX279133 internal Uni SerDes PHY");
MODULE_LICENSE("GPL");
