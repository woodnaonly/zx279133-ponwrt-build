/* SPDX-License-Identifier: GPL-2.0-only */
#ifndef BW_RAM_READONLY_POLICY_H
#define BW_RAM_READONLY_POLICY_H
#include "bootweb.h"
/* Fixed PHYSICAL ECC-read ranges. Never wrap, skip bad blocks or include OOB. */
static inline int bw_ram_read_allowed(uint32_t off,size_t n) {
    if(!n||n>BW_ERASE_SIZE||off>BW_FLASH_SIZE-n)return 0;
#ifdef BW_RAM_BACKUP
    return 1; /* separate full physical ECC backup build, still no write APIs */
#else
    return (off<0x200000u&&n<=0x200000u-off)||
           (off>=0xb00000u&&off<0xf00000u&&n<=0xf00000u-off);
#endif
}
/* 0 = complete clean read, 1 = complete corrected ECC, -1 = failure. */
static inline int bw_ram_ecc_result(int rc,size_t done,size_t wanted) {
    if(!wanted||done!=wanted)return -1;
    return rc==0?0:rc==-117?1:-1;
}
#endif
