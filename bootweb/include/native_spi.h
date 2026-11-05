/* SPDX-License-Identifier: GPL-2.0-only */
#ifndef BW_NATIVE_SPI_H
#define BW_NATIVE_SPI_H
#include <stdint.h>
/* Experimental RAM-only bounded controller binding. Never enables NAND
 * program/erase; every mutating NAND opcode is denied by this trial adapter. */
int bw_native_spi_attach(uintptr_t reloc,uintptr_t mtd);
int bw_native_spi_failed(void);
int bw_native_spi_ready(void);
int bw_native_spi_detach(void);
/* Internal wrapper-scoped deadline. Read/bad queries use these directly;
 * erase/page gates own them internally. Failure is sticky, never retried. */
int bw_native_spi_io_begin(uint32_t maximum_ms);
int bw_native_spi_io_end(void);
#ifdef BW_NATIVE_INSTALL
/* Internal native installer only; never exported by the immutable RAM build.
 * Caller MUST complete current-backup, container, RAM and power preflight. */
int bw_native_spi_write_begin(uint32_t offset,const void *page);
int bw_native_spi_write_end(void);
#ifdef BW_RESIDENT_NATIVE_WRITE
int bw_native_spi_write_target_begin(unsigned,uint32_t,const void *);
int bw_native_spi_write_layout_begin(unsigned,unsigned,uint32_t,const void *);
#endif
#endif
#ifdef BW_NATIVE_NAND_DRYRUN
/* Execute the exact ECC wrapper ABI while emulating ALL mutating opcodes.
 * This never sends WREN, program-load, program-execute or erase to the chip. */
int bw_native_spi_dry_begin(uint32_t offset,const void *page);
int bw_native_spi_dry_end(void);
#ifdef BW_RESIDENT_NATIVE_WRITE
int bw_native_spi_dry_target_begin(unsigned,uint32_t,const void *);
int bw_native_spi_dry_layout_begin(unsigned,unsigned,uint32_t,const void *);
#endif
#endif
#endif
