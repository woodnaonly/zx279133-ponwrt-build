# The PPU was running another board's microcode, and the frames never reached the DSA ports

**2026-10-24.** Two separate faults stood between this board and a working
LAN. Both were found by measurement, not by reading the vendor's code, and
both are fixed here.

## Fault one: the microcode was the SR1010's

The tree ships a PPU microcode blob at
`target/linux/zte/zx279133/firmware/zte/zx279133/mcode_intel.bin`. It is the
**SR1010's** blob - the same SoC, a different board - and the vendor's own
blob for **this** board sits in this board's flash at
`/etc/mcode/mcode_133.bin`.

| | SR1010's blob (what the tree shipped) | this board's blob (the vendor's) |
|---|---|---|
| CRC32 | `0xef1d7647` | `0xc839b946` |
| size | 103456 | 103456 |
| version tag | `0x10001.0x1` | `0x10108.0x31ba6a03` |
| first instruction | 5272 | 5166 |

Both are the same format and the same length, and the loader accepts either:
the driver's own header checks (magic `0xffffaaaa`, 12288 instructions, a
sane `first`) pass for both, and the driver reports a PPU version at boot.

What differs is what the microcode *does*. A PPU running the SR1010's blob
does not forward this board's frames: the frame stalls inside the PPU and
nothing comes out the far side. Measured with the same 7500-frame flood that
the vendor's own board passes without a single drop:

| counter | vendor's board | the SR1010's blob |
|---|---|---|
| ISU spa in | +7517 | +3111 |
| ISU out to PPU unicast | +7517 | **+427** |
| ODMA ppu in | +7517 | **0** |
| RED in/out idm fwd | +7500 | 0 |

With the **board's own** blob, in the same RAM image, the same flood:

| counter | value |
|---|---|
| ISU spa in | +7625 |
| ISU out to PPU unicast | +7625 |
| **frames dropped by the ISU** | **0** |
| ODMA ppu in | +7625 |
| ODMA read smct | +7625 |
| ODMA out to red | +7625 |
| RED in/out idm fwd | +7578 |
| SPA UNTAG | +7578 |

Every stage now carries the frames and none of them drops any.

### The fix

The vendor's blob is now the one the build ships:
`overlays/target/linux/zte/zx279133/firmware/zte/zx279133/mcode_intel.bin`
overwrites the tree's copy when the CI applies the overlays
(`cp -a overlays/. openwrt/`), and `CONFIG_EXTRA_FIRMWARE_DIR` points the
kernel build at the same directory. The blob is a binary with 98 CR bytes
in it, so `.gitattributes` gets a narrow `-text` rule for that one path -
without it, a CRLF checkout would rewrite the microcode's instructions and
the CRC the driver checks would not be the CRC it measures.

The driver now also says so at boot. `zx279133_ppu_mcode_prepare()` checks
the blob's CRC32 against `ZX279133_MCODE_BOARD_CRC` and warns loudly when
the blob is not this board's own, because that failure has no other
symptom: the blob loads, the PPU reports a version, and the counters
simply stop.

### The RX-hash hook stays off, on purpose

The driver used to install an RX-hash hook into the microcode's instruction
stream at a fixed offset (`zx279133_rx_hash_programs[]`). That hook is a
patch to *one* blob: the offset means nothing in any other blob, and the
vendor does not patch this board's blob either - the vendor's PPU takes its
hash from the SE hardware hash table (`se_hash_ddr_init`) instead.

So the hook is now gated on the CRC of the blob it was written against
(`ZX279133_MCODE_RX_HASH_CRC`, the SR1010's) and is simply not installed for
the board's own blob. The datapath does not need it: the flood above was
taken with the hook off. The boot log says which state the board is in.

## Fault two: the frames never reached the DSA ports

With the datapath fixed, the board still answered no ping. The counters
said the frames were arriving at the 9132 and reaching the CPU port -
`lan-cpu0`'s RX counter rose by four for every ping - and then nothing
happened to them.

They were being handed to the network stack with the transport VLAN tag
still on them (VID 59..62), on the **conduit** netdev, `lan-cpu0`:

* `lan-cpu0` is the CPU port's conduit. It carries the frames between the
  SoC and the switch. It is not a bridge port, and it has no address.
* The four jacks (`lan1`..`lan4`) are DSA *user* ports. They are the host's
  way in and out of the switch, and they are the bridge's ports.
* A frame that is handed to the conduit instead of to a user port never
  reaches the host. It arrives with a VLAN tag that no VLAN device claims
  and is dropped before the IP layer sees it; `br-lan`'s RX counter never
  moves, the board never answers the ARP request, and the PC times out.

This tree's DSA core does not install a receive handler on the conduit
(`dsa_conduit_setup()` only sets `dev->dsa_ptr`), exactly like mainline:
the conduit's driver is the one that calls the CPU port's receive function.
The tree's own drivers never did - `zx279133-idm.c` ended its receive path
with `napi_gro_receive()` on the conduit - so on this board the frames
arrived and stopped there.

The fix is the tree's own tagger,
`net/dsa/tag_zx279133_rtl8372n.c`: the CPU port's `rcv` reads the transport
VID, turns it into the DSA port number, sets `skb->dev` to that port's
netdev and takes the tag off. The IDM receive path now calls it.

The mapping is checked end to end and every step lines up:

| step | value |
|---|---|
| transport VID base | 55 (`ZX279133_RTL8372N_TRANSPORT_VID_BASE`) |
| user port range | 4..7 (`ZX279133_RTL8372N_USER_PORT_MIN/MAX`) |
| the driver's transport VIDs | 59..62 (`ZX279133_LAN_TRANSPORT_VID_MIN/MAX`) |
| the 9132's UNI port 1..4 | VID 59..62, DSA port 4..7, netdev `lan1`..`lan4` |
| the CPU port | DSA port 8, conduit `lan_cpu` (`port@8`, `ethernet = <&lan_cpu>`) |

`dsa_conduit_find_user(conduit, 0, port)` looks the port up by the switch's
index in the tree (0, the only switch) and by the port's own index, and
finds the user port's netdev.

The frames now go where they belong: to `lan2`, the jack the PC is plugged
into, which is a bridge port, which is how a frame reaches the address the
board answers on.

### The protocol has to be read on the port, not on the conduit

`eth_type_trans()` answers `ETH_P_XDSA` whenever the device it is handed is
a DSA conduit:

    /* Some variants of DSA tagging don't have an ethertype field
     * at all, so we check here whether one of those tagging
     * variants has been configured on the receiving interface,
     * and if so, set skb->protocol without looking at the packet.
     */
    if (unlikely(netdev_uses_dsa(dev)))
            return htons(ETH_P_XDSA);

The conduit does not know what the frame is - the tagger does. So the
protocol is read **after** the tagger has put the frame on its port, and on
that port: the port is not a conduit, so `eth_type_trans()` reads the
frame's own EtherType.

It is read exactly once, and it has to be: `eth_type_trans()` pulls the MAC
header off the frame (`eth_skb_pull_mac()`), so a second call would pull a
second fourteen bytes and the frame would arrive one header short.

### The inline header is not the tagger's shape

The driver recognises two shapes of frame: one that arrives plain, which is
what the 9132 delivers - it is a transparent MAC-learning bridge with no
VLANs at all - and one that arrives carrying its own DSA header in the
data, which is the external RTL8372N's S-VLAN.

Only the first is handed to the tagger. The tagger reads the transport tag
out of the frame's hardware-accelerated VLAN field, and the frame it reads
there is one the driver put there itself. A frame that carries its own
header in the data is a different shape, and the tagger has never been
exercised on it - nothing in this tree ever called it - so it is left
exactly as it was rather than guessed at.

## What was ruled out on the way

* **The port-flow table.** The blob carries one, and it is board
  configuration: the SR1010's says something different for 55 of the 64
  ports. The board's own table is written instead
  (`zx279133_9132_port_flow[]`), and the vendor's blob's tags are not
  applied. The vendor applies the microcode first and the port-flow tags
  afterwards, and so does this driver - the order is the vendor's, measured
  from `np_133.ko`.
* **The ME table and `PKT_TYPE_DETAIL`.** The vendor does not write either
  at init; this driver does (`zx279133_ppu_cluster_runtime_prepare()`). The
  datapath works with them written, so they are left alone - a deviation
  from the vendor, but a harmless one, and changing it without a
  measurement would be a guess.
* **The AGCLK gating.** The vendor gates the core's and the eight ME
  clocks around the instruction download; this driver gates them around
  the whole sequence. Different, and it works.
* **The ISU, the ODMA, the SMCT and the driver's constants.** Every
  descriptor entry the vendor's init writes is written here with the same
  value, and the live board matches word for word.
* **The PC's address.** The lab machine's address on this segment is
  `192.168.1.25`, not `192.168.9.25` as the plan says, and the board's own
  ARP table learned the lab client's MAC but never the PC's - because the
  PC's ARP request never arrived.

## How the numbers were taken

The flood is the same one the vendor's board was measured with, and the
vendor's numbers come from the vendor's own instruments
(`staging/ctl/vendor-ctr.txt`: `module_reg` reads with a flood in between).

For this board the counters were read through the debugfs interface the
driver exposes (`/sys/kernel/debug/zx279133-9132`), and the per-netdev
counters from `/proc/net/dev`. The RAM image with the vendor's blob was
built by patching the blob into the run 148 ITB in place
(`staging/ctl/fwpatch.py`), so the comparison is the same kernel with one
file changed.
