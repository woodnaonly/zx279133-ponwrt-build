/* SPDX-License-Identifier: GPL-2.0-only
 * Live-probed svn308776 controller binding, deliberately READ ONLY. Replaces
 * the RAM spi_controller_mem_ops.exec_op pointer, not persistent instructions.
 */
#if (!defined(BW_RAM_READONLY) && !defined(BW_NATIVE_INSTALL)) || !defined(BW_NATIVE_BOUNDED_SPI)
#error "bounded SPI requires a distinct read-only trial or native installer target"
#endif
#if defined(BW_RAM_READONLY) && defined(BW_NATIVE_INSTALL)
#error "the immutable read-only artifact cannot become a native installer"
#endif
#include "bounded_spi.h"
#include "nand_gate.h"
#include "native_spi.h"
#include "internal.h"
struct bw_spi_pin {uint32_t offset,length;const char *sha256;};
#include "bw_spi_pins.h"
typedef int (*print_fn)(const char *,...);
static struct {
    uintptr_t reloc,mtd,nand,mem,device,parent,private,slot,original,mmio;
    uint64_t busy_end;
    uint64_t io_started;
    uint32_t io_maximum;
    uint32_t calls,status_reads,max_ms,denied,busy_polls;
    int attached,failed;
    uint8_t read_opcode;
#if defined(BW_NATIVE_NAND_DRYRUN) || defined(BW_NATIVE_INSTALL)
    struct bw_nand_gate gate;
    int dry,armed;
    uint32_t hardware_attempts,hardware_completed;
#endif
} spi;
static uintptr_t ptr(uintptr_t at) {return *(volatile const uintptr_t *)at;}
static int resident(uintptr_t at,size_t n) {return at>=0x9f6ee080u&&n&&at<=0xa0000000u-n;}
static uint64_t now(void *unused) {(void)unused;return ((unsigned long (*)(unsigned long))(spi.reloc+0x65630))(0);}
static uint32_t read32(void *unused,uint32_t off) {
    uint32_t v;(void)unused;v=*(volatile const uint32_t *)(spi.mmio+off);__asm__ volatile("dmb sy":::"memory");return v;
}
static void write32(void *unused,uint32_t off,uint32_t value) {
    (void)unused;__asm__ volatile("dmb sy":::"memory");*(volatile uint32_t *)(spi.mmio+off)=value;
}
static int select_chip(void *unused) {
    /* This device's measured CS syscon pointer is NULL. Original claim_bus
     * already sets its native controller selection; never invent a GPIO CS. */
    (void)unused;return ptr(spi.private+2448)==0?0:-1;
}
static int allowed(const struct bw_spi_op *op) {
    if(op->opcode==spi.read_opcode)return op->direction==1&&op->length>0;
    if(op->opcode==0x13)return !op->length&&op->address_bytes==3;
    if(op->opcode==0x0f)return op->direction==1&&op->length==1&&op->address_bytes==1;
    /* On-die ECC/quad feature changes use the SAME existing read path. These
     * are volatile chip configuration, not page program, erase or bad marking. */
    if(op->opcode==0x1f)return op->direction==2&&op->length==1&&op->address_bytes==1&&op->address==0xb0;
    return 0;
}
static int bounded_exec(void *mem,const struct bw_spi_op *op) {
    struct bw_spi_io io={0,read32,write32,now,select_chip,5,100000};uint64_t before,elapsed;int rc;
    if(spi.failed||!spi.attached||(uintptr_t)mem!=spi.mem||!op)return -5;
    if(spi.io_maximum&&bw_bounded_spi_remaining(spi.io_started,now(0),spi.io_maximum,&io.timeout_ms)) {
        spi.failed=1;return -110;
    }
#if defined(BW_NATIVE_NAND_DRYRUN) || defined(BW_NATIVE_INSTALL)
    int mutation=op->opcode==6||op->opcode==0xd8||op->opcode==0x10||op->opcode==2||op->opcode==0x32;
    if(mutation) {
        int valid=spi.armed&&!bw_nand_gate_accept(&spi.gate,op);
        if(!valid)spi.failed=1;
        if(spi.dry||!valid) {
#ifndef BW_INSTALL_PREFLIGHT
            ((print_fn)(spi.reloc+0x6677c))("BW_NAND_DRY_EMULATED opcode=%x row_or_column=%llx length=%u valid=%d hardware_sent=0\n",
                op->opcode,(unsigned long long)op->address,op->length,valid);
#else
            /* Full-window preflight has 2,134 emulated commands. Printing
             * every one at 115200 unnecessarily consumes the HTTP deadline.
             * Aggregate counts plus per-block/error records remain retained. */
            if(!valid)((print_fn)(spi.reloc+0x6677c))(
                "BW_NAND_PREFLIGHT_DENIED opcode=%x row_or_column=%llx length=%u hardware_sent=0\n",
                op->opcode,(unsigned long long)op->address,op->length);
#endif
            return valid?0:-1;
        }
    }
#endif
    if(!allowed(op)
#ifdef BW_NATIVE_INSTALL
       &&!mutation
#endif
       ) {
        spi.denied++;spi.failed=1;
        ((print_fn)(spi.reloc+0x6677c))("BW_BOUNDED_SPI_DENIED opcode=%x addr=%llx length=%u direction=%u\n",
            op->opcode,(unsigned long long)op->address,op->length,op->direction);return -1;
    }
    if(op->opcode==0x13){spi.busy_end=now(0)+10;spi.busy_polls=0;}
#ifdef BW_NATIVE_INSTALL
    if(op->opcode==0x10||op->opcode==0xd8){spi.busy_end=now(0)+(op->opcode==0x10?100:2000);spi.busy_polls=0;}
#endif
    before=now(0);
#ifdef BW_NATIVE_INSTALL
    int submitted=0;
    rc=bw_bounded_spi_exec_tracked(&io,op,&submitted);
    /* A failed operation may still have been submitted. Conversely initial
     * busy or validation errors must never be logged as hardware mutations. */
    if(submitted&&(op->opcode==0x10||op->opcode==0xd8))spi.hardware_attempts++;
#else
    rc=bw_bounded_spi_exec(&io,op);
#endif
    elapsed=now(0)-before;
    if(spi.io_maximum) {
        uint32_t left;
        if(bw_bounded_spi_remaining(spi.io_started,now(0),spi.io_maximum,&left))rc=-110;
    }
#ifdef BW_NATIVE_INSTALL
    if(!rc&&(op->opcode==0x10||op->opcode==0xd8))spi.hardware_completed++;
#endif
    spi.calls++;if(elapsed>spi.max_ms)spi.max_ms=(uint32_t)elapsed;
    if(!rc&&op->opcode==0x0f&&op->address==0xc0) {
        uint8_t status=*(const uint8_t *)op->data;spi.status_reads++;
        if(status&1) {
            if(!spi.busy_end)spi.busy_end=now(0)+100;
            if(now(0)>=spi.busy_end||++spi.busy_polls>100000u)rc=-110;
        } else {spi.busy_end=0;spi.busy_polls=0;}
    }
    if(rc) {
        spi.failed=1;
        ((print_fn)(spi.reloc+0x6677c))("BW_BOUNDED_SPI_ERROR opcode=%x rc=%d duration_ms=%llu\n",
            op->opcode,rc,(unsigned long long)elapsed);
    }
    return rc;
}
int bw_native_spi_attach(uintptr_t reloc,uintptr_t mtd) {
    unsigned i;char hash[65];uintptr_t driver,ops,mem_ops,read_op;
    if(spi.attached||reloc!=0x9ff3c000u||!resident(mtd,384))return -1;
    for(i=0;i<sizeof(bw_spi_pins)/sizeof(bw_spi_pins[0]);i++) {
        const struct bw_spi_pin *p=&bw_spi_pins[i];
        bw_sha256((const void *)(reloc+p->offset),p->length,hash);
        if(!bw_string(hash,p->sha256))return -1;
    }
    spi.reloc=reloc;spi.mtd=mtd;spi.nand=ptr(mtd+312);
    if(!resident(spi.nand,216)||ptr(spi.nand)!=mtd)return -1;
    spi.mem=ptr(spi.nand+80);if(!resident(spi.mem,64))return -1;
    spi.device=ptr(spi.mem);if(!resident(spi.device,160))return -1;
    spi.parent=ptr(spi.device+56);if(!resident(spi.parent,160))return -1;
    spi.private=ptr(spi.parent+64);if(!resident(spi.private,2480))return -1;
    driver=ptr(spi.parent);if(driver!=reloc+0x98158)return -1;
    ops=ptr(driver+104);if(ops!=reloc+0x72f30)return -1;
    mem_ops=ptr(ops+32);if(mem_ops!=reloc+0x72f18)return -1;
    /* These indirect edges are part of the bounded call-chain argument.
     * A changed adjust/support/claim callback invalidates that argument even
     * if the final FIFO exec pointer still happens to match. */
    if(ptr(ops)!=reloc+0x53738||ptr(ops+8)!=reloc+0x5355c||ptr(ops+16)||ptr(ops+24)||
       ptr(ops+40)!=reloc+0x5370c||ptr(ops+48)!=reloc+0x53564||ptr(ops+56)||
       ptr(mem_ops)!=reloc+0x53554||ptr(mem_ops+8)!=reloc+0x5356c)return -1;
    spi.slot=mem_ops+16;spi.original=ptr(spi.slot);if(spi.original!=reloc+0x53918)return -1;
    spi.mmio=ptr(spi.private+2432);
    if(spi.mmio!=0x10d0f000u||ptr(spi.private+2448)!=0)return -1;
    read_op=ptr(spi.nand+104);
    if(!resident(read_op,56))return -1;
    spi.read_opcode=*(const uint8_t *)(read_op+1);
    if(spi.read_opcode!=3&&spi.read_opcode!=0x0b&&spi.read_opcode!=0x3b&&spi.read_opcode!=0x6b&&spi.read_opcode!=0xbb&&spi.read_opcode!=0xeb)return -1;
    *(volatile uintptr_t *)spi.slot=(uintptr_t)bounded_exec;__asm__ volatile("dsb sy\n\tisb":::"memory");
    spi.attached=1;
    if(ptr(spi.slot)!=(uintptr_t)bounded_exec)return -1;
    ((print_fn)(reloc+0x6677c))("BW_BOUNDED_SPI_ATTACHED mmio=%lx read_opcode=%x operation_timeout_ms=5 read_ready_timeout_ms=10 writes=%d\n",
         (unsigned long)spi.mmio,spi.read_opcode,
#ifdef BW_NATIVE_INSTALL
         1
#else
         0
#endif
         );
    return 0;
}
int bw_native_spi_failed(void) {return spi.failed;}
int bw_native_spi_ready(void) {
    return spi.attached&&!spi.failed&&!spi.io_maximum
#if defined(BW_NATIVE_NAND_DRYRUN) || defined(BW_NATIVE_INSTALL)
        &&!spi.armed
#endif
        &&ptr(spi.slot)==(uintptr_t)bounded_exec;
}
int bw_native_spi_io_begin(uint32_t maximum) {
    uint32_t left;
    if(spi.failed||!spi.attached||spi.io_maximum)return -1;
    spi.io_started=now(0);
    if(bw_bounded_spi_remaining(spi.io_started,spi.io_started,maximum,&left))return -1;
    spi.io_maximum=maximum;return 0;
}
int bw_native_spi_io_end(void) {
    uint32_t left;int rc;
    if(!spi.io_maximum)return -1;
    rc=spi.failed||bw_bounded_spi_remaining(spi.io_started,now(0),spi.io_maximum,&left);
    spi.io_maximum=0;
    if(rc)spi.failed=1;
    return rc?-1:0;
}
#if defined(BW_NATIVE_NAND_DRYRUN) || defined(BW_NATIVE_INSTALL)
static int operation_begin(unsigned layout,unsigned target,uint32_t offset,const void *page,int dry) {
    uintptr_t program;
    uint8_t opcode;
#ifndef BW_RESIDENT_NATIVE_WRITE
    if(target!=BW_NAND_BOOT||layout!=BW_LAYOUT_CURRENT)return -1;
#endif
    if(spi.failed||!spi.attached||spi.armed)return -1;
    program=ptr(spi.nand+112);
    if(!resident(program,56))return -1;
    opcode=*(const uint8_t *)(program+1);
    if(bw_native_spi_io_begin(page?300u:2100u))return -1;
    if(bw_nand_gate_begin_layout(&spi.gate,layout,target,offset,page,opcode)) {
        (void)bw_native_spi_io_end();return -1;
    }
    spi.dry=dry;spi.armed=1;spi.hardware_attempts=0;spi.hardware_completed=0;
    return 0;
}
static int operation_end(void) {
    int rc;uint32_t kind=spi.gate.kind;
    if(!spi.armed)return -1;
    rc=bw_native_spi_io_end();
    if(spi.failed||bw_nand_gate_finish(&spi.gate))rc=1;
#ifdef BW_INSTALL_PREFLIGHT
    if(rc||kind==1)
#elif defined(BW_RESIDENT_NATIVE_WRITE)
    if(rc||kind==1||!(spi.gate.offset&(BW_ERASE_SIZE-1u)))
#endif
    ((print_fn)(spi.reloc+0x6677c))("BW_NAND_%s_END rc=%d kind=%s offset=%x wren=%u loads=%u executes=%u hardware_mutations=%u\n",
        spi.dry?"DRY":"REAL",rc,kind==2?"page":"erase",spi.gate.offset,
        spi.gate.state>0,kind==2&&spi.gate.state>1,spi.gate.state==(kind==2?3u:2u),spi.hardware_attempts);
    if(!spi.dry
#ifdef BW_RESIDENT_NATIVE_WRITE
       &&(rc||kind==1||!(spi.gate.offset&(BW_ERASE_SIZE-1u)))
#endif
       )((print_fn)(spi.reloc+0x6677c))(
        "BW_NAND_REAL_HARDWARE attempts=%u controller_completed=%u\n",spi.hardware_attempts,spi.hardware_completed);
    spi.armed=0;spi.dry=0;if(rc)spi.failed=1;return rc?-1:0;
}
#endif
#ifdef BW_NATIVE_NAND_DRYRUN
int bw_native_spi_dry_begin(uint32_t offset,const void *page) {return operation_begin(BW_LAYOUT_CURRENT,BW_NAND_BOOT,offset,page,1);}
int bw_native_spi_dry_end(void) {return operation_end();}
#ifdef BW_RESIDENT_NATIVE_WRITE
int bw_native_spi_dry_target_begin(unsigned target,uint32_t offset,const void *page) {return operation_begin(BW_LAYOUT_CURRENT,target,offset,page,1);}
int bw_native_spi_dry_layout_begin(unsigned layout,unsigned target,uint32_t offset,const void *page) {return operation_begin(layout,target,offset,page,1);}
#endif
#endif
#ifdef BW_NATIVE_INSTALL
int bw_native_spi_write_begin(uint32_t offset,const void *page) {return operation_begin(BW_LAYOUT_CURRENT,BW_NAND_BOOT,offset,page,0);}
int bw_native_spi_write_end(void) {return operation_end();}
#ifdef BW_RESIDENT_NATIVE_WRITE
int bw_native_spi_write_target_begin(unsigned target,uint32_t offset,const void *page) {return operation_begin(BW_LAYOUT_CURRENT,target,offset,page,0);}
int bw_native_spi_write_layout_begin(unsigned layout,unsigned target,uint32_t offset,const void *page) {return operation_begin(layout,target,offset,page,0);}
#endif
#endif
int bw_native_spi_detach(void) {
    int rc;
    if(!spi.attached)return 0;
    rc=spi.failed;
    if(ptr(spi.slot)!=(uintptr_t)bounded_exec)rc=1;
    else {*(volatile uintptr_t *)spi.slot=spi.original;__asm__ volatile("dsb sy\n\tisb":::"memory");}
    ((print_fn)(spi.reloc+0x6677c))("BW_BOUNDED_SPI_DETACHED rc=%d calls=%u status_reads=%u max_ms=%u denied=%u pointer_restored=%d\n",
        rc,spi.calls,spi.status_reads,spi.max_ms,spi.denied,ptr(spi.slot)==spi.original);
    spi.attached=0;return rc?-1:0;
}
