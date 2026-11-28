// SPDX-License-Identifier: GPL-2.0-only
#include <linux/bitops.h>
#include <linux/completion.h>
#include <linux/crc32.h>
#include <linux/delay.h>
#include <linux/interrupt.h>
#include <linux/io.h>
#include <linux/irqdomain.h>
#include <linux/module.h>
#include <linux/of.h>
#include <linux/platform_device.h>
#include <linux/property.h>
#include <linux/reset.h>
#include <linux/sizes.h>

#include "zx279133-ipsec-kat-core.h"

#define ZX_TOPCRM_BASE		0x10e10000
#define ZX_TOPCRM_SEC_MUX	0x08
#define ZX_TOPCRM_GATE2		0x38
#define ZX_TOPCRM_BUS_DIV	0x5c
#define ZX_TOPCRM_RESET		0x60
#define ZX_IPSEC_GATES		(BIT(17) | BIT(19) | BIT(28) | BIT(29))
#define ZX_IPSEC_RESET_ID	13
#define ZX_BTLL_BASE		0x12000000
#define ZX_SEC_BASE		0x12400000
#define ZX_QC_BASE		0x12500000

static bool init_check;
module_param(init_check, bool, 0400);
MODULE_PARM_DESC(init_check, "Exercise and reset only SEC/BTLL setup; never submit DMA");
static bool queue_check;
module_param(queue_check, bool, 0400);
MODULE_PARM_DESC(queue_check, "Exercise QC setup with an isolated arena; no DMA by default");
static bool aes_check;
module_param(aes_check, bool, 0400);
MODULE_PARM_DESC(aes_check, "Submit one AES ECB encrypt and decrypt KAT after queue checks");
static bool irq_check;
module_param(irq_check, bool, 0400);
MODULE_PARM_DESC(irq_check, "Require SPI 174 hardware completion for both AES requests");
static bool reset_check;
module_param(reset_check, bool, 0400);
MODULE_PARM_DESC(reset_check, "Cancel after doorbell, reset, then retain the first DMA arena");
static bool provider_check;
module_param(provider_check, bool, 0400);
MODULE_PARM_DESC(provider_check, "Register the experimental ECB provider after hardware and IRQ tests");
static uint recover_r5_dma;
module_param(recover_r5_dma, uint, 0400);
MODULE_PARM_DESC(recover_r5_dma, "Reset the drained R5 test engine only; never release its pinned arena");

struct sk_test_reset_ref {
	struct fwnode_handle consumer;
	struct fwnode_handle *provider;
};

static int sk_reset_ref(const struct fwnode_handle *node, const char *prop,
			const char *nargs_prop, unsigned int nargs, unsigned int index,
			struct fwnode_reference_args *args)
{
	const struct sk_test_reset_ref *ref =
		container_of(node, struct sk_test_reset_ref, consumer);

	if (strcmp(prop, "resets") || index)
		return -ENOENT;
	args->fwnode = fwnode_handle_get(ref->provider);
	args->nargs = 1;
	args->args[0] = ZX_IPSEC_RESET_ID;
	return 0;
}

static const struct fwnode_operations sk_reset_consumer_ops = {
	.get_reference_args = sk_reset_ref,
};

static int sk_reset_pulse(struct reset_control *rst)
{
	int asserted, released, ret;

	ret = reset_control_assert(rst);
	if (ret)
		return ret;
	asserted = reset_control_status(rst);
	usleep_range(1000, 1500);
	ret = reset_control_deassert(rst);
	if (ret)
		return ret;
	usleep_range(300, 400);
	released = reset_control_status(rst);
	pr_info("sk-ipsec-live: reset status asserted=%d released=%d\n", asserted, released);
	return asserted == 1 && !released ? 0 : -EIO;
}

static bool sk_qc_ready(void __iomem *qc, u32 base)
{
	unsigned int i;
	bool ready = readl(qc) == 2 && (readl(qc + 4) & 0x1f) == 0x13;

	for (i = 0; i < ARRAY_SIZE(zx_qc_windows); i++) {
		u32 start = readl(qc + zx_qc_windows[i]);
		u32 same = readl(qc + zx_qc_windows[i] + 4);
		u32 mask = readl(qc + zx_qc_windows[i] + 8);

		pr_info("sk-ipsec-live: QC window %u=%#x/%#x/%#x\n", i, start, same, mask);
		ready &= start == (base >> 16) + 2 * i && same == start && mask == 0xfffe;
	}
	return ready && !zx_queues_busy(qc);
}

#include "zx279133-ipsec-ecb.h"

static void sk_pin_module(void)
{
	__module_get(THIS_MODULE);
}

static int sk_wait_irq(void *data)
{
	struct sk_test_irq *test = data;

	if (!wait_for_completion_timeout(&test->done, msecs_to_jiffies(100)))
		return -ETIMEDOUT;
	pr_info("sk-ipsec-live: IRQ SPI 174 status=%#x output enc=%#x dec=%#x\n",
		test->status, test->enc, test->dec);
	return 0;
}

static int sk_test_irq_get(struct sk_test_irq *test)
{
	struct device_node *gic __free(device_node) =
		of_find_compatible_node(NULL, NULL, "arm,gic-v3");
	struct irq_fwspec spec = { .param_count = 3, .param = { 0, 174, IRQ_TYPE_LEVEL_HIGH } };
	int irq, ret;

	if (!gic)
		return -ENODEV;
	spec.fwnode = of_fwnode_handle(gic);
	irq = irq_create_fwspec_mapping(&spec);
	if (!irq)
		return -ENXIO;
	init_completion(&test->done);
	ret = request_irq(irq, sk_test_complete, 0, "sk-ipsec-kat", test);
	return ret ?: irq;
}

static int sk_check_submitted_reset(struct reset_control *rst, void __iomem *btll,
				    void __iomem *sec, void __iomem *qc,
				    const struct zx_kat_mem *mem)
{
	u32 crc;
	int ret;

	pr_info("sk-ipsec-live: injected cancellation before completion drain: in=%#x out=%#x\n",
		readl(qc + zx_enc.input_count), readl(qc + zx_enc.count));
	ret = sk_reset_pulse(rst);
	if (ret)
		return ret;
	if (readl(btll + 4) || readl(sec + ZX_SEC_CTRL) || readl(qc) || zx_queues_busy(qc))
		return -EIO;
	dma_rmb();
	crc = crc32_le(0, mem->coherent, SZ_2M);
	msleep(50);
	dma_rmb();
	if (crc != crc32_le(0, mem->coherent, SZ_2M) || zx_queues_busy(qc))
		return -EIO;
	pr_info("sk-ipsec-live: submitted reset check passed; queues empty and arena stable for 50 ms; arena stays pinned\n");
	return 0;
}

static int sk_check_queues(struct reset_control *rst, void __iomem *btll,
			   void __iomem *sec, void __iomem *qc)
{
	struct zx_kat_mem mem = {};
	struct sk_test_irq local_test = { .qc = qc };
	struct sk_test_irq *test = provider_check ? &sk_ecb.test : &local_test;
	struct platform_device *pdev;
	dma_addr_t dma;
	bool submitted = false;
	int irq = 0, ret, reset_ret;

	pdev = platform_device_register_simple(reset_check ? "sk-ipsec-live-reset-dma" :
					      "sk-ipsec-live-v2-dma", PLATFORM_DEVID_NONE, NULL, 0);
	if (IS_ERR(pdev))
		return PTR_ERR(pdev);
	ret = dma_coerce_mask_and_coherent(&pdev->dev, DMA_BIT_MASK(32));
	if (ret)
		goto out_device;
	mem.coherent = dma_alloc_coherent(&pdev->dev, SZ_2M, &dma, GFP_KERNEL | GFP_DMA32);
	if (!mem.coherent) {
		ret = -ENOMEM;
		goto out_device;
	}
	if (!IS_ALIGNED(dma, SZ_512K) || upper_32_bits(dma + SZ_2M - 1)) {
		ret = -ERANGE;
		goto out_dma;
	}
	mem.dma_base = lower_32_bits(dma);
	pr_info("sk-ipsec-live: isolated DMA arena at %pad (2 MiB)\n", &dma);
	ret = zx_init(&pdev->dev, btll, sec, qc, mem.dma_base);
	if (!ret && !sk_qc_ready(qc, mem.dma_base))
		ret = -EIO;
	if (!ret && reset_check) {
		mem.cancel_after_submit = true;
		ret = zx_kat(&pdev->dev, qc, &mem, false, &submitted);
		if (ret == -ECANCELED) {
			ret = sk_check_submitted_reset(rst, btll, sec, qc, &mem);
			if (ret)
				goto quarantine;
			__module_get(THIS_MODULE);
			return 0;
		}
	}
	if (!ret && irq_check) {
		test->qc = qc;
		irq = sk_test_irq_get(test);
		if (irq < 0)
			ret = irq;
		else {
			mem.wait_irq = sk_wait_irq;
			mem.irq_data = test;
			writel(0xf, qc + ZX_QC_PD_IRQ_ENABLE);
		}
	}
	if (!ret && aes_check) {
		ret = zx_kat(&pdev->dev, qc, &mem, false, &submitted);
		if (!ret) {
			if (irq > 0) {
				synchronize_irq(irq);
				reinit_completion(&test->done);
				writel(0xf, qc + ZX_QC_PD_IRQ_ENABLE);
			}
			ret = zx_kat(&pdev->dev, qc, &mem, true, &submitted);
		}
		if (!ret && zx_queues_busy(qc))
			ret = -EBUSY;
	}
	if (!ret && provider_check) {
		sk_ecb.dev = &pdev->dev;
		sk_ecb.rst = rst;
		sk_ecb.btll = btll;
		sk_ecb.sec = sec;
		sk_ecb.qc = qc;
		sk_ecb.arena = mem.coherent;
		sk_ecb.dma = dma;
		sk_ecb.irq = irq;
		sk_ecb.pin = sk_pin_module;
		ret = sk_ecb_register();
		if (!ret) {
			pr_info("sk-ipsec-live: registered experimental ecb-aes-zx279133 provider\n");
			return 0;
		}
		/* Both KAT completions were consumed before registration. */
		submitted = false;
	}
	if (irq > 0) {
		writel(0, qc + ZX_QC_PD_IRQ_ENABLE);
		readl(qc + ZX_QC_PD_IRQ_ENABLE);
		free_irq(irq, test);
	}
	if (ret && submitted && ((ret != -EBADMSG && ret != -ENOMSG) || zx_queues_busy(qc)))
		goto quarantine;
	reset_ret = sk_reset_pulse(rst);
	if (!reset_ret && (readl(btll + ZX_BTLL_TOP_CTRL) || readl(qc) || zx_queues_busy(qc)))
		reset_ret = -EIO;
	if (reset_ret && submitted) {
		ret = reset_ret;
		goto quarantine;
	}
	if (!ret)
		ret = reset_ret;
	if (!ret)
		pr_info("sk-ipsec-live: %s passed; queues reset\n",
			aes_check ? "hardware AES-128 ECB encrypt/decrypt" : "pre-DMA QC readback");
out_dma:
	dma_free_coherent(&pdev->dev, SZ_2M, mem.coherent, dma);
out_device:
	platform_device_unregister(pdev);
	return ret;

quarantine:
	/* No proven DMA stop after submission failure: retain the live arena. */
	__module_get(THIS_MODULE);
	pr_err("sk-ipsec-live: DMA failure (%d); module and arena pinned, do not reboot or retry\n", ret);
	return 0;
}

static int sk_check_init(void __iomem *btll, void __iomem *sec, void __iomem *qc)
{
	struct device_node *provider __free(device_node) =
		of_find_node_by_path("/soc/reset-controller@10e10060");
	struct sk_test_reset_ref ref = { .consumer.ops = &sk_reset_consumer_ops };
	struct reset_control *rst;
	struct device *r5_dev;
	u32 aux, top;
	int ret;

	if (!provider || !of_device_is_compatible(provider, "zte,zx296718-reset"))
		return -ENODEV;
	if (zx_queues_busy(qc))
		return -EBUSY;
	if (recover_r5_dma) {
		r5_dev = bus_find_device_by_name(&platform_bus_type, NULL, "sk-ipsec-live-dma");
		if (!r5_dev)
			return -ENODEV;
		put_device(r5_dev);
		if (!IS_ALIGNED(recover_r5_dma, SZ_512K) ||
		    readl(btll + 4) != ZX_BTLL_TOP_ENABLE || readl(sec + ZX_SEC_CTRL) != 5 ||
		    !sk_qc_ready(qc, recover_r5_dma))
			return -EBUSY;
	} else if (readl(qc) || readl(btll + 4)) {
		return -EBUSY;
	}
	/* The running DT has the reset provider but no IPsec consumer phandle. */
	ref.provider = of_fwnode_handle(provider);
	rst = __fwnode_reset_control_get(&ref.consumer, NULL, 0, RESET_CONTROL_EXCLUSIVE);
	if (IS_ERR(rst))
		return PTR_ERR(rst);
	ret = reset_control_status(rst);
	if (ret) {
		ret = ret < 0 ? ret : -EBUSY;
		goto out;
	}
	ret = sk_reset_pulse(rst);
	if (ret)
		goto out;
	if (zx_queues_busy(qc) || readl(qc)) {
		ret = -EBUSY;
		goto out;
	}
	if (recover_r5_dma) {
		if (readl(btll + 4) || readl(sec + ZX_SEC_CTRL))
			ret = -EIO;
		else
			pr_info("sk-ipsec-live: drained R5 engine reset; old arena remains pinned; no DMA submitted\n");
		goto out;
	}
	if (queue_check) {
		ret = sk_check_queues(rst, btll, sec, qc);
		goto out;
	}

	writel(ZX_SEC_CTRL_INIT, sec + ZX_SEC_CTRL);
	aux = readl(sec + ZX_SEC_CTRL);
	writel(0x7e9b, btll + 0x18);
	writel(readl(btll + 4) | ZX_BTLL_TOP_ENABLE, btll + 4);
	top = readl(btll + 4);
	pr_info("sk-ipsec-live: init-only SEC[450]=%#x BTLL[4]=%#x; no QC writes or DMA\n",
		aux, top);
	ret = sk_reset_pulse(rst);
	if (!ret) {
		pr_info("sk-ipsec-live: after cleanup BTLL[4]=%#x SEC[450]=%#x QC[0]=%#x\n",
			readl(btll + 4), readl(sec + 0x450), readl(qc));
		if ((top & ZX_BTLL_TOP_ENABLE) != ZX_BTLL_TOP_ENABLE)
			ret = -EIO;
	}
out:
	if (!sk_ecb.registered)
		reset_control_put(rst);
	return ret;
}

static int __init sk_ipsec_live_init(void)
{
	struct device_node *engine;
	void __iomem *crm, *btll, *sec, *qc;
	u32 gates, reset;
	int ret = -ENOMEM;

	if ((aes_check && !queue_check) || (queue_check && !init_check) ||
	    (irq_check && !aes_check) ||
	    (provider_check && !irq_check) ||
	    (reset_check && (!queue_check || aes_check)) ||
	    (recover_r5_dma && (!init_check || queue_check || aes_check)))
		return -EINVAL;
	if (!of_machine_is_compatible("skyworth,sk-d840n"))
		return -ENODEV;
	for_each_compatible_node(engine, NULL, "zte,zx279133-ipsec") {
		if (!of_device_is_available(engine))
			continue;
		of_node_put(engine);
		return -EBUSY;
	}

	crm = ioremap(ZX_TOPCRM_BASE, SZ_4K);
	if (!crm)
		return -ENOMEM;
	gates = readl(crm + ZX_TOPCRM_GATE2);
	reset = readl(crm + ZX_TOPCRM_RESET);
	pr_info("sk-ipsec-live: mux=%#x gate2=%#x bus_div=%#x reset=%#x\n",
		readl(crm + ZX_TOPCRM_SEC_MUX), gates,
		readl(crm + ZX_TOPCRM_BUS_DIV), reset);
	iounmap(crm);
	if ((gates & ZX_IPSEC_GATES) != ZX_IPSEC_GATES || !(reset & BIT(ZX_IPSEC_RESET_ID))) {
		pr_info("sk-ipsec-live: engine MMIO skipped (clock/reset prerequisite)\n");
		return init_check ? -EAGAIN : 0;
	}

	btll = ioremap(ZX_BTLL_BASE, queue_check ? SZ_32K : SZ_4K);
	if (!btll)
		return -ENOMEM;
	sec = ioremap(ZX_SEC_BASE, SZ_4K);
	if (!sec)
		goto out_btll;
	qc = ioremap(ZX_QC_BASE, aes_check || reset_check ? SZ_1M : SZ_4K);
	if (!qc)
		goto out_sec;
	/* Only control and count reads; never read a completion FIFO. */
	pr_info("sk-ipsec-live: BTLL[4]=%#x BTLL[18]=%#x SEC[450]=%#x QC[0/4]=%#x/%#x\n",
		readl(btll + 4), readl(btll + 0x18), readl(sec + 0x450),
		readl(qc), readl(qc + 4));
	pr_info("sk-ipsec-live: QC in enc=%#x/%#x dec=%#x/%#x out enc=%#x/%#x dec=%#x/%#x\n",
		readl(qc + 0xc0), readl(qc + 0xc4), readl(qc + 0xd0), readl(qc + 0xd4),
		readl(qc + 0xe0), readl(qc + 0xe4), readl(qc + 0xf0), readl(qc + 0xf4));
	ret = init_check ? sk_check_init(btll, sec, qc) : 0;
	if (sk_ecb.registered)
		return ret;
	if (ret)
		pr_err("sk-ipsec-live: init-only check failed: %d\n", ret);
	iounmap(qc);
out_sec:
	iounmap(sec);
out_btll:
	iounmap(btll);
	return ret;
}

static void __exit sk_ipsec_live_exit(void)
{
	struct sk_ecb_device *engine = &sk_ecb;

	if (!engine->registered)
		return;
	crypto_unregister_skcipher(&sk_ecb_alg);
	destroy_workqueue(engine->wq);
	writel(0, engine->qc + ZX_QC_PD_IRQ_ENABLE);
	readl(engine->qc + ZX_QC_PD_IRQ_ENABLE);
	free_irq(engine->irq, &engine->test);
	if (!zx_queues_busy(engine->qc) && !sk_reset_pulse(engine->rst) &&
	    !readl(engine->qc) && !zx_queues_busy(engine->qc)) {
		memzero_explicit(engine->arena, SZ_2M);
		dma_free_coherent(engine->dev, SZ_2M, engine->arena, engine->dma);
		platform_device_unregister(to_platform_device(engine->dev));
	} else {
		pr_err("sk-ipsec-live: teardown could not verify a clean reset; retaining DMA arena\n");
	}
	reset_control_put(engine->rst);
	iounmap(engine->qc);
	iounmap(engine->sec);
	iounmap(engine->btll);
}

module_init(sk_ipsec_live_init);
module_exit(sk_ipsec_live_exit);
MODULE_LICENSE("GPL");
MODULE_DESCRIPTION("SK-D840N IPsec MMIO snapshot and opt-in hardware diagnostics");
