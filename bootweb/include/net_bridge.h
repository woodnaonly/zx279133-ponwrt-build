/* SPDX-License-Identifier: GPL-2.0-only */
#ifndef BW_NET_BRIDGE_H
#define BW_NET_BRIDGE_H
#include "platform.h"
/* Bind only verified raw-Ethernet operations. The legacy eth_rx wrapper feeds
 * U-Boot's OLD IP stack, so it is NOT a replacement for poll_frames here.
 * poll_frames must deliver complete frames, strip vendor DMA metadata, copy
 * before DMA buffer reuse, and restore any RAM-only receive hook on exit. */
struct bw_ethernet {
    void *context;
    uint8_t mac[6];
    int (*start)(void *);
    int (*send_frame)(void *, const void *, size_t);
    int (*poll_frames)(void *, void (*receive)(void *, const void *, size_t), void *);
    uint64_t (*milliseconds)(void *);
    uint32_t (*random32)(void *);
    void (*watchdog)(void *);
};
BW_API int bw_lwip_bind(struct bw_platform *, const struct bw_ethernet *);
/* Close listener/streams and remove the netif BEFORE restoring a vendor RX
 * hook. Clears all references to the caller's RAM-trial stack/context. */
BW_API void bw_lwip_shutdown(void);
#endif
