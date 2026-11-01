// SPDX-License-Identifier: GPL-2.0-only
/* Raw baseline XG-PON OMCI submission through the dedicated IDM queue 0.
 * The optical MAC activation, OMCC provisioning and integrity key lifecycle
 * are separate prerequisites, not inferred from an Ethernet carrier bit.
 */
#include <linux/iopoll.h>
#include "zx279133.h"
#include "zx279133-omci-wire.h"

void zx279133_omci_reclaim(struct zx279133_eth *eth)
{
	u16 done;

	if (!eth->omci_pending)
		return;
	done = readl(eth->base + ZX279133_IDM_BASE +
		     zx279133_idm_tx_done_reg(0)) & 0xffff;
	if (done == eth->omci_done)
		return;
	/* There is at most one outstanding management descriptor. */
	dma_rmb();
	eth->omci_pending = false;
	eth->omci_completed++;
	eth->omci_done = done;
}

int zx279133_omci_prepare(struct zx279133_eth *eth)
{
	zx279133_omci_reclaim(eth);
	if (eth->omci_pending)
		return -EBUSY;
	eth->omci_done = readl(eth->base + ZX279133_IDM_BASE +
			      zx279133_idm_tx_done_reg(0)) & 0xffff;
	eth->omci_producer = eth->omci_done & (ZX279133_IDM_TX_DEPTH - 1);
	return 0;
}

int zx279133_omci_send(struct zx279133_eth *eth, u16 gem,
			const u8 *frame, size_t length)
{
	struct zx279133_idm_desc *descriptor;
	u32 done;
	int result;

	if (length != 48 || gem >= 0xfffe)
		return -EINVAL;
	mutex_lock(&eth->datapath_lock);
	if (!eth->tx_prepared || eth->tx_stopping) {
		result = -ENETDOWN;
		goto out;
	}
	zx279133_omci_reclaim(eth);
	if (eth->omci_pending) {
		result = -EBUSY;
		goto out;
	}
	if (!eth->omci_buffer) {
		eth->omci_buffer = dmam_alloc_coherent(eth->dev, 48,
						      &eth->omci_dma, GFP_KERNEL);
		if (!eth->omci_buffer) {
			result = -ENOMEM;
			goto out;
		}
		if (upper_32_bits(eth->omci_dma)) {
			result = -ERANGE;
			goto out;
		}
	}
	if (upper_32_bits(eth->omci_dma)) {
		result = -ERANGE;
		goto out;
	}
	memcpy(eth->omci_buffer, frame, 48);
	/* The reserved first ring is already covered by tx_descs' coherent DMA
	 * allocation. Do not pad to ETH_ZLEN or use LAN/PON data metadata.
	 */
	descriptor = eth->tx_descs + eth->omci_producer;
	memset(descriptor, 0, sizeof(*descriptor));
	descriptor->address = cpu_to_le32(lower_32_bits(eth->omci_dma));
	descriptor->length_flags = cpu_to_le32(zx279133_omci_length_word(48));
	descriptor->metadata[0] = cpu_to_le32(zx279133_omci_control_word());
	descriptor->metadata[4] = cpu_to_le32(zx279133_omci_gem_word(gem));
	eth->omci_pending = true;
	eth->omci_producer = (eth->omci_producer + 1) & (ZX279133_IDM_TX_DEPTH - 1);
	wmb();
	writel(1 << ZX279133_IDM_TX_DOORBELL_COUNT_SHIFT,
	       eth->base + ZX279133_IDM_BASE + zx279133_idm_tx_doorbell_reg(0));
	eth->omci_submitted++;
	result = readl_poll_timeout(eth->base + ZX279133_IDM_BASE +
				    zx279133_idm_tx_done_reg(0), done,
				    (u16)done != eth->omci_done, 100, 20000);
	/* On timeout keep both the descriptor and coherent payload alive. A
	 * later call/drain reclaims it only after the hardware counter advances.
	 */
	if (!result)
		zx279133_omci_reclaim(eth);
out:
	eth->omci_error = result;
	mutex_unlock(&eth->datapath_lock);
	/* 0 means IDM consumed the descriptor, NOT that an OLT accepted it. */
	return result;
}
