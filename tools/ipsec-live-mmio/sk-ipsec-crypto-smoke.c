// SPDX-License-Identifier: GPL-2.0-only
#include <crypto/aes.h>
#include <crypto/skcipher.h>
#include <linux/module.h>
#include <linux/scatterlist.h>
#include <linux/slab.h>

#define TEST_LEN 96

static int sk_smoke_case(struct crypto_skcipher *tfm, struct skcipher_request *req,
			 unsigned int keylen)
{
	DECLARE_CRYPTO_WAIT(wait);
	struct aes_key sw;
	struct scatterlist src[3], dst[3];
	u8 key[AES_MAX_KEY_SIZE], expected[TEST_LEN];
	u8 *plain, *out;
	unsigned int i;
	int ret;

	plain = kmalloc(2 * TEST_LEN, GFP_KERNEL);
	if (!plain)
		return -ENOMEM;
	out = plain + TEST_LEN;
	for (i = 0; i < keylen; i++)
		key[i] = i;
	for (i = 0; i < TEST_LEN; i++)
		plain[i] = 0x5a ^ (i * 17);
	ret = aes_preparekey(&sw, key, keylen);
	if (ret)
		goto done;
	for (i = 0; i < TEST_LEN; i += AES_BLOCK_SIZE)
		aes_encrypt(&sw, expected + i, plain + i);
	memzero_explicit(&sw, sizeof(sw));
	ret = crypto_skcipher_setkey(tfm, key, keylen);
	if (ret)
		goto done;
	sg_init_table(src, ARRAY_SIZE(src));
	sg_init_table(dst, ARRAY_SIZE(dst));
	sg_set_buf(&src[0], plain, 7);
	sg_set_buf(&src[1], plain + 7, 50);
	sg_set_buf(&src[2], plain + 57, TEST_LEN - 57);
	sg_set_buf(&dst[0], out, 23);
	sg_set_buf(&dst[1], out + 23, 44);
	sg_set_buf(&dst[2], out + 67, TEST_LEN - 67);
	skcipher_request_set_callback(req, CRYPTO_TFM_REQ_MAY_SLEEP | CRYPTO_TFM_REQ_MAY_BACKLOG,
				      crypto_req_done, &wait);
	skcipher_request_set_crypt(req, src, dst, TEST_LEN, NULL);
	ret = crypto_wait_req(crypto_skcipher_encrypt(req), &wait);
	if (ret || memcmp(expected, out, TEST_LEN)) {
		ret = ret ?: -EBADMSG;
		goto done;
	}
	skcipher_request_set_crypt(req, dst, dst, TEST_LEN, NULL);
	ret = crypto_wait_req(crypto_skcipher_decrypt(req), &wait);
	if (ret || memcmp(plain, out, TEST_LEN)) {
		ret = ret ?: -EBADMSG;
		goto done;
	}
	pr_info("sk-ipsec-crypto-smoke: AES-%u fragmented encrypt and in-place decrypt passed\n", keylen * 8);
done:
	kfree(plain);
	return ret;
}

static int __init sk_smoke_init(void)
{
	struct crypto_skcipher *tfm;
	struct skcipher_request *req;
	unsigned int len;
	int ret = 0;

	tfm = crypto_alloc_skcipher("ecb-aes-zx279133", 0, 0);
	if (IS_ERR(tfm))
		return PTR_ERR(tfm);
	req = skcipher_request_alloc(tfm, GFP_KERNEL);
	if (!req) {
		ret = -ENOMEM;
		goto out;
	}
	for (len = AES_KEYSIZE_128; !ret && len <= AES_KEYSIZE_256; len += 8)
		ret = sk_smoke_case(tfm, req, len);
	skcipher_request_free(req);
out:
	crypto_free_skcipher(tfm);
	return ret;
}

static void __exit sk_smoke_exit(void)
{
}

module_init(sk_smoke_init);
module_exit(sk_smoke_exit);
MODULE_LICENSE("GPL");
MODULE_DESCRIPTION("SK-D840N live AES ECB Crypto API cross-check");
