/* SPDX-License-Identifier: GPL-2.0-only */
#include <crypto/aes.h>
#include <crypto/internal/skcipher.h>
#include <linux/completion.h>
#include <linux/interrupt.h>
#include <linux/scatterlist.h>
#include <linux/workqueue.h>

#define SK_ECB_CHUNK 64

struct sk_ecb_key {
	u8 enc[AES_MAX_KEY_SIZE];
	u8 dec[AES_MAX_KEY_SIZE];
	u8 len;
};

struct sk_ecb_req {
	bool decrypt;
};

struct sk_test_irq {
	void __iomem *qc;
	struct completion done;
	u32 status, enc, dec;
};

static struct sk_ecb_device {
	struct device *dev;
	struct reset_control *rst;
	void __iomem *btll, *sec, *qc;
	struct sk_test_irq test;
	dma_addr_t dma;
	u8 *arena;
	int irq;
	spinlock_t lock;
	struct crypto_queue queue;
	struct workqueue_struct *wq;
	struct work_struct work;
	bool failed;
	bool registered;
	void (*pin)(void);
} sk_ecb;

static irqreturn_t sk_test_complete(int irq, void *data)
{
	struct sk_test_irq *test = data;

	test->status = readl(test->qc + 0x180);
	writel(0, test->qc + ZX_QC_PD_IRQ_ENABLE);
	readl(test->qc + ZX_QC_PD_IRQ_ENABLE);
	test->enc = readl(test->qc + zx_enc.count);
	test->dec = readl(test->qc + zx_dec.count);
	complete(&test->done);
	return IRQ_HANDLED;
}

static int sk_ecb_setkey(struct crypto_skcipher *tfm, const u8 *key, unsigned int len)
{
	struct sk_ecb_key *ctx = crypto_skcipher_ctx(tfm);
	struct crypto_aes_ctx aes;
	unsigned int i, extra, words;
	int ret;

	ret = aes_expandkey(&aes, key, len);
	if (ret)
		return ret;
	memzero_explicit(ctx, sizeof(*ctx));
	ctx->len = len;
	memcpy(ctx->enc, key, len);
	words = len / sizeof(u32);
	extra = words - 4;
	for (i = 0; i < 4; i++)
		put_unaligned_le32(aes.key_dec[i], ctx->dec + sizeof(u32) * i);
	for (i = 0; i < extra; i++)
		put_unaligned_le32(aes.key_enc[len + 24 - extra + i], ctx->dec + 16 + sizeof(u32) * i);
	memzero_explicit(&aes, sizeof(aes));
	return 0;
}

static int sk_ecb_chunk(const struct sk_ecb_key *key, bool decrypt, unsigned int len)
{
	struct sk_ecb_device *engine = &sk_ecb;
	const struct zx_queue *q = decrypt ? &zx_dec : &zx_enc;
	u32 base = lower_32_bits(engine->dma);
	const struct zx_desc_addr addr = {
		.khb = base + ZX_KHB, .key = base + ZX_KEY,
		.dynamic = base + ZX_DYN, .src = base + ZX_SRC, .dst = base + ZX_DST,
	};
	u32 count, completed;
	int ret = -EIO;

	if (zx_queues_busy(engine->qc))
		goto fail;
	zx_pack(engine->arena + ZX_PD, engine->arena + ZX_KHB, decrypt,
		ZX_AES_ECB, key->len, len, &addr);
	memcpy(engine->arena + ZX_KEY, decrypt ? key->dec : key->enc, key->len);
	dma_wmb();
	reinit_completion(&engine->test.done);
	writel(0xf, engine->qc + ZX_QC_PD_IRQ_ENABLE);
	writel(1, engine->qc + q->request_credit);
	writel(base + ZX_PD, engine->qc + q->submit);
	if (!wait_for_completion_timeout(&engine->test.done, msecs_to_jiffies(100))) {
		ret = -ETIMEDOUT;
		goto fail;
	}
	synchronize_irq(engine->irq);
	count = readl(engine->qc + q->count);
	if (count != 1)
		goto fail;
	completed = readl(engine->qc + q->complete);
	if (completed != base + ZX_PD)
		goto fail;
	dma_rmb();
	writel(1, engine->qc + q->completion_credit);
	if (zx_queues_busy(engine->qc))
		goto fail;
	return 0;

fail:
	writel(0, engine->qc + ZX_QC_PD_IRQ_ENABLE);
	readl(engine->qc + ZX_QC_PD_IRQ_ENABLE);
	synchronize_irq(engine->irq);
	WRITE_ONCE(engine->failed, true);
	if (engine->pin)
		engine->pin();
	dev_err(engine->dev, "ECB engine disabled (%d); DMA arena pinned\n", ret);
	return ret;
}

static int sk_ecb_run(struct skcipher_request *req)
{
	struct sk_ecb_device *engine = &sk_ecb;
	const struct sk_ecb_req *rctx = skcipher_request_ctx(req);
	const struct sk_ecb_key *ctx = crypto_skcipher_ctx(crypto_skcipher_reqtfm(req));
	unsigned int off, len;
	int src, dst, ret = 0;

	if (READ_ONCE(engine->failed))
		return -EIO;
	src = sg_nents_for_len(req->src, req->cryptlen);
	dst = sg_nents_for_len(req->dst, req->cryptlen);
	if (src < 0 || dst < 0)
		return -EINVAL;
	for (off = 0; off < req->cryptlen; off += len) {
		len = min_t(unsigned int, SK_ECB_CHUNK, req->cryptlen - off);
		if (sg_pcopy_to_buffer(req->src, src, engine->arena + ZX_SRC, len, off) != len) {
			ret = -EINVAL;
			break;
		}
		ret = sk_ecb_chunk(ctx, rctx->decrypt, len);
		if (ret)
			return ret;
		if (sg_pcopy_from_buffer(req->dst, dst, engine->arena + ZX_DST, len, off) != len) {
			ret = -EINVAL;
			break;
		}
	}
	memzero_explicit(engine->arena + ZX_KEY, AES_MAX_KEY_SIZE);
	memzero_explicit(engine->arena + ZX_SRC, SK_ECB_CHUNK);
	memzero_explicit(engine->arena + ZX_DST, SK_ECB_CHUNK);
	return ret;
}

static void sk_ecb_worker(struct work_struct *work)
{
	struct sk_ecb_device *engine = container_of(work, struct sk_ecb_device, work);
	struct crypto_async_request *async, *backlog;
	int ret;

	for (;;) {
		spin_lock_bh(&engine->lock);
		backlog = crypto_get_backlog(&engine->queue);
		async = crypto_dequeue_request(&engine->queue);
		spin_unlock_bh(&engine->lock);
		if (!async)
			return;
		if (backlog) {
			local_bh_disable();
			crypto_request_complete(backlog, -EINPROGRESS);
			local_bh_enable();
		}
		ret = sk_ecb_run(container_of(async, struct skcipher_request, base));
		local_bh_disable();
		crypto_request_complete(async, ret);
		local_bh_enable();
	}
}

static int sk_ecb_enqueue(struct skcipher_request *req, bool decrypt)
{
	struct sk_ecb_req *rctx = skcipher_request_ctx(req);
	int ret;

	if (!req->cryptlen)
		return 0;
	if (!IS_ALIGNED(req->cryptlen, AES_BLOCK_SIZE))
		return -EINVAL;
	if (READ_ONCE(sk_ecb.failed))
		return -EIO;
	rctx->decrypt = decrypt;
	spin_lock_bh(&sk_ecb.lock);
	ret = crypto_enqueue_request(&sk_ecb.queue, &req->base);
	queue_work(sk_ecb.wq, &sk_ecb.work);
	spin_unlock_bh(&sk_ecb.lock);
	return ret;
}

static int sk_ecb_encrypt(struct skcipher_request *req)
{
	return sk_ecb_enqueue(req, false);
}

static int sk_ecb_decrypt(struct skcipher_request *req)
{
	return sk_ecb_enqueue(req, true);
}

static int sk_ecb_tfm_init(struct crypto_skcipher *tfm)
{
	crypto_skcipher_set_reqsize(tfm, sizeof(struct sk_ecb_req));
	return 0;
}

static void sk_ecb_tfm_exit(struct crypto_skcipher *tfm)
{
	memzero_explicit(crypto_skcipher_ctx(tfm), sizeof(struct sk_ecb_key));
}

static struct skcipher_alg sk_ecb_alg = {
	.base = {
		.cra_name = "ecb(aes)",
		.cra_driver_name = "ecb-aes-zx279133",
		.cra_priority = 300,
		.cra_flags = CRYPTO_ALG_ASYNC,
		.cra_blocksize = AES_BLOCK_SIZE,
		.cra_ctxsize = sizeof(struct sk_ecb_key),
		.cra_module = THIS_MODULE,
	},
	.min_keysize = AES_MIN_KEY_SIZE,
	.max_keysize = AES_MAX_KEY_SIZE,
	.setkey = sk_ecb_setkey,
	.encrypt = sk_ecb_encrypt,
	.decrypt = sk_ecb_decrypt,
	.init = sk_ecb_tfm_init,
	.exit = sk_ecb_tfm_exit,
};

static int sk_ecb_register(void)
{
	int ret;

	spin_lock_init(&sk_ecb.lock);
	crypto_init_queue(&sk_ecb.queue, 32);
	INIT_WORK(&sk_ecb.work, sk_ecb_worker);
	sk_ecb.wq = alloc_ordered_workqueue("sk-ipsec-ecb", WQ_MEM_RECLAIM);
	if (!sk_ecb.wq)
		return -ENOMEM;
	ret = crypto_register_skcipher(&sk_ecb_alg);
	if (ret) {
		destroy_workqueue(sk_ecb.wq);
		return ret;
	}
	sk_ecb.registered = true;
	return 0;
}
