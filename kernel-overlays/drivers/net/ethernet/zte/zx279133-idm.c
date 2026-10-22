// SPDX-License-Identifier: GPL-2.0-only

#include <linux/dma-mapping.h>
#include <linux/bpf_trace.h>
#include <linux/dsa/8021q.h>
#include <linux/etherdevice.h>
#include <linux/filter.h>
#include <linux/if_arp.h>
#include <linux/if_vlan.h>
#include <linux/io.h>
#include <linux/ip.h>
#include <linux/ipv6.h>
#include <linux/interrupt.h>
#include <linux/prefetch.h>
#include <linux/slab.h>
#include <linux/tcp.h>
#include <linux/udp.h>
#include <linux/unaligned.h>

#include <net/ip.h>
#include <net/page_pool/helpers.h>
#include <net/xdp.h>
#include <net/xdp_sock_drv.h>

#include "zx279133.h"

void zx279133_idm_set_masked(struct zx279133_eth *eth, u32 mask, bool masked)
{
	void __iomem *idm = eth->base + ZX279133_IDM_BASE;
	u32 value = readl(idm + ZX279133_IDM_INT_MASK);

	if (masked)
		value |= mask;
	else
		value &= ~mask;
	writel(value, idm + ZX279133_IDM_INT_MASK);
}

static u32 zx279133_idm_bp_ring_cfg(u32 bp_depth, u32 free_depth)
{
	/*
	 * idm_rx_bp_init() writes ((bp_depth >> 12) - 1) << 21 into the pool
	 * size register and idm_rev_bp_init() adds ((free_depth >> 12) - 1)
	 * << 10, with bit 8 set in both.  The depths are entry counts, and
	 * the hardware reads them back out of the register to find the ring,
	 * so a ring that is not this size is a ring the hardware cannot use.
	 */
	return (((bp_depth >> 12) - 1) << 21) |
	       (((free_depth >> 12) - 1) << 10) | BIT(8);
}

static u32 zx279133_idm_rx_page_key(dma_addr_t dma)
{
	return lower_32_bits(dma);
}

static unsigned int zx279133_idm_rx_page_slot(u32 key)
{
	return hash_32(key, ZX279133_IDM_RX_PAGE_MAP_BITS);
}

static void zx279133_idm_rx_prefetch_payload(struct page *page)
{
	u8 *data = page_address(page) + ZX279133_IDM_RX_PAYLOAD_OFFSET;

	prefetch(data);
	prefetch(data + L1_CACHE_BYTES);
	prefetch(data + 2 * L1_CACHE_BYTES);
}

static struct page_pool *zx279133_rx_page_pool(struct page *page)
{
	return netmem_get_pp(page_to_netmem(page));
}

static void zx279133_rx_recycle_page(struct page *page)
{
	page_pool_put_full_page(zx279133_rx_page_pool(page), page, false);
}

static int zx279133_idm_rx_track_page(struct zx279133_eth *eth,
				      struct page *page)
{
	dma_addr_t dma = page_pool_get_dma_addr(page) +
			 ZX279133_IDM_RX_PAYLOAD_OFFSET;
	u32 key;
	unsigned int slot;
	unsigned int i;

	if (upper_32_bits(dma))
		return -ERANGE;
	if (WARN_ON(eth->rx_page_map_count >= ZX279133_IDM_RX_BUFFER_COUNT))
		return -ENOSPC;

	key = zx279133_idm_rx_page_key(dma);
	slot = zx279133_idm_rx_page_slot(key);
	for (i = 0; i < ZX279133_IDM_RX_PAGE_MAP_SIZE; i++) {
		struct zx279133_rx_page_entry *entry = &eth->rx_page_map[slot];

		if (!entry->page) {
			entry->key = key;
			entry->page = page;
			entry->xsk = false;
			eth->rx_page_map_count++;
			if (eth->rx_page_map_count > eth->rx_page_map_high_water)
				eth->rx_page_map_high_water = eth->rx_page_map_count;
			return 0;
		}
		if (WARN_ON(entry->key == key))
			return -EBUSY;
		slot = (slot + 1) & (ZX279133_IDM_RX_PAGE_MAP_SIZE - 1);
	}

	return -ENOSPC;
}

static void zx279133_idm_rx_remove_page(struct zx279133_eth *eth,
					unsigned int hole)
{
	const unsigned int mask = ZX279133_IDM_RX_PAGE_MAP_SIZE - 1;
	unsigned int scan = (hole + 1) & mask;

	while (eth->rx_page_map[scan].page) {
		u32 key = eth->rx_page_map[scan].key;
		unsigned int ideal = zx279133_idm_rx_page_slot(key);

		if (((scan - ideal) & mask) >= ((scan - hole) & mask)) {
			eth->rx_page_map[hole] = eth->rx_page_map[scan];
			hole = scan;
		}
		scan = (scan + 1) & mask;
	}
	eth->rx_page_map[hole].page = NULL;
	eth->rx_page_map[hole].key = 0;
	eth->rx_page_map[hole].xsk = false;
	eth->rx_page_map_count--;
}

static int zx279133_idm_rx_track_xsk(struct zx279133_eth *eth,
				     struct xdp_buff *xdp)
{
	dma_addr_t dma = xsk_buff_xdp_get_dma(xdp);
	u32 key;
	unsigned int slot;
	unsigned int i;

	if (upper_32_bits(dma))
		return -ERANGE;
	if (WARN_ON(eth->rx_page_map_count >= ZX279133_IDM_RX_BUFFER_COUNT))
		return -ENOSPC;

	key = zx279133_idm_rx_page_key(dma);
	slot = zx279133_idm_rx_page_slot(key);
	for (i = 0; i < ZX279133_IDM_RX_PAGE_MAP_SIZE; i++) {
		struct zx279133_rx_page_entry *entry = &eth->rx_page_map[slot];

		if (!entry->page) {
			entry->key = key;
			entry->xdp = xdp;
			entry->xsk = true;
			eth->rx_page_map_count++;
			if (eth->rx_page_map_count > eth->rx_page_map_high_water)
				eth->rx_page_map_high_water = eth->rx_page_map_count;
			return 0;
		}
		if (WARN_ON(entry->key == key))
			return -EBUSY;
		slot = (slot + 1) & (ZX279133_IDM_RX_PAGE_MAP_SIZE - 1);
	}

	return -ENOSPC;
}

static int __zx279133_idm_rx_post_page(struct zx279133_eth *eth,
				       struct page *page)
{
	dma_addr_t dma = page_pool_get_dma_addr(page) +
			 ZX279133_IDM_RX_PAYLOAD_OFFSET;
	int ret;

	ret = zx279133_idm_rx_track_page(eth, page);
	if (ret)
		return ret;
	WRITE_ONCE(eth->rx_normal_bp[eth->rx_bp_prod],
		   cpu_to_be32(lower_32_bits(dma)));
	eth->rx_bp_prod = (eth->rx_bp_prod + 1) &
			  (ZX279133_IDM_BP_RING_SIZE - 1);
	return 0;
}

static int __zx279133_idm_rx_post_xsk(struct zx279133_eth *eth,
				      struct xdp_buff *xdp)
{
	dma_addr_t dma = xsk_buff_xdp_get_dma(xdp);
	int ret;

	ret = zx279133_idm_rx_track_xsk(eth, xdp);
	if (ret)
		return ret;
	WRITE_ONCE(eth->rx_normal_bp[eth->rx_bp_prod],
		   cpu_to_be32(lower_32_bits(dma)));
	eth->rx_bp_prod = (eth->rx_bp_prod + 1) &
			  (ZX279133_IDM_BP_RING_SIZE - 1);
	return 0;
}

static int zx279133_idm_rx_post_page(struct zx279133_eth *eth,
				     struct page *page)
{
	int ret;

	spin_lock_bh(&eth->rx_buffer_lock);
	ret = __zx279133_idm_rx_post_page(eth, page);
	if (!ret)
		eth->rx_refill_pending++;
	spin_unlock_bh(&eth->rx_buffer_lock);
	return ret;
}

static int zx279133_idm_rx_post_xsk(struct zx279133_eth *eth,
				    struct xdp_buff *xdp)
{
	int ret;

	spin_lock_bh(&eth->rx_buffer_lock);
	ret = __zx279133_idm_rx_post_xsk(eth, xdp);
	if (!ret)
		eth->rx_refill_pending++;
	spin_unlock_bh(&eth->rx_buffer_lock);
	return ret;
}

static unsigned int zx279133_idm_rx_flush_refill(struct zx279133_eth *eth)
{
	unsigned int count;

	spin_lock_bh(&eth->rx_buffer_lock);
	count = eth->rx_refill_pending;
	if (count) {
		void __iomem *idm = eth->base + ZX279133_IDM_BASE;
		u32 i;

		dma_wmb();
		/*
		 * The vendor's idm_new_bp_update() does not write the count
		 * into this register.  It writes one 0x400 pulse per 1024
		 * buffers, then the two remainders - the normal pool in bits
		 * 15:0 and the jumbo pool in bits 31:16.  The hardware reads
		 * the count out of those fields, so writing the count itself
		 * announces nothing at all, and a count that is a multiple of
		 * 1024 announces zero buffers.  Without this the hardware
		 * never learns that there is a pool to receive into.
		 */
		for (i = 0; i < (count >> 10); i++)
			writel(0x400, idm + ZX279133_IDM_BP_REFILL);
		writel(count & 0x3ff, idm + ZX279133_IDM_BP_REFILL);
		eth->rx_refill_pending = 0;
	}
	spin_unlock_bh(&eth->rx_buffer_lock);
	return count;
}

static void
zx279133_idm_rx_sync_xsk_for_device(struct zx279133_eth *eth, struct xdp_buff *xdp)
{
	struct xsk_buff_pool *pool = eth->xsk_pool;
	dma_addr_t dma = xsk_buff_xdp_get_dma(xdp);
	u32 size = xsk_pool_get_rx_frame_size(pool);

	xsk_buff_raw_dma_sync_for_device(pool, dma, size);
}

static bool
__zx279133_idm_rx_take_buffer(struct zx279133_eth *eth, dma_addr_t dma,
			      struct zx279133_rx_page_entry *buffer)
{
	u32 key;
	unsigned int slot;
	unsigned int i;

	key = zx279133_idm_rx_page_key(dma);
	slot = zx279133_idm_rx_page_slot(key);
	for (i = 0; i < ZX279133_IDM_RX_PAGE_MAP_SIZE; i++) {
		struct zx279133_rx_page_entry *entry = &eth->rx_page_map[slot];

		if (!entry->page)
			return false;
		if (entry->key == key) {
			*buffer = *entry;
			zx279133_idm_rx_remove_page(eth, slot);
			if (buffer->xsk) {
				if (dma == xsk_buff_xdp_get_dma(buffer->xdp))
					return true;
				xsk_buff_free(buffer->xdp);
			} else if (dma == page_pool_get_dma_addr(buffer->page) +
					  ZX279133_IDM_RX_PAYLOAD_OFFSET) {
				return true;
			} else {
				zx279133_rx_recycle_page(buffer->page);
			}
			/* The buffer is released; the caller must not use it. */
			WARN_ON_ONCE(1);
			memset(buffer, 0, sizeof(*buffer));
			return false;
		}
		slot = (slot + 1) & (ZX279133_IDM_RX_PAGE_MAP_SIZE - 1);
	}

	return false;
}

static bool zx279133_idm_rx_take_buffer(struct zx279133_eth *eth,
					dma_addr_t dma,
					struct zx279133_rx_page_entry *buffer)
{
	bool found;

	spin_lock_bh(&eth->rx_buffer_lock);
	found = __zx279133_idm_rx_take_buffer(eth, dma, buffer);
	spin_unlock_bh(&eth->rx_buffer_lock);
	return found;
}

unsigned int zx279133_idm_tx_reclaim_locked(struct zx279133_eth *eth);

static void zx279133_idm_rx_put_vlan(struct sk_buff *skb, u16 vid)
{
	__vlan_hwaccel_put_tag(skb, htons(ETH_P_8021Q), vid);
}

/*
 * The source port the PPU reports in descriptor word 1.
 *
 * The PPU reports the logical port the frame came in on, and which number
 * that is depends on the switch on the other end of the lane:
 *
 *   - with the tree's external RTL8372N it is the transport VID the tagger
 *     put on the frame (59..62), and the entry program (PC 10) has removed
 *     the tag itself;
 *   - with the 9132 - a transparent MAC-learning bridge with no VLANs at
 *     all, see docs/16 §2.2 - it is the chip's own port number, 1..4 for
 *     the four UNI jacks.
 *
 * Both mean the same thing, so both are understood here, and both are turned
 * into the transport VID the DSA tagger dispatches on.
 */
static u16 zx279133_idm_rx_lan_vport(u32 word1)
{
	u16 port = (word1 >> 16) & 0x3f;

	if (port >= ZX279133_LAN_TRANSPORT_VID_MIN &&
	    port <= ZX279133_LAN_TRANSPORT_VID_MAX)
		return port;
	if (port >= ZX279133_9132_UNI_PPU_FIRST &&
	    port <= ZX279133_9132_UNI_PPU_LAST)
		return ZX279133_LAN_TRANSPORT_VID_MIN +
		       (port - ZX279133_9132_UNI_PPU_FIRST);

	return 0;
}

/* Whether word 1 names the port of a frame the PPU parsed for this side. */
static bool zx279133_idm_rx_parsed(u32 word1, bool lan_source)
{
	u16 port = (word1 >> 16) & 0x3f;

	if (lan_source)
		return port == ZX279133_LAN_TX_PORT ||
		       zx279133_idm_rx_lan_vport(word1);
	return port == zx279133_tx_port;
}

/*
 * Whether the frame still carries an Ethernet type field.
 *
 * The 9132 is a transparent MAC-learning bridge (docs/16 2.2): it does
 * not take the Ethernet header off a frame, and the frame it hands to
 * the CPU has the same header the client sent.  The rewrite below is
 * for the other shape - a frame whose type field the PPU took off - and
 * applied to a frame that has one it shifts the payload four bytes to
 * the right and writes the type and the IP version word twice, so the
 * IP header the stack reads starts four bytes too far in and every
 * IPv4 and IPv6 frame is dropped for a total length of 0x0800.
 *
 * The rest of this file already assumes the frame has its header: the
 * checksum path reads the IP header at ETH_HLEN and the DSA path reads
 * the frame as a vlan_ethhdr.  So the rewrite may only run on a frame
 * that has no type field of its own.
 */
static bool zx279133_idm_rx_has_ethertype(const struct sk_buff *skb)
{
	if (skb->len < ETH_HLEN)
		return false;

	return get_unaligned_be16(skb->data + 2 * ETH_ALEN) >= ETH_P_802_3_MIN;
}

static u16 zx279133_idm_rx_restore_lan_l2(struct sk_buff *skb, u32 metadata0)
{
	u16 l3_prefix = metadata0 >> 16;
	u16 protocol;

	if ((l3_prefix & 0xf000) == 0x4000) {
		protocol = ETH_P_IP;
	} else if ((l3_prefix & 0xf000) == 0x6000) {
		protocol = ETH_P_IPV6;
	} else if (l3_prefix == ARPHRD_ETHER &&
		   get_unaligned_be16(skb->data + 2 * ETH_ALEN) == ETH_P_IP &&
		   skb->data[2 * ETH_ALEN + 2] == ETH_ALEN &&
		   skb->data[2 * ETH_ALEN + 3] == 4) {
		protocol = ETH_P_ARP;
	} else {
		return 0;
	}

	skb_put(skb, VLAN_HLEN);
	memmove(skb->data + 2 * ETH_ALEN + VLAN_HLEN,
		skb->data + 2 * ETH_ALEN,
		skb->len - 2 * ETH_ALEN - VLAN_HLEN);
	put_unaligned_be16(protocol, skb->data + 2 * ETH_ALEN);
	put_unaligned_be16(l3_prefix,
			   skb->data + 2 * ETH_ALEN + sizeof(protocol));

	return VLAN_HLEN;
}

static bool zx279133_idm_rx_preserve_dsa_vlan(struct vlan_ethhdr *vhdr)
{
	u16 transport_vid = ZX279133_LAN_VID;
	u16 ingress_vid;
	u16 tci;

	if (!eth_type_vlan(vhdr->h_vlan_proto))
		return false;

	tci = ntohs(vhdr->h_vlan_TCI);
	ingress_vid = tci & VLAN_VID_MASK;
	if (vid_is_dsa_8021q(ingress_vid))
		return true;

	/* Preserve customer VLANs. Normalize only the old private VID format. */
	if (ingress_vid != ZX279133_LAN_INGRESS_VID &&
	    (ingress_vid < ZX279133_LAN_TRANSPORT_VID_MIN ||
	     ingress_vid > ZX279133_LAN_TRANSPORT_VID_MAX))
		return true;

	if (ingress_vid >= ZX279133_LAN_TRANSPORT_VID_MIN)
		transport_vid = ingress_vid;
	tci = (tci & ~VLAN_VID_MASK) | transport_vid;
	vhdr->h_vlan_TCI = htons(tci);

	return true;
}

static void zx279133_idm_rx_sync_for_device(struct zx279133_eth *eth,
					    struct page *page, u16 len)
{
	dma_addr_t dma = page_pool_get_dma_addr(page);

	dma_sync_single_range_for_device(eth->dev, dma,
					 ZX279133_IDM_RX_PAYLOAD_OFFSET, len,
					 DMA_BIDIRECTIONAL);
}

/* XMAC IPC rejects checksum errors before IDM. Its protocol metadata alone
 * does not report checksum validity. Trust only the tested unfragmented
 * TCP/UDP formats; tunnels, extension headers and XDP-modified packets keep
 * software validation. The MAC error filter remains enabled when RXCSUM is
 * off, which disables only the stack's use of this validation result.
 */
static bool zx279133_idm_rx_l4_valid(struct sk_buff *skb)
{
	__be16 proto = ((struct ethhdr *)skb->data)->h_proto;
	unsigned int offset = ETH_HLEN;
	unsigned int payload_len;
	u8 ip_proto;

	if (eth_type_vlan(proto)) {
		const struct vlan_ethhdr *vhdr = (void *)skb->data;

		if (skb->len < sizeof(*vhdr))
			return false;
		proto = vhdr->h_vlan_encapsulated_proto;
		offset += VLAN_HLEN;
	}
	if (proto == htons(ETH_P_IP)) {
		const struct iphdr *iph = (void *)(skb->data + offset);

		if (skb->len < offset + sizeof(*iph) || iph->version != 4 ||
		    iph->ihl != 5 || (iph->frag_off & htons(IP_MF | IP_OFFSET)) ||
		    ntohs(iph->tot_len) < sizeof(*iph) ||
		    ntohs(iph->tot_len) > skb->len - offset)
			return false;
		payload_len = ntohs(iph->tot_len) - sizeof(*iph);
		ip_proto = iph->protocol;
		offset += sizeof(*iph);
	} else if (proto == htons(ETH_P_IPV6)) {
		const struct ipv6hdr *ip6h = (void *)(skb->data + offset);

		if (skb->len < offset + sizeof(*ip6h) || ip6h->version != 6)
			return false;
		payload_len = ntohs(ip6h->payload_len);
		ip_proto = ip6h->nexthdr;
		offset += sizeof(*ip6h);
		if (payload_len > skb->len - offset)
			return false;
	} else {
		return false;
	}
	if (ip_proto == IPPROTO_TCP) {
		const struct tcphdr *th = (void *)(skb->data + offset);

		return payload_len >= sizeof(*th) && th->doff >= 5 &&
		       th->doff * 4 <= payload_len;
	}
	if (ip_proto == IPPROTO_UDP) {
		const struct udphdr *uh = (void *)(skb->data + offset);

		return payload_len >= sizeof(*uh) && uh->check &&
		       ntohs(uh->len) == payload_len;
	}
	return false;
}

static int zx279133_idm_rx_process_queue(struct zx279133_eth *eth,
					 struct napi_struct *napi,
					 unsigned int queue, u16 count,
					 bool *xdp_tx, bool *xdp_redirect)
{
	bool secondary = napi == &eth->lan_napi;
	struct zx279133_rx_stats *stats = &eth->rx_stats[secondary];
	struct page_pool *pool = secondary ? eth->lan_rx_page_pool : eth->rx_page_pool;
	struct net_device *ndev = eth->ndev;
	void __iomem *idm = eth->base + ZX279133_IDM_BASE;
	u16 refill = 0;
	u16 rx_work = 0;

	while (rx_work < count) {
		struct zx279133_idm_desc *desc;
		struct zx279133_rx_page_entry buffer = {};
		struct page *replacement = NULL;
		struct page *page = NULL;
		struct sk_buff *skb = NULL;
		void *data;
		dma_addr_t dma;
		u32 word1;
		u16 desc_index = eth->rx_cons[queue];
		u16 refill_before = refill;
		u16 len;
		bool lan_source;
		bool valid_dma;
		bool xdp_ran = false;

		desc = eth->rx_descs + queue * ZX279133_IDM_RX_RING_SIZE +
			desc_index;
		dma = le32_to_cpu(READ_ONCE(desc->address));
		if (!dma) {
			u64_stats_inc(&stats->rx_desc_not_ready);
			break;
		}
		eth->rx_cons[queue] = (eth->rx_cons[queue] + 1) &
					      (ZX279133_IDM_RX_RING_SIZE - 1);
		dma_rmb();
		valid_dma = eth->xsk_pool ||
			    (dma >= ZX279133_IDM_RX_PAYLOAD_OFFSET &&
			     IS_ALIGNED(dma - ZX279133_IDM_RX_PAYLOAD_OFFSET,
					PAGE_SIZE));
		if (likely(valid_dma)) {
			u32 key = zx279133_idm_rx_page_key(dma);

			prefetch(&eth->rx_page_map[zx279133_idm_rx_page_slot(key)]);
		}
		word1 = le32_to_cpu(READ_ONCE(desc->length_flags));
		len = word1 & GENMASK(13, 0);
		if (unlikely(!valid_dma)) {
			u64_stats_inc(&stats->rx_invalid_dma);
		} else if (!zx279133_idm_rx_take_buffer(eth, dma, &buffer)) {
			u64_stats_inc(&stats->rx_page_lookup_misses);
		}

		if (!buffer.page) {
			zx279133_stats_rx_error(eth, ndev);
			if (net_ratelimit())
				netdev_warn(ndev,
					    "RX queue %u descriptor %u has unknown DMA %pad (word1 %#x, tracked %u)\n",
					    queue, desc_index, &dma, word1,
					    eth->rx_page_map_count);
			goto release_desc;
		}
		if (!buffer.xsk) {
			page = buffer.page;
			if (zx279133_rx_page_pool(page) != pool)
				u64_stats_inc(&stats->foreign_pages);
		}

		if (word1 & BIT(14)) {
			u64_stats_inc(&stats->rx_jumbo_drops);
			zx279133_stats_rx_dropped(eth, ndev);
			zx279133_stats_rx_error(eth, ndev);
			goto reuse_buffer;
		}
		if (word1 & BIT(15)) {
			u64_stats_inc(&stats->rx_descriptor_flag_drops);
			zx279133_stats_rx_dropped(eth, ndev);
			goto reuse_buffer;
		}
		if (len < ETH_HLEN || len > ZX279133_IDM_RX_FRAME_LIMIT) {
			zx279133_stats_rx_error(eth, ndev);
			zx279133_stats_rx_length_error(eth, ndev);
			goto reuse_buffer;
		}
		/*
		 * A LAN port switched to WAN-type hands its frames for the CPU
		 * to the WAN queues, whole, with its transport VID in word 1.
		 */
		lan_source = queue < ZX279133_LAN_RX_QUEUE_COUNT ||
			     zx279133_idm_rx_lan_vport(word1);

		if (buffer.xsk) {
			struct xdp_buff *replacement_xsk;
			struct xdp_buff *xdp = buffer.xdp;
			struct bpf_prog *xdp_prog = NULL;
			u32 act = XDP_PASS;

			replacement_xsk = xsk_buff_alloc(eth->xsk_pool);
			if (replacement_xsk) {
				if (zx279133_idm_rx_post_xsk(eth, replacement_xsk)) {
					u64_stats_inc(&stats->rx_refill_post_failures);
					xsk_buff_free(replacement_xsk);
					replacement_xsk = NULL;
				} else {
					refill++;
				}
			} else {
				u64_stats_inc(&stats->rx_page_alloc_failures);
			}

			xsk_buff_set_size(xdp, len);
			xsk_buff_dma_sync_for_cpu(xdp);
			data = xdp->data;
			if (!lan_source)
				xdp_prog = rcu_dereference_bh(eth->xdp_prog);
			if (xdp_prog) {
				xdp_ran = true;
				act = bpf_prog_run_xdp(xdp_prog, xdp);
			}

			switch (act) {
			case XDP_PASS:
				if (xdp_prog)
					u64_stats_inc(&stats->xdp_pass);
				data = xdp->data;
				len = xdp->data_end - xdp->data;
				break;
			case XDP_TX:
				if (!zx279133_xsk_rx_enqueue(eth, xdp)) {
					u64_stats_inc(&stats->xdp_tx);
					*xdp_tx = true;
					goto release_desc;
				}
				u64_stats_inc(&stats->xdp_aborted);
				trace_xdp_exception(ndev, xdp_prog, act);
				goto xsk_recycle;
			case XDP_REDIRECT:
				if (!xdp_do_redirect(ndev, xdp, xdp_prog)) {
					u64_stats_inc(&stats->xdp_redirect);
					*xdp_redirect = true;
					goto release_desc;
				}
				u64_stats_inc(&stats->xdp_aborted);
				trace_xdp_exception(ndev, xdp_prog, act);
				goto xsk_recycle;
			case XDP_ABORTED:
				u64_stats_inc(&stats->xdp_aborted);
				trace_xdp_exception(ndev, xdp_prog, act);
				goto xsk_recycle;
			case XDP_DROP:
				u64_stats_inc(&stats->xdp_drop);
				goto xsk_recycle;
			default:
				u64_stats_inc(&stats->xdp_aborted);
				bpf_warn_invalid_xdp_action(ndev, xdp_prog, act);
				trace_xdp_exception(ndev, xdp_prog, act);
				goto xsk_recycle;
			}

			skb = napi_alloc_skb(napi, len + VLAN_HLEN + NET_IP_ALIGN);
			if (skb) {
				skb_reserve(skb, NET_IP_ALIGN);
				memcpy(skb_put(skb, len), data, len);
			} else {
				u64_stats_inc(&stats->rx_skb_alloc_failures);
				zx279133_stats_rx_dropped(eth, ndev);
			}

xsk_recycle:
			if (replacement_xsk) {
				xsk_buff_free(xdp);
			} else {
				zx279133_idm_rx_sync_xsk_for_device(eth, xdp);
				if (zx279133_idm_rx_post_xsk(eth, xdp)) {
					u64_stats_inc(&stats->rx_refill_post_failures);
					xsk_buff_free(xdp);
					zx279133_stats_rx_error(eth, ndev);
				} else {
					refill++;
				}
			}
			goto deliver_skb;
		}

		replacement = page_pool_dev_alloc_pages(pool);
		if (!replacement) {
			u64_stats_inc(&stats->rx_page_alloc_failures);
		} else if (zx279133_idm_rx_post_page(eth, replacement)) {
			u64_stats_inc(&stats->rx_refill_post_failures);
			zx279133_rx_recycle_page(replacement);
			replacement = NULL;
		}
		if (replacement) {
			refill++;
		}
		page_pool_dma_sync_for_cpu(zx279133_rx_page_pool(page), page, 0, len);
		zx279133_idm_rx_prefetch_payload(page);
		data = page_address(page) + ZX279133_IDM_RX_PAYLOAD_OFFSET;

		if (!lan_source) {
			struct bpf_prog *xdp_prog =
				rcu_dereference_bh(eth->xdp_prog);

			if (xdp_prog) {
				struct xdp_buff xdp;
				u32 act;

				xdp_init_buff(&xdp, ZX279133_RX_PAGE_SIZE,
					      &eth->xdp_rxq[(queue & 7) >> 2]);
				xdp_prepare_buff(&xdp, page_address(page),
						 ZX279133_IDM_RX_PAYLOAD_OFFSET,
						 len, false);
				xdp_ran = true;
				act = bpf_prog_run_xdp(xdp_prog, &xdp);
				switch (act) {
				case XDP_PASS:
					u64_stats_inc(&stats->xdp_pass);
					data = xdp.data;
					len = xdp.data_end - xdp.data;
					break;
				case XDP_TX: {
					struct xdp_frame *xdpf;

					xdpf = xdp_convert_buff_to_frame(&xdp);
					if (xdpf && !zx279133_xdp_enqueue(eth, xdpf)) {
						u64_stats_inc(&stats->xdp_tx);
						*xdp_tx = true;
						goto release_desc;
					}
					u64_stats_inc(&stats->xdp_aborted);
					trace_xdp_exception(ndev, xdp_prog, act);
					if (xdpf) {
						xdp_return_frame(xdpf);
						goto release_desc;
					}
					goto xdp_recycle;
				}
				case XDP_REDIRECT:
					if (!xdp_do_redirect(ndev, &xdp, xdp_prog)) {
						u64_stats_inc(&stats->xdp_redirect);
						*xdp_redirect = true;
						goto release_desc;
					}
					u64_stats_inc(&stats->xdp_aborted);
					trace_xdp_exception(ndev, xdp_prog, act);
					goto xdp_recycle;
				case XDP_ABORTED:
					u64_stats_inc(&stats->xdp_aborted);
					trace_xdp_exception(ndev, xdp_prog, act);
					goto xdp_recycle;
				case XDP_DROP:
					u64_stats_inc(&stats->xdp_drop);
					goto xdp_recycle;
				default:
					u64_stats_inc(&stats->xdp_aborted);
					bpf_warn_invalid_xdp_action(ndev, xdp_prog,
								    act);
					trace_xdp_exception(ndev, xdp_prog, act);
					goto xdp_recycle;
				}
			}
		}

		if (replacement) {
			skb = napi_build_skb(page_address(page),
					     ZX279133_RX_PAGE_SIZE);
			if (!skb) {
				u64_stats_inc(&stats->rx_skb_alloc_failures);
				zx279133_stats_rx_dropped(eth, ndev);
				zx279133_rx_recycle_page(page);
				goto release_desc;
			}
			skb_mark_for_recycle(skb);
			skb_reserve(skb, data - page_address(page));
			skb_put(skb, len);
		} else {
			u64_stats_inc(&stats->rx_copy_fallbacks);
			skb = napi_alloc_skb(napi, len + VLAN_HLEN + NET_IP_ALIGN);
			if (skb) {
				skb_reserve(skb, NET_IP_ALIGN);
				memcpy(skb_put(skb, len), data, len);
			} else {
				u64_stats_inc(&stats->rx_skb_alloc_failures);
				zx279133_stats_rx_dropped(eth, ndev);
			}
			zx279133_idm_rx_sync_for_device(eth, page, len);
			if (zx279133_idm_rx_post_page(eth, page)) {
				u64_stats_inc(&stats->rx_refill_post_failures);
				zx279133_rx_recycle_page(page);
				zx279133_stats_rx_error(eth, ndev);
			} else {
				refill++;
			}
		}

deliver_skb:
		if (skb) {
			struct net_device *rx_ndev = ndev;
			bool lan_dsa_active;

			lan_dsa_active = READ_ONCE(eth->lan_dsa_active);
			/* Factory CPU RX queues 0..7 belong to the external switch;
			 * queues 8..15 belong to the other CPU datapath group. Unlike the
			 * parsed descriptor fields, that split remains stable when CPU8
			 * strips the LAN transport VLAN before PPU.
			 */
			if (queue < ZX279133_LAN_RX_QUEUE_COUNT &&
			    !zx279133_idm_rx_has_ethertype(skb))
				len += zx279133_idm_rx_restore_lan_l2(
					skb, le32_to_cpu(READ_ONCE(desc->metadata[0])));
			if (lan_source && !lan_dsa_active) {
				if (eth->lan_ndev)
					zx279133_stats_rx_dropped(eth, eth->lan_ndev);
				dev_kfree_skb_any(skb);
				goto release_desc;
			}

			if (lan_dsa_active && lan_source && eth->lan_ndev) {
				u16 transport_vid = zx279133_idm_rx_lan_vport(word1);
				bool dsa_header = false;

				/* The tag is gone: the port comes from word 1. */
				if (!transport_vid)
					transport_vid = ZX279133_LAN_VID;

				rx_ndev = eth->lan_ndev;
				if (len >= ETH_HLEN &&
				    ((struct ethhdr *)skb->data)->h_proto ==
				    htons(ETH_P_REALTEK))
					dsa_header = true;
				else if (len >= sizeof(struct vlan_ethhdr))
					dsa_header = zx279133_idm_rx_preserve_dsa_vlan(
						(struct vlan_ethhdr *)skb->data);
				/* Native Realtek and VLAN-based DSA headers are already
				 * complete. Only synthesize the legacy transport tag for
				 * a genuinely untagged legacy frame.
				 */
				if (!dsa_header)
					zx279133_idm_rx_put_vlan(skb, transport_vid);
			}
			if (!xdp_ran && (rx_ndev->features & NETIF_F_RXCSUM) &&
			    zx279133_idm_rx_parsed(word1, lan_source) &&
			    zx279133_idm_rx_l4_valid(skb)) {
				skb->ip_summed = CHECKSUM_UNNECESSARY;
				u64_stats_inc(&stats->rx_hw_csum_packets);
			}
			/* The terminal PPU program marks only parsed transport tuples.
			 * XDP may have changed that tuple before XDP_PASS delivery.
			 */
			if (!xdp_ran && eth->rx_hash_active &&
			    (rx_ndev->features & NETIF_F_RXHASH) &&
			    (le32_to_cpu(READ_ONCE(desc->metadata[3])) & BIT(31)) &&
			    zx279133_idm_rx_l4_valid(skb)) {
				skb_set_hash(skb, le32_to_cpu(READ_ONCE(desc->metadata[2])),
					     PKT_HASH_TYPE_L4);
				u64_stats_inc(&stats->rx_hw_hash_packets);
			}
			skb_record_rx_queue(skb, eth->xsk_pool ? 0 : (queue & 7) >> 2);
			skb->protocol = eth_type_trans(skb, rx_ndev);
			napi_gro_receive(napi, skb);
			dev_sw_netstats_rx_add(rx_ndev, len);
		}
		goto release_desc;

xdp_recycle:
		if (replacement) {
			zx279133_rx_recycle_page(page);
			goto release_desc;
		}
		zx279133_idm_rx_sync_for_device(eth, page, len);

reuse_buffer:
		if (buffer.xsk) {
			if (zx279133_idm_rx_post_xsk(eth, buffer.xdp)) {
				u64_stats_inc(&stats->rx_refill_post_failures);
				xsk_buff_free(buffer.xdp);
				zx279133_stats_rx_error(eth, ndev);
			} else {
				refill++;
			}
			goto release_desc;
		}

		if (zx279133_idm_rx_post_page(eth, page)) {
			u64_stats_inc(&stats->rx_refill_post_failures);
			zx279133_rx_recycle_page(page);
			zx279133_stats_rx_error(eth, ndev);
		} else {
			refill++;
		}

release_desc:
		if (refill == refill_before) {
			spin_lock_bh(&eth->rx_buffer_lock);
			u64_stats_inc(&stats->rx_refill_shortfalls);
			if (WARN_ON_ONCE(eth->rx_refill_deficit >=
					 ZX279133_IDM_RX_BUFFER_COUNT)) {
				eth->rx_refill_deficit =
					ZX279133_IDM_RX_BUFFER_COUNT;
			} else {
				eth->rx_refill_deficit++;
				if (eth->rx_refill_deficit >
				    eth->rx_refill_deficit_high_water)
					eth->rx_refill_deficit_high_water =
						eth->rx_refill_deficit;
			}
			spin_unlock_bh(&eth->rx_buffer_lock);
		}
		WRITE_ONCE(desc->address, 0);
		rx_work++;
	}

	if (rx_work) {
		dma_wmb();
		writel(rx_work | (queue << 12), idm + ZX279133_IDM_RX_RELEASE);
		u64_stats_add(&stats->rx_release_published, rx_work);
		u64_stats_add(&stats->rx_queue_descs[queue], rx_work);
		u64_stats_add(&stats->rx_refill_published,
			      zx279133_idm_rx_flush_refill(eth));
	}

	return rx_work;
}

static int zx279133_idm_rx_alloc_post(struct zx279133_eth *eth)
{
	struct xdp_buff *xdp;
	struct page *page;
	int ret;

	if (eth->xsk_pool) {
		xdp = xsk_buff_alloc(eth->xsk_pool);
		if (!xdp)
			return -ENOMEM;
		ret = zx279133_idm_rx_post_xsk(eth, xdp);
		if (ret)
			xsk_buff_free(xdp);
		return ret;
	}

	page = page_pool_dev_alloc_pages(eth->rx_page_pool);
	if (!page)
		return -ENOMEM;
	ret = zx279133_idm_rx_post_page(eth, page);
	if (ret)
		zx279133_rx_recycle_page(page);
	return ret;
}

static unsigned int zx279133_idm_rx_recover_refill(struct zx279133_eth *eth)
{
	struct zx279133_rx_stats *stats = &eth->rx_stats[0];
	unsigned int recovered = 0;

	if (!READ_ONCE(eth->rx_refill_deficit))
		return 0;
	u64_stats_inc(&stats->rx_refill_recovery_attempts);
	while (recovered < ZX279133_RX_REFILL_RECOVERY_BATCH) {
		spin_lock_bh(&eth->rx_buffer_lock);
		if (!eth->rx_refill_deficit) {
			spin_unlock_bh(&eth->rx_buffer_lock);
			break;
		}
		if (eth->rx_page_map_count >= ZX279133_IDM_RX_BUFFER_COUNT) {
			spin_unlock_bh(&eth->rx_buffer_lock);
			u64_stats_inc(&stats->rx_refill_recovery_failures);
			break;
		}
		eth->rx_refill_deficit--;
		spin_unlock_bh(&eth->rx_buffer_lock);
		if (zx279133_idm_rx_alloc_post(eth)) {
			spin_lock_bh(&eth->rx_buffer_lock);
			eth->rx_refill_deficit++;
			spin_unlock_bh(&eth->rx_buffer_lock);
			u64_stats_inc(&stats->rx_page_alloc_failures);
			u64_stats_inc(&stats->rx_refill_recovery_failures);
			break;
		}
		recovered++;
	}
	u64_stats_add(&stats->rx_refill_published,
		      zx279133_idm_rx_flush_refill(eth));
	u64_stats_add(&stats->rx_refill_recovery_pages, recovered);
	if (eth->xsk_pool && xsk_uses_need_wakeup(eth->xsk_pool)) {
		if (READ_ONCE(eth->rx_refill_deficit))
			xsk_set_rx_need_wakeup(eth->xsk_pool);
		else
			xsk_clear_rx_need_wakeup(eth->xsk_pool);
	}
	return recovered;
}

void zx279133_idm_rx_refill_work(struct work_struct *work)
{
	struct zx279133_eth *eth =
		container_of(to_delayed_work(work), struct zx279133_eth,
			     rx_refill_work);

	if (!READ_ONCE(eth->rx_running) || !READ_ONCE(eth->napi_enabled) ||
	    !READ_ONCE(eth->rx_refill_deficit))
		return;

	atomic64_inc(&eth->rx_refill_retry_work_runs);
	napi_schedule(&eth->napi);
}

static const u8 zx279133_idm_rx_poll_order[ZX279133_IDM_CPU_RX_QUEUES] = {
	7, 15, 6, 14, 5, 13, 4, 12, 3, 11, 2, 10, 1, 9, 0, 8,
};

static int zx279133_idm_rx_poll_group(struct zx279133_eth *eth,
				      struct napi_struct *napi,
				      unsigned int group, int budget)
{
	struct zx279133_rx_stats *stats = &eth->rx_stats[group];
	bool xdp_redirect = false;
	bool xdp_tx = false;
	bool xsk = !!eth->xsk_pool;
	u32 mask = xsk ? ZX279133_IDM_NAPI_MASK :
		   group ? ZX279133_IDM_DIRECT_RX_MASK : ZX279133_IDM_LOCAL_MASK;
	unsigned int cpu = raw_smp_processor_id();
	u8 cursor = eth->rx_poll_cursor[group];
	int work = 0;
	int scanned;

	if (unlikely(!budget)) {
		spin_lock_bh(&eth->tx_lock);
		zx279133_idm_tx_reclaim_locked(eth);
		spin_unlock_bh(&eth->tx_lock);
		if (!group)
			zx279133_xsk_tx(eth);
		return 0;
	}

	u64_stats_update_begin(&stats->syncp);
	u64_stats_inc(&stats->rx_napi_polls);
	if (cpu < ARRAY_SIZE(stats->cpu_polls))
		u64_stats_inc(&stats->cpu_polls[cpu]);
	if (atomic_fetch_or(BIT(group), &eth->rx_poll_active) & BIT(!group))
		u64_stats_inc(&stats->overlaps);
	/* A common hardware supply can deliver a page from the other pool. */
	xdp_set_return_frame_no_direct();
	for (scanned = 0;
	     scanned < ARRAY_SIZE(zx279133_idm_rx_poll_order) && work < budget;
	     scanned++) {
		u8 queue = zx279133_idm_rx_poll_order[cursor];
		u16 count;

		cursor = (cursor + 1) &
			 (ARRAY_SIZE(zx279133_idm_rx_poll_order) - 1);
		if (!(mask & BIT(queue)))
			continue;
		/*
		 * The queue is scanned, not gated on a counter.
		 *
		 * This used to read a per-queue packet count out of
		 * IDM+0xc4+4*queue and only look at the queue when that count
		 * was non-zero.  Those words are not a packet count on this
		 * hardware: on this board's own vendor system, receiving
		 * frames normally, every word from IDM+0xc4 up to IDM+0xfc
		 * reads back zero - the vendor's own on-demand dump of the
		 * whole IDM register file has zeros in that range - so the
		 * gate was never open and no descriptor was ever looked at.
		 * The gate is gone, and with it the whole RX path's only
		 * reader.
		 *
		 * The scan is cheap: the descriptor ring is stopped by the
		 * first descriptor whose address is zero, which is exactly
		 * what an empty queue looks like, so an empty queue costs
		 * one read and no register write.
		 */
		count = min_t(u16, budget - work, ZX279133_IDM_RX_RING_SIZE);
		work += zx279133_idm_rx_process_queue(eth, napi, queue,
						      count, &xdp_tx,
						      &xdp_redirect);
	}
	eth->rx_poll_cursor[group] = cursor;
	if (xdp_tx)
		zx279133_xdp_flush(eth);
	if (xdp_redirect)
		xdp_do_flush();
	xdp_clear_return_frame_no_direct();

	spin_lock_bh(&eth->tx_lock);
	zx279133_idm_tx_reclaim_locked(eth);
	spin_unlock_bh(&eth->tx_lock);
	if (!group) {
		zx279133_xsk_tx(eth);
		zx279133_idm_rx_recover_refill(eth);
	}
	if (READ_ONCE(eth->rx_refill_deficit) &&
	    READ_ONCE(eth->rx_page_map_count) < ZX279133_IDM_RX_BUFFER_COUNT &&
	    READ_ONCE(eth->rx_running))
		mod_delayed_work(system_wq, &eth->rx_refill_work,
				 msecs_to_jiffies(ZX279133_RX_REFILL_RETRY_MS));

	u64_stats_add(&stats->rx_napi_work, work);
	if (work >= budget)
		u64_stats_inc(&stats->rx_napi_budget_exhaustions);
	atomic_andnot(BIT(group), &eth->rx_poll_active);
	u64_stats_update_end(&stats->syncp);
	if (work < budget && napi_complete_done(napi, work)) {
		unsigned long flags;

		spin_lock_irqsave(&eth->irq_lock, flags);
		if (eth->rx_running)
			zx279133_idm_set_masked(eth, mask, false);
		spin_unlock_irqrestore(&eth->irq_lock, flags);
	}
	return work;
}

int zx279133_idm_rx_poll(struct napi_struct *napi, int budget)
{
	struct zx279133_eth *eth = container_of(napi, struct zx279133_eth, napi);

	return zx279133_idm_rx_poll_group(eth, napi, 0, budget);
}

int zx279133_idm_lan_rx_poll(struct napi_struct *napi, int budget)
{
	struct zx279133_eth *eth = container_of(napi, struct zx279133_eth,
					       lan_napi);

	return zx279133_idm_rx_poll_group(eth, napi, 1, budget);
}

irqreturn_t zx279133_idm_rx_irq(int irq, void *data)
{
	struct zx279133_eth *eth = data;
	unsigned long flags;
	bool running;

	atomic64_inc(&eth->rx_irq_count);
	spin_lock_irqsave(&eth->irq_lock, flags);
	running = eth->rx_running;
	zx279133_idm_set_masked(eth, ZX279133_IDM_DIRECT_RX_MASK, true);
	spin_unlock_irqrestore(&eth->irq_lock, flags);

	if (running)
		napi_schedule_irqoff(eth->xsk_pool ? &eth->napi : &eth->lan_napi);

	return IRQ_HANDLED;
}

irqreturn_t zx279133_idm_local_irq(int irq, void *data)
{
	struct zx279133_eth *eth = data;
	unsigned long flags;
	bool running;

	atomic64_inc(&eth->idm_local_irq_count);
	spin_lock_irqsave(&eth->irq_lock, flags);
	running = eth->rx_running;
	zx279133_idm_set_masked(eth, ZX279133_IDM_LOCAL_MASK, true);
	spin_unlock_irqrestore(&eth->irq_lock, flags);

	if (running)
		napi_schedule_irqoff(&eth->napi);

	return IRQ_HANDLED;
}

static void zx279133_idm_rx_put_active_pages(struct zx279133_eth *eth)
{
	unsigned int i;

	for (i = 0; i < ZX279133_IDM_RX_PAGE_MAP_SIZE; i++) {
		struct zx279133_rx_page_entry *entry = &eth->rx_page_map[i];

		if (!entry->page)
			continue;
		if (entry->xsk)
			xsk_buff_free(entry->xdp);
		else
			zx279133_rx_recycle_page(entry->page);
		entry->page = NULL;
		entry->key = 0;
		entry->xsk = false;
	}
	eth->rx_page_map_count = 0;
}

int zx279133_idm_rx_prepare(struct zx279133_eth *eth)
{
	size_t desc_size = sizeof(struct zx279133_idm_desc) *
			   ZX279133_IDM_RX_QUEUES * ZX279133_IDM_RX_RING_SIZE;
	unsigned int i;
	int ret;

	if (WARN_ON(eth->rx_page_map_count))
		zx279133_idm_rx_put_active_pages(eth);
	memset(eth->rx_descs, 0, desc_size);
	memset(eth->rx_normal_bp, 0, (ZX279133_IDM_BP_RING_SIZE +
				      ZX279133_IDM_JUMBO_BP_RING_SIZE) *
				     sizeof(__be32));
	memset(eth->rx_cons, 0, sizeof(eth->rx_cons));
	eth->rx_bp_prod = 0;
	memset(eth->rx_poll_cursor, 0, sizeof(eth->rx_poll_cursor));
	eth->rx_refill_pending = 0;
	eth->rx_refill_deficit = 0;

	for (i = 0; i < ZX279133_IDM_RX_BUFFER_COUNT; i++) {
		ret = zx279133_idm_rx_alloc_post(eth);
		if (!ret)
			continue;
		if (!eth->xsk_pool)
			goto err_put_pages;
		break;
	}

	zx279133_idm_rx_flush_refill(eth);
	eth->rx_refill_deficit = ZX279133_IDM_RX_BUFFER_COUNT - i;
	if (eth->rx_refill_deficit > eth->rx_refill_deficit_high_water)
		eth->rx_refill_deficit_high_water = eth->rx_refill_deficit;
	if (eth->xsk_pool && xsk_uses_need_wakeup(eth->xsk_pool)) {
		if (eth->rx_refill_deficit)
			xsk_set_rx_need_wakeup(eth->xsk_pool);
		else
			xsk_clear_rx_need_wakeup(eth->xsk_pool);
	}
	eth->rx_prepared = true;
	return 0;

err_put_pages:
	zx279133_idm_rx_put_active_pages(eth);
	return ret;
}

void zx279133_idm_rx_release(struct zx279133_eth *eth)
{
	cancel_delayed_work_sync(&eth->rx_refill_work);
	zx279133_idm_rx_put_active_pages(eth);
	eth->rx_prepared = false;
}

void zx279133_idm_tx_flush_queue_locked(struct zx279133_eth *eth,
					unsigned int queue)
{
	struct zx279133_tx_ring *tx = &eth->tx[queue];
	u32 count = tx->notify_pending;

	if (!count)
		return;
	/* Vendor idm_cpu_nb_tx_update() requires DSB ST before publication. */
	wmb();
	writel(count << ZX279133_IDM_TX_DOORBELL_COUNT_SHIFT,
	       eth->base + ZX279133_IDM_BASE +
	       zx279133_idm_tx_doorbell_reg(ZX279133_IDM_CPU_TX_FIRST + queue));
	tx->notify_pending = 0;
	tx->submitted += count;
	eth->tx_doorbell_writes++;
	eth->tx_doorbell_descs += count;
}

void zx279133_idm_tx_flush_locked(struct zx279133_eth *eth)
{
	int i;

	for (i = 0; i < ZX279133_CPU_TX_QUEUES; i++)
		zx279133_idm_tx_flush_queue_locked(eth, i);
}

unsigned int zx279133_idm_tx_reclaim_queue_locked(struct zx279133_eth *eth,
						  unsigned int queue)
{
	struct zx279133_tx_ring *tx = &eth->tx[queue];
	u32 done_reg = zx279133_idm_tx_done_reg(ZX279133_IDM_CPU_TX_FIRST + queue);
	u16 done = readl(eth->base + ZX279133_IDM_BASE + done_reg) & 0xffff;
	u16 completed = done - tx->done;
	u16 reclaimed;
	u32 bytes[2] = {};
	u16 packets[2] = {};
	u16 xsk_completed = 0;

	eth->tx_reclaim_polls++;
	if (completed > tx->pending) {
		if (net_ratelimit())
			netdev_warn(eth->ndev,
				    "TX queue %u: invalid completion delta %u (pending %u)\n",
				    queue, completed, tx->pending);
		completed = tx->pending;
	}
	tx->done = done;
	reclaimed = completed;

	while (completed--) {
		struct zx279133_tx_slot *slot =
			&tx->slots[tx->consumer];

		if (slot->skb) {
			struct net_device *ndev = slot->ndev ?: eth->ndev;
			unsigned int owner = ndev != eth->ndev;

			if (slot->dma_mapped)
				dma_unmap_single(eth->dev, slot->dma, slot->len,
						 DMA_TO_DEVICE);
			dev_sw_netstats_tx_add(ndev, 1, slot->len);
			bytes[owner] += slot->len;
			packets[owner]++;
			dev_consume_skb_any(slot->skb);
		} else if (slot->xdpf) {
			if (slot->dma_mapped)
				dma_unmap_single(eth->dev, slot->dma, slot->len,
						 DMA_TO_DEVICE);
			dev_sw_netstats_tx_add(eth->ndev, 1, slot->len);
			xdp_return_frame(slot->xdpf);
		} else if (slot->xsk_rx) {
			dev_sw_netstats_tx_add(eth->ndev, 1, slot->len);
			xsk_buff_free(slot->xsk_rx);
		} else if (slot->xsk_tx) {
			dev_sw_netstats_tx_add(eth->ndev, 1, slot->len);
			xsk_completed++;
		}
		memset(slot, 0, sizeof(*slot));
		tx->consumer = (tx->consumer + 1) &
					   (ZX279133_IDM_TX_DEPTH - 1);
		tx->pending--;
	}

	/* BQL requires one completion report per reclaim round, not per skb. */
	if (packets[0])
		netdev_tx_completed_queue(netdev_get_tx_queue(eth->ndev, queue),
					  packets[0], bytes[0]);
	if (packets[1])
		netdev_tx_completed_queue(netdev_get_tx_queue(eth->lan_ndev, queue),
					  packets[1], bytes[1]);
	tx->completed += reclaimed;
	if (xsk_completed)
		xsk_tx_completed(eth->xsk_pool, xsk_completed);

	if (!READ_ONCE(eth->tx_stopping) &&
	    tx->pending < ZX279133_IDM_TX_DEPTH - 1) {
		if (__netif_subqueue_stopped(eth->ndev, queue))
			netif_wake_subqueue(eth->ndev, queue);
		if ((READ_ONCE(eth->datapath_users) &
		     ZX279133_DATAPATH_USER_LAN) &&
		    READ_ONCE(eth->lan_datapath_ready) && eth->lan_ndev &&
		    netif_running(eth->lan_ndev) &&
		    __netif_subqueue_stopped(eth->lan_ndev, queue))
			netif_wake_subqueue(eth->lan_ndev, queue);
	}

	return reclaimed;
}

unsigned int zx279133_idm_tx_reclaim_locked(struct zx279133_eth *eth)
{
	unsigned int reclaimed = 0;
	int i;

	for (i = 0; i < ZX279133_CPU_TX_QUEUES; i++)
		reclaimed += zx279133_idm_tx_reclaim_queue_locked(eth, i);
	return reclaimed;
}

void zx279133_idm_tx_reclaim_work(struct work_struct *work)
{
	struct zx279133_eth *eth =
		container_of(to_delayed_work(work), struct zx279133_eth,
			     tx_reclaim_work);
	unsigned int reclaimed = 0;
	bool rearm = false;

	spin_lock_bh(&eth->tx_lock);
	if (!READ_ONCE(eth->tx_stopping) && eth->tx_prepared &&
	    READ_ONCE(eth->hardware_prepared)) {
		eth->tx_reclaim_work_runs++;
		zx279133_idm_tx_flush_locked(eth);
		reclaimed = zx279133_idm_tx_reclaim_locked(eth);
		eth->tx_reclaim_work_packets += reclaimed;
		rearm = zx279133_idm_tx_pending(eth);
	}
	spin_unlock_bh(&eth->tx_lock);
	zx279133_xsk_tx(eth);

	if (rearm && !READ_ONCE(eth->tx_stopping))
		mod_delayed_work(system_wq, &eth->tx_reclaim_work,
				 msecs_to_jiffies(ZX279133_TX_RECLAIM_DELAY_MS));
}

bool zx279133_idm_tx_drain(struct zx279133_eth *eth)
{
	unsigned long timeout = jiffies + msecs_to_jiffies(100);

	do {
		spin_lock_bh(&eth->tx_lock);
		zx279133_idm_tx_flush_locked(eth);
		zx279133_idm_tx_reclaim_locked(eth);
		if (!zx279133_idm_tx_pending(eth)) {
			spin_unlock_bh(&eth->tx_lock);
			return true;
		}
		spin_unlock_bh(&eth->tx_lock);
		usleep_range(100, 200);
	} while (time_before(jiffies, timeout));

	return false;
}

static const u32 zx279133_idm_cfg_offsets[] = {
	0x0000, 0x0008, 0x000c, 0x0010,
	0x0014, 0x0018, 0x001c, 0x0020, 0x0024,
	0x0028, 0x002c, 0x0030, 0x0034, 0x0038,
	0x0054, 0x0058, 0x005c, 0x0060,
	0x0070, 0x0074,
	0x0090, 0x0094,
	0x00c0, 0x0124,
	0x03fc, 0x05c0,
	/* Buffer pools, free rings, and interrupt-group configuration. */
	0x0040, 0x0044, 0x0048, 0x004c, 0x0050,
	0x006c,
	0x0104, 0x0108, 0x0118, 0x011c, 0x0408,
	0x010c, 0x0110, 0x040c,
	/* QMG descriptor-read mode; saved and restored like the rest of the block. */
	0x0490,
};

/*
 * The IDM bring-up register image, taken from this board's own vendor module
 * (np_133.ko) rather than from plat_132.ko, which is another product's
 * module and programs the same block differently.
 *
 * np_133.ko's idm_init() and the three helpers it calls write:
 *  - the control word: |= 0xf0000, (v & 0xf00fffff) | 0x01f00000, |= 0x1000,
 *    then idm_rx_desc_init() does (v & 0x8fffffff) | (depth code << 28),
 *    where the depth code is 0 for the stock DT's 0x400 descriptors per queue.
 *    From the hardware's reset value that is 0x01ff1007 - the value the
 *    vendor's own board reads back.
 *  - the per-queue routing words 0x014..0x034 are left alone; the hardware
 *    keeps its reset default 0x00010001, which is also what the vendor's
 *    board reads back, so the driver writes the same value explicitly.
 *  - 0x010 = g_idm_rx_bp_retev_num_th (0x40), 0x038 = 0x1000,
 *    0x06c = 0x800, 0x070 = the RX descriptor count (depth - 0x18),
 *    0x074 = 0, 0x090 = 0, 0x094 = 0, 0x0bc = 0,
 *    0x0c0 = the descriptor mode, 0x00c = the TX config depth << 16,
 *    0x124 = 0x80, 0x3fc = 0x0f49, 0x5c0 = 7 and 0x490 = 0.
 *  - the buffer pools: the RX normal pool base at 0x104, the RX jumbo pool
 *    base at 0x108, the three free rings at 0x118, 0x11c and 0x408, and the
 *    two pool-size registers 0x10c and 0x110, which carry the ring depths
 *    rather than a byte count.
 *
 * Everything the vendor leaves at its reset value is left there here too:
 * a register the vendor never writes is a register this driver has no
 * business writing.
 */

static void zx279133_idm_tx_configure(struct zx279133_eth *eth)
{
	void __iomem *idm = eth->base + ZX279133_IDM_BASE;
	u32 value;
	int i;

	for (i = 0; i < ARRAY_SIZE(zx279133_idm_cfg_offsets); i++)
		eth->idm_cfg_saved[i] = readl(idm + zx279133_idm_cfg_offsets[i]);

	value = eth->idm_cfg_saved[0];
	value |= 0x000f0000;
	/*
	 * np_133.ko's idm_init() does (v & 0xf00fffff) | 0x01f00000 - bits 20
	 * through 24, so bit 24 ends up set - and only bit 12 of the two that
	 * follow, never bit 13.  The proven vendor image reads back 0x01ff1007.
	 */
	value = (value & 0xf00fffff) | 0x01f00000;
	value |= 0x00001000;
	value = (value & 0x8fffffff) | (ZX279133_IDM_RX_CFG_DEPTH << 28);
	writel(value, idm + 0x0000);

	writel(lower_32_bits(eth->rx_descs_dma), idm + 0x0008);
	writel(ZX279133_IDM_TX_CFG_DEPTH << 16, idm + 0x000c);
	/* g_idm_rx_bp_retev_num_th, from the stock DT's bp_retev_num_th. */
	writel(0x00000040, idm + 0x0010);
	/*
	 * The reset default of the per-queue routing words, which is what
	 * this board's own vendor module leaves them at.  See the comment
	 * above zx279133_idm_cfg_offsets[].
	 */
	for (i = 0x14; i <= 0x34; i += 4)
		writel(0x00010001, idm + i);
	writel(0x00001000, idm + 0x0038);
	writel(0x06060606, idm + 0x0054);
	writel(0x00060606, idm + 0x0058);
	writel(0x07070707, idm + 0x005c);
	writel(0x07070707, idm + 0x0060);
	/*
	 * The RX descriptor count is the per-queue depth minus the 24 queue
	 * headers (idm_rx_desc_init: sub w1, w1, #0x18), not depth - 1.
	 */
	writel(ZX279133_IDM_RX_QUEUE_DESC_DEPTH - 0x18, idm + 0x0070);
	writel(0, idm + 0x0074);
	writel(0, idm + 0x0090);
	writel(0, idm + 0x0094);
	writel(ZX279133_IDM_DESC_MODE, idm + 0x00c0);
	/*
	 * QMG descriptor-read mode. A store-only scan of this board's own np_133.ko
	 * (staging/idmgap.py, both controls passing) leaves exactly two IDM words the
	 * vendor's bring-up writes and this driver never touches: +0x2c8, which U-Boot
	 * already leaves at the vendor's value (docs/09), and this one. U-Boot's proven
	 * store list (docs/09 section 4) does not include +0x490 either, so on this board
	 * the word has never been programmed by anything and still holds its reset
	 * default - a value no working driver has ever run with. The vendor reaches it
	 * from idm_qmg_desc_rd_mode_cfg(), a six-instruction function whose only store
	 * is `str wzr, [idm, #0x490]`. Its call site cannot be read out of the .ko,
	 * because a BL in a relocatable object is a relocation placeholder rather than
	 * a target, so this is placed with the other idm_init() words.
	 */
	writel(0, idm + 0x0490);
	/*
	 * The other word of the same diff, and the one that is actually wrong on this
	 * board. The vendor's idm_init() does ldr / and #0xc0ffffff / orr #0x12000000 /
	 * str here, i.e. field bits[29:24] = 0x12 with everything else preserved. Read
	 * with /sbin/devmem2 on the flashed mainline (2026-10-20 15:48) this word is
	 * 0x00112233: the field is 0x00, so neither U-Boot nor mainline leaves it the
	 * way docs/09 assumed, and docs/09's "do not build a kit for +0x2c8" was based
	 * on U-Boot's intent rather than on the value hardware ends up holding. The
	 * read itself is trustworthy: +0x10 came back 0x80 and +0x14 0x00800080, both
	 * exactly what this driver writes, and the neighbouring words of the two refuted
	 * candidates (+0xbc = 0, +0x490 = 0 with +0x48c = 0x01030101 and +0x498 =
	 * 0x02000000) show those two zeros are real register contents.
	 */
	writel((readl(idm + 0x02c8) & 0xc0ffffffu) | 0x12000000u, idm + 0x02c8);
	writel(0x00000080, idm + 0x0124);
	writel(0x00000f49, idm + 0x03fc);
	writel(7, idm + 0x05c0);
	/* The vendor's bring-up clears the TX queue-8 doorbell. */
	writel(0, idm + 0x00bc);

	/*
	 * The interrupt mask.  The vendor's board holds zero here - every
	 * source is unmasked and idm_int_enable()/idm_int_disable() do the
	 * read-modify-write from there - so the driver starts from the same
	 * state instead of masking all 27 sources and unmasking them again.
	 */
	writel(0, idm + 0x0040);
	/*
	 * Source -> IRQ-line routing, copied word for word from this board's working
	 * vendor 4.19 boot: np_133.ko prints "irq[0..3] sg_idm_irq_mask_conf(0x71ffbfb
	 * 0x200000 0x400000 0x800404)" and its idm_int_mask_configure_init() stores
	 * exactly those four words to +0x44/+0x48/+0x4c/+0x50, one per line - so +0x44
	 * is the routing for SPI1, the "rx" IRQ requested in zx279133-main.c. Of the 22
	 * sources the vendor delivers there, the previous 0xf00f/0x00ff0000/0/0x0f00
	 * split stranded 4..7 and 24..26 on no line at all and sent 8,9,11 to
	 * local-test and 16..20 to the "idm" line, which is never requested. SPI2 and
	 * SPI3 stay masked at the distributor, so writing the vendor's words for those
	 * two lines cannot storm.
	 */
	writel(0x071ffbfb, idm + 0x0044);
	writel(0x00200000, idm + 0x0048);
	writel(0x00400000, idm + 0x004c);
	writel(0x00800404, idm + 0x0050);
	writel(0x00000800, idm + 0x006c);

	/*
	 * The buffer pools.  idm_rx_bp_init() writes the RX normal pool base
	 * to +0x104 and the RX jumbo pool base - one normal_bp_size further on -
	 * to +0x108; idm_rev_bp_init() writes the three free rings to +0x118,
	 * +0x11c and +0x408.  This driver had the jumbo pool pointed at a free
	 * ring and the free rings shifted by one slot, so the hardware was
	 * handed a pool base that was not a pool at all.
	 */
	writel(lower_32_bits(eth->rx_normal_bp_dma), idm + 0x0104);
	writel(lower_32_bits(eth->rx_jumbo_bp_dma), idm + 0x0108);
	writel(lower_32_bits(eth->idm_base + ZX279133_IDM_FREE_RING1),
	       idm + 0x0118);
	writel(lower_32_bits(eth->idm_base + ZX279133_IDM_FREE_RING2),
	       idm + 0x011c);
	writel(lower_32_bits(eth->idm_base + ZX279133_IDM_FREE_RING3),
	       idm + 0x0408);
	/*
	 * The two pool-size registers carry the ring depths, not a byte count:
	 * idm_rx_bp_init() writes ((depth >> 12) - 1) << 21 and idm_rev_bp_init()
	 * adds ((free_ring_depth >> 12) - 1) << 10, with bit 8 set in both.
	 * With the stock DT's depths - 0x2000 normal, 0x1000 jumbo, 0x10000 free
	 * - that is 0x203d00 and 0x3d00, exactly what the vendor's board holds.
	 */
	writel(zx279133_idm_bp_ring_cfg(ZX279133_IDM_BP_RING_SIZE,
					ZX279133_IDM_FREE_RING_DEPTH),
	       idm + 0x010c);
	writel(zx279133_idm_bp_ring_cfg(ZX279133_IDM_JUMBO_BP_RING_SIZE,
					ZX279133_IDM_FREE_RING_DEPTH),
	       idm + 0x0110);
	writel(0x00003d00, idm + 0x040c);
}

static void zx279133_idm_tx_deconfigure(struct zx279133_eth *eth)
{
	void __iomem *idm = eth->base + ZX279133_IDM_BASE;
	int i;

	for (i = 0; i < ARRAY_SIZE(zx279133_idm_cfg_offsets); i++)
		writel(eth->idm_cfg_saved[i], idm + zx279133_idm_cfg_offsets[i]);
}

int zx279133_idm_tx_prepare(struct zx279133_eth *eth)
{
	size_t size = sizeof(*eth->tx_descs) * ZX279133_IDM_TX_QUEUES *
		      ZX279133_IDM_TX_DEPTH;
	u32 ready;
	int i;

	ready = readl(eth->base + ZX279133_NP_READY);
	if ((ready & ZX279133_NP_READY_MASK) != ZX279133_NP_READY_MASK)
		return dev_err_probe(eth->dev, -EIO,
				     "NPPT handoff is not ready: %#x\n", ready);

	memset(eth->tx_descs, 0, size);

	eth->idm_tx_base_saved = readl(eth->base + ZX279133_IDM_BASE +
					ZX279133_IDM_TX_BASE);
	for (i = 0; i < ZX279133_CPU_TX_QUEUES; i++) {
		struct zx279133_tx_ring *tx = &eth->tx[i];

		tx->done = readl(eth->base + ZX279133_IDM_BASE +
				zx279133_idm_tx_done_reg(ZX279133_IDM_CPU_TX_FIRST + i)) &
			   0xffff;
		tx->producer = tx->done & (ZX279133_IDM_TX_DEPTH - 1);
		tx->consumer = tx->producer;
		tx->pending = 0;
		tx->notify_pending = 0;
		memset(tx->slots, 0, sizeof(*tx->slots) * ZX279133_IDM_TX_DEPTH);
		netdev_tx_reset_queue(netdev_get_tx_queue(eth->ndev, i));
		if (eth->lan_ndev)
			netdev_tx_reset_queue(netdev_get_tx_queue(eth->lan_ndev, i));
	}
	zx279133_idm_tx_configure(eth);
	writel(lower_32_bits(eth->tx_descs_dma),
	       eth->base + ZX279133_IDM_BASE + ZX279133_IDM_TX_BASE);
	eth->tx_prepared = true;

	return 0;
}

void zx279133_idm_tx_deactivate(struct zx279133_eth *eth)
{
	writel(eth->idm_tx_base_saved,
	       eth->base + ZX279133_IDM_BASE + ZX279133_IDM_TX_BASE);
	zx279133_idm_tx_deconfigure(eth);
}

static void zx279133_idm_tx_release_queue(struct zx279133_eth *eth,
					  unsigned int queue)
{
	struct zx279133_tx_ring *tx = &eth->tx[queue];
	u32 completed_bytes[2] = {};
	u16 completed_packets[2] = {};
	u16 xsk_completed = 0;

	tx->notify_pending = 0;
	while (tx->pending) {
		struct zx279133_tx_slot *slot =
			&tx->slots[tx->consumer];
		struct zx279133_tx_slot owner = *slot;

		memset(slot, 0, sizeof(*slot));
		tx->consumer = (tx->consumer + 1) &
					   (ZX279133_IDM_TX_DEPTH - 1);
		tx->pending--;
		spin_unlock_bh(&eth->tx_lock);

		if (owner.skb) {
			struct net_device *ndev = owner.ndev ?: eth->ndev;
			unsigned int dev = ndev != eth->ndev;

			if (owner.dma_mapped)
				dma_unmap_single(eth->dev, owner.dma, owner.len,
						 DMA_TO_DEVICE);
			zx279133_stats_tx_dropped(eth, ndev);
			completed_packets[dev]++;
			completed_bytes[dev] += owner.len;
			dev_kfree_skb_any(owner.skb);
		} else if (owner.xdpf) {
			if (owner.dma_mapped)
				dma_unmap_single(eth->dev, owner.dma, owner.len,
						 DMA_TO_DEVICE);
			xdp_return_frame(owner.xdpf);
		} else if (owner.xsk_rx) {
			xsk_buff_free(owner.xsk_rx);
		} else if (owner.xsk_tx) {
			zx279133_stats_tx_dropped(eth, eth->ndev);
			xsk_completed++;
		}
		spin_lock_bh(&eth->tx_lock);
	}
	if (completed_packets[0])
		netdev_tx_completed_queue(netdev_get_tx_queue(eth->ndev, queue),
					  completed_packets[0], completed_bytes[0]);
	if (completed_packets[1])
		netdev_tx_completed_queue(netdev_get_tx_queue(eth->lan_ndev, queue),
					  completed_packets[1], completed_bytes[1]);
	if (xsk_completed)
		xsk_tx_completed(eth->xsk_pool, xsk_completed);
}

void zx279133_idm_tx_release(struct zx279133_eth *eth, bool hardware_alive)
{
	int i;

	if (!eth->tx_prepared)
		return;

	spin_lock_bh(&eth->tx_lock);
	if (hardware_alive) {
		zx279133_idm_tx_flush_locked(eth);
		zx279133_idm_tx_reclaim_locked(eth);
	}
	for (i = 0; i < ZX279133_CPU_TX_QUEUES; i++)
		zx279133_idm_tx_release_queue(eth, i);
	spin_unlock_bh(&eth->tx_lock);

	if (hardware_alive)
		zx279133_idm_tx_deactivate(eth);
	eth->tx_prepared = false;
}
