/* SPDX-License-Identifier: GPL-2.0-only
 * Executed at the BTL's ORIGINAL load address. Only caller-saved registers
 * are used. The resident dispatcher is linked at its final RAM address and
 * is never executed in-place or left where bootcmd loads the Linux kernel.
 */
#include <stdint.h>
#include "bw_bootstrap_blob.h"
__asm__(".section .text.entry,\"ax\"\n"
        ".balign 4\n.global bw_resident_stage0\n.type bw_resident_stage0,%function\n"
        "bw_resident_stage0:\n"
        "adrp x9,bw_bootstrap_blob\nadd x9,x9,:lo12:bw_bootstrap_blob\n"
        "movz w10,#0x8910,lsl #16\nmov x12,x10\n"
        "movz x11,#(" BW_BOOTSTRAP_BYTES_ASM " & 0xffff)\n"
        "movk x11,#(" BW_BOOTSTRAP_BYTES_ASM " >> 16),lsl #16\n"
        "add x13,x10,x11\n"
        "1: ldp x14,x15,[x9],#16\nstp x14,x15,[x10],#16\n"
        "subs x11,x11,#16\nb.ne 1b\n"
        /* Clean the data side to PoU before invalidating the instruction side.
         * CTR_EL0 supplies the actual minimum line sizes, not guessed 64B. */
        "mrs x9,ctr_el0\nubfx x11,x9,#16,#4\nmov x14,#4\n"
        "lsl x11,x14,x11\nsub x15,x11,#1\nbic x10,x12,x15\n"
        "2: dc cvau,x10\nadd x10,x10,x11\ncmp x10,x13\nb.lo 2b\n"
        "dsb sy\nand x9,x9,#0xf\nlsl x11,x14,x9\n"
        "sub x15,x11,#1\nbic x10,x12,x15\n"
        "3: ic ivau,x10\nadd x10,x10,x11\ncmp x10,x13\nb.lo 3b\n"
        "dsb sy\nisb\nbr x12\n"
        ".size bw_resident_stage0,.-bw_resident_stage0\n.previous\n");
