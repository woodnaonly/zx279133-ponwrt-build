/* SPDX-License-Identifier: GPL-2.0-only */
#ifndef ZX279133_OMCI_WIRE_H
#define ZX279133_OMCI_WIRE_H
/* np_133.ko idm_net_cpu_tx +0x12e00..0x132cc, XG-PON branch.
 * Queue 0 is management, not an Ethernet TX queue. Caller supplies the MIC.
 * These helpers are also compiled by the offline descriptor contract test.
 */
static inline unsigned int zx279133_omci_length_word(unsigned int length)
{
	return (length << 1) | 0x0f000000;
}
static inline unsigned int zx279133_omci_control_word(void)
{
	return 0x00400001;
}
static inline unsigned int zx279133_omci_gem_word(unsigned int gem)
{
	return 0x1e070000 | (gem & 0xffff);
}
#endif
