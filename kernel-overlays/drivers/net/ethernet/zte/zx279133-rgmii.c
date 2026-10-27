// SPDX-License-Identifier: GPL-2.0-only
/*
 * zx279133-rgmii.c - the SoC's RGMII MAC register block (0x15900000).
 *
 * What this is for
 * ----------------
 *
 * The 2.5G jack on this board is served by a MAC that this tree does not
 * drive.  The SoC has three MACs: XMAC0 (0x140000, the lane to the 9132's
 * CPU port), XMAC1 (0x180000, the PON side, which this tree's eth0 is), and
 * the RGMII MAC (0x15900000), which the vendor's device tree declares as
 *
 *	rgmii@15900000 {
 *		compatible = "zte,zx279133-rgmii";
 *		reg = <0x00 0x15900000 0x00 0x100000>;
 *	};
 *
 * and which the vendor's np_133.ko binds.  The tree this CI builds against
 * (cnjn/linux-mainline-zte-zxslc-sr1010 at 96e5a241) has neither that node
 * nor a driver for it, so nothing in mainline can move a byte through the
 * 2.5G jack.
 *
 * That this block is the one behind the jack is not a guess: the board's
 * U-Boot is the vendor's, and at 2026-10-28 03:45:57 it downloaded
 * 12162040 bytes from the test PC through that jack - "tftpboot 0x89000000
 * sk-d840n-initramfs-run194.itb", "Bytes transferred = 12162040 (b993f8 hex)"
 * - and the test PC has exactly one NIC, which is plugged into the 2.5G
 * jack.  U-Boot has no driver for the 9132, so the MAC that moved those
 * bytes can only be this one.
 *
 * What this driver does
 * ---------------------
 *
 * Nothing to the hardware: it maps the window and makes it readable.  It
 * does not configure the block, does not claim an interrupt, and does not
 * create a netdev.  Writing the driver that does those is the next piece of
 * work, and it needs the register map first.
 *
 * The register map, so far, is what the vendor's own code says:
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
 * The names of those counters are not known.  What is known is that they are
 * the block's only traffic counters, so they are the probe that says whether
 * the block is alive at all: if they move while the 2.5G jack carries
 * traffic, this is the MAC behind that jack; if they stay at zero while the
 * jack is carrying traffic, it is not.
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
		 "the RGMII MAC's register block is mapped at 0x%08x; this driver only reads it - the 2.5G jack still has no MAC behind it in mainline\n",
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
