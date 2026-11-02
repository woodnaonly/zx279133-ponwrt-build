/* SPDX-License-Identifier: GPL-2.0-only */
#ifndef BW_PLATFORM_H
#define BW_PLATFORM_H
#include "bootweb.h"
/* Network streams are provided by a small TCP stack (e.g. lwIP raw API) over
 * vendor Ethernet TX/RX. This interface NEVER exports a U-Boot shell runner.
 * recv/send return byte count or <=0; accept returns >0 stream, 0 none, <0 error.
 * poll MUST keep Ethernet and the VERIFIED watchdog path alive. */
struct bw_platform {
    struct bw_port nand;
    void *context;
    uint8_t *upload;
    size_t upload_capacity;
    void *scratch;
    size_t scratch_capacity;
    char token[65]; /* device entropy, NOT derived from public MAC/time */
    uint32_t layout;
    int (*listen)(void *, uint32_t ipv4, uint16_t port);
    int (*accept)(void *);
    int (*recv)(void *, int stream, void *, size_t, uint32_t timeout_ms);
    int (*send)(void *, int stream, const void *, size_t);
    void (*close)(void *, int stream);
    void (*poll)(void *);
    uint64_t (*milliseconds)(void *);
    int (*reboot)(void *);
    /* Optional serial-side exit for RAM trials, checked only BETWEEN tasks.
     * No HTTP route exposes this. Never abandons an accepted flash task. */
    int (*stop_requested)(void *);
};
BW_API int bw_serve(struct bw_platform *);
int bw_vendor_attach(struct bw_platform *, int argc, char *const argv[]);
#endif
