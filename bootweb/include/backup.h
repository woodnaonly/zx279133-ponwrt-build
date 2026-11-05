/* SPDX-License-Identifier: GPL-2.0-only */
#ifndef BW_BACKUP_H
#define BW_BACKUP_H
#include "bootweb.h"
#define BW_BACKUP_HEADER 128u
#define BW_BACKUP_CHUNK 0x100000u
/* Physical ECC data, never raw/OOB or skip-bad. Known bad blocks have explicit
 * FF placeholders and a bitmap; an error is NOT converted into a bad block.
 * This optional API exists only in a separate immutable read-only artifact. */
BW_API int bw_backup_capture(const struct bw_port *, uint32_t offset, uint32_t length,
                             void *buffer, size_t capacity, char error[BW_ERROR_SIZE]);
/* Optional bounded byte-run wire packing, AFTER ECC and the raw SHA256. Dense
 * data stays v1. Sparse NAND is v2; hashes always describe decoded data. */
BW_API size_t bw_backup_pack(void *buffer, size_t capacity);
/* Authenticated read-only completion, never a reboot or a write capability. */
BW_API void bw_backup_finish_reset(void);
BW_API void bw_backup_finish(void);
BW_API int bw_backup_finished(void);
#endif
