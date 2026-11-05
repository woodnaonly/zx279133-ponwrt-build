/* SPDX-License-Identifier: GPL-2.0-only
 * Experimental RAM-only svn308776 adapter. Never linked into the ordinary
 * unadapted output. No vendor erase/program/reset/saveenv address is bound.
 * Code pins are generated from the exact, fixed SHA256 baseline, then checked
 * again against this running relocated instance before calling any vendor API.
 */
#ifndef BW_RAM_READONLY
#error "this adapter must NEVER be linked into a write-capable artifact"
#endif
#include "net_bridge.h"
#include "internal.h"
#include "ram_readonly.h"
#ifdef BW_RAM_BACKUP
#include "backup.h"
#endif
#ifndef BW_RAM_CONTINUOUS_POWER
#define BW_RAM_CONTINUOUS_POWER 0
#endif
#ifdef BW_RESIDENT_BOOTSTRAP
#include "resident_image.h"
#endif
#ifdef BW_NATIVE_BOUNDED_SPI
#include "native_spi.h"
#endif
#ifdef BW_INSTALL_PREFLIGHT
#include "native_nand.h"
static struct bw_native_nand preflight_nand;
static struct bw_port preflight_port;
#endif
#include <string.h>
struct bw_pin {uint32_t offset,length;const char *sha256;};
#include "bw_ram_pins.h"
typedef int (*print_fn)(const char *,...);
typedef unsigned long (*timer_fn)(unsigned long);
typedef int (*read_fn)(void *,uint64_t,size_t,size_t *,void *);
typedef int (*bad_fn)(void *,uint64_t);
static struct {
    uintptr_t reloc,gd,device,mtd;
    struct bw_platform *platform;
    uint64_t end;
    uint32_t random,read_calls,corrected_reads,frames_rx,frames_tx;
    int attached,net_started,rx_hooked,failed;
    uintptr_t old_rx;
    uint32_t old_state;
    uint32_t old_phy_modes[2];
    void (*receive)(void *,const void *,size_t);
    void *receive_arg;
    uint8_t driver_before[0x1e8]; /* measured legacy NP context AD340..AD528 */
    char before[2][65];
    _Alignas(64) uint8_t scratch[BW_ERASE_SIZE];
} trial;
static print_fn printer(void) {return (print_fn)(trial.reloc+0x6677c);}
static uintptr_t pointer(uintptr_t at) {return *(volatile const uintptr_t *)at;}
static uint32_t word(uintptr_t at) {return *(volatile const uint32_t *)at;}
static int watchdog_disabled(const char *phase) {
    /* Addresses come from the pinned vendor Disable-watchdog sequence.
     * The high halfword is its write key; all writable low control bits must
     * be zero. Never enable, kick, reconfigure or write these peripherals. */
    uint32_t a=word(0x14f0901cu),b=word(0x14f0a01cu),c=word(0x14f0b01cu),d=word(0x14f0c01cu);
    int disabled=((a|b|c|d)&0xffffu)==0;
    printer()("BW_RAM_WATCHDOG phase=%s controls=%x,%x,%x,%x disabled=%d\n",phase,a,b,c,d,disabled);
    return disabled?0:-1;
}
static int resident(uintptr_t at,size_t n) {return at>=0x9f6ee080u&&n&&at<=0xa0000000u-n;}
static int code_pinned(uintptr_t reloc) {
    unsigned i;char hash[65];
    for(i=0;i<sizeof(bw_ram_pins)/sizeof(bw_ram_pins[0]);i++) {
        const struct bw_pin *p=&bw_ram_pins[i];
        if(p->offset>0xb0000||p->length>0xb0000-p->offset)return -1;
        bw_sha256((const void *)(reloc+p->offset),p->length,hash);
        if(!bw_string(hash,p->sha256))return -1;
    }return 0;
}
static uint64_t now(void *unused) {(void)unused;return ((timer_fn)(trial.reloc+0x65630))(0);}
static int stop(void *unused) {(void)unused;return trial.failed||
#ifdef BW_RAM_BACKUP
    bw_backup_finished()||
#endif
    now(0)>=trial.end;}
static int deny_erase(void *unused,uint32_t off) {(void)unused;(void)off;trial.failed=1;return -1;}
static int deny_page(void *unused,uint32_t off,const void *p) {(void)unused;(void)off;(void)p;trial.failed=1;return -1;}
static int is_bad(void *unused,uint32_t off) {
    int rc;(void)unused;
    if(off%BW_ERASE_SIZE||off>=BW_FLASH_SIZE)return -1;
    rc=((bad_fn)(trial.reloc+0x192c0))((void *)trial.mtd,off);
    return
#ifdef BW_NATIVE_BOUNDED_SPI
        bw_native_spi_failed()?-1:
#endif
        rc==0||rc==1?rc:-1;
}
static int read_flash(void *unused,uint32_t off,void *out,size_t n) {
    uint32_t block;size_t done=0;int rc;(void)unused;
    if(stop(0)||!bw_ram_read_allowed(off,n))return -1;
    for(block=off&~(BW_ERASE_SIZE-1u);block<off+n;block+=BW_ERASE_SIZE) {
        rc=is_bad(0,block);
        if(rc!=0) {printer()("BW_RAM_BAD_BLOCK_OR_QUERY_ERROR off=%x rc=%d\n",block,rc);trial.failed=1;return -1;}
    }
    rc=((read_fn)(trial.reloc+0x18d34))((void *)trial.mtd,off,n,&done,out);
#ifdef BW_NATIVE_BOUNDED_SPI
    if(bw_native_spi_failed())rc=-5;
#endif
    trial.read_calls++;
    if(bw_ram_ecc_result(rc,done,n)<0) {
        printer()("BW_RAM_READ_ERROR off=%x requested=%lu returned=%lu rc=%d\n",off,(unsigned long)n,(unsigned long)done,rc);
        trial.failed=1;return -1;
    }
    if(rc==-117) {trial.corrected_reads++;printer()("BW_RAM_ECC_CORRECTED off=%x length=%lu\n",off,(unsigned long)n);}
    return 0;
}
static int hashes(char values[2][65],const char *phase) {
    static const uint32_t starts[2]={0,0xb00000},sizes[2]={0x200000,0x400000};
    unsigned i;
    for(i=0;i<2;i++) {
        struct bw_hash hash;uint32_t off;bw_hash_init(&hash);
        for(off=0;off<sizes[i];off+=BW_ERASE_SIZE) {
            if(read_flash(0,starts[i]+off,trial.scratch,BW_ERASE_SIZE))return -1;
            bw_hash_add(&hash,trial.scratch,BW_ERASE_SIZE);
        }bw_hash_end(&hash,values[i]);
        printer()("BW_RAM_HASH %s %s %s\n",phase,i?"tags":"boot",values[i]);
    }return 0;
}
#ifdef BW_RESIDENT_BOOTSTRAP
static int installed_identity(void *unused,const struct bw_identity *identity) {
    uint8_t *image=(uint8_t *)0x89200000u;uint32_t off;char digest[65];int kind;(void)unused;
    if(!bw_identity_valid(identity))return -1;
    for(off=0;off<BW_STAGE2_SIZE;off+=BW_ERASE_SIZE)
        if(read_flash(0,BW_STAGE2_START+off,image+off,BW_ERASE_SIZE))return -1;
    kind=bw_resident_image_check(image,BW_STAGE2_SIZE,digest);
    if(kind<0||!bw_string(digest,identity->installed_sha256))return -1;
    trial.platform->resident_version=kind==1?bw_resident_image_version(image,BW_STAGE2_SIZE):0;
    printer()("BW_INSTALLED_IDENTITY resident=%d vendor_abi=%s installed_sha256=%s\n",kind,
              BW_VENDOR_ABI_SHA256,digest);
    return 0;
}
#endif
#ifdef BW_NATIVE_NAND_DRYRUN
#ifdef BW_INSTALL_PREFLIGHT
static int native_nand_dryrun(void) {
    uint32_t off,page;unsigned i;
    /* The private preflight callbacks only reach SPI-level emulation. The
     * HTTP platform keeps deny_erase/deny_page and capabilities=0 throughout. */
    for(off=BW_STAGE2_START;off<BW_STAGE2_END;off+=BW_ERASE_SIZE) {
        if(preflight_port.is_bad(preflight_port.context,off)!=0||
           preflight_port.erase(preflight_port.context,off))return -1;
        for(page=0;page<BW_ERASE_SIZE;page+=BW_PAGE_SIZE) {
            for(i=0;i<BW_PAGE_SIZE;i++)trial.scratch[i]=(uint8_t)(i*37u+11u+(off+page)/BW_PAGE_SIZE);
            if(preflight_port.write_page(preflight_port.context,off+page,trial.scratch))return -1;
        }
    }
    printer()("BW_NATIVE_NAND_PREFLIGHT rc=0 erases=%u pages=%u max_bad_ms=%u max_erase_ms=%u max_page_ms=%u hardware_mutations=0\n",
              preflight_nand.erases,preflight_nand.pages,preflight_nand.max_bad_ms,
              preflight_nand.max_erase_ms,preflight_nand.max_page_ms);
    return preflight_nand.erases==11&&preflight_nand.pages==704?0:-1;
}
#else
static int native_nand_dryrun(void) {
    _Alignas(16) uint8_t erase[80];size_t done=0;int rc,dryrc;unsigned i;
    if(pointer(trial.mtd+112)!=trial.reloc+0x25824||pointer(trial.mtd+136)!=0||
       pointer(trial.mtd+160)!=trial.reloc+0x25fa0||pointer(trial.mtd+256)!=trial.reloc+0x2592c)return -1;
    bw_zero(erase,sizeof(erase));*(uintptr_t *)(erase+0)=trial.mtd;
    *(uint64_t *)(erase+8)=BW_STAGE2_START;*(uint64_t *)(erase+16)=BW_ERASE_SIZE;
    if(bw_native_spi_dry_begin(BW_STAGE2_START,0))return -1;
    rc=((int (*)(void *,void *))(trial.reloc+0x18cc0))((void *)trial.mtd,erase);
    dryrc=bw_native_spi_dry_end();
    printer()("BW_NAND_DRY_ERASE_ABI rc=%d emulation_rc=%d fail_address=%llx state=%u hardware_mutations=0\n",
              rc,dryrc,(unsigned long long)*(uint64_t *)(erase+24),erase[72]);
    if(rc||dryrc||*(uint64_t *)(erase+24)!=UINT64_MAX)return -1;
    for(i=0;i<BW_PAGE_SIZE;i++)trial.scratch[i]=(uint8_t)(i*37u+11u);
    if(bw_native_spi_dry_begin(BW_STAGE2_START,trial.scratch))return -1;
    rc=((read_fn)(trial.reloc+0x18dfc))((void *)trial.mtd,BW_STAGE2_START,BW_PAGE_SIZE,&done,trial.scratch);
    dryrc=bw_native_spi_dry_end();
    printer()("BW_NAND_DRY_PAGE_ABI rc=%d emulation_rc=%d returned=%lu requested=%u hardware_mutations=0\n",
              rc,dryrc,(unsigned long)done,BW_PAGE_SIZE);
    return rc||dryrc||done!=BW_PAGE_SIZE?-1:0;
}
#endif
#endif
static uint32_t random32(void *unused) {
    uint32_t x=trial.random;(void)unused;x^=x<<13;x^=x>>17;x^=x<<5;trial.random=x;return x;
}
static void raw_receive(const void *frame,int length) {
    if(trial.receive&&!stop(0)&&length>=14&&length<=1536) {
        trial.frames_rx++;trial.receive(trial.receive_arg,frame,(size_t)length);
    }
}
static int network_start(void *unused) {
    int rc;volatile uintptr_t *slot=(volatile uintptr_t *)(trial.reloc+0xad520);(void)unused;
    if(stop(0)||trial.net_started||*slot!=trial.old_rx)return -1;
    /* This is the measured legacy eth_init, not a shell handler. */
    rc=((int (*)(void))(trial.reloc+0x67e00))();
    if(rc<0)return -1;
    trial.net_started=1;
    if(pointer(trial.reloc+0xb1938)!=trial.device||*slot!=trial.old_rx)return -1;
    *slot=(uintptr_t)raw_receive;__asm__ volatile("dsb sy\n\tisb":::"memory");trial.rx_hooked=1;
    printer()("BW_RAM_NETWORK_STARTED ip=192.168.9.1 read_only=1\n");return 0;
}
static int send_frame(void *unused,const void *frame,size_t n) {
    int rc;(void)unused;if(stop(0)||!trial.net_started||n<14||n>1536)return -1;
    rc=((int (*)(void *,const void *,int))(trial.reloc+0x36dac))((void *)trial.device,frame,(int)n);
    if(rc>=0)trial.frames_tx++;return rc;
}
static int poll_frames(void *unused,void (*receive)(void *,const void *,size_t),void *arg) {
    int rc;(void)unused;if(stop(0)||!trial.net_started)return -1;
    trial.receive=receive;trial.receive_arg=arg;
    rc=((int (*)(void))(trial.reloc+0x37364))();
    trial.receive=0;trial.receive_arg=0;return rc;
}
int bw_vendor_attach(struct bw_platform *p,int argc,char *const argv[]) {
    uintptr_t gd,reloc;uint64_t duration;unsigned i;char again[2][65];struct bw_ethernet eth;
    __asm__ volatile("mov %0,x18":"=r"(gd));
    if(!p||argc!=3||!argv||!bw_hex64(argv[1])||bw_decimal((const uint8_t *)argv[2],strlen(argv[2]),&duration)||
       duration<1000||duration>
#ifdef BW_RAM_BACKUP
       (BW_RAM_CONTINUOUS_POWER?1800000u:900000u)
#else
       180000u
#endif
       ||!resident(gd,208))return -1;
    reloc=pointer(gd+112);
    if(reloc!=0x9ff3c000u||pointer(gd+152)!=reloc-0x88000000u||code_pinned(reloc))return -1;
    trial.reloc=reloc;trial.gd=gd;trial.platform=p;trial.device=pointer(reloc+0xb1938);trial.mtd=pointer(reloc+0xac580);
    if(!resident(trial.device,128)||!resident(trial.mtd,384)||pointer(trial.mtd+8)!=BW_FLASH_SIZE||
       word(trial.mtd+16)!=BW_ERASE_SIZE||word(trial.mtd+20)!=BW_PAGE_SIZE||word(trial.mtd+28)!=128||
       pointer(trial.mtd+128)!=0||pointer(trial.mtd+152)!=reloc+0x26220||pointer(trial.mtd+256)!=reloc+0x2592c||
       pointer(trial.device+48)!=reloc+0x36f88||pointer(trial.device+56)!=reloc+0x36dac||
       pointer(trial.device+64)!=reloc+0x37364||pointer(trial.device+72)!=reloc+0x36fdc)return -1;
    trial.old_rx=pointer(reloc+0xad520);if(trial.old_rx)return -1;
    if(watchdog_disabled("before"))return -1;
    trial.old_state=word(trial.device+40);
    /* Pinned 3A4A4..3A524 reads/updates the two SerDes mode caches.
     * A full NP halt/init without restoring them can skip required PHY init. */
    trial.old_phy_modes[0]=word(reloc+0x91328);trial.old_phy_modes[1]=word(reloc+0x9132c);
    if(trial.old_phy_modes[0]!=12||trial.old_phy_modes[1]!=12)return -1;
    bw_copy(trial.driver_before,(const void *)(reloc+0xad340),sizeof(trial.driver_before));
    trial.attached=1;trial.end=now(0)+duration;
#ifdef BW_RAM_BACKUP
    bw_backup_finish_reset();
#endif
    /* Host validated this single record before entry; leave '?' usable even
     * if the UART owner exits while the RAM-only service is still running. */
#ifndef BW_RESIDENT_BOOTSTRAP
    *(volatile uintptr_t *)(reloc+0x96c50+24)=reloc+0x2600;
#endif
    __asm__ volatile("dsb sy\n\tisb":::"memory");
    for(i=0;i<64;i++) {p->token[i]=argv[1][i];trial.random=trial.random*33u+(uint8_t)argv[1][i];}
    p->token[64]=0;if(!trial.random)trial.random=1;
    p->context=&trial;p->layout=BW_LAYOUT_CURRENT;p->reboot=0;p->stop_requested=stop;
    p->require_continuous_power=BW_RAM_CONTINUOUS_POWER;
    p->upload=(uint8_t *)0x8a000000u;p->upload_capacity=0x6000000u;
    p->scratch=trial.scratch;p->scratch_capacity=sizeof(trial.scratch);
    p->nand.context=&trial;p->nand.capabilities=0;p->nand.read=read_flash;p->nand.is_bad=is_bad;
    p->nand.erase=deny_erase;p->nand.write_page=deny_page;p->nand.verify_boot=0;
#ifdef BW_RESIDENT_BOOTSTRAP
    p->nand.verify_identity=installed_identity;
#endif
    bw_zero(&eth,sizeof(eth));eth.context=&trial;bw_copy(eth.mac,(const void *)(trial.device+20),6);
    if(!(eth.mac[0]|eth.mac[1]|eth.mac[2]|eth.mac[3]|eth.mac[4]|eth.mac[5])||(eth.mac[0]&1))return -1;
    eth.start=network_start;eth.send_frame=send_frame;eth.poll_frames=poll_frames;eth.milliseconds=now;eth.random32=random32;
    printer()("BW_RAM_READONLY_ENTRY duration_ms=%llu caps=0 writes_compiled_out=1\n",(unsigned long long)duration);
#ifdef BW_NATIVE_BOUNDED_SPI
#ifdef BW_INSTALL_PREFLIGHT
    if(bw_native_nand_bind(&preflight_nand,reloc,trial.mtd,&preflight_port))return -1;
#else
    if(bw_native_spi_attach(reloc,trial.mtd))return -1;
#endif
#endif
    if(hashes(trial.before,"before"))return -1;
#ifdef BW_NATIVE_NAND_DRYRUN
    if(native_nand_dryrun())return -1;
#endif
    if(hashes(again,"repeat")||!bw_equal(trial.before,again,sizeof(again)))return -1;
    if(bw_lwip_bind(p,&eth))return -1;
    return 0;
}
int bw_vendor_detach(struct bw_platform *p) {
    char after[2][65];int rc=0,earlier_error;(void)p;
    if(!trial.attached)return 0;
    bw_lwip_shutdown();
    if(trial.rx_hooked) {
        *(volatile uintptr_t *)(trial.reloc+0xad520)=trial.old_rx;
        __asm__ volatile("dsb sy\n\tisb":::"memory");trial.rx_hooked=0;
        if(pointer(trial.reloc+0xad520)!=trial.old_rx)rc=-1;
    }
    if(trial.net_started) {
        ((void (*)(void))(trial.reloc+0x67e84))();
        *(volatile uint32_t *)(trial.device+40)=trial.old_state;trial.net_started=0;
    }
    /* Halt all legacy ports before restoring ring indices/pointers and the
     * one-time NP-init flag. A later U-Boot eth_init must take its original
     * path, not inherit our private stack or RX callback. */
    bw_copy((void *)(trial.reloc+0xad340),trial.driver_before,sizeof(trial.driver_before));
    *(volatile uint32_t *)(trial.reloc+0x91328)=trial.old_phy_modes[0];
    *(volatile uint32_t *)(trial.reloc+0x9132c)=trial.old_phy_modes[1];
    __asm__ volatile("dsb sy\n\tisb":::"memory");
    if(!bw_equal((const void *)(trial.reloc+0xad340),trial.driver_before,sizeof(trial.driver_before))||
       word(trial.device+40)!=trial.old_state||word(trial.reloc+0x91328)!=trial.old_phy_modes[0]||
       word(trial.reloc+0x9132c)!=trial.old_phy_modes[1])rc=-1;
    printer()("BW_RAM_PHY_MODES before=%x,%x after=%x,%x\n",trial.old_phy_modes[0],trial.old_phy_modes[1],
              word(trial.reloc+0x91328),word(trial.reloc+0x9132c));
    /* A service deadline must not prevent the final read-only integrity audit.
     * The host has a separate minute reserved before its 18-minute cutoff. */
    trial.end=now(0)+30000;earlier_error=trial.failed;trial.failed=0;
#ifdef BW_RAM_BACKUP
    bw_backup_finish_reset();
#endif
    if(hashes(after,"after")||!bw_equal(trial.before,after,sizeof(after))||earlier_error)rc=-1;
    if(watchdog_disabled("after"))rc=-1;
#ifdef BW_NATIVE_BOUNDED_SPI
#ifdef BW_INSTALL_PREFLIGHT
    if(bw_native_nand_release(&preflight_nand))rc=-1;
#else
    if(bw_native_spi_detach())rc=-1;
#endif
#endif
    printer()("BW_RAM_READONLY_EXIT rc=%d reads=%u corrected_reads=%u rx=%u tx=%u rx_restored=%d driver_restored=%d phy_modes_restored=%d\n",
              rc,trial.read_calls,trial.corrected_reads,trial.frames_rx,trial.frames_tx,
              pointer(trial.reloc+0xad520)==trial.old_rx,
              bw_equal((const void *)(trial.reloc+0xad340),trial.driver_before,sizeof(trial.driver_before))&&word(trial.device+40)==trial.old_state,
              word(trial.reloc+0x91328)==trial.old_phy_modes[0]&&word(trial.reloc+0x9132c)==trial.old_phy_modes[1]);
    trial.attached=0;return rc;
}
