/* SPDX-License-Identifier: GPL-2.0-only */
#include "nand_gate.h"
#include "internal.h"
int bw_nand_target_contains(unsigned target,uint32_t off,size_t n) {
    return bw_nand_target_contains_layout(BW_LAYOUT_CURRENT,target,off,n);
}
int bw_nand_target_contains_layout(unsigned layout,unsigned target,uint32_t off,size_t n) {
    static const uint32_t starts[4]={BW_STAGE2_START,0x200000,0xa00000,0xf00000};
    static const uint32_t ends[4]={BW_STAGE2_END,0xa00000,0xb00000,0xff00000};
    uint32_t end;
    if(layout>BW_LAYOUT_RECOVERY237||target>=4||!n)return 0;
    end=ends[target];
    if(target==BW_NAND_ROOT)end=layout==BW_LAYOUT_NATIVE241?BW_FLASH_SIZE:
        layout==BW_LAYOUT_RECOVERY237?0xfc00000u:0xff00000u;
    return off>=starts[target]&&off<end&&n<=end-off;
}
int bw_nand_gate_begin_target(struct bw_nand_gate *g,unsigned target,uint32_t offset,const void *page,uint8_t opcode) {
    return bw_nand_gate_begin_layout(g,BW_LAYOUT_CURRENT,target,offset,page,opcode);
}
int bw_nand_gate_begin_layout(struct bw_nand_gate *g,unsigned layout,unsigned target,uint32_t offset,const void *page,uint8_t opcode) {
    if(!g)return -1;bw_zero(g,sizeof(*g));
    if(!bw_nand_target_contains_layout(layout,target,offset,page?BW_PAGE_SIZE:BW_ERASE_SIZE)||offset%(page?BW_PAGE_SIZE:BW_ERASE_SIZE)||
       (opcode!=2&&opcode!=0x32)){g->failed=1;return -1;}
    g->offset=offset;g->kind=page?2:1;g->load_opcode=opcode;
    if(page)bw_sha256(page,BW_PAGE_SIZE,g->page_sha256);
    return 0;
}
int bw_nand_gate_begin(struct bw_nand_gate *g,uint32_t offset,const void *page,uint8_t opcode) {
    return bw_nand_gate_begin_target(g,BW_NAND_BOOT,offset,page,opcode);
}
int bw_nand_gate_accept(struct bw_nand_gate *g,const struct bw_spi_op *op) {
    char digest[65];int valid=0;
    if(!g||!op)return -1;
    if(g->failed||!g->kind||op->command_width!=1||op->dummy_bytes)goto bad;
    if(op->opcode==6&&g->state==0&&!op->address_bytes&&!op->length&&!op->data&&!op->direction)valid=1;
    else if(g->kind==1&&g->state==1&&op->opcode==0xd8&&op->address_bytes==3&&op->address_width==1&&
            op->address==g->offset/BW_PAGE_SIZE&&!op->length&&!op->direction&&!op->data)valid=1;
    else if(g->kind==2&&g->state==1&&op->opcode==g->load_opcode&&op->address_bytes==2&&op->address_width==1&&
            !op->address&&op->direction==2&&op->length==BW_PAGE_SIZE&&op->data&&op->data_width==(op->opcode==2?1:4)) {
        bw_sha256(op->data,op->length,digest);valid=bw_string(digest,g->page_sha256);
    } else if(g->kind==2&&g->state==2&&op->opcode==0x10&&op->address_bytes==3&&op->address_width==1&&
              op->address==g->offset/BW_PAGE_SIZE&&!op->length&&!op->direction&&!op->data)valid=1;
    if(!valid)goto bad;
    g->state++;return 0;
bad:
    g->failed=1;return -1;
}
int bw_nand_gate_finish(struct bw_nand_gate *g) {
    if(!g||g->failed||!g->kind||g->state!=(g->kind==1?2u:3u))return -1;
    g->kind=0;return 0;
}
