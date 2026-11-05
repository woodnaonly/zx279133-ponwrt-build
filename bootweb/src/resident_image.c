/* SPDX-License-Identifier: GPL-2.0-only */
#include "resident_image.h"
#include "internal.h"
static const uint8_t original_hook[16]={0xfd,0x7b,0xbf,0xa9,0xfd,3,0,0x91,0xf9,0xfb,0xff,0x97,0xff,0xff,0xff,0x17};
static const uint8_t resident_hook[16]={0x50,0,0,0x58,0,2,0x1f,0xd6,0,0,0x10,0x88,0,0,0,0};
static uint32_t little32(const uint8_t *p) {return p[0]|(uint32_t)p[1]<<8|(uint32_t)p[2]<<16|(uint32_t)p[3]<<24;}
int bw_resident_image_check(const void *data,size_t n,char installed[65]) {
    const uint8_t *p=data,*d;uint32_t tail,i;uint8_t erased[256];char hash[65];struct bw_hash normalized;
    if(!p||!installed||n!=BW_STAGE2_SIZE)return -1;
    bw_sha256(p,n,installed);
    if(bw_string(installed,BW_VENDOR_ABI_SHA256))return 0; /* unmodified vendor */
    d=p+BW_RESIDENT_DESCRIPTOR;tail=little32(d+20);
    if(!bw_equal(p+0xa098,resident_hook,16)||!bw_equal(d,"BW-RESIDENT-V1\0\0",16)||
       little32(d+16)!=1||!tail||tail%64||tail>BW_RESIDENT_DESCRIPTOR-BW_RESIDENT_SLOT||
       !(bw_equal(d+24,"bootweb-resident-v1\0",20)||bw_equal(d+24,"bootweb-native241\0\0\0",20))||d[108])return -1;
    for(i=109;i<BW_RESIDENT_DESCRIPTOR_BYTES;i++)if(d[i])return -1;
    for(i=BW_RESIDENT_SLOT+tail;i<BW_RESIDENT_DESCRIPTOR;i++)if(p[i]!=255)return -1;
    bw_sha256(p+BW_RESIDENT_SLOT,tail,hash);
    if(!bw_equal(hash,d+44,65))return -1;
    bw_hash_init(&normalized);
    bw_hash_add(&normalized,p,0xa098);bw_hash_add(&normalized,original_hook,sizeof(original_hook));
    bw_hash_add(&normalized,p+0xa0a8,BW_RESIDENT_SLOT-0xa0a8);
    for(i=0;i<sizeof(erased);i++)erased[i]=255;
    for(i=BW_RESIDENT_SLOT;i<BW_STAGE2_SIZE;i+=sizeof(erased))bw_hash_add(&normalized,erased,sizeof(erased));
    bw_hash_end(&normalized,hash);
    return bw_string(hash,BW_VENDOR_ABI_SHA256)?1:-1;
}
const char *bw_resident_image_version(const void *data,size_t n) {
    char installed[65];const uint8_t *p=data;
    if(bw_resident_image_check(data,n,installed)!=1)return 0;
    return bw_equal(p+BW_RESIDENT_DESCRIPTOR+24,"bootweb-native241\0\0\0",20)?
        "bootweb-resident-native241-v1":"bootweb-resident-v1";
}
