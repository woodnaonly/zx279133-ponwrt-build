/* SPDX-License-Identifier: GPL-2.0-only
 * Minimal RAM-only four-argument command. Constants are compiled only after
 * the host has inspected the real relocated command record and printf bytes.
 * No network, NAND, GPIO, reset, environment or cache-register write exists.
 */
#include <stddef.h>
#include <stdint.h>
#if !defined(BW_PROBE_PRINTF) || !defined(BW_PROBE_COMMAND) || !defined(BW_PROBE_ORIGINAL) || !defined(BW_PROBE_RELOC)
#error "probe requires measured runtime constants"
#endif
#ifndef BW_PROBE_COMMAND_NAME
#define BW_PROBE_COMMAND_NAME "?"
#endif
typedef int (*print_function)(const char *,...);
__attribute__((section(".text.entry")))
int bw_ram_probe(void *command,int flags,int argc,char *const argv[]) {
    uintptr_t gd,sp;uint64_t level,control,reloc,offset;print_function print=(print_function)BW_PROBE_PRINTF;
    (void)flags;
    __asm__ volatile("mov %0,x18":"=r"(gd));
    __asm__ volatile("mov %0,sp":"=r"(sp));
    __asm__ volatile("mrs %0,CurrentEL":"=r"(level));
    {const char *expected=BW_PROBE_COMMAND_NAME;size_t i;
     if((uintptr_t)command!=BW_PROBE_COMMAND||argc!=1||!argv||!argv[0])return 1;
     for(i=0;expected[i];i++)if(argv[0][i]!=expected[i])return 1;
     if(argv[0][i])return 1;}
    if(gd<0x9e000000u||gd>0xa0000000u-208||sp<0x9e000000u||sp>=0xa0000000u)return 1;
    reloc=*(const uint64_t *)(gd+112);offset=*(const uint64_t *)(gd+152);
    if(reloc!=BW_PROBE_RELOC||offset!=BW_PROBE_RELOC-0x88000000u)return 1;
    if(level==4)__asm__ volatile("mrs %0,SCTLR_EL1":"=r"(control));
    else if(level==8)__asm__ volatile("mrs %0,SCTLR_EL2":"=r"(control));
    else if(level==12)__asm__ volatile("mrs %0,SCTLR_EL3":"=r"(control));
    else return 1;
    /* Restore '?' before printing/returning, even if the host later goes away. */
    *(volatile uint64_t *)((uintptr_t)command+24)=BW_PROBE_ORIGINAL;
    __asm__ volatile("dsb sy\n\tisb":::"memory");
    print("BW_RAM_PROBE v1 gd=%llx sp=%llx el=%llu sctlr=%llx reloc=%llx off=%llx argc=%d RETURNING\n",
          (unsigned long long)gd,(unsigned long long)sp,(unsigned long long)(level>>2),
          (unsigned long long)control,(unsigned long long)reloc,(unsigned long long)offset,argc);
    return 0;
}
