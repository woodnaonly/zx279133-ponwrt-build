/* SPDX-License-Identifier: GPL-2.0-only */
/*
 * The 9132's own register map, as the SK-D840N's LAN switch driver needs it.
 *
 * These definitions belong to the 9132 and to nothing else in the tree, so
 * they live here instead of in the shared zx279133.h: that header is the
 * SoC's, and the 7.3 generation of the ZX279133 driver set has no use for
 * the switch's MAC blocks.  The 6.18 generation of this repository keeps the
 * same definitions in its own zx279133.h - the values are identical, and
 * they were carried over from there word for word.
 */
#ifndef ZX279133_9132_H
#define ZX279133_9132_H

#include <linux/bitops.h>
#include <linux/types.h>

/*
 * The shared soft-reset word at NPPT + 0x2c0004 carries one bit per block;
 * the vendor's smac_reset() pulses bit(mac) to reset that MAC and nothing
 * else, so the XMAC and the PCS are not disturbed by it.
 *
 * This word used to be spelled 0x2c004 in the 6.18 generation's header -
 * sixteen times too low - while the comment above it, the vendor's U-Boot
 * (which prints the absolute address 0x192c0004) and the rest of the driver
 * (the XMAC0 reset) all said 0x2c0004.  So the pulse went to an address that
 * is not the register: the four internal-PHY MACs were never reset, their
 * "clear-over-ready" word stayed 0, none of them was ever published, and no
 * frame from a LAN jack was ever received.
 */
#define ZX279133_SYS_SOFT_RESET		0x2c0004

/*
 * The four internal-PHY MACs - the vendor's "smac0..3".  Each one has a
 * block of its own at NPPT + 0x40000 * (mac + 1), and each one is published
 * through its own pair of SOPC words.
 *
 * These are not the XMACs.  An XMAC is a GMAC that talks to something outside
 * the chip; an SMAC is the MAC in front of one of the four internal PHYs, and
 * it is on the path a frame takes out of a LAN port.  The vendor's
 * nppt_smac_init() programs all four of them, and a frame that arrives from a
 * LAN port stops at the PHY if they are still at their reset value.
 */
#define ZX279133_SMAC_COUNT		4
#define ZX279133_SMAC_BASE(mac)		(0x40000 * ((mac) + 1))
#define ZX279133_SMAC_AN_CTRL		0x0000
#define ZX279133_SMAC_AN_CTRL2		0x0004
#define ZX279133_SMAC_PKT_FILTER	0x0008
#define ZX279133_SMAC_SPEED		0x00e0
#define ZX279133_SMAC_PORT_CTRL		0x0b00
/* The per-MAC "uni mode" words are absolute: 0x14, 0x18, 0x1c, 0x20. */
#define ZX279133_SMAC_UNI_MODE(mac)	(4 * ((mac) + 5))
#define ZX279133_SMAC_SOPC_READY_BASE	0x34294
#define ZX279133_SMAC_SOPC_SEND_BASE	0x342b0
/*
 * The vendor's nppt_smac_init() pulses this MAC's bit in the word at
 * NPPT + 0x343f0 (it sets the bit and then clears it again) before it
 * publishes the MAC.  The word's steady value on the vendor's board is 0
 * (staging/ctl/vendor-serial-mods/tm-sopc.txt, word 0xfc).
 */
#define ZX279133_SMAC_SOPC_PULSE	0x343f0
#define ZX279133_SMAC_AN_CTRL_VALUE	0x00fa2203
#define ZX279133_SMAC_AN_CTRL_RESET_VALUE	0x00ba2203
#define ZX279133_SMAC_AN_CTRL2_VALUE	0x3fff
#define ZX279133_SMAC_RESET_WORD	ZX279133_SYS_SOFT_RESET
/*
 * The MAC's packet-filter word, at +0x08 of its block.  np_133.ko writes
 * 0x80000001 there, twice, after the reset pulse.
 *
 * The 6.18 generation of this driver used to skip the write because on this
 * board the hardware derived the word itself and writing over it "destroyed"
 * it: the word read back 0x80000000 instead of 0x80000001.  That is not
 * destruction, it is a write-one-to-clear bit - the same thing happens to the
 * speed word's bit 12 - and the vendor's own board ends up with the same
 * value, because the vendor's dump of the block was taken before its Linux
 * driver wrote it.  So the vendor's write is made here as well, and the
 * read-back is printed.
 */
#define ZX279133_SMAC_PKT_FILTER_VALUE	0x80000001
/*
 * The MAC's speed/duplex word, at +0xe0 of its block.  np_133.ko writes
 * 0x00011200 there - the word the hardware already holds (0x00011000) with
 * bit 9 set - after the reset pulse.
 *
 * The value used to be spelled 0x00012000 in the 6.18 generation's header,
 * which is not what the vendor writes: bit 9 and bit 12 differ.  The
 * experiments that "proved" the word must not be written used that wrong
 * constant.
 */
#define ZX279133_SMAC_SPEED_VALUE	0x00011200
#define ZX279133_SMAC_PORT_CTRL_BIT	0x200
#define ZX279133_SMAC_UNI_MODE_MASK	0x03800000

/*
 * The 9132's SOPC send-enable word.  It is the word the internal-PHY MACs
 * are published through; the 6.18 generation's header spelled it
 * ZX279133_XMAC0_SOPC_SEND_ENABLE and the switch driver spells the same
 * address ZX279133_XMAC0_SOPC_SEND.  Both names are carried over so that the
 * two generations can be read side by side.
 */
#define ZX279133_XMAC0_SOPC_SEND_ENABLE	0x342c0

/*
 * The 9132's outer port.  The port has a DSA user port of its own now: it is
 * DSA port 8, it carries the transport VID 63, and the tagger dispatches on
 * that VID (docs/92 §90).
 *
 * The IDM reports the source port of a frame in the descriptor's word 1, so
 * a frame that came in on this port is the one with source 5 - and it is
 * handed to the conduit with the transport VID 63, which is what the tagger
 * reads to say which port it came in on.
 */
#define ZX279133_9132_OUTERPHY_PPU	5

#endif /* ZX279133_9132_H */
