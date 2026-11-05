/* SPDX-License-Identifier: GPL-2.0-only
 * Exact svn308776 fixed-physical ECC interfaces. Never skip/remap bad blocks.
 * PREFLIGHT builds have no real erase/page binding; only SPI-level emulation.
 */
#if !defined(BW_NATIVE_INSTALL) && !defined(BW_INSTALL_PREFLIGHT)
#error "native NAND adapter requires a distinct installer/preflight artifact"
#endif
#include "native_nand.h"
#include "native_spi.h"
#include "nand_gate.h"
#include "internal.h"
typedef int (*read_fn)(void *,uint64_t,size_t,size_t *,void *);
static uintptr_t ptr(uintptr_t at) {return *(volatile const uintptr_t *)at;}
static uint32_t word(uintptr_t at) {return *(volatile const uint32_t *)at;}
static uint64_t now(struct bw_native_nand *n) {return ((unsigned long (*)(unsigned long))(n->reloc+0x65630))(0);}
static void maximum(uint32_t *v,uint64_t elapsed) {if(elapsed>*v)*v=elapsed>UINT32_MAX?UINT32_MAX:(uint32_t)elapsed;}
static int read_range(uint32_t off,size_t n) {
#ifdef BW_RESIDENT_NATIVE_WRITE
    return n&&n<=BW_ERASE_SIZE&&off<BW_FLASH_SIZE&&n<=BW_FLASH_SIZE-off;
#else
    return n&&n<=BW_ERASE_SIZE&&((off<0x200000u&&n<=0x200000u-off)||(off>=0xb00000u&&off<0xf00000u&&n<=0xf00000u-off));
#endif
}
static int target_at(struct bw_native_nand *n,uint32_t off,size_t bytes) {
    unsigned target;
    for(target=0;target<4;target++)if((n->targets&(1u<<target))&&bw_nand_target_contains_layout(n->layout,target,off,bytes))return (int)target;
    return -1;
}
static int bad(void *context,uint32_t off) {
    struct bw_native_nand *n=context;uint64_t start=now(n),elapsed;int rc;
    if(n->failed||off%BW_ERASE_SIZE||off>=BW_FLASH_SIZE||bw_native_spi_io_begin(100))return -1;
    rc=((int (*)(void *,uint64_t))(n->reloc+0x192c0))((void *)n->mtd,off);elapsed=now(n)-start;
    if(bw_native_spi_io_end())rc=-1;
    maximum(&n->max_bad_ms,elapsed);
    if(bw_native_spi_failed()||elapsed>100||rc<0||rc>1){n->failed=1;return -1;}
    return rc;
}
static int read_ecc(void *context,uint32_t off,void *buffer,size_t len) {
    struct bw_native_nand *n=context;uint64_t start=now(n),elapsed;size_t done=0;int rc;
    if(n->failed||!buffer||!read_range(off,len)||bw_native_spi_io_begin(3000))return -1;
    rc=((read_fn)(n->reloc+0x18d34))((void *)n->mtd,off,len,&done,buffer);elapsed=now(n)-start;
    if(bw_native_spi_io_end())rc=-1;
    n->reads++;maximum(&n->max_read_ms,elapsed);
    if(bw_native_spi_failed()||elapsed>3000||done!=len||(rc!=0&&rc!=-117)) {
        n->failed=1;
        ((int (*)(const char *,...))(n->reloc+0x6677c))("BW_INSTALL_ECC_ERROR off=%x requested=%lu returned=%lu rc=%d elapsed_ms=%llu\n",
            off,(unsigned long)len,(unsigned long)done,rc,(unsigned long long)elapsed);return -1;
    }
    if(rc==-117)n->corrected_reads++;
    return 0;
}
static int erase_block(void *context,uint32_t off) {
    struct bw_native_nand *n=context;_Alignas(16) uint8_t erase[80];uint64_t start,elapsed;int rc,gate,target=target_at(n,off,BW_ERASE_SIZE);
    if(n->failed||target<0||off%BW_ERASE_SIZE)return -1;
    bw_zero(erase,sizeof(erase));*(uintptr_t *)erase=n->mtd;*(uint64_t *)(erase+8)=off;*(uint64_t *)(erase+16)=BW_ERASE_SIZE;
#ifdef BW_INSTALL_PREFLIGHT
#ifdef BW_RESIDENT_NATIVE_WRITE
    if(bw_native_spi_dry_layout_begin(n->layout,(unsigned)target,off,0))return -1;
#else
    if(bw_native_spi_dry_begin(off,0))return -1;
#endif
#else
#ifdef BW_RESIDENT_NATIVE_WRITE
    if(bw_native_spi_write_layout_begin(n->layout,(unsigned)target,off,0))return -1;
#else
    if(bw_native_spi_write_begin(off,0))return -1;
#endif
#endif
    start=now(n);rc=((int (*)(void *,void *))(n->reloc+0x18cc0))((void *)n->mtd,erase);elapsed=now(n)-start;
#ifdef BW_INSTALL_PREFLIGHT
    gate=bw_native_spi_dry_end();
#else
    gate=bw_native_spi_write_end();
#endif
    maximum(&n->max_erase_ms,elapsed);
    if(rc||gate||bw_native_spi_failed()||elapsed>2100||*(uint64_t *)(erase+24)!=UINT64_MAX){n->failed=1;return -1;}
    n->erases++;return 0;
}
static int write_page(void *context,uint32_t off,const void *page) {
    struct bw_native_nand *n=context;uint64_t start,elapsed;size_t done=0,i;int rc,gate,target=target_at(n,off,BW_PAGE_SIZE);
    if(n->failed||!page||target<0||off%BW_PAGE_SIZE)return -1;
    for(i=0;i<BW_PAGE_SIZE&&((const uint8_t *)page)[i]==255;i++){}
    if(i==BW_PAGE_SIZE){n->skipped_ff++;return 0;}
#ifdef BW_INSTALL_PREFLIGHT
#ifdef BW_RESIDENT_NATIVE_WRITE
    if(bw_native_spi_dry_layout_begin(n->layout,(unsigned)target,off,page))return -1;
#else
    if(bw_native_spi_dry_begin(off,page))return -1;
#endif
#else
#ifdef BW_RESIDENT_NATIVE_WRITE
    if(bw_native_spi_write_layout_begin(n->layout,(unsigned)target,off,page))return -1;
#else
    if(bw_native_spi_write_begin(off,page))return -1;
#endif
#endif
    start=now(n);rc=((read_fn)(n->reloc+0x18dfc))((void *)n->mtd,off,BW_PAGE_SIZE,&done,(void *)page);elapsed=now(n)-start;
#ifdef BW_INSTALL_PREFLIGHT
    gate=bw_native_spi_dry_end();
#else
    gate=bw_native_spi_write_end();
#endif
    maximum(&n->max_page_ms,elapsed);
    if(rc||gate||bw_native_spi_failed()||elapsed>300||done!=BW_PAGE_SIZE){n->failed=1;return -1;}
    n->pages++;return 0;
}
int bw_native_nand_bind(struct bw_native_nand *n,uintptr_t reloc,uintptr_t mtd,struct bw_port *port) {
    uintptr_t nand;
    if(!n||!port||reloc!=0x9ff3c000u||mtd<0x9f6ee080u||mtd>0xa0000000u-384)return -1;
    if(ptr(mtd+8)!=BW_FLASH_SIZE||word(mtd+16)!=BW_ERASE_SIZE||word(mtd+20)!=BW_PAGE_SIZE||word(mtd+28)!=128||
       ptr(mtd+112)!=reloc+0x25824||ptr(mtd+128)||ptr(mtd+136)||ptr(mtd+152)!=reloc+0x26220||
       ptr(mtd+160)!=reloc+0x25fa0||ptr(mtd+256)!=reloc+0x2592c)return -1;
    nand=ptr(mtd+312);
    if(nand<0x9f6ee080u||nand>0xa0000000u-216||ptr(nand)!=mtd||word(nand+12)!=2048||word(nand+16)!=128||
       word(nand+20)!=64||word(nand+24)!=2048||word(nand+32)!=1||word(nand+36)!=1||word(nand+40)!=1)return -1;
    bw_zero(n,sizeof(*n));bw_zero(port,sizeof(*port));n->reloc=reloc;n->mtd=mtd;n->targets=1u<<BW_NAND_BOOT;
    if(bw_native_spi_attach(reloc,mtd))return -1;
    port->context=n;port->is_bad=bad;port->read=read_ecc;port->erase=erase_block;port->write_page=write_page;
    ((int (*)(const char *,...))(reloc+0x6677c))(
        "BW_NATIVE_IO_BOUNDS read_ms=3000 bad_ms=100 erase_ms=2100 page_ms=300 complete_call_gates=1 proof_claimed=0\n");
    /* No capability/evidence bit is set here. No reset or shell is bound. */
    return 0;
}
#ifdef BW_RESIDENT_NATIVE_WRITE
int bw_native_nand_enable_system(struct bw_native_nand *n,unsigned layout) {
    if(!n||n->failed||n->reloc!=0x9ff3c000u||!bw_native_spi_ready()||layout!=BW_LAYOUT_NATIVE241)return -1;
    /* A Web service cannot arm BOOT. Its installer remains a separate binary. */
    n->layout=layout;n->targets=(1u<<BW_NAND_KERNEL)|(1u<<BW_NAND_DTB)|(1u<<BW_NAND_ROOT);return 0;
}
int bw_native_nand_system_ready(const struct bw_native_nand *n) {
    return n&&!n->failed&&n->reloc==0x9ff3c000u&&n->layout==BW_LAYOUT_NATIVE241&&
        n->targets==((1u<<BW_NAND_KERNEL)|(1u<<BW_NAND_DTB)|(1u<<BW_NAND_ROOT))&&bw_native_spi_ready();
}
#endif
int bw_native_nand_release(struct bw_native_nand *n) {
    int rc;
    if(!n||!n->reloc)return -1;rc=bw_native_spi_detach();
    ((int (*)(const char *,...))(n->reloc+0x6677c))("BW_INSTALL_NAND_RELEASE rc=%d reads=%u corrected_reads=%u erases=%u pages=%u skipped_ff=%u max_read_ms=%u max_bad_ms=%u max_erase_ms=%u max_page_ms=%u failed=%d\n",
        rc,n->reads,n->corrected_reads,n->erases,n->pages,n->skipped_ff,n->max_read_ms,n->max_bad_ms,n->max_erase_ms,n->max_page_ms,n->failed);
    return rc||n->failed?-1:0;
}
