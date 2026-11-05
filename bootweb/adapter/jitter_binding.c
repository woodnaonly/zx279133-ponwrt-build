/* SPDX-License-Identifier: GPL-2.0-only
 * Bare-metal binding for unmodified Linux v6.6 Jitterentropy 3.4.0.
 * SHA3 conditioning follows the Linux glue, with initialized temporary data.
 * No machine ID, boot clock alone, host seed or xorshift substitutes for RNG.
 */
#include "entropy.h"
#include "sha3.h"
#include <stddef.h>
typedef unsigned long long __u64;
typedef unsigned int __u32;
typedef unsigned char u8;
#include "jitterentropy.h"
static struct {
    const struct bw_entropy_io *io;
    uint64_t begin,last,counter;
    uint32_t calls;
    size_t allocated;
    int failed;
    _Alignas(16) uint8_t memory[4096];
} rng;
static void wipe(void *p,size_t n) {volatile uint8_t *v=p;while(n--)*v++=0;}
static int expired(void) {
    uint64_t now;
    if(rng.failed||!rng.io)return 1;
    now=rng.io->milliseconds(rng.io->context);
    if(now<rng.last||now<rng.begin||now-rng.begin>=rng.io->timeout_ms||++rng.calls>1000000u)rng.failed=1;
    rng.last=now;return rng.failed;
}
void *jent_zalloc(unsigned int n) {
    void *p;size_t size=((size_t)n+15u)&~(size_t)15u;
    if(expired()||!n||size>sizeof(rng.memory)-rng.allocated)return 0;
    p=rng.memory+rng.allocated;rng.allocated+=size;wipe(p,size);return p;
}
void jent_zfree(void *p) {(void)p;/* Entire fixed pool is wiped on every exit. */}
void jent_get_nstime(__u64 *out) {
    uint64_t value;
    *out=0;if(expired())return;
    value=rng.io->counter(rng.io->context);
    if(!value||value<rng.counter){rng.failed=1;return;}
    rng.counter=value;*out=value;
}
int jent_hash_time(void *state,__u64 time,u8 *addtl,unsigned int n,__u64 loops,unsigned int stuck) {
    struct bw_sha3 scratch;uint8_t intermediary[32]={0};__u64 j;
    if(expired()||!state||!addtl||n>128||loops>16)return -1;
    /* Work is intentionally consumed through SHA3. No uninitialized stack
     * bytes are mixed in; they would have no credited entropy anyway. */
    for(j=0;j<loops;j++) {
        bw_sha3_init(&scratch);bw_sha3_add(&scratch,intermediary,sizeof(intermediary));
        bw_sha3_add(&scratch,addtl,n);bw_sha3_end(&scratch,intermediary);
    }
    bw_sha3_add(&scratch,intermediary,sizeof(intermediary));
    if(!stuck)bw_sha3_add(state,&time,sizeof(time));
    wipe(intermediary,sizeof(intermediary));wipe(&scratch,sizeof(scratch));
    return expired()?-1:0;
}
int jent_read_random_block(void *state,char *dst,unsigned int n) {
    uint8_t block[32];unsigned i;
    if(expired()||!state||n>32||(!dst&&n))return -1;
    bw_sha3_end(state,block);bw_sha3_add(state,block,sizeof(block));
    for(i=0;i<n;i++)dst[i]=(char)block[i];wipe(block,sizeof(block));return expired()?-1:0;
}
int bw_entropy_token(const struct bw_entropy_io *io,char token[65]) {
    static const char hex[]="0123456789abcdef";
    struct bw_sha3 pool;struct rand_data *ec=0;uint8_t bytes[32];unsigned i;int rc=-1;
    if(!token)return -1;
    wipe(token,65);wipe(&rng,sizeof(rng));wipe(bytes,sizeof(bytes));bw_sha3_init(&pool);
    if(!io||!io->counter||!io->milliseconds||!io->timeout_ms||io->timeout_ms>90000)goto out;
    rng.io=io;rng.begin=rng.last=io->milliseconds(io->context);
    rc=jent_entropy_init(&pool);if(rc||expired()){rc=-10;goto out;}
    ec=jent_entropy_collector_alloc(2,0,&pool);if(!ec||expired()){rc=-11;goto out;}
    rc=jent_read_entropy(ec,bytes,sizeof(bytes));if(rc||expired()){rc=-12;goto out;}
    for(i=0;i<32;i++){token[i*2]=hex[bytes[i]>>4];token[i*2+1]=hex[bytes[i]&15];}
    token[64]=0;rc=0;
out:
    if(ec)jent_entropy_collector_free(ec);
    if(rc)wipe(token,65);
    wipe(bytes,sizeof(bytes));wipe(&pool,sizeof(pool));wipe(&rng,sizeof(rng));return rc;
}
