/* SPDX-License-Identifier: GPL-2.0-only */
#ifndef _ZX279133_IPSEC_KAT_CORE_H
#define _ZX279133_IPSEC_KAT_CORE_H

#include <linux/device.h>
#include <linux/dma-mapping.h>
#include <linux/io.h>
#include <linux/iopoll.h>
#include <linux/sizes.h>
#include <linux/string.h>
#include <linux/unaligned.h>

#define ZX_SEC_CTRL		0x450
#define ZX_SEC_CTRL_INIT	0x05000000
#define ZX_BTLL_TOP_CTRL	0x04
#define ZX_BTLL_TOP_ENABLE	0x08010000
#define ZX_PD			0x000000
#define ZX_KHB			0x000040
#define ZX_KEY			0x080000
#define ZX_DYN			0x120000
#define ZX_SRC			0x1f0000
#define ZX_DST			0x1f0040
#define ZX_BLOCK_SIZE		16
#define ZX_DESC_SIZE		64
#define ZX_AES_ECB		0x13
#define ZX_QC_FIFO_IRQ_ENABLE	0x08
#define ZX_QC_RAM_IRQ_ENABLE	0x0c
#define ZX_QC_PD_IRQ_ENABLE	0x10

struct zx_queue {
	u32 input_count, count, request_credit, submit, complete, completion_credit;
};

struct zx_desc_addr {
	u32 khb, key, dynamic, src, dst;
};

static const struct zx_queue zx_enc = { 0xc0, 0xe0, 0x80, 0x80000, 0x80020, 0xa0 };
static const struct zx_queue zx_dec = { 0xd0, 0xf0, 0x90, 0x80010, 0x80030, 0xb0 };
static const u16 zx_qc_windows[] = { 0x1c0, 0x1e0, 0x1f8, 0x210 };

static const u8 zx_key[ZX_BLOCK_SIZE] = {
	0x00, 0x01, 0x02, 0x03, 0x04, 0x05, 0x06, 0x07,
	0x08, 0x09, 0x0a, 0x0b, 0x0c, 0x0d, 0x0e, 0x0f,
};

/* ECB decrypt seeds the inverse key schedule with the last AES round key. */
static const u8 zx_decrypt_key[ZX_BLOCK_SIZE] = {
	0x13, 0x11, 0x1d, 0x7f, 0xe3, 0x94, 0x4a, 0x17,
	0xf3, 0x07, 0xa7, 0x8b, 0x4d, 0x2b, 0x30, 0xc5,
};

static const u8 zx_clear[ZX_BLOCK_SIZE] = {
	0x00, 0x11, 0x22, 0x33, 0x44, 0x55, 0x66, 0x77,
	0x88, 0x99, 0xaa, 0xbb, 0xcc, 0xdd, 0xee, 0xff,
};

static const u8 zx_cipher[ZX_BLOCK_SIZE] = {
	0x69, 0xc4, 0xe0, 0xd8, 0x6a, 0x7b, 0x04, 0x30,
	0xd8, 0xcd, 0xb7, 0x80, 0x70, 0xb4, 0xc5, 0x5a,
};

static bool zx_queues_busy(void __iomem *qc)
{
	/* Input and output counts for both directions and both factory queues. */
	static const u16 counters[] = { 0xc0, 0xc4, 0xd0, 0xd4,
					0xe0, 0xe4, 0xf0, 0xf4 };
	unsigned int i;

	for (i = 0; i < ARRAY_SIZE(counters); i++)
		if (readl(qc + counters[i]))
			return true;
	return false;
}

static void zx_pack(u8 pd[ZX_DESC_SIZE], u8 khb[ZX_DESC_SIZE], bool decrypt,
		    u8 opcode, u8 keylen, u16 len, const struct zx_desc_addr *addr)
{
	unsigned int off;

	memset(pd, 0, ZX_DESC_SIZE);
	memset(khb, 0, ZX_DESC_SIZE);
	/* PD points at the slot's KHB, which holds the bounce destination. */
	put_unaligned_be32(addr->khb, pd + 0x24);
	put_unaligned_be16(decrypt, pd + 0x38);
	khb[0] = 0x40 | decrypt;
	khb[1] = opcode;
	khb[4] = 0x01;
	khb[5] = keylen;
	put_unaligned_be32(addr->src, khb + 0x08);
	put_unaligned_be16(0xffff, khb + 0x0c);
	put_unaligned_be16(len, khb + 0x10);
	put_unaligned_be16(len, khb + 0x16);
	for (off = 0x18; off <= 0x2c; off += 4)
		put_unaligned_be16(0xffff, khb + off);
	put_unaligned_be32(addr->key, khb + 0x30);
	put_unaligned_be32(addr->dynamic, khb + 0x34);
	put_unaligned_be32(addr->dst, khb + 0x38);
}

static int zx_init(struct device *dev, void __iomem *btll, void __iomem *sec,
		   void __iomem *qc, u32 dma_base)
{
	unsigned int i, off;
	u32 addr, aux, top;

	/* The auxiliary SEC register is in the BTLL aperture, not in QC. */
	writel(ZX_SEC_CTRL_INIT, sec + ZX_SEC_CTRL);
	aux = readl(sec + ZX_SEC_CTRL);
	writel(0x7e9b, btll + 0x18);
	writel(readl(btll + ZX_BTLL_TOP_CTRL) | ZX_BTLL_TOP_ENABLE,
	       btll + ZX_BTLL_TOP_CTRL);
	top = readl(btll + ZX_BTLL_TOP_CTRL);
	if ((top & ZX_BTLL_TOP_ENABLE) != ZX_BTLL_TOP_ENABLE) {
		dev_err(dev, "BTLL top control write did not latch: %#x (SEC[450]=%#x)\n",
			top, aux);
		return -EIO;
	}
	dev_info(dev, "SEC[450]=%#x BTLL[4]=%#x\n", aux, top);
	/* Use the factory queue setup without enabling QC interrupts. */
	writel(0, qc + ZX_QC_PD_IRQ_ENABLE);
	writel(0, qc + ZX_QC_FIFO_IRQ_ENABLE);
	writel(0, qc + ZX_QC_RAM_IRQ_ENABLE);
	writel(0x80, qc + 0x28);
	writel(0x80, qc + 0x2c);
	writel(0x2710, qc + 0x30);
	writel(0x2710, qc + 0x34);
	for (off = 0x1080; off <= 0x109c; off += 4)
		writel(0x44444444, btll + off);
	writel(0xf, btll + 0x1040);
	for (off = 0x3000; off <= 0x4000; off += 0x1000) {
		writel(readl(btll + off + 0x08) | 1, btll + off + 0x08);
		writel(0x3fffff, btll + off + 0x0c);
		writel(1, btll + off + 0x24);
	}
	writel(0x13, qc + 0x04);
	writel(2, qc + 0x00);
	for (off = 0x50; off <= 0x5c; off += 4)
		writel(0x3f8, qc + off);
	for (i = 0; i < ARRAY_SIZE(zx_qc_windows); i++) {
		addr = (dma_base >> 16) + 2 * i;
		writel(addr, qc + zx_qc_windows[i]);
		writel(addr, qc + zx_qc_windows[i] + 4);
		writel(0xfffe, qc + zx_qc_windows[i] + 8);
	}
	for (off = 0x228; off <= 0x234; off += 4)
		writel(0x40, qc + off);
	dev_info(dev, "KAT readback: BTLL[4]=%#x QC[0]=%#x QC[4]=%#x QC[1c0]=%#x\n",
		 top, readl(qc), readl(qc + 0x04), readl(qc + 0x1c0));
	return 0;
}

struct zx_kat_mem {
	void __iomem *io;
	void *coherent;
	u32 dma_base;
	int (*wait_irq)(void *data);
	void *irq_data;
	bool cancel_after_submit;
};

static int zx_kat(struct device *dev, void __iomem *qc, const struct zx_kat_mem *mem,
		  bool decrypt, bool *submitted)
{
	const u32 base = mem->dma_base;
	const struct zx_desc_addr addr = {
		.khb = base + ZX_KHB,
		.key = base + ZX_KEY,
		.dynamic = base + ZX_DYN,
		.src = base + ZX_SRC,
		.dst = base + ZX_DST,
	};
	const struct zx_queue *q = decrypt ? &zx_dec : &zx_enc;
	const char *dir = decrypt ? "decrypt" : "encrypt";
	const u8 *key = decrypt ? zx_decrypt_key : zx_key;
	u8 pd[ZX_DESC_SIZE], khb[ZX_DESC_SIZE], out[ZX_BLOCK_SIZE];
	u32 count, completed;
	int ret, irq_ret = 0;

	if (zx_queues_busy(qc)) {
		dev_err(dev, "%s: pending request or completion before submission\n", dir);
		return -EBUSY;
	}

	zx_pack(pd, khb, decrypt, ZX_AES_ECB, sizeof(zx_key), ZX_BLOCK_SIZE,
		&addr);
	if (mem->coherent) {
		void *ddr = mem->coherent;

		memset(ddr, 0, SZ_2M);
		memcpy(ddr + ZX_PD, pd, sizeof(pd));
		memcpy(ddr + ZX_KHB, khb, sizeof(khb));
		memcpy(ddr + ZX_KEY, key, ZX_BLOCK_SIZE);
		memcpy(ddr + ZX_SRC, decrypt ? zx_cipher : zx_clear, sizeof(out));
		dma_wmb();
	} else {
		void __iomem *ddr = mem->io;

		memset_io(ddr, 0, SZ_2M);
		memcpy_toio(ddr + ZX_PD, pd, sizeof(pd));
		memcpy_toio(ddr + ZX_KHB, khb, sizeof(khb));
		memcpy_toio(ddr + ZX_KEY, key, ZX_BLOCK_SIZE);
		memcpy_toio(ddr + ZX_SRC, decrypt ? zx_cipher : zx_clear, sizeof(out));
		wmb();
	}
	*submitted = true;
	writel(1, qc + q->request_credit);
	writel(base + ZX_PD, qc + q->submit);
	if (mem->cancel_after_submit)
		return -ECANCELED;

	if (mem->wait_irq) {
		irq_ret = mem->wait_irq(mem->irq_data);
		if (irq_ret)
			dev_err(dev, "%s: completion IRQ timeout (input=%#x output=%#x)\n",
				dir, readl(qc + q->input_count), readl(qc + q->count));
	}
	ret = readl_poll_timeout(qc + q->count, count, count, 10, 20000);
	if (ret) {
		dev_err(dev, "%s: completion timeout (input=%#x output=%#x)\n",
			dir, readl(qc + q->input_count), count);
		return ret;
	}
	if (count != 1) {
		dev_err(dev, "%s: unexpected completion count %#x\n", dir, count);
		return -EIO;
	}
	completed = readl(qc + q->complete);
	if (completed != base + ZX_PD) {
		dev_err(dev, "%s: unexpected completion address %#x\n", dir, completed);
		return -EIO;
	}

	if (mem->coherent) {
		dma_rmb();
		memcpy(out, mem->coherent + ZX_DST, sizeof(out));
	} else {
		rmb();
		memcpy_fromio(out, mem->io + ZX_DST, sizeof(out));
	}
	writel(1, qc + q->completion_credit);
	if (memcmp(out, decrypt ? zx_clear : zx_cipher, sizeof(out))) {
		dev_err(dev, "%s: AES KAT mismatch (%*phN)\n", dir,
			(int)sizeof(out), out);
		return -EBADMSG;
	}
	/* A matching completion was consumed, but polling cannot pass the IRQ test. */
	return irq_ret ? -ENOMSG : 0;
}

#endif
