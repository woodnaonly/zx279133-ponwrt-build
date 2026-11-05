/* SPDX-License-Identifier: GPL-2.0-only */
#include "internal.h"
#ifdef BW_RAM_READONLY
int bw_execute_identity(const void *v,size_t n,const struct bw_identity *identity,uint32_t layout,const struct bw_port *p,
               void *work,size_t work_bytes,char error[BW_ERROR_SIZE]) {
    /* There is no path from this artifact to a real erase/write callback. */
    (void)v;(void)n;(void)identity;(void)layout;(void)p;(void)work;(void)work_bytes;
    return error?bw_fail(error,"RAM-only build: NAND writes are permanently disabled"):-1;
}
#else
static void kick(const struct bw_port *p) {if(p->watchdog)p->watchdog(p->context);}
static void progress(const struct bw_port *p,const char *s,uint32_t n,uint32_t total) {
    kick(p);if(p->progress)p->progress(p->context,s,n,total);
}
static int hash_flash(const struct bw_port *p,uint32_t off,uint32_t n,uint8_t *scratch,char hash[65]) {
    struct bw_hash s;bw_hash_init(&s);
    while(n) {
        uint32_t z=n>BW_ERASE_SIZE?BW_ERASE_SIZE:n;
        kick(p);if(p->read(p->context,off,scratch,z))return -1;
        bw_hash_add(&s,scratch,z);off+=z;n-=z;
    }
    bw_hash_end(&s,hash);return 0;
}
/* Includes BOTH boot tail and parameter tags, even for system updates. */
static int protected_hash(const struct bw_port *p,uint8_t *scratch,char out[4][65]) {
    return hash_flash(p,0,0x20000,scratch,out[0])||
           hash_flash(p,0x180000,0x20000,scratch,out[1])||
           hash_flash(p,0x1a0000,0x60000,scratch,out[2])||
           hash_flash(p,0xb00000,0x400000,scratch,out[3]);
}
static int allowed(const struct bw_span *s,uint32_t off,uint32_t n) {
    if(!n||off<s->offset||off>s->offset+s->capacity||n>s->offset+s->capacity-off)return 0;
    /* Defense in depth, independent of the validated target table. */
    if(off<BW_STAGE2_START||(off<0x200000&&off+n>BW_STAGE2_END)||
       (off<0xf00000&&off+n>0xb00000)||off+n>BW_FLASH_SIZE)return 0;
    return 1;
}
int bw_execute_identity(const void *v,size_t n,const struct bw_identity *identity,uint32_t layout,const struct bw_port *p,
               void *work,size_t work_bytes,char error[BW_ERROR_SIZE]) {
    const uint8_t *data=v;uint8_t *scratch=work,bad[BW_FLASH_SIZE/BW_ERASE_SIZE];
    struct bw_result r;struct bw_uboot_budget budget;uint64_t maximum;int first_erase=1,need_rescue_waiver=0;
    char before[4][65],after[4][65],actual[65],boot_before[65];unsigned i;
    int strict_system;
    if(!error)return -1;error[0]=0;
    if(!bw_identity_valid(identity))return bw_fail(error,"unknown/unadapted vendor ABI or installed identity");
    if(!p||!scratch||work_bytes<BW_ERASE_SIZE||!p->is_bad||!p->read||!p->erase||!p->write_page)
        return bw_fail(error,"incomplete verified NAND adapter/scratch");
    if((p->capabilities&BW_REQUIRED)!=BW_REQUIRED)return bw_fail(error,"RAM/network/ECC/backup evidence gates are locked");
    if(layout==1&&!(p->capabilities&BW_CAP_NATIVE_LAYOUT))return bw_fail(error,"native 241 MiB layout gate is locked");
    if(layout==2&&!(p->capabilities&BW_CAP_RECOVERY_LAYOUT))return bw_fail(error,"4 MiB recovery layout gate is locked");
    if(bw_validate_identity(data,n,identity,layout,&r,error))return -1;
    strict_system=r.kind==BW_SYSTEM&&(p->capabilities&BW_CAP_STRICT_SYSTEM);
    if(r.kind==BW_UBOOT) {
        if(!(p->capabilities&BW_CAP_BOOT_CONTAINER))return bw_fail(error,"vendor boot-container gate is locked");
        need_rescue_waiver=!(p->capabilities&BW_CAP_SERIAL_RESCUE);
        if(need_rescue_waiver&&!p->authorize_no_rescue)
            return bw_fail(error,"serial rescue is unverified and no explicit one-operation waiver exists");
    }
    if(r.kind==BW_UBOOT&&(!p->verify_boot||p->verify_boot(p->context,data+r.writes[0].data_offset,(size_t)r.writes[0].length)))
        return bw_fail(error,"target U-Boot BTL/container verification is missing/failed");
    bw_zero(&budget,sizeof(budget));
    if(r.kind==BW_UBOOT) {
        if(!p->prepare_uboot_write||!p->milliseconds||
           p->prepare_uboot_write(p->context,identity,data+r.writes[0].data_offset,(size_t)r.writes[0].length,&budget))
            return bw_fail(error,"verified cold-power/RAM recovery-data preflight is missing/failed");
        if(bw_uboot_budget_check(&budget,p->milliseconds(p->context),&maximum,error))return -1;
    }
    progress(p,"preflight",0,r.count);
    /* The web client and CONTROL cannot choose this fingerprint. */
    if(hash_flash(p,BW_STAGE2_START,BW_STAGE2_SIZE,scratch,actual)||!bw_string(actual,identity->installed_sha256))
        return bw_fail(error,"installed stage2 read/hash differs from confirmed image");
    if((p->verify_identity&&p->verify_identity(p->context,identity))||
       (!p->verify_identity&&!bw_string(identity->installed_sha256,BW_VENDOR_ABI_SHA256)))
        return bw_fail(error,"patched installed image requires native vendor ABI/patch verification");
    if(protected_hash(p,scratch,before))return bw_fail(error,"cannot snapshot protected regions with ECC");
    if(strict_system&&hash_flash(p,0,0x200000,scratch,boot_before))
        return bw_fail(error,"cannot snapshot complete boot region with ECC");
    bw_zero(bad,sizeof(bad));
    /* ALL images must fit the available GOOD blocks before ANY erase. */
    for(i=0;i<r.count;i++) {
        const struct bw_span *s=&r.writes[i];uint32_t off,available=0;
        if(!allowed(s,s->offset,s->capacity))return bw_fail(error,"protected/overflowing target rejected");
        for(off=s->offset;off<s->offset+s->capacity;off+=BW_ERASE_SIZE) {
            int b;kick(p);b=p->is_bad(p->context,off);
            if(b<0||b>1)return bw_fail(error,"bad-block query failed");
            if(b&&r.kind==BW_UBOOT)return bw_fail(error,"bad block in physical boot window; BTL remapping is not verified");
            if(b&&strict_system)return bw_fail(error,"bad block in system target; mapping is not verified; nothing erased");
            bad[off/BW_ERASE_SIZE]=(uint8_t)b;if(!b)available+=BW_ERASE_SIZE;
        }
        if(s->length>available)return bw_fail(error,"not enough good NAND blocks in fixed partition");
    }
    for(i=0;i<r.count;i++) {
        const struct bw_span *s=&r.writes[i];uint32_t off,page;size_t done=0;struct bw_hash h;
        progress(p,"erase",0,s->capacity);
        for(off=s->offset;off<s->offset+s->capacity;off+=BW_ERASE_SIZE) {
            if(!bad[off/BW_ERASE_SIZE]) {
                /* Recheck AFTER all hashes, bad queries and the last progress
                 * callback. Never begin erasing just because an earlier check
                 * had enough time. Do not abort an accepted write on a timer. */
                if(first_erase&&r.kind==BW_UBOOT) {
                    if(need_rescue_waiver&&p->authorize_no_rescue(p->context,identity,r.package_sha256))
                        return bw_fail(error,"explicit no-rescue authorization denied/consumed; nothing erased");
                    /* The authorizer may take time or touch the upload buffer.
                     * Final candidate/time checks MUST follow it as well. */
                    bw_sha256(data,n,actual);
                    if(!bw_string(actual,r.package_sha256))return bw_fail(error,"candidate changed during preflight; nothing erased");
                    if(bw_uboot_budget_check(&budget,p->milliseconds(p->context),&maximum,error))return -1;
                }
                first_erase=0;
                if(!allowed(s,off,BW_ERASE_SIZE)||p->erase(p->context,off)) {
                    bw_fail(error,"NAND erase failed; no automatic retry/success");goto audit;
                }
            }
            progress(p,"erase",off-s->offset+BW_ERASE_SIZE,s->capacity);
        }
        progress(p,"write",0,(uint32_t)s->length);
        for(off=s->offset;off<s->offset+s->capacity&&done<s->length;off+=BW_ERASE_SIZE) {
            if(bad[off/BW_ERASE_SIZE])continue;
            for(page=0;page<BW_ERASE_SIZE&&done<s->length;page+=BW_PAGE_SIZE) {
                size_t z=(size_t)s->length-done,j;if(z>BW_PAGE_SIZE)z=BW_PAGE_SIZE;
                for(j=0;j<BW_PAGE_SIZE;j++)scratch[j]=0xff;
                bw_copy(scratch,data+s->data_offset+done,z);kick(p);
                if(!allowed(s,off+page,BW_PAGE_SIZE)||p->write_page(p->context,off+page,scratch)) {
                    bw_fail(error,"NAND page write failed; manual recovery required");goto audit;
                }
                done+=z;
            }
            progress(p,"write",(uint32_t)done,(uint32_t)s->length);
        }
        bw_hash_init(&h);done=0;
        for(off=s->offset;off<s->offset+s->capacity&&done<s->length;off+=BW_ERASE_SIZE) {
            size_t z=(size_t)s->length-done;if(z>BW_ERASE_SIZE)z=BW_ERASE_SIZE;
            if(bad[off/BW_ERASE_SIZE])continue;kick(p);
            if(p->read(p->context,off,scratch,z)) {bw_fail(error,"NAND readback/ECC failed");goto audit;}
            bw_hash_add(&h,scratch,z);done+=z;
            progress(p,"verify",(uint32_t)done,(uint32_t)s->length);
        }
        bw_hash_end(&h,actual);
        if(!bw_string(actual,s->sha256)) {bw_fail(error,"NAND readback SHA256 mismatch");goto audit;}
        if(strict_system)progress(p,"image-verified",i+1,r.count);
        if(strict_system) {
            /* Check every unused byte, including the padded last page. A
             * matching image prefix alone cannot prove an old JFFS2 tail is
             * gone. Native strict tasks reject bad blocks before any erase. */
            uint32_t at=(uint32_t)s->length;
            while(at<s->capacity) {
                uint32_t z=s->capacity-at,j;if(z>BW_ERASE_SIZE)z=BW_ERASE_SIZE;
                kick(p);
                if(p->read(p->context,s->offset+at,scratch,z)) {bw_fail(error,"erased-tail ECC read failed");goto audit;}
                for(j=0;j<z;j++)if(scratch[j]!=0xff) {bw_fail(error,"erased-tail verification failed; old data remains");goto audit;}
                at+=z;progress(p,"verify-tail",at,s->capacity);
            }
            progress(p,"tail-verified",i+1,r.count);
        }
    }
audit:
    /* Attempt this even after an erase/write/readback error. A failed update
     * must not silently skip the protected-region check. No further writes. */
    if(protected_hash(p,scratch,after)||!bw_equal(before,after,sizeof(before)))
        return bw_fail(error,"protected region hash changed/read failed; NOT success");
    if(strict_system&&(hash_flash(p,0,0x200000,scratch,actual)||!bw_string(actual,boot_before)))
        return bw_fail(error,"complete boot region changed/read failed; NOT success");
    if(error[0])return -1;
    if(strict_system)progress(p,"protected-audit",4,4);
    progress(p,"complete",r.count,r.count);return 0;
}
#endif
int bw_execute(const void *v,size_t n,const char *base,uint32_t layout,const struct bw_port *p,
               void *work,size_t work_bytes,char error[BW_ERROR_SIZE]) {
#ifdef BW_RAM_READONLY
    (void)base;return bw_execute_identity(v,n,0,layout,p,work,work_bytes,error);
#else
    struct bw_identity identity;
    if(bw_legacy_identity(base,&identity))return error?bw_fail(error,"unknown/unadapted installed U-Boot code base"):-1;
    return bw_execute_identity(v,n,&identity,layout,p,work,work_bytes,error);
#endif
}
