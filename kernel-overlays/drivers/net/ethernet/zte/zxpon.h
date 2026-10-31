#ifndef ZXPON_H
#define ZXPON_H

#ifdef __KERNEL__
#include <linux/types.h>
#else
#include <stdint.h>
#endif

/* genl "zxpon" numbers shared with the userspace daemon. */
#define ZXPON_A_STATE		1
#define ZXPON_A_SN		2
#define ZXPON_A_REGID		3
#define ZXPON_A_ALLOC		4
#define ZXPON_A_GEM		5
#define ZXPON_A_OMCC		6
#define ZXPON_A_KEY		7
#define ZXPON_A_MAX		8

#define ZXPON_CMD_GET_STATE	1
#define ZXPON_CMD_SET_IDENTITY	2
#define ZXPON_CMD_ADD_TCONT	3
#define ZXPON_CMD_ADD_GEM	4
#define ZXPON_CMD_MAP_GEM	5
#define ZXPON_CMD_SET_KEY	6
#define ZXPON_CMD_MAX		7

/* XG-PON activation (G.987.3) and the small G.988 MIB this ONU answers.
 * The SerDes image and the PON MAC registers are not programmed here.
 */

enum zxpon_state {
	ZXPON_O1 = 1,
	ZXPON_O23 = 23,
	ZXPON_O4 = 4,
	ZXPON_O5 = 5,
	ZXPON_O6 = 6,
	ZXPON_O7 = 7
};

#define ZXPON_DS_BURST_PROFILE		0x01
#define ZXPON_DS_ASSIGN_ONUID		0x03
#define ZXPON_DS_RANGING_TIME		0x04
#define ZXPON_DS_DEACTIVATE		0x05
#define ZXPON_DS_DISABLE_SN		0x06
#define ZXPON_DS_REQUEST_REG		0x09
#define ZXPON_DS_ASSIGN_ALLOCID		0x0a
#define ZXPON_DS_KEY_CONTROL		0x0d

#define ZXPON_US_SERIAL_NUMBER		0x01
#define ZXPON_US_REGISTRATION		0x02
#define ZXPON_US_KEY_REPORT		0x05
#define ZXPON_US_ACK			0x09

#define ZXPON_PLOAM_LEN			48
#define ZXPON_OMCI_LEN			48
#define ZXPON_SN_LEN			8
#define ZXPON_REGID_LEN			36
#define ZXPON_KEY_LEN			16
#define ZXPON_BCAST_ONUID		0x03ff
#define ZXPON_ALLOC_F_OMCC		0x01

#define ZXPON_PLOAM_Q			8
#define ZXPON_OMCI_Q			32
#define ZXPON_ME_MAX			24

struct zxpon_me {
	uint16_t class_id;
	uint16_t inst;
	uint8_t used;
	uint8_t builtin;
	uint16_t gem_port;
	uint16_t gem_ctp;
	uint8_t vlan_op;
	uint16_t vlan_tci;
};

struct zxpon {
	enum zxpon_state state;
	uint8_t sn[ZXPON_SN_LEN];
	uint8_t regid[ZXPON_REGID_LEN];
	int has_identity;
	int los;
	int lods;
	uint16_t onu_id;
	uint32_t to1_ms;
	uint32_t to2_ms;
	uint16_t omcc_gem;
	int omcc_valid;
	uint16_t data_gem;
	int data_valid;
	uint8_t key[ZXPON_KEY_LEN];
	int key_valid;
	uint8_t ploam_tx[ZXPON_PLOAM_Q][ZXPON_PLOAM_LEN];
	int ploam_tx_n;
	uint8_t omci_tx[ZXPON_OMCI_Q][ZXPON_OMCI_LEN];
	int omci_tx_n;
	struct zxpon_me me[ZXPON_ME_MAX];
	int me_n;
	uint8_t vlan_op;
	uint16_t vlan_tci;
};

void zxpon_init(struct zxpon *z);
void zxpon_set_identity(struct zxpon *z, const uint8_t sn[ZXPON_SN_LEN],
			const uint8_t regid[ZXPON_REGID_LEN]);
void zxpon_set_los(struct zxpon *z, int light_absent);
void zxpon_set_lods(struct zxpon *z, int lost);
void zxpon_tick(struct zxpon *z, uint32_t ms);
int zxpon_rx_ploam(struct zxpon *z, const uint8_t frame[ZXPON_PLOAM_LEN]);
int zxpon_rx_omci(struct zxpon *z, const uint8_t frame[ZXPON_OMCI_LEN]);
enum zxpon_state zxpon_state(const struct zxpon *z);
const char *zxpon_state_name(const struct zxpon *z);
int zxpon_take_ploam(struct zxpon *z, uint8_t out[ZXPON_PLOAM_LEN]);
int zxpon_take_omci(struct zxpon *z, uint8_t out[ZXPON_OMCI_LEN]);
int zxpon_ploam_pending(const struct zxpon *z);
int zxpon_data_gem(const struct zxpon *z, uint16_t *gem);
int zxpon_omcc_gem(const struct zxpon *z, uint16_t *gem);
int zxpon_map_gem(struct zxpon *z, uint16_t gem);
int zxpon_add_tcont(struct zxpon *z, uint16_t alloc_id, int omcc, uint16_t gem);
int zxpon_set_key(struct zxpon *z, const uint8_t key[ZXPON_KEY_LEN]);
void zxpon_ploam_seal(uint8_t frame[ZXPON_PLOAM_LEN]);
int zxpon_ploam_mic_ok(const uint8_t frame[ZXPON_PLOAM_LEN]);
void zxpon_cmac(const uint8_t key[ZXPON_KEY_LEN], const uint8_t *msg,
		uint32_t len, uint8_t mac[ZXPON_KEY_LEN]);
uint32_t zxpon_crc32(const uint8_t *buf, uint32_t len);
int zxpon_vlan(const struct zxpon *z, const uint8_t *in, uint32_t in_len,
	       uint8_t *out, uint32_t out_cap, uint32_t *out_len);

#endif