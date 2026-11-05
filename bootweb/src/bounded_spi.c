/* SPDX-License-Identifier: GPL-2.0-only
 * Bounded replacement for svn308776's 0x53918 SPI FIFO engine. The original
 * has four unbounded waits and masks completion errors by returning zero.
 * Registers/configuration below come from that exact measured function.
 */
#include "bounded_spi.h"
_Static_assert(offsetof(struct bw_spi_op,address_bytes)==8,"spi_mem_op address");
_Static_assert(offsetof(struct bw_spi_op,address)==16,"spi_mem_op address value");
_Static_assert(offsetof(struct bw_spi_op,dummy_bytes)==24,"spi_mem_op dummy");
_Static_assert(offsetof(struct bw_spi_op,data_width)==32,"spi_mem_op data width");
_Static_assert(offsetof(struct bw_spi_op,direction)==36,"spi_mem_op direction");
_Static_assert(offsetof(struct bw_spi_op,length)==40,"spi_mem_op length");
_Static_assert(offsetof(struct bw_spi_op,data)==48,"spi_mem_op buffer");
struct deadline {uint64_t started;uint32_t polls;};
int bw_bounded_spi_remaining(uint64_t started,uint64_t now,uint32_t maximum,uint32_t *remaining) {
    uint64_t elapsed;
    if(!remaining)return -22;
    *remaining=0;
    if(!maximum||maximum>3000u||now<started)return -22;
    elapsed=now-started;
    if(elapsed>=maximum)return -110;
    *remaining=(uint32_t)(maximum-elapsed);
    if(*remaining>5)*remaining=5;
    return 0;
}
static int expired(const struct bw_spi_io *io,struct deadline *d) {
    uint64_t now=io->milliseconds(io->context);
    if(++d->polls>io->poll_limit||now<d->started||now-d->started>=io->timeout_ms)return 1;
    return 0;
}
static int width(uint32_t n) {return n==1||n==2||n==4;}
int bw_bounded_spi_exec_tracked(const struct bw_spi_io *io,const struct bw_spi_op *op,int *submitted) {
    struct deadline d;uint32_t config=0,control=0,left,at=0,irq,level;int rc=-5,enabled=0;
    uint8_t *data;
    if(submitted)*submitted=0;
    if(!io||!op||!io->read32||!io->write32||!io->milliseconds||!io->select||
       !io->timeout_ms||io->timeout_ms>100||!io->poll_limit||io->poll_limit>1000000u||
       op->command_width!=1||op->address_bytes>4||op->dummy_bytes>32||
       (op->address_bytes&&(!width(op->address_width)||op->address>>((op->address_bytes==4)?32:8*op->address_bytes)))||
       (op->dummy_bytes&&!width(op->dummy_width))||op->length>4096||op->direction>2||
       (op->length&&(!op->data||!op->direction||!width(op->data_width)))||
       (!op->length&&(op->data||op->direction)))return -22;
    d.started=io->milliseconds(io->context);d.polls=0;
    while(io->read32(io->context,4)&1u)if(expired(io,&d))return -110;
    if(io->select(io->context))return -5;
    if(expired(io,&d))return -110;
    if(op->address_bytes) {control=16;config=32u*(op->address_bytes-1u);}
    if(op->dummy_bytes) {
        config|=((op->dummy_bytes/op->dummy_width)<<12)|
            ((8u*(op->dummy_bytes%op->dummy_width)/op->dummy_width)<<8);
        control|=4u;
    }
    if(op->data_width==2)config|=4u;else if(op->data_width==4)config|=5u;
    if(op->address_width>=2)config|=16u;
    if(op->length)control|=op->direction==1?2u:1u;
    io->write32(io->context,8,1);enabled=1;
    io->write32(io->context,32,op->opcode);
    io->write32(io->context,12,115776);
    io->write32(io->context,16,control);
    io->write32(io->context,20,config);
    io->write32(io->context,28,(uint32_t)op->address);
    io->write32(io->context,24,op->length?op->length-1u:0);
    io->write32(io->context,48,255);
    io->write32(io->context,40,55);
    io->write32(io->context,4,1);
    if(submitted)*submitted=1;
    left=op->length;data=op->data;
    while(left) {
        uint32_t count,j;
        if(expired(io,&d)){rc=-110;goto cleanup;}
        irq=io->read32(io->context,44);
        if(irq&0x36u)goto cleanup;
        level=io->read32(io->context,52);
        count=(level>>(op->direction==1?8:16))&31u;
        while(count--&&left) {
            uint32_t value=0,n=left>4?4:left;
            if(op->direction==1) {
                value=io->read32(io->context,56);
                for(j=0;j<n;j++)data[at+j]=(uint8_t)(value>>(8*j));
            } else {
                /* The old driver reads four bytes even for a 1B remainder. */
                for(j=0;j<n;j++)value|=(uint32_t)data[at+j]<<(8*j);
                io->write32(io->context,56,value);
            }
            at+=n;left-=n;
        }
    }
    for(;;) {
        if(expired(io,&d)){rc=-110;goto cleanup;}
        irq=io->read32(io->context,44);
        if(irq&0x36u)goto cleanup;
        if(irq&1u){rc=0;break;}
    }
cleanup:
    if(enabled)io->write32(io->context,8,0);
    return rc;
}
int bw_bounded_spi_exec(const struct bw_spi_io *io,const struct bw_spi_op *op) {
    return bw_bounded_spi_exec_tracked(io,op,0);
}
