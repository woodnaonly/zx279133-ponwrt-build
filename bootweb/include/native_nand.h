/* SPDX-License-Identifier: GPL-2.0-only */
#ifndef BW_NATIVE_NAND_H
#define BW_NATIVE_NAND_H
#include "bootweb.h"
struct bw_native_nand {
    uintptr_t reloc,mtd;
    uint32_t reads,corrected_reads,erases,pages,skipped_ff;
    uint32_t max_read_ms,max_bad_ms,max_erase_ms,max_page_ms;
    uint32_t targets,layout; /* installer binds BOOT/current240 only */
    int failed;
};
/* Interface binding is not flash authorization. Caller owns the complete
 * candidate/current-backup/container/confirmation/power preflight. */
int bw_native_nand_bind(struct bw_native_nand *,uintptr_t,uintptr_t,struct bw_port *);
int bw_native_nand_release(struct bw_native_nand *);
#ifdef BW_RESIDENT_NATIVE_WRITE
/* Only the distinct permanent Web adapter may expose current240 system
 * targets AFTER its identity/backup/time/confirmation preflight. Installers
 * do not compile this API and cannot expand their fixed physical window. */
int bw_native_nand_enable_system(struct bw_native_nand *,unsigned layout);
int bw_native_nand_system_ready(const struct bw_native_nand *);
#endif
#endif
