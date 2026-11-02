/* SPDX-License-Identifier: GPL-2.0-only
 * SHA-256 per FIPS 180-4. No allocation or platform crypto dependencies. */
#include "internal.h"
static void (*poll_fn)(void *);
static void *poll_context;
void bw_set_poll(void (*fn)(void *),void *ctx) {poll_fn=fn;poll_context=ctx;}
void bw_poll(void) {if(poll_fn)poll_fn(poll_context);}
const char *bw_base_hash(void) {return BW_BASE;}
int bw_test_build(void) {
#ifdef BW_TEST_BASE
    return 1;
#else
    return 0;
#endif
}
void bw_zero(void *v, size_t n) { uint8_t *p=v; while(n--) *p++=0; }
void bw_copy(void *v, const void *w, size_t n) {
    uint8_t *p=v; const uint8_t *q=w; while(n--) *p++=*q++;
}
int bw_equal(const void *v,const void *w,size_t n) {
    const uint8_t *p=v,*q=w; uint8_t x=0; while(n--) x|=*p++ ^ *q++; return x==0;
}
int bw_string(const char *a,const char *b) {
    while(*a && *a==*b) { a++; b++; } return *a==*b;
}
int bw_fail(char *out,const char *s) {
    size_t i=0; if(out) { for(;s[i] && i<BW_ERROR_SIZE-1;i++) out[i]=s[i]; out[i]=0; }
    return -1;
}
uint32_t bw_be32(const uint8_t *p) { return (uint32_t)p[0]<<24 | (uint32_t)p[1]<<16 | (uint32_t)p[2]<<8 | p[3]; }
uint32_t bw_le32(const uint8_t *p) { return bw_be32((uint8_t[]){p[3],p[2],p[1],p[0]}); }
int bw_hex64(const char *p) {
    size_t i; if(!p) return 0;
    for(i=0;i<64;i++) if(!((p[i]>='0' && p[i]<='9')||(p[i]>='a' && p[i]<='f'))) return 0;
    return p[64]==0;
}
int bw_decimal(const uint8_t *p,size_t n,uint64_t *out) {
    uint64_t v=0; size_t i; if(!n || n>20) return -1;
    for(i=0;i<n;i++) { uint32_t d=p[i]-'0'; if(d>9 || v>(UINT64_MAX-d)/10) return -1; v=v*10+d; }
    *out=v; return 0;
}
static uint32_t crc_run(const void *v,size_t n,uint32_t c) {
    const uint8_t *p=v; unsigned j;
    while(n--) { c^=*p++; for(j=0;j<8;j++) c=(c>>1)^((0u-(c&1u))&0xedb88320u);if(!(n&0x3fffu))bw_poll(); }
    return c;
}
uint32_t bw_crc32(const void *v,size_t n) {return crc_run(v,n,0xffffffffu)^0xffffffffu;}
/* Linux/JFFS2 crc32(0, ...): initial zero, NO final complement. */
uint32_t bw_crc32_linux(const void *v,size_t n) {return crc_run(v,n,0);}
static uint32_t rotr(uint32_t x,unsigned n) { return (x>>n)|(x<<(32-n)); }
static void transform(struct bw_hash *s,const uint8_t *p) {
    static const uint32_t k[64]={
        0x428a2f98,0x71374491,0xb5c0fbcf,0xe9b5dba5,0x3956c25b,0x59f111f1,0x923f82a4,0xab1c5ed5,
        0xd807aa98,0x12835b01,0x243185be,0x550c7dc3,0x72be5d74,0x80deb1fe,0x9bdc06a7,0xc19bf174,
        0xe49b69c1,0xefbe4786,0x0fc19dc6,0x240ca1cc,0x2de92c6f,0x4a7484aa,0x5cb0a9dc,0x76f988da,
        0x983e5152,0xa831c66d,0xb00327c8,0xbf597fc7,0xc6e00bf3,0xd5a79147,0x06ca6351,0x14292967,
        0x27b70a85,0x2e1b2138,0x4d2c6dfc,0x53380d13,0x650a7354,0x766a0abb,0x81c2c92e,0x92722c85,
        0xa2bfe8a1,0xa81a664b,0xc24b8b70,0xc76c51a3,0xd192e819,0xd6990624,0xf40e3585,0x106aa070,
        0x19a4c116,0x1e376c08,0x2748774c,0x34b0bcb5,0x391c0cb3,0x4ed8aa4a,0x5b9cca4f,0x682e6ff3,
        0x748f82ee,0x78a5636f,0x84c87814,0x8cc70208,0x90befffa,0xa4506ceb,0xbef9a3f7,0xc67178f2};
    uint32_t w[64],a,b,c,d,e,f,g,h,t1,t2; unsigned i;
    for(i=0;i<16;i++) w[i]=bw_be32(p+4*i);
    for(i=16;i<64;i++) {
        uint32_t x=w[i-15],y=w[i-2];
        w[i]=w[i-16]+(rotr(x,7)^rotr(x,18)^(x>>3))+w[i-7]+(rotr(y,17)^rotr(y,19)^(y>>10));
    }
    a=s->h[0];b=s->h[1];c=s->h[2];d=s->h[3];e=s->h[4];f=s->h[5];g=s->h[6];h=s->h[7];
    for(i=0;i<64;i++) {
        t1=h+(rotr(e,6)^rotr(e,11)^rotr(e,25))+((e&f)^(~e&g))+k[i]+w[i];
        t2=(rotr(a,2)^rotr(a,13)^rotr(a,22))+((a&b)^(a&c)^(b&c));
        h=g;g=f;f=e;e=d+t1;d=c;c=b;b=a;a=t1+t2;
    }
    s->h[0]+=a;s->h[1]+=b;s->h[2]+=c;s->h[3]+=d;s->h[4]+=e;s->h[5]+=f;s->h[6]+=g;s->h[7]+=h;
}
void bw_hash_init(struct bw_hash *s) {
    static const uint32_t iv[8]={0x6a09e667,0xbb67ae85,0x3c6ef372,0xa54ff53a,0x510e527f,0x9b05688c,0x1f83d9ab,0x5be0cd19};
    bw_copy(s->h,iv,sizeof(iv));s->bytes=0;s->used=0;
}
void bw_hash_add(struct bw_hash *s,const void *v,size_t n) {
    const uint8_t *p=v; s->bytes+=n;
    while(n) {
        size_t z=64-s->used; if(z>n) z=n; bw_copy(s->block+s->used,p,z);
        s->used+=z;p+=z;n-=z;
        if(s->used==64) { transform(s,s->block);s->used=0;if(!((s->bytes-n)&0x3fffu))bw_poll(); }
    }
}
void bw_hash_end(struct bw_hash *s,char out[65]) {
    uint64_t bits=s->bytes*8; unsigned i; static const char hex[]="0123456789abcdef";
    s->block[s->used++]=0x80;
    if(s->used>56) { while(s->used<64) s->block[s->used++]=0;transform(s,s->block);s->used=0; }
    while(s->used<56) s->block[s->used++]=0;
    for(i=0;i<8;i++) s->block[63-i]=(uint8_t)(bits>>(8*i));
    transform(s,s->block);
    for(i=0;i<32;i++) { unsigned v=(s->h[i/4]>>(24-8*(i%4)))&255;out[2*i]=hex[v>>4];out[2*i+1]=hex[v&15]; }
    out[64]=0;
}
void bw_sha256(const void *p,size_t n,char out[65]) {
    struct bw_hash s;bw_hash_init(&s);bw_hash_add(&s,p,n);bw_hash_end(&s,out);
}
