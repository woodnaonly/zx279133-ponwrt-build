// SPDX-License-Identifier: GPL-2.0-only

#include <linux/bitfield.h>
#include <linux/crc32.h>
#include <linux/debugfs.h>
#include <linux/delay.h>
#include <linux/device.h>
#include <linux/etherdevice.h>
#include <linux/firmware.h>
#include <linux/io.h>
#include <linux/iopoll.h>
#include <linux/mdio.h>
#include <linux/mii.h>
#include <linux/module.h>
#include <linux/of.h>
#include <linux/of_mdio.h>
#include <linux/regmap.h>
#include <linux/seq_file.h>
#include <linux/kstrtox.h>
#include <linux/unaligned.h>
#include <linux/uaccess.h>

#include "zx279133.h"
#include "zx279133-rx-hash.h"
#include "zx279133-rx-hash-ipv6.h"

static bool rx_hash = true;
module_param(rx_hash, bool, 0444);
MODULE_PARM_DESC(rx_hash, "Enable guarded RX flow hashing; the hook is installed only on the one PPU blob it was calibrated against");
static bool rx_hash_ipv6 = true;
module_param(rx_hash_ipv6, bool, 0444);
MODULE_PARM_DESC(rx_hash_ipv6, "Enable IPv6 tuple hashing with the calibrated RX hash programs");

void zx279133_route_set(struct zx279133_eth *eth, bool enabled)
{
	u32 value;

	value = readl(eth->base + ZX279133_ROUTE_CLK);
	writel(enabled ? value | ZX279133_ROUTE_CLK_EN :
			 value & ~ZX279133_ROUTE_CLK_EN,
	       eth->base + ZX279133_ROUTE_CLK);

	regmap_write(eth->pon_route, 0, enabled ? 0 : 1);

	value = readl(eth->base + ZX279133_ROUTE_CTRL);
	writel(enabled ? value & ~ZX279133_ROUTE_XMAC1 :
			 value | ZX279133_ROUTE_XMAC1,
	       eth->base + ZX279133_ROUTE_CTRL);
}

static const u32 zx279133_smct_offsets[] = {
	ZX279133_SMCT_SEL_CFG0,
	ZX279133_SMCT_SEL_CFG1,
	ZX279133_SMCT_CTRL,
	ZX279133_SMCT_COS_TH0,
	ZX279133_SMCT_COS_TH0 + 0x4,
	ZX279133_SMCT_COS_TH0 + 0x8,
	ZX279133_SMCT_COS_TH0 + 0xc,
	ZX279133_SMCT_INPORT_TH0,
	ZX279133_SMCT_INPORT_TH0 + 0x4,
	ZX279133_SMCT_INPORT_TH0 + 0x8,
	ZX279133_SMCT_INPORT_TH0 + 0xc,
	ZX279133_SMCT_INPORT_TH0 + 0x10,
	ZX279133_SMCT_INPORT_TH0 + 0x14,
	ZX279133_SMCT_INPORT_TH0 + 0x18,
	ZX279133_SMCT_INPORT_TH0 + 0x1c,
	ZX279133_SMCT_MC_TH,
	ZX279133_SMCT_NP1_TH,
	ZX279133_SMCT_IDM_SSCH_TH,
};

static const u32 zx279133_smct_cos_pairs[] = {
	ZX279133_SMCT_COS_TH_VALUE |
		ZX279133_SMCT_COS_TH_VALUE << ZX279133_SMCT_PAIR_HI_SHIFT,
	ZX279133_SMCT_COS_TH_VALUE |
		ZX279133_SMCT_COS_TH_VALUE << ZX279133_SMCT_PAIR_HI_SHIFT,
	ZX279133_SMCT_COS_TH_VALUE |
		ZX279133_SMCT_COS_TH_VALUE << ZX279133_SMCT_PAIR_HI_SHIFT,
	ZX279133_SMCT_COS_LAST_VALUE |
		ZX279133_SMCT_COS7_TH_VALUE << ZX279133_SMCT_PAIR_HI_SHIFT,
};

static const u32 zx279133_smct_inport_pairs[] = {
	ZX279133_SMCT_INPORT_TH_VALUE |
		ZX279133_SMCT_INPORT_TH_VALUE << ZX279133_SMCT_PAIR_HI_SHIFT,
	ZX279133_SMCT_INPORT_TH_VALUE |
		ZX279133_SMCT_INPORT_TH_VALUE << ZX279133_SMCT_PAIR_HI_SHIFT,
	ZX279133_SMCT_INPORT_TH_VALUE |
		ZX279133_SMCT_INPORT_TH_VALUE << ZX279133_SMCT_PAIR_HI_SHIFT,
	ZX279133_SMCT_INPORT_TH_VALUE |
		ZX279133_SMCT_INPORT_TH_VALUE << ZX279133_SMCT_PAIR_HI_SHIFT,
	ZX279133_SMCT_COS7_TH_VALUE |
		ZX279133_SMCT_INPORT_TH_VALUE << ZX279133_SMCT_PAIR_HI_SHIFT,
	ZX279133_SMCT_INPORT_TH_VALUE |
		ZX279133_SMCT_COS7_TH_VALUE << ZX279133_SMCT_PAIR_HI_SHIFT,
	ZX279133_SMCT_COS_LAST_VALUE |
		ZX279133_SMCT_COS_LAST_VALUE << ZX279133_SMCT_PAIR_HI_SHIFT,
	ZX279133_SMCT_COS_LAST_VALUE |
		ZX279133_SMCT_INPORT_TH_VALUE << ZX279133_SMCT_PAIR_HI_SHIFT,
};

static void zx279133_smct_prepare(struct zx279133_eth *eth)
{
	unsigned int i;

	for (i = 0; i < ARRAY_SIZE(zx279133_smct_offsets); i++)
		eth->smct_saved[i] = readl(eth->base + zx279133_smct_offsets[i]);

	writel(ZX279133_SMCT_SEL_CFG0_VALUE,
	       eth->base + ZX279133_SMCT_SEL_CFG0);
	writel(ZX279133_SMCT_SEL_CFG1_VALUE,
	       eth->base + ZX279133_SMCT_SEL_CFG1);
	writel(ZX279133_SMCT_CTRL_VALUE, eth->base + ZX279133_SMCT_CTRL);
	for (i = 0; i < ARRAY_SIZE(zx279133_smct_cos_pairs); i++)
		writel((eth->smct_saved[3 + i] &
			ZX279133_SMCT_PAIR_PRESERVE_MASK) |
		       zx279133_smct_cos_pairs[i],
		       eth->base + ZX279133_SMCT_COS_TH0 + 4 * i);
	for (i = 0; i < ARRAY_SIZE(zx279133_smct_inport_pairs); i++)
		writel((eth->smct_saved[7 + i] &
			ZX279133_SMCT_PAIR_PRESERVE_MASK) |
		       zx279133_smct_inport_pairs[i],
		       eth->base + ZX279133_SMCT_INPORT_TH0 + 4 * i);
	writel((eth->smct_saved[15] & ~ZX279133_SMCT_MC_TH_MASK) |
	       ZX279133_SMCT_MC_TH_VALUE,
	       eth->base + ZX279133_SMCT_MC_TH);
	writel(ZX279133_SMCT_NP1_TH_VALUE, eth->base + ZX279133_SMCT_NP1_TH);
	writel((eth->smct_saved[17] & GENMASK(31, 30)) |
	       ZX279133_SMCT_NP1_TH_VALUE |
	       ZX279133_SMCT_NP1_TH_VALUE << ZX279133_SMCT_PAIR_HI_SHIFT,
	       eth->base + ZX279133_SMCT_IDM_SSCH_TH);
	eth->smct_prepared = true;
}

static void zx279133_smct_restore(struct zx279133_eth *eth)
{
	unsigned int i;

	if (!eth->smct_prepared)
		return;

	for (i = 0; i < ARRAY_SIZE(zx279133_smct_offsets); i++)
		writel(eth->smct_saved[i], eth->base + zx279133_smct_offsets[i]);
	eth->smct_prepared = false;
}

static int
zx279133_ppu_inst_block(struct zx279133_eth *eth, u32 pc, const u32 *lo, const u32 *hi)
{
	u32 value;
	int ret;
	int i;

	ret = readl_poll_timeout_atomic(eth->pps_base +
					ZX279133_PPU_CLUSTER_RDY,
					value, value & BIT(0), 1, 1000);
	if (ret)
		return dev_err_probe(eth->dev, ret,
				     "PPU cluster not ready: %#x\n", value);
	for (i = 0; i < 4; i++) {
		writel(lo[i],
		       eth->pps_base + ZX279133_PPU_CLUSTER_INST_LO + 8 * i);
		writel((readl(eth->pps_base + ZX279133_PPU_CLUSTER_INST_HI +
			      8 * i) & ~ZX279133_PPU_CLUSTER_HI_MASK) | hi[i],
		       eth->pps_base + ZX279133_PPU_CLUSTER_INST_HI + 8 * i);
	}
	writel((readl(eth->pps_base + ZX279133_PPU_CLUSTER_PC) &
		~ZX279133_PPU_CLUSTER_PC_MASK) | pc >> 2,
	       eth->pps_base + ZX279133_PPU_CLUSTER_PC);
	ret = readl_poll_timeout_atomic(eth->pps_base +
					ZX279133_PPU_CLUSTER_PC,
					value, value & BIT(0), 1, 1000);
	if (ret)
		return dev_err_probe(eth->dev, ret,
				     "PPU instruction block timeout: %#x\n",
				     value);

	return 0;
}

/*
 * CPU133's factory PPU image assigns every fourth flow to ME0.  The reset
 * value leaves the cluster with no executable ME for the CPU-TX flow, which
 * is reported later as a DMA drop.  This is the runtime table used by
 * ppu_cluster_set_me_val_by_flow_num(), not PPU microcode.
 */
static void zx279133_ppu_cluster_runtime_prepare(struct zx279133_eth *eth)
{
	unsigned int i;

	for (i = 0; i < ZX279133_PPU_CLUSTER_ME_WORDS; i++)
		eth->ppu_cluster_me_saved[i] =
			readl(eth->pps_base + ZX279133_PPU_CLUSTER_ME_TABLE + 4 * i);
	eth->ppu_cluster_pkt_type_saved =
		readl(eth->pps_base + ZX279133_PPU_CLUSTER_PKT_TYPE_DETAIL);

	for (i = 0; i < ZX279133_PPU_CLUSTER_ME_WORDS; i++)
		writel(0x00000001,
		       eth->pps_base + ZX279133_PPU_CLUSTER_ME_TABLE + 4 * i);
	writel(1, eth->pps_base + ZX279133_PPU_CLUSTER_PKT_TYPE_DETAIL);
	eth->ppu_cluster_runtime_prepared = true;
}

static void zx279133_ppu_cluster_runtime_restore(struct zx279133_eth *eth)
{
	unsigned int i;

	if (!eth->ppu_cluster_runtime_prepared)
		return;

	for (i = 0; i < ZX279133_PPU_CLUSTER_ME_WORDS; i++)
		writel(eth->ppu_cluster_me_saved[i],
		       eth->pps_base + ZX279133_PPU_CLUSTER_ME_TABLE + 4 * i);
	writel(eth->ppu_cluster_pkt_type_saved,
	       eth->pps_base + ZX279133_PPU_CLUSTER_PKT_TYPE_DETAIL);
	eth->ppu_cluster_runtime_prepared = false;
}

static int __zx279133_spa_indirect_write(struct zx279133_eth *eth, u32 mem_id,
					 u32 addr, u32 value)
{
	u32 gate, status;
	int ret;

	gate = readl(eth->base + ZX279133_SPA_AUTO_GATE);
	if (gate & ZX279133_SPA_AUTO_GATE_BIT)
		writel(gate & ~ZX279133_SPA_AUTO_GATE_BIT,
		       eth->base + ZX279133_SPA_AUTO_GATE);

	ret = readl_poll_timeout_atomic(eth->base + ZX279133_SPA_IND_STATUS,
					status, status & BIT(0), 1, 1000);
	if (ret) {
		dev_err_probe(eth->dev, ret,
			      "SPA indirect access not done: %#x\n", status);
		goto out;
	}
	writel((readl(eth->base + ZX279133_SPA_IND_CMD) &
		~ZX279133_SPA_IND_CMD_MASK) |
	       mem_id << 20 | addr,
	       eth->base + ZX279133_SPA_IND_CMD);
	writel(value, eth->base + ZX279133_SPA_IND_DATA);

out:
	if (gate & ZX279133_SPA_AUTO_GATE_BIT)
		writel(gate, eth->base + ZX279133_SPA_AUTO_GATE);
	return ret;
}

static int zx279133_spa_indirect_write(struct zx279133_eth *eth, u32 mem_id,
				       u32 addr, u32 value)
{
	int ret;

	mutex_lock(&eth->np_ind_lock);
	ret = __zx279133_spa_indirect_write(eth, mem_id, addr, value);
	mutex_unlock(&eth->np_ind_lock);

	return ret;
}

static int __zx279133_spa_indirect_read(struct zx279133_eth *eth, u32 mem_id,
					u32 addr, u32 *value)
{
	u32 gate, status;
	int ret;

	gate = readl(eth->base + ZX279133_SPA_AUTO_GATE);
	if (gate & ZX279133_SPA_AUTO_GATE_BIT)
		writel(gate & ~ZX279133_SPA_AUTO_GATE_BIT,
		       eth->base + ZX279133_SPA_AUTO_GATE);

	ret = readl_poll_timeout_atomic(eth->base + ZX279133_SPA_IND_STATUS,
					status, status & BIT(0), 1, 1000);
	if (ret) {
		dev_err_probe(eth->dev, ret,
			      "SPA indirect access not done: %#x\n", status);
		goto out;
	}
	writel((readl(eth->base + ZX279133_SPA_IND_CMD) &
		~ZX279133_SPA_IND_CMD_MASK) |
	       ZX279133_SPA_IND_READ | mem_id << 20 | addr,
	       eth->base + ZX279133_SPA_IND_CMD);
	ret = readl_poll_timeout_atomic(eth->base + ZX279133_SPA_IND_STATUS,
					status, status & BIT(0), 1, 1000);
	if (ret) {
		dev_err_probe(eth->dev, ret,
			      "SPA indirect read not done: %#x\n", status);
		goto out;
	}
	*value = readl(eth->base + ZX279133_SPA_IND_DATA);

out:
	if (gate & ZX279133_SPA_AUTO_GATE_BIT)
		writel(gate, eth->base + ZX279133_SPA_AUTO_GATE);
	return ret;
}

static int zx279133_spa_indirect_read(struct zx279133_eth *eth, u32 mem_id,
				      u32 addr, u32 *value)
{
	int ret;

	mutex_lock(&eth->np_ind_lock);
	ret = __zx279133_spa_indirect_read(eth, mem_id, addr, value);
	mutex_unlock(&eth->np_ind_lock);

	return ret;
}

/*
 * SE SMMU0 indirect RAM access, recovered from np.ko se_smmu0_read()/
 * se_smmu0_write(): address, four data words and command live in the PPS
 * block; every transfer first polls the RAM-done status, and the SE i-key
 * auto-gate clock is quiesced around the access when enabled, exactly
 * like the vendor driver does.
 */
static int zx279133_smmu0_wait(struct zx279133_eth *eth)
{
	u32 status;

	return readl_poll_timeout_atomic(eth->pps_base +
					 ZX279133_SMMU0_RAM_DONE,
					 status, status & ZX279133_SMMU0_RAM_MASK,
					 1, 1000);
}

static int __zx279133_smmu0_read(struct zx279133_eth *eth, u32 addr_ind,
				 u32 *data, u32 words, u32 cmd)
{
	u32 agclk = readl(eth->pps_base + ZX279133_PPU_AGCLK_CFG) &
		    ZX279133_AGCLK_VALUE_MASK;
	u32 reg;
	int ret;
	int i;

	if (agclk == 1)
		writel(readl(eth->pps_base + ZX279133_PPU_AGCLK_CFG) &
		       ~BIT(0), eth->pps_base + ZX279133_PPU_AGCLK_CFG);

	ret = zx279133_smmu0_wait(eth);
	if (ret)
		goto out;

	reg = readl(eth->pps_base + ZX279133_SMMU0_ADDR);
	writel((addr_ind & ZX279133_SMMU0_ADDR_MASK) |
	       (reg & ~ZX279133_SMMU0_ADDR_MASK),
	       eth->pps_base + ZX279133_SMMU0_ADDR);
	reg = readl(eth->pps_base + ZX279133_SMMU0_CMD);
	writel((cmd & ZX279133_SMMU0_CMD_MASK) |
	       (reg & ~ZX279133_SMMU0_CMD_MASK),
	       eth->pps_base + ZX279133_SMMU0_CMD);
	ret = zx279133_smmu0_wait(eth);
	if (ret)
		goto out;
	for (i = 0; i < ZX279133_SMMU0_WORDS; i++)
		data[i] = readl(eth->pps_base + ZX279133_SMMU0_RDATA +
				4 * (ZX279133_SMMU0_WORDS - 1 - i));

out:
	if (agclk == 1)
		writel(readl(eth->pps_base + ZX279133_PPU_AGCLK_CFG) | BIT(0),
		       eth->pps_base + ZX279133_PPU_AGCLK_CFG);

	return ret;
}

static int zx279133_smmu0_read(struct zx279133_eth *eth, u32 addr_ind,
			       u32 *data, u32 words, u32 cmd)
{
	int ret;

	mutex_lock(&eth->np_ind_lock);
	ret = __zx279133_smmu0_read(eth, addr_ind, data, words, cmd);
	mutex_unlock(&eth->np_ind_lock);

	return ret;
}

static int __zx279133_smmu0_write(struct zx279133_eth *eth, u32 addr_ind,
				  const u32 *data, u32 words, u32 cmd)
{
	u32 agclk = readl(eth->pps_base + ZX279133_PPU_AGCLK_CFG) &
		    ZX279133_AGCLK_VALUE_MASK;
	u32 reg;
	int ret;
	int i;

	if (agclk == 1)
		writel(readl(eth->pps_base + ZX279133_PPU_AGCLK_CFG) &
		       ~BIT(0), eth->pps_base + ZX279133_PPU_AGCLK_CFG);

	ret = zx279133_smmu0_wait(eth);
	if (ret)
		goto out;

	reg = readl(eth->pps_base + ZX279133_SMMU0_ADDR);
	writel((addr_ind & ZX279133_SMMU0_ADDR_MASK) |
	       (reg & ~ZX279133_SMMU0_ADDR_MASK),
	       eth->pps_base + ZX279133_SMMU0_ADDR);
	for (i = words - 1; i >= 0; i--)
		writel(data[i], eth->pps_base + ZX279133_SMMU0_WDATA + 4 * i);
	reg = readl(eth->pps_base + ZX279133_SMMU0_CMD);
	writel((cmd & ZX279133_SMMU0_CMD_MASK) |
	       (reg & ~ZX279133_SMMU0_CMD_MASK),
	       eth->pps_base + ZX279133_SMMU0_CMD);

out:
	if (agclk == 1)
		writel(readl(eth->pps_base + ZX279133_PPU_AGCLK_CFG) | BIT(0),
		       eth->pps_base + ZX279133_PPU_AGCLK_CFG);

	return ret;
}

static int zx279133_smmu0_write(struct zx279133_eth *eth, u32 addr_ind,
				const u32 *data, u32 words, u32 cmd)
{
	int ret;

	mutex_lock(&eth->np_ind_lock);
	ret = __zx279133_smmu0_write(eth, addr_ind, data, words, cmd);
	mutex_unlock(&eth->np_ind_lock);

	return ret;
}

/*
 * Where the ERAM tables this driver uses actually are.
 *
 * The SE SDT tables are created here, out of one ERAM pool, with a strictly
 * sequential allocator that starts at block 12288: each ERAM table takes
 * the blocks that are left and the next one starts where the last ended.
 * The base of a table is therefore not a constant anybody can write down -
 * it is the sum of the depths of the tables before it, and it moves the
 * moment the list moves.
 *
 * The driver had three such constants written down, and none of them was
 * the base the driver itself gave the hardware: 17106 for the MF extension
 * table, 21266 for the protocol table and 23026 for the port type table.
 * They came from a different SDT list - the one the generic np.ko has,
 * which has a table this driver's list does not have - so every one of
 * those writes landed in some other table's blocks, and the hardware read
 * the table it was told about and found nothing in it.
 *
 * So the bases are taken from the allocation that told the hardware, and
 * they are looked up by the table's id.
 */
#define ZX279133_SDT_ID_MAX	0x100

/*
 * What the driver told the hardware about one ERAM table: where it starts
 * and how wide one of its entries is.  The width matters as much as the
 * base - the vendor's se_eram_table_set_by_sdt_info() places an entry at
 * (base << 7) + (width * index & ~0x7f) and merges the value into the
 * 16-byte window bit by bit, so a table declared with the wrong width is
 * read at the wrong place.
 */
struct zx279133_eram_table {
	u32 base;
	u32 bit_len;
};

static struct zx279133_eram_table zx279133_eram[ZX279133_SDT_ID_MAX];

static u32 zx279133_eram_base_of(u32 sdt_id)
{
	if (sdt_id >= ZX279133_SDT_ID_MAX)
		return 0;
	return zx279133_eram[sdt_id].base;
}

static u32 zx279133_eram_bit_len_of(u32 sdt_id)
{
	if (sdt_id >= ZX279133_SDT_ID_MAX)
		return 0;
	return zx279133_eram[sdt_id].bit_len;
}

/*
 * The vendor's se_eram_table_set_by_sdt_info() for a table whose entries are
 * narrower than a word: the entry is not written whole, it is merged into
 * the 16-byte window it lives in, bit by bit.  The window is read, the bits
 * of the entry are replaced, and the window is written back.
 *
 * The window is the 128-bit block the entry falls in, so the address of the
 * window is (base << 7) + (width * index & ~0x7f) and the entry sits in
 * word (width * index >> 5) & 3 at bit (width * index) & 0x1f of it.
 */
static int zx279133_eram_table_set_bits(struct zx279133_eth *eth, u32 sdt_id,
					u32 index, u32 width, u32 value)
{
	u32 base = zx279133_eram_base_of(sdt_id);
	u32 bit_len = zx279133_eram_bit_len_of(sdt_id);
	u32 bit_off, addr, word_idx, bit_pos, mask;
	u32 window[ZX279133_SMMU0_WORDS];
	int ret;

	if (!base || !bit_len)
		return -EINVAL;
	if (width > bit_len || width > 32)
		return -EINVAL;

	bit_off = bit_len * index;
	addr = (base << 7) + (bit_off & ~0x7fu);
	word_idx = (bit_off >> 5) & (ZX279133_SMMU0_WORDS - 1);
	bit_pos = bit_off & 0x1f;

	ret = zx279133_smmu0_read(eth, addr, window, ZX279133_SMMU0_WORDS,
				  ZX279133_SMMU0_CMD_READ);
	if (ret)
		return ret;

	mask = (width >= 32) ? 0xffffffffu : ((1u << width) - 1);
	window[word_idx] = (window[word_idx] & ~(mask << bit_pos)) |
			   ((value & mask) << bit_pos);

	return zx279133_smmu0_write(eth, addr, window, ZX279133_SMMU0_WORDS,
				     ZX279133_SMMU0_CMD_WRITE);
}

/*
 * The same table read back, one entry.
 */
static int zx279133_eram_table_get_bits(struct zx279133_eth *eth, u32 sdt_id,
					u32 index, u32 width, u32 *value)
{
	u32 base = zx279133_eram_base_of(sdt_id);
	u32 bit_len = zx279133_eram_bit_len_of(sdt_id);
	u32 bit_off, addr, word_idx, bit_pos, mask;
	u32 window[ZX279133_SMMU0_WORDS];
	int ret;

	if (!base || !bit_len)
		return -EINVAL;
	if (width > bit_len || width > 32)
		return -EINVAL;

	bit_off = bit_len * index;
	addr = (base << 7) + (bit_off & ~0x7fu);
	word_idx = (bit_off >> 5) & (ZX279133_SMMU0_WORDS - 1);
	bit_pos = bit_off & 0x1f;

	ret = zx279133_smmu0_read(eth, addr, window, ZX279133_SMMU0_WORDS,
				  ZX279133_SMMU0_CMD_READ);
	if (ret)
		return ret;

	mask = (width >= 32) ? 0xffffffffu : ((1u << width) - 1);
	*value = (window[word_idx] >> bit_pos) & mask;

	return 0;
}

/*
 * Patch one field of one word of the 128-bit window an entry lives in.
 *
 * The vendor's own helpers work exactly this way: they read the entry as
 * four words, change one field of it, and write the four words back.
 * se_eram_table_get(0x7d, mem_id) reads the block at
 * (base << 7) + (128 * mem_id) and port_attr_def_brd_flood_en_set() patches
 * byte 3 of it; glb_cfg_table_cpu_base_qid_set() reads entry 0 of table
 * 0x22 and patches bits 1..9 of the halfword at byte 14; and
 * tm_reason_map_cpu_qid_set() reads entry 0 of table 0x19 and patches bits
 * 0..4 of byte 3.
 *
 * The word to patch is named, not derived, because it depends on the
 * table: a table whose entries are 32 bits wide keeps four entries in one
 * window, so entry 0 is word 0 of it, while a table whose entries are 128
 * bits wide keeps one entry in a window and the field can be in any word.
 */
static int zx279133_eram_window_patch(struct zx279133_eth *eth, u32 sdt_id,
				       u32 index, u32 word, u32 mask,
				       u32 value)
{
	u32 base = zx279133_eram_base_of(sdt_id);
	u32 bit_len = zx279133_eram_bit_len_of(sdt_id);
	u32 addr, window[ZX279133_SMMU0_WORDS];
	int ret;

	if (!base || !bit_len)
		return -EINVAL;
	if (word >= ZX279133_SMMU0_WORDS)
		return -EINVAL;

	addr = (base << 7) + ((bit_len * index) & ~0x7fu);

	ret = zx279133_smmu0_read(eth, addr, window, ZX279133_SMMU0_WORDS,
				  ZX279133_SMMU0_CMD_READ);
	if (ret)
		return ret;

	window[word] = (window[word] & ~mask) | (value & mask);

	return zx279133_smmu0_write(eth, addr, window, ZX279133_SMMU0_WORDS,
				     ZX279133_SMMU0_CMD_WRITE);
}

/*
 * SPA port-attribute updates recovered from np.ko, entry 71 (memory
 * address 19), byte 2: mf_port_iswanport_set(0, 1) sets bit 1 on mem_id
 * 0 (companion 14) at np_nppu_init, and mf_port_ppuinit_set() sets bit 2
 * on the mem ids the vendor configures at np_init_done, including CPU
 * injection memory 15 (companion 10 for mem_id 5). Every update is
 * mirrored into the MF extension table, SE ERAM table 0x7d: 64 entries of
 * 128 bits keyed by SPA memory id, the first word
 * carrying the port-attribute word the SPA multi-forward engine consults
 * to accept CPU-originated frames for a port.
 */
#define ZX279133_MF_ERAM_SDT_ID	0x7d
#define ZX279133_VLAN_ERAM_SDT_ID	0x00
#define ZX279133_PRO_ERAM_SDT_ID	0x04
#define ZX279133_PORT_TYPE_ERAM_SDT_ID	0x06
#define ZX279133_SWITCH_PROTO_SDT_ID	0x18
/*
 * The three tables the CPU path runs on.  They are named here rather than
 * next to the code that fills them in, because the debugfs console below
 * reaches them as well.
 *
 *	0x19  the reason map: the CPU queue a frame with a given reason is
 *	      delivered on.  32 bits an entry.
 *	0x22  the global config table: the CPU base queue id lives in bits
 *	      17..25 of word 3 of entry 0.  128 bits an entry.
 *	0x27  the outport map: the CPU's own outport 15 goes to the real
 *	      port 15 and to a queue id.  16 bits an entry.
 */
#define ZX279133_QID_ERAM_SDT_ID	0x19
#define ZX279133_GLBCFG_ERAM_SDT_ID	0x22
#define ZX279133_OUTPORT_ERAM_SDT_ID	0x27
/*
 * The flood group table: one entry per group, one destination port id per
 * entry.  The vendor's group_id_table_port_id_set() writes it with
 * se_eram_table_set(0x1f, group_id, 1, &port_id), so the index IS the group
 * id and the entry is a single 32-bit word.
 */
#define ZX279133_FLOOD_ERAM_SDT_ID	0x1f

static const u32 zx279133_vlan0_entry[ZX279133_SMMU0_WORDS] = {
	0xffc00000, 0x0000ffff, 0xffffffff, 0x00003003,
};

/*
 * np_ppu_init() tail recovered from np.ko: the i-key response FIFO almost
 * full config, the global L2 MTU (SE ERAM sdt 1) and the per-wanid L3 MTU
 * (SE ERAM sdt 2) settings.
 */

static void zx279133_spa_onu_mac_write(struct zx279133_eth *eth,
				       unsigned int slot, const u8 *addr)
{
	void __iomem *lo = eth->base + ZX279133_SPA_ONU_MAC_LO +
			   ZX279133_SPA_ONU_STRIDE * slot;

	writel(addr ? get_unaligned_be32(addr + 2) : ZX279133_SPA_MAC_HI_VALUE,
	       eth->base + ZX279133_SPA_ONU_MAC_HI +
	       ZX279133_SPA_ONU_STRIDE * slot);
	writel((readl(lo) & ~ZX279133_SPA_MAC_LO_MASK) |
	       (addr ? get_unaligned_be16(addr) : ZX279133_SPA_MAC_LO_VALUE),
	       lo);
}

void zx279133_program_spa_cpu_mac(struct zx279133_eth *eth, const u8 *addr)
{
	u8 cpu_mac[ETH_ALEN];
	unsigned int i;

	ether_addr_copy(cpu_mac, addr);
	for (i = 0; i < ZX279133_WANID_CPU_MAC_COUNT; i++) {
		zx279133_spa_onu_mac_write(eth, i, cpu_mac);
		eth_addr_inc(cpu_mac);
	}
}

/*
 * The WAN ingress program forwards a frame in hardware only when its
 * destination is one of the ONU MAC slots; anything else goes to the CPU.
 * Slots 0 and 1 hold the WAN address. A macvlan on the WAN, such as one
 * PPPoE session of several each with its own MAC, puts its address on the
 * unicast list of the WAN device: give those the remaining slots, so that
 * their offloaded flows are forwarded downstream too. Caller holds
 * datapath_lock, with the hardware prepared.
 */
void zx279133_program_spa_uc_macs(struct zx279133_eth *eth)
{
	u8 addrs[ZX279133_SPA_ONU_MAC_SLOTS - ZX279133_WANID_CPU_MAC_COUNT][ETH_ALEN];
	struct net_device *ndev = eth->ndev;
	struct netdev_hw_addr *ha;
	unsigned int i, n = 0;

	netif_addr_lock_bh(ndev);
	netdev_for_each_uc_addr(ha, ndev) {
		if (n == ARRAY_SIZE(addrs)) {
			netdev_warn(ndev,
				    "%u unicast addresses, only %zu forwarded in hardware\n",
				    netdev_uc_count(ndev), ARRAY_SIZE(addrs));
			break;
		}
		ether_addr_copy(addrs[n++], ha->addr);
	}
	netif_addr_unlock_bh(ndev);

	for (i = 0; i < ARRAY_SIZE(addrs); i++)
		zx279133_spa_onu_mac_write(eth, ZX279133_WANID_CPU_MAC_COUNT + i,
					   i < n ? addrs[i] : NULL);
}

static int __zx279133_program_one_wanid_cpu_mac(struct zx279133_eth *eth,
						unsigned int wanid,
						const u8 *addr)
{
	struct zx279133_wanid_slot *slot = &eth->wanid[wanid];
	u32 data[4];
	int ret;

	/* Flows own the MAC of a lent entry; change what it returns to. */
	if (slot->users) {
		slot->saved[2] = get_unaligned_be32(addr + 2);
		slot->saved[3] = (slot->saved[3] & ~GENMASK(15, 0)) |
				 get_unaligned_be16(addr);
		return 0;
	}
	ret = zx279133_smmu0_read(eth, (16386 + wanid) << 7, data, 4,
				  ZX279133_SMMU0_CMD_READ);
	if (ret)
		return ret;
	data[2] = get_unaligned_be32(addr + 2);
	data[3] = (data[3] & ~GENMASK(15, 0)) | get_unaligned_be16(addr);
	return zx279133_smmu0_write(eth, (16386 + wanid) << 7, data, 4,
				    ZX279133_SMMU0_CMD_WRITE);
}

static int zx279133_program_one_wanid_cpu_mac(struct zx279133_eth *eth,
					      unsigned int wanid,
					      const u8 *addr)
{
	int ret;

	mutex_lock(&eth->wanid_lock);
	ret = __zx279133_program_one_wanid_cpu_mac(eth, wanid, addr);
	mutex_unlock(&eth->wanid_lock);

	return ret;
}

int zx279133_program_wanid_cpu_mac(struct zx279133_eth *eth, const u8 *addr)
{
	u8 cpu_mac[ETH_ALEN];
	int i, ret;

	ether_addr_copy(cpu_mac, addr);
	for (i = 0; i < ZX279133_WANID_CPU_MAC_COUNT; i++) {
		ret = zx279133_program_one_wanid_cpu_mac(eth, i, cpu_mac);
		if (ret)
			return ret;
		eth_addr_inc(cpu_mac);
	}

	/* WAN-to-LAN routes select WANID16. */
	return zx279133_program_one_wanid_cpu_mac(eth, 16, addr);
}

#define ZX279133_WANID_PPPOE_SID	GENMASK(31, 16)
#define ZX279133_WANID_PPPOE_MODE	GENMASK(27, 26)

static int zx279133_wanid_write(struct zx279133_eth *eth, unsigned int wanid,
				const struct zx279133_wanid_profile *profile,
				const u32 *base)
{
	u32 data[ZX279133_SMMU0_WORDS];

	memcpy(data, base, sizeof(data));
	data[0] &= ~ZX279133_WANID_PPPOE_SID;
	data[0] |= FIELD_PREP(ZX279133_WANID_PPPOE_SID, profile->sid);
	if (profile->sip)
		data[1] = profile->sip;
	data[2] = get_unaligned_be32(profile->mac + 2);
	data[3] &= ~(ZX279133_WANID_PPPOE_MODE | GENMASK(15, 0));
	data[3] |= FIELD_PREP(ZX279133_WANID_PPPOE_MODE, profile->pppoe) |
		   get_unaligned_be16(profile->mac);

	return zx279133_smmu0_write(eth, (16386 + wanid) << 7, data,
				    ARRAY_SIZE(data), ZX279133_SMMU0_CMD_WRITE);
}

/*
 * A flow may share an entry whose MAC and PPPoE rewrite match its own. The
 * source address matters only to flows that source-NAT, so a flow that does
 * not ignores it, and an entry nobody source-NATs through takes a new one.
 */
static bool zx279133_wanid_fits(const struct zx279133_wanid_slot *slot,
				const struct zx279133_wanid_profile *profile)
{
	return ether_addr_equal(slot->profile.mac, profile->mac) &&
	       slot->profile.pppoe == profile->pppoe &&
	       slot->profile.sid == profile->sid &&
	       (!profile->sip || !slot->snat_users ||
		slot->profile.sip == profile->sip);
}

/*
 * Lend a flow the WANID entry for its profile. Upstream flows take entries
 * 0 to 15 and downstream flows 16 to 31, as np.ko's index | direction << 4
 * does; the lowest free entry is taken, so one profile per direction keeps
 * using WANID 0 and 16.
 */
int zx279133_wanid_get(struct zx279133_eth *eth, bool downstream,
		       const struct zx279133_wanid_profile *profile, u8 *wanid)
{
	unsigned int first = downstream ? ZX279133_WANID_DOWNSTREAM : 0;
	unsigned int i, free = ZX279133_WANID_COUNT;
	struct zx279133_wanid_slot *slot;
	int ret = 0;

	if (profile->pppoe > 3)
		return -EINVAL;

	mutex_lock(&eth->wanid_lock);
	for (i = first; i < first + ZX279133_WANID_DOWNSTREAM; i++) {
		slot = &eth->wanid[i];
		if (!slot->users) {
			if (free == ZX279133_WANID_COUNT)
				free = i;
			continue;
		}
		if (zx279133_wanid_fits(slot, profile))
			break;
	}

	if (i < first + ZX279133_WANID_DOWNSTREAM) {
		if (profile->sip && slot->profile.sip != profile->sip) {
			struct zx279133_wanid_profile update = slot->profile;

			update.sip = profile->sip;
			ret = zx279133_wanid_write(eth, i, &update, slot->saved);
			if (ret)
				goto out_unlock;
			slot->profile.sip = profile->sip;
		}
	} else {
		if (free == ZX279133_WANID_COUNT) {
			ret = -ENOSPC;
			goto out_unlock;
		}
		i = free;
		slot = &eth->wanid[i];
		ret = zx279133_smmu0_read(eth, (16386 + i) << 7, slot->saved,
					  ARRAY_SIZE(slot->saved),
					  ZX279133_SMMU0_CMD_READ);
		if (ret)
			goto out_unlock;
		ret = zx279133_wanid_write(eth, i, profile, slot->saved);
		if (ret)
			goto out_unlock;
		slot->profile = *profile;
	}

	slot->users++;
	if (profile->sip)
		slot->snat_users++;
	*wanid = i;

out_unlock:
	mutex_unlock(&eth->wanid_lock);
	return ret;
}

void zx279133_wanid_put(struct zx279133_eth *eth, u8 wanid, bool snat)
{
	struct zx279133_wanid_slot *slot = &eth->wanid[wanid];

	mutex_lock(&eth->wanid_lock);
	if (WARN_ON(!slot->users || (snat && !slot->snat_users)))
		goto out_unlock;
	if (snat)
		slot->snat_users--;
	if (--slot->users)
		goto out_unlock;
	if (zx279133_smmu0_write(eth, (16386 + wanid) << 7, slot->saved,
				 ARRAY_SIZE(slot->saved),
				 ZX279133_SMMU0_CMD_WRITE))
		dev_warn(eth->dev, "failed to restore WANID %u\n", wanid);
	memset(&slot->profile, 0, sizeof(slot->profile));

out_unlock:
	mutex_unlock(&eth->wanid_lock);
}

#ifdef CONFIG_DEBUG_FS
/* One line per WANID: the hardware entry decoded, then who borrows it. */
static int zx279133_wanid_show(struct seq_file *s, void *unused)
{
	struct zx279133_eth *eth = s->private;
	u32 data[ZX279133_SMMU0_WORDS];
	unsigned int i;
	__be32 sip;
	u8 mac[ETH_ALEN];
	int ret = 0;

	mutex_lock(&eth->datapath_lock);
	if (!READ_ONCE(eth->hardware_prepared)) {
		seq_puts(s, "hardware not prepared\n");
		goto out_unlock;
	}
	mutex_lock(&eth->wanid_lock);
	for (i = 0; i < ZX279133_WANID_COUNT; i++) {
		const struct zx279133_wanid_slot *slot = &eth->wanid[i];

		ret = zx279133_smmu0_read(eth, (16386 + i) << 7, data,
					  ARRAY_SIZE(data),
					  ZX279133_SMMU0_CMD_READ);
		if (ret)
			break;
		put_unaligned_be16(data[3], mac);
		put_unaligned_be32(data[2], mac + 2);
		sip = cpu_to_be32(data[1]);
		seq_printf(s, "%2u %08x %08x %08x %08x mtu %lu sid %lu sip %pI4 mac %pM pppoe %lu users %u snat %u\n",
			   i, data[0], data[1], data[2], data[3],
			   data[0] & GENMASK(15, 0),
			   FIELD_GET(ZX279133_WANID_PPPOE_SID, data[0]),
			   &sip, mac,
			   FIELD_GET(ZX279133_WANID_PPPOE_MODE, data[3]),
			   slot->users, slot->snat_users);
	}
	mutex_unlock(&eth->wanid_lock);
out_unlock:
	mutex_unlock(&eth->datapath_lock);
	return ret;
}
DEFINE_SHOW_ATTRIBUTE(zx279133_wanid);

static void zx279133_debugfs_remove(void *data)
{
	debugfs_remove(data);
}

int zx279133_wanid_debugfs_init(struct zx279133_eth *eth)
{
	struct dentry *dentry;

	dentry = debugfs_create_file("zx279133-wanid", 0400, NULL, eth,
				     &zx279133_wanid_fops);
	return devm_add_action_or_reset(eth->dev,
					zx279133_debugfs_remove, dentry);
}

/*
 * The 9132's four internal PHYs, and the bus they are on.
 *
 * The addresses are not a guess.  The running vendor system's device tree
 * carries a `uni-phy` property on both of the SoC's MDIO controllers and it
 * is a u32 array:
 *
 *	{ 0x0a, 0x0b, 0x0c, 0x0d, 0x05, 0x1c, 0x02 }
 *
 * 0x0a..0x0d are the four internal UNI PHYs; 0x05 is the RTL8226 on jack 4,
 * which mainline's board DTS already declares at mdio1 address 5.
 */
#define ZX279133_9132_UNI_PHY_FIRST	0x0a
#define ZX279133_9132_UNI_PHY_LAST	0x0d

/* Defined further down, next to the PHY enable code that owns the bus. */
static struct mii_bus *zx279133_9132_uni_mdio_bus(struct zx279133_eth *eth);

/*
 * A console for the 9132's SPA indirect window.
 *
 * Why this exists: the 9132's forwarding is decided by words in a per-port attribute
 * table, and until now nothing in mainline could reach that table at runtime.  Every
 * question about the switch - does a flood reach the conduit, does the DSA tagger's
 * transport VID survive the trip, is the port learning at all - therefore cost a
 * 70-minute CI build, and the answers cannot be guessed: the vendor's own 4.19 system
 * programs a very specific set of values there and its own tm_debug interface can be
 * used to read them back, but mainline had no way to try anything.
 *
 * The interface is textual and writable, because the point is to try configurations
 * rather than to observe one:
 *
 *	cat  /sys/kernel/debug/zx279133-9132		# dump the port words and the PHYs
 *	echo "r 1 19" > .../zx279133-9132		# read one word
 *	echo "w 1 19 0x96040001" > .../zx279133-9132	# write one word, read it back
 *	echo "m 0b 04" > .../zx279133-9132		# read one PHY register
 *	echo "mw 0b 00 1040" > .../zx279133-9132	# write one, read it back
 *
 * "mem" is the SPA memory id and "addr" is the word inside that port's twenty-word
 * attribute table.  The memory ids are the ones the bootloader itself prints:
 *
 *	0 = PON0, 1..4 = the four UNI jacks, 5 = XMAC0 (the conduit the DSA CPU
 *	port talks to), 6 = XMAC1, 7 = RGMII1, 15 = the CPU trap port.
 *
 * The "m" and "mw" commands are the PHYs, and they are a different address
 * space: "addr" there is the MDIO address on the SoC's MDIO controller #0
 * (0x0a..0x0d are the four internal PHYs) and "reg" is the register inside
 * the PHY.  They are here because the same question - what does this chip
 * actually answer - is the question a PHY driver for it has to be written
 * against, and the answers are worth having in the same console.
 */
#define ZX279133_9132_DUMP_MEM_COUNT	20
static const u32 zx279133_9132_dump_mems[ZX279133_9132_DUMP_MEM_COUNT] = {
	0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19,
};
#define ZX279133_9132_DUMP_ADDR_COUNT	9
static const u32 zx279133_9132_dump_addrs[ZX279133_9132_DUMP_ADDR_COUNT] = {
	0, 1, 2, 9, 10, 13, 17, 18, 19,
};

static int zx279133_9132_show(struct seq_file *s, void *unused)
{
	struct zx279133_eth *eth = s->private;
	struct mii_bus *bus;
	unsigned int m, a;

	seq_puts(s, "mem   ");
	for (a = 0; a < ZX279133_9132_DUMP_ADDR_COUNT; a++)
		seq_printf(s, " a%-2u   ", zx279133_9132_dump_addrs[a]);
	seq_puts(s, "\n");
	for (m = 0; m < ZX279133_9132_DUMP_MEM_COUNT; m++) {
		u32 mem = zx279133_9132_dump_mems[m];

		seq_printf(s, "%3u  ", mem);
		for (a = 0; a < ZX279133_9132_DUMP_ADDR_COUNT; a++) {
			u32 val;

			if (zx279133_spa_indirect_read(eth, mem,
						       zx279133_9132_dump_addrs[a],
						       &val))
				val = 0xffffffff;
			seq_printf(s, " %08x", val);
		}
		seq_puts(s, "\n");
	}

	/*
	 * The four internal PHYs' register files, so that the console shows
	 * the chip's own answers and not only the switch's words.  The
	 * addresses are the ones on the SoC's MDIO controller #0.
	 */
	bus = zx279133_9132_uni_mdio_bus(eth);
	if (!bus) {
		seq_puts(s, "no 9132 PHY bus\n");
		return 0;
	}
	seq_puts(s, "\n9132 UNI PHY registers (mdio addr, reg 00..1f)\n");
	for (m = ZX279133_9132_UNI_PHY_FIRST;
	     m <= ZX279133_9132_UNI_PHY_LAST; m++) {
		int paddr = m;
		int reg;

		seq_printf(s, "%02x ", paddr);
		for (reg = 0; reg < 32; reg++) {
			int val = mdiobus_read(bus, paddr, reg);

			seq_printf(s, " %04x",
				   val < 0 ? 0xffff : (unsigned int)val);
		}
		seq_puts(s, "\n");
	}

	/*
	 * Where the ERAM tables this driver uses are.  The base is not a
	 * constant: it is what the allocation gave the table, and the
	 * hardware reads the table at the base it was told.  So the numbers
	 * here and the numbers the chip uses have to be the same, and this
	 * is where that can be seen.
	 */
	seq_puts(s, "\nERAM tables (id, base block, bits per entry)\n");
	for (m = 0; m < ZX279133_SDT_ID_MAX; m++) {
		if (!zx279133_eram[m].base)
			continue;
		seq_printf(s, "  0x%02x  %6u  %3u\n", m, zx279133_eram[m].base,
			   zx279133_eram[m].bit_len);
	}

	/*
	 * The lane's own counters.  They answer the question that decides
	 * where a frame that does not move is lost: does the SoC put a single
	 * bit on the wire towards the 9132, or does the frame stop inside
	 * the SoC?  XMAC0 is the MAC on the far side of that lane.
	 */
	seq_puts(s, "\n9132 lane (XMAC0, the SoC's MAC on the 9132's CPU port)\n");	seq_printf(s, "tx frames %08x  tx good %08x\n",
		   readl(eth->base + ZX279133_XMAC0_BASE +
			 ZX279133_XMAC_STAT_TX_FRAMES),
		   readl(eth->base + ZX279133_XMAC0_BASE +
			 ZX279133_XMAC_STAT_TX_GOOD));

	/*
	 * The VLAN action table, which is the other half of the forwarding
	 * story and is not in the SPA window this console started with: it
	 * lives in the NPPT's ERAM, and it says which ports a VID belongs
	 * to and which of them see it untagged.  The four service VIDs are
	 * the ones the DSA tagger uses, and VID 0 is the untagged one.
	 */
	seq_puts(s, "\n9132 VLAN table (vid, four words)\n");
	for (m = 0; m <= ZX279133_LAN_TRANSPORT_VID_MAX; m++) {
		u32 data[ZX279133_SMMU0_WORDS];
		int ret;

		if (m != 0 && (m < ZX279133_LAN_TRANSPORT_VID_MIN ||
			       m > ZX279133_LAN_TRANSPORT_VID_MAX))
			continue;

		ret = zx279133_smmu0_read(eth,
					  (zx279133_eram_base_of(ZX279133_VLAN_ERAM_SDT_ID) << 7) +
					  m * 128, data, ZX279133_SMMU0_WORDS,
					  ZX279133_SMMU0_CMD_READ);
		if (ret) {
			seq_printf(s, "%4u read failed (%d)\n", m, ret);
			continue;
		}
		seq_printf(s, "%4u %08x %08x %08x %08x\n", m,
			   data[0], data[1], data[2], data[3]);
	}

	return 0;
}

/* A tokenizer that does not need strsep(), which is not exported everywhere. */
static char *zx279133_9132_next_token(char **pp)
{
	char *s = *pp;
	char *start;

	while (*s == ' ' || *s == '\t' || *s == '\r' || *s == '\n')
		s++;
	if (!*s) {
		*pp = s;
		return NULL;
	}
	start = s;
	while (*s && *s != ' ' && *s != '\t' && *s != '\r' && *s != '\n')
		s++;
	if (*s) {
		*s = '\0';
		s++;
	}
	*pp = s;
	return start;
}

static ssize_t zx279133_9132_write(struct file *file, const char __user *ubuf,
				   size_t count, loff_t *ppos)
{
	struct seq_file *m = file->private_data;
	struct zx279133_eth *eth = m ? m->private : NULL;
	char buf[4096];
	char *p, *tok;
	u32 mem, addr, val = 0;
	int ret;

	if (!eth)
		return -ENODEV;
	if (count >= sizeof(buf))
		return -EINVAL;
	if (copy_from_user(buf, ubuf, count))
		return -EFAULT;
	buf[count] = '\0';

	p = buf;
	tok = zx279133_9132_next_token(&p);
	if (!tok)
		return -EINVAL;

	if (!strcmp(tok, "r") || !strcmp(tok, "w")) {
		bool is_write = tok[0] == 'w';

		tok = zx279133_9132_next_token(&p);
		if (!tok || kstrtou32(tok, 0, &mem))
			return -EINVAL;
		tok = zx279133_9132_next_token(&p);
		if (!tok || kstrtou32(tok, 0, &addr))
			return -EINVAL;
		if (is_write) {
			tok = zx279133_9132_next_token(&p);
			if (!tok || kstrtou32(tok, 0, &val))
				return -EINVAL;
			ret = zx279133_spa_indirect_write(eth, mem, addr, val);
			if (ret)
				return ret;
		}
		ret = zx279133_spa_indirect_read(eth, mem, addr, &val);
		if (ret)
			return ret;
		pr_info("9132 spa: mem %u addr %u = 0x%08x\n", mem, addr, val);
		return count;
	}

	/*
	 * The SoC's own registers, read and written directly.
	 *
	 * The offset is relative to the network processing block's base
	 * (0x19000000 on this board), which is the base every other offset in
	 * this file is relative to as well: the SPA window is at +0x8000, the
	 * XMAC0 at +0x140000, the XMAC1 at +0x180000 and the IDM at
	 * +0x280000.
	 *
	 * This is the one thing the console could not do before.  The
	 * kernel's /dev/mem refuses every device address, so a register
	 * outside the SPA window could not be read or written at all while
	 * the board was running - and the XMAC and the IDM are exactly where
	 * the questions are.  With this, the vendor's own values can be put
	 * back into a register and watched, one experiment at a time,
	 * without a rebuild for each of them.
	 *
	 *	"reg 140010"		read one word
	 *	"regw 140010 3e8000c6"	write one word and read it back
	 *	"regs 140000 8"		dump a range of words
	 */
	if (!strcmp(tok, "reg") || !strcmp(tok, "regw") ||
	    !strcmp(tok, "regs")) {
		bool is_write = !strcmp(tok, "regw");
		bool is_dump = !strcmp(tok, "regs");
		u32 off, n = 1, i;

		tok = zx279133_9132_next_token(&p);
		if (!tok || kstrtou32(tok, 0, &off))
			return -EINVAL;
		/*
		 * The NPPT window is device memory, so a read or a write that
		 * is not four-byte aligned is an alignment fault, and it takes
		 * the kernel down with it.  An unaligned offset is refused
		 * here instead: round 133 read one at +0x40002 and the board
		 * panicked in the middle of the round.
		 */
		if (off & 3)
			return -EINVAL;
		if (off >= ZX279133_NPPT_WINDOW_SIZE)
			return -EINVAL;
		if (is_dump) {
			tok = zx279133_9132_next_token(&p);
			if (!tok || kstrtou32(tok, 0, &n))
				return -EINVAL;
			if (!n || n > 256)
				return -EINVAL;
			if (off + 4 * n > ZX279133_NPPT_WINDOW_SIZE)
				return -EINVAL;
		}
		if (is_write) {
			tok = zx279133_9132_next_token(&p);
			if (!tok || kstrtou32(tok, 0, &val))
				return -EINVAL;
			writel(val, eth->base + off);
		}
		for (i = 0; i < n; i++) {
			u32 at = off + 4 * i;

			val = readl(eth->base + at);
			pr_info("soc reg 0x%08x (nppt +0x%06x) = 0x%08x\n",
				ZX279133_NPPT_BASE + at, at, val);
		}
		return count;
	}

	/*
	 * The PHYs, over the SoC's MDIO controller #0.
	 *
	 * "m <addr> <reg>" reads one register and "mw <addr> <reg> <val>"
	 * writes one and reads it back.  The address is the MDIO address
	 * (0x0a..0x0d for the four internal PHYs), NOT the DSA port number.
	 */
	if (!strcmp(tok, "m") || !strcmp(tok, "mw")) {
		struct mii_bus *bus;
		bool is_write = tok[1] == 'w';
		int maddr, mreg, mval = 0;

		tok = zx279133_9132_next_token(&p);
		if (!tok || kstrtou32(tok, 0, &mem))
			return -EINVAL;
		maddr = (int)mem;
		tok = zx279133_9132_next_token(&p);
		if (!tok || kstrtou32(tok, 0, &addr))
			return -EINVAL;
		mreg = (int)addr;
		if (is_write) {
			tok = zx279133_9132_next_token(&p);
			if (!tok || kstrtou32(tok, 0, &val))
				return -EINVAL;
			mval = (int)val;
		}

		bus = zx279133_9132_uni_mdio_bus(eth);
		if (!bus)
			return -ENODEV;
		if (is_write) {
			ret = mdiobus_write(bus, maddr, mreg, (u16)mval);
			if (ret)
				return ret;
		}
		ret = mdiobus_read(bus, maddr, mreg);
		if (ret < 0)
			return ret;
		pr_info("9132 mdio: addr %02x reg %02x = 0x%04x\n",
			maddr, mreg, (u16)ret);
		return count;
	}

	/*
	 * The VLAN action table, which is where the 9132 decides which
	 * ports a VID belongs to and which of them see it untagged.  It is
	 * in the NPPT's ERAM, not in the SPA window, so it gets its own
	 * commands:
	 *
	 *	"v 3b"			read the entry of VID 0x3b
	 *	"vw 3b w0 w1 w2 w3"	write it, then read it back
	 */
	if (!strcmp(tok, "v") || !strcmp(tok, "vw")) {
		u32 data[ZX279133_SMMU0_WORDS];
		bool is_write = tok[1] == 'w';
		u32 vid, addr_ind;
		int i;

		tok = zx279133_9132_next_token(&p);
		if (!tok || kstrtou32(tok, 0, &vid))
			return -EINVAL;
		if (vid > 0xfff)
			return -EINVAL;

		if (is_write) {
			for (i = 0; i < ZX279133_SMMU0_WORDS; i++) {
				tok = zx279133_9132_next_token(&p);
				if (!tok || kstrtou32(tok, 0, &data[i]))
					return -EINVAL;
			}
			addr_ind = (zx279133_eram_base_of(ZX279133_VLAN_ERAM_SDT_ID) << 7) +
				   vid * 128;
			ret = zx279133_smmu0_write(eth, addr_ind, data,
						   ZX279133_SMMU0_WORDS,
						   ZX279133_SMMU0_CMD_WRITE);
			if (ret)
				return ret;
		}

		addr_ind = (zx279133_eram_base_of(ZX279133_VLAN_ERAM_SDT_ID) << 7) + vid * 128;
		ret = zx279133_smmu0_read(eth, addr_ind, data,
					  ZX279133_SMMU0_WORDS,
					  ZX279133_SMMU0_CMD_READ);
		if (ret)
			return ret;
		pr_info("9132 vlan: vid %u = %08x %08x %08x %08x\n",
			vid, data[0], data[1], data[2], data[3]);
		return count;
	}

	/*
	 * The NPPT's own registers, read and written directly.
	 *
	 * The vendor's 4.19 system hands out the same window through
	 * /proc/pon/tm_debug/module_reg ("-r <module> reg=<n> cnt=<n>"), so a
	 * register this driver writes can be compared with the vendor's own
	 * value word for word, and the vendor's value can be written back
	 * without a rebuild.  The SPA window starts at 0x8000: register 0x14
	 * is the indirect command, 0x15 its status, 0x16 the data latch, and
	 * 0x17 the last value read - so those four are refused here, because
	 * writing them would break the indirect path every other command uses.
	 *
	 *	"nr 8054"			read one register
	 *	"nw 8054 0x1234"		write one and read it back
	 *	"nd 8000 1024"		dump a range into the kernel log
	 *	"nwp 8000 0x1 8004 0x2"	write a list of registers in one go
	 */
	if (!strcmp(tok, "nr") || !strcmp(tok, "nw") || !strcmp(tok, "nd") ||
	    !strcmp(tok, "nwp")) {
		bool is_dump = !strcmp(tok, "nd");
		bool is_list = !strcmp(tok, "nwp");
		u32 off, n = 1, i;

		tok = zx279133_9132_next_token(&p);
		if (!tok || kstrtou32(tok, 0, &off))
			return -EINVAL;
		if (off < ZX279133_SPA_WINDOW_BASE ||
		    off >= ZX279133_SPA_WINDOW_BASE + ZX279133_SPA_WINDOW_SIZE)
			return -EINVAL;

		if (is_dump) {
			tok = zx279133_9132_next_token(&p);
			if (!tok || kstrtou32(tok, 0, &n))
				return -EINVAL;
			if (!n || n > ZX279133_SPA_WINDOW_SIZE / 4)
				return -EINVAL;
			for (i = 0; i < n; i++) {
				u32 at = off + 4 * i;

				pr_info("nppt reg 0x%04x = 0x%08x\n", at,
					readl(eth->base + at));
			}
			return count;
		}

		if (is_list) {
			/*
			 * A list of (offset, value) pairs, so a whole
			 * register window can be written with one write to
			 * this file.  The console is a 115200 baud line and
			 * a register at a time would take minutes.
			 */
			while ((tok = zx279133_9132_next_token(&p)) != NULL) {
				u32 at, v;

				if (kstrtou32(tok, 0, &at))
					return -EINVAL;
				tok = zx279133_9132_next_token(&p);
				if (!tok || kstrtou32(tok, 0, &v))
					return -EINVAL;
				if (at < ZX279133_SPA_WINDOW_BASE ||
				    at >= ZX279133_SPA_WINDOW_BASE +
					     ZX279133_SPA_WINDOW_SIZE)
					return -EINVAL;
				if (at >= ZX279133_SPA_IND_CMD &&
				    at <= ZX279133_SPA_IND_DATA)
					return -EINVAL;
				writel(v, eth->base + at);
			}
			pr_info("nppt: a list of registers written\n");
			return count;
		}

		if (!strcmp(tok, "nw")) {
			tok = zx279133_9132_next_token(&p);
			if (!tok || kstrtou32(tok, 0, &val))
				return -EINVAL;
			if (off >= ZX279133_SPA_IND_CMD &&
			    off <= ZX279133_SPA_IND_DATA)
				return -EINVAL;
			writel(val, eth->base + off);
		}
		pr_info("nppt reg 0x%04x = 0x%08x\n", off,
			readl(eth->base + off));
		return count;
	}

	/*
	 * The NPPT's ERAM, which is where the tables the switch needs live:
	 * the multi-forward extension table, the VLAN action table, the port
	 * type table, the flood destination table and the port isolation
	 * matrix.  One block is 128 bits - the address the switch engine
	 * works in is a bit - and a table entry of 128 bits is one block,
	 * the first four words of it.
	 *
	 *	"er 125 8"			read eight blocks
	 *	"ew 125 w0 w1 w2 w3"		write one block and read it back
	 */
	if (!strcmp(tok, "er") || !strcmp(tok, "ew")) {
		u32 data[ZX279133_SMMU0_WORDS];
		bool is_write = tok[1] == 'w';
		u32 block, n = 1, i;
		int j;

		tok = zx279133_9132_next_token(&p);
		if (!tok || kstrtou32(tok, 0, &block))
			return -EINVAL;

		if (is_write) {
			for (j = 0; j < ZX279133_SMMU0_WORDS; j++) {
				tok = zx279133_9132_next_token(&p);
				if (!tok || kstrtou32(tok, 0, &data[j]))
					return -EINVAL;
			}
			ret = zx279133_smmu0_write(eth, block << 7, data,
						   ZX279133_SMMU0_WORDS,
						   ZX279133_SMMU0_CMD_WRITE);
			if (ret)
				return ret;
			n = 1;
		} else {
			tok = zx279133_9132_next_token(&p);
			if (!tok || kstrtou32(tok, 0, &n))
				return -EINVAL;
			if (!n || n > 4096)
				return -EINVAL;
		}

		for (i = 0; i < n; i++) {
			ret = zx279133_smmu0_read(eth, (block + i) << 7,
						  data, ZX279133_SMMU0_WORDS,
						  ZX279133_SMMU0_CMD_READ);
			if (ret)
				return ret;
			pr_info("eram block %u = %08x %08x %08x %08x\n",
				block + i, data[0], data[1], data[2], data[3]);
		}
		return count;
	}

	/*
	 * The MF extension table's copy of a port's attribute word - the
	 * copy the switch engine reads, and the one the vendor's own
	 * port_attr_def_*_set() helpers keep in step with the SPA memory.
	 *
	 *	"fa 5 96040001"	write it, then read the entry back
	 */
	if (!strcmp(tok, "fa")) {
		u32 data[ZX279133_SMMU0_WORDS];
		u32 mem_id;

		tok = zx279133_9132_next_token(&p);
		if (!tok || kstrtou32(tok, 0, &mem_id))
			return -EINVAL;
		tok = zx279133_9132_next_token(&p);
		if (!tok || kstrtou32(tok, 0, &val))
			return -EINVAL;

		ret = zx279133_smmu0_read(eth,
					  (zx279133_eram_base_of(ZX279133_MF_ERAM_SDT_ID) + mem_id) << 7,
					  data, ZX279133_SMMU0_WORDS,
					  ZX279133_SMMU0_CMD_READ);
		if (ret)
			return ret;
		data[0] = val;
		ret = zx279133_smmu0_write(eth,
					   (zx279133_eram_base_of(ZX279133_MF_ERAM_SDT_ID) + mem_id) << 7,
					   data, ZX279133_SMMU0_WORDS,
					   ZX279133_SMMU0_CMD_WRITE);
		if (ret)
			return ret;
		ret = zx279133_smmu0_read(eth,
					  (zx279133_eram_base_of(ZX279133_MF_ERAM_SDT_ID) + mem_id) << 7,
					  data, ZX279133_SMMU0_WORDS,
					  ZX279133_SMMU0_CMD_READ);
		if (ret)
			return ret;
		pr_info("mf eram mem %u = %08x %08x %08x %08x\n",
			mem_id, data[0], data[1], data[2], data[3]);
		return count;
	}

	/*
	 * The two knobs for the two questions that are still open about the
	 * way a frame leaves the board, both of them settled by experiment
	 * rather than by reading:
	 *
	 *	"txp 1"	the IDM TX descriptor's output port for the LAN
	 *			path.  The descriptor's byte 0x0a holds it in
	 *			bits 0..5; the vendor writes 1 (smac_0) and
	 *			never 5, this driver writes 5 (xmac_0, the
	 *			9132's own CPU port).
	 *	"usv 1"	give the four UNI ports the untag SVID their
	 *			transport VID asks for, so the tag the DSA
	 *			tagger puts on the conduit is taken off
	 *			again on the way out to the client.
	 */
	if (!strcmp(tok, "txp")) {
		u32 port;

		tok = zx279133_9132_next_token(&p);
		if (!tok || kstrtou32(tok, 0, &port) || port > 0x3f)
			return -EINVAL;
		eth->lan_tx_port = port;
		pr_info("the LAN TX output port is now %u\n", port);
		return count;
	}

	if (!strcmp(tok, "tx4")) {
		u32 word;

		tok = zx279133_9132_next_token(&p);
		if (!tok || kstrtou32(tok, 0, &word))
			return -EINVAL;
		eth->lan_tx_word4 = word;
		pr_info("the LAN TX metadata word 4 is now %08x\n", word);
		return count;
	}

	if (!strcmp(tok, "usv")) {
		u32 on, ppu;
		int err = 0;

		tok = zx279133_9132_next_token(&p);
		if (!tok || kstrtou32(tok, 0, &on) || on > 1)
			return -EINVAL;

		for (ppu = ZX279133_9132_UNI_PPU_FIRST;
		     ppu <= ZX279133_9132_UNI_PPU_LAST; ppu++) {
			u32 svid = on ? ZX279133_LAN_TRANSPORT_VID_MIN +
					 (ppu - 1) : 0;

			err = zx279133_spa_indirect_write(eth, ppu, 2,
							  svid << 18);
			if (err)
				break;
		}
		if (err)
			return err;
		eth->lan_untag_svid = on;
		pr_info("the UNI ports' untag SVID is now %s\n",
			on ? "their transport VID" : "0");
		return count;
	}

	/*
	 * Trap every protocol on a port to the CPU.
	 *
	 * The 9132 gives a frame from a jack to the CPU only when the port's
	 * protocol action table says so.  The vendor's flood group holds the
	 * WAN port and nothing else, so a frame whose destination is not in the
	 * MAC table is never flooded towards the CPU - it has to be trapped -
	 * and the trap is the only road the CPU has into the LAN.
	 *
	 * The table is ERAM table 0x18, two bits per (port, protocol) entry,
	 * and the protocol number the chip uses is not written down anywhere
	 * in this tree: the vendor's pairs were copied out of switch.ko's own
	 * .rodata, so the numbering is the chip's, and the only way to find
	 * out which number ARP has is to trap all of them and watch.
	 *
	 *	"trapall 2"	trap every protocol on the port 2
	 *	"trapall 2 0"	leave every protocol on the port 2 alone
	 */
	if (!strcmp(tok, "trapall")) {
		u32 port, action = 2, proto;
		int err = 0;

		tok = zx279133_9132_next_token(&p);
		if (!tok || kstrtou32(tok, 0, &port) || port > 7)
			return -EINVAL;
		tok = zx279133_9132_next_token(&p);
		if (tok && kstrtou32(tok, 0, &action))
			return -EINVAL;

		for (proto = 0; proto < 128; proto++) {
			err = zx279133_eram_table_set_bits(eth,
					ZX279133_SWITCH_PROTO_SDT_ID,
					proto | (port << 7), 2,
					action);
			if (err)
				break;
		}
		if (err)
			return err;
		pr_info("port %u traps every protocol with action %u\n",
			port, action);
		return count;
	}

	/*
	 * The two queue ids the CPU path runs on.
	 *
	 * The CPU's own outport is 15, and the outport map sends it to the real
	 * port 15 and to the queue id the global config table holds.  The
	 * vendor's value for both is 0x193, and nothing in this tree says how
	 * that number becomes one of the IDM's sixteen CPU queues, so both of
	 * them are knobs now.
	 *
	 *	"cqid 0x193 0x193"	the vendor's own values
	 *	"cqid 0x3 0x3"	the low ones
	 */
	if (!strcmp(tok, "cqid")) {
		u32 base, out;
		int err;

		tok = zx279133_9132_next_token(&p);
		if (!tok || kstrtou32(tok, 0, &base) || base > 0x1ff)
			return -EINVAL;
		tok = zx279133_9132_next_token(&p);
		if (!tok || kstrtou32(tok, 0, &out) || out > 0x1ff)
			return -EINVAL;

		err = zx279133_eram_window_patch(eth, ZX279133_GLBCFG_ERAM_SDT_ID,
						 0, 3,
						 (0x1ffu << 17) | (1u << 16) |
						 (1u << 13),
						 (base << 17) | (1u << 16) |
						 (1u << 13));
		if (err)
			return err;
		err = zx279133_eram_table_set_bits(eth, ZX279133_OUTPORT_ERAM_SDT_ID,
						    0x0f, 16,
						    0x0f | (out << 6));
		if (err)
			return err;
		pr_info("the CPU base queue id is now %u and the outport 15 queue id %u\n",
			base, out);
		return count;
	}

	/*
	 * The reason map: the CPU queue a frame that arrives with a given
	 * reason is delivered on.  The vendor's own load path sets only
	 * reason 0 - OMCI - to queue 7, so every other reason is whatever
	 * the chip came up with.
	 *
	 *	"rmap 8 0"	reason 8 goes to CPU queue 0
	 */
	if (!strcmp(tok, "rmap")) {
		u32 reason, qid;
		int err;

		tok = zx279133_9132_next_token(&p);
		if (!tok || kstrtou32(tok, 0, &reason) || reason > 0x3f)
			return -EINVAL;
		tok = zx279133_9132_next_token(&p);
		if (!tok || kstrtou32(tok, 0, &qid) || qid > 0x1f)
			return -EINVAL;

		err = zx279133_eram_window_patch(eth, ZX279133_QID_ERAM_SDT_ID,
						 reason, reason & 3,
						 0x1fu << 24, qid << 24);
		if (err)
			return err;
		pr_info("reason %u now goes to CPU queue %u\n", reason, qid);
		return count;
	}

	/*
	 * The IDM's receive descriptors.
	 *
	 * The rings live in coherent memory, so the console can read them: a
	 * descriptor the hardware has filled in has an address and a length in
	 * it, and a descriptor the driver has already released has an address
	 * of zero.  This is the one piece of evidence that says whether a
	 * frame ever reached the IDM at all, and which queue it landed in.
	 *
	 *	"rxd 0 8"	the eight descriptors at the head of queue 0
	 */
	if (!strcmp(tok, "rxd")) {
		u32 queue, count, i;
		u16 cons;

		tok = zx279133_9132_next_token(&p);
		if (!tok || kstrtou32(tok, 0, &queue) ||
		    queue >= ZX279133_IDM_RX_QUEUES)
			return -EINVAL;
		tok = zx279133_9132_next_token(&p);
		if (!tok || kstrtou32(tok, 0, &count) || count > 32)
			return -EINVAL;

		if (!eth->rx_descs)
			return -ENODEV;
		cons = eth->rx_cons[queue];
		for (i = 0; i < count; i++) {
			u16 index = (cons + i) &
				(ZX279133_IDM_RX_RING_SIZE - 1);
			struct zx279133_idm_desc *desc;

			desc = eth->rx_descs +
				queue * ZX279133_IDM_RX_RING_SIZE + index;
			pr_info("rx queue %u desc %u: address %08x length %08x metadata %08x %08x %08x\n",
				queue, index,
				le32_to_cpu(READ_ONCE(desc->address)),
				le32_to_cpu(READ_ONCE(desc->length_flags)),
				le32_to_cpu(READ_ONCE(desc->metadata[0])),
				le32_to_cpu(READ_ONCE(desc->metadata[1])),
				le32_to_cpu(READ_ONCE(desc->metadata[2])));
		}
		return count;
	}

	/*
	 * The next frame's bytes.
	 *
	 * The counters say how many frames arrived; they cannot say what
	 * the frames were, and on this board the two were very different
	 * things - the board's bridge learned source MAC addresses that
	 * were ASCII text.  This arms the printout of the next frame the
	 * driver receives: its descriptor, and the first 64 bytes of it.
	 *
	 *	"rxb"	arm it; the next frame received is printed
	 */
	if (!strcmp(tok, "rxb")) {
		WRITE_ONCE(eth->rx_dump, 1);
		pr_info("the next frame received will be printed\n");
		return count;
	}

	/*
	 * The BMU's BPPE table.
	 *
	 * The table is the map from a buffer index to a buffer, and it is the
	 * one thing in the RED's input path that no vendor module touches:
	 * np_133.ko, the vendor kernel and the stock U-Boot all leave it
	 * alone.  The tree's driver fills it with 16-bit big-endian indices,
	 * which may or may not be what this chip expects - and a table the
	 * hardware does not agree with makes the RED resolve a frame to
	 * nothing.
	 *
	 *	"bppd <count>"		read the first entries
	 *	"bppw <count> <mode>"	write the whole table:
	 *				0 = 16-bit big endian (the tree's)
	 *				1 = 16-bit little endian
	 *				2 = 32-bit little endian
	 *				3 = all zero
	 */
	if (!strcmp(tok, "bppd") || !strcmp(tok, "bppw")) {
		u32 count = 16, mode = 0, i;
		bool write_it = tok[1] == 'w';

		tok = zx279133_9132_next_token(&p);
		if (!tok || kstrtou32(tok, 0, &count))
			return -EINVAL;
		if (!count || count > 64)
			return -EINVAL;

		if (!eth->bmu_mem)
			return -ENODEV;

		if (write_it) {
			tok = zx279133_9132_next_token(&p);
			if (!tok || kstrtou32(tok, 0, &mode) || mode > 3)
				return -EINVAL;
			for (i = 0; i < ZX279133_BMU_NORMAL_COUNT; i++) {
				switch (mode) {
				case 0:
					writew(cpu_to_be16(i), eth->bmu_mem + 2 * i);
					break;
				case 1:
					writew(cpu_to_le16(i), eth->bmu_mem + 2 * i);
					break;
				case 2:
					writew(cpu_to_le16(i), eth->bmu_mem + 4 * i);
					writew(cpu_to_le16(i >> 16),
						eth->bmu_mem + 4 * i + 2);
					break;
				default:
					writew(0, eth->bmu_mem + 2 * i);
					break;
				}
			}
			pr_info("the BPPE table has been written, mode %u\n", mode);
			return count;
		}

		for (i = 0; i < count; i++)
			pr_info("the BPPE table at entry %u = 0x%08x\n", i,
				readl(eth->bmu_mem + 4 * i));
		return count;
	}

	/*
	 * The RED's queue RAMs.
	 *
	 * The RED reaches its queues through an indirect port, so the plain
	 * register console cannot touch them - and they are where the RED's
	 * per-queue geometry lives, which is what decides how much room a
	 * queue has and therefore what the RED hands to the queue manager.
	 * These commands write a value into every queue of one RAM, so that a
	 * geometry can be changed and the effect watched without a new
	 * kernel.
	 *
	 *	"redq2 <value>"		the IN buffer queue RAM (ram 2)
	 *	"redq0 <value>"		the OUT buffer queue RAM (ram 0)
	 *	"redq4 <w0> <w1> <w2> <w3>"	the per-queue config (ram 4)
	 *	"redqr <ram> <addr> <count>"	read a RAM back
	 */
	if (!strcmp(tok, "redq2") || !strcmp(tok, "redq0") ||
	    !strcmp(tok, "redq4") || !strcmp(tok, "redqr")) {
		u32 ram_id, addr = 0, count, data[4] = { 0, 0, 0, 0 };
		unsigned int i, q;
		bool is_read = tok[4] == 'r';

		tok = zx279133_9132_next_token(&p);
		if (!tok || kstrtou32(tok, 0, &ram_id))
			return -EINVAL;
		if (ram_id > 4)
			return -EINVAL;

		if (is_read) {
			tok = zx279133_9132_next_token(&p);
			if (!tok || kstrtou32(tok, 0, &addr))
				return -EINVAL;
			tok = zx279133_9132_next_token(&p);
			if (!tok || kstrtou32(tok, 0, &count))
				return -EINVAL;
			if (!count || count > 4)
				return -EINVAL;
			zx279133_red_ram_read(eth, ram_id, addr, data, count);
			for (i = 0; i < count; i++)
				pr_info("the RED's queue ram %u at queue %u = 0x%08x\n",
					ram_id, addr + i, data[i]);
			return count;
		}

		count = (ram_id == 4) ? 4 : 1;
		for (i = 0; i < count; i++) {
			tok = zx279133_9132_next_token(&p);
			if (!tok || kstrtou32(tok, 0, &data[i]))
				return -EINVAL;
		}

		for (q = 0; q < ZX279133_RED_QUEUE_COUNT; q++)
			zx279133_red_ram_write(eth, ram_id, q, data, count);

		zx279133_red_ram_read(eth, ram_id, 0, data, count);
		pr_info("the RED's queue ram %u has been written for %u queues, queue 0 reads 0x%08x\n",
			ram_id, ZX279133_RED_QUEUE_COUNT, data[0]);
		return count;
	}

	/*
	 * One entry of one ERAM table, by the table's id rather than by its
	 * base block, so that a table can be read and written the way the
	 * vendor's own code does it - the entry is merged into the 128-bit
	 * window it lives in, bit by bit.
	 *
	 *	"es 18 128 2 2"	write entry 128 of table 0x18, 2 bits wide,
	 *				with the value 2
	 *	"eg 18 128 2"		read that entry back
	 */
	if (!strcmp(tok, "es") || !strcmp(tok, "eg")) {
		u32 sdt_id, index, width, value = 0;
		bool is_write = tok[1] == 's';

		tok = zx279133_9132_next_token(&p);
		if (!tok || kstrtou32(tok, 0, &sdt_id))
			return -EINVAL;
		tok = zx279133_9132_next_token(&p);
		if (!tok || kstrtou32(tok, 0, &index))
			return -EINVAL;
		tok = zx279133_9132_next_token(&p);
		if (!tok || kstrtou32(tok, 0, &width))
			return -EINVAL;
		if (is_write) {
			tok = zx279133_9132_next_token(&p);
			if (!tok || kstrtou32(tok, 0, &value))
				return -EINVAL;
			ret = zx279133_eram_table_set_bits(eth, sdt_id,
							   index, width,
							   value);
			if (ret)
				return ret;
		}
		ret = zx279133_eram_table_get_bits(eth, sdt_id, index, width,
						   &value);
		if (ret)
			return ret;
		pr_info("eram table 0x%02x entry %u width %u = 0x%x\n",
			sdt_id, index, width, value);
		return count;
	}

	return -EINVAL;
}

static int zx279133_9132_open(struct inode *inode, struct file *file)
{
	return single_open(file, zx279133_9132_show, inode->i_private);
}

static const struct file_operations zx279133_9132_fops = {
	.owner	= THIS_MODULE,
	.open	= zx279133_9132_open,
	.read	= seq_read,
	.write	= zx279133_9132_write,
	.llseek	= seq_lseek,
	.release	= single_release,
};

int zx279133_9132_debugfs_init(struct zx279133_eth *eth)
{
	struct dentry *dentry;

	dentry = debugfs_create_file("zx279133-9132", 0600, NULL, eth,
				     &zx279133_9132_fops);
	return devm_add_action_or_reset(eth->dev,
					zx279133_debugfs_remove, dentry);
}
#endif

#define ZX279133_FAST_IKEY_BASE_BLOCK	23186
#define ZX279133_FAST_STAT_BASE_BLOCK	0x9081
#define ZX279133_FAST_STAT_ENTRY_STRIDE	64
#define ZX279133_SMMU0_CMD_READ64	0x08000000
#define ZX279133_SMMU0_CMD_READ_CLEAR	0x40000000

int zx279133_fast_ikey_write(struct zx279133_eth *eth, u32 index,
			     const u32 *data)
{
	return zx279133_smmu0_write(eth,
				    (ZX279133_FAST_IKEY_BASE_BLOCK + index) << 7,
				    data, ZX279133_SMMU0_WORDS,
				    ZX279133_SMMU0_CMD_WRITE);
}

static int zx279133_fast_stat_counter_read(struct zx279133_eth *eth,
					   u32 index, u64 *counter)
{
	u32 data[ZX279133_SMMU0_WORDS];
	int ret;

	/* A 64-bit SDT read returns the selected entry in words 2 and 3. */
	ret = zx279133_smmu0_read(eth,
				  (ZX279133_FAST_STAT_BASE_BLOCK << 7) +
				  index * ZX279133_FAST_STAT_ENTRY_STRIDE,
				  data, ARRAY_SIZE(data),
				  ZX279133_SMMU0_CMD_READ64);
	if (ret)
		return ret;
	*counter = (u64)data[3] << 32 | data[2];

	return 0;
}

int zx279133_fast_stats_read(struct zx279133_eth *eth, u16 flow_id,
			     u64 *packets, u64 *bytes)
{
	int ret;

	if (flow_id >= ZX279133_FAST_STAT_DEPTH)
		return -EINVAL;
	ret = zx279133_fast_stat_counter_read(eth, 2 * flow_id, packets);
	if (ret)
		return ret;

	return zx279133_fast_stat_counter_read(eth, 2 * flow_id + 1, bytes);
}

int zx279133_fast_age_read_clear(struct zx279133_eth *eth, u32 age,
				 bool *used)
{
	u32 data[ZX279133_SMMU0_WORDS];
	bool first;
	int ret;

	if (age >= ZX279133_FAST_AGE_DEPTH)
		return -EINVAL;
	ret = zx279133_smmu0_read(eth, age, data, ARRAY_SIZE(data),
				  ZX279133_SMMU0_CMD_READ_CLEAR);
	if (ret)
		return ret;
	first = data[3] >> 31;
	ret = zx279133_smmu0_read(eth, age, data, ARRAY_SIZE(data),
				  ZX279133_SMMU0_CMD_READ_CLEAR);
	if (ret)
		return ret;
	*used = first || data[3] >> 31;

	return 0;
}

int zx279133_vlan_runtime_prepare(struct zx279133_eth *eth)
{
	u16 vid;
	int ret;

	/* SDT0 is a 4096-entry, 128-bit VLAN action table. Apply the board-proven
	 * port policy to VID 0 and to the switch's private service tags.
	 */
	ret = zx279133_smmu0_write(eth,
				  zx279133_eram_base_of(ZX279133_VLAN_ERAM_SDT_ID) << 7,
				  zx279133_vlan0_entry,
				  ARRAY_SIZE(zx279133_vlan0_entry),
				  ZX279133_SMMU0_CMD_WRITE);
	if (ret)
		return ret;

	for (vid = ZX279133_LAN_TRANSPORT_VID_MIN;
	     vid <= ZX279133_LAN_TRANSPORT_VID_MAX; vid++) {
		ret = zx279133_smmu0_write(eth,
					  (zx279133_eram_base_of(ZX279133_VLAN_ERAM_SDT_ID) << 7) +
					  vid * 128,
					  zx279133_vlan0_entry,
					  ARRAY_SIZE(zx279133_vlan0_entry),
					  ZX279133_SMMU0_CMD_WRITE);
		if (ret)
			return ret;
	}

	return 0;
}

static int zx279133_np_ppu_init_tail(struct zx279133_eth *eth)
{
	/*
	 * Full-application runtime image from global_table_print() and a
	 * byte-exact factory SMMU0 capture. Besides the 1996-byte L2 MTU it
	 * selects switch UNI5/memory 6 as the external WAN input.
	 */
	/*
	 * se_smmu0_write() maps data[0..3] to WDATA0..3.  RDATA is exposed
	 * in reverse order from WDATA.  global_mf_cnt is the low nibble of
	 * data[0].
	 */
	u32 data[4] = { 0x04500600, 0x01f30036, 0, 0x40904400 };
	u32 reg;
	int ret, i;

	/* ppu_cluster_set_ikey_rsp_fifo_afull_cfg(0): PPS 0x9043c [4:0]. */
	reg = readl(eth->pps_base + 0x9043c);
	writel(reg & ~0x1f, eth->pps_base + 0x9043c);

	/* SE ERAM sdt 1, entry 0, block 16384. */
	ret = zx279133_smmu0_write(eth, 16384 << 7, data, 4,
				   ZX279133_SMMU0_CMD_WRITE);
	if (ret)
		return ret;

	/*
	 * The vendor cspd runtime WANID image uses a 1996-byte L3 limit.
	 * Keep that vendor-compatible CPU-WAN limit; it is not a 9K datapath
	 * configuration. WANIDs 0 and 1 still carry the two CPU MACs.
	 */
	for (i = 0; i < ZX279133_WANID_COUNT; i++) {
		ret = zx279133_smmu0_read(eth, (16386 + i) << 7, data, 4,
					  ZX279133_SMMU0_CMD_READ);
		if (ret)
			return ret;
		data[0] = (data[0] & ~GENMASK(15, 0)) |
			  ZX279133_WANID_L3_MTU;
		ret = zx279133_smmu0_write(eth, (16386 + i) << 7, data, 4,
					   ZX279133_SMMU0_CMD_WRITE);
		if (ret)
			return ret;
	}
	ret = zx279133_program_wanid_cpu_mac(eth, eth->ndev->dev_addr);
	if (ret)
		return ret;

	/* Runtime P2P flow dispatch and packet-type detail used by the ME. */
	zx279133_ppu_cluster_runtime_prepare(eth);

	return 0;
}

static int zx279133_mf_eram_entry_write(struct zx279133_eth *eth, u32 mem_id,
					const u32 *data)
{
	u32 base = zx279133_eram_base_of(ZX279133_MF_ERAM_SDT_ID);
	u32 addr_ind = (base + mem_id) << 7;
	u32 rd[ZX279133_SMMU0_WORDS];
	int ret;

	/* A table that was never created has no base, and block 0 is not
	 * a place to write a port's attributes to.
	 */
	if (!base)
		return -EINVAL;

	ret = zx279133_smmu0_write(eth, addr_ind, data,
				   ZX279133_SMMU0_WORDS,
				   ZX279133_SMMU0_CMD_WRITE);
	if (ret)
		return ret;

	/* Preserve the write/readback failure check without production noise. */
	ret = zx279133_smmu0_read(eth, addr_ind, rd, ZX279133_SMMU0_WORDS,
				  ZX279133_SMMU0_CMD_READ);
	if (ret) {
		dev_err(eth->dev, "MF ERAM readback failed: %d\n", ret);
		return ret;
	}
	dev_dbg(eth->dev,
		"MF ERAM mem %u ind 0x%x read %08x %08x %08x %08x\n",
		mem_id, addr_ind, rd[0], rd[1], rd[2], rd[3]);

	return 0;
}

static int zx279133_mf_eram_attr_set(struct zx279133_eth *eth, u32 mem_id,
				     u32 attr)
{
	u32 data[ZX279133_SMMU0_WORDS];
	u32 base = zx279133_eram_base_of(ZX279133_MF_ERAM_SDT_ID);
	u32 addr_ind = (base + mem_id) << 7;
	int ret;

	if (!base)
		return -EINVAL;

	ret = zx279133_smmu0_read(eth, addr_ind, data, ARRAY_SIZE(data),
				  ZX279133_SMMU0_CMD_READ);
	if (ret)
		return ret;
	/*
	 * The attribute word is the first word of the entry.  The vendor's
	 * port_attr_def_*_set() helpers read the entry, patch one bit of
	 * the byte at offset 3 - sa_lookup_learn_en is bit 7 of it - and
	 * write the entry back, so the word the switch engine reads is
	 * word 0, not the last one.
	 */
	data[0] = attr;

	return zx279133_mf_eram_entry_write(eth, mem_id, data);
}

static int zx279133_mf_port_attr_set(struct zx279133_eth *eth, u32 mem_id,
				     u32 companion, u32 bit)
{
	u32 attr;
	int ret;

	ret = zx279133_spa_indirect_read(eth, mem_id, 19, &attr);
	if (ret)
		return ret;
	attr |= bit;
	ret = zx279133_spa_indirect_write(eth, mem_id, 19, attr);
	if (ret)
		return ret;
	ret = zx279133_spa_indirect_write(eth, companion, 19, attr);
	if (ret)
		return ret;
	return zx279133_mf_eram_attr_set(eth, mem_id, attr);
}

static int zx279133_mf_port_ppuinit(struct zx279133_eth *eth)
{
	unsigned int mem_id;
	int ret;

	ret = zx279133_mf_port_attr_set(eth, 0, 14, BIT(17) | BIT(18));
	if (ret)
		return ret;

	for (mem_id = 1; mem_id <= 5; mem_id++) {
		ret = zx279133_mf_port_attr_set(eth, mem_id,
						mem_id == 5 ? 10 : mem_id,
						BIT(18));
		if (ret)
			return ret;
	}

	/* CPU-originated IDM frames enter the PPU through logical port 15. */
	ret = zx279133_mf_port_attr_set(eth, 15, 15, BIT(18));
	if (ret)
		return ret;

	return 0;
}

/*
 * 9132 UNI switch ports (SK-D840N).
 *
 * Provenance: docs/15-switchko-init-diff-20261020.md.  Everything below was
 * read out of the vendor modules, not guessed:
 *
 *   - the 9132 is programmed through the SPA indirect window, and the vendor's
 *     np_133.ko encodes the command exactly the way this file already does
 *     (addr | (mem_id << 20) | (op << 27)), so zx279133_spa_indirect_write()
 *     is the same hammer the vendor uses;
 *   - the per-port attribute word is SPA indirect address 19 (0x013) of a PPU
 *     port's memory - the same address zx279133_mf_port_attr_set() below
 *     already writes;
 *   - the bit positions come from np_133.ko's port_attr_def_*_set() helpers,
 *     which read that word, patch one bit of it and write it back.  They are
 *     listed in docs/15 §5.  Where this file and the MF names collide (bit 18
 *     is "ppuinit" to the MF block and "input VLAN check" to the switch API;
 *     bit 28 is "smac_learn" and "DA lookup") they are the same bit - the
 *     vendor's switch.ko init and np.ko's np_init_done both set it, and the
 *     live values 0x148e0000 (WAN) and 0x1c8c2800 (LAN vport) both carry it.
 *
 * The 9132's four UNI ports are PPU ports 1..4 and the external RTL8226 is
 * PPU port 5: np_133.ko's sw_port_change_to_chip_port() maps switch port p to
 * p+1, and the live board confirms switch port 4 is the outer PHY
 * ("outerphy_mode=5,outerphy_name=phy_RTL8226") while the 9132's own MACs
 * are switch ports 0..3.  PPU port 0 is the CPU port.
 *
 * The vendor's switch.ko init (sw_init_switch, switch.ko 0x2e74) does this
 * to every port; this is the part of it that belongs to the 9132's ports:
 *
 *   sw_init_port_isolate    isolate enable 0 for all 64 ports
 *   sw_init_mac_lookup_ctrl SA learn 1, DA lookup 1, MAC-limit-full 0
 *   sw_init_flood_ctrl      broadcast and multicast flood enabled
 *   sw_init_port_route_mode route mode 0
 *   sw_set_default_vlan_check_ctrl  input VLAN check enabled
 *
 * Port *enable* is not an SPA register in the vendor: zxic_api_sw_port_set_
 * port_enable() goes through np_133.ko's smac_set_port_enable(), which
 * drives the port's internal PHY over MDIO.  That part needs the PHY side and
 * is not attempted here.
 */
/*
 * The 9132's per-port attribute memories are the chip's own port numbers and
 * live in zx279133.h: ZX279133_9132_UNI_PPU_FIRST..LAST are the four UNI
 * jacks, ZX279133_9132_OUTERPHY_PPU is the external RTL8226 on jack 4.
 */
#define ZX279133_9132_OUTERPHY_PPU	5

/*
 * PPU 5 is also the 9132 side of the DSA CPU port.
 *
 * The port numbering here is the SPA memory id, and the bootloader prints the
 * authoritative list of them (spa_port_attr_op help, measured 2026-10-21):
 *
 *	 0 = PON0	 1..4 = SMAC0..3 (the four UNI jacks)	 5 = XMAC0
 *	 6 = XMAC1	 7 = RGMII1	 8 = LOOP_SOPC	10 = XMAC_B
 *	12 = PON_PTL	14 = PON1	15 = CPU (the trap port)	16 = WIFI0
 *
 * so the CPU trap port is memory 15 and memory 0 is PON0 - the comment that used
 * to call PPU 0 "the CPU port" was wrong.
 *
 * The DSA CPU port is PPU 5.  The DSA switch driver numbers its ports 4..7 for
 * the four jacks and 8 for the conduit, and maps a DSA port to its PPU with
 * ZX279133_SWITCH_DSA_PPU(port) = port - 3, so port 8 lands on PPU 5 - which is
 * XMAC0, the SGMII side the SoC's own MAC reaches the 9132 through.  Nothing
 * configured PPU 5 before: the four jacks were set up and the conduit, which is
 * where every frame between the CPU and the jacks has to pass, was left with
 * whatever the bootloader left behind.
 */
/* Per-port attribute word: SPA indirect address 19 of a PPU port. */
#define ZX279133_PPU_ATTR_REG		19
#define ZX279133_PPU_ATTR_ROUTE_MODE	BIT(13)
#define ZX279133_PPU_ATTR_IN_VLAN_CHK	BIT(18)
#define ZX279133_PPU_ATTR_BRD_FLOOD	BIT(25)
#define ZX279133_PPU_ATTR_MUL_FLOOD	BIT(26)
#define ZX279133_PPU_ATTR_ISOLATE_EN	BIT(27)
#define ZX279133_PPU_ATTR_DA_LOOKUP	BIT(28)
#define ZX279133_PPU_ATTR_MAC_LIMIT	BIT(29)
#define ZX279133_PPU_ATTR_SA_LEARN	BIT(31)

/*
 * Bit 0 of the attribute word, which the named bits above do not cover.
 *
 * The vendor's own working configuration was read out of the running
 * system through its module_ind_ram dump, and it says what a live port
 * looks like there:
 *
 *	spa_port_attr_smac_0..3		0x96040001
 *	spa_port_attr_xmac_0/1,
 *	spa_port_attr_rgmii		0x96040001
 *	spa_port_attr_pon_0		0x90040000
 *	spa_port_attr_wifi_ssid_*	0x90040000
 *	spa_port_attr_trap(cpu)		0x00000000
 *	spa_port_attr_loop,
 *	spa_port_attr_vir_err		0x00000000
 *
 * The named bits add up to 0x96040000, so the live ports carry bit 0 as
 * well - and only the ports that carry traffic carry it.  It is set here
 * for the same reason the vendor sets it: this word is a copy of a
 * configuration that is known to work, and a bit whose name nobody knows
 * is better copied than guessed at.
 */
#define ZX279133_PPU_ATTR_LIVE		BIT(0)

/*
 * The whole attribute word of a port that carries traffic, as the vendor's
 * own working system has it: 0x96040000 for the named bits plus bit 0 for
 * the one bit nobody can name.
 */
#define ZX279133_9132_VENDOR_ATTR_WORD	0x96040001

/*
 * The 9132's per-port words, read out and nothing else.
 *
 * Why this exists: everything below *writes* the per-port words, and after
 * that the chip no longer remembers what the bootloader left there.  The one
 * number nobody can guess is the configuration the vendor's own 4.19 working
 * system uses on each UNI port - if it is not the transport VID scheme, then
 * the scheme this driver implements cannot be what the switch actually does,
 * and no amount of further writing of a guess will help.
 * will make it work.  So the baseline is dumped first, before any of this
 * driver's own 9132 writes.
 *
 * The addresses, and what they are.  The layout is not a guess: it is
 * np_133.ko's own descriptor table, spa_port_attr_table, 74 entries of
 * {id, mask, bit offset, word address}, decoded on 2026-10-21 (see
 * docs/24).  The same numbering is what the bootloader's own
 * "spa_port_attr_op help" prints, so the bootloader and the vendor module
 * agree on it.
 *
 *   0x000  TLB_VLD(0), PC(1..14), FLOWNUM(15..20), COS(21..23),
 *          OUTPUT_PORT(24..29), NPPU_DIR_EN(30), CRC_ADD_EN(31).
 *          The entry program counter is READ here and never written, because
 *          writing bit 0 of it is what zx279133_lan_port_bringup() does to
 *          the SoC's own entry program and a port that runs the wrong program
 *          is a dead port.
 *   0x001  ICU_COS_GET_EN(0..2), ICU_PRI_CFG(3..11), VLAN_FILTER_MOD(12..13),
 *          QUEUE_ID(14..22), BPDU_TRAP_EN(23), PAE_TRAP_EN(24),
 *          DFT_PRI(25..27), LLC_EN(28).  DFT_PRI is the default VLAN priority.
 *   0x002  PORT_PVID_UNTAG(18..29) - the untagged SVID, i.e. the PVID -
 *          and PORT_DMAC_OFFSET(30..31).
 *   0x009  the IP rate-limit fields and TAG_FILTER(24..29).
 *   0x011  USER_DEF_AREA1 - where the vendor's flood tables live.
 *   0x012  USER_DEF_AREA2 - where the vendor's flood group ids live.
 *   0x013  USER_DEF_AREA3 - the per-port attribute word (isolate, SA learn,
 *          DA lookup, flooding, route mode, input VLAN check).  It is a
 *          user area, so its bit meanings come from the vendor's switch API
 *          rather than from the table above.
 *
 * The SPA memory ids are: 0 = PON0, 1..4 = the four UNI jacks, 5 = XMAC0
 * (the DSA conduit, and the external RTL8226's port on the vendor's
 * board), 6 = XMAC1, 7 = RGMII1, 15 = the CPU trap port.
 */
#define ZX279133_PPU_WORD_ENTRY_PC	0
#define ZX279133_PPU_WORD_PVID_PRI	1
#define ZX279133_PPU_WORD_PVID_VID	2
#define ZX279133_PPU_WORD_VLAN_FILT	9
#define ZX279133_PPU_WORD_ATTR		19

static void zx279133_9132_port_words_read(struct zx279133_eth *eth)
{
	static const struct {
		u32 addr;
		const char *name;
	} words[] = {
		{ ZX279133_PPU_WORD_ENTRY_PC,	"entry-pc" },
		{ ZX279133_PPU_WORD_PVID_PRI,	"pvid-pri" },
		{ ZX279133_PPU_WORD_PVID_VID,	"pvid-vid" },
		{ ZX279133_PPU_WORD_VLAN_FILT,	"vlan-filter" },
		{ ZX279133_PPU_WORD_ATTR,	"attr" },
	};
	u32 ppu;
	size_t i;

	for (ppu = 0; ppu <= ZX279133_9132_OUTERPHY_PPU; ppu++) {
		for (i = 0; i < ARRAY_SIZE(words); i++) {
			u32 val;

			if (zx279133_spa_indirect_read(eth, ppu, words[i].addr,
						       &val)) {
				dev_info(eth->dev,
					 "9132 PPU %u word %u (%s): read failed\n",
					 ppu, words[i].addr, words[i].name);
				continue;
			}

			dev_info(eth->dev,
				 "9132 PPU %u word %u (%s) = %08x\n",
				 ppu, words[i].addr, words[i].name, val);
		}
	}
}

/*
 * The vendor's own working state for one LAN-side 9132 port, word for word.
 *
 * Every value here was read out of the running 4.19 system with the vendor's
 * read-only tm_debug interface (spa_port_attr_smac_0 .. smac_3 and
 * spa_port_attr_xmac_0, 2026-10-21) and is kept in staging/ctl/
 * vlive9132-cpu-20261021.txt.  Nothing here is guessed:
 *
 *	word0  = 0x0000a6a5  the port's entry program
 *	word1  = 0x0000088f  (the same on every port; left alone)
 *	word2  = 0x00000000  the untag SVID - the vendor's 9132 has none
 *	word9  = 0x00000000  the VLAN filter
 *	word10 = 0x0fa0000e
 *	word13 = 0x00008008
 *	word17 = the port's broadcast flood word
 *	word18 = the port's unknown-unicast / multicast flood word
 *	word19 = 0x96040001  the port attribute word
 *
 * Why the transport VID is not in there: the 9132 is a transparent
 * MAC-learning bridge, not a VLAN switch.  Three independent pieces of the
 * vendor's own working system say so (docs/16 §2.2): the vendor's FDB holds
 * one entry and its vid field is 0, every port's VLAN filter mask reads 0,
 * and every port's ingress frame mode reads 0.  The transport VID belongs to
 * the tree's external RTL8372N, whose switch does speak 802.1Q; writing one
 * into this chip gives it a VLAN filter to fail, and the DSA tagger's tag
 * has to be taken off again on the way to the wire instead
 * (zx279133_lan_strip_transport_vlan() in zx279133-netdev.c).
 */
static const u32 zx279133_9132_vendor_words[][2] = {
	{ 0,  0x0000a6a5 },	/* the port's entry program */
	{ 1,  0x0000088f },
	{ 2,  0x00000000 },	/* the untag SVID: none */
	{ 9,  0x00000000 },	/* the VLAN filter: none */
	{ 10, 0x0fa0000e },
	{ 13, 0x00008008 },
	{ 19, ZX279133_9132_VENDOR_ATTR_WORD },	/* the port attribute word */
};

/*
 * The CPU trap's own words.
 *
 * The vendor's board reads these out of the trap port's SPA memory, and they
 * are *not* the ones the four jacks carry: the trap port runs a different
 * entry program, and its port attribute word is zero, because a trap port is
 * a sink - it has no traffic of its own to learn or forward.
 *
 *	word0  entry program   0x0000a767   (the jacks have 0x0000a6a5)
 *	word1                 0x0000088f
 *	word2  untag SVID     0
 *	word9  VLAN filter    0
 *	word10                0x0fa0000e
 *	word13                0x00008008
 *	word17                0x07c00000
 *	word18                0x03c00bc0
 *	word19 attribute word 0x00000000   (the jacks have 0x96040001)
 *
 * Words 17 and 18 are the port group and the queue base.  Every other port
 * carries `(group << 24) | (queue base << 16) | (4 TX queues) | (16 RX
 * queues)` in word 17, where the group is `4 + floor(ram id / 4)` and the
 * queue base is `0x40 * (ram id % 4)`; the CPU port is the exception, and
 * its word 17 has no queue-count field at all: it is the group (7) and the
 * queue base (0xc0) only.  Word 18 is the same base with the group's index
 * in it.  Read off the vendor's own live board on 2026-10-23 with
 * `spa_port_attr_trap(cpu)`; the four UNI jacks had been right all along
 * and these two words were the whole of the difference (docs/69).
 *
 * Evidence: docs/18's table of the vendor board's live SPA memory, and
 * np_133.ko's own per-port init (spa_init, 0x27834) plus the entry-program
 * descriptor it decodes out of the microcode blob (np_downlaod_mcode,
 * 0xf1550, and the setter at 0xf141c).
 */
static const u32 zx279133_9132_cpu_words[][2] = {
	{ 0,  0x0000a767 },	/* the trap port's own entry program */
	{ 1,  0x0000088f },
	{ 2,  0x00000000 },	/* the untag SVID: none */
	{ 9,  0x00000000 },	/* the VLAN filter: none */
	{ 10, 0x0fa0000e },
	{ 13, 0x00008008 },
	{ 17, 0x07c00000 },	/* the port group and the queue base */
	{ 18, 0x03c00bc0 },
	{ 19, 0x00000000 },	/* the port attribute word: a trap carries none */
};

/*
 * The flood words, per port: the first is the broadcast flood word and the
 * second the unknown-unicast / multicast one.  PPU 1..4 are the four UNI
 * jacks and PPU 5 is XMAC0; both carry the vendor's own values.  The CPU is
 * the trap port 15 and has no flood groups of its own - it is a sink - so
 * there is no entry for it here.
 */
static const u32 zx279133_9132_vendor_flood[6][2] = {
	{ 0x04400410, 0x00400840 },
	{ 0x04800410, 0x00800880 },
	{ 0x04c00410, 0x00c008c0 },
	{ 0x05000410, 0x01000900 },
	{ 0x05400410, 0x01400940 },
	{ 0x00000000, 0x00000000 },
};

static int zx279133_9132_port_words_vendor(struct zx279133_eth *eth, u32 ppu)
{
	u32 flood = ppu - 1;
	u32 i;
	int ret;

	/* The CPU trap has its own words, and no flood groups: it is a sink. */
	if (ppu == ZX279133_9132_CONDUIT_PPU) {
		for (i = 0; i < ARRAY_SIZE(zx279133_9132_cpu_words); i++) {
			ret = zx279133_spa_indirect_write(eth, ppu,
						zx279133_9132_cpu_words[i][0],
						zx279133_9132_cpu_words[i][1]);
			if (ret)
				return ret;
		}

		return 0;
	}

	if (flood >= ARRAY_SIZE(zx279133_9132_vendor_flood))
		return 0;

	for (i = 0; i < ARRAY_SIZE(zx279133_9132_vendor_words); i++) {
		ret = zx279133_spa_indirect_write(eth, ppu,
						  zx279133_9132_vendor_words[i][0],
						  zx279133_9132_vendor_words[i][1]);
		if (ret)
			return ret;
	}
	ret = zx279133_spa_indirect_write(eth, ppu, 17,
					  zx279133_9132_vendor_flood[flood][0]);
	if (ret)
		return ret;

	return zx279133_spa_indirect_write(eth, ppu, 18,
					   zx279133_9132_vendor_flood[flood][1]);
}

/*
 * The switch engine does not read the port's attribute word out of the SPA
 * memory.  It reads the copy that lives in the MF extension table, and the
 * vendor's port_attr_def_*_set() helpers mirror every bit of the attribute
 * word into that table when they set it - which is why np_133.ko's ERAM
 * table 0x7d is described as the learning flag table with one entry per SPA
 * memory id.
 *
 * So the word above has to be mirrored, and it has to be mirrored after the
 * SPA memory has been given it: the CPU port's copy was last written with
 * the RTL8372N path's value 0x00042000, which carries neither the learning
 * enable, nor the DA lookup, nor the flood bits.  In the table the chip
 * actually reads, the CPU port was therefore a dead end - and not one frame
 * moved in either direction, which is exactly what the board did.
 */
static int zx279133_9132_mf_mirror(struct zx279133_eth *eth, u32 ppu)
{
	u32 flood = ppu - 1;
	u32 attr = ZX279133_9132_VENDOR_ATTR_WORD;
	int ret;

	/*
	 * The CPU trap is a sink, and the vendor's own board reads **zero** in
	 * its copy of the attribute word - the trap does not learn, does not
	 * look the destination address up, and takes no part in the flood
	 * groups (staging/ctl/re-cpu-trap-attr.md, and the vendor's own
	 * port_attr_def_init(), which clears SPA register 19 on all 64 ports
	 * and never gives the trap anything else).
	 *
	 * The jacks' value here is worse than different: with learning on, a
	 * frame the trap receives because its destination was unknown is
	 * learned *onto the trap*, and the frame the board then sends back to
	 * that client is looked up and found on the trap - so it leaves by
	 * the trap again and never reaches the jack.
	 */
	if (ppu == ZX279133_9132_CONDUIT_PPU)
		attr = 0x00000000;

	ret = zx279133_mf_eram_attr_set(eth, ppu, attr);
	if (ret)
		return ret;

	if (flood >= ARRAY_SIZE(zx279133_9132_vendor_flood))
		return 0;

	/*
	 * The same entry carries the port's flood words: word 1 is the
	 * unknown-DA / multicast flood word (SPA register 18) and word 2 is the
	 * broadcast one (SPA register 17), bit for bit.  The switch engine
	 * reads the flood group ids from here - the group id of a port p is
	 * p<<6, (p+0x10)<<6 and (p+0x20)<<6, and it is an index into ERAM
	 * table 0x1f - so without these two words every port's group is
	 * group 0 and every group is whatever the driver never wrote.
	 *
	 * The two words are not bit for bit copies, though, and the difference
	 * is the whole broadcast path.  The vendor's own layout of one entry is
	 * (staging/ctl/re-eram-7d.md section 2.4):
	 *
	 *	word1 bytes 4..5   the multicast flood group id
	 *	word1 bytes 6..7   the unknown-DA flood group id
	 *	word2 bytes 10..11 the broadcast flood group id
	 *
	 * and SPA register 18 holds the two group ids in that same order - the
	 * multicast one in its low half, the unknown-DA one in its high half -
	 * so word 1 is register 18 as it stands.  SPA register 17 holds the
	 * broadcast group id in its *high* half, and the entry wants it in
	 * word 2's *high* half as well: the entry is little endian, word 2 is
	 * bytes 8..11, and byte 10..11 is word 2's most significant halfword.
	 *
	 * The vendor's own setters say exactly where they put it, and they are
	 * the only evidence there is.  The entry buffer is at x29+0x48 in
	 * np_133.ko, and the three setters store their halfword with
	 *
	 *	strh w22, [x29, #0x4c]	multicast, bytes 4..5
	 *	strh w22, [x29, #0x4e]	unknown-DA, bytes 6..7
	 *	strh w22, [x29, #0x52]	broadcast, bytes 10..11
	 *
	 * (port_attr_def_mul_flood_group_id_set at .text 0xbc794,
	 * port_attr_def_unk_da_flood_group_id_set at .text 0xbc5b8 and
	 * port_attr_def_brd_flood_group_id_set at .text 0xbc970).
	 *
	 * So word 2 is register 17 as it stands, unchanged: its low halfword
	 * carries the 0x0410 the vendor's flood count lives in, and its high
	 * halfword carries the broadcast group id.  Swapping the halves here
	 * puts the group id on bytes 8..9, where no setter ever put it, and
	 * runs the broadcast flood of a port on 0x0410 - a number that is not a
	 * group id at all - instead of on (p+0x10)<<6.
	 */
	ret = zx279133_eram_window_patch(eth, ZX279133_MF_ERAM_SDT_ID, ppu, 1,
					  0xffffffff,
					  zx279133_9132_vendor_flood[flood][1]);
	if (ret)
		return ret;

	{
		u32 brd = zx279133_9132_vendor_flood[flood][0];

		return zx279133_eram_window_patch(eth, ZX279133_MF_ERAM_SDT_ID,
						  ppu, 2, 0xffffffff,
						  brd);
	}
}

/*
 * The four UNI jacks' flood groups, with the CPU port in them.
 *
 * A group holds one destination port id, in bits 0..5, and the vendor's own
 * groups hold exactly one member: the WAN port (PPU 0, which on this board is
 * the PON).  That is right for a router, where a frame the switch does not
 * know is to go out of the WAN - and wrong for this board, whose CPU port is
 * a bridge port: a unicast frame addressed to the board itself, arriving
 * before the 9132 has learned the board's address, is dropped at the 9132 and
 * never reaches the CPU at all.  The vendor's own system has the same hole
 * and does not care, because the vendor's CPU port is fed by the protocol
 * trap (ERAM table 0x18) rather than by the flood path.
 *
 * So the CPU port goes into the four jacks' groups.  The entry holds one
 * destination port, so the group becomes the CPU port instead of the WAN
 * port: the frame is delivered instead of being dropped, and the board's
 * answer teaches the 9132 the entry by ordinary source-address learning.
 *
 * This is the one place where this driver deliberately differs from the
 * vendor, and it can only make the 9132 send more frames to the port the
 * CPU is listening on.
 */
static int zx279133_9132_flood_cpu_port(struct zx279133_eth *eth)
{
	u32 p;
	int ret;

	for (p = ZX279133_9132_UNI_PPU_FIRST;
	     p <= ZX279133_9132_UNI_PPU_LAST; p++) {
		ret = zx279133_eram_table_set_bits(eth,
						   ZX279133_FLOOD_ERAM_SDT_ID,
						   p << 6, 32,
						   ZX279133_9132_CONDUIT_PPU);
		if (ret)
			return ret;
		ret = zx279133_eram_table_set_bits(eth,
						   ZX279133_FLOOD_ERAM_SDT_ID,
						   (p + 0x10) << 6, 32,
						   ZX279133_9132_CONDUIT_PPU);
		if (ret)
			return ret;
		ret = zx279133_eram_table_set_bits(eth,
						   ZX279133_FLOOD_ERAM_SDT_ID,
						   (p + 0x20) << 6, 32,
						   ZX279133_9132_CONDUIT_PPU);
		if (ret)
			return ret;
	}

	dev_info(eth->dev,
		 "9132 flood groups of PPU %u..%u point at the CPU port %u\n",
		 ZX279133_9132_UNI_PPU_FIRST, ZX279133_9132_UNI_PPU_LAST,
		 ZX279133_9132_CONDUIT_PPU);

	return 0;
}

/*
 * Bring up the 9132's UNI ports and its CPU port.
 *
 * The CPU port is PPU 15, the trap the SoC reaches the 9132 through.  Every
 * frame between the CPU and the jacks crosses it.
 */
static int zx279133_9132_port_init(struct zx279133_eth *eth)
{
	u32 ppu, attr;
	int ret;

	for (ppu = ZX279133_9132_UNI_PPU_FIRST; ppu <= ZX279133_9132_UNI_PPU_LAST;
	     ppu++) {
		ret = zx279133_9132_port_words_vendor(eth, ppu);
		if (ret)
			return ret;

		/*
		 * Read the attribute word back.  The SPA indirect path is the
		 * only way this driver talks to the 9132, so a word that does
		 * not read back as written means the write never reached the
		 * chip, and no amount of further port setup can work until
		 * that is understood.
		 */
		ret = zx279133_spa_indirect_read(eth, ppu,
						 ZX279133_PPU_ATTR_REG, &attr);
		if (ret)
			return ret;
		dev_info(eth->dev,
			 "9132 PPU %u attribute word %08x (vendor configuration)\n",
			 ppu, attr);

		/* And the copy of it in the table the switch engine reads. */
		ret = zx279133_9132_mf_mirror(eth, ppu);
		if (ret)
			return ret;
	}

	/*
	 * The CPU port: PPU 15, the trap the SoC reaches the 9132 through.
	 * Every frame between the CPU and the jacks crosses it, and the
	 * vendor's own working value for a port's words is the same as for the
	 * four jacks - entry program 0xa6a5, no SVID, the same attribute word.
	 */
	ret = zx279133_9132_port_words_vendor(eth, ZX279133_9132_CONDUIT_PPU);
	if (ret)
		return ret;
	ret = zx279133_spa_indirect_read(eth, ZX279133_9132_CONDUIT_PPU,
					 ZX279133_PPU_ATTR_REG, &attr);
	if (ret)
		return ret;
	dev_info(eth->dev,
		 "9132 PPU %u (the CPU trap) attribute word %08x\n",
		 ZX279133_9132_CONDUIT_PPU, attr);

	/* And the copy of it in the table the switch engine reads. */
	ret = zx279133_9132_mf_mirror(eth, ZX279133_9132_CONDUIT_PPU);
	if (ret)
		return ret;

	/*
	 * And the flood groups: the vendor's hold the WAN port, which drops a
	 * unicast frame addressed to the board itself on the floor before the
	 * CPU ever sees it.
	 */
	ret = zx279133_9132_flood_cpu_port(eth);
	if (ret)
		return ret;

	dev_info(eth->dev,
		 "9132 UNI ports %u..%u and the CPU port %u up (the vendor's configuration)\n",
		 ZX279133_9132_UNI_PPU_FIRST, ZX279133_9132_UNI_PPU_LAST,
		 ZX279133_9132_CONDUIT_PPU);

	return 0;
}

/*
 * The 9132's four UNI ports have their own internal PHYs, and a port whose
 * PHY is still powered down has no link at all - so this write decides
 * whether a 9132 port can ever come up.  It is also the vendor's own port
 * enable: np_133.ko's smac_set_port_enable() calls zte_set_gephy_enable(),
 * which reads the PHY's BMCR, clears bit 11 (POWERDOWN) and writes it back
 * over zx_mdio_0_read/write(), i.e. MDIO controller #0.
 */

/*
 * The bus the 9132's four internal PHYs are on: the SoC's MDIO controller #0,
 * named by the "zx279133,uni-phy-bus" phandle on the NPPT node.
 */
static struct mii_bus *zx279133_9132_uni_mdio_bus(struct zx279133_eth *eth)
{
	struct device_node *np;
	struct mii_bus *bus;

	np = of_parse_phandle(eth->dev->of_node, "zx279133,uni-phy-bus", 0);
	if (!np)
		return NULL;
	bus = of_mdio_find_bus(np);
	of_node_put(np);

	return bus;
}

/*
 * BMSR's link bit is latched: it has to be read twice, and the first read
 * clears the latch.  Returns 1 for link, 0 for no link, -1 for "the PHY did
 * not answer".
 */
static int zx279133_9132_phy_link(struct mii_bus *bus, int addr)
{
	int bmsr;

	mdiobus_read(bus, addr, MII_BMSR);
	bmsr = mdiobus_read(bus, addr, MII_BMSR);

	return (bmsr < 0) ? -1 : !!(bmsr & BMSR_LSTATUS);
}

/*
 * The whole register file of one PHY, in one line.
 *
 * Why this is here and not a guess: a PHY driver for this chip has to know
 * what the chip answers before it can be written - which registers exist,
 * which are the capability registers, whether the link status is in the
 * standard place.  The chip answers 0x84b9:0x5032 over Clause 22, and no
 * driver in this tree claims that id, so the driver has to be ours, and ours
 * has to be written against the chip's own answers rather than against an
 * assumption about them.  Reading 0x00..0x1f once, at the moment the driver
 * finds the PHYs, is how those answers get into the log.
 */
static void zx279133_9132_phy_dump(struct zx279133_eth *eth, struct mii_bus *bus,
				   int addr)
{
	char buf[256];
	int reg, len;

	len = scnprintf(buf, sizeof(buf),
			"9132 UNI PHY %02x regs 00-1f:", addr);
	for (reg = 0; reg < 32; reg++) {
		int val = mdiobus_read(bus, addr, reg);

		len += scnprintf(buf + len, sizeof(buf) - len, " %04x",
				 val < 0 ? 0xffff : (unsigned int)val);
	}
	dev_info(eth->dev, "%s", buf);
}

static void zx279133_9132_phy_enable(struct zx279133_eth *eth)
{
	struct mii_bus *bus;
	u16 found_addr[ZX279133_9132_UNI_PHY_LAST -
			ZX279133_9132_UNI_PHY_FIRST + 1];
	int addr, found = 0;
	u32 i;

	bus = zx279133_9132_uni_mdio_bus(eth);
	if (!bus) {
		dev_err(eth->dev,
			"9132 PHY bus is not registered; UNI PHYs left as they are\n");
		return;
	}

	for (addr = ZX279133_9132_UNI_PHY_FIRST;
	     addr <= ZX279133_9132_UNI_PHY_LAST; addr++) {
		int id1, id2, bmcr, bmcr_rb, link;

		id1 = mdiobus_read(bus, addr, MII_PHYSID1);
		id2 = mdiobus_read(bus, addr, MII_PHYSID2);
		if (id1 < 0 || id2 < 0)
			continue;
		if ((id1 == 0xffff && id2 == 0xffff) || (id1 == 0 && id2 == 0))
			continue;

		bmcr = mdiobus_read(bus, addr, MII_BMCR);
		if (bmcr < 0)
			continue;
		/*
		 * Linux spells the powerdown bit BMCR_PDOWN (bit 11) - there is
		 * no BMCR_POWERDOWN - and BMCR has no "enable" bit at all, so
		 * there is no BMCR_ENABLE either.  Powering a PHY up is
		 * clearing PDOWN and enabling auto-negotiation, which is what
		 * the vendor's zte_set_gephy_enable() does.
		 */
		bmcr &= ~BMCR_PDOWN;
		bmcr |= BMCR_ANENABLE;
		mdiobus_write(bus, addr, MII_BMCR, (u16)bmcr);

		/*
		 * Read the control register back.  A write that does not land
		 * and a PHY that stays powered down look the same from the
		 * outside, and only the read-back tells them apart - and this
		 * word is the only place the write can be seen at all.
		 */
		bmcr_rb = mdiobus_read(bus, addr, MII_BMCR);
		if (bmcr_rb < 0)
			bmcr_rb = -1;

		link = zx279133_9132_phy_link(bus, addr);

		/*
		 * The full register file, once, right after the PHY has been
		 * powered up: it is the only place the chip's own answers are
		 * written down, and they are what a PHY driver for it has to
		 * be written against.
		 */
		zx279133_9132_phy_dump(eth, bus, addr);

		found_addr[found++] = addr;
		if (bmcr_rb < 0)
			dev_info(eth->dev,
				 "9132 UNI PHY %02x: id %04x:%04x, BMCR %04x, read-back failed (%d), link %d\n",
				 addr, id1, id2, bmcr & 0xffff, bmcr_rb, link);
		else
			dev_info(eth->dev,
				 "9132 UNI PHY %02x: id %04x:%04x, BMCR %04x->%04x, link %d\n",
				 addr, id1, id2, bmcr & 0xffff, (u16)bmcr_rb, link);
	}

	if (!found) {
		dev_err(eth->dev,
			"no 9132 UNI PHY answered on %02x..%02x\n",
			ZX279133_9132_UNI_PHY_FIRST,
			ZX279133_9132_UNI_PHY_LAST);
		return;
	}

	/*
	 * The copper side needs a moment to negotiate, and a PHY that was
	 * just powered up reports "no link" for a while even with the cable
	 * in.  One early read would call a slow port a dead one, so read the
	 * link again after the settle - this is the reading that says which
	 * of the four jacks the test PC is actually plugged into.
	 */
	msleep(1000);
	for (i = 0; i < (u32)found; i++)
		dev_info(eth->dev, "9132 UNI PHY %02x: link after settle %d\n",
			 found_addr[i], zx279133_9132_phy_link(bus, found_addr[i]));
}

/*
 * The 9132's per-port enable word: SPA indirect address 0 of a PPU port's
 * memory.  docs/15 lists it as the first difference between the vendor's
 * switch.ko and mainline - "port enable, written when the netdev is opened" -
 * and mainline has never written it.
 *
 * This round only reads it.  A read has no side effects and says what the
 * bootloader left behind, and a word that reads back as something else than
 * what was written is the one thing that distinguishes "the write never
 * reached the chip" from "the chip was never told anything" - the two look
 * identical from the outside.
 */
static void zx279133_9132_port_enable_read(struct zx279133_eth *eth)
{
	u32 ppu, val;
	int ret;

	for (ppu = 0; ppu <= 5; ppu++) {
		ret = zx279133_spa_indirect_read(eth, ppu, 0, &val);
		if (ret) {
			dev_info(eth->dev,
				 "9132 PPU %u enable word: read failed (%d)\n",
				 ppu, ret);
			continue;
		}
		dev_info(eth->dev, "9132 PPU %u enable word %08x\n", ppu, val);
	}
}

/*
 * WAN port bring-up recovered from np.ko tm_sw_lan_up_pon_set(): when the
 * switch-side WAN port (5, PPU memory id 6) comes up the vendor driver
 * copies the CPU port's SPA start-PC attribute tables (1/3/4 at memory
 * address 0), flags the port as the WAN port in the SPA attribute table
 * and the MF extension table, installs the WAN port's 128-entry PRO table
 * (SE ERAM table 4, 16-bit entries), and enables the LAN-up port in the
 * NPPT globals. The CPU-originated frames then carry port 6.
 */
static int zx279133_mf_port_startpc_copy(struct zx279133_eth *eth,
					 u32 dst_mem, u32 src_mem)
{
	u32 src, dst;
	int ret;

	ret = zx279133_spa_indirect_read(eth, src_mem, 0, &src);
	if (ret)
		return ret;
	ret = zx279133_spa_indirect_read(eth, dst_mem, 0, &dst);
	if (ret)
		return ret;
	dst = (dst & ~GENMASK(23, 0)) | (src & GENMASK(23, 0));

	return zx279133_spa_indirect_write(eth, dst_mem, 0, dst);
}

/*
 * The NPPT's own protocol table: 16-bit entries, 64 logical ports of 128
 * entries each, so 16 blocks - a block is 128 bits - per port.  Its base
 * is the one the allocation gave it.
 */
#define ZX279133_PRO_BLOCKS_PER_PORT	16
#define ZX279133_LAN_INGRESS_PPU_PORT	0
#define ZX279133_LAN_PPU_PORT		5
#define ZX279133_WAN_PPU_PORT		6
#define ZX279133_LAN_COMPANION_PORT	10
/* PC 10 uses the transport VID of a LAN port as its logical PPU port. */
#define ZX279133_LAN_VPORT_FIRST	ZX279133_LAN_TRANSPORT_VID_MIN
#define ZX279133_LAN_VPORT_LAST		ZX279133_LAN_TRANSPORT_VID_MAX

static const u32 zx279133_wan_proto_table[ZX279133_PRO_BLOCKS_PER_PORT]
					     [ZX279133_SMMU0_WORDS] = {
	{ 0x7c3c7c3c, 0x30307c3c, 0x70303030, 0x70307030 },
	{ 0x70307030, 0x70307030, 0x70307030, 0x70307030 },
	{ 0x70307030, 0x70307030, 0x70307030, 0x70307030 },
	{ 0x70307030, 0x70307030, 0x70307030, 0x70307434 },
	{ 0x74347030, 0x70307030, 0x70307030, 0x70307030 },
	{ 0x70307030, 0x70307030, 0x70303030, 0x30303030 },
	{ 0x30307030, 0x70307030, 0x70307030, 0x70307030 },
	{ 0x70307030, 0x70307030, 0x78387838, 0x70307030 },
	{ 0x70307030, 0x70307030, 0x70307030, 0x30307030 },
	{ 0x2c2c7030, 0x2c2c6022, 0x70302c2c, 0x6c2c6c2c },
	{ 0x70307030, 0x70307030, 0x70306030, 0x70307030 },
	{ 0x70307030, 0x70307030, 0x70307030, 0x70307030 },
	{ 0x6c2c6c2c, 0x70307030, 0x70307030, 0x70307030 },
	{ 0x70307030, 0x70307030, 0x70307030, 0x70307030 },
	{ 0x70307030, 0x70307030, 0x70307030, 0x70307030 },
	{ 0x70307030, 0x70307030, 0x70307030, 0x70307030 },
};

static const u32 zx279133_lan_proto_table[ZX279133_PRO_BLOCKS_PER_PORT]
					     [ZX279133_SMMU0_WORDS] = {
	{ 0x40024002, 0x14144002, 0x58181818, 0x58185818 },
	{ 0x58185818, 0x58185818, 0x58185818, 0x58185818 },
	{ 0x58185818, 0x58185818, 0x58185818, 0x58185818 },
	{ 0x58185818, 0x58185818, 0x58185818, 0x58185010 },
	{ 0x50105818, 0x58185818, 0x58185818, 0x58185818 },
	{ 0x58185818, 0x58185818, 0x58181818, 0x18181818 },
	{ 0x18185818, 0x58185818, 0x58185818, 0x58185818 },
	{ 0x58185818, 0x58185818, 0x58185818, 0x58185818 },
	{ 0x58185818, 0x58185818, 0x58185818, 0x18185818 },
	{ 0x08085818, 0x48084002, 0x58180808, 0x48084808 },
	{ 0x54145414, 0x58185414, 0x58184014, 0x58185818 },
	{ 0x58185818, 0x58185818, 0x58185818, 0x58185818 },
	{ 0x48084808, 0x58185818, 0x58185818, 0x58185818 },
	{ 0x58185818, 0x58185818, 0x58185818, 0x58185818 },
	{ 0x58185818, 0x58185818, 0x58185818, 0x58185818 },
	{ 0x58185818, 0x58185818, 0x58185818, 0x58185818 },
};

/*
 * The 9132's own protocol action table.
 *
 * The switch decides what to do with a frame of a given protocol on a given
 * port by looking in that port's reason-action table, and whether the frame
 * is forwarded or trapped to the CPU is what that table says.  The vendor's
 * switch.ko installs this table on every switch port: 77 (protocol, action)
 * pairs copied out of its own .rodata, and the action lands in the low two
 * bits of the entry.
 *
 * It is the only way a frame from a LAN jack reaches the CPU at all.  The
 * vendor's flood group holds the WAN port and nothing else
 * (sw_init_flood_ctrl), so a frame whose destination is not in the MAC
 * table is never flooded towards the CPU - it has to be trapped.  The
 * vendor's own counters show the trap firing exactly as many times as the
 * test PC sent frames, and the counter for the frames that arrived on the
 * PC's own jack and were output again towards the jacks - the same seven.
 *
 * This table is the switch's own, and it is not the NPPT's protocol table
 * above: the switch looks up the frame's protocol in the table of the port
 * it came in on, in SE ERAM table 0x18, and the entry is one word per
 * (port, protocol), at the entry (port << 7) | protocol.
 *
 * The vendor's own code writes it that way: tm_port_protocol_pktdeal_set()
 * puts the action into the low two bits of a byte and hands it to
 * port_reason_action_table_set(), which computes the index
 * reason | (port << 7) and writes one word.  The action is stored as it
 * is - 2 means "give this frame to the CPU" - and the pairs themselves are
 * the 77 (protocol, action) pairs in switch.ko's own .rodata.
 *
 * 0xff means the vendor's table has no entry for that protocol, so nothing
 * is written for it and the chip keeps what it had.
 */
static const u8 zx279133_9132_proto_action[128] = {
	2, 2, 255, 255, 255, 255, 255, 255,
	2, 2, 2, 2, 2, 2, 255, 255,
	2, 2, 2, 2, 2, 2, 255, 2,
	2, 2, 2, 2, 2, 2, 2, 2,
	2, 2, 2, 2, 2, 2, 2, 2,
	2, 2, 2, 2, 255, 2, 255, 255,
	2, 255, 2, 2, 2, 0, 0, 0,
	0, 0, 0, 0, 0, 0, 0, 0,
	0, 0, 0, 0, 0, 0, 0, 0,
	2, 0, 2, 2, 0, 255, 255, 255,
	2, 2, 2, 0, 2, 2, 2, 2,
	2, 255, 255, 255, 255, 255, 255, 255,
	2, 2, 2, 255, 255, 255, 255, 255,
	255, 255, 255, 255, 255, 255, 255, 255,
	255, 255, 255, 255, 255, 255, 255, 255,
	255, 255, 255, 255, 255, 255, 255, 255,
};

static int
zx279133_proto_table_write(struct zx279133_eth *eth, u32 port,
			   const u32 table[][ZX279133_SMMU0_WORDS])
{
	u32 block = zx279133_eram_base_of(ZX279133_PRO_ERAM_SDT_ID) +
		    port * ZX279133_PRO_BLOCKS_PER_PORT;
	u32 proto;
	int i;
	int ret;

	for (i = 0; i < ZX279133_PRO_BLOCKS_PER_PORT; i++) {
		ret = zx279133_smmu0_write(eth, (block + i) << 7,
					   table[i],
					   ZX279133_SMMU0_WORDS,
					   ZX279133_SMMU0_CMD_WRITE);
		if (ret)
			return ret;
	}

	/*
	 * The same table in the form the switch engine reads it: SE ERAM
	 * table 0x18, two bits per (port, protocol) entry.  The action is
	 * the low two bits of the 16-bit entry, and the port is the chip's
	 * own port number - 1 to 4 are the 9132's UNI jacks, 5 is XMAC0,
	 * the 9132's CPU port.
	 */
	for (proto = 0; proto < 128; proto++) {
		u32 half = table[proto / 8][(proto % 8) / 2];
		u32 action = (half >> ((proto % 2) * 16)) & 3;

		ret = zx279133_eram_table_set_bits(eth,
						   ZX279133_SWITCH_PROTO_SDT_ID,
						   proto | (port << 7), 2,
						   action);
		if (ret)
			return ret;
	}

	return 0;
}

/*
 * Give the switch its protocol action table.
 *
 * Without it the switch has no idea what to do with an ARP frame that
 * arrives on a jack: it is neither forwarded (the flood group holds the WAN
 * port and nothing else) nor trapped to the CPU, so it is dropped and the
 * client never sees an answer.
 *
 * The table is SE ERAM table 0x18: two bits per entry, 8192 entries, one
 * entry per (port, protocol).  The vendor's index is reason | (port << 7)
 * and the value is the action itself - 2 hands the frame to the CPU, 0
 * leaves it to the normal path.  The ports are the 9132's own: 1 to 4 are
 * the four UNI jacks and 5 is the CPU port the SoC talks to.
 *
 * The port number in the index is the one thing here that the vendor's own
 * code does not use consistently.  Its load path (switch.ko's
 * sw_init_port_protocol_action) writes the table for the RAW switch ports
 * 0..7, and its run-time path (np_133.ko's zxic_api_set_port_proation)
 * writes it for the CHIP PPU number, which is the raw number plus one for
 * 0..6.  The two spaces are one apart, and the table has a group of 128
 * entries per port, so a frame that arrives on a jack looks up a group that
 * differs by one whole group between the two readings.
 *
 * This driver used to write the chip PPU numbers 1..7 and nothing else,
 * which leaves the group 0 (raw port 0) empty.  Writing all eight groups
 * costs 128 entries and covers both readings: the vendor's own table B
 * goes to 1..7 and to 0x10..0x30, and the group that raw port 0 owns is
 * covered as well.  A frame that looked up a group this driver had left
 * empty found no action at all, was neither forwarded nor trapped, and was
 * dropped inside the chip - which is what the bench saw: the PC's frames
 * reached the 9132 and no netdev ever counted a byte.
 */
/*
 * One protocol action, in every port group the port has.
 *
 * The 9132 numbers its ports in three banks: a port p is also p + 0x10 and
 * p + 0x20.  The vendor's own flood words say so - a port p carries the
 * flood group ids p<<6, (p+0x10)<<6 and (p+0x20)<<6, and those ids are the
 * port numbers multiplied by 64 - and the protocol action table is indexed
 * by (port << 7) | protocol, so a frame that arrives on a jack looks its
 * action up in one of three groups.
 *
 * The vendor's load path writes all of them: the groups 0..7 and the groups
 * 0x10..0x30.  This driver wrote the groups 0..7 only, so the groups a frame
 * really looks up were empty - and a frame that finds no action is neither
 * forwarded nor trapped, it is dropped inside the chip.  That is what the
 * bench saw: the frame arrived at the SMAC, the SDET, the SIPC, the SPA and
 * the SMCT, and the SMCT never handed it on, because it had no action for
 * it.
 */
static int zx279133_9132_proto_action_set(struct zx279133_eth *eth, u32 port,
					  u32 proto, u32 action)
{
	static const u32 bank[] = { 0x00, 0x10, 0x20 };
	u32 b;
	int ret;

	for (b = 0; b < ARRAY_SIZE(bank); b++) {
		ret = zx279133_eram_table_set_bits(eth,
					ZX279133_SWITCH_PROTO_SDT_ID,
					proto | ((port + bank[b]) << 7), 2,
					action);
		if (ret)
			return ret;
	}

	return 0;
}

static int zx279133_9132_proto_init(struct zx279133_eth *eth)
{
	u32 base = zx279133_eram_base_of(ZX279133_SWITCH_PROTO_SDT_ID);
	u32 port, proto;
	int ret;

	if (!base) {
		dev_err(eth->dev, "the switch's protocol table has no base\n");
		return -EINVAL;
	}

	for (port = 0; port <= 7; port++) {
		for (proto = 0; proto < 128; proto++) {
			u32 action = zx279133_9132_proto_action[proto];

			if (action == 0xff)
				continue;
			ret = zx279133_9132_proto_action_set(eth, port, proto,
							     action);
			if (ret)
				return ret;
		}
	}

	dev_info(eth->dev,
		 "9132 switch ports 0..7 have the protocol action table at ERAM block %u (ARP trapped to the CPU)\n",
		 base);

	return 0;
}

/*
 * The tables that say where a frame the switch hands to the CPU goes.
 *
 * Without them the switch forwards happily and the frame arrives at a
 * queue the CPU is not listening on, which looks exactly like a dead
 * wire.  The three writes below are the vendor's own, and every value
 * comes out of the vendor's code rather than out of thin air:
 *
 *   0x22  the global config table.  np_user_table_init() calls
 *         glb_cfg_table_cpu_base_qid_set(0x193) and nothing else writes
 *         it; the setter puts the value in bits 1..9 of the halfword at
 *         byte 14 of entry 0.  The table's entries are 128 bits wide, so
 *         that halfword is word 3 of the entry, bits 17..25.
 *
 *   0x27  the outport map.  outport_and_real_port_map_table_init()
 *         writes outport 15 - the CPU's own outport - with the real
 *         port 15 and the queue id 0x193, which is the base queue id
 *         above.  The entry is 16 bits wide: the real port is in bits
 *         0..5 and the queue id in bits 6..14.
 *
 *   0x19  the reason map.  sw_init_omci_trap_qid_init() calls
 *         zxic_api_sw_reason_map_cpu_qid_set(direct=0, reason=0, qid=7),
 *         so reason 0 - OMCI - is delivered to CPU queue 7.  The queue
 *         id is in bits 0..4 of byte 3 of the entry, and the entries are
 *         32 bits wide, so entry 0 is word 0 of the window.
 */

#define ZX279133_VENDOR_CPU_BASE_QID	0x193
#define ZX279133_VENDOR_OMCI_QID	7

/*
 * The vendor's ERAM 0x19 - the reason map - in full.
 *
 * The index of an entry is cpu_qid + (reason << 7), so the table holds 128
 * cpu_qids per reason, and the value is the five-bit CPU queue id in bits
 * 24..28 of a 32-bit entry.
 *
 * The vendor's init path programs reason 0 and reason 1 only, 127 cpu_qids
 * each - cpu_qid 50 is the one it never writes - and the queue id for reason 1
 * is always the reason 0 one plus eight.  tm_reason_map_cpu_qid_set() at
 * np_133.ko 0x1153b4 is the only writer; the table and the values are in
 * staging/ctl/re-idm-queue-map.md.
 *
 * 0xff marks the entry the vendor leaves alone.  The ERAM comes up empty after
 * a power cut, so that entry reads back as zero, and zero is not a queue id
 * the vendor ever uses for a trapped LAN frame.
 */
static const u8 zx279133_9132_reason_qid[128] = {
	7, 7, 6, 4, 4, 4, 4, 4, 6, 6, 6, 6, 6, 4, 4, 4,
	6, 6, 6, 6, 6, 6, 6, 4, 4, 4, 4, 6, 6, 6, 6, 6,
	6, 6, 6, 6, 6, 6, 6, 6, 2, 2, 6, 6, 6, 6, 5, 6,
	6, 6, 6, 3, 0xff, 4, 4, 4, 4, 4, 4, 4, 4, 4, 4, 4,
	5, 5, 5, 4, 4, 4, 4, 3, 6, 6, 6, 2, 1, 4, 4, 4,
	3, 3, 3, 5, 4, 4, 3, 3, 4, 4, 4, 4, 4, 4, 4, 4,
	1, 1, 2, 4, 4, 4, 4, 4, 4, 4, 4, 4, 4, 4, 4, 4,
	4, 4, 4, 4, 4, 4, 4, 4, 4, 4, 4, 4, 4, 4, 4, 4,
};

/*
 * The vendor's ERAM 0x27 - the outport map - in full.
 *
 * The index is the outport, 0 to 63, and the entry is 16 bits: the real port
 * in bits 0..5 and the queue id in bits 6..14.
 *
 * The vendor's outport_and_real_port_map_table_init() (np_133.ko 0xc06c8)
 * writes all 64 entries, and it does *not* put the real port 15 everywhere:
 *
 *   outport  0..14  ->  real port = outport,  queue id = 0
 *   outport 15      ->  real port = 15,        queue id = 0x193 (403)
 *   outport 16..23  ->  real port = 15,        queue id = 0x1a3 (419) + delta
 *   outport 24..48  ->  real port = outport,  queue id = 0
 *   outport 49      ->  real port = 49,        queue id = 0x1b1 (433)
 *   outport 50      ->  real port = 50,        queue id = 0x1b3 (435)
 *   outport 51      ->  real port = 51,        queue id = 0x1b5 (437)
 *   outport 52..63  ->  real port = outport,  queue id = 0
 *
 * This driver used to write the real port 15 everywhere and the queue id
 * 0x1a3 everywhere, which is what the vendor's *print* says - but the print
 * only covers outports 16..23 (the ones that are the IDM's own), so the
 * other 61 entries were read off the wrong line of the log.
 *
 * Both columns matter.  The queue id 0x1a3 is 419, which is outside the
 * RED's 401 queues (0..400), so a frame the switch egresses on any outport
 * other than 15..23 was given a queue the RED cannot deliver on - which is
 * what the RED's `req_que_disc` and `drop_pkt` flags report.  And the real
 * port is the physical port the frame leaves by: a frame the 9132 forwards
 * to a UNI jack was aimed at the CPU trap instead.
 */
#define ZX279133_VENDOR_OUTPORT_CPU_REAL_PORT	0x0f
#define ZX279133_VENDOR_OUTPORT_IDM_FIRST	0x10
#define ZX279133_VENDOR_OUTPORT_IDM_LAST	0x17
#define ZX279133_VENDOR_OUTPORT_IDM_QID		0x1a3
#define ZX279133_VENDOR_OUTPORT_CPU_QID	0x193

static u32 zx279133_9132_outport_real(u32 outport)
{
	/* The CPU trap and the IDM's own outports all leave by port 15. */
	if (outport >= ZX279133_VENDOR_OUTPORT_CPU_REAL_PORT &&
	    outport <= ZX279133_VENDOR_OUTPORT_IDM_LAST)
		return ZX279133_VENDOR_OUTPORT_CPU_REAL_PORT;

	return outport;
}

static u32 zx279133_9132_outport_qid(u32 outport)
{
	switch (outport) {
	case 0x0f:
		return ZX279133_VENDOR_OUTPORT_CPU_QID;
	case 0x31:
		return 0x1b1;
	case 0x32:
		return 0x1b3;
	case 0x33:
		return 0x1b5;
	default:
		/*
		 * The IDM's own outports get the base queue plus a per-port
		 * delta the vendor reads out of a table it fills at run time;
		 * the delta is zero here, which is the base value.
		 */
		if (outport >= ZX279133_VENDOR_OUTPORT_IDM_FIRST &&
		    outport <= ZX279133_VENDOR_OUTPORT_IDM_LAST)
			return ZX279133_VENDOR_OUTPORT_IDM_QID;
		return 0;
	}
}

/*
 * Write one 128-bit ERAM block.  The block number is the one the console's
 * "er" and "ew" commands use, so a table written here can be read back with
 * them.
 */
static int zx279133_9132_eram_block_write(struct zx279133_eth *eth, u32 block,
					  const u32 words[ZX279133_SMMU0_WORDS])
{
	return zx279133_smmu0_write(eth, block << 7, words,
				     ZX279133_SMMU0_WORDS,
				     ZX279133_SMMU0_CMD_WRITE);
}

/*
 * The reason map, the vendor's way.
 *
 * A frame the switch hands to the CPU carries a reason, and this table says
 * which of the SoC's CPU queues it is delivered on.  The vendor writes 254
 * entries; this driver used to write one - reason 0, cpu_qid 0 - and left the
 * other 253 at zero, which is not a queue id the switch can deliver on.  The
 * whole table has to be there, because the cpu_qid of a trapped LAN frame is
 * not the one this driver happened to guess.
 */
static int zx279133_9132_reason_map_init(struct zx279133_eth *eth)
{
	u32 base = zx279133_eram_base_of(ZX279133_QID_ERAM_SDT_ID);
	u32 reason, block, cpu, i;
	u32 words[ZX279133_SMMU0_WORDS];
	int ret;

	if (!base)
		return dev_err_probe(eth->dev, -EINVAL,
				     "the reason map has no base\n");

	for (reason = 0; reason < 2; reason++) {
		for (block = 0; block < 32; block++) {
			for (i = 0; i < ZX279133_SMMU0_WORDS; i++)
				words[i] = 0;
			for (i = 0; i < ZX279133_SMMU0_WORDS; i++) {
				cpu = block * ZX279133_SMMU0_WORDS + i;

				if (zx279133_9132_reason_qid[cpu] == 0xff)
					continue;
				words[i] = (zx279133_9132_reason_qid[cpu] +
					    (reason ? 8 : 0)) << 24;
			}
			ret = zx279133_9132_eram_block_write(eth,
					base + reason * 32 + block, words);
			if (ret)
				return dev_err_probe(eth->dev, ret,
						     "the reason map did not go into ERAM table 0x19\n");
		}
	}

	dev_info(eth->dev,
		 "9132 reason map: ERAM table 0x19 at block %u holds the vendor's 254 entries (reasons 0 and 1)\n",
		 base);

	return 0;
}

/*
 * The outport map, the vendor's way.
 *
 * The switch says "this frame goes out of outport N"; this table says which
 * real port that is and which queue it lands on.  The vendor writes all 64
 * entries; this driver used to write one - outport 15 - and left the other
 * 63 at zero, so a frame that came out of any other outport had no real port
 * and no queue.
 */
static int zx279133_9132_outport_map_init(struct zx279133_eth *eth)
{
	u32 base = zx279133_eram_base_of(ZX279133_OUTPORT_ERAM_SDT_ID);
	u32 block, outport, i;
	u32 words[ZX279133_SMMU0_WORDS];
	int ret;

	if (!base)
		return dev_err_probe(eth->dev, -EINVAL,
				     "the outport map has no base\n");

	for (block = 0; block < 8; block++) {
		for (i = 0; i < ZX279133_SMMU0_WORDS; i++)
			words[i] = 0;
		for (i = 0; i < 8; i++) {
			u32 value;

			outport = block * 8 + i;
			value = zx279133_9132_outport_real(outport) |
				(zx279133_9132_outport_qid(outport) << 6);
			if (i & 1)
				words[i / 2] |= value << 16;
			else
				words[i / 2] |= value;
		}
		ret = zx279133_9132_eram_block_write(eth, base + block, words);
		if (ret)
			return dev_err_probe(eth->dev, ret,
					     "the outport map did not go into ERAM table 0x27\n");
	}

	dev_info(eth->dev,
		 "9132 outport map: ERAM table 0x27 at block %u holds the vendor's 64 entries\n",
		 base);

	return 0;
}

static int zx279133_9132_cpu_path_init(struct zx279133_eth *eth)
{
	int ret;

	/*
	 * The global config table.  np_user_table_init() calls
	 * glb_cfg_table_cpu_base_qid_set(0x193), and nothing else writes
	 * it; the setter puts the value in bits 1..9 of the halfword at
	 * byte 14 of entry 0.  The table's entries are 128 bits wide, so
	 * that halfword is word 3 of the entry, bits 17..25.
	 *
	 * Two more fields of the same word come from the same load path:
	 * glb_cfg_table_mul_ip_mode_set(1) puts bit 5 of byte 13 - bit 13
	 * of word 3 - and glb_cfg_table_ipv6_crc32_mode_set(1) puts bit 0
	 * of byte 14 - bit 16 of word 3.
	 */
	ret = zx279133_eram_window_patch(eth, ZX279133_GLBCFG_ERAM_SDT_ID, 0, 3,
					  (0x1ffu << 17) | (1u << 16) | (1u << 13),
					  (ZX279133_VENDOR_CPU_BASE_QID << 17) |
					  (1u << 16) | (1u << 13));
	if (ret)
		return dev_err_probe(eth->dev, ret,
				     "the CPU base queue id did not go into ERAM table 0x22\n");

	ret = zx279133_9132_outport_map_init(eth);
	if (ret)
		return ret;

	ret = zx279133_9132_reason_map_init(eth);
	if (ret)
		return ret;

	dev_info(eth->dev,
		 "9132 CPU path: base queue %u, outport 15 -> real port 15 queue %u, reason 0 -> queue %u\n",
		 ZX279133_VENDOR_CPU_BASE_QID, ZX279133_VENDOR_CPU_BASE_QID,
		 ZX279133_VENDOR_OMCI_QID);

	return 0;
}

/*
 * The tables that decide whether a port may talk to another port and
 * whether a frame is checked for a VLAN on the way out.
 *
 * The ERAM is DRAM: it comes up empty after a power cut, and the vendor
 * fills these tables in its own load path, so a driver that leaves them
 * alone leaves them empty.  An empty isolation matrix says that no port
 * may talk to any other, and an empty output-VLAN-check table says that
 * nothing is checked - the two are opposites, and only one of them lets
 * a frame through.
 *
 * Every value here is the vendor's:
 *
 *   0x1b  the isolation matrix, 64 by 64 ports, one bit each, indexed
 *         src | (dst << 6).  sw_init_port_isolate() writes the whole of
 *         it: the diagonal is 1 and everything else is 0.
 *
 *   0x23  the output VLAN check, one bit per port.
 *         sw_set_default_vlan_check_ctrl() turns it on for ports 0..7
 *         and 0x10..0x30, and only turns it off again for ports 5 and 6
 *         when the XMACs are extended - and on this board they are not:
 *         the live system's own port attribute word for those two ports
 *         has bit 19 clear, and that bit is the extend flag.
 *
 *   0x24  the port bind, one bit per port.  sw_init_port_route_mode()
 *         writes 0 into all of them.
 *
 *   0x25  the port PVID, 16 bits per port.  The live system reads 0 for
 *         every one of its ports, and so does the vendor's own VLAN
 *         setup: zxic_api_sw_vlan_set_port_vlancfg(port, 0, 0, 0xfff).
 */
#define ZX279133_ISOLATE_ERAM_SDT_ID	0x1b
#define ZX279133_OUTVLAN_ERAM_SDT_ID	0x23
#define ZX279133_PORTBIND_ERAM_SDT_ID	0x24
#define ZX279133_PVID_ERAM_SDT_ID	0x25

static int zx279133_9132_isolate_init(struct zx279133_eth *eth)
{
	u32 base = zx279133_eram_base_of(ZX279133_ISOLATE_ERAM_SDT_ID);
	u32 w;
	int ret;

	if (!base)
		return -EINVAL;

	/*
	 * The index is src | (dst << 6), so the 64 sources of one
	 * destination are 64 consecutive bits and two destinations share a
	 * 128-bit window.  One read and one write per window is enough.
	 *
	 * Destination 2w owns the first half of the window: its source s is
	 * bit s of it, so its diagonal (src == dst) is bit 2w.
	 *
	 * Destination 2w+1 owns the second half: its source s is bit 64+s of
	 * the window, so its diagonal is bit 64 + 2w + 1 = 2w + 65.  It was
	 * written as w + 65, which is the diagonal of destination 2w+1 only
	 * for w == 0; for every other w it left the diagonal of the odd
	 * destinations clear and set a bit that belongs to some other pair -
	 * (8, 15) among them, which is a port and the CPU's own.
	 */
	for (w = 0; w < 32; w++) {
		u32 window[ZX279133_SMMU0_WORDS];
		u32 addr = (base << 7) + (w << 7);
		u32 b0 = 2 * w;
		u32 b1 = 2 * w + 65;

		ret = zx279133_smmu0_read(eth, addr, window, ARRAY_SIZE(window),
					  ZX279133_SMMU0_CMD_READ);
		if (ret)
			return ret;

		window[0] = 0;
		window[1] = 0;
		window[2] = 0;
		window[3] = 0;
		window[b0 / 32] |= 1u << (b0 % 32);
		window[b1 / 32] |= 1u << (b1 % 32);

		ret = zx279133_smmu0_write(eth, addr, window, ARRAY_SIZE(window),
					   ZX279133_SMMU0_CMD_WRITE);
		if (ret)
			return ret;
	}

	dev_info(eth->dev,
		 "9132 isolation matrix written: the diagonal only, %u ports\n",
		 64);

	return 0;
}

static int zx279133_9132_switch_tables_init(struct zx279133_eth *eth)
{
	u32 p;
	int ret;

	/* The output VLAN check: on for every port the vendor turns it on. */
	for (p = 0; p < 8; p++) {
		ret = zx279133_eram_table_set_bits(eth,
						    ZX279133_OUTVLAN_ERAM_SDT_ID,
						    p, 1, 1);
		if (ret)
			return ret;
	}
	for (p = 0x10; p < 0x31; p++) {
		ret = zx279133_eram_table_set_bits(eth,
						    ZX279133_OUTVLAN_ERAM_SDT_ID,
						    p, 1, 1);
		if (ret)
			return ret;
	}

	/* The port bind: no port is bound to another. */
	for (p = 0; p < 64; p++) {
		ret = zx279133_eram_table_set_bits(eth,
						    ZX279133_PORTBIND_ERAM_SDT_ID,
						    p, 1, 0);
		if (ret)
			return ret;
	}

	/* The PVID: none, because this is a transparent bridge. */
	for (p = 0; p < 8; p++) {
		ret = zx279133_eram_table_set_bits(eth,
						    ZX279133_PVID_ERAM_SDT_ID,
						    p, 16, 0);
		if (ret)
			return ret;
	}
	for (p = 0x10; p < 0x31; p++) {
		ret = zx279133_eram_table_set_bits(eth,
						    ZX279133_PVID_ERAM_SDT_ID,
						    p, 16, 0);
		if (ret)
			return ret;
	}

	return 0;
}

static int zx279133_spa_pkt_type_runtime(struct zx279133_eth *eth)
{
	static const u16 idx[] = { 0, 1, 128, 129 };
	int i;

	for (i = 0; i < ARRAY_SIZE(idx); i++) {
		int ret = zx279133_spa_indirect_write(eth, 0x40, idx[i],
						      0x00f7c000);

		if (ret)
			return ret;
	}

	return 0;
}

/*
 * The logical LAN ports of PC 10. Their protocol actions are those of the
 * LAN port, except that reasons 30 and 33 (frames for the router itself)
 * also select a packet-rate limit set, and their MF extension entries carry
 * the per-port user-defined areas the factory driver copies from its SPA
 * attributes (words 1..3 = attributes 69..71).
 *
 * The port attribute word is the first word of the entry, the way the
 * vendor's port_attr_def_*_set() helpers leave it.
 */
static const u32 zx279133_lan_vport_mf[][ZX279133_SMMU0_WORDS] = {
	{ 0x1c8c2800, 0x2f235600, 0x00000a38, 0 },	/* VID 59, lan4 */
	{ 0x1c8c2800, 0x2fc36000, 0x00000a60, 0 },	/* VID 60, lan3 */
	{ 0x1c8c2800, 0x30636a00, 0x00000a88, 0 },	/* VID 61, lan2 */
	{ 0x1c8c2800, 0x31037400, 0x00000ab0, 0 },	/* VID 62, lan1 */
};

/* SDT 6: one entry per logical port; bit 31 of word 3 marks a LAN port. */
#define ZX279133_PORT_TYPE_LAN			BIT(31)
/*
 * The port attribute word of a WAN-type logical port, as the factory system
 * sets it for VID 59 (lan4) when that port becomes its "WAN2": 0x1c8c2800
 * turns into 0x148e2000, the kind of value of the WAN port itself
 * (0x148e0000).
 * Measured for VID 59 only; the same flip for 60..62 is an inference.
 */
#define ZX279133_LAN_VPORT_MF_WAN_FLIP		0x08020800

/*
 * Make a logical LAN port LAN-type or WAN-type. The factory system turns a
 * LAN port into a second WAN this way: it gives the port the protocol
 * actions of the WAN port, flips its MF extension entry to the WAN kind and
 * clears its LAN mark in SDT 6. Frames from it then take the WAN path of the
 * PPU: the PPPoE header is removed on the way in and added on the way out,
 * neither of which a LAN-type port does.
 */
static int zx279133_lan_vport_mode_write(struct zx279133_eth *eth, u16 vport,
					 bool wan)
{
	u32 table[ZX279133_PRO_BLOCKS_PER_PORT][ZX279133_SMMU0_WORDS];
	u32 addr = (zx279133_eram_base_of(ZX279133_PORT_TYPE_ERAM_SDT_ID) + vport) << 7;
	u32 mf[ZX279133_SMMU0_WORDS], type[ZX279133_SMMU0_WORDS];
	int ret;

	memcpy(table, wan ? zx279133_wan_proto_table : zx279133_lan_proto_table,
	       sizeof(table));
	/*
	 * Reasons 30 and 33 (frames for the router itself) also select a
	 * packet-rate limit set. Reason r is half r % 2 of word (r % 8) / 2
	 * of block r / 8.
	 */
	table[30 / 8][(30 % 8) / 2] |= BIT(1);
	table[33 / 8][(33 % 8) / 2] |= BIT(17);
	ret = zx279133_proto_table_write(eth, vport, table);
	if (ret)
		return ret;

	memcpy(mf, zx279133_lan_vport_mf[vport - ZX279133_LAN_VPORT_FIRST],
	       sizeof(mf));
	if (wan) {
		mf[0] ^= ZX279133_LAN_VPORT_MF_WAN_FLIP;
		mf[2] |= BIT(0);
	}
	ret = zx279133_mf_eram_entry_write(eth, vport, mf);
	if (ret)
		return ret;

	ret = zx279133_smmu0_read(eth, addr, type, ARRAY_SIZE(type),
				  ZX279133_SMMU0_CMD_READ);
	if (ret)
		return ret;
	if (wan)
		type[3] &= ~ZX279133_PORT_TYPE_LAN;
	else
		type[3] |= ZX279133_PORT_TYPE_LAN;

	return zx279133_smmu0_write(eth, addr, type, ARRAY_SIZE(type),
				    ZX279133_SMMU0_CMD_WRITE);
}

static int zx279133_lan_vport_bringup(struct zx279133_eth *eth)
{
	unsigned int port;
	bool wan;
	int ret;

	for (port = ZX279133_LAN_VPORT_FIRST; port <= ZX279133_LAN_VPORT_LAST;
	     port++) {
		wan = test_bit(port - ZX279133_LAN_VPORT_FIRST,
			       &eth->lan_wan_vports);
		ret = zx279133_lan_vport_mode_write(eth, port, wan);
		if (ret)
			return ret;
	}

	return 0;
}

/*
 * Switch one LAN port between LAN-type and WAN-type. Its offloaded flows were
 * built for the old type, so they all go; nf_flow_table offers them again.
 */
int zx279133_lan_vport_wan_set(struct zx279133_eth *eth, u16 vport, bool wan)
{
	unsigned int bit = vport - ZX279133_LAN_VPORT_FIRST;
	int ret = 0;

	if (vport < ZX279133_LAN_VPORT_FIRST || vport > ZX279133_LAN_VPORT_LAST)
		return -EINVAL;

	mutex_lock(&eth->datapath_lock);
	if (test_bit(bit, &eth->lan_wan_vports) == wan)
		goto out_unlock;
	if (eth->hardware_prepared) {
		ret = zx279133_lan_vport_mode_write(eth, vport, wan);
		if (ret)
			goto out_unlock;
	}
	if (wan)
		set_bit(bit, &eth->lan_wan_vports);
	else
		clear_bit(bit, &eth->lan_wan_vports);
	zx279133_flow_offload_reset(eth);

out_unlock:
	mutex_unlock(&eth->datapath_lock);
	return ret;
}

/*
 * LAN-side PPU state: the ingress start PC of the RTL8372N path, the protocol
 * action tables of the LAN ports and the SPA attributes of their memories.
 *
 * LAN frames reach XMAC0 (SPA memory 5) with the RTL8372N transport VLAN
 * (VID 59..62, one per LAN port) still in front of the payload. The factory
 * entry PC 10 removes it and carries on with the VID as the logical source
 * port: it looks the port up in the MF extension table and in the protocol
 * action tables, and a flow it routes to the WAN can have a PPPoE header
 * pushed, which the ordinary-Ethernet program (PC 561) never does. Those
 * per-port tables must therefore exist for the logical ports 59..62 as well;
 * without them every frame for the router itself is forwarded and lost.
 *
 * The LAN carries traffic whether or not the WAN has a link, so this belongs
 * to the shared data path and is programmed before RX is enabled. It used to
 * be done by the WAN link-up. With no WAN link the LAN then stayed on the
 * factory defaults (empty protocol tables), and every LAN frame went through
 * the default path to queue 0 and CPU0. The entry PC, the protocol tables and
 * the attributes belong together: the entry program relies on the tables, so
 * none of them can be moved on its own.
 */
static int zx279133_lan_port_bringup(struct zx279133_eth *eth)
{
	u32 reg;
	int ret;

	/* Table valid, start PC 10, flow number 1, as on the factory system. */
	ret = zx279133_spa_indirect_read(eth, 5, 0, &reg);
	if (ret)
		return ret;
	reg = (reg & ~GENMASK(23, 0)) | 0x8015;
	ret = zx279133_spa_indirect_write(eth, 5, 0, reg);
	if (ret)
		return ret;

	/*
	 * RTL8372N ingress reaches the PPU through port 0; ports 5 and 10 cover
	 * the CPU-facing and companion LAN paths.
	 */
	ret = zx279133_proto_table_write(eth, ZX279133_LAN_INGRESS_PPU_PORT,
					 zx279133_lan_proto_table);
	if (ret)
		return ret;
	ret = zx279133_proto_table_write(eth, ZX279133_LAN_PPU_PORT,
					 zx279133_lan_proto_table);
	if (ret)
		return ret;
	ret = zx279133_proto_table_write(eth, ZX279133_LAN_COMPANION_PORT,
					 zx279133_lan_proto_table);
	if (ret)
		return ret;

	ret = zx279133_spa_indirect_write(eth, 10, 0, 0x00c0800b);
	if (ret)
		return ret;
	ret = zx279133_spa_indirect_write(eth, 10, 1, 0x0000088e);
	if (ret)
		return ret;
	ret = zx279133_spa_indirect_write(eth, 10, 10, 0x01f4000c);
	if (ret)
		return ret;
	ret = zx279133_spa_indirect_write(eth, 10, 13, 0x00008008);
	if (ret)
		return ret;
	ret = zx279133_spa_indirect_write(eth, 10, 19, 0x00042000);
	if (ret)
		return ret;
	ret = zx279133_spa_indirect_write(eth, 15, 10, 0x01f4000c);
	if (ret)
		return ret;
	ret = zx279133_spa_indirect_write(eth, 5, 1, 0x0000088f);
	if (ret)
		return ret;
	ret = zx279133_spa_indirect_write(eth, 5, 10, 0x01f4000c);
	if (ret)
		return ret;
	ret = zx279133_spa_indirect_write(eth, 5, 13, 0x00650650);
	if (ret)
		return ret;
	ret = zx279133_spa_indirect_write(eth, 5, 14, 0x00650650);
	if (ret)
		return ret;
	ret = zx279133_spa_indirect_write(eth, 5, 19, 0x00042000);
	if (ret)
		return ret;
	ret = zx279133_mf_eram_attr_set(eth, 5, 0x00042000);
	if (ret)
		return ret;

	return zx279133_lan_vport_bringup(eth);
}

int zx279133_wan_port_bringup(struct zx279133_eth *eth)
{
	u32 reg;
	int ret;

	/*
	 * The vendor runtime (switch.ko port init) programs the CPU port
	 * memory 0 with Tbl Vld + start Pc 561 (SPA address 0 = 0x463,
	 * observed on the factory system); the start-PC copy below then
	 * propagates it to the WAN port. The switch-side UNI 5 maps to PPU
	 * port and SPA memory 6 (getPort(5) = 6). The factory's final
	 * memory-5 LAN state remains address 0 = 0x8015 (Pc 10, flow 1),
	 * address 10 = 0x01F4000C and address 19 = 0x42000.
	 * Forcing start-PC 0 to match vendor ODMA start_pc broke even 56-byte
	 * RX; ODMA start_pc=0 is post-PPU, not the ingress SPA start.
	 */
	ret = zx279133_spa_indirect_read(eth, 0, 0, &reg);
	if (ret)
		return ret;
	reg = (reg & ~GENMASK(23, 0)) | 0x463;
	ret = zx279133_spa_indirect_write(eth, 0, 0, reg);
	if (ret)
		return ret;
	ret = zx279133_spa_indirect_write(eth, 0, 10, 0x01f4000c);
	if (ret)
		return ret;

	ret = zx279133_mf_port_startpc_copy(eth, 6, 0);
	if (ret)
		return ret;
	ret = zx279133_proto_table_write(eth, ZX279133_WAN_PPU_PORT,
					 zx279133_wan_proto_table);
	if (ret)
		return ret;

	/*
	 * PPU port 6's final factory WAN state: MF attribute
	 * 0x148e0000 = iswanport | ppuinit | onumode | isolate |
	 * smac_learn | bit 26 (observed live on the factory system).
	 */
	ret = zx279133_spa_indirect_write(eth, 6, 1, 0x0000088f);
	if (ret)
		return ret;
	ret = zx279133_spa_indirect_write(eth, 6, 10, 0x01f4000c);
	if (ret)
		return ret;
	ret = zx279133_spa_indirect_write(eth, 6, 13, 0x00008008);
	if (ret)
		return ret;
	ret = zx279133_spa_indirect_write(eth, 6, 17, 0x0e014400);
	if (ret)
		return ret;
	ret = zx279133_spa_indirect_write(eth, 6, 18, 0x000001f1);
	if (ret)
		return ret;
	ret = zx279133_spa_indirect_write(eth, 6, 19, 0x148e0000);
	if (ret)
		return ret;
	{
		const u32 mf6[ZX279133_SMMU0_WORDS] = {
			0, 0x0e014400, 0x000001f1, 0x148e0000,
		};

		ret = zx279133_mf_eram_entry_write(eth, 6, mf6);
	}
	if (ret)
		return ret;

	/*
	 * The factory's runtime pkt-type table holds only the four
	 * entries below (0x00f7c000); the SPA uses them to tag CPU TX
	 * frames with the forwarding flow instead of the CPU exception
	 * flow.
	 */
	ret = zx279133_spa_pkt_type_runtime(eth);
	if (ret)
		return ret;

	/* spa_set_down_port_config(6): NPPT 0x8000 bits [11:6]. */
	reg = readl(eth->base + ZX279133_SPA_IPV6_CRC_MODE);
	writel((reg & ~(GENMASK(11, 6) | BIT(27))) | 6 << 6,
	       eth->base + ZX279133_SPA_IPV6_CRC_MODE);

	/*
	 * nppt_glb_lan_up_port_set(6) + nppt_glb_lan_up_en_set(1) +
	 * the OAM enables (mpcp/epon_oam/gpon_oam/omci_plus/omci,
	 * np_nppu_init): the CPU133 port table's byte for PPU port 6 is
	 * zero, so the port helper writes value 6 (not 0x106) into the
	 * NPPT 0x10 field; the enable helper sets bit 3 and the OAM
	 * helpers set bits [19:15].
	 */
	reg = readl(eth->base + 0x10);
	reg = (reg & ~0x107) | 0x006 | BIT(3) | GENMASK(19, 15);
	writel(reg, eth->base + 0x10);

	return 0;
}

/*
 * SE core SDT creation, recovered from np_133.ko - the module this board
 * actually loads - out of its g_core_sdt_info (.data+0x11be0, 53 entries
 * of 16 bytes: id, type, p3, num).
 *
 * The list is the vendor's, entry for entry and in the vendor's order,
 * because the switch engine's microcode knows each table's id, its width
 * and its number of entries.  A table declared with another width is
 * looked up at another place: the vendor's se_eram_table_set_by_sdt_info()
 * places an entry at (base << 7) + (width * index & ~0x7f) and merges the
 * value into the 16-byte window bit by bit, so a table of the right size
 * but the wrong width is read at the wrong address.
 *
 * type 1 is ERAM, 3 is hash, 4 is statistics.  Only ERAM takes a block
 * from the ERAM pool, and the pool is filled strictly in this order, so
 * every table's base follows from the list.  p3 selects the width of one
 * entry out of {1, 32, 64, 128, 2, 4, 8, 16} bits.
 *
 * The three tables this driver has of its own - the VLAN action table,
 * the NPPT's own 16-bit protocol table and the port type table - are not
 * in the vendor's list (the ids 0, 4 and 6 are free there) and are
 * appended after it.
 */
static const u32 zx279133_core_sdt_info[][4] = {
	{ 0x6f, 1, 0x3, 0x400 },	{ 0x70, 1, 0x3, 0x200 },
	{ 0x71, 1, 0x3, 0x200 },	{ 0x72, 1, 0x3, 0x200 },
	{ 0x74, 1, 0x2, 0x40 },		{ 0x75, 1, 0x3, 0x20 },
	{ 0x76, 1, 0x3, 0x20 },		{ 0x77, 1, 0x3, 0x20 },
	{ 0x78, 1, 0x1, 0x20 },		{ 0x79, 1, 0x3, 0x100 },
	{ 0x7a, 1, 0x3, 0x100 },	{ 0xa4, 3, 0x0, 0x3d },
	{ 0xa5, 3, 0x0, 0x39 },		{ 0x1c, 1, 0x4, 0x40000 },
	{ 0x22, 1, 0x3, 0x20 },		{ 0x18, 1, 0x4, 0x2000 },
	{ 0x19, 1, 0x1, 0x100 },	{ 0x1a, 1, 0x7, 0x20 },
	{ 0x1b, 1, 0x0, 0x1000 },	{ 0x1d, 1, 0x7, 0x40 },
	{ 0x23, 1, 0x0, 0x40 },		{ 0x24, 1, 0x0, 0x40 },
	{ 0x20, 1, 0x0, 0x40 },		{ 0x15, 1, 0x7, 0x40 },
	{ 0x16, 1, 0x7, 0x40 },		{ 0x17, 1, 0x7, 0x40 },
	{ 0x0a, 1, 0x7, 0x40 },		{ 0x26, 1, 0x7, 0x40 },
	{ 0x30, 4, 0x200, 0x20000000 },	{ 0x2e, 4, 0x10, 0x40000000 },
	{ 0x2f, 4, 0x3, 0x20000000 },	{ 0x31, 4, 0xc18, 0x60000000 },
	{ 0x25, 1, 0x7, 0x40 },		{ 0x1e, 1, 0x2, 0x40 },
	{ 0x21, 1, 0x2, 0x40 },		{ 0x1f, 1, 0x1, 0x4000 },
	{ 0x27, 1, 0x7, 0x40 },		{ 0x32, 3, 0x8, 0x64 },
	{ 0x38, 3, 0x6, 0x64 },		{ 0x35, 3, 0x8, 0x40 },
	{ 0x36, 3, 0x0, 0x10 },		{ 0x37, 3, 0x0, 0x10 },
	{ 0xfc, 4, 0x1, 0x60000000 },	{ 0x7b, 1, 0x3, 0x40 },
	{ 0x7c, 1, 0x3, 0x40 },		{ 0x29, 1, 0x6, 0x20 },
	{ 0x2a, 1, 0x3, 0x210 },		{ 0x39, 3, 0x6, 0x44 },
	{ 0x3a, 3, 0x2, 0x40 },		{ 0x3b, 3, 0x6, 0x40 },
	{ 0x7d, 1, 0x3, 0x40 },		{ 0x28, 1, 0x5, 0x2000 },
	{ 0x2b, 1, 0x2, 0x40 },
	/* this driver's own tables */
	{ 0x00, 1, 0x3, 0x1000 },	{ 0x04, 1, 0x7, 0x2000 },
	{ 0x06, 1, 0x3, 0x40 },
};

static const u32 zx279133_se_sdt_bit_len[8] = {
	1, 32, 64, 128, 2, 4, 8, 16
};

static int zx279133_se_sdt_eram_write(struct zx279133_eth *eth, u32 sdt_no,
				      u32 depth, u32 base, u32 mode)
{
	u32 node[2];

	node[0] = depth << 2;
	node[1] = (16 * (base & 0xffff)) |
		  ((0x10 | (mode << 1)) << 24);

	return zx279133_smmu0_write(eth, sdt_no, node, 2, 0xa8000000);
}

static int zx279133_se_parser_write(struct zx279133_eth *eth, u32 cmd,
				    const u32 *data, u32 words)
{
	u32 agclk = (readl(eth->pps_base + ZX279133_PPU_AGCLK_CFG) >> 1) & 1;
	u32 type = (cmd >> 9) & 7;
	u32 status, reg;
	int ret, i;

	if (agclk)
		writel(readl(eth->pps_base + ZX279133_PPU_AGCLK_CFG) & ~BIT(1),
		       eth->pps_base + ZX279133_PPU_AGCLK_CFG);

	ret = readl_poll_timeout_atomic(eth->pps_base + 0x40000, status,
					status & BIT(type), 1, 1000);
	if (!ret) {
		for (i = 0; i < words; i++)
			writel(data[i], eth->pps_base + 0x40040 + 4 * i);
		reg = readl(eth->pps_base + 0x40004);
		writel((cmd & 0xffff) | (reg & ~0xffff),
		       eth->pps_base + 0x40004);
	}

	if (agclk)
		writel(readl(eth->pps_base + ZX279133_PPU_AGCLK_CFG) | BIT(1),
		       eth->pps_base + ZX279133_PPU_AGCLK_CFG);

	return ret;
}

static int zx279133_se_parser_sdt_write(struct zx279133_eth *eth,
					u32 sdt_no, const u32 *node)
{
	return zx279133_se_parser_write(eth, sdt_no | 0x8000, node, 2);
}

static int zx279133_se_stat_sdt_write(struct zx279133_eth *eth, u32 sdt_no,
				      const u32 *node)
{
	u32 agclk = (readl(eth->pps_base + ZX279133_PPU_AGCLK_CFG) >> 2) & 1;
	u32 reg;
	int ret = 0;

	if (agclk)
		writel(readl(eth->pps_base + ZX279133_PPU_AGCLK_CFG) & ~BIT(2),
		       eth->pps_base + ZX279133_PPU_AGCLK_CFG);

	writel(node[0], eth->pps_base + 0x58808);
	writel(node[1], eth->pps_base + 0x5880c);
	writel((sdt_no & 0xff) | 0x300, eth->pps_base + 0x58804);
	ret = readl_poll_timeout_atomic(eth->pps_base + 0x58818, reg, reg != 0,
					1, 1000);

	if (agclk)
		writel(readl(eth->pps_base + ZX279133_PPU_AGCLK_CFG) | BIT(2),
		       eth->pps_base + ZX279133_PPU_AGCLK_CFG);

	return ret;
}

static int zx279133_se_hash_sdt_create(struct zx279133_eth *eth, u32 sdt_no,
				       u32 key_len, u32 tf)
{
	static const u32 key_base_tab[4] = { 4, 1, 2, 4 };
	u32 low = tf & 0xff;
	u32 width = (low >> 6) & 3;
	u32 as_rsp = (low >> 2) & 3;
	u32 multi = (tf >> 4) & 1;
	u32 age = (tf >> 5) & 1;
	u32 node[2], data = 0;
	u32 t, field;
	int ret, i;

	if (multi) {
		t = ffs(~eth->se_multi_idx_map) - 1;
		if (t >= 32)
			return -ENOSPC;
		eth->se_multi_idx_map |= BIT(t);
		t <<= 4;
		field = (t >> 5) & 0xf;
	} else {
		u32 key_base = key_base_tab[width];

		if (!key_len || ((key_base - 1) & key_len))
			return -EINVAL;
		t = ffs(~eth->se_hash_id_map[width]) - 1;
		if (t >= 32)
			return -ENOSPC;
		eth->se_hash_id_map[width] |= BIT(t);
		field = (key_len / key_base) & 0xf;
	}

	if (age && !eth->se_age_done) {
		/*
		 * se_hash_creat_age_sdt: one 1-bit x (0x1000<<7) age table
		 * at age pool block 0 (262144 bits); 32 parser base-address writes,
		 * each with the age base (0).
		 */
		for (i = 0; i < 32; i++) {
			ret = zx279133_se_parser_write(eth, i | 0x8400,
						       &data, 1);
			if (ret)
				return ret;
		}
		eth->se_age_done = true;
	}

	node[0] = ((age << 3) | (multi << 2) | (((tf >> 24) & 1) << 4)) << 8;
	node[1] = 0x30000000 |
		  (((field << 3) | ((t >> 2) & 7)) << 16) |
		  ((width & 3) << 23) | ((tf & 1) << 25) |
		  (((as_rsp << 4) | ((t & 3) << 6)) << 8);

	ret = zx279133_se_parser_write(eth, sdt_no | 0x8000, node, 2);
	if (ret)
		return ret;

	/* hash_multi_index_info_hw_set_default(): zero the HW index info. */
	if (multi)
		return zx279133_se_parser_write(eth, t | 0x8600, &data, 1);

	return 0;
}

static int zx279133_se_fast_multi_prepare(struct zx279133_eth *eth)
{
	static const u32 config[] = {
		0xff000000, 0xffffffff, 0xffffffff, 0xffffffff,
		0, 0, 0, 0, 0, 0, 0, 0,
		0x01000344,
	};
	u32 index = 0x8000;
	int ret;

	/* SDT 43 starts at multi-hash index 48.  Config table 0 extracts the
	 * 16-byte IPv4 fast-flow key and selects 256-bit hash table ID 1.
	 */
	ret = zx279133_se_parser_write(eth, 0x8800, config,
				       ARRAY_SIZE(config));
	if (ret)
		return ret;

	return zx279133_se_parser_write(eth, 0x8630, &index, 1);
}

static int zx279133_se_plcr_write(struct zx279133_eth *eth, u32 addr,
				  u32 value)
{
	u32 agclk = (readl(eth->pps_base + ZX279133_PPU_AGCLK_CFG) >> 2) & 1;

	if (agclk)
		writel(readl(eth->pps_base + ZX279133_PPU_AGCLK_CFG) & ~BIT(2),
		       eth->pps_base + ZX279133_PPU_AGCLK_CFG);

	writel(addr & 0xffffff, eth->pps_base + 0x58004);
	writel(value, eth->pps_base + 0x58008);
	writel(1, eth->pps_base + 0x58000);

	if (agclk)
		writel(readl(eth->pps_base + ZX279133_PPU_AGCLK_CFG) | BIT(2),
		       eth->pps_base + ZX279133_PPU_AGCLK_CFG);

	return 0;
}

static int zx279133_se_plcr_read(struct zx279133_eth *eth, u32 addr,
				 u32 *value)
{
	u32 agclk = (readl(eth->pps_base + ZX279133_PPU_AGCLK_CFG) >> 2) & 1;

	if (agclk)
		writel(readl(eth->pps_base + ZX279133_PPU_AGCLK_CFG) & ~BIT(2),
		       eth->pps_base + ZX279133_PPU_AGCLK_CFG);

	writel((addr & 0xffffff) | 0x80000000, eth->pps_base + 0x58004);
	writel(1, eth->pps_base + 0x58000);
	*value = readl(eth->pps_base + 0x5800c);

	if (agclk)
		writel(readl(eth->pps_base + ZX279133_PPU_AGCLK_CFG) | BIT(2),
		       eth->pps_base + ZX279133_PPU_AGCLK_CFG);

	return 0;
}

static int zx279133_se_plcr_init(struct zx279133_eth *eth)
{
	u32 status;
	int ret, i;

	/* 4096-flow policer table, mode 3, base block 40960 from the pool end. */
	ret = zx279133_se_plcr_write(eth, 0x280017, 40960);
	if (ret)
		return ret;
	ret = zx279133_se_plcr_write(eth, 0x280016, 3);
	if (ret)
		return ret;
	ret = zx279133_se_plcr_write(eth, 0x280018, 62500);
	if (ret)
		return ret;
	ret = zx279133_se_plcr_write(eth, 0x280015, 1);
	if (ret)
		return ret;
	ret = zx279133_se_plcr_write(eth, 0x30000e, 24);
	if (ret)
		return ret;
	ret = zx279133_se_plcr_write(eth, 0x300010, 8000);
	if (ret)
		return ret;
	ret = zx279133_se_plcr_write(eth, 0x28001f, 16000);
	if (ret)
		return ret;
	ret = zx279133_se_plcr_write(eth, 0x280014, 33000);
	if (ret)
		return ret;

	for (i = 0; i < 20; i++) {
		ret = zx279133_se_plcr_read(eth, 0x30000f, &status);
		if (ret)
			return ret;
		if (status & BIT(0))
			return 0;
	}

	return dev_err_probe(eth->dev, -ETIMEDOUT,
			     "SE PLCR init done status %#x\n", status);
}

static int zx279133_se_sdt_create(struct zx279133_eth *eth)
{
	/*
	 * se_smmu0_reg_table: the ERAM pool's first free block is 0x3000 and
	 * the pool is 0x9000 blocks long, so the tables live in
	 * [0x3000, 0x9000).  The statistics pool starts at 0x9000 and runs
	 * to the end of the ERAM at 0xc000.
	 *
	 * The first free block is 0x3000 and not 0x1000, and the board says
	 * so: a block below 0x3000 cannot be written at all.  Write
	 * 0xdeadbeef into it and read it back, and the word that comes out
	 * is not the one that went in - it is a page table entry out of the
	 * SoC's own memory, because that address is not ERAM at all.  Above
	 * 0x3000 the same write comes back exactly as it went in, and the
	 * boundary is exactly 0x3000:
	 *
	 *	eram block 12000 = 00b91a7f 40000000 00b91452 40000000
	 *	eram block 12288 = deadbeef 11111111 22222222 33333333
	 *
	 * So the tables this driver allocates have to start where the ERAM
	 * does.  Starting them 0x2000 blocks lower put every one of them
	 * outside the ERAM, and the hardware - which reads the tables where
	 * the driver told it to - found nothing there.
	 */
	u32 eram_base = 0x3000;
	u32 stat_base = 9 << 12;
	u32 depth, base, bit_len;
	u32 node[2];
	unsigned int i;
	int ret;

	/* se_age_manage_init: parser age table depth register. */
	writel(2047, eth->pps_base + 0x400c4);

	for (i = 0; i < ARRAY_SIZE(zx279133_core_sdt_info); i++) {
		u32 sdt_no = zx279133_core_sdt_info[i][0];
		u32 type = zx279133_core_sdt_info[i][1];
		u32 a = zx279133_core_sdt_info[i][2];
		u32 b = zx279133_core_sdt_info[i][3];

		switch (type) {
		case 1:
		case 5:
			bit_len = zx279133_se_sdt_bit_len[a & 7];
			depth = (bit_len * b + 127) >> 7;
			base = eram_base;
			eram_base += depth;
			/*
			 * The base the hardware is told about here is the
			 * only one that is right for it, so it is the one
			 * the tables are written at.  The width of an entry
			 * matters just as much: the hardware looks an entry
			 * up at (base << 7) + (width * index & ~0x7f).
			 */
			if (sdt_no < ZX279133_SDT_ID_MAX) {
				zx279133_eram[sdt_no].base = base;
				zx279133_eram[sdt_no].bit_len = bit_len;
			}
			ret = zx279133_se_sdt_eram_write(eth, sdt_no, depth,
							 base, a);
			if (ret)
				return dev_err_probe(eth->dev, ret,
					"SE ERAM SDT %#x write failed\n",
					sdt_no);
			if (type == 5) {
				node[0] = depth << 2;
				node[1] = (16 * (base & 0xffff)) |
					  ((0x10 | (a << 1)) << 24);
				ret = zx279133_se_parser_sdt_write(eth, sdt_no,
								   node);
				if (ret)
					return dev_err_probe(eth->dev, ret,
						"SE parser SDT %#x write failed\n",
						sdt_no);
			}
			break;
		case 3:
			ret = zx279133_se_hash_sdt_create(eth, sdt_no, a, b);
			if (ret)
				return dev_err_probe(eth->dev, ret,
					"SE hash SDT %#x create failed\n",
					sdt_no);
			break;
		case 4: {
			u32 st_mode = (b >> 24) >> 5;

			bit_len = zx279133_se_sdt_bit_len[st_mode];
			depth = (bit_len * a + 127) >> 7;
			base = stat_base;
			stat_base += depth;
			node[0] = depth << 12;
			node[1] = (base & 0xfffff) |
				  ((0x40 | (st_mode << 1)) << 24);
			ret = zx279133_se_stat_sdt_write(eth, sdt_no, node);
			if (ret)
				return dev_err_probe(eth->dev, ret,
					"SE stat SDT %#x write failed\n",
					sdt_no);
			break;
		}
		default:
			break;
		}
	}

	ret = zx279133_se_fast_multi_prepare(eth);
	if (ret)
		return dev_err_probe(eth->dev, ret,
				     "SE fast multi-hash setup failed\n");

	/*
	 * The factory's runtime port type table (SDT 6), captured live: one
	 * 128-bit entry per logical port, the port attribute words the
	 * switch engine reads directly.  The patterns below are the app's
	 * P2P-mode values.
	 */
	{
		static const struct {
			u32 entry, w0, w1, w2, w3;
		} tabs[] = {
			{ 1, 0xffff0140, 0x7801ffff, 0, 0 },
			{ 2, 0xffff0140, 0x7801ffff, 0, 0 },
			{ 3, 0xffff0140, 0x7801ffff, 0, 0 },
			{ 4, 0xffff0140, 0x7801ffff, 0, 0 },
			{ 5, 0xffff0140, 0x7801ffff, 0, 0 },
			{ 6, 0x00000040, 0, 0, 0 },
			{ 7, 0xffff0140, 0x7801ffff, 0, 0 },
			{ 8, 0, 0, 0, 0x80000000 },
		};
		u32 ptype_base = zx279133_eram_base_of(ZX279133_PORT_TYPE_ERAM_SDT_ID);
		u32 idx;

		if (!ptype_base)
			return dev_err_probe(eth->dev, -EINVAL,
					     "the port type table has no base\n");

		for (idx = 0; idx < ARRAY_SIZE(tabs); idx++) {
			u32 node[4] = { tabs[idx].w0, tabs[idx].w1,
					tabs[idx].w2, tabs[idx].w3 };

			ret = zx279133_smmu0_write(eth,
						   (ptype_base + tabs[idx].entry) << 7,
						   node, 4,
						   ZX279133_SMMU0_CMD_WRITE);
			if (ret)
				return ret;
		}
		for (idx = 0; idx < 16; idx++) {
			u32 node[4] = { 0x10000 << idx, 0, 0, 0 };

			ret = zx279133_smmu0_write(eth,
						   (ptype_base + 16 + idx) << 7,
						   node, 4,
						   ZX279133_SMMU0_CMD_WRITE);
			if (ret)
				return ret;
		}
		for (idx = 0; idx < 16; idx++) {
			u32 node[4] = { 0, 1 << idx, 0, 0 };

			ret = zx279133_smmu0_write(eth,
						   (ptype_base + 32 + idx) << 7,
						   node, 4,
						   ZX279133_SMMU0_CMD_WRITE);
			if (ret)
				return ret;
		}
		for (idx = 0; idx < 4; idx++) {
			u32 node[4] = { 0, 0, 0, 0x82000000 };

			ret = zx279133_smmu0_write(eth,
						   (ptype_base + 59 + idx) << 7,
						   node, 4,
						   ZX279133_SMMU0_CMD_WRITE);
			if (ret)
				return ret;
		}
	}

	return 0;
}

static void zx279133_se_stat_runtime_prepare(struct zx279133_eth *eth)
{
	u32 agclk = readl(eth->pps_base + ZX279133_PPU_AGCLK_CFG);
	u32 value;

	/* stat_init()'s isCpuType_133() path enables every stat RAM client. */
	if (agclk & BIT(2))
		writel(agclk & ~BIT(2),
		       eth->pps_base + ZX279133_PPU_AGCLK_CFG);

	value = readl(eth->pps_base + ZX279133_SE_STAT_RUNTIME_CFG);
	writel(value | ZX279133_SE_STAT_RUNTIME_MASK,
	       eth->pps_base + ZX279133_SE_STAT_RUNTIME_CFG);

	if (agclk & BIT(2))
		writel(agclk, eth->pps_base + ZX279133_PPU_AGCLK_CFG);

	value = readl(eth->pps_base + ZX279133_SE_PARSER_G988_CFG);
	writel(value | BIT(1), eth->pps_base + ZX279133_SE_PARSER_G988_CFG);
}

static int zx279133_ppu_dup_entry(struct zx279133_eth *eth, u32 index,
				  u32 value)
{
	u32 agclk;
	u32 status;
	int ret;

	agclk = readl(eth->pps_base + ZX279133_PPU_AGCLK_CFG);
	if (agclk & ZX279133_PPU_REORDER_AGCLK)
		writel(agclk & ~ZX279133_PPU_REORDER_AGCLK,
		       eth->pps_base + ZX279133_PPU_AGCLK_CFG);

	ret = readl_poll_timeout_atomic(eth->pps_base +
					ZX279133_PPU_BLOCK_RDY,
					status, status & BIT(0), 1, 1000);
	if (ret) {
		dev_err_probe(eth->dev, ret,
			      "PPU block not ready: %#x\n", status);
		goto out_gate;
	}
	writel((readl(eth->pps_base + ZX279133_PPU_BLOCK_DATA) &
		~ZX279133_PPU_BLOCK_DATA_MASK) | value,
		eth->pps_base + ZX279133_PPU_BLOCK_DATA);
	writel((readl(eth->pps_base + ZX279133_PPU_BLOCK_INDEX) &
		~ZX279133_PPU_BLOCK_INDEX_MASK) | index,
		eth->pps_base + ZX279133_PPU_BLOCK_INDEX);

out_gate:
	if (agclk & ZX279133_PPU_REORDER_AGCLK)
		writel(agclk, eth->pps_base + ZX279133_PPU_AGCLK_CFG);

	return ret;
}

static int zx279133_ppu_hash_write(struct zx279133_eth *eth, u32 pc,
				   const u64 *inst)
{
	u32 lo[4], hi[4], value;
	int i, ret;

	for (i = 0; i < 4; i++) {
		lo[i] = lower_32_bits(inst[i]);
		hi[i] = upper_32_bits(inst[i]);
	}
	ret = zx279133_ppu_inst_block(eth, pc, lo, hi);
	if (ret)
		return ret;
	ret = readl_poll_timeout_atomic(eth->pps_base + ZX279133_PPU_CLUSTER_RDY,
					value, value & BIT(0), 1, 1000);
	if (ret)
		return ret;
	/* Read command and result window calibrated against all loaded words. */
	writel((readl(eth->pps_base + ZX279133_PPU_CLUSTER_PC) &
		~ZX279133_PPU_CLUSTER_PC_MASK) | BIT(12) | pc >> 2,
	       eth->pps_base + ZX279133_PPU_CLUSTER_PC);
	ret = readl_poll_timeout_atomic(eth->pps_base + ZX279133_PPU_CLUSTER_PC,
					value, value & BIT(0), 1, 1000);
	if (ret)
		return ret;
	for (i = 0; i < 4; i++) {
		u64 actual = (u64)(readl(eth->pps_base + 0x90d5c + i * 8) &
				  ZX279133_PPU_CLUSTER_HI_MASK) << 32 |
			     readl(eth->pps_base + 0x90d58 + i * 8);

		if (actual != inst[i])
			return -EIO;
	}
	return 0;
}

static int zx279133_ppu_rx_hash_prepare(struct zx279133_eth *eth,
					const struct firmware *fw)
{
	const struct zx279133_rx_hash_program *programs = rx_hash_ipv6 ?
		zx279133_rx_hash_ipv6_programs : zx279133_rx_hash_programs;
	unsigned int which, i;
	int ret;

	/* The fingerprinted firmware leaves these ranges outside its loaded code.
	 * Publish and verify all bodies before any hook, before route/RX start.
	 */
	for (which = 0; which < ARRAY_SIZE(zx279133_rx_hash_programs); which++) {
		const struct zx279133_rx_hash_program *p = &programs[which];
		u64 block[4];
		unsigned int j;

		for (i = 0; i < p->count; i += 4) {
			memcpy(block, p->inst + i, sizeof(block));
			for (j = 0; j < p->map_count; j++) {
				unsigned int offset = p->map[j].offset;
				u8 map = eth->rx_rss_map[p->map[j].port];

				if (offset < i || offset >= i + 4)
					continue;
				block[offset - i] &= ~BIT_ULL(27);
				block[offset - i] |=
					(u64)((map >> p->map[j].bucket) & 1) << 27;
			}
			ret = zx279133_ppu_hash_write(eth, p->start + i, block);
			if (ret)
				return ret;
		}
	}
	for (which = 0; which < ARRAY_SIZE(zx279133_rx_hash_programs); which++) {
		const struct zx279133_rx_hash_program *p = &programs[which];
		u64 block[4];

		for (i = 0; i < 4; i++)
			block[i] = get_unaligned_be64(fw->data + 16 + ((p->hook & ~3) + i) * 8);
		block[p->hook & 3] = 0x0108000000000520ULL |
				    (u64)(p->hook + 1) << 31 | (u64)p->start << 15;
		ret = zx279133_ppu_hash_write(eth, p->hook & ~3, block);
		if (ret)
			return ret;
	}
	return 0;
}

/*
 * The board's own port-flow table: the entry program every SPA port runs.
 *
 * The PPU microcode carries a port-flow table of its own, and the loader
 * applies it to the SPA port attributes as it installs the code.  That table
 * is *board* configuration - it says which of the SoC's internal ports is
 * the WAN, which four are the UNI jacks, which one the CPU talks to - and the
 * blob this driver is given carries the one for the board the blob was taken
 * from.
 *
 * The blob in the tree (target/linux/zte/zx279133/firmware/zte/zx279133/
 * mcode_intel.bin) is the SR1010's.  Its table and this board's differ on
 * 55 of the 64 ports:
 *
 *	port	SR1010		this board
 *	0	0x00000463	0x0000a6a5
 *	1..4	0x00008015	0x0000a6a5
 *	5	0x00008015	0x0000a6a5	(XMAC0: the CPU's conduit)
 *	6, 7	0x00008015	0x0000a6a5
 *	8	-		0x00039b11
 *	10	0x00c0800b	0x0000a6a5
 *	12	0x00ef8445	0x00009f99
 *	14	0x00c00459	0x0000a6a5
 *	15	0x00008001	0x0000a767	(the CPU trap port)
 *	16..48	0x0000a4d3	0x0000a6a5
 *	49	0x0000a86d	0x00052825
 *	50	0x0000a86d	0x00052827
 *	51..54, 63	0x0000a86d / 0x00008009	- (the vendor configures none)
 *
 * The board's own table is the vendor's, and it is the one the vendor's own
 * board runs: the vendor's blob (etc/mcode/mcode_133.bin, read out of this
 * board's own flash) carries it, and it agrees with the SPA memory the
 * vendor's live board has - the four jacks read 0x0000a6a5 and the CPU trap
 * port reads 0x0000a767 out of the vendor's own tm_debug.
 *
 * The driver's own port setup (zx279133_9132_port_words_vendor()) already
 * writes the vendor's words for the four jacks and for the CPU trap port, so
 * those were right; every other port kept the SR1010's entry program.  The
 * port that matters most is 5: XMAC0 is the conduit the CPU talks to the
 * 9132 through, and it was running an entry program that belongs to another
 * board.
 *
 * So the blob's port-flow tags are not applied at all, and this table is
 * written instead.  A port the vendor's table does not name keeps whatever
 * the chip reset left in it, which is what the vendor's board has too - the
 * vendor's own table does not touch ports 9, 11, 13 or 51..63 either.
 */
static const u32 zx279133_9132_port_flow[64] = {
	/* 0: PON0, the WAN.  1..4: the four UNI jacks. */
	0x0000a6a5, 0x0000a6a5, 0x0000a6a5, 0x0000a6a5,
	/* 5: XMAC0, the CPU's conduit.  6: XMAC1.  7: RGMII1. */
	0x0000a6a5, 0x0000a6a5, 0x0000a6a5, 0x0000a6a5,
	0x00039b11,		/* 8 */
	0x00000000,		/* 9: none */
	0x0000a6a5,		/* 10 */
	0x00000000,		/* 11: none */
	0x00009f99,		/* 12 */
	0x00000000,		/* 13: none */
	0x0000a6a5,		/* 14 */
	0x0000a767,		/* 15: the CPU trap port */
	/* 16..48: the WOE's queue ports. */
	0x0000a6a5, 0x0000a6a5, 0x0000a6a5, 0x0000a6a5, 0x0000a6a5,
	0x0000a6a5, 0x0000a6a5, 0x0000a6a5, 0x0000a6a5, 0x0000a6a5,
	0x0000a6a5, 0x0000a6a5, 0x0000a6a5, 0x0000a6a5, 0x0000a6a5,
	0x0000a6a5, 0x0000a6a5, 0x0000a6a5, 0x0000a6a5, 0x0000a6a5,
	0x0000a6a5, 0x0000a6a5, 0x0000a6a5, 0x0000a6a5, 0x0000a6a5,
	0x0000a6a5, 0x0000a6a5, 0x0000a6a5, 0x0000a6a5, 0x0000a6a5,
	0x0000a6a5, 0x0000a6a5, 0x0000a6a5,
	0x00052825,		/* 49 */
	0x00052827,		/* 50 */
	/* 51..63: the vendor's board configures none of them. */
	0x00000000, 0x00000000, 0x00000000, 0x00000000, 0x00000000,
	0x00000000, 0x00000000, 0x00000000, 0x00000000, 0x00000000,
	0x00000000, 0x00000000, 0x00000000,
};

static int zx279133_9132_port_flow_apply(struct zx279133_eth *eth)
{
	unsigned int i;
	int ret;

	for (i = 0; i < ARRAY_SIZE(zx279133_9132_port_flow); i++) {
		if (!zx279133_9132_port_flow[i])
			continue;
		ret = zx279133_spa_indirect_write(eth, i, 0,
						  zx279133_9132_port_flow[i]);
		if (ret)
			return ret;
	}
	return 0;
}

static int zx279133_ppu_mcode_prepare(struct zx279133_eth *eth)
{
	const struct firmware *fw;
	const u8 *p;
	const u8 *end;
	u32 lo[4], hi[4];
	u32 first, inst_num, tag, value, entry, pkt_type, i, blob_crc;
	u32 agclk;
	bool hash;
	int ret;

	WRITE_ONCE(eth->rx_hash_active, false);
	WRITE_ONCE(eth->rx_hash_ipv6_active, false);
	ret = request_firmware(&fw, "zte/zx279133/mcode_intel.bin", eth->dev);
	if (ret)
		return dev_err_probe(eth->dev, ret,
				     "failed to load PPU microcode\n");

	if (fw->size < 48 ||
	    get_unaligned_be32(fw->data) != ZX279133_MCODE_MAGIC ||
	    get_unaligned_be32(fw->data + 8) != ZX279133_MCODE_INST_NUM)
		goto err_fw;

	inst_num = get_unaligned_be32(fw->data + 8);
	first = get_unaligned_be32(fw->data + 12);
	if (!first || first > ZX279133_MCODE_INST_NUM - 80 ||
	    first * 8 + 16 > fw->size || inst_num * 8 < 624 ||
	    inst_num * 8 + 16 > fw->size)
		goto err_fw;
	first = ALIGN(first, 4);
	blob_crc = crc32_le(~0U, fw->data, fw->size) ^ ~0U;
	hash = rx_hash && fw->size == 103456 &&
	       blob_crc == ZX279133_MCODE_RX_HASH_CRC;
	if (rx_hash && !hash)
		dev_info(eth->dev,
			 "the board's own PPU firmware is loaded; the RX-hash hook, which belongs to another blob, is not installed\n");
	if (fw->size == 103456 && blob_crc != ZX279133_MCODE_BOARD_CRC)
		dev_warn(eth->dev,
			 "the PPU firmware is not this board's own (crc32 0x%08x, expected 0x%08x): the PPU will not forward frames\n",
			 blob_crc, ZX279133_MCODE_BOARD_CRC);

	agclk = readl(eth->pps_base + ZX279133_PPU_AGCLK_CFG);
	if (agclk & ZX279133_PPU_CORE_AGCLK)
		writel(agclk & ~ZX279133_PPU_CORE_AGCLK,
		       eth->pps_base + ZX279133_PPU_AGCLK_CFG);

	p = fw->data + 16;
	for (i = 0; i < first; i += 4) {
		for (entry = 0; entry < 4; entry++) {
			/* np_downlaod_mcode stores each file pair high, low. */
			hi[entry] = get_unaligned_be32(p + 8 * entry);
			lo[entry] = get_unaligned_be32(p + 8 * entry + 4);
		}
		ret = zx279133_ppu_inst_block(eth, i, lo, hi);
		if (ret)
			goto out_gate;
		p += 32;
	}

	writel((readl(eth->pps_base + ZX279133_PPU_IKEY_AFULL) &
		~ZX279133_PPU_IKEY_AFULL_MASK),
	       eth->pps_base + ZX279133_PPU_IKEY_AFULL);

	p = fw->data + 8 * inst_num - 624;
	for (i = 0; i < 80; i += 4) {
		for (entry = 0; entry < 4; entry++) {
			hi[entry] = get_unaligned_be32(p + 8 * entry);
			lo[entry] = get_unaligned_be32(p + 8 * entry + 4);
		}
		ret = zx279133_ppu_inst_block(eth, 12208 + i, lo, hi);
		if (ret)
			goto out_gate;
		p += 32;
	}

	if (hash) {
		ret = zx279133_ppu_rx_hash_prepare(eth, fw);
		if (ret)
			goto out_gate;
	}
	p = fw->data + 8 * inst_num + 16;
	end = fw->data + fw->size;
	pkt_type = 0;
	while (p + 8 <= end) {
		tag = get_unaligned_be32(p);
		value = get_unaligned_be32(p + 4);
		switch (tag) {
		case ZX279133_MCODE_TAG_PKT_TYPE:
			if (pkt_type >= 512)
				goto err_layout;
			entry = (value & 0x3fff) |
				(value >> 16 & 0x3f) << 14 |
				(value >> 22 & 0x7) << 20 |
				(value & BIT(25) ? BIT(23) : 0);
			if (!(value & 0x2000000))
				entry = 0;
			ret = zx279133_spa_indirect_write(eth, 64, pkt_type,
							  entry);
			if (ret)
				goto out_gate;
			pkt_type++;
			break;
		case ZX279133_MCODE_TAG_DUP:
			entry = value & BIT(23);
			if (value & BIT(23))
				entry |= ((value & BIT(22)) ? BIT(22) : 0) |
					(value & 0xffff) << 6 |
					(value >> 16 & 0x3f);
			ret = zx279133_ppu_dup_entry(eth, value >> 24, entry);
			if (ret)
				goto out_gate;
			break;
		case ZX279133_MCODE_TAG_SKIP:
			p += 16;
			break;
		case ZX279133_MCODE_TAG_VERSION:
			if (p + 16 <= end)
				dev_info(eth->dev,
					 "PPU microcode version %#x.%#x\n",
					 value, get_unaligned_be32(p + 8));
			p += 8;
			break;
		case ZX279133_MCODE_TAG_PORT_FLOW:
			/*
			 * The blob's own port-flow table is the board's, and
			 * this driver is given the blob for a different
			 * board; see zx279133_9132_port_flow[] above.  The
			 * board's table is applied after this walk.
			 */
			break;
		default:
			break;
		}
		p += 8;
	}
	if (pkt_type != 512)
		goto err_layout;
	/*
	 * And the board's own port-flow table, which the blob's is not.
	 */
	ret = zx279133_9132_port_flow_apply(eth);
	if (ret)
		goto out_gate;
	ret = 0;
	eth->ppu_mcode_prepared = true;
	WRITE_ONCE(eth->rx_hash_active, hash);
	WRITE_ONCE(eth->rx_hash_ipv6_active, hash && rx_hash_ipv6);
	if (hash) {
		eth->rx_hash_loads++;
		dev_info(eth->dev, "guarded %s RX hash installed, generation=%u\n",
			 rx_hash_ipv6 ? "IPv4/IPv6" : "IPv4", eth->rx_hash_loads);
	}

out_gate:
	writel(agclk, eth->pps_base + ZX279133_PPU_AGCLK_CFG);
	release_firmware(fw);
	return ret;

err_layout:
	ret = -EINVAL;
	goto out_gate;

err_fw:
	release_firmware(fw);
	return dev_err_probe(eth->dev, -EINVAL,
			     "invalid PPU microcode file\n");
}

static void zx279133_ppu_mcode_restore(struct zx279133_eth *eth)
{
	eth->ppu_mcode_prepared = false;
	WRITE_ONCE(eth->rx_hash_active, false);
	WRITE_ONCE(eth->rx_hash_ipv6_active, false);
}

static const u32 zx279133_spa_tpid_values[] = {
	0x88a88100,
	0x92009100,
	0x07020701,
	0x81000703,
};

static void zx279133_spa_port_attr_init(struct zx279133_eth *eth)
{
	u32 value;
	int mem_id;

	/*
	 * np.ko spa_init() per-port attribute tables for CPU133:
	 * attr 42 writes 0x0A00000C (0xFA00000C & 0x0fffffff) to the low
	 * 28 bits of SPA address 10, attr 8 writes 6 on the CPU-side
	 * memories {10,12,14} and 7 elsewhere, attr 9 writes 0x111 (both
	 * at address 1), and the per-port default TPID selection
	 * (attrs 48-67) writes 0x8008 at address 13 (attrs 49 and 53 = 1,
	 * the rest 0) for all 64 memories plus memory 15; addresses 14
	 * and 15 stay 0. attr 4 writes 7 at address 0 for the PON-side
	 * memory ids 49-54.
	 */
	for (mem_id = 0; mem_id < 64; mem_id++) {
		if (!zx279133_spa_indirect_read(eth, mem_id, 10, &value)) {
			value = (value & ~GENMASK(27, 0)) | 0x0a00000c;
			zx279133_spa_indirect_write(eth, mem_id, 10, value);
		}

		if (!zx279133_spa_indirect_read(eth, mem_id, 1, &value)) {
			u32 attr8 = (mem_id == 10 || mem_id == 12 ||
				     mem_id == 14) ? 6 : 7;

			value = (value & ~GENMASK(11, 0)) |
				attr8 | 0x111 << 3;
			zx279133_spa_indirect_write(eth, mem_id, 1, value);
		}

		if (!zx279133_spa_indirect_read(eth, mem_id, 13, &value)) {
			value = (value & ~GENMASK(23, 0)) | 0x8008;
			zx279133_spa_indirect_write(eth, mem_id, 13, value);
		}
	}
	if (!zx279133_spa_indirect_read(eth, 15, 13, &value)) {
		value = (value & ~GENMASK(23, 0)) | 0x8008;
		zx279133_spa_indirect_write(eth, 15, 13, value);
	}

	for (mem_id = 49; mem_id < 55; mem_id++) {
		if (zx279133_spa_indirect_read(eth, mem_id, 0, &value))
			continue;
		value = (value & ~GENMASK(23, 21)) | 7 << 21;
		zx279133_spa_indirect_write(eth, mem_id, 0, value);
	}
}

static void zx279133_spa_prepare(struct zx279133_eth *eth)
{
	unsigned int i;

	eth->spa_saved[0] = readl(eth->base + ZX279133_SPA_TPID_BASE);
	eth->spa_saved[1] = readl(eth->base + ZX279133_SPA_TPID_BASE + 0x4);
	eth->spa_saved[2] = readl(eth->base + ZX279133_SPA_TPID_BASE + 0x8);
	eth->spa_saved[3] = readl(eth->base + ZX279133_SPA_TPID_BASE + 0xc);
	eth->spa_saved[4] = readl(eth->base + ZX279133_SPA_TRAP_ETH_TYPE);
	eth->spa_saved[5] = readl(eth->base + ZX279133_SPA_TRAP_ETH_TYPE + 0x4);
	eth->spa_saved[6] = readl(eth->base + ZX279133_SPA_TRAP_ETH_TYPE + 0x8);

	for (i = 0; i < ZX279133_SPA_ONU_COUNT; i++) {
		eth->spa_saved[7 + 2 * i] =
			readl(eth->base + ZX279133_SPA_ONU_MAC_HI +
			      ZX279133_SPA_ONU_STRIDE * i);
		eth->spa_saved[8 + 2 * i] =
			readl(eth->base + ZX279133_SPA_ONU_MAC_LO +
			      ZX279133_SPA_ONU_STRIDE * i);
	}
	for (i = 0; i < ZX279133_SPA_TRAP_DMAC_COUNT; i++) {
		eth->spa_saved[39 + 2 * i] =
			readl(eth->base + ZX279133_SPA_TRAP_DMAC_HI +
			      ZX279133_SPA_TRAP_DMAC_STRIDE * i);
		eth->spa_saved[40 + 2 * i] =
			readl(eth->base + ZX279133_SPA_TRAP_DMAC_LO +
			      ZX279133_SPA_TRAP_DMAC_STRIDE * i);
	}

	for (i = 0; i < ARRAY_SIZE(zx279133_spa_tpid_values); i++)
		writel(zx279133_spa_tpid_values[i],
		       eth->base + ZX279133_SPA_TPID_BASE + 4 * i);
	writel(0, eth->base + ZX279133_SPA_TRAP_ETH_TYPE);
	writel(0, eth->base + ZX279133_SPA_TRAP_ETH_TYPE + 0x4);
	writel(0, eth->base + ZX279133_SPA_TRAP_ETH_TYPE + 0x8);
	for (i = 0; i < ZX279133_SPA_ONU_COUNT; i++) {
		writel(ZX279133_SPA_MAC_HI_VALUE,
		       eth->base + ZX279133_SPA_ONU_MAC_HI +
		       ZX279133_SPA_ONU_STRIDE * i);
		writel((eth->spa_saved[8 + 2 * i] & ~ZX279133_SPA_MAC_LO_MASK) |
		       ZX279133_SPA_MAC_LO_VALUE,
		       eth->base + ZX279133_SPA_ONU_MAC_LO +
		       ZX279133_SPA_ONU_STRIDE * i);
	}
	zx279133_program_spa_cpu_mac(eth, eth->ndev->dev_addr);
	zx279133_program_spa_uc_macs(eth);
	for (i = 0; i < ZX279133_SPA_TRAP_DMAC_COUNT; i++) {
		writel(ZX279133_SPA_MAC_HI_VALUE,
		       eth->base + ZX279133_SPA_TRAP_DMAC_HI +
		       ZX279133_SPA_TRAP_DMAC_STRIDE * i);
		writel((eth->spa_saved[40 + 2 * i] & ~ZX279133_SPA_MAC_LO_MASK) |
		       ZX279133_SPA_MAC_LO_VALUE,
		       eth->base + ZX279133_SPA_TRAP_DMAC_LO +
		       ZX279133_SPA_TRAP_DMAC_STRIDE * i);
	}
	/* spa_set_tcp_ctrl_mask(7): 0x8000 bits [19:12]. */
	writel((readl(eth->base + ZX279133_SPA_IPV6_CRC_MODE) &
		~(GENMASK(19, 12) | BIT(27))) | 7 << 12,
	       eth->base + ZX279133_SPA_IPV6_CRC_MODE);
	zx279133_spa_port_attr_init(eth);
	eth->spa_prepared = true;
}

static void zx279133_spa_restore(struct zx279133_eth *eth)
{
	unsigned int i;

	if (!eth->spa_prepared)
		return;

	for (i = 0; i < 4; i++)
		writel(eth->spa_saved[i],
		       eth->base + ZX279133_SPA_TPID_BASE + 4 * i);
	for (i = 0; i < 3; i++)
		writel(eth->spa_saved[4 + i],
		       eth->base + ZX279133_SPA_TRAP_ETH_TYPE + 4 * i);
	for (i = 0; i < ZX279133_SPA_ONU_COUNT; i++) {
		writel(eth->spa_saved[7 + 2 * i],
		       eth->base + ZX279133_SPA_ONU_MAC_HI +
		       ZX279133_SPA_ONU_STRIDE * i);
		writel(eth->spa_saved[8 + 2 * i],
		       eth->base + ZX279133_SPA_ONU_MAC_LO +
		       ZX279133_SPA_ONU_STRIDE * i);
	}
	for (i = 0; i < ZX279133_SPA_TRAP_DMAC_COUNT; i++) {
		writel(eth->spa_saved[39 + 2 * i],
		       eth->base + ZX279133_SPA_TRAP_DMAC_HI +
		       ZX279133_SPA_TRAP_DMAC_STRIDE * i);
		writel(eth->spa_saved[40 + 2 * i],
		       eth->base + ZX279133_SPA_TRAP_DMAC_LO +
		       ZX279133_SPA_TRAP_DMAC_STRIDE * i);
	}
	eth->spa_prepared = false;
}

static int zx279133_nppu_early_prepare(struct zx279133_eth *eth)
{
	u32 value;
	int ret;

	eth->nppu_early_saved[0] = readl(eth->base + ZX279133_ISU_CFG);
	eth->nppu_early_saved[1] = readl(eth->base + ZX279133_ISU_INIT_REQ);
	eth->nppu_early_saved[2] = readl(eth->base + ZX279133_ISU_DWRR_WEIGHT);
	eth->nppu_early_saved[3] = readl(eth->base + ZX279133_ISU_RING_GAP);
	eth->nppu_early_saved[4] = readl(eth->base + ZX279133_ODMA_ISU_SP_EN);

	ret = readl_poll_timeout_atomic(eth->base + ZX279133_ISU_INIT_REQ,
					value,
					!(value & ZX279133_ISU_INIT_REQ_MASK),
					1, 1000);
	if (ret)
		return dev_err_probe(eth->dev, ret,
				     "ISU init request remained busy: %#x\n",
				     value);
	writel((value & ~ZX279133_ISU_INIT_REQ_MASK) |
	       ZX279133_ISU_INIT_REQ_VALUE,
	       eth->base + ZX279133_ISU_INIT_REQ);
	writel((eth->nppu_early_saved[0] & ~ZX279133_ISU_CFG_MASK) |
	       ZX279133_ISU_SPA_SP_EN,
	       eth->base + ZX279133_ISU_CFG);
	writel(ZX279133_ISU_DWRR_WEIGHT_VALUE,
	       eth->base + ZX279133_ISU_DWRR_WEIGHT);
	writel((eth->nppu_early_saved[3] & ~ZX279133_ISU_RING_GAP_MASK) |
	       ZX279133_ISU_RING_GAP_VALUE,
	       eth->base + ZX279133_ISU_RING_GAP);
	writel(eth->nppu_early_saved[4] | ZX279133_ODMA_ISU_SP_EN_BIT,
	       eth->base + ZX279133_ODMA_ISU_SP_EN);
	eth->nppu_early_prepared = true;

	ret = readl_poll_timeout_atomic(eth->base + ZX279133_ISU_INIT_REQ,
					value,
					!(value & ZX279133_ISU_INIT_REQ_MASK),
					1, 1000);
	if (ret)
		return dev_err_probe(eth->dev, ret,
				     "ISU init request timed out: %#x\n", value);

	return 0;
}

static void zx279133_nppu_early_restore(struct zx279133_eth *eth)
{
	if (!eth->nppu_early_prepared)
		return;

	writel(eth->nppu_early_saved[0], eth->base + ZX279133_ISU_CFG);
	writel(eth->nppu_early_saved[1] & ~ZX279133_ISU_INIT_REQ_MASK,
	       eth->base + ZX279133_ISU_INIT_REQ);
	writel(eth->nppu_early_saved[2], eth->base + ZX279133_ISU_DWRR_WEIGHT);
	writel(eth->nppu_early_saved[3], eth->base + ZX279133_ISU_RING_GAP);
	writel(eth->nppu_early_saved[4], eth->base + ZX279133_ODMA_ISU_SP_EN);
	eth->nppu_early_prepared = false;
}

static void zx279133_nppu_late_prepare(struct zx279133_eth *eth)
{
	eth->nppu_late_saved[0] = readl(eth->base + ZX279133_SPA_IPV6_CRC_MODE);
	eth->nppu_late_saved[1] = readl(eth->base + ZX279133_GLB_OAM_EN);

	writel((eth->nppu_late_saved[0] &
		~(ZX279133_SPA_IPV6_CRC_BIT | ZX279133_SPA_TCP_CTRL_MASK)) |
	       ZX279133_SPA_TCP_CTRL_VALUE << 12,
	       eth->base + ZX279133_SPA_IPV6_CRC_MODE);
	writel((eth->nppu_late_saved[1] & ~ZX279133_GLB_OAM_MASK) |
	       ZX279133_GLB_OAM_VALUE,
	       eth->base + ZX279133_GLB_OAM_EN);
	eth->nppu_late_prepared = true;
}

static void zx279133_nppu_late_restore(struct zx279133_eth *eth)
{
	if (!eth->nppu_late_prepared)
		return;

	writel(eth->nppu_late_saved[0], eth->base + ZX279133_SPA_IPV6_CRC_MODE);
	writel(eth->nppu_late_saved[1], eth->base + ZX279133_GLB_OAM_EN);
	eth->nppu_late_prepared = false;
}

static void zx279133_sdet_prepare(struct zx279133_eth *eth)
{
	eth->sdet_saved[0] = readl(eth->base + ZX279133_SDET_FRAME_CFG);
	eth->sdet_saved[1] = readl(eth->base + ZX279133_SDET_DOWN_FRAME_CFG);

	writel((eth->sdet_saved[0] & GENMASK(31, 28)) |
	       ZX279133_SDET_UP_MAX_LEN << ZX279133_SDET_UP_MAX_SHIFT |
	       ZX279133_SDET_UP_MIN_LEN,
	       eth->base + ZX279133_SDET_FRAME_CFG);
	writel((eth->sdet_saved[1] & GENMASK(31, 30)) |
	       ZX279133_SDET_DOWN_MAX_LEN << ZX279133_SDET_DOWN_MAX_SHIFT |
	       ZX279133_SDET_DOWN_MIN_LEN,
	       eth->base + ZX279133_SDET_DOWN_FRAME_CFG);
	eth->sdet_prepared = true;
}

static void zx279133_sdet_restore(struct zx279133_eth *eth)
{
	if (!eth->sdet_prepared)
		return;

	writel(eth->sdet_saved[0], eth->base + ZX279133_SDET_FRAME_CFG);
	writel(eth->sdet_saved[1], eth->base + ZX279133_SDET_DOWN_FRAME_CFG);
	eth->sdet_prepared = false;
}

static void zx279133_sipc_final_prepare(struct zx279133_eth *eth)
{
	/* sipc_set_sch_mode_sw(0): clear the scheduler-mode-switch bit. */
	writel(readl(eth->base + ZX279133_SIPC_CFG) &
	       ~ZX279133_SIPC_SCH_MODE_SW,
	       eth->base + ZX279133_SIPC_CFG);
	eth->sipc_final_prepared = true;
}

static void zx279133_sipc_final_restore(struct zx279133_eth *eth)
{
	if (!eth->sipc_final_prepared)
		return;

	writel(eth->sipc_saved[0], eth->base + ZX279133_SIPC_CFG);
	eth->sipc_final_prepared = false;
}

int zx279133_np_prepare(struct zx279133_eth *eth)
{
	int ret;

	ret = zx279133_se_plcr_init(eth);
	if (ret)
		return ret;
	ret = zx279133_se_sdt_create(eth);
	if (ret)
		return ret;
	zx279133_se_stat_runtime_prepare(eth);
	ret = zx279133_ppu_mcode_prepare(eth);
	if (ret)
		return ret;
	ret = zx279133_np_ppu_init_tail(eth);
	if (ret)
		return ret;
	zx279133_spa_prepare(eth);
	/*
	 * Before anything here writes a single one of the 9132's per-port
	 * words, take the baseline: what the bootloader left, per port.
	 */
	zx279133_9132_port_words_read(eth);
	ret = zx279133_nppu_early_prepare(eth);
	if (ret)
		goto err_nppu_early_restore;
	zx279133_smct_prepare(eth);
	zx279133_nppu_late_prepare(eth);
	zx279133_sdet_prepare(eth);
	zx279133_sipc_final_prepare(eth);
	ret = zx279133_mf_port_ppuinit(eth);
	if (ret)
		goto err_route_off;
	ret = zx279133_lan_port_bringup(eth);
	if (ret)
		goto err_route_off;
	/*
	 * The 9132's own UNI ports (PPU 1..4) are the physical LAN jacks on
	 * SK-D840N; see the 9132 block above and docs/15.  The external
	 * RTL8226 (jack 4) is PPU 5 and is left alone here: it is the
	 * SoC's own LAN-side port and the vendor 4.19 does not carry a byte
	 * on it either (docs/07 supplement 94).
	 */
	ret = zx279133_9132_port_init(eth);
	if (ret)
		goto err_route_off;
	/*
	 * And the protocol action table, which is what tells the switch to
	 * trap a frame for the CPU instead of forwarding it to the WAN.
	 */
	ret = zx279133_9132_proto_init(eth);
	if (ret)
		goto err_route_off;
	/*
	 * And the three tables that say where a frame the switch hands to
	 * the CPU goes.  The vendor writes them in np_user_table_init() and
	 * sw_init_omci_trap_qid_init(); without them the frame arrives at
	 * a queue nobody is listening on.
	 */
	ret = zx279133_9132_cpu_path_init(eth);
	if (ret)
		goto err_route_off;
	/*
	 * And the tables that decide whether two ports may talk to each
	 * other and whether a frame is VLAN-checked on the way out.  The
	 * ERAM comes up empty after a power cut, so a table nobody fills
	 * is a table that says nothing sensible.
	 */
	ret = zx279133_9132_isolate_init(eth);
	if (ret)
		goto err_route_off;
	ret = zx279133_9132_switch_tables_init(eth);
	if (ret)
		goto err_route_off;
	/*
	 * The port attributes are the switch side; the PHY is the line side.
	 * A UNI port whose PHY is still powered down never links, so it is
	 * enabled after the chip itself has been programmed.
	 */
	zx279133_9132_phy_enable(eth);
	/*
	 * And what the chip says about its own ports, read back.  This is a
	 * read, not a write: the point is to see the enable word the
	 * bootloader left before anything here changes it.
	 */
	zx279133_9132_port_enable_read(eth);

	return 0;

err_route_off:
	zx279133_route_set(eth, false);
	zx279133_sipc_final_restore(eth);
	zx279133_sdet_restore(eth);
	zx279133_nppu_late_restore(eth);
	zx279133_smct_restore(eth);
err_nppu_early_restore:
	zx279133_nppu_early_restore(eth);
	zx279133_spa_restore(eth);
	zx279133_ppu_cluster_runtime_restore(eth);
	zx279133_ppu_mcode_restore(eth);
	return ret;
}

void zx279133_np_restore(struct zx279133_eth *eth)
{
	zx279133_sipc_final_restore(eth);
	zx279133_sdet_restore(eth);
	zx279133_nppu_late_restore(eth);
	zx279133_smct_restore(eth);
	zx279133_nppu_early_restore(eth);
	zx279133_spa_restore(eth);
	zx279133_ppu_cluster_runtime_restore(eth);
	zx279133_ppu_mcode_restore(eth);
}
