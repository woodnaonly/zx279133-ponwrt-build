/* SPDX-License-Identifier: GPL-2.0-only
 * FIPS 202 SHA3-256 (not Keccak-256). Byte-wise little-endian absorption
 * avoids alignment and host-endian assumptions. No SIMD or dynamic storage.
 */
#include "sha3.h"
static uint64_t rol(uint64_t x,unsigned n) {return n?(x<<n)|(x>>(64-n)):x;}
static void permute(uint64_t s[25]) {
    static const uint64_t rc[24]={
        0x0000000000000001ULL,0x0000000000008082ULL,0x800000000000808aULL,0x8000000080008000ULL,
        0x000000000000808bULL,0x0000000080000001ULL,0x8000000080008081ULL,0x8000000000008009ULL,
        0x000000000000008aULL,0x0000000000000088ULL,0x0000000080008009ULL,0x000000008000000aULL,
        0x000000008000808bULL,0x800000000000008bULL,0x8000000000008089ULL,0x8000000000008003ULL,
        0x8000000000008002ULL,0x8000000000000080ULL,0x000000000000800aULL,0x800000008000000aULL,
        0x8000000080008081ULL,0x8000000000008080ULL,0x0000000080000001ULL,0x8000000080008008ULL};
    static const unsigned rotation[25]={0,1,62,28,27,36,44,6,55,20,3,10,43,25,39,41,45,15,21,8,18,2,61,56,14};
    unsigned round,x,y;uint64_t c[5],d[5],b[25];
    for(round=0;round<24;round++) {
        for(x=0;x<5;x++)c[x]=s[x]^s[x+5]^s[x+10]^s[x+15]^s[x+20];
        for(x=0;x<5;x++)d[x]=c[(x+4)%5]^rol(c[(x+1)%5],1);
        for(y=0;y<5;y++)for(x=0;x<5;x++)s[x+5*y]^=d[x];
        for(y=0;y<5;y++)for(x=0;x<5;x++)b[y+5*((2*x+3*y)%5)]=rol(s[x+5*y],rotation[x+5*y]);
        for(y=0;y<5;y++)for(x=0;x<5;x++)s[x+5*y]=b[x+5*y]^((~b[(x+1)%5+5*y])&b[(x+2)%5+5*y]);
        s[0]^=rc[round];
    }
}
void bw_sha3_init(struct bw_sha3 *s) {unsigned i;for(i=0;i<25;i++)s->state[i]=0;s->used=0;}
static void inject(struct bw_sha3 *s,size_t at,uint8_t value) {s->state[at/8]^=(uint64_t)value<<((at%8)*8);}
void bw_sha3_add(struct bw_sha3 *s,const void *data,size_t n) {
    const uint8_t *p=data;while(n--) {inject(s,s->used++,*p++);if(s->used==136){permute(s->state);s->used=0;}}
}
void bw_sha3_end(struct bw_sha3 *s,uint8_t out[32]) {
    unsigned i;inject(s,s->used,6);inject(s,135,0x80);permute(s->state);
    for(i=0;i<32;i++)out[i]=(uint8_t)(s->state[i/8]>>((i%8)*8));
    bw_sha3_init(s);
}
void bw_sha3_256(const void *data,size_t n,uint8_t out[32]) {struct bw_sha3 s;bw_sha3_init(&s);bw_sha3_add(&s,data,n);bw_sha3_end(&s,out);}
