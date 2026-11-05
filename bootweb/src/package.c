/* SPDX-License-Identifier: GPL-2.0-only */
#include "internal.h"
#define PREFIX "sysupgrade-" BW_BOARD
struct member {size_t off,len;};
static int all_zero(const uint8_t *p,size_t n) {
    while(n--) {if(*p++)return 0;if(!(n&0x3fffu)){bw_poll();if(bw_cancelled())return 0;}}return 1;
}
static size_t length(const char *p) {size_t n=0;while(p[n])n++;return n;}
static int text(const uint8_t *p,size_t n,const char *s) {return n==length(s)&&bw_equal(p,s,n);}
static int octal(const uint8_t *p,size_t n,size_t *out) {
    size_t i=0,v=0;int digits=0,ended=0;
    for(;i<n;i++) {
        if(p[i]==' '||p[i]==0) {if(digits)ended=1;continue;}
        if(ended||p[i]<'0'||p[i]>'7'||v>(SIZE_MAX-7)/8)return -1;
        v=v*8+(p[i]-'0');digits=1;
    }
    if(!digits)return -1;*out=v;return 0;
}
static int tar(const uint8_t *p,size_t n,int system,struct member m[4],char *error) {
    static const char *sys[4]={PREFIX "/CONTROL",PREFIX "/kernel",PREFIX "/dtb",PREFIX "/root"};
    static const char *boot[3]={"CONTROL","uboot.bin","sha256sums"};
    size_t pos=0;unsigned seen=0,dir=0;const unsigned count=system?4:3;
    while(pos<=n && n-pos>=512) {
        const uint8_t *h=p+pos;size_t sum=0,saved,size,i,namelen;unsigned j;
        if(all_zero(h,512)) {
            if(n-pos<1024||!all_zero(h,n-pos))return bw_fail(error,"truncated/concatenated tar end");
            if(seen!=((1u<<count)-1))return bw_fail(error,"missing archive member");
            return 0;
        }
        for(i=0;i<512;i++)sum+=i>=148&&i<156?' ':h[i];
        if(octal(h+148,8,&saved)||saved!=sum)return bw_fail(error,"tar header checksum mismatch");
        if(!bw_equal(h+257,"ustar",5)||!(h[262]==0||h[262]==' ')||!all_zero(h+345,155)||!all_zero(h+157,100))
            return bw_fail(error,"unsupported tar format/prefix/link (no PAX/GNU extensions)");
        for(namelen=0;namelen<100&&h[namelen];namelen++){}
        if(namelen==100||!all_zero(h+namelen,100-namelen)||octal(h+124,12,&size))
            return bw_fail(error,"invalid tar name/size");
        pos+=512;if(size>n-pos)return bw_fail(error,"truncated archive payload");
        if(system && (text(h,namelen,PREFIX)||text(h,namelen,PREFIX "/"))) {
            if(dir++||h[156]!='5'||size)return bw_fail(error,"duplicate/invalid archive directory");
        } else {
            for(j=0;j<count;j++)if(text(h,namelen,system?sys[j]:boot[j]))break;
            if(j==count)return bw_fail(error,"unexpected archive member/path");
            if(h[156]!='0'&&h[156]!=0)return bw_fail(error,"archive member is not a regular file");
            if(seen&(1u<<j))return bw_fail(error,"duplicate archive member");
            if(!size)return bw_fail(error,"empty archive member");
            if(j==0&&size>4096)return bw_fail(error,"CONTROL exceeds 4096 bytes");
            if(!system&&j==2&&size>512)return bw_fail(error,"sha256sums exceeds limit");
            seen|=1u<<j;m[j].off=pos;m[j].len=size;
        }
        if(size>SIZE_MAX-511)return bw_fail(error,"archive size overflow");
        i=(size+511)&~(size_t)511;
        if(i>n-pos||!all_zero(p+pos+size,i-size))return bw_fail(error,"truncated/nonzero tar padding");
        pos+=i;
    }
    return bw_fail(error,"tar has no complete end marker");
}
static int fwtool(const uint8_t *p,size_t n,size_t *end,char *error) {
    unsigned chunks=0;
    while(n>=16 && ++chunks<=4) {
        const uint8_t *h=p+n-16;uint32_t size=bw_be32(h+12);size_t start;
        if(bw_be32(h)!=0x46577830||size<16||size>n||!all_zero(h+9,3))return bw_fail(error,"missing/invalid fwtool trailer");
        if((bw_crc32(p,n-16)^0xffffffffu)!=bw_be32(h+4))return bw_fail(error,"fwtool CRC mismatch");
        start=n-size;
        if(h[8]==1) {
            if(size<24||!all_zero(p+start,8)||bw_json_metadata(p+start+8,size-24,error))return bw_fail(error,error[0]?error:"invalid fwtool metadata header");
            *end=start;return 0;
        }
        if(h[8]!=0||size>65536)return bw_fail(error,"unsupported fwtool signature chunk");
        /* A signature chunk's CRC is checked, NOT its authenticity. */
        n=start;
    }
    return bw_fail(error,"missing fwtool metadata");
}
static int kernel(const uint8_t *p,size_t n,char *error) {
    uint8_t head[64];
    if(n<82||bw_be32(p)!=0x27051956||bw_be32(p+12)!=n-64)return bw_fail(error,"invalid/truncated uImage size");
    if(bw_be32(p+16)!=0x80200000||bw_be32(p+20)!=0x80200000||!bw_equal(p+28,(uint8_t[]){5,22,2,1},4))
        return bw_fail(error,"wrong uImage load/entry/type");
    bw_copy(head,p,64);bw_zero(head+4,4);
    if(bw_crc32(head,64)!=bw_be32(p+4))return bw_fail(error,"uImage header CRC mismatch");
    if(bw_crc32(p+64,n-64)!=bw_be32(p+24))return bw_fail(error,"uImage data CRC mismatch");
    if(!bw_equal(p+64,(uint8_t[]){0x1f,0x8b,8},3)||(p[67]&0xe0))return bw_fail(error,"invalid gzip kernel header");
    return 0;
}
static int boot_control(const uint8_t *p,size_t n,const char *base,const char *payload,char *error) {
    static const char *keys[5]={"FORMAT=","BOARD=","BASE_SHA256=","PAYLOAD_SHA256=","PAYLOAD_BYTES="};
    const char *values[5]={"sk-d840n-uboot-v1",BW_BOARD,base,payload,"1441792"};
    size_t at=0;unsigned seen=0;
    while(at<n) {
        size_t e=at,k;unsigned i;
        while(e<n&&p[e]!='\n')e++;if(e==n)return bw_fail(error,"CONTROL requires LF-terminated fields");
        for(i=0;i<5;i++) {k=length(keys[i]);if(e-at>=k&&bw_equal(p+at,keys[i],k))break;}
        if(i==5||(seen&(1u<<i))||!text(p+at+k,e-at-k,values[i]))return bw_fail(error,"wrong/duplicate/unknown U-Boot CONTROL field or base");
        seen|=1u<<i;at=e+1;
    }
    return seen==31?0:bw_fail(error,"incomplete U-Boot CONTROL");
}
static int sums(const uint8_t *p,size_t n,const char *control,const char *payload,char *error) {
    size_t at=0;unsigned seen=0;
    while(at<n) {
        size_t e=at;unsigned bit;const char *hash;
        while(e<n&&p[e]!='\n')e++;
        if(e==n||e-at<68||p[at+64]!=' '||p[at+65]!=' ')return bw_fail(error,"invalid sha256sums format");
        if(text(p+at+66,e-at-66,"CONTROL")) {bit=1;hash=control;}
        else if(text(p+at+66,e-at-66,"uboot.bin")) {bit=2;hash=payload;}
        else return bw_fail(error,"unexpected sha256sums path");
        if(seen&bit||!bw_equal(p+at,hash,64))return bw_fail(error,"duplicate/mismatched SHA256");
        seen|=bit;at=e+1;
    }
    return seen==3?0:bw_fail(error,"missing SHA256 entry");
}
static void span(struct bw_span *s,const uint8_t *p,struct member m,const char *name,uint32_t off,uint32_t cap) {
    bw_zero(s,sizeof(*s));s->data_offset=m.off;s->length=m.len;s->offset=off;s->capacity=cap;
    bw_copy(s->name,name,length(name)+1);bw_sha256(p+m.off,m.len,s->sha256);
}
int bw_validate_identity(const void *v,size_t n,const struct bw_identity *identity,uint32_t layout,struct bw_result *out,char error[BW_ERROR_SIZE]) {
    const uint8_t *p=v;struct member m[4];struct bw_result r;size_t end=n;int system;
    if(error)error[0]=0;
    if(!p||!out||!error)return -1;
    bw_zero(out,sizeof(*out));bw_zero(m,sizeof(m));bw_zero(&r,sizeof(r));
    if(bw_cancelled())return bw_fail(error,"service deadline reached; validation cancelled");
    if(!bw_identity_valid(identity))return bw_fail(error,"unknown/unadapted vendor ABI or installed identity");
    if(layout>BW_LAYOUT_RECOVERY237)return bw_fail(error,"unknown partition layout");
    if(n<1536||n>BW_MAX_UPLOAD)return bw_fail(error,"upload length outside fixed limits");
    system=bw_equal(p,PREFIX,sizeof(PREFIX)-1);
    if(system&&fwtool(p,n,&end,error))return -1;
    if(bw_cancelled())return bw_fail(error,"service deadline reached; validation cancelled");
    if(tar(p,end,system,m,error))return -1;
    r.kind=system?BW_SYSTEM:BW_UBOOT;r.layout=layout;r.package_bytes=n;
    if(system) {
        uint32_t rootcap=layout==1?0xf100000:layout==2?0xed00000:0xf000000;
        if(!text(p+m[0].off,m[0].len,"BOARD=" BW_BOARD "\n"))return bw_fail(error,"wrong system CONTROL board");
        if(m[1].len>0x800000||m[2].len>0x100000||m[3].len>rootcap)return bw_fail(error,"image exceeds fixed partition capacity");
        if(kernel(p+m[1].off,m[1].len,error)||bw_fdt_layout(p+m[2].off,m[2].len,layout,error))return -1;
        if(m[3].len<12||p[m[3].off]!=0x85||p[m[3].off+1]!=0x19||
           bw_le32(p+m[3].off+4)<12||bw_le32(p+m[3].off+4)>m[3].len||
           bw_crc32_linux(p+m[3].off,8)!=bw_le32(p+m[3].off+8))return bw_fail(error,"invalid JFFS2 first-node header/CRC");
        span(&r.writes[0],p,m[3],"root",0xf00000,rootcap);
        span(&r.writes[1],p,m[2],"dtb",0xa00000,0x100000);
        span(&r.writes[2],p,m[1],"kernel",0x200000,0x800000);r.count=3;
    } else {
        char payload[65],control[65];
        if(m[1].len!=BW_STAGE2_SIZE)return bw_fail(error,"U-Boot payload must be exactly the fixed stage2 window (no full boot image)");
        bw_sha256(p+m[1].off,m[1].len,payload);bw_sha256(p+m[0].off,m[0].len,control);
        if(boot_control(p+m[0].off,m[0].len,identity->installed_sha256,payload,error)||sums(p+m[2].off,m[2].len,control,payload,error))return -1;
        span(&r.writes[0],p,m[1],"uboot",BW_STAGE2_START,BW_STAGE2_SIZE);r.count=1;
    }
    bw_sha256(p,n,r.package_sha256);
    if(bw_cancelled())return bw_fail(error,"service deadline reached; validation cancelled");
    bw_copy(out,&r,sizeof(r));return 0;
}
int bw_validate(const void *v,size_t n,const char *base,uint32_t layout,struct bw_result *out,char error[BW_ERROR_SIZE]) {
    struct bw_identity identity;
    if(bw_legacy_identity(base,&identity)) {
        if(out)bw_zero(out,sizeof(*out));
        return error?bw_fail(error,"unknown/unadapted installed U-Boot code base"):-1;
    }
    return bw_validate_identity(v,n,&identity,layout,out,error);
}
