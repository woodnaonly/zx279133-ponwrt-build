/* SPDX-License-Identifier: GPL-2.0-only */
#include "install_image.h"
#include "resident_image.h"
#include "internal.h"
static int overlap(const void *a,size_t an,const void *b,size_t bn) {
    uintptr_t aa=(uintptr_t)a,bb=(uintptr_t)b;
    if(!a||!b||!an||!bn||aa>UINTPTR_MAX-an||bb>UINTPTR_MAX-bn)return -1;
    return aa<bb+bn&&bb<aa+an;
}
int bw_install_prepare(const struct bw_install_parts *p,const void *original,size_t original_bytes,
                       void *output,size_t capacity,size_t *package_bytes,char error[BW_ERROR_SIZE]) {
    static const uint8_t hook[16]={0x50,0,0,0x58,0,2,0x1f,0xd6,0,0,0x10,0x88,0,0,0,0};
    uint8_t *image,*package=output;size_t total;struct bw_identity id;struct bw_result result;char digest[65];
    if(package_bytes)*package_bytes=0;if(!error)return -1;error[0]=0;
    if(!p||!package_bytes||original_bytes!=BW_STAGE2_SIZE||!p->prefix_bytes||p->prefix_bytes>16384||
       p->prefix_bytes%512||!p->suffix_bytes||p->suffix_bytes>16384||p->suffix_bytes%512||
       !p->tail_bytes||p->tail_bytes%64||p->tail_bytes>BW_RESIDENT_DESCRIPTOR-BW_RESIDENT_SLOT||
       p->descriptor_bytes!=BW_RESIDENT_DESCRIPTOR_BYTES||!bw_hex64(p->original_sha256)||
       !bw_hex64(p->candidate_sha256)||!bw_hex64(p->package_sha256))
        return bw_fail(error,"invalid bounded resident installation parts");
    total=p->prefix_bytes+BW_STAGE2_SIZE+p->suffix_bytes;
    if(capacity<total||overlap(original,original_bytes,output,total)!=0||
       overlap(p->prefix,p->prefix_bytes,output,total)!=0||overlap(p->tail,p->tail_bytes,output,total)!=0||
       overlap(p->descriptor,p->descriptor_bytes,output,total)!=0||overlap(p->suffix,p->suffix_bytes,output,total)!=0)
        return bw_fail(error,"installation RAM overlaps or capacity is insufficient");
    if(bw_resident_image_check(original,original_bytes,digest)<0||!bw_string(digest,p->original_sha256))
        return bw_fail(error,"current RAM recovery data is not the bound vendor/resident image");
    bw_copy(package,p->prefix,p->prefix_bytes);image=package+p->prefix_bytes;
    bw_copy(image,original,BW_STAGE2_SIZE);bw_copy(image+0xa098,hook,sizeof(hook));
    /* Remove the entire OLD resident area first, including metadata and any
     * longer prior payload. Preserve original recovery bytes separately. */
    {size_t at;for(at=BW_RESIDENT_SLOT;at<BW_STAGE2_SIZE;at++)image[at]=255;}
    bw_copy(image+BW_RESIDENT_SLOT,p->tail,p->tail_bytes);
    bw_copy(image+BW_RESIDENT_DESCRIPTOR,p->descriptor,p->descriptor_bytes);
    bw_copy(image+BW_STAGE2_SIZE,p->suffix,p->suffix_bytes);
    if(bw_resident_image_check(image,BW_STAGE2_SIZE,digest)!=1||!bw_string(digest,p->candidate_sha256))
        return bw_fail(error,"reconstructed resident image format or complete SHA256 mismatch");
    bw_sha256(package,total,digest);
    if(!bw_string(digest,p->package_sha256))return bw_fail(error,"reconstructed complete package SHA256 mismatch");
    bw_copy(id.vendor_abi_sha256,BW_VENDOR_ABI_SHA256,65);bw_copy(id.installed_sha256,p->original_sha256,65);
    if(bw_validate_identity(package,total,&id,BW_LAYOUT_CURRENT,&result,error))return -1;
    if(result.kind!=BW_UBOOT||result.count!=1||result.writes[0].offset!=BW_STAGE2_START||
       result.writes[0].capacity!=BW_STAGE2_SIZE||result.writes[0].length!=BW_STAGE2_SIZE||
       result.writes[0].data_offset!=p->prefix_bytes)return bw_fail(error,"reconstructed package is not the exact fixed boot window");
    *package_bytes=total;return 0;
}
