/* SPDX-License-Identifier: GPL-2.0-only */
#include "internal.h"
struct tree {const uint8_t *p,*str;size_t total,begin,end,strings;};
typedef int (*visit)(void *,const char *,const char *,const uint8_t *,size_t);
static size_t slen(const char *p) {size_t n=0;while(p[n])n++;return n;}
static int zeros(const uint8_t *p,size_t n) {
    while(n--) {if(*p++)return 0;if(!(n&0x3fffu)){bw_poll();if(bw_cancelled())return 0;}}return 1;
}
static int in_range(size_t off,size_t n,size_t total) {return off<=total && n<=total-off;}
static int overlap(size_t a,size_t n,size_t b,size_t m) {return a<b+m && b<a+n;}
static int open_tree(struct tree *t,const uint8_t *p,size_t len) {
    uint32_t st,sz,ss,ns,rv,v,compat;size_t r;
    if(len<40||bw_be32(p)!=0xd00dfeed)return -1;
    t->total=bw_be32(p+4);st=bw_be32(p+8);ss=bw_be32(p+12);rv=bw_be32(p+16);
    v=bw_be32(p+20);compat=bw_be32(p+24);ns=bw_be32(p+32);sz=bw_be32(p+36);
    if(t->total<40||t->total>len||!zeros(p+t->total,len-t->total)||v!=17||compat>17||compat<16||
       st<40||ss<40||rv<40||(st&3)||(rv&7)||!in_range(st,sz,t->total)||
       !in_range(ss,ns,t->total)||overlap(st,sz,ss,ns))return -1;
    for(r=rv;in_range(r,16,t->total);r+=16) {
        if(overlap(r,16,st,sz)||overlap(r,16,ss,ns))return -1;
        if(zeros(p+r,16))break;
    }
    if(!in_range(r,16,t->total))return -1;
    t->p=p;t->str=p+ss;t->begin=st;t->end=st+sz;t->strings=ns;return 0;
}
/* Bounded structure walk. Reject multiple roots and properties after children.
 * Every queried security-relevant property rejects duplicates; partition path
 * and label uniqueness is checked by the fixed target/alias pass below. */
static int walk(struct tree *t,visit fn,void *ctx) {
    char path[512];size_t lens[32],pos=t->begin,plen=0;int depth=-1,ended=0;
    unsigned nodes=0,roots=0,children_started[32];
    path[0]=0;
    while(pos+4<=t->end) {
        if(bw_cancelled())return -1;
        uint32_t tag=bw_be32(t->p+pos);pos+=4;
        if(tag==1) {
            size_t start=pos,n;
            if(depth>=0)children_started[depth]=1;
            while(pos<t->end&&t->p[pos]) {if(t->p[pos]<0x20||t->p[pos]=='/')return -1;pos++;}
            if(pos==t->end||++depth>=32||++nodes>2048)return -1;
            if(!depth&&roots++)return -1;
            children_started[depth]=0;
            n=pos-start;pos=(pos+4)&~(size_t)3;
            if(pos>t->end || (!depth&&n) || (depth&&!n) || plen+n+2>sizeof(path))return -1;
            lens[depth]=plen;
            if(depth) {path[plen++]='/';bw_copy(path+plen,t->p+start,n);plen+=n;path[plen]=0;}
            if(fn(ctx,path,0,0,0))return -1;
        } else if(tag==2) {
            if(depth<0)return -1;plen=lens[depth--];path[plen]=0;
        } else if(tag==3) {
            uint32_t size,off;size_t name_end;
            if(depth<0||children_started[depth]||pos+8>t->end)return -1;
            size=bw_be32(t->p+pos);off=bw_be32(t->p+pos+4);pos+=8;
            if(off>=t->strings||!in_range(pos,size,t->end))return -1;
            for(name_end=off;name_end<t->strings&&t->str[name_end];name_end++){}
            if(name_end==t->strings||name_end==off)return -1;
            if(fn(ctx,path,(const char *)t->str+off,t->p+pos,size))return -1;
            pos=(pos+size+3)&~(size_t)3;if(pos>t->end)return -1;
        } else if(tag==4) { /* FDT_NOP */
        } else if(tag==9) {if(depth!=-1||nodes==0)return -1;ended=1;break;}
        else return -1;
        if(!(pos&0xfffu))bw_poll();
    }
    return ended&&zeros(t->p+pos,t->end-pos)?0:-1;
}
struct query {const char *path,*name;const uint8_t *value;size_t size;unsigned found,nodes;};
static int query_cb(void *v,const char *path,const char *name,const uint8_t *data,size_t n) {
    struct query *q=v;
    if(!name&&bw_string(path,q->path)) {if(q->nodes++)return -1;return 0;}
    if(name&&bw_string(path,q->path)&&bw_string(name,q->name)) {if(q->found++)return -1;q->value=data;q->size=n;}
    return 0;
}
static int get(struct tree *t,const char *path,const char *name,struct query *q) {
    q->path=path;q->name=name;q->value=0;q->size=0;q->found=0;q->nodes=0;
    return walk(t,query_cb,q);
}
static int string_list_count(const uint8_t *p,size_t n,const char *want) {
    size_t at=0,w=slen(want);int found=0;
    if(!n)return -1;
    while(at<n) {
        size_t end=at;while(end<n&&p[end])end++;
        if(end==n||end==at)return -1;
        if(end-at==w&&bw_equal(p+at,want,w))found++;
        at=end+1;
    }
    return found;
}
static int string_list(const uint8_t *p,size_t n,const char *want) {return string_list_count(p,n,want)==1;}
struct discovery {char parent[384],children[8][512];unsigned parents,count;};
static int discover(void *v,const char *path,const char *name,const uint8_t *p,size_t n) {
    struct discovery *d=v;
    if(name&&bw_string(name,"compatible")) {
        int count=string_list_count(p,n,"fixed-partitions");
        if(count<0||count>1)return -1;
        if(count) {
            size_t len=slen(path);if(++d->parents!=1||len>=sizeof(d->parent))return -1;
            bw_copy(d->parent,path,len+1);
        }
    }
    return 0;
}
static int children(void *v,const char *path,const char *name,const uint8_t *p,size_t n) {
    struct discovery *d=v;size_t len=slen(d->parent),i;(void)p;(void)n;
    if(name||slen(path)<=len+1||!bw_equal(path,d->parent,len)||path[len]!='/')return 0;
    for(i=len+1;path[i];i++)if(path[i]=='/')return 0;
    if(d->count==8)return -1;
    bw_copy(d->children[d->count++],path,slen(path)+1);return 0;
}
static int aliases(void *v,const char *path,const char *name,const uint8_t *p,size_t n) {
    unsigned max=*(unsigned *)v;(void)p;(void)n;
    if(!name||!bw_string(path,"/aliases")||slen(name)<4||!bw_equal(name,"mtd",3))return 0;
    if(name[3]>='0'&&name[3]<='9'&&(slen(name)!=4||name[3]<'1'||(unsigned char)name[3]>'0'+max))return -1;
    return 0;
}
static int enabled(struct tree *t,const char *path,struct query *q) {
    if(get(t,path,"status",q))return 0;
    return !q->found||(q->size==5&&bw_equal(q->value,"okay",5))||(q->size==3&&bw_equal(q->value,"ok",3));
}
static int bootargs(const uint8_t *p,size_t n) {
    size_t at=0,i;unsigned roots=0,types=0;
    if(!n||n>4096||p[n-1])return 0;
    for(i=0;i+1<n;i++)if(p[i]<32||p[i]>126)return 0;
    while(at+1<n) {
        size_t end;
        if(p[at]==' '){at++;continue;}
        for(end=at;end+1<n&&p[end]!=' ';end++){}
        if(end-at>=5&&bw_equal(p+at,"root=",5)) {
            if(++roots!=1||end-at!=19||!bw_equal(p+at,"root=/dev/mtdblock5",19))return 0;
        } else if(end-at>=11&&bw_equal(p+at,"rootfstype=",11)) {
            if(++types!=1||end-at!=16||!bw_equal(p+at,"rootfstype=jffs2",16))return 0;
        } else if((end-at>=9&&bw_equal(p+at,"mtdparts=",9))||(end-at>=8&&bw_equal(p+at,"ubi.mtd=",8)))return 0;
        at=end;
    }
    return roots==1&&types==1;
}
static void error_text(char **out,const char *value) {while(*value)*(*out)++=*value++;}
static void error_hex(char **out,uint32_t value) {
    static const char digits[]="0123456789abcdef";unsigned i;
    error_text(out,"0x");for(i=0;i<8;i++)*(*out)++=digits[(value>>(28-4*i))&15];
}
static int layout_error(char *error,const char *name,uint32_t off,uint32_t size,const struct query *q) {
    char msg[BW_ERROR_SIZE],*out=msg;
    /* Fixed strings and a known partition label bound this to < 160 bytes. */
    error_text(&out,"FDT layout mismatch: ");error_text(&out,name);
    error_text(&out," expected offset=");error_hex(&out,off);error_text(&out," size=");error_hex(&out,size);
    if(q->found&&q->size==8) {
        error_text(&out," actual offset=");error_hex(&out,bw_be32(q->value));
        error_text(&out," size=");error_hex(&out,bw_be32(q->value+4));
    } else error_text(&out," actual reg missing/invalid");
    *out=0;return bw_fail(error,msg);
}
int bw_fdt_layout(const uint8_t *data,size_t length,uint32_t layout,char *error) {
    static const char *names[7]={"boot","kernel","dtb","parameter tags","root","scratch","recovery"};
    static const uint32_t offsets[7]={0,0x200000,0xa00000,0xb00000,0xf00000,0xff00000,0xfc00000};
    static const uint32_t sizes[7]={0x200000,0x800000,0x100000,0x400000,0xf000000,0x100000,0x400000};
    struct tree t;struct query q;struct discovery d;unsigned i,j,seen=0,want=layout==1?31:layout==2?95:63,maxalias=layout==1?5:6;
    bw_zero(&d,sizeof(d));
    if(open_tree(&t,data,length)||walk(&t,discover,&d)||d.parents!=1||walk(&t,children,&d))
        return bw_fail(error,"invalid/ambiguous FDT partition tree");
    if(get(&t,"","compatible",&q)||!q.found||!string_list(q.value,q.size,"skyworth,sk-d840n"))
        return bw_fail(error,"wrong FDT board");
    if(get(&t,"/chosen","bootargs",&q)||!q.found||!bootargs(q.value,q.size))
        return bw_fail(error,"FDT bootargs must keep JFFS2 mtdblock5, without partition overrides");
    if(get(&t,d.parent,"#address-cells",&q)||!q.found||q.size!=4||bw_be32(q.value)!=1||
       get(&t,d.parent,"#size-cells",&q)||!q.found||q.size!=4||bw_be32(q.value)!=1)
        return bw_fail(error,"unsupported FDT partition cells");
    if(!enabled(&t,d.parent,&q)||walk(&t,aliases,&maxalias))return bw_fail(error,"disabled FDT table / unexpected mtd alias");
    {char ancestor[384];size_t len;bw_copy(ancestor,d.parent,slen(d.parent)+1);
     for(;;) {
         if(!enabled(&t,ancestor,&q))return bw_fail(error,"disabled FDT flash ancestor");
         len=slen(ancestor);if(!len)break;
         while(len&&ancestor[len-1]!='/')len--;
         ancestor[len?len-1:0]=0;
     }}
    for(i=0;i<d.count;i++) {
        uint32_t size;char alias[5]={'m','t','d',0,0};
        if(get(&t,d.children[i],"label",&q)||!q.found||q.size<2||q.value[q.size-1])
            return bw_fail(error,"missing FDT partition label");
        for(j=0;j<7;j++)if(q.size==slen(names[j])+1&&bw_equal(q.value,names[j],q.size))break;
        if(j==7 || !(want&(1u<<j)) || (seen&(1u<<j)))return bw_fail(error,"unknown/duplicate FDT partition");
        seen|=1u<<j;size=sizes[j];if(j==4)size=layout==1?0xf100000:layout==2?0xed00000:0xf000000;
        if(!enabled(&t,d.children[i],&q))return bw_fail(error,"disabled FDT partition");
        if(get(&t,d.children[i],"reg",&q))return bw_fail(error,"invalid/duplicate FDT reg");
        if(!q.found||q.size!=8||bw_be32(q.value)!=offsets[j]||bw_be32(q.value+4)!=size)
            return layout_error(error,names[j],offsets[j],size,&q);
        if(j==0||j==3) {
            if(get(&t,d.children[i],"read-only",&q)||!q.found||q.size)return bw_fail(error,"FDT omits protected read-only partition");
        }
        alias[3]=(char)('1'+(j==6?5:j));
        if(get(&t,"/aliases",alias,&q)||!q.found||q.size!=slen(d.children[i])+1||!bw_equal(q.value,d.children[i],q.size))
            return bw_fail(error,"FDT mtd aliases changed (root must remain mtdblock5)");
    }
    if(seen!=want)return bw_fail(error,"incomplete FDT partition map");
    if(layout==1&&(get(&t,"/aliases","mtd6",&q)||q.found))return bw_fail(error,"stale scratch alias");
    return 0;
}
