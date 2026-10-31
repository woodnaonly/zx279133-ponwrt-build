#include <stdio.h>
#include <string.h>

#include "zxpon.h"

static int failed;

static void expect(int cond, const char *what)
{
	if (cond)
		return;
	fprintf(stderr, "FAIL %s\n", what);
	failed++;
}

static void put16(uint8_t *p, unsigned v)
{
	p[0] = (uint8_t)(v >> 8);
	p[1] = (uint8_t)v;
}

static void ds(uint8_t f[48], uint8_t id, unsigned onu,
	       const uint8_t *c, unsigned n)
{
	memset(f, 0, 48);
	put16(f, (uint16_t)onu);
	f[2] = id;
	if (c && n)
		memcpy(f + 4, c, n);
	zxpon_ploam_seal(f);
}

static void omci(uint8_t f[48], uint8_t mt, unsigned class_id, unsigned inst,
		 const uint8_t *c, unsigned n)
{
	memset(f, 0, 48);
	f[0] = 0x00;
	f[1] = 0x01;
	f[2] = (uint8_t)((mt & 0x1f) | 0x40);
	f[3] = 0x0a;
	put16(f + 4, (uint16_t)class_id);
	put16(f + 6, (uint16_t)inst);
	if (c && n)
		memcpy(f + 8, c, n);
	f[43] = 0x28;
}

static void arm(struct zxpon *z)
{
	static const uint8_t sn[8] = { 'Z','T','E','G', 0x11, 0x22, 0x33, 0x44 };
	static const uint8_t regid[36] = { 'L','O','I','D' };

	zxpon_init(z);
	zxpon_set_identity(z, sn, regid);
	zxpon_set_lods(z, 0);
	zxpon_set_los(z, 0);
}

static int take_type(struct zxpon *z, uint8_t want, uint8_t out[48])
{
	int i;

	for (i = 0; i < 8; i++) {
		if (!zxpon_take_ploam(z, out))
			return 0;
		expect(zxpon_ploam_mic_ok(out), "upstream MIC");
		if (out[2] == want)
			return 1;
	}
	return 0;
}

static void reach_o4(struct zxpon *z)
{
	uint8_t f[48], up[48], body[8];

	arm(z);
	expect(zxpon_state(z) == ZXPON_O23, "enter O2-3");
	expect(take_type(z, ZXPON_US_SERIAL_NUMBER, up), "serial number");
	expect(zxpon_ploam_pending(z) == 0, "only one serial number");
	ds(f, ZXPON_DS_BURST_PROFILE, ZXPON_BCAST_ONUID, NULL, 0);
	expect(zxpon_rx_ploam(z, f) == 0, "burst profile");
	put16(body, 7);
	ds(f, ZXPON_DS_ASSIGN_ONUID, ZXPON_BCAST_ONUID, body, 2);
	expect(zxpon_rx_ploam(z, f) == 0, "assign onu-id");
	expect(zxpon_state(z) == ZXPON_O4, "O4");
}

static void reach_o5(struct zxpon *z)
{
	uint8_t f[48], up[48];

	reach_o4(z);
	ds(f, ZXPON_DS_RANGING_TIME, 7, NULL, 0);
	expect(zxpon_rx_ploam(z, f) == 0, "ranging");
	expect(zxpon_state(z) == ZXPON_O5, "O5");
	expect(take_type(z, ZXPON_US_ACK, up), "ranging ack");
	ds(f, ZXPON_DS_REQUEST_REG, 7, NULL, 0);
	expect(zxpon_rx_ploam(z, f) == 0, "request registration");
	expect(take_type(z, ZXPON_US_REGISTRATION, up), "registration");
	expect(memcmp(up + 4, "LOID", 4) == 0, "registration id");
}

static void test_los_from_o5(void)
{
	struct zxpon z;
	uint8_t up[48];

	reach_o5(&z);
	while (zxpon_take_ploam(&z, up))
		;
	zxpon_set_los(&z, 1);
	expect(zxpon_state(&z) == ZXPON_O1, "LOS returns to O1");
	expect(!zxpon_take_ploam(&z, up), "LOS sends no serial number");
	expect(strcmp(zxpon_state_name(&z), "O1") == 0, "state name O1");
}

static void test_to1(void)
{
	struct zxpon z;
	uint8_t up[48];

	reach_o4(&z);
	zxpon_tick(&z, 9999);
	expect(zxpon_state(&z) == ZXPON_O4, "TO1 still running");
	zxpon_tick(&z, 1);
	expect(zxpon_state(&z) == ZXPON_O1, "TO1 back to O1");
	expect(!zxpon_take_ploam(&z, up), "TO1 sends no serial number");
}

static void test_disable(void)
{
	struct zxpon z;
	uint8_t f[48], body[9], up[48];

	reach_o5(&z);
	memset(body, 0xff, sizeof(body));
	ds(f, ZXPON_DS_DISABLE_SN, ZXPON_BCAST_ONUID, body, 9);
	expect(zxpon_rx_ploam(&z, f) == 0, "disable");
	expect(zxpon_state(&z) == ZXPON_O7, "O7");
	body[0] = 0x00;
	ds(f, ZXPON_DS_DISABLE_SN, ZXPON_BCAST_ONUID, body, 9);
	expect(zxpon_rx_ploam(&z, f) == 0, "enable");
	expect(zxpon_state(&z) == ZXPON_O23, "enable returns to O2-3");
	expect(take_type(&z, ZXPON_US_SERIAL_NUMBER, up), "serial number after enable");
}

static void test_gem_and_mib(void)
{
	struct zxpon z;
	uint8_t f[48], body[8], om[48], rsp[48];
	uint16_t gem = 0, omcc = 0;
	int i, n, saw256 = 0;
	unsigned seen[16];
	int nseen = 0;

	reach_o5(&z);
	memset(body, 0, sizeof(body));
	put16(body, 1);
	body[2] = ZXPON_ALLOC_F_OMCC;
	put16(body + 3, 100);
	ds(f, ZXPON_DS_ASSIGN_ALLOCID, 7, body, 5);
	expect(zxpon_rx_ploam(&z, f) == 0, "omcc alloc");
	memset(body, 0, sizeof(body));
	put16(body, 2);
	ds(f, ZXPON_DS_ASSIGN_ALLOCID, 7, body, 5);
	expect(zxpon_rx_ploam(&z, f) == 0, "data alloc");
	expect(zxpon_omcc_gem(&z, &omcc) && omcc == 100, "omcc gem 100");
	expect(zxpon_map_gem(&z, 100) == -1, "omcc cannot map to eth0");

	memset(body, 0, sizeof(body));
	put16(body, 200);
	omci(om, 4, 268, 1, body, 2);
	expect(zxpon_rx_omci(&z, om) == 0, "create gem ctp");
	memset(body, 0, sizeof(body));
	put16(body, 1);
	omci(om, 4, 266, 1, body, 2);
	expect(zxpon_rx_omci(&z, om) == 0, "create gem iw");
	while (zxpon_take_omci(&z, rsp))
		;
	expect(zxpon_data_gem(&z, &gem) && gem == 200, "eth0 gem is 200");
	expect(zxpon_omcc_gem(&z, &omcc) && omcc == 100, "omcc stays 100");

	put16(body, 0x2000);
	omci(om, 9, 256, 0, body, 2);
	expect(zxpon_rx_omci(&z, om) == 0, "get onu-g");
	expect(zxpon_take_omci(&z, rsp), "get response");
	expect(memcmp(rsp + 11, "ZTEG", 4) == 0, "ONU-G serial number");
	expect(zxpon_crc32(rsp, 44) ==
	       ((uint32_t)rsp[44] << 24 | (uint32_t)rsp[45] << 16 |
		(uint32_t)rsp[46] << 8 | rsp[47]), "omci crc");

	omci(om, 13, 0, 0, NULL, 0);
	expect(zxpon_rx_omci(&z, om) == 0, "mib upload");
	expect(zxpon_take_omci(&z, rsp), "upload count");
	n = (rsp[8] << 8) | rsp[9];
	expect(n >= 11, "upload covers the built-in and created MEs");
	for (i = 0; i < n; i++) {
		uint8_t seq[2];
		unsigned class_id;

		put16(seq, (unsigned)i);
		omci(om, 14, 0, 0, seq, 2);
		expect(zxpon_rx_omci(&z, om) == 0, "upload next");
		expect(zxpon_take_omci(&z, rsp), "upload next response");
		class_id = ((unsigned)rsp[8] << 8) | rsp[9];
		if (class_id == 256) {
			saw256 = 1;
			expect(memcmp(rsp + 14, "ZTEG", 4) == 0, "upload SN");
		}
		if (nseen < 16)
			seen[nseen++] = class_id;
	}
	expect(saw256, "upload includes ONU-G");
	{
		static const unsigned want[] = {
			257, 263, 45, 47, 329, 277, 134, 268, 266
		};
		unsigned w;

		for (w = 0; w < sizeof(want) / sizeof(want[0]); w++) {
			int got = 0;

			for (i = 0; i < nseen; i++)
				if (seen[i] == want[w])
					got = 1;
			expect(got, "uploaded ME missing");
		}
	}
}

static void test_vlan(void)
{
	struct zxpon z;
	uint8_t in[64], out[80], body[4], om[48];
	uint32_t out_len = 0;

	zxpon_init(&z);
	memset(in, 0x11, sizeof(in));
	body[0] = 1;
	put16(body + 1, 0x0064);
	omci(om, 4, 171, 1, body, 3);
	expect(zxpon_rx_omci(&z, om) == 0, "create vlan");
	expect(zxpon_vlan(&z, in, sizeof(in), out, sizeof(out), &out_len) == 0,
	       "push vlan");
	expect(out_len == sizeof(in) + 4, "vlan length");
	expect(out[12] == 0x81 && out[13] == 0x00 && out[14] == 0x00 &&
	       out[15] == 0x64, "vlan tag");
}

static void test_cmac_and_crc(void)
{
	static const uint8_t key[16] = {
		0x2b,0x7e,0x15,0x16,0x28,0xae,0xd2,0xa6,
		0xab,0xf7,0x15,0x88,0x09,0xcf,0x4f,0x3c
	};
	static const uint8_t msg[16] = {
		0x6b,0xc1,0xbe,0xe2,0x2e,0x40,0x9f,0x96,
		0xe9,0x3d,0x7e,0x11,0x73,0x93,0x17,0x2a
	};
	static const uint8_t want[16] = {
		0x07,0x0a,0x16,0xb4,0x6b,0x4d,0x41,0x44,
		0xf7,0x9b,0xdd,0x9d,0xd0,0x4a,0x28,0x7c
	};
	uint8_t mac[16];

	zxpon_cmac(key, msg, 16, mac);
	expect(memcmp(mac, want, 16) == 0, "NIST AES-CMAC");
	expect(zxpon_crc32((const uint8_t *)"123456789", 9) == 0xcbf43926u,
	       "CRC-32");
}

int main(void)
{
	test_cmac_and_crc();
	test_los_from_o5();
	test_to1();
	test_disable();
	test_gem_and_mib();
	test_vlan();
	if (failed) {
		fprintf(stderr, "%d failure(s)\n", failed);
		return 1;
	}
	printf("zxpon: pass\n");
	return 0;
}