/* SPDX-License-Identifier: GPL-2.0-only */
#ifndef BW_NAND_GATE_H
#define BW_NAND_GATE_H
#include "bounded_spi.h"
struct bw_nand_gate {
    uint32_t offset,state,kind,failed;
    uint8_t load_opcode;
    char page_sha256[65];
};
enum bw_nand_target {BW_NAND_BOOT=0,BW_NAND_KERNEL=1,BW_NAND_DTB=2,BW_NAND_ROOT=3};
/* Pure geometry. Legacy entry points keep their current240 meaning.
 * The installer APIs below retain BOOT-only semantics. System targets are
 * for a distinct future resident adapter, never a caller-supplied interval. */
BW_API int bw_nand_target_contains(unsigned,uint32_t,size_t);
BW_API int bw_nand_gate_begin_target(struct bw_nand_gate *,unsigned,uint32_t,const void *,uint8_t);
BW_API int bw_nand_target_contains_layout(unsigned,unsigned,uint32_t,size_t);
BW_API int bw_nand_gate_begin_layout(struct bw_nand_gate *,unsigned,unsigned,uint32_t,const void *,uint8_t);
/* These pure guards cannot access hardware. Every per-operation mutation is
 * restricted to one exact row in the physical U-Boot window. No bad mapping,
 * OOB programming, marking bad or multi-page cache writes are accepted. */
BW_API int bw_nand_gate_begin(struct bw_nand_gate *,uint32_t,const void *,uint8_t);
BW_API int bw_nand_gate_accept(struct bw_nand_gate *,const struct bw_spi_op *);
BW_API int bw_nand_gate_finish(struct bw_nand_gate *);
#endif
