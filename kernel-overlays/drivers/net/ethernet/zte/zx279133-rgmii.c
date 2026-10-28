// SPDX-License-Identifier: GPL-2.0-only
/*
 * zx279133-rgmii.c - the SoC's RGMII MAC register block (0x15900000).
 *
 * What this is for
 * ----------------
 *
 * The 2.5G jack is NOT behind this block.  That took a while to establish,
 * and it is worth writing down where the evidence is, because the block looks
 * like the obvious answer and it is not.
 *
 * The SoC has three MACs: XMAC0 (0x140000, the lane to the 9132's CPU
 * port), XMAC1 (0x180000, the PON side, which this tree's eth0 is), and
 * the RGMII MAC (0x15900000), which the vendor's device tree declares as
 *
 *	rgmii@15900000 {
 *		compatible = "zte,zx279133-rgmii";
 *		reg = <0x00 0x15900000 0x00 0x100000>;
 *	};
 *
 * and which the vendor's np_133.ko binds.  The tree this CI builds against
 * (cnjn/linux-mainline-zte-zxslc-sr1010 at 96e5a241) has neither that node
 * nor a driver for it.
 *
 * The evidence, all of it from this unit:
 *
 *  1. The board's U-Boot moves traffic through the 2.5G jack - at
 *     2026-10-28 03:45:57 it downloaded 12162040 bytes from the test PC
 *     through it ("tftpboot ... Bytes transferred = 12162040 (b993f8 hex)"),
 *     and the test PC has exactly one NIC, which is plugged into the 2.5G
 *     jack.  So the jack works, and it works in U-Boot.
 *
 *  2. U-Boot's own log says which MAC that is.  At 2026-10-28 04:09:10,
 *     "ping 192.168.9.25" from the U-Boot prompt answered "host 192.168.9.25
 *     is alive", and the network bring-up it printed just before that is the
 *     whole topology:
 *
 *	smac_init ... mac = 0x0 .. mac = 0x3
 *	--->phy identifier is [0xc849]		(the RTL8226)
 *	5 serdes option is 0
 *	xmac0 is used.
 *	[outerphy_phy_init]Warning: No outerphy on this port<5>
 *	uni serdes init succeed
 *	xmac 0 speed set 2 in sgmii(xmac speed 3)
 *	phy_id:0x5;  port:0x4;
 *	PHY_RLT8226_MDIO_READ_EXTENDED(0x2) in RTL_8226
 *	mac 4 phy status changed: 1000M full-duplex
 *
 *     XMAC0 goes into SGMII at 1G over the Uni SerDes - that is the 9132's
 *     CPU port - and the RTL8226 comes up on the 9132's port 4.  The 2.5G
 *     jack is therefore a copper port of the 9132 with an external PHY, and
 *     the SoC reaches it the same way it reaches the other three jacks: over
 *     the 9132's CPU port.  U-Boot's two MDIO buses say the same thing -
 *     "zx_mdio" carries the 9132's four internal PHYs at 0x0a..0x0d, and
 *     "zx_mdio_extern" carries the RTL8226 at 0x05.
 *
 *  3. This block is dead on this unit.  From the same U-Boot prompt:
 *
 *	=> md 0x15900000 0x10
 *	15900000: 00000000 00000000 00000000 00000000    ................
 *	...
 *	=> md 0x15900700 0x10
 *	15900700: 00000000 00000000 00000000 00000000    ................
 *
 *     All zeros, including the 41 counters smac_get_stat() reads.  The
 *     vendor's own Linux never brought this block up either: its boot log
 *     prints "rgmii_base is 0xffffff800a100000" when it maps the window,
 *     but "enter rgmii_init" never appears, and the vendor's own 2.5G port
 *     (its eth0) never receives a byte.
 *
 * So: the 2.5G jack needs a DSA port on the 9132, with the RTL8226 as that
 * port's PHY.  It does not need a MAC driver for this block.  What the block
 * is for on other boards is not known.
 *
 * What this driver does
 * ---------------------
 *
 * Nothing to the hardware: it maps the window and makes it readable, so the
 * zeros above are reproducible instead of a one-off observation from a
 * U-Boot prompt.  It does not configure the block, does not claim an
 * interrupt, and does not create a netdev.
 *
 * The register map, for the record, is what the vendor's own code says:
 *
 *   - the block is 0x400 words wide (np_133.ko's np_rgmii_reg_print() reads
 *     indices 0..0x3ff and writes them to /tmp/np/rgmii/rgmii_reg_value);
 *   - np_133.ko reaches it through fpga_rgmii_read_reg() /
 *     fpga_rgmii_write_reg(), which are *(u32 *)(base + index * 4) - so the
 *     index is a word index and the byte offset is four times it;
 *   - smac_get_stat() is the only function in np_133.ko that reads the
 *     block, and it reads 41 counters: word indices 0x1c5..0x1d1,
 *     0x1d9..0x1dc, 0x1e0..0x1e2, 0x1e3..0x1e5, 0x1e7..0x1ec, 0x1ed..0x1f1,
 *     0x1f4, 0x1f5, 0x1f7, 0x2c0, 0x2cc and 0x2cd.
 *
 * The console
 * -----------
 *
 *	cat /sys/kernel/debug/zx279133-rgmii		the counters and a summary
 *	echo 'r 0x714' > .../zx279133-rgmii		read one word
 *	echo 'w 0x714 0' > .../zx279133-rgmii		write one word and read it back
 *	echo 'dump 0x700 0x50' > .../zx279133-rgmii	read a range of words
 *
 * The write verbs are there for the experiments; nothing in the driver uses
 * them.
 */

#include <linux/debugfs.h>
#include <linux/errno.h>
#include <linux/fs.h>
#include <linux/io.h>
#include <linux/kernel.h>
#include <linux/module.h>
#include <linux/of.h>
#include <linux/platform_device.h>
#include <linux/seq_file.h>
#include <linux/string.h>
#include <linux/types.h>

#define ZX279133_RGMII_BASE		0x15900000
#define ZX279133_RGMII_WINDOW_SIZE	0x100000
/* The vendor's np_rgmii_reg_print() walks indices 0..0x3ff. */
#define ZX279133_RGMII_WORDS		0x400

/*
 * The counters smac_get_stat() reads, as word indices.  Grouped as the
 * vendor's function reads them: five groups of per-channel counters and
 * three global ones.
 */
static const u16 zx279133_rgmii_stat_idx[] = {
	0x1c5, 0x1c6, 0x1c7, 0x1c8, 0x1c9, 0x1ca, 0x1cb, 0x1cc, 0x1cd,
	0x1ce, 0x1cf, 0x1d0, 0x1d1,
	0x1d9, 0x1da, 0x1db, 0x1dc,
	0x1e0, 0x1e1, 0x1e2,
	0x1e3, 0x1e4, 0x1e5,
	0x1e7, 0x1e8, 0x1e9, 0x1ea, 0x1eb, 0x1ec,
	0x1ed, 0x1ee, 0x1ef, 0x1f0, 0x1f1,
	0x1f4, 0x1f5,
	0x1f7,
	0x2c0,
	0x2cc, 0x2cd,
};

struct zx279133_rgmii {
	struct device *dev;
	void __iomem *base;
	struct dentry *debugfs;
};

static inline u32 zx279133_rgmii_read(struct zx279133_rgmii *rgmii, u32 off)
{
	return readl(rgmii->base + off);
}

static void zx279133_rgmii_stats(struct seq_file *s, struct zx279133_rgmii *rgmii)
{
	unsigned int i;

	for (i = 0; i < ARRAY_SIZE(zx279133_rgmii_stat_idx); i++) {
		u32 idx = zx279133_rgmii_stat_idx[i];

		seq_printf(s, "  0x%03x -> 0x%05x -> 0x%08x\n", idx, idx * 4,
			   zx279133_rgmii_read(rgmii, idx * 4));
	}
}

static void zx279133_rgmii_stats_log(struct zx279133_rgmii *rgmii)
{
	unsigned int i;

	for (i = 0; i < ARRAY_SIZE(zx279133_rgmii_stat_idx); i++) {
		u32 idx = zx279133_rgmii_stat_idx[i];

		dev_info(rgmii->dev, "rgmii counter 0x%03x = 0x%08x", idx,
			 zx279133_rgmii_read(rgmii, idx * 4));
	}
}

static int zx279133_rgmii_show(struct seq_file *s, void *data)
{
	struct zx279133_rgmii *rgmii = s->private;
	u32 i;

	seq_printf(s, "RGMII MAC register block at 0x%08x, %u words\n",
		   ZX279133_RGMII_BASE, ZX279133_RGMII_WORDS);
	seq_printf(s, "the first 0x40 words\n");
	for (i = 0; i < 0x40; i++)
		seq_printf(s, "  0x%03x -> 0x%05x -> 0x%08x\n", i, i * 4,
			   zx279133_rgmii_read(rgmii, i * 4));
	seq_puts(s, "\nRGMII MAC counters (word index -> byte offset -> value)\n");
	zx279133_rgmii_stats(s, rgmii);
	return 0;
}

static int zx279133_rgmii_open(struct inode *inode, struct file *file)
{
	return single_open(file, zx279133_rgmii_show, file->private_data);
}

static char *zx279133_rgmii_next_token(char **p)
{
	char *tok;

	while (**p == ' ' || **p == '\t' || **p == '\n')
		(*p)++;
	if (!**p)
		return NULL;
	tok = *p;
	while (**p && **p != ' ' && **p != '\t' && **p != '\n')
		(*p)++;
	if (**p) {
		**p = '\0';
		(*p)++;
	}
	return tok;
}

static ssize_t zx279133_rgmii_write(struct file *file, const char __user *ubuf,
				     size_t count, loff_t *ppos)
{
	struct zx279133_rgmii *rgmii = file->private_data;
	char buf[256];
	char *p, *tok;
	u32 off, val;

	if (!rgmii)
		return -ENODEV;
	if (count >= sizeof(buf))
		return -EINVAL;
	if (copy_from_user(buf, ubuf, count))
		return -EFAULT;
	buf[count] = '\0';

	p = buf;
	tok = zx279133_rgmii_next_token(&p);
	if (!tok)
		return -EINVAL;

	if (!strcmp(tok, "r") || !strcmp(tok, "w")) {
		bool is_write = tok[0] == 'w';

		tok = zx279133_rgmii_next_token(&p);
		if (!tok || kstrtou32(tok, 0, &off))
			return -EINVAL;
		/*
		 * The window is device memory: an unaligned read is an
		 * alignment fault and it takes the kernel with it.  The
		 * NPPT driver's own console verb refuses an unaligned
		 * offset for the same reason (docs/92: round 133 read one
		 * at +0x40002 and the board panicked).
		 */
		if (off & 3)
			return -EINVAL;
		if (off >= ZX279133_RGMII_WINDOW_SIZE)
			return -EINVAL;
		if (is_write) {
			tok = zx279133_rgmii_next_token(&p);
			if (!tok || kstrtou32(tok, 0, &val))
				return -EINVAL;
			writel(val, rgmii->base + off);
		}
		val = zx279133_rgmii_read(rgmii, off);
		dev_info(rgmii->dev,
			 "rgmii reg 0x%08x (block +0x%05x) = 0x%08x\n",
			 ZX279133_RGMII_BASE + off, off, val);
		return count;
	}

	if (!strcmp(tok, "stats")) {
		zx279133_rgmii_stats_log(rgmii);
		return count;
	}

	if (!strcmp(tok, "dump")) {
		u32 i, words = 0x40;

		tok = zx279133_rgmii_next_token(&p);
		if (!tok || kstrtou32(tok, 0, &off))
			return -EINVAL;
		tok = zx279133_rgmii_next_token(&p);
		if (tok && kstrtou32(tok, 0, &words))
			return -EINVAL;
		if (off & 3)
			return -EINVAL;
		if (!words || words > 0x400)
			return -EINVAL;
		if (off + 4 * words > ZX279133_RGMII_WINDOW_SIZE)
			return -EINVAL;
		for (i = 0; i < words; i++) {
			u32 at = off + 4 * i;

			dev_info(rgmii->dev,
				 "rgmii reg 0x%08x (block +0x%05x) = 0x%08x\n",
				 ZX279133_RGMII_BASE + at, at,
				 zx279133_rgmii_read(rgmii, at));
		}
		return count;
	}

	return -EINVAL;
}

static const struct file_operations zx279133_rgmii_fops = {
	.owner	= THIS_MODULE,
	.open	= zx279133_rgmii_open,
	.write	= zx279133_rgmii_write,
	.llseek	= noop_llseek,
};

static void zx279133_rgmii_debugfs_remove(void *dentry)
{
	debugfs_remove(dentry);
}

static int zx279133_rgmii_probe(struct platform_device *pdev)
{
	struct zx279133_rgmii *rgmii;
	struct resource *res;
	int ret;

	rgmii = devm_kzalloc(&pdev->dev, sizeof(*rgmii), GFP_KERNEL);
	if (!rgmii)
		return -ENOMEM;
	rgmii->dev = &pdev->dev;

	res = platform_get_resource(pdev, IORESOURCE_MEM, 0);
	if (!res) {
		dev_err(&pdev->dev, "no register range\n");
		return -ENODEV;
	}

	rgmii->base = devm_ioremap_resource(&pdev->dev, res);
	if (IS_ERR(rgmii->base))
		return PTR_ERR(rgmii->base);
	/*
	 * devm_ioremap_resource() returns NULL - not an ERR_PTR - when the
	 * range could not be reserved, and IS_ERR() does not catch that.  The
	 * probe used to carry on with base == NULL, print the address it was
	 * going to use, and register the debug file; the first write through
	 * it then wrote to address 0 and the board panicked (win95, 2026-10-28
	 * 10:39: the "dump" verb oopsed on the very first use).  So the NULL
	 * is a failure here, and the reason is printed rather than guessed.
	 */
	if (!rgmii->base) {
		dev_err(&pdev->dev,
			"the register range at 0x%08x could not be reserved; not mapping it\n",
			ZX279133_RGMII_BASE);
		return -EBUSY;
	}

	platform_set_drvdata(pdev, rgmii);

	rgmii->debugfs = debugfs_create_file("zx279133-rgmii", 0600, NULL, rgmii,
					     &zx279133_rgmii_fops);
	if (IS_ERR(rgmii->debugfs))
		return PTR_ERR(rgmii->debugfs);
	ret = devm_add_action_or_reset(&pdev->dev, zx279133_rgmii_debugfs_remove,
				       rgmii->debugfs);
	if (ret)
		return ret;

	dev_info(&pdev->dev,
		 "the RGMII MAC's register block is mapped at 0x%08x; it reads all zero on this unit - the 2.5G jack is a port of the 9132 with the RTL8226 on it, not this block\n",
		 ZX279133_RGMII_BASE);

	/*
	 * The counters, once, in the kernel log: they are the only thing in
	 * this block that says whether it is alive, and the log survives a
	 * board that hangs a second later.
	 */
	zx279133_rgmii_stats_log(rgmii);

	return 0;
}

static const struct of_device_id zx279133_rgmii_of_match[] = {
	{ .compatible = "zte,zx279133-rgmii" },
	{ }
};
MODULE_DEVICE_TABLE(of, zx279133_rgmii_of_match);

static struct platform_driver zx279133_rgmii_driver = {
	.probe	= zx279133_rgmii_probe,
	.driver	= {
		.name		= "zx279133-rgmii",
		.of_match_table	= zx279133_rgmii_of_match,
	},
};
module_platform_driver(zx279133_rgmii_driver);

MODULE_DESCRIPTION("ZTE ZX279133 RGMII MAC register block (read-only probe)");
MODULE_LICENSE("GPL");
