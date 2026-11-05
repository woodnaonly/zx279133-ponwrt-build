/* SPDX-License-Identifier: GPL-2.0-only */
#ifndef BW_ENTROPY_H
#define BW_ENTROPY_H
#include <stdint.h>
struct bw_entropy_io {
    void *context;
    uint64_t (*counter)(void *);
    uint64_t (*milliseconds)(void *);
    uint32_t timeout_ms;
};
/* Single caller, bounded startup+continuous RCT/APT, no external seed or
 * deterministic fallback. On every failure the entire output is zeroed. */
int bw_entropy_token(const struct bw_entropy_io *,char [65]);
#endif
