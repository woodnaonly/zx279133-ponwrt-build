/* SPDX-License-Identifier: GPL-2.0-only */
#ifndef BW_SHA3_H
#define BW_SHA3_H
#include <stddef.h>
#include <stdint.h>
struct bw_sha3 {uint64_t state[25];size_t used;};
void bw_sha3_init(struct bw_sha3 *);
void bw_sha3_add(struct bw_sha3 *,const void *,size_t);
void bw_sha3_end(struct bw_sha3 *,uint8_t [32]);
void bw_sha3_256(const void *,size_t,uint8_t [32]);
#endif
