/* SPDX-License-Identifier: GPL-2.0-only */
#include "platform.h"
extern unsigned char __bss_start[],__bss_end[];
#if defined(BW_RAM_READONLY) || defined(BW_RESIDENT_NATIVE_WRITE)
extern uint64_t __ram_stack_bottom[];
#endif
void *memcpy(void *dst,const void *src,size_t n) {unsigned char *d=dst;const unsigned char *s=src;while(n--)*d++=*s++;return dst;}
void *memset(void *dst,int value,size_t n) {unsigned char *d=dst;while(n--)*d++=(unsigned char)value;return dst;}
void *memmove(void *dst,const void *src,size_t n) {
    unsigned char *d=dst;const unsigned char *s=src;size_t i;
    if((uintptr_t)d<(uintptr_t)s)for(i=0;i<n;i++)d[i]=s[i];else while(n){n--;d[n]=s[n];}return dst;
}
int memcmp(const void *a,const void *b,size_t n) {
    const unsigned char *x=a,*y=b;size_t i;for(i=0;i<n;i++)if(x[i]!=y[i])return x[i]-y[i];return 0;
}
size_t strlen(const char *s) {size_t n=0;while(s[n])n++;return n;}
int strcmp(const char *a,const char *b) {while(*a&&*a==*b){a++;b++;}return (unsigned char)*a-(unsigned char)*b;}
int strncmp(const char *a,const char *b,size_t n) {while(n&&*a&&*a==*b){a++;b++;n--;}return n?(unsigned char)*a-(unsigned char)*b:0;}
int atoi(const char *s) {
    int sign=1;uint32_t v=0;
    while(*s==' '||*s=='\t'||*s=='\r'||*s=='\n')s++;
    if(*s=='-'||*s=='+'){if(*s=='-')sign=-1;s++;}
    while(*s>='0'&&*s<='9'){uint32_t d=(uint32_t)(*s++-'0');if(v>(2147483647u-d)/10)return sign>0?2147483647:-2147483647;v=v*10+d;}
    return sign*(int)v;
}
int bootweb_entry(int argc,char *const argv[]) {
    struct bw_platform p;unsigned char *b;int rc;
    /* Called by a VERIFIED command ABI, retaining the caller's SP/x18.
     * 0x89000000 is a development link choice, NOT a proven safe RAM placement. */
    for(b=__bss_start;b<__bss_end;b++)*b=0;memset(&p,0,sizeof(p));
#if defined(BW_RAM_READONLY) || defined(BW_RESIDENT_NATIVE_WRITE)
    __ram_stack_bottom[0]=0xb00757ac6a11beefULL;
#endif
    if(bw_vendor_attach(&p,argc,argv)) {bw_vendor_detach(&p);return 1;}
    rc=bw_serve(&p);if(bw_vendor_detach(&p))rc=1;
#if defined(BW_RAM_READONLY) || defined(BW_RESIDENT_NATIVE_WRITE)
    if(__ram_stack_bottom[0]!=0xb00757ac6a11beefULL)rc=1;
#endif
#ifdef BW_RESIDENT_NATIVE_WRITE
    if(!rc&&p.requested_reboot)return BW_ENTRY_REQUEST_REBOOT;
#endif
    return rc;
}
/* The measured community image has no go command. A future RAM-only command
 * registration must use cmd_tbl's FOUR-argument ABI, not pretend go exists. */
#if defined(BW_RAM_READONLY) || defined(BW_RESIDENT_NATIVE_WRITE)
/* Four-argument command ABI. Preserve the vendor SP/FP/LR and run on an
 * explicit 64 KiB stack that is part of the audited ELF RAM footprint. */
__asm__(".section .text.entry,\"ax\"\n"
        ".balign 4\n.global bootweb_command\n.type bootweb_command,%function\n"
        "bootweb_command:\n"
        "mov x9,sp\nadrp x10,__ram_stack_top\nadd x10,x10,:lo12:__ram_stack_top\n"
        "mov sp,x10\nstp x29,x30,[sp,#-32]!\nstr x9,[sp,#16]\n"
        "mov w0,w2\nmov x1,x3\nbl bootweb_entry\n"
        "ldr x9,[sp,#16]\nldp x29,x30,[sp],#32\nmov sp,x9\nret\n"
        ".size bootweb_command,.-bootweb_command\n.previous\n");
#else
__attribute__((section(".text.entry")))
int bootweb_command(void *command,int flags,int argc,char *const argv[]) {
    (void)command;(void)flags;return bootweb_entry(argc,argv);
}
#endif
