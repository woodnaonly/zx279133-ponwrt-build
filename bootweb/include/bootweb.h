/* SPDX-License-Identifier: GPL-2.0-only */
#ifndef BOOTWEB_H
#define BOOTWEB_H
#include <stddef.h>
#include <stdint.h>

#ifdef _WIN32
#define BW_API __declspec(dllexport)
#else
#define BW_API __attribute__((visibility("default")))
#endif

#define BW_BOARD "skyworth_sk-d840n"
#ifndef BW_TEST_BASE
#define BW_BASE "5b084490813b540c1a82ddf2e2d4de46080e6cfe47b15b2e6575bdf3fe198fe9"
#else
#define BW_BASE BW_TEST_BASE
#endif
#define BW_FLASH_SIZE 0x10000000u
#define BW_ERASE_SIZE 0x20000u
#define BW_PAGE_SIZE 0x800u
#define BW_MAX_UPLOAD 0x0fa10000u
#define BW_STAGE2_START 0x20000u
#define BW_STAGE2_END 0x180000u
#define BW_STAGE2_SIZE (BW_STAGE2_END - BW_STAGE2_START)
#define BW_ERROR_SIZE 192u

enum bw_layout { BW_LAYOUT_CURRENT = 0, BW_LAYOUT_NATIVE241 = 1,
                 BW_LAYOUT_RECOVERY237 = 2 };
enum bw_kind { BW_SYSTEM = 1, BW_UBOOT = 2 };
enum bw_state { BW_IDLE, BW_RECEIVING, BW_VALIDATING, BW_READY, BW_QUEUED,
                BW_WRITING, BW_SUCCESS, BW_FAILED, BW_CANCELLED };
struct bw_span {
    uint32_t offset, capacity;
    uint64_t data_offset, length;
    char name[16], sha256[65];
};
struct bw_result {
    uint32_t kind, layout, count;
    uint64_t package_bytes;
    char package_sha256[65];
    struct bw_span writes[3];
};

/* All offsets below are physical NAND byte offsets. These are NOT raw/OOB
 * operations: read/write MUST use the verified vendor ECC path. Query bad
 * blocks before touching flash. Never substitute a shell command adapter. */
enum bw_caps {
    BW_CAP_RAM_NETWORK = 1u << 0,
    BW_CAP_ECC_BADBLOCK = 1u << 1,
    BW_CAP_RAM_BACKUP = 1u << 2,
    BW_CAP_SERIAL_RESCUE = 1u << 3,
    BW_CAP_BOOT_CONTAINER = 1u << 4,
    BW_CAP_NATIVE_LAYOUT = 1u << 5,
    BW_CAP_RECOVERY_LAYOUT = 1u << 6
};
#define BW_REQUIRED (BW_CAP_RAM_NETWORK | BW_CAP_ECC_BADBLOCK | BW_CAP_RAM_BACKUP)
struct bw_port {
    void *context;
    uint32_t capabilities;
    int (*is_bad)(void *, uint32_t block); /* 0 good, 1 bad, <0 error */
    int (*read)(void *, uint32_t offset, void *dst, size_t len);
    int (*erase)(void *, uint32_t block); /* exactly one erase block */
    int (*write_page)(void *, uint32_t offset, const void *page);
    /* Mandatory for U-Boot tasks: target-specific BTL/container integrity,
     * loading length and executable layout check, not merely a SHA check. */
    int (*verify_boot)(void *, const void *stage2, size_t length);
    void (*watchdog)(void *);
    void (*progress)(void *, const char *phase, uint32_t done, uint32_t total);
};

BW_API void bw_sha256(const void *, size_t, char out[65]);
BW_API const char *bw_base_hash(void);
BW_API int bw_test_build(void);
BW_API uint32_t bw_crc32(const void *, size_t);
BW_API int bw_validate(const void *package, size_t length, const char *base_hash,
                       uint32_t layout, struct bw_result *, char error[BW_ERROR_SIZE]);
/* Revalidates the package, rereads the installed base, preflights ALL targets
 * before the first erase, and hashes readback. scratch >= BW_ERASE_SIZE. */
BW_API int bw_execute(const void *, size_t, const char *base_hash, uint32_t layout,
                      const struct bw_port *, void *scratch, size_t scratch_bytes,
                      char error[BW_ERROR_SIZE]);

/* One upload and task per bootweb instance; the caller serializes HTTP events.
 * The frozen upload buffer remains owned by the task through success/failure.
 * token is supplied by the transport (at least 128 random bits in the HTTP port).
 * A retry of the SAME confirm is idempotent, not a second flash operation. */
struct bw_task {
    uint32_t state, layout;
    uint64_t id;
    size_t expected, received, capacity;
    uint8_t *buffer;
    char token[65], base[65], error[BW_ERROR_SIZE];
    struct bw_result result;
};
BW_API int bw_task_init(struct bw_task *, void *buffer, size_t capacity,
                        const char *base, uint32_t layout, const char *token);
BW_API int bw_task_begin(struct bw_task *, size_t length, uint64_t id);
BW_API int bw_task_feed(struct bw_task *, const void *, size_t);
BW_API int bw_task_finish(struct bw_task *);
BW_API int bw_task_confirm(struct bw_task *, uint64_t id, const char *digest,
                           const char *token);
BW_API int bw_task_cancel(struct bw_task *, const char *token);
BW_API int bw_task_run(struct bw_task *, const struct bw_port *, void *, size_t);

/* Header-only HTTP parser. Body bytes are fed separately, never interpreted as
 * headers. No chunked bodies, multipart, pipelining or keep-alive are accepted. */
struct bw_request {
    char method[8], path[32], token[65];
    char host[64], origin[96];
    uint64_t length;
};
BW_API int bw_http_parse(const void *header, size_t length, struct bw_request *,
                         char error[BW_ERROR_SIZE]);
struct bw_entry_io {
    void *context;
    int gpio_verified; /* only after physical press/release/polarity measurements */
    int (*pressed)(void *); /* normalized 0/1, negative means read failure */
    uint64_t (*milliseconds)(void *);
    void (*poll)(void *);
};
/* 1 = continuously held for 3 seconds; 0 = normal boot; -1 = unverified/error.
 * No GPIO number or MMIO register is guessed or modified by this function. */
BW_API int bw_reset_entry(const struct bw_entry_io *);
#endif
