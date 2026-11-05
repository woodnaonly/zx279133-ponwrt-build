/* SPDX-License-Identifier: GPL-2.0-only */
#ifndef BW_PLATFORM_H
#define BW_PLATFORM_H
#include "bootweb.h"
#define BW_ENTRY_REQUEST_REBOOT 3
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
    /* Optional exit for RAM trials, checked only BETWEEN tasks. Only the
     * immutable backup build has authenticated backup-finish; write-capable
     * services expose no exit route. Never abandons an accepted flash task. */
    int (*stop_requested)(void *);
    /* Set ONLY after the native adapter recognizes the actual ECC-read
     * installed image. A RAM load or candidate manifest is not installation. */
    const char *resident_version;
    /* Native-only policy. A capability bit cannot bypass a current backup,
     * candidate-specific timing bound or an installed-image check. */
    const char *(*update_lock)(void *, const struct bw_result *);
    int (*prepare_update)(void *, const struct bw_task *, char error[BW_ERROR_SIZE]);
    void (*finish_update)(void *, int success);
    uint32_t require_continuous_power;
    uint64_t update_bound_ms;
    uint32_t progress_io; /* bounded, read-only status handling during flash */
    uint32_t requested_reboot; /* deferred until native detach + cache/record restoration */
};
BW_API int bw_serve(struct bw_platform *);
int bw_vendor_attach(struct bw_platform *, int argc, char *const argv[]);
int bw_vendor_detach(struct bw_platform *);
#endif
