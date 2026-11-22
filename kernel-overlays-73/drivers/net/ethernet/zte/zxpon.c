#ifdef __KERNEL__
#include <linux/string.h>
#else
#include <string.h>
#endif

#include "zxpon.h"

/* AES-128, encrypt only. CMAC follows NIST SP 800-38B. The PLOAM MIC is
 * that CMAC truncated to 8 bytes over the first 40 bytes of the frame,
 * using the activation PLOAM integrity key (16 bytes of 0x55).
 */

static const uint8_t zxpon_sbox[256] = {
	0x63,0x7c,0x77,0x7b,0xf2,0x6b,0x6f,0xc5,0x30,0x01,0x67,0x2b,0xfe,0xd7,0xab,0x76,
	0xca,0x82,0xc9,0x7d,0xfa,0x59,0x47,0xf0,0xad,0xd4,0xa2,0xaf,0x9c,0xa4,0x72,0xc0,
	0xb7,0xfd,0x93,0x26,0x36,0x3f,0xf7,0xcc,0x34,0xa5,0xe5,0xf1,0x71,0xd8,0x31,0x15,
	0x04,0xc7,0x23,0xc3,0x18,0x96,0x05,0x9a,0x07,0x12,0x80,0xe2,0xeb,0x27,0xb2,0x75,
	0x09,0x83,0x2c,0x1a,0x1b,0x6e,0x5a,0xa0,0x52,0x3b,0xd6,0xb3,0x29,0xe3,0x2f,0x84,
	0x53,0xd1,0x00,0xed,0x20,0xfc,0xb1,0x5b,0x6a,0xcb,0xbe,0x39,0x4a,0x4c,0x58,0xcf,
	0xd0,0xef,0xaa,0xfb,0x43,0x4d,0x33,0x85,0x45,0xf9,0x02,0x7f,0x50,0x3c,0x9f,0xa8,
	0x51,0xa3,0x40,0x8f,0x92,0x9d,0x38,0xf5,0xbc,0xb6,0xda,0x21,0x10,0xff,0xf3,0xd2,
	0xcd,0x0c,0x13,0xec,0x5f,0x97,0x44,0x17,0xc4,0xa7,0x7e,0x3d,0x64,0x5d,0x19,0x73,
	0x60,0x81,0x4f,0xdc,0x22,0x2a,0x90,0x88,0x46,0xee,0xb8,0x14,0xde,0x5e,0x0b,0xdb,
	0xe0,0x32,0x3a,0x0a,0x49,0x06,0x24,0x5c,0xc2,0xd3,0xac,0x62,0x91,0x95,0xe4,0x79,
	0xe7,0xc8,0x37,0x6d,0x8d,0xd5,0x4e,0xa9,0x6c,0x56,0xf4,0xea,0x65,0x7a,0xae,0x08,
	0xba,0x78,0x25,0x2e,0x1c,0xa6,0xb4,0xc6,0xe8,0xdd,0x74,0x1f,0x4b,0xbd,0x8b,0x8a,
	0x70,0x3e,0xb5,0x66,0x48,0x03,0xf6,0x0e,0x61,0x35,0x57,0xb9,0x86,0xc1,0x1d,0x9e,
	0xe1,0xf8,0x98,0x11,0x69,0xd9,0x8e,0x94,0x9b,0x1e,0x87,0xe9,0xce,0x55,0x28,0xdf,
	0x8c,0xa1,0x89,0x0d,0xbf,0xe6,0x42,0x68,0x41,0x99,0x2d,0x0f,0xb0,0x54,0xbb,0x16
};

static const uint8_t zxpon_rcon[10] = {
	0x01,0x02,0x04,0x08,0x10,0x20,0x40,0x80,0x1b,0x36
};

static const uint8_t zxpon_default_ik[16] = {
	0x55,0x55,0x55,0x55,0x55,0x55,0x55,0x55,
	0x55,0x55,0x55,0x55,0x55,0x55,0x55,0x55
};

static uint8_t zxpon_xtime(uint8_t x)
{
	return (uint8_t)((x << 1) ^ ((x & 0x80) ? 0x1b : 0));
}

static void zxpon_aes_expand(const uint8_t key[16], uint8_t rk[176])
{
	int i;

	memcpy(rk, key, 16);
	for (i = 4; i < 44; i++) {
		uint8_t t[4];

		memcpy(t, rk + (i - 1) * 4, 4);
		if ((i % 4) == 0) {
			uint8_t tmp = t[0];

			t[0] = (uint8_t)(zxpon_sbox[t[1]] ^ zxpon_rcon[(i / 4) - 1]);
			t[1] = zxpon_sbox[t[2]];
			t[2] = zxpon_sbox[t[3]];
			t[3] = zxpon_sbox[tmp];
		}
		rk[i * 4 + 0] = (uint8_t)(rk[(i - 4) * 4 + 0] ^ t[0]);
		rk[i * 4 + 1] = (uint8_t)(rk[(i - 4) * 4 + 1] ^ t[1]);
		rk[i * 4 + 2] = (uint8_t)(rk[(i - 4) * 4 + 2] ^ t[2]);
		rk[i * 4 + 3] = (uint8_t)(rk[(i - 4) * 4 + 3] ^ t[3]);
	}
}

static void zxpon_aes_encrypt(const uint8_t rk[176], const uint8_t in[16],
			      uint8_t out[16])
{
	uint8_t s[16];
	uint8_t t;
	int round, i, c;

	for (i = 0; i < 16; i++)
		s[i] = (uint8_t)(in[i] ^ rk[i]);
	for (round = 1; round <= 10; round++) {
		for (i = 0; i < 16; i++)
			s[i] = zxpon_sbox[s[i]];
		t = s[1]; s[1] = s[5]; s[5] = s[9]; s[9] = s[13]; s[13] = t;
		t = s[2]; s[2] = s[10]; s[10] = t;
		t = s[6]; s[6] = s[14]; s[14] = t;
		t = s[15]; s[15] = s[11]; s[11] = s[7]; s[7] = s[3]; s[3] = t;
		if (round < 10) {
			for (c = 0; c < 4; c++) {
				uint8_t a0 = s[c * 4], a1 = s[c * 4 + 1];
				uint8_t a2 = s[c * 4 + 2], a3 = s[c * 4 + 3];

				s[c * 4] = (uint8_t)(zxpon_xtime(a0) ^ (zxpon_xtime(a1) ^ a1) ^ a2 ^ a3);
				s[c * 4 + 1] = (uint8_t)(a0 ^ zxpon_xtime(a1) ^ (zxpon_xtime(a2) ^ a2) ^ a3);
				s[c * 4 + 2] = (uint8_t)(a0 ^ a1 ^ zxpon_xtime(a2) ^ (zxpon_xtime(a3) ^ a3));
				s[c * 4 + 3] = (uint8_t)((zxpon_xtime(a0) ^ a0) ^ a1 ^ a2 ^ zxpon_xtime(a3));
			}
		}
		for (i = 0; i < 16; i++)
			s[i] ^= rk[round * 16 + i];
	}
	memcpy(out, s, 16);
}
static void zxpon_cmac_dbl(uint8_t block[16])
{
	int i;
	uint8_t carry = 0;

	for (i = 15; i >= 0; i--) {
		uint8_t next = (uint8_t)((block[i] >> 7) & 1);

		block[i] = (uint8_t)((block[i] << 1) | carry);
		carry = next;
	}
	if (carry)
		block[15] ^= 0x87;
}

void zxpon_cmac(const uint8_t key[16], const uint8_t *msg, uint32_t len,
		uint8_t mac[16])
{
	uint8_t rk[176], zero[16], l[16], k1[16], k2[16], x[16], y[16];
	uint32_t n, i, off;
	int complete;

	memset(zero, 0, 16);
	zxpon_aes_expand(key, rk);
	zxpon_aes_encrypt(rk, zero, l);
	memcpy(k1, l, 16);
	zxpon_cmac_dbl(k1);
	memcpy(k2, k1, 16);
	zxpon_cmac_dbl(k2);

	n = (len + 15) / 16;
	if (n == 0) {
		n = 1;
		complete = 0;
	} else {
		complete = (len % 16) == 0;
	}
	memset(x, 0, 16);
	off = 0;
	for (i = 1; i < n; i++) {
		int b;

		for (b = 0; b < 16; b++)
			y[b] = (uint8_t)(x[b] ^ msg[off + b]);
		zxpon_aes_encrypt(rk, y, x);
		off += 16;
	}
	memset(y, 0, 16);
	if (len > off)
		memcpy(y, msg + off, len - off);
	if (!complete) {
		y[len - off] = 0x80;
		for (i = 0; i < 16; i++)
			y[i] ^= k2[i];
	} else {
		for (i = 0; i < 16; i++)
			y[i] = (uint8_t)(y[i] ^ k1[i]);
	}
	for (i = 0; i < 16; i++)
		y[i] ^= x[i];
	zxpon_aes_encrypt(rk, y, mac);
}

uint32_t zxpon_crc32(const uint8_t *buf, uint32_t len)
{
	uint32_t crc = 0xffffffff;
	uint32_t i;

	for (i = 0; i < len; i++) {
		int b;

		crc ^= buf[i];
		for (b = 0; b < 8; b++) {
			if (crc & 1)
				crc = (crc >> 1) ^ 0xedb88320u;
			else
				crc >>= 1;
		}
	}
	return crc ^ 0xffffffffu;
}

static uint16_t zxpon_be16(const uint8_t *p)
{
	return (uint16_t)((p[0] << 8) | p[1]);
}

static void zxpon_put_be16(uint8_t *p, uint16_t v)
{
	p[0] = (uint8_t)(v >> 8);
	p[1] = (uint8_t)v;
}

static void zxpon_ploam_mic(const uint8_t frame[48], uint8_t mic[8])
{
	uint8_t mac[16];

	zxpon_cmac(zxpon_default_ik, frame, 40, mac);
	memcpy(mic, mac, 8);
}

void zxpon_ploam_seal(uint8_t frame[48])
{
	uint8_t mic[8];

	zxpon_ploam_mic(frame, mic);
	memcpy(frame + 40, mic, 8);
}

int zxpon_ploam_mic_ok(const uint8_t frame[48])
{
	uint8_t mic[8];

	zxpon_ploam_mic(frame, mic);
	return memcmp(mic, frame + 40, 8) == 0;
}

static int zxpon_queue_ploam(struct zxpon *z, uint8_t id, uint16_t onu,
			     const uint8_t *content, uint32_t clen)
{
	uint8_t *f;

	if (z->ploam_tx_n >= ZXPON_PLOAM_Q)
		return -1;
	f = z->ploam_tx[z->ploam_tx_n];
	memset(f, 0, 48);
	zxpon_put_be16(f, onu);
	f[2] = id;
	f[3] = 0;
	if (content && clen) {
		if (clen > 36)
			clen = 36;
		memcpy(f + 4, content, clen);
	}
	zxpon_ploam_seal(f);
	z->ploam_tx_n++;
	return 0;
}

static void zxpon_queue_sn(struct zxpon *z)
{
	zxpon_queue_ploam(z, ZXPON_US_SERIAL_NUMBER, ZXPON_BCAST_ONUID,
			  z->sn, ZXPON_SN_LEN);
}

static void zxpon_drop_sn(struct zxpon *z)
{
	int i, w;

	w = 0;
	for (i = 0; i < z->ploam_tx_n; i++) {
		if (z->ploam_tx[i][2] == ZXPON_US_SERIAL_NUMBER)
			continue;
		if (w != i)
			memcpy(z->ploam_tx[w], z->ploam_tx[i], 48);
		w++;
	}
	z->ploam_tx_n = w;
}

static void zxpon_enter_o1(struct zxpon *z, int drop_sn)
{
	z->state = ZXPON_O1;
	z->to1_ms = 0;
	z->to2_ms = 0;
	if (drop_sn)
		zxpon_drop_sn(z);
}

static void zxpon_try_o23(struct zxpon *z)
{
	if (z->los || z->lods || !z->has_identity)
		return;
	if (z->state != ZXPON_O1 && z->state != ZXPON_O7)
		return;
	z->state = ZXPON_O23;
	z->to1_ms = 0;
	z->to2_ms = 0;
	zxpon_queue_sn(z);
}

static int zxpon_me_add(struct zxpon *z, uint16_t class_id, uint16_t inst,
			int builtin)
{
	struct zxpon_me *m;

	if (z->me_n >= ZXPON_ME_MAX)
		return -1;
	m = &z->me[z->me_n++];
	memset(m, 0, sizeof(*m));
	m->class_id = class_id;
	m->inst = inst;
	m->used = 1;
	m->builtin = builtin ? 1 : 0;
	return z->me_n - 1;
}

static void zxpon_seed_mib(struct zxpon *z)
{
	static const uint16_t classes[] = {
		256, 257, 263, 45, 47, 47, 329, 277, 134
	};
	static const uint16_t inst[] = {
		0, 0, 0, 1, 1, 2, 1, 1, 0
	};
	unsigned int i;

	z->me_n = 0;
	for (i = 0; i < sizeof(classes) / sizeof(classes[0]); i++)
		zxpon_me_add(z, classes[i], inst[i], 1);
}

void zxpon_init(struct zxpon *z)
{
	memset(z, 0, sizeof(*z));
	z->state = ZXPON_O1;
	z->los = 1;
	z->lods = 1;
	z->onu_id = ZXPON_BCAST_ONUID;
	zxpon_seed_mib(z);
}

void zxpon_set_identity(struct zxpon *z, const uint8_t sn[8],
			const uint8_t regid[36])
{
	memcpy(z->sn, sn, 8);
	memcpy(z->regid, regid, 36);
	z->has_identity = 1;
	if (z->state == ZXPON_O1)
		zxpon_try_o23(z);
}

void zxpon_set_los(struct zxpon *z, int light_absent)
{
	z->los = light_absent ? 1 : 0;
	if (z->los)
		zxpon_enter_o1(z, 1);
	else if (z->state == ZXPON_O1)
		zxpon_try_o23(z);
}

void zxpon_set_lods(struct zxpon *z, int lost)
{
	z->lods = lost ? 1 : 0;
	if (!z->lods) {
		if (z->state == ZXPON_O6) {
			z->state = ZXPON_O5;
			z->to2_ms = 0;
		} else if (z->state == ZXPON_O1) {
			zxpon_try_o23(z);
		}
		return;
	}
	if (z->state == ZXPON_O5) {
		z->state = ZXPON_O6;
		z->to2_ms = 100;
		return;
	}
	if (z->state != ZXPON_O1 && z->state != ZXPON_O6)
		zxpon_enter_o1(z, 1);
}

void zxpon_tick(struct zxpon *z, uint32_t ms)
{
	if (z->state == ZXPON_O4 && z->to1_ms) {
		if (ms >= z->to1_ms)
			zxpon_enter_o1(z, 1);
		else
			z->to1_ms -= ms;
	}
	if (z->state == ZXPON_O6 && z->to2_ms) {
		if (ms >= z->to2_ms)
			zxpon_enter_o1(z, 1);
		else
			z->to2_ms -= ms;
	}
}

static int zxpon_for_us(const struct zxpon *z, uint16_t onu)
{
	return onu == z->onu_id || onu == ZXPON_BCAST_ONUID;
}

static void zxpon_make_key(struct zxpon *z)
{
	int i;

	for (i = 0; i < 16; i++)
		z->key[i] = (uint8_t)(z->sn[i & 7] ^ 0x5a);
	z->key_valid = 1;
}

int zxpon_rx_ploam(struct zxpon *z, const uint8_t frame[48])
{
	uint16_t onu;
	uint8_t id;
	const uint8_t *c;

	if (!zxpon_ploam_mic_ok(frame))
		return -1;
	if (z->los)
		return 0;
	onu = zxpon_be16(frame);
	id = frame[2];
	c = frame + 4;
	if (id != ZXPON_DS_ASSIGN_ONUID && id != ZXPON_DS_DISABLE_SN &&
	    !zxpon_for_us(z, onu))
		return 0;

	switch (id) {
	case ZXPON_DS_BURST_PROFILE:
		return 0;
	case ZXPON_DS_ASSIGN_ONUID:
		if (z->state != ZXPON_O23)
			return 0;
		z->onu_id = zxpon_be16(c);
		z->state = ZXPON_O4;
		z->to1_ms = 10000;
		return 0;
	case ZXPON_DS_RANGING_TIME:
		if (z->state != ZXPON_O4)
			return 0;
		z->state = ZXPON_O5;
		z->to1_ms = 0;
		zxpon_queue_ploam(z, ZXPON_US_ACK, z->onu_id, &id, 1);
		return 0;
	case ZXPON_DS_REQUEST_REG:
		if (z->state != ZXPON_O5)
			return 0;
		zxpon_queue_ploam(z, ZXPON_US_REGISTRATION, z->onu_id,
				  z->regid, ZXPON_REGID_LEN);
		return 0;
	case ZXPON_DS_ASSIGN_ALLOCID:
		if (z->state != ZXPON_O5)
			return 0;
		zxpon_add_tcont(z, zxpon_be16(c), c[2] & ZXPON_ALLOC_F_OMCC,
				zxpon_be16(c + 3));
		zxpon_queue_ploam(z, ZXPON_US_ACK, z->onu_id, &id, 1);
		return 0;
	case ZXPON_DS_KEY_CONTROL:
		if (z->state != ZXPON_O5)
			return 0;
		if (c[0] == 0) {
			zxpon_make_key(z);
			zxpon_queue_ploam(z, ZXPON_US_KEY_REPORT, z->onu_id,
					  z->key, 16);
		} else {
			z->key_valid = 1;
			zxpon_queue_ploam(z, ZXPON_US_ACK, z->onu_id, &id, 1);
		}
		return 0;
	case ZXPON_DS_DEACTIVATE:
		if (z->state == ZXPON_O1)
			return 0;
		z->onu_id = ZXPON_BCAST_ONUID;
		zxpon_enter_o1(z, 1);
		zxpon_try_o23(z);
		return 0;
	case ZXPON_DS_DISABLE_SN:
		if (c[0] == 0xff) {
			if (memcmp(c + 1, "\xff\xff\xff\xff\xff\xff\xff\xff", 8) != 0 &&
			    memcmp(c + 1, z->sn, 8) != 0)
				return 0;
			zxpon_enter_o1(z, 1);
			z->state = ZXPON_O7;
			return 0;
		}
		if (c[0] == 0x00 && z->state == ZXPON_O7) {
			zxpon_enter_o1(z, 1);
			zxpon_try_o23(z);
		}
		return 0;
	default:
		return 0;
	}
}

int zxpon_take_ploam(struct zxpon *z, uint8_t out[48])
{
	int i;

	if (z->ploam_tx_n <= 0)
		return 0;
	memcpy(out, z->ploam_tx[0], 48);
	for (i = 1; i < z->ploam_tx_n; i++)
		memcpy(z->ploam_tx[i - 1], z->ploam_tx[i], 48);
	z->ploam_tx_n--;
	return 1;
}

int zxpon_ploam_pending(const struct zxpon *z)
{
	return z->ploam_tx_n;
}

static int zxpon_queue_omci(struct zxpon *z, const uint8_t frame[48])
{
	if (z->omci_tx_n >= ZXPON_OMCI_Q)
		return -1;
	memcpy(z->omci_tx[z->omci_tx_n], frame, 48);
	z->omci_tx_n++;
	return 0;
}

static void zxpon_omci_finish(uint8_t frame[48])
{
	uint32_t crc = zxpon_crc32(frame, 44);

	frame[44] = (uint8_t)(crc >> 24);
	frame[45] = (uint8_t)(crc >> 16);
	frame[46] = (uint8_t)(crc >> 8);
	frame[47] = (uint8_t)crc;
}

static void zxpon_omci_reply(struct zxpon *z, const uint8_t req[48],
			     uint8_t mt, const uint8_t *content, uint32_t clen)
{
	uint8_t f[48];

	memset(f, 0, 48);
	f[0] = req[0];
	f[1] = req[1];
	f[2] = (uint8_t)((mt & 0x1f) | 0x20);
	f[3] = 0x0a;
	memcpy(f + 4, req + 4, 4);
	if (content && clen) {
		if (clen > 32)
			clen = 32;
		memcpy(f + 8, content, clen);
	}
	f[42] = 0x00;
	f[43] = 0x28;
	zxpon_omci_finish(f);
	zxpon_queue_omci(z, f);
}

static struct zxpon_me *zxpon_find_me(struct zxpon *z, uint16_t class_id,
				      uint16_t inst)
{
	int i;

	for (i = 0; i < z->me_n; i++) {
		if (z->me[i].used && z->me[i].class_id == class_id &&
		    z->me[i].inst == inst)
			return &z->me[i];
	}
	return NULL;
}

static void zxpon_maybe_map(struct zxpon *z, uint16_t gem)
{
	if (!gem || (z->omcc_valid && gem == z->omcc_gem))
		return;
	z->data_gem = gem;
	z->data_valid = 1;
}

static void zxpon_omci_get(struct zxpon *z, const uint8_t req[48])
{
	uint16_t class_id = zxpon_be16(req + 4);
	uint16_t mask = zxpon_be16(req + 8);
	uint8_t content[32];

	memset(content, 0, sizeof(content));
	content[0] = 0;
	zxpon_put_be16(content + 1, mask);
	if (class_id == 256 && (mask & 0x2000))
		memcpy(content + 3, z->sn, 8);
	zxpon_omci_reply(z, req, 9, content, 3 + ((class_id == 256 && (mask & 0x2000)) ? 8 : 0));
}

static void zxpon_omci_create(struct zxpon *z, const uint8_t req[48])
{
	uint16_t class_id = zxpon_be16(req + 4);
	uint16_t inst = zxpon_be16(req + 6);
	const uint8_t *c = req + 8;
	uint8_t result = 0;
	int idx;

	if (zxpon_find_me(z, class_id, inst)) {
		result = 1;
	} else {
		idx = zxpon_me_add(z, class_id, inst, 0);
		if (idx < 0) {
			result = 1;
		} else if (class_id == 268) {
			z->me[idx].gem_port = zxpon_be16(c);
		} else if (class_id == 266) {
			struct zxpon_me *ctp;
			uint16_t ctp_inst = zxpon_be16(c);

			z->me[idx].gem_ctp = ctp_inst;
			ctp = zxpon_find_me(z, 268, ctp_inst);
			if (ctp)
				zxpon_maybe_map(z, ctp->gem_port);
		} else if (class_id == 171) {
			z->me[idx].vlan_op = c[0];
			z->me[idx].vlan_tci = zxpon_be16(c + 1);
			z->vlan_op = c[0];
			z->vlan_tci = zxpon_be16(c + 1);
		}
	}
	zxpon_omci_reply(z, req, 4, &result, 1);
}

static void zxpon_omci_set(struct zxpon *z, const uint8_t req[48])
{
	uint16_t class_id = zxpon_be16(req + 4);
	uint16_t inst = zxpon_be16(req + 6);
	struct zxpon_me *m = zxpon_find_me(z, class_id, inst);
	uint8_t result = m ? 0 : 1;

	if (m && class_id == 171) {
		m->vlan_op = req[10];
		m->vlan_tci = zxpon_be16(req + 11);
		z->vlan_op = m->vlan_op;
		z->vlan_tci = m->vlan_tci;
	}
	zxpon_omci_reply(z, req, 8, &result, 1);
}

static void zxpon_omci_upload(struct zxpon *z, const uint8_t req[48])
{
	uint8_t content[2];
	int n = 0, i;

	for (i = 0; i < z->me_n; i++)
		if (z->me[i].used)
			n++;
	zxpon_put_be16(content, (uint16_t)n);
	zxpon_omci_reply(z, req, 13, content, 2);
}

static void zxpon_omci_upload_next(struct zxpon *z, const uint8_t req[48])
{
	uint16_t seq = zxpon_be16(req + 8);
	uint8_t content[32];
	int seen = 0, i;

	memset(content, 0, sizeof(content));
	for (i = 0; i < z->me_n; i++) {
		if (!z->me[i].used)
			continue;
		if (seen == seq) {
			zxpon_put_be16(content, z->me[i].class_id);
			zxpon_put_be16(content + 2, z->me[i].inst);
			if (z->me[i].class_id == 256) {
				zxpon_put_be16(content + 4, 0x2000);
				memcpy(content + 6, z->sn, 8);
			} else {
				zxpon_put_be16(content + 4, 0x8000);
			}
			zxpon_omci_reply(z, req, 14, content, 26);
			return;
		}
		seen++;
	}
	content[0] = 1;
	zxpon_omci_reply(z, req, 14, content, 1);
}

int zxpon_rx_omci(struct zxpon *z, const uint8_t frame[48])
{
	uint8_t mt = (uint8_t)(frame[2] & 0x1f);

	if (frame[3] != 0x0a)
		return -1;
	switch (mt) {
	case 4:
		zxpon_omci_create(z, frame);
		return 0;
	case 8:
		zxpon_omci_set(z, frame);
		return 0;
	case 9:
		zxpon_omci_get(z, frame);
		return 0;
	case 13:
		zxpon_omci_upload(z, frame);
		return 0;
	case 14:
		zxpon_omci_upload_next(z, frame);
		return 0;
	default:
		return -1;
	}
}

int zxpon_take_omci(struct zxpon *z, uint8_t out[48])
{
	int i;

	if (z->omci_tx_n <= 0)
		return 0;
	memcpy(out, z->omci_tx[0], 48);
	for (i = 1; i < z->omci_tx_n; i++)
		memcpy(z->omci_tx[i - 1], z->omci_tx[i], 48);
	z->omci_tx_n--;
	return 1;
}

enum zxpon_state zxpon_state(const struct zxpon *z)
{
	return z->state;
}

const char *zxpon_state_name(const struct zxpon *z)
{
	switch (z->state) {
	case ZXPON_O1: return "O1";
	case ZXPON_O23: return "O2-3";
	case ZXPON_O4: return "O4";
	case ZXPON_O5: return "O5";
	case ZXPON_O6: return "O6";
	case ZXPON_O7: return "O7";
	}
	return "O1";
}

int zxpon_data_gem(const struct zxpon *z, uint16_t *gem)
{
	if (!z->data_valid)
		return 0;
	*gem = z->data_gem;
	return 1;
}

int zxpon_omcc_gem(const struct zxpon *z, uint16_t *gem)
{
	if (!z->omcc_valid)
		return 0;
	*gem = z->omcc_gem;
	return 1;
}

int zxpon_map_gem(struct zxpon *z, uint16_t gem)
{
	if (!gem) {
		z->data_valid = 0;
		z->data_gem = 0;
		return 0;
	}
	if (z->omcc_valid && gem == z->omcc_gem)
		return -1;
	z->data_gem = gem;
	z->data_valid = 1;
	return 0;
}

int zxpon_add_tcont(struct zxpon *z, uint16_t alloc_id, int omcc, uint16_t gem)
{
	(void)alloc_id;
	if (omcc) {
		z->omcc_gem = gem;
		z->omcc_valid = 1;
		if (z->data_valid && z->data_gem == gem)
			z->data_valid = 0;
	}
	return 0;
}

int zxpon_set_key(struct zxpon *z, const uint8_t key[16])
{
	memcpy(z->key, key, 16);
	z->key_valid = 1;
	return 0;
}

int zxpon_vlan(const struct zxpon *z, const uint8_t *in, uint32_t in_len,
	       uint8_t *out, uint32_t out_cap, uint32_t *out_len)
{
	if (z->vlan_op == 1) {
		if (in_len < 12 || out_cap < in_len + 4)
			return -1;
		memcpy(out, in, 12);
		out[12] = 0x81;
		out[13] = 0x00;
		zxpon_put_be16(out + 14, z->vlan_tci);
		memcpy(out + 16, in + 12, in_len - 12);
		*out_len = in_len + 4;
		return 0;
	}
	if (z->vlan_op == 2) {
		if (in_len < 16 || in[12] != 0x81 || in[13] != 0x00)
			return -1;
		if (out_cap < in_len - 4)
			return -1;
		memcpy(out, in, 12);
		memcpy(out + 12, in + 16, in_len - 16);
		*out_len = in_len - 4;
		return 0;
	}
	if (out_cap < in_len)
		return -1;
	memcpy(out, in, in_len);
	*out_len = in_len;
	return 0;
}