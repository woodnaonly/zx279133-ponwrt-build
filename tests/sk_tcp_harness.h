/* SPDX-License-Identifier: GPL-2.0-only */
/* Freestanding host harness, not a replacement for in-kernel/runtime tests.
 * The two driver functions and enum are inserted verbatim by the Python test.
 * The skb model implements the documented single-inline-tag pop contract.
 */
typedef __SIZE_TYPE__ size_t;
typedef unsigned char u8;
typedef unsigned short u16;
typedef unsigned int u32;
typedef _Bool bool;
#define true 1
#define false 0
#define NULL ((void *)0)
#define EINVAL 22
#define ENOMEM 12
#define ETH_ALEN 6
#define ETH_HLEN 14
#define ETH_ZLEN 60
#define VLAN_ETH_HLEN 18
#define VLAN_HLEN 4
#define VLAN_VID_MASK 0xfff
#define ETH_P_IP 0x800
#define ETH_P_IPV6 0x86dd
#define ETH_P_8021Q 0x8100
#define IPPROTO_TCP 6
#define CHECKSUM_PARTIAL 3
#define ZX279133_LAN_TRANSPORT_VID_MIN 59
#define ZX279133_LAN_TRANSPORT_VID_MAX 63
#define ZX279133_9132_UNI_PPU_FIRST 1
#define zx279133_tx_hw_csum 1
#define offsetof(t, m) __builtin_offsetof(t, m)

static u16 htons(u16 n) { return (u16)((n << 8) | (n >> 8)); }
#define ntohs htons
static void *memcpy(void *dst, const void *src, size_t n)
{
    u8 *d = dst;
    const u8 *s = src;
    for (size_t i = 0; i < n; i++) d[i] = s[i];
    return dst;
}
static void *memmove(void *dst, const void *src, size_t n)
{
    u8 *d = dst;
    const u8 *s = src;
    if (d > s) while (n--) d[n] = s[n];
    else for (size_t i = 0; i < n; i++) d[i] = s[i];
    return dst;
}
static void zero(void *dst, size_t n)
{
    u8 *d = dst;
    for (size_t i = 0; i < n; i++) d[i] = 0;
}
static bool same(const void *a, const void *b, size_t n)
{
    const u8 *x = a, *y = b;
    for (size_t i = 0; i < n; i++) if (x[i] != y[i]) return false;
    return true;
}
struct ethhdr { u8 dest[6], src[6]; u16 h_proto; };
struct vlan_ethhdr { u8 dest[6], src[6]; u16 h_vlan_proto, h_vlan_TCI, h_vlan_encapsulated_proto; };
struct iphdr {
    u8 ihl:4, version:4;
    u8 tos;
    u16 tot_len, id, frag_off;
    u8 ttl, protocol;
    u16 check;
    u32 saddr, daddr;
};
struct tcphdr { u8 before_check[16]; u16 check; u8 rest[2]; };
struct sk_buff {
    u8 buffer[2048], copy[2048];
    u8 *head, *data;
    unsigned int len, head_len, data_len, network_header, transport_header, csum_start, mac_header;
    u16 protocol, csum_offset, vlan_tci;
    int ip_summed;
    bool encapsulation, sctp, pull_fail, write_fail, cloned;
};
static unsigned int skb_network_offset(struct sk_buff *s) { return s->head + s->network_header - s->data; }
static unsigned int skb_transport_offset(struct sk_buff *s) { return s->head + s->transport_header - s->data; }
static unsigned int skb_checksum_start_offset(struct sk_buff *s) { return s->head + s->csum_start - s->data; }
static bool skb_csum_is_sctp(struct sk_buff *s) { return s->sctp; }
static bool pskb_may_pull(struct sk_buff *s, unsigned int n)
{
    if (s->pull_fail || n > s->len) return false;
    if (n > s->head_len) { s->head_len = n; s->data_len = s->len - n; }
    return true;
}
static struct iphdr *ip_hdr(struct sk_buff *s) { return (struct iphdr *)(s->head + s->network_header); }
static bool ip_is_fragment(const struct iphdr *ip) { return (ntohs(ip->frag_off) & 0x3fff) != 0; }
static void skb_reset_mac_header(struct sk_buff *s) { s->mac_header = s->data - s->head; }
static int __skb_vlan_pop(struct sk_buff *s, u16 *tci)
{
    if (s->write_fail) return -ENOMEM;
    if (s->cloned) {
        unsigned int offset = s->data - s->head;
        memcpy(s->copy, s->head, sizeof(s->copy));
        s->head = s->copy;
        s->data = s->head + offset;
        s->cloned = false;
    }
    struct vlan_ethhdr *veth = (struct vlan_ethhdr *)s->data;
    *tci = ntohs(veth->h_vlan_TCI);
    u16 proto = veth->h_vlan_encapsulated_proto;
    memmove(s->data + VLAN_HLEN, s->data, 2 * ETH_ALEN);
    s->data += VLAN_HLEN;
    s->len -= VLAN_HLEN;
    s->head_len -= VLAN_HLEN;
    s->protocol = proto;
    s->mac_header += VLAN_HLEN;
    if (skb_network_offset(s) < ETH_HLEN) s->network_header = (s->data - s->head) + ETH_HLEN;
    return 0;
}

/* DRIVER_FUNCTIONS */

static int failed;
#define CHECK(x) do { if (!(x) && !failed) failed = __LINE__; } while (0)
static struct sk_buff packet;
static void init(unsigned int ihl, unsigned int vid, bool customer)
{
    struct sk_buff *s = &packet;
    zero(s, sizeof(*s));
    s->head = s->buffer;
    s->data = s->head + 64;
    s->len = 114 + 4 + (customer ? 4 : 0);
    s->head_len = s->len;
    s->ip_summed = CHECKSUM_PARTIAL;
    s->csum_offset = 16;
    s->protocol = htons(ETH_P_IP); /* DSA's tagger retains this value. */
    struct vlan_ethhdr *v = (struct vlan_ethhdr *)s->data;
    for (unsigned int i = 0; i < 12; i++) s->data[i] = i + 1;
    v->h_vlan_proto = htons(ETH_P_8021Q);
    v->h_vlan_TCI = htons(vid);
    v->h_vlan_encapsulated_proto = htons(customer ? ETH_P_8021Q : ETH_P_IP);
    if (customer) {
        s->data[18] = 0; s->data[19] = 100;
        s->data[20] = 8; s->data[21] = 0;
    }
    s->network_header = 64 + 18 + (customer ? 4 : 0);
    s->transport_header = s->network_header + ihl * 4;
    s->csum_start = s->transport_header;
    struct iphdr *ip = ip_hdr(s);
    ip->version = 4; ip->ihl = ihl;
    ip->tot_len = htons(100);
    ip->protocol = IPPROTO_TCP;
    for (unsigned int i = ihl * 4; i < 100; i++) ((u8 *)ip)[i] = i;
}
int main(void)
{
    struct sk_buff *s = &packet;
    u8 port, payload[100], mac[12];
    for (unsigned int ihl = 5; ihl <= 11; ihl++) {
        for (unsigned int vid = 59; vid <= 63; vid++) {
            init(ihl, vid, false);
            memcpy(payload, ip_hdr(s), 100); memcpy(mac, s->data, 12);
            unsigned int start = s->csum_start, network = s->network_header, transport = s->transport_header;
            CHECK(zx279133_lan_strip_transport_vlan(s, &port) == 1);
            CHECK(port == vid - 58);
            CHECK(s->len == 114 && s->data == s->head + 68);
            CHECK(same(mac, s->data, 12) && same(payload, ip_hdr(s), 100));
            CHECK(s->network_header == network && s->transport_header == transport && s->csum_start == start);
            CHECK(skb_checksum_start_offset(s) == skb_transport_offset(s));
            CHECK(s->ip_summed == CHECKSUM_PARTIAL);
            CHECK(zx279133_tx_csum_reason(s) == ZX279133_TX_CSUM_OK);
        }
    }
    init(5, 59, true);
    s->vlan_tci = 456; /* Existing customer metadata is not consumed. */
    CHECK(zx279133_lan_strip_transport_vlan(s, &port) == 1);
    CHECK(s->protocol == htons(ETH_P_8021Q) && s->vlan_tci == 456);
    CHECK(s->data[14] == 0 && s->data[15] == 100 && s->data[16] == 8);
    CHECK(zx279133_tx_csum_reason(s) == ZX279133_TX_CSUM_PROTOCOL);
    init(5, 100, false);
    u8 *original = s->data;
    CHECK(zx279133_lan_strip_transport_vlan(s, &port) == 0 && port == 0 && s->data == original);
    init(5, 59, false); s->cloned = true; s->head_len = 18; s->data_len = s->len - 18;
    CHECK(zx279133_lan_strip_transport_vlan(s, &port) == 1);
    CHECK(s->data_len > 0 && s->head == s->copy);
    CHECK(zx279133_tx_csum_reason(s) == ZX279133_TX_CSUM_OK);
    init(5, 59, false); s->write_fail = true;
    CHECK(zx279133_lan_strip_transport_vlan(s, &port) == -ENOMEM && port == 0);
    init(5, 59, false); s->len = 16;
    CHECK(zx279133_lan_strip_transport_vlan(s, &port) == -EINVAL);
    init(5, 59, false); s->pull_fail = true;
    CHECK(zx279133_lan_strip_transport_vlan(s, &port) == -EINVAL);
    init(5, 59, false); CHECK(zx279133_lan_strip_transport_vlan(s, &port) == 1);
    s->len = 54; CHECK(zx279133_tx_csum_reason(s) == ZX279133_TX_CSUM_SHORT);
    s->len = 60; CHECK(zx279133_tx_csum_reason(s) == ZX279133_TX_CSUM_LENGTH);
    s->len = 114; s->protocol = htons(ETH_P_IPV6);
    CHECK(zx279133_tx_csum_reason(s) == ZX279133_TX_CSUM_PROTOCOL);
    s->protocol = htons(ETH_P_IP); ip_hdr(s)->protocol = 17;
    CHECK(zx279133_tx_csum_reason(s) == ZX279133_TX_CSUM_NOT_TCP);
    ip_hdr(s)->protocol = IPPROTO_TCP; ip_hdr(s)->frag_off = htons(0x2000);
    CHECK(zx279133_tx_csum_reason(s) == ZX279133_TX_CSUM_FRAGMENT);
    ip_hdr(s)->frag_off = htons(1);
    CHECK(zx279133_tx_csum_reason(s) == ZX279133_TX_CSUM_FRAGMENT);
    ip_hdr(s)->frag_off = 0; s->csum_start -= 4;
    CHECK(zx279133_tx_csum_reason(s) == ZX279133_TX_CSUM_START);
    s->csum_start += 4; s->encapsulation = true;
    CHECK(zx279133_tx_csum_reason(s) == ZX279133_TX_CSUM_ENCAP);
    s->encapsulation = false; s->csum_offset = 6;
    CHECK(zx279133_tx_csum_reason(s) == ZX279133_TX_CSUM_OFFSET);
    s->csum_offset = 16; s->sctp = true;
    CHECK(zx279133_tx_csum_reason(s) == ZX279133_TX_CSUM_SCTP);
    s->sctp = false; s->transport_header = 64 + 68;
    CHECK(zx279133_tx_csum_reason(s) == ZX279133_TX_CSUM_RANGE);
    return failed;
}
