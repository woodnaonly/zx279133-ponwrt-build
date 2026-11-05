/* SPDX-License-Identifier: GPL-2.0-only */
#include "internal.h"
static int cost(uint64_t *sum,uint64_t count,uint64_t maximum) {
    if(!maximum||maximum>UINT64_MAX/count||*sum>UINT64_MAX-count*maximum)return -1;
    *sum+=count*maximum;return 0;
}
int bw_uboot_budget_check(const struct bw_uboot_budget *b,uint64_t now,
                          uint64_t *total,char error[BW_ERROR_SIZE]) {
    /* Fixed physical window: 11 erases, 704 pages. Read/hash bounds cover the
     * installed image, protected before/after snapshots, and full readback.
     * There is NO mid-write deadline abort: this gate runs before first erase. */
    const uint64_t blocks=BW_STAGE2_SIZE/BW_ERASE_SIZE;
    const uint64_t pages=BW_STAGE2_SIZE/BW_PAGE_SIZE;
    const uint64_t protected_blocks=(0x20000u+0x20000u+0x60000u+0x400000u)/BW_ERASE_SIZE;
    const uint64_t reads=2*blocks+2*protected_blocks;
    uint64_t elapsed,bound=0;
    if(!error)return -1;error[0]=0;if(total)*total=0;
    if(!b||!total||now<b->power_started_ms||b->power_policy>BW_POWER_CONTINUOUS_CONFIRMED)
        return bw_fail(error,"missing/non-monotonic cold-power timing evidence");
    elapsed=now-b->power_started_ms;
    if(elapsed>UINT64_MAX-b->power_uncertainty_ms)
        return bw_fail(error,"power timing overflow");
    elapsed+=b->power_uncertainty_ms;
    if(cost(&bound,blocks,b->erase_max_ms)||cost(&bound,pages,b->page_max_ms)||
       cost(&bound,reads,b->read_max_ms)||cost(&bound,reads,b->hash_max_ms)||
       cost(&bound,blocks,b->bad_query_max_ms)||
       cost(&bound,blocks+pages+reads+blocks+16,b->service_max_ms)||cost(&bound,1,b->fixed_max_ms))
        return bw_fail(error,"missing/overflowing bounded NAND operation times");
    if(b->power_policy==BW_POWER_CONTINUOUS_CONFIRMED){*total=bound;return 0;}
    if(elapsed>=5u*60000u)return bw_fail(error,"first erase must start before power-on minute 5");
    if(bound>=15u*60000u-elapsed)return bw_fail(error,"write/readback/audit cannot finish before minute 15");
    if(bound+elapsed>20u*60000u-2u*60000u)
        return bw_fail(error,"less than two minutes of power margin");
    *total=bound;return 0;
}
int bw_system_budget_bound(const struct bw_result *r,const struct bw_uboot_budget *b,
                             uint64_t *total,char error[BW_ERROR_SIZE]) {
    static const uint32_t offsets[3]={0xf00000u,0xa00000u,0x200000u};
    static const uint32_t capacities[3]={0xf100000u,0x100000u,0x800000u};
    /* prepare snapshot compare 48 + installed hash 11 + native identity 11
     * + protected before/after 74 + whole boot before/after 32
     * + native detach protected compare 37 = 213 non-image ECC reads. */
    uint64_t bound=0,blocks=0,pages=0,reads=213;unsigned i;
    if(!error)return -1;error[0]=0;if(total)*total=0;
    if(!r||!b||!total||r->kind!=BW_SYSTEM||r->count!=3||r->layout!=BW_LAYOUT_NATIVE241)
        return bw_fail(error,"system budget requires the strict native241 layout");
    for(i=0;i<3;i++) {
        const struct bw_span *s=&r->writes[i];
        if(s->offset!=offsets[i]||s->capacity!=capacities[i]||!s->length||s->length>s->capacity)
            return bw_fail(error,"system budget target offset/capacity/length mismatch");
        blocks+=s->capacity/BW_ERASE_SIZE;
        pages+=(s->length+BW_PAGE_SIZE-1)/BW_PAGE_SIZE;
        reads+=(s->length+BW_ERASE_SIZE-1)/BW_ERASE_SIZE;
        reads+=(s->capacity-s->length+BW_ERASE_SIZE-1)/BW_ERASE_SIZE;
    }
    if(cost(&bound,blocks,b->erase_max_ms)||cost(&bound,pages,b->page_max_ms)||
       cost(&bound,reads,b->read_max_ms)||cost(&bound,reads,b->hash_max_ms)||
       cost(&bound,blocks+2*reads,b->bad_query_max_ms)||
       cost(&bound,blocks+pages+reads,b->service_max_ms)||cost(&bound,1,b->fixed_max_ms))
        return bw_fail(error,"missing/overflowing bounded system operation times");
    *total=bound;return 0;
}
