/* SPDX-License-Identifier: GPL-2.0-only
 * Linux userspace, READ-ONLY SSH test harness. This is NOT a U-Boot adapter,
 * recovery kernel or persistent service. All MTD opens are O_RDONLY, there
 * are no flash ioctls, and erase/write callbacks unconditionally fail.
 * HTTP listens on LOOPBACK:18088 only, for a short-lived SSH tunnel test.
 */
#define _POSIX_C_SOURCE 200809L
#include "platform.h"
#include "internal.h"
#include <arpa/inet.h>
#include <errno.h>
#include <fcntl.h>
#include <poll.h>
#include <signal.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/random.h>
#include <sys/socket.h>
#include <sys/stat.h>
#include <sys/time.h>
#include <time.h>
#include <unistd.h>

#define LAB_UPLOAD (32u * 1024u * 1024u)
#define LAB_PORT 18088
#define LAB_LIFETIME 180000u
struct lab {int boot,tags,listener;unsigned denied,connections,receive_error,body_seen;size_t received,sent;uint64_t deadline;};
static volatile sig_atomic_t interrupted;
static void stop_signal(int signal_number) {(void)signal_number;interrupted=1;}
static uint64_t now(void *unused) {
    struct timespec t;(void)unused;
    if(clock_gettime(CLOCK_MONOTONIC,&t))return 0;
    return (uint64_t)t.tv_sec*1000u+(uint64_t)t.tv_nsec/1000000u;
}
static int text_file(const char *path,char *out,size_t capacity) {
    int fd=open(path,O_RDONLY|O_CLOEXEC|O_NOFOLLOW);ssize_t n;
    if(fd<0)return -1;n=read(fd,out,capacity-1);close(fd);
    if(n<=0||(size_t)n>=capacity-1)return -1;
    out[n]=0;while(n&&(out[n-1]=='\n'||out[n-1]=='\r'))out[--n]=0;return 0;
}
static int geometry(void) {
    static const char *names[]={"boot","kernel","dtb","parameter tags","root","scratch"};
    static const unsigned offsets[]={0,0x200000,0xa00000,0xb00000,0xf00000,0xff00000};
    static const unsigned sizes[]={0x200000,0x800000,0x100000,0x400000,0xf000000,0x100000};
    const char *keys[]={"offset","size","erasesize","writesize","bad_blocks","ecc_failures"};
    char path[96],value[256];unsigned i,j;
    if(text_file("/sys/firmware/devicetree/base/model",value,sizeof(value))||!strstr(value,"SK-D840N"))return -1;
    for(i=0;i<6;i++) {
        unsigned expected[]={offsets[i],sizes[i],BW_ERASE_SIZE,BW_PAGE_SIZE,0,0};
        snprintf(path,sizeof(path),"/sys/class/mtd/mtd%u/name",i+1);
        if(text_file(path,value,sizeof(value))||strcmp(value,names[i]))return -1;
        for(j=0;j<sizeof(keys)/sizeof(keys[0]);j++) {
            char *end;unsigned long v;
            snprintf(path,sizeof(path),"/sys/class/mtd/mtd%u/%s",i+1,keys[j]);
            if(text_file(path,value,sizeof(value)))return -1;
            errno=0;v=strtoul(value,&end,10);
            if(errno||!*value||*end||v!=expected[j])return -1;
        }
    }
    return 0;
}
static int open_mtd(const char *path,unsigned minor_number) {
    int fd=open(path,O_RDONLY|O_CLOEXEC|O_NOFOLLOW);struct stat st;
    if(fd<0)return -1;
    /* Linux dev_t, checked against the fixed /dev/mtd{1,4} character nodes. */
    if(fstat(fd,&st)||!S_ISCHR(st.st_mode)||st.st_rdev!=((dev_t)90u<<8|minor_number)){close(fd);return -1;}
    return fd;
}
static int read_flash(void *ctx,uint32_t offset,void *out,size_t n) {
    struct lab *l=ctx;int fd;uint32_t at;size_t done=0;
    if(offset<0x200000&&n<=0x200000-offset){fd=l->boot;at=offset;}
    else if(offset>=0xb00000&&offset<0xf00000&&n<=0xf00000-offset){fd=l->tags;at=offset-0xb00000;}
    else return -1;
    while(done<n) {
        ssize_t z=pread(fd,(char *)out+done,n-done,(off_t)at+(off_t)done);
        if(z<0&&errno==EINTR)continue;
        if(z<=0||(size_t)z>n-done)return -1;done+=(size_t)z;
    }
    return 0;
}
static int hash_region(struct lab *l,uint32_t offset,uint32_t n,char hash[65]) {
    uint8_t block[BW_ERASE_SIZE];struct bw_hash h;bw_hash_init(&h);
    while(n) {uint32_t take=n>sizeof(block)?sizeof(block):n;
        if(read_flash(l,offset,block,take))return -1;
        bw_hash_add(&h,block,take);offset+=take;n-=take;}
    bw_hash_end(&h,hash);return 0;
}
static int hashes(struct lab *l,char values[5][65]) {
    return hash_region(l,0,0x20000,values[0])||hash_region(l,0x20000,0x160000,values[1])||
           hash_region(l,0x180000,0x20000,values[2])||hash_region(l,0x1a0000,0x60000,values[3])||
           hash_region(l,0xb00000,0x400000,values[4]);
}
static void emit_hashes(const char *stage,char values[5][65]) {
    printf("{\"event\":\"%s\",\"linux_userspace_only\":true,\"read_only\":true,"
           "\"stage1\":\"%s\",\"stage2\":\"%s\",\"environment\":\"%s\","
           "\"boot_tail\":\"%s\",\"parameter_tags\":\"%s\"}\n",
           stage,values[0],values[1],values[2],values[3],values[4]);fflush(stdout);
}
static int is_bad(void *ctx,uint32_t offset) {(void)ctx;(void)offset;return -1; /* no unverified BBT adapter */}
static int deny_erase(void *ctx,uint32_t offset) {struct lab *l=ctx;(void)offset;l->denied++;return -1;}
static int deny_page(void *ctx,uint32_t offset,const void *page) {struct lab *l=ctx;(void)offset;(void)page;l->denied++;return -1;}
static void poll_lab(void *ctx) {(void)ctx; /* Linux owns NIC/watchdog; not a vendor watchdog test */}
static int stop_requested(void *ctx) {struct lab *l=ctx;return interrupted||now(0)>=l->deadline;}
static int listen_loopback(void *ctx,uint32_t address,uint16_t port) {
    struct lab *l=ctx;struct sockaddr_in a;int fd;
    if(address!=0xc0a80901u||port!=80)return -1;
    fd=socket(AF_INET,SOCK_STREAM|SOCK_CLOEXEC,0);if(fd<0)return -1;
    memset(&a,0,sizeof(a));a.sin_family=AF_INET;a.sin_addr.s_addr=htonl(INADDR_LOOPBACK);a.sin_port=htons(LAB_PORT);
    if(bind(fd,(struct sockaddr *)&a,sizeof(a))||listen(fd,3)){close(fd);return -1;}
    l->listener=fd;
    printf("{\"event\":\"listening\",\"address\":\"127.0.0.1:18088\",\"capabilities\":0,"
           "\"lifetime_ms\":%u,\"native_uboot_verified\":false}\n",LAB_LIFETIME);fflush(stdout);return 0;
}
static int accept_stream(void *ctx) {
    struct lab *l=ctx;struct pollfd p={l->listener,POLLIN,0};struct timeval timeout={1,0};int fd;
    if(stop_requested(l)||poll(&p,1,20)<=0)return 0;
    fd=accept(l->listener,0,0);if(fd<0)return 0;
    if(fd==0){close(fd);return -1;}
    if(setsockopt(fd,SOL_SOCKET,SO_RCVTIMEO,&timeout,sizeof(timeout))||
       setsockopt(fd,SOL_SOCKET,SO_SNDTIMEO,&timeout,sizeof(timeout))){close(fd);return -1;}
    l->connections++;l->received=l->sent=0;l->receive_error=l->body_seen=0;
    return fd;
}
static int receive_stream(void *ctx,int fd,void *out,size_t n,uint32_t timeout) {
    struct lab *l=ctx;ssize_t z;(void)timeout;if(stop_requested(ctx))return -1;
    do {z=recv(fd,out,n,0);}while(z<0&&errno==EINTR&&!interrupted);
    if(z>0) {
        l->received+=(size_t)z;
        /* The service requests headers one byte at a time. A larger read is
         * the body. Acknowledging it in the private log lets the SSH test
         * disconnect AFTER delivery, not race PuTTY's channel-open/EOF. */
        if(n>1&&!l->body_seen) {
            l->body_seen=1;
            printf("{\"event\":\"body_received\",\"connection\":%u,\"body_bytes\":%zd,"
                   "\"received_bytes\":%zu}\n",l->connections,z,l->received);fflush(stdout);
        }
    } else if(z<0)l->receive_error=(unsigned)errno;
    return (int)z;
}
static int send_stream(void *ctx,int fd,const void *data,size_t n) {
    struct lab *l=ctx;ssize_t z;if(stop_requested(ctx))return -1;
    do {z=send(fd,data,n,MSG_NOSIGNAL);}while(z<0&&errno==EINTR&&!interrupted);
    if(z>0)l->sent+=(size_t)z;
    return (int)z;
}
static void close_stream(void *ctx,int fd) {
    struct lab *l=ctx;close(fd);
    printf("{\"event\":\"connection_closed\",\"connection\":%u,\"received_bytes\":%zu,"
           "\"sent_bytes\":%zu,\"receive_errno\":%u}\n",l->connections,l->received,l->sent,l->receive_error);fflush(stdout);
}
static int entropy(char token[65]) {
    static const char hex[]="0123456789abcdef";uint8_t bytes[32];size_t at=0,i;
    while(at<sizeof(bytes)) {ssize_t n=getrandom(bytes+at,sizeof(bytes)-at,0);
        if(n<0&&errno==EINTR)continue;if(n<=0)return -1;at+=(size_t)n;}
    for(i=0;i<sizeof(bytes);i++){token[2*i]=hex[bytes[i]>>4];token[2*i+1]=hex[bytes[i]&15];}
    token[64]=0;return 0;
}
int main(int argc,char **argv) {
    struct lab l;struct bw_platform p;char before[5][65],after[5][65],hash[65];int rc=1;
    if(argc!=2||(strcmp(argv[1],"--probe")&&strcmp(argv[1],"--serve"))) {
        fputs("usage: bootweb-linux-readonly --probe | --serve\n",stderr);return 2;
    }
    bw_sha256("abc",3,hash);
    if(strcmp(hash,"ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad")||
       bw_crc32("123456789",9)!=0xcbf43926u||bw_test_build())return 3;
    memset(&l,0,sizeof(l));l.boot=l.tags=l.listener=-1;memset(&p,0,sizeof(p));
    if(geometry()){fputs("unrecognized geometry/board or MTD health counters\n",stderr);goto done;}
    l.boot=open_mtd("/dev/mtd1",2);l.tags=open_mtd("/dev/mtd4",8);
    if(l.boot<0||l.tags<0||hashes(&l,before)||strcmp(before[1],BW_BASE)) {
        fputs("read-only MTD/base verification failed\n",stderr);goto done;
    }
    emit_hashes("before",before);rc=0;
    if(!strcmp(argv[1],"--serve")) {
        p.upload=malloc(LAB_UPLOAD);p.scratch=malloc(BW_ERASE_SIZE);
        if(!p.upload||!p.scratch||entropy(p.token)){rc=4;goto done;}
        p.context=&l;p.nand.context=&l;p.nand.capabilities=0;p.nand.read=read_flash;p.nand.is_bad=is_bad;
        p.nand.erase=deny_erase;p.nand.write_page=deny_page;p.layout=BW_LAYOUT_CURRENT;
        p.upload_capacity=LAB_UPLOAD;p.scratch_capacity=BW_ERASE_SIZE;
        p.listen=listen_loopback;p.accept=accept_stream;p.recv=receive_stream;p.send=send_stream;
        p.close=close_stream;p.poll=poll_lab;p.milliseconds=now;p.stop_requested=stop_requested;
        /* p.reboot and p.nand.verify_boot remain NULL. No privileged action. */
        signal(SIGTERM,stop_signal);signal(SIGINT,stop_signal);
        l.deadline=now(0)+LAB_LIFETIME;rc=bw_serve(&p)?5:0;
    }
    if(hashes(&l,after)||memcmp(before,after,sizeof(before)))rc=6;
    else emit_hashes("after",after);
    printf("{\"event\":\"finished\",\"exit_code\":%d,\"denied_write_calls\":%u,"
           "\"flashable_release\":false,\"native_uboot_verified\":false}\n",rc,l.denied);
    if(l.denied)rc=7;
done:
    if(l.listener>=0)close(l.listener);if(l.boot>=0)close(l.boot);if(l.tags>=0)close(l.tags);
    free(p.upload);free(p.scratch);return rc;
}
