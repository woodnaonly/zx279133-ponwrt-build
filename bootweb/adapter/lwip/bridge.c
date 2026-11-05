/* SPDX-License-Identifier: GPL-2.0-only
 * Minimal raw-API TCP stream adapter. lwIP is BSD licensed and supplied from a
 * separate source directory; no Airoha hardware code is linked. One instance.
 */
#include "net_bridge.h"
#include "internal.h"
#include "lwip/init.h"
#include "lwip/netif.h"
#include "lwip/tcp.h"
#include "lwip/priv/tcp_priv.h"
#include "lwip/timeouts.h"
#include "lwip/etharp.h"
#include "netif/ethernet.h"

#define STREAMS 3
#define RX_RING 16384u
#define CLOSE_DRAIN_BYTES (256u * 1024u)
#define CLOSE_DRAIN_MS 200u
struct connection {struct tcp_pcb *pcb;unsigned state,eof,head,count,discarded;uint8_t data[RX_RING];};
static struct {
    struct bw_ethernet eth;
    struct netif iface;
    struct tcp_pcb *listener;
    struct connection streams[STREAMS];
    struct bw_platform *platform;
    unsigned initialized,servicing,fault,iface_added;
    _Alignas(64) uint8_t tx[1536];
} net;
uint32_t bw_lwip_random(void) {return net.eth.random32?net.eth.random32(net.eth.context):0;}
_Noreturn void bw_lwip_assert(void) {net.fault=1;for(;;){} /* fatal invariant; never continue flashing */}
u32_t sys_now(void) {return net.eth.milliseconds?(u32_t)net.eth.milliseconds(net.eth.context):0;}
static void pump(void *unused);
static int stopped(void) {
    return net.platform&&net.platform->stop_requested&&
        net.platform->stop_requested(net.platform->context);
}
static err_t transmit_frame(struct netif *iface,struct pbuf *p) {
    (void)iface;
    if(net.fault||stopped()||p->tot_len>sizeof(net.tx)||pbuf_copy_partial(p,net.tx,p->tot_len,0)!=p->tot_len)return ERR_IF;
    return net.eth.send_frame(net.eth.context,net.tx,p->tot_len)<0?ERR_IF:ERR_OK;
}
static err_t interface_init(struct netif *iface) {
    iface->name[0]='b';iface->name[1]='w';iface->hwaddr_len=6;
    bw_copy(iface->hwaddr,net.eth.mac,6);iface->mtu=1500;
    iface->flags=NETIF_FLAG_BROADCAST|NETIF_FLAG_ETHARP;
    iface->output=etharp_output;iface->linkoutput=transmit_frame;return ERR_OK;
}
static void receive_frame(void *unused,const void *frame,size_t n) {
    struct pbuf *p;(void)unused;
    if(net.fault||n<14||n>1536)return;
    p=pbuf_alloc(PBUF_RAW,(u16_t)n,PBUF_POOL);if(!p)return;
    if(pbuf_take(p,frame,(u16_t)n)!=ERR_OK||net.iface.input(p,&net.iface)!=ERR_OK)pbuf_free(p);
}
static void pump(void *unused) {
    (void)unused;
    if(net.servicing||net.fault||stopped())return;net.servicing=1;
    if(net.eth.watchdog)net.eth.watchdog(net.eth.context);
    if(net.initialized) {
        if(net.eth.poll_frames(net.eth.context,receive_frame,0)<0)net.fault=1;
        sys_check_timeouts();
    }
    net.servicing=0;
}
static void error_callback(void *arg,err_t error) {
    struct connection *c=arg;(void)error;
    if(c){if(c->state==1)bw_zero(c,sizeof(*c));else{c->pcb=0;c->eof=1;c->state=3;}}
}
static err_t receive_callback(void *arg,struct tcp_pcb *pcb,struct pbuf *p,err_t error) {
    struct connection *c=arg;size_t i;(void)pcb;
    if(!c)return ERR_ARG;
    if(!p){c->eof=1;return ERR_OK;}
    if(error!=ERR_OK)return error;
    if(c->state==4) {
        /* A rejected POST may still have its body in flight. It is discarded,
         * never handed back to HTTP or interpreted as another request. */
        if(p->tot_len>CLOSE_DRAIN_BYTES-c->discarded)return ERR_MEM;
        c->discarded+=p->tot_len;tcp_recved(pcb,p->tot_len);pbuf_free(p);return ERR_OK;
    }
    if(p->tot_len>RX_RING-c->count)return ERR_MEM; /* lwIP retains and retries p */
    for(i=0;i<p->tot_len;) {
        unsigned at=(c->head+c->count+(unsigned)i)%RX_RING;u16_t z=(u16_t)(RX_RING-at);
        if(z>p->tot_len-i)z=(u16_t)(p->tot_len-i);
        if(pbuf_copy_partial(p,c->data+at,z,(u16_t)i)!=z)return ERR_BUF;
        i+=z;
    }
    c->count+=p->tot_len;
    pbuf_free(p);return ERR_OK;
}
static err_t accept_callback(void *arg,struct tcp_pcb *pcb,err_t error) {
    unsigned i;(void)arg;if(error!=ERR_OK)return error;
    for(i=0;i<STREAMS;i++)if(!net.streams[i].state)break;
    if(i==STREAMS){tcp_abort(pcb);return ERR_ABRT;}
    bw_zero(&net.streams[i],sizeof(net.streams[i]));net.streams[i].pcb=pcb;net.streams[i].state=1;
    tcp_arg(pcb,&net.streams[i]);tcp_recv(pcb,receive_callback);tcp_err(pcb,error_callback);
    tcp_nagle_disable(pcb);return ERR_OK;
}
static int open_listener(void *unused,uint32_t address,uint16_t port) {
    ip4_addr_t ip,mask,gw;err_t error;struct tcp_pcb *pcb;(void)unused;
    if(net.initialized||address!=0xc0a80901u||port!=80||net.fault)return -1;
    if(net.eth.start(net.eth.context)<0)return -1;
    lwip_init();IP4_ADDR(&ip,192,168,9,1);IP4_ADDR(&mask,255,255,255,0);IP4_ADDR(&gw,0,0,0,0);
    if(!netif_add(&net.iface,&ip,&mask,&gw,0,interface_init,ethernet_input))return -1;
    net.iface_added=1;
    netif_set_default(&net.iface);netif_set_up(&net.iface);netif_set_link_up(&net.iface);
    pcb=tcp_new_ip_type(IPADDR_TYPE_V4);if(!pcb)return -1;
    if(tcp_bind(pcb,IP4_ADDR_ANY,port)!=ERR_OK){tcp_abort(pcb);return -1;}
    net.listener=tcp_listen_with_backlog_and_err(pcb,STREAMS,&error);
    if(!net.listener){tcp_abort(pcb);return -1;}
    tcp_accept(net.listener,accept_callback);net.initialized=1;return 0;
}
static int accept_stream(void *unused) {
    unsigned i;pump(unused);if(net.fault||stopped())return -1;
    for(i=0;i<STREAMS;i++)if(net.streams[i].state==1) {net.streams[i].state=2;return (int)i+1;}return 0;
}
static struct connection *stream(int fd) {return fd>0&&fd<=STREAMS&&net.streams[fd-1].state?&net.streams[fd-1]:0;}
static int read_stream(void *unused,int fd,void *dst,size_t n,uint32_t timeout) {
    struct connection *c=stream(fd);uint64_t start=sys_now();uint8_t *out=dst;size_t done=0;
    if(!c||n>0x7fffffff||stopped())return -1;
    while(!c->count) {
        if(stopped())return -1;
        if(c->eof||net.fault||!c->pcb)return 0;
        pump(unused);if((u32_t)(sys_now()-(u32_t)start)>=timeout)return -1;
    }
    if(n>c->count)n=c->count;
    while(done<n) {out[done++]=c->data[c->head];c->head=(c->head+1)%RX_RING;c->count--;}
    if(c->pcb)tcp_recved(c->pcb,(u16_t)done);return (int)done;
}
static int write_stream(void *unused,int fd,const void *src,size_t n) {
    struct connection *c=stream(fd);u32_t start=sys_now();err_t error;u16_t take;
    u32_t timeout=net.platform&&net.platform->progress_io?25u:5000u;
    if(!c||!c->pcb||net.fault||stopped())return -1;
    if(!n)return 0;
    for(;;) {
        if(!c->pcb||net.fault||stopped())return -1;
        take=tcp_sndbuf(c->pcb);if(take>n)take=(u16_t)n;
        error=take?tcp_write(c->pcb,src,take,TCP_WRITE_FLAG_COPY):ERR_MEM;
        if(error==ERR_OK){tcp_output(c->pcb);return take;}
        if(error!=ERR_MEM||(u32_t)(sys_now()-start)>=timeout)return -1;
        pump(unused);
    }
}
static void close_stream(void *unused,int fd) {
    struct connection *c=stream(fd);struct tcp_pcb *pcb;u32_t start=sys_now();if(!c)return;
    if(c->pcb&&!stopped()&&!net.fault) {
        /* tcp_close with unread RX bytes sends RST, which can erase a valid
         * 409 response at the client. Half-close TX first, credit/discard RX,
         * then allow at most 200 ms / 256 KiB for the peer's remaining body.
         * Both bounds and the absolute service deadline apply to this drain. */
        c->state=4;c->discarded=c->count;
        if(c->count)tcp_recved(c->pcb,(u16_t)c->count);
        c->head=c->count=0;
        if(tcp_shutdown(c->pcb,0,1)==ERR_OK) {
            while(c->pcb&&!c->eof&&!net.fault&&!stopped()&&
                  c->discarded<CLOSE_DRAIN_BYTES&&(u32_t)(sys_now()-start)<CLOSE_DRAIN_MS)pump(unused);
        }
    }
    pcb=c->pcb;
    if(pcb) {
        tcp_arg(pcb,0);tcp_recv(pcb,0);tcp_err(pcb,0);tcp_sent(pcb,0);tcp_poll(pcb,0,0);
        if(tcp_close(pcb)!=ERR_OK)tcp_abort(pcb);
    }
    bw_zero(c,sizeof(*c));
}
static uint64_t milliseconds(void *unused) {(void)unused;return net.eth.milliseconds?net.eth.milliseconds(net.eth.context):0;}
int bw_lwip_bind(struct bw_platform *p,const struct bw_ethernet *eth) {
    if(!p||!eth||!eth->start||!eth->send_frame||!eth->poll_frames||!eth->milliseconds||!eth->random32||net.initialized)return -1;
    /* This binds transport only, NEVER elevates a NAND or flash capability. */
    bw_zero(&net,sizeof(net));bw_copy(&net.eth,eth,sizeof(*eth));net.platform=p;
    p->listen=open_listener;p->accept=accept_stream;p->recv=read_stream;p->send=write_stream;
    p->close=close_stream;p->poll=pump;p->milliseconds=milliseconds;return 0;
}
void bw_lwip_shutdown(void) {
    unsigned i;
    for(i=0;i<STREAMS;i++)if(net.streams[i].state)close_stream(0,(int)i+1);
    if(net.listener) {tcp_accept(net.listener,0);tcp_close(net.listener);net.listener=0;}
    /* Normal close may leave FIN/TIME_WAIT PCBs pointing into this RAM image.
     * This is a private single-instance lwIP, not the vendor's old IP stack. */
    while(tcp_active_pcbs)tcp_abort(tcp_active_pcbs);
    while(tcp_tw_pcbs)tcp_abort(tcp_tw_pcbs);
    if(net.iface_added) {
        netif_set_link_down(&net.iface);netif_set_down(&net.iface);netif_remove(&net.iface);
    }
    bw_zero(&net,sizeof(net));
}
