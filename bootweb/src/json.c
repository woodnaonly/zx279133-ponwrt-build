/* SPDX-License-Identifier: GPL-2.0-only
 * Bounded JSON reader for fwtool metadata. No allocation, execution or coercion.
 * Escaped keys are deliberately unsupported (ambiguous spellings fail closed). */
#include "internal.h"
struct token { size_t start, size; unsigned next; char type; };
struct parser { const uint8_t *p; size_t size, pos; unsigned count; struct token t[512]; };
static void ws(struct parser *p) {
    while(p->pos<p->size && (p->p[p->pos]==' '||p->p[p->pos]=='\r'||p->p[p->pos]=='\n'||p->p[p->pos]=='\t')) p->pos++;
}
static int hex(uint8_t c) { return (c>='0'&&c<='9')||(c>='a'&&c<='f')||(c>='A'&&c<='F'); }
static int value(struct parser *p,unsigned depth,int key) {
    unsigned i,mark; size_t start; uint8_t c;
    ws(p); if(depth>16||p->count>=512||p->pos==p->size) return -1;
    mark=p->count++;start=p->pos;c=p->p[p->pos++];p->t[mark].type=(char)c;
    if(c=='"') {
        while(p->pos<p->size) {
            c=p->p[p->pos++];
            if(c=='"') goto done;
            if(c<0x20 || (key && c=='\\')) return -1;
            if(c=='\\') {
                if(p->pos==p->size) return -1;
                c=p->p[p->pos++];
                if(c=='u') { for(i=0;i<4;i++) { if(p->pos==p->size||!hex(p->p[p->pos++])) return -1; } }
                else if(!(c=='"'||c=='\\'||c=='/'||c=='b'||c=='f'||c=='n'||c=='r'||c=='t')) return -1;
            }
        }
        return -1;
    }
    if(key) return -1;
    if(c=='{'||c=='[') {
        uint8_t end=c=='{'?'}':']'; ws(p);
        if(p->pos<p->size&&p->p[p->pos]==end) {p->pos++;goto done;}
        for(;;) {
            if(c=='{') {
                unsigned k=p->count,j;
                if(value(p,depth+1,1)) return -1;
                for(j=mark+1;j<k;) {
                    if(p->t[j].size==p->t[k].size && bw_equal(p->p+p->t[j].start,p->p+p->t[k].start,p->t[k].size)) return -1;
                    j=p->t[p->t[j].next].next;
                }
                ws(p);if(p->pos==p->size||p->p[p->pos++]!=':') return -1;
            }
            if(value(p,depth+1,0)) return -1;
            ws(p);if(p->pos==p->size) return -1;
            if(p->p[p->pos++]==end) break;
            if(p->p[p->pos-1]!=',') return -1;
        }
        goto done;
    }
    if(c=='t'||c=='f'||c=='n') {
        const char *s=c=='t'?"true":c=='f'?"false":"null";size_t n=c=='f'?5:4;
        if(start+n>p->size||!bw_equal(p->p+start,s,n)) return -1;
        p->pos=start+n;goto done;
    }
    p->t[mark].type='0';p->pos=start;
    if(c=='-') {p->pos++;if(p->pos==p->size) return -1;}
    c=p->p[p->pos++];if(c<'0'||c>'9') return -1;
    if(c!='0') while(p->pos<p->size && p->p[p->pos]>='0'&&p->p[p->pos]<='9') p->pos++;
    if(p->pos<p->size && p->p[p->pos]=='.') {
        p->pos++;i=0;while(p->pos<p->size&&p->p[p->pos]>='0'&&p->p[p->pos]<='9') {p->pos++;i++;}if(!i)return -1;
    }
    if(p->pos<p->size && (p->p[p->pos]=='e'||p->p[p->pos]=='E')) {
        p->pos++;if(p->pos<p->size&&(p->p[p->pos]=='+'||p->p[p->pos]=='-'))p->pos++;
        i=0;while(p->pos<p->size&&p->p[p->pos]>='0'&&p->p[p->pos]<='9') {p->pos++;i++;}if(!i)return -1;
    }
done:
    p->t[mark].start=start;p->t[mark].size=p->pos-start;p->t[mark].next=p->count;return 0;
}
static int is_string(struct parser *p,unsigned i,const char *s) {
    size_t n=0;while(s[n])n++;
    return i<p->count&&p->t[i].type=='"'&&p->t[i].size==n+2&&bw_equal(p->p+p->t[i].start+1,s,n);
}
static unsigned prop(struct parser *p,unsigned object,const char *key) {
    unsigned i;if(object>=p->count||p->t[object].type!='{')return p->count;
    for(i=object+1;i<p->t[object].next;) {
        unsigned v=p->t[i].next;if(is_string(p,i,key))return v;i=p->t[v].next;
    }
    return p->count;
}
int bw_json_metadata(const uint8_t *data,size_t length,char *error) {
    struct parser p;unsigned ver,dev,i;int board=0,zte=0;
    if(length>16384)return bw_fail(error,"fwtool metadata exceeds 16 KiB");
    p.p=data;p.size=length;p.pos=0;p.count=0;
    if(value(&p,0,0))return bw_fail(error,"invalid/duplicate/too complex JSON metadata");
    ws(&p);if(p.pos!=length||p.t[0].type!='{')return bw_fail(error,"invalid metadata JSON root");
    if(!is_string(&p,prop(&p,0,"metadata_version"),"1.1"))return bw_fail(error,"wrong metadata version");
    i=prop(&p,0,"compat_version");
    if(i<p.count&&!is_string(&p,i,"1.0"))return bw_fail(error,"wrong compatibility version");
    ver=prop(&p,0,"version");
    if(!is_string(&p,prop(&p,ver,"board"),BW_BOARD)||!is_string(&p,prop(&p,ver,"target"),"zte/zx279133"))
        return bw_fail(error,"wrong metadata board/target");
    dev=prop(&p,0,"supported_devices");
    if(dev>=p.count||p.t[dev].type!='[')return bw_fail(error,"missing supported_devices array");
    for(i=dev+1;i<p.t[dev].next;i=p.t[i].next) {
        if(p.t[i].type!='"')return bw_fail(error,"invalid supported_devices item");
        if(is_string(&p,i,"skyworth,sk-d840n"))board++;
        if(is_string(&p,i,"zte,133"))zte++;
    }
    if(board!=1||zte!=1)return bw_fail(error,"missing/duplicate SK-D840N identifiers");
    return 0;
}
