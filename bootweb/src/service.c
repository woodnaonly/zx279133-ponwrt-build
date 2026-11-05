/* SPDX-License-Identifier: GPL-2.0-only
 * Tiny HTTP service; no filesystem, command interpreter, multipart or templates.
 * This is compiled into the unadapted ELF, but cannot run without a verified
 * vendor network/NAND/watchdog/entropy adapter. */
#include "platform.h"
#include "internal.h"
#include "web.inc"
#ifdef BW_RAM_BACKUP
#include "backup.h"
#ifndef BW_RAM_READONLY
#error "backup route requires writes to be compiled out"
#endif
#endif
struct server {
    struct bw_platform *p;
    struct bw_task task;
    struct bw_port writer;
    uint64_t next_id;
    const char *phase;
    uint32_t done,total;
};
struct output {char data[3072];size_t n;int overflow;};
static int service_stopped(struct server *s) {return s->p->stop_requested&&s->p->stop_requested(s->p->context);}
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
    uint64_t deadline=s->p->milliseconds(s->p->context)+(s->p->progress_io?100u:5000u);
    while(n) {int z;if(service_stopped(s)||s->p->milliseconds(s->p->context)>=deadline)return -1;z=s->p->send(s->p->context,fd,p,n);if(z<=0||(size_t)z>n)return -1;p+=z;n-=z;s->p->poll(s->p->context);}return 0;
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
static const char *lock_reason(struct server *s) {
#ifdef BW_RAM_READONLY
    return s->p->resident_version?"Resident read-only adapter: NAND writes are compiled out":"RAM-only artifact: writes are compiled out";
#else
    uint32_t caps=s->writer.capabilities;
    if(s->p->update_lock) {
        const char *reason=s->p->update_lock(s->p->context,&s->task.result);
        if(reason&&reason[0])return reason;
    }
    if((caps&BW_REQUIRED)!=BW_REQUIRED)return "RAM network / ECC / current backup evidence is incomplete";
    if(s->task.layout==1&&!(caps&BW_CAP_NATIVE_LAYOUT))return "native241 partition layout is not verified";
    if(s->task.layout==2&&!(caps&BW_CAP_RECOVERY_LAYOUT))return "recovery237 partition layout is not verified";
    if(s->task.result.kind==BW_UBOOT) {
        if(!(caps&BW_CAP_BOOT_CONTAINER)||(!(caps&BW_CAP_SERIAL_RESCUE)&&!s->p->nand.authorize_no_rescue))
            return "BTL rescue / boot container evidence is incomplete";
        if(!s->p->nand.verify_boot||!s->p->nand.prepare_uboot_write||!s->p->nand.milliseconds)
            return "native boot / cold-power / RAM recovery-data preflight is unavailable";
    }
    return "";
#endif
}
static int enabled(struct server *s) {return !lock_reason(s)[0];}
static void state(struct server *s,int fd,int code) {
    static const char *states[]={"idle","receiving","validating","ready","queued","writing","success","failed","cancelled"};
    static const char *layouts[]={"current240","native241","recovery237"};
    struct output o;unsigned i;bw_zero(&o,sizeof(o));
#ifdef BW_RAM_READONLY
    if(s->p->resident_version)
        append(&o,"{\"board\":\"" BW_BOARD "\",\"mode\":\"native-resident-readonly\",\"notice\":\"Installed resident image verified by ECC; NAND writes and reboot are compiled out\",\"base\":\"");
    else append(&o,"{\"board\":\"" BW_BOARD "\",\"mode\":\"native-ram-readonly\",\"notice\":\"RAM ONLY: NAND writes and reboot are compiled out; hardware acceptance incomplete\",\"base\":\"");
#else
    if(s->p->resident_version)
        append(&o,"{\"board\":\"" BW_BOARD "\",\"mode\":\"native-resident\",\"notice\":\"ECC-verified resident image; updates require current backup and bounded power budget\",\"base\":\"");
    else append(&o,"{\"board\":\"" BW_BOARD "\",\"mode\":\"native-development\",\"notice\":\"DEVELOPMENT: hardware acceptance incomplete; not a flashable release\",\"base\":\"");
#endif
    append(&o,s->task.base);append(&o,"\",\"version\":\"");
    append(&o,s->p->resident_version?s->p->resident_version:BW_VERSION);
    append(&o,"\",\"resident\":");append(&o,s->p->resident_version?"true":"false");
    append(&o,",\"vendor_abi_sha256\":\"");
    append(&o,s->task.vendor_abi_sha256);append(&o,"\",\"installed_sha256\":\"");append(&o,s->task.base);
    append(&o,"\",\"lock_reason\":\"");append(&o,lock_reason(s));append(&o,"\",\"layout\":\"");append(&o,layouts[s->task.layout]);
    append(&o,"\",\"power_mode\":\"");append(&o,s->p->require_continuous_power?"continuous_required":"unchanged");
    append(&o,"\",\"update_bound_ms\":");number(&o,s->p->update_bound_ms);
    append(&o,",\"token\":\"");append(&o,s->task.token);append(&o,"\",\"state\":\"");append(&o,states[s->task.state]);
    append(&o,"\",\"id\":\"");number(&o,s->task.id);append(&o,"\",\"error\":\"");append(&o,s->task.error);
    append(&o,"\",\"phase\":\"");append(&o,s->phase);append(&o,"\",\"done\":");number(&o,s->done);append(&o,",\"total\":");number(&o,s->total);
    append(&o,",\"can_confirm\":");append(&o,enabled(s)?"true":"false");
#ifdef BW_RAM_BACKUP
    append(&o,",\"can_backup\":true,\"can_finish_backup\":true,\"backup_format\":\"physical-ecc-v1\"");
#endif
    append(&o,",\"can_reboot\":");
#ifdef BW_RAM_READONLY
    append(&o,"false");
#else
    append(&o,s->task.state==BW_QUEUED||s->task.state==BW_WRITING||!s->p->reboot||
           (s->p->require_continuous_power&&s->task.state!=BW_SUCCESS)?"false":"true");
#endif
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
    char header[4096],error[BW_ERROR_SIZE],body[192];struct bw_request r;size_t n=0;int fd=s->p->accept(s->p->context);
    uint64_t deadline=s->p->milliseconds(s->p->context)+(fast?100u:5000u);
    if(fd<=0)return;
    while(n<sizeof(header)) {
        if(service_stopped(s)||s->p->milliseconds(s->p->context)>=deadline)goto close;
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
#ifdef BW_RAM_BACKUP
    if(bw_string(r.path,"/api/backup-finish")) {
        static const char result[]="{\"finished\":true,\"reboot\":false}";
        if(s->task.state!=BW_IDLE||r.length) {reject(s,fd,"backup finish requires idle task and empty body");goto close;}
        if(!reply(s,fd,200,"application/json",result,sizeof(result)-1))bw_backup_finish();
        goto close;
    }
    if(bw_string(r.path,"/api/backup")) {
        size_t at=0,split=0;uint64_t off=0,length=0;
        if(s->task.state!=BW_IDLE||!r.length||r.length>32) {reject(s,fd,"backup requires idle task and bounded range");goto close;}
        while(at<r.length) {
            int z;if(service_stopped(s)||s->p->milliseconds(s->p->context)>=deadline)goto close;
            z=s->p->recv(s->p->context,fd,body+at,(size_t)r.length-at,5000);
            if(z<=0||(size_t)z>r.length-at)goto close;at+=(size_t)z;
        }
        while(split<at&&body[split]!='\n')split++;
        if(split>=at||at<split+3||body[at-1]!='\n'||bw_decimal((uint8_t *)body,split,&off)||
           bw_decimal((uint8_t *)body+split+1,at-split-2,&length)||off>UINT32_MAX||length>UINT32_MAX) {
            reject(s,fd,"invalid physical backup offset/length");goto close;
        }
        if(bw_backup_capture(&s->p->nand,(uint32_t)off,(uint32_t)length,s->p->upload,s->p->upload_capacity,error))
            reject(s,fd,error);
        else {
            size_t packed=bw_backup_pack(s->p->upload,s->p->upload_capacity);
            if(!packed)goto close;
            reply(s,fd,200,"application/octet-stream",s->p->upload,packed);
        }
        goto close;
    }
#endif
    if(bw_string(r.path,"/api/upload")) {
        if(bw_task_begin(&s->task,(size_t)r.length,++s->next_id)) {reject(s,fd,s->task.error);goto close;}
        deadline=s->p->milliseconds(s->p->context)+300000u;
        while(s->task.received<s->task.expected) {
            size_t want=s->task.expected-s->task.received;int z;if(want>65536)want=65536;
            if(service_stopped(s))goto close;
            if(s->p->milliseconds(s->p->context)>=deadline) {bw_task_finish(&s->task);goto close;}
            z=s->p->recv(s->p->context,fd,s->task.buffer+s->task.received,want,30000);
            if(z<=0||(size_t)z>want) {bw_task_finish(&s->task);goto close;}
            bw_task_feed(&s->task,s->task.buffer+s->task.received,(size_t)z);s->p->poll(s->p->context);
        }
        if(service_stopped(s))goto close;
        bw_task_finish(&s->task);state(s,fd,s->task.state==BW_READY?200:409);
    } else if(bw_string(r.path,"/api/confirm")) {
        size_t at=0,split=0,base_length;uint64_t id=0;int continuous;
        if(!enabled(s)) {reject(s,fd,lock_reason(s));goto close;}
        if(r.length>=sizeof(body)||r.length<132) {reject(s,fd,"confirmation requires task / package / installed-image digests");goto close;}
        while(at<r.length) {int z;if(s->p->milliseconds(s->p->context)>=deadline)goto close;
            z=s->p->recv(s->p->context,fd,body+at,(size_t)r.length-at,5000);if(z<=0||(size_t)z>r.length-at)goto close;at+=(size_t)z;}
        while(split<at&&body[split]!='\n')split++;
        continuous=s->p->require_continuous_power&&s->task.result.kind==BW_SYSTEM;
        base_length=split+131;
        if(split==at||bw_decimal((uint8_t *)body,split,&id)||at!=base_length+(continuous?17u:0u)||
           body[split+65]!='\n'||body[base_length-1]!='\n'||
           (continuous&&!bw_equal(body+base_length,"continuous-power\n",17))) {
            reject(s,fd,continuous?"确认必须绑定任务、包、当前镜像并明确持续供电":"invalid confirmation body");goto close;
        }
        body[split+65]=0;body[base_length-1]=0;
        if(bw_task_confirm_identity(&s->task,id,body+split+1,body+split+66,r.token))reject(s,fd,s->task.error);
        else {s->task.continuous_power_confirmed=(uint32_t)continuous;state(s,fd,202);} /* execution is after close, even if reply disconnects */
    } else if(bw_string(r.path,"/api/cancel")&&r.length==0) {
        if(bw_task_cancel(&s->task,r.token))reject(s,fd,s->task.error);else state(s,fd,200);
#ifndef BW_RAM_READONLY
    } else if(bw_string(r.path,"/api/reboot")&&r.length==0&&s->p->reboot) {
        if(s->p->require_continuous_power&&s->task.state!=BW_SUCCESS) {reject(s,fd,"仅写后读回及保护区审计成功后允许重启");goto close;}
        state(s,fd,202);s->p->close(s->p->context,fd);s->p->reboot(s->p->context);return;
#endif
    } else reject(s,fd,"POST route/body not allowed");
close:
    s->p->close(s->p->context,fd);
}
static void report_progress(void *ctx,const char *phase,uint32_t done,uint32_t total) {
    struct server *s=ctx;s->phase=phase;s->done=done;s->total=total;
    if(s->p->nand.progress)s->p->nand.progress(s->p->nand.context,phase,done,total);
    s->p->progress_io=1;serve_one(s,1);s->p->progress_io=0;
}
static int bad(void *v,uint32_t off) {struct server *s=v;return s->p->nand.is_bad(s->p->nand.context,off);}
static int read_nand(void *v,uint32_t off,void *p,size_t n) {struct server *s=v;return s->p->nand.read(s->p->nand.context,off,p,n);}
static int erase(void *v,uint32_t off) {struct server *s=v;return s->p->nand.erase(s->p->nand.context,off);}
static int write_page(void *v,uint32_t off,const void *p) {struct server *s=v;return s->p->nand.write_page(s->p->nand.context,off,p);}
static int verify_boot(void *v,const void *p,size_t n) {struct server *s=v;return s->p->nand.verify_boot?s->p->nand.verify_boot(s->p->nand.context,p,n):-1;}
static int verify_identity(void *v,const struct bw_identity *id) {
    struct server *s=v;
    return s->p->nand.verify_identity?s->p->nand.verify_identity(s->p->nand.context,id):
           (bw_string(id->installed_sha256,BW_VENDOR_ABI_SHA256)?0:-1);
}
static int prepare_uboot(void *v,const struct bw_identity *id,const void *p,size_t n,struct bw_uboot_budget *budget) {
    struct server *s=v;
    return s->p->nand.prepare_uboot_write?s->p->nand.prepare_uboot_write(s->p->nand.context,id,p,n,budget):-1;
}
static uint64_t nand_time(void *v) {struct server *s=v;return s->p->nand.milliseconds?s->p->nand.milliseconds(s->p->nand.context):0;}
static int authorize_no_rescue(void *v,const struct bw_identity *id,const char *digest) {
    struct server *s=v;
    return s->p->nand.authorize_no_rescue?s->p->nand.authorize_no_rescue(s->p->nand.context,id,digest):-1;
}
static void watchdog(void *v) {struct server *s=v;s->p->poll(s->p->context);if(s->p->nand.watchdog)s->p->nand.watchdog(s->p->nand.context);}
int bw_serve(struct bw_platform *p) {
    struct server s;struct bw_identity identity;char base[65];
    if(!p||!p->listen||!p->accept||!p->recv||!p->send||!p->close||!p->poll||!p->milliseconds||!p->nand.read||
       !p->nand.is_bad||!p->nand.erase||!p->nand.write_page||!p->scratch||p->scratch_capacity<BW_ERASE_SIZE)return -1;
    bw_zero(&s,sizeof(s));s.p=p;s.phase="idle";
    /* Installed fingerprint comes from fixed ECC reads, never from HTTP input. */
    {struct bw_hash h;uint32_t off;bw_hash_init(&h);
     for(off=BW_STAGE2_START;off<BW_STAGE2_END;off+=BW_ERASE_SIZE) {
         p->poll(p->context);if(p->nand.read(p->nand.context,off,p->scratch,BW_ERASE_SIZE))return -1;
         bw_hash_add(&h,p->scratch,BW_ERASE_SIZE);
     }bw_hash_end(&h,base);}
    bw_copy(identity.vendor_abi_sha256,BW_VENDOR_ABI_SHA256,65);bw_copy(identity.installed_sha256,base,65);
    if(verify_identity(&s,&identity)||
       bw_task_init_identity(&s.task,p->upload,p->upload_capacity,&identity,p->layout,p->token))return -1;
    s.writer.context=&s;s.writer.capabilities=p->nand.capabilities;s.writer.is_bad=bad;s.writer.read=read_nand;
    s.writer.erase=erase;s.writer.write_page=write_page;s.writer.verify_boot=verify_boot;s.writer.watchdog=watchdog;s.writer.progress=report_progress;
    s.writer.verify_identity=verify_identity;s.writer.prepare_uboot_write=prepare_uboot;s.writer.milliseconds=nand_time;
    if(p->nand.authorize_no_rescue)s.writer.authorize_no_rescue=authorize_no_rescue;
    if(p->listen(p->context,0xc0a80901u,80))return -1;
    bw_set_poll(watchdog,&s);
#if defined(BW_RAM_READONLY) || defined(BW_RESIDENT_NATIVE_WRITE)
    bw_set_cancel(p->stop_requested,p->context);
#endif
    for(;;) {
        if(p->stop_requested&&p->stop_requested(p->context))break;
        p->poll(p->context);serve_one(&s,0);
#ifndef BW_RAM_READONLY
        if(s.task.state==BW_QUEUED) {
            if(p->prepare_update&&p->prepare_update(p->context,&s.task,s.task.error))s.task.state=BW_FAILED;
            else bw_task_run(&s.task,&s.writer,p->scratch,p->scratch_capacity);
            if(p->finish_update)p->finish_update(p->context,s.task.state==BW_SUCCESS);
        }
#endif
    }
    /* Do not leave a callback to this stack frame after a RAM-only exit. */
    bw_set_poll(0,0);bw_set_cancel(0,0);return 0;
}
