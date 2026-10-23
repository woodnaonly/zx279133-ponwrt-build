# The address the board answers on and the address the chip listens for

**2026-10-23.** The G2 acceptance round was blocked on a question that had
nothing to do with the 9132's programming: the board was answering on one
MAC address and listening for another.

## 1. What was measured

With the board on its **vendor firmware** and the PC on the wired adapter
(the neighbour entry left to ARP rather than pinned):

```
Reply from 192.168.9.1: bytes=32 time=3ms TTL=64
...
Packets: Sent = 4, Received = 4, Lost = 0 (0% loss)
```

and the neighbour the PC learned was

```
192.168.9.1   D0-45-8D-84-09-00   Reachable
```

So the vendor firmware answers on **d0:45:8d:84:09:00**. That is the
address the DTS has carried in `&nppt` since commit 3fe302d, and it is the
address the vendor's own `ip -o link show` prints for `br-lan`.

With the board on **our** kernel, `cat /sys/class/net/br-lan/address` prints

```
e2:93:27:96:14:6c
```

which is a random address: the locally-administered bit is set, and the
address is different on every boot.

## 2. Why the two addresses are different

There are two netdevs and they do not get their address from the same place.

* The **WAN** netdev (`eth0`) is the overlay driver's own
  (`zx279133-main.c`). It reads the address with
  `of_get_ethdev_address(dev->of_node, ndev)`, and `dev->of_node` is the
  `&nppt` node, which carries `local-mac-address = [d0 45 8d 84 09 00]`.
  **This one gets the right address.**
* The **DSA CPU port** - the netdev the four UNI jacks' bridge hangs off -
  is created by the *tree's* `zx279133-lan-conduit` driver, which is not
  part of this overlay. Its device tree node is `lan_cpu`
  (`compatible = "zte,zx279133-lan-conduit"`), and that node carried **no
  address at all**, so the tree's driver made a random one. The bridge takes
  its address from that port, so `br-lan` is random too.

The address the PC sees is the bridge's. The address the 9132's SPA was
programmed with is the WAN netdev's, because
`zx279133_program_spa_cpu_mac(eth, eth->ndev->dev_addr)` writes the
netdev's address into the SPA's ONU slots.

**So the board was answering on a random address and listening for
d0:45:8d:84:09:00.** A frame the PC sent to the random address never
matched what the chip compares a destination against, and it was dropped
inside the chip - which is exactly the shape of the symptom: the RED's
counters moved, and no netdev ever counted a byte.

This is the same conclusion the DTS comment reached before the fix landed
("a random address is unroutable from the outside, which is exactly what the
bench saw"), but the property was on the wrong node: it was on `&nppt`,
which is the WAN netdev, and the address that has to be right is the one on
`lan_cpu`.

## 3. The fix

`lan_cpu` now carries the address:

```dts
lan_cpu: ethernet-lan-cpu {
	compatible = "zte,zx279133-lan-conduit";
	local-mac-address = [d0 45 8d 84 09 00];
};
```

and the bench's neighbour entry is pinned to the same address
(`staging/ctl/macfix.py`, `MACFIX_MAC`, default `d0:45:8d:84:09:00`).

A round can also do it at run time, which is how the difference was
measured on a kernel whose device tree still lacks the property:

```
ip link set dev br-lan address d0:45:8d:84:09:00
```

## 4. What this says about the rounds that failed before

Every round that measured the RED's counters ran against a board whose
bridge carried a random address. The counters moved - frames reached the
9132 and the RED - and nothing reached a netdev. That is consistent with
the drop happening on the destination-address match, before the frame is
queued anywhere, and it is a second reason the earlier rounds could not see
a frame arrive no matter what the driver was told to program.

## 5. Run 133 says the address is not the problem any more

**Corrected 2026-10-23, after run 133.** The measurement above was made on
run 127, and run 133 answers differently:

```
ZZLINK eth0             UP   d0:45:8d:84:09:00
ZZLINK lan-cpu0         UP   d0:45:8d:84:09:00
ZZLINK lan1@lan-cpu0  LOWERLAYERDOWN d0:45:8d:84:09:00
ZZLINK lan2@lan-cpu0    UP   d0:45:8d:84:09:00
ZZLINK lan3@lan-cpu0  LOWERLAYERDOWN d0:45:8d:84:09:00
ZZLINK lan4@lan-cpu0  LOWERLAYERDOWN d0:45:8d:84:09:00
ZZLINK br-lan           UP   d0:45:8d:84:09:00
```

Every netdev on the board carries the board's own address, the DSA CPU port
included, and the bridge takes it from there.  The kernel's own diagnostic
dump agrees: `DIAG mac d0:45:8d:84:09:00` and `DIAG lan mac
d0:45:8d:84:09:00`.

The property has been on `&nppt` since the overlay's first commit
(`646c3b9`), so the address was never missing from the tree - the random
address of run 127 was something else, and whatever it was, it is not what
run 133 shows.

**So the address mismatch is not why run 133's counters do not move.**  On
run 133 the PC's frame reaches the board with the right destination address,
the bridge has the right address, and the switch's own counters stay exactly
where they were for the whole round.

The `lan_cpu` property added in this commit is kept, because it makes the
address explicit rather than inherited, and because an explicit address on
the CPU port is what the DSA framework expects.  It is not the fix for
anything.

## 6. The address is still a per-device address

`d0:45:8d:84:09:00` is this unit's address, read out of the vendor
firmware's own configuration. A build for another unit has to have its own;
the production answer is to parse the vendor's "parameter tags" partition,
which is not done yet (see the note in the DTS).
