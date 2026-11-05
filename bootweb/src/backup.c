/* SPDX-License-Identifier: GPL-2.0-only */
#if !defined(BW_RAM_READONLY) || !defined(BW_RAM_BACKUP)
#error "NAND backup must be linked only into the separate RAM-only backup build"
#endif
#include "internal.h"
#include "backup.h"
static int finished;
void bw_backup_finish_reset(void) {finished=0;}
void bw_backup_finish(void) {finished=1;}
int bw_backup_finished(void) {return finished;}
static void le32(uint8_t *p,uint32_t v) {
    unsigned i;for(i=0;i<4;i++)p[i]=(uint8_t)(v>>(8*i));
}
int bw_backup_capture(const struct bw_port *p,uint32_t off,uint32_t n,
                      void *v,size_t cap,char error[BW_ERROR_SIZE]) {
    uint8_t *buffer=v;uint32_t done,bad=0;char digest[65];
    if(!error)return -1;error[0]=0;
    if(!p||!p->read||!p->is_bad||!buffer||!n||n>BW_BACKUP_CHUNK||
       off%BW_ERASE_SIZE||n%BW_ERASE_SIZE||off>BW_FLASH_SIZE-n||cap<BW_BACKUP_HEADER+n)
        return bw_fail(error,"invalid physical ECC backup range/buffer");
    /* A valid magic is published only AFTER every read and the hash finishes. */
    bw_zero(buffer,BW_BACKUP_HEADER);
    for(done=0;done<n;done+=BW_ERASE_SIZE) {
        uint8_t *out=buffer+BW_BACKUP_HEADER+done;int b;size_t i;
        bw_poll();if(bw_cancelled())return bw_fail(error,"backup service deadline reached");
        b=p->is_bad(p->context,off+done);
        if(b<0||b>1)return bw_fail(error,"backup bad-block query failed");
        if(b) {bad|=1u<<(done/BW_ERASE_SIZE);for(i=0;i<BW_ERASE_SIZE;i++)out[i]=0xff;}
        else if(p->read(p->context,off+done,out,BW_ERASE_SIZE))
            return bw_fail(error,"backup ECC read failed; no substituted data");
    }
    bw_sha256(buffer+BW_BACKUP_HEADER,n,digest);
    if(bw_cancelled())return bw_fail(error,"backup service deadline reached");
    le32(buffer+8,off);le32(buffer+12,n);le32(buffer+16,bad);le32(buffer+20,BW_BACKUP_HEADER);
    bw_copy(buffer+24,digest,64);bw_copy(buffer,"BWROBK01",8);return 0;
}
size_t bw_backup_pack(void *v,size_t cap) {
    uint8_t *buffer=v,*source,*encoded;uint32_t n,at=0,out=0;
    if(!buffer||cap<BW_BACKUP_HEADER||!bw_equal(buffer,"BWROBK01",8))return 0;
    n=bw_le32(buffer+12);
    if(!n||n>BW_BACKUP_CHUNK||cap<BW_BACKUP_HEADER+n)return 0;
    if(cap<BW_BACKUP_HEADER+2u*n)return BW_BACKUP_HEADER+n;
    source=buffer+BW_BACKUP_HEADER;encoded=source+n;
    while(at<n) {
        uint32_t run=1;uint8_t value=source[at];
        while(run<65535u&&run<n-at&&source[at+run]==value)run++;
        if(out+3u>=n)return BW_BACKUP_HEADER+n; /* do not expand dense data */
        encoded[out++]=(uint8_t)run;encoded[out++]=(uint8_t)(run>>8);encoded[out++]=value;
        at+=run;if(!(at&0x3fffu)){bw_poll();if(bw_cancelled())return 0;}
    }
    if(bw_cancelled())return 0;
    bw_copy(source,encoded,out);le32(buffer+88,1);le32(buffer+92,out);
    bw_copy(buffer,"BWROBK02",8);return BW_BACKUP_HEADER+out;
}
