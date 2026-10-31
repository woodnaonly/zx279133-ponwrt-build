/* SPDX-License-Identifier: GPL-2.0-only */

#ifndef __ZX279133_H__
#define __ZX279133_H__

#include <linux/clk.h>
#include <linux/dma-mapping.h>
#include <linux/interrupt.h>
#include <linux/mdio.h>
#include <linux/netdevice.h>
#include <linux/phylink.h>
#include <linux/ptp_clock_kernel.h>
#include <linux/workqueue.h>

#include <net/xdp.h>

#include "zx279133-stats.h"
#include "zx279133-lan.h"

struct gpio_desc;
struct led_classdev;

#define ZX279133_NUM_CLOCKS	6
#define ZX279133_NUM_IRQS	5

/* Validated WAN/LAN hardware constants. */
#define ZX279133_IDM_CPU_TX_FIRST	1
/*
 * The port the NPPT-side transmit descriptors name.
 *
 * It was `#define ... 6` and nothing could reach it: the debugfs "txp" verb
 * writes eth->lan_tx_port, which only the LAN branch reads, so 6 was compiled
 * into every frame on this side for the whole history of this port.
 *
 * 6 is this file's own legend for XMAC1, and the jack the field has to point at
 * is the RTL8226 2.5G one, which the vendor calls switch port 4 / chip port 5
 * (docs/15) and which np_133.ko's np_user_table_init() hands to
 * port_attr_def_src_is_extend_port_flag_set(5, 1) in its xmac0_to_extend case.
 *
 * Read out of the bootloader that works, 2026-10-26, cable in panel LAN1, in the
 * same boot as the ping that this PC answered ("host 192.168.9.25 is alive",
 * `current idm tx outport is 4`):
 *
 *	b0008000: b0328000 0f800054 00440000 00000000	TX, 42 bytes = the ARP
 *	b0028000: b00e8000 0785004a c0a80919 c0a80901	RX, the echo reply back
 *
 * 0x00440000 is bit 22 (port valid) with 4 in bits 16..21 - where mainline writes
 * 0x00460000 - so the default here is 4, the number the working descriptor
 * carries. It is not yet established that 4 means the same thing in mainline's
 * hands: the field that goes out as 2 for panel LAN2 (zx279133.h's
 * ZX279133_LAN_TX_PORT, measured jack by jack in docs/90 §5) has 4 as PPU4 =
 * panel LAN4, while U-Boot reaches the 2.5G jack with 4 in it, and the vendor's
 * own chip numbering calls that jack 5. U-Boot drives that lane as an extend port
 * straight off XMAC0 (`xmac0 is used`, its outerphy init names phy_id 0x5) and may
 * therefore never consult this field the way the switch does. So 4 is the starting
 * value and the sweep is the verdict, not this paragraph.
 *
 * The RX side of the same pair of words says something on its own: the descriptor
 * of the echo reply that came back through this jack reads `0785004a` in its
 * second word, and 0x0785 & 0x3f = 5 - the source port in the chip's numbering,
 * where this file's own LAN receive path maps 1..4 to the four UNIs
 * (zx279133-idm.c:346 zx279133_idm_rx_lan_vport).
 *
 * One variable therefore cannot be both numbers, but it does not have to be: the
 * comparison at zx279133-idm.c:372 only decides CHECKSUM_UNNECESSARY (its one
 * caller sets skb->ip_summed, at 1215), so whichever value is wrong for the
 * direction costs an offload and loses no frame. Splitting it is a separate
 * question from making this jack carry traffic, and it is recorded here so the
 * next reader does not "fix" the RX side by changing this number.
 *
 * The parameter exists because finding a number by build-per-candidate is not
 * feasible here:
 *
 *	echo 5 > /sys/module/zx279133/parameters/zx279133_tx_port
 *
 * and because the one sweep that was run before, on 2026-10-19 (run 69, docs/07
 * 补八十九), came back "all eight values, 0 received" under conditions that made
 * that answer meaningless: that round's own counters read `xmac_tx_frames: 0` and
 * `idm_tx_done: 5` against 71 frames submitted, so the hardware had taken no
 * descriptor at all and no value of this field could have put a frame on the
 * wire. With that blocker since fixed, the same counters on this jack today read
 * `xmac_tx_frames 744 / xmac_tx_good_frames 757 / idm_tx_done 8497`: frames do
 * leave, and do not arrive. The sweep is meaningful for the first time now.
 */
extern u16 zx279133_tx_port;
/*
 * The chip port the LAN path's transmit descriptors name.
 *
 * The vendor's IDM transmit descriptors carry the *target UNI jack's* chip
 * port in the output-port field - 1..4, never 5 - and the 9132's chip
 * ports are its SPA memory ids: PPU 1..4 are the four jacks.  This board's
 * DSA map puts PPU 2 behind DSA port 5 (lan2), which is the jack the test
 * PC is plugged into.
 *
 * What 5 and 6 are is still an open question and this is where it is kept.
 * 5 is the memory mainline runs its LAN ingress program on
 * (zx279133-np.c:3814), and it is also the number the vendor's bootloader
 * names for the 2.5G jack's lane: its outerphy init prints
 * `port=4 outerphy_mode=5, phy_RTL8226 enable:1` (docs/13:208), its RX
 * descriptor of a frame from that jack names source 5, and its
 * `spa_port_attr_op help` table reads XMAC0_MEMID=5 / XMAC1_MEMID=6.
 * 6 is the memory whose attribute word already carries bit 19
 * (0x148e0000, the value port_attr_def_src_is_extend_port_flag_set()
 * writes) and whose MF mirror U-Boot left populated
 * (`960c0001`-shaped: `... 0e014400 000001f1 148e0000`).
 *
 * Neither is the answer to the jack, measured 2026-10-26 21:36..21:53: with
 * memory 5 rewritten to the *complete word set of a working jack* - entry
 * program 0xa6a5, regs 10/13/14/17/18 copied from memory 1, attribute 19 with
 * the extend bit, its MF mirror, and every protocol trapped to the CPU - the
 * bench PC sent 20 frames and the board received none: lan1, eth0 and br-lan
 * all stayed at rx_packets 0 and XMAC0's own statistics block
 * (nppt +0x142000..+0x1420fc) did not move by one word.  So the number in a
 * descriptor or an attribute is not what gates this jack, and what is missing
 * is behind the PHY, on the side it makes with the SoC - see docs/92 §11 and
 * the "m45" console command that reads it.
 */
#define ZX279133_LAN_TX_PORT		2
#define ZX279133_LAN_RX_QUEUE_COUNT	8
#define ZX279133_LAN_TRANSPORT_VID_MIN	59
/*
 * 63 is the fifth user port's: the 2.5G jack is the 9132's outerphy port
 * (PPU 5) and the DSA port it is behind is 8, and the tagger's VID is
 * 55 + the DSA port number.
 */
#define ZX279133_LAN_TRANSPORT_VID_MAX	63
#define ZX279133_LAN_INGRESS_VID	1
#define ZX279133_LAN_VID		62
#define ZX279133_LAN_MAX_MTU		(ZX279133_MAX_MTU + VLAN_HLEN)
/*
 * The 9132's per-port attribute memories (the SPA memory ids, which are the
 * chip's own port numbers): 1..4 are the four UNI jacks, 5 is XMAC0 and 15
 * is the CPU.
 *
 * The CPU is the trap port, not a MAC.  The vendor prints the namespace
 * itself - print_p_port(), np_133.ko 0xf6c80: "SPA_CPU_MEMID = 0xf" - and
 * its IDM transmit descriptors carry inport = 0x0f, while the outport map
 * sends dest_port 15 to the CPU's queue 0x193.  PPU 5 is XMAC0, the 10G
 * SerDes MAC, which on this board has no peer: the vendor's board reads zero
 * in its registers, because the vendor never programs the block.
 */
#define ZX279133_9132_UNI_PPU_FIRST	1
#define ZX279133_9132_UNI_PPU_LAST	4
#define ZX279133_9132_CONDUIT_PPU	15
/*
 * PPU 5 is the 2.5G jack's port on the 9132: the vendor's own port for that
 * jack, whose attribute table it names spa_port_attr_xmac_0 (docs/92 §70.3).
 * It is not one of the four UNI jacks and it is not the CPU trap port, so
 * zx279133_9132_port_init() leaves it alone - zx279133_9132_ppu5_words()
 * gives it the vendor's whole word set instead.
 *
 * What is on the far side of it, and what is not (docs/92 §82, measured):
 *
 *   - the far side is the **RTL8226**, over a 1000BASE-X link (the RTL8226
 *     answers Clause 45 with a 1000BASE-X PCS BMCR of 0x3000, and U-Boot's
 *     own net init brings that link up: "phy_id:0x5; port:0x4;", "mac 4 phy
 *     status changed: 1000M full-duplex");
 *   - the **XMAC0 lane is not this port**.  XMAC0 + Uni SerDes + XPCS0 is
 *     the 9132's **CPU port** - the DSA conduit, the lane every 1G jack
 *     reaches the SoC through, and the lane the switch driver programs.
 *
 * The port has a DSA user port of its own now: it is DSA port 8, it carries
 * the transport VID 63, and the tagger dispatches on that VID (docs/92 §90).
 *
 * The IDM reports the source port of a frame in the descriptor's word 1, so
 * a frame that came in on this port is the one with source 5 - and it is
 * handed to the conduit with the transport VID 63, which is what the tagger
 * reads to say which port it came in on.
 */
#define ZX279133_9132_OUTERPHY_PPU	5
#define ZX279133_TX_RECLAIM_DELAY_MS	10
#define ZX279133_DATAPATH_USER_WAN	BIT(0)
#define ZX279133_DATAPATH_USER_LAN	BIT(1)
#define zx279133_tx_selector		0x0f
#define zx279133_tx_pon_control		0x08000000
/*
 * The fourth metadata word of the IDM transmit descriptor on the LAN path.
 *
 * The vendor's own dump_desc_tx() calls this word the GEM port's LLID id
 * plus its queue id.  The vendor writes 0x003e0000 on its LAN path and
 * 0x1f568623 on its PON path; this driver wrote the PON value on both,
 * which is a PON control word in a LAN descriptor.
 */
#define ZX279133_LAN_TX_WORD4		0x003e0000
#define zx279133_tx_word4_bit23		0
#define zx279133_tx_hw_csum		1

/*
 * The BMU geometry, as the vendor's own board runs it.
 *
 * The buffer size in the global register at NPPT + 0x68 (the size of one
 * normal buffer in the low half, one jumbo buffer in the high half) is not a
 * free choice: the switch core and the SMAC lay a buffer out with it, and the
 * mainline driver used to disagree with the vendor on it - 0x840 against
 * 0x900 bytes - while writing that register twice, with two different
 * values.  The vendor's value is 0x3f000900: a 0x900 byte normal buffer and a
 * 0x3f00 byte jumbo one.
 *
 * The pool count stays at the mainline's 0x1800 normal buffers, because the
 * count is a software decision (how many buffers the pool holds) while the
 * size is a property of the hardware, and 0x1800 * 0x900 still fits in the
 * reserved area.  What matters is that the two agree with the register.
 *
 * Measured on the vendor board on 2026-10-22 (staging/ctl/vendor-serial-mods):
 *	NPPT + 0x68    = 0x3f000900	the buffer size
 *	NPPT + 0x6c    = 0x00000801	the usable size
 *	NPPT + 0x20078 = 0x00000801	the RED's usable size, same constant
 */
#define ZX279133_BMU_REQUIRED_SIZE	0x0101e000
#define ZX279133_SE_HASH_REQUIRED_SIZE	0x01000000
#define ZX279133_BMU_BPPE_SIZE		0x00020000
/*
 * The BMU's BPPI threshold.  The vendor's np_133.ko writes this value and
 * leaves it there, and the vendor's board reads it back; the tree's
 * zx279133_bmu_prepare() writes the same value and then overwrites it with
 * 0x01058080 after it has enabled the BMU, so the fixup in
 * zx279133_bmu_threshold_fixup() puts it back.
 */
#define ZX279133_BMU_BPPI_THRESH_VALUE	0x0104c040
#define ZX279133_BMU_NORMAL_COUNT	0x00001800
#define ZX279133_BMU_NORMAL_SIZE		0x00000900
#define ZX279133_BMU_JUMBO_COUNT		0x00000020
#define ZX279133_BMU_JUMBO_SIZE		0x00003f00
#define ZX279133_BMU_DESC_SIZE		0x00200000
#define ZX279133_BMU_JUMBO_BPPE_OFFSET	0x00010000

#define ZX279133_ROUTE_CLK	0x019c
#define ZX279133_ROUTE_CTRL	0x2438
#define ZX279133_ROUTE_CLK_EN	BIT(0)
#define ZX279133_ROUTE_XMAC1	BIT(2)

#define ZX279133_IDM_CCI_VALUE	0x00200020

#define ZX279133_NP_READY		0x0080
#define ZX279133_NP_READY_MASK		0x01fd

#define ZX279133_GREG_BUFFER_SIZE	0x0068
#define ZX279133_GREG_BUFFER_USABLE	0x006c
#define ZX279133_GREG_RED_BUFFER_USABLE	0x20078
#define ZX279133_GREG_BUFFER_SIZE_VALUE	0x3f000900
/*
 * The usable size.  The vendor's board reads 0x801 here and at the RED's
 * copy of the same counter (NPPT + 0x20078); the mainline driver used
 * 0x781 for both, which is a different number of usable buffers than the
 * vendor's chip hands out.
 */
#define ZX279133_GREG_BUFFER_USABLE_VALUE 0x00000801
#define ZX279133_GREG_SMAC_RUNT_MASK	BIT(18)
#define ZX279133_GREG_SMAC6_RUNT_MASK	BIT(16)
#define ZX279133_GREG_XMAC_RUNT_MASK	0x00000410

#define ZX279133_GLOBAL_INIT_DONE	0x0080
#define ZX279133_GLOBAL_INIT_DONE_MASK	0x000001fd
#define ZX279133_SIPC_CFG		0x4000
#define ZX279133_SIPC_RX_SPA_MASK	GENMASK(2, 0)
#define ZX279133_SIPC_RX_SPA_VALUE	0x3
/* np.ko sipc_set_sch_mode_sw(0): scheduler handoff mode must be zero. */
#define ZX279133_SIPC_SCH_MODE_SW	BIT(2)
#define ZX279133_SIPC_RX_GAP		0x4010
#define ZX279133_SIPC_RX_GAP_VALUE	0x1042
#define ZX279133_SYS_SOFT_RESET		0x2c0004
#define ZX279133_SYS_SDET_RESET_N	BIT(4)
#define ZX279133_SYS_PON_RESET_N		BIT(31)

#define ZX279133_DMA_AXI_MODE		0x38000
#define ZX279133_DMA_AXI_RID0		0x38080
#define ZX279133_DMA_AXI_RID1		0x38084
#define ZX279133_DMA_AXI_WID		0x38088
#define ZX279133_DMA_RDSE_GAP		0x38398
#define ZX279133_DMA_AXI_MODE_BIT	BIT(21)
#define ZX279133_DMA_RDSE_GAP_MASK	GENMASK(14, 10)

#define ZX279133_BMU_ENABLE		0x3c000
#define ZX279133_BMU_BPPI_THRESH		0x3c004
#define ZX279133_BMU_BPO_THRESH		0x3c008
#define ZX279133_BMU_BPPE_BASE		0x00054
#define ZX279133_BMU_JUMBO_BPPE_BASE	0x00058
#define ZX279133_BMU_DESC_BASE		0x0005c
#define ZX279133_BMU_NORMAL_BASE		0x00060
#define ZX279133_BMU_JUMBO_BASE		0x00064
#define ZX279133_BMU_BUFFER_SIZE		0x00068
#define ZX279133_BMU_NORMAL_CFG		0x3c048
#define ZX279133_BMU_JUMBO_CFG		0x3c04c
#define ZX279133_BMU_NORMAL_INDEX_MAX	0x3c058
#define ZX279133_BMU_JUMBO_INDEX_MAX	0x3c05c
#define ZX279133_BMU_NORMAL_RLS_CFG	0x3c060
#define ZX279133_BMU_JUMBO_RLS_CFG	0x3c064
#define ZX279133_BMU_NORMAL_COUNT_REG	0x20030
#define ZX279133_BMU_JUMBO_COUNT_REG	0x20034
#define ZX279133_BMU_USABLE_SIZE		0x20078

#define ZX279133_QMG_RAM_REQ		0x24000
#define ZX279133_QMG_RAM_DONE		0x24004
#define ZX279133_QMG_WATERMARK		0x24014
#define ZX279133_QMG_DESC_OUT		0x24018
#define ZX279133_QMG_WOE_OUT		0x24020
#define ZX279133_QMG_DESC_IN		0x24060
#define ZX279133_QMG_VR_GAP		0x24068
#define ZX279133_QMG_WOE_RED_WATERMARK	0x20088
#define ZX279133_QMG_RAM_MASK		GENMASK(4, 0)
#define ZX279133_QMG_LOW16_MASK		GENMASK(15, 0)
#define ZX279133_QMG_HIGH16_MASK		GENMASK(31, 16)
#define ZX279133_QMG_ENABLE_BIT		BIT(31)
#define ZX279133_QMG_VR_TOTAL_MASK	GENMASK(26, 18)
#define ZX279133_QMG_VR_UP_MASK		GENMASK(17, 9)
#define ZX279133_QMG_VR_DOWN_MASK	GENMASK(8, 0)

/*
 * The queue manager, the way this board's vendor firmware has it.
 *
 * The tree's zx279133_qmg_prepare() writes the SR1010's numbers, and they do
 * not agree with the RED geometry this board's vendor firmware runs: the
 * vendor's 0x24014 holds 0x0bb8_0fa0 where the tree writes 0x2bb8_2fa0, and
 * the same holds for the descriptor counters at 0x24018, 0x24020 and 0x24060.
 *
 * The RED and the QMG are the two halves of one queue, so they have to be set
 * to the same board's numbers.  The RED's half is put back by
 * zx279133_red_vendor_fixup() in zx279133-main.c; this is the other half.
 *
 * The values are the vendor board's own, out of its console dump
 * (staging/ctl/vendor-serial-mods/tm-qmg.txt, words 0x05, 0x06, 0x08 and
 * 0x18, and tm-red.txt word 0x22 for the WOE watermark).
 */
#define ZX279133_QMG_VENDOR_WATERMARK		0x0bb80fa0
#define ZX279133_QMG_VENDOR_DESC_OUT		0x80003000
#define ZX279133_QMG_VENDOR_WOE_OUT		0x80000000
#define ZX279133_QMG_VENDOR_DESC_IN		0x10001000
#define ZX279133_QMG_VENDOR_WOE_RED_WATERMARK	0x08bf07ff

#define ZX279133_SOPC_CRC_PADDING	0x343c4
#define ZX279133_SOPC_FIFO_THRESHOLD	0x343d0
#define ZX279133_SOPC_CRC_PADDING_MASK	GENMASK(13, 0)
#define ZX279133_SOPC_FIFO_THRESHOLD_MASK 0x00001f1f
#define ZX279133_SOPC_FIFO_THRESHOLD_VALUE 2056

#define ZX279133_RED_CFG			0x20004
#define ZX279133_RED_IN_SHARE_MAX	0x20040
#define ZX279133_RED_UP_IN_SHARE_MAX	0x20060
#define ZX279133_RED_DOWN_IN_SHARE_MAX	0x20064
#define ZX279133_RED_ALL_OUT_SHARE_MAX	0x2006c
#define ZX279133_RED_DOWN_OUT_MAX	0x20070
#define ZX279133_RED_UP_OUT_MAX		0x20074
#define ZX279133_RED_DESC_WATERMARK	0x20084
#define ZX279133_RED_IDM_DESC_WATERMARK	0x20088
/*
 * The RED's drop-enable words and its length filter.
 *
 * 0x20038 bit 0 enables the "illegal queue id" drop, 0x2003c bits 1 and 2
 * enable the "packet length is zero" and the "WOE packet length error"
 * drops, and 0x2008c bits 0..13 hold the minimum packet length the RED's
 * length filter accepts.
 *
 * np_133.ko never writes any of them - red_set_pkt_len_0_en_cfg(),
 * red_set_woe_pkt_len_err_drop_en_cfg(), red_set_illegal_qid_drop_cfg() and
 * red_set_mini_pkt_len() have no caller in np_133.ko or in any other vendor
 * module - so the vendor's board runs with the hardware's reset defaults:
 * 0x01, 0x06 and 0x40 (staging/ctl/vendor-serial-mods/tm-red.txt, words 0x0e,
 * 0x0f and 0x23).
 *
 * The mainline board reads exactly the same two words (measured 2026-10-23:
 * 0x20038 = 0x00000001 and 0x2003c = 0x00000006), so the difference the
 * "pktlen0 drop" symptom showed is *not* in these words: it is in the length
 * the RED is handed.  Clearing them does stop the drops, but it only hides
 * the symptom, so they are left as they are and are printed instead.
 *
 * 0x20030 and 0x20034 are the buffer-pool counters the BMU block owns; their
 * exact meaning is not established yet.  The vendor's board reads 0x3400 and
 * 0x0000 there, the mainline board 0x1800 and 0x0020.
 */
#define ZX279133_RED_DROP_ENABLE	0x20038
#define ZX279133_RED_LEN_FILTER	0x2003c
#define ZX279133_RED_MINI_PKT_LEN	0x2008c
#define ZX279133_RED_POOL_COUNT		0x20030
#define ZX279133_RED_POOL_COUNT_JUMBO	0x20034
#define ZX279133_RED_CPU133_CFG		0x2024c
#define ZX279133_RED_IDM_IN_SHARE_MAX	0x20344
#define ZX279133_RED_WOE_IN_SHARE_MAX	0x20348
#define ZX279133_RED_WOE_OUT_SHARE_MAX	0x2034c
#define ZX279133_RED_CFG_MASK		GENMASK(7, 0)
#define ZX279133_RED_SHARE14_MASK	GENMASK(13, 0)
#define ZX279133_RED_SHARE15_MASK	GENMASK(14, 0)

/*
 * The tree's zx279133_red_offsets[] and zx279133_red_np1_offsets[] arrays
 * are in the tree's own file, but the header that names their entries is
 * this overlay's, so the order of the saved-value arrays is known here.
 * A restore writes the saved values back, so the saved values are what
 * the chip would be left with if the hardware were ever unprepared.
 */
#define ZX279133_RED_SAVED_CFG			0
#define ZX279133_RED_SAVED_IN_SHARE_MAX		1
#define ZX279133_RED_SAVED_CPU133_CFG		2
#define ZX279133_RED_SAVED_IDM_IN_SHARE_MAX	3
#define ZX279133_RED_SAVED_WOE_IN_SHARE_MAX	4
#define ZX279133_RED_SAVED_UP_IN_SHARE_MAX	5
#define ZX279133_RED_SAVED_DOWN_IN_SHARE_MAX	6
#define ZX279133_RED_SAVED_UP_OUT_MAX		7
#define ZX279133_RED_SAVED_DOWN_OUT_MAX		8
#define ZX279133_RED_SAVED_ALL_OUT_SHARE_MAX	9
#define ZX279133_RED_SAVED_WOE_OUT_SHARE_MAX	10
#define ZX279133_RED_SAVED_DESC_WATERMARK	11
#define ZX279133_RED_SAVED_IDM_DESC_WATERMARK	12

#define ZX279133_RED_NP1_SAVED_Q0_TH		0
#define ZX279133_RED_NP1_SAVED_Q1_TH		1
#define ZX279133_RED_NP1_SAVED_DESC_WM		6
#define ZX279133_RED_NP1_SAVED_ISU_TH		7
#define ZX279133_RED_LOW16_MASK		GENMASK(15, 0)

/*
 * red_np1_config(256, 0x100) register image (NPPT word registers):
 * queue 0 threshold at 0x8004, queues 1-5 at 0x80d5-0x80d9, descriptor
 * watermark at 0x80da, and ISU thresholds at 0x8098-0x809e.
 */
#define ZX279133_RED_NP1_Q0_TH		0x20010
#define ZX279133_RED_NP1_Q1_TH		0x20354
#define ZX279133_RED_NP1_DESC_WM	0x20368
#define ZX279133_RED_NP1_ISU_TH		0x20260
#define ZX279133_RED_NP1_TH_MASK	GENMASK(13, 0)
#define ZX279133_RED_NP1_DESC_MASK	GENMASK(15, 0)
/*
 * The NP1 queue threshold.  The vendor's board reads 0x00100020 at
 * NPPT + 0x20010, so the threshold itself is 0x20; the mainline driver used
 * 272 (0x110).  The vendor's np_133.ko never calls the function that writes
 * this register, so 0x20 is what the hardware comes up with.
 */
#define ZX279133_RED_NP1_TH_VALUE	32
#define ZX279133_RED_NP1_DESC_VALUE	0x100
#define ZX279133_RED_NP1_ISU_VALUE	15

/*
 * RED indirect access port (red_ind_write): command at 0x20014
 * (addr | ram_id << 22 | rw << 27), done at 0x20018 bit 0, four data
 * words at 0x2001c-0x20028, and the shared auto-gate at 0xb8 bit 10.
 */
#define ZX279133_RED_IND_CMD		0x20014
#define ZX279133_RED_IND_DONE		0x20018
#define ZX279133_RED_IND_DATA		0x2001c
#define ZX279133_RED_IND_DONE_BIT	BIT(0)
#define ZX279133_RED_IND_RAM_ID_MASK	GENMASK(24, 22)
#define ZX279133_RED_AUTO_GATE_REG	0x00b8
#define ZX279133_RED_AUTO_GATE_BIT	BIT(10)
#define ZX279133_RED_IND_RW_BIT		BIT(27)
#define ZX279133_RED_QUEUE_COUNT	401

/*
 * The RED's configuration as this board's own vendor firmware has it.
 *
 * The tree's zx279133-tm.c programs the RED for the SR1010: RED_CFG 0x7e, the
 * SR1010 share-max numbers and the SR1010 queue-buffer geometry.  This board's
 * vendor module (np_133.ko) programs something else, and the vendor's board
 * reads back the values below - staging/ctl/vendor-serial-mods/tm-red.txt, where
 * the first column is the word index inside the RED block and the NPPT byte
 * offset is 0x20000 + 4 * word.
 *
 * Two of the differences matter for the receive path:
 *
 *   - RED_CFG bit 7 is all_active_mode.  The tree writes 0x7e, which sets
 *     dn/up_share_mode and leaves all_active_mode clear; the vendor writes
 *     0x9e, the other way round.
 *   - the queue-buffer geometry.  The tree gives every queue the SR1010's
 *     numbers; the vendor gives the PON/UNI queues (0x000-0x13f) an IN buffer
 *     of 0x600 and an OUT buffer of 0x1000, and the port blocks (0x140-0x17f,
 *     which is where smac0-3 and xmac0 - the CPU port - live) an IN buffer of
 *     0x300 and an OUT buffer of 0x800.
 *
 * The share-max and watermark registers are the ones the vendor's board reads
 * back; several of them carry bits the vendor never writes (they are the
 * hardware's reset default), so the whole word is written.
 */
#define ZX279133_RED_ALL_SHARE_MAX	0x20050
#define ZX279133_RED_VENDOR_CFG		0x0000009e
#define ZX279133_RED_VENDOR_CPU133_CFG	0x000a0001
#define ZX279133_RED_VENDOR_IN_SHARE_MAX	0x00000c00
#define ZX279133_RED_VENDOR_ALL_SHARE_MAX	0x00002600
#define ZX279133_RED_VENDOR_UP_IN_SHARE_MAX	0x10000900
#define ZX279133_RED_VENDOR_DOWN_IN_SHARE_MAX	0x04000500
#define ZX279133_RED_VENDOR_ALL_OUT_SHARE_MAX	0x00003000
#define ZX279133_RED_VENDOR_DOWN_OUT_MAX	0x06001000
#define ZX279133_RED_VENDOR_UP_OUT_MAX	0x14002000
#define ZX279133_RED_VENDOR_DESC_WATERMARK	0x3f9f3e9f
#define ZX279133_RED_VENDOR_IDM_DESC_WATERMARK	0x08bf07ff
#define ZX279133_RED_VENDOR_IDM_IN_SHARE_MAX	0x00000100
#define ZX279133_RED_VENDOR_WOE_IN_SHARE_MAX	0x01900000
#define ZX279133_RED_VENDOR_WOE_OUT_SHARE_MAX	0x025807ff
#define ZX279133_RED_VENDOR_NP1_Q0_TH	0x00100020
#define ZX279133_RED_VENDOR_NP1_Q1_TH	0x00100020
#define ZX279133_RED_VENDOR_NP1_DESC_WM	0x00000010
#define ZX279133_RED_VENDOR_NP1_ISU_TH	0x0005000f

/*
 * The vendor's queue-buffer words, in the form the indirect port takes:
 * the IN buffer RAM (ram_id 2) is "guart | (max << 14)" and the OUT buffer RAM
 * (ram_id 0) is "guart | (max << 12)".
 */
#define ZX279133_RED_VENDOR_IN_UNI	0x01800020	/* 0x20 | (0x600 << 14) */
#define ZX279133_RED_VENDOR_IN_PORT	0x00c00020	/* 0x20 | (0x300 << 14) */
#define ZX279133_RED_VENDOR_IN_ONE	0x00400020	/* 0x20 | (0x100 << 14) */
#define ZX279133_RED_VENDOR_OUT_UNI	0x01000040	/* 0x40 | (0x1000 << 12) */
#define ZX279133_RED_VENDOR_OUT_PORT	0x00800040	/* 0x40 | (0x800 << 12) */
#define ZX279133_RED_VENDOR_OUT_ONE	0x00400020	/* 0x20 | (0x400 << 12) */

/* The queue ranges the vendor programs. */
#define ZX279133_RED_VENDOR_UNI_LAST	0x13f
#define ZX279133_RED_VENDOR_PORT_LAST	0x17f
#define ZX279133_RED_VENDOR_ONE_QUEUE	0x180
#define ZX279133_RED_VENDOR_OUT_ONE_FIRST 0x187
#define ZX279133_RED_VENDOR_OUT_ONE_LAST 0x18c

#define ZX279133_SSCH_CFG		0x2c000
#define ZX279133_SSCH_AGING_TIME		0x2c004
#define ZX279133_SSCH_SPEND_BYTE		0x2c024
#define ZX279133_SSCH_FILL_TIME		0x2c028
#define ZX279133_SSCH_CFG_MASK		(BIT(13) | BIT(12) | BIT(1) | BIT(0))
#define ZX279133_SSCH_SPEND_BYTE_MASK	(GENMASK(15, 8) | GENMASK(5, 0))
#define ZX279133_SSCH_FILL_TIME_MASK	GENMASK(17, 0)

#define ZX279133_WSCH_CFG		0x2c400
#define ZX279133_WSCH_FILL_TIME		0x2c444
#define ZX279133_WSCH_SHARP_ENABLE	BIT(1)
#define ZX279133_WSCH_FILL_TIME_MASK	GENMASK(17, 0)

#define ZX279133_WOE_SCH_CFG		0x2c800
#define ZX279133_WOE_SCH_FILL_TIME	0x2c828
#define ZX279133_WOE_SCH_SHARP_ENABLE	BIT(0)
#define ZX279133_WOE_SCH_FILL_TIME_MASK	GENMASK(17, 0)

#define ZX279133_USCH_CFG		0x28000
#define ZX279133_USCH_AGING_TIME		0x28004
#define ZX279133_USCH_CFG_MASK		GENMASK(6, 0)

#define ZX279133_UOPC_TCONT_CMD		0x30000
#define ZX279133_UOPC_BURST_NUM		0x3004c
#define ZX279133_UOPC_FIFO_DEPTH		0x30200
#define ZX279133_UOPC_FIFO_ENABLE0	0x30204
#define ZX279133_UOPC_FIFO_ENABLE1	0x30208
#define ZX279133_UOPC_FIFO_SELECT0	0x3020c
#define ZX279133_UOPC_FIFO_SELECT1	0x30210
#define ZX279133_UOPC_FIFO_BASE0		0x30218
#define ZX279133_UOPC_FIFO_BASE1		0x3021c
#define ZX279133_UOPC_FIFO_BASE2		0x30220
#define ZX279133_UOPC_FIFO_BASE3		0x30224
#define ZX279133_UOPC_FIFO_BASE4		0x30228
#define ZX279133_UOPC_INFO_AFULL_GAP	0x30240
#define ZX279133_UOPC_PRE_AFULL_GAP0	0x30254
#define ZX279133_UOPC_PRE_AFULL_GAP1	0x30258
#define ZX279133_UOPC_TCONT_CMD_BIT	BIT(3)
#define ZX279133_UOPC_TCONT_MODE_MASK	GENMASK(5, 3)
#define ZX279133_UOPC_HIGH8_MASK		GENMASK(31, 24)
#define ZX279133_UOPC_LOW8_MASK		GENMASK(7, 0)
#define ZX279133_UOPC_LOW2_MASK		GENMASK(1, 0)
#define ZX279133_UOPC_INFO_GAP_MASK	GENMASK(4, 0)
#define ZX279133_UOPC_PRE_GAP_MASK	0x0fff0fff
#define ZX279133_UOPC_LOW12_MASK		GENMASK(11, 0)

#define ZX279133_SDET_FRAME_CFG		0x2040
#define ZX279133_SDET_DOWN_FRAME_CFG	0x2044
#define ZX279133_SDET_UP_MAX_LEN	2048
#define ZX279133_SDET_UP_MIN_LEN	12
#define ZX279133_SDET_DOWN_MAX_LEN	2048
#define ZX279133_SDET_DOWN_MIN_LEN	12
#define ZX279133_SDET_UP_MAX_SHIFT	14
#define ZX279133_SDET_DOWN_MAX_SHIFT	16

#define ZX279133_SMCT_SEL_CFG0		0xc000
#define ZX279133_SMCT_SEL_CFG1		0xc004
#define ZX279133_SMCT_CTRL		0xc008
#define ZX279133_SMCT_MC_TH		0xc038
#define ZX279133_SMCT_NP1_TH		0xc05c
#define ZX279133_SMCT_IDM_SSCH_TH	0xc060
#define ZX279133_SMCT_COS_TH0		0xc028
#define ZX279133_SMCT_INPORT_TH0	0xc03c
#define ZX279133_SMCT_SEL_CFG0_VALUE	0x1f00
#define ZX279133_SMCT_SEL_CFG1_VALUE	0x1fff
#define ZX279133_SMCT_CTRL_VALUE	0x9
#define ZX279133_SMCT_COS_TH_VALUE	0x1b00
#define ZX279133_SMCT_COS7_TH_VALUE	0x1d00
#define ZX279133_SMCT_COS_LAST_VALUE	0x1fff
#define ZX279133_SMCT_INPORT_TH_VALUE	0x1c00
#define ZX279133_SMCT_NP1_TH_VALUE	0x1a00
#define ZX279133_SMCT_MC_TH_VALUE	0x1c00
#define ZX279133_SMCT_MC_TH_MASK	GENMASK(13, 0)
#define ZX279133_SMCT_PAIR_PRESERVE_MASK	(GENMASK(31, 30) | GENMASK(15, 14))
#define ZX279133_SMCT_PAIR_HI_SHIFT	16

/*
 * The switch's own copy of the configuration, the way this board's vendor
 * firmware has it.
 *
 * The tree's zx279133_smct_prepare() writes the SR1010's thresholds, and this
 * board's vendor firmware runs different ones: the vendor's per-COS
 * thresholds at 0xc028..0xc034 are 0x1900_1900, 0x1900_1900, 0x1900_1900 and
 * 0x1b00_1fff, the multicast threshold at 0xc038 is 0x1fff_0d00, the eight
 * per-input-port thresholds at 0xc03c..0xc058 are all 0x1fff_1fff, the NP1
 * threshold at 0xc05c is 0x1d00 and the IDM scheduler threshold at 0xc060 is
 * 0x1fff_1fff.
 *
 * These are the thresholds that decide a frame's colour and its queue, so
 * they are part of the same set as the RED's and the QMG's.
 *
 * The values are the vendor board's own, out of its console dump
 * (staging/ctl/vendor-serial-mods/nppu-smct.txt, words 0x00, 0x0a..0x18).
 */
#define ZX279133_SMCT_VENDOR_SEL_CFG0		0x1e00
#define ZX279133_SMCT_VENDOR_COS_TH0		0x19001900
#define ZX279133_SMCT_VENDOR_COS_TH1		0x19001900
#define ZX279133_SMCT_VENDOR_COS_TH2		0x19001900
#define ZX279133_SMCT_VENDOR_COS_TH3		0x1b001fff
#define ZX279133_SMCT_VENDOR_MC_TH		0x1fff0d00
#define ZX279133_SMCT_VENDOR_INPORT_TH		0x1fff1fff
#define ZX279133_SMCT_VENDOR_NP1_TH		0x1d00
#define ZX279133_SMCT_VENDOR_IDM_SSCH_TH	0x1fff1fff

#define ZX279133_ISU_CFG		0x10000
#define ZX279133_ISU_INIT_REQ		0x10004
#define ZX279133_ISU_DWRR_WEIGHT	0x10040
#define ZX279133_ISU_RING_GAP		0x1010c
#define ZX279133_ODMA_ISU_SP_EN	0x14000
#define ZX279133_ISU_CFG_MASK		GENMASK(1, 0)
#define ZX279133_ISU_SPA_SP_EN		BIT(1)
#define ZX279133_ISU_INIT_REQ_MASK	GENMASK(2, 0)
#define ZX279133_ISU_INIT_REQ_VALUE	0x7
/* Factory PON/ETH/protocol/new-input weights; zero starves the WOE NP1 path. */
#define ZX279133_ISU_DWRR_WEIGHT_VALUE	0x01010202
#define ZX279133_ISU_RING_GAP_MASK	GENMASK(11, 0)
#define ZX279133_ISU_RING_GAP_VALUE	3
#define ZX279133_ODMA_ISU_SP_EN_BIT	BIT(0)
#define ZX279133_SPA_IPV6_CRC_MODE	0x8000
#define ZX279133_SPA_IPV6_CRC_BIT	BIT(31)
#define ZX279133_SPA_TCP_CTRL_MASK	GENMASK(19, 12)
#define ZX279133_SPA_TCP_CTRL_VALUE	0x7
#define ZX279133_SPA_TPID_BASE		0x8284
#define ZX279133_SPA_ONU_MAC_HI		0x8300
#define ZX279133_SPA_ONU_MAC_LO		0x8304
#define ZX279133_SPA_TRAP_ETH_TYPE	0x82e4
#define ZX279133_SPA_TRAP_DMAC_HI	0x8398
#define ZX279133_SPA_TRAP_DMAC_LO	0x839c
#define ZX279133_SPA_ONU_STRIDE		0x8
#define ZX279133_SPA_TRAP_DMAC_STRIDE	0x8
#define ZX279133_SPA_MAC_HI_VALUE	0xffffffff
#define ZX279133_SPA_MAC_LO_VALUE	0x7fff
#define ZX279133_SPA_MAC_LO_MASK	GENMASK(15, 0)
#define ZX279133_SPA_ONU_COUNT		16
/*
 * Registers 0..7 hold the MAC addresses the WAN accepts; 8..15 are the
 * factory's per-WAN local MACs (sw_mac_add_cpu_macaddress()), a different
 * table.
 */
#define ZX279133_SPA_ONU_MAC_SLOTS	8
#define ZX279133_SPA_TRAP_DMAC_COUNT	4
#define ZX279133_WANID_COUNT		32
#define ZX279133_WANID_CPU_MAC_COUNT	2
#define ZX279133_WANID_L3_MTU		1996
/* WANID bit 4 selects the downstream (WAN-to-LAN) half of the table. */
#define ZX279133_WANID_DOWNSTREAM	BIT(4)
#define ZX279133_WANID_PPPOE_PUSH	1
#define ZX279133_WANID_PPPOE_POP	2
#define ZX279133_GLB_OAM_EN		0x10
#define ZX279133_GLB_OAM_MASK		GENMASK(19, 14)
#define ZX279133_GLB_OAM_VALUE		0xf0000

#define ZX279133_SE_PARSER_RAM_DONE	0x40008
#define ZX279133_SE_PARSER_DEBUG_CFG	0x400c0
#define ZX279133_SE_PARSER_G988_CFG	0x400cc
#define ZX279133_SE_SMMU0_RAM_DONE	0x48084
#define ZX279133_SE_STAT_RUNTIME_CFG	0x58520
#define ZX279133_SE_SMMU1_HASH_BASE	0x44000
#define ZX279133_SE_ALG_HASH_CRC	0x500c8
#define ZX279133_SE_ALG_HASH_BULK	0x50208
#define ZX279133_SE_ALG_HASH_DEPTH0	0x50280
#define ZX279133_SE_ALG_HASH_DEPTH1	0x50284
#define ZX279133_SE_PARSER_RAM_MASK	GENMASK(4, 0)
#define ZX279133_SE_PARSER_DEBUG_MASK	GENMASK(1, 0)
#define ZX279133_SE_SMMU0_RAM_MASK	BIT(0)
#define ZX279133_SE_STAT_RUNTIME_MASK	GENMASK(7, 0)

#define ZX279133_PPU_AGCLK_CFG		0x1c
#define ZX279133_PPU_CORE_AGCLK		BIT(3)
#define ZX279133_PPU_REORDER_AGCLK	BIT(12)
#define ZX279133_PPU_CLUSTER_PC		0x90d00
#define ZX279133_PPU_CLUSTER_RDY	0x90d04
#define ZX279133_PPU_CLUSTER_INST_LO	0x90d0c
#define ZX279133_PPU_CLUSTER_INST_HI	0x90d10
#define ZX279133_PPU_CLUSTER_ME_TABLE	0x90350
#define ZX279133_PPU_CLUSTER_ME_WORDS	16
#define ZX279133_PPU_CLUSTER_PKT_TYPE_DETAIL	0x9045c
#define ZX279133_PPU_CLUSTER_PC_MASK	GENMASK(12, 0)
#define ZX279133_PPU_CLUSTER_HI_MASK	GENMASK(26, 0)
#define ZX279133_PPU_IKEY_AFULL		0x9043c
#define ZX279133_PPU_IKEY_AFULL_MASK	GENMASK(4, 0)
#define ZX279133_PPU_BLOCK_RDY		0x800d0
#define ZX279133_PPU_BLOCK_INDEX	0x800d4
#define ZX279133_PPU_BLOCK_DATA	0x800d8
#define ZX279133_PPU_BLOCK_INDEX_MASK	GENMASK(6, 0)
#define ZX279133_PPU_BLOCK_DATA_MASK	GENMASK(23, 0)
#define ZX279133_SPA_IND_CMD		0x8054
#define ZX279133_SPA_IND_STATUS	0x8058
#define ZX279133_SPA_IND_DATA		0x805c
/*
 * The SPA window itself: 1024 registers at 0x8000.  The vendor's 4.19
 * system reads the same window through /proc/pon/tm_debug/module_reg, so
 * the two can be compared register by register.
 */
#define ZX279133_SPA_WINDOW_BASE	0x8000
#define ZX279133_SPA_WINDOW_SIZE	0x1000
/*
 * The whole network processing block: the device tree maps it at
 * 0x19000000 with a size of 16 MiB, and every block in it is reachable
 * from the driver's own mapping - the SPA window at +0x8000, the XMAC0
 * at +0x140000, the XMAC1 at +0x180000, the IDM at +0x280000.  The
 * console's "reg" command uses this as its bound.
 */
#define ZX279133_NPPT_BASE		0x19000000
#define ZX279133_NPPT_WINDOW_SIZE	0x1000000
#define ZX279133_SMMU0_CMD		0x48004
#define ZX279133_SMMU0_ADDR		0x48008
#define ZX279133_SMMU0_WDATA		0x4800c
#define ZX279133_SMMU0_RAM_DONE	0x4801c
#define ZX279133_SMMU0_RDATA		0x48024
#define ZX279133_SMMU0_CMD_WRITE	0x8c000000
#define ZX279133_SMMU0_CMD_READ	0x0c000000
#define ZX279133_SMMU0_CMD_MASK	GENMASK(31, 11)
#define ZX279133_SMMU0_ADDR_MASK	GENMASK(27, 0)
#define ZX279133_SMMU0_RAM_MASK	GENMASK(1, 0)
#define ZX279133_SMMU0_WORDS		4
#define ZX279133_FAST_STAT_DEPTH	1024
#define ZX279133_FAST_AGE_DEPTH	BIT(18)
#define ZX279133_AGCLK_VALUE_MASK	GENMASK(12, 0)
#define ZX279133_SPA_AUTO_GATE		0xb8
#define ZX279133_SPA_AUTO_GATE_BIT	BIT(12)
#define ZX279133_SPA_IND_CMD_MASK	GENMASK(27, 0)
#define ZX279133_SPA_IND_READ		BIT(27)
#define ZX279133_MCODE_MAGIC		0xffffaaaa
#define ZX279133_MCODE_INST_NUM	12288
#define ZX279133_MCODE_TAG_PKT_TYPE	0xbdbdbdbd
#define ZX279133_MCODE_TAG_DUP		0xcccccccc
#define ZX279133_MCODE_TAG_SKIP		0xdddddddd
#define ZX279133_MCODE_TAG_VERSION	0xaaaaaaaa
#define ZX279133_MCODE_TAG_PORT_FLOW	0xbbbbbbbb
/*
 * The CRC32 of the PPU microcode this board runs.
 *
 * The blob in the tree is the vendor's own mcode_133.bin for this board.
 * The blob that used to be there was the SR1010's, and a PPU running the
 * SR1010's microcode does not forward frames: the datapath stalls inside
 * the PPU and the board receives nothing at all.  Nothing about that
 * failure looks like a firmware problem from the outside - the driver
 * loads the blob, the PPU reports a version, and the counters simply
 * stop - so the driver checks the CRC and says so.
 */
#define ZX279133_MCODE_BOARD_CRC	0xc839b946
/*
 * The CRC32 of the one blob the RX-hash hook may be installed on.
 *
 * The hook rewrites four instructions at an offset inside the blob's
 * instruction stream, so it only means anything in the blob it was
 * written against.  The vendor does not patch this board's blob either:
 * the vendor's PPU takes its hash from the SE hardware hash table.
 */
#define ZX279133_MCODE_RX_HASH_CRC	0xef1d7647

#define ZX279133_IDM_BASE		0x280000
#define ZX279133_IDM_TX_BASE		0x0004
#define ZX279133_IDM_TX_DOORBELL_COUNT_SHIFT	17
#define ZX279133_IDM_TX_QUEUES		4
#define ZX279133_IDM_TX_DEPTH		1024

/*
 * IDM exposes independent RX and CPU-TX descriptor base registers. Both
 * descriptor regions and the active normal RX free ring use DMA-coherent
 * allocations owned by the DMA API. Unused hardware free rings retain the
 * vendor reserved-memory layout.
 */
#define ZX279133_IDM_INT_MASK		0x0040
#define ZX279133_IDM_INT_CPU		0x0044
#define ZX279133_IDM_RX_RELEASE		0x0088
#define ZX279133_IDM_BP_REFILL		0x0100
/* Route half of each interface's queues to each RX interrupt source. */
/* Worker 0 retains native WAN queue 8; worker 1 retains native LAN queue 0. */
#define ZX279133_IDM_LOCAL_MASK		(GENMASK(11, 8) | GENMASK(7, 4))
#define ZX279133_IDM_DIRECT_RX_MASK	(GENMASK(15, 0) & \
					 ~ZX279133_IDM_LOCAL_MASK)
/*
 * Only the sources this driver has a handler for may be unmasked.
 *
 * The IDM's interrupt mask register (IDM+0x40) has one bit per source, and a
 * set bit means "masked".  The routing word (IDM+0x44) decides which source
 * reaches which interrupt line, and the vendor's own value for it is
 * 0x071ffbfb: it delivers sources 0,1,3,4,5,6,7,8,9,11,12,13,14,15 and also
 * 16,17,18,19,20,24,25,26 to the same line.
 *
 * The driver handles the two sets above - sources 0..15 - and nothing else.
 * Sources 16..26 must therefore stay masked, which is what the vendor does:
 * the vendor's own mask is 0x07ff0000, i.e. sources 16..26 masked and
 * sources 0..15 live, exactly the union of the two sets the driver owns.
 *
 * Unmasking them is not a small mistake.  A source that is asserted and
 * masked by nobody stays asserted, the line is level triggered, and the
 * handler re-enters forever: measured on the run-194 kit, the "rx" interrupt
 * (19000000.ethernet, /proc/interrupts line 22) fired 266 742 353 times in
 * half an hour while the driver's own rx_irq_count matched it to the last
 * one.  The CPU never got out of the handler, so the NET_RX softirq never
 * ran, so both NAPIs never polled a single descriptor
 * (rx_napi_0_polls = 0, rx_napi_1_polls = 0) - and the frames that were
 * sitting in the receive queues were never read.  Writing the vendor's
 * 0x07ff0000 stopped the storm at once: the interrupt count froze, the NAPIs
 * started polling, and the receive queues were drained.
 */
#define ZX279133_IDM_NAPI_MASK		GENMASK(15, 0)
#define ZX279133_IDM_UNHANDLED_MASK	GENMASK(26, 16)
#define ZX279133_IDM_RX_QUEUE		0
#define ZX279133_IDM_RX_QUEUES		24
#define ZX279133_IDM_CPU_RX_QUEUES	16
#define ZX279133_IDM_RX_RING_SIZE	1024
/*
 * The two buffer-pool rings the hardware expects, in the stock DT's units
 * (entries, four bytes each): idm_rx_normal_bp_ring_depth = 0x2000 and
 * idm_rx_jumbo_bp_ring_depth = 0x1000.  The hardware reads the depth out of
 * IDM+0x10c / IDM+0x110, so the ring has to be exactly this big.
 */
#define ZX279133_IDM_BP_RING_SIZE	8192
#define ZX279133_IDM_JUMBO_BP_RING_SIZE	4096
#define ZX279133_IDM_RX_BUFFER_COUNT	2048
#define ZX279133_IDM_RX_PAGE_MAP_BITS	12
#define ZX279133_IDM_RX_PAGE_MAP_SIZE	BIT(ZX279133_IDM_RX_PAGE_MAP_BITS)
#define ZX279133_IDM_RX_BUFFER_STRIDE	0x940
#define ZX279133_IDM_RX_BUFFER_SIZE	(ZX279133_IDM_RX_BUFFER_COUNT * \
					 ZX279133_IDM_RX_BUFFER_STRIDE)
#define ZX279133_IDM_RX_PAYLOAD_OFFSET	XDP_PACKET_HEADROOM
/*
 * Where the hardware puts the frame inside the buffer it was given.
 *
 * The descriptor's address is the *buffer*, and the frame is 128 bytes
 * further in: the switch engine keeps an area of its own in front of
 * the frame it has parsed, and the IDM copies the lot into the buffer
 * the driver posted.  The descriptor's length counts the frame only.
 *
 * Measured on this board (run 160's diagnostic, the frame read out of
 * the page the descriptor named): an ARP request and an IP/UDP frame
 * both landed at buffer + 0x80, and the bytes in front of them were
 * whatever the page had held before - zeros in one run, 0xff in
 * another.  So the driver read a buffer that had never been written,
 * and every frame it handed up was that buffer's leftovers.
 */
#define ZX279133_IDM_RX_FRAME_OFFSET	0x80
#define ZX279133_MAX_MTU		ZX279133_LAN_USER_MAX_MTU
#define ZX279133_IDM_RX_FRAME_LIMIT	(ZX279133_MAX_MTU + ETH_HLEN + \
					 VLAN_HLEN)
#define ZX279133_RX_PAGE_ORDER		0
#define ZX279133_RX_PAGE_SIZE		(PAGE_SIZE << ZX279133_RX_PAGE_ORDER)
#define ZX279133_RX_REFILL_RECOVERY_BATCH	64
#define ZX279133_RX_REFILL_RETRY_MS	100

/*
 * Vendor idm_init() free-ring layout after the legacy 0x1a0000-byte
 * descriptor region.  The stock DT's idm_free_ring_depth is 0x10000 entries,
 * so a free ring is 0x40000 bytes, and the vendor points IDM+0x118, IDM+0x11c
 * and IDM+0x408 at three of them, one 0x40000 bytes apart.
 */
#define ZX279133_IDM_FREE_RING_DEPTH	0x10000
#define ZX279133_IDM_FREE_RING_SIZE	0x40000
#define ZX279133_IDM_TX_FREE_RING_SIZE	0x40000
#define ZX279133_IDM_FREE_RING0	0x1a0000
#define ZX279133_IDM_FREE_RING1	(ZX279133_IDM_FREE_RING0 + \
					 ZX279133_IDM_FREE_RING_SIZE)
#define ZX279133_IDM_FREE_RING2	(ZX279133_IDM_FREE_RING1 + \
					 ZX279133_IDM_FREE_RING_SIZE)
#define ZX279133_IDM_FREE_RING3	(ZX279133_IDM_FREE_RING2 + \
					 ZX279133_IDM_FREE_RING_SIZE)
#define ZX279133_IDM_FREE_RING4	(ZX279133_IDM_FREE_RING3 + \
					 ZX279133_IDM_FREE_RING_SIZE)
#define ZX279133_IDM_REQUIRED_SIZE	(ZX279133_IDM_FREE_RING4 + \
					 ZX279133_IDM_TX_FREE_RING_SIZE)
#define ZX279133_IDM_TX_CONTROL		0x00400000

/*
 * Vendor kernel globals (kernel-2b5.elf rodata/BSS):
 * uNPPT_IDM_DESC_MODE = 0, uIDM_TX_CFG_DEPTH = 0 (no writer found).
 *
 * The two RX numbers are the ones this board's own np_133.ko uses and the
 * ones the hardware agrees with: the stock DT asks for idm_desc_rx_depth
 * = 0x400 descriptors per queue, the hardware's depth code for 0x400 is 0,
 * and IDM+0x070 holds the descriptor count minus the 24 queue headers.
 */
#define ZX279133_IDM_RX_QUEUE_DESC_DEPTH 1024
#define ZX279133_IDM_RX_CFG_DEPTH	0
#define ZX279133_IDM_DESC_MODE		0
#define ZX279133_IDM_TX_CFG_DEPTH	0x400

/*
 * Per-queue doorbells and completion counters, recovered from
 * idm_cpu_nb_tx_update() and idm_get_tx_done(): queue 0 uses 0x080/0x084,
 * queue n > 0 uses 4 * (n + 39) / 4 * (n + 42).
 */
static inline u32 zx279133_idm_tx_doorbell_reg(unsigned int queue)
{
	return queue == 0 ? 0x0080 : 4 * (queue + 39);
}

static inline u32 zx279133_idm_tx_done_reg(unsigned int queue)
{
	return queue == 0 ? 0x0084 : 4 * (queue + 42);
}

/* XMAC0 carries the LAN; the RTL8372N driver owns it. */
#define ZX279133_XMAC0_BASE	0x140000
#define ZX279133_XMAC1_BASE	0x180000
#define ZX279133_XMAC_TX_CTRL	0x0000
#define ZX279133_XMAC_RX_CTRL	0x0010
#define ZX279133_XMAC_RX_IPC	BIT(9)
#define ZX279133_XMAC_FRAME_CFG	0x0020
#define ZX279133_XMAC_MODE_CFG	0x0280
#define ZX279133_XMAC_DUPLEX	0x0500
#define ZX279133_XMAC_MISC_CFG	0x3400
#define ZX279133_XMAC_ENABLE	BIT(0)
#define ZX279133_XMAC_HALF_DUPLEX BIT(24)
#define ZX279133_XMAC_SPEED_MASK	GENMASK(31, 29)
#define ZX279133_XMAC_FLOW_CTRL	0x01c0
#define ZX279133_XMAC_TFE		BIT(1)
#define ZX279133_XMAC_PT_MASK		GENMASK(31, 16)
#define ZX279133_XMAC_PAUSE_TIME	0xffff
#define ZX279133_XMAC_RX_FLOW		0x0240
#define ZX279133_XMAC_RX_FLOW_EN	BIT(0)
#define ZX279133_XMAC1_RESET_MASK	BIT(11)
#define ZX279133_XMAC1_EEE_CTRL		(ZX279133_XMAC1_BASE + 0x0340)
#define ZX279133_XMAC1_EEE_TIMER	(ZX279133_XMAC1_BASE + 0x0350)
#define ZX279133_XMAC_EEE_ENABLE_MASK	0x000b0000
#define ZX279133_XMAC_EEE_TIMER_MASK	GENMASK(31, 16)
#define ZX279133_XMAC_EEE_TIMER_ENABLE	BIT(5)
#define ZX279133_XMAC_STAT_TX_FRAMES	0x2070
#define ZX279133_XMAC_STAT_TX_GOOD	0x2230
/* PTP/TOD registers are word-addressed as 0x704c..0x706a by np.ko. */
#define ZX279133_PTP_AGC_CFG		0x2c0000
#define ZX279133_PTP_AGC_WORK_CLOCKS	GENMASK(1, 0)
#define ZX279133_PTP_TOD_NS		0x1c130
#define ZX279133_PTP_TOD_SEC		0x1c134
#define ZX279133_PTP_CTRL		0x1c138
#define ZX279133_PTP_COMPARE		0x1c13c
#define ZX279133_PTP_UPDATE		0x1c148
#define ZX279133_PTP_UPDATE_SEC		0x1c14c
#define ZX279133_PTP_UPDATE_NS		0x1c150
#define ZX279133_PTP_UPDATE_ENABLE	BIT(4)
#define ZX279133_PTP_COMPARE_TIME	GENMASK(31, 2)
#define ZX279133_PTP_COMPARE_MODE	GENMASK(1, 0)
#define ZX279133_PTP_UPDATE_SIGN	BIT(2)
#define ZX279133_PTP_UPDATE_PULSE	BIT(1)
#define ZX279133_PTP_UPDATE_STATUS	BIT(0)
#define ZX279133_PTP_NOMINAL_COMPARE	(NSEC_PER_SEC - 8)
#define ZX279133_NPPT_XMAC_ERR		0x00fc
#define ZX279133_SMCT_DONE		0xc0f0
#define ZX279133_SSCH5			0x2c054
#define ZX279133_SOPC_RR5		0x340bc
#define ZX279133_SOPC_TO_SMAC5		0x34108
#define ZX279133_SOPC_REQ_SMAC5		0x34134
#define ZX279133_XMAC1_SOPC_READY	0x342a8
#define ZX279133_XMAC1_SOPC_SEND_ENABLE	0x342c4
#define ZX279133_XMAC1_SOPC_DUPLEX_MASK	BIT(5)
#define ZX279133_XMAC1_SGMII_SPEED	3
/*
 * The same window seen from the other side: XMAC0.
 *
 * docs/92 36 is why these exist. The vendor CLI's own memory table
 * (spa_port_attr_op help, np.c:2347) reads "1..4 = SMAC0..3 (the four UNI
 * jacks)  5 = XMAC0  6 = XMAC1", so the four working jacks are the SoC's own
 * GE MACs and XMAC0 is not what carries them - and in the one boot that
 * proved this lane carrying traffic, XMAC0's window counted while XMAC1's
 * read all zero (docs/92 14). The 2.5G jack's RTL8226 is therefore XMAC0's
 * outerphy: np_133.ko's xmac0_to_extend branch is the call
 * port_attr_def_src_is_extend_port_flag_set(5, 1).
 */
#define ZX279133_XMAC0_SOPC_READY	0x342a4
#define ZX279133_XMAC0_SOPC_SEND_ENABLE	0x342c0
#define ZX279133_XMAC0_SOPC_DUPLEX_MASK	BIT(4)
#define ZX279133_XMAC0_RESET_MASK	BIT(10)
#define ZX279133_XMAC_EEE_CTRL_OFF	0x0340
#define ZX279133_XMAC_EEE_TIMER_OFF	0x0350

/* The window eth0's MAC block is in - see zx279133_eth0_xmac in mac.c. */
u32 zx279133_xmac_base(void);
u32 zx279133_sopc_ready(void);
u32 zx279133_sopc_send_enable(void);

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
/*
 * The shared soft-reset word at NPPT + 0x2c0004 carries one bit per block;
 * the vendor's smac_reset() pulses bit(mac) to reset that MAC and nothing
 * else, so the XMAC and the PCS are not disturbed by it.
 *
 * This word used to be spelled 0x2c004 here - sixteen times too low - while
 * the comment above it, the vendor's U-Boot (which prints the absolute
 * address 0x192c0004) and the rest of this driver (ZX279133_SYS_SOFT_RESET,
 * used by tm.c, mac.c and the XMAC0 reset) all said 0x2c0004.  So the pulse
 * went to an address that is not the register: the four internal-PHY MACs
 * were never reset, their "clear-over-ready" word stayed 0, none of them
 * was ever published, and no frame from a LAN jack was ever received.
 */
#define ZX279133_SMAC_RESET_WORD	ZX279133_SYS_SOFT_RESET
/*
 * The MAC's packet-filter word, at +0x08 of its block.  np_133.ko writes
 * 0x80000001 there, twice, after the reset pulse.
 *
 * This driver used to skip the write because on this board the hardware
 * derived the word itself and writing over it "destroyed" it: the word read
 * back 0x80000000 instead of 0x80000001.  That is not destruction, it is a
 * write-one-to-clear bit - the same thing happens to the speed word's bit 12
 * - and the vendor's own board ends up with the same value, because the
 * vendor's dump of the block was taken before its Linux driver wrote it.
 * So the vendor's write is made here as well, and the read-back is printed.
 */
#define ZX279133_SMAC_PKT_FILTER_VALUE	0x80000001
/*
 * The MAC's speed/duplex word, at +0xe0 of its block.  np_133.ko writes
 * 0x00011200 there - the word the hardware already holds (0x00011000) with
 * bit 9 set - after the reset pulse.
 *
 * The value used to be spelled 0x00012000 here, which is not what the vendor
 * writes: bit 9 and bit 12 differ.  The experiments that "proved" the word
 * must not be written used that wrong constant.
 */
#define ZX279133_SMAC_SPEED_VALUE	0x00011200
#define ZX279133_SMAC_PORT_CTRL_BIT	0x200
#define ZX279133_SMAC_UNI_MODE_MASK	0x03800000

#define ZX279133_XPCS_BYPASS_REG	0x8005
#define ZX279133_XPCS_BYPASS_EN	BIT(4)

struct zx279133_idm_desc {
	__le32 address;
	__le32 length_flags;
	__le32 metadata[6];
};

struct zx279133_tx_slot {
	struct sk_buff *skb;
	struct xdp_frame *xdpf;
	struct xdp_buff *xsk_rx;
	struct net_device *ndev;
	dma_addr_t dma;
	u32 len;
	bool dma_mapped;
	bool xsk_tx;
};

struct zx279133_tx_ring {
	struct zx279133_tx_slot *slots;
	u16 done;
	u16 producer;
	u16 consumer;
	u16 pending;
	u16 notify_pending;
	u64 submitted;
	u64 completed;
};

struct zx279133_rx_page_entry {
	union {
		struct page *page;
		struct xdp_buff *xdp;
	};
	u32 key;
	bool xsk;
};

static_assert(sizeof(struct zx279133_idm_desc) == 32);
static_assert(ZX279133_BMU_BPPE_SIZE +
	      ZX279133_BMU_NORMAL_COUNT * ZX279133_BMU_NORMAL_SIZE +
	      ZX279133_BMU_JUMBO_COUNT * ZX279133_BMU_JUMBO_SIZE +
	      ZX279133_BMU_DESC_SIZE == ZX279133_BMU_REQUIRED_SIZE);

struct zx279133_eth;
struct zx279133_flow_offload;

struct zx279133_rx_stats {
	struct u64_stats_sync syncp;
	u64_stats_t rx_napi_polls;
	u64_stats_t rx_napi_work;
	u64_stats_t rx_queue_descs[ZX279133_IDM_CPU_RX_QUEUES];
	u64_stats_t rx_napi_budget_exhaustions;
	u64_stats_t rx_desc_not_ready;
	u64_stats_t rx_invalid_dma;
	u64_stats_t rx_page_lookup_misses;
	u64_stats_t rx_jumbo_drops;
	u64_stats_t rx_descriptor_flag_drops;
	u64_stats_t rx_page_alloc_failures;
	u64_stats_t rx_copy_fallbacks;
	u64_stats_t rx_skb_alloc_failures;
	u64_stats_t rx_hw_csum_packets;
	u64_stats_t rx_hw_hash_packets;
	u64_stats_t rx_refill_post_failures;
	u64_stats_t rx_refill_shortfalls;
	u64_stats_t rx_refill_published;
	u64_stats_t rx_release_published;
	u64_stats_t rx_refill_recovery_attempts;
	u64_stats_t rx_refill_recovery_pages;
	u64_stats_t rx_refill_recovery_failures;
	u64_stats_t xdp_pass;
	u64_stats_t xdp_drop;
	u64_stats_t xdp_tx;
	u64_stats_t xdp_redirect;
	u64_stats_t xdp_aborted;
	u64_stats_t cpu_polls[2];
	u64_stats_t overlaps;
	u64_stats_t foreign_pages;
};

/*
 * What a flow needs from the WANID entry its fast response selects: the
 * source MAC of every frame the flow sends, the source-NAT address (0 when
 * the flow does not source-NAT), and the PPPoE rewrite with its session ID.
 */
struct zx279133_wanid_profile {
	u8 mac[ETH_ALEN];
	u32 sip;
	u16 sid;
	u8 pppoe;
};

/*
 * One WANID entry lent to flows that share a profile. The entry's own image
 * is saved on first use and written back when the last flow returns it.
 */
struct zx279133_wanid_slot {
	struct zx279133_wanid_profile profile;
	u32 saved[ZX279133_SMMU0_WORDS];
	unsigned int users;
	unsigned int snat_users;
};

struct zx279133_eth {
	struct device *dev;
	struct net_device *ndev;
	struct net_device *lan_ndev;
	/*
	 * LAN ports switched to WAN-type in the PPU, one bit per transport VID
	 * from ZX279133_LAN_TRANSPORT_VID_MIN; written under datapath_lock.
	 */
	unsigned long lan_wan_vports;
	/*
	 * The port id the IDM TX descriptor names for a LAN frame that carries
	 * no transport VID, and the value the debugfs "txp" command turns.
	 *
	 * The descriptor's byte 0x0a holds the output port in bits 0..5 and the
	 * "output port valid" flag in bit 6 (the vendor's own dump_desc_tx()
	 * names both).  The vendor's LAN path writes the *target UNI jack's*
	 * chip port there - 1..4 - and never 5, which is xmac_0, a 10G SerDes
	 * MAC that has no peer on this board and that the vendor never
	 * programs.  A frame the DSA tagger has tagged does not come here: it
	 * names its own jack, and the tag is what zx279133_lan_netdev_xmit()
	 * reads.  This field is only the fallback, and measured on this bench
	 * as the single value that decided all four jacks while it was the only
	 * one there was: with it compiled as 2, only one jack answered.
	 */
	u8 lan_tx_port;
	/*
	 * The fourth metadata word of the IDM TX descriptor, for the LAN path.
	 *
	 * The vendor's own dump_desc_tx() calls this word the GEM port's LLID
	 * id plus its queue id.  The vendor's LAN path writes 0x003e0000 there
	 * and its PON path writes 0x1f568623; this driver used to write
	 * zx279133_tx_pon_control (0x08000000) on both, which is a PON
	 * control word in a LAN descriptor.  The debugfs "tx4" command can
	 * still turn it while the question is open.
	 */
	u32 lan_tx_word4;
	/*
	 * Whether the 9132's four UNI ports get their untag SVID (SPA
	 * register 2, bits 18..29) set to the transport VID their DSA port
	 * carries.  The vendor's own system leaves the SVID at 0, because the
	 * vendor's system never tags: the DSA tagger on this driver's conduit
	 * does, and a tagged frame that leaves the chip tagged is a frame the
	 * client cannot use.
	 */
	bool lan_untag_svid;
	struct zx279133_flow_offload *flow_offload;
	struct zx279133_lan_service lan_service;
	struct zx279133_netdev_stats stats;
	void __iomem *base;
	void __iomem *pps_base;
	struct regmap *pon_route;
	struct regmap *idm_cci;
	phys_addr_t bmu_base;
	phys_addr_t bmu_size;
	void __iomem *bmu_mem;
	phys_addr_t se_hash_base;
	phys_addr_t se_hash_size;
	void __iomem *se_hash_mem;
	phys_addr_t idm_base;
	phys_addr_t idm_size;
	void __iomem *idm_mem;
	struct page_pool *rx_page_pool;
	struct page_pool *lan_rx_page_pool;
	struct xdp_rxq_info xdp_rxq[ZX279133_CPU_RX_QUEUES];
	struct xdp_rxq_info xsk_rxq;
	struct xsk_buff_pool *xsk_pool;
	struct bpf_prog __rcu *xdp_prog;
	struct zx279133_rx_page_entry *rx_page_map;
	u16 rx_page_map_count;
	u32 idm_cci_saved[2];
	u32 sipc_saved[2];
	u32 idm_cfg_saved[41];
	u32 greg_saved[10];
	u32 dma_saved[5];
	u32 bmu_saved[18];
	u32 qmg_saved[7];
	u32 sopc_saved[2];
	u32 red_saved[13];
	u32 red_np1_saved[14];
	u32 ssch_saved[4];
	u32 wsch_saved[2];
	u32 woe_sch_saved[2];
	u32 usch_saved[2];
	u32 uopc_saved[14];
	u32 se_parser_debug_saved;
	u32 se_hash_smmu1_saved[8];
	u32 se_hash_alg_crc_saved;
	u32 se_hash_alg_bulk_saved;
	u32 se_hash_alg_depth_saved[2];
	u32 se_hash_id_map[4];
	u32 se_multi_idx_map;
	u32 smct_saved[18];
	u32 sdet_saved[2];
	u32 nppu_early_saved[5];
	u32 nppu_late_saved[2];
	u32 ppu_cluster_me_saved[ZX279133_PPU_CLUSTER_ME_WORDS];
	u32 ppu_cluster_pkt_type_saved;
	u32 spa_saved[47];
	struct clk_bulk_data clocks[ZX279133_NUM_CLOCKS];
	struct phy *serdes;
	struct dw_xpcs *xpcs;
	struct mdio_device *xpcs_mdiodev;
	/*
	 * The second PCS on the die: XPCS0, the LAN lane's, from
	 * "zte,lan-pcs-handle".  eth0's MAC block can be pointed at XMAC0 with
	 * zx279133_eth0_xmac (mac.c), and a MAC without its own PCS is not the
	 * same experiment as a MAC with it - docs/92 41 measured the pair half
	 * done (MAC moved, PCS left on XPCS1) and got zero frames in both
	 * directions, so this field is what completes that test.
	 */
	struct dw_xpcs *xpcs_lan;
	struct mdio_device *xpcs_lan_mdiodev;
	struct phylink_config phylink_config;
	struct phylink *phylink;
	phy_interface_t host_interface;
	phy_interface_t serdes_interface;
	struct zx279133_idm_desc *rx_descs;
	struct zx279133_idm_desc *tx_descs;
	__be32 *rx_normal_bp;
	__be32 *rx_jumbo_bp;
	dma_addr_t rx_descs_dma;
	dma_addr_t rx_normal_bp_dma;
	dma_addr_t rx_jumbo_bp_dma;
	dma_addr_t tx_descs_dma;
	u16 rx_cons[ZX279133_IDM_RX_QUEUES];
	u16 rx_bp_prod;
	u8 rx_poll_cursor[2];
	u16 rx_refill_pending;
	/* Set by the debugfs "rxb" command: print the next frame's bytes. */
	unsigned int rx_dump;
	/*
	 * Where the frame the "rxb" command printed is, so that the "rxu"
	 * command can read the same bytes without the CPU's cache in the
	 * way.
	 */
	dma_addr_t rx_dump_dma;
	u32 rx_dump_len;
	/* Serializes the shared DMA map, supply ring and refill credits. */
	spinlock_t rx_buffer_lock;
	atomic_t rx_poll_active;
	struct zx279133_tx_ring tx[ZX279133_CPU_TX_QUEUES];
	u32 idm_tx_base_saved;
	u64 tx_doorbell_writes;
	u64 tx_doorbell_descs;
	u64 tx_reclaim_polls;
	u64 tx_reclaim_work_runs;
	u64 tx_reclaim_work_packets;
	u64 tx_timeouts;
	u64 tx_timeout_recoveries;
	u64 tx_timeout_stalls;
	u64 tx_hw_csum_packets;
	u64 tx_sw_csum_packets;
	atomic64_t rx_irq_count;
	atomic64_t idm_local_irq_count;
	/*
	 * Frames that arrived on the 2.5G jack's lane (the IDM's source port
	 * 5).  They used to be counted and dropped: there was no DSA user
	 * port for that lane yet, and bridging them sent them back out of
	 * XMAC0 - which is the same lane they came in on (docs/92 §72).  The
	 * jack has a DSA user port now (lan5, DSA port 8, transport VID 63),
	 * so they are counted here and handed to the conduit (docs/92 §90 and
	 * §91).
	 *
	 * rx_jack_dropped only moves when the DSA path is *not* there, so it
	 * is the counter that says whether the RX path hands the frames over
	 * or throws them away: a "dropped" that stands still while "frames"
	 * moves is the result the RX path is supposed to give.
	 */
	atomic64_t rx_jack_frames;
	atomic64_t rx_jack_dropped;
	struct zx279133_rx_stats rx_stats[2];
	atomic64_t rx_refill_retry_work_runs;
	u16 rx_refill_deficit;
	u16 rx_refill_deficit_high_water;
	u16 rx_page_map_high_water;
	/* Serializes TX ring ownership against NAPI completion. */
	spinlock_t tx_lock;
	/* Serializes RX and local interrupt enable state. */
	spinlock_t irq_lock;
	/* Serializes shared XMAC reset and SOPC state with the LAN child. */
	struct mutex xmac_lock;
	/* Serializes ownership of the shared NPPT/IDM datapath. */
	struct mutex datapath_lock;
	/* Serializes multi-register NP indirect table accesses. */
	struct mutex np_ind_lock;
	/* Serializes read-modify-write of shared WANID table entries. */
	struct mutex wanid_lock;
	struct zx279133_wanid_slot wanid[ZX279133_WANID_COUNT];
	struct napi_struct napi;
	struct napi_struct lan_napi;
	struct delayed_work tx_reclaim_work;
	struct delayed_work rx_refill_work;
	/* Copies the WAN unicast list (macvlans) into the SPA ONU MAC slots. */
	struct work_struct rx_mode_work;
	/*
	 * The vendor's pon_plat puts the optical RX signal detect on gpio2
	 * pin 1.  The work polls it and drives the red LOS lamp: low means
	 * loss of signal, high means light present (docs/91).
	 */
	struct delayed_work pon_led_work;
	struct gpio_desc *opsd_gpio;
	struct led_classdev *los_led;
	struct ptp_clock_info ptp_info;
	struct ptp_clock *ptp_clock;
	u32 ptp_agc_saved;
	/* Serializes the split PPS/TOD seconds and nanoseconds registers. */
	spinlock_t ptp_lock;
	/* Serializes the PPS/TOD update command and its synchronized pulse. */
	struct mutex ptp_cmd_lock;
	unsigned long datapath_users;
	bool hardware_prepared;
	bool rx_hash_active;
	bool rx_hash_ipv6_active;
	u32 rx_hash_loads;
	u8 rx_rss_map[2]; /* WAN, LAN: each bit selects one Linux RX queue. */
	bool lan_datapath_ready;
	bool lan_dsa_active;
	bool serdes_powered;
	bool np_reset_prepared;
	/*
	 * Set once the NPPT has been brought up and the 9132 has been
	 * configured in it.  A second prepare must not reset the NPPT
	 * again: the reset wipes the 9132's tables and the four port MACs
	 * the switch driver programmed, and the switch driver does not run
	 * a second time.
	 */
	bool nppt_ready;
	bool greg_prepared;
	bool dma_prepared;
	bool bmu_prepared;
	bool qmg_prepared;
	bool sopc_prepared;
	bool red_prepared;
	bool ssch_prepared;
	bool wsch_prepared;
	bool woe_sch_prepared;
	bool usch_prepared;
	bool uopc_prepared;
	bool se_frontend_prepared;
	bool se_hash_prepared;
	bool se_age_done;
	bool ppu_mcode_prepared;
	bool spa_prepared;
	bool nppu_early_prepared;
	bool smct_prepared;
	bool nppu_late_prepared;
	bool ppu_cluster_runtime_prepared;
	bool sdet_prepared;
	bool sipc_final_prepared;
	bool tx_prepared;
	bool rx_prepared;
	bool rx_running;
	bool rx_irq_enabled;
	bool idm_local_irq_enabled;
	bool napi_enabled;
	bool xpcs_runtime_held;
	bool tx_stopping;
	bool ptp_agc_prepared;
	int irqs[ZX279133_NUM_IRQS];
};

static inline struct zx279133_netdev_stats *
zx279133_netdev_stats(struct zx279133_eth *eth, struct net_device *ndev)
{
	if (ndev == eth->ndev)
		return &eth->stats;

	return &((struct zx279133_lan_netdev_priv *)netdev_priv(ndev))->stats;
}

static inline void
zx279133_stats_rx_error(struct zx279133_eth *eth, struct net_device *ndev)
{
	atomic64_inc(&zx279133_netdev_stats(eth, ndev)->rx_errors);
}

static inline void
zx279133_stats_tx_error(struct zx279133_eth *eth, struct net_device *ndev)
{
	atomic64_inc(&zx279133_netdev_stats(eth, ndev)->tx_errors);
}

static inline void
zx279133_stats_rx_dropped(struct zx279133_eth *eth, struct net_device *ndev)
{
	atomic64_inc(&zx279133_netdev_stats(eth, ndev)->rx_dropped);
}

static inline void
zx279133_stats_tx_dropped(struct zx279133_eth *eth, struct net_device *ndev)
{
	atomic64_inc(&zx279133_netdev_stats(eth, ndev)->tx_dropped);
}

static inline void
zx279133_stats_rx_length_error(struct zx279133_eth *eth, struct net_device *ndev)
{
	atomic64_inc(&zx279133_netdev_stats(eth, ndev)->rx_length_errors);
}

int zx279133_tm_prepare(struct zx279133_eth *eth);
void zx279133_tm_restore(struct zx279133_eth *eth);
void zx279133_red_ram_write(struct zx279133_eth *eth, u32 ram_id, u32 addr,
			    const u32 *data, unsigned int count);
void zx279133_red_ram_read(struct zx279133_eth *eth, u32 ram_id, u32 addr,
			   u32 *data, unsigned int count);
int zx279133_np_prepare(struct zx279133_eth *eth);
void zx279133_np_restore(struct zx279133_eth *eth);
void zx279133_route_set(struct zx279133_eth *eth, bool enabled);
int zx279133_vlan_runtime_prepare(struct zx279133_eth *eth);
int zx279133_wan_port_bringup(struct zx279133_eth *eth);
void zx279133_program_spa_uc_macs(struct zx279133_eth *eth);
int zx279133_lan_vport_wan_set(struct zx279133_eth *eth, u16 vport, bool wan);
void zx279133_rx_mode_work(struct work_struct *work);
void zx279133_program_spa_cpu_mac(struct zx279133_eth *eth,
				  const unsigned char *addr);
int zx279133_program_wanid_cpu_mac(struct zx279133_eth *eth,
				   const unsigned char *addr);
int zx279133_wanid_get(struct zx279133_eth *eth, bool downstream,
		       const struct zx279133_wanid_profile *profile, u8 *wanid);
void zx279133_wanid_put(struct zx279133_eth *eth, u8 wanid, bool snat);
#ifdef CONFIG_DEBUG_FS
int zx279133_wanid_debugfs_init(struct zx279133_eth *eth);
int zx279133_9132_debugfs_init(struct zx279133_eth *eth);
#else
static inline int zx279133_wanid_debugfs_init(struct zx279133_eth *eth)
{
	return 0;
}
static inline int zx279133_9132_debugfs_init(struct zx279133_eth *eth)
{
	return 0;
}
#endif
int zx279133_fast_ikey_write(struct zx279133_eth *eth, u32 index,
			     const u32 *data);
int zx279133_fast_stats_read(struct zx279133_eth *eth, u16 flow_id,
			     u64 *packets, u64 *bytes);
int zx279133_fast_age_read_clear(struct zx279133_eth *eth, u32 age,
				 bool *used);
int zx279133_flow_offload_init(struct zx279133_eth *eth);
void zx279133_flow_offload_flush(struct zx279133_eth *eth);
void zx279133_flow_offload_reset(struct zx279133_eth *eth);
bool zx279133_flow_offload_active(struct zx279133_eth *eth);
int zx279133_flow_offload_setup_tc(struct zx279133_eth *eth,
				   struct net_device *ndev,
				   enum tc_setup_type type, void *type_data);

void zx279133_idm_set_masked(struct zx279133_eth *eth, u32 mask, bool masked);
unsigned int zx279133_idm_tx_reclaim_locked(struct zx279133_eth *eth);
int zx279133_xdp_enqueue(struct zx279133_eth *eth, struct xdp_frame *xdpf);
int zx279133_xsk_rx_enqueue(struct zx279133_eth *eth, struct xdp_buff *xdp);
void zx279133_xdp_flush(struct zx279133_eth *eth);
void zx279133_xsk_tx(struct zx279133_eth *eth);
void zx279133_idm_rx_refill_work(struct work_struct *work);
int zx279133_idm_rx_poll(struct napi_struct *napi, int budget);
int zx279133_idm_lan_rx_poll(struct napi_struct *napi, int budget);
irqreturn_t zx279133_idm_rx_irq(int irq, void *data);
irqreturn_t zx279133_idm_local_irq(int irq, void *data);
int zx279133_idm_rx_prepare(struct zx279133_eth *eth);
void zx279133_idm_rx_release(struct zx279133_eth *eth);
void zx279133_idm_tx_flush_locked(struct zx279133_eth *eth);
void zx279133_idm_tx_reclaim_work(struct work_struct *work);
bool zx279133_idm_tx_drain(struct zx279133_eth *eth);
int zx279133_idm_tx_prepare(struct zx279133_eth *eth);
void zx279133_idm_tx_deactivate(struct zx279133_eth *eth);
void zx279133_idm_tx_release(struct zx279133_eth *eth, bool hardware_alive);
void zx279133_idm_tx_flush_queue_locked(struct zx279133_eth *eth,
					unsigned int queue);
unsigned int zx279133_idm_tx_reclaim_queue_locked(struct zx279133_eth *eth,
						  unsigned int queue);

static inline unsigned int zx279133_idm_tx_pending(struct zx279133_eth *eth)
{
	unsigned int pending = 0;
	int i;

	for (i = 0; i < ZX279133_CPU_TX_QUEUES; i++)
		pending += eth->tx[i].pending;
	return pending;
}

void zx279133_xmac_set_enabled(struct zx279133_eth *eth, bool enabled);
int zx279133_xpcs_set_bypass(struct zx279133_eth *eth, bool enabled);
extern const struct phylink_mac_ops zx279133_phylink_ops;

struct led_classdev;
void zx279133_pon_set_led(struct led_classdev *led);
void zx279133_pon_note_rxsd(int light);
void zx279133_pon_carrier_kick(struct net_device *ndev);
void zx279133_pon_attach(struct net_device *ndev);
void zx279133_pon_stop(void);

int zx279133_ptp_init(struct zx279133_eth *eth);
void zx279133_ptp_start(struct zx279133_eth *eth);
void zx279133_ptp_stop(struct zx279133_eth *eth);
int zx279133_get_ts_info(struct net_device *ndev,
			 struct kernel_ethtool_ts_info *info);

int zx279133_hardware_prepare(struct zx279133_eth *eth);
void zx279133_hardware_unprepare(struct zx279133_eth *eth);
void zx279133_nppt_vendor_reconfigure(struct zx279133_eth *eth);
void zx279133_shutdown(struct zx279133_eth *eth);
extern const struct ethtool_ops zx279133_ethtool_ops;
extern const struct net_device_ops zx279133_netdev_ops;
extern const struct zx279133_lan_service_ops zx279133_lan_service_ops;

#endif /* __ZX279133_H__ */
