/* SPDX-License-Identifier: GPL-2.0-only */
#include "internal.h"
static int same(const uint8_t *p,size_t n,const char *s) {
    size_t i=0;while(s[i])i++;return i==n&&bw_equal(p,s,n);
}
static int lower_same(const uint8_t *p,size_t n,const char *s) {
    size_t i;for(i=0;i<n;i++){uint8_t c=p[i];if(c>='A'&&c<='Z')c+=32;if(!s[i]||c!=s[i])return 0;}return s[n]==0;
}
int bw_http_parse(const void *v,size_t n,struct bw_request *r,char error[BW_ERROR_SIZE]) {
    const uint8_t *p=v;size_t at=0,e,a,b,i;unsigned seen=0,headers=0;
    if(!r||!p||!error)return -1;bw_zero(r,sizeof(*r));error[0]=0;
    if(n<16||n>4096||!bw_equal(p+n-4,"\r\n\r\n",4))return bw_fail(error,"incomplete/oversized HTTP header");
    for(i=0;i<n;i++)if(!p[i]||p[i]>126||(p[i]<32&&p[i]!='\r'&&p[i]!='\n')||
        (p[i]=='\r'&&(i+1==n||p[i+1]!='\n'))||(p[i]=='\n'&&(!i||p[i-1]!='\r')))
        return bw_fail(error,"invalid HTTP header byte");
    for(e=0;e+1<n&&!bw_equal(p+e,"\r\n",2);e++){}
    for(a=0;a<e&&p[a]!=' ';a++){}
    for(b=a+1;b<e&&p[b]!=' ';b++){}
    if(a>=sizeof(r->method)||b<=a+1||b-a-1>=sizeof(r->path)||
       !(same(p+b+1,e-b-1,"HTTP/1.1")||same(p+b+1,e-b-1,"HTTP/1.0")))return bw_fail(error,"invalid HTTP request line");
    bw_copy(r->method,p,a);bw_copy(r->path,p+a+1,b-a-1);
    if(!bw_string(r->method,"GET")&&!bw_string(r->method,"POST"))return bw_fail(error,"method not allowed");
    if(!(bw_string(r->path,"/")||bw_string(r->path,"/api/state")||bw_string(r->path,"/api/upload")||
         bw_string(r->path,"/api/confirm")||bw_string(r->path,"/api/cancel")||bw_string(r->path,"/api/reboot")
#ifdef BW_RAM_BACKUP
         ||bw_string(r->path,"/api/backup")||bw_string(r->path,"/api/backup-finish")
#endif
         ))
        return bw_fail(error,"route not allowed");
    at=e+2;
    while(at+2<n) {
        size_t colon,start,finish;
        if(++headers>32)return bw_fail(error,"too many HTTP headers");
        for(e=at;e+1<n&&!bw_equal(p+e,"\r\n",2);e++){}
        if(e==at)break;
        for(colon=at;colon<e&&p[colon]!=':';colon++){
            if(!((p[colon]>='a'&&p[colon]<='z')||(p[colon]>='A'&&p[colon]<='Z')||(p[colon]>='0'&&p[colon]<='9')||p[colon]=='-'))
                return bw_fail(error,"invalid/folded HTTP header name");
        }
        if(colon==at||colon==e)return bw_fail(error,"invalid HTTP header");
        start=colon+1;while(start<e&&p[start]==' ')start++;finish=e;while(finish>start&&p[finish-1]==' ')finish--;
        if(lower_same(p+at,colon-at,"transfer-encoding")||lower_same(p+at,colon-at,"expect"))
            return bw_fail(error,"chunked/Expect uploads are unsupported");
        if(lower_same(p+at,colon-at,"content-length")) {
            if((seen&1)||bw_decimal(p+start,finish-start,&r->length)||r->length>BW_MAX_UPLOAD)return bw_fail(error,"invalid/duplicate Content-Length");seen|=1;
        }
        if(lower_same(p+at,colon-at,"x-bootweb-token")) {
            if((seen&2)||finish-start!=64)return bw_fail(error,"invalid/duplicate request token");
            bw_copy(r->token,p+start,64);seen|=2;if(!bw_hex64(r->token))return bw_fail(error,"invalid token encoding");
        }
        if(lower_same(p+at,colon-at,"host")) {
            if((seen&4)||! (finish-start)||finish-start>=sizeof(r->host))return bw_fail(error,"invalid/duplicate Host");
            seen|=4;bw_copy(r->host,p+start,finish-start);
        }
        if(lower_same(p+at,colon-at,"origin")) {
            if((seen&8)||! (finish-start)||finish-start>=sizeof(r->origin))return bw_fail(error,"invalid/duplicate Origin");
            seen|=8;bw_copy(r->origin,p+start,finish-start);
        }
        at=e+2;
    }
    if(at+2!=n)return bw_fail(error,"HTTP pipelining/header terminator rejected");
    if(!(seen&4))return bw_fail(error,"Host required (DNS-rebinding protection)");
    if(bw_string(r->method,"POST")&&((seen&3)!=3))return bw_fail(error,"POST requires length and token");
    if(bw_string(r->method,"GET")&&r->length)return bw_fail(error,"GET body rejected");
    return 0;
}
