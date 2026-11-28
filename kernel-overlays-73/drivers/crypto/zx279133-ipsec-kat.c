// SPDX-License-Identifier: GPL-2.0-only
#include <linux/atomic.h>
#include <linux/clk.h>
#include <linux/delay.h>
#include <linux/device.h>
#include <linux/init.h>
#include <linux/io.h>
#include <linux/iopoll.h>
#include <linux/moduleparam.h>
#include <linux/of.h>
#include <linux/of_reserved_mem.h>
#include <linux/platform_device.h>
#include <linux/reset.h>
#include <linux/sizes.h>
#include <linux/string.h>
#include <linux/unaligned.h>

#include "zx279133-ipsec-kat-core.h"
#include "zx279133-ipsec-ecb.h"

#define ZX_BTLL_BASE		0x12000000
#define ZX_SEC_OFFSET		0x400000
#define ZX_QC_BASE		0x500000
#define ZX_DDR_BASE		0x90000000
#define ZX_TOPCRM_BASE		0x10e10000
#define ZX_TOPCRM_SEC_MUX	0x08
#define ZX_TOPCRM_GATE2		0x38
#define ZX_TOPCRM_BUS_DIV	0x5c
#define ZX_TOPCRM_RESET		0x60
#define ZX_IPSEC_GATES		(BIT(17) | BIT(19) | BIT(28) | BIT(29))
#define ZX_IPSEC_RESET_BIT	BIT(13)
#define ZX_BTLL_ERR_STATUS	0x20
#define ZX_BTLL_EUC1_ERR_STATUS	0x3020
#define ZX_BTLL_EUC2_ERR_STATUS	0x4020

static bool enable;
module_param(enable, bool, 0400);
MODULE_PARM_DESC(enable, "Run the ZX279133 hardware AES KAT on a cold test boot");
static bool provider;
module_param(provider, bool, 0400);
MODULE_PARM_DESC(provider, "Register experimental hardware ECB after the IRQ KAT passes");

/* The diagnostic is one-shot even if a platform device is recreated. */
static atomic_t armed = ATOMIC_INIT(0);

static int zx_wait_test_irq(void *data)
{
	struct sk_test_irq *test = data;

	return wait_for_completion_timeout(&test->done, msecs_to_jiffies(100)) ? 0 : -ETIMEDOUT;
}

static int zx_provider_test(struct platform_device *pdev, void __iomem *btll,
			    void __iomem *sec, void __iomem *qc, bool *submitted)
{
	struct device *dev = &pdev->dev;
	struct zx_kat_mem mem = {};
	dma_addr_t dma;
	int ret, irq;

	irq = platform_get_irq(pdev, 0);
	if (irq < 0)
		return irq;
	ret = dma_set_mask_and_coherent(dev, DMA_BIT_MASK(32));
	if (ret)
		return ret;
	mem.coherent = dmam_alloc_coherent(dev, SZ_2M, &dma, GFP_KERNEL | GFP_DMA32);
	if (!mem.coherent)
		return -ENOMEM;
	if (!IS_ALIGNED(dma, SZ_512K) || upper_32_bits(dma + SZ_2M - 1))
		return -ERANGE;
	mem.dma_base = lower_32_bits(dma);
	ret = zx_init(dev, btll, sec, qc, mem.dma_base);
	if (ret)
		return ret;
	sk_ecb.test.qc = qc;
	init_completion(&sk_ecb.test.done);
	ret = devm_request_irq(dev, irq, sk_test_complete, 0, "zx279133-ipsec", &sk_ecb.test);
	if (ret)
		return ret;
	mem.wait_irq = zx_wait_test_irq;
	mem.irq_data = &sk_ecb.test;
	writel(0xf, qc + ZX_QC_PD_IRQ_ENABLE);
	ret = zx_kat(dev, qc, &mem, false, submitted);
	if (!ret) {
		synchronize_irq(irq);
		reinit_completion(&sk_ecb.test.done);
		writel(0xf, qc + ZX_QC_PD_IRQ_ENABLE);
		ret = zx_kat(dev, qc, &mem, true, submitted);
	}
	writel(0, qc + ZX_QC_PD_IRQ_ENABLE);
	readl(qc + ZX_QC_PD_IRQ_ENABLE);
	synchronize_irq(irq);
	if (ret || zx_queues_busy(qc))
		return ret ?: -EBUSY;
	sk_ecb.dev = dev;
	sk_ecb.qc = qc;
	sk_ecb.arena = mem.coherent;
	sk_ecb.dma = dma;
	sk_ecb.irq = irq;
	return sk_ecb_register();
}

static int zx_probe(struct platform_device *pdev)
{
	struct clk_bulk_data clocks[] = {
		{ .id = "wclk" }, { .id = "aclk" },
		{ .id = "sec_hclk" }, { .id = "sec_wclk" },
	};
	struct device *dev = &pdev->dev;
	struct reset_control *rst;
	struct reserved_mem *rmem;
	struct resource *regs;
	struct zx_kat_mem mem = { .dma_base = ZX_DDR_BASE };
	void __iomem *btll, *qc, *topcrm;
	u32 gates_before, reset_before, gates, asserted, reset;
	unsigned long wclk, aclk, sec_hclk, sec_wclk;
	bool submitted = false;
	int ret;

	if (!enable || !of_machine_is_compatible("skyworth,sk-d840n"))
		return -ENODEV;

	{
		struct device_node *mem __free(device_node) =
			of_parse_phandle(dev->of_node, "memory-region", 0);

		if (!mem || !of_device_is_available(mem) ||
		    !of_property_read_bool(mem, "no-map") ||
		    of_property_read_bool(mem, "reusable"))
			return dev_err_probe(dev, -EINVAL, "missing dedicated no-map DDR\n");
		rmem = of_reserved_mem_lookup(mem);
		if (!rmem || rmem->base != ZX_DDR_BASE || rmem->size != SZ_2M)
			return dev_err_probe(dev, -EINVAL, "unexpected IPsec DDR range\n");
	}

	regs = platform_get_resource(pdev, IORESOURCE_MEM, 0);
	if (!regs || regs->start != ZX_BTLL_BASE || resource_size(regs) != SZ_16M)
		return dev_err_probe(dev, -EINVAL, "unexpected IPsec MMIO range\n");
	btll = devm_ioremap_resource(dev, regs);
	if (IS_ERR(btll))
		return PTR_ERR(btll);
	qc = btll + ZX_QC_BASE;
	if (!provider) {
		mem.io = devm_ioremap_wc(dev, rmem->base, rmem->size);
		if (!mem.io)
			return -ENOMEM;
	}
	/* Diagnostic readback only; clock and reset owners keep write control. */
	topcrm = devm_ioremap(dev, ZX_TOPCRM_BASE, ZX_TOPCRM_RESET + sizeof(u32));
	if (!topcrm)
		return -ENOMEM;
	rst = devm_reset_control_get_exclusive(dev, "ipsec");
	if (IS_ERR(rst))
		return dev_err_probe(dev, PTR_ERR(rst), "cannot get IPsec reset\n");

	ret = clk_bulk_get(dev, ARRAY_SIZE(clocks), clocks);
	if (ret)
		return dev_err_probe(dev, ret, "cannot get IPsec clocks\n");
	gates_before = readl(topcrm + ZX_TOPCRM_GATE2);
	reset_before = readl(topcrm + ZX_TOPCRM_RESET);
	ret = clk_bulk_prepare_enable(ARRAY_SIZE(clocks), clocks);
	if (ret)
		goto put_clocks;
	if (atomic_xchg(&armed, 1)) {
		clk_bulk_disable_unprepare(ARRAY_SIZE(clocks), clocks);
		ret = -EBUSY;
		goto put_clocks;
	}

	gates = readl(topcrm + ZX_TOPCRM_GATE2);
	reset = readl(topcrm + ZX_TOPCRM_RESET);
	wclk = clk_get_rate(clocks[0].clk);
	aclk = clk_get_rate(clocks[1].clk);
	sec_hclk = clk_get_rate(clocks[2].clk);
	sec_wclk = clk_get_rate(clocks[3].clk);
	dev_info(dev, "KAT TOPCRM: gate2=%#x->%#x reset=%#x->%#x mux=%#x bus_div=%#x rates=%lu/%lu/%lu/%lu\n",
		 gates_before, gates, reset_before, reset, readl(topcrm + ZX_TOPCRM_SEC_MUX),
		 readl(topcrm + ZX_TOPCRM_BUS_DIV), wclk, aclk, sec_hclk, sec_wclk);
	if ((gates & ZX_IPSEC_GATES) != ZX_IPSEC_GATES || !wclk || !aclk ||
	    !sec_hclk || !sec_wclk) {
		dev_err(dev, "KAT not submitted: IPsec clock gate or rate unavailable\n");
		clk_bulk_disable_unprepare(ARRAY_SIZE(clocks), clocks);
		clk_bulk_put(ARRAY_SIZE(clocks), clocks);
		return 0;
	}

	if (zx_queues_busy(qc)) {
		dev_err(dev, "queues not empty before initialization\n");
		return 0;
	}
	/* Cold-boot initialization only; this is not a DMA recovery sequence. */
	ret = reset_control_assert(rst);
	if (ret)
		goto result;
	asserted = readl(topcrm + ZX_TOPCRM_RESET);
	usleep_range(1000, 1500);
	ret = reset_control_deassert(rst);
	if (ret)
		goto result;
	usleep_range(300, 400);
	reset = readl(topcrm + ZX_TOPCRM_RESET);
	dev_info(dev, "KAT IPsec reset: asserted=%#x deasserted=%#x\n", asserted, reset);
	if ((asserted & ZX_IPSEC_RESET_BIT) || !(reset & ZX_IPSEC_RESET_BIT)) {
		ret = -EIO;
		goto result;
	}
	dev_info(dev, "cold-boot KAT: BTLL[4]=%#x QC[0]=%#x\n",
		 readl(btll + ZX_BTLL_TOP_CTRL), readl(qc));
	if (provider) {
		ret = zx_provider_test(pdev, btll, btll + ZX_SEC_OFFSET, qc, &submitted);
		if (!ret) {
			dev_info(dev, "IRQ KAT passed; registered ecb-aes-zx279133\n");
			return 0;
		}
		goto result;
	}
	ret = zx_init(dev, btll, btll + ZX_SEC_OFFSET, qc, mem.dma_base);
	if (!ret)
		ret = zx_kat(dev, qc, &mem, false, &submitted);
	if (!ret)
		ret = zx_kat(dev, qc, &mem, true, &submitted);

result:
	/*
	 * No proven DMA stop: keep the clock enables and no-map DDR until
	 * a cold power cycle. Never let driver-core retry a failed submission.
	 */
	if (ret) {
		dev_err(dev, "BTLL error status: global=%#x euc1=%#x euc2=%#x\n",
			readl(btll + ZX_BTLL_ERR_STATUS),
			readl(btll + ZX_BTLL_EUC1_ERR_STATUS),
			readl(btll + ZX_BTLL_EUC2_ERR_STATUS));
		if (submitted)
			dev_err(dev, "KAT failed (%d) after submission; IPsec DDR quarantined until power cycle\n",
				ret);
		else
			dev_err(dev, "KAT stopped before descriptor submission (%d)\n", ret);
	} else {
		dev_info(dev, "AES-128 ECB encrypt/decrypt KAT passed; no provider registered\n");
	}
	return 0;

put_clocks:
	clk_bulk_put(ARRAY_SIZE(clocks), clocks);
	return dev_err_probe(dev, ret, "cannot arm IPsec KAT\n");
}

static const struct of_device_id zx_match[] = {
	{ .compatible = "zte,zx279133-ipsec" },
	{ }
};

static struct platform_driver zx_driver = {
	.probe = zx_probe,
	.driver = {
		.name = "zx279133-ipsec-kat",
		.of_match_table = zx_match,
		.suppress_bind_attrs = true,
	},
};
builtin_platform_driver(zx_driver);
