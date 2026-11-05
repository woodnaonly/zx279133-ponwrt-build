/* SPDX-License-Identifier: GPL-2.0-only
 * Distinct RAM installer, NEVER a resident Web image. PREFLIGHT physically
 * emulates every mutation. The real target additionally requires an exact,
 * one-use host authorization compiled from accepted final-candidate evidence.
 */
#include "install_image.h"
#include "resident_image.h"
#include "native_nand.h"
#include "internal.h"
extern uint8_t __bss_start[],__bss_end[];
extern uint64_t __ram_stack_bottom[];
struct bw_install_pin {unsigned offset,length;const char *sha256;};
#include "bw_install_data.h"
#ifndef BW_INSTALL_CONTINUOUS_POWER
#define BW_INSTALL_CONTINUOUS_POWER 0
#endif
static struct bw_native_nand nand;
static struct bw_port port;
static uintptr_t reloc;
#ifdef BW_NATIVE_INSTALL
static int authorized;
#endif
static size_t package_bytes;
static uint32_t max_hash_ms;
static _Alignas(64) uint8_t scratch[BW_ERASE_SIZE];
static uint8_t *const package=(uint8_t *)0x8a000000u;
static uint8_t *const recovery=(uint8_t *)0x8a200000u;
static uintptr_t ptr(uintptr_t at) {return *(volatile const uintptr_t *)at;}
static uint64_t now(void *unused) {(void)unused;return ((unsigned long (*)(unsigned long))(reloc+0x65630))(0);}
#define PRINT(...) ((int (*)(const char *,...))(reloc+0x6677c))(__VA_ARGS__)
static int identity(void) {
    uintptr_t gd;uint64_t el,cache;unsigned i;char digest[65];
    __asm__ volatile("mov %0,x18\n\tmrs %1,currentel\n\tmrs %2,sctlr_el3":"=r"(gd),"=r"(el),"=r"(cache));
    if(el!=12||(cache&0x1005)||gd<0x9f6ee080u||gd>0xa0000000u-208)return -1;
    reloc=ptr(gd+112);
    if(reloc!=0x9ff3c000u||ptr(gd+152)!=0x17f3c000u||ptr(0x9fff0010u)!=0x80000711u)return -1;
    for(i=0;i<sizeof(bw_install_pins)/sizeof(bw_install_pins[0]);i++) {
        const struct bw_install_pin *pin=&bw_install_pins[i];
        bw_sha256((const void *)(reloc+pin->offset),pin->length,digest);
        if(!bw_string(digest,pin->sha256))return -1;
    }
    /* The original vendor disables all four watchdogs before this entry. */
    if((*(volatile const uint32_t *)0x14f0901cu|*(volatile const uint32_t *)0x14f0a01cu|
        *(volatile const uint32_t *)0x14f0b01cu|*(volatile const uint32_t *)0x14f0c01cu)&0xffffu)return -1;
    return 0;
}
static int hash_range(uint32_t off,uint32_t bytes,char out[65]) {
    struct bw_hash h;bw_hash_init(&h);
    while(bytes) {
        uint32_t n=bytes>BW_ERASE_SIZE?BW_ERASE_SIZE:bytes;
        if(port.read(port.context,off,scratch,n))return -1;
        {uint64_t start=now(0),elapsed;bw_hash_add(&h,scratch,n);elapsed=now(0)-start;
         if(elapsed>max_hash_ms)max_hash_ms=(uint32_t)elapsed;}
        off+=n;bytes-=n;
    }
    bw_hash_end(&h,out);return 0;
}
static int protected_hashes(const char *phase) {
    static const uint32_t offsets[4]={0,0x180000,0x1a0000,0xb00000};
    static const uint32_t lengths[4]={0x20000,0x20000,0x60000,0x400000};
    unsigned i;char digest[65];int rc=0;
    for(i=0;i<4;i++) {
        if(hash_range(offsets[i],lengths[i],digest)){PRINT("BW_INSTALL_PROTECTED phase=%s off=%x read_failed=1\n",phase,offsets[i]);rc=-1;continue;}
        PRINT("BW_INSTALL_PROTECTED phase=%s off=%x bytes=%x sha256=%s matches_backup=%d\n",phase,offsets[i],lengths[i],digest,bw_string(digest,bw_install_protected[i]));
        if(!bw_string(digest,bw_install_protected[i]))rc=-1;
    }
    return rc;
}
static int verify_boot(void *unused,const void *image,size_t n) {
    char digest[65];(void)unused;
    return bw_resident_image_check(image,n,digest)==1&&bw_string(digest,bw_install_parts.candidate_sha256)?0:-1;
}
static int verify_identity(void *unused,const struct bw_identity *id) {
    char digest[65];(void)unused;
    return bw_identity_valid(id)&&bw_string(id->installed_sha256,bw_install_parts.original_sha256)&&
        bw_resident_image_check(recovery,BW_STAGE2_SIZE,digest)>=0&&bw_string(digest,id->installed_sha256)?0:-1;
}
static void budget_limits(struct bw_uboot_budget *b) {
    bw_zero(b,sizeof(*b));
    /* Native get_timer(0) is directly derived from the cold CNTPCT counter.
     * The UART owner independently binds it to this observed natural cycle. */
    b->power_started_ms=0;b->power_uncertainty_ms=2000;
    /* Development conservative ledger, NOT a timing certificate. The first
     * live preflight showed that 50ms/hash was too small with caches off.
     * Fixed overhead includes this wrapper's EXTRA post-engine 37-block audit
     * (37*(3000+500)ms), plus validation/identity/authorization RAM hashes.
     * Exact-build host timing evidence is still required before REAL builds. */
    b->erase_max_ms=2100;b->page_max_ms=300;b->read_max_ms=3000;b->hash_max_ms=500;
    b->bad_query_max_ms=100;b->service_max_ms=25;b->fixed_max_ms=180000;
    b->power_policy=BW_INSTALL_CONTINUOUS_POWER?BW_POWER_CONTINUOUS_CONFIRMED:BW_POWER_PERIODIC20M;
}
static int prepare(void *unused,const struct bw_identity *id,const void *image,size_t n,struct bw_uboot_budget *b) {
    char digest[65];(void)unused;
    if(verify_identity(0,id)||verify_boot(0,image,n))return -1;
    bw_sha256(package,package_bytes,digest);
    if(!bw_string(digest,bw_install_parts.package_sha256))return -1;
    budget_limits(b);return 0;
}
#ifdef BW_NATIVE_INSTALL
static int authorize(void *unused,const struct bw_identity *id,const char *digest) {
    (void)unused;
    if(authorized||verify_identity(0,id)||!bw_string(digest,bw_install_parts.package_sha256))return -1;
    authorized=1;PRINT("BW_INSTALL_AUTHORIZATION consumed_once=1 rescue_verified=0 package_sha256=%s\n",digest);return 0;
}
#endif
static void progress(void *unused,const char *phase,uint32_t done,uint32_t total) {
    (void)unused;PRINT("BW_INSTALL_PROGRESS phase=%s done=%u total=%u uptime_ms=%llu\n",phase,done,total,(unsigned long long)now(0));
}
int bw_installer_entry(int argc,char *const argv[]) {
    uint8_t *b;uint32_t off;struct bw_identity id;struct bw_result result;struct bw_uboot_budget budget;
    char error[BW_ERROR_SIZE]={0};uint64_t bound=0,start;int rc=1,bound_port=0;
#ifndef BW_NATIVE_INSTALL
    uint32_t page;char digest[65];
#endif
    for(b=__bss_start;b<__bss_end;b++)*b=0;__ram_stack_bottom[0]=0x696e7374616c6c31ULL;
    if(identity())return 1;
#ifdef BW_NATIVE_INSTALL
    if(argc!=(BW_INSTALL_CONTINUOUS_POWER?4:3)||!argv||!bw_string(argv[1],"install")||
       !bw_string(argv[2],BW_INSTALL_AUTHORIZATION)||
       (BW_INSTALL_CONTINUOUS_POWER&&!bw_string(argv[3],"continuous-power")))return 1;
#else
    if(argc!=2||!argv||!bw_string(argv[1],"preflight"))return 1;
#endif
    start=now(0);
    if(!BW_INSTALL_CONTINUOUS_POWER&&start>=240000u){PRINT("BW_INSTALL_REFUSED reason=cycle_too_old uptime_ms=%llu\n",(unsigned long long)start);return 1;}
    PRINT("BW_INSTALL_POWER mode=%s bounded_io_required=1 periodic_budget_proof=0\n",BW_INSTALL_CONTINUOUS_POWER?"continuous_confirmed":"periodic20m");
    PRINT("BW_INSTALL_ENTRY mode=%s uptime_ms=%llu original_ram=8a200000 package_ram=8a000000 candidate_sha256=%s\n",
#ifdef BW_NATIVE_INSTALL
          "real",
#else
          "preflight",
#endif
          (unsigned long long)start,bw_install_parts.candidate_sha256);
    if(bw_native_nand_bind(&nand,reloc,ptr(reloc+0xac580),&port))goto end;
    bound_port=1;
    for(off=0;off<BW_STAGE2_SIZE;off+=BW_ERASE_SIZE) {
        if(port.is_bad(port.context,BW_STAGE2_START+off)!=0||port.read(port.context,BW_STAGE2_START+off,recovery+off,BW_ERASE_SIZE))goto end;
    }
    if(bw_install_prepare(&bw_install_parts,recovery,BW_STAGE2_SIZE,package,0x200000,&package_bytes,error)) {
        PRINT("BW_INSTALL_REFUSED reason=%s\n",error);goto end;
    }
    bw_copy(id.vendor_abi_sha256,BW_VENDOR_ABI_SHA256,65);bw_copy(id.installed_sha256,bw_install_parts.original_sha256,65);
#ifdef BW_NATIVE_INSTALL
    /* The real core below performs full package, container, RAM recovery,
     * identity and prepare checks again, and rechecks time at first erase.
     * Do not spend the scarce cold-power window duplicating those checks
     * here. The independent saved-backup comparison remains before it. */
    (void)result;budget_limits(&budget);
    if(protected_hashes("before")||bw_uboot_budget_check(&budget,now(0),&bound,error)) {
        PRINT("BW_INSTALL_REFUSED reason=preflight_or_budget detail=%s\n",error);goto end;
    }
#else
    if(bw_validate_identity(package,package_bytes,&id,0,&result,error)||protected_hashes("before")||
       prepare(0,&id,package+result.writes[0].data_offset,BW_STAGE2_SIZE,&budget)||
       bw_uboot_budget_check(&budget,now(0),&bound,error)) {PRINT("BW_INSTALL_REFUSED reason=preflight_or_budget detail=%s\n",error);goto end;}
#endif
    PRINT("BW_INSTALL_RAM_READY original_sha256=%s candidate_sha256=%s package_sha256=%s package_bytes=%lu bound_ms=%llu uptime_ms=%llu\n",
          id.installed_sha256,bw_install_parts.candidate_sha256,bw_install_parts.package_sha256,(unsigned long)package_bytes,
          (unsigned long long)bound,(unsigned long long)now(0));
    port.verify_boot=verify_boot;port.verify_identity=verify_identity;port.prepare_uboot_write=prepare;port.milliseconds=now;port.progress=progress;
#ifdef BW_NATIVE_INSTALL
    /* These are host-accepted, exact-candidate facts, checked again by the
     * native ABI, RAM candidate, original recovery and timing gates above.
     * SERIAL_RESCUE is deliberately NOT fabricated. */
    port.capabilities=BW_REQUIRED|BW_CAP_BOOT_CONTAINER;port.authorize_no_rescue=authorize;
    rc=bw_execute_identity(package,package_bytes,&id,0,&port,scratch,sizeof(scratch),error);
    PRINT("BW_INSTALL_EXECUTE rc=%d error=%s\n",rc,error);
    if(!rc)PRINT("BW_INSTALL_READBACK sha256=%s bytes=%u compared_by_core=1\n",bw_install_parts.candidate_sha256,BW_STAGE2_SIZE);
#else
    for(off=BW_STAGE2_START;off<BW_STAGE2_END;off+=BW_ERASE_SIZE) {
        if(port.erase(port.context,off))goto end;
        for(page=0;page<BW_ERASE_SIZE;page+=BW_PAGE_SIZE)
            if(port.write_page(port.context,off+page,package+result.writes[0].data_offset+off-BW_STAGE2_START+page))goto end;
    }
    if(hash_range(BW_STAGE2_START,BW_STAGE2_SIZE,digest)||!bw_string(digest,id.installed_sha256))goto end;
    PRINT("BW_INSTALL_PREFLIGHT_CURRENT sha256=%s unchanged=1 candidate_readback_claimed=0\n",digest);rc=0;
#endif
    if(protected_hashes("after"))rc=1;
end:
    if(bound_port&&bw_native_nand_release(&nand))rc=1;
    if(__ram_stack_bottom[0]!=0x696e7374616c6c31ULL)rc=1;
    PRINT("BW_INSTALL_TIMING max_hash_ms=%u proof_claimed=0\n",max_hash_ms);
    PRINT("BW_INSTALL_EXIT rc=%d elapsed_ms=%llu stack_ok=%d authorization_consumed=%d\n",rc,(unsigned long long)(now(0)-start),
          __ram_stack_bottom[0]==0x696e7374616c6c31ULL,
#ifdef BW_NATIVE_INSTALL
          authorized
#else
          0
#endif
          );
    return rc?1:0;
}
__asm__(".section .text.entry,\"ax\"\n.balign 4\n.global bw_installer_command\n.type bw_installer_command,%function\n"
        "bw_installer_command:\nmov x9,sp\nadrp x10,__ram_stack_top\nadd x10,x10,:lo12:__ram_stack_top\nmov sp,x10\n"
        "stp x29,x30,[sp,#-32]!\nstr x9,[sp,#16]\nmov w0,w2\nmov x1,x3\nbl bw_installer_entry\n"
        "ldr x9,[sp,#16]\nldp x29,x30,[sp],#32\nmov sp,x9\nret\n.size bw_installer_command,.-bw_installer_command\n.previous\n");
