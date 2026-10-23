# What the board said once the PC was really talking to it

*2026-10-23, rounds 139-142. All of this is measured on the SK-D840N; every
number in it was read off the board or off the PC, not inferred.*

## 1. The three false leads

Three plausible causes were chased and each one turned out to be a red
herring. They are written down here so that nobody spends a day on them
again.

### 1.1 The flood group table was never empty

Round 139 read ERAM table 0x1f at entries 0, 1 and 15 and got 0 for all
three, which looked like "the broadcast and unknown-unicast floods have
nowhere to go, so the CPU never sees an ARP request".

It was a wrong index. The group id of a port p is p<<6, (p+0x10)<<6 and
(p+0x20)<<6 - the driver writes exactly those - so the entries that matter
for the four UNI jacks are 64, 80, 96, 128, 144, 160, 192, 208, 224, 256,
272 and 288. Read on the live board:

    eram table 0x1f entry 64   width 32 = 0xf
    eram table 0x1f entry 128  width 32 = 0xf
    eram table 0x1f entry 1152 width 32 = 0xf
    eram table 0x1f entry 2176 width 32 = 0xf

0xf is 15, the CPU port. Every flood group of every UNI jack points at the
CPU port, which is what the driver intends and the opposite of what the
vendor's own board does (the vendor's groups point at the WAN port, which
is why a frame addressed to the board itself is dropped by the 9132).

The protocol action table is right as well: ERAM 0x18 entry 273 (protocol 17,
ARP, with source port 2 shifted in) reads 0x2, which is "trap to the CPU".

**So neither the flood path nor the ARP trap was the problem.**

### 1.2 The DSA CPU port really was missing from the bridge

`lan-cpu0` was not in `br-lan`, and the kernel refused to add it
(`RTNETLINK answers: Invalid argument`) while it accepted the same
operation on a bridge this driver created itself. That was a real defect
and it is fixed (run 136: `02_network` and `S26nat` both keep `lan-cpu0` in
the LAN device list), but it is not what stopped the traffic: the frames
never reached the CPU in the first place.

### 1.3 The XMAC0 was the right MAC, after all

An early reading of the vendor's code suggested that XMAC0 might be a
SoC-side GMAC for an external RTL8226 and that the 9132's CPU port was
somewhere else. The vendor's own device tree names settle it:

  * `zte,zx279133-xmac0-pcs` is the node the vendor's own module maps as
    `xmac0_pcs_base` - that is XPCS0, and XPCS0 is XMAC0's PCS.
  * `zte,zx279133-pon_serdes` is the PON side, and the PON is routed to
    XMAC1 (`ponserdes_to_xmac1_en_set`).
  * The `sw` and `pon` names in `g_mii_dev_name` are not MACs at all; they
    are the two IDM/DMA netdevs the vendor's `idm_net_init` creates.
  * The live board reads XPCS0 status 0x380b, whose low three bits are the
    speed code 3 - 1G, and the vendor's U-Boot says the same out loud:
    "xmac 0 speed set 2 in sgmii(xmac speed 3)".

**XMAC0 is the lane to the 9132's CPU port, and XMAC1 is the PON side.**
The architecture the driver was built on is the right one.

## 2. The reference implementation

At 13:57 the smart plug let the board boot from flash, and what came up was
the vendor's own system: OpenWrt 24.10.8 with `np_133.ko` and `switch.ko`
in the kernel. It was on `192.168.9.1/24` on `br-lan`, and it answered the
PC's ping in 1 ms:

    Reply from 192.168.9.1: bytes=32 time=1ms TTL=64

The PC's cable is in the second UNI jack - the vendor's boot says
"mac 1 link up / mac 1 phy status changed: 1000M full-duplex" while macs 0,
2, 3 and 4 are down - and with the vendor's driver that jack carries
traffic.

**So the bench, the cable, the PHY, the 9132's forwarding and the CPU's IP
stack are all fine. Everything that was broken was in this driver.** The
vendor's system was also reachable over SSH with the lab key, which made it
a live oracle: its `dmesg` (captured as
`staging/ctl/vendor-dmesg-20261023.txt`) is the vendor's own init sequence,
side by side with ours.

## 3. The actual root cause

The vendor's Linux prints its `smac_init` for each of the four internal-PHY
MACs:

    enter smac_init
    smac_init: smac_pkt_filter = 0x80000001, mac = 0x0
    write smac an ctrl ,mac = 0x0
    read SOPC_CLR_OVER_READY_SMAC = 0x1, mac = 0x0
    write SOPC_SEND_EN_CFG_SMAC,mac = 0x0
    sopc send enable ,mac = 0x0
    exit smac_init:mac=0x0

and before each one it pulses a shared soft-reset word, once per MAC:

    val =0xffffffff, reg = 0x192c0004
    reset val =0xfffffffe        (bit 0 cleared for mac 0, bit 1 for mac 1, ...)
    restore val = 0xffffffff

0x192c0004 is NPPT + **0x2c0004**. Our driver pulsed NPPT + **0x2c004** -
sixteen times lower. The comment above the constant in `zx279133.h` said
0x2c0004; the value below it said 0x2c004. The rest of the driver -
`tm.c`, `mac.c` and the XMAC0 reset in `switch.c` - all use
`ZX279133_SYS_SOFT_RESET`, which is 0x2c0004, so the SMAC pulse was the
only one that went nowhere.

What that costs is visible on the board: our kernel prints

    SMAC 0 programmed: an_ctrl=0x00fa2203 pkt_filter=0x80000001 speed=0x00011000 ready=0x00000000
    SMAC 1 programmed: ... ready=0x00000000
    SMAC 2 programmed: ... ready=0x00000000
    SMAC 3 programmed: ... ready=0x00000000

while the vendor reads 0x1 for all four. A MAC that never became ready is
never published, so it never takes a frame off its PHY - and that is
exactly what was measured: every physical port's RX counter sat at zero
while the PC's TX bytes climbed.

### 3.1 Where the ready word lives - settled

Two sources disagreed: `np_133.ko`'s SMAC path and the vendor's U-Boot
seemed to read the "clear-over-ready" word of the same MAC at two
different addresses. The reverse engineering of `np_133.ko` (see
`staging/ctl/re-smac-ready-20261023.md`) settles it: the two are two
different functions that print the same format string.

  * `nppt_smac_init()` (.text 0xcf8c) reads the ready word at
    nppt+0x34294+4*mac and writes the send-enable at nppt+0x342b0+4*mac -
    the four internal-PHY MACs, mac 0..3. **This is what this driver uses.**
  * `xmac_sopc_send_enable()` (.text 0x1ac70) uses the indices 0xa9 and
    0xb0, which are nppt+0x342a4+4*n and nppt+0x342c0+4*n - the XMAC group
    (XMAC0, XMAC1, the RGMII MAC).

The live board agrees: after a reset pulse at nppt+0x2c0004, the words at
0x34294, 0x34298 and 0x342a0 come up to 1, and the ones at 0x342a4,
0x342a8 and 0x342ac do not.

The other address in the vendor's log, 0x6ed54a3d, is not an address at
all: it is a hashed `%p` pointer (`kptr_restrict`), 32 bits wide, not four
byte aligned, and different on every boot. The real address is the one
U-Boot prints, 0x192c0004.

### 3.2 The vendor's smac_init, write for write

`np_133.ko`'s `nppt_smac_init()` (0xda1c) does this for each MAC, in
program order:

| # | register | value |
|---|---|---|
| 1 | nppt+0x14+4*mac | (v & 0xfc7fffff) \| 0 - the uni mode |
| 2 | SMAC+0x00 | 0xba2203 - stop the MAC |
| 3 | nppt+0x2c0004 | pulse bit(mac) - reset the MAC |
| 5 | SMAC+0x00 | 0xfa2203 - run it |
| 6 | SMAC+0x04 | 0x3fff |
| 7 | SMAC+0x08 | 0x80000000 - the packet filter |
| 8 | SMAC+0xb00 | \|= 0x200 - the port control |
| 9 | SMAC+0xe0 | 0x00011200 - the speed/duplex word |
| 11 | nppt+0x343f0 | v & ~(1 << mac) - full duplex |
| 12 | read nppt+0x34294+4*mac, write 1 to nppt+0x342b0+4*mac | publish |
| 15 | SMAC+0x00 | \|= 2 |

Four things in this driver did not match it, and every one of them is on
the MAC's receive path:

  * **The reset pulse waited 100 us.** The vendor's `smac_reset()` (0xd29c)
    waits `__const_udelay(1718000)` - about 1.7 ms - between the two
    writes. The same pulse, done by hand on the live board with seconds
    in between, did bring the ready word up; with a tenth of a
    millisecond it never came up at all.
  * **The word at nppt+0x343f0 is a half-duplex bitmap**, bit(mac) for
    the SMACs and bit(mac+4) for the XMACs. The vendor clears the MAC's
    bit once and leaves it cleared. This driver set the bit and set it
    back, which leaves the MAC in half duplex.
  * **The packet filter and the speed/duplex word were never written** at
    all, and the value that was defined for the latter, 0x00012000, is
    not the vendor's 0x00011200.
  * **The vendor's last write to the MAC is an_ctrl |= 2**, after the
    send-enable.

The same reverse engineering also says that the vendor's own Linux never
writes the XMAC0 block at boot: the only writer of it is
`xamc_init_conf_by_speed()` (0x1a200), and the two call sites in
`nppt_smac_init()` that reach it are inside a `g_hol_enable` branch that
this board does not take (the log says "hol not enable"). So the XMAC0 on
the vendor's board is what the bootloader left it, and the run-137 change
that pulses XMAC0's reset bit before programming it is not something the
vendor does.

## 4. What was changed

  * `ZX279133_SMAC_RESET_WORD` is now `ZX279133_SYS_SOFT_RESET`
    (nppt+0x2c0004) instead of 0x2c004. The pulse the vendor performs now
    actually happens.
  * `ZX279133_XMAC_STAT_TX_GOOD` moved from 0x20b0 to 0x2230. The
    vendor's own statistics table (`xmac_reg_table`) puts "send good
    frames" at 0x2230; 0x20b0 is "receive good bytes", so the counter that
    was being reported as TX was in fact the RX byte counter.
  * The driver now warns when a MAC does not become ready, and prints the
    whole SOPC window.

## 5. Still open

  * The exact address of the "clear-over-ready" word (section 3.1).
  * Whether the DSA CPU port's link to `br-lan` behaves once the MACs are
    published.
  * The chip protocol number for ICMPv4 - the vendor's jump table has
    entries for ARP (17), IGMP (31), DHCPv6 (32), ICMPv6 (33), MLD (34),
    OSPF (37), EIGRP (36), PPPoE (27), UPNP (41) and RIP (42), but no named
    entry for ICMPv4.
