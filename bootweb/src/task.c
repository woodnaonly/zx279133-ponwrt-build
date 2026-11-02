/* SPDX-License-Identifier: GPL-2.0-only */
#include "internal.h"
int bw_task_init(struct bw_task *t,void *buffer,size_t cap,const char *base,uint32_t layout,const char *token) {
    if(!t||!buffer||cap<1536||cap>BW_MAX_UPLOAD||!bw_hex64(base)||!bw_hex64(token)||layout>2)return -1;
    bw_zero(t,sizeof(*t));t->buffer=buffer;t->capacity=cap;t->layout=layout;
    bw_copy(t->base,base,65);bw_copy(t->token,token,65);return 0;
}
int bw_task_begin(struct bw_task *t,size_t n,uint64_t id) {
    if(t->state!=BW_IDLE||!id)return bw_fail(t->error,"another task exists; explicit cancel required");
    if(n<1536||n>t->capacity)return bw_fail(t->error,"invalid upload length");
    t->id=id;t->expected=n;t->received=0;t->state=BW_RECEIVING;t->error[0]=0;return 0;
}
int bw_task_feed(struct bw_task *t,const void *p,size_t n) {
    if(t->state!=BW_RECEIVING)return bw_fail(t->error,"no receiving task");
    if(n>t->expected-t->received) {t->state=BW_FAILED;return bw_fail(t->error,"upload exceeds Content-Length");}
    bw_copy(t->buffer+t->received,p,n);t->received+=n;return 0;
}
int bw_task_finish(struct bw_task *t) {
    if(t->state!=BW_RECEIVING)return bw_fail(t->error,"upload is not receiving");
    if(t->received!=t->expected) {t->state=BW_FAILED;return bw_fail(t->error,"truncated upload; nothing erased");}
    t->state=BW_VALIDATING;
    if(bw_validate(t->buffer,t->received,t->base,t->layout,&t->result,t->error)) {t->state=BW_FAILED;return -1;}
    t->state=BW_READY;return 0;
}
int bw_task_confirm(struct bw_task *t,uint64_t id,const char *hash,const char *token) {
    if(!bw_hex64(token)||!bw_equal(t->token,token,65)||!bw_hex64(hash)||id!=t->id||
       !bw_equal(hash,t->result.package_sha256,65))return bw_fail(t->error,"wrong task/token/package digest");
    if(t->state==BW_QUEUED||t->state==BW_WRITING||t->state==BW_SUCCESS)return 0;
    if(t->state!=BW_READY)return bw_fail(t->error,"task is not validated and ready");
    t->state=BW_QUEUED;return 0;
}
int bw_task_cancel(struct bw_task *t,const char *token) {
    if(!bw_hex64(token)||!bw_equal(t->token,token,65))return bw_fail(t->error,"invalid token");
    if(t->state==BW_QUEUED||t->state==BW_WRITING||t->state==BW_SUCCESS)
        return bw_fail(t->error,"cannot clear a queued/writing/successful task (reboot required)");
    t->state=BW_IDLE;t->received=0;t->expected=0;t->id=0;t->error[0]=0;
    bw_zero(&t->result,sizeof(t->result));return 0;
}
int bw_task_run(struct bw_task *t,const struct bw_port *p,void *work,size_t n) {
    char digest[65];
    if(t->state!=BW_QUEUED)return bw_fail(t->error,"no queued task (never replay completed flash)");
    t->state=BW_WRITING;
    bw_sha256(t->buffer,t->received,digest);
    if(!bw_equal(digest,t->result.package_sha256,65)) {t->state=BW_FAILED;return bw_fail(t->error,"frozen upload changed after confirmation");}
    if(bw_execute(t->buffer,t->received,t->base,t->layout,p,work,n,t->error)) {t->state=BW_FAILED;return -1;}
    t->state=BW_SUCCESS;return 0;
}
