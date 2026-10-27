// SPDX-License-Identifier: GPL-2.0-only

#include <linux/bitfield.h>
#include <linux/clk.h>
#include <linux/cpumask.h>
#include <linux/delay.h>
#include <linux/device.h>
#include <linux/dma-mapping.h>
#include <linux/etherdevice.h>
#include <linux/firmware.h>
#include <linux/gpio/consumer.h>
#include <linux/hash.h>
#include <linux/if_vlan.h>
#include <linux/io.h>
#include <linux/iopoll.h>
#include <linux/ip.h>
#include <linux/interrupt.h>
#include <linux/jiffies.h>
#include <linux/leds.h>
#include <linux/mfd/syscon.h>
#include <linux/mdio.h>
#include <linux/module.h>
#include <linux/netdevice.h>
#include <linux/ethtool.h>
#include <linux/of.h>
#include <linux/of_net.h>
#include <linux/of_platform.h>
#include <linux/of_reserved_mem.h>
#include <linux/pcs/pcs-xpcs.h>
#include <linux/phy/phy.h>
#include <linux/phylink.h>
#include <linux/platform_device.h>
#include <linux/pm_runtime.h>
#include <linux/regmap.h>
#include <linux/tcp.h>
#include <linux/rtnetlink.h>
#include <linux/sizes.h>
#include <linux/unaligned.h>
#include <linux/workqueue.h>

#include <net/page_pool/helpers.h>

#include "zx279133.h"

static const char * const zx279133_clock_names[ZX279133_NUM_CLOCKS] = {
	"idm-aclk",
	"tm-aclk",
	"pclk",
	"smac-wclk",
	"woe1-wclk",
	"mac-wclk",
};

static const char * const zx279133_irq_names[ZX279133_NUM_IRQS] = {
	"nppt",
	"rx",
	"idm",
	"buffer-release",
	"local-test",
};

static void zx279133_xpcs_destroy(void *data)
{
	xpcs_destroy(data);
}

static void zx279133_napi_del(void *data)
{
	netif_napi_del(data);
}

static void zx279133_rx_affinity_clear(void *data)
{
	struct zx279133_eth *eth = data;

	irq_update_affinity_hint(eth->irqs[1], NULL);
	irq_update_affinity_hint(eth->irqs[4], NULL);
}

static void zx279133_rx_page_pool_destroy(void *data)
{
	struct zx279133_eth *eth = data;

	WARN_ON(eth->rx_page_map_count);
	page_pool_destroy(eth->lan_rx_page_pool);
	page_pool_destroy(eth->rx_page_pool);
}

static void zx279133_xdp_rxq_unreg(void *data)
{
	struct zx279133_eth *eth = data;
	unsigned int i;

	if (xdp_rxq_info_is_reg(&eth->xsk_rxq))
		xdp_rxq_info_unreg(&eth->xsk_rxq);
	for (i = 0; i < ZX279133_CPU_RX_QUEUES; i++)
		if (xdp_rxq_info_is_reg(&eth->xdp_rxq[i]))
			xdp_rxq_info_unreg(&eth->xdp_rxq[i]);
}

static void zx279133_mdio_device_put(void *data)
{
	mdio_device_put(data);
}

static void zx279133_phylink_destroy(void *data)
{
	phylink_destroy(data);
}

static int zx279133_idm_cci_enable(struct zx279133_eth *eth)
{
	int ret;

	ret = regmap_read(eth->idm_cci, 0, &eth->idm_cci_saved[0]);
	if (ret)
		return ret;

	ret = regmap_read(eth->idm_cci, 4, &eth->idm_cci_saved[1]);
	if (ret)
		return ret;

	ret = regmap_write(eth->idm_cci, 0, ZX279133_IDM_CCI_VALUE);
	if (ret)
		return ret;

	ret = regmap_write(eth->idm_cci, 4, ZX279133_IDM_CCI_VALUE);
	if (ret)
		regmap_write(eth->idm_cci, 0, eth->idm_cci_saved[0]);

	return ret;
}

static void zx279133_idm_cci_restore(struct zx279133_eth *eth)
{
	regmap_write(eth->idm_cci, 0, eth->idm_cci_saved[0]);
	regmap_write(eth->idm_cci, 4, eth->idm_cci_saved[1]);
}

/*
 * The BMU's BPPI threshold, put back the way the vendor has it.
 *
 * The tree's zx279133_bmu_prepare() writes 0x0104c040 into the register at
 * NPPT + 0x3c004 and then, after it has enabled the BMU, writes 0x01058080
 * over it.  The vendor's np_133.ko writes 0x0104c040 and leaves it there,
 * and that is what the vendor's board reads back (staging/ctl/
 * vendor-serial-mods/tm-bmu.txt, word 1).
 *
 * The register holds the buffer pool's low and high thresholds and the
 * largest burst, so the two values are not the same setting.  The value the
 * tree writes is a literal in the tree's zx279133-tm.c, which the overlay
 * cannot reach, so it is put back here.
 */
static void zx279133_bmu_threshold_fixup(struct zx279133_eth *eth)
{
	writel(ZX279133_BMU_BPPI_THRESH_VALUE, eth->base + ZX279133_BMU_BPPI_THRESH);
}

/*
 * The 9132's configuration words are behind an "auto" gate.
 *
 * NPPT + 0x00b8 bit 10 is that gate: while it is set, the chip manages
 * those words itself and a write to them is dropped.  The vendor's own
 * indirect access clears the gate around the access and puts it back
 * afterwards (np_133.ko's __red_ind_write), and this driver's copy of that
 * protocol does the same - but only for the indirect access.  The direct
 * writes of the RED's, the queue manager's and the switch's configuration
 * were all made with the gate still set, so the chip kept its own values.
 *
 * Measured 2026-10-23: every constant in zx279133.h that names a vendor
 * value *is* the vendor's value - 0x9e for the RED's configuration word,
 * 0x3f9f3e9f for the descriptor watermark, 0x08bf07ff for the IDM's, 0x0bb80fa0
 * for the queue manager's watermark, 0x1e00 for the switch's selection
 * configuration - and the board read back something else for every one of
 * them: 0x7e, 0x60002000, 0x5e000500, 0x2bb82fa0, 0x1f00.  Ninety-two words
 * differed between the two live boards (staging/ctl/tmp_livediff2.py), and
 * every one of them was a word this driver writes with the gate set.
 *
 * The gate is therefore cleared once around the configuration and put back
 * afterwards.  The indirect writes inside the RED's own fixup nest in it:
 * they read the gate, find bit 10 already clear, and write the value they
 * read back, so they leave the gate exactly as they found it.
 */
static u32 zx279133_red_gate_clear(struct zx279133_eth *eth)
{
	u32 gate = readl(eth->base + ZX279133_RED_AUTO_GATE_REG);

	if (gate & ZX279133_RED_AUTO_GATE_BIT) {
		writel(gate & ~ZX279133_RED_AUTO_GATE_BIT,
		       eth->base + ZX279133_RED_AUTO_GATE_REG);
		return gate;
	}

	return 0;
}

static void zx279133_red_gate_restore(struct zx279133_eth *eth, u32 gate)
{
	if (gate)
		writel(gate, eth->base + ZX279133_RED_AUTO_GATE_REG);
}

/*
 * The RED's indirect access port.
 *
 * The tree's zx279133-tm.c has a static copy of this, and so does the tree's
 * header, but both are in the kernel and not in the overlay, so the overlay
 * needs its own.  The protocol is the vendor's (np_133.ko's __red_ind_write
 * and __red_ind_read): the command word goes in first, the data words second,
 * and the shared auto-gate at NPPT + 0x00b8 bit 10 is cleared around the
 * access and put back afterwards.
 */
static void zx279133_red_ind_write(struct zx279133_eth *eth, u32 ram_id,
				   u32 addr, const u32 *data, unsigned int count)
{
	u32 gate = readl(eth->base + ZX279133_RED_AUTO_GATE_REG);
	u32 val;
	unsigned int i;

	if (gate & ZX279133_RED_AUTO_GATE_BIT)
		writel(gate & ~ZX279133_RED_AUTO_GATE_BIT,
		       eth->base + ZX279133_RED_AUTO_GATE_REG);

	readl_poll_timeout_atomic(eth->base + ZX279133_RED_IND_DONE, val,
				  val & ZX279133_RED_IND_DONE_BIT, 10, 200);

	writel(addr | FIELD_PREP(ZX279133_RED_IND_RAM_ID_MASK, ram_id),
	       eth->base + ZX279133_RED_IND_CMD);
	/*
	 * The data words go in the order they come out, first word first.
	 *
	 * They used to go in the other way round (data[count-1] into the
	 * lowest data register), which put the RED's four-word per-queue
	 * configuration into the chip reversed: the queue's first word ended
	 * up in its fourth place.  The vendor's own board says which way
	 * round it is: its last indirect access is a four-word write of the
	 * queue configuration, and the data registers still hold those four
	 * words afterwards - in order, first word in the lowest register.
	 * (staging/ctl/vendor-live-full.json, the red module's words 7..10.)
	 */
	for (i = 0; i < count; i++)
		writel(data[i], eth->base + ZX279133_RED_IND_DATA + 4 * i);

	writel(gate, eth->base + ZX279133_RED_AUTO_GATE_REG);
}

static void zx279133_red_ind_read(struct zx279133_eth *eth, u32 ram_id,
				  u32 addr, u32 *data, unsigned int count)
{
	u32 gate = readl(eth->base + ZX279133_RED_AUTO_GATE_REG);
	u32 val;
	unsigned int i;

	if (gate & ZX279133_RED_AUTO_GATE_BIT)
		writel(gate & ~ZX279133_RED_AUTO_GATE_BIT,
		       eth->base + ZX279133_RED_AUTO_GATE_REG);

	readl_poll_timeout_atomic(eth->base + ZX279133_RED_IND_DONE, val,
				  val & ZX279133_RED_IND_DONE_BIT, 10, 200);

	writel(addr | FIELD_PREP(ZX279133_RED_IND_RAM_ID_MASK, ram_id) |
	       ZX279133_RED_IND_RW_BIT,
	       eth->base + ZX279133_RED_IND_CMD);

	readl_poll_timeout_atomic(eth->base + ZX279133_RED_IND_DONE, val,
				  val & ZX279133_RED_IND_DONE_BIT, 10, 200);

	for (i = 0; i < count; i++)
		data[i] = readl(eth->base + ZX279133_RED_IND_DATA + 4 * i);

	writel(gate, eth->base + ZX279133_RED_AUTO_GATE_REG);
}

/*
 * The RED's queue RAMs, for the console.
 *
 * The RAMs are reached through the indirect port, so the plain register
 * console cannot touch them, and they are where the RED's per-queue
 * geometry lives - the one thing that decides how much room a queue has and
 * therefore what the RED hands to the queue manager.  These two wrappers are
 * what the debugfs "redq" commands use.
 */
void zx279133_red_ram_write(struct zx279133_eth *eth, u32 ram_id, u32 addr,
			    const u32 *data, unsigned int count)
{
	zx279133_red_ind_write(eth, ram_id, addr, data, count);
}

void zx279133_red_ram_read(struct zx279133_eth *eth, u32 ram_id, u32 addr,
			   u32 *data, unsigned int count)
{
	zx279133_red_ind_read(eth, ram_id, addr, data, count);
}

/*
 * The RED, put the way this board's vendor firmware has it.
 *
 * The tree's zx279133_tm_prepare() programs the RED for the SR1010 and the
 * overlay cannot reach that code, so the whole RED configuration is written
 * again here, afterwards, with the values the vendor's own board reads back.
 * The reasoning, and every value, is in the block comment above
 * ZX279133_RED_ALL_SHARE_MAX in zx279133.h.
 */
static void zx279133_red_vendor_fixup(struct zx279133_eth *eth)
{
	static const struct {
		u16 offset;
		u32 value;
	} regs[] = {
		{ ZX279133_RED_CFG,			ZX279133_RED_VENDOR_CFG },
		{ ZX279133_RED_IN_SHARE_MAX,		ZX279133_RED_VENDOR_IN_SHARE_MAX },
		{ ZX279133_RED_ALL_SHARE_MAX,		ZX279133_RED_VENDOR_ALL_SHARE_MAX },
		{ ZX279133_RED_UP_IN_SHARE_MAX,		ZX279133_RED_VENDOR_UP_IN_SHARE_MAX },
		{ ZX279133_RED_DOWN_IN_SHARE_MAX,	ZX279133_RED_VENDOR_DOWN_IN_SHARE_MAX },
		{ ZX279133_RED_ALL_OUT_SHARE_MAX,	ZX279133_RED_VENDOR_ALL_OUT_SHARE_MAX },
		{ ZX279133_RED_DOWN_OUT_MAX,		ZX279133_RED_VENDOR_DOWN_OUT_MAX },
		{ ZX279133_RED_UP_OUT_MAX,		ZX279133_RED_VENDOR_UP_OUT_MAX },
		{ ZX279133_RED_DESC_WATERMARK,		ZX279133_RED_VENDOR_DESC_WATERMARK },
		{ ZX279133_RED_IDM_DESC_WATERMARK,	ZX279133_RED_VENDOR_IDM_DESC_WATERMARK },
		{ ZX279133_RED_IDM_IN_SHARE_MAX,	ZX279133_RED_VENDOR_IDM_IN_SHARE_MAX },
		{ ZX279133_RED_WOE_IN_SHARE_MAX,	ZX279133_RED_VENDOR_WOE_IN_SHARE_MAX },
		{ ZX279133_RED_WOE_OUT_SHARE_MAX,	ZX279133_RED_VENDOR_WOE_OUT_SHARE_MAX },
		{ ZX279133_RED_NP1_Q0_TH,		ZX279133_RED_VENDOR_NP1_Q0_TH },
		{ ZX279133_RED_NP1_Q1_TH,		ZX279133_RED_VENDOR_NP1_Q1_TH },
		{ ZX279133_RED_NP1_DESC_WM,		ZX279133_RED_VENDOR_NP1_DESC_WM },
	};
	static const u32 cfg[4] = {
		0xff803fff, 0x0100ff80, 0x00100200, 0x00000020,
	};
	/*
	 * What the tree's restore would write back.  The tree's
	 * zx279133_red_prepare() saves what it reads before it writes, and
	 * zx279133_red_restore() writes the saved values back, so an
	 * unprepare puts the tree's own words into the RED - including the
	 * configuration word without all_active_mode.  The saved arrays
	 * belong to the tree's header, but this overlay replaces that
	 * header, so the values a restore would write are the vendor's
	 * here.
	 */
	static const struct {
		u32 index;
		u32 value;
	} saved[] = {
		{ ZX279133_RED_SAVED_CFG,			ZX279133_RED_VENDOR_CFG },
		{ ZX279133_RED_SAVED_IN_SHARE_MAX,		ZX279133_RED_VENDOR_IN_SHARE_MAX },
		{ ZX279133_RED_SAVED_CPU133_CFG,		ZX279133_RED_VENDOR_CPU133_CFG },
		{ ZX279133_RED_SAVED_IDM_IN_SHARE_MAX,	ZX279133_RED_VENDOR_IDM_IN_SHARE_MAX },
		{ ZX279133_RED_SAVED_WOE_IN_SHARE_MAX,	ZX279133_RED_VENDOR_WOE_IN_SHARE_MAX },
		{ ZX279133_RED_SAVED_UP_IN_SHARE_MAX,	ZX279133_RED_VENDOR_UP_IN_SHARE_MAX },
		{ ZX279133_RED_SAVED_DOWN_IN_SHARE_MAX,	ZX279133_RED_VENDOR_DOWN_IN_SHARE_MAX },
		{ ZX279133_RED_SAVED_UP_OUT_MAX,		ZX279133_RED_VENDOR_UP_OUT_MAX },
		{ ZX279133_RED_SAVED_DOWN_OUT_MAX,		ZX279133_RED_VENDOR_DOWN_OUT_MAX },
		{ ZX279133_RED_SAVED_ALL_OUT_SHARE_MAX,	ZX279133_RED_VENDOR_ALL_OUT_SHARE_MAX },
		{ ZX279133_RED_SAVED_WOE_OUT_SHARE_MAX,	ZX279133_RED_VENDOR_WOE_OUT_SHARE_MAX },
		{ ZX279133_RED_SAVED_DESC_WATERMARK,	ZX279133_RED_VENDOR_DESC_WATERMARK },
		{ ZX279133_RED_SAVED_IDM_DESC_WATERMARK,	ZX279133_RED_VENDOR_IDM_DESC_WATERMARK },
	};
	unsigned int i;
	unsigned int q;

	for (i = 0; i < ARRAY_SIZE(regs); i++)
		writel(regs[i].value, eth->base + regs[i].offset);

	for (i = 0; i < ARRAY_SIZE(saved); i++)
		eth->red_saved[saved[i].index] = saved[i].value;

	/* And the same for the NP1 thresholds, which have their own array. */
	eth->red_np1_saved[ZX279133_RED_NP1_SAVED_Q0_TH] =
		ZX279133_RED_VENDOR_NP1_Q0_TH;
	for (i = 0; i < 5; i++)
		eth->red_np1_saved[ZX279133_RED_NP1_SAVED_Q1_TH + i] =
			ZX279133_RED_VENDOR_NP1_Q1_TH;
	eth->red_np1_saved[ZX279133_RED_NP1_SAVED_DESC_WM] =
		ZX279133_RED_VENDOR_NP1_DESC_WM;
	for (i = 0; i < 6; i++)
		eth->red_np1_saved[ZX279133_RED_NP1_SAVED_ISU_TH + i] =
			ZX279133_RED_VENDOR_NP1_ISU_TH;

	/* The six NP1 ISU thresholds, q 0..5. */
	for (i = 0; i < 6; i++)
		writel(ZX279133_RED_VENDOR_NP1_ISU_TH,
		       eth->base + ZX279133_RED_NP1_ISU_TH + 4 * i);

	/*
	 * The queue-buffer geometry.  ram_id 2 is the IN buffer, ram_id 0 the
	 * OUT buffer, ram_id 4 the full four-word per-queue configuration.
	 * The vendor writes one RAM at a time, in this order.
	 */
	for (q = 0; q < ZX279133_RED_QUEUE_COUNT; q++) {
		u32 word;

		if (q <= ZX279133_RED_VENDOR_UNI_LAST)
			word = ZX279133_RED_VENDOR_IN_UNI;
		else if (q <= ZX279133_RED_VENDOR_PORT_LAST)
			word = ZX279133_RED_VENDOR_IN_PORT;
		else if (q == ZX279133_RED_VENDOR_ONE_QUEUE)
			word = ZX279133_RED_VENDOR_IN_ONE;
		else
			word = 0;

		zx279133_red_ind_write(eth, 2, q, &word, 1);
	}

	for (q = 0; q < ZX279133_RED_QUEUE_COUNT; q++) {
		u32 word;

		if (q <= ZX279133_RED_VENDOR_UNI_LAST)
			word = ZX279133_RED_VENDOR_OUT_UNI;
		else if (q <= ZX279133_RED_VENDOR_PORT_LAST)
			word = ZX279133_RED_VENDOR_OUT_PORT;
		else if (q >= ZX279133_RED_VENDOR_OUT_ONE_FIRST &&
			 q <= ZX279133_RED_VENDOR_OUT_ONE_LAST)
			word = ZX279133_RED_VENDOR_OUT_ONE;
		else
			word = 0;

		zx279133_red_ind_write(eth, 0, q, &word, 1);
	}

	for (q = 0; q < ZX279133_RED_QUEUE_COUNT; q++)
		zx279133_red_ind_write(eth, 4, q, cfg, 4);
}

/*
 * The queue manager and the switch's own copy of the configuration, the way
 * this board's vendor firmware has them.
 *
 * The tree's zx279133_tm_prepare() programs both for the SR1010, and the
 * overlay cannot reach that code.  The RED's half is put back by
 * zx279133_red_vendor_fixup() above; these are the other two halves.
 *
 * They have to come from the same board: the RED, the QMG and the SMCT
 * between them decide which queue a frame goes to and how long it is, and
 * the vendor's own numbers are the only set that has ever worked on this
 * chip.  The tree's numbers belong to a different board with a different
 * buffer pool, and a RED with one board's queue geometry in front of a QMG
 * with another's is how a frame ends up at the RED with a length of zero.
 *
 * The WOE watermark at 0x20088 is shared with the RED's own copy of it and
 * is already written by zx279133_red_vendor_fixup().
 */
static void zx279133_tm_vendor_fixup(struct zx279133_eth *eth)
{
	static const struct {
		u16 offset;
		u32 value;
	} regs[] = {
		{ ZX279133_QMG_WATERMARK,	ZX279133_QMG_VENDOR_WATERMARK },
		{ ZX279133_QMG_DESC_OUT,	ZX279133_QMG_VENDOR_DESC_OUT },
		{ ZX279133_QMG_WOE_OUT,		ZX279133_QMG_VENDOR_WOE_OUT },
		{ ZX279133_QMG_DESC_IN,		ZX279133_QMG_VENDOR_DESC_IN },
		{ ZX279133_SMCT_SEL_CFG0,	ZX279133_SMCT_VENDOR_SEL_CFG0 },
		{ ZX279133_SMCT_COS_TH0,	ZX279133_SMCT_VENDOR_COS_TH0 },
		{ ZX279133_SMCT_COS_TH0 + 4,	ZX279133_SMCT_VENDOR_COS_TH1 },
		{ ZX279133_SMCT_COS_TH0 + 8,	ZX279133_SMCT_VENDOR_COS_TH2 },
		{ ZX279133_SMCT_COS_TH0 + 12,	ZX279133_SMCT_VENDOR_COS_TH3 },
		{ ZX279133_SMCT_MC_TH,		ZX279133_SMCT_VENDOR_MC_TH },
		{ ZX279133_SMCT_NP1_TH,		ZX279133_SMCT_VENDOR_NP1_TH },
		{ ZX279133_SMCT_IDM_SSCH_TH,	ZX279133_SMCT_VENDOR_IDM_SSCH_TH },
	};
	unsigned int i;

	for (i = 0; i < ARRAY_SIZE(regs); i++)
		writel(regs[i].value, eth->base + regs[i].offset);

	/* The eight per-input-port thresholds. */
	for (i = 0; i < 8; i++)
		writel(ZX279133_SMCT_VENDOR_INPORT_TH,
		       eth->base + ZX279133_SMCT_INPORT_TH0 + 4 * i);
}

/*
 * The register image of the RED, the IDM and the CPU port's MAC, printed once
 * at bring-up.
 *
 * The reason is the bench, not the driver: the smart plug that feeds this
 * board cuts its power every few minutes, and a trial kernel lives in RAM, so
 * most boots are over before a shell exists.  Anything that has to be asked
 * for over the serial console is therefore measured on a random sample of the
 * boots at best.  Printing the image from the driver puts it in the console
 * log of *every* boot, and the console log is on the PC, so it survives the
 * power cut.
 *
 * The values to compare against are the vendor board's, in
 * staging/ctl/vendor-serial-mods/tm-red.txt (the RED block) and
 * nppu-idm.txt (the IDM block).
 */
static void zx279133_diag_dump(struct zx279133_eth *eth)
{
	static const struct {
		u16 offset;
		const char *name;
	} regs[] = {
		{ ZX279133_RED_CFG,			"RED_CFG" },
		{ ZX279133_RED_IN_SHARE_MAX,		"RED_IN_SHARE_MAX" },
		{ ZX279133_RED_ALL_SHARE_MAX,		"RED_ALL_SHARE_MAX" },
		{ ZX279133_RED_UP_IN_SHARE_MAX,		"RED_UP_IN_SHARE_MAX" },
		{ ZX279133_RED_DOWN_IN_SHARE_MAX,	"RED_DOWN_IN_SHARE_MAX" },
		{ ZX279133_RED_ALL_OUT_SHARE_MAX,	"RED_ALL_OUT_SHARE_MAX" },
		{ ZX279133_RED_DOWN_OUT_MAX,		"RED_DOWN_OUT_MAX" },
		{ ZX279133_RED_UP_OUT_MAX,		"RED_UP_OUT_MAX" },
		{ ZX279133_RED_DESC_WATERMARK,		"RED_DESC_WATERMARK" },
		{ ZX279133_RED_IDM_DESC_WATERMARK,	"RED_IDM_DESC_WATERMARK" },
		{ ZX279133_RED_POOL_COUNT,		"RED_POOL_COUNT" },
		{ ZX279133_RED_POOL_COUNT_JUMBO,	"RED_POOL_COUNT_JUMBO" },
		{ ZX279133_RED_DROP_ENABLE,		"RED_DROP_ENABLE" },
		{ ZX279133_RED_LEN_FILTER,		"RED_LEN_FILTER" },
		{ ZX279133_RED_MINI_PKT_LEN,		"RED_MINI_PKT_LEN" },
		{ ZX279133_RED_IDM_IN_SHARE_MAX,		"RED_IDM_IN_SHARE_MAX" },
		{ ZX279133_RED_WOE_IN_SHARE_MAX,		"RED_WOE_IN_SHARE_MAX" },
		{ ZX279133_RED_WOE_OUT_SHARE_MAX,	"RED_WOE_OUT_SHARE_MAX" },
		{ ZX279133_RED_NP1_Q0_TH,		"RED_NP1_Q0_TH" },
		{ ZX279133_RED_NP1_Q1_TH,		"RED_NP1_Q1_TH" },
		{ ZX279133_RED_NP1_DESC_WM,		"RED_NP1_DESC_WM" },
		{ ZX279133_RED_NP1_ISU_TH,		"RED_NP1_ISU_TH0" },
		{ ZX279133_RED_NP1_ISU_TH + 4,		"RED_NP1_ISU_TH1" },
		{ ZX279133_RED_NP1_ISU_TH + 8,		"RED_NP1_ISU_TH2" },
		{ ZX279133_RED_NP1_ISU_TH + 12,		"RED_NP1_ISU_TH3" },
		{ ZX279133_RED_NP1_ISU_TH + 16,		"RED_NP1_ISU_TH4" },
		{ ZX279133_RED_NP1_ISU_TH + 20,		"RED_NP1_ISU_TH5" },
		{ ZX279133_RED_NP1_ISU_TH + 24,		"RED_NP1_ISU_TH6" },
		{ ZX279133_RED_CPU133_CFG,		"RED_CPU133_CFG" },
		{ ZX279133_GREG_RED_BUFFER_USABLE,	"RED_BUFFER_USABLE" },
	};
	static const u32 idm_offsets[] = {
		0x0000, 0x0008, 0x000c, 0x0010, 0x0070, 0x0074,
		0x007c, 0x0090, 0x00c0, 0x0104, 0x0108, 0x010c,
		0x0110, 0x0118, 0x011c, 0x0408, 0x0418,
		/* The three RX-ring words, last: they are the ones the debugfs
		 * "reg" command has never managed to print.
		 */
		0x0180, 0x018c, 0x0190,
	};
	static const u32 queues[] = {
		0x000, 0x13f, 0x140, 0x160, 0x17f, 0x180, 0x187, 0x190,
	};
	static const u8 no_mac[ETH_ALEN];
	u32 data[4];
	unsigned int i;

	dev_info(eth->dev, "DIAG mac %pM\n",
		 eth->ndev ? eth->ndev->dev_addr : no_mac);
	if (eth->lan_ndev)
		dev_info(eth->dev, "DIAG lan mac %pM\n", eth->lan_ndev->dev_addr);

	for (i = 0; i < ARRAY_SIZE(regs); i++)
		dev_info(eth->dev, "DIAG nppt 0x%05x %-22s = 0x%08x\n",
			 regs[i].offset, regs[i].name,
			 readl(eth->base + regs[i].offset));

	for (i = 0; i < ARRAY_SIZE(idm_offsets); i++)
		dev_info(eth->dev, "DIAG idm 0x%05x              = 0x%08x\n",
			 idm_offsets[i],
			 readl(eth->base + ZX279133_IDM_BASE + idm_offsets[i]));

	/*
	 * The queue manager and the switch's own copy of the configuration.
	 * Both are printed whole, because both are compared against the
	 * vendor's board word by word and a missing word is a difference
	 * too.
	 */
	for (i = 0; i < 0x1c; i++)
		dev_info(eth->dev, "DIAG qmg 0x%05x              = 0x%08x\n",
			 0x24000 + 4 * i,
			 readl(eth->base + ZX279133_QMG_RAM_REQ + 4 * i));
	for (i = 0; i < 0x1c; i++)
		dev_info(eth->dev, "DIAG smct 0x%05x             = 0x%08x\n",
			 0xc000 + 4 * i,
			 readl(eth->base + ZX279133_SMCT_SEL_CFG0 + 4 * i));

	for (i = 0; i < ARRAY_SIZE(queues); i++) {
		zx279133_red_ind_read(eth, 2, queues[i], data, 1);
		dev_info(eth->dev, "DIAG red ram2 q%03x           = 0x%08x\n",
			 queues[i], data[0]);
		zx279133_red_ind_read(eth, 0, queues[i], data, 1);
		dev_info(eth->dev, "DIAG red ram0 q%03x           = 0x%08x\n",
			 queues[i], data[0]);
	}

	/* The four internal-PHY MACs and their SOPC publish words. */
	for (i = 0; i < ZX279133_SMAC_COUNT; i++) {
		u32 base = ZX279133_SMAC_BASE(i);

		dev_info(eth->dev, "DIAG smac%u an_ctrl  = 0x%08x an_ctrl2 = 0x%08x\n",
			 i, readl(eth->base + base + ZX279133_SMAC_AN_CTRL),
			 readl(eth->base + base + ZX279133_SMAC_AN_CTRL2));
		dev_info(eth->dev, "DIAG smac%u pkt_filt = 0x%08x speed    = 0x%08x\n",
			 i, readl(eth->base + base + ZX279133_SMAC_PKT_FILTER),
			 readl(eth->base + base + ZX279133_SMAC_SPEED));
		dev_info(eth->dev, "DIAG smac%u port_ctl = 0x%08x\n", i,
			 readl(eth->base + base + ZX279133_SMAC_PORT_CTRL));
		dev_info(eth->dev, "DIAG sopc%u ready = 0x%08x send = 0x%08x\n", i,
			 readl(eth->base + ZX279133_SMAC_SOPC_READY_BASE + 4 * i),
			 readl(eth->base + ZX279133_SMAC_SOPC_SEND_BASE + 4 * i));
	}

	/* The CPU port's MAC. */
	dev_info(eth->dev, "DIAG xmac0 tx_ctrl = 0x%08x rx_ctrl = 0x%08x\n",
		 readl(eth->base + ZX279133_XMAC0_BASE + ZX279133_XMAC_TX_CTRL),
		 readl(eth->base + ZX279133_XMAC0_BASE + ZX279133_XMAC_RX_CTRL));
	dev_info(eth->dev, "DIAG xmac0 frame   = 0x%08x mode    = 0x%08x misc   = 0x%08x\n",
		 readl(eth->base + ZX279133_XMAC0_BASE + ZX279133_XMAC_FRAME_CFG),
		 readl(eth->base + ZX279133_XMAC0_BASE + ZX279133_XMAC_MODE_CFG),
		 readl(eth->base + ZX279133_XMAC0_BASE + ZX279133_XMAC_MISC_CFG));
}

/*
 * Write the vendor's configuration of the RED, the queue manager and the
 * switch's own copy of it.
 *
 * The configuration is written once when the hardware is prepared, and again
 * here - after every driver has had its say.  The reason is that the LAN
 * child devices are probed after this one, and the switch driver resets the
 * chip's MACs while it does that: the reset word at NPPT + 0x2c0004 is
 * pulsed per module, and a pulse that lands on the traffic manager takes
 * the RED's configuration with it.
 *
 * Measured 2026-10-23: the register image the driver prints at the end of
 * its own probe - which is *after* the LAN devices have been probed - reads
 * the RED's configuration word as 0x7e, the value the tree's own
 * zx279133_red_prepare() writes, even though every constant in zx279133.h
 * names the vendor's value and the vendor's board reads 0x9e.  Written by
 * hand on the board at any later moment, the same register keeps the value
 * that is written into it, so the write itself is not the problem: the
 * configuration has to be written after the last reset, not before it.
 */
void zx279133_nppt_vendor_reconfigure(struct zx279133_eth *eth)
{
	u32 gate = zx279133_red_gate_clear(eth);

	zx279133_red_vendor_fixup(eth);
	zx279133_tm_vendor_fixup(eth);
	zx279133_red_gate_restore(eth, gate);
}

int zx279133_hardware_prepare(struct zx279133_eth *eth)
{
	int ret;
	u32 gate;

	/*
	 * The tree's zx279133_tm_prepare() begins by resetting the whole
	 * NPPT - it clears SDET_RESET_N and PON_RESET_N - and the 9132's
	 * configuration lives inside that NPPT: the protocol action table,
	 * the outport map, the trap's own words, the isolation matrix.  So a
	 * second prepare that fails anywhere after that reset leaves the
	 * chip with no configuration at all, and no frame ever reaches the
	 * CPU.
	 *
	 * Run 133 measured exactly that.  The driver printed the whole 9132
	 * set-up once, at probe time, and the board's own diagnostic dump -
	 * printed at the end of the same probe - read the RED's
	 * configuration word as the vendor's 0x9e.  Thirty-four seconds
	 * later the board read back the tree's 0x7e, which only
	 * zx279133_red_prepare() ever writes, and not one counter in the
	 * chip moved while the PC pinged for six minutes.
	 *
	 * So the prepare has to be the one that already ran.  A prepare
	 * after an unprepare is still a full one, because
	 * zx279133_hardware_unprepare() clears the flag.
	 */
	if (READ_ONCE(eth->hardware_prepared))
		return 0;

	ret = zx279133_idm_cci_enable(eth);
	if (ret)
		return ret;

	ret = clk_bulk_prepare_enable(ARRAY_SIZE(eth->clocks), eth->clocks);
	if (ret)
		goto err_cci_restore;

	ret = zx279133_tm_prepare(eth);
	if (ret) {
		/*
		 * The tree's zx279133_tm_prepare() resets the NPPT on the way
		 * in, so a failure here leaves the chip reset - and the 9132
		 * lives in that NPPT.  Giving up at this point leaves the
		 * board with no switch configuration at all, which is what
		 * run 133 did: the second prepare, the one the RSS and XSK
		 * paths ask for when they resume the data path, failed after
		 * the reset, and from then on no frame from a jack reached
		 * the CPU.
		 *
		 * So the vendor's configuration and the 9132's own set-up
		 * are written anyway.  They are what the chip needs to move a
		 * frame, and they do not depend on the traffic manager having
		 * come up.
		 */
		dev_warn(eth->dev,
			 "the traffic manager's prepare failed (%d); the vendor's configuration is written anyway\n",
			 ret);
	}
	zx279133_bmu_threshold_fixup(eth);
	/*
	 * The RED's, the queue manager's and the switch's configuration all
	 * sit behind the auto-gate, so they are written with it clear.
	 */
	gate = zx279133_red_gate_clear(eth);
	zx279133_red_vendor_fixup(eth);
	zx279133_tm_vendor_fixup(eth);
	zx279133_red_gate_restore(eth, gate);
	ret = zx279133_np_prepare(eth);
	if (ret)
		goto err_tm_restore;
	/*
	 * The NPPT now holds the 9132's configuration and the switch
	 * driver has programmed the four port MACs into it.  A later
	 * prepare - the datapath suspend and resume that the RSS and the
	 * XSK paths ask for - must not reset the NPPT again, because the
	 * reset would wipe all of that and the switch driver does not run
	 * a second time.
	 */
	eth->nppt_ready = true;
	zx279133_route_set(eth, true);
	ret = zx279133_vlan_runtime_prepare(eth);
	if (ret)
		goto err_route_off;
	zx279133_xmac_set_enabled(eth, false);

	/* A failed stop may leave the generic PHY power reference held. Retry
	 * the power-off before programming a new mode so the provider callback
	 * cannot be skipped by the following power-on.
	 */
	if (eth->serdes_powered) {
		ret = phy_power_off(eth->serdes);
		if (ret)
			goto err_route_off;
		eth->serdes_powered = false;
	}

	ret = phy_set_mode_ext(eth->serdes, PHY_MODE_ETHERNET,
			       eth->host_interface);
	if (ret)
		goto err_route_off;

	ret = phy_power_on(eth->serdes);
	if (ret)
		goto err_route_off;
	eth->serdes_powered = true;
	eth->serdes_interface = eth->host_interface;
	usleep_range(2000, 2500);
	eth->hardware_prepared = true;
	zx279133_ptp_start(eth);

	return 0;

err_route_off:
	zx279133_route_set(eth, false);
	zx279133_np_restore(eth);
err_tm_restore:
	zx279133_tm_restore(eth);
err_clk_disable:
	clk_bulk_disable_unprepare(ARRAY_SIZE(eth->clocks), eth->clocks);
err_cci_restore:
	zx279133_idm_cci_restore(eth);
	return ret;
}

void zx279133_hardware_unprepare(struct zx279133_eth *eth)
{
	int ret;

	if (!eth->hardware_prepared)
		return;

	zx279133_ptp_stop(eth);
	zx279133_xmac_set_enabled(eth, false);
	zx279133_flow_offload_flush(eth);
	if (eth->xpcs_runtime_held)
		zx279133_xpcs_set_bypass(eth, false);
	ret = phy_power_off(eth->serdes);
	if (ret) {
		dev_err(eth->dev, "failed to power off SerDes: %d\n", ret);
	} else {
		eth->serdes_powered = false;
		eth->serdes_interface = PHY_INTERFACE_MODE_NA;
	}
	zx279133_route_set(eth, false);
	zx279133_np_restore(eth);
	zx279133_tm_restore(eth);
	clk_bulk_disable_unprepare(ARRAY_SIZE(eth->clocks), eth->clocks);
	zx279133_idm_cci_restore(eth);
	eth->hardware_prepared = false;
}

static void zx279133_rx_mode_work_cancel(void *data)
{
	struct zx279133_eth *eth = data;

	cancel_work_sync(&eth->rx_mode_work);
}

static void zx279133_pon_led_cancel(void *data)
{
	struct zx279133_eth *eth = data;

	cancel_delayed_work_sync(&eth->pon_led_work);
}

static void zx279133_pon_led_work(struct work_struct *work)
{
	struct zx279133_eth *eth =
		container_of(to_delayed_work(work), struct zx279133_eth,
			     pon_led_work);
	int rxsd;

	if (!eth->opsd_gpio || !eth->los_led)
		return;

	rxsd = gpiod_get_value_cansleep(eth->opsd_gpio);
	if (rxsd >= 0)
		led_set_brightness(eth->los_led,
				   rxsd ? LED_OFF : eth->los_led->max_brightness);

	schedule_delayed_work(&eth->pon_led_work, msecs_to_jiffies(200));
}

static int zx279133_eth_probe(struct platform_device *pdev)
{
	struct device *dev = &pdev->dev;
	struct net_device *ndev;
	struct zx279133_eth *eth;
	struct device_node *pcs_np;
	struct device_node *bmu_np;
	struct device_node *se_hash_np;
	struct device_node *idm_np;
	struct reserved_mem *bmu_rmem;
	struct reserved_mem *se_hash_rmem;
	struct reserved_mem *idm_rmem;
	unsigned int i;
	int ret;

	ndev = devm_alloc_etherdev_mqs(dev, sizeof(*eth),
				       ZX279133_CPU_TX_QUEUES, ZX279133_CPU_RX_QUEUES);
	if (!ndev)
		return -ENOMEM;

	SET_NETDEV_DEV(ndev, dev);
	/* Normal completion is interrupt/NAPI driven; one second keeps a lost
	 * completion interrupt below common protocol retry timeouts.
	 */
	ndev->watchdog_timeo = HZ;
	eth = netdev_priv(ndev);
	eth->dev = dev;
	eth->ndev = ndev;
	eth->lan_tx_port = ZX279133_LAN_TX_PORT;
	eth->lan_tx_word4 = ZX279133_LAN_TX_WORD4;
	eth->lan_untag_svid = false;
	ndev->tstats = devm_netdev_alloc_pcpu_stats(dev,
						    struct pcpu_sw_netstats);
	if (!ndev->tstats)
		return -ENOMEM;
	zx279133_netdev_stats_init(&eth->stats);
	atomic64_set(&eth->rx_irq_count, 0);
	atomic64_set(&eth->idm_local_irq_count, 0);
	atomic64_set(&eth->rx_refill_retry_work_runs, 0);
	u64_stats_init(&eth->rx_stats[0].syncp);
	u64_stats_init(&eth->rx_stats[1].syncp);
	spin_lock_init(&eth->rx_buffer_lock);
	atomic_set(&eth->rx_poll_active, 0);
	spin_lock_init(&eth->tx_lock);
	spin_lock_init(&eth->irq_lock);
	spin_lock_init(&eth->ptp_lock);
	mutex_init(&eth->ptp_cmd_lock);
	INIT_DELAYED_WORK(&eth->tx_reclaim_work,
			  zx279133_idm_tx_reclaim_work);
	INIT_DELAYED_WORK(&eth->rx_refill_work,
			  zx279133_idm_rx_refill_work);
	INIT_WORK(&eth->rx_mode_work, zx279133_rx_mode_work);
	INIT_DELAYED_WORK(&eth->pon_led_work, zx279133_pon_led_work);
	mutex_init(&eth->xmac_lock);
	mutex_init(&eth->datapath_lock);
	mutex_init(&eth->np_ind_lock);
	mutex_init(&eth->wanid_lock);
	for (i = 0; i < ZX279133_CPU_TX_QUEUES; i++) {
		eth->tx[i].slots = devm_kcalloc(dev, ZX279133_IDM_TX_DEPTH,
						sizeof(*eth->tx[i].slots),
					      GFP_KERNEL);
		if (!eth->tx[i].slots)
			return -ENOMEM;
	}

	/*
	 * The optical RX signal detect is optional: without it the red LOS
	 * lamp keeps its fail-safe "on" state.  Both lookups may defer until
	 * the GPIO controller and the gpio-leds device exist.
	 */
	eth->opsd_gpio = devm_gpiod_get_optional(dev, "opsd", GPIOD_IN);
	if (IS_ERR(eth->opsd_gpio)) {
		ret = PTR_ERR(eth->opsd_gpio);
		if (ret == -EPROBE_DEFER)
			return ret;
		dev_warn(dev, "no optical RX signal detect GPIO: %d\n", ret);
		eth->opsd_gpio = NULL;
	}

	eth->los_led = devm_led_get(dev, "los");
	if (IS_ERR(eth->los_led)) {
		ret = PTR_ERR(eth->los_led);
		if (ret == -EPROBE_DEFER)
			return ret;
		dev_warn(dev, "no LOS LED: %d\n", ret);
		eth->los_led = NULL;
	}

	ret = devm_add_action_or_reset(dev, zx279133_pon_led_cancel, eth);
	if (ret)
		return ret;

	if (eth->opsd_gpio && eth->los_led)
		schedule_delayed_work(&eth->pon_led_work, 0);

	eth->base = devm_platform_ioremap_resource(pdev, 0);
	if (IS_ERR(eth->base))
		return PTR_ERR(eth->base);
	eth->pps_base = devm_platform_ioremap_resource_byname(pdev, "pps");
	if (IS_ERR(eth->pps_base))
		return PTR_ERR(eth->pps_base);

	bmu_np = of_parse_phandle(dev->of_node, "memory-region", 0);
	if (!bmu_np)
		return dev_err_probe(dev, -EINVAL,
				     "missing BMU reserved memory\n");
	bmu_rmem = of_reserved_mem_lookup(bmu_np);
	of_node_put(bmu_np);
	if (!bmu_rmem)
		return dev_err_probe(dev, -EINVAL,
				     "invalid BMU reserved memory\n");
	if (bmu_rmem->size < ZX279133_BMU_REQUIRED_SIZE)
		return dev_err_probe(dev, -EINVAL,
				     "BMU reserved memory is too small\n");
	if (upper_32_bits(bmu_rmem->base + ZX279133_BMU_REQUIRED_SIZE - 1))
		return dev_err_probe(dev, -ERANGE,
				     "BMU reserved memory exceeds 32-bit DMA range\n");
	eth->bmu_base = bmu_rmem->base;
	eth->bmu_size = bmu_rmem->size;
	eth->bmu_mem = devm_ioremap_wc(dev, eth->bmu_base, eth->bmu_size);
	if (!eth->bmu_mem)
		return dev_err_probe(dev, -ENOMEM,
				     "failed to map BMU reserved memory\n");

	se_hash_np = of_parse_phandle(dev->of_node, "memory-region", 1);
	if (!se_hash_np)
		return dev_err_probe(dev, -EINVAL,
				     "missing SE hash reserved memory\n");
	se_hash_rmem = of_reserved_mem_lookup(se_hash_np);
	of_node_put(se_hash_np);
	if (!se_hash_rmem)
		return dev_err_probe(dev, -EINVAL,
				     "invalid SE hash reserved memory\n");
	if (se_hash_rmem->size < ZX279133_SE_HASH_REQUIRED_SIZE)
		return dev_err_probe(dev, -EINVAL,
				     "SE hash reserved memory is too small\n");
	if (!IS_ALIGNED(se_hash_rmem->base, SZ_4K) ||
	    upper_32_bits((se_hash_rmem->base +
			   ZX279133_SE_HASH_REQUIRED_SIZE - 1) >> 12))
		return dev_err_probe(dev, -ERANGE,
				     "SE hash reserved memory is not representable\n");
	eth->se_hash_base = se_hash_rmem->base;
	eth->se_hash_size = se_hash_rmem->size;
	eth->se_hash_mem = devm_ioremap_wc(dev, eth->se_hash_base,
					   ZX279133_SE_HASH_REQUIRED_SIZE);
	if (!eth->se_hash_mem)
		return dev_err_probe(dev, -ENOMEM,
				     "failed to map SE hash reserved memory\n");

	idm_np = of_parse_phandle(dev->of_node, "memory-region", 2);
	if (!idm_np)
		return dev_err_probe(dev, -EINVAL,
				     "missing IDM reserved memory\n");
	idm_rmem = of_reserved_mem_lookup(idm_np);
	of_node_put(idm_np);
	if (!idm_rmem)
		return dev_err_probe(dev, -EINVAL,
				     "invalid IDM reserved memory\n");
	eth->idm_base = idm_rmem->base;
	eth->idm_size = idm_rmem->size;
	if (eth->idm_size < ZX279133_IDM_REQUIRED_SIZE)
		return dev_err_probe(dev, -EINVAL,
				     "IDM reserved memory is too small\n");
	if (eth->idm_base > U32_MAX ||
	    eth->idm_size - 1 > U32_MAX - eth->idm_base)
		return dev_err_probe(dev, -ERANGE,
				     "IDM reserved memory exceeds 32-bit DMA range\n");
	eth->idm_mem = devm_ioremap_wc(dev, eth->idm_base, eth->idm_size);
	if (!eth->idm_mem)
		return dev_err_probe(dev, -ENOMEM,
				     "failed to map IDM reserved memory\n");
	eth->pon_route = syscon_regmap_lookup_by_phandle(dev->of_node,
							 "zte,pon-route");
	if (IS_ERR(eth->pon_route))
		return dev_err_probe(dev, PTR_ERR(eth->pon_route),
				     "failed to get PON route syscon\n");

	eth->idm_cci = syscon_regmap_lookup_by_phandle(dev->of_node,
						       "zte,idm-cci");
	if (IS_ERR(eth->idm_cci))
		return dev_err_probe(dev, PTR_ERR(eth->idm_cci),
				     "failed to get IDM CCI syscon\n");

	ret = dma_set_mask_and_coherent(dev, DMA_BIT_MASK(32));
	if (ret)
		return dev_err_probe(dev, ret, "32-bit DMA is unavailable\n");
	{
		size_t rx_descs_size = sizeof(*eth->rx_descs) *
			ZX279133_IDM_RX_QUEUES * ZX279133_IDM_RX_RING_SIZE;
		size_t rx_normal_bp_size =
			(ZX279133_IDM_BP_RING_SIZE +
			 ZX279133_IDM_JUMBO_BP_RING_SIZE) * sizeof(__be32);
		size_t tx_descs_size = sizeof(*eth->tx_descs) *
			ZX279133_IDM_TX_QUEUES * ZX279133_IDM_TX_DEPTH;

		eth->rx_descs = dmam_alloc_coherent(dev, rx_descs_size,
						    &eth->rx_descs_dma,
						 GFP_KERNEL);
		if (!eth->rx_descs)
			return -ENOMEM;
		if (upper_32_bits(eth->rx_descs_dma + rx_descs_size - 1))
			return dev_err_probe(dev, -ERANGE,
					     "RX coherent descriptor rings are above 4GiB\n");

		/*
		 * The normal and the jumbo buffer-pool rings have to be one
		 * allocation: the hardware expects the jumbo pool immediately
		 * after the normal one (idm_rx_bp_init() adds the normal pool
		 * size to the normal pool base to get the jumbo pool base).
		 */
		eth->rx_normal_bp = dmam_alloc_coherent(dev, rx_normal_bp_size,
							&eth->rx_normal_bp_dma,
						      GFP_KERNEL);
		if (!eth->rx_normal_bp)
			return -ENOMEM;
		if (upper_32_bits(eth->rx_normal_bp_dma +
				  rx_normal_bp_size - 1))
			return dev_err_probe(dev, -ERANGE,
					     "RX coherent free ring is above 4GiB\n");
		eth->rx_jumbo_bp = eth->rx_normal_bp + ZX279133_IDM_BP_RING_SIZE;
		eth->rx_jumbo_bp_dma = eth->rx_normal_bp_dma +
				       ZX279133_IDM_BP_RING_SIZE * sizeof(__be32);

		eth->tx_descs = dmam_alloc_coherent(dev, tx_descs_size,
						    &eth->tx_descs_dma,
						 GFP_KERNEL);
		if (!eth->tx_descs)
			return -ENOMEM;
		if (upper_32_bits(eth->tx_descs_dma + tx_descs_size - 1))
			return dev_err_probe(dev, -ERANGE,
					     "TX coherent descriptor ring is above 4GiB\n");
	}
	for (i = 0; i < ARRAY_SIZE(eth->clocks); i++)
		eth->clocks[i].id = zx279133_clock_names[i];

	ret = devm_clk_bulk_get(dev, ARRAY_SIZE(eth->clocks), eth->clocks);
	if (ret)
		return dev_err_probe(dev, ret, "failed to get clocks\n");

	for (i = 0; i < ARRAY_SIZE(eth->irqs); i++) {
		eth->irqs[i] = platform_get_irq_byname(pdev, zx279133_irq_names[i]);
		if (eth->irqs[i] < 0)
			return dev_err_probe(dev, eth->irqs[i],
					     "failed to get %s IRQ\n",
					     zx279133_irq_names[i]);
	}

	eth->serdes = devm_phy_get(dev, "serdes");
	if (IS_ERR(eth->serdes))
		return dev_err_probe(dev, PTR_ERR(eth->serdes),
				     "failed to get SerDes PHY\n");

	pcs_np = of_parse_phandle(dev->of_node, "pcs-handle", 0);
	if (!pcs_np)
		return dev_err_probe(dev, -ENODEV, "PCS phandle is missing\n");

	eth->xpcs_mdiodev = fwnode_mdio_find_device(of_fwnode_handle(pcs_np));
	if (!eth->xpcs_mdiodev) {
		of_node_put(pcs_np);
		return dev_err_probe(dev, -EPROBE_DEFER,
				     "XPCS MDIO device is unavailable\n");
	}

	ret = devm_add_action_or_reset(dev, zx279133_mdio_device_put,
				       eth->xpcs_mdiodev);
	if (ret) {
		of_node_put(pcs_np);
		return ret;
	}

	eth->xpcs = xpcs_create_fwnode(of_fwnode_handle(pcs_np));
	of_node_put(pcs_np);

	/*
	 * The LAN lane's PCS, optional: a board whose outerphy hangs off XMAC0
	 * names it, and zx279133_eth0_xmac=0 needs it to be the PCS of the MAC
	 * it just moved.  Not fatal when absent - the parameter's default does
	 * not use it, and every older DT still probes.
	 */
	pcs_np = of_parse_phandle(dev->of_node, "zte,lan-pcs-handle", 0);
	if (pcs_np) {
		eth->xpcs_lan_mdiodev = fwnode_mdio_find_device(of_fwnode_handle(pcs_np));
		if (!eth->xpcs_lan_mdiodev) {
			of_node_put(pcs_np);
			return dev_err_probe(dev, -EPROBE_DEFER,
					     "LAN XPCS MDIO device is unavailable\n");
		}
		ret = devm_add_action_or_reset(dev, zx279133_mdio_device_put,
					       eth->xpcs_lan_mdiodev);
		if (ret) {
			of_node_put(pcs_np);
			return ret;
		}
		eth->xpcs_lan = xpcs_create_fwnode(of_fwnode_handle(pcs_np));
		of_node_put(pcs_np);
		if (IS_ERR(eth->xpcs_lan))
			return dev_err_probe(dev, PTR_ERR(eth->xpcs_lan),
					     "failed to create the LAN XPCS\n");
	}
	if (IS_ERR(eth->xpcs))
		return dev_err_probe(dev, PTR_ERR(eth->xpcs),
				     "failed to create XPCS handle\n");

	ret = devm_add_action_or_reset(dev, zx279133_xpcs_destroy, eth->xpcs);
	if (ret)
		return ret;

	ret = device_get_phy_mode(dev);
	if (ret < 0)
		return dev_err_probe(dev, ret,
				     "failed to get initial PHY interface mode\n");
	eth->host_interface = ret;

	eth->phylink_config.dev = &ndev->dev;
	eth->phylink_config.type = PHYLINK_NETDEV;
	/*
	 * The factory XMAC1 path leaves 802.3x pause disabled. Advertising it
	 * enables RX_FLOW, which throttles WAN transmit under forwarding load.
	 */
	eth->phylink_config.mac_capabilities =
		MAC_2500FD | MAC_1000FD |
		MAC_100 | MAC_10;
	__set_bit(PHY_INTERFACE_MODE_2500BASEX,
		  eth->phylink_config.supported_interfaces);
	__set_bit(PHY_INTERFACE_MODE_SGMII,
		  eth->phylink_config.supported_interfaces);
	__set_bit(PHY_INTERFACE_MODE_2500BASEX,
		  eth->phylink_config.lpi_interfaces);
	__set_bit(PHY_INTERFACE_MODE_SGMII,
		  eth->phylink_config.lpi_interfaces);
	eth->phylink_config.lpi_capabilities =
		MAC_2500FD | MAC_1000FD | MAC_100FD;
	eth->phylink_config.lpi_timer_default = 1000;
	xpcs_config_eee_mult_fact(eth->xpcs, 1);

	eth->phylink = phylink_create(&eth->phylink_config, dev_fwnode(dev),
				      eth->host_interface,
				      &zx279133_phylink_ops);
	if (IS_ERR(eth->phylink))
		return dev_err_probe(dev, PTR_ERR(eth->phylink),
				     "failed to create phylink\n");

	ret = devm_add_action_or_reset(dev, zx279133_phylink_destroy,
				       eth->phylink);
	if (ret)
		return ret;
	ret = zx279133_flow_offload_init(eth);
	if (ret)
		return ret;
	ret = zx279133_wanid_debugfs_init(eth);
	if (ret)
		return ret;
	ret = zx279133_9132_debugfs_init(eth);
	if (ret)
		return ret;
	ret = zx279133_ptp_init(eth);
	if (ret)
		return dev_err_probe(dev, ret, "failed to register PHC\n");

	ndev->netdev_ops = &zx279133_netdev_ops;
	ndev->ethtool_ops = &zx279133_ethtool_ops;
	ndev->priv_flags |= IFF_LIVE_ADDR_CHANGE;
	if (zx279133_tx_hw_csum) {
		/* IDM checksum generation is IPv4/TCP-only. */
		ndev->hw_features |= NETIF_F_HW_CSUM;
		ndev->features |= NETIF_F_HW_CSUM;
		ndev->vlan_features |= NETIF_F_HW_CSUM;
	}
	ndev->hw_features |= NETIF_F_HW_TC;
	ndev->features |= NETIF_F_HW_TC;
	ndev->hw_features |= NETIF_F_RXCSUM;
	ndev->features |= NETIF_F_RXCSUM;
	ndev->vlan_features |= NETIF_F_RXCSUM;
	ndev->hw_features |= NETIF_F_RXHASH;
	ndev->features |= NETIF_F_RXHASH;
	ndev->xdp_features = NETDEV_XDP_ACT_BASIC | NETDEV_XDP_ACT_REDIRECT |
			     NETDEV_XDP_ACT_NDO_XMIT |
			     NETDEV_XDP_ACT_XSK_ZEROCOPY;
	ndev->min_mtu = ETH_MIN_MTU;
	ndev->max_mtu = ZX279133_MAX_MTU;
	ret = of_get_ethdev_address(dev->of_node, ndev);
	if (ret == -EPROBE_DEFER ||
	    (ret && of_property_present(dev->of_node, "nvmem-cells")))
		return dev_err_probe(dev, ret, "failed to read factory MAC address\n");
	if (ret)
		eth_hw_addr_random(ndev);
	netif_carrier_off(ndev);

	netif_napi_add(ndev, &eth->napi, zx279133_idm_rx_poll);
	ret = devm_add_action_or_reset(dev, zx279133_napi_del, &eth->napi);
	if (ret)
		return ret;

	netif_napi_add(ndev, &eth->lan_napi, zx279133_idm_lan_rx_poll);
	ret = devm_add_action_or_reset(dev, zx279133_napi_del, &eth->lan_napi);
	if (ret)
		return ret;

	{
		struct page_pool_params params = {
			.order = ZX279133_RX_PAGE_ORDER,
			.pool_size = ZX279133_IDM_RX_BUFFER_COUNT,
			.nid = NUMA_NO_NODE,
			.dev = dev,
			.napi = &eth->napi,
			.dma_dir = DMA_BIDIRECTIONAL,
			.max_len = ZX279133_IDM_RX_FRAME_LIMIT,
			.offset = ZX279133_IDM_RX_PAYLOAD_OFFSET,
			.netdev = ndev,
			.queue_idx = 0,
			.flags = PP_FLAG_DMA_MAP | PP_FLAG_DMA_SYNC_DEV,
		};

		eth->rx_page_map = devm_kcalloc(dev,
						ZX279133_IDM_RX_PAGE_MAP_SIZE,
					       sizeof(*eth->rx_page_map),
					       GFP_KERNEL);
		if (!eth->rx_page_map)
			return -ENOMEM;
		eth->rx_page_pool = page_pool_create(&params);
		if (IS_ERR(eth->rx_page_pool)) {
			ret = PTR_ERR(eth->rx_page_pool);
			return dev_err_probe(dev, ret,
					     "failed to create RX page pool\n");
		}
		params.napi = &eth->lan_napi;
		eth->lan_rx_page_pool = page_pool_create(&params);
		if (IS_ERR(eth->lan_rx_page_pool)) {
			ret = PTR_ERR(eth->lan_rx_page_pool);
			page_pool_destroy(eth->rx_page_pool);
			return dev_err_probe(dev, ret, "failed to create LAN RX pool\n");
		}
		ret = devm_add_action_or_reset(dev,
					       zx279133_rx_page_pool_destroy, eth);
		if (ret)
			return ret;
	}
	for (i = 0; i < ZX279133_CPU_RX_QUEUES; i++) {
		struct napi_struct *napi = i ? &eth->lan_napi : &eth->napi;
		struct page_pool *pool = i ? eth->lan_rx_page_pool : eth->rx_page_pool;

		ret = xdp_rxq_info_reg(&eth->xdp_rxq[i], ndev, i, napi->napi_id);
		if (!ret)
			ret = xdp_rxq_info_reg_mem_model(&eth->xdp_rxq[i],
							 MEM_TYPE_PAGE_POOL, pool);
		if (ret) {
			zx279133_xdp_rxq_unreg(eth);
			return dev_err_probe(dev, ret, "failed to register XDP RX queue\n");
		}
	}
	ret = xdp_rxq_info_reg(&eth->xsk_rxq, ndev, 0, eth->napi.napi_id);
	if (ret) {
		zx279133_xdp_rxq_unreg(eth);
		return dev_err_probe(dev, ret,
				     "failed to register AF_XDP RX queue\n");
	}
	ret = xdp_rxq_info_reg_mem_model(&eth->xsk_rxq,
					 MEM_TYPE_XSK_BUFF_POOL, NULL);
	if (ret) {
		zx279133_xdp_rxq_unreg(eth);
		return dev_err_probe(dev, ret,
				     "failed to register AF_XDP buffer pool\n");
	}
	ret = devm_add_action_or_reset(dev, zx279133_xdp_rxq_unreg, eth);
	if (ret)
		return ret;

	/*
	 * Source 0 and source 2 (vendor "localtest") each serve half of WAN/LAN
	 * through separate NAPI instances. The NPPT aggregate source has no
	 * recovered acknowledge contract, source 1 belongs to the unused
	 * companion/Wi-Fi path, and source 3 belongs to the vendor buffer
	 * release callback, so those three DT IRQs remain masked/unrequested.
	 */
	ret = devm_request_irq(dev, eth->irqs[1], zx279133_idm_rx_irq,
			       IRQF_NO_AUTOEN, dev_name(dev), eth);
	if (ret)
		return dev_err_probe(dev, ret, "failed to request RX IRQ\n");

	ret = devm_request_irq(dev, eth->irqs[4], zx279133_idm_local_irq,
			       IRQF_NO_AUTOEN, "zx279133-idm-local", eth);
	if (ret)
		return dev_err_probe(dev, ret,
				     "failed to request IDM local-test IRQ\n");

	/* Start the independent RX workers on separate online CPUs.
	 * These are initial affinities; userspace may override them later.
	 */
	irq_set_affinity_and_hint(eth->irqs[1],
				  cpumask_of(cpumask_local_spread(0, dev_to_node(dev))));
	irq_set_affinity_and_hint(eth->irqs[4],
				  cpumask_of(cpumask_local_spread(1, dev_to_node(dev))));
	ret = devm_add_action_or_reset(dev, zx279133_rx_affinity_clear, eth);
	if (ret)
		return ret;

	eth->rx_rss_map[0] = 0xf0;
	eth->rx_rss_map[1] = 0xf0;
	/* Runs after the netdev is unregistered, when nothing queues it. */
	ret = devm_add_action_or_reset(dev, zx279133_rx_mode_work_cancel, eth);
	if (ret)
		return ret;
	ret = devm_register_netdev(dev, ndev);
	if (ret)
		return dev_err_probe(dev, ret, "failed to register netdev\n");

	eth->lan_service.ops = &zx279133_lan_service_ops;
	platform_set_drvdata(pdev, &eth->lan_service);

	ret = devm_of_platform_populate(dev);
	if (ret)
		return dev_err_probe(dev, ret,
				     "failed to populate LAN child devices\n");

	dev_info(dev, "WAN netdev and LAN child devices registered\n");

	/*
	 * The LAN child devices - the switch - have just been probed, and
	 * they reset the chip's MACs on the way, which takes the traffic
	 * manager's configuration with it.  So the vendor's configuration is
	 * written again, here, after every driver has had its say.
	 */
	zx279133_nppt_vendor_reconfigure(eth);

	/* The register image, printed once, after every driver has had its say. */
	zx279133_diag_dump(eth);

	return 0;
}

static void zx279133_eth_shutdown(struct platform_device *pdev)
{
	struct zx279133_lan_service *service = platform_get_drvdata(pdev);

	zx279133_shutdown(container_of(service, struct zx279133_eth,
				       lan_service));
}

static const struct of_device_id zx279133_eth_of_match[] = {
	{ .compatible = "zte,zx279133-nppt" },
	{ }
};
MODULE_DEVICE_TABLE(of, zx279133_eth_of_match);

static struct platform_driver zx279133_eth_driver = {
	.probe = zx279133_eth_probe,
	.shutdown = zx279133_eth_shutdown,
	.driver = {
		.name = "zx279133-eth",
		.of_match_table = zx279133_eth_of_match,
	},
};
module_platform_driver(zx279133_eth_driver);

MODULE_FIRMWARE("zte/zx279133/mcode_intel.bin");
MODULE_DESCRIPTION("ZTE ZX279133 NPPT Ethernet driver");
MODULE_LICENSE("GPL");
