/* SPDX-License-Identifier: GPL-2.0-only
 * Tiny HTTP service; no filesystem, command interpreter, multipart or templates.
 * This is compiled into the unadapted ELF, but cannot run without a verified
 * vendor network/NAND/watchdog/entropy adapter. */
#include "platform.h"
#include "internal.h"
#include "web.inc"
struct server {
    struct bw_platform *p;
    struct bw_task task;
    struct bw_port writer;
    uint64_t next_id;
    const char *phase;
    uint32_t done,total;
};
struct output {char data[3072];size_t n;int overflow;};
static void append(struct output *o,const char *s) {
    while(*s) {if(o->n+1>=sizeof(o->data)){o->overflow=1;return;}o->data[o->n++]=*s++;}o->data[o->n]=0;
}
static void number(struct output *o,uint64_t n) {
    char tmp[21],one[2]={0,0};unsigned i=0;
    do {tmp[i++]=(char)('0'+n%10);n/=10;}while(n);
    while(i){one[0]=tmp[--i];append(o,one);}
}
static int transmit(struct server *s,int fd,const void *v,size_t n) {
    const uint8_t *p=v;
    while(n) {int z=s->p->send(s->p->context,fd,p,n);if(z<=0||(size_t)z>n)return -1;p+=z;n-=z;s->p->poll(s->p->context);}return 0;
}
static int reply(struct server *s,int fd,int status,const char *type,const void *data,size_t n) {
    struct output h;bw_zero(&h,sizeof(h));
    append(&h,status==200?"HTTP/1.1 200 OK\r\n":status==202?"HTTP/1.1 202 Accepted\r\n":
           status==409?"HTTP/1.1 409 Conflict\r\n":"HTTP/1.1 400 Bad Request\r\n");
    append(&h,"Connection: close\r\nCache-Control: no-store\r\nX-Content-Type-Options: nosniff\r\n");
    append(&h,"Content-Security-Policy: default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; connect-src 'self'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'\r\n");
    append(&h,"Content-Type: ");append(&h,type);append(&h,"\r\nContent-Length: ");number(&h,n);append(&h,"\r\n\r\n");
    if(h.overflow||transmit(s,fd,h.data,h.n))return -1;return transmit(s,fd,data,n);
}
static int enabled(struct server *s) {
    uint32_t caps=s->writer.capabilities;
    if((caps&BW_REQUIRED)!=BW_REQUIRED)return 0;
    if(s->task.layout==1&&!(caps&BW_CAP_NATIVE_LAYOUT))return 0;
    if(s->task.layout==2&&!(caps&BW_CAP_RECOVERY_LAYOUT))return 0;
    return s->task.result.kind!=BW_UBOOT||(caps&(BW_CAP_SERIAL_RESCUE|BW_CAP_BOOT_CONTAINER))==(BW_CAP_SERIAL_RESCUE|BW_CAP_BOOT_CONTAINER);
}
static void state(struct server *s,int fd,int code) {
    static const char *states[]={"idle","receiving","validating","ready","queued","writing","success","failed","cancelled"};
    static const char *layouts[]={"current240","native241","recovery237"};
    struct output o;unsigned i;bw_zero(&o,sizeof(o));
    append(&o,"{\"board\":\"" BW_BOARD "\",\"mode\":\"native-development\",\"notice\":\"DEVELOPMENT: hardware acceptance incomplete; not a flashable release\",\"base\":\"");
    append(&o,s->task.base);append(&o,"\",\"layout\":\"");append(&o,layouts[s->task.layout]);
    append(&o,"\",\"token\":\"");append(&o,s->task.token);append(&o,"\",\"state\":\"");append(&o,states[s->task.state]);
    append(&o,"\",\"id\":\"");number(&o,s->task.id);append(&o,"\",\"error\":\"");append(&o,s->task.error);
    append(&o,"\",\"phase\":\"");append(&o,s->phase);append(&o,"\",\"done\":");number(&o,s->done);append(&o,",\"total\":");number(&o,s->total);
    append(&o,",\"can_confirm\":");append(&o,enabled(s)?"true":"false");
    append(&o,",\"can_reboot\":");append(&o,s->task.state==BW_QUEUED||s->task.state==BW_WRITING||!s->p->reboot?"false":"true");
    if(s->task.result.count) {
        append(&o,",\"result\":{\"kind\":\"");append(&o,s->task.result.kind==BW_SYSTEM?"system":"uboot");
        append(&o,"\",\"sha256\":\"");append(&o,s->task.result.package_sha256);append(&o,"\",\"writes\":[");
        for(i=0;i<s->task.result.count;i++) {
            const struct bw_span *w=&s->task.result.writes[i];if(i)append(&o,",");
            append(&o,"{\"name\":\"");append(&o,w->name);append(&o,"\",\"length\":");number(&o,w->length);
            append(&o,",\"capacity\":");number(&o,w->capacity);append(&o,",\"sha256\":\"");append(&o,w->sha256);append(&o,"\"}");
        }
        append(&o,"]}");
    }
    append(&o,"}");
    if(o.overflow) {const char msg[]="{\"error\":\"status buffer limit\"}";reply(s,fd,409,"application/json",msg,sizeof(msg)-1);}
    else reply(s,fd,code,"application/json; charset=utf-8",o.data,o.n);
}
static void reject(struct server *s,int fd,const char *msg) {
    struct output o;bw_zero(&o,sizeof(o));append(&o,"{\"error\":\"");append(&o,msg);append(&o,"\"}");reply(s,fd,409,"application/json",o.data,o.n);
}
static void serve_one(struct server *s,int fast) {
    char header[4096],error[BW_ERROR_SIZE],body[96];struct bw_request r;size_t n=0;int fd=s->p->accept(s->p->context);
    uint64_t deadline=s->p->milliseconds(s->p->context)+(fast?100u:5000u);
    if(fd<=0)return;
    while(n<sizeof(header)) {
        if(s->p->milliseconds(s->p->context)>=deadline)goto close;
        if(s->p->recv(s->p->context,fd,header+n,1,fast?50:5000)!=1)goto close;
        n++;s->p->poll(s->p->context);if(n>=4&&bw_equal(header+n-4,"\r\n\r\n",4))break;
    }
    if(bw_http_parse(header,n,&r,error)) {reject(s,fd,error);goto close;}
    if(!(bw_string(r.host,"192.168.9.1")||bw_string(r.host,"192.168.9.1:80"))||
       (r.origin[0]&&!bw_string(r.origin,"http://192.168.9.1")&&!bw_string(r.origin,"http://192.168.9.1:80"))) {
        reject(s,fd,"Host/Origin rejected");goto close;
    }
    if(bw_string(r.method,"GET")) {
        if(bw_string(r.path,"/"))reply(s,fd,200,"text/html; charset=utf-8",bw_page,sizeof(bw_page));
        else if(bw_string(r.path,"/api/state"))state(s,fd,200);
        else reject(s,fd,"GET route not allowed");
        goto close;
    }
    if(fast||s->task.state==BW_QUEUED||s->task.state==BW_WRITING) {reject(s,fd,"task busy");goto close;}
    if(!bw_equal(r.token,s->task.token,65)) {reject(s,fd,"invalid request token");goto close;}
    if(bw_string(r.path,"/api/upload")) {
        if(bw_task_begin(&s->task,(size_t)r.length,++s->next_id)) {reject(s,fd,s->task.error);goto close;}
        deadline=s->p->milliseconds(s->p->context)+300000u;
        while(s->task.received<s->task.expected) {
            size_t want=s->task.expected-s->task.received;int z;if(want>65536)want=65536;
            if(s->p->milliseconds(s->p->context)>=deadline) {bw_task_finish(&s->task);goto close;}
            z=s->p->recv(s->p->context,fd,s->task.buffer+s->task.received,want,30000);
            if(z<=0||(size_t)z>want) {bw_task_finish(&s->task);goto close;}
            bw_task_feed(&s->task,s->task.buffer+s->task.received,(size_t)z);s->p->poll(s->p->context);
        }
        bw_task_finish(&s->task);state(s,fd,s->task.state==BW_READY?200:409);
    } else if(bw_string(r.path,"/api/confirm")) {
        size_t at=0,split=0;uint64_t id=0;
        if(!enabled(s)||r.length>=sizeof(body)||r.length<67) {reject(s,fd,"write evidence gates locked / invalid confirmation");goto close;}
        while(at<r.length) {int z;if(s->p->milliseconds(s->p->context)>=deadline)goto close;
            z=s->p->recv(s->p->context,fd,body+at,(size_t)r.length-at,5000);if(z<=0||(size_t)z>r.length-at)goto close;at+=(size_t)z;}
        while(split<at&&body[split]!='\n')split++;
        if(split==at||bw_decimal((uint8_t *)body,split,&id)||at!=split+66||body[at-1]!='\n') {reject(s,fd,"invalid confirmation body");goto close;}
        body[at-1]=0;
        if(bw_task_confirm(&s->task,id,body+split+1,r.token))reject(s,fd,s->task.error);
        else state(s,fd,202); /* execution is after close, even if reply disconnects */
    } else if(bw_string(r.path,"/api/cancel")&&r.length==0) {
        if(bw_task_cancel(&s->task,r.token))reject(s,fd,s->task.error);else state(s,fd,200);
    } else if(bw_string(r.path,"/api/reboot")&&r.length==0&&s->p->reboot) {
        state(s,fd,202);s->p->close(s->p->context,fd);s->p->reboot(s->p->context);return;
    } else reject(s,fd,"POST route/body not allowed");
close:
    s->p->close(s->p->context,fd);
}
static void report_progress(void *ctx,const char *phase,uint32_t done,uint32_t total) {
    struct server *s=ctx;s->phase=phase;s->done=done;s->total=total;
    if(s->p->nand.progress)s->p->nand.progress(s->p->nand.context,phase,done,total);
    serve_one(s,1);
}
static int bad(void *v,uint32_t off) {struct server *s=v;return s->p->nand.is_bad(s->p->nand.context,off);}
static int read_nand(void *v,uint32_t off,void *p,size_t n) {struct server *s=v;return s->p->nand.read(s->p->nand.context,off,p,n);}
static int erase(void *v,uint32_t off) {struct server *s=v;return s->p->nand.erase(s->p->nand.context,off);}
static int write_page(void *v,uint32_t off,const void *p) {struct server *s=v;return s->p->nand.write_page(s->p->nand.context,off,p);}
static int verify_boot(void *v,const void *p,size_t n) {struct server *s=v;return s->p->nand.verify_boot?s->p->nand.verify_boot(s->p->nand.context,p,n):-1;}
static void watchdog(void *v) {struct server *s=v;s->p->poll(s->p->context);if(s->p->nand.watchdog)s->p->nand.watchdog(s->p->nand.context);}
int bw_serve(struct bw_platform *p) {
    struct server s;char base[65];
    if(!p||!p->listen||!p->accept||!p->recv||!p->send||!p->close||!p->poll||!p->milliseconds||!p->nand.read||
       !p->nand.is_bad||!p->nand.erase||!p->nand.write_page||!p->scratch||p->scratch_capacity<BW_ERASE_SIZE)return -1;
    bw_zero(&s,sizeof(s));s.p=p;s.phase="idle";
    /* Installed fingerprint comes from fixed ECC reads, never from HTTP input. */
    {struct bw_hash h;uint32_t off;bw_hash_init(&h);
     for(off=BW_STAGE2_START;off<BW_STAGE2_END;off+=BW_ERASE_SIZE) {
         p->poll(p->context);if(p->nand.read(p->nand.context,off,p->scratch,BW_ERASE_SIZE))return -1;
         bw_hash_add(&h,p->scratch,BW_ERASE_SIZE);
     }bw_hash_end(&h,base);}
    if(!bw_string(base,BW_BASE)||bw_task_init(&s.task,p->upload,p->upload_capacity,base,p->layout,p->token))return -1;
    s.writer.context=&s;s.writer.capabilities=p->nand.capabilities;s.writer.is_bad=bad;s.writer.read=read_nand;
    s.writer.erase=erase;s.writer.write_page=write_page;s.writer.verify_boot=verify_boot;s.writer.watchdog=watchdog;s.writer.progress=report_progress;
    if(p->listen(p->context,0xc0a80901u,80))return -1;
    bw_set_poll(watchdog,&s);
    for(;;) {
        if(p->stop_requested&&p->stop_requested(p->context))break;
        p->poll(p->context);serve_one(&s,0);
        if(s.task.state==BW_QUEUED)bw_task_run(&s.task,&s.writer,p->scratch,p->scratch_capacity);
    }
    /* Do not leave a callback to this stack frame after a RAM-only exit. */
    bw_set_poll(0,0);return 0;
}
