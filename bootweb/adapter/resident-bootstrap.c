/* SPDX-License-Identifier: GPL-2.0-only
 * First resident integration trial. NO erase/program/reboot binding. The
 * cold hook and command share exactly this dispatcher, but Reset/RNG and the
 * final writable adapter are still separate acceptance gates.
 */
#include "internal.h"
#include "platform.h"
#ifdef BW_RESIDENT_DEVICE_ENTROPY
#include "entropy.h"
#endif
extern unsigned char __bss_start[],__bss_end[];
extern uint64_t __ram_stack_bottom[];
extern int bw_resident_command(void *,int,int,char *const []);
typedef int (*print_fn)(const char *,...);
struct command_record {
    uintptr_t name;
    uint32_t maxargs,pad;
    uintptr_t repeat,handler,usage,help,complete;
};
_Static_assert(sizeof(struct command_record)==56,"measured cmd_tbl ABI");
#include "bw_resident_data.h"
static const char command_name[]="bootweb";
#ifdef BW_RESIDENT_NATIVE_SERVICE
static const char command_usage[]="resident Web; updates require native backup and timing gates";
#else
static const char command_usage[]="resident Web development; NAND writes disabled";
#endif
static const char command_help[]="[duration-ms] | trial <host-challenge> <duration-ms>";
#ifdef BW_RESIDENT_NATIVE241_SERVICE
#define BW_RESIDENT_SERVICE_MAX_MS 1800000u
#else
#define BW_RESIDENT_SERVICE_MAX_MS 180000u
#endif
static struct {uintptr_t reloc;struct command_record saved;} run;
static uintptr_t ptr(uintptr_t at) {return *(volatile const uintptr_t *)at;}
static void barrier(void) {__asm__ volatile("dsb sy\n\tisb":::"memory");}
static uint64_t control(void) {uint64_t x;__asm__ volatile("mrs %0,sctlr_el3":"=r"(x));return x;}
static size_t length(const char *s) {size_t n=0;while(s[n])n++;return n;}
#ifdef BW_RESIDENT_DEVICE_ENTROPY
static uint64_t cycles(void *unused) {
    uint64_t x;(void)unused;__asm__ volatile("isb\n\tmrs %0,cntpct_el0":"=r"(x));return x;
}
static uint64_t milliseconds(void *unused) {
    (void)unused;return ((unsigned long (*)(unsigned long))(run.reloc+0x65630))(0);
}
#endif
#ifdef BW_RESIDENT_RESET_ENTRY
#ifndef BW_RESIDENT_DEVICE_ENTROPY
#error "cold Reset Web entry requires device entropy"
#endif
static int reset_pressed(void *unused) {
    uint32_t value;(void)unused;
    value=*(volatile const uint32_t *)0x10d10014u;
    __asm__ volatile("dmb sy":::"memory");return (value&1u)==0;
}
static void reset_poll(void *unused) {
    uint64_t start=milliseconds(0);unsigned polls=0;(void)unused;
    /* Use the already pinned, live-tested timer, not another vendor ABI. */
    while(milliseconds(0)-start<1&&++polls<1000000u){}
}
static int reset_entry(void) {
    const struct bw_entry_io io={0,1,reset_pressed,milliseconds,reset_poll};
#ifdef BW_RESIDENT_RESET_PINCTRL
    volatile uint32_t *const mux=(volatile uint32_t *)0x10e20000u;
    uint32_t saved,configured;int entry;char dtb_hash[65];
    /* Exact field derived from live gpio-ranges + variable-width bank-range,
     * verified against native request (0x523ac) and RMW (0x522e4). Physical
     * acceptance is recorded separately; never change GPIO output/direction. */
    if(*(volatile const uint32_t *)0x10d10000u&1u)return -1;
    bw_sha256((const void *)0x9f6efd40u,0xc09bu,dtb_hash);
    if(!bw_string(dtb_hash,BW_RESIDENT_CONTROL_DTB_SHA256))return -1;
    saved=*mux;configured=(saved&~0x60000000u)|0x20000000u;
    *mux=configured;barrier();
    if(*mux!=configured){*mux=saved;barrier();return -1;}
    entry=bw_reset_entry(&io);
    *mux=saved;barrier();
    return *mux==saved?entry:-1;
#else
    return bw_reset_entry(&io);
#endif
}
#endif
static int identity(uintptr_t gd) {
    unsigned i;char hash[65];uint64_t el;
    __asm__ volatile("mrs %0,currentel":"=r"(el));
    if(el!=12||gd<0x9f6ee080u||gd>0xa0000000u-208)return -1;
    run.reloc=ptr(gd+112);
    if(run.reloc!=0x9ff3c000u||ptr(gd+152)!=0x17f3c000u||
       ptr(0x9fff0010u)!=0x80000711u)return -1;
    for(i=0;i<sizeof(bw_resident_pins)/sizeof(bw_resident_pins[0]);i++) {
        const struct bw_resident_pin *pin=&bw_resident_pins[i];
        bw_sha256((const void *)(run.reloc+pin->offset),pin->length,hash);
        if(!bw_string(hash,pin->sha256))return -1;
    }
    return 0;
}
static int record_matches(const struct command_record *r,int trial) {
    struct command_record expected=bw_original_alias;
    if(trial)expected.handler=0x88100000u;
    else if(r->handler==0x89100000u) {
        expected.name=(uintptr_t)command_name;expected.handler=0x89100000u;
        expected.maxargs=5;expected.usage=(uintptr_t)command_usage;expected.help=(uintptr_t)command_help;
    }
    return bw_equal(r,&expected,sizeof(expected));
}
int bw_resident_dispatch(void *command,int flags,int argc,char *const argv[]) {
    uintptr_t gd;uint64_t cache,duration=BW_RESIDENT_SERVICE_MAX_MS;char digest[65];int cold,native_cold,cold_trial=0,register_trial=0,trial=0,rc=1,web=0;
#ifdef BW_RESIDENT_NATIVE241_SERVICE
    int reboot=0;
#endif
    struct command_record *record;char *inner[3];unsigned char *b;
#ifdef BW_RESIDENT_DEVICE_ENTROPY
    char device_token[65];struct bw_entropy_io entropy={0,cycles,milliseconds,10000};int entropy_rc;
#endif
    (void)flags;
    for(b=__bss_start;b<__bss_end;b++)*b=0;
    __ram_stack_bottom[0]=0x7265736964656e74ULL;
    __asm__ volatile("mov %0,x18":"=r"(gd));
    native_cold=(uintptr_t)command==0x9ff46098u;
    if(identity(gd))return 1;
    print_fn print=(print_fn)(run.reloc+0x6677c);
    record=(struct command_record *)(run.reloc+0x96c50);
    if((uintptr_t)command!=(native_cold?0x9ff46098u:(uintptr_t)record))return 1;
    if(!native_cold) {
        if(!argv)return 1;
        cold_trial=argc==2&&bw_string(argv[1],"coldtrial");
        register_trial=argc==2&&bw_string(argv[1],"registertrial");
        trial=argc==4&&bw_string(argv[1],"trial");
        if(trial) {
            if(!bw_hex64(argv[2])||bw_decimal((const uint8_t *)argv[3],length(argv[3]),&duration))return 1;
        } else if(!cold_trial&&!register_trial) {
            if(argc!=1&&(argc!=2||bw_decimal((const uint8_t *)argv[1],length(argv[1]),&duration)))return 1;
#ifndef BW_RESIDENT_DEVICE_ENTROPY
            return 1; /* A resident serial command never accepts host fallback RNG. */
#endif
        }
        if(duration<1000||duration>BW_RESIDENT_SERVICE_MAX_MS)return 1;
    }
    cold=native_cold||cold_trial||register_trial;
    if(!record_matches(record,!native_cold&&(uintptr_t)record->handler==0x88100000u))return 1;
    run.saved=*record;
    cache=control();
    /* Preserve either the measured normal (M+C) state or MMU/cache-off RAM
     * trial state. A mixed/unmeasured state is not silently normalized. */
    if((cache&5)!=0&&(cache&5)!=5)return 1;
    print("BW_RESIDENT_BOOTSTRAP_ENTRY v=1 cold=%d equivalent=%d gd=%lx cache=%llx payload=%u uptime_ms=%lu\n",
          cold,cold_trial,(unsigned long)gd,(unsigned long long)cache,(unsigned)sizeof(bw_resident_payload),
          ((unsigned long (*)(unsigned long))(run.reloc+0x65630))(0));
    web=!cold;
#ifdef BW_RESIDENT_DEVICE_ENTROPY
#ifdef BW_RESIDENT_RESET_ENTRY
    if(cold&&!register_trial) {
        int entry=reset_entry();
        print("BW_RESIDENT_RESET entry=%d raw_bit=%u hold_ms=3000 gpio_written=0\n",entry,
              *(volatile const uint32_t *)0x10d10014u&1u);
        if(entry<0)return 1;
        web=entry==1;
    }
#endif
    if(web) {
        entropy_rc=bw_entropy_token(&entropy,device_token);
        if(entropy_rc) {print("BW_RESIDENT_ENTROPY rc=%d web_entry_denied=1\n",entropy_rc);return 1;}
        bw_sha256(device_token,64,digest);
        print("BW_RESIDENT_ENTROPY rc=0 bytes=32 source=CNTPCT_JENT_SHA3 startup_rct_apt=1 token_sha256=%s\n",digest);
        print("BW_RESIDENT_SESSION token=%s\n",device_token);
    }
#endif
    if(cache&4)((void (*)(void))(run.reloc+0x1420))();
    if(cache&0x1000)((void (*)(void))(run.reloc+0x14dc))();
    if(control()&0x1005)goto restore_cache;
    bw_sha256(bw_resident_payload,sizeof(bw_resident_payload),digest);
    if(!bw_string(digest,BW_RESIDENT_PAYLOAD_SHA256))goto restore_cache;
    bw_copy((void *)0x89000000u,bw_resident_payload,sizeof(bw_resident_payload));
    bw_sha256((const void *)0x89000000u,sizeof(bw_resident_payload),digest);
    if(!bw_string(digest,BW_RESIDENT_PAYLOAD_SHA256))goto restore_cache;
    /* Both caches are disabled. Invalidate the stale instruction-side state
     * with the pinned native all-I-cache primitive, then publish cmd_tbl. */
    ((void (*)(void))(run.reloc+0x157c))();
    record->name=(uintptr_t)command_name;record->maxargs=5;
    record->usage=(uintptr_t)command_usage;record->help=(uintptr_t)command_help;
    record->handler=(uintptr_t)bw_resident_command;barrier();
    print("BW_RESIDENT_REGISTERED handler=%lx payload_sha256=%s reset_verified=%d native_service=%d\n",
          (unsigned long)record->handler,digest,
#ifdef BW_RESIDENT_RESET_ENTRY
          BW_RESIDENT_RESET_VERIFIED
#else
          0
#endif
          ,
#ifdef BW_RESIDENT_NATIVE_SERVICE
          1
#else
          0
#endif
          );
    if(!web) {
        /* Development hook does not pretend Reset/RNG acceptance has passed.
         * Normal boot continues on the ORIGINAL vendor stack below. */
        rc=0;
    } else {
        inner[0]=(char *)command_name;
#ifdef BW_RESIDENT_DEVICE_ENTROPY
        inner[1]=device_token;
#else
        inner[1]=trial?argv[2]:0;
#endif
        {
            static char duration_text[8];unsigned n=0,j;uint64_t value=duration;
            do {duration_text[n++]=(char)('0'+value%10);value/=10;}while(value);
            for(j=0;j<n/2;j++){char t=duration_text[j];duration_text[j]=duration_text[n-j-1];duration_text[n-j-1]=t;}
            duration_text[n]=0;inner[2]=duration_text;
        }
        rc=((int (*)(void *,int,int,char *const []))0x89000000u)(record,0,3,inner);
#ifdef BW_RESIDENT_NATIVE241_SERVICE
        if(rc==BW_ENTRY_REQUEST_REBOOT){reboot=1;rc=0;}
#endif
        /* The entire record, not only its handler, belongs to the host trial. */
        if(!cold) {
            *record=run.saved;barrier();
            if(!bw_equal(record,&run.saved,sizeof(run.saved)))rc=1;
        }
    }
restore_cache:
    if(cache&0x1000)((void (*)(void))(run.reloc+0x1590))();
    if(cache&4)((void (*)(void))(run.reloc+0x1374))();
    if(control()!=cache||__ram_stack_bottom[0]!=0x7265736964656e74ULL)rc=1;
    print("BW_RESIDENT_BOOTSTRAP_EXIT rc=%d cold=%d cache_restored=%d record_restored=%d stack_ok=%d\n",
          rc,cold,control()==cache,cold?0:bw_equal(record,&run.saved,sizeof(run.saved)),
          __ram_stack_bottom[0]==0x7265736964656e74ULL);
#ifdef BW_RESIDENT_NATIVE241_SERVICE
    if(reboot&&!rc) {
        struct command_record *reset=(struct command_record *)(run.reloc+0x96c88);
        char *reset_argv[]={(char *)"reset"};
        if(identity(gd)||!bw_equal(reset,&bw_reset_record,sizeof(*reset)))return 1;
        print("BW_RESIDENT_MANUAL_REBOOT cleanup_pass=1 invoking_verified_reset_command=1\n");
        ((int (*)(void *,int,int,char *const []))reset->handler)(reset,0,1,reset_argv);
        return 1; /* native reset returning is NEVER reported as success */
    }
#endif
    return cold_trial&&!rc?2:rc?1:0;
}
/* No absolute pointers or literal pools in executable sections. x18 and all
 * AAPCS64 callee-saved registers survive; payload has its OWN separate stack. */
__asm__(".section .text.entry,\"ax\"\n"
        ".balign 4\n.global bw_resident_command\n.type bw_resident_command,%function\n"
        "bw_resident_command:\nmov x9,sp\nmovz w10,#0x8920,lsl #16\nmov sp,x10\n"
        "stp x29,x30,[sp,#-48]!\nstr x9,[sp,#16]\n"
        "movz w11,#0x6098\nmovk w11,#0x9ff4,lsl #16\ncmp x0,x11\ncset x11,eq\nstr x11,[sp,#24]\n"
        "bl bw_resident_dispatch\nldr x11,[sp,#24]\nldr x9,[sp,#16]\n"
        "ldp x29,x30,[sp],#48\nmov sp,x9\ncbnz x11,1f\ncmp w0,#2\nb.eq 1f\nret\n"
        /* Equivalent to the replaced run_main_loop, on the vendor stack. */
        "1: stp x29,x30,[sp,#-16]!\nmov x29,sp\n"
        "2: movz w16,#0x5084\nmovk w16,#0x9ff4,lsl #16\nblr x16\nb 2b\n"
        ".size bw_resident_command,.-bw_resident_command\n.previous\n");
