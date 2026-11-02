/* SPDX-License-Identifier: GPL-2.0-only */
#include "internal.h"
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
int bw_execute(const void *v,size_t n,const char *base,uint32_t layout,const struct bw_port *p,
               void *work,size_t work_bytes,char error[BW_ERROR_SIZE]) {
    const uint8_t *data=v;uint8_t *scratch=work,bad[BW_FLASH_SIZE/BW_ERASE_SIZE];
    struct bw_result r;char before[4][65],after[4][65],actual[65];unsigned i;
    if(!error)return -1;error[0]=0;
    if(!p||!scratch||work_bytes<BW_ERASE_SIZE||!p->is_bad||!p->read||!p->erase||!p->write_page)
        return bw_fail(error,"incomplete verified NAND adapter/scratch");
    if((p->capabilities&BW_REQUIRED)!=BW_REQUIRED)return bw_fail(error,"RAM/network/ECC/backup evidence gates are locked");
    if(layout==1&&!(p->capabilities&BW_CAP_NATIVE_LAYOUT))return bw_fail(error,"native 241 MiB layout gate is locked");
    if(layout==2&&!(p->capabilities&BW_CAP_RECOVERY_LAYOUT))return bw_fail(error,"4 MiB recovery layout gate is locked");
    if(bw_validate(data,n,base,layout,&r,error))return -1;
    if(r.kind==BW_UBOOT&&(p->capabilities&(BW_CAP_SERIAL_RESCUE|BW_CAP_BOOT_CONTAINER))!=
       (BW_CAP_SERIAL_RESCUE|BW_CAP_BOOT_CONTAINER))return bw_fail(error,"serial rescue/vendor boot-container gates are locked");
    if(r.kind==BW_UBOOT&&(!p->verify_boot||p->verify_boot(p->context,data+r.writes[0].data_offset,(size_t)r.writes[0].length)))
        return bw_fail(error,"target U-Boot BTL/container verification is missing/failed");
    progress(p,"preflight",0,r.count);
    /* The web client and CONTROL cannot choose this fingerprint. */
    if(hash_flash(p,BW_STAGE2_START,BW_STAGE2_SIZE,scratch,actual)||!bw_string(actual,base))
        return bw_fail(error,"installed stage2 read/hash differs from adapted base");
    if(protected_hash(p,scratch,before))return bw_fail(error,"cannot snapshot protected regions with ECC");
    bw_zero(bad,sizeof(bad));
    /* ALL images must fit the available GOOD blocks before ANY erase. */
    for(i=0;i<r.count;i++) {
        const struct bw_span *s=&r.writes[i];uint32_t off,available=0;
        if(!allowed(s,s->offset,s->capacity))return bw_fail(error,"protected/overflowing target rejected");
        for(off=s->offset;off<s->offset+s->capacity;off+=BW_ERASE_SIZE) {
            int b;kick(p);b=p->is_bad(p->context,off);
            if(b<0||b>1)return bw_fail(error,"bad-block query failed");
            bad[off/BW_ERASE_SIZE]=(uint8_t)b;if(!b)available+=BW_ERASE_SIZE;
        }
        if(s->length>available)return bw_fail(error,"not enough good NAND blocks in fixed partition");
    }
    for(i=0;i<r.count;i++) {
        const struct bw_span *s=&r.writes[i];uint32_t off,page;size_t done=0;struct bw_hash h;
        progress(p,"erase",0,s->capacity);
        for(off=s->offset;off<s->offset+s->capacity;off+=BW_ERASE_SIZE) {
            if(!bad[off/BW_ERASE_SIZE]) {
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
    }
audit:
    /* Attempt this even after an erase/write/readback error. A failed update
     * must not silently skip the protected-region check. No further writes. */
    if(protected_hash(p,scratch,after)||!bw_equal(before,after,sizeof(before)))
        return bw_fail(error,"protected region hash changed/read failed; NOT success");
    if(error[0])return -1;
    progress(p,"complete",r.count,r.count);return 0;
}
