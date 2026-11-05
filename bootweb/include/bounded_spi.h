/* SPDX-License-Identifier: GPL-2.0-only */
#ifndef BW_BOUNDED_SPI_H
#define BW_BOUNDED_SPI_H
#include "bootweb.h"
/* Exact measured AArch64 spi_mem_op layout. No packed/unaligned dereferences. */
struct bw_spi_op {
    uint8_t command_width,opcode,reserved0[6];
    uint8_t address_bytes,address_width,reserved1[6];
    uint64_t address;
    uint8_t dummy_bytes,dummy_width,reserved2[6];
    uint8_t data_width,reserved3[3];
    uint32_t direction,length,reserved4;
    void *data;
};
struct bw_spi_io {
    void *context;
    uint32_t (*read32)(void *,uint32_t offset);
    void (*write32)(void *,uint32_t offset,uint32_t value);
    uint64_t (*milliseconds)(void *);
    int (*select)(void *);
    uint32_t timeout_ms,poll_limit;
};
/* Replaces the measured controller operation, not the NAND ECC algorithm.
 * Any failure is sticky in the native owner. These functions do NOT authorize
 * program/erase opcodes, flash ranges, or installation budgets by themselves. */
BW_API int bw_bounded_spi_exec(const struct bw_spi_io *,const struct bw_spi_op *);
/* submitted=1 only after writing the controller's actual START register.
 * It is not a NAND completion indication. Busy/validation failures keep zero. */
BW_API int bw_bounded_spi_exec_tracked(const struct bw_spi_io *,const struct bw_spi_op *,int *submitted);
/* Complete native ECC wrapper deadline, in addition to each FIFO deadline.
 * Returns a <=5ms next-controller allowance. This is not a timing certificate. */
BW_API int bw_bounded_spi_remaining(uint64_t started,uint64_t now,uint32_t maximum_ms,uint32_t *remaining_ms);
#endif
