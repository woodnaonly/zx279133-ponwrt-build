/* SPDX-License-Identifier: GPL-2.0-only
 * Distinct permanent-service adapter. Never compiled as the immutable RAM
 * read-only payload. The native ECC/SPI modules own every physical mutation.
 */
#if !defined(BW_RESIDENT_NATIVE_WRITE) || !defined(BW_NATIVE_INSTALL) || defined(BW_RAM_READONLY)
#error "resident adapter requires its distinct native write target"
#endif
#include "net_bridge.h"
#include "native_nand.h"
#include "native_spi.h"
#include "resident_image.h"
#include "internal.h"
#include <string.h>
struct bw_pin {uint32_t offset,length;const char *sha256;};
#include "bw_ram_pins.h"
#include "bw_native_timing.h"
#ifndef BW_NATIVE_CONTINUOUS_POWER
#define BW_NATIVE_CONTINUOUS_POWER 0
#endif
#define BW_RESIDENT_SERVICE_MAX_MS (BW_NATIVE_CONTINUOUS_POWER?1800000u:180000u)
static struct {
    uintptr_t reloc,device,mtd,old_rx;
    struct bw_platform *platform;
    struct bw_native_nand native;
    struct bw_port io;
    struct bw_result prepared_result;
    uint64_t end;
    uint32_t random,old_state,old_phy[2],rx,tx;
    int attached,bound,network,hooked,failed,backup_valid,accepted,prepared,writing,succeeded;
    void (*receive)(void *,const void *,size_t);
    void *receive_arg;
    uint8_t driver[0x1e8];
    char boot_sha256[65],tags_sha256[65],installed_sha256[65],prepared_package[65];
    _Alignas(64) uint8_t scratch[BW_ERASE_SIZE];
} resident;
/* This reservation is disjoint from the payload/stack, dispatcher/stack,
 * identity workspace, upload buffer and the measured vendor/DMA regions. */
static uint8_t *const snapshot=(uint8_t *)0x89400000u;
#define PRINT(...) ((int (*)(const char *,...))(resident.reloc+0x6677c))(__VA_ARGS__)
static uintptr_t ptr(uintptr_t at) {return *(volatile const uintptr_t *)at;}
static uint32_t word(uintptr_t at) {return *(volatile const uint32_t *)at;}
static uint64_t now(void *unused) {(void)unused;return ((unsigned long (*)(unsigned long))(resident.reloc+0x65630))(0);}
static int address(uintptr_t at,size_t n) {return n&&at>=0x9f6ee080u&&at<=0xa0000000u-n;}
static int stop(void *unused) {
    (void)unused;
    if(resident.writing)return 0;
    if(resident.platform&&resident.platform->requested_reboot)return 1;
    /* Once a continuous-power task is accepted (or fails preparation), keep
     * its locked result page available. Returning from a cold Reset service
     * would resume the vendor autoboot path, including after a partial write.
     * Success waits for MANUAL reboot; failure never auto-boots/retries. */
    if(BW_NATIVE_CONTINUOUS_POWER&&(resident.accepted||resident.prepared||resident.failed))return 0;
    return now(0)>=resident.end||(!BW_NATIVE_CONTINUOUS_POWER&&now(0)>=1020000u);
}
static int watchdog(void) {
    return (word(0x14f0901cu)|word(0x14f0a01cu)|word(0x14f0b01cu)|word(0x14f0c01cu))&0xffffu?-1:0;
}
static int pins(uintptr_t reloc) {
    unsigned i;char digest[65];
    for(i=0;i<sizeof(bw_ram_pins)/sizeof(bw_ram_pins[0]);i++) {
        const struct bw_pin *pin=&bw_ram_pins[i];
        if(pin->offset>0xb0000||pin->length>0xb0000-pin->offset)return -1;
        bw_sha256((const void *)(reloc+pin->offset),pin->length,digest);
        if(!bw_string(digest,pin->sha256))return -1;
    }
    return 0;
}
static int bad(void *unused,uint32_t off) {(void)unused;return resident.io.is_bad(resident.io.context,off);}
static int read_ecc(void *unused,uint32_t off,void *out,size_t n) {
    uint32_t block;(void)unused;
    if(!n||n>BW_ERASE_SIZE||off>=BW_FLASH_SIZE||n>BW_FLASH_SIZE-off||stop(0))return -1;
    for(block=off&~(BW_ERASE_SIZE-1u);block<off+n;block+=BW_ERASE_SIZE)
        if(bad(0,block)!=0)return -1;
    return resident.io.read(resident.io.context,off,out,n);
}
static int capture(uint32_t off,uint32_t bytes,uint8_t *out,char hash[65]) {
    struct bw_hash h;uint32_t at;bw_hash_init(&h);
    for(at=0;at<bytes;at+=BW_ERASE_SIZE) {
        if(read_ecc(0,off+at,out+at,BW_ERASE_SIZE))return -1;
        bw_hash_add(&h,out+at,BW_ERASE_SIZE);
    }
    bw_hash_end(&h,hash);return 0;
}
static int compare(uint32_t off,uint32_t bytes,const uint8_t *expected) {
    uint32_t at;
    for(at=0;at<bytes;at+=BW_ERASE_SIZE)
        if(read_ecc(0,off+at,resident.scratch,BW_ERASE_SIZE)||!bw_equal(resident.scratch,expected+at,BW_ERASE_SIZE))return -1;
    return 0;
}
static int request_reboot(void *unused) {
    (void)unused;
    if(!resident.succeeded||resident.failed||resident.writing||!resident.prepared||!bw_native_spi_ready())return -1;
    resident.platform->requested_reboot=1;
    PRINT("BW_RESIDENT_MANUAL_REBOOT requested=1 deferred_until_native_and_cache_restore=1\n");return 0;
}
static int verify_identity(void *unused,const struct bw_identity *id) {
    uint8_t *image=(uint8_t *)0x89200000u;uint32_t off;char digest[65];int kind;(void)unused;
    if(!bw_identity_valid(id))return -1;
    for(off=0;off<BW_STAGE2_SIZE;off+=BW_ERASE_SIZE)
        if(read_ecc(0,BW_STAGE2_START+off,image+off,BW_ERASE_SIZE))return -1;
    kind=bw_resident_image_check(image,BW_STAGE2_SIZE,digest);
    if(kind<0||!bw_string(digest,id->installed_sha256))return -1;
    resident.platform->resident_version=kind==1?bw_resident_image_version(image,BW_STAGE2_SIZE):0;
    resident.platform->reboot=resident.platform->resident_version&&
        bw_string(resident.platform->resident_version,"bootweb-resident-native241-v1")?request_reboot:0;
    bw_copy(resident.installed_sha256,digest,65);
    PRINT("BW_INSTALLED_IDENTITY resident=%d vendor_abi=%s installed_sha256=%s\n",kind,BW_VENDOR_ABI_SHA256,digest);
    return 0;
}
static int verify_boot(void *unused,const void *image,size_t bytes) {
    char digest[65];(void)unused;return bw_resident_image_check(image,bytes,digest)<0?-1:0;
}
static void budget(struct bw_uboot_budget *b) {
    bw_zero(b,sizeof(*b));b->power_uncertainty_ms=2000;
    b->erase_max_ms=BW_NATIVE_ERASE_MAX_MS;b->page_max_ms=BW_NATIVE_PAGE_MAX_MS;
    b->read_max_ms=BW_NATIVE_READ_MAX_MS;b->hash_max_ms=BW_NATIVE_HASH_MAX_MS;
    b->bad_query_max_ms=BW_NATIVE_BAD_MAX_MS;b->service_max_ms=BW_NATIVE_SERVICE_MAX_MS;
    b->fixed_max_ms=BW_NATIVE_FIXED_MAX_MS;
}
static int prepare_boot(void *unused,const struct bw_identity *id,const void *image,size_t n,struct bw_uboot_budget *b) {
    char digest[65];(void)unused;
    if(!BW_NATIVE_TIMING_PROVEN||!resident.backup_valid||verify_identity(0,id)||verify_boot(0,image,n))return -1;
    bw_sha256(snapshot,0x200000,digest);if(!bw_string(digest,resident.boot_sha256))return -1;
    bw_sha256(snapshot+0x200000,0x400000,digest);if(!bw_string(digest,resident.tags_sha256))return -1;
    if(!bw_equal(snapshot+BW_STAGE2_START,(const void *)0x89200000u,BW_STAGE2_SIZE))return -1;
    budget(b);return 0;
}
static int system_bound(const struct bw_result *r,uint64_t *out) {
    struct bw_uboot_budget b;char error[BW_ERROR_SIZE];uint64_t total;
    budget(&b);b.fixed_max_ms+=2000;
    if(bw_system_budget_bound(r,&b,&total,error))return -1;
    *out=total;
    if(BW_NATIVE_CONTINUOUS_POWER)return 0;
    return now(0)+2000u<300000u&&total<900000u-now(0)?0:-1;
}
static const char *update_lock(void *unused,const struct bw_result *r) {
    uint64_t bound;(void)unused;
    if(resident.failed||resident.native.failed||bw_native_spi_failed())return "原生 ECC 或控制器错误，本次会话禁止重试";
    if(!resident.backup_valid)return "缺少当前 boot 和原厂参数的 ECC 备份";
    if(resident.prepared)return "本次会话已有执行任务，禁止再次写入";
    if(r->kind==BW_UBOOT)return "本版本仅开放系统更新，网页自刷 U-Boot 未解锁";
    if(!BW_NATIVE_CONTINUOUS_POWER&&!BW_NATIVE_TIMING_PROVEN)return "Complete native timing bound has not been verified for this build";
    if(!bw_native_nand_system_ready(&resident.native))return "原生 NAND 有界超时、地址门禁或身份未就绪";
    if(r->kind!=BW_SYSTEM)return "请先完整上传并校验系统包";
    if(system_bound(r,&bound))return "系统范围不匹配或超出定时供电预算";
    resident.platform->update_bound_ms=bound;
    return "";
}
static int prepare_update(void *unused,const struct bw_task *task,char error[BW_ERROR_SIZE]) {
    const char *reason;char digest[65];(void)unused;
    reason=update_lock(0,&task->result);if(reason[0])return bw_fail(error,reason);
    if(resident.prepared)return bw_fail(error,"native operation already prepared; no retry");
    if(BW_NATIVE_CONTINUOUS_POWER&&!task->continuous_power_confirmed)return bw_fail(error,"缺少本任务的持续供电确认");
    /* The HTTP task is already authenticated and QUEUED. Latch its lifetime
     * BEFORE hashing/backup comparison, not only before the first erase.
     * Native ECC/controller per-call deadlines remain enforced independently. */
    if(BW_NATIVE_CONTINUOUS_POWER)resident.accepted=1;
    bw_sha256(task->buffer,task->received,digest);
    if(!bw_string(digest,task->result.package_sha256))return bw_fail(error,"frozen package changed");
    if(!bw_string(task->base,resident.installed_sha256))return bw_fail(error,"confirmed installed identity changed");
    if(compare(0,0x200000,snapshot)||compare(0xb00000,0x400000,snapshot+0x200000))return bw_fail(error,"current backup no longer matches ECC flash");
    /* Recheck after all preparation; no deadline is tested mid-write. */
    reason=update_lock(0,&task->result);if(reason[0])return bw_fail(error,reason);
    bw_copy(resident.prepared_package,digest,65);resident.prepared=1;resident.writing=1;
    resident.prepared_result=task->result;
    PRINT("BW_RESIDENT_SYSTEM_PREPARED layout=native241 power_mode=%s periodic_timing_verified=%d bound_ms=%llu package_sha256=%s\n",
          BW_NATIVE_CONTINUOUS_POWER?"continuous_required":"periodic20m",BW_NATIVE_TIMING_PROVEN,
          (unsigned long long)resident.platform->update_bound_ms,digest);
    return 0;
}
static int erase(void *unused,uint32_t off) {
    (void)unused;if(!resident.prepared)return -1;
    resident.writing=1;return resident.io.erase(resident.io.context,off);
}
static int page(void *unused,uint32_t off,const void *data) {
    (void)unused;return resident.writing?resident.io.write_page(resident.io.context,off,data):-1;
}
static void finish_update(void *unused,int success) {
    (void)unused;resident.writing=0;
    /* A failed erase/write must not permit another task in this session. */
    if(!success)resident.failed=1;
    resident.succeeded=success;resident.end=now(0)+180000u;
    PRINT("BW_RESIDENT_TASK_FINISHED success=%d further_writes_locked=1 automatic_boot=0 manual_reboot_allowed=%d\n",success,success);
}
static void progress(void *unused,const char *phase,uint32_t done,uint32_t total) {
    (void)unused;PRINT("BW_RESIDENT_PROGRESS phase=%s done=%u total=%u uptime_ms=%llu\n",phase,done,total,(unsigned long long)now(0));
    if((bw_string(phase,"image-verified")||bw_string(phase,"tail-verified"))&&done&&done<=resident.prepared_result.count) {
        const struct bw_span *s=&resident.prepared_result.writes[done-1];
        PRINT("BW_RESIDENT_VERIFIED phase=%s part=%s offset=%x capacity=%u length=%llu sha256=%s compared_by_core=1\n",
              phase,s->name,s->offset,s->capacity,(unsigned long long)s->length,s->sha256);
    }
    if(bw_string(phase,"protected-audit"))PRINT("BW_RESIDENT_PROTECTED boot_bytes=2097152 boot_sha256=%s tags_sha256=%s unchanged=1 compared_by_core=1\n",
        resident.boot_sha256,resident.tags_sha256);
    /* finish_update owns the accepted-task lifetime, including its final
     * audit. Do not expire the service between completion and HTTP status. */
}
static uint32_t random32(void *unused) {
    uint32_t x=resident.random;(void)unused;x^=x<<13;x^=x>>17;x^=x<<5;resident.random=x;return x;
}
static void receive(const void *data,int n) {
    if(resident.receive&&!stop(0)&&n>=14&&n<=1536){resident.rx++;resident.receive(resident.receive_arg,data,(size_t)n);}
}
static int network_start(void *unused) {
    int rc;(void)unused;
    if(stop(0)||resident.network||ptr(resident.reloc+0xad520)!=resident.old_rx)return -1;
    rc=((int (*)(void))(resident.reloc+0x67e00))();if(rc<0)return -1;
    resident.network=1;
    if(ptr(resident.reloc+0xb1938)!=resident.device||ptr(resident.reloc+0xad520)!=resident.old_rx)return -1;
    *(volatile uintptr_t *)(resident.reloc+0xad520)=(uintptr_t)receive;
    __asm__ volatile("dsb sy\n\tisb":::"memory");resident.hooked=1;
    PRINT("BW_RESIDENT_NETWORK_STARTED ip=192.168.9.1 native_write_adapter=1\n");return 0;
}
static int send_frame(void *unused,const void *data,size_t n) {
    int rc;(void)unused;if(stop(0)||!resident.network||n<14||n>1536)return -1;
    rc=((int (*)(void *,const void *,int))(resident.reloc+0x36dac))((void *)resident.device,data,(int)n);
    if(rc>=0)resident.tx++;return rc;
}
static int poll_frames(void *unused,void (*fn)(void *,const void *,size_t),void *arg) {
    int rc;(void)unused;if(stop(0)||!resident.network)return -1;
    resident.receive=fn;resident.receive_arg=arg;rc=((int (*)(void))(resident.reloc+0x37364))();
    resident.receive=0;resident.receive_arg=0;return rc;
}
int bw_vendor_attach(struct bw_platform *p,int argc,char *const argv[]) {
    uintptr_t gd,reloc;uint64_t duration,el,cache;unsigned i;struct bw_ethernet eth;
    __asm__ volatile("mov %0,x18\n\tmrs %1,currentel\n\tmrs %2,sctlr_el3":"=r"(gd),"=r"(el),"=r"(cache));
    if(!p||argc!=3||!argv||!bw_hex64(argv[1])||bw_decimal((const uint8_t *)argv[2],strlen(argv[2]),&duration)||
       duration<1000||duration>BW_RESIDENT_SERVICE_MAX_MS||!address(gd,208)||el!=12||(cache&0x1005))return -1;
    reloc=ptr(gd+112);
    if(reloc!=0x9ff3c000u||ptr(gd+152)!=0x17f3c000u||ptr(0x9fff0010u)!=0x80000711u||pins(reloc))return -1;
    resident.reloc=reloc;resident.platform=p;resident.device=ptr(reloc+0xb1938);resident.mtd=ptr(reloc+0xac580);
    if(!address(resident.device,128)||ptr(resident.device+48)!=reloc+0x36f88||ptr(resident.device+56)!=reloc+0x36dac||
       ptr(resident.device+64)!=reloc+0x37364||ptr(resident.device+72)!=reloc+0x36fdc||watchdog())return -1;
    resident.old_rx=ptr(reloc+0xad520);resident.old_state=word(resident.device+40);
    resident.old_phy[0]=word(reloc+0x91328);resident.old_phy[1]=word(reloc+0x9132c);
    if(resident.old_rx||resident.old_phy[0]!=12||resident.old_phy[1]!=12)return -1;
    bw_copy(resident.driver,(const void *)(reloc+0xad340),sizeof(resident.driver));
    resident.attached=1;resident.end=now(0)+duration;
    PRINT("BW_RESIDENT_SERVICE_POLICY idle_timeout_ms=%llu accepted_task_ignores_idle_deadline=%d native_io_timeouts_retained=1\n",
          (unsigned long long)duration,BW_NATIVE_CONTINUOUS_POWER);
    for(i=0;i<64;i++){p->token[i]=argv[1][i];resident.random=resident.random*33u+(uint8_t)argv[1][i];}
    p->token[64]=0;if(!resident.random)resident.random=1;
    p->context=&resident;p->layout=BW_LAYOUT_NATIVE241;p->reboot=0;p->stop_requested=stop;
    p->require_continuous_power=BW_NATIVE_CONTINUOUS_POWER;
    p->upload=(uint8_t *)0x8a000000u;p->upload_capacity=0x6000000u;
    p->scratch=resident.scratch;p->scratch_capacity=sizeof(resident.scratch);
    p->update_lock=update_lock;p->prepare_update=prepare_update;p->finish_update=finish_update;
    if(bw_native_nand_bind(&resident.native,reloc,resident.mtd,&resident.io))return -1;
    resident.bound=1;
    if(capture(0,0x200000,snapshot,resident.boot_sha256)||capture(0xb00000,0x400000,snapshot+0x200000,resident.tags_sha256)||
       compare(0,0x200000,snapshot)||compare(0xb00000,0x400000,snapshot+0x200000)||bw_native_nand_enable_system(&resident.native,BW_LAYOUT_NATIVE241))return -1;
    resident.backup_valid=1;
    p->nand.context=&resident;p->nand.read=read_ecc;p->nand.is_bad=bad;p->nand.erase=erase;p->nand.write_page=page;
    p->nand.verify_identity=verify_identity;p->nand.verify_boot=verify_boot;p->nand.prepare_uboot_write=prepare_boot;
    p->nand.milliseconds=now;p->nand.progress=progress;
    /* Current backup is two complete ECC reads, not a persistent claim. */
    p->nand.capabilities=BW_REQUIRED|BW_CAP_NATIVE_LAYOUT|BW_CAP_STRICT_SYSTEM;
    bw_zero(&eth,sizeof(eth));eth.context=&resident;bw_copy(eth.mac,(const void *)(resident.device+20),6);
    if(!(eth.mac[0]|eth.mac[1]|eth.mac[2]|eth.mac[3]|eth.mac[4]|eth.mac[5])||(eth.mac[0]&1))return -1;
    eth.start=network_start;eth.send_frame=send_frame;eth.poll_frames=poll_frames;eth.milliseconds=now;eth.random32=random32;
    PRINT("BW_RESIDENT_NATIVE_ENTRY backup_ram=89400000 backup_bytes=600000 boot_sha256=%s tags_sha256=%s timing_verified=%d\n",
          resident.boot_sha256,resident.tags_sha256,BW_NATIVE_TIMING_PROVEN);
    return bw_lwip_bind(p,&eth);
}
int bw_vendor_detach(struct bw_platform *p) {
    int rc=0,requested=p?p->requested_reboot:0;if(!resident.attached)return 0;
    /* A deferred reboot stops the HTTP loop, not its mandatory ECC cleanup. */
    if(p)p->requested_reboot=0;
    bw_lwip_shutdown();
    if(resident.hooked){*(volatile uintptr_t *)(resident.reloc+0xad520)=resident.old_rx;resident.hooked=0;}
    if(resident.network){((void (*)(void))(resident.reloc+0x67e84))();resident.network=0;}
    *(volatile uint32_t *)(resident.device+40)=resident.old_state;
    bw_copy((void *)(resident.reloc+0xad340),resident.driver,sizeof(resident.driver));
    *(volatile uint32_t *)(resident.reloc+0x91328)=resident.old_phy[0];*(volatile uint32_t *)(resident.reloc+0x9132c)=resident.old_phy[1];
    __asm__ volatile("dsb sy\n\tisb":::"memory");
    if(ptr(resident.reloc+0xad520)!=resident.old_rx||word(resident.device+40)!=resident.old_state||
       !bw_equal((const void *)(resident.reloc+0xad340),resident.driver,sizeof(resident.driver))||
       word(resident.reloc+0x91328)!=resident.old_phy[0]||word(resident.reloc+0x9132c)!=resident.old_phy[1])rc=-1;
    /* Never compare a successfully updated boot window against its OLD
     * snapshot. The core performs readback plus all four protected hashes. */
    resident.end=now(0)+(BW_NATIVE_CONTINUOUS_POWER?180000u:30000u);
    if(resident.backup_valid&&(compare(0,0x20000,snapshot)||compare(0x180000,0x80000,snapshot+0x180000)||
       compare(0xb00000,0x400000,snapshot+0x200000)))rc=-1;
    if(resident.backup_valid&&!resident.prepared&&compare(0,0x200000,snapshot))rc=-1;
    PRINT("BW_RESIDENT_BACKUP_AUDIT rc=%d boot_sha256=%s tags_sha256=%s no_update=%d\n",
          rc,resident.boot_sha256,resident.tags_sha256,!resident.prepared);
    if(resident.bound&&bw_native_nand_release(&resident.native))rc=-1;
    if(watchdog())rc=-1;
    PRINT("BW_RESIDENT_NATIVE_EXIT rc=%d rx=%u tx=%u network_restored=%d\n",rc,resident.rx,resident.tx,rc==0);
    if(p&&!rc)p->requested_reboot=(uint32_t)requested;
    resident.attached=0;return rc;
}
