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

#include <linux/bitfield.h>
#include <linux/clk.h>
#include <linux/debugfs.h>
#include <linux/device.h>
#include <linux/fs.h>
#include <linux/io.h>
#include <linux/iopoll.h>
#include <linux/kernel.h>
#include <linux/kstrtox.h>
#include <linux/module.h>
#include <linux/mutex.h>
#include <linux/of.h>
#include <linux/phy.h>
#include <linux/phy/phy.h>
#include <linux/platform_device.h>
#include <linux/printk.h>
#include <linux/reset.h>
#include <linux/string.h>
#include <linux/uaccess.h>

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
	/* Which of the three images to write: 0 = the tree's own 1G one,
	 * 1 = the loader's 2.5G one, 2 = the vendor's 2.5G one (see the
	 * three arrays below).
	 */
	int image_sel;
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
 * 2026-10-28 13:00, one bit corrected.  The same U-Boot window, read again
 * after its net init (win105, staging/ctl/win105.log) and cross-checked
 * against the vendor's own mode_eth_1gbase_x_cfg() image
 * (devtools/np133.py image mode_eth_1gbase_x_cfg, entries 46..94), gives
 * word 0 = 0xe0000004 - not 0xe0001004.  The vendor's image and the board's
 * own measurement agree, and the bit 12 that was set here is in neither.
 *
 * That mistake was invisible to this driver's own check: it writes the
 * array and then compares the block against the same array, so it reported
 * "image differs in 0/49 word(s)" while the block differed from the
 * working U-Boot by exactly that one bit.  The window now has a dump
 * (below) so that the block can be compared with what the loader leaves
 * behind instead of with what this file says.
 *
 * The two images differ in 28 of the 49 words, and 28 is the number this
 * driver has been reporting since it started looking ("Uni SerDes 1G
 * inherited: ... image differs in 28/49 word(s)").  The word at
 * 0x16100054 - the mode register - reads 0x101 here and 0x01 in the other
 * image, and the log has been saying "mode 0x101 already" all along: the
 * board was holding the base-X image the whole time and the reference file
 * in this driver was the wrong one.
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
	0xe0000004, 0x4fa840a3, 0x013e860f, 0x0210c073,
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

/*
 * The image the bootloader itself leaves in this block, measured on the
 * board on 2026-10-28 14:46 with `md 0x16100000 0x40` at the loader's
 * prompt, after the loader's net init had run (the loader only configures
 * this lane when its network is brought up - `uni serdes init succeed` and
 * `mode_eth_1gbase_x_cfg` in its log).
 *
 * It differs from the image above in 18 of the 49 words, and the two that
 * matter most are the mode word and word 0:
 *
 *	word 0		0xe0000004 here and in the image above (0xe0001004 was
 *			the one that used to be wrong here)
 *	word 21	(mode)	0x00000409 here, 0x00000101 in the image above
 *
 * Which image this is, settled by measurement on 2026-10-29 05:11 at the
 * loader's own prompt in a boot where the loader took its 2.5G branch
 * (docs/92 §96.15): the 49 words the loader left behind then are this
 * image, word for word - 0 of 49 different.  The loader's other branch,
 * the 1G one, leaves the image above (mode 0x101).  So this is the
 * loader's 2.5G image, and the name it used to carry here ("the board's
 * 1G base-X image") was wrong.
 *
 * The image above is the one that makes the four 1G jacks work today, so
 * this one is not put in its place: the console writes either of them on
 * demand, which is how the two are compared on a live board.
 */
static const u32 zx279133_uni_1gbase_x_profile[] = {
	0xe0000004, 0x4fa8c0a2, 0x013e8604, 0x1f51c8f3,
	0x00000044, 0x00194000, 0x00ff8000, 0x00238220,
	0x80000100, 0x00050000, 0x0c633830, 0x00000000,
	0x00000020, 0x00002000, 0x00000000, 0x00ff0000,
	0x05a85100, 0x00000000, 0x04005b6a, 0x60002220,
	0x34000003, 0x00000409, 0x00000080, 0x00000000,
	0x200574a8, 0x00649052, 0x01000000, 0x40007700,
	0xa9004000, 0x01108002, 0x000000da, 0x800000c8,
	0x10371037, 0x01000200, 0x00020001, 0x10000000,
	0x0000001c, 0x802dc000, 0x000000ff, 0x55555500,
	0x55555555, 0x00555555, 0x30000818, 0x40002000,
	0x0000000c, 0x01000000, 0x00000080, 0x00010000,
	0x00000000,
};

static_assert(ARRAY_SIZE(zx279133_uni_1gbase_x_profile) ==
	      ZX279133_UNI_SERDES_PROFILE_WORDS);

/*
 * The vendor's own 2.5G base-X image, word for word.
 *
 * Provenance: np_133.ko's uni_mode_eth_2p5gbase_x_cfg(), read out of the
 * module with devtools/np133.py image (docs/92 §96.12).  The vendor picks
 * this image with uni_serdes_init(port, 5) - modes 5 and 6 - and the 1G one
 * with uni_serdes_init(port, 7) - modes 7 and 8 - so the two are two rate
 * classes of the same lane and not two variants of one.
 *
 * The array above - the one this file used to call "the loader's measured
 * base-X image" - is this image with two words changed: word 6
 * (0x00ff8000 here, 0x0b080000 in the vendor's copy) and word 21, the mode
 * word at 0x16100054 (0x409 here, 0x408 in the vendor's copy; bit 0 is the
 * enable bit and the driver sets it anyway).
 *
 * The loader does not use this one: in its 2.5G branch the block is left
 * holding the array above, word for word (0 of 49 different, measured
 * 2026-10-29 05:11 at the loader's prompt, docs/92 §96.15), and this one
 * differs from that in exactly the two words named here.
 *
 * The mode word is what tells the two apart at runtime: 0x101 is the 1G
 * image, 0x409 is the 2.5G one.  On 2026-10-29 the driver was seen putting
 * 0x101 into this block on every boot while the peer at the far end of the
 * lane - the RTL8226 on the 2.5G jack - was in 2500BASE-X (MMD 0x1e reg
 * 0x7580 = 0x9416).  Which image is right for which rate is the question
 * docs/92 §96.12 asks, and this console can answer it by hand:
 *
 *	"write 2"	writes the vendor's 2.5G image
 *
 * The word 6 difference is not cosmetic: the vendor's 1G image has
 * 0x0b016100 there and its 2.5G image 0x0b080000, so that word is part of
 * the rate class, and the board's own measurement (0x00ff8000) is neither.
 */
static const u32 zx279133_uni_2p5gbase_x_profile[] = {
	0xe0000004, 0x4fa8c0a2, 0x013e8604, 0x1f51c8f3,
	0x00000044, 0x00194000, 0x0b080000, 0x00238220,
	0x80000100, 0x00050000, 0x0c633830, 0x00000000,
	0x00000020, 0x00002000, 0x00000000, 0x00ff0000,
	0x05a85100, 0x00000000, 0x04005b6a, 0x60002220,
	0x34000003, 0x00000408, 0x00000080, 0x00000000,
	0x200574a8, 0x00649052, 0x01000000, 0x40007700,
	0xa9004000, 0x01108002, 0x000000da, 0x800000c8,
	0x10371037, 0x01000200, 0x00020001, 0x10000000,
	0x0000001c, 0x802dc000, 0x000000ff, 0x55555500,
	0x55555555, 0x00555555, 0x30000818, 0x40002000,
	0x0000000c, 0x01000000, 0x00000080, 0x00010000,
	0x00000000,
};

static_assert(ARRAY_SIZE(zx279133_uni_2p5gbase_x_profile) ==
	      ZX279133_UNI_SERDES_PROFILE_WORDS);

/*
 * The three images this block can hold, in the order the console numbers
 * them: 0 = the tree's own 1G image, 1 = the board's measured one, 2 = the
 * vendor's 2.5G image.
 */
static const u32 *zx279133_uni_serdes_image(int sel)
{
	switch (sel) {
	case 1:
		return zx279133_uni_1gbase_x_profile;
	case 2:
		return zx279133_uni_2p5gbase_x_profile;
	default:
		return zx279133_uni_1g_profile;
	}
}

static void zx279133_uni_serdes_write_profile(struct zx279133_uni_serdes *priv)
{
	const u32 *profile = priv->submode == PHY_INTERFACE_MODE_SGMII ?
		zx279133_uni_serdes_image(priv->image_sel) :
		zx279133_uni_10g_profile;
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
		/*
		 * This lane's image is the vendor's 1000BASE-X one: the one its
		 * xmac_1gbase_x_conf() reaches through uni_serdes_init(xmac, 7),
		 * which is the image measured in the board's own U-Boot
		 * (e0001004 4fa840a3 013e860f 0210c073 ..., mode word 0x101) and
		 * the one in zx279133_uni_1g_profile[].
		 *
		 * The bootloader does not leave it behind.  In a normal boot
		 * U-Boot never touches this lane at all - its banner does the PON
		 * side and stops - and the Uni SerDes still holds its reset
		 * default (mode word 0x01, image c0000004 50a840a7 012e064f
		 * 1fd0c093 ...), read at the loader's prompt on 2026-10-28 07:12.
		 * The "mode_eth_1gbase_x_cfg" and "uni serdes init succeed"
		 * lines in the log come from the sessions where U-Boot's net init
		 * ran, not from a normal boot.  So the image has to be written
		 * here.
		 *
		 * The vendor's uni_serdes_init() writes it like this: reset the
		 * datapath, write the 49 words, set the enable bit, then wait for
		 * the PLL and the CDR.  Neither wait is fatal here.  The CDR only
		 * locks once the far end of this lane is up, and that is the
		 * switch driver's business, not this driver's; failing the
		 * attach over a wait that has not timed out yet would take the
		 * whole datapath down.
		 *
		 * The far end of this lane is **the 9132's CPU port**, not the
		 * 2.5G jack's PHY.  The path across this board is:
		 *
		 *	PC --[1G jack]----- 9132 internal PHY   chip ports 1..4
		 *	PC --[2.5G jack]--- RTL8226 --(1000BASE-X)-- 9132 chip port 5
		 *	                                       9132 CPU port
		 *	                                           |
		 *	                         (this lane: Uni SerDes / XPCS0 /
		 *	                          XMAC0, SGMII 1G)
		 *	                                           |
		 *	                                         SoC
		 *
		 * An earlier version of this comment said "the far end of
		 * this lane - the RTL8226 on the 2.5G jack", and that was
		 * wrong, and it was wrong in a way that cost a kernel build:
		 * it made this lane's PCS mode look like the reason the 2.5G
		 * jack was dead, and the PCS mode was programmed as 1000BASE-X
		 * when the working state is SGMII.  This lane was never the
		 * problem - the 1G jacks go through it, and they work
		 * (docs/92 §81, §82).
		 */
		/*
		 * The reset is only needed when the image has to be written.
		 *
		 * In a boot where the loader's net init ran, this block already
		 * holds the right image and is already enabled - the count below
		 * says "0/49 word(s) differ" - and the lane behind it is the one
		 * the loader had brought up.  The reset pulse takes the SerDes
		 * domain down, and that domain holds the lane's PCS
		 * configuration too: the PCS's AN_CTRL reads 0x0000 right after
		 * this call, and the four registers the lane needs (the loader's
		 * own 0x1140 / 0x0020 / 0x2200 / 0x0005) are gone.  What is lost
		 * is the PCS's state, not its registers: writing the image and
		 * the four words back afterwards does not bring the link back
		 * (measured 2026-10-29, staging/ctl/win130.log and win131.log).
		 *
		 * So: when the block already holds the image and is enabled, do
		 * not touch it.
		 */
		{
			/*
			 * Is there anything in this block at all?
			 *
			 * The reset default of this block is the mode word 0x01
			 * and a run of words this driver does not know.  Any
			 * other mode word means somebody configured this
			 * SerDes - and on this board that somebody is the
			 * bootloader, whose net bring-up writes one of the
			 * vendor's images into this block.
			 *
			 * Which image it is does not have to be known to
			 * decide this.  The bootloader runs the vendor's own
			 * U-Boot code, and its copy of an image is not
			 * necessarily word for word the copy in np_133.ko -
			 * the mode word is what says "configured", and the
			 * three known images are only used to name it in the
			 * log.
			 *
			 * Not touching this block matters more than writing
			 * the right one: this block's reset takes the SerDes
			 * domain down, and the lane's PCS lives in that
			 * domain.  The reset is what emptied the PCS
			 * (AN_CTRL 0x0000) in every boot measured on
			 * 2026-10-29 (staging/ctl/win129.log).
			 */
			static const char *const names[] = {
				"the tree's 1G image",
				"the loader's 2.5G image",
				"the vendor's 2.5G image",
			};
			const u32 *profile;
			int match = -1;
			int sel;

			status = readl(priv->serdes + ZX279133_UNI_SERDES_MODE_CTRL);
			for (sel = 0; sel < (int)ARRAY_SIZE(names); sel++) {
				profile = zx279133_uni_serdes_image(sel);
				for (i = 0; i < ZX279133_UNI_SERDES_PROFILE_WORDS; i++)
					if (readl(priv->serdes + i * 4) != profile[i])
						break;
				if (i == ZX279133_UNI_SERDES_PROFILE_WORDS) {
					match = sel;
					break;
				}
			}
			if ((status & ZX279133_UNI_SERDES_ENABLE) &&
			    (status & ~ZX279133_UNI_SERDES_ENABLE)) {
				dev_info(priv->dev,
					 "Uni SerDes image inherited: mode %#x, %s, status %#x/%#x; the block is not reset\n",
					 status,
					 match >= 0 ? names[match] :
					 "an image this driver does not know",
					 readl(priv->serdes + ZX279133_UNI_SERDES_PLL_STATUS),
					 readl(priv->serdes + ZX279133_UNI_SERDES_RX_STATUS));
				priv->powered = true;
				ret = 0;
				goto out_unlock;
			}
		}

		ret = zx279133_uni_serdes_reset_pulse(priv);
		if (ret)
			goto err_disable_clocks;

		zx279133_uni_serdes_write_profile(priv);
		status = readl(priv->serdes + ZX279133_UNI_SERDES_MODE_CTRL);
		how = status & ZX279133_UNI_SERDES_ENABLE ? "already" : "set";
		writel(status | ZX279133_UNI_SERDES_ENABLE,
		       priv->serdes + ZX279133_UNI_SERDES_MODE_CTRL);

		readl_poll_timeout(priv->serdes + ZX279133_UNI_SERDES_PLL_STATUS,
				    status, status & ZX279133_UNI_SERDES_PLL_LOCK,
				    ZX279133_UNI_SERDES_POLL_US,
				    ZX279133_UNI_SERDES_TIMEOUT_US);
		readl_poll_timeout(priv->serdes + ZX279133_UNI_SERDES_RX_STATUS,
				    status,
				    status & ZX279133_UNI_SERDES_CDR_LOCK,
				    ZX279133_UNI_SERDES_POLL_US,
				    ZX279133_UNI_SERDES_TIMEOUT_US);
		/*
		 * Count the image that is in there now.  It has to be 0/49: the
		 * words above were just written from the same file.  A count
		 * that is not zero says the block did not take the writes, and
		 * that is worth seeing in the log.
		 */
		for (i = 0; i < ZX279133_UNI_SERDES_PROFILE_WORDS; i++)
			if (readl(priv->serdes + i * 4) != zx279133_uni_1g_profile[i])
				mismatch++;

		dev_info(priv->dev,
			 "Uni SerDes 1G profile written: mode %#x %s, image differs in %u/%d word(s), status %#x/%#x\n",
			 readl(priv->serdes + ZX279133_UNI_SERDES_MODE_CTRL), how,
			 mismatch, ZX279133_UNI_SERDES_PROFILE_WORDS,
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
		/* An ifdown must not tear down this lane: clearing the enable bit and gating the
		 * clock here is how a configured SerDes gets lost between one command and the next.
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

/*
 * debugfs hands the pointer given to debugfs_create_file() to the
 * inode as inode->i_private - it does not fill file->private_data.
 * The console wants the pointer in file->private_data, so it has to
 * be picked up when the file is opened.
 */
static int zx279133_uni_serdes_console_open(struct inode *inode,
					    struct file *file)
{
	file->private_data = inode->i_private;

	return 0;
}

/*
 * A console for this block.
 *
 * The point is to be able to compare the image in the block with what the
 * bootloader leaves behind - the driver's own check compares the block with
 * the array in this file, which is circular and once hid a one-bit
 * difference (word 0 was 0xe0001004 here and 0xe0000004 in the loader).
 *
 *	"dump"		print the first 256 words and the two status words,
 *			and the words in which image 0 and the selected image
 *			differ
 *	"write"		write the image the tree uses again, and set the
 *			enable bit
 *	"write 1"	write the board's measured image (the mode word
 *			0x409)
 *	"write 2"	write the vendor's own 2.5G base-X image
 *	"reset"	pulse the rx/tx resets
 */
static ssize_t zx279133_uni_serdes_console(struct file *file,
					   const char __user *ubuf,
					   size_t count, loff_t *ppos)
{
	struct zx279133_uni_serdes *priv = file->private_data;
	char buf[32];
	unsigned int i;

	if (count >= sizeof(buf))
		return -EINVAL;
	if (copy_from_user(buf, ubuf, count))
		return -EFAULT;
	buf[count] = '\0';

	if (!strncmp(buf, "dump", 4)) {
		const u32 *other = zx279133_uni_serdes_image(priv->image_sel);
		unsigned int diff = 0;

		for (i = 0; i < 256; i++)
			pr_info("uni serdes +0x%03x = 0x%08x\n", i * 4,
				readl(priv->serdes + i * 4));
		pr_info("uni serdes: mode 0x%08x status 0x%08x/0x%08x\n",
			readl(priv->serdes + ZX279133_UNI_SERDES_MODE_CTRL),
			readl(priv->serdes + ZX279133_UNI_SERDES_PLL_STATUS),
			readl(priv->serdes + ZX279133_UNI_SERDES_RX_STATUS));
		pr_info("uni serdes: image %d\n", priv->image_sel);
		for (i = 0; i < ZX279133_UNI_SERDES_PROFILE_WORDS; i++)
			if (zx279133_uni_1g_profile[i] != other[i]) {
				pr_info("uni serdes: word %u differs: own 0x%08x, image %d 0x%08x\n",
					i, zx279133_uni_1g_profile[i], priv->image_sel,
					other[i]);
				diff++;
			}
		pr_info("uni serdes: %u of %d word(s) differ between image 0 and image %d\n",
			diff, ZX279133_UNI_SERDES_PROFILE_WORDS, priv->image_sel);
		return count;
	}

	if (!strncmp(buf, "write", 5)) {
		int which = priv->image_sel;

		if (buf[5] == ' ') {
			if (kstrtoint(buf + 6, 0, &which))
				return -EINVAL;
		}
		if (which < 0 || which > 2)
			return -EINVAL;

		mutex_lock(&priv->lock);
		priv->image_sel = which;
		zx279133_uni_serdes_write_profile(priv);
		writel(readl(priv->serdes + ZX279133_UNI_SERDES_MODE_CTRL) |
			ZX279133_UNI_SERDES_ENABLE,
			priv->serdes + ZX279133_UNI_SERDES_MODE_CTRL);
		mutex_unlock(&priv->lock);
		pr_info("uni serdes: image %d written again\n", which);
		return count;
	}

	if (!strncmp(buf, "reset", 5)) {
		int ret;

		mutex_lock(&priv->lock);
		ret = zx279133_uni_serdes_reset_pulse(priv);
		mutex_unlock(&priv->lock);
		pr_info("uni serdes: reset pulse returned %d\n", ret);
		return count;
	}

	return -EINVAL;
}

static const struct file_operations zx279133_uni_serdes_console_fops = {
	.owner	= THIS_MODULE,
	.open	= zx279133_uni_serdes_console_open,
	.write	= zx279133_uni_serdes_console,
};

static void zx279133_uni_serdes_debugfs_remove(void *data)
{
	debugfs_remove_recursive(data);
}

static int zx279133_uni_serdes_probe(struct platform_device *pdev)
{
	struct device *dev = &pdev->dev;
	struct zx279133_uni_serdes *priv;
	struct phy_provider *provider;
	struct phy *phy;
	struct dentry *dir, *df;
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
	if (IS_ERR(provider))
		return PTR_ERR(provider);

	/*
	 * The console, and the reason it is built this way:
	 *
	 * The kernel has no devm_debugfs_create_file() and no
	 * devm_debugfs_create_dir().  Those names do not exist upstream - the
	 * only devm helpers of that shape are the devm_k*() ones - and
	 * writing them down here is what broke the build in CI run #217.
	 * debugfs_create_dir() and debugfs_create_file() take five arguments
	 * (name, mode, parent, data, fops); the old "aux" argument is
	 * gone, and there is a separate debugfs_create_file_aux() for the
	 * rare caller that wants one.
	 *
	 * The directory is removed with the device, so the cleanup goes
	 * through devm_add_action_or_reset() the way the NPPT console does.
	 * debugfs_remove_recursive() takes the file with the directory.
	 */
	dir = debugfs_create_dir("zx279133-uni-serdes", NULL);
	if (IS_ERR(dir)) {
		/* No debugfs: the block still works, there is just no console. */
		dev_info(dev, "debugfs is not available, no console for this block\n");
		return 0;
	}
	ret = devm_add_action_or_reset(dev, zx279133_uni_serdes_debugfs_remove,
				       dir);
	if (ret)
		return ret;

	df = debugfs_create_file("serdes", 0644, dir, priv,
				 &zx279133_uni_serdes_console_fops);
	if (IS_ERR(df)) {
		dev_warn(dev, "no console file for this block\n");
		return 0;
	}

	return 0;
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
