/* SPDX-License-Identifier: GPL-2.0-only */
#ifndef BW_INTERNAL_H
#define BW_INTERNAL_H
#include "bootweb.h"
struct bw_hash { uint32_t h[8]; uint64_t bytes; uint8_t block[64]; size_t used; };
void bw_hash_init(struct bw_hash *);
void bw_hash_add(struct bw_hash *, const void *, size_t);
void bw_hash_end(struct bw_hash *, char out[65]);
void bw_zero(void *, size_t);
void bw_copy(void *, const void *, size_t);
int bw_equal(const void *, const void *, size_t);
int bw_string(const char *, const char *);
int bw_fail(char *, const char *);
uint32_t bw_be32(const uint8_t *);
uint32_t bw_le32(const uint8_t *);
uint32_t bw_crc32_linux(const void *, size_t);
int bw_hex64(const char *);
int bw_identity_valid(const struct bw_identity *);
int bw_legacy_identity(const char *, struct bw_identity *);
int bw_decimal(const uint8_t *, size_t, uint64_t *);
/* Single native instance only. Keeps Ethernet/watchdog serviced during large
 * CRC/hash operations; callback MUST NOT reenter the validator/writer. */
void bw_set_poll(void (*)(void *), void *);
void bw_poll(void);
/* A deadline is independent of polling: polling may keep the NIC alive while
 * a validator must still abort. No callback is retained after bw_serve exits. */
#ifdef BW_TEST_BASE
BW_API
#endif
void bw_set_cancel(int (*)(void *), void *);
int bw_cancelled(void);
int bw_json_metadata(const uint8_t *, size_t, char *);
int bw_fdt_layout(const uint8_t *, size_t, uint32_t, char *);
#endif
