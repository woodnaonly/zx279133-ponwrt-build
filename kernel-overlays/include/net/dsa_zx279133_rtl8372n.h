/* SPDX-License-Identifier: GPL-2.0-only */
#ifndef _NET_DSA_ZX279133_RTL8372N_H
#define _NET_DSA_ZX279133_RTL8372N_H

/*
 * The tree's copy of this header, with one number changed.
 *
 * The tagger (net/dsa/tag_zx279133_rtl8372n.c) accepts a frame's transport
 * VID only if it lies between TRANSPORT_VID_BASE + USER_PORT_MIN and
 * TRANSPORT_VID_BASE + USER_PORT_MAX, and the tree's own SR1010 has the four
 * user ports 4..7 - so the tree's USER_PORT_MAX is 7 and the largest VID the
 * tagger will look at is 62.
 *
 * The 9132 on SK-D840N has a fifth user port: the 2.5G jack is the 9132's
 * fifth UNI port (the vendor's outerphy, PPU 5) and it is DSA port 8, so it
 * carries the transport VID 55 + 8 = 63 (docs/92 §90).  A VID of 63 is
 * outside the tree's range, so the tagger would refuse every frame that came
 * in on the 2.5G jack and drop it: the frame would arrive with the right tag
 * and leave with nothing.
 *
 * Raising USER_PORT_MAX to 8 is the whole of the change.  It widens the range
 * the tagger accepts by exactly one VID, and the tagger's transmit side is
 * already generic (it writes BASE + dp->index), so the port number itself is
 * never spelled out.  On the SR1010 nothing changes: its ports are 4..7, so
 * the tagger still finds no user port for a VID of 63 and drops such a frame
 * exactly as it did before - and the tree's own four ports are untouched.
 */
#define ZX279133_RTL8372N_USER_PORT_MIN	4
#define ZX279133_RTL8372N_USER_PORT_MAX	8
#define ZX279133_RTL8372N_TRANSPORT_VID_BASE	55

#endif /* _NET_DSA_ZX279133_RTL8372N_H */
